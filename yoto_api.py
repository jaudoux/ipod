import os
import time
import json
import base64
import hashlib
import requests
import webbrowser
import shutil
import subprocess
import tempfile
from urllib.parse import urlencode
from tqdm import tqdm
from termcolor import colored
from io import BytesIO

# Try to import PIL for image processing
try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Iconify API for icon search
ICONIFY_API_URL = "https://api.iconify.design"

# Yoto's default "star" icon. Used as a fallback and sentinel to detect
# chapters/tracks that have never been given a custom icon.
DEFAULT_ICON_REF = "yoto:#aUm9i3ex3qqAMYBv-i-O-pYMKuMJGICtR3Vhf289u2Q"

# Yoto API Constants
# You need to register at https://dashboard.yoto.dev/ to get a client ID
YOTO_CLIENT_ID = None  # Will be prompted for during authentication
YOTO_AUTH_URL = "https://login.yotoplay.com/oauth/device/code"
YOTO_TOKEN_URL = "https://login.yotoplay.com/oauth/token"
YOTO_API_URL = "https://api.yotoplay.com"

# Config storage
CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "yoto_config.json"
)

# Token storage
TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "yoto_tokens.json"
)


def save_tokens(access_token, refresh_token):
    """Save tokens to a local file."""
    with open(TOKEN_FILE, "w") as f:
        json.dump(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "created_at": time.time(),
            },
            f,
        )
    print("Tokens saved successfully.")


def save_config(client_id):
    """Save client ID to config file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump({"client_id": client_id}, f)
    print("Client ID saved successfully.")


def load_config():
    """Load client ID from config file if available."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return None


def load_tokens():
    """Load tokens from a local file if available."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None


def decode_jwt(token):
    """Decode JWT token to check expiration."""
    try:
        # Check if token is valid
        if not token or not isinstance(token, str) or token.count(".") != 2:
            print("Invalid token format")
            return None

        # Get the payload part (second part) of the JWT
        payload = token.split(".")[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4) if len(payload) % 4 else ""
        # Decode base64
        decoded = base64.b64decode(payload.replace("-", "+").replace("_", "/"))
        # Parse JSON
        return json.loads(decoded)
    except Exception as e:
        print(f"Error decoding token: {e}")
        return None


def is_token_expired(token):
    """Check if the token is expired or about to expire."""
    if not token:
        return True

    decoded = decode_jwt(token)
    if not decoded or "exp" not in decoded:
        return True

    # Add a 5-minute buffer
    return decoded["exp"] < time.time() + 300


def refresh_access_token(refresh_token):
    """Get a new access token using the refresh token."""
    try:
        # Get client ID from config
        config = load_config()
        if not config or "client_id" not in config:
            print("Client ID not found in config. Please authenticate again.")
            return None, None

        client_id = config["client_id"]

        response = requests.post(
            YOTO_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("access_token"), data.get("refresh_token")
        else:
            print(f"Failed to refresh token: {response.text}")
            return None, None
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None, None


def get_valid_token():
    """Get a valid access token, refreshing if necessary."""
    tokens = load_tokens()

    if tokens and "access_token" in tokens and "refresh_token" in tokens:
        # Check if token is properly formatted
        access_token = tokens["access_token"]
        if (
            not access_token
            or not isinstance(access_token, str)
            or not access_token.strip()
        ):
            print("Invalid token format in stored tokens")
            return None

        if not is_token_expired(access_token):
            return access_token.strip()  # Ensure no whitespace

        # Token is expired, try to refresh
        print("Access token expired. Refreshing...")
        access_token, refresh_token = refresh_access_token(tokens["refresh_token"])

        if access_token and refresh_token:
            save_tokens(access_token, refresh_token)
            return access_token.strip()  # Ensure no whitespace

    # No valid tokens, need to authenticate
    return None


def authenticate_yoto():
    """Authenticate with Yoto using device flow."""
    import tui  # local import to avoid circular imports during module init

    tui.rule("Yoto authentication")

    config = load_config()
    client_id = config.get("client_id") if config else None

    if not client_id:
        tui.panel(
            "Yoto client ID required",
            "Register at [bold]https://dashboard.yoto.dev/[/] and create a "
            "[bold]Public Client[/] application to get your client ID.",
            style="yellow",
        )
        client_id = tui.text(
            "Enter your Yoto Client ID",
            validate=lambda s: bool(s.strip()) or "Client ID required",
        )
        if not client_id:
            tui.status("err", "No client ID provided. Authentication canceled.")
            return None
        client_id = client_id.strip()
        save_config(client_id)

    try:
        with tui.CONSOLE.status("[cyan]Initializing device login…", spinner="dots"):
            response = requests.post(
                YOTO_AUTH_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": client_id,
                    "scope": "profile offline_access",
                    "audience": "https://api.yotoplay.com",
                },
            )

        if response.status_code != 200:
            error_msg = "Unknown error"
            try:
                error_data = response.json()
                error_msg = error_data.get(
                    "error_description", error_data.get("error", "Unknown error")
                )
            except Exception:
                error_msg = response.text
            tui.status("err", f"Failed to initialize device login: {error_msg}")

            if "unauthorized_client" in response.text:
                tui.panel(
                    "Invalid client ID",
                    "The client ID you provided is not valid or not authorized.\n"
                    "Make sure you registered at https://dashboard.yoto.dev/, "
                    "created a Public Client application, and copied the ID correctly.",
                    style="red",
                )
                if tui.confirm("Enter a different client ID?", default=True):
                    if os.path.exists(CONFIG_FILE):
                        os.remove(CONFIG_FILE)
                    return authenticate_yoto()
            return None

        auth_data = response.json()
        device_code = auth_data.get("device_code")
        user_code = auth_data.get("user_code")
        verification_uri = auth_data.get("verification_uri")
        verification_uri_complete = auth_data.get("verification_uri_complete")
        interval = auth_data.get("interval", 5)
        expires_in = auth_data.get("expires_in", 300)

        tui.auth_device_panel(
            user_code, verification_uri, verification_uri_complete, expires_in
        )

        try:
            webbrowser.open(verification_uri_complete)
            tui.status("info", "Opened browser automatically.")
        except Exception:
            tui.status("warn", "Could not open browser automatically — use the URL above.")

        start_time = time.time()
        with tui.progress("Waiting for authorization") as prog:
            task = prog.add_task("Waiting for authorization", total=expires_in)
            while time.time() - start_time < expires_in:
                prog.update(task, completed=int(time.time() - start_time))

                token_response = requests.post(
                    YOTO_TOKEN_URL,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": client_id,
                        "audience": "https://api.yotoplay.com",
                    },
                )

                if token_response.status_code == 200:
                    token_data = token_response.json()
                    access_token = token_data.get("access_token")
                    refresh_token = token_data.get("refresh_token")
                    if access_token and refresh_token:
                        access_token = access_token.strip()
                        refresh_token = refresh_token.strip()
                        save_tokens(access_token, refresh_token)
                        prog.update(task, completed=expires_in)
                        tui.status("ok", "Authorization successful!")
                        return access_token

                if token_response.status_code == 403:
                    error_data = token_response.json()
                    error = error_data.get("error")
                    if error == "authorization_pending":
                        time.sleep(interval)
                        continue
                    elif error == "slow_down":
                        interval += 5
                        tui.status("warn", "slow_down received — backing off.")
                        time.sleep(interval)
                        continue
                    elif error == "expired_token":
                        tui.status("err", "Device code expired. Please try again.")
                        return None
                    else:
                        tui.status(
                            "err", f"{error}: {error_data.get('error_description', '')}"
                        )
                        return None

                time.sleep(interval)

        tui.status("err", "Authorization timed out. Please try again.")
        return None

    except Exception as e:
        tui.status("err", f"Authentication error: {e}")
        return None


def get_yoto_playlists():
    """Get list of playlists from Yoto.

    Playlists are extracted from the content/mine endpoint which returns all content,
    including playlists.
    """
    # Get all content first
    all_content = get_yoto_content()

    if not all_content:
        return None

    # Debug: Print content structure to understand what we're working with
    print(f"Found {len(all_content)} content items")

    # Print the first item to understand its structure
    if all_content:
        first_item = all_content[0]
        print("\nSample content item structure:")
        print(f"Title: {first_item.get('title')}")
        print(f"Card ID: {first_item.get('cardId')}")
        print(
            f"Content keys: {list(first_item.get('content', {}).keys()) if 'content' in first_item else 'No content key'}"
        )
        print(
            f"Metadata keys: {list(first_item.get('metadata', {}).keys()) if 'metadata' in first_item else 'No metadata key'}"
        )

        # Check if any item has chapters
        has_chapters = False
        for item in all_content:
            if "content" in item and "chapters" in item["content"]:
                has_chapters = True
                print(f"\nFound item with chapters: {item.get('title')}")
                print(f"Chapter count: {len(item['content']['chapters'])}")
                break

        if not has_chapters:
            print("\nNo items with chapters found in content")

    # For Yoto, all content items can be treated as playlists
    # Based on the sample JSON, we should consider all items as potential playlists
    playlists = []
    for item in all_content:
        # Every content item with a cardId is a potential playlist
        if "cardId" in item and item.get("cardId"):
            # Initialize chapters - may be empty for new playlists
            chapters = []
            if "content" in item and "chapters" in item["content"]:
                chapters = item["content"]["chapters"]

            playlist_info = {
                "id": item.get("cardId"),
                "title": item.get("title") or "Untitled Playlist",
                "chapters": chapters,
                "createdAt": item.get("createdAt"),
                "updatedAt": item.get("updatedAt"),
            }
            playlists.append(playlist_info)
            print(
                f"Found playlist: {playlist_info['title']} (ID: {playlist_info['id']})"
            )

    if not playlists:
        print("No playlists found in your content.")
        return None

    return playlists


def get_playlist_details(playlist_id):
    """Fetch full playlist details by ID including all chapters and tracks."""
    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return None

    # Clean token
    clean_token = access_token.strip()

    try:
        response = requests.get(
            f"{YOTO_API_URL}/content/{playlist_id}",
            headers={"Authorization": f"Bearer {clean_token}"},
        )

        if response.status_code == 200:
            data = response.json()
            # Return the card data which contains the full playlist details
            return data.get("card", {})
        else:
            print(f"Failed to get playlist details: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching playlist details: {e}")
        return None


def get_yoto_content():
    """Get list of user's content from Yoto."""
    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return None

    # Clean token
    clean_token = access_token.strip()

    # Try using curl first (more reliable)
    try:
        print("Fetching content using curl...")
        curl_command = [
            "curl",
            "-s",  # Silent mode
            "-X",
            "GET",
            "-H",
            f"Authorization: Bearer {clean_token}",
            f"{YOTO_API_URL}/content/mine",  # Correct endpoint for user's content
        ]

        result = subprocess.run(
            curl_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode == 0 and result.stdout:
            try:
                content_data = json.loads(result.stdout)
                if "cards" in content_data and isinstance(content_data["cards"], list):
                    return content_data["cards"]
                else:
                    print(f"Unexpected content data format: {result.stdout[:100]}...")
            except json.JSONDecodeError:
                print(f"Failed to parse content JSON: {result.stdout[:100]}...")
        else:
            print(f"Curl failed to get content: {result.stderr}")
    except Exception as e:
        print(f"Error using curl to get content: {e}")

    # Fall back to requests
    print("Falling back to Python requests...")
    try:
        response = requests.get(
            f"{YOTO_API_URL}/content/mine",  # Correct endpoint for user's content
            headers={"Authorization": f"Bearer {clean_token}"},
        )

        if response.status_code == 200:
            data = response.json()
            if "cards" in data and isinstance(data["cards"], list):
                return data["cards"]
            else:
                print(f"Unexpected content data format: {response.text[:100]}...")
                return None
        else:
            print(f"Failed to get content: {response.text}")
            return None
    except Exception as e:
        print(f"Error getting content: {e}")
        return None


def upload_to_yoto(file_path, title=None, playlist_id=None, icon_ref=None):
    """Upload an audio file to Yoto using the correct API process.

    1. Request upload URL
    2. Upload audio to the URL
    3. Wait for transcoding
    4. Create content with the transcoded audio
    5. Add to playlist if playlist_id is provided

    `icon_ref` overrides the default chapter/track icon when provided.
    """
    if not os.path.exists(file_path):
        print(colored(f"File not found: {file_path}", "red"))
        return None

    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return None

    # Clean token
    clean_token = access_token.strip()

    # Use filename as title if not provided
    if not title:
        title = os.path.basename(file_path).replace(".mp3", "")

    # Get file size
    file_size = os.path.getsize(file_path)
    print(f"Original file size: {file_size / (1024 * 1024):.2f} MB")

    # If file is too large, compress it
    upload_path = file_path
    compressed_path = None
    if (
        file_size > 100 * 1024 * 1024
    ):  # 100 MB (Yoto can handle larger files now with transcoding)
        print(
            colored(
                f"File is very large, compressing to reduce upload time...", "yellow"
            )
        )
        # Create a temporary file for compressed audio
        compressed_path = f"{file_path}.compressed.mp3"

        try:
            # First try moderate compression (96kbps mono)
            print(colored("Trying moderate compression...", "cyan"))
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    file_path,
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "96k",
                    "-ac",
                    "1",  # mono
                    compressed_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Check if the compressed file is small enough
            compressed_size = os.path.getsize(compressed_path)
            print(f"Compressed file size: {compressed_size / (1024 * 1024):.2f} MB")

            # Use the compressed file if it's smaller than the original
            if compressed_size < file_size:
                upload_path = compressed_path
                print(
                    colored(
                        f"Using compressed file: {compressed_size / (1024 * 1024):.2f} MB",
                        "green",
                    )
                )
            else:
                print(
                    colored(
                        "Compression did not reduce file size. Using original file.",
                        "yellow",
                    )
                )
                os.remove(compressed_path)
                compressed_path = None
                upload_path = file_path

        except Exception as e:
            print(colored(f"Compression failed: {e}", "red"))
            print(colored("Using original file.", "yellow"))
            if compressed_path and os.path.exists(compressed_path):
                os.remove(compressed_path)
            compressed_path = None
            upload_path = file_path

    try:
        # STEP 1: Request upload URL
        print(colored("Step 1: Requesting upload URL from Yoto...", "cyan"))
        upload_url_response = requests.get(
            f"{YOTO_API_URL}/media/transcode/audio/uploadUrl",
            headers={
                "Authorization": f"Bearer {clean_token}",
                "Accept": "application/json",
            },
        )

        if upload_url_response.status_code != 200:
            print(
                colored(f"Failed to get upload URL: {upload_url_response.text}", "red")
            )
            return None

        upload_data = upload_url_response.json()
        audio_upload_url = upload_data.get("upload", {}).get("uploadUrl")
        upload_id = upload_data.get("upload", {}).get("uploadId")

        if not audio_upload_url or not upload_id:
            print(colored("Failed to get upload URL or upload ID", "red"))
            return None

        print(colored(f"Got upload URL and ID: {upload_id}", "green"))

        # STEP 2: Upload the audio file to the URL
        print(colored("Step 2: Uploading audio file...", "cyan"))

        # Determine content type based on file extension
        content_type = "audio/mpeg"  # Default to MP3
        if upload_path.lower().endswith(".wav"):
            content_type = "audio/wav"
        elif upload_path.lower().endswith(".m4a"):
            content_type = "audio/m4a"

        # Upload file using requests
        with open(upload_path, "rb") as f:
            # Use a simple filename without special characters to avoid encoding issues
            safe_filename = f"audio_{int(time.time())}.mp3"

            upload_response = requests.put(
                audio_upload_url,
                data=f.read(),
                headers={
                    "Content-Type": content_type,
                    "Content-Disposition": f"attachment; filename={safe_filename}",
                },
            )

        if upload_response.status_code not in [200, 201, 204]:
            print(colored(f"Failed to upload audio: {upload_response.text}", "red"))
            return None

        print(colored("Audio uploaded successfully", "green"))

        # STEP 3: Wait for transcoding (exponential backoff)
        print(colored("Step 3: Waiting for transcoding to complete...", "cyan"))
        transcoded_audio = None
        attempts = 0
        max_attempts = 10
        delay = 2
        max_delay = 60

        while attempts < max_attempts:
            transcode_response = requests.get(
                f"{YOTO_API_URL}/media/upload/{upload_id}/transcoded?loudnorm=false",
                headers={
                    "Authorization": f"Bearer {clean_token}",
                    "Accept": "application/json",
                },
            )

            if transcode_response.status_code == 200:
                data = transcode_response.json()
                if data.get("transcode", {}).get("transcodedSha256"):
                    transcoded_audio = data.get("transcode")
                    print(colored("Transcoding completed successfully", "green"))
                    break

            print(
                f"Waiting for transcoding... Attempt {attempts+1}/{max_attempts} "
                f"(sleeping {delay}s)"
            )
            time.sleep(delay)
            attempts += 1
            delay = min(delay * 2, max_delay)

        if not transcoded_audio:
            print(colored("Transcoding timed out", "red"))
            return None

        # Get media info from the transcoded audio
        media_info = transcoded_audio.get("transcodedInfo", {})
        chapter_title = media_info.get("metadata", {}).get("title") or title
        transcoded_sha256 = transcoded_audio.get("transcodedSha256")

        # If a playlist ID was provided, add directly to that playlist without creating standalone content
        if playlist_id:
            print(colored("Step 4: Adding track to existing playlist...", "cyan"))
            if add_to_playlist(
                None,
                playlist_id,
                title,
                transcoded_sha256,
                media_info,
                icon_ref=icon_ref,
            ):
                print(colored(f"Successfully added to playlist!", "green"))
                print(
                    colored(
                        f"Your podcast is now available on your Yoto player!", "cyan"
                    )
                )
                return playlist_id
            else:
                print(colored(f"Failed to add to playlist.", "red"))
                return None

        # Step 4: Create standalone content (only when no playlist_id is provided)
        print(colored("Step 4: Creating content with transcoded audio...", "cyan"))

        chapter_icon = icon_ref or DEFAULT_ICON_REF

        # Create the content payload
        # Create content with chapters structure as required by the API
        content = {
            "title": title,
            "content": {
                "chapters": [
                    {
                        "key": "01",
                        "title": chapter_title,
                        "overlayLabel": "1",
                        "tracks": [
                            {
                                "key": "01",
                                "title": chapter_title,
                                "trackUrl": f"yoto:#{transcoded_sha256}",
                                "duration": media_info.get("duration"),
                                "fileSize": media_info.get("fileSize"),
                                "channels": media_info.get("channels"),
                                "format": media_info.get("format") or "mp3",
                                "type": "audio",
                                "overlayLabel": "1",
                                "display": {
                                    "icon16x16": chapter_icon,
                                },
                            }
                        ],
                        "display": {
                            "icon16x16": chapter_icon,
                        },
                    }
                ],
                "playbackType": "linear",
            },
            "metadata": {
                "media": {
                    "duration": media_info.get("duration"),
                    "fileSize": media_info.get("fileSize"),
                    "readableFileSize": round(
                        (media_info.get("fileSize") or 0) / 1024 / 1024, 1
                    ),
                },
            },
        }

        # Create the content
        create_response = requests.post(
            f"{YOTO_API_URL}/content",
            headers={
                "Authorization": f"Bearer {clean_token}",
                "Content-Type": "application/json",
            },
            json=content,
        )

        if create_response.status_code not in [200, 201, 204]:
            print(colored(f"Failed to create content: {create_response.text}", "red"))
            return None

        result = create_response.json()
        content_id = result.get("card", {}).get("cardId")

        if not content_id:
            print(colored("No content ID in response", "red"))
            return None

        print(colored(f"Successfully uploaded to Yoto!", "green"))
        print(colored(f"Content ID: {content_id}", "green"))
        print(colored(f"Your podcast is now available on your Yoto player!", "cyan"))
        return content_id

    except Exception as e:
        print(f"Error uploading file: {e}")
        return None

    finally:
        # Clean up temporary files
        if compressed_path and os.path.exists(compressed_path):
            os.remove(compressed_path)


def add_to_playlist(
    content_id,
    playlist_id,
    title=None,
    transcoded_sha256=None,
    media_info=None,
    icon_ref=None,
):
    """Add content to a playlist.

    Args:
        content_id: The ID of the content to add (can be None if transcoded_sha256 is provided)
        playlist_id: The ID of the playlist to add to
        title: Optional title for the track
        transcoded_sha256: Optional SHA256 hash of the transcoded audio. If not provided,
                          we'll try to fetch it from the content details.
        media_info: Optional media info dict with duration, fileSize, channels, format
        icon_ref: Optional custom icon reference (e.g. "yoto:#<mediaId>") to use for the
                  new chapter and track. Falls back to DEFAULT_ICON_REF when None.
    """
    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return False

    # Clean token
    clean_token = access_token.strip()

    # If no transcoded_sha256 was provided and we have a content_id, get it from the content details
    if not transcoded_sha256 and content_id:
        print(f"Fetching content {content_id} details to get transcoded hash...")
        content_response = requests.get(
            f"{YOTO_API_URL}/content/{content_id}",
            headers={"Authorization": f"Bearer {clean_token}"},
        )

        if content_response.status_code != 200:
            print(f"Failed to get content details: {content_response.text}")
            return False

        content_data = content_response.json().get("card", {})
        if not content_data:
            print("No content data found")
            return False

        # Try to extract the transcoded SHA256 hash from the content
        # It should be in the trackUrl of the first track of the first chapter
        try:
            chapters = content_data.get("content", {}).get("chapters", [])
            if chapters and "tracks" in chapters[0] and chapters[0]["tracks"]:
                track_url = chapters[0]["tracks"][0].get("trackUrl", "")
                if track_url.startswith("yoto:#"):
                    transcoded_sha256 = track_url[6:]  # Remove 'yoto:#' prefix
                    print(f"Found transcoded hash: {transcoded_sha256}")
        except (IndexError, KeyError):
            pass

        if not transcoded_sha256:
            print(
                "Could not find transcoded hash in content. Using content ID as fallback."
            )
            transcoded_sha256 = content_id

    if not transcoded_sha256:
        print("No transcoded hash available. Cannot add to playlist.")
        return False

    # First get the specific playlist content to modify
    try:
        print(f"Fetching playlist {playlist_id} details...")
        response = requests.get(
            f"{YOTO_API_URL}/content/{playlist_id}",
            headers={"Authorization": f"Bearer {clean_token}"},
        )

        if response.status_code != 200:
            print(f"Failed to get playlist: {response.text}")
            return False

        playlist_data = response.json().get("card", {})
        if not playlist_data:
            print("No playlist data found")
            return False

        # Get chapters from the playlist
        chapters = playlist_data.get("content", {}).get("chapters", [])

        # Create a new chapter with the content
        import uuid

        chapter_key = str(uuid.uuid4()).replace("-", "")[:20]  # Generate a unique key

        chapter_icon = icon_ref or DEFAULT_ICON_REF

        # Create new track entry with the correct trackUrl format
        new_track = {
            "key": chapter_key,
            "title": title or "New Track",
            "format": (media_info.get("format") if media_info else None) or "mp3",
            "trackUrl": f"yoto:#{transcoded_sha256}",  # Use the transcoded SHA256 hash
            "type": "audio",
            "display": {
                "icon16x16": chapter_icon,
            },
            "ambient": None,
        }

        # Add media info to track if available
        if media_info:
            if media_info.get("duration"):
                new_track["duration"] = media_info.get("duration")
            if media_info.get("fileSize"):
                new_track["fileSize"] = media_info.get("fileSize")
            if media_info.get("channels"):
                new_track["channels"] = media_info.get("channels")

        # Create new chapter with the track
        new_chapter = {
            "key": chapter_key,
            "title": title or "New Chapter",
            "tracks": [new_track],
            "display": {
                "icon16x16": chapter_icon,
            },
            "availableFrom": None,
            "ambient": None,
            "defaultTrackDisplay": None,
            "defaultTrackAmbient": None,
        }

        # Add duration to chapter if available
        if media_info and media_info.get("duration"):
            new_chapter["duration"] = media_info.get("duration")

        # Add the new chapter to the playlist
        chapters.append(new_chapter)

        # Update the playlist content with the new chapters
        playlist_data["content"]["chapters"] = chapters

        # Update the playlist
        print("Updating playlist with new content...")
        # Make sure token is clean with no extra whitespace
        clean_token = clean_token.strip()

        # Create the correct JSON payload structure
        update_payload = {
            "cardId": playlist_id,
            "title": playlist_data.get("title"),
            "content": playlist_data.get("content", {}),
            "metadata": playlist_data.get("metadata", {}),
        }

        update_response = requests.post(
            f"{YOTO_API_URL}/content",  # Use the create/update endpoint
            headers={
                "Authorization": f"Bearer {clean_token}",
                "Content-Type": "application/json",
            },
            json=update_payload,
        )

        if update_response.status_code in [200, 201, 204]:
            print(colored("Successfully added to playlist!", "green"))
            return True
        else:
            print(f"Failed to update playlist: {update_response.text}")
            return False

    except Exception as e:
        print(f"Error adding to playlist: {e}")
        return False


def is_episode_in_playlist(episode_title, playlist):
    """Check if an episode is already in a playlist.

    Args:
        episode_title: The title of the episode to check
        playlist: The playlist data object

    Returns:
        bool: True if the episode is in the playlist, False otherwise
    """
    if not playlist:
        return False

    # Extract chapters from the playlist structure
    chapters = []

    # Handle different playlist data structures
    if isinstance(playlist, dict):
        # Direct chapters array
        if "chapters" in playlist:
            chapters = playlist["chapters"]
        # Chapters in content object
        elif "content" in playlist and isinstance(playlist["content"], dict):
            if "chapters" in playlist["content"]:
                chapters = playlist["content"]["chapters"]
        # Card structure from API response
        elif "card" in playlist and isinstance(playlist["card"], dict):
            if "content" in playlist["card"] and isinstance(
                playlist["card"]["content"], dict
            ):
                if "chapters" in playlist["card"]["content"]:
                    chapters = playlist["card"]["content"]["chapters"]

    # If no chapters found, don't show debug message and assume episode is not in playlist
    if not chapters:
        return False

    # Normalize the episode title for comparison
    normalized_title = episode_title.lower().strip()

    # Check each chapter in the playlist
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue

        chapter_title = str(chapter.get("title", "")).lower().strip()

        # Exact match with chapter title
        if normalized_title == chapter_title:
            return True

        # Check tracks within the chapter
        if "tracks" in chapter and isinstance(chapter["tracks"], list):
            for track in chapter["tracks"]:
                if not isinstance(track, dict):
                    continue
                track_title = str(track.get("title", "")).lower().strip()
                if normalized_title == track_title:
                    return True

    return False


def search_icons(query, limit=3):
    """Search for icons using Iconify API.

    Args:
        query: Search keywords
        limit: Maximum number of results to return (default 3)

    Returns:
        List of icon dicts with 'name', 'prefix', 'icon', 'url' keys
    """
    try:
        # Search for icons - prefer colored icon sets
        # noto, twemoji, fluent-emoji are colored emoji-style icons
        # flat-color-icons, logos are also colored
        response = requests.get(
            f"{ICONIFY_API_URL}/search",
            params={
                "query": query,
                "limit": limit * 3,  # Get more results to filter
                "prefixes": "noto,twemoji,fluent-emoji,flat-color-icons,emojione,openmoji,fxemoji,noto-v1",
            },
        )

        if response.status_code != 200:
            print(colored(f"Icon search failed: {response.status_code}", "red"))
            return []

        data = response.json()
        icons = data.get("icons", [])

        if not icons:
            # Try broader search without prefix filter
            response = requests.get(
                f"{ICONIFY_API_URL}/search", params={"query": query, "limit": limit * 2}
            )
            if response.status_code == 200:
                data = response.json()
                icons = data.get("icons", [])

        # Parse and return icon info
        results = []
        for icon_name in icons[:limit]:
            # Icon name format is "prefix:name"
            if ":" in icon_name:
                prefix, name = icon_name.split(":", 1)
            else:
                continue

            # Build SVG URL
            svg_url = f"{ICONIFY_API_URL}/{prefix}/{name}.svg"

            results.append(
                {
                    "name": icon_name,
                    "prefix": prefix,
                    "icon": name,
                    "svg_url": svg_url,
                    "png_url": f"{ICONIFY_API_URL}/{prefix}/{name}.svg?width=64&height=64",
                }
            )

        return results

    except Exception as e:
        print(colored(f"Error searching icons: {e}", "red"))
        return []


def download_icon_as_png(icon_info, size=64, color=None):
    """Download an icon and convert it to PNG format suitable for Yoto.

    Args:
        icon_info: Dict with icon information from search_icons
        size: Target size in pixels (default 64, Yoto will resize to 16x16)
        color: Optional hex color to apply (e.g., "ff0000" for red)

    Returns:
        Tuple of (png_bytes, temp_file_path) or (None, None) on failure
    """
    try:
        # Build SVG URL with size and optional color
        svg_url = f"{ICONIFY_API_URL}/{icon_info['prefix']}/{icon_info['icon']}.svg"
        params = {"width": size, "height": size}
        if color:
            params["color"] = color

        response = requests.get(svg_url, params=params)

        if response.status_code != 200:
            print(colored(f"Failed to download icon: {response.status_code}", "red"))
            return None, None

        svg_content = response.content

        # Save SVG to temp file for conversion
        svg_temp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        svg_temp.write(svg_content)
        svg_temp.close()

        png_temp_path = svg_temp.name.replace(".svg", ".png")

        # Try multiple methods to convert SVG to PNG
        png_bytes = None

        # Method 1: Try cairosvg
        try:
            import cairosvg

            png_bytes = cairosvg.svg2png(
                bytestring=svg_content, output_width=size, output_height=size
            )
            print(colored("Converted SVG to PNG using cairosvg", "cyan"))
        except ImportError:
            pass

        # Method 2: Try rsvg-convert (common on macOS with librsvg)
        if png_bytes is None:
            try:
                result = subprocess.run(
                    [
                        "rsvg-convert",
                        "-w",
                        str(size),
                        "-h",
                        str(size),
                        "-o",
                        png_temp_path,
                        svg_temp.name,
                    ],
                    capture_output=True,
                )
                if result.returncode == 0 and os.path.exists(png_temp_path):
                    with open(png_temp_path, "rb") as f:
                        png_bytes = f.read()
                    print(colored("Converted SVG to PNG using rsvg-convert", "cyan"))
            except FileNotFoundError:
                pass

        # Method 3: Try ImageMagick convert
        if png_bytes is None:
            try:
                result = subprocess.run(
                    [
                        "convert",
                        "-background",
                        "none",
                        "-resize",
                        f"{size}x{size}",
                        svg_temp.name,
                        png_temp_path,
                    ],
                    capture_output=True,
                )
                if result.returncode == 0 and os.path.exists(png_temp_path):
                    with open(png_temp_path, "rb") as f:
                        png_bytes = f.read()
                    print(colored("Converted SVG to PNG using ImageMagick", "cyan"))
            except FileNotFoundError:
                pass

        # Method 4: Try sips (macOS built-in) - limited SVG support
        if png_bytes is None:
            try:
                result = subprocess.run(
                    [
                        "sips",
                        "-s",
                        "format",
                        "png",
                        svg_temp.name,
                        "--out",
                        png_temp_path,
                    ],
                    capture_output=True,
                )
                if result.returncode == 0 and os.path.exists(png_temp_path):
                    with open(png_temp_path, "rb") as f:
                        png_bytes = f.read()
                    print(colored("Converted SVG to PNG using sips", "cyan"))
            except FileNotFoundError:
                pass

        # Clean up SVG temp file
        try:
            os.unlink(svg_temp.name)
        except:
            pass

        if png_bytes is None:
            print(
                colored("Could not convert SVG to PNG. Please install one of:", "red")
            )
            print(colored("  - cairosvg: pip install cairosvg", "yellow"))
            print(colored("  - rsvg-convert: brew install librsvg", "yellow"))
            print(colored("  - ImageMagick: brew install imagemagick", "yellow"))
            return None, None

        # Save PNG to temp file
        temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        temp_file.write(png_bytes)
        temp_file.close()

        return png_bytes, temp_file.name

    except Exception as e:
        print(colored(f"Error downloading icon: {e}", "red"))
        return None, None


def upload_custom_icon(
    file_path=None, file_bytes=None, filename=None, auto_convert=True
):
    """Upload a custom icon to Yoto.

    Args:
        file_path: Path to the image file to upload
        file_bytes: Raw bytes of the image (alternative to file_path)
        filename: Optional filename override
        auto_convert: If True, Yoto will resize/process the image to 16x16

    Returns:
        Dict with displayIcon info on success, None on failure
    """
    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return None

    clean_token = access_token.strip()

    try:
        # Prepare the file data
        if file_path:
            with open(file_path, "rb") as f:
                file_data = f.read()
            if not filename:
                filename = os.path.basename(file_path)
        elif file_bytes:
            file_data = file_bytes
            if not filename:
                filename = f"icon_{int(time.time())}.png"
        else:
            print(colored("No file provided for upload", "red"))
            return None

        # Determine content type
        content_type = "image/png"
        if filename.lower().endswith(".svg"):
            content_type = "image/svg+xml"
        elif filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
            content_type = "image/jpeg"
        elif filename.lower().endswith(".gif"):
            content_type = "image/gif"

        # Build query parameters
        params = []
        if auto_convert:
            params.append(f"autoConvert=true")
        if filename:
            # Remove extension for the filename parameter
            base_filename = os.path.splitext(filename)[0]
            params.append(f"filename={base_filename}")

        # Build URL with query params
        url = f"{YOTO_API_URL}/media/displayIcons/user/me/upload"
        if params:
            url += "?" + "&".join(params)

        # Upload the icon using curl (more reliable for binary uploads)
        print(colored("Uploading icon to Yoto...", "cyan"))

        # Use curl for binary file upload
        curl_command = [
            "curl",
            "-s",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {clean_token}",
            "-H",
            f"Content-Type: {content_type}",
            "--data-binary",
            "@-",  # Read from stdin
            url,
        ]

        result = subprocess.run(curl_command, input=file_data, capture_output=True)

        if result.returncode == 0 and result.stdout:
            try:
                response_data = json.loads(result.stdout.decode("utf-8"))
                if "displayIcon" in response_data:
                    display_icon = response_data.get("displayIcon", {})

                    if display_icon.get("new"):
                        print(colored("New icon uploaded successfully!", "green"))
                    else:
                        print(
                            colored(
                                "Icon already exists, using existing record.", "yellow"
                            )
                        )

                    icon_id = display_icon.get("displayIconId")
                    media_id = display_icon.get("mediaId")
                    icon_url = display_icon.get("url")

                    if icon_id:
                        print(colored(f"Icon ID: {icon_id}", "cyan"))
                    if media_id:
                        print(colored(f"Media ID: {media_id}", "cyan"))
                    if icon_url and isinstance(icon_url, str):
                        print(colored(f"Icon URL: {icon_url}", "cyan"))

                    return display_icon
                elif "error" in response_data:
                    print(
                        colored(
                            f"Failed to upload icon: {response_data['error']}", "red"
                        )
                    )
                    return None
            except json.JSONDecodeError:
                print(
                    colored(
                        f"Failed to parse response: {result.stdout.decode('utf-8')}",
                        "red",
                    )
                )
                return None
        else:
            error_msg = (
                result.stderr.decode("utf-8")
                if result.stderr
                else result.stdout.decode("utf-8")
            )
            print(colored(f"Failed to upload icon: {error_msg}", "red"))
            return None

    except Exception as e:
        print(colored(f"Error uploading icon: {e}", "red"))
        return None


def display_icon_preview(icons):
    """Display icon search results in the terminal.

    Args:
        icons: List of icon dicts from search_icons
    """
    if not icons:
        print(colored("No icons found.", "yellow"))
        return

    print("\n" + colored("=" * 50, "cyan"))
    print(colored(" ICON SEARCH RESULTS ", "cyan", attrs=["bold"]))
    print(colored("=" * 50, "cyan"))

    for i, icon in enumerate(icons):
        print(f"\n[{colored(str(i), 'green')}] {colored(icon['name'], 'yellow')}")
        print(f"    Preview URL: {icon['svg_url']}?width=64")
        print(f"    (Open URL in browser to preview)")


def icon_upload_menu():
    """Interactive menu for searching and uploading custom icons to Yoto."""
    import tui

    tui.rule("Custom icon upload")

    access_token = get_valid_token()
    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            tui.status("err", "Authentication failed.")
            return None

    while True:
        choice = tui.select(
            "Icon source",
            [
                tui.Choice(title="🔎  Search Iconify (colored emoji icons)", value="search"),
                tui.Choice(title="📁  Upload from a local file", value="file"),
                tui.Separator(),
                tui.Choice(title="← Back", value="back"),
            ],
        )
        if choice in (None, "back"):
            return None

        if choice == "search":
            result = _icon_search_flow()
            if result is not None:
                return result
            continue

        if choice == "file":
            result = _icon_file_flow()
            if result is not None:
                return result
            continue


def _icon_search_flow():
    """Iconify search → pick → optional browser preview → upload."""
    import tui

    query = tui.text(
        "Search keywords (e.g. book, music, star)",
        validate=lambda s: bool(s.strip()) or "Query required",
    )
    if not query:
        return None

    with tui.CONSOLE.status(f"[cyan]Searching Iconify for '{query}'…", spinner="dots"):
        icons = search_icons(query.strip(), limit=5)
    if not icons:
        tui.status("warn", "No icons found. Try different keywords.")
        return None

    _PREVIEW = "__preview__"
    while True:
        picked = tui.select(
            "Pick an icon (or preview all in a browser)",
            [
                *[tui.Choice(title=icon["name"], value=icon) for icon in icons],
                tui.Separator(),
                tui.Choice(title="🌐  Preview all in browser", value=_PREVIEW),
                tui.Choice(title="← Back", value=None),
            ],
        )
        if picked is None:
            return None
        if picked == _PREVIEW:
            for icon in icons:
                webbrowser.open(f"{icon['svg_url']}?width=128")
            continue

        tui.status("info", f"Selected: {picked['name']}")
        _png_bytes, temp_path = download_icon_as_png(picked, size=16)
        if not temp_path:
            tui.status("err", "Failed to download icon.")
            return None
        try:
            result = upload_custom_icon(
                file_path=temp_path,
                filename=f"{picked['icon']}.png",
                auto_convert=True,
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        if result and result.get("mediaId"):
            tui.status("ok", f"Icon reference: yoto:#{result['mediaId']}")
            return result
        return None


def _icon_file_flow():
    """Local file picker → validate → upload."""
    import tui

    file_path = tui.path("Path to image file")
    if not file_path:
        return None
    file_path = os.path.expanduser(file_path.strip())

    if not os.path.exists(file_path):
        tui.status("err", f"File not found: {file_path}")
        return None

    valid_exts = [".png", ".jpg", ".jpeg", ".gif", ".svg"]
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in valid_exts:
        tui.status("err", f"Invalid file type. Supported: {', '.join(valid_exts)}")
        return None

    auto_convert = bool(tui.confirm("Auto-convert to 16×16?", default=True))

    result = upload_custom_icon(file_path=file_path, auto_convert=auto_convert)
    if result and result.get("mediaId"):
        tui.status("ok", f"Icon reference: yoto:#{result['mediaId']}")
        return result
    return None


def create_playlist(title, description=None):
    """Create a new playlist/card on Yoto.

    Args:
        title: The title for the new playlist
        description: Optional description for the playlist

    Returns:
        The card ID of the created playlist, or None if creation failed
    """
    access_token = get_valid_token()

    if not access_token:
        access_token = authenticate_yoto()
        if not access_token:
            return None

    # Clean token
    clean_token = access_token.strip()

    try:
        # Create a new card/playlist using the /content endpoint
        # This creates an empty playlist with one chapter ready for tracks
        new_playlist = {
            "title": title,
            "content": {
                "chapters": [
                    {
                        "key": "01",
                        "title": title,
                        "tracks": [],
                        "display": {
                            "icon16x16": DEFAULT_ICON_REF,
                        },
                    }
                ],
                "playbackType": "linear",
            },
            "metadata": {
                "description": description or "Created by iPod Podcast Downloader",
                "category": "stories",
            },
        }

        response = requests.post(
            f"{YOTO_API_URL}/content",
            headers={
                "Authorization": f"Bearer {clean_token}",
                "Content-Type": "application/json",
            },
            json=new_playlist,
        )

        if response.status_code in [200, 201]:
            playlist_data = response.json()
            card_id = playlist_data.get("card", {}).get("cardId")
            if card_id:
                print(colored(f"Created new playlist: {title}", "green"))
                print(colored(f"Card ID: {card_id}", "cyan"))
                return card_id
            else:
                print(colored("Playlist created but no card ID returned.", "yellow"))
                return None
        else:
            print(colored(f"Failed to create playlist: {response.text}", "red"))
            return None

    except Exception as e:
        print(colored(f"Error creating playlist: {e}", "red"))
        return None


def _pick_playlist(message="Select a playlist", include_none=False):
    """Arrow-key playlist picker. Returns playlist_id, or None when the user
    cancels / no playlists. When include_none is True adds a "No playlist"
    choice that returns the sentinel string ``"__none__"``.
    """
    import tui

    playlists = get_yoto_playlists()
    if not playlists:
        tui.status("err", "Could not retrieve playlists.")
        return None

    choices = []
    if include_none:
        choices.append(tui.Choice(title="(no playlist — upload only)", value="__none__"))
    for pl in playlists:
        title = pl.get("title") or "?"
        pl_id = pl.get("id") or "?"
        choices.append(tui.Choice(title=f"{title}  [dim]{pl_id}[/]", value=pl_id))

    return tui.select(message, choices)


def yoto_menu(podcast_dir, episode_title=None, mp3_path=None, downloaded_episodes=None):
    """Display Yoto integration menu.

    Args:
        podcast_dir: Directory containing podcast files
        episode_title: Optional single episode title (legacy)
        mp3_path: Optional single mp3 path (legacy)
        downloaded_episodes: Optional list of tuples (title, path) for recently downloaded episodes
    """

    import tui  # local import avoids circulars during module init

    tui.rule("Yoto player integration")

    access_token = get_valid_token()
    if not access_token:
        tui.panel(
            "Yoto authentication required",
            "Register at [bold]https://dashboard.yoto.dev/[/], create a "
            "Public Client application, and copy the client ID.",
            style="yellow",
        )
        if not tui.confirm("Ready to continue with authentication?", default=True):
            tui.status("warn", "Authentication canceled. Returning to main menu.")
            return
        access_token = authenticate_yoto()
        if not access_token:
            tui.status("err", "Authentication failed. Returning to main menu.")
            return

    # Find all downloaded episodes
    def find_episodes():
        episodes = []
        if os.path.exists(podcast_dir):
            for root, dirs, files in os.walk(podcast_dir):
                for file in files:
                    if file.endswith(".mp3"):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, podcast_dir)
                        title = os.path.splitext(file)[0]
                        podcast_name = os.path.basename(root)
                        episodes.append(
                            {"title": title, "path": full_path, "podcast": podcast_name}
                        )
        return episodes

    def select_episode(episodes, playlist=None, multi_select=False):
        """Arrow/space/enter episode picker.

        Returns a list of (path, title) tuples when multi_select=True,
        otherwise a single (path, title) tuple or (None, None) on cancel.
        """
        if not episodes:
            tui.status("warn", "No downloaded episodes found.")
            return [] if multi_select else (None, None)

        choices = []
        for ep in episodes:
            title = ep["title"]
            in_pl = False
            if playlist:
                try:
                    in_pl = is_episode_in_playlist(title, playlist)
                except Exception:
                    pass
            choices.append(
                tui.episode_choice(
                    title,
                    synced=in_pl,
                    has_local=True,
                    card_linked=bool(playlist),
                    value=(ep["path"], ep["title"]),
                )
            )

        if multi_select:
            picked = tui.checkbox("Select episodes to upload", choices)
            if not picked:
                tui.status("info", "No episodes selected.")
                return []
            return picked
        picked = tui.select("Select an episode to upload", choices)
        if picked is None:
            return (None, None)
        return picked

    # If downloaded_episodes is provided, directly prompt to upload to a playlist
    if downloaded_episodes:
        tui.status("info", f"{len(downloaded_episodes)} episode(s) ready to upload:")
        for title, _path in downloaded_episodes:
            tui.CONSOLE.print(f"  • {title}")

        playlist_id = _pick_playlist("Select a playlist to upload to")
        if not playlist_id:
            return

        for title, path in downloaded_episodes:
            tui.status("info", f"Uploading: {title}")
            content_id = upload_to_yoto(path, title, playlist_id)
            if content_id:
                tui.status("ok", f"Uploaded '{title}'.")
            else:
                tui.status("err", f"Failed to upload '{title}'.")
        tui.status("ok", "All episodes processed.")
        return

    _yoto_menu_loop(podcast_dir, episode_title, mp3_path, find_episodes, select_episode)


def _yoto_menu_loop(podcast_dir, episode_title, mp3_path, find_episodes, select_episode):
    import tui
    import icon_factory

    while True:
        action = tui.select(
            "Yoto menu",
            [
                tui.Choice(title="📤  Upload episode(s) to a playlist", value="upload"),
                tui.Choice(title="📎  Add existing content to a playlist", value="add"),
                tui.Choice(title="🆕  Create a new playlist", value="create"),
                tui.Choice(title="📚  View my playlists", value="view"),
                tui.Choice(title="📦  Upload all downloaded episodes", value="bulk"),
                tui.Choice(title="🖼️   Upload a custom icon", value="icon_upload"),
                tui.Choice(title="✨  Backfill icons for a playlist", value="icon_backfill"),
                tui.Separator(),
                tui.Choice(title="← Back to main menu", value="back"),
            ],
        )
        if action in (None, "back"):
            return

        if action == "icon_upload":
            icon_upload_menu()
            continue

        if action == "icon_backfill":
            playlist_id = _pick_playlist("Backfill icons for which playlist?")
            if not playlist_id:
                continue
            force = False
            full = get_playlist_details(playlist_id)
            if full:
                existing = icon_factory.count_custom_icons(full)
                if existing:
                    force = bool(
                        tui.confirm(
                            f"{existing} chapter(s) already have a custom icon. "
                            "Regenerate all icons?",
                            default=False,
                        )
                    )
            stats = icon_factory.backfill_playlist_icons(playlist_id, force=force)
            tui.status(
                "ok" if stats["updated"] else "info",
                f"Done: {stats['updated']} updated, {stats['skipped']} already custom, "
                f"{stats['failed']} failed, of {stats['total']}.",
            )
            continue

        if action == "upload":
            playlist_id = _pick_playlist("Select a playlist")
            if not playlist_id:
                continue

            with tui.CONSOLE.status("[cyan]Fetching playlist details…", spinner="dots"):
                full_playlist = get_playlist_details(playlist_id)

            if mp3_path and episode_title:
                selected_episodes = [(mp3_path, episode_title)]
            else:
                episodes = find_episodes()
                selected_episodes = select_episode(episodes, full_playlist, multi_select=True)
                if not selected_episodes:
                    continue

            for path, title in selected_episodes:
                tui.status("info", f"Uploading: {title}")
                content_id = upload_to_yoto(path, title, playlist_id)
                if content_id:
                    tui.status("ok", f"Uploaded '{title}'.")
                else:
                    tui.status("err", f"Failed to upload '{title}'.")
            tui.status("ok", "All selected episodes processed.")
            continue

        if action == "add":
            tui.status("info", "Fetching your existing Yoto content…")
            content_items = get_yoto_content() or []
            if not content_items:
                tui.status("err", "Could not retrieve your content.")
                continue

            selected_content = tui.select(
                "Select content to add to a playlist",
                [
                    tui.Choice(
                        title=f"{item.get('title') or '?'}  [dim]{item.get('cardId') or '?'}[/]",
                        value=item,
                    )
                    for item in content_items
                ],
            )
            if not selected_content:
                continue

            playlist_id = _pick_playlist("Select the target playlist")
            if not playlist_id:
                continue

            content_id = selected_content.get("cardId")
            content_title = selected_content.get("title") or ""
            if not content_id:
                tui.status("err", "Selected content has no cardId.")
                continue

            if add_to_playlist(content_id, playlist_id, title=content_title):
                tui.status("ok", f"'{content_title}' added to playlist.")
            else:
                tui.status("err", f"Failed to add '{content_title}' to playlist.")
            continue

        if action == "create":
            title = tui.text(
                "Enter playlist name",
                validate=lambda s: bool(s.strip()) or "Name required",
            )
            if not title:
                continue
            description = tui.text("Enter description (optional)") or ""
            playlist_id = create_playlist(title.strip(), description.strip())
            if playlist_id:
                tui.status("ok", f"Created playlist '{title.strip()}' → {playlist_id}")
            else:
                tui.status("err", "Failed to create playlist.")
            continue

        if action == "view":
            playlists = get_yoto_playlists()
            if not playlists:
                tui.status("err", "Could not retrieve playlists.")
                continue
            tui.playlist_table(playlists)
            tui.pause()
            continue

        if action == "bulk":
            if not os.path.exists(podcast_dir):
                tui.status("err", f"Podcast directory not found: {podcast_dir}")
                continue

            picked = _pick_playlist("Select a playlist for bulk upload", include_none=True)
            if picked is None:
                continue
            playlist_id = None if picked == "__none__" else picked

            mp3_files = []
            for root, _dirs, files in os.walk(podcast_dir):
                for f in files:
                    if f.endswith(".mp3"):
                        mp3_files.append(os.path.join(root, f))
            if not mp3_files:
                tui.status("warn", "No MP3 files found.")
                continue

            tui.status("info", f"Found {len(mp3_files)} MP3 file(s). Starting upload…")
            for mp3_file in mp3_files:
                title = os.path.basename(mp3_file).replace(".mp3", "")
                tui.status("info", f"Processing: {title}")
                content_id = upload_to_yoto(mp3_file, title, playlist_id)
                if content_id:
                    tui.status("ok", f"Uploaded '{title}'.")
                else:
                    tui.status("err", f"Failed: '{title}'.")
            tui.status("ok", "Bulk upload completed.")
            continue

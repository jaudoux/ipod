from cgitb import grey
import feedparser
import requests
import os
import sys
import shutil
import subprocess
from tqdm import tqdm
from pydub import AudioSegment, silence
import yoto_api
import icon_factory

from termcolor import colored


# Preset podcasts with optional Yoto playlist/card ID for automatic upload
# Format: (name, rss_url, yoto_card_id or None)
PRESET_PODCASTS = {
    "1": (
        "🤠 Conte-moi l'aventure !",
        "https://feeds.audiomeans.fr/feed/2634f188-47a0-48e1-b4ae-0ac360ed3ac3.xml",
        "fTmjI",  # Yoto card ID
    ),
    "2": (
        "🐞 Bestiole",
        "https://radiofrance-podcast.net/podcast09/rss_22046.xml",
        "aRlyz",  # Yoto card ID
    ),
    "3": (
        "🎶 Les aventures d'Octave et Mélo",
        "https://radiofrance-podcast.net/podcast09/rss_23119.xml",
        "1YOQg",  # Yoto card ID
    ),
    "4": (
        "🧪 Curieux de sciences",
        "https://feed.ausha.co/Bqr2pcd8Aaqp",
        "942eV",  # Yoto card ID
    ),
    "5": (
        "🏛️ Quelle histoire",
        "https://feeds.acast.com/public/shows/quelle-histoire",
        "4yVD9",  # Yoto card ID
    ),
    "6": (
        "🔠 Petit vulgaire",
        "https://feeds.acast.com/public/shows/petit-vulgaire",
        "29BEO",  # Yoto card ID
    ),
    "7": (
        "💡 Qui a inventé",
        "https://feed.ausha.co/ygdr9TNV059K",
        "1TsMM",  # Yoto card ID
    ),
    "8": (
        "🪩 Discomobile",
        "https://radiofrance-podcast.net/podcast09/rss_24630.xml",
        "27u8W",
    ),
    "9": (
	"Mini mondes",
	"https://feeds.acast.com/public/shows/6798ff9d60e68f77d5aa65b0",
	"2kKr5",
    )
}


def display_ipod_logo():
    # ANSI Color Definitions (No White)
    frame = "\033[34m"  # Standard Blue
    text_main = "\033[96m"  # Bright Cyan
    screen_text = "\033[32m"  # Green (LCD look)
    subtitle = "\033[36m"  # Darker Cyan
    reset = "\033[0m"

    logo = f"""
{frame}          .----------.
{frame}          | -------- |    {text_main}_____  _____   ____  _____  
{frame}          | |  {screen_text}IPOD{frame}  |    {text_main}|_   _||  __ \ / __ \|  __ \ 
{frame}          | |  {screen_text}>_{frame}    |      {text_main}| |  | | | )| |  | | |  \ |
{frame}          | -------- |      {text_main}| |  |  ___/| |  | | |  | |
{frame}          |          |     {text_main}_| |_ | |    | |__| | |__| |
{frame}          |    _     |    {text_main}|_____||_|     \____/|_____/ 
{frame}          |  /   \   |
{frame}          | |  o  |  |    {subtitle}Interactive Podcast Downloader{reset}
{frame}          |  \ _ /   |    {frame}------------------------------{reset}
{frame}          '----------'{reset}
    """
    print(logo)


from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


def get_retry_session():
    """Returns a requests.Session with retry logic and a common User-Agent."""
    session = requests.Session()

    # Set a common browser User-Agent
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    session.headers.update(headers)

    retry = Retry(
        total=5,
        read=5,
        connect=5,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_smart_trim_point(audio_segment, min_silence_len=1000, silence_thresh=-45):
    """
    Scans the first 5 minutes for the first significant silence gap.
    """
    # Analyze only the first 5 minutes to save CPU
    intro_chunk = audio_segment[:300000]

    # detect_silence returns a list of [start, end] time periods
    silences = silence.detect_silence(
        intro_chunk, min_silence_len=min_silence_len, silence_thresh=silence_thresh
    )

    if silences:
        # We take the end of the very first silence found
        return silences[0][1]
    return 0


def download_file(url, filename, desc="Downloading"):
    """Download helper with a tqdm progress bar and retry logic."""
    session = get_retry_session()
    with session.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))
        with open(filename, "wb") as f, tqdm(
            desc=desc,
            total=total_size,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
        ) as bar:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                bar.update(len(chunk))


def process_audio_file(temp_file, final_filename):
    """
    Uses ffmpeg to extract a small chunk for analysis, finds silence,
    and then slices the original file without re-encoding.
    """
    analyze_file = "temp_analyze.wav"

    try:
        print(colored("Analyzing audio for ads (fast mode)...", "cyan"))
        # 1. Extract first 5 mins (300s) to WAV
        # -vn: no video
        # -ac 1: mono (faster)
        # -ar 16000: low sample rate (faster)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "quiet",
                "-i",
                temp_file,
                "-t",
                "300",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                analyze_file,
            ],
            check=True,
        )

        # 2. Load into pydub
        audio = AudioSegment.from_wav(analyze_file)
        trim_ms = get_smart_trim_point(audio)

        # 3. Slice
        if trim_ms > 0:
            start_sec = trim_ms / 1000.0
            print(
                colored(f"Ad detected! Cutting first {start_sec:.2f} seconds.", "green")
            )
            print(colored(f"Exporting: {final_filename}", "cyan"))

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "quiet",
                    "-i",
                    temp_file,
                    "-ss",
                    f"{start_sec:.3f}",
                    "-c",
                    "copy",
                    final_filename,
                ],
                check=True,
            )
        else:
            print(
                colored(
                    "No clear ad-break silence detected. Saving original.", "yellow"
                )
            )
            # Just copy
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "quiet",
                    "-i",
                    temp_file,
                    "-c",
                    "copy",
                    final_filename,
                ],
                check=True,
            )

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(
            colored(
                f"Fast processing failed ({e}). Falling back to slow legacy mode...",
                "red",
            )
        )
        # Fallback to original pydub method
        audio = AudioSegment.from_mp3(temp_file)
        trim_ms = get_smart_trim_point(audio)
        if trim_ms > 0:
            print(
                colored(
                    f"Ad detected! Cutting first {trim_ms/1000:.2f} seconds.", "green"
                )
            )
            final_audio = audio[trim_ms:]
        else:
            print(
                colored(
                    "No clear ad-break silence detected. Saving original.", "yellow"
                )
            )
            final_audio = audio

        print(colored(f"Exporting: {final_filename}", "cyan"))
        final_audio.export(final_filename, format="mp3")

    finally:
        if os.path.exists(analyze_file):
            os.remove(analyze_file)


def main_menu():
    display_ipod_logo()
    print("\n" + colored("=" * 30, "yellow"))
    print(colored(" PODCAST DOWNLOADER & AD-CUTTER ", "yellow", attrs=["bold"]))
    print(colored("=" * 30, "yellow"))

    while True:
        print("\nAvailable Podcasts:")
        for key, preset in PRESET_PODCASTS.items():
            print(f"[{colored(key, 'green')}] {preset[0]}")

        print(f"\n[{colored('Y', 'magenta')}] Yoto Player Integration")

        user_input = input(
            "\nEnter RSS Feed URL, Podcast Number, or Option (or type 'exit' to quit): "
        ).strip()

        if user_input.lower() == "exit":
            break

        if user_input.lower() == "y":
            # Create base download directory if it doesn't exist
            base_download_dir = "downloads"
            os.makedirs(base_download_dir, exist_ok=True)
            yoto_api.yoto_menu(base_download_dir)
            continue

        # Track if this is a preset podcast with Yoto card ID
        yoto_card_id = None
        if user_input in PRESET_PODCASTS:
            preset = PRESET_PODCASTS[user_input]
            rss_url = preset[1]
            # Get Yoto card ID if available (3rd element in tuple)
            if len(preset) > 2:
                yoto_card_id = preset[2]
            print(colored(f"Selected: {preset[0]}", "cyan"))
            if yoto_card_id:
                print(
                    colored(
                        f"  → Auto-upload to Yoto playlist: {yoto_card_id}", "magenta"
                    )
                )
        else:
            rss_url = user_input

        feed = feedparser.parse(rss_url)

        if not feed.entries:
            print("Error: Could not retrieve feed. Please check the URL.")
            continue

        podcast_name = feed.feed.title.replace("/", "-").strip()

        # Create download directories
        base_download_dir = "downloads"
        podcast_dir = os.path.join(base_download_dir, podcast_name)
        os.makedirs(podcast_dir, exist_ok=True)

        # If a Yoto card is linked, fetch the playlist so we can tell
        # "downloaded locally" apart from "actually synced on Yoto".
        yoto_playlist = None
        if yoto_card_id:
            print(colored("Fetching Yoto playlist state...", "magenta"))
            yoto_playlist = yoto_api.get_playlist_details(yoto_card_id)
            if not yoto_playlist:
                print(
                    colored(
                        "Could not fetch playlist — sync status will be unavailable.",
                        "yellow",
                    )
                )

        def is_synced(title):
            if not yoto_playlist:
                return False
            try:
                return yoto_api.is_episode_in_playlist(title, yoto_playlist)
            except Exception:
                return False

        # Persistent per-podcast icon cache (title → "yoto:#<mediaId>").
        icon_cache = icon_factory.load_cache(podcast_dir) if yoto_card_id else {}

        # Pagination settings
        page = 0
        per_page = 15
        total_episodes = len(feed.entries)
        total_pages = (total_episodes + per_page - 1) // per_page

        # Episode selection loop
        while True:
            print(
                colored(
                    f"\n--- {podcast_name} (Page {page + 1}/{total_pages}) ---",
                    "cyan",
                    attrs=["bold"],
                )
            )
            start_index = page * per_page
            end_index = start_index + per_page

            # Display episodes for the current page
            for i, entry in enumerate(feed.entries[start_index:end_index]):
                episode_title = entry.title.replace("/", "-").strip()
                final_filename = os.path.join(podcast_dir, f"{episode_title}.mp3")
                has_local = os.path.exists(final_filename)
                synced = is_synced(episode_title) if yoto_card_id else None

                prefix = f"[{colored(i, 'yellow')}]"
                if synced:
                    status = colored("(Synced)", "green")
                    print(f"{prefix} {colored(entry.title, 'blue')} {status}")
                elif has_local and yoto_card_id:
                    status = colored("(Downloaded, not synced)", "yellow")
                    print(f"{prefix} {colored(entry.title, 'blue')} {status}")
                elif has_local:
                    status = colored("(Downloaded)", "green")
                    print(f"{prefix} {colored(entry.title, 'blue')} {status}")
                else:
                    print(f"{prefix} {entry.title}")

            print(colored("\n[M]", "green") + " Back to Main Menu")
            nav_prompt = []
            if page > 0:
                nav_prompt.append(colored("[P]", "green") + "rev Page")
            if page < total_pages - 1:
                nav_prompt.append(colored("[N]", "green") + "ext Page")
            if yoto_card_id:
                nav_prompt.append(colored("[I]", "green") + "cons (backfill)")
            print(" | ".join(nav_prompt))
            print(
                colored(
                    "\nTip: Enter multiple numbers separated by spaces (e.g., '0 2 5') to download multiple episodes",
                    "grey",
                )
            )

            choice = (
                input(
                    colored(
                        "\nSelect episode(s), navigate pages, or go back to main menu: ",
                        "white",
                    )
                )
                .strip()
                .lower()
            )

            if choice == "m":
                break
            if choice == "n" and page < total_pages - 1:
                page += 1
                continue
            if choice == "p" and page > 0:
                page -= 1
                continue
            if choice == "i" and yoto_card_id:
                force = False
                if yoto_playlist:
                    existing = icon_factory.count_custom_icons(yoto_playlist)
                    if existing:
                        ans = (
                            input(
                                colored(
                                    f"\n{existing} chapter(s) already have a custom icon. "
                                    "Regenerate all icons? (y/N): ",
                                    "magenta",
                                )
                            )
                            .strip()
                            .lower()
                        )
                        force = ans == "y"
                stats = icon_factory.backfill_playlist_icons(
                    yoto_card_id, podcast_dir=podcast_dir, force=force
                )
                print(
                    colored(
                        f"\nIcon {'regenerate' if force else 'backfill'}: "
                        f"{stats['updated']} updated, "
                        f"{stats['skipped']} already custom, "
                        f"{stats['failed']} failed, of {stats['total']}.",
                        "cyan",
                    )
                )
                # Refresh the playlist so (Synced) indicators stay accurate.
                yoto_playlist = yoto_api.get_playlist_details(yoto_card_id)
                continue

            # Parse selection - support multiple episodes separated by spaces
            try:
                indices = [int(idx.strip()) for idx in choice.split()]
                if not indices:
                    raise ValueError("No indices provided")

                # Validate all indices
                selected_episodes = []
                for idx in indices:
                    absolute_idx = start_index + idx
                    if not (start_index <= absolute_idx < end_index):
                        print(colored(f"Invalid selection: {idx}. Skipping.", "red"))
                        continue
                    selected_episodes.append(feed.entries[absolute_idx])

                if not selected_episodes:
                    print(colored("No valid episodes selected.", "red"))
                    continue

            except ValueError:
                print(colored("Invalid selection. Try again.", "red"))
                continue

            # Process all selected episodes
            downloaded_episodes = []  # Track successfully downloaded episodes

            for selected_episode in selected_episodes:
                episode_title = selected_episode.title.replace("/", "-").strip()

                # Define file paths
                final_filename = os.path.join(podcast_dir, f"{episode_title}.mp3")
                temp_file = os.path.join(podcast_dir, "temp_processing.mp3")

                # Already synced on Yoto: nothing to do.
                if yoto_card_id and is_synced(episode_title):
                    print(
                        colored(
                            f"\n'{episode_title}' already synced on Yoto. Skipping.",
                            "yellow",
                        )
                    )
                    continue

                # Local file exists but not synced (e.g. previous transcoding
                # timeout). Reuse the local file and queue it for upload.
                if os.path.exists(final_filename):
                    if yoto_card_id:
                        print(
                            colored(
                                f"\n'{episode_title}' downloaded but not synced. "
                                "Queueing upload without re-downloading.",
                                "yellow",
                            )
                        )
                        downloaded_episodes.append((episode_title, final_filename))
                    else:
                        print(
                            colored(
                                f"\n'{episode_title}' already downloaded. Skipping.",
                                "yellow",
                            )
                        )
                    continue

                # Extract MP3 URL
                mp3_url = None
                for link in selected_episode.enclosures:
                    if link.type == "audio/mpeg":
                        mp3_url = link.href
                        break

                if not mp3_url:
                    print(
                        colored(
                            f"Could not find an MP3 link for '{episode_title}'.", "red"
                        )
                    )
                    continue

                # Execution
                temp_file = "temp_processing.mp3"
                print(colored(f"\nDownloading: {episode_title}", "cyan"))
                download_file(mp3_url, temp_file)

                process_audio_file(temp_file, final_filename)

                os.remove(temp_file)
                print(colored(f"Success! '{episode_title}' downloaded.", "green"))

                # Track downloaded episode
                downloaded_episodes.append((episode_title, final_filename))

            # After all downloads, handle Yoto upload
            if downloaded_episodes:
                print(
                    colored(
                        f"\n{len(downloaded_episodes)} episode(s) downloaded successfully!",
                        "green",
                    )
                )

                # If we have a Yoto card ID, automatically upload
                if yoto_card_id:
                    print(
                        colored(
                            f"\nAuto-uploading to Yoto playlist ({yoto_card_id})...",
                            "magenta",
                        )
                    )
                    for title, path in downloaded_episodes:
                        print(colored(f"\nUploading: {title}", "cyan"))
                        icon_ref = icon_factory.generate_icon_ref(title, icon_cache)
                        if icon_ref:
                            icon_factory.save_cache(podcast_dir, icon_cache)
                        content_id = yoto_api.upload_to_yoto(
                            path, title, yoto_card_id, icon_ref=icon_ref
                        )
                        if content_id:
                            print(
                                colored(
                                    f"Successfully uploaded '{title}' to playlist!",
                                    "green",
                                )
                            )
                        else:
                            print(colored(f"Failed to upload '{title}'.", "red"))
                    print(colored("\nAll episodes processed.", "cyan"))

                    # Refresh playlist so the episode list reflects new syncs
                    yoto_playlist = yoto_api.get_playlist_details(yoto_card_id)
                else:
                    # No preset card ID, ask user if they want to upload
                    yoto_choice = (
                        input(
                            colored(
                                "\nUpload downloaded episodes to Yoto playlist? (y/n): ",
                                "magenta",
                            )
                        )
                        .strip()
                        .lower()
                    )
                    if yoto_choice == "y":
                        yoto_api.yoto_menu(
                            podcast_dir, downloaded_episodes=downloaded_episodes
                        )


if __name__ == "__main__":
    main_menu()

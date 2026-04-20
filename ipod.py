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
import presets
import tui

from termcolor import colored


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


def get_smart_trim_point(audio_segment):
    """Find the end of an intro/ad break, if any, within the first 2 minutes.

    Kids-podcast heuristic:
    - Ignore speech-cadence pauses (< 1.5s).
    - Only consider silence *endings* between 10s and 75s. Below → likely
      just natural speech, not an ad boundary. Above → almost certainly
      eating real content on short kids episodes.
    - Pick the longest qualifying silence, not the first — the longest pause
      within the window is most likely the intentional transition between
      intro/ad and the actual content.

    Returns ms from start to trim to, or 0 for "keep original".
    """
    SEARCH_WINDOW_MS = 120_000  # first 2 minutes only
    MIN_SILENCE_LEN = 1500      # ms — filter out normal speech pauses
    SILENCE_THRESH = -40        # dBFS — a touch less strict than -45
    MIN_TRIM_MS = 10_000        # < 10s = noise
    MAX_TRIM_MS = 75_000        # > 75s = probably eating content

    intro_chunk = audio_segment[:SEARCH_WINDOW_MS]
    silences = silence.detect_silence(
        intro_chunk,
        min_silence_len=MIN_SILENCE_LEN,
        silence_thresh=SILENCE_THRESH,
    )

    candidates = [
        (start, end) for start, end in silences
        if MIN_TRIM_MS <= end <= MAX_TRIM_MS
    ]
    if not candidates:
        return 0

    start, end = max(candidates, key=lambda s: s[1] - s[0])
    return end


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


_CUSTOM_RSS = object()
_YOTO_MENU = object()
_MANAGE = object()
_EXIT = object()


def _build_main_choices(presets_list):
    choices = [tui.Choice(title=preset[0], value=preset) for preset in presets_list]
    choices.append(tui.Separator())
    choices.append(tui.Choice(title="🔗 Add a custom RSS URL…", value=_CUSTOM_RSS))
    choices.append(tui.Choice(title="🗂️  Manage podcasts", value=_MANAGE))
    choices.append(tui.Choice(title="🎵 Yoto menu", value=_YOTO_MENU))
    choices.append(tui.Choice(title="🚪 Exit", value=_EXIT))
    return choices


def _fetch_feed(rss_url):
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        tui.status("err", "Could not retrieve feed. Please check the URL.")
        return None
    return feed


def _preset_flow(preset):
    label, rss_url = preset[0], preset[1]
    yoto_card_id = preset[2] if len(preset) > 2 else None

    tui.status("info", f"Selected: {label}")
    if yoto_card_id:
        tui.status("info", f"Auto-upload target: Yoto playlist [bold]{yoto_card_id}[/]")

    feed = _fetch_feed(rss_url)
    if not feed:
        return

    podcast_name = feed.feed.title.replace("/", "-").strip()
    base_download_dir = "downloads"
    podcast_dir = os.path.join(base_download_dir, podcast_name)
    os.makedirs(podcast_dir, exist_ok=True)

    yoto_playlist = None
    if yoto_card_id:
        with tui.CONSOLE.status("[cyan]Fetching Yoto playlist state…", spinner="dots"):
            yoto_playlist = yoto_api.get_playlist_details(yoto_card_id)
        if not yoto_playlist:
            tui.status("warn", "Could not fetch playlist — sync status unavailable.")

    def is_synced(title):
        if not yoto_playlist:
            return False
        try:
            return yoto_api.is_episode_in_playlist(title, yoto_playlist)
        except Exception:
            return False

    icon_cache = icon_factory.load_cache(podcast_dir) if yoto_card_id else {}

    while True:
        tui.rule(podcast_name)

        actions = []
        if yoto_card_id:
            actions.append(
                tui.Choice(
                    title="⚡ Quick sync — download & upload all new episodes",
                    value="quick",
                )
            )
        actions.append(tui.Choice(title="📥 Browse & download episodes", value="browse"))
        if yoto_card_id:
            actions.append(tui.Choice(title="✨ Generate icons for this card", value="icons"))
            actions.append(
                tui.Choice(title="🔀 Reorder card (newest first)", value="reorder")
            )
        actions.append(tui.Separator())
        actions.append(tui.Choice(title="← Back to main menu", value="back"))

        action = tui.select("What do you want to do?", actions)
        if action in (None, "back"):
            return

        if action == "quick":
            yoto_playlist = _quick_sync_flow(
                feed, podcast_dir, yoto_card_id, yoto_playlist, is_synced, icon_cache
            )
            continue

        if action == "icons":
            yoto_playlist = _icons_flow(yoto_card_id, podcast_dir, yoto_playlist)
            continue

        if action == "reorder":
            if tui.confirm(
                "Reverse the chapter order on this card (newest first)?",
                default=True,
            ):
                yoto_api.reorder_playlist(yoto_card_id, mode="reverse")
                yoto_playlist = yoto_api.get_playlist_details(yoto_card_id)
            continue

        # browse
        yoto_playlist = _episodes_flow(
            feed, podcast_dir, yoto_card_id, yoto_playlist, is_synced, icon_cache
        )


def _icons_flow(yoto_card_id, podcast_dir, yoto_playlist):
    force = False
    if yoto_playlist:
        existing = icon_factory.count_custom_icons(yoto_playlist)
        if existing:
            force = bool(
                tui.confirm(
                    f"{existing} chapter(s) already have a custom icon. "
                    "Regenerate all icons?",
                    default=False,
                )
            )
    stats = icon_factory.backfill_playlist_icons(
        yoto_card_id, podcast_dir=podcast_dir, force=force
    )
    verb = "regenerate" if force else "backfill"
    tui.status(
        "ok" if stats["updated"] else "info",
        f"Icon {verb}: {stats['updated']} updated, "
        f"{stats['skipped']} already custom, "
        f"{stats['failed']} failed, of {stats['total']}.",
    )
    # Refresh so (Synced) dots stay accurate on the next browse.
    return yoto_api.get_playlist_details(yoto_card_id)


def _episodes_flow(feed, podcast_dir, yoto_card_id, yoto_playlist, is_synced, icon_cache):
    # Build one scrollable checkbox list — status dots inline.
    choices = []
    for entry in feed.entries:
        episode_title = entry.title.replace("/", "-").strip()
        final_filename = os.path.join(podcast_dir, f"{episode_title}.mp3")
        has_local = os.path.exists(final_filename)
        synced = is_synced(episode_title) if yoto_card_id else False
        choices.append(
            tui.episode_choice(
                entry.title,
                synced=synced,
                has_local=has_local,
                card_linked=bool(yoto_card_id),
                value=entry,
            )
        )

    selected_episodes = tui.checkbox(
        "Select episodes to download",
        choices,
    )
    if not selected_episodes:
        return yoto_playlist

    return _process_selected_episodes(
        selected_episodes,
        podcast_dir,
        yoto_card_id,
        yoto_playlist,
        is_synced,
        icon_cache,
    )


def _quick_sync_flow(feed, podcast_dir, yoto_card_id, yoto_playlist, is_synced, icon_cache):
    """Auto-select every feed entry not already on the Yoto card and run
    the download + upload pipeline on them after a single confirmation.
    """
    new_entries = [
        e for e in feed.entries
        if not is_synced(e.title.replace("/", "-").strip())
    ]
    if not new_entries:
        tui.status("ok", "Nothing to sync — every feed episode is already on Yoto.")
        return yoto_playlist

    preview_count = min(len(new_entries), 10)
    lines = [f"  • {e.title}" for e in new_entries[:preview_count]]
    if len(new_entries) > preview_count:
        lines.append(f"  … and {len(new_entries) - preview_count} more")
    tui.panel(
        f"Quick sync — {len(new_entries)} new episode(s)",
        "\n".join(lines),
        style="cyan",
    )

    if not tui.confirm(
        f"Download & upload all {len(new_entries)} new episodes?",
        default=len(new_entries) <= 10,
    ):
        return yoto_playlist

    return _process_selected_episodes(
        new_entries,
        podcast_dir,
        yoto_card_id,
        yoto_playlist,
        is_synced,
        icon_cache,
    )


def _process_selected_episodes(
    selected_episodes,
    podcast_dir,
    yoto_card_id,
    yoto_playlist,
    is_synced,
    icon_cache,
):
    """Download (if missing) + upload a batch of feedparser entries. Returns
    the refreshed `yoto_playlist` (so sync dots stay accurate on return).
    """
    downloaded_episodes = []

    for selected_episode in selected_episodes:
        episode_title = selected_episode.title.replace("/", "-").strip()
        final_filename = os.path.join(podcast_dir, f"{episode_title}.mp3")

        if yoto_card_id and is_synced(episode_title):
            tui.status("info", f"'{episode_title}' already synced on Yoto. Skipping.")
            continue

        if os.path.exists(final_filename):
            if yoto_card_id:
                tui.status(
                    "warn",
                    f"'{episode_title}' downloaded but not synced. "
                    "Queueing upload without re-downloading.",
                )
                downloaded_episodes.append((episode_title, final_filename))
            else:
                tui.status("info", f"'{episode_title}' already downloaded. Skipping.")
            continue

        mp3_url = None
        for link in selected_episode.enclosures:
            if link.type == "audio/mpeg":
                mp3_url = link.href
                break
        if not mp3_url:
            tui.status("err", f"Could not find an MP3 link for '{episode_title}'.")
            continue

        temp_file = "temp_processing.mp3"
        tui.status("info", f"Downloading: {episode_title}")
        download_file(mp3_url, temp_file)
        process_audio_file(temp_file, final_filename)
        os.remove(temp_file)
        tui.status("ok", f"'{episode_title}' downloaded.")
        downloaded_episodes.append((episode_title, final_filename))

    if not downloaded_episodes:
        return yoto_playlist

    tui.status("ok", f"{len(downloaded_episodes)} episode(s) ready.")

    if yoto_card_id:
        tui.status("info", f"Uploading to Yoto playlist {yoto_card_id}…")

        def resolve_icon(title):
            icon_ref = icon_factory.generate_icon_ref(title, icon_cache)
            if icon_ref:
                icon_factory.save_cache(podcast_dir, icon_cache)
            return icon_ref

        yoto_api.upload_many_to_playlist(
            [(path, title) for title, path in downloaded_episodes],
            yoto_card_id,
            icon_resolver=resolve_icon,
            max_workers=3,
        )
        tui.status("ok", "All episodes processed.")
        return yoto_api.get_playlist_details(yoto_card_id)

    if tui.confirm("Upload downloaded episodes to a Yoto playlist?", default=False):
        yoto_api.yoto_menu(podcast_dir, downloaded_episodes=downloaded_episodes)
    return yoto_playlist


def _preview_feed(url):
    """Fetch an RSS feed and return (feed, title, image_url, count) or
    (None, ...) on failure. Renders error state itself.
    """
    with tui.CONSOLE.status("[cyan]Fetching feed…", spinner="dots"):
        feed = feedparser.parse(url)
    if not feed.entries:
        tui.status("err", "Could not retrieve feed or it has no entries.")
        return None, None, None, 0
    feed_title = (feed.feed.get("title") or "").strip()
    feed_image = (feed.feed.get("image") or {}).get("href") or (
        feed.feed.get("image") or {}
    ).get("url")
    return feed, feed_title, feed_image, len(feed.entries)


def _show_feed_panel(title, image_url, count):
    lines = [f"[bold]{title or '?'}[/]", f"[dim]Episodes:[/] {count}"]
    if image_url:
        lines.append(f"[dim]Artwork:[/] {image_url}")
    else:
        lines.append("[yellow]No artwork in feed.[/]")
    tui.panel("Feed preview", "\n".join(lines))


def _add_podcast_flow():
    """Wizard: RSS URL → new Yoto card (with cover) → saved preset."""
    url = tui.text(
        "Paste the RSS feed URL",
        validate=lambda s: bool(s.strip()) or "URL required",
    )
    if not url:
        return
    url = url.strip()

    feed, feed_title, feed_image, count = _preview_feed(url)
    if not feed:
        return
    _show_feed_panel(feed_title, feed_image, count)

    name = tui.text(
        "Display name (feel free to prefix with an emoji)",
        default=feed_title,
    )
    if name is None:
        return
    name = name.strip() or feed_title

    if not tui.confirm(
        f"Create a new Yoto card '{name}' and link it to this feed?",
        default=True,
    ):
        return

    cover = None
    if feed_image:
        with tui.CONSOLE.status("[cyan]Uploading artwork to Yoto…", spinner="dots"):
            cover = yoto_api.upload_cover_image(feed_image, cover_type="podcast")
        if cover:
            tui.status("ok", "Artwork uploaded.")
        else:
            tui.status("warn", "Artwork upload failed — creating card without cover.")

    with tui.CONSOLE.status("[cyan]Creating Yoto card…", spinner="dots"):
        card_id = yoto_api.create_playlist(title=name, cover=cover)
    if not card_id:
        tui.status("err", "Failed to create Yoto card — preset not saved.")
        return

    presets.add((name, url, card_id))
    tui.status("ok", f"Added '{name}' → Yoto card [bold]{card_id}[/]")


def _attach_podcast_flow():
    """Wizard: RSS URL → pick an existing Yoto card → saved preset."""
    url = tui.text(
        "Paste the RSS feed URL",
        validate=lambda s: bool(s.strip()) or "URL required",
    )
    if not url:
        return
    url = url.strip()

    feed, feed_title, feed_image, count = _preview_feed(url)
    if not feed:
        return
    _show_feed_panel(feed_title, feed_image, count)

    name = tui.text(
        "Display name",
        default=feed_title,
    )
    if name is None:
        return
    name = name.strip() or feed_title

    card_id = yoto_api._pick_playlist("Pick the Yoto card to link")
    if not card_id:
        return

    presets.add((name, url, card_id))
    tui.status("ok", f"Linked '{name}' → Yoto card [bold]{card_id}[/]")


def _rename_podcast_flow(presets_list):
    if not presets_list:
        tui.status("info", "No presets to rename yet.")
        return
    picked = tui.select(
        "Rename which podcast?",
        [tui.Choice(title=p[0], value=p) for p in presets_list],
    )
    if picked is None:
        return
    new_name = tui.text("New display name", default=picked[0])
    if new_name is None:
        return
    new_name = new_name.strip()
    if not new_name or new_name == picked[0]:
        return
    if presets.rename(picked[1], new_name):
        tui.status("ok", f"Renamed to '{new_name}'.")
    else:
        tui.status("err", "Could not find that preset.")


def _manage_menu(presets_list):
    choices = [
        tui.Choice(title="➕ Add a new podcast", value="add"),
        tui.Choice(title="📎 Attach existing Yoto card", value="attach"),
    ]
    if presets_list:
        choices.append(tui.Choice(title="✏️  Rename a podcast", value="rename"))
    choices.append(tui.Separator())
    choices.append(tui.Choice(title="← Back", value="back"))

    action = tui.select("Manage podcasts", choices)
    if action in (None, "back"):
        return
    if action == "add":
        _add_podcast_flow()
    elif action == "attach":
        _attach_podcast_flow()
    elif action == "rename":
        _rename_podcast_flow(presets_list)


def main_menu():
    tui.banner()

    while True:
        presets_list = presets.load()
        choice = tui.select(
            "Choose a podcast or action", _build_main_choices(presets_list)
        )

        if choice is None or choice is _EXIT:
            tui.status("info", "Bye 👋")
            return

        if choice is _YOTO_MENU:
            os.makedirs("downloads", exist_ok=True)
            yoto_api.yoto_menu("downloads")
            continue

        if choice is _MANAGE:
            _manage_menu(presets_list)
            continue

        if choice is _CUSTOM_RSS:
            url = tui.text(
                "Paste the RSS feed URL",
                validate=lambda s: bool(s.strip()) or "URL required",
            )
            if not url:
                continue
            preset = ("Custom RSS", url.strip())
            _preset_flow(preset)
            continue

        # Regular preset tuple (name, rss_url[, yoto_card_id])
        _preset_flow(choice)


if __name__ == "__main__":
    main_menu()

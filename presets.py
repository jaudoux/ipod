"""Per-user podcast preset config.

Persists the podcast list as JSON at `~/.config/ipod/podcasts.json`. The
in-memory shape is a list of `(name, rss_url, yoto_card_id | None)` tuples —
same layout the rest of the app already expects. Data outside the repo,
survives clones and upgrades.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


CONFIG_PATH = Path.home() / ".config" / "ipod" / "podcasts.json"


Preset = tuple[str, str, str | None]


# Curated starter list for first-time users. Each entry must resolve to a real
# RSS feed — check new additions with feedparser before shipping them.
# (name, rss_url, tag). `tag` is a short one-liner shown next to the name.
SUGGESTIONS: list[dict] = [
    {
        "name": "Les Odyssées",
        "url": "https://radiofrance-podcast.net/podcast09/rss_20108.xml",
        "tag": "France Inter · 7–12 ans · Histoire",
    },
    {
        "name": "Bestioles",
        "url": "https://radiofrance-podcast.net/podcast09/rss_22046.xml",
        "tag": "France Inter · 5–7 ans · Nature & animaux",
    },
    {
        "name": "Une histoire et… Oli",
        "url": "https://radiofrance-podcast.net/podcast09/35099478-7c72-4f9e-a6de-1b928400e9e5/podcast_d555ed4e-dbe5-4908-912e-b3169f9ceede.xml",
        "tag": "France Inter · 5–7 ans · Histoires du soir",
    },
    {
        "name": "Les P'tits Bateaux",
        "url": "https://radiofrance-podcast.net/podcast09/rss_10137.xml",
        "tag": "France Inter · 5–10 ans · Questions d'enfants",
    },
    {
        "name": "Salut l'info !",
        "url": "https://radiofrance-podcast.net/podcast09/rss_20689.xml",
        "tag": "franceinfo × Astrapi · 7–11 ans · Actualité",
    },
    {
        "name": "Encore une histoire",
        "url": "https://access.acast.com/rss/670d1795df4dd6f896655670/",
        "tag": "Acast · 4+ · Contes et classiques",
    },
]


def _ensure_parent() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _entry_to_tuple(entry: dict) -> Preset:
    return (
        entry.get("name", ""),
        entry.get("rss_url", ""),
        entry.get("yoto_card_id") or None,
    )


def _tuple_to_entry(preset: Preset) -> dict:
    name, rss_url, card_id = preset
    out = {"name": name, "rss_url": rss_url}
    if card_id:
        out["yoto_card_id"] = card_id
    return out


def load() -> list[Preset]:
    """Read and return the preset list. Creates an empty config on first use."""
    if not CONFIG_PATH.exists():
        _ensure_parent()
        CONFIG_PATH.write_text("[]\n", encoding="utf-8")
        return []
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [_entry_to_tuple(e) for e in raw if isinstance(e, dict)]


def save(items: list[Preset]) -> None:
    """Atomic write: serialize to a tempfile in the same dir, then rename."""
    _ensure_parent()
    payload = json.dumps(
        [_tuple_to_entry(p) for p in items],
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    # Write to a temp file in the target dir, then replace atomically.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".podcasts.", suffix=".json", dir=str(CONFIG_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def add(item: Preset) -> None:
    """Append `item` to the list and persist."""
    items = load()
    items.append(item)
    save(items)


def rename(rss_url: str, new_name: str) -> bool:
    """Update the display name of the preset matching `rss_url`. Returns
    True if an entry was found and updated.
    """
    items = load()
    updated = False
    for i, (_name, url, card_id) in enumerate(items):
        if url == rss_url:
            items[i] = (new_name, url, card_id)
            updated = True
            break
    if updated:
        save(items)
    return updated

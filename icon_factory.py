"""Auto-generate per-episode Yoto pixel icons from titles.

Keyword extraction: tries a local Ollama model first (relevance), falls back
to an offline French→English noun dict when Ollama is unavailable. Then:
Iconify colored-emoji search → PNG render → Yoto icon upload →
inject `yoto:#{mediaId}` into chapter/track `display.icon16x16`.

Reuses: yoto_api.search_icons, download_icon_as_png, upload_custom_icon,
get_playlist_details, get_valid_token, DEFAULT_ICON_REF, YOTO_API_URL.
"""

import io
import json
import os
import re
import tempfile
import time
import unicodedata

import requests

try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

import tui
import yoto_api


ICON_CACHE_FILENAME = ".icon_cache.json"

# Yoto's public icon library (~530 native 16x16 pixel icons, many tagged "px"
# for the clean native set). Cached next to this script to avoid refetching.
YOTO_PUBLIC_ICONS_URL = "https://api.yotoplay.com/media/displayIcons/user/yoto"
YOTO_PUBLIC_ICONS_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "yoto_public_icons.json"
)

# Openverse: Creative Commons image search. Free, no API key, aggregates
# Wikimedia/Flickr/etc. We pick a PNG, pixel-quantize it, then upload.
OPENVERSE_URL = "https://api.openverse.org/v1/images/"

# Local Ollama config. Override via env if the user runs a different model/host.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:latest")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "15"))

# French stopwords, question words, pronouns, common auxiliaries.
_STOPWORDS = {
    "a", "ai", "au", "aux", "avec", "avoir", "c", "ca", "ce", "ces",
    "cet", "cette", "comme", "comment", "d", "dans", "de", "des",
    "deux", "du", "elle", "elles", "en", "est", "et", "etre", "eu",
    "faire", "fait", "il", "ils", "j", "je", "l", "la", "le", "les",
    "leur", "leurs", "ma", "mes", "mon", "n", "ne", "nos", "notre",
    "nous", "ou", "par", "pas", "peut", "plus", "pour", "pourquoi",
    "qu", "quand", "que", "qui", "quoi", "s", "sa", "sans", "se",
    "ses", "si", "son", "sont", "sous", "sur", "t", "ta", "tes",
    "ton", "tout", "tous", "toute", "toutes", "tu", "un", "une",
    "vers", "vient", "vont", "voient", "servent", "vraiment", "vrai",
    "presente", "episode", "podcast",
}

# Compact FR→EN noun map — covers common kids-podcast themes
# (animals, body, nature, science, history). Extend as needed.
_FR_EN = {
    "abeille": "bee",
    "algues": "seaweed",
    "ancetre": "ancestor",
    "arbre": "tree",
    "arbres": "tree",
    "araignee": "spider",
    "araignees": "spider",
    "aventure": "adventure",
    "aventures": "adventure",
    "baleine": "whale",
    "baleines": "whale",
    "bateau": "boat",
    "bebe": "baby",
    "bouche": "mouth",
    "bras": "arm",
    "cerveau": "brain",
    "chameau": "camel",
    "chameaux": "camel",
    "champignon": "mushroom",
    "chat": "cat",
    "chats": "cat",
    "chaleur": "fire",
    "cheval": "horse",
    "chevaux": "horse",
    "chien": "dog",
    "chiens": "dog",
    "ciel": "sky",
    "coeur": "heart",
    "dent": "tooth",
    "dents": "tooth",
    "desert": "desert",
    "dinosaure": "dinosaur",
    "dinosaures": "dinosaur",
    "dragon": "dragon",
    "eau": "water",
    "ecole": "school",
    "enfant": "child",
    "enfants": "child",
    "espace": "space",
    "etoile": "star",
    "etoiles": "star",
    "famille": "family",
    "feu": "fire",
    "fleur": "flower",
    "fleurs": "flower",
    "foret": "forest",
    "fromage": "cheese",
    "fruit": "fruit",
    "fusee": "rocket",
    "goutte": "water-drop",
    "histoire": "book",
    "hiver": "snowflake",
    "humain": "person",
    "humains": "people",
    "ile": "island",
    "insecte": "bug",
    "insectes": "bug",
    "instrument": "music",
    "inventer": "lightbulb",
    "invente": "lightbulb",
    "invention": "lightbulb",
    "jardin": "flower",
    "jour": "sun",
    "jouet": "toy",
    "lait": "milk",
    "langue": "tongue",
    "lapin": "rabbit",
    "lapins": "rabbit",
    "lecture": "book",
    "lion": "lion",
    "livre": "book",
    "livres": "book",
    "loup": "wolf",
    "lumiere": "lightbulb",
    "lune": "moon",
    "main": "hand",
    "maison": "house",
    "mer": "ocean",
    "mini": "sparkles",
    "mondes": "earth",
    "monde": "earth",
    "montagne": "mountain",
    "musique": "music",
    "nez": "nose",
    "noir": "moon",
    "nuage": "cloud",
    "nuit": "moon",
    "oeil": "eye",
    "oiseau": "bird",
    "oiseaux": "bird",
    "ouie": "ear",
    "ours": "bear",
    "pain": "bread",
    "papillon": "butterfly",
    "parler": "speech",
    "pierre": "rock",
    "pied": "foot",
    "pieds": "foot",
    "planete": "earth",
    "pluie": "rain",
    "poisson": "fish",
    "poissons": "fish",
    "pompier": "fire-truck",
    "prehistorique": "dinosaur",
    "prehistoriques": "dinosaur",
    "renard": "fox",
    "requin": "shark",
    "robot": "robot",
    "roi": "crown",
    "sable": "beach",
    "sciences": "microscope",
    "science": "microscope",
    "sens": "sparkles",
    "serpent": "snake",
    "singe": "monkey",
    "soleil": "sun",
    "soir": "moon",
    "souris": "mouse",
    "sport": "ball",
    "tete": "head",
    "toile": "spider-web",
    "toiles": "spider-web",
    "train": "train",
    "vache": "cow",
    "vent": "wind",
    "voiture": "car",
    "volcan": "volcano",
    "voir": "eye",
    "yeux": "eye",
}


_OLLAMA_PROMPT = (
    "You pick an icon for a kids' podcast episode. The title is usually in "
    "French. Translate it literally first — NEVER guess an English cognate "
    "that sounds similar but means something different (e.g. French 'tatou' "
    "means 'armadillo', NOT 'tattoo'; 'orque' means 'orca', NOT 'orchestra'; "
    "'taon' means 'horsefly', NOT 'cricket').\n\n"
    "Output 5 lowercase English single-word nouns that could stand in as a "
    "visual icon, most specific first. Comma-separated, no explanations, no "
    "numbering, no quotes.\n\n"
    "Examples:\n"
    "Title: Les 5 sens : l'ouie\nKeywords: ear, hearing, sound, head, body\n"
    "Title: À quoi servent les arbres ?\nKeywords: tree, forest, leaf, nature, plant\n"
    "Title: Comment les chats voient-ils dans le noir ?\nKeywords: cat, eye, night, animal, moon\n"
    "Title: D'où vient la première goutte d'eau ?\nKeywords: water, drop, rain, river, ocean\n"
    "Title: La cigogne\nKeywords: stork, bird, nest, wing, animal\n"
    "Title: Tatou\nKeywords: armadillo, shell, mammal, animal, ball\n"
    "Title: Hyène\nKeywords: hyena, predator, dog, mammal, animal\n"
    "Title: Le Taon\nKeywords: horsefly, fly, insect, bug, wing\n"
    "Title: Orque\nKeywords: orca, whale, dolphin, fish, sea\n\n"
    "Title: {title}\nKeywords:"
)


def _sanitize_words(raw: str) -> list[str]:
    """Parse a comma/space-separated keywords line from the model.

    Ollama sometimes echoes `title: ...` headers, repeats its answer, or
    hallucinates a second fake title+answer block. Take the FIRST answer
    line and ignore anything after a blank line.
    """
    if not raw:
        return []

    # Collect candidate lines (skip title/blank), then prefer one with commas
    # so a slug-style hallucination like "pourquoi-reve-ton" doesn't win over
    # the real answer "dream, brain, sleep, night, bed".
    candidates: list[str] = []
    for ln in raw.lower().splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("title:"):
            continue
        if stripped.startswith("keywords:"):
            stripped = stripped[len("keywords:"):].strip()
        if stripped:
            candidates.append(stripped)

    line = ""
    for c in candidates:
        if "," in c:
            line = c
            break
    if not line and candidates:
        line = candidates[0]

    tokens = re.split(r"[,\s]+", line)
    clean: list[str] = []
    for t in tokens:
        t = re.sub(r"[^a-z-]", "", t).strip("-")
        if 2 <= len(t) <= 30 and t not in clean:
            clean.append(t)
    return clean[:8]


def _ollama_keywords(title: str) -> list[str]:
    """Ask the local Ollama model for several English keywords. [] on failure."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": _OLLAMA_PROMPT.format(title=title),
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 40},
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    try:
        raw = response.json().get("response", "")
    except ValueError:
        return []
    return _sanitize_words(raw)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _tokenize(title: str) -> list[str]:
    """Lowercase, strip accents, split on non-letters, drop short tokens & stopwords."""
    flat = _strip_accents(title).lower()
    tokens = re.split(r"[^a-z]+", flat)
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def extract_keywords(title: str) -> list[str]:
    """Return ordered English keyword candidates for an episode title.

    Priority: (1) local Ollama suggestion, (2) bundled FR→EN noun map,
    (3) raw accent-stripped tokens. Every stage is optional — the worst case
    is an empty list, which callers handle as a fallback to the default icon.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(word: str | None):
        if word and word not in seen:
            seen.add(word)
            candidates.append(word)

    for kw in _ollama_keywords(title):
        add(kw)

    tokens = _tokenize(title)
    for tok in tokens:
        add(_FR_EN.get(tok))
    for tok in tokens:
        if tok not in _FR_EN:
            add(tok)
    return candidates


_yoto_icon_library: list[dict] | None = None


def _load_yoto_public_icons() -> list[dict]:
    """Return Yoto's public icon library. Cached in-memory and on disk."""
    global _yoto_icon_library
    if _yoto_icon_library is not None:
        return _yoto_icon_library

    if os.path.exists(YOTO_PUBLIC_ICONS_CACHE):
        try:
            with open(YOTO_PUBLIC_ICONS_CACHE, "r", encoding="utf-8") as f:
                _yoto_icon_library = json.load(f)
                return _yoto_icon_library
        except (json.JSONDecodeError, OSError):
            pass

    access_token = yoto_api.get_valid_token()
    if not access_token:
        _yoto_icon_library = []
        return _yoto_icon_library

    try:
        response = requests.get(
            YOTO_PUBLIC_ICONS_URL,
            headers={"Authorization": f"Bearer {access_token.strip()}"},
            timeout=15,
        )
    except requests.RequestException as e:
        tui.status("warn", f"Could not fetch Yoto icon library: {e}")
        _yoto_icon_library = []
        return _yoto_icon_library

    if response.status_code != 200:
        tui.status("warn", f"Yoto icon library fetch failed: {response.status_code}")
        _yoto_icon_library = []
        return _yoto_icon_library

    icons = response.json().get("displayIcons", [])
    _yoto_icon_library = icons
    try:
        with open(YOTO_PUBLIC_ICONS_CACHE, "w", encoding="utf-8") as f:
            json.dump(icons, f)
        tui.status("info", f"Cached {len(icons)} Yoto public icons.")
    except OSError:
        pass
    return _yoto_icon_library


def _keyword_variants(kw: str) -> set[str]:
    """Minimal plural/singular normalization so 'ears' matches 'ear'."""
    kw = kw.lower().strip().rstrip("-")
    if not kw:
        return set()
    out = {kw}
    if len(kw) > 3 and kw.endswith("s") and not kw.endswith("ss"):
        out.add(kw[:-1])
    if len(kw) > 4 and kw.endswith("es"):
        out.add(kw[:-2])
    # Allow singular → plural too so 'cat' matches a 'cats' tag.
    out.add(kw + "s")
    return out


# Show-licensed / derivative Yoto icons whose titles start with a franchise
# name. These are fine for their own cards but terrible as generic icons.
_LICENSED_PREFIXES = (
    "brainbots",
    "thomas and friends",
    "the witches",
    "yoto daily",
    "mr. men",
    "mr men",
    "little miss",
    "5 minute",
    "peppa pig",
    "paw patrol",
    "disney",
    "pokemon",
    "ben 10",
    "octonauts",
)


def _is_licensed(title: str) -> bool:
    t = (title or "").lower().strip()
    return any(t.startswith(p) for p in _LICENSED_PREFIXES)


def _score_icon_for_keywords(icon: dict, keywords: list[str]) -> tuple[int, str | None]:
    """Return (best_score, matched_keyword) for an icon against a ranked
    keyword list. Earlier (more specific) keywords score higher.
    """
    title = icon.get("title") or ""
    title_words = set(title.lower().split())
    tags = set(t.lower() for t in (icon.get("publicTags") or []))

    best_score = 0
    best_kw = None
    for rank, kw in enumerate(keywords):
        variants = _keyword_variants(kw)
        if not variants:
            continue
        if variants & tags:
            base = 30
        elif variants & title_words:
            base = 20
        else:
            continue

        score = base
        # Rank bonus: specific synonyms (rank 0-1) beat generic ones (rank 4+).
        score -= rank * 2
        # Boost the native pixel set.
        if "px" in tags:
            score += 5
        # Penalize show-licensed content heavily.
        if _is_licensed(title):
            score -= 25
        # Prefer short, focused titles.
        score -= len(title_words)
        # Prefer fewer tags (more generic icons).
        score -= len(tags) // 3

        if score > best_score:
            best_score = score
            best_kw = kw

    return best_score, best_kw


def _match_yoto_icon(keywords: list[str] | str) -> tuple[dict, str] | None:
    """Best-matching native Yoto icon across a list of ranked keywords.
    Accepts a single string too (back-compat). Returns (icon, matched_kw)
    or None.
    """
    icons = _load_yoto_public_icons()
    if not icons:
        return None
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k for k in keywords if k]
    if not keywords:
        return None

    best = None
    best_score = 0
    best_kw = None
    for icon in icons:
        score, kw = _score_icon_for_keywords(icon, keywords)
        if score > best_score:
            best_score = score
            best = icon
            best_kw = kw

    if best is None:
        return None
    return best, best_kw or keywords[0]


def _fetch_openverse_image(keyword: str) -> tuple[bytes, str] | None:
    """Search Openverse for a CC-licensed PNG. Returns (image_bytes, title) or None."""
    try:
        response = requests.get(
            OPENVERSE_URL,
            params={
                "q": keyword,
                "extension": "png",
                "page_size": 5,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None

    try:
        results = response.json().get("results") or []
    except ValueError:
        return None

    headers = {
        # Wikimedia (main Openverse source) 403s requests without a UA.
        "User-Agent": "podcast-crawl-yoto-icon-factory/1.0 (+github.com/seqone)",
    }
    for result in results:
        url = result.get("url")
        if not url:
            continue
        try:
            img_resp = requests.get(url, timeout=10, headers=headers)
        except requests.RequestException:
            continue
        if img_resp.status_code != 200 or not img_resp.content:
            continue
        return img_resp.content, (result.get("title") or keyword)[:60]
    return None


def _pixelize_to_tempfile(image_bytes: bytes) -> str | None:
    """Convert arbitrary PNG/JPG bytes into a chunky 16×16 PNG with a
    limited palette, saved to a temp file. Returns the path or None on failure.
    """
    if not _HAS_PIL:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGBA")

        # Center-crop to square so the subject isn't squashed.
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        # Pre-smooth to avoid jagged downsampling, then nearest-neighbor to 16×16.
        img = img.resize((32, 32), Image.LANCZOS)
        img = img.resize((16, 16), Image.NEAREST)

        # Quantize to a small palette so colors look pixel-art-ish, then back to RGBA
        # (Yoto's upload converter expects RGBA PNG, not indexed mode).
        palette = img.convert("RGB").quantize(colors=16, method=Image.MEDIANCUT)
        img = palette.convert("RGBA")

        temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(temp.name, format="PNG")
        temp.close()
        return temp.name
    except Exception as e:
        tui.status("warn", f"Pixelize failed: {e}")
        return None


def _upload_web_icon(keyword: str) -> tuple[str, str] | None:
    """Openverse → pixel-quantize → Yoto custom icon upload.
    Returns (icon_ref, source_title) on success.
    """
    if not _HAS_PIL:
        return None
    fetched = _fetch_openverse_image(keyword)
    if not fetched:
        return None
    image_bytes, source_title = fetched

    temp_path = _pixelize_to_tempfile(image_bytes)
    if not temp_path:
        return None

    try:
        result = yoto_api.upload_custom_icon(
            file_path=temp_path,
            filename=f"{keyword}.png",
            auto_convert=True,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if not result:
        return None
    media_id = result.get("mediaId")
    if not media_id:
        return None
    return f"yoto:#{media_id}", source_title


def load_cache(podcast_dir: str | None) -> dict:
    if not podcast_dir:
        return {}
    path = os.path.join(podcast_dir, ICON_CACHE_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(podcast_dir: str | None, cache: dict) -> None:
    if not podcast_dir:
        return
    path = os.path.join(podcast_dir, ICON_CACHE_FILENAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        tui.status("warn", f"Could not save icon cache: {e}")


def generate_icon_ref(
    title: str,
    cache: dict | None = None,
    *,
    force: bool = False,
) -> str | None:
    """Full pipeline: title → Iconify match → Yoto upload → `yoto:#{mediaId}`.

    Returns None if no keyword mapped, no icon found, or upload failed.
    Mutates `cache` in place (title → icon_ref). When `force` is True the
    cached entry for this title is ignored and overwritten.
    """
    if cache is None:
        cache = {}
    if not force and title in cache:
        return cache[title]

    keywords = extract_keywords(title)
    if not keywords:
        tui.status("warn", f"No keywords extracted from: {title!r}")
        return None

    # First pass: prefer native Yoto pixel icons (no upload, native 16x16).
    # Score all keywords globally so specific synonyms can still beat a weak
    # match on an earlier, more generic keyword.
    match = _match_yoto_icon(keywords)
    if match:
        icon, matched_kw = match
        icon_ref = f"yoto:#{icon['mediaId']}"
        cache[title] = icon_ref
        tui.CONSOLE.print(
            f"  [green]●[/] {title} [dim]→[/] Yoto «{icon.get('title') or '?'}» "
            f"[dim](via {matched_kw!r})[/]"
        )
        return icon_ref

    # Second pass: pull a CC-licensed PNG from Openverse and pixelate it.
    for keyword in keywords:
        web = _upload_web_icon(keyword)
        if web:
            icon_ref, source_title = web
            cache[title] = icon_ref
            tui.CONSOLE.print(
                f"  [green]●[/] {title} [dim]→[/] Web «{source_title}»"
            )
            return icon_ref

    # Fallback: search Iconify, render, and upload as a custom icon.
    for keyword in keywords:
        icons = yoto_api.search_icons(keyword, limit=1)
        time.sleep(0.15)  # be polite to Iconify
        if not icons:
            continue

        icon = icons[0]
        _png_bytes, temp_path = yoto_api.download_icon_as_png(icon, size=16)
        if not temp_path:
            continue

        try:
            result = yoto_api.upload_custom_icon(
                file_path=temp_path,
                filename=f"{icon['icon']}.png",
                auto_convert=True,
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        if not result:
            continue
        media_id = result.get("mediaId")
        if not media_id:
            continue

        icon_ref = f"yoto:#{media_id}"
        cache[title] = icon_ref
        tui.CONSOLE.print(
            f"  [green]●[/] {title} [dim]→[/] Iconify «{icon['name']}»"
        )
        return icon_ref

    tui.status("warn", f"No icon match for: {title!r}")
    return None


def _needs_icon(display: dict | None) -> bool:
    """True when a chapter/track has no custom icon (missing or default)."""
    if not display:
        return True
    icon = display.get("icon16x16")
    return not icon or icon == yoto_api.DEFAULT_ICON_REF


def count_custom_icons(playlist: dict) -> int:
    """How many chapters in a playlist already have a non-default icon."""
    chapters = (playlist.get("content") or {}).get("chapters") or []
    return sum(1 for c in chapters if not _needs_icon(c.get("display")))


def backfill_playlist_icons(
    playlist_id: str,
    podcast_dir: str | None = None,
    *,
    force: bool = False,
) -> dict:
    """Fetch a Yoto playlist, generate icons for chapters on the default icon,
    then POST one batched update back to /content.

    When `force` is True, chapters that already have a custom icon are also
    regenerated (cache is bypassed for those titles).

    Returns stats: {"updated": N, "skipped": M, "failed": K, "total": T}.
    """
    stats = {"updated": 0, "skipped": 0, "failed": 0, "total": 0}

    playlist = yoto_api.get_playlist_details(playlist_id)
    if not playlist:
        tui.status("err", "Could not fetch playlist.")
        return stats

    chapters = (playlist.get("content") or {}).get("chapters") or []
    stats["total"] = len(chapters)
    if not chapters:
        tui.status("warn", "Playlist has no chapters.")
        return stats

    cache = load_cache(podcast_dir)
    changed = False

    mode = "force regenerate" if force else "backfill"
    tui.status(
        "info",
        f"Scanning {len(chapters)} chapter(s) in playlist {playlist_id} ({mode})…",
    )

    for i, chapter in enumerate(chapters, 1):
        title = (chapter.get("title") or "").strip()
        display = chapter.get("display") or {}
        tracks = chapter.get("tracks") or []

        track_needs = any(_needs_icon(t.get("display")) for t in tracks)
        if not force and not _needs_icon(display) and not track_needs:
            stats["skipped"] += 1
            tui.CONSOLE.print(f"  [dim][{i}/{len(chapters)}][/] {title} [dim]— already custom[/]")
            continue

        if not title:
            stats["failed"] += 1
            continue

        tui.CONSOLE.print(f"  [cyan][{i}/{len(chapters)}][/] generating for [bold]{title}[/]…")
        icon_ref = generate_icon_ref(title, cache, force=force)
        if not icon_ref:
            stats["failed"] += 1
            continue

        chapter.setdefault("display", {})["icon16x16"] = icon_ref
        for track in tracks:
            track.setdefault("display", {})["icon16x16"] = icon_ref
        stats["updated"] += 1
        changed = True
        save_cache(podcast_dir, cache)

    if not changed:
        tui.status("warn", "No changes to push.")
        return stats

    tui.status("info", f"Pushing update to Yoto ({stats['updated']} chapter(s))…")
    if _post_playlist_update(playlist_id, playlist):
        tui.status("ok", "Playlist updated successfully.")
    else:
        tui.status("err", "Failed to update playlist.")
        # Keep the cache: icons were uploaded, we can retry the POST later.

    return stats


def _post_playlist_update(playlist_id: str, playlist: dict) -> bool:
    access_token = yoto_api.get_valid_token()
    if not access_token:
        return False
    clean_token = access_token.strip()

    payload = {
        "cardId": playlist_id,
        "title": playlist.get("title"),
        "content": playlist.get("content", {}),
        "metadata": playlist.get("metadata", {}),
    }
    try:
        response = requests.post(
            f"{yoto_api.YOTO_API_URL}/content",
            headers={
                "Authorization": f"Bearer {clean_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except requests.RequestException as e:
        tui.status("err", f"Network error updating playlist: {e}")
        return False

    if response.status_code in (200, 201, 204):
        return True
    tui.status("err", f"Update failed: {response.status_code} {response.text}")
    return False

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

# Apple emoji (via emoji-datasource-apple on unpkg). These are designed to
# read at small sizes, so they downsample to 16×16 much better than generic
# web imagery. We pull the 64px Apple sheet variant and LANCZOS-shrink it.
APPLE_EMOJI_META_URL = "https://unpkg.com/emoji-datasource-apple/emoji.json"
APPLE_EMOJI_IMG_URL = (
    "https://unpkg.com/emoji-datasource-apple/img/apple/64/{image}"
)
APPLE_EMOJI_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "emoji_apple.json"
)

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

# English fillers that must never be used as a visual keyword. Ollama
# sometimes emits "the"/"of"/"story" and several Yoto icons carry these
# as tag pollution, which would otherwise yield absurd matches like
# "Le lapin de velours → The Dahl – Blue Kite (via 'the')".
_EN_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "this",
    "that", "these", "those", "it", "its", "their", "his", "her", "he",
    "she", "they", "we", "our", "my", "your", "who", "what", "when",
    "where", "why", "how", "all", "any", "some", "no", "not", "one",
    "two", "three", "about", "into", "over", "under", "out", "up",
    "down", "if", "than", "then", "so", "too", "very",
    # generic narrative words with no distinctive visual
    "episode", "podcast", "story", "stories", "tale", "tales",
    "chapter", "part", "title", "name", "thing", "things", "world",
    "place", "people", "person", "someone", "something", "anything",
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
    "Output 5 lowercase English CONCRETE VISUAL NOUNS (things you can draw "
    "as a 16x16 pixel icon), most specific first. Comma-separated, no "
    "explanations, no numbering, no quotes.\n\n"
    "HARD RULES:\n"
    "- NEVER output articles, pronouns, prepositions, or generic fillers: "
    "no 'the', 'a', 'an', 'of', 'to', 'in', 'on', 'and', 'is', 'it', 'this', "
    "'that', 'story', 'tale', 'episode', 'chapter', 'thing', 'world', "
    "'people', 'someone', 'something', 'adventure', 'book' (unless the "
    "episode is literally about books).\n"
    "- Strip author names, series labels, 'd'après ...', 'inédit', and any "
    "'- X, d'après Y' sub-clauses. Focus on the STORY SUBJECT.\n"
    "- Proper nouns are fine only if iconic (e.g. 'aladdin' → 'lamp, genie').\n"
    "- Prefer one dominant subject + 3-4 visually related nouns.\n\n"
    "Examples:\n"
    "Title: Les 5 sens : l'ouie\nKeywords: ear, hearing, sound, head, body\n"
    "Title: À quoi servent les arbres ?\nKeywords: tree, forest, leaf, nature, plant\n"
    "Title: Comment les chats voient-ils dans le noir ?\nKeywords: cat, eye, night, animal, moon\n"
    "Title: D'où vient la première goutte d'eau ?\nKeywords: water, drop, rain, river, ocean\n"
    "Title: La cigogne\nKeywords: stork, bird, nest, wing, animal\n"
    "Title: Tatou\nKeywords: armadillo, shell, mammal, animal, ball\n"
    "Title: Hyène\nKeywords: hyena, predator, dog, mammal, animal\n"
    "Title: Le Taon\nKeywords: horsefly, fly, insect, bug, wing\n"
    "Title: Orque\nKeywords: orca, whale, dolphin, fish, sea\n"
    "Title: Olaf au pays du roi Hiver\nKeywords: snowflake, snowman, crown, winter, castle\n"
    "Title: Conte-moi l'aventure ! - L'île mystérieuse, d'après le roman de Jules Verne\nKeywords: island, treasure, map, ship, palm\n"
    "Title: Aladdin et la lampe merveilleuse\nKeywords: lamp, genie, magic, wish, carpet\n"
    "Title: Le lapin de velours\nKeywords: rabbit, toy, bunny, heart, stuffed\n"
    "Title: Le mariage de Skadi\nKeywords: viking, mountain, bride, norse, helmet\n\n"
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
        if len(t) < 3 or len(t) > 30:
            continue
        if t in _EN_STOPWORDS or t in clean:
            continue
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

    Priority: (1) literal title tokens that are themselves known visual
    concepts (emoji short-names), (2) local Ollama suggestion, (3) bundled
    FR→EN noun map, (4) raw accent-stripped tokens. Every stage is optional.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(word: str | None):
        if word and word not in seen:
            seen.add(word)
            candidates.append(word)

    tokens = _tokenize(title)

    # Promote raw title tokens that are direct emoji hits. Ollama tends to
    # decompose short titles into parts ("skateboard" → wheel, board, truck,
    # deck, grip), burying the literal subject. If the title already contains
    # a concrete visual noun, it should outrank synonyms.
    emoji_index = _load_emoji_index()
    if emoji_index:
        for tok in tokens:
            if tok in emoji_index:
                add(tok)

    for kw in _ollama_keywords(title):
        add(kw)

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
    # Roald Dahl series — titles like "Dahl - Blue Kite" use this prefix.
    "dahl",
    "roald dahl",
    # Other series spotted in Yoto's library that behave like franchise art.
    "julia donaldson",
    "beatrix potter",
    "gruffalo",
    "stick man",
    "hairy maclary",
)


# Franchise/brand tokens that flag a title as licensed even mid-string.
_LICENSED_TOKENS = {
    "dahl", "gruffalo", "peppa", "pokemon", "disney", "octonauts",
    "brainbots", "donaldson", "potter",
}


def _is_licensed(title: str) -> bool:
    t = (title or "").lower().strip()
    if any(t.startswith(p) for p in _LICENSED_PREFIXES):
        return True
    words = set(re.split(r"[^a-z]+", t))
    return bool(words & _LICENSED_TOKENS)


def _score_icon_for_keywords(icon: dict, keywords: list[str]) -> tuple[int, str | None]:
    """Return (best_score, matched_keyword) for an icon against a ranked
    keyword list. Earlier (more specific) keywords score higher.
    """
    title = icon.get("title") or ""
    # Strip punctuation so "Cat, animal" tokenizes to {'cat', 'animal'},
    # not {'cat,', 'animal'} — otherwise title-word matches get missed.
    title_words = {w for w in re.split(r"[^a-z0-9-]+", title.lower()) if w}
    tags = set(t.lower() for t in (icon.get("publicTags") or []))

    # Ignore filler words on the icon side too: several Yoto icons are tagged
    # with articles like 'the'/'of' (book titles), which would otherwise let
    # any stray keyword match them. Only *content* tokens count as evidence.
    meaningful_tags = tags - _EN_STOPWORDS
    meaningful_title_words = {w for w in title_words if w not in _EN_STOPWORDS}

    best_score = 0
    best_kw = None
    matched_kws: set[str] = set()
    for rank, kw in enumerate(keywords):
        if kw in _EN_STOPWORDS:
            continue
        variants = _keyword_variants(kw) - _EN_STOPWORDS
        if not variants:
            continue
        if variants & meaningful_tags:
            base = 30
        elif variants & meaningful_title_words:
            base = 20
        else:
            continue

        matched_kws.add(kw)
        score = base
        # Rank bonus: specific synonyms (rank 0-1) beat generic ones (rank 4+).
        score -= rank * 2
        # Boost the native pixel set.
        if "px" in tags:
            score += 5
        # Penalize show-licensed content heavily.
        if _is_licensed(title):
            score -= 25
        # Empty/symbol-only titles carry no visual identity we can verify
        # from the log — only accept them when the tag evidence is strong
        # (≥2 meaningful tag hits), else downrank.
        if not meaningful_title_words:
            if len(variants & meaningful_tags) < 2:
                score -= 10
        # Prefer short, focused titles.
        score -= len(title_words)
        # Prefer fewer tags (more generic icons).
        score -= len(tags) // 3

        if score > best_score:
            best_score = score
            best_kw = kw

    # Title-word coverage bonus: reward icons whose own title words literally
    # ARE our keywords (or their variants). "Tennis Ball" with title words
    # {tennis, ball} both in keywords is semantically nailed on; "Rugby" /
    # "Mushroom" titled with words NOT in keywords are loose tag matches.
    # This is a stronger specificity signal than raw multi-keyword coverage,
    # which over-rewards icons with many atmospheric tags (nature/forest).
    if meaningful_title_words and best_kw is not None:
        keyword_variants_all: set[str] = set()
        for kw in matched_kws:
            keyword_variants_all |= _keyword_variants(kw)
        covered = meaningful_title_words & keyword_variants_all
        best_score += 2 * len(covered)

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
    keywords = [k for k in keywords if k and k not in _EN_STOPWORDS]
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


def _best_icon_candidate(keywords: list[str]):
    """Score Yoto icons AND Apple emoji in one pass, return the top-scoring.

    Returns a tuple (kind, payload, matched_kw, score) where:
      - kind='yoto',  payload=icon dict from Yoto's public library
      - kind='emoji', payload=emoji image filename (e.g. '1f430.png')
    Returns None if no candidate crosses the threshold.

    Emoji get the same base score as a Yoto tag hit (30), so the two
    sources compete on equal footing. The usual rank/pixel/license bonuses
    still apply on the Yoto side; emoji only pay a rank penalty. Ties go
    to Yoto (it enumerates first) — that's fine since a clean Yoto native
    pixel icon is strictly better than a downsampled emoji when both are
    equally specific.
    """
    keywords = [k for k in keywords if k and k not in _EN_STOPWORDS]
    if not keywords:
        return None

    best = None  # (score, kind, payload, matched_kw)

    for icon in _load_yoto_public_icons():
        score, kw = _score_icon_for_keywords(icon, keywords)
        if score > 0 and (best is None or score > best[0]):
            best = (score, "yoto", icon, kw)

    emoji_index = _load_emoji_index()
    if emoji_index:
        for rank, kw in enumerate(keywords):
            for variant in _keyword_variants(kw):
                if variant in _EN_STOPWORDS:
                    continue
                image = emoji_index.get(variant)
                if not image:
                    continue
                # Emoji short_names are hand-curated and 1-to-1 with the
                # concept, so a later-ranked match is still gold. Use a
                # softer rank penalty (×1) than Yoto (×2). Base 30 still
                # mirrors a Yoto tag hit so rank-0 parity holds.
                score = 30 - rank
                if best is None or score > best[0]:
                    best = (score, "emoji", image, kw)
                break  # first matching variant for this keyword is enough

    if best is None:
        return None
    score, kind, payload, kw = best
    return kind, payload, kw, score


_emoji_index: dict[str, str] | None = None


def _load_emoji_index() -> dict[str, str]:
    """Return keyword → Apple-emoji image filename (e.g. '1f430.png').

    Skips skin-tone-modified variants and ZWJ families — their short_names
    rarely match kids-podcast keywords and we don't want, say, a keyword
    'family' to resolve to a specific family-composition emoji.
    """
    global _emoji_index
    if _emoji_index is not None:
        return _emoji_index

    data = None
    if os.path.exists(APPLE_EMOJI_CACHE):
        try:
            with open(APPLE_EMOJI_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    if data is None:
        try:
            r = requests.get(APPLE_EMOJI_META_URL, timeout=15)
            if r.status_code != 200:
                _emoji_index = {}
                return _emoji_index
            data = r.json()
            try:
                with open(APPLE_EMOJI_CACHE, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                tui.status("info", f"Cached {len(data)} Apple emoji entries.")
            except OSError:
                pass
        except (requests.RequestException, ValueError):
            _emoji_index = {}
            return _emoji_index

    # Skin-tone modifiers (1F3FB–1F3FF) appear as hyphen-joined segments.
    skin_tones = {"1f3fb", "1f3fc", "1f3fd", "1f3fe", "1f3ff"}

    def _is_flag(parts: list[str]) -> bool:
        # Regional indicator symbols (1F1E6–1F1FF) form country flags.
        if len(parts) != 2:
            return False
        return all(
            p.startswith("1f1") and "e6" <= p[3:] <= "ff" for p in parts
        )

    index: dict[str, str] = {}
    for entry in data:
        if not entry.get("has_img_apple"):
            continue
        image = entry.get("image")
        unified = (entry.get("unified") or "").lower()
        if not image or not unified:
            continue
        parts = unified.split("-")
        if any(p in skin_tones for p in parts):
            continue
        if len(parts) > 2 or _is_flag(parts):
            continue

        # Only use curated short names. Tokenizing the free-form `name`
        # leaks bad matches: e.g. 'EAR OF CORN' contributes 'ear' (body
        # part keyword hits a corn emoji), and 'ASCENSION ISLAND' makes a
        # flag steal the 'island' keyword.
        names: set[str] = set()
        if entry.get("short_name"):
            names.add(entry["short_name"])
        names.update(entry.get("short_names") or [])
        for n in names:
            n = n.lower().replace("_", "-")
            if n and n not in _EN_STOPWORDS and n not in index:
                index[n] = image
    _emoji_index = index
    return _emoji_index


def _emoji_to_16x16_png(image_bytes: bytes) -> str | None:
    """Downsample an Apple emoji PNG to a 16×16 RGBA PNG, preserving alpha.

    Apple emoji are already centered and square, so we can skip the crop/
    quantize steps from `_pixelize_to_tempfile` and get a cleaner result
    with a single LANCZOS pass.
    """
    if not _HAS_PIL:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img = img.resize((16, 16), Image.LANCZOS)
        temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(temp.name, format="PNG")
        temp.close()
        return temp.name
    except Exception as e:
        tui.status("warn", f"Emoji pixelize failed: {e}")
        return None


def _find_emoji_for_keyword(keyword: str) -> str | None:
    """Return the image filename of an Apple emoji matching keyword, else None."""
    index = _load_emoji_index()
    if not index:
        return None
    for variant in _keyword_variants(keyword):
        if variant in _EN_STOPWORDS:
            continue
        image = index.get(variant)
        if image:
            return image
    return None


def _upload_emoji_image(image_name: str) -> str | None:
    """Fetch a specific Apple emoji PNG, pixelize, upload. Returns icon_ref."""
    if not _HAS_PIL:
        return None
    try:
        r = requests.get(APPLE_EMOJI_IMG_URL.format(image=image_name), timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.content:
        return None

    temp_path = _emoji_to_16x16_png(r.content)
    if not temp_path:
        return None

    try:
        result = yoto_api.upload_custom_icon(
            file_path=temp_path,
            filename=f"emoji_{image_name}",
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
    return f"yoto:#{media_id}"


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


# Cache entries are either a bare icon_ref string (legacy) or a dict with
# {"ref": icon_ref, "emoji": "🐳", "source": "emoji"|"yoto"|"web"|"iconify"}.
# The richer form lets the TUI show the chosen emoji next to titles.


def cached_ref(cache: dict, title: str) -> str | None:
    """Return the icon_ref stored for `title`, normalizing legacy/dict forms."""
    entry = cache.get(title)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("ref")
    return entry


def cached_emoji(cache: dict, title: str) -> str | None:
    """Return the emoji glyph stored for `title`, or None."""
    entry = cache.get(title)
    if isinstance(entry, dict):
        return entry.get("emoji")
    return None


def _set_cache(
    cache: dict, title: str, ref: str, *, emoji: str | None, source: str
) -> None:
    entry: dict = {"ref": ref, "source": source}
    if emoji:
        entry["emoji"] = emoji
    cache[title] = entry


def _emoji_char_from_filename(image_name: str) -> str:
    """Convert an emoji-datasource filename like '1f39e-fe0f.png' to '🎞️'.

    Returns '' if the filename isn't parseable as hex codepoints — callers
    can truthiness-check the result.
    """
    stem = image_name.rsplit(".", 1)[0]
    try:
        return "".join(chr(int(p, 16)) for p in stem.split("-") if p)
    except ValueError:
        return ""


_LEADING_PARTICLES_RE = re.compile(
    r"^(?:"
    r"[-:–—·|]+\s*"           # leading dash / colon / bullet
    r"|d['’]\s*"               # d' / d’
    r"|de\s+(?:la\s+|l['’]\s*|les\s+|l\s*)?"
    r"|du\s+|des\s+"
    r"|le\s+|la\s+|les\s+|l['’]\s*"
    r")+",
    flags=re.IGNORECASE,
)


def _normalize_word(w: str) -> str:
    """Lowercase + strip trailing punctuation for prefix comparison."""
    return w.lower().rstrip(",.!?:;'\"’)]}")


def detect_series_prefixes(titles: list[str]) -> dict[str, str]:
    """Map each title to its post-prefix remainder when ≥2 episodes share a
    ≥2-word prefix. Titles with no shared prefix are absent from the result.

    Recurring podcast series repeat the show name in every episode title
    (e.g. "La Discomobile de X"). That prefix dominates keyword extraction
    across the whole playlist, so every episode gets the same icon. Stripping
    it forces the matcher to look at the episode-specific remainder.
    """
    if len(titles) < 2:
        return {}

    tokenized: list[tuple[str, list[str]]] = []
    for t in titles:
        if not t:
            continue
        words = t.split()
        if words:
            tokenized.append((t, words))
    if len(tokenized) < 2:
        return {}

    # Count how many titles share each normalized word-prefix of length ≥2.
    prefix_counts: dict[tuple, int] = {}
    for _, words in tokenized:
        for n in range(2, len(words) + 1):
            key = tuple(_normalize_word(w) for w in words[:n])
            prefix_counts[key] = prefix_counts.get(key, 0) + 1

    result: dict[str, str] = {}
    for original, words in tokenized:
        # Longest prefix, ≥2 words, ≥2 titles share it, leaves ≥1 word behind.
        best_n = 0
        for n in range(2, len(words)):
            key = tuple(_normalize_word(w) for w in words[:n])
            if prefix_counts.get(key, 0) >= 2:
                best_n = n
        if best_n == 0:
            continue
        remainder = " ".join(words[best_n:])
        remainder = _LEADING_PARTICLES_RE.sub("", remainder).strip()
        if remainder and remainder != original:
            result[original] = remainder
    return result


def generate_icon_ref(
    title: str,
    cache: dict | None = None,
    *,
    force: bool = False,
    keyword_source: str | None = None,
) -> str | None:
    """Full pipeline: title → Iconify match → Yoto upload → `yoto:#{mediaId}`.

    Returns None if no keyword mapped, no icon found, or upload failed.
    Mutates `cache` in place (title → icon_ref). When `force` is True the
    cached entry for this title is ignored and overwritten.

    `keyword_source` lets callers pass a different string for keyword
    extraction while still keying the cache and log output on the real
    `title` — used by `backfill_playlist_icons` to strip recurring series
    prefixes without losing cache stability.
    """
    if cache is None:
        cache = {}
    if not force:
        existing = cached_ref(cache, title)
        if existing:
            return existing

    keywords = extract_keywords(keyword_source if keyword_source else title)
    if not keywords:
        tui.status("warn", f"No keywords extracted from: {title!r}")
        return None

    # Unified pass: Yoto native icons AND Apple emoji compete on the same
    # score. Whichever has the more specific semantic match wins.
    candidate = _best_icon_candidate(keywords)
    if candidate:
        kind, payload, matched_kw, _score = candidate
        if kind == "yoto":
            icon = payload
            icon_ref = f"yoto:#{icon['mediaId']}"
            _set_cache(cache, title, icon_ref, emoji=None, source="yoto")
            label = icon.get("title") or ""
            if not label.strip():
                tag_sample = ", ".join((icon.get("publicTags") or [])[:3])
                label = f"[{tag_sample}]" if tag_sample else "?"
            tui.CONSOLE.print(
                f"  [green]●[/] {title} [dim]→[/] Yoto «{label}» "
                f"[dim](via {matched_kw!r})[/]"
            )
            return icon_ref
        # kind == "emoji"
        icon_ref = _upload_emoji_image(payload)
        if icon_ref:
            emoji_char = _emoji_char_from_filename(payload)
            _set_cache(cache, title, icon_ref, emoji=emoji_char, source="emoji")
            glyph = f"{emoji_char}  " if emoji_char else ""
            tui.CONSOLE.print(
                f"  [green]●[/] {title} [dim]→[/] {glyph}Apple emoji «{payload}» "
                f"[dim](via {matched_kw!r})[/]"
            )
            return icon_ref
        # emoji upload failed — fall through to Openverse/Iconify
        tui.status("warn", f"Emoji upload failed for {title!r} ({payload}).")

    # Fallback: pull a CC-licensed PNG from Openverse and pixelate it.
    for keyword in keywords:
        web = _upload_web_icon(keyword)
        if web:
            icon_ref, source_title = web
            _set_cache(cache, title, icon_ref, emoji=None, source="web")
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
        _set_cache(cache, title, icon_ref, emoji=None, source="iconify")
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

    all_titles = [(c.get("title") or "").strip() for c in chapters]
    series_prefixes = detect_series_prefixes(all_titles)
    if series_prefixes:
        # Show a sample so the user sees what's being stripped.
        sample_orig = next(iter(series_prefixes))
        sample_strip = series_prefixes[sample_orig]
        tui.status(
            "info",
            f"Series prefix detected — stripping for {len(series_prefixes)} "
            f"chapter(s). e.g. {sample_orig!r} → {sample_strip!r}",
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
        keyword_source = series_prefixes.get(title)
        icon_ref = generate_icon_ref(
            title, cache, force=force, keyword_source=keyword_source
        )
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


def regenerate_chapter_icon(
    playlist_id: str,
    chapter_title: str,
    podcast_dir: str | None = None,
) -> bool:
    """Regenerate and push the icon for a single chapter, bypassing the cache.

    Useful for iterating on the matcher without rerunning a whole playlist
    backfill. Returns True on successful push, False otherwise.
    """
    playlist = yoto_api.get_playlist_details(playlist_id)
    if not playlist:
        tui.status("err", "Could not fetch playlist.")
        return False

    chapters = (playlist.get("content") or {}).get("chapters") or []
    target = next(
        (c for c in chapters if (c.get("title") or "").strip() == chapter_title),
        None,
    )
    if not target:
        tui.status("err", f"Chapter not found: {chapter_title!r}")
        return False

    all_titles = [(c.get("title") or "").strip() for c in chapters]
    series_prefixes = detect_series_prefixes(all_titles)
    keyword_source = series_prefixes.get(chapter_title)

    cache = load_cache(podcast_dir)
    icon_ref = generate_icon_ref(
        chapter_title, cache, force=True, keyword_source=keyword_source
    )
    if not icon_ref:
        tui.status("err", f"No icon generated for {chapter_title!r}.")
        return False
    save_cache(podcast_dir, cache)

    target.setdefault("display", {})["icon16x16"] = icon_ref
    for track in target.get("tracks") or []:
        track.setdefault("display", {})["icon16x16"] = icon_ref

    tui.status("info", f"Pushing update to Yoto…")
    if not _post_playlist_update(playlist_id, playlist):
        tui.status("err", "Failed to update playlist.")
        return False
    tui.status("ok", f"Icon updated for {chapter_title!r}.")
    return True


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

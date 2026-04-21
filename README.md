# iPod — Podcast → Yoto

A tiny terminal app that turns any kids-podcast RSS feed into a
[Yoto](https://yotoplay.com) MYO card — ads trimmed, pixel icons generated,
episodes ordered newest-first.

<p align="center">
  <img src="docs/screenshot.svg" alt="iPod main menu" width="640"/>
</p>

## What you get

- 🎧 **Pick a feed** — curated French starter list (Les Odyssées, Bestioles,
  Oli, Les P'tits Bateaux, Salut l'info !, Encore une histoire) or your own RSS.
- ✂️ **Ad-free audio** — auto-detects the first long silence and trims the
  intro ad.
- 🖼️ **Pixel icon per episode** — matches against Yoto's native library first,
  then Openverse (CC images), then Iconify emoji. No hand-picking.
- 🔀 **Newest on top** — new episodes are prepended to the card; a one-shot
  reorder action fixes legacy cards.
- ⚡ **Quick sync** — one keystroke downloads + uploads every episode not
  already on the card.
- 🟢 **Status dots** — `●` synced, `◌` downloaded, `○` not yet fetched. No
  re-downloading.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/jaudoux/ipod/main/install.sh | bash
```

Then `ipod`. On first run, paste your [Yoto developer](https://dashboard.yoto.dev/)
Client ID — the app handles OAuth in your browser.

Needs Python 3.10+, FFmpeg (`brew install ffmpeg`), and optionally
[Ollama](https://ollama.com) for smarter icon matching on French titles.

---

## Under the hood

### Ad trim

`pydub.silence` scans the first 5 minutes of each episode for the longest
silent stretch (≥1s, −40 dBFS floor). Everything before that cut is dropped —
which for most French kids podcasts neatly removes the sponsor spot and jingle
without touching the story.

### Icon matching

For each episode title the matcher walks three sources and stops at the first
hit:

1. **Yoto native library** — asks Ollama for 5 English keyword synonyms, scores
   every public Yoto icon (tag match = 30 pts, title-word match = 20 pts),
   boosts the `px`-tagged native pixel set, penalizes licensed-character icons
   (BrainBots, Thomas & Friends, Disney, Peppa Pig…). Referenced by `mediaId` —
   no upload needed.
2. **Openverse** — top CC-licensed PNG, center-cropped, Lanczos-downsampled,
   nearest-neighbored to 16×16, 16-color-quantized, uploaded as a custom icon.
3. **Iconify emoji** — Noto / Twemoji / Fluent-Emoji / Flat-Color-Icons SVG
   rendered to 16×16 PNG and uploaded.

Generated icons are cached per podcast in
`downloads/<podcast>/.icon_cache.json` so re-runs are free.

### Ollama

When `http://localhost:11434` answers, the matcher uses a local chat model
(default `llama3.1:latest`) to turn a French episode title into 5 ranked
English synonyms. Non-deterministic, but temperature is kept low (0.3).
If Ollama is offline, the matcher falls back to a bundled French→English
dictionary plus raw title tokens — matching still works, just less sharply.

### Upload pipeline

- **Resilient** — "downloaded locally" and "synced on Yoto" are tracked
  separately, so a failed transcode doesn't force a re-download.
- **Exponential-backoff polling** — after upload, transcode status is polled
  at 2s, 4s, 8s… capped at 60s over 10 attempts.
- **Newest-first** — new chapters are prepended, not appended. The 🔀 Reorder
  action reverses existing cards as a one-shot migration.

## Configuration

Environment variables (all optional):

| Variable         | Default                                 | Purpose                                   |
| ---------------- | --------------------------------------- | ----------------------------------------- |
| `OLLAMA_URL`     | `http://localhost:11434/api/generate`   | Ollama endpoint.                          |
| `OLLAMA_MODEL`   | `llama3.1:latest`                       | Model used for keyword extraction.        |
| `OLLAMA_TIMEOUT` | `15`                                    | Seconds before the Ollama request gives up. |

Local files:

- `~/.config/ipod/podcasts.json` — your podcast list. Hand-editable JSON.
- `yoto_config.json` — your Yoto client ID. *(gitignored)*
- `yoto_tokens.json` — OAuth access + refresh tokens. *(gitignored)*
- `yoto_public_icons.json` — cached Yoto icon library (~190 KB). *(gitignored)*
- `downloads/` — audio files and per-podcast icon cache. *(gitignored)*

## Project layout

```
ipod.py          # top-level menu, episode browser, download/upload orchestration
yoto_api.py      # OAuth device flow, content/media/icon endpoints, yoto_menu
icon_factory.py  # keyword extraction, icon matching, backfill
presets.py       # per-user podcast list persisted at ~/.config/ipod/podcasts.json
tui.py           # shared questionary + rich primitives
logo.py          # the ASCII Yoto-Mini banner
```

## Known limitations

- The Iconify fallback needs an SVG→PNG renderer on `PATH` (`cairosvg`,
  `rsvg-convert`, ImageMagick, or macOS `sips`).
- Ollama keyword extraction is non-deterministic; unusual French titles can
  still confuse it — the French→English dictionary and raw tokens are tried
  as backups.
- A few Yoto icons are licensed content (BrainBots, Thomas & Friends, Disney,
  Peppa Pig…). The matcher penalizes them heavily but they can still be
  picked when nothing else matches.

## License

MIT — see [LICENSE](LICENSE).

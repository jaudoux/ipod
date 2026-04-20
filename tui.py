"""Shared TUI primitives for the podcast-to-Yoto app.

Business logic lives in ipod.py / yoto_api.py / icon_factory.py; those modules
call into this one for every prompt, menu, table, and progress bar so the
theme stays consistent.

Prompts are built on `questionary` (arrow-key / space / enter navigation).
Styled output uses `rich` (panels, rules, tables, progress).

All `.ask()` calls return `None` on Ctrl+C / Esc — callers treat that as
"go back one level", matching the app's old M/B semantics.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from typing import Any, Callable, Iterable

import questionary
from questionary import Choice, Separator
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


CONSOLE = Console()


# ---------------------------------------------------------------- theming

_STYLE = questionary.Style(
    [
        ("qmark", "fg:#ffcc00 bold"),
        ("question", "bold"),
        ("pointer", "fg:#00d0ff bold"),
        ("highlighted", "fg:#00d0ff bold"),
        ("selected", "fg:#22dd88"),
        ("separator", "fg:#666666"),
        ("instruction", "fg:#888888 italic"),
        ("answer", "fg:#22dd88"),
        ("text", ""),
    ]
)


# ---------------------------------------------------------------- prompts

def select(message: str, choices: list[Any], default: Any | None = None) -> Any | None:
    """Arrow + Enter single-pick. Returns choice value or None on abort."""
    return questionary.select(
        message, choices=choices, default=default, style=_STYLE, qmark="?"
    ).ask()


def checkbox(
    message: str,
    choices: list[Any],
    instruction: str | None = "(space: toggle   a: all   i: invert   enter: confirm)",
) -> list[Any] | None:
    """Arrows + Space toggle + Enter confirm. Returns list or None on abort."""
    return questionary.checkbox(
        message,
        choices=choices,
        instruction=instruction,
        style=_STYLE,
        qmark="?",
    ).ask()


def confirm(message: str, default: bool = False) -> bool | None:
    """y/n (with arrow support). Returns bool or None on abort."""
    return questionary.confirm(message, default=default, style=_STYLE, qmark="?").ask()


def text(
    message: str,
    default: str = "",
    validate: Callable[[str], bool | str] | None = None,
) -> str | None:
    """Single-line text input. `validate` returns True or an error string."""
    return questionary.text(
        message, default=default, validate=validate, style=_STYLE, qmark="?"
    ).ask()


def path(message: str) -> str | None:
    """File path input with tab-completion."""
    return questionary.path(message, style=_STYLE, qmark="?").ask()


def pause(message: str = "Press Enter to continue…") -> None:
    """Block until user hits Enter. Quiet: no error on Ctrl+C."""
    try:
        questionary.press_any_key_to_continue(message, style=_STYLE).ask()
    except Exception:
        pass


# ---------------------------------------------------------------- display

def banner() -> None:
    """Title card. Replaces the old ASCII iPod logo."""
    term_width = shutil.get_terminal_size().columns
    title = Text()
    title.append("  iPOD  ", style="bold black on yellow")
    title.append("  ", style="")
    title.append("Podcast → Yoto", style="bold cyan")
    subtitle = Text("Download · Pixel-icon · Sync", style="dim cyan")
    body = Align.center(
        Text.assemble(title, "\n", subtitle),
        vertical="middle",
    )
    CONSOLE.print(
        Panel(body, border_style="yellow", padding=(1, 2), width=min(term_width, 70))
    )


def rule(title: str = "") -> None:
    CONSOLE.print(Rule(title, style="cyan"))


def panel(title: str, body: str, style: str = "cyan") -> None:
    CONSOLE.print(Panel(body, title=title, border_style=style, padding=(1, 2)))


_STATUS_GLYPH = {
    "ok": ("✓", "green"),
    "warn": ("⚠", "yellow"),
    "err": ("✗", "red"),
    "info": ("➜", "cyan"),
}


def status(kind: str, message: str) -> None:
    """Colored one-liner. kind ∈ ok/warn/err/info."""
    glyph, color = _STATUS_GLYPH.get(kind, ("•", "white"))
    CONSOLE.print(f"[{color}]{glyph}[/] {message}")


@contextmanager
def progress(description: str = "Working…"):
    """Context-manager rich.Progress with a spinner + bar + ETA."""
    prog = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[cyan]{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=CONSOLE,
        transient=False,
    )
    with prog:
        yield prog


# ---------------------------------------------------------------- domain helpers

def _truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[: max(0, width - 1)] + "…"


def episode_choice(
    title: str,
    *,
    synced: bool,
    has_local: bool,
    card_linked: bool,
    value: Any = None,
) -> Choice:
    """Build a questionary.Choice styled with a status dot + label.

    - ● green   → already synced on Yoto
    - ◌ yellow  → downloaded locally but not synced
    - ● dim     → downloaded locally (no Yoto card linked)
    - ○ dim     → not downloaded yet
    """
    if synced:
        dot, badge = ("● ", "[green]Synced[/]")
    elif has_local and card_linked:
        dot, badge = ("◌ ", "[yellow]Downloaded, not synced[/]")
    elif has_local:
        dot, badge = ("● ", "[dim]Downloaded[/]")
    else:
        dot, badge = ("○ ", "")

    # questionary renders Choice.title as plain text or as a list of style
    # tuples. We use the list form so colors render correctly.
    term_width = shutil.get_terminal_size().columns
    max_title = max(20, term_width - 30)

    parts: list[tuple[str, str]] = []
    parts.append(("fg:#00d0ff" if synced else "fg:#888888", dot))
    parts.append(("", _truncate(title, max_title)))
    if synced:
        parts.append(("fg:#22dd88 italic", "   · synced"))
    elif has_local and card_linked:
        parts.append(("fg:#ffcc00 italic", "   · downloaded"))
    elif has_local:
        parts.append(("fg:#888888 italic", "   · downloaded"))
    return Choice(title=parts, value=value if value is not None else title)


def playlist_table(playlists: Iterable[dict]) -> None:
    """Render a Yoto playlist list as a rich.Table (used by yoto_menu opt 4)."""
    table = Table(title="Yoto playlists", title_style="bold cyan", border_style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Chapters", justify="right")
    for pl in playlists:
        chapters = (pl.get("content") or {}).get("chapters") or []
        n = str(len(chapters)) if chapters else "—"
        table.add_row(pl.get("title") or "?", pl.get("id") or "?", n)
    CONSOLE.print(table)


def auth_device_panel(
    user_code: str,
    verification_uri: str,
    verification_uri_complete: str | None = None,
    expires_in: int | None = None,
) -> None:
    """Big centered device-flow code. Replaces authenticate_yoto's loose prints."""
    lines = Text()
    lines.append("Open this page in your browser:\n", style="")
    lines.append(f"  {verification_uri}\n", style="bold cyan")
    if verification_uri_complete and verification_uri_complete != verification_uri:
        lines.append(f"  (or direct: {verification_uri_complete})\n", style="dim")
    lines.append("\nThen enter this code:\n\n", style="")
    lines.append(f"    {user_code}\n", style="bold yellow on black")
    if expires_in:
        mins = expires_in // 60
        lines.append(f"\nCode expires in about {mins} minute(s).", style="dim")

    CONSOLE.print(
        Panel(
            lines,
            title="Yoto authentication",
            border_style="yellow",
            padding=(1, 2),
        )
    )


__all__ = [
    "CONSOLE",
    "Choice",
    "Separator",
    "select",
    "checkbox",
    "confirm",
    "text",
    "path",
    "pause",
    "banner",
    "rule",
    "panel",
    "status",
    "progress",
    "episode_choice",
    "playlist_table",
    "auth_device_panel",
]

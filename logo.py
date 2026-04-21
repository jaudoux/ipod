from rich.console import Console
from rich.text import Text

ORANGE = "#F04E23"
DARK = "#2B2B2B"
GREY = "grey50"
WHITE = "white"
DIM = "grey39"


def render_logo(console: Console | None = None) -> None:
    c = console or Console()

    o = f"[{ORANGE}]"
    w = f"[{WHITE}]"
    g = f"[{GREY}]"
    d = f"[{DARK} on {ORANGE}]"

    lines = [
        f"       {w}╭─┬────┬─╮",
        f"       {w}│ │ {o}♥★{w} │ │",
        f"    {w}╭──┴─┴────┴─┴──╮",
        f"    {w}│    {o}●{w}      {o}●{w}   │       {o}▪ iPod ▪",
        f"    {w}│               │       {g}─────────",
        f"    {w}│  {g}▦▦▦{w}   ┌───┐  │       {g}podcast {o}→{g} yoto",
        f"    {w}│  {g}▦▦▦{w}   │{d} ◡ [/]{w}│  │",
        f"    {w}│  {g}▦▦▦{w}   └───┘  │",
        f"    {w}╰───────────────╯",
    ]
    for line in lines:
        c.print(Text.from_markup(line))


if __name__ == "__main__":
    render_logo()

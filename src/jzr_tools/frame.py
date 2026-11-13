"""The bordered frame that the host reports are printed in.

A report is a list of titled sections, each a list of text lines. This module
draws them into one frame and knows how to fit that frame to the output it is
going to: colour or not, box-drawing characters or plain ones, and how wide.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import astuple, dataclass
import os
import shutil
import sys

import click

from jzr_tools.core.display import cut, display_width, pad

BAR_WIDTH = 20
GAP = "  "
_MINIMUM_WIDTH = 20


@dataclass(frozen=True)
class Glyphs:
    """The characters a report is drawn with."""

    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    divider_left: str
    divider_right: str
    horizontal: str
    vertical: str
    bar_open: str
    bar_full: str
    bar_empty: str
    bar_close: str
    cut: str


UNICODE = Glyphs("╭", "╮", "╰", "╯", "├", "┤", "─", "│", "", "█", "░", "", "…")
ASCII = Glyphs("+", "+", "+", "+", "+", "+", "-", "|", "[", "#", ".", "]", ">")


class Painter:
    """Formats the coloured parts of a report, or plain text without colour."""

    def __init__(self, color: bool, glyphs: Glyphs) -> None:
        self.color = color
        self.glyphs = glyphs

    def _paint(self, text: str, percent: float | None = None, **style: object) -> str:
        if not self.color:
            return text
        if percent is not None:
            style["fg"] = "green" if percent < 70 else "yellow" if percent < 90 else "red"
        return click.style(text, **style)

    def strong(self, text: str) -> str:
        return self._paint(text, bold=True)

    def dim(self, text: str) -> str:
        return self._paint(text, dim=True)

    def percent(self, percent: float) -> str:
        return self._paint(f"{percent:3.0f}%", percent)

    def usage(self, percent: float) -> str:
        """A bar and the figure it stands for: ``█████░░░░░░░░░░░░░░░   25%``."""
        glyphs = self.glyphs
        filled = min(BAR_WIDTH, max(0, round(percent * BAR_WIDTH / 100)))
        bar = self._paint(glyphs.bar_full * filled, percent) + self.dim(
            glyphs.bar_empty * (BAR_WIDTH - filled)
        )
        return f"{glyphs.bar_open}{bar}{glyphs.bar_close}{GAP}{self.percent(percent)}"


def draw_frame(
    sections: Sequence[tuple[str, Sequence[str]]], paint: Painter, width: int | None = None
) -> str:
    """Draw titled sections into one frame, as wide as its widest line needs.

    The first title goes into the top border and the others into dividers.
    ``width`` is the most columns a line may take; lines that need more are
    cut, and the frame is never narrower than 20 columns.
    """
    glyphs = paint.glyphs
    inner = max(display_width(item) for title, rows in sections for item in (title, *rows))
    if width is not None:
        inner = min(inner, max(width, _MINIMUM_WIDTH) - 6)

    side = paint.dim(glyphs.vertical)
    out: list[str] = []
    for index, (title, rows) in enumerate(sections):
        title = cut(title, inner, glyphs.cut)
        left = glyphs.top_left if index == 0 else glyphs.divider_left
        right = glyphs.top_right if index == 0 else glyphs.divider_right
        rule = glyphs.horizontal * (inner + 1 - display_width(title))
        out.append(
            paint.dim(f"{left}{glyphs.horizontal} ")
            + paint.strong(title)
            + paint.dim(f" {rule}{right}")
        )
        out += [f"{side}  {pad(cut(row, inner, glyphs.cut), inner)}  {side}" for row in rows]
    out.append(
        paint.dim(f"{glyphs.bottom_left}{glyphs.horizontal * (inner + 4)}{glyphs.bottom_right}")
    )
    return "\n".join(out) + "\n"


def _output_encoding() -> str:
    return getattr(sys.stdout, "encoding", None) or "ascii"


def _can_write(text: str) -> bool:
    try:
        text.encode(_output_encoding())
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def writable(text: str) -> str:
    """Replace what the output encoding cannot represent, so printing never fails.

    Each replacement takes the columns of what it replaces, so the frame
    stays straight.
    """
    if _can_write(text):
        return text

    def stand_in(char: str) -> str:
        if char == "°":
            return " "
        return "?" * display_width(char)

    return "".join(char if _can_write(char) else stand_in(char) for char in text)


def use_color() -> bool:
    """Colour only for a terminal, and never when ``NO_COLOR`` is set."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def needs_ascii() -> bool:
    """Return whether the output cannot show the box-drawing characters."""
    return not _can_write("".join(astuple(UNICODE)))


def terminal_size() -> os.terminal_size | None:
    """Return the terminal's size when output goes to one, else None."""
    return shutil.get_terminal_size() if sys.stdout.isatty() else None

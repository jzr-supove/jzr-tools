"""Text helpers shared by the host commands: sizes and column widths."""

from __future__ import annotations

import re
import unicodedata

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_COLOR_CODE = re.compile(r"(\x1b\[[0-9;]*m)")
_COLOR_RESET = "\x1b[0m"


def _unit_for(size: float) -> int:
    """Return the index of the largest unit that keeps ``size`` at 1 or more."""
    index = 0
    while size >= 1024 and index < len(_UNITS) - 1:
        size /= 1024
        index += 1
    return index


def _scaled(size: float, unit: int) -> str:
    return f"{size / 1024**unit:.0f}" if unit == 0 else f"{size / 1024**unit:.1f}"


def human_size(size: float) -> str:
    """Write a byte count in binary units with one decimal: ``22.5 GiB``."""
    unit = _unit_for(size)
    return f"{_scaled(size, unit)} {_UNITS[unit]}"


def size_pair(used: float, total: float) -> str:
    """Write used and total in the total's unit: ``22.5 / 31.1 GiB``."""
    unit = _unit_for(total)
    return f"{_scaled(used, unit)} / {_scaled(total, unit)} {_UNITS[unit]}"


def display_width(text: str) -> int:
    """Return the terminal columns ``text`` takes, not counting colour codes."""
    text = _COLOR_CODE.sub("", text)
    if text.isascii():
        return len(text)
    return sum(
        2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in text
        if not unicodedata.combining(char)
    )


def pad(text: str, width: int, *, right: bool = False) -> str:
    """Pad ``text`` with spaces to ``width`` columns; ``right`` aligns it right."""
    space = " " * max(0, width - display_width(text))
    return space + text if right else text + space


def cut(text: str, width: int, marker: str) -> str:
    """Shorten ``text`` to ``width`` columns, ending it with ``marker``."""
    if display_width(text) <= width:
        return text
    room = width - len(marker)
    kept: list[str] = []
    coloured = False
    for index, piece in enumerate(_COLOR_CODE.split(text)):
        if index % 2:
            # A colour code: it takes no room, and must be closed after a cut.
            kept.append(piece)
            coloured = True
            continue
        for char in piece:
            room -= display_width(char)
            if room < 0:
                break
            kept.append(char)
        if room < 0:
            break
    return "".join(kept) + (_COLOR_RESET if coloured else "") + marker

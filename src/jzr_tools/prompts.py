"""Interactive prompts: a checkbox list and a yes/no question.

``questionary`` is imported inside the functions, so commands that never ask
anything do not pay for loading it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import sys

# Colours by the prompt_toolkit class questionary gives each part. A ticked
# row is green with a check mark; the default grey band behind it is gone.
_STYLE = {
    "qmark": "fg:ansicyan bold",
    "question": "bold",
    "instruction": "fg:ansibrightblack",
    "pointer": "fg:ansicyan bold",
    "highlighted": "fg:ansicyan bold noreverse",
    "selected": "fg:ansigreen noreverse",
    "separator": "fg:ansibrightblack bold",
    "disabled": "fg:ansibrightblack italic",
    "answer": "fg:ansicyan",
    "text": "",
}


@dataclass(frozen=True)
class _Glyphs:
    ticked: str
    unticked: str
    pointer: str
    keys: str


_UNICODE = _Glyphs("✔", "○", "›", "↑↓ move · space tick · a all · i invert · enter done")
_ASCII = _Glyphs("x", "-", ">", "up/down move, space tick, a all, i invert, enter done")


def _glyphs() -> _Glyphs:
    """Pick check marks the output can show; plain letters otherwise."""
    sample = "".join((_UNICODE.ticked, _UNICODE.unticked, _UNICODE.pointer, _UNICODE.keys))
    try:
        sample.encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return _ASCII
    return _UNICODE


def _style() -> object:
    from prompt_toolkit.styles import Style

    return Style.from_dict(_STYLE)


@contextmanager
def _indicators(glyphs: _Glyphs) -> Iterator[None]:
    """Draw ticked and unticked rows with ``glyphs`` while a list is open.

    questionary takes its markers from module constants and offers no option
    for them, so they are swapped for the duration and put back after.
    """
    try:
        from questionary.prompts import common
    except ImportError:
        yield
        return
    saved = common.INDICATOR_SELECTED, common.INDICATOR_UNSELECTED
    common.INDICATOR_SELECTED, common.INDICATOR_UNSELECTED = glyphs.ticked, glyphs.unticked
    try:
        yield
    finally:
        common.INDICATOR_SELECTED, common.INDICATOR_UNSELECTED = saved


@dataclass(frozen=True)
class Choice:
    """One entry of a checkbox list."""

    value: object
    label: str
    checked: bool = False
    disabled: str = ""  # the reason; a disabled entry cannot be ticked
    description: str = ""  # shown while the entry is highlighted


@dataclass(frozen=True)
class Heading:
    """A line of text between entries; it cannot be ticked."""

    text: str


def choose(message: str, entries: Sequence[Choice | Heading]) -> list[object] | None:
    """Show a checkbox list and return the ticked values, or None if cancelled."""
    import questionary

    choices = []
    for entry in entries:
        if isinstance(entry, Heading):
            choices.append(questionary.Separator(entry.text))
            continue
        options: dict[str, object] = {"value": entry.value, "checked": entry.checked}
        if entry.disabled:
            options["disabled"] = entry.disabled
        if entry.description:
            options["description"] = entry.description
        choices.append(questionary.Choice(entry.label, **options))
    glyphs = _glyphs()
    question = questionary.checkbox(
        message,
        choices=choices,
        pointer=glyphs.pointer,
        instruction=f"  {glyphs.keys}",
        style=_style(),
    )
    with _indicators(glyphs):
        return question.ask()


def confirm(message: str, *, default: bool = False) -> bool:
    """Ask a yes/no question; cancelling it counts as no."""
    import questionary

    return bool(questionary.confirm(message, default=default, style=_style()).ask())

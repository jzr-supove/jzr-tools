"""Interactive prompts: a checkbox list and a yes/no question.

``questionary`` is imported inside the functions, so commands that never ask
anything do not pay for loading it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


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
    return questionary.checkbox(message, choices=choices).ask()


def confirm(message: str, *, default: bool = False) -> bool:
    """Ask a yes/no question; cancelling it counts as no."""
    import questionary

    return bool(questionary.confirm(message, default=default).ask())

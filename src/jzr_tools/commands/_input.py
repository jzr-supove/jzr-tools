"""Reading a command's text input: an argument, or stdin."""

from __future__ import annotations

import click


def read_text(value: str | None) -> str:
    """Return ``value``, or stdin when it is missing or ``-``.

    On a terminal, say how to finish pasting first, so the wait is not a
    mystery.
    """
    if value is not None and value != "-":
        return value
    stream = click.get_text_stream("stdin")
    try:
        terminal = stream.isatty()
    except (AttributeError, ValueError):
        terminal = False
    if terminal:
        click.echo(
            "Paste text, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )
    return stream.read()


def stdout_is_terminal() -> bool:
    stream = click.get_text_stream("stdout")
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False

"""Text-related CLI commands."""

from datetime import date
import sys

import click

from jzr_tools.core.system import copy_to_clipboard
from jzr_tools.core.text import kebab_case, slugify


@click.group()
def app() -> None:
    """Text utilities."""


def _fail_empty() -> None:
    click.echo("Nothing to convert: the text has no letters or digits.", err=True)
    raise click.exceptions.Exit(1)


@app.command()
@click.argument("text")
@click.option(
    "--separator",
    "-s",
    default="-",
    show_default=True,
    help="Separator to use in the slug.",
)
@click.option(
    "--preserve-case",
    is_flag=True,
    help="Keep the original letter casing instead of lowercasing.",
)
def slug(text: str, separator: str, preserve_case: bool) -> None:
    """Convert text into a URL-safe slug."""
    result = slugify(text, separator=separator, lowercase=not preserve_case)
    if not result:
        _fail_empty()

    click.echo(result)


def _read_text(text: str | None) -> str:
    if text is not None:
        return text

    if sys.stdin.isatty():
        click.echo(
            "Paste text, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )

    return sys.stdin.read()


@app.command()
@click.argument("text", required=False)
@click.option(
    "--date",
    "--include-date",
    "include_date",
    is_flag=True,
    help="Prefix the result with today's ISO date and an underscore.",
)
@click.option(
    "--copy",
    "-c",
    is_flag=True,
    help="Copy the result to the system clipboard.",
)
def kebab(text: str | None, include_date: bool, copy: bool) -> None:
    """Convert text into lowercase kebab-case.

    Omit TEXT to read pasted or piped stdin.
    """
    source_text = _read_text(text)
    if not kebab_case(source_text):
        # Checked before the date is added, so the output is never a bare prefix.
        _fail_empty()

    date_prefix = date.today() if include_date else None
    result = kebab_case(source_text, date_prefix=date_prefix)

    if copy:
        try:
            copy_to_clipboard(result)
        except RuntimeError as exc:
            click.echo(str(exc), err=True)
            raise click.exceptions.Exit(1) from exc

    click.echo(result)

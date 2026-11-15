"""Text-related CLI commands."""

from datetime import date
import sys

import click

from jzr_tools.core.system import copy_to_clipboard
from jzr_tools.core.text import slugify


@click.group()
def app() -> None:
    """Text utilities."""


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
    "--separator",
    "-s",
    default="-",
    show_default=True,
    help="Separator to put between words.",
)
@click.option(
    "--preserve-case",
    is_flag=True,
    help="Keep the original letter casing instead of lowercasing.",
)
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
def slug(
    text: str | None, separator: str, preserve_case: bool, include_date: bool, copy: bool
) -> None:
    """Convert text into a URL-safe slug, lowercase kebab-case by default.

    camelCase and PascalCase words are split (myVarName becomes my-var-name).
    Omit TEXT to read pasted or piped stdin.
    """
    result = slugify(_read_text(text), separator=separator, lowercase=not preserve_case)
    if not result:
        # Checked before the date is added, so the output is never a bare prefix.
        click.echo("Nothing to convert: the text has no letters or digits.", err=True)
        raise click.exceptions.Exit(1)

    if include_date:
        result = f"{date.today().isoformat()}_{result}"

    if copy:
        try:
            copy_to_clipboard(result)
        except RuntimeError as exc:
            click.echo(str(exc), err=True)
            raise click.exceptions.Exit(1) from exc

    click.echo(result)


# The old name of the same command, kept working but not listed.
app.add_command(click.Command(
    "kebab",
    callback=slug.callback,
    params=slug.params,
    help=slug.help,
    hidden=True,
))

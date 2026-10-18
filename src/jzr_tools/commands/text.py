"""Text-related CLI commands."""

from datetime import date
import sys
from typing import Annotated

import typer

from jzr_tools.core.system import copy_to_clipboard
from jzr_tools.core.text import kebab_case, slugify

app = typer.Typer(help="Text utilities.")


@app.command()
def slug(
    text: str,
    separator: Annotated[
        str,
        typer.Option("--separator", "-s", help="Separator to use in the slug."),
    ] = "-",
    preserve_case: Annotated[
        bool,
        typer.Option(
            "--preserve-case",
            help="Keep the original letter casing instead of lowercasing.",
        ),
    ] = False,
) -> None:
    """Convert text into a URL-safe slug."""
    typer.echo(slugify(text, separator=separator, lowercase=not preserve_case))


def _read_text(text: str | None) -> str:
    if text is not None:
        return text

    if sys.stdin.isatty():
        typer.echo(
            "Paste text, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )

    return sys.stdin.read()


@app.command()
def kebab(
    text: Annotated[
        str | None,
        typer.Argument(help="Text to convert. Omit to read pasted or piped stdin."),
    ] = None,
    include_date: Annotated[
        bool,
        typer.Option(
            "--date",
            "--include-date",
            help="Prefix the result with today's ISO date and an underscore.",
        ),
    ] = False,
    copy: Annotated[
        bool,
        typer.Option(
            "--copy",
            "-c",
            help="Copy the result to the system clipboard.",
        ),
    ] = False,
) -> None:
    """Convert text into lowercase kebab-case."""
    source_text = _read_text(text)
    date_prefix = date.today() if include_date else None
    result = kebab_case(source_text, date_prefix=date_prefix)

    if copy:
        try:
            copy_to_clipboard(result)
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(result)

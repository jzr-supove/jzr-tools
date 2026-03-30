"""Text-related CLI commands."""

from typing import Annotated

import typer

from jzr_tools.core.text import slugify

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

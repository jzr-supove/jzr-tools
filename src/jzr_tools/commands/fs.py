"""Filesystem-related CLI commands."""

from pathlib import Path
from typing import Annotated

import typer

from jzr_tools.core.filesystem import list_entries

app = typer.Typer(help="Filesystem utilities.")


@app.command("ls")
def list_path(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Directory to inspect.",
        ),
    ] = Path("."),
    hidden: Annotated[
        bool,
        typer.Option("--hidden", "-a", help="Include dot-prefixed entries."),
    ] = False,
) -> None:
    """List entries in a directory."""
    for entry in list_entries(path, include_hidden=hidden):
        typer.echo(entry)

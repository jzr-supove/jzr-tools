"""Root CLI application for jzr_tools."""

from pathlib import Path
from typing import Annotated

import typer

from jzr_tools import __version__
from jzr_tools.commands.convert import app as convert_app
from jzr_tools.core.image import convert_png_to_jpg
from jzr_tools.commands.network import killport
from jzr_tools.commands.fs import app as fs_app
from jzr_tools.commands.system import app as system_app
from jzr_tools.commands.text import app as text_app

app = typer.Typer(
    help="Personal cross-platform CLI toolkit.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Register shared CLI options."""


@app.command()
def version() -> None:
    """Print the toolkit version."""
    typer.echo(__version__)


@app.command()
def png2jpg(
    source: Annotated[
        Path,
        typer.Argument(help="PNG basename or .png file path to convert."),
    ],
    quality: Annotated[
        int,
        typer.Option(
            "--quality",
            "-q",
            min=1,
            max=100,
            help="JPEG quality passed to ImageMagick.",
        ),
    ] = 92,
) -> None:
    """Convert a PNG into a JPG without repeating the basename."""
    try:
        output_path = convert_png_to_jpg(source, quality=quality)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(output_path)


app.command()(killport)
app.add_typer(text_app, name="text")
app.add_typer(fs_app, name="fs")
app.add_typer(system_app, name="system")
app.add_typer(convert_app, name="convert")


def main() -> None:
    """Console script entrypoint."""
    app()

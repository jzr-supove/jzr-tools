"""Image conversion CLI commands.

Two shapes are supported:

* ``jzr convert png2jpg photo.png -q 90`` -- a ``<from>2<to>`` subcommand that
  is resolved on demand, so every supported format pair works without being
  registered up front.
* ``jzr convert --ext jpg --pattern "^IMG_"`` -- batch mode over a directory,
  converting whatever the regex matches.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shlex
from typing import Annotated, Sequence

import click
import typer
from typer.core import TyperGroup

from jzr_tools.core.images import (
    SUPPORTED_EXTENSIONS,
    BackendUnavailableError,
    ConversionResult,
    convert_file,
    detect_backends,
    equivalent_extensions,
    find_sources,
    normalize_extension,
    quality_caveat,
    resolve_backend,
    target_for,
)

_PAIR_PATTERN = re.compile(r"^([a-z0-9]+)2([a-z0-9]+)$")

_BACKEND_CHOICE = click.Choice(["auto", "magick", "ffmpeg"])


class ConvertGroup(TyperGroup):
    """Typer group that resolves ``<from>2<to>`` command names on demand."""

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        command = super().get_command(ctx, name)
        if command is not None:
            return command

        match = _PAIR_PATTERN.match(name.lower())
        if match is None:
            return None

        source_extension, target_extension = match.group(1), match.group(2)
        if source_extension not in SUPPORTED_EXTENSIONS:
            return None
        if target_extension not in SUPPORTED_EXTENSIONS:
            return None

        return _build_pair_command(name.lower(), source_extension, target_extension)


app = typer.Typer(
    cls=ConvertGroup,
    help=(
        "Convert images using ImageMagick or ffmpeg.\n\n"
        "Use a format pair as the subcommand -- 'jzr convert png2jpg shot.png -q 90' "
        "-- or run batch mode with '--ext'. Run 'jzr convert formats' to see the "
        "supported extensions and which backends are installed."
    ),
    no_args_is_help=True,
)


def _report(result: ConversionResult) -> None:
    if result.status == "planned":
        typer.echo(shlex.join(result.command))
    elif result.status == "converted":
        typer.echo(f"{result.source} -> {result.target}")
    elif result.status == "skipped":
        typer.echo(f"skip {result.source}: {result.detail}", err=True)
    else:
        typer.echo(f"fail {result.source}: {result.detail}", err=True)


def _run(
    sources: Sequence[Path],
    target_extension: str,
    *,
    quality: int | None,
    backend: str,
    outdir: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Convert every source, reporting per file and summarizing at the end."""
    if not sources:
        typer.echo("No matching files.", err=True)
        raise typer.Exit(code=1)

    try:
        backend_name, executable = resolve_backend(backend)
    except BackendUnavailableError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    if quality is not None:
        caveat = quality_caveat(backend_name, target_extension)
        if caveat is not None:
            typer.echo(f"note: {caveat}", err=True)

    counts: Counter[str] = Counter()
    for source in sources:
        result = convert_file(
            source,
            target_for(source, target_extension, outdir=outdir),
            backend=backend_name,
            executable=executable,
            quality=quality,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        counts[result.status] += 1
        _report(result)

    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    typer.echo(f"{backend_name}: {summary}")

    if counts["failed"]:
        raise typer.Exit(code=1)


def _build_pair_command(name: str, source_extension: str, target_extension: str) -> click.Command:
    """Create the click command backing a ``<from>2<to>`` invocation."""

    @click.command(
        name=name,
        help=(
            f"Convert {source_extension.upper()} files to {target_extension.upper()}.\n\n"
            "Pass SOURCES explicitly, or omit them to convert every matching file "
            "in --dir."
        ),
    )
    @click.argument(
        "sources",
        nargs=-1,
        type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    )
    @click.option(
        "-q",
        "--quality",
        type=click.IntRange(1, 100),
        default=None,
        help="Output quality, 1-100 (higher is better).",
    )
    @click.option(
        "-d",
        "--dir",
        "directory",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        default=Path("."),
        show_default=".",
        help="Directory scanned when no SOURCES are given.",
    )
    @click.option(
        "-p",
        "--pattern",
        default=None,
        help="Regex filter applied to candidate filenames.",
    )
    @click.option(
        "-r",
        "--recursive",
        is_flag=True,
        help="Recurse into subdirectories while discovering files.",
    )
    @click.option(
        "-o",
        "--outdir",
        type=click.Path(file_okay=False, path_type=Path),
        default=None,
        help="Write outputs here instead of beside each source.",
    )
    @click.option(
        "-b",
        "--backend",
        type=_BACKEND_CHOICE,
        default="auto",
        show_default=True,
        help="Conversion backend to use.",
    )
    @click.option("--overwrite", is_flag=True, help="Replace existing output files.")
    @click.option("--dry-run", is_flag=True, help="Print commands without running them.")
    def command(
        sources: tuple[Path, ...],
        quality: int | None,
        directory: Path,
        pattern: str | None,
        recursive: bool,
        outdir: Path | None,
        backend: str,
        overwrite: bool,
        dry_run: bool,
    ) -> None:
        if sources:
            selected = list(sources)
            if pattern is not None:
                regex = re.compile(pattern)
                selected = [path for path in selected if regex.search(path.name) is not None]
        else:
            selected = find_sources(
                directory,
                extensions=equivalent_extensions(source_extension),
                pattern=pattern,
                recursive=recursive,
            )

        _run(
            selected,
            target_extension,
            quality=quality,
            backend=backend,
            outdir=outdir,
            overwrite=overwrite,
            dry_run=dry_run,
        )

    return command


@app.callback(invoke_without_command=True)
def convert(
    ctx: typer.Context,
    ext: Annotated[
        str | None,
        typer.Option("--ext", "-e", help="Target extension for batch mode, e.g. jpg."),
    ] = None,
    pattern: Annotated[
        str | None,
        typer.Option("--pattern", "-p", help="Regex filter applied to candidate filenames."),
    ] = None,
    directory: Annotated[
        Path,
        typer.Option(
            "--dir",
            "-d",
            exists=True,
            file_okay=False,
            help="Directory to scan.",
        ),
    ] = Path("."),
    recursive: Annotated[
        bool,
        typer.Option("--recursive", "-r", help="Recurse into subdirectories."),
    ] = False,
    quality: Annotated[
        int | None,
        typer.Option("--quality", "-q", min=1, max=100, help="Output quality, 1-100."),
    ] = None,
    outdir: Annotated[
        Path | None,
        typer.Option(
            "--outdir",
            "-o",
            file_okay=False,
            help="Write outputs here instead of beside each source.",
        ),
    ] = None,
    backend: Annotated[
        str,
        typer.Option("--backend", "-b", click_type=_BACKEND_CHOICE, help="Backend to use."),
    ] = "auto",
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace existing output files."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print commands without running them."),
    ] = False,
) -> None:
    """Convert images in bulk when no format-pair subcommand is used."""
    if ctx.invoked_subcommand is not None:
        return

    if ext is None:
        typer.echo(
            "Provide a format pair (e.g. 'jzr convert png2jpg shot.png') or --ext.",
            err=True,
        )
        raise typer.Exit(code=2)

    target_extension = normalize_extension(ext)
    if target_extension not in SUPPORTED_EXTENSIONS:
        typer.echo(
            f"Unsupported extension: {ext}. See 'jzr convert formats'.",
            err=True,
        )
        raise typer.Exit(code=2)

    skip = equivalent_extensions(target_extension)
    sources = [
        path
        for path in find_sources(
            directory,
            extensions=SUPPORTED_EXTENSIONS,
            pattern=pattern,
            recursive=recursive,
        )
        if normalize_extension(path.suffix) not in skip
    ]

    _run(
        sources,
        target_extension,
        quality=quality,
        backend=backend,
        outdir=outdir,
        overwrite=overwrite,
        dry_run=dry_run,
    )


@app.command()
def formats() -> None:
    """Show supported extensions and detected backends."""
    typer.echo("extensions: " + " ".join(sorted(SUPPORTED_EXTENSIONS)))

    available = detect_backends()
    for name in ("magick", "ffmpeg"):
        location = available.get(name)
        typer.echo(f"{name}: {location[0] if location else 'not found'}")

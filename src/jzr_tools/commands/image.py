"""Image CLI commands.

* ``jzr image convert [SOURCES]... --to jpg`` converts the listed files, or
  every image in a directory when none are listed.
* ``jzr image png2jpg ...`` is shorthand for ``convert --from png --to jpg``.
  Any ``<from>2<to>`` pair is resolved on demand, so every supported format
  pair works without being registered up front.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shlex
from typing import Callable, Sequence

import click

from jzr_tools.core.images import (
    SUPPORTED_EXTENSIONS,
    BackendUnavailableError,
    ConversionResult,
    convert_file,
    detect_backends,
    equivalent_extensions,
    find_sources,
    normalize_extension,
    plan_targets,
    quality_caveat,
    resolve_backend,
    unsupported_target,
)

_PAIR_PATTERN = re.compile(r"^([a-z0-9]+)2([a-z0-9]+)$")

_BACKEND_CHOICE = click.Choice(["auto", "magick", "ffmpeg"])


class ImageGroup(click.Group):
    """Click group that resolves ``<from>2<to>`` command names on demand."""

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


@click.group(cls=ImageGroup)
def app() -> None:
    """Convert images using ImageMagick or ffmpeg.

    Besides the commands listed below, any format pair works as a command:
    'jzr image png2jpg shot.png' is short for
    'jzr image convert shot.png --from png --to jpg'.
    """


def _conversion_options(function: Callable[..., None]) -> Callable[..., None]:
    """Attach the argument and options shared by ``convert`` and the pair form."""
    decorators = [
        click.argument("sources", nargs=-1, type=click.Path(path_type=Path)),
        click.option(
            "-d",
            "--dir",
            "directory",
            type=click.Path(exists=True, file_okay=False, path_type=Path),
            default=None,
            help="Directory to scan when no SOURCES are listed.  [default: .]",
        ),
        click.option(
            "-p",
            "--pattern",
            default=None,
            help="Regex filter applied to candidate filenames.",
        ),
        click.option(
            "-r",
            "--recursive",
            is_flag=True,
            help="Recurse into subdirectories while scanning.",
        ),
        click.option(
            "-o",
            "--outdir",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Write outputs here instead of beside each source.",
        ),
        click.option(
            "-q",
            "--quality",
            type=click.IntRange(1, 100),
            default=None,
            help="Output quality, 1-100 (higher is better).",
        ),
        click.option(
            "-b",
            "--backend",
            type=_BACKEND_CHOICE,
            default="auto",
            show_default=True,
            help="Conversion backend to use.",
        ),
        click.option("--overwrite", is_flag=True, help="Replace existing output files."),
        click.option("--dry-run", is_flag=True, help="Print commands without running them."),
    ]
    # Decorators apply bottom-up, so reverse to keep this order in --help.
    for decorator in reversed(decorators):
        function = decorator(function)
    return function


def _report(result: ConversionResult) -> None:
    if result.status == "planned":
        click.echo(shlex.join(result.command))
        if result.detail:
            click.echo(f"note: {result.detail}", err=True)
    elif result.status == "converted":
        click.echo(f"{result.source} -> {result.target}")
    elif result.status == "skipped":
        click.echo(f"skip {result.source}: {result.detail}", err=True)
    else:
        click.echo(f"fail {result.source}: {result.detail}", err=True)


def _compile_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """Compile ``--pattern``, turning a bad regex into a usage error."""
    if pattern is None:
        return None
    try:
        return re.compile(pattern)
    except re.error as error:
        raise click.BadParameter(
            f"{pattern!r} is not a valid regular expression ({error}).",
            param_hint="'--pattern'",
        ) from error


def _supported_extension(value: str) -> str:
    """Normalize a format option, rejecting formats no backend handles."""
    extension = normalize_extension(value)
    if extension not in SUPPORTED_EXTENSIONS:
        raise click.UsageError(f"Unsupported extension: {value}. See 'jzr image formats'.")
    return extension


def _resolve_source(path: Path, source_extension: str | None) -> Path:
    """Return the file a listed source refers to.

    When the source format is known, the extension may be left off
    (``shot`` or ``shot.v2`` for ``shot.png`` / ``shot.v2.png``), and a file
    in another format is rejected instead of being converted anyway.
    """
    if source_extension is None:
        if path.is_file():
            return path
        raise click.UsageError(f"File not found: {path}")

    allowed = equivalent_extensions(source_extension)
    if path.is_file() and normalize_extension(path.suffix) in allowed:
        return path

    # Append rather than replace the suffix, so dotted names stay intact.
    for extension in (source_extension, *sorted(allowed - {source_extension})):
        candidate = path.with_name(f"{path.name}.{extension}")
        if candidate.is_file():
            return candidate

    if path.is_file():
        raise click.UsageError(f"{path} is not a .{source_extension} file.")
    raise click.UsageError(f"File not found: {path} (also tried {path}.{source_extension})")


def _run(
    sources: Sequence[Path],
    target_extension: str,
    *,
    quality: int | None,
    backend: str,
    outdir: Path | None,
    root: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Convert every source, reporting per file and summarizing at the end.

    ``root`` is the scanned directory when sources were discovered rather than
    listed, so ``--outdir`` can mirror its subdirectories.
    """
    if not sources:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    try:
        backend_name, executable = resolve_backend(backend)
    except BackendUnavailableError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error

    unsupported = unsupported_target(backend_name, target_extension)
    if unsupported is not None:
        click.echo(unsupported, err=True)
        raise click.exceptions.Exit(1)

    if quality is not None:
        caveat = quality_caveat(backend_name, target_extension)
        if caveat is not None:
            click.echo(f"note: {caveat}", err=True)

    targets = plan_targets(sources, target_extension, outdir=outdir, root=root)

    counts: Counter[str] = Counter()
    for source, target in zip(sources, targets):
        result = convert_file(
            source,
            target,
            backend=backend_name,
            executable=executable,
            quality=quality,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        counts[result.status] += 1
        _report(result)

    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    click.echo(f"{backend_name}: {summary}")

    if counts["failed"]:
        raise click.exceptions.Exit(1)


def _convert(
    sources: tuple[Path, ...],
    source_extension: str | None,
    target_extension: str,
    *,
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    outdir: Path | None,
    quality: int | None,
    backend: str,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Select the sources for ``convert`` or a pair command, then convert them."""
    regex = _compile_pattern(pattern)

    if sources:
        if directory is not None or recursive:
            raise click.UsageError(
                "--dir and --recursive only apply when no SOURCES are listed."
            )
        selected = [_resolve_source(path, source_extension) for path in sources]
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        if source_extension is None:
            # Everything convertible that is not already in the target format.
            extensions = SUPPORTED_EXTENSIONS - equivalent_extensions(target_extension)
        else:
            extensions = equivalent_extensions(source_extension)
        selected = find_sources(
            root, extensions=extensions, pattern=regex, recursive=recursive
        )

    _run(
        selected,
        target_extension,
        quality=quality,
        backend=backend,
        outdir=outdir,
        root=root,
        overwrite=overwrite,
        dry_run=dry_run,
    )


@app.command()
@click.option("-t", "--to", "target", required=True, help="Target format, e.g. jpg.")
@click.option(
    "-f",
    "--from",
    "source",
    default=None,
    help="Only convert this source format; lets SOURCES omit the extension.",
)
@_conversion_options
def convert(
    sources: tuple[Path, ...], target: str, source: str | None, **options: object
) -> None:
    """Convert images to another format.

    List SOURCES to convert those files, or leave them out to convert every
    image in --dir that is not already in the target format.
    """
    target_extension = _supported_extension(target)
    source_extension = _supported_extension(source) if source is not None else None
    _convert(sources, source_extension, target_extension, **options)


def _build_pair_command(name: str, source_extension: str, target_extension: str) -> click.Command:
    """Create the click command backing a ``<from>2<to>`` invocation."""

    @click.command(
        name=name,
        help=(
            f"Convert {source_extension.upper()} files to {target_extension.upper()}.\n\n"
            f"Short for 'jzr image convert --from {source_extension} "
            f"--to {target_extension}'. List SOURCES (the .{source_extension} "
            "extension may be left off), or leave them out to convert every "
            "matching file in --dir."
        ),
    )
    @_conversion_options
    def command(sources: tuple[Path, ...], **options: object) -> None:
        _convert(sources, source_extension, target_extension, **options)

    return command


@app.command()
def formats() -> None:
    """Show supported extensions and detected backends."""
    click.echo("extensions: " + " ".join(sorted(SUPPORTED_EXTENSIONS)))

    available = detect_backends()
    for name in ("magick", "ffmpeg"):
        location = available.get(name)
        line = f"{name}: {location[0] if location else 'not found'}"
        unwritable = sorted(
            extension
            for extension in SUPPORTED_EXTENSIONS
            if unsupported_target(name, extension) is not None
        )
        if unwritable:
            line += f" (cannot write: {' '.join(unwritable)})"
        click.echo(line)

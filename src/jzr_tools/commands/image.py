"""Image CLI commands.

* ``jzr image convert [SOURCES]... --to jpg`` converts the listed files, or
  every image in a directory when none are listed.
* ``jzr image png2jpg ...`` is shorthand for ``convert --from png --to jpg``.
  Any ``<from>2<to>`` pair is resolved on demand, so every supported format
  pair works without being registered up front.
* ``jzr image info FILES...`` shows the format, size and metadata of images.
* ``jzr image resize [SOURCES]... --width 1600`` writes resized copies.
* ``jzr image compress [SOURCES]...`` writes smaller copies.
* ``jzr image strip [SOURCES]...`` writes copies without metadata.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import TYPE_CHECKING, Sequence

import click

from jzr_tools.commands._batch import (
    batch_options,
    check_output,
    compile_pattern,
    report,
    summarize,
)
from jzr_tools.core.images import (
    BACKENDS,
    SUPPORTED_EXTENSIONS,
    BackendUnavailableError,
    background_caveat,
    convert_file,
    detect_backends,
    equivalent_extensions,
    find_sources,
    normalize_extension,
    parse_colour,
    plan_targets,
    quality_caveat,
    resolve_backend,
)

if TYPE_CHECKING:
    from jzr_tools.core.batch import ConversionResult

_PAIR_PATTERN = re.compile(r"^([a-z0-9]+)2([a-z0-9]+)$")

_BACKEND_CHOICE = click.Choice(["auto", *BACKENDS])


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
    """Convert, compress, resize and inspect images.

    Besides the commands listed below, any format pair works as a command:
    'jzr image png2jpg shot.png' is short for
    'jzr image convert shot.png --from png --to jpg'.
    """


def _background(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> tuple[int, int, int] | None:
    if value is None:
        return None
    try:
        return parse_colour(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


_conversion_options = batch_options(
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
        help="Conversion engine: built-in Pillow, or ImageMagick from PATH.",
    ),
    click.option(
        "--background",
        metavar="COLOR",
        default=None,
        callback=_background,
        help="Colour for transparent areas in jpg, bmp and ppm output (default white).",
    ),
)


def _supported_extension(value: str) -> str:
    """Normalize a format option, rejecting formats that are not supported."""
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
    background: tuple[int, int, int] | None,
    backend: str,
    output: Path | None,
    outdir: Path | None,
    root: Path | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Convert every source, reporting per file and summarizing at the end.

    ``root`` is the scanned directory when sources were discovered rather than
    listed, so ``--outdir`` can mirror its subdirectories. ``output`` is the
    exact file for a single source.
    """
    if not sources:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    try:
        backend_name, executable = resolve_backend(backend)
    except BackendUnavailableError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error

    if quality is not None:
        caveat = quality_caveat(backend_name, target_extension)
        if caveat is not None:
            click.echo(f"note: {caveat}", err=True)
    if background is not None:
        caveat = background_caveat(target_extension)
        if caveat is not None:
            click.echo(f"note: {caveat}", err=True)

    if output is not None:
        targets = [output]
    else:
        targets = plan_targets(sources, target_extension, outdir=outdir, root=root)

    counts: Counter[str] = Counter()
    for source, target in zip(sources, targets):
        result = convert_file(
            source,
            target,
            backend=backend_name,
            executable=executable,
            quality=quality,
            background=background,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        counts[result.status] += 1
        report(result)

    summarize(backend_name, counts)


def _convert(
    sources: tuple[Path, ...],
    source_extension: str | None,
    target_extension: str,
    *,
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    output: Path | None,
    outdir: Path | None,
    quality: int | None,
    background: tuple[int, int, int] | None,
    backend: str,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Select the sources for ``convert`` or a pair command, then convert them."""
    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)
    if output is not None and (
        normalize_extension(output.suffix) not in equivalent_extensions(target_extension)
    ):
        raise click.UsageError(f"--output {output} does not end in .{target_extension}.")

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
        background=background,
        backend=backend,
        output=output,
        outdir=outdir,
        root=root,
        overwrite=overwrite,
        dry_run=dry_run,
    )


@app.command()
@click.option(
    "-t",
    "--to",
    "target",
    default=None,
    help="Target format, e.g. jpg. Taken from --output when left out.",
)
@click.option(
    "-f",
    "--from",
    "source",
    default=None,
    help="Only convert this source format; lets SOURCES omit the extension.",
)
@_conversion_options
def convert(
    sources: tuple[Path, ...],
    target: str | None,
    source: str | None,
    output: Path | None,
    **options: object,
) -> None:
    """Convert images to another format.

    List SOURCES to convert those files, or leave them out to convert every
    image in --dir that is not already in the target format.
    """
    if target is not None:
        target_extension = _supported_extension(target)
    elif output is not None and output.suffix:
        target_extension = _supported_extension(output.suffix)
    else:
        raise click.UsageError("Missing option '-t' / '--to' (or name the file with -o).")
    source_extension = _supported_extension(source) if source is not None else None
    _convert(sources, source_extension, target_extension, output=output, **options)


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
    """Show supported extensions and the conversion engines available."""
    from importlib.metadata import version

    click.echo("extensions: " + " ".join(sorted(SUPPORTED_EXTENSIONS)))
    click.echo(f"pillow: {version('pillow')}, pillow-heif {version('pillow-heif')}")

    magick = detect_backends().get("magick")
    click.echo(f"magick: {magick[0] if magick else 'not found'}")


@app.command()
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--json", "as_json", is_flag=True, help="Print one JSON array.")
@click.option("--no-gps", is_flag=True, help="Leave the GPS position out.")
def info(files: tuple[Path, ...], as_json: bool, no_gps: bool) -> None:
    """Show the format, size, colour profile and metadata of images.

    GPS positions are shown so you can check a photo before sharing it;
    --no-gps leaves them out. --json prints one array with the same facts.
    """
    import json

    from jzr_tools.core import image_info
    from jzr_tools.core.pillow_engine import ConversionError

    documents: list[dict[str, object]] = []
    failed = False
    printed = False
    for path in files:
        try:
            facts = image_info.read_info(path)
        except (ConversionError, OSError) as error:
            reason = error.strerror if isinstance(error, OSError) and error.strerror else error
            click.echo(f"fail {path}: {reason}", err=True)
            failed = True
            continue

        if as_json:
            documents.append(image_info.info_as_dict(facts, gps=not no_gps))
            continue

        rows = image_info.info_rows(facts, gps=not no_gps)
        width = max(len(label) for label, _ in rows)
        if printed:
            click.echo()
        click.echo(str(path))
        for label, value in rows:
            click.echo(f"  {label.ljust(width)}  {value}")
        printed = True

    if as_json:
        click.echo(json.dumps(documents, indent=2, ensure_ascii=False))
    if failed:
        raise click.exceptions.Exit(1)


def _resize_notes(
    extensions: list[str],
    *,
    quality: int | None,
    background: tuple[int, int, int] | None,
) -> None:
    """Print each note once, for the first target extension it applies to."""
    checks = []
    if quality is not None:
        checks.append(lambda extension: quality_caveat("pillow", extension))
    if background is not None:
        checks.append(background_caveat)
    for check in checks:
        for extension in extensions:
            caveat = check(extension)
            if caveat is not None:
                click.echo(f"note: {caveat}", err=True)
                break


@app.command()
@batch_options(
    click.option(
        "--width",
        type=click.IntRange(min=1),
        metavar="N",
        default=None,
        help="Width in pixels; the height follows.",
    ),
    click.option(
        "--height",
        type=click.IntRange(min=1),
        metavar="N",
        default=None,
        help="Height in pixels; the width follows.",
    ),
    click.option(
        "--percent",
        type=click.FloatRange(min=0, max=1000, min_open=True),
        metavar="P",
        default=None,
        help="Scale both sides by this percentage.",
    ),
    click.option(
        "--max-side",
        type=click.IntRange(min=1),
        metavar="N",
        default=None,
        help="Longest side in pixels.",
    ),
    click.option("--upscale", is_flag=True, help="Allow making images larger."),
    click.option(
        "-t",
        "--to",
        "target",
        default=None,
        help="Write in this format instead of the source's.",
    ),
    click.option(
        "-q",
        "--quality",
        type=click.IntRange(1, 100),
        default=None,
        help="Output quality, 1-100 (higher is better).",
    ),
    click.option(
        "--strip",
        is_flag=True,
        help="Leave out EXIF, XMP and comments; the colour profile is kept.",
    ),
    click.option(
        "--background",
        metavar="COLOR",
        default=None,
        callback=_background,
        help="Colour for transparent areas in jpg, bmp and ppm output (default white).",
    ),
)
def resize(
    sources: tuple[Path, ...],
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    output: Path | None,
    outdir: Path | None,
    width: int | None,
    height: int | None,
    percent: float | None,
    max_side: int | None,
    upscale: bool,
    target: str | None,
    quality: int | None,
    strip: bool,
    background: tuple[int, int, int] | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Make images smaller (or, with --upscale, larger).

    Give one size: --width, --height, both (the image fits inside that box),
    --percent or --max-side. Proportions are always kept. Each file is
    written as <name>_<W>x<H>.<ext> beside its source unless -o or --outdir
    says otherwise.
    """
    from jzr_tools.core import resize as resizer
    from jzr_tools.core.batch import ConversionResult

    request = resizer.SizeRequest(
        width=width, height=height, percent=percent, max_side=max_side, upscale=upscale
    )
    try:
        resizer.check_request(request)
    except ValueError as error:
        raise click.UsageError(f"{error}.") from error

    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)
    target_extension = _supported_extension(target) if target is not None else None
    if output is not None:
        written = normalize_extension(output.suffix)
        if target_extension is not None:
            if written not in equivalent_extensions(target_extension):
                raise click.UsageError(
                    f"--output {output} does not end in .{target_extension}."
                )
        elif not written:
            raise click.UsageError(f"--output {output} has no extension; add one or give --to.")
        else:
            target_extension = _supported_extension(written)

    if sources:
        if directory is not None or recursive:
            raise click.UsageError(
                "--dir and --recursive only apply when no SOURCES are listed."
            )
        selected = [_resolve_source(path, None) for path in sources]
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        found = find_sources(
            root, extensions=SUPPORTED_EXTENSIONS, pattern=regex, recursive=recursive
        )
        # Earlier results are not resized again.
        selected = [path for path in found if resizer.RESIZED_STEM.search(path.stem) is None]

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    def extension_of(source: Path) -> str:
        if target_extension is not None:
            return target_extension
        return source.suffix.lstrip(".")

    _resize_notes(
        [normalize_extension(extension_of(source)) for source in selected],
        quality=quality,
        background=background,
    )

    from jzr_tools.core import pillow_engine

    # Each source becomes a plan, or the result that ends it here.
    outcomes: list[ConversionResult | resizer.SizePlan] = []
    for source in selected:
        if normalize_extension(extension_of(source)) not in SUPPORTED_EXTENSIONS:
            reason = f".{source.suffix.lstrip('.')} is not an image extension; add --to"
            outcomes.append(ConversionResult(source, source, (), "failed", reason))
            continue
        try:
            source_width, source_height, frames = pillow_engine.probe(source)
        except pillow_engine.ConversionError as error:
            outcomes.append(ConversionResult(source, source, (), "failed", str(error)))
            continue
        if frames > 1:
            reason = pillow_engine.NOT_RESIZED_ANIMATED
            outcomes.append(ConversionResult(source, source, (), "failed", reason))
            continue
        planned = resizer.plan_size(source_width, source_height, request)
        if isinstance(planned, str):
            outcomes.append(ConversionResult(source, source, (), "skipped", planned))
        else:
            outcomes.append(planned)

    items = [
        (source, outcome)
        for source, outcome in zip(selected, outcomes)
        if isinstance(outcome, resizer.SizePlan)
    ]
    if output is not None:
        targets = iter([output] * len(items))
    else:
        targets = iter(
            resizer.resize_targets(items, extension=target_extension, outdir=outdir, root=root)
        )

    counts: Counter[str] = Counter()
    for source, outcome in zip(selected, outcomes):
        if isinstance(outcome, resizer.SizePlan):
            result = resizer.resize_file(
                source,
                next(targets),
                outcome,
                quality=quality,
                background=background,
                strip=strip,
                overwrite=overwrite,
                dry_run=dry_run,
            )
        else:
            result = outcome
        counts[result.status] += 1
        report(result)

    summarize("pillow", counts)


def _size_limit(ctx: click.Context, param: click.Parameter, value: str | None) -> int | None:
    if value is None:
        return None
    from jzr_tools.core.media import parse_size

    try:
        return parse_size(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


@app.command()
@batch_options(
    click.option(
        "-t",
        "--to",
        "target",
        type=click.Choice(["jpg", "png", "webp", "avif", "heic"], case_sensitive=False),
        default=None,
        help="Re-encode into this format.",
    ),
    click.option(
        "-q",
        "--quality",
        type=click.IntRange(1, 100),
        default=None,
        help="Output quality, 1-100 (higher is better).",
    ),
    click.option(
        "-s",
        "--size",
        "size_limit",
        metavar="SIZE",
        default=None,
        callback=_size_limit,
        help=(
            "Largest file size, such as 500KB or 2MB; quality is lowered "
            "(and the image shrunk if needed) to fit."
        ),
    ),
    click.option(
        "--max-side",
        type=click.IntRange(min=1),
        metavar="N",
        default=None,
        help="Longest side in pixels; smaller images are not enlarged.",
    ),
    click.option(
        "--strip",
        is_flag=True,
        help="Leave out EXIF, XMP and comments; the colour profile is kept.",
    ),
    click.option(
        "--background",
        metavar="COLOR",
        default=None,
        callback=_background,
        help="Colour for transparent areas in jpg output (default white).",
    ),
)
def compress(
    sources: tuple[Path, ...],
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    output: Path | None,
    outdir: Path | None,
    target: str | None,
    quality: int | None,
    size_limit: int | None,
    max_side: int | None,
    strip: bool,
    background: tuple[int, int, int] | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Make image files smaller, optionally to a target size.

    Each file is re-encoded in its own format (or with --to another) and
    written as <name>_compressed.<ext> beside its source unless -o or
    --outdir says otherwise. A copy that is not smaller than its source is
    not kept, unless --to changes the format.
    """
    from jzr_tools.core import image_compress as compressor
    from jzr_tools.core.batch import ConversionResult

    if quality is not None and size_limit is not None:
        raise click.UsageError("--quality and --size cannot be used together.")

    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)
    target_extension = target.lower() if target is not None else None

    if sources:
        if directory is not None or recursive:
            raise click.UsageError(
                "--dir and --recursive only apply when no SOURCES are listed."
            )
        selected = [_resolve_source(path, None) for path in sources]
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        extensions = SUPPORTED_EXTENSIONS if target_extension is not None else compressor.COMPRESSIBLE
        found = find_sources(root, extensions=extensions, pattern=regex, recursive=recursive)
        # Earlier results are not compressed again.
        selected = [path for path in found if not path.stem.endswith(compressor.COMPRESSED_STEM)]

    if output is not None:
        written = output.suffix.lstrip(".")
        if target_extension is not None:
            if normalize_extension(written) not in equivalent_extensions(target_extension):
                raise click.UsageError(
                    f"--output {output} does not end in .{target_extension}."
                )
        elif written:
            own = selected[0].suffix if selected else ""
            if not (
                normalize_extension(written) in compressor.TARGETS
                or compressor.same_format(written, own)
            ):
                raise click.UsageError(
                    f"--output {output}: .{written.lower()} is not a compress format; "
                    "use jpg, png, webp, avif or heic."
                )
            if not compressor.same_format(written, own):
                target_extension = normalize_extension(written)

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    def extension_of(source: Path) -> str:
        if target_extension is not None:
            return target_extension
        return source.suffix.lstrip(".")

    # Each source becomes the result that ends it here, or None to compress.
    outcomes: list[ConversionResult | None] = []
    for source in selected:
        reason = compressor.not_compressible(extension_of(source))
        if reason is None:
            outcomes.append(None)
        else:
            reason = compressor.not_compressible(source.suffix) or reason
            outcomes.append(ConversionResult(source, source, (), "failed", reason))

    planned = [source for source, outcome in zip(selected, outcomes) if outcome is None]
    if output is not None:
        targets = iter([output] * len(planned))
    else:
        targets = iter(
            compressor.compress_targets(
                planned, extension=target_extension, outdir=outdir, root=root
            )
        )

    _resize_notes(
        [normalize_extension(extension_of(source)) for source in planned],
        quality=quality,
        background=background,
    )

    counts: Counter[str] = Counter()
    for source, outcome in zip(selected, outcomes):
        if outcome is None:
            result = compressor.compress_file(
                source,
                next(targets),
                quality=quality,
                size_limit=size_limit,
                max_side=max_side,
                background=background,
                strip=strip,
                overwrite=overwrite,
                dry_run=dry_run,
            )
        else:
            result = outcome
        counts[result.status] += 1
        report(result)

    summarize("pillow", counts)


def _strip_line(result: ConversionResult, *, in_place: bool) -> None:
    """Print a cleaned or planned file on stdout; anything else through report."""
    if result.status not in ("cleaned", "planned"):
        report(result)
        return
    shown = f"{result.source}" if in_place else f"{result.source} -> {result.target}"
    click.echo(f"{shown} ({result.detail})")


@app.command()
@batch_options(
    click.option("--in-place", is_flag=True, help="Replace each source with its cleaned copy."),
    click.option(
        "--no-color-profile",
        "no_color_profile",
        is_flag=True,
        help="Remove the colour profile too.",
    ),
)
def strip(
    sources: tuple[Path, ...],
    directory: Path | None,
    pattern: str | None,
    recursive: bool,
    output: Path | None,
    outdir: Path | None,
    in_place: bool,
    no_color_profile: bool,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Remove metadata from JPEG, PNG and WebP files without re-encoding.

    The image data is copied byte for byte, so quality does not change. EXIF
    (with GPS), XMP, IPTC, comments, text chunks and data after the image are
    removed; the orientation and the colour profile are kept. Each file is
    written as <name>_clean.<ext> beside its source unless -o, --outdir or
    --in-place says otherwise.
    """
    from jzr_tools.core import image_strip as stripper
    from jzr_tools.core.batch import ConversionResult, _path_key

    if in_place and (output is not None or outdir is not None):
        raise click.UsageError("--in-place cannot be used with --output or --outdir.")
    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)

    if sources:
        if directory is not None or recursive:
            raise click.UsageError(
                "--dir and --recursive only apply when no SOURCES are listed."
            )
        selected = [_resolve_source(path, None) for path in sources]
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        found = find_sources(
            root, extensions=stripper.STRIPPABLE, pattern=regex, recursive=recursive
        )
        # Earlier results are not cleaned again, unless nothing new is named.
        selected = (
            found
            if in_place
            else [path for path in found if not path.stem.endswith(stripper.CLEAN_STEM)]
        )

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    if in_place:
        targets = list(selected)
    elif output is not None:
        targets = [output]
    else:
        targets = stripper.clean_targets(selected, outdir=outdir, root=root)

    counts: Counter[str] = Counter()
    for source, target in zip(selected, targets):
        if not in_place and _path_key(source) == _path_key(target):
            # Only --in-place may replace a source, even with --overwrite.
            result = ConversionResult(source, target, (), "skipped", "source is the target")
        else:
            result = stripper.strip_file(
                source,
                target,
                keep_color_profile=not no_color_profile,
                overwrite=overwrite or in_place,
                dry_run=dry_run,
            )
        counts[result.status] += 1
        _strip_line(result, in_place=in_place)

    summarize("strip", counts)

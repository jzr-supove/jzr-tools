"""Audio CLI commands.

* ``jzr audio convert [SOURCES]... --to mp3`` converts the listed files, or
  every audio file in a directory when none are listed.
* ``jzr audio wav2mp3 ...`` is shorthand for ``convert --from wav --to mp3``;
  any ``<from>2<to>`` pair is resolved on demand.
* ``jzr audio info FILE...`` describes audio files, tags included.
* ``jzr audio formats`` lists the targets and what the ffmpeg build can do.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

import click

from jzr_tools.commands._batch import (
    batch_options,
    check_output,
    compile_pattern,
    report,
    summarize,
)
from jzr_tools.commands._media import (
    ProgressLine,
    formats_command,
    info_command,
    resolve_source,
)
from jzr_tools.core.media import AUDIO_EXTENSIONS, AUDIO_FORMATS

_PAIR_PATTERN = re.compile(r"^([a-z0-9]+)2([a-z0-9]+)$")


class AudioGroup(click.Group):
    """Click group that resolves ``<from>2<to>`` command names on demand."""

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        command = super().get_command(ctx, name)
        if command is not None:
            return command
        match = _PAIR_PATTERN.match(name.lower())
        if match is None:
            return None
        source_extension, target_extension = match.groups()
        if source_extension not in AUDIO_EXTENSIONS or target_extension not in AUDIO_FORMATS:
            return None
        return _build_pair_command(name.lower(), source_extension, target_extension)


@click.group(cls=AudioGroup)
def app() -> None:
    """Work with audio files.

    Needs ffmpeg and ffprobe: set JZR_FFMPEG and JZR_FFPROBE to use a
    particular copy, otherwise they are taken from PATH. Besides the commands
    below, any format pair works as a command: 'jzr audio wav2mp3 song.wav'
    is short for 'jzr audio convert song.wav --from wav --to mp3'.
    """


def _bitrate(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    from jzr_tools.core.media import parse_bitrate

    try:
        return parse_bitrate(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


_conversion_options = batch_options(
    click.option(
        "-b",
        "--bitrate",
        default=None,
        callback=_bitrate,
        help="Bitrate for lossy targets in kb/s, e.g. 128k; forces an encode.",
    ),
)


def _audio_format(value: str, option: str) -> str:
    extension = value.strip().lstrip(".").lower()
    if extension not in AUDIO_FORMATS:
        raise click.UsageError(
            f"Unsupported {option} format: {value}. Choose from {', '.join(AUDIO_FORMATS)}."
        )
    return extension


def _source_format(value: str) -> str:
    extension = value.strip().lstrip(".").lower()
    if extension not in AUDIO_EXTENSIONS:
        raise click.UsageError(
            f"Unsupported --from format: {value}. "
            f"Choose from {', '.join(sorted(AUDIO_EXTENSIONS))}."
        )
    return extension


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
    bitrate: str | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    """Select the sources for ``convert`` or a pair command, then convert them."""
    from jzr_tools.core import ffmpeg, media, transcode
    from jzr_tools.core.batch import find_sources, normalize_extension, plan_targets

    regex = compile_pattern(pattern)
    check_output(output, sources=sources, outdir=outdir)
    if output is not None and normalize_extension(output.suffix) != target_extension:
        raise click.UsageError(f"--output {output} does not end in .{target_extension}.")

    if sources:
        if directory is not None or recursive:
            raise click.UsageError(
                "--dir and --recursive only apply when no SOURCES are listed."
            )
        selected = [resolve_source(path, source_extension) for path in sources]
        if regex is not None:
            selected = [path for path in selected if regex.search(path.name) is not None]
        root = None
    else:
        root = directory if directory is not None else Path(".")
        if source_extension is None:
            extensions = AUDIO_EXTENSIONS - {target_extension}
        else:
            extensions = frozenset({source_extension})
        selected = find_sources(root, extensions=extensions, pattern=regex, recursive=recursive)

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    try:
        tools = ffmpeg.locate()
    except media.MissingToolError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error

    if bitrate is not None:
        caveat = media.bitrate_caveat(target_extension)
        if caveat is not None:
            click.echo(f"note: {caveat}", err=True)

    options = media.AudioOptions(bitrate=bitrate)

    def plan(info: media.MediaInfo) -> media.AudioPlan:
        return media.plan_audio_convert(info, target_extension, options)

    if output is not None:
        targets = [output]
    else:
        targets = plan_targets(selected, target_extension, outdir=outdir, root=root)

    counts: Counter[str] = Counter()
    for source, target in zip(selected, targets):
        progress = ProgressLine(source.name)
        try:
            result = transcode.convert_file(
                source,
                target,
                tools=tools,
                plan=plan,
                overwrite=overwrite,
                dry_run=dry_run,
                on_progress=progress if progress.enabled else None,
            )
        finally:
            progress.clear()
        counts[result.status] += 1
        report(result)

    summarize("ffmpeg", counts)


@app.command()
@click.option(
    "-t",
    "--to",
    "target",
    default=None,
    help="Target format: mp3, m4a, opus, ogg, flac or wav. Taken from --output when left out.",
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
    """Convert audio files to another format.

    List SOURCES to convert those files, or leave them out to convert every
    audio file in --dir that is not already in the target format. The audio
    is copied when the target can hold it as it is, otherwise encoded.
    """
    if target is not None:
        target_extension = _audio_format(target, "--to")
    elif output is not None and output.suffix:
        target_extension = _audio_format(output.suffix, "--output")
    else:
        raise click.UsageError("Missing option '-t' / '--to' (or name the file with -o).")
    source_extension = _source_format(source) if source is not None else None
    _convert(sources, source_extension, target_extension, output=output, **options)


def _build_pair_command(name: str, source_extension: str, target_extension: str) -> click.Command:
    """Create the click command backing a ``<from>2<to>`` invocation."""

    @click.command(
        name=name,
        help=(
            f"Convert {source_extension.upper()} files to {target_extension.upper()}.\n\n"
            f"Short for 'jzr audio convert --from {source_extension} "
            f"--to {target_extension}'. List SOURCES (the .{source_extension} "
            "extension may be left off), or leave them out to convert every "
            "matching file in --dir."
        ),
    )
    @_conversion_options
    def command(sources: tuple[Path, ...], **options: object) -> None:
        _convert(sources, source_extension, target_extension, **options)

    return command


app.add_command(
    info_command(
        tags=True,
        help_text=(
            "Show the format, duration, streams and tags of audio files.\n\n"
            "--json prints one array with the same facts and every tag."
        ),
    )
)
app.add_command(
    formats_command(
        targets=AUDIO_FORMATS,
        video=False,
        help_text="Show the target formats, the ffmpeg in use and its audio encoders.",
    )
)

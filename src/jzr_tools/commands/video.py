"""Video CLI commands.

* ``jzr video convert [SOURCES]... --to mp4`` converts the listed files, or
  every video file in a directory when none are listed; an audio format as
  the target takes the video's sound.
* ``jzr video mov2mp4 ...`` is shorthand for ``convert --from mov --to mp4``;
  any ``<from>2<to>`` pair is resolved on demand.
* ``jzr video info FILE...`` describes video files.
* ``jzr video formats`` lists the targets and what the ffmpeg build can do.
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
    require_tools,
    summarize,
)
from jzr_tools.commands._media import (
    ProgressLine,
    formats_command,
    info_command,
    resolve_source,
)
from jzr_tools.core.media import (
    AUDIO_FORMATS,
    VIDEO_CODECS,
    VIDEO_CONTAINERS,
    VIDEO_EXTENSIONS,
    VIDEO_QUALITIES,
    VIDEO_TARGETS,
)

_PAIR_PATTERN = re.compile(r"^([a-z0-9]+)2([a-z0-9]+)$")
# What `convert --to` takes in this version: containers and audio formats.
_CONVERT_TARGETS = VIDEO_CONTAINERS + AUDIO_FORMATS
# Extensions that already are the target container, left alone by a scan.
_SAME_CONTAINER = {"mp4": frozenset({"mp4", "m4v"})}


class VideoGroup(click.Group):
    """Click group that resolves ``<from>2<to>`` command names on demand."""

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        command = super().get_command(ctx, name)
        if command is not None:
            return command
        match = _PAIR_PATTERN.match(name.lower())
        if match is None:
            return None
        source_extension, target_extension = match.groups()
        if source_extension not in VIDEO_EXTENSIONS or target_extension not in _CONVERT_TARGETS:
            return None
        return _build_pair_command(name.lower(), source_extension, target_extension)


@click.group(cls=VideoGroup)
def app() -> None:
    """Work with video files.

    Needs ffmpeg and ffprobe: `jzr ffmpeg install` downloads them, and a
    command that finds none offers to. JZR_FFMPEG and JZR_FFPROBE pick a
    particular copy; PATH is used when jzr has none of its own. Besides the
    commands below, any format pair works as a command: 'jzr video mov2mp4
    clip.mov' is short for 'jzr video convert clip.mov --from mov --to mp4'.
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
        "-c",
        "--codec",
        type=click.Choice(VIDEO_CODECS),
        default=None,
        help="Encode the video with this codec instead of copying it.",
    ),
    click.option(
        "-q",
        "--quality",
        type=click.Choice(VIDEO_QUALITIES),
        default=None,
        help="Quality of a video encode.  [default: balanced]",
    ),
    click.option(
        "--crf",
        type=click.IntRange(0, 63),
        default=None,
        help="Constant-quality value for a video encode (lower is better).",
    ),
    click.option(
        "--resize",
        type=click.IntRange(min=2),
        metavar="N",
        default=None,
        help="Scale the short edge down to N pixels; never up.",
    ),
    click.option(
        "--fps",
        type=click.FloatRange(min=0, min_open=True),
        metavar="N",
        default=None,
        help="Lower the frame rate to N.",
    ),
    click.option("--no-audio", is_flag=True, help="Leave the audio out."),
    click.option(
        "-b",
        "--bitrate",
        default=None,
        callback=_bitrate,
        help="Bitrate for lossy audio targets in kb/s, e.g. 128k; forces an encode.",
    ),
)


def _target_format(value: str, option: str) -> str:
    extension = value.strip().lstrip(".").lower()
    if extension not in _CONVERT_TARGETS:
        raise click.UsageError(
            f"Unsupported {option} format: {value}. Choose from {', '.join(_CONVERT_TARGETS)}."
        )
    return extension


def _source_format(value: str) -> str:
    extension = value.strip().lstrip(".").lower()
    if extension not in VIDEO_EXTENSIONS:
        raise click.UsageError(
            f"Unsupported --from format: {value}. "
            f"Choose from {', '.join(sorted(VIDEO_EXTENSIONS))}."
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
    codec: str | None,
    quality: str | None,
    crf: int | None,
    resize: int | None,
    fps: float | None,
    no_audio: bool,
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

    video_options = media.VideoOptions(
        codec=codec, quality=quality, crf=crf, resize=resize, fps=fps, no_audio=no_audio
    )
    try:
        media.check_convert_options(target_extension, video_options, bitrate=bitrate)
    except ValueError as error:
        raise click.UsageError(f"{error}.") from error

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
            same = _SAME_CONTAINER.get(target_extension, frozenset({target_extension}))
            extensions = VIDEO_EXTENSIONS - same
        else:
            extensions = frozenset({source_extension})
        selected = find_sources(root, extensions=extensions, pattern=regex, recursive=recursive)

    if not selected:
        click.echo("No matching files.", err=True)
        raise click.exceptions.Exit(1)

    tools = ffmpeg.Tools(*require_tools("ffmpeg", "ffprobe"))

    if target_extension in AUDIO_FORMATS:
        if bitrate is not None:
            caveat = media.bitrate_caveat(target_extension)
            if caveat is not None:
                click.echo(f"note: {caveat}", err=True)
        audio_options = media.AudioOptions(bitrate=bitrate)

        def plan(info: media.MediaInfo) -> media.Plan:
            return media.plan_audio_convert(info, target_extension, audio_options)

    else:

        def plan(info: media.MediaInfo) -> media.Plan:
            return media.plan_convert(info, target_extension, video_options)

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
    help=(
        "Target format: mp4, mov, mkv or webm, or mp3, m4a, opus, ogg, flac or wav "
        "for the sound only. Taken from --output when left out."
    ),
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
    """Convert video files to another container, or to audio.

    List SOURCES to convert those files, or leave them out to convert every
    video file in --dir that is not already in the target container. Each
    stream is copied when the target can hold it, which is quick and loses
    nothing; otherwise it is encoded (H.264 and AAC, or VP9 and Opus for
    webm). --codec, --resize and --fps make the video encode.
    """
    if target is not None:
        target_extension = _target_format(target, "--to")
    elif output is not None and output.suffix:
        target_extension = _target_format(output.suffix, "--output")
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
            f"Short for 'jzr video convert --from {source_extension} "
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
        tags=False,
        help_text=(
            "Show the container, duration, size and streams of video files.\n\n"
            "Rotation is given in degrees clockwise. --json prints one array "
            "with the same facts."
        ),
    )
)
app.add_command(
    formats_command(
        targets=VIDEO_TARGETS,
        video=True,
        help_text="Show the target formats, the ffmpeg in use and its encoders.",
    )
)

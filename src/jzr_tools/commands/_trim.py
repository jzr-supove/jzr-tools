"""The ``trim`` command of ``jzr video`` and ``jzr audio``.

Both groups cut a time range out of their files with the same options; only
the planner, the scanned extensions and the mode flag differ (``--fast`` for
video, ``--exact`` for audio). The media modules are imported inside the
command, so ``trim -h`` starts nothing and imports little.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import click

from jzr_tools.commands._batch import (
    batch_options,
    check_output,
    compile_pattern,
    report,
    require_tools,
    summarize,
)
from jzr_tools.commands._media import ProgressLine, resolve_source

if TYPE_CHECKING:
    from jzr_tools.core.batch import ConversionResult
    from jzr_tools.core.ffmpeg import Tools
    from jzr_tools.core.trim import TrimRange


def _time(ctx: click.Context, param: click.Parameter, value: str | None) -> float | None:
    if value is None:
        return None
    from jzr_tools.core.media import parse_time

    try:
        return parse_time(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


_VIDEO_HELP = """Cut a time range out of video files.

The cut is exact by default: it starts on the asked frame and lasts the
asked time, which means encoding the video again (at high quality, in the
source's codec when there is an encoder for it). --fast copies the streams
instead: instant and lossless, but the cut starts at the keyframe at or
before --start, and the result line says where. The output is
<name>_trim.<ext> in the source's container.

Times are seconds (90, 7.5) or [H:]MM:SS[.mmm] (1:30, 0:01:30.250).
"""

_AUDIO_HELP = """Cut a time range out of audio files.

WAV and FLAC are cut to the sample, losslessly. MP3, AAC, Opus and Vorbis
are cut by copying, with no quality loss and to within one audio frame
(about 1/40 s); --exact encodes them again at the source's bitrate for a cut
to the millisecond. Tags and cover art are kept. The output is
<name>_trim.<ext> in the source's format.

Times are seconds (90, 7.5) or [H:]MM:SS[.mmm] (1:30, 0:01:30.250).
"""


def trim_command(kind: str) -> click.Command:
    """Build ``trim`` for the ``video`` or ``audio`` group."""
    video = kind == "video"
    if video:
        mode = click.option(
            "--fast",
            "mode",
            is_flag=True,
            help="Copy the streams: instant, but starts at the keyframe before --start.",
        )
    else:
        mode = click.option(
            "--exact",
            "mode",
            is_flag=True,
            help="Encode MP3, AAC, Opus or Vorbis again for a cut to the millisecond.",
        )

    @click.command(name="trim", help=_VIDEO_HELP if video else _AUDIO_HELP)
    @batch_options(
        click.option(
            "--start",
            default=None,
            metavar="TIME",
            callback=_time,
            help="Where the cut starts.  [default: 0]",
        ),
        click.option(
            "--end",
            default=None,
            metavar="TIME",
            callback=_time,
            help="Where the cut ends.  [default: the end of the file]",
        ),
        click.option(
            "--duration",
            default=None,
            metavar="TIME",
            callback=_time,
            help="How long the cut lasts, instead of --end.",
        ),
        mode,
    )
    def command(
        sources: tuple[Path, ...],
        directory: Path | None,
        pattern: str | None,
        recursive: bool,
        output: Path | None,
        outdir: Path | None,
        start: float | None,
        end: float | None,
        duration: float | None,
        mode: bool,
        overwrite: bool,
        dry_run: bool,
    ) -> None:
        from jzr_tools.core import ffmpeg, media, trim
        from jzr_tools.core.batch import ConversionResult, find_sources, normalize_extension

        regex = compile_pattern(pattern)
        try:
            rng = trim.make_range(start, end, duration)
        except ValueError as error:
            raise click.UsageError(f"{error}.") from error
        check_output(output, sources=sources, outdir=outdir)

        if sources:
            if directory is not None or recursive:
                raise click.UsageError(
                    "--dir and --recursive only apply when no SOURCES are listed."
                )
            selected = [resolve_source(path, None) for path in sources]
            if regex is not None:
                selected = [path for path in selected if regex.search(path.name) is not None]
            root = None
        else:
            root = directory if directory is not None else Path(".")
            extensions = media.VIDEO_EXTENSIONS if video else media.AUDIO_EXTENSIONS
            found = find_sources(root, extensions=extensions, pattern=regex, recursive=recursive)
            selected = [path for path in found if not trim.is_trim_output(path)]

        if output is not None and selected:
            source_extension = normalize_extension(selected[0].suffix)
            if normalize_extension(output.suffix) != source_extension:
                raise click.UsageError(
                    f"--output {output} must keep the source's .{source_extension}; "
                    "trim does not change the container."
                )

        if not selected:
            click.echo("No matching files.", err=True)
            raise click.exceptions.Exit(1)

        tools = ffmpeg.Tools(*require_tools("ffmpeg", "ffprobe"))
        targets = [output] if output is not None else trim.trim_targets(
            selected, outdir=outdir, root=root
        )

        counts: Counter[str] = Counter()
        for source, target in zip(selected, targets):
            extension = normalize_extension(source.suffix)
            if not extension:
                result = ConversionResult(
                    source, target, (), "failed", "no file extension, so the container is unknown"
                )
            else:
                result = _trim_one(
                    source, target, extension, rng, video=video, mode=mode,
                    tools=tools, overwrite=overwrite, dry_run=dry_run,
                )
            counts[result.status] += 1
            report(result)

        summarize("ffmpeg", counts)

    return command


def _trim_one(
    source: Path,
    target: Path,
    extension: str,
    rng: TrimRange,
    *,
    video: bool,
    mode: bool,
    tools: Tools,
    overwrite: bool,
    dry_run: bool,
) -> ConversionResult:
    """Trim one file with the planner of its group."""
    from jzr_tools.core import ffmpeg, media, trim

    keyframe = None
    if video:

        def plan(info: media.MediaInfo, found: float | None) -> trim.TrimPlan:
            return trim.plan_video_trim(info, extension, rng, fast=mode, keyframe=found)

        if mode and rng.start > 0:

            def keyframe(info: media.MediaInfo) -> float | None:
                if info.video is None:
                    return None
                return ffmpeg.keyframe_before(
                    source, tools.ffprobe, rng.start, info.video.index
                )

    else:

        def plan(info: media.MediaInfo, found: float | None) -> trim.TrimPlan:
            return trim.plan_audio_trim(info, extension, rng, exact=mode)

    progress = ProgressLine(source.name)
    try:
        return trim.trim_file(
            source,
            target,
            tools=tools,
            plan=plan,
            overwrite=overwrite,
            dry_run=dry_run,
            on_progress=progress if progress.enabled else None,
            keyframe=keyframe,
        )
    finally:
        progress.clear()

"""Convert one media file with ffmpeg, reporting what happened.

The per-file step of the video and audio batch commands: skip what must not
be written, probe, plan, then run ffmpeg (or only show the command for
``--dry-run``). It returns a ``ConversionResult`` instead of raising, so one
bad file never stops a batch.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from jzr_tools.core import ffmpeg
from jzr_tools.core.batch import ConversionResult
from jzr_tools.core.media import MediaError, MediaInfo, Plan

Planner = Callable[[MediaInfo], Plan]


def _same_file(source: Path, target: Path) -> bool:
    try:
        return target.exists() and os.path.samefile(source, target)
    except OSError:
        return False


def convert_file(
    source: Path,
    target: Path,
    *,
    tools: ffmpeg.Tools,
    plan: Planner,
    overwrite: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[ffmpeg.Progress], None] | None = None,
) -> ConversionResult:
    """Probe ``source``, plan with ``plan`` and write ``target``."""
    if _same_file(source, target):
        return ConversionResult(source, target, (), "skipped", "source is the target")
    if target.exists() and not overwrite:
        return ConversionResult(
            source, target, (), "skipped", "target exists (use --overwrite)"
        )

    try:
        info = ffmpeg.probe(source, tools.ffprobe)
        planned = plan(info)
    except MediaError as error:
        return ConversionResult(source, target, (), "failed", str(error))

    step = ("-i", os.path.abspath(source), *planned.args, ffmpeg.OUTPUT)
    job = ffmpeg.Job((step,), target, info.duration)
    command = tuple(ffmpeg.command_lines(job, tools)[0])
    if dry_run:
        return ConversionResult(source, target, command, "planned", planned.note)

    try:
        ffmpeg.run(job, tools, on_progress=on_progress)
    except MediaError as error:
        return ConversionResult(source, target, command, "failed", str(error))
    return ConversionResult(source, target, command, "converted", planned.note)

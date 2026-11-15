"""Cutting a time range out of a video or audio file.

The planners are pure: they take a ``MediaInfo`` and return the ffmpeg
arguments, so they are tested from hand-made data. ``trim_file`` is the
per-file step of ``jzr video trim`` and ``jzr audio trim``: skip, probe,
plan, run, and report what happened without raising.

A video cut is exact by default: ffmpeg seeks to the keyframe before the
start and decodes from there, so the output begins on the asked frame and the
video is encoded again. ``fast`` copies the streams instead, so the cut starts
on that keyframe. Audio is copied (lossy formats, cut to within one audio
frame) or decoded and written again in the same lossless codec (WAV, FLAC,
cut to the sample).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re

from jzr_tools.core import ffmpeg
from jzr_tools.core.batch import ConversionResult, _path_key
from jzr_tools.core.media import (
    VIDEO_ENCODERS,
    MediaError,
    MediaInfo,
    _CRF,
    _COVER_TARGETS,
    _audio_label,
    _best_audio,
    _container_names,
    _deep_pixels,
    _encoder_args,
    _scale,
    format_time,
)

TRIM_SUFFIX = "_trim"

_TRIM_STEM = re.compile(r"_trim(?:-\d+)?$")
_FAST_START = frozenset({"mp4", "m4v", "mov"})
# Containers that hold neither H.264 nor VP9 (Ogg video holds Theora and VP8),
# so a source codec without an encoder of ours cannot be cut exactly there.
_NO_FALLBACK = frozenset({"ogv"})
# A different start smaller than this is the asked start, printed rounded.
_SAME_TIME = 0.0005
# Encoders for an `--exact` audio cut, by source codec, and the setting used
# when the source's bitrate is unknown.
_EXACT_AUDIO = {
    "mp3": ("libmp3lame", ("-q:a", "2")),
    "aac": ("aac", ("-b:a", "192k")),
    "opus": ("libopus", ("-b:a", "128k")),
    "vorbis": ("libvorbis", ("-q:a", "5")),
}


@dataclass(frozen=True)
class TrimRange:
    """The part to keep, in seconds; ``end=None`` is the end of the file."""

    start: float = 0.0
    end: float | None = None


@dataclass(frozen=True)
class TrimPlan:
    # ffmpeg arguments before ``-i <source>`` (the seek).
    input_args: tuple[str, ...]
    # ffmpeg arguments between ``-i <source>`` and the output path.
    args: tuple[str, ...]
    # Length of the output in seconds, for the progress line.
    duration: float | None
    # What happened, for the result line.
    note: str
    # Copy the source's cover art with mutagen after ffmpeg (Ogg files,
    # whose pictures ffmpeg reads but cannot write).
    copy_cover: bool = False


def make_range(
    start: float | None, end: float | None, duration: float | None
) -> TrimRange:
    """Combine ``--start``, ``--end`` and ``--duration`` into a range.

    Raises ``ValueError`` for what no file could satisfy.
    """
    if start is None and end is None and duration is None:
        raise ValueError("give --start, --end or --duration")
    if end is not None and duration is not None:
        raise ValueError("--end and --duration cannot be used together")
    if duration is not None and duration <= 0:
        raise ValueError("--duration must be more than 0")
    begin = start or 0.0
    if duration is not None:
        end = begin + duration
    if end is not None and end <= begin:
        raise ValueError(
            f"--end {format_time(end, 3)} is not after --start {format_time(begin, 3)}"
        )
    return TrimRange(begin, end)


def seconds_arg(value: float) -> str:
    """Write ``value`` seconds for ffmpeg: ``7``, ``7.5``, ``90.25``."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def fit_range(info: MediaInfo, rng: TrimRange) -> tuple[float | None, list[str]]:
    """Check ``rng`` against the file; return the ``-t`` length and notes.

    A length of ``None`` means up to the end of the file.
    """
    notes: list[str] = []
    end = rng.end
    if info.duration is not None:
        if rng.start >= info.duration:
            raise MediaError(
                f"--start {format_time(rng.start, 3)} is past the end of the file "
                f"({format_time(info.duration, 2)})"
            )
        if end is not None and end > info.duration:
            notes.append(f"the file ends at {format_time(info.duration, 2)}")
            end = None
    return (None if end is None else end - rng.start), notes


def is_trim_output(path: Path) -> bool:
    """Whether ``path`` looks like an earlier ``_trim`` result."""
    return _TRIM_STEM.search(path.stem) is not None


def trim_targets(
    sources: Sequence[Path], *, outdir: Path | None = None, root: Path | None = None
) -> list[Path]:
    """Return ``<stem>_trim<ext>`` for each source, distinct within the run."""
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = source.parent
        if outdir is not None:
            directory = outdir
            if root is not None:
                try:
                    directory = outdir / source.parent.relative_to(root)
                except ValueError:
                    directory = outdir
        name = f"{source.stem}{TRIM_SUFFIX}"
        suffix = source.suffix.lower()
        candidate = directory / f"{name}{suffix}"
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = directory / f"{name}-{counter}{suffix}"
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)
    return targets


def _ext(target_ext: str) -> str:
    return target_ext.strip().lstrip(".").lower()


def _seek(start: float) -> tuple[str, ...]:
    return ("-ss", seconds_arg(start)) if start > 0 else ()


def _length(length: float | None) -> list[str]:
    return ["-t", seconds_arg(length)] if length is not None else []


def plan_video_trim(
    info: MediaInfo,
    target_ext: str,
    rng: TrimRange,
    *,
    fast: bool = False,
    keyframe: float | None = None,
) -> TrimPlan:
    """Plan the cut of ``rng`` out of a video, exact unless ``fast``."""
    target = _ext(target_ext)
    video = info.video
    if video is None:
        raise MediaError("no video track; use 'jzr audio trim' for it")
    audio = _best_audio(info.audio)
    length, range_notes = fit_range(info, rng)

    args: list[str] = ["-map", f"0:{video.index}"]
    if audio is not None:
        args += ["-map", f"0:{audio.index}"]
    args += _length(length)

    if fast:
        args += ["-c:v", "copy"]
        summary = "video copied"
        if audio is not None:
            args += ["-c:a", "copy"]
            summary += ", audio copied"
        # Keep the frames from the keyframe on instead of hiding them behind
        # an edit list, which players that ignore edit lists would show.
        args += ["-avoid_negative_ts", "make_zero"]
    else:
        if video.codec in VIDEO_ENCODERS and video.codec != "gif":
            codec = video.codec
        elif target in _NO_FALLBACK:
            raise MediaError(f"an exact cut cannot be written back into .{target}; use --fast")
        else:
            codec = "vp9" if target == "webm" else "h264"
        scale, _ = _scale(video, None)
        if scale is not None:
            args += ["-vf", scale]
        high = _CRF[codec][0][0]
        args += ["-c:v", VIDEO_ENCODERS[codec], "-crf", str(high)]
        if codec == "vp9":
            args += ["-b:v", "0"]
        deep = codec != "h264" and _deep_pixels(video.pixel_format)
        args += ["-pix_fmt", "yuv420p10le" if deep else "yuv420p"]
        if codec == "hevc":
            args += ["-x265-params", "log-level=error"]
        elif codec == "vp9":
            args += ["-row-mt", "1", "-cpu-used", "4"]
        if codec == "hevc" and target in _FAST_START:
            args += ["-tag:v", "hvc1"]
        summary = f"video encoded to {codec}"
        if audio is not None:
            args += ["-c:a", "copy"]
            if rng.start > 0:
                # Copied audio would otherwise start at the keyframe too.
                args += ["-copypriorss:a", "0"]
            summary += ", audio copied"

    if target in _FAST_START:
        args += ["-movflags", "+faststart"]

    notes = [summary, *range_notes]
    dropped = [_audio_label(s) for s in info.audio if s is not audio]
    dropped += [label for label in info.extra if not label.startswith("data stream")]
    if info.cover_art:
        dropped.append("cover art")
    if dropped:
        notes.append("dropped: " + ", ".join(dropped))

    duration = length
    if duration is None and info.duration is not None:
        duration = info.duration - rng.start
    if fast and keyframe is not None and rng.start > 0:
        if abs(rng.start - keyframe) > _SAME_TIME:
            notes.append(
                f"starts at {format_time(keyframe, 3)}, "
                f"the keyframe before {format_time(rng.start, 3)}"
            )
        if duration is not None:
            duration += max(0.0, rng.start - keyframe)

    return TrimPlan(_seek(rng.start), tuple(args), duration, "; ".join(notes))


def _exact_bitrate(codec: str, bitrate: int | None) -> int | None:
    if not bitrate:
        return None
    rate = round(bitrate / 1000)
    low, high = (32, 320) if codec == "mp3" else (8, 512)
    return min(high, max(low, rate))


def plan_audio_trim(
    info: MediaInfo, target_ext: str, rng: TrimRange, *, exact: bool = False
) -> TrimPlan:
    """Plan the cut of ``rng`` out of an audio file."""
    target = _ext(target_ext)
    if info.video is not None:
        raise MediaError("a video file; use 'jzr video trim' for it")
    if not info.audio:
        raise MediaError("no audio track")
    stream = info.audio[0]
    length, range_notes = fit_range(info, rng)
    ogg = "ogg" in _container_names(info)

    args: list[str] = ["-map", "0:a:0"]
    if info.cover_art and target in _COVER_TARGETS:
        args += ["-map", "0:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    args += ["-map_metadata", "0:s:a:0" if ogg else "0"]
    args += _length(length)

    codec = stream.codec or ""
    caveat: str | None = None
    if codec == "flac" or codec.startswith("pcm_"):
        if codec == "flac":
            args += _encoder_args("flac", stream, None)
        else:
            args += ["-c:a", codec]
        summary = "cut to the sample"
        if exact:
            caveat = f"--exact changes nothing: .{target} is always cut to the sample"
    elif not exact:
        args += ["-c:a", "copy"]
        if rng.start > 0:
            # Drop the packets before the cut that the seek lands on.
            args += ["-copypriorss:a", "0"]
        summary = "audio copied"
    else:
        if codec not in _EXACT_AUDIO:
            raise MediaError(
                f"--exact cannot re-encode {codec or 'this audio'}; "
                "leave it out to cut by copying"
            )
        encoder, fallback = _EXACT_AUDIO[codec]
        rate = _exact_bitrate(codec, stream.bitrate)
        args += ["-c:a", encoder]
        if rate is None:
            args += list(fallback)
            summary = f"audio encoded to {codec}"
        else:
            args += ["-b:a", f"{rate}k"]
            summary = f"audio encoded to {codec} at {rate} kb/s"

    if target == "mp3":
        args += ["-id3v2_version", "3"]

    copy_cover = info.cover_art and ogg
    notes = [summary, *range_notes]
    if caveat:
        notes.append(caveat)
    if info.cover_art and not copy_cover and target not in _COVER_TARGETS:
        notes.append(f"cover art dropped: .{target} cannot hold it")

    duration = length
    if duration is None and info.duration is not None:
        duration = info.duration - rng.start
    return TrimPlan(_seek(rng.start), tuple(args), duration, "; ".join(notes), copy_cover)


Planner = Callable[[MediaInfo, "float | None"], TrimPlan]
KeyframeFinder = Callable[[MediaInfo], "float | None"]


def _same_file(source: Path, target: Path) -> bool:
    try:
        return target.exists() and os.path.samefile(source, target)
    except OSError:
        return False


def _copy_cover(source: Path, target: Path) -> str | None:
    """Copy the cover art of ``source`` into ``target``; return why not."""
    from jzr_tools.core import tags

    try:
        cover = tags.read_tags(source).cover
        if cover is not None:
            tags.apply_edit(target, tags.TagEdit(cover=cover))
    except tags.TagError as error:
        return f"cover art not kept: {error}"
    return None


def trim_file(
    source: Path,
    target: Path,
    *,
    tools: ffmpeg.Tools,
    plan: Planner,
    overwrite: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[ffmpeg.Progress], None] | None = None,
    keyframe: KeyframeFinder | None = None,
) -> ConversionResult:
    """Probe ``source``, plan the cut with ``plan`` and write ``target``."""
    if _same_file(source, target):
        return ConversionResult(source, target, (), "skipped", "source is the target")
    if target.exists() and not overwrite:
        return ConversionResult(
            source, target, (), "skipped", "target exists (use --overwrite)"
        )

    try:
        info = ffmpeg.probe(source, tools.ffprobe)
        found = keyframe(info) if keyframe is not None else None
        planned = plan(info, found)
    except MediaError as error:
        return ConversionResult(source, target, (), "failed", str(error))

    step = (*planned.input_args, "-i", os.path.abspath(source), *planned.args, ffmpeg.OUTPUT)
    job = ffmpeg.Job((step,), target, planned.duration)
    command = tuple(ffmpeg.command_lines(job, tools)[0])
    if dry_run:
        return ConversionResult(source, target, command, "planned", planned.note)

    try:
        ffmpeg.run(job, tools, on_progress=on_progress)
    except MediaError as error:
        return ConversionResult(source, target, command, "failed", str(error))

    note = planned.note
    if planned.copy_cover:
        problem = _copy_cover(source, target)
        if problem is not None:
            note = f"{note}; {problem}" if note else problem
    return ConversionResult(source, target, command, "converted", note)

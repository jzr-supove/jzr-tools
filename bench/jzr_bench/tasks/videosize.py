"""Make a copy of a large video under a size limit, keeping its length and sound (held out)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import CheckResult, Task, sources_unchanged

PROMPT = (
    "Make a copy of talk.mp4 that is under 8 MB so I can post it in a chat. "
    "Keep the whole length and the sound. Save it as talk_small.mp4."
)
SOURCE = "talk.mp4"
OUTPUT = "talk_small.mp4"
SECONDS = 20
LIMIT = 8 * 1024 * 1024  # "8 MB" as chat apps mean it: 8 MiB
DURATION_TOLERANCE_S = 0.5
FRAME_AT_S = 10
COMPARE_SIZE = (96, 54)  # as in the gif task
FRAME_LIMIT = 25.0  # mean absolute difference at COMPARE_SIZE; a fair re-encode is under 10, a negated picture over 35


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"the videosize task needs {name} on PATH")
    return path


def _video_encoder(ffmpeg: str) -> list[str]:
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True, timeout=60
    ).stdout
    if " libx264 " in encoders:
        return ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "10M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-b:v", "10M", "-pix_fmt", "yuv420p"]


def _prepare(fixture_dir: Path) -> None:
    """A 20 s 720p test pattern with moving noise on it, so that it takes at least 20 MB."""
    ffmpeg = _tool("ffmpeg")
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
            "-vf", "noise=alls=24:allf=t",
            *_video_encoder(ffmpeg),
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart",
            str(fixture_dir / SOURCE),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )  # fmt: skip


def _probe(path: Path) -> dict | None:
    """ffprobe's streams and format of the file, None when ffprobe cannot read it."""
    result = subprocess.run(
        [
            _tool("ffprobe"), "-v", "error",
            "-show_entries", "stream=codec_type", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _frame(path: Path, second: float):
    """The frame at `second`, scaled to COMPARE_SIZE; None when it cannot be decoded."""
    from PIL import Image, UnidentifiedImageError

    with tempfile.TemporaryDirectory(prefix="jzr-bench-videosize-") as tmp:
        png = Path(tmp) / "frame.png"
        result = subprocess.run(
            [_tool("ffmpeg"), "-v", "error", "-y", "-ss", str(second), "-i", str(path), "-an",
             "-frames:v", "1", str(png)],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )  # fmt: skip
        if result.returncode != 0 or not png.is_file():
            return None
        try:
            with Image.open(png) as image:
                return image.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.BOX)
        except (UnidentifiedImageError, OSError):
            return None


def _distance(a, b) -> float:
    from PIL import ImageChops, ImageStat

    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _duration(info: dict) -> float | None:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    problems = sources_unchanged(work_dir, fixture_dir, [SOURCE])
    small = work_dir / OUTPUT
    if not small.is_file():
        problems.append(f"{OUTPUT} does not exist")
        return CheckResult(False, problems)
    info = _probe(small)
    kinds = {stream.get("codec_type") for stream in (info or {}).get("streams", [])}
    if info is None or not kinds & {"video", "audio"}:
        problems.append(f"{OUTPUT} is not a valid video that ffprobe can read (no video or audio stream found)")
        return CheckResult(False, problems)

    notes = []
    size = small.stat().st_size
    if size >= LIMIT:
        problems.append(f"{OUTPUT} is too large: {size:,} bytes, must be under {LIMIT:,} (8 MB)")
    else:
        notes.append(f"{size:,} bytes")
    if "video" not in kinds:
        problems.append(f"{OUTPUT} has no video stream: the picture is gone")
    if "audio" not in kinds:
        problems.append(f"{OUTPUT} has no audio stream: the sound must be kept")

    source = fixture_dir / SOURCE
    want_s = _duration(_probe(source) or {})
    length = _duration(info)
    if length is None:
        problems.append(f"{OUTPUT} has no duration that ffprobe can read")
    elif want_s is not None and abs(length - want_s) > DURATION_TOLERANCE_S:
        problems.append(
            f"{OUTPUT} duration is {length:.2f} s, the source's is {want_s:.2f} s: "
            f"the whole length must be kept (within {DURATION_TOLERANCE_S} s)"
        )
    elif length is not None:
        notes.append(f"duration {length:.2f} s")

    if "video" in kinds:
        reference = _frame(source, FRAME_AT_S)
        frame = _frame(small, FRAME_AT_S)
        if reference is None:
            raise RuntimeError(f"cannot read the frame at {FRAME_AT_S} s of the fixture {source}")
        if frame is None:
            problems.append(f"the frame at {FRAME_AT_S} s of {OUTPUT} cannot be decoded")
        else:
            difference = _distance(frame, reference)
            if difference >= FRAME_LIMIT:
                problems.append(
                    f"the frame at {FRAME_AT_S} s of {OUTPUT} does not resemble the source's "
                    f"(difference {difference:.1f}, must be under {FRAME_LIMIT:.0f})"
                )
            else:
                notes.append(f"frame at {FRAME_AT_S} s difference {difference:.1f}")
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, notes)


TASK = Task(name="videosize", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)

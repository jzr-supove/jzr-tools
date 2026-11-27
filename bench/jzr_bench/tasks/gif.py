"""Make a GIF from part of a video clip."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import CheckResult, Task

PROMPT = (
    "Make an animated GIF from clip.mp4 in this folder: start at 0:02, 3 seconds long, "
    "480 pixels wide (keep the aspect ratio), under 2 MB. Save it as clip.gif."
)

WIDTH = 480
HEIGHT = 270
HEIGHT_TOLERANCE = 2
DURATION_S = 3.0
DURATION_TOLERANCE_S = 0.3
MAX_BYTES = 2_000_000
START_S = 2
REFERENCE_TIMES = (0, 1, 2, 3, 4, 5)
SOURCE_FPS = 30
WINDOW_FRAMES = range(-2, 5)  # from 2 frames before to 4 after each reference time
# Frames are compared at this size: small enough that palette dithering averages out.
COMPARE_SIZE = (96, 54)


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("the gif task needs ffmpeg on PATH")
    return path


def _video_encoder(ffmpeg: str) -> list[str]:
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True, timeout=60
    ).stdout
    if " libx264 " in encoders:
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "4", "-pix_fmt", "yuv420p"]


def _prepare(fixture_dir: Path) -> None:
    ffmpeg = _ffmpeg()
    target = fixture_dir / "clip.mp4"
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            *_video_encoder(ffmpeg),
            "-c:a", "aac", "-b:a", "96k",
            "-shortest", "-movflags", "+faststart",
            str(target),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )  # fmt: skip


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _reference_frames(clip: Path) -> dict[int, list]:
    """For each of REFERENCE_TIMES, the source frames from a little before to a little after it.

    A GIF's first frame is rarely the source frame at exactly 2.000 s: an fps filter or a seek
    can land a frame or two away, and the test pattern moves fast, so each time is a window.
    """
    from PIL import Image

    wanted: dict[int, int] = {}  # source frame number -> reference time
    for t in REFERENCE_TIMES:
        for offset in WINDOW_FRAMES:
            n = t * SOURCE_FPS + offset
            if n >= 0:
                wanted[n] = t
    numbers = sorted(wanted)
    select = "+".join(f"eq(n\\,{n})" for n in numbers)
    frames: dict[int, list] = {t: [] for t in REFERENCE_TIMES}
    with tempfile.TemporaryDirectory(prefix="jzr-bench-gif-") as tmp:
        subprocess.run(
            [_ffmpeg(), "-v", "error", "-y", "-i", str(clip), "-an",
             "-vf", f"select='{select}',scale={WIDTH}:-2", "-fps_mode", "passthrough",
             str(Path(tmp) / "ref%03d.png")],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
        )  # fmt: skip
        for index, n in enumerate(numbers, 1):
            with Image.open(Path(tmp) / f"ref{index:03d}.png") as image:
                frames[wanted[n]].append(image.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.BOX))
    return frames


def _distance(a, b) -> float:
    from PIL import ImageChops, ImageStat

    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from PIL import Image, UnidentifiedImageError

    problems: list[str] = []
    passed_notes: list[str] = []

    clip = work_dir / "clip.mp4"
    if not clip.is_file():
        problems.append("clip.mp4 is missing: the source must stay unchanged")
    elif _sha256(clip) != _sha256(fixture_dir / "clip.mp4"):
        problems.append("clip.mp4 was changed: its SHA-256 differs from the fixture")
    else:
        passed_notes.append("clip.mp4 unchanged")

    gif = work_dir / "clip.gif"
    if not gif.is_file():
        problems.append("clip.gif does not exist")
        return CheckResult(False, problems)

    size = gif.stat().st_size
    if size >= MAX_BYTES:
        problems.append(f"clip.gif is too large: {size:,} bytes, must be under {MAX_BYTES:,}")
    else:
        passed_notes.append(f"size {size:,} bytes")

    try:
        image = Image.open(gif)
    except (UnidentifiedImageError, OSError) as error:
        problems.append(f"clip.gif is not a valid image: {error}")
        return CheckResult(False, problems)

    with image:
        if image.format != "GIF":
            problems.append(f"clip.gif is not a GIF but {image.format}")
            return CheckResult(False, problems)

        width, height = image.size
        if width != WIDTH:
            problems.append(f"width is {width}, must be {WIDTH}")
        if abs(height - HEIGHT) > HEIGHT_TOLERANCE:
            problems.append(f"height is {height}, must be {HEIGHT} ± {HEIGHT_TOLERANCE} (aspect ratio kept)")
        if width == WIDTH and abs(height - HEIGHT) <= HEIGHT_TOLERANCE:
            passed_notes.append(f"{width}x{height}")

        frames = getattr(image, "n_frames", 1)
        if frames < 2:
            problems.append("clip.gif has only one frame, it must be animated (more than one frame)")
        else:
            passed_notes.append(f"{frames} frames")

        total_ms = 0
        for index in range(frames):
            image.seek(index)
            total_ms += image.info.get("duration", 0) or 0
        duration_s = total_ms / 1000
        if abs(duration_s - DURATION_S) > DURATION_TOLERANCE_S:
            problems.append(f"duration is {duration_s:.2f} s, must be {DURATION_S} ± {DURATION_TOLERANCE_S} s")
        else:
            passed_notes.append(f"duration {duration_s:.2f} s")

        image.seek(0)
        first = image.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.BOX)

    references = _reference_frames(fixture_dir / "clip.mp4")
    distances = {t: min(_distance(first, ref) for ref in refs) for t, refs in references.items()}
    closest = min(distances, key=distances.__getitem__)
    if closest != START_S:
        problems.append(
            f"the first frame is closest to the source at {closest} s, not {START_S} s: wrong start "
            f"(distances {', '.join(f'{t} s: {d:.1f}' for t, d in distances.items())})"
        )
    else:
        passed_notes.append(f"starts at {START_S} s")

    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, passed_notes)


TASK = Task(name="gif", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)

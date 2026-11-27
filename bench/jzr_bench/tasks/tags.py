"""Tag three MP3s from their file names and embed a cover picture (held out)."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path

from ._pictures import photo_like
from .base import CheckResult, Task, sources_unchanged

PROMPT = (
    'Tag the three MP3s in this folder: artist "The Testers", album "Bench Songs", the title from each file name '
    '(for "01 - First Light.mp3" it is "First Light") and the track number from its leading number. '
    "Embed cover.jpg as the cover art in each."
)
# file name -> (title, track number, tone in Hz)
SONGS = {
    "01 - First Light.mp3": ("First Light", 1, 330),
    "02 - Second Wind.mp3": ("Second Wind", 2, 550),
    "03 - Third Rail.mp3": ("Third Rail", 3, 880),
}
ARTIST, ALBUM = "The Testers", "Bench Songs"
COVER = "cover.jpg"
COVER_SIZE = (600, 600)
SECONDS = 3
LENGTH_TOLERANCE_S = 0.2
THUMB = (32, 32)
COVER_LIMIT = 10.0  # mean absolute difference at THUMB; the cover scaled down is under 2, another picture over 10
TRACK = re.compile(r"\s*0*(\d+)\s*(?:/\s*\d+\s*)?")


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("the tags task needs ffmpeg on PATH to make its fixtures")
    return path


def _prepare(fixture_dir: Path) -> None:
    ffmpeg = _ffmpeg()
    for name, (_, _, hz) in SONGS.items():
        subprocess.run(
            [
                ffmpeg, "-v", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={SECONDS}",
                "-c:a", "libmp3lame", "-b:a", "128k",
                "-map_metadata", "-1", "-id3v2_version", "0", "-write_id3v1", "0", "-fflags", "+bitexact",
                str(fixture_dir / name),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )  # fmt: skip
    photo_like(COVER_SIZE, 41, noise=4).save(fixture_dir / COVER, format="JPEG", quality=90)


def _thumb(image):
    from PIL import Image

    return image.convert("RGB").resize(THUMB, Image.Resampling.BOX)


def _distance(a, b) -> float:
    from PIL import ImageChops, ImageStat

    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _text(tags, frame: str) -> str | None:
    """The first value of a text frame, None when there is none."""
    found = tags.get(frame) if tags is not None else None
    if found is None or not getattr(found, "text", None):
        return None
    return str(found.text[0])


def _cover_problem(name: str, tags, reference) -> str | None:
    from PIL import Image, UnidentifiedImageError

    pictures = tags.getall("APIC") if tags is not None else []
    if not pictures:
        return f"{name} has no embedded cover picture (art)"
    distances = []
    for picture in pictures:
        try:
            with Image.open(io.BytesIO(picture.data)) as image:
                image.load()
                distances.append(_distance(_thumb(image), reference))
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            continue
    if not distances:
        return f"{name} has an embedded cover picture that does not decode as an image"
    if min(distances) >= COVER_LIMIT:
        return f"{name}: the embedded cover picture does not resemble cover.jpg (difference {min(distances):.1f})"
    return None


def _song_problems(path: Path, name: str, original: Path, reference) -> list[str]:
    from mutagen import MutagenError
    from mutagen.mp3 import MP3

    if not path.is_file():
        return [f"{name} is missing: the files must be tagged under their original names"]
    try:
        audio = MP3(path)
    except (MutagenError, OSError) as error:
        return [f"{name} is not a valid MP3 audio file that can be read: {error}"]
    title, track, _ = SONGS[name]
    tags = audio.tags
    problems = []
    for label, frame, want in (("artist", "TPE1", ARTIST), ("album", "TALB", ALBUM), ("title", "TIT2", title)):
        value = _text(tags, frame)
        if value is None:
            problems.append(f"{name}: the {label} tag is missing, it must be {want!r}")
        elif value != want:
            problems.append(f"{name}: the {label} tag is {value!r}, it must be {want!r}")
    value = _text(tags, "TRCK")
    if value is None:
        problems.append(f"{name}: the track number tag is missing, it must be {track}")
    else:
        parsed = TRACK.fullmatch(value)
        if parsed is None or int(parsed.group(1)) != track:
            problems.append(f"{name}: the track number tag is {value!r}, it must be {track}")
    cover = _cover_problem(name, tags, reference)
    if cover is not None:
        problems.append(cover)
    want_s = MP3(original).info.length
    length = audio.info.length
    if abs(length - want_s) > LENGTH_TOLERANCE_S:
        problems.append(
            f"{name}: the audio length is {length:.2f} s, the original's is {want_s:.2f} s "
            f"(must be within {LENGTH_TOLERANCE_S} second)"
        )
    return problems


def _check(work_dir: Path, fixture_dir: Path, final_text: str) -> CheckResult:
    from PIL import Image

    problems = sources_unchanged(work_dir, fixture_dir, [COVER])
    with Image.open(fixture_dir / COVER) as cover:
        reference = _thumb(cover)
    for name in SONGS:
        problems.extend(_song_problems(work_dir / name, name, fixture_dir / name, reference))
    if problems:
        return CheckResult(False, problems)
    return CheckResult(True, ["artist, album, title, track and cover right in all three files"])


TASK = Task(name="tags", prompt=PROMPT, prepare_fixtures=_prepare, check=_check)

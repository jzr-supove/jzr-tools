"""Shared helpers for the `audio tags` tests.

Audio files and pictures are generated when a test class starts, so nothing
binary is stored in the repository.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib

from media_fixtures import ffmpeg_run

FORMATS = ("mp3", "m4a", "flac", "ogg", "opus")

_CODECS = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
}

# The save method each format's mutagen class uses.
SAVE_TARGETS = {
    "mp3": "mutagen.mp3.MP3.save",
    "m4a": "mutagen.mp4.MP4.save",
    "flac": "mutagen.flac.FLAC.save",
    "ogg": "mutagen.oggvorbis.OggVorbis.save",
    "opus": "mutagen.oggopus.OggOpus.save",
}

CYRILLIC = "Привет, мир"
CJK = "日本語のタイトル"
EMOJI = "Party \U0001F389\U0001F3B6 time"


def make_png(width: int = 8, height: int = 8, *, noise: bool = False) -> bytes:
    """A valid PNG. With ``noise`` the pixels are random, so it does not shrink."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    rows = []
    for y in range(height):
        if noise:
            pixels = os.urandom(width * 3)
        else:
            pixels = bytes((x * 30 % 256, y * 30 % 256, 128) [i] for x in range(width) for i in range(3))
        rows.append(b"\x00" + pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 0 if noise else 9))
        + chunk(b"IEND", b"")
    )


class AudioSet:
    """A temporary folder with one fresh, untagged file per format and some pictures."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-tags-")
        self.dir = Path(self._tmp.name).resolve()
        self.error: str | None = None
        try:
            self._build()
        except (subprocess.CalledProcessError, OSError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            self.error = f"could not generate test files: {exc} {detail.decode(errors='replace')}"

    def close(self) -> None:
        self._tmp.cleanup()

    def _build(self) -> None:
        sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        for name, codec in _CODECS.items():
            ffmpeg_run(*sine, *codec, str(self.dir / f"fresh.{name}"))
        ffmpeg_run(
            "-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1",
            str(self.dir / "cover.jpg"),
        )
        (self.dir / "cover.png").write_bytes(make_png())
        (self.dir / "other.png").write_bytes(make_png(16, 8))
        (self.dir / "big.png").write_bytes(make_png(300, 300, noise=True))

    def path(self, name: str) -> Path:
        return self.dir / name

    def read(self, name: str) -> bytes:
        return (self.dir / name).read_bytes()


def audio_md5(path: Path) -> str:
    """Hash of the audio packets alone, whatever tags surround them."""
    completed = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(path),
            "-map", "0:a", "-c", "copy", "-f", "md5", "-",
        ],
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("ascii").strip()


def raw_tag_count(path: Path) -> int:
    """How many tags mutagen finds in the file, of any kind."""
    import mutagen

    found = mutagen.File(str(path))
    if found is None:
        raise AssertionError(f"mutagen cannot read {path}")
    return len(found.tags) if found.tags is not None else 0


def damage_then_fail(self: object, *args: object, **kwargs: object) -> None:
    """Stand-in for a mutagen ``save``: ruins the file being written, then fails."""
    filename = getattr(self, "filename")
    with open(filename, "r+b") as handle:
        handle.truncate(10)
    raise OSError("disk went away")


def make_jpeg(width: int = 32, height: int = 24) -> bytes:
    """A small valid JPEG made in memory with Pillow."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 60)).save(buffer, "JPEG")
    return buffer.getvalue()

"""Shared helpers for the video and audio tests.

Clips are generated with ffmpeg's ``lavfi`` sources when a test class starts,
so no media file is stored in the repository. ``make_fake_tool`` writes a small
launcher that behaves like ffmpeg or ffprobe from a JSON description, which
lets tests control exit codes, stderr and progress output without patching the
process machinery.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

_FAKE_SCRIPT = r'''
import json, os, re, sys

spec_path = sys.argv[1]
args = sys.argv[2:]
with open(spec_path, encoding="utf-8") as handle:
    spec = json.load(handle)

with open(spec_path + ".argv", "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

if "-version" in args:
    sys.stdout.write(spec.get("version_text", "") )
    sys.exit(spec.get("version_code", 0))
if "-encoders" in args:
    sys.stdout.write(spec.get("encoders_text", ""))
    sys.exit(0)

for arg in args:
    name = os.path.basename(arg)
    if name.startswith("jzr-tmp-"):
        with open(arg, "wb") as handle:
            handle.write(spec.get("output_bytes", "partial").encode("ascii"))
passlog = None
if "-passlogfile" in args:
    passlog = args[args.index("-passlogfile") + 1]
    with open(passlog + "-0.log", "w", encoding="ascii") as handle:
        handle.write("log")

sys.stdout.write(spec.get("stdout", ""))
sys.stdout.flush()
sys.stderr.write(spec.get("stderr", ""))
sys.stderr.flush()
sys.exit(spec.get("code", 0))
'''


def make_fake_tool(folder: Path, name: str, **spec: object) -> str:
    """Create an executable stand-in for ffmpeg or ffprobe and return its path.

    ``spec`` keys: ``code``, ``stdout``, ``stderr``, ``version_text``,
    ``encoders_text``, ``output_bytes``. Every call's arguments are appended
    to ``<tool>.json.argv`` (see ``recorded_calls``).
    """
    script = folder / f"{name}_fake.py"
    script.write_text(_FAKE_SCRIPT, encoding="utf-8")
    spec_file = folder / f"{name}.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    if os.name == "nt":
        launcher = folder / f"{name}.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{script}" "{spec_file}" %*\r\n', encoding="ascii"
        )
    else:
        launcher = folder / name
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{spec_file}" "$@"\n',
            encoding="ascii",
        )
        launcher.chmod(0o755)
    return str(launcher)


def tool_not_found_error() -> type[Exception]:
    """The error class for "no ffmpeg anywhere", wherever the code defines it."""
    from jzr_tools.core import ffmpeg, media

    for module in (ffmpeg, media):
        found = getattr(module, "ToolNotFoundError", None)
        if found is not None:
            return found
    raise AssertionError("ToolNotFoundError is defined neither in core.ffmpeg nor in core.media")


def recorded_calls(tool_path: str) -> list[list[str]]:
    """Return the argument lists a fake tool was started with, oldest first."""
    folder = Path(tool_path).parent
    stem = Path(tool_path).stem
    log = folder / f"{stem}.json.argv"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def ffmpeg_run(*args: str) -> None:
    """Run the real ffmpeg quietly; raises CalledProcessError on failure."""
    subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


class Clips:
    """A temporary folder with short generated clips, built on first use."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-clips-")
        self.dir = Path(self._tmp.name).resolve()
        self.error: str | None = None
        try:
            self._build()
        except (subprocess.CalledProcessError, OSError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            self.error = f"could not generate test clips: {exc} {detail.decode(errors='replace')}"

    def close(self) -> None:
        self._tmp.cleanup()

    def _build(self) -> None:
        d = self.dir
        sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        ffmpeg_run(
            "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
            *sine,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(d / "clip.mp4"),
        )
        ffmpeg_run("-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1", str(d / "cover.jpg"))
        ffmpeg_run(
            *sine, "-i", str(d / "cover.jpg"),
            "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "copy",
            "-id3v2_version", "3",
            "-metadata", "title=Song", "-metadata", "artist=Band",
            "-metadata", "album=Disc", "-metadata", "date=2020",
            "-metadata", "track=3", "-disposition:v", "attached_pic",
            str(d / "tagged.mp3"),
        )
        ffmpeg_run(
            *sine, "-c:a", "libopus",
            "-metadata", "title=Opus Song", "-metadata", "artist=Opus Band",
            str(d / "tagged.opus"),
        )
        ffmpeg_run(*sine, "-c:a", "flac", str(d / "plain.flac"))
        ffmpeg_run(
            "-display_rotation", "90", "-i", str(d / "clip.mp4"), "-c", "copy",
            str(d / "rotated.mp4"),
        )
        (d / "notes.txt").write_text("just some words, not media\n", encoding="utf-8")

    def path(self, name: str) -> Path:
        return self.dir / name


def mp4_tool_names() -> list[str]:
    """File names that are awkward for command lines."""
    return ["with space.mp4", "100% real.mp4", "-leading dash.mp4", "видео.mp4"]

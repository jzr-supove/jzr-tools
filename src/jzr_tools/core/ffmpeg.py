"""Find and run ffmpeg and ffprobe.

This is the only module that starts them. Every run goes through ``run``,
which writes under a temporary name beside the target and renames it on
success, so a failed or interrupted run never leaves a partial file or
damages an existing one.

Paths are always passed absolute: ffmpeg reads an argument that starts with
``-`` as an option, and an absolute path never does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading

from jzr_tools.core.media import (
    MediaError,
    MediaInfo,
    MissingToolError,
    ToolNotFoundError,
    parse_probe,
)

__all__ = [
    "OUTPUT",
    "PASSLOG",
    "Job",
    "MediaError",
    "MissingToolError",
    "Progress",
    "Tool",
    "ToolNotFoundError",
    "Tools",
    "command_lines",
    "encoders",
    "locate",
    "locate_tool",
    "probe",
    "run",
    "version",
]

# Placeholders inside a job's steps, replaced when the job runs.
OUTPUT = "<output>"
PASSLOG = "<passlog>"

_COMMON_FLAGS = (
    "-hide_banner",
    "-nostdin",
    "-loglevel",
    "error",
    "-nostats",
    "-progress",
    "pipe:1",
    "-y",
)

_ENVIRONMENT = {"ffmpeg": "JZR_FFMPEG", "ffprobe": "JZR_FFPROBE"}


@dataclass(frozen=True)
class Tool:
    path: str
    # Where it was found: "environment", "jzr" (its own copy) or "PATH".
    origin: str


@dataclass(frozen=True)
class Tools:
    ffmpeg: Tool
    ffprobe: Tool


@dataclass(frozen=True)
class Job:
    """One output made by one or more ffmpeg runs (two passes are one job)."""

    steps: tuple[tuple[str, ...], ...]
    target: Path
    # Of the output, in seconds, so progress can be given as a fraction.
    duration: float | None = None


@dataclass(frozen=True)
class Progress:
    step: int
    steps: int
    seconds: float
    fraction: float | None
    speed: float | None


def locate_tool(name: str, environ: Mapping[str, str] | None = None) -> Tool:
    """Find ``ffmpeg`` or ``ffprobe``: the environment variable, then the copy
    ``jzr ffmpeg install`` made, then PATH."""
    from jzr_tools.core import ffmpeg_install

    environ = os.environ if environ is None else environ
    variable = _ENVIRONMENT[name]
    configured = environ.get(variable)
    if configured:
        if not os.path.isfile(configured):
            raise MissingToolError(f"{variable} is set to {configured}, which is not a file")
        return Tool(configured, "environment")

    own = ffmpeg_install.tool_path(name, environ)
    if own.is_file():
        return Tool(str(own), "jzr")

    found = shutil.which(name)
    if found is None:
        raise ToolNotFoundError(f"{name} was not found. Run: jzr ffmpeg install")
    return Tool(found, "PATH")


def locate(environ: Mapping[str, str] | None = None) -> Tools:
    """Find both tools, or raise ``MissingToolError`` for the first missing."""
    return Tools(locate_tool("ffmpeg", environ), locate_tool("ffprobe", environ))


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def probe(path: Path, tool: Tool) -> MediaInfo:
    """Describe one file with a single ffprobe run."""
    command = [
        tool.path,
        "-v",
        "error",
        "-of",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        os.path.abspath(path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        raise MediaError("not a readable media file")
    try:
        data = json.loads(_decode(completed.stdout))
    except ValueError as error:
        raise MediaError("not a readable media file") from error
    if not isinstance(data, dict):
        raise MediaError("not a readable media file")
    return parse_probe(data, Path(path))


def version(tool: Tool) -> str:
    """Return the version ffmpeg or ffprobe reports, or ``unknown``."""
    try:
        completed = subprocess.run(
            [tool.path, "-version"], capture_output=True, check=False
        )
    except OSError:
        return "unknown"
    first = _decode(completed.stdout).splitlines()[:1]
    words = first[0].split() if first else []
    if len(words) >= 3 and words[1] == "version":
        return words[2]
    return "unknown"


def encoders(tool: Tool) -> frozenset[str]:
    """Return the names of the encoders this ffmpeg build has."""
    completed = subprocess.run(
        [tool.path, "-hide_banner", "-encoders"], capture_output=True, check=False
    )
    names: set[str] = set()
    listing = False
    for line in _decode(completed.stdout).splitlines():
        if line.strip() == "------":
            listing = True
            continue
        if listing:
            fields = line.split()
            if len(fields) >= 2:
                names.add(fields[1])
    return frozenset(names)


def _fill(step: Sequence[str], output: str, passlog: str) -> list[str]:
    return [output if arg == OUTPUT else passlog if arg == PASSLOG else arg for arg in step]


def command_lines(job: Job, tools: Tools) -> list[list[str]]:
    """Return the commands ``run`` would start, naming the real target."""
    target = os.path.abspath(job.target)
    passlog = os.path.join(tempfile.gettempdir(), "jzr-pass", "pass")
    return [
        [tools.ffmpeg.path, *_COMMON_FLAGS, *_fill(step, target, passlog)]
        for step in job.steps
    ]


_PREFIX = re.compile(r"^\[[^\]]* @ [0-9a-fA-Fx]+\]\s*")
_UNKNOWN_ENCODER = re.compile(r"Unknown encoder '([^']+)'")
# Lines ffmpeg prints after the real reason, which say nothing new.
_FOLLOW_UPS = re.compile(
    r"^(Error opening (input|output) files?\b"
    r"|Error selecting an encoder"
    r"|Could not write header"
    r"|Conversion failed!)"
)


def _failure(stderr: str, returncode: int) -> str:
    """Pick the line of ffmpeg's error output that explains the failure."""
    unknown = _UNKNOWN_ENCODER.search(stderr)
    if unknown is not None:
        return f"this ffmpeg build has no {unknown.group(1)} encoder"

    lines = [_PREFIX.sub("", line).strip() for line in stderr.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return f"ffmpeg exited with code {returncode}"
    useful = [line for line in lines if _FOLLOW_UPS.match(line) is None]
    return (useful or lines)[-1]


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _run_step(
    argv: list[str],
    *,
    step: int,
    steps: int,
    duration: float | None,
    on_progress: Callable[[Progress], None] | None,
) -> None:
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise MediaError(f"could not start {argv[0]}: {error.strerror or error}") from error
    errors: list[bytes | str] = []
    # Read stderr on its own thread so a chatty ffmpeg never blocks on a full
    # pipe while progress is being read from stdout.
    reader = threading.Thread(
        target=lambda: errors.append(process.stderr.read()) if process.stderr else None,
        daemon=True,
    )
    reader.start()
    try:
        seconds = 0.0
        speed: float | None = None
        assert process.stdout is not None
        for raw in process.stdout:
            key, _, value = _decode(raw).strip().partition("=")
            if key == "out_time_us":
                microseconds = _number(value)
                if microseconds is not None and microseconds >= 0:
                    seconds = microseconds / 1_000_000
            elif key == "speed":
                speed = _number(value.rstrip("x"))
            elif key == "progress" and on_progress is not None:
                fraction = None
                if duration:
                    fraction = min(1.0, seconds / duration)
                on_progress(Progress(step, steps, seconds, fraction, speed))
        returncode = process.wait()
    except BaseException:
        _stop(process)
        raise
    finally:
        reader.join(timeout=5)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()

    if returncode != 0:
        raise MediaError(_failure("".join(_decode(part) for part in errors), returncode))


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run(
    job: Job,
    tools: Tools,
    *,
    on_progress: Callable[[Progress], None] | None = None,
) -> None:
    """Run ``job`` and move its output onto ``job.target``.

    Raises ``MediaError`` with a one-line reason when ffmpeg fails. On any
    failure, Ctrl+C included, the temporary output and the pass logs are
    removed and an existing target is left as it was.
    """
    target = Path(os.path.abspath(job.target))
    temporary = target.parent / f"jzr-tmp-{secrets.token_hex(8)}{target.suffix}"
    pass_dir = tempfile.mkdtemp(prefix="jzr-pass-")
    passlog = os.path.join(pass_dir, "pass")
    try:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            for number, step in enumerate(job.steps, start=1):
                _run_step(
                    [tools.ffmpeg.path, *_COMMON_FLAGS, *_fill(step, str(temporary), passlog)],
                    step=number,
                    steps=len(job.steps),
                    duration=job.duration,
                    on_progress=on_progress,
                )
            if not temporary.exists():
                raise MediaError(f"ffmpeg reported success but wrote no {target.name}")
            os.replace(temporary, target)
        except OSError as error:
            raise MediaError(f"could not write {target.name}: {error.strerror or error}") from error
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(pass_dir, ignore_errors=True)

"""Click pieces shared by ``jzr video`` and ``jzr audio``.

The media modules are imported inside the commands, so listing a group's
commands or asking for help starts nothing and imports little.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from jzr_tools.core.ffmpeg import Progress


def info_command(*, tags: bool, help_text: str) -> click.Command:
    """Build the ``info`` command; ``tags`` adds the tag rows (audio)."""

    @click.command(name="info", help=help_text)
    @click.argument(
        "files",
        nargs=-1,
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    @click.option("--json", "as_json", is_flag=True, help="Print one JSON array.")
    def command(files: tuple[Path, ...], as_json: bool) -> None:
        from jzr_tools.commands._batch import require_tools
        from jzr_tools.core import ffmpeg, media

        (tool,) = require_tools("ffprobe")

        documents: list[dict[str, object]] = []
        failed = False
        printed = False
        for path in files:
            try:
                info = ffmpeg.probe(path, tool)
            except media.MediaError as error:
                click.echo(f"fail {path}: {error}", err=True)
                failed = True
                continue

            if as_json:
                documents.append(media.info_as_dict(info))
                continue

            rows = media.info_rows(info, tags=tags)
            width = max((len(label) for label, _ in rows), default=0)
            if printed:
                click.echo()
            click.echo(str(path))
            for label, value in rows:
                click.echo(f"  {label.ljust(width)}  {value}")
            printed = True

        if as_json:
            click.echo(json.dumps(documents, indent=2, ensure_ascii=False))
        if failed:
            raise click.exceptions.Exit(1)

    return command


def formats_command(*, targets: tuple[str, ...], video: bool, help_text: str) -> click.Command:
    """Build the ``formats`` command; ``video`` adds the video encoders."""

    @click.command(name="formats", help=help_text)
    def command() -> None:
        from jzr_tools.core import ffmpeg, media

        click.echo("targets: " + " ".join(targets))

        found: dict[str, ffmpeg.Tool] = {}
        for name in ("ffmpeg", "ffprobe"):
            try:
                tool = ffmpeg.locate_tool(name)
            except media.MissingToolError as error:
                click.echo(f"{name}: not found")
                click.echo(str(error), err=True)
                continue
            found[name] = tool
            click.echo(f"{name}: {tool.path} ({ffmpeg.version(tool)}, from {tool.origin})")

        if "ffmpeg" not in found:
            return
        available = ffmpeg.encoders(found["ffmpeg"])
        tables = (media.VIDEO_ENCODERS, media.AUDIO_ENCODERS) if video else (media.AUDIO_ENCODERS,)
        rows = [(codec, encoder) for table in tables for codec, encoder in table.items()]
        codec_width = max(len(codec) for codec, _ in rows)
        encoder_width = max(len(encoder) for _, encoder in rows)
        click.echo("encoders:")
        for codec, encoder in rows:
            state = "yes" if encoder in available else "missing"
            click.echo(f"  {codec.ljust(codec_width)}  {encoder.ljust(encoder_width)}  {state}")

    return command


def resolve_source(path: Path, source_extension: str | None) -> Path:
    """Return the file a listed source refers to.

    With a known source format the extension may be left off (``song`` for
    ``song.wav``), and a file with another extension is a usage error.
    """
    if source_extension is None:
        if path.is_file():
            return path
        raise click.UsageError(f"File not found: {path}")

    if path.is_file() and path.suffix.lstrip(".").lower() == source_extension:
        return path
    # Append rather than replace the suffix, so dotted names stay intact.
    candidate = path.with_name(f"{path.name}.{source_extension}")
    if candidate.is_file():
        return candidate
    if path.is_file():
        raise click.UsageError(f"{path} is not a .{source_extension} file.")
    raise click.UsageError(f"File not found: {path} (also tried {candidate})")


class ProgressLine:
    """One updating line on stderr while ffmpeg works, on a terminal only."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.enabled = click.get_text_stream("stderr").isatty()
        self._width = 0

    def __call__(self, progress: "Progress") -> None:
        from jzr_tools.core.media import format_time

        parts: list[str] = []
        if progress.fraction is not None:
            parts.append(f"{progress.fraction:4.0%}")
        if progress.speed:
            parts.append(f"{progress.speed:.1f}x")
            if progress.fraction:
                remaining = progress.seconds * (1 / progress.fraction - 1) / progress.speed
                parts.append(f"{format_time(remaining)} left")
        else:
            parts.append(format_time(progress.seconds))
        status = "  ".join(parts)

        # A line wider than the terminal wraps, and `\r` then rewrites only
        # its last row; shorten the name from the left instead.
        room = shutil.get_terminal_size().columns - 1 - len(status) - 2
        label = self.label
        if len(label) > room:
            label = "..." + label[-(room - 3):] if room > 6 else ""
        text = f"{label}  {status}" if label else status
        self._width = max(self._width, len(text))
        click.echo("\r" + text.ljust(self._width), err=True, nl=False)

    def clear(self) -> None:
        """Remove the line so the result line starts on a clean row."""
        if self._width:
            click.echo("\r" + " " * self._width + "\r", err=True, nl=False)
            self._width = 0

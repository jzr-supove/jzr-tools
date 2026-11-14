"""Click pieces shared by ``jzr video`` and ``jzr audio``.

The media modules are imported inside the commands, so listing a group's
commands or asking for help starts nothing and imports little.
"""

from __future__ import annotations

import json
from pathlib import Path

import click


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
        from jzr_tools.core import ffmpeg, media

        try:
            tool = ffmpeg.locate_tool("ffprobe")
        except media.MissingToolError as error:
            click.echo(str(error), err=True)
            raise click.exceptions.Exit(1) from error

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

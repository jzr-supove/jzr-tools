"""Click pieces shared by the commands that turn files into other files.

The options for choosing sources and outputs, the per-file result lines and
the closing summary, so `jzr image convert` and the media commands behave the
same way.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shlex
import sys
from typing import TYPE_CHECKING, Callable, Sequence

import click

from jzr_tools.core.batch import ConversionResult

if TYPE_CHECKING:
    from jzr_tools.core.ffmpeg import Tool

Decorator = Callable[[Callable[..., None]], Callable[..., None]]


def batch_options(*extra: Decorator) -> Decorator:
    """Attach SOURCES and the source/output options, with ``extra`` before
    ``--overwrite`` and ``--dry-run``."""
    decorators = [
        click.argument("sources", nargs=-1, type=click.Path(path_type=Path)),
        click.option(
            "-d",
            "--dir",
            "directory",
            type=click.Path(exists=True, file_okay=False, path_type=Path),
            default=None,
            help="Directory to scan when no SOURCES are listed.  [default: .]",
        ),
        click.option(
            "-p",
            "--pattern",
            default=None,
            help="Regex filter applied to candidate filenames.",
        ),
        click.option(
            "-r",
            "--recursive",
            is_flag=True,
            help="Recurse into subdirectories while scanning.",
        ),
        click.option(
            "-o",
            "--output",
            type=click.Path(path_type=Path),
            metavar="FILE",
            default=None,
            help="Write the one listed SOURCE to this file.",
        ),
        click.option(
            "--outdir",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Write outputs here instead of beside each source.",
        ),
        *extra,
        click.option("--overwrite", is_flag=True, help="Replace existing output files."),
        click.option(
            "--dry-run", is_flag=True, help="Show what would be converted; write nothing."
        ),
    ]

    def apply(function: Callable[..., None]) -> Callable[..., None]:
        # Decorators apply bottom-up, so reverse to keep this order in --help.
        for decorator in reversed(decorators):
            function = decorator(function)
        return function

    return apply


def compile_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """Compile ``--pattern``, turning a bad regex into a usage error."""
    if pattern is None:
        return None
    try:
        return re.compile(pattern)
    except re.error as error:
        raise click.BadParameter(
            f"{pattern!r} is not a valid regular expression ({error}).",
            param_hint="'--pattern'",
        ) from error


def check_output(
    output: Path | None, *, sources: Sequence[Path], outdir: Path | None
) -> None:
    """Reject ``--output`` unless it names one file for one listed source."""
    if output is None:
        return
    if outdir is not None:
        raise click.UsageError("--output and --outdir cannot be used together.")
    if len(sources) != 1:
        raise click.UsageError(
            "--output names one file, so list exactly one SOURCE; "
            "use --outdir for several."
        )
    if output.is_dir():
        raise click.UsageError(f"--output {output} is a directory; use --outdir for that.")


def report(result: ConversionResult) -> None:
    """Print the line for one file: stdout for work done, stderr otherwise."""
    if result.status == "planned":
        if result.command:
            click.echo(shlex.join(result.command))
        else:
            click.echo(f"{result.source} -> {result.target}")
        if result.detail:
            click.echo(f"note: {result.detail}", err=True)
    elif result.status == "converted":
        note = f" ({result.detail})" if result.detail else ""
        click.echo(f"{result.source} -> {result.target}{note}")
    elif result.status == "skipped":
        click.echo(f"skip {result.source}: {result.detail}", err=True)
    else:
        click.echo(f"fail {result.source}: {result.detail}", err=True)


def summarize(label: str, counts: Counter[str]) -> None:
    """Print ``label: 2 converted, 1 failed`` and exit 1 if anything failed."""
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    click.echo(f"{label}: {summary}")
    if counts["failed"]:
        raise click.exceptions.Exit(1)


def _interactive() -> bool:
    """Whether someone is there to answer a question: stdin and stderr are terminals."""
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def require_tools(*names: str) -> tuple[Tool, ...]:
    """Find ffmpeg tools for a command, offering to install them when missing.

    The offer is made once per run and only when someone can answer it;
    otherwise, or after a "no", the reason is printed and the command exits 1.
    """
    from jzr_tools.core import ffmpeg, media

    found = []
    for name in names:
        try:
            found.append(ffmpeg.locate_tool(name))
            continue
        except media.ToolNotFoundError as error:
            missing = error
        except media.MissingToolError as error:
            click.echo(str(error), err=True)
            raise click.exceptions.Exit(1) from error

        meta = click.get_current_context().find_root().meta
        if _interactive() and not meta.get("jzr.ffmpeg_offered"):
            meta["jzr.ffmpeg_offered"] = True
            from jzr_tools.commands.ffmpeg import install_ffmpeg

            if install_ffmpeg(assume_yes=False, offer=True):
                try:
                    found.append(ffmpeg.locate_tool(name))
                    continue
                except media.MissingToolError as error:
                    missing = error
        click.echo(str(missing), err=True)
        raise click.exceptions.Exit(1) from missing
    return tuple(found)

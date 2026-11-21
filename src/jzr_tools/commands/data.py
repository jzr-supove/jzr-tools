"""`jzr data`: format and check structured data."""

from __future__ import annotations

from pathlib import Path

import click

_STDIN = "<stdin>"
_BOM = "﻿"


@click.group(short_help="Format and check JSON data.")
def app() -> None:
    """Format and check JSON data."""


class _Failed(Exception):
    """One input failed; the message is already user-facing."""


@app.command("json")
@click.argument("files", nargs=-1)
@click.option(
    "--indent",
    type=click.IntRange(0, 8),
    default=None,
    metavar="N",
    help="Indent with N spaces, 0 to 8 (default 2).",
)
@click.option("--tab", is_flag=True, help="Indent with tabs.")
@click.option("--minify", is_flag=True, help="Leave out all whitespace.")
@click.option("--sort-keys", is_flag=True, help="Sort the members of every object by key.")
@click.option("--check", is_flag=True, help="Only check that the input is valid; print nothing.")
@click.option("-w", "--write", is_flag=True, help="Rewrite the FILES in place.")
@click.option("--python", is_flag=True, help="Read a Python literal (a repr) and print it as JSON.")
def json_command(
    files: tuple[str, ...],
    indent: int | None,
    tab: bool,
    minify: bool,
    sort_keys: bool,
    check: bool,
    write: bool,
    python: bool,
) -> None:
    """Pretty-print, minify or check JSON from FILES or stdin.

    Only whitespace changes: numbers, string escapes and duplicate keys stay
    exactly as written. With several FILES each is printed in turn; a file
    that fails is named on stderr and the others still run. Use - for stdin.
    """
    if minify and (tab or indent is not None):
        raise click.UsageError(f"{'--tab' if tab else '--indent'} and --minify do not combine.")
    if tab and indent is not None:
        raise click.UsageError("--indent and --tab do not combine; give one of them.")
    if check and write:
        raise click.UsageError("--check and --write do not combine.")
    if python and write:
        raise click.UsageError("--python and --write do not combine: the files are not JSON.")
    if write and (not files or "-" in files):
        raise click.UsageError("--write needs FILES to rewrite, not stdin.")

    from jzr_tools.core import jsonfmt

    style = jsonfmt.Style(
        indent=None if minify else "\t" if tab else 2 if indent is None else indent,
        sort_keys=sort_keys,
    )
    _lf_output()
    failed = False
    for name in files or ("-",):
        try:
            text = _read(name)
            if write:
                _rewrite(name, text, style)
                continue
            if python:
                result = jsonfmt.python_to_json(text, style)
            elif check:
                jsonfmt.check_json(text)
                continue
            else:
                result = jsonfmt.format_json(text, style)
        except (_Failed, jsonfmt.JsonError, jsonfmt.LiteralError) as error:
            failed = True
            label = _STDIN if name == "-" else name
            click.echo(f"{label}: {error}", err=True)
            continue
        if not check:
            click.echo(result)
    if failed:
        raise click.exceptions.Exit(1)


def _read(name: str) -> str:
    if name == "-":
        data = _stdin_bytes()
    else:
        path = Path(name)
        if path.is_dir():
            raise _Failed("is a folder")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise _Failed("not found") from None
        except OSError as error:
            raise _Failed(f"cannot read: {error.strerror or error}") from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _Failed("not UTF-8 text") from None


def _rewrite(name: str, text: str, style) -> None:  # noqa: ANN001 - core.jsonfmt.Style
    """Format the file ``name`` in place, keeping its BOM, line ends and final newline."""
    from jzr_tools.core.jsonfmt import format_json

    bom = text.startswith(_BOM)
    body = text[1:] if bom else text
    new = format_json(body, style)
    if body.endswith("\n"):
        new += "\n"
    if "\r\n" in body:
        # A formatted value has no line break inside a string, so this only
        # touches the layout.
        new = new.replace("\n", "\r\n")
    if bom:
        new = _BOM + new
    if new == text:
        click.echo(f"{name}: already formatted")
        return
    _replace(Path(name), new.encode("utf-8"))
    click.echo(f"{name}: formatted")


def _replace(path: Path, data: bytes) -> None:
    """Write ``data`` through a temp name next to ``path``, then rename it over."""
    import os
    import shutil
    import tempfile

    folder = path.parent if str(path.parent) else Path(".")
    try:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{path.name}-", suffix=".tmp")
    except OSError as error:
        raise _Failed(f"cannot write: {error.strerror or error}") from None
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        try:
            shutil.copymode(path, temporary)
        except OSError:
            pass
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise _Failed(f"cannot write: {error.strerror or error}") from None


def _stdin_bytes() -> bytes:
    stream = click.get_binary_stream("stdin")
    try:
        terminal = stream.isatty()
    except (AttributeError, ValueError):
        terminal = False
    if terminal:
        click.echo(
            "Paste JSON, then finish with Ctrl+Z and Enter on Windows or Ctrl+D on macOS/Linux.",
            err=True,
        )
    return stream.read()


def _lf_output() -> None:
    """End lines with LF on Windows too, so redirected JSON has no CR."""
    import sys

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(newline="\n")
        except (OSError, ValueError):
            pass

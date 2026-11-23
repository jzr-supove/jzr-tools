"""`jzr data`: format, check and convert structured data."""

from __future__ import annotations

from pathlib import Path

import click

_STDIN = "<stdin>"
_BOM = "﻿"


@click.group(short_help="Format, check and convert structured data.")
def app() -> None:
    """Format, check and convert structured data."""


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


_FORMAT_CHOICE = click.Choice(
    ["json", "jsonl", "yaml", "csv", "toml", "xlsx"], case_sensitive=False
)


@app.command("convert")
@click.argument("file", required=False)
@click.option("-f", "--from", "source", type=_FORMAT_CHOICE, help="Format of the input; needed for stdin.")
@click.option(
    "-t",
    "--to",
    "target",
    type=_FORMAT_CHOICE,
    help="Format to write; or give -o a file name with an extension.",
)
@click.option("-o", "--output", metavar="FILE", help="Write to this file instead of stdout.")
@click.option(
    "--delimiter",
    type=click.Choice([",", ";", "tab", "|"]),
    help="CSV delimiter: , ; tab or | (default , and tab for .tsv).",
)
@click.option(
    "--no-header", is_flag=True, help="CSV and Excel: the first row is data; rows become arrays."
)
@click.option(
    "--header-row",
    type=click.IntRange(min=1),
    metavar="N",
    help="Excel: the row with the column names (default: the first row with data).",
)
@click.option(
    "--types", is_flag=True, help="CSV: read numbers, true/false and empty cells instead of text."
)
@click.option(
    "--nest", is_flag=True, help="CSV and Excel: turn dotted column names into nested objects."
)
@click.option(
    "--sheet",
    "sheets",
    multiple=True,
    metavar="NAME",
    help="Excel: read this sheet; repeat for more (default: every visible sheet).",
)
@click.option(
    "--indent",
    type=click.IntRange(0, 8),
    metavar="N",
    help="JSON: indent with N spaces, 0 to 8 (default 2).",
)
@click.option("--minify", is_flag=True, help="JSON: leave out all whitespace.")
@click.option("--overwrite", is_flag=True, help="Replace an existing output file.")
def convert_command(
    file: str | None,
    source: str | None,
    target: str | None,
    output: str | None,
    delimiter: str | None,
    no_header: bool,
    header_row: int | None,
    types: bool,
    nest: bool,
    sheets: tuple[str, ...],
    indent: int | None,
    minify: bool,
    overwrite: bool,
) -> None:
    """Convert data between JSON, YAML, CSV, TOML and Excel.

    The formats come from the file names, or from --from and --to. Text goes
    to stdout unless -o names a file; Excel output goes to -o, or to
    <name>.xlsx beside FILE. Anything lost on the way is named in a note on
    stderr.
    """
    if indent is not None and minify:
        raise click.UsageError("--indent and --minify do not combine.")
    if header_row is not None and no_header:
        raise click.UsageError("--header-row and --no-header do not combine.")

    from jzr_tools.core import dataconv

    from_stdin = file is None or file == "-"
    if not from_stdin:
        path = Path(file)
        if path.is_dir():
            raise click.UsageError(f"{file} is a folder, not a file")
        if not path.exists():
            raise click.UsageError(f"File not found: {file}")
    label = _STDIN if from_stdin else file

    try:
        if source is None:
            if from_stdin:
                raise click.UsageError("stdin has no name; give --from")
            source = dataconv.format_of(file)
            if source is None:
                raise click.UsageError(f"cannot tell the format of {file}; give --from")
        if target is None and output is not None:
            target = dataconv.format_of(output)
    except dataconv.ConvertError as error:
        raise click.UsageError(str(error)) from None
    if target is None:
        raise click.UsageError("give --to FORMAT, or -o with a known extension")
    source, target = source.lower(), target.lower()

    if sheets and source != "xlsx":
        raise click.UsageError("--sheet only applies to xlsx input.")
    if header_row is not None and source != "xlsx":
        raise click.UsageError("--header-row only applies to xlsx input.")
    if types and source != "csv":
        raise click.UsageError("--types only applies to csv input.")
    if nest and source not in ("csv", "xlsx"):
        raise click.UsageError("--nest only applies to csv and xlsx input.")
    if no_header and source not in ("csv", "xlsx"):
        raise click.UsageError("--no-header only applies to csv and xlsx input.")
    if delimiter is not None and "csv" not in (source, target):
        raise click.UsageError("--delimiter only applies to csv.")
    if (indent is not None or minify) and target != "json":
        raise click.UsageError("--indent and --minify only apply to json output.")

    destination: str | None = output
    if destination is None and target == "xlsx":
        if from_stdin:
            raise click.UsageError("xlsx output from stdin needs -o FILE")
        destination = str(Path(file).with_suffix(".xlsx"))
    if destination is not None:
        out_path = Path(destination)
        if not from_stdin and _same_path(out_path, Path(file)):
            raise click.UsageError(f"the output would replace {file}; give another -o")
        if out_path.is_dir():
            raise click.UsageError(f"{destination} is a folder; give a file name to -o")
        if out_path.exists() and not overwrite:
            raise click.UsageError(f"{destination} exists; add --overwrite to replace it")

    chosen = dataconv.DELIMITERS[delimiter] if delimiter is not None else None
    read = dataconv.ReadOptions(
        delimiter=chosen or dataconv.default_delimiter(None if from_stdin else file),
        header=not no_header,
        header_row=header_row,
        types=types,
        nest=nest,
        sheets=sheets,
    )
    write = dataconv.WriteOptions(
        indent=2 if indent is None else indent,
        minify=minify,
        delimiter=chosen or dataconv.default_delimiter(destination),
    )

    try:
        raw = _convert_input(None if from_stdin else file)
        result = dataconv.convert(raw, source=source, target=target, read=read, write=write)
        if destination is not None:
            _write_new(Path(destination), result.data)
    except (_Failed, dataconv.ConvertError) as error:
        click.echo(f"{label}: {error}", err=True)
        raise click.exceptions.Exit(1) from None

    if destination is None:
        stream = click.get_binary_stream("stdout")
        stream.write(result.data)
        stream.flush()
    else:
        summary = f" ({result.summary})" if result.summary else ""
        click.echo(f"wrote {destination}{summary}", err=True)
    for note in result.notes:
        click.echo(f"note: {note}", err=True)


def _same_path(first: Path, second: Path) -> bool:
    import os

    try:
        if first.exists() and second.exists():
            return os.path.samefile(first, second)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def _convert_input(name: str | None) -> bytes:
    if name is None:
        stream = click.get_binary_stream("stdin")
        try:
            terminal = stream.isatty()
        except (AttributeError, ValueError):
            terminal = False
        if terminal:
            click.echo(
                "Paste the data, then finish with Ctrl+Z and Enter on Windows "
                "or Ctrl+D on macOS/Linux.",
                err=True,
            )
        return stream.read()
    try:
        return Path(name).read_bytes()
    except OSError as error:
        raise _Failed(f"cannot read: {error.strerror or error}") from None


def _write_new(path: Path, data: bytes) -> None:
    """Write ``data`` through a temporary name beside ``path``, then rename it."""
    import os
    import tempfile

    folder = path.parent if str(path.parent) else Path(".")
    try:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{path.name}-", suffix=".tmp")
    except OSError as error:
        raise _Failed(f"cannot write {path}: {error.strerror or error}") from None
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise _Failed(f"cannot write {path}: {error.strerror or error}") from None

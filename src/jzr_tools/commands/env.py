"""`jzr env`: make `.env.example` files and compare the keys of `.env` files."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import click

_STDIN = "<stdin>"


@click.group(short_help="Make .env.example files and compare .env keys.")
def app() -> None:
    """Make .env.example files and compare .env keys.

    Values are never shown unless you ask for them (`compare --values`).
    """


class _Failed(Exception):
    """A file could not be used; the message is a whole user-facing line."""


@app.command("example")
@click.argument("file", default=".env")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the example to this file instead of printing it.",
)
@click.option("--overwrite", is_flag=True, help="Replace the -o file if it exists.")
@click.option("--copy", "-c", is_flag=True, help="Copy the result to the clipboard too.")
def example_command(file: str, output: Path | None, overwrite: bool, copy: bool) -> None:
    """Print the keys of FILE (default .env) as KEY= lines for a .env.example.

    Values and comments are left out; each key is listed once, in the order
    it first appears. Use - for stdin.
    """
    if output is not None and file != "-" and _same_file(Path(file), output):
        raise click.UsageError("-o is the input file; that would replace your values.")

    from jzr_tools.core.envfile import example_text

    try:
        entries = _load(file)
    except _Failed as error:
        _fail(str(error))
    text = example_text(entries)
    _lf_output()
    if output is not None:
        if output.exists() and not overwrite:
            _fail(f"{output} exists; add --overwrite to replace it.")
        _write(output, text.encode("utf-8"))
        count = len(text.splitlines())
        click.echo(f"Wrote {count} {'key' if count == 1 else 'keys'} to {output}")
    else:
        click.echo(text, nl=False)
    if copy:
        from jzr_tools.core.system import copy_to_clipboard

        try:
            copy_to_clipboard(text)
        except RuntimeError as error:
            _fail(str(error))


@app.command("compare")
@click.argument("a", default=".env.example")
@click.argument("b", default=".env")
@click.option("--values", is_flag=True, help="Also show keys whose values differ, with both values.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def compare_command(a: str, b: str, values: bool, as_json: bool) -> None:
    """Show the keys only in A (default .env.example) or only in B (default .env).

    Exits 1 when the key sets differ, so CI can check that .env has every key
    of .env.example, and 2 when a file cannot be read. Values are shown only
    with --values. Use - for stdin (one side).
    """
    if a == "-" and b == "-":
        raise click.UsageError("Only one of A and B can be - (stdin).")

    from jzr_tools.core.envfile import compare, last_values

    loaded = []
    errors = []
    for name in (a, b):
        try:
            loaded.append(_load(name))
        except _Failed as error:
            errors.append(str(error))
    if errors:
        for line in errors:
            click.echo(line, err=True)
        raise click.exceptions.Exit(2)

    entries_a, entries_b = loaded
    result = compare(entries_a, entries_b)
    label_a, label_b = _label(a), _label(b)
    same = not result.only_a and not result.only_b
    changes = None
    if values:
        values_a, values_b = last_values(entries_a), last_values(entries_b)
        changes = [(key, values_a[key], values_b[key]) for key in result.changed]

    _lf_output()
    if as_json:
        import json

        document = {
            "a": label_a,
            "b": label_b,
            "same": same,
            "only_a": result.only_a,
            "only_b": result.only_b,
            "changed": None
            if changes is None
            else [{"key": key, "a": left, "b": right} for key, left, right in changes],
        }
        click.echo(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        lines: list[str] = []
        for label, keys in ((label_a, result.only_a), (label_b, result.only_b)):
            if keys:
                lines.append(f"Only in {label} ({len(keys)}):")
                lines.extend(f"  {key}" for key in keys)
        if changes:
            lines.append(f"Different values ({len(changes)}):")
            lines.extend(
                f"  {key}: {_shown(left)} -> {_shown(right)}" for key, left, right in changes
            )
        if not lines:
            count = len(last_values(entries_a))
            lines.append(
                f"Same keys in {label_a} and {label_b} ({count} {'key' if count == 1 else 'keys'})."
            )
        click.echo("\n".join(lines))
    if not same:
        raise click.exceptions.Exit(1)


def _label(name: str) -> str:
    return _STDIN if name == "-" else name


def _shown(value: str) -> str:
    return value.replace("\n", "\\n") if value else '""'


def _load(name: str):  # noqa: ANN202 - list of core.envfile.Entry
    from jzr_tools.core.envfile import EnvError, parse_env

    label = _label(name)
    if name == "-":
        data = click.get_binary_stream("stdin").read()
    else:
        path = Path(name)
        if path.is_dir():
            raise _Failed(f"{label}: is a folder")
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise _Failed(f"{label}: not found") from None
        except OSError as error:
            raise _Failed(f"{label}: cannot read: {error.strerror or error}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _Failed(f"{label}: not UTF-8 text") from None
    try:
        return parse_env(text)
    except EnvError as error:
        raise _Failed(f"{label}:{error.line}: {error.reason}") from None


def _same_file(source: Path, output: Path) -> bool:
    import os

    try:
        if source.exists() and output.exists():
            return os.path.samefile(source, output)
        return source.resolve() == output.resolve()
    except OSError:
        return False


def _write(target: Path, data: bytes) -> None:
    """Write ``data`` through a temp name in the same folder, then rename."""
    import os
    import tempfile

    folder = target.parent if str(target.parent) else Path(".")
    try:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{target.name}-", suffix=".tmp")
    except OSError as error:
        _fail(f"cannot write {target}: {error.strerror or error}")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        _fail(f"cannot write {target}: {error.strerror or error}")


def _fail(message: str) -> NoReturn:
    click.echo(message, err=True)
    raise click.exceptions.Exit(1)


def _lf_output() -> None:
    """End lines with LF on Windows too, so redirected output matches the file form."""
    import sys

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(newline="\n")
        except (OSError, ValueError):
            pass

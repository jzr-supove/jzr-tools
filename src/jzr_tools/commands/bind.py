"""``jzr bind``: save a long command under a short name and run it."""

from __future__ import annotations

import json
import shutil
from typing import NoReturn

import click

from jzr_tools.core import bindings

# Everything after the name belongs to the bound command, options included.
_PASS_THROUGH = {"ignore_unknown_options": True, "allow_interspersed_args": False}


@click.group()
def app() -> None:
    """Save your own shortcuts for long commands.

    A shortcut added with `jzr bind add NAME COMMAND...` runs as `jzr NAME`.
    """


def _fail(message: object, code: int = 1) -> NoReturn:
    click.echo(str(message), err=True)
    raise click.exceptions.Exit(code)


def _load() -> dict[str, list[str]]:
    try:
        return bindings.load()
    except bindings.BindingError as exc:
        _fail(exc)


def _built_in() -> set[str]:
    """Return the names jzr itself answers to; they always win over a binding."""
    from jzr_tools.cli import app as root

    return set(root.list_commands(click.get_current_context()))


def _run(name: str, stored: list[str], extra: tuple[str, ...]) -> NoReturn:
    """Fill in a binding's placeholders, run it and exit with its exit code."""
    try:
        arguments = bindings.fill(stored, extra)
    except bindings.MissingArguments as exc:
        _fail(f"{name} {exc}: {bindings.describe(stored)}", 2)
    except bindings.BindingError as exc:
        _fail(f"{name}: {exc}")
    try:
        code = bindings.run(arguments)
    except bindings.ProgramNotFound as exc:
        _fail(f"{name}: {exc}", 127)
    except bindings.BindingError as exc:
        _fail(f"{name}: {exc}")
    raise click.exceptions.Exit(code)


def command_for(name: str) -> click.Command | None:
    """Return a command that runs the binding ``name``, or None if there is none.

    The root group asks this for names it does not know.
    """
    stored = _load().get(name)
    if stored is None:
        return None

    @click.command(name, context_settings=_PASS_THROUGH, add_help_option=False)
    @click.argument("extra", nargs=-1, type=click.UNPROCESSED)
    def bound(extra: tuple[str, ...]) -> None:
        _run(name, stored, extra)

    return bound


@app.command(context_settings=_PASS_THROUGH)
@click.option("-f", "--force", is_flag=True, help="Replace the binding if NAME is already bound.")
@click.argument("name")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def add(force: bool, name: str, command: tuple[str, ...]) -> None:
    """Bind NAME to COMMAND, so that `jzr NAME` runs it.

    COMMAND is everything after NAME, written out or as one quoted string.
    It is run directly, without a shell: no pipes, `&&` or variables.
    Write {1}, {2}, ... where the words given when it runs belong, and {*}
    for the rest of them; without {*}, the rest go on the end.
    """
    if not bindings.is_valid_name(name):
        raise click.UsageError(
            f"'{name}' cannot be a name: start with a letter or digit, "
            "then use letters, digits, '.', '_' and '-'."
        )
    if name in _built_in():
        raise click.UsageError(f"'{name}' is a jzr command and cannot be rebound.")
    block = bindings.powershell_script_block(command)
    if block is not None:
        raise click.UsageError(
            f"PowerShell turned {block} into a script block. Quote it ('{block}'), "
            "or give the whole command as one quoted string."
        )
    try:
        words = bindings.split_command(command)
        note = bindings.arity_note(words)
    except bindings.BindingError as exc:
        raise click.UsageError(str(exc)) from exc
    if not words:
        raise click.UsageError("Give the command to bind after the name.")

    found = _load()
    replacing = name in found
    if replacing and not force:
        raise click.UsageError(
            f"'{name}' is already bound to: {bindings.describe(found[name])}\n"
            "Use --force to replace it."
        )
    found[name] = words
    bindings.save(found)

    suffix = f" ({note})" if note else ""
    click.echo(f"{'Replaced' if replacing else 'Bound'} {name}: {bindings.describe(words)}{suffix}")
    if not bindings.has_placeholder(words[0]) and shutil.which(words[0]) is None:
        click.echo(f"note: '{words[0]}' is not on PATH right now.", err=True)


@app.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Print the bindings as JSON.")
def list_(as_json: bool) -> None:
    """List the bindings."""
    found = _load()
    if as_json:
        document = {"path": str(bindings.bindings_path()), "bindings": found}
        click.echo(json.dumps(document, indent=2, ensure_ascii=False))
        return
    if not found:
        click.echo("No bindings yet. Add one with: jzr bind add NAME COMMAND...")
        return

    built_in = _built_in()
    width = max(len(name) for name in found)
    for name in sorted(found):
        line = f"{name.ljust(width)}  {bindings.describe(found[name])}"
        if name in built_in:
            line += f"  (hidden by a built-in command; use: jzr bind run {name})"
        click.echo(line)


@app.command()
@click.argument("names", nargs=-1, required=True, metavar="NAME...")
def remove(names: tuple[str, ...]) -> None:
    """Remove bindings."""
    found = _load()
    missing = [name for name in names if name not in found]
    removed = [name for name in dict.fromkeys(names) if name in found]
    if removed:
        for name in removed:
            del found[name]
        bindings.save(found)
    for name in removed:
        click.echo(f"Removed {name}")
    for name in missing:
        click.echo(f"No binding named {name}.", err=True)
    if missing:
        raise click.exceptions.Exit(1)


@app.command(context_settings=_PASS_THROUGH)
@click.argument("name")
@click.argument("arguments", nargs=-1, type=click.UNPROCESSED, metavar="[ARGS]...")
def run(name: str, arguments: tuple[str, ...]) -> None:
    """Run the binding NAME, with ARGS added to its command.

    The same as `jzr NAME`, and it also reaches a binding whose name a
    built-in command has taken.
    """
    stored = _load().get(name)
    if stored is None:
        _fail(f"No binding named {name}.")
    _run(name, stored, arguments)


@app.command()
def path() -> None:
    """Print where the bindings are stored."""
    click.echo(bindings.bindings_path())

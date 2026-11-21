"""Generators: `jzr uuid`, `jzr password` and `jzr random`.

The core module is imported inside each command, so listing commands and
reading help stay as fast as the rest of jzr.
"""

from __future__ import annotations

from typing import Sequence

import click

from jzr_tools.commands._input import read_text, stdout_is_terminal

_CONTEXT = {"help_option_names": ["-h", "--help"]}
_MAX_PASSWORDS = 1000


def _copy(text: str) -> None:
    from jzr_tools.core import system

    try:
        system.copy_to_clipboard(text)
    except RuntimeError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error


# --- uuid --------------------------------------------------------------------


@click.command(
    "uuid",
    context_settings=_CONTEXT,
    short_help="Generate UUIDs (v4, or v7 with --v7).",
)
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many to make.")
@click.option("--v7", "v7", is_flag=True, help="Version 7: time-ordered, sorts by creation.")
@click.option("--upper", is_flag=True, help="Upper-case letters.")
@click.option("--no-hyphens", is_flag=True, help="Leave out the hyphens.")
@click.option("-c", "--copy", is_flag=True, help="Also copy the result to the clipboard.")
def uuid_command(count: int, v7: bool, upper: bool, no_hyphens: bool, copy: bool) -> None:
    """Generate UUIDs (v4, or v7 with --v7).

    Version 7 IDs made in one run sort in the order they were made.
    """
    from jzr_tools.core import generate

    try:
        values = generate.make_uuids(count, version=7 if v7 else 4)
    except generate.GenerateError as error:
        raise click.UsageError(str(error)) from error
    lines = [generate.format_uuid(value, upper=upper, hyphens=not no_hyphens) for value in values]
    if copy:
        _copy("\n".join(lines))
    click.echo("\n".join(lines))


# --- password ----------------------------------------------------------------


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


@click.command(
    "password",
    context_settings=_CONTEXT,
    short_help="Generate passwords or passphrases.",
)
@click.option("-l", "--length", type=int, default=None, help="Characters.  [default: 20]")
@click.option("--no-upper", is_flag=True, help="No upper-case letters.")
@click.option("--no-lower", is_flag=True, help="No lower-case letters.")
@click.option("--no-digits", is_flag=True, help="No digits.")
@click.option("--no-symbols", is_flag=True, help="No symbols.")
@click.option("--no-similar", is_flag=True, help="Leave out look-alikes: I l 1 O 0 o.")
@click.option(
    "-w", "--words", type=int, default=None, help="Make a passphrase of this many words (3-20)."
)
@click.option("--separator", default=None, help="Between passphrase words.  [default: -]")
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many to make.")
@click.option(
    "-c", "--copy", is_flag=True, help="Copy to the clipboard instead of printing."
)
def password_command(
    length: int | None,
    no_upper: bool,
    no_lower: bool,
    no_digits: bool,
    no_symbols: bool,
    no_similar: bool,
    words: int | None,
    separator: str | None,
    count: int,
    copy: bool,
) -> None:
    """Generate passwords or passphrases.

    Passwords use upper- and lower-case letters, digits and symbols, with at
    least one of each type chosen. -w makes a passphrase from the EFF Long
    Wordlist. On a terminal the strength is shown on stderr; --copy puts the
    result on the clipboard without printing it.
    """
    from jzr_tools.core import generate

    character_options = no_upper or no_lower or no_digits or no_symbols or no_similar
    if words is not None and (length is not None or character_options):
        raise click.UsageError(
            "--words makes a passphrase; --length and the character options do not apply."
        )
    if separator is not None and words is None:
        raise click.UsageError("--separator only applies with --words.")
    if not 1 <= count <= _MAX_PASSWORDS:
        raise click.UsageError(f"Choose a count from 1 to {_MAX_PASSWORDS}.")

    try:
        if words is not None:
            made = [
                generate.make_passphrase(words, "-" if separator is None else separator)
                for _ in range(count)
            ]
            bits = generate.passphrase_bits(words)
            kind, size = "passphrase", _plural(words, "word")
            single = f"a {words}-word passphrase"
        else:
            options = generate.PasswordOptions(
                length=20 if length is None else length,
                upper=not no_upper,
                lower=not no_lower,
                digits=not no_digits,
                symbols=not no_symbols,
                no_similar=no_similar,
            )
            generate.check_password_options(options)
            made = [generate.make_password(options) for _ in range(count)]
            bits = generate.password_bits(options)
            kind, size = "password", _plural(options.length, "character")
            single = f"a {options.length}-character password"
    except generate.GenerateError as error:
        raise click.UsageError(str(error)) from error

    strength = f"about {round(bits)} bits"
    if copy:
        _copy("\n".join(made))
        if count == 1:
            click.echo(f"Copied {single} ({strength}).", err=True)
        else:
            click.echo(f"Copied {count} {kind}s ({size}, {strength} each).", err=True)
        return

    click.echo("\n".join(made))
    if stdout_is_terminal():
        each = " each" if count > 1 else ""
        click.echo(f"{size}, {strength}{each}", err=True)


# --- random ------------------------------------------------------------------


@click.group(
    "random",
    context_settings=_CONTEXT,
    short_help="Pick random numbers or items, or shuffle a list.",
    invoke_without_command=True,
)
@click.pass_context
def random_group(context: click.Context) -> None:
    """Pick random numbers or items, or shuffle a list.

    Every draw uses the operating system's secure random source.
    """
    # Bare `jzr random` shows the actions on stdout instead of drawing anything.
    if context.invoked_subcommand is None:
        click.echo(context.get_help())


@random_group.command(
    "int",
    context_settings={**_CONTEXT, "ignore_unknown_options": True},
)
@click.argument("bounds", nargs=-1, type=int, metavar="[MIN MAX]")
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many numbers.")
@click.option("--unique", is_flag=True, help="No number twice.")
def random_int(bounds: tuple[int, ...], count: int, unique: bool) -> None:
    """Random whole numbers from MIN to MAX, both included (default 1 100).

    Negative bounds work as they are: jzr random int -10 10.
    """
    from jzr_tools.core import generate

    if len(bounds) not in (0, 2):
        raise click.UsageError("Give both MIN and MAX, or neither.")
    low, high = bounds if bounds else (1, 100)
    try:
        numbers = generate.random_ints(low, high, count, unique=unique)
    except generate.GenerateError as error:
        raise click.UsageError(str(error)) from error
    click.echo("\n".join(str(number) for number in numbers))


def _items(arguments: Sequence[str]) -> list[str]:
    from jzr_tools.core import generate

    if arguments:
        return list(arguments)
    return generate.read_items(read_text(None))


def _no_items(message: str) -> None:
    click.echo(message, err=True)
    raise click.exceptions.Exit(1)


@random_group.command("pick", context_settings=_CONTEXT)
@click.argument("items", nargs=-1)
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many items.")
@click.option("--repeat", is_flag=True, help="Allow the same item more than once.")
def random_pick(items: tuple[str, ...], count: int, repeat: bool) -> None:
    """Pick random ITEMS; without ITEMS, the lines of stdin."""
    from jzr_tools.core import generate

    choices = _items(items)
    if not choices:
        _no_items("No items to pick from.")
    try:
        picked = generate.pick_items(choices, count, repeat=repeat)
    except generate.GenerateError as error:
        raise click.UsageError(str(error)) from error
    click.echo("\n".join(picked))


@random_group.command("shuffle", context_settings=_CONTEXT)
@click.argument("items", nargs=-1)
def random_shuffle(items: tuple[str, ...]) -> None:
    """Print ITEMS in random order; without ITEMS, the lines of stdin."""
    from jzr_tools.core import generate

    choices = _items(items)
    if not choices:
        _no_items("No items to shuffle.")
    click.echo("\n".join(generate.shuffle_items(choices)))

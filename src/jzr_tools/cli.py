"""Root CLI application for jzr-tools.

Every ``jzr`` call pays for the modules it imports, so this module imports
nothing command-specific. Commands are listed in ``_COMMANDS`` and their
modules are loaded only when a command is actually invoked.
"""

from __future__ import annotations

from collections.abc import Sequence
import importlib
import sys

import click

from jzr_tools import __version__

# name -> ("package.module:attribute", one-line help shown by `jzr --help`).
# The help is repeated here so listing commands does not import them; a test
# keeps it equal to each command's own short help.
_COMMANDS: dict[str, tuple[str, str]] = {
    "image": ("jzr_tools.commands.image:app", "Convert images between formats."),
    "video": ("jzr_tools.commands.video:app", "Work with video files."),
    "audio": ("jzr_tools.commands.audio:app", "Work with audio files."),
    "text": ("jzr_tools.commands.text:app", "Text utilities."),
    "port": ("jzr_tools.commands.network:app", "Inspect and free local ports."),
    "host": ("jzr_tools.commands.host:app", "Inspect and clean up this machine."),
    "bind": ("jzr_tools.commands.bind:app", "Save your own shortcuts for long commands."),
    # The one shortcut: the same command as `jzr port kill`.
    "killport": (
        "jzr_tools.commands.network:killport",
        "Force-kill processes using a local port (Windows, Linux, and macOS).",
    ),
}


# Commands defined on the root group itself, below.
_ROOT_COMMANDS = frozenset({"version"})


class LazyGroup(click.Group):
    """Click group whose subcommands are imported on first use."""

    def __init__(
        self,
        *args: object,
        lazy_commands: dict[str, tuple[str, str]] | None = None,
        fallback: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lazy_commands = dict(lazy_commands or {})
        # "package.module:function" asked for names this group does not know;
        # it returns a command or None. Imported only when that happens.
        self.fallback = fallback

    def list_commands(self, ctx: click.Context) -> list[str]:
        return [*self.commands, *self.lazy_commands]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        entry = self.lazy_commands.get(name)
        if entry is not None:
            return self._load(entry[0])

        command = super().get_command(ctx, name)
        if command is None and self.fallback is not None:
            command = self._load(self.fallback)(name)
        return command

    @staticmethod
    def _load(path: str) -> object:
        module_name, _, attribute = path.partition(":")
        return getattr(importlib.import_module(module_name), attribute)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # The default implementation loads every command to read its help.
        rows = []
        for name in self.list_commands(ctx):
            if name in self.lazy_commands:
                rows.append((name, self.lazy_commands[name][1]))
                continue
            command = self.commands[name]
            if not command.hidden:
                rows.append((name, command.get_short_help_str(formatter.width)))

        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


@click.group(
    cls=LazyGroup,
    lazy_commands=_COMMANDS,
    # A name that is not a command may be one of the user's own shortcuts.
    fallback="jzr_tools.commands.bind:command_for",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(
    __version__,
    "--version",
    message="%(version)s",
    help="Show the installed version and exit.",
)
def app() -> None:
    """Personal cross-platform CLI toolkit."""


@app.command()
def version() -> None:
    """Print the toolkit version."""
    click.echo(__version__)


def should_expand_windows_args(argv: Sequence[str]) -> bool:
    """Decide whether Click may expand ``$VAR``, ``%VAR%``, ``~`` and globs.

    On Windows the shell leaves that to the program, and Click does it for
    every argument. File commands want the globs; a bound command and the
    words given to ``bind`` must reach the program exactly as typed.
    """
    if not argv:
        return True
    first = argv[0]
    if first == "bind":
        return False
    if first.startswith("-") or first in _COMMANDS or first in _ROOT_COMMANDS:
        return True
    from jzr_tools.core import bindings

    try:
        return first not in bindings.load()
    except bindings.BindingError:
        return True  # the binding fallback reports the damaged file


def _utf8_output() -> None:
    """Write UTF-8 to pipes and files, whatever the locale's encoding is.

    On Windows a pipe gets the ANSI code page (often cp1252), which cannot
    hold a Cyrillic file name, so result lines would end with a traceback.
    A terminal is left alone: Click writes to the Windows console through
    its wide-character API, and a POSIX terminal keeps the locale's encoding.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            terminal = stream.isatty()
        except (OSError, ValueError):
            terminal = False
        if terminal:
            # Never crash on a character the terminal cannot show.
            reconfigure(errors="backslashreplace")
        else:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    """Console script entrypoint."""
    _utf8_output()
    app(windows_expand_args=should_expand_windows_args(sys.argv[1:]))

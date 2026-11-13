"""Root CLI application for jzr-tools.

Every ``jzr`` call pays for the modules it imports, so this module imports
nothing command-specific. Commands are listed in ``_COMMANDS`` and their
modules are loaded only when a command is actually invoked.
"""

from __future__ import annotations

import importlib

import click

from jzr_tools import __version__

# name -> ("package.module:attribute", one-line help shown by `jzr --help`).
# The help is repeated here so listing commands does not import them; a test
# keeps it equal to each command's own short help.
_COMMANDS: dict[str, tuple[str, str]] = {
    "image": (
        "jzr_tools.commands.image:app",
        "Convert images using ImageMagick or ffmpeg.",
    ),
    "text": ("jzr_tools.commands.text:app", "Text utilities."),
    "port": ("jzr_tools.commands.network:app", "Inspect and free local ports."),
    "host": ("jzr_tools.commands.host:app", "Inspect and clean up this machine."),
    # The one shortcut: the same command as `jzr port kill`.
    "killport": (
        "jzr_tools.commands.network:killport",
        "Force-kill processes using a local port (Windows, Linux, and macOS).",
    ),
}


class LazyGroup(click.Group):
    """Click group whose subcommands are imported on first use."""

    def __init__(
        self,
        *args: object,
        lazy_commands: dict[str, tuple[str, str]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lazy_commands = dict(lazy_commands or {})

    def list_commands(self, ctx: click.Context) -> list[str]:
        return [*self.commands, *self.lazy_commands]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        entry = self.lazy_commands.get(name)
        if entry is None:
            return super().get_command(ctx, name)

        module_name, _, attribute = entry[0].partition(":")
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


def main() -> None:
    """Console script entrypoint."""
    app()

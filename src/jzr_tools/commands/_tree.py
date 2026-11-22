"""The commands an AI agent may want, found by walking the Click tree.

Shared by the agent skill and the MCP server, so neither keeps its own list.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import click

from jzr_tools.cli import app as root

# `agent` is how agents get here, `version` is not a job, `killport` is `port kill`.
LEFT_OUT = frozenset({"agent", "version", "killport"})
# The group of commands that are not in one.
TOP_LEVEL = "tools"


@dataclass(frozen=True)
class Entry:
    """One command that can be run."""

    names: tuple[str, ...]
    command: click.Command

    @property
    def path(self) -> str:
        return " ".join(self.names)

    @property
    def group(self) -> str:
        return self.names[0] if len(self.names) > 1 else TOP_LEVEL


def context(
    command: click.Command, name: str, parent: click.Context | None, **extra: object
) -> click.Context:
    """A context as Click makes one when the command is run."""
    return click.Context(command, info_name=name, parent=parent, **{**command.context_settings, **extra})


def _walk(group: click.Group, ctx: click.Context, names: tuple[str, ...]) -> Iterator[Entry]:
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if command is None or command.hidden or (not names and name in LEFT_OUT):
            continue
        path = (*names, name)
        if isinstance(command, click.Group):
            yield from _walk(command, context(command, name, ctx), path)
        else:
            yield Entry(path, command)


def entries() -> list[Entry]:
    """Every command, in the order `jzr --help` lists them; never the user's bindings."""
    return list(_walk(root, context(root, "jzr", None), ()))

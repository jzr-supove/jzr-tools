"""`jzr agent`: connect AI agents to jzr, with a skill or an MCP server."""

from __future__ import annotations

from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import click

from jzr_tools import __version__

if TYPE_CHECKING:
    from jzr_tools.core.skill import Outcome, Target

# The same names as `core.skill.AGENTS`, which is not imported for `-h`.
_AGENTS = ("claude", "codex")


@click.group()
def app() -> None:
    """Connect jzr to AI agents (skill and MCP server).

    The skill is a short guide to jzr's commands that Claude Code, Codex and
    Cursor load when a task needs it. Run `jzr agent install` again after
    updating jzr. The MCP server offers the commands as tools to programs
    without a shell, such as Claude Desktop.
    """


@app.command()
@click.option("--path", "show_path", is_flag=True, help="Print the folder of the packaged skill instead.")
def skill(show_path: bool) -> None:
    """Print the skill that `jzr agent install` installs."""
    from jzr_tools.core import skill as core

    folder = core.source_dir()
    if show_path:
        click.echo(str(folder))
        return
    # Bytes, so the line endings stay "\n" on Windows too.
    click.echo(core.files(folder)["SKILL.md"], nl=False)


@app.command("mcp")
def mcp_server() -> None:
    """Run the MCP server over stdin and stdout.

    An MCP client such as Claude Desktop starts this command. `jzr agent
    install --mcp` tells Claude about it.
    """
    from jzr_tools.core import mcp_setup

    if not mcp_setup.available():
        _fail(mcp_setup.NEEDS_EXTRA)
    from jzr_tools.commands import _mcp_server

    if sys.stdin.isatty():
        click.echo(
            "jzr MCP server: an MCP client starts this command. Press Ctrl+C to stop.", err=True
        )
    _mcp_server.serve()


def _target_options(overwrite_help: str, dry_run_help: str, mcp_help: str):  # noqa: ANN202
    def attach(command):  # noqa: ANN001, ANN202
        for option in (
            click.option("--dry-run", is_flag=True, help=dry_run_help),
            click.option("--mcp", "with_mcp", is_flag=True, help=mcp_help),
            click.option("--overwrite", is_flag=True, help=overwrite_help),
            click.option("--project", is_flag=True, help="Use the current folder instead of your home folder."),
            click.option("--agent", type=click.Choice(_AGENTS), help="Only this agent, found or not."),
        ):
            command = option(command)
        return command

    return attach


@app.command()
@_target_options(
    "Replace a copy that was changed, is newer, or was not installed by jzr.",
    "Show what would be installed; write nothing.",
    "Also register the MCP server with Claude.",
)
def install(
    agent: str | None, project: bool, overwrite: bool, with_mcp: bool, dry_run: bool
) -> None:
    """Install the skill for every AI agent found.

    Claude Code gets ~/.claude/skills/jzr; Codex gets ~/.agents/skills/jzr.
    Cursor reads either. With --project the skill goes into .claude/skills
    (--agent codex: .agents/skills) of the current folder.
    """
    from jzr_tools.core import skill as core

    _check_mcp_options(with_mcp, agent, project)
    if with_mcp:
        from jzr_tools.core import mcp_setup

        if not mcp_setup.available():
            _fail(mcp_setup.NEEDS_EXTRA)

    def run(target: Target) -> Outcome:
        return core.install(target, __version__, overwrite=overwrite, dry_run=dry_run)

    outcomes = _run(agent, project, dry_run, run)
    if not dry_run and any(outcome.status in ("installed", "updated") for outcome in outcomes):
        click.echo("Restart the agent, or start a new session, to use it.")
    registered = _register(dry_run) if with_mcp else True
    _exit(outcomes, registered)


@app.command()
@_target_options(
    "Remove a copy that was changed since jzr installed it.",
    "Show what would be removed; delete nothing.",
    "Also take the MCP server out of Claude Code.",
)
def remove(
    agent: str | None, project: bool, overwrite: bool, with_mcp: bool, dry_run: bool
) -> None:
    """Remove the skill that `jzr agent install` installed."""
    from jzr_tools.core import skill as core

    _check_mcp_options(with_mcp, agent, project)

    def run(target: Target) -> Outcome:
        return core.remove(target, overwrite=overwrite, dry_run=dry_run)

    outcomes = _run(agent, project, dry_run, run)
    unregistered = _unregister(dry_run) if with_mcp else True
    _exit(outcomes, unregistered)


def _run(agent: str | None, project: bool, dry_run: bool, run) -> list[Outcome]:  # noqa: ANN001
    """Work on every target and print a line for each."""
    from jzr_tools.core import skill as core

    try:
        targets = core.targets(Path.home(), agent=agent, project=Path.cwd() if project else None)
    except core.SkillError as error:
        click.echo(f"error: {error}", err=True)
        raise SystemExit(1) from None

    outcomes = []
    for target in targets:
        outcome = run(target)
        outcomes.append(outcome)
        if outcome.status == "refused":
            click.echo(f"skip {target.path}: {outcome.detail}", err=True)
        elif outcome.status == "failed":
            click.echo(f"error: {target.path}: {outcome.detail}", err=True)
        else:
            click.echo(_line(outcome, dry_run))
    return outcomes


def _line(outcome: Outcome, dry_run: bool) -> str:
    where = f"{outcome.target.path} ({', '.join(outcome.target.agents)})"
    if outcome.status == "current":
        return f"jzr skill {__version__} is already installed: {where}"
    if outcome.status == "absent":
        return f"no jzr skill at {where}"
    if outcome.status == "removed":
        return f"{'would remove' if dry_run else 'removed'} jzr skill -> {where}"
    if outcome.status == "installed":
        return f"{'would install' if dry_run else 'installed'} jzr skill {__version__} -> {where}"
    if outcome.detail == __version__:
        change = __version__
    elif outcome.detail[:1].isdigit():
        change = f"{outcome.detail} to {__version__}"
    else:
        # Unknown, or something in the marker that is not a version.
        change = f"to {__version__}"
    return f"{'would update' if dry_run else 'updated'} jzr skill {change} -> {where}"


def _exit(outcomes: list[Outcome], mcp_ok: bool) -> None:
    if not mcp_ok or any(outcome.status in ("refused", "failed") for outcome in outcomes):
        raise SystemExit(1)


def _fail(message: str) -> None:
    click.echo(f"error: {message}", err=True)
    raise SystemExit(1)


def _check_mcp_options(with_mcp: bool, agent: str | None, project: bool) -> None:
    if with_mcp and (project or agent == "codex"):
        raise click.UsageError(
            "--mcp registers with Claude; it cannot be used with --project or --agent codex."
        )


def _shown(command: list[str]) -> str:
    """A command line to paste into this system's shell."""
    return subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)


def _register(dry_run: bool) -> bool:
    """Register the MCP server; whether nothing failed."""
    from jzr_tools.core import mcp_setup

    server = mcp_setup.server_command(mcp_setup.jzr_command(sys.argv[0]))
    claude = shutil.which("claude")
    ok = True
    if claude is None:
        command = _shown(mcp_setup.add_command("claude", server))
        click.echo(f"Claude Code is not on PATH; to register there, run: {command}")
    elif dry_run:
        click.echo(f"would run: {_shown(mcp_setup.add_command(claude, server))}")
    else:
        result = mcp_setup.register(claude, server)
        if result.status == "added":
            click.echo("registered the jzr MCP server with Claude Code")
        elif result.status == "exists":
            click.echo("the jzr MCP server is already registered with Claude Code")
        else:
            click.echo(f"error: Claude Code: {result.detail}", err=True)
            ok = False
    click.echo(
        "Claude Desktop: open Settings > Developer > Edit Config, add this entry and restart the app:"
    )
    click.echo(mcp_setup.desktop_entry(server))
    click.echo(f"The file is usually {mcp_setup.desktop_path(sys.platform)}.")
    return ok


def _unregister(dry_run: bool) -> bool:
    """Take the MCP server out of Claude Code; whether nothing failed."""
    from jzr_tools.core import mcp_setup

    claude = shutil.which("claude")
    ok = True
    if claude is None:
        command = _shown(mcp_setup.remove_command("claude"))
        click.echo(f"Claude Code is not on PATH; to remove it there, run: {command}")
    elif dry_run:
        click.echo(f"would run: {_shown(mcp_setup.remove_command(claude))}")
    else:
        result = mcp_setup.unregister(claude)
        if result.status == "removed":
            click.echo("removed the jzr MCP server from Claude Code")
        elif result.status == "absent":
            click.echo("the jzr MCP server is not registered with Claude Code")
        else:
            click.echo(f"error: Claude Code: {result.detail}", err=True)
            ok = False
    click.echo("Claude Desktop: remove the jzr entry under Settings > Developer > Edit Config.")
    return ok

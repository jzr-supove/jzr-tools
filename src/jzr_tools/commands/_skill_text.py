"""Build the texts of the agent skill from the commands themselves.

The results are committed under ``data/skill/jzr`` and shipped with the
package; a test fails when they no longer match the commands. In a checkout
the same run also writes the Claude Code plugin files (see ``_plugin``). After
changing a command, its help or its examples, run::

    uv run python -m jzr_tools.commands._skill_text
"""

from __future__ import annotations

from pathlib import Path

import click

from jzr_tools.cli import app as root
from jzr_tools.commands._skill_examples import EXAMPLES
from jzr_tools.commands._tree import LEFT_OUT, TOP_LEVEL, Entry, context, entries
from jzr_tools.core.skill import source_dir

__all__ = [
    "LEFT_OUT", "TOP_LEVEL", "Entry", "build", "command_map", "entries", "help_text", "reference", "write",
]

HELP_WIDTH = 88

_START = "<!-- commands:start -->"
_END = "<!-- commands:end -->"


def _by_group() -> dict[str, list[Entry]]:
    groups: dict[str, list[Entry]] = {}
    for entry in entries():
        groups.setdefault(entry.group, []).append(entry)
    return groups


def command_map() -> str:
    """The list of commands in SKILL.md, under one heading for each reference file."""
    blocks = []
    for group, commands in _by_group().items():
        lines = [f"### {group} ([reference/{group}.md](reference/{group}.md))"]
        for entry in commands:
            lines.append(f"- `jzr {entry.path}` - {entry.command.get_short_help_str(limit=300)}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def help_text(entry: Entry) -> str:
    """What `jzr <path> -h` prints, at a fixed width and without the -h line."""
    ctx = context(root, "jzr", None)
    command: click.Command = root
    for name in entry.names:
        assert isinstance(command, click.Group)
        command = command.get_command(ctx, name) or entry.command
        ctx = context(command, name, ctx, terminal_width=HELP_WIDTH, max_content_width=HELP_WIDTH)

    lines = [line.rstrip() for line in command.get_help(ctx).splitlines()]
    lines = [line for line in lines if not line.startswith("  -h, --help")]
    while lines and lines[-1] in ("", "Options:"):
        lines.pop()
    return "\n".join(lines) + "\n"


def reference(stem: str) -> str:
    """The reference file of one group: help and examples for each command."""
    title = "jzr top-level commands" if stem == TOP_LEVEL else f"jzr {stem}"
    parts = [f"# {title}\n"]
    for entry in _by_group()[stem]:
        examples = "\n".join(f"- `{line}` - {what}" for line, what in EXAMPLES[entry.path])
        parts.append(
            f"## jzr {entry.path}\n\n```text\n{help_text(entry)}```\n\nExamples:\n\n{examples}\n"
        )
    return "\n".join(parts)


def build() -> dict[str, str]:
    """The text of every file of the skill, by its path in the skill folder."""
    template = (source_dir() / "SKILL.md").read_text(encoding="utf-8")
    before, _, rest = template.partition(_START)
    _, _, after = rest.partition(_END)
    built = {"SKILL.md": f"{before}{_START}\n{command_map()}{_END}{after}"}
    for group in _by_group():
        built[f"reference/{group}.md"] = reference(group)
    return built


def write(folder: Path) -> list[Path]:
    """Bring the files in ``folder`` up to date; the ones that changed."""
    changed = []
    for name, text in build().items():
        path = folder / name
        if path.is_file() and path.read_bytes().replace(b"\r\n", b"\n") == text.encode("utf-8"):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        changed.append(path)
    return changed


def _main() -> None:
    from jzr_tools import __version__
    from jzr_tools.commands import _plugin

    changed = write(source_dir())
    root = _plugin.repository_root(Path(__file__).resolve().parents[1])
    if root is not None:
        changed += _plugin.write(root, _plugin.plugin_files(build(), __version__))
    for path in changed or ["up to date"]:
        click.echo(str(path))


if __name__ == "__main__":
    _main()

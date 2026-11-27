"""Build the texts of the four agent skills from the commands themselves.

``data/skill/template.md`` is the only hand-written text; each skill's
SKILL.md is cut from it and its reference files are made from the commands.
The results are committed under ``data/skill/<skill>`` and shipped with the
package; a test fails when they no longer match the commands. In a checkout
the same run also writes the Claude Code plugin files (see ``_plugin``). After
changing a command, its help, its examples or the template, run::

    uv run python -m jzr_tools.commands._skill_text
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re

import click

from jzr_tools.cli import app as root
from jzr_tools.commands._plugin import sync
from jzr_tools.commands._skill_examples import EXAMPLES
from jzr_tools.commands._tree import LEFT_OUT, TOP_LEVEL, Entry, context, entries
from jzr_tools.core.skill import SKILL_NAMES, skills_dir

__all__ = [
    "LEFT_OUT", "SHARED_OPTIONS", "SKILLS", "TOP_LEVEL", "Entry", "Skill", "build", "command_map", "entries",
    "help_text", "own_options", "reference", "skill_text", "write",
]

HELP_WIDTH = 88
TEMPLATE = "template.md"


@dataclass(frozen=True)
class Skill:
    """What the front matter says, and the groups whose commands the skill lists."""

    description: str
    groups: tuple[str, ...]


SKILLS: dict[str, Skill] = {
    "jzr-media": Skill(
        "Run the jzr command line for image, video and audio files. Convert, compress, resize, trim or "
        "inspect images, video and audio; make GIFs; edit audio tags. Use it instead of ffmpeg or Pillow "
        "scripts.",
        ("image", "video", "audio", "ffmpeg"),
    ),
    "jzr-pdf": Skill(
        "Run the jzr command line for PDF files. Merge, split, reorder, rotate or remove pages; pull out "
        "text or images; show page counts and sizes. Use it instead of pypdf scripts or Ghostscript.",
        ("pdf",),
    ),
    "jzr-data": Skill(
        "Run the jzr command line for data, text and file jobs. Convert JSON, YAML, CSV, TOML and Excel "
        "data; rename files; hash files; encode, diff or slug text; read JWTs; convert timestamps; make "
        "UUIDs or passwords.",
        ("data", "text", "tools", "random", "env", "file"),
    ),
    "jzr-system": Skill(
        "Run the jzr command line for machine jobs. Find or free a port, see what holds it, clean caches, "
        "and set up shell completion.",
        ("port", "host", "bind", "completion"),
    ),
}
assert tuple(SKILLS) == SKILL_NAMES, "the skills must be the installer's, in its order"

# "[media pdf] " at the start of a template line: the line is only for those skills.
_TAG = re.compile(r"\[([^\]]+)\] ")


def _by_group() -> dict[str, list[Entry]]:
    groups: dict[str, list[Entry]] = {}
    for entry in entries():
        groups.setdefault(entry.group, []).append(entry)
    return groups


# Explained once in the Rules of SKILL.md for every batch command, so the list leaves them out.
SHARED_OPTIONS = frozenset(
    {"--dir", "--pattern", "--recursive", "--output", "--outdir", "--overwrite", "--dry-run", "--help"}
)
# Click's words for numbers, written as N in the list.
_NUMERIC = frozenset({"INTEGER", "INTEGER RANGE", "FLOAT", "FLOAT RANGE"})


def _command_context(entry: Entry) -> tuple[click.Command, click.Context]:
    """The command and the context Click makes for it when it is run as ``jzr <path>``."""
    ctx = context(root, "jzr", None)
    command: click.Command = root
    for name in entry.names:
        assert isinstance(command, click.Group)
        command = command.get_command(ctx, name) or entry.command
        ctx = context(command, name, ctx)
    return command, ctx


def own_options(entry: Entry) -> list[str]:
    """The arguments, then the options as `-h` lists them, without the shared ones.

    An agent can then run the command without asking for its help first, which
    costs it a whole turn.
    """
    command, ctx = _command_context(entry)
    arguments: list[str] = []
    options: list[str] = []
    for param in command.get_params(ctx):
        if isinstance(param, click.Argument):
            arguments.append(param.make_metavar(ctx))
            continue
        record = param.get_help_record(ctx)
        if not isinstance(param, click.Option) or param.hidden or record is None:
            continue
        longest = max((n for n in (*param.opts, *param.secondary_opts) if n.startswith("--")), key=len)
        if longest in SHARED_OPTIONS:
            continue
        # The record reads "-s, --size SIZE": the metavar follows the last name.
        metavar = record[0].split(", ")[-1].partition(" ")[2]
        metavar = "N" if metavar in _NUMERIC else metavar
        options.append(f"{longest} {metavar}" if metavar else longest)
    return [*arguments, *options]


def command_map(groups: Sequence[str]) -> str:
    """The list of commands of ``groups`` in SKILL.md, one heading per group, in `jzr --help` order."""
    blocks = []
    for group, commands in _by_group().items():
        if group not in groups:
            continue
        lines = [f"### {group}"]
        for entry in commands:
            span = " ".join([f"jzr {entry.path}", *own_options(entry)])
            lines.append(f"- `{span}` - {entry.command.get_short_help_str(limit=300)}")
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


def _template() -> str:
    return (skills_dir() / TEMPLATE).read_text(encoding="utf-8").replace("\r\n", "\n")


def skill_text(name: str, template: str | None = None) -> str:
    """The SKILL.md of one skill: the template's lines for it, with the placeholders filled in.

    A line that starts with a tag such as ``[media pdf] `` is kept, without the
    tag, only in the skills named; a line without one is kept in every skill.
    """
    if name not in SKILLS:
        raise ValueError(f"no skill named {name}; the skills are {', '.join(SKILLS)}")
    known = {skill.removeprefix("jzr-") for skill in SKILLS}
    short = name.removeprefix("jzr-")
    kept = []
    for line in (_template() if template is None else template).splitlines(keepends=True):
        tag = _TAG.match(line)
        if tag is None:
            kept.append(line)
            continue
        names = tag.group(1).split()
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"unknown skill {', '.join(unknown)} in the template line {line.rstrip()!r}")
        if short in names:
            kept.append(line[tag.end():])
    skill = SKILLS[name]
    # The commands go in last, so that nothing in a command's help is taken for a placeholder.
    text = "".join(kept).replace("{name}", name).replace("{description}", skill.description)
    return text.replace("{commands}", command_map(skill.groups).rstrip("\n"))


def build() -> dict[str, dict[str, str]]:
    """The text of every file of every skill: skill -> {path in its folder -> text}."""
    groups = list(_by_group())
    owned = {group for skill in SKILLS.values() for group in skill.groups}
    for group in groups:
        if group not in owned:
            raise ValueError(f"the command group {group} belongs to no skill; add it to one in SKILLS")
    template = _template()
    built = {}
    for name, skill in SKILLS.items():
        files = {"SKILL.md": skill_text(name, template)}
        for group in skill.groups:
            if group not in groups:
                raise ValueError(f"the skill {name} names {group}, which is not a command group")
            files[f"reference/{group}.md"] = reference(group)
        built[name] = files
    return built


def write(folder: Path) -> list[Path]:
    """Bring the skill folders in ``folder`` (``data/skill``) up to date; the files changed or deleted.

    A file in one of the skill folders that the build no longer has is
    deleted; everything else in ``folder``, such as the template, is left alone.
    """
    changed = []
    for name, texts in build().items():
        files = {f"{name}/{path}": text.encode("utf-8") for path, text in texts.items()}
        changed += sync(folder, files, folder / name)
    return changed


def _main() -> None:
    from jzr_tools import __version__
    from jzr_tools.commands import _plugin

    changed = write(skills_dir())
    root = _plugin.repository_root(Path(__file__).resolve().parents[1])
    if root is not None:
        changed += _plugin.write(root, _plugin.plugin_files(build(), __version__))
    for path in changed or ["up to date"]:
        click.echo(str(path))


if __name__ == "__main__":
    _main()

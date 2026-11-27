"""Tests for the generated skill texts, the worked examples and the packaged skill folders.

The Click tree is walked here on its own, so the code under test is checked
against the real commands and not against itself.
"""

from __future__ import annotations

from collections.abc import Iterable
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import unittest
from unittest import mock

import click

import jzr_tools
from jzr_tools.cli import app

SKILLS_DIR = Path(jzr_tools.__file__).resolve().parent / "data" / "skill"
OLD_SKILL = SKILLS_DIR / "jzr"
TEMPLATE = SKILLS_DIR / "template.md"
SKILL_NAMES = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
# The reference files (groups) of each skill, as the spec lists them.
GROUPS = {
    "jzr-media": ["image", "video", "audio", "ffmpeg"],
    "jzr-pdf": ["pdf"],
    "jzr-data": ["data", "text", "tools", "random", "env", "file"],
    "jzr-system": ["port", "host", "bind", "completion"],
}
DESCRIPTIONS = {
    "jzr-media": (
        "Run the jzr command line for image, video and audio files. Convert, compress, resize, trim or "
        "inspect images, video and audio; make GIFs; edit audio tags. Use it instead of ffmpeg or Pillow "
        "scripts."
    ),
    "jzr-pdf": (
        "Run the jzr command line for PDF files. Merge, split, reorder, rotate or remove pages; pull out "
        "text or images; show page counts and sizes. Use it instead of pypdf scripts or Ghostscript."
    ),
    "jzr-data": (
        "Run the jzr command line for data, text and file jobs. Convert JSON, YAML, CSV, TOML and Excel "
        "data; rename files; hash files; encode, diff or slug text; read JWTs; convert timestamps; make "
        "UUIDs or passwords."
    ),
    "jzr-system": (
        "Run the jzr command line for machine jobs. Find or free a port, see what holds it, clean caches, "
        "and set up shell completion."
    ),
}
# Acceptance criterion 2: each built SKILL.md is smaller (in characters) than the bench's split skill was.
SIZE_LIMITS = {"jzr-media": 6010, "jzr-pdf": 4118, "jzr-data": 6154, "jzr-system": 4603}
LEFT_OUT = frozenset({"agent", "version", "killport"})
TOOLS_TITLE = "# jzr top-level commands"
REGENERATE = "run `python -m jzr_tools.commands._skill_text` to regenerate the skill files"

_CLICK_LAYOUT_IS_KNOWN = tuple(
    int(part) for part in importlib.metadata.version("click").split(".")[:2]
) == (8, 3)
_needs_known_click = unittest.skipUnless(
    _CLICK_LAYOUT_IS_KNOWN, "help layout is only compared against Click 8.3"
)

# Commands that take files or other sources: the examples must show more than one way to call them.
SOURCE_COMMANDS = {
    "image convert", "image compress", "image resize",
    "video convert", "video compress", "video trim", "video gif",
    "audio convert", "audio trim", "audio tags",
    "file rename",
}


def _text():
    return importlib.import_module("jzr_tools.commands._skill_text")


def _examples():
    return importlib.import_module("jzr_tools.commands._skill_examples").EXAMPLES


def _context(command: click.Command, name: str, parent: click.Context | None, **extra: object):
    settings = {**command.context_settings, **extra}
    return click.Context(command, info_name=name, parent=parent, **settings)


def _walk() -> list[tuple[tuple[str, ...], click.Command, click.Context]]:
    """Every visible runnable command of the real tree, found without the code under test."""
    found: list[tuple[tuple[str, ...], click.Command, click.Context]] = []

    def visit(command: click.Command, ctx: click.Context, names: tuple[str, ...]) -> None:
        if isinstance(command, click.Group):
            for name in command.list_commands(ctx):
                child = command.get_command(ctx, name)
                if child is None or child.hidden:
                    continue
                if not names and name in LEFT_OUT:
                    continue
                visit(child, _context(child, name, ctx), (*names, name))
        else:
            found.append((names, command, ctx))

    root = _context(app, "jzr", None)
    visit(app, root, ())
    return found


def _stem(names: tuple[str, ...]) -> str:
    return names[0] if len(names) > 1 else "tools"


# The Rules section of SKILL.md explains these for every batch command, so the list leaves them out.
SHARED_OPTIONS = frozenset(
    {"--dir", "--pattern", "--recursive", "--output", "--outdir", "--overwrite", "--dry-run", "--help"}
)
# Click's own words for numbers; the list shortens each of them to N.
NUMERIC_METAVARS = frozenset({"INTEGER", "INTEGER RANGE", "FLOAT", "FLOAT RANGE"})


def _own_items(command: click.Command, ctx: click.Context) -> list[str]:
    """What the list shows after the short help: the arguments, then the options as `-h` lists them.

    Worked out from Click's own help records, so it does not use the code under test.
    """
    arguments: list[str] = []
    options: list[str] = []
    for param in command.get_params(ctx):
        if isinstance(param, click.Argument):
            arguments.append(param.make_metavar(ctx))
            continue
        assert isinstance(param, click.Option)
        record = param.get_help_record(ctx)
        if param.hidden or record is None:
            continue
        longest = max((n for n in (*param.opts, *param.secondary_opts) if n.startswith("--")), key=len)
        if longest in SHARED_OPTIONS:
            continue
        # The record reads "-s, --size SIZE": the metavar follows the last name.
        _name, _, metavar = record[0].split(", ")[-1].partition(" ")
        metavar = "N" if metavar in NUMERIC_METAVARS else metavar
        options.append(f"{longest} {metavar}" if metavar else longest)
    return [*arguments, *options]


def _help_variants(ctx_names: tuple[str, ...]) -> set[str]:
    """`jzr <path> -h` at width 88 without the -h line, with and without an emptied Options heading."""
    ctx = _context(app, "jzr", None)
    command: click.Command = app
    for name in ctx_names:
        command = command.get_command(ctx, name)
        ctx = _context(command, name, ctx)
    leaf = _context(command, ctx_names[-1], ctx.parent, terminal_width=88, max_content_width=88)
    lines = [
        line.rstrip()
        for line in command.get_help(leaf).splitlines()
        if not re.match(r"\s+-h, --help\b", line)
    ]
    plain = "\n".join(lines).rstrip("\n")
    trimmed = list(lines)
    for index, line in enumerate(trimmed):
        if line == "Options:" and (index + 1 == len(trimmed) or not trimmed[index + 1].strip()):
            del trimmed[index]
            break
    return {plain, "\n".join(trimmed).rstrip("\n")}


class SkillTextCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = tempfile.TemporaryDirectory(prefix="jzr-skill-text-")
        (Path(cls.config.name) / "bindings.json").write_text(
            json.dumps({"version": 1, "bindings": {"mybinding": ["text", "slug", "hello"]}}),
            encoding="utf-8",
        )
        cls.patched = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": cls.config.name})
        cls.patched.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.patched.stop()
        cls.config.cleanup()


class EntriesTests(SkillTextCase):
    def test_constants_match_the_agreed_values(self) -> None:
        module = _text()
        self.assertEqual(module.HELP_WIDTH, 88)
        self.assertEqual(module.LEFT_OUT, LEFT_OUT)
        self.assertEqual(module.TOP_LEVEL, "tools")

    def test_every_visible_runnable_command_appears_exactly_once_in_cli_order(self) -> None:
        expected = [names for names, _command, _ctx in _walk()]

        actual = [entry.names for entry in _text().entries()]

        self.assertEqual(actual, expected)
        self.assertEqual(len(set(actual)), len(actual))

    def test_the_real_tree_has_the_commands_the_skill_must_cover(self) -> None:
        paths = {" ".join(entry.names) for entry in _text().entries()}
        for path in ("image compress", "video gif", "audio tags", "file rename", "uuid", "hash", "jwt",
                     "random int", "host status", "port kill", "bind add", "completion install"):
            self.assertIn(path, paths)

    def test_hidden_commands_and_the_left_out_names_never_appear(self) -> None:
        paths = {" ".join(entry.names) for entry in _text().entries()}
        for path in ("text kebab", "completion setup", "version", "killport"):
            self.assertNotIn(path, paths)
        self.assertFalse(any(path == "agent" or path.startswith("agent ") for path in paths), paths)

    def test_groups_are_not_entries_even_when_they_also_run_bare(self) -> None:
        for entry in _text().entries():
            self.assertNotIsInstance(entry.command, click.Group, entry.names)
        self.assertNotIn(("random",), [entry.names for entry in _text().entries()])

    def test_users_own_bindings_never_appear_anywhere(self) -> None:
        module = _text()
        self.assertTrue((Path(self.config.name) / "bindings.json").is_file())

        self.assertFalse(any(entry.names[0] == "mybinding" for entry in module.entries()))
        self.assertNotIn("mybinding", module.command_map(sorted({e.group for e in module.entries()})))

    def test_path_property_joins_the_names_and_group_is_the_reference_stem(self) -> None:
        for entry in _text().entries():
            with self.subTest(names=entry.names):
                self.assertEqual(entry.path, " ".join(entry.names))
                self.assertEqual(entry.group, _stem(entry.names))
                self.assertIsInstance(entry.command, click.Command)

    def test_top_level_commands_share_the_tools_stem_and_grouped_ones_use_their_group(self) -> None:
        stems = {entry.path: entry.group for entry in _text().entries()}
        for path in ("uuid", "password", "hash", "timestamp", "jwt"):
            self.assertEqual(stems[path], "tools")
        self.assertEqual(stems["random int"], "random")
        self.assertEqual(stems["image compress"], "image")
        self.assertEqual(stems["bind add"], "bind")


class HelpTextTests(SkillTextCase):
    def test_the_first_line_is_usage_with_the_full_command_path(self) -> None:
        for entry in _text().entries():
            with self.subTest(path=entry.path):
                first = _text().help_text(entry).splitlines()[0]
                self.assertTrue(first.startswith(f"Usage: jzr {entry.path}"), first)

    def test_there_is_no_help_option_line_and_no_line_is_padded(self) -> None:
        for entry in _text().entries():
            with self.subTest(path=entry.path):
                text = _text().help_text(entry)
                self.assertNotIn("-h, --help", text)
                self.assertNotIn("\r", text)
                for line in text.splitlines():
                    self.assertEqual(line, line.rstrip(), repr(line))

    @_needs_known_click
    def test_the_text_is_what_click_prints_at_width_88_without_the_help_line(self) -> None:
        for entry in _text().entries():
            with self.subTest(path=entry.path):
                self.assertIn(_text().help_text(entry).rstrip("\n"), _help_variants(entry.names))

    @_needs_known_click
    def test_no_line_is_wider_than_88_and_long_help_wraps_at_88_not_80(self) -> None:
        widest = 0
        for entry in _text().entries():
            for line in _text().help_text(entry).splitlines():
                widest = max(widest, len(line))
                self.assertLessEqual(len(line), 88, line)
        if widest <= 80:
            self.skipTest("no command help is wider than 80 columns, so the width cannot be told apart")

    def test_the_text_does_not_depend_on_the_terminal_colour_or_locale(self) -> None:
        entries = _text().entries()
        reference = {entry.path: _text().help_text(entry) for entry in entries}
        settings = (
            {"COLUMNS": "40"},
            {"COLUMNS": "250"},
            {"NO_COLOR": "1", "FORCE_COLOR": "1"},
            {"LANG": "C", "LC_ALL": "C"},
            {"LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8", "TERM": "dumb"},
        )
        for env in settings:
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                for entry in entries:
                    self.assertEqual(_text().help_text(entry), reference[entry.path], entry.path)



class OwnOptionsTests(SkillTextCase):
    def test_it_is_the_arguments_then_the_options_as_help_lists_them_without_the_shared_ones(self) -> None:
        commands = {" ".join(names): (command, ctx) for names, command, ctx in _walk()}

        for entry in _text().entries():
            with self.subTest(path=entry.path):
                command, ctx = commands[entry.path]
                self.assertEqual(_text().own_options(entry), _own_items(command, ctx))

    def test_some_commands_are_pinned(self) -> None:
        by_path = {entry.path: entry for entry in _text().entries()}

        self.assertEqual(_text().own_options(by_path["image formats"]), [])
        self.assertEqual(_text().own_options(by_path["pdf merge"]), ["[FILES]..."])
        self.assertEqual(
            _text().own_options(by_path["pdf organize"]),
            ["FILE", "--order PAGES", "--delete PAGES", "--rotate [PAGES:]ANGLE"],
        )
        self.assertEqual(_text().own_options(by_path["uuid"])[:2], ["--count N", "--v7"])
        self.assertEqual(_text().SHARED_OPTIONS, SHARED_OPTIONS)


def _help_order() -> list[str]:
    """The groups in the order `jzr --help` lists them, from the real tree."""
    seen: list[str] = []
    for names, _command, _ctx in _walk():
        if _stem(names) not in seen:
            seen.append(_stem(names))
    return seen


def _span(path: str, command: click.Command, ctx: click.Context) -> str:
    """The one code span of a list line: the command, then its own arguments and options."""
    return " ".join([f"jzr {path}", *_own_items(command, ctx)])


def _expected_blocks(groups: Iterable[str]) -> list[tuple[str, list[str]]]:
    """(heading, lines) of the command list for ``groups``, in `jzr --help` order, from the real tree."""
    wanted = set(groups)
    by_stem: dict[str, list[str]] = {}
    for names, command, ctx in _walk():
        line = f"- `{_span(' '.join(names), command, ctx)}` - {command.get_short_help_str(limit=300)}"
        by_stem.setdefault(_stem(names), []).append(line)
    return [(f"### {stem}", by_stem[stem]) for stem in _help_order() if stem in wanted]


def _parse_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Split a command list into (heading, lines); a line before the first heading is an error."""
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append((line, []))
        else:
            if not blocks:
                raise AssertionError(f"a line before any heading: {line!r}")
            blocks[-1][1].append(line)
    return blocks


def _path_of(line: str) -> str:
    """The command path of a list line: the longest real path the span starts with."""
    span = line.split("`")[1]
    paths = [" ".join(names) for names, _command, _ctx in _walk()]
    return max((p for p in paths if span == f"jzr {p}" or span.startswith(f"jzr {p} ")), key=len)


def _all_lines(text: str) -> dict[str, str]:
    """The list lines of a command list by command path."""
    return {_path_of(line): line for line in text.splitlines() if line.startswith("- `jzr ")}


def _spans(text: str) -> list[str]:
    return [line.split("`")[1] for line in text.splitlines() if line.startswith("- `jzr ")]


class SkillsTests(SkillTextCase):
    def test_the_four_skills_in_the_agreed_order(self) -> None:
        self.assertEqual(list(_text().SKILLS), list(SKILL_NAMES))

    def test_the_order_is_the_one_of_the_installer(self) -> None:
        from jzr_tools.core import skill

        self.assertEqual(tuple(_text().SKILLS), tuple(skill.SKILL_NAMES))

    def test_each_skill_has_the_agreed_groups_in_the_agreed_order(self) -> None:
        for name, groups in GROUPS.items():
            with self.subTest(skill=name):
                self.assertEqual(list(_text().SKILLS[name].groups), groups)

    def test_each_skill_has_the_agreed_description(self) -> None:
        for name, description in DESCRIPTIONS.items():
            with self.subTest(skill=name):
                self.assertEqual(_text().SKILLS[name].description, description)

    def test_every_group_of_the_command_tree_belongs_to_exactly_one_skill(self) -> None:
        owners: dict[str, list[str]] = {}
        for name, skill in _text().SKILLS.items():
            for group in skill.groups:
                owners.setdefault(group, []).append(name)

        self.assertEqual(set(owners), set(_help_order()))
        self.assertEqual(set(owners), {entry.group for entry in _text().entries()})
        for group, names in owners.items():
            with self.subTest(group=group):
                self.assertEqual(len(names), 1, names)

    def test_a_group_that_belongs_to_no_skill_fails_the_build_and_the_error_names_it(self) -> None:
        module = _text()
        extra = module.Entry(("zzzgroup", "go"), click.Command("go", help="Go."))
        listed = [*module.entries(), extra]

        with (
            mock.patch.object(module, "entries", lambda: listed),
            mock.patch("jzr_tools.commands._tree.entries", lambda: listed),
            self.assertRaises(ValueError) as caught,
        ):
            module.build()

        self.assertIn("zzzgroup", str(caught.exception))

    def test_the_real_tree_builds_so_the_error_above_comes_from_the_extra_group(self) -> None:
        self.assertEqual(list(_text().build()), list(SKILL_NAMES))


class CommandMapTests(SkillTextCase):
    def test_one_plain_heading_per_requested_group_in_help_order_whatever_the_order_asked_for(self) -> None:
        request = ["completion", "pdf", "image", "tools"]
        in_help_order = [group for group in _help_order() if group in request]
        self.assertNotEqual(request, in_help_order)

        headings = [heading for heading, _lines in _parse_blocks(_text().command_map(request))]

        self.assertEqual(headings, [f"### {group}" for group in in_help_order])
        self.assertEqual(headings, ["### image", "### pdf", "### tools", "### completion"])

    def test_headings_no_longer_link_the_reference_file(self) -> None:
        text = _text().command_map(_help_order())

        self.assertNotIn("reference/", text)
        self.assertNotIn("](", "".join(heading for heading, _lines in _parse_blocks(text)))

    def test_only_the_requested_groups_are_listed(self) -> None:
        text = _text().command_map(["pdf"])

        self.assertEqual([heading for heading, _lines in _parse_blocks(text)], ["### pdf"])
        paths = list(_all_lines(text))
        self.assertEqual(paths, [" ".join(e.names) for e in _text().entries() if e.group == "pdf"])
        self.assertGreater(len(paths), 5)
        for other in ("image", "video", "port", "random"):
            self.assertNotIn(f"jzr {other} ", text)

    def test_a_group_with_a_single_word_path_is_listed_with_its_top_level_commands(self) -> None:
        text = _text().command_map(["tools"])

        self.assertEqual(
            list(_all_lines(text)), [e.path for e in _text().entries() if e.group == "tools"]
        )
        self.assertIn("uuid", _all_lines(text))

    def test_a_tuple_of_groups_works_as_well_as_a_list(self) -> None:
        self.assertEqual(
            _text().command_map(("port", "bind")), _text().command_map(["port", "bind"])
        )

    def test_the_commands_are_in_the_order_of_the_help_under_each_heading(self) -> None:
        blocks = _parse_blocks(_text().command_map(GROUPS["jzr-data"]))

        self.assertEqual(
            [heading for heading, _lines in blocks],
            [f"### {g}" for g in _help_order() if g in GROUPS["jzr-data"]],
        )
        for heading, lines in blocks:
            with self.subTest(group=heading):
                group = heading.removeprefix("### ")
                expected = [e.path for e in _text().entries() if e.group == group]
                self.assertEqual([_path_of(line) for line in lines], expected)

    def test_each_line_is_one_code_span_with_the_command_and_its_own_options_then_the_short_help(self) -> None:
        shown = 0
        for name, groups in GROUPS.items():
            with self.subTest(skill=name):
                actual = _parse_blocks(_text().command_map(groups))
                expected = _expected_blocks(groups)

                self.assertEqual(actual, expected)
                shown += sum(len(lines) for _heading, lines in expected)
        # Without this the loop could pass over lists that show nothing at all.
        self.assertEqual(shown, len(_walk()))

    def test_the_whole_tree_in_one_call_lists_every_command_once(self) -> None:
        lines = _all_lines(_text().command_map(_help_order()))

        # One heading per group, so the commands of a group that is spread over the help come together.
        expected = [e.path for group in _help_order() for e in _text().entries() if e.group == group]
        self.assertEqual(list(lines), expected)
        self.assertEqual(sorted(lines), sorted(" ".join(names) for names, _c, _x in _walk()))

    def test_the_options_of_some_commands_are_pinned_to_the_agreed_shape(self) -> None:
        pinned = {
            # From the spec: arguments first, then the options in the order `-h` lists them.
            "video gif": "[SOURCES]... --start TIME --end TIME --duration TIME --width N --fps N",
            # Choices stay, the batch options are left out, numbers become N.
            "image compress": (
                "[SOURCES]... --to [jpg|png|webp|avif|heic] --quality N --size SIZE --max-side N --strip "
                "--background COLOR"
            ),
            "uuid": "--count N --v7 --upper --no-hyphens --copy",
            "text slug": "[TEXT] --separator TEXT --preserve-case --include-date --copy",
            "random int": "[MIN MAX] --count N --unique",
            "audio tags": (
                "FILES... --json --set KEY=VALUE --unset KEY --clear --cover IMAGE --no-cover --lyrics FILE "
                "--export-cover FILE"
            ),
            "pdf split": "FILE --every N --pages GROUPS",
            "pdf organize": "FILE --order PAGES --delete PAGES --rotate [PAGES:]ANGLE",
            "pdf merge": "[FILES]...",
            "data convert": (
                "[FILE] --from [json|jsonl|yaml|csv|toml|xlsx] --to [json|jsonl|yaml|csv|toml|xlsx] "
                "--delimiter [,|;|tab||] --no-header --header-row N --types --nest --sheet NAME --indent N "
                "--minify"
            ),
        }
        lines = _all_lines(_text().command_map(_help_order()))
        for path, rest in pinned.items():
            with self.subTest(path=path):
                self.assertEqual(lines[path].split("`")[1], f"jzr {path} {rest}")

    def test_the_spec_example_for_pdf_organize_is_the_line_as_written(self) -> None:
        lines = _all_lines(_text().command_map(["pdf"]))

        self.assertEqual(
            lines["pdf organize"],
            "- `jzr pdf organize FILE --order PAGES --delete PAGES --rotate [PAGES:]ANGLE` - "
            "Reorder, rotate or remove the pages of a PDF.",
        )
        self.assertEqual(
            lines["pdf merge"], "- `jzr pdf merge [FILES]...` - Join PDF files into one, in the order given."
        )

    def test_a_command_with_nothing_of_its_own_to_list_has_only_its_path_in_the_span(self) -> None:
        text = _text().command_map(_help_order())
        lines = text.splitlines()
        for line in (
            "- `jzr image formats` - Show supported extensions and the conversion engines available.",
            "- `jzr bind path` - Print where the bindings are stored.",
            "- `jzr completion bash` - Print the bash completion script.",
            "- `jzr ffmpeg status` - Show which ffmpeg and ffprobe jzr uses and where they come from.",
        ):
            with self.subTest(line=line):
                self.assertIn(line, lines)

    def test_the_shared_batch_options_and_help_are_never_listed(self) -> None:
        tokens = [token for span in _spans(_text().command_map(_help_order())) for token in span.split()]

        self.assertGreater(len([t for t in tokens if t.startswith("--")]), 150)
        for token in tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, SHARED_OPTIONS)
                self.assertNotIn(token, {"-h"})

    def test_numeric_click_types_are_written_as_n_and_choices_are_kept(self) -> None:
        spans = _spans(_text().command_map(_help_order()))

        for span in spans:
            with self.subTest(span=span):
                self.assertNotRegex(span, r"\b(INTEGER|FLOAT)\b")
        joined = "\n".join(spans)
        for item in ("--quality N", "--count N", "--fps N", "--quality [high|balanced|small]"):
            with self.subTest(item=item):
                self.assertIn(item, joined)

    def test_an_option_shows_its_longest_name_and_no_metavar_when_it_is_a_flag(self) -> None:
        span = _all_lines(_text().command_map(["text"]))["text slug"].split("`")[1]

        self.assertIn(" --include-date ", span + " ")
        self.assertNotIn("--date", span.replace("--include-date", ""))
        self.assertNotRegex(span, r"(?<!-)-s\b")
        self.assertIn(" --preserve-case ", span + " ")

    def test_the_compress_commands_say_they_can_reach_a_target_size(self) -> None:
        lines = _all_lines(_text().command_map(["image", "video"]))
        for path in ("image compress", "video compress"):
            with self.subTest(path=path):
                self.assertIn("target size", lines[path])

    def test_each_command_stays_on_one_line_and_there_is_one_span_per_line(self) -> None:
        lines = [
            line for line in _text().command_map(_help_order()).splitlines()
            if line.strip() and not line.startswith("### ")
        ]

        self.assertEqual(len(lines), len(_text().entries()))
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"^- `jzr [^`]+` - \S")

    def test_users_own_bindings_never_appear(self) -> None:
        self.assertTrue((Path(self.config.name) / "bindings.json").is_file())

        self.assertNotIn("mybinding", _text().command_map(_help_order()))

    def test_the_text_has_no_carriage_return(self) -> None:
        self.assertNotIn("\r", _text().command_map(_help_order()))


TAGGED = (
    "head {name}\n"
    "untagged one\n"
    "\n"
    "[media] only media\n"
    "[pdf] only pdf\n"
    "[data] only data\n"
    "[system] only system\n"
    "[media pdf] media and pdf\n"
    "[pdf data system] three names\n"
    "[media pdf data system] every skill, tagged\n"
    "untagged two\n"
    "tail\n"
)
TAGGED_EXPECTED = {
    "jzr-media": ["only media", "media and pdf", "every skill, tagged"],
    "jzr-pdf": ["only pdf", "media and pdf", "three names", "every skill, tagged"],
    "jzr-data": ["only data", "three names", "every skill, tagged"],
    "jzr-system": ["only system", "three names", "every skill, tagged"],
}


class TemplateTests(SkillTextCase):
    def test_name_description_and_commands_are_replaced_in_every_place(self) -> None:
        template = "name: {name}\ndescription: {description}\n# {name}\n{commands}\nend {name}\n"
        for name, groups in GROUPS.items():
            with self.subTest(skill=name):
                text = _text().skill_text(name, template=template)

                lines = text.splitlines()
                self.assertEqual(lines[0], f"name: {name}")
                self.assertEqual(lines[1], f"description: {DESCRIPTIONS[name]}")
                self.assertEqual(lines[2], f"# {name}")
                self.assertEqual(lines[-1], f"end {name}")
                for placeholder in ("{name}", "{description}", "{commands}"):
                    self.assertNotIn(placeholder, text)
                self.assertIn(_text().command_map(groups).strip(), text)

    def test_the_commands_are_the_command_list_of_the_groups_of_that_skill_only(self) -> None:
        for name, groups in GROUPS.items():
            with self.subTest(skill=name):
                text = _text().skill_text(name, template="{commands}\n")

                headings = [h for h, _lines in _parse_blocks(text)]
                self.assertEqual(headings, [f"### {g}" for g in _help_order() if g in groups])
                self.assertEqual(text.strip(), _text().command_map(groups).strip())

    def test_a_tagged_line_is_kept_without_its_tag_and_its_space_only_in_the_skills_named(self) -> None:
        for name, kept in TAGGED_EXPECTED.items():
            with self.subTest(skill=name):
                text = _text().skill_text(name, template=TAGGED)

                self.assertEqual(
                    text.splitlines(),
                    [f"head {name}", "untagged one", "", *kept, "untagged two", "tail"],
                )

    def test_the_tags_may_name_one_two_three_or_all_four_skills(self) -> None:
        counts = {
            tag: [n for n in SKILL_NAMES if tag_line in _text().skill_text(n, template=TAGGED).splitlines()]
            for tag, tag_line in (
                ("one", "only media"),
                ("two", "media and pdf"),
                ("three", "three names"),
                ("four", "every skill, tagged"),
            )
        }

        self.assertEqual(
            {tag: len(names) for tag, names in counts.items()}, {"one": 1, "two": 2, "three": 3, "four": 4}
        )

    def test_a_line_without_a_tag_goes_into_all_four_unchanged_blank_lines_included(self) -> None:
        template = "first\n\n  indented, with [brackets]\n- plain bullet\n\nlast\n"
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(_text().skill_text(name, template=template), template)

    def test_the_text_ends_with_the_one_newline_of_the_template(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = _text().skill_text(name, template=TAGGED)

                self.assertTrue(text.endswith("tail\n"))
                self.assertNotIn("\r", text)

    def test_an_unknown_name_in_a_tag_is_an_error_even_when_other_names_in_it_are_known(self) -> None:
        for tagged in ("[nope] x\n", "[media nope] x\n", "[nope pdf] x\n", "[media pdf datum] x\n"):
            for name in SKILL_NAMES:
                with self.subTest(template=tagged, skill=name), self.assertRaises(ValueError):
                    _text().skill_text(name, template=f"head\n{tagged}tail\n")

    def test_a_tag_names_the_skills_without_the_jzr_prefix(self) -> None:
        text = _text().skill_text("jzr-pdf", template="[pdf] kept\n[media] dropped\n")

        self.assertEqual(text.splitlines(), ["kept"])

    def test_brackets_later_in_a_line_do_not_make_it_a_tag(self) -> None:
        bullet = "- Run `jzr pdf organize [SOURCES]... --rotate [PAGES:]ANGLE` or [media] here"
        template = f"{bullet}\n[pdf] {bullet}\n[media] {bullet}\n"

        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = _text().skill_text(name, template=template)

                expected = [bullet]
                if name in ("jzr-pdf", "jzr-media"):
                    expected.append(bullet)
                self.assertEqual(text.splitlines(), expected)

    def test_the_default_template_is_the_packaged_template_md(self) -> None:
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} is missing")
        template = TEMPLATE.read_text(encoding="utf-8")

        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(
                    _text().skill_text(name), _text().skill_text(name, template=template.replace("\r\n", "\n"))
                )

    def test_a_different_template_changes_the_text_so_the_default_is_not_hard_coded(self) -> None:
        self.assertNotEqual(
            _text().skill_text("jzr-pdf", template="only this\n"), _text().skill_text("jzr-pdf")
        )
        self.assertEqual(_text().skill_text("jzr-pdf", template="only this\n"), "only this\n")

    def test_the_packaged_template_has_the_placeholders_and_only_valid_tags(self) -> None:
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} is missing")
        template = TEMPLATE.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = template.splitlines()

        self.assertEqual(lines.count("{commands}"), 1)
        self.assertIn("name: {name}", lines)
        self.assertTrue(any(line.startswith("description: {description}") for line in lines))
        self.assertEqual(set(re.findall(r"\{(\w+)\}", template)), {"name", "description", "commands"})
        used: set[str] = set()
        for line in lines:
            match = re.match(r"\[([^\]]*)\] ", line)
            if match:
                used.update(match.group(1).split())
        self.assertEqual(used, {n.removeprefix("jzr-") for n in SKILL_NAMES})


def _sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^## ", text)
    return [(part.split("\n", 1)[0], part.split("\n", 1)[1] if "\n" in part else "") for part in parts[1:]]


class ReferenceTests(SkillTextCase):
    def _stems(self) -> list[str]:
        seen: list[str] = []
        for entry in _text().entries():
            if entry.group not in seen:
                seen.append(entry.group)
        return seen

    def test_title_is_the_group_or_the_top_level_title(self) -> None:
        for stem in self._stems():
            with self.subTest(stem=stem):
                first = _text().reference(stem).splitlines()[0]
                self.assertEqual(first, TOOLS_TITLE if stem == "tools" else f"# jzr {stem}")

    def test_one_section_per_command_in_cli_order(self) -> None:
        for stem in self._stems():
            with self.subTest(stem=stem):
                headings = [heading for heading, _body in _sections(_text().reference(stem))]
                expected = [f"jzr {e.path}" for e in _text().entries() if e.group == stem]
                self.assertEqual(headings, expected)

    def test_each_section_holds_the_help_in_a_text_block_then_the_examples(self) -> None:
        examples = _examples()
        for stem in self._stems():
            sections = dict(_sections(_text().reference(stem)))
            for entry in (e for e in _text().entries() if e.group == stem):
                with self.subTest(path=entry.path):
                    body = sections[f"jzr {entry.path}"]
                    block = "```text\n" + _text().help_text(entry).rstrip("\n") + "\n```\n"
                    self.assertIn(block, body)
                    after = body.split(block, 1)[1]
                    self.assertIn("Examples:", after)
                    listed = [line for line in after.split("Examples:", 1)[1].splitlines() if line.strip()]
                    expected = [f"- `{line}` - {what}" for line, what in examples[entry.path]]
                    self.assertEqual(listed, expected)

    def test_the_text_is_lf_only_and_ends_in_exactly_one_newline(self) -> None:
        for stem in self._stems():
            with self.subTest(stem=stem):
                text = _text().reference(stem)
                self.assertNotIn("\r", text)
                self.assertTrue(text.endswith("\n"))
                self.assertFalse(text.endswith("\n\n"))



class BuiltCase(SkillTextCase):
    """A case that looks at what `build()` makes (not at what was last committed)."""

    _built: dict[str, dict[str, str]] | None = None

    @classmethod
    def built(cls) -> dict[str, dict[str, str]]:
        if cls._built is None:
            cls._built = _text().build()
        return cls._built

    def skill_md(self, name: str) -> str:
        return self.built()[name]["SKILL.md"]


def _expected_names(name: str) -> set[str]:
    return {"SKILL.md"} | {f"reference/{group}.md" for group in GROUPS[name]}


def _rules(text: str) -> str:
    return text[text.index("## Rules"):]


def _bullets(text: str, needle: str) -> list[str]:
    return [line for line in _rules(text).splitlines() if line.startswith("- ") and needle in line]


def _files_under(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


class BuildTests(BuiltCase):
    def test_build_is_skill_by_skill_and_path_by_path_text(self) -> None:
        built = self.built()

        self.assertEqual(list(built), list(SKILL_NAMES))
        for name, files in built.items():
            self.assertIsInstance(files, dict)
            for path, text in files.items():
                with self.subTest(skill=name, file=path):
                    self.assertIsInstance(path, str)
                    self.assertIsInstance(text, str)

    def test_each_skill_has_skill_md_and_one_reference_file_for_each_of_its_own_groups_only(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(set(self.built()[name]), _expected_names(name))

    def test_the_skills_share_no_reference_file_and_together_have_one_for_every_group(self) -> None:
        references = [
            path for files in self.built().values() for path in files if path.startswith("reference/")
        ]

        self.assertEqual(sorted(references), sorted(f"reference/{g}.md" for g in _help_order()))
        self.assertEqual(len(references), len(set(references)))

    def test_the_reference_files_are_what_reference_returns(self) -> None:
        for name, groups in GROUPS.items():
            for group in groups:
                with self.subTest(skill=name, group=group):
                    self.assertEqual(self.built()[name][f"reference/{group}.md"], _text().reference(group))

    def test_skill_md_is_what_skill_text_returns(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(self.skill_md(name), _text().skill_text(name))

    def test_every_text_is_lf_only_and_ends_in_exactly_one_newline(self) -> None:
        for name, files in self.built().items():
            for path, text in files.items():
                with self.subTest(skill=name, file=path):
                    self.assertNotIn("\r", text)
                    self.assertTrue(text.endswith("\n"))
                    self.assertFalse(text.endswith("\n\n"))

    def test_the_four_skill_files_differ_from_each_other(self) -> None:
        self.assertEqual(len({self.skill_md(name) for name in SKILL_NAMES}), 4)

    def test_users_own_bindings_never_appear_anywhere(self) -> None:
        self.assertTrue((Path(self.config.name) / "bindings.json").is_file())

        for name, files in self.built().items():
            for path, text in files.items():
                with self.subTest(skill=name, file=path):
                    self.assertNotIn("mybinding", text)


class SkillMdTests(BuiltCase):
    def _front_matter(self, text: str) -> dict[str, str]:
        lines = text.split("\n")
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        found: dict[str, str] = {}
        for line in lines[1:end]:
            self.assertRegex(line, r"^[a-z-]+: \S", f"front matter line is not one 'key: value' line: {line!r}")
            key, _, value = line.partition(": ")
            found[key] = value
        return found

    def _commands_section(self, text: str) -> str:
        return text[text.index("## Commands"):text.index("## Rules")]

    def test_front_matter_has_the_name_and_the_description_of_the_skill_and_nothing_else(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                meta = self._front_matter(self.skill_md(name))

                self.assertEqual(list(meta), ["name", "description"])
                self.assertEqual(meta["name"], name)
                self.assertEqual(meta["description"], DESCRIPTIONS[name])
                self.assertLess(len(meta["description"]), 400)
                self.assertNotIn(meta["description"][0], ">|")

    def test_the_title_is_the_name_and_the_only_sections_are_commands_and_rules(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                self.assertEqual(re.findall(r"(?m)^# (.*)$", text), [name])
                self.assertEqual(re.findall(r"(?m)^## (.*)$", text), ["Commands", "Rules"])
                self.assertLess(text.index("# " + name), text.index("## Commands"))

    def test_the_old_sections_are_gone(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                self.assertNotIn("Leave these to the user", text)
                self.assertNotIn("When not to use jzr", text)

    def test_nothing_of_the_template_is_left_over(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                for placeholder in ("{name}", "{description}", "{commands}"):
                    self.assertNotIn(placeholder, text)
                self.assertNotRegex(text, r"(?m)^\[[a-z ]+\] ")
                self.assertNotIn("<!-- commands", text)
                self.assertNotIn("jzr --version", text)

    def test_each_skill_is_smaller_than_the_split_skill_of_the_same_name_in_the_bench(self) -> None:
        for name, limit in SIZE_LIMITS.items():
            with self.subTest(skill=name):
                self.assertLess(len(self.skill_md(name)), limit)

    def test_each_skill_stays_under_150_lines(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertLess(len(self.skill_md(name).splitlines()), 150)

    def test_the_intro_says_what_to_do_when_jzr_is_not_found(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                self.assertIn("uvx --from jzr-tools jzr <command>", text)
                self.assertIn("uv tool install jzr-tools", text)
                self.assertLess(text.index("uvx --from jzr-tools jzr"), text.index("## Commands"))

    def test_the_commands_section_is_the_command_list_of_the_groups_of_that_skill(self) -> None:
        shown = 0
        for name, groups in GROUPS.items():
            with self.subTest(skill=name):
                section = self._commands_section(self.skill_md(name))
                listed = section[section.index("### "):]

                self.assertEqual(_parse_blocks(listed), _expected_blocks(groups))
                self.assertIn(_text().command_map(groups).strip(), section)
                shown += len(_spans(listed))
        self.assertEqual(shown, len(_walk()))

    def test_no_line_of_the_command_list_links_a_reference_file(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertNotIn("](reference/", self.skill_md(name))

    def test_the_intro_of_the_commands_section_names_the_help_command_and_the_reference_files(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                section = self._commands_section(self.skill_md(name))
                intro = section[:section.index("### ")]

                self.assertIn("`jzr <command> -h`", intro)
                self.assertIn("`reference/<group>.md`", intro)
                self.assertIn("worked examples", intro)
                self.assertRegex(intro, r"(?i)\bown arguments and options\b")
                self.assertNotIn("shows no options", intro)
                self.assertNotIn("Before using", intro)

    def test_the_commands_of_a_skill_are_not_listed_in_another_skill(self) -> None:
        for name, groups in GROUPS.items():
            others = [heading for other in SKILL_NAMES if other != name for heading in self._headings(other)]
            with self.subTest(skill=name):
                self.assertEqual(
                    self._headings(name), [f"### {g}" for g in _help_order() if g in groups]
                )
                self.assertEqual(set(self._headings(name)) & set(others), set())

    def _headings(self, name: str) -> list[str]:
        return re.findall(r"(?m)^### .*$", self.skill_md(name))


# What the Rules section of each skill must say, and what must appear nowhere in it.
MUST = {
    "jzr-media": [
        "Sources stay as they are", "`--overwrite`", "`--outdir DIR`", "`-o FILE`", "source -> target",
        "`--dir`", "`-r`", "-p REGEX", "--dry-run", "jzr audio tags", "jzr ffmpeg status",
        "jzr ffmpeg install", "jzr ffmpeg remove", "crop",
    ],
    "jzr-pdf": [
        "Sources stay as they are", "`--overwrite`", "`--outdir DIR`", "`-o FILE`", "source -> target",
        "`--dir`", "`-r`", "-p REGEX", "--dry-run", "PAGES", "jzr pdf split --pages",
        "does not change what is on a page",
    ],
    "jzr-data": [
        "`--dir`", "`-r`", "-p REGEX", "--types", "jzr data convert", "jzr file rename", "`-y`",
        "jzr data json -w", "jzr text diff", "jzr env compare", "jzr jwt", "--secret-env", "--secret-file",
        "Quote", "does not edit spreadsheets", "`-o FILE`", "stdout", "never redirect", "`wrote ...`",
    ],
    "jzr-system": [
        "jzr port kill", "jzr port show PORT", "jzr host clean", "jzr host clean --dry-run",
        "jzr host procs --kill", "jzr bind add", "jzr bind remove", "jzr completion install",
        "jzr completion remove",
    ],
}
MUST_NOT = {
    "jzr-media": [
        "PAGES", "--types", "jzr port", "jzr host", "jzr bind", "jzr completion", "jzr jwt",
        "jzr file rename", "jzr data", "jzr text", "jzr env", "jzr pdf", "Quote", "spreadsheets",
    ],
    "jzr-pdf": [
        "ffmpeg", "--types", "jzr port", "jzr host", "jzr bind", "jzr completion", "jzr jwt",
        "jzr file rename", "jzr data", "jzr audio", "jzr image", "jzr video", "crop", "Quote", "spreadsheets",
    ],
    "jzr-data": [
        "--overwrite", "--outdir", "--dry-run", "PAGES", "ffmpeg", "jzr port", "jzr host",
        "jzr bind", "jzr completion", "Sources stay as they are", "source -> target", "jzr audio",
        "jzr image", "jzr video", "jzr pdf", "crop",
    ],
    "jzr-system": [
        "--overwrite", "--outdir", "`-o FILE`", "--dir", "PAGES", "--types", "ffmpeg", "jzr jwt",
        "jzr file rename", "Sources stay as they are", "source -> target", "Quote", "spreadsheets", "crop",
        "jzr image", "jzr video", "jzr audio", "jzr pdf", "jzr data", "jzr text", "jzr env",
    ],
}


class RulesTests(BuiltCase):
    def test_each_skill_says_what_it_must_in_its_rules(self) -> None:
        for name, phrases in MUST.items():
            rules = _rules(self.skill_md(name))
            for phrase in phrases:
                with self.subTest(skill=name, phrase=phrase):
                    self.assertIn(phrase, rules)

    def test_each_skill_says_nothing_about_the_jobs_of_the_others_anywhere_in_the_file(self) -> None:
        for name, phrases in MUST_NOT.items():
            text = self.skill_md(name)
            for phrase in phrases:
                with self.subTest(skill=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_do_not_guess_options_is_in_all_four(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertIn("Do not guess options", _rules(self.skill_md(name)))
                self.assertEqual(len(_bullets(self.skill_md(name), "Do not guess options")), 1)

    def test_the_json_flag_and_the_exit_codes_are_in_all_four(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                self.assertEqual(len(_bullets(text, "`--json`")), 1)
                exit_lines = [line for line in _rules(text).splitlines() if re.search(r"exit code", line, re.I)]
                self.assertTrue(exit_lines, "no line about exit codes")
                for code in ("0", "1", "2"):
                    self.assertRegex(" ".join(exit_lines), rf"(?<!\d){code}(?!\d)")

    def test_every_rule_is_one_bullet_and_there_are_no_blank_lines_or_other_text_in_the_section(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                lines = _rules(self.skill_md(name)).splitlines()[1:]
                body = [line for line in lines if line.strip()]

                self.assertGreater(len(body), 3)
                for line in body:
                    self.assertTrue(line.startswith("- "), line)

    def test_the_media_and_pdf_skills_tell_to_read_the_result_line_instead_of_checking_the_file(self) -> None:
        for name in ("jzr-media", "jzr-pdf"):
            with self.subTest(skill=name):
                (rule,) = _bullets(self.skill_md(name), "source -> target")
                self.assertRegex(rule, r"(?i)\bread that line\b")
                self.assertRegex(rule, r"(?i)\bsize\b")

    def test_the_other_skills_do_not_have_that_rule(self) -> None:
        for name in ("jzr-data", "jzr-system"):
            with self.subTest(skill=name):
                self.assertNotIn("read that line", self.skill_md(name))

    def test_only_the_media_skill_says_how_to_get_ffmpeg_and_not_to_run_install_or_remove_itself(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-media"), "jzr ffmpeg install")
        self.assertIn("jzr ffmpeg status", rule)
        self.assertIn("jzr ffmpeg remove", rule)
        self.assertRegex(rule, r"(?i)\bsuggest\b")
        for name in ("jzr-pdf", "jzr-data", "jzr-system"):
            with self.subTest(skill=name):
                self.assertNotIn("ffmpeg", self.skill_md(name))

    def test_only_the_media_skill_says_audio_tags_change_the_file_in_place(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-media"), "jzr audio tags")
        self.assertIn("in place", rule)
        for name in ("jzr-pdf", "jzr-data", "jzr-system"):
            with self.subTest(skill=name):
                self.assertNotIn("jzr audio tags", self.skill_md(name))

    def test_only_the_data_skill_explains_types_and_it_does_so_in_one_rule(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-data"), "--types")
        self.assertIn("jzr data convert", rule)
        self.assertIn("CSV", rule)
        self.assertRegex(rule, r"(?i)\bnumbers\b")
        self.assertRegex(rule, r"(?i)\bExcel\b")
        for name in ("jzr-media", "jzr-pdf", "jzr-system"):
            with self.subTest(skill=name):
                self.assertNotIn("--types", self.skill_md(name))

    def test_the_data_skill_says_which_commands_change_files_in_place(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-data"), "jzr file rename")
        self.assertIn("jzr data json -w", rule)
        self.assertIn("`-y`", rule)
        self.assertIn("in place", rule)

    def test_the_data_skill_keeps_the_secret_off_the_command_line(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-data"), "--secret-env")
        self.assertRegex(rule, r"(?i)\bnever\b")
        self.assertIn("--secret-file", rule)

    def test_the_exit_one_for_a_difference_is_only_in_the_data_skill(self) -> None:
        (rule,) = _bullets(self.skill_md("jzr-data"), "jzr env compare")
        self.assertIn("jzr text diff", rule)
        self.assertRegex(rule, r"exit 1")
        self.assertRegex(rule, r"\b2\b")

    def test_only_the_system_skill_makes_the_user_decide_about_ending_and_changing_things(self) -> None:
        text = self.skill_md("jzr-system")
        (kill,) = _bullets(text, "jzr port kill")
        self.assertIn("without asking", kill)
        self.assertRegex(kill, r"Run `jzr port show PORT` first")
        (clean,) = _bullets(text, "jzr host procs --kill")
        self.assertIn("jzr host clean", clean)
        self.assertRegex(clean, r"(?i)\bsuggest\b")
        (ask,) = _bullets(text, "jzr bind add")
        self.assertRegex(ask, r"(?i)^- Ask before\b")
        for command in ("jzr bind remove", "jzr completion install", "jzr completion remove"):
            with self.subTest(command=command):
                self.assertIn(command, ask)

    def test_the_limits_of_jzr_are_each_in_the_skill_they_belong_to(self) -> None:
        for name, words in (
            ("jzr-media", ("crop", "rotate", "join", "watermark")),
            ("jzr-pdf", ("does not change what is on a page",)),
            ("jzr-data", ("does not edit spreadsheets",)),
        ):
            for word in words:
                with self.subTest(skill=name, word=word):
                    self.assertTrue(
                        [b for b in _bullets(self.skill_md(name), word) if "jzr does not" in b], word
                    )
        self.assertEqual(_bullets(self.skill_md("jzr-system"), "jzr does not"), [])


class ScanRuleTests(BuiltCase):
    """The Rules lines about running without SOURCES, in the skills that have batch commands."""

    def _scan_rule(self, name: str) -> str:
        bullets = _bullets(self.skill_md(name), "--dir")
        self.assertEqual(len(bullets), 1, bullets)
        return bullets[0]

    def _dry_run_rule(self, name: str) -> str:
        bullets = _bullets(self.skill_md(name), "--dry-run")
        self.assertEqual(len(bullets), 1, bullets)
        return bullets[0]

    def _sentences(self, name: str) -> list[str]:
        return re.split(r"(?<=[.;])\s+", self._dry_run_rule(name))

    def test_no_skill_tells_agents_to_dry_run_every_scan_first(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                text = self.skill_md(name)

                self.assertNotIn("Run such a scan with `--dry-run` first", text)
                self.assertNotRegex(text, r"(?i)scan with `--dry-run` first")

    def test_the_skills_with_batch_commands_keep_the_facts_about_the_folder_scan(self) -> None:
        for name in ("jzr-media", "jzr-pdf", "jzr-data"):
            with self.subTest(skill=name):
                rule = self._scan_rule(name)

                self.assertRegex(rule, r"(?i)\b(no|without)\s+SOURCES\b")
                self.assertIn("every matching file in `--dir`", rule)
                self.assertIn("current folder", rule)
                self.assertIn("`-r`", rule)
                self.assertIn("subfolders", rule)
                self.assertIn("-p REGEX", rule)

    def test_media_and_pdf_say_files_named_as_sources_need_no_dry_run(self) -> None:
        no_dry_run = (
            r"(?i)\bno\s+`?(?:--)?dry[- ]run"
            r"|dry[- ]run`?\s+(?:is\s+)?(?:not needed|not necessary|unnecessary|optional)"
            r"|\b(?:not|n't)\s+(?:need|needed|necessary)\b[^.]*dry[- ]run"
            r"|without a\s+`?(?:--)?dry[- ]run"
        )
        named = r"(?i)\b(named|given|listed|SOURCES?|files?|paths?|arguments?)\b"
        for name in ("jzr-media", "jzr-pdf"):
            with self.subTest(skill=name):
                found = [s for s in self._sentences(name) if re.search(no_dry_run, s)]

                self.assertTrue(found, f"no sentence says a dry run is not needed: {self._dry_run_rule(name)!r}")
                self.assertTrue([s for s in found if re.search(named, s)], found)

    def test_media_and_pdf_keep_dry_run_for_previewing_a_scan_that_may_catch_unwanted_files(self) -> None:
        for name in ("jzr-media", "jzr-pdf"):
            with self.subTest(skill=name):
                for sentence in self._sentences(name):
                    if (
                        "--dry-run" in sentence
                        and re.search(r"(?i)\b(scan|preview\w*|folder)\b|--dir", sentence)
                        and re.search(r"(?i)\b(may|might|if|when|unsure|unless)\b", sentence)
                    ):
                        break
                else:
                    self.fail(f"no sentence offers --dry-run for a scan that may catch unwanted files: {name}")

    def test_the_data_skill_has_the_scan_rule_but_no_dry_run_anywhere(self) -> None:
        self.assertNotIn("dry-run", self.skill_md("jzr-data"))
        self.assertNotIn("dry run", self.skill_md("jzr-data"))

    def test_the_system_skill_has_no_scan_rule(self) -> None:
        self.assertEqual(_bullets(self.skill_md("jzr-system"), "--dir"), [])
        self.assertNotIn("SOURCES", self.skill_md("jzr-system"))


class PageListRuleTests(BuiltCase):
    """A Rules line explains PAGES, so agents need neither `-h` nor a page count first."""

    def _rule(self) -> str:
        bullets = _bullets(self.skill_md("jzr-pdf"), "PAGES")
        self.assertEqual(len(bullets), 1, bullets)
        return bullets[0]

    def test_it_shows_numbers_ranges_and_open_ranges(self) -> None:
        rule = self._rule()
        self.assertIn("`1-3,5`", rule)
        self.assertRegex(rule, r"`\d+-`")

    def test_it_explains_last(self) -> None:
        rule = self._rule()
        self.assertRegex(rule, r"`\d+-last`")
        self.assertRegex(rule, r"`last` is the last page")

    def test_it_says_how_split_groups_are_separated(self) -> None:
        self.assertRegex(self._rule(), r"`;`")

    def test_every_page_example_in_it_parses(self) -> None:
        from jzr_tools.core.pdfdoc import parse_pages

        examples = re.findall(r"`([0-9last,;-]+)`", self._rule())
        self.assertTrue(examples)
        for example in examples:
            if example == ";":
                continue
            with self.subTest(example=example):
                parse_pages(example, 12, groups=True)

    def test_the_other_skills_do_not_explain_pages(self) -> None:
        for name in ("jzr-media", "jzr-data", "jzr-system"):
            with self.subTest(skill=name):
                self.assertNotIn("PAGES", self.skill_md(name))
                self.assertNotIn("2-last", self.skill_md(name))


class CommittedFilesTests(BuiltCase):
    def test_the_template_is_packaged_and_the_old_single_skill_folder_is_gone(self) -> None:
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} is missing")
        self.assertFalse(OLD_SKILL.exists(), f"{OLD_SKILL} must be deleted")
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertTrue((SKILLS_DIR / name).is_dir(), f"{SKILLS_DIR / name} is missing; {REGENERATE}")

    def test_each_packaged_skill_folder_holds_exactly_the_built_files(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(set(_files_under(SKILLS_DIR / name)), set(self.built()[name]), REGENERATE)

    @_needs_known_click
    def test_the_packaged_files_equal_what_build_produces(self) -> None:
        for name, files in self.built().items():
            for path, text in files.items():
                with self.subTest(skill=name, file=path):
                    file = SKILLS_DIR / name / path
                    self.assertTrue(file.is_file(), f"{file} is missing; {REGENERATE}")
                    committed = file.read_bytes().decode("utf-8").replace("\r\n", "\n")
                    self.assertEqual(committed, text, f"{name}/{path} is out of date; {REGENERATE}")

    @_needs_known_click
    def test_write_into_a_copy_of_the_packaged_folder_reports_nothing_when_it_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "skill"
            shutil.copytree(SKILLS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))

            changed = _text().write(copy)

            self.assertEqual(changed, [], REGENERATE)


class WriteTests(BuiltCase):
    def _paths(self, folder: Path) -> set[Path]:
        return {
            (folder / name / path).resolve() for name, files in self.built().items() for path in files
        }

    def _folder(self) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="jzr-skill-write-")
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / "skill"
        folder.mkdir()
        return folder

    def test_write_into_an_empty_folder_creates_the_four_skills_in_lf_and_lists_every_file(self) -> None:
        folder = self._folder()

        changed = _text().write(folder)

        self.assertEqual({Path(p).resolve() for p in changed}, self._paths(folder))
        self.assertEqual(len(changed), sum(len(files) for files in self.built().values()))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(SKILL_NAMES))
        for name, files in self.built().items():
            for path, text in files.items():
                with self.subTest(skill=name, file=path):
                    self.assertEqual((folder / name / path).read_bytes(), text.encode("utf-8"))

    def test_write_does_not_put_the_template_into_the_folder(self) -> None:
        folder = self._folder()

        _text().write(folder)

        self.assertFalse((folder / "template.md").exists())

    def test_a_second_write_changes_nothing_and_returns_an_empty_list(self) -> None:
        folder = self._folder()
        _text().write(folder)
        before = {
            path.relative_to(folder).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in folder.rglob("*") if path.is_file()
        }
        self.assertGreater(len(before), 4)

        changed = _text().write(folder)

        self.assertEqual(changed, [])
        after = {
            path.relative_to(folder).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in folder.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    def test_write_restores_a_damaged_and_a_missing_file(self) -> None:
        folder = self._folder()
        _text().write(folder)
        damaged = folder / "jzr-media" / "reference" / "image.md"
        damaged.write_text("broken\n", encoding="utf-8")
        missing = folder / "jzr-pdf" / "SKILL.md"
        missing.unlink()

        changed = _text().write(folder)

        self.assertEqual({Path(p).resolve() for p in changed}, {damaged.resolve(), missing.resolve()})
        self.assertEqual(damaged.read_bytes(), self.built()["jzr-media"]["reference/image.md"].encode("utf-8"))
        self.assertEqual(missing.read_bytes(), self.built()["jzr-pdf"]["SKILL.md"].encode("utf-8"))

    def test_write_deletes_files_inside_the_four_skill_folders_that_the_build_no_longer_has(self) -> None:
        folder = self._folder()
        _text().write(folder)
        stale = [
            folder / "jzr-media" / "reference" / "old.md",
            folder / "jzr-data" / "notes.txt",
            # A reference file that belongs to another skill.
            folder / "jzr-pdf" / "reference" / "image.md",
            folder / "jzr-system" / "reference" / "deeper" / "x.md",
        ]
        for path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale\n", encoding="utf-8")

        changed = _text().write(folder)

        for path in stale:
            with self.subTest(file=path.relative_to(folder).as_posix()):
                self.assertFalse(path.exists())
        # Whether a deleted file is listed among the changed ones is not fixed; a rewrite of a current one is.
        self.assertEqual({Path(p).resolve() for p in changed} - {p.resolve() for p in stale}, set())
        self.assertEqual(
            {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()},
            {f"{name}/{path}" for name, files in self.built().items() for path in files},
        )

    def test_write_leaves_the_template_and_every_unrelated_file_of_the_folder_alone(self) -> None:
        folder = self._folder()
        _text().write(folder)
        keep = {
            "template.md": "my template {name}\n",
            "notes.txt": "keep me\n",
            "other-skill/SKILL.md": "not a jzr skill\n",
            "other-skill/reference/x.md": "keep me too\n",
            "jzr-extra/SKILL.md": "a folder that is not one of the four\n",
        }
        for name, text in keep.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (folder / "jzr-media" / "reference" / "old.md").write_text("stale\n", encoding="utf-8")

        _text().write(folder)

        for name, text in keep.items():
            with self.subTest(file=name):
                self.assertEqual((folder / name).read_text(encoding="utf-8"), text)
        self.assertFalse((folder / "jzr-media" / "reference" / "old.md").exists())

    def test_write_builds_from_the_packaged_template_not_from_one_in_the_folder(self) -> None:
        folder = self._folder()
        (folder / "template.md").write_text("only this\n", encoding="utf-8")

        _text().write(folder)

        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertEqual(
                    (folder / name / "SKILL.md").read_text(encoding="utf-8"), self.skill_md(name)
                )

    def test_a_file_a_windows_checkout_gave_crlf_endings_counts_as_current(self) -> None:
        folder = self._folder()
        _text().write(folder)
        path = folder / "jzr-data" / "SKILL.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

        changed = _text().write(folder)

        self.assertEqual(changed, [])


class ExamplesTests(SkillTextCase):
    def _command(self, path: str) -> tuple[click.Command, click.Context]:
        for names, command, ctx in _walk():
            if " ".join(names) == path:
                return command, ctx
        self.fail(f"no command {path}")

    def test_the_keys_are_exactly_the_entry_paths(self) -> None:
        self.assertEqual(set(_examples()), {e.path for e in _text().entries()})

    def test_each_command_has_one_to_four_examples_and_sources_have_at_least_two(self) -> None:
        for path, items in _examples().items():
            with self.subTest(path=path):
                self.assertGreaterEqual(len(items), 1)
                self.assertLessEqual(len(items), 4)
                if path in SOURCE_COMMANDS:
                    self.assertGreaterEqual(len(items), 2)

    def test_every_source_command_is_covered(self) -> None:
        self.assertEqual(SOURCE_COMMANDS - set(_examples()), set())

    def test_lines_start_with_jzr_and_their_own_path_and_split_like_a_shell(self) -> None:
        for path, items in _examples().items():
            for line, _what in items:
                with self.subTest(line=line):
                    words = shlex.split(line)
                    self.assertEqual(words[0], "jzr")
                    self.assertEqual(words[1:1 + len(path.split())], path.split())
                    self.assertNotIn("\\", line)
                    self.assertNotIn("\n", line)

    def test_descriptions_are_plain_sentences_without_a_closing_full_stop(self) -> None:
        for path, items in _examples().items():
            for line, what in items:
                with self.subTest(line=line):
                    self.assertIsInstance(what, str)
                    self.assertTrue(what.strip())
                    self.assertEqual(what, what.strip())
                    self.assertFalse(what.endswith("."), what)

    def test_lines_are_unique_within_a_command(self) -> None:
        for path, items in _examples().items():
            with self.subTest(path=path):
                lines = [line for line, _what in items]
                self.assertEqual(len(set(lines)), len(lines))

    def test_every_example_parses_against_its_real_command(self) -> None:
        for path, items in _examples().items():
            command, ctx = self._command(path)
            for line, _what in items:
                with self.subTest(line=line):
                    arguments = shlex.split(line)[1 + len(path.split()):]
                    self._check(command, ctx, arguments)

    def _check(self, command: click.Command, ctx: click.Context, arguments: list[str]) -> None:
        parser = command.make_parser(ctx)
        try:
            opts, left_over, _order = parser.parse_args(args=list(arguments))
        except click.ClickException as error:
            self.fail(f"does not parse: {error.format_message()}")
        self.assertEqual(left_over, [], f"too many arguments: {left_over}")

        for param in command.params:
            given = opts.get(param.name)
            # Click 8.3 stores a marker object for an argument that was left out.
            present = given not in (None, ()) and type(given).__name__ != "Sentinel"
            if param.required:
                self.assertTrue(present, f"required {param.name} is missing")
            if not present:
                continue
            if isinstance(param.type, (click.Path, click.File)):
                continue
            if getattr(param, "is_flag", False) or getattr(param, "count", False):
                continue
            try:
                value = param.type_cast_value(ctx, given)
                if param.callback is not None and not _holds_path(value):
                    param.callback(ctx, param, value)
            except click.ClickException as error:
                self.fail(f"{param.name}: {error.format_message()}")


def _holds_path(value: object) -> bool:
    if isinstance(value, Path):
        return True
    if isinstance(value, (tuple, list)):
        return any(isinstance(item, Path) for item in value)
    return False


if __name__ == "__main__":
    unittest.main()


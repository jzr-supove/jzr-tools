"""Tests for the generated skill texts, the worked examples and the packaged skill folder.

The Click tree is walked here on its own, so the code under test is checked
against the real commands and not against itself.
"""

from __future__ import annotations

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

PACKAGED = Path(jzr_tools.__file__).resolve().parent / "data" / "skill" / "jzr"
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
        self.assertNotIn("mybinding", module.command_map())
        for name, text in module.build().items():
            with self.subTest(file=name):
                self.assertNotIn("mybinding", text)

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


class CommandMapTests(SkillTextCase):
    def _parse(self) -> list[tuple[str, list[str]]]:
        blocks: list[tuple[str, list[str]]] = []
        for line in _text().command_map().splitlines():
            if not line.strip():
                continue
            if line.startswith("### "):
                blocks.append((line, []))
            else:
                self.assertTrue(blocks, f"a line before any heading: {line!r}")
                blocks[-1][1].append(line)
        return blocks

    def test_one_heading_per_stem_that_links_its_reference_file(self) -> None:
        stems = {entry.group for entry in _text().entries()}

        headings = [heading for heading, _lines in self._parse()]

        self.assertEqual(len(headings), len(stems))
        found = set()
        for heading in headings:
            match = re.fullmatch(r"### (\S+) \(\[reference/(\S+)\.md\]\(reference/(\S+)\.md\)\)", heading)
            self.assertIsNotNone(match, heading)
            self.assertEqual(match.group(1), match.group(2))
            self.assertEqual(match.group(2), match.group(3))
            found.add(match.group(1))
        self.assertEqual(found, stems)

    def test_the_compress_commands_say_they_can_reach_a_target_size(self) -> None:
        lines = _text().command_map().splitlines()
        for path in ("image compress", "video compress"):
            with self.subTest(path=path):
                (line,) = [line for line in lines if line.startswith(f"- `jzr {path}` ")]
                self.assertIn("target size", line)

    def test_each_command_has_one_line_with_its_short_help_under_its_own_heading(self) -> None:
        by_stem: dict[str, list[str]] = {}
        for names, command, _ctx in _walk():
            help_line = command.get_short_help_str(limit=300)
            by_stem.setdefault(_stem(names), []).append(f"- `jzr {' '.join(names)}` - {help_line}")

        actual = {
            heading.split()[1]: lines for heading, lines in self._parse()
        }

        self.assertEqual(actual, by_stem)

    def test_the_text_has_no_carriage_return(self) -> None:
        self.assertNotIn("\r", _text().command_map())


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


class BuildAndWriteTests(SkillTextCase):
    def _expected_names(self) -> set[str]:
        return {"SKILL.md"} | {f"reference/{e.group}.md" for e in _text().entries()}

    def test_build_returns_skill_md_and_one_reference_file_per_stem(self) -> None:
        built = _text().build()

        self.assertEqual(set(built), self._expected_names())
        for name, text in built.items():
            with self.subTest(file=name):
                self.assertIsInstance(text, str)
                self.assertNotIn("\r", text)
                self.assertTrue(text.endswith("\n"))
                self.assertFalse(text.endswith("\n\n"))

    def test_build_fills_the_marked_block_of_skill_md_with_the_command_map(self) -> None:
        skill = _text().build()["SKILL.md"]

        start = skill.index("<!-- commands:start -->") + len("<!-- commands:start -->")
        end = skill.index("<!-- commands:end -->")

        self.assertLess(start, end)
        self.assertEqual(skill[start:end].strip(), _text().command_map().strip())

    def test_build_reference_files_are_what_reference_returns(self) -> None:
        built = _text().build()
        for stem in {e.group for e in _text().entries()}:
            with self.subTest(stem=stem):
                self.assertEqual(built[f"reference/{stem}.md"], _text().reference(stem))

    def test_the_packaged_folder_holds_exactly_the_built_files(self) -> None:
        self.assertTrue(PACKAGED.is_dir(), f"{PACKAGED} is missing")
        present = {
            path.relative_to(PACKAGED).as_posix() for path in PACKAGED.rglob("*") if path.is_file()
        }

        self.assertEqual(present, self._expected_names(), REGENERATE)

    @_needs_known_click
    def test_the_packaged_files_equal_what_build_produces(self) -> None:
        for name, text in _text().build().items():
            with self.subTest(file=name):
                path = PACKAGED / name
                self.assertTrue(path.is_file(), f"{path} is missing; {REGENERATE}")
                committed = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
                self.assertEqual(committed, text, f"{name} is out of date; {REGENERATE}")

    def test_the_marked_block_of_the_committed_skill_md_is_the_current_command_map(self) -> None:
        committed = (PACKAGED / "SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n")
        start = committed.index("<!-- commands:start -->") + len("<!-- commands:start -->")
        end = committed.index("<!-- commands:end -->")

        self.assertEqual(committed[start:end].strip(), _text().command_map().strip(), REGENERATE)

    def test_write_into_an_empty_folder_creates_every_file_in_lf_and_lists_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "jzr"
            folder.mkdir()

            changed = _text().write(folder)

            built = _text().build()
            self.assertEqual({Path(p).resolve() for p in changed},
                             {(folder / name).resolve() for name in built})
            self.assertEqual(len(changed), len(built))
            for name, text in built.items():
                with self.subTest(file=name):
                    self.assertEqual((folder / name).read_bytes(), text.encode("utf-8"))

    def test_write_restores_a_damaged_file_and_a_missing_one_and_removes_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "jzr"
            folder.mkdir()
            _text().write(folder)
            built = _text().build()
            damaged = folder / "reference" / "image.md"
            damaged.write_text("broken\n", encoding="utf-8")
            (folder / "SKILL.md").unlink()
            stray = folder / "notes.txt"
            stray.write_text("keep me\n", encoding="utf-8")
            other = folder / "reference" / "extra.md"
            other.write_text("keep me too\n", encoding="utf-8")

            changed = _text().write(folder)

            self.assertEqual(
                {Path(p).resolve() for p in changed},
                {damaged.resolve(), (folder / "SKILL.md").resolve()},
            )
            self.assertEqual(damaged.read_bytes(), built["reference/image.md"].encode("utf-8"))
            self.assertEqual((folder / "SKILL.md").read_bytes(), built["SKILL.md"].encode("utf-8"))
            self.assertEqual(stray.read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual(other.read_text(encoding="utf-8"), "keep me too\n")

    def test_a_second_write_changes_nothing_and_returns_an_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "jzr"
            folder.mkdir()
            _text().write(folder)
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in folder.rglob("*") if path.is_file()
            }

            changed = _text().write(folder)

            self.assertEqual(changed, [])
            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in folder.rglob("*") if path.is_file()
            }
            self.assertEqual(after, before)

    def test_write_into_a_copy_of_the_packaged_folder_reports_nothing_when_it_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "jzr"
            shutil.copytree(PACKAGED, copy)

            changed = _text().write(copy)

            self.assertEqual(changed, [], REGENERATE)


class SkillMdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = PACKAGED / "SKILL.md"
        if not path.is_file():
            raise AssertionError(f"{path} is missing")
        cls.text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        cls.lines = cls.text.split("\n")

    def _front_matter(self) -> dict[str, str]:
        self.assertEqual(self.lines[0], "---")
        end = self.lines.index("---", 1)
        found: dict[str, str] = {}
        for line in self.lines[1:end]:
            self.assertRegex(line, r"^[a-z-]+: \S", f"front matter line is not one 'key: value' line: {line!r}")
            key, _, value = line.partition(": ")
            found[key] = value
        return found

    def _section(self, title: str) -> str:
        heading = re.compile(rf"^(#+)\s+.*{re.escape(title)}", re.IGNORECASE)
        for index, line in enumerate(self.lines):
            match = heading.match(line)
            if match:
                level = len(match.group(1))
                body: list[str] = []
                for follower in self.lines[index + 1:]:
                    other = re.match(r"^(#+)\s", follower)
                    if other and len(other.group(1)) <= level:
                        break
                    body.append(follower)
                return "\n".join(body)
        self.fail(f"no section titled like {title!r}")

    def test_front_matter_names_the_skill_jzr_and_describes_it_in_one_short_line(self) -> None:
        meta = self._front_matter()

        self.assertEqual(meta["name"], "jzr")
        self.assertTrue(meta["description"].strip())
        self.assertLess(len(meta["description"]), 400)
        self.assertNotIn(meta["description"][0], ">|")

    def test_the_description_says_when_to_use_the_skill(self) -> None:
        description = self._front_matter()["description"].lower()
        for word in ("image", "video", "audio"):
            self.assertIn(word, description)

    def test_the_file_is_under_150_lines(self) -> None:
        self.assertLess(len(self.text.splitlines()), 150)

    def test_both_command_map_markers_are_present_once_in_order(self) -> None:
        self.assertEqual(self.text.count("<!-- commands:start -->"), 1)
        self.assertEqual(self.text.count("<!-- commands:end -->"), 1)
        self.assertLess(self.text.index("<!-- commands:start -->"), self.text.index("<!-- commands:end -->"))

    def test_every_linked_reference_file_exists_and_nothing_else_is_linked_into_reference(self) -> None:
        links = re.findall(r"\]\((reference/[^)#\s]+)\)", self.text)

        self.assertTrue(links)
        for link in links:
            with self.subTest(link=link):
                self.assertTrue((PACKAGED / link).is_file(), link)

    def test_the_rules_name_the_flags_and_the_exit_codes(self) -> None:
        for flag in ("--dry-run", "--overwrite", "--json", "--outdir"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.text)
        exit_lines = [line for line in self.lines if re.search(r"exit code", line, re.IGNORECASE)]
        self.assertTrue(exit_lines, "no line about exit codes")
        joined = " ".join(exit_lines)
        for code in ("0", "1", "2"):
            self.assertRegex(joined, rf"(?<!\d){code}(?!\d)")

    def test_the_rules_say_how_to_get_ffmpeg(self) -> None:
        self.assertIn("jzr ffmpeg install", self.text)

    def test_crop_is_named_as_something_jzr_does_not_do(self) -> None:
        self.assertIn("crop", self._section("When not to use").lower())

    def test_questions_are_left_to_the_user(self) -> None:
        section = self._section("Leave these to the user")
        for command in ("host clean", "host procs --kill", "ffmpeg install"):
            with self.subTest(command=command):
                self.assertIn(command, section)

    def test_it_points_at_the_help_of_each_command(self) -> None:
        self.assertRegex(self.text, r"jzr <group> <command> -h|jzr .* -h")


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

"""Offline tests for jzr_bench.split_skill: the jzr skill cut into four domain skills.

The input is the real built skill (jzr_tools.commands._skill_text.build), so these tests also notice a
change of the generated text that the split cannot handle.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench import split_skill  # noqa: E402
from jzr_tools.commands._skill_text import build  # noqa: E402

NAMES = {"jzr-media", "jzr-pdf", "jzr-data", "jzr-system"}
# The table of the spec, section "Arm `split`".
EXPECTED_DOMAINS = {
    "jzr-media": {"image", "video", "audio", "ffmpeg"},
    "jzr-pdf": {"pdf"},
    "jzr-data": {"data", "text", "tools", "random", "env", "file"},
    "jzr-system": {"port", "host", "bind", "completion"},
}
KEPT_SECTIONS = ("Rules", "Leave these to the user", "When not to use jzr")
COMMAND_LINE = "- `jzr "


def front_matter(text: str) -> dict[str, str]:
    """The key: value lines between the opening and closing --- of a SKILL.md; fails on anything else."""
    lines = text.split("\n")
    assert lines[0] == "---", f"no front matter: {text[:80]!r}"
    end = lines.index("---", 1)
    found: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([a-z][a-z-]*): (\S.*)", line)
        assert match, f"front matter line is not a one-line 'key: value': {line!r}"
        found[match.group(1)] = match.group(2)
    return found


def body_of(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(lines[lines.index("---", 1) + 1 :])


def intro_of(text: str) -> str:
    """What stands between the front matter and the Commands section."""
    body = body_of(text)
    return body.split("\n## Commands", 1)[0].strip()


def section(text: str, title: str) -> str:
    """The text of '## title' up to the next '## ' heading."""
    lines = body_of(text).split("\n")
    start = lines.index(f"## {title}")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip()


def command_headings(text: str) -> list[str]:
    return re.findall(r"^### (\S+)", text, flags=re.M)


def commands_by_stem(text: str) -> dict[str, list[str]]:
    """stem -> its command lines, in order."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        heading = re.match(r"### (\S+)", line)
        if heading:
            current = heading.group(1)
            found[current] = []
        elif line.startswith("## "):
            current = None
        elif current is not None and line.startswith(COMMAND_LINE):
            found[current].append(line)
    return found


class SplitCase(unittest.TestCase):
    full: dict[str, str]
    full_skill: str
    full_stems: list[str]
    full_commands: dict[str, list[str]]
    split: dict[str, dict[str, str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.full = build()
        cls.full_skill = cls.full["SKILL.md"]
        cls.full_stems = command_headings(cls.full_skill)
        cls.full_commands = commands_by_stem(cls.full_skill)
        cls.split = split_skill.build_split(cls.full)


class FixtureSanityTest(SplitCase):
    """The assertions below are only meaningful if the real build has the shape they rely on."""

    def test_the_built_skill_has_the_shape_the_tests_use(self) -> None:
        self.assertGreaterEqual(len(self.full_stems), 15)
        self.assertEqual(len(set(self.full_stems)), len(self.full_stems))
        self.assertEqual(set(self.full_stems), set().union(*EXPECTED_DOMAINS.values()))
        self.assertTrue(all(self.full_commands[s] for s in self.full_stems))
        self.assertIn("<!-- commands:end -->", self.full_skill)
        for title in KEPT_SECTIONS:
            self.assertIn(f"\n## {title}\n", self.full_skill)
        for stem in self.full_stems:
            self.assertIn(f"reference/{stem}.md", self.full)


class ConstantsTest(unittest.TestCase):
    def test_domains_follow_the_spec_table(self) -> None:
        self.assertEqual(
            {name: set(stems) for name, stems in split_skill.DOMAINS.items()},
            EXPECTED_DOMAINS,
        )

    def test_split_skills_are_the_four_names(self) -> None:
        self.assertEqual(set(split_skill.SPLIT_SKILLS), NAMES)
        self.assertEqual(len(split_skill.SPLIT_SKILLS), 4)

    def test_descriptions_exist_for_each_skill_and_differ(self) -> None:
        self.assertEqual(set(split_skill.DESCRIPTIONS), NAMES)
        for name, text in split_skill.DESCRIPTIONS.items():
            with self.subTest(name=name):
                self.assertTrue(text.strip())
                self.assertNotIn("\n", text)
        self.assertEqual(len(set(split_skill.DESCRIPTIONS.values())), 4)


class BuildSplitTest(SplitCase):
    def test_four_skills_with_the_spec_names(self) -> None:
        self.assertEqual(set(self.split), NAMES)

    def test_each_skill_has_a_skill_md(self) -> None:
        for name, files in self.split.items():
            with self.subTest(name=name):
                self.assertIn("SKILL.md", files)

    def test_front_matter_has_own_name_and_a_one_line_description(self) -> None:
        for name, files in self.split.items():
            with self.subTest(name=name):
                meta = front_matter(files["SKILL.md"])
                self.assertEqual(meta["name"], name)
                self.assertTrue(meta["description"].strip())
                self.assertEqual(meta["description"], split_skill.DESCRIPTIONS[name])
                self.assertNotEqual(meta["description"], front_matter(self.full_skill)["description"])

    def test_front_matter_is_valid_yaml_with_string_values(self) -> None:
        # A plain scalar holding ": " or a leading quote character would break Claude Code's parser.
        for name, files in self.split.items():
            with self.subTest(name=name):
                lines = files["SKILL.md"].split("\n")
                raw = "\n".join(lines[1 : lines.index("---", 1)])
                parsed = yaml.safe_load(raw)
                self.assertEqual(parsed["name"], name)
                self.assertEqual(
                    parsed["description"],
                    front_matter(files["SKILL.md"])["description"],
                )

    def test_the_skills_have_the_same_description_as_the_one_in_the_module(
        self,
    ) -> None:
        descriptions = {front_matter(files["SKILL.md"])["description"] for files in self.split.values()}
        self.assertEqual(descriptions, set(split_skill.DESCRIPTIONS.values()))

    def test_each_skill_lists_exactly_its_domains_headings_in_the_original_order(
        self,
    ) -> None:
        for name, domain in EXPECTED_DOMAINS.items():
            with self.subTest(name=name):
                expected = [s for s in self.full_stems if s in domain]
                self.assertEqual(command_headings(self.split[name]["SKILL.md"]), expected)

    def test_each_skill_has_exactly_its_domains_command_lines(self) -> None:
        for name, domain in EXPECTED_DOMAINS.items():
            with self.subTest(name=name):
                expected = {s: self.full_commands[s] for s in self.full_stems if s in domain}
                self.assertEqual(commands_by_stem(self.split[name]["SKILL.md"]), expected)

    def test_every_stem_is_in_exactly_one_skill(self) -> None:
        seen: list[str] = []
        for files in self.split.values():
            seen.extend(command_headings(files["SKILL.md"]))
        self.assertCountEqual(seen, self.full_stems)

    def test_every_command_line_is_in_exactly_one_skill(self) -> None:
        # Only the lines under a ### heading are commands: the Rules section also has lines that start with "- `jzr ".
        full_lines = [line for lines in self.full_commands.values() for line in lines]
        split_lines = [
            line
            for files in self.split.values()
            for lines in commands_by_stem(files["SKILL.md"]).values()
            for line in lines
        ]
        self.assertGreater(len(full_lines), 50)
        self.assertCountEqual(split_lines, full_lines)

    def test_a_skill_does_not_list_commands_of_other_domains(self) -> None:
        for name, domain in EXPECTED_DOMAINS.items():
            with self.subTest(name=name):
                listed = commands_by_stem(self.split[name]["SKILL.md"])
                self.assertEqual(set(listed), domain)
                foreign = {line for stem in set(self.full_stems) - domain for line in self.full_commands[stem]}
                self.assertFalse(foreign & {line for lines in listed.values() for line in lines})

    def test_the_intro_is_the_same_in_every_skill(self) -> None:
        intro = intro_of(self.full_skill)
        self.assertIn("# jzr", intro)
        for name, files in self.split.items():
            with self.subTest(name=name):
                self.assertEqual(intro_of(files["SKILL.md"]), intro)

    def test_the_kept_sections_are_identical_to_the_full_skill(self) -> None:
        for name, files in self.split.items():
            for title in KEPT_SECTIONS:
                with self.subTest(name=name, section=title):
                    self.assertEqual(
                        section(files["SKILL.md"], title),
                        section(self.full_skill, title),
                    )

    def test_only_the_domains_reference_files_are_present_and_equal_to_the_build(
        self,
    ) -> None:
        for name, domain in EXPECTED_DOMAINS.items():
            with self.subTest(name=name):
                expected = {"SKILL.md"} | {f"reference/{stem}.md" for stem in domain}
                self.assertEqual(set(self.split[name]), expected)
                for path in expected - {"SKILL.md"}:
                    self.assertEqual(self.split[name][path], self.full[path], path)

    def test_every_reference_file_lands_in_exactly_one_skill(self) -> None:
        placed = [p for files in self.split.values() for p in files if p != "SKILL.md"]
        self.assertCountEqual(placed, [p for p in self.full if p != "SKILL.md"])

    def test_every_reference_link_points_at_a_file_of_that_skill(self) -> None:
        for name, files in self.split.items():
            with self.subTest(name=name):
                links = re.findall(r"\]\(([^)]+)\)", body_of(files["SKILL.md"]))
                self.assertTrue(links)
                for link in links:
                    self.assertIn(link, files, f"{link} is not a file of {name}")
                # and each reference file is linked, so none is orphaned
                self.assertEqual(set(links), {p for p in files if p != "SKILL.md"})

    def test_a_stem_in_no_domain_raises_value_error(self) -> None:
        marker = "<!-- commands:end -->"
        built = dict(self.full)
        built["SKILL.md"] = self.full_skill.replace(
            marker,
            "### mystery ([reference/mystery.md](reference/mystery.md))\n- `jzr mystery run` - Do it.\n" + marker,
        )
        built["reference/mystery.md"] = "mystery notes\n"
        self.assertIn("### mystery", built["SKILL.md"])  # the input really has the unknown stem
        with self.assertRaises(ValueError):
            split_skill.build_split(built)

    def test_the_same_input_without_the_unknown_stem_does_not_raise(self) -> None:
        self.assertEqual(set(split_skill.build_split(dict(self.full))), NAMES)


class WriteSplitTest(SplitCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "results" / "split-skills"  # does not exist yet

    def test_returns_the_four_names(self) -> None:
        names = split_skill.write_split(self.target)
        self.assertEqual(sorted(names), sorted(NAMES))

    def test_writes_every_file_of_every_skill_and_nothing_else(self) -> None:
        split_skill.write_split(self.target)
        self.assertEqual({p.name for p in self.target.iterdir()}, NAMES)
        on_disk: dict[tuple[str, str], str] = {}
        for skill_dir in self.target.iterdir():
            for path in skill_dir.rglob("*"):
                if path.is_file():
                    on_disk[(skill_dir.name, path.relative_to(skill_dir).as_posix())] = path.read_text()
        expected = {(name, path): text for name, files in self.split.items() for path, text in files.items()}
        self.assertEqual(on_disk, expected)

    def test_a_written_skill_md_starts_with_its_own_front_matter(self) -> None:
        split_skill.write_split(self.target)
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    front_matter((self.target / name / "SKILL.md").read_text())["name"],
                    name,
                )


if __name__ == "__main__":
    unittest.main()

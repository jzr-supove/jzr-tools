"""Tests for the Claude Code plugin and marketplace files that are generated from the packaged skills.

The committed files are compared with what the generator builds, and the
generator is run only against temporary folders, never against this checkout.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import jzr_tools
from jzr_tools import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGENERATE = "run `uv run python -m jzr_tools.commands._skill_text` to regenerate the plugin files"

MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_JSON = "plugins/jzr/.claude-plugin/plugin.json"
SKILLS_ROOT = "plugins/jzr/skills/"
SKILL_NAMES = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
DESCRIPTION = (
    "Everyday file and machine jobs from the jzr command line: convert, compress and "
    "inspect images, video, audio and PDFs, convert data, free ports and more."
)
REPOSITORY = "https://github.com/jzr-supove/jzr-tools"

# What `_skill_text.build()` returns: skill -> {path in its folder -> text}.
SAMPLE_SKILL = {
    "jzr-media": {
        "SKILL.md": "---\nname: jzr-media\n---\nUse jzr é\n",
        "reference/image.md": "# jzr image\n",
        "reference/video.md": "# jzr video\n",
    },
    "jzr-pdf": {
        "SKILL.md": "---\nname: jzr-pdf\n---\nPDF\n",
        "reference/pdf.md": "# jzr pdf\n",
    },
    "jzr-data": {
        "SKILL.md": "---\nname: jzr-data\n---\nData\n",
        "reference/tools.md": "# jzr top-level commands\n",
    },
    "jzr-system": {"SKILL.md": "---\nname: jzr-system\n---\nSystem\n"},
}


def skill_file(skill: str, name: str) -> str:
    """The path of one skill file from the repository root."""
    return f"{SKILLS_ROOT}{skill}/{name}"


SAMPLE_SKILL_FILES = {
    skill_file(skill, name): text.encode("utf-8")
    for skill, files in SAMPLE_SKILL.items()
    for name, text in files.items()
}


def _plugin():
    return importlib.import_module("jzr_tools.commands._plugin")


def _skill_text():
    return importlib.import_module("jzr_tools.commands._skill_text")


def _json(files: dict[str, bytes], name: str) -> dict:
    return json.loads(files[name].decode("utf-8"))


class PluginFilesTests(unittest.TestCase):
    def test_the_keys_are_the_marketplace_the_manifest_and_a_copy_of_every_file_of_every_skill(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        self.assertEqual(set(files), {MARKETPLACE, PLUGIN_JSON, *SAMPLE_SKILL_FILES})
        self.assertIn("plugins/jzr/skills/jzr-media/reference/video.md", files)
        self.assertIn("plugins/jzr/skills/jzr-system/SKILL.md", files)

    def test_each_skill_has_its_own_folder_and_there_is_no_folder_for_the_old_single_skill(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        folders = {name.split("/")[3] for name in files if name.startswith(SKILLS_ROOT)}
        self.assertEqual(folders, set(SKILL_NAMES))
        self.assertFalse([name for name in files if name.startswith(SKILLS_ROOT + "jzr/")], sorted(files))
        self.assertNotIn(SKILLS_ROOT + "SKILL.md", files)

    def test_a_file_goes_to_the_folder_of_the_skill_it_belongs_to(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        self.assertEqual(files[skill_file("jzr-pdf", "reference/pdf.md")], b"# jzr pdf\n")
        self.assertEqual(files[skill_file("jzr-data", "reference/tools.md")], b"# jzr top-level commands\n")
        self.assertNotIn(skill_file("jzr-pdf", "reference/image.md"), files)

    def test_every_value_is_bytes_with_lf_endings_only(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        for name, content in files.items():
            with self.subTest(file=name):
                self.assertIsInstance(content, bytes)
                self.assertNotIn(b"\r", content)
                self.assertTrue(content.endswith(b"\n"))

    def test_the_skill_files_are_copied_as_utf_8_unchanged(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        for skill, skill_files in SAMPLE_SKILL.items():
            for name, text in skill_files.items():
                with self.subTest(file=skill_file(skill, name)):
                    self.assertEqual(files[skill_file(skill, name)], text.encode("utf-8"))
        self.assertIn("é".encode("utf-8"), files[skill_file("jzr-media", "SKILL.md")])

    def test_the_manifest_has_the_agreed_fields_in_the_agreed_order(self) -> None:
        manifest = _json(_plugin().plugin_files(SAMPLE_SKILL, "9.8.7"), PLUGIN_JSON)

        self.assertEqual(
            list(manifest),
            ["name", "version", "description", "author", "homepage", "repository",
             "license", "keywords", "mcpServers"],
        )
        self.assertEqual(manifest["name"], "jzr")
        self.assertEqual(manifest["version"], "9.8.7")
        self.assertEqual(manifest["description"], DESCRIPTION)
        self.assertEqual(manifest["author"], {"name": "JZR SUPOVE"})
        self.assertEqual(manifest["homepage"], REPOSITORY)
        self.assertEqual(manifest["repository"], REPOSITORY)
        self.assertEqual(manifest["license"], "Apache-2.0")
        self.assertEqual(manifest["keywords"], ["cli", "images", "video", "audio", "pdf", "files"])

    def test_the_server_is_started_with_uvx_pinned_to_the_version_with_the_mcp_extra(self) -> None:
        manifest = _json(_plugin().plugin_files(SAMPLE_SKILL, "9.8.7"), PLUGIN_JSON)

        self.assertEqual(
            manifest["mcpServers"],
            {
                "jzr": {
                    "command": "uvx",
                    "args": ["--from", "jzr-tools[mcp]==9.8.7", "jzr", "agent", "mcp"],
                }
            },
        )

    def test_the_server_lives_in_the_manifest_not_in_a_dot_mcp_json_file(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        self.assertFalse([name for name in files if name.endswith(".mcp.json")], sorted(files))

    def test_the_manifest_is_two_space_indented_json_with_a_final_newline(self) -> None:
        text = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")[PLUGIN_JSON].decode("utf-8")

        self.assertEqual(text, json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n")
        self.assertIn('\n  "name": "jzr",\n', text)

    def test_the_marketplace_lists_the_plugin_with_a_relative_source(self) -> None:
        market = _json(_plugin().plugin_files(SAMPLE_SKILL, "9.8.7"), MARKETPLACE)

        self.assertEqual(list(market), ["name", "owner", "description", "plugins"])
        self.assertTrue(market["description"])
        self.assertEqual(market["name"], "jzr-tools")
        self.assertEqual(market["owner"], {"name": "JZR SUPOVE"})
        self.assertEqual(
            market["plugins"],
            [
                {
                    "name": "jzr",
                    "source": "./plugins/jzr",
                    "description": DESCRIPTION,
                    "version": "9.8.7",
                }
            ],
        )
        self.assertEqual(list(market["plugins"][0]), ["name", "source", "description", "version"])

    def test_the_marketplace_is_two_space_indented_json_with_a_final_newline(self) -> None:
        text = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")[MARKETPLACE].decode("utf-8")

        self.assertEqual(text, json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n")

    def test_a_different_version_changes_every_place_that_names_it(self) -> None:
        first = _plugin().plugin_files(SAMPLE_SKILL, "1.2.3")
        second = _plugin().plugin_files(SAMPLE_SKILL, "4.5.6")

        changed = {name for name in first if first[name] != second[name]}
        self.assertEqual(changed, {MARKETPLACE, PLUGIN_JSON})
        self.assertNotIn(b"1.2.3", second[PLUGIN_JSON] + second[MARKETPLACE])
        self.assertEqual(
            _json(second, PLUGIN_JSON)["mcpServers"]["jzr"]["args"][1], "jzr-tools[mcp]==4.5.6"
        )

    def test_the_input_is_not_changed(self) -> None:
        skill = copy.deepcopy(SAMPLE_SKILL)

        _plugin().plugin_files(skill, "9.8.7")

        self.assertEqual(skill, SAMPLE_SKILL)


class PluginFollowsThePackageTests(unittest.TestCase):
    def test_the_skill_copies_are_the_four_built_skills(self) -> None:
        built = _skill_text().build()

        files = _plugin().plugin_files(built, __version__)

        copied = {name: content for name, content in files.items() if name.startswith(SKILLS_ROOT)}
        self.assertEqual(
            copied,
            {
                skill_file(skill, name): text.encode("utf-8")
                for skill, skill_files in built.items()
                for name, text in skill_files.items()
            },
        )
        self.assertEqual(list(built), list(SKILL_NAMES))
        for skill in SKILL_NAMES:
            with self.subTest(skill=skill):
                self.assertIn(skill_file(skill, "SKILL.md"), copied)
                self.assertTrue(
                    any(name.startswith(skill_file(skill, "reference/")) for name in copied), sorted(copied)
                )

    def test_nothing_is_copied_to_the_folder_of_the_old_single_skill(self) -> None:
        files = _plugin().plugin_files(_skill_text().build(), __version__)

        self.assertEqual([name for name in files if name.startswith(SKILLS_ROOT + "jzr/")], [])

    def test_the_manifest_and_the_marketplace_carry_the_package_version(self) -> None:
        files = _plugin().plugin_files(_skill_text().build(), __version__)

        manifest = _json(files, PLUGIN_JSON)
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["mcpServers"]["jzr"]["args"][1], f"jzr-tools[mcp]=={__version__}")
        self.assertEqual(_json(files, MARKETPLACE)["plugins"][0]["version"], __version__)

    def test_the_package_version_is_the_one_in_pyproject(self) -> None:
        import tomllib

        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["version"], jzr_tools.__version__)


class CommittedPluginTests(unittest.TestCase):
    def test_every_committed_plugin_file_equals_what_the_generator_builds(self) -> None:
        expected = _plugin().plugin_files(_skill_text().build(), __version__)

        for name, content in expected.items():
            with self.subTest(file=name):
                path = PROJECT_ROOT / name
                self.assertTrue(path.is_file(), f"{path} is missing; {REGENERATE}")
                # A Windows checkout may turn LF into CRLF; the content is what counts.
                committed = path.read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(committed, content, f"{name} is out of date; {REGENERATE}")

    def test_the_plugin_folder_holds_no_file_the_generator_does_not_build(self) -> None:
        expected = set(_plugin().plugin_files(_skill_text().build(), __version__))
        folder = PROJECT_ROOT / "plugins" / "jzr"
        self.assertTrue(folder.is_dir(), f"{folder} is missing; {REGENERATE}")

        present = {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in folder.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }

        self.assertEqual(present, {name for name in expected if name.startswith("plugins/")}, REGENERATE)

    def test_the_folder_of_the_old_single_skill_is_gone(self) -> None:
        self.assertFalse((PROJECT_ROOT / "plugins" / "jzr" / "skills" / "jzr").exists(), REGENERATE)
        for skill in SKILL_NAMES:
            with self.subTest(skill=skill):
                self.assertTrue((PROJECT_ROOT / skill_file(skill, "SKILL.md")).is_file(), REGENERATE)


class PluginWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-plugin-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

    def _tree(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_write_creates_every_file_under_the_given_root_and_lists_them(self) -> None:
        changed = _plugin().write(self.root, self.files)

        self.assertEqual({Path(p).resolve() for p in changed}, {(self.root / n).resolve() for n in self.files})
        for name, content in self.files.items():
            with self.subTest(file=name):
                self.assertEqual((self.root / name).read_bytes(), content)

    def test_a_second_write_changes_nothing_and_lists_nothing(self) -> None:
        _plugin().write(self.root, self.files)
        before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }

        changed = _plugin().write(self.root, self.files)

        self.assertEqual(changed, [])
        after = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    def test_a_damaged_and_a_missing_file_are_restored_and_other_files_are_left_alone(self) -> None:
        _plugin().write(self.root, self.files)
        manifest = self.root / PLUGIN_JSON
        manifest.write_text("{}\n", encoding="utf-8")
        (self.root / MARKETPLACE).unlink()
        stray = self.root / "plugins" / "jzr" / "notes.txt"
        stray.write_text("keep me\n", encoding="utf-8")

        changed = _plugin().write(self.root, self.files)

        self.assertEqual(
            {Path(p).resolve() for p in changed},
            {manifest.resolve(), (self.root / MARKETPLACE).resolve()},
        )
        self.assertEqual(manifest.read_bytes(), self.files[PLUGIN_JSON])
        self.assertEqual((self.root / MARKETPLACE).read_bytes(), self.files[MARKETPLACE])
        self.assertEqual(stray.read_text(encoding="utf-8"), "keep me\n")

    def test_a_file_under_the_skills_folder_that_is_no_longer_built_is_deleted(self) -> None:
        _plugin().write(self.root, self.files)
        old_skill = self.root / skill_file("jzr", "SKILL.md")
        old_skill.parent.mkdir(parents=True)
        old_skill.write_text("the old single skill\n", encoding="utf-8")
        old_reference = self.root / skill_file("jzr", "reference/image.md")
        old_reference.parent.mkdir(parents=True)
        old_reference.write_text("old\n", encoding="utf-8")
        gone = self.root / skill_file("jzr-media", "reference/gone.md")
        gone.write_text("a group that moved away\n", encoding="utf-8")
        wrong_skill = self.root / skill_file("jzr-pdf", "reference/image.md")
        wrong_skill.write_text("belongs to jzr-media\n", encoding="utf-8")

        _plugin().write(self.root, self.files)

        for path in (old_skill, old_reference, gone, wrong_skill):
            with self.subTest(path=path.relative_to(self.root).as_posix()):
                self.assertFalse(path.exists())
        self.assertEqual(self._tree(), self.files)

    def test_a_skill_that_is_no_longer_built_loses_all_its_files(self) -> None:
        _plugin().write(self.root, self.files)
        fewer = {
            name: content for name, content in self.files.items() if not name.startswith(SKILLS_ROOT + "jzr-pdf/")
        }
        self.assertLess(len(fewer), len(self.files))

        _plugin().write(self.root, fewer)

        self.assertEqual(self._tree(), fewer)
        self.assertFalse((self.root / skill_file("jzr-pdf", "SKILL.md")).exists())

    def test_deleting_stale_files_leaves_every_file_outside_the_skills_folder_alone(self) -> None:
        _plugin().write(self.root, self.files)
        outside = {
            "plugins/jzr/notes.txt": "keep me\n",
            "plugins/jzr/.claude-plugin/other.json": "{}\n",
            ".claude-plugin/readme.txt": "keep me too\n",
            "plugins/other/skills/jzr/SKILL.md": "another plugin\n",
            "README.md": "root\n",
        }
        for name, text in outside.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        stale = self.root / skill_file("jzr", "SKILL.md")
        stale.parent.mkdir(parents=True)
        stale.write_text("old\n", encoding="utf-8")

        _plugin().write(self.root, self.files)

        self.assertFalse(stale.exists())
        for name, text in outside.items():
            with self.subTest(file=name):
                self.assertEqual((self.root / name).read_text(encoding="utf-8"), text)

    def test_a_current_tree_with_a_stale_file_changes_only_what_is_stale(self) -> None:
        _plugin().write(self.root, self.files)
        before = {
            name: (path.read_bytes(), path.stat().st_mtime_ns)
            for name in self.files
            for path in [self.root / name]
        }
        stale = self.root / skill_file("jzr", "SKILL.md")
        stale.parent.mkdir(parents=True)
        stale.write_text("old\n", encoding="utf-8")

        changed = _plugin().write(self.root, self.files)

        # Whether a deleted file is listed among the changed ones is not fixed; a rewrite of a current file is.
        self.assertEqual({Path(p).resolve() for p in changed} - {stale.resolve()}, set())
        for name, state in before.items():
            with self.subTest(file=name):
                path = self.root / name
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), state)
        self.assertFalse(stale.exists())

    def test_a_file_that_a_windows_checkout_gave_crlf_endings_counts_as_current(self) -> None:
        _plugin().write(self.root, self.files)
        manifest = self.root / PLUGIN_JSON
        manifest.write_bytes(self.files[PLUGIN_JSON].replace(b"\n", b"\r\n"))

        changed = _plugin().write(self.root, self.files)

        self.assertEqual(changed, [])


class GeneratorScriptTests(unittest.TestCase):
    """`python -m jzr_tools.commands._skill_text` run on a copy of the source tree."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-plugin-script-")
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name).resolve() / "repo"
        self.repo.mkdir()
        shutil.copytree(
            PROJECT_ROOT / "src", self.repo / "src", ignore=shutil.ignore_patterns("__pycache__")
        )
        self.config = Path(folder.name).resolve() / "config"
        self.config.mkdir()

    def run_script(self) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo / "src")
        env["JZR_CONFIG_DIR"] = str(self.config)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools.commands._skill_text"],
            cwd=self.repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )

    def test_it_writes_the_plugin_files_when_the_repository_root_has_a_pyproject(self) -> None:
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

        done = self.run_script()

        self.assertEqual(done.returncode, 0, done.stderr)
        names = [MARKETPLACE, PLUGIN_JSON, *(skill_file(skill, "SKILL.md") for skill in SKILL_NAMES)]
        for name in names:
            with self.subTest(file=name):
                self.assertTrue((self.repo / name).is_file(), done.stdout + done.stderr)
        self.assertFalse((self.repo / "plugins" / "jzr" / "skills" / "jzr").exists())
        printed = done.stdout.replace("\\", "/")
        self.assertIn("plugin.json", printed)
        self.assertIn("marketplace.json", printed)

    def test_the_files_it_writes_are_the_ones_the_generator_builds(self) -> None:
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

        self.assertEqual(self.run_script().returncode, 0)

        expected = _plugin().plugin_files(_skill_text().build(), __version__)
        self.assertGreater(len(expected), 2 + len(SKILL_NAMES))
        for name, content in expected.items():
            with self.subTest(file=name):
                self.assertEqual((self.repo / name).read_bytes(), content)

    def test_it_deletes_the_old_single_skill_and_stale_files_from_the_plugin_and_the_packaged_skills(self) -> None:
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        skills = self.repo / "src" / "jzr_tools" / "data" / "skill"
        stale = [
            self.repo / skill_file("jzr", "SKILL.md"),
            self.repo / skill_file("jzr-media", "reference/gone.md"),
            skills / "jzr-pdf" / "reference" / "gone.md",
            skills / "jzr-system" / "notes.txt",
        ]
        for path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale\n", encoding="utf-8")
        template = (skills / "template.md").read_bytes()

        done = self.run_script()

        self.assertEqual(done.returncode, 0, done.stderr)
        for path in stale:
            with self.subTest(file=path.relative_to(self.repo).as_posix()):
                self.assertFalse(path.exists(), done.stdout + done.stderr)
        self.assertEqual((skills / "template.md").read_bytes(), template)
        self.assertFalse((skills / "jzr").exists())

    def test_a_second_run_reports_that_everything_is_up_to_date(self) -> None:
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        self.run_script()

        done = self.run_script()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "up to date")

    def test_without_a_pyproject_beside_the_source_no_plugin_folder_is_made(self) -> None:
        done = self.run_script()

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.repo / "plugins").exists())
        self.assertFalse((self.repo / ".claude-plugin").exists())


if __name__ == "__main__":
    unittest.main()

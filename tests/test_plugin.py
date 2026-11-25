"""Tests for the Claude Code plugin and marketplace files that are generated from the packaged skill.

The committed files are compared with what the generator builds, and the
generator is run only against temporary folders, never against this checkout.
"""

from __future__ import annotations

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
SKILL_DIR = "plugins/jzr/skills/jzr/"
DESCRIPTION = (
    "Everyday file and machine jobs from the jzr command line: convert, compress and "
    "inspect images, video, audio and PDFs, convert data, free ports and more."
)
REPOSITORY = "https://github.com/jzr-supove/jzr-tools"

SAMPLE_SKILL = {
    "SKILL.md": "---\nname: jzr\n---\nUse jzr é\n",
    "reference/image.md": "# jzr image\n",
    "reference/tools.md": "# jzr top-level commands\n",
}


def _plugin():
    return importlib.import_module("jzr_tools.commands._plugin")


def _skill_text():
    return importlib.import_module("jzr_tools.commands._skill_text")


def _json(files: dict[str, bytes], name: str) -> dict:
    return json.loads(files[name].decode("utf-8"))


class PluginFilesTests(unittest.TestCase):
    def test_the_keys_are_the_marketplace_the_manifest_and_a_copy_of_every_skill_file(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        self.assertEqual(
            set(files),
            {
                MARKETPLACE,
                PLUGIN_JSON,
                SKILL_DIR + "SKILL.md",
                SKILL_DIR + "reference/image.md",
                SKILL_DIR + "reference/tools.md",
            },
        )

    def test_every_value_is_bytes_with_lf_endings_only(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        for name, content in files.items():
            with self.subTest(file=name):
                self.assertIsInstance(content, bytes)
                self.assertNotIn(b"\r", content)
                self.assertTrue(content.endswith(b"\n"))

    def test_the_skill_files_are_copied_as_utf_8_unchanged(self) -> None:
        files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

        for name, text in SAMPLE_SKILL.items():
            with self.subTest(file=name):
                self.assertEqual(files[SKILL_DIR + name], text.encode("utf-8"))

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
        skill = dict(SAMPLE_SKILL)

        _plugin().plugin_files(skill, "9.8.7")

        self.assertEqual(skill, SAMPLE_SKILL)


class PluginFollowsThePackageTests(unittest.TestCase):
    def test_the_skill_copy_is_the_built_skill(self) -> None:
        built = _skill_text().build()

        files = _plugin().plugin_files(built, __version__)

        copied = {
            name[len(SKILL_DIR):]: content
            for name, content in files.items()
            if name.startswith(SKILL_DIR)
        }
        self.assertEqual(copied, {name: text.encode("utf-8") for name, text in built.items()})
        self.assertIn("SKILL.md", copied)
        self.assertTrue(any(name.startswith("reference/") for name in copied), sorted(copied))

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


class PluginWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-plugin-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.files = _plugin().plugin_files(SAMPLE_SKILL, "9.8.7")

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
        for name in (MARKETPLACE, PLUGIN_JSON, SKILL_DIR + "SKILL.md"):
            with self.subTest(file=name):
                self.assertTrue((self.repo / name).is_file(), done.stdout + done.stderr)
        printed = done.stdout.replace("\\", "/")
        self.assertIn("plugin.json", printed)
        self.assertIn("marketplace.json", printed)

    def test_the_files_it_writes_are_the_ones_the_generator_builds(self) -> None:
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

        self.assertEqual(self.run_script().returncode, 0)

        expected = _plugin().plugin_files(_skill_text().build(), __version__)
        for name, content in expected.items():
            with self.subTest(file=name):
                self.assertEqual((self.repo / name).read_bytes(), content)

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

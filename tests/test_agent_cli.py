"""Behavioural tests for `jzr agent skill`, `jzr agent install` and `jzr agent remove`.

Every test runs against a throw-away home folder and a throw-away current
folder, so the real ~/.claude, ~/.agents and ~/.codex are never touched.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from click.testing import CliRunner

import jzr_tools
from jzr_tools import __version__
from jzr_tools.cli import app

PACKAGED = Path(jzr_tools.__file__).resolve().parent / "data" / "skill" / "jzr"
MARKER = ".jzr-version"
RESTART = "Restart the agent, or start a new session, to use it."
NO_AGENT = (
    "error: no AI agent found (looked for ~/.claude, ~/.codex and ~/.cursor); "
    "name one with --agent, or use --project"
)


def _packaged_files() -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in sorted(PACKAGED.rglob("*")):
        if path.is_file():
            found[path.relative_to(PACKAGED).as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    return found


def _installed_files(folder: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            found[path.relative_to(folder).as_posix()] = path.read_bytes()
    return found


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    found: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_dir():
            found[key] = (b"<dir>", 0)
        else:
            found[key] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


class AgentCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-agent-cli-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        project = self.root / "project"
        project.mkdir()

        previous = os.getcwd()
        os.chdir(project)
        self.addCleanup(os.chdir, previous)
        self.cwd = Path(os.getcwd())

        self.env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "JZR_CONFIG_DIR": str(self.root / "bindings"),
        }
        patched = mock.patch.dict(os.environ, self.env)
        patched.start()
        self.addCleanup(patched.stop)
        home = mock.patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

        self.runner = CliRunner()

    def jzr(self, *args: str):
        return self.runner.invoke(app, ["agent", *args], env=self.env)

    def make_home(self, *names: str) -> None:
        for name in names:
            (self.home / name).mkdir(exist_ok=True)

    @property
    def claude(self) -> Path:
        return self.home / ".claude" / "skills" / "jzr"

    @property
    def agents(self) -> Path:
        return self.home / ".agents" / "skills" / "jzr"

    @property
    def project_claude(self) -> Path:
        return self.cwd / ".claude" / "skills" / "jzr"

    @property
    def project_agents(self) -> Path:
        return self.cwd / ".agents" / "skills" / "jzr"

    def installed_line(self, path: Path, agents: str) -> str:
        return f"installed jzr skill {__version__} -> {path} ({agents})"

    def set_marker_version(self, target: Path, version: str) -> None:
        lines = (target / MARKER).read_text(encoding="utf-8").split("\n")
        lines[0] = version
        (target / MARKER).write_bytes("\n".join(lines).encode("utf-8"))


class SkillCommandTests(AgentCase):
    def test_skill_prints_the_packaged_skill_file_in_utf8_with_lf(self) -> None:
        result = self.jzr("skill")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        expected = (PACKAGED / "SKILL.md").read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        printed = result.stdout_bytes.decode("utf-8")
        self.assertNotIn("\r", printed)
        self.assertTrue(printed.startswith("---\n"), printed[:40])
        self.assertEqual(printed.rstrip("\n"), expected.rstrip("\n"))
        self.assertTrue(printed.endswith("\n"))

    def test_skill_path_prints_the_packaged_folder_and_nothing_else(self) -> None:
        result = self.jzr("skill", "--path")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        printed = Path(lines[0])
        self.assertEqual(printed.resolve(), PACKAGED)
        self.assertTrue((printed / "SKILL.md").is_file())

    def test_skill_never_writes_to_the_home_or_current_folder(self) -> None:
        before = _snapshot(self.root)

        self.jzr("skill")
        self.jzr("skill", "--path")

        self.assertEqual(_snapshot(self.root), before)


class InstallDetectionTests(AgentCase):
    def test_nothing_found_is_an_error_naming_the_ways_out(self) -> None:
        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.strip(), NO_AGENT)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.output)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_claude_home_gets_the_skill_and_the_restart_line(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.claude, "claude"), RESTART]
        )
        self.assertEqual(result.stderr, "")

    def test_the_installed_copy_holds_every_packaged_file_plus_a_marker_with_lf_only(self) -> None:
        self.make_home(".claude")

        self.jzr("install")

        installed = _installed_files(self.claude)
        marker = installed.pop(MARKER)
        packaged = _packaged_files()
        self.assertIn("SKILL.md", installed)
        self.assertTrue(
            any(name.startswith("reference/") for name in installed), sorted(installed)
        )
        self.assertEqual(installed, packaged)
        self.assertEqual(marker.split(b"\n")[0].decode("utf-8"), __version__)
        for name, content in {**installed, MARKER: marker}.items():
            with self.subTest(file=name):
                self.assertNotIn(b"\r", content)

    def test_no_temporary_folder_is_left_beside_the_installed_copy(self) -> None:
        self.make_home(".claude")

        self.jzr("install")

        self.assertEqual([p.name for p in (self.home / ".claude" / "skills").iterdir()], ["jzr"])

    def test_codex_home_installs_into_the_shared_agents_folder(self) -> None:
        self.make_home(".codex")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.agents, "codex"), RESTART]
        )
        self.assertTrue((self.agents / "SKILL.md").is_file())
        self.assertFalse((self.home / ".claude").exists())

    def test_claude_and_codex_both_get_a_line_and_the_restart_line_comes_once_at_the_end(self) -> None:
        self.make_home(".claude", ".codex")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3, result.stdout)
        self.assertEqual(
            set(lines[:2]),
            {self.installed_line(self.claude, "claude"), self.installed_line(self.agents, "codex")},
        )
        self.assertEqual(lines[2], RESTART)
        self.assertTrue((self.claude / "SKILL.md").is_file())
        self.assertTrue((self.agents / "SKILL.md").is_file())

    def test_cursor_alone_uses_the_agents_folder_and_is_named(self) -> None:
        self.make_home(".cursor")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.agents, "cursor"), RESTART]
        )
        self.assertFalse((self.home / ".claude").exists())

    def test_cursor_next_to_codex_shares_one_target_listed_under_both_names(self) -> None:
        self.make_home(".codex", ".cursor")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.agents, "codex, cursor"), RESTART]
        )

    def test_cursor_next_to_claude_adds_no_target_of_its_own(self) -> None:
        self.make_home(".claude", ".cursor")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.claude, "claude"), RESTART]
        )
        self.assertFalse((self.home / ".agents").exists())

    def test_a_plain_file_named_like_an_agent_home_does_not_count(self) -> None:
        (self.home / ".claude").write_text("not a folder", encoding="utf-8")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.strip(), NO_AGENT)


class InstallOptionTests(AgentCase):
    def test_agent_claude_installs_even_when_the_claude_home_is_missing(self) -> None:
        result = self.jzr("install", "--agent", "claude")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.claude, "claude"), RESTART]
        )
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_agent_codex_limits_the_run_to_codex(self) -> None:
        self.make_home(".claude", ".codex")

        result = self.jzr("install", "--agent", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.agents, "codex"), RESTART]
        )
        self.assertFalse(self.claude.exists())

    def test_an_unknown_agent_is_a_usage_error_and_writes_nothing(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)

        result = self.jzr("install", "--agent", "bogus")

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("bogus", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(_snapshot(self.root), before)

    def test_remove_rejects_an_unknown_agent_too(self) -> None:
        result = self.jzr("remove", "--agent", "bogus")

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("bogus", result.stderr)

    def test_project_installs_into_the_current_folder_without_looking_at_the_home(self) -> None:
        result = self.jzr("install", "--project")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.project_claude, "claude"), RESTART]
        )
        self.assertTrue((self.project_claude / "SKILL.md").is_file())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_project_with_codex_uses_the_agents_folder_of_the_project(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--project", "--agent", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.project_agents, "codex"), RESTART]
        )
        self.assertFalse(self.project_claude.exists())
        self.assertFalse(self.claude.exists())


class DryRunTests(AgentCase):
    def test_dry_run_says_would_install_and_writes_nothing_not_even_parents(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)

        result = self.jzr("install", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"would install jzr skill {__version__} -> {self.claude} (claude)"],
        )
        self.assertNotIn("Restart", result.output)
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.home / ".claude" / "skills").exists())

    def test_dry_run_with_project_writes_nothing(self) -> None:
        before = _snapshot(self.root)

        result = self.jzr("install", "--project", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"would install jzr skill {__version__} -> {self.project_claude} (claude)"],
        )
        self.assertEqual(_snapshot(self.root), before)

    def test_dry_run_of_an_update_says_would_update_with_both_versions_and_keeps_the_old_copy(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.set_marker_version(self.claude, "0.0.1")
        before = _snapshot(self.root)

        result = self.jzr("install", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"would update jzr skill 0.0.1 to {__version__} -> {self.claude} (claude)"],
        )
        self.assertEqual(_snapshot(self.root), before)

    def test_dry_run_of_a_refusal_still_reports_it_and_fails(self) -> None:
        self.make_home(".claude")
        self.claude.mkdir(parents=True)
        (self.claude / "mine.md").write_text("mine", encoding="utf-8")
        before = _snapshot(self.root)

        result = self.jzr("install", "--dry-run")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"skip {self.claude}: not installed by jzr", result.stderr)
        self.assertEqual(_snapshot(self.root), before)

    def test_dry_run_of_remove_says_would_remove_and_deletes_nothing(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        before = _snapshot(self.root)

        result = self.jzr("remove", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"would remove jzr skill -> {self.claude} (claude)"]
        )
        self.assertEqual(_snapshot(self.root), before)


class SecondRunTests(AgentCase):
    def test_a_second_install_says_it_is_already_installed_and_touches_nothing(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        before = _snapshot(self.root)

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"jzr skill {__version__} is already installed: {self.claude} (claude)"],
        )
        self.assertNotIn("Restart", result.output)
        self.assertEqual(result.stderr, "")
        self.assertEqual(_snapshot(self.root), before)

    def test_an_older_copy_is_updated_and_the_restart_line_is_printed(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.set_marker_version(self.claude, "0.0.1")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"updated jzr skill 0.0.1 to {__version__} -> {self.claude} (claude)", RESTART],
        )
        first = (self.claude / MARKER).read_text(encoding="utf-8").split("\n")[0]
        self.assertEqual(first, __version__)

    def test_a_marker_version_that_cannot_be_read_counts_as_older_and_is_left_out_of_the_line(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.set_marker_version(self.claude, "garbage")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"updated jzr skill to {__version__} -> {self.claude} (claude)", RESTART],
        )

    def test_a_copy_from_a_newer_jzr_is_refused_until_overwrite(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.set_marker_version(self.claude, "99.0.0")

        refused = self.jzr("install")

        self.assertEqual(refused.exit_code, 1, refused.output)
        self.assertEqual(
            refused.stderr.strip(),
            f"skip {self.claude}: installed by a newer jzr (99.0.0); use --overwrite to replace it",
        )
        self.assertNotIn("Restart", refused.output)
        self.assertTrue((self.claude / MARKER).read_text(encoding="utf-8").startswith("99.0.0"))

        forced = self.jzr("install", "--overwrite")

        self.assertEqual(forced.exit_code, 0, forced.output)
        self.assertEqual(forced.stdout.splitlines()[-1], RESTART)
        self.assertTrue(forced.stdout.startswith("updated jzr skill"), forced.stdout)
        self.assertTrue((self.claude / MARKER).read_text(encoding="utf-8").startswith(__version__))


class RefusalTests(AgentCase):
    def test_an_edited_copy_is_refused_and_left_as_the_user_made_it(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        skill = self.claude / "SKILL.md"
        skill.write_text("my own edits\n", encoding="utf-8")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stderr.strip(),
            f"skip {self.claude}: changed since jzr installed it; use --overwrite to replace it",
        )
        self.assertEqual(result.stdout, "")
        self.assertEqual(skill.read_text(encoding="utf-8"), "my own edits\n")

    def test_overwrite_replaces_an_edited_copy_with_the_packaged_files(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        (self.claude / "SKILL.md").write_text("my own edits\n", encoding="utf-8")

        result = self.jzr("install", "--overwrite")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("updated jzr skill"), lines)
        self.assertTrue(lines[0].endswith(f"-> {self.claude} (claude)"), lines)
        self.assertEqual(lines[-1], RESTART)
        installed = _installed_files(self.claude)
        installed.pop(MARKER)
        self.assertEqual(installed, _packaged_files())

    def test_a_foreign_folder_is_refused_then_replaced_with_overwrite(self) -> None:
        self.make_home(".claude")
        self.claude.mkdir(parents=True)
        (self.claude / "custom.md").write_text("someone else's skill\n", encoding="utf-8")

        refused = self.jzr("install")

        self.assertEqual(refused.exit_code, 1, refused.output)
        self.assertEqual(
            refused.stderr.strip(),
            f"skip {self.claude}: not installed by jzr; use --overwrite to replace it",
        )
        self.assertEqual(refused.stdout, "")
        self.assertEqual(
            (self.claude / "custom.md").read_text(encoding="utf-8"), "someone else's skill\n"
        )
        self.assertFalse((self.claude / "SKILL.md").exists())

        forced = self.jzr("install", "--overwrite")

        self.assertEqual(forced.exit_code, 0, forced.output)
        self.assertTrue(forced.stdout.startswith("updated jzr skill"), forced.stdout)
        self.assertEqual(forced.stdout.splitlines()[-1], RESTART)
        self.assertTrue((self.claude / "SKILL.md").is_file())
        self.assertFalse((self.claude / "custom.md").exists())
        self.assertTrue((self.claude / MARKER).is_file())

    def test_a_refused_target_does_not_stop_the_other_and_the_run_fails(self) -> None:
        self.make_home(".claude", ".codex")
        self.claude.mkdir(parents=True)
        (self.claude / "custom.md").write_text("mine\n", encoding="utf-8")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [self.installed_line(self.agents, "codex"), RESTART]
        )
        self.assertEqual(
            result.stderr.strip(),
            f"skip {self.claude}: not installed by jzr; use --overwrite to replace it",
        )
        self.assertTrue((self.agents / "SKILL.md").is_file())
        self.assertEqual((self.claude / "custom.md").read_text(encoding="utf-8"), "mine\n")

    def test_when_every_target_is_refused_there_is_no_restart_line(self) -> None:
        self.make_home(".claude")
        self.claude.mkdir(parents=True)

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn("Restart", result.output)

    def test_a_target_that_cannot_be_written_is_an_error_line_not_a_traceback(self) -> None:
        self.make_home(".claude")
        (self.home / ".claude" / "skills").write_text("in the way", encoding="utf-8")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        errors = [line for line in result.stderr.splitlines() if line.startswith("error: ")]
        self.assertEqual(len(errors), 1, result.stderr)
        self.assertTrue(errors[0].startswith(f"error: {self.claude}: "), errors[0])
        self.assertNotIn("Traceback", result.output)
        self.assertNotIn("Restart", result.output)


class RemoveTests(AgentCase):
    def test_remove_deletes_the_installed_folder_only_and_keeps_its_parents(self) -> None:
        self.make_home(".claude")
        self.jzr("install")

        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"removed jzr skill -> {self.claude} (claude)"]
        )
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.claude.exists())
        self.assertTrue((self.home / ".claude" / "skills").is_dir())

    def test_remove_with_nothing_installed_says_so_and_succeeds(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)

        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines(), [f"no jzr skill at {self.claude} (claude)"])
        self.assertEqual(_snapshot(self.root), before)

    def test_remove_with_no_agent_found_is_the_same_error_as_install(self) -> None:
        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.strip(), NO_AGENT)

    def test_remove_never_prints_the_restart_line(self) -> None:
        self.make_home(".claude")
        self.jzr("install")

        result = self.jzr("remove")

        self.assertNotIn("Restart", result.output)

    def test_remove_refuses_an_edited_copy_until_overwrite(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        (self.claude / "SKILL.md").write_text("edited\n", encoding="utf-8")

        refused = self.jzr("remove")

        self.assertEqual(refused.exit_code, 1, refused.output)
        self.assertEqual(
            refused.stderr.strip(),
            f"skip {self.claude}: changed since jzr installed it; use --overwrite to remove it",
        )
        self.assertTrue(self.claude.exists())

        forced = self.jzr("remove", "--overwrite")

        self.assertEqual(forced.exit_code, 0, forced.output)
        self.assertEqual(
            forced.stdout.splitlines(), [f"removed jzr skill -> {self.claude} (claude)"]
        )
        self.assertFalse(self.claude.exists())

    def test_remove_never_deletes_a_folder_jzr_did_not_install_even_with_overwrite(self) -> None:
        self.make_home(".claude")
        self.claude.mkdir(parents=True)
        (self.claude / "custom.md").write_text("mine\n", encoding="utf-8")

        for extra in ((), ("--overwrite",)):
            with self.subTest(extra=extra):
                result = self.jzr("remove", *extra)

                self.assertEqual(result.exit_code, 1, result.output)
                self.assertEqual(
                    result.stderr.strip(),
                    f"skip {self.claude}: not installed by jzr; remove it yourself",
                )
                self.assertEqual((self.claude / "custom.md").read_text(encoding="utf-8"), "mine\n")

    def test_remove_with_agent_touches_only_that_agent(self) -> None:
        self.make_home(".claude", ".codex")
        self.jzr("install")

        result = self.jzr("remove", "--agent", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"removed jzr skill -> {self.agents} (codex)"]
        )
        self.assertFalse(self.agents.exists())
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_remove_with_project_looks_only_at_the_current_folder(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.jzr("install", "--project")

        result = self.jzr("remove", "--project")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"removed jzr skill -> {self.project_claude} (claude)"]
        )
        self.assertFalse(self.project_claude.exists())
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_remove_names_codex_and_cursor_together_when_they_share_a_folder(self) -> None:
        self.make_home(".codex", ".cursor")
        self.jzr("install")

        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"removed jzr skill -> {self.agents} (codex, cursor)"]
        )

    def test_one_refused_removal_does_not_stop_the_other(self) -> None:
        self.make_home(".claude", ".codex")
        self.jzr("install")
        (self.claude / "SKILL.md").write_text("edited\n", encoding="utf-8")

        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"removed jzr skill -> {self.agents} (codex)"]
        )
        self.assertIn(f"skip {self.claude}: changed since jzr installed it", result.stderr)
        self.assertFalse(self.agents.exists())
        self.assertTrue(self.claude.exists())


class GroupHelpTests(AgentCase):
    def test_group_help_has_the_group_text_and_lists_install_mcp_remove_and_skill(self) -> None:
        result = self.jzr("--help")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Connect jzr to AI agents (skill and MCP server).", " ".join(result.stdout.split()))
        listed = re.findall(r"^  (\S+)", result.stdout.split("Commands:", 1)[1], flags=re.MULTILINE)
        self.assertEqual(sorted(listed), ["install", "mcp", "remove", "skill"])

    def test_install_and_remove_help_list_the_mcp_option(self) -> None:
        for action in ("install", "remove"):
            with self.subTest(action=action):
                result = self.jzr(action, "-h")

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("--mcp", result.stdout)

    def test_mcp_help_exits_zero_and_says_what_it_does(self) -> None:
        result = self.jzr("mcp", "-h")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("MCP", result.stdout)

    def test_install_help_lists_its_options(self) -> None:
        result = self.jzr("install", "-h")

        self.assertEqual(result.exit_code, 0, result.output)
        for option in ("--agent", "--project", "--overwrite", "--dry-run"):
            self.assertIn(option, result.stdout)
        self.assertIn("claude", result.stdout)
        self.assertIn("codex", result.stdout)


NEEDS_EXTRA = (
    'the MCP server needs the mcp extra: uv tool install "jzr-tools[mcp]" '
    '(or pipx install "jzr-tools[mcp]")'
)
if sys.platform == "win32":
    FAKE_CLAUDE = "C:\\tools\\claude.exe"
    JZR_EXE = "C:\\tools\\jzr.exe"
    JZR_SPACED = "C:\\Program Files\\jzr\\jzr.exe"
else:
    FAKE_CLAUDE = "/opt/tools/claude"
    JZR_EXE = "/opt/tools/jzr"
    JZR_SPACED = "/opt/my tools/jzr"
SERVER = [JZR_EXE, "agent", "mcp"]
CONFLICT = "--mcp registers for your user; it cannot be used with --project."
DESKTOP_ADD = (
    "Claude Desktop: open Settings > Developer > Edit Config, add this entry and restart the app:"
)
DESKTOP_REMOVE = "Claude Desktop: remove the jzr entry under Settings > Developer > Edit Config."


def shown(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)


def add_words(claude: str, server: list[str]) -> list[str]:
    return [claude, "mcp", "add", "-s", "user", "jzr", "--", *server]


class McpCase(AgentCase):
    """The agent commands with `claude` and the registration helpers replaced.

    Nothing starts a process; the helpers record how they were called.
    """

    def setUp(self) -> None:
        super().setUp()
        from jzr_tools.core import mcp_setup

        self.setup_module = mcp_setup
        self.claude_path: str | None = FAKE_CLAUDE
        self.codex_path: str | None = None
        self.code_path: str | None = None
        self.available = True
        self.jzr_words = [JZR_EXE]
        self.registration = mcp_setup.Registration("added")
        self.unregistration = mcp_setup.Registration("removed")

        def which(name: str, *args: object, **kwargs: object) -> str | None:
            return {"claude": self.claude_path, "codex": self.codex_path, "code": self.code_path}.get(name)

        self.register = mock.Mock(side_effect=lambda *a, **k: self.registration)
        self.unregister = mock.Mock(side_effect=lambda *a, **k: self.unregistration)
        for patcher in (
            mock.patch("shutil.which", side_effect=which),
            mock.patch.object(mcp_setup, "available", side_effect=lambda: self.available),
            mock.patch.object(mcp_setup, "jzr_command", side_effect=lambda *a, **k: list(self.jzr_words)),
            mock.patch.object(mcp_setup, "register", self.register),
            mock.patch.object(mcp_setup, "unregister", self.unregister),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def values(call: mock._Call) -> list[object]:
        return list(call.args) + list(call.kwargs.values())

    def desktop_block(self, stdout: str) -> tuple[list[str], dict]:
        """The lines after the Claude Desktop header and the JSON document in them."""
        lines = stdout.splitlines()
        start = lines.index(DESKTOP_ADD)
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("The file is usually "))
        return lines[start : end + 1], json.loads("\n".join(lines[start + 1 : end]))


class InstallMcpTests(McpCase):
    def test_registers_with_claude_code_after_the_skill_and_prints_the_desktop_entry(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], self.installed_line(self.claude, "claude"))
        self.assertEqual(lines.count(RESTART), 1, lines)
        self.assertTrue((self.claude / "SKILL.md").is_file())
        self.assertEqual(self.register.call_count, 1)
        values = self.values(self.register.call_args)
        self.assertIn(FAKE_CLAUDE, values)
        self.assertIn(SERVER, values)
        registered = "registered the jzr MCP server with Claude Code"
        self.assertEqual(lines.count(registered), 1, lines)
        block, document = self.desktop_block(result.stdout)
        self.assertLess(lines.index(registered), lines.index(DESKTOP_ADD))
        entry = document.get("mcpServers", document)
        entry = entry.get("jzr", entry)
        self.assertEqual(entry["command"], JZR_EXE)
        self.assertEqual(entry["args"], ["agent", "mcp"])
        self.assertTrue(block[-1].endswith("claude_desktop_config.json."), block[-1])

    def test_an_existing_registration_is_reported_and_is_not_an_error(self) -> None:
        self.make_home(".claude")
        self.registration = self.setup_module.Registration("exists")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        self.assertIn("the jzr MCP server is already registered with Claude Code", result.stdout.splitlines())
        self.assertNotIn("registered the jzr MCP server with Claude Code", result.stdout.splitlines())
        self.assertIn(DESKTOP_ADD, result.stdout.splitlines())

    def test_a_failed_registration_is_an_error_line_exit_1_and_the_skill_stays_installed(self) -> None:
        self.make_home(".claude")
        self.registration = self.setup_module.Registration("failed", "cannot write the config")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: Claude Code: cannot write the config", result.stderr.splitlines())
        self.assertNotIn("Traceback", result.output)
        self.assertTrue((self.claude / "SKILL.md").is_file())
        self.assertIn(self.installed_line(self.claude, "claude"), result.stdout.splitlines())
        self.assertNotIn("registered the jzr MCP server with Claude Code", result.stdout.splitlines())

    def test_without_claude_on_path_it_prints_the_command_to_run_and_registers_nothing(self) -> None:
        self.make_home(".claude")
        self.claude_path = None
        self.jzr_words = [JZR_SPACED]

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.register.call_count, 0)
        expected = "Claude Code is not on PATH; to register there, run: " + shown(
            add_words("claude", [JZR_SPACED, "agent", "mcp"])
        )
        self.assertIn(expected, result.stdout.splitlines())
        self.assertIn(DESKTOP_ADD, result.stdout.splitlines())
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_a_module_launch_is_registered_word_by_word(self) -> None:
        self.make_home(".claude")
        self.jzr_words = ["/py/python", "-m", "jzr_tools"]

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(["/py/python", "-m", "jzr_tools", "agent", "mcp"], self.values(self.register.call_args))

    def test_dry_run_writes_and_runs_nothing_and_shows_the_command(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)

        result = self.jzr("install", "--mcp", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(self.register.call_count, 0)
        lines = result.stdout.splitlines()
        self.assertIn(f"would install jzr skill {__version__} -> {self.claude} (claude)", lines)
        self.assertIn("would run: " + shown(add_words(FAKE_CLAUDE, SERVER)), lines)
        self.assertNotIn("registered the jzr MCP server with Claude Code", lines)
        self.assertIn(DESKTOP_ADD, lines)
        self.assertNotIn("Restart", result.output)

    def test_agent_claude_is_allowed_with_mcp(self) -> None:
        result = self.jzr("install", "--mcp", "--agent", "claude")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.register.call_count, 1)
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_a_second_install_with_mcp_still_registers_and_says_the_skill_is_current(self) -> None:
        self.make_home(".claude")
        self.jzr("install")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            f"jzr skill {__version__} is already installed: {self.claude} (claude)", result.stdout.splitlines()
        )
        self.assertEqual(self.register.call_count, 1)

    def test_install_without_mcp_never_registers_and_prints_no_desktop_text(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.register.call_count, 0)
        self.assertNotIn("Claude Desktop", result.output)
        self.assertNotIn("MCP", result.output)


class McpOptionConflictTests(McpCase):
    def check_usage_error(self, *args: str) -> None:
        self.make_home(".claude", ".codex")
        before = _snapshot(self.root)

        result = self.jzr(*args)

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(CONFLICT, " ".join(result.stderr.split()))
        self.assertEqual(result.stdout, "")
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(self.register.call_count, 0)
        self.assertEqual(self.unregister.call_count, 0)

    def test_install_mcp_with_project_is_a_usage_error_and_writes_nothing(self) -> None:
        self.check_usage_error("install", "--mcp", "--project")

    def test_install_mcp_with_project_and_codex_is_a_usage_error_and_writes_nothing(self) -> None:
        self.check_usage_error("install", "--mcp", "--project", "--agent", "codex")

    def test_remove_mcp_with_project_is_a_usage_error_with_the_same_text(self) -> None:
        self.check_usage_error("remove", "--mcp", "--project")

    def test_the_old_wording_that_named_agent_codex_is_gone(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp", "--project")

        self.assertNotIn("--agent codex", result.stderr)
        self.assertNotIn("registers with Claude;", result.stderr)

    def test_install_mcp_with_agent_codex_is_allowed(self) -> None:
        self.make_home(".claude", ".codex")

        result = self.jzr("install", "--mcp", "--agent", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn(CONFLICT, result.output)
        self.assertTrue((self.agents / "SKILL.md").is_file())
        self.assertFalse(self.claude.exists())

    def test_the_conflict_wins_over_a_dry_run(self) -> None:
        self.check_usage_error("install", "--mcp", "--project", "--dry-run")

    def test_remove_mcp_with_project_or_codex_is_a_usage_error_and_deletes_nothing(self) -> None:
        self.make_home(".claude", ".codex")
        self.jzr("install")
        self.jzr("install", "--project")
        before = _snapshot(self.root)
        for extra in (("--project",), ("--project", "--agent", "codex")):
            with self.subTest(extra=extra):
                result = self.jzr("remove", "--mcp", *extra)

                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn(CONFLICT, " ".join(result.stderr.split()))
                self.assertEqual(_snapshot(self.root), before)
                self.assertEqual(self.unregister.call_count, 0)


class McpMissingPackageTests(McpCase):
    def test_install_mcp_without_the_package_stops_before_writing_anything(self) -> None:
        self.make_home(".claude")
        self.available = False
        before = _snapshot(self.root)

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.strip(), "error: " + NEEDS_EXTRA)
        self.assertEqual(result.stdout, "")
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(self.claude.exists())
        self.assertEqual(self.register.call_count, 0)

    def test_install_without_mcp_does_not_need_the_package(self) -> None:
        self.make_home(".claude")
        self.available = False

        result = self.jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_remove_mcp_does_not_need_the_package(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.available = False

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.unregister.call_count, 1)
        self.assertFalse(self.claude.exists())


class RemoveMcpTests(McpCase):
    def test_removes_the_registration_after_the_skill_and_names_the_desktop_step(self) -> None:
        self.make_home(".claude")
        self.jzr("install")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"removed jzr skill -> {self.claude} (claude)")
        self.assertEqual(lines.count("removed the jzr MCP server from Claude Code"), 1, lines)
        self.assertEqual(lines.count(DESKTOP_REMOVE), 1, lines)
        self.assertLess(lines.index("removed the jzr MCP server from Claude Code"), lines.index(DESKTOP_REMOVE))
        self.assertEqual(self.unregister.call_count, 1)
        self.assertIn(FAKE_CLAUDE, self.values(self.unregister.call_args))
        self.assertFalse(self.claude.exists())

    def test_a_server_that_was_never_registered_is_reported_and_is_not_an_error(self) -> None:
        self.make_home(".claude")
        self.unregistration = self.setup_module.Registration("absent")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("the jzr MCP server is not registered with Claude Code", result.stdout.splitlines())
        self.assertIn(DESKTOP_REMOVE, result.stdout.splitlines())

    def test_a_failed_unregistration_is_an_error_line_exit_1_and_the_skill_is_still_removed(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        self.unregistration = self.setup_module.Registration("failed", "cannot write the config")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: Claude Code: cannot write the config", result.stderr.splitlines())
        self.assertNotIn("Traceback", result.output)
        self.assertFalse(self.claude.exists())

    def test_dry_run_shows_the_command_and_changes_nothing(self) -> None:
        self.make_home(".claude")
        self.jzr("install")
        before = _snapshot(self.root)

        result = self.jzr("remove", "--mcp", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(self.unregister.call_count, 0)
        lines = result.stdout.splitlines()
        self.assertIn(f"would remove jzr skill -> {self.claude} (claude)", lines)
        self.assertIn("would run: " + shown(self.setup_module.remove_command(FAKE_CLAUDE)), lines)
        self.assertNotIn("removed the jzr MCP server from Claude Code", lines)
        self.assertIn(DESKTOP_REMOVE, lines)

    def test_remove_without_mcp_never_unregisters_and_prints_no_desktop_text(self) -> None:
        self.make_home(".claude")
        self.jzr("install")

        result = self.jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.unregister.call_count, 0)
        self.assertNotIn("Claude Desktop", result.output)
        self.assertNotIn("MCP", result.output)


FAKE_PROGRAM = """\
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
name = sys.argv[1]
with open(os.path.join(here, name + ".calls"), "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[2:]) + "\\n")
with open(os.path.join(here, name + ".reply.json"), encoding="utf-8") as handle:
    reply = json.load(handle)
sys.stdout.write(reply["out"])
sys.stderr.write(reply["err"])
sys.exit(reply["code"])
"""
CODEX_ADDED = "Added global MCP server 'jzr'.\n"
CODEX_REMOVED = "Removed global MCP server 'jzr'.\n"
CODEX_ABSENT = "No MCP server named 'jzr' found.\n"
CODE_ADDED = "Added MCP servers: jzr\n"
CODEX_REGISTERED = "registered the jzr MCP server with Codex"
CODEX_UNREGISTERED = "removed the jzr MCP server from Codex"
CODEX_NOT_REGISTERED = "the jzr MCP server is not registered with Codex"
VSCODE_REGISTERED = "registered the jzr MCP server with VS Code"
CURSOR_HEADER = (
    "Cursor: add this to ~/.cursor/mcp.json (or %USERPROFILE%\\.cursor\\mcp.json on Windows), "
    "or open this link:"
)
CURSOR_REMOVE = "Cursor: remove the jzr entry from ~/.cursor/mcp.json."
CURSOR_LINK = "cursor://anysphere.cursor-deeplink/mcp/install?name=jzr&config="
VSCODE_FILE = {
    "win32": "%APPDATA%\\Code\\User\\mcp.json",
    "darwin": "~/Library/Application Support/Code/User/mcp.json",
}.get(sys.platform, "~/.config/Code/User/mcp.json")
VSCODE_REMOVE = f'VS Code: remove the jzr entry from {VSCODE_FILE} (or run "MCP: Open User Configuration").'
CLIENT_NAMES = ("claude", "codex", "vscode", "cursor", "desktop")


class FakePrograms:
    """Stand-ins for `codex` and `code` that record their arguments and print a set reply."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "fake_program.py").write_text(FAKE_PROGRAM, encoding="utf-8")

    def make(self, name: str, *, code: int = 0, out: str = "", err: str = "") -> str:
        reply = {"code": code, "out": out, "err": err}
        (self.folder / f"{name}.reply.json").write_text(json.dumps(reply), encoding="utf-8")
        script = self.folder / "fake_program.py"
        if sys.platform == "win32":
            wrapper = self.folder / f"{name}.cmd"
            wrapper.write_text(
                f'@"{sys.executable}" "{script}" {name} %*\r\n@exit /b %ERRORLEVEL%\r\n',
                encoding="utf-8",
            )
        else:
            wrapper = self.folder / name
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" {name} "$@"\n', encoding="utf-8")
            wrapper.chmod(0o755)
        return str(wrapper)

    def calls(self, name: str) -> list[list[str]]:
        log = self.folder / f"{name}.calls"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


class ClientCase(McpCase):
    """The agent commands with `claude` replaced and `codex` and `code` as recording fakes."""

    def setUp(self) -> None:
        super().setUp()
        self.fakes = FakePrograms(self.root / "fakebin")

    def add_codex(self, **reply: object) -> str:
        reply.setdefault("out", CODEX_ADDED)
        self.codex_path = self.fakes.make("codex", **reply)
        return self.codex_path

    def add_code(self, **reply: object) -> str:
        reply.setdefault("out", CODE_ADDED)
        self.code_path = self.fakes.make("code", **reply)
        return self.code_path

    def cursor_block(self, stdout: str) -> tuple[dict, str]:
        lines = stdout.splitlines()
        start = lines.index(CURSOR_HEADER)
        link_at = next(i for i in range(start + 1, len(lines)) if lines[i].startswith(CURSOR_LINK))
        return json.loads("\n".join(lines[start + 1 : link_at])), lines[link_at]


class ClientOptionTests(ClientCase):
    def test_install_help_lists_the_client_option_with_its_five_names(self) -> None:
        for action in ("install", "remove"):
            with self.subTest(action=action):
                result = self.jzr(action, "-h")

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("--client", result.stdout)
                for name in CLIENT_NAMES:
                    self.assertIn(name, result.stdout)

    def test_client_without_mcp_is_a_usage_error_and_changes_nothing(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)
        for action in ("install", "remove"):
            with self.subTest(action=action):
                result = self.jzr(action, "--client", "codex")

                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("--client only applies to --mcp.", " ".join(result.stderr.split()))
                self.assertEqual(result.stdout, "")
                self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(self.register.call_count, 0)
        self.assertEqual(self.unregister.call_count, 0)

    def test_an_unknown_client_is_a_usage_error_and_changes_nothing(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root)
        for action in ("install", "remove"):
            with self.subTest(action=action):
                result = self.jzr(action, "--mcp", "--client", "bogus")

                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("bogus", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(_snapshot(self.root), before)

    def test_every_listed_client_is_accepted(self) -> None:
        self.make_home(".claude")
        self.add_codex()
        self.add_code()
        for name in CLIENT_NAMES:
            with self.subTest(client=name):
                result = self.jzr("install", "--mcp", "--client", name, "--dry-run")

                self.assertEqual(result.exit_code, 0, result.output)

    def test_agent_codex_with_mcp_installs_the_skill_for_codex_and_still_registers_every_client(self) -> None:
        self.make_home(".claude", ".codex")
        self.add_codex()

        result = self.jzr("install", "--mcp", "--agent", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(self.installed_line(self.agents, "codex"), lines)
        self.assertFalse(self.claude.exists())
        self.assertEqual(self.register.call_count, 1)
        self.assertIn(CODEX_REGISTERED, lines)
        self.assertEqual(len(self.fakes.calls("codex")), 1)

    def test_agent_does_not_pick_the_mcp_client_and_client_does_not_pick_the_skill_folder(self) -> None:
        self.make_home(".claude", ".codex")
        self.add_codex()

        result = self.jzr("install", "--mcp", "--client", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(self.installed_line(self.claude, "claude"), lines)
        self.assertIn(self.installed_line(self.agents, "codex"), lines)
        self.assertEqual(self.register.call_count, 0)
        self.assertIn(CODEX_REGISTERED, lines)


class CodexInstallTests(ClientCase):
    def test_a_codex_on_path_is_run_with_the_add_command_and_reported(self) -> None:
        self.make_home(".claude")
        self.add_codex()

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.fakes.calls("codex"), [["mcp", "add", "jzr", "--", JZR_EXE, "agent", "mcp"]])
        lines = result.stdout.splitlines()
        self.assertEqual(lines.count(CODEX_REGISTERED), 1, lines)
        self.assertEqual(self.register.call_count, 1)
        self.assertEqual(self.fakes.calls("code"), [])

    def test_codex_runs_after_the_skill_is_installed(self) -> None:
        self.make_home(".claude")
        self.add_codex()

        lines = self.jzr("install", "--mcp").stdout.splitlines()

        self.assertLess(
            lines.index(self.installed_line(self.claude, "claude")), lines.index(CODEX_REGISTERED)
        )

    def test_a_module_launch_is_passed_to_codex_word_by_word(self) -> None:
        self.make_home(".claude")
        self.add_codex()
        self.jzr_words = ["/py/python", "-m", "jzr_tools"]

        self.jzr("install", "--mcp")

        self.assertEqual(
            self.fakes.calls("codex"),
            [["mcp", "add", "jzr", "--", "/py/python", "-m", "jzr_tools", "agent", "mcp"]],
        )

    def test_a_failing_codex_is_an_error_line_with_its_last_output_line_and_exit_1(self) -> None:
        self.make_home(".claude")
        self.add_codex(code=1, out="loading\n", err="error: cannot write config.toml\n")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: Codex: error: cannot write config.toml", result.stderr.splitlines())
        self.assertNotIn(CODEX_REGISTERED, result.stdout.splitlines())
        self.assertNotIn("Traceback", result.output)
        self.assertTrue((self.claude / "SKILL.md").is_file())
        self.assertEqual(self.register.call_count, 1)

    def test_an_exit_zero_without_the_added_text_is_an_error_too(self) -> None:
        self.make_home(".claude")
        self.add_codex(code=0, out="all done\n")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: Codex: all done", result.stderr.splitlines())
        self.assertNotIn(CODEX_REGISTERED, result.stdout.splitlines())

    def test_a_codex_that_is_not_on_path_is_skipped_without_a_word(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Codex", result.output)
        self.assertEqual(self.fakes.calls("codex"), [])

    def test_client_codex_without_codex_prints_the_command_and_exits_zero(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp", "--client", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        hint = [line for line in lines if "not on PATH" in line]
        self.assertEqual(len(hint), 1, lines)
        self.assertTrue(hint[0].startswith("Codex"), hint[0])
        self.assertTrue(hint[0].endswith("run: " + shown(["codex", "mcp", "add", "jzr", "--", *SERVER])), hint[0])
        self.assertEqual(self.register.call_count, 0)

    def test_client_codex_handles_codex_alone(self) -> None:
        self.make_home(".claude")
        self.add_codex()
        self.add_code()

        result = self.jzr("install", "--mcp", "--client", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(CODEX_REGISTERED, lines)
        self.assertEqual(self.register.call_count, 0)
        self.assertEqual(self.fakes.calls("code"), [])
        self.assertNotIn(DESKTOP_ADD, lines)
        self.assertNotIn(CURSOR_HEADER, lines)
        self.assertNotIn("Claude Code", result.output)

    def test_dry_run_shows_the_command_and_does_not_start_codex(self) -> None:
        self.make_home(".claude")
        codex = self.add_codex()
        before = _snapshot(self.root / "home")

        result = self.jzr("install", "--mcp", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn("would run: " + shown([codex, "mcp", "add", "jzr", "--", *SERVER]), lines)
        self.assertNotIn(CODEX_REGISTERED, lines)
        self.assertEqual(self.fakes.calls("codex"), [])
        self.assertEqual(_snapshot(self.root / "home"), before)


class VscodeInstallTests(ClientCase):
    def test_a_code_on_path_gets_the_add_mcp_json_as_one_argument(self) -> None:
        self.make_home(".claude")
        self.add_code()

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        (call,) = self.fakes.calls("code")
        self.assertEqual(len(call), 2, call)
        self.assertEqual(call[0], "--add-mcp")
        self.assertEqual(
            json.loads(call[1]),
            {"name": "jzr", "type": "stdio", "command": JZR_EXE, "args": ["agent", "mcp"]},
        )
        self.assertEqual(result.stdout.splitlines().count(VSCODE_REGISTERED), 1)

    def test_a_path_with_spaces_reaches_code_intact(self) -> None:
        self.make_home(".claude")
        self.add_code()
        self.jzr_words = [JZR_SPACED]

        self.jzr("install", "--mcp")

        (call,) = self.fakes.calls("code")
        document = json.loads(call[1])
        self.assertEqual(document["command"], JZR_SPACED)
        self.assertEqual(document["args"], ["agent", "mcp"])

    def test_bad_json_is_an_error_even_though_code_exits_zero(self) -> None:
        self.make_home(".claude")
        self.add_code(code=0, out="Invalid JSON: Unexpected token\n")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: VS Code: Invalid JSON: Unexpected token", result.stderr.splitlines())
        self.assertNotIn(VSCODE_REGISTERED, result.stdout.splitlines())
        self.assertNotIn("Traceback", result.output)

    def test_a_code_that_is_not_on_path_is_skipped_without_a_word(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("VS Code", result.output)
        self.assertNotIn("--add-mcp", result.output)

    def test_client_vscode_without_code_prints_the_command_and_exits_zero(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp", "--client", "vscode")

        self.assertEqual(result.exit_code, 0, result.output)
        hint = [line for line in result.stdout.splitlines() if "not on PATH" in line]
        self.assertEqual(len(hint), 1, result.stdout)
        self.assertTrue(hint[0].startswith("VS Code"), hint[0])
        self.assertIn("run: code --add-mcp ", hint[0])
        self.assertIn("jzr", hint[0])
        self.assertEqual(self.register.call_count, 0)

    def test_client_vscode_handles_vs_code_alone(self) -> None:
        self.make_home(".claude")
        self.add_code()
        self.add_codex()

        result = self.jzr("install", "--mcp", "--client", "vscode")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(VSCODE_REGISTERED, result.stdout.splitlines())
        self.assertEqual(self.fakes.calls("codex"), [])
        self.assertEqual(self.register.call_count, 0)

    def test_dry_run_shows_the_command_and_does_not_start_code(self) -> None:
        self.make_home(".claude")
        code = self.add_code()

        result = self.jzr("install", "--mcp", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        shown_lines = [line for line in result.stdout.splitlines() if line.startswith("would run: ") and "--add-mcp" in line]
        self.assertEqual(len(shown_lines), 1, result.stdout)
        self.assertEqual(shown_lines[0], "would run: " + shown(self.setup_module.vscode_add_command(code, SERVER)))
        self.assertEqual(self.fakes.calls("code"), [])
        self.assertNotIn(VSCODE_REGISTERED, result.stdout.splitlines())


class CursorAndDesktopInstallTests(ClientCase):
    def test_the_cursor_entry_and_link_are_printed_without_any_cursor_folder(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        document, link = self.cursor_block(result.stdout)
        entry = document.get("mcpServers", document)
        entry = entry.get("jzr", entry)
        self.assertEqual(entry["command"], JZR_EXE)
        self.assertEqual(entry["args"], ["agent", "mcp"])
        self.assertFalse((self.home / ".cursor").exists())

    def test_the_link_holds_the_base64_of_the_compact_command_json(self) -> None:
        self.make_home(".claude")
        self.jzr_words = [JZR_SPACED]

        result = self.jzr("install", "--mcp")

        _document, link = self.cursor_block(result.stdout)
        compact = json.dumps({"command": JZR_SPACED, "args": ["agent", "mcp"]}, separators=(",", ":"))
        self.assertEqual(link, CURSOR_LINK + base64.b64encode(compact.encode("utf-8")).decode("ascii"))
        self.assertEqual(base64.b64decode(link[len(CURSOR_LINK):], validate=True).decode("utf-8"), compact)

    def test_the_desktop_entry_is_still_printed(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp")

        _block, document = self.desktop_block(result.stdout)
        self.assertEqual(document["mcpServers"]["jzr"]["command"], JZR_EXE)

    def test_client_cursor_prints_only_the_cursor_entry_and_runs_nothing(self) -> None:
        self.make_home(".claude")
        self.add_codex()
        self.add_code()

        result = self.jzr("install", "--mcp", "--client", "cursor")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(CURSOR_HEADER, lines)
        self.assertNotIn(DESKTOP_ADD, lines)
        self.assertEqual(self.register.call_count, 0)
        self.assertEqual(self.fakes.calls("codex"), [])
        self.assertEqual(self.fakes.calls("code"), [])

    def test_client_desktop_prints_only_the_desktop_entry(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp", "--client", "desktop")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(DESKTOP_ADD, lines)
        self.assertNotIn(CURSOR_HEADER, lines)
        self.assertEqual(self.register.call_count, 0)

    def test_client_claude_prints_neither_entry(self) -> None:
        self.make_home(".claude")

        result = self.jzr("install", "--mcp", "--client", "claude")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertNotIn(DESKTOP_ADD, lines)
        self.assertNotIn(CURSOR_HEADER, lines)
        self.assertIn("registered the jzr MCP server with Claude Code", lines)

    def test_a_dry_run_still_prints_the_entries_and_writes_nothing(self) -> None:
        self.make_home(".claude")
        before = _snapshot(self.root / "home")

        result = self.jzr("install", "--mcp", "--dry-run")

        lines = result.stdout.splitlines()
        self.assertIn(CURSOR_HEADER, lines)
        self.assertIn(DESKTOP_ADD, lines)
        self.assertEqual(_snapshot(self.root / "home"), before)


class ClientRemoveTests(ClientCase):
    def test_codex_is_run_with_the_remove_command_and_reported(self) -> None:
        self.make_home(".claude")
        self.add_codex(out=CODEX_REMOVED)

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.fakes.calls("codex"), [["mcp", "remove", "jzr"]])
        self.assertEqual(result.stdout.splitlines().count(CODEX_UNREGISTERED), 1)

    def test_a_codex_without_the_server_is_reported_and_is_not_an_error(self) -> None:
        self.make_home(".claude")
        self.add_codex(code=0, out=CODEX_ABSENT)

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(CODEX_NOT_REGISTERED, result.stdout.splitlines())
        self.assertNotIn(CODEX_UNREGISTERED, result.stdout.splitlines())

    def test_a_failing_codex_is_an_error_line_and_exit_1(self) -> None:
        self.make_home(".claude")
        self.add_codex(code=1, out="", err="error: config is locked\n")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: Codex: error: config is locked", result.stderr.splitlines())

    def test_a_codex_that_is_not_on_path_is_skipped_without_a_word(self) -> None:
        self.make_home(".claude")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Codex", result.output)

    def test_client_codex_without_codex_prints_the_command_and_exits_zero(self) -> None:
        self.make_home(".claude")

        result = self.jzr("remove", "--mcp", "--client", "codex")

        self.assertEqual(result.exit_code, 0, result.output)
        hint = [line for line in result.stdout.splitlines() if "not on PATH" in line]
        self.assertEqual(len(hint), 1, result.stdout)
        self.assertTrue(hint[0].startswith("Codex"), hint[0])
        self.assertTrue(hint[0].endswith("run: " + shown(["codex", "mcp", "remove", "jzr"])), hint[0])

    def test_dry_run_shows_the_codex_command_and_runs_nothing(self) -> None:
        self.make_home(".claude")
        codex = self.add_codex(out=CODEX_REMOVED)

        result = self.jzr("remove", "--mcp", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("would run: " + shown([codex, "mcp", "remove", "jzr"]), result.stdout.splitlines())
        self.assertNotIn(CODEX_UNREGISTERED, result.stdout.splitlines())
        self.assertEqual(self.fakes.calls("codex"), [])

    def test_vscode_says_where_to_delete_the_entry_and_runs_nothing(self) -> None:
        self.make_home(".claude")
        self.add_code()

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(VSCODE_REMOVE, result.stdout.splitlines())
        self.assertEqual(self.fakes.calls("code"), [])

    def test_client_vscode_says_where_to_delete_the_entry_only(self) -> None:
        self.make_home(".claude")
        self.add_code()
        self.add_codex()

        result = self.jzr("remove", "--mcp", "--client", "vscode")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(VSCODE_REMOVE, lines)
        self.assertNotIn(DESKTOP_REMOVE, lines)
        self.assertNotIn(CURSOR_REMOVE, lines)
        self.assertEqual(self.fakes.calls("codex"), [])
        self.assertEqual(self.unregister.call_count, 0)

    def test_cursor_says_where_to_delete_the_entry(self) -> None:
        self.make_home(".claude")

        result = self.jzr("remove", "--mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(CURSOR_REMOVE, lines)
        self.assertIn(DESKTOP_REMOVE, lines)

    def test_client_cursor_says_only_the_cursor_line(self) -> None:
        self.make_home(".claude")

        result = self.jzr("remove", "--mcp", "--client", "cursor")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(CURSOR_REMOVE, lines)
        self.assertNotIn(DESKTOP_REMOVE, lines)
        self.assertEqual(self.unregister.call_count, 0)

    def test_client_desktop_says_only_the_desktop_line(self) -> None:
        self.make_home(".claude")

        result = self.jzr("remove", "--mcp", "--client", "desktop")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertIn(DESKTOP_REMOVE, lines)
        self.assertNotIn(CURSOR_REMOVE, lines)

    def test_client_claude_unregisters_claude_alone(self) -> None:
        self.make_home(".claude")
        self.add_codex(out=CODEX_REMOVED)

        result = self.jzr("remove", "--mcp", "--client", "claude")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.unregister.call_count, 1)
        self.assertEqual(self.fakes.calls("codex"), [])
        self.assertNotIn(CURSOR_REMOVE, result.stdout.splitlines())


class McpActionTests(McpCase):
    def fake_server_module(self):  # noqa: ANN202
        module = types.ModuleType("jzr_tools.commands._mcp_server")
        module.serve = mock.Mock()
        module.build_server = mock.Mock()
        return module

    def with_fake_server(self, module: types.ModuleType) -> None:
        import jzr_tools.commands as commands

        patchers = (
            mock.patch.dict(sys.modules, {"jzr_tools.commands._mcp_server": module}),
            mock.patch.object(commands, "_mcp_server", module, create=True),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_without_the_package_it_prints_the_error_and_starts_no_server(self) -> None:
        module = self.fake_server_module()
        self.with_fake_server(module)
        self.available = False

        result = self.jzr("mcp")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.strip(), "error: " + NEEDS_EXTRA)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.output)
        self.assertEqual(module.serve.call_count, 0)

    def test_with_the_package_it_serves_once_and_prints_nothing_on_stdout(self) -> None:
        module = self.fake_server_module()
        self.with_fake_server(module)

        result = self.jzr("mcp")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(module.serve.call_count, 1)
        self.assertEqual(result.stdout, "")

    def test_mcp_takes_no_arguments(self) -> None:
        module = self.fake_server_module()
        self.with_fake_server(module)

        result = self.jzr("mcp", "extra")

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(module.serve.call_count, 0)


if __name__ == "__main__":
    unittest.main()

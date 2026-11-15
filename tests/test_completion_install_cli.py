"""Behavioural tests for `jzr completion install` and `jzr completion remove`.

Every test runs against a throw-away home folder. The process, shell and
PowerShell lookups are replaced, so no real profile, start-up file or
subprocess is ever touched.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools import __version__
from jzr_tools.cli import app

MARK_START = "# >>> jzr completion >>>"
MARK_END = "# <<< jzr completion <<<"

_ISOLATED_VARIABLES = (
    "HOME", "USERPROFILE", "JZR_DATA_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "ZDOTDIR", "SHELL", "LOCALAPPDATA", "JZR_CONFIG_DIR",
)


def _core():
    return importlib.import_module("jzr_tools.core.completion")


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    found: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_dir():
            found[key] = (b"<dir>", 0)
        else:
            found[key] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


class CompletionInstallCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-completion-install-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.data = self.home / ".local" / "share" / "jzr"
        self.config = self.home / ".config"
        self.env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "JZR_DATA_DIR": str(self.data),
            "XDG_CONFIG_HOME": str(self.config),
            "JZR_CONFIG_DIR": str(self.root / "bindings"),
        }
        patched = mock.patch.dict(os.environ, self.env)
        patched.start()
        self.addCleanup(patched.stop)
        for name in _ISOLATED_VARIABLES:
            if name not in self.env:
                os.environ.pop(name, None)

        self.ancestors: list = []
        self.policy = "RemoteSigned"
        self.profiles: dict[str, Path] = {}
        self.info_calls: list[str] = []
        self.on_path: list[str] = []
        self.bash_ver: tuple[int, int] | None = (5, 2)
        self.info_errors: dict[str, str] = {}

        self._patch("parent_processes", side_effect=lambda *a, **k: list(self.ancestors))
        self._patch("powershell_info", side_effect=self._fake_info)
        self._patch("powershells_on_path", side_effect=lambda *a, **k: list(self.on_path))
        self._patch("bash_version", side_effect=lambda *a, **k: self.bash_ver)

        self.runner = CliRunner()

    def _patch(self, name: str, **kwargs):
        patcher = mock.patch(f"jzr_tools.core.completion.{name}", **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def _fake_info(self, exe, *args, **kwargs):
        core = _core()
        self.info_calls.append(exe)
        if exe in self.info_errors:
            raise core.CompletionError(self.info_errors[exe])
        profile = self.profiles.get(exe)
        if profile is None:
            profile = self.home / "Documents" / "PowerShell" / "profile.ps1"
        return core.PowerShellInfo(profile=profile, policy=self.policy)

    def run_jzr(self, *args: str):
        return self.runner.invoke(app, ["completion", *args], env=self.env)

    def ancestor(self, name: str, exe: str = ""):
        return _core().Ancestor(name, exe)

    def launchers(self, *shell) -> list:
        return [
            self.ancestor("python.exe", "C:/py/python.exe"),
            self.ancestor("uv.exe", "C:/tools/uv.exe"),
            *shell,
        ]

    def shown(self, path: Path) -> str:
        return "~" + os.sep + str(path.relative_to(self.home))

    @property
    def bashrc(self) -> Path:
        return self.home / ".bashrc"

    @property
    def zshrc(self) -> Path:
        return self.home / ".zshrc"

    def script(self, shell: str) -> Path:
        suffix = {"bash": "bash", "zsh": "zsh", "powershell": "ps1"}[shell]
        return self.data / "completion" / f"jzr.{suffix}"

    def printed_script(self, shell: str) -> bytes:
        result = self.runner.invoke(app, ["completion", shell])
        self.assertEqual(result.exit_code, 0, result.output)
        return result.stdout_bytes


class InstallBashTests(CompletionInstallCase):
    def setUp(self) -> None:
        super().setUp()
        self.ancestors = self.launchers(self.ancestor("bash.exe", "C:/Git/usr/bin/bash.exe"))

    def test_detects_bash_from_the_parent_process_and_reports_each_step(self) -> None:
        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Shell: bash (parent process)")
        self.assertIn(f"Wrote {self.shown(self.script('bash'))}", lines)
        self.assertIn(f"Added completion to {self.shown(self.bashrc)}", lines)
        self.assertEqual(
            lines[-1], "Open a new terminal to use it. Undo: jzr completion remove bash"
        )
        self.assertEqual(result.stderr, "")

    def test_writes_the_script_with_a_version_header_and_the_printed_script(self) -> None:
        self.run_jzr("install")

        written = self.script("bash").read_bytes()
        header = (
            f"# jzr completion for bash, written by jzr {__version__}; "
            "re-run 'jzr completion install' after upgrading\n"
        ).encode()
        self.assertEqual(written, header + self.printed_script("bash"))
        self.assertNotIn(b"\r", written)

    def test_adds_one_marked_block_that_sources_the_script(self) -> None:
        self.run_jzr("install")

        text = self.bashrc.read_text(encoding="utf-8")
        path = str(self.script("bash"))
        if sys.platform == "win32":
            path = path.replace("\\", "/")
        self.assertEqual(text.count(MARK_START), 1)
        self.assertEqual(text.count(MARK_END), 1)
        self.assertIn(f'[ -f "{path}" ] && . "{path}"', text)
        self.assertLess(text.index(MARK_START), text.index(path))
        self.assertLess(text.index(path), text.index(MARK_END))

    def test_keeps_what_the_start_up_file_already_holds(self) -> None:
        self.bashrc.write_text("alias ll='ls -l'\nexport EDITOR=vim\n", encoding="utf-8")

        self.run_jzr("install")

        text = self.bashrc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("alias ll='ls -l'\nexport EDITOR=vim\n"))
        self.assertIn(MARK_START, text)

    def test_a_second_run_says_so_and_changes_no_file(self) -> None:
        self.run_jzr("install")
        before = _snapshot(self.root)

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            f"Completion for bash is already set up ({self.shown(self.bashrc)}).", result.stdout
        )
        self.assertNotIn("Wrote", result.stdout)
        self.assertNotIn("Added completion", result.stdout)
        self.assertEqual(_snapshot(self.root), before)

    def test_a_changed_script_is_rewritten_and_a_changed_block_replaced_in_place(self) -> None:
        self.run_jzr("install")
        self.script("bash").write_text("# stale\n", encoding="utf-8")
        text = self.bashrc.read_text(encoding="utf-8")
        self.bashrc.write_text(
            text.replace('[ -f "', '[ -e "') + "export AFTER=1\n", encoding="utf-8"
        )

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Updated " + self.shown(self.script("bash")), result.stdout)
        self.assertIn("Updated completion in " + self.shown(self.bashrc), result.stdout)
        fixed = self.bashrc.read_text(encoding="utf-8")
        self.assertEqual(fixed.count(MARK_START), 1)
        self.assertNotIn('[ -e "', fixed)
        self.assertTrue(fixed.endswith("export AFTER=1\n"))
        self.assertNotEqual(self.script("bash").read_bytes(), b"# stale\n")

    def test_dry_run_describes_the_changes_and_writes_nothing(self) -> None:
        before = _snapshot(self.root)

        result = self.run_jzr("install", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"Would write {self.shown(self.script('bash'))}", result.stdout)
        self.assertIn(f"Would add completion to {self.shown(self.bashrc)}", result.stdout)
        self.assertNotIn("Open a new terminal", result.stdout)
        self.assertEqual(_snapshot(self.root), before)

    def test_setup_is_the_same_command_as_install(self) -> None:
        via_install = self.run_jzr("install")
        installed = self.bashrc.read_bytes()
        self.bashrc.unlink()
        self.script("bash").unlink()

        via_setup = self.run_jzr("setup")

        self.assertEqual(via_setup.exit_code, 0, via_setup.output)
        self.assertEqual(via_setup.stdout, via_install.stdout)
        self.assertEqual(self.bashrc.read_bytes(), installed)

    def test_an_old_bash_still_gets_the_block_and_a_warning_on_stderr(self) -> None:
        self.bash_ver = (3, 2)

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(MARK_START, self.bashrc.read_text(encoding="utf-8"))
        self.assertIn(
            "warning: bash 3.2 is too old for jzr completion (4.4 or newer is needed)",
            result.stderr,
        )

    def test_a_bash_profile_that_skips_bashrc_is_warned_about_and_left_alone(self) -> None:
        profile = self.home / ".bash_profile"
        profile.write_text("export A=1\n", encoding="utf-8")

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("warning: ", result.stderr)
        self.assertIn(".bash_profile does not load", result.stderr)
        self.assertIn("[ -f ~/.bashrc ] && . ~/.bashrc", result.stderr)
        self.assertEqual(profile.read_text(encoding="utf-8"), "export A=1\n")

    def test_paths_under_home_are_shown_with_a_tilde(self) -> None:
        result = self.run_jzr("install")

        self.assertNotIn(str(self.home), result.stdout)
        self.assertIn("~" + os.sep, result.stdout)

    def test_an_unwritable_start_up_file_fails_cleanly_and_leaves_no_script(self) -> None:
        self.bashrc.mkdir()

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: ", result.stderr)
        self.assertIn(".bashrc", result.stderr)
        self.assertNotIn("Traceback", result.output)
        self.assertFalse(self.script("bash").exists())


GIT_BASH_NOTE = (
    "Note: the next Git Bash window will say it found ~/.bashrc but no ~/.bash_profile "
    "and create one that loads ~/.bashrc. That is expected."
)


class InstallGitBashNoteTests(CompletionInstallCase):
    def setUp(self) -> None:
        super().setUp()
        self.ancestors = self.launchers(self.ancestor("bash.exe", "C:/Git/usr/bin/bash.exe"))

    def run_on(self, platform: str, *args: str):
        with mock.patch.object(sys, "platform", platform):
            return self.run_jzr(*args)

    def test_windows_install_prints_the_note_between_the_changes_and_the_closing_line(self) -> None:
        result = self.run_on("win32", "install")

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertEqual(lines.count(GIT_BASH_NOTE), 1, result.output)
        added = lines.index(f"Added completion to {self.shown(self.bashrc)}")
        note = lines.index(GIT_BASH_NOTE)
        closing = next(i for i, line in enumerate(lines) if line.startswith("Open a new terminal"))
        self.assertLess(added, note)
        self.assertLess(note, closing)
        self.assertNotIn("Git Bash window", result.stderr)

    def test_a_dry_run_prints_the_note_too(self) -> None:
        result = self.run_on("win32", "install", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(GIT_BASH_NOTE, result.stdout.splitlines())
        self.assertFalse(self.bashrc.exists())

    def test_a_bash_profile_that_loads_bashrc_means_no_note(self) -> None:
        (self.home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\n", encoding="utf-8")

        result = self.run_on("win32", "install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Added completion to", result.stdout)
        self.assertNotIn("Git Bash window", result.output)

    def test_a_second_run_that_changes_nothing_prints_no_note(self) -> None:
        first = self.run_on("win32", "install")
        self.assertIn(GIT_BASH_NOTE, first.stdout)

        second = self.run_on("win32", "install")

        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("is already set up", second.stdout)
        self.assertNotIn("Git Bash window", second.output)

    def test_zsh_never_gets_the_note(self) -> None:
        result = self.run_on("win32", "install", "zsh")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Added completion to", result.stdout)
        self.assertNotIn("Git Bash window", result.output)

    def test_other_platforms_print_no_note(self) -> None:
        result = self.run_on("linux", "install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Added completion to", result.stdout)
        self.assertNotIn("Git Bash window", result.output)


class InstallNamedShellTests(CompletionInstallCase):
    def test_a_named_shell_says_named_and_skips_the_process_walk(self) -> None:
        self.ancestors = self.launchers(self.ancestor("bash.exe"))

        result = self.run_jzr("install", "zsh")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "Shell: zsh (named)")
        self.assertIn(f"Wrote {self.shown(self.script('zsh'))}", result.stdout)
        self.assertIn(f"Added completion to {self.shown(self.zshrc)}", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("Undo: jzr completion remove zsh"))
        self.assertFalse(self.bashrc.exists())

    def test_zsh_block_starts_compinit_only_when_it_is_missing_then_sources_the_script(self) -> None:
        self.run_jzr("install", "zsh")

        text = self.zshrc.read_text(encoding="utf-8")
        path = str(self.script("zsh"))
        if sys.platform == "win32":
            path = path.replace("\\", "/")
        guard = "(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }"
        source = f'[ -f "{path}" ] && . "{path}"'
        self.assertIn(guard, text)
        self.assertIn(source, text)
        self.assertLess(text.index(guard), text.index(source))

    def test_zsh_uses_zdotdir_when_it_is_set(self) -> None:
        zdotdir = self.home / "zdot"
        zdotdir.mkdir()
        env = dict(self.env, ZDOTDIR=str(zdotdir))

        with mock.patch.dict(os.environ, {"ZDOTDIR": str(zdotdir)}):
            result = self.runner.invoke(app, ["completion", "install", "zsh"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(MARK_START, (zdotdir / ".zshrc").read_text(encoding="utf-8"))
        self.assertFalse(self.zshrc.exists())

    def test_an_unknown_shell_is_a_usage_error(self) -> None:
        for command in ("install", "remove", "setup"):
            with self.subTest(command=command):
                result = self.run_jzr(command, "tcsh")

                self.assertEqual(result.exit_code, 2)
                self.assertIn("tcsh", result.stderr)
        self.assertEqual(_snapshot(self.root).get(".bashrc"), None)

    @unittest.skipIf(sys.platform == "win32", "the SHELL fallback is for macOS and Linux")
    def test_without_a_shell_among_the_ancestors_the_shell_variable_decides(self) -> None:
        self.ancestors = self.launchers()
        env = dict(self.env, SHELL="/usr/bin/zsh")

        with mock.patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
            result = self.runner.invoke(app, ["completion", "install"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "Shell: zsh (from $SHELL)")


class InstallFishTests(CompletionInstallCase):
    def test_fish_gets_only_the_completions_file(self) -> None:
        self.ancestors = self.launchers(self.ancestor("fish", "/usr/bin/fish"))
        target = self.config / "fish" / "completions" / "jzr.fish"

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "Shell: fish (parent process)")
        self.assertIn(f"Wrote {self.shown(target)}", result.stdout)
        self.assertNotIn("Added completion", result.stdout)
        header = (
            f"# jzr completion for fish, written by jzr {__version__}; "
            "re-run 'jzr completion install' after upgrading\n"
        ).encode()
        self.assertEqual(target.read_bytes(), header + self.printed_script("fish"))
        self.assertFalse(self.data.exists())
        self.assertEqual(
            sorted(p.name for p in self.home.rglob("*") if p.is_file()), ["jzr.fish"]
        )

    def test_remove_deletes_the_file_it_wrote_but_not_a_foreign_one(self) -> None:
        self.ancestors = self.launchers(self.ancestor("fish", "/usr/bin/fish"))
        target = self.config / "fish" / "completions" / "jzr.fish"
        self.run_jzr("install")

        removed = self.run_jzr("remove")

        self.assertEqual(removed.exit_code, 0, removed.output)
        self.assertIn(f"Deleted {self.shown(target)}", removed.stdout)
        self.assertFalse(target.exists())

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# my own completions\ncomplete -c jzr\n", encoding="utf-8")
        foreign = self.run_jzr("remove")

        self.assertEqual(foreign.exit_code, 1, foreign.output)
        self.assertIn("is not jzr's", foreign.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "# my own completions\ncomplete -c jzr\n")


class InstallPowerShellTests(CompletionInstallCase):
    def setUp(self) -> None:
        super().setUp()
        self.pwsh = "C:/Program Files/PowerShell/7/pwsh.exe"
        self.profile = self.home / "Documents" / "PowerShell" / "profile.ps1"
        self.profiles[self.pwsh] = self.profile

    def test_pwsh_ancestor_uses_the_profile_it_reports_and_is_named_on_the_shell_line(self) -> None:
        self.ancestors = self.launchers(self.ancestor("pwsh.exe", self.pwsh))

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines()[0], "Shell: powershell (pwsh.exe, parent process)"
        )
        self.assertEqual(self.info_calls, [self.pwsh])
        self.assertIn(f"Wrote {self.shown(self.script('powershell'))}", result.stdout)
        self.assertIn(f"Added completion to {self.shown(self.profile)}", result.stdout)
        text = self.profile.read_text(encoding="utf-8")
        path = str(self.script("powershell"))
        self.assertIn(f"if (Test-Path '{path}') {{ . '{path}' }}", text)
        self.assertEqual(text.count(MARK_START), 1)
        self.assertEqual(result.stderr, "")

    def test_a_restricted_policy_warns_on_stderr_and_still_succeeds(self) -> None:
        self.ancestors = self.launchers(self.ancestor("pwsh.exe", self.pwsh))
        self.policy = "Restricted"

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "warning: PowerShell will not run the profile while the execution policy is "
            "Restricted. To allow it for your user: "
            "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned",
            result.stderr,
        )
        self.assertTrue(self.profile.exists())

    def test_the_warning_comes_after_the_changes_and_not_on_stdout(self) -> None:
        self.ancestors = self.launchers(self.ancestor("pwsh.exe", self.pwsh))
        self.policy = "AllSigned"

        result = self.run_jzr("install")

        self.assertNotIn("warning", result.stdout)
        self.assertIn("Set-ExecutionPolicy -Scope CurrentUser RemoteSigned", result.stderr)

    def test_a_remote_signed_policy_gives_no_warning(self) -> None:
        self.ancestors = self.launchers(self.ancestor("pwsh.exe", self.pwsh))

        result = self.run_jzr("install")

        self.assertNotIn("Set-ExecutionPolicy", result.stderr)

    def test_naming_powershell_without_one_among_the_ancestors_sets_up_every_one_on_path(self) -> None:
        self.ancestors = self.launchers(self.ancestor("bash.exe"))
        windows_ps = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        other_profile = self.home / "Documents" / "WindowsPowerShell" / "profile.ps1"
        self.profiles[windows_ps] = other_profile
        self.on_path = [self.pwsh, windows_ps]

        result = self.run_jzr("install", "powershell")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(sorted(self.info_calls), sorted([self.pwsh, windows_ps]))
        for profile in (self.profile, other_profile):
            with self.subTest(profile=profile.parent.name):
                self.assertIn(MARK_START, profile.read_text(encoding="utf-8"))
                self.assertIn(f"Added completion to {self.shown(profile)}", result.stdout)

    def test_naming_powershell_with_one_among_the_ancestors_sets_up_only_that_one(self) -> None:
        self.ancestors = self.launchers(self.ancestor("pwsh.exe", self.pwsh))
        windows_ps = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        other_profile = self.home / "Documents" / "WindowsPowerShell" / "profile.ps1"
        self.profiles[windows_ps] = other_profile
        self.on_path = [self.pwsh, windows_ps]

        result = self.run_jzr("install", "powershell")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(self.profile.exists())
        self.assertFalse(other_profile.exists())

    def test_one_powershell_failing_does_not_stop_the_other_but_fails_the_run(self) -> None:
        self.ancestors = self.launchers()
        broken = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        self.on_path = [broken, self.pwsh]
        self.info_errors[broken] = "powershell did not answer within 10 seconds"

        result = self.run_jzr("install", "powershell")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("error: powershell did not answer within 10 seconds", result.stderr)
        self.assertNotIn("Traceback", result.output)
        self.assertIn(MARK_START, self.profile.read_text(encoding="utf-8"))


class InstallUnsupportedShellTests(CompletionInstallCase):
    def test_cmd_has_no_completion_and_fails_without_a_traceback_or_files(self) -> None:
        self.ancestors = self.launchers(self.ancestor("cmd.exe", "C:/Windows/System32/cmd.exe"))
        before = _snapshot(self.root)

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "error: cmd.exe has no programmable completion; use PowerShell, "
            "or name a shell: jzr completion install SHELL",
            result.stderr,
        )
        self.assertNotIn("Traceback", result.output)
        self.assertEqual(_snapshot(self.root), before)

    def test_an_unsupported_shell_is_named_with_the_supported_ones(self) -> None:
        self.ancestors = self.launchers(self.ancestor("dash", "/bin/dash"))

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "error: dash is not supported; supported: bash, zsh, fish, powershell",
            result.stderr,
        )

    @unittest.skipIf(sys.platform != "win32", "the SHELL fallback does not apply on Windows")
    def test_on_windows_with_nothing_found_it_asks_for_a_name(self) -> None:
        self.ancestors = []

        result = self.run_jzr("install")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "error: could not tell which shell this is; name it: jzr completion install SHELL",
            result.stderr,
        )


class RemoveTests(CompletionInstallCase):
    def setUp(self) -> None:
        super().setUp()
        self.ancestors = self.launchers(self.ancestor("bash.exe", "C:/Git/usr/bin/bash.exe"))

    def test_remove_after_install_takes_out_the_block_and_the_script(self) -> None:
        self.bashrc.write_text("alias ll='ls -l'\n", encoding="utf-8")
        self.run_jzr("install")

        result = self.run_jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "Shell: bash (parent process)")
        self.assertIn(f"Removed completion from {self.shown(self.bashrc)}", result.stdout)
        self.assertIn(f"Deleted {self.shown(self.script('bash'))}", result.stdout)
        text = self.bashrc.read_text(encoding="utf-8")
        self.assertNotIn(MARK_START, text)
        self.assertNotIn(MARK_END, text)
        self.assertIn("alias ll='ls -l'", text)
        self.assertFalse(self.script("bash").exists())

    def test_remove_with_nothing_installed_says_so_and_changes_nothing(self) -> None:
        before = _snapshot(self.root)

        result = self.run_jzr("remove")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Completion for bash is not set up.", result.stdout)
        self.assertEqual(_snapshot(self.root), before)

    def test_remove_dry_run_reports_and_leaves_everything(self) -> None:
        self.run_jzr("install")
        before = _snapshot(self.root)

        result = self.run_jzr("remove", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"Would remove completion from {self.shown(self.bashrc)}", result.stdout)
        self.assertIn(f"Would delete {self.shown(self.script('bash'))}", result.stdout)
        self.assertEqual(_snapshot(self.root), before)

    def test_remove_of_a_named_shell_leaves_other_shells_alone(self) -> None:
        self.run_jzr("install", "bash")
        self.run_jzr("install", "zsh")

        result = self.run_jzr("remove", "zsh")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], "Shell: zsh (named)")
        self.assertNotIn(MARK_START, self.zshrc.read_text(encoding="utf-8"))
        self.assertIn(MARK_START, self.bashrc.read_text(encoding="utf-8"))
        self.assertTrue(self.script("bash").exists())


class GroupHelpTests(CompletionInstallCase):
    def test_help_lists_install_and_remove_but_not_the_setup_alias(self) -> None:
        result = self.run_jzr("--help")

        self.assertEqual(result.exit_code, 0, result.output)
        commands = result.stdout.split("Commands:", 1)[1]
        listed = re.findall(r"^  (\S+)", commands, flags=re.MULTILINE)
        self.assertIn("install", listed)
        self.assertIn("remove", listed)
        self.assertNotIn("setup", listed)

    def test_setup_has_its_own_help_with_the_install_options(self) -> None:
        result = self.run_jzr("setup", "--help")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("SHELL", result.stdout)


if __name__ == "__main__":
    unittest.main()

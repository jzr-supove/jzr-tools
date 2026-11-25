"""Tests for the completion installer's core: shell detection, file locations,
the marked start-up block, install and remove plans, and the warnings.

Nothing here starts a real shell. Process ancestors are passed in as fakes,
``subprocess.run`` is replaced where a shell would be asked a question, and
files live in temporary folders. The one test that runs a real PowerShell
skips itself when ``pwsh`` is not on PATH.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from jzr_tools.core import completion, ffmpeg_install, paths
from jzr_tools.core.completion import (
    MARK_END,
    MARK_START,
    Ancestor,
    Change,
    CompletionError,
    add_block,
    apply,
    bash_version,
    block_lines,
    detect_shell,
    header,
    install_warnings,
    plan_install,
    plan_remove,
    powershell_info,
    remove_block,
    script_file,
    startup_file,
)

SOURCE = "_jzr_complete() { :; }\ncomplete -F _jzr_complete jzr\n"
VERSION = "1.2.3"
SHELL_LIST = "supported: bash, zsh, fish, powershell"


def block(*lines: str, nl: str = "\n") -> str:
    return nl.join([MARK_START, *lines, MARK_END]) + nl


def run_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr="")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.data = self.root / "data"
        self.home.mkdir()

    def environ(self, **extra: str) -> dict[str, str]:
        env = {"HOME": str(self.home), "JZR_DATA_DIR": str(self.data)}
        env.update(extra)
        return env


class DataDirTests(unittest.TestCase):
    def test_paths_data_dir_is_what_ffmpeg_install_exposes(self) -> None:
        for environ, platform in [
            ({"JZR_DATA_DIR": str(Path("/x/y")), "HOME": "/h"}, "linux"),
            ({"HOME": "/h", "LOCALAPPDATA": "/L"}, "win32"),
            ({"HOME": "/h"}, "darwin"),
            ({"HOME": "/h"}, "linux"),
        ]:
            with self.subTest(platform=platform, environ=environ):
                self.assertEqual(
                    paths.data_dir(environ, platform),
                    ffmpeg_install.data_dir(environ, platform),
                )
        self.assertIs(ffmpeg_install.data_dir, paths.data_dir)

    def test_data_dir_honours_override_and_platform_defaults(self) -> None:
        self.assertEqual(paths.data_dir({"JZR_DATA_DIR": "/over"}, "linux"), Path("/over"))
        self.assertEqual(
            paths.data_dir({"HOME": "/h"}, "darwin"),
            Path("/h") / "Library" / "Application Support" / "jzr",
        )
        self.assertEqual(
            paths.data_dir({"HOME": "/h"}, "linux"), Path("/h") / ".local" / "share" / "jzr"
        )


class DetectShellTests(unittest.TestCase):
    def detect(self, names, environ=None, platform="linux"):
        ancestors = [n if isinstance(n, Ancestor) else Ancestor(n) for n in names]
        return detect_shell(ancestors, environ=environ or {}, platform=platform)

    def test_launchers_are_skipped_to_reach_the_shell(self) -> None:
        launchers = [
            "python.exe",
            "Python.EXE",
            "python3",
            "python3.13",
            "pythonw.exe",
            "py.exe",
            "uv.exe",
            "uvx",
            "pipx",
            "jzr.exe",
        ]
        for launcher in launchers:
            with self.subTest(launcher=launcher):
                shell = self.detect([launcher, Ancestor("python.exe", r"C:\p\.venv\Scripts\python.exe"), "zsh"])
                self.assertEqual(shell.name, "zsh")
                self.assertEqual(shell.source, "parent process")

    def test_realistic_chain_from_git_bash(self) -> None:
        bash_exe = r"C:\Program Files\Git\usr\bin\bash.exe"
        shell = self.detect(
            [
                Ancestor("python.exe", r"C:\base\python.exe"),
                Ancestor("python.exe", r"C:\proj\.venv\Scripts\python.exe"),
                Ancestor("uv.exe", r"C:\bin\uv.exe"),
                Ancestor("bash.exe", bash_exe),
                Ancestor("explorer.exe"),
            ],
            platform="win32",
        )
        self.assertEqual((shell.name, shell.exe, shell.source), ("bash", bash_exe, "parent process"))

    def test_each_supported_shell_is_recognised(self) -> None:
        for name, expected in [
            ("bash", "bash"),
            ("zsh", "zsh"),
            ("fish", "fish"),
            ("pwsh.exe", "powershell"),
            ("powershell.exe", "powershell"),
            ("PWSH.EXE", "powershell"),
            ("Bash.exe", "bash"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.detect(["python", name]).name, expected)

    def test_powershell_keeps_the_executable_that_was_found(self) -> None:
        for name, exe in [
            ("pwsh.exe", r"C:\Program Files\PowerShell\7\pwsh.exe"),
            ("powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        ]:
            with self.subTest(name=name):
                shell = self.detect(["uv.exe", Ancestor(name, exe)], platform="win32")
                self.assertEqual((shell.name, shell.exe), ("powershell", exe))

    def test_first_non_launcher_decides(self) -> None:
        shell = self.detect(["python", "fish", "bash"])
        self.assertEqual(shell.name, "fish")

    def test_cmd_is_refused_with_advice(self) -> None:
        for name in ("cmd.exe", "CMD.EXE"):
            with self.subTest(name=name):
                with self.assertRaises(CompletionError) as caught:
                    self.detect(["python.exe", name], platform="win32")
                self.assertEqual(
                    str(caught.exception),
                    "cmd.exe has no programmable completion; use PowerShell, "
                    "or name a shell: jzr completion install SHELL",
                )

    def test_unsupported_shell_is_refused_and_named(self) -> None:
        for name in ("nu", "nu.exe", "dash", "sh", "tcsh"):
            with self.subTest(name=name):
                with self.assertRaises(CompletionError) as caught:
                    self.detect(["python", name])
                message = str(caught.exception)
                self.assertIn(name.removesuffix(".exe"), message)
                self.assertIn("is not supported", message)
                self.assertTrue(message.endswith(SHELL_LIST), message)

    def test_unsupported_shell_wins_over_a_later_supported_one(self) -> None:
        with self.assertRaises(CompletionError):
            self.detect(["python", "dash", "bash"])

    def test_nothing_found_falls_back_to_shell_variable(self) -> None:
        for platform in ("linux", "darwin"):
            for value, expected in [("/bin/zsh", "zsh"), ("/usr/bin/fish", "fish"), ("/bin/bash", "bash")]:
                with self.subTest(platform=platform, value=value):
                    shell = self.detect(["python", "uv"], {"SHELL": value}, platform)
                    self.assertEqual((shell.name, shell.source), (expected, "from $SHELL"))

    def test_empty_ancestors_fall_back_to_shell_variable(self) -> None:
        shell = self.detect([], {"SHELL": "/bin/zsh"}, "darwin")
        self.assertEqual((shell.name, shell.source), ("zsh", "from $SHELL"))

    def test_nothing_found_on_windows_fails_even_with_shell_variable(self) -> None:
        with self.assertRaises(CompletionError) as caught:
            self.detect(["python.exe"], {"SHELL": "/usr/bin/bash"}, "win32")
        self.assertEqual(
            str(caught.exception),
            "could not tell which shell this is; name it: jzr completion install SHELL",
        )

    def test_nothing_found_without_shell_variable_fails(self) -> None:
        for environ in ({}, {"SHELL": ""}):
            with self.subTest(environ=environ):
                with self.assertRaises(CompletionError) as caught:
                    self.detect(["python"], environ, "linux")
                self.assertIn("could not tell which shell this is", str(caught.exception))

    def test_unsupported_shell_variable_fails(self) -> None:
        with self.assertRaises(CompletionError) as caught:
            self.detect([], {"SHELL": "/usr/bin/nu"}, "linux")
        self.assertIn("nu", str(caught.exception))
        self.assertIn("is not supported", str(caught.exception))

    def test_a_real_ancestor_beats_the_shell_variable(self) -> None:
        shell = self.detect(["python", "fish"], {"SHELL": "/bin/zsh"}, "linux")
        self.assertEqual((shell.name, shell.source), ("fish", "parent process"))


class ScriptAndStartupFileTests(TempDirCase):
    def test_script_file_for_bash_zsh_powershell_lives_in_the_data_dir(self) -> None:
        env = self.environ()
        for shell, name in [("bash", "jzr.bash"), ("zsh", "jzr.zsh"), ("powershell", "jzr.ps1")]:
            with self.subTest(shell=shell):
                self.assertEqual(
                    script_file(shell, environ=env, platform="linux"),
                    self.data / "completion" / name,
                )

    def test_script_file_follows_the_data_dir_rules_of_each_platform(self) -> None:
        local = self.root / "local"
        xdg = self.root / "xdg"
        self.assertEqual(
            script_file("powershell", environ={"HOME": str(self.home), "LOCALAPPDATA": str(local)}, platform="win32"),
            local / "jzr" / "completion" / "jzr.ps1",
        )
        self.assertEqual(
            script_file("zsh", environ={"HOME": str(self.home)}, platform="darwin"),
            self.home / "Library" / "Application Support" / "jzr" / "completion" / "jzr.zsh",
        )
        self.assertEqual(
            script_file("bash", environ={"HOME": str(self.home), "XDG_DATA_HOME": str(xdg)}, platform="linux"),
            xdg / "jzr" / "completion" / "jzr.bash",
        )
        self.assertEqual(
            script_file("bash", environ={"HOME": str(self.home)}, platform="linux"),
            self.home / ".local" / "share" / "jzr" / "completion" / "jzr.bash",
        )

    def test_fish_script_goes_to_the_fish_completions_folder(self) -> None:
        self.assertEqual(
            script_file("fish", environ=self.environ(), platform="linux"),
            self.home / ".config" / "fish" / "completions" / "jzr.fish",
        )
        xdg = self.root / "cfg"
        self.assertEqual(
            script_file("fish", environ=self.environ(XDG_CONFIG_HOME=str(xdg)), platform="linux"),
            xdg / "fish" / "completions" / "jzr.fish",
        )

    def test_relative_xdg_config_home_is_ignored_for_fish(self) -> None:
        self.assertEqual(
            script_file("fish", environ=self.environ(XDG_CONFIG_HOME="relative/cfg"), platform="linux"),
            self.home / ".config" / "fish" / "completions" / "jzr.fish",
        )

    def test_startup_files(self) -> None:
        env = self.environ()
        self.assertEqual(startup_file("bash", environ=env), self.home / ".bashrc")
        self.assertEqual(startup_file("zsh", environ=env), self.home / ".zshrc")
        zdot = self.root / "zdot"
        self.assertEqual(
            startup_file("zsh", environ=self.environ(ZDOTDIR=str(zdot))), zdot / ".zshrc"
        )
        profile = self.root / "Documents" / "PowerShell" / "profile.ps1"
        self.assertEqual(startup_file("powershell", environ=env, profile=profile), profile)

    def test_fish_has_no_startup_file(self) -> None:
        self.assertIsNone(startup_file("fish", environ=self.environ()))

    def test_header_text(self) -> None:
        self.assertEqual(
            header("zsh", "2.0.1"),
            "# jzr completion for zsh, written by jzr 2.0.1; "
            "re-run 'jzr completion install' after upgrading",
        )


class BlockLinesTests(unittest.TestCase):
    def test_bash_line(self) -> None:
        script = Path("/home/me/.local/share/jzr/completion/jzr.bash")
        p = str(script)
        self.assertEqual(
            block_lines("bash", script, platform="linux"), [f'[ -f "{p}" ] && . "{p}"']
        )

    def test_bash_and_zsh_use_forward_slashes_on_windows(self) -> None:
        script = Path("C:/Users/me/AppData/Local/jzr/completion/jzr.bash")
        posix = "C:/Users/me/AppData/Local/jzr/completion/jzr.bash"
        self.assertEqual(
            block_lines("bash", script, platform="win32"), [f'[ -f "{posix}" ] && . "{posix}"']
        )
        zsh_script = Path("C:/Users/me/AppData/Local/jzr/completion/jzr.zsh")
        zposix = "C:/Users/me/AppData/Local/jzr/completion/jzr.zsh"
        self.assertEqual(
            block_lines("zsh", zsh_script, platform="win32"),
            [
                "(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }",
                f'[ -f "{zposix}" ] && . "{zposix}"',
            ],
        )

    def test_zsh_initialises_completion_before_sourcing(self) -> None:
        script = Path("/home/me/jzr.zsh")
        p = str(script)
        self.assertEqual(
            block_lines("zsh", script, platform="darwin"),
            [
                "(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }",
                f'[ -f "{p}" ] && . "{p}"',
            ],
        )

    def test_powershell_line(self) -> None:
        script = Path("C:/Users/me/jzr.ps1")
        p = str(script)
        self.assertEqual(
            block_lines("powershell", script, platform="win32"),
            [f"if (Test-Path '{p}') {{ . '{p}' }}"],
        )

    def test_powershell_doubles_apostrophes_in_the_path(self) -> None:
        script = Path("C:/Users/O'Brien/jzr.ps1")
        quoted = str(script).replace("'", "''")
        self.assertIn("O''Brien", quoted)
        self.assertEqual(
            block_lines("powershell", script, platform="win32"),
            [f"if (Test-Path '{quoted}') {{ . '{quoted}' }}"],
        )


class AddRemoveBlockTests(unittest.TestCase):
    LINES = ["line one", "line two"]

    def test_added_to_empty_text(self) -> None:
        self.assertEqual(add_block("", self.LINES), block(*self.LINES))

    def test_appended_after_existing_text(self) -> None:
        self.assertEqual(add_block("export A=1\n", self.LINES), "export A=1\n" + block(*self.LINES))

    def test_newline_added_when_text_has_no_trailing_one(self) -> None:
        self.assertEqual(add_block("export A=1", self.LINES), "export A=1\n" + block(*self.LINES))

    def test_existing_block_is_replaced_in_place(self) -> None:
        text = "before\n" + block("old") + "after 1\nafter 2\n"
        self.assertEqual(
            add_block(text, self.LINES), "before\n" + block(*self.LINES) + "after 1\nafter 2\n"
        )

    def test_adding_twice_changes_nothing_the_second_time(self) -> None:
        for text in ("", "x\n", "x", "a\r\nb\r\n", "\ufeffa\n"):
            with self.subTest(text=text):
                once = add_block(text, self.LINES)
                self.assertEqual(add_block(once, self.LINES), once)

    def test_same_block_leaves_text_identical(self) -> None:
        text = "a\n" + block(*self.LINES) + "b\n"
        self.assertEqual(add_block(text, self.LINES), text)

    def test_crlf_text_stays_crlf(self) -> None:
        result = add_block("export A=1\r\nexport B=2\r\n", self.LINES)
        self.assertEqual(result, "export A=1\r\nexport B=2\r\n" + block(*self.LINES, nl="\r\n"))
        self.assertEqual(result.count("\n"), result.count("\r\n"))

    def test_crlf_text_without_final_newline_stays_crlf(self) -> None:
        result = add_block("a\r\nb", self.LINES)
        self.assertTrue(result.startswith("a\r\nb\r\n" + MARK_START + "\r\n"))
        self.assertEqual(result.count("\n"), result.count("\r\n"))

    def test_replacing_in_crlf_text_keeps_crlf(self) -> None:
        text = "a\r\n" + block("old", nl="\r\n") + "b\r\n"
        self.assertEqual(
            add_block(text, self.LINES), "a\r\n" + block(*self.LINES, nl="\r\n") + "b\r\n"
        )

    def test_newline_argument_is_used_only_when_text_has_no_line_ending(self) -> None:
        self.assertEqual(add_block("", self.LINES, newline="\r\n"), block(*self.LINES, nl="\r\n"))
        self.assertEqual(add_block("a\n", self.LINES, newline="\r\n"), "a\n" + block(*self.LINES))

    def test_bom_is_kept(self) -> None:
        result = add_block("\ufeffexport A=1\n", self.LINES)
        self.assertEqual(result, "\ufeffexport A=1\n" + block(*self.LINES))
        self.assertEqual(result.count("\ufeff"), 1)

    def test_bom_is_kept_when_replacing(self) -> None:
        text = "\ufeffa\n" + block("old")
        self.assertEqual(add_block(text, self.LINES), "\ufeffa\n" + block(*self.LINES))

    def test_remove_drops_only_the_block(self) -> None:
        text = "a\n" + block("x", "y") + "b\n"
        self.assertEqual(remove_block(text), "a\nb\n")

    def test_remove_leaves_other_lines_byte_identical(self) -> None:
        before = "a \r\n\tb  \r\n\r\n"
        after = "c\t\r\nlast no newline"
        text = before + block("x", nl="\r\n") + after
        self.assertEqual(remove_block(text), before + after)

    def test_remove_without_a_block_returns_text_unchanged(self) -> None:
        for text in ("", "a\nb\n", "a\r\nb", "\ufeffa\n", "# >>> other >>>\nx\n# <<< other <<<\n"):
            with self.subTest(text=text):
                self.assertEqual(remove_block(text), text)

    def test_remove_keeps_bom(self) -> None:
        self.assertEqual(remove_block("\ufeffa\n" + block("x")), "\ufeffa\n")

    def test_add_then_remove_restores_text(self) -> None:
        for text in ("", "a\n", "a\r\nb\r\n", "\ufeffa\n"):
            with self.subTest(text=text):
                self.assertEqual(remove_block(add_block(text, self.LINES)), text)


class PlanInstallTests(TempDirCase):
    def plan(self, shell="bash", *, version=VERSION, source=SOURCE, platform="linux", **kwargs):
        return plan_install(
            shell, source, version, environ=kwargs.pop("environ", None) or self.environ(),
            platform=platform, **kwargs,
        )

    def by_kind(self, changes, kind):
        found = [c for c in changes if c.kind == kind]
        self.assertEqual(len(found), 1, changes)
        return found[0]

    def install(self, shell="bash", **kwargs):
        changes = self.plan(shell, **kwargs)
        apply(changes)
        return changes

    def test_first_install_writes_script_and_creates_startup_file(self) -> None:
        changes = self.plan("bash")
        script = self.by_kind(changes, "script")
        startup = self.by_kind(changes, "startup")
        self.assertEqual(script.path, self.data / "completion" / "jzr.bash")
        self.assertEqual(script.action, "write")
        self.assertEqual(startup.path, self.home / ".bashrc")
        self.assertEqual(startup.action, "write")
        self.assertFalse(startup.replaced)
        self.assertFalse(script.path.exists())
        self.assertFalse(startup.path.exists())

    def test_script_bytes_are_header_newline_source_with_lf_only(self) -> None:
        source = "line one\nline two\n"
        script = self.by_kind(self.plan("bash", source=source), "script")
        expected = (
            "# jzr completion for bash, written by jzr 1.2.3; "
            "re-run 'jzr completion install' after upgrading\n" + source
        ).encode("utf-8")
        self.assertEqual(script.content, expected)
        self.assertNotIn(b"\r", script.content)
        self.assertFalse(script.content.startswith(b"\xef\xbb\xbf"))

    def test_startup_content_holds_the_block_for_the_script_path(self) -> None:
        changes = self.plan("bash")
        script = self.by_kind(changes, "script")
        startup = self.by_kind(changes, "startup")
        expected = add_block("", block_lines("bash", script.path, platform="linux"))
        self.assertEqual(startup.content.decode("utf-8"), expected)

    def test_zsh_honours_zdotdir(self) -> None:
        zdot = self.root / "zdot"
        changes = self.plan("zsh", environ=self.environ(ZDOTDIR=str(zdot)))
        self.assertEqual(self.by_kind(changes, "startup").path, zdot / ".zshrc")
        self.assertEqual(self.by_kind(changes, "script").path, self.data / "completion" / "jzr.zsh")

    def test_existing_startup_file_without_block_is_updated_not_replaced(self) -> None:
        (self.home / ".bashrc").write_text("export A=1\n", encoding="utf-8", newline="")
        startup = self.by_kind(self.plan("bash"), "startup")
        self.assertEqual(startup.action, "update")
        self.assertFalse(startup.replaced)
        self.assertTrue(startup.content.decode().startswith("export A=1\n" + MARK_START + "\n"))

    def test_second_run_is_all_unchanged(self) -> None:
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                self.install(shell)
                again = self.plan(shell)
                self.assertEqual({c.action for c in again}, {"unchanged"}, again)
                self.assertEqual({c.kind for c in again}, {"script", "startup"})

    def test_new_version_updates_the_script_only(self) -> None:
        self.install("bash")
        again = self.plan("bash", version="1.2.4")
        self.assertEqual(self.by_kind(again, "script").action, "update")
        self.assertIn(b"jzr 1.2.4", self.by_kind(again, "script").content)
        self.assertEqual(self.by_kind(again, "startup").action, "unchanged")

    def test_changed_script_source_updates_the_script(self) -> None:
        self.install("bash")
        again = self.plan("bash", source=SOURCE + "# more\n")
        self.assertEqual(self.by_kind(again, "script").action, "update")

    def test_different_existing_block_is_replaced_in_place(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_text("top\n" + block("old line") + "bottom\n", encoding="utf-8", newline="")
        script = self.data / "completion" / "jzr.bash"
        startup = self.by_kind(self.plan("bash"), "startup")
        self.assertEqual(startup.action, "update")
        self.assertTrue(startup.replaced)
        expected = "top\n" + block(*block_lines("bash", script, platform="linux")) + "bottom\n"
        self.assertEqual(startup.content.decode(), expected)

    def test_planning_writes_nothing(self) -> None:
        (self.home / ".bashrc").write_text("export A=1\n", encoding="utf-8")
        self.plan("bash")
        self.assertEqual((self.home / ".bashrc").read_text(), "export A=1\n")
        self.assertFalse(self.data.exists())

    def test_fish_plans_only_the_completions_file(self) -> None:
        env = self.environ(XDG_CONFIG_HOME=str(self.root / "cfg"))
        changes = self.plan("fish", environ=env)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].kind, "script")
        self.assertEqual(changes[0].action, "write")
        self.assertEqual(changes[0].path, self.root / "cfg" / "fish" / "completions" / "jzr.fish")
        self.assertTrue(changes[0].content.decode().startswith("# jzr completion for fish, written by jzr 1.2.3"))
        apply(changes)
        self.assertEqual(self.plan("fish", environ=env)[0].action, "unchanged")
        self.assertFalse((self.home / ".config" / "fish" / "config.fish").exists())

    def test_new_powershell_profile_on_windows_uses_crlf(self) -> None:
        profile = self.root / "Documents" / "PowerShell" / "profile.ps1"
        changes = self.plan("powershell", platform="win32", profile=profile)
        startup = self.by_kind(changes, "startup")
        self.assertEqual(startup.path, profile)
        self.assertEqual(startup.action, "write")
        self.assertIn(b"\r\n", startup.content)
        self.assertEqual(startup.content.count(b"\n"), startup.content.count(b"\r\n"))
        self.assertEqual(self.by_kind(changes, "script").path.name, "jzr.ps1")
        self.assertNotIn(b"\r", self.by_kind(changes, "script").content)

    def test_new_bash_startup_file_on_windows_uses_lf(self) -> None:
        startup = self.by_kind(self.plan("bash", platform="win32"), "startup")
        self.assertNotIn(b"\r", startup.content)

    def test_existing_line_endings_and_bom_are_kept(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_bytes(b"\xef\xbb\xbfexport A=1\r\n")
        startup = self.by_kind(self.plan("bash"), "startup")
        self.assertTrue(startup.content.startswith(b"\xef\xbb\xbfexport A=1\r\n" + MARK_START.encode() + b"\r\n"))
        self.assertEqual(startup.content.count(b"\n"), startup.content.count(b"\r\n"))
        self.assertEqual(startup.content.count(b"\xef\xbb\xbf"), 1)

    def test_startup_file_that_is_not_utf8_fails_and_names_the_file(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_bytes(b"caf\xe9\n")
        with self.assertRaises(CompletionError) as caught:
            self.plan("bash")
        self.assertIn(str(rc), str(caught.exception))
        self.assertEqual(rc.read_bytes(), b"caf\xe9\n")
        self.assertFalse(self.data.exists())


class ApplyTests(TempDirCase):
    def plan(self, shell="bash", **kwargs):
        return plan_install(shell, SOURCE, VERSION, environ=self.environ(), platform="linux", **kwargs)

    def test_apply_writes_script_and_startup_file_with_parent_folders(self) -> None:
        changes = self.plan("zsh")
        apply(changes)
        script = self.data / "completion" / "jzr.zsh"
        self.assertEqual(script.read_bytes(), next(c.content for c in changes if c.kind == "script"))
        text = (self.home / ".zshrc").read_text(encoding="utf-8")
        self.assertIn(MARK_START, text)
        self.assertIn(str(script), text)
        self.assertEqual(
            sorted(p.name for p in script.parent.iterdir()), ["jzr.zsh"], "no temp file may be left behind"
        )
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".zshrc"])

    def test_unchanged_changes_touch_no_file(self) -> None:
        apply(self.plan("bash"))
        rc = self.home / ".bashrc"
        before = rc.stat().st_mtime_ns
        os.utime(rc, ns=(before - 10**10, before - 10**10))
        stamp = rc.stat().st_mtime_ns
        apply(self.plan("bash"))
        self.assertEqual(rc.stat().st_mtime_ns, stamp)

    def test_symlinked_startup_file_is_edited_at_its_target(self) -> None:
        dotfiles = self.root / "dotfiles"
        dotfiles.mkdir()
        target = dotfiles / "bashrc"
        target.write_text("export A=1\n", encoding="utf-8")
        link = self.home / ".bashrc"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links cannot be created here")
        apply(self.plan("bash"))
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.path.realpath(link)), target.resolve())
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("export A=1\n"))
        self.assertIn(MARK_START, text)

    def test_unwritable_startup_file_fails_and_removes_the_new_script(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_text("export A=1\n", encoding="utf-8")
        if sys.platform == "win32":
            guarded, mode_off, mode_on = rc, 0o444, 0o666
        else:
            guarded, mode_off, mode_on = self.home, 0o555, 0o755
        os.chmod(guarded, mode_off)
        self.addCleanup(os.chmod, guarded, mode_on)
        if os.access(guarded, os.W_OK):
            self.skipTest("permissions do not make the start-up file read-only for this user")
        changes = self.plan("bash")
        with self.assertRaises(CompletionError) as caught:
            apply(changes)
        self.assertTrue(str(caught.exception).startswith("cannot write "), str(caught.exception))
        self.assertIn(str(rc), str(caught.exception))
        self.assertFalse((self.data / "completion" / "jzr.bash").exists())
        self.assertEqual(rc.read_text(encoding="utf-8"), "export A=1\n")

    def test_failed_apply_keeps_a_script_file_that_already_existed(self) -> None:
        apply(self.plan("bash"))
        rc = self.home / ".bashrc"
        if sys.platform == "win32":
            guarded, mode_off, mode_on = rc, 0o444, 0o666
        else:
            guarded, mode_off, mode_on = self.home, 0o555, 0o755
        os.chmod(guarded, mode_off)
        self.addCleanup(os.chmod, guarded, mode_on)
        if os.access(guarded, os.W_OK):
            self.skipTest("permissions do not make the start-up file read-only for this user")
        os.chmod(guarded, mode_on)
        rc.write_text("# changed\n", encoding="utf-8")
        os.chmod(guarded, mode_off)
        changes = plan_install("bash", SOURCE, "9.9.9", environ=self.environ(), platform="linux")
        with self.assertRaises(CompletionError):
            apply(changes)
        self.assertTrue((self.data / "completion" / "jzr.bash").exists())

    def test_delete_removes_the_file(self) -> None:
        target = self.root / "gone.txt"
        target.write_text("x", encoding="utf-8")
        apply([Change(path=target, action="delete", kind="script")])
        self.assertFalse(target.exists())

    def test_absent_and_unchanged_do_nothing(self) -> None:
        missing = self.root / "never.txt"
        apply([Change(path=missing, action="absent", kind="script")])
        self.assertFalse(missing.exists())


class PlanRemoveTests(TempDirCase):
    def installed(self, shell="bash", **kwargs):
        apply(plan_install(shell, SOURCE, VERSION, environ=self.environ(**kwargs.pop("env", {})), platform="linux", **kwargs))

    def remove_plan(self, shell="bash", env=None, **kwargs):
        return plan_remove(shell, environ=self.environ(**(env or {})), platform="linux", **kwargs)

    def test_remove_deletes_the_block_and_the_script(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_text("export A=1\n", encoding="utf-8")
        self.installed("bash")
        changes = self.remove_plan("bash")
        by_kind = {c.kind: c for c in changes}
        self.assertEqual(by_kind["script"].action, "delete")
        self.assertEqual(by_kind["script"].path, self.data / "completion" / "jzr.bash")
        self.assertEqual(by_kind["startup"].action, "update")
        self.assertEqual(by_kind["startup"].path, rc)
        apply(changes)
        self.assertEqual(rc.read_text(encoding="utf-8"), "export A=1\n")
        self.assertFalse((self.data / "completion" / "jzr.bash").exists())

    def test_remove_keeps_every_other_line_of_the_startup_file(self) -> None:
        rc = self.home / ".zshrc"
        rc.write_bytes(b"alias a=b \r\n\tfoo  \r\n")
        self.installed("zsh")
        apply(self.remove_plan("zsh"))
        self.assertEqual(rc.read_bytes(), b"alias a=b \r\n\tfoo  \r\n")

    def test_remove_then_nothing_is_left_to_remove(self) -> None:
        self.installed("bash")
        apply(self.remove_plan("bash"))
        again = self.remove_plan("bash")
        self.assertEqual({c.kind: c.action for c in again}, {"script": "absent", "startup": "unchanged"})

    def test_nothing_installed_reports_absent(self) -> None:
        changes = self.remove_plan("bash")
        self.assertEqual({c.kind: c.action for c in changes}, {"script": "absent", "startup": "absent"})
        self.assertFalse((self.home / ".bashrc").exists())

    def test_startup_file_without_a_block_is_unchanged(self) -> None:
        (self.home / ".bashrc").write_text("export A=1\n", encoding="utf-8")
        changes = self.remove_plan("bash")
        self.assertEqual({c.kind: c.action for c in changes}["startup"], "unchanged")

    def test_remove_powershell_uses_the_given_profile(self) -> None:
        profile = self.root / "Documents" / "PowerShell" / "profile.ps1"
        self.installed("powershell", profile=profile)
        changes = self.remove_plan("powershell", profile=profile)
        self.assertEqual({c.kind: c.action for c in changes}, {"script": "delete", "startup": "update"})
        apply(changes)
        self.assertNotIn(MARK_START, profile.read_text(encoding="utf-8"))
        self.assertFalse((self.data / "completion" / "jzr.ps1").exists())

    def test_fish_remove_deletes_the_completions_file(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.root / "cfg")}
        self.installed("fish", env=env)
        changes = self.remove_plan("fish", env=env)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "delete")
        apply(changes)
        self.assertFalse(changes[0].path.exists())

    def test_fish_remove_refuses_a_file_without_the_jzr_header(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.root / "cfg")}
        target = self.root / "cfg" / "fish" / "completions" / "jzr.fish"
        target.parent.mkdir(parents=True)
        target.write_text("# somebody else's completions\ncomplete -c jzr\n", encoding="utf-8")
        with self.assertRaises(CompletionError) as caught:
            self.remove_plan("fish", env=env)
        self.assertIn("not jzr's", str(caught.exception))
        self.assertIn(str(target), str(caught.exception))
        self.assertTrue(target.exists())

    def test_fish_remove_with_no_file_is_absent(self) -> None:
        changes = self.remove_plan("fish", env={"XDG_CONFIG_HOME": str(self.root / "cfg")})
        self.assertEqual([c.action for c in changes], ["absent"])


class InstallWarningsTests(TempDirCase):
    def warnings(self, shell, **kwargs):
        return install_warnings(shell, environ=self.environ(), **kwargs)

    def test_restricted_policy_warns_with_the_command_to_run(self) -> None:
        result = self.warnings("powershell", policy="Restricted")
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0],
            "PowerShell will not run the profile while the execution policy is Restricted. "
            "To allow it for your user: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned",
        )

    def test_allsigned_policy_warns(self) -> None:
        result = self.warnings("powershell", policy="AllSigned")
        self.assertEqual(len(result), 1)
        self.assertIn("AllSigned", result[0])
        self.assertIn("Set-ExecutionPolicy -Scope CurrentUser RemoteSigned", result[0])

    def test_permissive_policies_do_not_warn(self) -> None:
        for policy in ("RemoteSigned", "Unrestricted", "Bypass", None):
            with self.subTest(policy=policy):
                self.assertEqual(self.warnings("powershell", policy=policy), [])

    def test_old_bash_warns(self) -> None:
        result = self.warnings("bash", bash=(3, 2))
        self.assertEqual(result, ["bash 3.2 is too old for jzr completion (4.4 or newer is needed)"])
        self.assertEqual(len(self.warnings("bash", bash=(4, 3))), 1)

    def test_new_or_unknown_bash_does_not_warn(self) -> None:
        for version in ((5, 2), (4, 4), (4, 5), None):
            with self.subTest(version=version):
                self.assertEqual(self.warnings("bash", bash=version), [])

    def test_bash_version_is_not_judged_for_other_shells(self) -> None:
        self.assertEqual(self.warnings("zsh", bash=(3, 2)), [])

    def test_bash_profile_that_skips_bashrc_warns(self) -> None:
        (self.home / ".bash_profile").write_text("export PATH=$PATH:~/bin\n", encoding="utf-8")
        result = self.warnings("bash", bash=(5, 2))
        self.assertEqual(
            result,
            [
                "~/.bash_profile does not load ~/.bashrc, so login shells will not get "
                "completion; add: [ -f ~/.bashrc ] && . ~/.bashrc"
            ],
        )

    def test_bash_profile_that_loads_bashrc_does_not_warn(self) -> None:
        (self.home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\n", encoding="utf-8")
        self.assertEqual(self.warnings("bash", bash=(5, 2)), [])

    def test_missing_bash_profile_does_not_warn(self) -> None:
        self.assertEqual(self.warnings("bash", bash=(5, 2)), [])

    def test_both_bash_warnings_can_appear_together(self) -> None:
        (self.home / ".bash_profile").write_text("echo hi\n", encoding="utf-8")
        self.assertEqual(len(self.warnings("bash", bash=(3, 2))), 2)


GIT_BASH_NOTE = (
    "Note: the next Git Bash window will say it found ~/.bashrc but no ~/.bash_profile "
    "and create one that loads ~/.bashrc. That is expected."
)


class InstallNotesTests(TempDirCase):
    def notes(self, shell, platform="win32"):
        from jzr_tools.core.completion import install_notes

        return install_notes(shell, environ={"HOME": str(self.home)}, platform=platform)

    def test_bash_on_windows_without_a_login_file_gets_the_git_bash_note(self) -> None:
        self.assertEqual(self.notes("bash"), [GIT_BASH_NOTE])

    def test_any_one_login_file_removes_the_note(self) -> None:
        for name in (".bash_profile", ".bash_login", ".profile"):
            with self.subTest(file=name):
                path = self.home / name
                path.write_text("", encoding="utf-8")
                self.addCleanup(path.unlink, missing_ok=True)
                self.assertEqual(self.notes("bash"), [])
                path.unlink()
                self.assertEqual(self.notes("bash"), [GIT_BASH_NOTE])

    def test_other_platforms_get_no_note(self) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                self.assertEqual(self.notes("bash", platform), [])

    def test_other_shells_get_no_note(self) -> None:
        for shell in ("zsh", "fish", "powershell"):
            with self.subTest(shell=shell):
                self.assertEqual(self.notes(shell), [])


class PowerShellInfoTests(unittest.TestCase):
    EXE =r"C:\Program Files\PowerShell\7\pwsh.exe"

    def test_two_output_lines_are_profile_and_policy(self) -> None:
        out = "C:\\Users\\me\\Documents\\PowerShell\\profile.ps1\nRemoteSigned\n"
        with mock.patch("subprocess.run", return_value=run_result(out)) as run:
            info = powershell_info(self.EXE)
        self.assertEqual(info.profile, Path("C:\\Users\\me\\Documents\\PowerShell\\profile.ps1"))
        self.assertEqual(info.policy, "RemoteSigned")
        command = run.call_args.args[0]
        self.assertEqual(command[0], self.EXE)
        for flag in ("-NoProfile", "-NoLogo", "-NonInteractive", "-Command"):
            self.assertIn(flag, command)
        self.assertIn("CurrentUserAllHosts", command[-1])
        self.assertIn("Get-ExecutionPolicy", command[-1])
        self.assertEqual(run.call_args.kwargs.get("timeout"), 10.0)

    def test_windows_line_endings_and_blank_lines_are_tolerated(self) -> None:
        out = "C:\\Users\\me\\profile.ps1\r\nRestricted\r\n\r\n"
        with mock.patch("subprocess.run", return_value=run_result(out)):
            info = powershell_info(self.EXE)
        self.assertEqual(info.profile, Path("C:\\Users\\me\\profile.ps1"))
        self.assertEqual(info.policy, "Restricted")

    def test_non_zero_exit_is_an_error(self) -> None:
        with mock.patch("subprocess.run", return_value=run_result("", returncode=1)):
            with self.assertRaises(CompletionError):
                powershell_info(self.EXE)

    def test_timeout_is_an_error(self) -> None:
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pwsh", 10)):
            with self.assertRaises(CompletionError):
                powershell_info(self.EXE)

    def test_output_with_too_few_lines_is_an_error(self) -> None:
        with mock.patch("subprocess.run", return_value=run_result("only-one-line\n")):
            with self.assertRaises(CompletionError):
                powershell_info(self.EXE)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not on PATH")
    def test_real_pwsh_reports_an_all_hosts_profile_and_a_policy(self) -> None:
        # A first PowerShell start on a busy CI runner has taken more than the
        # 10 s that jzr itself waits; this test checks what pwsh reports, not
        # how fast it starts.
        info = powershell_info(shutil.which("pwsh"), timeout=120)
        self.assertEqual(info.profile.name, "profile.ps1")
        self.assertTrue(info.policy)


class BashVersionTests(unittest.TestCase):
    def test_version_is_read_from_the_first_line(self) -> None:
        cases = [
            ("GNU bash, version 5.2.37(1)-release (x86_64-pc-msys)\nCopyright (C) 2022\n", (5, 2)),
            ("GNU bash, version 3.2.57(1)-release (arm64-apple-darwin23)\n", (3, 2)),
            ("GNU bash, version 4.4.20(1)-release (x86_64-pc-linux-gnu)\n", (4, 4)),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("subprocess.run", return_value=run_result(text)):
                    self.assertEqual(bash_version("/bin/bash"), expected)

    def test_given_executable_is_run_with_version_flag(self) -> None:
        with mock.patch("subprocess.run", return_value=run_result("GNU bash, version 5.2.0(1)\n")) as run:
            bash_version("/opt/bash/bin/bash")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/opt/bash/bin/bash")
        self.assertIn("--version", command)

    def test_without_an_executable_bash_on_path_is_asked(self) -> None:
        with mock.patch("subprocess.run", return_value=run_result("GNU bash, version 5.1.16(1)\n")) as run:
            self.assertEqual(bash_version(None), (5, 1))
        self.assertEqual(run.call_args.args[0][0], "bash")

    def test_unreadable_output_gives_none(self) -> None:
        for text in ("", "not a version\n", "zsh 5.9\n"):
            with self.subTest(text=text):
                with mock.patch("subprocess.run", return_value=run_result(text)):
                    self.assertIsNone(bash_version("bash"))

    def test_failures_give_none(self) -> None:
        for effect in (
            FileNotFoundError("bash"),
            subprocess.TimeoutExpired("bash", 5),
            OSError("denied"),
        ):
            with self.subTest(effect=type(effect).__name__):
                with mock.patch("subprocess.run", side_effect=effect):
                    self.assertIsNone(bash_version("bash"))

    def test_non_zero_exit_gives_none(self) -> None:
        with mock.patch("subprocess.run", return_value=run_result("GNU bash, version 5.2.0(1)\n", returncode=1)):
            self.assertIsNone(bash_version("bash"))


class ModuleSurfaceTests(unittest.TestCase):
    def test_shells_and_markers(self) -> None:
        self.assertEqual(completion.SHELLS, ("bash", "zsh", "fish", "powershell"))
        self.assertEqual(MARK_START, "# >>> jzr completion >>>")
        self.assertEqual(MARK_END, "# <<< jzr completion <<<")

    def test_named_shell_has_no_executable(self) -> None:
        shell = completion.named_shell("zsh")
        self.assertEqual((shell.name, shell.exe, shell.source), ("zsh", None, "named"))

    def test_home_dir_prefers_home_variable(self) -> None:
        self.assertEqual(completion.home_dir({"HOME": "/c/h"}), Path("/c/h"))


if __name__ == "__main__":
    unittest.main()

"""Acceptance tests for the `jzr completion` command and shell completion at runtime."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from click.testing import CliRunner

from jzr_tools.cli import _COMMANDS, app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

SHELLS = ("bash", "zsh", "fish", "powershell")

# Unknown command names consult the bindings file, so no test may see the real one.
_CONFIG_DIR = tempfile.TemporaryDirectory(prefix="jzr-completion-config-")

_MAIN = "from jzr_tools.cli import main; main()"
_MAIN_AS_EXE = "import sys; sys.argv[0] = 'jzr.exe'; from jzr_tools.cli import main; main()"
_MAIN_WITH_MODULE_REPORT = (
    "import atexit, sys\n"
    "atexit.register(lambda: sys.stderr.write('@@MODULES@@' + ','.join(sorted(sys.modules)) + '\\n'))\n"
    "from jzr_tools.cli import main\n"
    "main()\n"
)

runner = CliRunner()


class Completed:
    def __init__(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.lines = proc.stdout.splitlines()
        self.modules: set[str] = set()
        for line in proc.stderr.splitlines():
            if line.startswith("@@MODULES@@"):
                self.modules = set(filter(None, line[len("@@MODULES@@"):].split(",")))

    def describe(self) -> str:
        return f"rc={self.returncode}\nstdout={self.stdout!r}\nstderr={self.stderr[-500:]!r}"


def _complete(
    mode: str, words: str, cword: int, *, code: str = _MAIN
) -> Completed:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_PATH)
    env["JZR_CONFIG_DIR"] = _CONFIG_DIR.name
    env["PYTHONIOENCODING"] = "utf-8"
    env["_JZR_COMPLETE"] = mode
    env["COMP_WORDS"] = words
    env["COMP_CWORD"] = str(cword)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return Completed(proc)


class CompletionScriptTests(unittest.TestCase):
    def _script(self, shell: str) -> str:
        result = runner.invoke(app, ["completion", shell])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.strip(), f"{shell} script is empty")
        return result.stdout

    def test_every_shell_prints_a_non_empty_script_on_stdout(self) -> None:
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self._script(shell)

    def test_bash_script_calls_back_into_jzr_through_the_completion_variable(self) -> None:
        script = self._script("bash")

        self.assertIn("_JZR_COMPLETE", script)
        self.assertIn("jzr", script)

    def test_zsh_and_fish_scripts_use_the_completion_variable(self) -> None:
        for shell in ("zsh", "fish"):
            with self.subTest(shell=shell):
                script = self._script(shell)
                self.assertIn("_JZR_COMPLETE", script)
                self.assertIn("jzr", script)

    def test_powershell_script_registers_a_native_completer_for_jzr(self) -> None:
        script = self._script("powershell")

        self.assertIn("Register-ArgumentCompleter", script)
        self.assertIn("_JZR_COMPLETE", script)
        self.assertIn("powershell_complete", script)
        self.assertIn("jzr", script)

    def test_scripts_differ_per_shell(self) -> None:
        scripts = {shell: self._script(shell) for shell in SHELLS}

        self.assertEqual(len(set(scripts.values())), len(SHELLS))

    def test_unknown_shell_is_a_usage_error(self) -> None:
        result = runner.invoke(app, ["completion", "tcsh"])

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("tcsh", result.stderr)

    def test_missing_shell_is_a_usage_error(self) -> None:
        result = runner.invoke(app, ["completion"])

        self.assertEqual(result.exit_code, 2)

    def test_help_explains_installation_for_each_shell(self) -> None:
        result = runner.invoke(app, ["completion", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertIn(shell, result.stdout)
        self.assertIn("SHELL", result.stdout)

    def test_registered_with_the_short_help_shown_in_the_root_help(self) -> None:
        self.assertEqual(
            _COMMANDS["completion"],
            ("jzr_tools.commands.completion:app", "Print a shell completion script."),
        )


class BashCompletionRuntimeTests(unittest.TestCase):
    def test_partial_command_name_completes_to_text(self) -> None:
        done = _complete("bash_complete", "jzr te", 1)

        self.assertEqual(done.returncode, 0, done.describe())
        self.assertIn("plain,text", done.lines)

    def test_completion_works_when_the_launcher_is_jzr_exe(self) -> None:
        done = _complete("bash_complete", "jzr te", 1, code=_MAIN_AS_EXE)

        self.assertEqual(done.returncode, 0, done.describe())
        self.assertIn("plain,text", done.lines)

    def test_group_subcommands_complete_through_click(self) -> None:
        done = _complete("bash_complete", "jzr port ", 2)

        self.assertEqual(done.returncode, 0, done.describe())
        self.assertIn("plain,show", done.lines)
        self.assertIn("plain,kill", done.lines)

    def test_options_complete_through_click(self) -> None:
        done = _complete("bash_complete", "jzr text slug --pre", 3)

        self.assertEqual(done.returncode, 0, done.describe())
        self.assertIn("plain,--preserve-case", done.lines)

    def test_first_word_lists_every_registered_command(self) -> None:
        done = _complete("bash_complete", "jzr ", 1)

        self.assertEqual(done.returncode, 0, done.describe())
        offered = {line.split(",", 1)[1] for line in done.lines if line.startswith("plain,")}
        self.assertTrue(set(_COMMANDS) <= offered, f"missing: {set(_COMMANDS) - offered}")
        self.assertIn("completion", offered)


class PowershellCompletionRuntimeTests(unittest.TestCase):
    def _items(self, done: Completed) -> list[list[str]]:
        return [line.split("\t") for line in done.lines if line]

    def test_partial_command_name_prints_tab_separated_item_with_help(self) -> None:
        done = _complete("powershell_complete", "jzr te", 1)

        self.assertEqual(done.returncode, 0, done.describe())
        items = self._items(done)
        text_items = [item for item in items if item[:2] == ["plain", "text"]]
        self.assertEqual(len(text_items), 1, done.describe())
        self.assertEqual(len(text_items[0]), 3, done.describe())
        self.assertEqual(text_items[0][2], "Text utilities.")

    def test_every_line_has_a_known_type_and_three_fields(self) -> None:
        done = _complete("powershell_complete", "jzr ", 1)

        self.assertEqual(done.returncode, 0, done.describe())
        items = self._items(done)
        self.assertTrue(items, done.describe())
        for item in items:
            with self.subTest(item=item):
                self.assertEqual(len(item), 3)
                self.assertIn(item[0], {"plain", "file", "dir"})

    def test_works_when_the_launcher_is_jzr_exe(self) -> None:
        done = _complete("powershell_complete", "jzr te", 1, code=_MAIN_AS_EXE)

        self.assertEqual(done.returncode, 0, done.describe())
        self.assertTrue(any(line.startswith("plain\ttext\t") for line in done.lines), done.describe())

    def test_group_subcommands_are_listed(self) -> None:
        done = _complete("powershell_complete", "jzr port ", 2)

        self.assertEqual(done.returncode, 0, done.describe())
        values = {item[1] for item in self._items(done) if item[0] == "plain"}
        self.assertTrue({"show", "kill"} <= values, done.describe())

    def test_first_word_lists_every_registered_command(self) -> None:
        done = _complete("powershell_complete", "jzr ", 1)

        values = {item[1] for item in self._items(done) if item[0] == "plain"}
        self.assertTrue(set(_COMMANDS) <= values, f"missing: {set(_COMMANDS) - values}")


class FirstWordCompletionIsLightTests(unittest.TestCase):
    def test_completing_the_first_word_imports_no_other_command_module(self) -> None:
        for mode in ("bash_complete", "powershell_complete"):
            with self.subTest(mode=mode):
                done = _complete(mode, "jzr ", 1, code=_MAIN_WITH_MODULE_REPORT)

                self.assertEqual(done.returncode, 0, done.describe())
                self.assertTrue(done.modules, "module report is missing\n" + done.describe())
                loaded = sorted(
                    name
                    for name in done.modules
                    if name.startswith("jzr_tools.commands.")
                    and name != "jzr_tools.commands.completion"
                )
                self.assertEqual(loaded, [])
                self.assertNotIn("psutil", done.modules)


if __name__ == "__main__":
    unittest.main()

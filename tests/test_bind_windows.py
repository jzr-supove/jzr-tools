"""Acceptance tests for Windows support of ``jzr bind``
(docs/features/windows-host-bind/spec.md, section "bind").

Seams assumed in production code (none exist yet; tests create them with
``mock.patch.object(..., create=True)`` so each test fails on the missing
behaviour and not on an AttributeError):

``jzr_tools.core.bindings``
    ``_windows() -> bool``
        True on Windows; called at run time.  ``describe``, ``split_command``
        and ``run`` use it (not ``os.name``) to choose between the POSIX and
        the Windows rules.
    ``run(arguments)`` on Windows when ``shutil.which(arguments[0])`` resolves
        to a path ending in ``.cmd`` or ``.bat`` (any case): it starts exactly
        one process with ``subprocess.Popen(command_line)`` where
        ``command_line`` is a *str*::

            <cmd.exe or %COMSPEC%> /d /s /c "<q(program)> <q(arg1)> <q(arg2)>"

        with ``q(x) = '"' + x + '"'``.  If an argument (not the program) holds
        ``%``, ``"``, ``!``, CR or LF, nothing is started and ``BindingError``
        is raised whose text contains that argument (a line break may appear
        escaped, as in ``repr``).  Other programs keep ``Popen([program, *args])``.
    ``describe(arguments)`` on Windows is ``subprocess.list2cmdline(arguments)``.
    ``split_command([text])`` on Windows reads Windows command-line rules
        (double quotes group, backslashes are literal unless they precede a
        double quote, ``\\"`` is a quote), so it reads ``describe`` back.

``jzr_tools.cli``
    ``should_expand_windows_args(argv: Sequence[str]) -> bool``
        ``argv`` is ``sys.argv[1:]``.  False when ``argv[0]`` is ``bind`` or
        the name of an existing binding that no built-in command shadows;
        True otherwise (also for no arguments, options, unknown names).
    ``main()`` calls ``app(windows_expand_args=should_expand_windows_args(sys.argv[1:]))``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_bind_cli as bind_tests

WINDOWS = sys.platform == "win32"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
ARGV_PRINTER = bind_tests.ARGV_PRINTER


def bindings():
    from jzr_tools.core import bindings as module

    return module


def fake_windows(value: bool):
    return mock.patch.object(bindings(), "_windows", return_value=value, create=True)


# --------------------------------------------------------------------------
# which commands get Click's argument expansion
# --------------------------------------------------------------------------


class ExpansionDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="jzr-bind-win-")
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.cli = __import__("jzr_tools.cli", fromlist=["cli"])

    def bind(self, **named: list[str]) -> None:
        (self.config / "bindings.json").write_text(
            json.dumps({"version": 1, "bindings": named}), encoding="utf-8"
        )

    def decide(self, *argv: str) -> bool:
        return self.cli.should_expand_windows_args(list(argv))

    def test_bind_commands_are_not_expanded(self) -> None:
        for argv in (["bind"], ["bind", "add", "x", "$HOME"], ["bind", "run", "x", "%PATH%"], ["bind", "list"]):
            with self.subTest(argv=argv):
                self.assertIs(self.decide(*argv), False)

    def test_a_binding_name_is_not_expanded(self) -> None:
        self.bind(tunnel=["ngrok", "http"])

        self.assertIs(self.decide("tunnel", "$HOME", "*.py"), False)

    def test_other_commands_keep_the_expansion(self) -> None:
        self.bind(tunnel=["ngrok", "http"])
        for argv in (
            ["text", "x"], ["image", "convert", "*.png"], ["port", "kill", "8080"],
            ["host", "procs"], ["killport", "8080"], ["version"],
        ):
            with self.subTest(argv=argv):
                self.assertIs(self.decide(*argv), True)

    def test_a_built_in_name_wins_over_a_binding_of_the_same_name(self) -> None:
        self.bind(host=["echo", "hi"])

        self.assertIs(self.decide("host", "procs", "*"), True)

    def test_an_unknown_name_that_is_not_bound_keeps_the_expansion(self) -> None:
        self.bind(tunnel=["ngrok"])

        self.assertIs(self.decide("nothing-called-this", "*.py"), True)

    def test_no_arguments_and_leading_options_keep_the_expansion(self) -> None:
        self.bind(tunnel=["ngrok"])
        for argv in ([], ["--version"], ["--help"], ["-h"]):
            with self.subTest(argv=argv):
                self.assertIs(self.decide(*argv), True)

    def test_a_corrupt_bindings_file_does_not_break_the_decision(self) -> None:
        (self.config / "bindings.json").write_text("{not json", encoding="utf-8")

        self.assertIsInstance(self.decide("whatever"), bool)
        self.assertIs(self.decide("bind", "list"), False)
        self.assertIs(self.decide("text"), True)

    def test_main_hands_the_decision_to_click(self) -> None:
        self.bind(tunnel=["ngrok"])
        cases = {("bind", "add", "x", "$HOME"): False, ("tunnel", "*.py"): False, ("text", "*.py"): True}
        for argv, expected in cases.items():
            with self.subTest(argv=argv):
                with mock.patch.object(self.cli, "app") as app, \
                     mock.patch.object(sys, "argv", ["jzr", *argv]):
                    self.cli.main()

                app.assert_called_once()
                self.assertIs(app.call_args.kwargs.get("windows_expand_args", True), expected)


class RealExpansionTests(bind_tests.BindTestCase):
    """Real ``python -m jzr_tools`` processes; the words reach the program as typed."""

    WORDS = ["$JZR_TEST_VALUE", "$HOME", "%JZR_TEST_VALUE%", "%PATH%", "~", "~/sub", "*.py", "?.py", "two words"]

    def env(self) -> dict[str, str]:
        env = self.subprocess_env()
        env["JZR_TEST_VALUE"] = "EXPANDED"
        env["HOME"] = env["USERPROFILE"] = str(self.config.parent / "home")
        return env

    def run_jzr(self, *args: str) -> subprocess.CompletedProcess:
        # The tests folder holds *.py files, so a glob would find something.
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", *args],
            cwd=TESTS_DIR, env=self.env(), capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_extra_arguments_of_a_binding_are_passed_verbatim(self) -> None:
        added = self.run_jzr("bind", "add", "show", sys.executable, "-c", ARGV_PRINTER)
        self.assertEqual(added.returncode, 0, added.stderr)

        for prefix in (("show",), ("bind", "run", "show")):
            with self.subTest(prefix=prefix):
                proc = self.run_jzr(*prefix, *self.WORDS)

                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertEqual(proc.stdout.strip(), repr(self.WORDS))

    def test_bind_add_stores_the_words_verbatim(self) -> None:
        added = self.run_jzr("bind", "add", "keep", sys.executable, "-c", ARGV_PRINTER, *self.WORDS)
        self.assertEqual(added.returncode, 0, added.stderr)

        self.assertEqual(self.stored()["keep"], [sys.executable, "-c", ARGV_PRINTER, *self.WORDS])
        proc = self.run_jzr("keep")
        self.assertEqual(proc.stdout.strip(), repr(self.WORDS))

    @unittest.skipUnless(WINDOWS, "Click only expands arguments on Windows")
    def test_other_commands_keep_clicks_expansion(self) -> None:
        # `version` takes no arguments, so Click names the (expanded) extras.
        proc = self.run_jzr("version", "$JZR_TEST_VALUE", "%JZR_TEST_VALUE%")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("EXPANDED", proc.stderr)
        self.assertNotIn("JZR_TEST_VALUE", proc.stderr)


# --------------------------------------------------------------------------
# .cmd / .bat programs: the exact command line (subprocess faked)
# --------------------------------------------------------------------------

CMD_LINE = re.compile(r'^(?P<shell>"?[^"]*cmd(?:\.exe)?"?) /d /s /c "(?P<inner>.*)"$', re.DOTALL | re.IGNORECASE)


class FakePopen:
    def __init__(self, calls: list):
        self.calls = calls

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        process = mock.MagicMock()
        process.wait.return_value = 0
        process.returncode = 0
        return process


class BatchCommandLineTests(unittest.TestCase):
    PROGRAM = "C:\\tools\\prog.cmd"

    def run_batch(self, arguments, program=PROGRAM, windows=True):
        b = bindings()
        calls: list = []
        with fake_windows(windows), \
             mock.patch.object(b.shutil, "which", return_value=program), \
             mock.patch.object(b.subprocess, "Popen", side_effect=FakePopen(calls)):
            code = b.run(arguments)
        return code, calls

    def command_line(self, arguments, **kwargs) -> re.Match:
        _, calls = self.run_batch(arguments, **kwargs)
        self.assertEqual(len(calls), 1, "exactly one process must be started")
        first = calls[0][0][0]
        self.assertIsInstance(first, str, "the command line must be one string, not a list")
        match = CMD_LINE.match(first)
        self.assertIsNotNone(match, f"not of the form 'cmd.exe /d /s /c \"...\"': {first!r}")
        return match

    def test_run_through_cmd_with_every_argument_double_quoted(self) -> None:
        match = self.command_line(["prog", "a", "two words", "x&y", "p|q", "<", ">", "c^d", "(e)", ""])

        self.assertEqual(
            match["inner"],
            '"C:\\tools\\prog.cmd" "a" "two words" "x&y" "p|q" "<" ">" "c^d" "(e)" ""',
        )

    def test_the_shell_is_cmd_exe(self) -> None:
        match = self.command_line(["prog"])

        shell = match["shell"].strip('"').replace("/", "\\").rsplit("\\", 1)[-1].lower()
        self.assertIn(shell, ("cmd", "cmd.exe"))

    def test_the_program_with_no_arguments_is_quoted_too(self) -> None:
        self.assertEqual(self.command_line(["prog"])["inner"], '"C:\\tools\\prog.cmd"')

    def test_a_program_path_with_spaces_stays_one_word(self) -> None:
        match = self.command_line(["prog", "x"], program="C:\\My Tools\\run it.bat")

        self.assertEqual(match["inner"], '"C:\\My Tools\\run it.bat" "x"')

    def test_extension_is_matched_without_regard_to_case(self) -> None:
        for program in ("C:\\t\\a.CMD", "C:\\t\\a.Bat", "C:\\t\\a.bat"):
            with self.subTest(program=program):
                self.assertTrue(self.command_line(["a", "x"], program=program)["inner"].startswith('"' + program))

    def test_the_exit_code_of_the_batch_file_is_returned(self) -> None:
        b = bindings()
        process = mock.MagicMock()
        process.wait.return_value = 7
        with fake_windows(True), \
             mock.patch.object(b.shutil, "which", return_value=self.PROGRAM), \
             mock.patch.object(b.subprocess, "Popen", return_value=process):
            self.assertEqual(b.run(["prog"]), 7)

    def test_an_executable_is_started_directly_without_cmd(self) -> None:
        _, calls = self.run_batch(["prog", "x&y", "50%"], program="C:\\tools\\prog.exe")

        self.assertEqual(calls[0][0][0], ["C:\\tools\\prog.exe", "x&y", "50%"])

    def test_on_posix_a_cmd_file_is_started_directly(self) -> None:
        _, calls = self.run_batch(["prog", "x&y"], windows=False)

        self.assertEqual(calls[0][0][0], [self.PROGRAM, "x&y"])

    def refused(self, arguments, program=PROGRAM):
        b = bindings()
        calls: list = []
        with fake_windows(True), \
             mock.patch.object(b.shutil, "which", return_value=program), \
             mock.patch.object(b.subprocess, "Popen", side_effect=FakePopen(calls)):
            with self.assertRaises(b.BindingError) as caught:
                b.run(arguments)
        self.assertEqual(calls, [], "the program must not be started")
        return str(caught.exception)

    def test_arguments_that_cannot_be_passed_safely_are_refused_by_name(self) -> None:
        for bad in ("50%", "%PATH%", 'say "hi"', "wow!", "a\nb", "a\rb"):
            with self.subTest(argument=bad):
                message = self.refused(["prog", "fine", bad])

                self.assertTrue(
                    bad in message or repr(bad)[1:-1] in message,
                    f"the error must name the argument: {message!r}",
                )

    def test_a_stored_word_is_checked_like_an_extra_one(self) -> None:
        message = self.refused(["prog", "--rate", "5%", "extra"])

        self.assertIn("5%", message)

    def test_the_same_characters_are_fine_for_an_executable(self) -> None:
        b = bindings()
        calls: list = []
        with fake_windows(True), \
             mock.patch.object(b.shutil, "which", return_value="C:\\tools\\prog.exe"), \
             mock.patch.object(b.subprocess, "Popen", side_effect=FakePopen(calls)):
            b.run(["prog", "50%", 'say "hi"', "wow!"])

        self.assertEqual(calls[0][0][0], ["C:\\tools\\prog.exe", "50%", 'say "hi"', "wow!"])

    def test_percent_in_the_program_path_is_not_an_argument_and_is_not_refused(self) -> None:
        match = self.command_line(["prog", "x"], program="C:\\100%\\prog.cmd")

        self.assertEqual(match["inner"], '"C:\\100%\\prog.cmd" "x"')


class RefusalThroughTheCommandTests(bind_tests.BindTestCase):
    def test_a_refused_argument_gives_exit_one_and_a_message_that_names_it(self) -> None:
        b = bindings()
        self.write_file({"show": ["prog"]})
        calls: list = []
        with fake_windows(True), \
             mock.patch.object(b.shutil, "which", return_value="C:\\tools\\prog.cmd"), \
             mock.patch.object(b.subprocess, "Popen", side_effect=FakePopen(calls)):
            result = self.jzr("show", "50%")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertTrue(result.stderr.startswith("show:"), result.stderr)
        self.assertIn("50%", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(calls, [])

    def test_the_same_through_bind_run(self) -> None:
        b = bindings()
        self.write_file({"show": ["prog"]})
        calls: list = []
        with fake_windows(True), \
             mock.patch.object(b.shutil, "which", return_value="C:\\tools\\prog.cmd"), \
             mock.patch.object(b.subprocess, "Popen", side_effect=FakePopen(calls)):
            result = self.jzr("bind", "run", "show", 'a"b')

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn('a"b', result.stderr)
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------
# .cmd / .bat programs: real cmd.exe (Windows only)
# --------------------------------------------------------------------------

PRINTER = "import sys\nprint(sys.argv[1:])\nsys.exit(3)\n"


@unittest.skipUnless(WINDOWS, "needs cmd.exe")
class RealBatchTests(bind_tests.BindTestCase):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="jzr-bind-batch-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "printer.py").write_text(PRINTER, encoding="utf-8")

    def make_script(self, suffix: str) -> Path:
        script = self.dir / f"echo-args{suffix}"
        script.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0printer.py" %*\r\n', encoding="utf-8")
        return script

    def jzr_process(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
        # Run in the scratch folder: while quoting is missing, `>` and `<` in
        # the arguments would create or read files in the working directory.
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", *args],
            cwd=self.dir, env=self.subprocess_env(), input=input,
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def bind(self, script: Path, *stored: str) -> None:
        added = self.jzr_process("bind", "add", "--force", "batch", str(script), *stored)
        self.assertEqual(added.returncode, 0, added.stderr)

    AWKWARD = ["a&b", "c|d", "e<f", "g>h", "i^j", "two  words", "(paren)", "--flag", "back\\slash", "k&echo pwned"]

    def test_special_characters_reach_the_program_literally(self) -> None:
        for suffix in (".cmd", ".bat"):
            with self.subTest(suffix=suffix):
                self.bind(self.make_script(suffix), "stored&one")

                proc = self.jzr_process("batch", *self.AWKWARD)

                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertEqual(proc.stdout.strip(), repr(["stored&one", *self.AWKWARD]))

    def test_the_same_through_bind_run(self) -> None:
        self.bind(self.make_script(".cmd"))

        proc = self.jzr_process("bind", "run", "batch", "x&y", "p|q")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["x&y", "p|q"]))

    def test_unsafe_arguments_are_refused_and_the_batch_file_is_not_started(self) -> None:
        marker = self.dir / "started.txt"
        script = self.dir / "marker.cmd"
        script.write_text(f'@echo started> "{marker}"\r\n', encoding="utf-8")
        self.bind(script)

        for bad in ("100%", "%PATH%", 'say "hi"', "wow!", "a\nb"):
            with self.subTest(argument=bad):
                proc = self.jzr_process("batch", bad)

                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertTrue(bad in proc.stderr or repr(bad)[1:-1] in proc.stderr, proc.stderr)
                self.assertFalse(marker.exists(), "the batch file must not have run")
                self.assertNotIn("Traceback", proc.stderr)

    def test_the_batch_file_runs_when_the_arguments_are_safe(self) -> None:
        marker = self.dir / "started.txt"
        script = self.dir / "marker.cmd"
        script.write_text(f'@echo started> "{marker}"\r\n', encoding="utf-8")
        self.bind(script)

        proc = self.jzr_process("batch", "fine")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(marker.exists())


# --------------------------------------------------------------------------
# describe / split_command / the lines bind prints
# --------------------------------------------------------------------------

TRICKY = [
    ["prog"],
    ["prog", "a b"],
    ["prog", ""],
    ["prog", "two", "", "words"],
    ["prog", "back\\slash"],
    ["prog", "C:\\Program Files\\App\\app.exe"],
    ["prog", "trailing\\"],
    ["prog", "C:\\Program Files\\App Dir\\"],
    ["prog", "two\\\\"],
    ["prog", 'say "hi"'],
    ["prog", 'a\\"b'],
    ["prog", '"'],
    ["prog", "\\"],
    ["prog", "tab\there"],
    ["prog", "--opt=a b", "ünï"],
    ["prog", "a&b", "c|d", "e^f", "g>h"],
]


class WindowsDescribeTests(unittest.TestCase):
    def test_a_word_with_a_space_is_double_quoted(self) -> None:
        with fake_windows(True):
            self.assertEqual(bindings().describe(["echo", "hello world", "x y"]), 'echo "hello world" "x y"')

    def test_plain_words_are_not_decorated(self) -> None:
        with fake_windows(True):
            self.assertEqual(bindings().describe(["ngrok", "http", "5173", "--url", "https://x.dev"]),
                             "ngrok http 5173 --url https://x.dev")

    def test_an_empty_word_is_written_as_two_quotes(self) -> None:
        with fake_windows(True):
            self.assertEqual(bindings().describe(["echo", ""]), 'echo ""')

    def test_describe_is_what_subprocess_would_build(self) -> None:
        for arguments in TRICKY:
            with self.subTest(arguments=arguments), fake_windows(True):
                self.assertEqual(bindings().describe(arguments), subprocess.list2cmdline(arguments))

    def test_on_posix_describe_keeps_shell_quoting(self) -> None:
        with fake_windows(False):
            self.assertEqual(bindings().describe(["echo", "hello world"]), "echo 'hello world'")


class WindowsSplitTests(unittest.TestCase):
    def split(self, text: str) -> list[str]:
        with fake_windows(True):
            return bindings().split_command([text])

    def test_describe_then_split_gives_the_same_words(self) -> None:
        b = bindings()
        for arguments in TRICKY:
            with self.subTest(arguments=arguments), fake_windows(True):
                text = b.describe(arguments)

                self.assertEqual(b.split_command([text]), arguments)

    def test_plain_words_split_on_spaces(self) -> None:
        self.assertEqual(
            self.split("ngrok http 5173 --url https://example.dev"),
            ["ngrok", "http", "5173", "--url", "https://example.dev"],
        )

    def test_double_quotes_group_words(self) -> None:
        self.assertEqual(self.split('git commit -m "two words" "and more"'), ["git", "commit", "-m", "two words", "and more"])

    def test_backslashes_in_paths_are_kept(self) -> None:
        self.assertEqual(
            self.split('C:\\Tools\\x.exe --dir C:\\data\\in "C:\\Program Files\\y z\\f.txt"'),
            ["C:\\Tools\\x.exe", "--dir", "C:\\data\\in", "C:\\Program Files\\y z\\f.txt"],
        )

    def test_a_backslash_before_a_quote_escapes_the_quote(self) -> None:
        self.assertEqual(self.split('echo \\"hi\\" "a \\"b\\" c"'), ["echo", '"hi"', 'a "b" c'])

    def test_two_backslashes_before_a_closing_quote_are_one_literal_backslash(self) -> None:
        self.assertEqual(self.split('echo "dir\\\\"'), ["echo", "dir\\"])

    def test_an_empty_quoted_word_is_kept(self) -> None:
        self.assertEqual(self.split('echo "" x'), ["echo", "", "x"])

    def test_empty_or_blank_gives_no_words(self) -> None:
        for text in ("", "   ", "\t \n"):
            with self.subTest(text=text):
                self.assertEqual(self.split(text), [])

    def test_several_parts_are_still_returned_unchanged(self) -> None:
        parts = ["ngrok", "http 5173", 'say "hi"', "C:\\x\\"]
        with fake_windows(True):
            self.assertEqual(bindings().split_command(parts), parts)

    def test_on_posix_single_quotes_and_backslash_escapes_still_work(self) -> None:
        with fake_windows(False):
            self.assertEqual(bindings().split_command(["echo 'a b' c\\ d"]), ["echo", "a b", "c d"])


class WindowsBindLinesTests(bind_tests.BindTestCase):
    """What `bind add` and `bind list` print on Windows."""

    def test_add_and_list_quote_the_windows_way(self) -> None:
        with fake_windows(True):
            added = self.jzr("bind", "add", "greet", "echo", "hello world", "C:\\a b\\")
            listed = self.jzr("bind", "list")

        self.assertEqual(added.exit_code, 0, added.stderr)
        self.assertEqual(added.stdout.splitlines()[0], 'Bound greet: echo "hello world" "C:\\a b\\\\"')
        self.assertEqual(listed.stdout.strip(), 'greet  echo "hello world" "C:\\a b\\\\"')

    def test_the_line_printed_by_add_reads_back_to_the_stored_words(self) -> None:
        words = ["prog", "two words", 'say "hi"', "C:\\Program Files\\x\\", ""]
        with fake_windows(True):
            added = self.jzr("bind", "add", "t", *words)
            printed = added.stdout.splitlines()[0].split(": ", 1)[1]

            self.assertEqual(bindings().split_command([printed]), words)
        self.assertEqual(self.stored()["t"], words)

    def test_one_string_form_with_double_quotes_stores_the_words(self) -> None:
        with fake_windows(True):
            result = self.jzr("bind", "add", "t", 'prog "two words" C:\\x\\y')

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored()["t"], ["prog", "two words", "C:\\x\\y"])


@unittest.skipUnless(WINDOWS, "list2cmdline quoting is only a command line on Windows")
class RealSingleStringTests(bind_tests.BindTestCase):
    """Windows counterpart of the POSIX-only single-string run test."""

    def test_single_string_form_runs_like_the_multi_argument_form(self) -> None:
        command = subprocess.list2cmdline([sys.executable, "-c", ARGV_PRINTER, "one", "two words"])
        added = self.jzr_process("bind", "add", "oneline", command)
        self.assertEqual(added.returncode, 0, added.stderr)

        proc = self.jzr_process("oneline", "three")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["one", "two words", "three"]))


if __name__ == "__main__":
    unittest.main()

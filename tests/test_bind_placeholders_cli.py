"""Placeholders in `jzr bind` commands, through the command line.

Every invocation points JZR_CONFIG_DIR at a fresh temporary folder, so the
real bindings file is never read or written.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import bindings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

POWERSHELL_WORDS = ["-encodedCommand", "MQA=", "-inputFormat", "xml", "-outputFormat", "text"]
POWERSHELL_MESSAGE = (
    "PowerShell turned {1} into a script block. Quote it ('{1}'), "
    "or give the whole command as one quoted string."
)


def shown(words: list[str]) -> str:
    """The command line as `bind add` and `bind list` print it."""
    return bindings.describe(words)


class PlaceholderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="jzr-bind-placeholders-")
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "config"
        self.file = self.config / "bindings.json"
        self.runner = CliRunner()

    def jzr(self, *args: str):
        return self.runner.invoke(app, list(args), env={"JZR_CONFIG_DIR": str(self.config)})

    def write_file(self, stored: dict[str, list[str]]) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"version": 1, "bindings": stored}), encoding="utf-8")

    def stored(self) -> dict[str, list[str]]:
        return json.loads(self.file.read_text(encoding="utf-8"))["bindings"]

    def run_with_mock(self, *args: str, returns: int = 0):
        with mock.patch("jzr_tools.core.bindings.run", return_value=returns) as fake:
            result = self.jzr(*args)
        return result, fake

    def words_run(self, fake) -> list[str]:
        fake.assert_called_once()
        return list(fake.call_args.args[0])


class AddPlaceholderTests(PlaceholderTestCase):
    def test_a_numbered_placeholder_is_stored_and_the_arity_reported(self) -> None:
        result = self.jzr("bind", "add", "ci", "git", "commit", "-m", "{1}", "--no-verify")

        self.assertEqual(result.exit_code, 0, result.stderr)
        words = ["git", "commit", "-m", "{1}", "--no-verify"]
        self.assertEqual(self.stored(), {"ci": words})
        self.assertEqual(result.stdout.strip(), f"Bound ci: {shown(words)} (takes 1 argument)")
        if os.name == "nt":
            self.assertEqual(
                result.stdout.strip(), "Bound ci: git commit -m {1} --no-verify (takes 1 argument)"
            )

    def test_the_one_string_form_stores_the_same_words(self) -> None:
        result = self.jzr("bind", "add", "ci", "git commit -m {1} --no-verify")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored(), {"ci": ["git", "commit", "-m", "{1}", "--no-verify"]})
        self.assertTrue(result.stdout.strip().endswith("(takes 1 argument)"), result.stdout)

    def test_the_arity_wording_follows_the_placeholders(self) -> None:
        cases = [
            (["git", "mv", "{1}", "{2}"], "(takes 2 arguments)"),
            (["git", "mv", "{2}", "{1}"], "(takes 2 arguments)"),
            (["git", "log", "{*}", "--oneline"], "(takes any arguments)"),
            (["git", "log", "{1}", "{*}"], "(takes 1 argument and more)"),
            (["git", "mv", "{1}", "{2}", "{*}"], "(takes 2 arguments and more)"),
            (["echo", "{1}", "{1}"], "(takes 1 argument)"),
        ]
        for words, suffix in cases:
            with self.subTest(words=words):
                self.file.unlink(missing_ok=True)
                result = self.jzr("bind", "add", "t", *words)

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(self.stored()["t"], words)
                self.assertEqual(result.stdout.strip(), f"Bound t: {shown(words)} {suffix}")

    def test_replacing_a_binding_also_reports_the_arity(self) -> None:
        self.write_file({"t": ["echo", "old"]})

        result = self.jzr("bind", "add", "--force", "t", "echo", "{1}")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"Replaced t: {shown(['echo', '{1}'])} (takes 1 argument)")

    def test_commands_without_placeholders_get_no_suffix(self) -> None:
        cases = [
            ["echo", "hi"],
            ["curl", "-d", '{"a":1}', "http://x"],
            ["find", ".", "-exec", "ls", "{}", ";"],
            ["echo", "{{1}}", "{{*}}"],
            ["echo", "{1:main}", "{a}", "{ 1 }"],
        ]
        for words in cases:
            with self.subTest(words=words):
                self.file.unlink(missing_ok=True)
                result = self.jzr("bind", "add", "t", *words)

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(self.stored()["t"], words)
                self.assertEqual(result.stdout.strip(), f"Bound t: {shown(words)}")

    def test_a_placeholder_in_the_first_word_gets_no_path_note(self) -> None:
        result = self.jzr("bind", "add", "go", "{1}", "--version")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertNotIn("not on PATH", result.stderr)
        self.assertEqual(self.stored(), {"go": ["{1}", "--version"]})

    def test_a_placeholder_inside_the_first_word_gets_no_path_note(self) -> None:
        result = self.jzr("bind", "add", "go", "jzr-no-such-{1}-xyz", "arg")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertNotIn("not on PATH", result.stderr)

    def test_a_missing_program_without_a_placeholder_is_still_noted(self) -> None:
        result = self.jzr("bind", "add", "ghost", "jzr-no-such-program-xyz", "{1}")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("'jzr-no-such-program-xyz' is not on PATH", result.stderr)


class AddRefusalTests(PlaceholderTestCase):
    def assert_refused(self, words: list[str], message: str) -> None:
        before = self.file.read_bytes() if self.file.exists() else None

        result = self.jzr("bind", "add", "ci", *words)

        self.assertEqual(result.exit_code, 2, f"{words}: {result.stdout!r} {result.stderr!r}")
        self.assertIn(message, result.stderr)
        after = self.file.read_bytes() if self.file.exists() else None
        self.assertEqual(after, before, "a refused add must not change the file")

    def test_a_star_inside_a_word_is_refused(self) -> None:
        self.assert_refused(["git", "log", "--x={*}"], "{*} must be a word on its own.")

    def test_a_star_twice_is_refused(self) -> None:
        self.assert_refused(["echo", "{*}", "{*}"], "{*} must be a word on its own.")

    def test_a_gap_is_refused(self) -> None:
        self.assert_refused(
            ["echo", "{2}"], "{2} is used but {1} is not; number the arguments from {1}."
        )
        self.assert_refused(
            ["echo", "{1}", "{3}"], "{3} is used but {2} is not; number the arguments from {1}."
        )

    def test_zero_and_leading_zeros_are_refused(self) -> None:
        self.assert_refused(["echo", "{0}"], "{0} is not a placeholder; they start at {1}.")
        self.assert_refused(["echo", "{01}"], "{01} is not a placeholder; they start at {1}.")

    def test_a_refusal_leaves_an_existing_file_and_binding_alone(self) -> None:
        self.write_file({"keep": ["echo", "{1}"]})

        self.assert_refused(["echo", "{2}"], "{2} is used but {1} is not")
        self.assertEqual(self.stored(), {"keep": ["echo", "{1}"]})

    def test_a_refused_add_in_the_one_string_form_changes_nothing(self) -> None:
        result = self.jzr("bind", "add", "ci", "git commit -m {2}")

        self.assertEqual(result.exit_code, 2, result.stderr)
        self.assertIn("{2} is used but {1} is not", result.stderr)
        self.assertFalse(self.file.exists())

    def test_the_powershell_script_block_words_are_refused(self) -> None:
        self.assert_refused(["git", "commit", "-m", *POWERSHELL_WORDS], POWERSHELL_MESSAGE)

    def test_the_powershell_words_after_an_attached_prefix_are_refused(self) -> None:
        self.assert_refused(["git", "commit", "--m=", *POWERSHELL_WORDS], POWERSHELL_MESSAGE)

    def test_the_powershell_words_alone_are_refused_without_creating_the_file(self) -> None:
        self.assert_refused(POWERSHELL_WORDS, POWERSHELL_MESSAGE)
        self.assertFalse(self.file.exists())


class RunPlaceholderTests(PlaceholderTestCase):
    def assert_runs(self, stored: list[str], given: list[str], expected: list[str]) -> None:
        self.write_file({"t": stored})
        for prefix in (("t",), ("bind", "run", "t")):
            with self.subTest(prefix=prefix, stored=stored, given=given):
                result, fake = self.run_with_mock(*prefix, *given)

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(self.words_run(fake), expected)

    def test_an_argument_goes_into_the_placeholder_as_one_word(self) -> None:
        self.assert_runs(
            ["git", "commit", "-m", "{1}", "--no-verify"],
            ["fix it"],
            ["git", "commit", "-m", "fix it", "--no-verify"],
        )

    def test_a_placeholder_next_to_text_is_filled_in_the_same_word(self) -> None:
        self.assert_runs(
            ["tail", "--file=/var/log/{1}.log", "-n", "5"],
            ["my app"],
            ["tail", "--file=/var/log/my app.log", "-n", "5"],
        )

    def test_the_same_number_twice_gets_the_same_value(self) -> None:
        self.assert_runs(["cp", "{1}", "{1}.bak"], ["a.txt"], ["cp", "a.txt", "a.txt.bak"])

    def test_numbers_may_come_in_any_order(self) -> None:
        self.assert_runs(["mv", "{2}", "{1}"], ["a", "b"], ["mv", "b", "a"])

    def test_extra_arguments_without_a_star_go_on_the_end(self) -> None:
        self.assert_runs(
            ["git", "commit", "-m", "{1}", "--no-verify"],
            ["first", "second", "-x"],
            ["git", "commit", "-m", "first", "--no-verify", "second", "-x"],
        )

    def test_a_star_takes_the_rest_in_its_place(self) -> None:
        self.assert_runs(
            ["git", "log", "{*}", "--oneline"],
            ["-5", "main"],
            ["git", "log", "-5", "main", "--oneline"],
        )

    def test_a_star_after_a_number_takes_only_what_is_left(self) -> None:
        self.assert_runs(
            ["ssh", "{1}", "{*}", "--end"],
            ["host", "ls", "-l"],
            ["ssh", "host", "ls", "-l", "--end"],
        )

    def test_a_star_with_no_arguments_disappears(self) -> None:
        self.assert_runs(["git", "log", "{*}", "--oneline"], [], ["git", "log", "--oneline"])

    def test_a_star_with_exactly_the_numbered_arguments_disappears(self) -> None:
        self.assert_runs(["ssh", "{1}", "{*}"], ["host"], ["ssh", "host"])

    def test_option_looking_arguments_belong_to_the_command(self) -> None:
        for flag in ("--help", "-h", "--version"):
            self.assert_runs(["prog", "{1}", "--tail"], [flag], ["prog", flag, "--tail"])

    def test_an_empty_argument_counts_as_given(self) -> None:
        self.assert_runs(["git", "commit", "-m", "{1}"], [""], ["git", "commit", "-m", ""])

    def test_a_placeholder_can_stand_in_the_program_word(self) -> None:
        self.assert_runs(["{1}", "--version"], ["python"], ["python", "--version"])

    def test_a_value_that_looks_like_a_placeholder_is_not_filled_again(self) -> None:
        self.assert_runs(["echo", "{1}", "{2}"], ["{2}", "x"], ["echo", "{2}", "x"])

    def test_doubled_braces_reach_the_program_as_single_ones(self) -> None:
        self.assert_runs(["echo", "{{1}}", "{{*}}", "x"], [], ["echo", "{1}", "{*}", "x"])

    def test_an_escaped_placeholder_does_not_count_as_one(self) -> None:
        self.assert_runs(["echo", "{{1}}"], ["extra"], ["echo", "{1}", "extra"])

    def test_other_braces_are_left_alone(self) -> None:
        stored = ["curl", "-d", '{"a":1}', "{}", "{a}", "{1:main}", "{ 1 }"]
        self.assert_runs(stored, ["more"], [*stored, "more"])

    def test_the_exit_code_of_the_program_is_kept(self) -> None:
        self.write_file({"t": ["prog", "{1}"]})

        result, _ = self.run_with_mock("t", "x", returns=7)

        self.assertEqual(result.exit_code, 7)


class MissingArgumentTests(PlaceholderTestCase):
    def test_too_few_arguments_run_nothing_and_exit_2(self) -> None:
        stored = ["git", "commit", "-m", "{1}", "--no-verify"]
        self.write_file({"ci": stored})

        for prefix in (("ci",), ("bind", "run", "ci")):
            with self.subTest(prefix=prefix):
                result, fake = self.run_with_mock(*prefix)

                self.assertEqual(result.exit_code, 2, result.stderr)
                fake.assert_not_called()
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr.strip(), f"ci needs 1 argument (got 0): {shown(stored)}"
                )
                self.assertNotIn("Traceback", result.stderr)

    def test_the_message_has_no_quotes_around_a_placeholder_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("quoting of braces follows the platform shell")
        self.write_file({"ci": ["git", "commit", "-m", "{1}", "--no-verify"]})

        result, _ = self.run_with_mock("ci")

        self.assertEqual(
            result.stderr.strip(), "ci needs 1 argument (got 0): git commit -m {1} --no-verify"
        )

    def test_two_needed_uses_the_plural(self) -> None:
        stored = ["git", "mv", "{1}", "{2}"]
        self.write_file({"mv": stored})

        for given, count in (([], 0), (["a"], 1)):
            with self.subTest(given=given):
                result, fake = self.run_with_mock("mv", *given)

                self.assertEqual(result.exit_code, 2, result.stderr)
                fake.assert_not_called()
                self.assertEqual(
                    result.stderr.strip(), f"mv needs 2 arguments (got {count}): {shown(stored)}"
                )

    def test_the_highest_number_decides_how_many_are_needed(self) -> None:
        stored = ["echo", "{1}", "{3}", "{2}"]
        self.write_file({"t": stored})

        result, fake = self.run_with_mock("t", "a", "b")

        self.assertEqual(result.exit_code, 2, result.stderr)
        fake.assert_not_called()
        self.assertIn("t needs 3 arguments (got 2)", result.stderr)

    def test_a_star_does_not_make_arguments_optional_when_numbers_are_used(self) -> None:
        self.write_file({"t": ["ssh", "{1}", "{*}"]})

        result, fake = self.run_with_mock("t")

        self.assertEqual(result.exit_code, 2, result.stderr)
        fake.assert_not_called()
        self.assertIn("t needs 1 argument (got 0)", result.stderr)

    def test_an_empty_argument_is_enough(self) -> None:
        self.write_file({"t": ["prog", "{1}"]})

        result, fake = self.run_with_mock("t", "")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.words_run(fake), ["prog", ""])


class HandEditedFileTests(PlaceholderTestCase):
    def test_a_bad_placeholder_fails_that_binding_only(self) -> None:
        self.write_file({"ci": ["git", "--x={*}"], "ok": ["echo", "{1}"]})

        for prefix in (("ci",), ("bind", "run", "ci")):
            with self.subTest(prefix=prefix):
                result, fake = self.run_with_mock(*prefix, "arg")

                self.assertEqual(result.exit_code, 1, result.stderr)
                fake.assert_not_called()
                self.assertTrue(
                    result.stderr.startswith("ci: {*} must be a word on its own."), result.stderr
                )
                self.assertNotIn("Traceback", result.stderr)

        result, fake = self.run_with_mock("ok", "hi")
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.words_run(fake), ["echo", "hi"])

    def test_a_gap_and_a_zero_fail_when_the_binding_runs(self) -> None:
        self.write_file({"gap": ["echo", "{2}"], "zero": ["echo", "{0}"]})

        gap, fake = self.run_with_mock("gap", "a", "b")
        self.assertEqual(gap.exit_code, 1, gap.stderr)
        self.assertTrue(
            gap.stderr.startswith("gap: {2} is used but {1} is not; number the arguments from {1}."),
            gap.stderr,
        )
        zero, _ = self.run_with_mock("zero", "a")
        self.assertEqual(zero.exit_code, 1, zero.stderr)
        self.assertTrue(
            zero.stderr.startswith("zero: {0} is not a placeholder; they start at {1}."), zero.stderr
        )
        fake.assert_not_called()

    def test_the_file_is_not_changed_by_a_failing_run(self) -> None:
        self.write_file({"ci": ["git", "--x={*}"]})
        before = self.file.read_bytes()

        self.run_with_mock("ci", "arg")
        self.run_with_mock("bind", "run", "ci", "arg")

        self.assertEqual(self.file.read_bytes(), before)


class ListPlaceholderTests(PlaceholderTestCase):
    def test_list_shows_the_placeholders_as_stored(self) -> None:
        stored = {
            "ci": ["git", "commit", "-m", "{1}", "--no-verify"],
            "lg": ["git", "log", "{*}", "--oneline"],
        }
        self.write_file(stored)

        result = self.jzr("bind", "list")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"ci  {shown(stored['ci'])}", f"lg  {shown(stored['lg'])}"],
        )
        self.assertIn("{1}", result.stdout)
        self.assertIn("{*}", result.stdout)

    def test_list_json_keeps_its_shape(self) -> None:
        stored = {"ci": ["git", "commit", "-m", "{1}"], "lg": ["git", "log", "{*}"]}
        self.write_file(stored)

        result = self.jzr("bind", "list", "--json")

        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"path", "bindings"})
        self.assertEqual(document["path"], str(self.file))
        self.assertEqual(document["bindings"], stored)


class RealProgramTests(PlaceholderTestCase):
    PRINTER = "import sys; print(sys.argv[1:]); sys.exit(3)"

    def subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        env["JZR_CONFIG_DIR"] = str(self.config)
        return env

    def jzr_process(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", *args],
            cwd=PROJECT_ROOT,
            env=self.subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def bind_printer(self, *tail: str) -> None:
        added = self.jzr("bind", "add", "show", sys.executable, "-c", self.PRINTER, *tail)
        self.assertEqual(added.exit_code, 0, added.stderr)

    def test_the_program_receives_the_filled_in_words(self) -> None:
        self.bind_printer("{1}", "--tail")

        for prefix in (("show",), ("bind", "run", "show")):
            with self.subTest(prefix=prefix):
                proc = self.jzr_process(*prefix, "two words", "more")

                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertEqual(proc.stdout.strip(), repr(["two words", "--tail", "more"]))

    def test_a_star_and_an_embedded_number_reach_the_program(self) -> None:
        self.bind_printer("--m={1}", "{*}", "--tail")

        proc = self.jzr_process("show", "x y", "-5", "main")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["--m=x y", "-5", "main", "--tail"]))

    def test_escaped_and_plain_braces_reach_the_program(self) -> None:
        self.bind_printer("{{1}}", '{"a":1}', "{}")

        proc = self.jzr_process("show")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["{1}", '{"a":1}', "{}"]))

    def test_missing_arguments_stop_before_the_program_starts(self) -> None:
        self.bind_printer("{1}", "--tail")

        proc = self.jzr_process("show")

        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertIn("show needs 1 argument (got 0): ", proc.stderr)


if __name__ == "__main__":
    unittest.main()

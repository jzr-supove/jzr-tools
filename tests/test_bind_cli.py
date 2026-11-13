"""Acceptance tests for `jzr bind` and `jzr NAME`.

HARD RULE: every invocation points JZR_CONFIG_DIR at a fresh temporary folder, so
the developer's real bindings file is never read or written.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

try:
    import fcntl
    import pty
    import termios
except ImportError:  # Windows
    pty = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
POSIX = os.name == "posix"

HIDDEN_MARK = "(hidden by a built-in command; use: jzr bind run host)"
EMPTY_LIST_MESSAGE = "No bindings yet. Add one with: jzr bind add NAME COMMAND..."

ARGV_PRINTER = "import sys; print(sys.argv[1:]); sys.exit(3)"

# `describe` quotes the way the platform's own shell does (docs/features/windows-host-bind).
QUOTED_HELLO_WORLD = '"hello world"' if os.name == "nt" else "'hello world'"


class BindTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="jzr-bind-cli-")
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "config"  # deliberately not created yet
        self.file = self.config / "bindings.json"
        self.runner = CliRunner()

    # -- helpers ---------------------------------------------------------
    def jzr(self, *args: str):
        return self.runner.invoke(app, list(args), env={"JZR_CONFIG_DIR": str(self.config)})

    def write_file(self, bindings: dict[str, list[str]]) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"version": 1, "bindings": bindings}), encoding="utf-8")

    def write_raw(self, content: bytes) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(content)

    def stored(self) -> dict[str, list[str]]:
        return json.loads(self.file.read_text(encoding="utf-8"))["bindings"]

    def subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        env["JZR_CONFIG_DIR"] = str(self.config)
        return env

    def jzr_process(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", *args],
            cwd=PROJECT_ROOT,
            env=self.subprocess_env(),
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )


class AddTests(BindTestCase):
    def test_several_arguments_are_stored_exactly_and_reported(self) -> None:
        result = self.jzr("bind", "add", "tunnel", "ngrok", "http", "5173", "--url", "https://example.dev")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            self.stored(),
            {"tunnel": ["ngrok", "http", "5173", "--url", "https://example.dev"]},
        )
        self.assertEqual(
            result.stdout.strip(), "Bound tunnel: ngrok http 5173 --url https://example.dev"
        )

    def test_one_quoted_argument_stores_the_same_list(self) -> None:
        result = self.jzr("bind", "add", "tunnel", "ngrok http 5173 --url https://example.dev")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            self.stored(),
            {"tunnel": ["ngrok", "http", "5173", "--url", "https://example.dev"]},
        )

    def test_both_forms_produce_identical_files(self) -> None:
        self.jzr("bind", "add", "t", "ngrok", "http", "5173")
        first = self.file.read_bytes()
        self.file.unlink()
        self.jzr("bind", "add", "t", "ngrok http 5173")
        self.assertEqual(self.file.read_bytes(), first)

    def test_several_arguments_with_spaces_are_not_resplit(self) -> None:
        result = self.jzr("bind", "add", "greet", "echo", "hello world", "x y")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored()["greet"], ["echo", "hello world", "x y"])
        quoted_xy = '"x y"' if os.name == "nt" else "'x y'"
        self.assertIn(f"Bound greet: echo {QUOTED_HELLO_WORLD} {quoted_xy}", result.stdout)

    def test_options_inside_the_command_are_not_read_by_jzr(self) -> None:
        result = self.jzr("bind", "add", "srv", "prog", "--url", "u", "-p", "80", "--help", "-h", "--force", "--version")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            self.stored()["srv"],
            ["prog", "--url", "u", "-p", "80", "--help", "-h", "--force", "--version"],
        )

    def test_a_command_that_starts_with_an_option_looking_word_is_kept(self) -> None:
        result = self.jzr("bind", "add", "weird", "-x", "y")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored()["weird"], ["-x", "y"])

    def test_file_is_created_with_documented_content_and_owner_only_mode(self) -> None:
        self.assertFalse(self.config.exists())
        result = self.jzr("bind", "add", "a", "echo", "hi")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            json.loads(self.file.read_text(encoding="utf-8")),
            {"version": 1, "bindings": {"a": ["echo", "hi"]}},
        )
        if POSIX:
            self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_adding_keeps_other_bindings_sorted(self) -> None:
        self.jzr("bind", "add", "zeta", "echo", "z")
        self.jzr("bind", "add", "alpha", "echo", "a")

        self.assertEqual(list(self.stored()), ["alpha", "zeta"])

    def test_names_are_case_sensitive(self) -> None:
        self.jzr("bind", "add", "Tunnel", "echo", "upper")
        result = self.jzr("bind", "add", "tunnel", "echo", "lower")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored(), {"Tunnel": ["echo", "upper"], "tunnel": ["echo", "lower"]})

    def test_valid_name_characters_are_accepted(self) -> None:
        for name in ("9lives", "a.b", "a_b", "a-b", "x1"):
            with self.subTest(name=name):
                result = self.jzr("bind", "add", name, "echo")
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertIn(name, self.stored())

    def test_force_replaces_an_existing_binding(self) -> None:
        self.jzr("bind", "add", "t", "echo", "old")
        for flag in ("--force", "-f"):
            with self.subTest(flag=flag):
                result = self.jzr("bind", "add", flag, "t", "echo", f"new{flag}")

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(self.stored(), {"t": ["echo", f"new{flag}"]})
                self.assertEqual(result.stdout.strip(), f"Replaced t: echo new{flag}")

    def test_force_on_a_new_name_just_binds(self) -> None:
        result = self.jzr("bind", "add", "--force", "fresh", "echo")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Bound fresh: echo")

    def test_force_after_the_name_belongs_to_the_command(self) -> None:
        self.jzr("bind", "add", "t", "echo", "old")
        before = self.file.read_bytes()

        result = self.jzr("bind", "add", "t", "echo", "--force")

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_missing_program_is_noted_on_stderr_but_still_saved(self) -> None:
        result = self.jzr("bind", "add", "ghost", "jzr-no-such-program-xyz", "arg")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("jzr-no-such-program-xyz", result.stderr)
        self.assertEqual(self.stored(), {"ghost": ["jzr-no-such-program-xyz", "arg"]})
        self.assertIn("Bound ghost: jzr-no-such-program-xyz arg", result.stdout)

    def test_an_existing_program_gets_no_note(self) -> None:
        result = self.jzr("bind", "add", "py", sys.executable, "-V")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stderr, "")


class AddRefusalTests(BindTestCase):
    """Refusals are usage errors (exit 2) and change nothing."""

    def assert_refused(self, *args: str, existing: dict[str, list[str]] | None = None) -> str:
        if existing is not None:
            self.write_file(existing)
        before = self.file.read_bytes() if self.file.exists() else None

        result = self.jzr("bind", "add", *args)

        self.assertEqual(result.exit_code, 2, f"{args}: {result.stdout!r} {result.stderr!r}")
        self.assertNotIn("No such command", result.stderr, "`bind add` must exist")
        self.assertIn("Error:", result.stderr)
        after = self.file.read_bytes() if self.file.exists() else None
        self.assertEqual(after, before, "a refused add must not change the file")
        return result.stderr

    def test_builtin_names_are_refused(self) -> None:
        for name in ("bind", "host", "version", "killport", "image", "text", "port"):
            with self.subTest(name=name):
                error = self.assert_refused(name, "echo", "hi")
                self.assertIn(name, error)

    def test_builtin_name_is_refused_even_with_force(self) -> None:
        self.assert_refused("--force", "host", "echo")

    def test_an_existing_name_is_refused_without_force_and_the_old_command_stays(self) -> None:
        error = self.assert_refused("t", "echo", "new", existing={"t": ["echo", "old"]})

        self.assertIn("t", error)
        self.assertEqual(self.stored(), {"t": ["echo", "old"]})

    def test_bad_names_are_refused(self) -> None:
        for name in ("a b", "a/b", ".a", "_a", "a:b", "a$", "é", "", " "):
            with self.subTest(name=name):
                self.assert_refused(name, "echo")

    def test_a_name_starting_with_a_dash_is_refused(self) -> None:
        # After `--` so Click does not treat it as an option of `add`.
        self.assert_refused("--", "-x", "echo")

    def test_an_empty_command_is_refused(self) -> None:
        self.assert_refused("t")
        self.assert_refused("t", "")
        self.assert_refused("t", "   ")

    def test_no_arguments_at_all_is_a_usage_error(self) -> None:
        self.assert_refused()

    def test_a_refused_add_does_not_create_the_file_or_folder(self) -> None:
        self.assert_refused("host", "echo")
        self.assertFalse(self.file.exists())

    def test_unbalanced_quotes_in_a_one_argument_command_change_nothing(self) -> None:
        self.write_file({"keep": ["echo"]})
        before = self.file.read_bytes()

        # A double quote is a quote on both systems; a single quote only on POSIX.
        result = self.jzr("bind", "add", "t", 'echo "oops')

        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn("No such command", result.stderr)
        self.assertIn("quot", result.stderr.lower())
        self.assertEqual(self.file.read_bytes(), before)
        self.assertNotIn("Traceback", result.stderr)


class ListTests(BindTestCase):
    def test_empty_message_when_there_are_no_bindings(self) -> None:
        result = self.jzr("bind", "list")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EMPTY_LIST_MESSAGE)
        self.assertFalse(self.file.exists(), "list must not create the file")

    def test_empty_message_when_the_file_holds_no_bindings(self) -> None:
        self.write_file({})

        result = self.jzr("bind", "list")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EMPTY_LIST_MESSAGE)

    def test_lines_are_sorted_padded_and_shell_quoted(self) -> None:
        self.write_file(
            {
                "zz": ["echo", "hello world"],
                "tunnel": ["ngrok", "http", "5173", "--url", "https://example.dev"],
                "a": ["ls", "-l"],
            }
        )

        result = self.jzr("bind", "list")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "a       ls -l",
                "tunnel  ngrok http 5173 --url https://example.dev",
                f"zz      echo {QUOTED_HELLO_WORLD}",
            ],
        )

    def test_sorting_is_by_name(self) -> None:
        self.write_file({"b": ["x"], "B": ["x"], "a": ["x"], "10": ["x"], "9": ["x"]})

        result = self.jzr("bind", "list")

        names = [line.split()[0] for line in result.stdout.splitlines()]
        self.assertEqual(sorted(names), ["10", "9", "B", "a", "b"])
        self.assertEqual(names, sorted(names))

    def test_a_hidden_binding_is_marked_and_others_are_not(self) -> None:
        self.write_file({"host": ["echo", "mine"], "tunnel": ["ngrok"]})

        result = self.jzr("bind", "list")

        self.assertEqual(result.exit_code, 0, result.stderr)
        lines = {line.split()[0]: line for line in result.stdout.splitlines()}
        self.assertTrue(lines["host"].endswith(HIDDEN_MARK), lines["host"])
        self.assertIn("echo mine", lines["host"])
        self.assertNotIn("hidden", lines["tunnel"])

    def test_json_document_has_path_and_bindings(self) -> None:
        self.write_file({"b": ["echo", "two words"], "a": ["ls"]})

        result = self.jzr("bind", "list", "--json")

        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"path", "bindings"})
        self.assertEqual(document["path"], str(self.file))
        self.assertEqual(document["bindings"], {"a": ["ls"], "b": ["echo", "two words"]})

    def test_json_with_no_bindings_is_still_a_document(self) -> None:
        result = self.jzr("bind", "list", "--json")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"path": str(self.file), "bindings": {}})

    def test_list_never_modifies_the_file(self) -> None:
        self.write_file({"b": ["x"], "a": ["y"]})
        before = self.file.read_bytes()

        self.assertEqual(self.jzr("bind", "list").exit_code, 0)
        self.assertEqual(self.jzr("bind", "list", "--json").exit_code, 0)

        self.assertEqual(self.file.read_bytes(), before)


class RemoveTests(BindTestCase):
    def test_removes_one_and_says_so(self) -> None:
        self.write_file({"a": ["x"], "b": ["y"]})

        result = self.jzr("bind", "remove", "a")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Removed a")
        self.assertEqual(self.stored(), {"b": ["y"]})

    def test_removes_several_names_in_one_call(self) -> None:
        self.write_file({"a": ["x"], "b": ["y"], "c": ["z"]})

        result = self.jzr("bind", "remove", "a", "c")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["Removed a", "Removed c"])
        self.assertEqual(self.stored(), {"b": ["y"]})

    def test_a_missing_name_is_reported_exits_1_and_the_others_are_still_removed(self) -> None:
        self.write_file({"a": ["x"], "b": ["y"]})

        result = self.jzr("bind", "remove", "a", "nope", "b")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("No binding named nope.", result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["Removed a", "Removed b"])
        self.assertEqual(self.stored(), {})

    def test_only_missing_names_exit_1_and_change_nothing(self) -> None:
        self.write_file({"a": ["x"]})
        before = self.file.read_bytes()

        result = self.jzr("bind", "remove", "nope")

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), "No binding named nope.")
        self.assertEqual(self.file.read_bytes(), before)

    def test_remove_with_no_file_exits_1_without_creating_one(self) -> None:
        result = self.jzr("bind", "remove", "a")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("No binding named a.", result.stderr)
        self.assertFalse(self.file.exists())

    def test_removing_the_last_binding_leaves_a_valid_empty_file(self) -> None:
        self.write_file({"a": ["x"]})
        self.jzr("bind", "remove", "a")

        self.assertEqual(
            json.loads(self.file.read_text(encoding="utf-8")), {"version": 1, "bindings": {}}
        )
        self.assertEqual(self.jzr("bind", "list").stdout.strip(), EMPTY_LIST_MESSAGE)

    def test_a_hidden_binding_can_be_removed(self) -> None:
        self.write_file({"host": ["echo"]})

        result = self.jzr("bind", "remove", "host")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.stored(), {})

    def test_no_names_is_a_usage_error(self) -> None:
        result = self.jzr("bind", "remove")
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("No such command", result.stderr)
        self.assertIn("Missing argument", result.stderr)


class PathTests(BindTestCase):
    def test_prints_the_file_path_even_when_it_does_not_exist(self) -> None:
        result = self.jzr("bind", "path")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.file))
        self.assertFalse(self.file.exists())
        self.assertFalse(self.config.exists(), "path must not create the folder")

    def test_path_works_even_when_the_file_is_corrupt(self) -> None:
        self.write_raw(b"not json")

        result = self.jzr("bind", "path")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.file))


class RunDispatchTests(BindTestCase):
    """Arguments handed to the program, seen by patching bindings.run."""

    def run_with_mock(self, *args: str, returns: int = 0):
        with mock.patch("jzr_tools.core.bindings.run", return_value=returns) as fake:
            result = self.jzr(*args)
        return result, fake

    def test_jzr_name_runs_the_stored_arguments(self) -> None:
        self.write_file({"tunnel": ["ngrok", "http", "5173"]})

        result, fake = self.run_with_mock("tunnel")

        self.assertEqual(result.exit_code, 0, result.stderr)
        fake.assert_called_once()
        self.assertEqual(list(fake.call_args.args[0]), ["ngrok", "http", "5173"])

    def test_bind_run_does_the_same(self) -> None:
        self.write_file({"tunnel": ["ngrok", "http", "5173"]})

        result, fake = self.run_with_mock("bind", "run", "tunnel")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(list(fake.call_args.args[0]), ["ngrok", "http", "5173"])

    def test_extra_arguments_are_appended_unchanged(self) -> None:
        self.write_file({"t": ["prog", "--stored", "x"]})
        extra = ["more", "two words", "-h", "--help", "--version", "-x", "--opt=1", "--", "-"]

        for prefix in (("t",), ("bind", "run", "t")):
            with self.subTest(prefix=prefix):
                result, fake = self.run_with_mock(*prefix, *extra)

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(
                    list(fake.call_args.args[0]), ["prog", "--stored", "x", *extra]
                )

    def test_help_flags_after_the_name_are_not_jzrs_help(self) -> None:
        self.write_file({"t": ["prog"]})

        for flag in ("-h", "--help", "--version"):
            with self.subTest(flag=flag):
                result, fake = self.run_with_mock("t", flag)

                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertNotIn("Usage:", result.stdout)
                self.assertEqual(list(fake.call_args.args[0]), ["prog", flag])

    def test_exit_code_of_the_program_is_the_exit_code_of_jzr(self) -> None:
        self.write_file({"t": ["prog"]})

        for code in (0, 1, 3, 42, 130):
            for prefix in (("t",), ("bind", "run", "t")):
                with self.subTest(code=code, prefix=prefix):
                    result, _ = self.run_with_mock(*prefix, returns=code)
                    self.assertEqual(result.exit_code, code)

    def test_a_hidden_binding_is_not_run_by_jzr_name_but_is_by_bind_run(self) -> None:
        self.write_file({"host": ["echo", "mine"]})

        result, fake = self.run_with_mock("host", "-h")
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("Usage:", result.stdout, "the built-in host help must win")
        fake.assert_not_called()

        result, fake = self.run_with_mock("bind", "run", "host", "extra")
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(list(fake.call_args.args[0]), ["echo", "mine", "extra"])

    def test_other_builtins_win_over_bindings_too(self) -> None:
        self.write_file({"version": ["echo", "mine"], "text": ["echo"], "killport": ["echo"]})

        result, fake = self.run_with_mock("version")

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.1.0")
        fake.assert_not_called()

    def test_bind_run_of_an_unknown_name_exits_1(self) -> None:
        self.write_file({"a": ["x"]})

        result, fake = self.run_with_mock("bind", "run", "nope")

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), "No binding named nope.")
        fake.assert_not_called()

    def test_bind_run_with_no_file_exits_1(self) -> None:
        result, fake = self.run_with_mock("bind", "run", "nope")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("No binding named nope.", result.stderr)
        fake.assert_not_called()

    def test_bind_run_without_a_name_is_a_usage_error(self) -> None:
        result = self.jzr("bind", "run")
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("No such command", result.stderr)
        self.assertIn("Missing argument", result.stderr)

    def test_unknown_name_is_clicks_no_such_command(self) -> None:
        self.write_file({"a": ["x"]})

        result, fake = self.run_with_mock("nope", "arg")

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No such command 'nope'", result.stderr)
        fake.assert_not_called()

    def test_unknown_name_with_no_bindings_file_does_not_create_it(self) -> None:
        result = self.jzr("nope")

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No such command", result.stderr)
        self.assertFalse(self.file.exists())
        self.assertFalse(self.config.exists())

    def test_a_typo_of_a_builtin_is_still_no_such_command(self) -> None:
        self.write_file({"a": ["x"]})

        for typo in ("hots", "imag", "bnd"):
            with self.subTest(typo=typo):
                result, fake = self.run_with_mock(typo)
                self.assertEqual(result.exit_code, 2)
                self.assertIn(f"No such command '{typo}'", result.stderr)
                fake.assert_not_called()

    def test_builtin_commands_behave_as_before(self) -> None:
        self.write_file({"a": ["x"]})

        slug = self.jzr("text", "slug", "Hello World")
        self.assertEqual(slug.exit_code, 0, slug.stderr)
        self.assertEqual(slug.stdout.strip(), "hello-world")
        self.assertEqual(self.jzr("--version").stdout.strip(), "0.1.0")
        self.assertEqual(self.jzr("-h").exit_code, 0)

    def test_running_never_modifies_the_file(self) -> None:
        self.write_file({"t": ["prog"]})
        before = self.file.read_bytes()

        self.run_with_mock("t", "x")
        self.run_with_mock("bind", "run", "t")

        self.assertEqual(self.file.read_bytes(), before)

    def test_missing_program_exits_127_with_the_documented_message(self) -> None:
        self.write_file({"ghost": ["jzr-no-such-program-xyz", "a"]})

        for prefix in (("ghost",), ("bind", "run", "ghost")):
            with self.subTest(prefix=prefix):
                result = self.jzr(*prefix, "extra")

                self.assertEqual(result.exit_code, 127)
                self.assertEqual(
                    result.stderr.strip(),
                    "ghost: 'jzr-no-such-program-xyz' was not found on PATH.",
                )

    def test_a_binding_error_text_from_run_is_printed_without_a_traceback(self) -> None:
        self.write_file({"t": ["prog"]})
        from jzr_tools.core import bindings

        # A missing program is its own kind of error (exit 127); any other
        # failure to run is exit 1. Both print the text after the name.
        cases = (
            (bindings.ProgramNotFound("'prog' was not found on PATH."), 127),
            (bindings.BindingError("'prog' could not be started: Permission denied."), 1),
        )
        for error, code in cases:
            with self.subTest(error=str(error)):
                with mock.patch("jzr_tools.core.bindings.run", side_effect=error):
                    result = self.jzr("t")

                self.assertEqual(result.exit_code, code)
                self.assertEqual(result.stderr.strip(), f"t: {error}")


class CorruptFileTests(BindTestCase):
    CASES = {
        "not json": b"definitely not json",
        "wrong shape": b'{"version": 1, "bindings": []}',
        "bad command": b'{"version": 1, "bindings": {"a": []}}',
    }

    def assert_reported(self, result, content: bytes) -> None:
        self.assertEqual(result.exit_code, 1, f"{result.stdout!r} {result.stderr!r}")
        self.assertIn(f"{self.file}: not a valid bindings file: ", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.file.read_bytes(), content, "a corrupt file must never be overwritten")

    def test_every_bind_command_reports_and_leaves_the_file_alone(self) -> None:
        commands = [
            ("bind", "add", "t", "echo"),
            ("bind", "add", "--force", "t", "echo"),
            ("bind", "list"),
            ("bind", "list", "--json"),
            ("bind", "remove", "t"),
            ("bind", "run", "t"),
        ]
        for label, content in self.CASES.items():
            for args in commands:
                with self.subTest(case=label, args=args):
                    self.write_raw(content)
                    self.assert_reported(self.jzr(*args), content)

    def test_jzr_name_reports_for_any_non_builtin_name_even_an_unbound_one(self) -> None:
        for label, content in self.CASES.items():
            for name in ("t", "whatever"):
                with self.subTest(case=label, name=name):
                    self.write_raw(content)
                    self.assert_reported(self.jzr(name, "arg"), content)

    def test_builtins_and_help_still_work_with_a_corrupt_file(self) -> None:
        content = b"definitely not json"
        self.write_raw(content)

        self.assertEqual(self.jzr("version").exit_code, 0)
        self.assertEqual(self.jzr("-h").exit_code, 0)
        self.assertEqual(self.jzr("--version").exit_code, 0)
        self.assertEqual(self.jzr("text", "slug", "A B").stdout.strip(), "a-b")
        self.assertEqual(self.jzr("bind", "-h").exit_code, 0)
        self.assertEqual(self.file.read_bytes(), content)


class RealProgramTests(BindTestCase):
    """End to end through a real `jzr` process and a real child process."""

    def bind_printer(self, name: str = "show", *stored_extra: str) -> None:
        result = self.jzr("bind", "add", name, sys.executable, "-c", ARGV_PRINTER, *stored_extra)
        self.assertEqual(result.exit_code, 0, result.stderr)

    def test_output_exit_code_and_extra_arguments_pass_through(self) -> None:
        self.bind_printer()

        for prefix in (("show",), ("bind", "run", "show")):
            with self.subTest(prefix=prefix):
                proc = self.jzr_process(*prefix, "two words", "--help", "-h", "--version", "$HOME")

                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertEqual(
                    proc.stdout.strip(),
                    repr(["two words", "--help", "-h", "--version", "$HOME"]),
                )
                self.assertNotIn("Traceback", proc.stderr)

    def test_stored_option_looking_arguments_reach_the_program(self) -> None:
        self.bind_printer("keep", "--url", "u", "-p", "80", "a b")

        proc = self.jzr_process("keep", "-q")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["--url", "u", "-p", "80", "a b", "-q"]))

    @unittest.skipUnless(POSIX, "shell-style splitting")
    def test_single_string_form_runs_like_the_multi_argument_form(self) -> None:
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(ARGV_PRINTER)} one 'two words'"
        added = self.jzr("bind", "add", "oneline", command)
        self.assertEqual(added.exit_code, 0, added.stderr)

        proc = self.jzr_process("oneline", "three")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["one", "two words", "three"]))

    def test_no_shell_is_involved(self) -> None:
        self.bind_printer("lit", "&&", "$HOME", "~", "a|b")

        proc = self.jzr_process("lit")

        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(["&&", "$HOME", "~", "a|b"]))

    def test_standard_input_reaches_the_program(self) -> None:
        code = "import sys; print(sys.stdin.read().upper())"
        self.jzr("bind", "add", "shout", sys.executable, "-c", code)

        proc = self.jzr_process("shout", input="quiet")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "QUIET")

    def test_program_stderr_stays_on_stderr(self) -> None:
        code = "import sys; print('bad', file=sys.stderr); sys.exit(4)"
        self.jzr("bind", "add", "noisy", sys.executable, "-c", code)

        proc = self.jzr_process("noisy")

        self.assertEqual(proc.returncode, 4)
        self.assertEqual(proc.stdout, "")
        self.assertIn("bad", proc.stderr)

    @unittest.skipUnless(POSIX, "signal exit codes are POSIX")
    def test_a_program_killed_by_a_signal_gives_128_plus_the_signal(self) -> None:
        code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
        self.jzr("bind", "add", "dies", sys.executable, "-c", code)

        proc = self.jzr_process("dies")

        self.assertEqual(proc.returncode, 128 + 15, proc.stderr)

    def test_missing_program_through_a_real_process(self) -> None:
        self.jzr("bind", "add", "ghost", "jzr-no-such-program-xyz")

        proc = self.jzr_process("ghost")

        self.assertEqual(proc.returncode, 127)
        self.assertEqual(
            proc.stderr.strip(), "ghost: 'jzr-no-such-program-xyz' was not found on PATH."
        )
        self.assertEqual(proc.stdout, "")

    def test_add_list_remove_round_trip_through_real_processes(self) -> None:
        added = self.jzr_process("bind", "add", "t", "echo", "hi")
        self.assertEqual(added.returncode, 0, added.stderr)
        listed = self.jzr_process("bind", "list")
        self.assertEqual(listed.stdout.strip(), "t  echo hi")
        removed = self.jzr_process("bind", "remove", "t")
        self.assertEqual(removed.stdout.strip(), "Removed t")
        self.assertEqual(self.jzr_process("bind", "list").stdout.strip(), EMPTY_LIST_MESSAGE)


@unittest.skipIf(pty is None, "needs a POSIX pseudo-terminal")
class CtrlCTests(BindTestCase):
    CHILD = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGINT, lambda *a: sys.exit(42))\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
        "sys.exit(0)\n"
    )

    @staticmethod
    def _become_session_leader_with_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    @staticmethod
    def _read_until(master: int, proc: subprocess.Popen, stop, timeout: float = 20.0) -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
                if stop(b"".join(chunks).decode("utf-8", "replace")):
                    break
            elif proc.poll() is not None:
                break
        return b"".join(chunks).decode("utf-8", "replace")

    def test_ctrl_c_ends_the_child_and_jzr_exits_with_the_childs_code(self) -> None:
        self.write_file({"sleepy": [sys.executable, "-c", self.CHILD]})
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-m", "jzr_tools", "sleepy"],
            cwd=PROJECT_ROOT,
            env=self.subprocess_env(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            preexec_fn=self._become_session_leader_with_tty,
        )
        os.close(slave)

        def cleanup() -> None:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            try:
                os.close(master)
            except OSError:
                pass

        self.addCleanup(cleanup)

        seen = self._read_until(master, proc, lambda text: "ready" in text)
        self.assertIn("ready", seen, "the bound program never started")

        os.write(master, b"\x03")
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail(f"jzr did not exit after Ctrl+C; output so far: {seen!r}")
        rest = self._read_until(master, proc, lambda text: False, timeout=2.0)
        output = seen + rest

        self.assertEqual(proc.returncode, 42, output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("Aborted", output)
        self.assertNotIn("KeyboardInterrupt", output)


if __name__ == "__main__":
    unittest.main()

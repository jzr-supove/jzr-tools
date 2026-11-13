"""Acceptance tests for jzr_tools.core.bindings (the file, names, splitting, running).

No test touches the real bindings file: every call gets an explicit ``path=`` or
``environ=`` that points into a temporary folder.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

POSIX = os.name == "posix"


def _bindings():
    from jzr_tools.core import bindings

    return bindings


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="jzr-bindings-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = self.tmp / "bindings.json"


class LocationTests(unittest.TestCase):
    def test_jzr_config_dir_wins_over_everything(self) -> None:
        b = _bindings()
        env = {
            "JZR_CONFIG_DIR": "/somewhere/custom",
            "XDG_CONFIG_HOME": "/xdg",
            "APPDATA": "/appdata",
        }
        self.assertEqual(b.config_dir(env), Path("/somewhere/custom"))
        self.assertEqual(b.bindings_path(env), Path("/somewhere/custom") / "bindings.json")

    @unittest.skipUnless(POSIX, "POSIX rule")
    def test_xdg_config_home_gets_a_jzr_subfolder(self) -> None:
        b = _bindings()
        env = {"XDG_CONFIG_HOME": "/xdg"}
        self.assertEqual(b.config_dir(env), Path("/xdg") / "jzr")
        self.assertEqual(b.bindings_path(env), Path("/xdg") / "jzr" / "bindings.json")

    @unittest.skipUnless(POSIX, "POSIX rule")
    def test_default_is_dot_config_jzr_under_home(self) -> None:
        b = _bindings()
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                result = b.config_dir({})
        self.assertEqual(result, Path(home) / ".config" / "jzr")

    @unittest.skipUnless(POSIX, "POSIX rule")
    def test_appdata_is_ignored_off_windows(self) -> None:
        b = _bindings()
        env = {"APPDATA": "/appdata", "XDG_CONFIG_HOME": "/xdg"}
        self.assertEqual(b.config_dir(env), Path("/xdg") / "jzr")

    @unittest.skipUnless(os.name == "nt", "Windows rule; patching os.name on POSIX breaks pathlib")
    def test_appdata_jzr_on_windows(self) -> None:
        b = _bindings()
        env = {"APPDATA": r"C:\\Users\\x\\AppData\\Roaming", "XDG_CONFIG_HOME": r"C:\\xdg"}
        self.assertEqual(b.config_dir(env), Path(env["APPDATA"]) / "jzr")

    def test_environ_defaults_to_the_process_environment(self) -> None:
        b = _bindings()
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": folder}):
                self.assertEqual(b.config_dir(), Path(folder))
                self.assertEqual(b.bindings_path(), Path(folder) / "bindings.json")


class LoadTests(_TempDirCase):
    def test_missing_file_means_no_bindings(self) -> None:
        self.assertEqual(_bindings().load(self.path), {})
        self.assertFalse(self.path.exists(), "load must not create the file")

    def test_valid_file_is_returned(self) -> None:
        self.path.write_text(
            json.dumps(
                {"version": 1, "bindings": {"tunnel": ["ngrok", "http", "5173"], "a": ["ls"]}}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            _bindings().load(self.path),
            {"tunnel": ["ngrok", "http", "5173"], "a": ["ls"]},
        )

    def test_empty_bindings_object_is_valid(self) -> None:
        self.path.write_text('{"version": 1, "bindings": {}}', encoding="utf-8")
        self.assertEqual(_bindings().load(self.path), {})

    def test_load_uses_the_default_path_from_the_environment(self) -> None:
        self.path.write_text('{"version": 1, "bindings": {"x": ["echo"]}}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.tmp)}):
            self.assertEqual(_bindings().load(), {"x": ["echo"]})

    def test_every_kind_of_invalid_file_raises_naming_the_path_and_is_left_alone(self) -> None:
        cases = {
            "not json": b"this is not json",
            "empty file": b"",
            "truncated json": b'{"version": 1, "bindings": {"a": ["ls"',
            "top level list": b'[{"a": ["ls"]}]',
            "top level string": b'"bindings"',
            "bindings missing": b'{"version": 1}',
            "bindings is a list": b'{"version": 1, "bindings": []}',
            "bindings is null": b'{"version": 1, "bindings": null}',
            "command is a string": b'{"version": 1, "bindings": {"a": "ls -l"}}',
            "command is empty list": b'{"version": 1, "bindings": {"a": []}}',
            "command has a number": b'{"version": 1, "bindings": {"a": ["ls", 1]}}',
            "command has null": b'{"version": 1, "bindings": {"a": ["ls", null]}}',
            "command has nested list": b'{"version": 1, "bindings": {"a": [["ls"]]}}',
            "command is an object": b'{"version": 1, "bindings": {"a": {"0": "ls"}}}',
            "not utf-8": b'{"version": 1, "bindings": {"a": ["\xff\xfe"]}}',
        }
        b = _bindings()
        for label, content in cases.items():
            with self.subTest(case=label):
                self.path.write_bytes(content)
                with self.assertRaises(b.BindingError) as caught:
                    b.load(self.path)
                message = str(caught.exception)
                self.assertTrue(
                    message.startswith(f"{self.path}: not a valid bindings file: "), message
                )
                self.assertGreater(len(message), len(f"{self.path}: not a valid bindings file: "))
                self.assertEqual(self.path.read_bytes(), content)


class SaveTests(_TempDirCase):
    def test_writes_the_documented_shape(self) -> None:
        _bindings().save({"tunnel": ["ngrok", "http", "5173"]}, self.path)
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {"version": 1, "bindings": {"tunnel": ["ngrok", "http", "5173"]}},
        )

    def test_file_is_indented_and_names_are_sorted(self) -> None:
        _bindings().save({"zeta": ["z"], "alpha": ["a"], "Mid": ["m"]}, self.path)
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertGreater(len(lines), 5, "expected an indented multi-line document")
        self.assertTrue(any(line.startswith("  ") for line in lines[1:]), text)
        names = list(json.loads(text)["bindings"])
        self.assertEqual(names, sorted(names))
        self.assertLess(text.index('"Mid"'), text.index('"alpha"'))
        self.assertLess(text.index('"alpha"'), text.index('"zeta"'))

    def test_creates_missing_folders(self) -> None:
        target = self.tmp / "deep" / "er" / "bindings.json"
        _bindings().save({"a": ["ls"]}, target)
        self.assertEqual(_bindings().load(target), {"a": ["ls"]})

    def test_round_trips_awkward_arguments(self) -> None:
        data = {
            "odd": ["prog", "two words", "--opt=a b", "quo'te", 'dq"', "ünï", "", "$HOME", "*"],
        }
        b = _bindings()
        b.save(data, self.path)
        self.assertEqual(b.load(self.path), data)

    def test_replaces_an_existing_file_completely(self) -> None:
        b = _bindings()
        b.save({"old": ["a"], "keep": ["b"]}, self.path)
        b.save({"new": ["c"]}, self.path)
        self.assertEqual(b.load(self.path), {"new": ["c"]})

    def test_saving_nothing_writes_a_valid_empty_file(self) -> None:
        b = _bindings()
        b.save({}, self.path)
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), {"version": 1, "bindings": {}}
        )
        self.assertEqual(b.load(self.path), {})

    def test_no_temporary_file_is_left_behind(self) -> None:
        b = _bindings()
        b.save({"a": ["ls"]}, self.path)
        b.save({"a": ["ls"], "b": ["pwd"]}, self.path)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["bindings.json"])

    def test_save_uses_the_default_path_from_the_environment(self) -> None:
        with mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.tmp / "cfg")}):
            _bindings().save({"a": ["ls"]})
        self.assertEqual(_bindings().load(self.tmp / "cfg" / "bindings.json"), {"a": ["ls"]})

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_file_is_readable_and_writable_by_the_owner_only(self) -> None:
        _bindings().save({"a": ["ls"]}, self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_permissions_are_tightened_when_replacing_a_loose_file(self) -> None:
        self.path.write_text('{"version": 1, "bindings": {}}', encoding="utf-8")
        os.chmod(self.path, 0o644)
        _bindings().save({"a": ["ls"]}, self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_failed_replace_leaves_the_old_file_whole(self) -> None:
        b = _bindings()
        b.save({"old": ["a"]}, self.path)
        before = self.path.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk on fire")):
            with self.assertRaises(Exception):
                b.save({"new": ["b"]}, self.path)
        self.assertEqual(self.path.read_bytes(), before)


class IsValidNameTests(unittest.TestCase):
    def test_accepted_names(self) -> None:
        for name in ("a", "7", "tunnel", "Tunnel", "a.b", "a_b", "a-b", "9lives", "x.y-z_1", "A"):
            with self.subTest(name=name):
                self.assertTrue(_bindings().is_valid_name(name))

    def test_rejected_names(self) -> None:
        for name in (
            "",
            " ",
            "-a",
            ".a",
            "_a",
            "a b",
            "a/b",
            "a\\b",
            "a:b",
            "a$",
            "a*",
            "é",
            "caf\u00e9",
            "a\n",
            "--help",
            "a\tb",
        ):
            with self.subTest(name=name):
                self.assertFalse(_bindings().is_valid_name(name))


class SplitCommandTests(unittest.TestCase):
    def test_several_parts_are_returned_unchanged_even_with_spaces(self) -> None:
        parts = ["ngrok", "http 5173", "--url", "https://x.dev/a b", "'q'"]
        self.assertEqual(_bindings().split_command(parts), parts)

    def test_several_parts_are_returned_as_a_list(self) -> None:
        self.assertEqual(_bindings().split_command(("a", "b")), ["a", "b"])

    @unittest.skipUnless(POSIX, "shell-style splitting; Windows splits differently")
    def test_one_part_is_split_like_a_shell_would(self) -> None:
        self.assertEqual(
            _bindings().split_command(["ngrok http 5173 --url https://example.dev"]),
            ["ngrok", "http", "5173", "--url", "https://example.dev"],
        )

    @unittest.skipUnless(POSIX, "shell-style splitting; Windows splits differently")
    def test_quotes_group_words_in_a_single_part(self) -> None:
        self.assertEqual(
            _bindings().split_command(["git commit -m 'two words' \"and more\""]),
            ["git", "commit", "-m", "two words", "and more"],
        )

    def test_one_part_without_spaces_is_one_word(self) -> None:
        self.assertEqual(_bindings().split_command(["htop"]), ["htop"])

    def test_unbalanced_quotes_raise_binding_error(self) -> None:
        b = _bindings()
        for text in ("echo 'oops", 'echo "oops'):
            with self.subTest(text=text):
                with self.assertRaises(b.BindingError):
                    b.split_command([text])

    def test_empty_or_blank_gives_an_empty_list(self) -> None:
        b = _bindings()
        for parts in ([], [""], ["   "], ["\t \n"]):
            with self.subTest(parts=parts):
                self.assertEqual(b.split_command(parts), [])


class DescribeTests(unittest.TestCase):
    @unittest.skipUnless(POSIX, "shell quoting is POSIX-style")
    def test_round_trips_through_shlex_split(self) -> None:
        samples = [
            ["ngrok", "http", "5173", "--url", "https://example.dev"],
            ["echo", "two words", "it's", 'say "hi"', "$HOME", "a;b", "*", "~", "", "tab\there"],
            ["prog", "--opt=a b", "ünï"],
        ]
        for arguments in samples:
            with self.subTest(arguments=arguments):
                text = _bindings().describe(arguments)
                self.assertNotIn("\n", text)
                self.assertEqual(shlex.split(text), arguments)

    def test_plain_words_are_not_decorated(self) -> None:
        self.assertEqual(_bindings().describe(["ngrok", "http", "5173"]), "ngrok http 5173")

    def test_a_word_with_a_space_is_quoted(self) -> None:
        text = _bindings().describe(["echo", "a b"])
        self.assertNotEqual(text, "echo a b")


def _py(code: str, *args: str) -> list[str]:
    return [sys.executable, "-c", code, *args]


class RunTests(unittest.TestCase):
    def test_exit_code_is_passed_through(self) -> None:
        b = _bindings()
        for code in (0, 1, 3, 42):
            with self.subTest(code=code):
                self.assertEqual(b.run(_py(f"import sys; sys.exit({code})")), code)

    def test_arguments_with_spaces_and_option_lookalikes_arrive_intact(self) -> None:
        awkward = ["two words", "--help", "-h", "--version", "-", "--", "a=b c", "$HOME", "*", ""]
        # Compare inside the child so no output capture is needed.
        code = (
            "import sys; "
            f"expected = {awkward!r}; "
            "sys.exit(0 if sys.argv[1:] == expected else 9)"
        )
        self.assertEqual(_bindings().run(_py(code, *awkward)), 0)

    def test_child_output_is_inherited_by_the_parent(self) -> None:
        driver = (
            "import json, sys; from jzr_tools.core import bindings; "
            "sys.exit(bindings.run(json.loads(sys.argv[1])))"
        )
        child = _py("import sys; print('hello from child'); print('oops', file=sys.stderr); sys.exit(5)")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        proc = subprocess.run(
            [sys.executable, "-c", driver, json.dumps(child)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertIn("hello from child", proc.stdout)
        self.assertIn("oops", proc.stderr)

    def test_child_inherits_standard_input(self) -> None:
        driver = (
            "import json, sys; from jzr_tools.core import bindings; "
            "sys.exit(bindings.run(json.loads(sys.argv[1])))"
        )
        child = _py("import sys; print(sys.stdin.read().upper())")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        proc = subprocess.run(
            [sys.executable, "-c", driver, json.dumps(child)],
            env=env,
            input="piped text",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PIPED TEXT", proc.stdout)

    def test_missing_program_raises_binding_error_naming_it(self) -> None:
        b = _bindings()
        with self.assertRaises(b.BindingError) as caught:
            b.run(["jzr-definitely-not-a-program-xyz", "arg"])
        self.assertEqual(
            str(caught.exception),
            "'jzr-definitely-not-a-program-xyz' was not found on PATH.",
        )

    def test_a_shell_is_not_involved(self) -> None:
        # `&&` and `$HOME` are plain arguments, not operators or variables.
        code = "import sys; sys.exit(0 if sys.argv[1:] == ['&&', '$HOME', '~'] else 9)"
        self.assertEqual(_bindings().run(_py(code, "&&", "$HOME", "~")), 0)

    @unittest.skipUnless(POSIX, "signal exit codes are POSIX")
    def test_child_killed_by_a_signal_gives_128_plus_the_signal(self) -> None:
        code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
        self.assertEqual(_bindings().run(_py(code)), 128 + signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()

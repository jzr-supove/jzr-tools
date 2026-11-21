"""CLI behaviour of `jzr text diff`, run in temporary folders."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

ANSI = re.compile(r"\x1b\[[0-9;]*m")
UNDERLINED = re.compile(r"\x1b\[4m([^\x1b]*)\x1b\[")

OLD = "one\ntwo\nthree\n"
NEW = "one\n2\nthree\n"
PLAIN_OLD_NEW = "--- old.txt\n+++ new.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+2\n three\n"
BINARY_TEXT = "{} looks binary; not compared"


class DiffCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-text-diff-")
        self.addCleanup(self._tmp.cleanup)
        self._old_cwd = os.getcwd()
        self.folder = Path(self._tmp.name).resolve()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self._old_cwd)
        # Unknown command names consult the bindings file: keep it off the real one.
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.folder / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("NO_COLOR", None)

    def write(self, name: str, data: bytes | str) -> Path:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def run_diff(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["text", "diff", *args], input=input)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def diff_files(self, old: bytes | str, new: bytes | str, *args: str):
        self.write("old.txt", old)
        self.write("new.txt", new)
        return self.run_diff(*args, "old.txt", "new.txt")


class OutputTests(DiffCliCase):
    def test_small_pair_prints_the_exact_unified_diff(self) -> None:
        result = self.diff_files(OLD, NEW)
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, PLAIN_OLD_NEW)
        self.assertEqual(result.stderr, "")

    def test_header_names_are_the_arguments_as_given(self) -> None:
        self.write("sub/before.txt", "a\n")
        self.write("sub/after.txt", "b\n")
        result = self.run_diff("sub/before.txt", "sub/after.txt")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- sub/before.txt\n+++ sub/after.txt\n@@ -1 +1 @@\n-a\n+b\n",
        )

    def test_equal_files_print_nothing_and_exit_zero(self) -> None:
        result = self.diff_files(OLD, OLD)
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_two_empty_files_are_equal(self) -> None:
        result = self.diff_files("", "")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")

    def test_an_empty_file_against_content(self) -> None:
        result = self.diff_files("", "a\nb\n")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "--- old.txt\n+++ new.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n")

    def test_files_are_not_modified(self) -> None:
        self.diff_files(OLD, NEW)
        self.assertEqual((self.folder / "old.txt").read_bytes(), OLD.encode())
        self.assertEqual((self.folder / "new.txt").read_bytes(), NEW.encode())

    def test_non_ascii_lines(self) -> None:
        result = self.diff_files("héllo\n日本語\n", "héllo\n日本\n")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n héllo\n-日本語\n+日本\n",
        )

    def test_a_byte_order_mark_counts_as_a_difference(self) -> None:
        result = self.diff_files(b"\xef\xbb\xbfa\n", b"a\n")
        self.assert_exit(result, 1)


class ContextTests(DiffCliCase):
    OLD = "a\nb\nc\nd\ne\n"
    NEW = "a\nb\nX\nd\ne\n"

    def test_default_context_is_three_lines(self) -> None:
        result = self.diff_files(self.OLD, self.NEW)
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,5 +1,5 @@\n a\n b\n-c\n+X\n d\n e\n",
        )

    def test_unified_zero_shows_no_context(self) -> None:
        for args in (("-U0",), ("-U", "0"), ("--unified", "0")):
            with self.subTest(args=args):
                result = self.diff_files(self.OLD, self.NEW, *args)
                self.assert_exit(result, 1)
                self.assertEqual(
                    result.stdout, "--- old.txt\n+++ new.txt\n@@ -3 +3 @@\n-c\n+X\n"
                )

    def test_unified_one_shows_one_line_each_side(self) -> None:
        result = self.diff_files(self.OLD, self.NEW, "-U", "1")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -2,3 +2,3 @@\n b\n-c\n+X\n d\n",
        )

    def test_negative_context_is_a_usage_error(self) -> None:
        self.write("old.txt", self.OLD)
        self.write("new.txt", self.NEW)
        result = self.run_diff("-U", "-1", "old.txt", "new.txt")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_a_non_number_context_is_a_usage_error(self) -> None:
        result = self.diff_files(self.OLD, self.NEW, "-U", "many")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")

    def test_far_apart_changes_make_two_hunks(self) -> None:
        old = "".join(f"line {i}\n" for i in range(30))
        new = old.replace("line 2\n", "#2\n").replace("line 27\n", "#27\n")
        result = self.diff_files(old, new)
        self.assert_exit(result, 1)
        self.assertEqual(sum(1 for line in result.stdout.splitlines() if line.startswith("@@")), 2)


class IgnoreSpaceTests(DiffCliCase):
    def test_leading_and_trailing_whitespace_is_ignored_with_w(self) -> None:
        for flag in ("-w", "--ignore-space"):
            with self.subTest(flag=flag):
                result = self.diff_files("a\n  b  \n\tc\n", "a\nb\nc \n", flag)
                self.assert_exit(result, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_whitespace_differs_without_w(self) -> None:
        result = self.diff_files("a\n  b  \n", "a\nb\n")
        self.assert_exit(result, 1)

    def test_inner_whitespace_still_differs_with_w(self) -> None:
        result = self.diff_files("a b\n", "a  b\n", "-w")
        self.assert_exit(result, 1)

    def test_context_lines_keep_the_old_text_with_w(self) -> None:
        result = self.diff_files("  keep  \nx\n", "keep\ny\n", "-w")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout, "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n   keep  \n-x\n+y\n"
        )


class LineEndingTests(DiffCliCase):
    NOTE = (
        "old.txt and new.txt differ only in line endings; "
        "add --strict-eol to compare them.\n"
    )

    def test_crlf_against_lf_is_equal_with_a_note_on_stderr(self) -> None:
        result = self.diff_files("a\r\nb\r\n", "a\nb\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, self.NOTE)

    def test_the_note_names_the_files_as_given(self) -> None:
        self.write("x/old.txt", "a\r\n")
        self.write("x/new.txt", "a\n")
        result = self.run_diff("x/old.txt", "x/new.txt")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stderr,
            "x/old.txt and x/new.txt differ only in line endings; "
            "add --strict-eol to compare them.\n",
        )

    def test_lf_against_crlf_gives_the_same_note(self) -> None:
        result = self.diff_files("a\nb\n", "a\r\nb\r\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, self.NOTE)

    def test_strict_eol_reports_the_difference(self) -> None:
        result = self.diff_files("a\r\nb\r\n", "a\nb\n", "--strict-eol")
        self.assert_exit(result, 1)
        self.assertTrue(result.stdout.startswith("--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n"))
        # Result.stdout turns "\r\n" into "\n"; the raw bytes keep the carriage return.
        self.assertIn(b"-a\r\n", result.stdout_bytes)
        self.assertIn(b"+a\n", result.stdout_bytes)
        self.assertEqual(result.stderr, "")

    def test_a_real_difference_beside_line_endings_has_no_note(self) -> None:
        result = self.diff_files("a\r\nb\r\n", "a\nc\n")
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout_bytes, b"--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n a\r\n-b\r\n+c\n"
        )

    def test_identical_crlf_files_have_no_note(self) -> None:
        result = self.diff_files("a\r\n", "a\r\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")

    def test_whitespace_only_equality_has_no_line_ending_note(self) -> None:
        result = self.diff_files("a\n  b  \n", "a\nb\n", "-w")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")


class MissingNewlineTests(DiffCliCase):
    def test_missing_final_newline_is_marked(self) -> None:
        result = self.diff_files("a\nb", "a\nb\n")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n a\n-b\n"
            "\\ No newline at end of file\n+b\n",
        )

    def test_missing_on_the_new_side(self) -> None:
        result = self.diff_files("a\nb\n", "a\nc")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
            "\\ No newline at end of file\n",
        )

    def test_unterminated_context_line_gets_the_note(self) -> None:
        result = self.diff_files("x\nb", "y\nb")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n-x\n+y\n b\n"
            "\\ No newline at end of file\n",
        )


class ColourTests(DiffCliCase):
    EDIT_OLD = "keep\nalpha one two three\n"
    EDIT_NEW = "keep\nalpha ONE two three\n"

    def colour_lines(self, *args: str) -> dict[str, str]:
        result = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, *args)
        self.assert_exit(result, 1)
        lines = result.stdout.split("\n")
        found = {}
        for line in lines:
            plain = ANSI.sub("", line)
            if plain.startswith("--- "):
                found["old_header"] = line
            elif plain.startswith("+++ "):
                found["new_header"] = line
            elif plain.startswith("@@"):
                found["hunk"] = line
            elif plain.startswith("-"):
                found["removed"] = line
            elif plain.startswith("+"):
                found["added"] = line
            elif plain.startswith(" "):
                found["context"] = line
        return found

    def test_never_has_no_escape_codes(self) -> None:
        result = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, "--color", "never")
        self.assert_exit(result, 1)
        self.assertNotIn("\x1b", result.stdout)
        self.assertEqual(
            result.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,2 +1,2 @@\n keep\n"
            "-alpha one two three\n+alpha ONE two three\n",
        )

    def test_always_colours_each_kind_of_line(self) -> None:
        found = self.colour_lines("--color", "always")
        self.assertIn("\x1b[1m", found["old_header"])
        self.assertIn("\x1b[1m", found["new_header"])
        self.assertIn("\x1b[36m", found["hunk"])
        self.assertIn("\x1b[31m", found["removed"])
        self.assertIn("\x1b[32m", found["added"])
        self.assertEqual(ANSI.sub("", found["context"]), " keep")
        self.assertNotRegex(found["context"], r"\x1b\[(?!0?m)")

    def test_always_text_is_the_plain_diff_once_codes_are_removed(self) -> None:
        coloured = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, "--color", "always")
        plain = self.run_diff("--color", "never", "old.txt", "new.txt")
        self.assertIn("\x1b[", coloured.stdout)
        self.assertEqual(ANSI.sub("", coloured.stdout), plain.stdout)

    def test_always_underlines_the_changed_span_of_an_edited_pair(self) -> None:
        found = self.colour_lines("--color", "always")
        self.assertEqual(UNDERLINED.findall(found["removed"]), ["one"])
        self.assertEqual(UNDERLINED.findall(found["added"]), ["ONE"])

    def test_unpaired_lines_are_not_underlined(self) -> None:
        result = self.diff_files("abcdefgh\n", "uvwxyz12\n", "--color", "always")
        self.assert_exit(result, 1)
        self.assertIn("\x1b[31m", result.stdout)
        self.assertIn("\x1b[32m", result.stdout)
        self.assertNotIn("\x1b[4m", result.stdout)

    def test_always_with_no_differences_prints_nothing(self) -> None:
        result = self.diff_files("a\n", "a\n", "--color", "always")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")

    def test_auto_does_not_colour_when_stdout_is_not_a_terminal(self) -> None:
        for args in ((), ("--color", "auto")):
            with self.subTest(args=args):
                result = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, *args)
                self.assert_exit(result, 1)
                self.assertNotIn("\x1b", result.stdout)

    def test_no_color_variable_keeps_auto_plain(self) -> None:
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            result = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, "--color", "auto")
        self.assert_exit(result, 1)
        self.assertNotIn("\x1b", result.stdout)

    def test_always_colours_even_when_no_color_is_set(self) -> None:
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            result = self.diff_files(self.EDIT_OLD, self.EDIT_NEW, "--color", "always")
        self.assert_exit(result, 1)
        self.assertIn("\x1b[31m", result.stdout)

    def test_unknown_colour_mode_is_a_usage_error(self) -> None:
        result = self.diff_files("a\n", "b\n", "--color", "sometimes")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")


class StdinTests(DiffCliCase):
    def test_dash_reads_the_old_side_from_stdin(self) -> None:
        self.write("new.txt", NEW)
        result = self.run_diff("-", "new.txt", input=OLD)
        self.assert_exit(result, 1)
        self.assertIn("@@ -1,3 +1,3 @@\n one\n-two\n+2\n three\n", result.stdout)

    def test_dash_reads_the_new_side_from_stdin(self) -> None:
        self.write("old.txt", OLD)
        result = self.run_diff("old.txt", "-", input=NEW)
        self.assert_exit(result, 1)
        self.assertIn("@@ -1,3 +1,3 @@\n one\n-two\n+2\n three\n", result.stdout)

    def test_equal_content_from_stdin_exits_zero(self) -> None:
        self.write("old.txt", OLD)
        result = self.run_diff("old.txt", "-", input=OLD)
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")

    def test_stdin_bytes_are_decoded_as_utf8(self) -> None:
        self.write("old.txt", "日本語\n")
        result = self.run_diff("old.txt", "-", input="日本\n".encode("utf-8"))
        self.assert_exit(result, 1)
        self.assertIn("-日本語\n+日本\n", result.stdout)

    def test_two_dashes_is_a_usage_error(self) -> None:
        result = self.run_diff("-", "-", input="a\n")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")


class ErrorTests(DiffCliCase):
    def assert_error(self, result, *lines: str) -> None:
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "", self.describe(result))
        self.assertEqual(result.stderr.splitlines(), list(lines), self.describe(result))
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_file(self) -> None:
        self.write("new.txt", "a\n")
        self.assert_error(self.run_diff("missing.txt", "new.txt"), "missing.txt: not found")

    def test_missing_new_file(self) -> None:
        self.write("old.txt", "a\n")
        self.assert_error(self.run_diff("old.txt", "gone.txt"), "gone.txt: not found")

    def test_folder(self) -> None:
        (self.folder / "dir").mkdir()
        self.write("new.txt", "a\n")
        self.assert_error(self.run_diff("dir", "new.txt"), "dir: is a folder")

    def test_file_with_a_nul_byte_is_binary(self) -> None:
        self.write("bin.dat", b"a\x00b\n")
        self.write("new.txt", "a\n")
        self.assert_error(
            self.run_diff("bin.dat", "new.txt"), BINARY_TEXT.format("bin.dat")
        )

    def test_file_that_is_not_utf8_is_binary(self) -> None:
        self.write("latin.txt", b"caf\xe9\n")
        self.write("new.txt", "café\n")
        self.assert_error(
            self.run_diff("new.txt", "latin.txt"), BINARY_TEXT.format("latin.txt")
        )

    def test_two_failing_files_give_two_lines(self) -> None:
        self.write("bin.dat", b"\x00\x01")
        self.assert_error(
            self.run_diff("bin.dat", "missing.txt"),
            BINARY_TEXT.format("bin.dat"),
            "missing.txt: not found",
        )

    def test_two_binary_files_give_two_lines(self) -> None:
        self.write("a.bin", b"\x00")
        self.write("b.bin", b"\xff\xfe")
        self.assert_error(
            self.run_diff("a.bin", "b.bin"),
            BINARY_TEXT.format("a.bin"),
            BINARY_TEXT.format("b.bin"),
        )

    def test_a_failing_file_beats_a_difference(self) -> None:
        self.write("old.txt", "a\n")
        self.write("bad.bin", b"\x00")
        result = self.run_diff("old.txt", "bad.bin")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")

    def test_missing_arguments_is_a_usage_error(self) -> None:
        self.write("old.txt", "a\n")
        result = self.run_diff("old.txt")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")


class DiffLineEndingTests(unittest.TestCase):
    """Output redirected to a pipe must use LF on every platform."""

    def run_program(self, folder: str, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", "text", "diff", *args],
            cwd=folder,
            capture_output=True,
            env={**os.environ, "JZR_CONFIG_DIR": folder},
        )

    def test_redirected_output_has_no_carriage_returns_added(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "old.txt").write_bytes("héllo\ntwo\nthree\n".encode("utf-8"))
            Path(folder, "new.txt").write_bytes("héllo\n2\nthree\n".encode("utf-8"))
            completed = self.run_program(folder, "old.txt", "new.txt")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertNotIn(b"\r", completed.stdout)
        self.assertEqual(
            completed.stdout,
            "--- old.txt\n+++ new.txt\n@@ -1,3 +1,3 @@\n héllo\n-two\n+2\n three\n".encode(
                "utf-8"
            ),
        )

    def test_equal_files_exit_zero_with_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a.txt").write_bytes(b"same\n")
            Path(folder, "b.txt").write_bytes(b"same\n")
            completed = self.run_program(folder, "a.txt", "b.txt")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, b"")

    def test_plain_output_through_a_pipe_has_no_colour(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "old.txt").write_bytes(b"a\n")
            Path(folder, "new.txt").write_bytes(b"b\n")
            completed = self.run_program(folder, "old.txt", "new.txt")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertNotIn(b"\x1b", completed.stdout)

    def test_error_exit_code_is_two(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a.txt").write_bytes(b"x\n")
            completed = self.run_program(folder, "a.txt", "nope.txt")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stdout, b"")
        self.assertIn(b"nope.txt: not found", completed.stderr)


if __name__ == "__main__":
    unittest.main()

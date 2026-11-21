"""CLI behaviour of `jzr data json`, run in temporary folders."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

PRETTY_A1 = '{\n  "a": 1\n}\n'


class JsonCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-data-json-")
        self.addCleanup(self._tmp.cleanup)
        self._old_cwd = os.getcwd()
        self.folder = Path(self._tmp.name).resolve()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self._old_cwd)
        # Unknown command names consult the bindings file: keep it off the real one.
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.folder / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name: str, data: bytes | str) -> Path:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def run_json(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["data", "json", *args], input=input)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def assert_usage_error(self, result, *mentions: str) -> None:
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "", self.describe(result))
        for text in mentions:
            self.assertIn(text, result.stderr, self.describe(result))

    def names(self) -> set[str]:
        return {p.name for p in self.folder.iterdir() if p.name != ".config"}


class JsonPrintTests(JsonCliCase):
    def test_stdin_is_pretty_printed_with_two_spaces(self) -> None:
        result = self.run_json(input='{"a":1,"b":[1,2],"c":{}}')
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ],\n  "c": {}\n}\n',
        )
        self.assertEqual(result.stderr, "")

    def test_a_dash_reads_stdin(self) -> None:
        result = self.run_json("-", input='{"a":1}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, PRETTY_A1)

    def test_output_ends_with_exactly_one_newline(self) -> None:
        for text in ('{"a":1}', '{"a":1}\n', '{"a":1}\n\n\n', '{"a":1}\r\n'):
            with self.subTest(text=text):
                result = self.run_json(input=text)
                self.assert_exit(result, 0)
                self.assertTrue(result.stdout.endswith("}\n"))
                self.assertFalse(result.stdout.endswith("\n\n"))

    def test_a_file_is_pretty_printed(self) -> None:
        self.write("a.json", '{"a":1}')
        result = self.run_json("a.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, PRETTY_A1)
        self.assertEqual(result.stderr, "")

    def test_files_print_one_after_another_in_the_order_given(self) -> None:
        self.write("one.json", "[1]")
        self.write("two.json", '{"b":2}')
        self.write("three.json", "3")
        result = self.run_json("two.json", "one.json", "three.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n  "b": 2\n}\n[\n  1\n]\n3\n')

    def test_a_file_and_stdin_can_be_mixed(self) -> None:
        self.write("one.json", "[1]")
        result = self.run_json("one.json", "-", input='{"a":1}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "[\n  1\n]\n" + PRETTY_A1)

    def test_a_file_with_a_bom_is_read(self) -> None:
        self.write("bom.json", b"\xef\xbb\xbf" + b'{"a":1}')
        result = self.run_json("bom.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, PRETTY_A1)

    def test_non_ascii_text_is_printed_as_it_is(self) -> None:
        self.write("u.json", '{"name":"Zoë 日本語"}'.encode("utf-8"))
        result = self.run_json("u.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n  "name": "Zoë 日本語"\n}\n')

    def test_numbers_and_escapes_and_duplicate_keys_are_kept(self) -> None:
        self.write(
            "k.json",
            r'{"n":1.10,"big":123456789012345678901234567890,"e":1e400,"s":"é\/","n":2}',
        )
        result = self.run_json("--minify", "k.json")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            r'{"n":1.10,"big":123456789012345678901234567890,"e":1e400,"s":"é\/","n":2}' + "\n",
        )


class JsonStyleOptionTests(JsonCliCase):
    def test_indent_4(self) -> None:
        result = self.run_json("--indent", "4", input='{"a":[1]}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n    "a": [\n        1\n    ]\n}\n')

    def test_indent_0_has_line_breaks_only(self) -> None:
        result = self.run_json("--indent", "0", input='{"a":[1]}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n"a": [\n1\n]\n}\n')

    def test_indent_8_is_the_largest_accepted(self) -> None:
        result = self.run_json("--indent", "8", input="[1]")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "[\n        1\n]\n")

    def test_indent_out_of_range_is_a_usage_error(self) -> None:
        for value in ("9", "-1", "two"):
            with self.subTest(value=value):
                result = self.run_json("--indent", value, input="[1]")
                self.assert_usage_error(result, "--indent")

    def test_tab(self) -> None:
        result = self.run_json("--tab", input='{"a":[1]}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n\t"a": [\n\t\t1\n\t]\n}\n')

    def test_minify(self) -> None:
        result = self.run_json("--minify", input='{ "a": 1,\n "b": [ 1, 2 ] }')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{"a":1,"b":[1,2]}\n')

    def test_sort_keys(self) -> None:
        result = self.run_json("--sort-keys", input='{"b":{"y":1,"x":2},"a":0}')
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout, '{\n  "a": 0,\n  "b": {\n    "x": 2,\n    "y": 1\n  }\n}\n'
        )

    def test_sort_keys_orders_by_the_decoded_key_and_keeps_duplicates(self) -> None:
        result = self.run_json("--sort-keys", "--minify", input=r'{"b":1,"b":2,"a":3,"a":4}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, r'{"a":3,"a":4,"b":1,"b":2}' + "\n")

    def test_without_sort_keys_the_order_is_kept(self) -> None:
        result = self.run_json("--minify", input='{"b":1,"a":2}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{"b":1,"a":2}\n')

    def test_options_combine(self) -> None:
        result = self.run_json("--minify", "--sort-keys", input='{"b":1,"a":[{"d":1,"c":2}]}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{"a":[{"c":2,"d":1}],"b":1}\n')
        result = self.run_json("--tab", "--sort-keys", input='{"b":1,"a":2}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{\n\t"a": 2,\n\t"b": 1\n}\n')

    def test_indent_2_given_explicitly_is_the_default(self) -> None:
        result = self.run_json("--indent", "2", input='{"a":1}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, PRETTY_A1)


class JsonUsageConflictTests(JsonCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.json", '{"a":1}')

    def test_tab_with_minify(self) -> None:
        self.assert_usage_error(self.run_json("--tab", "--minify", "a.json"), "--tab", "--minify")

    def test_indent_with_minify(self) -> None:
        self.assert_usage_error(
            self.run_json("--indent", "4", "--minify", "a.json"), "--indent", "--minify"
        )

    def test_indent_with_tab(self) -> None:
        self.assert_usage_error(
            self.run_json("--indent", "4", "--tab", "a.json"), "--indent", "--tab"
        )

    def test_check_with_write(self) -> None:
        self.assert_usage_error(self.run_json("--check", "--write", "a.json"), "--check", "--write")
        self.assertEqual((self.folder / "a.json").read_bytes(), b'{"a":1}')

    def test_write_without_files(self) -> None:
        self.assert_usage_error(self.run_json("--write", input='{"a":1}'), "--write")

    def test_write_with_a_dash(self) -> None:
        result = self.run_json("--write", "-", input='{"a":1}')
        self.assert_usage_error(result, "--write")

    def test_write_with_a_dash_among_files_changes_nothing(self) -> None:
        result = self.run_json("--write", "a.json", "-", input='{"a":1}')
        self.assert_usage_error(result, "--write")
        self.assertEqual((self.folder / "a.json").read_bytes(), b'{"a":1}')

    def test_python_with_write(self) -> None:
        self.write("p.py", "{'a': 1}")
        result = self.run_json("--python", "--write", "p.py")
        self.assert_usage_error(result, "--python", "--write")
        self.assertEqual((self.folder / "p.py").read_bytes(), b"{'a': 1}")


class JsonErrorTests(JsonCliCase):
    def test_an_invalid_file_is_named_with_line_column_and_reason(self) -> None:
        self.write("bad.json", '{"a":1,\n  "b":}\n')
        result = self.run_json("bad.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.splitlines(), ["bad.json: invalid JSON at line 2 column 7: expected a value"]
        )

    def test_the_name_is_printed_as_given(self) -> None:
        self.write("sub/bad.json", "[1,]")
        result = self.run_json("sub/bad.json")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(), ["sub/bad.json: invalid JSON at line 1 column 4: trailing comma"]
        )

    def test_other_files_are_still_printed_and_the_exit_is_1(self) -> None:
        self.write("one.json", '{"a":1}')
        self.write("bad.json", "[1 2]")
        self.write("two.json", "[2]")
        result = self.run_json("one.json", "bad.json", "two.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, PRETTY_A1 + "[\n  2\n]\n")
        self.assertEqual(
            result.stderr.splitlines(),
            ["bad.json: invalid JSON at line 1 column 4: expected ',' or ']'"],
        )

    def test_stdin_is_named_stdin(self) -> None:
        result = self.run_json(input='{"a" 1}')
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.splitlines(), ["<stdin>: invalid JSON at line 1 column 6: expected ':'"]
        )

    def test_empty_stdin_is_an_error(self) -> None:
        result = self.run_json(input="")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(), ["<stdin>: invalid JSON at line 1 column 1: the input is empty"]
        )

    def test_a_dash_is_named_stdin(self) -> None:
        result = self.run_json("-", input="[,]")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(), ["<stdin>: invalid JSON at line 1 column 2: expected a value"]
        )

    def test_a_missing_file_fails_alone(self) -> None:
        self.write("here.json", "[1]")
        result = self.run_json("gone.json", "here.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "[\n  1\n]\n")
        self.assertEqual(result.stderr.splitlines(), ["gone.json: not found"])

    def test_a_folder_fails_alone(self) -> None:
        (self.folder / "dir").mkdir()
        self.write("here.json", "[1]")
        result = self.run_json("dir", "here.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "[\n  1\n]\n")
        self.assertEqual(result.stderr.splitlines(), ["dir: is a folder"])

    def test_a_file_that_is_not_utf8_fails_alone(self) -> None:
        self.write("latin.json", b'["caf\xe9"]')
        self.write("here.json", "[1]")
        result = self.run_json("latin.json", "here.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "[\n  1\n]\n")
        self.assertEqual(result.stderr.splitlines(), ["latin.json: not UTF-8 text"])

    def test_errors_are_one_plain_line_without_a_traceback(self) -> None:
        self.write("bad.json", "{")
        result = self.run_json("bad.json")
        self.assert_exit(result, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, self.describe(result))
        self.assertNotIn("Traceback", result.stderr)

    def test_the_input_is_not_repeated_in_the_message(self) -> None:
        self.write("secret.json", '{"token": "SECRETMARK" SECRETMARK}')
        result = self.run_json("secret.json")
        self.assert_exit(result, 1)
        self.assertNotIn("SECRETMARK", result.stderr)
        self.assertNotIn("SECRETMARK", result.stdout)


class JsonCheckTests(JsonCliCase):
    def test_valid_input_prints_nothing_and_exits_0(self) -> None:
        self.write("a.json", '{"a":1.10}')
        self.write("b.json", "[1, 2]")
        for args in (("a.json", "b.json"), ()):
            with self.subTest(args=args):
                result = self.run_json("--check", *args, input='{"a":1}')
                self.assert_exit(result, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_invalid_input_reports_and_exits_1(self) -> None:
        self.write("good.json", "[1]")
        self.write("bad.json", '{"a":}')
        result = self.run_json("--check", "good.json", "bad.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.splitlines(), ["bad.json: invalid JSON at line 1 column 6: expected a value"]
        )

    def test_stdin_errors_are_named_stdin(self) -> None:
        result = self.run_json("--check", input="[1,")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(),
            ["<stdin>: invalid JSON at line 1 column 4: unexpected end of input"],
        )

    def test_a_missing_file_is_reported(self) -> None:
        result = self.run_json("--check", "gone.json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.splitlines(), ["gone.json: not found"])

    def test_check_never_modifies_files(self) -> None:
        path = self.write("a.json", '{"a":1}')
        self.run_json("--check", "a.json")
        self.assertEqual(path.read_bytes(), b'{"a":1}')


class JsonWriteTests(JsonCliCase):
    def test_an_unformatted_file_is_rewritten(self) -> None:
        path = self.write("a.json", '{"a":1}\n')
        result = self.run_json("--write", "a.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines(), ["a.json: formatted"])
        self.assertEqual(result.stderr, "")
        self.assertEqual(path.read_bytes(), PRETTY_A1.encode())

    def test_short_option_is_the_same_as_write(self) -> None:
        path = self.write("a.json", '{"a":1}\n')
        result = self.run_json("-w", "a.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines(), ["a.json: formatted"])
        self.assertEqual(path.read_bytes(), PRETTY_A1.encode())

    def test_nothing_goes_to_stdout_but_the_status_line(self) -> None:
        self.write("a.json", '{"a":1}\n')
        result = self.run_json("--write", "a.json")
        self.assertNotIn("{", result.stdout)

    def test_a_formatted_file_is_left_untouched(self) -> None:
        path = self.write("a.json", PRETTY_A1)
        os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        before = path.stat().st_mtime_ns
        result = self.run_json("--write", "a.json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines(), ["a.json: already formatted"])
        self.assertEqual(path.read_bytes(), PRETTY_A1.encode())
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_writing_twice_is_stable(self) -> None:
        path = self.write("a.json", '{"a":[1,2]}')
        first = self.run_json("--write", "a.json")
        self.assertEqual(first.stdout.splitlines(), ["a.json: formatted"])
        content = path.read_bytes()
        second = self.run_json("--write", "a.json")
        self.assert_exit(second, 0)
        self.assertEqual(second.stdout.splitlines(), ["a.json: already formatted"])
        self.assertEqual(path.read_bytes(), content)

    def test_a_file_without_a_final_newline_stays_without_one(self) -> None:
        path = self.write("a.json", '{"a":1}')
        self.run_json("--write", "a.json")
        self.assertEqual(path.read_bytes(), b'{\n  "a": 1\n}')

    def test_an_already_formatted_file_without_a_final_newline_is_untouched(self) -> None:
        path = self.write("a.json", PRETTY_A1.rstrip("\n"))
        result = self.run_json("--write", "a.json")
        self.assertEqual(result.stdout.splitlines(), ["a.json: already formatted"])
        self.assertEqual(path.read_bytes(), PRETTY_A1.rstrip("\n").encode())

    def test_a_file_with_a_final_newline_keeps_exactly_one(self) -> None:
        path = self.write("a.json", '{"a":1}\n\n\n')
        self.run_json("--write", "a.json")
        self.assertEqual(path.read_bytes(), PRETTY_A1.encode())

    def test_a_crlf_file_stays_crlf(self) -> None:
        path = self.write("a.json", b'{"a":[1,2]}\r\n')
        result = self.run_json("--write", "a.json")
        self.assert_exit(result, 0)
        self.assertEqual(path.read_bytes(), b'{\r\n  "a": [\r\n    1,\r\n    2\r\n  ]\r\n}\r\n')

    def test_a_crlf_file_without_a_final_newline_stays_crlf_and_without_one(self) -> None:
        path = self.write("a.json", b'{"a":\r\n1}')
        self.run_json("--write", "a.json")
        self.assertEqual(path.read_bytes(), b'{\r\n  "a": 1\r\n}')

    def test_a_formatted_crlf_file_is_already_formatted(self) -> None:
        data = b'{\r\n  "a": 1\r\n}\r\n'
        path = self.write("a.json", data)
        result = self.run_json("--write", "a.json")
        self.assertEqual(result.stdout.splitlines(), ["a.json: already formatted"])
        self.assertEqual(path.read_bytes(), data)

    def test_a_file_with_any_crlf_is_written_with_crlf_only(self) -> None:
        path = self.write("a.json", b'{"a":\r\n[1,\n2]}\n')
        self.run_json("--write", "a.json")
        data = path.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertTrue(data.endswith(b"\r\n"))

    def test_an_lf_file_stays_lf(self) -> None:
        path = self.write("a.json", '{"a":[1]}\n')
        self.run_json("--write", "a.json")
        self.assertNotIn(b"\r", path.read_bytes())

    def test_a_bom_is_kept(self) -> None:
        path = self.write("a.json", b'\xef\xbb\xbf{"a":1}\n')
        result = self.run_json("--write", "a.json")
        self.assert_exit(result, 0)
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbf" + PRETTY_A1.encode())

    def test_a_formatted_file_with_a_bom_is_already_formatted(self) -> None:
        data = b"\xef\xbb\xbf" + PRETTY_A1.encode()
        path = self.write("a.json", data)
        result = self.run_json("--write", "a.json")
        self.assertEqual(result.stdout.splitlines(), ["a.json: already formatted"])
        self.assertEqual(path.read_bytes(), data)

    def test_a_file_without_a_bom_gets_none(self) -> None:
        path = self.write("a.json", '{"a":1}')
        self.run_json("--write", "a.json")
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_the_style_options_apply(self) -> None:
        path = self.write("a.json", '{"b":1,"a":[1]}\n')
        self.run_json("--write", "--tab", "--sort-keys", "a.json")
        self.assertEqual(path.read_bytes(), b'{\n\t"a": [\n\t\t1\n\t],\n\t"b": 1\n}\n')
        self.run_json("--write", "--minify", "a.json")
        self.assertEqual(path.read_bytes(), b'{"a":[1],"b":1}\n')

    def test_an_invalid_file_is_left_byte_for_byte_and_others_are_written(self) -> None:
        bad = self.write("bad.json", b'\xef\xbb\xbf{"a":1,}\r\n')
        good = self.write("good.json", "[1]\n")
        result = self.run_json("--write", "bad.json", "good.json")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(),
            ["bad.json: invalid JSON at line 1 column 8: trailing comma"],
        )
        self.assertEqual(result.stdout.splitlines(), ["good.json: formatted"])
        self.assertEqual(bad.read_bytes(), b'\xef\xbb\xbf{"a":1,}\r\n')
        self.assertEqual(good.read_bytes(), b"[\n  1\n]\n")

    def test_missing_folder_and_non_utf8_fail_alone(self) -> None:
        (self.folder / "dir").mkdir()
        latin = self.write("latin.json", b'["caf\xe9"]')
        good = self.write("good.json", "[1]")
        result = self.run_json("--write", "gone.json", "dir", "latin.json", "good.json")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(),
            ["gone.json: not found", "dir: is a folder", "latin.json: not UTF-8 text"],
        )
        self.assertEqual(result.stdout.splitlines(), ["good.json: formatted"])
        self.assertEqual(latin.read_bytes(), b'["caf\xe9"]')
        self.assertEqual(good.read_bytes(), b"[\n  1\n]")

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.write("a.json", '{"a":1}\n')
        self.write("b.json", PRETTY_A1)
        self.write("bad.json", "{")
        before = self.names()
        self.run_json("--write", "a.json", "b.json", "bad.json")
        self.assertEqual(self.names(), before)

    def test_a_failure_while_replacing_leaves_the_file_and_folder_as_they_were(self) -> None:
        path = self.write("a.json", '{"a":1}\n')
        before = self.names()
        with mock.patch("os.replace", side_effect=OSError("disk trouble")):
            result = self.run_json("--write", "a.json")
        self.assert_exit(result, 1)
        self.assertEqual(path.read_bytes(), b'{"a":1}\n')
        self.assertEqual(self.names(), before)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.startswith("a.json:"), self.describe(result))

    @unittest.skipIf(os.name == "nt", "POSIX file modes only")
    def test_the_file_mode_is_kept(self) -> None:
        path = self.write("a.json", '{"a":1}\n')
        os.chmod(path, 0o640)
        self.run_json("--write", "a.json")
        self.assertEqual(path.read_bytes(), PRETTY_A1.encode())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)


class JsonPythonTests(JsonCliCase):
    def test_stdin_literal_becomes_json(self) -> None:
        result = self.run_json("--python", input="{'a': True, 'b': None, 'c': (1, 'x')}")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            '{\n  "a": true,\n  "b": null,\n  "c": [\n    1,\n    "x"\n  ]\n}\n',
        )

    def test_a_file_literal_becomes_json(self) -> None:
        self.write("p.txt", "{'a': False, 'b': [1, 2.5]}\n")
        result = self.run_json("--python", "p.txt")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout, '{\n  "a": false,\n  "b": [\n    1,\n    2.5\n  ]\n}\n'
        )

    def test_style_options_apply(self) -> None:
        result = self.run_json("--python", "--minify", "--sort-keys", input="{'b': 1, 'a': (2,)}")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, '{"a":[2],"b":1}\n')

    def test_a_repr_with_a_call_is_an_error_naming_the_input(self) -> None:
        self.write("p.txt", "{'when': datetime(2020, 1, 1)}")
        result = self.run_json("--python", "p.txt")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "p.txt: not a Python literal: only strings, numbers, True, False, None, "
                "lists, tuples and dicts can be read."
            ],
        )

    def test_stdin_errors_are_named_stdin(self) -> None:
        result = self.run_json("--python", input="{(1, 2): 3}")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stderr.splitlines(),
            ["<stdin>: a dict key must be a string, number, True, False or None."],
        )

    def test_empty_input_is_an_error(self) -> None:
        result = self.run_json("--python", input="")
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr.splitlines(), ["<stdin>: the input is empty"])

    def test_a_syntax_error_has_a_position(self) -> None:
        result = self.run_json("--python", input="{'a': }")
        self.assert_exit(result, 1)
        self.assertRegex(
            result.stderr, r"^<stdin>: not a Python literal at line 1 column \d+: .+\n$"
        )

    def test_one_bad_file_does_not_stop_the_others(self) -> None:
        self.write("bad.txt", "foo")
        self.write("good.txt", "(1,)")
        result = self.run_json("--python", "bad.txt", "good.txt")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "[\n  1\n]\n")
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_json_text_is_not_read_as_python_unless_it_is_a_literal(self) -> None:
        # `true` is a JSON literal but a bare name in Python.
        result = self.run_json("--python", input="[true]")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")

    def test_the_input_is_not_repeated_in_the_message(self) -> None:
        result = self.run_json("--python", input="{'k': SECRETMARK('SECRETMARK')}")
        self.assert_exit(result, 1)
        self.assertNotIn("SECRETMARK", result.stderr)


class JsonLineEndingTests(unittest.TestCase):
    """Output redirected to a file or pipe must use LF on every platform."""

    def run_program(self, folder: str, *args: str, input: bytes | None = None):
        return subprocess.run(
            [sys.executable, "-m", "jzr_tools", "data", "json", *args],
            cwd=folder,
            input=input,
            capture_output=True,
            env={**os.environ, "JZR_CONFIG_DIR": folder},
        )

    def test_redirected_output_has_no_carriage_returns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a.json").write_bytes(b'{"a":[1,2]}\r\n')
            Path(folder, "b.json").write_bytes(b"[true]")
            completed = self.run_program(folder, "a.json", "b.json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(b"\r", completed.stdout)
        self.assertEqual(
            completed.stdout, b'{\n  "a": [\n    1,\n    2\n  ]\n}\n[\n  true\n]\n'
        )

    def test_piped_input_with_crlf_gives_lf_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            completed = self.run_program(folder, input=b'{\r\n"a":1\r\n}\r\n')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(b"\r", completed.stdout)
        self.assertEqual(completed.stdout, PRETTY_A1.encode())

    def test_non_ascii_output_is_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "u.json").write_bytes('{"n":"Zoë 日本語"}'.encode("utf-8"))
            completed = self.run_program(folder, "u.json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, '{\n  "n": "Zoë 日本語"\n}\n'.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()

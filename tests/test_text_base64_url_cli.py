"""CLI behaviour of `jzr text base64` and `jzr text url`, run in temporary folders."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

INVALID_BASE64 = "Invalid Base64 input. Check the alphabet and padding."
NOT_TEXT = "The decoded data is not text; write it to a file with -o FILE."
BAD_ESCAPE = "Invalid percent-encoding: % must be followed by two hex digits."
NOT_UTF8 = "The decoded text is not valid UTF-8."
NOT_ABSOLUTE = "Not an absolute URL: it needs a scheme such as https://."
BAD_PORT = "The port is not a number from 0 to 65535."


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-b64-url-")
        self.addCleanup(self._tmp.cleanup)
        self._old_cwd = os.getcwd()
        self.folder = Path(self._tmp.name).resolve()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self._old_cwd)
        # Unknown command names consult the bindings file: keep it off the real one.
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.folder / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_text(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["text", *args], input=input)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def assert_error_line(self, result, message: str) -> None:
        """Exit 1, the message alone on stderr, nothing on stdout."""
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr.strip(), message, self.describe(result))
        self.assertEqual(result.stdout, "", self.describe(result))

    def assert_usage_error(self, result, fragment: str) -> None:
        self.assert_exit(result, 2)
        self.assertIn(fragment, result.stderr, self.describe(result))
        self.assertEqual(result.stdout, "", self.describe(result))

    def write(self, name: str, data: bytes) -> Path:
        path = self.folder / name
        path.write_bytes(data)
        return path

    def folder_files(self) -> set[str]:
        return {p.name for p in self.folder.iterdir() if p.name != ".config"}

    @contextlib.contextmanager
    def clipboard(self):
        """Replace the clipboard function wherever the commands may look it up."""
        with contextlib.ExitStack() as stack:
            fakes = [stack.enter_context(mock.patch("jzr_tools.core.system.copy_to_clipboard"))]
            fakes.append(stack.enter_context(mock.patch("jzr_tools.commands.text.copy_to_clipboard")))
            yield fakes

    def copied(self, fakes) -> list[str]:
        """Every text handed to the clipboard, whichever name the command used."""
        return [call.args[0] for fake in fakes for call in fake.call_args_list]


# ---------------------------------------------------------------- group help


class TextHelpTests(CliCase):
    def test_text_help_lists_base64_url_and_slug(self) -> None:
        result = self.run_text("-h")
        self.assert_exit(result, 0)
        for name in ("base64", "url", "slug"):
            self.assertRegex(result.stdout, rf"(?m)^\s+{name}\s", self.describe(result))


# ---------------------------------------------------------------- base64 encode


class Base64EncodeTests(CliCase):
    def test_text_argument_is_encoded_as_utf8(self) -> None:
        result = self.run_text("base64", "hi")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "aGk=\n")
        self.assertEqual(result.stderr, "")

    def test_non_ascii_text(self) -> None:
        result = self.run_text("base64", "é€")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "w6nigqw=\n")

    def test_url_safe_has_no_padding_and_the_url_alphabet(self) -> None:
        standard = self.run_text("base64", "ûÿ")
        url_safe = self.run_text("base64", "ûÿ", "--url-safe")
        self.assert_exit(standard, 0)
        self.assert_exit(url_safe, 0)
        self.assertEqual(standard.stdout, "w7vDvw==\n")
        self.assertEqual(url_safe.stdout, "w7vDvw\n")
        # bytes that differ between the alphabets
        data = self.write("alpha.bin", b"\xfb\xff\xfe")
        self.assertEqual(self.run_text("base64", "-f", str(data)).stdout, "+//+\n")
        self.assertEqual(self.run_text("base64", "-f", str(data), "--url-safe").stdout, "-__-\n")

    def test_stdin_bytes_are_encoded_exactly_including_the_newline(self) -> None:
        result = self.run_text("base64", input=b"hi\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "aGkK\n")

    def test_dash_reads_stdin(self) -> None:
        result = self.run_text("base64", "-", input=b"hi")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "aGk=\n")

    def test_stdin_binary_bytes(self) -> None:
        result = self.run_text("base64", input=b"\x00\xff\xfe\x80")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "AP/+gA==\n")

    def test_file_bytes_are_encoded_exactly(self) -> None:
        self.write("in.bin", b"\x00\x01line\r\n\xff")
        result = self.run_text("base64", "-f", "in.bin")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "AAFsaW5lDQr/\n")

    def test_long_output_is_one_line(self) -> None:
        self.write("big.bin", bytes(range(256)) * 10)
        result = self.run_text("base64", "--file", "big.bin")
        self.assert_exit(result, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(len(result.stdout.strip()), 3416)

    def test_text_and_file_together_are_a_usage_error(self) -> None:
        self.write("in.bin", b"x")
        result = self.run_text("base64", "hi", "-f", "in.bin")
        self.assert_usage_error(result, "Give TEXT or --file, not both.")

    def test_a_missing_file_is_a_usage_error(self) -> None:
        result = self.run_text("base64", "-f", "nope.bin")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")

    def test_a_folder_is_not_accepted_as_file(self) -> None:
        (self.folder / "sub").mkdir()
        result = self.run_text("base64", "-f", "sub")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")

    def test_copy_copies_and_prints(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("base64", "hi", "-c")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "aGk=\n")
        copied = self.copied(fakes)
        self.assertEqual(len(copied), 1, copied)
        self.assertEqual(copied[0].rstrip("\n"), "aGk=")

    def test_without_copy_the_clipboard_is_untouched(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("base64", "hi")
        self.assert_exit(result, 0)
        self.assertEqual(self.copied(fakes), [])

    def test_output_without_decode_is_a_usage_error(self) -> None:
        result = self.run_text("base64", "hi", "-o", "out.bin")
        self.assert_usage_error(result, "-o writes decoded bytes; it only applies with --decode.")
        self.assertNotIn("out.bin", self.folder_files())

    def test_overwrite_without_output_does_not_break_encoding(self) -> None:
        result = self.run_text("base64", "hi", "--overwrite")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "aGk=\n")


# ---------------------------------------------------------------- base64 decode


class Base64DecodeTests(CliCase):
    def test_text_argument(self) -> None:
        result = self.run_text("base64", "-d", "aGk=")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hi\n")

    def test_long_option_name(self) -> None:
        result = self.run_text("base64", "--decode", "aGk=")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hi\n")

    def test_stdin_with_whitespace_and_newlines(self) -> None:
        result = self.run_text("base64", "-d", input="aGVs\nbG8g d29y\r\nbGQ=\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hello world\n")

    def test_file_input(self) -> None:
        self.write("enc.txt", b"aGVs\nbG8=\n")
        result = self.run_text("base64", "-d", "-f", "enc.txt")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hello\n")

    def test_a_newline_is_added_unless_the_text_ends_with_one(self) -> None:
        without = self.run_text("base64", "-d", "aGk=")  # "hi"
        with_newline = self.run_text("base64", "-d", "aGkK")  # "hi\n"
        self.assertEqual(without.stdout, "hi\n")
        self.assertEqual(with_newline.stdout, "hi\n")

    def test_decoded_non_ascii_text(self) -> None:
        result = self.run_text("base64", "-d", "w6nigqw=")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.encode("utf-8"), "é€\n".encode("utf-8"))

    def test_url_safe_accepts_missing_padding(self) -> None:
        result = self.run_text("base64", "-d", "--url-safe", "aGk")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hi\n")

    def test_standard_refuses_missing_padding(self) -> None:
        result = self.run_text("base64", "-d", "aGk")
        self.assert_error_line(result, INVALID_BASE64)

    def test_invalid_input_is_an_error_on_stderr_only(self) -> None:
        for text in ["aGk!", "aGl=", "a", "aGk===", "a-_b"]:
            with self.subTest(text=text):
                result = self.run_text("base64", "-d", text)
                self.assert_error_line(result, INVALID_BASE64)

    def test_invalid_stdin_input(self) -> None:
        result = self.run_text("base64", "-d", input="not base64!\n")
        self.assert_error_line(result, INVALID_BASE64)

    def test_the_error_does_not_repeat_the_input(self) -> None:
        result = self.run_text("base64", "-d", "SecretValue!!")
        self.assert_exit(result, 1)
        self.assertNotIn("SecretValue", result.stderr)
        self.assertNotIn("SecretValue", result.stdout)

    def test_data_that_is_not_text_is_not_printed(self) -> None:
        result = self.run_text("base64", "-d", "/w==")  # the single byte 0xFF
        self.assert_error_line(result, NOT_TEXT)

    def test_non_text_data_may_be_written_with_output(self) -> None:
        result = self.run_text("base64", "-d", "/+4=", "-o", "out.bin")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.bin").read_bytes(), b"\xff\xee")
        self.assertEqual(result.stdout.strip(), "Wrote 2 bytes to out.bin")

    def test_decode_copy_copies_and_prints(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("base64", "-d", "aGk=", "-c")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "hi\n")
        copied = self.copied(fakes)
        self.assertEqual(len(copied), 1, copied)
        self.assertEqual(copied[0].rstrip("\n"), "hi")

    def test_decode_copy_does_not_copy_on_error(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("base64", "-d", "!!", "-c")
        self.assert_exit(result, 1)
        self.assertEqual(self.copied(fakes), [])

    def test_copy_with_output_is_a_usage_error(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("base64", "-d", "aGk=", "-o", "out.bin", "-c")
        self.assert_usage_error(result, "--copy and -o do not combine.")
        self.assertEqual(self.copied(fakes), [])
        self.assertNotIn("out.bin", self.folder_files())


class Base64OutputFileTests(CliCase):
    def test_writes_the_exact_bytes_and_reports_the_count(self) -> None:
        payload = b"\x00\x01\x02\xff\xfe line\r\nend"
        encoded = self.run_text("base64", "-f", str(self.write("src.bin", payload))).stdout.strip()
        result = self.run_text("base64", "-d", encoded, "-o", "out.bin")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.bin").read_bytes(), payload)
        self.assertEqual(result.stdout.strip(), f"Wrote {len(payload)} bytes to out.bin")
        self.assertEqual(result.stderr, "")

    def test_one_byte_is_singular(self) -> None:
        result = self.run_text("base64", "-d", "YQ==", "-o", "one.bin")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.strip(), "Wrote 1 byte to one.bin")
        self.assertEqual((self.folder / "one.bin").read_bytes(), b"a")

    def test_the_count_has_thousands_separators(self) -> None:
        payload = bytes(range(256)) * 5 + b"abcd"  # 1,284 bytes
        self.write("src.bin", payload)
        encoded = self.run_text("base64", "-f", "src.bin").stdout.strip()
        result = self.run_text("base64", "-d", encoded, "-o", "big.bin")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.strip(), "Wrote 1,284 bytes to big.bin")
        self.assertEqual((self.folder / "big.bin").read_bytes(), payload)

    def test_text_that_ends_without_a_newline_is_written_unchanged(self) -> None:
        result = self.run_text("base64", "-d", "aGk=", "-o", "out.txt")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.txt").read_bytes(), b"hi")
        self.assertEqual(result.stdout.strip(), "Wrote 2 bytes to out.txt")

    def test_stdin_input_with_output(self) -> None:
        result = self.run_text("base64", "-d", "-o", "out.bin", input="/+4=\n")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.bin").read_bytes(), b"\xff\xee")

    def test_an_existing_file_is_kept_without_overwrite(self) -> None:
        self.write("out.bin", b"original")
        result = self.run_text("base64", "-d", "aGk=", "-o", "out.bin")
        self.assert_error_line(result, "out.bin exists; add --overwrite to replace it.")
        self.assertEqual((self.folder / "out.bin").read_bytes(), b"original")
        self.assertEqual(self.folder_files(), {"out.bin"})

    def test_overwrite_replaces_an_existing_file(self) -> None:
        self.write("out.bin", b"original, longer than the new data")
        result = self.run_text("base64", "-d", "aGk=", "-o", "out.bin", "--overwrite")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.bin").read_bytes(), b"hi")
        self.assertEqual(result.stdout.strip(), "Wrote 2 bytes to out.bin")

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.run_text("base64", "-d", "aGk=", "-o", "out.bin")
        self.assertEqual(self.folder_files(), {"out.bin"})
        self.run_text("base64", "-d", "aGk=", "-o", "out.bin", "--overwrite")
        self.assertEqual(self.folder_files(), {"out.bin"})
        self.run_text("base64", "-d", "aGk=", "-o", "out.bin")  # refused
        self.assertEqual(self.folder_files(), {"out.bin"})

    def test_invalid_input_writes_nothing(self) -> None:
        result = self.run_text("base64", "-d", "aGk!", "-o", "out.bin")
        self.assert_error_line(result, INVALID_BASE64)
        self.assertEqual(self.folder_files(), set())

    def test_invalid_input_keeps_an_existing_file(self) -> None:
        self.write("out.bin", b"original")
        result = self.run_text("base64", "-d", "aGk!", "-o", "out.bin", "--overwrite")
        self.assert_error_line(result, INVALID_BASE64)
        self.assertEqual((self.folder / "out.bin").read_bytes(), b"original")
        self.assertEqual(self.folder_files(), {"out.bin"})

    def test_output_in_another_folder(self) -> None:
        (self.folder / "sub").mkdir()
        target = Path("sub") / "out.bin"
        result = self.run_text("base64", "-d", "aGk=", "-o", str(target))
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "sub" / "out.bin").read_bytes(), b"hi")
        self.assertEqual(sorted(p.name for p in (self.folder / "sub").iterdir()), ["out.bin"])


# ---------------------------------------------------------------- url encode / decode


class UrlEncodeDecodeTests(CliCase):
    def test_encode_text_argument(self) -> None:
        result = self.run_text("url", "a b&c=d/é!~*'()")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a%20b%26c%3Dd%2F%C3%A9!~*'()\n")
        self.assertEqual(result.stderr, "")

    def test_encode_form_mode(self) -> None:
        result = self.run_text("url", "--form", "a b+c")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a+b%2Bc\n")

    def test_decode_text_argument(self) -> None:
        result = self.run_text("url", "-d", "a%20b%26c%3Dd%2F%C3%A9")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a b&c=d/é\n")

    def test_decode_keeps_plus_without_form(self) -> None:
        result = self.run_text("url", "-d", "a+b")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a+b\n")

    def test_decode_form_mode(self) -> None:
        result = self.run_text("url", "--decode", "--form", "a+b%2Bc")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a b+c\n")

    def test_stdin_loses_one_trailing_line_break(self) -> None:
        for data in ("hello world\n", "hello world\r\n", "hello world"):
            with self.subTest(data=data):
                result = self.run_text("url", input=data)
                self.assert_exit(result, 0)
                self.assertEqual(result.stdout, "hello%20world\n")

    def test_only_one_trailing_line_break_is_removed(self) -> None:
        result = self.run_text("url", input="x\n\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "x%0A\n")

    def test_inner_newlines_in_stdin_are_encoded(self) -> None:
        result = self.run_text("url", input="a\nb\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a%0Ab\n")

    def test_dash_reads_stdin(self) -> None:
        result = self.run_text("url", "-", input="a b\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a%20b\n")

    def test_decode_from_stdin(self) -> None:
        result = self.run_text("url", "-d", input="a%20b\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a b\n")

    def test_bad_escape_is_an_error(self) -> None:
        for text in ["%", "%4", "%zz", "100%"]:
            with self.subTest(text=text):
                result = self.run_text("url", "-d", text)
                self.assert_error_line(result, BAD_ESCAPE)

    def test_bytes_that_are_not_utf8_are_an_error(self) -> None:
        result = self.run_text("url", "-d", "%FF")
        self.assert_error_line(result, NOT_UTF8)

    def test_the_error_does_not_repeat_the_input(self) -> None:
        result = self.run_text("url", "-d", "SecretValue%zz")
        self.assert_exit(result, 1)
        self.assertNotIn("SecretValue", result.stderr)

    def test_copy_copies_and_prints_the_encoding(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("url", "a b", "-c")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a%20b\n")
        copied = self.copied(fakes)
        self.assertEqual(len(copied), 1, copied)
        self.assertEqual(copied[0].rstrip("\n"), "a%20b")

    def test_copy_copies_and_prints_the_decoding(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("url", "-d", "a%20b", "--copy")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "a b\n")
        self.assertEqual([c.rstrip("\n") for c in self.copied(fakes)], ["a b"])

    def test_nothing_is_copied_on_error(self) -> None:
        with self.clipboard() as fakes:
            result = self.run_text("url", "-d", "%zz", "-c")
        self.assert_exit(result, 1)
        self.assertEqual(self.copied(fakes), [])


# ---------------------------------------------------------------- url --parse

FULL_URL = "https://bob:s3cr3tpw@example.com:8443/a/b?q=hello+world&q=again&=x#top"

FULL_TEXT = (
    "Scheme    https\n"
    "Host      example.com\n"
    "Port      8443\n"
    "User      bob:***\n"
    "Path      /a/b\n"
    "Fragment  top\n"
    "Query     q = hello world\n"
    "          q = again\n"
    "          (no name) = x\n"
)


class UrlParseTextTests(CliCase):
    def test_full_url_layout(self) -> None:
        result = self.run_text("url", "--parse", FULL_URL)
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, FULL_TEXT)
        self.assertEqual(result.stderr, "")

    def test_the_password_is_never_shown(self) -> None:
        result = self.run_text("url", "--parse", FULL_URL)
        self.assertNotIn("s3cr3tpw", result.stdout + result.stderr)

    def test_user_without_password_has_no_stars(self) -> None:
        result = self.run_text("url", "--parse", "ftp://bob@files.example.com/pub")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            "Scheme    ftp\nHost      files.example.com\nUser      bob\nPath      /pub\n",
        )

    def test_lines_without_a_value_are_left_out(self) -> None:
        result = self.run_text("url", "--parse", "https://example.com")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "Scheme    https\nHost      example.com\n")

    def test_a_name_without_equals_has_an_empty_value(self) -> None:
        result = self.run_text("url", "--parse", "https://h/?flag")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "Scheme    https\nHost      h\nPath      /\nQuery     flag =\n")

    def test_query_values_are_decoded(self) -> None:
        result = self.run_text("url", "--parse", "https://h/?k=%C3%A9&s=a+b")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertIn("Query     k = é", lines)
        self.assertIn("          s = a b", lines)

    def test_url_without_a_host(self) -> None:
        result = self.run_text("url", "--parse", "mailto:alice@example.com")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "Scheme    mailto\nPath      alice@example.com\n")

    def test_ipv6_host_is_shown_without_brackets(self) -> None:
        result = self.run_text("url", "--parse", "http://[::1]:8080/")
        self.assert_exit(result, 0)
        self.assertIn("Host      ::1\n", result.stdout)
        self.assertIn("Port      8080\n", result.stdout)

    def test_url_from_stdin(self) -> None:
        result = self.run_text("url", "--parse", input="https://example.com/x\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "Scheme    https\nHost      example.com\nPath      /x\n")

    def test_relative_url_is_an_error(self) -> None:
        for text in ["/a/b", "example.com/x"]:
            with self.subTest(text=text):
                result = self.run_text("url", "--parse", text)
                self.assert_error_line(result, NOT_ABSOLUTE)

    def test_bad_port_is_an_error(self) -> None:
        result = self.run_text("url", "--parse", "http://h:99999/")
        self.assert_error_line(result, BAD_PORT)

    def test_query_that_is_not_utf8_is_an_error(self) -> None:
        result = self.run_text("url", "--parse", "http://h/?a=%FF")
        self.assert_error_line(result, NOT_UTF8)

    def test_the_error_does_not_repeat_the_url(self) -> None:
        result = self.run_text("url", "--parse", "secret-token/path")
        self.assert_exit(result, 1)
        self.assertNotIn("secret-token", result.stderr)


class UrlParseJsonTests(CliCase):
    def test_full_url_object(self) -> None:
        result = self.run_text("url", "--parse", "--json", FULL_URL)
        self.assert_exit(result, 0)
        data = json.loads(result.stdout)
        self.assertEqual(
            data,
            {
                "scheme": "https",
                "host": "example.com",
                "port": 8443,
                "user": "bob",
                "password": "***",
                "path": "/a/b",
                "fragment": "top",
                "query": [
                    {"name": "q", "value": "hello world"},
                    {"name": "q", "value": "again"},
                    {"name": "", "value": "x"},
                ],
            },
        )
        self.assertNotIn("s3cr3tpw", result.stdout)

    def test_keys_come_in_the_documented_order(self) -> None:
        result = self.run_text("url", "--parse", "--json", FULL_URL)
        self.assertEqual(
            list(json.loads(result.stdout)),
            ["scheme", "host", "port", "user", "password", "path", "fragment", "query"],
        )

    def test_missing_parts_are_null(self) -> None:
        result = self.run_text("url", "--parse", "--json", "https://example.com")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "scheme": "https",
                "host": "example.com",
                "port": None,
                "user": None,
                "password": None,
                "path": "",
                "fragment": None,
                "query": [],
            },
        )

    def test_user_without_password_has_a_null_password(self) -> None:
        result = self.run_text("url", "--parse", "--json", "ftp://bob@h/")
        data = json.loads(result.stdout)
        self.assertEqual(data["user"], "bob")
        self.assertIsNone(data["password"])

    def test_flag_without_equals_has_an_empty_value(self) -> None:
        data = json.loads(self.run_text("url", "--parse", "--json", "https://h/?flag").stdout)
        self.assertEqual(data["query"], [{"name": "flag", "value": ""}])

    def test_output_is_indented_by_two_and_keeps_non_ascii(self) -> None:
        result = self.run_text("url", "--parse", "--json", "https://h/é?n=é")
        self.assert_exit(result, 0)
        self.assertTrue(result.stdout.startswith('{\n  "scheme": "https",\n'), result.stdout)
        self.assertIn("é", result.stdout)
        self.assertNotIn("\\u", result.stdout)
        self.assertTrue(result.stdout.endswith("}\n"), result.stdout)

    def test_relative_url_is_an_error_with_no_json(self) -> None:
        result = self.run_text("url", "--parse", "--json", "/a/b")
        self.assert_error_line(result, NOT_ABSOLUTE)


class UrlOptionCombinationTests(CliCase):
    def test_json_without_parse_is_a_usage_error(self) -> None:
        result = self.run_text("url", "--json", "a b")
        self.assert_usage_error(result, "--json only applies with --parse.")

    def test_parse_does_not_combine_with_decode_form_or_copy(self) -> None:
        message = "--parse shows the parts of a URL; it does not combine with -d, --form or --copy."
        for flag in ("-d", "--decode", "--form", "-c", "--copy"):
            with self.subTest(flag=flag), self.clipboard() as fakes:
                result = self.run_text("url", "--parse", flag, "https://example.com/")
                self.assert_usage_error(result, message)
                self.assertEqual(self.copied(fakes), [])


if __name__ == "__main__":
    unittest.main()

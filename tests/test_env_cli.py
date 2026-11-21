"""CLI behaviour of `jzr env example` and `jzr env compare`, run in temporary folders."""

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

SECRET_A = "LEFT-SECRET-MARKER-4412"
SECRET_B = "RIGHT-SECRET-MARKER-9087"

KEY_RULE = (
    "Expected KEY=value. Keys may contain letters, digits and underscores, "
    "and cannot start with a digit."
)
UNCLOSED = "Unclosed quoted value. Add its matching closing quote."
AFTER_QUOTE = "Only a comment may follow a closing quote."


class EnvCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-env-")
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

    def read(self, name: str) -> bytes:
        return (self.folder / name).read_bytes()

    def names(self) -> set[str]:
        return {p.name for p in self.folder.iterdir() if p.name != ".config"}

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def assert_error_line(self, result, code: int, line: str) -> None:
        self.assert_exit(result, code)
        self.assertEqual(result.stdout, "", self.describe(result))
        self.assertEqual(result.stderr.strip().splitlines(), [line], self.describe(result))

    def example(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["env", "example", *args], input=input)

    def compare(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["env", "compare", *args], input=input)

    @contextlib.contextmanager
    def clipboard(self):
        """Replace the clipboard function wherever the command may look it up."""
        with contextlib.ExitStack() as stack:
            fakes = [stack.enter_context(mock.patch("jzr_tools.core.system.copy_to_clipboard"))]
            fakes.append(
                stack.enter_context(
                    mock.patch("jzr_tools.commands.env.copy_to_clipboard", create=True)
                )
            )
            yield fakes

    def copied(self, fakes) -> list[str]:
        return [call.args[0] for fake in fakes for call in fake.call_args_list]


# ------------------------------------------------------------------ example


class ExampleOutputTests(EnvCliCase):
    def test_default_file_is_dot_env(self) -> None:
        self.write(".env", "B=2\nA=1\n")
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "B=\nA=\n")
        self.assertEqual(result.stderr, "")

    def test_a_named_file(self) -> None:
        self.write(".env", "WRONG=1\n")
        self.write("prod.env", "# c\nexport X=1\nY='two words'\n")
        result = self.example("prod.env")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "X=\nY=\n")

    def test_a_file_in_a_subfolder(self) -> None:
        self.write("conf/app.env", "K=1\n")
        result = self.example("conf/app.env")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "K=\n")

    def test_stdin_with_a_dash(self) -> None:
        self.write(".env", "WRONG=1\n")
        result = self.example("-", input="P=1\nQ=2\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "P=\nQ=\n")

    def test_values_comments_and_duplicates_do_not_reach_the_output(self) -> None:
        self.write(
            ".env",
            f'# {SECRET_A}\nexport TOKEN="{SECRET_A}" # {SECRET_B}\n'
            f'MULTI="one\nINNER={SECRET_B}\nthree"\nTOKEN=again\n',
        )
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "TOKEN=\nMULTI=\n")
        self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_bom_and_crlf_files(self) -> None:
        self.write(".env", b"\xef\xbb\xbfA=1\r\nB=2\r\n")
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "A=\nB=\n")

    def test_an_empty_file_prints_nothing(self) -> None:
        self.write(".env", "")
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_a_file_with_only_comments_prints_nothing(self) -> None:
        self.write(".env", "# one\n\n# two\n")
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")

    def test_empty_stdin_prints_nothing(self) -> None:
        result = self.example("-", input="")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")

    def test_the_input_file_is_not_changed(self) -> None:
        self.write(".env", "A=1\n")
        result = self.example()
        self.assert_exit(result, 0)
        self.assertEqual(self.read(".env"), b"A=1\n")
        self.assertEqual(self.names(), {".env"})


class ExampleWriteTests(EnvCliCase):
    def test_output_file_is_written_and_reported(self) -> None:
        self.write(".env", "A=1\nB=2\nC=3\n")
        result = self.example("-o", ".env.example")
        self.assert_exit(result, 0)
        self.assertEqual(self.read(".env.example"), b"A=\nB=\nC=\n")
        self.assertEqual(result.stdout.strip(), "Wrote 3 keys to .env.example")
        self.assertEqual(result.stderr, "")

    def test_one_key_is_singular(self) -> None:
        self.write(".env", "A=1\n")
        result = self.example("--output", "out.txt")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.strip(), "Wrote 1 key to out.txt")

    def test_the_output_has_lf_line_endings_on_every_platform(self) -> None:
        self.write(".env", "A=1\r\nB=2\r\n")
        self.example("-o", "out.txt")
        self.assertEqual(self.read("out.txt"), b"A=\nB=\n")

    def test_output_from_stdin(self) -> None:
        result = self.example("-", "-o", "out.txt", input="A=1\n")
        self.assert_exit(result, 0)
        self.assertEqual(self.read("out.txt"), b"A=\n")

    def test_an_existing_output_is_kept_without_overwrite(self) -> None:
        self.write(".env", "A=1\n")
        self.write("out.txt", "old content\n")
        result = self.example("-o", "out.txt")
        self.assert_error_line(result, 1, "out.txt exists; add --overwrite to replace it.")
        self.assertEqual(self.read("out.txt"), b"old content\n")
        self.assertEqual(self.names(), {".env", "out.txt"})

    def test_overwrite_replaces_an_existing_output(self) -> None:
        self.write(".env", "A=1\nB=2\n")
        self.write("out.txt", "old content\n")
        result = self.example("-o", "out.txt", "--overwrite")
        self.assert_exit(result, 0)
        self.assertEqual(self.read("out.txt"), b"A=\nB=\n")
        self.assertEqual(result.stdout.strip(), "Wrote 2 keys to out.txt")

    def test_no_temporary_files_are_left_behind(self) -> None:
        self.write(".env", "A=1\n")
        self.example("-o", "out.txt")
        self.example("-o", "out.txt", "--overwrite")
        self.assertEqual(self.names(), {".env", "out.txt"})

    def test_a_parse_error_creates_no_output_file(self) -> None:
        self.write(".env", "A=1\nbad line\n")
        result = self.example("-o", "out.txt")
        self.assert_error_line(result, 1, f".env:2: {KEY_RULE}")
        self.assertEqual(self.names(), {".env"})

    def test_an_empty_input_writes_an_empty_file(self) -> None:
        self.write(".env", "# nothing\n")
        result = self.example("-o", "out.txt")
        self.assert_exit(result, 0)
        self.assertEqual(self.read("out.txt"), b"")
        self.assertEqual(result.stdout.strip(), "Wrote 0 keys to out.txt")

    def test_output_in_a_missing_folder_is_a_plain_error(self) -> None:
        self.write(".env", "A=1\n")
        result = self.example("-o", "nope/out.txt")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, self.describe(result))
        self.assertNotIn("Traceback", result.stderr)

    def test_output_naming_the_input_file_is_a_usage_error(self) -> None:
        self.write(".env", "A=1\n")
        for extra in ([], ["--overwrite"]):
            with self.subTest(extra=extra):
                result = self.example("-o", ".env", *extra)
                self.assert_exit(result, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    "Error: -o is the input file; that would replace your values.",
                    result.stderr,
                )
                self.assertEqual(self.read(".env"), b"A=1\n")
                self.assertEqual(self.names(), {".env"})

    def test_the_same_file_under_another_spelling_is_also_refused(self) -> None:
        self.write("prod.env", "A=1\n")
        result = self.example("prod.env", "-o", "./prod.env", "--overwrite")
        self.assert_exit(result, 2)
        self.assertIn("-o is the input file; that would replace your values.", result.stderr)
        self.assertEqual(self.read("prod.env"), b"A=1\n")

    def test_the_default_input_is_checked_against_the_output_too(self) -> None:
        self.write(".env", "A=1\n")
        result = self.example("--output", ".env", "--overwrite")
        self.assert_exit(result, 2)
        self.assertIn("-o is the input file; that would replace your values.", result.stderr)
        self.assertEqual(self.read(".env"), b"A=1\n")


class ExampleClipboardTests(EnvCliCase):
    def test_copy_sends_the_text_to_the_clipboard_and_still_prints_it(self) -> None:
        self.write(".env", "A=1\nB=2\n")
        with self.clipboard() as fakes:
            result = self.example("-c")
        self.assert_exit(result, 0)
        self.assertEqual(self.copied(fakes), ["A=\nB=\n"])
        self.assertEqual(result.stdout, "A=\nB=\n")

    def test_long_option_name(self) -> None:
        self.write(".env", "A=1\n")
        with self.clipboard() as fakes:
            result = self.example("--copy")
        self.assert_exit(result, 0)
        self.assertEqual(self.copied(fakes), ["A=\n"])

    def test_nothing_is_copied_without_the_flag(self) -> None:
        self.write(".env", "A=1\n")
        with self.clipboard() as fakes:
            self.example()
        self.assertEqual(self.copied(fakes), [])

    def test_nothing_is_copied_when_the_file_cannot_be_parsed(self) -> None:
        self.write(".env", "bad line\n")
        with self.clipboard() as fakes:
            result = self.example("-c")
        self.assert_exit(result, 1)
        self.assertEqual(self.copied(fakes), [])


class ExampleErrorTests(EnvCliCase):
    def test_parse_error_is_one_line_with_file_line_and_reason(self) -> None:
        self.write(".env", f"A=1\nB='{SECRET_A}' trailing\n")
        result = self.example()
        self.assert_error_line(result, 1, f".env:2: {AFTER_QUOTE}")
        self.assertNotIn("SECRET", result.stderr)

    def test_unclosed_quote_names_the_key_line(self) -> None:
        self.write("x.env", f'\nA="{SECRET_A}\nmore\n')
        result = self.example("x.env")
        self.assert_error_line(result, 1, f"x.env:2: {UNCLOSED}")

    def test_parse_error_on_stdin_names_stdin(self) -> None:
        result = self.example("-", input="A=1\n1BAD=2\n")
        self.assert_error_line(result, 1, f"<stdin>:2: {KEY_RULE}")

    def test_missing_default_file(self) -> None:
        self.assert_error_line(self.example(), 1, ".env: not found")

    def test_missing_named_file(self) -> None:
        self.assert_error_line(self.example("nope.env"), 1, "nope.env: not found")

    def test_a_folder(self) -> None:
        (self.folder / "d").mkdir()
        self.assert_error_line(self.example("d"), 1, "d: is a folder")

    def test_a_file_that_is_not_utf8(self) -> None:
        self.write("bin.env", b"A=\xff\xfe\n")
        self.assert_error_line(self.example("bin.env"), 1, "bin.env: not UTF-8 text")

    def test_stdin_that_is_not_utf8(self) -> None:
        self.assert_error_line(self.example("-", input=b"A=\xff\n"), 1, "<stdin>: not UTF-8 text")

    def test_nul_character_is_a_parse_error(self) -> None:
        self.write(".env", b"A=1\nB=x\x00y\n")
        self.assert_error_line(
            self.example(), 1, ".env:2: Remove null characters from the file."
        )


# ------------------------------------------------------------------ compare


EXAMPLE_FILE = "API_KEY=\nDB_URL=\nPORT=8000\n"
ENV_FILE = "PORT=8080\nDEBUG=1\n"


class CompareLayoutTests(EnvCliCase):
    def test_defaults_are_env_example_and_env(self) -> None:
        self.write(".env.example", EXAMPLE_FILE)
        self.write(".env", ENV_FILE)
        result = self.compare()
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "Only in .env.example (2):\n  API_KEY\n  DB_URL\nOnly in .env (1):\n  DEBUG\n",
        )
        self.assertEqual(result.stderr, "")

    def test_one_argument_names_a_and_b_stays_dot_env(self) -> None:
        self.write(".env.example", "WRONG=1\n")
        self.write("other.env", "A=1\nB=2\n")
        self.write(".env", "B=2\nC=3\n")
        result = self.compare("other.env")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in other.env (1):\n  A\nOnly in .env (1):\n  C\n")

    def test_two_arguments(self) -> None:
        self.write("a.env", "X=1\nS=1\n")
        self.write("b.env", "S=1\nY=1\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in a.env (1):\n  X\nOnly in b.env (1):\n  Y\n")

    def test_only_sections_that_have_keys_are_printed(self) -> None:
        self.write(".env.example", "A=\nB=\n")
        self.write(".env", "A=1\n")
        result = self.compare()
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in .env.example (1):\n  B\n")
        self.write(".env.example", "A=\n")
        self.write(".env", "A=1\nZ=1\n")
        result = self.compare()
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in .env (1):\n  Z\n")

    def test_keys_are_listed_in_first_seen_order_not_sorted(self) -> None:
        self.write("a.env", "ZED=1\nALPHA=1\nZED=2\nMID=1\n")
        self.write("b.env", "")
        result = self.compare("a.env", "b.env")
        self.assertEqual(result.stdout, "Only in a.env (3):\n  ZED\n  ALPHA\n  MID\n")

    def test_missing_in_b_when_b_is_empty_or_only_comments(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", "# nothing\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in a.env (1):\n  A\n")

    def test_same_keys_print_one_line_and_exit_zero(self) -> None:
        self.write("a.env", "A=1\nB=2\nC=3\n")
        self.write("b.env", "C=x\nB=y\nA=z\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout, r"^Same keys in .+ and .+ \(3 keys\)\.\n$")
        self.assertEqual(result.stderr, "")

    def test_same_keys_one_key_is_singular(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", "A=2\nA=3\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout, r"\(1 key\)\.\n$")

    def test_same_keys_zero_keys(self) -> None:
        self.write("a.env", "# a\n")
        self.write("b.env", "\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout, r"\(0 keys\)\.\n$")

    def test_differing_values_alone_do_not_change_the_exit_code(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", "A=2\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout, r"^Same keys in ")

    def test_stdin_can_be_a(self) -> None:
        self.write(".env", "B=1\n")
        result = self.compare("-", input="A=1\nB=1\n")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in <stdin> (1):\n  A\n")

    def test_stdin_can_be_b(self) -> None:
        self.write("a.env", "A=1\n")
        result = self.compare("a.env", "-", input="A=1\nB=1\n")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "Only in <stdin> (1):\n  B\n")

    def test_both_dashes_are_a_usage_error(self) -> None:
        result = self.compare("-", "-", input="A=1\n")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("Error:", result.stderr)
        self.assertNotIn("No such command", result.stderr)
        self.assertNotIn("not found", result.stderr)

    def test_bom_and_crlf_files_compare_by_key(self) -> None:
        self.write("a.env", b"\xef\xbb\xbfA=1\r\nB=2\r\n")
        self.write("b.env", "A=1\nB=2\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)

    def test_values_stay_out_of_the_output_without_values(self) -> None:
        self.write("a.env", f"ONLY_A={SECRET_A}\nBOTH={SECRET_A}\n")
        self.write("b.env", f"ONLY_B={SECRET_B}\nBOTH={SECRET_B}\n")
        for extra in ([], ["--json"]):
            with self.subTest(extra=extra):
                result = self.compare("a.env", "b.env", *extra)
                self.assert_exit(result, 1)
                self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_values_stay_out_of_the_output_when_keys_match(self) -> None:
        self.write("a.env", f"BOTH={SECRET_A}\n")
        self.write("b.env", f"BOTH={SECRET_B}\n")
        result = self.compare("a.env", "b.env")
        self.assert_exit(result, 0)
        self.assertNotIn("SECRET", result.stdout + result.stderr)


class CompareValuesTests(EnvCliCase):
    def test_different_values_section(self) -> None:
        self.write(".env.example", "PORT=8000\n")
        self.write(".env", "PORT=8080\n")
        result = self.compare("--values")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "Different values (1):\n  PORT: 8000 -> 8080\n")

    def test_the_section_replaces_the_same_keys_line(self) -> None:
        self.write("a.env", "A=1\nB=2\n")
        self.write("b.env", "A=1\nB=3\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 0)
        self.assertNotIn("Same keys", result.stdout)

    def test_same_keys_and_same_values_still_print_the_same_keys_line(self) -> None:
        self.write("a.env", "A=1\nB=2\n")
        self.write("b.env", "B=2\nA=1\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout, r"^Same keys in .+ and .+ \(2 keys\)\.\n$")

    def test_the_section_comes_after_the_two_key_sections(self) -> None:
        self.write(".env.example", EXAMPLE_FILE)
        self.write(".env", ENV_FILE)
        result = self.compare("--values")
        self.assert_exit(result, 1)
        self.assertEqual(
            result.stdout,
            "Only in .env.example (2):\n  API_KEY\n  DB_URL\n"
            "Only in .env (1):\n  DEBUG\n"
            "Different values (1):\n  PORT: 8000 -> 8080\n",
        )

    def test_values_follow_a_order_and_use_the_last_assignment(self) -> None:
        self.write("a.env", "Z=1\nA=old\nA=new\nM=same\n")
        self.write("b.env", "A=changed\nM=same\nZ=2\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout, "Different values (2):\n  Z: 1 -> 2\n  A: new -> changed\n"
        )

    def test_values_are_shown_as_written_with_quotes_kept(self) -> None:
        self.write("a.env", 'A="left"\nB=\'q\'\nC=x # note\n')
        self.write("b.env", "A=right # note\nB=\"q2\"\nC=y\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assertEqual(
            result.stdout,
            'Different values (3):\n  A: "left" -> right\n  B: \'q\' -> "q2"\n  C: x -> y\n',
        )

    def test_empty_values_show_as_two_quotes(self) -> None:
        self.write("a.env", "EMPTY=\nOTHER=set\n")
        self.write("b.env", "EMPTY=set\nOTHER=\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assertEqual(
            result.stdout, 'Different values (2):\n  EMPTY: "" -> set\n  OTHER: set -> ""\n'
        )

    def test_a_newline_in_a_value_shows_as_backslash_n(self) -> None:
        self.write("a.env", 'MULTI="one\ntwo"\n')
        self.write("b.env", 'MULTI="one\nthree"\n')
        result = self.compare("a.env", "b.env", "--values")
        self.assertEqual(result.stdout, 'Different values (1):\n  MULTI: "one\\ntwo" -> "one\\nthree"\n')
        self.assertEqual(len(result.stdout.splitlines()), 2)

    def test_one_pair_of_quotes_is_not_a_difference(self) -> None:
        self.write("a.env", 'A="x"\nB=\'y\'\nC=z\n')
        self.write("b.env", "A=x\nB=y\nC=\"z\"\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 0)
        self.assertNotIn("Different values", result.stdout)
        self.assertRegex(result.stdout, r"^Same keys in .+ and .+ \(3 keys\)\.\n$")

    def test_keys_missing_on_one_side_are_not_listed_as_different(self) -> None:
        self.write("a.env", "A=1\nB=1\n")
        self.write("b.env", "B=1\nC=1\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assertEqual(result.stdout, "Only in a.env (1):\n  A\nOnly in b.env (1):\n  C\n")

    def test_values_appear_only_in_the_different_values_section(self) -> None:
        self.write("a.env", f"ONLY_A={SECRET_A}\nBOTH=left\n")
        self.write("b.env", f"ONLY_B={SECRET_B}\nBOTH=right\n")
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 1)
        self.assertNotIn("SECRET", result.stdout)
        self.assertIn("BOTH: left -> right", result.stdout)


class CompareJsonTests(EnvCliCase):
    def test_shape_without_values(self) -> None:
        self.write(".env.example", EXAMPLE_FILE)
        self.write(".env", ENV_FILE)
        result = self.compare("--json")
        self.assert_exit(result, 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "a": ".env.example",
                "b": ".env",
                "same": False,
                "only_a": ["API_KEY", "DB_URL"],
                "only_b": ["DEBUG"],
                "changed": None,
            },
        )
        self.assertEqual(result.stderr, "")

    def test_shape_with_values(self) -> None:
        self.write(".env.example", EXAMPLE_FILE)
        self.write(".env", ENV_FILE)
        result = self.compare("--json", "--values")
        self.assert_exit(result, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["changed"], [{"key": "PORT", "a": "8000", "b": "8080"}])
        self.assertEqual(data["only_a"], ["API_KEY", "DB_URL"])
        self.assertFalse(data["same"])

    def test_same_keys_exit_zero_and_same_true(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", "A=2\n")
        result = self.compare("a.env", "b.env", "--json")
        self.assert_exit(result, 0)
        data = json.loads(result.stdout)
        self.assertEqual(
            data,
            {"a": "a.env", "b": "b.env", "same": True, "only_a": [], "only_b": [], "changed": None},
        )

    def test_same_keys_with_values_lists_the_changes_and_keeps_exit_zero(self) -> None:
        self.write("a.env", "A=1\nB=same\n")
        self.write("b.env", "A=2\nB=same\n")
        result = self.compare("a.env", "b.env", "--json", "--values")
        self.assert_exit(result, 0)
        data = json.loads(result.stdout)
        self.assertTrue(data["same"])
        self.assertEqual(data["changed"], [{"key": "A", "a": "1", "b": "2"}])

    def test_no_changes_with_values_is_an_empty_list(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", "A=1\n")
        data = json.loads(self.compare("a.env", "b.env", "--json", "--values").stdout)
        self.assertEqual(data["changed"], [])

    def test_values_in_json_are_as_written_without_display_escaping(self) -> None:
        self.write("a.env", 'MULTI="one\ntwo"\nE=\nQ="x"\n')
        self.write("b.env", "MULTI=zero\nE=set\nQ='y'\n")
        data = json.loads(self.compare("a.env", "b.env", "--json", "--values").stdout)
        self.assertEqual(
            data["changed"],
            [
                {"key": "MULTI", "a": '"one\ntwo"', "b": "zero"},
                {"key": "E", "a": "", "b": "set"},
                {"key": "Q", "a": '"x"', "b": "'y'"},
            ],
        )

    def test_stdin_is_named_in_json(self) -> None:
        self.write(".env", "A=1\n")
        data = json.loads(self.compare("-", "--json", input="A=1\n").stdout)
        self.assertEqual(data["a"], "<stdin>")
        self.assertEqual(data["b"], ".env")

    def test_values_are_absent_from_json_without_values(self) -> None:
        self.write("a.env", f"BOTH={SECRET_A}\nA_ONLY={SECRET_A}\n")
        self.write("b.env", f"BOTH={SECRET_B}\n")
        result = self.compare("a.env", "b.env", "--json")
        self.assert_exit(result, 1)
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        self.assertIsNone(json.loads(result.stdout)["changed"])

    def test_json_errors_use_exit_two_and_print_no_document(self) -> None:
        result = self.compare("--json")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip().splitlines(),
            [".env.example: not found", ".env: not found"],
        )


class CompareErrorTests(EnvCliCase):
    def test_parse_error_exit_two_with_file_line_and_reason(self) -> None:
        self.write("a.env", "A=1\n")
        self.write("b.env", f"A=1\nB='{SECRET_B}' trailing\n")
        result = self.compare("a.env", "b.env")
        self.assert_error_line(result, 2, f"b.env:2: {AFTER_QUOTE}")
        self.assertNotIn("SECRET", result.stderr)

    def test_each_failing_file_gets_its_line_and_nothing_is_printed(self) -> None:
        self.write("a.env", "1BAD=x\n")
        self.write("b.env", 'ok=1\nB="open\n')
        result = self.compare("a.env", "b.env", "--values")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip().splitlines(),
            [f"a.env:1: {KEY_RULE}", f"b.env:2: {UNCLOSED}"],
        )

    def test_one_good_and_one_bad_file_prints_only_the_error(self) -> None:
        self.write("a.env", "A=1\nB=2\n")
        self.write("b.env", "A=1\nnot valid\n")
        result = self.compare("a.env", "b.env")
        self.assert_error_line(result, 2, f"b.env:2: {KEY_RULE}")

    def test_missing_default_files(self) -> None:
        result = self.compare()
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip().splitlines(),
            [".env.example: not found", ".env: not found"],
        )

    def test_one_missing_file(self) -> None:
        self.write(".env.example", "A=\n")
        self.assert_error_line(self.compare(), 2, ".env: not found")

    def test_missing_named_file(self) -> None:
        self.write("b.env", "A=\n")
        self.assert_error_line(self.compare("nope.env", "b.env"), 2, "nope.env: not found")

    def test_a_folder(self) -> None:
        (self.folder / "d").mkdir()
        self.write("b.env", "A=\n")
        self.assert_error_line(self.compare("d", "b.env"), 2, "d: is a folder")

    def test_a_file_that_is_not_utf8(self) -> None:
        self.write("a.env", b"A=\xff\xfe\n")
        self.write("b.env", "A=\n")
        self.assert_error_line(self.compare("a.env", "b.env"), 2, "a.env: not UTF-8 text")

    def test_stdin_errors_name_stdin(self) -> None:
        self.write(".env", "A=1\n")
        self.assert_error_line(
            self.compare("-", input="1BAD=x\n"), 2, f"<stdin>:1: {KEY_RULE}"
        )
        self.assert_error_line(
            self.compare("-", input=b"\xff\n"), 2, "<stdin>: not UTF-8 text"
        )


if __name__ == "__main__":
    unittest.main()

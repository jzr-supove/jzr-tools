"""CLI behaviour of `jzr data convert`, run in temporary folders."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from tests import dataconv_fixtures as fx

CSV_TEXT_NOTE = (
    "note: CSV cells were kept as text; --types reads numbers, true/false and empty cells"
)
PRETTY_ROWS = '[\n  {\n    "a": 1,\n    "b": "x"\n  }\n]\n'
EXPECTED_OPTIONS = [
    "--from", "--to", "--output", "--delimiter", "--no-header", "--header-row", "--types",
    "--nest", "--sheet", "--indent", "--minify", "--overwrite", "--help",
]


def flat(text: str) -> str:
    return " ".join(text.split())


class ConvertCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-data-convert-")
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

    def convert(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["data", "convert", *args], input=input)

    def describe(self, result) -> str:
        return (
            f"exit={result.exit_code}\nstdout={result.stdout_bytes!r}\nstderr={result.stderr!r}"
        )

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def assert_usage_error(self, result, message: str) -> None:
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout_bytes, b"", self.describe(result))
        self.assertIn(message, flat(result.stderr), self.describe(result))

    def stderr_lines(self, result) -> list[str]:
        return result.stderr.splitlines()

    def names(self) -> set[str]:
        return {p.name for p in self.folder.iterdir() if p.name != ".config"}


class FormatChoiceTests(ConvertCliCase):
    def test_both_formats_come_from_the_extensions(self) -> None:
        self.write("a.json", '[{"a": 1, "b": "x"}]')
        result = self.convert("a.json", "-o", "out.csv")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.csv").read_bytes(), b"a,b\r\n1,x\r\n")

    def test_the_input_format_comes_from_the_file_extension(self) -> None:
        self.write("a.yaml", "a: 1\n")
        result = self.convert("a.yaml", "--to", "json")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), {"a": 1})

    def test_from_overrides_the_extension(self) -> None:
        self.write("notes.txt", '{"a": 1}')
        result = self.convert("notes.txt", "--from", "json", "--to", "yaml")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"a: 1\n")
        self.write("data.json", "a: 1\n")
        result = self.convert("data.json", "-f", "yaml", "-t", "json")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), {"a": 1})

    def test_to_overrides_the_output_extension(self) -> None:
        self.write("a.json", '{"a": 1}')
        result = self.convert("a.json", "--to", "yaml", "-o", "out.txt")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.txt").read_bytes(), b"a: 1\n")

    def test_format_names_are_not_case_sensitive(self) -> None:
        self.write("a.json", '{"a": 1}')
        result = self.convert("a.json", "--from", "JSON", "--to", "YAML")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"a: 1\n")

    def test_every_extension_is_understood(self) -> None:
        cases = {
            "x.json": '{"a": 1}', "x.jsonl": '{"a": 1}\n', "x.ndjson": '{"a": 1}\n',
            "x.yaml": "a: 1\n", "x.yml": "a: 1\n", "x.csv": "a\n1\n", "x.tsv": "a\n1\n",
            "x.toml": "a = 1\n", "X.JSON": '{"a": 1}',
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                self.write(name, content)
                result = self.convert(name, "--to", "json")
                self.assert_exit(result, 0)
                json.loads(result.stdout_bytes)

    def test_a_tsv_file_is_tab_delimited(self) -> None:
        self.write("a.tsv", "a\tb\n1\t2,3\n")
        result = self.convert("a.tsv", "--to", "json")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), [{"a": "1", "b": "2,3"}])

    def test_a_tsv_output_is_tab_delimited(self) -> None:
        self.write("a.json", '[{"a": 1, "b": 2}]')
        result = self.convert("a.json", "-o", "out.tsv")
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "out.tsv").read_bytes(), b"a\tb\r\n1\t2\r\n")

    def test_the_delimiter_option_is_used_for_reading_and_writing(self) -> None:
        self.write("a.csv", "a;b\n1;x,y\n")
        result = self.convert("a.csv", "--to", "json", "--delimiter", ";")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), [{"a": "1", "b": "x,y"}])
        for name, expected in ((";", b"a;b\r\n1;x\r\n"), ("tab", b"a\tb\r\n1\tx\r\n"), ("|", b"a|b\r\n1|x\r\n")):
            with self.subTest(delimiter=name):
                result = self.convert(
                    "--from", "json", "--to", "csv", "--delimiter", name, input='[{"a": 1, "b": "x"}]'
                )
                self.assert_exit(result, 0)
                self.assertEqual(result.stdout_bytes, expected)

    def test_jsonl_both_ways(self) -> None:
        self.write("a.jsonl", '{"a": 1}\n\n{"a": 2}\n')
        result = self.convert("a.jsonl", "--to", "json", "--minify")
        self.assertEqual(result.stdout_bytes, b'[{"a":1},{"a":2}]\n')
        result = self.convert("--from", "json", "--to", "jsonl", input="[1, 2]")
        self.assertEqual(result.stdout_bytes, b"1\n2\n")


class StdinAndStdoutTests(ConvertCliCase):
    def test_stdin_is_read_when_there_is_no_file(self) -> None:
        result = self.convert("--from", "json", "--to", "yaml", input='{"a": [1, 2]}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes.decode("utf-8").split(), ["a:", "-", "1", "-", "2"])
        self.assertEqual(self.stderr_lines(result), [])

    def test_a_dash_reads_stdin(self) -> None:
        result = self.convert("-", "--from", "json", "--to", "yaml", input='{"a": 1}')
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"a: 1\n")

    def test_binary_stdin_is_read_for_xlsx(self) -> None:
        result = self.convert("--from", "xlsx", "--to", "json", input=fx.simple_workbook())
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes)[0]["name"], "Ann")

    def test_csv_output_keeps_crlf_and_everything_else_uses_lf(self) -> None:
        data = '[{"a": 1, "b": "x"}]'
        csv_out = self.convert("--from", "json", "--to", "csv", input=data).stdout_bytes
        self.assertEqual(csv_out, b"a,b\r\n1,x\r\n")
        for target in ("json", "yaml", "jsonl"):
            with self.subTest(target=target):
                out = self.convert("--from", "json", "--to", target, input=data).stdout_bytes
                self.assertNotIn(b"\r", out)
        out = self.convert("--from", "json", "--to", "toml", input='{"a": {"b": 1}}').stdout_bytes
        self.assertNotIn(b"\r", out)

    def test_json_output_bytes(self) -> None:
        result = self.convert("--from", "yaml", "--to", "json", input="a: 1\nb: x\n")
        self.assertEqual(result.stdout_bytes, b'{\n  "a": 1,\n  "b": "x"\n}\n')

    def test_non_ascii_output_is_utf8(self) -> None:
        result = self.convert("--from", "json", "--to", "yaml", input='{"n": "Zoë 日本語"}')
        self.assertEqual(result.stdout_bytes, "n: Zoë 日本語\n".encode("utf-8"))

    def test_input_with_crlf_gives_lf_json(self) -> None:
        result = self.convert("--from", "json", "--to", "json", input=b'{\r\n"a":1\r\n}\r\n')
        self.assertEqual(result.stdout_bytes, b'{\n  "a": 1\n}\n')

    def test_indent_and_minify_apply_to_json_output(self) -> None:
        result = self.convert("--from", "yaml", "--to", "json", "--indent", "4", input="a: [1]\n")
        self.assertEqual(result.stdout_bytes, b'{\n    "a": [\n        1\n    ]\n}\n')
        result = self.convert("--from", "yaml", "--to", "json", "--minify", input="a: [1]\n")
        self.assertEqual(result.stdout_bytes, b'{"a":[1]}\n')

    def test_a_file_is_read_as_it_is_and_never_changed(self) -> None:
        path = self.write("a.json", '{"a":1}')
        result = self.convert("a.json", "--to", "yaml")
        self.assert_exit(result, 0)
        self.assertEqual(path.read_bytes(), b'{"a":1}')


class OutputFileTests(ConvertCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.json", '[{"a": 1, "b": "x"}]')

    def test_o_writes_the_file_and_names_it_on_stderr(self) -> None:
        result = self.convert("a.json", "-o", "out.yaml")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"")
        self.assertEqual(self.stderr_lines(result), ["wrote out.yaml"])
        self.assertEqual((self.folder / "out.yaml").read_bytes(), b"- a: 1\n  b: x\n")

    def test_the_wrote_line_carries_a_summary_where_there_is_one(self) -> None:
        result = self.convert("a.json", "-o", "out.csv")
        self.assertEqual(len(self.stderr_lines(result)), 1)
        self.assertRegex(self.stderr_lines(result)[0], r"^wrote out\.csv \(1 rows?\)$")
        result = self.convert("--from", "json", "--to", "jsonl", "-o", "lines.jsonl", input="[1, 2, 3]")
        self.assertEqual(self.stderr_lines(result), ["wrote lines.jsonl (3 lines)"])

    def test_the_output_may_be_in_another_folder(self) -> None:
        (self.folder / "sub").mkdir()
        result = self.convert("a.json", "-o", str(Path("sub") / "out.csv"))
        self.assert_exit(result, 0)
        self.assertEqual((self.folder / "sub" / "out.csv").read_bytes(), b"a,b\r\n1,x\r\n")

    def test_an_existing_output_is_refused_and_left_alone(self) -> None:
        existing = self.write("out.json", "keep me")
        result = self.convert("a.json", "-o", "out.json")
        self.assert_usage_error(result, "out.json exists; add --overwrite to replace it")
        self.assertEqual(existing.read_bytes(), b"keep me")

    def test_overwrite_replaces_it(self) -> None:
        self.write("out.json", "old")
        result = self.convert("a.json", "-o", "out.json", "--overwrite")
        self.assert_exit(result, 0)
        self.assertEqual(self.stderr_lines(result)[0].split(" (")[0], "wrote out.json")
        self.assertEqual((self.folder / "out.json").read_text(encoding="utf-8"), PRETTY_ROWS)

    def test_no_temporary_files_are_left_behind(self) -> None:
        before = self.names()
        self.convert("a.json", "-o", "out.yaml")
        self.assertEqual(self.names(), before | {"out.yaml"})

    def test_a_failure_while_renaming_leaves_the_folder_as_it_was(self) -> None:
        before = self.names()
        with mock.patch("os.replace", side_effect=OSError("disk trouble")):
            result = self.convert("a.json", "-o", "out.yaml")
        self.assert_exit(result, 1)
        self.assertEqual(self.names(), before)
        self.assertNotIn("Traceback", result.stderr)

    def test_the_output_may_not_be_the_input(self) -> None:
        for extra in ((), ("--overwrite",)):
            with self.subTest(extra=extra):
                result = self.convert("a.json", "-o", "a.json", *extra)
                self.assert_usage_error(result, "the output would replace a.json; give another -o")
                self.assertEqual((self.folder / "a.json").read_bytes(), b'[{"a": 1, "b": "x"}]')

    def test_the_output_may_not_be_the_input_under_another_spelling(self) -> None:
        result = self.convert("a.json", "-o", str(self.folder / "a.json"), "--overwrite")
        self.assert_exit(result, 2)
        self.assertIn("the output would replace", result.stderr)
        self.assertEqual((self.folder / "a.json").read_bytes(), b'[{"a": 1, "b": "x"}]')


class NotesTests(ConvertCliCase):
    def test_notes_go_to_stderr_after_the_output(self) -> None:
        self.write("a.csv", "a,b\n1,2\n")
        result = self.convert("a.csv", "--to", "json")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), [{"a": "1", "b": "2"}])
        self.assertEqual(self.stderr_lines(result), [CSV_TEXT_NOTE])

    def test_notes_follow_the_wrote_line(self) -> None:
        self.write("a.csv", "a,b\n1,2\n")
        result = self.convert("a.csv", "-o", "out.json")
        self.assertEqual(self.stderr_lines(result), ["wrote out.json", CSV_TEXT_NOTE])

    def test_every_note_is_on_its_own_line(self) -> None:
        self.write("a.yaml", "1: a\n1.5: b\n")
        result = self.convert("a.yaml", "--to", "json")
        self.assertEqual(
            self.stderr_lines(result),
            [
                "note: YAML comments and anchors are not kept",
                "note: keys that were not text became text: 1, 1.5",
            ],
        )

    def test_a_clean_conversion_prints_nothing_on_stderr(self) -> None:
        result = self.convert("--from", "json", "--to", "yaml", input='{"a": 1}')
        self.assertEqual(result.stderr, "")

    def test_types_removes_the_text_note(self) -> None:
        self.write("a.csv", "a,b\n1,2\n")
        result = self.convert("a.csv", "--to", "json", "--types")
        self.assertEqual(json.loads(result.stdout_bytes), [{"a": 1, "b": 2}])
        self.assertEqual(result.stderr, "")

    def test_nest_and_no_header_for_csv(self) -> None:
        self.write("a.csv", "u.n,u.m\nx,y\n")
        result = self.convert("a.csv", "--to", "json", "--nest", "--minify")
        self.assertEqual(result.stdout_bytes, b'[{"u":{"n":"x","m":"y"}}]\n')
        result = self.convert("a.csv", "--to", "json", "--no-header", "--minify")
        self.assertEqual(result.stdout_bytes, b'[["u.n","u.m"],["x","y"]]\n')


class XlsxCliTests(ConvertCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("data.json", '[{"a": 1}, {"a": 2}]')

    def test_without_o_the_workbook_is_written_beside_the_file(self) -> None:
        result = self.convert("data.json", "--to", "xlsx")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"")
        self.assertEqual(self.stderr_lines(result), ["wrote data.xlsx (1 sheet, 2 rows)"])
        from openpyxl import load_workbook

        ws = load_workbook(self.folder / "data.xlsx").active
        self.assertEqual([[c.value for c in row] for row in ws.iter_rows()], [["a"], [1], [2]])

    def test_the_default_name_is_beside_a_file_in_another_folder(self) -> None:
        self.write("sub/rows.json", '[{"a": 1}]')
        result = self.convert(str(Path("sub") / "rows.json"), "--to", "xlsx")
        self.assert_exit(result, 0)
        self.assertTrue((self.folder / "sub" / "rows.xlsx").is_file())
        self.assertIn("rows.xlsx", result.stderr.replace("\\", "/"))

    def test_o_names_the_workbook(self) -> None:
        result = self.convert("data.json", "-o", "book.xlsx")
        self.assert_exit(result, 0)
        self.assertTrue((self.folder / "book.xlsx").is_file())
        self.assertFalse((self.folder / "data.xlsx").exists())
        self.assertEqual(self.stderr_lines(result), ["wrote book.xlsx (1 sheet, 2 rows)"])

    def test_the_workbook_is_a_zip_file(self) -> None:
        self.convert("data.json", "--to", "xlsx")
        self.assertEqual((self.folder / "data.xlsx").read_bytes()[:2], b"PK")

    def test_an_existing_default_workbook_is_not_replaced_without_overwrite(self) -> None:
        existing = self.write("data.xlsx", "keep me")
        result = self.convert("data.json", "--to", "xlsx")
        self.assert_exit(result, 2)
        self.assertIn("data.xlsx exists; add --overwrite to replace it", flat(result.stderr))
        self.assertEqual(existing.read_bytes(), b"keep me")
        result = self.convert("data.json", "--to", "xlsx", "--overwrite")
        self.assert_exit(result, 0)
        self.assertEqual(existing.read_bytes()[:2], b"PK")

    def test_xlsx_from_stdin_needs_o(self) -> None:
        result = self.convert("--from", "json", "--to", "xlsx", input="[]")
        self.assert_usage_error(result, "xlsx output from stdin needs -o FILE")
        self.assertEqual(self.names(), {"data.json"})

    def test_xlsx_from_stdin_with_o_works(self) -> None:
        result = self.convert("--from", "json", "-o", "out.xlsx", input='[{"a": 1}]')
        self.assert_exit(result, 0)
        self.assertRegex(self.stderr_lines(result)[0], r"^wrote out\.xlsx \(1 sheet, 1 rows?\)$")

    def test_the_default_workbook_name_may_not_be_the_input(self) -> None:
        book = self.write("book.xlsx", fx.simple_workbook())
        before = book.read_bytes()
        result = self.convert("book.xlsx", "--to", "xlsx", "--overwrite")
        self.assert_usage_error(result, "the output would replace book.xlsx; give another -o")
        self.assertEqual(book.read_bytes(), before)

    def test_xlsx_to_json_reads_the_first_sheet(self) -> None:
        self.write("people.xlsx", fx.simple_workbook())
        result = self.convert("people.xlsx", "--to", "json", "--minify")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout_bytes),
            [
                {"name": "Ann", "age": 30, "score": 1.5, "ok": True},
                {"name": "Bob", "age": None, "score": 2, "ok": False},
            ],
        )
        self.assertEqual(result.stderr, "")

    def test_the_sheet_option_and_notes_work_from_the_command_line(self) -> None:
        self.write("book.xlsx", fx.two_visible_one_hidden())
        result = self.convert("book.xlsx", "--to", "json", "--sheet", "Places", "--minify")
        self.assertEqual(result.stdout_bytes, b'[{"city":"Oslo"}]\n')
        result = self.convert(
            "book.xlsx", "--to", "json", "--sheet", "Places", "--sheet", "People", "--minify"
        )
        self.assertEqual(result.stdout_bytes, b'{"People":[{"name":"Ann"}],"Places":[{"city":"Oslo"}]}\n')

    def test_header_row_and_no_header_work_from_the_command_line(self) -> None:
        self.write("g.xlsx", fx.rows_workbook([["junk"], ["id", "w"], [1, "one"]]))
        result = self.convert("g.xlsx", "--to", "json", "--header-row", "2", "--minify")
        self.assertEqual(result.stdout_bytes, b'[{"id":1,"w":"one"}]\n')
        self.assertRegex(
            result.stderr, r"note: the first rows above the header were skipped: 1(-1)?\b"
        )
        result = self.convert("g.xlsx", "--to", "json", "--no-header", "--minify")
        self.assertEqual(result.stdout_bytes, b'[["junk",null],["id","w"],[1,"one"]]\n')

    def test_a_damaged_workbook_is_one_plain_line(self) -> None:
        self.write("bad.xlsx", b"not a workbook")
        result = self.convert("bad.xlsx", "--to", "json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout_bytes, b"")
        self.assertEqual(
            self.stderr_lines(result), ["bad.xlsx: not an Excel workbook, or a damaged one"]
        )

    def test_xlsm_is_read(self) -> None:
        self.write("macro.xlsm", fx.simple_workbook())
        result = self.convert("macro.xlsm", "--to", "csv")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout_bytes, b"name,age,score,ok\r\nAnn,30,1.5,true\r\nBob,,2,false\r\n")


class UsageErrorTests(ConvertCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.json", '{"a": 1}')
        self.write("a.csv", "a\n1\n")
        self.write("book.xlsx", fx.simple_workbook())

    def test_no_target_format(self) -> None:
        message = "give --to FORMAT, or -o with a known extension"
        self.assert_usage_error(self.convert("a.json"), message)
        self.assert_usage_error(self.convert("a.json", "-o", "out.txt"), message)
        self.assert_usage_error(self.convert("a.json", "-o", "out"), message)
        self.assertEqual(self.names(), {"a.json", "a.csv", "book.xlsx"})

    def test_stdin_without_from(self) -> None:
        message = "stdin has no name; give --from"
        self.assert_usage_error(self.convert("--to", "json", input="{}"), message)
        self.assert_usage_error(self.convert("-", "--to", "json", input="{}"), message)

    def test_a_file_with_an_unknown_extension(self) -> None:
        self.write("notes.txt", "x")
        self.assert_usage_error(
            self.convert("notes.txt", "--to", "json"), "cannot tell the format of notes.txt; give --from"
        )

    def test_refused_extensions(self) -> None:
        self.write("old.xls", "x")
        self.assert_usage_error(
            self.convert("old.xls", "--to", "json"), ".xls is not supported; save it as .xlsx"
        )
        self.assert_usage_error(
            self.convert("a.json", "-o", "out.ods"), ".ods is not supported; save it as .xlsx"
        )
        self.assertFalse((self.folder / "out.ods").exists())

    def test_a_missing_file(self) -> None:
        result = self.convert("missing.json", "--to", "yaml")
        self.assert_usage_error(result, "File not found: missing.json")

    def test_an_unknown_format_name(self) -> None:
        for args, option in (
            (("a.json", "--to", "xml"), "--to"),
            (("a.json", "--from", "xml", "--to", "json"), "--from"),
        ):
            with self.subTest(args=args):
                result = self.convert(*args)
                self.assert_exit(result, 2)
                self.assertIn(option, result.stderr)
                self.assertIn("xml", result.stderr)
                self.assertNotIn("No such command", result.stderr)

    def test_options_that_do_not_apply_to_the_formats(self) -> None:
        cases = [
            (("a.json", "--to", "yaml", "--sheet", "S"), "--sheet only applies to xlsx input."),
            (("a.csv", "--to", "json", "--sheet", "S"), "--sheet only applies to xlsx input."),
            (("a.csv", "--to", "json", "--header-row", "2"), "--header-row only applies to xlsx input."),
            (("a.json", "--to", "yaml", "--types"), "--types only applies to csv input."),
            (("book.xlsx", "--to", "json", "--types"), "--types only applies to csv input."),
            (("a.json", "--to", "yaml", "--nest"), "--nest only applies to csv and xlsx input."),
            (("a.json", "--to", "yaml", "--no-header"), "--no-header only applies to csv and xlsx input."),
            (("a.json", "--to", "yaml", "--delimiter", ";"), "--delimiter only applies to csv."),
            (("a.json", "--to", "csv", "--indent", "4"), "--indent and --minify only apply to json output."),
            (("a.json", "--to", "yaml", "--minify"), "--indent and --minify only apply to json output."),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                self.assert_usage_error(self.convert(*args), message)
        self.assertEqual(self.names(), {"a.json", "a.csv", "book.xlsx"})

    def test_options_that_combine_badly(self) -> None:
        self.assert_usage_error(
            self.convert("a.json", "--to", "json", "--indent", "4", "--minify"),
            "--indent and --minify do not combine.",
        )
        self.assert_usage_error(
            self.convert("book.xlsx", "--to", "json", "--header-row", "2", "--no-header"),
            "--header-row and --no-header do not combine.",
        )

    def test_delimiter_is_allowed_when_either_side_is_csv(self) -> None:
        result = self.convert("--from", "json", "--to", "csv", "--delimiter", ";", input='[{"a": 1}]')
        self.assert_exit(result, 0)
        result = self.convert("a.csv", "--to", "json", "--delimiter", ";")
        self.assert_exit(result, 0)

    def test_option_values_out_of_range(self) -> None:
        for args in (
            ("--indent", "9"), ("--indent", "-1"), ("--indent", "x"), ("--delimiter", "x"),
            ("--header-row", "0"),
        ):
            with self.subTest(args=args):
                result = self.convert("book.xlsx", "--to", "json", *args)
                self.assert_exit(result, 2)
                self.assertIn(args[0], result.stderr)
                self.assertNotIn("No such command", result.stderr)

    def test_usage_errors_are_plain_text(self) -> None:
        result = self.convert("a.json")
        self.assert_exit(result, 2)
        self.assertIn("Error: give --to FORMAT", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class FailureTests(ConvertCliCase):
    def test_a_conversion_error_names_the_file_and_exits_1(self) -> None:
        self.write("bad.json", '{"a":1,\n  "b":}\n')
        before = self.names()
        result = self.convert("bad.json", "-o", "out.yaml")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout_bytes, b"")
        lines = self.stderr_lines(result)
        self.assertEqual(len(lines), 1, self.describe(result))
        self.assertRegex(lines[0], r"^bad\.json: invalid JSON at line 2 column 7: ")
        self.assertEqual(self.names(), before)

    def test_the_file_is_named_as_given(self) -> None:
        self.write("sub/bad.json", "[1,")
        result = self.convert("sub/bad.json", "--to", "yaml")
        self.assert_exit(result, 1)
        self.assertTrue(result.stderr.startswith("sub/bad.json: "), result.stderr)

    def test_stdin_is_named_stdin(self) -> None:
        result = self.convert("--from", "json", "--to", "yaml", input="")
        self.assert_exit(result, 1)
        self.assertEqual(self.stderr_lines(result), ["<stdin>: the input is empty"])
        result = self.convert("--from", "yaml", "--to", "json", input="a: 1\n---\nb: 2\n")
        self.assertEqual(
            self.stderr_lines(result),
            ["<stdin>: use one YAML document; this input has 2 (split it, or remove the extra --- lines)"],
        )

    def test_a_writing_error_leaves_no_output_file(self) -> None:
        self.write("a.json", '{"a": null}')
        result = self.convert("a.json", "-o", "out.toml")
        self.assert_exit(result, 1)
        self.assertEqual(self.stderr_lines(result), ["a.json: TOML has no null: a"])
        self.assertFalse((self.folder / "out.toml").exists())
        self.assertEqual(self.names(), {"a.json"})

    def test_a_writing_error_prints_nothing_to_stdout(self) -> None:
        result = self.convert("--from", "json", "--to", "toml", input="[1]")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout_bytes, b"")
        self.assertEqual(self.stderr_lines(result), ["<stdin>: TOML needs an object at the top"])

    def test_a_failed_conversion_keeps_the_file_it_would_have_replaced(self) -> None:
        self.write("bad.json", "{")
        old = self.write("out.yaml", "old: 1\n")
        result = self.convert("bad.json", "-o", "out.yaml", "--overwrite")
        self.assert_exit(result, 1)
        self.assertEqual(old.read_bytes(), b"old: 1\n")

    def test_a_file_that_is_not_utf8(self) -> None:
        self.write("latin.json", b'["caf\xe9"]')
        result = self.convert("latin.json", "--to", "yaml")
        self.assert_exit(result, 1)
        self.assertEqual(self.stderr_lines(result), ["latin.json: not UTF-8 text"])

    def test_the_input_is_not_repeated_in_the_message(self) -> None:
        self.write("secret.json", '{"token": SECRETMARK}')
        result = self.convert("secret.json", "--to", "yaml")
        self.assert_exit(result, 1)
        self.assertNotIn("SECRETMARK", result.stderr)
        self.assertNotIn("SECRETMARK", result.stdout)

    def test_no_notes_are_printed_when_the_conversion_fails(self) -> None:
        self.write("a.yaml", "1: a\nb: .nan\n")
        result = self.convert("a.yaml", "--to", "json")
        self.assert_exit(result, 1)
        self.assertEqual(self.stderr_lines(result), ["a.yaml: JSON has no NaN or Infinity: b"])

    def test_a_folder_given_as_the_file(self) -> None:
        (self.folder / "dir.json").mkdir()
        result = self.convert("dir.json", "--to", "yaml")
        self.assertIn(result.exit_code, (1, 2), self.describe(result))
        self.assertIn("dir.json", result.stderr)
        self.assertNotIn("No such command", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class HelpTests(ConvertCliCase):
    def help(self) -> str:
        result = self.runner.invoke(app, ["data", "convert", "-h"])
        self.assert_exit(result, 0)
        return result.stdout

    def test_the_description(self) -> None:
        text = flat(self.help())
        self.assertIn("Convert data between JSON, YAML, CSV, TOML and Excel.", text)
        self.assertIn(
            "The formats come from the file names, or from --from and --to. Text goes to "
            "stdout unless -o names a file; Excel output goes to -o, or to <name>.xlsx beside "
            "FILE. Anything lost on the way is named in a note on stderr.",
            text,
        )

    def test_the_options_are_listed_in_the_agreed_order(self) -> None:
        listed = re.findall(
            r"^  (?:-\w, )?(--[a-z-]+)", self.help().split("Options:", 1)[1], flags=re.MULTILINE
        )
        self.assertEqual(listed, EXPECTED_OPTIONS)

    def test_short_options(self) -> None:
        text = self.help()
        for short in ("-f, --from", "-t, --to", "-o, --output"):
            self.assertIn(short, text)

    def test_option_help_texts(self) -> None:
        text = flat(self.help())
        for expected in (
            "Format of the input; needed for stdin.",
            "Format to write; or give -o a file name with an extension.",
            "Write to this file instead of stdout.",
            "CSV and Excel: the first row is data; rows become arrays.",
            "Excel: the row with the column names (default: the first row with data).",
            "CSV: read numbers, true/false and empty cells instead of text.",
            "CSV and Excel: turn dotted column names into nested objects.",
            "Excel: read this sheet; repeat for more (default: every visible sheet).",
            "JSON: indent with N spaces, 0 to 8 (default 2).",
            "JSON: leave out all whitespace.",
            "Replace an existing output file.",
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, text)
        self.assertIn("CSV delimiter:", text)
        self.assertIn("(default , and tab for .tsv).", text)

    def test_the_format_choices_are_listed(self) -> None:
        text = self.help()
        self.assertRegex(text, r"--from \[json\|jsonl\|yaml\|csv\|toml\|xlsx\]")
        self.assertRegex(text, r"--to \[json\|jsonl\|yaml\|csv\|toml\|xlsx\]")

    def test_the_data_group_lists_convert_and_json(self) -> None:
        result = self.runner.invoke(app, ["data", "-h"])
        self.assert_exit(result, 0)
        listed = re.findall(r"^  (\S+)", result.stdout.split("Commands:", 1)[1], flags=re.MULTILINE)
        self.assertEqual(sorted(listed), ["convert", "json"])
        self.assertIn("Format, check and convert structured data.", flat(result.stdout))
        self.assertRegex(result.stdout, r"(?m)^  convert\s+Convert data")

    def test_the_root_help_has_the_new_group_text(self) -> None:
        result = self.runner.invoke(app, ["-h"])
        self.assertRegex(result.stdout, r"(?m)^  data\s+Format, check and convert structured data\.")
        self.assertNotIn("Format and check JSON data.", result.stdout)


class WorkbookSanityTests(ConvertCliCase):
    def test_a_workbook_written_by_the_cli_reads_back_through_the_cli(self) -> None:
        rows = [{"id": 1, "name": "Ann", "ok": True}, {"id": 2, "name": "Bob", "ok": False}]
        self.write("rows.json", json.dumps(rows))
        self.assert_exit(self.convert("rows.json", "-o", "rows.xlsx"), 0)
        result = self.convert("rows.xlsx", "--to", "json", "--minify")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout_bytes), rows)
        self.assertEqual(result.stderr, "")
        # and as CSV, the same rows
        result = self.convert("rows.xlsx", "--to", "csv")
        self.assertEqual(result.stdout_bytes, b"id,name,ok\r\n1,Ann,true\r\n2,Bob,false\r\n")


if __name__ == "__main__":
    unittest.main()

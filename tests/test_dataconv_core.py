"""Converting between JSON, JSON Lines, YAML, CSV, TOML and Excel data."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import tomllib
import unittest

from jzr_tools.core import dataconv
from jzr_tools.core.dataconv import (
    ConvertError,
    ReadOptions,
    WriteOptions,
    convert,
    default_delimiter,
    format_of,
)
from jzr_tools.core.dataconv import model
from tests import dataconv_fixtures as fx

YAML_NOTE = "YAML comments and anchors are not kept"
CSV_TEXT_NOTE = (
    "CSV cells were kept as text; --types reads numbers, true/false and empty cells"
)
FORMULA_NOTE = (
    "some cells start with =, +, - or @, and spreadsheets may treat them as formulas"
)


def run(source: str, target: str, data, read=ReadOptions(), write=WriteOptions()):
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    return convert(raw, source=source, target=target, read=read, write=write)


def text(source: str, target: str, data, **kwargs) -> str:
    return run(source, target, data, **kwargs).data.decode("utf-8")


def value(source: str, data, **kwargs):
    """What a reader makes of ``data``, seen through JSON."""
    return json.loads(text(source, "json", data, **kwargs))


def failure(source: str, target: str, data, **kwargs) -> str:
    with unittest.TestCase().assertRaises(ConvertError) as caught:
        run(source, target, data, **kwargs)
    return str(caught.exception)


def sheet_rows(data: bytes, sheet: str | None = None) -> list[list]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [[cell.value for cell in row] for row in ws.iter_rows()]


class FormatTableTests(unittest.TestCase):
    def test_the_formats_and_extensions(self) -> None:
        self.assertEqual(dataconv.FORMATS, ("json", "jsonl", "yaml", "csv", "toml", "xlsx"))
        self.assertEqual(
            dataconv.EXTENSIONS,
            {
                ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
                ".yaml": "yaml", ".yml": "yaml", ".csv": "csv", ".tsv": "csv",
                ".toml": "toml", ".xlsx": "xlsx", ".xlsm": "xlsx",
            },
        )
        self.assertEqual(dataconv.REFUSED, {".xls", ".ods", ".xlsb"})
        self.assertEqual((dataconv.MAX_VALUES, dataconv.MAX_DEPTH), (1_000_000, 100))

    def test_format_of_reads_the_extension_in_any_case(self) -> None:
        for name, expected in (
            ("a.json", "json"), ("A.JSON", "json"), ("dir/x.ndjson", "jsonl"),
            ("x.YML", "yaml"), ("x.tsv", "csv"), ("x.xlsm", "xlsx"), ("x.toml", "toml"),
        ):
            with self.subTest(name=name):
                self.assertEqual(format_of(name), expected)

    def test_format_of_accepts_paths(self) -> None:
        from pathlib import Path

        self.assertEqual(format_of(Path("sub") / "a.csv"), "csv")

    def test_format_of_does_not_guess(self) -> None:
        for name in ("notes.txt", "README", "archive.tar.gz", ".json"):
            with self.subTest(name=name):
                self.assertIsNone(format_of(name))

    def test_format_of_refuses_old_spreadsheet_formats(self) -> None:
        for name in ("old.xls", "OLD.XLS", "sheet.ods", "book.xlsb"):
            with self.subTest(name=name):
                with self.assertRaises(ConvertError) as caught:
                    format_of(name)
                extension = "." + name.rsplit(".", 1)[1].lower()
                self.assertEqual(
                    str(caught.exception), f"{extension} is not supported; save it as .xlsx"
                )

    def test_default_delimiter_is_a_tab_only_for_tsv(self) -> None:
        self.assertEqual(default_delimiter("a.tsv"), "\t")
        self.assertEqual(default_delimiter("A.TSV"), "\t")
        self.assertEqual(default_delimiter("a.csv"), ",")
        self.assertEqual(default_delimiter("a.json"), ",")
        self.assertEqual(default_delimiter(None), ",")


class InputEdgeTests(unittest.TestCase):
    def test_empty_and_blank_input_is_refused_in_every_text_format(self) -> None:
        for source in ("json", "jsonl", "yaml", "csv", "toml"):
            for data in ("", "   \n\t\n"):
                with self.subTest(source=source, data=data):
                    self.assertEqual(failure(source, "json", data), "the input is empty")

    def test_input_that_is_not_utf8_is_refused(self) -> None:
        for source in ("json", "jsonl", "yaml", "csv", "toml"):
            with self.subTest(source=source):
                self.assertEqual(failure(source, "json", b"a\xe9\xe9"), "not UTF-8 text")

    def test_a_utf8_bom_is_dropped_in_every_text_format(self) -> None:
        bom = b"\xef\xbb\xbf"
        cases = {
            "json": b'{"a": 1}',
            "jsonl": b'{"a": 1}\n',
            "yaml": b"a: 1\n",
            "toml": b"a = 1\n",
        }
        for source, body in cases.items():
            with self.subTest(source=source):
                result = json.loads(run(source, "json", bom + body).data)
                self.assertEqual(result[0] if source == "jsonl" else result, {"a": 1})


class JsonReadTests(unittest.TestCase):
    def test_key_order_and_big_numbers_survive(self) -> None:
        out = text("json", "json", '{"z": 1, "a": 123456789012345678901234567890, "m": 1.5}')
        self.assertEqual(
            out, '{\n  "z": 1,\n  "a": 123456789012345678901234567890,\n  "m": 1.5\n}\n'
        )

    def test_a_duplicate_key_keeps_the_last_value_and_is_noted(self) -> None:
        result = run("json", "json", '{"a": 1, "a": 2}')
        self.assertEqual(json.loads(result.data), {"a": 2})
        self.assertEqual(result.notes, ("duplicate keys: a; the last value was kept",))

    def test_a_nested_duplicate_key_is_named_by_its_path(self) -> None:
        result = run("json", "json", '{"b": {"c": 1, "c": 2}}')
        self.assertEqual(json.loads(result.data), {"b": {"c": 2}})
        self.assertEqual(result.notes, ("duplicate keys: b.c; the last value was kept",))

    def test_several_duplicate_paths_share_one_note(self) -> None:
        result = run("json", "json", '{"a": 1, "a": 2, "b": {"c": 1, "c": 2}}')
        duplicates = [n for n in result.notes if n.startswith("duplicate keys: ")]
        self.assertEqual(len(duplicates), 1)
        self.assertIn("a", duplicates[0].split(": ", 1)[1].split("; ")[0].split(", "))
        self.assertIn("b.c", duplicates[0])
        self.assertTrue(duplicates[0].endswith("; the last value was kept"))

    def test_nan_and_infinity_are_refused_with_a_position(self) -> None:
        for data in ("[NaN]", "[Infinity]", '{"a": -Infinity}'):
            with self.subTest(data=data):
                self.assertRegex(
                    failure("json", "json", data),
                    r"^JSON has no NaN or Infinity \(line 1 column \d+\)$",
                )

    def test_a_syntax_error_says_where_it_is(self) -> None:
        self.assertRegex(
            failure("json", "yaml", '{"a":1,\n  "b":}\n'),
            r"^invalid JSON at line 2 column 7: [a-z]",
        )

    def test_the_message_does_not_repeat_the_input(self) -> None:
        self.assertNotIn("SECRETMARK", failure("json", "json", '{"k": SECRETMARK}'))

    def test_json_nested_deeper_than_the_limit_is_refused(self) -> None:
        deep = "[" * 105 + "]" * 105
        self.assertEqual(failure("json", "json", deep), "the data is nested more than 100 levels deep")

    def test_json_nested_within_the_limit_is_converted(self) -> None:
        shallow = "[" * 50 + "]" * 50
        self.assertEqual(json.loads(run("json", "json", shallow).data), json.loads(shallow))

    def test_more_values_than_the_limit_is_refused(self) -> None:
        many = "[" + ",".join(["0"] * 1_000_100) + "]"
        self.assertEqual(failure("json", "json", many), "the data has more than 1,000,000 values")


class JsonWriteTests(unittest.TestCase):
    DATA = '{"name": "Zoë 日本語", "list": [1, 2], "empty": {}}'

    def test_the_default_is_two_spaces_with_a_final_newline_and_real_unicode(self) -> None:
        self.assertEqual(
            run("json", "json", self.DATA).data,
            '{\n  "name": "Zoë 日本語",\n  "list": [\n    1,\n    2\n  ],\n  "empty": {}\n}\n'.encode(),
        )

    def test_indent_and_minify(self) -> None:
        self.assertEqual(
            text("json", "json", '{"a": [1]}', write=WriteOptions(indent=4)),
            '{\n    "a": [\n        1\n    ]\n}\n',
        )
        self.assertEqual(
            text("json", "json", '{"a": [1]}', write=WriteOptions(indent=0)),
            '{\n"a": [\n1\n]\n}\n',
        )
        self.assertEqual(
            text("json", "json", '{ "a": 1, "b": [1, 2] }', write=WriteOptions(minify=True)),
            '{"a":1,"b":[1,2]}\n',
        )

    def test_json_has_no_summary(self) -> None:
        self.assertEqual(run("json", "json", "[1]").summary, "")

    def test_nan_and_infinity_from_yaml_are_refused_by_the_targets_that_cannot_hold_them(self) -> None:
        self.assertEqual(failure("yaml", "json", "a: .inf\n"), "JSON has no NaN or Infinity: a")
        self.assertEqual(failure("yaml", "jsonl", "a: .nan\n"), "JSON has no NaN or Infinity: a")
        self.assertEqual(failure("yaml", "toml", "a: .inf\n"), "TOML has no NaN or Infinity: a")
        self.assertEqual(
            failure("yaml", "xlsx", "- a: .inf\n"), "Excel has no NaN or Infinity: [0].a"
        )

    def test_yaml_and_csv_can_hold_nan_and_infinity(self) -> None:
        out = text("yaml", "yaml", "a: .inf\nb: .nan\nc: -.inf\n")
        self.assertIn("a: .inf", out)
        self.assertIn("b: .nan", out)
        self.assertIn("c: -.inf", out)
        self.assertEqual(run("yaml", "csv", "- a: .inf\n- a: .nan\n").data, b"a\r\ninf\r\nnan\r\n")


class JsonLinesTests(unittest.TestCase):
    def test_every_non_blank_line_is_a_value_and_the_result_is_an_array(self) -> None:
        self.assertEqual(
            value("jsonl", '{"a": 1}\n\n[2]\n  \n"x"\n3'), [{"a": 1}, [2], "x", 3]
        )

    def test_an_error_names_its_physical_line(self) -> None:
        message = failure("jsonl", "json", '{"a": 1}\n\n{"a": }\n')
        self.assertRegex(message, r"^line 3: invalid JSON at column \d+: \S")

    def test_nan_is_refused(self) -> None:
        self.assertRegex(failure("jsonl", "json", "[1]\nNaN\n"), r"^line 2: ")

    def test_an_array_is_written_one_item_per_line(self) -> None:
        self.assertEqual(text("json", "jsonl", "[1, 2, 3]"), "1\n2\n3\n")
        lines = text("json", "jsonl", '[{"a": 1, "b": [1, 2]}, {"a": "é"}]').split("\n")
        self.assertEqual(lines[-1], "")
        self.assertEqual([json.loads(line) for line in lines[:-1]], [{"a": 1, "b": [1, 2]}, {"a": "é"}])

    def test_anything_else_is_one_line(self) -> None:
        out = text("json", "jsonl", '{"a": [1, 2], "b": {"c": 1}}')
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out), {"a": [1, 2], "b": {"c": 1}})
        self.assertEqual(text("json", "jsonl", '"just text"'), '"just text"\n')

    def test_the_summary_counts_lines(self) -> None:
        self.assertEqual(run("json", "jsonl", "[1, 2, 3, 4, 5]").summary, "5 lines")


class YamlReadTests(unittest.TestCase):
    def test_yaml_1_1_booleans_stay_strings(self) -> None:
        self.assertEqual(
            value("yaml", "a: yes\nb: no\nc: on\nd: off\ne: y\nf: n\n"),
            {"a": "yes", "b": "no", "c": "on", "d": "off", "e": "y", "f": "n"},
        )

    def test_the_core_schema_booleans_and_nulls(self) -> None:
        self.assertEqual(
            value("yaml", "a: true\nb: True\nc: FALSE\nd: null\ne: ~\nf:\ng: Null\n"),
            {"a": True, "b": True, "c": False, "d": None, "e": None, "f": None, "g": None},
        )

    def test_numbers_follow_the_core_schema(self) -> None:
        self.assertEqual(
            value("yaml", "a: 0777\nb: 0o17\nc: 0x1F\nd: -12\ne: 1.5\nf: 1e3\ng: 123456789012345678901234567890\n"),
            {"a": 777, "b": 15, "c": 31, "d": -12, "e": 1.5, "f": 1000.0,
             "g": 123456789012345678901234567890},
        )

    def test_underscores_and_sexagesimals_are_not_numbers(self) -> None:
        self.assertEqual(value("yaml", "a: 1_000\nb: 1:30\n"), {"a": "1_000", "b": "1:30"})

    def test_dates_stay_text_and_get_no_date_note(self) -> None:
        result = run("yaml", "json", "d: 2026-10-24\nt: 2026-10-24T10:15:00\n")
        self.assertEqual(json.loads(result.data), {"d": "2026-10-24", "t": "2026-10-24T10:15:00"})
        self.assertEqual(result.notes, (YAML_NOTE,))

    def test_merge_keys_are_ordinary_keys(self) -> None:
        self.assertEqual(
            value("yaml", "base: &b {x: 1}\nchild:\n  <<: *b\n  y: 2\n"),
            {"base": {"x": 1}, "child": {"<<": {"x": 1}, "y": 2}},
        )

    def test_every_yaml_input_notes_that_comments_and_anchors_are_lost(self) -> None:
        result = run("yaml", "json", "# a comment\na: 1\n")
        self.assertEqual(result.notes, (YAML_NOTE,))

    def test_several_documents_are_refused(self) -> None:
        self.assertEqual(
            failure("yaml", "json", "a: 1\n---\nb: 2\n---\nc: 3\n"),
            "use one YAML document; this input has 3 (split it, or remove the extra --- lines)",
        )

    def test_one_document_with_markers_is_fine(self) -> None:
        self.assertEqual(value("yaml", "---\na: 1\n...\n"), {"a": 1})

    def test_tags_outside_the_core_schema_are_refused(self) -> None:
        self.assertEqual(failure("yaml", "json", "a: !x 1\n"), "YAML tag !x is not supported")
        self.assertRegex(
            failure("yaml", "json", "a: !!python/object:os.system {}\n"), r"^YAML tag .+ is not supported$"
        )

    def test_core_schema_tags_are_allowed(self) -> None:
        self.assertEqual(value("yaml", "a: !!str 123\n"), {"a": "123"})

    def test_aliases_are_expanded(self) -> None:
        self.assertEqual(
            value("yaml", "a: &x [1, 2]\nb: *x\nc: {k: *x}\n"),
            {"a": [1, 2], "b": [1, 2], "c": {"k": [1, 2]}},
        )

    def test_an_alias_bomb_is_refused_without_expanding_it(self) -> None:
        lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
        for level in range(1, 8):
            refs = ", ".join([f"*a{level - 1}"] * 10)
            lines.append(f"a{level}: &a{level} [{refs}]")
        self.assertEqual(
            failure("yaml", "json", "\n".join(lines) + "\n"),
            "YAML aliases expand past 1,000,000 values",
        )

    def test_a_cyclic_alias_is_refused(self) -> None:
        self.assertEqual(
            failure("yaml", "json", "a: &x\n  b: *x\n"),
            "cyclic YAML aliases cannot be converted",
        )

    def test_yaml_nested_too_deeply_is_refused(self) -> None:
        deep = "[" * 105 + "]" * 105 + "\n"
        self.assertEqual(failure("yaml", "json", deep), "the data is nested more than 100 levels deep")

    def test_keys_that_are_not_text_become_text_with_a_note(self) -> None:
        result = run("yaml", "json", "1: a\ntrue: b\nnull: c\n2.5: d\n")
        self.assertEqual(json.loads(result.data), {"1": "a", "true": "b", "null": "c", "2.5": "d"})
        self.assertIn(
            "keys that were not text became text: 1, true, null, 2.5", result.notes
        )

    def test_one_key_note_names_the_key(self) -> None:
        result = run("yaml", "json", "1: a\n")
        self.assertEqual(
            result.notes, (YAML_NOTE, "keys that were not text became text: 1")
        )

    def test_duplicate_keys_keep_the_last_value_and_are_noted(self) -> None:
        result = run("yaml", "json", "a: 1\na: 2\n")
        self.assertEqual(json.loads(result.data), {"a": 2})
        self.assertIn("duplicate keys: a; the last value was kept", result.notes)

    def test_a_syntax_error_has_a_one_based_line_and_column(self) -> None:
        self.assertRegex(failure("yaml", "json", "a: b: c\n"), r"^invalid YAML at line 1 column 5: \S")
        self.assertRegex(failure("yaml", "json", "ok: 1\nbad: [1, 2\n"), r"^invalid YAML at line \d+ column \d+: \S")


class YamlWriteTests(unittest.TestCase):
    def test_simple_block_output(self) -> None:
        self.assertEqual(text("json", "yaml", '{"a": 1, "b": "x", "c": true, "d": null}'),
                         "a: 1\nb: x\nc: true\nd: null\n")

    def test_keys_stay_in_order(self) -> None:
        out = text("json", "yaml", '{"z": 1, "a": 2, "m": 3}')
        self.assertEqual(out, "z: 1\na: 2\nm: 3\n")

    def test_text_is_written_as_is_and_long_lines_are_not_wrapped(self) -> None:
        sentence = " ".join(["word"] * 80)
        out = text("json", "yaml", json.dumps({"s": sentence, "n": "Zoë 日本語"}, ensure_ascii=False))
        self.assertIn(sentence, out)
        self.assertEqual(len(out.splitlines()), 2)
        self.assertIn("Zoë 日本語", out)

    def test_strings_that_would_read_back_as_something_else_are_quoted(self) -> None:
        for word in ("no", "yes", "1.0", "null", "true", "0777", "~", "1"):
            with self.subTest(word=word):
                out = text("json", "yaml", json.dumps({"a": word}))
                self.assertRegex(out, rf"^a: ['\"]{re.escape(word)}['\"]\n$")

    def test_round_trip_through_yaml_keeps_every_value(self) -> None:
        original = {
            "plain": "text", "yes": "no", "num": "1.0", "nul": "null", "n": 12, "f": 1.5,
            "b": False, "z": None, "list": [1, "two", [3, {"four": 4}]], "obj": {"k": "v:colon"},
            "multi": "line one\nline two", "colon": "a: b", "hash": "# not a comment",
            "uni": "Zoë 日本語", "empty": "", "e1": [], "e2": {},
        }
        as_yaml = text("json", "yaml", json.dumps(original))
        self.assertEqual(value("yaml", as_yaml), original)

    def test_no_anchors_are_written_for_repeated_values(self) -> None:
        out = text("yaml", "yaml", "a: &x [1, 2]\nb: *x\nc: *x\n")
        self.assertNotIn("&", out)
        self.assertNotIn("*", out)
        self.assertEqual(value("yaml", out), {"a": [1, 2], "b": [1, 2], "c": [1, 2]})

    def test_a_yaml_input_converted_to_yaml_still_notes_the_loss(self) -> None:
        self.assertEqual(run("yaml", "yaml", "a: 1\n").notes, (YAML_NOTE,))


class CsvReadTests(unittest.TestCase):
    def test_the_first_row_is_the_header_and_cells_stay_text(self) -> None:
        result = run("csv", "json", "a,b\n1,2\n3,\n")
        self.assertEqual(json.loads(result.data), [{"a": "1", "b": "2"}, {"a": "3", "b": ""}])
        self.assertEqual(result.notes, (CSV_TEXT_NOTE,))

    def test_quoted_cells_hold_delimiters_quotes_and_line_breaks(self) -> None:
        data = 'a,b\n"x,y","he said ""hi"""\n"line1\nline2",z\n'
        self.assertEqual(
            value("csv", data),
            [{"a": "x,y", "b": 'he said "hi"'}, {"a": "line1\nline2", "b": "z"}],
        )

    def test_crlf_lf_and_cr_all_end_rows(self) -> None:
        expected = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
        for ending in ("\r\n", "\n", "\r"):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(value("csv", f"a,b{ending}1,2{ending}3,4{ending}"), expected)

    def test_a_line_break_inside_quotes_is_kept_as_written(self) -> None:
        self.assertEqual(value("csv", 'a\r\n"x\r\ny"\r\n'), [{"a": "x\r\ny"}])

    def test_a_missing_final_line_break_is_fine(self) -> None:
        self.assertEqual(value("csv", "a,b\n1,2"), [{"a": "1", "b": "2"}])

    def test_a_bom_is_dropped_from_the_first_name(self) -> None:
        self.assertEqual(list(value("csv", b"\xef\xbb\xbfa,b\n1,2\n")[0]), ["a", "b"])

    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual(
            value("csv", "a,b\n\n1,2\n\n\n3,4\n"),
            [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}],
        )

    def test_delimiters(self) -> None:
        for delimiter in (";", "\t", "|"):
            with self.subTest(delimiter=repr(delimiter)):
                data = delimiter.join(["a", "b"]) + "\n" + delimiter.join(["1,5", "x"]) + "\n"
                self.assertEqual(
                    value("csv", data, read=ReadOptions(delimiter=delimiter)),
                    [{"a": "1,5", "b": "x"}],
                )

    def test_no_header_gives_arrays(self) -> None:
        result = run("csv", "json", "1,2\n3,4\n", read=ReadOptions(header=False))
        self.assertEqual(json.loads(result.data), [["1", "2"], ["3", "4"]])
        self.assertEqual(result.notes, (CSV_TEXT_NOTE,))

    def test_a_header_without_rows_is_an_empty_array_with_a_note(self) -> None:
        result = run("csv", "json", "a,b\n")
        self.assertEqual(json.loads(result.data), [])
        self.assertEqual(
            result.notes,
            ("the CSV has a header but no rows, so the result is an empty array",),
        )

    def test_a_header_without_rows_is_not_noted_for_csv_output(self) -> None:
        result = run("csv", "csv", "a,b\n")
        self.assertNotIn(
            "the CSV has a header but no rows, so the result is an empty array", result.notes
        )

    def test_types_reads_numbers_booleans_and_empty_cells(self) -> None:
        result = run(
            "csv", "json", "a,b,c,d,e,f,g,h\n1,-2,3.5,1e3,true,false,,007\n",
            read=ReadOptions(types=True),
        )
        row = json.loads(result.data)[0]
        self.assertEqual(
            row, {"a": 1, "b": -2, "c": 3.5, "d": 1000.0, "e": True, "f": False, "g": None, "h": "007"}
        )
        self.assertIsInstance(row["a"], int)
        self.assertEqual(result.notes, ())

    def test_types_keeps_everything_else_as_text(self) -> None:
        row = value("csv", 'a,b,c,d,e\nTrue,"1,5",+5,.5,1.\n', read=ReadOptions(types=True))[0]
        self.assertEqual(row, {"a": "True", "b": "1,5", "c": "+5", "d": ".5", "e": "1."})

    def test_types_with_no_header(self) -> None:
        self.assertEqual(
            value("csv", "1,x,\n", read=ReadOptions(header=False, types=True)), [[1, "x", None]]
        )

    def test_names_that_repeat_are_refused(self) -> None:
        self.assertEqual(failure("csv", "json", "a,b,a\n1,2,3\n"), "CSV column names must be unique: a")

    def test_blank_names_are_refused(self) -> None:
        self.assertEqual(
            failure("csv", "json", "a,,c\n1,2,3\n"), "CSV column names must not be blank (column 2)"
        )

    def test_a_short_or_long_row_names_its_row(self) -> None:
        rows = ["a,b,c,d,e"] + ["1,2,3,4,5"] * 5 + ["1,2,3,4", "1,2,3,4,5"]
        self.assertEqual(
            failure("csv", "json", "\n".join(rows) + "\n"),
            "CSV row 7 has 4 cells; the header has 5",
        )
        self.assertEqual(
            failure("csv", "json", "a,b\n1,2,3\n"), "CSV row 2 has 3 cells; the header has 2"
        )

    def test_the_row_number_is_the_line_where_the_record_starts(self) -> None:
        data = 'a,b,c\n"x\ny\nz",2,3\n1,2\n'
        self.assertEqual(
            failure("csv", "json", data), "CSV row 5 has 2 cells; the header has 3"
        )

    def test_quote_errors_name_their_row(self) -> None:
        self.assertEqual(failure("csv", "json", 'a,b\n1,2\n"x,y\n'), "CSV quote is not closed (row 3)")
        self.assertEqual(
            failure("csv", "json", 'a,b\n"x"y,1\n'),
            "unexpected text after a closing CSV quote (row 2)",
        )

    def test_nest_turns_dotted_names_into_objects(self) -> None:
        self.assertEqual(
            value(
                "csv", "id,user.name,user.address.city\n1,Ann,Oslo\n", read=ReadOptions(nest=True)
            ),
            [{"id": "1", "user": {"name": "Ann", "address": {"city": "Oslo"}}}],
        )

    def test_nest_keeps_a_clashing_key_flat_and_says_so(self) -> None:
        result = run("csv", "json", "a,a.b\n1,2\n", read=ReadOptions(nest=True))
        self.assertEqual(json.loads(result.data), [{"a": "1", "a.b": "2"}])
        self.assertIn("keys kept flat because nesting would clash: a.b", result.notes)

    def test_without_nest_dotted_names_stay(self) -> None:
        self.assertEqual(value("csv", "a.b\n1\n"), [{"a.b": "1"}])


class ReadCellTests(unittest.TestCase):
    def test_what_becomes_what(self) -> None:
        cases = [
            ("", None), ("true", True), ("false", False),
            ("0", 0), ("-0", 0), ("12", 12), ("-12", -12),
            ("1.5", 1.5), ("-0.25", -0.25), ("1e5", 100000.0), ("2.5E-2", 0.025),
            ("123456789012345678901234567890", 123456789012345678901234567890),
        ]
        for cell, expected in cases:
            with self.subTest(cell=cell):
                result = model.read_cell(cell)
                self.assertEqual(result, expected)
                self.assertIs(type(result), type(expected))

    def test_text_that_only_looks_like_a_number_stays_text(self) -> None:
        for cell in ("007", "1,5", "+5", ".5", "5.", "1_000", "0x10", " 5", "5 ", "True", "TRUE",
                     "null", "NaN", "inf", "1e", "--1", "1.2.3"):
            with self.subTest(cell=cell):
                self.assertEqual(model.read_cell(cell), cell)


class FlattenTests(unittest.TestCase):
    def test_nested_objects_become_dotted_columns(self) -> None:
        self.assertEqual(
            dict(model.flatten({"id": 1, "user": {"name": "Ann", "geo": {"lat": 1}}, "t": [1]})),
            {"id": 1, "user.name": "Ann", "user.geo.lat": 1, "t": [1]},
        )

    def test_key_order_is_kept(self) -> None:
        self.assertEqual(list(model.flatten({"b": {"y": 1, "x": 2}, "a": 0})), ["b.y", "b.x", "a"])

    def test_an_empty_object_becomes_a_null_cell(self) -> None:
        self.assertEqual(dict(model.flatten({"a": {}, "b": 1})), {"a": None, "b": 1})

    def test_lists_are_left_alone(self) -> None:
        self.assertEqual(dict(model.flatten({"a": [{"b": 1}]})), {"a": [{"b": 1}]})

    def test_a_dotted_name_taken_by_a_real_key_is_an_error(self) -> None:
        with self.assertRaises(ConvertError) as caught:
            model.flatten({"a": {"b": 1}, "a.b": 2})
        self.assertEqual(str(caught.exception), "nested key a.b clashes with a column of the same name")

    def test_the_clash_is_found_through_convert_too(self) -> None:
        self.assertEqual(
            failure("json", "csv", '[{"a": {"b": 1}, "a.b": 2}]'),
            "nested key a.b clashes with a column of the same name",
        )
        self.assertEqual(
            failure("json", "xlsx", '[{"a.b": 2, "a": {"b": 1}}]'),
            "nested key a.b clashes with a column of the same name",
        )


class CsvWriteTests(unittest.TestCase):
    def test_lines_end_in_crlf_and_there_is_no_bom(self) -> None:
        out = run("json", "csv", '[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]').data
        self.assertEqual(out, b"a,b\r\n1,x\r\n2,y\r\n")

    def test_columns_are_every_key_in_the_order_first_seen(self) -> None:
        result = run("json", "csv", '[{"a": 1}, {"b": 2, "a": 3}, {"c": 4}]')
        self.assertEqual(result.data, b"a,b,c\r\n1,,\r\n3,2,\r\n,,4\r\n")
        self.assertIn("null and missing values became empty cells", result.notes)

    def test_null_becomes_an_empty_cell_with_a_note(self) -> None:
        result = run("json", "csv", '[{"a": null, "b": 1}]')
        self.assertEqual(result.data, b"a,b\r\n,1\r\n")
        self.assertEqual(result.notes, ("null and missing values became empty cells",))

    def test_an_empty_string_is_quoted_so_it_differs_from_null(self) -> None:
        result = run("json", "csv", '[{"a": "", "b": null}]')
        self.assertEqual(result.data, b'a,b\r\n"",\r\n')

    def test_no_note_when_nothing_is_missing(self) -> None:
        self.assertEqual(run("json", "csv", '[{"a": 1}]').notes, ())

    def test_values_are_written_by_type(self) -> None:
        self.assertEqual(
            run("json", "csv", '[{"b": true, "f": false, "i": 12345678901234567890, "x": 1.5, "s": "é"}]').data,
            "b,f,i,x,s\r\ntrue,false,12345678901234567890,1.5,é\r\n".encode("utf-8"),
        )

    def test_cells_with_delimiters_quotes_and_line_breaks_are_quoted(self) -> None:
        out = run("json", "csv", json.dumps([{"a": "x,y", "b": 'say "hi"', "c": "l1\nl2"}])).data
        self.assertEqual(out, b'a,b,c\r\n"x,y","say ""hi""","l1\nl2"\r\n')

    def test_what_is_written_reads_back(self) -> None:
        rows = [{"a": "x,y", "b": 'say "hi"', "c": "l1\nl2", "d": "é"}]
        self.assertEqual(value("csv", run("json", "csv", json.dumps(rows)).data), rows)

    def test_nested_objects_become_dotted_columns(self) -> None:
        self.assertEqual(
            run("json", "csv", '[{"id": 1, "user": {"name": "Ann", "geo": {"lat": 2}}}]').data,
            b"id,user.name,user.geo.lat\r\n1,Ann,2\r\n",
        )

    def test_arrays_become_json_text_with_a_note(self) -> None:
        result = run("json", "csv", '[{"tags": ["a", "b"], "n": [1, {"k": 2}]}]')
        row = next(csv.DictReader(io.StringIO(result.data.decode("utf-8"), newline="")))
        self.assertEqual(json.loads(row["tags"]), ["a", "b"])
        self.assertEqual(json.loads(row["n"]), [1, {"k": 2}])
        self.assertEqual(result.notes, ("arrays were written as JSON text",))

    def test_the_delimiter_applies(self) -> None:
        options = WriteOptions(delimiter=";")
        self.assertEqual(
            run("json", "csv", '[{"a": 1, "b": "x;y", "c": "p,q"}]', write=options).data,
            b'a;b;c\r\n1;"x;y";p,q\r\n',
        )
        self.assertEqual(
            run("json", "csv", '[{"a": 1, "b": 2}]', write=WriteOptions(delimiter="\t")).data,
            b"a\tb\r\n1\t2\r\n",
        )

    def test_one_object_becomes_one_row_with_a_note(self) -> None:
        result = run("json", "csv", '{"a": 1, "b": {"c": 2}}')
        self.assertEqual(result.data, b"a,b.c\r\n1,2\r\n")
        self.assertEqual(result.notes, ("the object became one row",))

    def test_an_array_of_arrays_is_rows_without_a_header(self) -> None:
        self.assertEqual(run("json", "csv", '[[1, 2], [3, "x"]]').data, b"1,2\r\n3,x\r\n")

    def test_other_shapes_are_refused(self) -> None:
        for data in ("5", '"text"', "[1, 2]", '[{"a": 1}, 2]', "null"):
            with self.subTest(data=data):
                self.assertEqual(
                    failure("json", "csv", data), "CSV needs an array of objects (or one object)"
                )

    def test_a_cell_that_could_run_as_a_formula_is_noted_once_and_not_changed(self) -> None:
        result = run("json", "csv", '[{"a": "=1+1", "b": "+x", "c": "-y", "d": "@z", "e": "  =w"}]')
        self.assertEqual(result.data, b"a,b,c,d,e\r\n=1+1,+x,-y,@z,  =w\r\n")
        self.assertEqual(result.notes.count(FORMULA_NOTE), 1)
        self.assertEqual(len(result.notes), 1)

    def test_ordinary_text_gets_no_formula_note(self) -> None:
        self.assertEqual(run("json", "csv", '[{"a": "a=b", "b": "x-y", "c": "hello"}]').notes, ())

    def test_the_summary_counts_data_rows(self) -> None:
        self.assertEqual(run("json", "csv", '[{"a": 1}, {"a": 2}]').summary, "2 rows")

    def test_csv_round_trips_with_types_and_nest(self) -> None:
        rows = [
            {"id": 1, "user": {"name": "Ann", "vip": True}, "score": 1.5},
            {"id": 2, "user": {"name": "Bob", "vip": False}, "score": 2.25},
        ]
        written = run("json", "csv", json.dumps(rows)).data
        back = value("csv", written, read=ReadOptions(types=True, nest=True))
        self.assertEqual(back, rows)

    def test_tsv_through_the_default_delimiter(self) -> None:
        self.assertEqual(value("csv", "a\tb\n1\t2\n", read=ReadOptions(delimiter=default_delimiter("x.tsv"))),
                         [{"a": "1", "b": "2"}])


class TomlTests(unittest.TestCase):
    DOC = (
        'title = "x"\nnums = [1, 2]\n[owner]\nname = "Tom"\nborn = 1979-05-27\n'
        '[[items]]\nid = 1\n[[items]]\nid = 2\n'
    )

    def test_toml_reads_into_ordered_values(self) -> None:
        result = run("toml", "json", self.DOC)
        parsed = json.loads(result.data)
        self.assertEqual(list(parsed), ["title", "nums", "owner", "items"])
        self.assertEqual(parsed["items"], [{"id": 1}, {"id": 2}])
        self.assertEqual(parsed["owner"], {"name": "Tom", "born": "1979-05-27"})

    def test_dates_and_times_become_iso_text_with_a_note_naming_them(self) -> None:
        result = run(
            "toml", "json",
            "a = 2026-10-24\nb = 10:15:00\nc = 2026-10-24T10:15:00\nd = 2026-10-24T10:15:00+02:00\n",
        )
        parsed = json.loads(result.data)
        self.assertEqual(parsed["a"], "2026-10-24")
        self.assertEqual(parsed["b"], "10:15:00")
        self.assertEqual(parsed["c"], "2026-10-24T10:15:00")
        self.assertTrue(parsed["d"].startswith("2026-10-24T10:15:00"))
        self.assertEqual(result.notes, ("dates and times became ISO 8601 text: a, b, c, d",))

    def test_date_paths_use_the_path_notation(self) -> None:
        result = run("toml", "json", 'when = [2026-10-24]\n"a.b" = 2026-10-25\n[x]\n"a b" = 2026-10-26\n[[items]]\nd = 2026-10-27\n')
        self.assertEqual(
            result.notes,
            ('dates and times became ISO 8601 text: when[0], ["a.b"], x["a b"], items[0].d',),
        )

    def test_a_date_is_kept_as_a_date_when_toml_is_written_again(self) -> None:
        result = run("toml", "toml", "d = 2026-10-24\nt = 10:15:00\nstamp = 2026-10-24T10:15:00\n")
        parsed = tomllib.loads(result.data.decode("utf-8"))
        self.assertEqual(parsed["d"], dt.date(2026, 10, 24))
        self.assertEqual(parsed["t"], dt.time(10, 15))
        self.assertEqual(parsed["stamp"], dt.datetime(2026, 10, 24, 10, 15))
        self.assertEqual(result.notes, ())

    def test_dates_stay_dates_in_yaml_too_without_a_note(self) -> None:
        result = run("toml", "yaml", "d = 2026-10-24\n")
        self.assertEqual(result.data, b"d: 2026-10-24\n")
        self.assertEqual(result.notes, ())

    def test_dates_become_text_in_csv_and_jsonl(self) -> None:
        for target in ("csv", "jsonl"):
            with self.subTest(target=target):
                result = run("toml", target, "d = 2026-10-24\n")
                self.assertIn(b"2026-10-24", result.data)
                self.assertIn("dates and times became ISO 8601 text: d", result.notes)

    def test_a_long_path_list_is_cut_at_five_items(self) -> None:
        keys = "".join(f"d{i} = 2026-10-{20 + i}\n" for i in range(1, 8))
        note = run("toml", "json", keys).notes[0]
        self.assertTrue(note.startswith("dates and times became ISO 8601 text: d1, d2, d3, d4, d5"), note)
        self.assertTrue(note.endswith("and 2 more"), note)
        self.assertNotIn("d6", note)
        self.assertNotIn("d7", note)

    def test_five_paths_are_all_shown(self) -> None:
        keys = "".join(f"d{i} = 2026-10-{20 + i}\n" for i in range(1, 6))
        self.assertEqual(
            run("toml", "json", keys).notes,
            ("dates and times became ISO 8601 text: d1, d2, d3, d4, d5",),
        )

    def test_a_syntax_error_is_one_plain_line(self) -> None:
        message = failure("toml", "json", "a = \n")
        self.assertTrue(message.startswith("invalid TOML: "), message)
        self.assertNotIn("\n", message)

    def test_json_to_toml_reads_back_the_same(self) -> None:
        original = {
            "title": "é", "n": 1, "f": 1.5, "b": True, "list": [1, 2],
            "table": {"k": "v", "sub": {"x": 1}}, "items": [{"id": 1}, {"id": 2}],
            "date_text": "2026-10-24",
        }
        out = run("json", "toml", json.dumps(original)).data.decode("utf-8")
        self.assertEqual(tomllib.loads(out), original)

    def test_iso_date_text_stays_text_in_toml(self) -> None:
        out = text("json", "toml", '{"d": "2026-10-24"}')
        self.assertEqual(tomllib.loads(out), {"d": "2026-10-24"})
        self.assertIn('"2026-10-24"', out)

    def test_toml_needs_an_object_at_the_top(self) -> None:
        for data in ("[1]", "5", '"x"', "[{\"a\": 1}]"):
            with self.subTest(data=data):
                self.assertEqual(failure("json", "toml", data), "TOML needs an object at the top")

    def test_null_is_refused_with_its_path(self) -> None:
        self.assertEqual(
            failure("json", "toml", '{"users": [{"email": "a"}, {"email": "b"}, {"email": null}]}'),
            "TOML has no null: users[2].email",
        )
        self.assertEqual(failure("json", "toml", '{"a": {"b": null}}'), "TOML has no null: a.b")
        self.assertEqual(failure("yaml", "toml", "a:\n"), "TOML has no null: a")


class XlsxReadTests(unittest.TestCase):
    def read(self, data: bytes, **options):
        return run("xlsx", "json", data, read=ReadOptions(**options))

    def test_one_sheet_is_an_array_of_objects_with_typed_cells(self) -> None:
        result = self.read(fx.simple_workbook())
        self.assertEqual(
            json.loads(result.data),
            [
                {"name": "Ann", "age": 30, "score": 1.5, "ok": True},
                {"name": "Bob", "age": None, "score": 2, "ok": False},
            ],
        )
        self.assertEqual(result.notes, ())

    def test_a_whole_number_stored_as_a_float_is_an_integer(self) -> None:
        out = text("xlsx", "json", fx.rows_workbook([["n"], [2.0], [3.5]]))
        self.assertIn('"n": 2\n', out)
        self.assertNotIn("2.0", out)

    def test_the_header_is_the_first_row_with_data_and_empty_rows_and_columns_are_dropped(self) -> None:
        self.assertEqual(
            json.loads(self.read(fx.gaps_workbook()).data),
            [{"id": 1, "word": "one"}, {"id": 2, "word": "two"}],
        )

    def test_header_row_picks_another_row_and_says_what_was_skipped(self) -> None:
        data = fx.rows_workbook([["junk"], ["more junk"], ["id", "word"], [1, "one"]])
        result = self.read(data, header_row=3)
        self.assertEqual(json.loads(result.data), [{"id": 1, "word": "one"}])
        self.assertIn("the first rows above the header were skipped: 1-2", result.notes)

    def test_header_row_past_the_end_is_an_error(self) -> None:
        data = fx.rows_workbook([["id"], [1], [2]])
        self.assertEqual(
            failure("xlsx", "json", data, read=ReadOptions(header_row=9)),
            "--header-row 9 is past the last row (3)",
        )

    def test_no_header_gives_arrays(self) -> None:
        self.assertEqual(
            json.loads(self.read(fx.simple_workbook(), header=False).data),
            [["name", "age", "score", "ok"], ["Ann", 30, 1.5, True], ["Bob", None, 2, False]],
        )

    def test_several_visible_sheets_make_an_object_in_workbook_order(self) -> None:
        parsed = json.loads(self.read(fx.two_visible_one_hidden()).data)
        self.assertEqual(list(parsed), ["People", "Places"])
        self.assertEqual(parsed, {"People": [{"name": "Ann"}], "Places": [{"city": "Oslo"}]})

    def test_a_hidden_sheet_is_read_when_named(self) -> None:
        self.assertEqual(
            json.loads(self.read(fx.two_visible_one_hidden(), sheets=("Secret",)).data),
            [{"key": "x"}],
        )

    def test_sheet_picks_sheets_in_workbook_order(self) -> None:
        parsed = json.loads(self.read(fx.two_visible_one_hidden(), sheets=("Places", "People")).data)
        self.assertEqual(parsed, {"People": [{"name": "Ann"}], "Places": [{"city": "Oslo"}]})

    def test_an_unknown_sheet_lists_the_sheets(self) -> None:
        data = fx.rows_workbook([["a"], [1]], title="Only")
        self.assertEqual(
            failure("xlsx", "json", data, read=ReadOptions(sheets=("Nope",))),
            'no sheet named "Nope"; the sheets are: Only',
        )

    def test_a_workbook_without_data_is_an_error(self) -> None:
        self.assertEqual(failure("xlsx", "json", fx.only_empty_sheets()), "no sheet has data")

    def test_merged_cells_are_filled_with_their_value(self) -> None:
        self.assertEqual(
            json.loads(self.read(fx.merged_workbook()).data),
            [
                {"group": "g1", "value": 1},
                {"group": "g1", "value": 2},
                {"group": "g2", "value": 3},
            ],
        )

    def test_error_cells_are_kept_as_text_with_a_note(self) -> None:
        result = self.read(fx.error_and_formula_workbook())
        rows = json.loads(result.data)
        self.assertEqual(rows[0], {"key": "err", "val": "#N/A"})
        self.assertIn("cells with errors such as #N/A were kept as text: Sheet1!B2", result.notes)

    def test_a_formula_without_a_saved_value_is_null_with_a_note(self) -> None:
        result = self.read(fx.error_and_formula_workbook())
        rows = json.loads(result.data)
        self.assertEqual(rows[1], {"key": "calc", "val": None})
        self.assertEqual(rows[2], {"key": "plain", "val": 7})
        self.assertIn("formulas without a saved value became null: Sheet1!B3", result.notes)

    def test_date_time_and_duration_cells_become_iso_text(self) -> None:
        rows = json.loads(self.read(fx.temporal_workbook()).data)
        self.assertEqual(
            {row["kind"]: row["value"] for row in rows},
            {
                "date": "2026-10-24",
                "stamp": "2026-10-24T10:15:00",
                "midnight": "2026-10-24T00:00:00",
                "time": "10:15:00",
                "duration": "36:00:00",
            },
        )

    def test_blank_and_repeated_names_are_renamed_with_a_note(self) -> None:
        result = self.read(fx.named_columns_workbook(["name", "name", None, "name"]))
        self.assertEqual(
            json.loads(result.data),
            [{"name": "v1", "name (2)": "v2", "column C": "v3", "name (3)": "v4"}],
        )
        self.assertIn(
            "blank or repeated column names were renamed: name (2), column C, name (3)",
            result.notes,
        )

    def test_names_are_trimmed_and_turned_into_text(self) -> None:
        self.assertEqual(
            json.loads(self.read(fx.named_columns_workbook(["  id  ", 2024])).data),
            [{"id": "v1", "2024": "v2"}],
        )

    def test_nest_turns_dotted_names_into_objects(self) -> None:
        data = fx.rows_workbook([["id", "user.name", "user.age"], [1, "Ann", 30]])
        self.assertEqual(
            json.loads(self.read(data, nest=True).data),
            [{"id": 1, "user": {"name": "Ann", "age": 30}}],
        )

    def test_a_file_that_is_not_a_workbook_is_one_plain_line(self) -> None:
        for data in (b"just text", b"PK\x03\x04 not really"):
            with self.subTest(data=data):
                self.assertEqual(
                    failure("xlsx", "json", data), "not an Excel workbook, or a damaged one"
                )

    def test_a_workbook_can_be_written_as_any_other_format(self) -> None:
        self.assertEqual(
            run("xlsx", "csv", fx.rows_workbook([["a", "b"], [1, "x"]])).data, b"a,b\r\n1,x\r\n"
        )
        self.assertEqual(text("xlsx", "yaml", fx.rows_workbook([["a"], [1]])), "- a: 1\n")


class XlsxWriteTests(unittest.TestCase):
    def test_an_array_of_objects_is_one_sheet_called_data(self) -> None:
        from openpyxl import load_workbook

        out = run("json", "xlsx", '[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]').data
        wb = load_workbook(io.BytesIO(out))
        self.assertEqual(wb.sheetnames, ["data"])
        self.assertEqual(sheet_rows(out), [["a", "b"], [1, "x"], [2, "y"]])

    def test_the_header_is_bold_and_frozen(self) -> None:
        from openpyxl import load_workbook

        out = run("json", "xlsx", '[{"a": 1, "b": "x"}]').data
        ws = load_workbook(io.BytesIO(out)).active
        self.assertTrue(ws["A1"].font.bold)
        self.assertTrue(ws["B1"].font.bold)
        self.assertFalse(ws["A2"].font.bold)
        self.assertEqual(ws.freeze_panes, "A2")

    def test_cells_keep_their_json_type(self) -> None:
        from openpyxl import load_workbook

        out = run("json", "xlsx", '[{"i": 7, "f": 1.5, "t": true, "s": "text", "n": null}]').data
        ws = load_workbook(io.BytesIO(out)).active
        values = [ws.cell(row=2, column=c).value for c in range(1, 6)]
        self.assertEqual(values, [7, 1.5, True, "text", None])
        self.assertIs(type(values[2]), bool)
        self.assertEqual(ws.cell(row=2, column=1).data_type, "n")

    def test_an_object_of_arrays_is_one_sheet_per_key(self) -> None:
        from openpyxl import load_workbook

        data = '{"people": [{"n": "Ann"}], "places": [{"c": "Oslo"}, {"c": "Rome"}]}'
        out = run("json", "xlsx", data).data
        self.assertEqual(load_workbook(io.BytesIO(out)).sheetnames, ["people", "places"])
        self.assertEqual(sheet_rows(out, "people"), [["n"], ["Ann"]])
        self.assertEqual(sheet_rows(out, "places"), [["c"], ["Oslo"], ["Rome"]])

    def test_an_array_of_arrays_is_a_grid_without_a_header(self) -> None:
        from openpyxl import load_workbook

        out = run("json", "xlsx", '[["a", "b"], [1, 2]]').data
        ws = load_workbook(io.BytesIO(out)).active
        self.assertEqual(sheet_rows(out), [["a", "b"], [1, 2]])
        self.assertFalse(ws["A1"].font.bold)
        self.assertIsNone(ws.freeze_panes)

    def test_other_shapes_are_refused(self) -> None:
        for data in ('"text"', "5", "[1, 2]", '{"a": [1, 2]}', '{"a": [{"x": 1}], "b": 5}', "null"):
            with self.subTest(data=data):
                self.assertEqual(
                    failure("json", "xlsx", data),
                    "XLSX needs an array of objects, or an object of such arrays",
                )

    def test_nested_objects_become_dotted_columns(self) -> None:
        out = run("json", "xlsx", '[{"id": 1, "user": {"name": "Ann"}}]').data
        self.assertEqual(sheet_rows(out), [["id", "user.name"], [1, "Ann"]])

    def test_arrays_are_joined_and_noted(self) -> None:
        result = run("json", "xlsx", '[{"tags": ["a", "b"], "nums": [1, 2]}]')
        self.assertEqual(sheet_rows(result.data), [["tags", "nums"], ["a, b", "1, 2"]])
        self.assertEqual(result.notes, ('arrays were joined with ", "',))

    def test_an_array_with_objects_or_arrays_becomes_json_text(self) -> None:
        out = run("json", "xlsx", '[{"a": [{"k": 1}], "b": [[1, 2], 3]}]').data
        row = sheet_rows(out)[1]
        self.assertEqual(json.loads(row[0]), [{"k": 1}])
        self.assertEqual(json.loads(row[1]), [[1, 2], 3])

    def test_numbers_with_more_than_15_digits_become_text_so_no_digit_is_lost(self) -> None:
        result = run("json", "xlsx", '[{"ok": 999999999999999, "big": 1234567890123456789}]')
        self.assertEqual(sheet_rows(result.data)[1], [999999999999999, "1234567890123456789"])
        self.assertEqual(
            result.notes,
            (
                "numbers with more than 15 digits were written as text so that no digit is lost: [0].big",
            ),
        )

    def test_text_longer_than_an_excel_cell_is_cut(self) -> None:
        result = run("json", "xlsx", json.dumps([{"t": "x" * 40000, "u": "short"}]))
        cell = sheet_rows(result.data)[1][0]
        self.assertEqual(cell, "x" * 32767)
        self.assertIn(
            "text longer than 32,767 characters was cut to fit an Excel cell: [0].t", result.notes
        )

    def test_text_of_exactly_the_limit_is_kept_whole(self) -> None:
        result = run("json", "xlsx", json.dumps([{"t": "y" * 32767}]))
        self.assertEqual(sheet_rows(result.data)[1][0], "y" * 32767)
        self.assertEqual(result.notes, ())

    def test_sheet_names_follow_excels_rules(self) -> None:
        from openpyxl import load_workbook

        long_a = "x" * 31 + "1"
        long_b = "x" * 31 + "2"
        data = json.dumps(
            {"a/b:c*d?e[f]g\\h": [{"v": 1}], long_a: [{"v": 2}], long_b: [{"v": 3}]}
        )
        names = load_workbook(io.BytesIO(run("json", "xlsx", data).data)).sheetnames
        self.assertEqual(names[0], "a_b_c_d_e_f_g_h")
        self.assertEqual(names[1], "x" * 31)
        self.assertTrue(names[2].endswith(" (2)"), names)
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(len(name) <= 31 for name in names), names)

    def test_a_formula_like_cell_is_noted_and_keeps_its_text(self) -> None:
        result = run("json", "xlsx", '[{"a": "=1+1", "b": "ok"}]')
        self.assertEqual(sheet_rows(result.data)[1], ["=1+1", "ok"])
        self.assertEqual(result.notes, (FORMULA_NOTE,))

    def test_the_summary_counts_sheets_and_rows_with_thousands_separators(self) -> None:
        self.assertEqual(run("json", "xlsx", '[{"a": 1}, {"a": 2}]').summary, "1 sheet, 2 rows; 2 number cells")
        self.assertEqual(
            run("json", "xlsx", '{"x": [{"a": 1}], "y": [{"a": 1}, {"a": 2}]}').summary,
            "2 sheets, 3 rows; 3 number cells",
        )
        many = json.dumps([{"a": i} for i in range(1204)])
        self.assertEqual(run("json", "xlsx", many).summary, "1 sheet, 1,204 rows; 1,204 number cells")

    def test_a_workbook_written_here_reads_back_through_the_reader(self) -> None:
        original = [{"id": 1, "user": {"name": "Ann"}, "ok": True, "score": 1.5}]
        written = run("json", "xlsx", json.dumps(original)).data
        self.assertEqual(
            value("xlsx", written, read=ReadOptions(nest=True)), original
        )


class NoteRulesTests(unittest.TestCase):
    def test_a_note_appears_once_however_often_it_arises(self) -> None:
        result = run("json", "csv", '[{"a": null}, {"a": null}, {"a": 1, "b": null}]')
        self.assertEqual(len(result.notes), len(set(result.notes)))

    def test_notes_come_in_the_order_they_arose(self) -> None:
        result = run("csv", "csv", "a,b\n=x,\n")
        self.assertEqual(result.notes[0], CSV_TEXT_NOTE)
        self.assertEqual(result.notes[1:], (FORMULA_NOTE,))

    def test_a_clean_conversion_has_no_notes(self) -> None:
        self.assertEqual(run("json", "yaml", '{"a": 1}').notes, ())
        self.assertEqual(run("json", "toml", '{"a": 1}').notes, ())


class PathNotationTests(unittest.TestCase):
    def test_paths_in_messages(self) -> None:
        cases = [
            ('{"a": {"b": null}}', "TOML has no null: a.b"),
            ('{"items": [{"name": null}]}', "TOML has no null: items[0].name"),
            ('{"a.b": null}', 'TOML has no null: ["a.b"]'),
            ('{"a": {"b c": null}}', 'TOML has no null: a["b c"]'),
            ('{"m": [[1, null]]}', "TOML has no null: m[0][1]"),
            ('{"a[0]": null}', 'TOML has no null: ["a[0]"]'),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(failure("json", "toml", data), expected)

    def test_a_path_that_starts_at_a_list(self) -> None:
        self.assertEqual(failure("yaml", "xlsx", "- a: .nan\n"), "Excel has no NaN or Infinity: [0].a")


if __name__ == "__main__":
    unittest.main()

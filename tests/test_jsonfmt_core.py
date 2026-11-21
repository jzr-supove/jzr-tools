"""JSON formatting that only changes whitespace, and Python literals to JSON."""

from __future__ import annotations

import ast
import json
import unittest

from jzr_tools.core.jsonfmt import (
    JsonError,
    LiteralError,
    Style,
    check_json,
    format_json,
    python_to_json,
)

MARKER = "SECRETMARK"

DOCUMENTS = [
    '{"a":1,"b":[1,2,3],"c":{"d":null,"e":true,"f":false}}',
    '  [ 1 ,\n\t2 , [ ] , { } , "x y" ]  ',
    '{"name": "Zoë", "tags": ["日本語", "naïve"], "n": 0.5, "big": 12}',
    '[[[[1]]], {"a": {"b": {"c": [ {"d": "e"} ]}}}]',
    '{"empty": {}, "list": [], "nested": [[], {}, [[]]]}',
    '"just a string"',
    "  42  ",
    "null",
    '{\r\n  "crlf":\r\n  [1,\r\n2]\r\n}',
    '{"spaces": "a  b\u00a0c", "k": [true, false, null]}',
]


def minified(text: str) -> str:
    return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)


def pretty(text: str, indent) -> str:
    return json.dumps(json.loads(text), indent=indent, ensure_ascii=False)


def error_of(text: str) -> JsonError:
    with unittest.TestCase().assertRaises(JsonError) as caught:
        format_json(text)
    return caught.exception


class KeepAsWrittenTests(unittest.TestCase):
    def test_numbers_are_copied_exactly(self) -> None:
        text = "[1.10, 1e400, -0, 123456789012345678901234567890, 1E+2, 0.0, -1.50e-3]"
        self.assertEqual(
            format_json(text, Style(indent=None)),
            "[1.10,1e400,-0,123456789012345678901234567890,1E+2,0.0,-1.50e-3]",
        )

    def test_numbers_are_copied_exactly_in_pretty_output(self) -> None:
        self.assertEqual(
            format_json('{"a":1.10,"b":1E+2}'),
            '{\n  "a": 1.10,\n  "b": 1E+2\n}',
        )

    def test_duplicate_keys_are_kept_in_order(self) -> None:
        self.assertEqual(
            format_json('{"a":1,"b":2,"a":3,"b":4}', Style(indent=None)),
            '{"a":1,"b":2,"a":3,"b":4}',
        )

    def test_key_order_is_kept_without_sort_keys(self) -> None:
        self.assertEqual(
            format_json('{"z":1,"y":2,"x":3}', Style(indent=None)),
            '{"z":1,"y":2,"x":3}',
        )

    def test_string_escapes_are_not_decoded_or_rewritten(self) -> None:
        text = r'["\u00e9", "\/", "\u0041", "\n\t\"\\", "\ud83d\ude00"]'
        self.assertEqual(
            format_json(text, Style(indent=None)),
            r'["\u00e9","\/","\u0041","\n\t\"\\","\ud83d\ude00"]',
        )

    def test_escaped_keys_are_not_rewritten(self) -> None:
        self.assertEqual(
            format_json(r'{"\u0062":1,"\/":2}', Style(indent=None)),
            r'{"\u0062":1,"\/":2}',
        )

    def test_non_ascii_strings_are_not_escaped(self) -> None:
        self.assertEqual(
            format_json('["é", "日本語", "😀"]', Style(indent=None)),
            '["é","日本語","😀"]',
        )

    def test_whitespace_inside_strings_is_kept(self) -> None:
        self.assertEqual(format_json('[" a  b "]', Style(indent=None)), '[" a  b "]')

    def test_literals_are_copied(self) -> None:
        self.assertEqual(
            format_json("[ true , false , null ]", Style(indent=None)),
            "[true,false,null]",
        )

    def test_a_leading_bom_is_ignored(self) -> None:
        self.assertEqual(format_json("\ufeff" + '{"a":1}', Style(indent=None)), '{"a":1}')

    def test_a_bom_before_an_unformatted_value_does_not_reach_the_output(self) -> None:
        self.assertFalse(format_json("\ufeff[1]").startswith("\ufeff"))

    def test_there_is_no_final_newline_and_line_ends_are_lf(self) -> None:
        out = format_json('{\r\n"a":\r\n[1,2]\r\n}\r\n')
        self.assertEqual(out, '{\n  "a": [\n    1,\n    2\n  ]\n}')
        self.assertNotIn("\r", out)


class LayoutTests(unittest.TestCase):
    def test_default_layout_matches_json_dumps_indent_2(self) -> None:
        for text in DOCUMENTS:
            with self.subTest(text=text):
                self.assertEqual(format_json(text), pretty(text, 2))

    def test_style_default_is_two_spaces(self) -> None:
        self.assertEqual(format_json("[1]"), "[\n  1\n]")
        self.assertEqual(Style().indent, 2)
        self.assertFalse(Style().sort_keys)

    def test_indent_4_matches_json_dumps(self) -> None:
        for text in DOCUMENTS:
            with self.subTest(text=text):
                self.assertEqual(format_json(text, Style(indent=4)), pretty(text, 4))

    def test_indent_8_matches_json_dumps(self) -> None:
        text = '{"a":[1,{"b":2}]}'
        self.assertEqual(format_json(text, Style(indent=8)), pretty(text, 8))

    def test_indent_0_has_line_breaks_and_no_indentation(self) -> None:
        for text in DOCUMENTS:
            with self.subTest(text=text):
                self.assertEqual(format_json(text, Style(indent=0)), pretty(text, 0))

    def test_tab_indent_matches_json_dumps(self) -> None:
        for text in DOCUMENTS:
            with self.subTest(text=text):
                self.assertEqual(format_json(text, Style(indent="\t")), pretty(text, "\t"))

    def test_minify_has_no_whitespace_outside_strings(self) -> None:
        for text in DOCUMENTS:
            with self.subTest(text=text):
                self.assertEqual(format_json(text, Style(indent=None)), minified(text))

    def test_minify_example(self) -> None:
        self.assertEqual(
            format_json('{ "a": 1, "b": [ 1, 2 ] }', Style(indent=None)),
            '{"a":1,"b":[1,2]}',
        )

    def test_empty_containers_stay_on_one_line(self) -> None:
        for text in ("{}", "[]", "{ }", "[\n]", "[\r\n  ]"):
            with self.subTest(text=text):
                self.assertEqual(format_json(text), text.strip()[0] + text.strip()[-1])

    def test_empty_containers_nested(self) -> None:
        self.assertEqual(
            format_json('{"a":{},"b":[]}'),
            '{\n  "a": {},\n  "b": []\n}',
        )

    def test_no_line_has_trailing_spaces(self) -> None:
        out = format_json('{"a": [1, 2, {"b": []}], "c": {}}', Style(indent=4))
        for line in out.split("\n"):
            self.assertEqual(line, line.rstrip(), line)

    def test_surrounding_whitespace_is_dropped(self) -> None:
        self.assertEqual(format_json(" \t\r\n 7 \r\n"), "7")

    def test_depth_10000_has_no_recursion_error(self) -> None:
        depth = 10000
        text = "[" * depth + "]" * depth
        self.assertEqual(format_json(text, Style(indent=None)), text)
        out = format_json(text)
        # depth - 1 opening lines, the innermost "[]", depth - 1 closing lines.
        self.assertEqual(len(out.split("\n")), 2 * depth - 1)
        self.assertEqual(out.split("\n")[depth - 1], " " * 2 * (depth - 1) + "[]")

    def test_object_depth_10000_has_no_recursion_error(self) -> None:
        depth = 10000
        text = '{"a":' * depth + "1" + "}" * depth
        self.assertEqual(format_json(text, Style(indent=None)), text)
        self.assertEqual(format_json(text, Style(indent=None, sort_keys=True)), text)
        self.assertIsNone(check_json(text))


class SortKeysTests(unittest.TestCase):
    def test_keys_are_sorted(self) -> None:
        self.assertEqual(
            format_json('{"b":1,"c":2,"a":3}', Style(indent=None, sort_keys=True)),
            '{"a":3,"b":1,"c":2}',
        )

    def test_the_decoded_key_decides_the_order(self) -> None:
        self.assertEqual(
            format_json(r'{"c":1,"\u0062":2,"a":3}', Style(indent=None, sort_keys=True)),
            r'{"a":3,"\u0062":2,"c":1}',
        )

    def test_duplicates_keep_their_input_order(self) -> None:
        self.assertEqual(
            format_json(r'{"b":1,"\u0062":2,"a":3,"a":4,"b":5}', Style(indent=None, sort_keys=True)),
            r'{"a":3,"a":4,"b":1,"\u0062":2,"b":5}',
        )

    def test_nested_objects_and_objects_in_arrays_are_sorted(self) -> None:
        self.assertEqual(
            format_json('{"z":{"y":1,"x":2},"a":[{"d":1,"c":2},[{"f":0,"e":0}]]}',
                        Style(indent=None, sort_keys=True)),
            '{"a":[{"c":2,"d":1},[{"e":0,"f":0}]],"z":{"x":2,"y":1}}',
        )

    def test_array_order_is_never_changed(self) -> None:
        self.assertEqual(
            format_json("[3,1,2]", Style(indent=None, sort_keys=True)), "[3,1,2]"
        )

    def test_values_are_copied_as_written_when_sorting(self) -> None:
        self.assertEqual(
            format_json('{"b":1.10,"a":1E+2}', Style(indent=None, sort_keys=True)),
            '{"a":1E+2,"b":1.10}',
        )

    def test_sorting_with_pretty_layout(self) -> None:
        self.assertEqual(
            format_json('{"b":{"y":1,"x":2},"a":0}', Style(sort_keys=True)),
            '{\n  "a": 0,\n  "b": {\n    "x": 2,\n    "y": 1\n  }\n}',
        )

    def test_sorting_matches_json_dumps_for_ordinary_documents(self) -> None:
        text = '{"b":[{"z":1,"a":2}],"B":0,"é":1,"a":{"y":0,"x":0}}'
        expected = json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False)
        self.assertEqual(format_json(text, Style(sort_keys=True)), expected)


# (text, line, column, reason)
ERROR_CASES = [
    # the input is empty
    ("", 1, 1, "the input is empty"),
    ("  \n\t ", 1, 1, "the input is empty"),
    ("\ufeff", 1, 1, "the input is empty"),
    ("\ufeff \n", 1, 1, "the input is empty"),
    # expected a value
    ("[,1]", 1, 2, "expected a value"),
    ('{"a":}', 1, 6, "expected a value"),
    ("[1,,2]", 1, 4, "expected a value"),
    ("[\n  1,\n  :\n]", 3, 3, "expected a value"),
    # expected a string key
    ("{1:2}", 1, 2, "expected a string key"),
    ('{"a":1,2:3}', 1, 8, "expected a string key"),
    ("{true:1}", 1, 2, "expected a string key"),
    # expected ':'
    ('{"a" 1}', 1, 6, "expected ':'"),
    ('{"a"}', 1, 5, "expected ':'"),
    ('{"a",1}', 1, 5, "expected ':'"),
    # expected ',' or '}'
    ('{"a":1 "b":2}', 1, 8, "expected ',' or '}'"),
    ('{"a":1]', 1, 7, "expected ',' or '}'"),
    # expected ',' or ']'
    ("[1 2]", 1, 4, "expected ',' or ']'"),
    ("[1}", 1, 3, "expected ',' or ']'"),
    # trailing comma
    ("[1,]", 1, 4, "trailing comma"),
    ('{"a":1,}', 1, 8, "trailing comma"),
    ("[1,\n]", 2, 1, "trailing comma"),
    # unterminated string
    ('["abc', 1, 2, "unterminated string"),
    ('"abc', 1, 1, "unterminated string"),
    ('["ab\ncd"]', 1, 2, "unterminated string"),
    ('[1,\n  "abc', 2, 3, "unterminated string"),
    # invalid escape in string
    ('"a\\qb"', 1, 3, "invalid escape in string"),
    ('["\\u12G4"]', 1, 3, "invalid escape in string"),
    ('"\\x41"', 1, 2, "invalid escape in string"),
    # control character in string
    ('"a\tb"', 1, 3, "control character in string"),
    ('"a\x01b"', 1, 3, "control character in string"),
    # invalid number
    ("01", 1, 1, "invalid number"),
    ("[1.]", 1, 2, "invalid number"),
    (".5", 1, 1, "invalid number"),
    ("-", 1, 1, "invalid number"),
    ("1e", 1, 1, "invalid number"),
    ("[1e+]", 1, 2, "invalid number"),
    # unexpected character
    ("'a'", 1, 1, "unexpected character"),
    ("[NaN]", 1, 2, "unexpected character"),
    ("True", 1, 1, "unexpected character"),
    ("// note", 1, 1, "unexpected character"),
    ('{"a": @}', 1, 7, "unexpected character"),
    # unexpected end of input
    ("[", 1, 2, "unexpected end of input"),
    ("{", 1, 2, "unexpected end of input"),
    ("[1", 1, 3, "unexpected end of input"),
    ("[1,", 1, 4, "unexpected end of input"),
    ('{"a"', 1, 5, "unexpected end of input"),
    ('{"a":', 1, 6, "unexpected end of input"),
    ('{"a":1', 1, 7, "unexpected end of input"),
    ("[\n1,", 2, 3, "unexpected end of input"),
    # unexpected text after the JSON value
    ("1 2", 1, 3, "unexpected text after the JSON value"),
    ("{} []", 1, 4, "unexpected text after the JSON value"),
    ("[]]", 1, 3, "unexpected text after the JSON value"),
    ('"a" "b"', 1, 5, "unexpected text after the JSON value"),
    ("{}\n\n  []", 3, 3, "unexpected text after the JSON value"),
    # positions: lines end at LF only, columns count characters
    ("[\r\n  1,\r\n  2 3\r\n]", 3, 5, "expected ',' or ']'"),
    ("[1,\r 2 3]", 1, 8, "expected ',' or ']'"),
    ('["é€", 1 2]', 1, 10, "expected ',' or ']'"),
    ('["😀" 1]', 1, 6, "expected ',' or ']'"),
    ('{"é": 1,\n"ü" 2}', 2, 5, "expected ':'"),
]


class ErrorTests(unittest.TestCase):
    def test_every_reason_is_reported_at_its_line_and_column(self) -> None:
        for text, line, column, reason in ERROR_CASES:
            with self.subTest(text=text):
                with self.assertRaises(JsonError) as caught:
                    format_json(text)
                error = caught.exception
                self.assertEqual((error.line, error.column, error.reason), (line, column, reason))

    def test_check_json_reports_the_same_errors(self) -> None:
        for text, line, column, reason in ERROR_CASES:
            with self.subTest(text=text):
                with self.assertRaises(JsonError) as caught:
                    check_json(text)
                error = caught.exception
                self.assertEqual((error.line, error.column, error.reason), (line, column, reason))

    def test_sort_keys_does_not_change_the_errors(self) -> None:
        for text, line, column, reason in ERROR_CASES:
            with self.subTest(text=text):
                with self.assertRaises(JsonError) as caught:
                    format_json(text, Style(indent=None, sort_keys=True))
                error = caught.exception
                self.assertEqual((error.line, error.column, error.reason), (line, column, reason))

    def test_the_text_form(self) -> None:
        error = error_of('{"a":1,}')
        self.assertEqual(str(error), "invalid JSON at line 1 column 8: trailing comma")

    def test_the_text_form_for_every_case(self) -> None:
        for text, line, column, reason in ERROR_CASES:
            with self.subTest(text=text):
                with self.assertRaises(JsonError) as caught:
                    format_json(text)
                self.assertEqual(
                    str(caught.exception),
                    f"invalid JSON at line {line} column {column}: {reason}",
                )

    def test_it_is_a_value_error(self) -> None:
        self.assertIsInstance(error_of("[1,"), ValueError)

    def test_messages_never_contain_the_input(self) -> None:
        bad = [
            f'["{MARKER}" x]',
            f'{{"{MARKER}": {MARKER}}}',
            f'"{MARKER}',
            f'"{MARKER}\\q"',
            f'["{MARKER}" "{MARKER}"]',
            f'{{"{MARKER}" 1}}',
            f"{MARKER}",
            f'"{MARKER}" {MARKER}',
            f'{{"{MARKER}":1,}}',
            f'[1.{MARKER}]',
        ]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(JsonError) as caught:
                    format_json(text)
                error = caught.exception
                self.assertNotIn(MARKER, str(error))
                self.assertNotIn(MARKER, error.reason)
                self.assertNotIn(MARKER, repr(error))

    def test_valid_input_passes_check_json(self) -> None:
        for text in DOCUMENTS + ["\ufeff[1]", '{"a":1.10,"a":2}', "1e400"]:
            with self.subTest(text=text):
                self.assertIsNone(check_json(text))


class PythonLiteralTests(unittest.TestCase):
    def convert(self, text: str, style: Style = Style(indent=None)) -> str:
        return python_to_json(text, style)

    def refused(self, text: str) -> str:
        with self.assertRaises(LiteralError) as caught:
            python_to_json(text)
        return str(caught.exception)

    def test_python_constants_and_single_quotes(self) -> None:
        self.assertEqual(
            self.convert("{'a': True, 'b': None, 'c': False, 'd': 'x'}"),
            '{"a":true,"b":null,"c":false,"d":"x"}',
        )

    def test_tuples_and_lists_become_arrays(self) -> None:
        self.assertEqual(self.convert("[(1, 2), [3, (4,)], ()]"), "[[1,2],[3,[4]],[]]")

    def test_nested_structures(self) -> None:
        self.assertEqual(
            self.convert("{'a': [{'b': (None, True)}], 'c': {}}"),
            '{"a":[{"b":[null,true]}],"c":{}}',
        )

    def test_default_layout_is_two_space_pretty_json(self) -> None:
        self.assertEqual(
            python_to_json("{'a': [1, 2], 'b': {}}"),
            '{\n  "a": [\n    1,\n    2\n  ],\n  "b": {}\n}',
        )

    def test_style_is_applied(self) -> None:
        self.assertEqual(python_to_json("{'b': 1, 'a': (2,)}", Style(indent="\t", sort_keys=True)),
                         '{\n\t"a": [\n\t\t2\n\t],\n\t"b": 1\n}')
        self.assertEqual(python_to_json("{'b': 1, 'a': 2}", Style(indent=4)),
                         '{\n    "b": 1,\n    "a": 2\n}')

    def test_strings_are_not_ascii_escaped_but_are_json_escaped(self) -> None:
        self.assertEqual(self.convert("['é', 'a\"b', 'line\\n']"), '["é","a\\"b","line\\n"]')

    def test_numbers(self) -> None:
        self.assertEqual(
            self.convert("[1, -2, 1.5, 2e3, 123456789012345678901234567890]"),
            "[1,-2,1.5,2000.0,123456789012345678901234567890]",
        )

    def test_keys_are_converted_like_json_dumps(self) -> None:
        self.assertEqual(
            self.convert("{1: 'a', 2.5: 'b', None: 'c', 'k': 'd'}"),
            '{"1":"a","2.5":"b","null":"c","k":"d"}',
        )
        self.assertEqual(self.convert("{True: 1}"), '{"true":1}')
        self.assertEqual(self.convert("{False: 1}"), '{"false":1}')

    def test_a_tuple_key_is_refused(self) -> None:
        self.assertEqual(
            self.refused("{(1, 2): 3}"),
            "a dict key must be a string, number, True, False or None.",
        )

    def test_a_set_is_refused(self) -> None:
        self.assertEqual(self.refused("{1, 2}"), "a set has no JSON form.")

    def test_bytes_are_refused(self) -> None:
        self.assertEqual(self.refused("b'abc'"), "bytes have no JSON form.")
        self.assertEqual(self.refused("[b'abc']"), "bytes have no JSON form.")

    def test_complex_numbers_are_refused(self) -> None:
        self.assertEqual(self.refused("1+2j"), "a complex number has no JSON form.")

    def test_infinity_is_refused(self) -> None:
        self.assertEqual(self.refused("1e999"), "NaN and infinity have no JSON form.")
        self.assertEqual(self.refused("[-1e999]"), "NaN and infinity have no JSON form.")

    def test_calls_and_names_are_refused_not_run(self) -> None:
        expected = (
            "not a Python literal: only strings, numbers, True, False, None, "
            "lists, tuples and dicts can be read."
        )
        for text in ("datetime(2020, 1, 1)", "foo", "{'when': datetime(2020, 1, 1)}",
                     "__import__('os').getcwd()", "nan", "float('inf')", "[1, x]"):
            with self.subTest(text=text):
                self.assertEqual(self.refused(text), expected)

    def test_a_syntax_error_names_line_column_and_python_message(self) -> None:
        text = "[1,\n 2 3]"
        with self.assertRaises(SyntaxError) as syntax:
            ast.literal_eval(text)
        self.assertEqual(syntax.exception.lineno, 2)
        message = self.refused(text)
        self.assertEqual(
            message,
            f"not a Python literal at line {syntax.exception.lineno} "
            f"column {syntax.exception.offset}: {syntax.exception.msg}",
        )

    def test_an_unclosed_bracket_is_a_syntax_error_with_position(self) -> None:
        message = self.refused("{'a': 1")
        self.assertRegex(message, r"^not a Python literal at line 1 column \d+: \S")

    def test_empty_input(self) -> None:
        self.assertEqual(self.refused(""), "the input is empty")
        self.assertEqual(self.refused("  \n "), "the input is empty")

    def test_it_is_a_value_error_and_not_a_json_error(self) -> None:
        with self.assertRaises(LiteralError) as caught:
            python_to_json("foo")
        self.assertIsInstance(caught.exception, ValueError)
        self.assertNotIsInstance(caught.exception, JsonError)

    def test_messages_never_contain_the_input(self) -> None:
        bad = [
            f"foo('{MARKER}')",
            f"{{'{MARKER}': {MARKER}}}",
            f"{{('{MARKER}',): 1}}",
            f"{{'{MARKER}'}}",
            f"b'{MARKER}'",
            f"['{MARKER}' '{MARKER}' {MARKER}]",
            f"['{MARKER}'",
            f"complex('{MARKER}')",
        ]
        for text in bad:
            with self.subTest(text=text):
                self.assertNotIn(MARKER, self.refused(text))


if __name__ == "__main__":
    unittest.main()

"""Behaviour of the .env parser, the example text and the key comparison."""

from __future__ import annotations

import unittest

from jzr_tools.core.envfile import (
    Comparison,
    Entry,
    EnvError,
    compare,
    effective,
    example_text,
    last_values,
    parse_env,
)

SECRET = "TOPSECRET-MARKER-7731"

KEY_RULE = (
    "Expected KEY=value. Keys may contain letters, digits and underscores, "
    "and cannot start with a digit."
)
BACKTICK = "Backtick quoting is not supported. Use single or double quotes."
AFTER_QUOTE = "Only a comment may follow a closing quote."
UNCLOSED = "Unclosed quoted value. Add its matching closing quote."
NULS = "Remove null characters from the file."


def pairs(text: str) -> list[tuple[str, str]]:
    return [(e.key, e.value) for e in parse_env(text)]


def error_of(text: str) -> EnvError:
    try:
        parse_env(text)
    except EnvError as error:
        return error
    raise AssertionError(f"no EnvError for {text!r}")


class ParseBasicsTests(unittest.TestCase):
    def test_a_plain_assignment(self) -> None:
        self.assertEqual(parse_env("A=1"), [Entry(key="A", value="1", exported=False, line=1)])

    def test_empty_text_and_comment_only_text_have_no_entries(self) -> None:
        for text in ("", "\n", "# note\n\n   \n\t\n# TOKEN=hidden\n", "   # indented comment"):
            with self.subTest(text=text):
                self.assertEqual(parse_env(text), [])

    def test_entries_keep_file_order_and_line_numbers(self) -> None:
        entries = parse_env("# head\n\nA=1\n  # mid\nB=2\n\nC=3\n")
        self.assertEqual([(e.key, e.line) for e in entries], [("A", 3), ("B", 5), ("C", 7)])

    def test_a_leading_bom_is_dropped(self) -> None:
        self.assertEqual(pairs("﻿A=1\nB=2"), [("A", "1"), ("B", "2")])

    def test_a_bom_before_a_comment_does_not_hide_the_next_key(self) -> None:
        self.assertEqual(pairs("﻿# c\nA=1"), [("A", "1")])

    def test_crlf_line_endings(self) -> None:
        entries = parse_env("A=1\r\nB=2\r\n")
        self.assertEqual([(e.key, e.value, e.line) for e in entries], [("A", "1", 1), ("B", "2", 2)])

    def test_a_lone_cr_ends_a_line(self) -> None:
        entries = parse_env("A=1\rB=2\rC=3")
        self.assertEqual([(e.key, e.value, e.line) for e in entries], [("A", "1", 1), ("B", "2", 2), ("C", "3", 3)])

    def test_mixed_line_endings_count_each_break_once(self) -> None:
        entries = parse_env("A=1\r\nB=2\nC=3\rD=4")
        self.assertEqual([e.line for e in entries], [1, 2, 3, 4])

    def test_the_last_line_needs_no_newline(self) -> None:
        self.assertEqual(pairs("A=1\nB=2"), [("A", "1"), ("B", "2")])


class KeyRuleTests(unittest.TestCase):
    def test_valid_keys(self) -> None:
        for key in ("A", "_", "_x", "a1", "A_B_9", "lower", "__proto__", "export_path", "exportX"):
            with self.subTest(key=key):
                self.assertEqual(pairs(f"{key}=v"), [(key, "v")])

    def test_invalid_keys_are_rejected(self) -> None:
        for line in ("1BAD=value", "BAD-KEY=value", "BAD.KEY=v", "=value", "A B=1", "ÄB=1", "missing-equals"):
            with self.subTest(line=line):
                error = error_of(line)
                self.assertEqual(error.line, 1)
                self.assertEqual(error.reason, KEY_RULE)

    def test_export_without_an_assignment_is_rejected(self) -> None:
        for line in ("export A", "export", "unset A"):
            with self.subTest(line=line):
                self.assertEqual(error_of(line).reason, KEY_RULE)

    def test_export_prefix_sets_the_flag(self) -> None:
        entries = parse_env("export A=1\nB=2\n \texport\t C=3")
        self.assertEqual([(e.key, e.value, e.exported) for e in entries],
                         [("A", "1", True), ("B", "2", False), ("C", "3", True)])

    def test_a_key_called_export_is_a_key(self) -> None:
        entry = parse_env("export=1")[0]
        self.assertEqual((entry.key, entry.value, entry.exported), ("export", "1", False))

    def test_a_key_starting_with_export_is_not_an_export_prefix(self) -> None:
        entry = parse_env("exportA=1")[0]
        self.assertEqual((entry.key, entry.exported), ("exportA", False))

    def test_keys_are_case_sensitive(self) -> None:
        self.assertEqual([e.key for e in parse_env("a=1\nA=2")], ["a", "A"])

    def test_leading_whitespace_before_a_key_is_allowed(self) -> None:
        self.assertEqual(pairs("  \t A=1"), [("A", "1")])


class SpacingAndUnquotedValueTests(unittest.TestCase):
    def test_spaces_around_equals(self) -> None:
        for line in ("A=1", "A =1", "A= 1", "A = 1", "A\t=\t1", "A   =   1"):
            with self.subTest(line=line):
                self.assertEqual(pairs(line), [("A", "1")])

    def test_empty_values(self) -> None:
        for line in ("A=", "A =", "A=   ", "A= # only a comment"):
            with self.subTest(line=line):
                self.assertEqual(pairs(line), [("A", "")])

    def test_unquoted_value_is_stripped_at_the_end(self) -> None:
        self.assertEqual(pairs("A=hello world   \t"), [("A", "hello world")])

    def test_unquoted_value_stops_at_the_first_hash(self) -> None:
        self.assertEqual(pairs("A=abc#comment"), [("A", "abc")])
        self.assertEqual(pairs("A=hello=world # raw"), [("A", "hello=world")])
        self.assertEqual(pairs("A=x # y # z"), [("A", "x")])

    def test_unquoted_value_may_contain_quotes_after_its_start(self) -> None:
        self.assertEqual(pairs("A=it's"), [("A", "it's")])
        self.assertEqual(pairs('A=say "hi"'), [("A", 'say "hi"')])

    def test_values_are_never_interpreted(self) -> None:
        self.assertEqual(
            pairs("A=${B}\nC=$(whoami)\nD=a\\nb\nE=\"${B}\\n\""),
            [("A", "${B}"), ("C", "$(whoami)"), ("D", "a\\nb"), ("E", '"${B}\\n"')],
        )


class QuotedValueTests(unittest.TestCase):
    def test_quotes_are_kept_in_the_value(self) -> None:
        self.assertEqual(pairs("A=\"x y\"\nB='x y'"), [("A", '"x y"'), ("B", "'x y'")])

    def test_empty_quotes(self) -> None:
        self.assertEqual(pairs('A=""\nB=\'\''), [("A", '""'), ("B", "''")])

    def test_hash_inside_quotes_is_part_of_the_value(self) -> None:
        self.assertEqual(pairs('A=" a # b "\nB="#inner"'), [("A", '" a # b "'), ("B", '"#inner"')])

    def test_escaped_quotes_do_not_close_the_value(self) -> None:
        self.assertEqual(pairs(r'A="hello \"world\" # inside"'), [("A", r'"hello \"world\" # inside"')])
        self.assertEqual(pairs(r"B='it\'s literal'"), [("B", r"'it\'s literal'")])

    def test_an_escaped_backslash_then_a_closing_quote(self) -> None:
        self.assertEqual(pairs('C="\\\\"\nD=1'), [("C", '"\\\\"'), ("D", "1")])

    def test_the_other_kind_of_quote_inside_is_plain_text(self) -> None:
        self.assertEqual(pairs("A=\"it's\"\nB='say \"hi\"'"), [("A", "\"it's\""), ("B", "'say \"hi\"'")])

    def test_a_comment_may_follow_the_closing_quote(self) -> None:
        cases = {
            'A="x" # note': '"x"',
            'A="x"#note': '"x"',
            "A='x'   # note": "'x'",
            'A="x"   ': '"x"',
        }
        for line, value in cases.items():
            with self.subTest(line=line):
                self.assertEqual(pairs(line), [("A", value)])

    def test_spaces_inside_the_quotes_are_kept(self) -> None:
        self.assertEqual(pairs('A="  padded  "'), [("A", '"  padded  "')])

    def test_export_with_a_quoted_value(self) -> None:
        entry = parse_env('export TOKEN="x y" # note')[0]
        self.assertEqual((entry.key, entry.value, entry.exported), ("TOKEN", '"x y"', True))


class MultiLineValueTests(unittest.TestCase):
    def test_a_quoted_value_spans_lines_joined_by_newline(self) -> None:
        entries = parse_env('A="one\ntwo\nthree"\nB=1')
        self.assertEqual([(e.key, e.value) for e in entries], [("A", '"one\ntwo\nthree"'), ("B", "1")])

    def test_the_entry_line_is_the_key_line_and_later_lines_are_counted(self) -> None:
        entries = parse_env('A="one\ntwo\nthree"\nB=1')
        self.assertEqual([e.line for e in entries], [1, 4])

    def test_crlf_inside_a_quoted_value_becomes_a_newline(self) -> None:
        entries = parse_env("MULTI='one\r\n# still value\r\ntwo'\r\nNEXT=1\r\n")
        self.assertEqual(entries[0].value, "'one\n# still value\ntwo'")
        self.assertEqual([e.line for e in entries], [1, 4])

    def test_lone_cr_inside_a_quoted_value_becomes_a_newline(self) -> None:
        self.assertEqual(parse_env('A="x\ry"')[0].value, '"x\ny"')

    def test_keys_inside_a_quoted_value_are_not_entries(self) -> None:
        entries = parse_env('MULTI="line-one\nSECRET_INNER=value\nline-three"\nTOKEN=x')
        self.assertEqual([e.key for e in entries], ["MULTI", "TOKEN"])

    def test_a_comment_after_the_closing_quote_of_a_multi_line_value(self) -> None:
        entries = parse_env('A="one\ntwo" # note\nB=2')
        self.assertEqual([(e.key, e.value, e.line) for e in entries], [("A", '"one\ntwo"', 1), ("B", "2", 3)])

    def test_text_after_the_closing_quote_of_a_multi_line_value_reports_the_key_line(self) -> None:
        error = error_of('\nA="one\ntwo" trailing\n')
        self.assertEqual(error.line, 2)
        self.assertEqual(error.reason, AFTER_QUOTE)


class ErrorTests(unittest.TestCase):
    def test_backtick_value(self) -> None:
        error = error_of("A=1\nB=`cmd`")
        self.assertEqual((error.line, error.reason), (2, BACKTICK))

    def test_text_after_a_closing_quote(self) -> None:
        for source in ('A="closed" trailing', "A='closed'x", 'A="x"y'):
            with self.subTest(source=source):
                error = error_of(source)
                self.assertEqual((error.line, error.reason), (1, AFTER_QUOTE))

    def test_unclosed_quote_reports_the_key_line(self) -> None:
        error = error_of('ok=1\nA="unclosed\nmore\nlines')
        self.assertEqual((error.line, error.reason), (2, UNCLOSED))

    def test_an_escaped_closing_quote_leaves_the_value_unclosed(self) -> None:
        self.assertEqual(error_of('A="x\\"').reason, UNCLOSED)
        self.assertEqual(error_of("A='x\\'").reason, UNCLOSED)

    def test_the_first_bad_line_is_the_one_reported(self) -> None:
        error = error_of("A=1\nbad line\nB=`x`")
        self.assertEqual((error.line, error.reason), (2, KEY_RULE))

    def test_a_bad_line_after_a_multi_line_value_has_its_real_line_number(self) -> None:
        error = error_of('A="x\ny"\n\nbad line')
        self.assertEqual(error.line, 4)

    def test_nul_in_a_value_line(self) -> None:
        error = error_of("A=ok\nB=bad\0value\n")
        self.assertEqual((error.line, error.reason), (2, NULS))

    def test_nul_in_a_comment_line(self) -> None:
        error = error_of("A=ok\n# note\0\nB=2")
        self.assertEqual((error.line, error.reason), (2, NULS))

    def test_nul_inside_a_multi_line_value_reports_its_own_line(self) -> None:
        error = error_of('A="one\ntw\0o"')
        self.assertEqual((error.line, error.reason), (2, NULS))

    def test_str_of_the_error_is_line_and_reason(self) -> None:
        self.assertEqual(str(error_of("A=1\n1X=2")), f"line 2: {KEY_RULE}")
        self.assertEqual(str(error_of("A=`x`")), f"line 1: {BACKTICK}")

    def test_the_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(EnvError, ValueError))

    def test_reasons_never_contain_the_value(self) -> None:
        sources = [
            f"{SECRET}",
            f"1BAD={SECRET}",
            f"{SECRET}-KEY={SECRET}",
            f"A=`{SECRET}`",
            f'A="{SECRET}" trailing',
            f"A='{SECRET}' {SECRET}",
            f'A="{SECRET}\n{SECRET}',
            f"A={SECRET}\0{SECRET}",
            f'A="{SECRET}\0"',
            f"export {SECRET}",
        ]
        for source in sources:
            with self.subTest(source=source):
                error = error_of(source)
                self.assertNotIn(SECRET, error.reason)
                self.assertNotIn(SECRET, str(error))
                self.assertNotIn(SECRET, repr(error.args))


class ExampleTextTests(unittest.TestCase):
    def test_keys_in_first_seen_order_without_values_or_comments(self) -> None:
        text = f'# {SECRET}\nexport TOKEN="{SECRET}" # {SECRET}\nB=2\nA=3\n'
        self.assertEqual(example_text(parse_env(text)), "TOKEN=\nB=\nA=\n")

    def test_no_keys_gives_empty_text(self) -> None:
        self.assertEqual(example_text([]), "")
        self.assertEqual(example_text(parse_env("# only a comment\n\n")), "")

    def test_a_duplicate_key_is_listed_once_at_its_first_position(self) -> None:
        self.assertEqual(example_text(parse_env("A=1\nB=2\nA=3\nC=4\nB=5")), "A=\nB=\nC=\n")

    def test_multi_line_values_do_not_leak_inner_lines(self) -> None:
        text = 'MULTI="line-one\nSECRET_INNER=value\nline-three"\nTOKEN=x'
        self.assertEqual(example_text(parse_env(text)), "MULTI=\nTOKEN=\n")

    def test_empty_quoted_values_still_make_keys(self) -> None:
        self.assertEqual(example_text(parse_env('A=""\nB=\'\'\nC=\nD="#inner"')), "A=\nB=\nC=\nD=\n")

    def test_every_key_ends_with_a_newline(self) -> None:
        self.assertTrue(example_text(parse_env("A=1")).endswith("A=\n"))

    def test_the_example_is_itself_parseable_and_has_the_same_keys(self) -> None:
        source = parse_env("export A=1\nB='x'\nA=2\nC=\n")
        again = parse_env(example_text(source))
        self.assertEqual([e.key for e in again], ["A", "B", "C"])
        self.assertEqual([e.value for e in again], ["", "", ""])


class LastValuesTests(unittest.TestCase):
    def test_last_assignment_wins_and_values_are_as_written(self) -> None:
        entries = parse_env('A=old\nexport T="x y" # note\nA=new\nE=\nM="one\ntwo"\n')
        self.assertEqual(last_values(entries), {"A": "new", "T": '"x y"', "E": "", "M": '"one\ntwo"'})

    def test_keys_keep_first_seen_order(self) -> None:
        values = last_values(parse_env("B=1\nA=2\nB=3"))
        self.assertEqual(list(values), ["B", "A"])

    def test_empty(self) -> None:
        self.assertEqual(last_values([]), {})


class EffectiveTests(unittest.TestCase):
    def test_one_pair_of_matching_quotes_is_removed(self) -> None:
        self.assertEqual(effective('"x"'), "x")
        self.assertEqual(effective("'x'"), "x")

    def test_mismatched_quotes_are_unchanged(self) -> None:
        self.assertEqual(effective("\"x'"), "\"x'")
        self.assertEqual(effective("'x\""), "'x\"")

    def test_empty_quotes_give_an_empty_string(self) -> None:
        self.assertEqual(effective('""'), "")
        self.assertEqual(effective("''"), "")

    def test_only_one_pair_is_removed(self) -> None:
        self.assertEqual(effective("\"'x'\""), "'x'")
        self.assertEqual(effective('""x""'), '"x"')

    def test_unquoted_values_and_a_single_quote_character_are_unchanged(self) -> None:
        for value in ("x", "", '"', "'", 'x"', '"x', "a b"):
            with self.subTest(value=value):
                self.assertEqual(effective(value), value)

    def test_inner_text_is_kept_as_it_is(self) -> None:
        self.assertEqual(effective('" a # b "'), " a # b ")
        self.assertEqual(effective('"one\ntwo"'), "one\ntwo")
        self.assertEqual(effective(r'"say \"hi\""'), r"say \"hi\"")


class CompareTests(unittest.TestCase):
    def cmp(self, a: str, b: str) -> Comparison:
        return compare(parse_env(a), parse_env(b))

    def test_only_a_and_only_b_in_first_seen_order(self) -> None:
        result = self.cmp("Z=1\nA=1\nSHARED=1\nM=1\n", "Q=1\nSHARED=1\nB=1\n")
        self.assertEqual(result.only_a, ["Z", "A", "M"])
        self.assertEqual(result.only_b, ["Q", "B"])
        self.assertEqual(result.changed, [])

    def test_swapping_the_sides_swaps_the_lists(self) -> None:
        a, b = "Z=1\nA=1\nS=1", "Q=1\nS=1\nB=1"
        forward, backward = self.cmp(a, b), self.cmp(b, a)
        self.assertEqual(forward.only_a, backward.only_b)
        self.assertEqual(forward.only_b, backward.only_a)

    def test_a_duplicate_key_is_listed_once(self) -> None:
        result = self.cmp("A=1\nB=1\nA=2", "")
        self.assertEqual(result.only_a, ["A", "B"])

    def test_keys_are_case_sensitive(self) -> None:
        result = self.cmp("a=1\nA=1", "A=1")
        self.assertEqual(result.only_a, ["a"])
        self.assertEqual(result.only_b, [])

    def test_prototype_like_names_are_plain_keys(self) -> None:
        result = self.cmp("__proto__=one\nconstructor=two", "__proto__=x\nB=1")
        self.assertEqual(result.only_a, ["constructor"])
        self.assertEqual(result.only_b, ["B"])
        self.assertEqual(result.changed, ["__proto__"])

    def test_empty_files(self) -> None:
        result = self.cmp("", "")
        self.assertEqual((result.only_a, result.only_b, result.changed), ([], [], []))
        self.assertEqual(self.cmp("", "A=1\nB=2").only_b, ["A", "B"])

    def test_changed_lists_shared_keys_with_different_values_in_a_order(self) -> None:
        result = self.cmp("Z=1\nA=same\nB=left\nEMPTY=\nONLY_A=1", "Z=2\nA=same\nB=right\nEMPTY=v\nONLY_B=1")
        self.assertEqual(result.changed, ["Z", "B", "EMPTY"])
        self.assertEqual(result.only_a, ["ONLY_A"])
        self.assertEqual(result.only_b, ["ONLY_B"])

    def test_the_last_assignment_is_the_one_compared(self) -> None:
        result = self.cmp("A=old\nA=same", "A=same")
        self.assertEqual(result.changed, [])
        result = self.cmp("A=same\nA=old", "A=same")
        self.assertEqual(result.changed, ["A"])

    def test_one_pair_of_quotes_does_not_count_as_a_change(self) -> None:
        result = self.cmp('A="x"\nB=\'y\'\nC=z\nD=1\nD="2"', "A=x\nB=y\nC=\"z\"\nD=2")
        self.assertEqual(result.changed, [])

    def test_a_deeper_quote_difference_is_a_change(self) -> None:
        result = self.cmp("B=\"'x'\"\nC=x", "B='x'\nC=X")
        self.assertEqual(result.changed, ["B", "C"])

    def test_empty_and_empty_quotes_are_equal_but_not_equal_to_a_value(self) -> None:
        result = self.cmp('A=\nB=""\nC=\'\'', "A=''\nB=\nC=x")
        self.assertEqual(result.changed, ["C"])

    def test_a_trailing_comment_is_not_part_of_the_value(self) -> None:
        self.assertEqual(self.cmp("A=x # note", "A=x").changed, [])

    def test_missing_keys_are_never_listed_as_changed(self) -> None:
        result = self.cmp("A=1", "B=2")
        self.assertEqual(result.changed, [])

    def test_the_comparison_object_holds_lists(self) -> None:
        result = self.cmp("A=1", "B=1")
        self.assertIsInstance(result.only_a, list)
        self.assertIsInstance(result.only_b, list)
        self.assertIsInstance(result.changed, list)


if __name__ == "__main__":
    unittest.main()

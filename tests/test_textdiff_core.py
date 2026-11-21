"""Behaviour of the text diff core: decoding, line splitting, rows, pairing."""

from __future__ import annotations

import difflib
import random
import time
import unittest

from jzr_tools.core.textdiff import Line, Row, decode, diff_rows, similar, split_lines

NOTE = "\\ No newline at end of file"


def lines(*texts: str, final_newline: bool = True) -> list[Line]:
    """Lines ending with LF, except possibly the last one."""
    result = [Line(text, True) for text in texts]
    if result and not final_newline:
        result[-1] = Line(texts[-1], False)
    return result


def prefixed(rows: list[Row]) -> list[str]:
    marks = {"context": " ", "removed": "-", "added": "+"}
    return [row.text if row.kind == "hunk" else marks[row.kind] + row.text for row in rows]


def kinds(rows: list[Row]) -> list[tuple[str, str]]:
    return [(row.kind, row.text) for row in rows]


def oracle(old: list[str], new: list[str], context: int) -> list[str]:
    produced = list(difflib.unified_diff(old, new, n=context, lineterm=""))
    return produced[2:]


class DecodeTests(unittest.TestCase):
    def test_utf8_text_is_decoded(self) -> None:
        self.assertEqual(decode("héllo 日本語\n".encode("utf-8")), "héllo 日本語\n")

    def test_empty_data_is_empty_text(self) -> None:
        self.assertEqual(decode(b""), "")

    def test_a_nul_byte_means_binary(self) -> None:
        self.assertIsNone(decode(b"abc\x00def"))

    def test_invalid_utf8_means_binary(self) -> None:
        self.assertIsNone(decode(b"caf\xe9\n"))
        self.assertIsNone(decode(b"\xff\xfe"))

    def test_utf16_with_nuls_is_binary(self) -> None:
        self.assertIsNone(decode("hi".encode("utf-16")))

    def test_a_byte_order_mark_stays_in_the_text(self) -> None:
        self.assertEqual(decode(b"\xef\xbb\xbfhi\n"), "﻿hi\n")


class SplitLinesTests(unittest.TestCase):
    def test_lf_lines(self) -> None:
        self.assertEqual(split_lines("a\nb\n"), [Line("a", True), Line("b", True)])

    def test_crlf_keeps_the_carriage_return_in_the_text(self) -> None:
        self.assertEqual(split_lines("a\r\nb\r\n"), [Line("a\r", True), Line("b\r", True)])

    def test_no_final_newline_leaves_the_last_line_unterminated(self) -> None:
        self.assertEqual(split_lines("a\nb"), [Line("a", True), Line("b", False)])
        self.assertEqual(split_lines("a"), [Line("a", False)])

    def test_empty_text_has_no_lines(self) -> None:
        self.assertEqual(split_lines(""), [])

    def test_a_lone_newline_is_one_empty_line(self) -> None:
        self.assertEqual(split_lines("\n"), [Line("", True)])

    def test_blank_line_between_lines_is_kept(self) -> None:
        self.assertEqual(
            split_lines("a\n\nb\n"), [Line("a", True), Line("", True), Line("b", True)]
        )

    def test_a_lone_carriage_return_does_not_split(self) -> None:
        self.assertEqual(split_lines("a\rb\n"), [Line("a\rb", True)])

    def test_mixed_endings(self) -> None:
        self.assertEqual(
            split_lines("a\r\nb\nc"),
            [Line("a\r", True), Line("b", True), Line("c", False)],
        )


class EqualTests(unittest.TestCase):
    def test_equal_documents_give_no_rows(self) -> None:
        doc = lines("a", "b", "c")
        self.assertEqual(diff_rows(doc, list(doc)), [])

    def test_two_empty_documents_give_no_rows(self) -> None:
        self.assertEqual(diff_rows([], []), [])

    def test_equal_documents_without_final_newline_give_no_rows(self) -> None:
        self.assertEqual(
            diff_rows(lines("a", "b", final_newline=False), lines("a", "b", final_newline=False)),
            [],
        )


class UnifiedDiffOracleTests(unittest.TestCase):
    """The hunks and prefixed lines match difflib.unified_diff."""

    CONTEXTS = (0, 1, 3, 5)

    def check(self, old: list[str], new: list[str]) -> None:
        for context in self.CONTEXTS:
            with self.subTest(context=context, old=old, new=new):
                rows = diff_rows(lines(*old), lines(*new), context=context)
                self.assertEqual(prefixed(rows), oracle(old, new, context))
                self.assertNotIn("note", [row.kind for row in rows])

    def test_single_replaced_line(self) -> None:
        self.check(["a", "b", "c"], ["a", "x", "c"])

    def test_pure_insertion_and_deletion(self) -> None:
        base = [f"line number {i}" for i in range(12)]
        self.check(base, base[:6] + ["inserted"] + base[6:])
        self.check(base, base[:4] + base[5:])

    def test_everything_replaced(self) -> None:
        self.check(["one", "two"], ["completely", "different", "text here"])

    def test_empty_to_content_and_back(self) -> None:
        self.check([], ["a", "b"])
        self.check(["a", "b"], [])

    def test_repeated_lines(self) -> None:
        self.check(["a", "a", "b", "a", "a", "b"], ["a", "b", "a", "a", "a", "b", "b"])

    def test_blank_lines(self) -> None:
        self.check(["a", "", "b", "", "", "c"], ["a", "", "", "c", ""])

    def test_two_changes_far_apart_make_two_hunks(self) -> None:
        base = [f"line number {i}" for i in range(40)]
        new = list(base)
        new[3] = "#3"
        new[35] = "#35"
        for context in self.CONTEXTS:
            with self.subTest(context=context):
                rows = diff_rows(lines(*base), lines(*new), context=context)
                self.assertEqual(sum(row.kind == "hunk" for row in rows), 2)
                self.assertEqual(prefixed(rows), oracle(base, new, context))

    def test_close_changes_merge_into_one_hunk_by_context(self) -> None:
        base = [f"line number {i}" for i in range(20)]
        new = list(base)
        new[5] = "#5"
        new[9] = "#9"
        self.assertEqual(
            sum(row.kind == "hunk" for row in diff_rows(lines(*base), lines(*new), context=1)), 2
        )
        self.assertEqual(
            sum(row.kind == "hunk" for row in diff_rows(lines(*base), lines(*new), context=3)), 1
        )

    def test_randomised_documents_whose_edits_are_never_paired(self) -> None:
        for seed in range(25):
            rnd = random.Random(seed)
            base = [f"line {i:03d} of the base document text" for i in range(rnd.randint(1, 70))]
            new: list[str] = []
            for text in base:
                roll = rnd.random()
                if roll < 0.08:
                    continue
                if roll < 0.16:
                    new.append(f"#{rnd.randint(0, 99)}")
                    continue
                if roll < 0.22:
                    new.append(f"#{rnd.randint(0, 99)}")
                new.append(text)
            if base == new:
                continue
            self.check(base, new)

    def test_randomised_documents_with_paired_edits_keep_each_side_in_order(self) -> None:
        for seed in range(25):
            rnd = random.Random(1000 + seed)
            base = [f"line {i:03d} of the base document text" for i in range(rnd.randint(1, 70))]
            new = [
                text + " edited" if rnd.random() < 0.2 else text
                for text in base
                if rnd.random() > 0.05
            ]
            if base == new:
                continue
            for context in self.CONTEXTS:
                with self.subTest(seed=seed, context=context):
                    rows = diff_rows(lines(*base), lines(*new), context=context)
                    expected = oracle(base, new, context)
                    self.assertEqual(
                        [row.text for row in rows if row.kind == "hunk"],
                        [line for line in expected if line.startswith("@@")],
                    )
                    old_side = [
                        r for r in prefixed(rows) if r.startswith("@@") or r[0] in " -"
                    ]
                    new_side = [
                        r for r in prefixed(rows) if r.startswith("@@") or r[0] in " +"
                    ]
                    self.assertEqual(old_side, [line for line in expected if line.startswith(("@@", " ", "-"))])
                    self.assertEqual(new_side, [line for line in expected if line.startswith(("@@", " ", "+"))])


class HunkRangeTests(unittest.TestCase):
    def hunks(self, old: list[Line], new: list[Line], context: int = 3) -> list[str]:
        return [row.text for row in diff_rows(old, new, context=context) if row.kind == "hunk"]

    def test_one_line_each_side_leaves_the_counts_out(self) -> None:
        self.assertEqual(self.hunks(lines("a"), lines("b")), ["@@ -1 +1 @@"])

    def test_several_lines_show_start_and_count(self) -> None:
        self.assertEqual(
            self.hunks(lines("a", "b", "c"), lines("a", "x", "c")), ["@@ -1,3 +1,3 @@"]
        )

    def test_added_to_an_empty_file(self) -> None:
        self.assertEqual(self.hunks([], lines("a")), ["@@ -0,0 +1 @@"])
        self.assertEqual(self.hunks([], lines("a", "b")), ["@@ -0,0 +1,2 @@"])

    def test_everything_removed(self) -> None:
        self.assertEqual(self.hunks(lines("a"), []), ["@@ -1 +0,0 @@"])
        self.assertEqual(self.hunks(lines("a", "b"), []), ["@@ -1,2 +0,0 @@"])

    def test_zero_count_insertion_points_at_the_line_before(self) -> None:
        old = lines("a", "b", "c", "d")
        new = lines("a", "b", "c", "X", "d")
        self.assertEqual(self.hunks(old, new, context=0), ["@@ -3,0 +4 @@"])

    def test_zero_count_deletion_points_at_the_line_before(self) -> None:
        old = lines("a", "b", "c", "X", "d")
        new = lines("a", "b", "c", "d")
        self.assertEqual(self.hunks(old, new, context=0), ["@@ -4 +3,0 @@"])

    def test_context_widens_the_ranges(self) -> None:
        old = lines("a", "b", "c", "d", "e")
        new = lines("a", "b", "X", "d", "e")
        self.assertEqual(self.hunks(old, new, context=0), ["@@ -3 +3 @@"])
        self.assertEqual(self.hunks(old, new, context=1), ["@@ -2,3 +2,3 @@"])
        self.assertEqual(self.hunks(old, new, context=3), ["@@ -1,5 +1,5 @@"])


class MissingFinalNewlineTests(unittest.TestCase):
    def test_note_follows_a_removed_line_without_newline(self) -> None:
        rows = diff_rows(lines("a", "b", final_newline=False), lines("a", "c"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("context", "a"),
                ("removed", "b"),
                ("note", NOTE),
                ("added", "c"),
            ],
        )

    def test_note_follows_an_added_line_without_newline(self) -> None:
        rows = diff_rows(lines("a", "b"), lines("a", "c", final_newline=False))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("context", "a"),
                ("removed", "b"),
                ("added", "c"),
                ("note", NOTE),
            ],
        )

    def test_both_sides_without_newline_get_a_note_each(self) -> None:
        rows = diff_rows(lines("a", final_newline=False), lines("b", final_newline=False))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1 @@"),
                ("removed", "a"),
                ("note", NOTE),
                ("added", "b"),
                ("note", NOTE),
            ],
        )

    def test_a_missing_final_newline_alone_is_a_difference(self) -> None:
        rows = diff_rows(lines("a"), lines("a", final_newline=False))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1 @@"),
                ("removed", "a"),
                ("added", "a"),
                ("note", NOTE),
            ],
        )
        self.assertIsNone(rows[1].span)
        self.assertIsNone(rows[2].span)

    def test_an_added_final_newline_puts_the_note_after_the_removed_line(self) -> None:
        rows = diff_rows(lines("a", final_newline=False), lines("a"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1 @@"),
                ("removed", "a"),
                ("note", NOTE),
                ("added", "a"),
            ],
        )

    def test_note_follows_an_unterminated_context_line(self) -> None:
        rows = diff_rows(
            lines("x", "b", final_newline=False), lines("y", "b", final_newline=False)
        )
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("removed", "x"),
                ("added", "y"),
                ("context", "b"),
                ("note", NOTE),
            ],
        )

    def test_unterminated_last_line_after_a_pure_insertion(self) -> None:
        rows = diff_rows(lines("a"), lines("a", "b", final_newline=False))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1,2 @@"),
                ("context", "a"),
                ("added", "b"),
                ("note", NOTE),
            ],
        )

    def test_note_stays_with_its_line_inside_a_paired_block(self) -> None:
        rows = diff_rows(
            lines("a = 1", "b = 2", final_newline=False),
            lines("a = 3", "b = 4", final_newline=False),
        )
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("removed", "a = 1"),
                ("added", "a = 3"),
                ("removed", "b = 2"),
                ("note", NOTE),
                ("added", "b = 4"),
                ("note", NOTE),
            ],
        )


class LineEndingTests(unittest.TestCase):
    def test_crlf_and_lf_are_equal_by_default(self) -> None:
        self.assertEqual(diff_rows(split_lines("a\r\nb\r\n"), split_lines("a\nb\n")), [])
        self.assertEqual(diff_rows(split_lines("a\nb\n"), split_lines("a\r\nb\r\n")), [])

    def test_strict_eol_makes_them_different(self) -> None:
        rows = diff_rows(split_lines("a\r\nb\r\n"), split_lines("a\nb\n"), strict_eol=True)
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("removed", "a\r"),
                ("added", "a"),
                ("removed", "b\r"),
                ("added", "b"),
            ],
        )

    def test_strict_eol_changes_nothing_for_identical_endings(self) -> None:
        self.assertEqual(
            diff_rows(split_lines("a\r\nb\r\n"), split_lines("a\r\nb\r\n"), strict_eol=True), []
        )

    def test_context_lines_show_the_old_text(self) -> None:
        rows = diff_rows(split_lines("a\r\nb\r\nc\r\n"), split_lines("a\nX\nc\n"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,3 +1,3 @@"),
                ("context", "a\r"),
                ("removed", "b\r"),
                ("added", "X"),
                ("context", "c\r"),
            ],
        )

    def test_a_real_change_is_still_found_in_crlf_text(self) -> None:
        rows = diff_rows(split_lines("a\r\nb\r\n"), split_lines("a\r\nB\r\n"))
        self.assertEqual([r.kind for r in rows], ["hunk", "context", "removed", "added"])

    def test_missing_final_newline_still_differs_when_the_other_has_crlf(self) -> None:
        rows = diff_rows(split_lines("a\r\n"), split_lines("a"))
        self.assertNotEqual(rows, [])
        self.assertIn("note", [row.kind for row in rows])


class IgnoreSpaceTests(unittest.TestCase):
    def test_leading_and_trailing_spaces_are_ignored(self) -> None:
        self.assertEqual(
            diff_rows(lines("  a  ", "b"), lines("a", "  b"), ignore_space=True), []
        )

    def test_tabs_are_ignored_at_the_ends(self) -> None:
        self.assertEqual(diff_rows(lines("\ta\t"), lines("a"), ignore_space=True), [])
        self.assertEqual(diff_rows(lines(" \t a"), lines("a \t "), ignore_space=True), [])

    def test_not_ignored_without_the_option(self) -> None:
        self.assertNotEqual(diff_rows(lines("  a  "), lines("a")), [])
        self.assertNotEqual(diff_rows(lines("\ta"), lines("a")), [])

    def test_inner_spaces_still_count(self) -> None:
        self.assertNotEqual(diff_rows(lines("a b"), lines("a  b"), ignore_space=True), [])
        self.assertNotEqual(diff_rows(lines("a\tb"), lines("a b"), ignore_space=True), [])

    def test_a_real_change_shows_context_with_the_old_text(self) -> None:
        rows = diff_rows(lines("  keep  ", "x"), lines("keep", "y"), ignore_space=True)
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("context", "  keep  "),
                ("removed", "x"),
                ("added", "y"),
            ],
        )

    def test_a_trailing_carriage_return_is_whitespace_too(self) -> None:
        self.assertEqual(
            diff_rows(split_lines("a  \r\n"), split_lines("a\n"), ignore_space=True), []
        )


class SimilarTests(unittest.TestCase):
    def test_exactly_half_shared_counts(self) -> None:
        self.assertTrue(similar("abXY", "abZW"))  # prefix 2 of 4
        self.assertTrue(similar("abc123", "abcxyz"))  # prefix 3 of 6
        self.assertTrue(similar("XYcd", "ZWcd"))  # suffix 2 of 4

    def test_just_under_half_does_not_count(self) -> None:
        self.assertFalse(similar("abxyz", "abqrs"))  # prefix 2 of 5
        self.assertFalse(similar("abcdef", "abxyzq"))  # prefix 2 of 6

    def test_prefix_and_suffix_add_up(self) -> None:
        self.assertTrue(similar("abXcd", "abYcd"))  # 2 + 2 of 5
        self.assertTrue(similar("key = 1", "key = 2"))

    def test_the_longer_line_sets_the_length(self) -> None:
        self.assertTrue(similar("abc", "abcdef"))  # prefix 3 of 6
        self.assertFalse(similar("abc", "abcdefg"))  # prefix 3 of 7

    def test_prefix_and_suffix_never_overlap(self) -> None:
        self.assertTrue(similar("aa", "aaa"))
        self.assertFalse(similar("a", "aaaaa"))  # 1 of 5, not 2

    def test_identical_non_empty_strings(self) -> None:
        self.assertTrue(similar("same", "same"))

    def test_empty_strings_are_not_similar(self) -> None:
        self.assertFalse(similar("", ""))
        self.assertFalse(similar("", "a"))
        self.assertFalse(similar("a", ""))

    def test_nothing_in_common(self) -> None:
        self.assertFalse(similar("abcdefgh", "uvwxyz12"))


class PairingTests(unittest.TestCase):
    def test_a_changed_word_in_the_middle_gets_a_span_on_both_sides(self) -> None:
        rows = diff_rows(lines("alpha one two three"), lines("alpha ONE two three"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1 @@"),
                ("removed", "alpha one two three"),
                ("added", "alpha ONE two three"),
            ],
        )
        self.assertIsNone(rows[0].span)
        self.assertEqual(rows[1].span, (6, 9))
        self.assertEqual(rows[2].span, (6, 9))

    def test_span_slices_the_changed_characters(self) -> None:
        rows = diff_rows(lines("port = 80 # web"), lines("port = 81 # web"))
        removed, added = rows[1], rows[2]
        self.assertEqual(removed.text[slice(*removed.span)], "0")
        self.assertEqual(added.text[slice(*added.span)], "1")

    def test_a_pure_insertion_inside_a_line_has_no_span_on_the_shorter_side(self) -> None:
        rows = diff_rows(lines("hello world"), lines("hello big world"))
        removed, added = rows[1], rows[2]
        self.assertEqual((removed.kind, added.kind), ("removed", "added"))
        self.assertIsNone(removed.span)
        self.assertEqual(added.text[slice(*added.span)], "big ")

    def test_each_edit_is_shown_directly_above_its_replacement(self) -> None:
        rows = diff_rows(lines("a = 1", "b = 2"), lines("a = 3", "b = 4"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,2 +1,2 @@"),
                ("removed", "a = 1"),
                ("added", "a = 3"),
                ("removed", "b = 2"),
                ("added", "b = 4"),
            ],
        )
        self.assertEqual([row.span for row in rows[1:]], [(4, 5)] * 4)

    def test_an_inserted_line_before_an_edited_one_still_pairs_the_edited_one(self) -> None:
        rows = diff_rows(lines("name = bob"), lines("totally different", "name = rob"))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1 +1,2 @@"),
                ("added", "totally different"),
                ("removed", "name = bob"),
                ("added", "name = rob"),
            ],
        )
        self.assertIsNone(rows[1].span)
        self.assertEqual(rows[2].span, (7, 8))
        self.assertEqual(rows[3].span, (7, 8))

    def test_rows_never_move_across_each_other_on_one_side(self) -> None:
        old = ["alpha = 1", "beta = 2", "gamma = 3"]
        new = ["gamma = 4", "new thing here", "alpha = 9"]
        rows = diff_rows(lines(*old), lines(*new))
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,3 +1,3 @@"),
                ("added", "gamma = 4"),
                ("added", "new thing here"),
                ("removed", "alpha = 1"),
                ("added", "alpha = 9"),
                ("removed", "beta = 2"),
                ("removed", "gamma = 3"),
            ],
        )
        self.assertEqual([r.text for r in rows if r.kind == "removed"], old)
        self.assertEqual([r.text for r in rows if r.kind == "added"], new)

    def test_unpaired_removed_lines_come_before_the_pair_they_precede(self) -> None:
        rows = diff_rows(
            lines("zzzzzzzz", "key = 1", "qqqqqqqq"), lines("key = 2", "wwwwwwww")
        )
        self.assertEqual(
            kinds(rows),
            [
                ("hunk", "@@ -1,3 +1,2 @@"),
                ("removed", "zzzzzzzz"),
                ("removed", "key = 1"),
                ("added", "key = 2"),
                ("removed", "qqqqqqqq"),
                ("added", "wwwwwwww"),
            ],
        )

    def test_dissimilar_lines_get_no_span(self) -> None:
        rows = diff_rows(lines("abcdefgh"), lines("uvwxyz12"))
        self.assertEqual(
            kinds(rows),
            [("hunk", "@@ -1 +1 @@"), ("removed", "abcdefgh"), ("added", "uvwxyz12")],
        )
        self.assertTrue(all(row.span is None for row in rows))

    def test_without_a_replacement_lines_have_no_span(self) -> None:
        rows = diff_rows(lines("a", "b"), lines("a", "b", "c"))
        self.assertTrue(all(row.span is None for row in rows))

    def test_context_and_hunk_rows_have_no_span(self) -> None:
        rows = diff_rows(lines("a", "key = 1", "c"), lines("a", "key = 2", "c"))
        for row in rows:
            if row.kind in ("hunk", "context", "note"):
                self.assertIsNone(row.span)

    def test_spans_ignore_a_trailing_carriage_return(self) -> None:
        rows = diff_rows(
            split_lines("key = 1\r\n"), split_lines("key = 2\r\n"), strict_eol=True
        )
        self.assertEqual(
            kinds(rows),
            [("hunk", "@@ -1 +1 @@"), ("removed", "key = 1\r"), ("added", "key = 2\r")],
        )
        self.assertEqual(rows[1].span, (6, 7))
        self.assertEqual(rows[2].span, (6, 7))

    def test_a_carriage_return_on_one_side_only_gives_no_span(self) -> None:
        rows = diff_rows(split_lines("key = 1\r\n"), split_lines("key = 1\n"), strict_eol=True)
        self.assertEqual(
            kinds(rows),
            [("hunk", "@@ -1 +1 @@"), ("removed", "key = 1\r"), ("added", "key = 1")],
        )
        self.assertIsNone(rows[1].span)
        self.assertIsNone(rows[2].span)

    def test_pairing_looks_at_most_one_hundred_added_lines_ahead(self) -> None:
        filler = [f"zz{i}" for i in range(99)]
        within = diff_rows(lines("config.value = 1"), lines(*filler, "config.value = 2"))
        self.assertEqual(sum(row.span is not None for row in within), 2)
        beyond = diff_rows(lines("config.value = 1"), lines(*filler, "zz99", "config.value = 2"))
        self.assertTrue(all(row.span is None for row in beyond))
        self.assertEqual(
            [r.kind for r in beyond],
            ["hunk", "removed"] + ["added"] * 101,
        )

    def test_equal_lines_around_a_block_are_not_paired(self) -> None:
        rows = diff_rows(lines("same", "key = 1", "same2"), lines("same", "key = 2", "same2"))
        self.assertEqual(
            [r.kind for r in rows], ["hunk", "context", "removed", "added", "context"]
        )


class SpeedTests(unittest.TestCase):
    def test_a_few_thousand_lines_are_compared_quickly(self) -> None:
        base = [f"line {i} with some text in it" for i in range(5000)]
        new = list(base)
        new[500] = "changed early"
        new[2500] = "line 2500 with some text in it, edited"
        del new[4500]
        started = time.perf_counter()
        rows = diff_rows(lines(*base), lines(*new))
        elapsed = time.perf_counter() - started
        self.assertEqual(sum(row.kind == "hunk" for row in rows), 3)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()

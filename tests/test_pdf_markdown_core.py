"""Layout of positioned text runs into Markdown, and the link and escape helpers.

No PDF is read here: the tests feed text runs with positions straight to the
layout. A run is built so that its width is ``len(text) * size / 2``, which is
close to what a proportional font gives, and pages are 600 points wide unless
a test says otherwise.
"""

from __future__ import annotations

import unittest

from jzr_tools.core import pdf_markdown
from jzr_tools.core.pdf_markdown import TextRun, escape_markdown, layout_page, pages_to_markdown
from jzr_tools.core.pdf_text import safe_url


def run(text: str, x: float, y: float, size: float = 12, **extra) -> TextRun:  # noqa: ANN003
    """A run; ``extra`` may set width, rtl, url or bold. The only place that names fields."""
    fields = {"text": text, "x": x, "y": y, "size": size, "width": len(text) * size / 2}
    fields.update(extra)
    return TextRun(**fields)


def convert(runs: list[TextRun], headings: bool = True) -> str:
    page = layout_page(1, 600, runs)
    markdown, _empty, _complex = pages_to_markdown([page], headings=headings, page_breaks=False)
    return markdown


def convert_page(page) -> str:  # noqa: ANN001
    return pages_to_markdown([page], headings=True, page_breaks=False)[0]


class ProseAndHeadingTests(unittest.TestCase):
    def test_fragments_join_paragraphs_separate_and_sizes_give_heading_levels(self) -> None:
        result = convert(
            [
                run("Document title", 40, 40, 24),
                run("Section", 40, 80, 16),
                run("Hello", 40, 110),
                run("world", 75, 110),
                run("A wrapped line with enough body text to establish the normal size.", 40, 126),
                run("A separate paragraph.", 40, 160),
            ]
        )
        self.assertRegex(result, r"\A# Document title\n\n## Section\n\nHello world A wrapped")
        self.assertRegex(result, r"normal size\.\n\nA separate paragraph\.")

    def test_headings_can_be_switched_off(self) -> None:
        result = convert(
            [run("Title", 40, 40, 24), run("Long enough body text for normal font size.", 40, 90)],
            headings=False,
        )
        self.assertNotRegex(result, r"\A#")
        self.assertTrue(result.startswith("Title"))

    def test_a_larger_font_wrapped_sentence_stays_prose(self) -> None:
        text = convert(
            [
                run("A normal-size body paragraph establishes the baseline size.", 40, 40, 7),
                run("The venue uses the", 370, 80, 9),
                run("local timezone.", 370, 94, 9),
            ]
        )
        self.assertRegex(text, r"\n\nThe venue uses the local timezone\.\n\Z")
        self.assertNotRegex(text, r"#.*venue")

    def test_large_section_titles_containing_a_colon_remain_headings(self) -> None:
        text = convert(
            [
                run("Section: Overview", 40, 40, 24, bold=True),
                run("A normal paragraph with sufficient body text establishes the baseline size.", 40, 100),
            ]
        )
        self.assertRegex(text, r"\A# Section: Overview\n\n")

    def test_a_standalone_bold_notice_stays_apart_from_the_prose_after_it(self) -> None:
        self.assertEqual(
            convert(
                [
                    run("A prominent notice.", 40, 40, 12, bold=True),
                    run("The explanation is still", 40, 56, 12),
                    run("reflowed normally.", 40, 72, 12),
                ]
            ),
            "**A prominent notice.**\n\nThe explanation is still reflowed normally.\n",
        )

    def test_unicode_text_is_kept_and_right_to_left_fragments_are_put_in_logical_order(self) -> None:
        result = convert(
            [
                run("O\u2018zbek \u041a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430 \u65e5\u672c\u8a9e", 40, 40),
                run("\u0645\u0631\u062d\u0628\u0627", 250, 80, 12, rtl=True, width=30),
                run("\u0628\u0627\u0644\u0639\u0627\u0644\u0645", 200, 80, 12, rtl=True, width=42),
            ]
        )
        self.assertIn("O\u2018zbek \u041a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430 \u65e5\u672c\u8a9e", result)
        self.assertIn("\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645", result)


class ListTests(unittest.TestCase):
    def test_bulleted_and_numbered_lists_become_markdown_lists(self) -> None:
        self.assertEqual(
            convert(
                [
                    run("\u2022 Apples", 40, 40),
                    run("\u2022 Pears", 40, 56),
                    run("1) First", 40, 96),
                    run("2. Second", 40, 112),
                ]
            ),
            "- Apples\n- Pears\n\n1. First\n2. Second\n",
        )

    def test_a_bold_item_keeps_its_bold_after_a_detached_bullet(self) -> None:
        self.assertEqual(
            convert([run("\u2022 ", 40, 40, 12, width=10), run("Important", 50, 40, 12, bold=True)]),
            "- **Important**\n",
        )

    def test_nested_bullets_hanging_wraps_and_parent_continuations_keep_the_hierarchy(self) -> None:
        def bullet(label: str, x: float, y: float, bold: bool = False) -> list[TextRun]:
            return [run("\u2022", x, y, 12, width=4), run(label, x + 24, y, 12, bold=bold)]

        self.assertEqual(
            convert(
                [
                    *bullet("Parent", 40, 40, True),
                    run("wraps here", 64, 56),
                    *bullet("Child", 64, 72),
                    run("wraps too", 88, 88),
                    *bullet("Deep", 88, 104),
                    *bullet("Sibling", 64, 120),
                    run("A parent continuation.", 64, 136),
                    *bullet("Next", 40, 152),
                    run("A separate paragraph.", 40, 168),
                ]
            ),
            "- **Parent** wraps here\n  - Child wraps too\n    - Deep\n  - Sibling\n\n"
            "  A parent continuation.\n- Next\n\nA separate paragraph.\n",
        )

    def test_ordered_parents_keep_their_numbers_and_indent_children_past_a_two_digit_marker(self) -> None:
        text = convert(
            [
                run("10) ", 40, 40, 12, width=24),
                run("Parent", 76, 40, 12, bold=True),
                run("\u2022 ", 76, 56, 12, width=12),
                run("Child", 100, 56, 12, url="https://example.test/child"),
                run("continued link description", 100, 72),
                run("11. ", 40, 88, 12, width=24),
                run("Second parent", 76, 88),
            ]
        )
        self.assertEqual(
            text,
            "10. **Parent**\n    - [Child](https://example.test/child) continued link description\n"
            "11. Second parent\n",
        )


class ColumnTests(unittest.TestCase):
    def test_simple_columns_are_read_as_separate_bands_and_mark_the_page_complex(self) -> None:
        runs = [run("Full width section heading", 40, 20, 24, width=510)]
        for i in range(3):
            runs.append(run(f"Left {i}", 40, 60 + i * 16))
            runs.append(run(f"Right {i}", 340, 60 + i * 16))
        page = layout_page(1, 600, runs)
        self.assertTrue(page.complex)
        self.assertEqual(
            [line.runs[0].text for line in page.lines],
            ["Full width section heading", "Left 0", "Left 1", "Left 2", "Right 0", "Right 1", "Right 2"],
        )

    def test_narrow_unequal_columns_stay_inside_their_section(self) -> None:
        runs = [
            run("Full-width introduction", 40, 30, 18, width=520),
            run("Equipment summary", 40, 100, 10),
            run("Device: Example machine", 40, 115, 7.5),
            run("Payment: None", 372, 115, 7.5),
            run("Policy: Standard", 372, 126, 6.75),
            run("A notice follows the", 372, 144, 9),
            run("venue's local time.", 372, 158, 9),
            run("Next full-width section", 40, 220, 18, width=520),
        ]
        for i in range(5):
            runs.append(run(f"Equipment \u2022 Facility \u2022 Access \u2022 Detail {i}", 40, 135 + i * 8, 6.75, width=310))
        page = layout_page(1, 612, runs)
        text = convert_page(page)
        self.assertTrue(page.complex)
        self.assertLess(text.index("Detail 4"), text.index("Payment: None"))
        self.assertLess(text.index("venue's local time."), text.index("Next full-width section"))
        self.assertRegex(text, r"A notice follows the venue's local time\.")
        self.assertNotRegex(text, r"Detail \d A notice")

    def test_independent_columns_with_inline_field_values_are_not_paired_as_one_form(self) -> None:
        rows = []
        for index in range(3):
            rows.append(run(f"Name: Example {index}", 40, 40 + index * 20))
            rows.append(run(f"Status: State {index}", 350, 40 + index * 20))
        page = layout_page(1, 600, rows)
        self.assertEqual(
            ["".join(item.text for item in line.runs) for line in page.lines],
            [
                "Name: Example 0", "Name: Example 1", "Name: Example 2",
                "Status: State 0", "Status: State 1", "Status: State 2",
            ],
        )


class FieldTests(unittest.TestCase):
    def test_labelled_fields_stay_on_separate_lines_and_a_label_only_row_is_kept(self) -> None:
        text = convert(
            [
                run("Contact name:", 40, 40, 12, bold=True, width=78),
                run(" Example Visitor", 118, 40),
                run("Participants:", 40, 56, 12, bold=True, width=78),
                run(" 2 people", 118, 56),
                run("Package:", 40, 72, 12, bold=True),
                run("Breakfast included", 40, 88),
                run("A normal paragraph is still", 40, 130),
                run("reflowed across wrapped lines.", 40, 146),
            ]
        )
        self.assertEqual(
            text,
            "**Contact name:** Example Visitor  \n**Participants:** 2 people  \n"
            "**Package:**  \nBreakfast included\n\n"
            "A normal paragraph is still reflowed across wrapped lines.\n",
        )

    def test_urls_and_clock_times_are_not_taken_for_fields(self) -> None:
        self.assertEqual(
            convert([run("https://example.test/section", 40, 40), run("12:30 is the local time.", 40, 56)]),
            "https://example.test/section 12:30 is the local time.\n",
        )

    def test_widely_separated_labels_and_values_stay_paired_without_invented_emphasis(self) -> None:
        def field(label: str, value: str, y: float) -> list[TextRun]:
            return [
                run(label, 82.35, y, 16, width=len(label) * 5.4),
                run(value, 318.15, y, 16, width=len(value) * 5.4),
            ]

        text = convert(
            [
                run("A. CONTACT DETAILS", 52.75, 157.66, 18),
                *field("Contact name :", "Example Member", 184.88),
                *field("Member code :", "REG-2048", 207.84),
                *field("Home office :", "Example Center", 230.74),
                run("B. REGISTRATION DATA", 52.03, 373.01, 18),
                *field("Transaction reference :", "DEMO-203004-123456", 400.51),
                *field("Place of registration :", "Sample City", 422.47),
                *field("Registration number :", "EX-6048", 443.43),
                *field("Registration type :", "Standard", 464.38),
                *field("Allowed visits :", "Single", 487.35),
                *field("Valid from :", "03 April 2030", 510.24),
                *field("Valid until :", "30 June 2030", 533.21),
                run("Notes :", 81.91, 555.6, 16, width=38),
                run("Example conditions", 147.58, 555.6, 16, width=100),
                run("A separate confirmation message.", 52.75, 598, 16),
            ]
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "A. CONTACT DETAILS", "",
                    "Contact name : Example Member  ",
                    "Member code : REG-2048  ",
                    "Home office : Example Center", "",
                    "B. REGISTRATION DATA", "",
                    "Transaction reference : DEMO-203004-123456  ",
                    "Place of registration : Sample City  ",
                    "Registration number : EX-6048  ",
                    "Registration type : Standard  ",
                    "Allowed visits : Single  ",
                    "Valid from : 03 April 2030  ",
                    "Valid until : 30 June 2030  ",
                    "Notes : Example conditions", "",
                    "A separate confirmation message.", "",
                ]
            ),
        )

    def test_separated_values_can_wrap_and_an_empty_field_does_not_take_the_next_rows_value(self) -> None:
        def label(text: str, y: float) -> TextRun:
            return run(text, 82, y, 12, bold=True)

        def value(text: str, y: float) -> TextRun:
            return run(text, 318, y, 12)

        text = convert(
            [
                label("Record:", 40), value("DEMO-42", 40),
                label("Optional field:", 63),
                label("Department:", 86), value("Example Office", 86),
                label("Conditions:", 109), value("A long value that wraps", 109),
                value("onto another line.", 125),
                label("Status:", 148), value("Ready", 148),
                run("https://example.test/reference", 40, 190),
                run("12:30 is the local time.", 40, 206),
            ]
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "**Record:** DEMO-42  ",
                    "**Optional field:**  ",
                    "**Department:** Example Office  ",
                    "**Conditions:** A long value that wraps onto another line.  ",
                    "**Status:** Ready", "",
                    "https://example.test/reference 12:30 is the local time.", "",
                ]
            ),
        )

    def test_a_separate_sentence_under_the_value_column_is_not_added_to_a_finished_value(self) -> None:
        text = convert(
            [
                run("Record:", 82, 40), run("DEMO-42", 318, 40),
                run("Department:", 82, 63), run("Example Office", 318, 63),
                run("Conditions:", 82, 86), run("Registration complete.", 318, 86),
                run("Please retain this document.", 318, 102),
            ]
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "Record: DEMO-42  ",
                    "Department: Example Office  ",
                    "Conditions: Registration complete.", "",
                    "Please retain this document.", "",
                ]
            ),
        )


class LedgerTests(unittest.TestCase):
    def test_right_aligned_charge_rows_stay_paired_with_their_amounts_and_a_larger_total(self) -> None:
        def value(text: str, y: float, size: float = 10) -> TextRun:
            return run(text, 560 - len(text) * size / 2, y, size)

        page = layout_page(
            1,
            612,
            [
                run("CHARGES", 40, 20, 16, bold=True),
                run("1 suite", 40, 50, 10), value("USD 100.00", 50),
                run("7 % taxes", 40, 62, 10), value("USD 7.00", 62),
                run("Service charge", 40, 74, 10), value("USD 10.00", 74),
                run("Total", 40, 96, 18), value("approx. USD 117.00", 96, 18),
                run("(per person)", 40, 112, 10), value("EUR 110.00", 112),
                run("Displayed total is payable to the provider.", 40, 136, 12, bold=True),
                run("A regular explanatory paragraph follows.", 40, 150, 10),
            ],
        )
        self.assertEqual(len([line for line in page.lines if line.cells]), 5)
        text = convert_page(page)
        self.assertRegex(text, r"1 suite — USD 100\.00  \n7 % taxes — USD 7\.00  \nService charge — USD 10\.00")
        self.assertRegex(text, r"\*\*Total — approx\. USD 117\.00\*\*")
        self.assertRegex(
            text, r"\*\*Displayed total is payable to the provider\.\*\*\n\nA regular explanatory paragraph"
        )


class LinkAndEscapeTests(unittest.TestCase):
    def test_runs_of_one_link_are_grouped_and_parentheses_in_the_address_are_encoded(self) -> None:
        url = "https://example.test/a_(b)"
        self.assertEqual(safe_url(url), "https://example.test/a_%28b%29")
        self.assertEqual(
            convert([run("Example", 40, 40, 12, url=url, width=42), run("link", 86, 40, 12, url=url)]),
            "[Example link](https://example.test/a_%28b%29)\n",
        )

    def test_unsafe_addresses_leave_the_text_without_a_link(self) -> None:
        for url in ("javascript:alert(1)", "data:text/html,evil", "file:///tmp/x", "relative/path"):
            with self.subTest(url=url):
                self.assertEqual(convert([run("Click", 40, 40, 12, url=url)]), "Click\n")

    def test_markdown_syntax_in_the_source_is_escaped(self) -> None:
        self.assertEqual(
            escape_markdown("<script> **bold** [x] ![y] `z`"),
            "\\<script\\> \\*\\*bold\\*\\* \\[x\\] \\!\\[y\\] \\`z\\`",
        )
        self.assertEqual(escape_markdown("a\\b|c_d{e}#f"), "a\\\\b\\|c\\_d\\{e\\}\\#f")
        self.assertEqual(escape_markdown("plain words 1.5"), "plain words 1.5")

    def test_escaped_text_reaches_the_output(self) -> None:
        self.assertEqual(
            convert([run("<b> and *stars*", 40, 40)]),
            "\\<b\\> and \\*stars\\*\n",
        )


class SafeUrlTests(unittest.TestCase):
    def test_web_and_mail_addresses_are_kept_as_written(self) -> None:
        for url in (
            "https://example.test/path?q=1&r=2#frag",
            "http://example.test/a%20b",
            "mailto:someone@example.test",
            "https://example.test",
            "https://example.test/a|b",
        ):
            with self.subTest(url=url):
                self.assertEqual(safe_url(url), url)

    def test_parentheses_are_percent_encoded(self) -> None:
        self.assertEqual(
            safe_url("https://example.test/f(1)(2)"), "https://example.test/f%281%29%282%29"
        )

    def test_other_schemes_and_relative_paths_are_refused(self) -> None:
        for url in (
            "javascript:alert(1)",
            "data:text/html,evil",
            "file:///tmp/x",
            "ftp://example.test/x",
            "relative/path",
            "//example.test/x",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(safe_url(url))

    def test_white_space_control_characters_and_angle_brackets_are_refused(self) -> None:
        for url in (
            "https://example.test/\nsecret",
            "https://example.test/a b",
            "https://example.test/\tx",
            "https://example.test/\x7f",
            "https://example.test/\x00",
            "https://example.test/<x>",
            "https://example.test/x>",
        ):
            with self.subTest(url=repr(url)):
                self.assertIsNone(safe_url(url))

    def test_web_addresses_need_a_host(self) -> None:
        for url in ("https://", "http:///path", "https:///"):
            with self.subTest(url=url):
                self.assertIsNone(safe_url(url))

    def test_the_length_limit_is_4096_characters(self) -> None:
        prefix = "https://example.test/"
        self.assertEqual(safe_url(prefix + "a" * (4096 - len(prefix))), prefix + "a" * (4096 - len(prefix)))
        self.assertIsNone(safe_url(prefix + "a" * (4097 - len(prefix))))


class PageBreakTests(unittest.TestCase):
    def test_empty_pages_are_reported_and_page_breaks_are_optional(self) -> None:
        pages = [
            layout_page(1, 600, [run("Page one", 40, 40)]),
            layout_page(2, 600, []),
            layout_page(3, 600, [run("Page three", 40, 40)]),
        ]
        markdown, empty, _complex = pages_to_markdown(pages, headings=False, page_breaks=True)
        self.assertEqual(list(empty), [2])
        self.assertEqual(markdown, "Page one\n\n---\n\nPage three\n")

        markdown, empty, _complex = pages_to_markdown(pages, headings=False, page_breaks=False)
        self.assertEqual(list(empty), [2])
        self.assertEqual(markdown, "Page one\n\nPage three\n")

    def test_page_breaks_default_to_off_and_headings_to_on(self) -> None:
        pages = [
            layout_page(1, 600, [run("Big title", 40, 40, 24), run("Body text that is long enough to count.", 40, 90)]),
            layout_page(2, 600, [run("More body text that is long enough.", 40, 40)]),
        ]
        markdown, _empty, _complex = pages_to_markdown(pages)
        self.assertEqual(
            markdown,
            "# Big title\n\nBody text that is long enough to count.\n\nMore body text that is long enough.\n",
        )

    def test_a_document_without_any_text_fails_clearly(self) -> None:
        empty = layout_page(2, 600, [])
        with self.assertRaises(pdf_markdown.NoText):
            pages_to_markdown([empty], headings=True, page_breaks=False)
        with self.assertRaises(pdf_markdown.NoText):
            pages_to_markdown([empty, layout_page(3, 600, [])])

    def test_runs_of_only_white_space_count_as_no_text(self) -> None:
        page = layout_page(1, 600, [run("   ", 40, 40)])
        with self.assertRaises(pdf_markdown.NoText):
            pages_to_markdown([page])

    def test_the_complex_flag_is_passed_through_and_reported(self) -> None:
        plain = layout_page(1, 600, [run("Only a line.", 40, 40)])
        flagged = layout_page(2, 600, [run("Another line.", 40, 40)], complex=True)
        self.assertFalse(plain.complex)
        self.assertTrue(flagged.complex)
        self.assertFalse(pages_to_markdown([plain])[2])
        self.assertTrue(pages_to_markdown([plain, flagged])[2])


class TableTests(unittest.TestCase):
    def test_aligned_headers_and_rows_become_a_table_with_prose_kept_around_it(self) -> None:
        rows = [["Product", "Units", "State"], ["Adapter", "2", "Ready"], ["Cable", "4", "Pending"]]
        runs = [run("A paragraph before the grid.", 40, 20)]
        for index, row in enumerate(rows):
            for column, text in enumerate(row):
                runs.append(run(text, [40, 250, 400][column], 60 + index * 20, 12, bold=index == 0))
        runs.append(run("A paragraph after the grid.", 40, 136))
        self.assertEqual(
            convert(runs),
            "A paragraph before the grid.\n\n"
            "| **Product** | **Units** | **State** |\n| --- | --- | --- |\n"
            "| Adapter | 2 | Ready |\n| Cable | 4 | Pending |\n\n"
            "A paragraph after the grid.\n",
        )

    def test_two_column_numeric_tables_get_a_plain_header(self) -> None:
        rows = [["Product", "Count"], ["Apples", "2"], ["Pears", "3"]]
        runs = [
            run(text, 40 + column * 250, 40 + index * 20)
            for index, row in enumerate(rows)
            for column, text in enumerate(row)
        ]
        self.assertEqual(
            convert(runs),
            "| Product | Count |\n| --- | --- |\n| Apples | 2 |\n| Pears | 3 |\n",
        )

    def test_a_grid_without_a_header_keeps_every_data_row_under_an_empty_header(self) -> None:
        data = [["Adapter", "2", "12.50"], ["Cable", "4", "3.00"], ["Case", "1", "9.00"]]
        runs = [
            run(text, [40, 250, 400][column], 40 + index * 20)
            for index, row in enumerate(data)
            for column, text in enumerate(row)
        ]
        self.assertEqual(
            convert(runs),
            "|  |  |  |\n| --- | --- | --- |\n"
            "| Adapter | 2 | 12.50 |\n| Cable | 4 | 3.00 |\n| Case | 1 | 9.00 |\n",
        )

    def test_cells_keep_wraps_empty_values_pipes_backslashes_bold_text_and_safe_links(self) -> None:
        runs = [
            *(
                run(text, [40, 280, 420][column], 40, 12, bold=True)
                for column, text in enumerate(["Description", "Qty", "Status"])
            ),
            run("An extended description", 40, 60, 12, width=234),
            run("2", 280, 60),
            run("Open", 420, 60),
            run("continued on the next line.", 40, 74),
            run("A | B", 40, 100),
            run("Ready", 420, 100),
            run("Linked|value", 40, 120, 12, url="https://example.test/a|b", bold=True),
            run("5", 280, 120),
            run("C:\\Temp", 420, 120),
            run("A separate paragraph follows.", 40, 136),
        ]
        text = convert(runs)
        self.assertRegex(text, r"\| An extended description continued on the next line\. \| 2 \| Open \|")
        self.assertRegex(text, r"\| A \\\| B \|  \| Ready \|")
        self.assertIn("[**Linked\\|value**](https://example.test/a%7Cb)", text)
        self.assertIn("C:\\\\Temp", text)
        self.assertRegex(text, r"\n\nA separate paragraph follows\.\n\Z")

    def test_right_to_left_cells_keep_their_logical_column_order(self) -> None:
        rows = [
            ["\u0642\u064a\u0645\u0629", "\u0627\u0633\u0645"],
            ["\u0661", "\u0623\u0644\u0641"],
            ["\u0662", "\u0628\u0627\u0621"],
        ]
        runs = [
            run(text, [40, 340][column], 40 + index * 20, 12, rtl=True, bold=index == 0)
            for index, row in enumerate(rows)
            for column, text in enumerate(row)
        ]
        text = convert(runs)
        self.assertRegex(text, r"\A\| \*\*\u0627\u0633\u0645\*\* \| \*\*\u0642\u064a\u0645\u0629\*\* \|")
        self.assertIn("| \u0623\u0644\u0641 | \u0661 |", text)
        self.assertIn("| \u0628\u0627\u0621 | \u0662 |", text)

    def test_compact_monospace_tables_recover_merged_header_cells_and_keep_wrapped_rows(self) -> None:
        anchors = [58.5, 159.39, 244.15, 418.32, 495.88]

        def cell(text: str, column: int, y: float) -> TextRun:
            return run(text, anchors[column], y, 12, width=len(text) * 7.2)

        headers = ["Left label", "Next label", "Work mode", "Available", "Verified"]
        values = [
            ["Bright token", "Amberstone", "Quick setup + light", "1", "1"],
            ["Fallback", "Amberstone", "Short task + OK", "1", "1"],
            ["Current", "Riverstone", "Main use", "1", "1"],
            ["Bright token", "Cloud", "Safe plans | routines", "1", "1"],
            ["Secondary", "Contact", "Quick task followup", "1", "0"],
            ["Secondary", "Amberstone", "Longer task processing", "1", "0"],
            ["Reserve", "Horn", "Protect mode (extra", "1", "0"],
        ]
        baselines = [322.5, 360, 382.5, 420.75, 443.25, 480.75, 503.25]
        runs = [run("A paragraph before the table.", 58.5, 260)]
        runs += [cell(text, column, 300) for column, text in enumerate(headers)]
        for index, row in enumerate(values):
            runs += [cell(text, column, baselines[index]) for column, text in enumerate(row)]
        runs += [
            cell("task | steps", 2, 338.25),
            cell("standby", 0, 398.25),
            cell("+ help", 2, 458.25),
            cell("detail)", 2, 519),
            run("A separate paragraph after the table.", 58.5, 560),
        ]
        self.assertEqual(
            convert(runs),
            "\n".join(
                [
                    "A paragraph before the table.", "",
                    "| Left label | Next label | Work mode | Available | Verified |",
                    "| --- | --- | --- | --- | --- |",
                    "| Bright token | Amberstone | Quick setup + light task \\| steps | 1 | 1 |",
                    "| Fallback | Amberstone | Short task + OK | 1 | 1 |",
                    "| Current standby | Riverstone | Main use | 1 | 1 |",
                    "| Bright token | Cloud | Safe plans \\| routines | 1 | 1 |",
                    "| Secondary | Contact | Quick task followup + help | 1 | 0 |",
                    "| Secondary | Amberstone | Longer task processing | 1 | 0 |",
                    "| Reserve | Horn | Protect mode (extra detail) | 1 | 0 |", "",
                    "A separate paragraph after the table.", "",
                ]
            ),
        )

    def test_cells_that_wrap_together_stay_in_one_logical_row_with_their_numbers(self) -> None:
        anchors = [40, 180, 350, 480]

        def cell(text: str, column: int, y: float, **extra) -> TextRun:  # noqa: ANN003
            return run(text, anchors[column], y, 12, **extra)

        runs = [
            *(cell(text, column, 40, bold=True) for column, text in enumerate(["Item", "Description", "Units", "Ready"])),
            cell("Wide entry", 0, 62.5), cell("A longer description", 1, 64), cell("2", 2, 62.5), cell("1", 3, 62.5),
            cell("continued", 0, 78.25), cell("continued text", 1, 79.5),
            cell("Second entry", 0, 100.75), cell("Another description", 1, 102),
            cell("3", 2, 100.75), cell("0", 3, 100.75),
            cell("also wrapped", 0, 116.5), cell("also continued", 1, 117.75),
            cell("Final entry", 0, 139), cell("Short description", 1, 140.25), cell("4", 2, 139), cell("1", 3, 139),
            run("A paragraph outside the table.", 40, 185),
        ]
        self.assertEqual(
            convert(runs),
            "\n".join(
                [
                    "| **Item** | **Description** | **Units** | **Ready** |",
                    "| --- | --- | --- | --- |",
                    "| Wide entry continued | A longer description continued text | 2 | 1 |",
                    "| Second entry also wrapped | Another description also continued | 3 | 0 |",
                    "| Final entry | Short description | 4 | 1 |", "",
                    "A paragraph outside the table.", "",
                ]
            ),
        )

    def test_an_observed_cell_wrap_does_not_pull_a_following_paragraph_into_a_finished_cell(self) -> None:
        anchors = [40, 280, 420]

        def cell(text: str, column: int, y: float, bold: bool = False) -> TextRun:
            return run(text, anchors[column], y, 12, bold=bold)

        runs = [
            *(cell(text, column, 40, True) for column, text in enumerate(["Description", "Qty", "State"])),
            cell("An extended description", 0, 62.5), cell("2", 1, 62.5), cell("Open", 2, 62.5),
            cell("continued here.", 0, 78.25),
            cell("Second item.", 0, 100.75), cell("3", 1, 100.75), cell("Open", 2, 100.75),
            cell("Completed item.", 0, 123.25), cell("4", 1, 123.25), cell("Done", 2, 123.25),
            cell("Review results here.", 0, 139),
        ]
        self.assertEqual(
            convert(runs),
            "\n".join(
                [
                    "| **Description** | **Qty** | **State** |",
                    "| --- | --- | --- |",
                    "| An extended description continued here. | 2 | Open |",
                    "| Second item. | 3 | Open |",
                    "| Completed item. | 4 | Done |", "",
                    "Review results here.", "",
                ]
            ),
        )


class NotATableTests(unittest.TestCase):
    def test_parallel_prose_columns_are_not_guessed_into_a_table(self) -> None:
        runs = []
        for index in range(4):
            runs.append(run(f"Left {index}", 40, 40 + index * 16))
            runs.append(run(f"Middle {index}", 230, 40 + index * 16))
            runs.append(run(f"Right {index}", 430, 40 + index * 16))
        self.assertNotIn("|", convert(runs))

    def test_parallel_form_columns_are_not_guessed_into_a_table(self) -> None:
        fields = []
        for index in range(3):
            fields.append(run(f"Name: Person {index}", 40, 40 + index * 16, 12, bold=True))
            fields.append(run(f"Payment: Status {index}", 350, 40 + index * 16, 12, bold=True))
        self.assertNotIn("|", convert(fields))

    def test_two_columns_of_sentences_under_bold_headings_are_not_guessed_into_a_table(self) -> None:
        prose = [run("First column", 40, 20, 12, bold=True), run("Second column", 340, 20, 12, bold=True)]
        for index in range(3):
            prose.append(run(f"An independent sentence on the left {index}.", 40, 40 + index * 16))
            prose.append(run(f"A separate sentence on the right {index}.", 340, 40 + index * 16))
        self.assertNotIn("|", convert(prose))


if __name__ == "__main__":
    unittest.main()

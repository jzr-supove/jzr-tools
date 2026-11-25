"""Tests of the PDF core: checking inputs, page ranges, naming, and building outputs."""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from jzr_tools.core import pdf_pages, pdfdoc
from jzr_tools.core.pdfdoc import PageBox, PdfError
from tests import pdf_fixtures as fx


class TempFolderCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-pdf-core-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def check(self, name: str, data: bytes) -> pdfdoc.Checked:
        return pdfdoc.open_checked(self.write(name, data))

    def refusal(self, data: bytes) -> str:
        with self.assertRaises(PdfError) as caught:
            pdfdoc.open_checked(self.write("bad.pdf", data))
        return str(caught.exception)


def read(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


class EnvelopeTests(unittest.TestCase):
    def test_a_file_written_by_pypdf_has_an_envelope(self) -> None:
        self.assertTrue(pdfdoc.envelope_ok(fx.marked(2)))

    def test_versions_1_0_to_1_7_and_2_0_are_accepted(self) -> None:
        for version in (b"1.0", b"1.4", b"1.7", b"2.0"):
            with self.subTest(version=version):
                self.assertTrue(pdfdoc.envelope_ok(fx.with_version(version)))

    def test_other_versions_are_refused(self) -> None:
        for version in (b"1.8", b"3.0", b"0.9"):
            with self.subTest(version=version):
                self.assertFalse(pdfdoc.envelope_ok(fx.with_version(version)))

    def test_the_header_may_follow_a_short_prefix(self) -> None:
        self.assertTrue(pdfdoc.envelope_ok(b"junk" * 25 + b"\n" + fx.marked(1)))

    def test_a_header_past_the_first_kilobyte_does_not_count(self) -> None:
        self.assertFalse(pdfdoc.envelope_ok(b"x" * 2000 + b"\n" + fx.marked(1)))

    def test_whitespace_after_the_end_marker_is_fine(self) -> None:
        self.assertTrue(pdfdoc.envelope_ok(fx.marked(1).rstrip() + b" \r\n\r\n"))

    def test_text_after_the_end_marker_is_not(self) -> None:
        self.assertFalse(pdfdoc.envelope_ok(fx.marked(1) + b"trailing text"))

    def test_a_missing_end_marker_is_refused(self) -> None:
        self.assertFalse(pdfdoc.envelope_ok(fx.without_eof()))

    def test_a_text_file_is_refused(self) -> None:
        self.assertFalse(pdfdoc.envelope_ok(fx.not_a_pdf()))
        self.assertFalse(pdfdoc.envelope_ok(b""))


class OpenCheckedTests(TempFolderCase):
    def test_a_plain_file_reports_its_pages_and_no_features(self) -> None:
        source = self.write("a.pdf", fx.marked(3, rotate={2: 90}))
        checked = pdfdoc.open_checked(source)
        self.assertEqual(checked.path, source)
        self.assertEqual(
            checked.pages,
            [
                PageBox((0.0, 0.0, 110.0, 200.0), (0.0, 0.0, 110.0, 200.0), 0),
                PageBox((0.0, 0.0, 120.0, 200.0), (0.0, 0.0, 120.0, 200.0), 90),
                PageBox((0.0, 0.0, 130.0, 200.0), (0.0, 0.0, 130.0, 200.0), 0),
            ],
        )
        self.assertEqual(checked.features, ())
        self.assertEqual(checked.bad_links, 0)
        self.assertEqual(len(checked.reader.pages), 3)

    def test_boxes_and_rotation_are_taken_from_the_page_tree_when_a_page_has_none(self) -> None:
        checked = self.check("a.pdf", fx.inherited_boxes())
        self.assertEqual(len(checked.pages), 2)
        for page in checked.pages:
            self.assertEqual(page.media, (0.0, 0.0, 300.0, 400.0))
            self.assertEqual(page.crop, (0.0, 0.0, 300.0, 400.0))
            self.assertEqual(page.rotation, 90)

    def test_a_box_given_with_its_corners_swapped_is_put_in_order(self) -> None:
        checked = self.check("a.pdf", fx.reversed_media_box())
        self.assertEqual(checked.pages[0].media, (0.0, 0.0, 200.0, 300.0))

    def test_a_crop_box_inside_the_media_box_is_kept(self) -> None:
        checked = self.check("a.pdf", fx.crop_box(10, 20, 150, 250))
        self.assertEqual(checked.pages[0].media, (0.0, 0.0, 200.0, 300.0))
        self.assertEqual(checked.pages[0].crop, (10.0, 20.0, 150.0, 250.0))

    def test_rotation_is_reported_as_0_90_180_or_270(self) -> None:
        for given, expected in ((0, 0), (90, 90), (180, 180), (270, 270), (-90, 270), (360, 0), (450, 90)):
            with self.subTest(rotate=given):
                checked = self.check("a.pdf", fx.rotate(given))
                self.assertEqual(checked.pages[0].rotation, expected)

    def test_the_largest_allowed_edge_is_accepted(self) -> None:
        checked = self.check("a.pdf", fx.huge_media_box(14400))
        self.assertEqual(checked.pages[0].media, (0.0, 0.0, 14400.0, 100.0))

    def test_a_page_wider_than_14400_points_is_unsupported(self) -> None:
        self.assertEqual(self.refusal(fx.huge_media_box(14401)), pdfdoc.UNSUPPORTED)
        self.assertEqual(self.refusal(fx.huge_media_box()), "unsupported page size, scaling or rotation")

    def test_an_empty_box_is_unsupported(self) -> None:
        self.assertEqual(self.refusal(fx.empty_media_box()), pdfdoc.UNSUPPORTED)

    def test_a_crop_box_outside_the_media_box_is_unsupported(self) -> None:
        self.assertEqual(self.refusal(fx.crop_box(-10, 0, 150, 250)), pdfdoc.UNSUPPORTED)
        self.assertEqual(self.refusal(fx.crop_box(0, 0, 250, 250)), pdfdoc.UNSUPPORTED)

    def test_a_user_unit_other_than_one_is_unsupported(self) -> None:
        self.assertEqual(self.refusal(fx.user_unit(2)), pdfdoc.UNSUPPORTED)
        self.assertEqual(self.check("one.pdf", fx.user_unit(1)).pages[0].media, (0.0, 0.0, 200.0, 200.0))

    def test_a_rotation_that_is_not_a_multiple_of_90_is_unsupported(self) -> None:
        self.assertEqual(self.refusal(fx.rotate(45)), pdfdoc.UNSUPPORTED)

    def test_an_encrypted_file_is_refused_even_with_an_empty_password(self) -> None:
        self.assertEqual(self.refusal(fx.encrypted()), pdfdoc.ENCRYPTED)
        self.assertEqual(pdfdoc.ENCRYPTED, "encrypted PDFs are not supported; use an unencrypted copy")

    def test_damaged_files_are_refused_with_one_text(self) -> None:
        cases = {
            "text file": fx.not_a_pdf(),
            "cut in half": fx.truncated(),
            "no end marker": fx.without_eof(),
            "unknown version": fx.with_version(b"3.0"),
            "empty": b"",
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertEqual(self.refusal(data), "damaged or not a PDF")
        self.assertEqual(pdfdoc.DAMAGED, "damaged or not a PDF")

    def test_a_page_tree_with_the_wrong_count_is_damaged(self) -> None:
        self.assertEqual(self.refusal(fx.wrong_count(5)), pdfdoc.DAMAGED)
        self.assertEqual(self.refusal(fx.wrong_count(1)), pdfdoc.DAMAGED)

    def test_a_page_tree_with_a_cycle_is_damaged(self) -> None:
        self.assertEqual(self.refusal(fx.page_tree_cycle()), pdfdoc.DAMAGED)

    def test_a_page_whose_parent_does_not_point_back_is_damaged(self) -> None:
        self.assertEqual(self.refusal(fx.wrong_parent()), pdfdoc.DAMAGED)

    def test_a_leaf_that_is_not_a_page_is_damaged(self) -> None:
        self.assertEqual(self.refusal(fx.leaf_without_type()), pdfdoc.DAMAGED)

    def test_a_trailer_claiming_more_than_a_million_objects_is_too_complex(self) -> None:
        self.assertEqual(self.refusal(fx.with_size(2_000_000)), pdfdoc.TOO_COMPLEX)
        self.assertEqual(pdfdoc.TOO_COMPLEX, "this PDF is too complex to process safely")

    def test_the_checks_do_not_change_the_file(self) -> None:
        data = fx.with_outline()
        source = self.write("a.pdf", data)
        pdfdoc.open_checked(source)
        self.assertEqual(source.read_bytes(), data)


ORDER = [
    "structure", "annotations", "opening-view", "links-removed",
    "forms", "signatures", "active-content", "attachments",
]


class FeatureTests(TempFolderCase):
    def features(self, data: bytes) -> tuple[str, ...]:
        return self.check("a.pdf", data).features

    def test_plain_pages_text_and_images_have_no_features(self) -> None:
        for label, data in (
            ("blank", fx.marked(2)),
            ("text", fx.text_pdf(["one", "two"])),
            ("image", fx.with_image(2)),
            ("rotated", fx.marked(2, rotate={1: 90})),
            ("metadata", fx.with_metadata(Title="T", Author="A")),
        ):
            with self.subTest(label):
                self.assertEqual(self.features(data), ())

    def test_bookmarks_are_structure(self) -> None:
        self.assertEqual(self.features(fx.with_outline()), ("structure",))

    def test_a_web_link_is_an_annotation(self) -> None:
        checked = self.check("a.pdf", fx.with_link())
        self.assertEqual(checked.features, ("annotations",))
        self.assertEqual(checked.bad_links, 0)

    def test_an_open_action_is_the_opening_view(self) -> None:
        self.assertIn("opening-view", self.features(fx.with_open_action()))

    def test_a_malformed_link_is_found_and_counted(self) -> None:
        checked = self.check("a.pdf", fx.with_bad_link())
        self.assertIn("links-removed", checked.features)
        self.assertIn("annotations", checked.features)
        self.assertEqual(checked.bad_links, 1)

    def test_a_form_field_is_a_form(self) -> None:
        self.assertIn("forms", self.features(fx.with_form_field()))
        self.assertNotIn("signatures", self.features(fx.with_form_field()))

    def test_a_signature_field_is_a_signature(self) -> None:
        self.assertIn("signatures", self.features(fx.with_signature()))

    def test_a_script_action_is_active_content(self) -> None:
        self.assertIn("active-content", self.features(fx.with_javascript_action()))
        self.assertIn("active-content", self.features(fx.with_document_javascript()))

    def test_an_embedded_file_is_an_attachment(self) -> None:
        self.assertIn("attachments", self.features(fx.with_attachment()))

    def test_codes_come_in_the_fixed_order(self) -> None:
        found = self.features(fx.with_signature())
        self.assertEqual(list(found), sorted(found, key=ORDER.index))


class ParsePagesTests(unittest.TestCase):
    def usage(self, count: int) -> str:
        return f"use page numbers and ranges such as 1-3,5 (pages 1 to {count})"

    def rejected(self, text: str, count: int = 12, **options: bool) -> str:
        with self.assertRaises(ValueError) as caught:
            pdfdoc.parse_pages(text, count, **options)
        return str(caught.exception)

    def test_items_and_ranges_come_back_in_the_order_given(self) -> None:
        self.assertEqual(pdfdoc.parse_pages("1-3,5", 12), [[1, 2, 3, 5]])
        self.assertEqual(pdfdoc.parse_pages("3,1,2", 12), [[3, 1, 2]])
        self.assertEqual(pdfdoc.parse_pages("12", 12), [[12]])

    def test_spaces_around_items_are_allowed(self) -> None:
        self.assertEqual(pdfdoc.parse_pages(" 1-3 , 5 ", 12), [[1, 2, 3, 5]])

    def test_an_open_range_runs_to_the_last_page(self) -> None:
        self.assertEqual(pdfdoc.parse_pages("10-", 12), [[10, 11, 12]])
        self.assertEqual(pdfdoc.parse_pages("12-", 12), [[12]])
        self.assertEqual(pdfdoc.parse_pages("1,10-", 12), [[1, 10, 11, 12]])

    def test_groups_are_split_at_semicolons(self) -> None:
        self.assertEqual(
            pdfdoc.parse_pages("1-3;4,6;7-", 8, groups=True), [[1, 2, 3], [4, 6], [7, 8]]
        )

    def test_one_group_when_groups_are_not_asked_for(self) -> None:
        self.assertEqual(self.rejected("1-3;4", 12), self.usage(12))

    def test_a_backwards_range_runs_down_when_allowed(self) -> None:
        self.assertEqual(pdfdoc.parse_pages("12-1", 12, backwards=True), [list(range(12, 0, -1))])
        self.assertEqual(pdfdoc.parse_pages("5-3,1", 12, backwards=True), [[5, 4, 3, 1]])

    def test_a_backwards_range_is_an_error_otherwise(self) -> None:
        self.assertEqual(self.rejected("5-3"), self.usage(12))

    def test_every_malformed_text_gives_the_usage_text_with_the_page_count(self) -> None:
        bad = [
            "", " ", "0", "00", "01", "1-03", "13", "1-13", "1,,2", "1,", ",1", "a", "-3",
            "1-2-3", "1.5", "1,1", "1-3,2", "x-3",
        ]
        for text in bad:
            with self.subTest(text=text):
                self.assertEqual(self.rejected(text), self.usage(12))
        self.assertEqual(self.rejected("0", 7), self.usage(7))

    def test_an_open_range_that_starts_past_the_end_is_an_error(self) -> None:
        self.assertEqual(self.rejected("13-"), self.usage(12))

    def test_a_group_may_not_be_empty(self) -> None:
        self.assertEqual(self.rejected("1;;2", groups=True), self.usage(12))
        self.assertEqual(self.rejected("1;", groups=True), self.usage(12))

    def test_a_page_may_appear_once_within_a_group(self) -> None:
        self.assertEqual(self.rejected("1;2,2", groups=True), self.usage(12))
        self.assertEqual(self.rejected("3-5,4", 12, backwards=True), self.usage(12))

    def test_text_longer_than_4096_characters_is_refused(self) -> None:
        long = ",".join(str(i) for i in range(1, 1200))
        self.assertGreater(len(long), 4096)
        self.assertEqual(self.rejected(long, 5000), self.usage(5000))
        short = ",".join(str(i) for i in range(1, 1000))
        self.assertLessEqual(len(short), 4096)
        self.assertEqual(pdfdoc.parse_pages(short, 5000), [list(range(1, 1000))])

    def test_more_than_100_groups_are_refused(self) -> None:
        many = ";".join(str(i) for i in range(1, 102))
        self.assertEqual(self.rejected(many, 200, groups=True), self.usage(200))
        ok = ";".join(str(i) for i in range(1, 101))
        self.assertEqual(len(pdfdoc.parse_pages(ok, 200, groups=True)), 100)


class OutputNameTests(unittest.TestCase):
    def test_a_name_beside_the_source(self) -> None:
        source = Path("some") / "folder" / "report.pdf"
        self.assertEqual(pdfdoc.output_name(source, "merged"), Path("some") / "folder" / "report_merged.pdf")

    def test_an_index_is_added_with_three_digits(self) -> None:
        source = Path("a.pdf")
        self.assertEqual(pdfdoc.output_name(source, "split", index=1), Path("a_split-001.pdf"))
        self.assertEqual(pdfdoc.output_name(source, "split", index=42), Path("a_split-042.pdf"))

    def test_more_digits_can_be_asked_for(self) -> None:
        self.assertEqual(
            pdfdoc.output_name(Path("a.pdf"), "split", index=7, digits=4), Path("a_split-0007.pdf")
        )
        self.assertEqual(
            pdfdoc.output_name(Path("a.pdf"), "split", index=1234, digits=4), Path("a_split-1234.pdf")
        )

    def test_an_output_folder_replaces_the_source_folder(self) -> None:
        target = pdfdoc.output_name(Path("in") / "a.pdf", "split", index=2, outdir=Path("out"))
        self.assertEqual(target, Path("out") / "a_split-002.pdf")

    def test_the_stem_keeps_its_dots_and_the_extension_is_lowercase_pdf(self) -> None:
        self.assertEqual(pdfdoc.output_name(Path("v1.2.PDF"), "organized"), Path("v1.2_organized.pdf"))


class VerifyTests(TempFolderCase):
    def boxes(self, data: bytes) -> list[PageBox]:
        return self.check("v.pdf", data).pages

    def test_matching_boxes_pass(self) -> None:
        data = fx.marked(3, rotate={2: 180})
        self.assertIsNone(pdfdoc.verify(data, self.boxes(data)))

    def test_a_different_page_count_fails(self) -> None:
        data = fx.marked(3)
        with self.assertRaises(PdfError) as caught:
            pdfdoc.verify(data, self.boxes(data)[:2])
        self.assertEqual(str(caught.exception), "the result could not be checked; nothing written")

    def test_a_different_media_box_fails(self) -> None:
        data = fx.marked(2)
        wrong = self.boxes(data)
        wrong[1] = PageBox((0.0, 0.0, 999.0, 200.0), (0.0, 0.0, 999.0, 200.0), 0)
        with self.assertRaises(PdfError) as caught:
            pdfdoc.verify(data, wrong)
        self.assertEqual(str(caught.exception), pdfdoc.UNCHECKED)

    def test_a_different_crop_box_fails(self) -> None:
        data = fx.crop_box(10, 20, 150, 250)
        box = self.boxes(data)[0]
        wrong = [PageBox(box.media, (0.0, 0.0, 200.0, 300.0), box.rotation)]
        with self.assertRaises(PdfError):
            pdfdoc.verify(data, wrong)

    def test_a_different_rotation_fails(self) -> None:
        data = fx.marked(1, rotate={1: 90})
        box = self.boxes(data)[0]
        with self.assertRaises(PdfError):
            pdfdoc.verify(data, [PageBox(box.media, box.crop, 0)])

    def test_a_difference_below_a_thousandth_of_a_point_is_ignored(self) -> None:
        data = fx.marked(1)
        box = self.boxes(data)[0]
        near = PageBox((0.0, 0.0, box.media[2] + 0.0005, 200.0), box.crop, 0)
        self.assertIsNone(pdfdoc.verify(data, [near]))
        far = PageBox((0.0, 0.0, box.media[2] + 0.01, 200.0), box.crop, 0)
        with self.assertRaises(PdfError):
            pdfdoc.verify(data, [far])

    def test_bytes_that_are_not_a_pdf_fail(self) -> None:
        with self.assertRaises(PdfError):
            pdfdoc.verify(fx.not_a_pdf(), [PageBox((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0), 0)])


class WritePdfTests(TempFolderCase):
    def test_the_data_lands_in_the_target_and_nothing_else_is_left(self) -> None:
        target = self.root / "out.pdf"
        pdfdoc.write_pdf(target, fx.marked(2))
        self.assertEqual(os.listdir(self.root), ["out.pdf"])
        self.assertEqual(fx.markers(target.read_bytes()), [1, 2])

    def test_an_existing_target_is_replaced(self) -> None:
        target = self.write("out.pdf", b"old")
        pdfdoc.write_pdf(target, fx.marked(3))
        self.assertEqual(fx.markers(target.read_bytes()), [1, 2, 3])
        self.assertEqual(os.listdir(self.root), ["out.pdf"])


class PlanOrderTests(unittest.TestCase):
    def plan(self, count: int = 6, *, order=None, delete=None, rotate=()):  # noqa: ANN001, ANN201
        return pdfdoc_plan(count, order=order, delete=delete, rotate=tuple(rotate))

    def rejected(self, **options) -> str:  # noqa: ANN003
        with self.assertRaises(ValueError) as caught:
            self.plan(**options)
        return str(caught.exception)

    def test_no_options_keep_every_page_in_place(self) -> None:
        self.assertEqual(self.plan(4), ([1, 2, 3, 4], {}))

    def test_order_sets_the_output_and_drops_the_rest(self) -> None:
        self.assertEqual(self.plan(order="3,1"), ([3, 1], {}))
        self.assertEqual(self.plan(order="2-3"), ([2, 3], {}))

    def test_a_backwards_order_reverses(self) -> None:
        self.assertEqual(self.plan(4, order="4-1"), ([4, 3, 2, 1], {}))

    def test_delete_removes_pages_by_source_number(self) -> None:
        self.assertEqual(self.plan(5, delete="2,4-5"), ([1, 3], {}))

    def test_delete_counts_source_numbers_after_an_order(self) -> None:
        self.assertEqual(self.plan(5, order="5,4,3,2", delete="3"), ([5, 4, 2], {}))

    def test_rotate_with_pages_marks_only_those_pages(self) -> None:
        self.assertEqual(self.plan(5, rotate=["2-3:90"]), ([1, 2, 3, 4, 5], {2: 90, 3: 90}))

    def test_several_rotate_items_are_all_applied(self) -> None:
        _order, turns = self.plan(5, rotate=["1:180", "4,5:270"])
        self.assertEqual(turns, {1: 180, 4: 270, 5: 270})

    def test_rotate_without_pages_turns_every_kept_page(self) -> None:
        order, turns = self.plan(3, delete="2", rotate=["90"])
        self.assertEqual(order, [1, 3])
        self.assertEqual({page: turns[page] for page in order}, {1: 90, 3: 90})

    def test_deleting_every_page_is_an_error(self) -> None:
        self.assertEqual(self.rejected(count=3, delete="1-3"), "--delete would remove every page")
        self.assertEqual(
            self.rejected(count=3, order="1,2", delete="2,1"), "--delete would remove every page"
        )

    def test_a_bad_angle_gets_the_rotate_text(self) -> None:
        expected = "--rotate takes 90, 180 or 270, optionally after PAGES: (such as 2-4:90)"
        for item in ("45", "0", "360", "abc", "2:45", "2-4:"):
            with self.subTest(item=item):
                self.assertEqual(self.rejected(rotate=[item]), expected)

    def test_bad_pages_name_the_option_and_the_page_count(self) -> None:
        tail = "use page numbers and ranges such as 1-3,5 (pages 1 to 6)"
        self.assertEqual(self.rejected(order="7"), f"--order: {tail}")
        self.assertEqual(self.rejected(delete="0"), f"--delete: {tail}")
        self.assertEqual(self.rejected(rotate=["7:90"]), f"--rotate: {tail}")


def pdfdoc_plan(count, *, order, delete, rotate):  # noqa: ANN001, ANN201
    return pdf_pages.plan_order(count, order=order, delete=delete, rotate=rotate)


class MergeTests(TempFolderCase):
    def test_pages_follow_the_sources_in_the_order_given(self) -> None:
        first = self.check("a.pdf", fx.marked(2))
        second = self.check("b.pdf", fx.marked(3, first=11))
        planned = pdf_pages.merge([second, first])
        self.assertEqual(fx.markers(planned.data), [11, 12, 13, 1, 2])
        self.assertEqual(len(planned.pages), 5)

    def test_the_expected_boxes_describe_the_data(self) -> None:
        planned = pdf_pages.merge(
            [self.check("a.pdf", fx.marked(2, rotate={2: 270})), self.check("b.pdf", fx.crop_box(10, 20, 150, 250))]
        )
        self.assertIsNone(pdfdoc.verify(planned.data, planned.pages))
        self.assertEqual([box.rotation for box in planned.pages], [0, 270, 0])
        self.assertEqual(fx.rotations(planned.data), [0, 270, 0])
        self.assertEqual(planned.pages[2].crop, (10.0, 20.0, 150.0, 250.0))

    def test_a_source_can_be_merged_with_itself(self) -> None:
        source = self.check("a.pdf", fx.marked(2))
        planned = pdf_pages.merge([source, source])
        self.assertEqual(fx.markers(planned.data), [1, 2, 1, 2])

    def test_a_font_shared_inside_a_source_stays_shared(self) -> None:
        first = self.check("a.pdf", fx.text_pdf(["a1", "a2", "a3"]))
        second = self.check("b.pdf", fx.text_pdf(["b1", "b2", "b3"], first=11))
        planned = pdf_pages.merge([first, second])
        ids = fx.font_ids(planned.data)
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids[:3])), 1)
        self.assertEqual(len(set(ids[3:])), 1)
        self.assertEqual(len(set(ids)), 2)

    def test_text_survives(self) -> None:
        planned = pdf_pages.merge(
            [self.check("a.pdf", fx.text_pdf(["alpha"])), self.check("b.pdf", fx.text_pdf(["bravo"]))]
        )
        texts = [page.extract_text().strip() for page in read(planned.data).pages]
        self.assertEqual(texts, ["alpha", "bravo"])

    def test_bookmarks_are_carried(self) -> None:
        planned = pdf_pages.merge([self.check("a.pdf", fx.with_outline()), self.check("b.pdf", fx.marked(1))])
        self.assertEqual(fx.outline_titles(planned.data), ["Chapter one", "Chapter two"])

    def test_a_malformed_link_is_disabled_and_a_good_one_is_kept(self) -> None:
        planned = pdf_pages.merge(
            [self.check("a.pdf", fx.with_bad_link()), self.check("b.pdf", fx.with_link("https://example.com/ok"))]
        )
        self.assertEqual(fx.link_uris(planned.data), [None, "https://example.com/ok"])

    def test_the_sources_are_not_changed(self) -> None:
        data = fx.with_outline()
        source = self.check("a.pdf", data)
        pdf_pages.merge([source, source])
        self.assertEqual(source.path.read_bytes(), data)


class SplitTests(TempFolderCase):
    def test_each_group_becomes_a_file_with_its_pages(self) -> None:
        source = self.check("a.pdf", fx.marked(6))
        planned = pdf_pages.split(source, [[1, 2], [4], [6, 5]])
        self.assertEqual(len(planned), 3)
        self.assertEqual(fx.markers(planned[0].data), [1, 2])
        self.assertEqual(fx.markers(planned[1].data), [4])
        self.assertEqual(sorted(fx.markers(planned[2].data)), [5, 6])

    def test_the_expected_boxes_describe_the_data(self) -> None:
        source = self.check("a.pdf", fx.marked(4, rotate={3: 90}))
        for piece in pdf_pages.split(source, [[3, 4], [1]]):
            self.assertIsNone(pdfdoc.verify(piece.data, piece.pages))
        first = pdf_pages.split(source, [[3, 4]])[0]
        self.assertEqual([box.rotation for box in first.pages], [90, 0])

    def test_a_malformed_link_is_disabled_in_every_piece(self) -> None:
        source = self.check("a.pdf", fx.with_bad_link())
        self.assertEqual(fx.link_uris(pdf_pages.split(source, [[1]])[0].data), [None])


class OrganizeTests(TempFolderCase):
    def test_pages_come_out_in_the_order_given(self) -> None:
        source = self.check("a.pdf", fx.marked(4))
        planned = pdf_pages.organize(source, [4, 2, 3], {})
        self.assertEqual(fx.markers(planned.data), [4, 2, 3])
        self.assertIsNone(pdfdoc.verify(planned.data, planned.pages))

    def test_a_rotation_is_added_to_the_page_own_rotation(self) -> None:
        source = self.check("a.pdf", fx.marked(3, rotate={2: 90, 3: 270}))
        planned = pdf_pages.organize(source, [1, 2, 3], {1: 90, 2: 180, 3: 90})
        self.assertEqual(fx.markers(planned.data), [1, 2, 3])
        self.assertEqual(fx.rotations(planned.data), [90, 270, 0])
        self.assertEqual([box.rotation % 360 for box in planned.pages], [90, 270, 0])

    def test_rotation_follows_the_source_page_when_pages_move(self) -> None:
        source = self.check("a.pdf", fx.marked(3))
        planned = pdf_pages.organize(source, [3, 1], {3: 90})
        self.assertEqual(fx.markers(planned.data), [3, 1])
        self.assertEqual(fx.rotations(planned.data), [90, 0])

    def test_the_same_page_can_be_listed_twice(self) -> None:
        source = self.check("a.pdf", fx.marked(2))
        planned = pdf_pages.organize(source, [1, 1, 2], {})
        self.assertEqual(fx.markers(planned.data), [1, 1, 2])

    def test_the_source_is_unchanged_and_can_be_used_again(self) -> None:
        data = fx.marked(3)
        source = self.check("a.pdf", data)
        pdf_pages.organize(source, [3, 2, 1], {1: 90})
        again = pdf_pages.organize(source, [1], {})
        self.assertEqual(fx.markers(again.data), [1])
        self.assertEqual(fx.rotations(again.data), [0])
        self.assertEqual(source.path.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()

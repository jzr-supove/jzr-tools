"""Tests of ``drawn_sizes``: the size, in points, at which each image of a PDF is drawn.

Each test builds a small document in memory with a known content stream and
reads the sizes back by the object number of the image.
"""

from __future__ import annotations

import threading
import unittest

from pypdf import PdfWriter

from jzr_tools.core.pdf_content import drawn_sizes
from tests import compress_fixtures as cf


def run_with_limit(function, seconds: float = 60):  # noqa: ANN001, ANN201
    """Call ``function`` on a thread and fail the test when it does not return in time."""
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["value"] = function()
        except BaseException as error:  # noqa: BLE001
            outcome["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(f"did not return within {seconds} seconds")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]


class DrawnSizeCase(unittest.TestCase):
    def assertSize(self, sizes: dict, reference, expected: tuple[float, float]) -> None:  # noqa: ANN001
        self.assertIn(reference.idnum, sizes, f"no size found; got {sizes}")
        width, height = sizes[reference.idnum]
        self.assertAlmostEqual(width, expected[0], places=3)
        self.assertAlmostEqual(height, expected[1], places=3)

    def draw(self, content, *, writer: PdfWriter | None = None, **page: object):  # noqa: ANN001, ANN201
        """One page with ``content`` and an image called /Im1; returns (writer, image, sizes)."""
        writer = writer or PdfWriter()
        image = cf.tiny_image(writer)
        cf.add_page(writer, content, {"/Im1": image}, **page)  # type: ignore[arg-type]
        return writer, image, drawn_sizes(writer)


class MatrixTests(DrawnSizeCase):
    def test_a_scaling_cm_gives_the_size_it_draws(self) -> None:
        _, image, sizes = self.draw(b"q 200 0 0 100 10 10 cm /Im1 Do Q")
        self.assertSize(sizes, image, (200, 100))

    def test_a_rotated_image_is_measured_along_its_own_sides(self) -> None:
        _, image, sizes = self.draw(b"q 0 100 -50 0 0 0 cm /Im1 Do Q")
        self.assertSize(sizes, image, (100, 50))

    def test_a_translation_does_not_change_the_size(self) -> None:
        _, image, sizes = self.draw(b"1 0 0 1 300 400 cm 60 0 0 40 0 0 cm /Im1 Do")
        self.assertSize(sizes, image, (60, 40))

    def test_successive_cm_operators_multiply(self) -> None:
        _, image, sizes = self.draw(b"2 0 0 2 0 0 cm 50 0 0 50 0 0 cm /Im1 Do")
        self.assertSize(sizes, image, (100, 100))

    def test_a_later_cm_is_applied_before_the_earlier_ones(self) -> None:
        # The stretch comes first and the quarter turn second, so the unit
        # width ends up along the stretched axis' neighbour: 1 wide, 2 high.
        _, image, sizes = self.draw(b"2 0 0 1 0 0 cm 0 1 -1 0 0 0 cm /Im1 Do")
        self.assertSize(sizes, image, (1, 2))

    def test_q_and_capital_q_save_and_restore_the_matrix(self) -> None:
        _, image, sizes = self.draw(b"q 300 0 0 300 0 0 cm Q 100 0 0 50 0 0 cm /Im1 Do")
        self.assertSize(sizes, image, (100, 50))

    def test_nested_q_levels_restore_one_at_a_time(self) -> None:
        _, image, sizes = self.draw(b"q 2 0 0 2 0 0 cm q 5 0 0 5 0 0 cm Q 10 0 0 10 0 0 cm /Im1 Do Q")
        self.assertSize(sizes, image, (20, 20))

    def test_only_the_last_six_numbers_before_cm_count(self) -> None:
        _, image, sizes = self.draw(b"99 50 0 0 50 0 0 cm /Im1 Do")
        self.assertSize(sizes, image, (50, 50))

    def test_a_name_does_not_survive_the_operator_after_it(self) -> None:
        _, image, sizes = self.draw(b"/Im1 gs 10 0 0 10 0 0 cm Do")
        self.assertNotIn(image.idnum, sizes)


class UseTests(DrawnSizeCase):
    def test_the_largest_width_and_the_largest_height_of_all_uses_are_kept(self) -> None:
        content = b"q 200 0 0 20 0 0 cm /Im1 Do Q q 50 0 0 100 0 0 cm /Im1 Do Q"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (200, 100))

    def test_uses_on_different_pages_are_combined(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        cf.add_page(writer, b"q 50 0 0 50 0 0 cm /Im1 Do Q", {"/Im1": image})
        cf.add_page(writer, b"q 200 0 0 100 0 0 cm /Im1 Do Q", {"/Im1": image})
        self.assertSize(drawn_sizes(writer), image, (200, 100))

    def test_an_image_that_is_never_drawn_has_no_entry(self) -> None:
        writer = PdfWriter()
        used, unused = cf.tiny_image(writer), cf.tiny_image(writer)
        cf.add_page(writer, b"q 10 0 0 10 0 0 cm /Im1 Do Q", {"/Im1": used, "/Im2": unused})
        sizes = drawn_sizes(writer)
        self.assertIn(used.idnum, sizes)
        self.assertNotIn(unused.idnum, sizes)

    def test_an_image_name_missing_from_the_resources_is_ignored(self) -> None:
        _, image, sizes = self.draw(b"q 10 0 0 10 0 0 cm /Nothing Do /Im1 Do Q")
        self.assertSize(sizes, image, (10, 10))

    def test_content_streams_in_an_array_are_read_as_one(self) -> None:
        _, image, sizes = self.draw([b"q 60 0 0", b"60 0 0 cm /Im1 Do Q"])
        self.assertSize(sizes, image, (60, 60))

    def test_a_flate_coded_content_stream_is_decoded(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        page = cf.add_page(writer, b"q 80 0 0 40 0 0 cm /Im1 Do Q " + cf.PADDING, {"/Im1": image})
        page.compress_content_streams()
        self.assertSize(drawn_sizes(writer), image, (80, 40))

    def test_resources_are_inherited_from_the_page_tree(self) -> None:
        _, image, sizes = self.draw(b"q 30 0 0 30 0 0 cm /Im1 Do Q", resources_on_page_tree=True)
        self.assertSize(sizes, image, (30, 30))

    def test_a_document_without_images_gives_no_sizes(self) -> None:
        writer = PdfWriter()
        cf.add_page(writer, b"q 10 0 0 10 0 0 cm Q 0 0 m 5 5 l S")
        self.assertEqual(drawn_sizes(writer), {})


class TextAndCommentTests(DrawnSizeCase):
    def test_operators_inside_a_string_are_not_run(self) -> None:
        content = b"BT (q 999 0 0 999 0 0 cm /Im1 Do Q) Tj ET q 40 0 0 20 0 0 cm /Im1 Do Q"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (40, 20))

    def test_an_image_named_only_inside_a_string_is_not_drawn(self) -> None:
        _, image, sizes = self.draw(b"BT (/Im1 Do) Tj ET")
        self.assertNotIn(image.idnum, sizes)

    def test_escaped_and_nested_parentheses_do_not_end_a_string_early(self) -> None:
        content = (
            b"BT (a \\) 999 0 0 999 0 0 cm /Im1 Do b) Tj (x (y 998 0 0 998 0 0 cm) z) Tj ET "
            b"q 25 0 0 25 0 0 cm /Im1 Do Q"
        )
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (25, 25))

    def test_hex_strings_are_skipped(self) -> None:
        # <...> spells "999 0 0 999 0 0 cm /Im1 Do" in hex.
        text = b"999 0 0 999 0 0 cm /Im1 Do"
        content = b"BT <" + text.hex().encode() + b"> Tj ET q 15 0 0 15 0 0 cm /Im1 Do Q"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (15, 15))

    def test_a_string_clears_the_numbers_before_it(self) -> None:
        # The numbers before the string are dropped, so the one number after
        # it is not a matrix and the image is drawn at its unit size.
        _, image, sizes = self.draw(b"5 0 0 5 0 (x) 0 cm /Im1 Do")
        self.assertSize(sizes, image, (1, 1))

    def test_comments_are_skipped(self) -> None:
        content = b"% 500 0 0 500 0 0 cm /Im1 Do\nq 35 0 0 35 0 0 cm /Im1 Do Q"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (35, 35))

    def test_dictionary_and_array_delimiters_are_skipped(self) -> None:
        content = b"/P << /MCID 0 >> BDC [ 1 2 ] 0 d q 45 0 0 45 0 0 cm /Im1 Do Q EMC"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (45, 45))


class InlineImageTests(DrawnSizeCase):
    def test_inline_image_data_is_not_read_as_operators(self) -> None:
        inline = (
            b"q 10 0 0 10 0 0 cm BI /W 4 /H 4 /BPC 8 /CS /G ID "
            b"999 0 0 999 0 0 cm /Im1 Do Q q 998 0 0 998 0 0 cm\n EI Q "
        )
        _, image, sizes = self.draw(inline + b"q 70 0 0 70 0 0 cm /Im1 Do Q")
        self.assertSize(sizes, image, (70, 70))

    def test_the_matrix_after_an_inline_image_is_restored(self) -> None:
        content = b"q 500 0 0 500 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x00\nEI Q 12 0 0 12 0 0 cm /Im1 Do"
        _, image, sizes = self.draw(content)
        self.assertSize(sizes, image, (12, 12))


class FormTests(DrawnSizeCase):
    def test_an_image_in_a_form_is_scaled_by_the_matrix_of_the_page(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(writer, b"q 50 0 0 50 0 0 cm /Im1 Do Q", xobjects={"/Im1": image})
        cf.add_page(writer, b"3 0 0 3 0 0 cm /Fm1 Do", {"/Fm1": form})
        self.assertSize(drawn_sizes(writer), image, (150, 150))

    def test_the_matrix_of_a_form_is_applied(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(
            writer,
            b"q 50 0 0 50 0 0 cm /Im1 Do Q",
            xobjects={"/Im1": image},
            matrix=(2, 0, 0, 2, 0, 0),
        )
        cf.add_page(writer, b"q 1 0 0 1 10 10 cm /Fm1 Do Q", {"/Fm1": form})
        self.assertSize(drawn_sizes(writer), image, (100, 100))

    def test_the_form_matrix_comes_before_the_matrix_of_the_page(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(
            writer, b"/Im1 Do", xobjects={"/Im1": image}, matrix=(0, 1, -1, 0, 0, 0)
        )
        cf.add_page(writer, b"2 0 0 1 0 0 cm /Fm1 Do", {"/Fm1": form})
        self.assertSize(drawn_sizes(writer), image, (1, 2))

    def test_a_form_without_resources_uses_those_of_its_caller(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(writer, b"q 20 0 0 10 0 0 cm /Im1 Do Q")
        cf.add_page(writer, b"/Fm1 Do", {"/Fm1": form, "/Im1": image})
        self.assertSize(drawn_sizes(writer), image, (20, 10))

    def test_forms_nest(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        inner = cf.add_form(writer, b"q 10 0 0 10 0 0 cm /Im1 Do Q", xobjects={"/Im1": image})
        outer = cf.add_form(
            writer, b"2 0 0 2 0 0 cm /Fm2 Do", xobjects={"/Fm2": inner}, matrix=(3, 0, 0, 3, 0, 0)
        )
        cf.add_page(writer, b"q 1 0 0 1 5 5 cm /Fm1 Do Q", {"/Fm1": outer})
        self.assertSize(drawn_sizes(writer), image, (60, 60))

    def test_a_form_drawn_twice_counts_both_uses(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(writer, b"/Im1 Do", xobjects={"/Im1": image})
        content = b"q 10 0 0 10 0 0 cm /Fm1 Do Q q 30 0 0 30 0 0 cm /Fm1 Do Q"
        cf.add_page(writer, content, {"/Fm1": form})
        self.assertSize(drawn_sizes(writer), image, (30, 30))

    def test_a_form_that_draws_itself_ends(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        form = cf.add_form(writer, b"q 2 0 0 2 0 0 cm /Fm1 Do /Im1 Do Q", xobjects={"/Im1": image})
        cf.set_form_xobjects(form, {"/Fm1": form, "/Im1": image})
        cf.add_page(writer, b"q 10 0 0 10 0 0 cm /Fm1 Do Q", {"/Fm1": form})
        sizes = run_with_limit(lambda: drawn_sizes(writer))
        self.assertSize(sizes, image, (20, 20))

    def test_two_forms_that_draw_each_other_end(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        first = cf.add_form(writer, b"/Fm2 Do", xobjects={})
        second = cf.add_form(writer, b"q 7 0 0 7 0 0 cm /Im1 Do Q /Fm1 Do", xobjects={})
        cf.set_form_xobjects(first, {"/Fm2": second})
        cf.set_form_xobjects(second, {"/Fm1": first, "/Im1": image})
        cf.add_page(writer, b"/Fm1 Do", {"/Fm1": first})
        sizes = run_with_limit(lambda: drawn_sizes(writer))
        self.assertSize(sizes, image, (7, 7))

    def test_a_chain_of_a_few_forms_is_followed(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        bottom = cf.add_form(writer, b"q 9 0 0 9 0 0 cm /Im1 Do Q", xobjects={"/Im1": image})
        for _ in range(5):
            bottom = cf.add_form(writer, b"/Fm Do", xobjects={"/Fm": bottom})
        cf.add_page(writer, b"/Fm Do", {"/Fm": bottom})
        self.assertSize(drawn_sizes(writer), image, (9, 9))

    def test_a_chain_of_forms_deeper_than_the_limit_is_cut_off_without_an_error(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        bottom = cf.add_form(writer, b"q 9 0 0 9 0 0 cm /Im1 Do Q", xobjects={"/Im1": image})
        for _ in range(40):
            bottom = cf.add_form(writer, b"/Fm Do", xobjects={"/Fm": bottom})
        cf.add_page(writer, b"/Fm Do", {"/Fm": bottom})
        sizes = run_with_limit(lambda: drawn_sizes(writer))
        self.assertNotIn(image.idnum, sizes)


class BrokenContentTests(DrawnSizeCase):
    def test_garbage_content_gives_a_result_and_no_error(self) -> None:
        garbage = b"\x00\xff q q ( [ << /Im1 Do ) ( >> ] cm 1 2 3 Do EI ID BI \x80\x81"
        _, _, sizes = self.draw(garbage)
        self.assertIsInstance(sizes, dict)

    def test_an_unclosed_string_ends_the_scan_without_an_error(self) -> None:
        _, image, sizes = self.draw(b"q 10 0 0 10 0 0 cm /Im1 Do Q BT (never closed /Im1 Do")
        self.assertSize(sizes, image, (10, 10))

    def test_a_stream_that_cannot_be_decoded_is_skipped_and_the_others_are_read(self) -> None:
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        page = cf.add_page(writer, [b"", b"q 50 0 0 50 0 0 cm /Im1 Do Q"], {"/Im1": image})
        broken = cf.stream_object(cf.noise(40, 1, 1, 1), {"/Filter": "/FlateDecode"})
        references = page["/Contents"]
        references.insert(0, writer._add_object(broken))
        self.assertSize(drawn_sizes(writer), image, (50, 50))

    def test_a_page_without_content_is_fine(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        self.assertEqual(drawn_sizes(writer), {})

    def test_a_scan_that_runs_out_of_budget_stops_in_reasonable_time(self) -> None:
        # Millions of operators before the drawing: the image has no size
        # and the call still returns.
        filler = b"q Q " * 4_000_000
        writer = PdfWriter()
        image = cf.tiny_image(writer)
        early = cf.tiny_image(writer)
        content = b"q 10 0 0 10 0 0 cm /Im2 Do Q " + filler + b" q 50 0 0 50 0 0 cm /Im1 Do Q"
        cf.add_page(writer, content, {"/Im1": image, "/Im2": early})
        sizes = run_with_limit(lambda: drawn_sizes(writer), seconds=180)
        self.assertSize(sizes, early, (10, 10))
        self.assertNotIn(image.idnum, sizes)


if __name__ == "__main__":
    unittest.main()

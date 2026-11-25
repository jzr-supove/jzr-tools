"""Tests of the page layout used by ``jzr pdf from-images``.

``layout`` takes the stored pixel size of a picture, its EXIF orientation
(1-8), the paper, the page orientation and the margin in millimetres. It
returns the page size, the size and position of the drawn picture and the
``cm`` matrix that places the stored pixels. All lengths are in points and are
compared to within 0.01.
"""

from __future__ import annotations

import unittest

from jzr_tools.core import pdf_build

# For each EXIF orientation 1-8: how the unit square of the stored pixels maps
# onto the upright picture, as (a, b, c, d, e, f) before scaling.
EXIF_MATRICES = [
    (1, 0, 0, 1, 0, 0),
    (-1, 0, 0, 1, 1, 0),
    (-1, 0, 0, -1, 1, 1),
    (1, 0, 0, -1, 0, 1),
    (0, -1, -1, 0, 1, 1),
    (0, -1, 1, 0, 0, 1),
    (0, 1, 1, 0, 0, 0),
    (0, 1, -1, 0, 1, 0),
]


def lay(width, height, exif=1, paper="a4", page_orientation="auto", margin=10.0):  # noqa: ANN001, ANN201
    return pdf_build.layout(width, height, exif, paper, page_orientation, margin)


def box_of(matrix) -> tuple[float, float, float, float]:  # noqa: ANN001
    """The rectangle the unit square covers under ``a b c d e f``."""
    a, b, c, d, e, f = matrix
    xs = [e, e + a, e + c, e + a + c]
    ys = [f, f + b, f + d, f + b + d]
    return min(xs), min(ys), max(xs), max(ys)


class LayoutTests(unittest.TestCase):
    def assertClose(self, got, want, places_text: str = "") -> None:  # noqa: ANN001
        self.assertEqual(len(got), len(want), places_text)
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=0.01, msg=f"{places_text} {got} != {want}")

    def sizes(self, found) -> tuple[float, ...]:  # noqa: ANN001
        return (
            found.page_width,
            found.page_height,
            found.draw_width,
            found.draw_height,
            found.x,
            found.y,
        )

    def test_a_wide_picture_on_a4_turns_the_page_to_landscape(self) -> None:
        self.assertClose(self.sizes(lay(200, 100)), (841.89, 595.276, 785.197, 392.598, 28.346, 101.339))

    def test_a_tall_picture_on_a4_keeps_the_page_upright(self) -> None:
        self.assertClose(self.sizes(lay(100, 200)), (595.276, 841.89, 392.598, 785.197, 101.339, 28.346))

    def test_a_square_picture_stays_portrait_and_is_centred(self) -> None:
        self.assertClose(self.sizes(lay(100, 100)), (595.276, 841.89, 538.583, 538.583, 28.346, 151.654))

    def test_a_margin_of_zero_fills_the_width(self) -> None:
        self.assertClose(
            self.sizes(lay(100, 100, margin=0)), (595.276, 841.89, 595.276, 595.276, 0, 123.307)
        )

    def test_the_margin_is_in_millimetres(self) -> None:
        found = lay(200, 100, margin=30)
        self.assertClose(self.sizes(found), (841.89, 595.276, 671.811, 335.906, 85.039, 129.685))

    def test_letter_is_612_by_792(self) -> None:
        self.assertClose(
            self.sizes(lay(100, 100, paper="letter", margin=0)), (612, 792, 612, 612, 0, 90)
        )
        self.assertClose(
            self.sizes(lay(100, 200, paper="letter")), (612, 792, 367.654, 735.307, 122.173, 28.346)
        )

    def test_landscape_is_forced_whatever_the_picture(self) -> None:
        self.assertClose(
            self.sizes(lay(100, 200, paper="letter", page_orientation="landscape", margin=0)),
            (792, 612, 306, 612, 243, 0),
        )

    def test_portrait_is_forced_whatever_the_picture(self) -> None:
        self.assertClose(
            self.sizes(lay(200, 100, page_orientation="portrait")),
            (595.276, 841.89, 538.583, 269.291, 28.346, 286.299),
        )

    def test_original_makes_the_page_the_picture_size_at_96_dpi(self) -> None:
        found = lay(960, 480, paper="original")
        self.assertClose(self.sizes(found), (720, 360, 720, 360, 0, 0))
        self.assertClose(found.matrix, (720, 0, 0, 360, 0, 0))

    def test_original_ignores_the_margin_and_the_page_orientation(self) -> None:
        plain = self.sizes(lay(960, 480, paper="original"))
        other = self.sizes(lay(960, 480, paper="original", page_orientation="portrait", margin=25))
        self.assertClose(other, plain)

    def test_orientations_5_to_8_swap_the_sides(self) -> None:
        for exif in (5, 6, 7, 8):
            with self.subTest(exif=exif):
                found = lay(90, 40, exif)
                self.assertClose(
                    self.sizes(found), (595.276, 841.89, 348.976, 785.197, 123.15, 28.346), str(exif)
                )
        for exif in (1, 2, 3, 4):
            with self.subTest(exif=exif):
                self.assertClose(self.sizes(lay(90, 40, exif))[:2], (841.89, 595.276))

    def test_original_with_a_turned_picture_swaps_the_page_sides(self) -> None:
        found = lay(960, 480, 6, paper="original")
        self.assertClose(self.sizes(found), (360, 720, 360, 720, 0, 0))

    def test_the_matrix_of_each_orientation(self) -> None:
        for exif, (a, b, c, d, e, f) in enumerate(EXIF_MATRICES, start=1):
            with self.subTest(exif=exif):
                found = lay(90, 40, exif)
                dw, dh, x, y = found.draw_width, found.draw_height, found.x, found.y
                self.assertClose(found.matrix, (a * dw, b * dh, c * dw, d * dh, e * dw + x, f * dh + y))

    def test_the_matrix_always_covers_the_drawn_rectangle(self) -> None:
        for exif in range(1, 9):
            for paper in ("a4", "letter", "original"):
                with self.subTest(exif=exif, paper=paper):
                    found = lay(90, 40, exif, paper=paper)
                    self.assertClose(
                        box_of(found.matrix),
                        (found.x, found.y, found.x + found.draw_width, found.y + found.draw_height),
                    )
                    self.assertLessEqual(found.x + found.draw_width, found.page_width + 0.01)
                    self.assertLessEqual(found.y + found.draw_height, found.page_height + 0.01)


if __name__ == "__main__":
    unittest.main()

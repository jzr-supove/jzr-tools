"""Tests for image compression: names, size arithmetic, the size search, the
Pillow engine's prepare/encode/write_bytes and compress_file on real files.

The size search is tested against fake encoders (plain functions whose output
size is a made-up function of quality and pixel count), so those tests need no
Pillow. Fixtures for the engine and for compress_file are small images built
in temporary folders from seeded noise, because noise has stable, predictable
size behaviour. Animated fixtures use a different colour in every frame,
because Pillow merges identical consecutive frames.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageCms

from jzr_tools.core import image_compress as compress
from jzr_tools.core import pillow_engine
from jzr_tools.core.media import format_size

ANIMATED_MESSAGE = (
    "animated images are not compressed yet; jzr image convert keeps every frame"
)

MAKE = "AcmeCamCo"
XMP_MARK = b"XMPMARKER"
COMMENT_MARK = b"COMMENTMARKER"
XMP_PACKET = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><' + XMP_MARK + b"/></x:xmpmeta>"
    b'<?xpacket end="w"?>'
)


_SRGB: list[bytes] = []


def srgb_profile() -> bytes:
    """One sRGB profile for the whole module.

    A new profile carries the time it was made, so two made a second apart
    differ; every test compares against this one.
    """
    if not _SRGB:
        _SRGB.append(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    return _SRGB[0]


def noise(width: int, height: int, seed: int = 1) -> Image.Image:
    """An RGB image of seeded random pixels."""
    data = random.Random(seed).randbytes(width * height * 3)
    return Image.frombytes("RGB", (width, height), data)


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 24) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def percent(before: int, after: int) -> int:
    return max(1, round(abs(after - before) * 100 / before))


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name)

    def listing(self, folder: Path | None = None) -> list[str]:
        return sorted(path.name for path in (folder or self.dir).iterdir())

    def noise_file(self, name: str, size: tuple[int, int] = (400, 300), **save) -> Path:  # noqa: ANN003
        path = self.dir / name
        noise(*size).save(path, **save)
        return path

    def two_frame_gif(self, name: str = "anim.gif") -> Path:
        frames = [Image.new("RGB", (10, 10), colour) for colour in [(255, 0, 0), (0, 255, 0)]]
        path = self.dir / name
        frames[0].save(path, save_all=True, append_images=frames[1:])
        return path


# --- names -------------------------------------------------------------------


class SameFormatTests(unittest.TestCase):
    def test_equal_extensions_are_the_same_format(self) -> None:
        self.assertTrue(compress.same_format("png", "png"))
        self.assertTrue(compress.same_format("webp", "webp"))

    def test_jpg_and_jpeg_are_one_format(self) -> None:
        self.assertTrue(compress.same_format("jpg", "jpeg"))
        self.assertTrue(compress.same_format("jpeg", "jpg"))

    def test_tif_and_tiff_are_one_format(self) -> None:
        self.assertTrue(compress.same_format("tif", "tiff"))

    def test_heic_and_heif_are_one_format(self) -> None:
        self.assertTrue(compress.same_format("heic", "heif"))
        self.assertTrue(compress.same_format("heif", "heic"))

    def test_case_does_not_matter(self) -> None:
        self.assertTrue(compress.same_format("JPG", "jpeg"))
        self.assertTrue(compress.same_format("PNG", "png"))

    def test_different_formats_differ(self) -> None:
        self.assertFalse(compress.same_format("jpg", "png"))
        self.assertFalse(compress.same_format("png", "webp"))
        self.assertFalse(compress.same_format("heic", "avif"))
        self.assertFalse(compress.same_format("tif", "png"))


class NotCompressibleTests(unittest.TestCase):
    def test_formats_that_are_compressed_in_place_give_none(self) -> None:
        for extension in ("jpg", "jpeg", "png", "webp", "avif", "heic", "heif", "tif", "tiff"):
            with self.subTest(extension=extension):
                self.assertIsNone(compress.not_compressible(extension))

    def test_case_is_ignored_for_known_formats(self) -> None:
        self.assertIsNone(compress.not_compressible("JPG"))
        self.assertIsNone(compress.not_compressible("Png"))

    def test_other_formats_say_what_to_add(self) -> None:
        self.assertEqual(
            compress.not_compressible("bmp"),
            ".bmp is not compressed; add --to webp (or jpg, png)",
        )

    def test_the_extension_is_shown_lower_case(self) -> None:
        self.assertEqual(
            compress.not_compressible("BMP"),
            ".bmp is not compressed; add --to webp (or jpg, png)",
        )

    def test_gif_and_other_unknown_formats_are_not_compressible(self) -> None:
        for extension in ("gif", "ico", "tga", "txt"):
            with self.subTest(extension=extension):
                self.assertEqual(
                    compress.not_compressible(extension),
                    f".{extension} is not compressed; add --to webp (or jpg, png)",
                )


class ConstantsTests(unittest.TestCase):
    def test_default_qualities(self) -> None:
        self.assertEqual(compress.DEFAULT_QUALITY["jpg"], 82)
        self.assertEqual(compress.DEFAULT_QUALITY["jpeg"], 82)
        self.assertEqual(compress.DEFAULT_QUALITY["webp"], 80)
        self.assertEqual(compress.DEFAULT_QUALITY["avif"], 60)
        self.assertEqual(compress.DEFAULT_QUALITY["heic"], 50)
        self.assertEqual(compress.DEFAULT_QUALITY["heif"], 50)

    def test_lossless_formats_have_no_default_quality(self) -> None:
        for extension in ("png", "tif", "tiff"):
            with self.subTest(extension=extension):
                self.assertNotIn(extension, compress.DEFAULT_QUALITY)

    def test_targets_are_the_formats_to_can_name(self) -> None:
        self.assertEqual(tuple(compress.TARGETS), ("jpg", "png", "webp", "avif", "heic"))

    def test_compressed_stem(self) -> None:
        self.assertEqual(compress.COMPRESSED_STEM, "_compressed")


class CompressedNameTests(unittest.TestCase):
    def test_name_is_the_stem_then_compressed_then_the_extension(self) -> None:
        self.assertEqual(
            compress.compressed_name(Path("holiday/photo.jpg"), "webp"), "photo_compressed.webp"
        )

    def test_only_the_last_extension_is_replaced(self) -> None:
        self.assertEqual(
            compress.compressed_name(Path("a.tar.png"), "png"), "a.tar_compressed.png"
        )

    def test_the_extension_is_used_as_given(self) -> None:
        self.assertEqual(compress.compressed_name(Path("IMG.JPG"), "JPG"), "IMG_compressed.JPG")


class CompressTargetsTests(TempDirCase):
    def targets(self, sources: list[Path], **fields) -> list[Path]:  # noqa: ANN003
        fields.setdefault("extension", None)
        fields.setdefault("outdir", None)
        fields.setdefault("root", None)
        return compress.compress_targets(sources, **fields)

    def test_targets_sit_beside_their_sources(self) -> None:
        sub = self.dir / "sub"
        result = self.targets([self.dir / "a.jpg", sub / "b.png"])
        self.assertEqual(result, [self.dir / "a_compressed.jpg", sub / "b_compressed.png"])

    def test_the_extension_is_kept_as_written(self) -> None:
        result = self.targets([self.dir / "IMG.JPG", self.dir / "Shot.Jpeg"])
        self.assertEqual(
            result, [self.dir / "IMG_compressed.JPG", self.dir / "Shot_compressed.Jpeg"]
        )

    def test_an_extension_replaces_each_sources_own(self) -> None:
        result = self.targets([self.dir / "a.jpg", self.dir / "b.png"], extension="webp")
        self.assertEqual(result, [self.dir / "a_compressed.webp", self.dir / "b_compressed.webp"])

    def test_outdir_without_root_is_flat(self) -> None:
        out = self.dir / "out"
        result = self.targets(
            [self.dir / "x" / "a.jpg", self.dir / "y" / "b.jpg"], outdir=out
        )
        self.assertEqual(result, [out / "a_compressed.jpg", out / "b_compressed.jpg"])

    def test_outdir_with_root_mirrors_the_folders_under_the_root(self) -> None:
        out = self.dir / "out"
        result = self.targets(
            [self.dir / "a.jpg", self.dir / "sub" / "deep" / "b.png"],
            outdir=out,
            root=self.dir,
        )
        self.assertEqual(
            result, [out / "a_compressed.jpg", out / "sub" / "deep" / "b_compressed.png"]
        )

    def test_a_source_outside_the_root_goes_flat_into_outdir(self) -> None:
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        out = self.dir / "out"
        result = self.targets([Path(other.name) / "a.jpg"], outdir=out, root=self.dir / "root")
        self.assertEqual(result, [out / "a_compressed.jpg"])

    def test_sources_that_share_a_name_get_numbered(self) -> None:
        out = self.dir / "out"
        result = self.targets(
            [self.dir / "x" / "a.jpg", self.dir / "y" / "a.jpg", self.dir / "z" / "a.jpg"],
            outdir=out,
        )
        self.assertEqual(
            result,
            [
                out / "a_compressed.jpg",
                out / "a_compressed-2.jpg",
                out / "a_compressed-3.jpg",
            ],
        )

    def test_different_formats_with_one_stem_collide_under_an_extension(self) -> None:
        result = self.targets([self.dir / "a.jpg", self.dir / "a.png"], extension="webp")
        self.assertEqual(
            result, [self.dir / "a_compressed.webp", self.dir / "a_compressed-2.webp"]
        )

    def test_names_that_differ_only_by_case_collide(self) -> None:
        result = self.targets([self.dir / "a.jpg", self.dir / "A.jpg"], extension="webp")
        self.assertEqual(
            result, [self.dir / "a_compressed.webp", self.dir / "A_compressed-2.webp"]
        )

    def test_sources_with_different_names_are_not_numbered(self) -> None:
        result = self.targets([self.dir / "a.jpg", self.dir / "b.jpg"], extension="webp")
        self.assertEqual(result, [self.dir / "a_compressed.webp", self.dir / "b_compressed.webp"])


# --- size arithmetic ---------------------------------------------------------


class MaxSideSizeTests(unittest.TestCase):
    def test_a_landscape_image_is_limited_on_its_width(self) -> None:
        self.assertEqual(compress.max_side_size(4000, 3000, 1000), (1000, 750))

    def test_a_portrait_image_is_limited_on_its_height(self) -> None:
        self.assertEqual(compress.max_side_size(3000, 4000, 1000), (750, 1000))

    def test_a_square_image(self) -> None:
        self.assertEqual(compress.max_side_size(500, 500, 200), (200, 200))

    def test_an_image_that_already_fits_gives_none(self) -> None:
        self.assertIsNone(compress.max_side_size(800, 600, 1000))

    def test_an_image_whose_longer_side_equals_the_limit_gives_none(self) -> None:
        self.assertIsNone(compress.max_side_size(1000, 500, 1000))
        self.assertIsNone(compress.max_side_size(500, 1000, 1000))

    def test_the_shorter_side_is_rounded_to_the_nearest_pixel(self) -> None:
        self.assertEqual(compress.max_side_size(1000, 333, 100), (100, 33))  # 33.3
        self.assertEqual(compress.max_side_size(1000, 337, 100), (100, 34))  # 33.7
        self.assertEqual(compress.max_side_size(300, 200, 100), (100, 67))  # 66.67

    def test_a_side_that_rounds_to_zero_becomes_one_pixel(self) -> None:
        self.assertEqual(compress.max_side_size(1000, 1, 100), (100, 1))
        self.assertEqual(compress.max_side_size(1, 1000, 100), (1, 100))


class ShrunkSizeTests(unittest.TestCase):
    def test_each_side_is_scaled_by_the_root_of_the_ratio_times_the_margin(self) -> None:
        # sqrt(250000 / 1000000) * 0.85 = 0.425
        self.assertEqual(compress.shrunk_size(4000, 3000, 250_000, 1_000_000), (1700, 1275))

    def test_the_sides_are_rounded_to_the_nearest_pixel(self) -> None:
        # sqrt(0.05) * 0.85 = 0.19007
        self.assertEqual(compress.shrunk_size(1000, 1000, 2_000, 40_000), (190, 190))
        # sqrt(0.5) * 0.85 = 0.60104
        self.assertEqual(compress.shrunk_size(1001, 333, 500, 1_000), (602, 200))

    def test_the_margin_is_applied_even_when_the_miss_is_small(self) -> None:
        # 1.01 times too big would need 0.995; the margin aims lower, at 0.846.
        width, height = compress.shrunk_size(2000, 1000, 1_000_000, 1_010_000)
        self.assertEqual((width, height), (1692, 846))

    def test_a_shorter_side_under_the_floor_is_raised_to_32_pixels(self) -> None:
        # Plain scaling would give 340x8.5.
        self.assertEqual(compress.shrunk_size(4000, 100, 100, 10_000), (1280, 32))

    def test_the_floor_applies_to_the_width_of_a_portrait_image(self) -> None:
        self.assertEqual(compress.shrunk_size(100, 4000, 100, 10_000), (32, 1280))

    def test_the_floor_rounds_the_longer_side(self) -> None:
        # 1000 * 32 / 90 = 355.55...
        self.assertEqual(compress.shrunk_size(1000, 90, 1, 100_000), (356, 32))

    def test_a_square_image_can_shrink_to_the_floor(self) -> None:
        self.assertEqual(compress.shrunk_size(64, 64, 100, 10_000), (32, 32))

    def test_none_when_the_shorter_side_is_already_at_the_floor(self) -> None:
        self.assertIsNone(compress.shrunk_size(40, 32, 100, 10_000))
        self.assertIsNone(compress.shrunk_size(32, 32, 100, 10_000))

    def test_an_image_whose_shorter_side_is_under_the_floor_is_never_shrunk(self) -> None:
        self.assertIsNone(compress.shrunk_size(100, 20, 100, 10_000))
        self.assertIsNone(compress.shrunk_size(4000, 20, 100, 1_000_000))
        self.assertIsNone(compress.shrunk_size(20, 4000, 100, 1_000_000))
        self.assertIsNone(compress.shrunk_size(5, 5, 1, 1_000_000))

    def test_none_when_the_smallest_attempt_is_not_over_the_limit(self) -> None:
        # sqrt(2) * 0.85 is above 1: that would enlarge the image.
        self.assertIsNone(compress.shrunk_size(1000, 800, 200_000, 100_000))


class ChangeTextTests(unittest.TestCase):
    def test_a_smaller_file_shows_a_minus_percentage(self) -> None:
        self.assertEqual(compress.change_text(1000, 260), "-74%")

    def test_a_larger_file_shows_a_plus_percentage(self) -> None:
        self.assertEqual(compress.change_text(1000, 1120), "+12%")

    def test_equal_sizes_say_same_size(self) -> None:
        self.assertEqual(compress.change_text(5000, 5000), "same size")

    def test_the_percentage_is_rounded_to_the_nearest_whole_number(self) -> None:
        self.assertEqual(compress.change_text(1000, 603), "-40%")  # 39.7
        self.assertEqual(compress.change_text(1000, 1074), "+7%")  # 7.4
        self.assertEqual(compress.change_text(1000, 1076), "+8%")  # 7.6

    def test_a_tiny_saving_never_shows_zero_percent(self) -> None:
        self.assertEqual(compress.change_text(100_000, 99_999), "-1%")
        self.assertEqual(compress.change_text(100_000, 99_800), "-1%")

    def test_a_tiny_growth_never_shows_zero_percent(self) -> None:
        self.assertEqual(compress.change_text(100_000, 100_001), "+1%")

    def test_almost_all_gone(self) -> None:
        self.assertEqual(compress.change_text(100_000, 1), "-100%")

    def test_growth_beyond_double(self) -> None:
        self.assertEqual(compress.change_text(1000, 3500), "+250%")


# --- the size search, against fake encoders ----------------------------------


class FakeEncoder:
    """Records its calls; the output size is ``cost(quality, size)`` bytes."""

    def __init__(self, cost) -> None:  # noqa: ANN001
        self.cost = cost
        self.calls: list[tuple[int | None, tuple[int, int]]] = []

    def __call__(self, quality: int | None, size: tuple[int, int]) -> bytes:
        self.calls.append((quality, size))
        return b"x" * self.cost(quality, size)

    @property
    def sizes(self) -> list[tuple[int, int]]:
        seen: list[tuple[int, int]] = []
        for _, size in self.calls:
            if size not in seen:
                seen.append(size)
        return seen


def pixels_times_quality(quality: int | None, size: tuple[int, int]) -> int:
    """100 pixels' worth per quality point at 100x100; grows with both."""
    return (quality or 0) * size[0] * size[1] // 100


class SearchQualityTests(unittest.TestCase):
    SIZE = (100, 100)  # cost is then 100 bytes per quality point

    def test_a_fit_at_92_takes_one_encode(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.search_quality(encoder, self.SIZE, 10_000)
        self.assertTrue(fits)
        self.assertEqual(encoder.calls, [(92, self.SIZE)])
        self.assertEqual(attempt.quality, 92)

    def test_the_attempt_carries_the_size_and_the_bytes_that_were_encoded(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, _ = compress.search_quality(encoder, (100, 100), 10_000)
        self.assertEqual((attempt.width, attempt.height), (100, 100))
        self.assertEqual(attempt.data, b"x" * 9200)

    def test_exactly_the_limit_fits(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.search_quality(encoder, self.SIZE, 9_200)
        self.assertTrue(fits)
        self.assertEqual(attempt.quality, 92)

    def test_a_miss_at_40_returns_that_attempt_and_false(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.search_quality(encoder, self.SIZE, 3_999)
        self.assertFalse(fits)
        self.assertEqual(encoder.calls, [(92, self.SIZE), (40, self.SIZE)])
        self.assertEqual(attempt.quality, 40)
        self.assertEqual(len(attempt.data), 4_000)

    def test_a_fit_at_40_exactly_is_a_fit(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.search_quality(encoder, self.SIZE, 4_000)
        self.assertTrue(fits)
        self.assertEqual(attempt.quality, 40)

    def test_bisection_lands_on_the_highest_quality_that_fits(self) -> None:
        for limit, expected in [(6_000, 60), (6_099, 60), (9_199, 91), (4_100, 41), (7_345, 73)]:
            with self.subTest(limit=limit):
                encoder = FakeEncoder(pixels_times_quality)
                attempt, fits = compress.search_quality(encoder, self.SIZE, limit)
                self.assertTrue(fits)
                self.assertEqual(attempt.quality, expected)
                self.assertLessEqual(len(attempt.data), limit)

    def test_every_quality_from_41_to_91_can_be_found(self) -> None:
        for wanted in range(41, 92):
            with self.subTest(wanted=wanted):
                encoder = FakeEncoder(pixels_times_quality)
                attempt, fits = compress.search_quality(encoder, self.SIZE, wanted * 100 + 50)
                self.assertTrue(fits)
                self.assertEqual(attempt.quality, wanted)

    def test_at_most_eight_encodes_per_round(self) -> None:
        for limit in range(4_000, 9_200, 37):
            encoder = FakeEncoder(pixels_times_quality)
            compress.search_quality(encoder, self.SIZE, limit)
            self.assertLessEqual(len(encoder.calls), 8, limit)

    def test_every_encode_is_at_the_size_asked_for(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        compress.search_quality(encoder, self.SIZE, 6_000)
        self.assertEqual({size for _, size in encoder.calls}, {self.SIZE})

    def test_no_quality_is_tried_twice_or_outside_40_to_92(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        compress.search_quality(encoder, self.SIZE, 6_000)
        qualities = [quality for quality, _ in encoder.calls]
        self.assertEqual(len(qualities), len(set(qualities)))
        self.assertTrue(all(40 <= quality <= 92 for quality in qualities))

    def test_the_answer_is_the_highest_fit_not_a_neighbour_of_the_last_probe(self) -> None:
        # The size only drops below the limit at 73; a search that stopped at
        # the first fit it met would return something lower.
        def cliff(quality: int | None, size: tuple[int, int]) -> int:
            return 5_000 if (quality or 0) <= 73 else 9_000

        encoder = FakeEncoder(cliff)
        attempt, fits = compress.search_quality(encoder, self.SIZE, 6_000)
        self.assertTrue(fits)
        self.assertEqual(attempt.quality, 73)


class FitToSizeTests(unittest.TestCase):
    def test_a_lossy_fit_in_the_first_round_does_not_shrink(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.fit_to_size(encoder, (100, 100), 6_000, lossy=True)
        self.assertTrue(fits)
        self.assertEqual(attempt.quality, 60)
        self.assertEqual(encoder.sizes, [(100, 100)])

    def test_a_fit_at_92_takes_one_encode(self) -> None:
        encoder = FakeEncoder(pixels_times_quality)
        attempt, fits = compress.fit_to_size(encoder, (100, 100), 50_000, lossy=True)
        self.assertTrue(fits)
        self.assertEqual(len(encoder.calls), 1)
        self.assertEqual(attempt.quality, 92)

    def test_a_miss_at_quality_40_shrinks_to_a_size_from_the_smallest_attempt(self) -> None:
        # Cost 1000 bytes per quality point at 1000x1000; limit 2000 is out of
        # reach at any quality. shrunk_size(1000, 1000, 2000, 40000) is 190x190.
        encoder = FakeEncoder(lambda q, s: (q or 0) * s[0] * s[1] // 1000)
        attempt, fits = compress.fit_to_size(encoder, (1000, 1000), 2_000, lossy=True)
        self.assertTrue(fits)
        self.assertEqual(encoder.sizes, [(1000, 1000), (190, 190)])
        self.assertEqual((attempt.width, attempt.height), (190, 190))
        # 55 * 36100 // 1000 = 1985 fits; 56 gives 2021.
        self.assertEqual(attempt.quality, 55)
        self.assertLessEqual(len(attempt.data), 2_000)

    def test_the_first_round_misses_at_40_and_the_search_does_not_continue_there(self) -> None:
        encoder = FakeEncoder(lambda q, s: (q or 0) * s[0] * s[1] // 1000)
        compress.fit_to_size(encoder, (1000, 1000), 2_000, lossy=True)
        first_round = [call for call in encoder.calls if call[1] == (1000, 1000)]
        self.assertEqual([quality for quality, _ in first_round], [92, 40])

    def test_a_search_that_never_fits_stops_after_three_shrink_rounds(self) -> None:
        # A fixed overhead keeps the limit out of reach at every size.
        def cost(quality: int | None, size: tuple[int, int]) -> int:
            return 1_000 + (quality or 0) * size[0] * size[1] // 10_000_000

        encoder = FakeEncoder(cost)
        attempt, fits = compress.fit_to_size(encoder, (4000, 4000), 500, lossy=True)
        self.assertFalse(fits)
        self.assertEqual(len(encoder.sizes), 4)  # the base size and three shrinks
        self.assertEqual(len(encoder.calls), 8)  # 92 and 40 in each round

    def test_each_round_size_is_computed_from_the_previous_size_and_smallest_attempt(self) -> None:
        def cost(quality: int | None, size: tuple[int, int]) -> int:
            return 1_000 + (quality or 0) * size[0] * size[1] // 10_000_000

        limit = 500
        encoder = FakeEncoder(cost)
        compress.fit_to_size(encoder, (4000, 4000), limit, lossy=True)

        expected = [(4000, 4000)]
        for _ in range(3):
            width, height = expected[-1]
            smallest = cost(40, (width, height))  # the last round's q40 is the smallest
            expected.append(compress.shrunk_size(width, height, limit, smallest))
        self.assertEqual(encoder.sizes, expected)
        self.assertTrue(all(a[0] > b[0] for a, b in zip(expected, expected[1:])))

    def test_failure_returns_the_smallest_attempt_made(self) -> None:
        def cost(quality: int | None, size: tuple[int, int]) -> int:
            return 1_000 + (quality or 0) * size[0] * size[1] // 10_000_000

        encoder = FakeEncoder(cost)
        attempt, fits = compress.fit_to_size(encoder, (4000, 4000), 500, lossy=True)
        self.assertFalse(fits)
        smallest = min(cost(quality, size) for quality, size in encoder.calls)
        self.assertEqual(len(attempt.data), smallest)
        self.assertEqual((attempt.width, attempt.height), encoder.sizes[-1])
        self.assertEqual(attempt.quality, 40)

    def test_no_round_has_more_than_eight_encodes(self) -> None:
        encoder = FakeEncoder(lambda q, s: (q or 0) * s[0] * s[1] // 10_000)
        compress.fit_to_size(encoder, (1000, 1000), 3_000, lossy=True)
        for size in encoder.sizes:
            count = sum(1 for _, called in encoder.calls if called == size)
            self.assertLessEqual(count, 8, size)

    def test_shrinking_stops_early_when_no_smaller_size_exists(self) -> None:
        # The shorter side is already under 32, so shrunk_size gives None.
        encoder = FakeEncoder(lambda q, s: 10_000 + (q or 0))
        attempt, fits = compress.fit_to_size(encoder, (400, 20), 100, lossy=True)
        self.assertFalse(fits)
        self.assertEqual(encoder.sizes, [(400, 20)])
        self.assertEqual(len(encoder.calls), 2)
        self.assertEqual((attempt.width, attempt.height), (400, 20))

    def test_the_floor_ends_the_rounds(self) -> None:
        # Round 1 reaches 32 pixels on the shorter side; round 2 has nowhere to go.
        encoder = FakeEncoder(lambda q, s: 10_000 + (q or 0))
        _, fits = compress.fit_to_size(encoder, (1000, 1000), 1, lossy=True)
        self.assertFalse(fits)
        self.assertEqual(encoder.sizes, [(1000, 1000), (32, 32)])

    def test_lossless_does_one_encode_per_round_without_a_quality(self) -> None:
        # 100000 bytes at 1000x1000; limit 20000 needs 380x380 (14440 bytes).
        encoder = FakeEncoder(lambda q, s: s[0] * s[1] // 10)
        attempt, fits = compress.fit_to_size(encoder, (1000, 1000), 20_000, lossy=False)
        self.assertTrue(fits)
        self.assertEqual(encoder.calls, [(None, (1000, 1000)), (None, (380, 380))])
        self.assertIsNone(attempt.quality)
        self.assertEqual((attempt.width, attempt.height), (380, 380))

    def test_lossless_that_fits_at_once_takes_one_encode(self) -> None:
        encoder = FakeEncoder(lambda q, s: s[0] * s[1] // 10)
        attempt, fits = compress.fit_to_size(encoder, (1000, 1000), 100_000, lossy=False)
        self.assertTrue(fits)
        self.assertEqual(encoder.calls, [(None, (1000, 1000))])
        self.assertIsNone(attempt.quality)

    def test_lossless_that_never_fits_makes_four_encodes_and_returns_the_smallest(self) -> None:
        encoder = FakeEncoder(lambda q, s: 1_000 + s[0] * s[1] // 1_000_000)
        attempt, fits = compress.fit_to_size(encoder, (4000, 4000), 500, lossy=False)
        self.assertFalse(fits)
        self.assertEqual(len(encoder.calls), 4)
        self.assertTrue(all(quality is None for quality, _ in encoder.calls))
        self.assertEqual((attempt.width, attempt.height), encoder.sizes[-1])
        self.assertEqual(len(attempt.data), min(1_000 + w * h // 1_000_000 for _, (w, h) in encoder.calls))

    def test_an_attempt_is_immutable(self) -> None:
        attempt = compress.Attempt(quality=50, width=3, height=2, data=b"ab")
        with self.assertRaises(Exception):
            attempt.quality = 60  # type: ignore[misc]
        self.assertEqual((attempt.quality, attempt.width, attempt.height, attempt.data), (50, 3, 2, b"ab"))


# --- the Pillow engine --------------------------------------------------------


class PrepareTests(TempDirCase):
    def test_prepare_reports_the_upright_size(self) -> None:
        source = self.dir / "a.png"
        Image.new("RGB", (40, 20), (200, 30, 30)).save(source)
        prepared = pillow_engine.prepare(source, "png")
        self.assertEqual((prepared.width, prepared.height), (40, 20))

    def test_jpeg_with_optimized_tables_is_smaller_than_a_plain_save(self) -> None:
        source = self.dir / "noise.png"
        noise(200, 200).save(source)
        prepared = pillow_engine.prepare(source, "jpg")
        data = prepared.encode(82, (200, 200))

        plain = io.BytesIO()
        with Image.open(source) as image:
            image.convert("RGB").save(plain, "JPEG", quality=82)
        self.assertLess(len(data), len(plain.getvalue()))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.size, (200, 200))

    def test_a_lower_quality_gives_fewer_bytes(self) -> None:
        source = self.dir / "noise.png"
        noise(200, 200).save(source)
        prepared = pillow_engine.prepare(source, "jpg")
        sizes = [len(prepared.encode(quality, (200, 200))) for quality in (40, 70, 92)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(len(set(sizes)), 3)

    def test_one_prepared_image_encodes_many_times_with_the_same_result(self) -> None:
        source = self.dir / "noise.png"
        noise(64, 64).save(source)
        prepared = pillow_engine.prepare(source, "webp")
        self.assertEqual(prepared.encode(70, (64, 64)), prepared.encode(70, (64, 64)))

    def test_a_smaller_size_resamples_the_image(self) -> None:
        source = self.dir / "noise.png"
        noise(200, 100).save(source)
        prepared = pillow_engine.prepare(source, "jpg")
        full = prepared.encode(80, (200, 100))
        half = prepared.encode(80, (100, 50))
        with Image.open(io.BytesIO(half)) as result:
            self.assertEqual(result.size, (100, 50))
        self.assertLess(len(half), len(full))
        # The full size is still available afterwards: nothing was shrunk in place.
        with Image.open(io.BytesIO(prepared.encode(80, (200, 100)))) as again:
            self.assertEqual(again.size, (200, 100))

    def test_png_pixels_survive_unchanged(self) -> None:
        source = self.dir / "noise.png"
        original = noise(60, 40)
        original.save(source)
        prepared = pillow_engine.prepare(source, "png")
        data = prepared.encode(None, (60, 40))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.size, (60, 40))
            self.assertEqual(result.convert("RGB").tobytes(), original.tobytes())

    def test_png_keeps_transparency(self) -> None:
        source = self.dir / "alpha.png"
        image = Image.new("RGBA", (10, 10), (10, 20, 30, 0))
        image.putpixel((3, 3), (200, 100, 50, 255))
        image.save(source)
        data = pillow_engine.prepare(source, "png").encode(None, (10, 10))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.getpixel((3, 3)), (200, 100, 50, 255))
            self.assertEqual(result.getpixel((0, 0))[3], 0)

    def test_tiff_is_written_with_lzw(self) -> None:
        source = self.dir / "flat.png"
        original = Image.new("RGB", (80, 60), (10, 200, 30))
        original.save(source)
        data = pillow_engine.prepare(source, "tif").encode(None, (80, 60))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "TIFF")
            self.assertEqual(result.info.get("compression"), "tiff_lzw")
            self.assertEqual(result.convert("RGB").tobytes(), original.tobytes())

    def exif_with_date_taken(self) -> Image.Exif:
        exif = Image.Exif()
        exif[0x010F] = MAKE
        exif.get_ifd(0x8769)[0x9003] = "2020:01:02 03:04:05"
        return exif

    def test_tiff_with_an_exif_sub_block_is_uncompressed_and_keeps_the_date(self) -> None:
        source = self.dir / "dated.jpg"
        Image.new("RGB", (80, 60), (10, 200, 30)).save(source, exif=self.exif_with_date_taken())
        data = pillow_engine.prepare(source, "tiff").encode(None, (80, 60))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "TIFF")
            self.assertNotEqual(result.info.get("compression"), "tiff_lzw")
            exif = result.getexif()
            self.assertEqual(exif.get(0x010F), MAKE)
            self.assertEqual(exif.get_ifd(0x8769).get(0x9003), "2020:01:02 03:04:05")

    def test_tiff_without_a_sub_block_stays_lzw_when_the_exif_is_kept(self) -> None:
        source = self.dir / "make.jpg"
        exif = Image.Exif()
        exif[0x010F] = MAKE
        Image.new("RGB", (80, 60), (10, 200, 30)).save(source, exif=exif)
        data = pillow_engine.prepare(source, "tif").encode(None, (80, 60))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.info.get("compression"), "tiff_lzw")
            self.assertEqual(result.getexif().get(0x010F), MAKE)

    def sideways(self, name: str = "sideways.png") -> Path:
        """40x20 with a red left half and a blue right half, orientation 6."""
        image = Image.new("RGB", (40, 20), (255, 0, 0))
        image.paste((0, 0, 255), (20, 0, 40, 20))
        exif = Image.Exif()
        exif[0x0112] = 6
        path = self.dir / name
        image.save(path, exif=exif)
        return path

    def test_the_image_is_made_upright_first(self) -> None:
        source = self.sideways()
        prepared = pillow_engine.prepare(source, "png")
        self.assertEqual((prepared.width, prepared.height), (20, 40))
        data = prepared.encode(None, (20, 40))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.size, (20, 40))
            # Orientation 6 turns the picture a quarter clockwise: left is top.
            self.assertEqual(result.convert("RGB").getpixel((10, 5)), (255, 0, 0))
            self.assertEqual(result.convert("RGB").getpixel((10, 35)), (0, 0, 255))
            self.assertIsNone(result.getexif().get(0x0112))

    def test_an_upright_size_request_resizes_the_turned_image(self) -> None:
        prepared = pillow_engine.prepare(self.sideways(), "png")
        data = prepared.encode(None, (10, 20))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.size, (10, 20))
            self.assertIsNone(result.getexif().get(0x0112))

    def tagged_jpeg(self) -> Path:
        exif = Image.Exif()
        exif[0x010F] = MAKE
        path = self.dir / "tagged.jpg"
        Image.new("RGB", (40, 20), (200, 30, 30)).save(
            path,
            exif=exif,
            icc_profile=srgb_profile(),
            xmp=XMP_PACKET,
            comment=COMMENT_MARK,
        )
        return path

    def test_strip_drops_exif_xmp_and_comment_and_keeps_the_profile(self) -> None:
        data = pillow_engine.prepare(self.tagged_jpeg(), "jpg", strip=True).encode(82, (40, 20))
        self.assertNotIn(MAKE.encode(), data)
        self.assertNotIn(XMP_MARK, data)
        self.assertNotIn(COMMENT_MARK, data)
        with Image.open(io.BytesIO(data)) as result:
            self.assertNotIn(0x010F, result.getexif())
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_without_strip_the_exif_and_the_profile_are_kept(self) -> None:
        data = pillow_engine.prepare(self.tagged_jpeg(), "jpg").encode(82, (40, 20))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.getexif().get(0x010F), MAKE)
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_strip_applies_to_png_output_too(self) -> None:
        data = pillow_engine.prepare(self.tagged_jpeg(), "png", strip=True).encode(None, (40, 20))
        self.assertNotIn(MAKE.encode(), data)
        self.assertNotIn(XMP_MARK, data)
        self.assertNotIn(COMMENT_MARK, data)
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def transparent_png(self) -> Path:
        path = self.dir / "clear.png"
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save(path)
        return path

    def test_a_transparent_png_becomes_white_in_a_jpeg_by_default(self) -> None:
        data = pillow_engine.prepare(self.transparent_png(), "jpg").encode(90, (40, 40))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.mode, "RGB")
            self.assertTrue(near(result.getpixel((20, 20)), (255, 255, 255)))

    def test_the_background_colour_fills_transparency_in_a_jpeg(self) -> None:
        prepared = pillow_engine.prepare(
            self.transparent_png(), "jpg", background=(0, 0, 255)
        )
        data = prepared.encode(90, (40, 40))
        with Image.open(io.BytesIO(data)) as result:
            self.assertTrue(near(result.getpixel((20, 20)), (0, 0, 255)), result.getpixel((20, 20)))

    def test_an_animated_gif_is_refused(self) -> None:
        source = self.two_frame_gif()
        with self.assertRaises(pillow_engine.ConversionError) as caught:
            pillow_engine.prepare(source, "webp")
        self.assertEqual(str(caught.exception), ANIMATED_MESSAGE)

    def test_the_animated_message_is_a_public_constant(self) -> None:
        self.assertEqual(pillow_engine.NOT_COMPRESSED_ANIMATED, ANIMATED_MESSAGE)

    def test_bytes_that_are_not_an_image_are_refused(self) -> None:
        source = self.dir / "junk.jpg"
        source.write_bytes(b"this is not an image at all")
        with self.assertRaises(pillow_engine.ConversionError) as caught:
            pillow_engine.prepare(source, "jpg")
        self.assertEqual(str(caught.exception), "not a readable image")

    def test_the_source_file_is_not_changed(self) -> None:
        source = self.dir / "noise.png"
        noise(40, 40).save(source)
        before = source.read_bytes()
        pillow_engine.prepare(source, "jpg").encode(50, (20, 20))
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.listing(), ["noise.png"])


class WriteBytesTests(TempDirCase):
    def test_the_bytes_appear_at_the_target(self) -> None:
        target = self.dir / "out.bin"
        pillow_engine.write_bytes(target, b"hello")
        self.assertEqual(target.read_bytes(), b"hello")
        self.assertEqual(self.listing(), ["out.bin"])

    def test_an_existing_target_is_replaced(self) -> None:
        target = self.dir / "out.bin"
        target.write_bytes(b"old and longer")
        pillow_engine.write_bytes(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(self.listing(), ["out.bin"])

    def test_the_target_folder_is_created(self) -> None:
        target = self.dir / "a" / "b" / "out.bin"
        pillow_engine.write_bytes(target, b"x")
        self.assertEqual(target.read_bytes(), b"x")

    def test_a_failed_rename_leaves_no_temporary_file_and_the_old_target(self) -> None:
        target = self.dir / "out.bin"
        target.write_bytes(b"old")
        with mock.patch("os.replace", side_effect=OSError("disk says no")):
            with self.assertRaises(OSError):
                pillow_engine.write_bytes(target, b"new")
        self.assertEqual(self.listing(), ["out.bin"])
        self.assertEqual(target.read_bytes(), b"old")

    def test_a_failed_write_leaves_no_temporary_file(self) -> None:
        blocker = self.dir / "blocker"
        blocker.write_bytes(b"i am a file")
        with self.assertRaises(OSError):
            pillow_engine.write_bytes(blocker / "out.bin", b"x")
        self.assertEqual(self.listing(), ["blocker"])
        self.assertEqual(blocker.read_bytes(), b"i am a file")

    def test_the_bytes_are_staged_in_a_temporary_name_beside_the_target(self) -> None:
        target = self.dir / "out.bin"
        seen: list[str] = []
        real = os.replace

        def spy(source, destination):  # noqa: ANN001, ANN202
            seen.append(Path(source).name)
            self.assertEqual(Path(source).parent, target.parent)
            real(source, destination)

        with mock.patch("os.replace", side_effect=spy):
            pillow_engine.write_bytes(target, b"data")
        self.assertEqual(len(seen), 1)
        self.assertTrue(re.fullmatch(r"jzr-tmp-[0-9a-f]+\.bin", seen[0]), seen[0])


# --- compress_file -------------------------------------------------------------


class CompressFileTests(TempDirCase):
    def run_compress(self, source: Path, target: Path, **fields):  # noqa: ANN003, ANN201
        options = {
            "quality": None,
            "size_limit": None,
            "max_side": None,
            "background": None,
            "strip": False,
            "overwrite": False,
            "dry_run": False,
        }
        options.update(fields)
        return compress.compress_file(source, target, **options)

    def photo(self, name: str = "photo.jpg", quality: int = 95) -> Path:
        return self.noise_file(name, (400, 300), quality=quality)

    def jpeg_size(self, source: Path, quality: int) -> int:
        buffer = io.BytesIO()
        with Image.open(source) as image:
            image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
        return len(buffer.getvalue())

    # status and detail text

    def test_a_smaller_copy_is_compressed_with_sizes_and_percentage(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target)
        self.assertEqual(result.status, "compressed")
        self.assertTrue(result.ok)
        self.assertEqual(result.source, source)
        self.assertEqual(result.target, target)
        before, after = source.stat().st_size, target.stat().st_size
        self.assertLess(after, before)
        self.assertEqual(
            result.detail,
            f"{format_size(before)} -> {format_size(after)}, -{percent(before, after)}%, kept 400x300",
        )

    def test_the_written_file_is_a_readable_jpeg_of_the_same_size(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        self.run_compress(source, target)
        with Image.open(target) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.size, (400, 300))

    def test_the_source_is_left_alone(self) -> None:
        source = self.photo()
        before = source.read_bytes()
        self.run_compress(source, self.dir / "photo_compressed.jpg")
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.listing(), ["photo.jpg", "photo_compressed.jpg"])

    def test_the_default_jpeg_quality_is_82(self) -> None:
        source = self.photo(quality=98)
        target = self.dir / "photo_compressed.jpg"
        self.run_compress(source, target)
        size = target.stat().st_size
        self.assertGreater(size, self.jpeg_size(source, 70))
        self.assertLess(size, self.jpeg_size(source, 90))
        self.assertLess(abs(size - self.jpeg_size(source, 82)), self.jpeg_size(source, 82) * 0.05)

    def test_a_given_quality_is_used(self) -> None:
        source = self.photo(quality=98)
        low = self.dir / "low.jpg"
        high = self.dir / "high.jpg"
        self.assertEqual(self.run_compress(source, low, quality=30).status, "compressed")
        self.assertEqual(self.run_compress(source, high, quality=90).status, "compressed")
        self.assertLess(low.stat().st_size, high.stat().st_size)
        self.assertLess(abs(low.stat().st_size - self.jpeg_size(source, 30)), 0.05 * self.jpeg_size(source, 30))

    def test_no_quality_text_without_a_size_limit(self) -> None:
        result = self.run_compress(self.photo(), self.dir / "out.jpg")
        self.assertNotIn("quality", result.detail)

    # dry run and skips

    def test_a_dry_run_plans_and_writes_nothing(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertTrue(result.ok)
        self.assertEqual(result.target, target)
        self.assertEqual(self.listing(), ["photo.jpg"])

    def test_a_dry_run_reads_nothing_so_an_animated_file_is_planned(self) -> None:
        source = self.two_frame_gif()
        result = self.run_compress(source, self.dir / "anim_compressed.gif", dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(self.listing(), ["anim.gif"])

    def test_a_target_that_is_the_source_is_skipped(self) -> None:
        source = self.photo()
        before = source.read_bytes()
        result = self.run_compress(source, source, overwrite=True)
        self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))
        self.assertEqual(source.read_bytes(), before)

    def test_a_target_that_differs_only_in_case_is_the_source(self) -> None:
        source = self.photo("Photo.jpg")
        result = self.run_compress(source, self.dir / "PHOTO.JPG")
        self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))

    def test_an_existing_target_is_skipped_without_overwrite(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        target.write_bytes(b"precious")
        result = self.run_compress(source, target)
        self.assertEqual(
            (result.status, result.detail), ("skipped", "target exists (use --overwrite)")
        )
        self.assertEqual(target.read_bytes(), b"precious")

    def test_an_existing_target_is_replaced_with_overwrite(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        target.write_bytes(b"precious")
        result = self.run_compress(source, target, overwrite=True)
        self.assertEqual(result.status, "compressed")
        with Image.open(target) as image:
            self.assertEqual(image.format, "JPEG")

    def test_the_target_folder_is_created(self) -> None:
        source = self.photo()
        target = self.dir / "out" / "deeper" / "photo_compressed.jpg"
        result = self.run_compress(source, target)
        self.assertEqual(result.status, "compressed")
        self.assertTrue(target.exists())

    # never bigger

    def test_a_copy_that_is_not_smaller_is_skipped_and_not_written(self) -> None:
        # Re-encoding a quality-30 JPEG at 82 makes it larger.
        source = self.photo(quality=30)
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target)
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.ok)
        match = re.fullmatch(
            r"already small \(compressed copy was (.+), source is (.+)\)", result.detail
        )
        self.assertIsNotNone(match, result.detail)
        self.assertEqual(match.group(2), format_size(source.stat().st_size))
        self.assertGreaterEqual(
            self.jpeg_size(source, 82), source.stat().st_size, "fixture must come out larger"
        )
        self.assertFalse(target.exists())
        self.assertEqual(self.listing(), ["photo.jpg"])

    def test_the_copy_size_in_the_skip_text_is_the_size_that_would_have_been_written(self) -> None:
        source = self.photo(quality=30)
        result = self.run_compress(source, self.dir / "photo_compressed.jpg")
        match = re.fullmatch(r"already small \(compressed copy was (.+), source is .+\)", result.detail)
        self.assertIsNotNone(match, result.detail)
        expected = format_size(self.jpeg_size(source, 82))
        self.assertEqual(match.group(1), expected)

    def test_a_png_that_is_already_optimal_is_skipped(self) -> None:
        source = self.dir / "flat.png"
        noise(48, 48).save(source, optimize=True)
        result = self.run_compress(source, self.dir / "flat_compressed.png")
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.detail.startswith("already small (compressed copy was "))
        self.assertEqual(self.listing(), ["flat.png"])

    def test_jpg_to_jpeg_is_not_a_format_change_so_a_larger_copy_is_skipped(self) -> None:
        source = self.photo("shot.jpeg", quality=30)
        target = self.dir / "shot_compressed.jpg"
        result = self.run_compress(source, target)
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.detail.startswith("already small"), result.detail)
        self.assertFalse(target.exists())

    def test_a_format_change_is_kept_even_when_the_file_is_larger(self) -> None:
        source = self.photo(quality=30)
        target = self.dir / "photo_compressed.png"
        result = self.run_compress(source, target)
        self.assertEqual(result.status, "compressed")
        before, after = source.stat().st_size, target.stat().st_size
        self.assertGreater(after, before)
        self.assertEqual(
            result.detail,
            f"{format_size(before)} -> {format_size(after)}, +{percent(before, after)}%"
            ", larger than the source, kept 400x300",
        )
        with Image.open(target) as image:
            self.assertEqual(image.format, "PNG")

    def test_a_format_change_to_a_smaller_file_has_no_larger_note(self) -> None:
        photo = self.photo("noisy.png")
        target = self.dir / "noisy_compressed.webp"
        result = self.run_compress(photo, target)
        self.assertEqual(result.status, "compressed")
        self.assertLess(target.stat().st_size, photo.stat().st_size)
        self.assertNotIn("larger than the source", result.detail)

    # --size

    def test_a_source_already_under_the_size_limit_is_skipped(self) -> None:
        source = self.photo()
        limit = source.stat().st_size + 1_000
        result = self.run_compress(source, self.dir / "photo_compressed.jpg", size_limit=limit)
        self.assertEqual(
            (result.status, result.detail), ("skipped", f"already under {format_size(limit)}")
        )
        self.assertEqual(self.listing(), ["photo.jpg"])

    def test_a_source_exactly_at_the_size_limit_is_skipped(self) -> None:
        source = self.photo()
        limit = source.stat().st_size
        result = self.run_compress(source, self.dir / "photo_compressed.jpg", size_limit=limit)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.detail, f"already under {format_size(limit)}")

    def test_the_under_limit_skip_text_uses_decimal_units(self) -> None:
        source = self.photo()
        result = self.run_compress(source, self.dir / "out.jpg", size_limit=10_000_000)
        self.assertEqual(result.detail, "already under 10.0 MB")

    def test_a_format_change_is_not_skipped_for_being_under_the_limit(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.webp"
        result = self.run_compress(source, target, size_limit=source.stat().st_size * 2)
        self.assertEqual(result.status, "compressed")
        self.assertTrue(target.exists())

    def test_a_max_side_that_shrinks_the_image_is_not_skipped_for_being_under_the_limit(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(
            source, target, size_limit=source.stat().st_size * 2, max_side=200
        )
        self.assertEqual(result.status, "compressed")
        with Image.open(target) as image:
            self.assertEqual(image.size, (200, 150))

    def test_a_max_side_that_changes_nothing_still_allows_the_under_limit_skip(self) -> None:
        source = self.photo()
        result = self.run_compress(
            source,
            self.dir / "photo_compressed.jpg",
            size_limit=source.stat().st_size * 2,
            max_side=1000,
        )
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.detail.startswith("already under "))

    def test_a_size_limit_is_met_by_lowering_the_quality(self) -> None:
        source = self.photo()
        high, low = self.jpeg_size(source, 92), self.jpeg_size(source, 40)
        limit = (high + low) // 2
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, size_limit=limit)
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertLessEqual(target.stat().st_size, limit)
        match = re.search(r"quality (\d+)", result.detail)
        self.assertIsNotNone(match, result.detail)
        self.assertTrue(40 < int(match.group(1)) < 92, result.detail)
        self.assertNotIn("resized", result.detail)
        with Image.open(target) as image:
            self.assertEqual(image.size, (400, 300))

    def test_the_quality_found_is_the_highest_that_fits(self) -> None:
        source = self.photo()
        limit = (self.jpeg_size(source, 92) + self.jpeg_size(source, 40)) // 2
        result = self.run_compress(source, self.dir / "photo_compressed.jpg", size_limit=limit)
        found = int(re.search(r"quality (\d+)", result.detail).group(1))
        self.assertGreater(self.jpeg_size(source, found + 1), limit)

    def test_a_limit_that_the_top_quality_meets_is_reported_at_92(self) -> None:
        source = self.photo(quality=98)
        limit = self.jpeg_size(source, 92) + 100
        self.assertLess(limit, source.stat().st_size)
        result = self.run_compress(source, self.dir / "photo_compressed.jpg", size_limit=limit)
        self.assertEqual(result.status, "compressed")
        self.assertIn("quality 92", result.detail)

    def test_a_size_limit_that_quality_cannot_meet_shrinks_the_image(self) -> None:
        source = self.photo()
        limit = self.jpeg_size(source, 40) // 3
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, size_limit=limit)
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertLessEqual(target.stat().st_size, limit)
        match = re.search(r"resized to (\d+)x(\d+) to fit", result.detail)
        self.assertIsNotNone(match, result.detail)
        width, height = int(match.group(1)), int(match.group(2))
        self.assertLess(width, 400)
        self.assertLess(height, 300)
        with Image.open(target) as image:
            self.assertEqual(image.size, (width, height))
        self.assertIn("quality ", result.detail)

    def test_the_shrunk_image_keeps_its_proportions(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        self.run_compress(source, target, size_limit=self.jpeg_size(source, 40) // 3)
        with Image.open(target) as image:
            width, height = image.size
        self.assertAlmostEqual(width / height, 4 / 3, delta=0.03)

    def test_a_lossless_target_is_shrunk_to_fit_without_a_quality_text(self) -> None:
        source = self.dir / "noise.png"
        noise(300, 300).save(source)
        limit = source.stat().st_size // 3
        target = self.dir / "noise_compressed.png"
        result = self.run_compress(source, target, size_limit=limit)
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertLessEqual(target.stat().st_size, limit)
        self.assertNotIn("quality", result.detail)
        self.assertRegex(result.detail, r"resized to \d+x\d+ to fit")

    def test_an_impossible_limit_fails_and_writes_nothing(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, size_limit=200)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)
        match = re.fullmatch(
            r"could not get under 200 B; the smallest attempt was (.+) at (\d+)x(\d+)",
            result.detail,
        )
        self.assertIsNotNone(match, result.detail)
        self.assertGreater(int(match.group(2)), 0)
        self.assertLess(int(match.group(2)), 400)
        self.assertFalse(target.exists())
        self.assertEqual(self.listing(), ["photo.jpg"])

    def test_the_failure_text_names_the_limit_in_decimal_units(self) -> None:
        source = self.photo()
        result = self.run_compress(source, self.dir / "out.jpg", size_limit=300)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.detail.startswith("could not get under 300 B; "), result.detail)

    # --max-side

    def test_max_side_resizes_the_longer_side(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, max_side=200)
        self.assertEqual(result.status, "compressed")
        self.assertIn("resized to 200x150", result.detail)
        self.assertNotIn("to fit", result.detail)
        with Image.open(target) as image:
            self.assertEqual(image.size, (200, 150))

    def test_max_side_is_measured_on_the_upright_image(self) -> None:
        source = self.dir / "turned.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6
        noise(40, 20).save(source, exif=exif, quality=95)
        target = self.dir / "turned_compressed.jpg"
        result = self.run_compress(source, target, max_side=10, quality=60)
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertIn("resized to 5x10", result.detail)
        with Image.open(target) as image:
            self.assertEqual(image.size, (5, 10))

    def test_a_smaller_image_is_not_enlarged_by_max_side(self) -> None:
        source = self.photo()
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, max_side=1000)
        self.assertEqual(result.status, "compressed")
        self.assertNotIn("resized", result.detail)
        with Image.open(target) as image:
            self.assertEqual(image.size, (400, 300))

    def test_max_side_with_a_size_limit_shrinks_first_and_then_fits(self) -> None:
        source = self.photo()
        with Image.open(source) as image:
            scaled = image.resize((200, 150), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        scaled.save(buffer, "JPEG", quality=40, optimize=True)
        limit = len(buffer.getvalue()) // 2
        target = self.dir / "photo_compressed.jpg"
        result = self.run_compress(source, target, max_side=200, size_limit=limit)
        self.assertEqual(result.status, "compressed", result.detail)
        self.assertLessEqual(target.stat().st_size, limit)
        with Image.open(target) as image:
            self.assertLess(image.width, 200)

    # failures

    def test_an_animated_gif_fails(self) -> None:
        source = self.two_frame_gif()
        target = self.dir / "anim_compressed.gif"
        result = self.run_compress(source, target)
        self.assertEqual((result.status, result.detail), ("failed", ANIMATED_MESSAGE))
        self.assertFalse(result.ok)
        self.assertEqual(self.listing(), ["anim.gif"])

    def test_junk_fails_with_a_plain_message(self) -> None:
        source = self.dir / "junk.jpg"
        source.write_bytes(b"not an image, just text")
        target = self.dir / "junk_compressed.jpg"
        result = self.run_compress(source, target)
        self.assertEqual((result.status, result.detail), ("failed", "not a readable image"))
        self.assertEqual(self.listing(), ["junk.jpg"])

    def test_a_missing_source_fails_instead_of_raising(self) -> None:
        result = self.run_compress(self.dir / "gone.jpg", self.dir / "gone_compressed.jpg")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.detail)
        self.assertEqual(self.listing(), [])

    def test_a_target_folder_that_is_a_file_fails_with_its_text(self) -> None:
        source = self.photo()
        blocker = self.dir / "blocker"
        blocker.write_bytes(b"file")
        result = self.run_compress(source, blocker / "photo_compressed.jpg")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.detail)
        self.assertEqual(self.listing(), ["blocker", "photo.jpg"])

    def test_one_failure_does_not_stop_the_next_file(self) -> None:
        junk = self.dir / "junk.jpg"
        junk.write_bytes(b"nope")
        bad = self.run_compress(junk, self.dir / "junk_compressed.jpg")
        good = self.run_compress(self.photo(), self.dir / "photo_compressed.jpg")
        self.assertEqual((bad.status, good.status), ("failed", "compressed"))

    def test_no_temporary_file_is_left_after_a_run(self) -> None:
        source = self.photo()
        self.run_compress(source, self.dir / "photo_compressed.jpg", size_limit=self.jpeg_size(source, 40) // 3)
        self.assertEqual(self.listing(), ["photo.jpg", "photo_compressed.jpg"])

    # strip and background through the file API

    def test_strip_removes_exif_from_the_written_file(self) -> None:
        source = self.dir / "tagged.jpg"
        exif = Image.Exif()
        exif[0x010F] = MAKE
        noise(200, 200).save(source, exif=exif, quality=95, icc_profile=srgb_profile())
        kept = self.dir / "kept.jpg"
        stripped = self.dir / "stripped.jpg"
        self.run_compress(source, kept)
        self.run_compress(source, stripped, strip=True)
        with Image.open(kept) as image:
            self.assertEqual(image.getexif().get(0x010F), MAKE)
        with Image.open(stripped) as image:
            self.assertNotIn(0x010F, image.getexif())
            self.assertEqual(image.info.get("icc_profile"), srgb_profile())

    def test_background_fills_transparency_when_the_format_changes_to_jpeg(self) -> None:
        source = self.dir / "clear.png"
        Image.new("RGBA", (60, 60), (255, 0, 0, 0)).save(source)
        target = self.dir / "clear_compressed.jpg"
        result = self.run_compress(source, target, background=(0, 0, 255))
        self.assertEqual(result.status, "compressed")
        with Image.open(target) as image:
            self.assertTrue(near(image.convert("RGB").getpixel((30, 30)), (0, 0, 255)))


if __name__ == "__main__":
    unittest.main()

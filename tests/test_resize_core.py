"""Tests for the resize rules, output names and the Pillow engine's resizing.

Fixtures are built with Pillow in temporary folders and outputs are checked by
reopening them. JPEG colours are read from the middle of large blocks, because
JPEG blends neighbouring pixels. Animated fixtures use a different colour in
every frame, because Pillow merges identical consecutive frames.
"""

from __future__ import annotations

import io
from pathlib import Path
import random
import struct
import tempfile
import unittest
import warnings

from PIL import Image, ImageCms

from jzr_tools.core import pillow_engine, resize

THUMBNAIL_MARK = b"THUMBNAILMARK"
XMP_MARK = b"XMPMARKER"
COMMENT_MARK = b"COMMENTMARKER"
MAKE = "AcmeCamCo"

XMP_PACKET = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><' + XMP_MARK + b"/></x:xmpmeta>"
    b'<?xpacket end="w"?>'
)

ANIMATED_MESSAGE = (
    "animated images are not resized yet; jzr image convert keeps every frame"
)

LANCZOS = Image.Resampling.LANCZOS


def request(**fields: object) -> resize.SizeRequest:
    return resize.SizeRequest(**fields)  # type: ignore[arg-type]


def plan(width: int, height: int, enlarged: bool = False) -> resize.SizePlan:
    return resize.SizePlan(width=width, height=height, enlarged=enlarged)


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 16) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def thumbnail_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buffer, "JPEG", comment=THUMBNAIL_MARK)
    return buffer.getvalue()


def exif_with_thumbnail(make: str = MAKE, orientation: int | None = None) -> bytes:
    """Hand-build little-endian EXIF whose second directory holds a thumbnail."""
    thumbnail = thumbnail_jpeg()
    make_bytes = make.encode("ascii") + b"\0"
    entries = [(0x010F, 2, len(make_bytes), make_bytes)]
    if orientation is not None:
        entries.append((0x0112, 3, 1, struct.pack("<HH", orientation, 0)))

    first_size = 2 + 12 * len(entries) + 4
    data_offset = 8 + first_size
    extra = b""
    first = struct.pack("<H", len(entries))
    for tag, kind, count, value in sorted(entries):
        if kind == 2:
            slot = struct.pack("<I", data_offset + len(extra))
            extra += value
        else:
            slot = value
        first += struct.pack("<HHI", tag, kind, count) + slot
    second_offset = data_offset + len(extra)
    first += struct.pack("<I", second_offset)

    thumbnail_offset = second_offset + 2 + 12 * 2 + 4
    second = struct.pack("<H", 2)
    second += struct.pack("<HHII", 0x0201, 4, 1, thumbnail_offset)
    second += struct.pack("<HHII", 0x0202, 4, 1, len(thumbnail))
    second += struct.pack("<I", 0)
    return b"Exif\0\0II*\0" + struct.pack("<I", 8) + first + extra + second + thumbnail


_SRGB: list[bytes] = []


def srgb_profile() -> bytes:
    """One sRGB profile for the whole module.

    A new profile carries the time it was made, so two made a second apart
    differ; every test compares against this one.
    """
    if not _SRGB:
        _SRGB.append(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    return _SRGB[0]


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name)

    def listing(self, folder: Path | None = None) -> list[str]:
        return sorted(path.name for path in (folder or self.dir).iterdir())

    def halves(self, name: str, size: tuple[int, int], left, right, **save) -> Path:  # noqa: ANN001
        """Write an image whose left half is one colour and right half another."""
        image = Image.new("RGB", size, left)
        image.paste(right, (size[0] // 2, 0, size[0], size[1]))
        path = self.dir / name
        image.save(path, **save)
        return path

    def two_frame_gif(self, name: str = "anim.gif") -> Path:
        frames = [Image.new("RGB", (10, 10), colour) for colour in [(255, 0, 0), (0, 255, 0)]]
        path = self.dir / name
        frames[0].save(path, save_all=True, append_images=frames[1:])
        return path

    def two_frame_webp(self, name: str = "anim.webp") -> Path:
        frames = [Image.new("RGB", (10, 10), colour) for colour in [(255, 0, 0), (0, 255, 0)]]
        path = self.dir / name
        frames[0].save(path, save_all=True, append_images=frames[1:], lossless=True)
        return path


class CheckRequestTests(unittest.TestCase):
    NEED_ONE = "give one size: --width and/or --height, --percent or --max-side"

    def assertRejected(self, **fields: object) -> None:
        with self.assertRaises(ValueError) as caught:
            resize.check_request(request(**fields))
        self.assertEqual(str(caught.exception), self.NEED_ONE)

    def test_no_size_is_rejected_with_the_usage_text(self) -> None:
        self.assertRejected()

    def test_upscale_alone_is_not_a_size(self) -> None:
        self.assertRejected(upscale=True)

    def test_width_with_percent_is_rejected(self) -> None:
        self.assertRejected(width=100, percent=50.0)

    def test_height_with_percent_is_rejected(self) -> None:
        self.assertRejected(height=100, percent=50.0)

    def test_width_with_max_side_is_rejected(self) -> None:
        self.assertRejected(width=100, max_side=200)

    def test_height_with_max_side_is_rejected(self) -> None:
        self.assertRejected(height=100, max_side=200)

    def test_percent_with_max_side_is_rejected(self) -> None:
        self.assertRejected(percent=50.0, max_side=200)

    def test_all_three_kinds_together_are_rejected(self) -> None:
        self.assertRejected(width=1, percent=50.0, max_side=3)

    def test_each_single_kind_is_accepted(self) -> None:
        for fields in (
            {"width": 100},
            {"height": 100},
            {"width": 100, "height": 50},
            {"percent": 50.0},
            {"max_side": 100},
            {"width": 100, "upscale": True},
        ):
            with self.subTest(fields=fields):
                self.assertIsNone(resize.check_request(request(**fields)))


class PlanSizeTests(unittest.TestCase):
    def assertPlan(self, source: tuple[int, int], expected: tuple[int, int], **fields) -> None:  # noqa: ANN003
        result = resize.plan_size(*source, request(**fields))
        self.assertIsInstance(result, resize.SizePlan, result)
        self.assertEqual((result.width, result.height), expected)

    def test_width_only_follows_the_ratio(self) -> None:
        self.assertPlan((4000, 3000), (1600, 1200), width=1600)

    def test_height_only_follows_the_ratio(self) -> None:
        self.assertPlan((4000, 3000), (800, 600), height=600)

    def test_a_computed_side_is_rounded_to_the_nearest_pixel(self) -> None:
        self.assertPlan((1000, 333), (100, 33), width=100)  # 33.3
        self.assertPlan((1000, 337), (100, 34), width=100)  # 33.7
        self.assertPlan((300, 200), (100, 67), width=100)  # 66.67

    def test_exact_halves_round_the_way_python_rounds(self) -> None:
        self.assertPlan((10, 5), (5, 2), width=5)  # 2.5
        self.assertPlan((10, 7), (5, 4), width=5)  # 3.5

    def test_a_side_that_rounds_to_zero_becomes_one_pixel(self) -> None:
        self.assertPlan((1000, 10), (50, 1), width=50)  # 0.5
        self.assertPlan((1000, 1), (10, 1), width=10)  # 0.01
        self.assertPlan((10, 1000), (1, 50), height=50)

    def test_percent_scales_both_sides(self) -> None:
        self.assertPlan((4000, 3000), (2000, 1500), percent=50.0)
        self.assertPlan((4000, 3000), (400, 300), percent=10)

    def test_percent_rounds_each_side_from_the_source(self) -> None:
        self.assertPlan((101, 51), (34, 17), percent=33.3333)

    def test_percent_has_a_one_pixel_floor_on_each_side(self) -> None:
        self.assertPlan((100, 10), (1, 1), percent=1.0)

    def test_percent_above_100_enlarges(self) -> None:
        result = resize.plan_size(400, 300, request(percent=250.0, upscale=True))
        self.assertIsInstance(result, resize.SizePlan)
        self.assertEqual((result.width, result.height), (1000, 750))
        self.assertTrue(result.enlarged)

    def test_percent_up_to_1000_is_planned(self) -> None:
        self.assertPlan((10, 20), (100, 200), percent=1000.0, upscale=True)

    def test_percent_below_100_is_not_enlarged(self) -> None:
        result = resize.plan_size(400, 300, request(percent=99.0))
        self.assertIsInstance(result, resize.SizePlan)
        self.assertFalse(result.enlarged)

    def test_max_side_limits_the_longer_side_of_a_landscape_image(self) -> None:
        self.assertPlan((4000, 3000), (1000, 750), max_side=1000)

    def test_max_side_limits_the_longer_side_of_a_portrait_image(self) -> None:
        self.assertPlan((3000, 4000), (750, 1000), max_side=1000)

    def test_max_side_on_a_square_image(self) -> None:
        self.assertPlan((500, 500), (200, 200), max_side=200)

    def test_box_fit_keeps_the_width_when_the_width_limits(self) -> None:
        self.assertPlan((4000, 3000), (1000, 750), width=1000, height=1000)

    def test_box_fit_keeps_the_height_when_the_height_limits(self) -> None:
        self.assertPlan((3000, 4000), (750, 1000), width=1000, height=1000)

    def test_box_fit_limiting_side_is_exact_while_the_other_rounds(self) -> None:
        self.assertPlan((7, 3), (5, 2), width=5, height=5)  # 2.14
        self.assertPlan((1234, 567), (100, 46), width=100, height=100)  # 45.95
        self.assertPlan((3, 7), (2, 5), width=5, height=5)  # 2.14 on the width

    def test_box_fit_with_equal_ratios_fills_the_box(self) -> None:
        self.assertPlan((400, 200), (100, 50), width=100, height=50)

    def test_box_fit_into_a_box_much_wider_than_the_image(self) -> None:
        self.assertPlan((4000, 3000), (400, 300), width=5000, height=300)

    def test_box_fit_never_exceeds_the_box(self) -> None:
        for source in [(4000, 3000), (3000, 4000), (1234, 567), (7, 3), (999, 1001)]:
            for box in [(1000, 1000), (640, 480), (100, 900), (33, 17)]:
                with self.subTest(source=source, box=box):
                    result = resize.plan_size(
                        *source, request(width=box[0], height=box[1], upscale=True)
                    )
                    self.assertIsInstance(result, resize.SizePlan)
                    self.assertLessEqual(result.width, box[0])
                    self.assertLessEqual(result.height, box[1])
                    self.assertTrue(result.width == box[0] or result.height == box[1])

    def test_enlarging_is_skipped_without_upscale(self) -> None:
        self.assertEqual(
            resize.plan_size(640, 480, request(width=1280)),
            "already 640x480; not enlarged (add --upscale)",
        )

    def test_enlarging_by_height_or_percent_or_box_is_skipped_too(self) -> None:
        expected = "already 640x480; not enlarged (add --upscale)"
        for fields in (
            {"height": 960},
            {"percent": 150.0},
            {"max_side": 2000},
            {"width": 1280, "height": 960},
            {"width": 2000, "height": 2000},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(resize.plan_size(640, 480, request(**fields)), expected)

    def test_skip_text_uses_the_source_size(self) -> None:
        self.assertEqual(
            resize.plan_size(30, 20, request(max_side=100)),
            "already 30x20; not enlarged (add --upscale)",
        )

    def test_upscale_allows_enlarging_and_marks_the_plan(self) -> None:
        result = resize.plan_size(640, 480, request(width=1280, upscale=True))
        self.assertIsInstance(result, resize.SizePlan)
        self.assertEqual((result.width, result.height), (1280, 960))
        self.assertTrue(result.enlarged)

    def test_upscale_leaves_a_shrinking_request_not_enlarged(self) -> None:
        result = resize.plan_size(640, 480, request(width=320, upscale=True))
        self.assertIsInstance(result, resize.SizePlan)
        self.assertEqual((result.width, result.height), (320, 240))
        self.assertFalse(result.enlarged)

    def test_equal_size_is_skipped_without_upscale(self) -> None:
        self.assertEqual(resize.plan_size(640, 480, request(width=640)), "already 640x480")

    def test_equal_size_is_skipped_with_upscale(self) -> None:
        self.assertEqual(
            resize.plan_size(640, 480, request(width=640, upscale=True)), "already 640x480"
        )

    def test_equal_size_through_every_kind_of_request(self) -> None:
        for fields in (
            {"height": 480},
            {"percent": 100.0},
            {"max_side": 640},
            {"width": 640, "height": 480},
            {"width": 640, "height": 9999},
        ):
            for upscale in (False, True):
                with self.subTest(fields=fields, upscale=upscale):
                    self.assertEqual(
                        resize.plan_size(640, 480, request(upscale=upscale, **fields)),
                        "already 640x480",
                    )

    def test_a_size_that_rounds_back_to_the_source_is_equal(self) -> None:
        self.assertEqual(resize.plan_size(1000, 3, request(width=1000)), "already 1000x3")
        self.assertEqual(resize.plan_size(100, 10, request(percent=100.4)), "already 100x10")


class ResizedNameTests(unittest.TestCase):
    def test_name_is_stem_size_and_extension(self) -> None:
        self.assertEqual(resize.resized_name(Path("a.jpg"), 1600, 1200, "jpg"), "a_1600x1200.jpg")

    def test_directory_is_not_part_of_the_name(self) -> None:
        self.assertEqual(
            resize.resized_name(Path("photos") / "trip" / "a.png", 10, 5, "png"), "a_10x5.png"
        )

    def test_extension_is_used_as_given(self) -> None:
        self.assertEqual(resize.resized_name(Path("IMG.JPG"), 1600, 1200, "JPG"), "IMG_1600x1200.JPG")
        self.assertEqual(resize.resized_name(Path("IMG.JPG"), 800, 600, "webp"), "IMG_800x600.webp")

    def test_only_the_last_suffix_is_replaced(self) -> None:
        self.assertEqual(resize.resized_name(Path("my.holiday.png"), 3, 2, "jpg"), "my.holiday_3x2.jpg")


class ResizedStemTests(unittest.TestCase):
    def test_stems_that_end_in_a_size_match(self) -> None:
        for stem in ("photo_1600x1200", "a_1x1", "my_photo_800x600", "a_b_10x20"):
            with self.subTest(stem=stem):
                self.assertIsNotNone(resize.RESIZED_STEM.search(stem))

    def test_other_stems_do_not_match(self) -> None:
        for stem in (
            "photo",
            "photo_1600",
            "photo_1600x",
            "photo_axb",
            "1600x1200",
            "photo_1600x1200_final",
            "photo_1600x1200x3",
            "photo 1600x1200",
        ):
            with self.subTest(stem=stem):
                self.assertIsNone(resize.RESIZED_STEM.search(stem))

    def test_names_made_by_resized_name_are_matched(self) -> None:
        name = resize.resized_name(Path("a.jpg"), 1600, 1200, "jpg")
        self.assertIsNotNone(resize.RESIZED_STEM.search(Path(name).stem))


class ResizeTargetsTests(unittest.TestCase):
    def test_names_sit_beside_their_sources_and_keep_their_extensions(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [(root / "IMG.JPG", plan(1600, 1200)), (root / "b.png", plan(10, 5))],
            extension=None,
            outdir=None,
            root=None,
        )
        self.assertEqual(targets, [root / "IMG_1600x1200.JPG", root / "b_10x5.png"])

    def test_to_extension_replaces_every_source_extension(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [(root / "a.jpg", plan(10, 5)), (root / "b.PNG", plan(20, 10))],
            extension="webp",
            outdir=None,
            root=None,
        )
        self.assertEqual(targets, [root / "a_10x5.webp", root / "b_20x10.webp"])

    def test_outdir_collects_all_outputs(self) -> None:
        targets = resize.resize_targets(
            [(Path("one") / "a.jpg", plan(10, 5)), (Path("two") / "b.png", plan(4, 4))],
            extension=None,
            outdir=Path("out"),
            root=None,
        )
        self.assertEqual(targets, [Path("out") / "a_10x5.jpg", Path("out") / "b_4x4.png"])

    def test_root_mirrors_the_source_folders_under_outdir(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [
                (root / "top.jpg", plan(10, 5)),
                (root / "a" / "x.jpg", plan(10, 5)),
                (root / "a" / "b" / "y.png", plan(8, 8)),
            ],
            extension=None,
            outdir=Path("out"),
            root=root,
        )
        self.assertEqual(
            targets,
            [
                Path("out") / "top_10x5.jpg",
                Path("out") / "a" / "x_10x5.jpg",
                Path("out") / "a" / "b" / "y_8x8.png",
            ],
        )

    def test_same_name_in_different_folders_does_not_collide_when_mirrored(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [(root / "a" / "x.jpg", plan(10, 5)), (root / "b" / "x.jpg", plan(10, 5))],
            extension=None,
            outdir=Path("out"),
            root=root,
        )
        self.assertEqual(targets, [Path("out") / "a" / "x_10x5.jpg", Path("out") / "b" / "x_10x5.jpg"])

    def test_sources_that_share_an_output_get_numbered_after_the_stem(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [
                (root / "a.png", plan(10, 5)),
                (root / "a.webp", plan(10, 5)),
                (root / "a.bmp", plan(10, 5)),
            ],
            extension="jpg",
            outdir=None,
            root=None,
        )
        self.assertEqual(
            targets, [root / "a_10x5.jpg", root / "a_10x5-2.jpg", root / "a_10x5-3.jpg"]
        )

    def test_same_stem_from_two_folders_collides_in_a_flat_outdir(self) -> None:
        targets = resize.resize_targets(
            [(Path("a") / "x.jpg", plan(10, 5)), (Path("b") / "x.jpg", plan(10, 5))],
            extension=None,
            outdir=Path("out"),
            root=None,
        )
        self.assertEqual(targets, [Path("out") / "x_10x5.jpg", Path("out") / "x_10x5-2.jpg"])

    def test_different_planned_sizes_do_not_collide(self) -> None:
        root = Path("pics")
        targets = resize.resize_targets(
            [(root / "a.png", plan(10, 5)), (root / "a.webp", plan(20, 10))],
            extension="jpg",
            outdir=None,
            root=None,
        )
        self.assertEqual(targets, [root / "a_10x5.jpg", root / "a_20x10.jpg"])

    def test_no_items_give_no_targets(self) -> None:
        self.assertEqual(resize.resize_targets([], extension=None, outdir=None, root=None), [])


class ProbeTests(TempDirCase):
    def test_reports_size_and_one_frame_for_a_still_image(self) -> None:
        path = self.dir / "a.png"
        Image.new("RGB", (30, 20)).save(path)
        self.assertEqual(pillow_engine.probe(path), (30, 20, 1))

    def test_reports_the_upright_size_for_orientation_6(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6
        path = self.dir / "sideways.jpg"
        Image.new("RGB", (40, 20)).save(path, exif=exif.tobytes())
        self.assertEqual(pillow_engine.probe(path), (20, 40, 1))

    def test_reports_the_stored_size_for_orientation_1_and_3(self) -> None:
        for orientation in (1, 3):
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[0x0112] = orientation
                path = self.dir / f"o{orientation}.jpg"
                Image.new("RGB", (40, 20)).save(path, exif=exif.tobytes())
                self.assertEqual(pillow_engine.probe(path), (40, 20, 1))

    def test_swaps_the_sides_for_every_quarter_turn_orientation(self) -> None:
        for orientation in (5, 6, 7, 8):
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[0x0112] = orientation
                path = self.dir / f"q{orientation}.jpg"
                Image.new("RGB", (40, 20)).save(path, exif=exif.tobytes())
                self.assertEqual(pillow_engine.probe(path), (20, 40, 1))

    def test_counts_the_frames_of_an_animated_gif(self) -> None:
        self.assertEqual(pillow_engine.probe(self.two_frame_gif()), (10, 10, 2))

    def test_counts_the_frames_of_an_animated_webp(self) -> None:
        self.assertEqual(pillow_engine.probe(self.two_frame_webp()), (10, 10, 2))

    def test_a_still_gif_has_one_frame(self) -> None:
        path = self.dir / "still.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(path)
        self.assertEqual(pillow_engine.probe(path), (10, 10, 1))

    def test_a_file_that_is_not_an_image_raises(self) -> None:
        path = self.dir / "junk.png"
        path.write_bytes(b"this is not a picture at all")
        with self.assertRaises(pillow_engine.ConversionError) as caught:
            pillow_engine.probe(path)
        self.assertEqual(str(caught.exception), "not a readable image")

    def test_an_empty_file_raises(self) -> None:
        path = self.dir / "empty.jpg"
        path.write_bytes(b"")
        with self.assertRaises(pillow_engine.ConversionError):
            pillow_engine.probe(path)

    def test_the_size_is_read_without_the_pixel_data(self) -> None:
        path = self.dir / "cut.bmp"
        Image.new("RGB", (30, 20), (1, 2, 3)).save(path)
        path.write_bytes(path.read_bytes()[:70])
        self.assertEqual(pillow_engine.probe(path), (30, 20, 1))


class ConvertSizeTests(TempDirCase):
    def test_output_has_exactly_the_requested_size(self) -> None:
        source = self.dir / "a.png"
        Image.new("RGB", (100, 60), (10, 20, 30)).save(source)
        for extension in ("png", "jpg", "webp", "bmp", "tif"):
            with self.subTest(extension=extension):
                target = self.dir / f"out.{extension}"
                pillow_engine.convert(source, target, size=(33, 20))
                with Image.open(target) as result:
                    self.assertEqual(result.size, (33, 20))

    def test_size_is_exact_even_when_the_ratio_differs(self) -> None:
        source = self.dir / "a.png"
        Image.new("RGB", (100, 60)).save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(7, 50))
        with Image.open(target) as result:
            self.assertEqual(result.size, (7, 50))

    def test_enlarging_gives_the_exact_size_too(self) -> None:
        source = self.dir / "a.png"
        Image.new("RGB", (10, 6), (200, 100, 50)).save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(35, 21))
        with Image.open(target) as result:
            self.assertEqual(result.size, (35, 21))
            self.assertTrue(near(result.getpixel((17, 10)), (200, 100, 50), 2))

    def test_pixels_are_resampled_with_lanczos(self) -> None:
        generator = random.Random(7)
        source_image = Image.new("RGB", (64, 48))
        source_image.putdata(
            [tuple(generator.randrange(256) for _ in range(3)) for _ in range(64 * 48)]
        )
        source = self.dir / "noise.png"
        source_image.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(20, 15))
        with Image.open(target) as result:
            expected = source_image.resize((20, 15), LANCZOS)
            self.assertEqual(result.convert("RGB").tobytes(), expected.tobytes())

    def test_orientation_is_applied_before_resizing(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6
        source = self.halves("sideways.jpg", (40, 20), (255, 0, 0), (0, 0, 255), exif=exif.tobytes(), quality=95)
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(10, 20))
        with Image.open(target) as result:
            self.assertEqual(result.size, (10, 20))
            self.assertTrue(near(result.getpixel((5, 3)), (255, 0, 0)), result.getpixel((5, 3)))
            self.assertTrue(near(result.getpixel((5, 17)), (0, 0, 255)), result.getpixel((5, 17)))
            self.assertIn(result.getexif().get(0x0112), (None, 1))

    def test_a_palette_image_without_transparency_keeps_its_colours(self) -> None:
        palette = Image.new("P", (16, 16), 0)
        palette.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        palette.paste(1, (8, 0, 16, 16))
        source = self.dir / "palette.png"
        palette.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(8, 8))
        with Image.open(target) as result:
            self.assertEqual(result.size, (8, 8))
            rgb = result.convert("RGB")
            self.assertTrue(near(rgb.getpixel((0, 4)), (255, 0, 0), 4), rgb.getpixel((0, 4)))
            self.assertTrue(near(rgb.getpixel((7, 4)), (0, 0, 255), 4), rgb.getpixel((7, 4)))

    def test_a_palette_image_keeps_its_transparency(self) -> None:
        palette = Image.new("P", (16, 4), 0)
        palette.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        palette.paste(1, (8, 0, 16, 4))
        palette.info["transparency"] = 0
        source = self.dir / "palette.png"
        palette.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(8, 2))
        with Image.open(target) as result:
            rgba = result.convert("RGBA")
            self.assertEqual(rgba.size, (8, 2))
            self.assertEqual(rgba.getpixel((0, 0))[3], 0)
            self.assertEqual(rgba.getpixel((7, 0)), (255, 0, 0, 255))

    def test_a_one_bit_image_is_resized(self) -> None:
        bilevel = Image.new("1", (16, 16), 0)
        bilevel.paste(1, (0, 0, 8, 16))
        source = self.dir / "bilevel.png"
        bilevel.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(8, 8))
        with Image.open(target) as result:
            self.assertEqual(result.size, (8, 8))
            grey = result.convert("L")
            self.assertGreaterEqual(grey.getpixel((0, 4)), 245)
            self.assertLessEqual(grey.getpixel((7, 4)), 10)

    def test_sixteen_bit_grey_keeps_its_brightness(self) -> None:
        source = self.dir / "deep.png"
        Image.new("I;16", (8, 8), 40000).save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(4, 4))
        with Image.open(target) as result:
            self.assertEqual(result.size, (4, 4))
            full_scale = 65535 if result.mode == "I;16" else 255
            level = result.getpixel((1, 1)) / full_scale
            self.assertAlmostEqual(level, 40000 / 65535, delta=0.03)

    def test_cmyk_keeps_its_colours(self) -> None:
        source = self.dir / "print.tif"
        Image.new("CMYK", (16, 16), (0, 255, 255, 0)).save(source)
        for extension in ("jpg", "png"):
            with self.subTest(extension=extension):
                target = self.dir / f"out.{extension}"
                pillow_engine.convert(source, target, size=(8, 8))
                with Image.open(target) as result:
                    self.assertEqual(result.size, (8, 8))
                    self.assertTrue(
                        near(result.convert("RGB").getpixel((4, 4)), (255, 0, 0), 20),
                        result.convert("RGB").getpixel((4, 4)),
                    )

    def test_rgba_edges_have_no_dark_fringe_after_a_downscale(self) -> None:
        image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        image.putpixel((1, 1), (255, 0, 0, 255))
        source = self.dir / "dot.png"
        image.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(2, 2))
        with Image.open(target) as result:
            red, green, blue, alpha = result.convert("RGBA").getpixel((0, 0))
        self.assertGreater(alpha, 0)
        self.assertGreaterEqual(red, 250)
        self.assertLessEqual(green, 5)
        self.assertLessEqual(blue, 5)

    def test_grey_alpha_edges_have_no_dark_fringe_after_a_downscale(self) -> None:
        image = Image.new("LA", (4, 4), (0, 0))
        image.putpixel((1, 1), (255, 255))
        source = self.dir / "dot.png"
        image.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(2, 2))
        with Image.open(target) as result:
            grey, alpha = result.convert("LA").getpixel((0, 0))
        self.assertGreater(alpha, 0)
        self.assertGreaterEqual(grey, 250)

    def test_transparent_areas_stay_transparent_in_a_png_target(self) -> None:
        image = Image.new("RGBA", (16, 4), (0, 0, 0, 0))
        image.paste((0, 0, 255, 255), (8, 0, 16, 4))
        source = self.dir / "half.png"
        image.save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(8, 2))
        with Image.open(target) as result:
            rgba = result.convert("RGBA")
            self.assertEqual(rgba.getpixel((0, 0))[3], 0)
            self.assertEqual(rgba.getpixel((7, 0)), (0, 0, 255, 255))

    def _half_transparent_png(self) -> Path:
        image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        image.paste((0, 0, 255, 255), (16, 0, 32, 32))
        source = self.dir / "half.png"
        image.save(source)
        return source

    def test_background_fills_transparent_areas_of_a_jpg_target(self) -> None:
        source = self._half_transparent_png()
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(16, 16), background=(0, 0, 0))
        with Image.open(target) as result:
            self.assertEqual(result.size, (16, 16))
            self.assertTrue(near(result.getpixel((2, 8)), (0, 0, 0)), result.getpixel((2, 8)))
            self.assertTrue(near(result.getpixel((13, 8)), (0, 0, 255)), result.getpixel((13, 8)))

    def test_transparent_areas_are_white_in_a_jpg_target_by_default(self) -> None:
        source = self._half_transparent_png()
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(16, 16))
        with Image.open(target) as result:
            self.assertTrue(near(result.getpixel((2, 8)), (255, 255, 255)), result.getpixel((2, 8)))

    def test_quality_follows_convert_rules(self) -> None:
        generator = random.Random(3)
        noise = Image.new("RGB", (120, 120))
        noise.putdata([tuple(generator.randrange(256) for _ in range(3)) for _ in range(120 * 120)])
        source = self.dir / "noise.png"
        noise.save(source)
        default = self.dir / "default.jpg"
        low = self.dir / "low.jpg"
        high = self.dir / "high.jpg"
        pillow_engine.convert(source, default, size=(100, 100))
        pillow_engine.convert(source, low, size=(100, 100), quality=10)
        pillow_engine.convert(source, high, size=(100, 100), quality=100)
        self.assertLess(low.stat().st_size, default.stat().st_size)
        self.assertLess(default.stat().st_size, high.stat().st_size)

        reference = self.dir / "reference.jpg"
        resized = noise.resize((100, 100), LANCZOS)
        resized.save(reference, quality=90)
        with Image.open(default) as made, Image.open(reference) as expected:
            self.assertEqual(made.tobytes(), expected.tobytes())


class ConvertAnimatedTests(TempDirCase):
    def assertRefused(self, source: Path, target: Path) -> None:
        with self.assertRaises(pillow_engine.ConversionError) as caught:
            pillow_engine.convert(source, target, size=(5, 5))
        self.assertEqual(str(caught.exception), ANIMATED_MESSAGE)
        self.assertFalse(target.exists())
        self.assertEqual(self.listing(), [source.name])

    def test_animated_gif_is_refused_and_nothing_is_written(self) -> None:
        source = self.two_frame_gif()
        self.assertRefused(source, self.dir / "out.gif")

    def test_animated_gif_to_a_still_format_is_refused_too(self) -> None:
        source = self.two_frame_gif()
        self.assertRefused(source, self.dir / "out.png")

    def test_animated_webp_is_refused_and_nothing_is_written(self) -> None:
        source = self.two_frame_webp()
        self.assertRefused(source, self.dir / "out.webp")

    def test_an_existing_target_is_left_alone_when_the_source_is_animated(self) -> None:
        source = self.two_frame_gif()
        target = self.dir / "out.png"
        target.write_bytes(b"precious")
        with self.assertRaises(pillow_engine.ConversionError):
            pillow_engine.convert(source, target, size=(5, 5))
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual(self.listing(), ["anim.gif", "out.png"])

    def test_a_still_gif_is_resized(self) -> None:
        source = self.dir / "still.gif"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(source)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(5, 5))
        with Image.open(target) as result:
            self.assertEqual(result.size, (5, 5))


class ConvertMetadataTests(TempDirCase):
    def tagged_jpeg(self, name: str = "tagged.jpg", **extra: object) -> Path:
        path = self.dir / name
        Image.new("RGB", (40, 20), (200, 30, 30)).save(
            path,
            exif=exif_with_thumbnail(orientation=6),
            icc_profile=srgb_profile(),
            xmp=XMP_PACKET,
            comment=COMMENT_MARK,
            **extra,
        )
        return path

    def test_the_fixture_really_carries_what_the_tests_look_for(self) -> None:
        source = self.tagged_jpeg()
        data = source.read_bytes()
        self.assertIn(THUMBNAIL_MARK, data)
        self.assertIn(XMP_MARK, data)
        self.assertIn(COMMENT_MARK, data)
        with Image.open(source) as image:
            self.assertEqual(image.getexif().get(0x010F), MAKE)
            self.assertEqual(image.info["icc_profile"], srgb_profile())

    def test_strip_removes_exif_and_xmp_but_keeps_the_colour_profile(self) -> None:
        source = self.tagged_jpeg()
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(10, 20), strip=True)
        data = target.read_bytes()
        self.assertNotIn(MAKE.encode(), data)
        self.assertNotIn(XMP_MARK, data)
        self.assertNotIn(COMMENT_MARK, data)
        with Image.open(target) as result:
            self.assertEqual(result.size, (10, 20))
            self.assertNotIn(0x010F, result.getexif())
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_strip_to_png_removes_exif_text_and_xmp_but_keeps_the_profile(self) -> None:
        source = self.tagged_jpeg()
        target = self.dir / "out.png"
        pillow_engine.convert(source, target, size=(10, 20), strip=True)
        data = target.read_bytes()
        self.assertNotIn(MAKE.encode(), data)
        self.assertNotIn(XMP_MARK, data)
        self.assertNotIn(COMMENT_MARK, data)
        with Image.open(target) as result:
            self.assertNotIn(0x010F, result.getexif())
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_strip_removes_png_text_and_xmp(self) -> None:
        from PIL import PngImagePlugin

        text = PngImagePlugin.PngInfo()
        text.add_text("XML:com.adobe.xmp", XMP_PACKET.decode())
        text.add_text("comment", COMMENT_MARK.decode())
        source = self.dir / "text.png"
        Image.new("RGB", (20, 20), (1, 2, 3)).save(source, pnginfo=text, icc_profile=srgb_profile())
        for extension in ("png", "jpg"):
            with self.subTest(extension=extension):
                target = self.dir / f"out.{extension}"
                pillow_engine.convert(source, target, size=(10, 10), strip=True)
                data = target.read_bytes()
                self.assertNotIn(XMP_MARK, data)
                self.assertNotIn(COMMENT_MARK, data)
                with Image.open(target) as result:
                    self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_exif_is_kept_without_strip(self) -> None:
        source = self.tagged_jpeg()
        for extension in ("jpg", "png"):
            with self.subTest(extension=extension):
                target = self.dir / f"out.{extension}"
                pillow_engine.convert(source, target, size=(10, 20))
                with Image.open(target) as result:
                    self.assertEqual(result.size, (10, 20))
                    self.assertEqual(result.getexif().get(0x010F), MAKE)
                    self.assertEqual(result.info.get("icc_profile"), srgb_profile())

    def test_the_orientation_tag_is_gone_because_the_pixels_are_upright(self) -> None:
        source = self.tagged_jpeg()
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(10, 20))
        with Image.open(target) as result:
            self.assertIn(result.getexif().get(0x0112), (None, 1))

    def test_an_exif_thumbnail_is_never_written(self) -> None:
        source = self.tagged_jpeg()
        for extension in ("jpg", "png", "webp"):
            for strip in (False, True):
                with self.subTest(extension=extension, strip=strip):
                    target = self.dir / f"out_{strip}.{extension}"
                    pillow_engine.convert(source, target, size=(10, 20), strip=strip)
                    self.assertNotIn(THUMBNAIL_MARK, target.read_bytes())
                    with Image.open(target) as result:
                        self.assertNotIn(1, result.getexif()._ifds)

    def test_exif_without_a_thumbnail_is_unchanged_by_the_thumbnail_rule(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = MAKE
        source = self.dir / "plain.jpg"
        Image.new("RGB", (40, 20)).save(source, exif=exif.tobytes())
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, size=(20, 10))
        with Image.open(target) as result:
            self.assertEqual(result.getexif().get(0x010F), MAKE)

    def test_the_source_file_is_not_modified(self) -> None:
        source = self.tagged_jpeg()
        before = source.read_bytes()
        pillow_engine.convert(source, self.dir / "out.jpg", size=(10, 20), strip=True)
        self.assertEqual(source.read_bytes(), before)


class ConvertWithoutSizeTests(TempDirCase):
    def test_convert_keeps_the_size_turns_pixels_upright_and_keeps_exif(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6
        exif[0x010F] = MAKE
        source = self.halves("sideways.jpg", (40, 20), (255, 0, 0), (0, 0, 255), exif=exif.tobytes(), quality=95)
        target = self.dir / "out.png"
        pillow_engine.convert(source, target)
        with Image.open(target) as result:
            self.assertEqual(result.size, (20, 40))
            self.assertTrue(near(result.convert("RGB").getpixel((10, 5)), (255, 0, 0)))
            self.assertTrue(near(result.convert("RGB").getpixel((10, 35)), (0, 0, 255)))
            self.assertEqual(result.getexif().get(0x010F), MAKE)

    def test_convert_without_size_leaves_pixels_untouched(self) -> None:
        generator = random.Random(5)
        noise = Image.new("RGB", (30, 20))
        noise.putdata([tuple(generator.randrange(256) for _ in range(3)) for _ in range(600)])
        source = self.dir / "noise.png"
        noise.save(source)
        target = self.dir / "out.bmp"
        pillow_engine.convert(source, target)
        with Image.open(target) as result:
            self.assertEqual(result.tobytes(), noise.tobytes())

    def test_strip_works_without_a_size(self) -> None:
        source = self.dir / "tagged.jpg"
        Image.new("RGB", (40, 20), (9, 9, 9)).save(
            source, exif=exif_with_thumbnail(), icc_profile=srgb_profile()
        )
        target = self.dir / "out.jpg"
        pillow_engine.convert(source, target, strip=True)
        self.assertNotIn(MAKE.encode(), target.read_bytes())
        with Image.open(target) as result:
            self.assertEqual(result.size, (40, 20))
            self.assertEqual(result.info.get("icc_profile"), srgb_profile())


class ResizeFileTests(TempDirCase):
    def run_resize(self, source: Path, target: Path, size_plan: resize.SizePlan, **options: object):  # noqa: ANN201
        settings = {
            "quality": None,
            "background": None,
            "strip": False,
            "overwrite": False,
            "dry_run": False,
        }
        settings.update(options)
        return resize.resize_file(source, target, size_plan, **settings)

    def png(self, name: str = "a.png", size: tuple[int, int] = (40, 20)) -> Path:
        path = self.dir / name
        Image.new("RGB", size, (30, 60, 90)).save(path)
        return path

    def test_a_resize_reports_the_sizes_and_writes_the_file(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.png"
        result = self.run_resize(source, target, plan(20, 10))
        self.assertEqual(result.status, "resized")
        self.assertEqual(result.detail, "40x20 -> 20x10")
        self.assertEqual(result.source, source)
        self.assertEqual(result.target, target)
        self.assertEqual(result.command, ())
        self.assertTrue(result.ok)
        with Image.open(target) as written:
            self.assertEqual(written.size, (20, 10))

    def test_an_enlargement_adds_the_soft_note(self) -> None:
        source = self.png(size=(10, 10))
        target = self.dir / "a_20x20.png"
        result = self.run_resize(source, target, plan(20, 20, enlarged=True))
        self.assertEqual(result.status, "resized")
        self.assertEqual(result.detail, "10x10 -> 20x20, enlarged; may look soft")
        with Image.open(target) as written:
            self.assertEqual(written.size, (20, 20))

    def test_the_detail_uses_the_upright_source_size(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6
        source = self.dir / "sideways.jpg"
        Image.new("RGB", (40, 20)).save(source, exif=exif.tobytes())
        target = self.dir / "sideways_10x20.jpg"
        result = self.run_resize(source, target, plan(10, 20))
        self.assertEqual(result.status, "resized")
        self.assertEqual(result.detail, "20x40 -> 10x20")
        with Image.open(target) as written:
            self.assertEqual(written.size, (10, 20))

    def test_the_target_folder_is_created(self) -> None:
        source = self.png()
        target = self.dir / "out" / "deep" / "a_20x10.png"
        result = self.run_resize(source, target, plan(20, 10))
        self.assertEqual(result.status, "resized")
        self.assertTrue(target.is_file())

    def test_a_different_target_extension_sets_the_format(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.jpg"
        result = self.run_resize(source, target, plan(20, 10))
        self.assertEqual(result.status, "resized")
        with Image.open(target) as written:
            self.assertEqual(written.format, "JPEG")

    def test_options_reach_the_engine(self) -> None:
        source = self.dir / "tagged.jpg"
        Image.new("RGB", (40, 20), (9, 9, 9)).save(source, exif=exif_with_thumbnail())
        kept = self.dir / "kept_20x10.jpg"
        stripped = self.dir / "stripped_20x10.jpg"
        self.run_resize(source, kept, plan(20, 10))
        self.run_resize(source, stripped, plan(20, 10), strip=True)
        self.assertIn(MAKE.encode(), kept.read_bytes())
        self.assertNotIn(MAKE.encode(), stripped.read_bytes())

        half = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        half.paste((0, 0, 255, 255), (16, 0, 32, 32))
        transparent = self.dir / "half.png"
        half.save(transparent)
        black = self.dir / "black_16x16.jpg"
        self.run_resize(transparent, black, plan(16, 16), background=(0, 0, 0))
        with Image.open(black) as written:
            self.assertTrue(near(written.getpixel((2, 8)), (0, 0, 0)), written.getpixel((2, 8)))

    def test_quality_reaches_the_engine(self) -> None:
        generator = random.Random(9)
        noise = Image.new("RGB", (120, 120))
        noise.putdata([tuple(generator.randrange(256) for _ in range(3)) for _ in range(120 * 120)])
        source = self.dir / "noise.png"
        noise.save(source)
        low = self.dir / "low_100x100.jpg"
        high = self.dir / "high_100x100.jpg"
        self.run_resize(source, low, plan(100, 100), quality=10)
        self.run_resize(source, high, plan(100, 100), quality=95)
        self.assertLess(low.stat().st_size, high.stat().st_size)

    def test_a_dry_run_plans_and_writes_nothing(self) -> None:
        source = self.png()
        target = self.dir / "out" / "a_20x10.png"
        result = self.run_resize(source, target, plan(20, 10), dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.source, source)
        self.assertEqual(result.target, target)
        self.assertTrue(result.ok)
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())
        self.assertEqual(self.listing(), ["a.png"])

    def test_a_dry_run_with_an_existing_target_and_overwrite_leaves_it_alone(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.png"
        target.write_bytes(b"precious")
        result = self.run_resize(source, target, plan(20, 10), overwrite=True, dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(target.read_bytes(), b"precious")

    def test_the_source_as_target_is_skipped(self) -> None:
        source = self.png()
        before = source.read_bytes()
        result = self.run_resize(source, source, plan(20, 10))
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.detail, "source is the target")
        self.assertTrue(result.ok)
        self.assertEqual(source.read_bytes(), before)

    def test_the_source_as_target_is_skipped_even_with_overwrite_and_dry_run(self) -> None:
        source = self.png()
        before = source.read_bytes()
        for options in ({"overwrite": True}, {"dry_run": True}, {"overwrite": True, "dry_run": True}):
            with self.subTest(options=options):
                result = self.run_resize(source, source, plan(20, 10), **options)
                self.assertEqual(result.status, "skipped")
                self.assertEqual(result.detail, "source is the target")
                self.assertEqual(source.read_bytes(), before)

    def test_an_existing_target_is_skipped_without_overwrite(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.png"
        target.write_bytes(b"precious")
        result = self.run_resize(source, target, plan(20, 10))
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.detail, "target exists (use --overwrite)")
        self.assertTrue(result.ok)
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual(self.listing(), ["a.png", "a_20x10.png"])

    def test_an_existing_target_is_skipped_in_a_dry_run_too(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.png"
        target.write_bytes(b"precious")
        result = self.run_resize(source, target, plan(20, 10), dry_run=True)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.detail, "target exists (use --overwrite)")
        self.assertEqual(target.read_bytes(), b"precious")

    def test_overwrite_replaces_an_existing_target(self) -> None:
        source = self.png()
        target = self.dir / "a_20x10.png"
        target.write_bytes(b"old")
        result = self.run_resize(source, target, plan(20, 10), overwrite=True)
        self.assertEqual(result.status, "resized")
        with Image.open(target) as written:
            self.assertEqual(written.size, (20, 10))

    def test_a_file_that_is_not_an_image_fails_and_leaves_nothing(self) -> None:
        source = self.dir / "junk.png"
        source.write_bytes(b"not a picture")
        target = self.dir / "junk_20x10.png"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = self.run_resize(source, target, plan(20, 10))
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "not a readable image")
        self.assertEqual(self.listing(), ["junk.png"])

    def test_an_animated_source_fails_with_the_refusal_and_leaves_nothing(self) -> None:
        source = self.two_frame_gif()
        target = self.dir / "anim_5x5.gif"
        result = self.run_resize(source, target, plan(5, 5))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.detail, ANIMATED_MESSAGE)
        self.assertEqual(self.listing(), ["anim.gif"])


if __name__ == "__main__":
    unittest.main()

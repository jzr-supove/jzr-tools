"""Tests of the image facts reader: ``read_info``, ``info_rows`` and ``info_as_dict``.

Every fixture is built with Pillow inside a temporary folder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

import pillow_heif
from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

from jzr_tools.core import image_info
from jzr_tools.core.image_info import ImageInfo, info_as_dict, info_rows, read_info
from jzr_tools.core.media import format_size
from jzr_tools.core.pillow_engine import ConversionError

pillow_heif.register_heif_opener()

DICT_KEYS = [
    "file", "format", "width", "height", "orientation", "upright_width",
    "upright_height", "mode", "file_size", "frames", "loop", "duration_ms",
    "transparency", "color_profile", "dpi", "camera", "taken", "software",
    "gps", "metadata",
]

XMP_PACKET = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
    b'<?xpacket end="w"?>'
)


def make_exif(
    *,
    make: str | None = None,
    model: str | None = None,
    taken: str | None = None,
    software: str | None = None,
    orientation: int | None = None,
    gps: tuple | None = None,
) -> Image.Exif:
    exif = Image.Exif()
    if make is not None:
        exif[0x010F] = make
    if model is not None:
        exif[0x0110] = model
    if software is not None:
        exif[0x0131] = software
    if orientation is not None:
        exif[0x0112] = orientation
    if taken is not None:
        exif.get_ifd(0x8769)[0x9003] = taken
    if gps is not None:
        lat_ref, lat, lon_ref, lon = gps
        ifd = exif.get_ifd(0x8825)
        ifd[1] = lat_ref
        ifd[2] = lat
        ifd[3] = lon_ref
        ifd[4] = lon
    return exif


def blank_info(**changes: object) -> ImageInfo:
    values: dict[str, object] = dict(
        path=Path("sample.png"), format="PNG", width=40, height=20, orientation=None,
        mode="RGB", file_size=1234, frames=1, loop=None, duration_ms=None,
        transparency=False, color_profile=None, dpi=None, camera=None, taken=None,
        software=None, gps=None, metadata=(),
    )
    values.update(changes)
    return ImageInfo(**values)  # type: ignore[arg-type]


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def jpeg(self, name: str = "photo.jpg", size: tuple[int, int] = (40, 20), **save: object) -> Path:
        path = self.root / name
        Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG", **save)
        return path

    def png(self, name: str = "pic.png", mode: str = "RGB", **save: object) -> Path:
        path = self.root / name
        Image.new(mode, (40, 20)).save(path, "PNG", **save)
        return path


def rows_of(info: ImageInfo, **kwargs: object) -> dict[str, str]:
    return dict(info_rows(info, **kwargs))  # type: ignore[arg-type]


class BasicFactsTests(TempCase):
    def test_plain_jpeg(self) -> None:
        path = self.jpeg()
        info = read_info(path)
        self.assertEqual(info.path, path)
        self.assertEqual(info.format, "JPEG")
        self.assertEqual((info.width, info.height), (40, 20))
        self.assertIsNone(info.orientation)
        self.assertEqual(info.mode, "RGB")
        self.assertEqual(info.file_size, os.path.getsize(path))
        self.assertEqual(info.frames, 1)
        self.assertIsNone(info.loop)
        self.assertIsNone(info.duration_ms)
        self.assertFalse(info.transparency)
        self.assertIsNone(info.color_profile)
        self.assertIsNone(info.camera)
        self.assertIsNone(info.taken)
        self.assertIsNone(info.software)
        self.assertIsNone(info.gps)
        self.assertEqual(info.upright_size, (40, 20))
        self.assertEqual(tuple(info.metadata), ())

    def test_plain_image_rows_are_the_basic_five_in_order(self) -> None:
        path = self.png()
        rows = info_rows(read_info(path))
        self.assertEqual(
            rows,
            [
                ("format", "PNG"),
                ("size", "40x20"),
                ("mode", "RGB, 8 bits per channel"),
                ("file size", format_size(os.path.getsize(path))),
                ("frames", "1"),
            ],
        )

    def test_png_named_jpg_reports_its_real_format(self) -> None:
        path = self.root / "lies.jpg"
        Image.new("RGB", (10, 10)).save(path, "PNG")
        self.assertEqual(read_info(path).format, "PNG")

    def test_junk_file_raises_conversion_error(self) -> None:
        path = self.root / "junk.png"
        path.write_bytes(b"this is not an image at all" * 10)
        with self.assertRaises(ConversionError) as caught:
            read_info(path)
        self.assertEqual(str(caught.exception), "not a readable image")

    def test_empty_file_raises_conversion_error(self) -> None:
        path = self.root / "empty.jpg"
        path.write_bytes(b"")
        with self.assertRaises(ConversionError):
            read_info(path)

    def test_heic_reports_heif_and_size(self) -> None:
        path = self.root / "shot.heic"
        Image.new("RGB", (64, 48), (10, 120, 200)).save(path, "HEIF")
        info = read_info(path)
        self.assertEqual(info.format, "HEIF")
        self.assertEqual((info.width, info.height), (64, 48))
        self.assertEqual(info.frames, 1)
        self.assertIsNone(info.orientation)
        self.assertEqual(rows_of(info)["size"], "64x48")

    def test_pixels_are_not_decoded_for_a_still_image(self) -> None:
        path = self.jpeg()
        original_load = Image.Image.load
        calls: list[object] = []

        def spy(self_: Image.Image, *args: object, **kwargs: object) -> object:
            calls.append(self_)
            return original_load(self_, *args, **kwargs)

        Image.Image.load = spy  # type: ignore[method-assign]
        try:
            read_info(path)
        finally:
            Image.Image.load = original_load  # type: ignore[method-assign]
        self.assertEqual(calls, [])


class ExifTests(TempCase):
    def test_camera_taken_software(self) -> None:
        exif = make_exif(
            make="Apple", model="iPhone 15", taken="2026:10:02 14:31:05", software="17.6"
        )
        info = read_info(self.jpeg(exif=exif))
        self.assertEqual(info.camera, "Apple iPhone 15")
        self.assertEqual(info.taken, "2026:10:02 14:31:05")
        self.assertEqual(info.software, "17.6")
        self.assertIn("EXIF", info.metadata)
        rows = rows_of(info)
        self.assertEqual(rows["camera"], "Apple iPhone 15")
        self.assertEqual(rows["taken"], "2026:10:02 14:31:05")
        self.assertEqual(rows["software"], "17.6")

    def test_model_that_starts_with_make_is_not_repeated(self) -> None:
        exif = make_exif(make="Canon", model="Canon EOS R5")
        self.assertEqual(read_info(self.jpeg(exif=exif)).camera, "Canon EOS R5")

    def test_make_prefix_check_ignores_case(self) -> None:
        exif = make_exif(make="NIKON CORPORATION", model="Nikon Corporation D850")
        self.assertEqual(read_info(self.jpeg(exif=exif)).camera, "Nikon Corporation D850")

    def test_model_alone_is_the_camera(self) -> None:
        exif = make_exif(model="Pixel 8")
        self.assertEqual(read_info(self.jpeg(exif=exif)).camera, "Pixel 8")

    def test_make_with_padding_is_stripped(self) -> None:
        exif = make_exif(make="SONY  ", model="ILCE-7M4 ")
        self.assertEqual(read_info(self.jpeg(exif=exif)).camera, "SONY ILCE-7M4")

    def test_gps_south_and_west_are_negative_with_six_decimals(self) -> None:
        exif = make_exif(
            gps=("S", (33.0, 51.0, 54.0), "W", (151.0, 12.0, 30.0)),
        )
        info = read_info(self.jpeg(exif=exif))
        self.assertIsNotNone(info.gps)
        lat, lon = info.gps
        self.assertAlmostEqual(lat, -33.865, places=6)
        self.assertAlmostEqual(lon, -151.2083333, places=6)
        self.assertEqual(rows_of(info)["gps"], "-33.865000, -151.208333")

    def test_gps_north_and_east_are_positive(self) -> None:
        exif = make_exif(gps=("N", (41.0, 18.0, 39.9), "E", (69.0, 14.0, 26.0)))
        info = read_info(self.jpeg(exif=exif))
        self.assertAlmostEqual(info.gps[0], 41.3111, places=4)
        self.assertAlmostEqual(info.gps[1], 69.240556, places=5)
        lat_text, lon_text = rows_of(info)["gps"].split(", ")
        self.assertEqual(lat_text, "41.311083")
        self.assertEqual(len(lat_text.split(".")[1]), 6)
        self.assertEqual(len(lon_text.split(".")[1]), 6)

    def test_missing_gps_gives_no_row(self) -> None:
        info = read_info(self.jpeg(exif=make_exif(make="Canon", model="R5")))
        self.assertIsNone(info.gps)
        self.assertNotIn("gps", rows_of(info))

    def test_malformed_gps_does_not_hide_the_rest(self) -> None:
        exif = make_exif(make="Canon", model="Canon R5", software="1.0")
        ifd = exif.get_ifd(0x8825)
        ifd[1] = "N"
        ifd[2] = (10.0, 5.0, 2.0)
        info = read_info(self.jpeg(exif=exif))
        self.assertIsNone(info.gps)
        self.assertEqual(info.camera, "Canon R5")
        self.assertEqual(info.software, "1.0")

    def test_taken_falls_back_to_the_modify_date(self) -> None:
        exif = Image.Exif()
        exif[0x0132] = "2020:01:02 03:04:05"
        info = read_info(self.jpeg(exif=exif))
        self.assertEqual(info.taken, "2020:01:02 03:04:05")

    def test_original_date_wins_over_modify_date(self) -> None:
        exif = make_exif(taken="2019:05:06 07:08:09")
        exif[0x0132] = "2020:01:02 03:04:05"
        self.assertEqual(read_info(self.jpeg(exif=exif)).taken, "2019:05:06 07:08:09")


class OrientationTests(TempCase):
    def test_orientation_6_swaps_the_upright_size(self) -> None:
        info = read_info(self.jpeg(size=(40, 20), exif=make_exif(orientation=6)))
        self.assertEqual(info.orientation, 6)
        self.assertEqual((info.width, info.height), (40, 20))
        self.assertEqual(info.upright_size, (20, 40))
        self.assertEqual(rows_of(info)["size"], "40x20 (upright 20x40, orientation 6)")

    def test_orientation_8_also_swaps(self) -> None:
        info = read_info(self.jpeg(size=(40, 20), exif=make_exif(orientation=8)))
        self.assertEqual(info.upright_size, (20, 40))
        self.assertEqual(rows_of(info)["size"], "40x20 (upright 20x40, orientation 8)")

    def test_orientation_3_keeps_the_size_and_notes_it(self) -> None:
        info = read_info(self.jpeg(size=(40, 20), exif=make_exif(orientation=3)))
        self.assertEqual(info.orientation, 3)
        self.assertEqual(info.upright_size, (40, 20))
        self.assertEqual(rows_of(info)["size"], "40x20 (orientation 3)")

    def test_orientation_1_is_no_orientation(self) -> None:
        info = read_info(self.jpeg(exif=make_exif(orientation=1, make="Canon", model="X")))
        self.assertIsNone(info.orientation)
        self.assertEqual(rows_of(info)["size"], "40x20")

    def test_orientation_0_is_no_orientation(self) -> None:
        info = read_info(self.jpeg(exif=make_exif(orientation=0, make="Canon", model="X")))
        self.assertIsNone(info.orientation)
        self.assertEqual(rows_of(info)["size"], "40x20")

    def test_dict_carries_the_upright_size(self) -> None:
        document = info_as_dict(read_info(self.jpeg(size=(40, 20), exif=make_exif(orientation=6))))
        self.assertEqual(document["orientation"], 6)
        self.assertEqual((document["width"], document["height"]), (40, 20))
        self.assertEqual((document["upright_width"], document["upright_height"]), (20, 40))

    def test_heic_is_reported_upright_without_an_orientation_note(self) -> None:
        path = self.root / "turned.heic"
        image = Image.new("RGB", (64, 48))
        image.save(path, "HEIF", exif=make_exif(orientation=6).tobytes())
        info = read_info(path)
        self.assertIsNone(info.orientation)
        self.assertNotIn("orientation", rows_of(info)["size"])


class ColourProfileAndDpiTests(TempCase):
    def test_icc_profile_description(self) -> None:
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        description = ImageCms.getProfileDescription(profile).strip()
        self.assertTrue(description)
        info = read_info(self.jpeg(icc_profile=profile.tobytes()))
        self.assertEqual(info.color_profile, description)
        self.assertEqual(rows_of(info)["color"], f'ICC profile "{description}"')
        self.assertIn("ICC", info.metadata)

    def test_unreadable_icc_profile_uses_plain_text(self) -> None:
        info = read_info(self.jpeg(icc_profile=b"not a real profile" * 20))
        self.assertEqual(info.color_profile, "ICC profile")
        self.assertEqual(rows_of(info)["color"], "ICC profile")
        self.assertIn("ICC", info.metadata)

    def test_no_profile_means_no_color_row(self) -> None:
        self.assertNotIn("color", rows_of(read_info(self.jpeg())))

    def test_equal_dpi_is_one_number(self) -> None:
        info = read_info(self.jpeg(dpi=(72, 72)))
        self.assertEqual(info.dpi, (72, 72))
        self.assertEqual(rows_of(info)["dpi"], "72")

    def test_unequal_dpi_shows_both(self) -> None:
        info = read_info(self.jpeg(dpi=(72, 96)))
        self.assertEqual(info.dpi, (72, 96))
        self.assertEqual(rows_of(info)["dpi"], "72x96")

    def test_missing_dpi_gives_no_row(self) -> None:
        info = read_info(self.png())
        self.assertIsNone(info.dpi)
        self.assertNotIn("dpi", rows_of(info))

    def test_jpeg_with_exif_but_no_resolution_has_no_dpi(self) -> None:
        info = read_info(self.jpeg(exif=make_exif(make="Canon", model="Canon R5")))
        self.assertIsNone(info.dpi)
        self.assertNotIn("dpi", rows_of(info))

    def test_tiff_without_resolution_tags_has_no_dpi(self) -> None:
        path = self.root / "plain.tif"
        Image.new("RGB", (8, 8)).save(path, "TIFF")
        self.assertIsNone(read_info(path).dpi)

    def test_tiff_with_resolution_tags_has_its_dpi(self) -> None:
        path = self.root / "print.tif"
        Image.new("RGB", (8, 8)).save(path, "TIFF", dpi=(300, 300))
        self.assertEqual(read_info(path).dpi, (300, 300))

    def test_dpi_rounding_in_rows(self) -> None:
        self.assertEqual(rows_of(blank_info(dpi=(300.0, 300.0)))["dpi"], "300")
        self.assertEqual(rows_of(blank_info(dpi=(299.9994, 299.9994)))["dpi"], "300")
        self.assertEqual(rows_of(blank_info(dpi=(72.04, 72.04)))["dpi"], "72")
        self.assertEqual(rows_of(blank_info(dpi=(300.0, 150.5)))["dpi"], "300x150.5")
        self.assertEqual(rows_of(blank_info(dpi=(72.26, 72.26)))["dpi"], "72.3")


class MetadataBlockTests(TempCase):
    def test_xmp(self) -> None:
        info = read_info(self.jpeg(xmp=XMP_PACKET))
        self.assertEqual(tuple(info.metadata), ("XMP",))
        self.assertEqual(rows_of(info)["metadata"], "XMP")

    def test_jpeg_comment(self) -> None:
        info = read_info(self.jpeg(comment=b"shot on a tuesday"))
        self.assertEqual(tuple(info.metadata), ("comment",))
        self.assertEqual(rows_of(info)["metadata"], "comment")

    def test_png_text_chunk(self) -> None:
        text = PngInfo()
        text.add_text("Author", "someone")
        info = read_info(self.png(pnginfo=text))
        self.assertEqual(tuple(info.metadata), ("text",))
        self.assertEqual(rows_of(info)["metadata"], "text")

    def test_blocks_come_in_the_stated_order(self) -> None:
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        info = read_info(
            self.jpeg(
                comment=b"hello",
                icc_profile=profile,
                xmp=XMP_PACKET,
                exif=make_exif(make="Canon", model="Canon R5"),
            )
        )
        self.assertEqual(tuple(info.metadata), ("EXIF", "XMP", "ICC", "comment"))
        self.assertEqual(rows_of(info)["metadata"], "EXIF, XMP, ICC, comment")

    def test_no_metadata_leaves_the_row_out(self) -> None:
        info = read_info(self.jpeg())
        self.assertNotIn("metadata", rows_of(info))

    def test_exif_orientation_alone_counts_as_exif(self) -> None:
        info = read_info(self.jpeg(exif=make_exif(orientation=6)))
        self.assertIn("EXIF", info.metadata)

    def test_each_block_is_named_once(self) -> None:
        text = PngInfo()
        text.add_text("A", "1")
        text.add_text("B", "2")
        info = read_info(self.png(pnginfo=text))
        self.assertEqual(tuple(info.metadata).count("text"), 1)


class ModeAndTransparencyTests(TempCase):
    def mode_text(self, info: ImageInfo) -> str:
        return rows_of(info)["mode"]

    def test_rgba_has_transparency(self) -> None:
        info = read_info(self.png(mode="RGBA"))
        self.assertEqual(info.mode, "RGBA")
        self.assertTrue(info.transparency)
        self.assertEqual(self.mode_text(info), "RGBA, 8 bits per channel")
        self.assertEqual(rows_of(info)["transparency"], "yes")

    def test_palette_with_transparent_colour(self) -> None:
        info = read_info(self.png(mode="P", transparency=0))
        self.assertEqual(info.mode, "P")
        self.assertTrue(info.transparency)
        self.assertEqual(self.mode_text(info), "palette, 8 bits")
        self.assertEqual(rows_of(info)["transparency"], "yes")

    def test_palette_without_transparency(self) -> None:
        info = read_info(self.png(mode="P"))
        self.assertFalse(info.transparency)
        self.assertNotIn("transparency", rows_of(info))

    def test_opaque_rgb_has_no_transparency_row(self) -> None:
        info = read_info(self.png())
        self.assertFalse(info.transparency)
        self.assertNotIn("transparency", rows_of(info))

    def test_one_bit(self) -> None:
        info = read_info(self.png(mode="1"))
        self.assertEqual(info.mode, "1")
        self.assertEqual(self.mode_text(info), "black and white, 1 bit")

    def test_grey(self) -> None:
        self.assertEqual(self.mode_text(read_info(self.png(mode="L"))), "grey, 8 bits")

    def test_grey_with_alpha(self) -> None:
        info = read_info(self.png(mode="LA"))
        self.assertEqual(self.mode_text(info), "grey with alpha, 8 bits per channel")
        self.assertTrue(info.transparency)

    def test_sixteen_bit_grey(self) -> None:
        info = read_info(self.png(mode="I;16"))
        self.assertTrue(info.mode.startswith("I;16"))
        self.assertEqual(self.mode_text(info), "grey, 16 bits")

    def test_cmyk(self) -> None:
        path = self.root / "print.jpg"
        Image.new("CMYK", (40, 20)).save(path, "JPEG")
        info = read_info(path)
        self.assertEqual(info.mode, "CMYK")
        self.assertEqual(self.mode_text(info), "CMYK, 8 bits per channel")

    def test_mode_texts_for_the_remaining_modes(self) -> None:
        cases = {
            "I": "grey, 32-bit integer",
            "F": "grey, 32-bit float",
            "I;16L": "grey, 16 bits",
            "I;16B": "grey, 16 bits",
            "YCbCr": "YCbCr",
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                self.assertEqual(rows_of(blank_info(mode=mode))["mode"], expected)


class AnimationTests(TempCase):
    def gif(self, name: str, **save: object) -> Path:
        path = self.root / name
        frames = [Image.new("P", (8, 8), index * 40) for index in range(3)]
        for index, frame in enumerate(frames):
            frame.putpalette([c for i in range(256) for c in (i, (i * 7 + index * 60) % 256, 255 - i)])
        frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], **save)
        return path

    def test_gif_that_loops_forever(self) -> None:
        info = read_info(self.gif("loop.gif", duration=[100, 200, 300], loop=0))
        self.assertEqual(info.frames, 3)
        self.assertEqual(info.loop, 0)
        self.assertEqual(info.duration_ms, 600)
        self.assertEqual(rows_of(info)["frames"], "3 (loops forever, 0.6 s)")

    def test_gif_that_plays_once(self) -> None:
        info = read_info(self.gif("once.gif", duration=[500, 500, 1000]))
        self.assertEqual(info.frames, 3)
        self.assertIsNone(info.loop)
        self.assertEqual(info.duration_ms, 2000)
        self.assertEqual(rows_of(info)["frames"], "3 (plays once, 2.0 s)")

    def test_gif_that_loops_a_number_of_times(self) -> None:
        info = read_info(self.gif("three.gif", duration=100, loop=3))
        self.assertEqual(info.loop, 3)
        self.assertEqual(info.duration_ms, 300)
        self.assertEqual(rows_of(info)["frames"], "3 (loops 3 times, 0.3 s)")

    def test_still_image_has_no_loop_or_duration(self) -> None:
        info = read_info(self.png())
        self.assertEqual(info.frames, 1)
        self.assertIsNone(info.loop)
        self.assertIsNone(info.duration_ms)

    def test_frames_text_from_values(self) -> None:
        forever = blank_info(frames=24, loop=0, duration_ms=2400)
        once = blank_info(frames=24, loop=None, duration_ms=2400)
        times = blank_info(frames=24, loop=3, duration_ms=2400)
        self.assertEqual(rows_of(forever)["frames"], "24 (loops forever, 2.4 s)")
        self.assertEqual(rows_of(once)["frames"], "24 (plays once, 2.4 s)")
        self.assertEqual(rows_of(times)["frames"], "24 (loops 3 times, 2.4 s)")

    def test_animated_file_reports_the_first_frame_mode_and_size(self) -> None:
        info = read_info(self.gif("shape.gif", duration=100, loop=0))
        self.assertEqual((info.width, info.height), (8, 8))
        self.assertEqual(info.format, "GIF")


class RowsTests(unittest.TestCase):
    def test_full_row_order_and_labels(self) -> None:
        info = blank_info(
            path=Path("photo.jpg"), format="JPEG", width=4032, height=3024, orientation=6,
            file_size=3_100_000, color_profile="Display P3", dpi=(72.0, 72.0),
            camera="Apple iPhone 15", taken="2026:10:02 14:31:05", software="17.6",
            gps=(41.311081, 69.240562), metadata=("EXIF", "XMP", "ICC"), transparency=True,
        )
        self.assertEqual(
            info_rows(info),
            [
                ("format", "JPEG"),
                ("size", "4032x3024 (upright 3024x4032, orientation 6)"),
                ("mode", "RGB, 8 bits per channel"),
                ("file size", "3.1 MB"),
                ("frames", "1"),
                ("transparency", "yes"),
                ("color", 'ICC profile "Display P3"'),
                ("dpi", "72"),
                ("camera", "Apple iPhone 15"),
                ("taken", "2026:10:02 14:31:05"),
                ("software", "17.6"),
                ("gps", "41.311081, 69.240562"),
                ("metadata", "EXIF, XMP, ICC"),
            ],
        )

    def test_no_gps_leaves_the_row_out(self) -> None:
        info = blank_info(gps=(41.311081, 69.240562))
        labels = [label for label, _ in info_rows(info, gps=False)]
        self.assertNotIn("gps", labels)
        self.assertIn("gps", [label for label, _ in info_rows(info)])

    def test_color_row_without_a_description(self) -> None:
        self.assertEqual(rows_of(blank_info(color_profile="ICC profile"))["color"], "ICC profile")

    def test_rows_are_label_value_string_pairs(self) -> None:
        for label, value in info_rows(blank_info(gps=(1.0, 2.0), dpi=(72.0, 72.0))):
            self.assertIsInstance(label, str)
            self.assertIsInstance(value, str)

    def test_upright_size_property(self) -> None:
        for orientation, expected in ((None, (40, 20)), (2, (40, 20)), (3, (40, 20)),
                                      (4, (40, 20)), (5, (20, 40)), (6, (20, 40)),
                                      (7, (20, 40)), (8, (20, 40))):
            with self.subTest(orientation=orientation):
                self.assertEqual(blank_info(orientation=orientation).upright_size, expected)

    def test_info_is_immutable(self) -> None:
        with self.assertRaises(Exception):
            blank_info().width = 5  # type: ignore[misc]


class DictTests(TempCase):
    def test_key_order(self) -> None:
        document = info_as_dict(read_info(self.jpeg()))
        self.assertEqual(list(document), DICT_KEYS)

    def test_values_for_a_rich_photo(self) -> None:
        exif = make_exif(
            make="Apple", model="iPhone 15", taken="2026:10:02 14:31:05", software="17.6",
            orientation=6, gps=("S", (33.0, 51.0, 54.0), "W", (151.0, 12.0, 30.0)),
        )
        path = self.jpeg(size=(40, 20), exif=exif, dpi=(72, 96))
        document = info_as_dict(read_info(path))
        self.assertEqual(str(document["file"]), str(path))
        self.assertEqual(document["format"], "JPEG")
        self.assertEqual((document["width"], document["height"]), (40, 20))
        self.assertEqual(document["orientation"], 6)
        self.assertEqual((document["upright_width"], document["upright_height"]), (20, 40))
        self.assertEqual(document["mode"], "RGB")
        self.assertEqual(document["file_size"], os.path.getsize(path))
        self.assertEqual(document["frames"], 1)
        self.assertIsNone(document["loop"])
        self.assertIsNone(document["duration_ms"])
        self.assertIs(document["transparency"], False)
        self.assertIsNone(document["color_profile"])
        self.assertEqual(document["dpi"], [72, 96])
        self.assertEqual(document["camera"], "Apple iPhone 15")
        self.assertEqual(document["taken"], "2026:10:02 14:31:05")
        self.assertEqual(document["software"], "17.6")
        gps = document["gps"]
        self.assertEqual(list(gps), ["latitude", "longitude"])
        self.assertAlmostEqual(gps["latitude"], -33.865, places=6)
        self.assertAlmostEqual(gps["longitude"], -151.2083333, places=6)
        self.assertEqual(document["metadata"], ["EXIF"])

    def test_missing_values_are_none(self) -> None:
        document = info_as_dict(read_info(self.png()))
        for key in ("orientation", "loop", "duration_ms", "color_profile", "dpi",
                    "camera", "taken", "software", "gps"):
            with self.subTest(key=key):
                self.assertIsNone(document[key])
        self.assertEqual(document["metadata"], [])

    def test_no_gps_makes_it_null(self) -> None:
        exif = make_exif(gps=("N", (41.0, 18.0, 40.0), "E", (69.0, 14.0, 26.0)))
        info = read_info(self.jpeg(exif=exif))
        self.assertIsNotNone(info_as_dict(info)["gps"])
        self.assertIsNone(info_as_dict(info, gps=False)["gps"])

    def test_animation_values(self) -> None:
        path = self.root / "a.gif"
        frames = [Image.new("RGB", (4, 4), (i * 80, 0, 0)) for i in range(3)]
        frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=[100, 100, 200], loop=0)
        document = info_as_dict(read_info(path))
        self.assertEqual(document["frames"], 3)
        self.assertEqual(document["loop"], 0)
        self.assertEqual(document["duration_ms"], 400)

    def test_is_json_serialisable_and_keeps_order(self) -> None:
        document = info_as_dict(read_info(self.jpeg(dpi=(72, 72))))
        text = json.dumps(document, ensure_ascii=False)
        self.assertEqual(list(json.loads(text)), DICT_KEYS)

    def test_transparency_is_a_boolean(self) -> None:
        document = info_as_dict(read_info(self.png(mode="RGBA")))
        self.assertIs(document["transparency"], True)


class ModuleShapeTests(unittest.TestCase):
    def test_module_does_not_import_click(self) -> None:
        source = Path(image_info.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import click", source)
        self.assertNotIn("from click", source)


if __name__ == "__main__":
    unittest.main()

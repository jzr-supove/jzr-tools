"""Tests for removing metadata from JPEG, PNG and WebP files without re-encoding.

Every file is assembled in the test from raw segments and chunks around image
data that Pillow encodes (see strip_fixtures), so each test states which bytes
go in and which bytes the cleaned copy must hold. Pictures are compared by
decoding both files with Pillow.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from jzr_tools.core import image_strip as strip
from jzr_tools.core.media import format_size
from tests import strip_fixtures as fx

DAMAGED = "damaged or unsupported container; nothing written"
UNREADABLE = "some metadata could not be read safely; nothing written"
ANIMATED = "animated images are not supported"
HEIC_TEXT = (
    ".heic metadata cannot be removed without re-encoding; "
    "use jzr image compress --strip (or convert)"
)


def refusal(data: bytes, **options) -> str:  # noqa: ANN003
    try:
        strip.strip_bytes(data, **options)
    except strip.StripError as error:
        return str(error)
    raise AssertionError("strip_bytes accepted the file")


class StripCase(unittest.TestCase):
    def assert_same_picture(self, before: bytes, after: bytes) -> None:
        self.assertEqual(fx.pixels(after), fx.pixels(before))


# --- JPEG -----------------------------------------------------------------------------


class JpegTests(StripCase):
    def setUp(self) -> None:
        self.source = fx.plain_jpeg()
        self.body = fx.jpeg_body(self.source)

    def jpeg(self, *segments: bytes, trailer: bytes = b"") -> bytes:
        return fx.build_jpeg(list(segments), self.body, trailer)

    def everything(self) -> bytes:
        return self.jpeg(
            fx.JFIF_DIRTY,
            fx.exif_app1(fx.photo_tiff(6)),
            fx.xmp_app1(),
            fx.icc_app2(),
            fx.COMMENT,
            fx.IPTC,
            fx.UNKNOWN_APP5,
            fx.UNKNOWN_APP1,
            trailer=fx.MOTION_VIDEO,
        )

    def test_a_loaded_photo_comes_out_with_only_the_rendering_parts(self) -> None:
        data = self.everything()
        result = strip.strip_bytes(data)
        orientation = fx.exif_app1(fx.orientation_only_tiff(6))
        self.assertEqual(
            result.data, self.jpeg(fx.JFIF_CLEAN, orientation, fx.icc_app2())
        )
        self.assertEqual(result.kind, "jpeg")
        self.assertTrue(result.changed)
        self.assertEqual(
            result.removed,
            (
                "EXIF",
                "GPS",
                "XMP",
                "IPTC",
                "comments",
                "thumbnail",
                "print density",
                "other blocks",
                "video after the image",
            ),
        )
        self.assertCountEqual(result.kept, ("orientation", "colour profile"))

    def test_the_picture_is_the_same_after_cleaning(self) -> None:
        data = self.everything()
        self.assert_same_picture(data, strip.strip_bytes(data).data)
        self.assert_same_picture(self.source, strip.strip_bytes(data).data)

    def test_pillow_reads_the_orientation_and_nothing_else_from_the_copy(self) -> None:
        out = strip.strip_bytes(self.everything()).data
        with Image.open(io.BytesIO(out)) as image:
            exif = image.getexif()
            self.assertEqual(dict(exif), {0x0112: 6})
            self.assertEqual(exif.get_ifd(0x8825), {})
            self.assertNotIn("xmp", image.info)
            self.assertNotIn("comment", image.info)
            self.assertEqual(image.info.get("icc_profile"), fx.icc_profile())

    def test_the_scan_data_and_every_other_segment_are_copied_unchanged(self) -> None:
        out = strip.strip_bytes(self.everything()).data
        self.assertTrue(out.startswith(fx.SOI + fx.JFIF_CLEAN))
        self.assertTrue(out.endswith(self.body))
        self.assertTrue(out.endswith(fx.EOI))

    def test_a_progressive_file_keeps_all_of_its_scans(self) -> None:
        progressive = fx.plain_jpeg(progressive=True)
        body = fx.jpeg_body(progressive)
        self.assertGreater(body.count(b"\xff\xda"), 1)
        data = fx.build_jpeg([fx.JFIF_CLEAN, fx.COMMENT, fx.exif_app1(fx.photo_tiff(None))], body)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, fx.build_jpeg([fx.JFIF_CLEAN], body))
        self.assertEqual(result.removed, ("EXIF", "GPS", "comments"))
        self.assert_same_picture(progressive, result.data)

    def test_each_kind_of_block_is_named_on_its_own(self) -> None:
        cases = {
            "exif with gps": (fx.exif_app1(fx.photo_tiff(None)), ("EXIF", "GPS")),
            "exif without gps": (
                fx.exif_app1(fx.photo_tiff(None, gps=False)),
                ("EXIF",),
            ),
            "xmp": (fx.xmp_app1(), ("XMP",)),
            "comment": (fx.COMMENT, ("comments",)),
            "iptc": (fx.IPTC, ("IPTC",)),
            "unknown app5": (fx.UNKNOWN_APP5, ("other blocks",)),
            "app1 that is neither exif nor xmp": (fx.UNKNOWN_APP1, ("other blocks",)),
            "app2 that is not a profile": (
                fx.segment(0xE2, b"MPF\0" + b"\x4d\x4d\x00\x2a" * 4),
                ("other blocks",),
            ),
            "adobe block of the wrong length": (fx.LONG_ADOBE_APP14, ("other blocks",)),
        }
        for name, (block, expected) in cases.items():
            with self.subTest(name):
                result = strip.strip_bytes(self.jpeg(fx.JFIF_CLEAN, block))
                self.assertEqual(result.removed, expected)
                self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))
                self.assertEqual(result.kept, ())
                self.assertTrue(result.changed)

    def test_the_jfif_header_loses_its_thumbnail_and_density(self) -> None:
        cases = {
            "thumbnail only": (fx.jfif(thumbnail=(2, 2)), ("thumbnail",)),
            "unit and density": (fx.jfif(units=1, density=(72, 72)), ("print density",)),
            "density without unit": (fx.jfif(units=0, density=(72, 72)), ("print density",)),
            "unit with a 1 x 1 density": (fx.jfif(units=2), ("print density",)),
            "both": (fx.JFIF_DIRTY, ("thumbnail", "print density")),
        }
        for name, (header, expected) in cases.items():
            with self.subTest(name):
                result = strip.strip_bytes(self.jpeg(header))
                self.assertEqual(result.removed, expected)
                self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))

    def test_the_jfif_version_is_not_touched(self) -> None:
        old = fx.segment(0xE0, b"JFIF\0\x01\x01\x01\x00\x48\x00\x48\x00\x00")
        kept = fx.segment(0xE0, b"JFIF\0\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        result = strip.strip_bytes(self.jpeg(old))
        self.assertEqual(result.data, self.jpeg(kept))
        self.assertEqual(result.removed, ("print density",))

    def test_a_12_byte_adobe_block_stays(self) -> None:
        data = self.jpeg(fx.ADOBE_APP14)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.removed, ())
        self.assertFalse(result.changed)

    def test_two_colour_profile_segments_both_stay(self) -> None:
        first = fx.segment(0xE2, b"ICC_PROFILE\0\x01\x02" + fx.icc_profile()[:200])
        second = fx.segment(0xE2, b"ICC_PROFILE\0\x02\x02" + fx.icc_profile()[200:])
        data = self.jpeg(fx.JFIF_CLEAN, first, second)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.kept, ("colour profile",))

    def test_the_colour_profile_can_be_removed_too(self) -> None:
        data = self.jpeg(fx.JFIF_DIRTY, fx.icc_app2(), fx.UNKNOWN_APP5)
        result = strip.strip_bytes(data, keep_color_profile=False)
        self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))
        self.assertEqual(
            result.removed, ("thumbnail", "print density", "colour profile", "other blocks")
        )
        self.assertEqual(result.kept, ())
        self.assert_same_picture(data, result.data)

    def test_orientation_is_kept_even_when_the_profile_goes(self) -> None:
        data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(8)), fx.icc_app2())
        result = strip.strip_bytes(data, keep_color_profile=False)
        self.assertEqual(
            result.data, self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(8)))
        )
        self.assertEqual(result.kept, ("orientation",))

    def test_every_orientation_but_upright_is_written_back(self) -> None:
        for value in range(2, 9):
            with self.subTest(orientation=value):
                data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(value)))
                result = strip.strip_bytes(data)
                self.assertEqual(
                    result.data,
                    self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(value))),
                )
                with Image.open(io.BytesIO(result.data)) as image:
                    self.assertEqual(image.getexif().get(0x0112), value)

    def test_orientation_one_is_not_written_back(self) -> None:
        data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(1)))
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))
        self.assertEqual(result.removed, ("EXIF", "GPS"))
        self.assertEqual(result.kept, ())

    def test_without_a_jfif_header_the_orientation_follows_the_start_of_image(self) -> None:
        data = self.jpeg(fx.exif_app1(fx.photo_tiff(6, gps=False)), fx.COMMENT)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, self.jpeg(fx.exif_app1(fx.orientation_only_tiff(6))))

    def test_an_orientation_stored_as_a_long_is_read(self) -> None:
        tiff = fx.tiff_block([fx.ascii_entry(0x010F, "AcmeCam"), fx.long_entry(0x0112, 3)])
        result = strip.strip_bytes(self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(tiff)))
        self.assertEqual(
            result.data, self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(3)))
        )

    def test_a_long_zero_orientation_means_upright_and_is_dropped(self) -> None:
        tiff = fx.tiff_block([fx.ascii_entry(0x010F, "AcmeCam"), fx.long_entry(0x0112, 0)])
        result = strip.strip_bytes(self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(tiff)))
        self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))
        self.assertEqual(result.removed, ("EXIF",))
        self.assertEqual(result.kept, ())

    def test_a_big_endian_exif_is_read_and_replaced_by_a_little_endian_block(self) -> None:
        data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(6, big=True)))
        result = strip.strip_bytes(data)
        self.assertEqual(
            result.data, self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6)))
        )
        self.assertEqual(result.removed, ("EXIF", "GPS"))

    def test_orientation_in_xmp_is_kept_in_both_spellings(self) -> None:
        elements = (
            b'<x:xmpmeta><rdf:Description><tiff:Orientation>6</tiff:Orientation>'
            b"</rdf:Description></x:xmpmeta>"
        )
        packets = {
            "double quotes": fx.xmp_packet("6"),
            "element": elements,
            "single quotes and spaces": b"<rdf:Description tiff:Orientation = '6' />",
        }
        for name, packet in packets.items():
            with self.subTest(name):
                data = self.jpeg(fx.JFIF_CLEAN, fx.xmp_app1(packet))
                result = strip.strip_bytes(data)
                self.assertEqual(
                    result.data,
                    self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6))),
                )
                self.assertEqual(result.removed, ("XMP",))
                self.assertEqual(result.kept, ("orientation",))

    def test_the_same_orientation_in_exif_and_xmp_is_written_once(self) -> None:
        data = self.jpeg(
            fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(6)), fx.xmp_app1(fx.xmp_packet("6"))
        )
        result = strip.strip_bytes(data)
        self.assertEqual(
            result.data, self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6)))
        )
        self.assertEqual(result.data.count(b"Exif\0\0"), 1)

    def test_two_different_orientations_refuse_the_file(self) -> None:
        cases = {
            "exif and xmp": [fx.exif_app1(fx.photo_tiff(6)), fx.xmp_app1(fx.xmp_packet("3"))],
            "upright exif and rotated xmp": [
                fx.exif_app1(fx.photo_tiff(1)),
                fx.xmp_app1(fx.xmp_packet("6")),
            ],
            "two exif blocks": [
                fx.exif_app1(fx.photo_tiff(6)),
                fx.exif_app1(fx.photo_tiff(8)),
            ],
        }
        for name, blocks in cases.items():
            with self.subTest(name):
                self.assertEqual(refusal(self.jpeg(fx.JFIF_CLEAN, *blocks)), DAMAGED)

    def test_exif_that_cannot_be_read_safely_refuses_the_file(self) -> None:
        good = fx.photo_tiff(6)
        broken = {
            "truncated": good[:30],
            "not a tiff": b"this is not a tiff block at all",
            "orientation out of range": fx.photo_tiff(9),
            "bad gps reference": fx.tiff_block(
                [fx.short_entry(0x0112, 6)],
                {
                    0x8825: [
                        fx.ascii_entry(1, "X"),
                        fx.rational_entry(2, [(41, 1), (30, 1), (0, 1)]),
                        fx.ascii_entry(3, "E"),
                        fx.rational_entry(4, [(69, 1), (15, 1), (0, 1)]),
                    ]
                },
            ),
        }
        for name, tiff in broken.items():
            with self.subTest(name):
                data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(tiff))
                self.assertEqual(refusal(data), UNREADABLE)

    def test_data_after_the_end_of_the_image_is_removed_and_named(self) -> None:
        cases = {
            "an mp4 right after": (fx.MOTION_VIDEO, "video after the image"),
            "an mp4 with padding first": (b"\0" * 100 + fx.MOTION_VIDEO, "video after the image"),
            "other bytes": (b"trailer bytes that are not a video", "data after the image"),
            "an mp4 marker past the first 4 KB": (
                b"\0" * 5000 + fx.MOTION_VIDEO,
                "data after the image",
            ),
        }
        for name, (trailer, label) in cases.items():
            with self.subTest(name):
                result = strip.strip_bytes(self.jpeg(fx.JFIF_CLEAN, trailer=trailer))
                self.assertEqual(result.removed, (label,))
                self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))
                self.assertTrue(result.changed)

    def test_a_file_with_nothing_to_remove_comes_back_unchanged(self) -> None:
        data = self.jpeg(fx.JFIF_CLEAN)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.removed, ())
        self.assertEqual(result.kept, ())
        self.assertFalse(result.changed)

    def test_a_file_that_holds_only_the_minimal_orientation_block_is_unchanged(self) -> None:
        data = self.jpeg(fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6)))
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.removed, ())
        self.assertEqual(result.kept, ("orientation",))
        self.assertFalse(result.changed)

    def test_cleaning_twice_changes_nothing_the_second_time(self) -> None:
        first = strip.strip_bytes(self.everything())
        second = strip.strip_bytes(first.data)
        self.assertEqual(second.data, first.data)
        self.assertFalse(second.changed)
        self.assertEqual(second.removed, ())
        self.assertCountEqual(second.kept, first.kept)

    def test_the_input_is_never_modified(self) -> None:
        data = self.everything()
        copy = bytes(data)
        strip.strip_bytes(data)
        self.assertEqual(data, copy)

    def test_a_plain_pillow_file_keeps_its_picture(self) -> None:
        result = strip.strip_bytes(self.source)
        self.assert_same_picture(self.source, result.data)

    def test_the_segment_limit_stops_a_crafted_file(self) -> None:
        many = fx.COMMENT * 10_001
        self.assertEqual(refusal(self.jpeg(fx.JFIF_CLEAN, many)), DAMAGED)

    def test_a_file_just_under_the_segment_limit_is_cleaned(self) -> None:
        many = fx.COMMENT * 9_000
        result = strip.strip_bytes(self.jpeg(fx.JFIF_CLEAN, many))
        self.assertEqual(result.removed, ("comments",))
        self.assertEqual(result.data, self.jpeg(fx.JFIF_CLEAN))

    def test_damaged_files_are_refused(self) -> None:
        parts, scan = fx.jpeg_parts(self.source)
        tables_and_frame = b"".join(
            raw for marker, raw in parts if not (0xE0 <= marker <= 0xEF or marker == 0xFE)
        )
        without_frame = b"".join(
            raw
            for marker, raw in parts
            if marker not in (0xC0, 0xC1, 0xC2) and not 0xE0 <= marker <= 0xEF
        )
        whole = self.jpeg(fx.JFIF_CLEAN)
        cases = {
            "cut in the middle of the scan": whole[: len(whole) // 2],
            "cut right after the header": whole[:30],
            "no end of image": whole[:-2],
            "no scan": fx.SOI + fx.JFIF_CLEAN + tables_and_frame + fx.EOI,
            "no frame": fx.SOI + fx.JFIF_CLEAN + without_frame + scan,
            "segment longer than the file": fx.SOI + b"\xff\xe1\xff\xff" + b"short",
            "segment length under 2": fx.SOI + b"\xff\xe1\x00\x01" + self.body,
            "no marker where one must be": fx.SOI + b"\x00\x00" + self.body,
            "a second start of image": fx.SOI + fx.SOI + self.body,
            "a restart marker before the scan": fx.SOI + b"\xff\xd0" + self.body,
            "only the start of image": fx.SOI,
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(refusal(data), DAMAGED)


# --- PNG -----------------------------------------------------------------------------------


class PngTests(StripCase):
    def setUp(self) -> None:
        self.source = fx.plain_png()
        self.ihdr, self.idat, self.iend = fx.png_header_and_data(self.source)

    def png(self, *middle: bytes, before_data: tuple[bytes, ...] = (), trailer: bytes = b"") -> bytes:
        return fx.build_png(self.ihdr, *before_data, *middle, self.idat, self.iend) + trailer

    def clean_png(self, *kept: bytes) -> bytes:
        return fx.build_png(self.ihdr, *kept, self.idat, self.iend)

    GAMMA = fx.png_chunk(b"gAMA", struct.pack(">I", 45455))
    CHROMA = fx.png_chunk(b"cHRM", struct.pack(">8I", *range(31000, 31008)))

    def test_a_loaded_png_keeps_rendering_chunks_and_the_profile_only(self) -> None:
        exif = fx.png_chunk(b"eXIf", fx.photo_tiff(6))
        data = fx.build_png(
            self.ihdr,
            self.GAMMA,
            self.CHROMA,
            fx.iccp_chunk(),
            fx.png_chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
            fx.text_chunk(),
            fx.ztxt_chunk(),
            fx.itxt_chunk("Comment", "ünï".encode()),
            fx.itxt_chunk("XML:com.adobe.xmp", fx.xmp_packet()),
            fx.png_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 10, 2, 14, 31, 5)),
            exif,
            fx.png_chunk(b"abCD", b"private"),
            self.idat,
            self.iend,
            fx.MOTION_VIDEO,
        )
        result = strip.strip_bytes(data)
        self.assertEqual(
            result.data,
            self.clean_png(
                fx.png_chunk(b"eXIf", fx.orientation_only_tiff(6)),
                self.GAMMA,
                self.CHROMA,
                fx.iccp_chunk(),
            ),
        )
        self.assertEqual(result.kind, "png")
        self.assertEqual(
            result.removed,
            ("EXIF", "GPS", "XMP", "text", "timestamp", "other blocks", "video after the image"),
        )
        self.assertCountEqual(result.kept, ("orientation", "colour profile"))
        self.assertTrue(result.changed)
        self.assert_same_picture(self.source, result.data)

    def test_pillow_reads_the_orientation_from_the_copy(self) -> None:
        data = self.png(fx.png_chunk(b"eXIf", fx.photo_tiff(6)), fx.text_chunk())
        out = strip.strip_bytes(data).data
        with Image.open(io.BytesIO(out)) as image:
            image.load()
            self.assertEqual(dict(image.getexif()), {0x0112: 6})
            self.assertNotIn("Comment", image.info)

    def test_each_kind_of_chunk_is_named_on_its_own(self) -> None:
        cases = {
            "tEXt": (fx.text_chunk(), ("text",)),
            "zTXt": (fx.ztxt_chunk(), ("text",)),
            "iTXt text": (fx.itxt_chunk("Description", b"hello"), ("text",)),
            "iTXt xmp": (fx.itxt_chunk("XML:com.adobe.xmp", fx.xmp_packet()), ("XMP",)),
            "tIME": (fx.png_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 1, 2, 3, 4, 5)), ("timestamp",)),
            "pHYs": (fx.png_chunk(b"pHYs", struct.pack(">IIB", 3780, 3780, 1)), ("other blocks",)),
            "unknown ancillary": (fx.png_chunk(b"abCD", b"xyz"), ("other blocks",)),
            "eXIf with gps": (fx.png_chunk(b"eXIf", fx.photo_tiff(None)), ("EXIF", "GPS")),
            "eXIf without gps": (
                fx.png_chunk(b"eXIf", fx.photo_tiff(None, gps=False)),
                ("EXIF",),
            ),
            "eXIf with the exif prefix": (
                fx.png_chunk(b"eXIf", b"Exif\0\0" + fx.photo_tiff(None, gps=False)),
                ("EXIF",),
            ),
        }
        for name, (chunk, expected) in cases.items():
            with self.subTest(name):
                result = strip.strip_bytes(self.png(chunk))
                self.assertEqual(result.removed, expected)
                self.assertEqual(result.data, self.clean_png())
                self.assertEqual(result.kept, ())

    def test_the_rendering_chunks_stay_byte_for_byte(self) -> None:
        kept = (
            fx.png_chunk(b"sRGB", b"\x00"),
            self.GAMMA,
            self.CHROMA,
            fx.png_chunk(b"cICP", bytes([9, 16, 0, 1])),
            fx.png_chunk(b"mDCV", bytes(range(24))),
            fx.png_chunk(b"cLLI", bytes(range(8))),
            fx.png_chunk(b"sBIT", bytes([8, 8, 8])),
            fx.png_chunk(b"bKGD", struct.pack(">HHH", 1, 2, 3)),
        )
        data = self.png(*kept)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.removed, ())
        self.assertFalse(result.changed)

    def test_a_palette_png_keeps_its_palette_and_transparency(self) -> None:
        image = Image.new("P", (16, 16))
        image.putpalette([c for i in range(256) for c in (i, 255 - i, i // 2)])
        for x in range(16):
            for y in range(16):
                image.putpixel((x, y), (x * 16 + y) % 256)
        buffer = io.BytesIO()
        image.save(buffer, "PNG", transparency=3)
        original = buffer.getvalue()
        kinds = [kind for kind, _ in fx.png_chunks(original)]
        self.assertIn(b"PLTE", kinds)
        self.assertIn(b"tRNS", kinds)
        data = original + b"after the end"
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, original)
        self.assertEqual(result.removed, ("data after the image",))
        self.assert_same_picture(original, result.data)

    def test_an_rgba_png_keeps_its_picture(self) -> None:
        buffer = io.BytesIO()
        fx.noise(20, 20, 3, "RGBA").save(buffer, "PNG")
        original = buffer.getvalue()
        data = fx.build_png(
            *(fx.png_chunk(k, p) for k, p in fx.png_chunks(original)[:1]),
            fx.text_chunk(),
            *(fx.png_chunk(k, p) for k, p in fx.png_chunks(original)[1:]),
        )
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, original)
        self.assert_same_picture(original, result.data)

    def test_the_minimal_orientation_chunk_is_written_right_after_the_header(self) -> None:
        data = self.png(self.GAMMA, fx.png_chunk(b"eXIf", fx.photo_tiff(3)))
        result = strip.strip_bytes(data)
        chunks = [kind for kind, _ in fx.png_chunks(result.data)]
        self.assertEqual(chunks, [b"IHDR", b"eXIf", b"gAMA", b"IDAT", b"IEND"])
        self.assertEqual(dict(fx.png_chunks(result.data))[b"eXIf"], fx.orientation_only_tiff(3))

    def test_an_orientation_chunk_that_is_already_minimal_is_unchanged(self) -> None:
        data = self.png(fx.png_chunk(b"eXIf", fx.orientation_only_tiff(6)))
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.kept, ("orientation",))
        self.assertFalse(result.changed)

    def test_unreadable_exif_refuses_the_file(self) -> None:
        data = self.png(fx.png_chunk(b"eXIf", fx.photo_tiff(6)[:30]))
        self.assertEqual(refusal(data), UNREADABLE)

    def test_the_colour_profile_can_be_removed(self) -> None:
        data = self.png(self.GAMMA, fx.iccp_chunk())
        result = strip.strip_bytes(data, keep_color_profile=False)
        self.assertEqual(result.data, self.clean_png(self.GAMMA))
        self.assertEqual(result.removed, ("colour profile",))
        self.assertEqual(result.kept, ())

    def test_the_colour_profile_is_kept_by_default(self) -> None:
        data = self.png(fx.iccp_chunk())
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.kept, ("colour profile",))
        self.assertFalse(result.changed)

    def test_data_after_the_end_chunk_is_named_by_what_it_holds(self) -> None:
        video = strip.strip_bytes(self.png(trailer=fx.MOTION_VIDEO))
        self.assertEqual(video.removed, ("video after the image",))
        other = strip.strip_bytes(self.png(trailer=b"plain bytes"))
        self.assertEqual(other.removed, ("data after the image",))
        self.assertEqual(other.data, self.clean_png())

    def test_a_png_with_nothing_to_remove_comes_back_unchanged(self) -> None:
        result = strip.strip_bytes(self.source)
        self.assertEqual(result.data, self.source)
        self.assertEqual(result.removed, ())
        self.assertFalse(result.changed)

    def test_animated_pngs_are_refused_by_name(self) -> None:
        for kind, payload in (
            (b"acTL", struct.pack(">II", 2, 0)),
            (b"fcTL", bytes(26)),
            (b"fdAT", bytes(8)),
        ):
            with self.subTest(kind=kind):
                data = self.png(fx.png_chunk(kind, payload))
                self.assertEqual(refusal(data), ANIMATED)

    def test_a_real_animated_png_is_refused(self) -> None:
        frames = [fx.noise(16, 16, seed) for seed in (1, 2, 3)]
        buffer = io.BytesIO()
        frames[0].save(buffer, "PNG", save_all=True, append_images=frames[1:])
        self.assertEqual(refusal(buffer.getvalue()), ANIMATED)

    def test_damaged_files_are_refused(self) -> None:
        whole = self.png()
        corrupt = bytearray(whole)
        idat_at = whole.index(b"IDAT")
        corrupt[idat_at + 6] ^= 0xFF
        bad_depth = fx.png_chunk(b"IHDR", struct.pack(">IIBBBBB", 24, 16, 4, 2, 0, 0, 0))
        cases = {
            "a bad crc on the image data": bytes(corrupt),
            "a bad crc on a text chunk": self.png(
                fx.png_chunk(b"tEXt", b"a\0b", crc=12345)
            ),
            "an unknown critical chunk": self.png(fx.png_chunk(b"ABCD", b"critical")),
            "cut in the middle of the image data": whole[: len(whole) // 2],
            "no end chunk": whole[: -len(self.iend)],
            "no image data": fx.build_png(self.ihdr, self.iend),
            "the header is not first": fx.build_png(self.GAMMA, self.ihdr, self.idat, self.iend),
            "a header with an impossible depth": fx.build_png(bad_depth, self.idat, self.iend),
            "an end chunk with a payload": fx.build_png(
                self.ihdr, self.idat, fx.png_chunk(b"IEND", b"x")
            ),
            "only the signature": fx.PNG_SIGNATURE,
            "a damaged signature": b"\x89PNG\r\n\x1a\r" + whole[8:],
            "gAMA of the wrong length": self.png(fx.png_chunk(b"gAMA", b"\0\0\0")),
            "cHRM of the wrong length": self.png(fx.png_chunk(b"cHRM", bytes(31))),
            "sRGB of the wrong length": self.png(fx.png_chunk(b"sRGB", b"\0\0")),
            "cICP of the wrong length": self.png(fx.png_chunk(b"cICP", bytes(5))),
            "an empty palette": self.png(fx.png_chunk(b"PLTE", b"")),
            "a palette that is not whole colours": self.png(fx.png_chunk(b"PLTE", bytes(7))),
            "a palette over 256 colours": self.png(fx.png_chunk(b"PLTE", bytes(771))),
            "a bKGD of the wrong length": self.png(fx.png_chunk(b"bKGD", bytes(3))),
            "an sBIT of the wrong length": self.png(fx.png_chunk(b"sBIT", bytes(5))),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(refusal(data), DAMAGED)


# --- WebP ---------------------------------------------------------------------------------------


class WebpTests(StripCase):
    def setUp(self) -> None:
        self.image, self.simple = fx.lossless_webp_image()
        self.width, self.height = 40, 30

    def extended(self, flags: int, *chunks: bytes, trailer: bytes = b"") -> bytes:
        return fx.riff(fx.vp8x(flags, self.width, self.height), *chunks) + trailer

    def test_a_loaded_webp_keeps_the_picture_the_profile_and_the_orientation(self) -> None:
        data = self.extended(
            fx.VP8X_ICC | fx.VP8X_EXIF | fx.VP8X_XMP,
            fx.webp_chunk(b"ICCP", fx.icc_profile()),
            self.image,
            fx.webp_chunk(b"EXIF", fx.photo_tiff(6)),
            fx.webp_chunk(b"XMP ", fx.xmp_packet()),
            fx.webp_chunk(b"ABCD", b"odd"),
            trailer=fx.MOTION_VIDEO,
        )
        result = strip.strip_bytes(data)
        self.assertEqual(
            result.data,
            self.extended(
                fx.VP8X_ICC | fx.VP8X_EXIF,
                fx.webp_chunk(b"ICCP", fx.icc_profile()),
                self.image,
                fx.webp_chunk(b"EXIF", fx.orientation_only_tiff(6)),
            ),
        )
        self.assertEqual(result.kind, "webp")
        self.assertEqual(
            result.removed,
            ("EXIF", "GPS", "XMP", "other blocks", "video after the image"),
        )
        self.assertCountEqual(result.kept, ("orientation", "colour profile"))
        self.assertTrue(result.changed)
        self.assertEqual(struct.unpack("<I", result.data[4:8])[0], len(result.data) - 8)
        self.assert_same_picture(self.simple, result.data)

    def test_pillow_reads_the_orientation_from_the_copy(self) -> None:
        data = self.extended(
            fx.VP8X_EXIF, self.image, fx.webp_chunk(b"EXIF", fx.photo_tiff(6))
        )
        out = strip.strip_bytes(data).data
        with Image.open(io.BytesIO(out)) as image:
            image.load()
            self.assertEqual(dict(image.getexif()), {0x0112: 6})

    def test_the_flags_of_removed_blocks_are_cleared_and_the_rest_stay(self) -> None:
        data = self.extended(
            fx.VP8X_ALPHA | fx.VP8X_EXIF | fx.VP8X_XMP,
            self.image,
            fx.webp_chunk(b"EXIF", fx.photo_tiff(None)),
            fx.webp_chunk(b"XMP ", fx.xmp_packet()),
        )
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, self.extended(fx.VP8X_ALPHA, self.image))
        self.assertEqual(result.data[20], fx.VP8X_ALPHA)
        self.assertEqual(result.removed, ("EXIF", "GPS", "XMP"))
        self.assertEqual(struct.unpack("<I", result.data[4:8])[0], len(result.data) - 8)

    def test_each_kind_of_chunk_is_named_on_its_own(self) -> None:
        cases = {
            "exif with gps": (
                fx.VP8X_EXIF,
                fx.webp_chunk(b"EXIF", fx.photo_tiff(None)),
                ("EXIF", "GPS"),
            ),
            "exif with the exif prefix": (
                fx.VP8X_EXIF,
                fx.webp_chunk(b"EXIF", b"Exif\0\0" + fx.photo_tiff(None, gps=False)),
                ("EXIF",),
            ),
            "xmp": (fx.VP8X_XMP, fx.webp_chunk(b"XMP ", fx.xmp_packet()), ("XMP",)),
            "unknown chunk": (0, fx.webp_chunk(b"ABCD", b"four"), ("other blocks",)),
            "unknown chunk of odd length": (0, fx.webp_chunk(b"ABCD", b"odd"), ("other blocks",)),
        }
        for name, (flags, chunk, expected) in cases.items():
            with self.subTest(name):
                result = strip.strip_bytes(self.extended(flags, self.image, chunk))
                self.assertEqual(result.removed, expected)
                self.assertEqual(result.data, self.extended(0, self.image))
                self.assertEqual(result.kept, ())

    def test_the_colour_profile_can_be_removed_and_its_flag_cleared(self) -> None:
        data = self.extended(
            fx.VP8X_ICC, fx.webp_chunk(b"ICCP", fx.icc_profile()), self.image
        )
        result = strip.strip_bytes(data, keep_color_profile=False)
        self.assertEqual(result.data, self.extended(0, self.image))
        self.assertEqual(result.removed, ("colour profile",))
        self.assertEqual(result.kept, ())

    def test_the_colour_profile_is_kept_by_default(self) -> None:
        data = self.extended(fx.VP8X_ICC, fx.webp_chunk(b"ICCP", fx.icc_profile()), self.image)
        result = strip.strip_bytes(data)
        self.assertEqual(result.data, data)
        self.assertEqual(result.kept, ("colour profile",))
        self.assertFalse(result.changed)

    def test_orientation_in_xmp_is_written_as_an_exif_chunk_with_its_flag(self) -> None:
        data = self.extended(
            fx.VP8X_XMP, self.image, fx.webp_chunk(b"XMP ", fx.xmp_packet("6"))
        )
        result = strip.strip_bytes(data)
        self.assertEqual(
            result.data,
            self.extended(
                fx.VP8X_EXIF, self.image, fx.webp_chunk(b"EXIF", fx.orientation_only_tiff(6))
            ),
        )
        self.assertEqual(result.removed, ("XMP",))

    def test_conflicting_orientations_refuse_the_file(self) -> None:
        data = self.extended(
            fx.VP8X_EXIF | fx.VP8X_XMP,
            self.image,
            fx.webp_chunk(b"EXIF", fx.photo_tiff(6)),
            fx.webp_chunk(b"XMP ", fx.xmp_packet("8")),
        )
        self.assertEqual(refusal(data), DAMAGED)

    def test_unreadable_exif_refuses_the_file(self) -> None:
        data = self.extended(
            fx.VP8X_EXIF, self.image, fx.webp_chunk(b"EXIF", fx.photo_tiff(9))
        )
        self.assertEqual(refusal(data), UNREADABLE)

    def test_an_xmp_block_over_one_mebibyte_is_not_read_safely(self) -> None:
        data = self.extended(
            fx.VP8X_XMP,
            self.image,
            fx.webp_chunk(b"XMP ", b"x" * (1024 * 1024 + 1)),
        )
        self.assertEqual(refusal(data), UNREADABLE)

    def test_data_after_the_riff_payload_is_removed_and_named(self) -> None:
        video = strip.strip_bytes(self.extended(0, self.image, trailer=fx.MOTION_VIDEO))
        self.assertEqual(video.removed, ("video after the image",))
        self.assertEqual(video.data, self.extended(0, self.image))
        other = strip.strip_bytes(self.simple + b"trailing bytes")
        self.assertEqual(other.removed, ("data after the image",))
        self.assertEqual(other.data, self.simple)

    def test_a_simple_webp_comes_back_unchanged(self) -> None:
        result = strip.strip_bytes(self.simple)
        self.assertEqual(result.data, self.simple)
        self.assertEqual(result.removed, ())
        self.assertFalse(result.changed)
        self.assertEqual(result.kind, "webp")

    def test_pillow_written_metadata_is_removed_and_the_picture_stays(self) -> None:
        buffer = io.BytesIO()
        picture = fx.noise(40, 30, 1)
        picture.save(
            buffer,
            "WEBP",
            lossless=True,
            exif=fx.photo_tiff(None),
            xmp=fx.xmp_packet(),
            icc_profile=fx.icc_profile(),
        )
        original = buffer.getvalue()
        result = strip.strip_bytes(original, keep_color_profile=False)
        self.assertEqual(result.removed, ("EXIF", "GPS", "XMP", "colour profile"))
        self.assert_same_picture(original, result.data)
        self.assertEqual(fx.pixels(result.data)[2], picture.tobytes())
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertEqual(dict(image.getexif()), {})
            self.assertNotIn("xmp", image.info)
            self.assertNotIn("icc_profile", image.info)

    def test_animated_webps_are_refused_by_name(self) -> None:
        frames = [fx.noise(16, 16, seed) for seed in (1, 2, 3)]
        buffer = io.BytesIO()
        frames[0].save(buffer, "WEBP", save_all=True, append_images=frames[1:], lossless=True)
        self.assertEqual(refusal(buffer.getvalue()), ANIMATED)
        flagged = self.extended(fx.VP8X_ANIMATION, self.image)
        self.assertEqual(refusal(flagged), ANIMATED)
        anim = self.extended(0, fx.webp_chunk(b"ANIM", bytes(6)), self.image)
        self.assertEqual(refusal(anim), ANIMATED)

    def test_damaged_files_are_refused(self) -> None:
        extended = self.extended(fx.VP8X_EXIF, self.image, fx.webp_chunk(b"EXIF", fx.photo_tiff(None)))
        cases = {
            "cut in the middle": extended[: len(extended) // 2],
            "the riff size is larger than the file": extended[:-4],
            "only the riff header": b"RIFF\x04\0\0\0WEBP",
            "no image chunk": fx.riff(fx.vp8x(0, self.width, self.height)),
            "two image chunks": self.extended(0, self.image, self.image),
            "exif without an extended header": fx.riff(
                self.image, fx.webp_chunk(b"EXIF", fx.photo_tiff(None))
            ),
            "an extended header that is not first": fx.riff(
                self.image, fx.vp8x(0, self.width, self.height)
            ),
            "an extended header of the wrong length": fx.riff(
                fx.webp_chunk(b"VP8X", bytes(12)), self.image
            ),
            "two extended headers": fx.riff(
                fx.vp8x(0, self.width, self.height), fx.vp8x(0, self.width, self.height), self.image
            ),
            "a chunk longer than the file": fx.riff(
                fx.vp8x(0, self.width, self.height), b"ABCD" + struct.pack("<I", 999) + b"xx"
            ),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(refusal(data), DAMAGED)


# --- content that is not a supported image ---------------------------------------------------


class NotAnImageTests(StripCase):
    def test_content_that_is_not_jpeg_png_or_webp_is_refused(self) -> None:
        gif = io.BytesIO()
        fx.noise(10, 10).save(gif, "GIF")
        bmp = io.BytesIO()
        fx.noise(10, 10).save(bmp, "BMP")
        cases = {
            "nothing": b"",
            "text": b"this is not an image",
            "a gif": gif.getvalue(),
            "a bmp": bmp.getvalue(),
            "a riff file that is not webp": b"RIFF\x24\0\0\0WAVEfmt " + bytes(24),
            "a pdf": b"%PDF-1.7\n" + bytes(64),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(refusal(data), DAMAGED)

    def test_the_error_is_an_exception_with_the_text_for_the_result_line(self) -> None:
        with self.assertRaises(strip.StripError) as caught:
            strip.strip_bytes(b"nope")
        self.assertEqual(str(caught.exception), DAMAGED)
        self.assertIsInstance(caught.exception, Exception)

    def test_the_texts_are_exported(self) -> None:
        self.assertEqual(strip.DAMAGED, DAMAGED)
        self.assertEqual(strip.UNREADABLE, UNREADABLE)
        self.assertEqual(strip.ANIMATED, ANIMATED)


# --- EXIF ---------------------------------------------------------------------------------


class ReadExifTests(unittest.TestCase):
    def test_a_phone_photo_is_read_field_by_field(self) -> None:
        fields = strip.read_exif(fx.photo_tiff(6))
        self.assertEqual(fields.camera, "AcmeCam X1")
        self.assertEqual(fields.software, "1.2.3")
        self.assertEqual(fields.date, "2026:10:03 09:00:00")
        self.assertEqual(fields.gps, "41.500000, 69.250000")
        self.assertEqual(fields.orientation, 6)
        self.assertFalse(fields.unreadable)

    def test_a_gps_directory_without_a_position_is_read_as_no_position(self) -> None:
        # Cameras without GPS, and Photoshop, write a GPS directory that holds
        # only its version number.
        version_only = fx.tiff_block(
            [fx.ascii_entry(0x010F, "Canon"), fx.short_entry(0x0112, 1)],
            {0x8825: [(0x0000, 1, 4, b"\x02\x03\x00\x00")]},
        )
        fields = strip.read_exif(version_only)
        self.assertIsNone(fields.gps)
        self.assertFalse(fields.unreadable)
        self.assertEqual(fields.camera, "Canon")

    def test_a_gps_position_with_half_its_parts_is_still_unreadable(self) -> None:
        latitude_only = fx.tiff_block(
            [fx.ascii_entry(0x010F, "Canon")],
            {0x8825: [fx.ascii_entry(1, "N"), fx.rational_entry(2, [(41, 1), (30, 1), (0, 1)])]},
        )
        self.assertTrue(strip.read_exif(latitude_only).unreadable)

    def test_a_photo_whose_gps_directory_has_no_position_is_cleaned_without_naming_gps(self) -> None:
        version_only = fx.tiff_block(
            [fx.ascii_entry(0x010F, "Canon")],
            {0x8825: [(0x0000, 1, 4, b"\x02\x03\x00\x00")]},
        )
        body = fx.jpeg_body(fx.plain_jpeg())
        result = strip.strip_bytes(fx.build_jpeg([fx.JFIF_CLEAN, fx.exif_app1(version_only)], body))
        self.assertEqual(result.removed, ("EXIF",))
        self.assertEqual(result.data, fx.build_jpeg([fx.JFIF_CLEAN], body))

    def test_the_file_date_is_used_when_there_is_no_original_date(self) -> None:
        only_file_date = fx.tiff_block([fx.ascii_entry(0x0132, "2020:01:02 03:04:05")])
        self.assertEqual(strip.read_exif(only_file_date).date, "2020:01:02 03:04:05")

    def test_the_block_may_start_with_the_exif_prefix(self) -> None:
        tiff = fx.photo_tiff(6)
        self.assertEqual(strip.read_exif(b"Exif\0\0" + tiff), strip.read_exif(tiff))

    def test_big_endian_blocks_are_read(self) -> None:
        fields = strip.read_exif(fx.photo_tiff(8, big=True))
        self.assertEqual(fields.orientation, 8)
        self.assertEqual(fields.gps, "41.500000, 69.250000")
        self.assertEqual(fields.camera, "AcmeCam X1")

    def test_south_and_west_are_negative(self) -> None:
        fields = strip.read_exif(fx.photo_tiff(1, south_west=True))
        self.assertEqual(fields.gps, "-41.500000, -69.250000")

    def test_minutes_and_seconds_count(self) -> None:
        tiff = fx.tiff_block(
            [],
            {
                0x8825: [
                    fx.ascii_entry(1, "N"),
                    fx.rational_entry(2, [(10, 1), (30, 1), (1800, 100)]),
                    fx.ascii_entry(3, "E"),
                    fx.rational_entry(4, [(20, 1), (0, 1), (3600, 100)]),
                ]
            },
        )
        self.assertEqual(strip.read_exif(tiff).gps, "10.505000, 20.010000")

    def test_a_block_with_only_an_orientation_has_no_other_fields(self) -> None:
        fields = strip.read_exif(fx.orientation_only_tiff(6))
        self.assertEqual(fields.orientation, 6)
        self.assertIsNone(fields.camera)
        self.assertIsNone(fields.date)
        self.assertIsNone(fields.software)
        self.assertIsNone(fields.gps)
        self.assertFalse(fields.unreadable)

    def test_a_block_without_an_orientation_reports_none(self) -> None:
        self.assertIsNone(strip.read_exif(fx.photo_tiff(None)).orientation)

    def test_a_long_orientation_is_read_and_a_long_zero_means_none(self) -> None:
        three = fx.tiff_block([fx.long_entry(0x0112, 3)])
        zero = fx.tiff_block([fx.long_entry(0x0112, 0)])
        self.assertEqual(strip.read_exif(three).orientation, 3)
        unspecified = strip.read_exif(zero)
        self.assertIsNone(unspecified.orientation)
        self.assertFalse(unspecified.unreadable)

    def test_a_short_orientation_of_zero_means_none_too(self) -> None:
        fields = strip.read_exif(fx.tiff_block([fx.short_entry(0x0112, 0)]))
        self.assertIsNone(fields.orientation)
        self.assertFalse(fields.unreadable)

    def test_control_and_direction_characters_are_taken_out_of_text(self) -> None:
        tiff = fx.tiff_block(
            [fx.ascii_entry(0x010F, "Acme‮\x07Cam"), fx.ascii_entry(0x0110, "  X1⁦ ")]
        )
        self.assertEqual(strip.read_exif(tiff).camera, "AcmeCam X1")

    def test_text_stops_at_the_first_nul(self) -> None:
        tiff = fx.tiff_block([fx.ascii_entry(0x0131, "1.2\0hidden tail")])
        self.assertEqual(strip.read_exif(tiff).software, "1.2")

    def test_blocks_that_cannot_be_read_safely_are_flagged(self) -> None:
        good = fx.photo_tiff(6)
        gps_with = lambda *entries: fx.tiff_block([], {0x8825: list(entries)})  # noqa: E731
        north = fx.ascii_entry(1, "N")
        east = fx.ascii_entry(3, "E")
        lon = fx.rational_entry(4, [(69, 1), (15, 1), (0, 1)])
        cases = {
            "empty": b"",
            "too short": b"II*\0\x08\0",
            "not a tiff": b"this is not a tiff block",
            "bad magic number": b"II\x2b\0\x08\0\0\0" + bytes(10),
            "truncated": good[:30],
            "an orientation of 9": fx.photo_tiff(9),
            "an orientation stored as text": fx.tiff_block([fx.ascii_entry(0x0112, "6")]),
            "a repeated tag": fx.tiff_block([fx.short_entry(0x0112, 6), fx.short_entry(0x0112, 3)]),
            "a gps direction that is not N or S": gps_with(
                fx.ascii_entry(1, "X"),
                fx.rational_entry(2, [(41, 1), (30, 1), (0, 1)]),
                east,
                lon,
            ),
            "a gps position missing its longitude": gps_with(
                north, fx.rational_entry(2, [(41, 1), (30, 1), (0, 1)])
            ),
            "gps minutes of 60": gps_with(
                north, fx.rational_entry(2, [(41, 1), (60, 1), (0, 1)]), east, lon
            ),
            "gps with a zero denominator": gps_with(
                north, fx.rational_entry(2, [(41, 0), (30, 1), (0, 1)]), east, lon
            ),
            "a latitude over 90": gps_with(
                north, fx.rational_entry(2, [(91, 1), (0, 1), (0, 1)]), east, lon
            ),
        }
        for name, block in cases.items():
            with self.subTest(name):
                self.assertTrue(strip.read_exif(block).unreadable)

    def test_a_block_that_cannot_be_read_has_no_fields(self) -> None:
        fields = strip.read_exif(b"garbage")
        self.assertIsNone(fields.orientation)
        self.assertIsNone(fields.gps)


class OrientationExifTests(unittest.TestCase):
    def test_the_block_is_26_little_endian_bytes_holding_only_the_orientation(self) -> None:
        self.assertEqual(
            strip.orientation_exif(6),
            bytes(
                [73, 73, 42, 0, 8, 0, 0, 0, 1, 0, 18, 1, 3, 0, 1, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0]
            ),
        )

    def test_the_value_is_the_only_byte_that_changes(self) -> None:
        for value in range(1, 9):
            with self.subTest(value=value):
                block = strip.orientation_exif(value)
                self.assertEqual(len(block), 26)
                self.assertEqual(block[:18] + block[19:], strip.orientation_exif(6)[:18] + strip.orientation_exif(6)[19:])
                self.assertEqual(block[18], value)

    def test_the_block_reads_back_as_the_same_orientation_and_no_other_field(self) -> None:
        for value in range(1, 9):
            with self.subTest(value=value):
                fields = strip.read_exif(strip.orientation_exif(value))
                self.assertEqual(fields.orientation, value)
                self.assertIsNone(fields.camera)
                self.assertIsNone(fields.gps)
                self.assertFalse(fields.unreadable)

    def test_pillow_reads_the_block_too(self) -> None:
        exif = Image.Exif()
        exif.load(strip.orientation_exif(8))
        self.assertEqual(dict(exif), {0x0112: 8})


# --- names -------------------------------------------------------------------------------------


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="jzr-strip-core-")
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name)

    def listing(self, folder: Path | None = None) -> list[str]:
        return sorted(path.name for path in (folder or self.dir).iterdir())


class ConstantsTests(unittest.TestCase):
    def test_the_formats_that_can_be_stripped(self) -> None:
        self.assertEqual(strip.STRIPPABLE, frozenset({"jpg", "jpeg", "png", "webp"}))

    def test_the_stem_a_scan_skips(self) -> None:
        self.assertEqual(strip.CLEAN_STEM, "_clean")

    def test_the_segment_limit(self) -> None:
        self.assertEqual(strip.SEGMENT_LIMIT, 10_000)


class NotStrippableTests(unittest.TestCase):
    def test_the_supported_formats_give_none_in_any_case_with_or_without_the_dot(self) -> None:
        for extension in ("jpg", "jpeg", "png", "webp", "JPG", "Png", ".webp", ".JPEG"):
            with self.subTest(extension=extension):
                self.assertIsNone(strip.not_strippable(extension))

    def test_other_formats_say_why(self) -> None:
        self.assertEqual(strip.not_strippable("heic"), HEIC_TEXT)
        self.assertEqual(strip.not_strippable(".HEIC"), HEIC_TEXT)

    def test_the_extension_is_named_in_lower_case(self) -> None:
        self.assertEqual(
            strip.not_strippable("GIF"),
            ".gif metadata cannot be removed without re-encoding; "
            "use jzr image compress --strip (or convert)",
        )
        self.assertTrue(strip.not_strippable("tiff").startswith(".tiff metadata"))

    def test_a_name_without_an_extension_says_this_format(self) -> None:
        self.assertEqual(
            strip.not_strippable(""),
            "this format metadata cannot be removed without re-encoding; "
            "use jzr image compress --strip (or convert)",
        )


class CleanNameTests(unittest.TestCase):
    def test_the_name_is_the_stem_then_clean_then_the_suffix(self) -> None:
        self.assertEqual(strip.clean_name(Path("holiday/photo.jpg")), "photo_clean.jpg")

    def test_the_suffix_is_kept_as_written(self) -> None:
        self.assertEqual(strip.clean_name(Path("IMG.JPG")), "IMG_clean.JPG")
        self.assertEqual(strip.clean_name(Path("Shot.Jpeg")), "Shot_clean.Jpeg")

    def test_only_the_last_extension_is_split_off(self) -> None:
        self.assertEqual(strip.clean_name(Path("a.tar.png")), "a.tar_clean.png")

    def test_a_file_without_a_suffix(self) -> None:
        self.assertEqual(strip.clean_name(Path("photo")), "photo_clean")


class CleanTargetsTests(TempDirCase):
    def targets(self, sources: list[Path], **fields) -> list[Path]:  # noqa: ANN003
        fields.setdefault("outdir", None)
        fields.setdefault("root", None)
        return strip.clean_targets(sources, **fields)

    def test_targets_sit_beside_their_sources(self) -> None:
        sub = self.dir / "sub"
        self.assertEqual(
            self.targets([self.dir / "a.jpg", sub / "b.PNG"]),
            [self.dir / "a_clean.jpg", sub / "b_clean.PNG"],
        )

    def test_outdir_without_root_is_flat(self) -> None:
        out = self.dir / "out"
        self.assertEqual(
            self.targets([self.dir / "x" / "a.jpg", self.dir / "y" / "b.png"], outdir=out),
            [out / "a_clean.jpg", out / "b_clean.png"],
        )

    def test_outdir_with_root_mirrors_the_folders_under_the_root(self) -> None:
        out = self.dir / "out"
        self.assertEqual(
            self.targets(
                [self.dir / "a.jpg", self.dir / "sub" / "deep" / "b.png"],
                outdir=out,
                root=self.dir,
            ),
            [out / "a_clean.jpg", out / "sub" / "deep" / "b_clean.png"],
        )

    def test_a_source_outside_the_root_goes_flat_into_outdir(self) -> None:
        other = tempfile.TemporaryDirectory(prefix="jzr-strip-other-")
        self.addCleanup(other.cleanup)
        out = self.dir / "out"
        self.assertEqual(
            self.targets([Path(other.name) / "a.jpg"], outdir=out, root=self.dir / "root"),
            [out / "a_clean.jpg"],
        )

    def test_sources_that_share_a_name_get_numbered(self) -> None:
        out = self.dir / "out"
        self.assertEqual(
            self.targets(
                [self.dir / "x" / "a.jpg", self.dir / "y" / "a.jpg", self.dir / "z" / "a.jpg"],
                outdir=out,
            ),
            [out / "a_clean.jpg", out / "a_clean-2.jpg", out / "a_clean-3.jpg"],
        )

    def test_names_that_differ_only_by_case_collide(self) -> None:
        self.assertEqual(
            self.targets([self.dir / "x" / "a.jpg", self.dir / "y" / "A.jpg"], outdir=self.dir / "o"),
            [self.dir / "o" / "a_clean.jpg", self.dir / "o" / "A_clean-2.jpg"],
        )

    def test_different_names_are_not_numbered(self) -> None:
        self.assertEqual(
            self.targets([self.dir / "a.jpg", self.dir / "b.jpg"]),
            [self.dir / "a_clean.jpg", self.dir / "b_clean.jpg"],
        )


# --- the text of a result -----------------------------------------------------------------------


def stripped(removed: tuple[str, ...], size: int) -> strip.Stripped:
    return strip.Stripped(
        kind="jpeg", data=b"x" * size, removed=removed, kept=(), changed=True
    )


class RemovedTextTests(unittest.TestCase):
    def test_labels_and_the_size_saved(self) -> None:
        self.assertEqual(
            strip.removed_text(stripped(("EXIF", "GPS", "XMP"), 2_000), 50_000),
            "removed EXIF, GPS, XMP; 48.0 kB smaller",
        )

    def test_a_dry_run_says_would_remove(self) -> None:
        self.assertEqual(
            strip.removed_text(stripped(("EXIF", "GPS"), 2_000), 50_000, dry_run=True),
            "would remove EXIF, GPS; 48.0 kB smaller",
        )

    def test_a_small_saving_is_in_bytes(self) -> None:
        self.assertEqual(
            strip.removed_text(stripped(("comments",), 900), 960),
            "removed comments; 60 B smaller",
        )

    def test_one_label(self) -> None:
        self.assertEqual(
            strip.removed_text(stripped(("text",), 10), 5_010), "removed text; 5.0 kB smaller"
        )

    def test_there_is_no_size_part_when_the_size_did_not_drop(self) -> None:
        self.assertEqual(strip.removed_text(stripped(("EXIF",), 100), 100), "removed EXIF")
        self.assertEqual(strip.removed_text(stripped(("EXIF",), 130), 100), "removed EXIF")
        self.assertEqual(
            strip.removed_text(stripped(("EXIF",), 130), 100, dry_run=True), "would remove EXIF"
        )


# --- one file -------------------------------------------------------------------------------------


class StripFileTests(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.body = fx.jpeg_body(fx.plain_jpeg())
        self.dirty = fx.build_jpeg(
            [fx.JFIF_DIRTY, fx.exif_app1(fx.photo_tiff(6)), fx.xmp_app1(), fx.COMMENT],
            self.body,
            fx.MOTION_VIDEO,
        )
        self.cleaned = fx.build_jpeg(
            [fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6))], self.body
        )
        self.labels = "EXIF, GPS, XMP, comments, thumbnail, print density, video after the image"
        self.saved = format_size(len(self.dirty) - len(self.cleaned))

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def run_strip(self, source: Path, target: Path, **options):  # noqa: ANN003, ANN201
        options.setdefault("keep_color_profile", True)
        options.setdefault("overwrite", False)
        options.setdefault("dry_run", False)
        return strip.strip_file(source, target, **options)

    def test_a_dirty_file_is_written_to_the_target_and_reported(self) -> None:
        source = self.write("a.jpg", self.dirty)
        target = self.dir / "a_clean.jpg"
        result = self.run_strip(source, target)
        self.assertEqual(result.status, "cleaned")
        self.assertEqual(result.detail, f"removed {self.labels}; {self.saved} smaller")
        self.assertEqual((result.source, result.target, result.command), (source, target, ()))
        self.assertTrue(result.ok)
        self.assertEqual(target.read_bytes(), self.cleaned)
        self.assertEqual(source.read_bytes(), self.dirty)
        self.assertEqual(self.listing(), ["a.jpg", "a_clean.jpg"])

    def test_the_colour_profile_option_reaches_the_cleaning(self) -> None:
        data = fx.build_jpeg([fx.JFIF_CLEAN, fx.icc_app2()], self.body)
        source = self.write("a.jpg", data)
        target = self.dir / "a_clean.jpg"
        result = self.run_strip(source, target, keep_color_profile=False)
        self.assertEqual(result.status, "cleaned")
        self.assertTrue(result.detail.startswith("removed colour profile; "))
        self.assertEqual(target.read_bytes(), fx.build_jpeg([fx.JFIF_CLEAN], self.body))

    def test_a_dry_run_reports_what_would_go_and_writes_nothing(self) -> None:
        source = self.write("a.jpg", self.dirty)
        result = self.run_strip(source, self.dir / "a_clean.jpg", dry_run=True)
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.detail, f"would remove {self.labels}; {self.saved} smaller")
        self.assertEqual(self.listing(), ["a.jpg"])
        self.assertEqual(source.read_bytes(), self.dirty)

    def test_a_dry_run_does_not_create_the_target_folder(self) -> None:
        source = self.write("a.jpg", self.dirty)
        self.run_strip(source, self.dir / "new" / "a_clean.jpg", dry_run=True)
        self.assertEqual(self.listing(), ["a.jpg"])

    def test_a_dry_run_reads_the_file_so_a_damaged_one_fails(self) -> None:
        source = self.write("a.jpg", b"not an image")
        result = self.run_strip(source, self.dir / "a_clean.jpg", dry_run=True)
        self.assertEqual((result.status, result.detail), ("failed", DAMAGED))
        self.assertFalse(result.ok)

    def test_a_dry_run_skips_a_file_with_nothing_to_remove(self) -> None:
        source = self.write("a.jpg", self.cleaned)
        result = self.run_strip(source, self.dir / "a_clean.jpg", dry_run=True)
        self.assertEqual((result.status, result.detail), ("skipped", "no metadata to remove"))

    def test_a_file_with_nothing_to_remove_is_skipped_and_nothing_is_written(self) -> None:
        source = self.write("a.jpg", self.cleaned)
        result = self.run_strip(source, self.dir / "a_clean.jpg")
        self.assertEqual((result.status, result.detail), ("skipped", "no metadata to remove"))
        self.assertTrue(result.ok)
        self.assertEqual(self.listing(), ["a.jpg"])

    def test_a_format_that_cannot_be_stripped_fails_without_reading_the_file(self) -> None:
        source = self.write("a.heic", b"\0\0\0\x18ftypheic")
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                result = self.run_strip(source, self.dir / "a_clean.heic", dry_run=dry_run)
                self.assertEqual((result.status, result.detail), ("failed", HEIC_TEXT))
        self.assertEqual(self.listing(), ["a.heic"])

    def test_a_format_that_cannot_be_stripped_fails_even_when_the_target_exists(self) -> None:
        source = self.write("a.heic", b"x")
        target = self.write("a_clean.heic", b"old")
        result = self.run_strip(source, target)
        self.assertEqual((result.status, result.detail), ("failed", HEIC_TEXT))
        self.assertEqual(target.read_bytes(), b"old")

    def test_a_missing_source_fails_with_the_system_text(self) -> None:
        result = self.run_strip(self.dir / "gone.jpg", self.dir / "gone_clean.jpg")
        self.assertEqual(result.status, "failed")
        self.assertIn("No such file or directory", result.detail)
        self.assertEqual(self.listing(), [])

    def test_each_refusal_fails_the_file_and_writes_nothing(self) -> None:
        refused = {
            DAMAGED: self.dirty[: len(self.dirty) // 3],
            UNREADABLE: fx.build_jpeg([fx.exif_app1(fx.photo_tiff(9))], self.body),
        }
        for text, data in refused.items():
            with self.subTest(text):
                source = self.write("a.jpg", data)
                result = self.run_strip(source, self.dir / "a_clean.jpg")
                self.assertEqual((result.status, result.detail), ("failed", text))
                self.assertEqual(self.listing(), ["a.jpg"])
                self.assertEqual(source.read_bytes(), data)

    def test_an_animated_png_fails_with_the_animation_text(self) -> None:
        frames = [fx.noise(16, 16, seed) for seed in (1, 2)]
        buffer = io.BytesIO()
        frames[0].save(buffer, "PNG", save_all=True, append_images=frames[1:])
        source = self.write("a.png", buffer.getvalue())
        result = self.run_strip(source, self.dir / "a_clean.png")
        self.assertEqual((result.status, result.detail), ("failed", ANIMATED))

    def test_an_existing_target_is_skipped_and_left_alone(self) -> None:
        source = self.write("a.jpg", self.dirty)
        target = self.write("a_clean.jpg", b"old")
        result = self.run_strip(source, target)
        self.assertEqual(
            (result.status, result.detail), ("skipped", "target exists (use --overwrite)")
        )
        self.assertEqual(target.read_bytes(), b"old")

    def test_an_existing_target_is_skipped_before_the_source_is_read(self) -> None:
        source = self.write("a.jpg", b"not an image")
        self.write("a_clean.jpg", b"old")
        result = self.run_strip(source, self.dir / "a_clean.jpg")
        self.assertEqual(result.status, "skipped")

    def test_overwrite_replaces_an_existing_target(self) -> None:
        source = self.write("a.jpg", self.dirty)
        target = self.write("a_clean.jpg", b"old")
        result = self.run_strip(source, target, overwrite=True)
        self.assertEqual(result.status, "cleaned")
        self.assertEqual(target.read_bytes(), self.cleaned)
        self.assertEqual(self.listing(), ["a.jpg", "a_clean.jpg"])

    def test_the_source_is_not_its_own_target_without_overwrite(self) -> None:
        source = self.write("a.jpg", self.dirty)
        result = self.run_strip(source, source)
        self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))
        self.assertEqual(source.read_bytes(), self.dirty)

    def test_the_same_path_written_another_way_is_still_the_source(self) -> None:
        source = self.write("a.jpg", self.dirty)
        (self.dir / "sub").mkdir()
        other = self.dir / "sub" / ".." / "a.jpg"
        result = self.run_strip(source, other)
        self.assertEqual((result.status, result.detail), ("skipped", "source is the target"))

    def test_overwrite_with_the_source_as_target_replaces_it_in_place(self) -> None:
        source = self.write("a.jpg", self.dirty)
        result = self.run_strip(source, source, overwrite=True)
        self.assertEqual(result.status, "cleaned")
        self.assertEqual(result.detail, f"removed {self.labels}; {self.saved} smaller")
        self.assertEqual(source.read_bytes(), self.cleaned)
        self.assertEqual(self.listing(), ["a.jpg"])

    def test_an_in_place_file_that_is_refused_is_left_unchanged(self) -> None:
        data = self.dirty[: len(self.dirty) // 3]
        source = self.write("a.jpg", data)
        result = self.run_strip(source, source, overwrite=True)
        self.assertEqual((result.status, result.detail), ("failed", DAMAGED))
        self.assertEqual(source.read_bytes(), data)
        self.assertEqual(self.listing(), ["a.jpg"])

    def test_an_in_place_file_with_nothing_to_remove_is_not_rewritten(self) -> None:
        source = self.write("a.jpg", self.cleaned)
        before = os.stat(source).st_mtime_ns
        result = self.run_strip(source, source, overwrite=True)
        self.assertEqual((result.status, result.detail), ("skipped", "no metadata to remove"))
        self.assertEqual(os.stat(source).st_mtime_ns, before)
        self.assertEqual(self.listing(), ["a.jpg"])

    def test_the_target_folder_may_be_another_folder(self) -> None:
        source = self.write("a.jpg", self.dirty)
        out = self.dir / "out"
        out.mkdir()
        result = self.run_strip(source, out / "a_clean.jpg")
        self.assertEqual(result.status, "cleaned")
        self.assertEqual(self.listing(out), ["a_clean.jpg"])

    def test_png_and_webp_files_are_written_the_same_way(self) -> None:
        ihdr, idat, iend = fx.png_header_and_data(fx.plain_png())
        png = self.write("a.png", fx.build_png(ihdr, fx.text_chunk(), idat, iend))
        image, _simple = fx.lossless_webp_image()
        webp = self.write(
            "a.webp",
            fx.riff(
                fx.vp8x(fx.VP8X_EXIF, 40, 30),
                image,
                fx.webp_chunk(b"EXIF", fx.photo_tiff(None, gps=False)),
            ),
        )
        for source, expected in (
            (png, fx.build_png(ihdr, idat, iend)),
            (webp, fx.riff(fx.vp8x(0, 40, 30), image)),
        ):
            with self.subTest(source.name):
                target = self.dir / f"{source.stem}_clean{source.suffix}"
                result = self.run_strip(source, target)
                self.assertEqual(result.status, "cleaned")
                self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(
            self.listing(), ["a.png", "a.webp", "a_clean.png", "a_clean.webp"]
        )


if __name__ == "__main__":
    unittest.main()

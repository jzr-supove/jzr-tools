"""Tests for ``--background``, LZW-compressed TIFF output and colour parsing.

Fixtures are built with Pillow in temporary folders and outputs are checked by
reopening them. Only the tests that run ImageMagick need it on PATH; the argv
tests do not.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from jzr_tools.core import images, pillow_engine
from jzr_tools.core.images import build_command, convert_file, detect_backends

runner = CliRunner()

HAS_MAGICK = "magick" in detect_backends()

TIFF_MODES = ["1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I", "I;16", "F"]


def bad_colour_message(value: str) -> str:
    return f"'{value}' is not a colour; use #rrggbb, #rgb, white, black or grey"


def note(extension: str) -> str:
    return f"note: .{extension} keeps transparency; --background is ignored"


def image_cli(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", *args])


def alpha_strip(path: Path) -> Path:
    """Write a 3x1 RGBA PNG: clear red, half-transparent red, opaque green."""
    image = Image.new("RGBA", (3, 1))
    image.putdata([(255, 0, 0, 0), (255, 0, 0, 128), (0, 255, 0, 255)])
    image.save(path)
    return path


def pixels(path: Path) -> list[tuple[int, ...]]:
    with Image.open(path) as image:
        return list(image.convert("RGB").getdata())


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 2) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def compression_tag(path: Path) -> int:
    with Image.open(path) as image:
        return image.tag_v2[259]


class ParseColourTests(unittest.TestCase):
    def test_short_hex_doubles_each_digit(self) -> None:
        self.assertEqual(images.parse_colour("#fff"), (255, 255, 255))
        self.assertEqual(images.parse_colour("#f80"), (255, 136, 0))
        self.assertEqual(images.parse_colour("#000"), (0, 0, 0))

    def test_long_hex_in_any_case(self) -> None:
        self.assertEqual(images.parse_colour("#1a2b3c"), (26, 43, 60))
        self.assertEqual(images.parse_colour("#1A2B3C"), (26, 43, 60))
        self.assertEqual(images.parse_colour("#aBcDeF"), (171, 205, 239))

    def test_short_hex_in_any_case(self) -> None:
        self.assertEqual(images.parse_colour("#ABC"), (170, 187, 204))

    def test_names_give_their_css_values(self) -> None:
        self.assertEqual(images.parse_colour("white"), (255, 255, 255))
        self.assertEqual(images.parse_colour("black"), (0, 0, 0))
        self.assertEqual(images.parse_colour("grey"), (128, 128, 128))
        self.assertEqual(images.parse_colour("gray"), (128, 128, 128))

    def test_names_ignore_case_and_surrounding_spaces(self) -> None:
        self.assertEqual(images.parse_colour("WHITE"), (255, 255, 255))
        self.assertEqual(images.parse_colour("  Black "), (0, 0, 0))
        self.assertEqual(images.parse_colour(" Gray"), (128, 128, 128))
        self.assertEqual(images.parse_colour("GREY  "), (128, 128, 128))

    def test_bad_values_raise_value_error_with_the_exact_message(self) -> None:
        for value in (
            "red",
            "",
            "#12",
            "#1234",
            "#12345",
            "#1234567",
            "#ggg",
            "#12345g",
            "1a2b3c",
            "fff",
            "#",
            "white-ish",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    images.parse_colour(value)
                self.assertEqual(str(caught.exception), bad_colour_message(value))


class CaveatTests(unittest.TestCase):
    def test_opaque_extensions_are_exactly_the_formats_without_transparency(self) -> None:
        self.assertEqual(images.OPAQUE_EXTENSIONS, frozenset({"bmp", "jpeg", "jpg", "ppm"}))

    def test_opaque_targets_have_no_caveat(self) -> None:
        for extension in sorted(images.OPAQUE_EXTENSIONS):
            with self.subTest(extension=extension):
                self.assertIsNone(images.background_caveat(extension))

    def test_transparency_capable_targets_get_the_note_text(self) -> None:
        for extension in ("png", "tif", "tiff", "gif", "webp", "ico", "tga", "avif", "heic"):
            with self.subTest(extension=extension):
                self.assertEqual(
                    images.background_caveat(extension),
                    f".{extension} keeps transparency; --background is ignored",
                )

    def test_every_supported_target_is_either_opaque_or_noted(self) -> None:
        for extension in sorted(images.SUPPORTED_EXTENSIONS):
            with self.subTest(extension=extension):
                caveat = images.background_caveat(extension)
                self.assertEqual(caveat is None, extension in images.OPAQUE_EXTENSIONS)


class PillowFlattenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-background-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        self.source = alpha_strip(self.root / "strip.png")

    def test_transparent_pixels_take_the_given_colour(self) -> None:
        target = self.root / "out.bmp"
        pillow_engine.convert(self.source, target, background=(10, 20, 30))
        first, _, last = pixels(target)
        self.assertEqual(first, (10, 20, 30))
        self.assertEqual(last, (0, 255, 0))

    def test_half_transparent_pixels_blend_with_the_given_colour(self) -> None:
        target = self.root / "out.bmp"
        pillow_engine.convert(self.source, target, background=(0, 0, 255))
        _, middle, _ = pixels(target)
        self.assertTrue(near(middle, (127, 0, 128)), middle)

    def test_without_a_colour_the_background_is_white(self) -> None:
        target = self.root / "out.bmp"
        pillow_engine.convert(self.source, target)
        first, middle, last = pixels(target)
        self.assertEqual(first, (255, 255, 255))
        self.assertTrue(near(middle, (255, 127, 127)), middle)
        self.assertEqual(last, (0, 255, 0))

    def test_black_background_darkens_the_half_transparent_pixel(self) -> None:
        target = self.root / "out.bmp"
        pillow_engine.convert(self.source, target, background=(0, 0, 0))
        first, middle, _ = pixels(target)
        self.assertEqual(first, (0, 0, 0))
        self.assertTrue(near(middle, (128, 0, 0)), middle)

    def test_photo_exif_with_dates_and_gps_survives_tiff_output(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Apple"
        exif.get_ifd(0x8769)[0x9003] = "2026:10:02 14:31:05"
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (41.0, 18.0, 39.9), "E", (69.0, 14.0, 26.0)
        source = self.root / "photo.jpg"
        Image.new("RGB", (16, 16), (1, 2, 3)).save(source, "JPEG", exif=exif)
        target = self.root / "photo.tif"
        pillow_engine.convert(source, target)
        with Image.open(target) as written:
            kept = written.getexif()
            self.assertEqual(kept.get(0x010F), "Apple")
            self.assertEqual(kept.get_ifd(0x8769).get(0x9003), "2026:10:02 14:31:05")
            self.assertEqual(kept.get_ifd(0x8825).get(1), "N")

    def test_photo_exif_without_sub_blocks_still_gets_lzw(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Apple"
        source = self.root / "plain.jpg"
        Image.new("RGB", (16, 16), (1, 2, 3)).save(source, "JPEG", exif=exif)
        target = self.root / "plain.tif"
        pillow_engine.convert(source, target)
        self.assertEqual(compression_tag(target), 5)
        with Image.open(target) as written:
            self.assertEqual(written.getexif().get(0x010F), "Apple")

    def test_jpeg_output_uses_the_colour_too(self) -> None:
        source = self.root / "clear.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 0)).save(source)
        target = self.root / "out.jpg"
        pillow_engine.convert(source, target, background=(0, 0, 255))
        self.assertTrue(near(pixels(target)[0], (0, 0, 255), 6), pixels(target)[0])

    def test_palette_image_with_a_transparent_index_takes_the_colour(self) -> None:
        palette = Image.new("P", (2, 1))
        palette.putpalette([255, 0, 0, 0, 255, 0] + [0] * 250 * 3)
        palette.putdata([0, 1])
        palette.info["transparency"] = 0
        source = self.root / "palette.png"
        palette.save(source)
        target = self.root / "palette.bmp"
        pillow_engine.convert(source, target, background=(1, 2, 3))
        first, second = pixels(target)
        self.assertEqual(first, (1, 2, 3))
        self.assertEqual(second, (0, 255, 0))

    def test_a_target_that_keeps_transparency_ignores_the_colour(self) -> None:
        target = self.root / "out.tif"
        pillow_engine.convert(self.source, target, background=(10, 20, 30))
        with Image.open(target) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_convert_file_passes_the_colour_to_the_pillow_engine(self) -> None:
        target = self.root / "viafile.bmp"
        result = convert_file(
            self.source,
            target,
            backend="pillow",
            executable=[],
            background=(9, 8, 7),
            overwrite=True,
        )
        self.assertEqual(result.status, "converted", result.detail)
        self.assertEqual(pixels(target)[0], (9, 8, 7))


class BackgroundCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-background-cli-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        self.source = alpha_strip(self.root / "strip.png")

    def test_pair_command_flattens_over_the_colour(self) -> None:
        result = image_cli("png2bmp", str(self.source), "--background", "#00f")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(pixels(self.root / "strip.bmp")[0], (0, 0, 255))

    def test_convert_flattens_over_the_colour(self) -> None:
        result = image_cli(
            "convert", str(self.source), "--to", "bmp", "--background", "#FF8800"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(pixels(self.root / "strip.bmp")[0], (255, 136, 0))

    def test_name_is_accepted_in_any_case(self) -> None:
        result = image_cli("png2bmp", str(self.source), "--background", "Black")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(pixels(self.root / "strip.bmp")[0], (0, 0, 0))

    def test_default_without_the_option_is_white(self) -> None:
        result = image_cli("png2bmp", str(self.source))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(pixels(self.root / "strip.bmp")[0], (255, 255, 255))

    def test_bad_value_is_a_usage_error_with_the_message(self) -> None:
        for command in (
            ["convert", str(self.source), "--to", "bmp"],
            ["png2jpg", str(self.source)],
        ):
            for value in ("red", "#12", "#gggggg"):
                with self.subTest(command=command[0], value=value):
                    result = image_cli(*command, "--background", value)
                    self.assertEqual(result.exit_code, 2, result.output)
                    self.assertIn(bad_colour_message(value), result.output)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["strip.png"])

    def test_bad_value_is_rejected_even_for_a_target_that_keeps_transparency(self) -> None:
        result = image_cli("png2tif", str(self.source), "--background", "red")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(bad_colour_message("red"), result.output)
        self.assertFalse((self.root / "strip.tif").exists())

    def test_help_describes_the_option_on_both_commands(self) -> None:
        for command in (["convert", "-h"], ["png2jpg", "-h"]):
            with self.subTest(command=command[0]):
                text = " ".join(image_cli(*command).stdout.split())
                self.assertIn("--background COLOR", text)
                self.assertIn(
                    "Colour for transparent areas in jpg, bmp and ppm output (default white).",
                    text,
                )

    def test_target_that_keeps_transparency_prints_the_note_once(self) -> None:
        second = alpha_strip(self.root / "second.png")
        for command, extension in (
            (["png2tif", str(self.source), str(second)], "tif"),
            (["convert", str(self.source), str(second), "--to", "gif"], "gif"),
            (["png2tiff", str(self.source), str(second)], "tiff"),
        ):
            with self.subTest(command=command[0], extension=extension):
                result = image_cli(*command, "--background", "white", "--overwrite")
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(result.stderr.count(note(extension)), 1, result.stderr)
                self.assertNotIn("ignored", result.stdout)

    def test_the_note_is_printed_in_a_dry_run_too(self) -> None:
        result = image_cli("png2tif", str(self.source), "--background", "#fff", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(note("tif")), 1, result.stderr)
        self.assertFalse((self.root / "strip.tif").exists())

    def test_no_note_for_a_target_without_transparency(self) -> None:
        for command in (
            ["png2jpg", str(self.source)],
            ["png2bmp", str(self.source)],
            ["png2ppm", str(self.source)],
            ["convert", str(self.source), "--to", "jpeg"],
        ):
            for extra in (["--background", "white"], ["--background", "white", "--dry-run"]):
                with self.subTest(command=command[0], extra=extra):
                    result = image_cli(*command, *extra, "--overwrite")
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertNotIn("keeps transparency", result.output)
                    self.assertNotIn("--background is ignored", result.output)

    def test_no_note_when_the_option_is_not_given(self) -> None:
        result = image_cli("png2tif", str(self.source))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--background", result.output)

    def test_target_that_keeps_transparency_still_keeps_it(self) -> None:
        result = image_cli("png2tif", str(self.source), "--background", "#123456")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open(self.root / "strip.tif") as image:
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_note_and_quality_note_can_both_appear(self) -> None:
        result = image_cli("png2tif", str(self.source), "--background", "white", "-q", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(note("tif")), 1, result.stderr)
        self.assertIn("--quality", result.stderr)


class MagickBackgroundArgvTests(unittest.TestCase):
    SOURCE = Path("in.png")

    def command(self, target: str, **options: object) -> list[str]:
        return build_command(
            "magick", ["magick"], self.SOURCE, Path(target), **options
        )

    def test_opaque_target_gets_the_flatten_arguments_after_the_source(self) -> None:
        for extension in sorted(images.OPAQUE_EXTENSIONS):
            with self.subTest(extension=extension):
                command = self.command(f"out.{extension}", background=(171, 205, 239))
                self.assertEqual(
                    command,
                    [
                        "magick",
                        "in.png[0]",
                        "-background",
                        "#abcdef",
                        "-alpha",
                        "remove",
                        "-alpha",
                        "off",
                        f"out.{extension}",
                    ],
                )

    def test_the_colour_is_written_as_lower_case_hex_with_every_digit(self) -> None:
        command = self.command("out.jpg", background=(0, 10, 255))
        self.assertEqual(command[command.index("-background") + 1], "#000aff")

    def test_flatten_arguments_come_before_quality(self) -> None:
        command = self.command("out.jpg", quality=80, background=(1, 2, 3))
        self.assertLess(command.index("off"), command.index("-quality"))
        self.assertEqual(command[command.index("-quality") + 1], "80")
        self.assertEqual(command[-1], "out.jpg")
        self.assertEqual(command[1], "in.png[0]")

    def test_flatten_arguments_stay_between_source_and_output_for_a_percent_path(self) -> None:
        command = build_command(
            "magick",
            ["magick"],
            Path("100%.png"),
            Path("out.jpg"),
            background=(1, 2, 3),
        )
        self.assertEqual(command[1], "100%.png")
        start = command.index("-background")
        self.assertEqual(
            command[start : start + 6],
            ["-background", "#010203", "-alpha", "remove", "-alpha", "off"],
        )
        self.assertEqual(command[-1], "out.jpg")

    def test_opaque_target_without_a_colour_has_no_flatten_arguments(self) -> None:
        for extension in sorted(images.OPAQUE_EXTENSIONS):
            with self.subTest(extension=extension):
                command = self.command(f"out.{extension}")
                self.assertEqual(command, ["magick", "in.png[0]", f"out.{extension}"])

    def test_transparency_capable_targets_ignore_the_colour(self) -> None:
        for extension in ("png", "gif", "webp", "ico", "tga", "avif", "heic"):
            with self.subTest(extension=extension):
                with_colour = self.command(f"out.{extension}", background=(1, 2, 3))
                without = self.command(f"out.{extension}")
                self.assertEqual(with_colour, without)
                self.assertNotIn("-background", with_colour)
                self.assertNotIn("-alpha", with_colour)

    def test_tiff_targets_ignore_the_colour_but_stay_lzw(self) -> None:
        for extension in ("tif", "tiff"):
            with self.subTest(extension=extension):
                command = self.command(f"out.{extension}", background=(1, 2, 3))
                self.assertNotIn("-background", command)
                self.assertNotIn("-alpha", command)
                self.assertEqual(command[-3:], ["-compress", "LZW", f"out.{extension}"])


class MagickLzwArgvTests(unittest.TestCase):
    def command(self, target: str, **options: object) -> list[str]:
        return build_command(
            "magick", ["magick"], Path("in.png"), Path(target), **options
        )

    def test_tiff_targets_get_lzw_just_before_the_output(self) -> None:
        for extension in ("tif", "tiff"):
            with self.subTest(extension=extension):
                self.assertEqual(
                    self.command(f"out.{extension}"),
                    ["magick", "in.png[0]", "-compress", "LZW", f"out.{extension}"],
                )

    def test_lzw_stays_last_with_a_quality(self) -> None:
        for extension in ("tif", "tiff"):
            with self.subTest(extension=extension):
                command = self.command(f"out.{extension}", quality=70)
                self.assertEqual(command[-3:], ["-compress", "LZW", f"out.{extension}"])
                self.assertEqual(command[command.index("-quality") + 1], "70")
                self.assertEqual(command.count("-compress"), 1)

    def test_target_spelling_in_upper_case_still_gets_lzw(self) -> None:
        command = self.command("OUT.TIF")
        self.assertEqual(command[-3:], ["-compress", "LZW", "OUT.TIF"])

    def test_other_targets_have_no_compress_argument(self) -> None:
        for extension in ("png", "jpg", "bmp", "gif", "webp", "ico", "ppm", "tga", "avif"):
            with self.subTest(extension=extension):
                self.assertNotIn("-compress", self.command(f"out.{extension}", quality=50))

    def test_dry_run_prints_the_new_arguments_in_the_planned_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jzr-background-argv-") as folder:
            source = alpha_strip(Path(folder) / "a.png")
            jpg = convert_file(
                source,
                Path(folder) / "a.jpg",
                backend="magick",
                executable=["magick"],
                background=(0, 0, 0),
                dry_run=True,
            )
            tif = convert_file(
                source,
                Path(folder) / "a.tif",
                backend="magick",
                executable=["magick"],
                dry_run=True,
            )
        self.assertEqual(jpg.status, "planned")
        self.assertIn("#000000", jpg.command)
        self.assertEqual(tif.status, "planned")
        self.assertEqual(tif.command[-3], "-compress")
        self.assertEqual(tif.command[-2], "LZW")


@unittest.skipUnless(HAS_MAGICK, "ImageMagick is not on PATH")
class MagickRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-background-magick-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        self.source = alpha_strip(self.root / "strip.png")

    def test_background_colour_reaches_the_output(self) -> None:
        result = image_cli(
            "png2bmp", str(self.source), "-b", "magick", "--background", "#0000ff"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        first, middle, last = pixels(self.root / "strip.bmp")
        self.assertTrue(near(first, (0, 0, 255), 3), first)
        self.assertTrue(near(middle, (127, 0, 128), 4), middle)
        self.assertTrue(near(last, (0, 255, 0), 3), last)

    def test_tiff_output_is_lzw(self) -> None:
        for extension in ("tif", "tiff"):
            with self.subTest(extension=extension):
                result = image_cli(
                    "convert", str(self.source), "--to", extension, "-b", "magick"
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(compression_tag(self.root / f"strip.{extension}"), 5)


class TiffLzwTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-lzw-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)

    def test_every_tiff_mode_is_written_with_lzw(self) -> None:
        for mode in TIFF_MODES:
            for extension in ("tif", "tiff"):
                with self.subTest(mode=mode, extension=extension):
                    name = mode.replace(";", "_")
                    source = self.root / f"in_{name}.tif"
                    Image.new(mode, (16, 16)).save(source)
                    self.assertEqual(compression_tag(source), 1)
                    target = self.root / f"out_{name}.{extension}"
                    pillow_engine.convert(source, target)
                    self.assertEqual(compression_tag(target), 5)
                    with Image.open(target) as written:
                        self.assertEqual(written.mode, mode)

    def test_png_source_goes_to_lzw_tiff_with_the_same_pixels(self) -> None:
        image = Image.new("RGB", (32, 32))
        image.putdata([(x * 8 % 256, y * 8 % 256, (x + y) % 256) for y in range(32) for x in range(32)])
        source = self.root / "photo.png"
        image.save(source)
        target = self.root / "photo.tif"
        pillow_engine.convert(source, target)
        self.assertEqual(compression_tag(target), 5)
        with Image.open(target) as written:
            self.assertEqual(list(written.getdata()), list(image.getdata()))

    def test_animated_source_gives_one_lzw_frame(self) -> None:
        frames = [Image.new("RGB", (8, 8), colour) for colour in ((255, 0, 0), (0, 255, 0))]
        source = self.root / "anim.gif"
        frames[0].save(source, save_all=True, append_images=frames[1:], duration=100)
        target = self.root / "anim.tif"
        pillow_engine.convert(source, target)
        self.assertEqual(compression_tag(target), 5)
        with Image.open(target) as written:
            self.assertEqual(getattr(written, "n_frames", 1), 1)

    def test_cli_tiff_output_is_lzw_with_and_without_quality(self) -> None:
        source = self.root / "plain.png"
        Image.new("RGB", (16, 16), (10, 20, 30)).save(source)
        for extension in ("tif", "tiff"):
            for extra in ([], ["-q", "50"]):
                with self.subTest(extension=extension, extra=extra):
                    result = image_cli(
                        "convert", str(source), "--to", extension, "--overwrite", *extra
                    )
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(compression_tag(self.root / f"plain.{extension}"), 5)

    def test_pair_command_tiff_output_is_lzw(self) -> None:
        source = self.root / "plain.png"
        Image.new("RGBA", (16, 16), (10, 20, 30, 40)).save(source)
        result = image_cli("png2tif", str(source))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(compression_tag(self.root / "plain.tif"), 5)

    def test_other_formats_are_not_affected(self) -> None:
        source = self.root / "plain.png"
        Image.new("RGB", (16, 16), (10, 20, 30)).save(source)
        result = image_cli("png2bmp", str(source))
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open(self.root / "plain.bmp") as written:
            self.assertEqual(written.format, "BMP")


if __name__ == "__main__":
    unittest.main()

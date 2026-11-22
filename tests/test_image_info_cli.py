"""Tests of ``jzr image info`` run through the real command line entry point."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from jzr_tools.core.media import format_size

runner = CliRunner()

DICT_KEYS = [
    "file", "format", "width", "height", "orientation", "upright_width",
    "upright_height", "mode", "file_size", "frames", "loop", "duration_ms",
    "transparency", "color_profile", "dpi", "camera", "taken", "software",
    "gps", "metadata",
]


def info_cli(*args: object):  # noqa: ANN201
    return runner.invoke(app, ["image", "info", *[str(a) for a in args]])


def gps_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15"
    ifd = exif.get_ifd(0x8825)
    ifd[1] = "S"
    ifd[2] = (33.0, 51.0, 54.0)
    ifd[3] = "W"
    ifd[4] = (151.0, 12.0, 30.0)
    return exif


class InfoCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def plain_png(self, name: str = "plain.png") -> Path:
        path = self.root / name
        Image.new("RGB", (40, 20)).save(path, "PNG")
        return path

    def photo(self, name: str = "photo.jpg") -> Path:
        path = self.root / name
        Image.new("RGB", (40, 20), (9, 9, 9)).save(path, "JPEG", exif=gps_exif())
        return path

    def junk(self, name: str = "junk.png") -> Path:
        path = self.root / name
        path.write_bytes(b"not an image " * 20)
        return path

    # ---- text layout

    def test_block_layout_and_label_padding(self) -> None:
        path = self.plain_png()
        result = info_cli(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                str(path),
                "  format     PNG",
                "  size       40x20",
                "  mode       RGB, 8 bits per channel",
                f"  file size  {format_size(os.path.getsize(path))}",
                "  frames     1",
            ],
        )
        self.assertEqual(result.stderr, "")

    def test_values_line_up_to_the_longest_label_in_the_block(self) -> None:
        result = info_cli(self.photo())
        self.assertEqual(result.exit_code, 0, result.output)
        rows = result.stdout.splitlines()[1:]
        self.assertTrue(rows)
        columns = {re.match(r"^  (.+?)  +(\S.*)$", row).start(2) for row in rows}
        self.assertEqual(len(columns), 1)
        self.assertEqual(columns.pop(), 2 + len("file size") + 2)

    def test_rows_in_order_with_exif_and_gps(self) -> None:
        result = info_cli(self.photo())
        labels = [line.split("  ")[1].strip() for line in result.stdout.splitlines()[1:]]
        self.assertEqual(
            labels,
            ["format", "size", "mode", "file size", "frames", "camera", "gps", "metadata"],
        )
        self.assertRegex(result.stdout, r"(?m)^  camera\s+Apple iPhone 15$")
        self.assertRegex(result.stdout, r"(?m)^  gps\s+-33\.865000, -151\.208333$")
        self.assertRegex(result.stdout, r"(?m)^  metadata\s+EXIF$")

    def test_blank_line_between_files_and_none_after_the_last(self) -> None:
        first, second = self.plain_png("a.png"), self.photo("b.jpg")
        result = info_cli(first, second)
        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.split("\n")
        self.assertEqual(lines[0], str(first))
        blank = lines.index("")
        self.assertEqual(lines[blank + 1], str(second))
        self.assertEqual(lines.count(""), 2)
        self.assertEqual(lines[-1], "")
        self.assertNotEqual(lines[-2], "")

    def test_padding_is_per_block(self) -> None:
        first, second = self.plain_png("a.png"), self.photo("b.jpg")
        lines = info_cli(first, second).stdout.split("\n")
        self.assertTrue(lines[1].startswith("  format     PNG"))
        later = lines[lines.index("") + 2]
        self.assertEqual(later, "  format     JPEG")

    def test_png_named_jpg_shows_png(self) -> None:
        path = self.root / "lies.jpg"
        Image.new("RGB", (10, 10)).save(path, "PNG")
        result = info_cli(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout, r"(?m)^  format\s+PNG$")

    def test_animated_gif_frames_row(self) -> None:
        path = self.root / "a.gif"
        frames = [Image.new("RGB", (4, 4), (i * 80, 0, 0)) for i in range(3)]
        frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=[100, 100, 200], loop=0)
        result = info_cli(path)
        self.assertRegex(result.stdout, r"(?m)^  frames\s+3 \(loops forever, 0\.4 s\)$")

    def test_heic(self) -> None:
        import pillow_heif

        pillow_heif.register_heif_opener()
        path = self.root / "shot.heic"
        Image.new("RGB", (64, 48)).save(path, "HEIF")
        result = info_cli(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout, r"(?m)^  format\s+HEIF$")
        self.assertRegex(result.stdout, r"(?m)^  size\s+64x48$")

    # ---- failures

    def test_junk_file_fails_alone_with_exit_one(self) -> None:
        junk, good = self.junk(), self.plain_png()
        result = info_cli(junk, good)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), f"fail {junk}: not a readable image")
        self.assertIn(str(good), result.stdout)
        self.assertNotIn(str(junk), result.stdout)
        self.assertRegex(result.stdout, r"(?m)^  format\s+PNG$")
        self.assertNotIn("Traceback", result.output)

    def test_junk_only(self) -> None:
        junk = self.junk()
        result = info_cli(junk)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr.strip(), f"fail {junk}: not a readable image")
        self.assertEqual(result.stdout.strip(), "")

    def test_failure_before_a_good_file_leaves_no_leading_blank_line(self) -> None:
        good = self.plain_png()
        result = info_cli(self.junk(), good)
        self.assertEqual(result.stdout.splitlines()[0], str(good))

    def test_missing_file_is_a_usage_error(self) -> None:
        result = info_cli(self.root / "nope.png")
        self.assertEqual(result.exit_code, 2)

    def test_no_arguments_is_a_usage_error(self) -> None:
        self.assertEqual(info_cli().exit_code, 2)

    def test_directory_is_a_usage_error(self) -> None:
        self.assertEqual(info_cli(self.root).exit_code, 2)

    # ---- json

    def test_json_is_one_array_with_ordered_keys(self) -> None:
        path = self.photo()
        result = info_cli("--json", path)
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(list(data[0]), DICT_KEYS)
        entry = data[0]
        self.assertEqual(entry["file"], str(path))
        self.assertEqual(entry["format"], "JPEG")
        self.assertEqual((entry["width"], entry["height"]), (40, 20))
        self.assertEqual(entry["file_size"], os.path.getsize(path))
        self.assertEqual(entry["camera"], "Apple iPhone 15")
        self.assertIsNone(entry["orientation"])
        self.assertIsNone(entry["loop"])
        self.assertAlmostEqual(entry["gps"]["latitude"], -33.865, places=6)
        self.assertAlmostEqual(entry["gps"]["longitude"], -151.2083333, places=6)
        self.assertEqual(entry["metadata"], ["EXIF"])

    def test_json_output_is_indented_by_two(self) -> None:
        result = info_cli("--json", self.plain_png())
        self.assertTrue(result.stdout.startswith("[\n  {\n    \"file\""))

    def test_json_has_one_array_for_several_files(self) -> None:
        first, second = self.plain_png("a.png"), self.photo("b.jpg")
        data = json.loads(info_cli("--json", first, second).stdout)
        self.assertEqual([entry["file"] for entry in data], [str(first), str(second)])

    def test_json_missing_values_are_null(self) -> None:
        entry = json.loads(info_cli("--json", self.plain_png()).stdout)[0]
        for key in ("orientation", "loop", "duration_ms", "color_profile", "dpi",
                    "camera", "taken", "software", "gps"):
            self.assertIsNone(entry[key], key)
        self.assertEqual(entry["metadata"], [])

    def test_json_with_a_failing_file_still_lists_the_others(self) -> None:
        junk, good = self.junk(), self.plain_png()
        result = info_cli("--json", junk, good)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual([entry["file"] for entry in json.loads(result.stdout)], [str(good)])
        self.assertEqual(result.stderr.strip(), f"fail {junk}: not a readable image")

    def test_json_with_only_failures_is_an_empty_array(self) -> None:
        result = info_cli("--json", self.junk())
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(json.loads(result.stdout), [])

    # ---- --no-gps

    def test_no_gps_hides_the_row(self) -> None:
        path = self.photo()
        shown = info_cli(path).stdout
        hidden = info_cli("--no-gps", path)
        self.assertEqual(hidden.exit_code, 0, hidden.output)
        self.assertIn("gps", shown)
        self.assertNotRegex(hidden.stdout, r"(?m)^  gps\b")
        self.assertNotIn("33.865", hidden.stdout)
        self.assertRegex(hidden.stdout, r"(?m)^  camera\s+Apple iPhone 15$")

    def test_no_gps_makes_json_null(self) -> None:
        entry = json.loads(info_cli("--json", "--no-gps", self.photo()).stdout)[0]
        self.assertIsNone(entry["gps"])
        self.assertEqual(list(entry), DICT_KEYS)
        self.assertEqual(entry["camera"], "Apple iPhone 15")

    # ---- help

    def test_help(self) -> None:
        result = runner.invoke(app, ["image", "info", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        flat = " ".join(result.stdout.split())
        self.assertIn("Show the format, size, colour profile and metadata of images.", flat)
        self.assertIn(
            "GPS positions are shown so you can check a photo before sharing it; "
            "--no-gps leaves them out. --json prints one array with the same facts.",
            flat,
        )
        self.assertIn("--json", flat)
        self.assertIn("Print one JSON array.", flat)
        self.assertIn("--no-gps", flat)
        self.assertIn("Leave the GPS position out.", flat)
        self.assertIn("FILES", flat)

    def test_listed_in_the_image_group_help(self) -> None:
        result = runner.invoke(app, ["image", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout, r"(?m)^\s+info\s+Show the format")


if __name__ == "__main__":
    unittest.main()

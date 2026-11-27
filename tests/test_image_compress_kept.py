"""`jzr image compress` says the output's dimensions when it did not resize.

`docs/features/agent-cost/spec.md`, round 3, change 6: a done line without a
resize ends its details with `kept WxH`, for example
`photo.jpg -> photo_small.jpg (3.2 MB -> 492.0 kB, -85%, quality 62, kept 4000x3000)`.
Lines that say `resized to WxH` (with or without `to fit`), skipped, failed
and planned lines are unchanged, and the size that the batch lines end with is
not added a second time.

The dimensions are read back from the written file, so a test cannot pass by
matching what the code under test claims.
"""

from __future__ import annotations

import os
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from jzr_tools.commands import _mcp_tools
from jzr_tools.core import image_compress as compress
from jzr_tools.core.media import format_size

runner = CliRunner()

SIZE = r"\d+(?:\.\d+)? (?:B|kB|MB)"


def noise(width: int, height: int, seed: int = 1) -> Image.Image:
    return Image.frombytes(
        "RGB", (width, height), random.Random(seed).randbytes(width * height * 3)
    )


def dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


class FolderCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-cmp-dims-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        config = mock.patch.dict(
            os.environ, {"JZR_CONFIG_DIR": str(self.root / ".config")}
        )
        config.start()
        self.addCleanup(config.stop)

    def photo(
        self, name: str = "photo.jpg", size: tuple[int, int] = (240, 180), **save
    ) -> Path:  # noqa: ANN003
        path = self.root / name
        options = {"quality": 95} if path.suffix == ".jpg" else {}
        noise(*size).save(path, **{**options, **save})
        return path

    def cli(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, ["image", "compress", *[str(arg) for arg in args]])

    def compress(self, source: Path, target: Path, **fields):  # noqa: ANN003, ANN201
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


class CoreDetailTests(FolderCase):
    def test_a_compressed_file_without_a_resize_ends_its_details_with_kept_and_the_output_size(
        self,
    ) -> None:
        source = self.photo()
        target = self.root / "photo_small.jpg"

        result = self.compress(source, target)

        self.assertEqual(result.status, "compressed", result.detail)
        width, height = dimensions(target)
        self.assertEqual((width, height), (240, 180))
        self.assertTrue(
            result.detail.endswith(f", kept {width}x{height}"), result.detail
        )
        before, after = source.stat().st_size, target.stat().st_size
        self.assertTrue(
            result.detail.startswith(
                f"{format_size(before)} -> {format_size(after)}, -"
            ),
            result.detail,
        )

    def test_kept_follows_the_quality_item_of_a_size_limit_that_needed_no_resize(
        self,
    ) -> None:
        source = self.photo()
        target = self.root / "photo_small.jpg"
        # Smaller than the source, larger than the picture at the lowest quality tried.
        limit = source.stat().st_size * 6 // 10

        result = self.compress(source, target, size_limit=limit)

        self.assertEqual(result.status, "compressed", result.detail)
        self.assertEqual(dimensions(target), (240, 180))
        self.assertRegex(result.detail, r", quality \d+, kept 240x180$")
        self.assertNotIn("resized", result.detail)

    def test_the_dimensions_are_those_of_the_written_file_for_a_turned_picture(
        self,
    ) -> None:
        source = self.root / "turned.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6  # shown turned a quarter: the upright picture is 20x40
        noise(40, 20).save(source, exif=exif, quality=95)
        target = self.root / "turned_small.jpg"

        result = self.compress(source, target, quality=40)

        self.assertEqual(result.status, "compressed", result.detail)
        width, height = dimensions(target)
        self.assertEqual((width, height), (20, 40))
        self.assertTrue(
            result.detail.endswith(f", kept {width}x{height}"), result.detail
        )

    def test_a_format_change_to_a_larger_file_still_ends_with_kept(self) -> None:
        source = self.photo(quality=30)
        target = self.root / "photo_small.png"

        result = self.compress(source, target)

        self.assertEqual(result.status, "compressed", result.detail)
        self.assertIn("larger than the source", result.detail)
        self.assertTrue(result.detail.endswith(", kept 240x180"), result.detail)

    def test_a_resize_by_max_side_says_resized_and_not_kept(self) -> None:
        source = self.photo()

        result = self.compress(source, self.root / "photo_small.jpg", max_side=120)

        self.assertEqual(result.status, "compressed", result.detail)
        self.assertTrue(result.detail.endswith(", resized to 120x90"), result.detail)
        self.assertNotIn("kept", result.detail)

    def test_a_shrink_to_fit_says_resized_to_fit_and_not_kept(self) -> None:
        source = self.photo()
        target = self.root / "photo_small.jpg"

        result = self.compress(source, target, size_limit=1500)

        self.assertEqual(result.status, "compressed", result.detail)
        width, height = dimensions(target)
        self.assertLess(width, 240)
        self.assertTrue(
            result.detail.endswith(f", resized to {width}x{height} to fit"),
            result.detail,
        )
        self.assertNotIn("kept", result.detail)

    def test_a_max_side_larger_than_the_picture_is_not_a_resize_so_it_is_kept(
        self,
    ) -> None:
        source = self.photo()

        result = self.compress(source, self.root / "photo_small.jpg", max_side=1000)

        self.assertEqual(result.status, "compressed", result.detail)
        self.assertTrue(result.detail.endswith(", kept 240x180"), result.detail)
        self.assertNotIn("resized", result.detail)

    def test_skipped_failed_and_planned_results_do_not_mention_kept(self) -> None:
        small = self.photo("small.jpg", quality=20)
        big = self.photo("big.jpg")

        skipped = self.compress(small, self.root / "small_c.jpg")
        failed = self.compress(big, self.root / "big_c.jpg", size_limit=100)
        planned = self.compress(big, self.root / "plan.jpg", dry_run=True)

        self.assertEqual(
            (skipped.status, failed.status, planned.status),
            ("skipped", "failed", "planned"),
        )
        for result in (skipped, failed, planned):
            self.assertNotIn("kept", result.detail)
        self.assertEqual(planned.detail, "")


class CliLineTests(FolderCase):
    def test_the_done_line_ends_with_kept_and_names_the_size_only_once(self) -> None:
        source = self.photo()

        result = self.cli(source, "--quality", "40")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "photo_compressed.jpg"
        self.assertEqual(dimensions(target), (240, 180))
        line = result.stdout.splitlines()[0]
        self.assertRegex(
            line,
            rf"^{re.escape(str(source))} -> {re.escape(str(target))} "
            rf"\(({SIZE}) -> ({SIZE}), -\d+%, kept 240x180\)$",
        )
        self.assertEqual(len(re.findall(SIZE, line)), 2, line)
        self.assertEqual(
            re.search(rf"-> ({SIZE})", line).group(1),
            format_size(target.stat().st_size),
        )
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 compressed")

    def test_a_size_limit_line_keeps_quality_then_kept(self) -> None:
        source = self.photo()
        limit = source.stat().st_size * 6 // 10

        result = self.cli(source, "--size", limit)

        self.assertEqual(result.exit_code, 0, result.output)
        line = result.stdout.splitlines()[0]
        self.assertRegex(line, r", quality \d+, kept 240x180\)$")
        self.assertEqual(len(re.findall(SIZE, line)), 2, line)

    def test_the_line_names_each_files_own_dimensions(self) -> None:
        wide = self.photo("wide.jpg", size=(240, 100))
        tall = self.photo("tall.jpg", size=(100, 240))

        result = self.cli(wide, tall)

        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        self.assertTrue(
            lines[0].startswith(str(wide)) and lines[0].endswith(", kept 240x100)"),
            lines,
        )
        self.assertTrue(
            lines[1].startswith(str(tall)) and lines[1].endswith(", kept 100x240)"),
            lines,
        )

    def test_a_resized_line_is_unchanged(self) -> None:
        source = self.photo()

        result = self.cli(source, "--max-side", "120")

        self.assertEqual(result.exit_code, 0, result.output)
        line = result.stdout.splitlines()[0]
        self.assertRegex(line, rf"\({SIZE} -> {SIZE}, -\d+%, resized to 120x90\)$")
        self.assertNotIn("kept", line)

    def test_a_resized_to_fit_line_is_unchanged(self) -> None:
        source = self.photo()

        result = self.cli(source, "--size", "1500")

        self.assertEqual(result.exit_code, 0, result.output)
        line = result.stdout.splitlines()[0]
        self.assertRegex(line, r", quality \d+, resized to \d+x\d+ to fit\)$")
        self.assertNotIn("kept", line)

    def test_dry_run_skipped_and_failed_lines_are_unchanged(self) -> None:
        big = self.photo("big.jpg")
        small = self.photo("small.jpg", quality=20)

        planned = self.cli(big, "--dry-run")
        skipped = self.cli(small)
        failed = self.cli(big, "--size", "100B")

        self.assertEqual(
            planned.stdout.splitlines()[0],
            f"{big} -> {self.root / 'big_compressed.jpg'}",
        )
        self.assertIn(f"skip {small}: already small", skipped.stderr)
        self.assertRegex(
            failed.stderr, r"^fail .*big\.jpg: could not get under 100 B; "
        )
        for result in (planned, skipped, failed):
            self.assertNotIn("kept", result.output)


class McpToolTextTests(FolderCase):
    def test_the_image_compress_tool_text_has_the_kept_dimensions(self) -> None:
        source = self.photo()

        result = _mcp_tools.call(
            "image_compress", {"sources": [str(source)], "quality": 40}
        )

        self.assertFalse(result.is_error, result.text)
        target = self.root / "photo_compressed.jpg"
        self.assertEqual(dimensions(target), (240, 180))
        self.assertRegex(
            result.text,
            rf"{re.escape(str(source))} -> {re.escape(str(target))} "
            rf"\({SIZE} -> {SIZE}, -\d+%, kept 240x180\)\n",
        )


if __name__ == "__main__":
    unittest.main()

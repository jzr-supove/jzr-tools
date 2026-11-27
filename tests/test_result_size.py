"""Result lines for written files end with the size of the file.

`docs/features/agent-cost/spec.md`, change 2: the shared result line of the
batch commands (image, video and audio), the `pdf organize` and `pdf split`
lines and the `wrote ...` line of `jzr data convert` end with the size of what
was written, as `jzr_tools.core.media.format_size` writes it. Compress commands
already show the target's size in their details and do not repeat it; dry runs,
skips, failures and the MCP tool results follow.

The expected size always comes from the file on disk (see size_fixtures), and
the stand-in tools of the CLI tests write a known number of bytes, so a test
cannot pass by matching a size the code under test made up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from jzr_tools.commands import _mcp_tools

from size_fixtures import size_text
from test_gif_cli import GifCase
from test_media_cli import describe
from test_video_compress_cli import CompressCase
from tests import pdf_fixtures as pdf_fx

runner = CliRunner()

SIZE = r"\d+(?:\.\d)? (?:B|kB|MB)"


def as_decimal(size: int) -> str:
    """A size in the way the spec writes it, worked out here and not by jzr's own function."""
    assert 1000 <= size < 999_950, size
    return f"{size / 1000:.1f} kB"


def noise_png(path: Path, side: int = 64, seed: int = 1) -> Path:
    """A picture of random pixels, so that its copies are a few kilobytes."""
    data = random.Random(seed).randbytes(side * side * 3)
    Image.frombytes("RGB", (side, side), data).save(path)
    return path


class FolderCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-result-size-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        config = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.root / ".config")})
        config.start()
        self.addCleanup(config.stop)

    def invoke(self, *args: object):  # noqa: ANN201
        return runner.invoke(app, [str(arg) for arg in args])


class ImageResultSizeTests(FolderCase):
    def test_a_converted_image_line_ends_with_the_size_of_the_new_file(self) -> None:
        source = noise_png(self.root / "photo.png")

        result = self.invoke("image", "convert", source, "--to", "jpg")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "photo.jpg"
        self.assertEqual(
            result.stdout.splitlines(),
            [f"{source} -> {target} ({size_text(target)})", "pillow: 1 converted"],
        )

    def test_the_size_is_in_decimal_units_such_as_kilobytes(self) -> None:
        source = noise_png(self.root / "photo.png")

        result = self.invoke("image", "convert", source, "--to", "png", "--outdir", self.root / "out")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "out" / "photo.png"
        written = target.stat().st_size
        self.assertGreaterEqual(written, 1000, "the picture must be big enough to need a unit")
        self.assertEqual(result.stdout.splitlines()[0], f"{source} -> {target} ({as_decimal(written)})")

    def test_each_line_has_the_size_of_its_own_target(self) -> None:
        small = noise_png(self.root / "small.png", side=16, seed=2)
        large = noise_png(self.root / "large.png", side=96, seed=3)

        result = self.invoke("image", "convert", small, large, "--to", "jpg")

        self.assertEqual(result.exit_code, 0, result.output)
        sizes = [(self.root / "small.jpg").stat().st_size, (self.root / "large.jpg").stat().st_size]
        self.assertNotEqual(sizes[0], sizes[1], "the two files must differ for this test to mean anything")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"{small} -> {self.root / 'small.jpg'} ({size_text(self.root / 'small.jpg')})")
        self.assertEqual(lines[1], f"{large} -> {self.root / 'large.jpg'} ({size_text(self.root / 'large.jpg')})")

    def test_the_size_is_the_target_and_not_the_source_when_output_names_the_file(self) -> None:
        source = noise_png(self.root / "photo.png", side=96)

        result = self.invoke("image", "convert", source, "-o", self.root / "tiny.jpg", "--quality", "5")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "tiny.jpg"
        self.assertLess(target.stat().st_size, source.stat().st_size)
        self.assertEqual(result.stdout.splitlines()[0], f"{source} -> {target} ({size_text(target)})")

    def test_a_resize_line_keeps_its_details_and_adds_the_size_last(self) -> None:
        source = noise_png(self.root / "photo.png", side=64)

        result = self.invoke("image", "resize", source, "--width", "32")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "photo_32x32.png"
        self.assertEqual(
            result.stdout.splitlines()[0],
            f"{source} -> {target} (64x64 -> 32x32, {size_text(target)})",
        )

    def test_a_dry_run_names_no_size_because_nothing_is_written(self) -> None:
        source = noise_png(self.root / "photo.png")

        result = self.invoke("image", "convert", source, "--to", "jpg", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(), [f"{source} -> {self.root / 'photo.jpg'}", "pillow: 1 planned"]
        )
        self.assertFalse((self.root / "photo.jpg").exists())

    def test_a_skipped_file_and_a_failed_file_keep_their_lines(self) -> None:
        source = noise_png(self.root / "photo.png")
        existing = self.root / "photo.jpg"
        existing.write_bytes(b"already here")
        broken = self.root / "broken.png"
        broken.write_bytes(b"not a picture")

        skipped = self.invoke("image", "convert", source, "--to", "jpg")
        failed = self.invoke("image", "convert", broken, "--to", "jpg")

        self.assertEqual(skipped.stderr.splitlines()[0], f"skip {source}: target exists (use --overwrite)")
        self.assertEqual(skipped.stdout.splitlines(), ["pillow: 1 skipped"])
        self.assertEqual(failed.exit_code, 1, failed.output)
        self.assertTrue(failed.stderr.startswith(f"fail {broken}: "), failed.stderr)
        self.assertNotRegex(failed.stderr.splitlines()[0], rf", {SIZE}\)$")
        self.assertEqual(failed.stdout.splitlines(), ["pillow: 1 failed"])
        self.assertEqual(existing.read_bytes(), b"already here")

    def test_a_replaced_file_shows_the_size_of_the_new_one(self) -> None:
        source = noise_png(self.root / "photo.png")
        (self.root / "photo.jpg").write_bytes(b"old")

        result = self.invoke("image", "convert", source, "--to", "jpg", "--overwrite")

        target = self.root / "photo.jpg"
        self.assertNotEqual(target.read_bytes(), b"old")
        self.assertEqual(result.stdout.splitlines()[0], f"{source} -> {target} ({size_text(target)})")


class CompressDoesNotRepeatTheSizeTests(FolderCase):
    def test_an_image_compress_line_ends_with_its_own_change_and_not_a_second_size(self) -> None:
        source = noise_png(self.root / "photo.png", side=96)

        result = self.invoke("image", "compress", source, "--to", "jpg", "--quality", "20")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "photo_compressed.jpg"
        line = result.stdout.splitlines()[0]
        match = re.fullmatch(
            rf"{re.escape(str(source))} -> {re.escape(str(target))} \(({SIZE}) -> ({SIZE}), [^,)]+, kept 96x96\)", line
        )
        self.assertIsNotNone(match, line)
        # The details already say how big the target is; it is that size and it is not said again.
        self.assertEqual(match.group(2), size_text(target))

    def test_a_pdf_compress_line_ends_with_its_own_change_and_not_a_second_size(self) -> None:
        # A page with a big picture, so that compressing it does something.
        picture = noise_png(self.root / "scan.png", side=200)
        built = self.invoke("pdf", "from-images", picture, "-o", self.root / "scan.pdf")
        self.assertEqual(built.exit_code, 0, built.output)
        source = self.root / "scan.pdf"

        result = self.invoke("pdf", "compress", source, "--level", "strong")

        self.assertEqual(result.exit_code, 0, result.output)
        line = result.stdout.splitlines()[0]
        target = self.root / "scan_compressed.pdf"
        match = re.fullmatch(
            rf"{re.escape(str(source))} -> {re.escape(str(target))} \(({SIZE}) -> ({SIZE}), [^,)]+(?:; [^,)]+)?\)",
            line,
        )
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group(2), size_text(target))


class VideoCompressDoesNotRepeatTheSizeTests(CompressCase):
    def test_the_stand_in_compress_line_names_the_size_once_and_ends_with_the_old_size(self) -> None:
        # The stand-in ffmpeg writes the nine bytes of "converted"; the probe says the source is 30 MB.
        (clip,) = self.touch("clip.mov")
        target = self.dir / "clip_compressed.mp4"

        result = self.invoke("compress", clip)

        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [text for text in result.stdout.splitlines() if text.startswith(f"{clip} -> ")]
        self.assertEqual(size_text(target), "9 B")
        self.assertRegex(line, r"; 9 B, was \d+\.\d MB\)$")
        self.assertEqual(line.count("9 B"), 1, line)

    def test_a_target_size_line_names_the_size_once_too(self) -> None:
        (clip,) = self.touch_big("clip.mov")

        result = self.invoke("compress", clip, "--size", "5MB")

        self.assertEqual(result.exit_code, 0, describe(result))
        (line,) = [text for text in result.stdout.splitlines() if text.startswith(f"{clip} -> ")]
        self.assertRegex(line, r"; 9 B of 5\.0 MB\)$")
        self.assertEqual(line.count("9 B"), 1, line)


class PdfResultSizeTests(FolderCase):
    def put(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_split_lines_end_with_the_size_of_each_file_they_name(self) -> None:
        source = self.put("a.pdf", pdf_fx.marked(6))

        result = self.invoke("pdf", "split", source, "--pages", "1;2-6")

        self.assertEqual(result.exit_code, 0, result.output)
        first, second = self.root / "a_split-001.pdf", self.root / "a_split-002.pdf"
        self.assertNotEqual(first.stat().st_size, second.stat().st_size, "one page and five pages must differ")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"{source} -> {first} (page 1, {size_text(first)})",
                f"{source} -> {second} (pages 2-6, {size_text(second)})",
                "pdf: 2 written",
            ],
        )

    def test_organize_line_ends_with_the_size_after_the_details(self) -> None:
        source = self.put("a.pdf", pdf_fx.marked(5))

        result = self.invoke("pdf", "organize", source, "--delete", "2", "--rotate", "3:90")

        self.assertEqual(result.exit_code, 0, result.output)
        target = self.root / "a_organized.pdf"
        self.assertEqual(
            result.stdout.splitlines(),
            [f"{source} -> {target} (4 pages; 1 rotated, 1 removed, {size_text(target)})", "pdf: 1 organized"],
        )

    def test_a_dry_run_of_either_writes_nothing_and_names_no_size(self) -> None:
        source = self.put("a.pdf", pdf_fx.marked(4))

        split = self.invoke("pdf", "split", source, "--every", "2", "--dry-run")
        organize = self.invoke("pdf", "organize", source, "--delete", "1", "--dry-run")

        self.assertEqual(
            split.stdout.splitlines(),
            [
                f"{source} -> {self.root / 'a_split-001.pdf'} (pages 1-2)",
                f"{source} -> {self.root / 'a_split-002.pdf'} (pages 3-4)",
                "pdf: 2 planned",
            ],
        )
        self.assertEqual(
            organize.stdout.splitlines(),
            [f"{source} -> {self.root / 'a_organized.pdf'} (3 pages; 1 removed)", "pdf: 1 planned"],
        )
        self.assertEqual(sorted(p.name for p in self.root.glob("*.pdf")), ["a.pdf"])

    def test_a_skipped_organize_and_a_refused_split_keep_their_lines(self) -> None:
        source = self.put("a.pdf", pdf_fx.marked(3))
        self.put("a_organized.pdf", b"already here")
        self.put("a_split-001.pdf", b"already here")

        organize = self.invoke("pdf", "organize", source, "--delete", "1")
        split = self.invoke("pdf", "split", source)

        self.assertEqual(
            organize.stderr.splitlines(),
            [f"skip {self.root / 'a_organized.pdf'}: target exists (use --overwrite)"],
        )
        self.assertEqual(
            split.stderr.splitlines(),
            [f"fail {source}: {self.root / 'a_split-001.pdf'} exists (use --overwrite)"],
        )
        self.assertEqual(split.stdout.splitlines(), ["pdf: 1 failed"])


class DataConvertResultSizeTests(FolderCase):
    def test_the_wrote_line_ends_with_the_size_of_the_file(self) -> None:
        source = self.root / "a.json"
        source.write_text('[{"a": 1, "b": "x"}]', encoding="utf-8")

        result = self.invoke("data", "convert", source, "-o", self.root / "out.yaml")

        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "out.yaml"
        self.assertEqual(out.read_bytes(), b"- a: 1\n  b: x\n")
        self.assertEqual(result.stderr.splitlines(), [f"wrote {out} (14 B)"])
        self.assertEqual(result.stdout, "")

    def test_a_big_enough_file_is_written_in_kilobytes_and_the_summary_stays_first(self) -> None:
        source = self.root / "rows.json"
        source.write_text(json.dumps([{"n": n, "text": "row " * 5} for n in range(120)]), encoding="utf-8")
        out = self.root / "rows.csv"

        result = self.invoke("data", "convert", source, "-o", out)

        self.assertEqual(result.exit_code, 0, result.output)
        written = out.stat().st_size
        self.assertEqual(result.stderr.splitlines(), [f"wrote {out} (120 rows, {as_decimal(written)})"])

    def test_notes_still_follow_the_wrote_line(self) -> None:
        source = self.root / "a.csv"
        source.write_text("a,b\n1,2\n", encoding="utf-8")
        out = self.root / "out.json"

        result = self.invoke("data", "convert", source, "-o", out)

        lines = result.stderr.splitlines()
        self.assertEqual(lines[0], f"wrote {out} ({size_text(out)})")
        self.assertTrue(lines[1].startswith("note: "), lines)
        self.assertEqual(len(lines), 2)

    def test_text_sent_to_stdout_has_no_wrote_line_and_no_size(self) -> None:
        source = self.root / "a.json"
        source.write_text('[{"a": 1}]', encoding="utf-8")

        result = self.invoke("data", "convert", source, "--to", "yaml")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout, "- a: 1\n")
        self.assertEqual(result.stderr, "")


class McpToolResultTests(FolderCase):
    """The MCP tools hand back the text the command printed, so they carry the size too."""

    def test_the_image_convert_tool_text_has_the_size_of_the_new_file(self) -> None:
        source = noise_png(self.root / "photo.png")

        result = _mcp_tools.call("image_convert", {"sources": [str(source)], "to": "jpg"})

        self.assertFalse(result.is_error, result.text)
        target = self.root / "photo.jpg"
        self.assertIn(f"{source} -> {target} ({size_text(target)})\n", result.text)

    def test_a_tool_that_would_only_plan_names_no_size(self) -> None:
        source = noise_png(self.root / "photo.png")

        result = _mcp_tools.call("image_convert", {"sources": [str(source)], "to": "jpg", "dry_run": True})

        self.assertFalse(result.is_error, result.text)
        self.assertIn(f"{source} -> {self.root / 'photo.jpg'}\n", result.text)
        self.assertNotRegex(result.text, r"photo\.jpg \(")


class McpGifToolResultTests(GifCase):
    """The motivating case: after `video_gif` the caller can see the GIF's size and need not look."""

    def test_the_video_gif_tool_text_ends_the_line_with_the_size_of_the_gif(self) -> None:
        (clip,) = self.touch("clip.mp4")
        environ = {**os.environ, "JZR_FFMPEG": self.ffmpeg, "JZR_FFPROBE": self.ffprobe}

        with mock.patch.dict(os.environ, environ, clear=True):
            result = _mcp_tools.call("video_gif", {"sources": [str(clip)]})

        self.assertFalse(result.is_error, result.text)
        target = self.dir / "clip.gif"
        # The stand-in ffmpeg writes the seven bytes of "trimmed".
        self.assertEqual(size_text(target), "7 B")
        self.assertIn(f"{clip} -> {target} (64x48, 15 fps, 7 B)\n", result.text)


if __name__ == "__main__":
    unittest.main()

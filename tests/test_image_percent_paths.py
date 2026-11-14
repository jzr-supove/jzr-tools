"""Acceptance tests: a literal `%` in image paths (ImageMagick backend).

`%` in a source, output name, or directory must be treated as an ordinary
character: output lands at exactly the expected path and nothing else appears.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from click.testing import CliRunner

from jzr_tools.cli import app
from tests.test_images import (
    HAS_MAGICK,
    ONE_PIXEL_PNG,
    combined,
    three_frame_gif,
)

runner = CliRunner()

JPEG_MAGIC = b"\xff\xd8"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGIC = b"GIF8"


def listing(root: Path) -> list[str]:
    """Every file and directory under root, relative and sorted."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def frame_count(png: Path, ext: str = "png") -> int:
    """Count frames of an image by copying it to a plain name first."""
    with tempfile.TemporaryDirectory() as plain:
        copy = Path(plain) / f"copy.{ext}"
        copy.write_bytes(png.read_bytes())
        out = subprocess.run(
            ["magick", "identify", str(copy)], capture_output=True, text=True, check=True
        )
    return len([line for line in out.stdout.splitlines() if line.strip()])


def convert(args: list[str], backend: str = "magick"):
    return runner.invoke(app, ["image", *args, "-b", backend])


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class PercentFileNameTests(unittest.TestCase):
    NAMES = [
        "100%d",
        "50%",
        "shot%03d",
        "%d0%bf%d1%80",
        "%x%o",
        "%%",
        "a%dz%d",
    ]

    def test_png_to_jpg_writes_exactly_one_literal_output(self) -> None:
        for stem in self.NAMES:
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.png").write_bytes(ONE_PIXEL_PNG)

                result = convert(["png2jpg", str(root / f"{stem}.png")])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(listing(root), sorted([f"{stem}.jpg", f"{stem}.png"]))
                self.assertEqual((root / f"{stem}.jpg").read_bytes()[:2], JPEG_MAGIC)

    def test_outdir_receives_literal_output_and_source_dir_is_untouched(self) -> None:
        for stem in self.NAMES:
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "src"
                out = Path(tmp) / "out"
                root.mkdir()
                (root / f"{stem}.png").write_bytes(ONE_PIXEL_PNG)

                result = convert(["png2jpg", str(root / f"{stem}.png"), "--outdir", str(out)])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(listing(root), [f"{stem}.png"])
                self.assertEqual(listing(out), [f"{stem}.jpg"])
                self.assertEqual((out / f"{stem}.jpg").read_bytes()[:2], JPEG_MAGIC)

    def test_extensionless_source_with_percent_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(root / "100%d")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["100%d.jpg", "100%d.png"])

    def test_quality_option_with_percent_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(root / "100%d.png"), "-q", "80"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["100%d.jpg", "100%d.png"])

    def test_convert_command_with_percent_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shot%03d.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["convert", "-t", "bmp", str(root / "shot%03d.png")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["shot%03d.bmp", "shot%03d.png"])
            self.assertEqual((root / "shot%03d.bmp").read_bytes()[:2], b"BM")


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class PercentDirectoryTests(unittest.TestCase):
    def test_source_directory_with_percent_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "d%d"
            src.mkdir()
            (src / "plain.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(src / "plain.png")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["d%d", "d%d/plain.jpg", "d%d/plain.png"])
            self.assertEqual((src / "plain.jpg").read_bytes()[:2], JPEG_MAGIC)

    def test_source_directory_with_percent_and_percent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "%d0%bf"
            src.mkdir()
            (src / "50%.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(src / "50%.png")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                listing(root), ["%d0%bf", "%d0%bf/50%.jpg", "%d0%bf/50%.png"]
            )

    def test_existing_outdir_with_percent_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plain.png").write_bytes(ONE_PIXEL_PNG)
            (root / "d%d").mkdir()

            result = convert(["png2jpg", str(root / "plain.png"), "--outdir", str(root / "d%d")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["d%d", "d%d/plain.jpg", "plain.png"])
            self.assertEqual((root / "d%d" / "plain.jpg").read_bytes()[:2], JPEG_MAGIC)

    def test_new_outdir_with_percent_d_is_created_literally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plain.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(root / "plain.png"), "--outdir", str(root / "d%d")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["d%d", "d%d/plain.jpg", "plain.png"])

    def test_outdir_and_file_name_both_contain_percent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", str(root / "100%d.png"), "--outdir", str(root / "o%d")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["100%d.png", "o%d", "o%d/100%d.jpg"])

    def test_relative_source_when_cwd_path_contains_percent_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cwd%d"
            root.mkdir()
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)
            original = os.getcwd()
            os.chdir(root)
            try:
                result = convert(["png2jpg", "100%d.png"])
                files = listing(root)
            finally:
                os.chdir(original)

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(files, ["100%d.jpg", "100%d.png"])
            self.assertEqual(listing(Path(tmp)), ["cwd%d", "cwd%d/100%d.jpg", "cwd%d/100%d.png"])

    def test_relative_source_and_outdir_when_cwd_contains_percent_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cwd%d"
            root.mkdir()
            (root / "plain.png").write_bytes(ONE_PIXEL_PNG)
            original = os.getcwd()
            os.chdir(root)
            try:
                result = convert(["png2jpg", "plain.png", "--outdir", "out"])
                files = listing(root)
            finally:
                os.chdir(original)

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(files, ["out", "out/plain.jpg", "plain.png"])


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class PercentDirectoryScanTests(unittest.TestCase):
    def test_dir_scan_with_percent_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scan%d"
            root.mkdir()
            for name in ("100%d.png", "50%.png", "%d0%bf%d1%80.png", "plain.png"):
                (root / name).write_bytes(ONE_PIXEL_PNG)

            result = convert(["png2jpg", "-d", str(root)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                listing(root),
                sorted(
                    [
                        "100%d.png", "100%d.jpg",
                        "50%.png", "50%.jpg",
                        "%d0%bf%d1%80.png", "%d0%bf%d1%80.jpg",
                        "plain.png", "plain.jpg",
                    ]
                ),
            )
            self.assertIn("4 converted", combined(result))

    def test_dir_scan_without_extension_filter_uses_convert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = convert(["convert", "-t", "jpg", "-d", str(root)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["100%d.jpg", "100%d.png"])

    def test_recursive_scan_with_outdir_mirrors_tree_with_percent_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "in%d"
            (root / "sub%d").mkdir(parents=True)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)
            (root / "sub%d" / "50%.png").write_bytes(ONE_PIXEL_PNG)
            out = base / "out%d"

            result = convert(["png2jpg", "-d", str(root), "-r", "--outdir", str(out)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                listing(base),
                sorted(
                    [
                        "in%d", "in%d/100%d.png", "in%d/sub%d", "in%d/sub%d/50%.png",
                        "out%d", "out%d/100%d.jpg", "out%d/sub%d", "out%d/sub%d/50%.jpg",
                    ]
                ),
            )
            self.assertEqual((out / "sub%d" / "50%.jpg").read_bytes()[:2], JPEG_MAGIC)

    def test_scan_of_current_directory_when_cwd_contains_percent_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cwd%d"
            root.mkdir()
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)
            original = os.getcwd()
            os.chdir(root)
            try:
                result = convert(["png2jpg"])
                files = listing(root)
            finally:
                os.chdir(original)

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(files, ["100%d.jpg", "100%d.png"])


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class PercentMultiFrameTests(unittest.TestCase):
    def test_animated_gif_with_percent_d_yields_one_single_frame_png(self) -> None:
        for stem in ("anim%d", "anim%03d", "%d0%bf"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.gif").write_bytes(three_frame_gif())

                result = convert(["gif2png", str(root / f"{stem}.gif")])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(listing(root), sorted([f"{stem}.gif", f"{stem}.png"]))
                png = root / f"{stem}.png"
                self.assertEqual(png.read_bytes()[:8], PNG_MAGIC)
                self.assertEqual(frame_count(png), 1)

    def test_animated_gif_in_percent_directory_to_outdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "d%d").mkdir()
            (root / "d%d" / "anim.gif").write_bytes(three_frame_gif())

            result = convert(["gif2png", str(root / "d%d" / "anim.gif"), "--outdir", str(root / "o%d")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                listing(root), ["d%d", "d%d/anim.gif", "o%d", "o%d/anim.png"]
            )
            self.assertEqual(frame_count(root / "o%d" / "anim.png"), 1)

    def test_animated_target_with_percent_d_is_a_single_gif(self) -> None:
        for stem in ("100%d", "%d0%bf", "shot%03d"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.png").write_bytes(ONE_PIXEL_PNG)

                result = convert(["png2gif", str(root / f"{stem}.png")])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(listing(root), sorted([f"{stem}.gif", f"{stem}.png"]))
                self.assertEqual((root / f"{stem}.gif").read_bytes()[:4], GIF_MAGIC)

    def test_animated_source_to_animated_target_is_one_webp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "anim%d.gif").write_bytes(three_frame_gif())

            result = convert(["gif2webp", str(root / "anim%d.gif")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), ["anim%d.gif", "anim%d.webp"])
            data = (root / "anim%d.webp").read_bytes()
            self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP"))


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class PercentFailureAndExistingTargetTests(unittest.TestCase):
    def test_corrupt_source_fails_and_leaves_nothing_behind(self) -> None:
        for stem in ("100%d", "50%", "%d0%bf%d1%80"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.png").write_bytes(b"this is not an image")

                result = convert(["png2jpg", str(root / f"{stem}.png")])

                self.assertEqual(result.exit_code, 1, combined(result))
                self.assertIn("fail", combined(result))
                self.assertEqual(listing(root), [f"{stem}.png"])

    def test_corrupt_source_with_outdir_leaves_no_output_or_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(b"this is not an image")
            (root / "o%d").mkdir()

            result = convert(["png2jpg", str(root / "100%d.png"), "--outdir", str(root / "o%d")])

            self.assertEqual(result.exit_code, 1, combined(result))
            self.assertIn("fail", combined(result))
            self.assertEqual(listing(root), ["100%d.png", "o%d"])

    def test_corrupt_gif_source_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "anim%d.gif").write_bytes(b"GIF89a-truncated-garbage")

            result = convert(["gif2png", str(root / "anim%d.gif")])

            self.assertEqual(result.exit_code, 1, combined(result))
            self.assertIn("fail", combined(result))
            self.assertEqual(listing(root), ["anim%d.gif"])

    def test_existing_target_is_skipped_without_overwrite(self) -> None:
        for stem in ("100%d", "50%", "%d0%bf"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.png").write_bytes(ONE_PIXEL_PNG)
                (root / f"{stem}.jpg").write_bytes(b"sentinel")

                result = convert(["png2jpg", str(root / f"{stem}.png")])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertIn("skip", combined(result))
                self.assertEqual((root / f"{stem}.jpg").read_bytes(), b"sentinel")
                self.assertEqual(listing(root), sorted([f"{stem}.jpg", f"{stem}.png"]))

    def test_existing_target_is_replaced_with_overwrite(self) -> None:
        for stem in ("100%d", "50%", "%d0%bf"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / f"{stem}.png").write_bytes(ONE_PIXEL_PNG)
                (root / f"{stem}.jpg").write_bytes(b"sentinel")

                result = convert(["png2jpg", str(root / f"{stem}.png"), "--overwrite"])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertNotIn("skip", combined(result))
                self.assertEqual((root / f"{stem}.jpg").read_bytes()[:2], JPEG_MAGIC)
                self.assertEqual(listing(root), sorted([f"{stem}.jpg", f"{stem}.png"]))

    def test_overwrite_failure_with_percent_name_keeps_nothing_extra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(b"this is not an image")

            result = convert(["png2jpg", str(root / "100%d.png"), "--overwrite"])

            self.assertEqual(result.exit_code, 1, combined(result))
            self.assertEqual(listing(root), ["100%d.png"])

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)
            (root / "50%.png").write_bytes(ONE_PIXEL_PNG)
            before = listing(root)

            result = convert(
                ["png2jpg", str(root / "100%d.png"), str(root / "50%.png"),
                 "--outdir", str(root / "o%d"), "--dry-run"]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), before)

    def test_dry_run_in_scan_mode_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)
            before = listing(root)

            result = convert(["png2jpg", "-d", str(root), "--dry-run"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(listing(root), before)


if __name__ == "__main__":
    unittest.main()

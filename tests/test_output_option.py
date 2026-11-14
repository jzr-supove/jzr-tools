"""Behavior tests for `-o/--output FILE` on the image batch commands.

`--output` names the single output file for exactly one listed source;
`--outdir` is the long-only option for an output directory.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app

runner = CliRunner()


def run(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", *args, "--backend", "pillow"])


def combined(result: object) -> str:
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{result.stdout}{stderr}"


def make_image(path: Path, fmt: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 30, 30)).save(path, fmt)
    return path


def listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def format_of(path: Path) -> str:
    with Image.open(path) as image:
        return image.format


class OutputFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = make_image(self.root / "a.png")

    def test_short_option_writes_exactly_that_file(self) -> None:
        target = self.root / "b.jpg"
        result = run("convert", str(self.source), "--to", "jpg", "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")
        self.assertEqual(listing(self.root), ["a.png", "b.jpg"])

    def test_long_option_writes_exactly_that_file(self) -> None:
        target = self.root / "renamed.jpg"
        result = run("convert", str(self.source), "--to", "jpg", "--output", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")
        self.assertEqual(listing(self.root), ["a.png", "renamed.jpg"])

    def test_pair_command_writes_exactly_that_file(self) -> None:
        target = self.root / "b.jpg"
        result = run("png2jpg", str(self.source), "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")
        self.assertEqual(listing(self.root), ["a.png", "b.jpg"])

    def test_missing_parent_directory_is_created(self) -> None:
        target = self.root / "deep" / "er" / "b.jpg"
        result = run("png2jpg", str(self.source), "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")

    def test_target_format_is_taken_from_output_suffix(self) -> None:
        target = self.root / "b.webp"
        result = run("convert", str(self.source), "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "WEBP")

    def test_jpeg_spelling_is_accepted_for_jpg_target(self) -> None:
        target = self.root / "b.jpeg"
        result = run("png2jpg", str(self.source), "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")
        self.assertEqual(listing(self.root), ["a.png", "b.jpeg"])

    def test_jpeg_spelling_is_accepted_with_explicit_to(self) -> None:
        target = self.root / "b.jpeg"
        result = run("convert", str(self.source), "--to", "jpg", "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")

    def test_output_is_not_suffixed_when_a_sibling_name_is_taken(self) -> None:
        make_image(self.root / "a.jpg", "JPEG")
        target = self.root / "b.jpg"
        result = run("png2jpg", str(self.source), "-o", str(target))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(listing(self.root), ["a.jpg", "a.png", "b.jpg"])


class OutputUsageErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = make_image(self.root / "a.png")
        self.other = make_image(self.root / "c.png")
        self.before = listing(self.root)

    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, combined(result))
        text = combined(result)
        self.assertNotIn("Traceback", text)
        for needle in needles:
            self.assertIn(needle, text)
        self.assertEqual(listing(self.root), self.before)

    def test_scan_without_listed_source_is_rejected(self) -> None:
        for command in (
            ["png2jpg", "-d", str(self.root), "-o", str(self.root / "out.jpg")],
            ["convert", "--to", "jpg", "-d", str(self.root), "-o", str(self.root / "out.jpg")],
        ):
            with self.subTest(command=command[0]):
                self.assert_usage_error(run(*command), "--output", "--outdir")

    def test_two_listed_sources_are_rejected(self) -> None:
        for command in (
            ["png2jpg", str(self.source), str(self.other), "-o", str(self.root / "out.jpg")],
            [
                "convert", "--to", "jpg", str(self.source), str(self.other),
                "--output", str(self.root / "out.jpg"),
            ],
        ):
            with self.subTest(command=command[0]):
                self.assert_usage_error(run(*command), "--output", "--outdir")

    def test_output_together_with_outdir_is_rejected(self) -> None:
        result = run(
            "png2jpg", str(self.source),
            "-o", str(self.root / "out.jpg"), "--outdir", str(self.root / "dir"),
        )
        self.assert_usage_error(result, "--output", "--outdir")

    def test_existing_directory_as_output_is_rejected(self) -> None:
        folder = self.root / "folder"
        folder.mkdir()
        self.before = listing(self.root)
        result = run("png2jpg", str(self.source), "-o", str(folder))
        self.assert_usage_error(result, "--output", "--outdir")

    def test_directory_scan_with_output_directory_is_rejected(self) -> None:
        out = self.root / "out"
        result = run("png2jpg", "-d", str(self.root), "-o", str(out))
        self.assert_usage_error(result, "--output")
        self.assertFalse(out.exists())

    def test_mismatched_extension_is_rejected(self) -> None:
        for command in (
            ["convert", str(self.source), "--to", "png", "-o", str(self.root / "out.jpg")],
            ["png2jpg", str(self.source), "-o", str(self.root / "out.png")],
        ):
            with self.subTest(command=command[0]):
                self.assert_usage_error(run(*command), "--output")

    def test_unsupported_output_extension_without_to_is_rejected(self) -> None:
        result = run("convert", str(self.source), "-o", str(self.root / "out.xyz"))
        self.assert_usage_error(result, "xyz")

    def test_output_without_extension_and_without_to_is_rejected(self) -> None:
        result = run("convert", str(self.source), "-o", str(self.root / "out"))
        self.assert_usage_error(result, "--to")

    def test_neither_to_nor_output_is_rejected(self) -> None:
        result = run("convert", str(self.source))
        self.assert_usage_error(result, "--to")


class OutputExistingTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = make_image(self.root / "a.png")

    def test_existing_output_is_skipped_and_left_untouched(self) -> None:
        target = self.root / "b.jpg"
        target.write_bytes(b"precious")
        result = run("png2jpg", str(self.source), "-o", str(target))
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertIn("target exists (use --overwrite)", combined(result))
        self.assertIn(str(self.source), combined(result))
        self.assertNotEqual(result.exit_code, 2)

    def test_existing_output_is_replaced_with_overwrite(self) -> None:
        target = self.root / "b.jpg"
        target.write_bytes(b"precious")
        result = run("png2jpg", str(self.source), "-o", str(target), "--overwrite")
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(format_of(target), "JPEG")

    def test_output_equal_to_source_is_skipped(self) -> None:
        before = self.source.read_bytes()
        result = run(
            "convert", str(self.source), "--to", "png", "-o", str(self.source), "--overwrite"
        )
        self.assertIn("source is the target", combined(result))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(listing(self.root), ["a.png"])


class OutputDryRunTests(unittest.TestCase):
    def test_dry_run_names_the_output_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_image(root / "a.png")
            target = root / "newdir" / "b.jpg"
            result = run("png2jpg", str(source), "-o", str(target), "--dry-run")
            self.assertEqual(result.exit_code, 0, combined(result))
            lines = [line.strip() for line in result.stdout.splitlines()]
            self.assertTrue(
                any(line.endswith("-> " + os.fspath(target)) for line in lines), lines
            )
            self.assertEqual(listing(root), ["a.png"])


class OutputHelpTests(unittest.TestCase):
    def test_help_lists_output_and_outdir_without_short_form(self) -> None:
        for command in (["convert"], ["png2jpg"]):
            with self.subTest(command=command[0]):
                result = runner.invoke(app, ["image", *command, "--help"])
                self.assertEqual(result.exit_code, 0)
                text = result.stdout
                self.assertIn("-o, --output", text)
                self.assertIn("--outdir", text)
                self.assertNotIn("-o, --outdir", text)
                self.assertLess(text.index("--output"), text.index("--outdir"))


if __name__ == "__main__":
    unittest.main()

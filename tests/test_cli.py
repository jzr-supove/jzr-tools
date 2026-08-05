"""CLI behavior tests."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from typer.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.filesystem import list_entries
from jzr_tools.core.images import (
    build_command,
    detect_backends,
    equivalent_extensions,
    find_sources,
    quality_caveat,
    target_for,
)
from jzr_tools.core.system import resolve_executable
from jzr_tools.core.text import slugify

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

# Smallest valid PNG, so image tests do not depend on a backend to build fixtures.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class CliTests(unittest.TestCase):
    def test_root_help_lists_registered_groups(self) -> None:
        result = runner.invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("text", result.stdout)
        self.assertIn("fs", result.stdout)
        self.assertIn("system", result.stdout)
        self.assertIn("convert", result.stdout)

    def test_version_flag_prints_package_version(self) -> None:
        result = runner.invoke(app, ["--version"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_slug_command_uses_text_core(self) -> None:
        result = runner.invoke(app, ["text", "slug", "Hello, World!"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "hello-world")
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_fs_ls_lists_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
            (tmp_path / "beta").mkdir()

            result = runner.invoke(app, ["fs", "ls", str(tmp_path)])

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.splitlines(), ["beta/", "alpha.txt"])
            self.assertEqual(list_entries(tmp_path), ["beta/", "alpha.txt"])

    def test_system_which_finds_python(self) -> None:
        result = runner.invoke(app, ["system", "which", sys.executable])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(Path(result.stdout.strip()).exists())
        self.assertEqual(resolve_executable(sys.executable), result.stdout.strip())

    def test_module_entrypoint_runs(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)

        result = subprocess.run(
            [sys.executable, "-m", "jzr_tools", "--version"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")


class ImageCoreTests(unittest.TestCase):
    def test_magick_command_places_quality_between_input_and_output(self) -> None:
        command = build_command(
            "magick",
            ["/usr/bin/magick"],
            Path("shot.png"),
            Path("shot.jpg"),
            quality=90,
        )

        self.assertEqual(
            command,
            ["/usr/bin/magick", "shot.png", "-quality", "90", "shot.jpg"],
        )

    def test_ffmpeg_command_maps_quality_onto_inverted_scale(self) -> None:
        command = build_command(
            "ffmpeg",
            ["/usr/bin/ffmpeg"],
            Path("shot.png"),
            Path("shot.jpg"),
            quality=100,
            overwrite=True,
        )

        self.assertIn("-q:v", command)
        self.assertEqual(command[command.index("-q:v") + 1], "2")
        self.assertIn("-y", command)
        self.assertEqual(command[-1], "shot.jpg")

    def test_ffmpeg_command_refuses_to_clobber_without_overwrite(self) -> None:
        command = build_command(
            "ffmpeg",
            ["/usr/bin/ffmpeg"],
            Path("shot.png"),
            Path("shot.webp"),
            overwrite=False,
        )

        self.assertIn("-n", command)
        self.assertNotIn("-y", command)
        # webp can be animated, so the single-frame guard must stay off.
        self.assertNotIn("-frames:v", command)

    def test_quality_caveat_is_silent_only_for_lossy_targets(self) -> None:
        self.assertIsNone(quality_caveat("ffmpeg", "jpg"))
        self.assertIsNone(quality_caveat("magick", "webp"))
        self.assertIn("ignores", quality_caveat("ffmpeg", "png"))
        self.assertIn("lossless", quality_caveat("magick", "png"))
        self.assertIn("ignores", quality_caveat("magick", "bmp"))

    def test_equivalent_extensions_groups_alternate_spellings(self) -> None:
        self.assertEqual(equivalent_extensions("jpeg"), frozenset({"jpg", "jpeg"}))
        self.assertEqual(equivalent_extensions(".PNG"), frozenset({"png"}))

    def test_find_sources_filters_by_extension_pattern_and_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "IMG_one.png").write_bytes(ONE_PIXEL_PNG)
            (root / "other.png").write_bytes(ONE_PIXEL_PNG)
            (root / "notes.txt").write_text("x", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "IMG_two.png").write_bytes(ONE_PIXEL_PNG)

            flat = find_sources(root, extensions={"png"})
            self.assertEqual([path.name for path in flat], ["IMG_one.png", "other.png"])

            filtered = find_sources(root, extensions={"png"}, pattern=r"^IMG_")
            self.assertEqual([path.name for path in filtered], ["IMG_one.png"])

            deep = find_sources(root, extensions={"png"}, pattern=r"IMG_", recursive=True)
            self.assertEqual(
                sorted(path.name for path in deep), ["IMG_one.png", "IMG_two.png"]
            )

    def test_target_for_honors_outdir(self) -> None:
        source = Path("/photos/shot.png")

        self.assertEqual(target_for(source, "jpg"), Path("/photos/shot.jpg"))
        self.assertEqual(
            target_for(source, ".JPEG", outdir=Path("/out")), Path("/out/shot.jpeg")
        )


class ConvertCliTests(unittest.TestCase):
    def test_format_pair_subcommand_is_resolved_on_demand(self) -> None:
        result = runner.invoke(app, ["convert", "png2jpg", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Convert PNG files to JPG", result.stdout)

    def test_unknown_format_pair_is_rejected(self) -> None:
        result = runner.invoke(app, ["convert", "foo2bar", "--help"])

        self.assertNotEqual(result.exit_code, 0)

    def test_bare_invocation_without_ext_fails(self) -> None:
        result = runner.invoke(app, ["convert", "--quality", "80"])

        self.assertEqual(result.exit_code, 2)

    def test_unsupported_ext_is_rejected(self) -> None:
        result = runner.invoke(app, ["convert", "--ext", "docx"])

        self.assertEqual(result.exit_code, 2)

    @unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
    def test_dry_run_prints_backend_command_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "shot.png"
            source.write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app, ["convert", "png2jpg", str(source), "-q", "90", "--dry-run"]
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn("shot.jpg", result.stdout)
            self.assertFalse((Path(tmpdir) / "shot.jpg").exists())

    @unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
    def test_batch_ext_mode_converts_pattern_matches_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "IMG_one.png").write_bytes(ONE_PIXEL_PNG)
            (root / "skipme.png").write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app, ["convert", "--ext", "jpg", "--pattern", r"^IMG_", "-d", str(root)]
            )

            self.assertEqual(result.exit_code, 0)
            self.assertTrue((root / "IMG_one.jpg").exists())
            self.assertFalse((root / "skipme.jpg").exists())

    @unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
    def test_existing_target_is_skipped_until_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "shot.png"
            source.write_bytes(ONE_PIXEL_PNG)
            target = root / "shot.jpg"
            target.write_bytes(b"stale")

            result = runner.invoke(app, ["convert", "png2jpg", str(source)])

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(target.read_bytes(), b"stale")

            result = runner.invoke(app, ["convert", "png2jpg", str(source), "--overwrite"])

            self.assertEqual(result.exit_code, 0)
            self.assertNotEqual(target.read_bytes(), b"stale")

    def test_no_matching_files_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["convert", "gif2png", "-d", tmpdir])

            self.assertEqual(result.exit_code, 1)


if __name__ == "__main__":
    unittest.main()

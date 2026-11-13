"""CLI behavior tests."""

from __future__ import annotations

import base64
from datetime import date
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.images import (
    build_command,
    detect_backends,
    equivalent_extensions,
    find_sources,
    quality_caveat,
    target_for,
)
from jzr_tools.core.text import kebab_case, slugify

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

# Smallest valid PNG, so image tests do not depend on a backend to build fixtures.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def combined_output(result: object) -> str:
    """Return stdout and stderr regardless of Click runner configuration."""
    stdout = getattr(result, "stdout", "")
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{stdout}{stderr}"


class CliTests(unittest.TestCase):
    def test_root_help_lists_registered_groups(self) -> None:
        result = runner.invoke(app, ["-h"])

        self.assertEqual(result.exit_code, 0)
        listed = re.findall(
            r"^  (\S+)", result.stdout.split("Commands:", 1)[1], flags=re.MULTILINE
        )
        self.assertEqual(
            sorted(listed), ["bind", "host", "image", "killport", "port", "text", "version"]
        )

    def test_host_help_lists_status_clean_and_procs(self) -> None:
        result = runner.invoke(app, ["host", "-h"])

        self.assertEqual(result.exit_code, 0)
        listed = re.findall(
            r"^  (\S+)", result.stdout.split("Commands:", 1)[1], flags=re.MULTILINE
        )
        self.assertEqual(sorted(listed), ["clean", "procs", "status"])

    def test_host_clean_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["host", "clean", "-h"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--dry-run", result.stdout)

    def test_removed_root_commands_are_unknown(self) -> None:
        for name in ("convert", "png2jpg", "fs", "system"):
            with self.subTest(name=name):
                # Unknown names consult the bindings file: keep it off the real one.
                with tempfile.TemporaryDirectory() as config:
                    result = runner.invoke(
                        app, [name, "--help"], env={"JZR_CONFIG_DIR": config}
                    )

                self.assertEqual(result.exit_code, 2)
                self.assertIn("No such command", combined_output(result))

    def test_version_flag_prints_package_version(self) -> None:
        result = runner.invoke(app, ["--version"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_slug_command_uses_text_core(self) -> None:
        result = runner.invoke(app, ["text", "slug", "Hello, World!"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "hello-world")
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_kebab_command_uses_lowercase_kebab_case(self) -> None:
        result = runner.invoke(app, ["text", "kebab", "ApiSix deployment Notes"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "apisix-deployment-notes")
        self.assertEqual(
            kebab_case("ApiSix deployment Notes"),
            "apisix-deployment-notes",
        )

    def test_kebab_command_can_prefix_iso_date(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(
                app,
                ["text", "kebab", "ApiSix deployment Notes", "--date"],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "2026-07-11_apisix-deployment-notes")

    def test_kebab_command_reads_multiline_stdin_when_text_is_omitted(self) -> None:
        text = "ApiSix deployment\n\nNotes"

        result = runner.invoke(app, ["text", "kebab"], input=text)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "apisix-deployment-notes")
        self.assertEqual(kebab_case(text), "apisix-deployment-notes")

    def test_kebab_command_can_copy_result_to_clipboard(self) -> None:
        with mock.patch("jzr_tools.commands.text.copy_to_clipboard") as copy_mock:
            result = runner.invoke(
                app,
                ["text", "kebab", "ApiSix deployment Notes", "-c"],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "apisix-deployment-notes")
        copy_mock.assert_called_once_with("apisix-deployment-notes")

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
            ["/usr/bin/magick", "shot.png[0]", "-quality", "90", "shot.jpg"],
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
        result = runner.invoke(app, ["image", "png2jpg", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Convert PNG files to JPG", result.stdout)

    def test_unknown_format_pair_is_rejected(self) -> None:
        result = runner.invoke(app, ["image", "foo2bar", "--help"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such command 'foo2bar'", combined_output(result))

    def test_convert_without_to_fails(self) -> None:
        result = runner.invoke(app, ["image", "convert", "--quality", "80"])

        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("No such command", combined_output(result))
        self.assertIn("--to", combined_output(result))

    def test_unsupported_to_is_rejected(self) -> None:
        result = runner.invoke(app, ["image", "convert", "--to", "docx"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Unsupported extension", combined_output(result))

    def test_unsupported_from_is_rejected(self) -> None:
        result = runner.invoke(
            app, ["image", "convert", "--to", "jpg", "--from", "docx"]
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Unsupported extension", combined_output(result))

    @unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
    def test_dry_run_prints_backend_command_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "shot.png"
            source.write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app, ["image", "png2jpg", str(source), "-q", "90", "--dry-run"]
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn("shot.jpg", result.stdout)
            self.assertFalse((Path(tmpdir) / "shot.jpg").exists())

    @unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
    def test_batch_mode_converts_pattern_matches_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "IMG_one.png").write_bytes(ONE_PIXEL_PNG)
            (root / "skipme.png").write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app,
                ["image", "convert", "--to", "jpg", "--pattern", r"^IMG_", "-d", str(root)],
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

            result = runner.invoke(app, ["image", "png2jpg", str(source)])

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(target.read_bytes(), b"stale")

            result = runner.invoke(
                app, ["image", "png2jpg", str(source), "--overwrite"]
            )

            self.assertEqual(result.exit_code, 0)
            self.assertNotEqual(target.read_bytes(), b"stale")

    def test_no_matching_files_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["image", "gif2png", "-d", tmpdir])

            self.assertEqual(result.exit_code, 1)
            self.assertIn("No matching files.", combined_output(result))


if __name__ == "__main__":
    unittest.main()

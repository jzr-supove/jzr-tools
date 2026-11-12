"""Acceptance tests for image conversion bug fixes (spec section A)."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import shlex
import struct
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import images
from jzr_tools.core.images import build_command, convert_file, detect_backends

runner = CliRunner()

ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

HAS_MAGICK = "magick" in detect_backends()
HAS_FFMPEG = "ffmpeg" in detect_backends()


def three_frame_gif() -> bytes:
    """Build a valid 1x1, 3-frame GIF without needing any backend."""
    frame = (
        b"\x21\xf9\x04\x00"
        + struct.pack("<H", 10)
        + b"\x00\x00"
        + b"\x2c"
        + struct.pack("<HHHH", 0, 0, 1, 1)
        + b"\x00"
        + b"\x02\x02\x44\x01\x00"
    )
    header = b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x80\x00\x00"
    palette = b"\x00\x00\x00\xff\xff\xff"
    return header + palette + frame * 3 + b"\x3b"


def combined(result: object) -> str:
    stdout = getattr(result, "stdout", "")
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{stdout}{stderr}"


def planned_targets(result: object) -> list[str]:
    """Return the last argv element of each dry-run command line on stdout."""
    targets: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.rstrip().endswith("planned"):
            continue
        targets.append(shlex.split(line)[-1])
    return targets


MAGICK = ["magick"]
FFMPEG = ["ffmpeg"]


class MagickCommandTests(unittest.TestCase):
    def test_non_animated_targets_select_first_frame(self) -> None:
        for ext in ("jpg", "jpeg", "png", "bmp", "tif", "tiff", "heic", "ico", "ppm", "tga"):
            with self.subTest(target=ext):
                command = build_command(
                    "magick", MAGICK, Path("anim.gif"), Path(f"anim.{ext}")
                )
                self.assertEqual(command, ["magick", "anim.gif[0]", f"anim.{ext}"])

    def test_first_frame_selector_stays_before_quality_option(self) -> None:
        command = build_command(
            "magick", MAGICK, Path("a.gif"), Path("a.jpg"), quality=80
        )
        self.assertEqual(command, ["magick", "a.gif[0]", "-quality", "80", "a.jpg"])

    def test_animated_targets_keep_plain_source(self) -> None:
        for ext in ("gif", "webp", "avif"):
            with self.subTest(target=ext):
                command = build_command(
                    "magick", MAGICK, Path("anim.png"), Path(f"anim.{ext}")
                )
                self.assertEqual(command[1], "anim.png")

    def test_percent_in_target_is_doubled(self) -> None:
        command = build_command("magick", MAGICK, Path("100%d.png"), Path("100%d.jpg"))
        self.assertEqual(command[-1], "100%%d.jpg")

    def test_percent_in_source_is_doubled_when_frame_selector_is_added(self) -> None:
        command = build_command("magick", MAGICK, Path("100%d.png"), Path("100%d.jpg"))
        self.assertEqual(command[1], "100%%d.png[0]")

    def test_names_without_percent_are_untouched(self) -> None:
        command = build_command("magick", MAGICK, Path("a b.png"), Path("a b.jpg"))
        self.assertEqual(command, ["magick", "a b.png[0]", "a b.jpg"])


class FfmpegCommandTests(unittest.TestCase):
    def _command(self, source: str, target: str) -> list[str]:
        return build_command("ffmpeg", FFMPEG, Path(source), Path(target))

    def test_pattern_type_none_precedes_input_for_percent_image2_sources(self) -> None:
        for ext in ("bmp", "jpeg", "jpg", "png", "ppm", "tga", "tif", "tiff", "webp"):
            with self.subTest(source_ext=ext):
                command = self._command(f"100%d.{ext}", "out.gif")
                self.assertIn("-pattern_type", command)
                index = command.index("-pattern_type")
                self.assertEqual(command[index + 1], "none")
                self.assertLess(index, command.index("-i"))

    def test_pattern_type_not_added_without_percent(self) -> None:
        command = self._command("shot.png", "shot.jpg")
        self.assertNotIn("-pattern_type", command)

    def test_pattern_type_not_added_for_non_image2_sources(self) -> None:
        for ext in ("gif", "avif", "heic", "ico"):
            with self.subTest(source_ext=ext):
                command = self._command(f"100%d.{ext}", "out.png")
                self.assertNotIn("-pattern_type", command)

    def test_webp_target_with_percent_forces_webp_muxer_before_target(self) -> None:
        command = self._command("shot.png", "100%d.webp")
        self.assertIn("-f", command)
        index = command.index("-f")
        self.assertEqual(command[index + 1], "webp")
        self.assertLess(index + 1, len(command) - 1)
        self.assertEqual(command[-1], "100%d.webp")

    def test_format_flag_not_added_for_plain_or_other_targets(self) -> None:
        self.assertNotIn("-f", self._command("shot.png", "shot.webp"))
        self.assertNotIn("-f", self._command("shot.png", "100%d.jpg"))

    def test_target_path_is_not_altered_by_ffmpeg_command(self) -> None:
        self.assertEqual(self._command("a.png", "100%d.jpg")[-1], "100%d.jpg")


class ConvertFileOutcomeTests(unittest.TestCase):
    def test_exit_zero_without_target_is_reported_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "shot.png"
            source.write_bytes(ONE_PIXEL_PNG)
            target = Path(tmpdir) / "shot.jpg"

            completed = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch(
                "jzr_tools.core.images.subprocess.run", return_value=completed
            ):
                result = convert_file(
                    source, target, backend="magick", executable=MAGICK
                )

            self.assertFalse(target.exists())
            self.assertEqual(result.status, "failed")
            self.assertTrue(result.detail.strip())

    def test_exit_zero_with_target_written_is_converted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "shot.png"
            source.write_bytes(ONE_PIXEL_PNG)
            target = Path(tmpdir) / "shot.jpg"

            def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
                target.write_bytes(b"jpg")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("jzr_tools.core.images.subprocess.run", side_effect=fake_run):
                result = convert_file(
                    source, target, backend="magick", executable=MAGICK
                )

            self.assertEqual(result.status, "converted")


class PlanTargetsTests(unittest.TestCase):
    def _plan(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return images.plan_targets(*args, **kwargs)

    def test_default_targets_follow_target_for(self) -> None:
        sources = [Path("/p/a.png"), Path("/p/b.webp")]
        self.assertEqual(
            self._plan(sources, "jpg"),
            [Path("/p/a.jpg"), Path("/p/b.jpg")],
        )

    def test_outdir_without_root_flattens(self) -> None:
        sources = [Path("/p/a/x.png")]
        self.assertEqual(self._plan(sources, "jpg", outdir=Path("/out")), [Path("/out/x.jpg")])

    def test_outdir_with_root_mirrors_relative_directory(self) -> None:
        root = Path("/p/root")
        sources = [root / "x.png", root / "sub" / "x.png", root / "sub" / "deep" / "y.png"]
        self.assertEqual(
            self._plan(sources, "jpg", outdir=Path("/out"), root=root),
            [Path("/out/x.jpg"), Path("/out/sub/x.jpg"), Path("/out/sub/deep/y.jpg")],
        )

    def test_root_without_outdir_keeps_targets_beside_sources(self) -> None:
        root = Path("/p/root")
        sources = [root / "sub" / "x.png"]
        self.assertEqual(self._plan(sources, "jpg", root=root), [root / "sub" / "x.jpg"])

    def test_collisions_get_numeric_suffix_in_order(self) -> None:
        sources = [Path("/p/x.png"), Path("/p/x.webp"), Path("/p/x.bmp")]
        self.assertEqual(
            self._plan(sources, "jpg"),
            [Path("/p/x.jpg"), Path("/p/x-2.jpg"), Path("/p/x-3.jpg")],
        )

    def test_collision_check_is_case_insensitive(self) -> None:
        sources = [Path("/p/Photo.png"), Path("/p/photo.webp")]
        targets = self._plan(sources, "jpg")
        self.assertEqual(targets[0], Path("/p/Photo.jpg"))
        self.assertEqual(targets[1], Path("/p/photo-2.jpg"))

    def test_suffix_skips_candidates_that_are_already_claimed(self) -> None:
        sources = [Path("/p/x-2.png"), Path("/p/x.png"), Path("/p/x.webp")]
        self.assertEqual(
            self._plan(sources, "jpg"),
            [Path("/p/x-2.jpg"), Path("/p/x.jpg"), Path("/p/x-3.jpg")],
        )

    def test_flattened_outdir_collisions_are_resolved(self) -> None:
        sources = [Path("/a/x.png"), Path("/b/x.png")]
        self.assertEqual(
            self._plan(sources, "jpg", outdir=Path("/out")),
            [Path("/out/x.jpg"), Path("/out/x-2.jpg")],
        )

    def test_source_that_is_its_own_target_claims_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            same = root / "x.jpg"
            other = root / "x.png"
            targets = self._plan([same, other], "jpg")
            # The jpg maps onto itself and is left alone; it must not push the
            # png over to x-2.jpg.
            self.assertEqual(targets, [same, root / "x.jpg"])


class CollisionCliTests(unittest.TestCase):
    def _invoke(self, args: list[str]):  # noqa: ANN202
        with mock.patch(
            "jzr_tools.commands.image.resolve_backend", return_value=("magick", MAGICK)
        ):
            return runner.invoke(app, args)

    def test_batch_mode_gives_colliding_sources_distinct_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "x.png").write_bytes(ONE_PIXEL_PNG)
            (root / "x.webp").write_bytes(b"webp")

            result = self._invoke(["image", "convert", "--to", "jpg", "-d", str(root), "--dry-run"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                planned_targets(result),
                [str(root / "x.jpg"), str(root / "x-2.jpg")],
            )
            self.assertIn("2 planned", result.stdout)

    def test_recursive_outdir_mirrors_tree_under_batch_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ROOT"
            out = Path(tmpdir) / "OUT"
            (root / "sub").mkdir(parents=True)
            (root / "x.png").write_bytes(ONE_PIXEL_PNG)
            (root / "sub" / "x.png").write_bytes(ONE_PIXEL_PNG)

            result = self._invoke(
                ["image", "convert", "--to", "jpg", "-d", str(root), "-r", "-o", str(out), "--dry-run"]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            # Discovery order is not part of the contract, only the mapping.
            self.assertEqual(
                sorted(planned_targets(result)),
                sorted([str(out / "x.jpg"), str(out / "sub" / "x.jpg")]),
            )

    def test_pair_form_with_explicit_sources_flattens_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a").mkdir()
            (base / "b").mkdir()
            out = base / "OUT"
            (base / "a" / "x.png").write_bytes(ONE_PIXEL_PNG)
            (base / "b" / "x.png").write_bytes(ONE_PIXEL_PNG)

            result = self._invoke(
                [
                    "image",
                    "png2jpg",
                    str(base / "a" / "x.png"),
                    str(base / "b" / "x.png"),
                    "-o",
                    str(out),
                    "--dry-run",
                ]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                planned_targets(result),
                [str(out / "x.jpg"), str(out / "x-2.jpg")],
            )


class InvalidPatternTests(unittest.TestCase):
    def _assert_clean_usage_error(self, result: object) -> None:
        self.assertNotIsInstance(result.exception, re.error)
        self.assertEqual(result.exit_code, 2, combined(result))
        self.assertIn("pattern", combined(result).lower())

    def test_batch_mode_rejects_invalid_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["image", "convert", "--to", "jpg", "-p", "[", "-d", tmpdir]
            )
            self._assert_clean_usage_error(result)

    def test_pair_form_rejects_invalid_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["image", "png2jpg", "-p", "[", "-d", tmpdir]
            )
            self._assert_clean_usage_error(result)

    def test_pair_form_with_explicit_sources_rejects_invalid_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "x.png"
            source.write_bytes(ONE_PIXEL_PNG)
            result = runner.invoke(app, ["image", "png2jpg", str(source), "-p", "["])
            self._assert_clean_usage_error(result)


class UnsupportedTargetTests(unittest.TestCase):
    def test_ffmpeg_cannot_write_heic_or_heif(self) -> None:
        for ext in ("heic", "heif"):
            with self.subTest(ext=ext):
                message = images.unsupported_target("ffmpeg", ext)
                self.assertIsNotNone(message)
                self.assertIn(ext, message)
                self.assertIn("ImageMagick", message)

    def test_supported_combinations_return_none(self) -> None:
        for backend, ext in (("magick", "heic"), ("ffmpeg", "jpg"), ("ffmpeg", "avif")):
            with self.subTest(backend=backend, ext=ext):
                self.assertIsNone(images.unsupported_target(backend, ext))

    def _invoke(self, extra: list[str]):  # noqa: ANN202
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "x.png"
            source.write_bytes(ONE_PIXEL_PNG)
            failed = mock.Mock(returncode=1, stdout="", stderr="boom")
            with (
                mock.patch(
                    "jzr_tools.commands.image.resolve_backend",
                    return_value=("ffmpeg", FFMPEG),
                ),
                mock.patch(
                    "jzr_tools.core.images.subprocess.run", return_value=failed
                ) as run_mock,
            ):
                result = runner.invoke(
                    app, ["image", "png2heic", str(source), "-b", "ffmpeg", *extra]
                )
            return result, run_mock

    def _assert_rejected(self, result: object, run_mock: mock.Mock) -> None:
        output = combined(result)
        self.assertEqual(result.exit_code, 1, output)
        self.assertIn("ImageMagick", output)
        self.assertIn("heic", output.lower())
        self.assertNotIn("fail ", output)
        run_mock.assert_not_called()

    def test_cli_rejects_ffmpeg_heic_target(self) -> None:
        result, run_mock = self._invoke([])
        self._assert_rejected(result, run_mock)

    def test_cli_rejects_ffmpeg_heic_target_in_dry_run(self) -> None:
        result, run_mock = self._invoke(["--dry-run"])
        self._assert_rejected(result, run_mock)
        self.assertNotIn("planned", result.stdout)


@unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
class MagickEndToEndTests(unittest.TestCase):
    def test_multi_frame_gif_converts_to_single_png_and_second_run_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "anim.gif"
            source.write_bytes(three_frame_gif())

            result = runner.invoke(app, ["image", "gif2png", str(source), "-b", "magick"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["anim.gif", "anim.png"])

            again = runner.invoke(app, ["image", "gif2png", str(source), "-b", "magick"])

            self.assertEqual(again.exit_code, 0, combined(again))
            self.assertIn("skip", combined(again))
            self.assertNotIn("converted", combined(again))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["anim.gif", "anim.png"])

    def test_percent_in_file_name_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app, ["image", "png2jpg", str(root / "100%d.png"), "-b", "magick"]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                sorted(p.name for p in root.iterdir()), ["100%d.jpg", "100%d.png"]
            )


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
class FfmpegEndToEndTests(unittest.TestCase):
    def test_percent_in_file_name_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "100%d.png").write_bytes(ONE_PIXEL_PNG)

            result = runner.invoke(
                app, ["image", "png2jpg", str(root / "100%d.png"), "-b", "ffmpeg"]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertTrue((root / "100%d.jpg").is_file())


if __name__ == "__main__":
    unittest.main()

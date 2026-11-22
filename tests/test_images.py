"""Acceptance tests for image conversion bug fixes."""

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


class MagickCommandTests(unittest.TestCase):
    def test_non_animated_targets_select_first_frame(self) -> None:
        for ext in ("jpg", "jpeg", "png", "bmp", "tif", "tiff", "heic", "ico", "ppm", "tga"):
            with self.subTest(target=ext):
                command = build_command(
                    "magick", MAGICK, Path("anim.gif"), Path(f"anim.{ext}")
                )
                lzw = ["-compress", "LZW"] if ext in ("tif", "tiff") else []
                self.assertEqual(command, ["magick", "anim.gif[0]", *lzw, f"anim.{ext}"])

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

    def test_percent_source_drops_extra_frames_instead_of_using_a_selector(self) -> None:
        for name in ("100%d.png", "50%.png", "d%d/shot.png"):
            with self.subTest(source=name):
                command = build_command("magick", MAGICK, Path(name), Path("out.jpg"))
                # The source is passed on as the platform renders the Path.
                self.assertEqual(command, ["magick", str(Path(name)), "-delete", "1--1", "out.jpg"])

    def test_percent_source_keeps_frame_drop_before_quality_option(self) -> None:
        command = build_command(
            "magick", MAGICK, Path("100%d.gif"), Path("out.jpg"), quality=80
        )
        self.assertEqual(
            command, ["magick", "100%d.gif", "-delete", "1--1", "-quality", "80", "out.jpg"]
        )

    def test_percent_source_with_animated_target_keeps_every_frame(self) -> None:
        command = build_command("magick", MAGICK, Path("100%d.png"), Path("out.gif"))
        self.assertEqual(command, ["magick", "100%d.png", "out.gif"])

    def test_percent_is_never_doubled(self) -> None:
        command = build_command("magick", MAGICK, Path("100%d.png"), Path("out.jpg"))
        self.assertFalse([part for part in command if "%%" in part])

    def test_names_without_percent_are_untouched(self) -> None:
        command = build_command("magick", MAGICK, Path("a b.png"), Path("a b.jpg"))
        self.assertEqual(command, ["magick", "a b.png[0]", "a b.jpg"])


class RemovedFfmpegBackendTests(unittest.TestCase):
    def test_build_command_rejects_ffmpeg(self) -> None:
        with self.assertRaises(ValueError):
            build_command("ffmpeg", ["ffmpeg"], Path("a.png"), Path("a.jpg"))

    def test_detect_backends_only_ever_reports_magick(self) -> None:
        with mock.patch(
            "jzr_tools.core.images.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            self.assertEqual(set(detect_backends()), {"magick"})

    def test_cli_rejects_ffmpeg_as_a_backend_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "x.png"
            source.write_bytes(ONE_PIXEL_PNG)
            for args in (["-b", "ffmpeg"], ["--backend", "ffmpeg"]):
                with self.subTest(args=args):
                    result = runner.invoke(app, ["image", "png2jpg", str(source), *args])

                    self.assertEqual(result.exit_code, 2, combined(result))
                    self.assertIn("ffmpeg", combined(result))
                    self.assertFalse((Path(tmpdir) / "x.jpg").exists())


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


class MagickPercentTargetTests(unittest.TestCase):
    """A `%` target never reaches ImageMagick: it is staged, then renamed."""

    def _convert(self, tmpdir: str, fake_run, **options):  # noqa: ANN001, ANN003, ANN202
        source = Path(tmpdir) / "100%d.png"
        source.write_bytes(ONE_PIXEL_PNG)
        target = Path(tmpdir) / "o%d" / "100%d.jpg"
        with mock.patch(
            "jzr_tools.core.images.subprocess.run", side_effect=fake_run
        ) as run_mock:
            result = convert_file(
                source, target, backend="magick", executable=MAGICK, **options
            )
        return result, target, run_mock

    def test_output_is_written_under_a_plain_name_then_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            seen: dict[str, object] = {}

            def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
                seen["command"], seen["cwd"] = command, kwargs.get("cwd")
                (Path(kwargs["cwd"]) / command[-1]).write_bytes(b"jpg")
                return mock.Mock(returncode=0, stdout="", stderr="")

            result, target, _ = self._convert(tmpdir, fake_run)

            self.assertEqual(result.status, "converted", result.detail)
            self.assertEqual(target.read_bytes(), b"jpg")
            self.assertEqual([p.name for p in target.parent.iterdir()], ["100%d.jpg"])
            command = seen["command"]
            self.assertNotIn("%", command[-1])
            self.assertEqual(Path(command[-1]).name, command[-1])
            self.assertEqual(seen["cwd"], target.parent)
            self.assertTrue(Path(command[1]).is_absolute())

    def test_failure_removes_the_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:

            def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
                (Path(kwargs["cwd"]) / command[-1]).write_bytes(b"partial")
                return mock.Mock(returncode=1, stdout="", stderr="boom")

            result, target, _ = self._convert(tmpdir, fake_run)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.detail, "boom")
            self.assertEqual(list(target.parent.iterdir()), [])

    def test_exit_zero_without_output_is_reported_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result, target, _ = self._convert(
                tmpdir, lambda command, **kwargs: mock.Mock(returncode=0, stdout="", stderr="")
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("100%d.jpg", result.detail)
            self.assertFalse(target.exists())

    def test_dry_run_explains_the_rename_and_runs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result, target, run_mock = self._convert(tmpdir, None, dry_run=True)

            self.assertEqual(result.status, "planned")
            self.assertIn(result.command[-1], result.detail)
            self.assertIn("100%d.jpg", result.detail)
            run_mock.assert_not_called()
            self.assertFalse(target.parent.exists())

    def test_plain_magick_targets_are_not_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "100%d.png"
            source.write_bytes(ONE_PIXEL_PNG)
            target = Path(tmpdir) / "plain.jpg"
            result = convert_file(
                source, target, backend="magick", executable=MAGICK, dry_run=True
            )
            self.assertEqual(result.command[-1], str(target))
            self.assertEqual(result.detail, "")


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
                ["image", "convert", "--to", "jpg", "-d", str(root), "-r", "--outdir", str(out), "--dry-run"]
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
                    "--outdir",
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


if __name__ == "__main__":
    unittest.main()

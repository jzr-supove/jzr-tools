"""Behavior tests for the `jzr image` grammar (convert, <from>2<to>, formats)."""

from __future__ import annotations

import base64
import contextlib
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.images import BackendUnavailableError, SUPPORTED_EXTENSIONS, detect_backends

runner = CliRunner()

ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
MAGICK = ["magick"]
PATCH_BACKEND = "jzr_tools.commands.image.resolve_backend"


def combined(result: object) -> str:
    stdout = getattr(result, "stdout", "")
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{stdout}{stderr}"


def planned_targets(result: object) -> list[str]:
    targets: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.rstrip().endswith("planned"):
            continue
        targets.append(shlex.split(line)[-1])
    return targets


def plan(args: list[str]):  # noqa: ANN201
    """Invoke with a fake magick backend, so nothing is executed."""
    with mock.patch(PATCH_BACKEND, return_value=("magick", MAGICK)):
        return runner.invoke(app, [*args, "--dry-run"])


def touch(path: Path, data: bytes = ONE_PIXEL_PNG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@contextlib.contextmanager
def working_directory(path: Path):  # noqa: ANN201
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class DirectoryModeTests(unittest.TestCase):
    def test_defaults_to_current_directory_top_level_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            touch(root / "a.png")
            touch(root / "sub" / "b.png")

            with working_directory(root):
                result = plan(["image", "convert", "--to", "jpg"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(len(planned_targets(result)), 1)
            self.assertEqual(Path(planned_targets(result)[0]).name, "a.jpg")
            self.assertIn("1 planned", result.stdout)

    def test_recursive_includes_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "a.png")
            touch(root / "sub" / "b.png")

            result = plan(["image", "convert", "--to", "jpg", "-d", str(root), "-r"])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                sorted(planned_targets(result)),
                sorted([str(root / "a.jpg"), str(root / "sub" / "b.jpg")]),
            )

    def test_without_from_every_supported_image_not_in_target_format_is_a_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("a.png", "b.jpg", "c.jpeg", "d.webp"):
                touch(root / name)
            touch(root / "notes.txt", b"x")

            result = plan(["image", "convert", "--to", "jpg", "-d", str(root)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                sorted(planned_targets(result)),
                sorted([str(root / "a.jpg"), str(root / "d.jpg")]),
            )

    def test_target_aliases_are_skipped_for_tiff_and_heic_too(self) -> None:
        for target, same, other in (("tif", "x.tiff", "y.png"), ("heif", "x.heic", "y.png")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                touch(root / same)
                touch(root / other)

                result = plan(["image", "convert", "--to", target, "-d", str(root)])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(
                    planned_targets(result), [str(root / f"y.{target}")]
                )

    def test_from_limits_sources_to_that_format_and_its_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("a.jpg", "b.jpeg", "c.png", "d.webp"):
                touch(root / name)

            result = plan(["image", "convert", "--from", "jpg", "--to", "png", "-d", str(root)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(
                sorted(planned_targets(result)),
                sorted([str(root / "a.png"), str(root / "b.png")]),
            )

    def test_pair_form_is_identical_to_convert_with_from_and_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "a.png")
            touch(root / "b.webp")
            touch(root / "c.png")

            pair = plan(["image", "png2jpg", "-d", str(root), "-q", "80"])
            explicit = plan(
                ["image", "convert", "--from", "png", "--to", "jpg", "-d", str(root), "-q", "80"]
            )

            self.assertEqual(pair.exit_code, 0, combined(pair))
            self.assertEqual(pair.exit_code, explicit.exit_code)
            self.assertEqual(pair.stdout, explicit.stdout)
            self.assertEqual(
                sorted(planned_targets(pair)),
                sorted([str(root / "a.jpg"), str(root / "c.jpg")]),
            )

    def test_pattern_is_matched_against_path_relative_to_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "IMG_top.png")
            touch(root / "sub" / "IMG_deep.png")
            touch(root / "other.png")

            anchored = plan(
                ["image", "convert", "--to", "jpg", "-d", str(root), "-r", "-p", r"^IMG_"]
            )
            in_sub = plan(
                ["image", "convert", "--to", "jpg", "-d", str(root), "-r", "-p", r"^sub/IMG_"]
            )

            self.assertEqual(planned_targets(anchored), [str(root / "IMG_top.jpg")])
            self.assertEqual(planned_targets(in_sub), [str(root / "sub" / "IMG_deep.jpg")])

    def test_nothing_matching_exits_one_with_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            touch(Path(tmpdir) / "a.jpg")

            result = plan(["image", "convert", "--to", "jpg", "-d", tmpdir])

            self.assertEqual(result.exit_code, 1)
            self.assertIn("No matching files.", combined(result))


class ExplicitSourceTests(unittest.TestCase):
    def test_without_from_sources_are_converted_as_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = touch(root / "b.webp")

            result = plan(["image", "convert", "--to", "jpg", str(source)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(planned_targets(result), [str(root / "b.jpg")])

    def test_existing_source_with_other_extension_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = touch(Path(tmpdir) / "b.webp")
            for args in (
                ["image", "png2jpg", str(source)],
                ["image", "convert", "--from", "png", "--to", "jpg", str(source)],
            ):
                with self.subTest(args=args):
                    result = plan(args)

                    self.assertEqual(result.exit_code, 2, combined(result))
                    self.assertIn("b.webp", combined(result))
                    self.assertNotIn("planned", result.stdout)

    def test_mismatched_source_prevents_converting_the_matching_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            good = touch(Path(tmpdir) / "a.png")
            bad = touch(Path(tmpdir) / "b.webp")

            result = plan(["image", "png2jpg", str(good), str(bad)])

            self.assertEqual(result.exit_code, 2, combined(result))
            self.assertNotIn("a.jpg", result.stdout)

    def test_alias_extension_counts_as_the_from_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jpeg = touch(root / "a.jpeg")
            jpg = touch(root / "b.jpg")

            first = plan(["image", "jpg2png", str(jpeg)])
            second = plan(["image", "jpeg2png", str(jpg)])

            self.assertEqual(first.exit_code, 0, combined(first))
            self.assertEqual(planned_targets(first), [str(root / "a.png")])
            self.assertEqual(second.exit_code, 0, combined(second))
            self.assertEqual(planned_targets(second), [str(root / "b.png")])

    def test_extension_may_be_omitted_when_from_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "shot.png")

            result = plan(["image", "png2jpg", str(root / "shot")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(planned_targets(result), [str(root / "shot.jpg")])

    def test_dotted_basename_without_extension_is_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "shot.v2.png")

            result = plan(["image", "png2jpg", str(root / "shot.v2")])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(planned_targets(result), [str(root / "shot.v2.jpg")])

    def test_inference_works_with_convert_from_and_outdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "shot.png")
            out = root / "OUT"

            result = plan(
                ["image", "convert", "--from", "png", "--to", "jpg", str(root / "shot"), "-o", str(out)]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertEqual(planned_targets(result), [str(out / "shot.jpg")])

    def test_missing_source_after_inference_is_a_usage_error_naming_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("nothere", "nothere.png", "shot.v2"):
                with self.subTest(name=name):
                    result = plan(["image", "png2jpg", str(Path(tmpdir) / name)])

                    self.assertEqual(result.exit_code, 2, combined(result))
                    self.assertIn(name, combined(result))

    def test_without_from_there_is_no_inference_and_missing_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "shot.png")
            missing = root / "shot"

            result = plan(["image", "convert", "--to", "jpg", str(missing)])

            self.assertEqual(result.exit_code, 2, combined(result))
            self.assertIn("shot", combined(result))
            self.assertNotIn("planned", result.stdout)

    def test_missing_source_next_to_valid_one_converts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            good = touch(root / "a.png")

            result = plan(["image", "png2jpg", str(good), str(root / "gone")])

            self.assertEqual(result.exit_code, 2, combined(result))
            self.assertIn("gone", combined(result))
            self.assertNotIn("a.jpg", result.stdout)

    def test_dir_or_recursive_with_sources_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = touch(Path(tmpdir) / "a.png")
            for extra in (["-d", tmpdir], ["--dir", tmpdir], ["-r"], ["--recursive"]):
                for head in (["image", "png2jpg"], ["image", "convert", "--to", "jpg"]):
                    with self.subTest(extra=extra, head=head):
                        result = plan([*head, str(source), *extra])

                        self.assertEqual(result.exit_code, 2, combined(result))
                        self.assertIn("SOURCES", combined(result))
                        self.assertNotIn("planned", result.stdout)

    def test_pattern_is_matched_against_the_file_name_for_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            keep = touch(root / "a" / "IMG_x.png")
            drop = touch(root / "b" / "other.png")

            matched = plan(["image", "png2jpg", str(keep), str(drop), "-p", r"^IMG_"])
            by_path = plan(["image", "png2jpg", str(keep), "-p", r"^a/"])

            self.assertEqual(matched.exit_code, 0, combined(matched))
            self.assertEqual(planned_targets(matched), [str(root / "a" / "IMG_x.jpg")])
            self.assertEqual(by_path.exit_code, 1, combined(by_path))
            self.assertIn("No matching files.", combined(by_path))

    def test_invalid_pattern_is_a_usage_error_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = touch(Path(tmpdir) / "x.png")
            for args in (
                ["image", "convert", "--to", "jpg", "-p", "[", "-d", tmpdir],
                ["image", "png2jpg", "-p", "[", "-d", tmpdir],
                ["image", "png2jpg", str(source), "-p", "["],
            ):
                with self.subTest(args=args):
                    result = runner.invoke(app, args)

                    self.assertEqual(result.exit_code, 2, combined(result))
                    self.assertIn("pattern", combined(result).lower())
                    self.assertFalse(
                        result.exception is not None
                        and not isinstance(result.exception, SystemExit),
                        repr(result.exception),
                    )


class ConvertOptionTests(unittest.TestCase):
    def test_quality_must_be_between_1_and_100(self) -> None:
        for value in ("0", "101"):
            with self.subTest(value=value):
                result = runner.invoke(
                    app, ["image", "convert", "--to", "jpg", "-q", value, "-d", "."]
                )
                self.assertEqual(result.exit_code, 2)
                self.assertNotIn("No such command", combined(result))
                self.assertIn("quality", combined(result).lower())

    def test_quality_on_lossless_target_prints_a_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            touch(Path(tmpdir) / "a.jpg")

            result = plan(["image", "convert", "--to", "png", "-q", "90", "-d", tmpdir])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertIn("note:", result.stderr)

    def test_quality_on_lossy_target_prints_no_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            touch(Path(tmpdir) / "a.png")

            result = plan(["image", "convert", "--to", "jpg", "-q", "90", "-d", tmpdir])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertNotIn("note:", result.stderr)

    def test_dry_run_prints_one_command_per_file_then_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            touch(Path(tmpdir) / "a.png")
            touch(Path(tmpdir) / "b.png")

            result = plan(["image", "png2jpg", "-d", tmpdir])

            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 3, lines)
            self.assertTrue(all(line.startswith("magick") for line in lines[:2]))
            self.assertEqual(lines[2], "magick: 2 planned")

    def test_no_backend_installed_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            touch(Path(tmpdir) / "a.png")
            with mock.patch(PATCH_BACKEND, side_effect=BackendUnavailableError("no backend")):
                result = runner.invoke(app, ["image", "png2jpg", "-d", tmpdir])

            self.assertEqual(result.exit_code, 1)
            self.assertIn("no backend", combined(result))

    def test_outdir_mirrors_subdirectories_when_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ROOT"
            out = Path(tmpdir) / "OUT"
            touch(root / "sub" / "x.png")

            result = plan(["image", "png2jpg", "-d", str(root), "-r", "-o", str(out)])

            self.assertEqual(planned_targets(result), [str(out / "sub" / "x.jpg")])


class ImageFormatsTests(unittest.TestCase):
    def test_formats_lists_extensions_and_each_backend(self) -> None:
        result = runner.invoke(app, ["image", "formats"])

        self.assertEqual(result.exit_code, 0, combined(result))
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[0], "extensions: " + " ".join(sorted(SUPPORTED_EXTENSIONS))
        )
        self.assertTrue(lines[1].startswith("magick: "), lines)
        self.assertTrue(lines[2].startswith("ffmpeg: "), lines)
        self.assertIn("cannot write: heic heif", lines[2])

    def test_image_help_lists_subcommands(self) -> None:
        result = runner.invoke(app, ["image", "-h"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("convert", result.stdout)
        self.assertIn("formats", result.stdout)


@unittest.skipUnless(detect_backends(), "needs ImageMagick or ffmpeg")
class RealConversionTests(unittest.TestCase):
    def test_extension_less_source_is_converted_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "shot.png")
            touch(root / "shot.v2.png")

            result = runner.invoke(
                app, ["image", "png2jpg", str(root / "shot"), str(root / "shot.v2"), "-q", "90"]
            )

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertTrue((root / "shot.jpg").is_file())
            self.assertTrue((root / "shot.v2.jpg").is_file())
            self.assertIn(f"{root / 'shot.png'} -> {root / 'shot.jpg'}", result.stdout)
            self.assertRegex(result.stdout, r"(magick|ffmpeg): 2 converted")

    def test_mismatched_source_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "b.webp", b"not really webp")

            result = runner.invoke(app, ["image", "png2jpg", str(root / "b.webp")])

            self.assertEqual(result.exit_code, 2, combined(result))
            self.assertIn("b.webp", combined(result))
            self.assertEqual([p.name for p in root.iterdir()], ["b.webp"])

    def test_directory_mode_converts_png_to_jpg_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            touch(root / "a.png")

            result = runner.invoke(app, ["image", "convert", "--to", "jpg", "-d", str(root)])

            self.assertEqual(result.exit_code, 0, combined(result))
            self.assertTrue((root / "a.jpg").stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()

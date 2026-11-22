"""Tests for ``jzr image resize``.

Fixtures are built with Pillow in temporary folders and every output is checked
by reopening it. Each test runs with the temporary folder as the working
directory, so the paths printed in result lines are the short names given on
the command line.
"""

from __future__ import annotations

import os
from pathlib import Path
import random
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app

runner = CliRunner()

SIZE_HINT = "give one size"
ANIMATED = "animated images are not resized yet; jzr image convert keeps every frame"


def resize(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", "resize", *args])


def image_help(*args: str) -> str:
    result = runner.invoke(app, ["image", *args])
    return " ".join(result.stdout.split())


def make(
    path: str | Path,
    width: int = 100,
    height: int = 80,
    colour: tuple[int, ...] = (200, 30, 30),
    mode: str = "RGB",
) -> Path:
    """Save a plain image; the format follows the file extension."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, (width, height), colour).save(target)
    return target


def size_of(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def format_of(path: str | Path) -> str | None:
    with Image.open(path) as image:
        return image.format


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 40) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def block_mean(path: str | Path, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Average RGB colour of a block, so JPEG blending does not matter."""
    with Image.open(path) as image:
        block = image.convert("RGB").crop(box).resize((1, 1), Image.Resampling.BOX)
        return block.getpixel((0, 0))


class ResizeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-resize-cli-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)


class SizeOptionTests(ResizeTestCase):
    def check(self, args: list[str], expected: tuple[int, int], source=(100, 80)) -> None:
        make("a.png", *source)
        result = resize("a.png", *args)
        self.assertEqual(result.exit_code, 0, result.output)
        name = f"a_{expected[0]}x{expected[1]}.png"
        self.assertEqual(names(self.root), sorted(["a.png", name]), result.output)
        self.assertEqual(size_of(name), expected)

    def test_width_alone_lets_the_height_follow(self) -> None:
        self.check(["--width", "50"], (50, 40))

    def test_height_alone_lets_the_width_follow(self) -> None:
        self.check(["--height", "40"], (50, 40))

    def test_height_follows_with_rounding(self) -> None:
        self.check(["--width", "30"], (30, 24), source=(100, 80))
        make("b.png", 100, 77)
        result = resize("b.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("b_50x38.png"), (50, 38))

    def test_width_and_height_fit_inside_the_box_when_the_width_limits(self) -> None:
        self.check(["--width", "50", "--height", "50"], (50, 40))

    def test_width_and_height_fit_inside_the_box_when_the_height_limits(self) -> None:
        self.check(["--width", "90", "--height", "20"], (25, 20))

    def test_percent_scales_both_sides(self) -> None:
        self.check(["--percent", "50"], (50, 40))

    def test_percent_may_be_fractional_and_rounds_each_side(self) -> None:
        self.check(["--percent", "33.3"], (33, 27))

    def test_max_side_limits_the_longer_side_of_a_landscape_image(self) -> None:
        self.check(["--max-side", "50"], (50, 40))

    def test_max_side_limits_the_longer_side_of_a_portrait_image(self) -> None:
        self.check(["--max-side", "50"], (40, 50), source=(80, 100))

    def test_a_computed_side_never_drops_below_one_pixel(self) -> None:
        self.check(["--width", "5"], (5, 1), source=(100, 10))

    def test_percent_of_a_thin_image_keeps_one_pixel(self) -> None:
        self.check(["--percent", "1"], (1, 1), source=(100, 10))

    def test_percent_of_1000_is_accepted_with_upscale(self) -> None:
        self.check(["--percent", "1000", "--upscale"], (100, 100), source=(10, 10))

    def test_the_picture_is_scaled_not_cropped(self) -> None:
        image = Image.new("RGB", (100, 80), (255, 0, 0))
        image.paste((0, 0, 255), (50, 0, 100, 80))
        image.save("halves.png")
        result = resize("halves.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        out = "halves_50x40.png"
        self.assertTrue(near(block_mean(out, (0, 0, 20, 40)), (255, 0, 0), 5))
        self.assertTrue(near(block_mean(out, (30, 0, 50, 40)), (0, 0, 255), 5))

    def test_result_line_shows_both_sizes(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines()[0], "a.png -> a_50x40.png (100x80 -> 50x40)"
        )

    def test_summary_counts_the_resized_files(self) -> None:
        make("a.png")
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 2 resized")

    def test_the_source_file_is_left_untouched(self) -> None:
        make("a.png")
        before = Path("a.png").read_bytes()
        result = resize("a.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(Path("a_50x40.png").is_file())
        self.assertEqual(Path("a.png").read_bytes(), before)

    def test_files_are_scaled_by_the_same_ratio_each(self) -> None:
        make("wide.png", 300, 100)
        make("tall.png", 100, 300)
        result = resize("wide.png", "tall.png", "--max-side", "30")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("wide_30x10.png"), (30, 10))
        self.assertEqual(size_of("tall_10x30.png"), (10, 30))


class UsageErrorTests(ResizeTestCase):
    def setUp(self) -> None:
        super().setUp()
        make("a.png")

    def assert_usage_error(self, *args: str, hint: str | None = None) -> str:
        result = resize(*args)
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertNotIn("No such command", result.stderr)
        if hint is not None:
            self.assertIn(hint, result.stderr)
        self.assertEqual(names(self.root), ["a.png"], "nothing may be written")
        return result.stderr

    def test_no_size_is_a_usage_error(self) -> None:
        self.assertIn(SIZE_HINT, self.assert_usage_error("a.png"))

    def test_width_with_percent_is_a_usage_error(self) -> None:
        self.assertIn(SIZE_HINT, self.assert_usage_error("a.png", "--width", "50", "--percent", "50"))

    def test_height_with_max_side_is_a_usage_error(self) -> None:
        self.assertIn(SIZE_HINT, self.assert_usage_error("a.png", "--height", "50", "--max-side", "50"))

    def test_percent_with_max_side_is_a_usage_error(self) -> None:
        self.assertIn(SIZE_HINT, self.assert_usage_error("a.png", "--percent", "50", "--max-side", "50"))

    def test_width_with_max_side_is_a_usage_error(self) -> None:
        self.assertIn(SIZE_HINT, self.assert_usage_error("a.png", "--width", "50", "--max-side", "50"))

    def test_width_and_height_together_are_one_size(self) -> None:
        result = resize("a.png", "--width", "50", "--height", "50")
        self.assertEqual(result.exit_code, 0, result.output)

    def test_bad_percent_is_a_usage_error(self) -> None:
        for value in ("0", "-5", "1000.5", "1001", "abc"):
            with self.subTest(value=value):
                self.assert_usage_error("a.png", "--percent", value, hint="--percent")

    def test_bad_whole_numbers_are_usage_errors(self) -> None:
        for option in ("--width", "--height", "--max-side"):
            for value in ("0", "-3", "2.5", "x"):
                with self.subTest(option=option, value=value):
                    self.assert_usage_error("a.png", option, value, hint=option)

    def test_output_that_disagrees_with_to_is_a_usage_error(self) -> None:
        stderr = self.assert_usage_error("a.png", "--width", "50", "-o", "out.jpg", "--to", "png")
        self.assertIn("--output out.jpg does not end in .png.", stderr)

    def test_output_that_agrees_with_to_is_accepted(self) -> None:
        result = resize("a.png", "--width", "50", "-o", "out.jpg", "--to", "jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("out.jpg"), "JPEG")

    def test_output_with_outdir_is_a_usage_error(self) -> None:
        self.assert_usage_error(
            "a.png", "--width", "50", "-o", "out.png", "--outdir", "o",
            hint="--output and --outdir cannot be used together.",
        )

    def test_output_with_two_sources_is_a_usage_error(self) -> None:
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50", "-o", "out.png")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output names one file", result.stderr)
        self.assertEqual(names(self.root), ["a.png", "b.png"])

    def test_dir_with_sources_is_a_usage_error(self) -> None:
        self.assert_usage_error(
            "a.png", "--width", "50", "--dir", ".",
            hint="--dir and --recursive only apply when no SOURCES are listed.",
        )

    def test_unsupported_format_is_a_usage_error(self) -> None:
        self.assert_usage_error("a.png", "--width", "50", "--to", "xyz", hint="Unsupported extension: xyz")

    def test_bad_background_is_a_usage_error(self) -> None:
        stderr = self.assert_usage_error("a.png", "--width", "50", "--background", "red")
        self.assertIn("'red' is not a colour", stderr)

    def test_bad_quality_is_a_usage_error(self) -> None:
        self.assert_usage_error("a.png", "--width", "50", "-q", "0", hint="--quality")
        self.assert_usage_error("a.png", "--width", "50", "-q", "101", hint="--quality")

    def test_the_backend_option_does_not_exist(self) -> None:
        self.assert_usage_error("a.png", "--width", "50", "--backend", "pillow", hint="No such option")


class EnlargeTests(ResizeTestCase):
    def setUp(self) -> None:
        super().setUp()
        make("small.png", 640, 480)

    def test_a_larger_size_is_skipped_with_the_hint(self) -> None:
        result = resize("small.png", "--width", "800")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(),
            ["skip small.png: already 640x480; not enlarged (add --upscale)"],
        )
        self.assertEqual(result.stdout.splitlines(), ["pillow: 1 skipped"])
        self.assertEqual(names(self.root), ["small.png"])

    def test_every_size_option_skips_when_it_would_enlarge(self) -> None:
        for args in (
            ["--height", "600"],
            ["--percent", "150"],
            ["--max-side", "1000"],
            ["--width", "800", "--height", "800"],
        ):
            with self.subTest(args=args):
                result = resize("small.png", *args)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(
                    "skip small.png: already 640x480; not enlarged (add --upscale)",
                    result.stderr,
                )
                self.assertEqual(names(self.root), ["small.png"])

    def test_a_box_larger_on_one_side_only_still_shrinks(self) -> None:
        result = resize("small.png", "--width", "320", "--height", "1000")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("small_320x240.png"), (320, 240))

    def test_upscale_enlarges_and_notes_it_in_the_result_line(self) -> None:
        result = resize("small.png", "--width", "800", "--upscale")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines()[0],
            "small.png -> small_800x600.png (640x480 -> 800x600, enlarged; may look soft)",
        )
        self.assertEqual(size_of("small_800x600.png"), (800, 600))
        self.assertEqual(result.stderr, "")

    def test_upscale_with_a_shrinking_size_adds_no_note(self) -> None:
        result = resize("small.png", "--width", "320", "--upscale")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("enlarged", result.output)

    def test_an_unchanged_size_is_skipped_even_with_upscale(self) -> None:
        for extra in ([], ["--upscale"]):
            with self.subTest(extra=extra):
                result = resize("small.png", "--width", "640", *extra)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(result.stderr.splitlines(), ["skip small.png: already 640x480"])
                self.assertEqual(names(self.root), ["small.png"])

    def test_skips_do_not_stop_the_other_files(self) -> None:
        make("big.png", 1000, 500)
        result = resize("small.png", "big.png", "--width", "800")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("big_800x400.png"), (800, 400))
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 resized, 1 skipped")


class FormatTests(ResizeTestCase):
    def setUp(self) -> None:
        super().setUp()
        make("a.png")

    def test_to_changes_the_format_and_the_extension(self) -> None:
        result = resize("a.png", "--width", "50", "--to", "jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.png", "a_50x40.jpg"])
        self.assertEqual(format_of("a_50x40.jpg"), "JPEG")
        self.assertEqual(size_of("a_50x40.jpg"), (50, 40))
        self.assertEqual(
            result.stdout.splitlines()[0], "a.png -> a_50x40.jpg (100x80 -> 50x40)"
        )

    def test_to_accepts_other_convert_targets(self) -> None:
        for target, expected in (("bmp", "BMP"), ("webp", "WEBP"), ("tif", "TIFF"), ("gif", "GIF")):
            with self.subTest(target=target):
                result = resize("a.png", "--width", "50", "--to", target)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(format_of(f"a_50x40.{target}"), expected)
                self.assertEqual(size_of(f"a_50x40.{target}"), (50, 40))

    def test_without_to_the_format_of_the_source_is_kept(self) -> None:
        make("p.jpg")
        result = resize("p.jpg", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("p_50x40.jpg"), "JPEG")

    def test_the_extension_is_kept_as_written(self) -> None:
        make("IMG.PNG")
        result = resize("IMG.PNG", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("IMG_50x40.PNG", names(self.root))
        self.assertEqual(format_of("IMG_50x40.PNG"), "PNG")

    def test_output_names_the_file_exactly_and_sets_the_format(self) -> None:
        result = resize("a.png", "--width", "50", "-o", "small.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.png", "small.jpg"])
        self.assertEqual(format_of("small.jpg"), "JPEG")
        self.assertEqual(size_of("small.jpg"), (50, 40))
        self.assertEqual(result.stdout.splitlines()[0], "a.png -> small.jpg (100x80 -> 50x40)")

    def test_output_in_another_folder_is_created(self) -> None:
        result = resize("a.png", "--width", "50", "-o", str(Path("deep") / "x.png"))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of(Path("deep") / "x.png"), (50, 40))

    def test_listed_sources_with_resized_looking_names_are_still_resized(self) -> None:
        make("c_20x20.png")
        result = resize("c_20x20.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("c_20x20_50x40.png"), (50, 40))

    def test_two_sources_with_the_same_planned_name_do_not_overwrite_each_other(self) -> None:
        make("a.jpg", 100, 80, (10, 10, 200))
        result = resize("a.png", "a.jpg", "--width", "50", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "a.png", "a_50x40-2.png", "a_50x40.png"])
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 2 resized")

    def test_quality_changes_the_size_of_a_jpeg(self) -> None:
        generator = random.Random(7)
        noisy = Image.new("RGB", (200, 200))
        noisy.putdata(
            [tuple(generator.randrange(256) for _ in range(3)) for _ in range(200 * 200)]
        )
        noisy.save("noise.jpg")
        resize("noise.jpg", "--width", "150", "-q", "10", "-o", "low.jpg")
        resize("noise.jpg", "--width", "150", "-q", "95", "-o", "high.jpg")
        self.assertLess(Path("low.jpg").stat().st_size, Path("high.jpg").stat().st_size)


class PlacementTests(ResizeTestCase):
    def test_outdir_collects_the_outputs_and_is_created(self) -> None:
        make("a.png")
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.png", "b.png", "out"])
        self.assertEqual(names(self.root / "out"), ["a_50x40.png", "b_50x40.png"])

    def test_outputs_land_beside_their_sources_by_default(self) -> None:
        make(Path("one") / "a.png")
        make(Path("two") / "b.png")
        result = resize(str(Path("one") / "a.png"), str(Path("two") / "b.png"), "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "one"), ["a.png", "a_50x40.png"])
        self.assertEqual(names(self.root / "two"), ["b.png", "b_50x40.png"])

    def test_a_directory_scan_resizes_images_and_skips_resized_names(self) -> None:
        make("a.png")
        make("b.jpg")
        make("c_10x10.png", 100, 80)
        (self.root / "notes.txt").write_text("not an image", encoding="utf-8")
        result = resize("--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            names(self.root),
            ["a.png", "a_50x40.png", "b.jpg", "b_50x40.jpg", "c_10x10.png", "notes.txt"],
        )
        self.assertNotIn("c_10x10", result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 2 resized")

    def test_the_scan_skip_matches_only_a_trailing_size(self) -> None:
        make("shot_2x2_final.png")
        make("v2.png")
        result = resize("--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("shot_2x2_final_50x40.png", names(self.root))
        self.assertIn("v2_50x40.png", names(self.root))

    def test_dir_selects_the_folder_to_scan(self) -> None:
        make(Path("photos") / "a.png")
        make("elsewhere.png")
        result = resize("--dir", "photos", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "photos"), ["a.png", "a_50x40.png"])
        self.assertEqual(names(self.root), ["elsewhere.png", "photos"])

    def test_pattern_filters_the_scan(self) -> None:
        make("keep_a.png")
        make("keep_b.png")
        make("other.png")
        result = resize("--pattern", "^keep", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            names(self.root),
            ["keep_a.png", "keep_a_50x40.png", "keep_b.png", "keep_b_50x40.png", "other.png"],
        )

    def test_a_scan_ignores_subfolders_unless_recursive(self) -> None:
        make("top.png")
        make(Path("sub") / "deep.png")
        result = resize("--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.png"])

    def test_recursive_resizes_beside_each_source(self) -> None:
        make("top.png")
        make(Path("sub") / "deep.png")
        result = resize("--recursive", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.png", "deep_50x40.png"])
        self.assertIn("top_50x40.png", names(self.root))

    def test_recursive_with_outdir_mirrors_the_folders(self) -> None:
        make(Path("pics") / "top.png")
        make(Path("pics") / "sub" / "deep.png")
        result = resize("--dir", "pics", "--recursive", "--outdir", "out", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of(Path("out") / "top_50x40.png"), (50, 40))
        self.assertEqual(size_of(Path("out") / "sub" / "deep_50x40.png"), (50, 40))
        self.assertEqual(names(self.root / "pics"), ["sub", "top.png"])
        self.assertEqual(names(self.root / "pics" / "sub"), ["deep.png"])

    def test_nothing_to_resize_is_reported(self) -> None:
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        result = resize("--width", "50")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("No matching files.", result.stderr)


class ExistingOutputTests(ResizeTestCase):
    def setUp(self) -> None:
        super().setUp()
        make("a.png")
        Path("a_50x40.png").write_bytes(b"old")

    def test_an_existing_target_is_skipped_and_left_alone(self) -> None:
        result = resize("a.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), ["skip a.png: target exists (use --overwrite)"]
        )
        self.assertEqual(Path("a_50x40.png").read_bytes(), b"old")
        self.assertEqual(result.stdout.splitlines(), ["pillow: 1 skipped"])

    def test_overwrite_replaces_the_target(self) -> None:
        result = resize("a.png", "--width", "50", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_50x40.png"), (50, 40))

    def test_an_existing_output_file_is_skipped_too(self) -> None:
        Path("mine.png").write_bytes(b"old")
        result = resize("a.png", "--width", "50", "-o", "mine.png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a.png: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("mine.png").read_bytes(), b"old")

    def test_the_source_is_never_its_own_output(self) -> None:
        before = Path("a.png").read_bytes()
        result = resize("a.png", "--width", "50", "-o", "a.png", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a.png: source is the target", result.stderr)
        self.assertEqual(Path("a.png").read_bytes(), before)

    def test_a_skipped_target_does_not_stop_the_next_file(self) -> None:
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("b_50x40.png"), (50, 40))
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 resized, 1 skipped")


class DryRunTests(ResizeTestCase):
    def test_dry_run_writes_nothing_and_prints_the_planned_names(self) -> None:
        make("a.png")
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            ["a.png -> a_50x40.png", "b.png -> b_50x40.png", "pillow: 2 planned"],
        )
        self.assertEqual(names(self.root), ["a.png", "b.png"])

    def test_dry_run_plans_the_actual_size_of_each_image(self) -> None:
        make("wide.png", 300, 100)
        make("tall.png", 100, 300)
        result = resize("wide.png", "tall.png", "--max-side", "30", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("wide.png -> wide_30x10.png", result.stdout)
        self.assertIn("tall.png -> tall_10x30.png", result.stdout)

    def test_dry_run_uses_to_and_outdir(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50", "--to", "jpg", "--outdir", "out", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines()[0], f"a.png -> {Path('out') / 'a_50x40.jpg'}")
        self.assertEqual(names(self.root), ["a.png"])

    def test_dry_run_still_reports_skips(self) -> None:
        make("small.png", 40, 30)
        make("a.png")
        result = resize("small.png", "a.png", "--width", "50", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "skip small.png: already 40x30; not enlarged (add --upscale)", result.stderr
        )
        self.assertIn("a.png -> a_50x40.png", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 planned, 1 skipped")

    def test_dry_run_with_upscale_plans_the_enlarged_name(self) -> None:
        make("small.png", 40, 30)
        result = resize("small.png", "--width", "80", "--upscale", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("small.png -> small_80x60.png", result.stdout)
        self.assertEqual(names(self.root), ["small.png"])

    def test_dry_run_reports_an_existing_target(self) -> None:
        make("a.png")
        Path("a_50x40.png").write_bytes(b"old")
        result = resize("a.png", "--width", "50", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a.png: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("a_50x40.png").read_bytes(), b"old")

    def test_dry_run_reports_an_unreadable_file(self) -> None:
        Path("junk.png").write_bytes(b"this is not an image")
        result = resize("junk.png", "--width", "50", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("fail junk.png:", result.stderr)
        self.assertEqual(names(self.root), ["junk.png"])


class FailureTests(ResizeTestCase):
    def test_a_junk_file_fails_alone_and_the_others_are_resized(self) -> None:
        make("a.png")
        Path("junk.png").write_bytes(b"this is not an image")
        make("z.png")
        result = resize("a.png", "junk.png", "z.png", "--width", "50")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            names(self.root), ["a.png", "a_50x40.png", "junk.png", "z.png", "z_50x40.png"]
        )
        self.assertEqual(size_of("a_50x40.png"), (50, 40))
        self.assertEqual(size_of("z_50x40.png"), (50, 40))
        failures = [line for line in result.stderr.splitlines() if line.startswith("fail ")]
        self.assertEqual(len(failures), 1, result.stderr)
        self.assertTrue(failures[0].startswith("fail junk.png: "), failures[0])
        self.assertIn("not a readable image", failures[0])
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 failed, 2 resized")

    def test_an_animated_gif_fails_alone_with_the_message(self) -> None:
        frames = [Image.new("RGB", (40, 30), colour) for colour in ((255, 0, 0), (0, 0, 255))]
        frames[0].save("anim.gif", save_all=True, append_images=frames[1:], duration=100)
        make("a.png")
        result = resize("anim.gif", "a.png", "--width", "20")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"fail anim.gif: {ANIMATED}", result.stderr.splitlines())
        self.assertEqual(names(self.root), ["a.png", "a_20x16.png", "anim.gif"])
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 failed, 1 resized")

    def test_a_dry_run_already_reports_an_animated_gif_as_failed(self) -> None:
        frames = [Image.new("RGB", (40, 30), colour) for colour in ((255, 0, 0), (0, 0, 255))]
        frames[0].save("anim.gif", save_all=True, append_images=frames[1:], duration=100)
        result = resize("anim.gif", "--width", "20", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"fail anim.gif: {ANIMATED}", result.stderr.splitlines())
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 failed")

    def test_a_single_frame_gif_is_resized(self) -> None:
        Image.new("RGB", (40, 30), (255, 0, 0)).save("still.gif")
        result = resize("still.gif", "--width", "20")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("still_20x15.gif"), (20, 15))

    def test_a_failed_file_leaves_no_partial_output(self) -> None:
        Path("junk.png").write_bytes(b"this is not an image")
        result = resize("junk.png", "--width", "50")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(names(self.root), ["junk.png"])

    def test_only_failures_give_exit_one(self) -> None:
        Path("junk.png").write_bytes(b"this is not an image")
        result = resize("junk.png", "--width", "50")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stdout.splitlines()[-1], "pillow: 1 failed")


class NoteTests(ResizeTestCase):
    QUALITY_PNG = "note: .png is lossless; --quality is ignored"
    BACKGROUND_PNG = "note: .png keeps transparency; --background is ignored"

    def test_quality_for_a_lossless_target_is_noted_once_per_run(self) -> None:
        make("a.png")
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50", "-q", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)

    def test_quality_note_comes_with_to_for_a_lossless_target(self) -> None:
        make("a.jpg")
        make("b.jpg")
        result = resize("a.jpg", "b.jpg", "--width", "50", "--to", "png", "-q", "50")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)

    def test_quality_note_is_for_the_first_file_it_applies_to(self) -> None:
        make("a.jpg")
        make("b.png")
        make("c.png")
        result = resize("a.jpg", "b.png", "c.png", "--width", "50", "-q", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)

    def test_no_quality_note_for_a_lossy_target(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50", "--to", "jpg", "-q", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--quality", result.output)

    def test_no_quality_note_without_the_option(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--quality", result.output)

    def test_background_for_a_target_that_keeps_transparency_is_noted_once(self) -> None:
        make("a.png")
        make("b.png")
        result = resize("a.png", "b.png", "--width", "50", "--background", "white")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(self.BACKGROUND_PNG), 1, result.stderr)

    def test_background_note_is_for_the_first_file_it_applies_to(self) -> None:
        make("a.jpg")
        make("b.png")
        make("c.png")
        result = resize("a.jpg", "b.png", "c.png", "--width", "50", "--background", "white")
        self.assertEqual(result.stderr.count(self.BACKGROUND_PNG), 1, result.stderr)

    def test_no_background_note_for_a_target_without_transparency(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50", "--to", "jpg", "--background", "white")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--background", result.output)

    def test_both_notes_can_appear_in_one_run(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50", "-q", "50", "--background", "white")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)
        self.assertEqual(result.stderr.count(self.BACKGROUND_PNG), 1, result.stderr)

    def test_notes_are_printed_in_a_dry_run_too(self) -> None:
        make("a.png")
        result = resize("a.png", "--width", "50", "-q", "50", "--dry-run")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)

    def test_background_colour_fills_transparent_pixels_in_a_jpeg(self) -> None:
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save("clear.png")
        result = resize("clear.png", "--width", "20", "--to", "jpg", "--background", "#0000ff")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(near(block_mean("clear_20x20.jpg", (4, 4, 16, 16)), (0, 0, 255), 8))

    def test_without_background_transparent_pixels_turn_white_in_a_jpeg(self) -> None:
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save("clear.png")
        result = resize("clear.png", "--width", "20", "--to", "jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(near(block_mean("clear_20x20.jpg", (4, 4, 16, 16)), (255, 255, 255), 8))

    def test_transparency_survives_in_a_png(self) -> None:
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save("clear.png")
        result = resize("clear.png", "--width", "20")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("clear_20x20.png") as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((10, 10))[3], 0)


class OrientationAndMetadataTests(ResizeTestCase):
    def sideways(self, name: str = "side.jpg") -> None:
        """A 40x20 stored image, marked Orientation 6: upright it is 20x40,
        with the stored left half (red) on top and the right half (blue) below."""
        image = Image.new("RGB", (40, 20), (255, 0, 0))
        image.paste((0, 0, 255), (20, 0, 40, 20))
        exif = Image.Exif()
        exif[0x0112] = 6
        image.save(name, "JPEG", quality=95, exif=exif)

    def test_width_means_the_upright_width(self) -> None:
        self.sideways()
        result = resize("side.jpg", "--width", "10")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["side.jpg", "side_10x20.jpg"])
        self.assertEqual(size_of("side_10x20.jpg"), (10, 20))
        self.assertEqual(
            result.stdout.splitlines()[0],
            "side.jpg -> side_10x20.jpg (20x40 -> 10x20)",
        )

    def test_height_and_max_side_use_the_upright_size_too(self) -> None:
        self.sideways()
        resize("side.jpg", "--height", "20")
        resize("side.jpg", "--max-side", "20")
        self.assertEqual(size_of("side_10x20.jpg"), (10, 20))

    def test_the_pixels_are_turned_upright(self) -> None:
        self.sideways()
        resize("side.jpg", "--width", "10")
        out = "side_10x20.jpg"
        self.assertTrue(near(block_mean(out, (2, 2, 8, 8)), (255, 0, 0)))
        self.assertTrue(near(block_mean(out, (2, 12, 8, 18)), (0, 0, 255)))

    def test_upright_size_decides_whether_the_image_would_be_enlarged(self) -> None:
        self.sideways()
        result = resize("side.jpg", "--width", "30")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "skip side.jpg: already 20x40; not enlarged (add --upscale)", result.stderr
        )

    def photo(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Acme"
        Image.new("RGB", (100, 80), (10, 120, 200)).save("photo.jpg", "JPEG", exif=exif)

    def test_exif_is_carried_over_by_default(self) -> None:
        self.photo()
        result = resize("photo.jpg", "--width", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("photo_50x40.jpg") as image:
            self.assertEqual(image.getexif().get(0x010F), "Acme")

    def test_strip_removes_the_exif(self) -> None:
        self.photo()
        result = resize("photo.jpg", "--width", "50", "--strip")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("photo_50x40.jpg") as image:
            self.assertEqual(dict(image.getexif()), {})
        self.assertEqual(size_of("photo_50x40.jpg"), (50, 40))

    def test_strip_applies_when_the_format_changes(self) -> None:
        self.photo()
        result = resize("photo.jpg", "--width", "50", "--strip", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("photo_50x40.png") as image:
            self.assertNotIn(0x010F, image.getexif())


class HelpTests(unittest.TestCase):
    def test_resize_is_listed_in_the_image_help(self) -> None:
        result = runner.invoke(app, ["image", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        commands = result.stdout.split("Commands:", 1)[1]
        self.assertRegex(commands, r"(?m)^  resize\s+Make images smaller")

    def test_help_has_the_command_text_and_the_option_texts(self) -> None:
        result = runner.invoke(app, ["image", "resize", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        text = " ".join(result.stdout.split())
        for expected in (
            "Make images smaller (or, with --upscale, larger).",
            "Give one size: --width, --height, both (the image fits inside that box), "
            "--percent or --max-side. Proportions are always kept. Each file is written as "
            "<name>_<W>x<H>.<ext> beside its source unless -o or --outdir says otherwise.",
            "--width N",
            "Width in pixels; the height follows.",
            "--height N",
            "Height in pixels; the width follows.",
            "--percent P",
            "Scale both sides by this percentage.",
            "--max-side N",
            "Longest side in pixels.",
            "--upscale",
            "Allow making images larger.",
            "Write in this format instead of the source's.",
            "Leave out EXIF, XMP and comments; the colour profile is kept.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_help_lists_every_option_and_no_backend(self) -> None:
        text = image_help("resize", "-h")
        for option in (
            "-d, --dir",
            "-p, --pattern",
            "-r, --recursive",
            "-o, --output",
            "--outdir",
            "-t, --to",
            "-q, --quality",
            "--strip",
            "--background",
            "--overwrite",
            "--dry-run",
        ):
            with self.subTest(option=option):
                self.assertIn(option, text)
        self.assertNotIn("--backend", text)

    def test_options_are_listed_in_the_documented_order(self) -> None:
        text = image_help("resize", "-h")
        order = [
            "--outdir",
            "--width",
            "--height",
            "--percent",
            "--max-side",
            "--upscale",
            "--to",
            "--quality",
            "--strip",
            "--background",
            "--overwrite",
            "--dry-run",
        ]
        positions = [text.index(option, text.index("Options:")) for option in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

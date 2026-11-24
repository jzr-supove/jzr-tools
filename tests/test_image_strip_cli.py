"""Tests for ``jzr image strip``.

The files are assembled from raw segments and chunks (see strip_fixtures), so
each test knows which bytes the cleaned copy must hold. Each test runs with a
temporary folder as the working directory, so the paths printed in result
lines are the short names given on the command line.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image

from jzr_tools.cli import app
from jzr_tools.core.media import format_size
from tests import strip_fixtures as fx

runner = CliRunner()

DAMAGED = "damaged or unsupported container; nothing written"
UNREADABLE = "some metadata could not be read safely; nothing written"
ANIMATED = "animated images are not supported"


def heic_text(extension: str) -> str:
    return (
        f".{extension} metadata cannot be removed without re-encoding; "
        "use jzr image compress --strip (or convert)"
    )


def strip(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", "strip", *args])


def image_help(*args: str) -> str:
    result = runner.invoke(app, ["image", *args])
    assert result.exit_code == 0, result.output
    return " ".join(result.stdout.split())


BODY = fx.jpeg_body(fx.plain_jpeg())

# A JPEG with camera EXIF and a GPS position: the copy has neither.
DIRTY_JPG = fx.build_jpeg([fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(None))], BODY)
CLEAN_JPG = fx.build_jpeg([fx.JFIF_CLEAN], BODY)
SAVED_JPG = format_size(len(DIRTY_JPG) - len(CLEAN_JPG))
LABELS_JPG = "EXIF, GPS"

# A photo taken sideways: the copy keeps only the orientation.
SIDEWAYS_JPG = fx.build_jpeg([fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(6))], BODY)
SIDEWAYS_CLEAN = fx.build_jpeg(
    [fx.JFIF_CLEAN, fx.exif_app1(fx.orientation_only_tiff(6))], BODY
)
SAVED_SIDEWAYS = format_size(len(SIDEWAYS_JPG) - len(SIDEWAYS_CLEAN))

_IHDR, _IDAT, _IEND = fx.png_header_and_data(fx.plain_png())
DIRTY_PNG = fx.build_png(_IHDR, fx.text_chunk(), _IDAT, _IEND)
CLEAN_PNG = fx.build_png(_IHDR, _IDAT, _IEND)
SAVED_PNG = format_size(len(DIRTY_PNG) - len(CLEAN_PNG))

_VP8L, _SIMPLE_WEBP = fx.lossless_webp_image()
DIRTY_WEBP = fx.riff(
    fx.vp8x(fx.VP8X_EXIF, 40, 30), _VP8L, fx.webp_chunk(b"EXIF", fx.photo_tiff(None, gps=False))
)
CLEAN_WEBP = fx.riff(fx.vp8x(0, 40, 30), _VP8L)
SAVED_WEBP = format_size(len(DIRTY_WEBP) - len(CLEAN_WEBP))

PROFILE_JPG = fx.build_jpeg([fx.JFIF_CLEAN, fx.icc_app2()], BODY)
PROFILE_FREE_JPG = CLEAN_JPG
SAVED_PROFILE = format_size(len(PROFILE_JPG) - len(PROFILE_FREE_JPG))


def write(path: str | Path, data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def dirty(path: str | Path) -> Path:
    return write(path, DIRTY_JPG)


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def stdout_lines(result) -> list[str]:  # noqa: ANN001
    return result.stdout.splitlines()


def cleaned_line(source: str, target: str) -> str:
    return f"{source} -> {target} (removed {LABELS_JPG}; {SAVED_JPG} smaller)"


def planned_line(source: str, target: str) -> str:
    return f"{source} -> {target} (would remove {LABELS_JPG}; {SAVED_JPG} smaller)"


class StripTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-strip-cli-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)


class DefaultsTests(StripTestCase):
    def test_a_file_is_cleaned_into_a_copy_beside_its_source(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "a_clean.jpg"])
        self.assertEqual(
            stdout_lines(result), [cleaned_line("a.jpg", "a_clean.jpg"), "strip: 1 cleaned"]
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(Path("a_clean.jpg").read_bytes(), CLEAN_JPG)

    def test_the_source_file_is_left_untouched(self) -> None:
        dirty("a.jpg")
        strip("a.jpg")
        self.assertEqual(Path("a.jpg").read_bytes(), DIRTY_JPG)

    def test_jpeg_png_and_webp_each_get_their_own_result_line(self) -> None:
        write("j.jpeg", DIRTY_JPG)
        write("p.png", DIRTY_PNG)
        write("w.webp", DIRTY_WEBP)
        result = strip("j.jpeg", "p.png", "w.webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                f"j.jpeg -> j_clean.jpeg (removed {LABELS_JPG}; {SAVED_JPG} smaller)",
                f"p.png -> p_clean.png (removed text; {SAVED_PNG} smaller)",
                f"w.webp -> w_clean.webp (removed EXIF; {SAVED_WEBP} smaller)",
                "strip: 3 cleaned",
            ],
        )
        self.assertEqual(Path("j_clean.jpeg").read_bytes(), CLEAN_JPG)
        self.assertEqual(Path("p_clean.png").read_bytes(), CLEAN_PNG)
        self.assertEqual(Path("w_clean.webp").read_bytes(), CLEAN_WEBP)

    def test_the_extension_is_kept_as_written(self) -> None:
        dirty("IMG.JPG")
        result = strip("IMG.JPG")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("IMG_clean.JPG", names(self.root))
        self.assertEqual(Path("IMG_clean.JPG").read_bytes(), CLEAN_JPG)

    def test_the_picture_and_the_orientation_of_a_sideways_photo_are_kept(self) -> None:
        write("side.jpg", SIDEWAYS_JPG)
        result = strip("side.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0],
            f"side.jpg -> side_clean.jpg (removed {LABELS_JPG}; {SAVED_SIDEWAYS} smaller)",
        )
        data = Path("side_clean.jpg").read_bytes()
        self.assertEqual(data, SIDEWAYS_CLEAN)
        self.assertEqual(fx.pixels(data), fx.pixels(SIDEWAYS_JPG))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(dict(image.getexif()), {0x0112: 6})

    def test_a_file_with_nothing_to_remove_is_skipped_and_nothing_is_written(self) -> None:
        write("clean.jpg", CLEAN_JPG)
        result = strip("clean.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip clean.jpg: no metadata to remove"])
        self.assertEqual(stdout_lines(result), ["strip: 1 skipped"])
        self.assertEqual(names(self.root), ["clean.jpg"])

    def test_a_skipped_file_does_not_stop_the_next_one(self) -> None:
        write("clean.jpg", CLEAN_JPG)
        dirty("dirty.jpg")
        write("also_clean.png", CLEAN_PNG)
        result = strip("clean.jpg", "dirty.jpg", "also_clean.png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [cleaned_line("dirty.jpg", "dirty_clean.jpg"), "strip: 1 cleaned, 2 skipped"],
        )
        self.assertEqual(
            result.stderr.splitlines(),
            [
                "skip clean.jpg: no metadata to remove",
                "skip also_clean.png: no metadata to remove",
            ],
        )

    def test_cleaning_a_result_again_finds_nothing_to_remove(self) -> None:
        dirty("a.jpg")
        strip("a.jpg")
        again = strip("a_clean.jpg")
        self.assertEqual(again.exit_code, 0, again.output)
        self.assertEqual(again.stderr.splitlines(), ["skip a_clean.jpg: no metadata to remove"])
        self.assertEqual(names(self.root), ["a.jpg", "a_clean.jpg"])


class ColourProfileTests(StripTestCase):
    def test_the_colour_profile_is_kept_by_default(self) -> None:
        dirty_profile = fx.build_jpeg(
            [fx.JFIF_CLEAN, fx.icc_app2(), fx.exif_app1(fx.photo_tiff(None))], BODY
        )
        write("a.jpg", dirty_profile)
        result = strip("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("a_clean.jpg").read_bytes(), PROFILE_JPG)
        self.assertNotIn("colour profile", result.output)

    def test_no_color_profile_removes_it_too(self) -> None:
        write("a.jpg", PROFILE_JPG)
        result = strip("a.jpg", "--no-color-profile")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0],
            f"a.jpg -> a_clean.jpg (removed colour profile; {SAVED_PROFILE} smaller)",
        )
        self.assertEqual(Path("a_clean.jpg").read_bytes(), PROFILE_FREE_JPG)

    def test_a_profile_alone_is_nothing_to_remove_by_default(self) -> None:
        write("a.jpg", PROFILE_JPG)
        result = strip("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip a.jpg: no metadata to remove"])
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_the_option_applies_to_png_and_webp_profiles(self) -> None:
        png = fx.build_png(_IHDR, fx.iccp_chunk(), _IDAT, _IEND)
        webp = fx.riff(
            fx.vp8x(fx.VP8X_ICC, 40, 30), fx.webp_chunk(b"ICCP", fx.icc_profile()), _VP8L
        )
        write("a.png", png)
        write("a.webp", webp)
        result = strip("a.png", "a.webp", "--no-color-profile")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("a_clean.png").read_bytes(), CLEAN_PNG)
        self.assertEqual(Path("a_clean.webp").read_bytes(), CLEAN_WEBP)


class OutputTests(StripTestCase):
    def test_output_names_the_file_exactly(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "-o", "small.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "small.jpg"])
        self.assertEqual(stdout_lines(result)[0], cleaned_line("a.jpg", "small.jpg"))
        self.assertEqual(Path("small.jpg").read_bytes(), CLEAN_JPG)

    def test_output_with_several_sources_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        dirty("b.jpg")
        result = strip("a.jpg", "b.jpg", "-o", "out.jpg")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output names one file", result.stderr)
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg"])

    def test_output_with_outdir_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "-o", "out.jpg", "--outdir", "o")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output and --outdir cannot be used together.", result.stderr)

    def test_outdir_collects_the_copies_and_is_created(self) -> None:
        dirty("a.jpg")
        dirty("b.jpg")
        result = strip("a.jpg", "b.jpg", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "out"), ["a_clean.jpg", "b_clean.jpg"])
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg", "out"])
        self.assertEqual(
            stdout_lines(result)[0],
            cleaned_line("a.jpg", str(Path("out") / "a_clean.jpg")),
        )

    def test_recursive_with_outdir_mirrors_the_folders(self) -> None:
        dirty(Path("pics") / "top.jpg")
        dirty(Path("pics") / "sub" / "deep.jpg")
        result = strip("--dir", "pics", "--recursive", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual((Path("out") / "top_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual((Path("out") / "sub" / "deep_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(names(self.root / "pics"), ["sub", "top.jpg"])
        self.assertEqual(names(self.root / "pics" / "sub"), ["deep.jpg"])

    def test_sources_with_the_same_planned_name_do_not_overwrite_each_other(self) -> None:
        dirty(Path("x") / "a.jpg")
        write(Path("y") / "a.jpg", SIDEWAYS_JPG)
        result = strip(str(Path("x") / "a.jpg"), str(Path("y") / "a.jpg"), "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "out"), ["a_clean-2.jpg", "a_clean.jpg"])
        self.assertEqual((Path("out") / "a_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual((Path("out") / "a_clean-2.jpg").read_bytes(), SIDEWAYS_CLEAN)
        self.assertEqual(stdout_lines(result)[-1], "strip: 2 cleaned")

    def test_options_that_do_not_exist_are_rejected(self) -> None:
        dirty("a.jpg")
        for option in (("--quality", "50"), ("--to", "png"), ("--backend", "pillow")):
            with self.subTest(option=option):
                result = strip("a.jpg", *option)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("No such option", result.stderr)


class ScanTests(StripTestCase):
    STRIPPABLE = {
        "jpg": DIRTY_JPG,
        "jpeg": DIRTY_JPG,
        "png": DIRTY_PNG,
        "webp": DIRTY_WEBP,
    }
    OTHERS = ("heic", "heif", "avif", "gif", "bmp", "tif", "tiff", "ico", "ppm")

    def populate(self) -> None:
        for extension, data in self.STRIPPABLE.items():
            write(f"pic_{extension}.{extension}", data)
        for extension in self.OTHERS:
            write(f"pic_{extension}.{extension}", b"x")
        Path("notes.txt").write_text("not an image", encoding="utf-8")

    def planned(self, result) -> set[str]:  # noqa: ANN001
        return {line.split(" (")[0] for line in stdout_lines(result) if " -> " in line}

    def test_a_scan_picks_only_jpeg_png_and_webp(self) -> None:
        self.populate()
        result = strip("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.planned(result),
            {f"pic_{e}.{e} -> pic_{e}_clean.{e}" for e in self.STRIPPABLE},
        )
        self.assertEqual(stdout_lines(result)[-1], "strip: 4 planned")
        self.assertEqual(result.stderr, "")

    def test_a_scan_cleans_beside_the_sources_and_skips_its_own_results(self) -> None:
        dirty("a.jpg")
        write("b.png", DIRTY_PNG)
        first = strip()
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(names(self.root), ["a.jpg", "a_clean.jpg", "b.png", "b_clean.png"])
        self.assertEqual(stdout_lines(first)[-1], "strip: 2 cleaned")
        second = strip()
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(names(self.root), ["a.jpg", "a_clean.jpg", "b.png", "b_clean.png"])
        self.assertEqual(
            sorted(second.stderr.splitlines()),
            [
                "skip a.jpg: target exists (use --overwrite)",
                "skip b.png: target exists (use --overwrite)",
            ],
        )
        self.assertEqual(stdout_lines(second), ["strip: 2 skipped"])

    def test_a_scan_that_finds_only_earlier_results_finds_nothing(self) -> None:
        dirty("a_clean.jpg")
        result = strip()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("No matching files.", result.stderr)
        self.assertEqual(names(self.root), ["a_clean.jpg"])

    def test_the_scan_skip_matches_only_a_trailing_clean(self) -> None:
        dirty("my_clean_notes.jpg")
        result = strip("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("my_clean_notes.jpg -> my_clean_notes_clean.jpg", result.stdout)

    def test_a_listed_file_is_cleaned_even_when_it_looks_like_a_result(self) -> None:
        dirty("a_clean.jpg")
        result = strip("a_clean.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a_clean.jpg", "a_clean_clean.jpg"])

    def test_nothing_to_clean_is_reported(self) -> None:
        write("a.bmp", b"x")
        Path("notes.txt").write_text("hello", encoding="utf-8")
        result = strip()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("No matching files.", result.stderr)

    def test_dir_selects_the_folder_to_scan(self) -> None:
        dirty(Path("photos") / "a.jpg")
        dirty("elsewhere.jpg")
        result = strip("--dir", "photos")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "photos"), ["a.jpg", "a_clean.jpg"])
        self.assertEqual(names(self.root), ["elsewhere.jpg", "photos"])

    def test_dir_with_sources_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--dir", ".")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(
            "--dir and --recursive only apply when no SOURCES are listed.", result.stderr
        )

    def test_recursive_with_sources_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--recursive")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(
            "--dir and --recursive only apply when no SOURCES are listed.", result.stderr
        )

    def test_pattern_filters_the_scan(self) -> None:
        dirty("keep_a.jpg")
        dirty("keep_b.jpg")
        dirty("other.jpg")
        result = strip("--pattern", "^keep")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            names(self.root),
            ["keep_a.jpg", "keep_a_clean.jpg", "keep_b.jpg", "keep_b_clean.jpg", "other.jpg"],
        )

    def test_pattern_filters_listed_sources_too(self) -> None:
        dirty("keep_a.jpg")
        dirty("other.jpg")
        result = strip("keep_a.jpg", "other.jpg", "--pattern", "^keep")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["keep_a.jpg", "keep_a_clean.jpg", "other.jpg"])

    def test_a_scan_ignores_subfolders_unless_recursive(self) -> None:
        dirty("top.jpg")
        dirty(Path("sub") / "deep.jpg")
        result = strip()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.jpg"])

    def test_recursive_cleans_beside_each_source(self) -> None:
        dirty("top.jpg")
        dirty(Path("sub") / "deep.jpg")
        result = strip("--recursive")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.jpg", "deep_clean.jpg"])
        self.assertIn("top_clean.jpg", names(self.root))


class ExistingTargetTests(StripTestCase):
    def setUp(self) -> None:
        super().setUp()
        dirty("a.jpg")
        Path("a_clean.jpg").write_bytes(b"old")

    def test_an_existing_target_is_skipped_and_left_alone(self) -> None:
        result = strip("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), ["skip a.jpg: target exists (use --overwrite)"]
        )
        self.assertEqual(Path("a_clean.jpg").read_bytes(), b"old")
        self.assertEqual(stdout_lines(result), ["strip: 1 skipped"])

    def test_overwrite_replaces_the_target(self) -> None:
        result = strip("a.jpg", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("a_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(stdout_lines(result)[-1], "strip: 1 cleaned")
        self.assertEqual(names(self.root), ["a.jpg", "a_clean.jpg"])

    def test_an_existing_output_file_is_skipped_too(self) -> None:
        Path("mine.jpg").write_bytes(b"old")
        result = strip("a.jpg", "-o", "mine.jpg")
        self.assertIn("skip a.jpg: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("mine.jpg").read_bytes(), b"old")

    def test_the_source_is_never_its_own_output(self) -> None:
        result = strip("a.jpg", "-o", "a.jpg")
        self.assertIn("skip a.jpg: source is the target", result.stderr)
        self.assertEqual(Path("a.jpg").read_bytes(), DIRTY_JPG)

    def test_a_skipped_target_does_not_stop_the_next_file(self) -> None:
        dirty("b.jpg")
        result = strip("a.jpg", "b.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("b_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(stdout_lines(result)[-1], "strip: 1 cleaned, 1 skipped")


class DryRunTests(StripTestCase):
    def test_dry_run_says_what_would_be_removed_and_writes_nothing(self) -> None:
        dirty("a.jpg")
        write("b.png", DIRTY_PNG)
        result = strip("a.jpg", "b.png", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [
                planned_line("a.jpg", "a_clean.jpg"),
                f"b.png -> b_clean.png (would remove text; {SAVED_PNG} smaller)",
                "strip: 2 planned",
            ],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(names(self.root), ["a.jpg", "b.png"])

    def test_dry_run_uses_outdir(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--outdir", "out", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0], planned_line("a.jpg", str(Path("out") / "a_clean.jpg"))
        )
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_dry_run_skips_a_file_with_nothing_to_remove(self) -> None:
        write("clean.jpg", CLEAN_JPG)
        result = strip("clean.jpg", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip clean.jpg: no metadata to remove"])
        self.assertEqual(stdout_lines(result), ["strip: 1 skipped"])

    def test_dry_run_reports_an_existing_target(self) -> None:
        dirty("a.jpg")
        Path("a_clean.jpg").write_bytes(b"old")
        result = strip("a.jpg", "--dry-run")
        self.assertIn("skip a.jpg: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("a_clean.jpg").read_bytes(), b"old")

    def test_dry_run_reads_the_files_so_a_damaged_one_fails(self) -> None:
        write("junk.jpg", b"this is not an image")
        result = strip("junk.jpg", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail junk.jpg: {DAMAGED}"])
        self.assertEqual(stdout_lines(result), ["strip: 1 failed"])

    def test_dry_run_reports_a_format_that_cannot_be_stripped(self) -> None:
        write("x.heic", b"x")
        result = strip("x.heic", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail x.heic: {heic_text('heic')}"])

    def test_dry_run_with_no_color_profile_names_the_profile(self) -> None:
        write("a.jpg", PROFILE_JPG)
        result = strip("a.jpg", "--no-color-profile", "--dry-run")
        self.assertEqual(
            stdout_lines(result)[0],
            f"a.jpg -> a_clean.jpg (would remove colour profile; {SAVED_PROFILE} smaller)",
        )


class InPlaceTests(StripTestCase):
    def test_the_source_is_replaced_by_its_cleaned_copy(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [f"a.jpg (removed {LABELS_JPG}; {SAVED_JPG} smaller)", "strip: 1 cleaned"],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(Path("a.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_in_place_needs_no_overwrite_and_leaves_no_temporary_file(self) -> None:
        dirty("a.jpg")
        write("b.png", DIRTY_PNG)
        write("c.webp", DIRTY_WEBP)
        result = strip("a.jpg", "b.png", "c.webp", "--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "b.png", "c.webp"])
        self.assertEqual(Path("b.png").read_bytes(), CLEAN_PNG)
        self.assertEqual(Path("c.webp").read_bytes(), CLEAN_WEBP)

    def test_in_place_keeps_the_picture_and_the_orientation(self) -> None:
        write("side.jpg", SIDEWAYS_JPG)
        result = strip("side.jpg", "--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        data = Path("side.jpg").read_bytes()
        self.assertEqual(fx.pixels(data), fx.pixels(SIDEWAYS_JPG))
        self.assertEqual(data, SIDEWAYS_CLEAN)

    def test_in_place_ignores_a_copy_that_already_exists(self) -> None:
        dirty("a.jpg")
        Path("a_clean.jpg").write_bytes(b"old")
        result = strip("a.jpg", "--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("a.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(Path("a_clean.jpg").read_bytes(), b"old")

    def test_a_file_with_nothing_to_remove_is_skipped_and_not_rewritten(self) -> None:
        write("a.jpg", CLEAN_JPG)
        before = os.stat("a.jpg").st_mtime_ns
        result = strip("a.jpg", "--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip a.jpg: no metadata to remove"])
        self.assertEqual(os.stat("a.jpg").st_mtime_ns, before)

    def test_a_scan_in_place_cleans_every_file_including_names_ending_in_clean(self) -> None:
        dirty("a.jpg")
        dirty("b_clean.jpg")
        result = strip("--in-place")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(Path("a.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(Path("b_clean.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(names(self.root), ["a.jpg", "b_clean.jpg"])
        self.assertEqual(stdout_lines(result)[-1], "strip: 2 cleaned")

    def test_dry_run_in_place_changes_nothing(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--in-place", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            [f"a.jpg (would remove {LABELS_JPG}; {SAVED_JPG} smaller)", "strip: 1 planned"],
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(Path("a.jpg").read_bytes(), DIRTY_JPG)
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_a_refused_file_is_left_unchanged(self) -> None:
        broken = DIRTY_JPG[: len(DIRTY_JPG) // 2]
        write("a.jpg", broken)
        dirty("b.jpg")
        result = strip("a.jpg", "b.jpg", "--in-place")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail a.jpg: {DAMAGED}"])
        self.assertEqual(Path("a.jpg").read_bytes(), broken)
        self.assertEqual(Path("b.jpg").read_bytes(), CLEAN_JPG)
        self.assertEqual(stdout_lines(result)[-1], "strip: 1 cleaned, 1 failed")
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg"])

    def test_in_place_with_output_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--in-place", "-o", "x.jpg")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--in-place cannot be used with --output or --outdir.", result.stderr)
        self.assertEqual(Path("a.jpg").read_bytes(), DIRTY_JPG)
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_in_place_with_outdir_is_a_usage_error(self) -> None:
        dirty("a.jpg")
        result = strip("a.jpg", "--in-place", "--outdir", "out")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--in-place cannot be used with --output or --outdir.", result.stderr)
        self.assertEqual(Path("a.jpg").read_bytes(), DIRTY_JPG)
        self.assertEqual(names(self.root), ["a.jpg"])


class FailureTests(StripTestCase):
    def test_a_format_that_cannot_be_stripped_fails_alone(self) -> None:
        dirty("a.jpg")
        write("x.heic", b"\0\0\0\x18ftypheic")
        write("z.png", DIRTY_PNG)
        result = strip("a.jpg", "x.heic", "z.png")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail x.heic: {heic_text('heic')}"])
        self.assertEqual(
            names(self.root), ["a.jpg", "a_clean.jpg", "x.heic", "z.png", "z_clean.png"]
        )
        self.assertEqual(stdout_lines(result)[-1], "strip: 2 cleaned, 1 failed")

    def test_other_formats_are_named_by_their_own_extension(self) -> None:
        for extension in ("gif", "tif", "avif", "bmp"):
            with self.subTest(extension=extension):
                write(f"x.{extension}", b"x")
                result = strip(f"x.{extension}")
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertEqual(
                    result.stderr.splitlines(),
                    [f"fail x.{extension}: {heic_text(extension)}"],
                )
                self.assertEqual(stdout_lines(result), ["strip: 1 failed"])

    def test_a_junk_file_fails_alone_and_the_others_are_cleaned(self) -> None:
        dirty("a.jpg")
        write("junk.jpg", b"this is not an image")
        dirty("z.jpg")
        result = strip("a.jpg", "junk.jpg", "z.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail junk.jpg: {DAMAGED}"])
        self.assertEqual(
            names(self.root), ["a.jpg", "a_clean.jpg", "junk.jpg", "z.jpg", "z_clean.jpg"]
        )
        self.assertEqual(stdout_lines(result)[-1], "strip: 2 cleaned, 1 failed")

    def test_a_failed_file_leaves_no_partial_output(self) -> None:
        write("junk.png", b"\x89PNG\r\n\x1a\nnot really")
        result = strip("junk.png")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(names(self.root), ["junk.png"])
        self.assertEqual(stdout_lines(result), ["strip: 1 failed"])

    def test_a_file_whose_metadata_cannot_be_read_safely_fails(self) -> None:
        write("odd.jpg", fx.build_jpeg([fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(9))], BODY))
        result = strip("odd.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail odd.jpg: {UNREADABLE}"])
        self.assertEqual(names(self.root), ["odd.jpg"])

    def test_two_different_orientations_fail_the_file(self) -> None:
        write(
            "odd.jpg",
            fx.build_jpeg(
                [fx.JFIF_CLEAN, fx.exif_app1(fx.photo_tiff(6)), fx.xmp_app1(fx.xmp_packet("3"))],
                BODY,
            ),
        )
        result = strip("odd.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [f"fail odd.jpg: {DAMAGED}"])

    def test_an_animated_png_and_an_animated_webp_fail_alone(self) -> None:
        frames = [fx.noise(16, 16, seed) for seed in (1, 2, 3)]
        png = io.BytesIO()
        frames[0].save(png, "PNG", save_all=True, append_images=frames[1:])
        webp = io.BytesIO()
        frames[0].save(webp, "WEBP", save_all=True, append_images=frames[1:], lossless=True)
        write("anim.png", png.getvalue())
        write("anim.webp", webp.getvalue())
        dirty("a.jpg")
        result = strip("anim.png", "anim.webp", "a.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            result.stderr.splitlines(),
            [f"fail anim.png: {ANIMATED}", f"fail anim.webp: {ANIMATED}"],
        )
        self.assertEqual(
            names(self.root), ["a.jpg", "a_clean.jpg", "anim.png", "anim.webp"]
        )

    def test_the_result_lines_split_between_stdout_and_stderr(self) -> None:
        dirty("a.jpg")
        write("clean.jpg", CLEAN_JPG)
        write("junk.jpg", b"nope")
        result = strip("a.jpg", "clean.jpg", "junk.jpg")
        self.assertEqual(
            stdout_lines(result),
            [cleaned_line("a.jpg", "a_clean.jpg"), "strip: 1 cleaned, 1 failed, 1 skipped"],
        )
        self.assertEqual(
            result.stderr.splitlines(),
            ["skip clean.jpg: no metadata to remove", f"fail junk.jpg: {DAMAGED}"],
        )


class HelpTests(unittest.TestCase):
    def test_strip_is_listed_in_the_image_help(self) -> None:
        result = runner.invoke(app, ["image", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        commands = result.stdout.split("Commands:", 1)[1]
        self.assertRegex(commands, r"(?m)^  strip\s+Remove metadata from JPEG, PNG and WebP")

    def test_help_has_the_command_text_and_the_option_texts(self) -> None:
        text = image_help("strip", "-h")
        for expected in (
            "Remove metadata from JPEG, PNG and WebP files without re-encoding.",
            "The image data is copied byte for byte, so quality does not change. EXIF "
            "(with GPS), XMP, IPTC, comments, text chunks and data after the image are "
            "removed; the orientation and the colour profile are kept. Each file is "
            "written as <name>_clean.<ext> beside its source unless -o, --outdir or "
            "--in-place says otherwise.",
            "Replace each source with its cleaned copy.",
            "Remove the colour profile too.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_help_lists_every_option_in_order_and_no_format_options(self) -> None:
        text = image_help("strip", "-h")
        start = text.index("Options:")
        order = [
            "-d, --dir",
            "-p, --pattern",
            "-r, --recursive",
            "-o, --output",
            "--outdir",
            "--in-place",
            "--no-color-profile",
            "--overwrite",
            "--dry-run",
        ]
        positions = [text.index(option, start) for option in order]
        self.assertEqual(positions, sorted(positions))
        for option in ("--to", "--quality", "--backend", "--background", "--size"):
            with self.subTest(option=option):
                self.assertNotIn(option, text)


if __name__ == "__main__":
    unittest.main()

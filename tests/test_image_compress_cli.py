"""Tests for ``jzr image compress``.

Fixtures are built with Pillow in temporary folders from seeded pixels, so file
sizes are stable, and every output is checked by reopening it. Each test runs
with the temporary folder as the working directory, so the paths printed in
result lines are the short names given on the command line.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import random
import tempfile
import unittest

from click.testing import CliRunner
from PIL import Image, ImageChops
import pillow_heif

from jzr_tools.cli import app
from jzr_tools.core.media import format_size

pillow_heif.register_heif_opener()

runner = CliRunner()

ANIMATED = "animated images are not compressed yet; jzr image convert keeps every frame"
SIZE = r"\d+(?:\.\d+)? (?:B|kB|MB)"


def compress(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", "compress", *args])


def image_help(*args: str) -> str:
    result = runner.invoke(app, ["image", *args])
    assert result.exit_code == 0, result.output
    return " ".join(result.stdout.split())


def noise(width: int = 200, height: int = 200, seed: int = 1) -> Image.Image:
    """Random pixels: they do not compress well, so quality changes the size a lot."""
    data = random.Random(seed).randbytes(width * height * 3)
    return Image.frombytes("RGB", (width, height), data)


def blocks(width: int = 200, height: int = 200, seed: int = 2) -> Image.Image:
    """Flat 8x8 blocks of random colour: they compress very well when lossless."""
    small = noise(width // 8, height // 8, seed)
    return small.resize((width, height), Image.Resampling.NEAREST)


def save(image: Image.Image, path: str | Path, **options) -> Path:  # noqa: ANN003
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, **options)
    return target


def noisy_jpg(path: str | Path, width: int = 200, height: int = 200, quality: int = 95) -> Path:
    return save(noise(width, height), path, quality=quality)


def flat(path: str | Path, width: int = 100, height: int = 80) -> Path:
    return save(Image.new("RGB", (width, height), (200, 30, 30)), path)


def size_of(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def format_of(path: str | Path) -> str | None:
    with Image.open(path) as image:
        return image.format


def names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def same_pixels(first: str | Path, second: str | Path) -> bool:
    with Image.open(first) as one, Image.open(second) as two:
        if one.size != two.size:
            return False
        return ImageChops.difference(one.convert("RGBA"), two.convert("RGBA")).getbbox() is None


def reencoded(path: str | Path, fmt: str, **options) -> bytes:  # noqa: ANN003
    """What Pillow writes for the decoded pixels of ``path`` with these options."""
    buffer = io.BytesIO()
    with Image.open(path) as image:
        image.convert("RGB").save(buffer, fmt, **options)
    return buffer.getvalue()


def quantization_of(data: bytes | str | Path) -> dict[int, list[int]]:
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    with Image.open(source) as image:
        return {key: list(table) for key, table in image.quantization.items()}


def stdout_lines(result) -> list[str]:  # noqa: ANN001
    return result.stdout.splitlines()


class CompressTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="jzr-compress-cli-")
        self.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

    def assert_result_line(
        self, line: str, source: str, target: str, before: Path, after: Path
    ) -> re.Match[str]:
        """The line names both files and the real sizes with the real percentage."""
        match = re.fullmatch(
            rf"{re.escape(source)} -> {re.escape(target)} "
            rf"\((?P<before>{SIZE}) -> (?P<after>{SIZE}), (?P<change>-\d+%)(?P<more>.*)\)",
            line,
        )
        self.assertIsNotNone(match, line)
        assert match is not None
        old, new = before.stat().st_size, after.stat().st_size
        self.assertEqual(match["before"], format_size(old), line)
        self.assertEqual(match["after"], format_size(new), line)
        self.assertEqual(match["change"], f"-{max(1, round((old - new) * 100 / old))}%", line)
        return match


class DefaultsTests(CompressTestCase):
    def test_jpg_is_reencoded_at_quality_82_beside_the_source(self) -> None:
        source = noisy_jpg("a.jpg")
        result = compress("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "a_compressed.jpg"])
        out = self.root / "a_compressed.jpg"
        self.assertEqual(format_of(out), "JPEG")
        self.assertEqual(size_of(out), (200, 200))
        self.assertLess(out.stat().st_size, source.stat().st_size)
        self.assertEqual(quantization_of(out), quantization_of(reencoded(source, "JPEG", quality=82)))
        self.assert_result_line(
            stdout_lines(result)[0], "a.jpg", "a_compressed.jpg", source, out
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed")
        self.assertEqual(result.stderr, "")

    def test_jpg_output_is_smaller_than_a_plain_encode_at_the_same_quality(self) -> None:
        source = noisy_jpg("a.jpg")
        result = compress("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        plain = reencoded(source, "JPEG", quality=82)
        self.assertLess((self.root / "a_compressed.jpg").stat().st_size, len(plain))

    def test_png_keeps_every_pixel_and_gets_smaller(self) -> None:
        source = save(blocks(), "a.png", compress_level=0)
        result = compress("a.png")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.png"
        self.assertEqual(format_of(out), "PNG")
        self.assertTrue(same_pixels(source, out))
        self.assertLess(out.stat().st_size, source.stat().st_size)
        self.assert_result_line(
            stdout_lines(result)[0], "a.png", "a_compressed.png", source, out
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed")

    def test_webp_is_reencoded_at_quality_80(self) -> None:
        source = save(noise(), "a.webp", quality=95)
        result = compress("a.webp")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.webp"
        self.assertEqual(format_of(out), "WEBP")
        reference = len(reencoded(source, "WEBP", quality=80))
        self.assertAlmostEqual(out.stat().st_size, reference, delta=reference * 0.03)
        self.assertLess(out.stat().st_size, source.stat().st_size)
        self.assert_result_line(
            stdout_lines(result)[0], "a.webp", "a_compressed.webp", source, out
        )

    def test_tif_is_rewritten_with_lzw_and_keeps_every_pixel(self) -> None:
        source = save(blocks(), "a.tif")
        with Image.open(source) as image:
            self.assertEqual(image.info.get("compression"), "raw")
        result = compress("a.tif")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.tif"
        with Image.open(out) as image:
            self.assertEqual(image.format, "TIFF")
            self.assertEqual(image.info.get("compression"), "tiff_lzw")
        self.assertTrue(same_pixels(source, out))
        self.assertLess(out.stat().st_size, source.stat().st_size)
        self.assert_result_line(
            stdout_lines(result)[0], "a.tif", "a_compressed.tif", source, out
        )

    def test_explicit_quality_replaces_the_default(self) -> None:
        source = noisy_jpg("a.jpg")
        result = compress("a.jpg", "-q", "40")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            quantization_of(self.root / "a_compressed.jpg"),
            quantization_of(reencoded(source, "JPEG", quality=40)),
        )

    def test_the_extension_is_kept_as_written(self) -> None:
        noisy_jpg("IMG.JPG")
        result = compress("IMG.JPG")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("IMG_compressed.JPG", names(self.root))
        self.assertEqual(format_of("IMG_compressed.JPG"), "JPEG")

    def test_the_source_file_is_left_untouched(self) -> None:
        noisy_jpg("a.jpg")
        before = Path("a.jpg").read_bytes()
        result = compress("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(Path("a_compressed.jpg").is_file())
        self.assertEqual(Path("a.jpg").read_bytes(), before)

    def test_summary_counts_every_compressed_file(self) -> None:
        noisy_jpg("a.jpg")
        noisy_jpg("b.jpg")
        result = compress("a.jpg", "b.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(stdout_lines(result)[-1], "pillow: 2 compressed")

    def test_summary_lists_statuses_in_alphabetical_order(self) -> None:
        noisy_jpg("a.jpg")
        noisy_jpg("small.jpg", quality=20)
        result = compress("a.jpg", "small.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed, 1 skipped")


class FormatOptionTests(CompressTestCase):
    def test_to_writes_each_target_format(self) -> None:
        save(blocks(), "a.png")
        for target, expected in (
            ("jpg", "JPEG"),
            ("png", "PNG"),
            ("webp", "WEBP"),
            ("avif", "AVIF"),
            ("heic", "HEIF"),
        ):
            with self.subTest(target=target):
                result = compress("a.png", "--to", target)
                self.assertEqual(result.exit_code, 0, result.output)
                out = f"a_compressed.{target}"
                self.assertEqual(format_of(out), expected)
                self.assertEqual(size_of(out), (200, 200))
                self.assertIn(f"a.png -> {out} (", result.stdout)

    def test_to_ignores_case(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "--to", "WEBP")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.png", "a_compressed.webp"])
        self.assertEqual(format_of("a_compressed.webp"), "WEBP")

    def test_short_flag_for_to(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-t", "jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("a_compressed.jpg"), "JPEG")

    def test_to_a_format_that_is_not_a_compress_target_is_a_usage_error(self) -> None:
        save(blocks(), "a.png")
        for value in ("gif", "bmp", "xyz"):
            with self.subTest(value=value):
                result = compress("a.png", "--to", value)
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("--to", result.stderr)
                self.assertEqual(names(self.root), ["a.png"])

    def test_output_names_the_file_exactly(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "-o", "small.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "small.jpg"])
        self.assertEqual(stdout_lines(result)[0].split(" (")[0], "a.jpg -> small.jpg")

    def test_output_in_another_folder_is_created(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "-o", str(Path("deep") / "x.jpg"))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of(Path("deep") / "x.jpg"), "JPEG")

    def test_output_that_agrees_with_to_is_accepted(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-o", "out.webp", "--to", "webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("out.webp"), "WEBP")

    def test_output_with_another_extension_than_to_is_a_usage_error(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-o", "out.jpg", "--to", "webp")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output out.jpg does not end in .webp.", result.stderr)
        self.assertEqual(names(self.root), ["a.png"])

    def test_output_extension_sets_the_format_like_to_would(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-o", "out.webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("out.webp"), "WEBP")

    def test_output_that_is_not_a_compress_format_is_a_usage_error(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-o", "out.bmp")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(
            "--output out.bmp: .bmp is not a compress format; "
            "use jpg, png, webp, avif or heic.",
            result.stderr,
        )
        self.assertEqual(names(self.root), ["a.png"])

    def test_output_without_an_extension_takes_the_source_format(self) -> None:
        save(blocks(), "a.png", compress_level=0)
        result = compress("a.png", "-o", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        created = [name for name in names(self.root) if name != "a.png"]
        self.assertEqual(len(created), 1, created)
        self.assertTrue(created[0].startswith("out"), created)
        self.assertEqual(format_of(created[0]), "PNG")

    def test_output_with_several_sources_is_a_usage_error(self) -> None:
        noisy_jpg("a.jpg")
        noisy_jpg("b.jpg")
        result = compress("a.jpg", "b.jpg", "-o", "out.jpg")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output names one file", result.stderr)
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg"])

    def test_output_with_outdir_is_a_usage_error(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "-o", "out.jpg", "--outdir", "o")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--output and --outdir cannot be used together.", result.stderr)

    def test_dir_with_sources_is_a_usage_error(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--dir", ".")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(
            "--dir and --recursive only apply when no SOURCES are listed.", result.stderr
        )

    def test_the_backend_option_does_not_exist(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--backend", "pillow")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("No such option", result.stderr)


class OptionErrorTests(CompressTestCase):
    def setUp(self) -> None:
        super().setUp()
        noisy_jpg("a.jpg")

    def assert_usage_error(self, *args: str, hint: str) -> None:
        result = compress("a.jpg", *args)
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn(hint, result.stderr)
        self.assertEqual(names(self.root), ["a.jpg"], "nothing may be written")

    def test_quality_with_size_is_a_usage_error(self) -> None:
        self.assert_usage_error(
            "-q", "50", "--size", "30KB", hint="--quality and --size cannot be used together."
        )

    def test_a_bad_size_is_a_usage_error_with_the_parsers_message(self) -> None:
        for value in ("abc", "12 parsecs", "0"):
            with self.subTest(value=value):
                self.assert_usage_error("--size", value, hint="--size")

    def test_a_bad_size_names_the_accepted_units(self) -> None:
        self.assert_usage_error("-s", "abc", hint="is not a size; use a number and a unit")

    def test_bad_quality_is_a_usage_error(self) -> None:
        self.assert_usage_error("-q", "0", hint="--quality")
        self.assert_usage_error("-q", "101", hint="--quality")

    def test_bad_max_side_is_a_usage_error(self) -> None:
        for value in ("0", "-3", "2.5", "x"):
            with self.subTest(value=value):
                self.assert_usage_error("--max-side", value, hint="--max-side")

    def test_bad_background_is_a_usage_error(self) -> None:
        self.assert_usage_error("--background", "red", hint="'red' is not a colour")


class ScanTests(CompressTestCase):
    COMPRESS_SET = ("jpg", "jpeg", "png", "webp", "avif", "heic", "heif", "tif", "tiff")
    OTHER_IMAGES = ("bmp", "gif", "ico", "ppm", "tga")

    def populate(self) -> None:
        for extension in self.COMPRESS_SET + self.OTHER_IMAGES:
            Path(f"pic_{extension}.{extension}").write_bytes(b"x")
        Path("notes.txt").write_text("not an image", encoding="utf-8")

    def planned(self, result) -> set[str]:  # noqa: ANN001
        return {line for line in stdout_lines(result) if " -> " in line}

    def test_a_scan_without_to_picks_only_the_compressible_formats(self) -> None:
        self.populate()
        result = compress("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.planned(result),
            {f"pic_{e}.{e} -> pic_{e}_compressed.{e}" for e in self.COMPRESS_SET},
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 9 planned")
        self.assertEqual(result.stderr, "")

    def test_a_scan_with_to_picks_every_supported_image(self) -> None:
        self.populate()
        result = compress("--dry-run", "--to", "webp")
        self.assertEqual(result.exit_code, 0, result.output)
        everything = self.COMPRESS_SET + self.OTHER_IMAGES
        self.assertEqual(
            self.planned(result),
            {f"pic_{e}.{e} -> pic_{e}_compressed.webp" for e in everything},
        )
        self.assertEqual(stdout_lines(result)[-1], f"pillow: {len(everything)} planned")
        self.assertNotIn("notes", result.output)

    def test_a_scan_compresses_beside_the_sources_and_skips_its_own_results(self) -> None:
        noisy_jpg("a.jpg")
        save(blocks(), "b.png", compress_level=0)
        first = compress()
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(
            names(self.root), ["a.jpg", "a_compressed.jpg", "b.png", "b_compressed.png"]
        )
        self.assertEqual(stdout_lines(first)[-1], "pillow: 2 compressed")
        second = compress()
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(
            names(self.root), ["a.jpg", "a_compressed.jpg", "b.png", "b_compressed.png"]
        )
        self.assertEqual(
            sorted(second.stderr.splitlines()),
            [
                "skip a.jpg: target exists (use --overwrite)",
                "skip b.png: target exists (use --overwrite)",
            ],
        )
        self.assertEqual(stdout_lines(second), ["pillow: 2 skipped"])

    def test_a_scan_that_finds_only_earlier_results_finds_nothing(self) -> None:
        noisy_jpg("a_compressed.jpg")
        result = compress()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("No matching files.", result.stderr)
        self.assertEqual(names(self.root), ["a_compressed.jpg"])

    def test_the_scan_skip_matches_only_a_trailing_compressed(self) -> None:
        noisy_jpg("my_compressed_notes.jpg")
        result = compress("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "my_compressed_notes.jpg -> my_compressed_notes_compressed.jpg", result.stdout
        )

    def test_a_listed_file_is_compressed_even_when_it_looks_like_a_result(self) -> None:
        noisy_jpg("a_compressed.jpg")
        result = compress("a_compressed.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a_compressed.jpg", "a_compressed_compressed.jpg"])

    def test_nothing_to_compress_is_reported(self) -> None:
        save(Image.new("RGB", (10, 10)), "a.bmp")
        Path("notes.txt").write_text("hello", encoding="utf-8")
        result = compress()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("No matching files.", result.stderr)

    def test_dir_selects_the_folder_to_scan(self) -> None:
        noisy_jpg(Path("photos") / "a.jpg")
        noisy_jpg("elsewhere.jpg")
        result = compress("--dir", "photos")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "photos"), ["a.jpg", "a_compressed.jpg"])
        self.assertEqual(names(self.root), ["elsewhere.jpg", "photos"])

    def test_pattern_filters_the_scan(self) -> None:
        noisy_jpg("keep_a.jpg")
        noisy_jpg("keep_b.jpg")
        noisy_jpg("other.jpg")
        result = compress("--pattern", "^keep")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            names(self.root),
            ["keep_a.jpg", "keep_a_compressed.jpg", "keep_b.jpg", "keep_b_compressed.jpg", "other.jpg"],
        )

    def test_a_scan_ignores_subfolders_unless_recursive(self) -> None:
        noisy_jpg("top.jpg")
        noisy_jpg(Path("sub") / "deep.jpg")
        result = compress()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.jpg"])

    def test_recursive_compresses_beside_each_source(self) -> None:
        noisy_jpg("top.jpg")
        noisy_jpg(Path("sub") / "deep.jpg")
        result = compress("--recursive")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "sub"), ["deep.jpg", "deep_compressed.jpg"])
        self.assertIn("top_compressed.jpg", names(self.root))

    def test_recursive_with_outdir_mirrors_the_folders(self) -> None:
        noisy_jpg(Path("pics") / "top.jpg")
        noisy_jpg(Path("pics") / "sub" / "deep.jpg")
        result = compress("--dir", "pics", "--recursive", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of(Path("out") / "top_compressed.jpg"), "JPEG")
        self.assertEqual(format_of(Path("out") / "sub" / "deep_compressed.jpg"), "JPEG")
        self.assertEqual(names(self.root / "pics"), ["sub", "top.jpg"])
        self.assertEqual(names(self.root / "pics" / "sub"), ["deep.jpg"])

    def test_outdir_collects_listed_sources_and_is_created(self) -> None:
        noisy_jpg("a.jpg")
        noisy_jpg("b.jpg")
        result = compress("a.jpg", "b.jpg", "--outdir", "out")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root / "out"), ["a_compressed.jpg", "b_compressed.jpg"])
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg", "out"])

    def test_two_sources_with_the_same_planned_name_do_not_overwrite_each_other(self) -> None:
        save(blocks(), "a.png", compress_level=0)
        save(blocks(seed=5), "a.bmp")
        result = compress("a.png", "a.bmp", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            names(self.root), ["a.bmp", "a.png", "a_compressed-2.png", "a_compressed.png"]
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 2 compressed")


class ExistingTargetTests(CompressTestCase):
    def setUp(self) -> None:
        super().setUp()
        noisy_jpg("a.jpg")
        Path("a_compressed.jpg").write_bytes(b"old")

    def test_an_existing_target_is_skipped_and_left_alone(self) -> None:
        result = compress("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stderr.splitlines(), ["skip a.jpg: target exists (use --overwrite)"]
        )
        self.assertEqual(Path("a_compressed.jpg").read_bytes(), b"old")
        self.assertEqual(stdout_lines(result), ["pillow: 1 skipped"])

    def test_overwrite_replaces_the_target(self) -> None:
        result = compress("a.jpg", "--overwrite")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("a_compressed.jpg"), "JPEG")
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed")

    def test_an_existing_output_file_is_skipped_too(self) -> None:
        Path("mine.jpg").write_bytes(b"old")
        result = compress("a.jpg", "-o", "mine.jpg")
        self.assertIn("skip a.jpg: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("mine.jpg").read_bytes(), b"old")

    def test_the_source_is_never_its_own_output(self) -> None:
        before = Path("a.jpg").read_bytes()
        result = compress("a.jpg", "-o", "a.jpg", "--overwrite")
        self.assertIn("skip a.jpg: source is the target", result.stderr)
        self.assertEqual(Path("a.jpg").read_bytes(), before)

    def test_a_skipped_target_does_not_stop_the_next_file(self) -> None:
        noisy_jpg("b.jpg")
        result = compress("a.jpg", "b.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("b_compressed.jpg"), "JPEG")
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed, 1 skipped")


class DryRunTests(CompressTestCase):
    def test_dry_run_prints_the_planned_names_and_writes_nothing(self) -> None:
        noisy_jpg("a.jpg")
        save(blocks(), "b.png")
        result = compress("a.jpg", "b.png", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result),
            ["a.jpg -> a_compressed.jpg", "b.png -> b_compressed.png", "pillow: 2 planned"],
        )
        self.assertEqual(names(self.root), ["a.jpg", "b.png"])

    def test_dry_run_uses_to_and_outdir(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--to", "webp", "--outdir", "out", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result)[0], f"a.jpg -> {Path('out') / 'a_compressed.webp'}"
        )
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_dry_run_with_size_and_max_side_still_writes_nothing(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--size", "10KB", "--max-side", "50", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(stdout_lines(result), ["a.jpg -> a_compressed.jpg", "pillow: 1 planned"])
        self.assertEqual(names(self.root), ["a.jpg"])

    def test_dry_run_reports_an_existing_target(self) -> None:
        noisy_jpg("a.jpg")
        Path("a_compressed.jpg").write_bytes(b"old")
        result = compress("a.jpg", "--dry-run")
        self.assertIn("skip a.jpg: target exists (use --overwrite)", result.stderr)
        self.assertEqual(Path("a_compressed.jpg").read_bytes(), b"old")

    def test_dry_run_reports_a_format_that_cannot_be_compressed(self) -> None:
        save(Image.new("RGB", (10, 10)), "x.bmp")
        result = compress("x.bmp", "--dry-run")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "fail x.bmp: .bmp is not compressed; add --to webp (or jpg, png)",
            result.stderr.splitlines(),
        )
        self.assertEqual(stdout_lines(result), ["pillow: 1 failed"])

    def test_dry_run_does_not_open_the_files(self) -> None:
        Path("junk.jpg").write_bytes(b"this is not an image")
        result = compress("junk.jpg", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            stdout_lines(result), ["junk.jpg -> junk_compressed.jpg", "pillow: 1 planned"]
        )


class FailureTests(CompressTestCase):
    BMP_FAILURE = "fail x.bmp: .bmp is not compressed; add --to webp (or jpg, png)"

    def test_a_format_that_cannot_be_compressed_fails_alone(self) -> None:
        noisy_jpg("a.jpg")
        save(blocks(), "x.bmp")
        noisy_jpg("z.jpg")
        result = compress("a.jpg", "x.bmp", "z.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(result.stderr.splitlines(), [self.BMP_FAILURE])
        self.assertEqual(
            names(self.root),
            ["a.jpg", "a_compressed.jpg", "x.bmp", "z.jpg", "z_compressed.jpg"],
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 2 compressed, 1 failed")

    def test_to_makes_a_bmp_acceptable(self) -> None:
        save(blocks(), "x.bmp")
        result = compress("x.bmp", "--to", "webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("x_compressed.webp"), "WEBP")
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed")

    def test_a_junk_file_fails_alone_and_the_others_are_compressed(self) -> None:
        noisy_jpg("a.jpg")
        Path("junk.jpg").write_bytes(b"this is not an image")
        noisy_jpg("z.jpg")
        result = compress("a.jpg", "junk.jpg", "z.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        failures = [line for line in result.stderr.splitlines() if line.startswith("fail ")]
        self.assertEqual(len(failures), 1, result.stderr)
        self.assertTrue(failures[0].startswith("fail junk.jpg: "), failures[0])
        self.assertIn("not a readable image", failures[0])
        self.assertEqual(
            names(self.root),
            ["a.jpg", "a_compressed.jpg", "junk.jpg", "z.jpg", "z_compressed.jpg"],
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 2 compressed, 1 failed")

    def test_a_failed_file_leaves_no_partial_output(self) -> None:
        Path("junk.jpg").write_bytes(b"this is not an image")
        result = compress("junk.jpg")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(names(self.root), ["junk.jpg"])
        self.assertEqual(stdout_lines(result), ["pillow: 1 failed"])

    def animated(self, name: str = "anim.gif") -> None:
        frames = [Image.new("RGB", (40, 30), colour) for colour in ((255, 0, 0), (0, 0, 255))]
        frames[0].save(name, save_all=True, append_images=frames[1:], duration=100)

    def test_an_animated_gif_fails_alone(self) -> None:
        self.animated()
        noisy_jpg("a.jpg")
        result = compress("anim.gif", "a.jpg", "--to", "webp")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"fail anim.gif: {ANIMATED}", result.stderr.splitlines())
        self.assertEqual(
            names(self.root), ["a.jpg", "a_compressed.webp", "anim.gif"]
        )
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed, 1 failed")

    def test_a_single_frame_gif_is_accepted_with_to(self) -> None:
        Image.new("RGB", (40, 30), (255, 0, 0)).save("still.gif")
        result = compress("still.gif", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("still_compressed.png"), (40, 30))


class NeverBiggerTests(CompressTestCase):
    def test_a_copy_that_is_not_smaller_is_skipped_and_not_written(self) -> None:
        source = noisy_jpg("a.jpg", quality=20)
        result = compress("a.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["a.jpg"])
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        match = re.fullmatch(
            rf"skip a\.jpg: already small \(compressed copy was (?P<copy>{SIZE}), "
            rf"source is (?P<source>{SIZE})\)",
            lines[0],
        )
        self.assertIsNotNone(match, lines[0])
        assert match is not None
        self.assertEqual(match["source"], format_size(source.stat().st_size))
        self.assertEqual(stdout_lines(result), ["pillow: 1 skipped"])

    def test_a_smaller_file_next_to_a_skipped_one_is_still_written(self) -> None:
        noisy_jpg("big.jpg")
        noisy_jpg("small.jpg", quality=20)
        result = compress("big.jpg", "small.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(names(self.root), ["big.jpg", "big_compressed.jpg", "small.jpg"])

    def test_a_larger_result_is_kept_when_to_changes_the_format(self) -> None:
        source = noisy_jpg("a.jpg", quality=20)
        result = compress("a.jpg", "--to", "png")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.png"
        self.assertGreater(out.stat().st_size, source.stat().st_size)
        line = stdout_lines(result)[0]
        self.assertRegex(
            line,
            rf"^a\.jpg -> a_compressed\.png \({SIZE} -> {SIZE}, \+\d+%, larger than the source\)$",
        )
        self.assertEqual(result.stderr, "")
        self.assertEqual(stdout_lines(result)[-1], "pillow: 1 compressed")

    def test_a_smaller_result_after_to_has_no_larger_note(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--to", "webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("larger than the source", result.output)

    def test_jpg_to_on_a_jpeg_file_is_not_a_format_change(self) -> None:
        save(noise(), "a.jpeg", quality=20)
        result = compress("a.jpeg", "--to", "jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skip a.jpeg: already small", result.stderr)
        self.assertEqual(names(self.root), ["a.jpeg"])


class SizeLimitTests(CompressTestCase):
    def test_the_result_fits_the_limit_by_lowering_quality(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--size", "20KB")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.jpg"
        self.assertLessEqual(out.stat().st_size, 20_000)
        self.assertEqual(size_of(out), (200, 200))
        line = stdout_lines(result)[0]
        self.assertRegex(line, r"^a\.jpg -> a_compressed\.jpg \(.*, quality \d+\)$")
        self.assertNotIn("resized", line)

    def test_the_quality_in_the_line_is_the_one_used(self) -> None:
        source = noisy_jpg("a.jpg")
        result = compress("a.jpg", "-s", "20KB")
        self.assertEqual(result.exit_code, 0, result.output)
        quality = int(re.search(r"quality (\d+)", result.stdout).group(1))  # type: ignore[union-attr]
        self.assertTrue(40 <= quality <= 92, quality)
        self.assertEqual(
            quantization_of(self.root / "a_compressed.jpg"),
            quantization_of(reencoded(source, "JPEG", quality=quality)),
        )

    def test_a_tight_limit_shrinks_the_image_and_says_so(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--size", "10KB")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.jpg"
        self.assertLessEqual(out.stat().st_size, 10_000)
        width, height = size_of(out)
        self.assertLess(width, 200)
        self.assertEqual(width, height)
        line = stdout_lines(result)[0]
        self.assertIn("quality ", line)
        self.assertIn(f"resized to {width}x{height} to fit", line)

    def test_a_limit_the_source_already_meets_is_skipped(self) -> None:
        flat("a.jpg")
        result = compress("a.jpg", "--size", "500KB")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), ["skip a.jpg: already under 500.0 kB"])
        self.assertEqual(names(self.root), ["a.jpg"])
        self.assertEqual(stdout_lines(result), ["pillow: 1 skipped"])

    def test_a_limit_is_not_a_reason_to_skip_when_the_format_changes(self) -> None:
        flat("a.jpg")
        result = compress("a.jpg", "--size", "500KB", "--to", "webp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(format_of("a_compressed.webp"), "WEBP")

    def test_a_limit_the_source_meets_does_not_stop_a_max_side_resize(self) -> None:
        save(noise(200, 100), "a.jpg", quality=20)
        result = compress("a.jpg", "--size", "500KB", "--max-side", "64")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_compressed.jpg"), (64, 32))

    def test_an_impossible_limit_fails_and_writes_nothing(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--size", "100B")
        self.assertEqual(result.exit_code, 1, result.output)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertRegex(
            lines[0],
            rf"^fail a\.jpg: could not get under 100 B; the smallest attempt was {SIZE} at \d+x\d+$",
        )
        self.assertEqual(names(self.root), ["a.jpg"])
        self.assertEqual(stdout_lines(result), ["pillow: 1 failed"])

    def test_an_impossible_limit_fails_that_file_only(self) -> None:
        noisy_jpg("a.jpg")
        flat("b.jpg")
        result = compress("a.jpg", "b.jpg", "--size", "100B")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(names(self.root), ["a.jpg", "b.jpg"])

    def test_a_lossless_target_fits_by_shrinking_and_reports_no_quality(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--to", "png", "--size", "100KB")
        self.assertEqual(result.exit_code, 0, result.output)
        out = self.root / "a_compressed.png"
        self.assertLessEqual(out.stat().st_size, 100_000)
        line = stdout_lines(result)[0]
        self.assertNotIn("quality", line)
        self.assertIn("to fit", line)


class MaxSideTests(CompressTestCase):
    def test_the_longer_side_is_limited_and_the_proportions_kept(self) -> None:
        save(noise(200, 100), "a.jpg", quality=95)
        result = compress("a.jpg", "--max-side", "64")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_compressed.jpg"), (64, 32))
        line = stdout_lines(result)[0]
        self.assertIn("resized to 64x32", line)
        self.assertNotIn("to fit", line)

    def test_a_portrait_image_is_limited_by_its_height(self) -> None:
        save(noise(100, 200), "a.jpg", quality=95)
        result = compress("a.jpg", "--max-side", "64")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_compressed.jpg"), (32, 64))

    def test_a_smaller_image_is_not_enlarged(self) -> None:
        save(noise(40, 20), "a.jpg", quality=95)
        result = compress("a.jpg", "--max-side", "64")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_compressed.jpg"), (40, 20))
        self.assertNotIn("resized", result.stdout)

    def test_a_resize_makes_a_copy_that_is_kept_even_for_an_unchanged_format(self) -> None:
        save(noise(200, 100), "a.png")
        result = compress("a.png", "--max-side", "64")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("a_compressed.png"), (64, 32))


class NoteTests(CompressTestCase):
    QUALITY_PNG = "note: .png is lossless; --quality is ignored"
    BACKGROUND_PNG = "note: .png keeps transparency; --background is ignored"

    def test_quality_with_png_sources_is_noted_once_before_the_files(self) -> None:
        save(blocks(), "a.png", compress_level=0)
        save(blocks(seed=4), "b.png", compress_level=0)
        result = compress("a.png", "b.png", "-q", "50")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.splitlines(), [self.QUALITY_PNG])

    def test_quality_note_applies_to_a_lossless_to_target(self) -> None:
        noisy_jpg("a.jpg")
        result = compress("a.jpg", "--to", "png", "-q", "50")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)

    def test_no_quality_note_for_lossy_targets_or_without_the_option(self) -> None:
        noisy_jpg("a.jpg")
        save(blocks(), "b.png")
        with_quality = compress("a.jpg", "-q", "50")
        self.assertEqual(with_quality.exit_code, 0, with_quality.output)
        self.assertTrue(Path("a_compressed.jpg").is_file())
        self.assertNotIn("--quality", with_quality.output)
        without = compress("b.png", "--to", "webp")
        self.assertEqual(without.exit_code, 0, without.output)
        self.assertTrue(Path("b_compressed.webp").is_file())
        self.assertNotIn("--quality", without.output)

    def test_background_with_a_png_target_is_noted_once(self) -> None:
        save(blocks(), "a.png")
        save(blocks(seed=4), "b.png")
        result = compress("a.png", "b.png", "--background", "white")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stderr.count(self.BACKGROUND_PNG), 1, result.stderr)

    def test_no_background_note_for_a_jpg_target(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "--to", "jpg", "--background", "white")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--background", result.output)

    def test_background_colour_fills_transparent_pixels_in_a_jpeg(self) -> None:
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save("clear.png")
        result = compress("clear.png", "--to", "jpg", "--background", "#0000ff")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("clear_compressed.jpg") as image:
            red, green, blue = image.convert("RGB").getpixel((20, 20))
        self.assertLess(red, 40)
        self.assertGreater(blue, 215)

    def test_both_notes_can_appear_in_one_run(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-q", "50", "--background", "white")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)
        self.assertEqual(result.stderr.count(self.BACKGROUND_PNG), 1, result.stderr)

    def test_notes_are_printed_in_a_dry_run_too(self) -> None:
        save(blocks(), "a.png")
        result = compress("a.png", "-q", "50", "--dry-run")
        self.assertEqual(result.stderr.count(self.QUALITY_PNG), 1, result.stderr)


class MetadataTests(CompressTestCase):
    def photo(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Acme"
        save(noise(), "photo.jpg", quality=95, exif=exif)

    def test_exif_is_carried_over_by_default(self) -> None:
        self.photo()
        result = compress("photo.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("photo_compressed.jpg") as image:
            self.assertEqual(image.getexif().get(0x010F), "Acme")

    def test_strip_removes_the_exif(self) -> None:
        self.photo()
        result = compress("photo.jpg", "--strip")
        self.assertEqual(result.exit_code, 0, result.output)
        with Image.open("photo_compressed.jpg") as image:
            self.assertEqual(dict(image.getexif()), {})

    def test_the_picture_is_turned_upright_first(self) -> None:
        image = noise(60, 30)
        exif = Image.Exif()
        exif[0x0112] = 6
        save(image, "side.jpg", quality=95, exif=exif)
        result = compress("side.jpg")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(size_of("side_compressed.jpg"), (30, 60))


class HelpTests(unittest.TestCase):
    def test_compress_is_listed_in_the_image_help(self) -> None:
        result = runner.invoke(app, ["image", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        commands = result.stdout.split("Commands:", 1)[1]
        self.assertRegex(commands, r"(?m)^  compress\s+Make image files smaller\.")

    def test_help_has_the_command_text_and_the_option_texts(self) -> None:
        result = runner.invoke(app, ["image", "compress", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        text = " ".join(result.stdout.split())
        for expected in (
            "Make image files smaller.",
            "Each file is re-encoded in its own format (or with --to another) and "
            "written as <name>_compressed.<ext> beside its source unless -o or --outdir "
            "says otherwise. A copy that is not smaller than its source is not kept, "
            "unless --to changes the format.",
            "Re-encode into this format.",
            "Output quality, 1-100 (higher is better).",
            "Largest file size, such as 500KB or 2MB; quality is lowered (and the image "
            "shrunk if needed) to fit.",
            "Longest side in pixels; smaller images are not enlarged.",
            "Leave out EXIF, XMP and comments; the colour profile is kept.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_help_lists_every_option_and_no_backend(self) -> None:
        text = image_help("compress", "-h")
        for option in (
            "-d, --dir",
            "-p, --pattern",
            "-r, --recursive",
            "-o, --output",
            "--outdir",
            "-t, --to",
            "-q, --quality",
            "-s, --size",
            "--max-side",
            "--strip",
            "--background",
            "--overwrite",
            "--dry-run",
        ):
            with self.subTest(option=option):
                self.assertIn(option, text)
        self.assertNotIn("--backend", text)

    def test_options_are_listed_in_the_documented_order(self) -> None:
        text = image_help("compress", "-h")
        start = text.index("Options:")
        order = [
            "--outdir",
            "-t, --to",
            "-q, --quality",
            "-s, --size",
            "--max-side",
            "--strip",
            "--background",
            "--overwrite",
            "--dry-run",
        ]
        positions = [text.index(option, start) for option in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

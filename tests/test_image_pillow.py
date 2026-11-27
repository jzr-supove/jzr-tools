"""Acceptance tests for the Pillow image engine.

Fixtures are built with Pillow itself and every
output is verified by reopening it with Pillow or by reading magic bytes, so the
tests do not depend on what is installed on PATH (except where marked).
"""

from __future__ import annotations

import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner
import pillow_heif
from PIL import Image, ImageCms

from jzr_tools.cli import app
from jzr_tools.core import images
from jzr_tools.core.images import SUPPORTED_EXTENSIONS, convert_file, detect_backends
from size_fixtures import with_size
from tests.test_images import ONE_PIXEL_PNG, three_frame_gif
from tests.test_startup import Observed

import PIL

pillow_heif.register_heif_opener()

runner = CliRunner()

HAS_MAGICK = "magick" in detect_backends()

HEIF_BRANDS = {
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1",
}
ALL_EXTENSIONS = sorted(SUPPORTED_EXTENSIONS)

RED = (230, 20, 20)
GREEN = (20, 200, 40)
BLUE = (20, 40, 230)
WHITE = (255, 255, 255)
GREY = (128, 128, 128)
BLACK = (0, 0, 0)


# --------------------------------------------------------------------------- helpers


def combined(result: object) -> str:
    stdout = getattr(result, "stdout", "")
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{stdout}{stderr}"


def image_cli(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["image", *args])


def listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def stripes(
    colors: list[tuple[int, ...]], mode: str = "RGBA", size: int = 24
) -> Image.Image:
    """Square image made of equally wide vertical stripes."""
    image = Image.new(mode, (size, size))
    width = size // len(colors)
    for index, color in enumerate(colors):
        image.paste(Image.new(mode, (width, size), color), (index * width, 0))
    return image


def sample(image: Image.Image, fraction: float, mode: str = "RGBA") -> tuple[int, ...]:
    """Pixel at the vertical middle, ``fraction`` of the way across, as ``mode``."""
    rgba = image.convert(mode)
    return rgba.getpixel((int(rgba.width * fraction), rgba.height // 2))


STRIPE_CENTRES = (1 / 6, 1 / 2, 5 / 6)


def noisy(width: int, height: int, seed: int = 7) -> Image.Image:
    data = random.Random(seed).randbytes(width * height * 3)
    return Image.frombytes("RGB", (width, height), data)


def gradient(size: int = 16) -> Image.Image:
    image = Image.new("RGB", (size, size))
    for x in range(size):
        for y in range(size):
            image.putpixel((x, y), (x * 255 // (size - 1), y * 255 // (size - 1), 90))
    return image


def grey16(values: list[int], size: int = 24) -> Image.Image:
    """16-bit grey image of equal vertical stripes with the given sample values."""
    width = size // len(values)
    row = b"".join(struct.pack("<H", value) * width for value in values)
    return Image.frombytes("I;16", (width * len(values), size), row * size)


def save_heif(image: Image.Image, path: Path, **params: object) -> None:
    image.save(path, format="HEIF", **params)


def save_any(image: Image.Image, path: Path) -> None:
    """Write ``image`` in the format named by ``path``'s extension."""
    extension = path.suffix.lstrip(".").lower()
    if extension in {"heic", "heif"}:
        save_heif(image, path)
    elif extension == "avif":
        image.save(path, format="AVIF")
    elif extension == "ico":
        image.save(path, format="ICO", sizes=[(image.width, image.height)])
    elif extension == "jpg" or extension == "jpeg":
        image.convert("RGB").save(path, format="JPEG")
    elif extension in {"bmp", "ppm"}:
        image.convert("RGB").save(path)
    else:
        image.save(path)


_OPENED: list[Image.Image] = []


def opened(path: Path) -> Image.Image:
    """Open an image that the test case closes again when it finishes."""
    image = Image.open(path)
    _OPENED.append(image)
    return image


def close_opened() -> None:
    while _OPENED:
        _OPENED.pop().close()


def tearDownModule() -> None:
    close_opened()


def reopen(path: Path) -> Image.Image:
    image = opened(path)
    image.load()
    return image


def exif_orientation(image: Image.Image) -> int | None:
    return image.getexif().get(0x0112)


def sniff(path: Path) -> str:
    """Identify a file by its leading bytes, ignoring its extension."""
    head = path.read_bytes()[:16]
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"GIF8":
        return "gif"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if len(head) >= 2 and head[:1] == b"P" and head[1:2] in b"123456":
        return "ppm"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in HEIF_BRANDS:
            return "heif"
        return "ftyp:" + brand.decode("latin-1")
    return "unknown"


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Pillow can keep a decoder handle (AVIF) open on Windows.
        holder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(holder.cleanup)
        self.addCleanup(close_opened)  # runs first, so the files can be removed
        self.root = Path(holder.name)
        self.out = self.root / "out"


# --------------------------------------------------------------------------- backends


class BackendSelectionTests(TempDirTestCase):
    def test_auto_and_pillow_resolve_to_the_builtin_engine(self) -> None:
        for preference in ("auto", "pillow"):
            with self.subTest(preference=preference):
                self.assertEqual(images.resolve_backend(preference), ("pillow", []))

    def test_pillow_engine_needs_nothing_on_path(self) -> None:
        with mock.patch("jzr_tools.core.images.shutil.which", return_value=None):
            self.assertEqual(images.resolve_backend("auto"), ("pillow", []))
            source = self.root / "a.png"
            source.write_bytes(ONE_PIXEL_PNG)
            result = image_cli("png2jpg", str(source))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertIn("pillow: 1 converted", result.stdout)
        self.assertEqual(sniff(self.root / "a.jpg"), "jpeg")

    def test_magick_missing_from_path_is_exit_one(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)
        with mock.patch("jzr_tools.core.images.shutil.which", return_value=None):
            with self.assertRaises(images.BackendUnavailableError) as raised:
                images.resolve_backend("magick")
            result = image_cli("png2jpg", str(source), "-b", "magick")
        self.assertEqual(str(raised.exception), "Backend not found on PATH: magick")
        self.assertEqual(result.exit_code, 1, combined(result))
        self.assertIn("Backend not found on PATH: magick", result.stderr)
        self.assertFalse((self.root / "a.jpg").exists())

    def test_pillow_and_auto_flags_use_the_same_engine(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)
        for flag in ("auto", "pillow"):
            with self.subTest(backend=flag):
                result = image_cli("png2jpg", str(source), "-b", flag, "--overwrite")
                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertIn("pillow: 1 converted", result.stdout)

    def test_ffmpeg_is_not_a_backend_choice(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)
        result = image_cli("png2jpg", str(source), "--backend", "ffmpeg")
        self.assertEqual(result.exit_code, 2, combined(result))
        self.assertFalse((self.root / "a.jpg").exists())

    def test_backend_help_lists_the_new_choices_and_text(self) -> None:
        for command in (["convert", "-h"], ["png2jpg", "-h"]):
            with self.subTest(command=command):
                text = " ".join(image_cli(*command).stdout.split())
                self.assertIn(
                    "Conversion engine: built-in Pillow, or ImageMagick from PATH.", text
                )
                self.assertIn("auto|pillow|magick", text)
                self.assertNotIn("ffmpeg", text)

    def test_group_help_text_in_group_and_root_listing(self) -> None:
        for args in (["image", "-h"], ["-h"]):
            with self.subTest(args=args):
                result = runner.invoke(app, args)
                self.assertEqual(result.exit_code, 0)
                self.assertIn(
                    "Convert, compress, resize and inspect images.",
                    " ".join(result.stdout.split()),
                )
                # The root listing has an `ffmpeg` group of its own; only the
                # image text must not offer ffmpeg as an image engine.
                text = result.stdout
                if args == ["-h"]:
                    text = "\n".join(
                        line for line in text.splitlines() if line.strip().startswith("image ")
                    )
                self.assertNotIn("ffmpeg", text)

    def test_unchanged_skip_rules_apply_to_the_pillow_engine(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)

        same = image_cli("convert", "--to", "png", str(source))
        self.assertEqual(same.exit_code, 0, combined(same))
        self.assertIn("source is the target", same.stderr)
        self.assertIn("pillow: 1 skipped", same.stdout)

        existing = self.root / "a.jpg"
        existing.write_bytes(b"sentinel")
        skipped = image_cli("png2jpg", str(source))
        self.assertEqual(skipped.exit_code, 0, combined(skipped))
        self.assertIn("target exists (use --overwrite)", skipped.stderr)
        self.assertEqual(existing.read_bytes(), b"sentinel")

        replaced = image_cli("png2jpg", str(source), "--overwrite")
        self.assertEqual(replaced.exit_code, 0, combined(replaced))
        self.assertEqual(sniff(existing), "jpeg")

    def test_convert_file_reports_statuses_for_the_pillow_engine(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)
        target = self.root / "a.jpg"

        planned = convert_file(source, target, backend="pillow", executable=[], dry_run=True)
        self.assertEqual(planned.status, "planned")
        self.assertFalse(target.exists())

        done = convert_file(source, target, backend="pillow", executable=[])
        self.assertEqual((done.status, done.ok), ("converted", True))
        self.assertEqual(sniff(target), "jpeg")

        again = convert_file(source, target, backend="pillow", executable=[])
        self.assertEqual(again.status, "skipped")
        self.assertEqual(again.detail, "target exists (use --overwrite)")

        bad = self.root / "bad.png"
        bad.write_bytes(b"not an image at all")
        failed = convert_file(bad, self.root / "bad.jpg", backend="pillow", executable=[])
        self.assertEqual((failed.status, failed.ok), ("failed", False))
        self.assertTrue(failed.detail.strip())


# --------------------------------------------------------------------------- formats


class FormatsOutputTests(unittest.TestCase):
    def test_formats_prints_exactly_three_lines_in_order(self) -> None:
        with mock.patch(
            "jzr_tools.core.images.shutil.which",
            side_effect=lambda name: "/opt/bin/magick" if name == "magick" else None,
        ):
            result = image_cli("formats")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "extensions: " + " ".join(ALL_EXTENSIONS),
                f"pillow: {PIL.__version__}, pillow-heif {pillow_heif.__version__}",
                "magick: /opt/bin/magick",
            ],
        )

    def test_formats_says_not_found_when_magick_is_missing(self) -> None:
        with mock.patch("jzr_tools.core.images.shutil.which", return_value=None):
            result = image_cli("formats")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(result.stdout.splitlines()[2:], ["magick: not found"])
        self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_formats_ignores_ffmpeg_even_when_it_is_on_path(self) -> None:
        with mock.patch(
            "jzr_tools.core.images.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"
        ):
            result = image_cli("formats")

        self.assertNotIn("ffmpeg", combined(result))
        self.assertNotIn("cannot write", combined(result))


# --------------------------------------------------------------------------- output format


class OutputFormatTests(TempDirTestCase):
    EXPECTED = {
        "avif": "avif", "bmp": "bmp", "gif": "gif", "heic": "heif", "heif": "heif",
        "ico": "ico", "jpeg": "jpeg", "jpg": "jpeg", "png": "png", "ppm": "ppm",
        "tif": "tiff", "tiff": "tiff", "webp": "webp",
    }

    def test_every_target_extension_yields_that_format(self) -> None:
        source = self.root / "src.png"
        gradient().save(source)
        for extension in ALL_EXTENSIONS:
            with self.subTest(target=extension):
                result = image_cli(
                    "convert", "--to", extension, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                target = self.out / f"src.{extension}"
                self.assertTrue(target.is_file())
                if extension == "tga":
                    # TGA has no signature; the reopened format is the evidence.
                    self.assertEqual(opened(target).format, "TGA")
                else:
                    self.assertEqual(sniff(target), self.EXPECTED[extension])
                self.assertEqual(reopen(target).size, (16, 16))

    def test_heic_and_avif_have_the_right_ftyp_brand(self) -> None:
        source = self.root / "src.png"
        gradient().save(source)
        image_cli("convert", "--to", "heic", str(source), "--outdir", str(self.out))
        image_cli("convert", "--to", "avif", str(source), "--outdir", str(self.out))
        heic = (self.out / "src.heic").read_bytes()
        avif = (self.out / "src.avif").read_bytes()
        self.assertEqual(heic[4:8], b"ftyp")
        self.assertIn(heic[8:12], HEIF_BRANDS)
        self.assertEqual(avif[4:8], b"ftyp")
        self.assertIn(avif[8:12], {b"avif", b"avis"})

    def test_every_supported_extension_can_be_read_as_a_source(self) -> None:
        picture = stripes([RED, GREEN, BLUE], mode="RGB", size=32)
        for extension in ALL_EXTENSIONS:
            with self.subTest(source=extension):
                source = self.root / f"src_{extension}.{extension}"
                save_any(picture, source)

                result = image_cli(
                    "convert", "--to", "png", str(source), "--outdir", str(self.out)
                )

                self.assertEqual(result.exit_code, 0, combined(result))
                converted = reopen(self.out / f"src_{extension}.png").convert("RGB")
                self.assertEqual(converted.size, (32, 32))
                self.assertTrue(near(sample(converted, 1 / 6, "RGB"), RED, 40))
                self.assertTrue(near(sample(converted, 5 / 6, "RGB"), BLUE, 40))


# --------------------------------------------------------------------------- transparency


def transparency_fixture(kind: str) -> tuple[Image.Image, list[tuple[int, ...]]]:
    """Return (image, expected RGB per stripe when flattened on white)."""
    if kind == "RGBA":
        # Hidden colour under the transparent stripe is red, not white.
        image = stripes([(255, 0, 0, 0), (0, 0, 0, 128), (20, 200, 30, 255)], "RGBA")
        return image, [WHITE, GREY, (20, 200, 30)]
    if kind == "LA":
        image = stripes([(0, 0), (0, 128), (200, 255)], "LA")
        return image, [WHITE, GREY, (200, 200, 200)]
    image = Image.new("P", (24, 24))
    image.putpalette([255, 0, 0, 0, 0, 0, 20, 200, 30] + [0] * (256 * 3 - 9))
    for index in range(3):
        image.paste(index, (index * 8, 0, index * 8 + 8, 24))
    image.info["transparency"] = 0
    return image, [WHITE, BLACK, (20, 200, 30)]


class TransparencyTests(TempDirTestCase):
    def _convert(self, kind: str, target: str) -> tuple[Image.Image, list[tuple[int, ...]]]:
        image, expected = transparency_fixture(kind)
        source = self.root / f"{kind}.png"
        image.save(source)
        result = image_cli("convert", "--to", target, str(source), "--outdir", str(self.out))
        self.assertEqual(result.exit_code, 0, combined(result))
        return reopen(self.out / f"{kind}.{target}"), expected

    def test_opaque_only_targets_flatten_over_white(self) -> None:
        for kind in ("RGBA", "LA", "P"):
            for target in ("jpg", "jpeg", "bmp", "ppm"):
                with self.subTest(source=kind, target=target):
                    output, expected = self._convert(kind, target)
                    tolerance = 3 if target in {"bmp", "ppm"} else 12
                    for fraction, want in zip(STRIPE_CENTRES, expected):
                        got = sample(output, fraction, "RGB")
                        self.assertTrue(
                            near(got, want, tolerance), f"{got} is not near {want}"
                        )

    def test_fully_transparent_pixel_is_white_whatever_colour_was_hidden(self) -> None:
        # The transparent stripe hides pure red; a naive alpha drop gives red.
        output, _ = self._convert("RGBA", "bmp")
        self.assertTrue(near(sample(output, 1 / 6, "RGB"), WHITE, 3))

    def test_half_transparent_black_is_mid_grey(self) -> None:
        output, _ = self._convert("RGBA", "bmp")
        self.assertTrue(near(sample(output, 1 / 2, "RGB"), GREY, 3))

    def test_transparency_capable_targets_keep_it(self) -> None:
        for target in ("png", "webp", "avif", "heic", "gif", "tif", "tiff", "tga", "ico"):
            with self.subTest(target=target):
                output, _ = self._convert("RGBA", target)
                self.assertLess(sample(output, 1 / 6)[3], 40, "transparent stripe is opaque")
                self.assertGreater(sample(output, 5 / 6)[3], 215, "opaque stripe is see-through")

    def test_palette_transparency_survives_png_and_gif(self) -> None:
        for target in ("png", "gif"):
            with self.subTest(target=target):
                output, _ = self._convert("P", target)
                self.assertLess(sample(output, 1 / 6)[3], 40)
                self.assertGreater(sample(output, 1 / 2)[3], 215)


# --------------------------------------------------------------------------- orientation


class OrientationTests(TempDirTestCase):
    # EXIF Orientation -> (size of the upright result, colour on its left/top,
    # colour on its right/bottom) for a 32x16 source that is red left, blue right.
    CASES = {
        2: ((32, 16), "x", BLUE, RED),
        3: ((32, 16), "x", BLUE, RED),
        5: ((16, 32), "y", RED, BLUE),
        6: ((16, 32), "y", RED, BLUE),
        7: ((16, 32), "y", BLUE, RED),
        8: ((16, 32), "y", BLUE, RED),
    }

    def _source(self, orientation: int, name: str = "photo.jpg") -> Path:
        image = Image.new("RGB", (32, 16), RED)
        image.paste(Image.new("RGB", (16, 16), BLUE), (16, 0))
        exif = Image.Exif()
        exif[0x0112] = orientation
        path = self.root / name
        image.save(path, exif=exif.tobytes(), quality=100, subsampling=0)
        return path

    def test_pixels_are_made_upright(self) -> None:
        for orientation, (size, axis, first, second) in self.CASES.items():
            for target in ("png", "jpg"):
                with self.subTest(orientation=orientation, target=target):
                    source = self._source(orientation, f"o{orientation}.jpg")
                    result = image_cli(
                        "convert", "--to", target, str(source), "--outdir", str(self.out)
                    )
                    self.assertEqual(result.exit_code, 0, combined(result))
                    output = reopen(self.out / f"o{orientation}.{target}").convert("RGB")
                    self.assertEqual(output.size, size)
                    low, high = (0.2, 0.8)
                    if axis == "x":
                        points = [(int(size[0] * low), 8), (int(size[0] * high), 8)]
                    else:
                        points = [(8, int(size[1] * low)), (8, int(size[1] * high))]
                    self.assertTrue(near(output.getpixel(points[0]), first, 30))
                    self.assertTrue(near(output.getpixel(points[1]), second, 30))

    def test_example_4x2_jpeg_orientation_6_becomes_2x4(self) -> None:
        image = Image.new("RGB", (4, 2), RED)
        exif = Image.Exif()
        exif[0x0112] = 6
        source = self.root / "tiny.jpg"
        image.save(source, exif=exif.tobytes())

        result = image_cli("convert", "--to", "png", str(source))

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(reopen(self.root / "tiny.png").size, (2, 4))

    def test_output_carries_no_rotating_orientation_tag(self) -> None:
        for target in ("png", "jpg", "webp", "tif", "avif", "heic"):
            with self.subTest(target=target):
                source = self._source(6, f"t_{target}.jpg")
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = reopen(self.out / f"t_{target}.{target}")
                self.assertIn(exif_orientation(output), (None, 1))

    def test_upright_source_is_left_alone(self) -> None:
        source = self._source(1)
        result = image_cli("convert", "--to", "png", str(source), "--outdir", str(self.out))
        self.assertEqual(result.exit_code, 0, combined(result))
        output = reopen(self.out / "photo.png")
        self.assertEqual(output.size, (32, 16))
        self.assertTrue(near(output.convert("RGB").getpixel((4, 8)), RED, 30))


# --------------------------------------------------------------------------- metadata


def icc_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


class MetadataTests(TempDirTestCase):
    def _source(self, extension: str) -> tuple[Path, bytes]:
        icc = icc_profile()
        exif = Image.Exif()
        exif[0x010F] = "JzrTestMake"
        exif[0x0112] = 1
        path = self.root / f"meta.{extension}"
        stripes([RED, GREEN, BLUE], mode="RGB", size=32).save(
            path, exif=exif.tobytes(), icc_profile=icc
        )
        return path, icc

    def test_exif_and_icc_are_carried_into_targets_that_can_store_them(self) -> None:
        for source_extension, targets in (
            ("jpg", ("png", "webp", "tif", "avif", "heic", "jpg")),
            ("png", ("jpg", "webp", "tiff", "heic")),
        ):
            source, icc = self._source(source_extension)
            for target in targets:
                with self.subTest(source=source_extension, target=target):
                    destination = self.out / f"{source_extension}_{target}"
                    result = image_cli(
                        "convert", "--to", target, str(source), "--outdir", str(destination)
                    )
                    self.assertEqual(result.exit_code, 0, combined(result))
                    output = reopen(destination / f"meta.{target}")
                    self.assertEqual(output.getexif().get(0x010F), "JzrTestMake")
                    self.assertEqual(output.info.get("icc_profile"), icc)

    def test_icc_profile_is_dropped_when_pixels_were_converted_out_of_cmyk(self) -> None:
        source = self.root / "cmyk.jpg"
        Image.new("CMYK", (16, 16), (0, 0, 0, 0)).save(source, icc_profile=icc_profile())
        self.assertIn("icc_profile", opened(source).info, "fixture lost its profile")

        for target in ("png", "webp"):
            with self.subTest(target=target):
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = reopen(self.out / f"cmyk.{target}")
                self.assertNotIn("icc_profile", output.info)
                self.assertTrue(near(sample(output, 0.5, "RGB"), WHITE, 20))

    def test_cmyk_stays_cmyk_with_its_profile_where_the_target_can_hold_it(self) -> None:
        source = self.root / "cmyk.jpg"
        profile = icc_profile()
        Image.new("CMYK", (16, 16), (0, 0, 0, 0)).save(source, icc_profile=profile)

        for target in ("jpg", "tiff"):
            with self.subTest(target=target):
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = reopen(self.out / f"cmyk.{target}")
                self.assertEqual(output.mode, "CMYK")
                self.assertEqual(output.info.get("icc_profile"), profile)


# --------------------------------------------------------------------------- quality


LOSSY = ("jpg", "jpeg", "webp", "avif", "heic", "heif")
LOSSLESS = ("png", "bmp", "gif", "ico", "ppm", "tga", "tif", "tiff")


class QualityTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.source = self.root / "noise.png"
        noisy(192, 128).save(self.source)

    def _size(self, target: str, *quality: str) -> int:
        destination = self.out / f"{target}_{'_'.join(quality) or 'default'}"
        result = image_cli(
            "convert", "--to", target, str(self.source), "--outdir", str(destination), *quality
        )
        self.assertEqual(result.exit_code, 0, combined(result))
        return (destination / f"noise.{target}").stat().st_size

    def test_higher_quality_gives_a_larger_file_for_lossy_targets(self) -> None:
        for target in LOSSY:
            with self.subTest(target=target):
                low = self._size(target, "-q", "30")
                high = self._size(target, "-q", "95")
                self.assertLess(low, high)

    def test_no_quality_means_90(self) -> None:
        for target in LOSSY:
            with self.subTest(target=target):
                self.assertEqual(self._size(target), self._size(target, "-q", "90"))
                self.assertNotEqual(self._size(target), self._size(target, "-q", "30"))

    def test_quality_on_lossy_targets_prints_no_note(self) -> None:
        for target in LOSSY:
            with self.subTest(target=target):
                result = image_cli(
                    "convert", "--to", target, str(self.source),
                    "--outdir", str(self.out / target), "-q", "80",
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertNotIn("note:", result.stderr)

    def test_quality_on_lossless_targets_notes_and_still_converts(self) -> None:
        for target in LOSSLESS:
            with self.subTest(target=target):
                result = image_cli(
                    "convert", "--to", target, str(self.source),
                    "--outdir", str(self.out / target), "-q", "80",
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertIn(
                    f"note: .{target} is lossless; --quality is ignored", result.stderr
                )
                self.assertTrue((self.out / target / f"noise.{target}").is_file())

    def test_quality_does_not_change_a_lossless_png(self) -> None:
        with_q = self._size("png", "-q", "10")
        without = self._size("png")
        self.assertEqual(with_q, without)

    def test_quality_range_is_still_validated(self) -> None:
        for value in ("0", "101"):
            with self.subTest(value=value):
                result = image_cli("convert", "--to", "jpg", str(self.source), "-q", value)
                self.assertEqual(result.exit_code, 2)


# --------------------------------------------------------------------------- colour modes


class ColourModeTests(TempDirTestCase):
    def _sources(self) -> list[Path]:
        made: dict[str, Image.Image] = {}
        bw = Image.new("1", (16, 16), 1)
        bw.paste(0, (0, 0, 8, 16))
        made["bw"] = bw
        grey = Image.new("L", (16, 16), 200)
        made["grey"] = grey
        made["grey16"] = grey16([65535, 32768, 0], size=15)
        made["palette"] = gradient().quantize(16)
        made["rgb"] = gradient()
        made["rgba"] = stripes([(255, 0, 0, 0), (0, 0, 0, 128)], "RGBA", 16)
        made["la"] = stripes([(0, 0), (90, 255)], "LA", 16)
        paths = []
        for name, image in made.items():
            path = self.root / f"{name}.png"
            image.save(path)
            paths.append(path)
        cmyk = self.root / "cmyk.jpg"
        Image.new("CMYK", (16, 16), (0, 0, 0, 0)).save(cmyk)
        return [*paths, cmyk]

    def test_every_mode_converts_to_every_target(self) -> None:
        sources = self._sources()
        for extension in ALL_EXTENSIONS:
            with self.subTest(target=extension):
                destination = self.out / extension
                result = image_cli(
                    "convert", "--to", extension, *map(str, sources), "--outdir", str(destination)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertIn(f"pillow: {len(sources)} converted", result.stdout)
                for source in sources:
                    output = destination / f"{source.stem}.{extension}"
                    self.assertTrue(output.is_file(), f"{output.name} missing")
                    with Image.open(source) as original:
                        expected_size = original.size
                    self.assertEqual(reopen(output).size, expected_size, output.name)

    def test_16_bit_grey_is_scaled_not_clipped(self) -> None:
        source = self.root / "grey16.png"
        grey16([65535, 32768, 0]).save(source)
        self.assertEqual(opened(source).mode, "I;16", "fixture is not 16-bit")

        for extension in ("jpg", "bmp", "ppm", "webp", "gif", "tga", "png", "tif", "avif"):
            with self.subTest(target=extension):
                result = image_cli(
                    "convert", "--to", extension, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = reopen(self.out / f"grey16.{extension}")
                values = []
                for fraction in STRIPE_CENTRES:
                    pixel = output.convert("RGB" if output.mode == "P" else output.mode)
                    point = (int(output.width * fraction), output.height // 2)
                    value = pixel.getpixel(point)
                    if isinstance(value, tuple):
                        value = sum(value[:3]) / 3
                    if output.mode.startswith("I"):
                        value = value / 257
                    values.append(value)
                white, mid, black = values
                self.assertGreaterEqual(white, 240, values)
                self.assertTrue(100 <= mid <= 156, values)
                self.assertLessEqual(black, 15, values)


# --------------------------------------------------------------------------- animation


def animated_frames(path: Path, durations: list[int], loop: int = 2) -> None:
    frames = [Image.new("RGB", (16, 16), color) for color in (RED, GREEN, BLUE)]
    frames[0].save(
        path, save_all=True, append_images=frames[1:], duration=durations, loop=loop
    )


class AnimationTests(TempDirTestCase):
    DURATIONS = [100, 200, 300]

    def test_animated_targets_keep_every_frame_duration_and_loop(self) -> None:
        source = self.root / "anim.gif"
        animated_frames(source, self.DURATIONS, loop=2)
        for target in ("gif", "webp", "avif"):
            with self.subTest(target=target):
                destination = self.out / target
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(destination)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = opened(destination / f"anim.{target}")
                self.assertEqual(getattr(output, "n_frames", 1), 3)

                seen_durations, centres = [], []
                for index in range(3):
                    output.seek(index)
                    output.load()
                    seen_durations.append(output.info.get("duration"))
                    centres.append(output.convert("RGB").getpixel((8, 8)))
                slack = 10 if target == "avif" else 0
                for seen, wanted in zip(seen_durations, self.DURATIONS):
                    self.assertIsNotNone(seen)
                    self.assertLessEqual(abs(seen - wanted), slack, seen_durations)
                for seen, wanted in zip(centres, (RED, GREEN, BLUE)):
                    self.assertTrue(near(seen, wanted, 40), centres)
                if target in {"gif", "webp"}:
                    output.seek(0)
                    output.load()
                    self.assertEqual(output.info.get("loop"), 2)
                output.close()

    def test_animated_webp_source_keeps_frames_when_written_as_gif(self) -> None:
        source = self.root / "anim.webp"
        animated_frames(source, self.DURATIONS)
        result = image_cli("convert", "--to", "gif", str(source), "--outdir", str(self.out))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(opened(self.out / "anim.gif").n_frames, 3)

    def test_other_targets_get_only_the_first_frame(self) -> None:
        source = self.root / "anim.gif"
        animated_frames(source, self.DURATIONS)
        for target in ("png", "jpg", "bmp", "tif", "tga", "heic", "heif", "ppm"):
            with self.subTest(target=target):
                destination = self.out / target
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(destination)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(
                    [p.name for p in destination.iterdir()], [f"anim.{target}"]
                )
                output = opened(destination / f"anim.{target}")
                self.assertEqual(getattr(output, "n_frames", 1), 1)
                self.assertTrue(near(output.convert("RGB").getpixel((8, 8)), RED, 40))

    def test_single_frame_source_to_animated_target_is_one_frame(self) -> None:
        source = self.root / "still.png"
        gradient().save(source)
        for target in ("gif", "webp", "avif"):
            with self.subTest(target=target):
                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(self.out)
                )
                self.assertEqual(result.exit_code, 0, combined(result))
                output = opened(self.out / f"still.{target}")
                self.assertEqual(getattr(output, "n_frames", 1), 1)

    def test_handmade_three_frame_gif_works_end_to_end(self) -> None:
        source = self.root / "anim.gif"
        source.write_bytes(three_frame_gif())
        result = image_cli("gif2png", str(source))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(getattr(opened(self.root / "anim.png"), "n_frames", 1), 1)


# --------------------------------------------------------------------------- safe writes


class SafeWriteTests(TempDirTestCase):
    def test_encoder_failure_leaves_no_target_and_no_temp_file(self) -> None:
        source = self.root / "a.bmp"
        gradient().save(source)

        def failing_save(self, fp, format=None, **params):  # noqa: ANN001, ANN003, ANN202
            if hasattr(fp, "write"):
                fp.write(b"PARTIAL")
            else:
                Path(fp).write_bytes(b"PARTIAL")
            raise OSError("simulated encoder failure")

        for target in ("jpg", "png"):
            with self.subTest(target=target):
                with mock.patch.object(Image.Image, "save", failing_save):
                    result = image_cli("convert", "--to", target, str(source))
                self.assertEqual(result.exit_code, 1, combined(result))
                self.assertIn("fail ", result.stderr)
                self.assertEqual(
                    [p for p in listing(self.root) if p != "a.bmp"], [], "left files behind"
                )

    def test_encoder_failure_keeps_an_existing_target_untouched(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        target = self.root / "a.jpg"
        target.write_bytes(b"precious")

        def failing_save(self, fp, format=None, **params):  # noqa: ANN001, ANN003, ANN202
            if hasattr(fp, "write"):
                fp.write(b"PARTIAL")
            else:
                Path(fp).write_bytes(b"PARTIAL")
            raise OSError("simulated encoder failure")

        with mock.patch.object(Image.Image, "save", failing_save):
            result = image_cli("png2jpg", str(source), "--overwrite")

        self.assertEqual(result.exit_code, 1, combined(result))
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual(listing(self.root), ["a.jpg", "a.png"])

    def test_unreadable_source_keeps_an_existing_target_untouched(self) -> None:
        (self.root / "a.png").write_bytes(b"this is not an image")
        target = self.root / "a.jpg"
        target.write_bytes(b"precious")

        result = image_cli("png2jpg", str(self.root / "a.png"), "--overwrite")

        self.assertEqual(result.exit_code, 1, combined(result))
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual(listing(self.root), ["a.jpg", "a.png"])

    def test_truncated_source_leaves_nothing_behind(self) -> None:
        full = self.root / "full.png"
        noisy(64, 64).save(full)
        data = full.read_bytes()
        full.unlink()
        (self.root / "cut.png").write_bytes(data[: len(data) // 2])

        for target in ("jpg", "webp"):
            with self.subTest(target=target):
                result = image_cli("convert", "--to", target, str(self.root / "cut.png"))
                self.assertEqual(result.exit_code, 1, combined(result))
                self.assertIn("fail ", result.stderr)
                self.assertEqual(listing(self.root), ["cut.png"])

    def test_successful_overwrite_replaces_the_target_and_leaves_no_temp(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        (self.root / "a.jpg").write_bytes(b"old")

        result = image_cli("png2jpg", str(source), "--overwrite")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(sniff(self.root / "a.jpg"), "jpeg")
        self.assertEqual(listing(self.root), ["a.jpg", "a.png"])


# --------------------------------------------------------------------------- unreadable sources


class UnreadableSourceTests(TempDirTestCase):
    def test_bad_files_fail_individually_and_the_batch_continues(self) -> None:
        (self.root / "a_text.png").write_text("hello, I am not a picture", encoding="utf-8")
        (self.root / "b_empty.png").write_bytes(b"")
        full = self.root / "c_full.png"
        noisy(64, 64).save(full)
        (self.root / "d_cut.png").write_bytes(full.read_bytes()[:200])
        gradient().save(self.root / "e_good.png")
        full.unlink()

        result = image_cli("png2jpg", "-d", str(self.root))

        self.assertEqual(result.exit_code, 1, combined(result))
        for name in ("a_text", "b_empty", "d_cut"):
            line = next(
                (l for l in result.stderr.splitlines() if f"{name}.png" in l), ""
            )
            self.assertTrue(line.startswith("fail "), f"{name}: {result.stderr!r}")
            self.assertIn(f"{name}.png:", line)
            self.assertGreater(len(line.split(f"{name}.png:", 1)[1].strip()), 0)
        self.assertEqual(sniff(self.root / "e_good.jpg"), "jpeg")
        self.assertIn("pillow: 1 converted, 3 failed", result.stdout)
        self.assertEqual(
            sorted(p for p in listing(self.root) if p.endswith(".jpg")), ["e_good.jpg"]
        )

    def test_failure_to_open_is_reported_through_convert_file(self) -> None:
        bad = self.root / "x.png"
        bad.write_text("nope", encoding="utf-8")
        result = convert_file(bad, self.root / "x.jpg", backend="pillow", executable=[])
        self.assertEqual(result.status, "failed")
        self.assertFalse((self.root / "x.jpg").exists())


# --------------------------------------------------------------------------- content over extension


class ContentOverExtensionTests(TempDirTestCase):
    def test_content_decides_not_the_extension(self) -> None:
        picture = stripes([RED, GREEN, BLUE], mode="RGB", size=32)
        cases = {
            "photo.jpg": ("heic", "png"),
            "x.webp": ("png", "jpg"),
            "y.png": ("jpg", "webp"),
            "z.gif": ("webp", "png"),
            "w.heic": ("png", "jpg"),
        }
        for name, (really, target) in cases.items():
            with self.subTest(name=name, content=really, target=target):
                source = self.root / name
                save_any(picture, source.with_name("tmp." + really))
                source.write_bytes(source.with_name("tmp." + really).read_bytes())
                self.assertEqual(opened(source).size, (32, 32), "fixture check")

                result = image_cli(
                    "convert", "--to", target, str(source), "--outdir", str(self.out)
                )

                self.assertEqual(result.exit_code, 0, combined(result))
                output = reopen(self.out / f"{source.stem}.{target}").convert("RGB")
                self.assertEqual(output.size, (32, 32))
                self.assertTrue(near(sample(output, 1 / 6, "RGB"), RED, 40))
                self.assertTrue(near(sample(output, 5 / 6, "RGB"), BLUE, 40))


# --------------------------------------------------------------------------- literal names


class LiteralNameTests(TempDirTestCase):
    STEMS = ["100%d", "50%", "shot%03d", "%d0%bf%d1%80", "%%", "a b", "x[0]", "café", "it's", "a&b#1"]

    def test_special_characters_in_file_names_are_literal(self) -> None:
        for stem in self.STEMS:
            with self.subTest(stem=stem):
                directory = self.root / f"case-{self.STEMS.index(stem)}"
                directory.mkdir()
                gradient().save(directory / f"{stem}.png")

                result = image_cli("png2jpg", str(directory / f"{stem}.png"))

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(listing(directory), sorted([f"{stem}.jpg", f"{stem}.png"]))
                self.assertEqual(sniff(directory / f"{stem}.jpg"), "jpeg")
                self.assertEqual(result.stderr, "", "unexpected note or warning")

    def test_percent_in_source_and_output_directories_is_literal(self) -> None:
        source_dir = self.root / "in%d"
        source_dir.mkdir()
        gradient().save(source_dir / "50%.png")
        result = image_cli(
            "png2jpg", str(source_dir / "50%.png"), "--outdir", str(self.root / "o%d" / "sub%03d")
        )
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(
            listing(self.root),
            ["in%d", "in%d/50%.png", "o%d", "o%d/sub%03d", "o%d/sub%03d/50%.jpg"],
        )
        self.assertEqual(result.stderr, "")

    def test_scan_and_recursive_mirroring_with_percent_names(self) -> None:
        root = self.root / "scan%d"
        (root / "sub%d").mkdir(parents=True)
        gradient().save(root / "100%d.png")
        gradient().save(root / "sub%d" / "50%.png")
        out = self.root / "out%d"

        result = image_cli("png2jpg", "-d", str(root), "-r", "--outdir", str(out))

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(
            listing(out), ["100%d.jpg", "sub%d", "sub%d/50%.jpg"]
        )
        self.assertIn("pillow: 2 converted", result.stdout)

    def test_animated_percent_names_keep_frames(self) -> None:
        source = self.root / "anim%d.gif"
        animated_frames(source, [100, 100, 100])
        result = image_cli("gif2webp", str(source))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(opened(self.root / "anim%d.webp").n_frames, 3)
        result = image_cli("gif2png", str(source))
        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(getattr(opened(self.root / "anim%d.png"), "n_frames", 1), 1)

    def test_no_staging_note_is_printed(self) -> None:
        gradient().save(self.root / "100%d.png")
        dry = image_cli("png2jpg", str(self.root / "100%d.png"), "--outdir", str(self.root / "o%d"), "--dry-run")
        self.assertEqual(dry.exit_code, 0, combined(dry))
        self.assertEqual(dry.stderr, "")
        self.assertNotIn("renamed", combined(dry))


# --------------------------------------------------------------------------- large images


class LargeImageTests(TempDirTestCase):
    def test_image_over_pillows_bomb_limit_converts_without_warning(self) -> None:
        # 1-bit keeps the fixture and the png output small, while the pixel
        # count (182M) is past Pillow's error threshold (2 x 89M).
        side = 13500
        source = self.root / "big.png"
        Image.new("1", (side, side), 1).save(source)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["JZR_CONFIG_DIR"] = str(self.root)
        proc = subprocess.run(
            [
                sys.executable, "-W", "always", "-m", "jzr_tools",
                "image", "convert", "--to", "png", str(source), "--outdir", str(self.out),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=300,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(proc.stderr, "")
        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", None):
            self.assertEqual(opened(self.out / "big.png").size, (side, side))


# --------------------------------------------------------------------------- dry run


class DryRunTests(TempDirTestCase):
    def test_dry_run_lists_source_arrow_target_then_summary(self) -> None:
        a, b = self.root / "a.png", self.root / "b.png"
        gradient().save(a)
        gradient().save(b)

        result = image_cli("png2jpg", "-d", str(self.root), "--dry-run")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"{a} -> {self.root / 'a.jpg'}",
                f"{b} -> {self.root / 'b.jpg'}",
                "pillow: 2 planned",
            ],
        )
        self.assertEqual(listing(self.root), ["a.png", "b.png"])

    def test_dry_run_line_is_the_line_a_real_conversion_prints_without_the_size(self) -> None:
        a = self.root / "a.png"
        gradient().save(a)
        planned = image_cli("png2jpg", str(a), "--dry-run").stdout.splitlines()[0]
        real = image_cli("png2jpg", str(a)).stdout.splitlines()[0]
        self.assertEqual(real, with_size(planned, self.root / "a.jpg"))

    def test_dry_run_reports_skips_as_usual_and_writes_nothing(self) -> None:
        gradient().save(self.root / "a.png")
        gradient().save(self.root / "b.png")
        (self.root / "b.jpg").write_bytes(b"sentinel")

        result = image_cli("png2jpg", "-d", str(self.root), "--dry-run")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertIn("target exists (use --overwrite)", result.stderr)
        self.assertIn("pillow: 1 planned, 1 skipped", result.stdout)
        self.assertFalse((self.root / "a.jpg").exists())
        self.assertEqual((self.root / "b.jpg").read_bytes(), b"sentinel")

    def test_dry_run_does_not_open_sources(self) -> None:
        (self.root / "garbage.png").write_bytes(b"not an image")

        result = image_cli("png2jpg", "-d", str(self.root), "--dry-run")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertIn("pillow: 1 planned", result.stdout)

    def test_dry_run_help_text(self) -> None:
        for command in (["convert", "-h"], ["png2jpg", "-h"]):
            with self.subTest(command=command):
                text = " ".join(image_cli(*command).stdout.split())
                self.assertIn("Show what would be converted; write nothing.", text)
                self.assertNotIn("Print commands without running them", text)


# --------------------------------------------------------------------------- lazy imports


class ImportLaziness(TempDirTestCase):
    def _observe(self, *args: str) -> Observed:
        return Observed(list(args))

    def _assert(self, obs: Observed, *, pil: bool, heif: bool) -> None:
        self.assertEqual(obs.exit_code, "0", obs.describe())
        self.assertEqual(obs.loaded("PIL"), pil, f"PIL loaded={obs.loaded('PIL')}\n{obs.describe()}")
        self.assertEqual(
            obs.loaded("pillow_heif"), heif, f"pillow_heif loaded={obs.loaded('pillow_heif')}"
        )

    def test_help_loads_neither(self) -> None:
        for args in (["image", "-h"], ["image", "convert", "-h"], ["image", "png2jpg", "-h"]):
            with self.subTest(args=args):
                self._assert(self._observe(*args), pil=False, heif=False)

    def test_dry_runs_load_neither(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        heic = self.root / "b.heic"
        save_heif(gradient(), heic)
        for args in (
            ["image", "png2jpg", str(source), "--dry-run"],
            ["image", "png2heic", str(source), "--dry-run"],
            ["image", "heic2png", str(heic), "--dry-run"],
            ["image", "convert", "--to", "webp", "-d", str(self.root), "--dry-run"],
        ):
            with self.subTest(args=args):
                obs = self._observe(*args)
                self._assert(obs, pil=False, heif=False)
                self.assertIn("planned", obs.stdout)

    def test_png_to_jpg_loads_pillow_but_not_pillow_heif(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        obs = self._observe("image", "png2jpg", str(source))
        self._assert(obs, pil=True, heif=False)
        self.assertTrue((self.root / "a.jpg").is_file())

    def test_other_non_heif_conversions_do_not_load_pillow_heif(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        for target in ("webp", "avif", "gif", "bmp"):
            with self.subTest(target=target):
                obs = self._observe("image", "convert", "--to", target, str(source))
                self._assert(obs, pil=True, heif=False)

    def test_heic_target_loads_both(self) -> None:
        source = self.root / "a.png"
        gradient().save(source)
        for target in ("heic", "heif"):
            with self.subTest(target=target):
                obs = self._observe("image", "convert", "--to", target, str(source))
                self._assert(obs, pil=True, heif=True)
                self.assertEqual(sniff(self.root / f"a.{target}"), "heif")

    def test_heic_source_loads_both(self) -> None:
        source = self.root / "a.heic"
        save_heif(gradient(), source)
        obs = self._observe("image", "heic2png", str(source))
        self._assert(obs, pil=True, heif=True)
        self.assertTrue((self.root / "a.png").is_file())

    def test_source_that_needs_pillow_heif_to_be_identified_loads_it(self) -> None:
        disguised = self.root / "photo.jpg"
        save_heif(gradient(), self.root / "real.heic")
        disguised.write_bytes((self.root / "real.heic").read_bytes())
        (self.root / "real.heic").unlink()

        obs = self._observe("image", "convert", "--to", "png", str(disguised))

        self._assert(obs, pil=True, heif=True)
        self.assertEqual(reopen(self.root / "photo.png").size, (16, 16))

    @unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
    def test_magick_runs_load_neither(self) -> None:
        source = self.root / "a.png"
        source.write_bytes(ONE_PIXEL_PNG)
        obs = self._observe("image", "png2jpg", str(source), "-b", "magick")
        self._assert(obs, pil=False, heif=False)
        self.assertTrue((self.root / "a.jpg").is_file())


# --------------------------------------------------------------------------- magick signature check


def fake_magick_writing(data: bytes):  # noqa: ANN201
    """Stand-in for ``subprocess.run`` that exits 0 after writing ``data``."""

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003, ANN202
        cwd = kwargs.get("cwd")
        written = Path(command[-1])
        if cwd is not None and not written.is_absolute():
            written = Path(cwd) / written
        written.write_bytes(data)
        return mock.Mock(returncode=0, stdout="", stderr="")

    return fake_run


def ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00" + brand + b"mif1"


class MagickSignatureCheckTests(TempDirTestCase):
    def _run(self, name: str, data: bytes):  # noqa: ANN202
        source = self.root / "src.png"
        source.write_bytes(ONE_PIXEL_PNG)
        target = self.root / name
        with mock.patch(
            "jzr_tools.core.images.subprocess.run", side_effect=fake_magick_writing(data)
        ):
            result = convert_file(source, target, backend="magick", executable=["magick"])
        return result, target

    def test_png_data_under_a_heic_name_fails_and_is_removed(self) -> None:
        result, target = self._run("x.heic", ONE_PIXEL_PNG)

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)
        self.assertIn("PNG", result.detail)
        self.assertIn(".heic", result.detail)
        self.assertFalse(target.exists())
        self.assertEqual(listing(self.root), ["src.png"])

    def test_detail_names_both_formats_for_other_mismatches(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        for name, data, wrote in (
            ("x.jpg", ONE_PIXEL_PNG, "PNG"),
            ("x.png", jpeg, "JPEG"),
            ("x.heif", ONE_PIXEL_PNG, "PNG"),
            ("x.webp", jpeg, "JPEG"),
        ):
            with self.subTest(name=name):
                result, target = self._run(name, data)
                self.assertEqual(result.status, "failed")
                self.assertIn(wrote, result.detail)
                self.assertIn(Path(name).suffix, result.detail)
                self.assertFalse(target.exists())

    def test_matching_signatures_pass(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        for name, data in (
            ("x.png", ONE_PIXEL_PNG),
            ("x.jpg", jpeg),
            ("x.jpeg", jpeg),
            ("x.heic", ftyp(b"heic")),
            ("x.gif", b"GIF89a" + b"\x00" * 16),
            ("x.bmp", b"BM" + b"\x00" * 16),
            ("x.webp", b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8),
        ):
            with self.subTest(name=name):
                result, target = self._run(name, data)
                self.assertEqual(result.status, "converted", result.detail)
                self.assertEqual(target.read_bytes(), data)
                target.unlink()

    def test_formats_without_a_signature_are_not_checked(self) -> None:
        result, target = self._run("x.tga", b"\x00" * 40)
        self.assertEqual(result.status, "converted", result.detail)
        self.assertTrue(target.exists())

    def test_ambiguous_heif_brands_are_not_checked(self) -> None:
        for name in ("x.avif", "x.heic", "x.heif"):
            with self.subTest(name=name):
                result, target = self._run(name, ftyp(b"mif1"))
                self.assertEqual(result.status, "converted", result.detail)
                target.unlink()

    def test_percent_target_is_checked_and_cleaned_up_too(self) -> None:
        source = self.root / "src.png"
        source.write_bytes(ONE_PIXEL_PNG)
        target = self.root / "o%d" / "100%d.heic"
        with mock.patch(
            "jzr_tools.core.images.subprocess.run",
            side_effect=fake_magick_writing(ONE_PIXEL_PNG),
        ):
            result = convert_file(source, target, backend="magick", executable=["magick"])

        self.assertEqual(result.status, "failed")
        self.assertIn("PNG", result.detail)
        self.assertFalse(target.exists())
        self.assertEqual([p for p in listing(self.root) if p.endswith((".heic", ".png")) and "src" not in p], [])

    def test_cli_reports_the_mismatch_as_a_failure(self) -> None:
        source = self.root / "src.png"
        source.write_bytes(ONE_PIXEL_PNG)
        with mock.patch(
            "jzr_tools.core.images.subprocess.run",
            side_effect=fake_magick_writing(ONE_PIXEL_PNG),
        ), mock.patch(
            "jzr_tools.commands.image.resolve_backend", return_value=("magick", ["magick"])
        ):
            result = image_cli("png2heic", str(source))

        self.assertEqual(result.exit_code, 1, combined(result))
        self.assertIn(f"fail {source}:", result.stderr)
        self.assertIn("magick: 1 failed", result.stdout)
        self.assertFalse((self.root / "src.heic").exists())

    @unittest.skipUnless(HAS_MAGICK, "needs ImageMagick")
    def test_real_magick_never_leaves_png_data_under_a_heic_name(self) -> None:
        source = self.root / "src.png"
        gradient().save(source)

        result = image_cli("png2heic", str(source), "-b", "magick")

        target = self.root / "src.heic"
        if result.exit_code == 0:
            # This ImageMagick build has a HEIC encoder, so the bytes are HEIF.
            self.assertEqual(sniff(target), "heif")
        else:
            self.assertEqual(result.exit_code, 1, combined(result))
            self.assertIn("fail ", result.stderr)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

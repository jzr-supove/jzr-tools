"""Plain Python image helpers."""

from __future__ import annotations

from pathlib import Path
import subprocess

from jzr_tools.core.system import resolve_executable


def convert_png_to_jpg(source: Path | str, *, quality: int = 92) -> Path:
    """Convert a PNG into a JPG using ImageMagick."""
    source_path = Path(source)
    if source_path.suffix.lower() == ".png":
        png_path = source_path
        jpg_path = source_path.with_suffix(".jpg")
    else:
        # Anything else is a basename. Appending keeps dotted names such as
        # `shot.v2` intact, where `with_suffix` would replace the `.v2`.
        png_path = source_path.with_name(f"{source_path.name}.png")
        jpg_path = source_path.with_name(f"{source_path.name}.jpg")
        if not png_path.is_file() and source_path.suffix and source_path.is_file():
            raise ValueError("Expected a PNG file path or a basename without an extension.")

    if not png_path.is_file():
        raise FileNotFoundError(f"PNG file not found: {png_path}")

    magick = resolve_executable("magick")
    if magick is None:
        raise RuntimeError("ImageMagick command 'magick' was not found on PATH.")

    try:
        subprocess.run(
            [magick, str(png_path), "-quality", str(quality), str(jpg_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        error_output = (exc.stderr or exc.stdout or "").strip()
        message = error_output or "ImageMagick failed to convert the image."
        raise RuntimeError(message) from exc

    return jpg_path

"""Per-user folders jzr keeps its own files in."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sys


def data_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return jzr's per-user data folder, whether or not it exists."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if environ.get("JZR_DATA_DIR"):
        return Path(environ["JZR_DATA_DIR"])
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    if platform == "win32":
        local = environ.get("LOCALAPPDATA")
        return (Path(local) if local else home / "AppData" / "Local") / "jzr"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "jzr"
    configured = environ.get("XDG_DATA_HOME", "")
    # The XDG rules say a relative path here is to be ignored.
    base = Path(configured) if os.path.isabs(configured) else home / ".local" / "share"
    return base / "jzr"

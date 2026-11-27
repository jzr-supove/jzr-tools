"""Expected result-line text for the size that a written file ends with.

The size always comes from the file on disk, so a test cannot pass by
hard-coding what the code under test prints.
"""

from __future__ import annotations

import os
from pathlib import Path

from jzr_tools.core.media import format_size


def size_text(path: str | os.PathLike[str]) -> str:
    """The size of ``path`` as jzr writes it, such as ``628.3 kB``."""
    return format_size(Path(path).stat().st_size)


def with_size(line: str, path: str | os.PathLike[str]) -> str:
    """``line`` as it ends once ``path``'s size is added.

    With details (``... (480x270, 15 fps)``) the size becomes the last item in
    the parentheses; without any (``a.png -> a.jpg``) it gets parentheses of
    its own.
    """
    size = size_text(path)
    if line.endswith(")"):
        return f"{line[:-1]}, {size})"
    return f"{line} ({size})"

"""Finding sources and planning outputs for batch commands.

Shared by every command that turns files into other files (`jzr image
convert`, the video and audio commands): which files to read, where each
output goes, and the record of what happened to one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence


def normalize_extension(value: str) -> str:
    """Return a bare, lowercased extension such as ``jpg``."""
    return value.strip().lstrip(".").lower()


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a single file conversion."""

    source: Path
    target: Path
    # The external command, or empty when the built-in engine does the work.
    command: tuple[str, ...]
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def target_for(source: Path, target_extension: str, *, outdir: Path | None = None) -> Path:
    """Return the output path for ``source`` rendered as ``target_extension``."""
    parent = outdir if outdir is not None else source.parent
    return parent / f"{source.stem}.{normalize_extension(target_extension)}"


def _path_key(path: Path) -> str:
    """Return a comparison key that treats paths differing only by case as equal."""
    return str(path.resolve()).casefold()


def plan_targets(
    sources: Sequence[Path],
    target_extension: str,
    *,
    outdir: Path | None = None,
    root: Path | None = None,
) -> list[Path]:
    """Return one distinct output path per source, in order.

    With both ``outdir`` and ``root``, each source's directory relative to
    ``root`` is recreated under ``outdir``. Sources that would still share an
    output (``x.png`` and ``x.webp``) get ``-2``, ``-3``, ... appended to the
    stem, so no file in a run is written twice.
    """
    claimed: set[str] = set()
    targets: list[Path] = []
    for source in sources:
        directory = outdir
        if outdir is not None and root is not None:
            try:
                directory = outdir / source.parent.relative_to(root)
            except ValueError:
                directory = outdir

        target = target_for(source, target_extension, outdir=directory)
        if _path_key(target) == _path_key(source):
            # Converting a file onto itself is skipped later; it must not push
            # another source onto a suffixed name.
            targets.append(target)
            continue

        candidate = target
        counter = 2
        while _path_key(candidate) in claimed:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        claimed.add(_path_key(candidate))
        targets.append(candidate)

    return targets


def find_sources(
    directory: Path,
    *,
    extensions: set[str] | frozenset[str] | None = None,
    pattern: str | re.Pattern[str] | None = None,
    recursive: bool = False,
) -> list[Path]:
    """Collect candidate files under ``directory``.

    ``pattern`` is a regular expression searched against each file's path
    relative to ``directory`` (just the filename when not recursing).
    """
    regex = re.compile(pattern) if pattern is not None else None
    allowed = {normalize_extension(item) for item in extensions} if extensions else None

    candidates = directory.rglob("*") if recursive else directory.glob("*")
    matches: list[Path] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if allowed is not None and normalize_extension(path.suffix) not in allowed:
            continue
        if regex is not None and regex.search(path.relative_to(directory).as_posix()) is None:
            continue
        matches.append(path)

    return matches

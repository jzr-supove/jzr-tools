"""Plain Python filesystem helpers."""

from pathlib import Path


def list_entries(path: Path, *, include_hidden: bool = False) -> list[str]:
    """Return directory entries sorted with directories first."""
    entries = []
    for entry in path.iterdir():
        if not include_hidden and entry.name.startswith("."):
            continue
        display_name = f"{entry.name}/" if entry.is_dir() else entry.name
        entries.append((not entry.is_dir(), entry.name.casefold(), display_name))

    entries.sort()
    return [display_name for _, _, display_name in entries]

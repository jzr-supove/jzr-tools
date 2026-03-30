"""Plain Python system helpers."""

import shutil


def resolve_executable(command: str) -> str | None:
    """Return the resolved executable path if it exists."""
    return shutil.which(command)

"""Plain Python system helpers."""

import platform
import shutil
import subprocess


def resolve_executable(command: str) -> str | None:
    """Return the resolved executable path if it exists."""
    return shutil.which(command)


def _clipboard_command() -> list[str] | None:
    system = platform.system()

    if system == "Windows":
        return ["clip"]
    if system == "Darwin":
        return ["pbcopy"]

    if resolve_executable("wl-copy") is not None:
        return ["wl-copy"]
    if resolve_executable("xclip") is not None:
        return ["xclip", "-selection", "clipboard"]
    if resolve_executable("xsel") is not None:
        return ["xsel", "--clipboard", "--input"]

    return None


def copy_to_clipboard(text: str) -> None:
    """Copy text to the system clipboard using native clipboard commands."""
    command = _clipboard_command()
    if command is None:
        raise RuntimeError(
            "No clipboard command found. Install wl-clipboard, xclip, or xsel."
        )

    try:
        subprocess.run(
            command,
            input=text,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Clipboard command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.strip() or exc.stdout.strip()
        if not error:
            error = f"Clipboard command failed: {command[0]}"
        raise RuntimeError(error) from exc

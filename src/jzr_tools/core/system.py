"""Plain Python system helpers.

Standard-library modules are imported inside the functions that need them:
this module is loaded by commands that never touch the clipboard, and
every import adds to the start-up time of each `jzr` call.
"""


def _clipboard_command() -> list[str] | None:
    import platform
    import shutil

    system = platform.system()

    if system == "Windows":
        return ["clip"]
    if system == "Darwin":
        return ["pbcopy"]

    if shutil.which("wl-copy") is not None:
        return ["wl-copy"]
    if shutil.which("xclip") is not None:
        return ["xclip", "-selection", "clipboard"]
    if shutil.which("xsel") is not None:
        return ["xsel", "--clipboard", "--input"]

    return None


def copy_to_clipboard(text: str) -> None:
    """Copy text to the system clipboard using native clipboard commands."""
    import subprocess

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

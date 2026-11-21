"""User-defined shortcuts: the file they are kept in, and running one.

A binding is a name and the arguments of one command. It is run directly,
with no shell in between, so it behaves the same on every system.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

_FILE_NAME = "bindings.json"
_VERSION = 1
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _windows() -> bool:
    """Return whether this is Windows; asked at run time so tests can fake it."""
    return sys.platform == "win32"


class BindingError(Exception):
    """Something about a binding went wrong; the text is meant for the user."""


class ProgramNotFound(BindingError):
    """The program a binding starts with is not on PATH."""


def config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the folder jzr keeps its own files in."""
    environ = os.environ if environ is None else environ
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    if environ.get("JZR_CONFIG_DIR"):
        return Path(environ["JZR_CONFIG_DIR"])
    if os.name == "nt":
        roaming = environ.get("APPDATA")
        return (Path(roaming) if roaming else home / "AppData" / "Roaming") / "jzr"
    configured = environ.get("XDG_CONFIG_HOME", "")
    # The XDG rules say a relative path here is to be ignored.
    base = Path(configured) if os.path.isabs(configured) else home / ".config"
    return base / "jzr"


def bindings_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return where the bindings are kept, whether or not the file exists."""
    return config_dir(environ) / _FILE_NAME


def _invalid(path: Path, reason: str) -> BindingError:
    return BindingError(f"{path}: not a valid bindings file: {reason}")


def load(path: Path | None = None) -> dict[str, list[str]]:
    """Read the bindings; a missing file means there are none."""
    path = bindings_path() if path is None else Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise _invalid(path, f"it cannot be read ({exc})") from exc

    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _invalid(path, f"it is not JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise _invalid(path, "it does not hold a JSON object")
    found = document.get("bindings")
    if not isinstance(found, dict):
        raise _invalid(path, 'it has no "bindings" object')
    for name, arguments in found.items():
        well_formed = (
            isinstance(arguments, list)
            and arguments
            and all(isinstance(argument, str) for argument in arguments)
        )
        if not well_formed:
            raise _invalid(path, f'"{name}" is not a list of command words')
    return found


def save(bindings: Mapping[str, Sequence[str]], path: Path | None = None) -> None:
    """Write the bindings, replacing the file in one step."""
    path = bindings_path() if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": _VERSION,
        "bindings": {name: list(bindings[name]) for name in sorted(bindings)},
    }
    # Written next to the target and renamed over it, so an interrupted write
    # never leaves half a file. mkstemp makes it private to the owner, which
    # matters because bound commands often carry tokens.
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".bindings-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def is_valid_name(name: str) -> bool:
    """Return whether ``name`` can be a binding's name."""
    return _NAME.fullmatch(name) is not None


def split_command(parts: Sequence[str]) -> list[str]:
    """Turn what the user typed after the name into the words of a command.

    Several parts are already the words. A single part is a whole command
    line, and is split the way a shell would split it.
    """
    if len(parts) != 1:
        return list(parts)
    try:
        if _windows():
            return _split_windows(parts[0])
        return shlex.split(parts[0])
    except ValueError as exc:
        raise BindingError(f"The command could not be split into words: {exc}.") from exc


def _split_windows(line: str) -> list[str]:
    """Split a command line by the rules Windows programs read their own with.

    Double quotes group words, a backslash is literal unless backslashes run
    up to a double quote, where each pair is one backslash and an odd one
    out makes the quote literal. Paths keep their backslashes, and this reads
    back what ``describe`` writes.
    """
    words: list[str] = []
    word: list[str] = []
    started = quoted = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\":
            end = index
            while end < len(line) and line[end] == "\\":
                end += 1
            run = end - index
            if end < len(line) and line[end] == '"':
                word.append("\\" * (run // 2))
                if run % 2:
                    word.append('"')
                    end += 1
            else:
                word.append("\\" * run)
            started, index = True, end
            continue
        if char == '"':
            quoted, started = not quoted, True
        elif char in " \t\n\r" and not quoted:
            if started:
                words.append("".join(word))
                word, started = [], False
        else:
            word.append(char)
            started = True
        index += 1
    if quoted:
        raise ValueError("No closing quotation")
    if started:
        words.append("".join(word))
    return words


class MissingArguments(BindingError):
    """A binding was run with fewer arguments than its placeholders need."""

    def __init__(self, needed: int, given: int) -> None:
        self.needed = needed
        self.given = given
        noun = "argument" if needed == 1 else "arguments"
        super().__init__(f"needs {needed} {noun} (got {given})")


# One token per match: an escaped placeholder, a placeholder, or a number
# with a leading zero, which is refused. Every other brace is plain text.
_TOKEN = re.compile(r"\{\{([0-9]+|\*)\}\}|\{([1-9][0-9]*|\*)\}|\{(0[0-9]*)\}")


def _placeholders(word: str) -> list[str]:
    """Return the placeholders in ``word`` as written inside the braces."""
    found: list[str] = []
    for match in _TOKEN.finditer(word):
        if match.group(3) is not None:
            raise BindingError(f"{{{match.group(3)}}} is not a placeholder; they start at {{1}}.")
        if match.group(2) is not None:
            found.append(match.group(2))
    return found


def placeholder_signature(words: Sequence[str]) -> tuple[int, bool]:
    """Return the highest ``{N}`` a command uses and whether it uses ``{*}``.

    Raises BindingError when a placeholder is malformed.
    """
    numbers: set[int] = set()
    stars = 0
    for word in words:
        for inside in _placeholders(word):
            if inside == "*":
                if word != "{*}":
                    raise BindingError("{*} must be a word on its own.")
                stars += 1
                if stars > 1:
                    raise BindingError("{*} must be a word on its own.")
            else:
                numbers.add(int(inside))
    highest = max(numbers, default=0)
    for number in range(1, highest + 1):
        if number not in numbers:
            above = min(n for n in numbers if n > number)
            raise BindingError(
                f"{{{above}}} is used but {{{number}}} is not; number the arguments from {{1}}."
            )
    return highest, stars == 1


def check_placeholders(words: Sequence[str]) -> None:
    """Raise BindingError if a command's placeholders are malformed."""
    placeholder_signature(words)


def arity_note(words: Sequence[str]) -> str:
    """Say how many arguments a command takes; empty without placeholders."""
    highest, rest = placeholder_signature(words)
    if highest == 0:
        return "takes any arguments" if rest else ""
    note = f"takes {highest} argument" + ("" if highest == 1 else "s")
    return note + " and more" if rest else note


def has_placeholder(word: str) -> bool:
    """Return whether ``word`` holds a placeholder (escapes do not count)."""
    return any(match.group(2) is not None for match in _TOKEN.finditer(word))


def fill(words: Sequence[str], arguments: Sequence[str]) -> list[str]:
    """Put the arguments a binding is run with into its command.

    ``{N}`` becomes the N-th argument, inside its word and as one piece.
    ``{*}`` becomes the arguments after the highest N, one word each; without
    it they go on the end. ``{{N}}`` and ``{{*}}`` are the plain text.
    """
    highest, rest = placeholder_signature(words)
    if len(arguments) < highest:
        raise MissingArguments(highest, len(arguments))
    remaining = list(arguments[highest:])

    def replace(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return "{" + match.group(1) + "}"
        return arguments[int(match.group(2)) - 1]

    filled: list[str] = []
    for word in words:
        if word == "{*}":
            filled.extend(remaining)
        else:
            filled.append(_TOKEN.sub(replace, word))
    if not rest:
        filled.extend(remaining)
    return filled


_PWSH_TAIL = ["-inputformat", "xml", "-outputformat", "text"]


def powershell_script_block(words: Sequence[str]) -> str | None:
    """Return the text of a script block PowerShell passed in place of a word.

    PowerShell hands an unquoted ``{1}`` to a program as
    ``-encodedCommand <base64> ... -inputFormat xml -outputFormat text``.
    The block's text is given back in braces, or ``{...}`` when the base64
    does not decode; None when the words show no such thing.
    """
    import base64
    import binascii

    lowered = [word.lower() for word in words]
    for index, word in enumerate(lowered):
        if word != "-encodedcommand":
            continue
        after = lowered[index + 1 :]
        tail = any(after[at : at + 4] == _PWSH_TAIL for at in range(len(after) - 3))
        if not tail:
            continue
        try:
            text = base64.b64decode(words[index + 1], validate=True).decode("utf-16-le")
        except (IndexError, binascii.Error, UnicodeDecodeError, ValueError):
            return "{...}"
        return "{" + text + "}"
    return None


def describe(arguments: Sequence[str]) -> str:
    """Write a command as one line, quoted so that it reads back the same."""
    if _windows():
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


# cmd.exe reads these even inside double quotes, so no quoting passes them on.
_BATCH_UNSAFE = ("%", '"', "!", "\r", "\n")


def _batch_command_line(program: str, arguments: Sequence[str]) -> str:
    """Build the cmd.exe line that runs a batch file with literal arguments.

    Windows starts a ``.cmd`` or ``.bat`` file through cmd.exe, which would
    read ``&``, ``|``, ``<`` and ``>`` in the arguments as its own operators.
    Inside double quotes they are plain text; the few characters that cmd.exe
    still acts on there are refused.
    """
    for argument in arguments:
        unsafe = [char for char in _BATCH_UNSAFE if char in argument]
        if unsafe:
            shown = ", ".join(repr(char) for char in unsafe)
            raise BindingError(
                f"{argument!r} cannot be passed to the batch file "
                f"{os.path.basename(program)}: cmd.exe would act on {shown} in it."
            )
    shell = os.environ.get("COMSPEC") or "cmd.exe"
    if " " in shell:
        shell = f'"{shell}"'
    inner = " ".join(f'"{word}"' for word in (program, *arguments))
    # /d skips AutoRun commands; /s keeps the outer quotes from being reread.
    return f'{shell} /d /s /c "{inner}"'


def run(arguments: Sequence[str]) -> int:
    """Run a command in the foreground and return its exit code."""
    program = shutil.which(arguments[0])
    if program is None:
        raise ProgramNotFound(f"'{arguments[0]}' was not found on PATH.")
    command: str | list[str] = [program, *arguments[1:]]
    if _windows() and program.lower().endswith((".cmd", ".bat")):
        command = _batch_command_line(program, arguments[1:])
    # Whatever jzr has printed must come out before the program's own output.
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        process = subprocess.Popen(command)
    except OSError as exc:
        raise BindingError(f"'{arguments[0]}' could not be started: {exc.strerror or exc}.") from exc
    while True:
        try:
            code = process.wait()
            break
        except KeyboardInterrupt:
            # Ctrl+C reached the program too; let it finish in its own way.
            continue
    # A negative code is the signal that killed it; shells report 128 + signal.
    return 128 - code if code < 0 else code

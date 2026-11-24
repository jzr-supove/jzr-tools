"""Turn jzr commands into MCP tools, and run one for a tool call.

Tools are made from the Click commands, so a command's options and help are
described once. Which commands are offered is a fixed list: a test fails until
a new command is put in ``EXPOSED`` or in ``NOT_EXPOSED``.

Nothing here needs the ``mcp`` package; `_mcp_server` connects this to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from functools import lru_cache
import io
import json
import sys

import click

from jzr_tools.commands._tree import entries

# The longest text a call returns, in characters.
MAX_TEXT = 20_000


class ToolInputError(Exception):
    """The arguments of a call do not fit its tool."""


@dataclass(frozen=True)
class Exposed:
    """A command offered as a tool.

    ``kind`` is "read" (changes nothing), "write" (only makes new files) or
    "destructive". ``leave_out`` holds the properties that would make the tool
    do more than its kind says, or that make no sense without a terminal.
    ``fixed`` words are passed on every call.
    """

    path: str
    kind: str
    leave_out: frozenset[str] = frozenset()
    fixed: tuple[str, ...] = ()


def _read(path: str, *leave_out: str) -> Exposed:
    return Exposed(path, "read", frozenset(leave_out))


def _write(path: str) -> Exposed:
    # Without --overwrite these only ever add files.
    return Exposed(path, "write", frozenset({"overwrite"}))


EXPOSED: dict[str, Exposed] = {
    "image_compress": _write("image compress"),
    "image_convert": _write("image convert"),
    "image_formats": _read("image formats"),
    "image_info": _read("image info"),
    "image_resize": _write("image resize"),
    # --in-place replaces the sources, which no write tool may do.
    "image_strip": Exposed("image strip", "write", frozenset({"overwrite", "in_place"})),
    "video_compress": _write("video compress"),
    "video_convert": _write("video convert"),
    "video_formats": _read("video formats"),
    "video_gif": _write("video gif"),
    "video_info": _read("video info"),
    "video_trim": _write("video trim"),
    "audio_convert": _write("audio convert"),
    "audio_formats": _read("audio formats"),
    "audio_info": _read("audio info"),
    "audio_tags": _read(
        "audio tags", "set", "unset", "clear", "cover", "no_cover", "lyrics", "export_cover"
    ),
    "audio_tags_edit": Exposed("audio tags", "destructive", frozenset({"json"})),
    "audio_trim": _write("audio trim"),
    "ffmpeg_status": _read("ffmpeg status"),
    "file_rename": _read("file rename", "apply"),
    "file_rename_apply": Exposed("file rename", "destructive", frozenset({"apply"}), ("--apply",)),
    "text_base64": _read("text base64", "output", "overwrite", "copy"),
    "text_diff": _read("text diff", "color"),
    "text_slug": _read("text slug", "copy"),
    "text_url": _read("text url", "copy"),
    "uuid": _read("uuid", "copy"),
    "password": _read("password", "copy"),
    "random_int": _read("random int"),
    "random_pick": _read("random pick"),
    "random_shuffle": _read("random shuffle"),
    "hash": _read("hash"),
    "timestamp": _read("timestamp"),
    "data_json": _read("data json", "write"),
    "data_convert": _write("data convert"),
    "env_compare": _read("env compare"),
    "env_example": _read("env example", "output", "overwrite", "copy"),
    "jwt": _read("jwt"),
    "port_kill": Exposed("port kill", "destructive"),
    "port_show": _read("port show"),
    "host_status": _read("host status", "live", "interval"),
    "host_procs": _read("host procs", "kill", "force"),
}

_SETUP = "changes the user's own setup"
_SCRIPT = "prints a script for the user's shell"

NOT_EXPOSED: dict[str, str] = {
    "ffmpeg install": "downloads a program",
    "ffmpeg remove": "deletes the downloaded ffmpeg",
    "host clean": "asks the user what to remove",
    "bind add": _SETUP,
    "bind list": "only matters to the user's own shortcuts",
    "bind path": "only matters to the user's own shortcuts",
    "bind remove": _SETUP,
    "bind run": "runs any command the user has bound",
    "completion bash": _SCRIPT,
    "completion fish": _SCRIPT,
    "completion install": _SETUP,
    "completion powershell": _SCRIPT,
    "completion remove": _SETUP,
    "completion zsh": _SCRIPT,
}

# Click arguments have no help of their own.
_ARGUMENT_HELP = {
    "sources": "Files to work on. Without them every matching file in dir is taken.",
    "files": "The files to read.",
    "file": "The file to read.",
    "ports": "Port numbers.",
    "bounds": "The lowest and the highest number; left out, 1 and 100.",
    "items": "The items.",
    "text": "The text.",
    "value": "Unix time or a date; left out, now.",
    "token": "The token.",
    "pattern": "Only rows whose name or command line contains this.",
    "old": "The old file, as an absolute path.",
    "new": "The new file, as an absolute path.",
    "a": "The first file; left out, .env.example.",
    "b": "The second file; left out, .env.",
}

# Commands that take file names as plain text, so "-" can mean stdin.
_FILE_NAMES_AS_TEXT = frozenset({"data convert", "data json", "env compare", "env example"})

# Measured on a fast desktop with a one minute 1080p clip.
_TIMES = {
    "video compress": "A minute of 1080p takes about 15 seconds on a fast desktop, often more elsewhere",
    "video convert": (
        "A minute of 1080p takes about 2 seconds when the streams can be copied, "
        "15 seconds to encode, and over a minute to webm, on a fast desktop"
    ),
    "video gif": "Ten seconds of 1080p can take 30 seconds on a fast desktop",
    "video trim": (
        "A minute of 1080p takes about 15 seconds on a fast desktop; fast copies it at once"
    ),
    "audio convert": "Five minutes of sound take about 2 seconds on a fast desktop",
    "audio trim": "Five minutes of sound take about 2 seconds on a fast desktop",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    exposed: Exposed
    command: click.Command
    description: str
    schema: dict[str, object]


@dataclass(frozen=True)
class Output:
    """What a command printed. ``exit_code`` is None when it crashed."""

    stdout: str
    stderr: str
    exit_code: int | None
    crash: str = ""


@dataclass(frozen=True)
class CallResult:
    text: str
    is_error: bool
    data: object | None = None


def tools() -> list[ToolSpec]:
    """Every tool, with its schema."""
    return list(_specs().values())


@lru_cache(maxsize=1)
def _specs() -> dict[str, ToolSpec]:
    commands = {entry.path: entry.command for entry in entries()}
    specs = {}
    for name, exposed in EXPOSED.items():
        command = commands[exposed.path]
        specs[name] = ToolSpec(
            name, exposed, command, _description(exposed, command), _schema(exposed, command)
        )
    return specs


# -- describing a command ---------------------------------------------------


def property_name(param: click.Parameter) -> str:
    """``--max-side`` as max_side; an argument by its own name."""
    if isinstance(param, click.Option):
        return _flag(param).lstrip("-").replace("-", "_")
    return param.name or ""


def _flag(option: click.Option) -> str:
    return max((name for name in option.opts if name.startswith("--")), key=len)


def _offered(exposed: Exposed, command: click.Command) -> dict[str, click.Parameter]:
    """The parameters of a tool by property name, in the command's order."""
    found = {}
    for param in command.params:
        if getattr(param, "hidden", False):
            continue
        name = property_name(param)
        if name not in exposed.leave_out:
            found[name] = param
    return found


def _description(exposed: Exposed, command: click.Command) -> str:
    parts = [_plain(command.help or "")]
    takes_paths = any(
        isinstance(param.type, (click.Path, click.File)) for param in _offered(exposed, command).values()
    )
    if takes_paths or exposed.path in _FILE_NAMES_AS_TEXT:
        parts.append("Use absolute paths.")
    if exposed.path in _TIMES:
        parts.append(f"{_TIMES[exposed.path]}; run a big job in a shell instead.")
    return "\n\n".join(parts)


def _plain(help_text: str) -> str:
    """Help without Click's line wrapping marks; each paragraph on one line."""
    paragraphs = []
    for block in help_text.split("\n\n"):
        lines = [line.strip() for line in block.strip().splitlines()]
        if lines and lines[0] == "\b":
            paragraphs.append("\n".join(lines[1:]))
        elif lines:
            paragraphs.append(" ".join(lines))
    return "\n\n".join(paragraphs)


def _schema(exposed: Exposed, command: click.Command) -> dict[str, object]:
    properties = {}
    required = []
    for name, param in _offered(exposed, command).items():
        properties[name] = _property(param)
        if param.required:
            required.append(name)
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _property(param: click.Parameter) -> dict[str, object]:
    found: dict[str, object] = _value_schema(param)
    if _is_pair(param):
        found = {"type": "array", "items": {"type": "array", "items": found, "minItems": 2, "maxItems": 2}}
    elif _is_list(param):
        found = {"type": "array", "items": found}
        if param.required:
            found["minItems"] = 1
    default = param.default
    # Click marks "no default" with an object of its own.
    if type(default) in (str, int, float) or default is True:
        found["default"] = default
    if isinstance(param, click.Option):
        found["description"] = param.help or ""
    else:
        found["description"] = _ARGUMENT_HELP.get(param.name or "", "")
    return found


def _is_list(param: click.Parameter) -> bool:
    return param.nargs == -1 or bool(getattr(param, "multiple", False))


def _is_pair(param: click.Parameter) -> bool:
    return param.nargs == 2


def _value_schema(param: click.Parameter) -> dict[str, object]:
    kind = param.type
    if getattr(param, "is_flag", False):
        return {"type": "boolean"}
    if isinstance(kind, click.Choice):
        return {"type": "string", "enum": [str(choice) for choice in kind.choices]}
    if isinstance(kind, (click.IntRange, click.FloatRange)):
        found: dict[str, object] = {"type": "integer" if isinstance(kind, click.IntRange) else "number"}
        if kind.min is not None:
            found["exclusiveMinimum" if kind.min_open else "minimum"] = kind.min
        if kind.max is not None:
            found["exclusiveMaximum" if kind.max_open else "maximum"] = kind.max
        return found
    if isinstance(kind, click.types.IntParamType):
        return {"type": "integer"}
    if isinstance(kind, click.types.FloatParamType):
        return {"type": "number"}
    return {"type": "string"}


# -- calling a command ------------------------------------------------------


def argv(spec: ToolSpec, arguments: Mapping[str, object]) -> list[str]:
    """The command line of a call.

    Options are written as one word and arguments come after ``--``, so no
    value can be taken for an option.
    """
    offered = _offered(spec.exposed, spec.command)
    given = {name: value for name, value in arguments.items() if value is not None}
    for name in given:
        if name not in offered:
            takes = f" takes: {', '.join(offered)}" if offered else " takes no arguments"
            raise ToolInputError(f"unknown argument '{name}'; {spec.name}{takes}")

    options: list[str] = []
    positional: list[tuple[str, click.Parameter, list[str]]] = []
    for name, param in offered.items():
        words = _words(name, param, given[name]) if name in given else []
        if isinstance(param, click.Option):
            options += words
        else:
            positional.append((name, param, words))
            if param.required and not words:
                raise ToolInputError(f"{name} is required")

    if "sources" in offered and "dir" in offered and not given.get("sources") and "dir" not in given:
        raise ToolInputError("give sources, or dir to work on a whole folder")

    line = [*spec.exposed.path.split(), *spec.exposed.fixed, *options]
    rest = _positional_words(positional)
    return [*line, "--", *rest] if rest else line


def _positional_words(positional: Sequence[tuple[str, click.Parameter, list[str]]]) -> list[str]:
    """The argument words in order; one left out in front of another gets its default."""
    last = max((index for index, (_, _, words) in enumerate(positional) if words), default=-1)
    found: list[str] = []
    for index, (name, param, words) in enumerate(positional[: last + 1]):
        if not words:
            if type(param.default) is not str:
                raise ToolInputError(f"{name} is required when {positional[last][0]} is given")
            words = [param.default]
        found += words
    return found


def _words(name: str, param: click.Parameter, value: object) -> list[str]:
    """The words one property adds to the command line."""
    if getattr(param, "is_flag", False):
        if not isinstance(value, bool):
            raise ToolInputError(f"{name} must be true or false")
        return [_flag(param)] if value else []  # type: ignore[arg-type]

    if _is_pair(param):
        words = []
        for pair in _list(name, value):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ToolInputError(f"{name} must be a list of pairs, each with two strings")
            words += [_flag(param), *(_word(name, param, part) for part in pair)]  # type: ignore[arg-type]
        return words

    if _is_list(param):
        values = [_word(name, param, item) for item in _list(name, value)]
    else:
        values = [_word(name, param, value)]
    if isinstance(param, click.Option):
        return [f"{_flag(param)}={item}" for item in values]
    return values


def _list(name: str, value: object) -> Sequence[object]:
    if isinstance(value, (list, tuple)):
        return value
    if isinstance(value, dict):
        raise ToolInputError(f"{name} must be a list")
    return [value]


def _word(name: str, param: click.Parameter, value: object) -> str:
    wanted = _value_schema(param)["type"]
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if wanted == "integer":
        if number and float(value).is_integer():  # type: ignore[arg-type]
            return str(int(value))  # type: ignore[call-overload]
        raise ToolInputError(f"{name} must be an integer")
    if wanted == "number":
        if number:
            return str(value)
        raise ToolInputError(f"{name} must be a number")
    if isinstance(value, str) or number:
        return str(value)
    raise ToolInputError(f"{name} must be a string")


def run(argv: Sequence[str]) -> Output:
    """Run a jzr command line in this process, as if piped, and collect its output.

    It replaces ``sys.stdin``, ``sys.stdout`` and ``sys.stderr`` meanwhile, so
    only one call may run at a time.
    """
    from jzr_tools.cli import app

    out, err = _stream(), _stream()
    exit_code: int | None = 0
    crash = message = ""
    stdin = sys.stdin
    sys.stdin = _stream()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                app.main(args=list(argv), prog_name="jzr")
            except SystemExit as stop:
                if isinstance(stop.code, str):
                    exit_code, message = 1, stop.code + "\n"
                else:
                    exit_code = int(stop.code or 0)
            except Exception as error:
                exit_code = None
                first = (str(error).splitlines() or [""])[0]
                crash = f"{type(error).__name__}: {first}" if first else type(error).__name__
    finally:
        sys.stdin = stdin
    return Output(_text(out), _text(err) + message, exit_code, crash)


def _stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(
        io.BytesIO(), encoding="utf-8", errors="backslashreplace", newline="\n", write_through=True
    )


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    data = stream.buffer.getvalue()  # type: ignore[attr-defined]
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def call(name: str, arguments: Mapping[str, object] | None) -> CallResult:
    """Answer one tool call."""
    spec = _specs().get(name)
    if spec is None:
        return CallResult(f"unknown tool '{name}'", True)
    arguments = arguments or {}
    try:
        line = argv(spec, arguments)
    except ToolInputError as error:
        return CallResult(str(error), True)

    output = run(line)
    text, cut = _result_text(output)
    if output.exit_code is None:
        return CallResult(text, True)
    data = None
    if arguments.get("json") is True and output.exit_code in (0, 1) and not cut:
        try:
            data = json.loads(output.stdout)
        except ValueError:
            pass
    return CallResult(text, output.exit_code >= 2, data)


def _result_text(output: Output) -> tuple[str, bool]:
    # Errors are worth more than a long listing, so they keep their room.
    stderr, cut_err = _cut(output.stderr, MAX_TEXT // 4)
    stdout, cut_out = _cut(output.stdout, MAX_TEXT - len(stderr))
    parts = [stdout]
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if output.exit_code is None:
        parts.append(f"jzr failed: {output.crash}")
    elif output.exit_code:
        parts.append(f"[exit code {output.exit_code}]")
    text = ""
    for part in parts:
        # What a command printed is passed on as it is; the next part starts on its own line.
        text += part if not text or text.endswith("\n") else f"\n{part}"
    return text or "(no output)", cut_out or cut_err


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    end = text.rfind("\n", 0, limit + 1)
    if end <= 0:  # one line longer than the limit
        end = limit
    left = text[end:].strip("\n").count("\n") + 1
    return f"{text[:end]}\n... {left} more lines; narrow the input or use pattern", True

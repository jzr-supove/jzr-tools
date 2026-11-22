"""Tests for the tool layer behind the MCP server: which commands are offered as
tools, the JSON schema of each, how arguments become a command line, and how a
command is run and its output turned into a result.

The Click tree is walked here on its own, so the code under test is checked
against the real commands and not against itself. Nothing here needs the `mcp`
package.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import click

HERE = Path(__file__).resolve().parent
SRC_PATH = HERE.parent / "src"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from media_fixtures import HAVE_FFMPEG, ffmpeg_run  # noqa: E402

try:
    import jsonschema
except ImportError:  # pragma: no cover - depends on the environment
    jsonschema = None

LEFT_OUT_TOP = frozenset({"agent", "version", "killport"})


def tools_mod():
    return importlib.import_module("jzr_tools.commands._mcp_tools")


def tree_mod():
    return importlib.import_module("jzr_tools.commands._tree")


# -- the agreed tool table --------------------------------------------------

def _row(path: str, kind: str, leave_out: tuple[str, ...] = (), fixed: tuple[str, ...] = ()):
    return path, kind, frozenset(leave_out), fixed


EXPECTED_EXPOSED = {
    "image_info": _row("image info", "read"),
    "image_formats": _row("image formats", "read"),
    "video_info": _row("video info", "read"),
    "video_formats": _row("video formats", "read"),
    "audio_info": _row("audio info", "read"),
    "audio_formats": _row("audio formats", "read"),
    "audio_tags": _row(
        "audio tags", "read",
        ("set", "unset", "clear", "cover", "no_cover", "lyrics", "export_cover"),
    ),
    "ffmpeg_status": _row("ffmpeg status", "read"),
    "file_rename": _row("file rename", "read", ("apply",)),
    "text_base64": _row("text base64", "read", ("output", "overwrite", "copy")),
    "text_diff": _row("text diff", "read", ("color",)),
    "text_slug": _row("text slug", "read", ("copy",)),
    "text_url": _row("text url", "read", ("copy",)),
    "uuid": _row("uuid", "read", ("copy",)),
    "password": _row("password", "read", ("copy",)),
    "random_int": _row("random int", "read"),
    "random_pick": _row("random pick", "read"),
    "random_shuffle": _row("random shuffle", "read"),
    "hash": _row("hash", "read"),
    "timestamp": _row("timestamp", "read"),
    "data_json": _row("data json", "read", ("write",)),
    "env_compare": _row("env compare", "read"),
    "env_example": _row("env example", "read", ("output", "overwrite", "copy")),
    "jwt": _row("jwt", "read"),
    "port_show": _row("port show", "read"),
    "host_status": _row("host status", "read", ("live", "interval")),
    "host_procs": _row("host procs", "read", ("kill", "force")),
    "image_compress": _row("image compress", "write", ("overwrite",)),
    "image_convert": _row("image convert", "write", ("overwrite",)),
    "image_resize": _row("image resize", "write", ("overwrite",)),
    "video_compress": _row("video compress", "write", ("overwrite",)),
    "video_convert": _row("video convert", "write", ("overwrite",)),
    "video_trim": _row("video trim", "write", ("overwrite",)),
    "video_gif": _row("video gif", "write", ("overwrite",)),
    "audio_convert": _row("audio convert", "write", ("overwrite",)),
    "audio_trim": _row("audio trim", "write", ("overwrite",)),
    "audio_tags_edit": _row("audio tags", "destructive", ("json",)),
    "file_rename_apply": _row("file rename", "destructive", ("apply",), ("--apply",)),
    "port_kill": _row("port kill", "destructive"),
}

EXPECTED_NOT_EXPOSED = frozenset({
    "host clean",
    "bind add", "bind list", "bind path", "bind remove", "bind run",
    "completion bash", "completion fish", "completion install",
    "completion powershell", "completion remove", "completion zsh",
    "ffmpeg install", "ffmpeg remove",
})

ENCODERS = (
    "video_compress", "video_convert", "video_trim", "video_gif", "audio_convert", "audio_trim",
)
# Take file names as plain text, so whether they count as "path parameters" is a judgement call.
PATH_UNDECIDED = frozenset({"data_json", "env_compare", "env_example"})


# -- an independent view of the real commands ------------------------------

def _ctx(command: click.Command, name: str, parent: click.Context | None) -> click.Context:
    return click.Context(command, info_name=name, parent=parent, **command.context_settings)


_real_cache: dict[str, click.Command] = {}


def real_commands() -> dict[str, click.Command]:
    """Every visible runnable command of the real tree, by path, found without the code under test."""
    from jzr_tools.cli import app

    if _real_cache:
        return _real_cache
    found = _real_cache

    def visit(command: click.Command, ctx: click.Context, names: tuple[str, ...]) -> None:
        if isinstance(command, click.Group):
            for name in command.list_commands(ctx):
                child = command.get_command(ctx, name)
                if child is None or child.hidden or (not names and name in LEFT_OUT_TOP):
                    continue
                visit(child, _ctx(child, name, ctx), (*names, name))
        else:
            found[" ".join(names)] = command

    visit(app, _ctx(app, "jzr", None), ())
    return found


def prop_name(param: click.Parameter) -> str:
    if isinstance(param, click.Argument):
        return str(param.name)
    longest = max((opt for opt in param.opts if opt.startswith("--")), key=len)
    return longest[2:].replace("-", "_")


def long_flag(param: click.Parameter) -> str:
    return max((opt for opt in param.opts if opt.startswith("--")), key=len)


def offered_params(name: str) -> list[click.Parameter]:
    """The parameters of a tool's command that should become properties."""
    path, _kind, leave_out, _fixed = EXPECTED_EXPOSED[name]
    command = real_commands()[path]
    return [
        p for p in command.params
        if not getattr(p, "hidden", False) and prop_name(p) not in leave_out
    ]


def has_path_param(name: str) -> bool:
    return any(isinstance(p.type, (click.Path, click.File)) for p in offered_params(name))


def is_many(param: click.Parameter) -> bool:
    return bool(param.multiple) or param.nargs < 0


def is_flag(param: click.Parameter) -> bool:
    return bool(getattr(param, "is_flag", False))


def normalize(text: str) -> str:
    return " ".join(text.replace("\b", " ").split())


_tools_cache: dict[str, object] = {}


def tool(name: str):
    if not _tools_cache:
        for spec in tools_mod().tools():
            _tools_cache[spec.name] = spec
    try:
        return _tools_cache[name]
    except KeyError:
        raise AssertionError(f"no tool named {name!r}") from None


def argv(name: str, arguments):
    return tools_mod().argv(tool(name), arguments)


def split_at_dashes(words: list[str]) -> tuple[list[str], list[str] | None]:
    if "--" not in words:
        return words, None
    index = words.index("--")
    return words[:index], words[index + 1:]


def has_run(sequence: list[str], run: list[str]) -> bool:
    return any(sequence[i:i + len(run)] == run for i in range(len(sequence) - len(run) + 1))


# -- the base for tests that run commands ----------------------------------

class QuietCase(unittest.TestCase):
    """Keeps the user's bindings and colour settings out of every test."""

    def setUp(self) -> None:
        config = tempfile.TemporaryDirectory(prefix="jzr-mcp-config-")
        self.addCleanup(config.cleanup)
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("NO_COLOR", None)


class FolderCase(QuietCase):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="jzr-mcp-files-")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()

    def write(self, name: str, data: bytes | str) -> Path:
        path = self.folder / name
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def png(self, name: str = "a.png", size: tuple[int, int] = (200, 100)) -> Path:
        from PIL import Image

        path = self.folder / name
        Image.new("RGB", size, (10, 200, 30)).save(path)
        return path


@contextlib.contextmanager
def patched(path: str, callback):
    """Replace what a real command does; the Click layer around it stays real."""
    with mock.patch.object(real_commands()[path], "callback", callback):
        yield


def says(out: str = "", err: str = "", code: int | None = None):
    def callback(**_kwargs):
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        if code is not None:
            raise SystemExit(code)

    return callback


def raises(error: BaseException):
    def callback(**_kwargs):
        raise error

    return callback


def nonblank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# ===========================================================================
# the shared tree walk
# ===========================================================================

class TreeModuleTests(unittest.TestCase):
    def test_entries_are_the_visible_runnable_commands_in_cli_order(self) -> None:
        expected = list(real_commands())

        actual = [entry.path for entry in tree_mod().entries()]

        self.assertEqual(actual, expected)

    def test_each_entry_holds_the_real_command_its_names_and_its_reference_group(self) -> None:
        commands = real_commands()
        for entry in tree_mod().entries():
            with self.subTest(path=entry.path):
                self.assertIs(entry.command, commands[entry.path])
                self.assertEqual(entry.path, " ".join(entry.names))
                self.assertEqual(entry.group, entry.names[0] if len(entry.names) > 1 else "tools")

    def test_constants_keep_their_values(self) -> None:
        self.assertEqual(tree_mod().LEFT_OUT, LEFT_OUT_TOP)
        self.assertEqual(tree_mod().TOP_LEVEL, "tools")

    def test_hidden_commands_agent_and_the_killport_shortcut_are_not_entries(self) -> None:
        paths = {entry.path for entry in tree_mod().entries()}
        for path in ("text kebab", "version", "killport"):
            self.assertNotIn(path, paths)
        self.assertFalse(any(p == "agent" or p.startswith("agent ") for p in paths))

    def test_the_skill_text_module_offers_the_very_same_objects(self) -> None:
        text = importlib.import_module("jzr_tools.commands._skill_text")
        tree = tree_mod()
        for name in ("Entry", "entries", "LEFT_OUT", "TOP_LEVEL"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(text, name))
                self.assertIs(getattr(text, name), getattr(tree, name))

    def test_importing_the_tree_does_not_pull_in_the_skill_text(self) -> None:
        code = (
            "import sys, json\n"
            f"sys.path.insert(0, {str(SRC_PATH)!r})\n"
            "import jzr_tools.commands._tree\n"
            "print(json.dumps('jzr_tools.commands._skill_text' in sys.modules))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "check.py"
            script.write_text(code, encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(script)], cwd=tmp, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=120,
            )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout.decode().strip(), "false")


# ===========================================================================
# the module does not need the mcp package
# ===========================================================================

class NoMcpImportTests(unittest.TestCase):
    def _child(self, code: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "check.py"
            script.write_text(code, encoding="utf-8")
            env = {**os.environ, "JZR_CONFIG_DIR": tmp}
            return subprocess.run(
                [sys.executable, str(script)], cwd=tmp, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=180, env=env,
            )

    def test_importing_the_module_does_not_import_mcp(self) -> None:
        done = self._child(
            "import sys, json\n"
            f"sys.path.insert(0, {str(SRC_PATH)!r})\n"
            "import jzr_tools.commands._mcp_tools\n"
            "print(json.dumps([m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')]))\n"
        )

        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(done.stdout.decode()), [])

    def test_the_tools_list_and_a_call_work_with_mcp_made_unimportable(self) -> None:
        done = self._child(
            "import sys, json\n"
            f"sys.path.insert(0, {str(SRC_PATH)!r})\n"
            "sys.modules['mcp'] = None\n"
            "from jzr_tools.commands import _mcp_tools as m\n"
            "count = len(m.tools())\n"
            "result = m.call('uuid', {})\n"
            "print(json.dumps({'count': count, 'error': result.is_error, 'lines': len(result.text.split())}))\n"
        )

        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        report = json.loads(done.stdout.decode())
        self.assertEqual(report["count"], len(EXPECTED_EXPOSED))
        self.assertFalse(report["error"])
        self.assertEqual(report["lines"], 1)

    def test_a_run_never_writes_to_the_real_stdout(self) -> None:
        done = self._child(
            "import sys, json, click\n"
            f"sys.path.insert(0, {str(SRC_PATH)!r})\n"
            "from unittest import mock\n"
            "from jzr_tools.commands import generate\n"
            "from jzr_tools.commands import _mcp_tools as m\n"
            "def chatty(**kw):\n"
            "    print('LEAK print')\n"
            "    click.echo('LEAK echo')\n"
            "    sys.stdout.write('LEAK write\\n')\n"
            "    sys.stdout.buffer.write(b'LEAK buffer\\n')\n"
            "    print('LEAK err', file=sys.stderr)\n"
            "with mock.patch.object(generate.uuid_command, 'callback', chatty):\n"
            "    out = m.run(['uuid'])\n"
            "real = m.run(['uuid', '--count=2'])\n"
            "sys.stderr.write(json.dumps({'out': out.stdout, 'err': out.stderr, 'real': real.stdout}))\n"
        )

        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout, b"", "something reached the real stdout")
        report = json.loads(done.stderr.decode("utf-8"))
        self.assertEqual(
            report["out"], "LEAK print\nLEAK echo\nLEAK write\nLEAK buffer\n",
        )
        self.assertEqual(report["err"], "LEAK err\n")
        self.assertEqual(len(report["real"].split()), 2)


# ===========================================================================
# the allow-list
# ===========================================================================

class AllowListTests(unittest.TestCase):
    def test_exposed_has_exactly_the_agreed_tools(self) -> None:
        exposed = tools_mod().EXPOSED

        self.assertEqual(set(exposed), set(EXPECTED_EXPOSED))
        for name, (path, kind, leave_out, fixed) in EXPECTED_EXPOSED.items():
            with self.subTest(tool=name):
                entry = exposed[name]
                self.assertEqual(entry.path, path)
                self.assertEqual(entry.kind, kind)
                self.assertEqual(entry.leave_out, leave_out)
                self.assertIsInstance(entry.leave_out, frozenset)
                self.assertEqual(tuple(entry.fixed), fixed)

    def test_kinds_are_only_read_write_or_destructive(self) -> None:
        for name, entry in tools_mod().EXPOSED.items():
            with self.subTest(tool=name):
                self.assertIn(entry.kind, {"read", "write", "destructive"})

    def test_the_destructive_tools_are_exactly_the_three_that_change_or_kill(self) -> None:
        destructive = {n for n, e in tools_mod().EXPOSED.items() if e.kind == "destructive"}

        self.assertEqual(destructive, {"audio_tags_edit", "file_rename_apply", "port_kill"})

    def test_no_new_file_tool_can_replace_an_existing_output(self) -> None:
        for name, entry in tools_mod().EXPOSED.items():
            if entry.kind != "write":
                continue
            with self.subTest(tool=name):
                self.assertIn("overwrite", entry.leave_out)

    def test_not_exposed_lists_exactly_the_agreed_commands_each_with_a_reason(self) -> None:
        not_exposed = tools_mod().NOT_EXPOSED

        self.assertEqual(set(not_exposed), EXPECTED_NOT_EXPOSED)
        for path, reason in not_exposed.items():
            with self.subTest(path=path):
                self.assertIsInstance(reason, str)
                self.assertTrue(reason.strip(), "the reason is empty")

    def test_every_command_is_exposed_or_left_out_on_purpose(self) -> None:
        exposed_paths = {e.path for e in tools_mod().EXPOSED.values()}
        listed = exposed_paths | set(tools_mod().NOT_EXPOSED)
        every = {entry.path for entry in tree_mod().entries()}

        self.assertEqual(
            every - listed, set(),
            "these commands are new: add each to EXPOSED (as a tool) or to NOT_EXPOSED (with a reason)",
        )
        self.assertEqual(
            listed - every, set(),
            "these paths in EXPOSED or NOT_EXPOSED are not commands any more: remove or rename them",
        )

    def test_a_command_is_never_both_exposed_and_not_exposed(self) -> None:
        exposed_paths = {e.path for e in tools_mod().EXPOSED.values()}

        self.assertEqual(exposed_paths & set(tools_mod().NOT_EXPOSED), set())

    def test_only_audio_tags_and_file_rename_back_two_tools(self) -> None:
        by_path: dict[str, list[str]] = {}
        for name, entry in tools_mod().EXPOSED.items():
            by_path.setdefault(entry.path, []).append(name)

        doubled = {path: sorted(names) for path, names in by_path.items() if len(names) > 1}

        self.assertEqual(
            doubled,
            {
                "audio tags": ["audio_tags", "audio_tags_edit"],
                "file rename": ["file_rename", "file_rename_apply"],
            },
        )

    def test_left_out_names_are_real_properties_of_the_command(self) -> None:
        for name, entry in tools_mod().EXPOSED.items():
            command = real_commands()[entry.path]
            known = {prop_name(p) for p in command.params if not getattr(p, "hidden", False)}
            for left in entry.leave_out:
                with self.subTest(tool=name, left_out=left):
                    self.assertIn(left, known, f"{entry.path} has no property {left!r}: typo?")

    def test_the_fixed_flag_of_a_tool_is_a_real_option_of_its_command(self) -> None:
        for name, entry in tools_mod().EXPOSED.items():
            command = real_commands()[entry.path]
            flags = {opt for p in command.params for opt in p.opts}
            for flag in entry.fixed:
                with self.subTest(tool=name, fixed=flag):
                    self.assertIn(flag, flags)

    def test_tools_lists_every_exposed_tool_in_exposed_order_with_its_real_command(self) -> None:
        commands = real_commands()

        specs = tools_mod().tools()

        self.assertEqual([s.name for s in specs], list(tools_mod().EXPOSED))
        for spec in specs:
            with self.subTest(tool=spec.name):
                self.assertIs(spec.exposed, tools_mod().EXPOSED[spec.name])
                self.assertIs(spec.command, commands[spec.exposed.path])
                self.assertIsInstance(spec.description, str)
                self.assertIsInstance(spec.schema, dict)


# ===========================================================================
# property names
# ===========================================================================

class PropertyNameTests(unittest.TestCase):
    def test_the_rule_holds_for_every_parameter_of_every_command(self) -> None:
        for path, command in real_commands().items():
            for param in command.params:
                with self.subTest(command=path, param=param.name):
                    self.assertEqual(tools_mod().property_name(param), prop_name(param))

    def test_options_use_the_longest_long_name_and_arguments_their_own_name(self) -> None:
        params = {(path, p.name): p for path, c in real_commands().items() for p in c.params}

        self.assertEqual(tools_mod().property_name(params[("image compress", "max_side")]), "max_side")
        self.assertEqual(tools_mod().property_name(params[("image compress", "directory")]), "dir")
        self.assertEqual(tools_mod().property_name(params[("image compress", "target")]), "to")
        self.assertEqual(tools_mod().property_name(params[("image compress", "sources")]), "sources")
        self.assertEqual(tools_mod().property_name(params[("image info", "no_gps")]), "no_gps")
        date = next(p for p in real_commands()["text slug"].params if "--include-date" in p.opts)
        self.assertEqual(tools_mod().property_name(date), "include_date")
        self.assertEqual(tools_mod().property_name(params[("host procs", "top")]), "top")


# ===========================================================================
# schemas
# ===========================================================================

def scalar_expectation(param: click.Parameter) -> dict[str, object]:
    """What the schema of one value of this parameter must say, worked out from Click alone."""
    kind = param.type
    if is_flag(param):
        return {"type": "boolean"}
    if isinstance(kind, click.Choice):
        return {"type": "string", "enum": list(kind.choices)}
    if isinstance(kind, click.types.IntParamType):
        found: dict[str, object] = {"type": "integer"}
    elif isinstance(kind, click.types.FloatParamType):
        found = {"type": "number"}
    else:
        return {"type": "string"}
    if isinstance(kind, (click.IntRange, click.FloatRange)):
        if kind.min is not None:
            found["exclusiveMinimum" if kind.min_open else "minimum"] = kind.min
        if kind.max is not None:
            found["exclusiveMaximum" if kind.max_open else "maximum"] = kind.max
    return found


BOUND_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")


def plain_default(param: click.Parameter):
    """The default a schema should carry, or None when it should carry none."""
    default = param.default
    if default is None or type(default).__name__ == "Sentinel":
        return None
    if is_flag(param) or is_many(param) or param.nargs != 1:
        return None
    if isinstance(default, (str, int, float)) and not isinstance(default, bool):
        return default
    return None


class SchemaShapeTests(unittest.TestCase):
    def test_every_schema_is_an_object_that_refuses_extra_properties(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                schema = spec.schema
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                self.assertIsInstance(schema["properties"], dict)

    def test_properties_are_the_visible_options_and_arguments_less_the_left_out_ones(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                expected = {prop_name(p) for p in offered_params(spec.name)}
                self.assertEqual(set(spec.schema["properties"]), expected)

    def test_no_help_option_and_no_hidden_option_is_offered(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                props = spec.schema["properties"]
                self.assertNotIn("help", props)
                self.assertNotIn("h", props)
        # the hidden --dry-run of `file rename` is not a property, while the visible ones are
        self.assertNotIn("dry_run", tool("file_rename").schema["properties"])
        self.assertNotIn("dry_run", tool("file_rename_apply").schema["properties"])
        self.assertIn("dry_run", tool("image_convert").schema["properties"])

    def test_required_lists_exactly_the_required_parameters_and_is_absent_when_empty(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                expected = {prop_name(p) for p in offered_params(spec.name) if p.required}
                required = spec.schema.get("required")
                if expected:
                    self.assertIsInstance(required, list)
                    self.assertEqual(set(required), expected)
                    self.assertEqual(len(required), len(set(required)))
                    self.assertLessEqual(set(required), set(spec.schema["properties"]))
                else:
                    self.assertNotIn("required", spec.schema)

    def test_every_property_has_a_type_and_a_description_of_its_own(self) -> None:
        for spec in tools_mod().tools():
            for name, prop in spec.schema["properties"].items():
                with self.subTest(tool=spec.name, prop=name):
                    self.assertIn("type", prop)
                    description = prop.get("description")
                    self.assertIsInstance(description, str)
                    self.assertTrue(description.strip(), "empty description")

    def test_an_option_description_carries_the_options_own_help_text(self) -> None:
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                if not isinstance(param, click.Option) or not param.help:
                    continue
                with self.subTest(tool=spec.name, prop=prop_name(param)):
                    description = normalize(spec.schema["properties"][prop_name(param)]["description"])
                    self.assertIn(normalize(param.help).rstrip("."), description)

    def test_a_schema_is_plain_json_data(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                text = json.dumps(spec.schema)
                self.assertEqual(json.loads(text), spec.schema)

    @unittest.skipUnless(jsonschema is not None, "jsonschema is not installed")
    def test_every_schema_is_a_valid_draft_2020_12_schema(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                jsonschema.Draft202012Validator.check_schema(spec.schema)


class SchemaTypeTests(unittest.TestCase):
    """Types, choices, bounds, defaults: derived from Click for every parameter of every tool."""

    def _item(self, param: click.Parameter, prop: dict) -> dict:
        if not is_many(param):
            return prop
        self.assertEqual(prop["type"], "array")
        items = prop["items"]
        if param.multiple and param.nargs == 2:
            self.assertEqual(items["type"], "array")
            self.assertEqual(items["items"]["type"], "string")
            return items
        return items

    def test_types_choices_and_bounds_follow_the_click_parameter(self) -> None:
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                name = prop_name(param)
                prop = spec.schema["properties"][name]
                with self.subTest(tool=spec.name, prop=name):
                    item = self._item(param, prop)
                    if param.multiple and param.nargs == 2:
                        continue
                    expected = scalar_expectation(param)
                    for key, value in expected.items():
                        self.assertEqual(item.get(key), value, f"{key} of {name}")
                    for key in BOUND_KEYS:
                        if key not in expected:
                            self.assertNotIn(key, item, f"{key} of {name} is not in the Click type")
                    if "enum" not in expected:
                        self.assertNotIn("enum", item)

    def test_arguments_and_options_that_take_several_values_are_arrays(self) -> None:
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                name = prop_name(param)
                with self.subTest(tool=spec.name, prop=name):
                    kind = spec.schema["properties"][name]["type"]
                    self.assertEqual(kind == "array", is_many(param))

    def test_required_arguments_that_take_several_values_need_at_least_one(self) -> None:
        seen = 0
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                if param.required and is_many(param):
                    seen += 1
                    with self.subTest(tool=spec.name, prop=prop_name(param)):
                        self.assertEqual(spec.schema["properties"][prop_name(param)]["minItems"], 1)
        self.assertGreaterEqual(seen, 3, "image_info, video_info, port_kill and more take required lists")

    def test_a_default_is_listed_when_click_has_a_plain_one(self) -> None:
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                if is_many(param) or param.nargs != 1:
                    continue
                name = prop_name(param)
                prop = spec.schema["properties"][name]
                with self.subTest(tool=spec.name, prop=name):
                    wanted = plain_default(param)
                    if wanted is None:
                        self.assertNotIn("default", prop)
                    else:
                        self.assertEqual(prop["default"], wanted)
                        self.assertNotIsInstance(prop["default"], bool)

    def test_pairs_are_arrays_of_two_strings(self) -> None:
        for name in ("replace", "regex"):
            with self.subTest(prop=name):
                prop = tool("file_rename").schema["properties"][name]
                self.assertEqual(prop["type"], "array")
                pair = prop["items"]
                self.assertEqual(pair["type"], "array")
                self.assertEqual(pair["items"]["type"], "string")
                sized = pair.get("minItems") == 2 and pair.get("maxItems") == 2
                prefixed = len(pair.get("prefixItems", [])) == 2
                self.assertTrue(sized or prefixed, f"a pair must be exactly two strings: {pair}")


class SchemaExampleTests(unittest.TestCase):
    """Concrete values, so a mistake in the general rule above cannot hide a wrong schema."""

    def props(self, name: str) -> dict:
        return tool(name).schema["properties"]

    def test_image_compress(self) -> None:
        props = self.props("image_compress")

        self.assertEqual(props["quality"]["type"], "integer")
        self.assertEqual(props["quality"]["minimum"], 1)
        self.assertEqual(props["quality"]["maximum"], 100)
        self.assertEqual(props["to"]["type"], "string")
        self.assertEqual(props["to"]["enum"], ["jpg", "png", "webp", "avif", "heic"])
        self.assertEqual(props["max_side"]["type"], "integer")
        self.assertEqual(props["max_side"]["minimum"], 1)
        self.assertNotIn("maximum", props["max_side"])
        self.assertEqual(props["sources"]["type"], "array")
        self.assertEqual(props["sources"]["items"]["type"], "string")
        self.assertEqual(props["dir"]["type"], "string")
        self.assertEqual(props["strip"]["type"], "boolean")
        self.assertEqual(props["dry_run"]["type"], "boolean")
        self.assertNotIn("overwrite", props)
        self.assertNotIn("required", tool("image_compress").schema)

    def test_image_resize_percent_is_open_at_zero_and_closed_at_1000(self) -> None:
        percent = self.props("image_resize")["percent"]

        self.assertEqual(percent["type"], "number")
        self.assertEqual(percent["exclusiveMinimum"], 0)
        self.assertEqual(percent["maximum"], 1000)
        self.assertNotIn("minimum", percent)
        self.assertNotIn("exclusiveMaximum", percent)

    def test_port_kill_needs_at_least_one_port_in_range(self) -> None:
        schema = tool("port_kill").schema
        ports = schema["properties"]["ports"]

        self.assertEqual(schema["required"], ["ports"])
        self.assertEqual(ports["type"], "array")
        self.assertEqual(ports["minItems"], 1)
        self.assertEqual(ports["items"]["type"], "integer")
        self.assertEqual(ports["items"]["minimum"], 1)
        self.assertEqual(ports["items"]["maximum"], 65535)
        self.assertEqual(self.props("port_kill")["restart_hns"]["type"], "boolean")

    def test_file_rename_pairs_and_the_apply_split(self) -> None:
        plan = self.props("file_rename")
        apply = self.props("file_rename_apply")

        self.assertEqual(plan["replace"]["items"]["type"], "array")
        self.assertEqual(plan["replace"]["items"]["items"]["type"], "string")
        self.assertEqual(plan["case"]["enum"], ["lower", "upper", "title", "slug", "snake"])
        self.assertEqual(plan["start"]["default"], 1)
        self.assertNotIn("apply", plan)
        self.assertNotIn("apply", apply)
        self.assertEqual(set(plan), set(apply))

    def test_random_int_bounds_are_integers(self) -> None:
        props = self.props("random_int")

        self.assertEqual(props["bounds"]["type"], "array")
        self.assertEqual(props["bounds"]["items"]["type"], "integer")
        self.assertNotIn("minimum", props["bounds"]["items"])
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["count"]["default"], 1)
        self.assertEqual(props["unique"]["type"], "boolean")

    def test_hash_algorithm_defaults_to_sha256(self) -> None:
        algorithm = self.props("hash")["algorithm"]

        self.assertEqual(algorithm["default"], "sha256")
        self.assertEqual(algorithm["type"], "string")
        self.assertIn("sha256", algorithm["enum"])
        self.assertIn("sha3-256", algorithm["enum"])
        self.assertIn("blake2b", algorithm["enum"])
        self.assertTrue(algorithm["description"].strip())
        self.assertEqual(self.props("hash")["files"]["type"], "array")

    def test_host_procs_top_defaults_to_15_and_cannot_kill(self) -> None:
        props = self.props("host_procs")

        self.assertEqual(props["top"]["default"], 15)
        self.assertEqual(props["top"]["minimum"], 1)
        self.assertEqual(props["pattern"]["type"], "string")
        for left in ("kill", "force"):
            self.assertNotIn(left, props)

    def test_image_info_requires_files(self) -> None:
        schema = tool("image_info").schema

        self.assertEqual(schema["required"], ["files"])
        self.assertEqual(schema["properties"]["files"]["minItems"], 1)
        self.assertEqual(schema["properties"]["json"]["type"], "boolean")
        self.assertNotIn("default", schema["properties"]["json"])
        self.assertEqual(set(schema["properties"]), {"files", "json", "no_gps"})

    def test_text_diff_takes_two_required_texts_and_a_context_size(self) -> None:
        schema = tool("text_diff").schema

        self.assertEqual(set(schema["required"]), {"old", "new"})
        self.assertEqual(schema["properties"]["unified"]["minimum"], 0)
        self.assertEqual(schema["properties"]["unified"]["default"], 3)
        self.assertNotIn("color", schema["properties"])

    def test_video_gif_numbers(self) -> None:
        props = self.props("video_gif")

        self.assertEqual(props["width"]["minimum"], 2)
        self.assertEqual(props["width"]["default"], 480)
        self.assertEqual(props["fps"]["type"], "number")
        self.assertEqual(props["fps"]["exclusiveMinimum"], 0)
        self.assertEqual(props["fps"]["default"], 15)

    def test_image_convert_backend_choice_and_default(self) -> None:
        backend = self.props("image_convert")["backend"]

        self.assertEqual(backend["enum"], ["auto", "pillow", "magick"])
        self.assertEqual(backend["default"], "auto")

    def test_audio_tags_edit_offers_the_edit_options_and_audio_tags_does_not(self) -> None:
        edit = self.props("audio_tags_edit")
        read = self.props("audio_tags")

        for name in ("set", "unset", "clear", "cover", "no_cover", "lyrics", "export_cover"):
            self.assertIn(name, edit)
            self.assertNotIn(name, read)
        self.assertEqual(edit["set"]["type"], "array")
        self.assertNotIn("json", edit)
        self.assertIn("json", read)
        self.assertEqual(set(read), {"files", "json"})

    def test_read_only_tools_do_not_offer_a_way_to_write_or_copy(self) -> None:
        for name in ("text_base64", "env_example"):
            for left in ("output", "overwrite", "copy"):
                self.assertNotIn(left, self.props(name))
        for name in ("text_slug", "text_url", "uuid", "password"):
            self.assertNotIn("copy", self.props(name))
        self.assertNotIn("write", self.props("data_json"))
        for left in ("live", "interval"):
            self.assertNotIn(left, self.props("host_status"))

    def test_the_timestamp_unit_is_a_choice(self) -> None:
        self.assertEqual(self.props("timestamp")["unit"]["enum"], ["s", "ms"])


@unittest.skipUnless(jsonschema is not None, "jsonschema is not installed")
class SchemaValidatorTests(unittest.TestCase):
    def valid(self, name: str, instance: object) -> bool:
        return jsonschema.Draft202012Validator(tool(name).schema).is_valid(instance)

    def test_image_compress_accepts_good_input_and_refuses_bad_input(self) -> None:
        self.assertTrue(self.valid("image_compress", {"sources": ["/a.png"], "quality": 50, "to": "webp"}))
        self.assertTrue(self.valid("image_compress", {"dir": "/pics", "max_side": 2000}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "quality": 0}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "quality": 101}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "quality": 50.5}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "to": "bmp"}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "overwrite": True}))
        self.assertFalse(self.valid("image_compress", {"sources": "/a.png"}))
        self.assertFalse(self.valid("image_compress", {"sources": ["/a.png"], "max_side": 0}))

    def test_image_resize_percent_edges(self) -> None:
        self.assertFalse(self.valid("image_resize", {"dir": "/p", "percent": 0}))
        self.assertTrue(self.valid("image_resize", {"dir": "/p", "percent": 0.5}))
        self.assertTrue(self.valid("image_resize", {"dir": "/p", "percent": 1000}))
        self.assertFalse(self.valid("image_resize", {"dir": "/p", "percent": 1000.5}))

    def test_port_kill_needs_ports_inside_the_port_range(self) -> None:
        self.assertTrue(self.valid("port_kill", {"ports": [80, 65535]}))
        self.assertFalse(self.valid("port_kill", {}))
        self.assertFalse(self.valid("port_kill", {"ports": []}))
        self.assertFalse(self.valid("port_kill", {"ports": [0]}))
        self.assertFalse(self.valid("port_kill", {"ports": [65536]}))
        self.assertFalse(self.valid("port_kill", {"ports": ["80"]}))

    def test_hash_algorithm_must_be_one_of_the_choices(self) -> None:
        self.assertTrue(self.valid("hash", {"text": "x", "algorithm": "blake2b"}))
        self.assertFalse(self.valid("hash", {"text": "x", "algorithm": "md4"}))

    def test_file_rename_replace_must_be_pairs(self) -> None:
        self.assertTrue(self.valid("file_rename", {"dir": "/d", "replace": [["a", "b"], ["c", "d"]]}))
        self.assertFalse(self.valid("file_rename", {"dir": "/d", "replace": [["a"]]}))
        self.assertFalse(self.valid("file_rename", {"dir": "/d", "replace": ["a", "b"]}))
        self.assertFalse(self.valid("file_rename", {"dir": "/d", "replace": [["a", "b", "c"]]}))

    def test_image_info_needs_files(self) -> None:
        self.assertTrue(self.valid("image_info", {"files": ["/a.png"]}))
        self.assertFalse(self.valid("image_info", {}))
        self.assertFalse(self.valid("image_info", {"files": []}))

    def test_host_procs_has_no_kill(self) -> None:
        self.assertTrue(self.valid("host_procs", {"top": 5}))
        self.assertFalse(self.valid("host_procs", {"kill": True}))
        self.assertFalse(self.valid("host_procs", {"top": 0}))


# ===========================================================================
# descriptions
# ===========================================================================

class DescriptionTests(unittest.TestCase):
    def _remainder(self, spec) -> str:
        """The description without the command's own help text."""
        text = normalize(spec.description)
        return text.replace(normalize(spec.command.help or ""), "").strip()

    def test_every_description_is_non_empty_and_under_1024_characters(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                self.assertTrue(spec.description.strip())
                self.assertLess(len(spec.description), 1024)

    def test_every_description_holds_the_short_help_of_its_command(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                short = normalize(spec.command.get_short_help_str(limit=300))
                self.assertIn(short, normalize(spec.description))

    def test_every_description_holds_the_whole_help_text_of_its_command(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                self.assertIn(normalize(spec.command.help or ""), normalize(spec.description))

    def test_the_options_listing_of_the_help_page_is_not_copied(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                self.assertNotIn("Options:", spec.description)
                self.assertNotIn("Usage:", spec.description)

    def test_tools_with_a_path_parameter_say_to_use_absolute_paths_once(self) -> None:
        sentence = "Use absolute paths."
        checked = 0
        for spec in tools_mod().tools():
            if spec.name in PATH_UNDECIDED:
                continue
            checked += 1
            with self.subTest(tool=spec.name):
                if has_path_param(spec.name):
                    self.assertEqual(spec.description.count(sentence), 1)
                else:
                    self.assertNotIn(sentence, spec.description)
        self.assertGreater(checked, 30)

    def test_the_tools_that_need_absolute_paths_include_the_obvious_ones(self) -> None:
        for name in ("image_compress", "image_info", "video_trim", "audio_tags_edit", "file_rename",
                     "file_rename_apply", "hash", "text_base64", "jwt"):
            with self.subTest(tool=name):
                self.assertTrue(has_path_param(name), "the test's own idea of a path parameter changed")
                self.assertIn("Use absolute paths.", tool(name).description)
        for name in ("uuid", "port_kill", "host_status", "random_int", "text_slug", "image_formats"):
            with self.subTest(tool=name):
                self.assertNotIn("Use absolute paths.", tool(name).description)

    def test_video_and_audio_encoders_give_a_measured_time_and_point_to_a_shell(self) -> None:
        time_words = re.compile(r"\d+(\.\d+)?\s*(seconds?|secs?|minutes?|mins?|s|min)\b")
        for name in ENCODERS:
            with self.subTest(tool=name):
                extra = self._remainder(tool(name))
                self.assertIn("shell", extra)
                self.assertRegex(extra, time_words)

    def test_other_tools_do_not_talk_about_running_in_a_shell(self) -> None:
        for spec in tools_mod().tools():
            if spec.name in ENCODERS:
                continue
            with self.subTest(tool=spec.name):
                self.assertNotIn("shell", self._remainder(spec).lower())


# ===========================================================================
# argv
# ===========================================================================

def sample_value(param: click.Parameter):
    """(value for the arguments mapping, the words argv must produce for it)."""
    flag = long_flag(param) if isinstance(param, click.Option) else None
    if flag is not None and is_flag(param):
        return True, [flag]
    if flag is not None and param.multiple and param.nargs == 2:
        return [["a", "b"], ["c d", "e"]], [flag, "a", "b", flag, "c d", "e"]
    if param.multiple or param.nargs < 0:
        if isinstance(param.type, click.types.IntParamType):
            values: list[object] = [3, 4]
        else:
            values = ["p q", "r=s"]
        words = [str(v) for v in values]
        return values, ([f"{flag}={w}" for w in words] if flag else words)
    kind = param.type
    if isinstance(kind, click.Choice):
        value: object = kind.choices[0]
    elif isinstance(kind, click.types.IntParamType):
        value = 7
    elif isinstance(kind, click.types.FloatParamType):
        value = 2.5
    else:
        value = "v a"
    return value, [f"{flag}={value}"] if flag else [str(value)]


def minimal_arguments(name: str) -> dict[str, object]:
    arguments: dict[str, object] = {}
    params = offered_params(name)
    names = {prop_name(p) for p in params}
    if {"sources", "dir"} <= names:
        arguments["dir"] = "/work"
    for param in params:
        if param.required:
            arguments[prop_name(param)] = sample_value(param)[0]
    return arguments


class ArgvEveryParameterTests(unittest.TestCase):
    def test_every_parameter_of_every_tool_becomes_the_words_click_expects(self) -> None:
        for spec in tools_mod().tools():
            for param in offered_params(spec.name):
                name = prop_name(param)
                with self.subTest(tool=spec.name, prop=name):
                    arguments = minimal_arguments(spec.name)
                    value, words = sample_value(param)
                    arguments[name] = value

                    result = tools_mod().argv(spec, arguments)

                    head, tail = split_at_dashes(result)
                    if isinstance(param, click.Argument):
                        self.assertIsNotNone(tail, result)
                        self.assertTrue(has_run(tail, words), f"{words} not in {tail}")
                    else:
                        self.assertTrue(has_run(head, words), f"{words} not in {head}")

    def test_every_tool_starts_with_its_path_words_then_its_fixed_flags(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                result = tools_mod().argv(spec, minimal_arguments(spec.name))

                prefix = [*spec.exposed.path.split(), *spec.exposed.fixed]
                self.assertEqual(result[:len(prefix)], prefix)

    def test_nothing_but_arguments_follows_the_double_dash(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                arguments = minimal_arguments(spec.name)
                for param in offered_params(spec.name):
                    if isinstance(param, click.Option) and is_flag(param):
                        arguments[prop_name(param)] = True
                head, tail = split_at_dashes(tools_mod().argv(spec, arguments))
                option_words = {long_flag(p) for p in offered_params(spec.name) if isinstance(p, click.Option)}
                for word in tail or []:
                    self.assertNotIn(word, option_words)
                for word in head[len(spec.exposed.path.split()):]:
                    if word in spec.exposed.fixed:
                        continue
                    self.assertTrue(word.startswith("--"), f"{word!r} before -- is not an option")

    def test_arguments_come_in_the_commands_own_order_whatever_the_mapping_order(self) -> None:
        self.assertEqual(argv("text_diff", {"new": "n", "old": "o"}), ["text", "diff", "--", "o", "n"])
        self.assertEqual(argv("env_compare", {"b": "y", "a": "x"}), ["env", "compare", "--", "x", "y"])


class ArgvExampleTests(unittest.TestCase):
    def test_a_tool_without_input_is_just_its_path_words(self) -> None:
        self.assertEqual(argv("image_formats", {}), ["image", "formats"])
        self.assertEqual(argv("uuid", {}), ["uuid"])
        self.assertEqual(argv("ffmpeg_status", {}), ["ffmpeg", "status"])

    def test_arguments_come_after_a_double_dash(self) -> None:
        self.assertEqual(argv("image_info", {"files": ["/x/a.png"]}), ["image", "info", "--", "/x/a.png"])
        self.assertEqual(
            argv("image_info", {"files": ["/x/a.png", "/x/b.png"]}),
            ["image", "info", "--", "/x/a.png", "/x/b.png"],
        )

    def test_true_flags_are_options_and_false_flags_are_left_out(self) -> None:
        self.assertEqual(
            argv("image_info", {"files": ["/a.png"], "json": True, "no_gps": False}),
            ["image", "info", "--json", "--", "/a.png"],
        )
        self.assertEqual(
            argv("image_info", {"files": ["/a.png"], "json": False}), ["image", "info", "--", "/a.png"]
        )

    def test_null_counts_as_not_given(self) -> None:
        self.assertEqual(
            argv("image_compress", {"sources": ["/a.png"], "quality": None, "to": None, "strip": None}),
            ["image", "compress", "--", "/a.png"],
        )
        self.assertEqual(argv("uuid", {"count": None}), ["uuid"])

    def test_options_with_a_value_use_the_equals_form_with_the_longest_name(self) -> None:
        result = argv(
            "image_compress",
            {"sources": ["/a b/x.png", "/c.png"], "to": "jpg", "quality": 80, "strip": True, "dry_run": True},
        )

        head, tail = split_at_dashes(result)
        self.assertEqual(head[:2], ["image", "compress"])
        self.assertEqual(sorted(head[2:]), sorted(["--to=jpg", "--quality=80", "--strip", "--dry-run"]))
        self.assertEqual(tail, ["/a b/x.png", "/c.png"])

    def test_the_value_may_contain_equals_signs_and_spaces(self) -> None:
        self.assertEqual(
            argv("image_compress", {"sources": ["/a.png"], "pattern": "a=b c"}),
            ["image", "compress", "--pattern=a=b c", "--", "/a.png"],
        )

    def test_the_dir_property_is_the_dir_option(self) -> None:
        self.assertEqual(
            argv("image_compress", {"dir": "/pics", "recursive": True}),
            any_option_order(["image", "compress", "--dir=/pics", "--recursive"]),
        )

    def test_fixed_flags_come_after_the_path_words_and_before_every_option(self) -> None:
        result = argv("file_rename_apply", {"sources": ["/a"], "prefix": "p_", "suffix": "_s"})

        head, tail = split_at_dashes(result)
        self.assertEqual(head[:3], ["file", "rename", "--apply"])
        self.assertEqual(sorted(head[3:]), ["--prefix=p_", "--suffix=_s"])
        self.assertEqual(tail, ["/a"])
        self.assertEqual(
            argv("file_rename_apply", {"sources": ["/a"]}), ["file", "rename", "--apply", "--", "/a"]
        )

    def test_the_plan_tool_never_passes_apply(self) -> None:
        self.assertEqual(
            argv("file_rename", {"sources": ["/a"], "prefix": "p_"}),
            ["file", "rename", "--prefix=p_", "--", "/a"],
        )

    def test_pairs_are_two_separate_words_after_each_repeated_option(self) -> None:
        self.assertEqual(
            argv("file_rename", {"sources": ["/a"], "replace": [["a", "b"], ["c d", "e"]]}),
            ["file", "rename", "--replace", "a", "b", "--replace", "c d", "e", "--", "/a"],
        )
        result = argv("file_rename", {"dir": "/d", "regex": [["^(\\d+)", "n\\1"]]})
        self.assertTrue(has_run(result, ["--regex", "^(\\d+)", "n\\1"]), result)

    def test_an_option_that_repeats_gives_one_word_for_each_value_in_order(self) -> None:
        result = argv(
            "audio_tags_edit",
            {"files": ["/f.mp3"], "set": ["title=A", "artist=B"], "unset": ["lyrics"], "clear": False},
        )

        head, tail = split_at_dashes(result)
        self.assertEqual(sorted(head[2:]), sorted(["--set=title=A", "--set=artist=B", "--unset=lyrics"]))
        self.assertLess(head.index("--set=title=A"), head.index("--set=artist=B"))
        self.assertEqual(tail, ["/f.mp3"])

    def test_a_negative_number_is_an_argument_after_the_double_dash(self) -> None:
        self.assertEqual(
            argv("random_int", {"bounds": [-5, 10], "count": 3}),
            ["random", "int", "--count=3", "--", "-5", "10"],
        )
        self.assertEqual(argv("random_int", {"bounds": [-10, -5]}), ["random", "int", "--", "-10", "-5"])
        self.assertEqual(argv("timestamp", {"value": -1}), ["timestamp", "--", "-1"])

    def test_values_that_look_like_options_cannot_be_read_as_options(self) -> None:
        self.assertEqual(
            argv("text_base64", {"text": "--overwrite"}), ["text", "base64", "--", "--overwrite"]
        )
        self.assertEqual(argv("text_slug", {"text": "-y"}), ["text", "slug", "--", "-y"])
        self.assertEqual(argv("text_slug", {"text": "-"}), ["text", "slug", "--", "-"])
        self.assertEqual(
            argv("hash", {"files": ["-", "--json", "-y"]}), ["hash", "--", "-", "--json", "-y"]
        )
        self.assertEqual(argv("hash", {"text": "--overwrite"}), ["hash", "--text=--overwrite"])
        self.assertEqual(argv("hash", {"text": "-y"}), ["hash", "--text=-y"])
        self.assertEqual(argv("hash", {"text": "-"}), ["hash", "--text=-"])
        self.assertEqual(
            argv("random_pick", {"items": ["--count=9", "-n", "--"]}),
            ["random", "pick", "--", "--count=9", "-n", "--"],
        )

    def test_empty_strings_are_kept(self) -> None:
        self.assertEqual(argv("hash", {"text": ""}), ["hash", "--text="])
        self.assertEqual(argv("text_slug", {"text": ""}), ["text", "slug", "--", ""])
        self.assertEqual(
            argv("random_pick", {"items": ["", "a"]}), ["random", "pick", "--", "", "a"]
        )

    def test_unicode_and_spaces_pass_through_untouched(self) -> None:
        path = "/tmp/my pics/é日本 \U0001F389.png"
        self.assertEqual(
            argv("image_compress", {"sources": [path], "pattern": "*é*"}),
            ["image", "compress", "--pattern=*é*", "--", path],
        )
        self.assertEqual(
            argv("text_url", {"text": "Привет мир"}),
            ["text", "url", "--", "Привет мир"],
        )

    def test_values_outside_a_range_or_choice_are_left_to_click(self) -> None:
        self.assertEqual(
            argv("image_compress", {"sources": ["/a.png"], "quality": 500, "to": "bmp"}),
            any_option_order(["image", "compress", "--quality=500", "--to=bmp", "--", "/a.png"]),
        )

    def test_the_mapping_is_not_changed_and_any_mapping_will_do(self) -> None:
        arguments = {"sources": ["/a.png"], "quality": 80.0, "dry_run": True}
        before = copy.deepcopy(arguments)

        tools_mod().argv(tool("image_compress"), arguments)
        self.assertEqual(arguments, before)
        self.assertEqual(
            argv("image_compress", types.MappingProxyType(arguments)),
            argv("image_compress", arguments),
        )


def any_option_order(words: list[str]) -> list[str]:
    """Expected words in which the options may come in any order among themselves."""
    return OptionsEqual(words)


class OptionsEqual(list):
    """A list that equals another list holding the same path words, the same options in any
    order and the same words after the double dash."""

    @staticmethod
    def _key(words):
        head, tail = split_at_dashes(list(words))
        return (head[:2], sorted(head[2:]), tail)

    def __eq__(self, other) -> bool:  # type: ignore[override]
        return isinstance(other, list) and self._key(self) == self._key(other)

    def __ne__(self, other) -> bool:  # type: ignore[override]
        return not self.__eq__(other)

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"{list.__repr__(self)} (options in any order)"


class ArgvLeniencyTests(unittest.TestCase):
    def test_a_whole_float_is_an_integer(self) -> None:
        self.assertEqual(
            argv("image_compress", {"sources": ["/a.png"], "quality": 80.0, "max_side": 1024.0}),
            any_option_order(["image", "compress", "--quality=80", "--max-side=1024", "--", "/a.png"]),
        )
        self.assertEqual(argv("uuid", {"count": 3.0}), ["uuid", "--count=3"])
        self.assertEqual(argv("port_show", {"ports": [80.0, 443]}), ["port", "show", "--", "80", "443"])
        self.assertEqual(argv("random_int", {"bounds": [-5.0, 10.0]}), ["random", "int", "--", "-5", "10"])

    def test_numbers_for_a_number_option(self) -> None:
        self.assertEqual(
            argv("image_resize", {"dir": "/p", "percent": 50.5}),
            any_option_order(["image", "resize", "--dir=/p", "--percent=50.5"]),
        )
        self.assertEqual(
            argv("image_resize", {"dir": "/p", "percent": 50}),
            any_option_order(["image", "resize", "--dir=/p", "--percent=50"]),
        )

    def test_a_number_is_accepted_where_a_string_is_wanted(self) -> None:
        self.assertEqual(argv("timestamp", {"value": 5}), ["timestamp", "--", "5"])
        self.assertEqual(argv("timestamp", {"value": 1700000000.5}), ["timestamp", "--", "1700000000.5"])
        self.assertEqual(argv("timestamp", {"value": "0", "tz": 5}), any_option_order(["timestamp", "--tz=5", "--", "0"]))
        self.assertEqual(argv("text_slug", {"text": 2024}), ["text", "slug", "--", "2024"])

    def test_one_string_is_a_list_of_one(self) -> None:
        self.assertEqual(argv("image_info", {"files": "/a.png"}), ["image", "info", "--", "/a.png"])
        self.assertEqual(
            argv("audio_tags_edit", {"files": ["/f.mp3"], "set": "title=A"}),
            any_option_order(["audio", "tags", "--set=title=A", "--", "/f.mp3"]),
        )
        self.assertEqual(
            argv("image_compress", {"sources": "/a.png"}), ["image", "compress", "--", "/a.png"]
        )

    def test_true_and_false_are_never_numbers(self) -> None:
        for name, arguments, prop in (
            ("image_compress", {"sources": ["/a.png"], "quality": True}, "quality"),
            ("image_compress", {"sources": ["/a.png"], "quality": False}, "quality"),
            ("image_resize", {"dir": "/p", "percent": True}, "percent"),
            ("port_show", {"ports": [True]}, "ports"),
        ):
            with self.subTest(tool=name, prop=prop):
                with self.assertRaises(tools_mod().ToolInputError) as raised:
                    argv(name, arguments)
                self.assertIn(prop, str(raised.exception))

    def test_true_and_false_are_not_strings_either(self) -> None:
        with self.assertRaises(tools_mod().ToolInputError) as raised:
            argv("image_compress", {"sources": ["/a.png"], "pattern": True})
        self.assertIn("pattern", str(raised.exception))


class ArgvErrorTests(unittest.TestCase):
    def message(self, name: str, arguments) -> str:
        with self.assertRaises(tools_mod().ToolInputError) as raised:
            argv(name, arguments)
        return str(raised.exception)

    def test_an_unknown_property_is_named_with_the_ones_the_tool_takes(self) -> None:
        self.assertEqual(
            self.message("image_info", {"files": ["/a.png"], "x": 1}),
            "unknown argument 'x'; image_info takes: files, json, no_gps",
        )

    def test_every_tool_lists_exactly_its_own_properties_in_that_message(self) -> None:
        for spec in tools_mod().tools():
            with self.subTest(tool=spec.name):
                text = self.message(spec.name, {**minimal_arguments(spec.name), "zzz": 1})
                self.assertTrue(text.startswith(f"unknown argument 'zzz'; {spec.name} takes"), text)
                if spec.schema["properties"]:
                    listed = text.split(f"{spec.name} takes: ", 1)[1].split(", ")
                    self.assertEqual(set(listed), set(spec.schema["properties"]))
                    self.assertEqual(len(listed), len(set(listed)))

    def test_a_left_out_property_is_unknown(self) -> None:
        cases = (
            ("image_compress", {"sources": ["/a"], "overwrite": True}, "overwrite"),
            ("host_procs", {"kill": True}, "kill"),
            ("host_procs", {"force": True}, "force"),
            ("file_rename", {"sources": ["/a"], "apply": True}, "apply"),
            ("file_rename_apply", {"sources": ["/a"], "apply": True}, "apply"),
            ("audio_tags", {"files": ["/a.mp3"], "set": ["title=x"]}, "set"),
            ("audio_tags_edit", {"files": ["/a.mp3"], "json": True}, "json"),
            ("text_base64", {"text": "x", "output": "/o.txt"}, "output"),
            ("data_json", {"files": ["/a.json"], "write": True}, "write"),
            ("host_status", {"live": True}, "live"),
            ("uuid", {"copy": True}, "copy"),
        )
        for name, arguments, left in cases:
            with self.subTest(tool=name, left_out=left):
                text = self.message(name, arguments)
                self.assertTrue(text.startswith(f"unknown argument '{left}'; {name} takes: "), text)
                listed = text.split("takes: ", 1)[1].split(", ")
                self.assertNotIn(left, listed)

    def test_a_missing_required_property_is_named(self) -> None:
        self.assertEqual(self.message("image_info", {}), "files is required")
        self.assertEqual(self.message("image_info", {"json": True}), "files is required")
        self.assertEqual(self.message("image_info", {"files": None}), "files is required")
        self.assertEqual(self.message("port_kill", {}), "ports is required")
        self.assertEqual(self.message("text_diff", {"old": "a"}), "new is required")

    def test_a_wrong_type_names_the_property_and_the_type(self) -> None:
        self.assertEqual(
            self.message("image_compress", {"sources": ["/a"], "quality": "high"}),
            "quality must be an integer",
        )
        self.assertEqual(
            self.message("image_compress", {"sources": ["/a"], "quality": 80.5}),
            "quality must be an integer",
        )
        self.assertEqual(
            self.message("image_compress", {"sources": ["/a"], "quality": "80"}),
            "quality must be an integer",
        )
        self.assertEqual(
            self.message("image_compress", {"sources": ["/a"], "quality": True}),
            "quality must be an integer",
        )

    def test_other_wrong_types_name_the_property(self) -> None:
        cases = (
            ("image_resize", {"dir": "/p", "percent": "half"}, "percent"),
            ("image_info", {"files": ["/a.png"], "json": "yes"}, "json"),
            ("image_info", {"files": ["/a.png"], "json": 1}, "json"),
            ("image_info", {"files": [["/a.png"]]}, "files"),
            ("image_info", {"files": {"a": 1}}, "files"),
            ("image_compress", {"sources": ["/a"], "pattern": ["a", "b"]}, "pattern"),
            ("port_kill", {"ports": ["80"]}, "ports"),
            ("port_kill", {"ports": [80.5]}, "ports"),
            ("file_rename", {"sources": ["/a"], "replace": [["a"]]}, "replace"),
            ("file_rename", {"sources": ["/a"], "replace": [["a", "b", "c"]]}, "replace"),
            ("file_rename", {"sources": ["/a"], "replace": ["a", "b"]}, "replace"),
            ("file_rename", {"sources": ["/a"], "replace": "ab"}, "replace"),
        )
        for name, arguments, prop in cases:
            with self.subTest(tool=name, prop=prop, value=arguments):
                self.assertIn(prop, self.message(name, arguments))

    def test_a_batch_tool_with_neither_sources_nor_dir_is_refused(self) -> None:
        wanted = "give sources, or dir to work on a whole folder"
        batch = [
            spec.name for spec in tools_mod().tools()
            if {"sources", "dir"} <= set(spec.schema["properties"])
        ]
        self.assertEqual(
            set(batch),
            {"image_compress", "image_convert", "image_resize", "video_compress", "video_convert",
             "video_trim", "video_gif", "audio_convert", "audio_trim", "file_rename", "file_rename_apply"},
        )
        for name in batch:
            for arguments in ({}, {"dir": None}, {"sources": None}, {"sources": []}, {"recursive": True}):
                with self.subTest(tool=name, arguments=arguments):
                    self.assertEqual(self.message(name, arguments), wanted)

    def test_a_batch_tool_with_sources_or_dir_is_accepted(self) -> None:
        for name in ("image_compress", "video_trim", "audio_convert", "file_rename"):
            with self.subTest(tool=name):
                self.assertIn("--dir=/w", argv(name, {"dir": "/w"}))
                self.assertEqual(split_at_dashes(argv(name, {"sources": ["/a"]}))[1], ["/a"])
                both = argv(name, {"sources": ["/a"], "dir": "/w"})
                self.assertIn("--dir=/w", both)
                self.assertEqual(split_at_dashes(both)[1], ["/a"])

    def test_a_tool_that_does_not_take_both_has_no_such_rule(self) -> None:
        self.assertEqual(argv("hash", {}), ["hash"])
        self.assertEqual(argv("uuid", {}), ["uuid"])
        self.assertEqual(argv("host_procs", {}), ["host", "procs"])


# ===========================================================================
# run
# ===========================================================================

class RunTests(QuietCase):
    def run_words(self, *words: str):
        return tools_mod().run(list(words))

    def test_a_command_gives_its_output_exit_code_and_no_crash(self) -> None:
        out = self.run_words("uuid", "--count=3")

        self.assertEqual(out.exit_code, 0)
        self.assertEqual(out.crash, "")
        self.assertEqual(out.stderr, "")
        lines = out.stdout.split("\n")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[3], "")
        for line in lines[:3]:
            self.assertRegex(line, r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")

    def test_any_sequence_of_words_will_do(self) -> None:
        out = tools_mod().run(("uuid", "--no-hyphens"))

        self.assertEqual(out.exit_code, 0)
        self.assertRegex(out.stdout, r"^[0-9a-f]{32}\n$")

    def test_stdout_and_stderr_are_kept_apart(self) -> None:
        with patched("uuid", says(out="to out\n", err="to err\n")):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, "to out\n")
        self.assertEqual(out.stderr, "to err\n")
        self.assertEqual(out.exit_code, 0)

    def test_click_echo_to_either_stream_is_captured(self) -> None:
        def callback(**_kwargs):
            click.echo("one")
            click.echo("two", err=True)

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual((out.stdout, out.stderr), ("one\n", "two\n"))

    def test_print_from_the_command_is_captured(self) -> None:
        def callback(**_kwargs):
            print("printed")
            print("warned", file=sys.stderr)

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual((out.stdout, out.stderr), ("printed\n", "warned\n"))

    def test_a_usage_error_is_exit_code_2_with_the_message_on_stderr(self) -> None:
        out = self.run_words("uuid", "--count=x")

        self.assertEqual(out.exit_code, 2)
        self.assertEqual(out.stdout, "")
        self.assertEqual(out.crash, "")
        self.assertIn("Usage: jzr uuid", out.stderr)
        self.assertIn("'x' is not a valid integer", out.stderr)

    def test_the_program_name_is_jzr(self) -> None:
        out = self.run_words("uuid", "--help")

        self.assertEqual(out.exit_code, 0)
        self.assertTrue(out.stdout.startswith("Usage: jzr uuid"), out.stdout[:60])

    def test_an_unknown_command_is_a_usage_error(self) -> None:
        out = self.run_words("nosuchcommand_xyz")

        self.assertEqual(out.exit_code, 2)
        self.assertEqual(out.crash, "")

    def test_exit_code_1_is_reported_as_it_is_with_the_output(self) -> None:
        with patched("uuid", says(out="partial\n", err="one failed\n", code=1)):
            out = self.run_words("uuid")

        self.assertEqual((out.stdout, out.stderr, out.exit_code, out.crash), ("partial\n", "one failed\n", 1, ""))

    def test_a_text_diff_that_differs_exits_1_and_one_that_does_not_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
            (folder / "b.txt").write_text("one\n2\n", encoding="utf-8")
            (folder / "c.txt").write_text("one\ntwo\n", encoding="utf-8")

            differ = self.run_words("text", "diff", str(folder / "a.txt"), str(folder / "b.txt"))
            same = self.run_words("text", "diff", str(folder / "a.txt"), str(folder / "c.txt"))

        self.assertEqual(differ.exit_code, 1)
        self.assertIn("-two", differ.stdout)
        self.assertIn("+2", differ.stdout)
        self.assertEqual(same.exit_code, 0)
        self.assertEqual(same.stdout, "")

    def test_a_system_exit_with_a_number_none_or_a_string(self) -> None:
        for raised, expected in ((SystemExit(3), 3), (SystemExit(0), 0), (SystemExit(None), 0),
                                 (SystemExit("fatal: x"), 1)):
            with self.subTest(raised=repr(raised)):
                with patched("uuid", raises(raised)):
                    out = self.run_words("uuid")
                self.assertEqual(out.exit_code, expected)
                self.assertEqual(out.crash, "")

    def test_a_crash_is_caught_and_described_on_one_line(self) -> None:
        with patched("uuid", raises(ValueError("first line\nsecond line"))):
            out = self.run_words("uuid")

        self.assertIsNone(out.exit_code)
        self.assertEqual(out.crash, "ValueError: first line")

    def test_crash_names_the_exception_type_of_the_error(self) -> None:
        with patched("uuid", raises(FileNotFoundError("no such thing"))):
            out = self.run_words("uuid")

        self.assertIsNone(out.exit_code)
        self.assertEqual(out.crash, "FileNotFoundError: no such thing")

    def test_what_a_command_wrote_before_crashing_is_kept(self) -> None:
        def callback(**_kwargs):
            print("before")
            raise RuntimeError("late")

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, "before\n")
        self.assertEqual(out.crash, "RuntimeError: late")

    def test_output_has_lf_line_ends_even_on_windows(self) -> None:
        def callback(**_kwargs):
            print("a")
            print("b")
            click.echo("c")
            sys.stdout.write("d\n")
            print("e", file=sys.stderr)

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, "a\nb\nc\nd\n")
        self.assertEqual(out.stderr, "e\n")
        self.assertNotIn("\r", out.stdout + out.stderr)

    def test_text_outside_the_console_code_page_survives(self) -> None:
        text = "Привет 日本語 \U0001F389 é"

        def callback(**_kwargs):
            print(text)
            print(text, file=sys.stderr)

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, text + "\n")
        self.assertEqual(out.stderr, text + "\n")

    def test_bytes_written_to_the_buffer_are_decoded_as_utf_8(self) -> None:
        def callback(**_kwargs):
            sys.stdout.buffer.write("café 日本\n".encode("utf-8"))
            sys.stderr.buffer.write("über\n".encode("utf-8"))

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, "café 日本\n")
        self.assertEqual(out.stderr, "über\n")

    def test_text_and_bytes_written_in_turn_keep_their_order(self) -> None:
        def callback(**_kwargs):
            print("one")
            sys.stdout.buffer.write(b"two\n")
            print("three")

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.stdout, "one\ntwo\nthree\n")

    def test_stdin_is_empty_as_text_and_as_bytes(self) -> None:
        seen: dict[str, object] = {}

        def callback(**_kwargs):
            seen["text"] = sys.stdin.read()
            seen["bytes"] = click.get_binary_stream("stdin").read()
            seen["buffer"] = sys.stdin.buffer.read()

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.exit_code, 0, out)
        self.assertEqual(seen, {"text": "", "bytes": b"", "buffer": b""})

    def test_a_command_that_reads_stdin_sees_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            new = Path(tmp) / "new.txt"
            new.write_text("one\nzwei\n", encoding="utf-8")

            out = self.run_words("text", "diff", "--", "-", str(new))

        self.assertEqual(out.exit_code, 1, out)
        self.assertIn("+one", out.stdout)
        self.assertIn("+zwei", out.stdout)

    def test_a_prompt_cannot_hang_it(self) -> None:
        def callback(**_kwargs):
            click.confirm("Delete everything?", abort=True)
            print("deleted")

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(out.exit_code, 1)
        self.assertNotIn("deleted", out.stdout)

    def test_the_streams_are_not_terminals_so_nothing_is_coloured(self) -> None:
        seen: dict[str, object] = {}

        def callback(**_kwargs):
            seen["tty"] = (sys.stdin.isatty(), sys.stdout.isatty(), sys.stderr.isatty())
            seen["encoding"] = sys.stdout.encoding.lower().replace("_", "-")
            click.secho("red", fg="red")
            click.secho("blue", fg="blue", err=True)

        with patched("uuid", callback):
            out = self.run_words("uuid")

        self.assertEqual(seen["tty"], (False, False, False))
        self.assertEqual(seen["encoding"], "utf-8")
        self.assertEqual((out.stdout, out.stderr), ("red\n", "blue\n"))

    def test_the_streams_are_the_originals_again_afterwards(self) -> None:
        originals = (sys.stdin, sys.stdout, sys.stderr)
        inside: list[tuple[object, ...]] = []

        def callback(**_kwargs):
            inside.append((sys.stdin, sys.stdout, sys.stderr))

        with patched("uuid", callback):
            self.run_words("uuid")

        self.assertEqual(len(inside), 1)
        for during, before in zip(inside[0], originals):
            self.assertIsNot(during, before, "the command saw the real stream")
        self.assertEqual((sys.stdin, sys.stdout, sys.stderr), originals)

    def test_the_streams_are_restored_after_a_crash_an_exit_and_a_usage_error(self) -> None:
        originals = (sys.stdin, sys.stdout, sys.stderr)
        for label, callback, words in (
            ("crash", raises(KeyError("k")), ("uuid",)),
            ("exit", raises(SystemExit(5)), ("uuid",)),
            ("string exit", raises(SystemExit("bye")), ("uuid",)),
            ("usage", None, ("uuid", "--count=x")),
        ):
            with self.subTest(case=label):
                if callback is None:
                    self.run_words(*words)
                else:
                    with patched("uuid", callback):
                        self.run_words(*words)
                self.assertEqual((sys.stdin, sys.stdout, sys.stderr), originals)

    def test_a_run_after_a_crash_works(self) -> None:
        with patched("uuid", raises(ValueError("boom"))):
            self.assertIsNone(self.run_words("uuid").exit_code)

        again = self.run_words("uuid")

        self.assertEqual(again.exit_code, 0)
        self.assertEqual(len(again.stdout.split()), 1)

    def test_one_runs_output_does_not_leak_into_the_next(self) -> None:
        with patched("uuid", says(out="first\n", err="first err\n")):
            self.run_words("uuid")

        out = self.run_words("uuid", "--count=2")

        self.assertNotIn("first", out.stdout)
        self.assertEqual(out.stderr, "")

    def test_values_reach_the_command_exactly_as_given(self) -> None:
        awkward = ["--overwrite", "-y", "-", "", "--", "a b", "-5", "--count=9", "é日 \U0001F389"]
        got: list[object] = []

        def callback(**kwargs):
            got.append(kwargs["items"])

        with patched("random shuffle", callback):
            out = self.run_words(*argv("random_shuffle", {"items": awkward}))

        self.assertEqual(out.exit_code, 0, out)
        self.assertEqual(list(got[0]), awkward)


# ===========================================================================
# values reach real commands unchanged
# ===========================================================================

class ValuesEndToEndTests(FolderCase):
    def go(self, name: str, arguments: dict):
        return tools_mod().run(argv(name, arguments))

    def test_text_that_looks_like_an_option_is_encoded_as_text(self) -> None:
        for text in ("--overwrite", "-y", "--json", "-5", "--"):
            with self.subTest(text=text):
                out = self.go("text_base64", {"text": text})

                self.assertEqual(out.exit_code, 0, out)
                self.assertEqual(out.stdout.strip(), base64.b64encode(text.encode()).decode())

    def test_unicode_text_with_spaces_is_encoded_as_utf_8(self) -> None:
        text = "Привет мир 日本語 \U0001F389"

        out = self.go("text_base64", {"text": text})

        self.assertEqual(out.exit_code, 0, out)
        self.assertEqual(base64.b64decode(out.stdout.strip()).decode("utf-8"), text)

    def test_url_encoding_of_spaces_and_unicode(self) -> None:
        out = self.go("text_url", {"text": "a b&c=é"})

        self.assertEqual(out.stdout.strip(), "a%20b%26c%3D%C3%A9")

    def test_hash_of_text_that_looks_like_an_option_or_is_empty(self) -> None:
        for text in ("--overwrite", "-y", "-", "", "é日本 \U0001F389"):
            with self.subTest(text=text):
                out = self.go("hash", {"text": text})

                self.assertEqual(out.exit_code, 0, out)
                self.assertIn(hashlib.sha256(text.encode("utf-8")).hexdigest(), out.stdout)

    def test_hash_with_another_algorithm(self) -> None:
        out = self.go("hash", {"text": "abc", "algorithm": "md5"})

        self.assertIn(hashlib.md5(b"abc").hexdigest(), out.stdout)

    def test_items_that_look_like_options_are_picked_and_shuffled_as_items(self) -> None:
        items = ["--overwrite", "-y", "-"]

        picked = self.go("random_pick", {"items": items, "count": 3})
        shuffled = self.go("random_shuffle", {"items": items})

        self.assertEqual(picked.exit_code, 0, picked)
        self.assertEqual(sorted(picked.stdout.split()), sorted(items))
        self.assertEqual(shuffled.exit_code, 0, shuffled)
        self.assertEqual(sorted(shuffled.stdout.split()), sorted(items))

    def test_negative_bounds(self) -> None:
        exact = self.go("random_int", {"bounds": [-5, -5]})
        spread = self.go("random_int", {"bounds": [-10, -5], "count": 30})

        self.assertEqual(exact.stdout.strip(), "-5")
        numbers = [int(n) for n in spread.stdout.split()]
        self.assertEqual(len(numbers), 30)
        self.assertTrue(all(-10 <= n <= -5 for n in numbers), numbers)

    def test_a_negative_timestamp(self) -> None:
        out = self.go("timestamp", {"value": -1, "utc": True})

        self.assertEqual(out.exit_code, 0, out)
        self.assertIn("1969-12-31T23:59:59Z", out.stdout)

    def test_a_timestamp_given_as_a_number(self) -> None:
        out = self.go("timestamp", {"value": 0, "utc": True, "json": True})

        self.assertEqual(json.loads(out.stdout)["utc"], "1970-01-01T00:00:00Z")

    def test_a_whole_float_count_gives_that_many(self) -> None:
        out = self.go("uuid", {"count": 4.0})

        self.assertEqual(len(out.stdout.split()), 4)

    def test_file_names_with_spaces_and_unicode_are_found(self) -> None:
        path = self.write("naïve file 日本.txt", "hello")

        out = self.go("hash", {"files": [str(path)]})

        self.assertEqual(out.exit_code, 0, out)
        self.assertIn(hashlib.sha256(b"hello").hexdigest(), out.stdout)

    def test_a_pair_with_spaces_and_unicode_renames_as_given(self) -> None:
        path = self.write("naïve file.txt", "x")

        out = self.go("file_rename_apply", {"sources": [str(path)], "replace": [["ï", "i"], [" ", "_"]]})

        self.assertEqual(out.exit_code, 0, out)
        self.assertFalse(path.exists())
        self.assertTrue((self.folder / "naive_file.txt").exists())

    def test_an_invalid_value_is_refused_by_click_not_by_the_tool_layer(self) -> None:
        out = self.go("port_show", {"ports": [70000]})

        self.assertEqual(out.exit_code, 2)
        self.assertIn("70000", out.stderr)


# ===========================================================================
# call
# ===========================================================================

class CallTextTests(QuietCase):
    def call(self, name: str, arguments=None):
        return tools_mod().call(name, arguments)

    def test_a_successful_command_returns_its_stdout_as_the_text(self) -> None:
        with patched("uuid", says(out="alpha\nbeta\n")):
            result = self.call("uuid", {})

        self.assertEqual(result.text.splitlines(), ["alpha", "beta"])
        self.assertFalse(result.is_error)
        self.assertIsNone(result.data)

    def test_none_means_no_arguments(self) -> None:
        result = self.call("uuid", None)

        self.assertFalse(result.is_error)
        self.assertRegex(result.text.strip(), r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")

    def test_stderr_follows_the_stdout_under_its_own_line(self) -> None:
        with patched("uuid", says(out="out line\n", err="err line\n")):
            result = self.call("uuid", {})

        self.assertEqual(nonblank_lines(result.text), ["out line", "[stderr]", "err line"])
        self.assertFalse(result.is_error)

    def test_stderr_alone(self) -> None:
        with patched("uuid", says(err="only err\n")):
            result = self.call("uuid", {})

        self.assertEqual(nonblank_lines(result.text), ["[stderr]", "only err"])
        self.assertFalse(result.is_error)

    def test_exit_code_1_adds_its_line_and_is_not_an_error(self) -> None:
        with patched("uuid", says(out="2 ok, 1 failed\n", code=1)):
            result = self.call("uuid", {})

        self.assertEqual(nonblank_lines(result.text), ["2 ok, 1 failed", "[exit code 1]"])
        self.assertFalse(result.is_error)

    def test_all_three_parts_come_in_this_order(self) -> None:
        with patched("uuid", says(out="out\n", err="err\n", code=1)):
            result = self.call("uuid", {})

        self.assertEqual(nonblank_lines(result.text), ["out", "[stderr]", "err", "[exit code 1]"])

    def test_nothing_at_all_is_said_so(self) -> None:
        with patched("uuid", says()):
            result = self.call("uuid", {})

        self.assertEqual(result.text, "(no output)")
        self.assertFalse(result.is_error)
        self.assertIsNone(result.data)

    def test_exit_code_1_without_output_still_shows_the_code(self) -> None:
        with patched("uuid", says(code=1)):
            result = self.call("uuid", {})

        self.assertIn("[exit code 1]", result.text)
        self.assertFalse(result.is_error)

    def test_exit_code_2_or_more_is_an_error_with_the_usage_message(self) -> None:
        with patched("uuid", raises(click.UsageError("nope, not like this"))):
            result = self.call("uuid", {})

        self.assertTrue(result.is_error)
        self.assertIn("[stderr]", result.text)
        self.assertIn("Usage: jzr uuid", result.text)
        self.assertIn("nope, not like this", result.text)
        self.assertEqual(nonblank_lines(result.text)[-1], "[exit code 2]")

    def test_exit_code_3_is_an_error_too(self) -> None:
        with patched("uuid", says(out="x\n", code=3)):
            result = self.call("uuid", {})

        self.assertTrue(result.is_error)
        self.assertEqual(nonblank_lines(result.text)[-1], "[exit code 3]")

    def test_a_value_click_refuses_is_an_error_naming_the_problem(self) -> None:
        result = self.call("port_show", {"ports": [70000]})

        self.assertTrue(result.is_error)
        self.assertIn("70000", result.text)
        self.assertIn("[exit code 2]", result.text)

    def test_a_crash_is_an_error_with_one_line_and_no_traceback(self) -> None:
        with patched("uuid", raises(ValueError("kaput"))):
            result = self.call("uuid", {})

        self.assertTrue(result.is_error)
        self.assertIn("jzr failed: ValueError: kaput", result.text)
        self.assertNotIn("Traceback", result.text)
        self.assertIsNone(result.data)

    def test_the_next_call_works_after_a_crash(self) -> None:
        with patched("uuid", raises(ValueError("kaput"))):
            self.assertTrue(self.call("uuid", {}).is_error)

        result = self.call("uuid", {})

        self.assertFalse(result.is_error)
        self.assertEqual(len(result.text.split()), 1)

    def test_an_unknown_tool_is_an_error_that_names_it(self) -> None:
        result = self.call("no_such_tool", {})

        self.assertTrue(result.is_error)
        self.assertIn("no_such_tool", result.text)
        self.assertIsNone(result.data)

    def test_a_command_path_is_not_a_tool_name(self) -> None:
        for name in ("image compress", "image-compress", "Image_Compress", "", "image_compress "):
            with self.subTest(name=name):
                self.assertTrue(self.call(name, {"dir": "/nowhere"}).is_error)

    def test_a_bad_argument_is_an_error_with_the_message_and_nothing_runs(self) -> None:
        ran: list[object] = []
        with patched("image info", lambda **kw: ran.append(kw)):
            result = self.call("image_info", {"files": ["/a.png"], "x": 1})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'x'; image_info takes: files, json, no_gps", result.text)
        self.assertEqual(ran, [])
        self.assertIsNone(result.data)

    def test_a_missing_required_argument_is_an_error(self) -> None:
        result = self.call("image_info", {})

        self.assertTrue(result.is_error)
        self.assertIn("files is required", result.text)

    def test_a_wrong_type_is_an_error(self) -> None:
        result = self.call("uuid", {"count": "many"})

        self.assertTrue(result.is_error)
        self.assertIn("count must be an integer", result.text)

    def test_commands_that_are_not_exposed_cannot_be_called_and_never_run(self) -> None:
        ran: list[str] = []
        stack = contextlib.ExitStack()
        for path in EXPECTED_NOT_EXPOSED:
            stack.enter_context(patched(path, lambda _p=path, **kw: ran.append(_p)))
        with stack:
            for path in sorted(EXPECTED_NOT_EXPOSED):
                for name in (path.replace(" ", "_"), path.replace(" ", "_") + "_", path):
                    with self.subTest(name=name):
                        result = self.call(name, {})
                        self.assertTrue(result.is_error)
        self.assertEqual(ran, [])


class CallDataTests(QuietCase):
    def json_hash(self, out: str, code: int | None = None, *, json_flag: bool = True, err: str = ""):
        with patched("hash", says(out=out, err=err, code=code)):
            args = {"text": "x"}
            if json_flag:
                args["json"] = True
            return tools_mod().call("hash", args)

    def test_json_output_is_parsed_into_data_and_the_text_holds_the_same_json(self) -> None:
        result = tools_mod().call("hash", {"text": "abc", "json": True})

        self.assertFalse(result.is_error)
        self.assertIsInstance(result.data, list)
        self.assertEqual(json.loads(result.text), result.data)
        self.assertIn(hashlib.sha256(b"abc").hexdigest(), json.dumps(result.data))

    def test_without_json_there_is_no_data_even_if_the_output_happens_to_be_json(self) -> None:
        result = self.json_hash('{"a": 1}\n', json_flag=False)

        self.assertIsNone(result.data)
        self.assertIn('{"a": 1}', result.text)

    def test_json_false_gives_no_data(self) -> None:
        with patched("hash", says(out="[1]\n")):
            result = tools_mod().call("hash", {"text": "x", "json": False})

        self.assertIsNone(result.data)

    def test_exit_code_1_still_gives_data(self) -> None:
        result = self.json_hash('{"a": 1}\n', code=1)

        self.assertEqual(result.data, {"a": 1})
        self.assertFalse(result.is_error)

    def test_exit_code_2_gives_no_data(self) -> None:
        result = self.json_hash('{"a": 1}\n', code=2)

        self.assertIsNone(result.data)
        self.assertTrue(result.is_error)

    def test_a_crash_gives_no_data(self) -> None:
        with patched("hash", raises(ValueError("x"))):
            result = tools_mod().call("hash", {"text": "x", "json": True})

        self.assertIsNone(result.data)
        self.assertTrue(result.is_error)

    def test_output_that_is_not_json_gives_no_data_and_no_exception(self) -> None:
        result = self.json_hash("not json at all\n")

        self.assertIsNone(result.data)
        self.assertFalse(result.is_error)
        self.assertIn("not json at all", result.text)

    def test_empty_output_gives_no_data(self) -> None:
        result = self.json_hash("")

        self.assertIsNone(result.data)
        self.assertEqual(result.text, "(no output)")

    def test_data_comes_from_stdout_alone(self) -> None:
        result = self.json_hash('[1, 2]\n', err="note: something\n")

        self.assertEqual(result.data, [1, 2])
        self.assertIn("[stderr]", result.text)

    def test_json_scalars_and_objects_are_data_too(self) -> None:
        self.assertEqual(self.json_hash("5\n").data, 5)
        self.assertEqual(self.json_hash('"text"\n').data, "text")
        self.assertEqual(self.json_hash('{"k": [1, {"z": null}]}\n').data, {"k": [1, {"z": None}]})

    def test_data_is_none_when_the_text_was_cut(self) -> None:
        body = "[\n" + ",\n".join(str(i) for i in range(2000)) + "\n]\n"
        with mock.patch.object(tools_mod(), "MAX_TEXT", 500):
            cut = self.json_hash(body)
        with mock.patch.object(tools_mod(), "MAX_TEXT", 100_000):
            whole = self.json_hash(body)

        self.assertIsNone(cut.data)
        self.assertEqual(whole.data, list(range(2000)))


class CallCutTests(QuietCase):
    def rows(self, count: int):
        def callback(**_kwargs):
            for i in range(count):
                print(f"row {i:03d}")

        return callback

    def test_the_default_limit_is_20000_characters(self) -> None:
        self.assertEqual(tools_mod().MAX_TEXT, 20_000)

    def test_a_long_text_is_cut_at_a_line_end_with_a_closing_line(self) -> None:
        with mock.patch.object(tools_mod(), "MAX_TEXT", 1000), patched("uuid", self.rows(500)):
            result = tools_mod().call("uuid", {})

        lines = result.text.rstrip("\n").split("\n")
        closing = lines[-1]
        match = re.fullmatch(r"\.\.\. (\d+) more lines; narrow the input or use pattern", closing)
        self.assertIsNotNone(match, f"closing line was {closing!r}")
        kept = lines[:-1]
        if kept and kept[-1] == "":
            kept = kept[:-1]
        self.assertEqual(kept, [f"row {i:03d}" for i in range(len(kept))], "a line was cut in two or skipped")
        self.assertEqual(int(match.group(1)), 500 - len(kept))
        self.assertGreater(int(match.group(1)), 0)
        self.assertLessEqual(len("\n".join(kept)), 1000)
        self.assertGreaterEqual(len("\n".join(kept)), 500, "far less than the limit was kept")
        self.assertFalse(result.is_error)

    def test_a_text_within_the_limit_is_not_touched(self) -> None:
        with mock.patch.object(tools_mod(), "MAX_TEXT", 1000), patched("uuid", self.rows(50)):
            result = tools_mod().call("uuid", {})

        self.assertEqual(result.text.splitlines(), [f"row {i:03d}" for i in range(50)])
        self.assertNotIn("more lines", result.text)

    def test_the_real_limit_cuts_a_real_flood(self) -> None:
        with patched("uuid", self.rows(5000)):
            result = tools_mod().call("uuid", {})

        self.assertLess(len(result.text), 20_000 + 200)
        match = re.search(r"\.\.\. (\d+) more lines; narrow the input or use pattern\s*$", result.text)
        self.assertIsNotNone(match)
        kept = len(result.text.splitlines()) - 1
        self.assertEqual(int(match.group(1)), 5000 - kept)


# ===========================================================================
# real files in temporary folders
# ===========================================================================

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ImageToolTests(FolderCase):
    def call(self, name: str, arguments: dict):
        return tools_mod().call(name, arguments)

    def test_resize_writes_a_new_file_and_leaves_the_source_alone(self) -> None:
        from PIL import Image

        source = self.png()
        before = digest(source)

        result = self.call("image_resize", {"sources": [str(source)], "width": 50})

        self.assertFalse(result.is_error, result.text)
        made = self.folder / "a_50x25.png"
        self.assertTrue(made.is_file())
        with Image.open(made) as image:
            self.assertEqual(image.size, (50, 25))
        self.assertEqual(digest(source), before)
        self.assertIn(str(made), result.text)

    def test_a_second_identical_resize_skips_and_does_not_replace_the_file(self) -> None:
        source = self.png()
        self.call("image_resize", {"sources": [str(source)], "width": 50})
        made = self.folder / "a_50x25.png"
        stamp = (digest(made), made.stat().st_mtime_ns)

        again = self.call("image_resize", {"sources": [str(source)], "width": 50})

        self.assertFalse(again.is_error, again.text)
        self.assertIn("target exists", again.text)
        self.assertEqual((digest(made), made.stat().st_mtime_ns), stamp)

    def test_an_existing_output_is_never_replaced_by_any_writing_image_tool(self) -> None:
        cases = (
            ("image_resize", {"width": 50}, "a_50x25.png"),
            ("image_convert", {"to": "jpg"}, "a.jpg"),
            ("image_compress", {"quality": 50}, "a_compressed.png"),
        )
        for name, options, output in cases:
            with self.subTest(tool=name):
                source = self.png()
                target = self.write(output, b"keep me")

                result = self.call(name, {"sources": [str(source)], **options})

                self.assertFalse(result.is_error, result.text)
                self.assertEqual(target.read_bytes(), b"keep me")
                self.assertIn("target exists", result.text)
                target.unlink()

    def test_convert_and_compress_write_new_files(self) -> None:
        from PIL import Image

        source = self.png()

        converted = self.call("image_convert", {"sources": [str(source)], "to": "jpg"})
        compressed = self.call("image_compress", {"sources": [str(source)], "quality": 50})

        self.assertFalse(converted.is_error, converted.text)
        self.assertFalse(compressed.is_error, compressed.text)
        with Image.open(self.folder / "a.jpg") as image:
            self.assertEqual(image.format, "JPEG")
        self.assertTrue((self.folder / "a_compressed.png").is_file())

    def test_a_whole_folder_can_be_given_as_dir(self) -> None:
        self.png("one.png")
        self.png("two.png")

        result = self.call("image_convert", {"dir": str(self.folder), "to": "jpg"})

        self.assertFalse(result.is_error, result.text)
        self.assertTrue((self.folder / "one.jpg").is_file())
        self.assertTrue((self.folder / "two.jpg").is_file())

    def test_a_dry_run_writes_nothing(self) -> None:
        source = self.png()

        result = self.call("image_resize", {"sources": [str(source)], "width": 50, "dry_run": True})

        self.assertFalse(result.is_error, result.text)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["a.png"])

    def test_overwrite_is_not_available_and_nothing_is_written(self) -> None:
        source = self.png()
        target = self.write("a_50x25.png", b"keep me")

        result = self.call("image_resize", {"sources": [str(source)], "width": 50, "overwrite": True})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'overwrite'", result.text)
        self.assertEqual(target.read_bytes(), b"keep me")

    def test_a_batch_call_without_sources_or_dir_never_looks_at_the_servers_folder(self) -> None:
        self.png()
        old = os.getcwd()
        os.chdir(self.folder)
        try:
            result = self.call("image_convert", {"to": "jpg"})
        finally:
            os.chdir(old)

        self.assertTrue(result.is_error)
        self.assertIn("give sources, or dir to work on a whole folder", result.text)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["a.png"])

    def test_image_info_returns_json_as_data(self) -> None:
        source = self.png()

        result = self.call("image_info", {"files": [str(source)], "json": True})

        self.assertFalse(result.is_error, result.text)
        self.assertEqual(json.loads(result.text), result.data)
        self.assertEqual(len(result.data), 1)
        self.assertEqual((result.data[0]["width"], result.data[0]["height"]), (200, 100))
        self.assertEqual(result.data[0]["format"], "PNG")

    def test_image_info_without_json_is_plain_text_and_no_data(self) -> None:
        source = self.png()

        result = self.call("image_info", {"files": [str(source)]})

        self.assertFalse(result.is_error, result.text)
        self.assertIsNone(result.data)
        self.assertIn("200", result.text)

    def test_image_info_of_a_missing_file_is_a_failed_file_not_a_crash(self) -> None:
        result = self.call("image_info", {"files": [str(self.folder / "missing.png")]})

        self.assertNotIn("jzr failed", result.text)
        self.assertIn("missing.png", result.text)


class ReadOnlyToolTests(FolderCase):
    def call(self, name: str, arguments: dict):
        return tools_mod().call(name, arguments)

    def test_hash_of_a_file_and_of_text(self) -> None:
        path = self.write("n.txt", "hello")

        of_file = self.call("hash", {"files": [str(path)], "json": True})
        of_text = self.call("hash", {"text": "abc"})

        self.assertFalse(of_file.is_error, of_file.text)
        self.assertEqual(of_file.data[0]["hash"], hashlib.sha256(b"hello").hexdigest())
        self.assertEqual(of_file.data[0]["algorithm"], "sha256")
        self.assertEqual(of_text.text.strip(), hashlib.sha256(b"abc").hexdigest())

    def test_file_rename_only_plans_and_file_rename_apply_renames(self) -> None:
        source = self.write("f1.txt", "1")

        plan = self.call("file_rename", {"sources": [str(source)], "prefix": "x_"})

        self.assertFalse(plan.is_error, plan.text)
        self.assertIn("would be renamed", plan.text)
        self.assertTrue(source.exists())
        self.assertFalse((self.folder / "x_f1.txt").exists())

        done = self.call("file_rename_apply", {"sources": [str(source)], "prefix": "x_"})

        self.assertFalse(done.is_error, done.text)
        self.assertFalse(source.exists())
        self.assertEqual((self.folder / "x_f1.txt").read_text(encoding="utf-8"), "1")

    def test_file_rename_cannot_be_told_to_apply(self) -> None:
        source = self.write("f1.txt", "1")

        result = self.call("file_rename", {"sources": [str(source)], "prefix": "x_", "apply": True})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'apply'", result.text)
        self.assertTrue(source.exists())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["f1.txt"])

    def test_file_rename_plan_as_json_is_data(self) -> None:
        source = self.write("f1.txt", "1")

        result = self.call("file_rename", {"sources": [str(source)], "prefix": "x_", "json": True})

        self.assertFalse(result.is_error, result.text)
        self.assertIsNotNone(result.data)
        self.assertEqual(json.loads(result.text), result.data)
        self.assertTrue(source.exists())

    def test_data_json_formats_and_never_writes(self) -> None:
        source = self.write("d.json", '{"b":1,"a":[1,2]}')
        before = source.read_bytes()

        shown = self.call("data_json", {"files": [str(source)], "indent": 2})
        refused = self.call("data_json", {"files": [str(source)], "indent": 2, "write": True})

        self.assertFalse(shown.is_error, shown.text)
        self.assertIn('"b": 1', shown.text)
        self.assertTrue(refused.is_error)
        self.assertIn("unknown argument 'write'", refused.text)
        self.assertEqual(source.read_bytes(), before)

    def test_text_base64_cannot_write_a_file_or_use_the_clipboard(self) -> None:
        target = self.folder / "out.txt"
        for left in ({"output": str(target)}, {"overwrite": True}, {"copy": True}):
            with self.subTest(left_out=sorted(left)):
                result = self.call("text_base64", {"text": "hi", **left})
                self.assertTrue(result.is_error)
                self.assertIn(f"unknown argument '{next(iter(left))}'", result.text)
        self.assertFalse(target.exists())

    def test_the_clipboard_option_is_refused_before_anything_runs(self) -> None:
        ran: list[str] = []
        for name, path in (("text_slug", "text slug"), ("text_url", "text url"), ("uuid", "uuid"),
                           ("password", "password"), ("env_example", "env example")):
            with self.subTest(tool=name), patched(path, lambda _n=name, **kw: ran.append(_n)):
                result = self.call(name, {"copy": True})
                self.assertTrue(result.is_error)
                self.assertIn("unknown argument 'copy'", result.text)
        self.assertEqual(ran, [])

    def test_env_example_prints_the_keys_and_writes_nothing(self) -> None:
        env = self.write(".env", "A=1\nSECRET=xyz\n")

        result = self.call("env_example", {"file": str(env)})
        refused = self.call("env_example", {"file": str(env), "output": str(self.folder / ".env.example")})

        self.assertFalse(result.is_error, result.text)
        self.assertIn("A=", result.text)
        self.assertIn("SECRET=", result.text)
        self.assertNotIn("xyz", result.text)
        self.assertTrue(refused.is_error)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [".env"])

    def test_env_compare_reads_two_files(self) -> None:
        one = self.write("one.env", "A=1\nB=2\n")
        two = self.write("two.env", "A=1\nC=3\n")

        result = self.call("env_compare", {"a": str(one), "b": str(two)})

        self.assertFalse(result.is_error, result.text)
        self.assertIn("B", result.text)
        self.assertIn("C", result.text)

    def test_text_diff_of_two_files_differing_is_not_an_error(self) -> None:
        old = self.write("old.txt", "one\ntwo\n")
        new = self.write("new.txt", "one\nzwei é\n")

        result = self.call("text_diff", {"old": str(old), "new": str(new)})

        self.assertFalse(result.is_error, result.text)
        self.assertIn("-two", result.text)
        self.assertIn("+zwei é", result.text)
        self.assertTrue(result.text.rstrip().endswith("[exit code 1]"))

    def test_text_diff_of_equal_files_has_no_output(self) -> None:
        old = self.write("old.txt", "same\n")
        new = self.write("new.txt", "same\n")

        result = self.call("text_diff", {"old": str(old), "new": str(new)})

        self.assertEqual(result.text, "(no output)")
        self.assertFalse(result.is_error)

    def test_text_diff_cannot_ask_for_colour(self) -> None:
        result = self.call("text_diff", {"old": "a", "new": "b", "color": "always"})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'color'", result.text)

    def test_port_show_as_json_is_a_list(self) -> None:
        from jzr_tools.core.network import PortOwner

        # macOS lists other processes' sockets only for root.
        owner = PortOwner(pid=42, name="demo", protocol="tcp", address="127.0.0.1", port=1, status="LISTEN")
        with mock.patch("jzr_tools.commands.network.list_port_owners", return_value=[owner]):
            result = self.call("port_show", {"ports": [1], "json": True})

        self.assertFalse(result.is_error, result.text)
        self.assertIsInstance(result.data, list)
        self.assertEqual([row["pid"] for row in result.data], [42])
        self.assertEqual(json.loads(result.text), result.data)

    def test_port_show_without_permission_passes_the_message_on_and_has_no_data(self) -> None:
        denied = RuntimeError("Cannot inspect network ports.")
        with mock.patch("jzr_tools.commands.network.list_port_owners", side_effect=denied):
            result = self.call("port_show", {"ports": [1], "json": True})

        self.assertIn("Cannot inspect network ports.", result.text)
        self.assertIn("[exit code 1]", result.text)
        self.assertIsNone(result.data)

    def test_host_procs_lists_processes_and_cannot_kill(self) -> None:
        ran: list[object] = []
        listed = self.call("host_procs", {"json": True, "top": 1})
        with patched("host procs", lambda **kw: ran.append(kw)):
            for left in ("kill", "force"):
                with self.subTest(left_out=left):
                    refused = self.call("host_procs", {left: True})
                    self.assertTrue(refused.is_error)
                    self.assertIn(f"unknown argument '{left}'", refused.text)

        self.assertFalse(listed.is_error, listed.text)
        self.assertIn("rows", listed.data)
        self.assertLessEqual(len(listed.data["rows"]), 1)
        self.assertEqual(ran, [])

    def test_host_status_cannot_go_live(self) -> None:
        result = self.call("host_status", {"live": True})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'live'", result.text)

    def test_jwt_is_decoded_without_a_secret(self) -> None:
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )

        result = self.call("jwt", {"token": token, "json": True})

        self.assertFalse(result.is_error, result.text)
        self.assertEqual(result.data["header"]["alg"], "HS256")
        self.assertEqual(result.data["payload"]["sub"], "1234567890")

    def test_random_int_in_a_range(self) -> None:
        result = self.call("random_int", {"bounds": [7, 7]})

        self.assertEqual(result.text.strip(), "7")

    def test_ffmpeg_status_reports_without_changing_anything(self) -> None:
        result = self.call("ffmpeg_status", {})

        self.assertFalse(result.is_error, result.text)
        self.assertIn("ffmpeg", result.text.lower())


class DestructiveToolTests(QuietCase):
    def test_port_kill_hands_the_ports_to_the_command_as_integers(self) -> None:
        seen: list[dict] = []
        with patched("port kill", lambda **kw: seen.append(kw)):
            result = tools_mod().call("port_kill", {"ports": [8123, 8124.0], "restart_hns": False})

        self.assertFalse(result.is_error, result.text)
        self.assertEqual(len(seen), 1)
        self.assertEqual(list(seen[0]["ports"]), [8123, 8124])
        self.assertFalse(seen[0]["restart_hns"])

    def test_port_kill_without_ports_is_refused_before_it_runs(self) -> None:
        ran: list[object] = []
        with patched("port kill", lambda **kw: ran.append(kw)):
            for arguments in ({}, {"ports": []}, {"ports": [0]}, {"ports": [65536]}):
                with self.subTest(arguments=arguments):
                    result = tools_mod().call("port_kill", arguments)
                    self.assertTrue(result.is_error)
        self.assertEqual(ran, [])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class AudioTagToolTests(FolderCase):
    def setUp(self) -> None:
        super().setUp()
        self.mp3 = self.folder / "song.mp3"
        ffmpeg_run(
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libmp3lame", str(self.mp3)
        )

    def test_audio_tags_reads_and_cannot_edit(self) -> None:
        before = digest(self.mp3)

        read = tools_mod().call("audio_tags", {"files": [str(self.mp3)], "json": True})
        refused = tools_mod().call("audio_tags", {"files": [str(self.mp3)], "set": ["title=x"]})

        self.assertFalse(read.is_error, read.text)
        self.assertEqual(read.data[0]["tags"], {})
        self.assertTrue(refused.is_error)
        self.assertIn("unknown argument 'set'", refused.text)
        self.assertEqual(digest(self.mp3), before)

    def test_audio_tags_edit_writes_tags_that_audio_tags_then_reads(self) -> None:
        title = "Привет 日本"

        edited = tools_mod().call("audio_tags_edit", {"files": [str(self.mp3)], "set": [f"title={title}"]})
        read = tools_mod().call("audio_tags", {"files": [str(self.mp3)], "json": True})

        self.assertFalse(edited.is_error, edited.text)
        self.assertEqual(read.data[0]["tags"]["title"], title)

    def test_audio_tags_edit_has_no_json_property(self) -> None:
        result = tools_mod().call("audio_tags_edit", {"files": [str(self.mp3)], "json": True})

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'json'", result.text)

    def test_audio_trim_writes_a_new_file_and_keeps_the_source(self) -> None:
        before = digest(self.mp3)

        result = tools_mod().call(
            "audio_trim", {"sources": [str(self.mp3)], "start": "0", "end": "0.5"}
        )

        self.assertFalse(result.is_error, result.text)
        self.assertEqual(digest(self.mp3), before)
        self.assertTrue((self.folder / "song_trim.mp3").is_file())

        again = tools_mod().call("audio_trim", {"sources": [str(self.mp3)], "start": "0", "end": "0.5"})
        self.assertFalse(again.is_error, again.text)
        self.assertIn("exists", again.text)


if __name__ == "__main__":
    unittest.main()

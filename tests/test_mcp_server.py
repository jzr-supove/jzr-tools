"""Behavioural tests for the MCP server: in-process and as a real stdio process.

The whole file is skipped when the optional `mcp` package is not installed.
"""

from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import anyio
    import mcp  # noqa: F401
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
except ImportError as error:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest(f"the mcp package is not installed: {error}") from None

from jzr_tools import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
WAIT = 60


def _tools():  # noqa: ANN202
    return importlib.import_module("jzr_tools.commands._mcp_tools")


def _server_module():  # noqa: ANN202
    return importlib.import_module("jzr_tools.commands._mcp_server")


def run_async(body):  # noqa: ANN001, ANN201
    """Run `body(client)` against an in-process server; a hang fails after WAIT seconds."""

    async def main():  # noqa: ANN202
        with anyio.fail_after(WAIT):
            async with Client(_server_module().build_server(), **body.client_args) as client:
                return await body(client)

    return anyio.run(main)


def in_process(**client_args):  # noqa: ANN003, ANN201
    def wrap(body):  # noqa: ANN001, ANN202
        body.client_args = client_args
        return run_async(body)

    return wrap


def text_of(result) -> str:  # noqa: ANN001
    return "".join(block.text for block in result.content if getattr(block, "type", "") == "text")


def child_env(config_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_PATH)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["JZR_CONFIG_DIR"] = config_dir
    return env


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="jzr-mcp-server-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()


class ListingTests(ServerCase):
    def test_the_server_names_itself_jzr_with_the_package_version_and_instructions(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return client.server_info, client.instructions

        info, instructions = in_process(mode="legacy")(body)

        self.assertEqual(info.name, "jzr")
        self.assertEqual(info.version, __version__)
        self.assertTrue(instructions and instructions.strip())
        lowered = instructions.lower()
        self.assertIn("absolute", lowered)
        self.assertIn("destructive", lowered)

    def test_tools_list_has_exactly_the_exposed_tools_once_each(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.list_tools()

        listing = in_process()(body)

        names = [tool.name for tool in listing.tools]
        self.assertEqual(len(names), len(set(names)), names)
        self.assertEqual(set(names), set(_tools().EXPOSED))
        self.assertIsNone(listing.next_cursor)

    def test_every_tool_has_the_schema_and_description_generated_from_its_command(self) -> None:
        specs = {spec.name: spec for spec in _tools().tools()}

        async def body(client):  # noqa: ANN001, ANN202
            return await client.list_tools()

        listing = in_process()(body)

        for tool in listing.tools:
            with self.subTest(tool=tool.name):
                self.assertEqual(tool.input_schema, specs[tool.name].schema)
                self.assertEqual(tool.description, specs[tool.name].description)
                self.assertEqual(tool.input_schema["type"], "object")
                self.assertIs(tool.input_schema["additionalProperties"], False)

    def test_annotations_follow_the_kind_of_each_tool(self) -> None:
        exposed = _tools().EXPOSED

        async def body(client):  # noqa: ANN001, ANN202
            return await client.list_tools()

        listing = in_process()(body)

        seen = set()
        for tool in listing.tools:
            kind = exposed[tool.name].kind
            seen.add(kind)
            note = tool.annotations
            with self.subTest(tool=tool.name, kind=kind):
                self.assertIsNotNone(note)
                self.assertIs(note.open_world_hint, False)
                if kind == "read":
                    self.assertIs(note.read_only_hint, True)
                elif kind == "write":
                    self.assertIs(note.read_only_hint, False)
                    self.assertIs(note.destructive_hint, False)
                else:
                    self.assertEqual(kind, "destructive")
                    self.assertIs(note.read_only_hint, False)
                    self.assertIs(note.destructive_hint, True)
        self.assertEqual(seen, {"read", "write", "destructive"})

    def test_a_few_tools_have_the_hints_the_allow_list_promises(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return {tool.name: tool for tool in (await client.list_tools()).tools}

        tools = in_process()(body)

        self.assertIs(tools["hash"].annotations.read_only_hint, True)
        self.assertIs(tools["image_resize"].annotations.destructive_hint, False)
        self.assertIs(tools["port_kill"].annotations.destructive_hint, True)
        self.assertIs(tools["file_rename_apply"].annotations.destructive_hint, True)
        self.assertNotIn("overwrite", tools["image_resize"].input_schema["properties"])


class CallTests(ServerCase):
    def test_a_read_tool_returns_the_command_output_as_text(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc"})

        result = in_process()(body)

        self.assertFalse(result.is_error, text_of(result))
        self.assertIn(ABC_SHA256, text_of(result))
        self.assertIsNone(result.structured_content)

    def test_json_output_that_is_a_list_comes_back_wrapped_in_result(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc", "json": True})

        result = in_process()(body)

        self.assertFalse(result.is_error, text_of(result))
        self.assertEqual(
            result.structured_content,
            {"result": [{"file": None, "algorithm": "sha256", "hash": ABC_SHA256}]},
        )
        self.assertEqual(json.loads(text_of(result)), result.structured_content["result"])

    def test_json_output_that_is_an_object_comes_back_as_it_is(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("timestamp", {"value": "0", "utc": True, "json": True})

        result = in_process()(body)

        self.assertFalse(result.is_error, text_of(result))
        self.assertEqual(result.structured_content["seconds"], 0)
        self.assertEqual(result.structured_content["utc"], "1970-01-01T00:00:00Z")
        self.assertNotIn("result", result.structured_content)

    def test_a_usage_error_is_a_tool_error_with_the_message_and_exit_code(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc", "algorithm": "nope"})

        result = in_process()(body)

        self.assertTrue(result.is_error)
        text = text_of(result)
        self.assertIn("nope", text)
        self.assertIn("[exit code 2]", text)
        self.assertIsNone(result.structured_content)

    def test_an_unknown_tool_is_a_tool_error(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("no_such_tool", {})

        result = in_process()(body)

        self.assertTrue(result.is_error)
        self.assertIn("no_such_tool", text_of(result))

    def test_a_missing_required_argument_is_a_tool_error_naming_it(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("image_info", {})

        result = in_process()(body)

        self.assertTrue(result.is_error)
        self.assertIn("files is required", text_of(result))

    def test_an_unknown_argument_is_a_tool_error_listing_what_the_tool_takes(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc", "bogus": 1})

        result = in_process()(body)

        self.assertTrue(result.is_error)
        self.assertIn("unknown argument 'bogus'", text_of(result))

    def test_a_batch_tool_with_neither_sources_nor_dir_is_refused_without_scanning(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("image_resize", {"width": 10})

        result = in_process()(body)

        self.assertTrue(result.is_error)
        self.assertIn("give sources, or dir to work on a whole folder", text_of(result))

    def test_a_crash_in_a_command_is_a_tool_error_and_the_next_call_still_works(self) -> None:
        from jzr_tools import cli

        async def crash(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc"})

        with mock.patch.object(cli.app, "main", side_effect=ValueError("boom\nsecond line")):
            crashed = in_process()(crash)

        self.assertTrue(crashed.is_error)
        text = text_of(crashed)
        self.assertIn("jzr failed: ValueError: boom", text)
        self.assertNotIn("second line", text)
        self.assertNotIn("Traceback", text)

        async def twice(client):  # noqa: ANN001, ANN202
            with mock.patch.object(cli.app, "main", side_effect=RuntimeError("once")):
                first = await client.call_tool("hash", {"text": "abc"})
            second = await client.call_tool("hash", {"text": "abc"})
            return first, second

        first, second = in_process()(twice)

        self.assertTrue(first.is_error)
        self.assertIn("jzr failed: RuntimeError: once", text_of(first))
        self.assertFalse(second.is_error, text_of(second))
        self.assertIn(ABC_SHA256, text_of(second))


class ConcurrencyTests(ServerCase):
    def slow_runner(self, seconds: float, log: list[tuple[str, float]], lock: threading.Lock):  # noqa: ANN202
        tools = _tools()

        def run(argv):  # noqa: ANN001, ANN202
            with lock:
                log.append(("enter", time.monotonic()))
            time.sleep(seconds)
            with lock:
                log.append(("exit", time.monotonic()))
            return tools.Output(stdout="done\n", stderr="", exit_code=0)

        return run

    def test_two_concurrent_calls_never_overlap_inside_the_runner(self) -> None:
        log: list[tuple[str, float]] = []
        lock = threading.Lock()
        results = []

        async def body(client):  # noqa: ANN001, ANN202
            async def one() -> None:
                results.append(await client.call_tool("hash", {"text": "x"}))

            async with anyio.create_task_group() as group:
                for _ in range(3):
                    group.start_soon(one)

        with mock.patch.object(_tools(), "run", self.slow_runner(0.25, log, lock)):
            in_process()(body)

        self.assertEqual(len(results), 3)
        for result in results:
            self.assertFalse(result.is_error, text_of(result))
            self.assertIn("done", text_of(result))
        active = peak = 0
        for kind, _when in log:
            active += 1 if kind == "enter" else -1
            peak = max(peak, active)
        self.assertEqual([kind for kind, _ in log], ["enter", "exit"] * 3)
        self.assertEqual(peak, 1)

    def test_the_server_keeps_answering_while_a_slow_tool_runs(self) -> None:
        log: list[tuple[str, float]] = []
        lock = threading.Lock()
        observed: dict[str, object] = {}

        async def body(client):  # noqa: ANN001, ANN202
            slow_done = anyio.Event()

            async def slow() -> None:
                observed["slow"] = await client.call_tool("hash", {"text": "x"})
                slow_done.set()

            async with anyio.create_task_group() as group:
                group.start_soon(slow)
                with anyio.fail_after(10):
                    while not log:
                        await anyio.sleep(0.02)
                started = time.monotonic()
                with anyio.fail_after(1.0):
                    listing = await client.list_tools()
                observed["list_seconds"] = time.monotonic() - started
                observed["slow_finished_first"] = slow_done.is_set()
                observed["tools"] = len(listing.tools)

        with mock.patch.object(_tools(), "run", self.slow_runner(2.0, log, lock)):
            in_process()(body)

        self.assertFalse(observed["slow_finished_first"], "tools/list had to wait for the slow tool")
        self.assertLess(observed["list_seconds"], 1.0)
        self.assertGreater(observed["tools"], 0)
        self.assertEqual(text_of(observed["slow"]), "done\n")


class ServeTests(unittest.TestCase):
    def test_ctrl_c_ends_serve_quietly(self) -> None:
        module = _server_module()
        outcome: dict[str, object] = {}
        stderr = io.StringIO()

        def interrupted(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            for item in args:
                if hasattr(item, "close"):
                    item.close()
            raise KeyboardInterrupt

        def target() -> None:
            try:
                with mock.patch("anyio.run", side_effect=interrupted), mock.patch.object(sys, "stderr", stderr):
                    outcome["value"] = module.serve()
            except BaseException as error:  # noqa: BLE001
                outcome["error"] = error

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(10)

        self.assertFalse(worker.is_alive(), "serve() did not return")
        self.assertNotIn("error", outcome, outcome.get("error"))
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stderr.getvalue().strip(), "")


class StdioProcessTests(ServerCase):
    def params(self) -> StdioServerParameters:
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "jzr_tools", "agent", "mcp"],
            cwd=work,
            env=child_env(str(self.root / "bindings")),
        )

    def over_stdio(self, body):  # noqa: ANN001, ANN201
        async def main():  # noqa: ANN202
            with anyio.fail_after(WAIT):
                async with Client(self.params()) as client:
                    return await body(client)

        return anyio.run(main)

    def test_the_real_server_lists_the_same_tools(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.list_tools()

        listing = self.over_stdio(body)

        self.assertEqual({tool.name for tool in listing.tools}, set(_tools().EXPOSED))

    def test_hash_on_text_works_through_the_pipe(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("hash", {"text": "abc"})

        result = self.over_stdio(body)

        self.assertFalse(result.is_error, text_of(result))
        self.assertIn(ABC_SHA256, text_of(result))

    def test_image_resize_on_an_absolute_path_with_non_ascii_letters(self) -> None:
        from PIL import Image

        folder = self.root / "bilder und фото"
        folder.mkdir()
        source = folder / "fötö ünï.png"
        Image.new("RGB", (16, 16), (10, 120, 200)).save(source)

        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("image_resize", {"sources": [str(source)], "width": 8})

        result = self.over_stdio(body)

        self.assertFalse(result.is_error, text_of(result))
        made = sorted(path.name for path in folder.iterdir() if path != source)
        self.assertEqual(len(made), 1, made)
        self.assertTrue(made[0].startswith("fötö ünï"), made)
        with Image.open(folder / made[0]) as small:
            self.assertEqual(small.size, (8, 8))
        self.assertIn("fötö ünï", text_of(result))

    def test_image_info_with_json_returns_structured_content(self) -> None:
        from PIL import Image

        source = self.root / "small.png"
        Image.new("RGB", (16, 8), (10, 120, 200)).save(source)

        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("image_info", {"json": True, "files": [str(source)]})

        result = self.over_stdio(body)

        self.assertFalse(result.is_error, text_of(result))
        self.assertEqual(result.structured_content, {"result": json.loads(text_of(result))})
        self.assertIsInstance(result.structured_content["result"], list)

    def test_a_command_that_prints_a_lot_is_cut_with_a_closing_line(self) -> None:
        async def body(client):  # noqa: ANN001, ANN202
            return await client.call_tool("random_int", {"count": 30000})

        result = self.over_stdio(body)

        self.assertFalse(result.is_error, text_of(result)[-200:])
        text = text_of(result)
        self.assertIn("more lines; narrow the input or use pattern", text)
        self.assertLessEqual(len(text), _tools().MAX_TEXT + 200)
        self.assertGreater(len(text), _tools().MAX_TEXT // 2)
        self.assertIsNone(result.structured_content)


class RawStdioTests(ServerCase):
    """Talks to the server with hand-written JSON-RPC lines."""

    def start(self) -> subprocess.Popen:
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        process = subprocess.Popen(
            [sys.executable, "-m", "jzr_tools", "agent", "mcp"],
            cwd=work,
            env=child_env(str(self.root / "bindings")),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop, process)
        self.lines: queue.Queue = queue.Queue()
        self.seen: list[bytes] = []
        self.err_chunks: list[bytes] = []
        threading.Thread(target=self.pump_out, args=(process,), daemon=True).start()
        threading.Thread(target=lambda: self.err_chunks.append(process.stderr.read()), daemon=True).start()
        return process

    def pump_out(self, process: subprocess.Popen) -> None:
        for line in iter(process.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def send(self, process: subprocess.Popen, message: dict) -> None:
        process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        process.stdin.flush()

    def reply(self, message_id: int, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.fail(f"no reply to request {message_id} within {timeout} s; saw {self.seen!r}")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                self.fail(f"the server closed stdout before replying to {message_id}; saw {self.seen!r}")
            self.seen.append(line)
            message = json.loads(line)
            if message.get("id") == message_id and ("result" in message or "error" in message):
                return message

    def test_stdout_carries_only_protocol_lines_and_closing_stdin_ends_the_process(self) -> None:
        from mcp_types.version import LATEST_HANDSHAKE_VERSION

        process = self.start()
        self.send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_HANDSHAKE_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "jzr-test", "version": "0"},
                },
            },
        )
        init = self.reply(1)
        self.assertEqual(init["result"]["serverInfo"]["name"], "jzr", init)
        self.send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing = self.reply(2)
        self.assertEqual({tool["name"] for tool in listing["result"]["tools"]}, set(_tools().EXPOSED))
        self.send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "random_int", "arguments": {"count": 30000}},
            },
        )
        called = self.reply(3)
        self.assertFalse(called["result"].get("isError", False), called)
        self.send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "hash", "arguments": {"text": "abc"}},
            },
        )
        hashed = self.reply(4)
        self.assertIn(ABC_SHA256, hashed["result"]["content"][0]["text"])

        process.stdin.close()
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("the server did not exit within 15 s of stdin being closed")
        while True:
            try:
                line = self.lines.get(timeout=5)
            except queue.Empty:
                break
            if line is None:
                break
            self.seen.append(line)

        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(self.seen), 4)
        for line in self.seen:
            with self.subTest(line=line[:80]):
                document = json.loads(line.decode("utf-8"))
                self.assertIsInstance(document, dict)
                self.assertEqual(document.get("jsonrpc"), "2.0")
        time.sleep(0.2)
        stderr = b"".join(self.err_chunks).decode("utf-8", "replace")
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()

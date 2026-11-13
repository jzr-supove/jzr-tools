"""Unmocked smoke tests: `jzr host status` on the machine running the tests."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

import jzr_tools
from jzr_tools.cli import app

try:
    import fcntl
    import pty
    import struct
    import termios
except ImportError:  # Windows
    pty = None

from host_fixtures import display_width, strip_ansi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = Path(jzr_tools.__file__).resolve().parents[1]  # the package under test

runner = CliRunner()


def is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_percent(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 100
    )


class RealMachineJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = runner.invoke(app, ["host", "status", "--json"])
        cls.data = json.loads(cls.result.stdout) if cls.result.exit_code == 0 else {}

    def setUp(self) -> None:
        self.assertEqual(self.result.exit_code, 0, self.result.output)

    def test_exits_zero_and_prints_a_json_document_without_control_codes(self) -> None:
        self.assertEqual(self.result.exit_code, 0, self.result.output)
        self.assertNotIn("\x1b", self.result.stdout)
        self.assertEqual(
            set(self.data),
            {
                "timestamp",
                "host",
                "cpu",
                "memory",
                "storage",
                "gpus",
                "network",
                "temperatures",
                "battery",
            },
        )

    def test_timestamp_is_iso_8601_with_a_utc_offset(self) -> None:
        stamp = datetime.fromisoformat(self.data["timestamp"])

        self.assertIsNotNone(stamp.utcoffset())

    def test_host_section_has_a_name_and_a_positive_uptime(self) -> None:
        host = self.data["host"]

        self.assertTrue(host["hostname"])
        self.assertTrue(host["os"])
        self.assertGreater(host["uptime_seconds"], 0)

    def test_memory_has_positive_byte_counts_and_sane_percentages(self) -> None:
        memory = self.data["memory"]

        self.assertTrue(is_count(memory["total_bytes"]), memory)
        self.assertTrue(is_count(memory["used_bytes"]), memory)
        self.assertLessEqual(memory["used_bytes"], memory["total_bytes"])
        self.assertTrue(is_percent(memory["percent"]), memory)
        self.assertTrue(is_percent(memory["swap_percent"]), memory)
        self.assertIsInstance(memory["swap_total_bytes"], int)
        self.assertGreaterEqual(memory["swap_total_bytes"], 0)

    def test_at_least_one_storage_entry_with_positive_capacity(self) -> None:
        storage = self.data["storage"]

        self.assertGreaterEqual(len(storage), 1)
        for entry in storage:
            with self.subTest(mount=entry["mount"]):
                self.assertTrue(is_count(entry["total_bytes"]), entry)
                self.assertTrue(is_percent(entry["percent"]), entry)
                self.assertGreaterEqual(entry["used_bytes"], 0)
                self.assertGreaterEqual(entry["free_bytes"], 0)
                self.assertNotIn(entry["fstype"], ("squashfs", "tmpfs", "proc", "sysfs"))

    def test_cpu_has_cores_and_percentages(self) -> None:
        cpu = self.data["cpu"]

        self.assertGreaterEqual(cpu["logical_cores"], 1)
        self.assertTrue(is_percent(cpu["usage_percent"]), cpu)
        self.assertGreaterEqual(len(cpu["per_core_percent"]), 1)
        for value in cpu["per_core_percent"]:
            self.assertTrue(is_percent(value), cpu["per_core_percent"])

    def test_gpu_entries_if_any_are_well_formed(self) -> None:
        self.assertIsInstance(self.data["gpus"], list)
        for index, gpu in enumerate(self.data["gpus"]):
            with self.subTest(gpu=gpu["name"]):
                self.assertEqual(gpu["index"], index)
                self.assertTrue(gpu["name"])
                if gpu["utilization_percent"] is not None:
                    self.assertTrue(is_percent(gpu["utilization_percent"]), gpu)

    def test_other_sections_have_the_documented_shapes(self) -> None:
        self.assertIsInstance(self.data["network"], list)
        self.assertIsInstance(self.data["temperatures"], list)
        self.assertTrue(self.data["battery"] is None or is_percent(self.data["battery"]["percent"]))


class RealMachineTextTests(unittest.TestCase):
    def test_default_view_has_the_four_sections_and_no_placeholders(self) -> None:
        result = runner.invoke(app, ["host", "status"])

        self.assertEqual(result.exit_code, 0, result.output)
        for label in ("CPU", "Memory", "Storage", "GPU"):
            self.assertIn(label, result.stdout)
        self.assertNotIn("None", result.stdout)
        self.assertNotIn("null", result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_detail_view_exits_zero_and_keeps_the_four_sections(self) -> None:
        plain = runner.invoke(app, ["host", "status"])
        detailed = runner.invoke(app, ["host", "status", "--detail"])

        self.assertEqual(detailed.exit_code, 0, detailed.output)
        for label in ("CPU", "Memory", "Storage", "GPU"):
            self.assertIn(label, detailed.stdout)
        self.assertGreater(len(detailed.stdout), len(plain.stdout))
        self.assertNotIn("None", detailed.stdout)
        self.assertNotIn("null", detailed.stdout)


@unittest.skipIf(pty is None, "needs a POSIX pseudo-terminal")
class RealTerminalTests(unittest.TestCase):
    """Colour and live redraw only apply on a terminal, so use a pty."""

    CODE = "import sys; from jzr_tools.cli import app; app(sys.argv[1:], prog_name='jzr')"

    def spawn(
        self,
        args: list[str],
        extra_env: dict[str, str] | None = None,
        size: tuple[int, int] | None = None,
    ):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_PATH)
        env.pop("NO_COLOR", None)
        env["TERM"] = "xterm"
        env.update(extra_env or {})
        master, slave = pty.openpty()
        if size:  # (rows, columns)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
        proc = subprocess.Popen(
            [sys.executable, "-c", self.CODE, *args],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        self.addCleanup(self._reap, proc, master)
        return proc, master

    @staticmethod
    def _reap(proc: subprocess.Popen, master: int) -> None:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.close(master)
        except OSError:
            pass

    @staticmethod
    def read_until(master: int, proc: subprocess.Popen, stop, timeout: float = 30.0) -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
                if stop(b"".join(chunks).decode("utf-8", "replace")):
                    break
            elif proc.poll() is not None:
                break
        return b"".join(chunks).decode("utf-8", "replace")

    def test_terminal_output_is_coloured(self) -> None:
        proc, master = self.spawn(["host", "status"])
        out = self.read_until(master, proc, lambda text: False)
        proc.wait(timeout=30)

        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("\x1b[", out)

    def test_no_color_disables_colour_on_a_terminal(self) -> None:
        proc, master = self.spawn(["host", "status"], {"NO_COLOR": "1"})
        out = self.read_until(master, proc, lambda text: False)
        proc.wait(timeout=30)

        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("Memory", out)
        self.assertNotIn("\x1b", out)

    def test_live_on_a_terminal_redraws_in_place_and_restores_the_cursor(self) -> None:
        proc, master = self.spawn(["host", "status", "--live", "-n", "0.3"])
        seen = self.read_until(master, proc, lambda text: text.count("\x1b[H") >= 2)
        proc.send_signal(signal.SIGINT)
        seen += self.read_until(master, proc, lambda text: False, timeout=10)
        proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0, seen)
        self.assertGreaterEqual(seen.count("\x1b[H"), 2, seen)
        self.assertIn("\x1b[J", seen)
        self.assertIn("\x1b[?25l", seen)
        self.assertTrue(seen.rstrip().endswith("\x1b[?25h") or "\x1b[?25h" in seen, seen)
        self.assertNotIn("Traceback", seen)
        self.assertNotIn("Aborted", seen)

    def frame_lines(self, out: str) -> list[str]:
        return [ln for ln in strip_ansi(out).replace("\r", "").split("\n") if ln.strip()]

    def run_sized(self, columns: int, env: dict[str, str], args=("host", "status")):
        full_env = {"PYTHONIOENCODING": "utf-8", **env}
        proc, master = self.spawn(list(args), full_env, size=(40, columns))
        out = self.read_until(master, proc, lambda text: False)
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        return out

    def test_narrow_terminal_gets_a_frame_never_wider_than_the_terminal(self) -> None:
        for env in ({}, {"NO_COLOR": "1"}):
            for args in (("host", "status"), ("host", "status", "--detail")):
                with self.subTest(env=env, args=args):
                    lines = self.frame_lines(self.run_sized(50, env, args))

                    self.assertTrue(lines)
                    self.assertTrue(lines[0].startswith("╭"), lines[0])
                    self.assertTrue(lines[-1].startswith("╰"), lines[-1])
                    self.assertTrue(all(display_width(ln) <= 50 for ln in lines), lines)
                    self.assertEqual({display_width(ln) for ln in lines}, {display_width(lines[0])})
                    self.assertTrue(any(ln.rstrip("│ ").endswith("…") for ln in lines), lines)

    def test_wide_terminal_cuts_nothing(self) -> None:
        lines = self.frame_lines(self.run_sized(400, {}, ("host", "status", "--detail")))

        self.assertTrue(lines[0].startswith("╭"))
        self.assertFalse(any("…" in ln for ln in lines), lines)
        self.assertEqual(len({display_width(ln) for ln in lines}), 1)

    def test_terminal_with_an_ascii_only_encoding_gets_the_ascii_frame(self) -> None:
        out = self.run_sized(50, {"PYTHONIOENCODING": "ascii"})
        lines = self.frame_lines(out)

        self.assertTrue(lines[0].startswith("+-"), lines[0])
        self.assertTrue(lines[-1].startswith("+-"), lines[-1])
        self.assertTrue(all(len(ln) <= 50 for ln in lines), lines)
        self.assertEqual(len({len(ln) for ln in lines}), 1)
        self.assertTrue(any(ln.rstrip("| ").endswith(">") for ln in lines), lines)

    def test_live_report_is_cut_to_the_terminal_height(self) -> None:
        rows = 12
        proc, master = self.spawn(
            ["host", "status", "--live", "-n", "0.3"],
            {"PYTHONIOENCODING": "utf-8"},
            size=(rows, 100),
        )
        seen = self.read_until(master, proc, lambda text: text.count("\x1b[H") >= 2)
        proc.send_signal(signal.SIGINT)
        seen += self.read_until(master, proc, lambda text: False, timeout=10)
        proc.wait(timeout=10)

        frame = seen.split("\x1b[H")[1]
        lines = self.frame_lines(frame)
        self.assertGreaterEqual(seen.count("\x1b[H"), 2, seen)
        self.assertLessEqual(len(lines), rows, lines)
        self.assertTrue(all(display_width(ln) <= 100 for ln in lines))


if __name__ == "__main__":
    unittest.main()

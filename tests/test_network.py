"""Port selection, failure handling, and real process termination tests."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import psutil
from typer.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.network import KillPortResult, kill_port
from jzr_tools.core.windows_network import get_port_exclusions


def connection(port: int, pid: int | None = 101, **kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        laddr=SimpleNamespace(port=port), pid=pid,
        status=kwargs.pop("status", psutil.CONN_LISTEN), **kwargs,
    )


class KillPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.net_connections", return_value=[]
        ))
        self.process = self.enterContext(mock.patch("jzr_tools.core.network.psutil.Process"))
        self.wait = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.wait_procs",
            side_effect=lambda processes, timeout: (processes, []),
        ))

    def fake_process(self, pid: int, port: int = 3000) -> mock.Mock:
        process = mock.Mock(pid=pid)
        process.net_connections.return_value = [connection(port, pid)]
        return process

    def test_exact_local_port_and_unique_owners_across_socket_types(self) -> None:
        self.connections.return_value = [
            connection(3000, 101, family=socket.AF_INET, type=socket.SOCK_STREAM),
            connection(3000, 101, family=socket.AF_INET6, type=socket.SOCK_STREAM),
            connection(3000, 102, type=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            connection(30001, 103),
            connection(50000, 104, raddr=SimpleNamespace(port=3000)),
            SimpleNamespace(laddr=(), pid=105),
        ]
        processes = {pid: self.fake_process(pid) for pid in (101, 102)}
        self.process.side_effect = processes.__getitem__

        result = kill_port(3000)

        self.assertEqual(result.killed, [101, 102])
        self.assertEqual(result.errors, [])
        self.assertEqual(self.process.call_args_list, [mock.call(101), mock.call(102)])
        for process in processes.values():
            process.kill.assert_called_once()

    def test_permission_failure_does_not_prevent_other_kills(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(3000, 102)]
        denied, allowed = self.fake_process(101), self.fake_process(102)
        denied.kill.side_effect = psutil.AccessDenied(101)
        self.process.side_effect = [denied, allowed]

        result = kill_port(3000)

        self.assertEqual(result.killed, [102])
        self.assertIn("Access denied for PID 101", result.errors[0])

    def test_disappeared_or_changed_owner_is_not_killed(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(3000, 102)]
        changed = self.fake_process(102, port=4000)
        self.process.side_effect = [psutil.NoSuchProcess(101), changed]

        self.assertEqual(kill_port(3000), KillPortResult())
        changed.kill.assert_not_called()

    def test_unknown_owner_is_reported_but_time_wait_is_ignored(self) -> None:
        self.connections.return_value = [connection(3000, None, status=psutil.CONN_TIME_WAIT)]
        self.assertEqual(kill_port(3000), KillPortResult())
        self.connections.return_value.append(connection(3000, None))
        self.assertIn("Cannot identify an owner", kill_port(3000).errors[0])
        self.process.assert_not_called()

    def test_reserved_and_current_pid_are_not_killed(self) -> None:
        self.connections.return_value = [connection(3000, 0), connection(3000, os.getpid())]
        self.assertEqual(len(kill_port(3000).errors), 2)
        self.process.assert_not_called()

    def test_inspection_denied_has_actionable_error(self) -> None:
        self.connections.side_effect = psutil.AccessDenied()
        with self.assertRaisesRegex(RuntimeError, "Administrator.*sudo"):
            kill_port(3000)
        self.process.assert_not_called()

    def test_exit_timeout_is_not_reported_as_success(self) -> None:
        self.connections.return_value = [connection(3000)]
        process = self.fake_process(101)
        self.process.return_value = process
        self.wait.side_effect = lambda processes, timeout: ([], processes)
        result = kill_port(3000)
        self.assertEqual(result.killed, [])
        self.assertIn("has not exited", result.errors[0])

    def test_core_rejects_invalid_ports_before_inspection(self) -> None:
        for port in (0, -1, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                kill_port(port)
        self.connections.assert_not_called()

    def test_unix_zombie_has_exited_even_before_parent_reaps_it(self) -> None:
        self.connections.return_value = [connection(3000)]
        process = self.fake_process(101)
        process.status.return_value = psutil.STATUS_ZOMBIE
        self.process.return_value = process
        self.wait.side_effect = lambda processes, timeout: ([], processes)
        self.assertEqual(kill_port(3000), KillPortResult(killed=[101]))


class KillPortCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("jzr_tools.commands.network.get_port_exclusions", return_value=[]))

    def test_help_and_argument_validation(self) -> None:
        runner = CliRunner()
        self.assertIn("killport", runner.invoke(app, ["--help"]).stdout)
        with mock.patch("jzr_tools.commands.network.kill_port") as kill:
            for arguments in ([], ["0"], ["65536"], ["abc"], ["--", "-1"]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(runner.invoke(app, ["killport", *arguments]).exit_code, 2)
            kill.assert_not_called()

    def test_empty_port_is_successful(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", return_value=KillPortResult()) as kill:
            result = CliRunner().invoke(app, ["killport", "3000"])
        kill.assert_called_once_with(3000)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No visible processes", result.stdout)

    def test_partial_failure_reports_killed_pid_and_returns_error(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", return_value=KillPortResult(
            killed=[101], errors=["Access denied for PID 102."]
        )):
            result = CliRunner().invoke(app, ["killport", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 101", result.stdout)
        self.assertIn("Access denied for PID 102", result.stderr)

    def test_inspection_failure_returns_clean_error(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", side_effect=RuntimeError("Cannot inspect")):
            result = CliRunner().invoke(app, ["killport", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect", result.stderr)


class KillPortIntegrationTests(unittest.TestCase):
    def test_kills_test_owned_tcp_and_udp_processes_and_releases_port(self) -> None:
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        child_code = """
import os, pathlib, socket, sys, time
sock = socket.socket(socket.AF_INET, int(sys.argv[2]))
sock.bind(('127.0.0.1', 0))
if sock.type == socket.SOCK_STREAM:
    sock.listen()
pathlib.Path(sys.argv[1]).write_text(f'{sock.getsockname()[1]}:{os.getpid()}')
time.sleep(60)
"""
        for socket_type in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with self.subTest(socket_type=socket_type), tempfile.TemporaryDirectory() as tmp:
                ready = Path(tmp) / "port.txt"
                child = subprocess.Popen(
                    [sys.executable, "-c", child_code, str(ready), str(int(socket_type))],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                try:
                    deadline = time.monotonic() + 5
                    port_text = ""
                    while time.monotonic() < deadline:
                        if ready.exists():
                            port_text = ready.read_text()
                        if port_text or child.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertTrue(port_text, "Test socket did not become ready")
                    port, socket_pid = map(int, port_text.split(":"))
                    # A TCP-assigned port may independently be excluded for UDP.
                    exclusions = get_port_exclusions(port)
                    result = subprocess.run(
                        [sys.executable, "-m", "jzr_tools", "killport", str(port)],
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, 1 if exclusions else 0, result.stdout + result.stderr)
                    if exclusions:
                        self.assertIn(f"Port {port} is excluded for", result.stderr)
                    # Windows virtualenv launchers can create a second process.
                    self.assertIn(f"Killed PID {socket_pid}", result.stdout)
                    child.wait(timeout=5)
                    with socket.socket(socket.AF_INET, socket_type) as probe:
                        probe.bind(("127.0.0.1", port))
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

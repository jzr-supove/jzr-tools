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

import click
import psutil
from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import network as core_network
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


class KillPortViaPortGroupTests(unittest.TestCase):
    """`jzr port kill` is the same command as `jzr killport`."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("jzr_tools.commands.network.get_port_exclusions", return_value=[]))

    def test_port_kill_reports_killed_pids(self) -> None:
        with mock.patch(
            "jzr_tools.commands.network.kill_port", return_value=KillPortResult(killed=[101, 102])
        ) as kill:
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        kill.assert_called_once_with(3000)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Killed PID 101 using port 3000.", result.stdout)
        self.assertIn("Killed PID 102 using port 3000.", result.stdout)

    def test_port_kill_with_nothing_found_is_successful(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", return_value=KillPortResult()):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No visible processes using local port 3000.", result.stdout)

    def test_port_kill_partial_failure_exits_one(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", return_value=KillPortResult(
            killed=[101], errors=["Access denied for PID 102."]
        )):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 101", result.stdout)
        self.assertIn("Access denied for PID 102", result.stderr)

    def test_port_kill_inspection_failure_exits_one(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port", side_effect=RuntimeError("Cannot inspect")):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect", result.stderr)

    def test_port_kill_rejects_out_of_range_and_non_numeric_ports(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_port") as kill:
            for arguments in ([], ["0"], ["65536"], ["abc"]):
                with self.subTest(arguments=arguments):
                    result = CliRunner().invoke(app, ["port", "kill", *arguments])
                    self.assertEqual(result.exit_code, 2)
                    self.assertNotIn("No such command", result.stderr)
            kill.assert_not_called()

    def test_port_kill_restart_hns_is_refused_off_windows(self) -> None:
        with (
            mock.patch("jzr_tools.commands.network.is_windows", return_value=False),
            mock.patch("jzr_tools.commands.network.kill_port") as kill,
        ):
            result = CliRunner().invoke(app, ["port", "kill", "3000", "--restart-hns"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("only available on Windows", result.stderr)
        kill.assert_not_called()

    def test_killport_is_registered_as_port_kill(self) -> None:
        from jzr_tools.commands import network

        self.assertIs(network.app.get_command(click.Context(network.app), "kill"), network.killport)


def owner_connection(
    port: int,
    pid: int | None = 4321,
    ip: str = "0.0.0.0",
    type_: int = socket.SOCK_STREAM,
    status: str = psutil.CONN_LISTEN,
) -> SimpleNamespace:
    return SimpleNamespace(
        laddr=SimpleNamespace(ip=ip, port=port), pid=pid, type=type_, status=status,
    )


class ListPortOwnersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.net_connections", return_value=[]
        ))
        self.process = self.enterContext(mock.patch("jzr_tools.core.network.psutil.Process"))
        self.names = {4321: "node.exe", 77: "dns"}
        self.process.side_effect = lambda pid: mock.Mock(
            **{"name.return_value": self.names[pid]}
        )

    def test_reports_fields_of_a_listening_tcp_owner(self) -> None:
        self.connections.return_value = [owner_connection(3000)]

        self.assertEqual(
            core_network.list_port_owners(3000),
            [core_network.PortOwner(pid=4321, name="node.exe", protocol="tcp", address="0.0.0.0", port=3000, status=psutil.CONN_LISTEN)],
        )
        self.connections.assert_called_once_with(kind="inet")

    def test_protocol_comes_from_socket_type(self) -> None:
        self.connections.return_value = [
            owner_connection(53, 77, type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            owner_connection(53, 77, type_=socket.SOCK_STREAM),
        ]

        owners = core_network.list_port_owners(53)

        self.assertEqual([owner.protocol for owner in owners], ["tcp", "udp"])
        self.assertEqual(owners[1].status, psutil.CONN_NONE)

    def test_only_exact_local_port_is_listed_and_time_wait_is_skipped(self) -> None:
        self.connections.return_value = [
            owner_connection(3000),
            owner_connection(30001),
            owner_connection(3000, 77, status=psutil.CONN_TIME_WAIT),
            SimpleNamespace(laddr=(), pid=77, type=socket.SOCK_STREAM, status=psutil.CONN_LISTEN),
            SimpleNamespace(
                laddr=SimpleNamespace(ip="127.0.0.1", port=50000), pid=77,
                raddr=SimpleNamespace(ip="127.0.0.1", port=3000),
                type=socket.SOCK_STREAM, status=psutil.CONN_ESTABLISHED,
            ),
        ]

        owners = core_network.list_port_owners(3000)

        self.assertEqual([owner.pid for owner in owners], [4321])

    def test_unresolvable_process_name_falls_back_to_question_mark(self) -> None:
        for error in (psutil.NoSuchProcess(4321), psutil.AccessDenied(4321)):
            with self.subTest(error=type(error).__name__):
                self.process.side_effect = error
                self.connections.return_value = [owner_connection(3000)]
                owners = core_network.list_port_owners(3000)
                self.assertEqual([(o.pid, o.name) for o in owners], [(4321, "?")])

    def test_unknown_pid_is_listed_without_looking_up_a_process(self) -> None:
        self.connections.return_value = [owner_connection(3000, None)]

        owners = core_network.list_port_owners(3000)

        self.assertEqual([(o.pid, o.name) for o in owners], [(None, "?")])
        self.process.assert_not_called()

    def test_exact_duplicates_are_dropped_but_distinct_addresses_are_kept(self) -> None:
        self.connections.return_value = [
            owner_connection(3000),
            owner_connection(3000),
            owner_connection(3000, ip="::"),
        ]

        owners = core_network.list_port_owners(3000)

        self.assertEqual(sorted(owner.address for owner in owners), ["0.0.0.0", "::"])

    def test_results_are_sorted_by_pid_protocol_then_address(self) -> None:
        self.connections.return_value = [
            owner_connection(53, 4321, ip="::"),
            owner_connection(53, 4321, ip="0.0.0.0", type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            owner_connection(53, 77, ip="127.0.0.1"),
            owner_connection(53, None, ip="10.0.0.1"),
            owner_connection(53, 4321, ip="0.0.0.0"),
        ]

        owners = core_network.list_port_owners(53)

        self.assertEqual(
            [(o.pid, o.protocol, o.address) for o in owners],
            [
                (None, "tcp", "10.0.0.1"),
                (77, "tcp", "127.0.0.1"),
                (4321, "tcp", "0.0.0.0"),
                (4321, "tcp", "::"),
                (4321, "udp", "0.0.0.0"),
            ],
        )

    def test_empty_when_nothing_uses_the_port(self) -> None:
        self.connections.return_value = [owner_connection(4000)]
        self.assertEqual(core_network.list_port_owners(3000), [])

    def test_invalid_ports_are_rejected_before_inspection(self) -> None:
        for port in (0, -1, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                core_network.list_port_owners(port)
        self.connections.assert_not_called()

    def test_inspection_errors_become_runtime_errors(self) -> None:
        for error in (psutil.AccessDenied(), PermissionError(), OSError("boom")):
            with self.subTest(error=type(error).__name__):
                self.connections.side_effect = error
                with self.assertRaises(RuntimeError):
                    core_network.list_port_owners(3000)

    def test_permission_failure_mentions_how_to_elevate(self) -> None:
        self.connections.side_effect = psutil.AccessDenied()
        with self.assertRaisesRegex(RuntimeError, "Administrator.*sudo"):
            core_network.list_port_owners(3000)

    def test_never_kills_anything(self) -> None:
        processes = []

        def make(pid: int) -> mock.Mock:
            process = mock.Mock()
            process.name.return_value = "node.exe"
            processes.append(process)
            return process

        self.process.side_effect = make
        self.connections.return_value = [owner_connection(3000)]

        core_network.list_port_owners(3000)

        self.assertTrue(processes)
        for process in processes:
            process.kill.assert_not_called()
            process.terminate.assert_not_called()


class PortShowCliTests(unittest.TestCase):
    def invoke(self, owners: list[core_network.PortOwner] | Exception, *args: str):  # noqa: ANN202
        kwargs = {"side_effect": owners} if isinstance(owners, Exception) else {"return_value": owners}
        with (
            mock.patch("jzr_tools.commands.network.list_port_owners", **kwargs) as listing,
            mock.patch("jzr_tools.commands.network.kill_port") as kill,
        ):
            result = CliRunner().invoke(app, ["port", "show", *args])
        kill.assert_not_called()
        return result, listing

    def test_listening_tcp_owner_line(self) -> None:
        result, listing = self.invoke(
            [core_network.PortOwner(4321, "node.exe", "tcp", "0.0.0.0", 3000, "LISTEN")], "3000"
        )

        listing.assert_called_once_with(3000)
        self.assertEqual(result.exit_code, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, lines)
        for piece in ("PID 4321", "node.exe", "TCP", "0.0.0.0:3000", "LISTEN"):
            self.assertIn(piece, lines[0])
        self.assertNotIn("tcp", lines[0])

    def test_ipv6_address_is_bracketed(self) -> None:
        result, _ = self.invoke([core_network.PortOwner(4321, "node.exe", "tcp", "::", 3000, "LISTEN")], "3000")

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[::]:3000", result.stdout)

    def test_udp_owner_omits_none_status(self) -> None:
        result, _ = self.invoke([core_network.PortOwner(77, "dns", "udp", "0.0.0.0", 53, "NONE")], "53")

        self.assertEqual(result.exit_code, 0)
        self.assertIn("UDP", result.stdout)
        self.assertIn("0.0.0.0:53", result.stdout)
        self.assertNotIn("NONE", result.stdout)

    def test_one_line_per_owner(self) -> None:
        result, _ = self.invoke(
            [
                core_network.PortOwner(77, "dns", "tcp", "127.0.0.1", 53, "LISTEN"),
                core_network.PortOwner(4321, "node.exe", "udp", "0.0.0.0", 53, "NONE"),
            ],
            "53",
        )

        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("dns", lines[0])
        self.assertIn("node.exe", lines[1])

    def test_unknown_pid_does_not_crash(self) -> None:
        result, _ = self.invoke([core_network.PortOwner(None, "?", "tcp", "0.0.0.0", 3000, "LISTEN")], "3000")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0.0.0.0:3000", result.stdout)

    def test_nothing_using_port_is_successful(self) -> None:
        result, _ = self.invoke([], "3000")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "No visible processes using local port 3000.")

    def test_inspection_failure_goes_to_stderr_with_exit_one(self) -> None:
        result, _ = self.invoke(RuntimeError("Cannot inspect network ports."), "3000")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect network ports.", result.stderr)
        self.assertNotIn("Cannot inspect", result.stdout)

    def test_port_argument_validation(self) -> None:
        with mock.patch("jzr_tools.commands.network.list_port_owners") as listing:
            for arguments in ([], ["0"], ["65536"], ["abc"]):
                with self.subTest(arguments=arguments):
                    result = CliRunner().invoke(app, ["port", "show", *arguments])
                    self.assertEqual(result.exit_code, 2)
                    self.assertNotIn("No such command", result.stderr)
            listing.assert_not_called()

    def test_show_and_kill_are_the_port_subcommands(self) -> None:
        result = CliRunner().invoke(app, ["port", "-h"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("show", result.stdout)
        self.assertIn("kill", result.stdout)


class PortShowIntegrationTests(unittest.TestCase):
    def test_finds_a_socket_owned_by_this_process_without_killing_it(self) -> None:
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]

            owners = core_network.list_port_owners(port)
            result = CliRunner().invoke(app, ["port", "show", str(port)])

        mine = [owner for owner in owners if owner.pid == os.getpid()]
        self.assertEqual(len(mine), 1, owners)
        self.assertEqual(mine[0].protocol, "tcp")
        self.assertEqual(mine[0].address, "127.0.0.1")
        self.assertEqual(mine[0].port, port)
        self.assertEqual(mine[0].name, psutil.Process().name())
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"PID {os.getpid()}", result.stdout)
        self.assertIn(f"127.0.0.1:{port}", result.stdout)


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

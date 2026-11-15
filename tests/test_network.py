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
from jzr_tools.core.windows_network import PortExclusion, get_port_exclusions


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
        with mock.patch("jzr_tools.commands.network.kill_ports") as kill:
            for arguments in ([], ["0"], ["65536"], ["abc"], ["--", "-1"]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(runner.invoke(app, ["killport", *arguments]).exit_code, 2)
            kill.assert_not_called()

    def test_empty_port_is_successful(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", return_value={3000: KillPortResult()}) as kill:
            result = CliRunner().invoke(app, ["killport", "3000"])
        kill.assert_called_once()
        self.assertEqual(list(kill.call_args.args[0]), [3000])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No visible processes", result.stdout)

    def test_partial_failure_reports_killed_pid_and_returns_error(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", return_value={3000: KillPortResult(
            killed=[101], errors=["Access denied for PID 102."]
        )}):
            result = CliRunner().invoke(app, ["killport", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 101", result.stdout)
        self.assertIn("Access denied for PID 102", result.stderr)

    def test_inspection_failure_returns_clean_error(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", side_effect=RuntimeError("Cannot inspect")):
            result = CliRunner().invoke(app, ["killport", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect", result.stderr)


class KillPortViaPortGroupTests(unittest.TestCase):
    """`jzr port kill` is the same command as `jzr killport`."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("jzr_tools.commands.network.get_port_exclusions", return_value=[]))

    def test_port_kill_reports_killed_pids(self) -> None:
        with mock.patch(
            "jzr_tools.commands.network.kill_ports", return_value={3000: KillPortResult(killed=[101, 102])}
        ) as kill:
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        kill.assert_called_once()
        self.assertEqual(list(kill.call_args.args[0]), [3000])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Killed PID 101 using port 3000.", result.stdout)
        self.assertIn("Killed PID 102 using port 3000.", result.stdout)

    def test_port_kill_with_nothing_found_is_successful(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", return_value={3000: KillPortResult()}):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No visible processes using local port 3000.", result.stdout)

    def test_port_kill_partial_failure_exits_one(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", return_value={3000: KillPortResult(
            killed=[101], errors=["Access denied for PID 102."]
        )}):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 101", result.stdout)
        self.assertIn("Access denied for PID 102", result.stderr)

    def test_port_kill_inspection_failure_exits_one(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports", side_effect=RuntimeError("Cannot inspect")):
            result = CliRunner().invoke(app, ["port", "kill", "3000"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect", result.stderr)

    def test_port_kill_rejects_out_of_range_and_non_numeric_ports(self) -> None:
        with mock.patch("jzr_tools.commands.network.kill_ports") as kill:
            for arguments in ([], ["0"], ["65536"], ["abc"]):
                with self.subTest(arguments=arguments):
                    result = CliRunner().invoke(app, ["port", "kill", *arguments])
                    self.assertEqual(result.exit_code, 2)
                    self.assertNotIn("No such command", result.stderr)
            kill.assert_not_called()

    def test_port_kill_restart_hns_is_refused_off_windows(self) -> None:
        with (
            mock.patch("jzr_tools.commands.network.is_windows", return_value=False),
            mock.patch("jzr_tools.commands.network.kill_ports") as kill,
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
            mock.patch("jzr_tools.commands.network.kill_ports") as kill,
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
            for arguments in (["0"], ["65536"], ["abc"], ["3000", "0"], ["--", "-1"]):
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


class ListListeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.net_connections", return_value=[]
        ))
        self.process = self.enterContext(mock.patch("jzr_tools.core.network.psutil.Process"))
        self.names = {4321: "node.exe", 77: "dns", 500: "svc"}
        self.process.side_effect = lambda pid: mock.Mock(**{"name.return_value": self.names[pid]})

    def test_lists_only_listening_tcp_sockets_by_default(self) -> None:
        self.connections.return_value = [
            owner_connection(3000),
            owner_connection(3001, 77, status=psutil.CONN_ESTABLISHED),
            owner_connection(3002, 77, status=psutil.CONN_TIME_WAIT),
            owner_connection(53, 77, type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            SimpleNamespace(laddr=(), pid=77, type=socket.SOCK_STREAM, status=psutil.CONN_LISTEN),
        ]

        owners = core_network.list_listening()

        self.assertEqual(
            owners,
            [core_network.PortOwner(4321, "node.exe", "tcp", "0.0.0.0", 3000, psutil.CONN_LISTEN)],
        )
        self.connections.assert_called_once_with(kind="inet")

    def test_udp_option_adds_every_udp_socket_with_a_local_address(self) -> None:
        self.connections.return_value = [
            owner_connection(3000),
            owner_connection(53, 77, type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            owner_connection(5353, 500, ip="::", type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            SimpleNamespace(laddr=(), pid=77, type=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            owner_connection(3001, 77, status=psutil.CONN_ESTABLISHED),
            owner_connection(3002, 77, status=psutil.CONN_TIME_WAIT),
        ]

        owners = core_network.list_listening(udp=True)

        self.assertEqual(
            [(o.port, o.protocol, o.pid, o.address) for o in owners],
            [(53, "udp", 77, "0.0.0.0"), (3000, "tcp", 4321, "0.0.0.0"), (5353, "udp", 500, "::")],
        )
        self.assertEqual(owners[0].status, psutil.CONN_NONE)

    def test_results_are_deduplicated_and_sorted_by_port_protocol_pid_address(self) -> None:
        self.connections.return_value = [
            owner_connection(8080, 4321),
            owner_connection(53, 4321, ip="::"),
            owner_connection(53, 4321, ip="0.0.0.0", type_=socket.SOCK_DGRAM, status=psutil.CONN_NONE),
            owner_connection(53, 77, ip="127.0.0.1"),
            owner_connection(53, None, ip="10.0.0.1"),
            owner_connection(53, 4321, ip="0.0.0.0"),
            owner_connection(53, 4321, ip="0.0.0.0"),
            owner_connection(22, 500),
        ]

        owners = core_network.list_listening(udp=True)

        self.assertEqual(
            [(o.port, o.protocol, o.pid, o.address) for o in owners],
            [
                (22, "tcp", 500, "0.0.0.0"),
                (53, "tcp", None, "10.0.0.1"),
                (53, "tcp", 77, "127.0.0.1"),
                (53, "tcp", 4321, "0.0.0.0"),
                (53, "tcp", 4321, "::"),
                (53, "udp", 4321, "0.0.0.0"),
                (8080, "tcp", 4321, "0.0.0.0"),
            ],
        )

    def test_unknown_pid_is_listed_without_looking_up_a_process(self) -> None:
        self.connections.return_value = [owner_connection(3000, None)]

        owners = core_network.list_listening()

        self.assertEqual([(o.pid, o.name) for o in owners], [(None, "?")])
        self.process.assert_not_called()

    def test_unresolvable_process_name_falls_back_to_question_mark(self) -> None:
        self.process.side_effect = psutil.AccessDenied(4321)
        self.connections.return_value = [owner_connection(3000)]

        self.assertEqual([(o.pid, o.name) for o in core_network.list_listening()], [(4321, "?")])

    def test_nothing_listening_gives_empty_list(self) -> None:
        self.connections.return_value = [owner_connection(3001, 77, status=psutil.CONN_ESTABLISHED)]
        self.assertEqual(core_network.list_listening(), [])

    def test_inspection_errors_become_runtime_errors_with_elevation_hint(self) -> None:
        self.connections.side_effect = psutil.AccessDenied()
        with self.assertRaisesRegex(RuntimeError, "Administrator.*sudo"):
            core_network.list_listening()
        for error in (PermissionError(), OSError("boom")):
            with self.subTest(error=type(error).__name__):
                self.connections.side_effect = error
                with self.assertRaises(RuntimeError):
                    core_network.list_listening(udp=True)

    def test_never_kills_anything(self) -> None:
        processes = []

        def make(pid: int) -> mock.Mock:
            process = mock.Mock()
            process.name.return_value = "node.exe"
            processes.append(process)
            return process

        self.process.side_effect = make
        self.connections.return_value = [owner_connection(3000)]

        core_network.list_listening(udp=True)

        self.assertTrue(processes)
        for process in processes:
            process.kill.assert_not_called()
            process.terminate.assert_not_called()


class KillPortsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.net_connections", return_value=[]
        ))
        self.process = self.enterContext(mock.patch("jzr_tools.core.network.psutil.Process"))
        self.wait = self.enterContext(mock.patch(
            "jzr_tools.core.network.psutil.wait_procs",
            side_effect=lambda processes, timeout: (processes, []),
        ))

    def fake_process(self, pid: int, *ports: int) -> mock.Mock:
        process = mock.Mock(pid=pid)
        process.net_connections.return_value = [connection(port, pid) for port in ports]
        return process

    def test_results_follow_given_order_with_duplicates_once(self) -> None:
        results = core_network.kill_ports([8080, 3000, 8080, 5173])

        self.assertEqual(list(results), [8080, 3000, 5173])
        for result in results.values():
            self.assertEqual(result, KillPortResult())

    def test_one_snapshot_serves_all_ports(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(8080, 102)]
        processes = {101: self.fake_process(101, 3000), 102: self.fake_process(102, 8080)}
        self.process.side_effect = processes.__getitem__

        results = core_network.kill_ports([3000, 8080])

        self.connections.assert_called_once()
        self.assertEqual(results[3000].killed, [101])
        self.assertEqual(results[8080].killed, [102])
        self.assertEqual(results[3000].errors, [])
        self.assertEqual(results[8080].errors, [])

    def test_pid_holding_several_ports_is_killed_once_and_reported_for_each(self) -> None:
        self.connections.return_value = [
            connection(3000, 101), connection(8080, 101), connection(9000, 101),
        ]
        shared = self.fake_process(101, 3000, 8080, 9000)
        self.process.side_effect = lambda pid: shared

        results = core_network.kill_ports([3000, 8080])

        shared.kill.assert_called_once()
        self.assertEqual(results[3000].killed, [101])
        self.assertEqual(results[8080].killed, [101])

    def test_ports_that_were_not_listed_are_not_affected(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(9000, 102)]
        processes = {101: self.fake_process(101, 3000)}
        self.process.side_effect = processes.__getitem__

        results = core_network.kill_ports([3000])

        self.assertEqual(list(results), [3000])
        self.assertEqual(results[3000].killed, [101])
        self.assertEqual(self.process.call_args_list, [mock.call(101)])

    def test_recheck_accepts_any_of_the_listed_ports(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(8080, 101)]
        process = self.fake_process(101, 8080)  # has since released 3000
        self.process.return_value = process

        results = core_network.kill_ports([3000, 8080])

        process.kill.assert_called_once()
        self.assertEqual(results[3000].killed, [101])
        self.assertEqual(results[8080].killed, [101])

    def test_owner_that_no_longer_holds_any_listed_port_is_not_killed(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(8080, 101)]
        process = self.fake_process(101, 4000)
        self.process.return_value = process

        results = core_network.kill_ports([3000, 8080])

        process.kill.assert_not_called()
        self.assertEqual(results, {3000: KillPortResult(), 8080: KillPortResult()})

    def test_access_denied_is_attached_to_every_port_the_pid_held(self) -> None:
        self.connections.return_value = [
            connection(3000, 101), connection(8080, 101), connection(5173, 102),
        ]
        denied = self.fake_process(101, 3000, 8080)
        denied.kill.side_effect = psutil.AccessDenied(101)
        allowed = self.fake_process(102, 5173)
        processes = {101: denied, 102: allowed}
        self.process.side_effect = processes.__getitem__

        results = core_network.kill_ports([3000, 8080, 5173])

        for port in (3000, 8080):
            self.assertEqual(results[port].killed, [])
            self.assertEqual(len(results[port].errors), 1)
            self.assertIn("Access denied for PID 101", results[port].errors[0])
        self.assertEqual(results[5173], KillPortResult(killed=[102]))

    def test_unknown_owner_error_belongs_only_to_its_port(self) -> None:
        self.connections.return_value = [connection(3000, None), connection(8080, 102)]
        self.process.return_value = self.fake_process(102, 8080)

        results = core_network.kill_ports([3000, 8080])

        self.assertEqual(len(results[3000].errors), 1)
        self.assertIn("Cannot identify an owner of port 3000", results[3000].errors[0])
        self.assertEqual(results[8080], KillPortResult(killed=[102]))

    def test_time_wait_sockets_are_ignored(self) -> None:
        self.connections.return_value = [
            connection(3000, None, status=psutil.CONN_TIME_WAIT),
            connection(8080, 101, status=psutil.CONN_TIME_WAIT),
        ]

        results = core_network.kill_ports([3000, 8080])

        self.assertEqual(results, {3000: KillPortResult(), 8080: KillPortResult()})
        self.process.assert_not_called()

    def test_process_that_does_not_exit_is_an_error_for_each_port_it_held(self) -> None:
        self.connections.return_value = [connection(3000, 101), connection(8080, 101)]
        self.process.return_value = self.fake_process(101, 3000, 8080)
        self.wait.side_effect = lambda processes, timeout: ([], processes)

        results = core_network.kill_ports([3000, 8080])

        for port in (3000, 8080):
            self.assertEqual(results[port].killed, [])
            self.assertIn("has not exited", results[port].errors[0])

    def test_reserved_and_current_pid_are_refused(self) -> None:
        self.connections.return_value = [connection(3000, 0), connection(8080, os.getpid())]

        results = core_network.kill_ports([3000, 8080])

        self.assertEqual(len(results[3000].errors), 1)
        self.assertEqual(len(results[8080].errors), 1)
        self.process.assert_not_called()

    def test_invalid_port_is_rejected_before_inspection(self) -> None:
        for ports in ([0], [3000, 0], [65536, 3000], [-1]):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                core_network.kill_ports(ports)
        self.connections.assert_not_called()

    def test_inspection_denied_has_actionable_error(self) -> None:
        self.connections.side_effect = psutil.AccessDenied()
        with self.assertRaisesRegex(RuntimeError, "Administrator.*sudo"):
            core_network.kill_ports([3000, 8080])
        self.process.assert_not_called()


class PortShowAllCliTests(unittest.TestCase):
    OWNERS = [
        core_network.PortOwner(77, "dns", "tcp", "127.0.0.1", 53, "LISTEN"),
        core_network.PortOwner(77, "dns", "udp", "0.0.0.0", 53, "NONE"),
        core_network.PortOwner(4321, "node.exe", "tcp", "0.0.0.0", 3000, "LISTEN"),
        core_network.PortOwner(4321, "node.exe", "tcp", "::", 3000, "LISTEN"),
        core_network.PortOwner(None, "?", "tcp", "10.0.0.1", 8080, "LISTEN"),
    ]

    def invoke(self, owners, *args: str):  # noqa: ANN001, ANN202
        kwargs = {"side_effect": owners} if isinstance(owners, Exception) else {"return_value": owners}
        with (
            mock.patch("jzr_tools.commands.network.list_listening", **kwargs) as listing,
            mock.patch("jzr_tools.commands.network.list_port_owners") as by_port,
            mock.patch("jzr_tools.commands.network.kill_ports") as kill,
        ):
            result = CliRunner().invoke(app, ["port", "show", *args])
        by_port.assert_not_called()
        kill.assert_not_called()
        return result, listing

    def test_table_has_header_and_one_row_per_port_protocol_pid(self) -> None:
        result, listing = self.invoke(self.OWNERS)

        listing.assert_called_once_with(udp=False)
        self.assertEqual(result.exit_code, 0, result.output)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(lines[0].split(), ["PORT", "PROTO", "PID", "NAME", "ADDRESS"])
        rows = [line.split(None, 4) for line in lines[1:]]
        self.assertEqual(
            rows,
            [
                ["53", "TCP", "77", "dns", "127.0.0.1"],
                ["53", "UDP", "77", "dns", "0.0.0.0"],
                ["3000", "TCP", "4321", "node.exe", "0.0.0.0, [::]"],
                ["8080", "TCP", "?", "?", "10.0.0.1"],
            ],
        )

    def test_udp_option_is_passed_to_the_listing(self) -> None:
        result, listing = self.invoke(self.OWNERS, "--udp")

        listing.assert_called_once_with(udp=True)
        self.assertEqual(result.exit_code, 0, result.output)

    def test_nothing_listening_is_successful(self) -> None:
        result, _ = self.invoke([])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "No listening ports found.")

    def test_udp_option_together_with_ports_is_a_usage_error(self) -> None:
        with (
            mock.patch("jzr_tools.commands.network.list_listening") as listing,
            mock.patch("jzr_tools.commands.network.list_port_owners") as by_port,
        ):
            result = CliRunner().invoke(app, ["port", "show", "3000", "--udp"])
        self.assertEqual(result.exit_code, 2)
        listing.assert_not_called()
        by_port.assert_not_called()

    def test_inspection_failure_goes_to_stderr_with_exit_one(self) -> None:
        for args in ((), ("--json",)):
            with self.subTest(args=args):
                result, _ = self.invoke(RuntimeError("Cannot inspect network ports."), *args)
                self.assertEqual(result.exit_code, 1)
                self.assertIn("Cannot inspect network ports.", result.stderr)
                self.assertNotIn("Cannot inspect", result.stdout)
                self.assertNotIn("Traceback", result.output)

    def test_json_is_one_array_with_one_element_per_socket(self) -> None:
        import json

        result, listing = self.invoke(self.OWNERS, "--json")

        listing.assert_called_once_with(udp=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"port": 53, "protocol": "tcp", "address": "127.0.0.1", "pid": 77, "name": "dns", "status": "LISTEN"},
                {"port": 53, "protocol": "udp", "address": "0.0.0.0", "pid": 77, "name": "dns", "status": None},
                {"port": 3000, "protocol": "tcp", "address": "0.0.0.0", "pid": 4321, "name": "node.exe", "status": "LISTEN"},
                {"port": 3000, "protocol": "tcp", "address": "::", "pid": 4321, "name": "node.exe", "status": "LISTEN"},
                {"port": 8080, "protocol": "tcp", "address": "10.0.0.1", "pid": None, "name": "?", "status": "LISTEN"},
            ],
        )

    def test_json_with_udp_option_and_nothing_found(self) -> None:
        import json

        result, listing = self.invoke([], "--json", "--udp")

        listing.assert_called_once_with(udp=True)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout), [])


class PortShowSeveralPortsCliTests(unittest.TestCase):
    OWNERS = {
        3000: [core_network.PortOwner(4321, "node.exe", "tcp", "0.0.0.0", 3000, "LISTEN")],
        53: [core_network.PortOwner(77, "dns", "udp", "::", 53, "NONE")],
        8080: [],
    }

    def invoke(self, *args: str):  # noqa: ANN202
        with (
            mock.patch(
                "jzr_tools.commands.network.list_port_owners",
                side_effect=lambda port: self.OWNERS[port],
            ) as by_port,
            mock.patch("jzr_tools.commands.network.list_listening") as listing,
            mock.patch("jzr_tools.commands.network.kill_ports") as kill,
        ):
            result = CliRunner().invoke(app, ["port", "show", *args])
        listing.assert_not_called()
        kill.assert_not_called()
        return result, by_port

    def test_each_port_prints_its_lines_in_the_order_given(self) -> None:
        result, by_port = self.invoke("3000", "8080", "53")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(by_port.call_args_list, [mock.call(3000), mock.call(8080), mock.call(53)])
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3, lines)
        for piece in ("PID 4321", "node.exe", "TCP", "0.0.0.0:3000", "LISTEN"):
            self.assertIn(piece, lines[0])
        self.assertEqual(lines[1], "No visible processes using local port 8080.")
        for piece in ("PID 77", "dns", "UDP", "[::]:53"):
            self.assertIn(piece, lines[2])
        self.assertNotIn("NONE", lines[2])

    def test_duplicate_ports_are_inspected_and_printed_once(self) -> None:
        result, by_port = self.invoke("3000", "3000")

        self.assertEqual(by_port.call_args_list, [mock.call(3000)])
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_one_invalid_port_rejects_the_whole_command(self) -> None:
        for arguments in (["3000", "0"], ["3000", "65536"], ["3000", "abc"]):
            with self.subTest(arguments=arguments):
                result, by_port = self.invoke(*arguments)
                self.assertEqual(result.exit_code, 2)
                by_port.assert_not_called()

    def test_json_combines_all_ports_into_one_array(self) -> None:
        import json

        result, _ = self.invoke("3000", "8080", "53", "--json")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"port": 3000, "protocol": "tcp", "address": "0.0.0.0", "pid": 4321, "name": "node.exe", "status": "LISTEN"},
                {"port": 53, "protocol": "udp", "address": "::", "pid": 77, "name": "dns", "status": None},
            ],
        )

    def test_json_for_unused_port_is_an_empty_array(self) -> None:
        import json

        result, _ = self.invoke("8080", "--json")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout), [])

    def test_inspection_failure_goes_to_stderr_with_exit_one(self) -> None:
        for args in (("3000",), ("3000", "--json")):
            with self.subTest(args=args):
                with mock.patch(
                    "jzr_tools.commands.network.list_port_owners",
                    side_effect=RuntimeError("Cannot inspect network ports."),
                ):
                    result = CliRunner().invoke(app, ["port", "show", *args])
                self.assertEqual(result.exit_code, 1)
                self.assertIn("Cannot inspect network ports.", result.stderr)
                self.assertNotIn("Cannot inspect", result.stdout)


class KillSeveralPortsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("jzr_tools.commands.network.get_port_exclusions", return_value=[]))

    def invoke(self, results, *args: str, command: tuple[str, ...] = ("killport",)):  # noqa: ANN001, ANN202
        kwargs = {"side_effect": results} if isinstance(results, Exception) else {"return_value": results}
        with mock.patch("jzr_tools.commands.network.kill_ports", **kwargs) as kill:
            result = CliRunner().invoke(app, [*command, *args])
        return result, kill

    def test_all_ports_go_to_one_core_call_for_both_command_paths(self) -> None:
        for command in (("killport",), ("port", "kill")):
            with self.subTest(command=command):
                result, kill = self.invoke(
                    {3000: KillPortResult(), 8080: KillPortResult(), 5173: KillPortResult()},
                    "3000", "8080", "5173", command=command,
                )
                kill.assert_called_once()
                self.assertEqual(list(kill.call_args.args[0]), [3000, 8080, 5173])
                self.assertEqual(result.exit_code, 0, result.output)

    def test_success_lines_follow_the_order_of_the_ports_given(self) -> None:
        result, _ = self.invoke(
            {8080: KillPortResult(killed=[102]), 3000: KillPortResult(killed=[101])}, "8080", "3000"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            ["Killed PID 102 using port 8080.", "Killed PID 101 using port 3000."],
        )

    def test_pid_holding_several_ports_gets_a_line_for_each_port(self) -> None:
        result, _ = self.invoke(
            {3000: KillPortResult(killed=[101]), 8080: KillPortResult(killed=[101])}, "3000", "8080"
        )

        self.assertEqual(
            result.stdout.splitlines(),
            ["Killed PID 101 using port 3000.", "Killed PID 101 using port 8080."],
        )

    def test_port_without_owner_is_reported_but_is_not_a_failure(self) -> None:
        result, _ = self.invoke(
            {3000: KillPortResult(killed=[101]), 5173: KillPortResult()}, "3000", "5173"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.stdout.splitlines(),
            ["Killed PID 101 using port 3000.", "No visible processes using local port 5173."],
        )

    def test_duplicate_ports_are_reported_once(self) -> None:
        result, _ = self.invoke({3000: KillPortResult(killed=[101])}, "3000", "3000")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.splitlines(), ["Killed PID 101 using port 3000."])

    def test_failure_on_one_port_does_not_hide_the_others_and_exits_one(self) -> None:
        result, _ = self.invoke(
            {
                3000: KillPortResult(errors=["Access denied for PID 101."]),
                8080: KillPortResult(killed=[102]),
                5173: KillPortResult(),
            },
            "3000", "8080", "5173",
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 102 using port 8080.", result.stdout)
        self.assertIn("No visible processes using local port 5173.", result.stdout)
        self.assertIn("Access denied for PID 101", result.stderr)
        self.assertNotIn("using port 3000", result.stdout)

    def test_inspection_failure_exits_one_with_message_on_stderr(self) -> None:
        result, _ = self.invoke(RuntimeError("Cannot inspect"), "3000", "8080")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_any_invalid_port_or_no_port_is_a_usage_error(self) -> None:
        for command in (("killport",), ("port", "kill")):
            for arguments in ([], ["3000", "0"], ["3000", "65536"], ["3000", "abc"]):
                with self.subTest(command=command, arguments=arguments):
                    result, kill = self.invoke({}, *arguments, command=command)
                    self.assertEqual(result.exit_code, 2)
                    kill.assert_not_called()

    def test_restart_hns_is_refused_off_windows_for_several_ports(self) -> None:
        with mock.patch("jzr_tools.commands.network.is_windows", return_value=False):
            result, kill = self.invoke({}, "3000", "8080", "--restart-hns")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("only available on Windows", result.stderr)
        kill.assert_not_called()


class KillSeveralPortsExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.restarted = False
        self.persistent = False  # exclusions survive an HNS restart
        self.excluded: dict[int, bool] = {}  # port -> administered

        def exclusions(port: int) -> list[PortExclusion]:
            if port not in self.excluded or (self.restarted and not self.persistent):
                return []
            return [PortExclusion("ipv4", "tcp", port, port, self.excluded[port])]

        def restart() -> None:
            self.restarted = True

        self.enterContext(mock.patch("jzr_tools.commands.network.is_windows", return_value=True))
        self.get_exclusions = self.enterContext(mock.patch(
            "jzr_tools.commands.network.get_port_exclusions", side_effect=exclusions
        ))
        self.restart = self.enterContext(mock.patch(
            "jzr_tools.commands.network.restart_hns_service", side_effect=restart
        ))
        self.bind_check = self.enterContext(mock.patch("jzr_tools.commands.network.check_port_bindings"))

    def invoke(self, results: dict[int, KillPortResult], *args: str):  # noqa: ANN202
        with mock.patch("jzr_tools.commands.network.kill_ports", return_value=results):
            return CliRunner().invoke(app, ["killport", *args])

    def test_excluded_port_fails_but_the_other_ports_are_still_reported(self) -> None:
        self.excluded = {3000: False}

        result = self.invoke(
            {3000: KillPortResult(), 8080: KillPortResult(killed=[102]), 5173: KillPortResult()},
            "3000", "8080", "5173",
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Port 3000 is excluded for", result.stderr)
        self.assertNotIn("Port 8080 is excluded", result.stderr)
        self.assertIn("Killed PID 102 using port 8080.", result.stdout)
        self.assertIn("No visible processes using local port 5173.", result.stdout)
        self.assertNotIn("No visible processes using local port 3000.", result.stdout)
        self.restart.assert_not_called()

    def test_hint_without_restart_option_mentions_the_option_and_the_port(self) -> None:
        self.excluded = {3000: False}

        result = self.invoke({3000: KillPortResult(), 8080: KillPortResult()}, "3000", "8080")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("--restart-hns", result.stderr)
        self.assertIn("3000", result.stderr)

    def test_restart_option_restarts_hns_once_for_several_excluded_ports(self) -> None:
        self.excluded = {3000: False, 8080: False}

        result = self.invoke(
            {3000: KillPortResult(), 8080: KillPortResult(), 5173: KillPortResult()},
            "3000", "8080", "5173", "--restart-hns",
        )

        self.assertEqual(result.exit_code, 0, result.output + result.stderr)
        self.restart.assert_called_once()
        self.assertEqual(
            sorted(call.args[0] for call in self.bind_check.call_args_list), [3000, 8080]
        )
        self.assertIn("port 3000 passed local TCP/UDP bind checks", result.stdout)
        self.assertIn("port 8080 passed local TCP/UDP bind checks", result.stdout)
        self.assertIn("No visible processes using local port 5173.", result.stdout)

    def test_administered_exclusion_prevents_the_restart_for_all_ports(self) -> None:
        self.excluded = {3000: True, 8080: False}

        result = self.invoke(
            {3000: KillPortResult(), 8080: KillPortResult()}, "3000", "8080", "--restart-hns"
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("administered", result.stderr)
        self.restart.assert_not_called()

    def test_port_still_excluded_after_restart_fails_the_command(self) -> None:
        self.excluded = {3000: False, 8080: False}
        self.persistent = True

        result = self.invoke(
            {3000: KillPortResult(), 8080: KillPortResult()}, "3000", "8080", "--restart-hns"
        )

        self.assertEqual(result.exit_code, 1)
        self.restart.assert_called_once()
        self.assertIn("still excluded", result.stderr)

    def test_restart_option_without_any_exclusion_leaves_hns_alone(self) -> None:
        result = self.invoke(
            {3000: KillPortResult(killed=[101]), 8080: KillPortResult()}, "3000", "8080", "--restart-hns"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.restart.assert_not_called()
        self.bind_check.assert_not_called()
        self.assertIn("Killed PID 101 using port 3000.", result.stdout)
        self.assertIn("No visible processes using local port 8080.", result.stdout)

    def test_every_listed_port_is_checked_for_exclusions(self) -> None:
        self.invoke({3000: KillPortResult(), 8080: KillPortResult()}, "3000", "8080")

        self.assertEqual(
            sorted(call.args[0] for call in self.get_exclusions.call_args_list), [3000, 8080]
        )


class SeveralPortsIntegrationTests(unittest.TestCase):
    CHILD_CODE = """
import os, pathlib, socket, sys, time
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind(('127.0.0.1', 0))
sock.listen()
pathlib.Path(sys.argv[1]).write_text(f'{sock.getsockname()[1]}:{os.getpid()}')
time.sleep(60)
"""

    def spawn(self, tmp: str, name: str) -> tuple[subprocess.Popen, int, int]:
        ready = Path(tmp) / name
        child = subprocess.Popen(
            [sys.executable, "-c", self.CHILD_CODE, str(ready)],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait(timeout=5)))
        deadline = time.monotonic() + 5
        text = ""
        while time.monotonic() < deadline:
            if ready.exists():
                text = ready.read_text()
            if text or child.poll() is not None:
                break
            time.sleep(0.02)
        self.assertTrue(text, "Test socket did not become ready")
        port, pid = map(int, text.split(":"))
        return child, port, pid

    def test_killport_frees_two_ports_held_by_two_processes(self) -> None:
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        with tempfile.TemporaryDirectory() as tmp:
            first, port1, pid1 = self.spawn(tmp, "one.txt")
            second, port2, pid2 = self.spawn(tmp, "two.txt")
            self.assertNotEqual(port1, port2)
            excluded = bool(get_port_exclusions(port1) or get_port_exclusions(port2))

            result = subprocess.run(
                [sys.executable, "-m", "jzr_tools", "killport", str(port1), str(port2)],
                capture_output=True, text=True, timeout=30,
            )

            self.assertEqual(result.returncode, 1 if excluded else 0, result.stdout + result.stderr)
            self.assertIn(f"Killed PID {pid1} using port {port1}.", result.stdout)
            self.assertIn(f"Killed PID {pid2} using port {port2}.", result.stdout)
            first.wait(timeout=5)
            second.wait(timeout=5)
            for port in (port1, port2):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.bind(("127.0.0.1", port))


class ListListeningIntegrationTests(unittest.TestCase):
    def test_finds_a_tcp_listener_opened_by_this_process(self) -> None:
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]

            owners = core_network.list_listening()

        mine = [owner for owner in owners if owner.port == port]
        self.assertEqual(len(mine), 1, mine)
        self.assertEqual(mine[0].pid, os.getpid())
        self.assertEqual(mine[0].protocol, "tcp")
        self.assertEqual(mine[0].address, "127.0.0.1")
        self.assertEqual(mine[0].name, psutil.Process().name())

    def test_udp_sockets_appear_only_when_requested(self) -> None:
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

            default = core_network.list_listening()
            with_udp = core_network.list_listening(udp=True)

        self.assertEqual([o for o in default if o.port == port and o.protocol == "udp"], [])
        mine = [o for o in with_udp if o.port == port and o.protocol == "udp"]
        self.assertEqual([(o.pid, o.address) for o in mine], [(os.getpid(), "127.0.0.1")])

    def test_port_show_json_lists_the_listener(self) -> None:
        import json

        if sys.platform == "darwin" and os.geteuid() != 0:
            self.skipTest("macOS requires root for system-wide connection inspection")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]

            result = CliRunner().invoke(app, ["port", "show", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        mine = [item for item in json.loads(result.stdout) if item["port"] == port]
        self.assertEqual(len(mine), 1, mine)
        self.assertEqual(mine[0]["pid"], os.getpid())
        self.assertEqual(mine[0]["protocol"], "tcp")
        self.assertEqual(mine[0]["status"], "LISTEN")


if __name__ == "__main__":
    unittest.main()

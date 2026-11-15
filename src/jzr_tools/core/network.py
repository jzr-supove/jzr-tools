"""Cross-platform helpers for processes bound to network ports."""

from collections.abc import Sequence
from dataclasses import dataclass, field
import os
import socket

import psutil


PERMISSION_HINT = "Try an Administrator terminal on Windows or sudo on Unix."


@dataclass
class KillPortResult:
    killed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortOwner:
    """One socket bound to a local port, and the process holding it."""

    pid: int | None
    name: str
    protocol: str
    address: str
    port: int
    status: str


def _check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")


def _inet_connections() -> list:
    try:
        return psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError) as exc:
        raise RuntimeError(f"Cannot inspect network ports. {PERMISSION_HINT}") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect network ports: {exc}") from exc


def _process_name(pid: int | None) -> str:
    if pid is None:
        return "?"
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return "?"


def _owner(connection, port: int) -> PortOwner:
    return PortOwner(
        pid=connection.pid,
        name=_process_name(connection.pid),
        protocol="udp" if connection.type == socket.SOCK_DGRAM else "tcp",
        address=connection.laddr.ip,
        port=port,
        status=connection.status,
    )


def list_port_owners(port: int) -> list[PortOwner]:
    """Return the sockets bound to a local TCP/UDP port, without touching them."""
    _check_port(port)

    owners: set[PortOwner] = set()
    for connection in _inet_connections():
        if not connection.laddr or connection.laddr.port != port:
            continue
        # TIME_WAIT sockets can outlive their process; kill_port skips them too.
        if connection.status == psutil.CONN_TIME_WAIT:
            continue
        owners.add(_owner(connection, port))

    return sorted(owners, key=lambda owner: (owner.pid or -1, owner.protocol, owner.address))


def list_listening(*, udp: bool = False) -> list[PortOwner]:
    """Return every listening TCP socket, and bound UDP sockets if asked.

    Sorted by port, protocol, PID and address. Nothing is touched.
    """
    owners: set[PortOwner] = set()
    for connection in _inet_connections():
        if not connection.laddr:
            continue
        if connection.type == socket.SOCK_DGRAM:
            if not udp:
                continue
        elif connection.status != psutil.CONN_LISTEN:
            continue
        owners.add(_owner(connection, connection.laddr.port))

    return sorted(
        owners,
        key=lambda owner: (owner.port, owner.protocol, owner.pid or -1, owner.address),
    )


def kill_port(port: int) -> KillPortResult:
    """Force-kill owners of a local TCP/UDP port and wait for their exit."""
    return kill_ports([port])[port]


def kill_ports(ports: Sequence[int]) -> dict[int, KillPortResult]:
    """Force-kill owners of local TCP/UDP ports and wait for their exit.

    One snapshot of the sockets serves every port. A process holding several
    of the ports is killed once and reported for each of them. Only processes
    that still own one of the ports at the pre-kill check are targeted.
    Process.kill guards against PID reuse after creating the Process object.
    The result has one entry per distinct port, in the order given.
    """
    wanted = list(dict.fromkeys(ports))
    for port in wanted:
        _check_port(port)
    connections = _inet_connections()

    results = {port: KillPortResult() for port in wanted}
    held: dict[int, set[int]] = {}  # pid -> listed ports it holds
    for connection in connections:
        if not connection.laddr or connection.laddr.port not in results:
            continue
        # TIME_WAIT sockets can outlive their process and cannot be killed.
        if connection.status == psutil.CONN_TIME_WAIT:
            continue
        port = connection.laddr.port
        if connection.pid is None:
            message = f"Cannot identify an owner of port {port}. {PERMISSION_HINT}"
            if message not in results[port].errors:
                results[port].errors.append(message)
        else:
            held.setdefault(connection.pid, set()).add(port)

    def fail(pid: int, message: str) -> None:
        for port in held[pid]:
            results[port].errors.append(message)

    pending: list[psutil.Process] = []
    for pid in sorted(held):
        if pid <= 0 or pid == os.getpid():
            fail(pid, f"Refusing to kill reserved or current PID {pid}.")
            continue
        try:
            process = psutil.Process(pid)
            # The initial snapshot can become stale before we reach this PID.
            if not any(
                connection.laddr and connection.laddr.port in held[pid]
                for connection in process.net_connections(kind="inet")
            ):
                continue
            process.kill()
            pending.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, PermissionError):
            fail(pid, f"Access denied for PID {pid}. {PERMISSION_HINT}")
        except OSError as exc:
            fail(pid, f"Could not kill PID {pid}: {exc}")

    killed: list[int] = []
    gone, alive = psutil.wait_procs(pending, timeout=3)
    killed.extend(process.pid for process in gone)
    for process in alive:
        try:
            # Unix retains a dead process as a zombie until its parent reaps it.
            # Its sockets are already closed, even though wait_procs sees the PID.
            if process.status() == psutil.STATUS_ZOMBIE:
                killed.append(process.pid)
            else:
                fail(process.pid, f"PID {process.pid} has not exited after the kill request.")
        except psutil.NoSuchProcess:
            killed.append(process.pid)
        except (psutil.AccessDenied, OSError):
            fail(process.pid, f"Cannot confirm exit of PID {process.pid}. {PERMISSION_HINT}")

    for pid in sorted(killed):
        for port in held[pid]:
            results[port].killed.append(pid)
    return results

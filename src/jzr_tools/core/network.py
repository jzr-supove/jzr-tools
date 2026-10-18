"""Cross-platform helpers for processes bound to network ports."""

from dataclasses import dataclass, field
import os

import psutil


PERMISSION_HINT = "Try an Administrator terminal on Windows or sudo on Unix."


@dataclass
class KillPortResult:
    killed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def kill_port(port: int) -> KillPortResult:
    """Force-kill owners of a local TCP/UDP port and wait for their exit.

    Only processes that still own the port at the pre-kill check are targeted.
    Process.kill guards against PID reuse after creating the Process object.
    """
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")

    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError) as exc:
        raise RuntimeError(f"Cannot inspect network ports. {PERMISSION_HINT}") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect network ports: {exc}") from exc

    result = KillPortResult()
    pids: set[int] = set()
    unknown_owner = False
    for connection in connections:
        if not connection.laddr or connection.laddr.port != port:
            continue
        # TIME_WAIT sockets can outlive their process and cannot be killed.
        if connection.status == psutil.CONN_TIME_WAIT:
            continue
        if connection.pid is None:
            unknown_owner = True
        else:
            pids.add(connection.pid)

    if unknown_owner:
        result.errors.append(f"Cannot identify an owner of port {port}. {PERMISSION_HINT}")

    pending: list[psutil.Process] = []
    for pid in sorted(pids):
        if pid <= 0 or pid == os.getpid():
            result.errors.append(f"Refusing to kill reserved or current PID {pid}.")
            continue
        try:
            process = psutil.Process(pid)
            # The initial snapshot can become stale before we reach this PID.
            if not any(
                connection.laddr and connection.laddr.port == port
                for connection in process.net_connections(kind="inet")
            ):
                continue
            process.kill()
            pending.append(process)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, PermissionError):
            result.errors.append(f"Access denied for PID {pid}. {PERMISSION_HINT}")
        except OSError as exc:
            result.errors.append(f"Could not kill PID {pid}: {exc}")

    gone, alive = psutil.wait_procs(pending, timeout=3)
    result.killed = [process.pid for process in gone]
    for process in alive:
        try:
            # Unix retains a dead process as a zombie until its parent reaps it.
            # Its sockets are already closed, even though wait_procs sees the PID.
            if process.status() == psutil.STATUS_ZOMBIE:
                result.killed.append(process.pid)
            else:
                result.errors.append(f"PID {process.pid} has not exited after the kill request.")
        except psutil.NoSuchProcess:
            result.killed.append(process.pid)
        except (psutil.AccessDenied, OSError):
            result.errors.append(f"Cannot confirm exit of PID {process.pid}. {PERMISSION_HINT}")
    result.killed.sort()
    return result

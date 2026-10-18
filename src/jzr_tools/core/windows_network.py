"""Windows port exclusion diagnostics and explicit HNS recovery."""

import ctypes
from dataclasses import dataclass
import platform
import re
import socket
import subprocess


@dataclass(frozen=True)
class PortExclusion:
    family: str
    protocol: str
    start: int
    end: int
    administered: bool = False

    def describe(self) -> str:
        suffix = " (administered)" if self.administered else ""
        return f"{self.family.upper()} {self.protocol.upper()} {self.start}-{self.end}{suffix}"


def is_windows() -> bool:
    return platform.system() == "Windows"


def _run(command: list[str], *, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            check=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"{command[0]} failed: {detail or exc.returncode}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{command[0]} timed out after {timeout} seconds.") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run {command[0]}: {exc}") from exc
    return result.stdout


def get_port_exclusions(port: int) -> list[PortExclusion]:
    """Find matching active exclusions without relying on localized headers."""
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if not is_windows():
        return []
    matches = []
    for family in ("ipv4", "ipv6"):
        for protocol in ("tcp", "udp"):
            output = _run([
                "netsh", "interface", family, "show", "excludedportrange",
                f"protocol={protocol}", "store=active",
            ])
            if not re.search(r"(?m)^\s*-{2,}\s+-{2,}\s*$", output):
                raise RuntimeError(f"Cannot parse {family} {protocol} port exclusions from netsh.")
            for row in re.finditer(r"(?m)^\s*(\d+)\s+(\d+)[ \t]*(\*)?[ \t]*\r?$", output):
                start, end = int(row[1]), int(row[2])
                if start <= port <= end:
                    matches.append(PortExclusion(family, protocol, start, end, bool(row[3])))
    return matches


def restart_hns() -> None:
    """Restart only HNS, without forcing dependent services to stop."""
    if not is_windows():
        raise RuntimeError("--restart-hns is only available on Windows.")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Restarting HNS requires an Administrator terminal. Re-run there.")
    script = """$ErrorActionPreference = 'Stop'
try {
    Stop-Service -Name hns -ErrorAction Stop
} finally {
    Start-Service -Name hns -ErrorAction Stop
}
(Get-Service -Name hns).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
"""
    try:
        _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=60)
    except RuntimeError as exc:
        raise RuntimeError(
            f"HNS restart failed: {exc} Check Get-Service hns; if stopped, run Start-Service hns "
            "in an Administrator PowerShell."
        ) from exc


def check_port_bindings(port: int) -> None:
    """Verify local binds after recovery; do not claim an exclusion-free port is usable."""
    families = [(socket.AF_INET, "0.0.0.0")]
    if socket.has_ipv6:
        families.append((socket.AF_INET6, "::"))
    for family, address in families:
        for kind, protocol in ((socket.SOCK_STREAM, "TCP"), (socket.SOCK_DGRAM, "UDP")):
            try:
                with socket.socket(family, kind) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    if family == socket.AF_INET6:
                        probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    probe.bind((address, port))
                    if kind == socket.SOCK_STREAM:
                        probe.listen(1)
            except OSError as exc:
                raise RuntimeError(f"Port {port} still cannot bind on {address} ({protocol}): {exc}") from exc

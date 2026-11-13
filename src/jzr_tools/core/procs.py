"""See what uses memory, grouped by application, and end processes.

``snapshot`` is the only function that lists processes; grouping is pure, and
ending acts on exactly the processes it is given.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
import sys

import psutil

GRACE_SECONDS = 5.0

# Ending one of these ends the desktop or remote session with it.
SESSION_NAMES = frozenset(
    {
        "systemd",
        "gnome-shell",
        "gnome-session-binary",
        "plasmashell",
        "kwin_wayland",
        "kwin_x11",
        "Xorg",
        "Xwayland",
        "dbus-daemon",
        "dbus-broker",
        "pipewire",
        "pipewire-pulse",
        "wireplumber",
        "sshd",
        "login",
        "gdm",
        "sddm",
        "lightdm",
    }
)

# The same for Windows: the shell, the log-on chain and the service hosts.
# Windows compares names without regard to case, and so does this list.
WINDOWS_SESSION_NAMES = frozenset(
    name.casefold()
    for name in (
        "explorer.exe",
        "dwm.exe",
        "csrss.exe",
        "winlogon.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "smss.exe",
        "sihost.exe",
        "ctfmon.exe",
        "fontdrvhost.exe",
        "svchost.exe",
        "taskhostw.exe",
        "RuntimeBroker.exe",
        "ShellExperienceHost.exe",
        "StartMenuExperienceHost.exe",
        "SearchHost.exe",
        "TextInputHost.exe",
        "LogonUI.exe",
        "audiodg.exe",
    )
)
# The idle process and the kernel.
WINDOWS_SYSTEM_PIDS = frozenset({0, 4})

THIS_TERMINAL = "this terminal"
SYSTEM = "system"
ANOTHER_USER = "another user"


@dataclass(frozen=True)
class Proc:
    """One process as it was when the snapshot was taken."""

    pid: int
    name: str
    exe: str
    user: str
    command: str
    memory_bytes: int
    swap_bytes: int
    started: float
    protected: str = ""  # why it must not be ended, or empty


@dataclass(frozen=True)
class Group:
    """The processes of one user that run the same executable."""

    name: str
    user: str
    processes: tuple[Proc, ...]

    @property
    def memory_bytes(self) -> int:
        return sum(process.memory_bytes for process in self.processes)

    @property
    def swap_bytes(self) -> int:
        return sum(process.swap_bytes for process in self.processes)

    @property
    def pids(self) -> tuple[int, ...]:
        return tuple(process.pid for process in self.processes)

    @property
    def endable(self) -> tuple[Proc, ...]:
        return tuple(process for process in self.processes if not process.protected)

    @property
    def protected(self) -> str:
        """The reason, when every process is protected; empty otherwise."""
        reasons = [process.protected for process in self.processes]
        return reasons[0] if reasons and all(reasons) else ""


@dataclass(frozen=True)
class EndResult:
    """What became of the processes that were asked to end."""

    ended: tuple[Proc, ...]
    survivors: tuple[Proc, ...]
    failed: tuple[tuple[Proc, str], ...]


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _windows() -> bool:
    """Return whether this is Windows; asked at run time so tests can fake it."""
    return sys.platform == "win32"


def memory_totals() -> dict[str, int]:
    """Return the machine's memory and swap, in use and in total."""
    memory, swap = psutil.virtual_memory(), psutil.swap_memory()
    return {
        "total_bytes": memory.total,
        # Cache the system can hand back does not count as used.
        "used_bytes": memory.total - memory.available,
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
    }


def _swap_share(pid: int) -> int | None:
    """Return a process's proportional share of swap, where the system has one.

    Linux reports it as ``SwapPss``: a swapped page shared by several
    processes is split between them, so the shares add up to what is really
    in swap. Elsewhere, or when it cannot be read, the answer is None.
    """
    try:
        with open(f"/proc/{pid}/smaps_rollup", "rb") as rollup:
            for line in rollup:
                if line.startswith(b"SwapPss:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _memory(process: psutil.Process) -> tuple[int, int]:
    """Return a process's memory and swap, as precisely as the system allows."""
    try:
        full = process.memory_full_info()
    except psutil.AccessDenied:
        # Someone else's process: only the resident size is readable, and it
        # counts shared pages in full.
        return process.memory_info().rss, 0
    swap = _swap_share(process.pid)
    if swap is None:
        swap = getattr(full, "swap", 0) or 0
    for field in ("pss", "uss", "rss"):
        value = getattr(full, field, None)
        if value is not None:
            return value, swap
    return 0, swap


def snapshot() -> list[Proc]:
    """List every process except kernel threads, with protection decided."""
    current = psutil.Process()
    lineage = {current.pid, *(parent.pid for parent in current.parents())}
    me, root, windows = current.username(), _is_root(), _windows()

    found: list[Proc] = []
    fields = ["pid", "name", "exe", "username", "cmdline", "create_time"]
    for process in psutil.process_iter(fields):
        try:
            info = process.info
            exe = info.get("exe") or ""
            command = " ".join(info.get("cmdline") or [])
            if not exe and not command:
                continue  # a kernel thread
            memory, swap = _memory(process)
        except psutil.Error:
            continue  # it exited, or nothing about it can be read

        pid, name, user = info["pid"], info.get("name") or "", info.get("username") or ""
        if pid in lineage:
            protected = THIS_TERMINAL
        elif windows:
            protected = _windows_protection(pid, name, exe, user, me, root)
        elif pid == 1 or name in SESSION_NAMES or os.path.basename(exe) in SESSION_NAMES:
            protected = SYSTEM
        elif not root and user != me:
            protected = ANOTHER_USER
        else:
            protected = ""
        started = info.get("create_time") or 0.0
        found.append(Proc(pid, name, exe, user, command, memory, swap, started, protected))
    return found


def _windows_protection(
    pid: int, name: str, exe: str, user: str, me: str, root: bool
) -> str:
    """Decide protection on Windows.

    A process whose owner cannot be read belongs to the system or a service,
    as does one owned by ``NT AUTHORITY``; those stay protected even for an
    administrator. Other users' processes are protected unless ``root``.
    """
    names = {name.casefold(), exe.replace("/", "\\").rpartition("\\")[2].casefold()}
    if pid in WINDOWS_SYSTEM_PIDS or names & WINDOWS_SESSION_NAMES:
        return SYSTEM
    if not user or user.casefold().startswith("nt authority\\"):
        return SYSTEM
    return "" if root or user == me else ANOTHER_USER


def group_processes(processes: Iterable[Proc]) -> list[Group]:
    """Group processes by user and executable, largest group first."""
    members: dict[tuple[str, str], list[Proc]] = {}
    for process in processes:
        # No readable executable: the process name is the next best identity.
        identity = process.exe or f"name:{process.name}"
        members.setdefault((process.user, identity), []).append(process)

    groups = []
    for (user, _), group in members.items():
        first = group[0]
        name = os.path.basename(first.exe) if first.exe else first.name
        if not any(char.isalpha() for char in name):
            # A binary stored under its version number says nothing; the
            # oldest process is the application's main one.
            name = min(group, key=lambda process: process.started).name or name
        group.sort(key=lambda process: (-process.memory_bytes, process.pid))
        groups.append(Group(name, user, tuple(group)))
    return sorted(groups, key=lambda group: (-group.memory_bytes, group.name, group.user))


def _signal(processes: Iterable[Proc], action: str, grace: float) -> EndResult:
    """Send ``action`` to each process and wait up to ``grace`` for them.

    ``action`` is a psutil method name, or ``"close"``: post a close to the
    process's windows, the polite request Windows has instead of SIGTERM.
    """
    ended: list[Proc] = []
    failed: list[tuple[Proc, str]] = []
    waiting: dict[int, Proc] = {}
    handles = []
    windowed = False
    for process in processes:
        if process.protected:
            continue
        try:
            handle = psutil.Process(process.pid)
            if abs(handle.create_time() - process.started) > 1.0:
                # The PID now belongs to something else: ours is gone.
                ended.append(process)
                continue
            if action == "close":
                windowed = _close_windows(process.pid) or windowed
            else:
                getattr(handle, action)()
        except psutil.NoSuchProcess:
            ended.append(process)
        except psutil.AccessDenied:
            failed.append((process, "permission denied"))
        else:
            handles.append(handle)
            waiting[process.pid] = process

    survivors: list[Proc] = []
    if handles and action == "close" and not windowed:
        # Nothing had a window, so nothing was asked to exit, and waiting
        # would only delay the question about force-killing.
        for handle in handles:
            (ended if _has_exited(handle) else survivors).append(waiting[handle.pid])
    elif handles:
        gone, alive = psutil.wait_procs(handles, timeout=grace)
        ended += [waiting[handle.pid] for handle in gone]
        for handle in alive:
            # A zombie has exited and only waits for its parent to notice.
            (ended if _has_exited(handle) else survivors).append(waiting[handle.pid])
    return EndResult(tuple(ended), tuple(survivors), tuple(failed))


def _close_windows(pid: int) -> bool:
    """Post WM_CLOSE to every top-level window of ``pid`` (Windows only).

    Returns whether the process had such a window. Hidden ones count: a
    program minimised to the tray still handles the message. Windows owned
    by another window, such as dialogs, close with their owner.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    gw_owner, wm_close = 4, 0x0010
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and not user32.GetWindow(window, gw_owner):
            found.append(window)
        return True

    user32.EnumWindows(visit, 0)
    for window in found:
        user32.PostMessageW(window, wm_close, 0, 0)
    return bool(found)


def _has_exited(handle: psutil.Process) -> bool:
    try:
        return handle.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def end_processes(processes: Iterable[Proc], *, grace: float = GRACE_SECONDS) -> EndResult:
    """Ask processes to exit and wait up to ``grace`` seconds for them.

    On Windows psutil's ``terminate`` is a hard kill, so the request is a
    close posted to each process's windows; a program without a window
    cannot be asked, and comes back as a survivor at once.
    """
    return _signal(processes, "close" if _windows() else "terminate", grace)


def kill_processes(processes: Iterable[Proc], *, grace: float = GRACE_SECONDS) -> EndResult:
    """Force-kill processes and wait up to ``grace`` seconds for them."""
    return _signal(processes, "kill", grace)

"""Finding the processes of a test on the machine by a unique `sleep` argument, and killing them.

With a run in its own pid namespace a pid noted inside it means nothing outside, so the tests give each long
`sleep` a length no other process has (`sleep 3600.123456789`) and look for that in the machine's command lines.

Not a test module (no test_ prefix), so unittest discovery does not collect it.
"""

from __future__ import annotations

import os
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path


def kill_with_token(token: str) -> list[int]:
    """Kill every process on the machine that has `token` in its command line; the pids that were found."""
    found = host_pids_with(token)
    for pid in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return found


def host_pids_with(token: str) -> list[int]:
    """The processes of the machine (as seen from outside any wrapper) with `token` in their command line."""
    found: list[int] = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            command = Path(f"/proc/{name}/cmdline").read_bytes()
        except OSError:
            continue
        if token.encode() in command:
            try:
                state = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                continue
            if state != "Z":  # a zombie is dead, only not yet collected
                found.append(int(name))
    return found


def wait_until(condition: Callable[[], bool], seconds: float = 10) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def unique_sleep() -> str:
    """A sleep length that no other process uses, so that `sleep <it>` names one test's process."""
    return f"3600.{uuid.uuid4().int % 10**9:09d}"

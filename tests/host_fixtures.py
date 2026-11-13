"""Hand-built ``Status`` objects shared by the host tests (not a test module)."""

from __future__ import annotations

import re
import unicodedata

from jzr_tools.core.host import (
    BatteryInfo,
    CpuInfo,
    GpuInfo,
    HostInfo,
    MemoryInfo,
    NetworkInfo,
    StorageInfo,
    Status,
    TemperatureInfo,
)

GIB = 1024**3
TIB = 1024**4
MIB = 1024**2

UPTIME_3D_4H_12M = 3 * 86400 + 4 * 3600 + 12 * 60 + 30


def make_full(hostname: str = "atlas", uptime: float = UPTIME_3D_4H_12M) -> Status:
    """A machine where every optional value is present."""
    return Status(
        timestamp="2026-11-13T12:00:00+00:00",
        host=HostInfo(
            hostname=hostname,
            os="Fedora Linux 42",
            kernel="6.15.10-200.fc42.x86_64",
            arch="x86_64",
            boot_time="2026-11-10T08:00:00+00:00",
            uptime_seconds=uptime,
        ),
        cpu=CpuInfo(
            model="Intel Core i7-12700K",
            physical_cores=12,
            logical_cores=20,
            usage_percent=37.4,
            per_core_percent=tuple(float(81 + i) for i in range(20)),
            load_average=(2.5, 3.5, 4.5),
            frequency_mhz=3600.0,
            temperature_c=58.0,
        ),
        memory=MemoryInfo(
            total_bytes=round(31.1 * GIB),
            used_bytes=round(22.5 * GIB),
            available_bytes=round(8.5 * GIB),
            percent=72.4,
            swap_total_bytes=4 * GIB,
            swap_used_bytes=1 * GIB,
            swap_percent=25.0,
        ),
        storage=(
            StorageInfo("/", "/dev/nvme0n1p2", "btrfs", 200 * GIB, 150 * GIB, 50 * GIB, 75.0),
            StorageInfo("/home", "/dev/nvme0n1p2", "btrfs", 200 * GIB, 150 * GIB, 50 * GIB, 75.0),
            StorageInfo("/mnt/data", "/dev/sda1", "ext4", 2 * TIB, 1 * TIB, 1 * TIB, 50.0),
        ),
        gpus=(
            GpuInfo(
                index=0,
                name="NVIDIA GeForce RTX 4080",
                vendor="NVIDIA",
                driver="580.105.08",
                utilization_percent=31.0,
                memory_used_bytes=4 * GIB,
                memory_total_bytes=16 * GIB,
                temperature_c=63.0,
                power_watts=17.63,
            ),
            GpuInfo(
                index=1,
                name="AMD Radeon RX 6800",
                vendor="AMD",
                driver="amdgpu",
                utilization_percent=12.0,
                memory_used_bytes=2 * GIB,
                memory_total_bytes=8 * GIB,
                temperature_c=52.0,
                power_watts=75.5,
            ),
        ),
        network=(
            NetworkInfo("eth0", True, ("192.168.1.20", "fe80::1"), 1 * MIB, 5 * MIB),
            NetworkInfo("wlan0", False, (), 0, 0),
        ),
        temperatures=(
            TemperatureInfo("coretemp", "Package id 0", 58.0),
            TemperatureInfo("nvme", "Composite", 41.0),
        ),
        battery=BatteryInfo(percent=64.0, plugged=True, seconds_left=None),
    )


def make_bare() -> Status:
    """A machine that exposes almost nothing optional."""
    return Status(
        timestamp="2026-11-13T12:00:00+00:00",
        host=HostInfo(
            hostname="bare-host",
            os="Windows 11",
            kernel="10.0.22631",
            arch="AMD64",
            boot_time="2026-11-13T08:00:00+00:00",
            uptime_seconds=4 * 3600 + 12 * 60,
        ),
        cpu=CpuInfo(
            model="Generic CPU",
            physical_cores=None,
            logical_cores=4,
            usage_percent=5.0,
            per_core_percent=(4.0, 6.0, 5.0, 5.0),
            load_average=None,
            frequency_mhz=None,
            temperature_c=None,
        ),
        memory=MemoryInfo(
            total_bytes=8 * GIB,
            used_bytes=2 * GIB,
            available_bytes=6 * GIB,
            percent=25.0,
            swap_total_bytes=0,
            swap_used_bytes=0,
            swap_percent=0.0,
        ),
        storage=(StorageInfo("C:\\", "C:\\", "NTFS", 500 * GIB, 100 * GIB, 400 * GIB, 20.0),),
        gpus=(),
        network=(),
        temperatures=(),
        battery=None,
    )


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

UNICODE_BAR = re.compile("[\u2588\u2591]+")  # full block, light shade
ASCII_BAR = re.compile(r"\[([#.]+)\]")


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def display_width(text: str) -> int:
    """Terminal columns: wide/fullwidth East Asian characters 2, combining marks 0."""
    width = 0
    for char in text:
        if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def find_bars(line: str, ascii: bool = False) -> list[tuple[int, str]]:
    """Return ``(start display column, bar cells)`` for each bar on a line.

    A Unicode bar is a run of full/light blocks; an ASCII bar is ``[`` + cells
    + ``]``. The cells are returned without the brackets.
    """
    pattern = ASCII_BAR if ascii else UNICODE_BAR
    found = []
    for match in pattern.finditer(line):
        cells = match.group(1) if ascii else match.group(0)
        found.append((display_width(line[: match.start()]), cells))
    return found


def bar_fill(cells: str, ascii: bool = False) -> int | None:
    """Filled cells of a bar drawn from the left, or ``None`` if not left-filled."""
    full, empty = ("#", ".") if ascii else ("\u2588", "\u2591")
    rest = cells.lstrip(full)
    if set(rest) - {empty}:
        return None
    return len(cells) - len(rest)

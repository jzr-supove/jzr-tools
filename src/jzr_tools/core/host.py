"""Read the state of the machine: host, CPU, memory, storage, GPU and more.

Everything is best effort. A value the platform does not expose is ``None``
(or an empty tuple) rather than an error, so a report can always be produced.
``psutil`` covers what is common to Windows, Linux and macOS; GPUs have no
common interface and are read from vendor tools and the operating system.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
from typing import Callable, Mapping, TypeVar

import psutil

_T = TypeVar("_T")

_MIB = 1024**2

# How long an external command may take before it is treated as absent.
_COMMAND_TIMEOUT = 5

_PCI_VENDORS = {"1002": "AMD", "8086": "Intel", "10de": "NVIDIA"}

_NVIDIA_SMI_QUERY = (
    "index,name,driver_version,utilization.gpu,memory.used,memory.total,"
    "temperature.gpu,power.draw"
)

# Read-only package images, always "100% full" and never interesting.
_IGNORED_FILESYSTEMS = frozenset({"squashfs"})

# Sensor chips that report the processor's own temperature, best first.
_CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "soc_thermal")


@dataclass(frozen=True)
class HostInfo:
    hostname: str
    os: str
    kernel: str
    arch: str
    boot_time: str
    uptime_seconds: float


@dataclass(frozen=True)
class CpuInfo:
    model: str
    physical_cores: int | None
    logical_cores: int | None
    usage_percent: float
    per_core_percent: tuple[float, ...]
    load_average: tuple[float, float, float] | None
    frequency_mhz: float | None
    temperature_c: float | None


@dataclass(frozen=True)
class MemoryInfo:
    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float
    swap_total_bytes: int
    swap_used_bytes: int
    swap_percent: float


@dataclass(frozen=True)
class StorageInfo:
    mount: str
    device: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    vendor: str | None = None
    driver: str | None = None
    utilization_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    temperature_c: float | None = None
    power_watts: float | None = None


@dataclass(frozen=True)
class NetworkInfo:
    name: str
    is_up: bool
    addresses: tuple[str, ...]
    bytes_sent: int
    bytes_received: int


@dataclass(frozen=True)
class TemperatureInfo:
    sensor: str
    label: str
    celsius: float


@dataclass(frozen=True)
class BatteryInfo:
    percent: float
    plugged: bool | None
    seconds_left: int | None


@dataclass(frozen=True)
class Status:
    """One reading of the whole machine."""

    timestamp: str
    host: HostInfo
    cpu: CpuInfo
    memory: MemoryInfo
    storage: tuple[StorageInfo, ...]
    gpus: tuple[GpuInfo, ...]
    network: tuple[NetworkInfo, ...]
    temperatures: tuple[TemperatureInfo, ...]
    battery: BatteryInfo | None

    def to_dict(self) -> dict:
        """Return the reading as plain JSON-serialisable data."""
        # A JSON round trip turns the tuples `asdict` keeps into lists.
        return json.loads(json.dumps(asdict(self)))


# --- GPU parsers -----------------------------------------------------------


def _number(value: str) -> float | None:
    """Parse a number, treating ``[N/A]``-style placeholders as missing."""
    try:
        return float(value.strip())
    except ValueError:
        return None


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    """Parse ``nvidia-smi --query-gpu=... --format=csv,noheader,nounits``."""
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 8:
            continue
        index, name, driver, utilization, used, total, temperature, power = fields
        position = _number(index)
        if position is None or not name:
            continue
        used_mib, total_mib = _number(used), _number(total)
        gpus.append(
            GpuInfo(
                index=int(position),
                name=name,
                vendor="NVIDIA",
                driver=driver if _is_present(driver) else None,
                utilization_percent=_number(utilization),
                memory_used_bytes=None if used_mib is None else round(used_mib * _MIB),
                memory_total_bytes=None if total_mib is None else round(total_mib * _MIB),
                temperature_c=_number(temperature),
                power_watts=_number(power),
            )
        )
    return gpus


def _is_present(value: str) -> bool:
    return bool(value) and "N/A" not in value and "Not Supported" not in value


def _short_vendor(vendor: str) -> str:
    lowered = vendor.lower()
    if "intel" in lowered:
        return "Intel"
    if "nvidia" in lowered:
        return "NVIDIA"
    if "advanced micro devices" in lowered or re.search(r"\b(amd|ati)\b", lowered):
        return "AMD"
    return vendor


def parse_lspci(output: str) -> dict[str, str]:
    """Map PCI slots to display names, from ``lspci -mm -D`` output."""
    names: dict[str, str] = {}
    for line in output.splitlines():
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        if len(fields) < 4:
            continue
        slot, device_class, vendor, device = fields[:4]
        if not any(kind in device_class for kind in ("VGA", "3D", "Display")):
            continue
        # "AD103 [GeForce RTX 4080]" carries the marketing name in brackets.
        bracketed = re.findall(r"\[([^\]]+)\]", device)
        model = bracketed[-1] if bracketed else device
        names[slot] = f"{_short_vendor(vendor)} {model}".strip()
    return names


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _read_number(path: Path, scale: float = 1.0) -> float | None:
    text = _read_text(path)
    if text is None:
        return None
    value = _number(text)
    return None if value is None else value / scale


def _read_hwmon(device: Path, name: str, scale: float) -> float | None:
    try:
        candidates = sorted((device / "hwmon").glob(f"hwmon*/{name}"))
    except OSError:
        return None
    for candidate in candidates:
        value = _read_number(candidate, scale)
        if value is not None:
            return value
    return None


def read_drm_gpus(drm_root: Path, names: Mapping[str, str] | None = None) -> list[GpuInfo]:
    """Read the graphics cards Linux exposes under ``/sys/class/drm``."""
    try:
        entries = [entry.name for entry in drm_root.iterdir()]
    except OSError:
        return []

    # `card0-DP-1` is a connector of `card0`, not a card of its own.
    numbers = sorted(
        int(match.group(1))
        for match in (re.fullmatch(r"card(\d+)", entry) for entry in entries)
        if match is not None
    )

    gpus: list[GpuInfo] = []
    for number in numbers:
        device = drm_root / f"card{number}" / "device"
        uevent = _read_text(device / "uevent")
        if uevent is None:
            continue
        properties = dict(
            line.split("=", 1) for line in uevent.splitlines() if "=" in line
        )

        driver = properties.get("DRIVER") or None
        pci_id = properties.get("PCI_ID", "").lower()
        vendor = _PCI_VENDORS.get(pci_id.partition(":")[0])
        name = (names or {}).get(properties.get("PCI_SLOT_NAME", ""))
        if not name:
            if pci_id:
                name = f"{vendor or 'Unknown'} GPU ({pci_id})"
            else:
                name = driver or f"card{number}"

        used = _read_number(device / "mem_info_vram_used")
        total = _read_number(device / "mem_info_vram_total")
        gpus.append(
            GpuInfo(
                index=len(gpus),
                name=name,
                vendor=vendor,
                driver=driver,
                utilization_percent=_read_number(device / "gpu_busy_percent"),
                memory_used_bytes=None if used is None else int(used),
                memory_total_bytes=None if total is None else int(total),
                temperature_c=_read_hwmon(device, "temp1_input", 1000),
                power_watts=_read_hwmon(device, "power1_average", 1_000_000),
            )
        )
    return gpus


def parse_windows_video_controllers(output: str) -> list[GpuInfo]:
    """Parse ``Get-CimInstance Win32_VideoController ... | ConvertTo-Json``."""
    try:
        data = json.loads(output)
    except ValueError:
        return []
    # PowerShell prints a bare object when there is only one adapter.
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    gpus: list[GpuInfo] = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("Name"):
            continue
        gpus.append(
            GpuInfo(
                index=len(gpus),
                name=str(entry["Name"]).strip(),
                vendor=str(entry.get("AdapterCompatibility") or "").strip() or None,
                driver=str(entry.get("DriverVersion") or "").strip() or None,
            )
        )
    return gpus


def _macos_vendor(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # "sppci_vendor_amd", "NVIDIA (0x10de)" and "Intel" are all seen.
    name = re.sub(r"\s*\(0x[0-9a-fA-F]+\)", "", value).strip()
    name = re.sub(r"^sppci_vendor_", "", name)
    lowered = name.lower()
    if lowered in ("amd", "nvidia", "ati"):
        return lowered.upper()
    if lowered in ("intel", "apple"):
        return lowered.capitalize()
    return name or None


def _macos_memory(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*([\d.]+)\s*([KMGT])B", value, flags=re.IGNORECASE)
    if match is None:
        return None
    power = "KMGT".index(match.group(2).upper()) + 1
    return round(float(match.group(1)) * 1024**power)


def parse_macos_displays(output: str) -> list[GpuInfo]:
    """Parse ``system_profiler SPDisplaysDataType -json``."""
    try:
        data = json.loads(output)
    except ValueError:
        return []
    entries = data.get("SPDisplaysDataType") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    gpus: list[GpuInfo] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("sppci_model") or entry.get("_name")
        if not name:
            continue
        gpus.append(
            GpuInfo(
                index=len(gpus),
                name=str(name).strip(),
                vendor=_macos_vendor(entry.get("spdisplays_vendor")),
                memory_total_bytes=_macos_memory(
                    entry.get("spdisplays_vram") or entry.get("spdisplays_vram_shared")
                ),
            )
        )
    return gpus


# --- collection ------------------------------------------------------------


def _run(command: list[str]) -> str | None:
    """Return a command's stdout, or ``None`` if it is missing or fails."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=_COMMAND_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _attempt(read: Callable[[], _T], fallback: _T) -> _T:
    """Run one collector, so a broken section cannot take the report down."""
    try:
        return read()
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return fallback


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _os_name() -> str:
    system = platform.system()
    if system == "Linux":
        try:
            pretty = platform.freedesktop_os_release().get("PRETTY_NAME")
        except OSError:
            pretty = None
        return pretty or "Linux"
    if system == "Darwin":
        version = platform.mac_ver()[0]
        return f"macOS {version}".strip()
    if system == "Windows":
        release = platform.release()
        # Windows 11 still calls itself 10; only the build number tells.
        build = platform.version().rpartition(".")[2]
        if release == "10" and build.isdigit() and int(build) >= 22000:
            release = "11"
        return f"Windows {release}".strip()
    return system or "Unknown"


def _kernel() -> str:
    system = platform.system()
    if system == "Windows":
        return platform.version()
    if system == "Darwin":
        return f"Darwin {platform.release()}"
    return platform.release()


def _cpu_model() -> str:
    system = platform.system()
    model = ""
    if system == "Linux":
        cpuinfo = _read_text(Path("/proc/cpuinfo")) or ""
        # x86 has "model name"; ARM boards have "Model" or "Hardware".
        for key in ("model name", "Model", "Hardware"):
            match = re.search(rf"^{key}\s*:\s*(.+)$", cpuinfo, flags=re.MULTILINE)
            if match is not None:
                model = match.group(1)
                break
    elif system == "Darwin":
        model = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or ""
    elif system == "Windows":
        model = _attempt(_windows_cpu_model, "")
    model = " ".join(model.split())
    return model or platform.processor() or platform.machine() or "Unknown CPU"


def _windows_cpu_model() -> str:
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    )
    with key:
        return str(winreg.QueryValueEx(key, "ProcessorNameString")[0])


def _sensor_readings() -> dict:
    # Not implemented by psutil on Windows and macOS.
    read = getattr(psutil, "sensors_temperatures", None)
    return read() if read is not None else {}


def _cpu_temperature(sensors: dict) -> float | None:
    for name in _CPU_SENSORS:
        readings = sensors.get(name)
        if not readings:
            continue
        for reading in readings:
            # The whole-package reading, rather than one core's.
            if reading.label.startswith(("Package", "Tctl", "Tdie")):
                return float(reading.current)
        return float(readings[0].current)
    return None


def _temperatures(sensors: dict) -> tuple[TemperatureInfo, ...]:
    return tuple(
        TemperatureInfo(sensor=name, label=reading.label or "", celsius=float(reading.current))
        for name, readings in sensors.items()
        for reading in readings
    )


def _memory() -> MemoryInfo:
    memory = psutil.virtual_memory()
    try:
        swap = psutil.swap_memory()
        swap_values = (int(swap.total), int(swap.used), float(swap.percent))
    except (OSError, RuntimeError):
        swap_values = (0, 0, 0.0)
    return MemoryInfo(
        total_bytes=int(memory.total),
        # "Used" means "not available": psutil's own `used` leaves out
        # different things on each system and would not match `percent`.
        used_bytes=int(memory.total - memory.available),
        available_bytes=int(memory.available),
        percent=float(memory.percent),
        swap_total_bytes=swap_values[0],
        swap_used_bytes=swap_values[1],
        swap_percent=swap_values[2],
    )


def _is_reported_mount(partition: object) -> bool:
    device, mount = partition.device, partition.mountpoint
    if partition.fstype.lower() in _IGNORED_FILESYSTEMS or device.startswith("/dev/loop"):
        return False
    # macOS splits the system disk into helper volumes; only Data holds files.
    if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
        return False
    return True


def _storage() -> tuple[StorageInfo, ...]:
    mounts: list[StorageInfo] = []
    for partition in psutil.disk_partitions(all=False):
        if not _is_reported_mount(partition):
            continue
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            # An empty card reader or optical drive, or no permission.
            continue
        if usage.total <= 0:
            continue
        mounts.append(
            StorageInfo(
                mount=partition.mountpoint,
                device=partition.device,
                fstype=partition.fstype,
                total_bytes=int(usage.total),
                used_bytes=int(usage.used),
                free_bytes=int(usage.free),
                percent=float(usage.percent),
            )
        )
    return tuple(sorted(mounts, key=lambda mount: mount.mount))


def _network() -> tuple[NetworkInfo, ...]:
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    interfaces: list[NetworkInfo] = []
    for name, entries in sorted(psutil.net_if_addrs().items()):
        addresses = tuple(
            # IPv6 link-local addresses carry a "%interface" scope suffix.
            entry.address.partition("%")[0]
            for entry in entries
            if entry.family in (socket.AF_INET, socket.AF_INET6)
        )
        if any(address in ("127.0.0.1", "::1") for address in addresses):
            continue
        counter = counters.get(name)
        interfaces.append(
            NetworkInfo(
                name=name,
                is_up=bool(stats[name].isup) if name in stats else False,
                addresses=addresses,
                bytes_sent=int(counter.bytes_sent) if counter else 0,
                bytes_received=int(counter.bytes_recv) if counter else 0,
            )
        )
    return tuple(interfaces)


def _battery() -> BatteryInfo | None:
    read = getattr(psutil, "sensors_battery", None)
    battery = read() if read is not None else None
    if battery is None:
        return None
    seconds = battery.secsleft
    return BatteryInfo(
        percent=float(battery.percent),
        plugged=battery.power_plugged,
        # psutil uses negative sentinels for "unknown" and "charging".
        seconds_left=int(seconds) if isinstance(seconds, (int, float)) and seconds >= 0 else None,
    )


def _renumber(gpus: list[GpuInfo]) -> tuple[GpuInfo, ...]:
    return tuple(replace(gpu, index=index) for index, gpu in enumerate(gpus))


class StatusSampler:
    """Takes readings, remembering what does not change between them."""

    def __init__(self) -> None:
        self._system = platform.system()
        self._cpu_model: str | None = None
        self._pci_names: dict[str, str] | None = None
        self._os_gpus: list[GpuInfo] | None = None
        self._nvidia_smi = shutil.which("nvidia-smi")

    def sample(self, interval: float) -> Status:
        """Read the machine, measuring CPU usage over ``interval`` seconds."""
        per_core = tuple(
            float(value) for value in psutil.cpu_percent(interval=interval, percpu=True)
        )
        now = datetime.now().astimezone()
        sensors = _attempt(_sensor_readings, {})
        return Status(
            timestamp=now.isoformat(timespec="seconds"),
            host=self._host(now.timestamp()),
            cpu=self._cpu(per_core, sensors),
            memory=_memory(),
            storage=_attempt(_storage, ()),
            gpus=_attempt(self._gpus, ()),
            network=_attempt(_network, ()),
            temperatures=_attempt(lambda: _temperatures(sensors), ()),
            battery=_attempt(_battery, None),
        )

    def _host(self, now: float) -> HostInfo:
        boot = psutil.boot_time()
        return HostInfo(
            hostname=socket.gethostname() or platform.node(),
            os=_attempt(_os_name, self._system or "Unknown"),
            kernel=_attempt(_kernel, ""),
            arch=platform.machine(),
            boot_time=_iso(boot),
            uptime_seconds=float(max(0, round(now - boot))),
        )

    def _cpu(self, per_core: tuple[float, ...], sensors: dict) -> CpuInfo:
        if self._cpu_model is None:
            self._cpu_model = _attempt(_cpu_model, "Unknown CPU")
        # Windows has no load average; psutil's emulation needs a warm-up.
        load = os.getloadavg() if hasattr(os, "getloadavg") else None
        frequency = _attempt(psutil.cpu_freq, None)
        return CpuInfo(
            model=self._cpu_model,
            physical_cores=psutil.cpu_count(logical=False),
            logical_cores=psutil.cpu_count(logical=True),
            usage_percent=round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
            per_core_percent=per_core,
            load_average=None if load is None else tuple(round(value, 2) for value in load),
            frequency_mhz=round(frequency.current, 1) if frequency and frequency.current else None,
            temperature_c=_attempt(lambda: _cpu_temperature(sensors), None),
        )

    def _gpus(self) -> tuple[GpuInfo, ...]:
        nvidia = self._nvidia_gpus()
        if self._system == "Linux":
            if self._pci_names is None:
                self._pci_names = parse_lspci(_run(["lspci", "-mm", "-D"]) or "")
            # Read every time: AMD usage and memory change between readings.
            others = read_drm_gpus(Path("/sys/class/drm"), self._pci_names)
        else:
            if self._os_gpus is None:
                self._os_gpus = self._list_os_gpus()
            others = self._os_gpus
        if nvidia:
            others = [gpu for gpu in others if not _is_nvidia(gpu)]
        return _renumber([*nvidia, *others])

    def _nvidia_gpus(self) -> list[GpuInfo]:
        if self._nvidia_smi is None:
            return []
        output = _run(
            [
                self._nvidia_smi,
                f"--query-gpu={_NVIDIA_SMI_QUERY}",
                "--format=csv,noheader,nounits",
            ]
        )
        if output is None:
            # A broken driver makes every call wait for the timeout.
            self._nvidia_smi = None
            return []
        return parse_nvidia_smi(output)

    def _list_os_gpus(self) -> list[GpuInfo]:
        if self._system == "Windows":
            output = _run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object Name,AdapterCompatibility,DriverVersion | "
                    "ConvertTo-Json -Compress",
                ]
            )
            return parse_windows_video_controllers(output or "")
        if self._system == "Darwin":
            output = _run(["system_profiler", "SPDisplaysDataType", "-json"])
            return parse_macos_displays(output or "")
        return []


def _is_nvidia(gpu: GpuInfo) -> bool:
    return "nvidia" in f"{gpu.vendor or ''} {gpu.name}".lower()

"""Tests for how ``StatusSampler`` gathers and combines readings.

The operating system is faked through ``psutil`` and the command runner, so
these run the same on every platform.
"""

from __future__ import annotations

from collections import namedtuple
import socket
import unittest
from unittest import mock

from jzr_tools.core import host
from jzr_tools.core.host import GpuInfo, StatusSampler

Partition = namedtuple("Partition", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")
Address = namedtuple("Address", "family address")
NicStats = namedtuple("NicStats", "isup")
NicCounters = namedtuple("NicCounters", "bytes_sent bytes_recv")
Reading = namedtuple("Reading", "label current")
Battery = namedtuple("Battery", "percent secsleft power_plugged")

NVIDIA_OUTPUT = "0, NVIDIA GeForce RTX 4080, 580.105.08, 31, 79, 16376, 35, 17.63\n"
LSPCI_OUTPUT = (
    '0000:00:02.0 "VGA compatible controller" "Intel Corporation" '
    '"Alder Lake-S GT1 [UHD Graphics 770]" -r0c "Gigabyte" "Device d000"\n'
)
DRM_CARDS = [
    GpuInfo(index=0, name="NVIDIA GPU (10de:2704)", vendor="NVIDIA", driver="nvidia"),
    GpuInfo(index=1, name="Intel UHD Graphics 770", vendor="Intel", driver="i915"),
]


def patch(name: str, **kwargs: object):
    return mock.patch(f"jzr_tools.core.host.{name}", **kwargs)


class SamplerCase(unittest.TestCase):
    """Runs with a quiet, instant ``cpu_percent`` and no external commands."""

    system = "Linux"

    def setUp(self) -> None:
        self.commands: list[list[str]] = []
        self.outputs: dict[str, str | None] = {}
        for patcher in (
            patch("psutil.cpu_percent", return_value=[10.0, 30.0]),
            patch("platform.system", return_value=self.system),
            patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
            patch("_run", side_effect=self._run),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, command: list[str]) -> str | None:
        self.commands.append(command)
        program = command[0].rpartition("/")[2]
        return self.outputs.get(program)

    def ran(self, program: str) -> int:
        return sum(1 for command in self.commands if command[0].rpartition("/")[2] == program)


class GpuCombiningTests(SamplerCase):
    def test_nvidia_smi_cards_come_first_and_drm_adds_the_rest(self) -> None:
        self.outputs = {"nvidia-smi": NVIDIA_OUTPUT, "lspci": LSPCI_OUTPUT}
        with patch("read_drm_gpus", return_value=DRM_CARDS):
            gpus = StatusSampler().sample(0).gpus

        self.assertEqual(
            [(gpu.index, gpu.name) for gpu in gpus],
            [(0, "NVIDIA GeForce RTX 4080"), (1, "Intel UHD Graphics 770")],
        )
        self.assertEqual(gpus[0].utilization_percent, 31.0)

    def test_drm_keeps_nvidia_cards_when_nvidia_smi_is_missing(self) -> None:
        self.outputs = {"lspci": LSPCI_OUTPUT}
        with (
            patch("shutil.which", return_value=None),
            patch("read_drm_gpus", return_value=DRM_CARDS),
        ):
            gpus = StatusSampler().sample(0).gpus

        self.assertEqual([gpu.driver for gpu in gpus], ["nvidia", "i915"])
        self.assertEqual(self.ran("nvidia-smi"), 0)

    def test_drm_names_come_from_lspci(self) -> None:
        self.outputs = {"lspci": LSPCI_OUTPUT}
        with patch("read_drm_gpus", return_value=[]) as read:
            StatusSampler().sample(0)

        self.assertEqual(read.call_args.args[1], {"0000:00:02.0": "Intel UHD Graphics 770"})

    def test_failing_nvidia_smi_is_not_run_again(self) -> None:
        self.outputs = {"nvidia-smi": None}
        with patch("read_drm_gpus", return_value=DRM_CARDS):
            sampler = StatusSampler()
            first = sampler.sample(0).gpus
            second = sampler.sample(0).gpus

        self.assertEqual(self.ran("nvidia-smi"), 1)
        self.assertEqual([gpu.driver for gpu in first], ["nvidia", "i915"])
        self.assertEqual(second, first)

    def test_static_lookups_run_once_but_readings_are_fresh(self) -> None:
        self.outputs = {"nvidia-smi": NVIDIA_OUTPUT, "lspci": LSPCI_OUTPUT}
        with patch("read_drm_gpus", return_value=DRM_CARDS) as read:
            sampler = StatusSampler()
            for _ in range(3):
                sampler.sample(0)

        self.assertEqual(self.ran("lspci"), 1)
        self.assertEqual(self.ran("nvidia-smi"), 3)
        self.assertEqual(read.call_count, 3)

    def test_no_gpu_anywhere_gives_an_empty_tuple(self) -> None:
        with patch("shutil.which", return_value=None), patch("read_drm_gpus", return_value=[]):
            self.assertEqual(StatusSampler().sample(0).gpus, ())

    def test_a_crashing_gpu_reader_only_empties_the_gpu_section(self) -> None:
        with patch("read_drm_gpus", side_effect=RuntimeError("boom")):
            status = StatusSampler().sample(0)

        self.assertEqual(status.gpus, ())
        self.assertGreater(status.memory.total_bytes, 0)


class WindowsGpuTests(SamplerCase):
    system = "Windows"

    def test_controllers_are_listed_once_and_nvidia_is_not_repeated(self) -> None:
        self.outputs = {
            "nvidia-smi": NVIDIA_OUTPUT,
            "powershell.exe": (
                '[{"Name":"NVIDIA GeForce RTX 4080","AdapterCompatibility":"NVIDIA",'
                '"DriverVersion":"32.0.15.8010"},{"Name":"Intel(R) UHD Graphics 770",'
                '"AdapterCompatibility":"Intel Corporation","DriverVersion":"31.0.101.5186"}]'
            ),
        }
        sampler = StatusSampler()
        gpus = sampler.sample(0).gpus
        sampler.sample(0)

        self.assertEqual(
            [(gpu.index, gpu.name) for gpu in gpus],
            [(0, "NVIDIA GeForce RTX 4080"), (1, "Intel(R) UHD Graphics 770")],
        )
        self.assertEqual(gpus[0].driver, "580.105.08")
        self.assertEqual(self.ran("powershell.exe"), 1)
        self.assertEqual(self.ran("lspci"), 0)

    def test_powershell_failure_leaves_only_nvidia_smi_cards(self) -> None:
        self.outputs = {"nvidia-smi": NVIDIA_OUTPUT, "powershell.exe": None}

        gpus = StatusSampler().sample(0).gpus

        self.assertEqual([gpu.name for gpu in gpus], ["NVIDIA GeForce RTX 4080"])


class MacGpuTests(SamplerCase):
    system = "Darwin"

    def test_system_profiler_adapters_are_listed(self) -> None:
        self.outputs = {
            "system_profiler": (
                '{"SPDisplaysDataType":[{"sppci_model":"Apple M2 Pro",'
                '"spdisplays_vendor":"sppci_vendor_Apple"}]}'
            )
        }
        with patch("shutil.which", return_value=None):
            sampler = StatusSampler()
            gpus = sampler.sample(0).gpus
            sampler.sample(0)

        self.assertEqual([(gpu.name, gpu.vendor) for gpu in gpus], [("Apple M2 Pro", "Apple")])
        self.assertEqual(self.ran("system_profiler"), 1)


class StorageTests(SamplerCase):
    def _storage(self, partitions: list[Partition], usage: object):
        with (
            patch("psutil.disk_partitions", return_value=partitions),
            patch("psutil.disk_usage", side_effect=usage),
        ):
            return StatusSampler().sample(0).storage

    def test_snap_images_and_loop_devices_are_left_out(self) -> None:
        storage = self._storage(
            [
                Partition("/dev/nvme0n1p6", "/home", "btrfs", "rw"),
                Partition("/dev/loop3", "/var/lib/snapd/snap/core20/2686", "squashfs", "ro"),
                Partition("/dev/loop9", "/mnt/image", "ext4", "rw"),
                Partition("/dev/nvme0n1p6", "/", "btrfs", "rw"),
            ],
            lambda mount: Usage(1000, 250, 750, 25.0),
        )

        self.assertEqual([mount.mount for mount in storage], ["/", "/home"])
        self.assertEqual(
            (storage[0].total_bytes, storage[0].used_bytes, storage[0].free_bytes),
            (1000, 250, 750),
        )

    def test_unreadable_and_empty_mounts_are_skipped(self) -> None:
        def usage(mount: str) -> Usage:
            if mount == "D:\\":
                raise PermissionError("device is not ready")
            return Usage(0, 0, 0, 0.0) if mount == "E:\\" else Usage(500, 100, 400, 20.0)

        storage = self._storage(
            [
                Partition("C:\\", "C:\\", "NTFS", "rw,fixed"),
                Partition("D:\\", "D:\\", "", "cdrom"),
                Partition("E:\\", "E:\\", "", "removable"),
            ],
            usage,
        )

        self.assertEqual([mount.mount for mount in storage], ["C:\\"])

    def test_macos_helper_volumes_are_left_out_but_data_is_kept(self) -> None:
        storage = self._storage(
            [
                Partition("/dev/disk3s1s1", "/", "apfs", "ro"),
                Partition("/dev/disk3s6", "/System/Volumes/VM", "apfs", "rw"),
                Partition("/dev/disk3s2", "/System/Volumes/Preboot", "apfs", "rw"),
                Partition("/dev/disk3s5", "/System/Volumes/Data", "apfs", "rw"),
            ],
            lambda mount: Usage(1000, 250, 750, 25.0),
        )

        self.assertEqual([mount.mount for mount in storage], ["/", "/System/Volumes/Data"])

    def test_macos_asset_volumes_are_left_out(self) -> None:
        assets = "/System/Library/AssetsV2/com_apple_MobileAsset_Example/purpose_auto"
        storage = self._storage(
            [
                Partition("/dev/disk3s1s1", "/", "apfs", "ro"),
                Partition("/dev/disk5s1", f"{assets}/abc123.asset/AssetData", "apfs", "ro"),
                Partition("/dev/disk6s1", f"{assets}/def456.asset/AssetData", "apfs", "ro"),
                Partition("/dev/disk7s1", "/System/Library/AssetsV2", "apfs", "ro"),
                Partition("/dev/disk3s5", "/System/Volumes/Data", "apfs", "rw"),
            ],
            lambda mount: Usage(1000, 250, 750, 25.0),
        )

        self.assertEqual([mount.mount for mount in storage], ["/", "/System/Volumes/Data"])

    def test_mounts_that_only_resemble_the_asset_folder_are_kept(self) -> None:
        storage = self._storage(
            [
                Partition("/dev/disk3s1s1", "/", "apfs", "ro"),
                Partition("/dev/disk8s1", "/System/Library/AssetsV2x", "apfs", "rw"),
                Partition("/dev/disk9s1", "/Volumes/AssetsV2", "apfs", "rw"),
                Partition("/dev/disk10s1", "/System/Library/Other", "apfs", "rw"),
            ],
            lambda mount: Usage(1000, 250, 750, 25.0),
        )

        self.assertEqual(
            sorted(mount.mount for mount in storage),
            ["/", "/System/Library/AssetsV2x", "/System/Library/Other", "/Volumes/AssetsV2"],
        )

    def test_a_failing_partition_listing_empties_only_storage(self) -> None:
        with patch("psutil.disk_partitions", side_effect=OSError("nope")):
            status = StatusSampler().sample(0)

        self.assertEqual(status.storage, ())
        self.assertTrue(status.host.hostname)


class CpuAndSensorTests(SamplerCase):
    def test_usage_is_the_mean_of_the_cores_over_the_requested_window(self) -> None:
        with patch("psutil.cpu_percent", return_value=[10.0, 30.0]) as cpu_percent:
            cpu = StatusSampler().sample(1.5).cpu

        cpu_percent.assert_called_once_with(interval=1.5, percpu=True)
        self.assertEqual(cpu.per_core_percent, (10.0, 30.0))
        self.assertEqual(cpu.usage_percent, 20.0)

    def test_cpu_temperature_prefers_the_package_reading(self) -> None:
        sensors = {
            "nvme": [Reading("Composite", 37.0)],
            "coretemp": [Reading("Core 0", 61.0), Reading("Package id 0", 70.0)],
        }
        with patch("_sensor_readings", return_value=sensors):
            status = StatusSampler().sample(0)

        self.assertEqual(status.cpu.temperature_c, 70.0)
        self.assertEqual(
            [(t.sensor, t.label, t.celsius) for t in status.temperatures],
            [("nvme", "Composite", 37.0), ("coretemp", "Core 0", 61.0),
             ("coretemp", "Package id 0", 70.0)],
        )

    def test_no_sensors_means_no_temperatures(self) -> None:
        for readings in ({}, {"nvme": [Reading("Composite", 37.0)]}):
            with self.subTest(readings=readings), patch("_sensor_readings", return_value=readings):
                self.assertIsNone(StatusSampler().sample(0).cpu.temperature_c)

    def test_broken_sensors_do_not_break_the_reading(self) -> None:
        with patch("_sensor_readings", side_effect=OSError("no hwmon")):
            status = StatusSampler().sample(0)

        self.assertEqual(status.temperatures, ())
        self.assertIsNone(status.cpu.temperature_c)

    def test_missing_frequency_and_load_average_become_none(self) -> None:
        with (
            patch("psutil.cpu_freq", side_effect=NotImplementedError, create=True),
            patch("os", spec=[]),
        ):
            cpu = StatusSampler().sample(0).cpu

        self.assertIsNone(cpu.frequency_mhz)
        self.assertIsNone(cpu.load_average)

    def test_psutil_without_cpu_freq_reports_no_frequency(self) -> None:
        import psutil

        had_cpu_freq = hasattr(psutil, "cpu_freq")
        with mock.patch.dict(psutil.__dict__):
            psutil.__dict__.pop("cpu_freq", None)
            self.assertFalse(hasattr(psutil, "cpu_freq"))
            status = StatusSampler().sample(0)

        self.assertEqual(hasattr(psutil, "cpu_freq"), had_cpu_freq)
        self.assertIsNone(status.cpu.frequency_mhz)
        self.assertEqual(status.cpu.usage_percent, 20.0)

    def test_cpu_model_is_looked_up_once(self) -> None:
        with patch("_cpu_model", return_value="Test CPU") as model:
            sampler = StatusSampler()
            names = {sampler.sample(0).cpu.model for _ in range(3)}

        self.assertEqual(names, {"Test CPU"})
        model.assert_called_once()


class MemoryNetworkBatteryTests(SamplerCase):
    def test_used_memory_is_total_minus_available(self) -> None:
        memory = mock.Mock(total=1000, available=300, percent=70.0, used=123)
        swap = mock.Mock(total=400, used=100, percent=25.0)
        with (
            patch("psutil.virtual_memory", return_value=memory),
            patch("psutil.swap_memory", return_value=swap),
        ):
            info = StatusSampler().sample(0).memory

        self.assertEqual((info.total_bytes, info.used_bytes, info.available_bytes), (1000, 700, 300))
        self.assertEqual((info.swap_total_bytes, info.swap_used_bytes, info.swap_percent), (400, 100, 25.0))

    def test_unreadable_swap_is_reported_as_none_configured(self) -> None:
        with patch("psutil.swap_memory", side_effect=RuntimeError("no swap info")):
            info = StatusSampler().sample(0).memory

        self.assertEqual((info.swap_total_bytes, info.swap_used_bytes, info.swap_percent), (0, 0, 0.0))

    def test_interfaces_list_ip_addresses_and_skip_loopback(self) -> None:
        addresses = {
            "lo": [Address(socket.AF_INET, "127.0.0.1"), Address(socket.AF_INET6, "::1")],
            "eth0": [
                Address(socket.AF_INET, "192.168.1.20"),
                Address(socket.AF_INET6, "fe80::1%eth0"),
                Address(getattr(socket, "AF_PACKET", -1), "aa:bb:cc:dd:ee:ff"),
            ],
            "wlan0": [],
        }
        with (
            patch("psutil.net_if_addrs", return_value=addresses),
            patch("psutil.net_if_stats", return_value={"eth0": NicStats(True)}),
            patch("psutil.net_io_counters", return_value={"eth0": NicCounters(10, 20)}),
        ):
            network = StatusSampler().sample(0).network

        self.assertEqual([nic.name for nic in network], ["eth0", "wlan0"])
        self.assertEqual(network[0].addresses, ("192.168.1.20", "fe80::1"))
        self.assertEqual((network[0].is_up, network[0].bytes_sent, network[0].bytes_received), (True, 10, 20))
        self.assertEqual((network[1].is_up, network[1].bytes_sent, network[1].addresses), (False, 0, ()))

    def test_battery_sentinels_become_none(self) -> None:
        cases = [
            (Battery(64.0, -1, False), (64.0, False, None)),
            (Battery(100.0, -2, True), (100.0, True, None)),
            (Battery(50.0, 5400, False), (50.0, False, 5400)),
        ]
        for reading, expected in cases:
            with self.subTest(reading=reading), patch("psutil.sensors_battery", create=True, return_value=reading):
                battery = StatusSampler().sample(0).battery
                self.assertEqual((battery.percent, battery.plugged, battery.seconds_left), expected)

    def test_no_battery_is_none(self) -> None:
        with patch("psutil.sensors_battery", create=True, return_value=None):
            self.assertIsNone(StatusSampler().sample(0).battery)


class HostNamingTests(unittest.TestCase):
    def test_windows_11_is_recognised_by_its_build_number(self) -> None:
        cases = [("10", "10.0.22631", "Windows 11"), ("10", "10.0.19045", "Windows 10"),
                 ("11", "10.0.26100", "Windows 11")]
        for release, version, expected in cases:
            with (
                self.subTest(version=version),
                patch("platform.system", return_value="Windows"),
                patch("platform.release", return_value=release),
                patch("platform.version", return_value=version),
            ):
                self.assertEqual(host._os_name(), expected)

    def test_macos_and_unknown_linux_names(self) -> None:
        with (
            patch("platform.system", return_value="Darwin"),
            patch("platform.mac_ver", return_value=("14.5", ("", "", ""), "arm64")),
        ):
            self.assertEqual(host._os_name(), "macOS 14.5")
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.freedesktop_os_release", side_effect=OSError),
        ):
            self.assertEqual(host._os_name(), "Linux")


if __name__ == "__main__":
    unittest.main()

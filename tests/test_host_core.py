"""Acceptance tests for the pure parts of ``jzr_tools.core.host``."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from jzr_tools.core.host import (
    GpuInfo,
    parse_lspci,
    parse_macos_displays,
    parse_nvidia_smi,
    parse_windows_video_controllers,
    read_drm_gpus,
)

from host_fixtures import GIB, MIB, make_bare, make_full

NVIDIA_SAMPLE = (
    "0, NVIDIA GeForce RTX 4080, 580.105.08, 31, 79, 16376, 35, 17.63\n"
    "1, Tesla T4, 535.54.03, [N/A], 0, 15360, [Not Supported], [N/A]\n"
)

LSPCI_SAMPLE = (
    '0000:00:02.0 "VGA compatible controller" "Intel Corporation" '
    '"Alder Lake-S GT1 [UHD Graphics 770]" -r0c -p00 "Gigabyte Technology Co., Ltd" "Device d000"\n'
    '0000:01:00.0 "VGA compatible controller" "NVIDIA Corporation" '
    '"AD103 [GeForce RTX 4080]" -ra1 -p00 "Micro-Star International Co., Ltd. [MSI]" "Device 5112"\n'
    '0000:03:00.0 "VGA compatible controller" "Advanced Micro Devices, Inc. [AMD/ATI]" '
    '"Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]" -rc1 "Sapphire" "Device e437"\n'
    '0000:04:00.0 "Ethernet controller" "Realtek Semiconductor Co., Ltd." '
    '"RTL8125 2.5GbE Controller" -r05 "Gigabyte" "Device e000"\n'
)

WINDOWS_ARRAY = (
    '[{"Name":"NVIDIA GeForce RTX 4080","AdapterCompatibility":"NVIDIA","DriverVersion":"32.0.15.8010"},'
    '{"Name":"Intel(R) UHD Graphics 770","AdapterCompatibility":"Intel Corporation",'
    '"DriverVersion":"31.0.101.5186"}]'
)

MACOS_SAMPLE = json.dumps(
    {
        "SPDisplaysDataType": [
            {
                "_name": "Apple M2 Pro",
                "sppci_model": "Apple M2 Pro",
                "spdisplays_vendor": "sppci_vendor_Apple",
                "sppci_cores": "19",
            },
            {
                "_name": "AMD Radeon Pro 5500M",
                "sppci_model": "AMD Radeon Pro 5500M",
                "spdisplays_vendor": "sppci_vendor_amd",
                "spdisplays_vram": "8 GB",
            },
        ]
    }
)


class ParseNvidiaSmiTests(unittest.TestCase):
    def test_sample_output_gives_two_gpus_with_documented_values(self) -> None:
        gpus = parse_nvidia_smi(NVIDIA_SAMPLE)

        self.assertEqual(
            gpus[0],
            GpuInfo(
                index=0,
                name="NVIDIA GeForce RTX 4080",
                vendor="NVIDIA",
                driver="580.105.08",
                utilization_percent=31.0,
                memory_used_bytes=79 * MIB,
                memory_total_bytes=16376 * MIB,
                temperature_c=35.0,
                power_watts=17.63,
            ),
        )
        self.assertEqual(len(gpus), 2)

    def test_na_and_not_supported_fields_become_none(self) -> None:
        second = parse_nvidia_smi(NVIDIA_SAMPLE)[1]

        self.assertEqual(
            second,
            GpuInfo(
                index=1,
                name="Tesla T4",
                vendor="NVIDIA",
                driver="535.54.03",
                utilization_percent=None,
                memory_used_bytes=0,
                memory_total_bytes=15360 * MIB,
                temperature_c=None,
                power_watts=None,
            ),
        )

    def test_bare_na_and_empty_fields_become_none(self) -> None:
        (gpu,) = parse_nvidia_smi("0, Some GPU, 1.2.3, N/A, , 8192, N/A, N/A\n")

        self.assertEqual(gpu.name, "Some GPU")
        self.assertIsNone(gpu.utilization_percent)
        self.assertIsNone(gpu.memory_used_bytes)
        self.assertEqual(gpu.memory_total_bytes, 8192 * MIB)
        self.assertIsNone(gpu.temperature_c)
        self.assertIsNone(gpu.power_watts)

    def test_blank_lines_and_wrong_field_counts_are_skipped(self) -> None:
        text = (
            "\n"
            "0, NVIDIA GeForce RTX 4080, 580.105.08, 31, 79, 16376, 35, 17.63\n"
            "   \n"
            "this is not csv at all\n"
            "1, Too, Few, Fields\n"
            "2, Too, Many, 1, 2, 3, 4, 5, 6, 7\n"
            "\n"
        )

        gpus = parse_nvidia_smi(text)

        self.assertEqual([g.name for g in gpus], ["NVIDIA GeForce RTX 4080"])

    def test_empty_output_gives_empty_list(self) -> None:
        self.assertEqual(parse_nvidia_smi(""), [])


class ParseLspciTests(unittest.TestCase):
    def test_sample_gives_display_devices_with_short_names(self) -> None:
        self.assertEqual(
            parse_lspci(LSPCI_SAMPLE),
            {
                "0000:00:02.0": "Intel UHD Graphics 770",
                "0000:01:00.0": "NVIDIA GeForce RTX 4080",
                "0000:03:00.0": "AMD Radeon RX 6800/6800 XT / 6900 XT",
            },
        )

    def test_non_display_devices_are_left_out(self) -> None:
        self.assertNotIn("0000:04:00.0", parse_lspci(LSPCI_SAMPLE))

    def test_3d_and_display_classes_count_and_unknown_vendor_is_kept_as_given(self) -> None:
        text = (
            '0000:05:00.0 "3D controller" "NVIDIA Corporation" "GA100 [A100 PCIe]" -ra1\n'
            '0000:06:00.0 "Display controller" "Acme Graphics" "Frobnicator 9000" -r01\n'
        )

        result = parse_lspci(text)

        self.assertEqual(result["0000:05:00.0"], "NVIDIA A100 PCIe")
        self.assertEqual(result["0000:06:00.0"], "Acme Graphics Frobnicator 9000")

    def test_empty_output_gives_empty_dict(self) -> None:
        self.assertEqual(parse_lspci(""), {})


class DrmTreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "drm"
        self.root.mkdir()

    def make_card(
        self,
        name: str,
        uevent: str | None,
        files: dict[str, str] | None = None,
        hwmon: dict[str, str] | None = None,
    ) -> Path:
        card = self.root / name
        device = card / "device"
        device.mkdir(parents=True)
        if uevent is not None:
            (device / "uevent").write_text(uevent, encoding="utf-8")
        for rel, value in (files or {}).items():
            (device / rel).write_text(value, encoding="utf-8")
        if hwmon:
            hw = device / "hwmon" / "hwmon3"
            hw.mkdir(parents=True)
            for rel, value in hwmon.items():
                (hw / rel).write_text(value, encoding="utf-8")
        return card


AMD_UEVENT = "DRIVER=amdgpu\nPCI_CLASS=30000\nPCI_ID=1002:73BF\nPCI_SLOT_NAME=0000:03:00.0\n"


class ReadDrmGpusTests(DrmTreeCase):
    def test_amd_card_with_all_files_reports_every_measurement(self) -> None:
        self.make_card(
            "card0",
            AMD_UEVENT,
            files={
                "gpu_busy_percent": "42\n",
                "mem_info_vram_used": "1073741824\n",
                "mem_info_vram_total": "17163091968\n",
            },
            hwmon={"temp1_input": "54000\n", "power1_average": "120000000\n"},
        )

        (gpu,) = read_drm_gpus(self.root, {"0000:03:00.0": "AMD Radeon RX 6800"})

        self.assertEqual(
            gpu,
            GpuInfo(
                index=0,
                name="AMD Radeon RX 6800",
                vendor="AMD",
                driver="amdgpu",
                utilization_percent=42.0,
                memory_used_bytes=1073741824,
                memory_total_bytes=17163091968,
                temperature_c=54.0,
                power_watts=120.0,
            ),
        )

    def test_name_falls_back_to_vendor_and_lower_case_pci_id(self) -> None:
        self.make_card("card0", AMD_UEVENT)

        (gpu,) = read_drm_gpus(self.root)

        self.assertEqual(gpu.name, "AMD GPU (1002:73bf)")

    def test_name_falls_back_when_slot_is_not_in_the_names_mapping(self) -> None:
        self.make_card("card0", AMD_UEVENT)

        (gpu,) = read_drm_gpus(self.root, {"0000:99:00.0": "Something else"})

        self.assertEqual(gpu.name, "AMD GPU (1002:73bf)")

    def test_name_is_the_driver_when_there_is_no_pci_id(self) -> None:
        self.make_card("card0", "DRIVER=vkms\n")

        (gpu,) = read_drm_gpus(self.root)

        self.assertEqual(gpu.name, "vkms")
        self.assertEqual(gpu.driver, "vkms")
        self.assertIsNone(gpu.vendor)

    def test_vendor_ids_map_to_amd_intel_nvidia_or_none(self) -> None:
        cases = {
            "1002:73BF": "AMD",
            "8086:4688": "Intel",
            "10de:2704": "NVIDIA",
            "10DE:2704": "NVIDIA",
            "1234:abcd": None,
        }
        for pci_id, vendor in cases.items():
            with self.subTest(pci_id=pci_id):
                root = self.root / pci_id.replace(":", "_")
                root.mkdir()
                card = root / "card0" / "device"
                card.mkdir(parents=True)
                (card / "uevent").write_text(
                    f"DRIVER=x\nPCI_ID={pci_id}\nPCI_SLOT_NAME=0000:01:00.0\n", encoding="utf-8"
                )

                (gpu,) = read_drm_gpus(root, {"0000:01:00.0": "Named"})

                self.assertEqual(gpu.vendor, vendor)

    def test_missing_measurement_files_leave_fields_none(self) -> None:
        self.make_card("card0", "DRIVER=i915\nPCI_ID=8086:4688\nPCI_SLOT_NAME=0000:00:02.0\n")

        (gpu,) = read_drm_gpus(self.root, {"0000:00:02.0": "Intel UHD Graphics 770"})

        self.assertEqual(gpu.name, "Intel UHD Graphics 770")
        self.assertEqual(gpu.vendor, "Intel")
        self.assertEqual(gpu.driver, "i915")
        for field in (
            "utilization_percent",
            "memory_used_bytes",
            "memory_total_bytes",
            "temperature_c",
            "power_watts",
        ):
            with self.subTest(field=field):
                self.assertIsNone(getattr(gpu, field))

    def test_unreadable_numeric_content_leaves_field_none(self) -> None:
        self.make_card(
            "card0",
            AMD_UEVENT,
            files={"gpu_busy_percent": "not a number\n"},
            hwmon={"temp1_input": "\n"},
        )

        (gpu,) = read_drm_gpus(self.root)

        self.assertIsNone(gpu.utilization_percent)
        self.assertIsNone(gpu.temperature_c)

    def test_connector_directories_are_not_cards(self) -> None:
        self.make_card("card0", AMD_UEVENT)
        self.make_card("card0-DP-1", "DRIVER=amdgpu\nPCI_ID=1002:73BF\n")
        (self.root / "renderD128").mkdir()
        (self.root / "version").write_text("x", encoding="utf-8")

        gpus = read_drm_gpus(self.root)

        self.assertEqual(len(gpus), 1)

    def test_cards_come_back_in_card_number_order_with_indexes_from_zero(self) -> None:
        self.make_card("card10", "DRIVER=amdgpu\nPCI_ID=1002:0010\n")
        self.make_card("card2", "DRIVER=i915\nPCI_ID=8086:0002\n")
        self.make_card("card0", "DRIVER=nvidia\nPCI_ID=10de:0000\n")

        gpus = read_drm_gpus(self.root)

        self.assertEqual([g.driver for g in gpus], ["nvidia", "i915", "amdgpu"])
        self.assertEqual([g.index for g in gpus], [0, 1, 2])

    def test_card_without_uevent_is_skipped(self) -> None:
        self.make_card("card0", None)
        self.make_card("card1", "DRIVER=i915\nPCI_ID=8086:0002\n")

        gpus = read_drm_gpus(self.root)

        self.assertEqual([g.driver for g in gpus], ["i915"])

    def test_card_directory_without_device_directory_is_skipped(self) -> None:
        (self.root / "card0").mkdir()

        self.assertEqual(read_drm_gpus(self.root), [])

    def test_missing_drm_root_gives_empty_list(self) -> None:
        self.assertEqual(read_drm_gpus(self.root / "does-not-exist"), [])

    def test_empty_drm_root_gives_empty_list(self) -> None:
        self.assertEqual(read_drm_gpus(self.root), [])


class ParseWindowsVideoControllersTests(unittest.TestCase):
    def test_array_of_adapters(self) -> None:
        gpus = parse_windows_video_controllers(WINDOWS_ARRAY)

        self.assertEqual(
            [(g.name, g.vendor, g.driver) for g in gpus],
            [
                ("NVIDIA GeForce RTX 4080", "NVIDIA", "32.0.15.8010"),
                ("Intel(R) UHD Graphics 770", "Intel Corporation", "31.0.101.5186"),
            ],
        )

    def test_measurements_are_all_none(self) -> None:
        for gpu in parse_windows_video_controllers(WINDOWS_ARRAY):
            with self.subTest(name=gpu.name):
                self.assertIsNone(gpu.utilization_percent)
                self.assertIsNone(gpu.memory_used_bytes)
                self.assertIsNone(gpu.memory_total_bytes)
                self.assertIsNone(gpu.temperature_c)
                self.assertIsNone(gpu.power_watts)

    def test_single_adapter_is_a_bare_object_not_an_array(self) -> None:
        text = '{"Name":"Intel(R) UHD Graphics 770","AdapterCompatibility":"Intel Corporation","DriverVersion":"31.0.101.5186"}'

        (gpu,) = parse_windows_video_controllers(text)

        self.assertEqual(gpu.name, "Intel(R) UHD Graphics 770")
        self.assertEqual(gpu.vendor, "Intel Corporation")
        self.assertEqual(gpu.driver, "31.0.101.5186")

    def test_entries_without_a_name_are_skipped(self) -> None:
        text = (
            '[{"AdapterCompatibility":"X","DriverVersion":"1"},'
            '{"Name":null,"DriverVersion":"2"},'
            '{"Name":"Real GPU","AdapterCompatibility":"Y","DriverVersion":"3"}]'
        )

        gpus = parse_windows_video_controllers(text)

        self.assertEqual([g.name for g in gpus], ["Real GPU"])

    def test_empty_and_invalid_json_give_empty_list(self) -> None:
        for text in ("", "   \n", "not json", "{", "[{"):
            with self.subTest(text=text):
                self.assertEqual(parse_windows_video_controllers(text), [])


class ParseMacosDisplaysTests(unittest.TestCase):
    def test_sample_gives_names_and_vendors(self) -> None:
        gpus = parse_macos_displays(MACOS_SAMPLE)

        self.assertEqual([g.name for g in gpus], ["Apple M2 Pro", "AMD Radeon Pro 5500M"])
        self.assertEqual([g.vendor for g in gpus], ["Apple", "AMD"])

    def test_memory_is_parsed_from_vram_and_absent_when_not_reported(self) -> None:
        apple, amd = parse_macos_displays(MACOS_SAMPLE)

        self.assertIsNone(apple.memory_total_bytes)
        self.assertEqual(amd.memory_total_bytes, 8 * GIB)

    def test_shared_vram_in_megabytes(self) -> None:
        text = json.dumps(
            {
                "SPDisplaysDataType": [
                    {
                        "_name": "Intel UHD Graphics 630",
                        "sppci_model": "Intel UHD Graphics 630",
                        "spdisplays_vendor": "sppci_vendor_intel",
                        "spdisplays_vram_shared": "1536 MB",
                    }
                ]
            }
        )

        (gpu,) = parse_macos_displays(text)

        self.assertEqual(gpu.vendor, "Intel")
        self.assertEqual(gpu.memory_total_bytes, 1536 * MIB)

    def test_nvidia_vendor_is_upper_cased(self) -> None:
        text = json.dumps(
            {
                "SPDisplaysDataType": [
                    {"sppci_model": "GeForce GT 750M", "spdisplays_vendor": "sppci_vendor_nvidia"}
                ]
            }
        )

        (gpu,) = parse_macos_displays(text)

        self.assertEqual(gpu.vendor, "NVIDIA")

    def test_name_falls_back_to_underscore_name(self) -> None:
        text = json.dumps(
            {
                "SPDisplaysDataType": [
                    {"_name": "Fallback Name", "spdisplays_vendor": "sppci_vendor_Apple"}
                ]
            }
        )

        (gpu,) = parse_macos_displays(text)

        self.assertEqual(gpu.name, "Fallback Name")

    def test_empty_and_invalid_json_give_empty_list(self) -> None:
        for text in ("", "  ", "not json", "{"):
            with self.subTest(text=text):
                self.assertEqual(parse_macos_displays(text), [])


class StatusToDictTests(unittest.TestCase):
    def test_top_level_keys_are_exactly_the_documented_ones(self) -> None:
        self.assertEqual(
            set(make_full().to_dict()),
            {
                "timestamp",
                "host",
                "cpu",
                "memory",
                "storage",
                "gpus",
                "network",
                "temperatures",
                "battery",
            },
        )

    def test_nested_objects_use_exactly_the_documented_field_names(self) -> None:
        data = make_full().to_dict()

        self.assertEqual(
            set(data["host"]),
            {"hostname", "os", "kernel", "arch", "boot_time", "uptime_seconds"},
        )
        self.assertEqual(
            set(data["cpu"]),
            {
                "model",
                "physical_cores",
                "logical_cores",
                "usage_percent",
                "per_core_percent",
                "load_average",
                "frequency_mhz",
                "temperature_c",
            },
        )
        self.assertEqual(
            set(data["memory"]),
            {
                "total_bytes",
                "used_bytes",
                "available_bytes",
                "percent",
                "swap_total_bytes",
                "swap_used_bytes",
                "swap_percent",
            },
        )
        self.assertEqual(
            set(data["storage"][0]),
            {"mount", "device", "fstype", "total_bytes", "used_bytes", "free_bytes", "percent"},
        )
        self.assertEqual(
            set(data["gpus"][0]),
            {
                "index",
                "name",
                "vendor",
                "driver",
                "utilization_percent",
                "memory_used_bytes",
                "memory_total_bytes",
                "temperature_c",
                "power_watts",
            },
        )
        self.assertEqual(
            set(data["network"][0]),
            {"name", "is_up", "addresses", "bytes_sent", "bytes_received"},
        )
        self.assertEqual(set(data["temperatures"][0]), {"sensor", "label", "celsius"})
        self.assertEqual(set(data["battery"]), {"percent", "plugged", "seconds_left"})

    def test_values_come_through_unchanged(self) -> None:
        data = make_full().to_dict()

        self.assertEqual(data["timestamp"], "2026-11-13T12:00:00+00:00")
        self.assertEqual(data["host"]["hostname"], "atlas")
        self.assertEqual(data["host"]["uptime_seconds"], 3 * 86400 + 4 * 3600 + 12 * 60 + 30)
        self.assertEqual(data["cpu"]["logical_cores"], 20)
        self.assertEqual(data["cpu"]["usage_percent"], 37.4)
        self.assertEqual(data["memory"]["total_bytes"], round(31.1 * GIB))
        self.assertEqual(data["memory"]["swap_total_bytes"], 4 * GIB)
        self.assertEqual(
            data["storage"][2],
            {
                "mount": "/mnt/data",
                "device": "/dev/sda1",
                "fstype": "ext4",
                "total_bytes": 2 * 1024**4,
                "used_bytes": 1024**4,
                "free_bytes": 1024**4,
                "percent": 50.0,
            },
        )
        self.assertEqual(data["gpus"][1]["name"], "AMD Radeon RX 6800")
        self.assertEqual(data["gpus"][0]["power_watts"], 17.63)
        self.assertEqual(
            data["network"][0],
            {
                "name": "eth0",
                "is_up": True,
                "addresses": ["192.168.1.20", "fe80::1"],
                "bytes_sent": MIB,
                "bytes_received": 5 * MIB,
            },
        )
        self.assertEqual(
            data["temperatures"][1], {"sensor": "nvme", "label": "Composite", "celsius": 41.0}
        )
        self.assertEqual(
            data["battery"], {"percent": 64.0, "plugged": True, "seconds_left": None}
        )

    def test_tuples_become_lists(self) -> None:
        data = make_full().to_dict()

        self.assertIs(type(data["cpu"]["per_core_percent"]), list)
        self.assertEqual(data["cpu"]["per_core_percent"], [float(81 + i) for i in range(20)])
        self.assertIs(type(data["cpu"]["load_average"]), list)
        self.assertEqual(data["cpu"]["load_average"], [2.5, 3.5, 4.5])
        for key in ("storage", "gpus", "network", "temperatures"):
            with self.subTest(key=key):
                self.assertIs(type(data[key]), list)
        self.assertIs(type(data["network"][0]["addresses"]), list)

    def test_nested_dataclasses_become_plain_dicts(self) -> None:
        data = make_full().to_dict()

        for obj in (data["host"], data["cpu"], data["memory"], data["storage"][0], data["battery"]):
            self.assertIs(type(obj), dict)

    def test_result_is_json_serialisable_and_round_trips(self) -> None:
        for status in (make_full(), make_bare()):
            with self.subTest(host=status.host.hostname):
                data = status.to_dict()

                self.assertEqual(json.loads(json.dumps(data)), data)

    def test_none_stays_none_and_empty_collections_stay_empty(self) -> None:
        data = make_bare().to_dict()

        self.assertIsNone(data["cpu"]["load_average"])
        self.assertIsNone(data["cpu"]["frequency_mhz"])
        self.assertIsNone(data["cpu"]["temperature_c"])
        self.assertIsNone(data["cpu"]["physical_cores"])
        self.assertIsNone(data["battery"])
        self.assertEqual(data["gpus"], [])
        self.assertEqual(data["temperatures"], [])
        self.assertEqual(data["network"], [])
        self.assertEqual(data["memory"]["swap_total_bytes"], 0)
        self.assertIn('"battery": null', json.dumps(data))

    def test_gpu_optional_fields_default_to_none(self) -> None:
        gpu = GpuInfo(index=0, name="Mystery GPU")

        self.assertIsNone(gpu.vendor)
        self.assertIsNone(gpu.driver)
        self.assertIsNone(gpu.utilization_percent)
        self.assertIsNone(gpu.memory_used_bytes)
        self.assertIsNone(gpu.memory_total_bytes)
        self.assertIsNone(gpu.temperature_c)
        self.assertIsNone(gpu.power_watts)

    def test_data_classes_are_frozen(self) -> None:
        status = make_full()

        with self.assertRaises(dataclasses.FrozenInstanceError):
            status.timestamp = "x"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            status.gpus[0].name = "x"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

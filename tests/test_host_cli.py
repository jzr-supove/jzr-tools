"""Acceptance tests for ``jzr host status`` with data collection faked."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.commands.host import render_status

from host_fixtures import GIB, MIB, bar_fill, find_bars, make_bare, make_full

runner = CliRunner()

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

TOP_LEVEL_KEYS = {
    "timestamp",
    "host",
    "cpu",
    "memory",
    "storage",
    "gpus",
    "network",
    "temperatures",
    "battery",
}

# Facts about make_full() that the spec puts in the default view.
DEFAULT_FACTS = [
    "atlas",
    "Fedora Linux 42",
    "x86_64",
    "up 3d 4h 12m",
    "Intel Core i7-12700K",
    "37%",
    "72%",
    "22.5 / 31.1 GiB",
    "150.0 / 200.0 GiB",
    "75%",
    "/mnt/data",
    "1.0 / 2.0 TiB",
    "50%",
    "NVIDIA GeForce RTX 4080",
    "AMD Radeon RX 6800",
    "31%",
    "12%",
    "4.0 / 16.0 GiB",
    "2.0 / 8.0 GiB",
    "63°C",
    "52°C",
    "2.5",
    "3.5",
    "4.5",
]

# Facts that the spec lists as added by --detail.
DETAIL_ONLY_FACTS = [
    "6.15.10-200.fc42.x86_64",
    "2026-11-10",
    "Swap",
    "1.0 / 4.0 GiB",
    "25%",
    "8.5 GiB",
    "/home",
    "btrfs",
    "ext4",
    "/dev/nvme0n1p2",
    "/dev/sda1",
    "50.0 GiB",
    "Network",
    "eth0",
    "wlan0",
    "192.168.1.20",
    "fe80::1",
    "1.0 MiB",
    "5.0 MiB",
    "Temperatures",
    "Package id 0",
    "Composite",
    "58°C",
    "41°C",
    "Battery",
    "64%",
    "580.105.08",
    "amdgpu",
    "17.6",
    "93%",
    "99%",
]


def combined(result: object) -> str:
    stdout = getattr(result, "stdout", "")
    try:
        stderr = result.stderr
    except (AttributeError, ValueError):
        stderr = ""
    return f"{stdout}{stderr}"


@contextlib.contextmanager
def fake_sampler(*readings: object):
    """Patch the sampler; each item is returned (or raised, if an exception)."""
    with mock.patch("jzr_tools.commands.host.StatusSampler") as cls:
        cls.return_value.sample.side_effect = list(readings)
        yield cls


def run_status(status, *args: str):
    with fake_sampler(status) as cls:
        result = runner.invoke(app, ["host", "status", *args])
    return result, cls


def fills(text: str, ascii: bool = False) -> list[int]:
    """Filled cells of every 20-cell bar in the text, in reading order."""
    counts = []
    for line in text.splitlines():
        for _, cells in find_bars(line, ascii):
            counts.append(bar_fill(cells, ascii))
    return counts


class HelpTests(unittest.TestCase):
    def test_group_help_describes_the_group_and_lists_status(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result = runner.invoke(app, ["host", flag])

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertIn("Inspect and clean up this machine.", result.stdout)
                self.assertIn("status", result.stdout.split("Commands:", 1)[1])

    def test_command_help_lists_every_option(self) -> None:
        result = runner.invoke(app, ["host", "status", "-h"])

        self.assertEqual(result.exit_code, 0, combined(result))
        for option in ("--live", "-l", "--interval", "-n", "--detail", "-d", "--json"):
            with self.subTest(option=option):
                self.assertIn(option, result.stdout)

    def test_no_root_shortcut_is_added(self) -> None:
        for name in ("status", "hoststatus"):
            with self.subTest(name=name):
                result = runner.invoke(app, [name])

                self.assertEqual(result.exit_code, 2)


class DefaultTextViewTests(unittest.TestCase):
    def setUp(self) -> None:
        result, self.cls = run_status(make_full())
        self.result = result
        self.text = result.stdout

    def test_exits_zero_after_exactly_one_reading_with_the_short_window(self) -> None:
        self.assertEqual(self.result.exit_code, 0, combined(self.result))
        self.cls.return_value.sample.assert_called_once_with(0.25)

    def test_section_labels_are_present(self) -> None:
        for label in ("CPU", "Memory", "Storage", "GPU"):
            with self.subTest(label=label):
                self.assertIn(label, self.text)

    def test_default_facts_are_shown_in_the_documented_formats(self) -> None:
        for fact in DEFAULT_FACTS:
            with self.subTest(fact=fact):
                self.assertIn(fact, self.text)

    def test_percentages_are_whole_numbers(self) -> None:
        self.assertIn("72%", self.text)
        self.assertNotIn("72.4", self.text)
        self.assertNotIn("37.4", self.text)

    def test_usage_bars_are_20_characters_filled_from_the_left(self) -> None:
        found = [cells for line in self.text.splitlines() for _, cells in find_bars(line)]

        self.assertTrue(found, self.text)
        for cells in found:
            with self.subTest(bar=cells):
                self.assertEqual(len(cells), 20)
                self.assertIsNotNone(bar_fill(cells), "not filled from the left")
        # cpu 37.4% -> 7, RAM 72.4% -> 14, storage 75% -> 15 and 50% -> 10,
        # GPU 0 usage 31% -> 6 and memory 4/16 -> 5, GPU 1 usage 12% -> 2 and memory 2/8 -> 5
        self.assertEqual(fills(self.text), [7, 14, 15, 10, 6, 5, 2, 5])

    def test_each_storage_device_is_listed_once_at_its_shortest_mount(self) -> None:
        self.assertEqual(self.text.count("150.0 / 200.0 GiB"), 1)
        self.assertNotIn("/home", self.text)
        self.assertEqual(self.text.count("/dev/nvme0n1p2"), 0)

    def test_detail_only_content_is_not_shown(self) -> None:
        for fact in DETAIL_ONLY_FACTS:
            if fact in ("25%",):
                continue  # not distinctive enough to rule out by substring
            with self.subTest(fact=fact):
                self.assertNotIn(fact, self.text)

    def test_never_prints_none_or_null(self) -> None:
        self.assertNotIn("None", self.text)
        self.assertNotIn("null", self.text)

    def test_has_no_control_codes_when_not_a_terminal(self) -> None:
        self.assertNotIn("\x1b", self.text)

    def test_output_is_what_render_status_returns(self) -> None:
        expected = render_status(make_full(), detail=False, color=False)

        self.assertEqual(self.text.strip(), expected.strip())


class UptimeAndSizeFormatTests(unittest.TestCase):
    def test_uptime_wording_drops_leading_zero_units(self) -> None:
        cases = [
            (3 * 86400 + 4 * 3600 + 12 * 60 + 59, "up 3d 4h 12m"),
            (4 * 3600 + 12 * 60, "up 4h 12m"),
            (12 * 60 + 5, "up 12m"),
            (59, "up 0m"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                text = render_status(make_full(uptime=seconds))

                self.assertIn(expected, text)

    def test_leading_zero_units_do_not_appear(self) -> None:
        self.assertNotIn("up 0", render_status(make_full(uptime=4 * 3600 + 12 * 60)))
        self.assertNotIn("up 0", render_status(make_full(uptime=12 * 60)))
        self.assertNotIn("0d", render_status(make_full(uptime=4 * 3600 + 12 * 60)))

    def test_used_and_total_share_the_totals_unit(self) -> None:
        full = make_full()
        cases = [
            (1024, 2048, "1.0 / 2.0 KiB"),
            (256 * MIB, 512 * MIB, "256.0 / 512.0 MiB"),
            (512 * MIB, 1024 * MIB, "0.5 / 1.0 GiB"),
            (3 * GIB, 8 * GIB, "3.0 / 8.0 GiB"),
        ]
        for used, total, expected in cases:
            with self.subTest(expected=expected):
                memory = dataclasses.replace(full.memory, used_bytes=used, total_bytes=total)
                text = render_status(dataclasses.replace(full, memory=memory))

                self.assertIn(expected, text)


class DetailViewTests(unittest.TestCase):
    def test_detail_adds_the_detail_only_content(self) -> None:
        result, _ = run_status(make_full(), "--detail")

        self.assertEqual(result.exit_code, 0, combined(result))
        for fact in DETAIL_ONLY_FACTS:
            with self.subTest(fact=fact):
                self.assertIn(fact, result.stdout)

    def test_detail_removes_nothing_from_the_default_view(self) -> None:
        result, _ = run_status(make_full(), "--detail")

        for fact in DEFAULT_FACTS:
            with self.subTest(fact=fact):
                self.assertIn(fact, result.stdout)

    def test_detail_lists_every_mount_of_a_shared_device(self) -> None:
        result, _ = run_status(make_full(), "--detail")

        self.assertEqual(result.stdout.count("150.0 / 200.0 GiB"), 2)
        self.assertEqual(result.stdout.count("/dev/nvme0n1p2"), 2)

    def test_short_flag_is_the_same_as_the_long_one(self) -> None:
        long_result, _ = run_status(make_full(), "--detail")
        short_result, _ = run_status(make_full(), "-d")

        self.assertEqual(short_result.exit_code, 0, combined(short_result))
        self.assertEqual(short_result.stdout, long_result.stdout)

    def test_detail_view_never_prints_none_or_null(self) -> None:
        result, _ = run_status(make_full(), "--detail")

        self.assertNotIn("None", result.stdout)
        self.assertNotIn("null", result.stdout)

    def test_output_is_what_render_status_returns_with_detail(self) -> None:
        result, _ = run_status(make_full(), "--detail")

        expected = render_status(make_full(), detail=True, color=False)
        self.assertEqual(result.stdout.strip(), expected.strip())

    def test_detail_labels_for_sections_without_data_are_not_invented(self) -> None:
        result, _ = run_status(make_bare(), "--detail")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertNotIn("Battery", result.stdout)


class BareMachineTests(unittest.TestCase):
    def test_full_report_for_a_machine_with_almost_nothing_optional(self) -> None:
        for args in ((), ("--detail",)):
            with self.subTest(args=args):
                result, _ = run_status(make_bare(), *args)
                text = result.stdout

                self.assertEqual(result.exit_code, 0, combined(result))
                for label in ("CPU", "Memory", "Storage", "GPU"):
                    self.assertIn(label, text)
                for fact in (
                    "bare-host",
                    "Windows 11",
                    "AMD64",
                    "up 4h 12m",
                    "Generic CPU",
                    "5%",
                    "25%",
                    "2.0 / 8.0 GiB",
                    "100.0 / 500.0 GiB",
                    "20%",
                    "none detected",
                ):
                    with self.subTest(fact=fact):
                        self.assertIn(fact, text)

    def test_missing_values_are_left_out_not_printed_as_none(self) -> None:
        for args in ((), ("--detail",)):
            with self.subTest(args=args):
                text = run_status(make_bare(), *args)[0].stdout

                self.assertNotIn("None", text)
                self.assertNotIn("null", text)
                self.assertNotIn("nan", text.lower())
                self.assertNotIn("load", text.lower())
                self.assertNotIn("°C", text)

    def test_full_machine_with_gpus_does_not_say_none_detected(self) -> None:
        text = run_status(make_full())[0].stdout

        self.assertNotIn("none detected", text)

    def test_bars_follow_the_percentages(self) -> None:
        text = run_status(make_bare())[0].stdout

        # cpu 5% -> 1, RAM 25% -> 5, storage 20% -> 4
        self.assertEqual(fills(text), [1, 5, 4])


class JsonTests(unittest.TestCase):
    def test_output_parses_and_has_exactly_the_top_level_keys(self) -> None:
        result, _ = run_status(make_full(), "--json")

        self.assertEqual(result.exit_code, 0, combined(result))
        data = json.loads(result.stdout)
        self.assertEqual(set(data), TOP_LEVEL_KEYS)

    def test_values_round_trip_from_the_status(self) -> None:
        result, _ = run_status(make_full(), "--json")

        data = json.loads(result.stdout)
        self.assertEqual(data, json.loads(json.dumps(make_full().to_dict())))
        self.assertEqual(data["host"]["hostname"], "atlas")
        self.assertEqual(data["memory"]["total_bytes"], round(31.1 * GIB))
        self.assertEqual([s["mount"] for s in data["storage"]], ["/", "/home", "/mnt/data"])
        self.assertEqual(
            [g["name"] for g in data["gpus"]],
            ["NVIDIA GeForce RTX 4080", "AMD Radeon RX 6800"],
        )
        self.assertEqual(data["battery"]["percent"], 64.0)

    def test_has_no_control_codes_and_is_not_the_text_view(self) -> None:
        result, _ = run_status(make_full(), "--json")

        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("none detected", result.stdout)

    def test_alone_the_document_is_indented(self) -> None:
        result, _ = run_status(make_full(), "--json")

        self.assertGreater(len(result.stdout.strip().splitlines()), 20)
        self.assertRegex(result.stdout, r'\n +"host"')

    def test_detail_changes_nothing(self) -> None:
        plain, _ = run_status(make_full(), "--json")
        detailed, _ = run_status(make_full(), "--json", "--detail")

        self.assertEqual(detailed.exit_code, 0, combined(detailed))
        self.assertEqual(detailed.stdout, plain.stdout)

    def test_bare_machine_uses_null_and_empty_lists(self) -> None:
        result, _ = run_status(make_bare(), "--json")

        self.assertEqual(result.exit_code, 0, combined(result))
        data = json.loads(result.stdout)
        self.assertEqual(set(data), TOP_LEVEL_KEYS)
        self.assertEqual(data["gpus"], [])
        self.assertEqual(data["temperatures"], [])
        self.assertIsNone(data["battery"])
        self.assertIsNone(data["cpu"]["load_average"])
        self.assertIsNone(data["cpu"]["frequency_mhz"])
        self.assertEqual(data["memory"]["swap_total_bytes"], 0)

    def test_one_shot_json_uses_one_reading_with_the_short_window(self) -> None:
        _, cls = run_status(make_full(), "--json")

        cls.return_value.sample.assert_called_once_with(0.25)


class LiveTests(unittest.TestCase):
    def live(self, *args: str):
        first = make_full(hostname="first-host")
        second = make_full(hostname="second-host")
        with fake_sampler(first, second, KeyboardInterrupt()) as cls:
            result = runner.invoke(app, ["host", "status", *args])
        return result, cls, first, second

    def test_ctrl_c_ends_the_loop_cleanly_with_both_readings_printed(self) -> None:
        result, _, _, _ = self.live("--live")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertIsNone(result.exception)
        self.assertIn("first-host", result.stdout)
        self.assertIn("second-host", result.stdout)
        self.assertNotIn("Aborted", combined(result))
        self.assertNotIn("Traceback", combined(result))
        self.assertNotIn("KeyboardInterrupt", combined(result))

    def test_first_reading_uses_the_short_window_then_the_default_interval(self) -> None:
        _, cls, _, _ = self.live("--live")

        cls.assert_called_once_with()
        self.assertEqual(
            cls.return_value.sample.call_args_list,
            [mock.call(0.25), mock.call(2), mock.call(2)],
        )

    def test_custom_interval_is_passed_to_later_readings(self) -> None:
        for flag in ("-n", "--interval"):
            with self.subTest(flag=flag):
                result, cls, _, _ = self.live("-l", flag, "0.5")

                self.assertEqual(result.exit_code, 0, combined(result))
                self.assertEqual(
                    cls.return_value.sample.call_args_list,
                    [mock.call(0.25), mock.call(0.5), mock.call(0.5)],
                )

    def test_the_minimum_interval_is_accepted(self) -> None:
        result, cls, _, _ = self.live("--live", "--interval", "0.2")

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertEqual(
            cls.return_value.sample.call_args_list,
            [mock.call(0.25), mock.call(0.2), mock.call(0.2)],
        )

    def test_without_a_terminal_readings_are_appended_with_a_blank_line_between(self) -> None:
        result, _, first, second = self.live("--live")
        out = result.stdout

        self.assertNotIn("\x1b", out)
        one = render_status(first).strip("\n")
        two = render_status(second).strip("\n")
        start_one, start_two = out.find(one), out.find(two)
        self.assertGreaterEqual(start_one, 0, out)
        self.assertGreater(start_two, start_one)
        between = out[start_one + len(one) : start_two]
        self.assertEqual(between.strip(), "")
        self.assertGreaterEqual(between.count("\n"), 2)

    def test_live_with_detail_renders_the_detail_view(self) -> None:
        result, _, _, _ = self.live("--live", "--detail")

        self.assertEqual(result.stdout.count("Package id 0"), 2)

    def test_live_json_prints_one_compact_document_per_line(self) -> None:
        result, _, first, second = self.live("--live", "--json")

        self.assertEqual(result.exit_code, 0, combined(result))
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2, result.stdout)
        docs = [json.loads(line) for line in lines]
        self.assertEqual([d["host"]["hostname"] for d in docs], ["first-host", "second-host"])
        for doc in docs:
            self.assertEqual(set(doc), TOP_LEVEL_KEYS)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("Aborted", combined(result))

    def test_live_json_documents_match_the_readings(self) -> None:
        result, _, first, second = self.live("--live", "--json")

        docs = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(docs[0], json.loads(json.dumps(first.to_dict())))
        self.assertEqual(docs[1], json.loads(json.dumps(second.to_dict())))

    def test_interrupt_before_any_later_reading_still_exits_zero(self) -> None:
        with fake_sampler(make_full(hostname="only-host"), KeyboardInterrupt()):
            result = runner.invoke(app, ["host", "status", "--live"])

        self.assertEqual(result.exit_code, 0, combined(result))
        self.assertIn("only-host", result.stdout)
        self.assertNotIn("Aborted", combined(result))


class UsageErrorTests(unittest.TestCase):
    def assert_usage_error(self, *args: str) -> None:
        with fake_sampler(make_full(), make_full(), KeyboardInterrupt()) as cls:
            result = runner.invoke(app, ["host", "status", *args])

        self.assertEqual(result.exit_code, 2, combined(result))
        self.assertIn("Error", combined(result))
        cls.return_value.sample.assert_not_called()

    def test_interval_without_live_is_rejected(self) -> None:
        self.assert_usage_error("--interval", "5")
        self.assert_usage_error("-n", "5")
        self.assert_usage_error("--json", "--interval", "5")

    def test_interval_below_the_minimum_is_rejected(self) -> None:
        for value in ("0.19", "0.1", "0", "-1"):
            with self.subTest(value=value):
                self.assert_usage_error("--live", f"--interval={value}")

    def test_non_numeric_interval_is_rejected(self) -> None:
        self.assert_usage_error("--live", "--interval", "soon")

    def test_unknown_option_is_rejected(self) -> None:
        self.assert_usage_error("--bogus")


class RenderStatusTests(unittest.TestCase):
    def test_plain_output_has_no_escape_codes(self) -> None:
        for status in (make_full(), make_bare()):
            for detail in (False, True):
                with self.subTest(host=status.host.hostname, detail=detail):
                    self.assertNotIn("\x1b", render_status(status, detail=detail, color=False))

    def test_default_arguments_are_plain_and_not_detailed(self) -> None:
        text = render_status(make_full())

        self.assertNotIn("\x1b", text)
        self.assertNotIn("Network", text)

    def test_color_output_has_escape_codes(self) -> None:
        for detail in (False, True):
            with self.subTest(detail=detail):
                self.assertIn("\x1b", render_status(make_full(), detail=detail, color=True))

    def test_color_only_adds_escape_codes(self) -> None:
        for detail in (False, True):
            with self.subTest(detail=detail):
                coloured = render_status(make_full(), detail=detail, color=True)

                self.assertEqual(
                    ANSI.sub("", coloured), render_status(make_full(), detail=detail, color=False)
                )

    def _codes(self, memory_percent: float):
        from collections import Counter

        status = make_full()
        memory = dataclasses.replace(status.memory, percent=memory_percent)
        text = render_status(dataclasses.replace(status, memory=memory), color=True)
        return Counter(ANSI.findall(text))

    def test_colour_changes_at_70_and_90_percent(self) -> None:
        green, yellow, red = self._codes(50.0), self._codes(80.0), self._codes(95.0)

        self.assertNotEqual(green, yellow)
        self.assertNotEqual(yellow, red)
        self.assertNotEqual(green, red)

    def test_colour_thresholds_are_below_70_below_90_and_from_90(self) -> None:
        self.assertEqual(self._codes(69.0), self._codes(50.0))
        self.assertEqual(self._codes(70.0), self._codes(80.0))
        self.assertEqual(self._codes(89.0), self._codes(80.0))
        self.assertEqual(self._codes(90.0), self._codes(95.0))
        self.assertEqual(self._codes(100.0), self._codes(95.0))

    def test_no_literal_none_in_either_fixture(self) -> None:
        for status in (make_full(), make_bare()):
            for detail in (False, True):
                with self.subTest(host=status.host.hostname, detail=detail):
                    text = render_status(status, detail=detail)

                    self.assertNotIn("None", text)
                    self.assertNotIn("null", text)


class NoColorOnPipeTests(unittest.TestCase):
    def test_command_output_is_plain_when_stdout_is_not_a_terminal(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("NO_COLOR", None)
            result, _ = run_status(make_full(), "--detail")

        self.assertNotIn("\x1b", result.stdout)


if __name__ == "__main__":
    unittest.main()

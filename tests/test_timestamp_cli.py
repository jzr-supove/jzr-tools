"""CLI behaviour of `jzr timestamp`, with a fixed clock and a fixed local zone."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import timestamp as ts

STAMP = 1_795_236_000  # 2026-11-21T04:40:00Z
NOW_US = (STAMP + 2 * 3600) * 1_000_000  # two hours after STAMP
TASHKENT = ts.Zone("Asia/Tashkent", ZoneInfo("Asia/Tashkent"))

STANDARD = (
    "Unix seconds  1795236000\n"
    "Unix ms       1795236000000\n"
    "UTC           2026-11-21T04:40:00Z\n"
    "Local         2026-11-21T09:40:00+05:00 (Asia/Tashkent)\n"
    "Relative      2 hours ago\n"
)


class TimestampCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        env = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(Path(__file__).parent)})
        env.start()
        self.addCleanup(env.stop)
        for target, value in (
            ("jzr_tools.core.timestamp.now", NOW_US),
            ("jzr_tools.core.timestamp.local_zone", TASHKENT),
        ):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args: str):
        return self.runner.invoke(app, ["timestamp", *args])

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def assert_usage_error(self, result, text: str | None = None) -> None:
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "", self.describe(result))
        if text is not None:
            self.assertIn(text, result.stderr, self.describe(result))

    def assert_value_error(self, result, text: str) -> None:
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "", self.describe(result))
        self.assertIn(text, result.stderr, self.describe(result))


class LayoutTests(TimestampCliCase):
    def test_a_unix_time_prints_the_five_lines(self) -> None:
        result = self.run_cli("1795236000")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, STANDARD)

    def test_without_a_value_it_shows_the_current_time(self) -> None:
        result = self.run_cli()
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            "Unix seconds  1795243200\n"
            "Unix ms       1795243200000\n"
            "UTC           2026-11-21T06:40:00Z\n"
            "Local         2026-11-21T11:40:00+05:00 (Asia/Tashkent)\n"
            "Relative      now\n",
        )

    def test_a_negative_number_works_without_a_double_dash(self) -> None:
        result = self.run_cli("-86400")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Unix seconds  -86400")
        self.assertEqual(lines[1], "Unix ms       -86400000")
        self.assertEqual(lines[2], "UTC           1969-12-31T00:00:00Z")
        self.assertEqual(lines[3], "Local         1969-12-31T06:00:00+06:00 (Asia/Tashkent)")
        self.assertEqual(lines[4], "Relative      56 years ago")

    def test_a_future_time_is_relative_to_the_clock(self) -> None:
        result = self.run_cli(str(STAMP + 2 * 3600 + 3 * 86400))
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines()[4], "Relative      in 3 days")

    def test_a_large_number_is_read_as_milliseconds(self) -> None:
        result = self.run_cli("1795236000000")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, STANDARD)

    def test_unit_forces_the_reading(self) -> None:
        result = self.run_cli("1795236000", "--unit", "ms")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Unix seconds  1795236")
        self.assertEqual(lines[1], "Unix ms       1795236000")
        self.assertEqual(lines[2], "UTC           1970-01-21T18:40:36Z")

    def test_a_fraction_is_kept_in_every_line(self) -> None:
        result = self.run_cli("1795236000.125")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Unix seconds  1795236000.125")
        self.assertEqual(lines[1], "Unix ms       1795236000125")
        self.assertEqual(lines[2], "UTC           2026-11-21T04:40:00.125Z")
        self.assertEqual(lines[3], "Local         2026-11-21T09:40:00.125+05:00 (Asia/Tashkent)")

    def test_a_date_time_is_read_in_the_local_zone(self) -> None:
        result = self.run_cli("2026-11-21T09:40")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, STANDARD)

    def test_a_date_alone_is_local_midnight(self) -> None:
        result = self.run_cli("2026-11-21")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Unix seconds  1795201200")
        self.assertEqual(lines[2], "UTC           2026-11-20T19:00:00Z")
        self.assertEqual(lines[3], "Local         2026-11-21T00:00:00+05:00 (Asia/Tashkent)")

    def test_a_date_with_z_ignores_the_local_zone(self) -> None:
        result = self.run_cli("2026-11-21T04:40:00Z")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, STANDARD)


class ZoneTests(TimestampCliCase):
    def test_tz_shows_that_zone_under_the_label_zone(self) -> None:
        result = self.run_cli("1795236000", "--tz", "Europe/Berlin")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stdout,
            "Unix seconds  1795236000\n"
            "Unix ms       1795236000000\n"
            "UTC           2026-11-21T04:40:00Z\n"
            "Zone          2026-11-21T05:40:00+01:00 (Europe/Berlin)\n"
            "Relative      2 hours ago\n",
        )

    def test_utc_flag_shows_utc_under_the_label_zone(self) -> None:
        result = self.run_cli("1795236000", "--utc")
        self.assert_exit(result, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[3], "Zone          2026-11-21T04:40:00+00:00 (UTC)")
        self.assertFalse(any(line.startswith("Local") for line in lines))

    def test_tz_utc_is_the_same_as_the_utc_flag(self) -> None:
        self.assertEqual(
            self.run_cli("1795236000", "--tz", "UTC").stdout,
            self.run_cli("1795236000", "--utc").stdout,
        )

    def test_a_date_without_an_offset_is_read_in_the_given_zone(self) -> None:
        result = self.run_cli("2026-11-21T09:40", "--utc")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines()[0], "Unix seconds  1795254000")
        result = self.run_cli("2026-11-21T05:40", "--tz", "Europe/Berlin")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout.splitlines()[0], "Unix seconds  1795236000")

    def test_the_zone_option_may_come_before_the_value(self) -> None:
        result = self.run_cli("--tz", "Europe/Berlin", "1795236000")
        self.assert_exit(result, 0)
        self.assertIn("Zone          2026-11-21T05:40:00+01:00 (Europe/Berlin)", result.stdout)

    def test_the_same_local_time_is_fine_with_an_offset_in_a_repeated_hour(self) -> None:
        result = self.run_cli("2026-10-25T02:30+01:00", "--tz", "Europe/Berlin")
        self.assert_exit(result, 0)
        self.assertIn("Zone          2026-10-25T02:30:00+01:00 (Europe/Berlin)", result.stdout)


class FormatTests(TimestampCliCase):
    def test_format_prints_only_the_formatted_text(self) -> None:
        result = self.run_cli("1795236000", "--format", "%Y-%m-%d %H:%M")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "2026-11-21 09:40\n")

    def test_format_uses_the_chosen_zone(self) -> None:
        result = self.run_cli("1795236000", "--format", "%Y-%m-%d %H:%M", "--utc")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "2026-11-21 04:40\n")

    def test_format_without_a_value_uses_the_clock(self) -> None:
        result = self.run_cli("--format", "%H:%M")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "11:40\n")

    def test_a_format_the_system_refuses_is_a_usage_error(self) -> None:
        refusal = ts.TimestampError("The --format text is not valid on this system.")
        with mock.patch("jzr_tools.core.timestamp.strftime_text", side_effect=refusal):
            result = self.run_cli("1795236000", "--format", "%Y")
        self.assert_usage_error(result, "The --format text is not valid on this system.")


class JsonTests(TimestampCliCase):
    def test_json_has_the_five_keys_in_order(self) -> None:
        result = self.run_cli("1795236000", "--json")
        self.assert_exit(result, 0)
        document = json.loads(result.stdout)
        self.assertEqual(list(document), ["seconds", "milliseconds", "utc", "local", "zone"])
        self.assertEqual(
            document,
            {
                "seconds": 1795236000,
                "milliseconds": 1795236000000,
                "utc": "2026-11-21T04:40:00Z",
                "local": "2026-11-21T09:40:00+05:00",
                "zone": "Asia/Tashkent",
            },
        )

    def test_json_is_indented_by_two(self) -> None:
        result = self.run_cli("1795236000", "--json")
        self.assert_exit(result, 0)
        self.assertTrue(result.stdout.startswith('{\n  "seconds": 1795236000,\n'), result.stdout)
        self.assertTrue(result.stdout.endswith("}\n"))

    def test_whole_numbers_are_integers_in_json(self) -> None:
        document = json.loads(self.run_cli("1795236000", "--json").stdout)
        self.assertIs(type(document["seconds"]), int)
        self.assertIs(type(document["milliseconds"]), int)

    def test_fractions_make_a_number_a_float_in_json(self) -> None:
        document = json.loads(self.run_cli("1795236000.125", "--json").stdout)
        self.assertIs(type(document["seconds"]), float)
        self.assertEqual(document["seconds"], 1795236000.125)
        self.assertIs(type(document["milliseconds"]), int)
        self.assertEqual(document["milliseconds"], 1795236000125)

    def test_a_fraction_of_a_millisecond_makes_both_floats(self) -> None:
        document = json.loads(self.run_cli("1125.5", "--unit", "ms", "--json").stdout)
        self.assertEqual(document["seconds"], 1.1255)
        self.assertEqual(document["milliseconds"], 1125.5)
        self.assertIs(type(document["seconds"]), float)
        self.assertIs(type(document["milliseconds"]), float)

    def test_json_local_and_zone_follow_tz(self) -> None:
        document = json.loads(self.run_cli("1795236000", "--tz", "Europe/Berlin", "--json").stdout)
        self.assertEqual(document["local"], "2026-11-21T05:40:00+01:00")
        self.assertEqual(document["zone"], "Europe/Berlin")
        self.assertEqual(document["utc"], "2026-11-21T04:40:00Z")

    def test_json_has_no_relative_key_and_no_label_text(self) -> None:
        result = self.run_cli("1795236000", "--json")
        self.assertNotIn("Relative", result.stdout)
        self.assertNotIn("Unix seconds", result.stdout)


class UsageErrorTests(TimestampCliCase):
    def test_unit_without_a_value(self) -> None:
        self.assert_usage_error(self.run_cli("--unit", "s"), "--unit applies to a number.")

    def test_unit_with_a_date(self) -> None:
        self.assert_usage_error(
            self.run_cli("2026-11-21", "--unit", "ms"), "--unit applies to a number."
        )

    def test_unit_must_be_s_or_ms(self) -> None:
        self.assert_usage_error(self.run_cli("5", "--unit", "h"))

    def test_tz_with_utc(self) -> None:
        self.assert_usage_error(
            self.run_cli("1795236000", "--tz", "Europe/Berlin", "--utc"),
            "--utc is --tz UTC; give one of them.",
        )

    def test_format_with_json(self) -> None:
        self.assert_usage_error(
            self.run_cli("1795236000", "--format", "%Y", "--json"),
            "--format and --json do not combine.",
        )

    def test_unknown_zone(self) -> None:
        self.assert_usage_error(
            self.run_cli("1795236000", "--tz", "Mars/Olympus"),
            "Unknown time zone: Mars/Olympus. Use an IANA name such as Europe/Berlin.",
        )

    def test_usage_errors_are_plain_text_without_a_traceback(self) -> None:
        result = self.run_cli("1795236000", "--tz", "Mars/Olympus")
        self.assertNotIn("Traceback", result.stderr)


class ValueErrorTests(TimestampCliCase):
    def test_garbage_is_one_plain_line_that_does_not_echo_the_input(self) -> None:
        result = self.run_cli("hunter2-secret")
        self.assert_value_error(
            result,
            "Not a Unix time or an ISO 8601 date (for example 1795236000 or 2026-11-21T09:40).",
        )
        self.assertNotIn("hunter2", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_a_time_the_clocks_skip(self) -> None:
        self.assert_value_error(
            self.run_cli("2026-03-29T02:30", "--tz", "Europe/Berlin"),
            "2026-03-29T02:30:00 does not exist in Europe/Berlin: the clocks skip it.",
        )

    def test_a_time_that_happens_twice(self) -> None:
        self.assert_value_error(
            self.run_cli("2026-10-25T02:30", "--tz", "Europe/Berlin"),
            "2026-10-25T02:30:00 happens twice in Europe/Berlin; "
            "add an offset such as +02:00 or +01:00.",
        )

    def test_a_time_far_outside_the_years_1_to_9999(self) -> None:
        self.assert_value_error(
            self.run_cli("99999999999999999999", "--unit", "s"),
            "That time is outside the years 1 to 9999.",
        )

    def test_a_date_before_year_1_once_the_zone_is_applied(self) -> None:
        self.assert_value_error(
            self.run_cli("0001-01-01"), "That time is outside the years 1 to 9999."
        )

    def test_value_errors_print_nothing_in_json_mode_either(self) -> None:
        self.assert_value_error(
            self.run_cli("not-a-time", "--json"),
            "Not a Unix time or an ISO 8601 date",
        )

    def test_an_error_in_the_zone_is_a_value_error_not_a_usage_error(self) -> None:
        result = self.run_cli("2026-03-29T02:30", "--tz", "Europe/Berlin", "--format", "%Y")
        self.assert_exit(result, 1)


if __name__ == "__main__":
    unittest.main()

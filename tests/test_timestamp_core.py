"""Reading, converting and formatting instants in `jzr_tools.core.timestamp`."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from jzr_tools.core import timestamp as ts
from jzr_tools.core.timestamp import TimestampError, Zone

UTC = ts.zone_by_name("UTC")
TASHKENT = ts.zone_by_name("Asia/Tashkent")
BERLIN = ts.zone_by_name("Europe/Berlin")

# 2026-11-21T04:40:00Z
STAMP = 1_795_236_000
STAMP_US = STAMP * 1_000_000

GARBAGE = "Not a Unix time or an ISO 8601 date (for example 1795236000 or 2026-11-21T09:40)."
OUT_OF_RANGE = "That time is outside the years 1 to 9999."


class ZoneByNameTests(unittest.TestCase):
    def test_known_names_give_a_zone_with_that_name(self) -> None:
        for name in ("UTC", "Asia/Tashkent", "Europe/Berlin"):
            with self.subTest(name=name):
                zone = ts.zone_by_name(name)
                self.assertIsInstance(zone, Zone)
                self.assertEqual(zone.name, name)
                self.assertIsNotNone(zone.tzinfo)

    def test_the_zone_has_the_right_offset(self) -> None:
        summer = ts.zone_by_name("Europe/Berlin").tzinfo.utcoffset(
            datetime(2026, 7, 1)
        )
        self.assertEqual(summer, timedelta(hours=2))

    def test_unknown_name_is_refused_with_the_name_in_the_message(self) -> None:
        with self.assertRaises(TimestampError) as caught:
            ts.zone_by_name("Mars/Olympus")
        self.assertEqual(
            str(caught.exception),
            "Unknown time zone: Mars/Olympus. Use an IANA name such as Europe/Berlin.",
        )

    def test_malformed_name_is_refused_the_same_way(self) -> None:
        with self.assertRaises(TimestampError) as caught:
            ts.zone_by_name("../etc/passwd")
        self.assertEqual(
            str(caught.exception),
            "Unknown time zone: ../etc/passwd. Use an IANA name such as Europe/Berlin.",
        )


class LocalZoneTests(unittest.TestCase):
    def test_tz_variable_naming_an_iana_zone_wins(self) -> None:
        zone = ts.local_zone(environ={"TZ": "Asia/Tashkent"})
        self.assertEqual(zone.name, "Asia/Tashkent")
        self.assertIsNotNone(zone.tzinfo)
        self.assertEqual(
            zone.tzinfo.utcoffset(datetime(2026, 1, 1)),
            timedelta(hours=5),
        )

    def test_a_leading_colon_in_tz_is_dropped(self) -> None:
        zone = ts.local_zone(environ={"TZ": ":Europe/Berlin"})
        self.assertEqual(zone.name, "Europe/Berlin")
        self.assertIsNotNone(zone.tzinfo)

    def test_a_tz_value_zoneinfo_does_not_know_falls_through_to_some_zone(self) -> None:
        zone = ts.local_zone(environ={"TZ": "Not/AZone"})
        self.assertIsInstance(zone, Zone)
        self.assertTrue(zone.name)
        self.assertNotEqual(zone.name, "Not/AZone")

    def test_an_empty_environment_still_gives_a_zone(self) -> None:
        zone = ts.local_zone(environ={})
        self.assertIsInstance(zone, Zone)
        self.assertTrue(zone.name)


class ParseNumberTests(unittest.TestCase):
    def parse(self, text: str, unit: str | None = None, zone: Zone = UTC) -> int:
        return ts.parse_value(text, unit=unit, zone=zone)

    def test_a_whole_number_below_the_boundary_is_seconds(self) -> None:
        self.assertEqual(self.parse("1795236000"), STAMP_US)

    def test_the_largest_seconds_value_is_99999999999(self) -> None:
        self.assertEqual(self.parse("99999999999"), 99_999_999_999 * 1_000_000)

    def test_one_hundred_billion_is_the_first_milliseconds_value(self) -> None:
        self.assertEqual(self.parse("100000000000"), 100_000_000_000 * 1_000)

    def test_a_thirteen_digit_number_is_milliseconds(self) -> None:
        self.assertEqual(self.parse("1795236000000"), STAMP_US)

    def test_negative_numbers_use_the_same_boundary(self) -> None:
        self.assertEqual(self.parse("-86400"), -86_400_000_000)
        self.assertEqual(self.parse("-100000000000"), -100_000_000_000 * 1_000)
        # Just inside the boundary it is seconds, which is before year 1.
        with self.assertRaises(TimestampError) as caught:
            self.parse("-99999999999")
        self.assertEqual(str(caught.exception), OUT_OF_RANGE)

    def test_an_explicit_plus_sign_is_allowed(self) -> None:
        self.assertEqual(self.parse("+1795236000"), STAMP_US)

    def test_unit_s_forces_seconds_even_for_a_large_number(self) -> None:
        self.assertEqual(self.parse("100000000000", unit="s"), 100_000_000_000 * 1_000_000)

    def test_unit_ms_forces_milliseconds_even_for_a_small_number(self) -> None:
        self.assertEqual(self.parse("1795236000", unit="ms"), 1_795_236_000_000)
        self.assertEqual(self.parse("1000", unit="ms"), 1_000_000)

    def test_a_fraction_of_seconds_is_exact(self) -> None:
        self.assertEqual(self.parse("1.125"), 1_125_000)
        self.assertEqual(self.parse("1795236000.125"), STAMP_US + 125_000)
        self.assertEqual(self.parse("-0.5"), -500_000)

    def test_a_fraction_of_milliseconds_is_exact(self) -> None:
        self.assertEqual(self.parse("1125.5", unit="ms"), 1_125_500)
        self.assertEqual(self.parse("100000000000.5"), 100_000_000_000_500)

    def test_a_forced_fraction_is_read_in_that_unit(self) -> None:
        self.assertEqual(self.parse("1.5", unit="s"), 1_500_000)
        self.assertEqual(self.parse("1.5", unit="ms"), 1_500)

    def test_digits_beyond_the_microsecond_round_half_to_even(self) -> None:
        self.assertEqual(self.parse("1.0000005"), 1_000_000)
        self.assertEqual(self.parse("1.0000015"), 1_000_002)
        self.assertEqual(self.parse("1.0000016"), 1_000_002)
        self.assertEqual(self.parse("1.0000014"), 1_000_001)
        self.assertEqual(self.parse("0.0005", unit="ms"), 0)
        self.assertEqual(self.parse("0.0015", unit="ms"), 2)

    def test_the_zone_does_not_matter_for_a_number(self) -> None:
        self.assertEqual(self.parse("1795236000", zone=TASHKENT), STAMP_US)

    def test_a_number_outside_the_years_1_to_9999_is_refused(self) -> None:
        for text, unit in (
            ("9999999999999", "s"),
            ("-9999999999999", "s"),
            ("99999999999999999999", "s"),
            ("99999999999999999999", None),
        ):
            with self.subTest(text=text, unit=unit):
                with self.assertRaises(TimestampError) as caught:
                    self.parse(text, unit=unit)
                self.assertEqual(str(caught.exception), OUT_OF_RANGE)


class ParseDateTests(unittest.TestCase):
    def parse(self, text: str, zone: Zone = UTC, unit: str | None = None) -> int:
        return ts.parse_value(text, unit=unit, zone=zone)

    def test_a_date_alone_is_midnight_in_the_zone(self) -> None:
        self.assertEqual(self.parse("2026-11-21", UTC), 1_795_219_200 * 1_000_000)
        self.assertEqual(self.parse("2026-11-21", TASHKENT), 1_795_201_200 * 1_000_000)

    def test_a_naive_date_time_is_read_in_the_zone(self) -> None:
        self.assertEqual(self.parse("2026-11-21T09:40", TASHKENT), STAMP_US)
        self.assertEqual(self.parse("2026-11-21T09:40", UTC), 1_795_254_000 * 1_000_000)
        self.assertEqual(self.parse("2026-11-21T05:40:00", BERLIN), STAMP_US)

    def test_z_means_utc_whatever_the_zone(self) -> None:
        self.assertEqual(self.parse("2026-11-21T04:40:00Z", TASHKENT), STAMP_US)

    def test_an_offset_wins_over_the_zone(self) -> None:
        self.assertEqual(self.parse("2026-11-21T09:40:00+05:00", UTC), STAMP_US)
        self.assertEqual(self.parse("2026-11-21T00:40:00-04:00", BERLIN), STAMP_US)

    def test_fractions_of_a_second_are_kept(self) -> None:
        self.assertEqual(self.parse("2026-11-21T09:40:00.5+05:00", UTC), STAMP_US + 500_000)
        self.assertEqual(self.parse("2026-11-21T04:40:00.000001Z", UTC), STAMP_US + 1)

    def test_a_space_may_replace_the_t(self) -> None:
        self.assertEqual(self.parse("2026-11-21 09:40", TASHKENT), STAMP_US)

    def test_the_first_and_last_representable_instants(self) -> None:
        self.assertEqual(self.parse("0001-01-01T00:00:00Z"), -62_135_596_800 * 1_000_000)
        self.assertEqual(
            self.parse("9999-12-31T23:59:59Z"), 253_402_300_799 * 1_000_000
        )

    def test_a_date_that_is_before_year_1_in_utc_is_refused(self) -> None:
        for text, zone in (("0001-01-01", TASHKENT), ("0001-01-01T00:00:00+05:00", UTC)):
            with self.subTest(text=text):
                with self.assertRaises(TimestampError) as caught:
                    self.parse(text, zone)
                self.assertEqual(str(caught.exception), OUT_OF_RANGE)

    def test_a_date_that_is_after_year_9999_in_utc_is_refused(self) -> None:
        with self.assertRaises(TimestampError) as caught:
            self.parse("9999-12-31T23:59:59-05:00", UTC)
        self.assertEqual(str(caught.exception), OUT_OF_RANGE)

    def test_a_time_the_clocks_skip_is_named_not_moved(self) -> None:
        with self.assertRaises(TimestampError) as caught:
            self.parse("2026-03-29T02:30", BERLIN)
        self.assertEqual(
            str(caught.exception),
            "2026-03-29T02:30:00 does not exist in Europe/Berlin: the clocks skip it.",
        )

    def test_a_time_that_happens_twice_names_both_offsets_earlier_first(self) -> None:
        with self.assertRaises(TimestampError) as caught:
            self.parse("2026-10-25T02:30", BERLIN)
        self.assertEqual(
            str(caught.exception),
            "2026-10-25T02:30:00 happens twice in Europe/Berlin; "
            "add an offset such as +02:00 or +01:00.",
        )

    def test_the_same_times_with_an_offset_are_fine(self) -> None:
        first = self.parse("2026-10-25T02:30+02:00", BERLIN)
        second = self.parse("2026-10-25T02:30+01:00", BERLIN)
        self.assertEqual(second - first, 3_600 * 1_000_000)

    def test_times_next_to_the_changes_are_fine(self) -> None:
        self.assertEqual(
            self.parse("2026-03-29T01:59", BERLIN), 1_774_745_940 * 1_000_000
        )
        self.assertEqual(
            self.parse("2026-03-29T03:00", BERLIN), 1_774_746_000 * 1_000_000
        )

    def test_a_skipped_time_is_fine_in_a_zone_without_the_change(self) -> None:
        self.assertEqual(
            self.parse("2026-03-29T02:30", UTC), self.parse("2026-03-29T02:30Z", UTC)
        )


class ParseGarbageTests(unittest.TestCase):
    def assert_garbage(self, text: str) -> None:
        with self.assertRaises(TimestampError) as caught:
            ts.parse_value(text, unit=None, zone=UTC)
        self.assertEqual(str(caught.exception), GARBAGE)

    def test_text_that_is_neither_a_number_nor_a_date_is_refused(self) -> None:
        for text in ("tomorrow", "", "   ", "12:30", "2026-13-45", "1e3", "0x10", "1,5", "--5"):
            with self.subTest(text=text):
                self.assert_garbage(text)

    def test_the_message_never_contains_the_input(self) -> None:
        secret = "hunter2-token-9f3a"
        with self.assertRaises(TimestampError) as caught:
            ts.parse_value(secret, unit=None, zone=UTC)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))


class NowTests(unittest.TestCase):
    def test_now_is_whole_microseconds_close_to_the_clock(self) -> None:
        import time

        before = time.time_ns() // 1000
        value = ts.now()
        after = time.time_ns() // 1000
        self.assertIsInstance(value, int)
        self.assertLessEqual(before, value)
        self.assertLessEqual(value, after)


class NumberTextTests(unittest.TestCase):
    def test_seconds_text_whole_and_fractional(self) -> None:
        cases = {
            STAMP_US: "1795236000",
            1_125_000: "1.125",
            -500_000: "-0.5",
            0: "0",
            1: "0.000001",
            -86_400_000_000: "-86400",
            1_000_001: "1.000001",
            1_500_000: "1.5",
        }
        for micros, expected in cases.items():
            with self.subTest(micros=micros):
                self.assertEqual(ts.seconds_text(micros), expected)

    def test_millis_text_whole_and_fractional(self) -> None:
        cases = {
            STAMP_US: "1795236000000",
            1_125_000: "1125",
            1_125_500: "1125.5",
            0: "0",
            1: "0.001",
            -1: "-0.001",
            -1_500: "-1.5",
            -86_400_000_000: "-86400000",
        }
        for micros, expected in cases.items():
            with self.subTest(micros=micros):
                self.assertEqual(ts.millis_text(micros), expected)

    def test_no_exponent_for_very_large_or_very_small_values(self) -> None:
        big = 253_402_300_799 * 1_000_000
        self.assertEqual(ts.seconds_text(big), "253402300799")
        self.assertEqual(ts.millis_text(big), "253402300799000")
        self.assertEqual(ts.millis_text(10_000_000_000_000_000), "10000000000000")
        for text in (ts.seconds_text(1), ts.millis_text(1), ts.seconds_text(big)):
            self.assertNotIn("E", text.upper())


class DateTextTests(unittest.TestCase):
    def test_utc_text_without_a_fraction(self) -> None:
        self.assertEqual(ts.utc_text(STAMP_US), "2026-11-21T04:40:00Z")
        self.assertEqual(ts.utc_text(0), "1970-01-01T00:00:00Z")

    def test_utc_text_shows_a_fraction_without_trailing_zeros(self) -> None:
        self.assertEqual(ts.utc_text(STAMP_US + 125_000), "2026-11-21T04:40:00.125Z")
        self.assertEqual(ts.utc_text(STAMP_US + 500_000), "2026-11-21T04:40:00.5Z")
        self.assertEqual(ts.utc_text(STAMP_US + 1), "2026-11-21T04:40:00.000001Z")

    def test_utc_text_before_1970(self) -> None:
        self.assertEqual(ts.utc_text(-1_000_000), "1969-12-31T23:59:59Z")
        self.assertEqual(ts.utc_text(-500_000), "1969-12-31T23:59:59.5Z")
        self.assertEqual(ts.utc_text(-86_400_000_000), "1969-12-31T00:00:00Z")

    def test_utc_text_at_the_ends_of_the_range(self) -> None:
        self.assertEqual(ts.utc_text(-62_135_596_800 * 1_000_000), "0001-01-01T00:00:00Z")
        self.assertEqual(ts.utc_text(253_402_300_799 * 1_000_000), "9999-12-31T23:59:59Z")

    def test_zoned_text_uses_the_offset_of_the_zone(self) -> None:
        self.assertEqual(ts.zoned_text(STAMP_US, TASHKENT), "2026-11-21T09:40:00+05:00")
        self.assertEqual(ts.zoned_text(STAMP_US, BERLIN), "2026-11-21T05:40:00+01:00")

    def test_zoned_text_follows_daylight_saving(self) -> None:
        july = 1_782_864_000 * 1_000_000  # 2026-07-01T00:00:00Z
        self.assertEqual(ts.zoned_text(july, BERLIN), "2026-07-01T02:00:00+02:00")

    def test_zoned_text_in_utc_uses_a_numeric_offset(self) -> None:
        self.assertEqual(ts.zoned_text(STAMP_US, UTC), "2026-11-21T04:40:00+00:00")

    def test_zoned_text_fraction_rules_match_utc_text(self) -> None:
        self.assertEqual(
            ts.zoned_text(STAMP_US + 125_000, TASHKENT), "2026-11-21T09:40:00.125+05:00"
        )
        self.assertEqual(
            ts.zoned_text(STAMP_US + 500_000, TASHKENT), "2026-11-21T09:40:00.5+05:00"
        )

    def test_zoned_text_before_1970_in_a_named_zone(self) -> None:
        self.assertEqual(
            ts.zoned_text(-86_400_000_000, TASHKENT), "1969-12-31T06:00:00+06:00"
        )

    def test_zoned_text_changes_the_date_when_the_offset_crosses_midnight(self) -> None:
        late = 1_795_219_200 * 1_000_000 - 1_000_000  # 2026-11-20T23:59:59Z
        self.assertEqual(ts.zoned_text(late, TASHKENT), "2026-11-21T04:59:59+05:00")


class RelativeTextTests(unittest.TestCase):
    NOW = STAMP_US + 10_000_000_000

    def past(self, seconds: float) -> str:
        return ts.relative_text(self.NOW - int(seconds * 1_000_000), self.NOW)

    def future(self, seconds: float) -> str:
        return ts.relative_text(self.NOW + int(seconds * 1_000_000), self.NOW)

    def test_less_than_a_second_either_way_is_now(self) -> None:
        self.assertEqual(ts.relative_text(self.NOW, self.NOW), "now")
        self.assertEqual(self.past(0.999999), "now")
        self.assertEqual(self.future(0.999999), "now")

    def test_seconds(self) -> None:
        self.assertEqual(self.past(1), "1 second ago")
        self.assertEqual(self.past(2), "2 seconds ago")
        self.assertEqual(self.past(59), "59 seconds ago")
        self.assertEqual(self.future(1), "in 1 second")
        self.assertEqual(self.future(45), "in 45 seconds")

    def test_minutes(self) -> None:
        self.assertEqual(self.past(60), "1 minute ago")
        self.assertEqual(self.past(119), "1 minute ago")
        self.assertEqual(self.past(120), "2 minutes ago")
        self.assertEqual(self.past(3599), "59 minutes ago")
        self.assertEqual(self.future(300), "in 5 minutes")

    def test_hours(self) -> None:
        self.assertEqual(self.past(3600), "1 hour ago")
        self.assertEqual(self.past(7199), "1 hour ago")
        self.assertEqual(self.past(7200), "2 hours ago")
        self.assertEqual(self.past(86399), "23 hours ago")
        self.assertEqual(self.future(3 * 3600), "in 3 hours")

    def test_days(self) -> None:
        day = 86_400
        self.assertEqual(self.past(day), "1 day ago")
        self.assertEqual(self.past(2 * day), "2 days ago")
        self.assertEqual(self.past(29 * day + 86_399), "29 days ago")
        self.assertEqual(self.future(3 * day), "in 3 days")
        self.assertEqual(self.future(day), "in 1 day")

    def test_months_are_thirty_days(self) -> None:
        day = 86_400
        self.assertEqual(self.past(30 * day), "1 month ago")
        self.assertEqual(self.past(59 * day), "1 month ago")
        self.assertEqual(self.past(60 * day), "2 months ago")
        self.assertEqual(self.past(364 * day), "12 months ago")
        self.assertEqual(self.future(90 * day), "in 3 months")
        self.assertEqual(self.future(30 * day), "in 1 month")

    def test_years_are_365_days(self) -> None:
        day = 86_400
        self.assertEqual(self.past(365 * day), "1 year ago")
        self.assertEqual(self.past(729 * day), "1 year ago")
        self.assertEqual(self.past(730 * day), "2 years ago")
        self.assertEqual(self.future(365 * day), "in 1 year")
        self.assertEqual(self.future(1000 * day), "in 2 years")

    def test_the_count_is_rounded_down_not_to_the_nearest(self) -> None:
        self.assertEqual(self.past(1.9), "1 second ago")
        self.assertEqual(self.past(119.9), "1 minute ago")
        self.assertEqual(self.future(7199.9), "in 1 hour")


class StrftimeTextTests(unittest.TestCase):
    def test_pattern_is_applied_in_the_zone(self) -> None:
        self.assertEqual(
            ts.strftime_text(STAMP_US, TASHKENT, "%Y-%m-%d %H:%M"), "2026-11-21 09:40"
        )
        self.assertEqual(
            ts.strftime_text(STAMP_US, UTC, "%Y-%m-%d %H:%M"), "2026-11-21 04:40"
        )

    def test_literal_text_and_several_fields(self) -> None:
        self.assertEqual(
            ts.strftime_text(STAMP_US, BERLIN, "day %d of %B %Y"), "day 21 of November 2026"
        )

    def test_a_pattern_without_fields_is_returned_as_is(self) -> None:
        self.assertEqual(ts.strftime_text(STAMP_US, UTC, "plain"), "plain")

    def test_the_zone_offset_field(self) -> None:
        self.assertEqual(ts.strftime_text(STAMP_US, TASHKENT, "%z"), "+0500")


if __name__ == "__main__":
    unittest.main()

"""Unix times and dates: reading them, time zones, and writing them out.

An instant is a whole number of Unix microseconds. Only the years 1 to 9999
can be shown, as Python's datetime allows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
import os
import re
import time

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
# From this size on a bare number is read as milliseconds (year 5138 in seconds).
MS_THRESHOLD = 100_000_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MIN = int((datetime(1, 1, 1, tzinfo=timezone.utc) - _EPOCH) / timedelta(microseconds=1))
_MAX = int(
    (datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc) - _EPOCH)
    / timedelta(microseconds=1)
)

NOT_A_TIME = (
    "Not a Unix time or an ISO 8601 date (for example 1795236000 or 2026-11-21T09:40)."
)
OUT_OF_RANGE = "That time is outside the years 1 to 9999."
NO_OLD_LOCAL = "The system cannot show local time before 1970; use --tz."
BAD_FORMAT = "The --format text is not valid on this system."


class TimestampError(ValueError):
    """The value or zone cannot be used; the text is for the user."""


@dataclass(frozen=True)
class Zone:
    """A time zone by name; ``tzinfo`` None is the system's local time."""

    name: str
    tzinfo: tzinfo | None


def zone_by_name(name: str) -> Zone:
    """Return the IANA zone ``name`` (``UTC`` included)."""
    found = _zoneinfo(name)
    if found is None:
        raise TimestampError(
            f"Unknown time zone: {name}. Use an IANA name such as Europe/Berlin."
        )
    return Zone(name, found)


def _zoneinfo(name: str) -> tzinfo | None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not name or name.startswith(("/", "\\")) or ".." in name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def local_zone(environ: Mapping[str, str] | None = None) -> Zone:
    """Return the local time zone, by its IANA name when it can be found.

    TZ first, then the zone /etc/localtime links to. Otherwise the system's
    own local time, named as the system names it (on Windows, for example
    "West Asia Standard Time").
    """
    environ = os.environ if environ is None else environ
    configured = environ.get("TZ", "").lstrip(":")
    if configured:
        found = _zoneinfo(configured)
        if found is not None:
            return Zone(configured, found)
    if os.name == "posix":
        try:
            target = os.path.realpath("/etc/localtime")
        except OSError:
            target = ""
        marker = "/zoneinfo/"
        if marker in target:
            key = target.split(marker, 1)[1]
            found = _zoneinfo(key)
            if found is not None:
                return Zone(key, found)
    name = time.localtime().tm_zone or time.tzname[0]
    return Zone(name, None)


def now() -> int:
    """Return the current time in Unix microseconds."""
    return time.time_ns() // 1000


def _check_range(micros: int) -> int:
    if not _MIN <= micros <= _MAX:
        raise TimestampError(OUT_OF_RANGE)
    return micros


def _datetime_utc(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=_check_range(micros))


def _micros_of(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(microseconds=1)


def is_number(text: str) -> bool:
    """Return whether ``text`` is read as a Unix time rather than a date."""
    return _NUMBER.fullmatch(text.strip()) is not None


def parse_value(text: str, *, unit: str | None, zone: Zone) -> int:
    """Read a Unix time or an ISO 8601 date into Unix microseconds.

    A number is seconds, or milliseconds when ``unit`` says so or, without
    a unit, when it is at least ``MS_THRESHOLD``. A date without an offset
    is in ``zone``; a local time the clocks skip or repeat is refused.
    """
    text = text.strip()
    if _NUMBER.fullmatch(text):
        try:
            value = Decimal(text)
        except InvalidOperation as error:  # pragma: no cover - the regex guards it
            raise TimestampError(NOT_A_TIME) from error
        if unit is None:
            unit = "ms" if abs(value) >= MS_THRESHOLD else "s"
        scale = 1_000 if unit == "ms" else 1_000_000
        micros = int((value * scale).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        return _check_range(micros)

    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise TimestampError(NOT_A_TIME) from error
    if moment.tzinfo is not None:
        try:
            return _check_range(_micros_of(moment))
        except OverflowError as error:
            raise TimestampError(OUT_OF_RANGE) from error
    return _check_range(_local_to_micros(moment, zone))


def _local_to_micros(naive: datetime, zone: Zone) -> int:
    """Place a wall-clock time in ``zone``, refusing skipped and repeated ones."""
    if zone.tzinfo is not None:
        early = naive.replace(tzinfo=zone.tzinfo, fold=0)
        late = naive.replace(tzinfo=zone.tzinfo, fold=1)

        def exists(candidate: datetime) -> bool:
            back = candidate.astimezone(timezone.utc).astimezone(zone.tzinfo)
            return back.replace(tzinfo=None) == naive

        try:
            if early.utcoffset() == late.utcoffset():
                return _micros_of(early)
            first, second = exists(early), exists(late)
        except OverflowError as error:
            raise TimestampError(OUT_OF_RANGE) from error
        if first and second:
            raise TimestampError(
                f"{naive.isoformat()} happens twice in {zone.name}; add an offset such as "
                f"{_offset_text(early.utcoffset())} or {_offset_text(late.utcoffset())}."
            )
        if not (first or second):
            raise TimestampError(f"{naive.isoformat()} does not exist in {zone.name}: the clocks skip it.")
        return _micros_of(early if first else late)

    # The system's own local time, through the C library, in whole seconds.
    whole = naive.replace(microsecond=0)
    try:
        early_ts = whole.replace(fold=0).timestamp()
        late_ts = whole.replace(fold=1).timestamp()
        exists_early = datetime.fromtimestamp(early_ts) == whole
        exists_late = datetime.fromtimestamp(late_ts) == whole
        early_off = datetime.fromtimestamp(early_ts, tz=timezone.utc).astimezone().utcoffset()
        late_off = datetime.fromtimestamp(late_ts, tz=timezone.utc).astimezone().utcoffset()
    except (OverflowError, OSError, ValueError) as error:
        raise TimestampError(NO_OLD_LOCAL) from error
    if early_ts != late_ts and exists_early and exists_late:
        raise TimestampError(
            f"{naive.isoformat()} happens twice in {zone.name}; add an offset such as "
            f"{_offset_text(early_off)} or {_offset_text(late_off)}."
        )
    if not (exists_early or exists_late):
        raise TimestampError(f"{naive.isoformat()} does not exist in {zone.name}: the clocks skip it.")
    offset = early_off if exists_early else late_off
    return _micros_of(naive.replace(tzinfo=timezone(offset or timedelta())))


def _offset_text(offset: timedelta | None) -> str:
    total = int((offset or timedelta()).total_seconds())
    sign = "-" if total < 0 else "+"
    hours, rest = divmod(abs(total), 3600)
    minutes, seconds = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}"
    return f"{text}:{seconds:02d}" if seconds else text


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def seconds_text(micros: int) -> str:
    """Write the instant as Unix seconds, exactly, without trailing zeros."""
    return _decimal_text(Decimal(micros) / 1_000_000)


def millis_text(micros: int) -> str:
    """Write the instant as Unix milliseconds, exactly, without trailing zeros."""
    return _decimal_text(Decimal(micros) / 1_000)


def _iso(moment: datetime) -> str:
    text = moment.isoformat(timespec="microseconds" if moment.microsecond else "seconds")
    if moment.microsecond:
        head, dot, tail = text.partition(".")
        digits = tail[:6].rstrip("0")
        text = f"{head}.{digits}{tail[6:]}"
    return text


def utc_text(micros: int) -> str:
    """Write the instant in UTC as ISO 8601 with a ``Z``."""
    return _iso(_datetime_utc(micros).replace(tzinfo=None)) + "Z"


def _in_zone(micros: int, zone: Zone) -> datetime:
    moment = _datetime_utc(micros)
    if zone.tzinfo is not None:
        try:
            return moment.astimezone(zone.tzinfo)
        except OverflowError as error:
            raise TimestampError(OUT_OF_RANGE) from error
    try:
        return moment.astimezone()
    except (OverflowError, OSError, ValueError) as error:
        raise TimestampError(NO_OLD_LOCAL) from error


def zoned_text(micros: int, zone: Zone) -> str:
    """Write the instant as ISO 8601 in ``zone``, with its offset."""
    return _iso(_in_zone(micros, zone))


_UNITS = (
    (365 * 86400, "year"),
    (30 * 86400, "month"),
    (86400, "day"),
    (3600, "hour"),
    (60, "minute"),
    (1, "second"),
)


def relative_text(micros: int, now: int) -> str:
    """Say how far the instant is from ``now``: "2 hours ago", "in 3 days"."""
    difference = micros - now
    seconds = abs(difference) // 1_000_000
    if seconds < 1:
        return "now"
    for size, name in _UNITS:
        if seconds >= size:
            count = seconds // size
            words = f"{count} {name}" + ("" if count == 1 else "s")
            return f"in {words}" if difference > 0 else f"{words} ago"
    return "now"  # pragma: no cover - seconds >= 1 always matches


def strftime_text(micros: int, zone: Zone, pattern: str) -> str:
    """Format the instant in ``zone`` with ``strftime``."""
    moment = _in_zone(micros, zone)
    try:
        return moment.strftime(pattern)
    except ValueError as error:
        raise TimestampError(BAD_FORMAT) from error

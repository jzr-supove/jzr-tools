"""`jzr timestamp`: Unix time to dates and back."""

from __future__ import annotations

import json

import click


@click.command(
    "timestamp",
    context_settings={"help_option_names": ["-h", "--help"], "ignore_unknown_options": True},
    short_help="Convert between Unix time and dates.",
)
@click.argument("value", required=False)
@click.option(
    "--unit",
    type=click.Choice(["s", "ms"]),
    default=None,
    help="Read VALUE as seconds or milliseconds (default: guessed from its size).",
)
@click.option("--tz", "tz_name", default=None, metavar="ZONE", help="Show this IANA zone, e.g. Europe/Berlin.")
@click.option("--utc", is_flag=True, help="Show UTC; the same as --tz UTC.")
@click.option("--format", "pattern", default=None, metavar="STRFTIME", help="Print only this strftime format.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def timestamp_command(
    value: str | None,
    unit: str | None,
    tz_name: str | None,
    utc: bool,
    pattern: str | None,
    as_json: bool,
) -> None:
    """Convert between Unix time and dates.

    VALUE is Unix seconds or milliseconds (told apart by size unless --unit
    says), or an ISO 8601 date such as 2026-11-21T09:40 (local time unless it
    has an offset or --tz is given). Without VALUE: now.
    """
    from jzr_tools.core import timestamp as ts

    if tz_name is not None and utc:
        raise click.UsageError("--utc is --tz UTC; give one of them.")
    if pattern is not None and as_json:
        raise click.UsageError("--format and --json do not combine.")
    if unit is not None and (value is None or not ts.is_number(value)):
        raise click.UsageError("--unit applies to a number.")

    if utc:
        tz_name = "UTC"
    if tz_name is not None:
        try:
            zone = ts.zone_by_name(tz_name)
        except ts.TimestampError as error:
            raise click.UsageError(str(error)) from error
        label = "Zone"
    else:
        zone = ts.local_zone()
        label = "Local"

    current = ts.now()
    try:
        micros = current if value is None else ts.parse_value(value, unit=unit, zone=zone)
        if pattern is not None:
            try:
                click.echo(ts.strftime_text(micros, zone, pattern))
            except ts.TimestampError as error:
                if str(error) == ts.BAD_FORMAT:
                    raise click.UsageError(str(error)) from error
                raise
            return
        zoned = ts.zoned_text(micros, zone)
        utc_line = ts.utc_text(micros)
    except ts.TimestampError as error:
        click.echo(str(error), err=True)
        raise click.exceptions.Exit(1) from error

    if as_json:
        document = {
            "seconds": _number(ts.seconds_text(micros)),
            "milliseconds": _number(ts.millis_text(micros)),
            "utc": utc_line,
            "local": zoned,
            "zone": zone.name,
        }
        click.echo(json.dumps(document, indent=2, ensure_ascii=False))
        return

    rows = (
        ("Unix seconds", ts.seconds_text(micros)),
        ("Unix ms", ts.millis_text(micros)),
        ("UTC", utc_line),
        (label, f"{zoned} ({zone.name})"),
        ("Relative", ts.relative_text(micros, current)),
    )
    for name, text in rows:
        click.echo(f"{name:<14}{text}")


def _number(text: str) -> int | float:
    return float(text) if "." in text else int(text)

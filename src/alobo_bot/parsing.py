"""Shared coercion and time-formatting helpers.

The API payloads, the durable state files and the request query strings all hand
the bot loosely-typed values — numbers as strings or ``""``, timestamps as ISO
text or absent. Every module needs the same forgiving turn of that raw data into
a value or ``None``, and the clock helpers need the same minute arithmetic, so
those live here once rather than being re-implemented per module.

Everything is pure and stdlib-only, so this module sits at the bottom of the
import graph and can be used from any layer.
"""

from __future__ import annotations

import datetime as dt

MINUTES_PER_DAY = 24 * 60


def hhmm(minute: int) -> str:
    """Minutes past midnight as ``HH:MM``.

    Values past 24:00 keep their hour (``1500`` -> ``"25:00"``), since a tariff
    window that wraps midnight is labelled past the day's end; a caller that wants
    a clock time within the day reduces the minute first (``minute % MINUTES_PER_DAY``).
    """
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_dt(value: object) -> dt.datetime | None:
    """An ISO-8601 timestamp as a datetime; None when absent or unusable."""
    if value is None or value == "":
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def as_float(value: object) -> float | None:
    """A JSON number (or numeric string) as a float; None when absent or unusable."""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def as_int(value: object) -> int | None:
    """A JSON number as an int; None when absent or unusable."""
    number = as_float(value)
    return int(number) if number is not None else None

"""Court price math for a requested time window.

Prices are read from the table the booking is made under. A :class:`CoreType`
carries one generic table and, usually, a table per *tariff* ("đối tượng áp
dụng") under ``targets``; the tariff's table wins when one is picked, because the
type-level ``normalPrice`` is only a placeholder in most branches.

Each table prices in one of two ways:

* ``normalPriceOneTime`` — the flat hourly price for an online ("one time")
  booking, used whenever no special window applies.
* ``specialPrice`` entries — recurring time-of-day rates (e.g. 200 000đ for
  17:00-23:00), optionally restricted to certain weekdays via ``dateRangeWeek``.

The total for a window is the sum of hourly buckets, so a booking that straddles
a rate boundary is priced correctly on both sides.
"""

from __future__ import annotations

import datetime as dt

from .models import CoreType, PriceTable

MINUTES_PER_DAY = 24 * 60


class ClockError(ValueError):
    """A --from/--to value could not be parsed."""


def parse_clock(text: str) -> int:
    """Parse "18:30" / "18h30" / "1830" into minutes past midnight."""
    raw = text.strip().lower().replace("h", ":")
    if raw.isdigit() and len(raw) == 4:
        raw = f"{raw[:2]}:{raw[2:]}"
    hour_text, _, minute_text = raw.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except ValueError as exc:
        raise ClockError(f"invalid time {text!r} (expected HH:MM)") from exc
    if not (0 <= hour <= 24) or not (0 <= minute <= 59) or (hour == 24 and minute):
        raise ClockError(f"invalid time {text!r} (expected HH:MM within a day)")
    return hour * 60 + minute


def window_cost(
    core_type: CoreType,
    start_minute: int,
    end_minute: int,
    weekday: int,
    target: PriceTable | None = None,
) -> float:
    """Cost of booking *core_type* from *start_minute* to *end_minute* (exclusive).

    *weekday* is 1=Monday..7=Sunday (``datetime.isoweekday``). *target* is the
    tariff to price under; without one the type's generic table is used. Windows
    that end at or before their start are treated as crossing midnight.
    """
    if end_minute <= start_minute:
        end_minute += MINUTES_PER_DAY
    table = target if target is not None else core_type
    total = 0.0
    cursor = start_minute
    while cursor < end_minute:
        step = min(end_minute, cursor + 60)
        price = table.price_per_unit(cursor % MINUTES_PER_DAY, weekday)
        total += price * (step - cursor) / 60.0
        cursor = step
    return total


def window_bounds(day: dt.date, start_minute: int, end_minute: int) -> tuple[dt.datetime, dt.datetime]:
    """Materialise a window on *day* as concrete datetimes."""
    start = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=start_minute)
    end = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=end_minute)
    if end <= start:
        end += dt.timedelta(days=1)
    return start, end


def hours_between(start_minute: int, end_minute: int) -> float:
    if end_minute <= start_minute:
        end_minute += MINUTES_PER_DAY
    return (end_minute - start_minute) / 60.0


def clip_to_hours(
    start_minute: int,
    end_minute: int,
    hours: tuple[int, int],
) -> tuple[int, int] | None:
    """The part of a window inside a venue's bookable *hours*, or None if it misses.

    A branch only sells its opening hours, so an 18:00-24:00 request against a
    venue that shuts at 23:00 really means 18:00-23:00 — quoting a rate for the
    hour after closing overstates the price, and calling it free overstates
    availability. A window that wraps past midnight is compared in minutes from
    its own day, since the venue keeps the same hours on both sides of it.
    """
    if end_minute <= start_minute:
        end_minute += MINUTES_PER_DAY
    open_minute, close_minute = hours
    low, high = max(start_minute, open_minute), min(end_minute, close_minute)
    return (low, high) if low < high else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    from math import asin, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(a))

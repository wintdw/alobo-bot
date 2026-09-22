import datetime as dt

import pytest

from alobo_bot.pricing import ClockError, haversine_km, hours_between, parse_clock, window_bounds, window_cost
from alobo_bot.models import CoreType, SpecialPrice


def make_type(normal=100000.0, specials=None):
    return CoreType(
        id="pickleball",
        name="Pickleball",
        normal_price=normal,
        normal_price_one_time=normal,
        special_prices=specials or [],
    )


def test_parse_clock_accepts_common_shapes():
    assert parse_clock("18:30") == 18 * 60 + 30
    assert parse_clock("1830") == 18 * 60 + 30
    assert parse_clock("18h30") == 18 * 60 + 30
    assert parse_clock("0:00") == 0


def test_parse_clock_rejects_garbage():
    for bad in ("", "25:00", "12:99", "noon"):
        with pytest.raises(ClockError):
            parse_clock(bad)


def test_window_cost_flat_price_scales_with_hours():
    core = make_type(normal=100000)
    assert window_cost(core, 18 * 60, 21 * 60, weekday=1) == 300000
    assert window_cost(core, 18 * 60, 18 * 60 + 30, weekday=1) == 50000


def test_window_cost_applies_special_window():
    special = SpecialPrice(start_minute=5 * 60 + 30, end_minute=10 * 60,
                           price=50000, price_one_time=60000)
    core = make_type(normal=100000, specials=[special])
    # entirely inside the cheap window
    assert window_cost(core, 6 * 60, 8 * 60, weekday=1) == 120000
    # straddles the 10:00 boundary: 09:00-10:00 cheap, 10:00-11:00 normal
    assert window_cost(core, 9 * 60, 11 * 60, weekday=1) == 60000 + 100000


def test_window_cost_crosses_midnight():
    core = make_type(normal=100000)
    assert hours_between(22 * 60, 1 * 60) == 3.0
    assert window_cost(core, 22 * 60, 1 * 60, weekday=5) == 300000


def test_special_window_respects_weekday_mask():
    monday_only = SpecialPrice(start_minute=0, end_minute=24 * 60,
                               price=1000, price_one_time=1000, date_range_week="1")
    core = make_type(normal=100000, specials=[monday_only])
    assert window_cost(core, 18 * 60, 19 * 60, weekday=1) == 1000    # Monday
    assert window_cost(core, 18 * 60, 19 * 60, weekday=2) == 100000  # Tuesday


def test_window_bounds_crossing_midnight():
    start, end = window_bounds(dt.date(2026, 9, 25), 22 * 60, 1 * 60)
    assert start == dt.datetime(2026, 9, 25, 22, 0)
    assert end == dt.datetime(2026, 9, 26, 1, 0)


def test_haversine_known_distance():
    # Hanoi Opera House to Hoan Kiem Lake area, ~0.6 km
    distance = haversine_km(21.0245, 105.8576, 21.0285, 105.8542)
    assert 0.3 < distance < 0.8

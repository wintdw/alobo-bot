import datetime as dt

from alobo_bot.availability import (
    BOOKED,
    FREE,
    PARTIAL,
    UNKNOWN,
    court_availability,
    free_spans,
    label,
    spans_label,
)
from alobo_bot.models import Booking


def booking(core_id, hour, duration=120, minute=0):
    return Booking(id=f"b-{core_id}-{hour}", core_id=core_id, duration_min=duration,
                   start=dt.datetime(2026, 9, 23, hour, minute))


def window(start_hour, end_hour):
    return dt.datetime(2026, 9, 23, start_hour, 0), dt.datetime(2026, 9, 23, end_hour, 0)


def test_from_api_splits_a_group_booking_into_one_leg_per_court():
    legs = Booking.from_api({
        "id": "g1", "time": "2026-09-23T18:00:00.000", "duration": 120,
        "services": [
            {"serviceId": "pickleball_1", "startTime": "2026-09-23T18:00:00.000", "duration": 120},
            {"serviceId": "pickleball_2", "startTime": "2026-09-23T18:00:00.000", "duration": 120},
        ],
    })
    assert [(leg.core_id, leg.start.hour, leg.duration_min) for leg in legs] == [
        ("pickleball_1", 18, 120), ("pickleball_2", 18, 120),
    ]


def test_from_api_falls_back_to_the_booking_time_and_duration():
    legs = Booking.from_api({
        "id": "b", "time": "2026-09-23T19:00:00.000", "duration": 90,
        "services": [{"serviceId": "pickleball_3"}],
    })
    assert legs[0].start.hour == 19 and legs[0].duration_min == 90


def test_from_api_skips_services_without_a_court():
    assert Booking.from_api({"id": "b", "services": [{"duration": 60}]}) == []


def test_overlap_returns_the_clipped_shared_stretch():
    leg = booking("c1", 18)  # 18:00-20:00
    assert leg.overlap(*window(19, 21)) == (dt.datetime(2026, 9, 23, 19, 0),
                                            dt.datetime(2026, 9, 23, 20, 0))
    assert leg.overlap(*window(17, 19)) == (dt.datetime(2026, 9, 23, 18, 0),
                                            dt.datetime(2026, 9, 23, 19, 0))


def test_overlap_is_none_when_the_leg_only_touches_the_window_edges():
    leg = booking("c1", 18)  # 18:00-20:00
    assert leg.overlap(*window(20, 21)) is None  # starts exactly when we end
    assert leg.overlap(*window(17, 18)) is None  # ends exactly when we start
    assert leg.overlap(*window(8, 9)) is None


def test_free_spans_is_the_whole_window_when_nothing_books_it():
    assert free_spans([booking("c1", 9)], "c1", *window(18, 21)) == [window(18, 21)]
    assert free_spans([], "c1", *window(18, 21)) == [window(18, 21)]


def test_free_spans_reports_the_open_remainder_of_a_partly_booked_window():
    # 18:00-20:00 taken inside an 18:00-21:00 request leaves 20:00-21:00 open.
    assert free_spans([booking("c1", 18)], "c1", *window(18, 21)) == [
        (dt.datetime(2026, 9, 23, 20, 0), dt.datetime(2026, 9, 23, 21, 0)),
    ]


def test_free_spans_merges_overlapping_legs_and_keeps_the_gaps():
    # 18:00-20:00 and 19:00-21:00 leave only 20:00-21:00 from 18:00, so merge first.
    legs = [booking("c1", 18), booking("c1", 19, duration=120)]
    assert free_spans(legs, "c1", *window(18, 23)) == [
        (dt.datetime(2026, 9, 23, 21, 0), dt.datetime(2026, 9, 23, 23, 0)),
    ]


def test_free_spans_ignores_other_courts():
    assert free_spans([booking("c2", 18)], "c1", *window(18, 21)) == [window(18, 21)]


def test_court_availability_is_free_when_nothing_touches_the_window():
    assert court_availability([booking("c1", 9)], "c1", *window(18, 21))[0] == FREE
    assert court_availability([], "c1", *window(18, 21))[0] == FREE


def test_court_availability_is_booked_when_the_window_is_covered_end_to_end():
    # 18:00-20:00 + 20:00-22:00 covers the whole 18:00-21:00 request.
    legs = [booking("c1", 18), booking("c1", 20)]
    assert court_availability(legs, "c1", *window(18, 21)) == (BOOKED, [])


def test_court_availability_is_partial_and_names_the_free_span():
    status, spans = court_availability([booking("c1", 18)], "c1", *window(18, 21))
    assert status == PARTIAL
    assert spans_label(spans) == "20:00-21:00"


def test_court_availability_is_unknown_without_data():
    # A failed lookup (a date outside the branch's booking window) is not "booked".
    assert court_availability(None, "c1", *window(18, 21)) == (UNKNOWN, [])


def test_label_names_the_free_span_only_for_a_partial_court():
    spans = [(dt.datetime(2026, 9, 23, 20, 0), dt.datetime(2026, 9, 23, 21, 0))]
    assert label(FREE, spans) == "free"
    assert label(BOOKED) == "booked"
    assert label(UNKNOWN) == "unknown"
    assert label(None) == "?"
    assert label(PARTIAL, spans) == "partial 20:00-21:00"


def test_spans_label_lists_every_open_span():
    spans = [
        (dt.datetime(2026, 9, 23, 18, 0), dt.datetime(2026, 9, 23, 19, 0)),
        (dt.datetime(2026, 9, 23, 20, 0), dt.datetime(2026, 9, 23, 21, 0)),
    ]
    assert spans_label(spans) == "18:00-19:00, 20:00-21:00"

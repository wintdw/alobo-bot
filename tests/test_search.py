import copy
import datetime as dt

import pytest

from alobo_bot.api import ApiError
from alobo_bot.config import DEFAULTS
from alobo_bot.models import Booking, Branch, Core, CoreType, PriceTarget, SocialSession, SportType
from alobo_bot.search import (
    build_query,
    find_cheapest,
    normalize,
    parse_availability,
    parse_category,
    place_score,
)


class FakeClient:
    """In-memory stand-in for AloboClient (no network)."""

    def __init__(self, branches, cores, core_types, sessions=None, areas=None,
                 bookings=None, availability_fails=()):
        self._branches = branches
        self._cores = cores
        self._core_types = core_types
        self._sessions = sessions or {}
        self._areas = areas or {}
        self._bookings = bookings or {}          # branch_id -> [Booking]
        self._availability_fails = set(availability_fails)
        self.calls = {"cores": set(), "bookings": 0, "availability": []}

    def sport_types(self):
        return [
            SportType(key="badminton", name="Cầu lông", int_value=2),
            SportType(key="pickleball", name="Pickleball", int_value=5),
        ]

    def all_branches(self, page_size=100):
        return list(self._branches)

    def get_cores(self, branch_id):
        self.calls["cores"].add(branch_id)
        return list(self._cores.get(branch_id, [])), list(self._areas.get(branch_id, []))

    def get_core_types(self, branch_id):
        return list(self._core_types.get(branch_id, []))

    def get_onetime_bookings(self, branch_id, day):
        self.calls["availability"].append((branch_id, day))
        if branch_id in self._availability_fails:
            raise ApiError(f"HTTP 400 for get_onetime_bookings on {branch_id}")
        return list(self._bookings.get(branch_id, []))

    def branch_booking_search(self, *args, **kwargs):
        self.calls["bookings"] += 1
        return [(b, list(self._sessions[b.id])) for b in self._branches if b.id in self._sessions]


def branch(bid, name, address, sport=5, lat=None, lng=None):
    return Branch(id=bid, name=name, address=address, sport_type=sport,
                  latitude=lat, longitude=lng)


def pickle_court(cid, setting="pickleball"):
    return Core(id=cid, name=cid, setting=setting, sport_type=5)


def price_type(price=100000):
    return CoreType(id="pickleball", name="Pickleball", normal_price=price,
                    normal_price_one_time=price)


def tariff(target_id, name, price=110000, blocks=()):
    """A PriceTarget as get_core_types publishes one."""
    return PriceTarget.from_api(target_id, {
        "name": name, "price": price, "priceOneTime": price,
        "specialPrice": [dict(block) for block in blocks],
    })


def tariff_type(*targets, price=100000):
    return CoreType(id="pickleball", name="Pickleball", normal_price=price,
                    normal_price_one_time=price,
                    targets={target.id: target for target in targets})


def cfg():
    return copy.deepcopy(DEFAULTS)


def test_normalize_strips_diacritics():
    assert normalize("Cầu Giấy, Hà Nội") == "cau giay ha noi"


def test_place_score_counts_matching_tokens():
    assert place_score("Sân Phương Pickleball, Hà Nội", "ha noi pickleball") == 3
    assert place_score("Sân Đà Nẵng", "ha noi") == 0


def test_build_query_defaults_place_when_no_coords():
    query = build_query(cfg())
    assert query.place == DEFAULTS["search"]["default_place"]
    assert query.sport == "pickleball"


def test_build_query_drops_place_when_coords_given():
    query = build_query(cfg(), latitude=21.0, longitude=105.0)
    assert query.place is None


def test_find_ranks_cheapest_first():
    branches = [branch("a", "Cheap Pickleball Hà Nội", "Hà Nội"),
                branch("b", "Pricey Pickleball Hà Nội", "Hà Nội")]
    cores = {"a": [pickle_court("a1")], "b": [pickle_court("b1")]}
    types = {"a": [price_type(80000)], "b": [price_type(200000)]}
    client = FakeClient(branches, cores, types)

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)

    ranked = result.ranked
    assert [o.branch.id for o in ranked] == ["a", "b"]
    assert ranked[0].total_price == 3 * 80000  # 18:00-21:00 default window


def test_find_filters_by_radius():
    branches = [
        branch("near", "Near Pickleball", "Hà Nội", lat=21.0285, lng=105.8542),
        branch("far", "Far Pickleball", "Hà Nội", lat=10.8231, lng=106.6297),  # HCMC
    ]
    cores = {"near": [pickle_court("n1")], "far": [pickle_court("f1")]}
    types = {"near": [price_type(50000)], "far": [price_type(10000)]}
    client = FakeClient(branches, cores, types)

    result = find_cheapest(
        cfg(),
        build_query(cfg(), latitude=21.0285, longitude=105.8542, radius_km=5),
        client=client,
    )

    assert [o.branch.id for o in result.ranked] == ["near"]


def test_find_ignores_non_sport_branches_and_courts():
    branches = [branch("p", "Pickleball Hà Nội", "Hà Nội", sport=5),
                branch("bad", "Badminton Hà Nội", "Hà Nội", sport=2)]
    cores = {"p": [pickle_court("p1"), Core(id="b1", name="badminton", setting="pickleball", sport_type=2)],
             "bad": [pickle_court("bad1")]}
    types = {"p": [price_type(60000)], "bad": [price_type(1000)]}
    client = FakeClient(branches, cores, types)

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)

    ids = sorted(o.branch.id for o in result.ranked)
    assert ids == ["p"]  # badminton branch excluded; non-sport court inside p excluded
    assert len(result.results[0].options) == 1


def test_find_skips_court_without_price_table():
    branches = [branch("x", "Pickleball Hà Nội", "Hà Nội")]
    cores = {"x": [pickle_court("x1", setting="unknown")]}
    client = FakeClient(branches, cores, {"x": [price_type(1000)]})
    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)
    assert result.ranked == []


def test_find_prices_courts_whose_yard_type_is_unset():
    # The API sends yardType -1 (or leaves it out) for most branches even though
    # the courts are real, so an unknown core sport must not drop the court.
    branches = [branch("x", "Pickleball Hà Nội", "Hà Nội")]
    cores = {"x": [Core(id="x1", name="Pickleball 1", setting="pickleball", sport_type=-1),
                   Core(id="x2", name="Pickleball 2", setting="pickleball", sport_type=None)]}
    client = FakeClient(branches, cores, {"x": [price_type(110000)]})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)

    assert [o.core.id for o in result.results[0].options] == ["x1", "x2"]
    assert [o.core.id for o in result.ranked] == ["x1", "x2"]  # one row per court


def test_ranked_lists_every_court_of_a_venue_cheapest_rate_first():
    # Courts inside one branch differ in price, hours and availability, so each
    # gets its own row: collapsing them to the venue's cheapest would hide the
    # others (e.g. one that is free when the cheap one is taken). These all sell
    # the same hours, so the cheaper rate wins.
    branches = [branch("a", "Cheap Pickleball Hà Nội", "Hà Nội"),
                branch("b", "Pricey Pickleball Hà Nội", "Hà Nội")]
    cores = {"a": [pickle_court("a1"), pickle_court("a2"), pickle_court("a3")],
             "b": [pickle_court("b1")]}
    # "a" prices its three courts differently: a2 is the cheapest of the venue.
    types = {"a": [CoreType(id="pickleball", name="Pickleball", normal_price=90000,
                            normal_price_one_time=90000),
                   CoreType(id="premium", name="Premium", normal_price=70000,
                            normal_price_one_time=70000)],
             "b": [price_type(200000)]}
    cores["a"][1] = Core(id="a2", name="a2", setting="premium", sport_type=5)
    client = FakeClient(branches, cores, types)

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)

    assert [o.core.id for o in result.ranked] == ["a2", "a1", "a3", "b1"]
    assert len(result.results[0].options) == 3


def test_area_sport_keeps_other_sports_out_of_a_mixed_branch():
    # A mixed branch (Văn Phú: pickleball + football) reports yardType -1 on every
    # core, so the area a court sits in is what separates the two sports.
    branches = [branch("v", "KHU THỂ THAO VĂN PHÚ", "Hà Đông")]
    cores = {"v": [
        Core(id="pk1", name="Pickleball 1", setting="pickleball", sport_type=-1, area_id="pickleball"),
        Core(id="bd1", name="B.Đá 1", setting="bong_da", sport_type=-1, area_id="bong_da"),
    ]}
    areas = {"v": [{"id": "pickleball", "yardType": 5}, {"id": "bong_da", "yardType": 3}]}
    types = {"v": [price_type(110000),
                   CoreType(id="bong_da", name="Bóng đá", normal_price=100000,
                            normal_price_one_time=110000)]}
    client = FakeClient(branches, cores, types, areas=areas)

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội"), client=client)

    assert [o.core.id for o in result.ranked] == ["pk1"]


def test_find_reports_unknown_sport():
    client = FakeClient([], {}, {})
    try:
        find_cheapest(cfg(), build_query(cfg(), sport="chess"), client=client)
    except ValueError as exc:
        assert "unknown sport" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_parse_category_defaults_and_rejects_unknown():
    assert parse_category(None) == "all"
    assert parse_category("SOCIAL") == "social"
    assert parse_category(" court ") == "court"
    try:
        parse_category("courts")
    except ValueError as exc:
        assert "unknown category" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_each_category_skips_the_other_one_s_work():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    cores = {"a": [pickle_court("a1")]}
    types = {"a": [price_type(50000)]}
    tickets = {"a": [SocialSession(id="s1", name="Xé vé tối", start=dt.datetime(2026, 9, 22, 19, 0),
                                   duration_min=120, ticket_price=65000, max_player=10,
                                   current_player=4)]}
    day = dt.date(2026, 9, 22)

    social_client = FakeClient(branches, cores, types, sessions=tickets)
    social = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=day, category="social"),
        client=social_client,
    )
    assert social_client.calls["cores"] == set()      # no court pricing at all
    assert social_client.calls["bookings"] == 1
    assert social.ranked == [] and [s.id for s in social.ranked_social] == ["s1"]

    court_client = FakeClient(branches, cores, types, sessions=tickets)
    court = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=day, category="court"),
        client=court_client,
    )
    assert court_client.calls["bookings"] == 0        # no ticket lookup at all
    assert court_client.calls["cores"] == {"a"}
    assert len(court.ranked) == 1 and court.ranked_social == []


def test_social_category_ranks_tickets_cheapest_first_and_names_the_venue():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội"),
                branch("b", "Pickleball Hà Nội 2", "Hà Nội")]
    tickets = {
        "a": [SocialSession(id="s1", name="Xé vé tối", start=dt.datetime(2026, 9, 22, 19, 0),
                            duration_min=180, ticket_price=65000, max_player=12, current_player=2)],
        "b": [SocialSession(id="s2", name="Social không giới hạn", start=dt.datetime(2026, 9, 22, 19, 30),
                            duration_min=120, ticket_price=50000, max_player=10, current_player=4)],
    }
    client = FakeClient(branches, {}, {}, sessions=tickets)

    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22),
                    start_minute=18 * 60, end_minute=21 * 60, category="social"),
        client=client,
    )

    assert [s.id for s in result.ranked_social] == ["s2", "s1"]  # cheapest ticket first
    assert result.ranked_social[0].branch.name == "Pickleball Hà Nội 2"
    assert result.ranked_social[1].spots_left == 10


def test_social_only_search_reports_branch_distance():
    # Pricing courts computes each branch's distance; a tickets-only run skips
    # that work, so the distance must be set for the candidates directly.
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội", lat=21.0285, lng=105.8542)]
    tickets = {"a": [SocialSession(id="s1", name="Xé vé tối", start=dt.datetime(2026, 9, 22, 19, 0),
                                   duration_min=120, ticket_price=50000, max_player=10,
                                   current_player=4)]}
    client = FakeClient(branches, {}, {}, sessions=tickets)

    result = find_cheapest(
        cfg(),
        build_query(cfg(), latitude=21.0285, longitude=105.8542, radius_km=5,
                    day=dt.date(2026, 9, 22), category="social"),
        client=client,
    )

    assert result.ranked_social[0].distance_km == 0.0


def test_ranked_social_lists_every_ticket_of_a_venue_cheapest_first():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    tickets = {"a": [
        SocialSession(id="s1", name="Xé vé tối", start=dt.datetime(2026, 9, 22, 19, 0),
                      duration_min=120, ticket_price=65000, max_player=10, current_player=4),
        SocialSession(id="s2", name="Xé vé sáng", start=dt.datetime(2026, 9, 22, 8, 0),
                      duration_min=60, ticket_price=40000, max_player=10, current_player=1),
    ]}
    client = FakeClient(branches, {}, {}, sessions=tickets)

    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22),
                    start_minute=0, end_minute=23 * 60 + 59, category="social"),
        client=client,
    )

    assert [s.id for s in result.ranked_social] == ["s2", "s1"]  # one row per ticket
    assert len(result.results[0].sessions) == 2


def test_window_crossing_midnight_is_priced():
    branches = [branch("n", "Night Pickleball Hà Nội", "Hà Nội")]
    client = FakeClient(branches, {"n": [pickle_court("n1")]}, {"n": [price_type(100000)]})
    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", start_minute=22 * 60, end_minute=1 * 60),
        client=client,
    )
    assert result.ranked[0].total_price == 300000  # 3 hours


def test_today_default_is_a_date():
    assert isinstance(build_query(cfg()).day, dt.date)


# --- tariffs ("đối tượng áp dụng") -------------------------------------------
# 2026-09-23 is a Wednesday, so weekday-restricted blocks apply.

PEAK = {"time": "17:00-23:00", "price": 200000}
OFF_PEAK = {"time": "17:00-23:00", "price": 180000}


def rate_branch(branch_id, *targets):
    """One pickleball branch whose court type is priced by the given tariffs."""
    return FakeClient(
        [branch(branch_id, f"Pickleball {branch_id}", "Hà Nội")],
        {branch_id: [pickle_court(f"{branch_id}1")]},
        {branch_id: [tariff_type(*targets)]},
    )


def test_courts_are_priced_from_the_branches_ordinary_tariff():
    # Both tariffs are listed, and the discount happens to come first: the walk-up
    # rate must win, or the venue looks 10% cheaper than it really is.
    client = rate_branch("a",
                         tariff("uu_dai", "Ưu đãi giảm giá", blocks=[OFF_PEAK]),
                         tariff("kh", "Pickleball", blocks=[PEAK]))
    result = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23)), client=client)

    option = result.ranked[0]
    assert option.total_price == 3 * 200000  # 18:00-21:00 on the `kh` tariff, not the 110k base
    assert (option.target_id, option.target_name) == ("kh", "Pickleball")


def test_target_flag_prices_under_a_named_tariff():
    client = rate_branch("a",
                         tariff("uu_dai", "Ưu đãi giảm giá", blocks=[OFF_PEAK]),
                         tariff("kh", "Pickleball", blocks=[PEAK]))
    query = build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), target="Ưu đãi giảm giá")

    option = find_cheapest(cfg(), query, client=client).ranked[0]

    assert option.total_price == 3 * 180000
    assert (option.target_id, option.target_name) == ("uu_dai", "Ưu đãi giảm giá")


def test_target_flag_matches_the_id_as_well_as_the_name():
    client = rate_branch("a", tariff("kh", "Pickleball", blocks=[PEAK]))
    query = build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), target="kh")

    assert find_cheapest(cfg(), query, client=client).ranked[0].total_price == 600000


def test_a_branch_without_the_requested_tariff_falls_back_to_its_own():
    # --target must work across the shortlist, so a type that lacks it prices normally.
    branches = [branch("a", "Pickleball A", "Hà Nội"), branch("b", "Pickleball B", "Hà Nội")]
    client = FakeClient(
        branches,
        {"a": [pickle_court("a1")], "b": [pickle_court("b1")]},
        {"a": [tariff_type(tariff("kh", "Pickleball", blocks=[PEAK]))],
         "b": [tariff_type(tariff("san_mai_che", "Sân mái che", blocks=[OFF_PEAK]))]},
    )
    query = build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), target="kh")

    by_branch = {opt.branch.id: opt for opt in find_cheapest(cfg(), query, client=client).ranked}

    assert by_branch["a"].total_price == 600000 and by_branch["a"].target_id == "kh"
    assert by_branch["b"].total_price == 540000 and by_branch["b"].target_id == "san_mai_che"


def test_an_unknown_target_is_rejected_rather_than_silently_defaulted():
    client = rate_branch("a", tariff("kh", "Pickleball", blocks=[PEAK]))
    query = build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), target="pickelball")

    with pytest.raises(ValueError, match="unknown tariff"):
        find_cheapest(cfg(), query, client=client)


def test_hidden_tariffs_are_not_selectable():
    hidden = tariff("ve_thang", "Vé tháng", blocks=[OFF_PEAK])
    hidden.hide = True
    client = rate_branch("a", hidden, tariff("kh", "Pickleball", blocks=[PEAK]))
    query = build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), target="ve_thang")

    with pytest.raises(ValueError, match="unknown tariff"):
        find_cheapest(cfg(), query, client=client)


def test_a_court_type_without_tariffs_keeps_pricing_from_its_own_table():
    client = FakeClient([branch("a", "Pickleball A", "Hà Nội")],
                        {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23)), client=client).ranked[0]

    assert option.total_price == 300000
    assert (option.target_id, option.target_name) == ("", "")


# --- the venue's bookable hours ----------------------------------------------
# A branch sells its opening hours only: the hours after closing are neither
# charged nor reported free, so an 18:00-24:00 request against a venue that shuts
# at 22:00 is priced and checked for 18:00-23:00.

EVENING = dict(start_minute=18 * 60, end_minute=24 * 60)


def venue(branch_id, open_hour=None, close_hour=None):
    return Branch(id=branch_id, name=f"Pickleball {branch_id}", address="Hà Nội", sport_type=5,
                  latitude=None, longitude=None, open_hour=open_hour, close_hour=close_hour)


def test_a_window_is_clipped_to_the_venues_bookable_hours():
    client = FakeClient([venue("a", 5, 22)], {"a": [pickle_court("a1")]},
                        {"a": [tariff_type(tariff("kh", "Pickleball", blocks=[PEAK]))]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING),
        client=client).ranked[0]

    assert option.hours == 5.0  # 18:00-23:00: the last sold hour starts at closing
    assert option.total_price == 5 * 200000


def test_availability_stops_at_closing_time_not_at_the_asked_window():
    # Taken 19:00-22:00, so the open parts are the hour before and the last sold hour.
    client = FakeClient([venue("a", 5, 22)], {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": [booking("a1", 19, duration=180, day=23)]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING),
        client=client).ranked[0]

    assert option.available == "partial"
    assert option.free_spans == [
        (dt.datetime(2026, 9, 23, 18, 0), dt.datetime(2026, 9, 23, 19, 0)),
        (dt.datetime(2026, 9, 23, 22, 0), dt.datetime(2026, 9, 23, 23, 0)),  # not to midnight
    ]
    # and the clipped window stays inside the day, so only one day is looked up
    assert client.calls["availability"] == [("a", dt.date(2026, 9, 23))]


def test_a_partly_free_court_is_priced_for_its_open_hour_at_the_tariff_rate():
    # La Khê shaped: hours 05:00-22:00, so an 18:00-24:00 request sells 18:00-23:00.
    # Everything but the last hour is taken, so the quote is that hour at peak rate —
    # the figure the app's own grid shows for it.
    client = FakeClient([venue("a", 5, 22)], {"a": [pickle_court("a1")]},
                        {"a": [tariff_type(tariff("kh", "Pickleball", blocks=[PEAK]))]},
                        bookings={"a": [booking("a1", 18, duration=240, day=23)]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING),
        client=client).ranked[0]

    assert option.available == "partial"
    assert option.free_spans == [(dt.datetime(2026, 9, 23, 22, 0), dt.datetime(2026, 9, 23, 23, 0))]
    assert option.hours == 1.0
    assert option.total_price == 200000
    assert option.hourly_price == 200000  # the tariff's peak rate, not the 110k base


def test_an_open_hour_the_tariff_does_not_price_is_not_sold():
    # Some branches' rate tables stop before their closing hour (blocks to 22:00 with
    # a 0 base). That hour is not on sale, so the court has nothing left to offer.
    client = FakeClient(
        [venue("a", 5, 22)], {"a": [pickle_court("a1")]},
        {"a": [tariff_type(tariff("kh", "Pickleball", price=0,
                                  blocks=[{"time": "17:00-22:00", "price": 200000}]))]},
        bookings={"a": [booking("a1", 18, duration=240, day=23)]})

    result = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING), client=client)

    assert result.ranked == []


def test_an_unpriced_hour_is_trimmed_off_a_longer_open_span():
    # Open 20:00-23:00, but the tariff stops at 22:00: the court is offered for the
    # two hours it prices, not three, and the per-hour figure is not diluted.
    client = FakeClient(
        [venue("a", 5, 22)], {"a": [pickle_court("a1")]},
        {"a": [tariff_type(tariff("kh", "Pickleball", price=0,
                                  blocks=[{"time": "17:00-22:00", "price": 200000}]))]},
        bookings={"a": [booking("a1", 18, duration=120, day=23)]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING),
        client=client).ranked[0]

    assert option.free_spans == [(dt.datetime(2026, 9, 23, 20, 0), dt.datetime(2026, 9, 23, 22, 0))]
    assert option.hours == 2.0
    assert option.total_price == 400000
    assert option.hourly_price == 200000


def test_a_venue_shut_for_the_whole_window_is_not_priced():
    client = FakeClient([venue("a", 5, 9)], {"a": [pickle_court("a1")]},
                        {"a": [price_type(100000)]})

    result = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING), client=client)

    assert result.ranked == []


def test_a_branch_that_publishes_no_hours_keeps_the_window_as_asked():
    client = FakeClient([branch("a", "Pickleball A", "Hà Nội")],
                        {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]})

    option = find_cheapest(
        cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 23), **EVENING),
        client=client).ranked[0]

    assert option.hours == 6.0
    assert option.total_price == 600000


def booking(core_id, hour, duration=120, day=22):
    return Booking(id=f"b-{core_id}", core_id=core_id, duration_min=duration,
                   start=dt.datetime(2026, 9, day, hour, 0))


def test_a_booked_court_drops_out_and_a_free_one_is_tagged_free():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    cores = {"a": [pickle_court("a1"), pickle_court("a2")]}
    types = {"a": [price_type(100000)]}
    # a1 is taken 18:00-21:00, which covers the default window end to end.
    client = FakeClient(branches, cores, types, bookings={"a": [booking("a1", 18, duration=180)]})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    # a1 has nothing left to sell, so it is not offered; a2 is free for the window.
    assert [o.core.id for o in result.results[0].options] == ["a2"]
    assert result.results[0].options[0].available == "free"


def test_a_booking_outside_the_window_leaves_the_court_free():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": [booking("a1", 9)]})  # 09:00-11:00, not overlapping

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    assert result.ranked[0].available == "free"


def test_free_only_drops_booked_courts_and_reranks_on_the_free_one():
    branches = [branch("a", "Cheap Pickleball", "Hà Nội"),
                branch("b", "Pricey Pickleball", "Hà Nội")]
    cores = {"a": [pickle_court("a1")], "b": [pickle_court("b1")]}
    types = {"a": [price_type(50000)], "b": [price_type(120000)]}
    # "a" is cheaper but its only court is taken for the whole window, so "b" wins.
    client = FakeClient(branches, cores, types, bookings={"a": [booking("a1", 18, duration=180)]})

    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22), availability="free"),
        client=client,
    )

    assert [o.branch.id for o in result.ranked] == ["b"]


def test_a_fully_booked_venue_is_not_offered_in_either_mode():
    branches = [branch("a", "Cheap Pickleball", "Hà Nội"),
                branch("b", "Pricey Pickleball", "Hà Nội")]
    cores = {"a": [pickle_court("a1")], "b": [pickle_court("b1")]}
    types = {"a": [price_type(50000)], "b": [price_type(120000)]}
    client = FakeClient(branches, cores, types, bookings={"a": [booking("a1", 18, duration=180)]})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    assert [o.branch.id for o in result.ranked] == ["b"]


def test_an_unreadable_booking_list_marks_courts_unknown_without_failing():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        availability_fails={"a"})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    assert result.ranked[0].available == "unknown"
    # and "free only" cannot rule an unknown court out, so it stays listed
    kept = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22), availability="free"),
        client=FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                          availability_fails={"a"}),
    )
    assert [o.core.id for o in kept.ranked] == ["a1"]


def test_a_window_crossing_midnight_checks_both_days():
    branches = [branch("a", "Night Pickleball Hà Nội", "Hà Nội")]
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": [booking("a1", 23, day=22), booking("a1", 0, day=23)]})

    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22),
                    start_minute=23 * 60, end_minute=2 * 60),
        client=client,
    )

    days = [day for _, day in client.calls["availability"]]
    assert days == [dt.date(2026, 9, 22), dt.date(2026, 9, 23)]
    assert result.ranked == []  # taken end to end, so there is nothing to quote


def test_social_only_search_never_looks_up_availability():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    tickets = {"a": [SocialSession(id="s1", name="Xé vé tối", start=dt.datetime(2026, 9, 22, 19, 0),
                                   duration_min=120, ticket_price=50000, max_player=10,
                                   current_player=4)]}
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        sessions=tickets)

    find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22),
                                     category="social"), client=client)

    assert client.calls["availability"] == []


def test_a_partly_booked_court_is_tagged_partial_with_its_free_span():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    # a1 is taken 18:00-20:00, so only 20:00-21:00 of the default window is open.
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": [booking("a1", 18)]})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    option = result.ranked[0]
    assert option.available == "partial"
    assert option.free_spans == [(dt.datetime(2026, 9, 22, 20, 0),
                                  dt.datetime(2026, 9, 22, 21, 0))]


def test_a_partly_free_court_is_priced_for_its_open_time_only():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    # Taken 19:00-21:00, so the first hour of the three-hour window is what you can buy.
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": [booking("a1", 19, duration=120)]})

    option = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client).ranked[0]

    assert option.available == "partial"
    assert option.free_spans == [(dt.datetime(2026, 9, 22, 18, 0),
                                  dt.datetime(2026, 9, 22, 19, 0))]
    assert option.hours == 1.0
    assert option.total_price == 100000  # the open hour, not the three asked for


def test_free_only_keeps_only_courts_open_for_the_whole_window():
    branches = [branch("a", "Partly Free", "Hà Nội"), branch("b", "Fully Free", "Hà Nội")]
    cores = {"a": [pickle_court("a1")], "b": [pickle_court("b1")]}
    types = {"a": [price_type(50000)], "b": [price_type(120000)]}
    # "a" is cheaper but only half open; only "b" is free for the window asked about.
    client = FakeClient(branches, cores, types, bookings={"a": [booking("a1", 18)]})

    result = find_cheapest(
        cfg(),
        build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22), availability="free"),
        client=client,
    )

    assert [o.branch.id for o in result.ranked] == ["b"]


def test_a_fully_booked_court_has_no_price_to_quote():
    branches = [branch("a", "Pickleball Hà Nội", "Hà Nội")]
    # Two adjacent bookings cover 18:00-21:00 end to end.
    legs = [booking("a1", 18), booking("a1", 20)]
    client = FakeClient(branches, {"a": [pickle_court("a1")]}, {"a": [price_type(100000)]},
                        bookings={"a": legs})

    result = find_cheapest(cfg(), build_query(cfg(), place="Hà Nội", day=dt.date(2026, 9, 22)),
                           client=client)

    assert result.ranked == []
    assert result.results[0].options == []


def test_parse_availability_defaults_and_rejects_unknown():
    assert parse_availability(None) == "any"
    assert parse_availability(" FREE ") == "free"
    try:
        parse_availability("booked")
    except ValueError as exc:
        assert "unknown availability" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")

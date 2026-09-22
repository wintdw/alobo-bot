import copy
import datetime as dt

from alobo_bot.config import DEFAULTS
from alobo_bot.models import Branch, Core, CoreType, SportType
from alobo_bot.search import build_query, find_cheapest, normalize, place_score


class FakeClient:
    """In-memory stand-in for AloboClient (no network)."""

    def __init__(self, branches, cores, core_types, sessions=None):
        self._branches = branches
        self._cores = cores
        self._core_types = core_types
        self._sessions = sessions or {}

    def sport_types(self):
        return [
            SportType(key="badminton", name="Cầu lông", int_value=2),
            SportType(key="pickleball", name="Pickleball", int_value=5),
        ]

    def all_branches(self, page_size=100):
        return list(self._branches)

    def get_cores(self, branch_id):
        return list(self._cores.get(branch_id, [])), []

    def get_core_types(self, branch_id):
        return list(self._core_types.get(branch_id, []))

    def branch_booking_search(self, *args, **kwargs):
        return []


def branch(bid, name, address, sport=5, lat=None, lng=None):
    return Branch(id=bid, name=name, address=address, sport_type=sport,
                  latitude=lat, longitude=lng)


def pickle_court(cid, setting="pickleball"):
    return Core(id=cid, name=cid, setting=setting, sport_type=5)


def price_type(price=100000):
    return CoreType(id="pickleball", name="Pickleball", normal_price=price,
                    normal_price_one_time=price)


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


def test_find_reports_unknown_sport():
    client = FakeClient([], {}, {})
    try:
        find_cheapest(cfg(), build_query(cfg(), sport="chess"), client=client)
    except ValueError as exc:
        assert "unknown sport" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


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

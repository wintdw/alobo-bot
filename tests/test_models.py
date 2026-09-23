import datetime as dt

from alobo_bot.models import (
    Booking,
    Branch,
    Core,
    CoreType,
    CourtOption,
    PriceTarget,
    SocialSession,
    SpecialPrice,
)
from alobo_bot.pricing import clip_to_hours, window_cost


def test_branch_from_search_payload():
    branch = Branch.from_api({
        "id": "sport_phuong_pickleball",
        "name": "Phương Pickleball",
        "address": "Khu công nghệ phần mềm",
        "type": 5,
        "provinceId": "74",
        "location": {"_latitude": 10.8676, "_longitude": 106.7940},
        "bookingTypes": ["oneTime", "groupOneTime"],
    })
    assert branch.sport_type == 5
    assert branch.latitude == 10.8676
    assert branch.booking_types == ["oneTime", "groupOneTime"]


def test_branch_tolerates_missing_location():
    branch = Branch.from_api({"id": "x", "name": "X", "address": "", "type": None})
    assert branch.latitude is None and branch.longitude is None
    assert branch.sport_type is None


def test_branch_reads_its_working_hours():
    branch = Branch.from_api({"id": "x", "name": "X", "morningStartWorkingTime": 5,
                              "afternoonEndWorkingTime": 22})
    # 05:00-22:00 hours, and the grid's last column starts at closing: sold to 23:00.
    assert branch.bookable == (5 * 60, 23 * 60)


def test_branch_status_marks_locked_and_removed_venues():
    # The list marks a locked branch -1 (its name carries "(khóa)") and a removed
    # one -2; the app drops exactly those. 0 and 1 are live.
    assert Branch.from_api({"id": "x", "status": -1}).is_locked
    assert Branch.from_api({"id": "x", "status": -2}).is_locked
    assert not Branch.from_api({"id": "x", "status": 0}).is_locked
    assert not Branch.from_api({"id": "x", "status": 1}).is_locked
    assert not Branch.from_api({"id": "x"}).is_locked  # unstated -> treated as active


def test_bookable_is_none_without_both_ends_and_clamps_to_the_day():
    assert Branch.from_api({"id": "x"}).bookable is None
    assert Branch.from_api({"id": "x", "morningStartWorkingTime": 6}).bookable is None
    at_midnight = Branch.from_api({"id": "x", "morningStartWorkingTime": 6,
                                   "afternoonEndWorkingTime": 24})
    assert at_midnight.bookable == (6 * 60, 24 * 60)  # 24:00 is the end of the day, not 25:00


def test_clip_to_hours_intersects_the_window_with_the_opening_hours():
    assert clip_to_hours(18 * 60, 24 * 60, (5 * 60, 23 * 60)) == (18 * 60, 23 * 60)
    assert clip_to_hours(6 * 60, 8 * 60, (5 * 60, 23 * 60)) == (6 * 60, 8 * 60)
    assert clip_to_hours(20 * 60, 22 * 60, (5 * 60, 10 * 60)) is None  # shut all window
    assert clip_to_hours(22 * 60, 1 * 60, (5 * 60, 23 * 60)) == (22 * 60, 23 * 60)  # wraps


def test_core_carries_pricing_key():
    core = Core.from_api({"id": "pickleball_4", "name": "Pickleball 4",
                          "setting": "pickleball", "yardType": 5})
    assert core.setting == "pickleball"
    assert core.sport_type == 5


def test_special_price_wraps_past_midnight():
    special = SpecialPrice.from_api({"time": "22:00-02:00", "price": 80000,
                                     "priceOneTime": 90000})
    assert special is not None
    assert special.contains(23 * 60, weekday=1)      # 23:00 -> inside
    assert special.contains(60, weekday=1)           # 01:00 -> inside (wrapped)
    assert not special.contains(12 * 60, weekday=1)  # 12:00 -> outside


def test_core_type_price_applies_matching_special_window():
    core = CoreType.from_api({
        "id": "pickleball", "name": "Pickleball",
        "normalPrice": 100000, "normalPriceOneTime": 110000,
        "specialPrice": [
            {"time": "5:30-10:00", "price": 50000, "priceOneTime": 60000, "minDuration": 60},
            {"time": "10:00-17:00", "price": 70000, "priceOneTime": 80000, "minDuration": 60},
        ],
    })
    assert core.price_per_unit(6 * 60, weekday=3) == 60000
    assert core.price_per_unit(12 * 60, weekday=3) == 80000
    assert core.price_per_unit(20 * 60, weekday=3) == 110000  # outside -> base price


def test_overlapping_special_windows_first_wins():
    # A special window can be pricier than the base (peak pricing), so the list is
    # an ordered override table: the first matching entry decides, never the min.
    core = CoreType.from_api({
        "id": "x", "name": "X", "normalPrice": 60000, "normalPriceOneTime": 60000,
        "specialPrice": [
            {"time": "5:00-7:00", "price": 95000, "priceOneTime": 95000},
            {"time": "17:00-23:00", "price": 75000, "priceOneTime": 75000},
            {"time": "5:00-23:00", "price": 82000, "priceOneTime": 82000},
        ],
    })
    assert core.price_per_unit(6 * 60, weekday=1) == 95000
    assert core.price_per_unit(18 * 60, weekday=1) == 75000
    assert core.price_per_unit(12 * 60, weekday=1) == 82000


LA_KHE_TYPE = {
    "id": "san", "name": "Sân",
    "normalPrice": 100000, "normalPriceOneTime": 110000,
    "specialPrice": [],
    "targets": {
        "khach_hang_dong_quy_uu_dai_giam_gia": {
            "name": "Ưu đãi giảm giá (Khách hàng đóng Quý - 3 tháng)",
            "price": 0, "priceOneTime": 0, "minDuration": 60, "priority": 0,
            "specialPrice": [
                {"time": "5:00-17:00", "dateRangeWeek": "1-5", "price": 120000},
                {"time": "17:00-23:00", "dateRangeWeek": "1-5", "price": 180000},
            ],
        },
        "kh": {
            "name": "Pickleball",
            "price": 100000, "priceOneTime": 110000, "minDuration": 30, "priority": 0,
            "specialPrice": [
                {"time": "5:00-17:00", "dateRangeWeek": "1-5", "price": 140000},
                {"time": "17:00-23:00", "dateRangeWeek": "1-5", "price": 200000},
            ],
        },
    },
}


def test_target_carries_its_own_price_table():
    # A target's special windows often carry only `price`, never `priceOneTime`.
    target = PriceTarget.from_api("kh", {
        "name": "Pickleball", "price": 100000, "priceOneTime": 110000,
        "specialPrice": [{"time": "17:00-23:00", "dateRangeWeek": "1-5", "price": 200000}],
    })
    assert target.id == "kh" and target.name == "Pickleball"
    assert target.price_per_unit(18 * 60, weekday=3) == 200000  # inside the block
    assert target.price_per_unit(23 * 60, weekday=3) == 110000  # base rate outside it
    assert target.price_per_unit(18 * 60, weekday=6) == 110000  # block is T2-T6 only


def test_core_type_parses_targets_in_api_order():
    core = CoreType.from_api(LA_KHE_TYPE)
    # the type-level table is a placeholder; the real rates live on the targets
    assert core.normal_price_one_time == 110000 and core.special_prices == []
    assert list(core.targets) == ["khach_hang_dong_quy_uu_dai_giam_gia", "kh"]
    assert core.targets["kh"].name == "Pickleball"


def test_a_zero_one_time_rate_falls_back_to_the_base_rate():
    # the app does this, and the API leans on it for branches with one flat rate
    core = CoreType.from_api({"id": "x", "normalPrice": 60000, "normalPriceOneTime": 0})
    assert core.normal_price_one_time == 60000


def test_default_target_prefers_the_generic_customer_tariff():
    # The API lists the quarterly-payer discount first; it is not the walk-up rate.
    core = CoreType.from_api(LA_KHE_TYPE)
    assert core.default_target().id == "kh"


def test_default_target_falls_back_to_the_only_or_first_visible_one():
    only = CoreType.from_api({"id": "t", "targets": {"san_mai_che": {"name": "Mái che"}}})
    assert only.default_target().id == "san_mai_che"

    picky = CoreType.from_api({"id": "t", "targets": {
        "ve_thang": {"name": "Vé tháng", "hide": True},
        "kh": {"name": "Khách hàng"},
    }})
    assert [t.id for t in picky.visible_targets()] == ["kh"]


def test_targets_order_by_priority_then_api_order():
    core = CoreType.from_api({"id": "t", "targets": {
        "b": {"name": "B", "priority": 1},
        "a": {"name": "A", "priority": 0},
        "c": {"name": "C", "priority": 0},
    }})
    assert [t.id for t in core.ordered_targets()] == ["a", "c", "b"]


def test_a_court_type_without_targets_prices_from_its_own_table():
    core = CoreType.from_api({"id": "pickleball", "normalPrice": 60000,
                              "normalPriceOneTime": 60000, "targets": {}})
    assert core.default_target() is None
    assert window_cost(core, 18 * 60, 21 * 60, weekday=3) == 180000


def test_window_cost_uses_the_targets_rates_per_hour():
    core = CoreType.from_api(LA_KHE_TYPE)
    # La Khê "Pickleball", Wednesday: 17:00-23:00 is 200k/h, the rest is the 110k base.
    assert window_cost(core, 18 * 60, 21 * 60, weekday=3, target=core.targets["kh"]) == 600000
    assert window_cost(core, 18 * 60, 24 * 60, weekday=3, target=core.targets["kh"]) == 1110000
    # the quarterly-payer tariff is a different table for the same window
    discount = core.targets["khach_hang_dong_quy_uu_dai_giam_gia"]
    assert window_cost(core, 18 * 60, 21 * 60, weekday=3, target=discount) == 540000


def test_social_session_spots_left_and_start():
    session = SocialSession.from_api({
        "id": "s1", "name": "Social không giới hạn", "duration": 120,
        "ticketPrice": 50000, "maxPlayer": 10, "currentPlayer": 4,
        "services": [{"serviceName": "Pickleball 2",
                      "startTime": "2026-09-23T08:00:00.000"}],
    })
    assert session.spots_left == 6
    assert session.start is not None and session.start.hour == 8
    assert session.court_names == ["Pickleball 2"]


def test_a_venue_and_its_court_and_ticket_all_share_one_booking_url():
    # The court and ticket rows must link a venue the same way, so both read the
    # branch's own deep link.
    branch = Branch(id="b1", name="Alpha", address="Alpha address", sport_type=5,
                    latitude=None, longitude=None)
    expected = "https://datlich.alobo.vn/san/b1"
    assert branch.booking_url == expected

    court = CourtOption(branch=branch, core=Core(id="c1", name="Sân 1", setting="pickleball"),
                        core_type=CoreType(id="pickleball", name="Pickleball",
                                           normal_price=100000, normal_price_one_time=100000),
                        total_price=100000, hours=1.0)
    assert court.booking_url == expected

    ticket = SocialSession(id="s1", name="Xé vé tối", start=None, duration_min=60,
                           ticket_price=50000, max_player=10, current_player=4, branch=branch)
    assert ticket.booking_url == expected
    # a session with no branch has no venue to link to
    assert SocialSession(id="s2", name="Xé vé", start=None, duration_min=60,
                         ticket_price=0, max_player=1, current_player=0).booking_url == ""


def test_booking_reads_a_legs_court_time_and_duration():
    legs = Booking.from_api({
        "id": "1EdtLXv2pxzZad5Omuks", "time": "2026-09-23T18:00:00.000", "duration": 120,
        "type": "groupOneTime", "status": 1,
        "services": [{"serviceId": "pickleball_1", "startTime": "2026-09-23T18:00:00.000",
                      "duration": 120, "price": 0, "amount": 2, "branchServiceType": "core"}],
    })
    assert len(legs) == 1
    leg = legs[0]
    assert leg.core_id == "pickleball_1"
    assert leg.start == dt.datetime(2026, 9, 23, 18, 0)
    assert leg.end == dt.datetime(2026, 9, 23, 20, 0)  # start + 120 min

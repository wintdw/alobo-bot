from alobo_bot.models import Branch, Core, CoreType, SocialSession, SpecialPrice


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

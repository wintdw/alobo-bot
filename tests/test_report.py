import datetime as dt

from alobo_bot.models import Branch, Core, CoreType, CourtOption, SocialSession
from alobo_bot.report import money, render_markdown, render_text, to_dict
from alobo_bot.search import BranchResult, FindQuery, FindResult


def option(branch_id, name, price, distance=None, court="Pickleball 1", available=None):
    branch = Branch(id=branch_id, name=name, address=f"{name} address",
                    sport_type=5, latitude=21.0, longitude=105.0)
    return CourtOption(branch=branch, core=Core(id="c1", name=court, setting="pickleball", sport_type=5),
                       core_type=CoreType(id="pickleball", name="Pickleball",
                                          normal_price=price, normal_price_one_time=price),
                       total_price=price, hours=3.0, distance_km=distance, available=available)


def make_result(sessions=False, category="all"):
    query = FindQuery(place="Hà Nội", day=dt.date(2026, 9, 25), start_minute=18 * 60,
                      end_minute=21 * 60, category=category)
    result = FindResult(query=query, sport_name="Pickleball")
    result.results = [
        BranchResult(branch=option("a", "Alpha", 300000).branch, options=[option("a", "Alpha", 300000)]),
        BranchResult(branch=option("b", "Beta", 150000).branch, options=[option("b", "Beta", 150000)]),
    ]
    if sessions:
        result.results[1].sessions = [
            SocialSession(id="s", name="Xé vé tối", duration_min=120, ticket_price=50000,
                          max_player=10, current_player=4, branch=result.results[1].branch,
                          start=dt.datetime(2026, 9, 25, 19, 0))
        ]
    return result


def test_money_uses_vietnamese_grouping():
    assert money(150000) == "150.000đ"
    assert money(1234567) == "1.234.567đ"


def test_to_dict_is_ranked_cheapest_first():
    payload = to_dict(make_result())
    assert payload["date"] == "2026-09-25"
    assert payload["from"] == "18:00" and payload["to"] == "21:00"
    assert payload["category"] == "all"
    assert [o["branchId"] for o in payload["options"]] == ["b", "a"]
    assert payload["options"][0]["bookingUrl"].endswith("/b")


def test_to_dict_ranks_tickets_and_names_their_venue():
    payload = to_dict(make_result(sessions=True))
    assert payload["options"][0]["rank"] == 1
    assert payload["socialSessions"] == [{
        "rank": 1, "branchId": "b", "branchName": "Beta", "address": "Beta address",
        "distanceKm": None, "sessionId": "s",
        "name": "Xé vé tối", "start": "2026-09-25T19:00", "end": "2026-09-25T21:00",
        "durationMin": 120,
        "ticketPrice": 50000, "spotsLeft": 6, "courts": [],
    }]


def test_render_text_lists_the_cheapest_branch_first():
    text = render_text(make_result())
    assert "Cheapest Pickleball — " in text
    assert text.index("Beta") < text.index("Alpha")


def test_render_text_puts_tickets_above_courts():
    text = render_text(make_result(sessions=True))
    assert text.index("Tickets (xé vé)") < text.index("Courts — per court, most hours then cheapest rate")
    assert "50.000đ · Xé vé tối @ 19:00-21:00 · 6 spots · Beta" in text


def test_render_text_says_when_a_category_is_empty():
    assert "none on sale in this window" in render_text(make_result())
    no_courts = make_result()
    no_courts.results = [BranchResult(branch=option("a", "Alpha", 1000).branch)]
    assert "none priced in this window" in render_text(no_courts)


def test_render_text_reports_only_the_chosen_category():
    social = render_text(make_result(sessions=True, category="social"))
    assert "Tickets (xé vé)" in social and "Courts — per court, most hours then cheapest rate" not in social
    courts = render_text(make_result(sessions=True, category="court"))
    assert "Courts — per court, most hours then cheapest rate" in courts and "Tickets (xé vé)" not in courts


def test_render_markdown_has_table_rows():
    md = render_markdown(make_result())
    assert md.startswith("# Cheapest Pickleball — ")
    assert "## Courts — per court, most hours then cheapest rate · 2 court(s)" in md
    assert "| 1 | 3h | 150.000đ | 50.000đ |" in md  # rank, hours, total, per hour


def test_render_markdown_has_a_ticket_table():
    md = render_markdown(make_result(sessions=True))
    assert md.index("## Tickets (xé vé)") < md.index("## Courts — per court, most hours then cheapest rate")
    assert "| 50.000đ | Xé vé tối | 19:00 | 21:00 | 6 | Beta | Beta address | — |" in md


def test_ticket_rows_show_the_venue_location():
    result = make_result(sessions=True)
    result.results[1].sessions[0].distance_km = 3.2
    assert "3.2 km · Beta address" in render_text(result)
    assert "| Beta | Beta address | 3.2 km |" in render_markdown(result)


def test_ticket_ranking_lists_every_ticket_of_a_venue():
    result = make_result(sessions=True)
    branch = result.results[1].branch
    result.results[1].sessions.append(
        SocialSession(id="s2", name="Xé vé sáng", duration_min=60, ticket_price=30000,
                      max_player=10, current_player=1, branch=branch,
                      start=dt.datetime(2026, 9, 25, 8, 0))
    )
    assert [s.id for s in result.ranked_social] == ["s2", "s"]  # cheapest ticket first
    text = render_text(result)
    assert "Tickets (xé vé) — per person, cheapest first · 2 ticket(s)" in text
    assert "30.000đ · Xé vé sáng" in text and "50.000đ · Xé vé tối" in text


def test_court_rows_carry_the_availability_status():
    result = make_result()
    result.results[0].options[0].available = "booked"  # Alpha
    result.results[1].options[0].available = "free"    # Beta

    text = render_text(result)
    assert "status" in text
    assert "free" in text and "booked" in text

    md = render_markdown(result)
    assert "| Status |" in md
    assert "| free |" in md and "| booked |" in md

    payload = to_dict(result)
    assert payload["availability"] == "any"
    assert [o["availability"] for o in payload["options"]] == ["free", "booked"]


def test_an_unchecked_court_shows_a_placeholder_status():
    assert "?" in render_text(make_result())  # option() leaves available None
    assert "| ? |" in render_markdown(make_result())


def test_a_partial_court_names_its_free_span_everywhere():
    result = make_result()
    partial = result.results[1].options[0]  # Beta, the cheapest row
    partial.available = "partial"
    partial.free_spans = [(dt.datetime(2026, 9, 25, 20, 0), dt.datetime(2026, 9, 25, 21, 0))]

    assert "partial 20:00-21:00" in render_text(result)
    assert "| partial 20:00-21:00 |" in render_markdown(result)

    payload = to_dict(result)
    slot = next(o for o in payload["options"] if o["branchId"] == "b")
    assert slot["availability"] == "partial"
    assert slot["freeSpans"] == [{"from": "2026-09-25T20:00", "to": "2026-09-25T21:00"}]


def test_free_only_courts_heading_says_so():
    result = make_result()
    result.query.availability = "free"
    assert "Courts — per court, most hours then cheapest rate · 2 court(s) · free only" in render_text(result)
    assert "· free only" in render_markdown(result)


def test_court_rows_name_the_tariff_the_price_came_from():
    result = make_result()
    tariffed = result.results[1].options[0]  # Beta, the cheapest row
    tariffed.target_id = "kh"
    tariffed.target_name = "BẢNG GIÁ THUÊ SÂN"

    assert "· tariff: BẢNG GIÁ THUÊ SÂN" in render_text(result)
    assert "| Court | Status | Tariff | Venue |" in render_markdown(result)
    assert "| BẢNG GIÁ THUÊ SÂN |" in render_markdown(result)

    slot = next(o for o in to_dict(result)["options"] if o["branchId"] == "b")
    assert (slot["priceTargetId"], slot["priceTarget"]) == ("kh", "BẢNG GIÁ THUÊ SÂN")


def test_a_court_type_without_tariffs_reports_no_tariff():
    result = make_result()

    assert "tariff:" not in render_text(result)
    slot = to_dict(result)["options"][0]
    assert slot["priceTargetId"] is None and slot["priceTarget"] is None


def test_a_venue_with_two_courts_lists_both_rows():
    # Courts in one venue have their own hours and availability, so the venue must
    # not collapse to a single row.
    result = make_result()
    result.results[1].options.append(
        option("b", "Beta", 250000, court="Pickleball 2", available="partial")
    )
    result.results[1].options[0].available = "free"  # the cheaper court, a different slot

    text = render_text(result)
    assert "Pickleball 1 · Beta" in text and "Pickleball 2 · Beta" in text

    md = render_markdown(result)
    assert "| Pickleball 1 |" in md and "| Pickleball 2 |" in md

    assert len(to_dict(result)["options"]) == 3  # one row per court, not per venue


def test_ranking_is_most_hours_then_cheapest_rate_not_total():
    # Hours available is the primary key and the rate the secondary; the total is
    # not used at all — a court that sells fewer hours has a smaller total for that
    # reason alone, so ranking on it is backwards.
    big = option("a", "Alpha", 600000, court="Pickleball 1")   # 3h, 200.000đ/h
    big.hours = 3.0
    cheap = option("b", "Beta", 450000, court="Pickleball 1")  # 3h, 150.000đ/h
    cheap.hours = 3.0
    tiny = option("c", "Gamma", 100000, court="Pickleball 1")  # 1h, 100.000đ/h
    tiny.hours = 1.0
    result = FindResult(query=FindQuery(place="Hà Nội"), sport_name="Pickleball")
    result.results = [
        BranchResult(branch=big.branch, options=[big]),
        BranchResult(branch=cheap.branch, options=[cheap]),
        BranchResult(branch=tiny.branch, options=[tiny]),
    ]

    # more hours wins (a before c) even though c is cheaper per hour and in total;
    # within equal hours the cheaper rate wins (b before a)
    assert [o.branch.id for o in result.ranked] == ["b", "a", "c"]
    assert [o["hourlyPrice"] for o in to_dict(result)["options"]] == [150000, 200000, 100000]

    text = render_text(result)
    assert text.index("Beta") < text.index("Alpha") < text.index("Gamma")

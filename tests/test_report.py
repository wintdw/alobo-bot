import datetime as dt

from alobo_bot.models import Branch, Core, CoreType, CourtOption
from alobo_bot.report import money, render_markdown, render_text, to_dict
from alobo_bot.search import BranchResult, FindQuery, FindResult


def option(branch_id, name, price, distance=None, court="Pickleball 1"):
    branch = Branch(id=branch_id, name=name, address=f"{name} address",
                    sport_type=5, latitude=21.0, longitude=105.0)
    return CourtOption(branch=branch, core=Core(id="c1", name=court, setting="pickleball", sport_type=5),
                       core_type=CoreType(id="pickleball", name="Pickleball",
                                          normal_price=price, normal_price_one_time=price),
                       total_price=price, hours=3.0, distance_km=distance)


def make_result():
    query = FindQuery(place="Hà Nội", day=dt.date(2026, 9, 25), start_minute=18 * 60, end_minute=21 * 60)
    result = FindResult(query=query, sport_name="Pickleball")
    result.results = [
        BranchResult(branch=option("a", "Alpha", 300000).branch, options=[option("a", "Alpha", 300000)]),
        BranchResult(branch=option("b", "Beta", 150000).branch, options=[option("b", "Beta", 150000)]),
    ]
    return result


def test_money_uses_vietnamese_grouping():
    assert money(150000) == "150.000đ"
    assert money(1234567) == "1.234.567đ"


def test_to_dict_is_ranked_cheapest_first():
    payload = to_dict(make_result())
    assert payload["date"] == "2026-09-25"
    assert payload["from"] == "18:00" and payload["to"] == "21:00"
    assert [o["branchId"] for o in payload["options"]] == ["b", "a"]
    assert payload["options"][0]["bookingUrl"].endswith("/b")


def test_render_text_lists_the_cheapest_branch_first():
    text = render_text(make_result())
    assert "Cheapest Pickleball courts" in text
    assert text.index("Beta") < text.index("Alpha")


def test_render_markdown_has_table_rows():
    md = render_markdown(make_result())
    assert md.startswith("# Cheapest Pickleball courts")
    assert "| 1 | 150.000đ |" in md

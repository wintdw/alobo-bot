import datetime as dt

from alobo_bot.models import Branch, Core, CoreType, CourtOption, SocialSession
from alobo_bot.search import BranchResult, FindQuery, FindResult
from alobo_bot.web import has_search_params
from alobo_bot.webui import esc, render_page, render_results, render_sessions

SPORTS = [("pickleball", "Pickleball"), ("badminton", "Cầu lông")]


def option(branch_id, name, price, distance=None):
    branch = Branch(id=branch_id, name=name, address=f"{name}, Hà Nội", sport_type=5,
                    latitude=None, longitude=None)
    return CourtOption(
        branch=branch,
        core=Core(id="c1", name="Pickleball 1", setting="pickleball", sport_type=5),
        core_type=CoreType(id="pickleball", name="Pickleball", normal_price=price,
                           normal_price_one_time=price),
        total_price=price,
        hours=3.0,
        distance_km=distance,
    )


def result_with(options, sessions=False):
    query = FindQuery(place="Hà Nội", day=dt.date(2026, 9, 25), start_minute=18 * 60, end_minute=21 * 60)
    result = FindResult(query=query, sport_name="Pickleball", branches_scanned=4)
    result.results = [
        BranchResult(branch=opt.branch,
                     options=[opt],
                     sessions=(
                         [SocialSession(id="s", name="Social", duration_min=120, ticket_price=50000,
                                        max_player=10, current_player=4, start=dt.datetime(2026, 9, 25, 19, 0))]
                         if sessions else []
                     ),
                     distance_km=opt.distance_km)
        for opt in options
    ]
    return result


def test_esc_escapes_markup():
    assert esc("<b>&") == "&lt;b&gt;&amp;"


def test_has_search_params():
    assert has_search_params(place="Hà Nội", lat=None, lng=None)
    assert has_search_params(place=None, lat=21.0, lng=None)
    assert not has_search_params(place="", lat=None, lng=None)


def test_page_has_landmarks_and_accessible_form():
    page = render_page(sports=SPORTS, query={})
    assert page.startswith("<!DOCTYPE html>")
    assert '<html lang="vi">' in page
    assert 'class="skip-link"' in page
    assert 'id="content" tabindex="-1"' in page
    # every control is labelled and the width/radius hints are wired up
    assert '<label for="place">' in page and 'id="place"' in page
    assert 'aria-describedby="place-hint"' in page
    assert '<select id="sport" name="sport">' in page
    assert '<option value="pickleball" selected>' in page


def test_results_table_is_semantic_and_ranked():
    page = render_results(result_with([option("a", "Alpha", 300000, 3.0),
                                       option("b", "Beta", 150000, 5.0)]))
    assert "<caption>" in page and 'scope="col"' in page
    assert page.index("Beta") < page.index("Alpha")     # cheaper first
    assert "150.000đ" in page
    assert page.count('class="top"') == 1               # only the cheapest row highlighted


def test_results_empty_state():
    page = render_results(result_with([]))
    assert "Không tìm thấy sân nào" in page


def test_sessions_section_only_when_present():
    assert render_sessions(result_with([option("a", "Alpha", 1000)])) == ""
    page = render_sessions(result_with([option("a", "Alpha", 1000)], sessions=True))
    assert "Suất chơi chung" in page and "50.000đ" in page


def test_error_is_announced():
    page = render_page(sports=SPORTS, query={"place": "X"}, error="broke")
    assert 'role="alert"' in page and "broke" in page

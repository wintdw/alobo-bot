import datetime as dt

from alobo_bot.models import Branch, Core, CoreType, CourtOption, SocialSession
from alobo_bot.search import BranchResult, FindQuery, FindResult
from alobo_bot.web import has_search_params
from alobo_bot.webui import esc, render_courts, render_page, render_results, render_social

SPORTS = [("pickleball", "Pickleball"), ("badminton", "Cầu lông")]
AREAS = ["Hà Nội", "Cầu Giấy, Hà Nội"]


def option(branch_id, name, price, distance=None, available=None):
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
        available=available,
    )


def result_with(options, sessions=False, category="all"):
    query = FindQuery(place="Hà Nội", day=dt.date(2026, 9, 25), start_minute=18 * 60,
                      end_minute=21 * 60, category=category)
    result = FindResult(query=query, sport_name="Pickleball", branches_scanned=4)
    result.results = [
        BranchResult(branch=opt.branch,
                     options=[opt],
                     sessions=(
                         [SocialSession(id="s", name="Xé vé tối", duration_min=120, ticket_price=50000,
                                        max_player=10, current_player=4, branch=opt.branch,
                                        distance_km=opt.distance_km,
                                        start=dt.datetime(2026, 9, 25, 19, 0))]
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
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert page.startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in page
    assert 'class="skip-link"' in page
    assert 'id="content" tabindex="-1"' in page
    # every control is labelled and the width/radius hints are wired up
    assert '<label for="place">' in page and 'id="place"' in page
    assert 'aria-describedby="place-hint"' in page
    assert '<select id="sport" name="sport" aria-describedby="sport-hint">' in page
    assert '<option value="pickleball" selected>' in page


def test_category_control_lists_both_and_preserves_the_choice():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert '<select id="category" name="category"' in page
    # the default is both categories, as the config says
    assert '<option value="all" selected>' in page
    assert '<option value="social">' in page and '<option value="court">' in page

    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Hà Nội", "category": "social"})
    assert '<option value="social" selected>' in page
    assert '<option value="all" selected>' not in page


def test_area_field_is_a_dropdown_that_still_accepts_free_text():
    page = render_page(sports=SPORTS, areas=["Cầu Giấy, Hà Nội"], query={})
    # a datalist gives the dropdown, while the input keeps name="place"
    assert 'id="place" name="place"' in page and 'list="place-options"' in page
    assert '<datalist id="place-options">' in page
    assert '<option value="Cầu Giấy, Hà Nội"></option>' in page


def test_current_location_button_is_present_and_gated():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # rendered hidden; the script reveals it only when geolocation exists
    assert 'id="geo" hidden' in page
    assert "navigator.geolocation" in page


def test_current_location_fills_coords_without_auto_searching():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # it fills lat/lng and tells the user to submit; it must not submit itself
    assert "getElementById('lat').value" in page
    assert "getElementById('lng').value" in page
    assert "requestSubmit" not in page
    assert 'press "Find"' in page


def test_current_location_autofills_the_area_from_a_reverse_geocode():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # the coordinates are reverse-geocoded and the place name lands in the Area box
    assert "api.bigdatacloud.net/data/reverse-geocode-client" in page
    assert "getElementById('place')" in page and "place.value = area" in page
    # and it decides that name from locality + city ("Cầu Giấy, Hà Nội")
    assert "function areaFromGeocode" in page
    assert "data.locality" in page and "data.city" in page


def test_ui_is_english():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert '<html lang="en">' in page
    assert ">Area<" in page and ">Find<" in page
    assert ">Use current location<" in page


def test_form_defaults_to_today_and_three_km():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert f'value="{dt.date.today().isoformat()}"' in page
    assert 'id="radius" name="radius" type="number" value="3"' in page


def test_title_links_back_to_clean_form():
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Hà Nội", "radius": "8"})
    assert '<h1><a href="/">' in page
    assert "<span>Alobo</span></a></h1>" in page


def test_page_is_branded_with_the_logo_and_a_favicon():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert "<title>Alobo | Cheapest pickleball courts</title>" in page
    assert '<link rel="icon" href="data:image/svg+xml,' in page
    assert 'type="image/svg+xml"' in page


def test_the_logo_follows_the_theme_and_is_decorative():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # inline SVG filled from the theme's own accents, so it flips with light/dark
    assert 'fill="var(--accent)"' in page
    assert 'fill="var(--accent-fg)"' in page
    # decorative: the wordmark carries the accessible name, so the mark is hidden
    assert 'aria-hidden="true"' in page and 'focusable="false"' in page
    assert page.count(">Alobo<") == 1


def test_favicon_data_uri_is_url_encoded():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    icon = page.split('<link rel="icon" href="', 1)[1].split('"', 1)[0]
    assert icon.startswith("data:image/svg+xml,")
    assert icon.endswith("%3C/svg%3E")
    assert "#" not in icon  # a raw # would truncate the URI at the fragment
    assert "%23" in icon    # the badge colour survived the round trip


def test_results_table_is_semantic_and_ranked():
    page = render_results(result_with([option("a", "Alpha", 300000, 3.0),
                                       option("b", "Beta", 150000, 5.0)]))
    assert "<caption>" in page and 'scope="col"' in page
    assert page.index("Beta") < page.index("Alpha")     # cheaper first
    assert "150.000đ" in page
    assert page.count('class="top"') == 1               # only the cheapest row highlighted


def test_results_empty_state():
    page = render_results(result_with([]))
    assert "No priced courts in this window" in page


def test_tickets_category_leads_and_is_labelled_per_person():
    page = render_results(result_with([option("a", "Alpha", 1000)], sessions=True))
    assert page.index('id="tickets-heading"') < page.index('id="courts-heading"')
    assert "Tickets (xé vé)" in page and "per person" in page
    assert "50.000đ" in page and "Xé vé tối" in page


def test_ticket_rows_show_the_venue_location():
    page = render_social(result_with([option("a", "Alpha", 1000, 3.0)], sessions=True))
    assert "Alpha, Hà Nội</div>" in page          # street address under the venue name
    assert "3.0 km" in page                       # distance column
    assert 'scope="col" class="num">Distance</th>' in page


def test_ticket_rows_show_the_session_start_and_end():
    page = render_social(result_with([option("a", "Alpha", 1000, 3.0)], sessions=True))
    assert ">Starts</th>" in page and ">Ends</th>" in page
    assert "<td>19:00</td><td>21:00</td>" in page  # 19:00 start + 120 min duration


def test_empty_ticket_category_still_renders_its_own_note():
    page = render_social(result_with([option("a", "Alpha", 1000)]))
    assert "Tickets (xé vé)" in page
    assert "No tickets on sale in this window" in page
    assert "<table>" not in page


def test_courts_category_lists_every_court():
    page = render_courts(result_with([option("a", "Alpha", 1000)]))
    assert "Courts" in page and "per court, most hours then cheapest rate" in page
    assert "1 court(s)" in page
    assert "one row per court" in page
    # the primary sort key is shown, so the ordering is legible
    assert 'scope="col" class="num">Hours</th>' in page
    assert '<td class="num">3h</td>' in page


def test_court_rows_name_the_court():
    page = render_courts(result_with([option("a", "Alpha", 1000),
                                      option("b", "Beta", 2000)]))
    assert 'scope="col">Court</th>' in page
    assert "<td>Pickleball 1</td>" in page


def test_category_selects_which_sections_render():
    tickets_only = render_results(result_with([option("a", "Alpha", 1000)], sessions=True,
                                              category="social"))
    assert 'id="tickets-heading"' in tickets_only
    assert 'id="courts-heading"' not in tickets_only

    courts_only = render_results(result_with([option("a", "Alpha", 1000)], sessions=True,
                                             category="court"))
    assert 'id="courts-heading"' in courts_only
    assert 'id="tickets-heading"' not in courts_only


def test_availability_control_lists_any_and_free_and_preserves_the_choice():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert '<select id="availability" name="availability"' in page
    assert '<option value="any" selected>' in page          # tags, but hides nothing
    assert '<option value="free">' in page

    page = render_page(sports=SPORTS, areas=AREAS,
                       query={"place": "Hà Nội", "availability": "free"})
    assert '<option value="free" selected>' in page
    assert '<option value="any" selected>' not in page


def test_court_rows_show_the_availability_status():
    page = render_courts(result_with([option("a", "Alpha", 1000, available="free"),
                                      option("b", "Beta", 2000, available="booked")]))
    assert 'scope="col">Status</th>' in page
    assert '<td class="status status-free">free</td>' in page
    assert '<td class="status status-booked">booked</td>' in page


def test_an_unchecked_court_renders_a_placeholder_status():
    page = render_courts(result_with([option("a", "Alpha", 1000)]))
    assert '<td class="status status-unknown">?</td>' in page


def test_a_partial_court_renders_its_free_span():
    partial = option("a", "Alpha", 1000, available="partial")
    partial.free_spans = [(dt.datetime(2026, 9, 25, 20, 0), dt.datetime(2026, 9, 25, 21, 0))]
    page = render_courts(result_with([partial]))
    assert '<td class="status status-partial">partial 20:00-21:00</td>' in page


def test_the_page_offers_no_tariff_choice():
    # Every court is priced at the branch's standard customer tariff; the page has no
    # control for it and ignores any `target` left over in a URL.
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Hà Nội", "target": "kh"})
    assert 'name="target"' not in page
    assert "Tariff" not in page.split("<footer>")[0]  # not a form control either


def test_court_rows_show_the_tariff_behind_the_price():
    tariffed = option("a", "Alpha", 1000, available="free")
    tariffed.target_name = "BẢNG GIÁ THUÊ SÂN"
    page = render_courts(result_with([tariffed, option("b", "Beta", 2000)]))

    assert 'scope="col">Tariff</th>' in page
    assert '<td class="tariff">BẢNG GIÁ THUÊ SÂN</td>' in page
    assert '<td class="tariff">—</td>' in page  # no tariffs on Beta's court type


def test_error_is_announced():
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "X"}, error="broke")
    assert 'role="alert"' in page and "broke" in page

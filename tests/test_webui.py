import datetime as dt

from alobo_bot.models import Branch, Core, CoreType, CourtOption, SocialSession
from alobo_bot.search import BranchResult, FindQuery, FindResult
from alobo_bot.web import (
    RESULT_REUSE_SECONDS,
    ResultCache,
    has_search_params,
    query_key,
)
from alobo_bot.webui import (
    esc,
    render_courts,
    render_page,
    render_results,
    render_results_region,
    render_social,
)

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


SEARCH = {"place": "Mulberry", "lat": 20.987175137028466, "lng": 105.784681195317,
          "radius": 3.0, "date": "2026-09-23", "from": "18:00", "to": "23:59",
          "sport": "pickleball", "limit": None, "category": "all", "availability": "any"}


def test_query_key_matches_a_reload_to_the_search_that_rendered_the_page():
    assert query_key(dict(SEARCH)) == query_key(dict(SEARCH))
    # whitespace around a name is noise; a different field is a different search
    assert query_key({**SEARCH, "place": "  Mulberry "}) == query_key(SEARCH)
    assert query_key({**SEARCH, "from": "19:00"}) != query_key(SEARCH)
    assert query_key({**SEARCH, "category": "court"}) != query_key(SEARCH)


def test_a_reload_reuses_the_cached_result_for_the_same_criteria():
    cache = ResultCache()
    now = dt.datetime(2026, 9, 23, 18, 30)
    cache.put(query_key(SEARCH), "the run", now)

    assert cache.get(query_key(SEARCH), now) == "the run"
    assert cache.get(query_key({**SEARCH, "from": "19:00"}), now) is None  # not run yet


def test_the_cache_keeps_each_search_so_a_revisit_is_instant():
    cache = ResultCache()
    now = dt.datetime(2026, 9, 23, 18, 30)
    first, second = query_key(SEARCH), query_key({**SEARCH, "from": "19:00"})
    cache.put(first, "the first run", now)
    cache.put(second, "the second run", now)

    # searching another window does not throw away the first one's result
    assert cache.get(first, now) == "the first run"
    assert cache.get(second, now) == "the second run"


def test_a_cached_run_past_the_reuse_window_is_a_miss():
    cache = ResultCache()
    now = dt.datetime(2026, 9, 23, 18, 30)
    cache.put(query_key(SEARCH), "the run", now - dt.timedelta(seconds=RESULT_REUSE_SECONDS + 1))
    assert cache.get(query_key(SEARCH), now) is None


def test_the_cache_is_bounded_and_drops_the_least_recently_used():
    cache = ResultCache(max_entries=2)
    now = dt.datetime(2026, 9, 23, 18, 30)
    keys = [query_key({**SEARCH, "from": f"{hour:02d}:00"}) for hour in (6, 13, 18)]
    for index, key in enumerate(keys):
        cache.put(key, index, now)

    assert cache.get(keys[0], now) is None          # evicted: the least recent
    assert cache.get(keys[1], now) == 1
    assert cache.get(keys[2], now) == 2


def test_reading_a_cached_run_makes_it_the_most_recent():
    cache = ResultCache(max_entries=2)
    now = dt.datetime(2026, 9, 23, 18, 30)
    keys = [query_key({**SEARCH, "from": f"{hour:02d}:00"}) for hour in (6, 13, 18)]
    cache.put(keys[0], 0, now)
    cache.put(keys[1], 1, now)
    assert cache.get(keys[0], now) == 0              # now the most recently used
    cache.put(keys[2], 2, now)                       # so this evicts keys[1], not keys[0]

    assert cache.get(keys[0], now) == 0
    assert cache.get(keys[1], now) is None
    assert cache.get(keys[2], now) == 2


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


def test_area_field_is_a_pick_only_dropdown():
    page = render_page(sports=SPORTS, areas=["Cầu Giấy, Hà Nội"], query={})
    # a real select, not a text box with a suggestion list
    assert '<select id="place" name="place"' in page
    assert '<input id="place"' not in page and 'datalist' not in page
    assert '<optgroup label="Area">' in page
    assert '<option value="Cầu Giấy, Hà Nội">Cầu Giấy, Hà Nội</option>' in page


def test_the_area_dropdown_offers_a_blank_entry_and_keeps_the_searched_area():
    page = render_page(sports=SPORTS, areas=["Cầu Giấy, Hà Nội"], query={})
    # blank is a real choice: it means "no area", so a fresh form still searches nothing
    assert '<option value="" selected>Any area</option>' in page

    page = render_page(sports=SPORTS, areas=["Cầu Giấy, Hà Nội"],
                       query={"place": "Cầu Giấy, Hà Nội"})
    assert '<option value="Cầu Giấy, Hà Nội" selected>Cầu Giấy, Hà Nội</option>' in page
    assert '<option value="" selected>' not in page


def test_an_area_outside_the_list_is_still_offered_selected():
    # a reverse-geocoded or bookmarked area is not in config, but must not be lost
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Sơn Tây, Hà Nội"})
    assert '<option value="Sơn Tây, Hà Nội" selected>Sơn Tây, Hà Nội</option>' in page


MULBERRY = {"Mulberry": (20.987175137028466, 105.784681195317)}


def test_saved_places_lead_the_area_dropdown():
    page = render_page(sports=SPORTS, areas=AREAS, presets=MULBERRY, query={})
    # one picker, not a second control: the saved place is the dropdown's top group
    assert '<optgroup label="Preset">' in page
    assert '<option value="Mulberry">Mulberry</option>' in page
    assert page.index('<optgroup label="Preset">') < page.index('<optgroup label="Area">')
    assert 'name="preset"' not in page
    assert "Saved places (Preset)" in page   # and the hint says what it does
    # an ordinary area still needs no mention of saved places
    assert "Saved places (Preset)" not in render_page(sports=SPORTS, areas=AREAS, query={})


def test_choosing_a_saved_place_fills_its_coordinates():
    page = render_page(sports=SPORTS, areas=AREAS, presets=MULBERRY, query={})
    # the coordinates travel to the page so the boxes can be filled without a round trip
    assert 'id="saved-places"' in page
    assert '"Mulberry": [20.987175137028466, 105.784681195317]' in page
    assert "applySavedPlace" in page
    assert "getElementById('lat').value" in page
    # and it reacts to picking a name from the dropdown
    assert "areaSelect.addEventListener('change', applySavedPlace)" in page
    # while coordinates the user typed themselves are never treated as ours to clear
    assert "coordinatesFromSavedPlace = false" in page


def test_no_saved_place_script_when_none_are_configured():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert 'id="saved-places"' not in page
    assert '"Mulberry"' not in page


def test_a_saved_place_cannot_break_out_of_the_script_element():
    page = render_page(sports=SPORTS, areas=AREAS, query={},
                       presets={"</script><b>": (1.0, 2.0)})
    assert "</script><b>" not in page
    assert "\\u003c/script>" in page


def test_the_area_dropdown_keeps_a_searched_saved_place_selected():
    page = render_page(sports=SPORTS, areas=AREAS, presets=MULBERRY,
                       query={"place": "Mulberry"})
    assert '<option value="Mulberry" selected>Mulberry</option>' in page
    assert '<option value="" selected>' not in page


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


def test_current_location_selects_the_geocoded_area_in_the_dropdown():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # the coordinates are reverse-geocoded and the name is selected in the Area dropdown
    assert "api.bigdatacloud.net/data/reverse-geocode-client" in page
    assert "function selectArea" in page and "selectArea(area)" in page
    # a name the list does not hold is added as an option, since a select drops it
    assert "new Option(name, name, true, true)" in page
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


def test_the_time_window_is_a_time_picker():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # native time inputs, so the browser offers its own clock picker
    assert '<input id="from" name="from" type="time" value="18:00">' in page
    assert '<input id="to" name="to" type="time" value="21:00">' in page
    # and the searched window comes back in the boxes
    page = render_page(sports=SPORTS, areas=AREAS, query={"from": "06:30", "to": "09:00"})
    assert '<input id="from" name="from" type="time" value="06:30">' in page
    assert '<input id="to" name="to" type="time" value="09:00">' in page


def test_the_time_picker_only_shows_values_it_can_hold():
    # a time input blanks anything outside the day, and a blank field submits empty
    page = render_page(sports=SPORTS, areas=AREAS, query={"from": "18:00", "to": "24:00"})
    assert 'id="to" name="to" type="time" value="23:59"' in page
    # an unparseable leftover falls back to the default rather than to a blank box
    page = render_page(sports=SPORTS, areas=AREAS, query={"from": "sixish"})
    assert 'id="from" name="from" type="time" value="18:00"' in page


def test_the_quick_windows_are_offered_beside_the_pickers():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # the three windows, each carrying the times it fills in
    assert 'data-from="06:00" data-to="12:00"' in page
    assert 'data-from="13:00" data-to="18:00"' in page
    assert 'data-from="18:00" data-to="23:59"' in page
    for name in ("Morning", "Afternoon", "Night"):
        assert f"{name} <span" in page
    # the range each button picks is on the button, so the choice is explicit
    assert "06:00–12:00" in page and "13:00–18:00" in page and "18:00–24:00" in page
    # and they sit with the time fields they fill
    when = page[page.index("when-title"):page.index("what-title")]
    assert 'id="window-presets"' in when and 'id="from"' in when and 'id="to"' in when
    # secondary buttons next to the primary Find, not a second search
    assert '<button type="submit" class="btn-primary">Find</button>' in page
    assert 'class="presets"' in page


def test_the_quick_windows_are_hidden_without_javascript():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # without the script they would do nothing, so they stay hidden until it runs
    assert 'id="window-presets" role="group" aria-label="Quick time windows" hidden' in page
    assert "presetRow.hidden = false" in page
    # the flex display must not beat the [hidden] attribute
    assert ".presets[hidden] { display:none; }" in page


def test_a_quick_window_fills_the_boxes_without_searching():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert "document.getElementById('from').value = preset.dataset.from" in page
    assert "document.getElementById('to').value = preset.dataset.to" in page
    # it only fills the fields: the user still presses Find, and sees the pick first
    assert "requestSubmit" not in page
    assert 'press "Find" to search' in page
    # the fill is announced, since changing a field is silent to a screen reader
    assert 'id="presets-hint" aria-live="polite"' in page


def test_title_links_back_to_clean_form():
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Hà Nội", "radius": "8"})
    assert '<h1><a href="/">' in page
    assert "<span>Alobo</span></a></h1>" in page


def test_page_is_branded_with_the_logo_and_a_favicon():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert "<title>Alobo | Cheapest pickleball courts and tickets</title>" in page
    assert '<link rel="icon" href="data:image/svg+xml,' in page
    assert 'type="image/svg+xml"' in page


def test_header_states_the_value_and_drops_the_read_only_blurb():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    header = page.split("<header>", 1)[1].split("</header>", 1)[0]
    assert "cheapest pickleball courts and tickets" in header.lower()
    assert "Public data" not in header
    assert "read-only" not in header.lower()
    # and the phrase is gone from the page as a whole, not just the header
    assert "Public data" not in page
    assert "read-only" not in page.lower()


def test_footer_explains_the_data_and_the_no_booking_rule():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    footer = page.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert "never books and never pays" in footer
    assert "datlich.alobo.vn" in footer
    assert "tariff each branch publishes" in footer
    # the service links are grouped under a labelled heading, not a run-on line
    assert 'aria-labelledby="footer-links-title"' in footer
    assert '<a href="/report.json">Latest report (JSON)</a>' in footer
    assert '<a href="/health">Service status</a>' in footer


def test_empty_state_names_the_action():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert "press Find to compare court and ticket prices" in page


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


def test_results_name_the_saved_place_that_was_searched():
    result = result_with([option("a", "Alpha", 1000)])
    result.query = FindQuery(place=None, preset="Mulberry", latitude=20.987175137028466,
                             longitude=105.784681195317, day=dt.date(2026, 9, 25),
                             start_minute=18 * 60, end_minute=21 * 60)
    page = render_results(result)
    assert "Mulberry" in page
    assert "20.9872" not in page           # the name stands in for the raw coordinates


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


def test_ticket_venue_links_to_the_branch_like_a_court_row():
    # A ticket's venue opens the branch on AloBooking, exactly as a court row does.
    page = render_social(result_with([option("a", "Alpha", 1000, 3.0)], sessions=True))
    assert ('<a href="https://datlich.alobo.vn/san/a" rel="noopener">Alpha</a>'
            '<div class="venue-sub">Alpha, Hà Nội</div>') in page


def test_ticket_rows_lead_with_a_rank_like_the_courts_table():
    page = render_social(result_with([option("a", "Alpha", 1000)], sessions=True))
    assert '<th scope="col">#</th>' in page
    assert '<td class="rank">1</td>' in page


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
    assert '<td class="status status-free">available</td>' in page
    assert '<td class="status status-booked">booked</td>' in page


def test_an_unchecked_court_renders_a_placeholder_status():
    page = render_courts(result_with([option("a", "Alpha", 1000)]))
    assert '<td class="status status-unknown">?</td>' in page


def test_a_partial_court_renders_its_free_span():
    partial = option("a", "Alpha", 1000, available="partial")
    partial.free_spans = [(dt.datetime(2026, 9, 25, 20, 0), dt.datetime(2026, 9, 25, 21, 0))]
    page = render_courts(result_with([partial]))
    assert '<td class="status status-partial">partial 20:00-21:00</td>' in page


def test_the_page_offers_no_target_choice():
    # Every court is priced at the branch's standard customer tariff; the page has no
    # control for it and ignores any `target` left over in a URL.
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "Hà Nội", "target": "kh"})
    assert 'name="target"' not in page
    assert "Target" not in page.split("<footer>")[0]  # not a form control either


def test_court_rows_show_the_target_behind_the_price():
    tariffed = option("a", "Alpha", 1000, available="free")
    tariffed.target_name = "BẢNG GIÁ THUÊ SÂN"
    page = render_courts(result_with([tariffed, option("b", "Beta", 2000)]))

    assert 'scope="col">Target</th>' in page
    assert '<td class="target">BẢNG GIÁ THUÊ SÂN</td>' in page
    assert '<td class="target">—</td>' in page  # no tariffs on Beta's court type


def test_error_is_announced():
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "X"}, error="broke")
    assert 'role="alert"' in page and "broke" in page


def test_results_region_is_the_one_swappable_fragment():
    # the same fragment the full page embeds, so the two renders cannot drift
    assert "press Find to compare court and ticket prices" in render_results_region()
    assert "No priced courts in this window" not in render_results_region()

    assert 'role="alert"' in render_results_region(error="broke")
    assert "broke" in render_results_region(error="broke")

    tables = render_results_region(result=result_with([option("a", "Alpha", 150000)]))
    assert "150.000đ" in tables


def test_the_page_embeds_the_results_in_a_live_swappable_region():
    # a stable container is what the script's innerHTML swap targets
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert '<div id="results" aria-live="polite" aria-busy="false">' in page
    assert "press Find to compare court and ticket prices" in page

    # an error renders inside that region, so the swap can replace it too
    page = render_page(sports=SPORTS, areas=AREAS, query={"place": "X"}, error="broke")
    region = page.split('id="results"', 1)[1]
    assert 'role="alert"' in region and "broke" in region


def test_the_page_shows_a_search_progress_line():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    assert 'id="search-status" role="status" aria-live="polite"' in page
    # hidden while empty, like the geolocation status
    assert ".geo-status:empty, .search-status:empty { display:none; }" in page


def test_find_is_progressive_enhancement_over_a_plain_get_form():
    page = render_page(sports=SPORTS, areas=AREAS, query={})
    # without JavaScript the form still navigates to the server-rendered page
    assert '<form method="get" action="/" class="card">' in page
    # with it, the submit is intercepted and the fragment is fetched instead
    assert "fetch('/results?'" in page
    assert "new URLSearchParams(new FormData(searchForm))" in page
    assert "preventDefault" in page
    # the URL is still updated, so a search stays bookmarkable/shareable
    assert "history.pushState(null, '', '/?'" in page
    # and Back re-runs the previous search rather than showing a stale region
    assert "addEventListener('popstate'" in page
    # a superseded request is cancelled
    assert "new AbortController()" in page
    # the enhancement is gated on the APIs it needs, so an old browser navigates
    assert "window.fetch && window.history.pushState" in page
    assert 'id="results"' in page and "aria-busy" in page

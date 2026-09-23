"""Server-rendered HTML for the alobo-bot web page (no JS framework, no build step).

Kept separate from :mod:`web` so the markup stays pure and testable without
FastAPI installed. Everything is a plain function returning an HTML fragment, so
the page works with JavaScript disabled; a few lines of progressive enhancement
let the form fetch the results into the page instead of reloading it.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import urllib.parse

from .availability import label as availability_label
from .metrics import Snapshot, status_class
from .parsing import MINUTES_PER_DAY, hhmm
from .pricing import ClockError, parse_clock, window_bounds
from .report import area_label, hours_label, money
from .search import FindResult

MONTHS = "01 02 03 04 05 06 07 08 09 10 11 12".split()

BRAND = "Alobo"
BRAND_TAGLINE = "Cheapest pickleball courts and tickets"


def logo_svg(badge: str, ball: str) -> str:
    """The brand mark: a pickleball on a rounded badge.

    Inline so the page carries no image request and no build step. The header
    copy fills it with the theme's own accent variables, so it follows the
    light/dark switch; the favicon pins the light-theme colours because it is
    rendered outside the page and has no CSS to inherit.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
        'aria-hidden="true" focusable="false">'
        f'<rect width="32" height="32" rx="8" fill="{badge}"/>'
        f'<circle cx="16" cy="16" r="9" fill="{ball}"/>'
        f'<g fill="{badge}">'
        '<circle cx="12.7" cy="12.7" r="2.3"/><circle cx="19.3" cy="12.7" r="2.3"/>'
        '<circle cx="12.7" cy="19.3" r="2.3"/><circle cx="19.3" cy="19.3" r="2.3"/>'
        "</g></svg>"
    )


LOGO = logo_svg("var(--accent)", "var(--accent-fg)")
FAVICON = "data:image/svg+xml," + urllib.parse.quote(logo_svg("#0b6b53", "#ffffff"))

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f4f6f8; --fg: #14171c; --muted: #5b6472; --card: #ffffff;
  --line: #e4e7ec; --line-strong: #ccd2da;
  --accent: #0b6b53; --accent-hover: #095742; --accent-fg: #ffffff; --warn: #8a1c1c;
  --radius: 12px; --radius-sm: 8px;
  --shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 1px 3px rgba(16, 24, 40, .06);
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1216; --fg:#e8ebef; --muted:#9aa4b2; --card:#171b21;
          --line:#272e37; --line-strong:#3a434f;
          --accent:#2ea884; --accent-hover:#3cbf98; --accent-fg:#08150f; --warn:#ff9c9c;
          --shadow: 0 1px 2px rgba(0, 0, 0, .45); }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font: 16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
a { color: var(--accent); }
header, main, footer { max-width: 62rem; margin-inline: auto; padding-inline: 1rem; }
header { padding-block: 1.75rem 0.75rem; }
h1 { font-size: 1.5rem; letter-spacing: -.01em; margin: 0 0 .3rem; }
h1 a { display: inline-flex; align-items: center; gap: .5rem;
  color: inherit; text-decoration: none; }
h1 svg { inline-size: 1.75rem; block-size: 1.75rem; flex: none; }
h1 a:hover { color: var(--accent); }
h2 { font-size: 1.05rem; margin: 1.9rem 0 .6rem; }
h3.category { display:flex; flex-wrap:wrap; align-items:baseline; gap:.55rem;
  font-size:.95rem; margin:1.7rem 0 .6rem; }
h3.category .unit { font-size:.8rem; font-weight:500; color:var(--muted); }
h3.category .count { margin-inline-start:auto; font-size:.8rem; font-weight:700;
  font-variant-numeric:tabular-nums; color:var(--accent); }
p.sub { color: var(--muted); margin: 0; max-width: 46rem; }
.visually-hidden:where(:not(:focus-within, :active)) {
  position:absolute !important; clip-path: inset(50%) !important; overflow:hidden !important;
  width:1px !important; height:1px !important; margin:-1px !important; padding:0 !important;
  border:0 !important; white-space:nowrap !important;
}
.skip-link { position:absolute; left:-999px; }
.skip-link:focus { position:static; display:inline-block; padding:.5rem; background:var(--accent);
  color:var(--accent-fg); border-radius:var(--radius); }
:where(a, button, input, select):focus-visible { outline:3px solid var(--accent); outline-offset:2px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  padding:1.25rem; box-shadow: var(--shadow); }
form { display:block; }
.group + .group { margin-block-start:1.2rem; padding-block-start:1.2rem;
  border-block-start:1px solid var(--line); }
.group-title { margin:0 0 .7rem; font-size:.78rem; font-weight:700; text-transform:uppercase;
  letter-spacing:.06em; color:var(--muted); }
.group-fields { display:grid; gap:.85rem 1rem;
  grid-template-columns:repeat(auto-fit, minmax(11rem, 1fr)); }
.field { display:flex; flex-direction:column; gap:.25rem; min-inline-size:0; }
.field.wide { grid-column: 1 / -1; }
label { font-weight:600; font-size:.85rem; }
.hint { color:var(--muted); font-size:.78rem; }
input, select { font:inherit; padding:.55rem .65rem; border:1px solid var(--line-strong);
  border-radius:var(--radius-sm); background:var(--bg); color:var(--fg); min-inline-size:0; }
input:hover, select:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--line-strong)); }
button { font:inherit; font-weight:600; padding:.62rem 1.15rem; border:1px solid transparent;
  border-radius:var(--radius-sm); cursor:pointer;
  transition: background .15s ease, border-color .15s ease, color .15s ease; }
button:active:not(:disabled) { transform: translateY(1px); }
button:disabled { opacity:.55; cursor:progress; }
.btn-primary { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
.btn-primary:hover:not(:disabled) {
  background:var(--accent-hover); border-color:var(--accent-hover); }
.btn-secondary { background:transparent; color:var(--fg); border-color:var(--line-strong); }
.btn-secondary:hover:not(:disabled) { border-color:var(--accent); color:var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, transparent); }
.presets { display:flex; flex-wrap:wrap; gap:.5rem; }
.presets[hidden] { display:none; }   /* the flex display would otherwise beat [hidden] */
.presets button { padding-block:.45rem; font-size:.9rem; }
.presets .range { margin-inline-start:.4rem; font-weight:500; opacity:.75; }
.actions { display:flex; flex-wrap:wrap; align-items:center; gap:.6rem;
  margin-block-start:1.3rem; padding-block-start:1.2rem; border-block-start:1px solid var(--line); }
.geo-status, .search-status { margin:.5rem 0 0; }
.geo-status:empty, .search-status:empty { display:none; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:var(--radius);
  background:var(--card); }
table { border-collapse:collapse; inline-size:100%; font-size:.9rem; }
caption { text-align:left; padding:.7rem .9rem .2rem; color:var(--muted); font-size:.85rem; }
th, td { padding:.5rem .9rem; text-align:left; border-top:1px solid var(--line); }
thead th { border-top:0; background:color-mix(in srgb, var(--line) 35%, transparent);
  position:sticky; top:0; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
td.status { white-space:nowrap; font-size:.82rem; font-weight:600; }
td.target { font-size:.82rem; color:var(--muted); }
td.status-free { color: var(--accent); }
td.status-partial { color: var(--fg); }
td.status-booked { color: var(--warn); }
td.status-unknown { color: var(--muted); font-weight:500; }
tr.top td { font-weight:600; }
tbody tr:hover td { background:color-mix(in srgb, var(--accent) 6%, transparent); }
.rank { color:var(--muted); }
.empty, .error { border-radius:var(--radius); padding:.9rem 1rem; }
.empty { background:var(--card); border:1px solid var(--line); color:var(--muted); }
.error { background:color-mix(in srgb, var(--warn) 12%, var(--card));
  border:1px solid var(--warn); color:var(--warn); }
.venue-sub { color:var(--muted); font-size:.82rem; }
footer { color:var(--muted); font-size:.8rem; padding-block:2.5rem 3rem;
  margin-block-start:3rem; border-block-start:1px solid var(--line); }
.footer-grid { display:grid; gap:1.5rem 2.5rem;
  grid-template-columns:minmax(0, 2fr) minmax(0, 1fr); }
.footer-title { margin:0 0 .5rem; font-size:.78rem; font-weight:700;
  text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.footer-block p { margin:0; max-width:44rem; }
.footer-links { list-style:none; margin:0; padding:0; display:grid; gap:.35rem; }
.footer-fineprint { margin:1.75rem 0 0; padding-block-start:1.25rem;
  border-block-start:1px solid var(--line); max-width:52rem; }
@media (max-width: 40rem) { .footer-grid { grid-template-columns:1fr; } }
.status-grid { display:grid; gap:.75rem; margin-block-end:1rem;
  grid-template-columns:repeat(auto-fit, minmax(11rem, 1fr)); }
.metric { display:flex; flex-direction:column; gap:.2rem; padding:.85rem 1rem;
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  box-shadow:var(--shadow); min-inline-size:0; }
.metric-value { font-size:1.3rem; font-weight:700; line-height:1.2; overflow-wrap:anywhere;
  font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
.metric-label { font-size:.72rem; font-weight:700; text-transform:uppercase;
  letter-spacing:.07em; color:var(--muted); }
.metric-hint { font-size:.78rem; color:var(--muted); }
.metric-warn .metric-value { color:var(--warn); }
.metric-live .metric-value { color:var(--accent); }
section.status { margin-block-start:1.9rem; }
section.status h2 { margin-block-start:0; }
section.status .lead { color:var(--muted); margin:0 0 .9rem; }
.status-note { color:var(--muted); font-size:.82rem; margin:.6rem 0 0; }
""".strip()

SCRIPT = """
// Progressive enhancement: the search itself. The form is a plain GET, so
// without this script Find reloads the page and the browser sits blank for the
// whole multi-second API fan-out. When fetch exists, the submit is intercepted
// and the results fragment is fetched into #results instead — the form stays put,
// a status line reports progress, and the URL is updated so the search is still
// shareable and the Back button re-runs it.
const searchForm = document.querySelector('main form');
const resultsRegion = document.getElementById('results');
const searchStatus = document.getElementById('search-status');
const findButton = searchForm && searchForm.querySelector('button[type=submit]');
let inFlight = null;

function setSearchBusy(on) {
  if (findButton) {
    findButton.disabled = on;
    findButton.textContent = on ? 'Searching…' : 'Find';
  }
  if (resultsRegion) resultsRegion.setAttribute('aria-busy', on ? 'true' : 'false');
  if (searchStatus) searchStatus.textContent = on ? 'Searching AloBooking…' : '';
}

async function runSearch(params) {
  if (inFlight) inFlight.abort();
  inFlight = new AbortController();
  setSearchBusy(true);
  try {
    const response = await fetch('/results?' + params.toString(), {signal: inFlight.signal});
    // The body is a server-rendered fragment either way — the tables on success,
    // an escaped error paragraph on 400/409/502 — so it is always swapped in.
    resultsRegion.innerHTML = await response.text();
  } catch (err) {
    if (err.name === 'AbortError') return;
    resultsRegion.innerHTML =
      '<p class="error" role="alert">Error: could not reach the service. Please try again.</p>';
  } finally {
    setSearchBusy(false);
  }
}

if (searchForm && resultsRegion && window.fetch && window.history.pushState) {
  searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const params = new URLSearchParams(new FormData(searchForm));
    history.pushState(null, '', '/?' + params.toString());
    runSearch(params);
  });
  addEventListener('popstate', () => {
    // Re-run whatever the new URL asks for; with no parameters the endpoint
    // returns the idle prompt, so Back to the blank form clears the results too.
    runSearch(new URLSearchParams(location.search));
  });
}

// Progressive enhancement: the geolocation button stays hidden unless the
// browser supports it, so without JS the form is unchanged (pick an area or type
// coordinates by hand). It fills the coordinates and names the area from them;
// the search runs when the user submits.
const GEOCODER = 'https://api.bigdatacloud.net/data/reverse-geocode-client';

const areaSelect = document.getElementById('place');

// Put *name* in the Area dropdown, adding it as an option when the list does not
// already offer it — a name the geocoder returned for the current location, which
// no district need match. A select silently ignores a value it has no option for,
// so without this the reverse-geocoded area would be dropped.
function selectArea(name) {
  if (!areaSelect || !name) return;
  if (!Array.from(areaSelect.options).some((option) => option.value === name)) {
    areaSelect.querySelectorAll('option[data-geocoded]').forEach((option) => option.remove());
    const option = new Option(name, name, true, true);
    option.dataset.geocoded = '1';
    areaSelect.add(option);
  }
  areaSelect.value = name;
}

// Progressive enhancement: choosing a saved place in the Area dropdown fills in
// its coordinates, so the numbers are visible (and editable) before the search
// runs. Choosing an ordinary area clears the coordinates this script filled, so a
// stale spot cannot override the area the user actually asked for. Without
// JavaScript the server still resolves a saved place by name.
const savedEl = document.getElementById('saved-places');
const savedPlaces = savedEl ? JSON.parse(savedEl.textContent) : null;
let coordinatesFromSavedPlace = false;

function applySavedPlace() {
  if (!savedPlaces || !areaSelect) return;
  const lat = document.getElementById('lat');
  const lng = document.getElementById('lng');
  if (!lat || !lng) return;
  const wanted = areaSelect.value.trim().toLowerCase();
  const name = Object.keys(savedPlaces)
    .find((key) => key.trim().toLowerCase() === wanted);
  if (name) {
    lat.value = savedPlaces[name][0];
    lng.value = savedPlaces[name][1];
    coordinatesFromSavedPlace = true;
  } else if (coordinatesFromSavedPlace) {
    lat.value = '';
    lng.value = '';
    coordinatesFromSavedPlace = false;
  }
}

if (areaSelect && savedPlaces) {
  areaSelect.addEventListener('change', applySavedPlace);
  // Hand-typed coordinates are the user's own, so a later area change keeps them.
  // (Selecting an option here does not fire 'input', so this only sees real edits.)
  ['lat', 'lng'].forEach((id) => {
    const box = document.getElementById(id);
    if (box) box.addEventListener('input', () => { coordinatesFromSavedPlace = false; });
  });
  // A bookmarked search carries only the name, so fill the boxes it left blank.
  const lat = document.getElementById('lat');
  const lng = document.getElementById('lng');
  if (!lat.value && !lng.value) applySavedPlace();
}

// Progressive enhancement: the quick windows (Morning/Afternoon/Night) fill the
// From/To boxes so the usual windows need neither typing nor picking. Like the
// location button, they only fill the fields — the search waits for "Find", so
// the window they set is still visible and editable before it runs.
const presetRow = document.getElementById('window-presets');
if (presetRow) {
  presetRow.hidden = false;
  presetRow.addEventListener('click', (e) => {
    const preset = e.target.closest('button[data-from]');
    if (!preset) return;
    document.getElementById('from').value = preset.dataset.from;
    document.getElementById('to').value = preset.dataset.to;
    const hint = document.getElementById('presets-hint');
    if (hint) hint.textContent = preset.textContent.trim() + ': press "Find" to search.';
  });
}

// "Cầu Giấy, Hà Nội" from the free key-less geocoder's reply.
function areaFromGeocode(data) {
  const city = data.city || data.principalSubdivision || '';
  const local = data.locality || '';
  if (local && local !== city) return local + ', ' + city;
  return city || local;
}

async function reverseGeocode(latitude, longitude) {
  const url = GEOCODER + '?latitude=' + latitude + '&longitude=' + longitude
    + '&localityLanguage=vi';
  const response = await fetch(url);
  if (!response.ok) return '';
  return areaFromGeocode(await response.json());
}

const geoBtn = document.getElementById('geo');
const geoStatus = document.getElementById('geo-status');
if (geoBtn && navigator.geolocation) {
  geoBtn.hidden = false;
  geoBtn.addEventListener('click', () => {
    geoBtn.disabled = true;
    if (geoStatus) geoStatus.textContent = 'Getting your location…';
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const latitude = pos.coords.latitude.toFixed(6);
        const longitude = pos.coords.longitude.toFixed(6);
        document.getElementById('lat').value = latitude;
        document.getElementById('lng').value = longitude;
        coordinatesFromSavedPlace = false;   // these are the user's own coordinates now
        if (geoStatus) geoStatus.textContent = 'Naming your area…';
        let area = '';
        try {
          area = await reverseGeocode(latitude, longitude);
        } catch (err) {
          area = '';
        }
        if (area) selectArea(area);
        geoBtn.disabled = false;
        if (geoStatus) geoStatus.textContent = area
          ? 'Area set to "' + area + '"; press "Find" to search.'
          : 'Location filled in; press "Find" to search.';
      },
      (err) => {
        geoBtn.disabled = false;
        if (geoStatus) geoStatus.textContent =
          'Could not get your location (' + err.message + '). Pick an area instead.';
      },
      { maximumAge: 300000, timeout: 10000 }
    );
  });
}
""".strip()


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def field(
    name: str,
    label: str,
    value: str = "",
    *,
    type: str = "text",
    hint: str = "",
    placeholder: str = "",
    step: str = "",
    wide: bool = False,
) -> str:
    """One labelled control, with its hint wired up via aria-describedby."""
    described = f' aria-describedby="{name}-hint"' if hint else ""
    step_attr = f' step="{step}"' if step else ""
    placeholder_attr = f' placeholder="{esc(placeholder)}"' if placeholder else ""
    hint_html = f'<span class="hint" id="{name}-hint">{esc(hint)}</span>' if hint else ""
    return f"""<div class="field{' wide' if wide else ''}">
  <label for="{name}">{esc(label)}</label>
  <input id="{name}" name="{name}" type="{type}" value="{esc(value)}"{step_attr}{placeholder_attr}{described}>
  {hint_html}
</div>"""


def time_value(text: object, fallback: str) -> str:
    """The value to put in an ``<input type="time">``: ``HH:MM`` within the day.

    A time input blanks any value it cannot represent, and a blanked field submits
    as empty — so a leftover ``?to=24:00`` (a window ending at midnight, which the
    CLI accepts) is clamped to the day's last minute and an unparseable value falls
    back to the field's default, rather than losing the box and failing the search.
    """
    try:
        minute = parse_clock(str(text))
    except ClockError:
        return fallback
    minute = min(minute, MINUTES_PER_DAY - 1)
    return hhmm(minute)


def select_field(
    name: str,
    label: str,
    options: list[tuple[str, str]],
    selected: str,
    hint: str = "",
) -> str:
    """One labelled ``<select>``, its hint wired up via aria-describedby."""
    body = "".join(
        f'<option value="{esc(value)}"{" selected" if value == selected else ""}>{esc(text)}</option>'
        for value, text in options
    )
    described = f' aria-describedby="{name}-hint"' if hint else ""
    hint_html = f'<span class="hint" id="{name}-hint">{esc(hint)}</span>' if hint else ""
    return f"""<div class="field">
  <label for="{name}">{esc(label)}</label>
  <select id="{name}" name="{name}"{described}>{body}</select>
  {hint_html}
</div>"""


def area_field(
    name: str,
    label: str,
    areas: list[str],
    presets: dict[str, tuple[float, float]],
    selected: str,
    hint: str = "",
) -> str:
    """The Area control: one ``<select>``, the saved places grouped at the top.

    A saved place leads the list (under "Preset") because picking one searches
    around its own coordinates; the configured districts follow (under "Area").
    A name in neither list — one the current-location button reverse-geocoded, or
    one carried in a bookmarked URL — is added as its own selected option so the
    choice survives, instead of the box silently snapping back to the first entry.
    The blank entry means "no area": the search then uses the coordinates you
    filled in, or the configured default area.
    """
    known = set(areas) | set(presets)
    body = [f'<option value=""{" selected" if not selected else ""}>{esc(AREA_ANY_LABEL)}</option>']
    if presets:
        group = "".join(
            f'<option value="{esc(name)}"{" selected" if name == selected else ""}>{esc(name)}</option>'
            for name in presets
        )
        body.append(f'<optgroup label="{esc(AREA_PRESET_GROUP)}">{group}</optgroup>')
    if areas:
        group = "".join(
            f'<option value="{esc(area)}"{" selected" if area == selected else ""}>{esc(area)}</option>'
            for area in areas
        )
        body.append(f'<optgroup label="{esc(AREA_AREA_GROUP)}">{group}</optgroup>')
    if selected and selected not in known:
        body.append(f'<option value="{esc(selected)}" selected>{esc(selected)}</option>')
    described = f' aria-describedby="{name}-hint"' if hint else ""
    hint_html = f'<span class="hint" id="{name}-hint">{esc(hint)}</span>' if hint else ""
    return f"""<div class="field wide">
  <label for="{name}">{esc(label)}</label>
  <select id="{name}" name="{name}"{described}>{''.join(body)}</select>
  {hint_html}
</div>"""


SPORT_OPTIONS_HINT = "Only sports with hourly court rentals are supported."

# Order is the display order of the categories in the results: tickets first.
CATEGORY_OPTIONS = [
    ("all", "Both: tickets and courts"),
    ("social", "Tickets only: xé vé (per person)"),
    ("court", "Courts only (per court)"),
]

AVAILABILITY_OPTIONS = [
    ("any", "Any: quote a partly-free court for its open part"),
    ("free", "Free only: courts open for the whole window"),
]


def group(title: str, key: str, *fields: str) -> str:
    """A titled block of related controls (its own responsive grid)."""
    return f"""<div class="group">
  <h3 class="group-title" id="{key}-title">{esc(title)}</h3>
  <div class="group-fields" role="group" aria-labelledby="{key}-title">
    {''.join(fields)}
  </div>
</div>"""


AREA_ANY_LABEL = "Any area"
AREA_PRESET_GROUP = "Preset"
AREA_AREA_GROUP = "Area"

# The quick windows (name, from, to, end as shown). The night one is labelled
# 18:00–24:00 but fills 23:59: a time input cannot hold "24:00", and the last
# minute of the day is the same request once the window is clipped to the
# venue's hours.
WINDOW_PRESETS = [
    ("Morning", "06:00", "12:00", "12:00"),
    ("Afternoon", "13:00", "18:00", "18:00"),
    ("Night", "18:00", "23:59", "24:00"),
]


def window_presets() -> str:
    """Quick-window buttons for the When group: they fill From/To, nothing more.

    Rendered hidden so a JavaScript-less page is unchanged — without the script
    they would do nothing, and the From/To pickers are still there to use. The
    hint doubles as the aria-live status the script writes the picked window to.
    """
    buttons = "".join(
        f'<button type="button" class="btn-secondary" data-from="{start}" '
        f'data-to="{end}">{esc(name)} <span class="range">{start}–{shown_end}</span></button>'
        for name, start, end, shown_end in WINDOW_PRESETS
    )
    return f"""<div class="field wide">
  <div class="presets" id="window-presets" role="group" aria-label="Quick time windows" hidden>
    {buttons}
  </div>
  <span class="hint" id="presets-hint" aria-live="polite">Quick windows fill From and To; the search waits for Find.</span>
</div>"""


AREA_HINT = ("Pick a district to match by name; Any area searches the coordinates you fill "
             "in, or the configured default area.")
SAVED_PLACE_HINT = ("Saved places (Preset) search around their own coordinates and fill the "
                    "latitude/longitude boxes; districts are matched by name.")


def render_form(query: dict, sports: list[tuple[str, str]], areas: list[str],
                presets: dict[str, tuple[float, float]] | None = None) -> str:
    # One picker, not two: saved places are the top group of the Area dropdown
    # (above the districts), and the script below fills a saved place's
    # coordinates so the numbers are visible and editable. The search resolves the
    # name server-side too, so a page with JavaScript disabled still searches the
    # right spot.
    presets = presets or {}
    saved_json = ""
    if presets:
        data = json.dumps(
            {name: [lat, lng] for name, (lat, lng) in presets.items()}, ensure_ascii=False
        ).replace("<", "\\u003c")  # keep a name from closing the script element
        saved_json = f'<script type="application/json" id="saved-places">{data}</script>'
    return f"""<form method="get" action="/" class="card">
  {group(
      "Where", "where",
      area_field("place", "Area", areas, presets, query.get("place") or "",
                 hint=SAVED_PLACE_HINT if presets else AREA_HINT),
      field("lat", "Latitude", query.get("lat") or "", type="number", step="any",
            hint="Leave blank when searching by area name."),
      field("lng", "Longitude", query.get("lng") or "", type="number", step="any",
            hint="Required together with latitude."),
      field("radius", "Radius", query.get("radius") or "3", type="number", step="any",
            hint="km, only used with coordinates."),
  )}
  {group(
      "When", "when",
      field("date", "Date", query.get("date") or dt.date.today().isoformat(), type="date",
            hint="Defaults to today."),
      field("from", "From", time_value(query.get("from"), "18:00"), type="time"),
      field("to", "To", time_value(query.get("to"), "21:00"), type="time"),
      window_presets(),
  )}
  {group(
      "What", "what",
      select_field("sport", "Sport", sports, query.get("sport") or "pickleball",
                   hint=SPORT_OPTIONS_HINT),
      select_field("category", "Category", CATEGORY_OPTIONS, query.get("category") or "all",
                   hint="Tickets are paid per person (xé vé); courts are rented whole."),
      select_field("availability", "Availability", AVAILABILITY_OPTIONS,
                   query.get("availability") or "any",
                   hint="Read from the branch's booking list. A partly-free court is priced "
                        "for the part that is still open; a taken court is left out."),
  )}
  <div class="actions">
    <button type="submit" class="btn-primary">Find</button>
    <button type="button" class="btn-secondary" id="geo" hidden>Use current location</button>
  </div>
  <p class="hint search-status" id="search-status" role="status" aria-live="polite"></p>
  <p class="hint geo-status" id="geo-status" aria-live="polite"></p>
</form>{saved_json}"""


def category_heading(title: str, unit: str, count: str, key: str) -> str:
    """The heading of one result category: what it is, its unit, and how many."""
    return (
        f'<h3 class="category" id="{key}-heading">{esc(title)}'
        f'<span class="unit">{esc(unit)}</span>'
        f'<span class="count">{esc(count)}</span></h3>'
    )


def render_courts(result: FindResult) -> str:
    """The court-rental category: most hours available first, then cheapest rate.

    One row per court rather than per venue: courts in one branch differ in
    availability and hours, so each is its own row. The total is not the ranking
    key — a partly-free court sells fewer hours, so its total is smaller for that
    reason alone — hence the Hours column, which is the primary sort key.
    """
    ranked = result.ranked
    heading = category_heading("Courts", "per court, most hours then cheapest rate",
                               f"{len(ranked)} court(s)", "courts")
    if not ranked:
        return heading + (
            '<p class="empty">No priced courts in this window. '
            'Try a wider radius, another area, or another date.</p>'
        )
    rows = []
    for index, opt in enumerate(ranked[:50], start=1):
        distance = f"{opt.distance_km:.1f} km" if opt.distance_km is not None else "—"
        status = availability_label(opt.available, opt.free_spans)
        target = opt.target_name or "—"
        rows.append(
            f'<tr class="{"top" if index == 1 else ""}">'
            f'<td class="rank">{index}</td>'
            f'<td class="num">{hours_label(opt.hours)}</td>'
            f'<td class="num">{money(opt.total_price)}</td>'
            f'<td class="num">{money(opt.hourly_price)}</td>'
            f'<td>{esc(opt.core.name)}</td>'
            f'<td class="status status-{esc(opt.available or "unknown")}">{esc(status)}</td>'
            f'<td class="target">{esc(target)}</td>'
            f'<td><a href="{esc(opt.booking_url)}" rel="noopener">{esc(opt.branch.name)}</a>'
            f'<div class="venue-sub">{esc(opt.branch.address)}</div></td>'
            f'<td class="num">{distance}</td></tr>'
        )
    return heading + f"""<div class="table-wrap">
<table>
  <caption>Most hours available first, then cheapest per hour: one row per court</caption>
  <thead><tr>
    <th scope="col">#</th>
    <th scope="col" class="num">Hours</th>
    <th scope="col" class="num">Total</th>
    <th scope="col" class="num">Per hour</th>
    <th scope="col">Court</th>
    <th scope="col">Status</th>
    <th scope="col">Target</th>
    <th scope="col">Venue</th>
    <th scope="col" class="num">Distance</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>"""


def render_social(result: FindResult) -> str:
    """The ticket category ("xé vé"): one ticket puts one person on a court.

    Ranked like the courts table — one row per ticket, nearest venue first, then
    cheapest, then the session covering most of the requested window.
    """
    tickets = result.ranked_social
    heading = category_heading("Tickets (xé vé)", "per person, nearest then cheapest then fullest window",
                               f"{len(tickets)} ticket(s)", "tickets")
    if not tickets:
        return heading + (
            '<p class="empty">No tickets on sale in this window. '
            'Try a wider radius, another area, or another date.</p>'
        )
    rows = []
    for index, session in enumerate(tickets[:50], start=1):
        starts = f"{session.start:%H:%M}" if session.start else "—"
        ends = f"{session.end:%H:%M}" if session.end else "—"
        # The venue links to AloBooking exactly as a court row does, so a ticket
        # opens the branch to book from. A session without a branch keeps the
        # plain name it has always shown.
        venue = (
            f'<a href="{esc(session.booking_url)}" rel="noopener">{esc(session.branch.name)}</a>'
            if session.branch
            else "—"
        )
        address = session.branch.address if session.branch else ""
        distance = f"{session.distance_km:.1f} km" if session.distance_km is not None else "—"
        rows.append(
            f'<tr class="{"top" if index == 1 else ""}">'
            f'<td class="rank">{index}</td>'
            f'<td class="num">{money(session.ticket_price)}</td>'
            f'<td>{esc(session.name)}<div class="venue-sub">'
            f"{esc(', '.join(session.court_names))}</div></td>"
            f'<td>{starts}</td><td>{ends}</td><td class="num">{session.spots_left}</td>'
            f'<td>{venue}<div class="venue-sub">{esc(address)}</div></td>'
            f'<td class="num">{distance}</td></tr>'
        )
    return heading + f"""<div class="table-wrap">
<table>
  <caption>Nearest first, then cheapest: every ticket on sale in this window, one row per ticket</caption>
  <thead><tr>
    <th scope="col">#</th>
    <th scope="col" class="num">Ticket</th><th scope="col">Session</th><th scope="col">Starts</th>
    <th scope="col">Ends</th><th scope="col" class="num">Spots left</th><th scope="col">Venue</th>
    <th scope="col" class="num">Distance</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>"""


def render_results(result: FindResult) -> str:
    query = result.query
    area = area_label(query)
    start = hhmm(query.start_minute)
    start_dt, end_dt = window_bounds(query.day, query.start_minute, query.end_minute)
    end_label = f"{end_dt:%H:%M}" if start_dt.date() == end_dt.date() else f"{end_dt:%d/%m %H:%M}"
    # Tickets lead: they are the cheapest way onto a court, and the category the
    # operator wants seen first.
    categories = []
    if query.wants_social:
        categories.append(render_social(result))
    if query.wants_courts:
        categories.append(render_courts(result))
    return f"""<section aria-labelledby="results-heading">
  <h2 id="results-heading">Results: {esc(result.sport_name)}</h2>
  <p class="sub">{esc(area)} · {query.day:%d/%m/%Y} {start}–{end_label} ·
     {result.branches_scanned} branches scanned · updated {result.generated_at:%H:%M}</p>
  {''.join(categories)}
</section>"""


EMPTY_RESULT_HTML = (
    '<p class="empty">Choose an area or your current location, set a date and time '
    'window, then press Find to compare court and ticket prices.</p>'
)


def render_results_region(result: FindResult | None = None, error: str | None = None) -> str:
    """The swappable results area: the results, an error, or the idle prompt.

    Kept a pure function so the fetch enhancement's endpoint (:mod:`web`) returns
    exactly the markup the full page embeds in its ``#results`` container — the
    two renders can never drift.
    """
    if error:
        return f'<p class="error" role="alert">Error: {esc(error)}</p>'
    if result is not None:
        return render_results(result)
    return EMPTY_RESULT_HTML


def render_page(
    *,
    sports: list[tuple[str, str]],
    areas: list[str],
    presets: dict[str, tuple[float, float]] | None = None,
    query: dict | None = None,
    result: FindResult | None = None,
    results_html: str | None = None,
    error: str | None = None,
) -> str:
    """The whole page: header, search form, then results (or an error).

    *results_html* lets the caller hand in an already-rendered results region —
    the cache stores the fragment so a reload can re-embed it verbatim without
    holding the result object. When it is None the region is rendered from
    *result*/*error* as usual.
    """
    region = results_html if results_html is not None else render_results_region(result, error)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{BRAND} | {BRAND_TAGLINE}</title>
<link rel="icon" href="{FAVICON}" type="image/svg+xml">
<style>{STYLE}</style>
</head>
<body>
<header>
  <a class="skip-link" href="#content">Skip to content</a>
  <h1><a href="/">{LOGO}<span>{BRAND}</span></a></h1>
  <p class="sub">Find the cheapest pickleball courts and tickets (xé vé) on AloBooking,
     by area and time window.</p>
</header>
<main id="content" tabindex="-1">
  <h2>Search criteria</h2>
  {render_form(query or {}, sports, areas, presets)}
  <div id="results" aria-live="polite" aria-busy="false">{region}</div>
</main>
<footer>
  <div class="footer-grid">
    <div class="footer-block">
      <h2 class="footer-title">What Alobo does</h2>
      <p>It reads AloBooking's published prices and live booking list, then ranks the
         cheapest court or ticket for your window. It never books and never pays, so
         booking stays with you on AloBooking.</p>
    </div>
    <nav class="footer-block" aria-labelledby="footer-links-title">
      <h2 class="footer-title" id="footer-links-title">Quick links</h2>
      <ul class="footer-links">
        <li><a href="/report.json">Latest report (JSON)</a></li>
        <li><a href="/health">Service status</a></li>
        <li><a href="?place=H%C3%A0%20N%E1%BB%99i&amp;from=18:00&amp;to=21:00">Example search: Hà Nội, 18:00–21:00</a></li>
      </ul>
    </nav>
  </div>
  <p class="footer-fineprint">Prices come from the tariff each branch publishes for a
     one-time rental and, for tickets, the listed price per person. Source: AloBooking's
     public API (datlich.alobo.vn).</p>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
"""


# ---------------------------------------------------------------- status page
# The operational view (:mod:`alobo_bot.metrics` counts; this renders). It is not
# linked from the public page — reach it at /status directly. A plain server-side
# render like the rest of the app: no JavaScript, so it works from curl too, and a
# meta refresh keeps a browser tab current for a human watching the service.

STATUS_REFRESH_SECONDS = 30

ENDPOINT_HEADERS = [("Path", False), ("Requests", True)]
CODE_HEADERS = [("Status", False), ("Class", False), ("Count", True)]
CLIENT_HEADERS = [
    ("Client", False), ("Requests", True), ("Last path", False),
    ("Last status", True), ("Last seen", False),
]
REPORT_HEADERS = [("File", False), ("Modified", False), ("Size", True)]


def format_uptime(seconds: float) -> str:
    """Uptime as ``2d 3h 4m``, dropping units that are zero above the minute."""
    total = int(max(seconds, 0))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    if not days:
        parts.append(f"{secs}s")
    return " ".join(parts)


def format_moment(moment: dt.datetime | None) -> str:
    """A timestamp for the status tables, or an em dash when there is none."""
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else "—"


def format_ms(value: float | None) -> str:
    """A duration in milliseconds, or an em dash before the first request."""
    if value is None:
        return "—"
    return f"{value:,.0f} ms" if value >= 10 else f"{value:.1f} ms"


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_window(seconds: float) -> str:
    """A duration as a coarse window, e.g. ``15 min``."""
    minutes = float(seconds) / 60
    return f"{minutes:.0f} min" if minutes < 60 else f"{minutes / 60:.1f} h"


def format_age(seconds: float | None) -> str:
    """A duration as an age, e.g. ``2m 5s ago`` (an em dash when unknown)."""
    return "—" if seconds is None else f"{format_uptime(seconds)} ago"


def format_bytes(value: int) -> str:
    """A byte count in its largest sensible unit, e.g. ``12.3 KB``."""
    size = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _count(value: int) -> str:
    return f"{value:,}"


def metric(label: str, value: str, *, hint: str = "", tone: str = "") -> str:
    """One number on the status page: its value over its label."""
    class_name = f"metric metric-{tone}" if tone else "metric"
    hint_html = f'<span class="metric-hint">{esc(hint)}</span>' if hint else ""
    return (
        f'<div class="{class_name}">'
        f'<span class="metric-value">{esc(value)}</span>'
        f'<span class="metric-label">{esc(label)}</span>'
        f"{hint_html}</div>"
    )


def status_table(caption: str, headers: list[tuple[str, bool]], rows: list[list[str]]) -> str:
    """A small table for the status page; numeric columns get the tabular style."""
    head_cells = []
    for text, numeric in headers:
        css = ' class="num"' if numeric else ""
        head_cells.append(f'<th scope="col"{css}>{esc(text)}</th>')
    if rows:
        body_rows = []
        for row in rows:
            cells = []
            for index, cell in enumerate(row):
                numeric = headers[index][1] if index < len(headers) else False
                css = ' class="num"' if numeric else ""
                cells.append(f"<td{css}>{esc(cell)}</td>")
            body_rows.append(f"<tr>{''.join(cells)}</tr>")
        body = "".join(body_rows)
    else:
        body = f'<tr><td colspan="{max(len(headers), 1)}" class="venue-sub">no data yet</td></tr>'
    return (
        '<div class="table-wrap"><table>'
        f"<caption>{esc(caption)}</caption>"
        f'<thead><tr>{"".join(head_cells)}</tr></thead>'
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )


def _status_section(key: str, title: str, lead: str, body: str) -> str:
    lead_html = f'<p class="lead">{esc(lead)}</p>' if lead else ""
    return (
        f'<section class="status" aria-labelledby="{key}-title">'
        f'<h2 id="{key}-title">{esc(title)}</h2>{lead_html}{body}</section>'
    )


def render_status(
    snapshot: Snapshot,
    *,
    version: str,
    sport: str,
    state_path: str = "",
    report_dir: str = "",
    reports: list[dict] | None = None,
    cache: dict | None = None,
    refresh_seconds: int = STATUS_REFRESH_SECONDS,
) -> str:
    """The whole ``/status`` page from one :class:`~alobo_bot.metrics.Snapshot`.

    Pure, like the rest of :mod:`webui`: everything it shows comes from the
    snapshot, the cache's own ``stats()`` and the report listing the caller reads
    off disk, so it can be rendered in a test without a server.
    """
    service = "".join([
        metric("State", "Searching" if snapshot.busy else "Idle",
               hint="one search at a time", tone="live" if snapshot.busy else ""),
        metric("Uptime", format_uptime(snapshot.uptime_seconds), hint="this process"),
        metric("Started", format_moment(snapshot.started_at)),
        metric("Version", version),
        metric("Sport", sport),
        metric("Last search", format_moment(snapshot.last_run)),
    ])
    traffic = "".join([
        metric("Requests", _count(snapshot.requests)),
        metric("In flight", _count(snapshot.in_flight)),
        metric("Errors", _count(snapshot.errors), tone="warn" if snapshot.errors else ""),
        metric("Error rate", format_percent(snapshot.error_rate),
               tone="warn" if snapshot.errors else ""),
        metric("Avg response", format_ms(snapshot.latency_ms_avg)),
        metric("Max response", format_ms(snapshot.latency_ms_max)),
    ])
    searches = "".join([
        metric("Completed", _count(snapshot.searches)),
        metric("Failures", _count(snapshot.search_failures),
               tone="warn" if snapshot.search_failures else ""),
        metric("Busy rejections", _count(snapshot.search_busy)),
    ])
    cache = cache or {}
    entries = cache.get("entries")
    capacity = cache.get("capacity")
    cache_fill = (
        f"{_count(entries)} / {_count(capacity)}"
        if isinstance(entries, int) and isinstance(capacity, int) else "—"
    )
    window = cache.get("reuse_seconds")
    cache_metrics = "".join([
        metric("Entries", cache_fill, hint="recent searches kept"),
        metric("Reuse window", format_window(window) if window else "—",
               hint="how long a result is reused"),
        metric("Newest entry", format_age(cache.get("newest_age"))),
        metric("Oldest entry", format_age(cache.get("oldest_age"))),
        metric("Cache hits", _count(snapshot.cache_hits)),
        metric("Cache misses", _count(snapshot.cache_misses)),
        metric("Cache hit rate", format_percent(snapshot.cache_hit_rate)),
    ])
    client_metrics = "".join([
        metric("Unique clients", _count(snapshot.clients_seen),
               hint=f"last {len(snapshot.clients)} listed"),
    ])

    endpoint_rows = [
        [path, _count(count)]
        for path, count in sorted(snapshot.by_path.items(), key=lambda item: (-item[1], item[0]))
    ]
    status_rows = [
        [str(code), status_class(code), _count(count)]
        for code, count in sorted(snapshot.by_status.items())
    ]
    client_rows = [
        [client.ip, _count(client.requests), client.last_path, str(client.last_status),
         format_moment(client.last_seen)]
        for client in snapshot.clients
    ]
    report_rows = [
        [report["name"], format_moment(report["modified"]), format_bytes(report["bytes"])]
        for report in (reports or [])
    ]

    error_html = (
        f'<p class="error" role="alert">Last search error: {esc(snapshot.last_error)}</p>'
        if snapshot.last_error else ""
    )
    reports_lead = (
        f"Reports on disk under {esc(report_dir)}: the bot's searchable output."
        if report_dir else "Reports written by each run."
    )
    cache_lead = (
        f"Rendered results kept for a repeated search, persisted at {esc(cache['path'])}."
        if cache.get("path")
        else "Rendered results kept for a repeated search, in memory only."
    )
    if state_path:
        persistence = (
            f"Counters and the recent-search cache are persisted on the host under "
            f"<code>{esc(state_path)}</code> and resumed on restart; only the process "
            f"uptime resets."
        )
    else:
        persistence = "Counters cover this process since it started."
    refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""
    )
    refresh_note = f"Auto-refreshes every {int(refresh_seconds)}s. " if refresh_seconds else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>{BRAND} | Status</title>
<link rel="icon" href="{FAVICON}" type="image/svg+xml">
<style>{STYLE}</style>
</head>
<body>
<header>
  <a class="skip-link" href="#content">Skip to content</a>
  <h1><a href="/">{LOGO}<span>{BRAND}</span></a></h1>
  <p class="sub">Service status: live traffic, client and search metrics for this process.</p>
</header>
<main id="content" tabindex="-1">
  {error_html}
  {_status_section("service", "Service", "", f'<div class="status-grid">{service}</div>')}
  {_status_section("traffic", "Traffic", "", f'<div class="status-grid">{traffic}</div>')}
  {_status_section("searches", "Searches", "", f'<div class="status-grid">{searches}</div>')}
  {_status_section("cache", "Cache", cache_lead,
                   f'<div class="status-grid">{cache_metrics}</div>')}
  {_status_section("clients", "Clients", "",
                   f'<div class="status-grid">{client_metrics}</div>'
                   + status_table("Most recently active clients", CLIENT_HEADERS, client_rows))}
  {_status_section("reports", "Reports", reports_lead,
                   status_table("Report files (newest first)", REPORT_HEADERS, report_rows))}
  {_status_section("endpoints", "Endpoints", "",
                   status_table("Requests by path", ENDPOINT_HEADERS, endpoint_rows)
                   + status_table("Responses by status code", CODE_HEADERS, status_rows))}
</main>
<footer>
  <p class="footer-fineprint">{refresh_note}{persistence}
     Machine-readable at <a href="/status.json">/status.json</a>.</p>
</footer>
</body>
</html>
"""

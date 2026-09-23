# alobo-bot

Read-only helper that answers one question: **for a given location and time
window, where is the cheapest pickleball court on
[AloBooking](https://datlich.alobo.vn/)?**

It talks straight to the AloBooking backend API (no browser, no login), prices
every court in range for the requested window, checks which of them are still
free, and prints a ranked list. It never books, pays, or writes anything back.

## Two ways to use it

- **Web page** (`alobo-bot serve`) — a server-rendered form: pick an area (Hà Nội
  districts) or tap **Use current location** to fill in your coordinates and
  area, choose a time window, get the ranked table. Works without JavaScript.
- **CLI** (`alobo-bot find ...`) — the same search from the terminal, with JSON
  output and saved reports for scripting.

## Web page

```bash
alobo-bot serve --port 8085      # then open http://localhost:8085
```

A live deployment runs at **https://alobo.atento.vn** — same page, nothing to
install (this is the bot's own UI; the AloBooking API it queries is separate).

The page is a plain HTML form (area, coordinates, date, from/to, sport,
category, availability), with an English UI. The **Area** box is a dropdown with
the saved places (`search.presets`, a name for a coordinate pair) leading it under
**Preset**, then the configured districts of `search.areas` under **Area**, and an
**Any area** entry that leaves the search to the coordinates you enter (or the
configured default area). Picking a saved place fills the latitude/longitude boxes
with its coordinates (so you can see or adjust them) and searches a radius around
that spot; naming it in the CLI's `--place` does the same without the page. The
**Category** control picks what to report: both
categories (the default), tickets only, or courts only. The **Availability**
control either tags every court available/booked for the window (the default) or
hides the courts already taken, so every listed venue is bookable. The
**From**/**To** boxes are native time pickers, and the **Morning** (06:00–12:00),
**Afternoon** (13:00–18:00) and **Night** (18:00–24:00, filled as 23:59 since a time
input cannot hold 24:00) buttons beneath them fill both with a standard window —
like the location button they only fill the fields, so the window stays visible,
editable and shareable in the URL before you press **Find**. The buttons need
JavaScript; the pickers themselves do not. The
**Use current location** button asks the browser for your coordinates,
reverse-geocodes them (the free, key-less BigDataCloud client API) to set the
**Area** dropdown to the nearest area name — adding it to the list when no district
matches — and does not search until you press
**Find**. The browser calls the geocoder directly; when it is
unreachable or offline the coordinates are still filled and the area is left at
**Any area**. The button is hidden when JavaScript or geolocation is
unavailable; browsers only grant location on HTTPS or `localhost`.
All state lives in the query string, so a search is just a URL you can bookmark
or share:

```
http://localhost:8085/?place=Hà Nội&date=2026-09-25&from=18:00&to=21:00&category=social
```

Results render as two labelled categories, each priced in its own unit — one row
per court, one per ticket, since courts in the same venue differ in hours and
availability. Courts are ranked by the hours they can sell and then by the hourly
rate (not the total); tickets by distance, then price, then the session covering
most of the requested window. Each row carries the winning highlight, the hours,
per-hour price, distance and a link to the venue on AloBooking.
Pressing **Find** does not reload the page: with JavaScript the form is
intercepted, a "Searching AloBooking…" status appears, and the results are
fetched into the page as a fragment — the form and your picks stay put. The URL
still gains the query string (so the search stays bookmarkable and the Back
button re-runs it). Without JavaScript the plain `GET /` form submits and the
server renders the whole page, exactly as before. Searches run in a
worker thread and are single-flighted; several branches are priced in parallel,
so a typical area returns in a few seconds.

Because the whole search lives in the URL, a refresh would otherwise repeat that
fan-out every time. Instead the server caches each run's result and **revisiting
the same criteria re-renders it** — no API calls — so F5, or returning to a
bookmarked search you ran a moment ago, is instant; **Find** still fetches fresh.
The cache is bounded and time-limited (`RESULT_CACHE_MAX` searches,
`RESULT_REUSE_SECONDS` in `web.py`, 32 and 15 minutes), so a run past the window
searches again rather than presenting stale prices as current.

## What it does

```
$ alobo-bot find --place "Cầu Giấy, Hà Nội" --date 2026-09-25 --from 18:00 --to 21:00
Cheapest Pickleball — 25/09/2026 18:00-21:00
Area: Cầu Giấy, Hà Nội · 6 branch(es) scanned

Tickets (xé vé) — per person, nearest then cheapest then longest in the window · 1 ticket(s)
  - 50.000đ · Social không giới hạn @ 19:00-21:00 · 6 spots · Sân Pickleball ABC
       Số 12 Trần Thái Tông, Cầu Giấy, Hà Nội

Courts — per court, most hours then cheapest rate · 4 court(s)
  #  hours         price       /hour    dist  status               court · venue
  1     3h      240.000đ     80.000đ   3.2km  available            Sân 1 · Sân Pickleball ABC
       Số 12 Trần Thái Tông, Cầu Giấy, Hà Nội · target: Khách hàng
  2     3h      240.000đ     80.000đ   3.2km  available            Sân 2 · Sân Pickleball ABC
       Số 12 Trần Thái Tông, Cầu Giấy, Hà Nội · target: Khách hàng
  3     3h      300.000đ    100.000đ   2.1km  available            Sân 1 · Sân Pickleball DEF
       Số 8 Dịch Vọng Hậu, Cầu Giấy, Hà Nội · target: Pickleball
  4     1h       90.000đ     90.000đ   1.4km  partial 20:00-21:00  Sân 1 · Sân Pickleball XYZ
       Ngõ 9 Dịch Vọng Hậu, Cầu Giấy, Hà Nội · target: Pickleball
```

Ranked by the hours a court can sell, then by the hourly rate — the one-hour slot
comes last despite having the second-cheapest rate and the smallest total.

- **Two categories, never mixed.** Tickets (`socialOneTime`) are priced per
  person and courts (`oneTime`) per court, so each is ranked in its own list —
  tickets first, since they are the cheapest way onto a court. `--category`
  (or the page's **Category** control) narrows the run to one of them; `all`
  does both.
- **Withdrawn venues are left out.** The branch list keeps locked and removed
  venues (`status` `-1`/`-2` — names like "789 Pickleball Club (đã khóa tạo cn
  mới)" or "(khóa)Stamina …") so the app can still show them a "closed" page, but
  their courts and tickets are no longer on sale. The bot drops them before
  pricing, so every listed result is a venue you can actually book.
- **Availability checked.** Each court is tagged from the branch's own booking
  list — `available`, `partial` with the still-open spans (a court taken 18:00–20:00 of
  an 18:00–21:00 search shows `partial 20:00-21:00`), or `?` when the list could
  not be read. A court taken for the *whole* window is left out entirely: it has
  nothing left to sell, so there is no price to quote.
- **You are quoted for what you can book.** A partly-free court is priced for its
  open time only, not the whole window: asked for 18:00–24:00 at a venue whose
  last hour is the only one free, you see that hour's price (e.g. `200.000đ`,
  1 hour) rather than five hours you cannot have. The status names the open part,
  and `hours` in the JSON is how long it is. `--availability free` (or the page's
  **Availability** control) narrows the list to courts open for the whole window.
- **Opening hours respected.** A branch only sells its working hours, so a window
  is clipped to them before pricing *and* before checking availability: asking
  for 18:00–24:00 at a venue that shuts at 22:00 is priced and checked for
  18:00–23:00 — its last sold hour starts at closing time — rather than charging
  a base rate for an hour nobody can book, or calling it free. An hour the venue
  publishes no rate for is not on sale either, so it is trimmed off the window
  instead of being quoted at nothing. `hours` in the JSON says how long the
  priced window turned out to be.
- **One row per court, ranked by hours available then rate.** Every priced court
  is listed on its own. Courts in the same branch are separate bookings with
  their own hours and availability, so a venue that rents several courts
  contributes a row each — collapsing them to the venue's cheapest court would
  hide one that is free when the cheap one is not. The ranking keys are, in
  order, **how many hours the court can sell** and then the **hourly rate** —
  never the total: a partly-free court is quoted for fewer hours, so its total is
  smaller for that reason alone, and ordering by it would put a one-hour slot at
  200.000đ above a six-hour court at 120.000đ/h. The Hours column is that primary
  key; ties go to the nearer branch.
- **Time-of-day pricing honoured**: each branch publishes its rates per time
  block (peak/off-peak, weekday/weekend), and they are applied per hour —
  including windows that straddle a boundary or cross midnight.
- **Priced under a named tariff.** A branch prices each court type per *tariff*
  — the app's "đối tượng áp dụng": the walk-up rate, a quarterly-payer discount,
  a monthly ticket, a ball machine. Every court is quoted at the branch's
  standard customer rate; `--target` (CLI only — the page has no control for it)
  names another by id or name, and every row says which target it used.
- Output as text, JSON, or a saved markdown + JSON report.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,server]"
```

## Usage

```bash
# cheapest pickleball courts near a place name, tonight
alobo-bot find --place "Hà Nội"

# by coordinates with a radius, a specific day and window
alobo-bot find --lat 21.0285 --lng 105.8542 --radius 5 --date 2026-09-25 --from 6:00 --to 9:00

# only the ticket ("xé vé") category, or only whole courts
alobo-bot find --place "Hà Nội" --category social
alobo-bot find --place "Hà Nội" --category court

# hide courts already booked for the whole window
alobo-bot find --place "Hà Nội" --from 18:00 --to 21:00 --availability free

# price under a named tariff ("đối tượng áp dụng") instead of the standard one
alobo-bot find --place "Hà Nội" --target kh

# machine-readable, and persist a report to data/reports/
alobo-bot find --place "Đà Nẵng" --json --out

# list the sport types the API exposes (pickleball == 5)
alobo-bot sports
```

Flags: `--place` (an area or a saved place), `--preset NAME` (the same saved
place, explicit), `--lat/--lng/--radius`, `--date YYYY-MM-DD`, `--from HH:MM`,
`--to HH:MM`, `--sport`, `--category all|court|social`,
`--availability any|free`, `--target ID|NAME`, `--limit`, `--json`, `--out`,
`--config`.

### HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the search page; add query params to run a search |
| GET | `/results` | results-only HTML fragment, same query params as `/` (the page's fetch target) |
| POST | `/find` | same search, markdown response (for scripts) |
| GET | `/report.json` | JSON snapshot of the last run |
| GET | `/health` | service state (busy, last run, last error) |

```bash
curl -X POST 'localhost:8085/find?place=H%C3%A0%20N%E1%BB%99i&from=18:00&to=21:00'
```

## Configuration

`config.yaml` (deep-merged over built-in defaults; delete a key to revert).
Notable keys: `api.base_url` / `api.global_url`, `api.version`,
`search.default_place`, `search.radius_km`, `search.max_branches`,
`search.category` (the default category the page and CLI start from),
`search.availability` (`any` also lists partly-free courts, `free` only those open
for the whole window),
`search.target` (the tariff to price under; blank = each type's standard one — the
page always uses this, only `find --target` overrides it),
`search.areas` (the districts in the Area dropdown), `search.presets` (saved
places: name -> `{lat, lng}`, the dropdown's **Preset** group and usable as
`--place` / `--preset`), `report.dir`, `raw.dir`. There are
**no user secrets** — see below.

## How the API is called

`datlich.alobo.vn` is a Flutter web app, so there is no HTML to scrape; the bot
reproduces the app's own API calls.

**Hosts.** The `*.alobo.vn` API hosts are behind a Cloudflare bot check that
answers non-browser clients with `403 error code: 1010`. The same backends are
reachable directly through their Cloud Run origins, which serve the identical
API without the check:

| Public host | Origin used | Serves |
|---|---|---|
| `user-api-new.alobo.vn` | `user-app-new-ootprnz4oa-uc.a.run.app` | `/api/v1/...` |
| `user-global.alobo.vn` | `user-global-ootprnz4oa-uc.a.run.app` | `/v2/user/branch/...` |

**Request headers.** Every call sends `x-name-app: alobo-user`, `x-platform: web`,
`x-version-app` (app build, `2.10.3`), `x-custom-lang`, and:

```
x-user-app = sha256_hex("<MM/dd/yyyy, HH:mm>@935b1fccd4bc45a12af095bf0bafa723")
```

The timestamp is **UTC**. The server checks it against its own clock and allows
only a couple of minutes of skew, so the header is regenerated per request — and
it must not come from the host's local time. A venue-local `TZ`
(`Asia/Ho_Chi_Minh` in `docker-compose.yml`) puts the stamp 7 hours off and every
call comes back `401` ("Vui lòng kiểm tra thời gian trên thiết bị của bạn"), so
the signature always uses `datetime.now(datetime.timezone.utc)`.

**Bodies.** Both directions are AES-256-CBC (PKCS#7), base64-wrapped. A POST
body is `{"enc": true, "data": "<base64>"}` with the fixed key
`1357924680_0123456789_0123987456` and IV `AjIrEp582ksFPaFKw4xwuw==`. Every
response comes back encrypted too, but with a **different key** and a
**per-response IV**: `{"enc": true, "data": "<base64>", "iv": "<base64>"}`,
opened with the key `Al0b0@Doczy2026_1123_Secret_0804` and the envelope's own
`iv` (cleartext `statusCode`/`message`/`_metadata` siblings ride alongside).

None of these constants is a user secret — they all ship inside the public web
app — but they are **per app release**, so they live in `config.yaml` /
`crypto.py` only so an update can be tracked without touching code. When the web
app ships a new build they change; see
[docs/updating-api-keys.md](docs/updating-api-keys.md) to re-derive them (the
live values are XOR-obfuscated in the bundle, not the plain literals).

**Endpoints used.**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/public/sport-type` | sport keys ↔ int values (pickleball = 5) |
| GET | `/v2/user/branch/branches` | full branch list (cursor `lastFetchBranch`) |
| POST | `/v2/user/branch/branches_first` | branches near a coordinate |
| GET | `/v2/user/branch/get_branch/{id}` | branch detail (sport `type`, location) |
| GET | `/v2/user/branch/get_cores/{id}` | courts, each with a `setting` |
| GET | `/v2/user/branch/get_core_types/{id}` | price tables per tariff; id matches core `setting` |
| GET | `/v2/user/branch/get_onetime_bookings?branchId=&startDate=&endDate=` | the branch's existing bookings, i.e. the courts that are *taken* |
| POST | `/v2/user/branch/get_filtered_branch_booking` | tickets for a date range (`socialOneTime`) |

A court's price comes from the `get_core_types` entry whose `id` equals the
core's `setting`, and within it from the **tariff** the booking is made under:
each type publishes a generic `normalPrice` plus, under `targets`, one table per
"đối tượng áp dụng" (the app makes the booker pick one). The bot reads the
table's `specialPrice` blocks — `{"time": "17:00-23:00", "dateRangeWeek": "1-5",
"priceOneTime": 200000}` — and sums them hour by hour over the window, falling
back to the tariff's base rate outside every block. Blank `--target` uses the
type's generic customer tariff (id `kh`, else `default`, else the first
unhidden); a type with no `targets` prices from its own table.

Which sport a court plays is read from the core's own
`yardType`, which the API leaves at `-1` for most branches — so the bot falls
back to the `yardType` of the *area* the court sits in (the only thing that
separates pickleball from football in a mixed branch), and finally to the sport
being searched for. Court availability comes from
`get_onetime_bookings` — the branch's own booking list, public and login-free —
and is what the `free`/`booked`/`partial` status is read from; see
[docs/court-availability.md](docs/court-availability.md).

## Limitations

- **Availability is best-effort, and per window.** The status describes the exact
  window you asked for: a court taken 18:00–20:00 is `booked` for 18:00–21:00 and
  `partial 20:00-21:00` for it. A `?` means the booking list could not be read —
  the API rejects a date outside the branch's booking window, and its clock is the
  venue's (UTC+7), so a date that is already past in Vietnam comes back empty.
  Prices follow availability and opening hours: a court is quoted for the part of
  the window it can actually sell (see **You are quoted for what you can book**),
  so two rows with different `hours` are not directly comparable on total alone —
  which is why the courts table is ranked by hours available and then the hourly
  rate, with the total shown only for reference.
- **Location is best-effort.** `--place` first checks the saved places, then does a
  diacritic-insensitive text match
  on branch name + address; `--lat/--lng` uses a true distance filter. The search
  itself uses no geocoding service — the web page's **Use current location**
  button calls a third-party reverse geocoder (BigDataCloud) only to label the
  **Area** box, and the search still runs on the coordinates.
- Prices reflect the branch's published price table for one-time bookings,
  under one tariff. The app makes the booker choose a tariff ("đối tượng áp
  dụng"), so there is no server-side default; the bot prices the generic
  customer rate (`kh`/`default`) unless `--target` names another, and a court
  type that does not publish that tariff falls back to its own standard rate.
  Dated promotional windows (`dateRange`/`level` inside `specialPrice`) are not
  filtered: within a tariff the first matching time block wins.
- **Tickets come from a coarser endpoint.** `get_filtered_branch_booking`
  ignores `dateStart`/`dateEnd`, `bookingType`, `types` and `branchIds`, and
  returns every ticket event nationwide; the bot filters that down to the search
  area, to tickets for the searched sport (each booking names its own
  `sportType`) and to sessions that *start* inside the window. Each branch it
  returns is a ticket candidate in its own right, so a venue is not dropped just
  because the court shortlist's `max_branches` cap did not reach it. A session
  the API does not return is invisible to the bot, so tickets are reliable for
  near dates and thin for dates far out.

## Development

```bash
.venv/bin/pytest            # pure-logic tests, no network
docker compose up -d --build
```

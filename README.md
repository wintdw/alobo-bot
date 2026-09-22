# alobo-bot

Read-only helper that answers one question: **for a given location and time
window, where is the cheapest pickleball court on
[AloBooking](https://datlich.alobo.vn/)?**

It talks straight to the AloBooking backend API (no browser, no login), prices
every court in range for the requested window, and prints a ranked list. It never
books, pays, or writes anything back.

## Two ways to use it

- **Web page** (`alobo-bot serve`) — a server-rendered form: type an area and a
  time window, get the ranked table. Works without JavaScript.
- **CLI** (`alobo-bot find ...`) — the same search from the terminal, with JSON
  output and saved reports for scripting.

## Web page

```bash
alobo-bot serve --port 8084      # then open http://localhost:8084
```

The page is a plain HTML form (area or coordinates, date, from/to, sport). All
state lives in the query string, so a search is just a URL you can bookmark or
share:

```
http://localhost:8084/?place=Hà Nội&date=2026-09-25&from=18:00&to=21:00
```

Results render as a semantic table — cheapest first, with the winning row
highlighted, per-hour price, distance and a link to the venue on AloBooking —
followed by any social/open-play sessions in that window. Searches run in a
worker thread and are single-flighted; several branches are priced in parallel,
so a typical area returns in a few seconds.

## What it does

```
$ alobo-bot find --place "Cầu Giấy, Hà Nội" --date 2026-09-25 --from 18:00 --to 21:00
Cheapest Pickleball courts — 25/09/2026 18:00-21:00
Area: Cầu Giấy, Hà Nội · 6 branch(es) scanned

  #         price      /hour    dist  venue / court
  1      240.000đ    80.000đ   3.2km  Sân Pickleball ABC / Sân 2
       Số 12 Trần Thái Tông, Cầu Giấy, Hà Nội
  ...

Social / open-play sessions in this window (price per person):
  - 50.000đ · Social không giới hạn @ 19:00 · 6 spots
```

- **Cheapest ranking** across every court, with per-hour price and distance.
- **Time-of-day pricing honoured**: peak/off-peak "special price" windows
  (e.g. `5:30-10:00` cheaper) are applied per hour, including windows that
  straddle a boundary or cross midnight.
- **Social/open-play sessions** listed separately, since those are priced per
  person rather than per court.
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

# machine-readable, and persist a report to data/reports/
alobo-bot find --place "Đà Nẵng" --json --out

# list the sport types the API exposes (pickleball == 5)
alobo-bot sports
```

Flags: `--place`, `--lat/--lng/--radius`, `--date YYYY-MM-DD`, `--from HH:MM`,
`--to HH:MM`, `--sport`, `--limit`, `--json`, `--out`, `--config`.

### HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the search page; add query params to run a search |
| POST | `/find` | same search, markdown response (for scripts) |
| GET | `/report.json` | JSON snapshot of the last run |
| GET | `/health` | service state (busy, last run, last error) |

```bash
curl -X POST 'localhost:8084/find?place=H%C3%A0%20N%E1%BB%99i&from=18:00&to=21:00'
```

## Configuration

`config.yaml` (deep-merged over built-in defaults; delete a key to revert).
Notable keys: `api.base_url` / `api.global_url`, `api.version`,
`search.default_place`, `search.radius_km`, `search.max_branches`, `report.dir`,
`raw.dir`. There are **no user secrets** — see below.

## How the API is called

`datlich.alobo.vn` is a Flutter web app, so there is no HTML to scrape; the bot
reproduces the app's own API calls.

**Hosts.** The `*.alobo.vn` API hosts are behind a Cloudflare bot check that
answers non-browser clients with `403 error code: 1010`. The same backends are
reachable directly through their Cloud Run origins, which serve the identical
API without the check:

| Public host | Origin used | Serves |
|---|---|---|
| `user-api-new.alobo.vn` | `user-app-new-vk7r7j5t3q-uc.a.run.app` | `/api/v1/...` |
| `user-global.alobo.vn` | `user-app-vk7r7j5t3q-uc.a.run.app` | `/v2/user/branch/...` |

**Request headers.** Every call sends `x-name-app: alobo-user`, `x-platform: web`,
`x-version-app` (app build, `2.10.3`), `x-custom-lang`, and:

```
x-user-app = sha256_hex("<MM/dd/yyyy, HH:mm>@Alobo-User-Key-2026")
```

The timestamp uses the caller's local clock; the server rejects stale ones, so
the header is regenerated per request.

**POST bodies.** Encrypted AES-256-CBC (PKCS#7) and wrapped as
`{"enc": true, "data": "<base64>"}`, with the fixed key
`0123456789_0123456789_0123456789` and IV `bmjSyRV4MLcxfvEWGJdqXQ==`. These
constants — and the signing key — ship inside the public web app, so they are
not user secrets; they live in `config.yaml` only so an app update can be
tracked without touching code.

**Endpoints used.**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/public/sport-type` | sport keys ↔ int values (pickleball = 5) |
| GET | `/v2/user/branch/branches` | full branch list (cursor `lastFetchBranch`) |
| POST | `/v2/user/branch/branches_first` | branches near a coordinate |
| GET | `/v2/user/branch/get_branch/{id}` | branch detail (sport `type`, location) |
| GET | `/v2/user/branch/get_cores/{id}` | courts, each with a `setting` |
| GET | `/v2/user/branch/get_core_types/{id}` | price table; id matches core `setting` |
| POST | `/v2/user/branch/get_filtered_branch_booking` | social sessions for a date range |

A court's price comes from the `get_core_types` entry whose `id` equals the
core's `setting`. Hourly availability calendars (`get_booking`) require a logged-in
account and are deliberately not used.

## Limitations

- **Availability is not checked.** The bot prices courts, but does not confirm a
  specific slot is still free — the booking calendar needs a login. Confirm the
  slot in the AloBooking app before heading out.
- **Location is best-effort.** `--place` does a diacritic-insensitive text match
  on branch name + address; `--lat/--lng` uses a true distance filter. There is
  no geocoding service involved.
- Prices reflect the branch's published price table for one-time bookings.
  Member/target-group rates are not applied.

## Development

```bash
.venv/bin/pytest            # pure-logic tests, no network
docker compose up -d --build
```

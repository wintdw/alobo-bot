# AGENTS.md

Guidance for agents working in this repository.

## What this is

`alobo-bot` is a read-only CLI/service that finds the cheapest pickleball court
on AloBooking for a place and time window. It speaks directly to the AloBooking
backend API. Skeleton cloned from the voz-bot / otofun-bot / social-bot family
(cli/config/tests/Docker layout).

## Layout

```
src/alobo_bot/
  config.py      DEFAULTS deep-merged with config.yaml; ROOT via ALOBO_BOT_HOME
  crypto.py      x-user-app signature + AES body crypto (encrypt requests, decrypt responses)
  api.py         HTTP client; one method per endpoint
  models.py      dataclasses for API payloads (Branch, Core, CoreType, PriceTarget, Booking, ...)
  pricing.py     time-window price math + haversine
  availability.py  free/booked/partial verdict for a court in a window
  search.py      shortlist branches -> price courts -> rank
  report.py      text / markdown / JSON rendering + report writing
  metrics.py     durable counters behind /status (requests, clients, searches)
  state.py       atomic JSON read/write for the durable state files
  webui.py       server-rendered HTML page (pure functions, no FastAPI)
  cli.py         `find`, `sports`, `serve`
  web.py         FastAPI wiring: GET / page, POST /find, /report.json,
                 /status (+ /status.json)
tests/         pytest, pure logic only (no network)
scripts/       recover_api_keys.py — decode the web app's obfuscated build keys
docs/          reverse-engineering notes: updating-api-keys.md,
               api-response-encryption.md, court-availability.md
```

## Rules

- **Read-only.** Never add booking, payment, or any state-changing API call.
- **No browser.** The API is called with plain `urllib`; the Cloud Run origins
  in `config.yaml` bypass the Cloudflare check on the `*.alobo.vn` hosts. Do not
  add Playwright/Puppeteer. (A browser is still fine as a one-off *dev* tool —
  e.g. to capture a reply while recovering keys — just never as a runtime
  dependency.)
- **No accounts, no user secrets.** Every endpoint the bot calls is public —
  including court availability (`get_onetime_bookings`) — so it never logs in and
  there are no credentials to store. The signing key and AES constants ship inside
  the public web app; they live in `config.yaml` as trackable build constants, not
  as secrets.
- **Tests must not touch the network.** Use the `FakeClient` pattern in
  `tests/test_search.py`.
- **Only `status == 1` venues are bookable.** The branch list returns every
  venue, including ones the booking app will not sell, and their
  `get_cores`/`get_core_types` calls answer normally — so an unbookable venue
  prices up and looks free. The app's own search settles the rule: its
  `get_filtered_branch_booking` answers with only `status` `1` branches, never a
  `0`/`-1`/`-2`. `-1`/`-2` are locked/removed and `0` is a draft or paused
  listing (a placeholder address is a giveaway) that can still hold bookings the
  venue made itself. Gate the shortlist and the tickets on
  `models.Branch.is_bookable`; a missing `status` counts as bookable.
- **Price from a tariff, never from the bare type.** `get_core_types` gives each
  court type a generic `normalPrice` *and* one table per tariff under `targets`
  (the app's "đối tượng áp dụng"). The generic one is a placeholder in most
  branches, so pricing from it silently shows a flat rate; the walk-up tariff is
  `kh`, else `default`. The page always prices at that standard rate and has no
  control for it, so keep the choice to the CLI (`--target`), where it is the
  operator's own explicit pick, with the generic rate as the fallback.
- **Keep the window inside the venue's working hours.** The branch *list*
  payload carries `morningStartWorkingTime`/`afternoonEndWorkingTime`, and the
  app's grid renders an hour column from opening *through* closing — so a
  venue's last sold hour starts at the closing time (La Khê: hours 05:00-22:00,
  last slot 22:00-23:00). Clip the requested window to that before pricing and
  before reading availability (`search.bookable_window`), or the bot charges a
  base rate for hours nobody can book and reports them free. An hour a tariff
  puts no price on is not for sale either — trim it
  (`search._sellable_minutes`), since some branches' rate tables stop before
  their stated closing time.
- **Quote what is actually bookable.** A partly-free court is priced for its
  open spans, and a fully booked one is dropped: it has nothing left to sell, so
  quoting the full window would invent a price for hours somebody else holds.
- **A slot the venue locked is not for sale either.** `get_lock_yards` lists the
  stretches a branch keeps its own courts off sale for — "Khóa" in the app's
  grid. Nobody holds them, yet the app paints them grey and refuses to book them,
  so a court with no booking can still be unsellable at 22:00; many branches end
  their evening that way (125 Hoàng Ngân locks 22:00-24:00 daily) and ignoring it
  quotes hours that are not on sale. A lock's `frequency` says which kind it is:
  a weekday list (1=Mon..7=Sun) makes it a *daily* window whose `startTime`/
  `endTime` dates are only the day it was filed, while an empty list makes it a
  one-off on the date it names. Both come off the window like a booking
  (`models.LockYard`, `availability.free_spans`); see
  [docs/court-availability.md](docs/court-availability.md).
- Keep `data/` git-ignored; it holds reports and raw snapshots.
- **Durable state lives on the host.** The `/status` counters (`metrics.py`) and
  the result cache (`web.py`) are written under `state.dir` (`data/state/`) and
  read back at start, so a restart resumes instead of resetting — keep that dir on
  the same bind mount as `data/`. `/status` is the service/health view (linked
  from the footer as "Service status").

## Keeping up with the app (API keys)

The signing key, the AES keys and the Cloud Run origins are baked into each web
app release and are **XOR-obfuscated** in `main.dart.js` — the plain literals in
the bundle are dead fallbacks. They change without notice, so when every call
401s ("Vui lòng kiểm tra thời gian…") or replies stop decrypting, re-derive them:

```bash
python scripts/recover_api_keys.py --verify     # decode the bundle, pin the live keys
```

then update `config.yaml` (`api.base_url` / `api.global_url` / `api.app_key`) and
`src/alobo_bot/crypto.py` (`_AES_KEY` / `_AES_IV` / `_RESPONSE_KEY`). Full
runbook: [docs/updating-api-keys.md](docs/updating-api-keys.md).

## Adding a data source

1. Add a method to `AloboClient` (`api.py`), returning model objects.
2. Keep parsing in `models.py` (`from_api` classmethods) so it stays testable.
3. Price logic goes in `pricing.py`; availability verdicts in `availability.py`;
   ranking/orchestration in `search.py`.
4. Cover the new parsing/pricing path with a network-free test.

## Verifying

```bash
.venv/bin/pytest
alobo-bot find --place "Hà Nội" --from 18:00 --to 21:00
```

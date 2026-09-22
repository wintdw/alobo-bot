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
  config.py    DEFAULTS deep-merged with config.yaml; ROOT via ALOBO_BOT_HOME
  crypto.py    x-user-app signature + AES body encryption
  api.py       HTTP client; one method per endpoint
  models.py    dataclasses for API payloads (Branch, Core, CoreType, ...)
  pricing.py   time-window price math + haversine
  search.py    shortlist branches -> price courts -> rank
  report.py    text / markdown / JSON rendering + report writing
  webui.py     server-rendered HTML page (pure functions, no FastAPI)
  cli.py       `find`, `sports`, `serve`
  web.py       FastAPI wiring: GET / page, POST /find, /report.json, /health
tests/         pytest, pure logic only (no network)
```

## Rules

- **Read-only.** Never add booking, payment, or any state-changing API call.
- **No browser.** The API is called with plain `urllib`; the Cloud Run origins
  in `config.yaml` bypass the Cloudflare check on the `*.alobo.vn` hosts. Do not
  add Playwright/Puppeteer.
- **No user secrets in the repo or in code.** The signing key and AES constants
  ship inside the public web app; they live in `config.yaml` as trackable build
  constants, not as credentials. If a real credential ever appears, use a
  git-ignored `credentials.yaml`, never `config.yaml`.
- **Tests must not touch the network.** Use the `FakeClient` pattern in
  `tests/test_search.py`.
- Keep `data/` git-ignored; it holds reports and raw snapshots.

## Adding a data source

1. Add a method to `AloboClient` (`api.py`), returning model objects.
2. Keep parsing in `models.py` (`from_api` classmethods) so it stays testable.
3. Price logic goes in `pricing.py`; ranking/orchestration in `search.py`.
4. Cover the new parsing/pricing path with a network-free test.

## Verifying

```bash
.venv/bin/pytest
alobo-bot find --place "Hà Nội" --from 18:00 --to 21:00
```

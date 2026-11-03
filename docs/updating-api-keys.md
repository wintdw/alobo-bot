# Updating the API keys

The bot calls the same Cloud Run origins the web app uses. Each call is signed
with a per-release **app key** and its bodies are decrypted with a **response
key** / **per-response IV**. Those values ship inside each web app build and are
**XOR-obfuscated** in `main.dart.js`, so a new app release can silently break the
bot even though nothing in this repo changed.

Run this whenever calls start failing. It takes a few minutes and the recovery
is mechanical.

## Quick path (the usual case: only the signing key rotated)

```bash
cd /home/dw/alobo-bot
python scripts/recover_api_keys.py --verify
```

That downloads the current `main.dart.js`, decodes every config constant and
probes the Cloud Run origin. The live key is printed as:

```
  app_key = '137b1fccd4bc45x12af095df0bafa776' (HTTP 200 from https://…)
```

Put it in `config.yaml` (`api.app_key`) and `src/alobo_bot/config.py`
(`DEFAULTS["api"]["app_key"]`), rebuild/restart, then run `alobo-bot sports`.
If `sports` still fails:

- **401** — app key wrong; re-run `--verify`.
- **403** — `User-Agent` or origin; see `api._USER_AGENT` and section 1. If the
  helper cannot reach any decoded origin, point it at one explicitly with
  `--verify --origin https://<app-origin>`.
- **padding / non-JSON** while the body is `{"enc":true,…}` — the AES keys moved
  too; continue with steps 2–4.

A release can overlap with the previous one (both keys answer `200` for a
while), so `--verify` picks the *new* build's key — use it rather than assuming
the old one is still permanent.

## 1. Confirm the symptom

| What you see | What changed |
|---|---|
| Every call: HTTP `401` `Vui lòng kiểm tra thời gian trên thiết bị của bạn` | signing **app key** (`x-user-app`) |
| Every call: HTTP `403` `{"statusCode":403,"message":"Forbidden"}` | missing/non-browser **`User-Agent`** (the Cloud Run origins now filter those too), or a retired origin |
| `ApiError: API returned non-JSON payload`, or PKCS#7 padding errors, while the body is `{"enc":true,"data":…,"iv":…}` | **AES** keys (request and/or response) |
| Envelopes from a *new* host name (e.g. `…-<newid>-uc.a.run.app`) | the Cloud Run **origins** |

If the old origins still answer but the web app's do not, the app moved to a new
deployment; the decoded origins below replace `config.yaml`.

## 2. Capture one live envelope (for the response-key check)

This is the ground truth used to disambiguate keys in step 4. Use a real browser
— the public `*.alobo.vn` hosts sit behind a Cloudflare check that rejects plain
HTTP clients:

```bash
agent-browser open https://datlich.alobo.vn
agent-browser network requests --filter "alobo.vn/v2"
agent-browser network request <request-id> --json   # postData / responseBody
```

Any encrypted reply works; keep it small. Record its `iv` and `data`.

## 3. Get the bundle and decode it

`datlich.alobo.vn` 403s the default urllib/curl user agent, so the helper sends a
browser UA itself:

```bash
python scripts/recover_api_keys.py                  # downloads main.dart.js and decodes
python scripts/recover_api_keys.py --bundle main.dart.js
python scripts/recover_api_keys.py --verify         # also probes for the live signing key
python scripts/recover_api_keys.py --verify --origin https://<app-origin>  # probe one origin
python scripts/recover_api_keys.py --match-header <x-user-app> <epoch_ms>  # from a capture
```

It prints every XOR-obfuscated config string, e.g. (live build, 2026-11-03):

```
eUu   URL                              https://user-api-new.alobo.vn
eUz   URL                              https://user-sv-api.alobo.vn
eUo   32-char (AES or signing key)     1357924680_0123456789_0123987456
eUn   base64 16-byte (IV)              AjIrEp582ksFPaFKw4xwuw==
eUm   32-char (AES or signing key)     Al0b0@Doczy2026_8768_Secret_0810
eUC   32-char (AES or signing key)     137b1fccd4bc45x12af095df0bafa776
eU7   URL                              https://user-app-new-ootprnz4oa-uc.a.run.app
```

The bundle now carries several complete config sets (dev/staging and
`cLz`/`cLA` for mobile/web production), so the same value appears under more
than one name. Production web is the `cLA` set; `--verify` settles the signing
key in one probe.

**Do not trust the decoded variable names — the minifier reshuffles them on every
build** (`eF…` one release, `eU…` the next). Match by value/shape, never by name.
Nor should you trust the plain literals elsewhere in the bundle (the hashing
helpers return dead fallbacks); only the decoded values are live.

## 4. Decide which value is which

| Role | Shape | How to confirm |
|---|---|---|
| `api.global_url` | URL containing `user-global` | send a signed `GET /v2/user/branch/branches` → 200 |
| `api.base_url` | URL containing `user-app-new` | send a signed `GET /api/v1/public/sport-type` → 200 |
| `api.app_key` | 32-char string (not always hex — the 2026-11 build contains an `x`) | `--verify` (or `--match-header`) prints the one that returns **200**; the other candidates 401 |
| `crypto._RESPONSE_KEY` | any 32-byte string | decrypt the captured envelope's `data` with it and the envelope's `iv` → valid JSON |
| `crypto._AES_IV` | base64 of 16 bytes (`…==`) | — the IV that pairs with the request key |
| `crypto._AES_KEY` | 32-byte string | base64-encrypt a `get_filtered_branch_booking` body with it and the IV → 200 |

There are usually several 32-byte candidates; only one decrypts the captured
response, which is why step 2 exists. One-liner for the response key:

```python
import base64, json
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

env = {"data": "...", "iv": "..."}          # from step 2
for key in RESPONSE_KEY_CANDIDATES:
    try:
        d = Cipher(algorithms.AES(key.encode()), modes.CBC(base64.b64decode(env["iv"]))).decryptor()
        raw = d.update(base64.b64decode(env["data"])) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        print(key, json.loads((u.update(raw) + u.finalize()).decode()))
    except Exception:
        pass                                 # wrong key -> PKCS#7 error
```

## 5. Apply

Two files. Nothing else in the codebase hard-codes these values.

- `config.yaml` → `api.base_url`, `api.global_url`, `api.app_key`
- `src/alobo_bot/crypto.py` → `_AES_KEY`, `_AES_IV`, `_RESPONSE_KEY`

## 6. Apply and verify

```bash
python -m pytest -q                     # pure logic, no network
alobo-bot sports                        # must list sports, not 401/403
alobo-bot find --place "Hà Nội" --limit 2
```

This deployment bakes `config.yaml` into the image, so restart it after editing:

```bash
sudo docker compose up -d --build
curl -s http://localhost:8085/status.json | head -c 200
```

Then throw a fresh captured envelope into
`tests/test_crypto.py::test_decrypt_body_opens_a_live_response_envelope` (replace
the `iv`/`data`/expected), so the response key is pinned against a real reply.

## Why this exists

The keys are not credentials — they are build constants that ship in the public
web app. They are reproduced here only so the bot can talk to the same API the
site does, and they change with every app release. See
[api-response-encryption.md](./api-response-encryption.md) for how the scheme
was reverse-engineered.

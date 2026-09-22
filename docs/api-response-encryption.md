# Encrypted API responses — findings

Reverse-engineered and **verified end-to-end**, 2026-09-22, against the live
`datlich.alobo.vn` build. Supersedes the earlier hand-transcribed guess (see
*History*). To re-derive these after an app release, follow
[updating-api-keys.md](./updating-api-keys.md).

## What the app does

`datlich.alobo.vn` is a Flutter web app; every API call it makes is signed and
both directions are AES-encrypted:

| | Request body | Response body |
|---|---|---|
| Shape | `{"enc":true,"data":…}` | `{"enc":true,"data":…,"iv":…}` |
| Cipher | AES-256-CBC, PKCS#7 | AES-256-CBC, PKCS#7 |
| Key | `_AES_KEY` (below) | `_RESPONSE_KEY` (below) |
| IV | `_AES_IV`, a fixed constant | base64-decode the envelope's own `iv` |

The response envelope also carries cleartext siblings — `statusCode`, `message`,
`_metadata` (`path`, `timestamp`, `timezone`, `repoVersion`) — around the
encrypted `data`/`iv`. Only `data` is encrypted.

The signing header is unchanged:

```
x-user-app = sha256_hex("<MM/dd/yyyy, HH:mm>@<app_key>")   # UTC clock
```

## Live values (build 2026-09-22)

| Where | Value |
|---|---|
| `api.app_key` | `935b1fccd4bc45a12af095bf0bafa723` |
| `crypto._AES_KEY` | `1357924680_0123456789_0123987456` |
| `crypto._AES_IV` | `base64("AjIrEp582ksFPaFKw4xwuw==")` |
| `crypto._RESPONSE_KEY` | `Al0b0@Doczy2026_1123_Secret_0804` |
| `api.base_url` | `https://user-app-new-ootprnz4oa-uc.a.run.app` |
| `api.global_url` | `https://user-global-ootprnz4oa-uc.a.run.app` |

## How the constants were recovered

The bundle exposes the config object as `$.kH()` (a `c_l`/`cD6` instance); the
AES values are `gP0()` / `gJm()` / `gJl()`. **The string literals those getters
contain are dead fallbacks** — the live build overrides them with values that are
XOR-obfuscated in the bundle:

```js
// per-value lazy initializer
s($,"fxh","eF9",()=>{var o=J.d6(32,p); for(q=0;q<32;++q)o[q]=q;
  return A.cf(B.b.br(o,new A.dIy(),p),0,null)})       // dIy: B.bT1[a] ^ B.bT2[a]
```

Each value is the XOR of two `Uint32List` tables, materialised one char per
index. Decode every such pair and you get the whole config —
`scripts/recover_api_keys.py` does exactly this:

```bash
python scripts/recover_api_keys.py            # decode all obfuscated constants
python scripts/recover_api_keys.py --verify   # probe each candidate signing key
```

The minifier reassigns the `eF*` names on each build, so identify values by
shape, not name. In the 2026-09-22 build the relevant ones were `eF2`, `eFk`,
`eFh`, `eFi`, `eFj`, `eFx`.

## Verification

- A captured envelope (`iv` + `data`) decrypts to valid JSON with
  `_RESPONSE_KEY` and the envelope `iv`; with any other key it fails PKCS#7.
- A `get_filtered_branch_booking` body encrypted with `_AES_KEY`/`_AES_IV` is
  accepted (`200`).
- `alobo-bot sports` and `alobo-bot find` return real data through the live
  origins.
- `tests/test_crypto.py::test_decrypt_body_opens_a_live_response_envelope` pins
  a real captured reply.

## History

An earlier note claimed the response key was `0123456789_0123456789_9876542026`
with a fixed IV and marked it unconfirmed. That literal is a dead fallback —
decryption with it always fails PKCS#7. The real key is the XOR-decoded value
above. (The old request key `0123456789_0123456789_0123456789` was also a dead
fallback; the live request key is `1357924680_0123456789_0123987456`.)

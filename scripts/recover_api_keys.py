#!/usr/bin/env python3
"""Recover the live AloBooking API keys from the web app bundle.

The Flutter app (https://datlich.alobo.vn/main.dart.js) hard-codes its build
config, but the live copies are XOR-obfuscated: each value is the XOR of two
``Uint32List`` tables, materialised by a ``$,"fx…","eF…"`` lazy initializer and
exposed at runtime as ``$.kH().gP0()/gJm()/gJl()/gXX()``. The plain string
literals elsewhere in the bundle are dead fallbacks and must not be trusted.

This script downloads the bundle, decodes every ``eF*``/``eE*`` config string
and prints them, so the values can be pasted into ``config.yaml`` and
``src/alobo_bot/crypto.py``. It also decodes a captured ``x-user-app`` header to
pin down which decoded value is the *signing* key.

Usage::

    python scripts/recover_api_keys.py                      # download + decode
    python scripts/recover_api_keys.py --bundle p.js        # decode a local copy
    python scripts/recover_api_keys.py --match-header <x-user-app> <epoch_ms>

Which decoded value goes where (live build 2026-09-22):

    eEU  32-char string  -> crypto._RESPONSE_KEY   (response AES key)
    eEV  base64 …==      -> crypto._AES_IV         (request IV, base64)
    eEW  32-char string  -> crypto._AES_KEY        (request AES key)
    eEX  https://…       -> config.yaml api.global_url
    eF2  https://…       -> config.yaml api.base_url
    (signing key)        -> config.yaml api.app_key  (confirmed via --match-header)

See docs/updating-api-keys.md for the full runbook.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
import urllib.request

BUNDLE_URL = "https://datlich.alobo.vn/main.dart.js"

# Cloudflare rejects the default urllib UA on this host with a 403.
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"

# Signing keys tried when nothing is captured; the live one is a 32-char hex.
LITERAL_KEYS = ("Alobo-User-Key-2026",)

_TABLE_RE = re.compile(r"B\.(\w+)=s\(\[([0-9,]+)\]")
_CLASS_RE = re.compile(
    r"A\.(dI\w+)\.prototype=\{\s*\$1\(a\)\{return\(B\.(\w+)\[a\]\^B\.(\w+)\[a\]\)"
)
_NAME_RE = re.compile(r'"(e[eF]\w*)",\(\)=>\{[^}]*?new A\.(dI\w+)\(\)')


def fetch_bundle(path: str | None) -> str:
    if path:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    request = urllib.request.Request(BUNDLE_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def decode_config(source: str) -> dict[str, str]:
    """Return {config-name: decoded string} for every XOR-obfuscated constant."""
    tables = {
        m.group(1): [int(x) for x in m.group(2).split(",") if x]
        for m in _TABLE_RE.finditer(source)
    }
    classes = {
        m.group(1): (m.group(2), m.group(3)) for m in _CLASS_RE.finditer(source)
    }
    out: dict[str, str] = {}
    for m in _NAME_RE.finditer(source):
        name, cls = m.group(1), m.group(2)
        t1, t2 = classes.get(cls, (None, None))
        if t1 is None or t1 not in tables or t2 not in tables:
            continue
        a, b = tables[t1], tables[t2]
        chars = []
        for i in range(min(len(a), len(b))):
            value = (a[i] ^ b[i]) & 0xFFFFFFFF
            chars.append(chr(value) if 0 < value < 0x110000 else "\ufffd")
        out[name] = "".join(chars)
    return out


def describe(value: str) -> str:
    if value.startswith("http"):
        return "URL"
    if re.fullmatch(r"[0-9a-f]{32}", value):
        return "32-hex (candidate signing key)"
    if value.endswith("==") and len(value) == 24:
        return "base64 16-byte (IV)"
    if len(value) == 32:
        return "32-byte (AES key)"
    return ""


def match_header(header: str, epoch_ms: int, decoded: dict[str, str]) -> list[str]:
    """Find which candidate key produces *header* for the given timestamp."""
    when = dt.datetime.fromtimestamp(epoch_ms / 1000, dt.timezone.utc)
    candidates = dict.fromkeys(list(decoded.values()) + list(LITERAL_KEYS))
    hits = []
    for key in candidates:
        for hours in (0, 7, -7):  # devices vary; the server wants its own zone
            stamp = (when + dt.timedelta(hours=hours)).strftime("%m/%d/%Y, %H:%M")
            if hashlib.sha256(f"{stamp}@{key}".encode()).hexdigest() == header:
                hits.append(f"{key!r} (utc_offset={hours:+d}h, stamp={stamp!r})")
    return hits


def _sign(key: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%m/%d/%Y, %H:%M")
    return hashlib.sha256(f"{stamp}@{key}".encode()).hexdigest()


def verify(decoded: dict[str, str]) -> str | None:
    """Probe the decoded origins with each candidate signing key; return the live one."""
    origin = next(
        (v for v in decoded.values() if v.startswith("http") and "user-app-new" in v),
        None,
    )
    if not origin:
        print("  (no user-app-new origin decoded — cannot verify)", file=sys.stderr)
        return None
    candidates = dict.fromkeys(
        [v for v in decoded.values() if re.fullmatch(r"[0-9a-f]{32}", v)] + list(LITERAL_KEYS)
    )
    for key in candidates:
        request = urllib.request.Request(
            f"{origin}/api/v1/public/sport-type",
            headers={
                "User-Agent": _UA,
                "x-user-app": _sign(key),
                "x-name-app": "alobo-user",
                "x-platform": "web",
                "x-version-app": "2.10.3",
                "x-custom-lang": "vi",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:  # noqa: S310
                if resp.status == 200:
                    print(f"  app_key = {key!r} (HTTP 200 from {origin})")
                    return key
        except Exception as exc:  # noqa: BLE001 - report and keep probing
            print(f"  {key}: {exc}")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", help="local main.dart.js (default: download)")
    parser.add_argument(
        "--match-header",
        nargs=2,
        metavar=("X_USER_APP", "EPOCH_MS"),
        help="identify the signing key from a captured header + its timestamp",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="probe the decoded origin with each candidate key and report the live one",
    )
    args = parser.parse_args(argv)

    decoded = decode_config(fetch_bundle(args.bundle))
    if not decoded:
        print("no obfuscated config constants found — did the build change?", file=sys.stderr)
        return 1

    print(f"decoded {len(decoded)} obfuscated config strings:\n")
    for name, value in sorted(decoded.items()):
        print(f"  {name:5} {describe(value):32} {value}")

    if args.match_header:
        header, epoch_ms = args.match_header
        print("\nsigning key match:")
        hits = match_header(header, int(epoch_ms), decoded)
        print("\n".join(f"  {h}" for h in hits) or "  (none — key may be new)")

    if args.verify:
        print("\nverifying signing keys against the decoded origin:")
        verify(decoded)

    print(
        "\nnext: put the response key / request key + IV in src/alobo_bot/crypto.py,\n"
        "      the origins + signing key in config.yaml, then run\n"
        "      `alobo-bot sports` to confirm the live API accepts them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

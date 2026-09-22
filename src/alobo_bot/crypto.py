"""Request signing, body encryption and response decryption for the AloBooking API.

The web app (datlich.alobo.vn, a Flutter build) authenticates every API call
with two mechanisms that this module reproduces byte-for-byte:

1. The ``x-user-app`` header is
   ``sha256_hex("<MM/dd/yyyy, HH:mm>@<app_key>")`` using the **UTC** clock. The
   server validates it against its own clock and allows only a couple of
   minutes of skew ("Vui lòng kiểm tra thời gian trên thiết bị của bạn" — check
   your device clock), so the header is regenerated per request and must never
   be derived from the host's local time — a venue-local ``TZ`` (Asia/Ho_Chi_Minh)
   puts it 7 hours off and every call comes back 401.

2. POST bodies carry an AES-256-CBC (PKCS#7) encrypted, base64 payload wrapped
   as ``{"enc": true, "data": "..."}``. The key and IV are fixed constants in
   the app bundle.

Every response comes back encrypted the same way, but with a different key and
a **per-response** IV, so it is wrapped as
``{"enc": true, "data": "...", "iv": "..."}`` — the IV is base64-decoded from
the envelope itself (see :func:`decrypt_body`). Responses that predate this, or
plaintext endpoints, carry no ``iv``; those fall back to the request key/IV.

None of these values is a user secret — all of them ship inside the public web
app. They live in config.yaml (``api.app_key``) only so a future app release
can be tracked without touching code. The app XOR-obfuscates the live copies of
the AES constants in the bundle (behind ``gP0()``/``gJm()``/``gJl()``, whose
plain literals are dead fallbacks); the values below are the decoded live ones
(see docs/api-response-encryption.md).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import padding as _padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Constants decoded from the live app bundle (see README "How the API is called").
_AES_KEY = b"1357924680_0123456789_0123987456"          # utf8($.kH().gJm()) — request key
_AES_IV = base64.b64decode("AjIrEp582ksFPaFKw4xwuw==")  # base64(.gJl()) — request IV
_RESPONSE_KEY = b"Al0b0@Doczy2026_1123_Secret_0804"     # utf8($.kH().gP0()) — response key

# The app formats the timestamp with a US pattern regardless of locale.
_TIMESTAMP_FORMAT = "%m/%d/%Y, %H:%M"


def signature(app_key: str, when: dt.datetime | None = None) -> str:
    """Return the ``x-user-app`` header value for *app_key* at time *when*.

    Defaults to the current UTC time: the server checks the stamp against its
    own clock, so using the host's local time (e.g. a venue-local ``TZ``) is
    rejected.
    """
    when = when or dt.datetime.now(dt.timezone.utc)
    stamp = when.strftime(_TIMESTAMP_FORMAT)
    return hashlib.sha256(f"{stamp}@{app_key}".encode("utf-8")).hexdigest()


def encrypt_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Encrypt *payload* into the ``{"enc": true, "data": "<base64>"}`` envelope."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    padder = _padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return {"enc": True, "data": base64.b64encode(ciphertext).decode("ascii")}


def decrypt_body(envelope: dict[str, Any]) -> dict[str, Any]:
    """Open an API response envelope.

    Encrypted replies carry their own base64 ``iv`` and are decrypted with the
    response key; envelopes without one (older or plaintext endpoints, and the
    request bodies :func:`encrypt_body` builds) fall back to the request
    key/IV, which makes this the exact inverse of :func:`encrypt_body`.
    """
    if not isinstance(envelope, dict) or "data" not in envelope:
        raise ValueError("not an encrypted envelope")
    ciphertext = base64.b64decode(envelope["data"])
    iv_field = envelope.get("iv")
    key = _RESPONSE_KEY if iv_field else _AES_KEY
    iv = base64.b64decode(iv_field) if iv_field else _AES_IV
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = _padding.PKCS7(128).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    return json.loads(raw.decode("utf-8"))

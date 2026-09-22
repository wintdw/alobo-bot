"""Request signing and body encryption for the AloBooking API.

The web app (datlich.alobo.vn, a Flutter build) authenticates every API call
with two mechanisms that this module reproduces byte-for-byte:

1. The ``x-user-app`` header is
   ``sha256_hex("<MM/dd/yyyy, HH:mm>@<app_key>")`` using the caller's local
   clock. The server rejects stale timestamps ("Vui lòng kiểm tra thời gian
   trên thiết bị của bạn" — check your device clock), so the header is
   regenerated per request.

2. POST bodies carry an AES-256-CBC (PKCS#7) encrypted, base64 payload wrapped
   as ``{"enc": true, "data": "..."}``. The key and IV are fixed constants in
   the app bundle.

Neither value is a user secret — both ship inside the public web app. They live
in config.yaml (``api.app_key``) only so a future app release can be tracked
without touching code.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import padding as _padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Constants lifted from main.dart.js (see README "How the API is called").
_AES_KEY = b"0123456789_0123456789_0123456789"        # utf8(gJm()) — 32 bytes
_AES_IV = base64.b64decode("bmjSyRV4MLcxfvEWGJdqXQ==")  # base64(gJl()) — 16 bytes

# The app formats the timestamp with a US pattern regardless of locale.
_TIMESTAMP_FORMAT = "%m/%d/%Y, %H:%M"


def signature(app_key: str, when: dt.datetime | None = None) -> str:
    """Return the ``x-user-app`` header value for *app_key* at time *when*."""
    when = when or dt.datetime.now()
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
    """Inverse of :func:`encrypt_body` (used by tests and any encrypted replies)."""
    if not isinstance(envelope, dict) or "data" not in envelope:
        raise ValueError("not an encrypted envelope")
    ciphertext = base64.b64decode(envelope["data"])
    decryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = _padding.PKCS7(128).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    return json.loads(raw.decode("utf-8"))

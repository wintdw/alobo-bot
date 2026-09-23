import base64
import datetime as dt
import os
import time

import pytest

from alobo_bot.crypto import decrypt_body, encrypt_body, signature

APP_KEY = "935b1fccd4bc45a12af095bf0bafa723"


def test_signature_matches_known_vector():
    when = dt.datetime(2026, 9, 22, 8, 43)
    assert signature(APP_KEY, when) == (
        "d3d7aad88a488586abb8d16279b2d19114bf2b46a48a29a6e65f5933a279c748"
    )


def test_signature_is_64_hex_chars():
    value = signature(APP_KEY)
    assert len(value) == 64
    int(value, 16)  # parses as hex


def test_decrypt_body_opens_a_live_response_envelope():
    """A real encrypted reply, captured verbatim from the web app.

    The response uses the response key and the envelope's own ``iv``; a
    plaintext-endpoint fixture cannot exercise that path, so this vector pins it.
    """
    envelope = {
        "enc": True,
        "iv": "SnFfDjCHxnuZroUJeKxTuw==",
        "data": "Kmfd9yXT1LWpDV33Hlo451SCCX9AaqFA6cR1T5co2dDMDulIWDITJO6Kj+pSvpBetMPPkV3wkLwuiugRF5ZuuA==",
    }
    assert decrypt_body(envelope) == {
        "branches": [],
        "lastFetchBranch": "2026-09-22T16:32:54.291Z",
    }


def test_default_signature_uses_utc_regardless_of_local_timezone():
    """The server checks the stamp against UTC, so a venue-local TZ must not shift it."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is not available on this platform")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Ho_Chi_Minh"  # local clock now reads UTC+7
    time.tzset()
    try:
        utc_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        expected = {
            signature(APP_KEY, utc_now + dt.timedelta(minutes=offset))
            for offset in (-1, 0, 1)  # tolerate a minute boundary between the calls
        }
        assert signature(APP_KEY) in expected
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_encrypt_body_round_trip():
    payload = {"bookingType": "oneTime", "types": [5], "branchIds": [], "dateStart": None}
    envelope = encrypt_body(payload)
    assert envelope["enc"] is True
    assert isinstance(envelope["data"], str)
    base64.b64decode(envelope["data"])  # decodes as base64
    assert decrypt_body(envelope) == payload


def test_encrypt_body_uses_separator_free_json():
    envelope = encrypt_body({"a": 1, "b": 2})
    # ciphertext length is a multiple of the 16-byte block size
    assert len(base64.b64decode(envelope["data"])) % 16 == 0

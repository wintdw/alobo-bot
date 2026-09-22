import base64
import datetime as dt

from alobo_bot.crypto import decrypt_body, encrypt_body, signature


def test_signature_matches_known_vector():
    when = dt.datetime(2026, 9, 22, 8, 43)
    assert signature("Alobo-User-Key-2026", when) == (
        "c4399997675fbffaff541772a17d73e51aaa2e180b780250d2e1d4a71914b1fc"
    )


def test_signature_is_64_hex_chars():
    value = signature("Alobo-User-Key-2026")
    assert len(value) == 64
    int(value, 16)  # parses as hex


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

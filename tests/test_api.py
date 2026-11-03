"""Request headers: the Cloud Run origins only answer browser-looking clients."""

from alobo_bot.api import AloboClient
from alobo_bot.config import DEFAULTS


def test_headers_look_like_the_web_app():
    client = AloboClient({"api": dict(DEFAULTS["api"])})
    headers = client._headers()

    # A missing or non-browser UA is answered with `403 Forbidden` even when
    # the signature is valid, so every request must carry a browser UA.
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["x-name-app"] == "alobo-user"
    assert headers["x-platform"] == "web"
    assert len(headers["x-user-app"]) == 64

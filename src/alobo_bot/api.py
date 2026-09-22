"""Thin HTTP client for the AloBooking public API.

All calls go to the Cloud Run origins of the two AloBooking backends (see
``config.yaml``): the ``*.alobo.vn`` hosts sit behind a Cloudflare bot check
that rejects non-browser clients (error 1010), while the Cloud Run origins serve
the identical API. Every request carries the app headers and a fresh
``x-user-app`` signature; POST bodies are AES-encrypted (see :mod:`crypto`).
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__
from .crypto import decrypt_body, encrypt_body, signature
from .models import Branch, Core, CoreType, SocialSession, SportType, _parse_dt  # noqa: F401


class ApiError(RuntimeError):
    """The API refused the request or returned an unexpected payload."""


class AloboClient:
    """Stateless-ish client; holds config and a single urllib opener."""

    def __init__(self, cfg: dict):
        api = cfg["api"]
        self.base_url = api["base_url"].rstrip("/")
        self.global_url = api["global_url"].rstrip("/")
        self.app_name = api["app_name"]
        self.platform = api["platform"]
        self.version = api["version"]
        self.lang = api["lang"]
        self.app_key = api["app_key"]
        self.timeout = float(api["timeout_seconds"])
        self.delay_min = float(api["delay_min_seconds"])
        self.delay_max = float(api["delay_max_seconds"])
        self.max_retries = int(api["max_retries"])
        self._opener = urllib.request.build_opener()

    # ------------------------------------------------------------------ core

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi,en;q=0.9",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "x-name-app": self.app_name,
            "x-platform": self.platform,
            "x-version-app": self.version,
            "x-custom-lang": self.lang,
            "x-user-app": signature(self.app_key),
            "Origin": "https://datlich.alobo.vn",
            "Referer": "https://datlich.alobo.vn/",
        }

    def _sleep(self) -> None:
        if self.delay_max > 0:
            time.sleep(random.uniform(self.delay_min, self.delay_max))

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        encrypt: bool = False,
    ) -> Any:
        """Perform one API call and return the decoded JSON."""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        data = None
        if body is not None:
            payload = encrypt_body(body) if encrypt else body
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(url, data=data, method=method)
            for key, value in self._headers().items():
                request.add_header(key, value)
            if data is not None:
                request.add_header("Content-Length", str(len(data)))
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                return self._decode(raw)
            except urllib.error.HTTPError as exc:  # noqa: PERF203 - retry loop
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                # A stale clock is the one 401 the caller can fix.
                if exc.code == 401 and "thời gian" in detail:
                    raise ApiError(
                        "AloBooking rejected the request timestamp — check the "
                        "system clock is correct and in a Vietnamese timezone."
                    ) from exc
                if exc.code in (403, 404, 400):
                    raise ApiError(f"HTTP {exc.code} for {url}: {detail}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 10) * 0.5)
        raise ApiError(f"{method} {url} failed after {self.max_retries} attempts: {last_error}")

    @staticmethod
    def _decode(raw: bytes) -> Any:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"API returned non-JSON payload: {raw[:200]!r}") from exc
        if isinstance(payload, dict) and payload.get("enc") and "data" in payload:
            return decrypt_body(payload)
        return payload

    @staticmethod
    def _data(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    # ------------------------------------------------------------- endpoints

    def sport_types(self) -> list[SportType]:
        payload = self.request(f"{self.base_url}/api/v1/public/sport-type")
        out: list[SportType] = []
        for item in self._data(payload) or []:
            value = item.get("intValue")
            out.append(
                SportType(
                    key=item.get("key") or "",
                    name=item.get("name") or "",
                    int_value=int(value) if value is not None else -1,
                    priority=int(item.get("priority") or 0),
                )
            )
        return out

    def branches_near(self, latitude: float, longitude: float) -> list[Branch]:
        """Branches around a coordinate (the onboarding 'near me' list)."""
        payload = self.request(
            f"{self.global_url}/v2/user/branch/branches_first",
            method="POST",
            body={"latitude": latitude, "longitude": longitude},
        )
        return self._branches(payload)

    def branches_page(self, last_fetch_branch: str | None = None) -> tuple[list[Branch], str | None]:
        """One page of the full branch list plus the cursor for the next page."""
        payload = self.request(
            f"{self.global_url}/v2/user/branch/branches",
            params={"lastFetchBranch": last_fetch_branch},
        )
        data = self._data(payload) or {}
        branches = data.get("branches") if isinstance(data, dict) else data
        cursor = data.get("lastFetchBranch") if isinstance(data, dict) else None
        return self._branches(branches), cursor

    def all_branches(self, page_size: int = 100, max_pages: int = 20) -> list[Branch]:
        """Walk the branch list pages until the cursor stops advancing."""
        seen: dict[str, Branch] = {}
        cursor: str | None = None
        for _ in range(max_pages):
            page, cursor = self.branches_page(cursor)
            if not page:
                break
            for branch in page:
                seen.setdefault(branch.id, branch)
            if not cursor or cursor == "0" or len(page) < page_size:
                break
            self._sleep()
        return list(seen.values())

    def get_branch(self, branch_id: str) -> Branch:
        payload = self.request(f"{self.global_url}/v2/user/branch/get_branch/{branch_id}")
        return Branch.from_api(self._data(payload) or {})

    def get_cores(self, branch_id: str) -> tuple[list[Core], list[dict]]:
        payload = self.request(f"{self.global_url}/v2/user/branch/get_cores/{branch_id}")
        data = self._data(payload) or {}
        if not isinstance(data, dict):
            return [], []
        return [Core.from_api(c) for c in (data.get("cores") or [])], list(data.get("areas") or [])

    def get_core_types(self, branch_id: str) -> list[CoreType]:
        payload = self.request(f"{self.global_url}/v2/user/branch/get_core_types/{branch_id}")
        data = self._data(payload)
        return [CoreType.from_api(item) for item in (data or [])]

    def get_lock_yards(self, branch_id: str) -> list[dict]:
        payload = self.request(f"{self.global_url}/v2/user/branch/get_lock_yards/{branch_id}")
        data = self._data(payload)
        return list(data or [])

    def branch_booking_search(
        self,
        date_start: str,
        date_end: str,
        *,
        booking_type: str = "oneTime",
        types: list[int] | None = None,
        branch_ids: list[str] | None = None,
        province_code: str | None = None,
        ward_code: str | None = None,
    ) -> list[tuple[Branch, list[SocialSession]]]:
        """Branches that sell social/open-play sessions in a date range.

        The endpoint returns the same branch set regardless of ``types`` (the
        app filters client-side), so callers should still filter by sport.
        """
        body = {
            "provinceCode": province_code,
            "wardCode": ward_code,
            "bookingType": booking_type,
            "types": types or [],
            "branchIds": branch_ids or [],
            "dateStart": date_start,
            "dateEnd": date_end,
        }
        payload = self.request(
            f"{self.global_url}/v2/user/branch/get_filtered_branch_booking",
            method="POST",
            body=body,
            encrypt=True,
        )
        out: list[tuple[Branch, list[SocialSession]]] = []
        for item in self._data(payload) or []:
            branch = Branch.from_api(item.get("branchInformation") or {})
            sessions = [SocialSession.from_api(b) for b in (item.get("bookings") or [])]
            out.append((branch, sessions))
        return out

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _branches(items: Any) -> list[Branch]:
        if not isinstance(items, list):
            return []
        return [Branch.from_api(item) for item in items]


def user_agent() -> str:
    return f"alobo-bot/{__version__} (+https://datlich.alobo.vn)"

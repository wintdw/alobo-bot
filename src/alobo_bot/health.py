"""Health checks behind ``GET /health``.

The report answers one question: can this process still serve a fresh search?
That needs a writable data directory, readable durable state and a reachable
AloBooking API, so each is checked separately and summarised as ``ok``,
``degraded`` or ``down``. A ``down`` check means a search cannot work right now
(the API is unreachable, or the reports/raw directories are not writable — the
failure that otherwise only shows up as a 500 on the next search).

The upstream probe is two of the cheapest reads that still exercise the whole
path (signature, origins, JSON decoding): the sport list on the app origin and
the "near me" branch list on the global origin. It is reused for a short window
(a minute by default) so frequent polling costs the API little. Nothing here
depends on FastAPI, so the checks run in a plain unit test against a fake client.
"""

from __future__ import annotations

import concurrent.futures
import copy
import datetime as dt
import os
import pathlib
import shutil
import threading
import time

from . import __version__
from .state import read_json

OK = "ok"
DEGRADED = "degraded"
DOWN = "down"

# Where the "near me" probe looks when config has no saved places: Hà Nội's centre.
DEFAULT_LATITUDE = 21.0285
DEFAULT_LONGITUDE = 105.8542

STATE_FILES = ("cache.json", "metrics.json")


# ------------------------------------------------------------------ storage

def writable_dir(path: str | pathlib.Path) -> tuple[bool, str | None]:
    """Whether *path* can be created and written, plus the reason when it cannot.

    Walks up to the nearest existing ancestor, so a directory that does not exist
    yet (a first run, a fresh mount) is judged by its parent's permissions rather
    than reported missing. A read-only mount or a root-owned directory is exactly
    what makes the report/raw writes fail at search time.
    """
    probe = pathlib.Path(path)
    while True:
        if probe.exists():
            if not probe.is_dir():
                return False, f"{probe} is not a directory"
            if not os.access(probe, os.W_OK | os.X_OK):
                return False, f"{probe} is not writable by uid {os.getuid()}"
            return True, None
        parent = probe.parent
        if parent == probe:
            return False, f"{path} has no existing parent directory"
        probe = parent


def check_storage(dirs: dict[str, str | pathlib.Path]) -> dict:
    """Check that every data directory the service writes to is writable."""
    checks = []
    for name, raw_path in dirs.items():
        path = pathlib.Path(raw_path)
        writable, error = writable_dir(path)
        checks.append({"name": name, "path": str(path), "ok": writable, "error": error})
    return {"status": OK if all(check["ok"] for check in checks) else DOWN, "dirs": checks}


def _existing_ancestor(path: str | pathlib.Path) -> pathlib.Path:
    """The nearest directory at or above *path* that exists (for a disk probe)."""
    probe = pathlib.Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def check_disk(path: str | pathlib.Path, *, min_free_bytes: int) -> dict:
    """Report free space on the filesystem holding *path*; degraded when it runs low."""
    anchor = _existing_ancestor(path)
    try:
        usage = shutil.disk_usage(anchor)
    except OSError as exc:
        return {"status": DEGRADED, "path": str(anchor), "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": OK if usage.free >= min_free_bytes else DEGRADED,
        "path": str(anchor),
        "freeBytes": usage.free,
        "totalBytes": usage.total,
        "freePercent": round(usage.free / usage.total, 4) if usage.total else 0.0,
        "minFreeBytes": min_free_bytes,
    }


# -------------------------------------------------------------------- state

def check_state(state_dir: str | pathlib.Path, files: tuple[str, ...] = STATE_FILES) -> dict:
    """Read the durable state files; a missing one is normal, a corrupt one is not.

    The service treats a bad file as empty and starts fresh (see :mod:`state`), so
    this is a degraded state rather than a down one — but a corrupt metrics or
    cache file is worth surfacing before the counters silently reset.
    """
    directory = pathlib.Path(state_dir)
    checks = []
    for name in files:
        path = directory / name
        if not path.exists():
            checks.append({"name": name, "path": str(path), "ok": True, "state": "absent"})
            continue
        readable = read_json(path) is not None
        checks.append({
            "name": name,
            "path": str(path),
            "ok": readable,
            "state": "ok" if readable else "corrupt",
            "error": None if readable else "not readable JSON",
        })
    return {
        "status": OK if all(check["ok"] for check in checks) else DEGRADED,
        "files": checks,
    }


# ----------------------------------------------------------------- upstream

def _probe(name: str, call) -> dict:
    """Run one upstream call, timing it and turning any failure into a result."""
    started = time.perf_counter()
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        return {
            "name": name,
            "status": DOWN,
            "latencyMs": round((time.perf_counter() - started) * 1000, 1),
            "items": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    count = len(value) if isinstance(value, (list, tuple)) else None
    empty = count == 0
    return {
        "name": name,
        "status": DEGRADED if empty else OK,
        "latencyMs": round((time.perf_counter() - started) * 1000, 1),
        "items": count,
        "error": "empty result" if empty else None,
    }


def check_upstream(client, latitude: float, longitude: float) -> dict:
    """Probe both API origins through *client* and judge whether searches can run.

    The sport list is the app origin (it feeds the page's dropdown, with a
    fallback when it fails), while the branch list is the global origin and the
    one every search needs — so only its failure is ``down``; the other is
    ``degraded``. The two calls run in parallel to keep the probe under a second.
    """
    probes = (
        ("sport-types", client.sport_types),
        ("branches-near", lambda: client.branches_near(latitude, longitude)),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as pool:
        checks = list(pool.map(lambda probe: _probe(*probe), probes))

    by_name = {check["name"]: check["status"] for check in checks}
    if by_name.get("branches-near") == DOWN:
        status = DOWN
    elif any(check["status"] != OK for check in checks):
        status = DEGRADED
    else:
        status = OK
    return {
        "status": status,
        "latitude": latitude,
        "longitude": longitude,
        "checks": checks,
    }


class UpstreamProbe:
    """The upstream check, reused for ``ttl_seconds`` so polling stays cheap.

    A health check is expected to answer fast and often; calling the API on every
    poll would add load (and signed-request noise) for no extra certainty. The
    cached answer keeps ``cached: true`` in the report so a reader can tell a
    fresh probe from a reused one.
    """

    def __init__(
        self,
        client,
        *,
        latitude: float = DEFAULT_LATITUDE,
        longitude: float = DEFAULT_LONGITUDE,
        ttl_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._latitude = latitude
        self._longitude = longitude
        self._ttl = max(float(ttl_seconds), 0.0)
        self._lock = threading.Lock()
        self._checked_at = float("-inf")
        self._result: dict | None = None

    def check(self, *, now: float | None = None) -> dict:
        """The upstream report; a fresh call, or the cached one inside the TTL."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            cached = self._result
            fresh = cached is not None and moment - self._checked_at < self._ttl
        if cached is not None and fresh:
            return {**cached, "cached": True}

        result = check_upstream(self._client, self._latitude, self._longitude)
        with self._lock:
            self._result = result
            self._checked_at = moment
        return {**result, "cached": False}


def probe_coordinates(cfg: dict) -> tuple[float, float]:
    """A coordinate for the "near me" probe: the first saved place, else Hà Nội.

    Any coordinate serves; a saved place keeps the probe near venues the operator
    actually searches, and needs no extra configuration.
    """
    presets = cfg.get("search", {}).get("presets") or {}
    for value in presets.values():
        try:
            return float(value["lat"]), float(value["lng"])
        except (KeyError, TypeError, ValueError):
            continue
    return DEFAULT_LATITUDE, DEFAULT_LONGITUDE


def health_api_config(cfg: dict, timeout_seconds: float) -> dict:
    """A copy of *cfg* whose API client is tuned for a probe: one fast attempt.

    The service's own client retries with backoff and waits up to the configured
    timeout, which is right for a search but would make a health check hang for a
    minute during an outage. The probe gets a single attempt and a short timeout;
    a failure is the answer it is looking for.
    """
    tuned = copy.deepcopy(cfg)
    tuned["api"]["timeout_seconds"] = max(float(timeout_seconds), 1.0)
    tuned["api"]["max_retries"] = 1
    tuned["api"]["delay_min_seconds"] = 0.0
    tuned["api"]["delay_max_seconds"] = 0.0
    return tuned


# ------------------------------------------------------------------- report

def build_report(
    *,
    upstream: dict,
    storage: dict,
    disk: dict,
    state: dict,
    service: dict,
    version: str = __version__,
    now: dt.datetime | None = None,
) -> dict:
    """Combine the checks into one verdict: down beats degraded beats ok.

    ``httpStatus`` is what the endpoint answers with: 503 only when a search
    cannot work, so an orchestrator's liveness/readiness rule can be "is it 200".
    """
    checks = {"upstream": upstream, "storage": storage, "disk": disk, "state": state}
    if any(check["status"] == DOWN for check in checks.values()):
        status = DOWN
    elif any(check["status"] != OK for check in checks.values()):
        status = DEGRADED
    else:
        status = OK
    moment = now or dt.datetime.now()
    return {
        "status": status,
        "ok": status != DOWN,
        "httpStatus": 200 if status != DOWN else 503,
        "version": version,
        "checkedAt": moment.isoformat(timespec="seconds"),
        "checks": checks,
        "service": service,
    }

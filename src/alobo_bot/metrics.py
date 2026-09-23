"""In-process service metrics behind the ``/status`` page.

The counters live here, away from FastAPI, so they can be ticked and read in a
plain unit test and the page can be rendered from a frozen :class:`Snapshot`
without a server. :func:`alobo_bot.web.create_app` owns one :class:`Metrics`,
feeds it from a request middleware and the search routes, and hands a snapshot to
:func:`alobo_bot.webui.render_status`.

Counters are **durable** when given a ``path``: they are written to that JSON file
(a few seconds apart at most, and once more on shutdown) and read back at start,
so a restart resumes the running totals instead of showing zero. Only the process
uptime is per-process; everything counted — requests, clients, searches, cache
lookups — carries across. Nothing here touches the network or the AloBooking API:
these are counters about *this* process.
"""

from __future__ import annotations

import collections
import datetime as dt
import pathlib
import threading
import time
from dataclasses import dataclass

from .state import read_json, write_json

CLIENT_CAPACITY = 500          # distinct client addresses remembered for the table
SAVE_INTERVAL_SECONDS = 5.0    # most often the counters are flushed to disk


def status_class(status: int) -> str:
    """The class a status code belongs to: ``1xx``-``5xx``, else ``---``."""
    return f"{status // 100}xx" if 100 <= status < 600 else "---"


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _non_negative_float(value: object) -> float:
    try:
        return max(float(value), 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _moment(value: object, fallback: dt.datetime) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def _counts(value: object, key=str) -> dict:
    """A ``{key: int}`` mapping from stored JSON, skipping anything unusable."""
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for raw_key, raw_count in value.items():
        try:
            out[key(raw_key)] = int(raw_count)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True)
class ClientStat:
    """One client address and its most recent request."""

    ip: str
    requests: int
    first_seen: dt.datetime
    last_seen: dt.datetime
    last_path: str
    last_status: int


@dataclass(frozen=True)
class Snapshot:
    """A consistent read of the counters, taken at one instant."""

    started_at: dt.datetime
    now: dt.datetime
    requests: int
    in_flight: int
    errors: int
    by_path: dict[str, int]
    by_status: dict[int, int]
    latency_ms_avg: float | None
    latency_ms_max: float | None
    clients_seen: int
    clients: list[ClientStat]
    searches: int
    search_failures: int
    search_busy: int
    cache_hits: int
    cache_misses: int
    busy: bool
    last_run: dt.datetime | None
    last_error: str | None

    @property
    def uptime_seconds(self) -> float:
        return max((self.now - self.started_at).total_seconds(), 0.0)

    @property
    def error_rate(self) -> float:
        """Share of finished requests that answered 4xx or 5xx."""
        return self.errors / self.requests if self.requests else 0.0

    @property
    def cache_hit_rate(self) -> float:
        """Share of result-cache lookups served from the cache."""
        lookups = self.cache_hits + self.cache_misses
        return self.cache_hits / lookups if lookups else 0.0

    def to_dict(self) -> dict:
        """A JSON-ready view of the snapshot (``/status.json``)."""
        return {
            "startedAt": self.started_at.isoformat(timespec="seconds"),
            "now": self.now.isoformat(timespec="seconds"),
            "uptimeSeconds": round(self.uptime_seconds, 1),
            "requests": self.requests,
            "inFlight": self.in_flight,
            "errors": self.errors,
            "errorRate": round(self.error_rate, 4),
            "byPath": dict(self.by_path),
            "byStatus": {str(code): count for code, count in self.by_status.items()},
            "latencyMsAvg": None if self.latency_ms_avg is None else round(self.latency_ms_avg, 2),
            "latencyMsMax": None if self.latency_ms_max is None else round(self.latency_ms_max, 2),
            "clientsSeen": self.clients_seen,
            "clients": [
                {
                    "ip": client.ip,
                    "requests": client.requests,
                    "firstSeen": client.first_seen.isoformat(timespec="seconds"),
                    "lastSeen": client.last_seen.isoformat(timespec="seconds"),
                    "lastPath": client.last_path,
                    "lastStatus": client.last_status,
                }
                for client in self.clients
            ],
            "searches": self.searches,
            "searchFailures": self.search_failures,
            "searchBusy": self.search_busy,
            "cacheHits": self.cache_hits,
            "cacheMisses": self.cache_misses,
            "cacheHitRate": round(self.cache_hit_rate, 4),
            "busy": self.busy,
            "lastRun": None if self.last_run is None else self.last_run.isoformat(timespec="seconds"),
            "lastError": self.last_error,
        }


class Metrics:
    """Thread-safe, optionally durable counters for one service process.

    The request middleware and the search route both write here, and the request
    middleware runs on the event loop while a search runs on a worker thread, so
    every mutation and the snapshot read take the same lock. With a ``path`` the
    counters are flushed there (throttled) and loaded back on construction.
    """

    def __init__(
        self,
        *,
        started_at: dt.datetime | None = None,
        clients_capacity: int = CLIENT_CAPACITY,
        path: pathlib.Path | None = None,
        save_interval_seconds: float = SAVE_INTERVAL_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._started_at = started_at or dt.datetime.now()
        self._clients_capacity = clients_capacity
        self._path = path
        self._save_interval = save_interval_seconds
        self._last_save = time.monotonic()
        self._requests = 0
        self._in_flight = 0
        self._errors = 0
        self._by_path: collections.Counter[str] = collections.Counter()
        self._by_status: collections.Counter[int] = collections.Counter()
        self._latency_total_ms = 0.0
        self._latency_max_ms = 0.0
        self._clients: collections.OrderedDict[str, ClientStat] = collections.OrderedDict()
        self._clients_seen = 0
        self._searches = 0
        self._search_failures = 0
        self._search_busy = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._busy = False
        self._last_run: dt.datetime | None = None
        self._last_error: str | None = None
        self._load()

    # ------------------------------------------------------------- requests

    def begin_request(self) -> None:
        """Count a request that has started but not yet answered."""
        with self._lock:
            self._in_flight += 1

    def end_request(
        self,
        *,
        path: str,
        status: int,
        client_ip: str,
        duration_ms: float,
        now: dt.datetime | None = None,
    ) -> None:
        """Count a finished request: its path, status, client and latency."""
        moment = now or dt.datetime.now()
        with self._lock:
            self._in_flight = max(self._in_flight - 1, 0)
            self._requests += 1
            self._by_path[path] += 1
            self._by_status[status] += 1
            if status >= 400:
                self._errors += 1
            self._latency_total_ms += duration_ms
            self._latency_max_ms = max(self._latency_max_ms, duration_ms)
            self._touch_client(client_ip, path, status, moment)
        self._maybe_save()

    def _touch_client(self, ip: str, path: str, status: int, moment: dt.datetime) -> None:
        """Record one request against a client, keeping the most recent addresses."""
        known = self._clients.get(ip)
        if known is None:
            self._clients_seen += 1
            requests = 1
            first_seen = moment
        else:
            requests = known.requests + 1
            first_seen = known.first_seen
        self._clients[ip] = ClientStat(
            ip=ip, requests=requests, first_seen=first_seen, last_seen=moment,
            last_path=path, last_status=status,
        )
        self._clients.move_to_end(ip)
        while len(self._clients) > self._clients_capacity:
            self._clients.popitem(last=False)

    # -------------------------------------------------------------- searches

    def begin_search(self) -> bool:
        """Claim the single search slot; False when one is already running."""
        with self._lock:
            if self._busy:
                self._search_busy += 1
                return False
            self._busy = True
            return True

    def finish_search(self, *, now: dt.datetime | None = None) -> None:
        """Mark a search done: count it and stamp it as the last run."""
        with self._lock:
            self._busy = False
            self._searches += 1
            self._last_run = now or dt.datetime.now()
            self._last_error = None
        self._maybe_save()

    def fail_search(self, error: str) -> None:
        """Mark a search failed, remembering the message for the status page."""
        with self._lock:
            self._busy = False
            self._search_failures += 1
            self._last_error = error
        self._maybe_save()

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1
        self._maybe_save()

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1
        self._maybe_save()

    # ------------------------------------------------------------- snapshot

    def snapshot(self, *, clients: int = 10, now: dt.datetime | None = None) -> Snapshot:
        """A consistent copy of the counters; the *clients* most recently active."""
        moment = now or dt.datetime.now()
        with self._lock:
            recent = list(self._clients.values())
            recent = recent[-clients:] if clients > 0 else []
            recent.reverse()  # most recently active first
            return Snapshot(
                started_at=self._started_at,
                now=moment,
                requests=self._requests,
                in_flight=self._in_flight,
                errors=self._errors,
                by_path=dict(self._by_path),
                by_status=dict(self._by_status),
                latency_ms_avg=self._latency_total_ms / self._requests if self._requests else None,
                latency_ms_max=self._latency_max_ms if self._requests else None,
                clients_seen=self._clients_seen,
                clients=recent,
                searches=self._searches,
                search_failures=self._search_failures,
                search_busy=self._search_busy,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                busy=self._busy,
                last_run=self._last_run,
                last_error=self._last_error,
            )

    # ----------------------------------------------------------- persistence

    def save(self) -> bool:
        """Write the counters to disk; True when there is nowhere to write or it worked."""
        if self._path is None:
            return True
        with self._lock:
            state = self._state_dict()
            self._last_save = time.monotonic()
        return write_json(self._path, state)

    def _maybe_save(self) -> None:
        """Flush at most once per ``save_interval_seconds`` (kept off the hot path)."""
        if self._path is None:
            return
        if time.monotonic() - self._last_save >= self._save_interval:
            self.save()

    def _state_dict(self) -> dict:
        """The durable counters as JSON-ready data (caller holds the lock)."""
        return {
            "requests": self._requests,
            "errors": self._errors,
            "byPath": dict(self._by_path),
            "byStatus": {str(code): count for code, count in self._by_status.items()},
            "latencyTotalMs": self._latency_total_ms,
            "latencyMaxMs": self._latency_max_ms,
            "clientsSeen": self._clients_seen,
            "clients": [
                {
                    "ip": client.ip,
                    "requests": client.requests,
                    "firstSeen": client.first_seen.isoformat(timespec="seconds"),
                    "lastSeen": client.last_seen.isoformat(timespec="seconds"),
                    "lastPath": client.last_path,
                    "lastStatus": client.last_status,
                }
                for client in self._clients.values()
            ],
            "searches": self._searches,
            "searchFailures": self._search_failures,
            "searchBusy": self._search_busy,
            "cacheHits": self._cache_hits,
            "cacheMisses": self._cache_misses,
            "lastRun": None if self._last_run is None else self._last_run.isoformat(timespec="seconds"),
            "lastError": self._last_error,
        }

    def _load(self) -> None:
        """Resume the counters from disk; a missing or bad file leaves them at zero."""
        data = read_json(self._path) if self._path is not None else None
        if not isinstance(data, dict):
            return
        fallback = dt.datetime.now()
        with self._lock:
            self._requests = _int(data.get("requests"))
            self._errors = _int(data.get("errors"))
            self._by_path = collections.Counter(_counts(data.get("byPath")))
            self._by_status = collections.Counter(_counts(data.get("byStatus"), key=int))
            self._latency_total_ms = _non_negative_float(data.get("latencyTotalMs"))
            self._latency_max_ms = _non_negative_float(data.get("latencyMaxMs"))
            self._clients_seen = _int(data.get("clientsSeen"))
            self._clients = self._load_clients(data.get("clients"), fallback)
            self._clients_seen = max(self._clients_seen, len(self._clients))
            self._searches = _int(data.get("searches"))
            self._search_failures = _int(data.get("searchFailures"))
            self._search_busy = _int(data.get("searchBusy"))
            self._cache_hits = _int(data.get("cacheHits"))
            self._cache_misses = _int(data.get("cacheMisses"))
            last_run = data.get("lastRun")
            self._last_run = _moment(last_run, fallback) if last_run else None
            error = data.get("lastError")
            self._last_error = str(error) if error else None

    def _load_clients(
        self, value: object, fallback: dt.datetime
    ) -> collections.OrderedDict[str, ClientStat]:
        clients: collections.OrderedDict[str, ClientStat] = collections.OrderedDict()
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            ip = item.get("ip")
            if not isinstance(ip, str) or not ip:
                continue
            clients[ip] = ClientStat(
                ip=ip,
                requests=_int(item.get("requests")),
                first_seen=_moment(item.get("firstSeen"), fallback),
                last_seen=_moment(item.get("lastSeen"), fallback),
                last_path=str(item.get("lastPath") or ""),
                last_status=_int(item.get("lastStatus")),
            )
        while len(clients) > self._clients_capacity:
            clients.popitem(last=False)
        return clients

"""alobo-bot as a service: a server-rendered web page over the search.

``GET /`` renders the search form, and re-renders it with results when the form
is submitted (all state travels in the query string, so the page works without
JavaScript). ``GET /results`` returns just the results fragment, which the page's
fetch enhancement swaps in so pressing Find never reloads the page —
progressive enhancement over the same no-JavaScript form. A *reload* — or a
return to a search run recently — re-renders that run's cached result instead of
repeating the fan-out (the whole search lives in the URL, so refreshing was the
expensive case); ``/results`` always searches, so Find still fetches fresh.
``POST /find`` keeps a plain-markdown endpoint for scripts, and ``GET
/report.json`` exposes the last run. The search hits the network, so it runs in a
worker thread and is single-flighted.

fastapi/uvicorn are imported lazily so the plain CLI keeps zero web
dependencies; the pure markup lives in :mod:`webui` and is tested without them.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import pathlib
import time
from contextlib import asynccontextmanager
from typing import Any

from . import __version__
from .api import AloboClient, ApiError
from .config import load_config, validate
from .metrics import Metrics
from .parsing import parse_dt
from .pricing import ClockError, parse_clock
from .report import render_markdown, write_report
from .search import build_query, find_cheapest, presets_config
from .state import read_json, write_json
from .webui import render_page, render_results_region, render_status


def latest_report_file(report_dir: pathlib.Path, suffix: str) -> pathlib.Path | None:
    """The most recently written report with *suffix*, or None before the first run."""
    if not report_dir.is_dir():
        return None
    files = sorted(report_dir.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def report_listing(report_dir: pathlib.Path, limit: int = 8) -> list[dict]:
    """The newest report files (markdown + JSON) for the status page's Reports table.

    Reports are the one thing the bot already keeps on disk; listing them on the
    status page shows the durable output next to the counters. A file that
    vanishes between the glob and the stat (a concurrent run rewriting it) is
    skipped rather than fatal.
    """
    if not report_dir.is_dir():
        return []
    found: list[tuple[float, pathlib.Path]] = []
    for path in report_dir.iterdir():
        if path.is_file() and path.suffix in (".md", ".json"):
            try:
                found.append((path.stat().st_mtime, path))
            except OSError:
                continue
    found.sort(key=lambda item: item[0], reverse=True)
    listing = []
    for modified, path in found[:limit]:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        listing.append({
            "name": path.name,
            "modified": dt.datetime.fromtimestamp(modified),
            "bytes": size,
        })
    return listing


def has_search_params(**params: Any) -> bool:
    """True when the request actually asks for a search (vs. a blank form load)."""
    return any(params.get(key) not in (None, "") for key in ("place", "lat", "lng"))


def request_client_ip(request: Any) -> str:
    """The caller's address for the metrics: the first proxy hop, else the peer.

    Deployments put the bot behind a reverse proxy (the Docker image maps the port
    with no proxy of its own), so the socket peer is often the proxy. When the
    request carries ``X-Forwarded-For`` its first entry is the original client;
    otherwise the peer address stands in.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


# The failures a search can raise, and the message/status each maps to. Kept in one
# place so ``/``, ``/results`` and ``/find`` cannot drift apart.
SEARCH_FAILURES = (ClockError, ValueError, ApiError, RuntimeError)


def search_error(exc: Exception) -> tuple[str, int]:
    """Map a search failure to the (message, HTTP status) the routes answer with."""
    if isinstance(exc, ApiError):
        return f"could not reach the API: {exc}", 502
    if isinstance(exc, RuntimeError):  # the single-flight "a search is already running"
        return str(exc), 409
    return str(exc), 400  # ClockError / ValueError: a bad request


# How long a cached run stays reusable, and how many distinct searches are kept.
# The whole search travels in the URL, so a reload — or a return to a bookmarked
# search — would otherwise pay for the fan-out again; the cache makes it instant
# while keeping the view recent — past the window the page searches again rather
# than presenting stale prices as current.
RESULT_REUSE_SECONDS = 15 * 60
RESULT_CACHE_MAX = 32


def query_key(raw: dict) -> tuple:
    """Identity of a search: its request fields, so a request can be matched to it.

    ``GET /`` and the page's fetch carry the same fields, so the URL a reload
    arrives on has the same key as the search that rendered the page — which is
    what lets that request reuse the result instead of running the search again.
    """
    return (
        (raw.get("place") or "").strip(),
        raw.get("lat"), raw.get("lng"), raw.get("radius"),
        raw.get("date") or "",
        raw.get("from") or "", raw.get("to") or "",
        (raw.get("sport") or "").strip(),
        raw.get("limit"),
        raw.get("category") or "", raw.get("availability") or "",
    )


class ResultCache:
    """The last result of each search, so a reload re-renders it, not re-searches.

    Keyed by :func:`query_key`, so *any* recently run search can be revisited
    without a fresh fan-out — not just the most recent one. Bounded and
    time-limited: only the ``max_entries`` most recently used searches are kept,
    and an entry older than ``max_age`` counts as absent.

    Each entry is the search's **rendered results fragment**, so the whole cache
    is plain text: cheap to compare, and — with a ``path`` — written to disk and
    read back at start, so a restart keeps the warm results of searches run
    minutes earlier instead of dropping them all.
    """

    def __init__(
        self,
        *,
        max_entries: int = RESULT_CACHE_MAX,
        max_age: dt.timedelta = dt.timedelta(seconds=RESULT_REUSE_SECONDS),
        path: pathlib.Path | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._max_age = max_age.total_seconds()
        self._path = path
        self._entries: collections.OrderedDict[tuple, tuple[dt.datetime, str]] = (
            collections.OrderedDict()
        )
        self._load()

    def put(self, key: tuple, fragment: str, now: dt.datetime | None = None) -> None:
        """Record *key*'s fragment, dropping the least recently used beyond the bound."""
        self._entries[key] = (now or dt.datetime.now(), fragment)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        self.save()

    def get(self, key: tuple, now: dt.datetime | None = None) -> str | None:
        """The remembered fragment for *key*, or None when it is absent or stale."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        at, fragment = entry
        if ((now or dt.datetime.now()) - at).total_seconds() > self._max_age:
            del self._entries[key]  # stale: forget it, the caller will search
            return None
        self._entries.move_to_end(key)
        return fragment

    def save(self) -> bool:
        """Write the cache to disk; True when there is nowhere to write or it worked."""
        if self._path is None:
            return True
        return write_json(self._path, {
            "entries": [
                {"key": list(key), "at": at.isoformat(timespec="seconds"), "fragment": fragment}
                for key, (at, fragment) in self._entries.items()
            ]
        })

    def stats(self, now: dt.datetime | None = None) -> dict:
        """What the cache currently holds, for the ``/status`` page.

        Age is measured against *now*, so a reader sees how recent the warmest and
        coldest entries are — the cache is only useful inside the reuse window, so
        an aging set is the signal that the next visit will search afresh.
        """
        moment = now or dt.datetime.now()
        ages = [max((moment - at).total_seconds(), 0.0) for at, _ in self._entries.values()]
        return {
            "entries": len(self._entries),
            "capacity": self._max_entries,
            "reuse_seconds": self._max_age,
            "newest_age": min(ages) if ages else None,
            "oldest_age": max(ages) if ages else None,
            "path": str(self._path) if self._path is not None else None,
        }

    def _load(self) -> None:
        """Resume from disk, keeping only what is still inside the reuse window."""
        data = read_json(self._path) if self._path is not None else None
        if not isinstance(data, dict):
            return
        now = dt.datetime.now()
        entries = data.get("entries")
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            fragment = item.get("fragment")
            at = parse_dt(item.get("at"))
            if not isinstance(key, list) or not isinstance(fragment, str) or at is None:
                continue
            if (now - at).total_seconds() > self._max_age:
                continue
            self._entries[tuple(key)] = (at, fragment)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


def create_app() -> Any:
    """Build the FastAPI app (imports fastapi lazily on first call)."""
    from fastapi import Depends, FastAPI, Query
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

    cfg = load_config()
    validate(cfg)
    report_dir = pathlib.Path(cfg["report"]["dir"])
    state_dir = pathlib.Path(cfg["state"]["dir"])
    raw_areas = cfg["search"].get("areas") or []
    areas = [str(area) for area in raw_areas] if isinstance(raw_areas, list) else []
    presets = presets_config(cfg)
    default_category = str(cfg["search"].get("category") or "all")
    default_availability = str(cfg["search"].get("availability") or "any")

    state: dict[str, Any] = {
        "lock": asyncio.Lock(),
        "sports": None,
    }
    # Durable state, on the host under state.dir: the recent-search cache and the
    # /status counters are read back at start and written on change + shutdown, so
    # a restart resumes rather than starting from zero.
    cache = ResultCache(path=state_dir / "cache.json")
    metrics = Metrics(path=state_dir / "metrics.json")

    @asynccontextmanager
    async def lifespan(_app: Any):
        yield
        # Flush on the way out: the throttled saves may have left the last few
        # seconds' counters unwritten.
        metrics.save()
        cache.save()

    def sports_options() -> list[tuple[str, str]]:
        """(key, name) pairs for the sport dropdown, fetched once and cached."""
        if state["sports"] is None:
            try:
                state["sports"] = [(s.key, s.name) for s in AloboClient(cfg).sport_types()]
            except ApiError:
                state["sports"] = [(cfg["search"]["sport"], cfg["search"]["sport"])]
        return state["sports"]

    async def _run_find(**kwargs: Any):
        async with state["lock"]:
            # Single-flight: the search runs on a worker thread while the event
            # loop stays free, so a second visitor waits rather than piling on.
            if not metrics.begin_search():
                raise RuntimeError("a search is already running; try again in a moment")
            try:
                query = build_query(cfg, **kwargs)
                result = await asyncio.to_thread(find_cheapest, cfg, query)
                write_report(result, cfg)
                metrics.finish_search()
                return result
            except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
                metrics.fail_search(f"{type(exc).__name__}: {exc}")
                raise

    app = FastAPI(
        title="alobo-bot", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )

    @app.middleware("http")
    async def record_request(request: Any, call_next: Any) -> Response:
        """Count every request for /status: its path, status, client and latency.

        The request *completing* is what is recorded, so the counters a page reads
        are the ones before its own request is added (a status view does not count
        itself until it has been rendered).
        """
        client_ip = request_client_ip(request)
        metrics.begin_request()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.end_request(path=request.url.path, status=500, client_ip=client_ip,
                                duration_ms=(time.perf_counter() - started) * 1000)
            raise
        metrics.end_request(path=request.url.path, status=response.status_code,
                            client_ip=client_ip,
                            duration_ms=(time.perf_counter() - started) * 1000)
        return response

    def _render(
        *,
        raw_query: dict,
        result=None,
        results_html: str | None = None,
        error: str | None = None,
        status: int = 200,
    ) -> Response:
        return HTMLResponse(
            render_page(
                sports=sports_options(),
                areas=areas,
                presets=presets,
                query=raw_query,
                result=result,
                results_html=results_html,
                error=error,
            ),
            status_code=status,
        )

    async def search_params(
        place: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius: float | None = None,
        date: str | None = None,
        time_from: str = Query("18:00", alias="from"),
        time_to: str = Query("21:00", alias="to"),
        sport: str | None = None,
        limit: int | None = None,
        category: str | None = None,
        availability: str | None = None,
    ) -> dict:
        """The search fields, declared once for every route that takes them.

        ``/`` (the page), ``/results`` (the fragment the page fetches) and
        ``/find`` (the markdown body) all read the same query string; keeping the
        signature here stops the three from drifting apart.
        """
        return {
            "place": place, "lat": lat, "lng": lng, "radius": radius, "date": date,
            "from": time_from, "to": time_to, "sport": sport, "limit": limit,
            "category": category or default_category,
            "availability": availability or default_availability,
        }

    async def _search(raw: dict):
        """Build the query from raw request fields and run the search."""
        day = dt.date.fromisoformat(raw["date"]) if raw["date"] else None
        result = await _run_find(
            place=raw["place"], latitude=raw["lat"], longitude=raw["lng"],
            radius_km=raw["radius"], day=day, start_minute=parse_clock(raw["from"]),
            end_minute=parse_clock(raw["to"]), sport=raw["sport"],
            max_branches=raw["limit"], category=raw["category"],
            availability=raw["availability"],
        )
        # Remember the rendered fragment, keyed by the fields: ``/`` reads this
        # back (that is the reload), while ``/results`` — what the page's Find
        # fetches — always searches, so pressing Find stays fresh. Storing the
        # fragment (not the result object) keeps the cache plain text, so it can
        # be written to disk and reloaded after a restart.
        cache.put(query_key(raw), render_results_region(result=result))
        return result

    @app.get("/", response_class=HTMLResponse)
    async def index(raw: dict = Depends(search_params)) -> Response:
        if not has_search_params(place=raw["place"], lat=raw["lat"], lng=raw["lng"]):
            return _render(raw_query=raw)
        # A reload lands here with the same fields as the search that rendered the
        # page, so re-render its result instead of paying for the fan-out again.
        cached = cache.get(query_key(raw))
        if cached is not None:
            metrics.record_cache_hit()
            return _render(raw_query=raw, results_html=cached)
        metrics.record_cache_miss()
        try:
            result = await _search(raw)
        except SEARCH_FAILURES as exc:
            message, status = search_error(exc)
            return _render(raw_query=raw, error=message, status=status)
        return _render(raw_query=raw, result=result)

    @app.get("/results", response_class=HTMLResponse)
    async def results(raw: dict = Depends(search_params)) -> Response:
        """Just the results fragment — what the page's fetch enhancement swaps in.

        The same query string as ``/``, so a no-JavaScript visitor still gets the
        whole page from ``/`` while the script receives only the region it needs to
        replace. The error responses carry the fragment too, so the client can swap
        the body whatever the status.
        """
        if not has_search_params(place=raw["place"], lat=raw["lat"], lng=raw["lng"]):
            return HTMLResponse(render_results_region())
        try:
            result = await _search(raw)
        except SEARCH_FAILURES as exc:
            message, status = search_error(exc)
            return HTMLResponse(render_results_region(error=message), status_code=status)
        return HTMLResponse(render_results_region(result=result))

    @app.post("/find", response_class=PlainTextResponse)
    async def find_now(raw: dict = Depends(search_params)) -> Response:
        """Machine-readable variant of the same search (markdown body)."""
        try:
            result = await _search(raw)
        except SEARCH_FAILURES as exc:
            message, status = search_error(exc)
            return PlainTextResponse(message, status_code=status)
        return PlainTextResponse(render_markdown(result))

    @app.get("/report.json")
    def report_json() -> Response:
        latest = latest_report_file(report_dir, ".json")
        if latest is None:
            return JSONResponse({"detail": "no report yet"}, status_code=404)
        return Response(latest.read_bytes(), media_type="application/json")

    @app.get("/status", response_class=HTMLResponse)
    def status_page() -> Response:
        """The human-facing monitoring page: traffic, clients, searches, reports.

        Not linked from the public page — reach it at /status directly. The
        request middleware counts this request too, so the numbers shown are the
        ones from before it.
        """
        snapshot = metrics.snapshot()
        return HTMLResponse(render_status(
            snapshot,
            version=__version__,
            sport=str(cfg["search"]["sport"]),
            state_path=str(state_dir),
            report_dir=str(report_dir),
            reports=report_listing(report_dir),
            cache=cache.stats(),
        ))

    @app.get("/status.json")
    def status_json() -> JSONResponse:
        """The same metrics a machine can read (for scraping or alerting)."""
        return JSONResponse({
            "version": __version__,
            "sport": cfg["search"]["sport"],
            "cache": cache.stats(),
            **metrics.snapshot().to_dict(),
        })

    return app

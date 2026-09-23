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
import traceback
from typing import Any

from . import __version__
from .api import AloboClient, ApiError
from .config import load_config, validate
from .pricing import ClockError, parse_clock
from .report import render_markdown, write_report
from .search import FindResult, build_query, find_cheapest, presets_config
from .webui import render_page, render_results_region


def latest_report_file(report_dir: pathlib.Path, suffix: str) -> pathlib.Path | None:
    """The most recently written report with *suffix*, or None before the first run."""
    if not report_dir.is_dir():
        return None
    files = sorted(report_dir.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def has_search_params(**params: Any) -> bool:
    """True when the request actually asks for a search (vs. a blank form load)."""
    return any(params.get(key) not in (None, "") for key in ("place", "lat", "lng"))


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
    """

    def __init__(
        self,
        *,
        max_entries: int = RESULT_CACHE_MAX,
        max_age: dt.timedelta = dt.timedelta(seconds=RESULT_REUSE_SECONDS),
    ) -> None:
        self._max_entries = max_entries
        self._max_age = max_age.total_seconds()
        self._entries: collections.OrderedDict[tuple, tuple[dt.datetime, FindResult]] = (
            collections.OrderedDict()
        )

    def put(self, key: tuple, result: FindResult, now: dt.datetime | None = None) -> None:
        """Record *key*'s result, dropping the least recently used beyond the bound."""
        self._entries[key] = (now or dt.datetime.now(), result)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def get(self, key: tuple, now: dt.datetime | None = None) -> FindResult | None:
        """The remembered result for *key*, or None when it is absent or stale."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        at, result = entry
        if ((now or dt.datetime.now()) - at).total_seconds() > self._max_age:
            del self._entries[key]  # stale: forget it, the caller will search
            return None
        self._entries.move_to_end(key)
        return result


def create_app() -> Any:
    """Build the FastAPI app (imports fastapi lazily on first call)."""
    from fastapi import Depends, FastAPI, Query
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

    cfg = load_config()
    validate(cfg)
    report_dir = pathlib.Path(cfg["report"]["dir"])
    raw_areas = cfg["search"].get("areas") or []
    areas = [str(area) for area in raw_areas] if isinstance(raw_areas, list) else []
    presets = presets_config(cfg)
    default_category = str(cfg["search"].get("category") or "all")
    default_availability = str(cfg["search"].get("availability") or "any")

    state: dict[str, Any] = {
        "lock": asyncio.Lock(),
        "busy": False,
        "last_run": None,
        "last_error": None,
        "sports": None,
    }
    cache = ResultCache()

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
            if state["busy"]:
                raise RuntimeError("a search is already running; try again in a moment")
            state["busy"] = True
            state["last_error"] = None
            try:
                query = build_query(cfg, **kwargs)
                result = await asyncio.to_thread(find_cheapest, cfg, query)
                write_report(result, cfg)
                state["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
                return result
            except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
                state["last_error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                state["busy"] = False

    app = FastAPI(title="alobo-bot", version=__version__, docs_url=None, redoc_url=None)

    def _render(
        *,
        raw_query: dict,
        result=None,
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
        # Remember it, keyed by the fields: ``/`` reads this back (that is the
        # reload), while ``/results`` — what the page's Find fetches — always
        # searches, so pressing Find stays fresh.
        cache.put(query_key(raw), result)
        return result

    @app.get("/", response_class=HTMLResponse)
    async def index(raw: dict = Depends(search_params)) -> Response:
        if not has_search_params(place=raw["place"], lat=raw["lat"], lng=raw["lng"]):
            return _render(raw_query=raw)
        # A reload lands here with the same fields as the search that rendered the
        # page, so re-render its result instead of paying for the fan-out again.
        cached = cache.get(query_key(raw))
        if cached is not None:
            return _render(raw_query=raw, result=cached)
        try:
            result = await _search(raw)
        except (ClockError, ValueError) as exc:
            return _render(raw_query=raw, error=str(exc), status=400)
        except ApiError as exc:
            return _render(raw_query=raw, error=f"could not reach the API: {exc}", status=502)
        except RuntimeError as exc:
            return _render(raw_query=raw, error=str(exc), status=409)
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
        except (ClockError, ValueError) as exc:
            return HTMLResponse(render_results_region(error=str(exc)), status_code=400)
        except ApiError as exc:
            return HTMLResponse(
                render_results_region(error=f"could not reach the API: {exc}"), status_code=502
            )
        except RuntimeError as exc:  # the single-flight "a search is already running" case
            return HTMLResponse(render_results_region(error=str(exc)), status_code=409)
        return HTMLResponse(render_results_region(result=result))

    @app.post("/find", response_class=PlainTextResponse)
    async def find_now(raw: dict = Depends(search_params)) -> Response:
        """Machine-readable variant of the same search (markdown body)."""
        try:
            result = await _search(raw)
        except (ClockError, ValueError) as exc:
            return PlainTextResponse(f"invalid request: {exc}", status_code=400)
        except ApiError as exc:
            return PlainTextResponse(f"find failed: {exc}", status_code=502)
        except RuntimeError as exc:
            return PlainTextResponse(str(exc), status_code=409)
        return PlainTextResponse(render_markdown(result))

    @app.get("/report.json")
    def report_json() -> Response:
        latest = latest_report_file(report_dir, ".json")
        if latest is None:
            return JSONResponse({"detail": "no report yet"}, status_code=404)
        return Response(latest.read_bytes(), media_type="application/json")

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "busy": state["busy"],
                "last_run": state["last_run"],
                "last_error": state["last_error"],
                "sport": cfg["search"]["sport"],
            }
        )

    return app

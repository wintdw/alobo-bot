"""alobo-bot as a service: a server-rendered web page over the search.

``GET /`` renders the search form, and re-renders it with results when the form
is submitted (all state travels in the query string, so the page works without
JavaScript). ``POST /find`` keeps a plain-markdown endpoint for scripts, and
``GET /report.json`` exposes the last run. The search hits the network, so it
runs in a worker thread and is single-flighted.

fastapi/uvicorn are imported lazily so the plain CLI keeps zero web
dependencies; the pure markup lives in :mod:`webui` and is tested without them.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import traceback
from typing import Any

from . import __version__
from .api import AloboClient, ApiError
from .config import load_config, validate
from .pricing import ClockError, parse_clock
from .report import render_markdown, write_report
from .search import build_query, find_cheapest
from .webui import render_page


def latest_report_file(report_dir: pathlib.Path, suffix: str) -> pathlib.Path | None:
    """The most recently written report with *suffix*, or None before the first run."""
    if not report_dir.is_dir():
        return None
    files = sorted(report_dir.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def has_search_params(**params: Any) -> bool:
    """True when the request actually asks for a search (vs. a blank form load)."""
    return any(params.get(key) not in (None, "") for key in ("place", "lat", "lng"))


def create_app() -> Any:
    """Build the FastAPI app (imports fastapi lazily on first call)."""
    from fastapi import FastAPI, Query
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

    cfg = load_config()
    validate(cfg)
    report_dir = pathlib.Path(cfg["report"]["dir"])

    state: dict[str, Any] = {
        "lock": asyncio.Lock(),
        "busy": False,
        "last_run": None,
        "last_error": None,
        "sports": None,
    }

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
                raise RuntimeError("a search is already running — try again in a moment")
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
            render_page(sports=sports_options(), query=raw_query, result=result, error=error),
            status_code=status,
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(
        place: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius: float | None = None,
        date: str | None = None,
        time_from: str = Query("18:00", alias="from"),
        time_to: str = Query("21:00", alias="to"),
        sport: str | None = None,
        limit: int | None = None,
    ) -> Response:
        raw = {
            "place": place, "lat": lat, "lng": lng, "radius": radius,
            "date": date, "from": time_from, "to": time_to,
            "sport": sport, "limit": limit,
        }
        if not has_search_params(place=place, lat=lat, lng=lng):
            return _render(raw_query=raw)
        try:
            day = dt.date.fromisoformat(date) if date else None
            result = await _run_find(
                place=place, latitude=lat, longitude=lng, radius_km=radius, day=day,
                start_minute=parse_clock(time_from), end_minute=parse_clock(time_to),
                sport=sport, max_branches=limit,
            )
        except (ClockError, ValueError) as exc:
            return _render(raw_query=raw, error=str(exc), status=400)
        except ApiError as exc:
            return _render(raw_query=raw, error=f"không gọi được API: {exc}", status=502)
        except RuntimeError as exc:
            return _render(raw_query=raw, error=str(exc), status=409)
        return _render(raw_query=raw, result=result)

    @app.post("/find", response_class=PlainTextResponse)
    async def find_now(
        place: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius: float | None = None,
        date: str | None = None,
        time_from: str = Query("18:00", alias="from"),
        time_to: str = Query("21:00", alias="to"),
        sport: str | None = None,
        limit: int | None = None,
    ) -> Response:
        """Machine-readable variant of the same search (markdown body)."""
        try:
            day = dt.date.fromisoformat(date) if date else None
            result = await _run_find(
                place=place, latitude=lat, longitude=lng, radius_km=radius, day=day,
                start_minute=parse_clock(time_from), end_minute=parse_clock(time_to),
                sport=sport, max_branches=limit,
            )
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

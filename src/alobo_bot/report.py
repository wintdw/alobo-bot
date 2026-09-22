"""Render a :class:`~alobo_bot.search.FindResult` as JSON, markdown and console text."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from .search import FindResult
from .pricing import window_bounds


def money(value: float) -> str:
    return f"{value:,.0f}đ".replace(",", ".")


def _window_label(result: FindResult) -> str:
    q = result.query
    start, end = window_bounds(q.day, q.start_minute, q.end_minute)
    same = start.date() == end.date()
    start_text = f"{start:%H:%M}"
    end_text = f"{end:%H:%M}" if same else f"{end:%d/%m %H:%M}"
    return f"{q.day:%d/%m/%Y} {start_text}-{end_text}"


def to_dict(result: FindResult) -> dict[str, Any]:
    """A serialisable snapshot of the result (used for --json and raw snapshots)."""
    query = result.query
    return {
        "generatedAt": result.generated_at.isoformat(timespec="seconds"),
        "sport": query.sport,
        "sportName": result.sport_name,
        "place": query.place,
        "latitude": query.latitude,
        "longitude": query.longitude,
        "radiusKm": query.radius_km,
        "date": query.day.isoformat(),
        "from": f"{query.start_minute // 60:02d}:{query.start_minute % 60:02d}",
        "to": f"{query.end_minute // 60:02d}:{query.end_minute % 60:02d}",
        "branchesScanned": result.branches_scanned,
        "options": [
            {
                "rank": index,
                "branchId": opt.branch.id,
                "branchName": opt.branch.name,
                "address": opt.branch.address,
                "distanceKm": round(opt.distance_km, 2) if opt.distance_km is not None else None,
                "courtId": opt.core.id,
                "courtName": opt.core.name,
                "priceType": opt.core_type.name,
                "totalPrice": round(opt.total_price),
                "hourlyPrice": round(opt.hourly_price),
                "hours": opt.hours,
                "phone": opt.branch.phone,
                "bookingUrl": opt.booking_url,
            }
            for index, opt in enumerate(result.ranked, start=1)
        ],
        "socialSessions": [
            {
                "branchId": res.branch.id,
                "branchName": res.branch.name,
                "sessionId": session.id,
                "name": session.name,
                "start": session.start.isoformat(timespec="minutes") if session.start else None,
                "durationMin": session.duration_min,
                "ticketPrice": round(session.ticket_price),
                "spotsLeft": session.spots_left,
                "courts": session.court_names,
            }
            for res in result.results
            for session in res.sessions
        ],
    }


def render_text(result: FindResult) -> str:
    """Compact console output for the `find` command."""
    lines = [
        f"Cheapest {result.sport_name} courts — {_window_label(result)}",
        f"Area: {result.query.place or '%.4f, %.4f' % (result.query.latitude, result.query.longitude)}"
        f" · {result.branches_scanned} branch(es) scanned",
        "",
    ]
    ranked = result.ranked
    if not ranked:
        lines.append("No priced courts found. Try a wider --radius, another --place, or another date.")
    else:
        lines.append(f"{'#':>3}  {'price':>12}  {'/hour':>10}  {'dist':>6}  venue / court")
        for index, opt in enumerate(ranked[:20], start=1):
            dist = f"{opt.distance_km:.1f}km" if opt.distance_km is not None else "  -"
            lines.append(
                f"{index:>3}  {money(opt.total_price):>12}  {money(opt.hourly_price):>10}  {dist:>6}  "
                f"{opt.branch.name} / {opt.core.name}"
            )
            lines.append(f"       {opt.branch.address}")
    sessions = [s for res in result.results for s in res.sessions]
    if sessions:
        lines += ["", "Social / open-play sessions in this window (price per person):"]
        for session in sorted(sessions, key=lambda s: s.ticket_price):
            when = f"{session.start:%H:%M}" if session.start else "?"
            lines.append(f"  - {money(session.ticket_price)} · {session.name} @ {when} · {session.spots_left} spots")
    return "\n".join(lines)


def render_markdown(result: FindResult) -> str:
    lines = [
        f"# Cheapest {result.sport_name} courts — {_window_label(result)}",
        "",
        f"*Area:* {result.query.place or '%.4f, %.4f' % (result.query.latitude, result.query.longitude)}  ",
        f"*Branches scanned:* {result.branches_scanned}  ",
        f"*Generated:* {result.generated_at:%Y-%m-%d %H:%M}",
        "",
        "| # | Total | Per hour | Court | Venue | Distance |",
        "|--:|------:|---------:|-------|-------|---------:|",
    ]
    for index, opt in enumerate(result.ranked, start=1):
        dist = f"{opt.distance_km:.1f} km" if opt.distance_km is not None else "—"
        lines.append(
            f"| {index} | {money(opt.total_price)} | {money(opt.hourly_price)} | "
            f"{opt.core.name} | [{opt.branch.name}]({opt.booking_url}) | {dist} |"
        )
    if not result.ranked:
        lines.append("| — | — | — | — | _no priced courts found_ | — |")

    sessions = [s for res in result.results for s in res.sessions]
    if sessions:
        lines += ["", "## Social / open-play sessions (per person)", "",
                  "| Price | Session | Start | Spots left | Venue |",
                  "|------:|---------|-------|-----------:|-------|"]
        for res in result.results:
            for session in sorted(res.sessions, key=lambda s: s.ticket_price):
                when = f"{session.start:%H:%M}" if session.start else "—"
                lines.append(
                    f"| {money(session.ticket_price)} | {session.name} | {when} | "
                    f"{session.spots_left} | {res.branch.name} |"
                )
    lines.append("")
    return "\n".join(lines)


def write_report(result: FindResult, cfg: dict) -> tuple[pathlib.Path, pathlib.Path]:
    """Write the latest markdown + JSON report and a dated raw snapshot."""
    report_dir = pathlib.Path(cfg["report"]["dir"])
    raw_dir = pathlib.Path(cfg["raw"]["dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    payload = to_dict(result)
    md_path = report_dir / "cheapest.md"
    json_path = report_dir / "cheapest.json"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (raw_dir / f"find_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return md_path, json_path

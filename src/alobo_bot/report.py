"""Render a :class:`~alobo_bot.search.FindResult` as JSON, markdown and console text."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from .search import FindQuery, FindResult
from .pricing import window_bounds
from .availability import label as availability_label


def money(value: float) -> str:
    return f"{value:,.0f}đ".replace(",", ".")


def hours_label(value: float) -> str:
    """The sellable hours of a window as a compact label, e.g. ``6h`` / ``2.5h``."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text or '0'}h"


def area_label(query: FindQuery) -> str:
    """Where a search ran: the area text, else the saved place's name, else its coordinates."""
    if query.place:
        return query.place
    if query.preset:
        return query.preset
    return "%.4f, %.4f" % (query.latitude, query.longitude)


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
        "category": query.category,
        "availability": query.availability,
        "place": query.place,
        "preset": query.preset,
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
                "priceTargetId": opt.target_id or None,
                "priceTarget": opt.target_name or None,
                "availability": opt.available,
                "freeSpans": [
                    {"from": start.isoformat(timespec="minutes"),
                     "to": end.isoformat(timespec="minutes")}
                    for start, end in opt.free_spans
                ],
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
                "rank": index,
                "branchId": session.branch.id if session.branch else None,
                "branchName": session.branch.name if session.branch else None,
                "address": session.branch.address if session.branch else None,
                "distanceKm": round(session.distance_km, 2) if session.distance_km is not None else None,
                "sessionId": session.id,
                "name": session.name,
                "start": session.start.isoformat(timespec="minutes") if session.start else None,
                "end": session.end.isoformat(timespec="minutes") if session.end else None,
                "durationMin": session.duration_min,
                "ticketPrice": round(session.ticket_price),
                "spotsLeft": session.spots_left,
                "courts": session.court_names,
                "bookingUrl": session.booking_url,
            }
            for index, session in enumerate(result.ranked_social, start=1)
        ],
    }


def _category_heading(result: FindResult, category: str) -> str:
    """A one-line label for a category, naming the unit it is priced in."""
    if category == "social":
        count = len(result.ranked_social)
        return (f"Tickets (xé vé) — per person, nearest then cheapest then longest "
                f"in the window · {count} ticket(s)")
    label = f"Courts — per court, most hours then cheapest rate · {len(result.ranked)} court(s)"
    if result.query.free_only:
        label += " · free only"
    return label


def render_text(result: FindResult) -> str:
    """Compact console output for the `find` command: tickets first, then courts."""
    query = result.query
    lines = [
        f"Cheapest {result.sport_name} — {_window_label(result)}",
        f"Area: {area_label(query)}"
        f" · {result.branches_scanned} branch(es) scanned",
        "",
    ]
    if query.wants_social:
        lines += _text_social(result)
    if query.wants_courts:
        lines += _text_courts(result)
    return "\n".join(lines)


def _session_location(session) -> str:
    """Distance and street address for a ticket's venue (either part may be missing)."""
    address = session.branch.address if session.branch else ""
    parts = []
    if session.distance_km is not None:
        parts.append(f"{session.distance_km:.1f} km")
    if address:
        parts.append(address)
    return " · ".join(parts)


def _session_span(session) -> str:
    """The session's clock span, e.g. "19:00-21:00" (start alone without a duration)."""
    if session.start is None:
        return "?"
    start = f"{session.start:%H:%M}"
    return f"{start}-{session.end:%H:%M}" if session.end else start


def _text_social(result: FindResult) -> list[str]:
    lines = [_category_heading(result, "social")]
    tickets = result.ranked_social
    if not tickets:
        lines.append("  none on sale in this window")
        return lines + [""]
    for session in tickets:
        when = _session_span(session)
        venue = session.branch.name if session.branch else "?"
        lines.append(
            f"  - {money(session.ticket_price)} · {session.name} @ {when} · "
            f"{session.spots_left} spots · {venue}"
        )
        location = _session_location(session)
        if location:
            lines.append(f"       {location}")
    return lines + [""]


def _text_courts(result: FindResult) -> list[str]:
    lines = [_category_heading(result, "court")]
    ranked = result.ranked
    if not ranked:
        lines.append("  none priced in this window — try a wider radius, another area or date")
        return lines + [""]
    lines.append(
        f"{'#':>3}  {'hours':>5}  {'price':>12}  {'/hour':>10}  {'dist':>6}  "
        f"{'status':<19}  court · venue"
    )
    for index, opt in enumerate(ranked[:20], start=1):
        dist = f"{opt.distance_km:.1f}km" if opt.distance_km is not None else "  -"
        status = availability_label(opt.available, opt.free_spans)
        lines.append(
            f"{index:>3}  {hours_label(opt.hours):>5}  {money(opt.total_price):>12}  "
            f"{money(opt.hourly_price):>10}  {dist:>6}  "
            f"{status:<19}  {opt.core.name} · {opt.branch.name}"
        )
        target = f" · target: {opt.target_name}" if opt.target_name else ""
        lines.append(f"       {opt.branch.address}{target}")
    return lines + [""]


def render_markdown(result: FindResult) -> str:
    query = result.query
    lines = [
        f"# Cheapest {result.sport_name} — {_window_label(result)}",
        "",
        f"*Area:* {area_label(query)}  ",
        f"*Branches scanned:* {result.branches_scanned}  ",
        f"*Generated:* {result.generated_at:%Y-%m-%d %H:%M}",
    ]
    if query.wants_social:
        lines += _markdown_social(result)
    if query.wants_courts:
        lines += _markdown_courts(result)
    lines.append("")
    return "\n".join(lines)


def _markdown_social(result: FindResult) -> list[str]:
    lines = ["", "## " + _category_heading(result, "social"), "",
             "| # | Ticket | Session | Starts | Ends | Spots left | Venue | Location | Distance |",
             "|--:|-------:|---------|--------|------|-----------:|-------|----------|---------:|"]
    tickets = result.ranked_social
    if not tickets:
        lines.append("| — | — | _none on sale in this window_ | — | — | — | — | — | — |")
        return lines
    for index, session in enumerate(tickets, start=1):
        starts = f"{session.start:%H:%M}" if session.start else "—"
        ends = f"{session.end:%H:%M}" if session.end else "—"
        venue = f"[{session.branch.name}]({session.booking_url})" if session.branch else "—"
        address = session.branch.address if session.branch else "—"
        distance = f"{session.distance_km:.1f} km" if session.distance_km is not None else "—"
        lines.append(
            f"| {index} | {money(session.ticket_price)} | {session.name} | {starts} | {ends} | "
            f"{session.spots_left} | {venue} | {address} | {distance} |"
        )
    return lines


def _markdown_courts(result: FindResult) -> list[str]:
    lines = ["", "## " + _category_heading(result, "court"), "",
             "| # | Hours | Total | Per hour | Court | Status | Target | Venue | Distance |",
             "|--:|------:|------:|---------:|-------|--------|--------|-------|---------:|"]
    ranked = result.ranked
    if not ranked:
        lines.append("| — | — | — | — | — | — | — | _no priced courts found_ | — |")
        return lines
    for index, opt in enumerate(ranked, start=1):
        dist = f"{opt.distance_km:.1f} km" if opt.distance_km is not None else "—"
        lines.append(
            f"| {index} | {hours_label(opt.hours)} | {money(opt.total_price)} | "
            f"{money(opt.hourly_price)} | "
            f"{opt.core.name} | "
            f"{availability_label(opt.available, opt.free_spans)} | "
            f"{opt.target_name or '—'} | "
            f"[{opt.branch.name}]({opt.booking_url}) | {dist} |"
        )
    return lines


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

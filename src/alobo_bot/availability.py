"""Whether a court is free for a requested window.

AloBooking publishes each branch's *existing* one-time bookings through a public
endpoint (:meth:`~alobo_bot.api.AloboClient.get_onetime_bookings`): a court is
free for a window exactly when none of those bookings overlaps it. This module
turns the raw bookings into a per-court verdict plus the open sub-spans, and is
pure, so it is tested without the network.

Four verdicts, because "is it free?" has more than two answers:

* ``free``    — nothing is booked anywhere in the window;
* ``booked``  — the window is covered end to end;
* ``partial`` — some of it is free, and :func:`free_spans` says which part;
* ``unknown`` — the lookup failed, so there is no answer to give. The endpoint
  rejects dates outside the branch's booking window, so this is normal, not an
  error, and must never be reported as ``booked``.

The window handed in is the part of the request the venue actually sells (see
:func:`alobo_bot.search.bookable_window`), and the caller decides what to do with
the verdict: the search quotes a ``partial`` court for its open spans only — the
rest is somebody else's booking — and drops a ``booked`` one, which has nothing
left to sell.
"""

from __future__ import annotations

import datetime as dt

from .models import Booking

FREE = "free"
BOOKED = "booked"
PARTIAL = "partial"
UNKNOWN = "unknown"

LABELS = {FREE: "free", BOOKED: "booked", UNKNOWN: "unknown"}

Span = tuple[dt.datetime, dt.datetime]


def label(status: str | None, spans: list[Span] | None = None) -> str:
    """Short human label for a verdict, naming the free span when it is partial."""
    if status == PARTIAL and spans:
        return f"partial {spans_label(spans)}"
    return LABELS.get(status or "", "?")


def spans_label(spans: list[Span]) -> str:
    """The spans as clock ranges, e.g. ``"20:00-21:00"``."""
    return ", ".join(f"{start:%H:%M}-{end:%H:%M}" for start, end in spans)


def free_spans(
    bookings: list[Booking],
    core_id: str,
    start: dt.datetime,
    end: dt.datetime,
) -> list[Span]:
    """The parts of ``[start, end)`` that *core_id* is not booked for.

    Empty means the whole window is taken; a single span equal to the window
    means it is entirely free; anything else is partial.
    """
    blocked = sorted(
        overlap
        for booking in bookings
        if booking.core_id == core_id
        if (overlap := booking.overlap(start, end)) is not None
    )
    spans: list[Span] = []
    cursor = start
    for open_at, taken_until in blocked:
        if open_at > cursor:
            spans.append((cursor, open_at))
        cursor = max(cursor, taken_until)
    if cursor < end:
        spans.append((cursor, end))
    return spans


def court_availability(
    bookings: list[Booking] | None,
    core_id: str,
    start: dt.datetime,
    end: dt.datetime,
) -> tuple[str, list[Span]]:
    """The verdict and the free spans for one court over ``[start, end)``.

    *bookings* is every booking the branch reports for the day(s) the window
    covers, or None when the lookup failed.
    """
    if bookings is None:
        return UNKNOWN, []
    spans = free_spans(bookings, core_id, start, end)
    if not spans:
        return BOOKED, []
    if len(spans) == 1 and spans[0] == (start, end):
        return FREE, spans
    return PARTIAL, spans

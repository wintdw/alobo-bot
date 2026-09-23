"""Tie the API calls together: pick candidate branches, price their courts, rank.

The flow for one ``find``:

1. Resolve the requested sport to its int value (pickleball == 5).
2. Gather every branch, keep those whose sport matches, then narrow by location
   — either a radius around ``--lat/--lng`` or a text match on ``--place``.
3. For each surviving branch, fetch its courts and price table and compute the
   cost of the requested window for every court that is actually that sport.
4. Also collect social/open-play sessions on the day (sold per ticket; "xé vé").

A branch prices a court type per **tariff** (the app's "đối tượng áp dụng": a
walk-up rate, a quarterly-payer discount, a monthly ticket, a ball machine…).
``target`` names one by id or name; left unset, each type is priced at its
generic customer tariff (:func:`alobo_bot.models.CoreType.default_target`).

The window is clipped to the venue's working hours first
(:func:`bookable_window`): a branch sells nothing before it opens or after it
closes, so those hours are neither priced nor reported free.

The last two are two separate **categories** — ``court`` (a whole court, priced
per court) and ``social`` (a ticket, priced per person) — so they are ranked
apart and never mixed into one table. ``category`` picks which to run: ``all``
does both, a single one skips the other's work entirely.

Every priced court is also tagged with its **availability** for the window
(``free``/``partial``/``?``; see :mod:`alobo_bot.availability`), read from the
branch's public booking list, and priced for the part of the window it can
actually sell: a court free for one hour of five is quoted for that hour. A court
with no open time left is not a result at all. ``availability`` picks whether
partly-free courts are kept (``any``) or only those free for the whole window
(``free``).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .api import AloboClient
from .availability import BOOKED, PARTIAL, UNKNOWN, court_availability
from .models import (
    Booking,
    Branch,
    Core,
    CoreType,
    CourtOption,
    PriceTable,
    PriceTarget,
    SocialSession,
)
from .pricing import (
    MINUTES_PER_DAY,
    clip_to_hours,
    haversine_km,
    hours_between,
    window_bounds,
    window_cost,
)

_TOKEN_RE = re.compile(r"[^a-z0-9 ]+")

COURT_CATEGORY = "court"    # rent a whole court, priced per court
SOCIAL_CATEGORY = "social"  # buy a ticket to a session, priced per person ("xé vé")
CATEGORIES = ("all", COURT_CATEGORY, SOCIAL_CATEGORY)

AVAILABILITY_ANY = "any"    # keep partly-free courts too, priced for their open part
AVAILABILITY_FREE = "free"  # only courts free for the whole window
AVAILABILITIES = (AVAILABILITY_ANY, AVAILABILITY_FREE)


def parse_category(value: str | None) -> str:
    """Normalise a category name; raises ValueError for anything unknown."""
    category = (value or "all").strip().lower()
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {value!r}; expected one of {', '.join(CATEGORIES)}")
    return category


def parse_availability(value: str | None) -> str:
    """Normalise an availability mode; raises ValueError for anything unknown."""
    mode = (value or AVAILABILITY_ANY).strip().lower()
    if mode not in AVAILABILITIES:
        raise ValueError(
            f"unknown availability {value!r}; expected one of {', '.join(AVAILABILITIES)}"
        )
    return mode


@dataclass(slots=True)
class FindQuery:
    place: str | None = None
    preset: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = 3.0
    day: dt.date = field(default_factory=dt.date.today)
    start_minute: int = 18 * 60
    end_minute: int = 21 * 60
    sport: str = "pickleball"
    max_branches: int = 25
    category: str = "all"
    availability: str = AVAILABILITY_ANY
    target: str | None = None

    @property
    def wants_courts(self) -> bool:
        return self.category in ("all", COURT_CATEGORY)

    @property
    def wants_social(self) -> bool:
        return self.category in ("all", SOCIAL_CATEGORY)

    @property
    def free_only(self) -> bool:
        """True when booked courts should be dropped from the results."""
        return self.availability == AVAILABILITY_FREE


@dataclass(slots=True)
class BranchResult:
    branch: Branch
    options: list[CourtOption] = field(default_factory=list)
    sessions: list[SocialSession] = field(default_factory=list)
    distance_km: float | None = None
    error: str | None = None


@dataclass(slots=True)
class FindResult:
    query: FindQuery
    sport_name: str
    branches_scanned: int = 0
    results: list[BranchResult] = field(default_factory=list)
    generated_at: dt.datetime = field(default_factory=dt.datetime.now)

    @property
    def ranked(self) -> list[CourtOption]:
        """Every priced court: most hours available first, then cheapest rate.

        One row per court, not per venue: courts inside one branch are separate
        bookings with their own hours and their own availability, so collapsing a
        venue to its cheapest court would hide one that is free when the cheap one
        is taken.

        Ranked by how much of the window the court can actually sell (more is
        better), then by the hourly rate, and never by the total: a partly-free
        court is quoted for fewer hours, so its total is smaller simply because it
        sells less time — a one-hour slot at 200.000đ would outrank a six-hour
        court at 120.000đ/h on total alone, which is backwards. Ties go to the
        nearer branch.
        """
        options = [opt for res in self.results for opt in res.options]
        options.sort(key=lambda o: (
            -o.hours,
            o.hourly_price,
            o.distance_km if o.distance_km is not None else 1e9,
            o.branch.name,
            o.core.name,
        ))
        return options

    @property
    def ranked_social(self) -> list[SocialSession]:
        """Every ticket on sale: nearest first, then cheapest, then fullest window.

        One row per ticket, like :attr:`ranked`: sessions at one venue differ in
        time and price, so each is its own row.

        Distance leads, because travelling to the venue is what decides whether a
        ticket is usable at all; a ticket whose branch has no known distance (a
        text-place search) sorts with the far ones. Price breaks distance ties
        (cheapest first), and the last key prefers the session that covers most of
        the requested window: a ticket running the whole window answers the search
        more fully than one covering an hour of it.
        """
        sessions = [session for res in self.results for session in res.sessions]
        start, end = window_bounds(self.query.day, self.query.start_minute, self.query.end_minute)
        sessions.sort(key=lambda s: (
            s.distance_km if s.distance_km is not None else 1e9,
            s.ticket_price,
            -_session_window_minutes(s, start, end),
            s.start.replace(tzinfo=None) if s.start else dt.datetime.max,
        ))
        return sessions


def normalize(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics for lenient matching."""
    decomposed = unicodedata.normalize("NFD", text or "")
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    spaced = without_marks.replace("đ", "d").replace("Đ", "d").lower()
    return " ".join(_TOKEN_RE.sub(" ", spaced).split())


def place_score(haystack: str, place: str) -> int:
    """How many of *place*'s tokens appear in *haystack* (0 = no match)."""
    hay = normalize(haystack)
    tokens = [t for t in normalize(place).split() if len(t) >= 2]
    if not tokens:
        return 0
    return sum(1 for token in tokens if token in hay)


def presets_config(cfg: dict) -> dict[str, tuple[float, float]]:
    """Saved places from ``config.search.presets`` as name -> (latitude, longitude)."""
    raw = cfg["search"].get("presets") or {}
    if not isinstance(raw, dict):
        return {}
    presets: dict[str, tuple[float, float]] = {}
    for key, value in raw.items():
        try:
            presets[str(key)] = (float(value["lat"]), float(value["lng"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"config: preset {key!r} needs numeric lat and lng") from exc
    return presets


def match_preset(cfg: dict, name: str) -> tuple[str, float, float] | None:
    """(name, latitude, longitude) for a saved place, or None when *name* is not one.

    The lookup is case-insensitive and whitespace-tolerant, so a name typed into
    the area field ("mulberry") finds the place declared as "Mulberry".
    """
    wanted = name.strip().casefold()
    for key, (latitude, longitude) in presets_config(cfg).items():
        if key.strip().casefold() == wanted:
            return key, latitude, longitude
    return None


def build_query(
    cfg: dict,
    *,
    place: str | None = None,
    preset: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float | None = None,
    day: dt.date | None = None,
    start_minute: int | None = None,
    end_minute: int | None = None,
    sport: str | None = None,
    max_branches: int | None = None,
    category: str | None = None,
    availability: str | None = None,
    target: str | None = None,
) -> FindQuery:
    """Merge CLI overrides with config defaults into a :class:`FindQuery`.

    A *place* — or an explicit *preset* — that names a saved place in
    ``config.search.presets`` searches around that spot instead of the text, so
    the area field doubles as the picker. Explicit coordinates win over a saved
    pair, since they are the operator's own pick.
    """
    search = cfg["search"]
    if place is not None and not place.strip():
        # The page's Area dropdown submits its blank "no area" entry as an empty
        # string; treat that as no area (the coordinates, else the default), not as
        # a text match against nothing.
        place = None
    saved = match_preset(cfg, preset) if preset else None
    if preset and saved is None:
        known = ", ".join(presets_config(cfg)) or "none configured"
        raise ValueError(f"unknown preset {preset!r}; known presets: {known}")
    if saved is None and place:
        saved = match_preset(cfg, place)
    if saved is not None:
        preset, saved_lat, saved_lng = saved
        if latitude is None and longitude is None:
            latitude, longitude = saved_lat, saved_lng
            place = None
    return FindQuery(
        place=place if place is not None else (None if latitude is not None else search["default_place"]),
        preset=preset or None,
        latitude=latitude,
        longitude=longitude,
        radius_km=float(radius_km if radius_km is not None else search["radius_km"]),
        day=day or dt.date.today(),
        start_minute=start_minute if start_minute is not None else 18 * 60,
        end_minute=end_minute if end_minute is not None else 21 * 60,
        sport=sport or search["sport"],
        max_branches=int(max_branches if max_branches is not None else search["max_branches"]),
        category=parse_category(category if category is not None else search.get("category")),
        availability=parse_availability(
            availability if availability is not None else search.get("availability")
        ),
        target=(target if target is not None else search.get("target")) or None,
    )


def find_cheapest(cfg: dict, query: FindQuery, client: AloboClient | None = None) -> FindResult:
    """Run the full search and return a ranked :class:`FindResult`."""
    client = client or AloboClient(cfg)

    sports = client.sport_types()
    sport = next((s for s in sports if s.key == query.sport), None)
    if sport is None:
        known = ", ".join(s.key for s in sports)
        raise ValueError(f"unknown sport {query.sport!r}; known sports: {known}")

    branches = client.all_branches(page_size=int(cfg["search"]["page_size"]))
    candidates = _shortlist(branches, query, sport.int_value)

    result = FindResult(query=query, sport_name=sport.name, branches_scanned=len(candidates))
    if query.wants_courts and candidates:
        # Each branch costs two API calls; fetch several at once so the page stays
        # responsive. Order is preserved by mapping over the original candidate list.
        workers = min(int(cfg["search"].get("workers", 6)) or 6, len(candidates))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            result.results = list(
                pool.map(lambda b: _price_branch(client, b, query, sport.int_value), candidates)
            )
    else:
        result.results = [
            BranchResult(branch=branch, distance_km=_branch_distance(query, branch))
            for branch in candidates
        ]

    if query.target and query.wants_courts:
        _require_target(result, query.target)
    if query.wants_social:
        _attach_sessions(client, result, query, sport.int_value)
    for res in result.results:
        # A court with no sellable time left is not a result: taken end to end, or
        # open only at an hour the branch publishes no rate for. There is no price
        # to quote for it.
        res.options = [
            opt for opt in res.options if opt.available != BOOKED and opt.total_price > 0
        ]
        if query.free_only:
            # Not "hide the booked ones" (that is implied now) but "only courts
            # free for the window you asked for": a partly-free court is priced
            # for its open part, which is not the window you asked about.
            res.options = [opt for opt in res.options if opt.available != PARTIAL]
    return result


def _sport_branches(branches: list[Branch], query: FindQuery, sport_value: int) -> list[Branch]:
    """Branches that play the searched sport — by declared type, else by name.

    ``branch.type`` is the sport the venue is listed under; when no branch
    declares it (some payloads leave it off), fall back to its name so a
    name-only match still works. Locked/removed venues are dropped here: the
    branch list keeps them (so the app can show a "site closed" page), but their
    courts are no longer for sale, so they are not results to price.
    """
    sporty = [b for b in branches if not b.is_locked and b.sport_type == sport_value]
    if not sporty:
        sporty = [b for b in branches if not b.is_locked and query.sport in normalize(b.name)]
    return sporty


def _narrow_by_location(branches: list[Branch], query: FindQuery) -> list[Branch]:
    """The searched branches that sit in the search area, best match first.

    Coordinates: within ``radius_km`` of the origin, nearest first. A text place:
    branches whose name/address matches, best match first.

    Deliberately uncapped. Pricing a court costs a few API calls *per branch*, so
    the court shortlist caps itself; tickets arrive in one call for every branch
    at once, so there is no per-branch cost to bound and capping here — as
    :func:`_shortlist` does — would hide a venue that sells tickets but sits past
    the court cap.
    """
    if query.latitude is not None and query.longitude is not None:
        scored: list[tuple[float, Branch]] = []
        for branch in branches:
            distance = _branch_distance(query, branch)
            if distance is not None and distance <= query.radius_km:
                scored.append((distance, branch))
        scored.sort(key=lambda pair: pair[0])
        return [branch for _, branch in scored]

    place = query.place or ""
    ranked: list[tuple[int, str, Branch]] = []
    for branch in branches:
        score = place_score(f"{branch.name} {branch.address}", place)
        if score > 0:
            ranked.append((-score, normalize(branch.name), branch))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [branch for _, _, branch in ranked]


def _shortlist(branches: list[Branch], query: FindQuery, sport_value: int) -> list[Branch]:
    """The branches to price: sport-matched, in the search area, capped for cost."""
    located = _narrow_by_location(_sport_branches(branches, query, sport_value), query)
    return located[: query.max_branches]


def _area_sports(areas: list[dict]) -> dict[str, int]:
    """Map each area id to the sport int it declares (areas without one are dropped)."""
    out: dict[str, int] = {}
    for area in areas or []:
        try:
            sport = int(area.get("yardType"))
        except (TypeError, ValueError):
            continue
        if sport > 0 and area.get("id"):
            out[str(area["id"])] = sport
    return out


def _core_sport(core: Core, area_sports: dict[str, int], default: int) -> int:
    """The sport a court plays.

    A core's own ``yardType`` is widely unreliable — the API sends -1 or omits it
    for most branches even though the courts are real — so fall back to the sport
    of the area the court sits in (this is what keeps a multi-sport branch's
    football courts out of a pickleball search), and finally to the sport being
    searched for, since the branch itself already matched it.
    """
    if core.sport_type is not None and core.sport_type > 0:
        return core.sport_type
    area_sport = area_sports.get(core.area_id)
    return area_sport if area_sport else default


def _branch_distance(query: FindQuery, branch: Branch) -> float | None:
    """Distance from the search origin, when both the query and the branch have coordinates."""
    if query.latitude is None or query.longitude is None:
        return None
    if branch.latitude is None or branch.longitude is None:
        return None
    return haversine_km(query.latitude, query.longitude, branch.latitude, branch.longitude)


def _price_branch(client: AloboClient, branch: Branch, query: FindQuery, sport_value: int) -> BranchResult:
    """Fetch one branch's courts + prices and build its priced options."""
    res = BranchResult(branch=branch, distance_km=_branch_distance(query, branch))
    try:
        cores, areas = client.get_cores(branch.id)
        core_types = {ct.id: ct for ct in client.get_core_types(branch.id)}
    except Exception as exc:  # noqa: BLE001 - one bad branch must not kill the run
        res.error = f"{type(exc).__name__}: {exc}"
        return res

    window = bookable_window(branch, query.start_minute, query.end_minute)
    if window is None:
        return res  # the venue is shut for the whole window, so nothing is bookable
    start_minute, end_minute = window
    hours = hours_between(start_minute, end_minute)
    weekday = query.day.isoweekday()
    area_sports = _area_sports(areas)
    chosen: dict[str, PriceTarget | None] = {}  # courts share a type, so resolve each once
    for core in cores:
        if _core_sport(core, area_sports, sport_value) != sport_value:
            continue
        core_type = core_types.get(core.setting)
        if core_type is None:
            continue
        if core_type.id not in chosen:
            chosen[core_type.id] = select_target(core_type, query.target)
        target = chosen[core_type.id]
        price = window_cost(core_type, start_minute, end_minute, weekday, target)
        if price <= 0:
            continue
        res.options.append(
            CourtOption(
                branch=branch,
                core=core,
                core_type=core_type,
                total_price=price,
                hours=hours,
                distance_km=res.distance_km,
                target_id=target.id if target else "",
                target_name=target.name if target else "",
            )
        )
    res.options.sort(key=lambda o: o.total_price)
    if res.options:
        _mark_availability(client, res, query)
    return res


def select_target(core_type: CoreType, wanted: str | None) -> PriceTarget | None:
    """The tariff to price *core_type* under, or None for its generic table.

    *wanted* matches a tariff id or name, diacritic-insensitively; a type that
    does not offer it falls back to its default, so one ``--target`` covers the
    whole shortlist. Only visible tariffs are selectable, exactly as the app
    hides the rest.
    """
    if wanted:
        key = normalize(wanted)
        for target in core_type.visible_targets():
            if key in (normalize(target.id), normalize(target.name)):
                return target
    return core_type.default_target()


def bookable_window(
    branch: Branch,
    start_minute: int,
    end_minute: int,
) -> tuple[int, int] | None:
    """The part of a requested window the venue actually sells, or None if no part.

    Both the price and the availability verdict are computed over this, so the
    hours after closing are neither charged nor reported free. A branch that does
    not publish its working hours keeps the window as asked.
    """
    hours = branch.bookable
    if hours is None:
        return start_minute, end_minute
    return clip_to_hours(start_minute, end_minute, hours)


def _require_target(result: FindResult, wanted: str) -> None:
    """Raise when no priced branch offers *wanted*, instead of quietly defaulting."""
    key = normalize(wanted)
    offered: list[str] = []
    for res in result.results:
        for opt in res.options:
            if opt.target_id and opt.target_id not in offered:
                offered.append(opt.target_id)
            if key in (normalize(opt.target_id), normalize(opt.target_name)):
                return
    known = ", ".join(offered) if offered else "none (no court was priced)"
    raise ValueError(f"unknown tariff {wanted!r}; tariffs offered here: {known}")


def _mark_availability(client: AloboClient, res: BranchResult, query: FindQuery) -> None:
    """Tag each of a branch's priced courts free/booked/partial for the window.

    The window is the venue's bookable part of the request (see
    :func:`bookable_window`), so an open span never runs past closing time. The
    lookup is public and never fatal: a branch whose bookings cannot be read (a
    date outside its booking window, a transient failure) leaves its courts
    marked ``unknown`` rather than dropping them.
    """
    window = bookable_window(res.branch, query.start_minute, query.end_minute)
    if window is None:
        return
    start, end = window_bounds(query.day, *window)
    days = [start.date()]
    if end.date() != start.date():  # a window that crosses midnight needs both days
        days.append(end.date())

    bookings: list[Booking] = []
    try:
        for day in days:
            bookings.extend(client.get_onetime_bookings(res.branch.id, day))
    except Exception:  # noqa: BLE001 - availability is a bonus, never fatal
        for opt in res.options:
            opt.available = UNKNOWN
        return

    for opt in res.options:
        opt.available, opt.free_spans = court_availability(bookings, opt.core.id, start, end)
        if opt.available == PARTIAL:
            _reprice_for_open_time(opt, query, start)


def _reprice_for_open_time(opt: CourtOption, query: FindQuery, window_start: dt.datetime) -> None:
    """Quote a partly-free court for the time it is actually free and on sale.

    Only the open part of a partial window can be booked, so charging the whole
    window would quote hours somebody else already has. Disjoint open spans are
    summed: the app's grid lets you select separate slots. Hours the tariff does
    not price are trimmed off rather than offered at nothing, which can leave the
    court with no open time and no price.
    """
    target = select_target(opt.core_type, opt.target_id or None)
    table = target if target is not None else opt.core_type
    weekday = query.day.isoweekday()
    day_start = dt.datetime.combine(window_start.date(), dt.time())
    open_spans: list[tuple[dt.datetime, dt.datetime]] = []
    total = 0.0
    minutes = 0.0
    for start, end in opt.free_spans:
        from_minute = _minutes_from(day_start, start)
        to_minute = _minutes_from(day_start, end)
        for low, high in _sellable_minutes(table, from_minute, to_minute, weekday):
            total += window_cost(opt.core_type, low, high, weekday, target)
            minutes += high - low
            open_spans.append((day_start + dt.timedelta(minutes=low),
                               day_start + dt.timedelta(minutes=high)))
    opt.free_spans = open_spans
    opt.total_price = total
    opt.hours = minutes / 60.0


def _minutes_from(day_start: dt.datetime, moment: dt.datetime) -> int:
    """*moment* as minutes from the window's own midnight (past 1440 if after it)."""
    return int((moment - day_start).total_seconds() // 60)


def _sellable_minutes(
    table: PriceTable,
    start_minute: int,
    end_minute: int,
    weekday: int,
) -> list[tuple[int, int]]:
    """The minute ranges in ``[start, end)`` the *table* puts a price on.

    Rates are hourly, so the span is walked a hour at a time and the runs that
    price at something are kept — a venue that stops publishing rates at 22:00 is
    not selling the 22:00-23:00 hour, whatever its stated closing time.
    """
    runs: list[tuple[int, int]] = []
    cursor = start_minute
    run_from: int | None = None
    while cursor < end_minute:
        step = min(end_minute, cursor + 60)
        if table.price_per_unit(cursor % MINUTES_PER_DAY, weekday) > 0:
            run_from = cursor if run_from is None else run_from
        elif run_from is not None:
            runs.append((run_from, cursor))
            run_from = None
        cursor = step
    if run_from is not None:
        runs.append((run_from, end_minute))
    return runs


def _attach_sessions(client: AloboClient, result: FindResult, query: FindQuery, sport_value: int) -> None:
    """Attach social/open-play tickets for the day to every branch selling them.

    The candidate set is the branch list the ticket endpoint returns, not the
    court shortlist: that shortlist is capped for pricing cost and would drop a
    venue whose tickets are on sale just because it sits past the cap. The
    endpoint itself ignores its ``types`` argument (it answers with every branch
    holding any booking, in every sport and province), so the sport is read from
    each ticket — see :func:`_session_sport` — and the search area is applied
    here.
    """
    start, end = window_bounds(query.day, query.start_minute, query.end_minute)
    day_start = dt.datetime.combine(query.day, dt.time())
    try:
        pairs = client.branch_booking_search(
            day_start.isoformat(timespec="milliseconds") + "Z",
            (day_start + dt.timedelta(days=1)).isoformat(timespec="milliseconds") + "Z",
            types=[sport_value],
        )
    except Exception:  # noqa: BLE001 - sessions are a bonus, never fatal
        return
    branches: dict[str, Branch] = {}
    tickets: dict[str, list[SocialSession]] = {}
    for branch, sessions in pairs:
        if branch.is_locked:  # a withdrawn venue's tickets are no longer on sale
            continue
        kept = [
            session
            for session in sessions
            if _session_sport(session, branch, sport_value) and _session_in_window(session, start, end)
        ]
        if kept:
            branches[branch.id] = branch
            tickets[branch.id] = kept
    by_id = {res.branch.id: res for res in result.results}
    for branch in _narrow_by_location(list(branches.values()), query):
        res = by_id.get(branch.id)
        if res is None:  # a venue that sells tickets but was not priced for courts
            res = BranchResult(branch=branch, distance_km=_branch_distance(query, branch))
            by_id[branch.id] = res
            result.results.append(res)
        for session in tickets[branch.id]:
            session.branch = res.branch
            session.distance_km = res.distance_km
        res.sessions = tickets[branch.id]


def _session_sport(session: SocialSession, branch: Branch, sport_value: int) -> bool:
    """Whether a ticket is for the sport being searched.

    Each booking names its own ``sportType``, which is the precise signal (a
    branch listed under one sport can still ticket another); the branch's type is
    the fallback for a payload that omits it.
    """
    if session.sport_type is not None:
        return session.sport_type == sport_value
    return branch.sport_type == sport_value


def _session_in_window(session: SocialSession, start: dt.datetime, end: dt.datetime) -> bool:
    if session.start is None:
        return True
    session_start = session.start.replace(tzinfo=None)
    return start <= session_start < end


def _session_window_minutes(
    session: SocialSession,
    start: dt.datetime,
    end: dt.datetime,
) -> float:
    """How much of a ticket's session falls inside the requested window, in minutes.

    A ticket buys the whole session, so the window is the yardstick: a session
    running the whole window covers more of it than one covering a single hour.
    The session's own clock is naive-by-comparison here — the window is built from
    the query's day — so any timezone the payload carried is stripped first.
    Sessions with no start time cannot be measured and score zero.
    """
    if session.start is None:
        return 0.0
    session_start = session.start.replace(tzinfo=None)
    session_end = session_start + dt.timedelta(minutes=session.duration_min)
    overlap = min(session_end, end) - max(session_start, start)
    return max(overlap.total_seconds() / 60.0, 0.0)

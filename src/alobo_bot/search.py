"""Tie the API calls together: pick candidate branches, price their courts, rank.

The flow for one ``find``:

1. Resolve the requested sport to its int value (pickleball == 5).
2. Gather every branch, keep those whose sport matches, then narrow by location
   — either a radius around ``--lat/--lng`` or a text match on ``--place``.
3. For each surviving branch, fetch its courts and price table and compute the
   cost of the requested window for every court that is actually that sport.
4. Also collect social/open-play sessions on the day (sold per ticket) as an
   alternative "option". Sessions are per person, so they are reported apart
   from court rental rather than mixed into the same ranking.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .api import AloboClient
from .models import Branch, CourtOption, SocialSession
from .pricing import haversine_km, hours_between, window_bounds, window_cost

_TOKEN_RE = re.compile(r"[^a-z0-9 ]+")


@dataclass(slots=True)
class FindQuery:
    place: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = 10.0
    day: dt.date = field(default_factory=dt.date.today)
    start_minute: int = 18 * 60
    end_minute: int = 21 * 60
    sport: str = "pickleball"
    max_branches: int = 25


@dataclass(slots=True)
class BranchResult:
    branch: Branch
    options: list[CourtOption] = field(default_factory=list)
    sessions: list[SocialSession] = field(default_factory=list)
    distance_km: float | None = None
    error: str | None = None

    @property
    def cheapest(self) -> CourtOption | None:
        return min(self.options, key=lambda o: o.total_price) if self.options else None


@dataclass(slots=True)
class FindResult:
    query: FindQuery
    sport_name: str
    branches_scanned: int = 0
    results: list[BranchResult] = field(default_factory=list)
    generated_at: dt.datetime = field(default_factory=dt.datetime.now)

    @property
    def ranked(self) -> list[CourtOption]:
        """Every court option across branches, cheapest first."""
        options = [opt for res in self.results for opt in res.options]
        options.sort(key=lambda o: (o.total_price, o.distance_km if o.distance_km is not None else 1e9))
        return options


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


def build_query(
    cfg: dict,
    *,
    place: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float | None = None,
    day: dt.date | None = None,
    start_minute: int | None = None,
    end_minute: int | None = None,
    sport: str | None = None,
    max_branches: int | None = None,
) -> FindQuery:
    """Merge CLI overrides with config defaults into a :class:`FindQuery`."""
    search = cfg["search"]
    return FindQuery(
        place=place if place is not None else (None if latitude is not None else search["default_place"]),
        latitude=latitude,
        longitude=longitude,
        radius_km=float(radius_km if radius_km is not None else search["radius_km"]),
        day=day or dt.date.today(),
        start_minute=start_minute if start_minute is not None else 18 * 60,
        end_minute=end_minute if end_minute is not None else 21 * 60,
        sport=sport or search["sport"],
        max_branches=int(max_branches if max_branches is not None else search["max_branches"]),
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
    if candidates:
        # Each branch costs two API calls; fetch several at once so the page stays
        # responsive. Order is preserved by mapping over the original candidate list.
        workers = min(int(cfg["search"].get("workers", 6)) or 6, len(candidates))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            result.results = list(
                pool.map(lambda b: _price_branch(client, b, query, sport.int_value), candidates)
            )

    _attach_sessions(client, result, query, sport.int_value)
    return result


def _shortlist(branches: list[Branch], query: FindQuery, sport_value: int) -> list[Branch]:
    """Sport-match, then narrow by distance or place text, then cap."""
    sporty = [b for b in branches if b.sport_type == sport_value]
    if not sporty:
        sporty = [b for b in branches if query.sport in normalize(b.name)]

    if query.latitude is not None and query.longitude is not None:
        scored: list[tuple[float, Branch]] = []
        for branch in sporty:
            if branch.latitude is None or branch.longitude is None:
                continue
            distance = haversine_km(query.latitude, query.longitude, branch.latitude, branch.longitude)
            if distance <= query.radius_km:
                scored.append((distance, branch))
        scored.sort(key=lambda pair: pair[0])
        return [branch for _, branch in scored[: query.max_branches]]

    place = query.place or ""
    ranked: list[tuple[int, str, Branch]] = []
    for branch in sporty:
        score = place_score(f"{branch.name} {branch.address}", place)
        if score > 0:
            ranked.append((-score, normalize(branch.name), branch))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [branch for _, _, branch in ranked[: query.max_branches]]


def _price_branch(client: AloboClient, branch: Branch, query: FindQuery, sport_value: int) -> BranchResult:
    """Fetch one branch's courts + prices and build its priced options."""
    res = BranchResult(branch=branch)
    if query.latitude is not None and branch.latitude is not None and branch.longitude is not None:
        res.distance_km = haversine_km(query.latitude, query.longitude, branch.latitude, branch.longitude)
    try:
        cores, _areas = client.get_cores(branch.id)
        core_types = {ct.id: ct for ct in client.get_core_types(branch.id)}
    except Exception as exc:  # noqa: BLE001 - one bad branch must not kill the run
        res.error = f"{type(exc).__name__}: {exc}"
        return res

    hours = hours_between(query.start_minute, query.end_minute)
    weekday = query.day.isoweekday()
    for core in cores:
        if core.sport_type != sport_value:
            continue
        core_type = core_types.get(core.setting)
        if core_type is None:
            continue
        price = window_cost(core_type, query.start_minute, query.end_minute, weekday)
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
            )
        )
    res.options.sort(key=lambda o: o.total_price)
    return res


def _attach_sessions(client: AloboClient, result: FindResult, query: FindQuery, sport_value: int) -> None:
    """Attach social/open-play sessions for the day to the matching branches."""
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
    by_id = {res.branch.id: res for res in result.results}
    for branch, sessions in pairs:
        res = by_id.get(branch.id)
        if res is None:
            continue
        res.sessions = [s for s in sessions if _session_in_window(s, start, end)]


def _session_in_window(session: SocialSession, start: dt.datetime, end: dt.datetime) -> bool:
    if session.start is None:
        return True
    session_start = session.start.replace(tzinfo=None)
    return start <= session_start < end

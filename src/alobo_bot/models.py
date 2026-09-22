"""Dataclasses for the AloBooking API payloads this bot consumes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

MINUTES_PER_DAY = 24 * 60


@dataclass(slots=True)
class SportType:
    key: str
    name: str
    int_value: int
    priority: int = 0


@dataclass(slots=True)
class Branch:
    id: str
    name: str
    address: str
    sport_type: int | None
    latitude: float | None
    longitude: float | None
    province_id: str | None = None
    district_id: str | None = None
    ward_id: str | None = None
    phone: str = ""
    booking_types: list[str] = field(default_factory=list)
    open_hour: float | None = None
    close_hour: float | None = None

    @property
    def bookable(self) -> tuple[int, int] | None:
        """The minutes from midnight the venue sells, or None when unstated.

        ``morningStartWorkingTime``..``afternoonEndWorkingTime`` is the working
        range a branch displays ("Giờ hoạt động"), and the app's booking grid
        renders one column per hour from the opening hour *through* the closing
        hour — so the last bookable hour starts at the close and runs one slot
        past it. La Khê, hours 05:00-22:00, offers 22:00-23:00 last; USILK 102,
        hours 06:00-24:00, ends at midnight. Clamped to the day.
        """
        if self.open_hour is None or self.close_hour is None:
            return None
        start = min(max(round(self.open_hour * 60), 0), MINUTES_PER_DAY)
        end = min(max(round((self.close_hour + 1) * 60), 0), MINUTES_PER_DAY)
        return (start, end) if end > start else None

    @classmethod
    def from_api(cls, data: dict) -> "Branch":
        """Build from either branchInformation (search) or get_branch payloads."""
        location = data.get("location") or {}
        lat = location.get("_latitude", location.get("latitude"))
        lng = location.get("_longitude", location.get("longitude"))
        sport = data.get("type")
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            address=data.get("address") or "",
            sport_type=int(sport) if sport is not None else None,
            latitude=float(lat) if lat not in (None, "") else None,
            longitude=float(lng) if lng not in (None, "") else None,
            province_id=data.get("provinceId") or data.get("province_id"),
            district_id=data.get("districtId") or data.get("district_id"),
            ward_id=data.get("wardId") or data.get("ward_id"),
            phone=data.get("phone") or "",
            booking_types=list(data.get("bookingTypes") or []),
            open_hour=_number(data.get("morningStartWorkingTime")),
            close_hour=_number(data.get("afternoonEndWorkingTime")),
        )


@dataclass(slots=True)
class Core:
    """A physical court/yard inside a branch.

    ``setting`` is the id of the matching :class:`CoreType` — that is how a court
    is priced — while ``sport_type`` mirrors the branch's sport int value.
    """

    id: str
    name: str
    area_id: str = ""
    short_name: str = ""
    setting: str = ""
    sport_type: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Core":
        yard = data.get("yardType")
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            area_id=data.get("areaId") or "",
            short_name=data.get("shortName") or "",
            setting=data.get("setting") or "",
            sport_type=int(yard) if yard is not None else None,
        )


@dataclass(slots=True)
class SpecialPrice:
    """A discounted price for a recurring time-of-day window (e.g. 5:30-10:00)."""

    start_minute: int
    end_minute: int
    price: float
    price_one_time: float
    min_duration: int = 30
    unit: str = "h"
    date_range_week: str = "1-7"

    @property
    def label(self) -> str:
        return f"{_hhmm(self.start_minute)}-{_hhmm(self.end_minute)}"

    def contains(self, start_minute: int, weekday: int) -> bool:
        """True when this window covers *start_minute* on *weekday* (1=Mon)."""
        if not _weekday_matches(self.date_range_week, weekday):
            return False
        minute = start_minute
        if minute < self.start_minute and self.end_minute > 24 * 60:
            minute += 24 * 60  # a window that wrapped midnight (e.g. 22:00-02:00)
        return self.start_minute <= minute < self.end_minute

    @classmethod
    def from_api(cls, data: dict) -> "SpecialPrice | None":
        window = _parse_window(data.get("time") or "")
        if window is None:
            return None
        start, end = window
        price = data.get("price")
        price_one = data.get("priceOneTime", price)
        if price is None and price_one is None:
            return None
        return cls(
            start_minute=start,
            end_minute=end,
            price=float(price if price is not None else price_one),
            price_one_time=float(price_one if price_one is not None else price),
            min_duration=int(data.get("minDuration") or 30),
            unit=data.get("unit") or "h",
            date_range_week=str(data.get("dateRangeWeek") or "1-7"),
        )


@dataclass(slots=True)
class PriceTable:
    """An hourly price table: a base rate plus time-of-day override windows."""

    normal_price: float
    normal_price_one_time: float
    min_duration: int = 30
    unit: str = "h"
    special_prices: list[SpecialPrice] = field(default_factory=list)

    def price_per_unit(self, start_minute: int, weekday: int) -> float:
        """One-time price per unit at *start_minute* on *weekday* (1=Mon).

        A matching ``specialPrice`` window overrides the base price; when several
        windows overlap, the first listed wins (the app treats the list as an
        ordered override table, not a set of discounts).
        """
        for special in self.special_prices:
            if special.contains(start_minute, weekday):
                return special.price_one_time
        return self.normal_price_one_time

    @staticmethod
    def _specials(data: dict) -> list[SpecialPrice]:
        parsed = (SpecialPrice.from_api(item) for item in (data.get("specialPrice") or []))
        return [special for special in parsed if special is not None]


@dataclass(slots=True)
class PriceTarget(PriceTable):
    """A named tariff a court type can be rented under (``targets`` in the API).

    ``get_core_types`` prices a type twice over: a generic ``normalPrice`` on the
    type itself, and one table per *tariff* under ``targets`` — "đối tượng áp
    dụng" in the app, which makes the booker pick one. The type-level price is a
    placeholder in most branches; the real time-of-day table lives here.
    """

    id: str = ""
    name: str = ""
    priority: int = 0
    hide: bool = False
    show_default: bool = False

    @classmethod
    def from_api(cls, target_id: str, data: dict) -> "PriceTarget":
        price = data.get("price")
        one_time = data.get("priceOneTime")
        if price is None:
            price = one_time
        if one_time is None:
            one_time = price
        return cls(
            normal_price=float(price or 0),
            normal_price_one_time=float(one_time or 0),
            min_duration=int(data.get("minDuration") or 30),
            unit=data.get("unit") or "h",
            special_prices=cls._specials(data),
            id=str(target_id),
            name=data.get("name") or "",
            priority=int(data.get("priority") or 0),
            hide=bool(data.get("hide")),
            show_default=bool(data.get("showDefault")),
        )


# The generic customer tariff branches name their ordinary price list. Preferred
# over whatever a branch happens to list first, which is often a niche product.
PREFERRED_TARGET_IDS = ("kh", "default")


@dataclass(slots=True)
class CoreType(PriceTable):
    """A bookable court category with its base pricing and per-tariff tables."""

    id: str = ""
    name: str = ""
    targets: dict[str, PriceTarget] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict) -> "CoreType":
        price = float(data.get("normalPrice") or 0)
        one_time = float(data.get("normalPriceOneTime") or 0)
        if one_time == 0:  # the app falls back to the base rate, and so do we
            one_time = price
        return cls(
            normal_price=price,
            normal_price_one_time=one_time,
            min_duration=int(data.get("minDuration") or 30),
            unit=data.get("unit") or "h",
            special_prices=cls._specials(data),
            id=data.get("id") or "",
            name=data.get("name") or "",
            targets={
                str(key): PriceTarget.from_api(key, value)
                for key, value in (data.get("targets") or {}).items()
                if isinstance(value, dict)
            },
        )

    def ordered_targets(self) -> list[PriceTarget]:
        """Every tariff in the order the app lists them: priority, then API order."""
        return sorted(self.targets.values(), key=lambda target: target.priority)

    def visible_targets(self) -> list[PriceTarget]:
        """The tariffs a booker can actually pick — the app drops ``hide`` ones."""
        return [target for target in self.ordered_targets() if not target.hide]

    def default_target(self) -> PriceTarget | None:
        """The tariff to price with when the operator has not named one.

        The app asks the booker to choose, so there is no server-side default to
        copy. Prefer the generic customer tariff (``kh``/``default``); a branch's
        first-listed entry is often a niche product — a quarterly prepay
        discount, a monthly ticket, a ball machine — and pricing an ordinary
        rental at one of those would misstate it.
        """
        visible = self.visible_targets()
        for wanted in PREFERRED_TARGET_IDS:
            for target in visible:
                if target.id == wanted:
                    return target
        return visible[0] if visible else None


@dataclass(slots=True)
class SocialSession:
    """A social/open-play event sold by the ticket (price is per person)."""

    id: str
    name: str
    start: dt.datetime | None
    duration_min: int
    ticket_price: float
    max_player: int
    current_player: int
    court_names: list[str] = field(default_factory=list)
    booking_code: str = ""
    branch: Branch | None = None  # filled in by search, so a flat ranking can name its venue
    distance_km: float | None = None  # filled in by search for coordinate searches

    @property
    def spots_left(self) -> int:
        return max(self.max_player - self.current_player, 0)

    @property
    def end(self) -> dt.datetime | None:
        """When the session finishes (``start`` plus its duration)."""
        if self.start is None:
            return None
        return self.start + dt.timedelta(minutes=self.duration_min)

    @classmethod
    def from_api(cls, data: dict) -> "SocialSession":
        services = data.get("services") or []
        start = _parse_dt(services[0].get("startTime")) if services else _parse_dt(data.get("time"))
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            start=start,
            duration_min=int(data.get("duration") or 0),
            ticket_price=float(data.get("ticketPrice") or 0),
            max_player=int(data.get("maxPlayer") or 0),
            current_player=int(data.get("currentPlayer") or 0),
            court_names=[s.get("serviceName") or "" for s in services],
            booking_code=str(data.get("bookingCode") or ""),
        )


@dataclass(slots=True)
class Booking:
    """One court already taken for a stretch of time — a leg of a live booking.

    ``get_onetime_bookings`` returns the bookings a branch has *already* taken,
    each naming the courts it holds in ``services``; a group booking can hold
    several courts at once, so one API booking becomes one leg per court. A court
    is free for a window exactly when no leg overlaps it.
    """

    id: str
    core_id: str
    start: dt.datetime | None
    duration_min: int

    @property
    def end(self) -> dt.datetime | None:
        """When the booking releases the court (``start`` plus its duration)."""
        if self.start is None:
            return None
        return self.start + dt.timedelta(minutes=self.duration_min)

    def overlap(self, start: dt.datetime, end: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
        """The part of ``[start, end)`` this leg occupies, or None when it misses it."""
        if self.start is None:
            return None
        finish = self.end or self.start
        low, high = max(self.start, start), min(finish, end)
        return (low, high) if low < high else None

    @classmethod
    def from_api(cls, data: dict) -> list["Booking"]:
        """One :class:`Booking` per court named in the payload's ``services``."""
        booking_id = data.get("id") or ""
        fallback_start = _parse_dt(data.get("time"))
        fallback_duration = int(data.get("duration") or 0)
        legs: list[Booking] = []
        for service in data.get("services") or []:
            core_id = service.get("serviceId") or ""
            if not core_id:
                continue
            legs.append(
                cls(
                    id=booking_id,
                    core_id=core_id,
                    start=_parse_dt(service.get("startTime")) or fallback_start,
                    duration_min=int(service.get("duration") or fallback_duration),
                )
            )
        return legs


@dataclass(slots=True)
class CourtOption:
    """One court in one branch, priced for the requested window.

    ``available`` is the availability verdict for the window — one of
    ``free``/``partial``/``unknown`` (see :mod:`alobo_bot.availability`) — or None
    when availability was not checked; a fully booked court is not offered at all.
    ``free_spans`` carries the parts of the window that are still open, and for a
    ``partial`` court the price covers exactly those, since taken hours cannot be
    booked. The window itself is first clipped to the venue's opening hours (see
    :func:`alobo_bot.search.bookable_window`), so ``hours`` is the bookable time,
    not simply what was asked for. ``target_id``/``target_name`` name the tariff
    the price came from (empty when the court type has no tariffs and its generic
    table was used).
    """

    branch: Branch
    core: Core
    core_type: CoreType
    total_price: float
    hours: float
    distance_km: float | None = None
    available: str | None = None
    free_spans: list[tuple[dt.datetime, dt.datetime]] = field(default_factory=list)
    target_id: str = ""
    target_name: str = ""

    @property
    def hourly_price(self) -> float:
        return self.total_price / self.hours if self.hours else self.total_price

    @property
    def booking_url(self) -> str:
        # The web app only resolves branch deep links under "/san/"; a bare
        # "/<branchId>" falls through to the home route. See the app's own share
        # builder, which emits f"{base}/san/{branchId}".
        return f"https://datlich.alobo.vn/san/{self.branch.id}"


def _number(value: object) -> float | None:
    """A JSON number (or numeric string) as a float; None when absent or unusable."""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_window(text: str) -> tuple[int, int] | None:
    """Parse "5:30-10:00" / "17:00-24:00" into (start_minute, end_minute)."""
    if "-" not in text:
        return None
    start_text, _, end_text = text.partition("-")
    start = _to_minute(start_text)
    end = _to_minute(end_text)
    if start is None or end is None:
        return None
    if end < start:  # window wraps past midnight (e.g. 22:00-02:00)
        end += 24 * 60
    return start, end


def _to_minute(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    hour_text, _, minute_text = text.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except ValueError:
        return None
    return hour * 60 + minute


def _hhmm(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_dt(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _weekday_matches(date_range_week: str, weekday: int) -> bool:
    """dateRangeWeek is "1-7" (all days) or a list like "1,3,5" (Mon/Wed/Fri)."""
    text = (date_range_week or "").strip()
    if not text or text == "1-7":
        return True
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            try:
                if int(low) <= weekday <= int(high):
                    return True
            except ValueError:
                continue
        elif part.isdigit() and int(part) == weekday:
            return True
    return False

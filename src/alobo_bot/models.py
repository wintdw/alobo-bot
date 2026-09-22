"""Dataclasses for the AloBooking API payloads this bot consumes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


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
class CoreType:
    """A bookable court category with its base and time-of-day pricing."""

    id: str
    name: str
    normal_price: float
    normal_price_one_time: float
    min_duration: int = 30
    unit: str = "h"
    special_prices: list[SpecialPrice] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "CoreType":
        specials = [SpecialPrice.from_api(item) for item in (data.get("specialPrice") or [])]
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            normal_price=float(data.get("normalPrice") or 0),
            normal_price_one_time=float(
                data.get("normalPriceOneTime")
                if data.get("normalPriceOneTime") is not None
                else data.get("normalPrice") or 0
            ),
            min_duration=int(data.get("minDuration") or 30),
            unit=data.get("unit") or "h",
            special_prices=[s for s in specials if s is not None],
        )

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

    @property
    def spots_left(self) -> int:
        return max(self.max_player - self.current_player, 0)

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
class CourtOption:
    """One court in one branch, priced for the requested window."""

    branch: Branch
    core: Core
    core_type: CoreType
    total_price: float
    hours: float
    distance_km: float | None = None

    @property
    def hourly_price(self) -> float:
        return self.total_price / self.hours if self.hours else self.total_price

    @property
    def booking_url(self) -> str:
        return f"https://datlich.alobo.vn/{self.branch.id}"


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

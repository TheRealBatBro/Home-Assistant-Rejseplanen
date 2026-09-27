"""Async client for the Rejseplanen API 2.0.

The API is a HAFAS ReST service. It answers in XML (JSON is also offered, but the
XML schema is published and is what the real sample responses were checked
against), authenticated with a Bearer access key. Times are Danish local time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging
import math
import re
from typing import Any
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.rejseplanen.dk/api/"
NS = "{http://hacon.de/hafas/proxy/hafas-proxy}"
TZ = ZoneInfo("Europe/Copenhagen")
TIMEOUT = aiohttp.ClientTimeout(total=20)

# Product classes (bit values used by the "products" filter and the "cls" attribute).
PRODUCTS: dict[str, int] = {
    "ic": 1,
    "lyn": 2,
    "regional": 4,
    "other_train": 8,
    "s_train": 16,
    "bus": 32,
    "express_bus": 64,
    "night_bus": 128,
    "flex_bus": 256,
    "ferry": 512,
    "metro": 1024,
    "light_rail": 2048,
    "flight": 4096,
}
PRODUCT_BY_CLASS = {bit: name for name, bit in PRODUCTS.items()}

# HAFAS error codes that just mean "nothing found".
_EMPTY_ERRORS = ("SVC_NO_RESULT", "SVC_LOC", "SVC_TI", "SVC_NO_MATCH", "H890", "H9380")


class RejseplanenError(Exception):
    """Any failure talking to Rejseplanen."""


class RejseplanenAuthError(RejseplanenError):
    """The access key was rejected."""


class RejseplanenQuotaError(RejseplanenError):
    """The key's call quota is used up."""


class RejseplanenConnectionError(RejseplanenError):
    """Network trouble or timeout."""


# --------------------------------------------------------------------------- models


@dataclass(slots=True)
class Stop:
    """A stop or station."""

    id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    products: int = 0
    distance: int | None = None
    main_mast: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "latitude": self.lat,
            "longitude": self.lon,
            "products": product_names(self.products),
            "distance": self.distance,
        }


@dataclass(slots=True)
class Message:
    """A traffic message (HIM): disruption, works, replacement buses..."""

    id: str
    head: str
    text: str
    lead: str = ""
    category: str | None = None
    priority: int | None = None
    company: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    active: bool = True
    affected_stops: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "head": self.head,
            "lead": self.lead,
            "text": self.text,
            "category": self.category,
            "priority": self.priority,
            "company": self.company,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "active": self.active,
            "affected_stops": self.affected_stops,
        }


@dataclass(slots=True)
class Product:
    """What is running: IC 50141, Re 54541, bus 350S..."""

    name: str
    line: str
    category: str
    cls: int
    operator: str | None = None
    number: str | None = None

    @property
    def type(self) -> str:
        return PRODUCT_BY_CLASS.get(self.cls, "other")


@dataclass(slots=True)
class Departure:
    """One row on a departure (or arrival) board."""

    key: str
    product: Product
    stop: str
    stop_id: str
    direction: str | None
    scheduled: datetime
    realtime: datetime | None
    track: str | None
    rt_track: str | None
    cancelled: bool = False
    part_cancelled: bool = False
    reachable: bool = True
    redirected: bool = False
    uncertain_delay: bool = False
    journey_status: str = "P"
    prognosis: str | None = None
    journey_ref: str | None = None
    notes: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    arrival_at_direction: datetime | None = None
    occupancy: dict[str, Any] | None = None

    @property
    def expected(self) -> datetime:
        return self.realtime or self.scheduled

    @property
    def delay(self) -> int | None:
        """Delay in whole minutes, None when there is no realtime data."""
        if self.realtime is None:
            return None
        return round((self.realtime - self.scheduled).total_seconds() / 60)

    @property
    def platform(self) -> str | None:
        return self.rt_track or self.track

    @property
    def track_changed(self) -> bool:
        return bool(self.rt_track and self.track and self.rt_track != self.track)

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.product.line,
            "name": self.product.name,
            "category": self.product.category,
            "type": self.product.type,
            "operator": self.product.operator,
            "direction": self.direction,
            "stop": self.stop,
            "scheduled": _iso(self.scheduled),
            "expected": _iso(self.expected),
            "realtime": self.realtime is not None,
            "delay": self.delay,
            "track": self.platform,
            "scheduled_track": self.track,
            "track_changed": self.track_changed,
            "cancelled": self.cancelled,
            "partially_cancelled": self.part_cancelled,
            "reachable": self.reachable,
            "redirected": self.redirected,
            "uncertain_delay": self.uncertain_delay,
            "extra": self.journey_status in ("A", "R"),
            "journey_status": self.journey_status,
            "notes": self.notes,
            "messages": [m.head or m.text for m in self.messages],
            "arrival_at_direction": _iso(self.arrival_at_direction),
            "occupancy": self.occupancy,
            "journey_ref": self.journey_ref,
        }


@dataclass(slots=True)
class LegPoint:
    """Origin or destination of a trip leg."""

    name: str
    id: str | None
    scheduled: datetime | None
    realtime: datetime | None
    track: str | None
    rt_track: str | None
    cancelled: bool = False

    @property
    def expected(self) -> datetime | None:
        return self.realtime or self.scheduled

    @property
    def delay(self) -> int | None:
        if self.realtime is None or self.scheduled is None:
            return None
        return round((self.realtime - self.scheduled).total_seconds() / 60)

    @property
    def platform(self) -> str | None:
        return self.rt_track or self.track

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "scheduled": _iso(self.scheduled),
            "expected": _iso(self.expected),
            "delay": self.delay,
            "track": self.platform,
            "track_changed": bool(self.rt_track and self.track and self.rt_track != self.track),
            "cancelled": self.cancelled,
        }


@dataclass(slots=True)
class Leg:
    """One part of a trip: a ride, a walk or a transfer."""

    type: str  # JNY, WALK, TRSF, ...
    origin: LegPoint
    destination: LegPoint
    product: Product | None = None
    direction: str | None = None
    cancelled: bool = False
    part_cancelled: bool = False
    reachable: bool = True
    journey_ref: str | None = None
    duration: int | None = None  # minutes
    notes: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    @property
    def is_ride(self) -> bool:
        return self.product is not None and self.type == "JNY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "line": self.product.line if self.product else None,
            "name": self.product.name if self.product else None,
            "category": self.product.category if self.product else None,
            "product_type": self.product.type if self.product else None,
            "operator": self.product.operator if self.product else None,
            "direction": self.direction,
            "origin": self.origin.as_dict(),
            "destination": self.destination.as_dict(),
            "cancelled": self.cancelled or self.origin.cancelled or self.destination.cancelled,
            "partially_cancelled": self.part_cancelled,
            "reachable": self.reachable,
            "duration": self.duration,
            "notes": self.notes,
            "messages": [m.head or m.text for m in self.messages],
            "journey_ref": self.journey_ref,
        }


@dataclass(slots=True)
class Trip:
    """A travel suggestion from A to B."""

    legs: list[Leg]
    duration: int | None  # scheduled, minutes
    rt_duration: int | None
    transfers: int
    messages: list[Message] = field(default_factory=list)
    context: str | None = None

    @property
    def rides(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.is_ride]

    @property
    def departure(self) -> LegPoint:
        return self.legs[0].origin

    @property
    def arrival(self) -> LegPoint:
        return self.legs[-1].destination

    @property
    def first_ride(self) -> Leg | None:
        rides = self.rides
        return rides[0] if rides else None

    @property
    def cancelled(self) -> bool:
        return any(
            leg.cancelled or leg.origin.cancelled or leg.destination.cancelled
            for leg in self.rides
        )

    @property
    def reachable(self) -> bool:
        return all(leg.reachable for leg in self.legs)

    @property
    def key(self) -> str:
        ride = self.first_ride
        dep = self.departure.scheduled
        return f"{ride.journey_ref if ride else ''}|{_iso(dep)}"

    @property
    def all_messages(self) -> list[Message]:
        seen: dict[str, Message] = {m.id: m for m in self.messages}
        for leg in self.legs:
            for msg in leg.messages:
                seen.setdefault(msg.id, msg)
        return list(seen.values())

    @property
    def expected_duration(self) -> int | None:
        dep, arr = self.departure.expected, self.arrival.expected
        if dep and arr:
            return round((arr - dep).total_seconds() / 60)
        return self.rt_duration or self.duration

    def as_dict(self) -> dict[str, Any]:
        return {
            "departure": self.departure.as_dict(),
            "arrival": self.arrival.as_dict(),
            "duration": self.expected_duration,
            "scheduled_duration": self.duration,
            "changes": self.transfers,
            "cancelled": self.cancelled,
            "reachable": self.reachable,
            "legs": [leg.as_dict() for leg in self.legs],
            "messages": [m.head or m.text for m in self.all_messages],
        }


@dataclass(slots=True)
class JourneyStop:
    """A stop on a vehicle's route."""

    index: int
    name: str
    id: str | None
    arr_scheduled: datetime | None
    arr_realtime: datetime | None
    dep_scheduled: datetime | None
    dep_realtime: datetime | None
    track: str | None
    rt_track: str | None
    cancelled: bool = False
    lat: float | None = None
    lon: float | None = None

    @property
    def delay(self) -> int | None:
        for sched, real in (
            (self.dep_scheduled, self.dep_realtime),
            (self.arr_scheduled, self.arr_realtime),
        ):
            if sched and real:
                return round((real - sched).total_seconds() / 60)
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "arrival": _iso(self.arr_realtime or self.arr_scheduled),
            "departure": _iso(self.dep_realtime or self.dep_scheduled),
            "scheduled_arrival": _iso(self.arr_scheduled),
            "scheduled_departure": _iso(self.dep_scheduled),
            "delay": self.delay,
            "track": self.rt_track or self.track,
            "cancelled": self.cancelled,
        }


@dataclass(slots=True)
class Journey:
    """The full route of one vehicle, with where it is right now."""

    ref: str
    stops: list[JourneyStop]
    product: Product | None
    direction: str | None
    cancelled: bool = False
    last_passed: int | None = None  # index into stops
    latitude: float | None = None
    longitude: float | None = None
    position_time: datetime | None = None
    messages: list[Message] = field(default_factory=list)
    occupancy: dict[str, Any] | None = None

    @property
    def last_stop(self) -> JourneyStop | None:
        if self.last_passed is None or not 0 <= self.last_passed < len(self.stops):
            return None
        return self.stops[self.last_passed]

    @property
    def next_stop(self) -> JourneyStop | None:
        idx = 0 if self.last_passed is None else self.last_passed + 1
        return self.stops[idx] if 0 <= idx < len(self.stops) else None

    def stop_after(self, now: datetime) -> JourneyStop | None:
        """First stop the vehicle has not left yet (fallback when no position is reported)."""
        for stop in self.stops:
            leave = stop.dep_realtime or stop.dep_scheduled or stop.arr_realtime or stop.arr_scheduled
            if leave and leave > now:
                return stop
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "name": self.product.name if self.product else None,
            "line": self.product.line if self.product else None,
            "direction": self.direction,
            "cancelled": self.cancelled,
            "last_stop": self.last_stop.name if self.last_stop else None,
            "next_stop": self.next_stop.name if self.next_stop else None,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "position_time": _iso(self.position_time),
            "occupancy": self.occupancy,
            "stops": [s.as_dict() for s in self.stops],
            "messages": [m.head or m.text for m in self.messages],
        }


# --------------------------------------------------------------------------- helpers


def product_names(bits: int) -> list[str]:
    return [name for name, bit in PRODUCTS.items() if bits & bit]


def products_mask(names: list[str] | None) -> int | None:
    if not names:
        return None
    mask = 0
    for name in names:
        mask |= PRODUCTS.get(name, 0)
    return mask or None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() == "true"


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_datetime(d: str | None, t: str | None) -> datetime | None:
    """Combine the API's YYYY-MM-DD and HH:MM[:SS] into a Danish-time datetime."""
    if not d or not t:
        return None
    try:
        day = date.fromisoformat(d)
        parts = [int(p) for p in t.split(":")]
        clock = time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return None
    return datetime.combine(day, clock, TZ)


_DURATION_RE = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 duration (PT1H5M) to whole minutes."""
    if not value:
        return None
    m = _DURATION_RE.fullmatch(value)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 1440 + h * 60 + mi + (1 if s >= 30 else 0)


def _tag(el: ET.Element) -> str:
    return el.tag.removeprefix(NS)


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    if el is None:
        return []
    return [c for c in el if _tag(c) == name]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    found = _children(el, name)
    return found[0] if found else None


def _parse_product(el: ET.Element | None) -> Product | None:
    if el is None:
        return None
    a = el.attrib
    name = a.get("name") or ""
    category = a.get("catOutL") or a.get("catOut") or a.get("catOutS") or ""
    line = a.get("line") or a.get("displayNumber") or a.get("num") or name
    # Trains are shown by category ("Re", "IC", "A" for S-tog line A); buses by line number.
    cls = _int(a.get("cls")) or 0
    if cls in (PRODUCTS["s_train"], PRODUCTS["metro"], PRODUCTS["light_rail"]) and a.get("line"):
        line = a["line"]
    return Product(
        name=name,
        line=line,
        category=category,
        cls=cls,
        operator=a.get("operator") or a.get("operatorCode"),
        number=a.get("num"),
    )


def _parse_notes(el: ET.Element | None) -> list[str]:
    """Traveller-facing notes (type A: facilities, bikes, wifi...). Internal codes are skipped."""
    notes = []
    for note in _children(_child(el, "Notes"), "Note"):
        text = (note.get("txtN") or note.text or "").strip()
        if note.get("type") == "A" and text:
            notes.append(text)
    return notes


def parse_message(el: ET.Element) -> Message:
    a = el.attrib
    stops = [
        s.get("name", "")
        for s in _children(_child(el, "affectedStops"), "StopLocation")
        if s.get("name")
    ]
    return Message(
        id=a.get("id") or a.get("externalId") or a.get("head", ""),
        head=(a.get("head") or "").strip(),
        lead=(a.get("lead") or "").strip(),
        text=(a.get("text") or a.get("lead") or "").strip(),
        category=a.get("category"),
        priority=_int(a.get("priority")),
        company=a.get("company"),
        start=parse_datetime(a.get("sDate"), a.get("sTime")),
        end=parse_datetime(a.get("eDate"), a.get("eTime")),
        active=_bool(a.get("act"), True),
        affected_stops=stops,
    )


def _parse_messages(el: ET.Element | None) -> list[Message]:
    return [parse_message(m) for m in _children(_child(el, "Messages"), "Message")]


def _parse_occupancy(el: ET.Element | None) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    for occ in _children(el, "Occupancy"):
        value = _int(occ.get("vRt")) if occ.get("vRt") else _int(occ.get("v"))
        name = occ.get("name") or "all"
        if value is not None:
            result[name] = value
    return result or None


def parse_departure(el: ET.Element) -> Departure:
    a = el.attrib
    product_el = _child(el, "ProductAtStop")
    if product_el is None:
        product_el = _child(el, "Product")
    product = _parse_product(product_el) or Product(
        name=a.get("name", ""), line=a.get("name", ""), category="", cls=0
    )
    ref_el = _child(el, "JourneyDetailRef")
    ref = ref_el.get("ref") if ref_el is not None else None
    scheduled = parse_datetime(a.get("date"), a.get("time"))
    if scheduled is None:
        raise RejseplanenError("Departure without a time")
    status = _child(el, "JourneyStatus")
    direction = a.get("direction") or a.get("origin")
    return Departure(
        key=ref or f"{product.name}|{scheduled.isoformat()}",
        product=product,
        stop=a.get("stop", ""),
        stop_id=a.get("stopExtId") or a.get("stopid", ""),
        direction=direction,
        scheduled=scheduled,
        realtime=parse_datetime(a.get("rtDate") or a.get("date"), a.get("rtTime")),
        track=a.get("track"),
        rt_track=a.get("rtTrack"),
        cancelled=_bool(a.get("cancelled")),
        part_cancelled=_bool(a.get("partCancelled")),
        reachable=_bool(a.get("reachable"), True),
        redirected=_bool(a.get("redirected")),
        uncertain_delay=_bool(a.get("uncertainDelay")),
        journey_status=(status.text or "P").strip() if status is not None else "P",
        prognosis=a.get("prognosisType"),
        journey_ref=ref,
        notes=_parse_notes(el),
        messages=_parse_messages(el),
        arrival_at_direction=parse_datetime(
            a.get("rtDateAtArrival") or a.get("dateAtArrival"),
            a.get("rtTimeAtArrival") or a.get("timeAtArrival"),
        ),
        occupancy=_parse_occupancy(el),
    )


def _parse_leg_point(el: ET.Element | None) -> LegPoint:
    a = el.attrib if el is not None else {}
    return LegPoint(
        name=a.get("name", ""),
        id=a.get("extId") or a.get("id"),
        scheduled=parse_datetime(a.get("date"), a.get("time")),
        realtime=parse_datetime(a.get("rtDate") or a.get("date"), a.get("rtTime")),
        track=a.get("track"),
        rt_track=a.get("rtTrack"),
        cancelled=_bool(a.get("cancelled")),
    )


def parse_trip(el: ET.Element) -> Trip:
    legs = []
    for leg_el in _children(_child(el, "LegList"), "Leg"):
        a = leg_el.attrib
        ref_el = _child(leg_el, "JourneyDetailRef")
        product_el = _child(leg_el, "Product")
        legs.append(
            Leg(
                type=a.get("type", "JNY"),
                origin=_parse_leg_point(_child(leg_el, "Origin")),
                destination=_parse_leg_point(_child(leg_el, "Destination")),
                product=_parse_product(product_el),
                direction=a.get("direction"),
                cancelled=_bool(a.get("cancelled")),
                part_cancelled=_bool(a.get("partCancelled")),
                reachable=_bool(a.get("reachable"), True),
                journey_ref=ref_el.get("ref") if ref_el is not None else None,
                duration=parse_duration(a.get("duration")),
                notes=_parse_notes(leg_el),
                messages=_parse_messages(leg_el),
            )
        )
    if not legs:
        raise RejseplanenError("Trip without legs")
    a = el.attrib
    rides = [leg for leg in legs if leg.is_ride]
    transfers = _int(a.get("transferCount"))
    return Trip(
        legs=legs,
        duration=parse_duration(a.get("duration")),
        rt_duration=parse_duration(a.get("rtDuration")),
        transfers=transfers if transfers is not None else max(len(rides) - 1, 0),
        messages=_parse_messages(el),
        context=a.get("ctxRecon"),
    )


def parse_journey(el: ET.Element, ref: str) -> Journey:
    stops = []
    for i, s in enumerate(_children(_child(el, "Stops"), "Stop")):
        a = s.attrib
        stops.append(
            JourneyStop(
                index=i,
                name=a.get("name", ""),
                id=a.get("extId") or a.get("id"),
                arr_scheduled=parse_datetime(a.get("arrDate"), a.get("arrTime")),
                arr_realtime=parse_datetime(a.get("rtArrDate") or a.get("arrDate"), a.get("rtArrTime")),
                dep_scheduled=parse_datetime(a.get("depDate"), a.get("depTime")),
                dep_realtime=parse_datetime(a.get("rtDepDate") or a.get("depDate"), a.get("rtDepTime")),
                track=a.get("depTrack") or a.get("arrTrack"),
                rt_track=a.get("rtDepTrack") or a.get("rtArrTrack"),
                cancelled=_bool(a.get("cancelled")),
                lat=_float(a.get("lat")),
                lon=_float(a.get("lon")),
            )
        )
    last = None
    for name in ("rtLastPassStopRef", "lastPassStopRef"):
        node = _child(el, name)
        if node is not None and node.text and node.text.strip().lstrip("-").isdigit():
            last = int(node.text.strip())
            break
    pos = _child(el, "lastPos")
    reported = _child(el, "lastPosReported")
    reported_at = None
    if reported is not None and reported.text:
        try:
            reported_at = datetime.fromisoformat(reported.text.strip())
            if reported_at.tzinfo is None:
                reported_at = reported_at.replace(tzinfo=TZ)
        except ValueError:
            reported_at = None
    directions = _children(_child(el, "Directions"), "Direction")
    a = el.attrib
    return Journey(
        ref=ref,
        stops=stops,
        product=_parse_product(_child(el, "Product")),
        direction=(directions[0].text or "").strip() if directions else (stops[-1].name if stops else None),
        cancelled=_bool(a.get("cancelled")),
        last_passed=last,
        latitude=_float(pos.get("lat")) if pos is not None else None,
        longitude=_float(pos.get("lon")) if pos is not None else None,
        position_time=reported_at,
        messages=_parse_messages(el),
        occupancy=_parse_occupancy(el),
    )


def parse_stop(el: ET.Element) -> Stop:
    a = el.attrib
    return Stop(
        id=a.get("extId") or a.get("id", ""),
        name=a.get("name", ""),
        lat=_float(a.get("lat")),
        lon=_float(a.get("lon")),
        products=_int(a.get("products")) or 0,
        distance=_int(a.get("dist")),
        main_mast=a.get("mainMastExtId") if _bool(a.get("hasMainMast")) else None,
    )


def dedupe_stops(stops: list[Stop]) -> list[Stop]:
    """Drop the individual platforms/masts of a station that is itself in the list."""
    ids = {s.id for s in stops}
    seen: set[tuple[str, str | None]] = set()
    result = []
    for stop in stops:
        if stop.main_mast and stop.main_mast in ids:
            continue
        key = (stop.name, stop.main_mast)
        if key in seen:
            continue
        seen.add(key)
        result.append(stop)
    return result


# --------------------------------------------------------------------------- client


class RejseplanenClient:
    """Thin async wrapper over the services this integration uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        *,
        language: str = "da",
        on_request: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._language = language
        self._on_request = on_request

    async def _get(self, service: str, params: dict[str, Any]) -> ET.Element | None:
        """Call a service; returns the root element, or None for 'no result'."""
        query = {k: v for k, v in params.items() if v is not None and v != ""}
        query.setdefault("lang", self._language)
        query["format"] = "xml"
        if self._on_request:
            self._on_request(service)
        try:
            async with self._session.get(
                BASE_URL + service,
                params=query,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/xml"},
                timeout=TIMEOUT,
            ) as resp:
                body = await resp.read()
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise RejseplanenConnectionError(f"{service}: {err}") from err

        root = None
        if body.strip():
            try:
                root = ET.fromstring(body)
            except ET.ParseError:
                root = None

        if root is not None and _tag(root).lower() in ("hafasresterror", "error"):
            code = root.get("errorCode", "")
            text = root.get("errorText", "")
            _raise_for_code(service, status, code, text)
            return None
        if status in (401, 403):
            raise RejseplanenAuthError(f"{service}: HTTP {status}")
        if status == 429:
            raise RejseplanenQuotaError(f"{service}: HTTP 429")
        if status >= 400:
            raise RejseplanenError(f"{service}: HTTP {status}")
        if root is None:
            raise RejseplanenError(f"{service}: unreadable response")
        return root

    async def async_validate(self) -> None:
        """Raise if the key does not work."""
        await self._get("datainfo", {})

    async def async_search_stops(self, query: str, limit: int = 10) -> list[Stop]:
        root = await self._get(
            "location.name", {"input": query, "type": "S", "maxNo": limit * 3}
        )
        return dedupe_stops([parse_stop(el) for el in _iter(root, "StopLocation")])[:limit]

    async def async_nearby_stops(
        self, lat: float, lon: float, radius: int = 2000, limit: int = 10
    ) -> list[Stop]:
        root = await self._get(
            "location.nearbystops",
            {
                "originCoordLat": lat,
                "originCoordLong": lon,
                "r": radius,
                "maxNo": limit,
                "type": "S",
            },
        )
        return dedupe_stops([parse_stop(el) for el in _iter(root, "StopLocation")])[:limit]

    async def async_board(
        self,
        stop_id: str,
        *,
        arrivals: bool = False,
        when: datetime | None = None,
        duration: int = 60,
        max_journeys: int | None = None,
        products: int | None = None,
        lines: list[str] | None = None,
        direction: str | None = None,
    ) -> list[Departure]:
        params: dict[str, Any] = {
            "id": stop_id,
            "duration": duration,
            "maxJourneys": max_journeys,
            "products": products,
            "lines": ",".join(lines) if lines else None,
            "direction": direction,
        }
        if when is not None:
            local = when.astimezone(TZ)
            params["date"] = local.strftime("%Y-%m-%d")
            params["time"] = local.strftime("%H:%M")
        service = "arrivalBoard" if arrivals else "departureBoard"
        root = await self._get(service, params)
        rows = []
        for el in _iter(root, "Arrival" if arrivals else "Departure"):
            try:
                rows.append(parse_departure(el))
            except RejseplanenError:
                continue
        rows.sort(key=lambda d: d.expected)
        return rows

    async def async_messages(self, stop_id: str) -> list[Message]:
        root = await self._get("himsearch", {"stations": stop_id})
        return [parse_message(el) for el in _iter(root, "Message")]

    async def async_trips(
        self,
        origin_id: str,
        dest_id: str,
        *,
        when: datetime | None = None,
        arrive_by: bool = False,
        num: int = 4,
        max_changes: int | None = None,
        products: int | None = None,
    ) -> list[Trip]:
        params: dict[str, Any] = {
            "originId": origin_id,
            "destId": dest_id,
            "numF": num,
            "numB": 0,
            "maxChange": max_changes,
            "products": products,
        }
        if when is not None:
            local = when.astimezone(TZ)
            params["date"] = local.strftime("%Y-%m-%d")
            params["time"] = local.strftime("%H:%M")
        if arrive_by:
            params["searchForArrival"] = 1
            params["numB"] = num
            params["numF"] = 0
        root = await self._get("trip", params)
        trips = []
        for el in _iter(root, "Trip"):
            try:
                trips.append(parse_trip(el))
            except RejseplanenError:
                continue
        return trips

    async def async_journey(self, ref: str) -> Journey | None:
        root = await self._get("journeyDetail", {"id": ref})
        if root is None:
            return None
        el = root if _tag(root) == "JourneyDetail" else next(iter(_iter(root, "JourneyDetail")), None)
        return parse_journey(el, ref) if el is not None else None


def _iter(root: ET.Element | None, name: str) -> list[ET.Element]:
    if root is None:
        return []
    return [c for c in root if _tag(c) == name]


def _raise_for_code(service: str, status: int, code: str, text: str) -> None:
    msg = f"{service}: {code} {text}".strip()
    upper = code.upper()
    if upper.startswith("API_AUTH") or status in (401, 403):
        raise RejseplanenAuthError(msg)
    if upper.startswith("API_QUOTA") or "QUOTA" in upper or status == 429:
        raise RejseplanenQuotaError(msg)
    if upper in _EMPTY_ERRORS or upper.startswith("SVC_NO_RESULT"):
        return
    raise RejseplanenError(msg)


def minutes_until(when: datetime, now: datetime) -> int:
    return max(0, math.ceil((when - now) / timedelta(minutes=1)))

"""Coordinators: one per station board or trip (config subentry)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Departure,
    Journey,
    Message,
    RejseplanenAuthError,
    RejseplanenClient,
    RejseplanenError,
    RejseplanenQuotaError,
    Trip,
    products_mask,
)
from .budget import CallBudget, PollPlan
from .const import (
    BOARD_ARRIVALS,
    CONF_ACTIVE_DAYS,
    CONF_ACTIVE_END,
    CONF_ACTIVE_START,
    CONF_BOARD,
    CONF_DELAY_THRESHOLD,
    CONF_DEST_ID,
    CONF_DIRECTION_ID,
    CONF_DURATION,
    CONF_HIDE_CANCELLED,
    CONF_IDLE_INTERVAL,
    CONF_LINES,
    CONF_MAX_CHANGES,
    CONF_MAX_DEPARTURES,
    CONF_NUM_TRIPS,
    CONF_ORIGIN_ID,
    CONF_PRODUCTS,
    CONF_STATION_MESSAGES,
    CONF_STOP_ID,
    CONF_TRACK_JOURNEY,
    CONF_WALK_MINUTES,
    DEFAULT_ACTIVE_END,
    DEFAULT_ACTIVE_START,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_DURATION,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_NUM_TRIPS,
    DOMAIN,
    EVENT_ARRIVED,
    EVENT_CANCELLED,
    EVENT_CONNECTION_AT_RISK,
    EVENT_DELAYED,
    EVENT_DEPARTED,
    EVENT_DISRUPTION,
    EVENT_NAME,
    EVENT_TRACK_CHANGED,
    MESSAGE_POLL_EVERY,
    STATUS_CANCELLED,
    STATUS_CONNECTION_AT_RISK,
    STATUS_DELAYED,
    STATUS_DISRUPTED,
    STATUS_NONE,
    STARVED_INTERVAL,
    STATUS_ON_TIME,
    SUBENTRY_STATION,
    TICK_INTERVAL,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

# Changes only matter for rows this close; far-away departures change all the time.
EVENT_HORIZON = timedelta(minutes=90)


@dataclass
class RejseplanenRuntime:
    """Everything a loaded config entry holds."""

    client: RejseplanenClient
    budget: CallBudget
    coordinators: dict[str, BoardCoordinator | TripCoordinator] = field(default_factory=dict)


type RejseplanenConfigEntry = ConfigEntry[RejseplanenRuntime]


@dataclass
class StationData:
    stop_name: str
    departures: list[Departure]
    messages: list[Message]
    journey: Journey | None
    updated: datetime


@dataclass
class TripData:
    trips: list[Trip]
    messages: list[Message]
    journey: Journey | None
    updated: datetime


def _parse_time(value: str | None, default: str) -> time:
    try:
        return time.fromisoformat(value or default)
    except ValueError:
        return time.fromisoformat(default)


def build_plan(options: dict[str, Any], weight: float) -> PollPlan:
    idle = int(options.get(CONF_IDLE_INTERVAL) or 0)
    return PollPlan(
        weight=weight,
        start=_parse_time(options.get(CONF_ACTIVE_START), DEFAULT_ACTIVE_START),
        end=_parse_time(options.get(CONF_ACTIVE_END), DEFAULT_ACTIVE_END),
        days=list(options.get(CONF_ACTIVE_DAYS) or WEEKDAYS),
        idle_interval=timedelta(minutes=idle) if idle > 0 else None,
    )


def _merge_messages(*groups: list[Message]) -> list[Message]:
    seen: dict[str, Message] = {}
    for group in groups:
        for msg in group:
            if msg.active:
                seen.setdefault(msg.id, msg)
    return sorted(seen.values(), key=lambda m: (m.priority is None, m.priority or 0))


class _BaseCoordinator[T](DataUpdateCoordinator[T]):
    """Budget-aware polling, a 30 s tick for countdowns, and change events."""

    config_entry: RejseplanenConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RejseplanenConfigEntry,
        subentry: ConfigSubentry,
        runtime: RejseplanenRuntime,
        weight: float,
    ) -> None:
        self.subentry = subentry
        self.options: dict[str, Any] = dict(subentry.data)
        self.client = runtime.client
        self.budget = runtime.budget
        self.plan_id = subentry.subentry_id
        self.budget.plans[self.plan_id] = build_plan(self.options, weight)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=self.budget.next_interval(self.plan_id),
        )
        self.walk = timedelta(minutes=int(self.options.get(CONF_WALK_MINUTES) or 0))
        self.threshold = int(self.options.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD))
        self.polls = 0
        self.last_poll: datetime | None = None
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._fired: dict[str, datetime] = {}  # departed/arrived keys already announced
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._quota_hit = False

    @property
    def plan(self) -> PollPlan:
        return self.budget.plans[self.plan_id]

    @property
    def title(self) -> str:
        return self.subentry.title

    @callback
    def async_start_tick(self) -> None:
        self._unsub_tick = async_track_time_interval(self.hass, self._tick, TICK_INTERVAL)
        self.config_entry.async_on_unload(self.async_stop_tick)

    @callback
    def async_stop_tick(self) -> None:
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
        self.budget.plans.pop(self.plan_id, None)

    @callback
    def async_add_event_listener(
        self, listener: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    @callback
    def fire(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {
            "type": event_type,
            "config_entry_id": self.config_entry.entry_id,
            "subentry_id": self.plan_id,
            "name": self.title,
            **data,
        }
        self.hass.bus.async_fire(EVENT_NAME, payload)
        for listener in list(self._event_listeners):
            listener(event_type, payload)

    async def _async_update_data(self) -> T:
        now = dt_util.utcnow()
        try:
            if (
                self.data is not None
                and self.plan.idle_interval is None
                and not self.plan.is_active(now)
            ):
                # Outside active hours: keep the last board, spend no calls.
                return self.data
            try:
                new = await self._fetch(now)
            except RejseplanenAuthError as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="auth_failed"
                ) from err
            except RejseplanenQuotaError as err:
                self._quota_hit = True
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="quota_exceeded"
                ) from err
            except RejseplanenError as err:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="update_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
            self.polls += 1
            self.last_poll = now
            if self.data is not None:
                self._compare(self.data, new, now)
            return new
        finally:
            # Hammering a key whose quota is gone only makes things worse.
            self.update_interval = (
                STARVED_INTERVAL if self._quota_hit else self.budget.next_interval(self.plan_id)
            )
            self._quota_hit = False

    async def _fetch(self, now: datetime) -> T:
        raise NotImplementedError

    def _compare(self, old: T, new: T, now: datetime) -> None:
        raise NotImplementedError

    @callback
    def _tick(self, now: datetime) -> None:
        if self.data is not None:
            self._on_tick(dt_util.utcnow())
            self.async_update_listeners()

    def _on_tick(self, now: datetime) -> None:
        raise NotImplementedError

    def _announce_once(self, key: str, event_type: str, data: dict[str, Any], now: datetime) -> None:
        if key in self._fired:
            return
        self._fired[key] = now
        for old_key, at in list(self._fired.items()):
            if now - at > timedelta(hours=6):
                del self._fired[old_key]
        self.fire(event_type, data)

    def _check_delay(self, key: str, old_delay: int | None, new_delay: int | None, data: dict[str, Any]) -> None:
        old_d, new_d = old_delay or 0, new_delay or 0
        if new_d >= self.threshold and (old_d < self.threshold or abs(new_d - old_d) >= 2):
            self.fire(EVENT_DELAYED, {**data, "previous_delay": old_delay})

    def new_messages(self, old: list[Message], new: list[Message]) -> None:
        known = {m.id for m in old}
        for msg in new:
            if msg.id not in known:
                self.fire(EVENT_DISRUPTION, {"message": msg.as_dict()})


class BoardCoordinator(_BaseCoordinator[StationData]):
    """A departure or arrival board for one stop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RejseplanenConfigEntry,
        subentry: ConfigSubentry,
        runtime: RejseplanenRuntime,
    ) -> None:
        options = subentry.data
        weight = 1.0
        if options.get(CONF_STATION_MESSAGES, True):
            weight += 1 / MESSAGE_POLL_EVERY
        if options.get(CONF_TRACK_JOURNEY):
            weight += 1
        super().__init__(hass, entry, subentry, runtime, weight)
        self.arrivals = options.get(CONF_BOARD) == BOARD_ARRIVALS
        self.stop_id: str = options[CONF_STOP_ID]
        self.max_rows = int(options.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES))
        self.lines = [
            line.strip().lower()
            for line in str(options.get(CONF_LINES) or "").split(",")
            if line.strip()
        ]
        self._station_messages: list[Message] = []

    @property
    def kind(self) -> str:
        return SUBENTRY_STATION

    def _line_matches(self, dep: Departure) -> bool:
        if not self.lines:
            return True
        names = {
            (dep.product.line or "").lower(),
            (dep.product.name or "").lower(),
            (dep.product.category or "").lower(),
            (dep.product.number or "").lower(),
        }
        return any(line in names for line in self.lines)

    async def _fetch(self, now: datetime) -> StationData:
        opts = self.options
        rows = await self.client.async_board(
            self.stop_id,
            arrivals=self.arrivals,
            duration=int(opts.get(CONF_DURATION, DEFAULT_DURATION)),
            products=products_mask(opts.get(CONF_PRODUCTS)),
            direction=opts.get(CONF_DIRECTION_ID) or None,
        )
        rows = [r for r in rows if self._line_matches(r)]
        if opts.get(CONF_HIDE_CANCELLED):
            rows = [r for r in rows if not r.cancelled]
        # Keep a little history so the tick can still announce what just left.
        rows = [r for r in rows if r.expected >= now - timedelta(minutes=5)]
        rows = rows[: self.max_rows * 2 + 5]

        if opts.get(CONF_STATION_MESSAGES, True) and self.polls % MESSAGE_POLL_EVERY == 0:
            try:
                self._station_messages = await self.client.async_messages(self.stop_id)
            except RejseplanenError as err:
                if isinstance(err, (RejseplanenAuthError, RejseplanenQuotaError)):
                    raise
                _LOGGER.debug("Station messages for %s failed: %s", self.title, err)

        journey = None
        if opts.get(CONF_TRACK_JOURNEY):
            nxt = self._next(rows, now)
            if nxt and nxt.journey_ref:
                try:
                    journey = await self.client.async_journey(nxt.journey_ref)
                except RejseplanenError as err:
                    if isinstance(err, (RejseplanenAuthError, RejseplanenQuotaError)):
                        raise
                    _LOGGER.debug("Journey detail for %s failed: %s", self.title, err)

        stop_name = rows[0].stop if rows else (self.data.stop_name if self.data else self.title)
        return StationData(
            stop_name=stop_name,
            departures=rows,
            messages=_merge_messages(self._station_messages, *(r.messages for r in rows)),
            journey=journey,
            updated=now,
        )

    def _next(self, rows: list[Departure], now: datetime) -> Departure | None:
        for row in rows:
            if not row.cancelled and row.expected >= now + self.walk:
                return row
        return None

    def upcoming(self, now: datetime | None = None) -> list[Departure]:
        """Rows you can still catch, soonest first, trimmed to the configured count."""
        if self.data is None:
            return []
        now = now or dt_util.utcnow()
        return [r for r in self.data.departures if r.expected >= now + self.walk][: self.max_rows]

    def next_departure(self, index: int = 0) -> Departure | None:
        running = [r for r in self.upcoming() if not r.cancelled]
        return running[index] if len(running) > index else None

    def delayed(self) -> list[Departure]:
        return [r for r in self.upcoming() if not r.cancelled and (r.delay or 0) >= self.threshold]

    def cancelled(self) -> list[Departure]:
        return [r for r in self.upcoming() if r.cancelled or r.part_cancelled]

    def status(self) -> str:
        rows = self.upcoming()
        if not rows:
            return STATUS_NONE
        first = rows[0]
        if first.cancelled:
            return STATUS_CANCELLED
        if not first.reachable:
            return STATUS_CONNECTION_AT_RISK
        if (first.delay or 0) >= self.threshold:
            return STATUS_DELAYED
        if first.messages:
            return STATUS_DISRUPTED
        return STATUS_ON_TIME

    def has_problem(self) -> bool:
        if self.data is None:
            return False
        return bool(self.delayed() or self.cancelled() or self.data.messages)

    def _compare(self, old: StationData, new: StationData, now: datetime) -> None:
        before = {r.key: r for r in old.departures}
        for row in new.departures:
            if row.expected - now > EVENT_HORIZON:
                continue
            prev = before.get(row.key)
            data = {"departure": row.as_dict(), "stop": new.stop_name}
            if prev is None:
                if row.cancelled:
                    self.fire(EVENT_CANCELLED, data)
                continue
            if row.cancelled and not prev.cancelled:
                self.fire(EVENT_CANCELLED, data)
            if not row.cancelled:
                self._check_delay(row.key, prev.delay, row.delay, data)
            if row.platform and prev.platform and row.platform != prev.platform:
                self.fire(EVENT_TRACK_CHANGED, {**data, "previous_track": prev.platform})
        # Rows that dropped off the board since the last poll have left.
        current = {r.key for r in new.departures}
        for key, prev in before.items():
            if key not in current and not prev.cancelled and prev.expected <= now:
                self._announce_departed(prev, old.stop_name, now)
        self.new_messages(old.messages, new.messages)

    def _announce_departed(self, row: Departure, stop: str, now: datetime) -> None:
        if now - row.expected < timedelta(minutes=10):
            self._announce_once(
                row.key,
                EVENT_ARRIVED if self.arrivals else EVENT_DEPARTED,
                {"departure": row.as_dict(), "stop": stop},
                now,
            )

    def _on_tick(self, now: datetime) -> None:
        for row in self.data.departures:
            if not row.cancelled and row.expected <= now:
                self._announce_departed(row, self.data.stop_name, now)


class TripCoordinator(_BaseCoordinator[TripData]):
    """Connections from A to B."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RejseplanenConfigEntry,
        subentry: ConfigSubentry,
        runtime: RejseplanenRuntime,
    ) -> None:
        weight = 2.0 if subentry.data.get(CONF_TRACK_JOURNEY) else 1.0
        super().__init__(hass, entry, subentry, runtime, weight)

    @property
    def kind(self) -> str:
        return "trip"

    async def _fetch(self, now: datetime) -> TripData:
        opts = self.options
        max_changes = opts.get(CONF_MAX_CHANGES)
        trips = await self.client.async_trips(
            opts[CONF_ORIGIN_ID],
            opts[CONF_DEST_ID],
            when=now + self.walk,
            num=int(opts.get(CONF_NUM_TRIPS, DEFAULT_NUM_TRIPS)),
            max_changes=int(max_changes) if max_changes not in (None, "") else None,
            products=products_mask(opts.get(CONF_PRODUCTS)),
        )
        trips.sort(key=lambda t: t.departure.expected or now)
        journey = None
        if opts.get(CONF_TRACK_JOURNEY):
            nxt = self._next(trips, now)
            ride = nxt.first_ride if nxt else None
            if ride and ride.journey_ref:
                try:
                    journey = await self.client.async_journey(ride.journey_ref)
                except RejseplanenError as err:
                    if isinstance(err, (RejseplanenAuthError, RejseplanenQuotaError)):
                        raise
                    _LOGGER.debug("Journey detail for %s failed: %s", self.title, err)
        return TripData(
            trips=trips,
            messages=_merge_messages(*(t.all_messages for t in trips[:2])),
            journey=journey,
            updated=now,
        )

    def _next(self, trips: list[Trip], now: datetime) -> Trip | None:
        for trip in trips:
            dep = trip.departure.expected
            if not trip.cancelled and dep and dep >= now + self.walk:
                return trip
        return None

    def upcoming(self, now: datetime | None = None) -> list[Trip]:
        if self.data is None:
            return []
        now = now or dt_util.utcnow()
        return [
            t for t in self.data.trips
            if t.departure.expected and t.departure.expected >= now + self.walk
        ]

    def next_trip(self, index: int = 0) -> Trip | None:
        running = [t for t in self.upcoming() if not t.cancelled]
        return running[index] if len(running) > index else None

    def status(self) -> str:
        upcoming = self.upcoming()
        if not upcoming:
            return STATUS_NONE
        if upcoming[0].cancelled:
            return STATUS_CANCELLED
        trip = self.next_trip()
        if trip is None:
            return STATUS_CANCELLED
        if not trip.reachable:
            return STATUS_CONNECTION_AT_RISK
        delays = [trip.departure.delay or 0, trip.arrival.delay or 0]
        if max(delays) >= self.threshold:
            return STATUS_DELAYED
        if trip.all_messages:
            return STATUS_DISRUPTED
        return STATUS_ON_TIME

    def has_problem(self) -> bool:
        return self.status() in (STATUS_CANCELLED, STATUS_CONNECTION_AT_RISK, STATUS_DELAYED, STATUS_DISRUPTED)

    def _compare(self, old: TripData, new: TripData, now: datetime) -> None:
        before = {t.key: t for t in old.trips}
        for trip in new.trips[:3]:
            dep = trip.departure.expected
            if dep is None or dep - now > EVENT_HORIZON:
                continue
            prev = before.get(trip.key)
            data = {"trip": trip.as_dict()}
            if prev is None:
                continue
            if trip.cancelled and not prev.cancelled:
                self.fire(EVENT_CANCELLED, data)
            if not trip.reachable and prev.reachable:
                self.fire(EVENT_CONNECTION_AT_RISK, data)
            if not trip.cancelled:
                self._check_delay(
                    trip.key,
                    max(prev.departure.delay or 0, prev.arrival.delay or 0),
                    max(trip.departure.delay or 0, trip.arrival.delay or 0),
                    data,
                )
            if (
                trip.departure.platform
                and prev.departure.platform
                and trip.departure.platform != prev.departure.platform
            ):
                self.fire(EVENT_TRACK_CHANGED, {**data, "previous_track": prev.departure.platform})
        self.new_messages(old.messages, new.messages)

    def _on_tick(self, now: datetime) -> None:
        for trip in self.data.trips:
            dep = trip.departure.expected
            if trip.cancelled or dep is None or dep > now:
                continue
            if now - dep < timedelta(minutes=10):
                self._announce_once(trip.key, EVENT_DEPARTED, {"trip": trip.as_dict()}, now)

"""Sensors for station boards, trips and the API budget."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Departure, Trip, minutes_until
from .const import CONF_TRACK_JOURNEY, STATUSES, SUBENTRY_STATION
from .coordinator import BoardCoordinator, RejseplanenConfigEntry, TripCoordinator
from .entity import AnyCoordinator, RejseplanenEntity, hub_device

# Large lists stay out of the recorder database.
UNRECORDED = frozenset(
    {"departures", "trips", "legs", "messages", "journey", "stops", "later_trips", "items"}
)


def _journey_value(c: AnyCoordinator) -> str | None:
    journey = c.data.journey if c.data else None
    if journey is None:
        return None
    stop = journey.last_stop or journey.stop_after(dt_util.utcnow())
    return stop.name if stop else None


def _journey_attrs(c: AnyCoordinator) -> dict[str, Any]:
    journey = c.data.journey if c.data else None
    return {"journey": journey.as_dict()} if journey else {}


# ----------------------------------------------------------------- station boards


@dataclass(frozen=True, kw_only=True)
class BoardSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BoardCoordinator], Any]
    attrs_fn: Callable[[BoardCoordinator], dict[str, Any]] | None = None
    needs_tracking: bool = False


def _dep(index: int = 0) -> Callable[[BoardCoordinator], Departure | None]:
    return lambda c: c.next_departure(index)


def _next_attrs(c: BoardCoordinator) -> dict[str, Any]:
    dep = c.next_departure()
    attrs: dict[str, Any] = dep.as_dict() if dep else {}
    attrs.pop("journey_ref", None)
    attrs["stop"] = c.data.stop_name if c.data else None
    attrs["departures"] = [row.as_dict() for row in c.upcoming()]
    attrs["messages"] = [m.as_dict() for m in c.data.messages] if c.data else []
    attrs["board"] = "arrivals" if c.arrivals else "departures"
    return attrs


def _rows_attrs(rows: list[Departure]) -> dict[str, Any]:
    return {"departures": [row.as_dict() for row in rows]}


BOARD_SENSORS: tuple[BoardSensorDescription, ...] = (
    BoardSensorDescription(
        key="next_departure",
        translation_key="next_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: d.expected if (d := c.next_departure()) else None,
        attrs_fn=_next_attrs,
    ),
    BoardSensorDescription(
        key="second_departure",
        translation_key="second_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: d.expected if (d := c.next_departure(1)) else None,
        attrs_fn=lambda c: d.as_dict() if (d := c.next_departure(1)) else {},
    ),
    BoardSensorDescription(
        key="minutes_until",
        translation_key="minutes_until",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: minutes_until(d.expected, dt_util.utcnow()) if (d := c.next_departure()) else None,
    ),
    BoardSensorDescription(
        key="next_line",
        translation_key="next_line",
        value_fn=lambda c: d.product.name if (d := c.next_departure()) else None,
        attrs_fn=lambda c: {"line": d.product.line, "category": d.product.category, "type": d.product.type, "operator": d.product.operator} if (d := c.next_departure()) else {},
    ),
    BoardSensorDescription(
        key="next_direction",
        translation_key="next_direction",
        value_fn=lambda c: d.direction if (d := c.next_departure()) else None,
    ),
    BoardSensorDescription(
        key="next_delay",
        translation_key="next_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: (d.delay or 0) if (d := c.next_departure()) else None,
        attrs_fn=lambda c: {"realtime": d.realtime is not None, "uncertain": d.uncertain_delay} if (d := c.next_departure()) else {},
    ),
    BoardSensorDescription(
        key="next_track",
        translation_key="next_track",
        value_fn=lambda c: d.platform if (d := c.next_departure()) else None,
        attrs_fn=lambda c: {"scheduled_track": d.track, "track_changed": d.track_changed} if (d := c.next_departure()) else {},
    ),
    BoardSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda c: c.status(),
    ),
    BoardSensorDescription(
        key="delayed",
        translation_key="delayed",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(c.delayed()),
        attrs_fn=lambda c: _rows_attrs(c.delayed()),
    ),
    BoardSensorDescription(
        key="cancelled",
        translation_key="cancelled",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(c.cancelled()),
        attrs_fn=lambda c: _rows_attrs(c.cancelled()),
    ),
    BoardSensorDescription(
        key="messages",
        translation_key="messages",
        value_fn=lambda c: len(c.data.messages) if c.data else 0,
        attrs_fn=lambda c: {
            "messages": [m.as_dict() for m in c.data.messages] if c.data else [],
            "headline": c.data.messages[0].head if c.data and c.data.messages else None,
        },
    ),
    BoardSensorDescription(
        key="train_position",
        translation_key="train_position",
        value_fn=_journey_value,
        attrs_fn=_journey_attrs,
        needs_tracking=True,
    ),
)


# ------------------------------------------------------------------------ trips


@dataclass(frozen=True, kw_only=True)
class TripSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TripCoordinator], Any]
    attrs_fn: Callable[[TripCoordinator], dict[str, Any]] | None = None
    needs_tracking: bool = False


def _trip(c: TripCoordinator) -> Trip | None:
    return c.next_trip()


def _trip_attrs(c: TripCoordinator) -> dict[str, Any]:
    trip = c.next_trip()
    attrs: dict[str, Any] = trip.as_dict() if trip else {}
    attrs["trips"] = [t.as_dict() for t in c.upcoming()]
    attrs["later_trips"] = [t.as_dict() for t in c.upcoming()[1:] if not t.cancelled]
    attrs["messages"] = [m.as_dict() for m in c.data.messages] if c.data else []
    return attrs


def _leave_by(c: TripCoordinator) -> datetime | None:
    trip = c.next_trip()
    if trip is None or trip.departure.expected is None:
        return None
    return trip.departure.expected - c.walk


TRIP_SENSORS: tuple[TripSensorDescription, ...] = (
    TripSensorDescription(
        key="next_departure",
        translation_key="trip_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: t.departure.expected if (t := _trip(c)) else None,
        attrs_fn=_trip_attrs,
    ),
    TripSensorDescription(
        key="arrival",
        translation_key="trip_arrival",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: t.arrival.expected if (t := _trip(c)) else None,
    ),
    TripSensorDescription(
        key="leave_by",
        translation_key="leave_by",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_leave_by,
    ),
    TripSensorDescription(
        key="minutes_until",
        translation_key="minutes_until",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: minutes_until(t.departure.expected, dt_util.utcnow()) if (t := _trip(c)) and t.departure.expected else None,
    ),
    TripSensorDescription(
        key="duration",
        translation_key="trip_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: t.expected_duration if (t := _trip(c)) else None,
        attrs_fn=lambda c: {"scheduled_duration": t.duration} if (t := _trip(c)) else {},
    ),
    TripSensorDescription(
        key="changes",
        translation_key="changes",
        value_fn=lambda c: t.transfers if (t := _trip(c)) else None,
    ),
    TripSensorDescription(
        key="departure_delay",
        translation_key="departure_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: (t.departure.delay or 0) if (t := _trip(c)) else None,
    ),
    TripSensorDescription(
        key="arrival_delay",
        translation_key="arrival_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: (t.arrival.delay or 0) if (t := _trip(c)) else None,
    ),
    TripSensorDescription(
        key="departure_track",
        translation_key="next_track",
        value_fn=lambda c: t.departure.platform if (t := _trip(c)) else None,
    ),
    TripSensorDescription(
        key="first_line",
        translation_key="next_line",
        value_fn=lambda c: r.product.name if (t := _trip(c)) and (r := t.first_ride) and r.product else None,
        attrs_fn=lambda c: {"direction": r.direction, "legs": [leg.as_dict() for leg in t.legs]} if (t := _trip(c)) and (r := t.first_ride) else {},
    ),
    TripSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda c: c.status(),
    ),
    TripSensorDescription(
        key="messages",
        translation_key="messages",
        value_fn=lambda c: len(c.data.messages) if c.data else 0,
        attrs_fn=lambda c: {
            "messages": [m.as_dict() for m in c.data.messages] if c.data else [],
            "headline": c.data.messages[0].head if c.data and c.data.messages else None,
        },
    ),
    TripSensorDescription(
        key="train_position",
        translation_key="train_position",
        value_fn=_journey_value,
        attrs_fn=_journey_attrs,
        needs_tracking=True,
    ),
)


class BoardSensor(RejseplanenEntity[AnyCoordinator], SensorEntity):
    """A sensor of a station board or a trip."""

    _unrecorded_attributes = UNRECORDED
    entity_description: BoardSensorDescription | TripSensorDescription

    def __init__(self, coordinator: AnyCoordinator, description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        if fn is None or self.coordinator.data is None:
            return None
        return fn(self.coordinator)


# ------------------------------------------------------------------------ budget


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    value_fn: Callable[[RejseplanenConfigEntry], Any]
    attrs_fn: Callable[[RejseplanenConfigEntry], dict[str, Any]] | None = None


HUB_SENSORS: tuple[HubSensorDescription, ...] = (
    HubSensorDescription(
        key="calls_month",
        translation_key="calls_month",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.runtime_data.budget.calls_month,
        attrs_fn=lambda e: {
            "quota": e.runtime_data.budget.quota,
            "by_service": dict(e.runtime_data.budget.by_service),
        },
    ),
    HubSensorDescription(
        key="calls_today",
        translation_key="calls_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.runtime_data.budget.calls_today,
    ),
    HubSensorDescription(
        key="calls_remaining",
        translation_key="calls_remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.runtime_data.budget.remaining,
    ),
    HubSensorDescription(
        key="calls_projected",
        translation_key="calls_projected",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.runtime_data.budget.projected(),
    ),
    HubSensorDescription(
        key="poll_interval",
        translation_key="poll_interval",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: round(e.runtime_data.budget.active_interval().total_seconds()),
    ),
)


class HubSensor(SensorEntity):
    """API usage of the whole key."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: HubSensorDescription

    def __init__(self, entry: RejseplanenConfigEntry, description: HubSensorDescription) -> None:
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.entry.runtime_data.budget.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.entry) if fn else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RejseplanenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(HubSensor(entry, d) for d in HUB_SENSORS)
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        tracking = bool(coordinator.options.get(CONF_TRACK_JOURNEY))
        descriptions = (
            BOARD_SENSORS if coordinator.subentry.subentry_type == SUBENTRY_STATION else TRIP_SENSORS
        )
        async_add_entities(
            [
                BoardSensor(coordinator, d)
                for d in descriptions
                if tracking or not d.needs_tracking
            ],
            config_subentry_id=subentry_id,
        )

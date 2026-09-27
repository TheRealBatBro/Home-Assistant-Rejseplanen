"""Live position of the next train/bus (only for boards and trips with tracking on)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_TRACK_JOURNEY
from .coordinator import RejseplanenConfigEntry
from .entity import AnyCoordinator, RejseplanenEntity


class VehicleTracker(RejseplanenEntity[AnyCoordinator], TrackerEntity):
    _attr_translation_key = "vehicle"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: AnyCoordinator) -> None:
        super().__init__(coordinator, "vehicle")

    def _position(self) -> tuple[float, float] | None:
        journey = self.coordinator.data.journey if self.coordinator.data else None
        if journey is None:
            return None
        if journey.latitude is not None and journey.longitude is not None:
            return journey.latitude, journey.longitude
        # No live position reported: use the last stop it passed.
        stop = journey.last_stop or journey.stop_after(dt_util.utcnow())
        if stop and stop.lat is not None and stop.lon is not None:
            return stop.lat, stop.lon
        return None

    @property
    def latitude(self) -> float | None:
        pos = self._position()
        return pos[0] if pos else None

    @property
    def longitude(self) -> float | None:
        pos = self._position()
        return pos[1] if pos else None

    @property
    def location_accuracy(self) -> float:
        journey = self.coordinator.data.journey if self.coordinator.data else None
        return 50 if journey and journey.latitude is not None else 500

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        journey = self.coordinator.data.journey if self.coordinator.data else None
        if journey is None:
            return None
        return {
            "line": journey.product.name if journey.product else None,
            "direction": journey.direction,
            "last_stop": journey.last_stop.name if journey.last_stop else None,
            "next_stop": journey.next_stop.name if journey.next_stop else None,
            "position_time": journey.position_time.isoformat() if journey.position_time else None,
            "live_position": journey.latitude is not None,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RejseplanenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        if coordinator.options.get(CONF_TRACK_JOURNEY):
            async_add_entities([VehicleTracker(coordinator)], config_subentry_id=subentry_id)

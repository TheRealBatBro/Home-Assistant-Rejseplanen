"""Event entity: departed, delayed, cancelled, track changed, new disruption..."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .coordinator import RejseplanenConfigEntry
from .entity import AnyCoordinator, RejseplanenEntity

# Keep event attributes small: summaries, not whole boards.
_KEEP = ("line", "name", "direction", "scheduled", "expected", "delay", "track", "cancelled")


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if dep := payload.get("departure"):
        data.update({k: dep.get(k) for k in _KEEP})
        data["stop"] = payload.get("stop")
    if trip := payload.get("trip"):
        data.update(
            {
                "departure": trip["departure"]["expected"],
                "arrival": trip["arrival"]["expected"],
                "delay": max(trip["departure"]["delay"] or 0, trip["arrival"]["delay"] or 0),
                "track": trip["departure"]["track"],
                "line": trip["legs"][0]["name"] if trip["legs"] else None,
                "cancelled": trip["cancelled"],
            }
        )
    if msg := payload.get("message"):
        data.update({"head": msg.get("head"), "text": msg.get("text")})
    for key in ("previous_delay", "previous_track"):
        if key in payload:
            data[key] = payload[key]
    return data


class BoardEvent(RejseplanenEntity[AnyCoordinator], EventEntity):
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "board_event"

    def __init__(self, coordinator: AnyCoordinator) -> None:
        super().__init__(coordinator, "event")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._on_event))

    @callback
    def _on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._trigger_event(event_type, _summary(payload))
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RejseplanenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([BoardEvent(coordinator)], config_subentry_id=subentry_id)

"""Problem sensor: on when delays, cancellations or disruptions affect a board or trip."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BoardCoordinator, RejseplanenConfigEntry
from .entity import AnyCoordinator, RejseplanenEntity


class ProblemSensor(RejseplanenEntity[AnyCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, coordinator: AnyCoordinator) -> None:
        super().__init__(coordinator, "problem")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.has_problem()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        c = self.coordinator
        if c.data is None:
            return None
        reasons: list[str] = []
        if isinstance(c, BoardCoordinator):
            for row in c.cancelled():
                reasons.append(f"{row.product.name} {row.scheduled:%H:%M}: cancelled")
            for row in c.delayed():
                reasons.append(f"{row.product.name} {row.scheduled:%H:%M}: +{row.delay} min")
        else:
            status = c.status()
            if status != "on_time":
                reasons.append(status)
        reasons.extend(m.head or m.text for m in c.data.messages)
        return {"status": c.status(), "reasons": reasons}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RejseplanenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([ProblemSensor(coordinator)], config_subentry_id=subentry_id)

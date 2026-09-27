"""Shared entity bits."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BOARD_ARRIVALS, CONF_BOARD, DOMAIN, SUBENTRY_STATION
from .coordinator import BoardCoordinator, RejseplanenConfigEntry, TripCoordinator

type AnyCoordinator = BoardCoordinator | TripCoordinator


def hub_device(entry: RejseplanenConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Rejseplanen",
        manufacturer="Rejseplanen",
        model="API 2.0",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://labs.rejseplanen.dk/",
    )


def board_device(coordinator: AnyCoordinator) -> DeviceInfo:
    sub = coordinator.subentry
    if sub.subentry_type == SUBENTRY_STATION:
        model = "Arrival board" if sub.data.get(CONF_BOARD) == BOARD_ARRIVALS else "Departure board"
    else:
        model = "Trip"
    return DeviceInfo(
        identifiers={(DOMAIN, sub.subentry_id)},
        name=sub.title,
        manufacturer="Rejseplanen",
        model=model,
        entry_type=DeviceEntryType.SERVICE,
    )


class RejseplanenEntity[C: AnyCoordinator](CoordinatorEntity[C]):
    """An entity belonging to one station board or trip."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.subentry.subentry_id}_{key}"
        self._attr_device_info = board_device(coordinator)

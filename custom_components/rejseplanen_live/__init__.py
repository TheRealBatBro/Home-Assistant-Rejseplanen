"""Rejseplanen Live: Danish public transport departures, trips and disruptions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import RejseplanenClient
from .budget import CallBudget
from .const import (
    CARD_URL,
    CONF_API_KEY,
    CONF_LANGUAGE,
    CONF_MIN_INTERVAL,
    CONF_MONTHLY_QUOTA,
    DEFAULT_LANGUAGE,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_MONTHLY_QUOTA,
    DOMAIN,
    SUBENTRY_STATION,
    SUBENTRY_TRIP,
)
from .coordinator import BoardCoordinator, RejseplanenConfigEntry, RejseplanenRuntime, TripCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions and the departure board card."""
    async_setup_services(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    if hass.http is None:
        return
    integration = await async_get_integration(hass, DOMAIN)
    path = Path(__file__).parent / "frontend" / "rejseplanen-live-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    try:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    except (ImportError, KeyError):
        _LOGGER.debug("Frontend not loaded; the card has to be added as a resource by hand")


async def async_setup_entry(hass: HomeAssistant, entry: RejseplanenConfigEntry) -> bool:
    options = {**entry.data, **entry.options}
    budget = CallBudget(
        hass,
        entry.entry_id,
        int(options.get(CONF_MONTHLY_QUOTA, DEFAULT_MONTHLY_QUOTA)),
        timedelta(seconds=int(options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL))),
    )
    await budget.async_load()
    client = RejseplanenClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        language=options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        on_request=budget.record,
    )
    runtime = RejseplanenRuntime(client=client, budget=budget)
    entry.runtime_data = runtime

    # Create every coordinator first so the budget knows all boards before handing out intervals.
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_STATION:
            runtime.coordinators[subentry.subentry_id] = BoardCoordinator(hass, entry, subentry, runtime)
        elif subentry.subentry_type == SUBENTRY_TRIP:
            runtime.coordinators[subentry.subentry_id] = TripCoordinator(hass, entry, subentry, runtime)
    for coordinator in runtime.coordinators.values():
        coordinator.update_interval = budget.next_interval(coordinator.plan_id)

    await asyncio.gather(
        *(c.async_config_entry_first_refresh() for c in runtime.coordinators.values())
    )
    for coordinator in runtime.coordinators.values():
        coordinator.async_start_tick()

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: RejseplanenConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RejseplanenConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.budget.async_save()
    return unloaded

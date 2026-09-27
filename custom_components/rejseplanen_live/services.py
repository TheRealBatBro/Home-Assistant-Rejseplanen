"""Actions that return data: ad-hoc boards, trip planning, journey details, stop search."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import PRODUCTS, RejseplanenClient, RejseplanenError, products_mask
from .const import DOMAIN

ATTR_ENTRY = "config_entry_id"

SERVICE_SEARCH = "search_stops"
SERVICE_BOARD = "get_departures"
SERVICE_TRIP = "plan_trip"
SERVICE_JOURNEY = "get_journey"
SERVICE_MESSAGES = "get_messages"
SERVICE_REFRESH = "refresh"

_ENTRY = {vol.Optional(ATTR_ENTRY): cv.string}
_PRODUCTS = vol.All(cv.ensure_list, [vol.In(list(PRODUCTS))])


def _runtime(hass: HomeAssistant, call: ServiceCall):
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
        and (ATTR_ENTRY not in call.data or e.entry_id == call.data[ATTR_ENTRY])
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entries[0].runtime_data


def _when(value: datetime | None) -> datetime | None:
    """Naive times from the UI are in Home Assistant's time zone."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=dt_util.get_default_time_zone())


async def _resolve(client: RejseplanenClient, value: str) -> tuple[str, str | None]:
    """Accept a stop id or a name; names are looked up."""
    value = str(value).strip()
    if value.isdigit() or value.startswith("A="):
        return value, None
    stops = await client.async_search_stops(value, limit=1)
    if not stops:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="stop_not_found",
            translation_placeholders={"stop": value},
        )
    return stops[0].id, stops[0].name


def async_setup_services(hass: HomeAssistant) -> None:
    async def search(call: ServiceCall) -> ServiceResponse:
        client = _runtime(hass, call).client
        try:
            if call.data.get("query"):
                stops = await client.async_search_stops(call.data["query"], call.data["limit"])
            else:
                stops = await client.async_nearby_stops(
                    call.data.get("latitude", hass.config.latitude),
                    call.data.get("longitude", hass.config.longitude),
                    call.data["radius"],
                    call.data["limit"],
                )
        except RejseplanenError as err:
            raise HomeAssistantError(str(err)) from err
        return {"stops": [s.as_dict() for s in stops]}

    async def board(call: ServiceCall) -> ServiceResponse:
        client = _runtime(hass, call).client
        try:
            stop_id, name = await _resolve(client, call.data["stop"])
            rows = await client.async_board(
                stop_id,
                arrivals=call.data["arrivals"],
                when=_when(call.data.get("time")),
                duration=call.data["duration"],
                max_journeys=call.data.get("max_results"),
                products=products_mask(call.data.get("products")),
            )
        except RejseplanenError as err:
            raise HomeAssistantError(str(err)) from err
        lines = [line.lower() for line in call.data.get("lines", [])]
        if lines:
            rows = [
                r for r in rows
                if {r.product.line.lower(), r.product.name.lower(), r.product.category.lower()} & set(lines)
            ]
        return {
            "stop": name or (rows[0].stop if rows else stop_id),
            "stop_id": stop_id,
            "departures": [r.as_dict() for r in rows],
        }

    async def trip(call: ServiceCall) -> ServiceResponse:
        client = _runtime(hass, call).client
        try:
            origin, origin_name = await _resolve(client, call.data["origin"])
            dest, dest_name = await _resolve(client, call.data["destination"])
            trips = await client.async_trips(
                origin,
                dest,
                when=_when(call.data.get("time")) or dt_util.utcnow(),
                arrive_by=call.data["arrive_by"],
                num=call.data["results"],
                max_changes=call.data.get("max_changes"),
                products=products_mask(call.data.get("products")),
            )
        except RejseplanenError as err:
            raise HomeAssistantError(str(err)) from err
        return {
            "origin": origin_name or origin,
            "destination": dest_name or dest,
            "trips": [t.as_dict() for t in trips],
        }

    async def journey(call: ServiceCall) -> ServiceResponse:
        client = _runtime(hass, call).client
        try:
            result = await client.async_journey(call.data["journey_ref"])
        except RejseplanenError as err:
            raise HomeAssistantError(str(err)) from err
        return {"journey": result.as_dict() if result else None}

    async def messages(call: ServiceCall) -> ServiceResponse:
        client = _runtime(hass, call).client
        try:
            stop_id, name = await _resolve(client, call.data["stop"])
            found = await client.async_messages(stop_id)
        except RejseplanenError as err:
            raise HomeAssistantError(str(err)) from err
        return {"stop": name or stop_id, "messages": [m.as_dict() for m in found]}

    async def refresh(call: ServiceCall) -> None:
        runtime = _runtime(hass, call)
        for coordinator in runtime.coordinators.values():
            await coordinator.async_request_refresh()

    services: list[tuple[str, Any, vol.Schema, SupportsResponse]] = [
        (
            SERVICE_SEARCH,
            search,
            vol.Schema(
                {
                    **_ENTRY,
                    vol.Optional("query"): cv.string,
                    vol.Optional("latitude"): cv.latitude,
                    vol.Optional("longitude"): cv.longitude,
                    vol.Optional("radius", default=1500): vol.All(vol.Coerce(int), vol.Range(100, 10000)),
                    vol.Optional("limit", default=10): vol.All(vol.Coerce(int), vol.Range(1, 50)),
                }
            ),
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_BOARD,
            board,
            vol.Schema(
                {
                    **_ENTRY,
                    vol.Required("stop"): cv.string,
                    vol.Optional("arrivals", default=False): cv.boolean,
                    vol.Optional("time"): cv.datetime,
                    vol.Optional("duration", default=60): vol.All(vol.Coerce(int), vol.Range(1, 1440)),
                    vol.Optional("max_results"): vol.All(vol.Coerce(int), vol.Range(1, 200)),
                    vol.Optional("products"): _PRODUCTS,
                    vol.Optional("lines", default=[]): vol.All(cv.ensure_list, [cv.string]),
                }
            ),
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_TRIP,
            trip,
            vol.Schema(
                {
                    **_ENTRY,
                    vol.Required("origin"): cv.string,
                    vol.Required("destination"): cv.string,
                    vol.Optional("time"): cv.datetime,
                    vol.Optional("arrive_by", default=False): cv.boolean,
                    vol.Optional("results", default=4): vol.All(vol.Coerce(int), vol.Range(1, 10)),
                    vol.Optional("max_changes"): vol.All(vol.Coerce(int), vol.Range(0, 10)),
                    vol.Optional("products"): _PRODUCTS,
                }
            ),
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_JOURNEY,
            journey,
            vol.Schema({**_ENTRY, vol.Required("journey_ref"): cv.string}),
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_MESSAGES,
            messages,
            vol.Schema({**_ENTRY, vol.Required("stop"): cv.string}),
            SupportsResponse.ONLY,
        ),
        (SERVICE_REFRESH, refresh, vol.Schema({**_ENTRY}), SupportsResponse.NONE),
    ]
    for name, handler, schema, response in services:
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=response)

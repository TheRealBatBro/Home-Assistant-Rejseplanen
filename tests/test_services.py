"""Response actions."""

from __future__ import annotations

import pytest

from custom_components.rejseplanen_live.const import DOMAIN
from homeassistant.exceptions import ServiceValidationError

from .conftest import NO_RESULT, calls, mock_api


async def _call(hass, service, data):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True)


async def test_get_departures_by_name(hass, aioclient_mock, setup_integration):
    response = await _call(hass, "get_departures", {"stop": "Roskilde", "lines": ["IC"]})
    assert response["stop"] == "Roskilde St."
    assert response["stop_id"] == "8600617"
    assert {d["name"] for d in response["departures"]} == {"IC 50141", "IC 50124"}


async def test_get_departures_by_id_skips_lookup(hass, aioclient_mock, setup_integration):
    before = len(calls(aioclient_mock, "location.name"))
    response = await _call(hass, "get_departures", {"stop": "8600617", "arrivals": True})
    assert len(response["departures"]) == 16
    assert len(calls(aioclient_mock, "location.name")) == before
    assert calls(aioclient_mock, "arrivalBoard")


async def test_plan_trip(hass, aioclient_mock, setup_integration):
    response = await _call(hass, "plan_trip", {"origin": "8600617", "destination": "8600626", "results": 3})
    assert len(response["trips"]) == 3
    assert response["trips"][1]["changes"] == 1
    assert response["trips"][1]["reachable"] is False


async def test_journey_and_messages(hass, aioclient_mock, setup_integration):
    journey = await _call(hass, "get_journey", {"journey_ref": "2|#VN#1#"})
    assert journey["journey"]["last_stop"] == "Ringsted St."
    messages = await _call(hass, "get_messages", {"stop": "8600617"})
    assert messages["messages"][0]["head"] == "Signalfejl ved Høje Taastrup"


async def test_search_near_home(hass, aioclient_mock, setup_integration):
    response = await _call(hass, "search_stops", {})
    assert response["stops"][0]["name"] == "Roskilde St."
    assert calls(aioclient_mock, "location.nearbystops")


async def test_unknown_stop(hass, aioclient_mock, setup_integration):
    mock_api(aioclient_mock, locations=NO_RESULT)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "get_departures", {"stop": "Atlantis"})


async def test_refresh(hass, aioclient_mock, setup_integration):
    before = len(calls(aioclient_mock, "departureBoard"))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "departureBoard")) > before

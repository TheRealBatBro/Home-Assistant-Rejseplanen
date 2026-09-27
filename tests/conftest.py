"""Fixtures: a fake Rejseplanen API served through aioclient_mock.

departure_board_roskilde.xml and location_roskilde.xml are real API responses
(from the MIT-licensed pyRejseplan test data); the trip, journey and HIM files are
synthetic but follow the published rest.xsd. The clock is frozen shortly before
the first departure on that board.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rejseplanen_live.api import BASE_URL
from custom_components.rejseplanen_live.const import (
    CONF_API_KEY,
    CONF_BOARD,
    CONF_DEST_ID,
    CONF_DEST_NAME,
    CONF_ORIGIN_ID,
    CONF_ORIGIN_NAME,
    CONF_STATION_MESSAGES,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_TRACK_JOURNEY,
    DOMAIN,
    SUBENTRY_STATION,
    SUBENTRY_TRIP,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2025-05-23 09:05:00+00:00"  # 11:05 in Copenhagen
API_KEY = "test-access-key"

_ERROR = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<hafasRestError xmlns="http://hacon.de/hafas/proxy/hafas-proxy" errorCode="{code}" errorText="{text}"/>'
)
AUTH_ERROR = _ERROR.format(code="API_AUTH", text="access denied for accessId")
QUOTA_ERROR = _ERROR.format(code="API_QUOTA", text="quota exceeded")
NO_RESULT = _ERROR.format(code="SVC_NO_RESULT", text="No result found")
DATAINFO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<DataInfo xmlns="http://hacon.de/hafas/proxy/hafas-proxy" serverVersion="2.49.1" requestId="t"/>'
)


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


BOARD = load("departure_board_roskilde.xml")
TRIPS = load("trip_roskilde_kbh.xml")
JOURNEY = load("journey_ic50141.xml")
HIM = load("him_roskilde.xml")
LOCATIONS = load("location_roskilde.xml")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def frozen(freezer):
    freezer.move_to(NOW)
    return freezer


def mock_api(
    aioclient_mock,
    *,
    board: str = BOARD,
    trips: str = TRIPS,
    journey: str = JOURNEY,
    him: str = HIM,
    locations: str = LOCATIONS,
    datainfo: str = DATAINFO,
    status: int = 200,
) -> None:
    aioclient_mock.clear_requests()
    for service, body in (
        ("departureBoard", board),
        ("arrivalBoard", board.replace("<Departure ", "<Arrival ").replace("</Departure>", "</Arrival>")),
        ("trip", trips),
        ("journeyDetail", journey),
        ("himsearch", him),
        ("location.name", locations),
        ("location.nearbystops", locations),
        ("datainfo", datainfo),
    ):
        aioclient_mock.get(BASE_URL + service, text=body, status=status)


def calls(aioclient_mock, service: str) -> list:
    return [c for c in aioclient_mock.mock_calls if str(c[1].path).endswith("/" + service)]


STATION_DATA = {
    CONF_STOP_ID: "8600617",
    CONF_STOP_NAME: "Roskilde St.",
    CONF_BOARD: "departures",
    "duration": 90,
    "max_departures": 10,
    "walk_minutes": 0,
    "delay_threshold": 3,
    CONF_STATION_MESSAGES: True,
    CONF_TRACK_JOURNEY: False,
}

TRIP_DATA = {
    CONF_ORIGIN_ID: "8600617",
    CONF_ORIGIN_NAME: "Roskilde St.",
    CONF_DEST_ID: "8600626",
    CONF_DEST_NAME: "København H",
    "num_trips": 4,
    "walk_minutes": 0,
    "delay_threshold": 3,
    CONF_TRACK_JOURNEY: True,
}


@pytest.fixture
def config_entry():
    return MockConfigEntry(
        domain=DOMAIN,
        title="Rejseplanen",
        data={CONF_API_KEY: API_KEY},
        subentries_data=[
            {
                "subentry_type": SUBENTRY_STATION,
                "title": "Roskilde St.",
                "data": STATION_DATA,
                "subentry_id": "station1",
                "unique_id": None,
            },
            {
                "subentry_type": SUBENTRY_TRIP,
                "title": "Roskilde St. → København H",
                "data": TRIP_DATA,
                "subentry_id": "trip1",
                "unique_id": "8600617_8600626",
            },
        ],
    )


@pytest.fixture
async def setup_integration(hass, aioclient_mock, config_entry):
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry

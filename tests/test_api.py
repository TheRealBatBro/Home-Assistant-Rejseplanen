"""Parsing against real and schema-shaped responses."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.rejseplanen_live.api import (
    BASE_URL,
    TZ,
    RejseplanenAuthError,
    RejseplanenClient,
    RejseplanenConnectionError,
    RejseplanenQuotaError,
    parse_duration,
    products_mask,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import AUTH_ERROR, NO_RESULT, QUOTA_ERROR, calls, mock_api


def local(h: int, m: int) -> datetime:
    return datetime(2025, 5, 23, h, m, tzinfo=TZ)


@pytest.fixture
def client(hass):
    return RejseplanenClient(async_get_clientsession(hass), "key")


async def test_board_real_response(hass, aioclient_mock, client):
    mock_api(aioclient_mock)
    rows = await client.async_board("8600617", products=products_mask(["regional", "ic"]))
    assert len(rows) == 16

    first = rows[0]
    assert first.product.name == "Re 54541"
    assert first.product.category == "Re"
    assert first.product.type == "regional"
    assert first.product.operator == "DSB"
    assert first.direction == "Holbæk St."
    assert first.scheduled == local(11, 10)
    assert first.realtime == local(11, 12)
    assert first.delay == 2
    assert first.platform == "1"
    assert not first.track_changed
    assert "Cykelmedtagning gratis" in first.notes
    assert all(len(n) > 3 for n in first.notes)  # internal codes (type I) skipped
    assert first.journey_ref.startswith("2|#VN#")

    kalundborg = next(r for r in rows if r.product.name == "Re 51541")
    assert kalundborg.delay == 11
    assert kalundborg.track == "1" and kalundborg.rt_track == "2"
    assert kalundborg.track_changed

    no_rt = next(r for r in rows if r.product.name == "Re 52522")
    assert no_rt.realtime is None and no_rt.delay is None

    local_line = next(r for r in rows if r.product.name == "Lokalbane 210R")
    assert local_line.product.type == "other_train"

    # rows come back sorted by expected time
    assert [r.expected for r in rows] == sorted(r.expected for r in rows)

    call = calls(aioclient_mock, "departureBoard")[0]
    assert call[1].query["id"] == "8600617"
    assert call[1].query["products"] == "5"
    assert call[1].query["format"] == "xml"
    assert call[3]["Authorization"] == "Bearer key"
    assert "accessId" not in call[1].query  # key is never put in the URL


async def test_trips(hass, aioclient_mock, client):
    mock_api(aioclient_mock)
    trips = await client.async_trips("8600617", "8600626", when=local(11, 5))
    assert len(trips) == 3

    direct = trips[0]
    assert direct.transfers == 0
    assert direct.departure.expected == local(11, 15)
    assert direct.departure.delay == 2
    assert direct.arrival.delay == 4
    assert direct.expected_duration == 25
    assert direct.duration == 23
    assert direct.first_ride.product.name == "IC 50141"
    assert direct.first_ride.notes == ["Cykelmedtagning gratis"]
    assert direct.reachable and not direct.cancelled

    change = trips[1]
    assert change.transfers == 1
    assert not change.reachable
    assert [leg.type for leg in change.legs] == ["JNY", "TRSF", "JNY"]
    assert change.legs[2].product.line == "Bx"
    assert change.legs[2].product.type == "s_train"
    assert change.all_messages[0].head == "Sporarbejde"

    assert trips[2].cancelled

    query = calls(aioclient_mock, "trip")[0][1].query
    assert query["originId"] == "8600617" and query["destId"] == "8600626"
    assert query["date"] == "2025-05-23" and query["time"] == "11:05"


async def test_journey(hass, aioclient_mock, client):
    mock_api(aioclient_mock)
    journey = await client.async_journey("ref")
    assert journey.product.name == "IC 50141"
    assert journey.direction == "Østerport St."
    assert len(journey.stops) == 5
    assert journey.last_stop.name == "Ringsted St."
    assert journey.next_stop.name == "Roskilde St."
    assert journey.latitude == 55.55 and journey.longitude == 11.95
    assert journey.stops[3].delay == 2
    assert journey.position_time.isoformat() == "2025-05-23T11:04:30+02:00"


async def test_messages(hass, aioclient_mock, client):
    mock_api(aioclient_mock)
    messages = await client.async_messages("8600617")
    assert len(messages) == 2
    signal = messages[0]
    assert signal.head == "Signalfejl ved Høje Taastrup"
    assert signal.active
    assert signal.start == local(9, 30)
    assert signal.affected_stops == ["Roskilde St.", "Høje Taastrup St."]
    assert not messages[1].active


async def test_stop_search_dedupes_masts(hass, aioclient_mock, client):
    mock_api(aioclient_mock)
    stops = await client.async_search_stops("Roskilde")
    names = [s.name for s in stops]
    assert names.count("Roskilde St.") == 1
    assert stops[0].id == "8600617"
    assert "regional" in stops[0].as_dict()["products"]


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        (AUTH_ERROR, 400, RejseplanenAuthError),
        ("", 401, RejseplanenAuthError),
        (QUOTA_ERROR, 400, RejseplanenQuotaError),
        ("", 429, RejseplanenQuotaError),
    ],
)
async def test_errors(hass, aioclient_mock, client, body, status, error):
    aioclient_mock.get(BASE_URL + "departureBoard", text=body, status=status)
    with pytest.raises(error):
        await client.async_board("1")


async def test_no_result_is_empty(hass, aioclient_mock, client):
    aioclient_mock.get(BASE_URL + "trip", text=NO_RESULT, status=400)
    assert await client.async_trips("1", "2") == []


async def test_connection_error(hass, aioclient_mock, client):
    aioclient_mock.get(BASE_URL + "departureBoard", exc=TimeoutError())
    with pytest.raises(RejseplanenConnectionError):
        await client.async_board("1")


def test_parse_duration():
    assert parse_duration("PT23M") == 23
    assert parse_duration("PT1H5M") == 65
    assert parse_duration("P1DT2H") == 1560
    assert parse_duration("PT45S") == 1
    assert parse_duration("junk") is None

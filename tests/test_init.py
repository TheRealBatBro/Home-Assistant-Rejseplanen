"""Setup, entities, events and budget behaviour of a loaded entry."""

from __future__ import annotations

from datetime import timedelta
import re

from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.rejseplanen_live.const import DOMAIN, EVENT_NAME
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import AUTH_ERROR, BOARD, QUOTA_ERROR, calls, mock_api

S = "sensor.roskilde_st_"
T = "sensor.roskilde_st_kobenhavn_h_"


def state(hass: HomeAssistant, entity_id: str):
    st = hass.states.get(entity_id)
    assert st is not None, entity_id
    return st


async def test_station_entities(hass, setup_integration):
    nxt = state(hass, S + "next_departure")
    assert nxt.state == "2025-05-23T09:12:00+00:00"  # Re 54541, 11:10 +2
    assert nxt.attributes["name"] == "Re 54541"
    assert nxt.attributes["direction"] == "Holbæk St."
    assert nxt.attributes["delay"] == 2
    assert nxt.attributes["stop"] == "Roskilde St."
    assert len(nxt.attributes["departures"]) == 10
    kalundborg = next(d for d in nxt.attributes["departures"] if d["name"] == "Re 51541")
    assert kalundborg["track_changed"] is True and kalundborg["track"] == "2"

    assert state(hass, S + "following_departure").state == "2025-05-23T09:15:00+00:00"
    assert state(hass, S + "minutes_until_departure").state == "7"
    assert state(hass, S + "line").state == "Re 54541"
    assert state(hass, S + "direction").state == "Holbæk St."
    assert state(hass, S + "delay").state == "2"
    assert state(hass, S + "track").state == "1"
    assert state(hass, S + "status").state == "on_time"
    assert state(hass, S + "delayed_departures").state == "1"  # Re 51541 +11
    assert state(hass, S + "cancelled_departures").state == "0"
    messages = state(hass, S + "traffic_messages")
    assert messages.state == "1"  # the inactive HIM message is dropped
    assert messages.attributes["headline"] == "Signalfejl ved Høje Taastrup"

    problem = state(hass, "binary_sensor.roskilde_st_problem")
    assert problem.state == "on"
    assert "Re 51541 11:21: +11 min" in problem.attributes["reasons"]

    # no journey tracking on this board
    assert hass.states.get(S + "vehicle_position") is None
    assert hass.states.get("device_tracker.roskilde_st_vehicle") is None


async def test_trip_entities(hass, setup_integration):
    dep = state(hass, T + "departure")
    assert dep.state == "2025-05-23T09:15:00+00:00"
    assert dep.attributes["changes"] == 0
    assert len(dep.attributes["trips"]) == 3
    assert len(dep.attributes["later_trips"]) == 1  # the cancelled one is left out
    assert state(hass, T + "arrival").state == "2025-05-23T09:40:00+00:00"
    assert state(hass, T + "leave_by").state == "2025-05-23T09:15:00+00:00"
    assert state(hass, T + "travel_time").state == "25"
    assert state(hass, T + "changes").state == "0"
    assert state(hass, T + "departure_delay").state == "2"
    assert state(hass, T + "arrival_delay").state == "4"
    assert state(hass, T + "track").state == "3"
    assert state(hass, T + "line").state == "IC 50141"
    assert state(hass, T + "status").state == "delayed"
    assert state(hass, "binary_sensor.roskilde_st_kobenhavn_h_problem").state == "on"

    position = state(hass, T + "vehicle_position")
    assert position.state == "Ringsted St."
    assert position.attributes["journey"]["next_stop"] == "Roskilde St."
    tracker = state(hass, "device_tracker.roskilde_st_kobenhavn_h_vehicle")
    assert tracker.attributes["latitude"] == 55.55
    assert tracker.attributes["longitude"] == 11.95
    assert tracker.attributes["next_stop"] == "Roskilde St."


async def test_devices_and_budget(hass, setup_integration, aioclient_mock):
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup_integration.entry_id)
    assert {d.name for d in devices} == {"Rejseplanen", "Roskilde St.", "Roskilde St. → København H"}
    entities = er.async_entries_for_config_entry(er.async_get(hass), setup_integration.entry_id)
    by_subentry = {e.config_subentry_id for e in entities}
    assert by_subentry == {None, "station1", "trip1"}

    # board + station messages + trip + journey
    assert len(aioclient_mock.mock_calls) == 4
    assert state(hass, "sensor.rejseplanen_api_calls_this_month").state == "4"
    assert state(hass, "sensor.rejseplanen_api_calls_today").state == "4"
    assert state(hass, "sensor.rejseplanen_api_calls_left_this_month").state == "49996"
    assert state(hass, "sensor.rejseplanen_update_interval").state == "60"
    usage = state(hass, "sensor.rejseplanen_api_calls_this_month").attributes["by_service"]
    assert usage == {"departureBoard": 1, "himsearch": 1, "trip": 1, "journeyDetail": 1}


async def test_change_events(hass, setup_integration, aioclient_mock):
    events = async_capture_events(hass, EVENT_NAME)
    board = BOARD.replace('rtTime="11:12:00"', 'rtTime="11:20:00"')
    board = board.replace('<Departure name="IC 50141" type="ST"', '<Departure name="IC 50141" cancelled="true" type="ST"')
    board = re.sub(r'(<Departure name="Re 52522"[^>]*?)rtTrack="5"', r'\1rtTrack="6"', board)
    mock_api(aioclient_mock, board=board)

    await setup_integration.runtime_data.coordinators["station1"].async_refresh()
    await hass.async_block_till_done()

    kinds = {(e.data["type"], (e.data.get("departure") or {}).get("name")) for e in events}
    assert ("delayed", "Re 54541") in kinds
    assert ("cancelled", "IC 50141") in kinds
    assert ("track_changed", "Re 52522") in kinds
    track = next(e for e in events if e.data["type"] == "track_changed")
    assert track.data["previous_track"] == "5"
    assert track.data["departure"]["track"] == "6"

    ev = state(hass, "event.roskilde_st_traffic_event")
    assert ev.attributes["event_type"] in {"delayed", "cancelled", "track_changed"}
    assert state(hass, S + "cancelled_departures").state == "1"
    assert state(hass, S + "status").state == "cancelled"  # IC 50141 is now first on the board


async def test_departed_event(hass, setup_integration, frozen):
    events = async_capture_events(hass, EVENT_NAME)
    frozen.move_to("2025-05-23 09:12:20+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    departed = [e for e in events if e.data["type"] == "departed"]
    assert departed and departed[0].data["departure"]["name"] == "Re 54541"
    # announced once only
    frozen.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len([e for e in events if e.data["type"] == "departed" and e.data.get("departure", {}).get("name") == "Re 54541"]) == 1
    # the board moved on
    assert state(hass, S + "line").state == "IC 50141"


async def test_auth_failure_starts_reauth(hass, aioclient_mock, config_entry):
    mock_api(aioclient_mock, board=AUTH_ERROR, trips=AUTH_ERROR, status=400)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_quota_failure_retries(hass, aioclient_mock, config_entry):
    mock_api(aioclient_mock, board=QUOTA_ERROR, trips=QUOTA_ERROR, status=400)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_outside_active_hours_spends_no_calls(hass, aioclient_mock, config_entry, frozen):
    config_entry.add_to_hass(hass)
    for sub in list(config_entry.subentries.values()):
        hass.config_entries.async_update_subentry(
            config_entry, sub, data={**sub.data, "active_start": "06:00:00", "active_end": "11:30:00"}
        )
    mock_api(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    first = len(aioclient_mock.mock_calls)

    frozen.move_to("2025-05-23 10:00:00+00:00")  # 12:00, after the window
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    frozen.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    after = len(aioclient_mock.mock_calls)
    # at most the polls that were already due inside the window
    assert after - first <= 4
    frozen.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(aioclient_mock.mock_calls) == after


async def test_unload_and_subentry_reload(hass, setup_integration):
    entry = setup_integration
    sub = entry.subentries["station1"]
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "track_journey": True})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("device_tracker.roskilde_st_vehicle") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in {e.domain for e in hass.config_entries.async_entries() if e.state is ConfigEntryState.LOADED}


async def test_quota_error_backs_off(hass, aioclient_mock, setup_integration):
    coordinator = setup_integration.runtime_data.coordinators["station1"]
    assert coordinator.update_interval == timedelta(seconds=60)
    mock_api(aioclient_mock, board=QUOTA_ERROR, status=400)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.update_interval == timedelta(minutes=60)
    mock_api(aioclient_mock)
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_diagnostics(hass, setup_integration):
    from custom_components.rejseplanen_live.diagnostics import async_get_config_entry_diagnostics

    diag = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert diag["entry"]["api_key"] == "**REDACTED**"
    assert diag["budget"]["calls_month"] == 4
    assert diag["boards"]["station1"]["departures"][0]["name"] == "Re 54541"
    assert diag["boards"]["trip1"]["journey"]["last_stop"] == "Ringsted St."

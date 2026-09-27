"""Config flow, options and subentry flows."""

from __future__ import annotations

from custom_components.rejseplanen_live.const import CONF_API_KEY, DOMAIN
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from .conftest import API_KEY, AUTH_ERROR, calls, mock_api


async def test_user_flow(hass, aioclient_mock):
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f" {API_KEY} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: API_KEY}
    assert calls(aioclient_mock, "datainfo")


async def test_user_flow_bad_key(hass, aioclient_mock):
    mock_api(aioclient_mock, datainfo=AUTH_ERROR, status=400)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "bad"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_key(hass, aioclient_mock, setup_integration):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass, aioclient_mock, setup_integration):
    entry = setup_integration
    result = await entry.start_reauth_flow(hass)
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new-key"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new-key"


async def test_options(hass, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"monthly_quota": 100000, "min_interval": 120, "language": "en"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.budget.quota == 100000
    assert entry.runtime_data.client._language == "en"


async def test_station_subentry(hass, aioclient_mock, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"query": "Roskilde"})
    assert result["step_id"] == "pick"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"stop_id": "8600617"})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "board": "arrivals",
            "products": ["regional", "ic"],
            "lines": "Re",
            "direction": "Roskilde",
            "duration": 60,
            "max_departures": 8,
            "walk_minutes": 5,
            "delay_threshold": 4,
            "hide_cancelled": True,
            "station_messages": False,
            "track_journey": False,
            "schedule": {
                "active_start": "07:00:00",
                "active_end": "09:00:00",
                "active_days": ["mon", "tue", "wed", "thu", "fri"],
                "idle_interval": 0,
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roskilde St. (ankomster) → Roskilde St."
    data = result["data"]
    assert data["stop_id"] == "8600617"
    assert data["direction_id"] == "8600617"
    assert data["active_start"] == "07:00:00"
    assert data["walk_minutes"] == 5
    assert "schedule" not in data
    await hass.async_block_till_done()
    assert len(entry.runtime_data.coordinators) == 3  # reloaded with the new board


async def test_station_nearby_search(hass, aioclient_mock, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pick"
    query = calls(aioclient_mock, "location.nearbystops")[0][1].query
    assert float(query["originCoordLat"]) == hass.config.latitude


async def test_trip_subentry(hass, aioclient_mock, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "trip"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"origin_query": "Roskilde", "destination_query": "Roskilde"}
    )
    assert result["step_id"] == "pick"
    same = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"origin_id": "8600617", "destination_id": "8600617"}
    )
    assert same["errors"] == {"base": "same_stop"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"origin_id": "8600617", "destination_id": "8600794"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "products": [],
            "max_changes": 1,
            "num_trips": 3,
            "walk_minutes": 10,
            "delay_threshold": 3,
            "track_journey": True,
            "schedule": {"active_start": "00:00:00", "active_end": "00:00:00", "active_days": ["mon"], "idle_interval": 30},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roskilde St. → Roskilde Festivalplads St."
    assert result["data"]["max_changes"] == 1
    assert result["data"]["idle_interval"] == 30
    await hass.async_block_till_done()
    assert len(entry.runtime_data.coordinators) == 3


async def test_station_reconfigure(hass, aioclient_mock, setup_integration):
    entry = setup_integration
    sub = entry.subentries["station1"]
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "board": "departures",
            "products": [],
            "lines": "",
            "direction": "",
            "duration": 120,
            "max_departures": 5,
            "walk_minutes": 0,
            "delay_threshold": 5,
            "hide_cancelled": False,
            "station_messages": True,
            "track_journey": True,
            "schedule": {"active_start": "00:00:00", "active_end": "00:00:00", "active_days": ["mon"], "idle_interval": 0},
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries["station1"].data["max_departures"] == 5
    assert entry.subentries["station1"].data["stop_id"] == "8600617"
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.roskilde_st_vehicle") is not None

"""Config flow: the API key is the entry; stations and trips are subentries."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .api import (
    PRODUCTS,
    RejseplanenAuthError,
    RejseplanenClient,
    RejseplanenError,
    Stop,
    product_names,
)
from .const import (
    BOARD_ARRIVALS,
    BOARD_DEPARTURES,
    CONF_ACTIVE_DAYS,
    CONF_ACTIVE_END,
    CONF_ACTIVE_START,
    CONF_API_KEY,
    CONF_BOARD,
    CONF_DELAY_THRESHOLD,
    CONF_DEST_ID,
    CONF_DEST_NAME,
    CONF_DIRECTION_ID,
    CONF_DIRECTION_NAME,
    CONF_DURATION,
    CONF_HIDE_CANCELLED,
    CONF_IDLE_INTERVAL,
    CONF_LANGUAGE,
    CONF_LINES,
    CONF_MAX_CHANGES,
    CONF_MAX_DEPARTURES,
    CONF_MIN_INTERVAL,
    CONF_MONTHLY_QUOTA,
    CONF_NUM_TRIPS,
    CONF_ORIGIN_ID,
    CONF_ORIGIN_NAME,
    CONF_PRODUCTS,
    CONF_STATION_MESSAGES,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_TRACK_JOURNEY,
    CONF_WALK_MINUTES,
    DEFAULT_ACTIVE_END,
    DEFAULT_ACTIVE_START,
    DEFAULT_DELAY_THRESHOLD,
    DEFAULT_DURATION,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_MONTHLY_QUOTA,
    DEFAULT_NUM_TRIPS,
    DEFAULT_WALK_MINUTES,
    DOMAIN,
    SUBENTRY_STATION,
    SUBENTRY_TRIP,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

SCHEDULE = "schedule"
SCHEDULE_KEYS = (CONF_ACTIVE_START, CONF_ACTIVE_END, CONF_ACTIVE_DAYS, CONF_IDLE_INTERVAL)
QUERY = "query"
ORIGIN_QUERY = "origin_query"
DEST_QUERY = "destination_query"
DIRECTION = "direction"


def _number(lo: float, hi: float, step: float = 1, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _multi(options: list[str], key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN, translation_key=key
        )
    )


def _stop_options(stops: list[Stop]) -> list[SelectOptionDict]:
    options = []
    for stop in stops:
        label = stop.name
        if stop.distance is not None:
            label += f" ({stop.distance} m)"
        options.append(SelectOptionDict(value=stop.id, label=label))
    return options


def _schedule_section(values: Mapping[str, Any]) -> section:
    return section(
        vol.Schema(
            {
                vol.Required(
                    CONF_ACTIVE_START, default=values.get(CONF_ACTIVE_START, DEFAULT_ACTIVE_START)
                ): TimeSelector(),
                vol.Required(
                    CONF_ACTIVE_END, default=values.get(CONF_ACTIVE_END, DEFAULT_ACTIVE_END)
                ): TimeSelector(),
                vol.Required(
                    CONF_ACTIVE_DAYS, default=list(values.get(CONF_ACTIVE_DAYS, WEEKDAYS))
                ): _multi(WEEKDAYS, "weekday"),
                vol.Required(
                    CONF_IDLE_INTERVAL,
                    default=values.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL),
                ): _number(0, 240, unit="min"),
            }
        ),
        {"collapsed": True},
    )


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in user_input.items() if k != SCHEDULE}
    data.update(user_input.get(SCHEDULE, {}))
    for key in (CONF_IDLE_INTERVAL, CONF_DURATION, CONF_MAX_DEPARTURES, CONF_WALK_MINUTES,
                CONF_DELAY_THRESHOLD, CONF_NUM_TRIPS):
        if key in data and data[key] is not None:
            data[key] = int(data[key])
    if data.get(CONF_MAX_CHANGES) in ("", None):
        data.pop(CONF_MAX_CHANGES, None)
    elif CONF_MAX_CHANGES in data:
        data[CONF_MAX_CHANGES] = int(data[CONF_MAX_CHANGES])
    return data


class RejseplanenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an API key."""

    VERSION = 1

    async def _validate(self, key: str) -> str | None:
        client = RejseplanenClient(async_get_clientsession(self.hass), key)
        try:
            await client.async_validate()
        except RejseplanenAuthError:
            return "invalid_auth"
        except RejseplanenError as err:
            _LOGGER.debug("Key check failed: %s", err)
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            self._async_abort_entries_match({CONF_API_KEY: key})
            if not (error := await self._validate(key)):
                return self.async_create_entry(title="Rejseplanen", data={CONF_API_KEY: key})
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            errors=errors,
            description_placeholders={"labs_url": "https://labs.rejseplanen.dk/"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            if not (error := await self._validate(key)):
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: key}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RejseplanenOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_STATION: StationSubentryFlow, SUBENTRY_TRIP: TripSubentryFlow}


class RejseplanenOptionsFlow(OptionsFlow):
    """Quota and polling limits."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_MONTHLY_QUOTA: int(user_input[CONF_MONTHLY_QUOTA]),
                    CONF_MIN_INTERVAL: int(user_input[CONF_MIN_INTERVAL]),
                    CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                }
            )
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MONTHLY_QUOTA, default=opts.get(CONF_MONTHLY_QUOTA, DEFAULT_MONTHLY_QUOTA)
                    ): _number(1000, 10_000_000, 1000),
                    vol.Required(
                        CONF_MIN_INTERVAL, default=opts.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)
                    ): _number(30, 3600, 10, "s"),
                    vol.Required(
                        CONF_LANGUAGE, default=opts.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
                    ): SelectSelector(
                        SelectSelectorConfig(options=["da", "en"], translation_key="language")
                    ),
                }
            ),
        )


class _BaseSubentryFlow(ConfigSubentryFlow):
    """Shared helpers: needs the loaded entry's client for stop searches."""

    def _client(self) -> RejseplanenClient | None:
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return None
        return entry.runtime_data.client

    async def _search(self, query: str | None) -> list[Stop]:
        client = self._client()
        assert client is not None
        if query:
            return await client.async_search_stops(query, limit=15)
        return await client.async_nearby_stops(
            self.hass.config.latitude, self.hass.config.longitude, radius=2000, limit=15
        )


class StationSubentryFlow(_BaseSubentryFlow):
    """Add or change a departure/arrival board."""

    def __init__(self) -> None:
        self._stops: list[Stop] = []
        self._stop: Stop | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if self._client() is None:
            return self.async_abort(reason="entry_not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._stops = await self._search(user_input.get(QUERY))
            except RejseplanenError:
                errors["base"] = "cannot_connect"
            else:
                if self._stops:
                    return await self.async_step_pick()
                errors["base"] = "no_stops"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(QUERY): TextSelector()}),
            errors=errors,
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None and CONF_STOP_ID in user_input:
            self._stop = next(s for s in self._stops if s.id == user_input[CONF_STOP_ID])
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STOP_ID, default=self._stops[0].id): SelectSelector(
                        SelectSelectorConfig(options=_stop_options(self._stops), mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    def _schema(self, values: Mapping[str, Any], products: list[str]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_BOARD, default=values.get(CONF_BOARD, BOARD_DEPARTURES)): SelectSelector(
                    SelectSelectorConfig(options=[BOARD_DEPARTURES, BOARD_ARRIVALS], translation_key="board")
                ),
                vol.Optional(CONF_PRODUCTS, default=list(values.get(CONF_PRODUCTS, []))): _multi(products, "product"),
                vol.Optional(CONF_LINES, default=values.get(CONF_LINES, "")): TextSelector(),
                vol.Optional(DIRECTION, default=values.get(CONF_DIRECTION_NAME, "")): TextSelector(),
                vol.Required(CONF_DURATION, default=values.get(CONF_DURATION, DEFAULT_DURATION)): _number(10, 720, unit="min"),
                vol.Required(CONF_MAX_DEPARTURES, default=values.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)): _number(1, 30),
                vol.Required(CONF_WALK_MINUTES, default=values.get(CONF_WALK_MINUTES, DEFAULT_WALK_MINUTES)): _number(0, 120, unit="min"),
                vol.Required(CONF_DELAY_THRESHOLD, default=values.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD)): _number(1, 60, unit="min"),
                vol.Required(CONF_HIDE_CANCELLED, default=values.get(CONF_HIDE_CANCELLED, False)): BooleanSelector(),
                vol.Required(CONF_STATION_MESSAGES, default=values.get(CONF_STATION_MESSAGES, True)): BooleanSelector(),
                vol.Required(CONF_TRACK_JOURNEY, default=values.get(CONF_TRACK_JOURNEY, False)): BooleanSelector(),
                vol.Required(SCHEDULE): _schedule_section(values),
            }
        )

    async def _direction(self, name: str) -> tuple[str | None, str | None, str | None]:
        """Resolve the direction text to a stop; returns (id, name, error)."""
        if not name.strip():
            return None, None, None
        try:
            found = await self._client().async_search_stops(name, limit=1)
        except RejseplanenError:
            return None, None, "cannot_connect"
        if not found:
            return None, None, "direction_not_found"
        return found[0].id, found[0].name, None

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        assert self._stop is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            dir_id, dir_name, error = await self._direction(user_input.get(DIRECTION, ""))
            if error:
                errors[DIRECTION] = error
            else:
                data = _flatten({k: v for k, v in user_input.items() if k != DIRECTION})
                data.update(
                    {
                        CONF_STOP_ID: self._stop.id,
                        CONF_STOP_NAME: self._stop.name,
                        CONF_DIRECTION_ID: dir_id,
                        CONF_DIRECTION_NAME: dir_name,
                    }
                )
                title = self._stop.name
                if data[CONF_BOARD] == BOARD_ARRIVALS:
                    title += " (ankomster)"
                if dir_name:
                    title += f" → {dir_name}"
                return self.async_create_entry(title=title, data=data)
        available = product_names(self._stop.products) or list(PRODUCTS)
        return self.async_show_form(
            step_id="settings",
            data_schema=self._schema(user_input or {}, available),
            errors=errors,
            description_placeholders={"stop": self._stop.name},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if self._client() is None:
            return self.async_abort(reason="entry_not_loaded")
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            dir_id, dir_name, error = await self._direction(user_input.get(DIRECTION, ""))
            if error:
                errors[DIRECTION] = error
            else:
                data = {
                    CONF_STOP_ID: subentry.data[CONF_STOP_ID],
                    CONF_STOP_NAME: subentry.data.get(CONF_STOP_NAME),
                    **_flatten({k: v for k, v in user_input.items() if k != DIRECTION}),
                    CONF_DIRECTION_ID: dir_id,
                    CONF_DIRECTION_NAME: dir_name,
                }
                return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(subentry.data, list(PRODUCTS)),
            errors=errors,
            description_placeholders={"stop": subentry.data.get(CONF_STOP_NAME, subentry.title)},
        )


class TripSubentryFlow(_BaseSubentryFlow):
    """Add or change an A→B trip."""

    def __init__(self) -> None:
        self._origins: list[Stop] = []
        self._dests: list[Stop] = []
        self._origin: Stop | None = None
        self._dest: Stop | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if self._client() is None:
            return self.async_abort(reason="entry_not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._origins = await self._search(user_input[ORIGIN_QUERY])
                self._dests = await self._search(user_input[DEST_QUERY])
            except RejseplanenError:
                errors["base"] = "cannot_connect"
            else:
                if not self._origins:
                    errors[ORIGIN_QUERY] = "no_stops"
                elif not self._dests:
                    errors[DEST_QUERY] = "no_stops"
                else:
                    return await self.async_step_pick()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(ORIGIN_QUERY): TextSelector(), vol.Required(DEST_QUERY): TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and CONF_ORIGIN_ID in user_input:
            if user_input[CONF_ORIGIN_ID] == user_input[CONF_DEST_ID]:
                errors["base"] = "same_stop"
            else:
                self._origin = next(s for s in self._origins if s.id == user_input[CONF_ORIGIN_ID])
                self._dest = next(s for s in self._dests if s.id == user_input[CONF_DEST_ID])
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORIGIN_ID, default=self._origins[0].id): SelectSelector(
                        SelectSelectorConfig(options=_stop_options(self._origins), mode=SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Required(CONF_DEST_ID, default=self._dests[0].id): SelectSelector(
                        SelectSelectorConfig(options=_stop_options(self._dests), mode=SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    def _schema(values: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_PRODUCTS, default=list(values.get(CONF_PRODUCTS, []))): _multi(list(PRODUCTS), "product"),
                vol.Optional(CONF_MAX_CHANGES, description={"suggested_value": values.get(CONF_MAX_CHANGES)}): _number(0, 10),
                vol.Required(CONF_NUM_TRIPS, default=values.get(CONF_NUM_TRIPS, DEFAULT_NUM_TRIPS)): _number(1, 10),
                vol.Required(CONF_WALK_MINUTES, default=values.get(CONF_WALK_MINUTES, DEFAULT_WALK_MINUTES)): _number(0, 120, unit="min"),
                vol.Required(CONF_DELAY_THRESHOLD, default=values.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD)): _number(1, 60, unit="min"),
                vol.Required(CONF_TRACK_JOURNEY, default=values.get(CONF_TRACK_JOURNEY, False)): BooleanSelector(),
                vol.Required(SCHEDULE): _schedule_section(values),
            }
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        assert self._origin is not None and self._dest is not None
        if user_input is not None:
            data = _flatten(user_input)
            data.update(
                {
                    CONF_ORIGIN_ID: self._origin.id,
                    CONF_ORIGIN_NAME: self._origin.name,
                    CONF_DEST_ID: self._dest.id,
                    CONF_DEST_NAME: self._dest.name,
                }
            )
            return self.async_create_entry(
                title=f"{self._origin.name} → {self._dest.name}",
                data=data,
                unique_id=f"{self._origin.id}_{self._dest.id}",
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=self._schema({}),
            description_placeholders={"origin": self._origin.name, "destination": self._dest.name},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {
                key: subentry.data[key]
                for key in (CONF_ORIGIN_ID, CONF_ORIGIN_NAME, CONF_DEST_ID, CONF_DEST_NAME)
            }
            data.update(_flatten(user_input))
            return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(subentry.data),
            description_placeholders={
                "origin": subentry.data[CONF_ORIGIN_NAME],
                "destination": subentry.data[CONF_DEST_NAME],
            },
        )

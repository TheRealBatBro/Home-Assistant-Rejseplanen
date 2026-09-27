"""Constants for Rejseplanen Live."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "rejseplanen_live"

# Main entry options
CONF_API_KEY: Final = "api_key"
CONF_MONTHLY_QUOTA: Final = "monthly_quota"
CONF_MIN_INTERVAL: Final = "min_interval"
CONF_LANGUAGE: Final = "language"

DEFAULT_MONTHLY_QUOTA: Final = 50_000  # free non-commercial tier
DEFAULT_MIN_INTERVAL: Final = 60  # seconds
DEFAULT_LANGUAGE: Final = "da"

# Share of the quota kept back for actions (service calls) and restarts.
QUOTA_RESERVE: Final = 0.05
MAX_INTERVAL: Final = timedelta(minutes=30)
STARVED_INTERVAL: Final = timedelta(minutes=60)
TICK_INTERVAL: Final = timedelta(seconds=30)

# Subentry types
SUBENTRY_STATION: Final = "station"
SUBENTRY_TRIP: Final = "trip"

# Subentry data
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_ORIGIN_ID: Final = "origin_id"
CONF_ORIGIN_NAME: Final = "origin_name"
CONF_DEST_ID: Final = "destination_id"
CONF_DEST_NAME: Final = "destination_name"
CONF_BOARD: Final = "board"
CONF_PRODUCTS: Final = "products"
CONF_LINES: Final = "lines"
CONF_DIRECTION_ID: Final = "direction_id"
CONF_DIRECTION_NAME: Final = "direction_name"
CONF_DURATION: Final = "duration"
CONF_MAX_DEPARTURES: Final = "max_departures"
CONF_WALK_MINUTES: Final = "walk_minutes"
CONF_DELAY_THRESHOLD: Final = "delay_threshold"
CONF_HIDE_CANCELLED: Final = "hide_cancelled"
CONF_TRACK_JOURNEY: Final = "track_journey"
CONF_STATION_MESSAGES: Final = "station_messages"
CONF_ACTIVE_START: Final = "active_start"
CONF_ACTIVE_END: Final = "active_end"
CONF_ACTIVE_DAYS: Final = "active_days"
CONF_IDLE_INTERVAL: Final = "idle_interval"
CONF_MAX_CHANGES: Final = "max_changes"
CONF_NUM_TRIPS: Final = "num_trips"

BOARD_DEPARTURES: Final = "departures"
BOARD_ARRIVALS: Final = "arrivals"

DEFAULT_DURATION: Final = 90
DEFAULT_MAX_DEPARTURES: Final = 10
DEFAULT_WALK_MINUTES: Final = 0
DEFAULT_DELAY_THRESHOLD: Final = 3
DEFAULT_IDLE_INTERVAL: Final = 0  # minutes; 0 = do not poll outside active hours
DEFAULT_NUM_TRIPS: Final = 4
DEFAULT_ACTIVE_START: Final = "00:00:00"
DEFAULT_ACTIVE_END: Final = "00:00:00"  # start == end means all day

WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# How often (in polls) the station HIM search runs next to the board.
MESSAGE_POLL_EVERY: Final = 5

EVENT_NAME: Final = f"{DOMAIN}_event"
EVENT_DEPARTED: Final = "departed"
EVENT_ARRIVED: Final = "arrived"
EVENT_DELAYED: Final = "delayed"
EVENT_CANCELLED: Final = "cancelled"
EVENT_TRACK_CHANGED: Final = "track_changed"
EVENT_DISRUPTION: Final = "disruption"
EVENT_CONNECTION_AT_RISK: Final = "connection_at_risk"
EVENT_TYPES: Final = [
    EVENT_DEPARTED,
    EVENT_ARRIVED,
    EVENT_DELAYED,
    EVENT_CANCELLED,
    EVENT_TRACK_CHANGED,
    EVENT_DISRUPTION,
    EVENT_CONNECTION_AT_RISK,
]

STATUS_ON_TIME: Final = "on_time"
STATUS_DELAYED: Final = "delayed"
STATUS_CANCELLED: Final = "cancelled"
STATUS_CONNECTION_AT_RISK: Final = "connection_at_risk"
STATUS_DISRUPTED: Final = "disrupted"
STATUS_NONE: Final = "no_departures"
STATUSES: Final = [
    STATUS_ON_TIME,
    STATUS_DELAYED,
    STATUS_CANCELLED,
    STATUS_CONNECTION_AT_RISK,
    STATUS_DISRUPTED,
    STATUS_NONE,
]

CARD_URL: Final = f"/{DOMAIN}/rejseplanen-live-card.js"

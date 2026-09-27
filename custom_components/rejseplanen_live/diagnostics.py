"""Diagnostics: budget state and the current boards (API key redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .coordinator import BoardCoordinator, RejseplanenConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RejseplanenConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    budget = runtime.budget
    boards: dict[str, Any] = {}
    for subentry_id, c in runtime.coordinators.items():
        info: dict[str, Any] = {
            "title": c.title,
            "type": c.subentry.subentry_type,
            "options": dict(c.options),
            "polls": c.polls,
            "last_poll": c.last_poll.isoformat() if c.last_poll else None,
            "interval_s": c.update_interval.total_seconds() if c.update_interval else None,
            "active_now": c.plan.is_active(c.last_poll) if c.last_poll else None,
            "status": c.status() if c.data else None,
        }
        if c.data is not None:
            if isinstance(c, BoardCoordinator):
                info["departures"] = [r.as_dict() for r in c.data.departures]
            else:
                info["trips"] = [t.as_dict() for t in c.data.trips]
            info["messages"] = [m.as_dict() for m in c.data.messages]
            info["journey"] = c.data.journey.as_dict() if c.data.journey else None
        boards[subentry_id] = info
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_API_KEY}),
        "options": dict(entry.options),
        "budget": {
            "quota": budget.quota,
            "month": budget.month,
            "calls_month": budget.calls_month,
            "calls_today": budget.calls_today,
            "by_service": budget.by_service,
            "active_interval_s": budget.active_interval().total_seconds(),
            "projected": budget.projected(),
        },
        "boards": boards,
    }

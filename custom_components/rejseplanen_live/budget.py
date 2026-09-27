"""Keeps polling inside the monthly API quota.

Rejseplanen's free tier is 50,000 calls a month for the whole key. Rather than a
fixed interval, every board/trip asks the budget how often it may poll: the
calls left this month are spread evenly over the minutes each board is active
(its active hours and weekdays) until the month ends. A board that is only
watched 07-09 on weekdays therefore gets a much shorter interval than one that
runs all day.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import TZ
from .const import DOMAIN, MAX_INTERVAL, QUOTA_RESERVE, STARVED_INTERVAL, WEEKDAYS

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SAVE_DELAY = 60


@dataclass(slots=True)
class PollPlan:
    """When one board polls and what one poll costs."""

    weight: float = 1.0  # API calls per poll (averaged)
    start: time = time(0, 0)
    end: time = time(0, 0)  # start == end means all day
    days: list[str] = field(default_factory=lambda: list(WEEKDAYS))
    idle_interval: timedelta | None = None  # polling outside the window, None = none

    @property
    def all_day(self) -> bool:
        return self.start == self.end

    def windows_on(self, day: datetime) -> list[tuple[datetime, datetime]]:
        """Active windows that start on the given local day."""
        if WEEKDAYS[day.weekday()] not in self.days:
            return []
        midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.all_day:
            return [(midnight, midnight + timedelta(days=1))]
        start = datetime.combine(midnight.date(), self.start, TZ)
        end = datetime.combine(midnight.date(), self.end, TZ)
        if end <= start:  # crosses midnight
            end += timedelta(days=1)
        return [(start, end)]

    def is_active(self, now: datetime) -> bool:
        local = now.astimezone(TZ)
        for offset in (0, -1):
            for start, end in self.windows_on(local + timedelta(days=offset)):
                if start <= local < end:
                    return True
        return False

    def next_start(self, now: datetime) -> datetime | None:
        local = now.astimezone(TZ)
        for offset in range(8):
            for start, _ in self.windows_on(local + timedelta(days=offset)):
                if start > local:
                    return start
        return None

    def active_minutes(self, now: datetime, until: datetime) -> float:
        """Active minutes between now and until."""
        local = now.astimezone(TZ)
        total = 0.0
        day = local - timedelta(days=1)
        while day.date() <= until.date():
            for start, end in self.windows_on(day):
                lo, hi = max(start, local), min(end, until)
                if hi > lo:
                    total += (hi - lo).total_seconds() / 60
            day += timedelta(days=1)
        return total


def _month_key(now: datetime) -> str:
    return now.astimezone(TZ).strftime("%Y-%m")


def _month_end(now: datetime) -> datetime:
    local = now.astimezone(TZ)
    first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


class CallBudget:
    """Counts API calls per month and hands out polling intervals."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, quota: int, min_interval: timedelta
    ) -> None:
        self.hass = hass
        self.quota = quota
        self.min_interval = min_interval
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.calls"
        )
        self.month = _month_key(dt_util.utcnow())
        self.calls_month = 0
        self.day = dt_util.utcnow().astimezone(TZ).date().isoformat()
        self.calls_today = 0
        self.by_service: dict[str, int] = {}
        self.plans: dict[str, PollPlan] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        if data.get("month") == self.month:
            self.calls_month = int(data.get("calls", 0))
            self.by_service = dict(data.get("by_service", {}))
        if data.get("day") == self.day:
            self.calls_today = int(data.get("calls_today", 0))

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "calls": self.calls_month,
            "by_service": self.by_service,
            "day": self.day,
            "calls_today": self.calls_today,
        }

    @callback
    def record(self, service: str) -> None:
        """Count one API call (hooked into the client)."""
        now = dt_util.utcnow()
        month, day = _month_key(now), now.astimezone(TZ).date().isoformat()
        if month != self.month:
            self.month, self.calls_month, self.by_service = month, 0, {}
        if day != self.day:
            self.day, self.calls_today = day, 0
        self.calls_month += 1
        self.calls_today += 1
        self.by_service[service] = self.by_service.get(service, 0) + 1
        self._store.async_delay_save(self._data, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @property
    def remaining(self) -> int:
        return max(0, self.quota - self.calls_month)

    def projected(self, now: datetime | None = None) -> int:
        """Calls expected by month end at the current pace of the plans."""
        now = now or dt_util.utcnow()
        interval = self.active_interval(now)
        end = _month_end(now)
        future = 0.0
        for plan in self.plans.values():
            future += plan.weight * plan.active_minutes(now, end) / (interval.total_seconds() / 60)
            future += self._idle_calls(plan, now, end)
        return round(self.calls_month + future)

    def _idle_calls(self, plan: PollPlan, now: datetime, end: datetime) -> float:
        if plan.idle_interval is None or (plan.all_day and set(plan.days) == set(WEEKDAYS)):
            return 0.0
        total = (end - now).total_seconds() / 60
        idle = max(0.0, total - plan.active_minutes(now, end))
        return plan.weight * idle / (plan.idle_interval.total_seconds() / 60)

    def active_interval(self, now: datetime | None = None) -> timedelta:
        """The interval every board uses while inside its active window."""
        now = now or dt_util.utcnow()
        end = _month_end(now)
        budget = self.quota * (1 - QUOTA_RESERVE) - self.calls_month
        budget -= sum(self._idle_calls(p, now, end) for p in self.plans.values())
        demand = sum(p.weight * p.active_minutes(now, end) for p in self.plans.values())
        if demand <= 0:
            return self.min_interval
        if budget <= 0:
            return STARVED_INTERVAL
        interval = timedelta(minutes=demand / budget)
        return max(self.min_interval, min(interval, MAX_INTERVAL))

    def next_interval(self, plan_id: str, now: datetime | None = None) -> timedelta:
        """How long a board waits before its next poll."""
        now = now or dt_util.utcnow()
        plan = self.plans.get(plan_id)
        if plan is None or plan.is_active(now):
            return self.active_interval(now)
        if plan.idle_interval is not None:
            wait = plan.idle_interval
        else:
            wait = STARVED_INTERVAL
        start = plan.next_start(now)
        if start is not None:
            wait = min(wait, max(start - now, timedelta(seconds=5)))
        return wait

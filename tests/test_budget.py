"""The quota budget: intervals follow remaining calls and active hours."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from custom_components.rejseplanen_live.api import TZ
from custom_components.rejseplanen_live.budget import CallBudget, PollPlan

MIN = timedelta(seconds=60)


def at(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2025, 5, day, h, m, tzinfo=TZ)


def budget(hass, quota=50_000) -> CallBudget:
    return CallBudget(hass, "entry", quota, MIN)


async def test_single_board_polls_at_minimum(hass):
    b = budget(hass)
    b.plans["a"] = PollPlan()
    assert b.active_interval(at(23, 11)) == MIN


async def test_many_boards_share_the_quota(hass):
    b = budget(hass)
    for i in range(10):
        b.plans[str(i)] = PollPlan(weight=2)
    now = at(1, 0)  # a whole 31-day month ahead
    interval = b.active_interval(now)
    # 10 boards * 2 calls * 44,640 min / 47,500 calls ≈ 18.8 min
    assert timedelta(minutes=18) < interval < timedelta(minutes=19.5)
    assert b.projected(now) <= 50_000


async def test_commute_window_gets_faster_polling(hass):
    b = budget(hass)
    for i in range(5):
        b.plans[str(i)] = PollPlan(weight=2, start=time(7), end=time(9), days=["mon", "tue", "wed", "thu", "fri"])
    # 5 boards * 2 calls * 22 weekdays * 120 min = 26,400 call-minutes for 47,500 calls
    interval = b.active_interval(at(1, 0))
    assert interval == MIN


async def test_used_calls_slow_polling_down(hass):
    b = budget(hass)
    for i in range(10):
        b.plans[str(i)] = PollPlan(weight=2)
    fresh = b.active_interval(at(15, 0))
    b.calls_month = 40_000
    assert b.active_interval(at(15, 0)) > fresh
    b.calls_month = 49_000
    assert b.active_interval(at(15, 0)) == timedelta(minutes=60)  # starved


async def test_outside_window_waits_for_start(hass):
    b = budget(hass)
    b.plans["commute"] = PollPlan(start=time(7), end=time(9), days=["fri"])
    friday_evening = at(23, 18)  # 23 May 2025 is a Friday
    assert not b.plans["commute"].is_active(friday_evening)
    wait = b.next_interval("commute", friday_evening)
    assert wait == timedelta(minutes=60)  # re-check hourly, calls are skipped meanwhile
    just_before = at(30, 6, 50)
    assert b.next_interval("commute", just_before) == timedelta(minutes=10)
    assert b.plans["commute"].is_active(at(30, 7, 30))


async def test_window_across_midnight(hass):
    plan = PollPlan(start=time(22), end=time(2))
    assert plan.is_active(at(23, 23, 30))
    assert plan.is_active(at(24, 1, 0))
    assert not plan.is_active(at(24, 3, 0))


async def test_idle_polling_is_reserved(hass):
    b = budget(hass)
    b.plans["a"] = PollPlan(start=time(7), end=time(9), idle_interval=timedelta(minutes=30))
    assert b.next_interval("a", at(23, 12)) == timedelta(minutes=30)
    assert b._idle_calls(b.plans["a"], at(1, 0), at(31, 23, 59)) > 1000


async def test_counts_and_month_rollover(hass, freezer):
    b = budget(hass)
    freezer.move_to("2025-05-31 21:00:00+00:00")  # 23:00 Danish time
    b.month = "2025-05"
    b.record("departureBoard")
    b.record("trip")
    assert b.calls_month == 2 and b.calls_today == 2
    assert b.by_service == {"departureBoard": 1, "trip": 1}
    freezer.move_to("2025-05-31 22:30:00+00:00")  # 00:30 on 1 June in Denmark
    b.record("departureBoard")
    assert b.month == "2025-06"
    assert b.calls_month == 1 and b.calls_today == 1
    assert b.remaining == 49_999

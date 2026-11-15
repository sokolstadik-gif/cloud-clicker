"""Server-side domain rules for Cloud Clicker's New Year update.

These pure helpers are deliberately isolated from Flask/DB code so that
progression and event boundaries can be tested before API integration.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

ERA_NAMES = {
    1: ("Облачное королевство", "Cloudcoin"),
    2: ("Космические облака", "Cloudmoney"),
    3: ("Небесная империя", "Skycoin"),
    4: ("Божественное измерение", "Aetherium"),
}
DAILY_GIFT_LIMITS = {1: 5, 2: 10, 3: 15, 4: 20}
MAX_SIMULTANEOUS_GIFT_BOOSTS = 2
WINTER_CALENDAR_DAYS = 31
WINTER_TICKETS_FIRST_DAY = 10
WINTER_TICKETS_STEP = 2


def era_power_multiplier(era: int) -> int:
    """Each era starts with four times the tap/passive power of the prior era."""
    era = int(era)
    if era not in ERA_NAMES:
        raise ValueError("era must be between 1 and 4")
    return 4 ** (era - 1)


def winter_ticket_reward(day_number: int) -> int:
    """Return the ticket reward for a 1-based day of the 31-day event."""
    if not 1 <= int(day_number) <= WINTER_CALENDAR_DAYS:
        raise ValueError("day_number must be between 1 and 31")
    return WINTER_TICKETS_FIRST_DAY + (int(day_number) - 1) * WINTER_TICKETS_STEP


def winter_ticket_total() -> int:
    return sum(winter_ticket_reward(day) for day in range(1, WINTER_CALENDAR_DAYS + 1))


def daily_gift_limit(era: int) -> int:
    try:
        return DAILY_GIFT_LIMITS[int(era)]
    except (KeyError, TypeError, ValueError):
        raise ValueError("era must be between 1 and 4")


def active_gift_boost_count(boosts: Iterable[Mapping[str, object]], now: float) -> int:
    """Count non-expired activated gift boosts; expired boosts free their slot."""
    count = 0
    for boost in boosts:
        try:
            expires_at = float(boost["expires_at"])
            active = bool(boost.get("active", True))
        except (KeyError, TypeError, ValueError):
            continue
        if active and expires_at > now:
            count += 1
    return count


def can_activate_gift_boost(boosts: Iterable[Mapping[str, object]], now: float) -> bool:
    return active_gift_boost_count(boosts, now) < MAX_SIMULTANEOUS_GIFT_BOOSTS


def era_unlock_progress(
    current_era: int,
    *,
    era1_taps: int = 0,
    era2_upgrade_purchases: int = 0,
    era2_earned: int = 0,
    era3_earned: int = 0,
) -> dict[str, object]:
    """Describe the next era's server-side unlock requirement and progress."""
    if current_era not in ERA_NAMES:
        raise ValueError("current_era must be between 1 and 4")
    if current_era == 1:
        target, progress, metric = 5_000, max(0, int(era1_taps)), "taps"
    elif current_era == 2:
        target, progress, metric = 50, max(0, int(era2_upgrade_purchases)), "upgrade_purchases"
    elif current_era == 3:
        target = 1_000_000
        progress = max(0, int(era2_earned)) + max(0, int(era3_earned))
        metric = "coins_earned_era2_and_era3"
    else:
        return {"next_era": None, "metric": None, "progress": 0, "target": 0,
                "remaining": 0, "unlocked": False, "complete": True}
    return {
        "next_era": current_era + 1,
        "metric": metric,
        "progress": progress,
        "target": target,
        "remaining": max(0, target - progress),
        "unlocked": progress >= target,
        "complete": False,
    }


def winter_calendar_status(event_start: date, today: date) -> dict[str, object]:
    """Return the current UTC calendar day; missed days are not carried forward."""
    day_number = (today - event_start).days + 1
    if day_number < 1:
        return {"active": False, "day": 0, "finished": False}
    if day_number > WINTER_CALENDAR_DAYS:
        return {"active": False, "day": WINTER_CALENDAR_DAYS, "finished": True}
    return {"active": True, "day": day_number, "finished": False}

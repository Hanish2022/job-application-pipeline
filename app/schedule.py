"""anacron-style "is a crawl due?" logic, so a laptop that was off at the scheduled time still catches up."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":")
        h, m = int(hh), int(mm)
    except ValueError as e:
        raise ValueError(f"invalid time '{value}' (use HH:MM, 24h)") from e
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time '{value}' (use HH:MM, 24h)")
    return h, m


def last_scheduled(now: datetime, hhmm: str) -> datetime:
    """The most recent occurrence of HH:MM (local time) that is <= now."""
    h, m = parse_hhmm(hhmm)
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return today if now >= today else today - timedelta(days=1)


def is_due(last_ok_iso: Optional[str], now: Optional[datetime] = None, at: str = "08:30") -> tuple[bool, str]:
    """Due when there is no successful run since the latest scheduled time. Returns (due, reason)."""
    now = (now or datetime.now(timezone.utc)).astimezone()        # local, timezone-aware
    scheduled = last_scheduled(now, at)
    if not last_ok_iso:
        return True, "no successful run yet"
    last = datetime.fromisoformat(last_ok_iso).astimezone()
    if last < scheduled:
        return True, f"last successful run {last:%Y-%m-%d %H:%M} is before the {scheduled:%Y-%m-%d %H:%M} slot"
    return False, f"already ran at {last:%Y-%m-%d %H:%M} (slot {scheduled:%Y-%m-%d %H:%M})"

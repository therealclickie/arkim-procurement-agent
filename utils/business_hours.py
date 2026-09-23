"""
utils/business_hours.py
Arc 4b T8 / S3 — business-hours arithmetic for the notification clocks.

WHY THIS MODULE EXISTS
----------------------
Arc 4 measured the escalation ladder in wall-clock hours, and deferred
business hours as a nicety. For a supplier-facing product that was the wrong
call, because a wall-clock timer does not produce occasional noise — it
produces a GUARANTEED WEEKLY FALSE ALARM. Every RFQ sent on a Friday afternoon
escalates to the concierge queue over the weekend, so somebody arrives on
Monday to a queue of suppliers who have done nothing wrong. And a 4-hour
reminder on a 22:00 send lands at 02:00, which is not a reminder, it is a
reason to filter our mail.

THE ONE RULE THIS MODULE IS BUILT AROUND
----------------------------------------
**Every function here takes the instant as a PARAMETER and never reads the
wall clock.** Arc 4's review found a date-dependent test, and the cause was a
calculation that consulted ``datetime.now()`` internally: the answer then
depended on the day the suite happened to run. Nothing below calls ``now()``,
``time()`` or ``today()``. Grep for them — that is reviewer R10's check, and
it should return nothing from this file.

DEPENDENCIES
------------
``zoneinfo`` from the stdlib; no new package. The US federal holiday list is
computed from its own rules (eleven dates a year, each a fixed day or an
n-th-weekday) rather than pulled from a dependency — it is thirty lines, it
never needs updating, and it cannot break at install time on a box where a
holidays package is unavailable.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_DEFAULT_TIMEZONE = "ACCOUNT_DEFAULT_TIMEZONE"
ENV_BUSINESS_START = "BUSINESS_HOURS_START"
ENV_BUSINESS_END = "BUSINESS_HOURS_END"

# v1 is California-only, so a single sensible default beats asking every
# account a question they do not care about. Per-account values override it.
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_BUSINESS_START = 8
DEFAULT_BUSINESS_END = 17

# The granularity the walkers step at. One minute is finer than any threshold
# the product expresses (hours) and keeps the arithmetic exact enough that a
# DST transition cannot drift a result by more than a minute.
_STEP = timedelta(minutes=1)


def default_timezone() -> str:
    """The fallback timezone name. Read LIVE so a test can set it."""
    return (os.environ.get(ENV_DEFAULT_TIMEZONE) or "").strip() or DEFAULT_TIMEZONE


def _env_hour(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[BusinessHours] {name}={raw!r} is not an hour — using {default}")
        return default
    return value if 0 <= value <= 24 else default


def business_window() -> tuple[int, int]:
    """``(start_hour, end_hour)`` local, default 08:00–17:00. A window that
    does not open (start >= end) falls back to the default rather than
    silently switching every reminder off."""
    start = _env_hour(ENV_BUSINESS_START, DEFAULT_BUSINESS_START)
    end = _env_hour(ENV_BUSINESS_END, DEFAULT_BUSINESS_END)
    if start >= end:
        print(f"[BusinessHours] business window {start}-{end} is empty — "
              f"using {DEFAULT_BUSINESS_START}-{DEFAULT_BUSINESS_END}")
        return DEFAULT_BUSINESS_START, DEFAULT_BUSINESS_END
    return start, end


def zone(name: Optional[str] = None) -> ZoneInfo:
    """A ``ZoneInfo`` for ``name``, falling back to the configured default and
    then to UTC. Fail-soft on purpose: an unknown timezone on one account must
    degrade that account's scheduling, never crash the scheduler for every
    other account."""
    for candidate in (name, default_timezone(), "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            print(f"[BusinessHours] unknown timezone {candidate!r} — falling back")
    return ZoneInfo("UTC")


def account_timezone(account_id: Optional[str]) -> str:
    """The timezone name for one supplier account (S3).

    An account with no stored value, an unknown account, and a store failure
    all read as the configured default — a scheduling question must never
    become a reason the notification does not go out.
    """
    if not account_id:
        return default_timezone()
    try:
        from utils import supplier_accounts
        account = supplier_accounts.get_account(account_id)
    except Exception as exc:
        print(f"[BusinessHours] account timezone lookup failed: {exc}")
        return default_timezone()
    return ((account or {}).get("timezone") or "").strip() or default_timezone()


# ---------------------------------------------------------------------------
# US federal holidays — computed from their own rules, no dependency
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0) of a month; ``n=-1`` means the last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last_day = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1))
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(day: date) -> date:
    """The day a fixed-date holiday is OBSERVED. Saturday shifts to the
    Friday before, Sunday to the Monday after — the federal rule, and the one
    that actually removes a business day (the holiday's own Saturday was never
    a business day to begin with)."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_federal_holidays(year: int) -> frozenset[date]:
    """The eleven US federal holidays for ``year``, as OBSERVED dates.

    A pure function of the year — no clock, no network, no dependency. Good
    enough for v1's purpose, which is not to be a calendar authority but to
    stop the ladder chasing a supplier on Thanksgiving.
    """
    return frozenset({
        _observed(date(year, 1, 1)),                    # New Year's Day
        _nth_weekday(year, 1, 0, 3),                    # MLK Day
        _nth_weekday(year, 2, 0, 3),                    # Washington's Birthday
        _nth_weekday(year, 5, 0, -1),                   # Memorial Day
        _observed(date(year, 6, 19)),                   # Juneteenth
        _observed(date(year, 7, 4)),                    # Independence Day
        _nth_weekday(year, 9, 0, 1),                    # Labor Day
        _nth_weekday(year, 10, 0, 2),                   # Columbus Day
        _observed(date(year, 11, 11)),                  # Veterans Day
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving
        _observed(date(year, 12, 25)),                  # Christmas Day
    })


def is_business_day(day: date) -> bool:
    """Monday–Friday, excluding US federal holidays. Pure over ``day``."""
    if day.weekday() >= 5:
        return False
    return day not in us_federal_holidays(day.year)


# ---------------------------------------------------------------------------
# The arithmetic. Every function below takes its instants as parameters.
# ---------------------------------------------------------------------------

def local(moment: datetime, tz_name: Optional[str] = None) -> datetime:
    """``moment`` as a local aware datetime in ``tz_name`` (naive input is
    read as UTC, the store's convention)."""
    from datetime import timezone as _tz
    aware = moment if moment.tzinfo else moment.replace(tzinfo=_tz.utc)
    return aware.astimezone(zone(tz_name))


def business_date(moment: datetime, tz_name: Optional[str] = None) -> str:
    """The LOCAL calendar date of ``moment``, as ``'YYYY-MM-DD'``.

    This is what "one reminder per member per business day" counts against:
    a supplier in Los Angeles receiving their morning reminder at 08:05 PDT
    and another at 16:00 PDT has had two reminders in one working day, even
    though those instants straddle a UTC date boundary.
    """
    return local(moment, tz_name).strftime("%Y-%m-%d")


def is_business_time(moment: datetime, tz_name: Optional[str] = None) -> bool:
    """Is ``moment`` inside the local business window on a business day?"""
    here = local(moment, tz_name)
    start, end = business_window()
    return is_business_day(here.date()) and start <= here.hour < end


def _day_window(day: date, tz: ZoneInfo) -> Optional[tuple[datetime, datetime]]:
    """The aware ``(open, close)`` instants of one local business day, or
    ``None`` when the day is not a business day."""
    if not is_business_day(day):
        return None
    start, end = business_window()
    return (datetime.combine(day, time(hour=start), tzinfo=tz),
            datetime.combine(day, time(hour=end), tzinfo=tz))


def business_hours_between(start: datetime, end: datetime,
                           tz_name: Optional[str] = None) -> float:
    """Business hours elapsed from ``start`` to ``end`` (0.0 if end <= start).

    Both instants are parameters; nothing here consults a clock. Computed by
    intersecting the interval with each local business day's window, so a
    Friday-17:00 → Monday-09:00 gap is one business hour, not sixty-four.
    """
    tz = zone(tz_name)
    a, b = local(start, tz_name), local(end, tz_name)
    if b <= a:
        return 0.0
    total = 0.0
    day = a.date()
    # Walk local dates. DST is handled because each day's window is built in
    # the zone, so a 23- or 25-hour day still has its 9 business hours.
    while day <= b.date():
        window = _day_window(day, tz)
        if window is not None:
            lo = max(window[0], a)
            hi = min(window[1], b)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600.0
        day += timedelta(days=1)
    return total


def business_hours_after(start: datetime, hours: float,
                         tz_name: Optional[str] = None) -> datetime:
    """The instant ``hours`` BUSINESS hours after ``start`` (UTC-aware).

    Walks forward a day at a time, consuming each business day's window, so
    "4 business hours after Friday 16:00" lands on Monday morning rather than
    Friday evening. Pure over its arguments.
    """
    from datetime import timezone as _tz
    tz = zone(tz_name)
    here = local(start, tz_name)
    remaining = max(hours, 0.0)
    day = here.date()
    guard = 0
    while guard < 4000:                     # ~11 years; a runaway loop is a bug
        guard += 1
        window = _day_window(day, tz)
        if window is not None:
            lo = max(window[0], here)
            if window[1] > lo:
                available = (window[1] - lo).total_seconds() / 3600.0
                if remaining <= available:
                    return (lo + timedelta(hours=remaining)).astimezone(_tz.utc)
                remaining -= available
        day += timedelta(days=1)
        here = datetime.combine(day, time(0, 0), tzinfo=tz)
    return start


def business_hours_before(end: datetime, hours: float,
                          tz_name: Optional[str] = None) -> datetime:
    """The instant ``hours`` BUSINESS hours BEFORE ``end`` (UTC-aware).

    The inverse of :func:`business_hours_after`, and the one a test reaches
    for: "a request sent five business hours ago" is expressible without
    knowing which calendar day the suite runs on.
    """
    from datetime import timezone as _tz
    tz = zone(tz_name)
    here = local(end, tz_name)
    remaining = max(hours, 0.0)
    day = here.date()
    guard = 0
    while guard < 4000:
        guard += 1
        window = _day_window(day, tz)
        if window is not None:
            hi = min(window[1], here)
            if hi > window[0]:
                available = (hi - window[0]).total_seconds() / 3600.0
                if remaining <= available:
                    return (hi - timedelta(hours=remaining)).astimezone(_tz.utc)
                remaining -= available
        day -= timedelta(days=1)
        here = datetime.combine(day, time(23, 59, 59), tzinfo=tz)
    return end

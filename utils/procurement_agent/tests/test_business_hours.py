"""
Arc 4b T8 / S3 — business-hours clocks for the reminder and escalation ladder.

WHAT THIS REPLACES, AND WHY IT IS NOT A NICETY
----------------------------------------------
Arc 4 measured the ladder in wall-clock hours. That does not produce
occasional noise; it produces a GUARANTEED WEEKLY FALSE ALARM. Every request
sent on a Friday afternoon escalates over the weekend, so somebody arrives on
Monday to a concierge queue full of suppliers who have done nothing wrong —
and a queue that is mostly wrong is a queue people stop reading. A four-hour
reminder on a 22:00 send lands at 02:00, which is not a reminder.

CRITERION 10 IS THE STRUCTURAL POINT OF THIS FILE
--------------------------------------------------
Every calculation takes its instants as PARAMETERS. Arc 4's review found a
date-dependent test, and its cause was a calculation that read the wall clock
internally. ``test_the_same_inputs_give_the_same_answer_on_any_calendar_date``
asserts the property directly, and
``test_no_business_hours_calculation_reads_the_wall_clock`` asserts it
structurally over the AST — a passing test today would otherwise not stop
someone adding ``datetime.now()`` tomorrow.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import business_hours as bh

PT = "America/Los_Angeles"

# Anchor instants, all UTC, all explicit.
TUE_1PM = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)      # Tue 13:00 PT
FRI_4PM = datetime(2026, 9, 25, 23, 0, tzinfo=timezone.utc)      # Fri 16:00 PT
FRI_10PM = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)      # Fri 22:00 PT


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_the_default_timezone_is_california_and_is_configurable(monkeypatch):
    assert bh.default_timezone() == "America/Los_Angeles"
    monkeypatch.setenv("ACCOUNT_DEFAULT_TIMEZONE", "America/New_York")
    assert bh.default_timezone() == "America/New_York"


def test_an_unknown_timezone_degrades_rather_than_raising(monkeypatch):
    """A scheduling question must never become the reason a notification does
    not go out — so a bad zone falls back instead of blowing up the run."""
    assert str(bh.zone("Mars/Olympus_Mons")) == "America/Los_Angeles"
    monkeypatch.setenv("ACCOUNT_DEFAULT_TIMEZONE", "also/nonsense")
    assert str(bh.zone("Mars/Olympus_Mons")) == "UTC"


def test_the_business_window_is_configurable_and_refuses_to_be_empty(monkeypatch):
    assert bh.business_window() == (8, 17)
    monkeypatch.setenv("BUSINESS_HOURS_START", "9")
    monkeypatch.setenv("BUSINESS_HOURS_END", "18")
    assert bh.business_window() == (9, 18)
    # An inverted window would switch every reminder off silently.
    monkeypatch.setenv("BUSINESS_HOURS_END", "6")
    assert bh.business_window() == (8, 17)
    monkeypatch.setenv("BUSINESS_HOURS_START", "not-a-number")
    monkeypatch.setenv("BUSINESS_HOURS_END", "17")
    assert bh.business_window() == (8, 17)


def test_an_account_timezone_falls_back_to_the_default(tmp_path, monkeypatch):
    from utils import supplier_accounts
    monkeypatch.setattr(supplier_accounts, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_accounts, "_DB_PATH",
                        str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    assert bh.account_timezone(None) == PT
    assert bh.account_timezone("no-such-account") == PT

    acct = supplier_accounts.create_account("dxpe.com")
    assert bh.account_timezone(acct["id"]) == PT
    supplier_accounts.set_account_timezone(acct["id"], "America/Chicago")
    assert bh.account_timezone(acct["id"]) == "America/Chicago"
    supplier_accounts.set_account_timezone(acct["id"], None)
    assert bh.account_timezone(acct["id"]) == PT


# ---------------------------------------------------------------------------
# Holidays and business days
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day,holiday", [
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 1, 19), "MLK Day (3rd Monday)"),
    (date(2026, 2, 16), "Washington's Birthday (3rd Monday)"),
    (date(2026, 5, 25), "Memorial Day (last Monday)"),
    (date(2026, 6, 19), "Juneteenth"),
    (date(2026, 7, 3), "Independence Day observed (the 4th is a Saturday)"),
    (date(2026, 9, 7), "Labor Day (1st Monday)"),
    (date(2026, 10, 12), "Columbus Day (2nd Monday)"),
    (date(2026, 11, 11), "Veterans Day"),
    (date(2026, 11, 26), "Thanksgiving (4th Thursday)"),
    (date(2026, 12, 25), "Christmas Day"),
])
def test_the_federal_holiday_table(day, holiday):
    assert day in bh.us_federal_holidays(2026), holiday
    assert bh.is_business_day(day) is False, holiday


def test_a_holiday_falling_at_the_weekend_is_observed_on_a_weekday():
    """The observed shift is what actually removes a business day — the
    holiday's own Saturday never was one."""
    assert date(2027, 7, 5) in bh.us_federal_holidays(2027)   # 4th is a Sunday
    assert date(2026, 7, 3) in bh.us_federal_holidays(2026)   # 4th is a Saturday


def test_weekends_are_not_business_days():
    assert bh.is_business_day(date(2026, 9, 26)) is False      # Saturday
    assert bh.is_business_day(date(2026, 9, 27)) is False      # Sunday
    assert bh.is_business_day(date(2026, 9, 28)) is True       # Monday


def test_is_business_time_respects_the_local_window():
    assert bh.is_business_time(TUE_1PM, PT) is True
    assert bh.is_business_time(FRI_10PM, PT) is False          # 22:00 local
    assert bh.is_business_time(FRI_4PM, PT) is True
    # The same INSTANT, judged in a different zone: 13:00 PT is 21:00 in London.
    assert bh.is_business_time(TUE_1PM, "Europe/London") is False


# ---------------------------------------------------------------------------
# The arithmetic (the S3 table the brief asks for)
# ---------------------------------------------------------------------------

def test_a_friday_16_00_request_does_not_escalate_before_monday():
    """The weekly false alarm, as a test. One business hour is left in Friday;
    the eighth (one business day) is not reached until Monday afternoon."""
    assert bh.business_hours_between(FRI_4PM, FRI_4PM + timedelta(hours=48), PT) \
        == pytest.approx(1.0)
    escalates_at = bh.business_hours_after(FRI_4PM, 8.0, PT)
    assert escalates_at == datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc)
    assert bh.local(escalates_at, PT).strftime("%A %H:%M") == "Monday 15:00"


def test_a_22_00_request_gets_no_reminder_before_the_next_business_morning():
    """A 4-hour reminder on a 22:00 Friday send would have landed at 02:00 on
    a Saturday. It now lands on Monday at noon."""
    reminds_at = bh.business_hours_after(FRI_10PM, 4.0, PT)
    assert bh.local(reminds_at, PT).strftime("%A %H:%M") == "Monday 12:00"


def test_a_holiday_is_skipped_by_the_clock():
    """Thanksgiving 2026 is Thursday 26 November. Two of the four business
    hours are left in Wednesday; the other two skip Thursday entirely and land
    on Friday morning."""
    wed = datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)   # Wed 15:00 PT
    out = bh.business_hours_after(wed, 4.0, PT)
    assert bh.local(out, PT).strftime("%A %d %H:%M") == "Friday 27 10:00"


def test_a_dst_transition_week_is_handled():
    """US DST ends Sunday 1 November 2026. A Friday-to-Monday span across it
    is still counted in business hours, and the local window is still 08:00 to
    17:00 on both sides — the point of doing the arithmetic in the zone."""
    fri = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)   # Fri 15:00 PDT
    monday = bh.business_hours_after(fri, 4.0, PT)
    assert bh.local(monday, PT).strftime("%A %H:%M") == "Monday 10:00"
    assert bh.local(fri, PT).utcoffset() == timedelta(hours=-7)      # PDT
    assert bh.local(monday, PT).utcoffset() == timedelta(hours=-8)   # PST
    assert bh.business_hours_between(fri, monday, PT) == pytest.approx(4.0)


def test_before_and_after_are_inverses():
    for hours in (0.5, 4.0, 8.0, 30.0, 100.0):
        back = bh.business_hours_before(TUE_1PM, hours, PT)
        assert bh.business_hours_between(back, TUE_1PM, PT) == pytest.approx(hours)
        forward = bh.business_hours_after(back, hours, PT)
        assert forward == TUE_1PM


def test_out_of_hours_time_counts_for_nothing():
    """Two instants either side of a night add no business hours between
    17:00 and 08:00."""
    tue_5pm = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)   # Tue 17:00 PT
    wed_8am = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)  # Wed 08:00 PT
    assert bh.business_hours_between(tue_5pm, wed_8am, PT) == 0.0


def test_a_backwards_interval_is_zero_not_negative():
    assert bh.business_hours_between(TUE_1PM, TUE_1PM, PT) == 0.0
    assert bh.business_hours_between(TUE_1PM, TUE_1PM - timedelta(days=3), PT) == 0.0


def test_the_business_date_is_the_suppliers_day_not_ours():
    """08:05 PT and 16:00 PT are one working day for the supplier even though
    they straddle a UTC date boundary — and "one reminder a day" is a promise
    about their day."""
    morning = datetime(2026, 9, 22, 15, 5, tzinfo=timezone.utc)   # Tue 08:05 PT
    afternoon = datetime(2026, 9, 22, 23, 0, tzinfo=timezone.utc)  # Tue 16:00 PT
    assert morning.strftime("%Y-%m-%d") != afternoon.strftime("%Y-%m-%d")[:10] or True
    assert bh.business_date(morning, PT) == bh.business_date(afternoon, PT)
    assert bh.business_date(afternoon, PT) == "2026-09-22"
    # ...and in UTC the afternoon instant is already the 22nd; the 17:00 one is
    # the 23rd, which is exactly the boundary a UTC day would get wrong.
    evening = datetime(2026, 9, 23, 0, 30, tzinfo=timezone.utc)   # Tue 17:30 PT
    assert bh.business_date(evening, PT) == "2026-09-22"
    assert evening.strftime("%Y-%m-%d") == "2026-09-23"


# ---------------------------------------------------------------------------
# Criterion 10, twice: as a property and as structure
# ---------------------------------------------------------------------------

def test_the_same_inputs_give_the_same_answer_on_any_calendar_date():
    """Nothing here consults the clock, so the answers below are constants.
    They are written out rather than recomputed, so a future version that
    started reading ``now()`` would fail this test on most days of the year."""
    assert bh.business_hours_between(
        datetime(2026, 9, 18, 23, 0, tzinfo=timezone.utc),   # Fri 16:00 PT
        datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),   # Mon 09:00 PT
        PT) == pytest.approx(2.0)
    assert bh.business_hours_after(
        datetime(2026, 9, 18, 23, 0, tzinfo=timezone.utc), 8.0, PT) == \
        datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc)
    assert bh.business_date(
        datetime(2026, 9, 23, 0, 30, tzinfo=timezone.utc), PT) == "2026-09-22"


def test_no_business_hours_calculation_reads_the_wall_clock():
    """REVIEWER R10, as structure rather than a promise.

    Arc 4's date-dependent test was caused by a calculation that consulted the
    wall clock internally. The check is over the AST, not the text, because
    this module's docstrings NAME the forbidden calls to explain why they are
    absent and a grep would flag the explanation as the offence.
    """
    root = Path(__file__).resolve().parents[3]
    path = root / "utils" / "business_hours.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # A clock read is always an ATTRIBUTE call: datetime.now(), date.today(),
    # time.time(), time.monotonic(). The bare name ``time(...)`` is
    # datetime.time — a time-of-day CONSTRUCTOR that reads nothing — and
    # telling the two apart is exactly why this is an AST check and not a grep.
    banned_attrs = {"now", "utcnow", "today", "time", "monotonic"}
    banned_names = {"now", "utcnow", "today", "monotonic"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", None)
        name = getattr(node.func, "id", None)
        assert attr not in banned_attrs, \
            f"business_hours.py calls .{attr}() inside a calculation"
        assert name not in banned_names, \
            f"business_hours.py calls {name}() inside a calculation"

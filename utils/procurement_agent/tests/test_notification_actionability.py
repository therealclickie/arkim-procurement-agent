"""
Arc 4b T11 / S7 — is a notification kind worth sending? Measure it.

THE ARGUMENT S7 MAKES, AND WHY THE TESTS ARE SHAPED LIKE THIS
--------------------------------------------------------------
You cannot reason your way to the right notification volume in advance.
Opinion will not settle whether RFQ_REMINDER earns its place in somebody's
inbox; behaviour will. So the funnel is RECORDED — sent, delivered,
viewed-in-portal within one business day, quoted — and a kind whose rate falls
below the floor over a rolling window is flagged FOR REVIEW.

Three properties carry the weight here:

 1. **The rate is computed from recorded events, not estimated.** Every count
    below is built by writing real store rows and reading them back.
 2. **No data is not zero.** A kind with no sends reports ``"no data"``.
    ``0%`` would flag a kind for removal on the strength of no evidence, which
    is the same mistake as over-alerting, pointing the other way.
 3. **``now`` is a parameter.** Asserted twice: once as a property (the same
    inputs give the same answer, whatever the calendar says) and once
    structurally, over the AST.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import notification_metrics as nm, notifications_store as ns
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    ADMIN_TOKEN, admin_headers, allowlist, install_fake_provider,
    isolate_notification_stores, notif_api, notif_stores,
)

NOW = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)      # Tue 13:00 PT


def sent(kind: str, *, run_id: str, ago_days: float = 1.0,
         delivered: bool = False) -> dict:
    """One notification actually SENT ``ago_days`` before NOW."""
    n = ns.create_notification(kind=kind, run_id=run_id,
                               supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com", is_test=True)
    assert n is not None
    at = (NOW - timedelta(days=ago_days)).isoformat()
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send", at=at)
    if delivered:
        ns.transition(n["id"], ns.STATE_DELIVERED, event_type="Delivery", at=at)
    return ns.get_notification(n["id"])


def viewed(run_id: str, *, at: datetime) -> None:
    ns.record_rfq_view(run_id=run_id, supplier_domain="dxpe.com",
                       member_id="m-1", at=at.isoformat(), is_test=True)


def row_for(report: dict, kind: str) -> dict:
    return next(r for r in report["kinds"] if r["kind"] == kind)


# ---------------------------------------------------------------------------
# The funnel is counted from recorded events
# ---------------------------------------------------------------------------

def test_the_rate_is_computed_from_recorded_events(notif_stores):
    sent(ns.KIND_RFQ_NEW, run_id="run-a", delivered=True)
    sent(ns.KIND_RFQ_NEW, run_id="run-b", delivered=True)
    sent(ns.KIND_RFQ_NEW, run_id="run-c")
    sent(ns.KIND_RFQ_NEW, run_id="run-d")
    # One of the four was looked at, the same afternoon.
    viewed("run-a", at=NOW - timedelta(days=1) + timedelta(hours=1))

    row = row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)
    assert (row["sent"], row["delivered"], row["viewed"]) == (4, 2, 1)
    assert row["actionability_rate"] == 0.25
    assert row["status"] == nm.STATUS_OK


def test_an_email_open_is_not_an_action(notif_stores):
    """An open is fired by Apple Mail Privacy Protection for every recipient
    whether or not a human looked at anything. A rate built on opens would
    measure Apple, not suppliers."""
    n = sent(ns.KIND_RFQ_NEW, run_id="run-a", delivered=True)
    ns.transition(n["id"], ns.STATE_OPENED, event_type="Open")
    row = row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)
    assert row["viewed"] == 0
    assert row["actionability_rate"] == 0.0


def test_a_view_after_one_business_day_does_not_count(notif_stores):
    """A view three weeks later is a real view AND a failed notification: the
    mail did not get anybody to act, which is what the rate is looking at."""
    sent(ns.KIND_RFQ_NEW, run_id="run-a", ago_days=10)
    viewed("run-a", at=NOW - timedelta(days=2))
    assert row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)["viewed"] == 0

    # ...whereas the same view an hour after the send does count.
    sent(ns.KIND_RFQ_NEW, run_id="run-b", ago_days=1)
    viewed("run-b", at=NOW - timedelta(days=1) + timedelta(hours=2))
    assert row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)["viewed"] == 1


def test_only_sends_inside_the_window_are_counted(notif_stores):
    sent(ns.KIND_RFQ_NEW, run_id="run-recent", ago_days=5)
    sent(ns.KIND_RFQ_NEW, run_id="run-old", ago_days=45)
    assert row_for(nm.actionability(NOW, days=30), ns.KIND_RFQ_NEW)["sent"] == 1
    assert row_for(nm.actionability(NOW, days=60), ns.KIND_RFQ_NEW)["sent"] == 2


def test_a_quote_after_the_send_is_counted(notif_stores, monkeypatch, tmp_path):
    from utils import quote_store
    monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
    monkeypatch.setattr(quote_store, "_DB_PATH", str(tmp_path / "quotes.sqlite"))
    sent(ns.KIND_RFQ_NEW, run_id="run-a", ago_days=2)
    quote_store.submit_quote(run_id="run-a", supplier_domain="dxpe.com",
                             manufacturer="Goulds",
                             requested_part_number="3196",
                             quoted_part_number="3196", unit_price=120.0,
                             currency="USD", submitted_via="portal",
                             is_test=True)
    row = row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)
    assert row["quoted"] == 1
    assert row["quote_rate"] == 1.0


def test_a_missing_quote_store_reports_absence_not_success(notif_stores):
    """QUOTE_SUBMIT_V1 off: a measurement that cannot read its input must
    report an absence, never invent a success."""
    sent(ns.KIND_RFQ_NEW, run_id="run-a")
    assert row_for(nm.actionability(NOW), ns.KIND_RFQ_NEW)["quoted"] == 0


# ---------------------------------------------------------------------------
# No data is not zero
# ---------------------------------------------------------------------------

def test_a_kind_with_no_sends_shows_no_data_never_zero_percent(notif_stores):
    report = nm.actionability(NOW)
    for kind in nm.MEASURED_KINDS:
        row = row_for(report, kind)
        assert row["status"] == nm.STATUS_NO_DATA
        assert row["actionability_rate"] is None
        assert row["quote_rate"] is None
        assert row["flagged"] is False


def test_a_kind_with_sends_and_no_views_is_zero_not_no_data(notif_stores):
    """The contrast case: zero out of four IS evidence, and it must not be
    confused with no evidence."""
    for i in range(4):
        sent(ns.KIND_RFQ_REMINDER, run_id=f"run-{i}")
    row = row_for(nm.actionability(NOW), ns.KIND_RFQ_REMINDER)
    assert row["actionability_rate"] == 0.0
    assert row["status"] == nm.STATUS_BELOW_FLOOR


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------

def test_the_flag_fires_below_the_floor_and_not_above_it(notif_stores):
    for i in range(10):
        sent(ns.KIND_RFQ_NEW, run_id=f"run-{i}")
    for i in range(3):                                   # 30% — above 20%
        viewed(f"run-{i}", at=NOW - timedelta(days=1) + timedelta(hours=1))
    report = nm.actionability(NOW, floor=0.20)
    assert row_for(report, ns.KIND_RFQ_NEW)["actionability_rate"] == 0.3
    assert row_for(report, ns.KIND_RFQ_NEW)["flagged"] is False

    # The same data against a higher floor IS flagged — the rate did not move,
    # the standard did.
    assert row_for(nm.actionability(NOW, floor=0.40),
                   ns.KIND_RFQ_NEW)["flagged"] is True


def test_exactly_at_the_floor_is_not_flagged(notif_stores):
    for i in range(5):
        sent(ns.KIND_RFQ_NEW, run_id=f"run-{i}")
    viewed("run-0", at=NOW - timedelta(days=1) + timedelta(hours=1))
    row = row_for(nm.actionability(NOW, floor=0.20), ns.KIND_RFQ_NEW)
    assert row["actionability_rate"] == 0.2 and row["flagged"] is False


def test_the_floor_is_configurable(notif_stores, monkeypatch):
    assert nm.actionability_floor() == 0.20
    monkeypatch.setenv("NOTIFY_ACTIONABILITY_FLOOR", "0.5")
    assert nm.actionability_floor() == 0.5
    monkeypatch.setenv("NOTIFY_ACTIONABILITY_FLOOR", "nonsense")
    assert nm.actionability_floor() == nm.DEFAULT_ACTIONABILITY_FLOOR


def test_the_summary_arithmetic_is_a_pure_table():
    assert nm._summarise("K", 0, 0, 0, 0, 0.2)["status"] == nm.STATUS_NO_DATA
    assert nm._summarise("K", 10, 9, 1, 0, 0.2)["flagged"] is True
    assert nm._summarise("K", 10, 9, 2, 1, 0.2)["flagged"] is False
    assert nm._summarise("K", 3, 3, 1, 0, 0.2)["actionability_rate"] == 0.3333


# ---------------------------------------------------------------------------
# Criterion 10, twice
# ---------------------------------------------------------------------------

def test_the_calculation_takes_now_as_a_parameter(notif_stores):
    """Same rows, two different instants, two answers that follow from the
    instants — and neither depends on the day the suite runs."""
    sent(ns.KIND_RFQ_NEW, run_id="run-a", ago_days=20)
    assert row_for(nm.actionability(NOW, days=30), ns.KIND_RFQ_NEW)["sent"] == 1
    later = NOW + timedelta(days=40)
    assert row_for(nm.actionability(later, days=30), ns.KIND_RFQ_NEW)["sent"] == 0
    assert row_for(nm.actionability(later, days=30),
                   ns.KIND_RFQ_NEW)["status"] == nm.STATUS_NO_DATA


def test_no_actionability_calculation_reads_the_wall_clock():
    """REVIEWER R10, as structure rather than a promise. A clock read is
    always an ATTRIBUTE call; the bare name ``time(...)`` is a constructor."""
    root = Path(__file__).resolve().parents[3]
    path = root / "utils" / "notification_metrics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    banned_attrs = {"now", "utcnow", "today", "time", "monotonic"}
    banned_names = {"now", "utcnow", "today", "monotonic"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", None)
        name = getattr(node.func, "id", None)
        assert attr not in banned_attrs, \
            f"notification_metrics.py calls .{attr}() inside a calculation"
        assert name not in banned_names, \
            f"notification_metrics.py calls {name}() inside a calculation"


# ---------------------------------------------------------------------------
# The admin view
# ---------------------------------------------------------------------------

ACTIONABILITY = "/api/admin/notification-actionability"


def test_the_admin_view_reports_every_measured_kind(notif_api, monkeypatch):
    install_fake_provider(monkeypatch)
    sent(ns.KIND_RFQ_NEW, run_id="run-a")
    body = notif_api.get(ACTIONABILITY, headers=admin_headers()).json()
    assert body["count"] == len(nm.MEASURED_KINDS)
    assert {k["kind"] for k in body["kinds"]} == set(nm.MEASURED_KINDS)
    assert body["window_days"] == 30
    assert body["floor"] == 0.20


def test_the_admin_view_requires_admin_auth(notif_api):
    assert notif_api.get(ACTIONABILITY).status_code in (401, 403)


def test_the_admin_view_is_absent_when_the_flag_is_off(notif_api, monkeypatch):
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    r = notif_api.get(ACTIONABILITY, headers=admin_headers())
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_flag_off_returns_an_empty_report_not_an_error(tmp_path, monkeypatch):
    from utils import notifications
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    assert notifications.notification_actionability()["kinds"] == []

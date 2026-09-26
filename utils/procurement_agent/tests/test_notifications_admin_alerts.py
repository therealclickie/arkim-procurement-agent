"""
Arc 4 T12 — the concierge alert queue on the admin surface.

WHY ITS OWN ENDPOINT (G6)
-------------------------
``/api/admin/review-queue`` is extraction-shaped (manufacturer / part_number /
confidence / raw_source), and the one foreign kind already added to it needed a
second endpoint to keep it from polluting the main list. So these alerts follow
the ``unmatched-replies`` pattern instead: own list, own resolve action, a status
flip and never a delete — "a human was told about this RFQ and said they had it"
has to stay answerable later.

WHAT THE BRIEF ASKS FOR
-----------------------
"alerts listed; acknowledge clears; admin auth required."
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import notifications, notifications_store as ns
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    ADMIN_TOKEN, admin_headers, allowlist, install_fake_provider,
    isolate_notification_stores, notif_api,
)

ALERTS = "/api/admin/notification-alerts"

# A fixed weekday instant. The ladder counts BUSINESS hours (arc 4b S3), so an
# instant read off the wall clock makes these tests fail whenever "now + 30h"
# lands on a weekend. The send is stamped at NOW too, so nothing here reads
# the clock.
NOW = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)      # Tue 13:00 PT


def ack_path(alert_id: str) -> str:
    return f"{ALERTS}/{alert_id}/acknowledge"


@pytest.fixture
def admin_api(notif_api, monkeypatch):
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    notif_api._provider = provider
    return notif_api


def raise_escalation(run_id: str = "run-1") -> dict:
    alert = ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION,
                           dedupe_key=f"escalation:{run_id}",
                           account_id="acct-1", run_id=run_id,
                           supplier_domain="dxpe.com",
                           email="sales@dxpe.com", is_test=True)
    assert alert is not None
    return alert


# ---------------------------------------------------------------------------
# Admin auth — the gate, and its ordering
# ---------------------------------------------------------------------------

def test_no_token_is_a_401_and_a_wrong_token_a_403(admin_api):
    raise_escalation()
    assert admin_api.get(ALERTS).status_code == 401
    assert admin_api.get(ALERTS, headers={"Authorization": "Bearer nope"}
                         ).status_code == 403
    assert admin_api.post(ack_path("any")).status_code == 401
    assert admin_api.post(ack_path("any"),
                          headers={"Authorization": "Bearer nope"}
                          ).status_code == 403


def test_no_unauthenticated_caller_can_read_an_alert(admin_api):
    """The alert body carries a supplier's domain and a member's address; an
    unauthenticated caller must get nothing back but the rejection."""
    alert = raise_escalation()
    r = admin_api.get(ALERTS)
    assert r.status_code == 401
    assert "dxpe.com" not in r.text
    assert alert["id"] not in r.text


def test_the_flag_gate_runs_before_the_admin_gate(admin_api, monkeypatch):
    """Arc 2's ordering convention: with the flag off the route is ABSENT even
    to a valid admin token — the 404 must not depend on who is asking."""
    raise_escalation()
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    for r in (admin_api.get(ALERTS, headers=admin_headers()),
              admin_api.get(ALERTS),
              admin_api.post(ack_path("any"), headers=admin_headers())):
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_an_empty_queue_is_an_empty_list_not_an_error(admin_api):
    r = admin_api.get(ALERTS, headers=admin_headers())
    assert r.status_code == 200
    assert r.json() == {"count": 0, "alerts": []}


def test_every_alert_kind_the_arc_raises_is_listed(admin_api):
    """SUPERSEDED IN PART BY ARC 4b S6 (prime-directive exception).

    This used to assert that all four kinds appear in the admin queue. S6
    rules that wrong: a queue where every row is equally urgent has no urgency
    in it, and the first thing a human does with one is stop reading it.
    SOFT_BOUNCE_REPEATED is informational — a mailbox that failed temporarily
    three times running is not something to act on this afternoon — so it
    lands in the DIGEST tier, and the queue shows ACTION_NOW and QUEUE only.

    Same setup, new pinned behaviour: three of the four, and the fourth is
    proved to still EXIST (it is tiered away, not lost) below.
    """
    ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION, dedupe_key="e1",
                   run_id="run-1", supplier_domain="dxpe.com", is_test=True)
    ns.raise_alert(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS, dedupe_key="n1",
                   run_id="run-2", supplier_domain="dxpe.com", is_test=True)
    ns.raise_alert(kind=ns.ALERT_EMAIL_SUPPRESSED, dedupe_key="s1",
                   email="sales@dxpe.com", is_test=True)
    ns.raise_alert(kind=ns.ALERT_SOFT_BOUNCE_REPEATED, dedupe_key="b1",
                   email="buyer@dxpe.com", is_test=True)

    body = admin_api.get(ALERTS, headers=admin_headers()).json()
    assert body["count"] == 3
    assert {a["kind"] for a in body["alerts"]} == {
        ns.ALERT_RFQ_ESCALATION, ns.ALERT_NO_NOTIFIABLE_MEMBERS,
        ns.ALERT_EMAIL_SUPPRESSED}
    # ...and the fourth is deferred to the digest, not discarded.
    assert [a["kind"] for a in ns.list_alerts(tiers=(ns.TIER_DIGEST,))] == [
        ns.ALERT_SOFT_BOUNCE_REPEATED]


def test_an_alert_carries_what_an_operator_needs_to_act(admin_api):
    """An operator has to phone someone. That needs the supplier, the request
    and the address — an id and a kind would send them digging."""
    raise_escalation(run_id="run-42")
    (alert,) = admin_api.get(ALERTS, headers=admin_headers()).json()["alerts"]
    assert alert["kind"] == ns.ALERT_RFQ_ESCALATION
    assert alert["run_id"] == "run-42"
    assert alert["supplier_domain"] == "dxpe.com"
    assert alert["email"] == "sales@dxpe.com"
    assert alert["status"] == ns.ALERT_OPEN
    assert alert["created_at"]
    assert alert["acknowledged_at"] is None


def test_the_escalation_ladder_populates_this_queue(admin_api):
    """End to end: the thing the arc exists to produce is a row here."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="acct-1",
                               member_id="m-1", run_id="run-ladder",
                               supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com", is_test=True)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send", at=NOW.isoformat())
    notifications.run_escalations(NOW + timedelta(hours=30))

    body = admin_api.get(ALERTS, headers=admin_headers()).json()
    kinds = {a["kind"]: a for a in body["alerts"]}
    assert ns.ALERT_RFQ_ESCALATION in kinds
    assert kinds[ns.ALERT_RFQ_ESCALATION]["notification_id"] == n["id"]


def test_the_list_is_newest_first(admin_api):
    for i in range(3):
        ns.raise_alert(kind=ns.ALERT_RFQ_ESCALATION, dedupe_key=f"e{i}",
                       run_id=f"run-{i}", is_test=True)
    runs = [a["run_id"] for a in
            admin_api.get(ALERTS, headers=admin_headers()).json()["alerts"]]
    assert runs == sorted(runs, reverse=True) or runs == ["run-2", "run-1", "run-0"]


# ---------------------------------------------------------------------------
# Acknowledge
# ---------------------------------------------------------------------------

def test_acknowledge_clears_the_alert_from_the_open_list(admin_api):
    alert = raise_escalation()
    r = admin_api.post(ack_path(alert["id"]), headers=admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["alert"]["status"] == ns.ALERT_ACKNOWLEDGED
    assert body["alert"]["acknowledged_by"] == "admin"
    assert body["alert"]["acknowledged_at"]
    assert admin_api.get(ALERTS, headers=admin_headers()).json()["count"] == 0


def test_acknowledge_is_a_status_flip_never_a_delete(admin_api):
    """The row has to stay: "a human was told and said they had it" is an
    auditable fact, and deleting it erases the only record of the hand-off."""
    alert = raise_escalation()
    admin_api.post(ack_path(alert["id"]), headers=admin_headers())
    stored = ns.get_alert(alert["id"])
    assert stored is not None
    assert stored["status"] == ns.ALERT_ACKNOWLEDGED
    assert stored["run_id"] == "run-1"
    assert [a["kind"] for a in ns.list_alerts(status=ns.ALERT_ACKNOWLEDGED)] == \
        [ns.ALERT_RFQ_ESCALATION]


def test_acknowledging_one_alert_leaves_the_others_open(admin_api):
    first = raise_escalation(run_id="run-1")
    raise_escalation(run_id="run-2")
    admin_api.post(ack_path(first["id"]), headers=admin_headers())
    remaining = admin_api.get(ALERTS, headers=admin_headers()).json()["alerts"]
    assert [a["run_id"] for a in remaining] == ["run-2"]


def test_an_unknown_alert_is_a_404(admin_api):
    r = admin_api.post(ack_path("no-such-alert"), headers=admin_headers())
    assert r.status_code == 404
    assert r.json()["detail"] == "Alert not found"


def test_acknowledging_twice_is_a_409_not_a_silent_restamp(admin_api):
    """A double-click must not overwrite whoever acknowledged it first."""
    alert = raise_escalation()
    assert admin_api.post(ack_path(alert["id"]),
                          headers=admin_headers()).status_code == 200
    first_at = ns.get_alert(alert["id"])["acknowledged_at"]
    r = admin_api.post(ack_path(alert["id"]), headers=admin_headers())
    assert r.status_code == 409
    assert ns.get_alert(alert["id"])["acknowledged_at"] == first_at


def test_acknowledging_does_not_re_open_the_ladder(admin_api):
    """Acknowledging says a human has it — it must not reset the notification's
    escalated_at and start the mail sequence over."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, account_id="acct-1",
                               member_id="m-1", run_id="run-ack",
                               supplier_domain="dxpe.com",
                               recipient="sales@dxpe.com", is_test=True)
    ns.transition(n["id"], ns.STATE_SENT, event_type="Send", at=NOW.isoformat())
    later = NOW + timedelta(hours=30)
    notifications.run_escalations(later)
    (alert,) = [a for a in ns.list_alerts(kind=ns.ALERT_RFQ_ESCALATION)]
    admin_api.post(ack_path(alert["id"]), headers=admin_headers())

    again = notifications.run_escalations(later + timedelta(hours=30))
    assert (again["reminded"], again["alerted"]) == (0, 0)
    assert admin_api._provider.outbox == []
    assert ns.get_notification(n["id"])["escalated_at"]

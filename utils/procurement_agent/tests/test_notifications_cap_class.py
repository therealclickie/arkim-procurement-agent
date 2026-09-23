"""
Arc 4b T1 / R-F8 — notification mail has its OWN governance class and cap.

WHAT WAS WRONG, AND WHY IT MATTERED
-----------------------------------
``utils/notifications.py`` set no ``message_class``, so ``send_governance``
applied the absent-class default and judged notification mail against the RFQ
daily cap. Two consequences, both failures of the product's one promise:
notification mail competed with cold outbound for a budget sized for cold
outbound, and once that budget was spent the supplier stopped being told about
RFQs **already sitting in their inbox**. It also wrote no ``sent_messages``
row, so the volume was invisible to the ledger that the cap itself counts.

REVIEWER R3 IS THE POINT OF THIS FILE. Every cap assertion below drives the
REAL ``send_governance`` stack against a real (tmp_path) governance store —
the RFQ cap is exhausted by writing real ledger rows and the verdict comes
from the real gate. Nothing about caps is mocked; ``FakeProvider`` sits BELOW
governance, so a message that reaches it has provably passed the real gate.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from utils import (notifications, notifications_store as ns, send_governance,
                   supplier_registry)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores,
)


@pytest.fixture
def gov(tmp_path, monkeypatch):
    """Arc-4 stores, NOTIFICATIONS_V1 on, SEND_GOVERNANCE_V1 on, dxpe.com
    allowlisted on the REAL governance store, a fake transport installed."""
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def notification(recipient: str = "sales@dxpe.com") -> dict:
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, recipient=recipient,
                               supplier_domain="dxpe.com", run_id="run-1",
                               is_test=True)
    assert n is not None
    return n


def send(n: dict, recipient: str = "sales@dxpe.com") -> dict:
    return notifications._send_notification_mail(
        n, subject="A quote request is waiting", body="body",
        recipient=recipient)


def exhaust_rfq_cap(monkeypatch, *, cap: int = 1) -> None:
    """Spend the ENTIRE RFQ daily budget with real, rfq-class ledger rows."""
    monkeypatch.setenv("SEND_GOVERNANCE_DAILY_CAP", str(cap))
    for i in range(cap):
        supplier_registry.record_sent_message(
            run_id=f"r{i}", supplier_domain="dxpe.com", vendor_name="DXP",
            to=["sales@dxpe.com"], status="sent")
    assert supplier_registry.count_send_attempts_utc_day() == cap


# ---------------------------------------------------------------------------
# The headline: a notification survives an exhausted RFQ cap (criterion 4)
# ---------------------------------------------------------------------------

def test_a_notification_sends_after_the_rfq_daily_cap_is_exhausted(gov, monkeypatch):
    """SUCCESS CRITERION 4, against the real gate.

    The RFQ budget is spent in full. A notification about an RFQ the supplier
    already has still goes out, because it is no longer judged by that budget.
    """
    exhaust_rfq_cap(monkeypatch, cap=1)
    out = send(notification())
    assert out["state"] == ns.STATE_SENT
    assert len(gov.outbox) == 1
    assert gov.outbox[0]["to"] == ["sales@dxpe.com"]
    # ...and the RFQ cap really is shut: cold outbound is still blocked.
    from utils.email_sender import EmailMessage, GmailSender
    rfq = EmailMessage(to=["sales@dxpe.com"], subject="Quote request",
                       body="hello", metadata={})
    assert GmailSender().send(rfq).status == "cap_blocked"


def test_notification_mail_is_counted_in_its_own_class_not_the_rfq_one(gov):
    send(notification())
    assert supplier_registry.count_send_attempts_utc_day(
        message_class=supplier_registry.MESSAGE_CLASS_NOTIFICATION) == 1
    assert supplier_registry.count_send_attempts_utc_day() == 0
    assert supplier_registry.count_send_attempts_utc_day(
        message_class=supplier_registry.MESSAGE_CLASS_AUTH) == 0


# ---------------------------------------------------------------------------
# The ledger row (R-F8's second half)
# ---------------------------------------------------------------------------

def test_notification_mail_writes_a_ledger_row(gov):
    send(notification())
    rows = supplier_registry.get_sent_messages()
    assert len(rows) == 1
    assert rows[0]["message_class"] == supplier_registry.MESSAGE_CLASS_NOTIFICATION
    assert rows[0]["status"] == "sent"
    assert rows[0]["subject"] == "A quote request is waiting"
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["recipients_to"] == ["sales@dxpe.com"]


def test_a_notification_row_never_appears_in_the_rfq_ledger_for_a_domain(gov):
    """``get_sent_messages(domain=...)`` IS the RFQ-ledger read the portal
    inbox and the admin RFQ views are built on. A notification is not an RFQ,
    so it must not show up there — the recipient address on the row is what
    keeps the account recoverable."""
    send(notification())
    assert supplier_registry.get_sent_messages(domain="dxpe.com") == []


def test_a_blocked_notification_records_the_block_honestly(gov, monkeypatch):
    """The pre-attempt row survives a blocked send and carries the real
    verdict — the same record-before-attempt discipline as rfq_send."""
    monkeypatch.setenv("NOTIFICATION_DAILY_CAP", "0")
    out = send(notification())
    assert out["state"] == ns.STATE_SUPPRESSED
    row = supplier_registry.get_sent_messages()[0]
    assert row["status"] == "cap_blocked"
    assert gov.outbox == []


# ---------------------------------------------------------------------------
# The notification cap itself, and its alert
# ---------------------------------------------------------------------------

def test_exceeding_the_notification_cap_suppresses_and_alerts(gov, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_DAILY_CAP", "1")
    assert send(notification())["state"] == ns.STATE_SENT
    second = send(notification())
    assert second["state"] == ns.STATE_SUPPRESSED
    assert len(gov.outbox) == 1, "nothing is sent past the cap"

    alerts = ns.list_alerts(kind=ns.ALERT_NOTIFICATION_CAP_BLOCKED)
    assert len(alerts) == 1
    assert alerts[0]["supplier_domain"] == "dxpe.com"
    assert alerts[0]["status"] == ns.ALERT_OPEN


def test_the_cap_alert_is_deduped_within_a_day_and_rearms_the_next(gov, monkeypatch):
    """One alert per day, however many sends the cap suppresses — and a new
    day raises its own, because the dedupe key embeds the UTC day."""
    monkeypatch.setenv("NOTIFICATION_DAILY_CAP", "0")
    for _ in range(4):
        send(notification())
    assert len(ns.list_alerts(kind=ns.ALERT_NOTIFICATION_CAP_BLOCKED)) == 1

    tomorrow = datetime(2099, 1, 2, 12, 0, tzinfo=timezone.utc)
    notifications._alert_notification_cap_blocked(notification(), now=tomorrow)
    days = {(a.get("detail") or {}).get("day")
            for a in ns.list_alerts(kind=ns.ALERT_NOTIFICATION_CAP_BLOCKED)}
    assert len(days) == 2 and "2099-01-02" in days


def test_the_cap_is_configurable_and_generous_by_default(gov):
    assert send_governance.DEFAULT_NOTIFICATION_DAILY_CAP >= 100
    assert send_governance.DEFAULT_NOTIFICATION_DAILY_CAP > \
        send_governance.DEFAULT_DAILY_CAP


def test_an_absent_message_class_still_resolves_to_the_rfq_cap(gov, monkeypatch):
    """The absent-class default is unchanged: every message built elsewhere in
    the repo is capped exactly as it was before this class existed."""
    from utils.email_sender import EmailMessage, GmailSender
    exhaust_rfq_cap(monkeypatch, cap=1)
    msg = EmailMessage(to=["sales@dxpe.com"], subject="s", body="b", metadata={})
    assert GmailSender().send(msg).status == "cap_blocked"


# ---------------------------------------------------------------------------
# Flag OFF — today's behaviour exactly
# ---------------------------------------------------------------------------

def test_flag_off_writes_no_notification_ledger_row(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    assert notifications.notify_rfq_new(
        {"run_id": "run-1", "supplier_domain": "dxpe.com",
         "sent_message_id": "sm-1"}, None) == []
    assert supplier_registry.get_sent_messages() == []
    assert ns.list_alerts(status=None) == []

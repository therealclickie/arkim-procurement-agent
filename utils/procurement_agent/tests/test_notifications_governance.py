"""
Arc 4 T3 — governance integration (D1) and the auth-mail ledger (D9).

REVIEWER R5 IS THE POINT OF THIS FILE. Every governance assertion here drives
the REAL ``send_governance`` stack against a real (tmp_path) governance store.
Nothing about suppression, the allowlist or the caps is mocked — the closest
this file comes to a double is ``FakeProvider``, which stands in for AWS and
sits BELOW governance, so a message that reaches it has provably passed the
real gate.
"""
from __future__ import annotations

import pytest

from utils import (email_sender, mail_provider, notifications,
                   notifications_store as ns, send_governance,
                   supplier_accounts, supplier_registry)
from utils.email_sender import EmailMessage, GmailSender
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores, notif_stores,
)


@pytest.fixture
def gov(tmp_path, monkeypatch):
    """Arc-4 stores, NOTIFICATIONS_V1 on, SEND_GOVERNANCE_V1 on, a fake
    transport installed. ``SEND_GOVERNANCE_V1`` is pinned OFF by conftest's
    autouse fixture, so turning it on here is an explicit opt-in."""
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    return provider


def msg(to="sales@dxpe.com", **meta) -> EmailMessage:
    return EmailMessage(to=[to], subject="Quote request", body="hello",
                        metadata=meta)


# ---------------------------------------------------------------------------
# D1 — nothing bypasses governance
# ---------------------------------------------------------------------------

def test_non_allowlisted_recipient_is_blocked_by_the_real_gate(gov):
    """The allowlist is empty, and it is fail-closed: nothing reaches the
    provider. No mock anywhere in this assertion."""
    assert send_governance.send_governance_active() is True
    result = GmailSender().send(msg())
    assert result.status == "not_allowlisted"
    assert gov.outbox == []


def test_allowlisted_recipient_reaches_the_provider(gov):
    allowlist("dxpe.com")
    result = GmailSender().send(msg())
    assert result.status == "sent"
    assert [m["to"] for m in gov.outbox] == [["sales@dxpe.com"]]


def test_suppressed_domain_beats_the_allowlist(gov):
    allowlist("dxpe.com")
    send_governance.suppression_add("dxpe.com", added_by="test", is_test=True)
    assert GmailSender().send(msg()).status == "suppressed"
    assert gov.outbox == []


def test_a_notification_to_a_non_allowlisted_member_lands_SUPPRESSED(gov):
    """D1 end-to-end: the notification's own state reflects the REAL gate's
    verdict, not a fake success."""
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, recipient="sales@dxpe.com",
                               supplier_domain="dxpe.com", is_test=True)
    out = notifications._send_notification_mail(
        n, subject="s", body="b", recipient="sales@dxpe.com")
    assert out["state"] == ns.STATE_SUPPRESSED
    assert gov.outbox == []


def test_a_notification_to_an_allowlisted_member_lands_SENT(gov):
    allowlist("dxpe.com")
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, recipient="sales@dxpe.com",
                               supplier_domain="dxpe.com", is_test=True)
    out = notifications._send_notification_mail(
        n, subject="s", body="b", recipient="sales@dxpe.com")
    assert out["state"] == ns.STATE_SENT
    assert out["provider_message_id"] == gov.outbox[0]["provider_message_id"]


def test_the_daily_cap_is_respected_by_notification_mail(gov, monkeypatch):
    """SUPERSEDED BY ARC 4b R-F8 (prime-directive exception).

    This scenario used to assert that an exhausted RFQ cap SUPPRESSED
    notification mail. R-F8 rules that wrong: a notification goes to an
    allowlisted, opted-in member about a message already sent to them, so
    starving it on the cold-outbound budget silences the product's one promise
    exactly when it matters. Same setup, new pinned behaviour — the RFQ cap no
    longer governs notification mail.

    The cap invariant itself survives, in its new form, in the test below.
    """
    allowlist("dxpe.com")
    monkeypatch.setenv("SEND_GOVERNANCE_DAILY_CAP", "1")
    supplier_registry.record_sent_message(
        run_id="r1", supplier_domain="dxpe.com", vendor_name="DXP",
        to=["sales@dxpe.com"], status="sent")
    n = ns.create_notification(kind=ns.KIND_RFQ_NEW, recipient="sales@dxpe.com",
                               supplier_domain="dxpe.com", is_test=True)
    out = notifications._send_notification_mail(
        n, subject="s", body="b", recipient="sales@dxpe.com")
    assert out["state"] == ns.STATE_SENT
    assert [m["to"] for m in gov.outbox] == [["sales@dxpe.com"]]


def test_notification_mail_is_suppressed_by_its_OWN_cap_and_never_sent_past_it(
        gov, monkeypatch):
    """The invariant the superseded test carried, in its new form (R-F8): a
    notification IS suppressed by a cap, and nothing goes out past it — the
    cap is now ``NOTIFICATION_DAILY_CAP``, not the RFQ one."""
    allowlist("dxpe.com")
    monkeypatch.setenv("NOTIFICATION_DAILY_CAP", "1")
    for _ in range(2):
        n = ns.create_notification(kind=ns.KIND_RFQ_NEW,
                                   recipient="sales@dxpe.com",
                                   supplier_domain="dxpe.com", is_test=True)
        out = notifications._send_notification_mail(
            n, subject="s", body="b", recipient="sales@dxpe.com")
    assert out["state"] == ns.STATE_SUPPRESSED
    assert len(gov.outbox) == 1


# ---------------------------------------------------------------------------
# D9 — auth mail joins the ledger (closes arc 2 review finding 2)
# ---------------------------------------------------------------------------

def test_magic_link_send_now_appears_in_the_ledger(gov):
    allowlist("dxpe.com")
    acct = supplier_accounts.create_account("dxpe.com")
    member = supplier_accounts.add_member(acct["id"], "sales@dxpe.com",
                                          status=supplier_accounts.MEMBER_ACTIVE)
    status = supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "raw-token-value", account_domain="dxpe.com",
        member_id=member["id"])
    assert status == "sent"
    rows = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert len(rows) == 1
    assert rows[0]["message_class"] == supplier_registry.MESSAGE_CLASS_AUTH
    assert rows[0]["status"] == "sent"


def test_the_ledgered_auth_row_never_contains_the_raw_token(gov):
    """The body is deliberately not ledgered: a magic-link body IS the
    credential, and the ledger must not become the one place it is written
    down."""
    allowlist("dxpe.com")
    supplier_accounts.create_account("dxpe.com")
    supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "super-secret-token", account_domain="dxpe.com")
    row = supplier_registry.get_sent_messages(domain="dxpe.com")[0]
    assert row["body"] is None
    assert "super-secret-token" not in str(row)


def test_auth_mail_is_still_gated_by_the_real_allowlist(gov):
    """D9 adds accounting; it does not relax the gate."""
    supplier_accounts.create_account("dxpe.com")
    status = supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "t", account_domain="dxpe.com")
    assert status == "not_allowlisted"
    assert gov.outbox == []
    # The pre-attempt row still exists, recording the blocked outcome honestly.
    row = supplier_registry.get_sent_messages(domain="dxpe.com")[0]
    assert row["status"] == "not_allowlisted"


def test_auth_mail_has_its_own_cap_and_cannot_starve_the_rfq_cap(gov, monkeypatch):
    """Gate FINDING F2, asserted: with the RFQ cap at 1 and one auth row
    already in the ledger, an RFQ still sends — because the auth row is in a
    different cap class."""
    allowlist("dxpe.com")
    monkeypatch.setenv("SEND_GOVERNANCE_DAILY_CAP", "1")
    supplier_accounts.create_account("dxpe.com")
    supplier_accounts.send_magic_link_email("sales@dxpe.com", "t",
                                            account_domain="dxpe.com")
    assert supplier_registry.count_send_attempts_utc_day(
        message_class=supplier_registry.MESSAGE_CLASS_AUTH) == 1
    assert supplier_registry.count_send_attempts_utc_day() == 0
    assert GmailSender().send(msg()).status == "sent"


def test_the_auth_cap_itself_is_enforced(gov, monkeypatch):
    allowlist("dxpe.com")
    monkeypatch.setenv("SEND_GOVERNANCE_AUTH_DAILY_CAP", "1")
    supplier_accounts.create_account("dxpe.com")
    assert supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "t1", account_domain="dxpe.com") == "sent"
    assert supplier_accounts.send_magic_link_email(
        "sales@dxpe.com", "t2", account_domain="dxpe.com") == "cap_blocked"


def test_the_arc2_in_process_rate_limiter_is_still_active(notif_stores, monkeypatch):
    """D9 keeps arc 2's limiter as defence in depth — it is not replaced by
    the ledger, and the api_server module still declares it."""
    import api_server
    assert hasattr(api_server, "_supplier_auth_rate_buckets")
    assert api_server._env_int("SUPPLIER_AUTH_RATE_LIMIT_PER_EMAIL", 3) >= 1


# ---------------------------------------------------------------------------
# Flag OFF — today's behaviour exactly
# ---------------------------------------------------------------------------

def test_flag_off_writes_no_ledger_row_for_auth_mail(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    supplier_accounts.create_account("dxpe.com")
    supplier_accounts.send_magic_link_email("sales@dxpe.com", "t",
                                            account_domain="dxpe.com")
    assert supplier_registry.get_sent_messages(domain="dxpe.com") == []


def test_flag_off_auth_mail_carries_no_arc4_metadata(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    captured = []

    class Recording(email_sender.GmailSender):
        def send(self, message):
            captured.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", Recording)
    supplier_accounts.create_account("dxpe.com")
    supplier_accounts.send_magic_link_email("sales@dxpe.com", "t",
                                            account_domain="dxpe.com")
    meta = captured[0].metadata
    assert "auth_mail" not in meta and "message_class" not in meta


def test_the_default_cap_query_is_unchanged_for_pre_arc4_rows(notif_stores):
    """A row written without a message_class — i.e. every row in the database
    before this arc — still counts against the RFQ cap exactly as it did."""
    supplier_registry.record_sent_message(
        run_id="r1", supplier_domain="dxpe.com", vendor_name="DXP",
        to=["sales@dxpe.com"], status="sent")
    assert supplier_registry.count_send_attempts_utc_day() == 1

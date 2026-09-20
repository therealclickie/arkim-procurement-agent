"""
Arc 4 T4 — the RFQ_NEW fan-out, hooked at the ``rfq_send`` seam (Q1 RULED).

The brief's list: "fan-out respects VIEW_REQUESTS and prefs; DAILY_DIGEST
members are deferred not sent; NONE members skipped; zero-notifiable-members
raises a concierge alert; the notification's subject ref is the sent_messages
row the inbox will render (so RfqView in T7 can bind to it); a TIER1_FYI kind,
if emitted, never enters the escalation ladder."

THE SUBJECT-REF TEST IS THE Q1 RULING MADE FALSIFIABLE. It asserts that the
row a notification points at is the same row ``_supplier_open_requests``
returns — which is what makes ``RfqView`` bindable and the ladder meaningful.
Hooked at ``tier1_notify`` instead, that assertion could not be written at all.
"""
from __future__ import annotations

import pytest

from utils import (notifications, notifications_store as ns, supplier_accounts,
                   supplier_registry)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    allowlist, install_fake_provider, isolate_notification_stores, open_rfq,
)


@pytest.fixture
def fanout(tmp_path, monkeypatch):
    """Arc-4 stores + flags, a fake transport, and dxpe.com allowlisted so the
    real governance gate lets notification mail through."""
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def account_with(*members, domain="dxpe.com"):
    """An account plus members, each ``(email, role, status)``."""
    acct = supplier_accounts.create_account(domain)
    out = []
    for email, role, status in members:
        out.append(supplier_accounts.add_member(acct["id"], email, role=role,
                                                status=status))
    return acct, out


def rfq(sent_message_id="sm-1", run_id="run-1", domain="dxpe.com") -> dict:
    return {"run_id": run_id, "supplier_domain": domain,
            "sent_message_id": sent_message_id,
            "manufacturer": "Goulds", "part_number": "3196", "quantity": 2}


# ---------------------------------------------------------------------------
# Fan-out: who gets notified
# ---------------------------------------------------------------------------

def test_every_active_member_with_view_requests_gets_one_notification(fanout):
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE),
                           ("staff@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_ACTIVE))
    created = notifications.notify_rfq_new(rfq(), acct)
    assert sorted(n["recipient"] for n in created) == ["owner@dxpe.com",
                                                       "staff@dxpe.com"]
    assert all(n["state"] == ns.STATE_SENT for n in created)
    assert len(fanout.outbox) == 2


def test_pending_and_revoked_members_are_not_notified(fanout):
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE),
                           ("pending@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_PENDING),
                           ("gone@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_REVOKED))
    created = notifications.notify_rfq_new(rfq(), acct)
    assert [n["recipient"] for n in created] == ["owner@dxpe.com"]


def test_fanout_goes_through_the_rbac_matrix_not_a_role_comparison(fanout, monkeypatch):
    """VIEW_REQUESTS is asked of the matrix. Take the capability away from
    MEMBER and the fan-out follows — which it could not do if the code
    compared role names inline."""
    from utils import supplier_accounts_rbac as rbac
    acct, _ = account_with(("staff@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_ACTIVE))
    monkeypatch.setitem(rbac.CAPABILITY_MATRIX, supplier_accounts.ROLE_MEMBER,
                        frozenset({rbac.SUBMIT_QUOTES}))
    assert notifications.notify_rfq_new(rfq(), acct) == []
    assert ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)


def test_suppressed_member_is_excluded_from_the_fanout(fanout):
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE),
                           ("bounced@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_ACTIVE))
    ns.suppress_email("bounced@dxpe.com", reason="hard_bounce")
    created = notifications.notify_rfq_new(rfq(), acct)
    assert [n["recipient"] for n in created] == ["owner@dxpe.com"]


# ---------------------------------------------------------------------------
# Preferences (D7)
# ---------------------------------------------------------------------------

def test_daily_digest_members_are_deferred_not_sent(fanout):
    acct, members = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                                  supplier_accounts.MEMBER_ACTIVE))
    ns.set_preference(members[0]["id"], ns.PREF_DAILY_DIGEST)
    created = notifications.notify_rfq_new(rfq(), acct)
    assert created[0]["deferred"] == 1
    assert created[0]["state"] == ns.STATE_QUEUED
    assert fanout.outbox == []


def test_none_members_are_skipped_for_rfq_mail(fanout):
    acct, members = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                                  supplier_accounts.MEMBER_ACTIVE))
    ns.set_preference(members[0]["id"], ns.PREF_NONE)
    assert notifications.notify_rfq_new(rfq(), acct) == []
    assert fanout.outbox == []


def test_none_members_still_receive_auth_mail(fanout):
    """D7 is explicit: NONE silences RFQ mail, never the sign-in link a
    person needs to get into the portal at all."""
    acct, members = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                                  supplier_accounts.MEMBER_ACTIVE))
    ns.set_preference(members[0]["id"], ns.PREF_NONE)
    status = supplier_accounts.send_magic_link_email(
        "owner@dxpe.com", "tok", account_domain="dxpe.com",
        member_id=members[0]["id"])
    assert status == "sent"


def test_default_preference_is_immediate_without_any_stored_row(fanout):
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE))
    assert notifications.notify_rfq_new(rfq(), acct)[0]["state"] == ns.STATE_SENT


# ---------------------------------------------------------------------------
# Zero notifiable members ⇒ a concierge alert (D7)
# ---------------------------------------------------------------------------

def test_account_with_no_notifiable_members_raises_an_alert(fanout):
    acct, _ = account_with(("pending@dxpe.com", supplier_accounts.ROLE_MEMBER,
                            supplier_accounts.MEMBER_PENDING))
    assert notifications.notify_rfq_new(rfq(), acct) == []
    alerts = ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)
    assert len(alerts) == 1 and alerts[0]["run_id"] == "run-1"


def test_no_account_at_all_raises_the_same_alert(fanout):
    assert notifications.notify_rfq_new(rfq(), None) == []
    assert len(ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)) == 1


def test_the_no_members_alert_is_deduplicated_per_run(fanout):
    notifications.notify_rfq_new(rfq(), None)
    notifications.notify_rfq_new(rfq(), None)
    assert len(ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)) == 1


# ---------------------------------------------------------------------------
# The Q1 ruling: the subject ref is the row the inbox renders
# ---------------------------------------------------------------------------

def test_subject_ref_is_the_sent_messages_row_the_inbox_will_render(fanout):
    """The load-bearing assertion of the Q1 ruling."""
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE))
    row_id = open_rfq(run_id="run-1")
    created = notifications.notify_rfq_new(rfq(sent_message_id=row_id), acct)
    assert created[0]["subject_ref"] == row_id

    ledger = supplier_registry.get_sent_messages(domain="dxpe.com")
    assert [r["id"] for r in ledger] == [row_id]
    assert ledger[0]["status"] in supplier_registry.OPEN_RFQ_STATUSES
    # ...and the run id on the notification is the key RfqView binds on (T7).
    assert created[0]["run_id"] == ledger[0]["run_id"]


def test_the_notified_run_is_the_one_the_open_requests_service_returns(fanout, monkeypatch):
    """End-to-end against the REAL shared read service the portal inbox uses."""
    import api_server
    acct, _ = account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                            supplier_accounts.MEMBER_ACTIVE))
    row_id = open_rfq(run_id="run-1")
    monkeypatch.setattr(api_server, "_run_specs_for_quote",
                        lambda rid: {"manufacturer": "Goulds",
                                     "part_number": "3196", "quantity": 2})
    created = notifications.notify_rfq_new(rfq(sent_message_id=row_id), acct)
    rendered = api_server._supplier_open_requests("dxpe.com")
    assert [r["run_id"] for r in rendered] == [created[0]["run_id"]]


# ---------------------------------------------------------------------------
# The rfq_send hook itself
# ---------------------------------------------------------------------------

def test_notify_rfq_sent_fires_for_an_open_status(fanout):
    account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                  supplier_accounts.MEMBER_ACTIVE))
    created = notifications.notify_rfq_sent(
        sent_message_id="sm-1", run_id="run-1", supplier_domain="dxpe.com",
        status="sent")
    assert len(created) == 1 and created[0]["kind"] == ns.KIND_RFQ_NEW


@pytest.mark.parametrize("status", ["not_allowlisted", "cap_blocked",
                                    "suppressed", "error"])
def test_notify_rfq_sent_does_not_fire_for_a_send_with_no_inbox_row(fanout, status):
    """A blocked or errored send produces no row in OPEN_RFQ_STATUSES, so the
    supplier has nothing to view — notifying would create a notification that
    can only ever escalate."""
    account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                  supplier_accounts.MEMBER_ACTIVE))
    assert notifications.notify_rfq_sent(
        sent_message_id="sm-1", run_id="run-1", supplier_domain="dxpe.com",
        status=status) == []
    assert ns.list_notifications() == []


def test_send_rfq_drives_the_hook_end_to_end(fanout, monkeypatch):
    """The seam is wired, not merely importable: a real ``send_rfq`` call
    produces both the ledger row and the notification bound to it."""
    from utils import rfq_send
    from utils.rfq_send import Approval
    account_with(("owner@dxpe.com", supplier_accounts.ROLE_OWNER,
                  supplier_accounts.MEMBER_ACTIVE))
    supplier_registry.upsert_contact("dxpe.com",
                                     {"contact_email": "sales@dxpe.com",
                                      "contact_method": "generic_inbox",
                                      "contact_status": "resolved"})
    monkeypatch.setattr(rfq_send, "write_audit_log", lambda payload: None)
    out = rfq_send.send_rfq(
        {"vendor_name": "DXP", "source_url": "https://dxpe.com"},
        "Subject: Quote request\n\nplease quote",
        Approval(approved_by="tester"), run_id="run-1", part_key="goulds|3196")
    assert out["sent_message_id"]
    created = ns.list_notifications(kind=ns.KIND_RFQ_NEW)
    assert len(created) == 1
    assert created[0]["subject_ref"] == out["sent_message_id"]
    assert created[0]["run_id"] == "run-1"


def test_a_notification_failure_never_breaks_the_send(fanout, monkeypatch):
    """The deliberate asymmetry: tracking degrades, the product does not."""
    from utils import rfq_send
    from utils.rfq_send import Approval
    supplier_registry.upsert_contact("dxpe.com",
                                     {"contact_email": "sales@dxpe.com",
                                      "contact_method": "generic_inbox",
                                      "contact_status": "resolved"})
    monkeypatch.setattr(rfq_send, "write_audit_log", lambda payload: None)
    monkeypatch.setattr(notifications, "notify_rfq_sent",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    out = rfq_send.send_rfq(
        {"vendor_name": "DXP", "source_url": "https://dxpe.com"},
        "Subject: Quote request\n\nplease quote",
        Approval(approved_by="tester"), run_id="run-1", part_key="goulds|3196")
    assert out["status"] == "sent" and out["sent_message_id"]


def test_flag_off_writes_no_notification_rows(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    acct = supplier_accounts.create_account("dxpe.com")
    assert notifications.notify_rfq_new(rfq(), acct) == []
    assert notifications.notify_rfq_sent(sent_message_id="sm-1", run_id="run-1",
                                         supplier_domain="dxpe.com",
                                         status="sent") == []
    assert ns.list_notifications() == []


# ---------------------------------------------------------------------------
# TIER1_FYI is tracked but never enters the ladder
# ---------------------------------------------------------------------------

def test_tier1_fyi_is_tracked(fanout):
    n = notifications.notify_tier1_fyi(supplier_domain="dxpe.com", run_id="run-9")
    assert n["kind"] == ns.KIND_TIER1_FYI and n["state"] == ns.STATE_QUEUED


def test_tier1_fyi_never_enters_the_escalation_ladder(fanout):
    """T4's explicit exclusion: the FYI has no sent_messages row, so there is
    nothing to view — inside the ladder it would escalate every single time."""
    from datetime import datetime, timedelta, timezone
    n = notifications.notify_tier1_fyi(supplier_domain="dxpe.com", run_id="run-9")
    assert ns.KIND_TIER1_FYI not in ns.ESCALATABLE_KINDS
    later = datetime.now(timezone.utc) + timedelta(days=30)
    assert notifications.decide_escalation(ns.get_notification(n["id"]),
                                           now=later, remind_after=4,
                                           alert_after=24) is None
    out = notifications.run_escalations(now=later)
    assert out == {"reminded": 0, "alerted": 0, "considered": 0}

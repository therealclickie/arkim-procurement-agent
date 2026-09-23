"""
Arc 4b T6 / S1 — RFQ mail goes to DESIGNATED contacts, not to everyone.

THE PROBLEM S1 FIXES IS OWNERSHIP, NOT VOLUME
---------------------------------------------
Arc 4 fanned RFQ_NEW out to every ACTIVE member holding ``VIEW_REQUESTS``. A
five-person account therefore received five emails for one request — and each
of the five could reasonably assume one of the other four was handling it. A
signal with five owners has none. One request, one owner.

``VIEW_REQUESTS`` still decides who may SEE a request in the portal. The new
flag decides only who is MAILED about it. They are different questions, and a
member who can see requests but is not the person who answers them is exactly
the case the default exists for.
"""
from __future__ import annotations

import pytest

from utils import (notifications, notifications_store as ns, supplier_accounts,
                   supplier_accounts_rbac as rbac)
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    APP_ORIGIN, active_member, allowlist, install_fake_provider,
    isolate_notification_stores, login, notif_api,
)


@pytest.fixture
def fanout(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
    provider = install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    return provider


def five_member_account():
    """One OWNER, one ADMIN, three MEMBERs — the S1 scenario, verbatim."""
    acct = supplier_accounts.create_account("dxpe.com")
    members = [
        supplier_accounts.add_member(acct["id"], "owner@dxpe.com",
                                     role=supplier_accounts.ROLE_OWNER,
                                     status=supplier_accounts.MEMBER_ACTIVE),
        supplier_accounts.add_member(acct["id"], "admin@dxpe.com",
                                     role=supplier_accounts.ROLE_ADMIN,
                                     status=supplier_accounts.MEMBER_ACTIVE),
    ]
    for i in range(3):
        members.append(supplier_accounts.add_member(
            acct["id"], f"rep{i}@dxpe.com", role=supplier_accounts.ROLE_MEMBER,
            status=supplier_accounts.MEMBER_ACTIVE))
    return acct, members


def rfq(run_id="run-1") -> dict:
    return {"run_id": run_id, "supplier_domain": "dxpe.com",
            "sent_message_id": "sm-1", "manufacturer": "Goulds",
            "part_number": "3196", "quantity": 2}


# ---------------------------------------------------------------------------
# The default designation
# ---------------------------------------------------------------------------

def test_a_five_member_account_produces_exactly_two_rfq_notifications(fanout):
    """S1's headline number: 1 owner + 1 admin + 3 members → 2, not 5."""
    acct, _ = five_member_account()
    created = notifications.notify_rfq_new(rfq(), acct)
    assert sorted(n["recipient"] for n in created) == ["admin@dxpe.com",
                                                       "owner@dxpe.com"]
    assert len(created) == 2


@pytest.mark.parametrize("role,expected", [
    (supplier_accounts.ROLE_OWNER, True),
    (supplier_accounts.ROLE_ADMIN, True),
    (supplier_accounts.ROLE_MEMBER, False),
])
def test_the_role_default_table(fanout, role, expected):
    """A pure table over the rule, with no store and no fan-out — so the
    default is stated once and changing it is one line, not a hunt."""
    assert supplier_accounts.member_receives_rfq({"role": role}) is expected


def test_an_absent_member_is_never_a_contact(fanout):
    assert supplier_accounts.member_receives_rfq(None) is False
    assert supplier_accounts.member_receives_rfq({}) is False


# ---------------------------------------------------------------------------
# Opting in and out
# ---------------------------------------------------------------------------

def test_a_member_who_opts_in_receives_rfq_mail(fanout):
    acct, members = five_member_account()
    assert supplier_accounts.set_member_receives_rfq(members[2]["id"], True)
    created = notifications.notify_rfq_new(rfq(), acct)
    assert sorted(n["recipient"] for n in created) == [
        "admin@dxpe.com", "owner@dxpe.com", "rep0@dxpe.com"]


def test_an_admin_who_opts_out_stops_receiving_rfq_mail(fanout):
    acct, members = five_member_account()
    assert supplier_accounts.set_member_receives_rfq(members[1]["id"], False)
    created = notifications.notify_rfq_new(rfq(), acct)
    assert [n["recipient"] for n in created] == ["owner@dxpe.com"]


def test_clearing_the_flag_restores_the_role_default(fanout):
    """``None`` means "follow the default", NOT "write the default's current
    value" — so a later change to the product default reaches the people who
    never chose, and only them."""
    acct, members = five_member_account()
    supplier_accounts.set_member_receives_rfq(members[1]["id"], False)
    restored = supplier_accounts.set_member_receives_rfq(members[1]["id"], None)
    assert restored["receives_rfq"] is None
    assert supplier_accounts.member_receives_rfq(restored) is True


def test_an_account_whose_contacts_all_opt_out_raises_the_concierge_alert(fanout):
    """The request has arrived and there is nobody to tell — a human has to
    know that, rather than the request evaporating quietly."""
    acct, members = five_member_account()
    for m in members[:2]:
        supplier_accounts.set_member_receives_rfq(m["id"], False)
    assert notifications.notify_rfq_new(rfq(), acct) == []
    assert len(ns.list_alerts(kind=ns.ALERT_NO_NOTIFIABLE_MEMBERS)) == 1


def test_a_designated_contact_who_loses_view_requests_is_dropped(fanout, monkeypatch):
    """S1 NARROWS the arc-4 rule; it does not replace it. A member who may not
    see requests is not mailed about them however they are designated."""
    acct, members = five_member_account()
    supplier_accounts.set_member_receives_rfq(members[2]["id"], True)
    monkeypatch.setitem(rbac.CAPABILITY_MATRIX, supplier_accounts.ROLE_MEMBER,
                        frozenset({rbac.SUBMIT_QUOTES}))
    created = notifications.notify_rfq_new(rfq(), acct)
    assert sorted(n["recipient"] for n in created) == ["admin@dxpe.com",
                                                       "owner@dxpe.com"]


def test_a_suppressed_designated_contact_is_still_excluded(fanout):
    acct, _ = five_member_account()
    ns.suppress_email("admin@dxpe.com", reason="hard_bounce")
    created = notifications.notify_rfq_new(rfq(), acct)
    assert [n["recipient"] for n in created] == ["owner@dxpe.com"]


# ---------------------------------------------------------------------------
# Server enforcement (not a hidden control)
# ---------------------------------------------------------------------------

def test_an_owner_can_designate_a_colleague_over_the_api(notif_api, monkeypatch):
    install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    acct, _owner = active_member(notif_api, email="owner@dxpe.com")
    rep = notif_api._sa.add_member(acct["id"], "rep@dxpe.com",
                                   role=notif_api._sa.ROLE_MEMBER,
                                   status=notif_api._sa.MEMBER_ACTIVE)
    login(notif_api, monkeypatch, email="owner@dxpe.com")

    r = notif_api.post(f"/api/supplier/members/{rep['id']}/rfq-contact",
                       json={"receives": True}, headers={"Origin": APP_ORIGIN})
    assert r.status_code == 200, r.text
    assert r.json()["member"]["receives_rfq"] is True
    assert notif_api._sa.member_receives_rfq(
        notif_api._sa.get_member(rep["id"])) is True


def test_a_MEMBER_cannot_set_another_members_flag(notif_api, monkeypatch):
    """403 FROM THE SERVER, with a real session for a real MEMBER — hiding the
    control on the screen is a courtesy, this is the control."""
    install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    acct, _owner = active_member(notif_api, email="owner@dxpe.com")
    rep = notif_api._sa.add_member(acct["id"], "rep@dxpe.com",
                                   role=notif_api._sa.ROLE_MEMBER,
                                   status=notif_api._sa.MEMBER_ACTIVE)
    other = notif_api._sa.add_member(acct["id"], "rep2@dxpe.com",
                                     role=notif_api._sa.ROLE_MEMBER,
                                     status=notif_api._sa.MEMBER_ACTIVE)
    login(notif_api, monkeypatch, email="rep@dxpe.com")

    r = notif_api.post(f"/api/supplier/members/{other['id']}/rfq-contact",
                       json={"receives": True}, headers={"Origin": APP_ORIGIN})
    assert r.status_code == 403
    assert notif_api._sa.get_member(other["id"])["receives_rfq"] is None
    assert rep["id"] != other["id"]


def test_a_member_from_another_account_is_a_404_not_a_403(notif_api, monkeypatch):
    """Cross-account ids are indistinguishable from unknown ones — the API
    must not confirm that a member id exists somewhere else."""
    install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    active_member(notif_api, email="owner@dxpe.com")
    outsider_acct = notif_api._sa.create_account("motion.com")
    outsider = notif_api._sa.add_member(outsider_acct["id"], "lee@motion.com",
                                        role=notif_api._sa.ROLE_ADMIN,
                                        status=notif_api._sa.MEMBER_ACTIVE)
    login(notif_api, monkeypatch, email="owner@dxpe.com")

    r = notif_api.post(f"/api/supplier/members/{outsider['id']}/rfq-contact",
                       json={"receives": True}, headers={"Origin": APP_ORIGIN})
    assert r.status_code == 404


def test_the_member_list_reports_the_effective_designation(notif_api, monkeypatch):
    install_fake_provider(monkeypatch)
    allowlist("dxpe.com")
    acct, _ = active_member(notif_api, email="owner@dxpe.com")
    notif_api._sa.add_member(acct["id"], "rep@dxpe.com",
                             role=notif_api._sa.ROLE_MEMBER,
                             status=notif_api._sa.MEMBER_ACTIVE)
    login(notif_api, monkeypatch, email="owner@dxpe.com")
    body = notif_api.get("/api/supplier/members").json()
    by_email = {m["email"]: m["receives_rfq"] for m in body["members"]}
    assert by_email == {"owner@dxpe.com": True, "rep@dxpe.com": False}


# ---------------------------------------------------------------------------
# Flag OFF — today's behaviour exactly
# ---------------------------------------------------------------------------

def test_flag_off_writes_no_designation(tmp_path, monkeypatch):
    isolate_notification_stores(tmp_path, monkeypatch, notifications_on=False)
    acct = supplier_accounts.create_account("dxpe.com")
    m = supplier_accounts.add_member(acct["id"], "owner@dxpe.com",
                                     role=supplier_accounts.ROLE_OWNER,
                                     status=supplier_accounts.MEMBER_ACTIVE)
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "")
    assert supplier_accounts.set_member_receives_rfq(m["id"], False) is None

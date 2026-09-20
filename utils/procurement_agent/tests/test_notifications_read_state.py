"""
Arc 4 T7 / D5 — portal read state (``RfqView``), written server-side.

WHY THE SERVER AND NOT A CLIENT PING
-----------------------------------
The gate's G5 finding: both inbox doors already call one read service
(``_supplier_open_requests``), and the two ROUTE handlers are the only places
that know the caller's credential — the session door knows the member, the
claim-token door knows only the company. So the view is recorded in the routes,
over the rows the shared service returned. A client-side "I rendered this" ping
would be a fact the supplier's browser asserts; this is a fact the server
observed.

WHAT THE BRIEF ASKS FOR
-----------------------
"first render writes first_viewed_at; subsequent renders update last_viewed_at
only; token-route (claim) views also record with member_id null +
supplier_domain."
"""
from __future__ import annotations

import json

import pytest

from utils import notifications, notifications_store as ns
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    active_member, isolate_notification_stores, login, notif_api, open_rfq,
)

GUSHER_SPECS = {"manufacturer": "Gusher Pumps", "part_number": "84004-28",
                "quantity": 2}


def make_run(client, specs=None) -> str:
    """A run whose specs resolve, so ``_supplier_open_requests`` returns a row
    rather than skipping it (it never fabricates a request for a vanished run)."""
    resp = client.post("/api/runs", json={})
    assert resp.status_code == 201
    rid = resp.json()["id"]
    SF = client._api_server._SessionFactory
    ORM = client._api_server.SourcingRunORM
    with SF() as session:
        run = session.get(ORM, rid)
        run.asset_specs_json = json.dumps(specs or GUSHER_SPECS)
        session.commit()
    return rid


@pytest.fixture
def session_inbox(notif_api, monkeypatch):
    """A logged-in member of dxpe.com with one open RFQ waiting."""
    acct, member = active_member(notif_api)
    login(notif_api, monkeypatch)
    run_id = make_run(notif_api)
    open_rfq(domain="dxpe.com", run_id=run_id)
    return notif_api, run_id, acct, member


@pytest.fixture
def token_inbox(notif_api, monkeypatch, tmp_path):
    """The claim-token door: no member, just a company credential."""
    from utils import claim_tokens as ct
    monkeypatch.setenv("SUPPLIER_PORTAL_V1", "1")
    monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
    monkeypatch.setattr(ct, "CLAIM_TOKENS_ENABLED", True)
    monkeypatch.setattr(ct, "_DB_PATH", str(tmp_path / "claim_tokens.sqlite"))
    run_id = make_run(notif_api)
    open_rfq(domain="dxpe.com", run_id=run_id)
    claim = ct.generate_for("dxpe.com")
    return notif_api, run_id, claim["token"]


# ---------------------------------------------------------------------------
# The session door
# ---------------------------------------------------------------------------

def test_first_render_writes_a_view_with_the_member_id(session_inbox):
    client, run_id, acct, member = session_inbox
    assert ns.viewed_run_ids("dxpe.com") == set()

    r = client.get("/api/supplier/requests")
    assert r.status_code == 200, r.text
    assert [row["run_id"] for row in r.json()["requests"]] == [run_id]

    view = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com",
                           member_id=member["id"])
    assert view is not None
    assert view["member_id"] == member["id"]
    assert view["supplier_domain"] == "dxpe.com"
    assert view["first_viewed_at"] == view["last_viewed_at"]
    assert view["view_count"] == 1


def test_a_second_render_updates_last_viewed_at_only(session_inbox):
    """``first_viewed_at`` is the fact D6's ladder is judged against — "when
    did they first see it" — so a later render must never rewrite it."""
    client, run_id, _, member = session_inbox
    client.get("/api/supplier/requests")
    first = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com",
                            member_id=member["id"])
    client.get("/api/supplier/requests")
    second = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com",
                             member_id=member["id"])
    assert second["first_viewed_at"] == first["first_viewed_at"]
    assert second["last_viewed_at"] >= first["last_viewed_at"]
    assert second["view_count"] == 2
    assert second["id"] == first["id"], "a second row was inserted, not updated"


def test_the_seen_flag_is_false_on_the_render_that_first_shows_the_request(
        session_inbox):
    """T11's indicator depends on the ordering: annotate from the view set as
    it was BEFORE this render, then record. Read-after-write would mark
    everything seen the instant it was displayed and no "new" badge would ever
    appear."""
    client, run_id, _, _ = session_inbox
    first = client.get("/api/supplier/requests").json()["requests"]
    assert [row["seen"] for row in first] == [False]
    second = client.get("/api/supplier/requests").json()["requests"]
    assert [row["seen"] for row in second] == [True]


def test_a_view_makes_the_request_seen_for_the_escalation_ladder(session_inbox):
    """The point of T7: after a portal render, D6 stops chasing."""
    client, run_id, acct, member = session_inbox
    notification = ns.create_notification(
        kind=ns.KIND_RFQ_NEW, account_id=acct["id"], member_id=member["id"],
        run_id=run_id, supplier_domain="dxpe.com", recipient=member["email"],
        is_test=True)
    assert notifications.is_seen(ns.get_notification(notification["id"])) is False
    client.get("/api/supplier/requests")
    assert notifications.is_seen(ns.get_notification(notification["id"])) is True


def test_a_colleagues_view_counts_for_the_company(session_inbox):
    """``rfq_viewed`` is deliberately company-wide: escalating to a human
    because the named addressee did not personally click, while their colleague
    already opened the request, would be the ladder crying wolf."""
    client, run_id, acct, member = session_inbox
    colleague = client._sa.add_member(acct["id"], "buyer@dxpe.com",
                                      role=client._sa.ROLE_MEMBER,
                                      status=client._sa.MEMBER_ACTIVE)
    notification = ns.create_notification(
        kind=ns.KIND_RFQ_NEW, account_id=acct["id"], member_id=colleague["id"],
        run_id=run_id, supplier_domain="dxpe.com", recipient=colleague["email"],
        is_test=True)
    client.get("/api/supplier/requests")          # the OWNER looks, not the colleague
    assert notifications.is_seen(ns.get_notification(notification["id"])) is True


def test_another_suppliers_view_does_not_mark_ours_seen(session_inbox):
    client, run_id, acct, member = session_inbox
    notification = ns.create_notification(
        kind=ns.KIND_RFQ_NEW, account_id=acct["id"], member_id=member["id"],
        run_id=run_id, supplier_domain="dxpe.com", recipient=member["email"],
        is_test=True)
    ns.record_rfq_view(run_id=run_id, supplier_domain="sealit.example",
                       is_test=True)
    assert notifications.is_seen(ns.get_notification(notification["id"])) is False


# ---------------------------------------------------------------------------
# The claim-token door (D5's member_id-null case)
# ---------------------------------------------------------------------------

def test_the_token_door_records_a_view_with_no_member_id(token_inbox):
    client, run_id, token = token_inbox
    r = client.get(f"/api/portal/{token}/open-requests")
    assert r.status_code == 200, r.text
    view = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com")
    assert view is not None
    assert view["member_id"] is None
    assert view["supplier_domain"] == "dxpe.com"
    assert view["view_count"] == 1


def test_a_token_view_and_a_member_view_are_separate_rows_both_counting_as_seen(
        token_inbox, monkeypatch):
    """The uniqueness key collapses a NULL member to a stable key so repeated
    token polls update one row — but a member's own view is still its own row,
    and either one means the company has seen the request."""
    client, run_id, token = token_inbox
    client.get(f"/api/portal/{token}/open-requests")
    client.get(f"/api/portal/{token}/open-requests")
    acct, member = active_member(client)
    login(client, monkeypatch)
    client.get("/api/supplier/requests")

    token_view = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com")
    member_view = ns.get_rfq_view(run_id=run_id, supplier_domain="dxpe.com",
                                  member_id=member["id"])
    assert token_view["view_count"] == 2, "token polls inserted duplicate rows"
    assert member_view["view_count"] == 1
    assert token_view["id"] != member_view["id"]
    assert ns.rfq_viewed(run_id, supplier_domain="dxpe.com") is True


# ---------------------------------------------------------------------------
# Flag-off inertness and fail-soft
# ---------------------------------------------------------------------------

def test_flag_off_writes_nothing_and_adds_no_key(session_inbox, monkeypatch):
    """Prime directive 2: with NOTIFICATIONS_V1 off the response is exactly
    today's shape and the store is never touched."""
    client, run_id, _, _ = session_inbox
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    rows = client.get("/api/supplier/requests").json()["requests"]
    assert rows and all("seen" not in row for row in rows)
    assert ns.viewed_run_ids("dxpe.com") == set()


def test_a_broken_view_store_never_breaks_the_inbox(session_inbox, monkeypatch):
    """A failure to record a view is a tracking gap; a failed inbox read is the
    supplier unable to see their work. The asymmetry is deliberate."""
    client, run_id, _, _ = session_inbox

    def boom(**kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(ns, "record_rfq_view", boom)
    r = client.get("/api/supplier/requests")
    assert r.status_code == 200
    assert [row["run_id"] for row in r.json()["requests"]] == [run_id]


def test_a_run_with_no_open_rfq_records_no_view(session_inbox):
    """Views are recorded over the rows actually rendered — never over every
    run the supplier has ever been sent."""
    client, run_id, _, _ = session_inbox
    resolved = make_run(client)
    mid = open_rfq(domain="dxpe.com", run_id=resolved, part_key="acme|p-2")
    from utils import supplier_registry
    supplier_registry.update_sent_message_status(mid, "replied")
    client.get("/api/supplier/requests")
    assert ns.viewed_run_ids("dxpe.com") == {run_id}

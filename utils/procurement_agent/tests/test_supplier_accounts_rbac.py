"""
Arc 2 T11 — the D7 permission matrix + member management.

  - THE MATRIX AS DATA: a table test over EVERY role × capability pair — the
    matrix in utils/supplier_accounts_rbac.py IS the specification, and this
    test pins it cell by cell (a fourth role would fail here until added).
  - Route enforcement is matrix-driven: a MEMBER gets 403 on every
    member-management route (and 200 on the views every role holds);
  - The D7 invariants through the routes: an ADMIN cannot promote to OWNER
    nor demote the OWNER; the sole OWNER cannot revoke themselves; a second
    OWNER cannot be created via invite OR role change; a member of account A
    cannot act on account B's members (404, no existence reveal);
  - Every action is audited;
  - NO ROUTE NAMES A ROLE: a source-scan test asserts the api_server route
    handlers for this arc's surface contain no role string literals or role
    comparisons (D7's reviewer rule, enforced as a test).

Sessions are driven through the REAL API (request-link → verify → bearer).
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from utils import supplier_accounts as sa
from utils import supplier_accounts_rbac as rbac


@pytest.fixture
def rbac_api(tmp_path, monkeypatch):
    """TestClient with SUPPLIER_ACCOUNTS_V1 ON; one account (dxpe.com) with
    an OWNER, an ADMIN, and a MEMBER; a second account (grainger.com) with
    its own OWNER — for the cross-account wall."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import send_governance, email_sender
    monkeypatch.setattr(sa, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sa, "_DB_PATH", str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setattr(send_governance, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(send_governance, "_DB_PATH",
                        str(tmp_path / "send_governance.sqlite"))

    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})

    acct = sa.create_account("dxpe.com")
    sa.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                  status=sa.MEMBER_ACTIVE)
    sa.add_member(acct["id"], "admin@dxpe.com", role=sa.ROLE_ADMIN,
                  status=sa.MEMBER_ACTIVE)
    sa.add_member(acct["id"], "staff@dxpe.com", role=sa.ROLE_MEMBER,
                  status=sa.MEMBER_ACTIVE)
    acct_b = sa.create_account("grainger.com")
    sa.add_member(acct_b["id"], "owner@grainger.com", role=sa.ROLE_OWNER,
                  status=sa.MEMBER_ACTIVE)

    client = TestClient(api_server.app)
    client._api_server = api_server
    client._sa = sa
    client._email_sender = email_sender

    def _login(email):
        recorded = []

        class RecordingSender(email_sender.GmailSender):
            def send(self, message):
                recorded.append(message)
                return super().send(message)

        monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
        r = client.post("/api/supplier/auth/request-link", json={"email": email})
        assert r.status_code == 200, r.text
        assert len(recorded) == 1, f"no link emailed for {email}"
        token = (recorded[0].body.split("/supplier/verify?token=", 1)[1]
                 .split("\n", 1)[0].strip())
        r = client.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    client._login = _login
    return client


def _h(bearer: str) -> dict:
    return {"Authorization": f"Bearer {bearer}"}


def _member_id(client, email, account="dxpe.com"):
    acct = client._sa.get_account_by_domain(account)
    return client._sa.get_member_by_email(acct["id"], email)["id"]


# ---------------------------------------------------------------------------
# THE MATRIX — asserted as DATA over every role × capability pair (D7)
# ---------------------------------------------------------------------------

class TestMatrixIsTheSpecification:
    # The brief's D7 table, spelled out cell by cell. The module's matrix is
    # the specification; this literal table is its witness.
    EXPECTED: dict[str, set[str]] = {
        sa.ROLE_OWNER: {
            rbac.VIEW_REQUESTS, rbac.SUBMIT_QUOTES, rbac.PROPOSE_REVISIONS,
            rbac.VIEW_MEMBERS, rbac.MANAGE_MEMBERS, rbac.CHANGE_ROLES,
            rbac.TRANSFER_OWNERSHIP,
        },
        sa.ROLE_ADMIN: {
            rbac.VIEW_REQUESTS, rbac.SUBMIT_QUOTES, rbac.PROPOSE_REVISIONS,
            rbac.VIEW_MEMBERS, rbac.MANAGE_MEMBERS, rbac.CHANGE_ROLES,
        },
        sa.ROLE_MEMBER: {
            rbac.VIEW_REQUESTS, rbac.SUBMIT_QUOTES, rbac.PROPOSE_REVISIONS,
            rbac.VIEW_MEMBERS,
        },
    }

    def test_every_role_capability_pair(self):
        for role in sa.ROLES:
            for capability in rbac.CAPABILITIES:
                expected = capability in self.EXPECTED[role]
                member = {"role": role, "status": sa.MEMBER_ACTIVE}
                assert rbac.has_permission(member, capability) is expected, \
                    f"{role} x {capability}: matrix says {expected}"

    def test_unknown_role_holds_nothing(self):
        for capability in rbac.CAPABILITIES:
            assert rbac.has_permission({"role": "VIEWER",
                                        "status": sa.MEMBER_ACTIVE},
                                       capability) is False

    def test_non_active_member_holds_nothing(self):
        # Belt+braces: sessions already require ACTIVE.
        owner = {"role": sa.ROLE_OWNER, "status": sa.MEMBER_PENDING}
        assert rbac.has_permission(owner, rbac.TRANSFER_OWNERSHIP) is False

    def test_every_declared_role_has_a_matrix_row(self):
        # A role added to the store vocabulary without a matrix row fails
        # here — the matrix cannot silently lag the vocabulary.
        for role in sa.ROLES:
            assert role in rbac.CAPABILITY_MATRIX


# ---------------------------------------------------------------------------
# Route enforcement — matrix-driven, MEMBER denied management
# ---------------------------------------------------------------------------

class TestRouteEnforcement:
    def test_member_gets_403_on_every_management_route(self, rbac_api):
        member = rbac_api._login("staff@dxpe.com")
        mid = _member_id(rbac_api, "admin@dxpe.com")
        assert rbac_api.post("/api/supplier/members/invite",
                             headers=_h(member),
                             json={"email": "x@dxpe.com"}).status_code == 403
        assert rbac_api.post(f"/api/supplier/members/{mid}/role",
                             headers=_h(member),
                             json={"role": "MEMBER"}).status_code == 403
        assert rbac_api.post(f"/api/supplier/members/{mid}/revoke",
                             headers=_h(member)).status_code == 403

    def test_every_role_can_view_members_and_requests(self, rbac_api):
        for email in ("owner@dxpe.com", "admin@dxpe.com", "staff@dxpe.com"):
            bearer = rbac_api._login(email)
            r = rbac_api.get("/api/supplier/members", headers=_h(bearer))
            assert r.status_code == 200
            assert {m["email"] for m in r.json()["members"]} == {
                "owner@dxpe.com", "admin@dxpe.com", "staff@dxpe.com"}
            assert rbac_api.get("/api/supplier/requests",
                                headers=_h(bearer)).status_code == 200

    def test_owner_and_admin_can_invite(self, rbac_api):
        for inviter, invitee in (("owner@dxpe.com", "newhire1@dxpe.com"),
                                 ("admin@dxpe.com", "newhire2@dxpe.com")):
            bearer = rbac_api._login(inviter)
            r = rbac_api.post("/api/supplier/members/invite",
                              headers=_h(bearer),
                              json={"email": invitee})
            assert r.status_code == 200, r.text
            assert r.json()["member"]["status"] == "ACTIVE"  # D2 domain match
            assert r.json()["member"]["role"] == "MEMBER"    # least privilege


# ---------------------------------------------------------------------------
# The D7 invariants through the routes
# ---------------------------------------------------------------------------

class TestD7Invariants:
    def test_admin_cannot_promote_to_owner(self, rbac_api):
        admin = rbac_api._login("admin@dxpe.com")
        staff_id = _member_id(rbac_api, "staff@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{staff_id}/role",
                          headers=_h(admin), json={"role": "OWNER"})
        assert r.status_code == 403
        assert client_member_role(rbac_api, "staff@dxpe.com") == "MEMBER"

    def test_owner_also_cannot_promote_to_owner_no_transfer_route(self, rbac_api):
        # v1 has no ownership-transfer route: promoting to OWNER is refused
        # for EVERYONE (a second live OWNER is impossible regardless).
        owner = rbac_api._login("owner@dxpe.com")
        staff_id = _member_id(rbac_api, "staff@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{staff_id}/role",
                          headers=_h(owner), json={"role": "OWNER"})
        assert r.status_code == 403

    def test_admin_cannot_demote_the_owner(self, rbac_api):
        admin = rbac_api._login("admin@dxpe.com")
        owner_id = _member_id(rbac_api, "owner@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{owner_id}/role",
                          headers=_h(admin), json={"role": "MEMBER"})
        assert r.status_code == 403
        assert client_member_role(rbac_api, "owner@dxpe.com") == "OWNER"

    def test_sole_owner_cannot_revoke_themselves(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        owner_id = _member_id(rbac_api, "owner@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{owner_id}/revoke",
                          headers=_h(owner))
        assert r.status_code == 403
        assert client_member_role(rbac_api, "owner@dxpe.com") == "OWNER"

    def test_admin_cannot_revoke_the_owner(self, rbac_api):
        admin = rbac_api._login("admin@dxpe.com")
        owner_id = _member_id(rbac_api, "owner@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{owner_id}/revoke",
                          headers=_h(admin))
        assert r.status_code == 403

    def test_second_owner_cannot_be_created_via_invite(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        r = rbac_api.post("/api/supplier/members/invite",
                          headers=_h(owner),
                          json={"email": "second@dxpe.com", "role": "OWNER"})
        assert r.status_code == 403
        acct = rbac_api._sa.get_account_by_domain("dxpe.com")
        assert rbac_api._sa.has_live_owner(acct["id"]) is True  # still exactly one

    def test_admin_can_promote_member_to_admin(self, rbac_api):
        admin = rbac_api._login("admin@dxpe.com")
        staff_id = _member_id(rbac_api, "staff@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{staff_id}/role",
                          headers=_h(admin), json={"role": "ADMIN"})
        assert r.status_code == 200
        assert r.json()["member"]["role"] == "ADMIN"

    def test_admin_can_revoke_a_member(self, rbac_api):
        admin = rbac_api._login("admin@dxpe.com")
        staff_id = _member_id(rbac_api, "staff@dxpe.com")
        r = rbac_api.post(f"/api/supplier/members/{staff_id}/revoke",
                          headers=_h(admin))
        assert r.status_code == 200
        assert r.json()["member"]["status"] == "REVOKED"

    def test_non_matching_invite_lands_pending(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        r = rbac_api.post("/api/supplier/members/invite",
                          headers=_h(owner),
                          json={"email": "contractor@gmail.com"})
        assert r.status_code == 200
        assert r.json()["member"]["status"] == "PENDING"  # D2 non-match
        # The concierge queue (T10) sees it.
        from utils import supplier_accounts as sa_mod
        assert any(m["email"] == "contractor@gmail.com"
                   for m in sa_mod.list_pending_members())

    def test_duplicate_invite_conflicts(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        rbac_api.post("/api/supplier/members/invite", headers=_h(owner),
                      json={"email": "dup@dxpe.com"})
        r = rbac_api.post("/api/supplier/members/invite", headers=_h(owner),
                          json={"email": "dup@dxpe.com"})
        assert r.status_code == 409

    def test_invalid_role_422(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        r = rbac_api.post("/api/supplier/members/invite",
                          headers=_h(owner),
                          json={"email": "x@dxpe.com", "role": "SUPERUSER"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Cross-account wall
# ---------------------------------------------------------------------------

class TestCrossAccount:
    def test_member_of_a_cannot_act_on_b(self, rbac_api):
        owner_a = rbac_api._login("owner@dxpe.com")
        owner_b_id = _member_id(rbac_api, "owner@grainger.com",
                                account="grainger.com")
        # A's owner acting on B's member id: 404, indistinguishable from
        # unknown (no cross-account existence reveal).
        for path, kw in (
                (f"/api/supplier/members/{owner_b_id}/role",
                 {"json": {"role": "MEMBER"}}),
                (f"/api/supplier/members/{owner_b_id}/revoke", {}),
        ):
            r = rbac_api.post(path, headers=_h(owner_a), **kw)
            assert r.status_code == 404
            assert r.json()["detail"] == "Member not found"
        assert client_member_role(rbac_api, "owner@grainger.com",
                                  account="grainger.com") == "OWNER"

    def test_member_list_is_own_account_only(self, rbac_api):
        bearer = rbac_api._login("owner@grainger.com")
        r = rbac_api.get("/api/supplier/members", headers=_h(bearer))
        emails = {m["email"] for m in r.json()["members"]}
        assert emails == {"owner@grainger.com"}  # no dxpe members leak


# ---------------------------------------------------------------------------
# Audit (guardrail 7)
# ---------------------------------------------------------------------------

class TestAudited:
    def test_every_management_action_is_audited(self, rbac_api):
        owner = rbac_api._login("owner@dxpe.com")
        staff_id = _member_id(rbac_api, "staff@dxpe.com")
        rbac_api.post("/api/supplier/members/invite", headers=_h(owner),
                      json={"email": "audited@dxpe.com"})
        rbac_api.post(f"/api/supplier/members/{staff_id}/role",
                      headers=_h(owner), json={"role": "ADMIN"})
        rbac_api.post(f"/api/supplier/members/{staff_id}/revoke",
                      headers=_h(owner))
        events = {row["event"] for row in rbac_api._sa.list_audit()}
        assert {"member_invited", "role_changed", "member_revoked"} <= events
        # The actor is recorded (who).
        rows = [r for r in rbac_api._sa.list_audit(event="member_invited")]
        assert rows[0]["actor"] == "owner@dxpe.com"


# ---------------------------------------------------------------------------
# D7's reviewer rule, enforced as a test: no route handler names a role
# ---------------------------------------------------------------------------

class TestNoInlineRoleLogicInRoutes:
    def test_arc2_route_handlers_contain_no_role_literals(self):
        import api_server
        handlers = [
            api_server.supplier_request_link, api_server.supplier_verify_link,
            api_server.supplier_me, api_server.supplier_logout,
            api_server.supplier_requests, api_server.supplier_quote_submit,
            api_server.supplier_members, api_server.supplier_members_invite,
            api_server.supplier_member_role, api_server.supplier_member_revoke,
            api_server.portal_request_account,
            api_server.admin_pending_members, api_server.admin_approve_member,
            api_server.admin_reject_member,
        ]
        role_literals = ("OWNER", "ADMIN", "MEMBER")  # as quoted strings only
        for handler in handlers:
            src = inspect.getsource(handler)
            for role in role_literals:
                # A quoted role literal in a route body is the D7 smell
                # (comparisons OR writes — policy belongs in the rbac module).
                assert f'"{role}"' not in src and f"'{role}'" not in src, \
                    f"{handler.__name__} contains inline role literal {role!r}"
            assert not re.search(r"\.role\s*==", src), \
                f"{handler.__name__} compares roles inline"


def client_member_role(client, email, account="dxpe.com"):
    acct = client._sa.get_account_by_domain(account)
    m = client._sa.get_member_by_email(acct["id"], email)
    return m["role"]

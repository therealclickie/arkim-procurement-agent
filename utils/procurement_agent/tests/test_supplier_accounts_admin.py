"""
Arc 2 T10 — the admin pending-membership queue
(GET /api/admin/supplier-members/pending, POST .../{id}/approve|reject).

Under the EXISTING admin auth (I3: the ARKIM_ADMIN_TOKEN bearer gate):
  - list/approve/reject work with a valid admin token,
  - the full require_admin contract holds when the flag is on
    (401 no header / 403 wrong token / 503 secret unset — fail-closed),
  - an approved member lands ACTIVE with role MEMBER (D7 least privilege —
    never OWNER/ADMIN via concierge approval),
  - a rejected member lands REVOKED and can never verify a link,
  - both decisions write SupplierAuthAudit rows (who/when),
  - 404 unknown member; 409 when not pending,
  - approved members can complete the full login flow (the queue is the
    D2 on-ramp).
The flag-off wall (routes absent for ANY caller incl. a valid admin token —
flag check precedes auth) is pinned in test_supplier_accounts_flag.py and
re-asserted here for the auth ordering specifically.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_ADMIN_TOKEN = "test-admin-secret-pending"


@pytest.fixture
def admin_api(tmp_path, monkeypatch):
    """TestClient with SUPPLIER_ACCOUNTS_V1 ON, one account with an ACTIVE
    owner and two PENDING members awaiting concierge decisions."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import supplier_accounts, send_governance, email_sender
    monkeypatch.setattr(supplier_accounts, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_accounts, "_DB_PATH",
                        str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setattr(send_governance, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(send_governance, "_DB_PATH",
                        str(tmp_path / "send_governance.sqlite"))

    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", _ADMIN_TOKEN)
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})

    acct = supplier_accounts.create_account("dxpe.com")
    supplier_accounts.add_member(acct["id"], "owner@dxpe.com",
                                 role=supplier_accounts.ROLE_OWNER,
                                 status=supplier_accounts.MEMBER_ACTIVE)
    supplier_accounts.add_member(acct["id"], "bob.smith@gmail.com",
                                 role=supplier_accounts.ROLE_MEMBER,
                                 status=supplier_accounts.MEMBER_PENDING)
    supplier_accounts.add_member(acct["id"], "carol@partner.io",
                                 role=supplier_accounts.ROLE_MEMBER,
                                 status=supplier_accounts.MEMBER_PENDING)

    client = TestClient(api_server.app)
    client._api_server = api_server
    client._sa = supplier_accounts
    client._email_sender = email_sender
    return client


def _auth(token: str = _ADMIN_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pending(client) -> list[dict]:
    r = client.get("/api/admin/supplier-members/pending", headers=_auth())
    assert r.status_code == 200, r.text
    return r.json()["members"]


class TestPendingQueue:
    def test_list_shows_pending_with_company_domain(self, admin_api):
        rows = _pending(admin_api)
        assert {r["email"] for r in rows} == {"bob.smith@gmail.com",
                                             "carol@partner.io"}
        for r in rows:
            assert r["status"] == "PENDING"
            assert r["supplier_domain"] == "dxpe.com"   # the company context
            assert r["registrable_domain"]  # why it needed concierge review

    def test_approve_makes_active_member_least_privilege(self, admin_api):
        (bob,) = [r for r in _pending(admin_api) if "bob" in r["email"]]
        r = admin_api.post(f"/api/admin/supplier-members/{bob['id']}/approve",
                           headers=_auth())
        assert r.status_code == 200
        out = r.json()["member"]
        assert out["status"] == "ACTIVE"
        assert out["role"] == "MEMBER"      # D7: never OWNER/ADMIN via approval
        # Gone from the queue.
        assert all(m["id"] != bob["id"] for m in _pending(admin_api))
        # Audited (guardrail 7).
        rows = admin_api._sa.list_audit(event="member_approved")
        assert any(row["member_id"] == bob["id"] and row["actor"] == "admin"
                   for row in rows)

    def test_approved_member_can_now_log_in(self, admin_api, monkeypatch):
        (bob,) = [r for r in _pending(admin_api) if "bob" in r["email"]]
        admin_api.post(f"/api/admin/supplier-members/{bob['id']}/approve",
                       headers=_auth())
        # The D2 on-ramp completes: a previously-pending member requests a
        # link, verifies, and gets a session.
        recorded = []

        class RecordingSender(admin_api._email_sender.GmailSender):
            def send(self, message):
                recorded.append(message)
                return super().send(message)

        monkeypatch.setattr(admin_api._email_sender, "GmailSender",
                            RecordingSender)
        r = admin_api.post("/api/supplier/auth/request-link",
                           json={"email": bob["email"]})
        assert r.status_code == 200 and len(recorded) == 1
        token = (recorded[0].body.split("/supplier/verify?token=", 1)[1]
                 .split("\n", 1)[0].strip())
        r = admin_api.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200
        me = admin_api.get("/api/supplier/me",
                           headers={"Authorization":
                                    f"Bearer {r.json()['token']}"})
        assert me.status_code == 200
        assert me.json()["member"]["role"] == "MEMBER"

    def test_reject_revokes_and_blocks_login(self, admin_api):
        (carol,) = [r for r in _pending(admin_api) if "carol" in r["email"]]
        r = admin_api.post(f"/api/admin/supplier-members/{carol['id']}/reject",
                           headers=_auth())
        assert r.status_code == 200
        assert r.json()["member"]["status"] == "REVOKED"
        rows = admin_api._sa.list_audit(event="member_rejected")
        assert any(row["member_id"] == carol["id"] for row in rows)
        # A revoked member can never verify a link (uniform 401).
        link = admin_api._sa.mint_magic_link(carol["id"])
        r = admin_api.post("/api/supplier/auth/verify",
                           json={"token": link["token"]})
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired link"}

    def test_unknown_member_404_not_pending_409(self, admin_api):
        assert admin_api.post("/api/admin/supplier-members/nope/approve",
                              headers=_auth()).status_code == 404
        (bob,) = [r for r in _pending(admin_api) if "bob" in r["email"]]
        admin_api.post(f"/api/admin/supplier-members/{bob['id']}/approve",
                       headers=_auth())
        # Approving again: no longer pending.
        r = admin_api.post(f"/api/admin/supplier-members/{bob['id']}/approve",
                           headers=_auth())
        assert r.status_code == 409
        assert r.json()["detail"] == "Member is not pending"

    def test_admin_auth_contract_when_flag_on(self, admin_api, monkeypatch):
        assert admin_api.get("/api/admin/supplier-members/pending"
                             ).status_code == 401            # no header
        assert admin_api.get("/api/admin/supplier-members/pending",
                             headers=_auth("wrong")).status_code == 403
        monkeypatch.delenv("ARKIM_ADMIN_TOKEN", raising=False)
        assert admin_api.get("/api/admin/supplier-members/pending",
                             headers=_auth()).status_code == 503  # fail-closed

    def test_flag_off_routes_absent_even_for_admin(self, admin_api,
                                                   monkeypatch):
        # Flag check precedes require_admin: a VALID admin token gets the
        # byte-identical 404, not a 401/403 that would reveal the routes.
        monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "")
        unknown = admin_api.get("/api/definitely-not-a-route")
        for method, path, kw in (
                ("get", "/api/admin/supplier-members/pending", {}),
                ("post", "/api/admin/supplier-members/x/approve", {}),
                ("post", "/api/admin/supplier-members/x/reject", {}),
        ):
            resp = getattr(admin_api, method)(path, headers=_auth(), **kw)
            assert resp.status_code == 404
            assert resp.content == unknown.content

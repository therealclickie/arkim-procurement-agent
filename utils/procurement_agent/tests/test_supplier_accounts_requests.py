"""
Arc 2 T7 + T8 — the session-authed supplier data surface:

  T7  GET /api/supplier/requests — the session door over the SAME
      open-requests read service the claim-token route uses (one service,
      two doors; gate F2 extraction), scoped to the session's account.
      Parity with the token route is asserted on the SAME fixture data, and
      cross-account access is impossible (session for A cannot read B).

  T8  POST /api/supplier/quotes — session-authed structured-quote submission
      into the EXISTING QUOTE_SUBMIT_V1 store (supplier identity from the
      session, not a quote token); open-RFQ required (mirrors path B);
      sanity flag-not-block behaviour unchanged.

All three flags the surface needs are ON (SUPPLIER_ACCOUNTS_V1,
SUPPLIER_PORTAL_V1 for the claim-token door, QUOTE_SUBMIT_V1 for quotes);
stores isolated to tmp_path; no live network/email.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_ADMIN_TOKEN = "test-admin-secret-requests"

_GUSHER_SPECS = {"manufacturer": "Gusher Pumps", "part_number": "84004-28",
                 "quantity": 2}


@pytest.fixture
def req_api(tmp_path, monkeypatch):
    """TestClient: accounts+portal+quote flags ON, stores isolated, one
    account+ACTIVE member for dxpe.com, a live claim token, and a login
    helper wired to the recording sender."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import supplier_registry, claim_tokens, quote_store, quote_tokens
    from utils import supplier_accounts, send_governance, email_sender
    monkeypatch.setattr(supplier_registry, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_registry, "_DB_PATH",
                        str(tmp_path / "supplier_registry.sqlite"))
    monkeypatch.setattr(claim_tokens, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(claim_tokens, "_DB_PATH",
                        str(tmp_path / "claim_tokens.sqlite"))
    monkeypatch.setattr(quote_store, "_DB_PATH", str(tmp_path / "quotes.sqlite"))
    monkeypatch.setattr(quote_tokens, "_DB_PATH",
                        str(tmp_path / "quote_tokens.sqlite"))
    monkeypatch.setattr(supplier_accounts, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_accounts, "_DB_PATH",
                        str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setattr(send_governance, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(send_governance, "_DB_PATH",
                        str(tmp_path / "send_governance.sqlite"))

    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", _ADMIN_TOKEN)
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    monkeypatch.setenv("SUPPLIER_PORTAL_V1", "1")
    monkeypatch.setenv("QUOTE_SUBMIT_V1", "1")
    monkeypatch.setattr(supplier_registry, "TIER1_V2", True)
    monkeypatch.setattr(claim_tokens, "CLAIM_TOKENS_ENABLED", True)

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})
    monkeypatch.setattr(api_server, "_portal_rate_buckets", {})
    monkeypatch.setattr(api_server, "_quote_rate_buckets", {})

    supplier_registry._ensure_supplier_row("dxpe.com", name="DXP Enterprises")
    supplier_registry._ensure_supplier_row("grainger.com", name="Grainger")
    claim = claim_tokens.generate_for("dxpe.com")
    assert claim is not None

    # The account + an ACTIVE member (store-level; the routes under test
    # consume the session, not the establishment flow).
    acct = supplier_accounts.create_account("dxpe.com")
    member = supplier_accounts.add_member(
        acct["id"], "sales@dxpe.com", role=supplier_accounts.ROLE_OWNER,
        status=supplier_accounts.MEMBER_ACTIVE)
    assert member is not None

    client = TestClient(api_server.app)
    client._api_server = api_server
    client._sa = supplier_accounts
    client._claim_token = claim["token"]
    client._email_sender = email_sender

    def _login(email="sales@dxpe.com"):
        recorded = []

        class RecordingSender(email_sender.GmailSender):
            def send(self, message):
                recorded.append(message)
                return super().send(message)

        monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
        r = client.post("/api/supplier/auth/request-link", json={"email": email})
        assert r.status_code == 200, r.text
        body = recorded[0].body
        token = body.split("/supplier/verify?token=", 1)[1].split("\n", 1)[0].strip()
        r = client.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    client._login = _login
    return client


def _make_run(client, specs=None) -> str:
    resp = client.post("/api/runs", json={})
    assert resp.status_code == 201
    rid = resp.json()["id"]
    SF = client._api_server._SessionFactory
    ORM = client._api_server.SourcingRunORM
    with SF() as session:
        run = session.get(ORM, rid)
        run.asset_specs_json = json.dumps(specs or _GUSHER_SPECS)
        session.commit()
    return rid


def _rfq(client, run_id, domain="dxpe.com", status="stubbed") -> None:
    from utils import supplier_registry
    supplier_registry.record_sent_message(
        run_id=run_id, supplier_domain=domain, vendor_name=domain,
        to=[f"sales@{domain}"], status=status)


def _valid_quote_body(**overrides) -> dict:
    body = {"quote_number": "DXP-0091", "unit_price": 189.0, "quantity": 2,
            "lead_time": "3 days"}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# T7 — session-authed open requests (same service as the token route)
# ---------------------------------------------------------------------------

class TestSessionOpenRequests:
    def test_same_payload_as_the_token_route(self, req_api):
        rid = _make_run(req_api)
        _rfq(req_api, rid)
        bearer = req_api._login()
        session_resp = req_api.get("/api/supplier/requests",
                                   headers={"Authorization": f"Bearer {bearer}"})
        token_resp = req_api.get(
            f"/api/portal/{req_api._claim_token}/open-requests")
        assert session_resp.status_code == 200
        assert token_resp.status_code == 200
        assert session_resp.json() == token_resp.json()
        (row,) = session_resp.json()["requests"]
        assert row["run_id"] == rid
        assert row["manufacturer"] == "Gusher Pumps"
        assert row["part_number"] == "84004-28"
        assert row["quantity"] == 2
        assert row["quoted"] is None

    def test_cross_account_access_impossible(self, req_api):
        # An open RFQ for dxpe.com; a session for the GRAINGER account must
        # see nothing of it (the domain comes from the session, never input).
        rid = _make_run(req_api)
        _rfq(req_api, rid, domain="dxpe.com")
        from utils import supplier_accounts as sa
        acct_b = req_api._sa.create_account("grainger.com")
        sa.add_member(acct_b["id"], "sales@grainger.com",
                      role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        bearer_b = req_api._login(email="sales@grainger.com")
        r = req_api.get("/api/supplier/requests",
                        headers={"Authorization": f"Bearer {bearer_b}"})
        assert r.status_code == 200
        assert r.json()["requests"] == []  # B sees none of A's RFQs

    def test_requires_a_session(self, req_api):
        assert req_api.get("/api/supplier/requests").status_code == 401
        assert req_api.get(
            "/api/supplier/requests",
            headers={"Authorization": "Bearer garbage"}).status_code == 401

    def test_resolved_rfqs_not_open(self, req_api):
        rid = _make_run(req_api)
        _rfq(req_api, rid, status="replied")
        bearer = req_api._login()
        r = req_api.get("/api/supplier/requests",
                        headers={"Authorization": f"Bearer {bearer}"})
        assert r.json()["requests"] == []

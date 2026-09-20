"""
Arc 2 T6 — the claim-token → account bridge
(POST /api/portal/{token}/request-account).

The funnel seam arc 3 will surface as "create your account": a VALID claim
token may request a magic link for its own supplier domain. Proven here:

  - valid claim token + domain-matching email → the account is CREATED, the
    establishing member lands ACTIVE and becomes the account's OWNER (D7:
    the first member to establish an account is its OWNER), link minted and
    enqueued through send governance,
  - a second domain-matching email on an owned account lands ACTIVE MEMBER
    (never a second OWNER),
  - a NON-matching email lands PENDING (D2) with no send — uniform 200,
  - an invalid claim token gets the portal's uniform 404 (byte-identical to
    an unknown route),
  - the claim token is NOT consumed — the portal profile route still works
    afterwards (D6: the first door is unmoved),
  - response uniformity across email modes (equality, not just status).

Both flags are on (SUPPLIER_PORTAL_V1 for the claim-token machinery,
SUPPLIER_ACCOUNTS_V1 for the accounts surface); stores isolated to tmp.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_ADMIN_TOKEN = "test-admin-secret-bridge"


@pytest.fixture
def bridge_api(tmp_path, monkeypatch):
    """TestClient with the portal + accounts flags ON, stores isolated, one
    registry supplier (dxpe.com) with a live claim token."""
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
    monkeypatch.setenv("SUPPLIER_PORTAL_V1", "1")
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    monkeypatch.setattr(supplier_registry, "TIER1_V2", True)
    # claim_tokens binds its flag at import (the conftest pin may have loaded
    # it OFF already) — patch the module attr like test_supplier_portal does.
    monkeypatch.setattr(claim_tokens, "CLAIM_TOKENS_ENABLED", True)

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})
    monkeypatch.setattr(api_server, "_portal_rate_buckets", {})

    # A registry supplier for the claim token's domain (read-only lookup on
    # the admin mint route; no scope needed for this arc).
    supplier_registry._ensure_supplier_row("dxpe.com", name="DXP Enterprises")
    out = claim_tokens.generate_for("dxpe.com")
    assert out is not None, "claim token mint failed (portal flag on?)"

    client = TestClient(api_server.app)
    client._api_server = api_server
    client._sa = supplier_accounts
    client._claim_token = out["token"]
    return client


def _request_account(client, email, token=None):
    return client.post(f"/api/portal/{token or client._claim_token}"
                       "/request-account", json={"email": email})


def _count(client, table):
    with sqlite3.connect(client._sa._DB_PATH) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestBridge:
    def test_matching_email_creates_account_owner_active_and_link(self, bridge_api):
        r = _request_account(bridge_api, "owner@dxpe.com")
        assert r.status_code == 200 and r.json() == {"ok": True}
        acct = bridge_api._sa.get_account_by_domain("dxpe.com")
        assert acct is not None                       # the account was created
        m = bridge_api._sa.get_member_by_email(acct["id"], "owner@dxpe.com")
        assert m["status"] == bridge_api._sa.MEMBER_ACTIVE
        assert m["role"] == bridge_api._sa.ROLE_OWNER  # D7 first member = OWNER
        assert _count(bridge_api, "supplier_accounts") == 1   # 1:1 with domain
        assert _count(bridge_api, "supplier_magic_links") == 1  # link enqueued
        events = [row["event"] for row in bridge_api._sa.list_audit()]
        assert "account_created" in events
        assert "account_requested" in events
        assert "link_requested" in events

    def test_second_email_lands_active_member_not_owner(self, bridge_api):
        _request_account(bridge_api, "owner@dxpe.com")
        r = _request_account(bridge_api, "sales@dxpe.com")
        assert r.status_code == 200
        acct = bridge_api._sa.get_account_by_domain("dxpe.com")
        m = bridge_api._sa.get_member_by_email(acct["id"], "sales@dxpe.com")
        assert m["status"] == bridge_api._sa.MEMBER_ACTIVE
        assert m["role"] == bridge_api._sa.ROLE_MEMBER   # never a second OWNER
        assert _count(bridge_api, "supplier_members") == 2  # no duplicate rows

    def test_non_matching_email_lands_pending_no_send(self, bridge_api):
        r = _request_account(bridge_api, "bob.smith@gmail.com")
        assert r.status_code == 200 and r.json() == {"ok": True}
        acct = bridge_api._sa.get_account_by_domain("dxpe.com")
        m = bridge_api._sa.get_member_by_email(acct["id"], "bob.smith@gmail.com")
        assert m is not None
        assert m["status"] == bridge_api._sa.MEMBER_PENDING   # D2
        assert m["role"] == bridge_api._sa.ROLE_MEMBER
        assert _count(bridge_api, "supplier_magic_links") == 0  # no link sent
        # (Ownerless account — documented follow-up: a matching-domain member
        # establishing later becomes OWNER; see establish_account docstring.)

    def test_ownerless_account_gets_owner_from_later_matching_member(self,
                                                                     bridge_api):
        # The gap-closer: PENDING gmail member first, then the domain-proving
        # owner arrives → they take ownership.
        _request_account(bridge_api, "bob.smith@gmail.com")   # PENDING, no owner
        _request_account(bridge_api, "owner@dxpe.com")
        acct = bridge_api._sa.get_account_by_domain("dxpe.com")
        owner = bridge_api._sa.get_member_by_email(acct["id"], "owner@dxpe.com")
        assert owner["role"] == bridge_api._sa.ROLE_OWNER
        assert bridge_api._sa.has_live_owner(acct["id"]) is True

    def test_uniform_200_across_email_modes(self, bridge_api):
        _request_account(bridge_api, "owner@dxpe.com")  # establish + owner
        responses = [
            _request_account(bridge_api, "sales@dxpe.com"),     # ACTIVE member
            _request_account(bridge_api, "stranger@gmail.com"),  # PENDING
            _request_account(bridge_api, "nobody@nowhere.io"),   # no claim relevance
            _request_account(bridge_api, "garbage"),             # unparseable
        ]
        assert all(r.status_code == 200 for r in responses)
        assert len({r.content for r in responses}) == 1

    def test_invalid_claim_token_uniform_404(self, bridge_api):
        unknown = bridge_api.get("/api/definitely-not-a-route")
        r = _request_account(bridge_api, "owner@dxpe.com",
                             token="garbage-claim-token")
        assert r.status_code == 404
        assert r.content == unknown.content

    def test_claim_token_not_consumed(self, bridge_api):
        _request_account(bridge_api, "owner@dxpe.com")
        # The first door still works exactly as before (D6).
        r = bridge_api.get(f"/api/portal/{bridge_api._claim_token}/profile")
        assert r.status_code == 200
        assert r.json()["supplier_domain"] == "dxpe.com"

    def test_existing_member_re_request_idempotent_rows(self, bridge_api):
        _request_account(bridge_api, "owner@dxpe.com")
        _request_account(bridge_api, "owner@dxpe.com")  # re-request
        assert _count(bridge_api, "supplier_accounts") == 1
        assert _count(bridge_api, "supplier_members") == 1
        # A fresh link per request (each single-use) — 2 links now.
        assert _count(bridge_api, "supplier_magic_links") == 2

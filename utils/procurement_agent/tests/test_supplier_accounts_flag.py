"""
Arc 2 T2 — the SUPPLIER_ACCOUNTS_V1 flag: every new route is ABSENT when the
flag is off (byte-identical 404 to an unknown route), and present/behaving
when on.

This file owns the COMPLETE planned route list for the arc (all of T3–T11's
surfaces) so the inertness wall is asserted in one place and stays true as
routes land: with the flag off nothing may leak — not existence (404 body
byte-identical), not auth ordering (admin routes 404 even with a bad/good
token, the flag check precedes require_admin), not data (no store row read).

Per-task API tests assert the flag-ON behaviour of their own routes; this
file re-asserts the flag-OFF wall over the whole surface after every task.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_ADMIN_TOKEN = "test-admin-secret-accounts"

# Every route this arc adds. (method, path, kwargs-with-a-body-where-needed)
NEW_ROUTES: list[tuple[str, str, dict]] = [
    ("post", "/api/supplier/auth/request-link",
     {"json": {"email": "sales@dxpe.com"}}),
    ("post", "/api/supplier/auth/verify", {"json": {"token": "x"}}),
    ("get", "/api/supplier/me", {}),
    ("post", "/api/supplier/auth/logout", {}),
    ("post", "/api/portal/some-claim-token/request-account",
     {"json": {"email": "sales@dxpe.com"}}),
    ("get", "/api/supplier/requests", {}),
    ("post", "/api/supplier/quotes",
     {"json": {"quote_number": "Q1", "unit_price": 10.0, "quantity": 1,
               "lead_time": "2 days", "run_id": "r1"}}),
    ("get", "/api/supplier/members", {}),
    ("post", "/api/supplier/members/invite",
     {"json": {"email": "new@dxpe.com", "role": "MEMBER"}}),
    ("post", "/api/supplier/members/m1/role", {"json": {"role": "ADMIN"}}),
    ("post", "/api/supplier/members/m1/revoke", {}),
    ("get", "/api/admin/supplier-members/pending", {}),
    ("post", "/api/admin/supplier-members/p1/approve", {}),
    ("post", "/api/admin/supplier-members/p1/reject", {}),
]

# Bearer-bearing variants: with the flag off, an (invalid OR valid-looking)
# session header must still get the SAME 404 — the route is absent, not 401.
_SESSION_HEADER = {"Authorization": "Bearer some-session-token"}


@pytest.fixture
def api_off(tmp_path, monkeypatch):
    """TestClient with SUPPLIER_ACCOUNTS_V1 OFF and stores isolated."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api_off.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import supplier_registry, claim_tokens, quote_store, quote_tokens
    from utils import supplier_accounts
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

    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", _ADMIN_TOKEN)
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "")  # the flag under test: OFF

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    return TestClient(api_server.app)


class TestFlagOffInert:
    def test_every_new_route_absent_byte_identical(self, api_off):
        unknown = api_off.get("/api/definitely-not-a-route")
        for method, path, kw in NEW_ROUTES:
            resp = getattr(api_off, method)(path, **kw)
            assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
            assert resp.content == unknown.content, f"{path}: body leaks existence"

    def test_session_bearer_does_not_change_the_404(self, api_off):
        # The routes are absent — a bearer header must not surface a 401 that
        # would reveal the route exists.
        unknown = api_off.get("/api/definitely-not-a-route")
        for method, path, kw in NEW_ROUTES:
            resp = getattr(api_off, method)(path, headers=_SESSION_HEADER, **kw)
            assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
            assert resp.content == unknown.content

    def test_admin_routes_404_even_with_valid_admin_token(self, api_off):
        # Flag check precedes require_admin (the api_server.py:6415 ordering
        # convention): a VALID admin token must not reach the handler flag-off.
        auth = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}
        for method, path, kw in NEW_ROUTES:
            if "/api/admin/" not in path:
                continue
            resp = getattr(api_off, method)(path, headers=auth, **kw)
            assert resp.status_code == 404
            assert resp.json() == {"detail": "Not Found"}

    def test_no_supplier_accounts_table_read_flag_off(self, api_off, tmp_path):
        # Prime a row flag-on style, then flip off: no route may surface it.
        import os
        from utils import supplier_accounts as sa
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as mp:
            mp.setenv("SUPPLIER_ACCOUNTS_V1", "1")
            acct = sa.create_account("dxpe.com")
            m = sa.add_member(acct["id"], "sales@dxpe.com",
                              role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        monkeypatch.undo()
        # flag is off again (fixture pinned "")
        assert os.environ.get("SUPPLIER_ACCOUNTS_V1", "") == ""
        assert sa.supplier_accounts_active() is False
        # The row exists in storage but every route is absent — nothing reads it.
        resp = api_off.post("/api/supplier/auth/request-link",
                            json={"email": "sales@dxpe.com"})
        assert resp.status_code == 404
        assert m["email"] == "sales@dxpe.com"  # (row really was created)

"""
Arc 6 — shared fixtures and the buyer-facing route RULE for the buyer identity
tests.

A helper module (leading underscore: not collected) rather than conftest.py,
following the ``_arc3_session_fixtures`` convention.

The buyer cookie is ``Secure``, so a client only returns it over https: every
session test runs on ``https://testserver`` (the arc 3 lesson).
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ADMIN_TOKEN = "test-admin-secret-arc6"
HTTPS_BASE = "https://testserver"
APP_ORIGIN = "http://localhost:3000"      # the first entry of api_server._cors_origins
FOREIGN_ORIGIN = "https://evil.example.com"
BUYER_COOKIE = "gofer_buyer_session"

COMPANY_A = "company-bayfoods"
COMPANY_B = "company-northgate"
FACILITY_A = "fac-stockton"
FACILITY_B = "fac-northgate-1"


# ---------------------------------------------------------------------------
# THE buyer-facing rule (approved at the gate, K2 / T4). A route is
# buyer-facing unless its path sits under one of these prefixes. The four
# FastAPI auto-routes are not APIRoutes and are skipped by the caller.
# ---------------------------------------------------------------------------

NON_BUYER_PREFIXES: tuple[str, ...] = (
    "/api/supplier/",
    "/api/admin/",
    "/api/webhooks/",
    "/api/portal/{token}",
    "/api/quote/{token}",
    "/api/intake/",
    "/api/health",
)


def is_buyer_facing(path: str) -> bool:
    return not any(path == p or path.startswith(p) for p in NON_BUYER_PREFIXES)


def buyer_facing_routes(app) -> list[APIRoute]:
    """Every buyer-facing APIRoute registered on the RUNNING app."""
    return [r for r in app.routes if isinstance(r, APIRoute) and is_buyer_facing(r.path)]


# ---------------------------------------------------------------------------
# Isolated app
# ---------------------------------------------------------------------------

def isolate(tmp_path, monkeypatch, *, flag_on: bool = True):
    """Point every store the buyer surface touches at tmp_path. Returns
    ``(api_server, buyer_accounts)``."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import (buyer_accounts, orders, send_governance, site_settings,
                       supplier_accounts, supplier_registry)
    monkeypatch.setattr(supplier_registry, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_registry, "_DB_PATH", str(tmp_path / "supplier_registry.sqlite"))
    monkeypatch.setattr(supplier_accounts, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supplier_accounts, "_DB_PATH", str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setattr(send_governance, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(send_governance, "_DB_PATH", str(tmp_path / "send_governance.sqlite"))
    monkeypatch.setattr(orders, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(orders, "_DB_PATH", str(tmp_path / "orders.sqlite"))
    monkeypatch.setattr(site_settings, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(site_settings, "_DB_PATH", str(tmp_path / "site_settings.sqlite"))
    monkeypatch.setattr(buyer_accounts, "_DB_PATH", str(tmp_path / "buyer_accounts.sqlite"))

    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("BUYER_ACCOUNTS_V1", "1" if flag_on else "")

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_buyer_auth_rate_buckets", {})
    return api_server, buyer_accounts


def _client(api_server, buyer_accounts, base_url=HTTPS_BASE):
    client = TestClient(api_server.app, base_url=base_url)
    client._api_server = api_server
    client._ba = buyer_accounts
    from utils import email_sender
    client._email_sender = email_sender
    return client


@pytest.fixture
def buyer_api(tmp_path, monkeypatch):
    """HTTPS TestClient with BUYER_ACCOUNTS_V1 ON and every store isolated."""
    api_server, ba = isolate(tmp_path, monkeypatch, flag_on=True)
    return _client(api_server, ba)


@pytest.fixture
def buyer_api_off(tmp_path, monkeypatch):
    """HTTPS TestClient with BUYER_ACCOUNTS_V1 OFF (D8 — today's behaviour)."""
    api_server, ba = isolate(tmp_path, monkeypatch, flag_on=False)
    return _client(api_server, ba)


# ---------------------------------------------------------------------------
# Company / member / login helpers
# ---------------------------------------------------------------------------

def make_company(client, cid=COMPANY_A, name="Bay Foods", facilities=(FACILITY_A,),
                 domains=("bayfoods.com",)):
    c = client._ba.create_company(cid, name, facility_ids=list(facilities),
                                  email_domains=list(domains), created_by="admin")
    assert c is not None
    return c


def make_member(client, email, role, cid=COMPANY_A):
    m = client._ba.add_member(cid, email, role=role, invited_by="test")
    assert m is not None
    return m


def two_companies(client):
    """Company A (Bay Foods) and company B (Northgate), each with an Admin."""
    a = make_company(client)
    b = make_company(client, cid=COMPANY_B, name="Northgate Manufacturing",
                     facilities=(FACILITY_B,), domains=("northgate.com",))
    return a, b


def login(client, member, monkeypatch=None) -> TestClient:
    """Put a live session for ``member`` into ``client``'s cookie jar. Uses the
    store directly (the login ROUTE is proven in the T3 tests); returns the
    client. The jar holds only the buyer cookie."""
    sess = client._ba.create_session(member["id"])
    assert sess is not None
    client.cookies.clear()
    client.cookies.set(BUYER_COOKIE, sess["token"])
    return client


def new_client_for(client, member) -> TestClient:
    """A SEPARATE https client logged in as ``member`` (for A-vs-B tests)."""
    other = _client(client._api_server, client._ba)
    return login(other, member)


def origin_headers() -> dict:
    return {"Origin": APP_ORIGIN}

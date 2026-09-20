"""
Arc 3 — shared fixtures for the supplier BROWSER-SESSION tests (D1 cookie,
D2 CSRF, and the session surfaces built on them).

Why a helper module rather than conftest.py: the arc's prime directive is
"no existing test file modified", and conftest.py is one. This mirrors the
existing ``_dsn_fixtures`` / ``_reply_fixtures`` convention — the leading
underscore keeps pytest from collecting it as a test module while the
fixtures below are importable into any arc-3 test file's namespace.

THE ONE MECHANICAL FACT these fixtures exist to encode
------------------------------------------------------
The D1 cookie carries ``Secure``, so RFC 6265 forbids a client from ever
sending it back over plain http. ``TestClient(app)`` defaults to
``http://testserver`` — under which a cookie-auth test would fail for
entirely the wrong reason, and under which arc 2's bearer suite is
structurally immune to this arc (its jar receives the cookie and never
returns it). So every cookie test here runs on ``https://testserver``.

``Set-Cookie`` itself is readable on the response at EITHER base URL, so the
four-attribute assertion never depends on the scheme.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ADMIN_TOKEN = "test-admin-secret-arc3"
HTTPS_BASE = "https://testserver"
APP_ORIGIN = "http://localhost:3000"   # the first entry of api_server._cors_origins
FOREIGN_ORIGIN = "https://evil.example.com"


def _isolate_stores(tmp_path, monkeypatch):
    """Point every store this surface touches at tmp_path and turn the arc-2
    flag on. Mirrors arc 2's ``sa_api`` fixture exactly — same stores, same
    order — so a cookie test and a bearer test differ ONLY in how they
    authenticate."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import (claim_tokens, quote_store, quote_tokens, send_governance,
                       supplier_accounts, supplier_registry)
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

    monkeypatch.setenv("ARKIM_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})
    return api_server, supplier_accounts, engine, TestSession


def _decorate(client, api_server, supplier_accounts):
    from utils import email_sender
    client._api_server = api_server
    client._sa = supplier_accounts
    client._email_sender = email_sender
    return client


@pytest.fixture
def sess_api(tmp_path, monkeypatch):
    """HTTPS TestClient — the browser's view. The D1 cookie is Secure, so only
    an https client both receives AND returns it."""
    api_server, sa, _e, _s = _isolate_stores(tmp_path, monkeypatch)
    return _decorate(TestClient(api_server.app, base_url=HTTPS_BASE),
                     api_server, sa)


@pytest.fixture
def sess_api_http(tmp_path, monkeypatch):
    """Plain-http TestClient — arc 2's posture, kept alongside so the bearer
    path can be re-proven unchanged in the same file as the cookie path."""
    api_server, sa, _e, _s = _isolate_stores(tmp_path, monkeypatch)
    return _decorate(TestClient(api_server.app), api_server, sa)


# ---------------------------------------------------------------------------
# Account / member / login helpers (same shapes as arc 2's)
# ---------------------------------------------------------------------------

def active_member(client, domain="dxpe.com", email="sales@dxpe.com",
                  role=None):
    """An account with one ACTIVE member (OWNER unless told otherwise)."""
    acct = client._sa.create_account(domain)
    assert acct is not None
    m = client._sa.add_member(acct["id"], email,
                              role=role or client._sa.ROLE_OWNER,
                              status=client._sa.MEMBER_ACTIVE)
    assert m is not None
    return acct, m


def magic_link_token(client, monkeypatch, email="sales@dxpe.com") -> str:
    """Drive the REAL request-link route and read the raw token out of the
    delivered message body — never out of the response (there is none) and
    never out of the store (only the hash is there)."""
    recorded = []
    email_sender = client._email_sender

    class RecordingSender(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
    r = client.post("/api/supplier/auth/request-link", json={"email": email})
    assert r.status_code == 200, r.text
    assert recorded, "no magic-link email was delivered"
    body = recorded[0].body
    return body.split("/supplier/verify?token=", 1)[1].split("\n", 1)[0].strip()


def verify(client, monkeypatch, email="sales@dxpe.com"):
    """Complete a magic-link login. Returns the verify response; on an https
    client the D1 cookie is now in the jar and every later call is
    cookie-authenticated with no header at all."""
    token = magic_link_token(client, monkeypatch, email)
    r = client.post("/api/supplier/auth/verify", json={"token": token},
                    headers={"Origin": APP_ORIGIN})
    assert r.status_code == 200, r.text
    return r


def set_cookie_header(response) -> str:
    """The RAW ``Set-Cookie`` header string — the only honest place to assert
    the four D1 attributes (a jar read loses every attribute)."""
    for name, value in response.headers.raw:
        if name.decode().lower() == "set-cookie":
            text = value.decode()
            if text.startswith("gofer_supplier_session="):
                return text
    return ""

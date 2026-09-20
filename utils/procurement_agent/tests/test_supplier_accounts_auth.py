"""
Arc 2 T3 — POST /api/supplier/auth/request-link (magic-link request).

Through the REAL API (TestClient), SUPPLIER_ACCOUNTS_V1 ON:

  - UNIFORM 200 across every mode (criterion 4: equality of bodies, not just
    status): known ACTIVE member, unknown email (no account), public-mailbox
    email, PENDING member — byte-identical responses, no enumeration oracle,
  - D2 establishment: domain-matching email → member created ACTIVE + link
    minted + email enqueued; non-matching email → PENDING (store level — the
    reachable API path for a non-match is the T6 bridge); public-mailbox
    domain NEVER auto-ACTIVE,
  - D5 / criterion 5: the send goes through the REAL governance stack — a
    non-allowlisted recipient is blocked "not_allowlisted" by send_governance
    itself (the gate is exercised, not mocked; only delivery is recorded via
    a delegating subclass), and an allowlisted domain passes governance and
    stubs at the EMAIL_SEND_ENABLED delivery gate,
  - no raw token anywhere: not in the response, not in the audit detail, not
    in storage (criterion 3, API-level),
  - rate limiting: per-email bucket and per-IP bucket (guardrail 4).

Stores are isolated to tmp_path (registry, claim/quote tokens, quote store,
supplier_accounts, send_governance); the conftest autouse net pins
EMAIL_SEND_ENABLED off and external keys empty.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_ADMIN_TOKEN = "test-admin-secret-auth"


@pytest.fixture
def sa_api(tmp_path, monkeypatch):
    """TestClient with SUPPLIER_ACCOUNTS_V1 ON and every store isolated."""
    from utils.procurement_agent.state import persistence
    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)

    from utils import supplier_registry, claim_tokens, quote_store, quote_tokens
    from utils import supplier_accounts, send_governance
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

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    monkeypatch.setattr(api_server, "DEMO_MODE", False)
    monkeypatch.setattr(api_server, "_supplier_auth_rate_buckets", {})

    from utils import email_sender
    client = TestClient(api_server.app)
    client._api_server = api_server
    client._email_sender = email_sender
    client._sa = supplier_accounts
    client._gov = send_governance
    return client


def _account(client, domain="dxpe.com"):
    acct = client._sa.create_account(domain)
    assert acct is not None
    return acct


def _active_member(client, domain="dxpe.com", email="sales@dxpe.com"):
    acct = _account(client, domain)
    m = client._sa.add_member(acct["id"], email, role=client._sa.ROLE_OWNER,
                              status=client._sa.MEMBER_ACTIVE)
    assert m is not None
    return acct, m


# ---------------------------------------------------------------------------
# Uniformity — no enumeration oracle (criterion 4: body EQUALITY)
# ---------------------------------------------------------------------------

class TestUniformResponse:
    def test_all_modes_return_identical_bodies(self, sa_api):
        acct, m = _active_member(sa_api)  # a known ACTIVE member
        # PENDING member on the same account (concierge has not approved).
        p = sa_api._sa.add_member(acct["id"], "newhire@dxpe.com",
                                  role=sa_api._sa.ROLE_MEMBER,
                                  status=sa_api._sa.MEMBER_PENDING)
        assert p is not None
        responses = [
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "sales@dxpe.com"}),        # known ACTIVE
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "nobody@unknowndomain.io"}),  # no account
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "someone@gmail.com"}),    # public mailbox
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "newhire@dxpe.com"}),     # PENDING
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "garbage-not-an-email"}),  # unparseable
        ]
        assert all(r.status_code == 200 for r in responses)
        bodies = {r.content for r in responses}
        assert len(bodies) == 1, f"responses differ across modes: {bodies}"
        assert responses[0].json() == {"ok": True}

    def test_no_token_in_response_or_audit(self, sa_api):
        _active_member(sa_api)
        r = sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "sales@dxpe.com"})
        assert r.status_code == 200
        assert "token" not in r.json()
        for row in sa_api._sa.list_audit():
            detail = row["detail_json"] or ""
            assert "token" not in detail.lower()


# ---------------------------------------------------------------------------
# D2 establishment through the route
# ---------------------------------------------------------------------------

class TestEstablishment:
    def test_domain_match_creates_active_member_and_mints_link(self, sa_api):
        _account(sa_api, "dxpe.com")
        r = sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "Sales@DXPE.com"})
        assert r.status_code == 200
        m = sa_api._sa.get_member_by_email(
            sa_api._sa.get_account_by_domain("dxpe.com")["id"], "sales@dxpe.com")
        assert m is not None
        assert m["status"] == sa_api._sa.MEMBER_ACTIVE      # domain match (D2)
        assert m["role"] == sa_api._sa.ROLE_MEMBER          # least privilege (D7)
        # A magic link WAS minted (hashed at rest) for the member.
        with sqlite3.connect(sa_api._sa._DB_PATH) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM supplier_magic_links WHERE member_id = ?",
                (m["id"],)).fetchone()[0]
        assert n == 1
        # Audited (guardrail 7): member_created + link_requested.
        events = [row["event"] for row in sa_api._sa.list_audit()]
        assert "member_created" in events and "link_requested" in events

    def test_unknown_email_creates_no_member(self, sa_api):
        _account(sa_api, "dxpe.com")
        sa_api.post("/api/supplier/auth/request-link",
                    json={"email": "stranger@unknowndomain.io"})
        with sqlite3.connect(sa_api._sa._DB_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM supplier_members").fetchone()[0]
        assert n == 0  # nothing created for an unknown-domain email

    def test_pending_member_gets_no_link(self, sa_api):
        acct = _account(sa_api)
        sa_api._sa.add_member(acct["id"], "newhire@dxpe.com",
                              role=sa_api._sa.ROLE_MEMBER,
                              status=sa_api._sa.MEMBER_PENDING)
        sa_api.post("/api/supplier/auth/request-link",
                    json={"email": "newhire@dxpe.com"})
        with sqlite3.connect(sa_api._sa._DB_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM supplier_magic_links").fetchone()[0]
        assert n == 0  # a PENDING member is never sent a working link

    def test_existing_active_member_can_re_request(self, sa_api):
        _active_member(sa_api)
        for _ in range(2):  # within the default email cap (3)
            r = sa_api.post("/api/supplier/auth/request-link",
                            json={"email": "sales@dxpe.com"})
            assert r.status_code == 200
        with sqlite3.connect(sa_api._sa._DB_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM supplier_magic_links").fetchone()[0]
        assert n == 2  # one mint per request; each independently single-use


# ---------------------------------------------------------------------------
# D2 store-level rule (the seam T6/T11 share)
# ---------------------------------------------------------------------------

class TestD2RuleStoreLevel:
    def test_non_matching_email_lands_pending(self, sa_api):
        # The claim-token bridge / invite paths locate the account by the
        # TOKEN or inviter, so a non-matching email IS reachable there. The
        # rule lives in ensure_member_for_link (the shared establishment seam)
        # and is pinned here.
        acct = _account(sa_api, "dxpe.com")
        m, created = sa_api._sa.ensure_member_for_link(acct["id"], "bob@other.com")
        assert created is True
        assert m["status"] == sa_api._sa.MEMBER_PENDING    # non-match → pending
        # And the matching case for the same seam:
        m2, created2 = sa_api._sa.ensure_member_for_link(acct["id"], "sales@dxpe.com")
        assert created2 is True
        assert m2["status"] == sa_api._sa.MEMBER_ACTIVE

    def test_public_mailbox_never_auto_active_even_with_account(self, sa_api):
        # Adversarial: an account exists for a public-mailbox domain. The
        # email still can never AUTO-match (D2) — it lands PENDING.
        acct = _account(sa_api, "gmail.com")
        m, created = sa_api._sa.ensure_member_for_link(acct["id"], "x@gmail.com")
        assert created is True
        assert m["status"] == sa_api._sa.MEMBER_PENDING


# ---------------------------------------------------------------------------
# D5 — the send goes through the REAL governance stack (criterion 5)
# ---------------------------------------------------------------------------

class TestSendGovernance:
    def _record_sender(self, sa_api, monkeypatch):
        """Wrap GmailSender with a recording subclass that DELEGATES to the
        real send() — the governance stack inside runs for real; only the
        handed-off message is recorded. Mocking the gate would defeat D5."""
        recorded = []

        class RecordingSender(sa_api._email_sender.GmailSender):
            def send(self, message):
                recorded.append(message)
                return super().send(message)

        monkeypatch.setattr(sa_api._email_sender, "GmailSender", RecordingSender)
        return recorded

    def test_non_allowlisted_recipient_suppressed_by_real_gate(self, sa_api,
                                                               monkeypatch):
        recorded = self._record_sender(sa_api, monkeypatch)
        monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")   # governance ACTIVE
        # allowlist is empty (isolated store) ⇒ fail-closed: every send blocked.
        assert sa_api._gov.allowlist_list() == []
        _active_member(sa_api)
        r = sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "sales@dxpe.com"})
        assert r.status_code == 200
        # The message WAS handed to the send seam, addressed to the member...
        assert len(recorded) == 1
        assert recorded[0].to == ["sales@dxpe.com"]
        assert recorded[0].metadata["supplier_domain"] == "dxpe.com"
        # ...and the REAL governance stack blocked it (not delivered, not
        # stubbed-as-ok): the verdict is recorded in the audit trail.
        rows = [row for row in sa_api._sa.list_audit(event="link_requested")
                if row["email"] == "sales@dxpe.com"]
        assert any('"send_status": "not_allowlisted"' in (row["detail_json"] or "")
                   for row in rows)

    def test_allowlisted_domain_passes_governance_stubs_at_delivery_gate(
            self, sa_api, monkeypatch):
        recorded = self._record_sender(sa_api, monkeypatch)
        monkeypatch.setenv("SEND_GOVERNANCE_V1", "1")
        sa_api._gov.allowlist_add("dxpe.com", added_by="test", is_test=True)
        _active_member(sa_api)
        r = sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "sales@dxpe.com"})
        assert r.status_code == 200
        assert len(recorded) == 1
        # Passed the real allowlist; the EMAIL_SEND_ENABLED delivery gate
        # (pinned OFF by the conftest safety net) stubs it — dev/test never
        # hits a real mailbox.
        rows = [row for row in sa_api._sa.list_audit(event="link_requested")]
        assert any('"send_status": "stubbed"' in (row["detail_json"] or "")
                   for row in rows)

    def test_governance_off_send_stubs_without_consulting_gate(self, sa_api,
                                                               monkeypatch):
        # SEND_GOVERNANCE_V1 off (the conftest pin): byte-identical legacy
        # behaviour — the sender stubs at the delivery gate without a verdict.
        recorded = self._record_sender(sa_api, monkeypatch)
        _active_member(sa_api)
        sa_api.post("/api/supplier/auth/request-link",
                    json={"email": "sales@dxpe.com"})
        assert len(recorded) == 1  # still handed to the seam, still stubbed


# ---------------------------------------------------------------------------
# T4 — verify → session
# ---------------------------------------------------------------------------

def _capture_link_token(sa_api, monkeypatch, email="sales@dxpe.com"):
    """Request a link for an ACTIVE member and recover the RAW token the only
    place it exists outside the store's hash: the emailed link URL (recorded
    via a delegating sender wrapper — the governance stack still runs)."""
    recorded = []

    class RecordingSender(sa_api._email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(sa_api._email_sender, "GmailSender", RecordingSender)
    r = sa_api.post("/api/supplier/auth/request-link", json={"email": email})
    assert r.status_code == 200, r.text
    assert len(recorded) == 1
    body = recorded[0].body
    marker = "/supplier/verify?token="
    assert marker in body
    return body.split(marker, 1)[1].split("\n", 1)[0].strip()


class TestVerifyLink:
    def test_success_returns_session_token_once(self, sa_api, monkeypatch):
        _active_member(sa_api)
        token = _capture_link_token(sa_api, monkeypatch)
        r = sa_api.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200
        out = r.json()
        assert out["token"] and len(out["token"]) >= 32
        assert out["expires_at"]
        # The raw session token is not persisted (hash only) — criterion 3.
        import sqlite3
        with sqlite3.connect(sa_api._sa._DB_PATH) as conn:
            dump = "\n".join(str(r_) for r_ in conn.execute(
                "SELECT * FROM supplier_sessions").fetchall())
        assert out["token"] not in dump
        assert sa_api._sa._hash_token(out["token"]) in dump
        # Audited: link_verified ok + login.
        events = [row["event"] for row in sa_api._sa.list_audit()]
        assert "link_verified" in events and "login" in events

    def test_every_rejection_mode_is_identical(self, sa_api, monkeypatch):
        # (criterion 4: equality across ALL modes, not separate checks)
        acct, owner = _active_member(sa_api)
        # A PENDING member's link (concierge has not approved).
        pending = sa_api._sa.add_member(
            acct["id"], "newhire@dxpe.com", role=sa_api._sa.ROLE_MEMBER,
            status=sa_api._sa.MEMBER_PENDING)
        pending_link = sa_api._sa.mint_magic_link(pending["id"])
        # An expired link for the ACTIVE member.
        expired = sa_api._sa.mint_magic_link(owner["id"], expiry_minutes=0)
        # A used link.
        used = _capture_link_token(sa_api, monkeypatch)
        assert sa_api.post("/api/supplier/auth/verify",
                           json={"token": used}).status_code == 200
        rejections = [
            sa_api.post("/api/supplier/auth/verify",
                        json={"token": "totally-unknown-token"}),
            sa_api.post("/api/supplier/auth/verify",
                        json={"token": expired["token"]}),
            sa_api.post("/api/supplier/auth/verify",
                        json={"token": used}),                       # replay
            sa_api.post("/api/supplier/auth/verify",
                        json={"token": pending_link["token"]}),     # PENDING
            sa_api.post("/api/supplier/auth/verify", json={"token": ""}),
        ]
        assert all(r.status_code == 401 for r in rejections)
        assert len({r.content for r in rejections}) == 1, \
            f"rejection bodies differ: {[r.content for r in rejections]}"
        assert rejections[0].json() == {"detail": "Invalid or expired link"}

    def test_second_use_of_a_valid_link_rejected(self, sa_api, monkeypatch):
        _active_member(sa_api)
        token = _capture_link_token(sa_api, monkeypatch)
        first = sa_api.post("/api/supplier/auth/verify", json={"token": token})
        second = sa_api.post("/api/supplier/auth/verify", json={"token": token})
        assert first.status_code == 200
        assert second.status_code == 401

    def test_pending_member_cannot_log_in(self, sa_api):
        acct = _account(sa_api)
        p = sa_api._sa.add_member(acct["id"], "newhire@dxpe.com",
                                  role=sa_api._sa.ROLE_MEMBER,
                                  status=sa_api._sa.MEMBER_PENDING)
        link = sa_api._sa.mint_magic_link(p["id"])
        r = sa_api.post("/api/supplier/auth/verify",
                        json={"token": link["token"]})
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired link"}


# ---------------------------------------------------------------------------
# Rate limiting (guardrail 4)
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_per_email_bucket_trips(self, sa_api):
        _active_member(sa_api)
        statuses = [
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "sales@dxpe.com"}).status_code
            for _ in range(4)  # default cap 3 per email per window
        ]
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3] == 429

    def test_distinct_emails_share_the_ip_bucket(self, sa_api, monkeypatch):
        monkeypatch.setenv("SUPPLIER_AUTH_RATE_CAP_IP", "2")
        _active_member(sa_api)
        statuses = [
            sa_api.post("/api/supplier/auth/request-link",
                        json={"email": f"user{i}@unknowndomain.io"}).status_code
            for i in range(3)
        ]
        assert statuses[:2] == [200, 200]
        assert statuses[2] == 429

    def test_429_applies_uniformly_to_known_and_unknown_emails(self, sa_api,
                                                               monkeypatch):
        # The limiter cannot be an oracle either: with the IP cap tuned to 3,
        # three UNKNOWN emails from one IP throttle the fourth request — even
        # one for a never-before-seen known-account email — identically.
        monkeypatch.setenv("SUPPLIER_AUTH_RATE_CAP_IP", "3")
        _active_member(sa_api)
        for i in range(3):
            assert sa_api.post(
                "/api/supplier/auth/request-link",
                json={"email": f"who{i}@unknowndomain.io"}).status_code == 200
        r = sa_api.post("/api/supplier/auth/request-link",
                        json={"email": "brandnew@dxpe.com"})
        assert r.status_code == 429  # IP bucket — uniform, not existence-based

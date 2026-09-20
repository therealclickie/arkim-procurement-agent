"""
Arc 2 T1 — the supplier-identity store: accounts, members, magic links,
sessions, audit.

Direct store tests (the route layer is later tasks' API tests):
  - round-trips for every table,
  - tokens/sessions hashed at rest (a storage inspection proves NO raw token
    is persisted — success criterion 3),
  - 1:1 account↔domain enforced (UNIQUE domain — a second account for one
    domain is impossible),
  - one-OWNER-per-account enforced AT THE PERSISTENCE LAYER (the partial
    unique index rejects a second live OWNER via add_member AND
    update_member_role, and a REVOKED owner frees the slot),
  - magic links single-use + expiring; sessions expiry + revoke,
  - fail-soft store gate: with SUPPLIER_ACCOUNTS_V1 off every write no-ops.

Fixtures isolate the store to tmp_path via the _DB_PATH monkeypatch seam
(convention B). The flag is live-read, so monkeypatch.setenv drives it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from utils import supplier_accounts as sa


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated store with the flag ON."""
    monkeypatch.setattr(sa, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sa, "_DB_PATH", str(tmp_path / "supplier_accounts.sqlite"))
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    return sa


@pytest.fixture
def store_off(tmp_path, monkeypatch):
    """Isolated store with the flag OFF (the inertness wall)."""
    monkeypatch.setattr(sa, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sa, "_DB_PATH", str(tmp_path / "sa_off.sqlite"))
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "")
    return sa


def _account(store, domain="dxpe.com"):
    acct = store.create_account(domain, created_by="fixture")
    assert acct is not None and acct["supplier_domain"] == domain
    return acct


# ---------------------------------------------------------------------------
# Round-trips
# ---------------------------------------------------------------------------

class TestRoundTrips:
    def test_account_roundtrip_and_idempotent_create(self, store):
        a1 = _account(store)
        a2 = store.create_account("dxpe.com")
        assert a2["id"] == a1["id"]           # idempotent — still ONE account
        assert store.get_account_by_domain("DXPE.COM")["id"] == a1["id"]
        assert store.get_account(a1["id"])["supplier_domain"] == "dxpe.com"
        assert store.get_account_by_domain("nope.com") is None

    def test_member_roundtrip_and_membership_key(self, store):
        acct = _account(store)
        m = store.add_member(acct["id"], "Sales@DXPE.com", role=sa.ROLE_OWNER,
                             status=sa.MEMBER_ACTIVE)
        assert m["email"] == "sales@dxpe.com"                 # normalized
        assert m["registrable_domain"] == "dxpe.com"          # registrable (I9)
        assert m["role"] == sa.ROLE_OWNER
        assert store.get_member_by_email(acct["id"], "sales@dxpe.com")["id"] == m["id"]
        assert store.get_member_by_email(acct["id"], "other@dxpe.com") is None
        assert [x["id"] for x in store.list_members(acct["id"])] == [m["id"]]

    def test_magic_link_and_session_roundtrip(self, store):
        acct = _account(store)
        m = store.add_member(acct["id"], "sales@dxpe.com", role=sa.ROLE_OWNER,
                             status=sa.MEMBER_ACTIVE)
        link = store.mint_magic_link(m["id"])
        assert link["token"] and len(link["token"]) >= 32
        v = store.verify_magic_link(link["token"])
        assert v["member_id"] == m["id"] and v["account_id"] == acct["id"]
        sess = store.create_session(m["id"])
        assert sess["token"] and sess["account_id"] == acct["id"]
        s = store.validate_session(sess["token"])
        assert s["member"]["email"] == "sales@dxpe.com"
        assert s["account"]["supplier_domain"] == "dxpe.com"

    def test_audit_row_written_with_context(self, store):
        store.audit("link_requested", email="sales@dxpe.com", actor="public",
                    ip="1.2.3.4", detail={"outcome": "sent"})
        rows = store.list_audit(event="link_requested")
        assert len(rows) == 1
        assert rows[0]["email"] == "sales@dxpe.com"
        assert rows[0]["ip"] == "1.2.3.4"
        assert '"outcome": "sent"' in rows[0]["detail_json"]


# ---------------------------------------------------------------------------
# Hashed at rest — storage inspection (criterion 3)
# ---------------------------------------------------------------------------

class TestHashedAtRest:
    def test_no_raw_magic_link_or_session_token_in_storage(self, store):
        acct = _account(store)
        m = store.add_member(acct["id"], "sales@dxpe.com",
                             role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        link = store.mint_magic_link(m["id"])
        sess = store.create_session(m["id"])
        with sqlite3.connect(store._DB_PATH) as conn:
            dump = "\n".join(
                str(r) for r in conn.execute(
                    "SELECT * FROM supplier_magic_links").fetchall())
            dump += "\n" + "\n".join(
                str(r) for r in conn.execute(
                    "SELECT * FROM supplier_sessions").fetchall())
        assert link["token"] not in dump        # raw link never persisted
        assert sess["token"] not in dump        # raw session never persisted
        assert sa._hash_token(link["token"]) in dump   # the digest IS
        assert sa._hash_token(sess["token"]) in dump

    def test_lookup_is_by_hash_not_string_compare(self, store):
        # An attacker-presented string that merely CONTAINS a token never
        # matches (lookup key is the full digest).
        acct = _account(store)
        m = store.add_member(acct["id"], "sales@dxpe.com",
                             role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        link = store.mint_magic_link(m["id"])
        assert store.verify_magic_link(f"xx{link['token']}xx") is None


# ---------------------------------------------------------------------------
# 1:1 account ↔ domain
# ---------------------------------------------------------------------------

class TestOneAccountPerDomain:
    def test_second_account_for_same_domain_is_impossible(self, store):
        a1 = _account(store, "dxpe.com")
        try:
            with sqlite3.connect(store._DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO supplier_accounts (id, supplier_domain, "
                    "status, created_at, is_test) VALUES ('x','dxpe.com',"
                    "'active','now',1)")
                conn.commit()
        except sqlite3.IntegrityError:
            pass  # the UNIQUE constraint fired — the 1:1 is persistence-level
        else:
            pytest.fail("UNIQUE(supplier_domain) did not reject a duplicate account")
        assert store.get_account_by_domain("dxpe.com")["id"] == a1["id"]

    def test_domain_normalizes_before_dedup(self, store):
        # www. / case variants are ONE domain (the shared registry rule).
        a1 = store.create_account("www.DXPE.com")
        a2 = store.create_account("dxpe.com")
        assert a1 is not None and a2["id"] == a1["id"]


# ---------------------------------------------------------------------------
# One OWNER per account — persistence-layer enforcement (brief T1)
# ---------------------------------------------------------------------------

class TestOneOwnerPerAccount:
    def test_add_member_rejects_second_live_owner(self, store):
        acct = _account(store)
        store.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                         status=sa.MEMBER_ACTIVE)
        with pytest.raises(sa.SupplierAccountsError) as ei:
            store.add_member(acct["id"], "owner2@dxpe.com", role=sa.ROLE_OWNER,
                             status=sa.MEMBER_ACTIVE)
        assert ei.value.code == "owner_exists"
        # And through the RAW index, bypassing every helper (the invariant is
        # not merely in application code):
        with sqlite3.connect(store._DB_PATH) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supplier_members (id, account_id, email, "
                    "registrable_domain, role, status, created_at, is_test) "
                    "VALUES ('raw','" + acct["id"] +
                    "','x@dxpe.com','dxpe.com','OWNER','ACTIVE','now',1)")

    def test_role_update_rejects_second_live_owner(self, store):
        acct = _account(store)
        store.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                         status=sa.MEMBER_ACTIVE)
        m2 = store.add_member(acct["id"], "staff@dxpe.com",
                              role=sa.ROLE_MEMBER, status=sa.MEMBER_ACTIVE)
        with pytest.raises(sa.SupplierAccountsError) as ei:
            store.update_member_role(m2["id"], sa.ROLE_OWNER)
        assert ei.value.code == "owner_exists"
        assert store.get_member(m2["id"])["role"] == sa.ROLE_MEMBER

    def test_revoked_owner_frees_the_owner_slot(self, store):
        acct = _account(store)
        o = store.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                             status=sa.MEMBER_ACTIVE)
        store.update_member_status(o["id"], sa.MEMBER_REVOKED)
        m2 = store.add_member(acct["id"], "newowner@dxpe.com",
                              role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        assert m2["role"] == sa.ROLE_OWNER  # exactly one LIVE owner again

    def test_pending_second_owner_is_also_rejected(self, store):
        # The invariant is one OWNER row, not one ACTIVE owner — a PENDING
        # owner-to-be must not squat the slot either.
        acct = _account(store)
        store.add_member(acct["id"], "owner@dxpe.com", role=sa.ROLE_OWNER,
                         status=sa.MEMBER_ACTIVE)
        with pytest.raises(sa.SupplierAccountsError):
            store.add_member(acct["id"], "p@dxpe.com", role=sa.ROLE_OWNER,
                             status=sa.MEMBER_PENDING)

    def test_two_accounts_each_have_their_own_owner(self, store):
        a1 = _account(store, "dxpe.com")
        a2 = _account(store, "grainger.com")
        store.add_member(a1["id"], "o@dxpe.com", role=sa.ROLE_OWNER,
                         status=sa.MEMBER_ACTIVE)
        m2 = store.add_member(a2["id"], "o@grainger.com", role=sa.ROLE_OWNER,
                              status=sa.MEMBER_ACTIVE)  # no cross-account clash
        assert m2["role"] == sa.ROLE_OWNER


# ---------------------------------------------------------------------------
# Magic-link lifecycle (D4) — single-use, expiring
# ---------------------------------------------------------------------------

class TestMagicLinkLifecycle:
    def _active_member(self, store):
        acct = _account(store)
        m = store.add_member(acct["id"], "sales@dxpe.com",
                             role=sa.ROLE_OWNER, status=sa.MEMBER_ACTIVE)
        return acct, m

    def test_single_use_second_replay_rejected(self, store):
        _, m = self._active_member(store)
        link = store.mint_magic_link(m["id"])
        assert store.verify_magic_link(link["token"]) is not None
        assert store.verify_magic_link(link["token"]) is None  # consumed

    def test_expired_link_rejected(self, store):
        _, m = self._active_member(store)
        link = store.mint_magic_link(m["id"], expiry_minutes=0)
        assert store.verify_magic_link(link["token"]) is None

    def test_unknown_link_rejected(self, store):
        assert store.verify_magic_link("no-such-token") is None

    def test_pending_member_cannot_verify(self, store):
        acct = _account(store)
        p = store.add_member(acct["id"], "sales@dxpe.com",
                             role=sa.ROLE_MEMBER, status=sa.MEMBER_PENDING)
        link = store.mint_magic_link(p["id"])
        assert store.verify_magic_link(link["token"]) is None

    def test_revoked_member_cannot_verify(self, store):
        acct, m = self._active_member(store)
        store.update_member_status(m["id"], sa.MEMBER_REVOKED)
        link = store.mint_magic_link(m["id"])
        assert store.verify_magic_link(link["token"]) is None

    def test_session_requires_active_member(self, store):
        acct = _account(store)
        p = store.add_member(acct["id"], "sales@dxpe.com",
                             role=sa.ROLE_MEMBER, status=sa.MEMBER_PENDING)
        assert store.create_session(p["id"]) is None

    def test_session_expiry_enforced(self, store):
        _, m = self._active_member(store)
        sess = store.create_session(m["id"], expiry_hours=0)
        assert store.validate_session(sess["token"]) is None

    def test_logout_revoke_ends_session(self, store):
        _, m = self._active_member(store)
        sess = store.create_session(m["id"])
        assert store.validate_session(sess["token"]) is not None
        assert store.revoke_session(sess["session_id"]) is True
        assert store.validate_session(sess["token"]) is None
        assert store.revoke_session(sess["session_id"]) is False  # idempotent

    def test_revoked_member_session_invalid(self, store):
        _, m = self._active_member(store)
        sess = store.create_session(m["id"])
        store.update_member_status(m["id"], sa.MEMBER_REVOKED)
        assert store.validate_session(sess["token"]) is None


# ---------------------------------------------------------------------------
# Email helpers (D2 primitives)
# ---------------------------------------------------------------------------

class TestEmailHelpers:
    def test_registrable_domain_matches_account_domain(self, store):
        assert store.email_registrable_domain("sales@dxpe.com") == "dxpe.com"
        assert store.email_registrable_domain("a.b@mail.dxpe.com") == "dxpe.com"
        assert store.email_registrable_domain("sales@DXPE.COM") == "dxpe.com"

    def test_public_mailbox_detection(self, store):
        assert store.is_public_mailbox_email("someone@gmail.com") is True
        assert store.is_public_mailbox_email("someone@outlook.com") is True
        assert store.is_public_mailbox_email("sales@dxpe.com") is False
        assert store.is_public_mailbox_email("not-an-email") is False

    def test_bad_emails_normalize_to_empty(self, store):
        assert store.normalize_email("") == ""
        assert store.normalize_email("a@@b.com") == ""
        assert store.normalize_email("@b.com") == ""


# ---------------------------------------------------------------------------
# Flag-off inertness (prime directive 3 — defense-in-depth layer)
# ---------------------------------------------------------------------------

class TestFlagOffInert:
    def test_writes_noop_flag_off(self, store_off):
        assert store_off.create_account("dxpe.com") is None
        assert store_off.mint_magic_link("m1") is None
        assert store_off.create_session("m1") is None
        assert store_off.revoke_session("s1") is False
        assert store_off.audit("x") is None  # best-effort, but no row written
        assert store_off.list_audit() == []

    def test_verify_noop_flag_off(self, store_off):
        assert store_off.verify_magic_link("anything") is None
        assert store_off.validate_session("anything") is None

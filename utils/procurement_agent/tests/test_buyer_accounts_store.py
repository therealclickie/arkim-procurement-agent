"""
Arc 6 T1 — the buyer identity store (utils/buyer_accounts.py).

Round-trips for companies, members, links, sessions and audit; tokens and
sessions hashed at rest; one company per member at the persistence layer; the
D5 policy defaults and validation; flag off ⇒ every write is a no-op.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def ba(tmp_path, monkeypatch):
    from utils import buyer_accounts
    monkeypatch.setattr(buyer_accounts, "_DB_PATH", str(tmp_path / "buyer_accounts.sqlite"))
    monkeypatch.setenv("BUYER_ACCOUNTS_V1", "1")
    return buyer_accounts


def _company(ba, cid="company-bayfoods", name="Bay Foods", facilities=("fac-stockton",)):
    c = ba.create_company(cid, name, facility_ids=list(facilities),
                          email_domains=["bayfoods.com"], created_by="admin")
    assert c is not None
    return c


def _raw_rows(ba, table):
    with sqlite3.connect(ba._DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


class TestFlag:
    def test_flag_is_strict_opt_in(self, monkeypatch):
        from utils import buyer_accounts
        for off in ("", "0", "false", "no", "junk"):
            monkeypatch.setenv("BUYER_ACCOUNTS_V1", off)
            assert buyer_accounts.buyer_accounts_active() is False
        for on in ("1", "true", "YES", "on"):
            monkeypatch.setenv("BUYER_ACCOUNTS_V1", on)
            assert buyer_accounts.buyer_accounts_active() is True

    def test_flag_off_every_write_is_a_noop(self, ba, monkeypatch):
        monkeypatch.setenv("BUYER_ACCOUNTS_V1", "")
        assert ba.create_company("company-x", "X") is None
        assert ba.add_member("company-x", "a@x.com") is None
        assert ba.mint_magic_link("m1") is None
        assert ba.create_session("m1") is None
        assert ba.validate_session("anything") is None
        assert ba.verify_magic_link("anything") is None
        ba.audit("nothing")
        assert ba.list_audit() == []


class TestCompanies:
    def test_round_trip_and_d5_defaults(self, ba):
        c = _company(ba, facilities=("fac-stockton", "fac-modesto"))
        assert c["id"] == "company-bayfoods"
        assert c["name"] == "Bay Foods"
        assert c["facility_ids"] == ["fac-stockton", "fac-modesto"]
        assert c["email_domains"] == ["bayfoods.com"]
        assert c["auto_approval_limit"] == 2500.0
        assert c["allow_admin_override"] is True
        assert ba.get_company("company-bayfoods") == c

    def test_duplicate_company_is_refused_not_merged(self, ba):
        _company(ba)
        with pytest.raises(ba.BuyerAccountsError) as exc:
            ba.create_company("company-bayfoods", "Other", facility_ids=["fac-x"])
        assert exc.value.code == "company_exists"
        assert ba.get_company("company-bayfoods")["facility_ids"] == ["fac-stockton"]

    def test_company_creation_is_audited(self, ba):
        _company(ba)
        rows = ba.list_audit(event="company_created")
        assert len(rows) == 1 and rows[0]["actor"] == "admin"

    def test_owns_facility(self, ba):
        c = _company(ba)
        assert ba.company_owns_facility(c, "fac-stockton")
        assert not ba.company_owns_facility(c, "fac-elsewhere")
        assert not ba.company_owns_facility(None, "fac-stockton")


class TestMembers:
    def test_round_trip_defaults_to_requester(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "  Dana@BayFoods.com ")
        assert m["email"] == "dana@bayfoods.com"
        assert m["role"] == ba.ROLE_REQUESTER
        assert m["status"] == ba.MEMBER_ACTIVE
        assert ba.get_member(m["id"]) == m
        assert ba.get_member_by_email("dana@bayfoods.com") == m
        assert ba.list_members("company-bayfoods") == [m]

    def test_one_company_per_member_is_enforced_by_the_store(self, ba):
        _company(ba)
        _company(ba, cid="company-northgate", name="Northgate", facilities=("fac-ng",))
        ba.add_member("company-bayfoods", "dana@bayfoods.com")
        with pytest.raises(ba.BuyerAccountsError) as exc:
            ba.add_member("company-northgate", "dana@bayfoods.com")
        assert exc.value.code == "already_member"
        # And at the persistence layer itself, bypassing the function:
        with sqlite3.connect(ba._DB_PATH) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO buyer_members (id, company_id, email, role, status, created_at) "
                "VALUES ('x', 'company-northgate', 'dana@bayfoods.com', 'ADMIN', 'ACTIVE', 'now')")

    def test_unknown_company_or_role(self, ba):
        assert ba.add_member("company-nope", "a@b.com") is None
        _company(ba)
        with pytest.raises(ba.BuyerAccountsError):
            ba.add_member("company-bayfoods", "a@b.com", role="OWNER")

    def test_revoking_a_member_kills_their_sessions(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com", role=ba.ROLE_ADMIN)
        sess = ba.create_session(m["id"])
        assert ba.validate_session(sess["token"]) is not None
        ba.update_member_status(m["id"], ba.MEMBER_REVOKED)
        assert ba.validate_session(sess["token"]) is None

    def test_count_active_role(self, ba):
        _company(ba)
        a = ba.add_member("company-bayfoods", "a@bayfoods.com", role=ba.ROLE_ADMIN)
        ba.add_member("company-bayfoods", "b@bayfoods.com", role=ba.ROLE_ADMIN)
        assert ba.count_active_role("company-bayfoods", ba.ROLE_ADMIN) == 2
        ba.update_member_status(a["id"], ba.MEMBER_REVOKED)
        assert ba.count_active_role("company-bayfoods", ba.ROLE_ADMIN) == 1


class TestLinksAndSessionsHashedAtRest:
    def test_magic_link_round_trip_single_use(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com")
        link = ba.mint_magic_link(m["id"])
        ctx = ba.verify_magic_link(link["token"])
        assert ctx == {"member_id": m["id"], "company_id": "company-bayfoods",
                       "email": "dana@bayfoods.com"}
        assert ba.verify_magic_link(link["token"]) is None   # single-use

    def test_expired_link_rejected(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com")
        link = ba.mint_magic_link(m["id"], expiry_minutes=-1)
        assert ba.verify_magic_link(link["token"]) is None

    def test_revoked_member_link_rejected(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com")
        link = ba.mint_magic_link(m["id"])
        ba.update_member_status(m["id"], ba.MEMBER_REVOKED)
        assert ba.verify_magic_link(link["token"]) is None

    def test_tokens_are_stored_only_as_hashes(self, ba):
        _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com")
        link = ba.mint_magic_link(m["id"])
        sess = ba.create_session(m["id"])
        link_rows = _raw_rows(ba, "buyer_magic_links")
        sess_rows = _raw_rows(ba, "buyer_sessions")
        dump = repr(link_rows) + repr(sess_rows)
        assert link["token"] not in dump
        assert sess["token"] not in dump
        assert link_rows[0]["token_hash"] == ba._hash_token(link["token"])
        assert sess_rows[0]["token_hash"] == ba._hash_token(sess["token"])

    def test_session_round_trip_expiry_and_revoke(self, ba):
        c = _company(ba)
        m = ba.add_member("company-bayfoods", "dana@bayfoods.com")
        sess = ba.create_session(m["id"])
        ctx = ba.validate_session(sess["token"])
        assert ctx["member_id"] == m["id"]
        assert ctx["company_id"] == "company-bayfoods"
        assert ctx["company"]["name"] == c["name"]
        assert ba.revoke_session(sess["session_id"]) is True
        assert ba.validate_session(sess["token"]) is None
        expired = ba.create_session(m["id"], expiry_hours=-1)
        assert ba.validate_session(expired["token"]) is None
        assert ba.validate_session("garbage") is None


class TestApprovalPolicyStorage:
    def test_change_is_audited_with_old_new_and_actor(self, ba):
        _company(ba)
        out = ba.set_approval_policy("company-bayfoods", actor="m-admin",
                                     auto_approval_limit=5000,
                                     allow_admin_override=False)
        assert out["auto_approval_limit"] == 5000.0
        assert out["allow_admin_override"] is False
        rows = ba.list_audit(event="approval_policy_changed")
        by_field = {r["detail"]["field"]: r for r in rows}
        assert by_field["auto_approval_limit"]["detail"]["old"] == 2500.0
        assert by_field["auto_approval_limit"]["detail"]["new"] == 5000.0
        assert by_field["allow_admin_override"]["detail"] == {
            "field": "allow_admin_override", "old": True, "new": False}
        assert {r["actor"] for r in rows} == {"m-admin"}

    def test_zero_is_valid(self, ba):
        _company(ba)
        assert ba.set_approval_policy("company-bayfoods", actor="a",
                                      auto_approval_limit=0)["auto_approval_limit"] == 0.0

    @pytest.mark.parametrize("bad", [-1, -0.01, "2500", "abc", None.__class__, True,
                                     float("nan"), float("inf")])
    def test_negative_or_non_numeric_rejected(self, ba, bad):
        _company(ba)
        if bad is None.__class__:
            bad = [2500]
        with pytest.raises(ba.BuyerAccountsError) as exc:
            ba.set_approval_policy("company-bayfoods", actor="a", auto_approval_limit=bad)
        assert exc.value.code == "invalid_limit"
        assert ba.get_company("company-bayfoods")["auto_approval_limit"] == 2500.0

    def test_noop_change_writes_no_audit(self, ba):
        _company(ba)
        ba.set_approval_policy("company-bayfoods", actor="a", auto_approval_limit=2500)
        assert ba.list_audit(event="approval_policy_changed") == []

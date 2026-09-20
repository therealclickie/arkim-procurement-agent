"""
Arc 3 T11 (D6) — RBAC is enforced on the SERVER, the UI merely reflects it.

The members screen hides invite / change-role / remove from a MEMBER. That
proves nothing on its own: a hidden button and an enforced policy look
identical from the outside. This file is the other half — it calls each
hidden control's endpoint DIRECTLY, with exactly the credential the browser
holds (the arc-3 httpOnly cookie, not a bearer), and gets 403.

Arc 2 already proved this for the bearer path. It is re-proved here for the
cookie path specifically, because the cookie is the credential the UI
actually uses and an exemption that applied only to bearers would leave the
browser path ungated.
"""
from __future__ import annotations

import pytest

from utils.procurement_agent.tests._arc3_session_fixtures import (  # noqa: F401
    APP_ORIGIN, sess_api, verify,
)


def _team(client):
    """One account: an OWNER, an ADMIN and an ordinary MEMBER, all ACTIVE."""
    sa = client._sa
    acct = sa.create_account("dxpe.com")
    assert acct is not None
    rows = {}
    for email, role in (("owner@dxpe.com", sa.ROLE_OWNER),
                        ("admin@dxpe.com", sa.ROLE_ADMIN),
                        ("member@dxpe.com", sa.ROLE_MEMBER)):
        m = sa.add_member(acct["id"], email, role=role,
                          status=sa.MEMBER_ACTIVE)
        assert m is not None
        rows[role] = m
    return acct, rows


def _sign_in(client, monkeypatch, email):
    client.cookies.clear()
    verify(client, monkeypatch, email=email)


# ---------------------------------------------------------------------------
# The hidden controls, called directly
# ---------------------------------------------------------------------------

class TestMemberIsRefusedByTheServer:
    """Every control the UI hides from a MEMBER is refused when called
    directly with that MEMBER's own session cookie."""

    def test_invite_is_403(self, sess_api, monkeypatch):
        _acct, rows = _team(sess_api)
        _sign_in(sess_api, monkeypatch, "member@dxpe.com")
        r = sess_api.post("/api/supplier/members/invite",
                          headers={"Origin": APP_ORIGIN},
                          json={"email": "new@dxpe.com"})
        assert r.status_code == 403
        assert r.json() == {"detail": "Forbidden"}

    def test_change_role_is_403(self, sess_api, monkeypatch):
        _acct, rows = _team(sess_api)
        target = rows[sess_api._sa.ROLE_ADMIN]["id"]
        _sign_in(sess_api, monkeypatch, "member@dxpe.com")
        r = sess_api.post(f"/api/supplier/members/{target}/role",
                          headers={"Origin": APP_ORIGIN},
                          json={"role": "MEMBER"})
        assert r.status_code == 403

    def test_revoke_is_403(self, sess_api, monkeypatch):
        _acct, rows = _team(sess_api)
        target = rows[sess_api._sa.ROLE_ADMIN]["id"]
        _sign_in(sess_api, monkeypatch, "member@dxpe.com")
        r = sess_api.post(f"/api/supplier/members/{target}/revoke",
                          headers={"Origin": APP_ORIGIN})
        assert r.status_code == 403

    def test_the_refusal_is_not_merely_the_csrf_check(self, sess_api,
                                                      monkeypatch):
        # A 403 that was really a CSRF 401 in disguise, or an origin problem,
        # would make the test above vacuous. Same request, same Origin, as an
        # ADMIN: it succeeds — so the 403 is about the ROLE and nothing else.
        _acct, _rows = _team(sess_api)
        _sign_in(sess_api, monkeypatch, "admin@dxpe.com")
        r = sess_api.post("/api/supplier/members/invite",
                          headers={"Origin": APP_ORIGIN},
                          json={"email": "new@dxpe.com"})
        assert r.status_code == 200, r.text

    def test_a_member_may_still_view_the_team(self, sess_api, monkeypatch):
        # The screen shows a MEMBER the list — view_members is in every role.
        # If the read were also 403 the UI would be showing an empty page and
        # calling it a permission model.
        _acct, _rows = _team(sess_api)
        _sign_in(sess_api, monkeypatch, "member@dxpe.com")
        r = sess_api.get("/api/supplier/members")
        assert r.status_code == 200
        emails = {m["email"] for m in r.json()["members"]}
        assert emails == {"owner@dxpe.com", "admin@dxpe.com",
                          "member@dxpe.com"}


# ---------------------------------------------------------------------------
# The ownership invariant — hidden for EVERYONE, and refused for everyone
# ---------------------------------------------------------------------------

class TestOwnerIsUntouchable:
    @pytest.mark.parametrize("actor", ["owner@dxpe.com", "admin@dxpe.com"])
    def test_the_owner_cannot_be_revoked_by_anyone(self, sess_api, monkeypatch,
                                                   actor):
        _acct, rows = _team(sess_api)
        owner_id = rows[sess_api._sa.ROLE_OWNER]["id"]
        _sign_in(sess_api, monkeypatch, actor)
        r = sess_api.post(f"/api/supplier/members/{owner_id}/revoke",
                          headers={"Origin": APP_ORIGIN})
        assert r.status_code == 403

    def test_the_owner_cannot_be_demoted(self, sess_api, monkeypatch):
        _acct, rows = _team(sess_api)
        owner_id = rows[sess_api._sa.ROLE_OWNER]["id"]
        _sign_in(sess_api, monkeypatch, "admin@dxpe.com")
        r = sess_api.post(f"/api/supplier/members/{owner_id}/role",
                          headers={"Origin": APP_ORIGIN},
                          json={"role": "MEMBER"})
        assert r.status_code == 403

    def test_nobody_can_be_promoted_to_owner(self, sess_api, monkeypatch):
        # The UI offers only ADMIN and MEMBER; the server is why.
        _acct, rows = _team(sess_api)
        target = rows[sess_api._sa.ROLE_MEMBER]["id"]
        _sign_in(sess_api, monkeypatch, "owner@dxpe.com")
        r = sess_api.post(f"/api/supplier/members/{target}/role",
                          headers={"Origin": APP_ORIGIN},
                          json={"role": "OWNER"})
        assert r.status_code in (403, 422), r.text


# ---------------------------------------------------------------------------
# The permissions the UI reads are the ones the server enforces
# ---------------------------------------------------------------------------

class TestPermissionsMatchEnforcement:
    def test_me_permissions_predict_the_403(self, sess_api, monkeypatch):
        # The screen hides a control when `permissions` lacks its capability.
        # This asserts that list is not decorative: what it omits, the server
        # refuses; what it includes, the server allows.
        _acct, _rows = _team(sess_api)
        for email, expect_manage in (("member@dxpe.com", False),
                                     ("admin@dxpe.com", True)):
            _sign_in(sess_api, monkeypatch, email)
            perms = sess_api.get("/api/supplier/me").json()["member"]["permissions"]
            assert ("manage_members" in perms) is expect_manage
            r = sess_api.post("/api/supplier/members/invite",
                              headers={"Origin": APP_ORIGIN},
                              json={"email": f"x{email[0]}@dxpe.com"})
            assert (r.status_code == 200) is expect_manage, r.text

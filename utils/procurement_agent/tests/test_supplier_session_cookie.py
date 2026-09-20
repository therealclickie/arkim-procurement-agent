"""
Arc 3 T1 — the BROWSER session cookie (D1).

``POST /api/supplier/auth/verify`` additionally sets ``gofer_supplier_session``
as HttpOnly / Secure / SameSite=Lax / Path=/, and the session dependency
accepts EITHER that cookie or arc 2's ``Authorization: Bearer``. Everything
here is additive: the bearer path, its response body and its uniform 401 are
byte-identical to arc 2.

Asserted on the REAL ``Set-Cookie`` header, not on a jar read — a jar read
loses every attribute, so "the cookie is HttpOnly" would be unfalsifiable.
"""
from __future__ import annotations

from utils.procurement_agent.tests._arc3_session_fixtures import (  # noqa: F401
    APP_ORIGIN, active_member, magic_link_token, sess_api, sess_api_http,
    set_cookie_header, verify,
)


# ---------------------------------------------------------------------------
# Issuance — the four attributes, on the real header
# ---------------------------------------------------------------------------

class TestCookieIssuance:
    def test_verify_sets_cookie_with_all_four_attributes(self, sess_api,
                                                         monkeypatch):
        active_member(sess_api)
        r = verify(sess_api, monkeypatch)
        raw = set_cookie_header(r)
        assert raw, "verify set no gofer_supplier_session cookie"
        lowered = raw.lower()
        assert "httponly" in lowered
        assert "secure" in lowered
        assert "samesite=lax" in lowered
        assert "path=/" in lowered

    def test_cookie_carries_the_same_session_as_the_body_token(self, sess_api,
                                                               monkeypatch):
        # D1: one session, two presentations — NOT two sessions. The body keeps
        # arc 2's raw bearer for API clients.
        active_member(sess_api)
        r = verify(sess_api, monkeypatch)
        assert set(r.json().keys()) == {"token", "expires_at"}
        assert sess_api.cookies.get("gofer_supplier_session") == r.json()["token"]

    def test_cookie_expiry_tracks_the_session_expiry(self, sess_api,
                                                     monkeypatch):
        # A cookie that outlived its server-side session would leave the
        # browser presenting a dead credential; Max-Age is derived from the
        # session's own expires_at (24h TTL, so <= 86400s, never unbounded).
        active_member(sess_api)
        r = verify(sess_api, monkeypatch)
        raw = set_cookie_header(r)
        max_age = int([p.split("=", 1)[1] for p in raw.split("; ")
                       if p.lower().startswith("max-age=")][0])
        assert 0 < max_age <= 24 * 3600

    def test_max_age_helper_never_yields_a_longer_life_on_bad_input(self,
                                                                    sess_api):
        # Fail-soft toward the SHORTER lifetime: an unparseable expiry becomes
        # a browser-session cookie (None), never a long-lived one.
        api = sess_api._api_server
        assert api._supplier_session_cookie_max_age(None) is None
        assert api._supplier_session_cookie_max_age("not-a-date") is None
        assert api._supplier_session_cookie_max_age(
            "2000-01-01T00:00:00+00:00") == 0


# ---------------------------------------------------------------------------
# Acceptance — cookie OR bearer, and the uniform 401 either way
# ---------------------------------------------------------------------------

class TestCookieAuthenticates:
    def test_cookie_alone_authenticates_me(self, sess_api, monkeypatch):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        # No Authorization header anywhere — the jar is the whole credential.
        r = sess_api.get("/api/supplier/me")
        assert r.status_code == 200, r.text
        assert r.json()["member"]["email"] == "sales@dxpe.com"

    def test_bearer_still_authenticates_me(self, sess_api_http, monkeypatch):
        # Arc 2's exact posture, re-proven in this arc's file: plain http, no
        # cookie is ever returned (Secure), bearer carries the session.
        active_member(sess_api_http)
        token = magic_link_token(sess_api_http, monkeypatch)
        r = sess_api_http.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200
        bearer = r.json()["token"]
        me = sess_api_http.get("/api/supplier/me",
                               headers={"Authorization": f"Bearer {bearer}"})
        assert me.status_code == 200
        assert me.json()["member"]["email"] == "sales@dxpe.com"

    def test_bearer_wins_when_both_are_present(self, sess_api, monkeypatch):
        # Bearer-first resolution (the T2 CSRF exemption keys off the mode, so
        # the order is load-bearing, not cosmetic). A garbage bearer alongside
        # a VALID cookie must still be rejected — the bearer is what was
        # chosen, and it is invalid.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        assert sess_api.get("/api/supplier/me").status_code == 200
        r = sess_api.get("/api/supplier/me",
                         headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}

    def test_every_cookie_failure_is_the_same_uniform_401(self, sess_api,
                                                          monkeypatch):
        # The arc-2 rejection contract, extended to the cookie: missing /
        # garbage / expired / revoked are ONE body (Set-size-1), and it is
        # byte-identical to the bearer rejection.
        acct, member = active_member(sess_api)
        sa = sess_api._sa
        expired = sa.create_session(member["id"], expiry_hours=-1)
        assert expired is not None
        verify(sess_api, monkeypatch)
        live = sess_api.cookies.get("gofer_supplier_session")

        def get_with_cookie(value):
            sess_api.cookies.clear()
            if value is not None:
                sess_api.cookies.set("gofer_supplier_session", value)
            return sess_api.get("/api/supplier/me")

        rejections = [
            get_with_cookie(None),                 # no cookie at all
            get_with_cookie("garbage"),            # unknown session
            get_with_cookie(expired["token"]),     # expired
        ]
        # Revoked: log the live session out, then re-present its cookie.
        sess_api.cookies.clear()
        sess_api.cookies.set("gofer_supplier_session", live)
        assert sess_api.post("/api/supplier/auth/logout",
                             headers={"Origin": APP_ORIGIN}).status_code == 200
        rejections.append(get_with_cookie(live))   # revoked

        assert all(r.status_code == 401 for r in rejections)
        assert len({r.content for r in rejections}) == 1
        assert rejections[0].json() == {"detail": "Invalid or expired session"}

    def test_empty_cookie_is_rejected_not_treated_as_absent_auth(self, sess_api):
        sess_api.cookies.set("gofer_supplier_session", "")
        r = sess_api.get("/api/supplier/me")
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}


# ---------------------------------------------------------------------------
# Logout — revoke server-side AND clear the browser's copy
# ---------------------------------------------------------------------------

class TestLogoutClearsCookie:
    def test_logout_clears_the_cookie_and_revokes_the_session(self, sess_api,
                                                              monkeypatch):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        assert sess_api.get("/api/supplier/me").status_code == 200

        r = sess_api.post("/api/supplier/auth/logout",
                          headers={"Origin": APP_ORIGIN})
        assert r.status_code == 200 and r.json() == {"ok": True}

        # (a) the browser is told to drop it — same Path, or it would keep it.
        cleared = set_cookie_header(r)
        assert cleared, "logout sent no clearing Set-Cookie"
        assert "path=/" in cleared.lower()
        assert cleared.split(";", 1)[0] in (
            'gofer_supplier_session=""', "gofer_supplier_session="), cleared

        # (b) the jar is empty, so the next call carries nothing.
        assert not sess_api.cookies.get("gofer_supplier_session")

        # (c) and even a client that kept the value gets nothing — the session
        # is revoked SERVER-side. Clearing a cookie is not a security control.
        assert sess_api.get("/api/supplier/me").status_code == 401


# ---------------------------------------------------------------------------
# Flag-off inertness
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_no_cookie_and_no_route_when_the_flag_is_off(self, sess_api,
                                                         monkeypatch):
        active_member(sess_api)
        token = magic_link_token(sess_api, monkeypatch)
        monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "0")
        r = sess_api.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}
        assert set_cookie_header(r) == ""
        assert not sess_api.cookies.get("gofer_supplier_session")

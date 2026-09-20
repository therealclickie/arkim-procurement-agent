"""
Arc 3 T2 — CSRF origin check on the cookie-authenticated session (D2).

Cookie auth is AMBIENT: the browser attaches it to a cross-site form post
without the attacking page ever seeing the value. SameSite=Lax (T1) already
stops that in current browsers; this is the second lock. Bearer auth is not
ambient and is exempt by design.

Everything below runs against the REAL dependency through the REAL routes
(reviewer checklist R7) — nothing here is mocked. The rejection is asserted
to be byte-identical to the ordinary session 401, so a CSRF refusal is not
itself an oracle.
"""
from __future__ import annotations

import pytest

from utils.procurement_agent.tests._arc3_session_fixtures import (  # noqa: F401
    APP_ORIGIN, FOREIGN_ORIGIN, active_member, magic_link_token, sess_api,
    sess_api_http, verify,
)

# The three state-changing session routes reachable with an OWNER session and
# no extra setup. `members/invite` is included so the check is proven on a
# capability-gated route too, not only on the ungated ones.
STATE_CHANGING = [
    ("/api/supplier/auth/logout", {}),
    ("/api/supplier/members/invite", {"json": {"email": "new@dxpe.com"}}),
]


class TestCookieAuthIsOriginChecked:
    @pytest.mark.parametrize("path,kwargs", STATE_CHANGING)
    def test_foreign_origin_is_rejected(self, sess_api, monkeypatch, path,
                                        kwargs):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        r = sess_api.post(path, headers={"Origin": FOREIGN_ORIGIN}, **kwargs)
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}

    @pytest.mark.parametrize("path,kwargs", STATE_CHANGING)
    def test_app_origin_is_accepted(self, sess_api, monkeypatch, path, kwargs):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        r = sess_api.post(path, headers={"Origin": APP_ORIGIN}, **kwargs)
        assert r.status_code == 200, r.text

    def test_referer_stands_in_for_a_missing_origin(self, sess_api,
                                                    monkeypatch):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/auth/logout",
                          headers={"Referer": f"{APP_ORIGIN}/supplier/requests"})
        assert r.status_code == 200, r.text

    def test_foreign_referer_is_rejected(self, sess_api, monkeypatch):
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/auth/logout",
                          headers={"Referer": f"{FOREIGN_ORIGIN}/attack"})
        assert r.status_code == 401

    def test_no_origin_and_no_referer_is_rejected(self, sess_api, monkeypatch):
        # R-G3b, stated as a decision rather than discovered: a cookie with no
        # provenance at all cannot be vouched for. TestClient sends neither
        # header by default, so this is the bare-request case.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/auth/logout")
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}
        # ...and the session survives the refusal — a blocked CSRF attempt
        # must not log the real user out as a side effect.
        assert sess_api.get("/api/supplier/me").status_code == 200

    def test_a_near_miss_origin_does_not_pass(self, sess_api, monkeypatch):
        # Substring/prefix matching would be the classic bug here.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        for bad in (f"{APP_ORIGIN}.evil.com", "http://localhost:3000.evil.com",
                    "https://localhost:3000", "http://localhost:3001",
                    "http://evil.com/?x=http://localhost:3000"):
            r = sess_api.post("/api/supplier/auth/logout",
                              headers={"Origin": bad})
            assert r.status_code == 401, f"{bad} was accepted"


class TestSafeMethodsAndBearerAreUnaffected:
    def test_get_is_unaffected_by_a_foreign_origin(self, sess_api,
                                                   monkeypatch):
        # Reads change nothing; SameSite=Lax + the same-origin policy already
        # mean a cross-site page cannot read the response body.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        for path in ("/api/supplier/me", "/api/supplier/requests",
                     "/api/supplier/members"):
            r = sess_api.get(path, headers={"Origin": FOREIGN_ORIGIN})
            assert r.status_code == 200, f"{path}: {r.text}"

    def test_bearer_post_from_a_foreign_origin_is_exempt(self, sess_api_http,
                                                         monkeypatch):
        # The exemption, proven end-to-end: a bearer is not ambient, so an
        # attacker page cannot produce this request in the first place.
        active_member(sess_api_http)
        token = magic_link_token(sess_api_http, monkeypatch)
        r = sess_api_http.post("/api/supplier/auth/verify",
                               json={"token": token})
        bearer = r.json()["token"]
        out = sess_api_http.post(
            "/api/supplier/members/invite",
            json={"email": "new@dxpe.com"},
            headers={"Authorization": f"Bearer {bearer}",
                     "Origin": FOREIGN_ORIGIN})
        assert out.status_code == 200, out.text

    def test_bearer_post_with_no_origin_is_exempt(self, sess_api_http,
                                                  monkeypatch):
        active_member(sess_api_http)
        token = magic_link_token(sess_api_http, monkeypatch)
        bearer = sess_api_http.post("/api/supplier/auth/verify",
                                    json={"token": token}).json()["token"]
        out = sess_api_http.post("/api/supplier/auth/logout",
                                 headers={"Authorization": f"Bearer {bearer}"})
        assert out.status_code == 200, out.text

    def test_a_stale_cookie_alongside_a_bearer_does_not_arm_the_check(
            self, sess_api, monkeypatch):
        # Bearer-first (T1) means a request carrying BOTH is bearer-mode and
        # therefore exempt. This is safe precisely because the attacker who
        # can set the cookie still cannot set the header.
        active_member(sess_api)
        r = verify(sess_api, monkeypatch)
        bearer = r.json()["token"]
        out = sess_api.post("/api/supplier/auth/logout",
                            headers={"Authorization": f"Bearer {bearer}",
                                     "Origin": FOREIGN_ORIGIN})
        assert out.status_code == 200, out.text


class TestPublicAuthRoutesAreNotOriginGated:
    def test_request_link_and_verify_work_without_an_origin(self, sess_api,
                                                            monkeypatch):
        # They carry no ambient credential — there is nothing for a CSRF to
        # ride. Gating them would break the emailed-link landing.
        active_member(sess_api)
        token = magic_link_token(sess_api, monkeypatch)
        r = sess_api.post("/api/supplier/auth/verify", json={"token": token})
        assert r.status_code == 200, r.text


class TestConfiguredOriginList:
    def test_the_check_uses_the_same_list_cors_enforces(self, sess_api,
                                                        monkeypatch):
        # One configuration surface, not two: adding an origin to
        # _cors_origins admits it here as well.
        active_member(sess_api)
        verify(sess_api, monkeypatch)
        extra = "https://portal.example.com"
        assert sess_api.post("/api/supplier/auth/logout",
                             headers={"Origin": extra}).status_code == 401
        monkeypatch.setattr(sess_api._api_server, "_cors_origins",
                            sess_api._api_server._cors_origins + [extra])
        assert sess_api.post("/api/supplier/auth/logout",
                             headers={"Origin": extra}).status_code == 200

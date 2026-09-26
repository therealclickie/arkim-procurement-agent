"""
Arc 6 T3 — buyer login and sessions (D4).

Magic-link request and verify, the ``gofer_buyer_session`` cookie (asserted on
the REAL Set-Cookie header), the arc 3 origin check on cookie-authenticated
state changes, logout, ``GET /api/buyer/me`` — and independence from the
supplier session.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    APP_ORIGIN, BUYER_COOKIE, COMPANY_A, FOREIGN_ORIGIN, buyer_api, buyer_api_off,
    make_company, make_member, origin_headers,
)


def _recording_sender(client, monkeypatch):
    recorded = []
    email_sender = client._email_sender

    class RecordingSender(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
    return recorded


def _request_link(client, email):
    return client.post("/api/buyer/auth/request-link", json={"email": email})


def _token_from(message) -> str:
    return message.body.split("/verify?token=", 1)[1].split("\n", 1)[0].strip()


def _login_via_routes(client, monkeypatch, email="dana@bayfoods.com"):
    recorded = _recording_sender(client, monkeypatch)
    assert _request_link(client, email).status_code == 200
    assert recorded, "no sign-in email was delivered"
    r = client.post("/api/buyer/auth/verify", json={"token": _token_from(recorded[-1])},
                    headers=origin_headers())
    assert r.status_code == 200, r.text
    return r


def _set_cookie_header(response, name=BUYER_COOKIE) -> str:
    for key, value in response.headers.raw:
        if key.decode().lower() == "set-cookie" and value.decode().startswith(f"{name}="):
            return value.decode()
    return ""


def _fingerprint(r):
    """Everything an observer of the response can see that could leak."""
    return (r.status_code, r.content,
            r.headers.get("content-type"), r.headers.get("cache-control"),
            r.headers.get("referrer-policy"), r.headers.get("set-cookie"))


class TestRequestLinkIsUniform:
    def test_known_unknown_and_rate_limited_are_byte_identical(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_ADMIN)
        recorded = _recording_sender(buyer_api, monkeypatch)
        monkeypatch.setenv("BUYER_AUTH_RATE_CAP_EMAIL", "1")

        known = _request_link(buyer_api, "dana@bayfoods.com")
        unknown = _request_link(buyer_api, "stranger@bayfoods.com")
        limited = _request_link(buyer_api, "dana@bayfoods.com")   # 2nd within the cap window

        assert _fingerprint(known) == _fingerprint(unknown) == _fingerprint(limited)
        assert known.json() == {"ok": True}
        # Only the genuine, un-throttled member request produced mail.
        assert [m.to for m in recorded] == [["dana@bayfoods.com"]]
        outcomes = [r["detail"]["outcome"] for r in buyer_api._ba.list_audit(event="link_requested")]
        assert outcomes == ["send_attempted", "no_active_member", "rate_limited"]

    def test_contrast_case_the_comparison_can_fail(self, buyer_api):
        # The fingerprint is sensitive: a different endpoint outcome differs.
        make_company(buyer_api)
        ok = _request_link(buyer_api, "someone@bayfoods.com")
        bad = buyer_api.post("/api/buyer/auth/verify", json={"token": "nope"})
        assert _fingerprint(ok) != _fingerprint(bad)
        assert bad.status_code == 401

    def test_revoked_member_gets_the_uniform_response_and_no_mail(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        m = make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        buyer_api._ba.update_member_status(m["id"], buyer_api._ba.MEMBER_REVOKED)
        recorded = _recording_sender(buyer_api, monkeypatch)
        r = _request_link(buyer_api, "dana@bayfoods.com")
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert recorded == []

    def test_unknown_email_is_never_auto_joined(self, buyer_api):
        make_company(buyer_api)
        _request_link(buyer_api, "new.person@bayfoods.com")
        assert buyer_api._ba.get_member_by_email("new.person@bayfoods.com") is None


class TestVerifyAndCookie:
    def test_cookie_has_all_four_attributes_on_the_real_header(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        r = _login_via_routes(buyer_api, monkeypatch)
        raw = _set_cookie_header(r).lower()
        assert raw, "verify set no gofer_buyer_session cookie"
        for attr in ("httponly", "secure", "samesite=lax", "path=/"):
            assert attr in raw, attr
        max_age = int([p.split("=", 1)[1] for p in raw.split("; ")
                       if p.startswith("max-age=")][0])
        assert 0 < max_age <= 24 * 3600

    def test_verify_body_carries_no_token(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        r = _login_via_routes(buyer_api, monkeypatch)
        assert set(r.json()) == {"ok", "expires_at"}
        assert buyer_api.cookies.get(BUYER_COOKIE) not in r.text

    def test_link_is_single_use(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        recorded = _recording_sender(buyer_api, monkeypatch)
        _request_link(buyer_api, "dana@bayfoods.com")
        token = _token_from(recorded[-1])
        assert buyer_api.post("/api/buyer/auth/verify", json={"token": token}).status_code == 200
        again = buyer_api.post("/api/buyer/auth/verify", json={"token": token})
        assert again.status_code == 401
        assert again.json() == {"detail": "Invalid or expired link"}

    def test_me_returns_company_member_and_permissions(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_REQUESTER)
        _login_via_routes(buyer_api, monkeypatch)
        me = buyer_api.get("/api/buyer/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["company"] == {"id": COMPANY_A, "name": "Bay Foods",
                                   "facility_ids": ["fac-stockton"]}
        assert body["member"]["email"] == "dana@bayfoods.com"
        assert body["member"]["role"] == "REQUESTER"
        assert body["member"]["permissions"] == ["raise_request", "view_company"]

    def test_me_without_a_session_is_the_uniform_401(self, buyer_api):
        r = buyer_api.get("/api/buyer/me")
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}
        buyer_api.cookies.set(BUYER_COOKIE, "garbage")
        assert buyer_api.get("/api/buyer/me").json() == {"detail": "Invalid or expired session"}

    def test_a_bearer_header_is_not_a_buyer_credential(self, buyer_api):
        # Q1: the cookie is the only buyer identity. A session token presented
        # as a bearer authenticates nothing.
        make_company(buyer_api)
        m = make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_ADMIN)
        sess = buyer_api._ba.create_session(m["id"])
        r = buyer_api.get("/api/buyer/me", headers={"Authorization": f"Bearer {sess['token']}"})
        assert r.status_code == 401


class TestCsrfAndLogout:
    def test_foreign_origin_cookie_post_is_refused(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        _login_via_routes(buyer_api, monkeypatch)
        r = buyer_api.post("/api/buyer/auth/logout", headers={"Origin": FOREIGN_ORIGIN})
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired session"}
        no_origin = buyer_api.post("/api/buyer/auth/logout")
        assert no_origin.status_code == 401
        # The session survived both refused attempts.
        assert buyer_api.get("/api/buyer/me").status_code == 200

    def test_logout_revokes_and_clears_the_cookie(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_BUYER)
        _login_via_routes(buyer_api, monkeypatch)
        live = buyer_api.cookies.get(BUYER_COOKIE)
        r = buyer_api.post("/api/buyer/auth/logout", headers=origin_headers())
        assert r.status_code == 200
        cleared = _set_cookie_header(r).lower()
        assert cleared and ("max-age=0" in cleared or "expires=" in cleared)
        assert buyer_api._ba.validate_session(live) is None
        events = [a["event"] for a in buyer_api._ba.list_audit(company_id=COMPANY_A)]
        assert "login" in events and "logout" in events


class TestSupplierAndBuyerSessionsAreIndependent:
    def _supplier(self, client, monkeypatch):
        from utils import supplier_accounts as sa
        monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
        acct = sa.create_account("dxpe.com")
        member = sa.add_member(acct["id"], "sales@dxpe.com", role=sa.ROLE_OWNER,
                               status=sa.MEMBER_ACTIVE)
        return sa, sa.create_session(member["id"])

    def test_neither_cookie_authenticates_the_other_side(self, buyer_api, monkeypatch):
        make_company(buyer_api)
        m = make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_ADMIN)
        sa, supplier_sess = self._supplier(buyer_api, monkeypatch)
        buyer_sess = buyer_api._ba.create_session(m["id"])
        buyer_api.cookies.set(BUYER_COOKIE, supplier_sess["token"])
        assert buyer_api.get("/api/buyer/me").status_code == 401
        buyer_api.cookies.clear()
        buyer_api.cookies.set("gofer_supplier_session", buyer_sess["token"])
        assert buyer_api.get("/api/supplier/me").status_code == 401

    def test_both_sessions_coexist_and_buyer_logout_leaves_the_supplier_alone(
            self, buyer_api, monkeypatch):
        make_company(buyer_api)
        m = make_member(buyer_api, "dana@bayfoods.com", buyer_api._ba.ROLE_ADMIN)
        sa, supplier_sess = self._supplier(buyer_api, monkeypatch)
        buyer_sess = buyer_api._ba.create_session(m["id"])
        buyer_api.cookies.set(BUYER_COOKIE, buyer_sess["token"])
        buyer_api.cookies.set("gofer_supplier_session", supplier_sess["token"])
        assert buyer_api.get("/api/buyer/me").status_code == 200
        assert buyer_api.get("/api/supplier/me").status_code == 200
        assert buyer_api.post("/api/buyer/auth/logout", headers=origin_headers()).status_code == 200
        assert sa.validate_session(supplier_sess["token"]) is not None
        assert buyer_api._ba.validate_session(buyer_sess["token"]) is None


class TestFlagOff:
    def test_every_new_buyer_route_is_absent(self, buyer_api_off):
        import api_server
        new_routes = [r for r in api_server.app.routes
                      if isinstance(r, APIRoute) and (r.path.startswith("/api/buyer/")
                                                      or r.path.startswith("/api/admin/buyer-"))]
        assert len(new_routes) >= 4
        for route in new_routes:
            path = route.path
            for name in ("member_id",):
                path = path.replace("{" + name + "}", "x")
            for method in route.methods:
                r = buyer_api_off.request(method, path, json={}, headers={
                    **origin_headers(), "Authorization": "Bearer test-admin-secret-arc6"})
                assert r.status_code == 404, (method, path, r.status_code)
                assert r.json() == {"detail": "Not Found"}

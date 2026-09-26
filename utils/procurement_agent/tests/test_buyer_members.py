"""
Arc 6 T6 — company bootstrap and member management (D3).

Only a Gofer operator (require_admin) creates a company and invites its first
Admin; only a company Admin manages members; the last Admin cannot demote or
revoke themselves; every action is audited.
"""
from __future__ import annotations

import pytest

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    ADMIN_TOKEN, COMPANY_A, COMPANY_B, buyer_api, make_company, make_member, new_client_for,
    origin_headers, two_companies,
)

H = origin_headers()
ADMIN_H = {**H, "Authorization": f"Bearer {ADMIN_TOKEN}"}
_COMPANY = {"company_id": COMPANY_A, "name": "Bay Foods", "facility_ids": ["fac-stockton"],
            "email_domains": ["BayFoods.com"]}


def _recording_sender(client, monkeypatch):
    recorded = []
    email_sender = client._email_sender

    class RecordingSender(email_sender.GmailSender):
        def send(self, message):
            recorded.append(message)
            return super().send(message)

    monkeypatch.setattr(email_sender, "GmailSender", RecordingSender)
    return recorded


class TestBootstrapIsGoferOnly:
    def test_create_company_requires_the_gofer_admin_token(self, buyer_api):
        assert buyer_api.post("/api/admin/buyer-companies", json=_COMPANY).status_code == 401
        wrong = buyer_api.post("/api/admin/buyer-companies", json=_COMPANY,
                               headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 403
        assert buyer_api._ba.get_company(COMPANY_A) is None

    def test_a_buyer_admin_session_is_not_a_gofer_operator(self, buyer_api):
        make_company(buyer_api, cid="company-other", name="Other", facilities=("fac-o",))
        admin = make_member(buyer_api, "admin@other.com", buyer_api._ba.ROLE_ADMIN,
                            cid="company-other")
        client = new_client_for(buyer_api, admin)
        r = client.post("/api/admin/buyer-companies", json=_COMPANY, headers=H)
        assert r.status_code == 401
        r = client.post("/api/admin/buyer-companies/company-other/invite-admin",
                        json={"email": "x@other.com"}, headers=H)
        assert r.status_code == 401

    def test_bootstrap_creates_company_allowlists_domains_and_audits(self, buyer_api):
        from utils import send_governance
        r = buyer_api.post("/api/admin/buyer-companies", json=_COMPANY, headers=ADMIN_H)
        assert r.status_code == 201, r.text
        c = r.json()["company"]
        assert c["id"] == COMPANY_A and c["auto_approval_limit"] == 2500.0
        assert c["allow_admin_override"] is True
        assert "bayfoods.com" in {row["domain"] for row in send_governance.allowlist_list()}
        events = [a["event"] for a in buyer_api._ba.list_audit(company_id=COMPANY_A)]
        assert events == ["company_created", "send_allowlist_add"]
        again = buyer_api.post("/api/admin/buyer-companies", json=_COMPANY, headers=ADMIN_H)
        assert again.status_code == 409

    def test_invite_first_admin_sends_a_sign_in_link_and_audits(self, buyer_api, monkeypatch):
        buyer_api.post("/api/admin/buyer-companies", json=_COMPANY, headers=ADMIN_H)
        recorded = _recording_sender(buyer_api, monkeypatch)
        r = buyer_api.post(f"/api/admin/buyer-companies/{COMPANY_A}/invite-admin",
                           json={"email": "Dana@BayFoods.com"}, headers=ADMIN_H)
        assert r.status_code == 201, r.text
        assert r.json()["member"]["role"] == "ADMIN"
        assert [m.to for m in recorded] == [["dana@bayfoods.com"]]
        token = recorded[0].body.split("/verify?token=", 1)[1].split("\n", 1)[0].strip()
        assert buyer_api.post("/api/buyer/auth/verify", json={"token": token}).status_code == 200
        assert buyer_api.get("/api/buyer/me").json()["member"]["role"] == "ADMIN"
        rows = buyer_api._ba.list_audit(company_id=COMPANY_A, event="member_invited")
        assert rows[0]["actor"] == "admin" and rows[0]["detail"]["via"] == "gofer_admin"
        # The link is only in the mailbox: never in the response, never in the audit.
        assert token not in r.text and token not in repr(buyer_api._ba.list_audit())

    def test_invite_admin_to_unknown_company_is_404(self, buyer_api):
        r = buyer_api.post("/api/admin/buyer-companies/company-nope/invite-admin",
                           json={"email": "x@nope.com"}, headers=ADMIN_H)
        assert r.status_code == 404


@pytest.fixture
def company(buyer_api):
    ba = buyer_api._ba
    two_companies(buyer_api)
    m = {
        "admin": make_member(buyer_api, "admin@bayfoods.com", ba.ROLE_ADMIN),
        "requester": make_member(buyer_api, "req@bayfoods.com", ba.ROLE_REQUESTER),
        "buyer": make_member(buyer_api, "buyer@bayfoods.com", ba.ROLE_BUYER),
        "approver": make_member(buyer_api, "appr@bayfoods.com", ba.ROLE_APPROVER),
        "b_admin": make_member(buyer_api, "admin@northgate.com", ba.ROLE_ADMIN, cid=COMPANY_B),
    }
    c = {k: new_client_for(buyer_api, v) for k, v in m.items()}
    return {"m": m, "c": c, "ba": ba}


class TestOnlyAdminManagesMembers:
    @pytest.mark.parametrize("who", ["requester", "buyer", "approver"])
    def test_non_admins_get_403_on_every_member_route(self, company, who):
        c, target = company["c"][who], company["m"]["requester"]["id"]
        calls = [
            c.get("/api/buyer/members"),
            c.post("/api/buyer/members", json={"email": "new@bayfoods.com"}, headers=H),
            c.post(f"/api/buyer/members/{target}/role", json={"role": "BUYER"}, headers=H),
            c.post(f"/api/buyer/members/{target}/revoke", headers=H),
        ]
        assert [r.status_code for r in calls] == [403, 403, 403, 403]
        assert company["ba"].get_member_by_email("new@bayfoods.com") is None

    def test_admin_lists_only_their_company(self, company):
        emails = {m["email"] for m in company["c"]["admin"].get("/api/buyer/members").json()["members"]}
        assert "admin@northgate.com" not in emails and "req@bayfoods.com" in emails


class TestInviteChangeRevoke:
    def test_invite_defaults_to_requester_and_is_audited(self, company, monkeypatch):
        recorded = _recording_sender(company["c"]["admin"], monkeypatch)
        r = company["c"]["admin"].post("/api/buyer/members", json={"email": "new@bayfoods.com"},
                                       headers=H)
        assert r.status_code == 201, r.text
        assert r.json()["member"]["role"] == "REQUESTER"
        assert [m.to for m in recorded] == [["new@bayfoods.com"]]
        row = company["ba"].list_audit(event="member_invited")[-1]
        assert row["actor"] == company["m"]["admin"]["id"]
        assert row["detail"]["role"] == "REQUESTER"

    def test_invite_with_a_chosen_role(self, company):
        r = company["c"]["admin"].post("/api/buyer/members",
                                       json={"email": "b2@bayfoods.com", "role": "APPROVER"},
                                       headers=H)
        assert r.json()["member"]["role"] == "APPROVER"

    def test_invite_conflicts(self, company):
        a = company["c"]["admin"]
        assert a.post("/api/buyer/members", json={"email": "req@bayfoods.com"},
                      headers=H).status_code == 409
        # One company per member: someone in company B cannot be invited into A.
        assert a.post("/api/buyer/members", json={"email": "admin@northgate.com"},
                      headers=H).status_code == 409
        assert a.post("/api/buyer/members", json={"email": "x@y.com", "role": "OWNER"},
                      headers=H).status_code == 422

    def test_change_role_is_audited_with_old_and_new(self, company):
        target = company["m"]["requester"]["id"]
        r = company["c"]["admin"].post(f"/api/buyer/members/{target}/role",
                                       json={"role": "BUYER"}, headers=H)
        assert r.status_code == 200 and r.json()["member"]["role"] == "BUYER"
        row = company["ba"].list_audit(event="member_role_changed")[-1]
        assert row["detail"] == {"old": "REQUESTER", "new": "BUYER"}
        assert row["actor"] == company["m"]["admin"]["id"]

    def test_revoke_kills_the_members_session_and_is_audited(self, company):
        target_client = company["c"]["buyer"]
        assert target_client.get("/api/buyer/me").status_code == 200
        r = company["c"]["admin"].post(f"/api/buyer/members/{company['m']['buyer']['id']}/revoke",
                                       headers=H)
        assert r.status_code == 200 and r.json()["member"]["status"] == "REVOKED"
        assert target_client.get("/api/buyer/me").status_code == 401
        assert company["ba"].list_audit(event="member_revoked")[-1]["actor"] == \
            company["m"]["admin"]["id"]

    def test_last_admin_cannot_demote_or_revoke_themselves(self, company):
        a, me = company["c"]["admin"], company["m"]["admin"]["id"]
        demote = a.post(f"/api/buyer/members/{me}/role", json={"role": "BUYER"}, headers=H)
        revoke = a.post(f"/api/buyer/members/{me}/revoke", headers=H)
        assert demote.status_code == 409 and revoke.status_code == 409
        assert company["ba"].get_member(me)["role"] == "ADMIN"
        assert a.get("/api/buyer/me").status_code == 200

    def test_with_a_second_admin_the_first_may_step_down(self, company):
        a = company["c"]["admin"]
        a.post(f"/api/buyer/members/{company['m']['approver']['id']}/role",
               json={"role": "ADMIN"}, headers=H)
        me = company["m"]["admin"]["id"]
        assert a.post(f"/api/buyer/members/{me}/role", json={"role": "BUYER"},
                      headers=H).status_code == 200

    def test_cross_company_member_is_indistinguishable_from_missing(self, company):
        a, foreign = company["c"]["admin"], company["m"]["b_admin"]["id"]
        for suffix, body in (("role", {"role": "BUYER"}), ("revoke", None)):
            f = a.post(f"/api/buyer/members/{foreign}/{suffix}", json=body, headers=H)
            m = a.post(f"/api/buyer/members/no-such-member/{suffix}", json=body, headers=H)
            assert f.status_code == 404
            assert (f.status_code, f.content) == (m.status_code, m.content)
        assert company["ba"].get_member(foreign)["status"] == "ACTIVE"

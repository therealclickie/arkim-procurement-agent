"""
Arc 6 T7 — approval policy settings (D5): stored, Admin-only, audited; NOT
enforced at order time (arc 7).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    COMPANY_A, COMPANY_B, FACILITY_A, buyer_api, make_member, new_client_for, origin_headers,
    two_companies,
)

H = origin_headers()
URL = "/api/buyer/settings/approval-policy"


@pytest.fixture
def co(buyer_api):
    ba = buyer_api._ba
    two_companies(buyer_api)
    m = {
        "admin": make_member(buyer_api, "admin@bayfoods.com", ba.ROLE_ADMIN),
        "requester": make_member(buyer_api, "req@bayfoods.com", ba.ROLE_REQUESTER),
        "buyer": make_member(buyer_api, "buyer@bayfoods.com", ba.ROLE_BUYER),
        "approver": make_member(buyer_api, "appr@bayfoods.com", ba.ROLE_APPROVER),
        "b_admin": make_member(buyer_api, "admin@northgate.com", ba.ROLE_ADMIN, cid=COMPANY_B),
    }
    return {"m": m, "c": {k: new_client_for(buyer_api, v) for k, v in m.items()},
            "ba": ba, "api": buyer_api._api_server}


def _policy(co, who="admin"):
    return co["c"][who].get("/api/buyer/settings").json()["approval_policy"]


class TestDefaultsAndReads:
    def test_defaults_are_2500_and_override_on(self, co):
        assert _policy(co) == {"auto_approval_limit": 2500.0, "allow_admin_override": True,
                               "currency": "USD"}

    @pytest.mark.parametrize("who", ["requester", "buyer", "approver"])
    def test_settings_are_admin_only(self, co, who):
        assert co["c"][who].get("/api/buyer/settings").status_code == 403


class TestChanges:
    @pytest.mark.parametrize("who", ["requester", "buyer", "approver"])
    def test_non_admin_gets_403_on_change(self, co, who):
        for body in ({"auto_approval_limit": 10}, {"allow_admin_override": False}):
            r = co["c"][who].put(URL, json=body, headers=H)
            assert r.status_code == 403, (who, body)
        assert _policy(co) == {"auto_approval_limit": 2500.0, "allow_admin_override": True,
                               "currency": "USD"}
        assert co["ba"].list_audit(event="approval_policy_changed") == []

    def test_each_change_writes_old_new_and_actor(self, co):
        a, me = co["c"]["admin"], co["m"]["admin"]["id"]
        assert a.put(URL, json={"auto_approval_limit": 5000}, headers=H).status_code == 200
        assert a.put(URL, json={"allow_admin_override": False}, headers=H).status_code == 200
        assert a.put(URL, json={"auto_approval_limit": 7500.5}, headers=H).status_code == 200
        rows = co["ba"].list_audit(company_id=COMPANY_A, event="approval_policy_changed")
        assert [r["detail"] for r in rows] == [
            {"field": "auto_approval_limit", "old": 2500.0, "new": 5000.0},
            {"field": "allow_admin_override", "old": True, "new": False},
            {"field": "auto_approval_limit", "old": 5000.0, "new": 7500.5},
        ]
        assert {r["actor"] for r in rows} == {me}
        assert all(r["created_at"] for r in rows)
        assert _policy(co)["auto_approval_limit"] == 7500.5
        assert _policy(co)["allow_admin_override"] is False

    def test_zero_is_accepted(self, co):
        r = co["c"]["admin"].put(URL, json={"auto_approval_limit": 0}, headers=H)
        assert r.status_code == 200
        assert r.json()["approval_policy"]["auto_approval_limit"] == 0.0

    @pytest.mark.parametrize("bad", [-1, -0.5, "abc", "2500", True, [2500], {"v": 1}, 1e309])
    def test_negative_or_non_numeric_is_rejected(self, co, bad):
        payload = json.dumps({"auto_approval_limit": bad}) if bad != 1e309 else \
            '{"auto_approval_limit": 1e309}'
        r = co["c"]["admin"].put(URL, content=payload,
                                 headers={**H, "Content-Type": "application/json"})
        assert r.status_code == 422, (bad, r.status_code, r.text)
        assert _policy(co)["auto_approval_limit"] == 2500.0

    def test_non_boolean_override_and_empty_change_are_rejected(self, co):
        a = co["c"]["admin"]
        assert a.put(URL, json={"allow_admin_override": "no"}, headers=H).status_code == 422
        assert a.put(URL, json={}, headers=H).status_code == 422

    def test_a_change_needs_a_same_origin_request(self, co):
        r = co["c"]["admin"].put(URL, json={"auto_approval_limit": 1},
                                 headers={"Origin": "https://evil.example.com"})
        assert r.status_code == 401
        assert _policy(co)["auto_approval_limit"] == 2500.0

    def test_changes_are_company_scoped(self, co):
        co["c"]["admin"].put(URL, json={"auto_approval_limit": 1}, headers=H)
        assert _policy(co, "b_admin")["auto_approval_limit"] == 2500.0


class TestNotEnforcedYet:
    def test_order_routing_ignores_the_stored_limit(self, co):
        # D5: stored here, enforced in arc 7. A $10 selection routes exactly as
        # the facility approval_rules say, whatever the company limit is.
        from utils.procurement_agent.state.persistence import SourcingRunORM
        co["c"]["admin"].put(URL, json={"auto_approval_limit": 0}, headers=H)
        now = datetime.now(timezone.utc)
        with co["api"]._SessionFactory() as s:
            s.add(SourcingRunORM(
                id="run-a", facility_id=FACILITY_A, company_id=COMPANY_A,
                current_phase="comparison", urgency_factor=0.3, warranty_status="unknown",
                sourcing_results_json=json.dumps({"tier_1": {"results": [
                    {"vendor_name": "Vend", "base_price": 10.0}]}}),
                approval_history_json="[]", initiated_at=now, updated_at=now))
            s.commit()
        from utils.procurement_agent.state.approval_rules import determine_approval_path
        expected, _ = determine_approval_path(FACILITY_A, 10.0)
        r = co["c"]["buyer"].post("/api/runs/run-a/select-candidate", headers=H,
                                  json={"candidate_id": "Vend-t1-0", "tier": 1})
        assert r.status_code == 200
        from utils.procurement_agent.state import persistence
        path = persistence.get_run("run-a")["selected_candidate_json"]["_approval_path"]
        assert path["approvers_required"] == expected

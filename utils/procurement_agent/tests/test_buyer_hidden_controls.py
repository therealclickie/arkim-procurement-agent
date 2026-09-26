"""
Arc 6 T8 — a control the UI hides is still refused by the SERVER.

The frontend hides select/order/approve from a Requester and the Team,
Approval policy and Delivery settings screens from a non-Admin. Hiding is
display only; this file calls every endpoint behind a hidden control directly,
with a valid session of a role that lacks the capability, on the member's OWN
company's resources — and requires 403 each time, with nothing changed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    COMPANY_A, FACILITY_A, buyer_api, make_company, make_member, new_client_for,
    origin_headers,
)

H = origin_headers()
RUN = "run-own"


@pytest.fixture
def own(buyer_api):
    from utils import supplier_registry
    from utils.procurement_agent.state import persistence
    from utils.procurement_agent.state.persistence import SourcingRunORM
    ba = buyer_api._ba
    make_company(buyer_api)
    now = datetime.now(timezone.utc)
    with buyer_api._api_server._SessionFactory() as s:
        s.add(SourcingRunORM(
            id=RUN, facility_id=FACILITY_A, company_id=COMPANY_A,
            current_phase="pending_first_approval", urgency_factor=0.3,
            warranty_status="unknown",
            sourcing_results_json=json.dumps({"tier_1": {"results": [
                {"vendor_name": "Vend", "base_price": 10.0}]}}),
            selected_candidate_json=json.dumps({"candidate_id": "Vend-t1-0", "tier": 1}),
            approval_history_json="[]", initiated_at=now, updated_at=now))
        s.commit()
    draft = persistence.create_draft(run_id=RUN, candidate_id="Vend-t1-0",
                                     candidate_snapshot={"vendor_name": "Vend"}, draft_body="x")
    item = supplier_registry.record_review_item(
        "quote", {"unit_price": 9.0}, status="confirmed", run_id=RUN, vendor_name="Vend")
    clients = {
        role: new_client_for(buyer_api, make_member(
            buyer_api, f"{role.lower()}@bayfoods.com", role))
        for role in (ba.ROLE_REQUESTER, ba.ROLE_BUYER, ba.ROLE_APPROVER)
    }
    return {"c": clients, "draft": draft["id"], "item": item, "ba": ba}


_APPROVE = {"approver_name": "x", "approver_role": "y"}

# Endpoints behind the select / order / approve controls a Requester never sees.
REQUESTER_HIDDEN = [
    ("POST", f"/api/runs/{RUN}/select-candidate", {"candidate_id": "Vend-t1-0", "tier": 1}),
    ("POST", f"/api/runs/{RUN}/order-now", {"candidate_id": "Vend-t1-0", "tier": 1}),
    ("POST", f"/api/runs/{RUN}/approve", _APPROVE),
    ("POST", f"/api/runs/{RUN}/reject", {**_APPROVE, "notes": "n"}),
    ("POST", f"/api/runs/{RUN}/execute", None),
    ("POST", f"/api/runs/{RUN}/rfq-draft", {"candidate_id": "Vend-t1-0", "tier": 1}),
    ("POST", f"/api/runs/{RUN}/outreach", {"candidate_ids": ["x"]}),
    ("POST", "/api/rfq-drafts/{draft}/approve", {"approved_by": "x"}),
    ("POST", "/api/review-items/{item}/place-order", None),
]

# Endpoints behind the Admin-only screens (Team, Approval policy, Delivery settings).
ADMIN_HIDDEN = [
    ("GET", "/api/buyer/members", None),
    ("POST", "/api/buyer/members", {"email": "new@bayfoods.com"}),
    ("GET", "/api/buyer/settings", None),
    ("PUT", "/api/buyer/settings/approval-policy", {"auto_approval_limit": 1}),
    ("PUT", "/api/sites/lamirada/ship-to", {"address": "x"}),
    ("POST", "/api/approval-rules", {"facility_id": FACILITY_A, "threshold": 0,
                                     "approvers_required": 0}),
]


def _fill(path, own):
    return path.replace("{draft}", own["draft"]).replace("{item}", own["item"])


def _snapshot(own):
    from utils import orders
    from utils.procurement_agent.state import persistence
    run = persistence.get_run(RUN)
    return (run["current_phase"], json.dumps(run["approval_history_json"]),
            persistence.get_draft(own["draft"])["status"], len(orders.get_orders()),
            len(own["ba"].list_members(COMPANY_A)))


class TestHiddenControlsAreRefusedByTheServer:
    @pytest.mark.parametrize("method,path,body", REQUESTER_HIDDEN,
                             ids=[p for _, p, _ in REQUESTER_HIDDEN])
    def test_requester_direct_call_is_403(self, own, method, path, body):
        before = _snapshot(own)
        r = own["c"]["REQUESTER"].request(method, _fill(path, own), json=body, headers=H)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json() == {"detail": "Forbidden"}
        assert _snapshot(own) == before

    @pytest.mark.parametrize("role", ["REQUESTER", "BUYER", "APPROVER"])
    @pytest.mark.parametrize("method,path,body", ADMIN_HIDDEN, ids=[p for _, p, _ in ADMIN_HIDDEN])
    def test_non_admin_direct_call_is_403(self, own, role, method, path, body):
        before = _snapshot(own)
        r = own["c"][role].request(method, path, json=body, headers=H)
        assert r.status_code == 403, (role, path, r.status_code, r.text)
        assert _snapshot(own) == before

    def test_the_requester_can_still_do_what_the_matrix_allows(self, own):
        c = own["c"]["REQUESTER"]
        assert c.get(f"/api/runs/{RUN}").status_code == 200
        assert c.post("/api/runs", json={}, headers=H).status_code == 201

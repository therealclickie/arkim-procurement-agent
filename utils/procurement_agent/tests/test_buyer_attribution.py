"""
Arc 6 T5 — attribution (D7).

Every buyer action in the gate's K4 list records the SESSION member's id —
never a name from the request body. Orders and runs carry ``company_id``. The
bodies below deliberately carry the walk's typed identity ("Dana
Plant-Manager") so each test proves it is ignored.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    COMPANY_A, FACILITY_A, buyer_api, buyer_api_off, make_company, make_member,
    new_client_for, origin_headers,
)
from utils.procurement_agent.tests.test_api_server import (
    _empty_sourcing, _mock_sourcing_pipeline,
)

H = origin_headers()
TYPED = "Dana Plant-Manager"
_APPROVE = {"approver_name": TYPED, "approver_role": "plant manager"}
_REJECT = {**_APPROVE, "notes": "wrong part"}


@pytest.fixture
def team(buyer_api):
    ba = buyer_api._ba
    make_company(buyer_api)
    members = {
        "buyer": make_member(buyer_api, "buyer@bayfoods.com", ba.ROLE_BUYER),
        "approver": make_member(buyer_api, "approver@bayfoods.com", ba.ROLE_APPROVER),
        "approver2": make_member(buyer_api, "approver2@bayfoods.com", ba.ROLE_APPROVER),
        "admin": make_member(buyer_api, "admin@bayfoods.com", ba.ROLE_ADMIN),
    }
    clients = {k: new_client_for(buyer_api, m) for k, m in members.items()}
    return {"m": members, "c": clients, "api": buyer_api._api_server, "ba": ba}


def _run(api, run_id="run-a", *, phase="comparison", selected=None, history=None,
         specs=None, group=None):
    from utils.procurement_agent.state.persistence import SourcingRunORM
    now = datetime.now(timezone.utc)
    with api._SessionFactory() as s:
        s.add(SourcingRunORM(
            id=run_id, facility_id=FACILITY_A, company_id=COMPANY_A, group_id=group,
            current_phase=phase, urgency_factor=0.3, warranty_status="unknown",
            asset_specs_json=json.dumps(specs or {"manufacturer": "SKF", "part_number": "6205"}),
            sourcing_results_json=json.dumps({"tier_1": {"results": [
                {"vendor_name": "Vend", "base_price": 10.0, "source_url": "https://vend.com/p"}]}}),
            selected_candidate_json=json.dumps(selected) if selected else None,
            approval_history_json=json.dumps(history or []),
            initiated_at=now, updated_at=now))
        s.commit()
    return run_id


def _get(run_id):
    from utils.procurement_agent.state import persistence
    return persistence.get_run(run_id)


def _rules(approvers):
    from utils.procurement_agent.state import persistence
    persistence.upsert_approval_rule(facility_id=FACILITY_A, threshold_usd=0,
                                     approvers_required=approvers, approver_roles=[])


class TestRunCreation:
    def test_create_run_records_the_session_member_and_company(self, team):
        r = team["c"]["buyer"].post("/api/runs", headers=H,
                                    json={"initiated_by_user_id": "mallory"})
        run = _get(r.json()["id"])
        assert run["initiated_by_user_id"] == team["m"]["buyer"]["id"]
        assert run["company_id"] == COMPANY_A

    def test_fan_out_records_the_member_on_every_run(self, team):
        r = team["c"]["buyer"].post("/api/requests", headers=H, json={"parts": [{}, {}]})
        for rid in r.json()["run_ids"]:
            assert _get(rid)["initiated_by_user_id"] == team["m"]["buyer"]["id"]

    def test_channel_runs_carry_no_member_and_a_channel_marker(self, team, monkeypatch):
        _mock_sourcing_pipeline(monkeypatch, sourcing_result=_empty_sourcing())
        rid = team["api"]._fire_sourcing_run_for_intake(
            {"manufacturer": "SKF", "part_number": "6205", "detected_type": "bearing"},
            "bayfoods", channel="email")
        assert rid
        run = _get(rid)
        assert run["initiated_by_user_id"] is None and run["company_id"] == COMPANY_A
        rows = team["ba"].list_audit(run_id=rid, event="action:create_run")
        assert rows[0]["detail"] == {"channel": "email", "acting_member": None}
        assert rows[0]["member_id"] is None


class TestConfirmAndOverride:
    def test_source_anyway_records_acknowledged_by(self, team, monkeypatch):
        _mock_sourcing_pipeline(monkeypatch, sourcing_result=_empty_sourcing())
        rid = _run(team["api"], phase="intake",
                   specs={"manufacturer": None, "model": None, "part_number": None,
                          "detected_type": "ball valve", "connection_size": "2 inch"})
        client = team["c"]["buyer"]
        assert client.post(f"/api/runs/{rid}/confirm-intake", headers=H).status_code == 422
        r = client.post(f"/api/runs/{rid}/confirm-intake?source_anyway=true", headers=H)
        assert r.status_code == 200, r.text
        ack = _get(rid)["asset_specs_json"]["spec_incomplete_ack"]
        assert ack["acknowledged_by"] == team["m"]["buyer"]["id"]
        rows = team["ba"].list_audit(run_id=rid, event="action:confirm_intake")
        assert rows[0]["member_id"] == team["m"]["buyer"]["id"]
        assert rows[0]["detail"] == {"source_anyway": True}

    def test_hygienic_override_records_acknowledged_by(self, team, monkeypatch):
        from utils.procurement_agent.tests.test_hygienic_context import S3_GAUGE
        _mock_sourcing_pipeline(monkeypatch, sourcing_result=_empty_sourcing())
        rid = _run(team["api"], phase="intake", specs=S3_GAUGE)
        r = team["c"]["buyer"].post(f"/api/runs/{rid}/confirm-intake?source_anyway=true",
                                    headers=H)
        assert r.status_code == 200, r.text
        from utils import intake_readiness
        ack = _get(rid)["asset_specs_json"][intake_readiness.HYGIENIC_OVERRIDE_ACK]
        assert ack["acknowledged_by"] == team["m"]["buyer"]["id"]

    def test_hygienic_ack_shape_is_unchanged_without_a_member(self):
        from utils import intake_readiness
        from utils.procurement_agent.tests.test_hygienic_context import S3_GAUGE
        specs = dict(S3_GAUGE)
        block = intake_readiness.assess(specs).hygienic
        intake_readiness.record_hygienic_override(specs, block, at="t")
        assert "acknowledged_by" not in specs[intake_readiness.HYGIENIC_OVERRIDE_ACK]


class TestSelectApproveReject:
    def test_select_records_the_member(self, team):
        rid = _run(team["api"])
        r = team["c"]["buyer"].post(f"/api/runs/{rid}/select-candidate", headers=H,
                                    json={"candidate_id": "Vend-t1-0", "tier": 1})
        assert r.status_code == 200, r.text
        assert _get(rid)["selected_candidate_json"]["selected_by"] == team["m"]["buyer"]["id"]

    def test_approve_records_member_id_and_ignores_typed_name(self, team):
        rid = _run(team["api"], phase="pending_first_approval",
                   selected={"candidate_id": "Vend-t1-0", "tier": 1,
                             "_approval_path": {"approvers_required": 1}})
        r = team["c"]["approver"].post(f"/api/runs/{rid}/approve", headers=H, json=_APPROVE)
        assert r.status_code == 200, r.text
        entry = _get(rid)["approval_history_json"][-1]
        assert entry["approver_id"] == team["m"]["approver"]["id"]
        assert entry["approver_name"] == "approver@bayfoods.com"
        assert entry["approver_role"] == "APPROVER"
        assert TYPED not in json.dumps(_get(rid)["approval_history_json"])

    def test_m1_compares_member_ids(self, team):
        rid = _run(team["api"], phase="pending_first_approval",
                   selected={"candidate_id": "Vend-t1-0", "tier": 1,
                             "_approval_path": {"approvers_required": 2}})
        a = team["c"]["approver"]
        assert a.post(f"/api/runs/{rid}/approve", headers=H, json=_APPROVE).json()["phase"] \
            == "pending_second_approval"
        again = a.post(f"/api/runs/{rid}/approve", headers=H, json=_APPROVE)
        assert again.status_code == 409
        second = team["c"]["approver2"].post(f"/api/runs/{rid}/approve", headers=H, json=_APPROVE)
        assert second.json()["phase"] == "approved"

    def test_reject_records_member_id(self, team):
        rid = _run(team["api"], phase="pending_first_approval",
                   selected={"candidate_id": "Vend-t1-0", "tier": 1})
        r = team["c"]["approver"].post(f"/api/runs/{rid}/reject", headers=H, json=_REJECT)
        assert r.status_code == 200, r.text
        entry = _get(rid)["approval_history_json"][-1]
        assert entry["approver_id"] == team["m"]["approver"]["id"]
        assert entry["approver_name"] == "approver@bayfoods.com"

    def test_basket_approve_and_reject_record_member_ids(self, team):
        for rid in ("g-1", "g-2"):
            _run(team["api"], rid, phase="pending_first_approval", group="grp-a",
                 selected={"candidate_id": "Vend-t1-0", "tier": 1})
        _rules(1)
        r = team["c"]["approver"].post("/api/groups/grp-a/approve", headers=H, json=_APPROVE)
        assert r.status_code == 200, r.text
        from utils.procurement_agent.state.persistence import RequestGroupApprovalORM
        with team["api"]._SessionFactory() as s:
            rec = s.query(RequestGroupApprovalORM).filter_by(group_id="grp-a").first()
            received = json.loads(rec.approvals_received_json)
        assert received[0]["approver_id"] == team["m"]["approver"]["id"]
        assert received[0]["approver_name"] == "approver@bayfoods.com"
        for rid in ("h-1",):
            _run(team["api"], rid, phase="pending_first_approval", group="grp-b2",
                 selected={"candidate_id": "Vend-t1-0", "tier": 1})
        team["c"]["approver"].post("/api/groups/grp-b2/reject", headers=H, json=_REJECT)
        entry = _get("h-1")["approval_history_json"][-1]
        assert entry["approver_id"] == team["m"]["approver"]["id"]
        assert TYPED not in json.dumps(entry)


class TestOrders:
    def test_execute_places_as_the_member_not_the_typed_approver(self, team):
        rid = _run(team["api"], phase="approved",
                   selected={"candidate_id": "Vend-t1-0", "tier": 1},
                   history=[{"action": "approved", "approver_name": TYPED}])
        r = team["c"]["buyer"].post(f"/api/runs/{rid}/execute", headers=H)
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert order["placed_by"] == team["m"]["buyer"]["id"]
        assert order["company_id"] == COMPANY_A

    def test_order_now_records_the_member_at_capture(self, team):
        _rules(0)
        rid = _run(team["api"])
        r = team["c"]["buyer"].post(f"/api/runs/{rid}/order-now", headers=H,
                                    json={"candidate_id": "Vend-t1-0", "tier": 1})
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert order["placed_by"] == team["m"]["buyer"]["id"]
        assert order["company_id"] == COMPANY_A

    def test_place_order_from_quote_records_the_member(self, team):
        from utils import supplier_registry
        rid = _run(team["api"])
        item = supplier_registry.record_review_item(
            "quote", {"unit_price": 9.0, "quantity": 1}, status="confirmed", run_id=rid,
            vendor_name="Vend", supplier_domain="vend.com", manufacturer="SKF",
            part_number="6205")
        r = team["c"]["buyer"].post(f"/api/review-items/{item}/place-order", headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["order"]["placed_by"] == team["m"]["buyer"]["id"]
        assert r.json()["order"]["company_id"] == COMPANY_A

    def test_rfq_draft_approve_and_reject_record_the_member(self, team):
        from utils.procurement_agent.state import persistence
        rid = _run(team["api"])
        d1 = persistence.create_draft(run_id=rid, candidate_id="Vend-t1-0",
                                      candidate_snapshot={"vendor_name": "Vend"}, draft_body="x")
        d2 = persistence.create_draft(run_id=rid, candidate_id="Vend-t1-0",
                                      candidate_snapshot={"vendor_name": "Vend"}, draft_body="x")
        c = team["c"]["buyer"]
        a = c.post(f"/api/rfq-drafts/{d1['id']}/approve", headers=H, json={"approved_by": TYPED})
        j = c.post(f"/api/rfq-drafts/{d2['id']}/reject", headers=H, json={"rejected_by": TYPED})
        assert a.json()["approved_by"] == team["m"]["buyer"]["id"]
        assert j.json()["rejected_by"] == team["m"]["buyer"]["id"]


class TestFlagOffAttributionUnchanged:
    def test_typed_names_still_flow_with_the_flag_off(self, buyer_api_off):
        # D8: flag off, today's behaviour — the typed approver name is recorded.
        api = buyer_api_off._api_server
        rid = _run(api, phase="pending_first_approval",
                   selected={"candidate_id": "Vend-t1-0", "tier": 1})
        r = buyer_api_off.post(f"/api/runs/{rid}/approve", json=_APPROVE)
        assert r.status_code == 200
        entry = _get(rid)["approval_history_json"][-1]
        assert entry["approver_name"] == TYPED and entry["approver_id"] is None
        assert _get(rid)["initiated_by_user_id"] is None

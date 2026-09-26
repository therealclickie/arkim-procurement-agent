"""
Arc 6 T4 — company isolation, proven PER ENDPOINT (D6).

For every buyer-facing endpoint in the gate's K2 list (B1–B45), a member of
company A reaching for company B's resource gets exactly what a genuinely
missing resource gets — same status, same body — and B's data is untouched.
List and aggregate endpoints are proven by what they omit. The structural
guard (test_buyer_route_guard.py) proves no route lacks the door; this file
proves each door actually scopes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from utils.procurement_agent.tests._buyer_fixtures import (  # noqa: F401
    COMPANY_A, COMPANY_B, FACILITY_A, FACILITY_B, buyer_api, make_member, new_client_for,
    origin_headers, two_companies,
)

MISSING = "does-not-exist"
RUN_B, RUN_A, GROUP_B = "run-company-b", "run-company-a", "grp-company-b"
H = origin_headers()


def _run(api, run_id, company, facility, *, group=None, phase="pending_first_approval"):
    from utils.procurement_agent.state.persistence import SourcingRunORM
    now = datetime.now(timezone.utc)
    run = SourcingRunORM(
        id=run_id, facility_id=facility, company_id=company, group_id=group,
        current_phase=phase, urgency_factor=0.3, warranty_status="unknown",
        asset_specs_json=json.dumps({"manufacturer": "SKF", "part_number": "6205"}),
        sourcing_results_json=json.dumps({"tier_1": {"results": [
            {"vendor_name": "Vend", "base_price": 10.0, "source_url": "https://vend.com/p"}]}}),
        approval_history_json="[]", initiated_at=now, updated_at=now,
    )
    with api._SessionFactory() as s:
        s.add(run)
        s.commit()


def _order(run_id, company, placed=True):
    from utils import orders
    o = orders.create_order({"run_id": run_id, "manufacturer": "SKF", "part_number": "6205",
                             "vendor_name": "Vend", "unit_price": 10.0}, company_id=company)
    if placed:
        orders.place_order(o["id"], placed_by="seed")
    return o


@pytest.fixture
def world(buyer_api):
    """Company A (Bay Foods) and company B (Northgate), each with an Admin, and
    a full set of B resources: a run in a basket, a draft, a confirmed quote
    item, placed orders, a ship-to and approval rules."""
    from utils import site_settings, supplier_registry
    from utils.procurement_agent.state import persistence
    ba = buyer_api._ba
    two_companies(buyer_api)
    admin_a = make_member(buyer_api, "admin@bayfoods.com", ba.ROLE_ADMIN)
    admin_b = make_member(buyer_api, "admin@northgate.com", ba.ROLE_ADMIN, cid=COMPANY_B)
    api = buyer_api._api_server
    _run(api, RUN_B, COMPANY_B, FACILITY_B, group=GROUP_B)
    _run(api, RUN_A, COMPANY_A, FACILITY_A)
    draft_b = persistence.create_draft(run_id=RUN_B, candidate_id="Vend-t1-0",
                                       candidate_snapshot={"vendor_name": "Vend",
                                                           "source_url": "https://vend.com/p"},
                                       draft_body="Please quote.")
    item_b = supplier_registry.record_review_item(
        "quote", {"unit_price": 9.0, "quantity": 1}, status="confirmed", run_id=RUN_B,
        vendor_name="Vend", supplier_domain="vend.com", manufacturer="SKF", part_number="6205")
    _order(RUN_B, COMPANY_B)
    _order(RUN_B, COMPANY_B)
    _order(RUN_A, COMPANY_A)
    site_settings.upsert_shipto("lamirada", {"company": "Northgate Receiving",
                                             "address": "1 B Street"}, company_id=COMPANY_B)
    persistence.upsert_approval_rule(facility_id=FACILITY_B, threshold_usd=0,
                                     approvers_required=3, approver_roles=["b"])
    return {
        "a": new_client_for(buyer_api, admin_a),
        "b": new_client_for(buyer_api, admin_b),
        "api": api, "ba": ba, "draft_b": draft_b["id"], "item_b": item_b,
        "admin_a": admin_a, "base": buyer_api,
    }


def _b_state(world):
    """A snapshot of B's resources — must be identical after A's attempts."""
    from utils import orders, site_settings, supplier_registry
    from utils.procurement_agent.state import persistence
    run = persistence.get_run(RUN_B)
    return (run["current_phase"], json.dumps(run.get("approval_history_json")),
            json.dumps(run.get("selected_candidate_json")),
            persistence.get_draft(world["draft_b"])["status"],
            supplier_registry.get_review_item(world["item_b"])["status"],
            len(orders.get_orders(company_id=COMPANY_B)),
            json.dumps(site_settings.get_shipto("lamirada", company_id=COMPANY_B)),
            json.dumps(persistence.list_approval_rules(FACILITY_B)))


# ---------------------------------------------------------------------------
# Path-scoped endpoints: foreign == missing, byte for byte
# ---------------------------------------------------------------------------

_APPROVE = {"approver_name": "Eve", "approver_role": "admin"}
_REJECT = {**_APPROVE, "notes": "no"}

# (K2 id, method, path with {id}, body, which B resource fills {id})
PATH_CASES = [
    ("B2", "PUT", "/api/runs/{id}/asset-specs", {"asset_specs": {"x": 1}}, "run"),
    ("B6", "GET", "/api/runs/{id}", None, "run"),
    ("B7", "POST", "/api/runs/{id}/open-from-pending", None, "run"),
    ("B8", "POST", "/api/runs/{id}/reject-submission", None, "run"),
    ("B9", "POST", "/api/runs/{id}/messages", {"content": "hello"}, "run"),
    ("B10", "POST", "/api/runs/{id}/upload", None, "run"),
    ("B11", "POST", "/api/runs/{id}/request-confirmation", {"candidate_ids": ["Vend-t1-0"]}, "run"),
    ("B12", "POST", "/api/runs/{id}/select-candidate", {"candidate_id": "Vend-t1-0", "tier": 1}, "run"),
    ("B13", "POST", "/api/runs/{id}/order-now", {"candidate_id": "Vend-t1-0", "tier": 1}, "run"),
    ("B14", "POST", "/api/runs/{id}/approve", _APPROVE, "run"),
    ("B15", "POST", "/api/runs/{id}/reject", _REJECT, "run"),
    ("B16", "POST", "/api/runs/{id}/confirm-intake?source_anyway=true", None, "run"),
    ("B17", "POST", "/api/runs/{id}/outreach", {"candidate_ids": ["x"]}, "run"),
    ("B18", "POST", "/api/runs/{id}/save-outreach", {"candidate_ids": ["x"]}, "run"),
    ("B20", "GET", "/api/approval-rules/{id}", None, "facility"),
    ("B22", "POST", "/api/runs/{id}/execute", None, "run"),
    ("B23", "POST", "/api/runs/{id}/mark-delivered", None, "run"),
    ("B24", "GET", "/api/runs/{id}/orders", None, "run"),
    ("B28", "GET", "/api/groups/{id}", None, "group"),
    ("B29", "POST", "/api/groups/{id}/approve", _APPROVE, "group"),
    ("B30", "POST", "/api/groups/{id}/reject", _REJECT, "group"),
    ("B31", "POST", "/api/runs/{id}/rfq-draft", {"candidate_id": "Vend-t1-0", "tier": 1}, "run"),
    ("B32", "GET", "/api/rfq-drafts/{id}", None, "draft"),
    ("B33", "GET", "/api/runs/{id}/rfq-drafts", None, "run"),
    ("B34", "POST", "/api/rfq-drafts/{id}/approve", {"approved_by": "Eve"}, "draft"),
    ("B35", "POST", "/api/rfq-drafts/{id}/reject", {"rejected_by": "Eve"}, "draft"),
    ("B36", "POST", "/api/rfq-drafts/{id}/send", None, "draft"),
    ("B39", "GET", "/api/runs/{id}/review-items", None, "run"),
    ("B40", "POST", "/api/runs/{id}/process-replies", None, "run"),
    ("B41", "POST", "/api/review-items/{id}/confirm", None, "item"),
    ("B42", "POST", "/api/review-items/{id}/reject", None, "item"),
    ("B43", "POST", "/api/review-items/{id}/place-order", None, "item"),
    ("B44", "GET", "/api/runs/{id}/impact", None, "run"),
]


def _b_id(world, kind):
    return {"run": RUN_B, "group": GROUP_B, "draft": world["draft_b"],
            "item": world["item_b"], "facility": FACILITY_B}[kind]


def _call(client, method, path, body):
    kw = {"headers": H}
    if body is not None:
        kw["json"] = body
    return client.request(method, path, **kw)


class TestForeignIsIndistinguishableFromMissing:
    @pytest.mark.parametrize("kid,method,path,body,kind", PATH_CASES,
                             ids=[c[0] for c in PATH_CASES])
    def test_member_of_a_gets_the_missing_response_for_b(self, world, kid, method, path,
                                                         body, kind):
        before = _b_state(world)
        foreign = _call(world["a"], method, path.replace("{id}", _b_id(world, kind)), body)
        missing = _call(world["a"], method, path.replace("{id}", MISSING), body)
        assert foreign.status_code == 404, (kid, foreign.status_code, foreign.text)
        assert (foreign.status_code, foreign.content) == (missing.status_code, missing.content), kid
        assert _b_state(world) == before, f"{kid} changed company B's data"

    @pytest.mark.parametrize("kid,method,path,body,kind",
                             [c for c in PATH_CASES if c[1] == "GET"],
                             ids=[c[0] for c in PATH_CASES if c[1] == "GET"])
    def test_the_owner_still_reaches_it(self, world, kid, method, path, body, kind):
        # Positive control: the same request from B's own Admin is not a 404.
        r = _call(world["b"], method, path.replace("{id}", _b_id(world, kind)), body)
        assert r.status_code == 200, (kid, r.status_code, r.text)

    def test_a_requester_gets_404_not_403_on_a_foreign_run(self, world):
        # Scope is checked before the matrix, so even a role that lacks the
        # capability learns nothing about another company's ids.
        req = make_member(world["base"], "req@bayfoods.com", world["ba"].ROLE_REQUESTER)
        client = new_client_for(world["base"], req)
        foreign = client.post(f"/api/runs/{RUN_B}/approve", json=_APPROVE, headers=H)
        missing = client.post(f"/api/runs/{MISSING}/approve", json=_APPROVE, headers=H)
        own = client.post(f"/api/runs/{RUN_A}/approve", json=_APPROVE, headers=H)
        assert (foreign.status_code, foreign.content) == (missing.status_code, missing.content)
        assert foreign.status_code == 404
        assert own.status_code == 403

    def test_legacy_null_company_runs_are_invisible_under_the_flag(self, world):
        _run(world["api"], "run-legacy", None, "00000000-0000-0000-0000-000000000000")
        assert world["a"].get("/api/runs/run-legacy").status_code == 404
        assert "run-legacy" not in {r["id"] for r in world["a"].get("/api/runs").json()}


# ---------------------------------------------------------------------------
# Creation (B1, B3): stamped with the session company; no foreign facility or basket
# ---------------------------------------------------------------------------

class TestCreationIsScoped:
    def _company_of(self, world, run_id):
        from utils.procurement_agent.state import persistence
        return persistence.get_run(run_id)["company_id"], persistence.get_run(run_id)["facility_id"]

    def test_b1_create_run_belongs_to_the_session_company(self, world):
        r = world["a"].post("/api/runs", json={}, headers=H)
        assert r.status_code == 201
        assert self._company_of(world, r.json()["id"]) == (COMPANY_A, FACILITY_A)

    def test_b1_foreign_facility_equals_nonexistent_facility(self, world):
        foreign = world["a"].post("/api/runs", json={"facility_id": FACILITY_B}, headers=H)
        missing = world["a"].post("/api/runs", json={"facility_id": "fac-nowhere"}, headers=H)
        assert foreign.status_code == 422
        assert (foreign.status_code, foreign.content) == (missing.status_code, missing.content)

    def test_b1_cannot_join_another_companys_basket(self, world):
        r = world["a"].post("/api/runs", json={"group_id": GROUP_B}, headers=H)
        assert r.status_code == 422
        new_basket = world["a"].post("/api/runs", json={"group_id": "grp-new-a"}, headers=H)
        assert new_basket.status_code == 201
        # B's basket still holds only B's run.
        assert [x["run_id"] for x in world["b"].get(f"/api/groups/{GROUP_B}").json()["runs"]] == [RUN_B]

    def test_b3_single_and_fan_out_requests_belong_to_the_session_company(self, world):
        single = world["a"].post("/api/requests", json={"parts": [{}]}, headers=H)
        assert single.status_code == 201
        assert self._company_of(world, single.json()["id"])[0] == COMPANY_A
        multi = world["a"].post("/api/requests", json={"parts": [{}, {}]}, headers=H)
        assert multi.status_code == 201
        for rid in multi.json()["run_ids"]:
            assert self._company_of(world, rid) == (COMPANY_A, FACILITY_A)

    def test_b3_foreign_facility_equals_nonexistent_facility(self, world):
        foreign = world["a"].post("/api/requests", json={"parts": [{}, {}], "facility_id": FACILITY_B},
                                  headers=H)
        missing = world["a"].post("/api/requests", json={"parts": [{}, {}], "facility_id": "fac-x"},
                                  headers=H)
        assert foreign.status_code == 422
        assert (foreign.status_code, foreign.content) == (missing.status_code, missing.content)


# ---------------------------------------------------------------------------
# Lists and aggregates (B4, B19, B25, B26, B27, B45): proven by omission
# ---------------------------------------------------------------------------

class TestListsAreScoped:
    def test_b4_list_runs(self, world):
        ids = {r["id"] for r in world["a"].get("/api/runs").json()}
        assert RUN_A in ids and RUN_B not in ids
        assert world["a"].get(f"/api/runs?facility_id={FACILITY_B}").json() == []
        assert world["a"].get(f"/api/runs?group_id={GROUP_B}").json() == []

    def test_b19_facilities(self, world):
        assert [f["id"] for f in world["a"].get("/api/facilities").json()] == [FACILITY_A]
        assert [f["id"] for f in world["b"].get("/api/facilities").json()] == [FACILITY_B]

    def test_b21_approval_rule_on_a_foreign_facility_equals_a_nonexistent_one(self, world):
        before = _b_state(world)
        body = {"threshold": 1, "approvers_required": 1}
        foreign = world["a"].post("/api/approval-rules", json={**body, "facility_id": FACILITY_B},
                                  headers=H)
        missing = world["a"].post("/api/approval-rules", json={**body, "facility_id": "fac-x"},
                                  headers=H)
        assert foreign.status_code == 404
        assert (foreign.status_code, foreign.content) == (missing.status_code, missing.content)
        assert _b_state(world) == before
        own = world["a"].post("/api/approval-rules", json={**body, "facility_id": FACILITY_A},
                              headers=H)
        assert own.status_code == 201

    def test_b25_orders(self, world):
        a_runs = {o["run_id"] for o in world["a"].get("/api/orders").json()["orders"]}
        b_runs = {o["run_id"] for o in world["b"].get("/api/orders").json()["orders"]}
        assert a_runs == {RUN_A} and b_runs == {RUN_B}

    def test_b26_reorder(self, world):
        # B bought the part twice (a cadence); A once. B's forecast must not leak.
        assert world["a"].get("/api/reorder").json() == {"count": 0, "reorder": []}
        b = world["b"].get("/api/reorder").json()
        assert b["count"] == 1 and b["reorder"][0]["order_count"] == 2

    def test_b27_events(self, world):
        a_runs = {e["run_id"] for e in world["a"].get("/api/events").json()["events"]}
        b_runs = {e["run_id"] for e in world["b"].get("/api/events").json()["events"]}
        assert RUN_B not in a_runs
        assert RUN_B in b_runs

    def test_b37_b38_ship_to(self, world):
        before = _b_state(world)
        a_view = world["a"].get("/api/sites/lamirada/ship-to").json()
        never = world["a"].get("/api/sites/never-saved/ship-to").json()
        assert a_view["ship_to"] is None and never["ship_to"] is None
        assert world["b"].get("/api/sites/lamirada/ship-to").json()["ship_to"]["address"] == "1 B Street"
        put = world["a"].put("/api/sites/lamirada/ship-to", json={"address": "9 A Road"}, headers=H)
        assert put.status_code == 200 and put.json()["ship_to"]["address"] == "9 A Road"
        assert _b_state(world) == before
        assert world["b"].get("/api/sites/lamirada/ship-to").json()["ship_to"]["address"] == "1 B Street"

    def test_b45_impact(self, world):
        from utils import orders
        a_ids = {o["id"] for o in orders.get_orders(company_id=COMPANY_A)}
        b_ids = {o["id"] for o in orders.get_orders(company_id=COMPANY_B)}

        def ids(resp):
            return {i for m in resp["savings_by_month"] for i in m["order_ids"]}
        assert ids(world["a"].get("/api/impact").json()) == a_ids
        assert ids(world["b"].get("/api/impact").json()) <= b_ids
        assert world["a"].get("/api/impact").json()["counts"]["quotes_read"] == 0

    def test_b44_saving_is_never_measured_against_another_companys_price(self, world):
        # B paid $10 for SKF 6205. A's run has an $8 quote. Unscoped, A would be
        # told it "saved $2" against B's purchase — a leak of B's price.
        from utils import supplier_registry
        supplier_registry.record_review_item(
            "quote", {"unit_price": 8.0, "quantity": 1}, status="confirmed", run_id=RUN_A,
            vendor_name="Other", supplier_domain="other.com", manufacturer="SKF",
            part_number="6205")
        r = world["a"].get(f"/api/runs/{RUN_A}/impact").json()
        assert r["saving_inputs"]["last_paid_price"] is None
        assert r["saving"] is None
        # Contrast: the unscoped (flag-off) computation WOULD have used B's price.
        from utils import impact
        assert impact.gather_run_decision(RUN_A)["saving_inputs"]["last_paid_price"] == 10.0

    def _empty_company_client(self, world):
        ba = world["ba"]
        ba.create_company("company-empty", "Empty Co", facility_ids=["fac-empty"])
        m = ba.add_member("company-empty", "admin@empty.com", role=ba.ROLE_ADMIN)
        return new_client_for(world["base"], m)

"""T4 / ruling R4 (evaluation finding F-15) — confirm is gated on sufficiency.

Fixtures are the evaluation's own S3 evidence, copied verbatim into
``fixtures/eval_s3_sufficiency.json`` from:

  * ``eval/e2e-flags-on:eval/e2e/evidence/s3_step2_confirm_attempt.json``
    — the confirm that returned **200** and the specs it sourced on.
  * ``eval/e2e-flags-on:eval/e2e/evidence/s3_step2_sourced_anyway.json``
    — the run detail afterwards: 1 Tier-2 and 3 Tier-3 priced results.
  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step9_buyer_view.json``
    — the S1 specs (Chesterton / 155), which must still confirm normally.

What the evaluation observed: "The pressure gauge on the CIP skid is reading wrong.
Need a new one." reached sourcing with **no manufacturer, no model and no part
number**, and came back with priced results for a part that was never specified. The
verify pass measured ``family_disambig_block`` returning ``None`` on those specs, so
nothing in the system stopped it.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from utils import badge_integrity, intake_sufficiency

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s3_sufficiency.json").read_text(encoding="utf-8")
)

S3_SPECS = _EVIDENCE["s3_specs_at_sourcing"]
S1_SPECS = _EVIDENCE["s1_specs_that_must_still_confirm"]


# ---------------------------------------------------------------------------
# The floor itself — pure, no HTTP
# ---------------------------------------------------------------------------

class TestTheIdentityFloor:
    def test_the_s3_specs_are_refused(self):
        block = intake_sufficiency.identity_block(S3_SPECS)
        assert block is not None
        assert block.reason == "identity_insufficient"
        assert "manufacturer" in block.missing_fields
        assert "model" in block.missing_fields

    def test_the_s1_specs_clear_it(self):
        """Chesterton / 155 — a manufacturer plus a model."""
        assert S1_SPECS["manufacturer"] == "Chesterton" and S1_SPECS["model"] == "155"
        assert intake_sufficiency.identity_block(S1_SPECS) is None

    def test_a_manufacturer_part_number_clears_it_without_a_model(self):
        assert intake_sufficiency.identity_block(
            {"manufacturer": "SKF", "part_number": "6205-2RS C3"}) is None

    @pytest.mark.parametrize("specs", [
        {"manufacturer": "Goulds"},                               # no model, no PN
        {"model": "3196", "part_number": "84004-28"},             # no manufacturer
        {"manufacturer": "Goulds", "model": "UNKNOWN-PN"},        # a null token
        {"manufacturer": " ", "model": "3196"},                   # whitespace only
        {},
        None,
    ])
    def test_a_half_identified_request_is_refused(self, specs):
        assert intake_sufficiency.identity_block(specs) is not None

    def test_null_tokens_are_not_identity(self):
        """`UNKNOWN-PN`, `N/A` and friends read as absent, as everywhere in intake."""
        for token in ("UNKNOWN-PN", "N/A", "Unknown", "none", ""):
            assert intake_sufficiency.identity_block(
                {"manufacturer": "Goulds", "part_number": token}) is not None

    def test_recording_the_override_marks_the_run_and_keeps_the_acknowledgement(self):
        specs = dict(S3_SPECS)
        block = intake_sufficiency.identity_block(specs)
        intake_sufficiency.record_override(specs, block, acknowledged_by="buyer@example.com")
        assert specs["spec_incomplete"] is True
        ack = specs["spec_incomplete_ack"]
        assert ack["acknowledged"] is True
        assert ack["reason"] == "identity_insufficient"
        assert ack["acknowledged_by"] == "buyer@example.com"
        assert ack["acknowledged_at"]
        assert intake_sufficiency.is_spec_incomplete(specs) is True


# ---------------------------------------------------------------------------
# Through confirm-intake
# ---------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    from utils import site_settings, supplier_registry
    from utils.procurement_agent.state import persistence

    engine = persistence._make_engine(f"sqlite:///{tmp_path / 'api.sqlite'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    persistence.Base.metadata.create_all(engine)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setattr(persistence, "_engine", engine)
    monkeypatch.setattr(persistence, "_SessionFactory", TestSession)
    for mod, name in ((supplier_registry, "supplier_registry"), (site_settings, "site_settings")):
        monkeypatch.setattr(mod, "_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(mod, "_DB_PATH", str(tmp_path / f"{name}.sqlite"))

    import api_server
    monkeypatch.setattr(api_server, "_engine", engine)
    monkeypatch.setattr(api_server, "_SessionFactory", TestSession)
    monkeypatch.setattr(api_server, "_messages", {})
    return TestClient(api_server.app)


def _empty_sourcing():
    return {f"tier_{n}": {"results": [], "count": 0, "status": "ok"} for n in (1, 2, 3)}


def _mock_sourcing(monkeypatch, sourcing_result=None):
    import api_server

    def _fake(run_id, specs_dict, urgency_factor, warranty_status):
        from utils.procurement_agent.state import persistence
        persistence.update_run(run_id, {
            "sourcing_results_json": sourcing_result or _empty_sourcing(),
            "current_phase": "comparison",
        })

    monkeypatch.setattr(api_server, "_run_sourcing_background", _fake)


#: The S2 request, fully specified — manufacturer, model AND part number.
_FULLY_SPECIFIED = {
    "manufacturer": "SKF", "part_number": "6205-2RS C3", "model": "6205-2RS C3",
    "detected_type": "deep groove ball bearing", "bore_diameter": "25mm",
    "category": "Part",
}


def _seed(api, specs):
    rid = api.post("/api/runs", json={}).json()["id"]
    api.put(f"/api/runs/{rid}/asset-specs", json={"asset_specs": specs})
    return rid


class TestConfirmRefusesTheS3Request:
    def test_the_s3_specs_are_returned_to_clarification(self, api, monkeypatch):
        _mock_sourcing(monkeypatch)
        rid = _seed(api, S3_SPECS)
        assert _EVIDENCE["s3_confirm_status_observed"] == 200, (
            "the evidence must still show the 200 this test supersedes")

        resp = api.post(f"/api/runs/{rid}/confirm-intake")
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["reason"] == "identity_insufficient"
        assert detail["override"] == "source_anyway"
        assert "manufacturer" in detail["missing_attrs"]

    def test_the_run_stays_in_intake_and_sources_nothing(self, api, monkeypatch):
        _mock_sourcing(monkeypatch)
        rid = _seed(api, S3_SPECS)
        api.post(f"/api/runs/{rid}/confirm-intake")
        detail = api.get(f"/api/runs/{rid}").json()
        assert detail["phase"] == "intake"
        assert detail["sourcing_results"] is None, (
            "the evaluation got 1 Tier-2 and 3 Tier-3 priced results here")

    def test_the_refusal_names_the_floor_not_the_family_guard(self, api, monkeypatch):
        """F-15's mechanism: family_disambig_block returns None on these specs."""
        from utils.procurement_agent.agents.intake_agent import family_disambig_block
        assert family_disambig_block(S3_SPECS) is None
        _mock_sourcing(monkeypatch)
        rid = _seed(api, S3_SPECS)
        detail = api.post(f"/api/runs/{rid}/confirm-intake").json()["detail"]
        assert detail["reason"] == "identity_insufficient"


class TestTheExplicitOverride:
    def _override(self, api, monkeypatch, sourcing=None):
        _mock_sourcing(monkeypatch, sourcing)
        rid = _seed(api, S3_SPECS)
        resp = api.post(f"/api/runs/{rid}/confirm-intake?source_anyway=true")
        assert resp.status_code == 200
        return rid

    def test_it_starts_sourcing(self, api, monkeypatch):
        rid = self._override(api, monkeypatch)
        assert api.get(f"/api/runs/{rid}").json()["phase"] == "comparison"

    def test_it_records_the_acknowledgement_on_the_run(self, api, monkeypatch):
        rid = self._override(api, monkeypatch)
        specs = api.get(f"/api/runs/{rid}").json()["asset_specs"]
        ack = specs["spec_incomplete_ack"]
        assert ack["acknowledged"] is True
        assert ack["reason"] == "identity_insufficient"
        assert ack["acknowledged_at"]

    def test_the_run_is_marked_spec_incomplete(self, api, monkeypatch):
        rid = self._override(api, monkeypatch)
        specs = api.get(f"/api/runs/{rid}").json()["asset_specs"]
        assert specs["spec_incomplete"] is True

    def test_the_results_carry_the_banner(self, api, monkeypatch):
        rid = self._override(api, monkeypatch)
        results = api.get(f"/api/runs/{rid}").json()["sourcing_results"]
        assert results["specIncomplete"] is True
        assert results["specIncompleteBanner"] == intake_sufficiency.BANNER
        assert "NOT been checked against your requirement" in results["specIncompleteBanner"]

    def test_no_candidate_in_such_a_run_may_be_badged_exact(self, api, monkeypatch):
        """Even a perfect string match: there was no requirement to have matched."""
        sourcing = _empty_sourcing()
        sourcing["tier_2"]["results"] = [{
            "vendor_name": "Instrument Co",
            "source_url": "https://instrumentco.example.com/p/pg-1000",
            "found_part_number": "PG-1000",
            "match_type": "Exact OEM", "pn_match_status": "exact_match",
            "base_price": 214.0, "suitability_score": 80,
        }]
        rid = self._override(api, monkeypatch, sourcing)
        # The override path also stamps a part number onto nothing — force the harder
        # case by writing one back, so the badge gate, not an empty PN, is what denies it.
        from utils.procurement_agent.state import persistence
        specs = persistence.get_run(rid)["asset_specs_json"]
        specs["part_number"] = "PG-1000"
        specs["manufacturer"] = "Instrument Co"
        persistence.update_run(rid, {"asset_specs_json": specs})

        cand = api.get(f"/api/runs/{rid}").json()["sourcing_results"]["tier2"][0]
        assert cand["isExactMatch"] is False
        assert cand["pnMatchLevel"] not in badge_integrity.EXACT_GRADE
        assert cand["pnMatchReason"] == badge_integrity.SPEC_INCOMPLETE_REASON

    def test_a_sufficient_request_is_never_marked_spec_incomplete(self, api, monkeypatch):
        """source_anyway is inert on a request that clears the floor anyway."""
        _mock_sourcing(monkeypatch)
        rid = _seed(api, _FULLY_SPECIFIED)
        assert api.post(
            f"/api/runs/{rid}/confirm-intake?source_anyway=true").status_code == 200
        detail = api.get(f"/api/runs/{rid}").json()
        assert "spec_incomplete" not in detail["asset_specs"]
        assert "specIncomplete" not in detail["sourcing_results"]
        assert "specIncompleteBanner" not in detail["sourcing_results"]


class TestSufficientRequestsConfirmNormally:
    def test_the_s1_path_still_confirms(self, api, monkeypatch):
        """S1 (Chesterton / 155) clears the new floor, and confirms exactly as it
        did in the evaluation — via the pre-existing open_family affordance, which
        is the call the S1 run actually made (its stored specs carry
        ``family_open_commit: true``). The floor adds no new obstacle to it."""
        assert S1_SPECS["family_open_commit"] is True
        assert intake_sufficiency.identity_block(S1_SPECS) is None
        _mock_sourcing(monkeypatch)
        rid = _seed(api, S1_SPECS)
        resp = api.post(f"/api/runs/{rid}/confirm-intake?open_family=true")
        assert resp.status_code == 200
        assert resp.json()["phase"] == "sourcing"
        assert "spec_incomplete" not in api.get(
            f"/api/runs/{rid}").json()["asset_specs"]

    def test_a_fully_specified_request_confirms(self, api, monkeypatch):
        _mock_sourcing(monkeypatch)
        rid = _seed(api, _FULLY_SPECIFIED)
        assert api.post(f"/api/runs/{rid}/confirm-intake").status_code == 200

    def test_the_floor_runs_after_the_family_guard_not_instead_of_it(self, api, monkeypatch):
        """A family-level request still gets the FAMILY refusal, not the floor's."""
        _mock_sourcing(monkeypatch)
        rid = _seed(api, {"manufacturer": "Allen-Bradley", "model": "PowerFlex 40",
                          "part_number": None, "detected_type": "Variable Frequency Drive (VFD)",
                          "_classified_type": "motor_drive", "_variant_disambig_pending": True})
        detail = api.post(f"/api/runs/{rid}/confirm-intake").json()["detail"]
        assert detail["reason"] != "identity_insufficient"

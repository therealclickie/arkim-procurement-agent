"""T5 / ruling R5 (evaluation finding F-16) — hygienic context adds hygienic questions.

The trigger case is the evaluation's own S3 request, whose specs are in
``fixtures/eval_s3_sufficiency.json`` (copied from
``eval/e2e-flags-on:eval/e2e/evidence/s3_step2_confirm_attempt.json``): a pressure
gauge whose ``description`` AND ``use_case`` both say "CIP skid", and whose
extractor reasoning even says *"CIP applications typically require sanitary-grade
gauges, but this cannot be confirmed without more detail"* — and nothing acted on it.

R5 is narrow: a QUESTION-SET addition. No hygienic equivalence logic is tested here
because none was built.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from utils import hygienic_context

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s3_sufficiency.json").read_text(encoding="utf-8")
)

#: The S3 gauge, given the identity the arc-5 floor (R4) requires, so this test
#: exercises the HYGIENIC gate rather than the identity one.
S3_GAUGE = dict(_EVIDENCE["s3_specs_at_sourcing"],
                manufacturer="Ashcroft", model="1009")

#: The same gauge with the CIP context removed, and nothing else changed.
PLAIN_GAUGE = dict(S3_GAUGE,
                   description="Replacement pressure gauge for the hydraulic press",
                   use_case="hydraulic press gauge replacement",
                   confidence_reasoning="Part type identified as a pressure gauge.")


class TestDetection:
    def test_the_s3_gauge_is_hygienic(self):
        assert hygienic_context.is_hygienic(S3_GAUGE) is True
        assert "cip" in hygienic_context.matched_tokens(S3_GAUGE)

    def test_the_same_gauge_without_the_context_is_not(self):
        assert hygienic_context.is_hygienic(PLAIN_GAUGE) is False

    @pytest.mark.parametrize("phrase,token", [
        ("gauge on the CIP skid", "cip"),
        ("SIP line instrument", "sip"),
        ("sanitary pressure transmitter", "sanitary"),
        ("washdown area sensor", "washdown"),
        ("food contact gauge", "food"),
        ("dairy line probe", "dairy"),
        ("beverage filler sensor", "beverage"),
        ("pharma skid transmitter", "pharma"),
        ("3-A certified gauge", "3-a"),
        ("EHEDG compliant fitting", "ehedg"),
        ("tri-clamp gasket", "tri-clamp"),
    ])
    def test_r5s_whole_trigger_vocabulary(self, phrase, token):
        specs = {"description": phrase, "detected_type": "pressure gauge"}
        assert token in hygienic_context.matched_tokens(specs)

    @pytest.mark.parametrize("phrase", [
        "principal bearing on the line",      # 'cip' inside 'principal'
        "heat dissipation sensor",            # 'sip' inside 'dissipation'
        "gauge for the foodstuffs",           # only 'food' as a whole word counts
    ])
    def test_a_substring_is_not_a_hygienic_signal(self, phrase):
        specs = {"description": phrase, "detected_type": "pressure gauge"}
        assert hygienic_context.matched_tokens(specs) == ()

    def test_the_users_turn_text_counts_as_context(self):
        """S3's hygienic token arrived in the user's own message."""
        specs = {"detected_type": "pressure gauge", "description": "new gauge"}
        assert hygienic_context.is_hygienic(specs) is False
        assert hygienic_context.is_hygienic(
            specs, _EVIDENCE["s3_user_message"]) is True


class TestScope:
    """R5 scopes the addition to instruments and fittings."""

    @pytest.mark.parametrize("detected_type", [
        "pressure gauge", "temperature transmitter", "level sensor", "flow meter",
    ])
    def test_instruments_are_in_scope(self, detected_type):
        assert hygienic_context.is_instrument_or_fitting(
            {"detected_type": detected_type}) is True

    @pytest.mark.parametrize("detected_type", [
        "tri-clamp gasket", "sanitary tee", "hose fitting", "ferrule",
    ])
    def test_fittings_are_in_scope(self, detected_type):
        assert hygienic_context.is_instrument_or_fitting(
            {"detected_type": detected_type}) is True

    @pytest.mark.parametrize("detected_type", [
        "centrifugal pump", "electric motor", "mechanical seal",
    ])
    def test_other_classes_are_not(self, detected_type):
        assert hygienic_context.is_instrument_or_fitting(
            {"detected_type": detected_type}) is False

    def test_a_hygienic_pump_gets_no_hygienic_block(self):
        specs = {"detected_type": "centrifugal pump", "manufacturer": "Goulds",
                 "model": "3196", "description": "CIP skid transfer pump"}
        assert hygienic_context.hygienic_block(specs) is None


class TestTheQuestionComesFromTheFieldSet:
    def test_the_required_fields_are_r5s_four(self):
        assert hygienic_context.REQUIRED_FIELDS == (
            "process_connection", "process_connection_size",
            "wetted_material", "hygienic_certification",
        )

    def test_the_question_names_every_missing_field_and_nothing_else(self):
        block = hygienic_context.hygienic_block(S3_GAUGE)
        assert block is not None
        for field in block.missing_fields:
            assert hygienic_context.FIELD_LABELS[field] in block.message

    def test_it_is_composed_from_the_labels_not_free_text(self):
        """Every label in the question is a FIELD_LABELS value, by construction."""
        for field in hygienic_context.REQUIRED_FIELDS:
            q = hygienic_context.question([field])
            assert hygienic_context.FIELD_LABELS[field] in q
        assert hygienic_context.question([]) == ""

    def test_the_certification_question_offers_3a_ehedg_or_none(self):
        assert (hygienic_context.FIELD_LABELS["hygienic_certification"]
                == "hygienic certification (3-A / EHEDG / none)")

    def test_an_answered_field_is_not_re_asked(self):
        answered = dict(S3_GAUGE, process_connection="Tri-Clamp",
                        connection_size='1.5"', wetted_material="316L")
        block = hygienic_context.hygienic_block(answered)
        assert block.missing_fields == ("hygienic_certification",)
        assert "process connection" not in block.message

    def test_all_four_answered_clears_the_block(self):
        answered = dict(S3_GAUGE, process_connection="Tri-Clamp",
                        process_connection_size='1.5"', wetted_material="316L",
                        hygienic_certification="3-A")
        assert hygienic_context.hygienic_block(answered) is None

    def test_a_null_token_does_not_count_as_answered(self):
        answered = dict(S3_GAUGE, process_connection="N/A",
                        process_connection_size="Unknown",
                        wetted_material="", hygienic_certification=None)
        assert hygienic_context.hygienic_block(answered).missing_fields == \
            hygienic_context.REQUIRED_FIELDS


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

    def _fake(run_id, specs_dict, urgency_factor, warranty_status):
        persistence.update_run(run_id, {
            "sourcing_results_json": {
                f"tier_{n}": {"results": [], "count": 0, "status": "ok"} for n in (1, 2, 3)},
            "current_phase": "comparison",
        })

    monkeypatch.setattr(api_server, "_run_sourcing_background", _fake)
    return TestClient(api_server.app)


def _seed(api, specs):
    rid = api.post("/api/runs", json={}).json()["id"]
    api.put(f"/api/runs/{rid}/asset-specs", json={"asset_specs": specs})
    return rid


class TestConfirmAsksBeforeSourcing:
    def test_the_cip_gauge_is_asked_all_four_before_confirm(self, api):
        rid = _seed(api, S3_GAUGE)
        resp = api.post(f"/api/runs/{rid}/confirm-intake")
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["reason"] == "hygienic_spec_incomplete"
        # The S3 specs carry the token in description AND use_case, and the
        # extractor's own confidence_reasoning adds "sanitary"/"hygienic".
        assert "cip" in detail["hygienic_context"]
        assert detail["missing_attrs"] == list(hygienic_context.REQUIRED_FIELDS)
        assert "process connection type" in detail["message"]
        assert "process connection size" in detail["message"]
        assert "wetted material" in detail["message"]
        assert "3-A / EHEDG / none" in detail["message"]

    def test_the_run_does_not_reach_sourcing(self, api):
        rid = _seed(api, S3_GAUGE)
        api.post(f"/api/runs/{rid}/confirm-intake")
        assert api.get(f"/api/runs/{rid}").json()["phase"] == "intake"

    def test_the_same_gauge_with_no_hygienic_context_confirms(self, api):
        rid = _seed(api, PLAIN_GAUGE)
        resp = api.post(f"/api/runs/{rid}/confirm-intake")
        assert resp.status_code == 200, (
            "R5 is narrow — a non-hygienic gauge must be unaffected")
        assert resp.json()["phase"] == "sourcing"

    def test_answering_the_four_lets_it_confirm(self, api):
        rid = _seed(api, dict(S3_GAUGE, process_connection="Tri-Clamp",
                              process_connection_size='1.5"',
                              wetted_material="316L", hygienic_certification="3-A"))
        assert api.post(f"/api/runs/{rid}/confirm-intake").status_code == 200

    def test_the_identity_floor_is_asked_first(self, api):
        """R4's floor runs before R5's questions — identity, then fitment."""
        rid = _seed(api, _EVIDENCE["s3_specs_at_sourcing"])   # no manufacturer/model
        detail = api.post(f"/api/runs/{rid}/confirm-intake").json()["detail"]
        assert detail["reason"] == "identity_insufficient"

    def test_the_explicit_override_still_sources(self, api):
        rid = _seed(api, S3_GAUGE)
        assert api.post(
            f"/api/runs/{rid}/confirm-intake?source_anyway=true").status_code == 200


# ---------------------------------------------------------------------------
# PH-01 — the gate has to be answerable in the chat
# ---------------------------------------------------------------------------

#: The post-hardening evaluation's own step-3 reply, which answered all four fields
#: and still left the run blocked (``eval/e2e-post-hardening``,
#: ``eval/e2e/evidence/s3b_step3_answered.json``).
_ANSWER_TEXT = ("It's on a 1.5 inch Tri-Clamp connection, 316L stainless wetted "
                "parts, 3-A certified sanitary gauge.")

#: What a schema-compliant extractor returns for that reply: the connection TYPE and
#: the certification land in their own fields, the SIZE stays in connection_size.
_ANSWER_EXTRACTION = {
    "manufacturer":            "Ashcroft",
    "model":                   "1032",
    "part_number":             None,
    "category":                "Part",
    "detected_type":           "pressure gauge",
    "description":             ("Ashcroft 1032 sanitary pressure gauge, 1.5\" Tri-Clamp, "
                                "316L wetted parts, 3-A certified, CIP return line"),
    "connection_size":         "1.5 inch",
    "process_connection":      "Tri-Clamp",
    "material_spec":           "316L stainless steel",
    "hygienic_certification":  "3-A",
    "manufacturer_confidence": 95,
    "part_id_confidence":      85,
    "confidence_reasoning":    "Manufacturer and model stated; sanitary fitment answered.",
}


class TestTheGateIsAnswerableInChat:
    """PH-01 — a required field the extractor cannot fill is an un-clearable gate.

    The evaluation answered all four in chat and stayed blocked: the gate named
    ``process_connection`` and ``hygienic_certification``, which existed neither on
    ``AssetSpecs`` nor in the extractor's JSON schema, so the answers landed in
    ``connection_size`` / ``material_spec`` / ``description`` and those two could
    never clear — ``source_anyway`` was the only exit. These tests hold the
    three-way contract (gate field <-> model field <-> extractor key) that broke.
    """

    def test_every_required_field_has_a_real_assetspecs_field(self):
        import dataclasses
        from utils.models import AssetSpecs

        model_fields = {f.name for f in dataclasses.fields(AssetSpecs)}
        for field in hygienic_context.REQUIRED_FIELDS:
            sources = set(hygienic_context._FIELD_SOURCES[field])
            assert sources & model_fields, (
                f"{field} is required by the hygienic gate but no source key "
                f"{sorted(sources)} exists on AssetSpecs — intake cannot store the "
                f"answer, so the gate can only be exited by source_anyway (PH-01)")

    def test_every_required_field_is_an_extractor_key(self):
        from utils.procurement_agent.agents.intake_agent import _EXTRACTION_SYSTEM

        for field in hygienic_context.REQUIRED_FIELDS:
            sources = hygienic_context._FIELD_SOURCES[field]
            assert any(f'"{key}"' in _EXTRACTION_SYSTEM for key in sources), (
                f"{field} is required by the hygienic gate but none of {list(sources)} "
                f"is a key in the intake extractor's JSON schema — the extractor has "
                f"nowhere to put the user's answer (PH-01)")

    def test_answering_all_four_in_chat_clears_the_gate(self, api):
        """The evaluation's step 3, end to end: reply -> extraction -> confirm 200."""
        from unittest.mock import MagicMock, patch

        from utils.models import SourcingRun
        from utils.procurement_agent.agents.intake_agent import IntakeAgent

        prior = dict(S3_GAUGE, model="1032")
        blocked = _seed(api, prior)
        assert api.post(f"/api/runs/{blocked}/confirm-intake").status_code == 422

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"content": [{"text": json.dumps(_ANSWER_EXTRACTION)}]}
        with patch("utils.procurement_agent.agents.intake_agent.requests.post",
                   return_value=resp):
            result = IntakeAgent(anthropic_api_key="test-key").run(
                SourcingRun(asset_specs_json=prior),
                {"text": _ANSWER_TEXT, "images": []})

        specs = result["asset_specs"]
        assert specs["process_connection"] == "Tri-Clamp"
        assert specs["hygienic_certification"] == "3-A"
        assert hygienic_context.missing_fields(specs) == ()

        rid = _seed(api, specs)
        answered = api.post(f"/api/runs/{rid}/confirm-intake")
        assert answered.status_code == 200, answered.json()
        assert answered.json()["phase"] == "sourcing"

    def test_an_explicit_not_required_answers_the_certification(self):
        """The one required field whose honest answer can be a negative."""
        answered = dict(S3_GAUGE, process_connection="Tri-Clamp",
                        connection_size='1.5"', wetted_material="316L",
                        hygienic_certification="not required")
        assert hygienic_context.hygienic_block(answered) is None

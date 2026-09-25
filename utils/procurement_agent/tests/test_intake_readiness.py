"""PH-01 round 3 — one readiness decision for both confirm gates.

Review round 2 (``review_ph01_r2.txt``) found the chat and confirm-intake still made
two decisions for the arc-5 identity floor: a model-only request was told "Specs look
complete" by the chat and refused 422 ``identity_insufficient`` by confirm, and a
no-identity request was told "Sourcing by category — no specific part number or model
is required". ``intake_readiness.assess`` is now the one decision both read.

The panel-equals-gate TABLE lives in ``test_hygienic_context.py`` (it predates this
file); these are the unit and route tests for the readiness module itself.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from utils import hygienic_context, intake_readiness
from utils.procurement_agent.tests.test_hygienic_context import (  # noqa: F401 (fixture)
    S3_GAUGE, _seed, api,
)

_S3B = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s3b_hygienic.json").read_text(encoding="utf-8")
)

#: Clears both gates.
_READY = dict(S3_GAUGE, process_connection="Tri-Clamp", connection_size='1.5"',
              wetted_material="316L", hygienic_certification="3-A")


def _chat(api, rid, text, extraction, monkeypatch):
    """One chat turn through send_message with the extractor returning ``extraction``."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"content": [{"text": json.dumps(extraction)}]}
    with patch("utils.procurement_agent.agents.intake_agent.requests.post",
               return_value=resp):
        sent = api.post(f"/api/runs/{rid}/messages", json={"content": text})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert sent.status_code == 200, sent.text
    return sent.json()["message"]["content"]


def _mock_intake(monkeypatch, result):
    import api_server
    agent = Mock()
    agent.run.return_value = result
    monkeypatch.setattr(api_server, "IntakeAgent", Mock(return_value=agent))


class TestAssess:
    def test_a_fully_answered_request_is_ready(self):
        readiness = intake_readiness.assess(_READY)
        assert readiness.ready
        assert readiness.missing_attrs == ()
        assert readiness.refusal_detail() is None

    def test_identity_is_refused_first_and_every_missing_item_is_listed(self):
        readiness = intake_readiness.assess(dict(S3_GAUGE, manufacturer=None))
        assert not readiness.ready
        detail = readiness.refusal_detail()
        assert detail["reason"] == "identity_insufficient"
        assert detail["all_missing_attrs"] == (
            ["manufacturer"] + list(hygienic_context.REQUIRED_FIELDS))

    def test_hygienic_alone_keeps_the_hygienic_detail(self):
        readiness = intake_readiness.assess(S3_GAUGE)
        detail = readiness.refusal_detail()
        assert detail["reason"] == "hygienic_spec_incomplete"
        assert detail["message"] == readiness.hygienic.message
        assert detail["override"] == "source_anyway"

    def test_the_ask_names_exactly_the_missing_items(self):
        readiness = intake_readiness.assess({"detected_type": "pressure gauge",
                                             "model": "1032"})
        assert readiness.ask() == (
            "Before sourcing I need the manufacturer — without them there is nothing "
            "to match a supplier's listing against.")


class TestCertificationAnswers:
    @pytest.mark.parametrize("said", ["none", "None", "no", "not required",
                                      "not needed", "none required"])
    def test_the_accepted_negatives_answer_it(self, said):
        specs = dict(_READY, hygienic_certification=said)
        assert "hygienic_certification" not in hygienic_context.missing_fields(specs)

    @pytest.mark.parametrize("said", ["N/A", "n/a", "not applicable",
                                      "Not applicable", "unknown"])
    def test_na_and_not_applicable_are_both_unanswered(self, said):
        """Round 2 finding 6: the two spellings land on the SAME side now."""
        specs = dict(_READY, hygienic_certification=said)
        assert "hygienic_certification" in hygienic_context.missing_fields(specs)
        assert hygienic_context.normalise(dict(specs))["hygienic_certification"] == said

    def test_the_question_offers_3a_ehedg_or_none_required(self):
        q = hygienic_context.question(["hygienic_certification"])
        assert "3-A, EHEDG, or none required" in q

    def test_the_choices_are_only_offered_when_the_certification_is_open(self):
        q = hygienic_context.question(["wetted_material"])
        assert "none required" not in q


class TestTheChatReadsTheSameDecision:
    def test_sourcing_by_category_is_gone(self, api, monkeypatch):
        """Round 2 finding 3: the no-model/no-PN reply invited a confirm arc 5 refuses."""
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_intake(monkeypatch, {
            "sufficient": True, "manufacturer_confidence": 90, "part_id_confidence": 80,
            "asset_specs": {"manufacturer": "Ashcroft", "detected_type": "pressure gauge"},
            "confidence_summary": {"proceed_state": "proceed_spec_based"},
        })
        reply = api.post(f"/api/runs/{rid}/messages",
                         json={"content": "Ashcroft gauge"}).json()["message"]["content"]
        assert "Sourcing by category" not in reply
        assert "model or part number" in reply
        detail = api.post(f"/api/runs/{rid}/confirm-intake").json()["detail"]
        assert detail["reason"] == "identity_insufficient"

    def test_a_cap_commit_keeps_its_message_and_asks_for_the_rest(self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_intake(monkeypatch, {
            "sufficient": True, "manufacturer_confidence": 20, "part_id_confidence": 40,
            "asset_specs": {"detected_type": "valve", "spec_based_sourcing": True},
            "commit_message": "I'll search on the specs we have.",
            "confidence_summary": {"proceed_state": "forced_commit"},
        })
        reply = api.post(f"/api/runs/{rid}/messages",
                         json={"content": "no idea"}).json()["message"]["content"]
        assert reply.startswith("I'll search on the specs we have.")
        assert "manufacturer and the model or part number" in reply

    def test_the_nameplate_upload_does_not_say_confirm_when_confirm_refuses(
            self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_intake(monkeypatch, {
            "sufficient": True, "manufacturer_confidence": 90, "part_id_confidence": 90,
            "asset_specs": {"model": "1032", "detected_type": "pressure gauge"},
            "confidence_summary": {"proceed_state": "proceed_full_confidence"},
        })
        reply = api.post(f"/api/runs/{rid}/upload", files={
            "file": ("plate.jpg", b"\xff\xd8fakejpeg", "image/jpeg")}).json()["message"]["content"]
        assert "confirm to start sourcing" not in reply
        assert "manufacturer" in reply


class TestTheSecondHygienicAskNamesSourceAnyway:
    """Round 2 finding 5: the hygienic ask is outside the intake turn cap, so from the
    second ask the chat names the way out."""

    def test_first_ask_is_the_gate_question_second_adds_source_anyway(
            self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        first = _chat(api, rid, _S3B["step3_user_message"], _S3B["step3_specs"],
                      monkeypatch)
        refusal = api.post(f"/api/runs/{rid}/confirm-intake").json()["detail"]
        assert first == refusal["message"]
        assert "Source anyway" not in first

        second = _chat(api, rid, "not sure", {"description": _S3B["step3_specs"]["description"]},
                       monkeypatch)
        assert second.startswith(refusal["message"])
        assert "Source anyway" in second
        third = _chat(api, rid, "still not sure", {}, monkeypatch)
        assert "Source anyway" in third

    def test_the_ask_counter_is_not_shown_in_the_panel(self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _chat(api, rid, _S3B["step3_user_message"], _S3B["step3_specs"], monkeypatch)
        specs = api.get(f"/api/runs/{rid}").json()["asset_specs"]
        assert intake_readiness.HYGIENIC_ASKS_KEY not in specs


class TestSourceAnywayOnTheHygienicGate:
    def test_it_proceeds_and_records_the_acknowledgement(self, api):
        rid = _seed(api, S3_GAUGE)
        resp = api.post(f"/api/runs/{rid}/confirm-intake?source_anyway=true")
        assert resp.status_code == 200
        specs = api.get(f"/api/runs/{rid}").json()["asset_specs"]
        ack = specs[intake_readiness.HYGIENIC_OVERRIDE_ACK]
        assert ack["acknowledged"] is True
        assert ack["reason"] == "hygienic_spec_incomplete"
        assert ack["missing_attrs"] == list(hygienic_context.REQUIRED_FIELDS)
        # Identity was sufficient: the identity banner's claim would be false.
        assert "spec_incomplete" not in specs

    def test_both_gates_record_both_acknowledgements(self, api):
        rid = _seed(api, dict(S3_GAUGE, manufacturer=None))
        assert api.post(
            f"/api/runs/{rid}/confirm-intake?source_anyway=true").status_code == 200
        specs = api.get(f"/api/runs/{rid}").json()["asset_specs"]
        assert specs["spec_incomplete_ack"]["reason"] == "identity_insufficient"
        assert specs[intake_readiness.HYGIENIC_OVERRIDE_ACK]["acknowledged"] is True

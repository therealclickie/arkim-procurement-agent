"""T6 / ruling R6 (evaluation finding F-08) — the guard recognises what it was told.

Fixtures are the evaluation's own S1 evidence, copied verbatim into
``fixtures/eval_s1_variant_guard.json`` from:

  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step9_buyer_view.json``
    — the S1 message thread and the final ``asset_specs``
      (``shaft_size: "1.875 inch"``).
  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step1c_inapp_fallback.json``
    — the agent turn that re-asked for the shaft size.
  * ``eval/e2e-flags-on:eval/e2e/evidence/s1_step1d_confirm_intake.json``
    — the 422: ``missing_attrs: ["shaft_size"]``, ``pending: true``.

What the evaluation observed: the user's second turn said *"Goulds 3196 MTX, 1.875
inch shaft, single cartridge seal…"* in their own words. The agent's next turn asked
for the shaft size again, and confirm-intake then reported ``shaft_size`` MISSING
against specs that held it.

The anti-hallucination rule stays: an attr the EXTRACTOR filled, with no user turn
behind it, still blocks. Only an attr the user actually supplied is treated as
confirmed.
"""

import json
from pathlib import Path

import pytest

from utils.procurement_agent.agents import intake_agent
from utils.procurement_agent.agents.intake_agent import (
    USER_SUPPLIED_ATTRS_KEY, classify_by_units, family_disambig_block,
    record_user_supplied_attrs, user_supplied_variant_attrs,
)

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s1_variant_guard.json").read_text(encoding="utf-8")
)

S1_SPECS = _EVIDENCE["s1_asset_specs"]
S1_SUPPLYING_TURN = _EVIDENCE["user_turn_supplying_the_shaft_size"]
OBSERVED_422 = _EVIDENCE["observed_confirm_422"]["body"]["detail"]


def _seal_specs(**over):
    """The S1 seal, family-level (a model, no part number) — the guard's trigger."""
    specs = {
        "manufacturer": "Chesterton",
        "model": "155",
        "part_number": None,
        "detected_type": "mechanical seal",
        "_classified_type": "mechanical_seal",
        "shaft_size": S1_SPECS["shaft_size"],
    }
    specs.update(over)
    return specs


class TestTheEvidence:
    def test_the_observed_422_named_a_field_the_specs_held(self):
        assert OBSERVED_422["missing_attrs"] == ["shaft_size"]
        assert OBSERVED_422["pending"] is True
        assert S1_SPECS["shaft_size"] == "1.875 inch"

    def test_the_observed_turn_really_supplied_the_shaft_size(self):
        assert "1.875 inch shaft" in S1_SUPPLYING_TURN

    def test_the_observed_reask_asked_for_it_again(self):
        assert "shaft size" in _EVIDENCE["observed_reask"]


class TestProvenance:
    """"Supplied" means the value is in the user's message; "filled" means it is not."""

    def test_the_s1_turn_supplies_the_shaft_size(self):
        assert user_supplied_variant_attrs(
            _seal_specs(), S1_SUPPLYING_TURN) == ["shaft_size"]

    def test_an_extractor_filled_value_with_no_user_turn_behind_it_is_not_supplied(self):
        assert user_supplied_variant_attrs(
            _seal_specs(), "the pump is leaking, need a seal today") == []

    @pytest.mark.parametrize("turn", [
        "1.875 inch shaft", "1.875-inch shaft", "the shaft: 1.875   inch",
        "Goulds 3196 MTX, 1.875 inch shaft, single cartridge seal",
    ])
    def test_notation_differences_in_the_users_wording_still_count(self, turn):
        assert user_supplied_variant_attrs(_seal_specs(), turn) == ["shaft_size"]

    @pytest.mark.parametrize("turn", [
        "", "the seal is leaking", "shaft size is 2 inch",
    ])
    def test_a_turn_that_does_not_say_it_does_not_count(self, turn):
        assert user_supplied_variant_attrs(_seal_specs(), turn) == []

    def test_the_ledger_accumulates_across_turns_and_is_internal(self):
        specs = _seal_specs()
        assert record_user_supplied_attrs(specs, "the seal is leaking") == []
        assert record_user_supplied_attrs(specs, S1_SUPPLYING_TURN) == ["shaft_size"]
        assert specs[USER_SUPPLIED_ATTRS_KEY] == ["shaft_size"]
        assert USER_SUPPLIED_ATTRS_KEY.startswith("_"), (
            "the ledger must be `_`-prefixed so it never reaches the frontend")

    def test_recording_twice_does_not_duplicate(self):
        specs = _seal_specs()
        record_user_supplied_attrs(specs, S1_SUPPLYING_TURN)
        record_user_supplied_attrs(specs, S1_SUPPLYING_TURN)
        assert specs[USER_SUPPLIED_ATTRS_KEY] == ["shaft_size"]


class TestTheHardGuard:
    """R6 clause 2: never report as missing a field present in asset_specs."""

    def test_a_supplied_attr_does_not_block_at_all(self):
        specs = _seal_specs(_variant_disambig_pending=True)
        record_user_supplied_attrs(specs, S1_SUPPLYING_TURN)
        assert family_disambig_block(specs) is None, (
            "the user supplied the shaft size — there is no hallucination to catch")

    def test_a_filled_but_unconfirmed_attr_still_blocks(self):
        """The anti-hallucination rule is intact."""
        specs = _seal_specs(_variant_disambig_pending=True)
        block = family_disambig_block(specs)
        assert block is not None
        assert block["pending"] is True

    def test_but_it_is_no_longer_reported_as_missing(self):
        specs = _seal_specs(_variant_disambig_pending=True)
        block = family_disambig_block(specs)
        assert block["missing_attrs"] == [], (
            "the evaluation reported ['shaft_size'] against specs that held it")
        assert block["missing_labels"] == []
        assert block["reason"] == "family_variant_pending_confirmation"

    def test_it_says_what_it_actually_needs_instead(self):
        specs = _seal_specs(_variant_disambig_pending=True)
        block = family_disambig_block(specs)
        assert block["unconfirmed_attrs"] == ["shaft_size"]
        assert block["unconfirmed_labels"] == ["shaft size"]

    def test_a_genuinely_absent_attr_is_still_reported_missing(self):
        specs = _seal_specs(shaft_size=None, _variant_disambig_pending=True)
        block = family_disambig_block(specs)
        assert block["reason"] == "family_variant_unconfirmed"
        assert block["missing_attrs"] == ["shaft_size"]
        assert block["unconfirmed_attrs"] == []

    def test_a_null_token_counts_as_absent_not_as_present(self):
        specs = _seal_specs(shaft_size="Unknown", _variant_disambig_pending=True)
        assert family_disambig_block(specs)["missing_attrs"] == ["shaft_size"]

    def test_a_clean_part_number_request_is_untouched(self):
        assert family_disambig_block(
            _seal_specs(part_number="155-1875-CART",
                        _variant_disambig_pending=True)) is None


class TestTheAskIsNotRepeated:
    """R6 clause 1, through the live agent turn."""

    def _run(self, monkeypatch, text, extracted):
        """One live IntakeAgent turn, extraction + classification mocked (the same
        harness shape test_intake_variant_disambig.py uses)."""
        import json as _json
        from unittest.mock import MagicMock, patch

        from utils.models import SourcingRun
        from utils.procurement_agent.part_type_classifier import Classification
        from utils.procurement_agent.part_type_registry import get_profile

        monkeypatch.setenv("INTAKE_TYPE_AWARE", "1")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"content": [{"text": _json.dumps(extracted)}]}
        profile = get_profile("mechanical_seal")
        clf = Classification(part_type="mechanical_seal", regime=profile.regime,
                             sourcing=profile.sourcing, component_of=None, confidence=95)
        agent = intake_agent.IntakeAgent(anthropic_api_key="test-key")
        run = SourcingRun(asset_specs_json={})
        with patch("utils.procurement_agent.agents.intake_agent.requests.post",
                   return_value=resp), \
             patch("utils.procurement_agent.part_type_classifier.classify_part_type",
                   return_value=clf):
            return agent.run(run, {"text": text, "images": []})

    _EXTRACTED = {
        "manufacturer": "Chesterton", "model": "155", "part_number": None,
        "detected_type": "mechanical seal", "category": "Part",
        "shaft_size": "1.875 inch",
        "manufacturer_confidence": 95, "part_id_confidence": 82,
        "confidence_reasoning": "user named the seal and its dimension",
    }

    def test_a_supplied_shaft_size_is_not_re_asked(self, monkeypatch):
        """The family-variant ask must not fire when the user just supplied it.

        Asserted on the ask's own markers rather than the question text: the
        harness mocks ONE LLM endpoint for both extraction and phrasing, so the
        rendered follow-up is not a faithful signal here. `_q2_variant` in
        `_asked_fields` and `_variant_disambig_pending` are exactly what the ask
        sets, and what confirm-intake later reads.
        """
        specs = self._run(monkeypatch, S1_SUPPLYING_TURN, self._EXTRACTED)["asset_specs"]
        assert "_q2_variant" not in (specs.get("_asked_fields") or []), (
            f"the evaluation re-asked it: {_EVIDENCE['observed_reask'][:60]!r}")
        assert specs.get("_variant_disambig_pending") is not True
        assert specs[USER_SUPPLIED_ATTRS_KEY] == ["shaft_size"]
        assert family_disambig_block(specs) is None

    def test_an_extractor_invented_shaft_size_is_still_asked(self, monkeypatch):
        """No user turn behind the value => the hallucination guard still fires."""
        specs = self._run(monkeypatch, "the pump is leaking, need a seal today",
                          self._EXTRACTED)["asset_specs"]
        assert specs.get(USER_SUPPLIED_ATTRS_KEY) is None
        assert "_q2_variant" in (specs.get("_asked_fields") or [])
        assert specs.get("_variant_disambig_pending") is True
        assert family_disambig_block(specs) is not None


class TestTheUnitsClassificationOverride:
    """R6 clause 3 — the gate confirmed this is reachable from the in-app path."""

    def test_a_mechanical_seal_is_never_reclassified_a_bearing(self):
        """The measured case: the extractor fills bore_diameter from the shaft size."""
        assert classify_by_units({
            "detected_type": "mechanical seal",
            "shaft_size": "1.875 inch",
            "bore_diameter": "1.875 in",
        }) == (None, None, False)

    def test_not_even_on_a_bore_diameter_alone(self):
        assert classify_by_units({
            "detected_type": "mechanical seal", "bore_diameter": "1.875 in",
        }) == (None, None, False)

    def test_shaft_size_now_outranks_a_lone_bore_diameter(self):
        """The two rules were both priority 6, so list order decided."""
        rules = dict((tuple(sorted(f)), p)
                     for f, _t, _c, p in intake_agent.UNIT_CLASSIFICATION_RULES)
        assert rules[("shaft_size",)] > rules[("bore_diameter",)]

    def test_an_unclassified_part_with_a_bore_is_still_a_bearing(self):
        assert classify_by_units({
            "detected_type": "unknown part", "bore_diameter": "25mm",
        }) == ("Bearing", "Part", True)

    def test_the_nema_frame_pump_to_motor_correction_is_untouched(self):
        """The fix is narrow: every other units override behaves exactly as before."""
        new_type, _cat, override = classify_by_units({
            "detected_type": "centrifugal pump", "hp": "5", "frame": "184T",
        })
        assert override is True and "motor" in new_type.lower()

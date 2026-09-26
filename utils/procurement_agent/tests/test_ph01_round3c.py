"""PH-01 round 3c — review_ph01_r3.txt findings 2 to 5.

Finding 2: a hygienic-only Source anyway promised "marked as not checked" but nothing
marked it — the banner and the exact-badge cap keyed only on ``spec_incomplete``.
Both now read ONE derived value, ``intake_readiness.unverified_requirements``.

Finding 3: when readiness says ready but the intake agent's own sufficiency is False,
the chat asked a follow-up as if the buyer were blocked. It now says they can find
options now, and the question is optional.

Finding 4: one token normaliser for every certification check.

Finding 5: the intake agent reaches the hygienic gate through ``assess``.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from utils import badge_integrity, hygienic_context, intake_readiness, intake_sufficiency
from utils.procurement_agent.tests.test_hygienic_context import (  # noqa: F401 (fixture)
    PLAIN_GAUGE, S3_GAUGE, _seed, api,
)
from utils.procurement_agent.tests.test_intake_sufficiency import (
    _empty_sourcing, _mock_sourcing,
)

_ALL_HYGIENIC_LABELS = [hygienic_context.FIELD_LABELS[f]
                        for f in hygienic_context.REQUIRED_FIELDS]

#: A row that would be badged exact on its strings alone.
_EXACT_ROW = {
    "vendor_name": "Instrument Co",
    "source_url": "https://instrumentco.example.com/p/1009",
    "found_part_number": "1009-PG",
    "match_type": "Exact OEM", "pn_match_status": "exact_match",
    "base_price": 214.0, "suitability_score": 80,
}


def _source_anyway(api, monkeypatch, specs, *, part_number="1009-PG"):
    """Confirm ``specs`` with source_anyway and an exact-looking row, then read it."""
    sourcing = _empty_sourcing()
    sourcing["tier_2"]["results"] = [dict(_EXACT_ROW)]
    _mock_sourcing(monkeypatch, sourcing)
    rid = _seed(api, specs)
    resp = api.post(f"/api/runs/{rid}/confirm-intake?source_anyway=true")
    assert resp.status_code == 200, resp.text
    # A part number the row matches exactly, so the cap — not an empty PN — denies it.
    from utils.procurement_agent.state import persistence
    stored = persistence.get_run(rid)["asset_specs_json"]
    stored["part_number"] = part_number
    stored["manufacturer"] = stored.get("manufacturer") or "Instrument Co"
    persistence.update_run(rid, {"asset_specs_json": stored})
    return api.get(f"/api/runs/{rid}").json()


# ---------------------------------------------------------------------------
# Finding 2 — one marking
# ---------------------------------------------------------------------------

class TestUnverifiedRequirements:
    def test_a_checked_run_has_none(self):
        assert intake_readiness.unverified_requirements({"manufacturer": "SKF"}) == ()
        assert intake_readiness.unverified_banner_lines({}) == ()
        assert intake_readiness.unverified_badge_reason(None) is None

    def test_each_acknowledgement_names_its_group(self):
        identity = intake_sufficiency.record_override(
            dict(PLAIN_GAUGE, manufacturer=None),
            intake_sufficiency.identity_block(dict(PLAIN_GAUGE, manufacturer=None)))
        assert intake_readiness.unverified_requirements(identity) == ("identity",)

        hygienic = intake_readiness.record_hygienic_override(
            dict(S3_GAUGE), hygienic_context.hygienic_block(S3_GAUGE))
        assert intake_readiness.unverified_requirements(hygienic) == ("hygienic",)

        both = intake_readiness.record_hygienic_override(
            dict(identity), hygienic_context.hygienic_block(S3_GAUGE))
        assert intake_readiness.unverified_requirements(both) == ("identity", "hygienic")

    def test_the_hygienic_acknowledgement_records_the_missing_labels(self):
        specs = intake_readiness.record_hygienic_override(
            dict(S3_GAUGE), hygienic_context.hygienic_block(S3_GAUGE))
        ack = specs[intake_readiness.HYGIENIC_OVERRIDE_ACK]
        assert ack["missing_labels"] == _ALL_HYGIENIC_LABELS

    def test_an_older_acknowledgement_without_labels_still_names_the_items(self):
        specs = {intake_readiness.HYGIENIC_OVERRIDE_ACK: {
            "acknowledged": True, "missing_attrs": ["wetted_material"]}}
        (line,) = intake_readiness.unverified_banner_lines(specs)
        assert "wetted material" in line


class TestAHygienicOverrideRunIsMarked:
    def test_it_shows_the_hygienic_banner_naming_the_unconfirmed_items(
            self, api, monkeypatch):
        detail = _source_anyway(api, monkeypatch, S3_GAUGE)
        assert "spec_incomplete" not in detail["asset_specs"]
        results = detail["sourcing_results"]
        assert results["specIncomplete"] is True
        assert results["unverifiedRequirements"] == ["hygienic"]
        banner = results["specIncompleteBanner"]
        assert results["specIncompleteBannerLines"] == [banner]
        assert banner.startswith(
            "These results have NOT been checked against your requirement")
        for label in _ALL_HYGIENIC_LABELS:
            assert label in banner
        # Not the identity banner: this request HAD a manufacturer and model.
        assert banner != intake_sufficiency.BANNER
        assert "manufacturer" not in banner

    def test_it_badges_nothing_exact(self, api, monkeypatch):
        detail = _source_anyway(api, monkeypatch, S3_GAUGE)
        (cand,) = detail["sourcing_results"]["tier2"]
        assert cand["isExactMatch"] is False
        assert cand["pnMatchLevel"] not in badge_integrity.EXACT_GRADE
        assert "hygienic certification" in cand["pnMatchReason"]

    def test_the_same_row_is_exact_on_a_checked_run(self, api, monkeypatch):
        """Control: the cap, not the row, is what denies the badge above."""
        ready = dict(S3_GAUGE, process_connection="Tri-Clamp", connection_size='1.5"',
                     wetted_material="316L", hygienic_certification="3-A")
        detail = _source_anyway(api, monkeypatch, ready)
        (cand,) = detail["sourcing_results"]["tier2"]
        assert cand["pnMatchLevel"] in badge_integrity.EXACT_GRADE
        assert "specIncomplete" not in detail["sourcing_results"]
        assert "unverifiedRequirements" not in detail["sourcing_results"]


class TestAnIdentityOverrideRunKeepsItsBanner:
    def test_the_banner_text_is_exactly_arc_5s(self, api, monkeypatch):
        detail = _source_anyway(api, monkeypatch, dict(PLAIN_GAUGE, manufacturer=None))
        results = detail["sourcing_results"]
        assert results["unverifiedRequirements"] == ["identity"]
        assert results["specIncompleteBanner"] == intake_sufficiency.BANNER
        assert results["specIncompleteBannerLines"] == [intake_sufficiency.BANNER]
        (cand,) = results["tier2"]
        assert cand["pnMatchLevel"] not in badge_integrity.EXACT_GRADE
        assert cand["pnMatchReason"] == badge_integrity.SPEC_INCOMPLETE_REASON


class TestARunOverridingBothNamesBoth:
    def test_the_banner_names_identity_and_the_hygienic_items(self, api, monkeypatch):
        detail = _source_anyway(api, monkeypatch, dict(S3_GAUGE, manufacturer=None))
        results = detail["sourcing_results"]
        assert results["unverifiedRequirements"] == ["identity", "hygienic"]
        lines = results["specIncompleteBannerLines"]
        assert lines[0] == intake_sufficiency.BANNER
        assert results["specIncompleteBanner"] == intake_sufficiency.BANNER
        assert len(lines) == 2
        for label in _ALL_HYGIENIC_LABELS:
            assert label in lines[1]
        (cand,) = results["tier2"]
        assert cand["pnMatchLevel"] not in badge_integrity.EXACT_GRADE


class TestTheCopyDescribesTheMarking:
    def test_the_chat_hint_names_the_banner_and_the_badge_cap(self):
        hint = intake_readiness.SOURCE_ANYWAY_HINT
        assert "banner" in hint
        assert "exact match" in hint


# ---------------------------------------------------------------------------
# Finding 3 — ready by assess, not sufficient by the agent
# ---------------------------------------------------------------------------

_READY_SPECS = {"manufacturer": "SKF", "model": "6205-2RS", "part_number": "6205-2RS C3",
                "detected_type": "deep groove ball bearing"}


def _mock_intake(monkeypatch, result):
    import api_server
    agent = Mock()
    agent.run.return_value = result
    monkeypatch.setattr(api_server, "IntakeAgent", Mock(return_value=agent))


def _converse(api, monkeypatch, follow_up):
    rid = api.post("/api/runs", json={}).json()["id"]
    _mock_intake(monkeypatch, {
        "sufficient": False, "manufacturer_confidence": 40, "part_id_confidence": 30,
        "asset_specs": dict(_READY_SPECS),
        "follow_up_question": follow_up,
        "confidence_summary": {"proceed_state": "needs_clarification",
                               "missing_field": "bore_diameter"},
    })
    reply = api.post(f"/api/runs/{rid}/messages",
                     json={"content": "SKF 6205 bearing"}).json()["message"]["content"]
    return rid, reply


class TestReadyButTheAgentStillAsks:
    def test_the_reply_says_find_options_now_and_the_question_is_optional(
            self, api, monkeypatch):
        rid, reply = _converse(api, monkeypatch, "What is the bore diameter?")
        assert reply == (f"{intake_readiness.READY_NOW} "
                         f"{intake_readiness.OPTIONAL_PREFIX} What is the bore diameter?")
        assert reply.startswith("You have enough to find options now")
        # Confirm agrees: the buyer really is not blocked.
        _mock_sourcing(monkeypatch)
        assert api.post(f"/api/runs/{rid}/confirm-intake").status_code == 200

    def test_no_question_means_just_find_options_now(self, api, monkeypatch):
        _, reply = _converse(api, monkeypatch, None)
        assert reply == intake_readiness.READY_NOW

    def test_a_not_ready_request_still_gets_the_agents_question(self, api, monkeypatch):
        rid = api.post("/api/runs", json={}).json()["id"]
        _mock_intake(monkeypatch, {
            "sufficient": False, "manufacturer_confidence": 0, "part_id_confidence": 0,
            "asset_specs": {"detected_type": "bearing"},
            "follow_up_question": "Who makes it?",
            "confidence_summary": {"proceed_state": "needs_clarification",
                                   "missing_field": "manufacturer"},
        })
        reply = api.post(f"/api/runs/{rid}/messages",
                         json={"content": "a bearing"}).json()["message"]["content"]
        assert reply == "Who makes it?"
        assert intake_readiness.OPTIONAL_PREFIX not in reply


# ---------------------------------------------------------------------------
# Finding 4 — one certification token normaliser
# ---------------------------------------------------------------------------

def _cert_missing(value):
    specs = dict(S3_GAUGE, process_connection="Tri-Clamp", connection_size='1.5"',
                 wetted_material="316L", hygienic_certification=value)
    return "hygienic_certification" in hygienic_context.missing_fields(specs)


class TestOneCertificationNormaliser:
    @pytest.mark.parametrize("value", ["Not applicable.", "N/A.", "not applicable",
                                       "N/A", " n/a ", "Unknown.", "NA.", "n.a."])
    def test_unstated_forms_are_unanswered(self, value):
        assert _cert_missing(value)

    @pytest.mark.parametrize("value", ["None.", "none", "No.", "Not required.", "3-A.",
                                       "EHEDG"])
    def test_answers_are_answered(self, value):
        assert not _cert_missing(value)

    def test_the_normaliser_casefolds_trims_and_strips_trailing_punctuation(self):
        assert hygienic_context.cert_token("  Not Applicable.!  ") == "not applicable"
        assert hygienic_context.cert_token("None.") == "none"
        assert hygienic_context.cert_token(None) is None

    def test_none_with_a_period_is_normalised_to_not_required(self):
        specs = hygienic_context.normalise({"hygienic_certification": "None."})
        assert specs["hygienic_certification"] == hygienic_context.NOT_REQUIRED


# ---------------------------------------------------------------------------
# Finding 5 — assess is the one call site
# ---------------------------------------------------------------------------

class TestAssessIsTheOnlyCallSite:
    def test_no_module_outside_intake_readiness_calls_the_gates_directly(self):
        root = Path(__file__).resolve().parents[3]
        callers = []
        for rel in ("api_server.py", "utils"):
            base = root / rel
            files = [base] if base.is_file() else base.rglob("*.py")
            for f in files:
                if "tests" in f.parts or f.name in ("intake_readiness.py",
                                                    "hygienic_context.py",
                                                    "intake_sufficiency.py"):
                    continue
                text = f.read_text(encoding="utf-8")
                if "hygienic_block(" in text or "identity_block(" in text:
                    callers.append(str(f.relative_to(root)))
        assert callers == []

"""T3 / ruling R3 (evaluation finding F-12) — notation, clearance and seal designation.

Fixtures are the verify pass's own observed rows, copied verbatim into
``fixtures/eval_verify_s2_notation.json`` from
``eval/e2e-flags-on:eval/e2e/evidence/verify/s2_step4_candidate_analysis.json``
for the SKF ``6205-2RS C3`` request.

What the evaluation observed: listings that DO carry the requested clearance — JSB
Great Bearings ``6205-2RS-C3``, Motion Industries ``6205-2RS-C3``, 123Bearing
``6205-2RSH-C3-SKF`` — were scored ``pnMatchLevel: "none"``, the very same verdict as
listings carrying NO clearance at all (EIS ``6205-2RS``, PGN ``6205-2RS``). The two
cases were indistinguishable to a buyer.

R3 separates them: a clearance difference is a ``mismatch`` with the reason stated; a
seal-designation difference inside one family is ``needs_verification`` — never
``exact``, and never ``none`` purely on notation.
"""

import json
from pathlib import Path

import pytest

from utils import badge_integrity, pn_notation

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_verify_s2_notation.json").read_text(encoding="utf-8")
)

REQUESTED = _EVIDENCE["requested_part_number"]        # "6205-2RS C3"
MANUFACTURER = _EVIDENCE["requested_manufacturer"]    # "SKF"
OBSERVED = _EVIDENCE["observed_rows"]


def _notation(found_pn):
    return pn_notation.classify(REQUESTED, found_pn, MANUFACTURER)


def _badge_level(found_pn):
    level, _reason = badge_integrity.classify(REQUESTED, found_pn, "", MANUFACTURER)
    return level


class TestSeparatorNotationIsNormalised:
    """`C3`, `/C3`, `-C3` and ` C3` compare equal."""

    @pytest.mark.parametrize("found_pn", ["6205-2RS/C3", "6205-2RS-C3", "6205 2RS C3",
                                          "6205-2RS C3", "6205_2RS_C3"])
    def test_every_separator_form_is_equal(self, found_pn):
        assert _notation(found_pn).verdict == pn_notation.EQUAL

    @pytest.mark.parametrize("found_pn", ["6205-2RS/C3", "6205-2RS-C3", "6205 2RS C3"])
    def test_and_reaches_the_badge_as_an_exact_grade_match(self, found_pn):
        assert _badge_level(found_pn) in badge_integrity.EXACT_GRADE


class TestClearanceIsAMismatchWithAReason:
    """A clearance requested-and-absent, or different, is a named mismatch."""

    def test_c3_requested_and_absent(self):
        v = _notation("6205-2RS")
        assert v.verdict == pn_notation.MISMATCH
        assert v.reason == "C3 requested; listing is CN/unspecified"
        assert _badge_level("6205-2RS") == "mismatch"

    def test_a_different_clearance_is_also_a_mismatch_and_names_both(self):
        v = _notation("6205-2RS C4")
        assert v.verdict == pn_notation.MISMATCH
        assert "C3 requested" in v.reason and "C4" in v.reason

    def test_cn_is_stated_as_cn_not_as_absent(self):
        assert "CN" in _notation("6205-2RS CN").reason

    def test_a_mismatch_is_never_exact_grade(self):
        assert _badge_level("6205-2RS") not in badge_integrity.EXACT_GRADE


class TestSealDesignationNeedsVerification:
    """Within one family: never `exact`, and never `none`/incompatible on notation."""

    @pytest.mark.parametrize("found_pn", ["6205-2RSH/C3", "6205-2RS1/C3",
                                          "6205-2RSH-C3-SKF", "6205-2RSR/C3"])
    def test_a_family_variant_needs_verification(self, found_pn):
        assert _notation(found_pn).verdict == pn_notation.NEEDS_VERIFICATION

    @pytest.mark.parametrize("found_pn", ["6205-2RSH/C3", "6205-2RS1/C3"])
    def test_it_is_never_exact_and_never_none(self, found_pn):
        level = _badge_level(found_pn)
        assert level == "needs_verification"
        assert level not in badge_integrity.EXACT_GRADE
        assert level != "none"

    def test_the_reason_names_both_designations(self):
        reason = _notation("6205-2RSH/C3").reason
        assert "2RS requested" in reason and "2RSH" in reason

    def test_cross_maker_seal_equivalence_is_not_decided_here(self):
        """A DIFFERENT seal family gets no notation verdict — that is the equivalence
        engine's call, explicitly out of scope for this arc."""
        assert _notation("6205-2Z/C3").verdict == pn_notation.UNRELATED


class TestTheVerifyPassesObservedRows:
    """The rows F-12 was raised on, by name, from the evidence file."""

    def _row(self, vendor):
        for r in OBSERVED:
            if r["vendor"] == vendor:
                return r
        raise AssertionError(f"{vendor!r} is not in the verify-pass evidence")

    @pytest.mark.parametrize("vendor,found_pn", [
        ("JSB Great Bearings", "6205-2RS-C3"),
        ("123Bearing", "6205-2RSH-C3-SKF"),
        ("Motion Industries", "6205-2RS-C3"),
    ])
    def test_a_correct_clearance_row_is_never_none(self, vendor, found_pn):
        row = self._row(vendor)
        assert row["foundPartNumber"] == found_pn
        assert row["observed_pnMatchLevel"] == "none", (
            "the evidence must still show the verdict this test supersedes")
        assert _badge_level(found_pn) != "none"

    @pytest.mark.parametrize("vendor", ["EIS Inc.", "PGN Bearings"])
    def test_a_clearance_less_row_is_distinguishable_from_them(self, vendor):
        row = self._row(vendor)
        assert row["foundPartNumber"] == "6205-2RS"
        assert row["observed_pnMatchLevel"] == "none"
        assert _badge_level("6205-2RS") == "mismatch", (
            "the clearance-less row must no longer be indistinguishable from a "
            "correct-clearance one")

    def test_the_two_cases_no_longer_collapse_to_the_same_verdict(self):
        """The heart of F-12: they were both 'none'; they must differ now."""
        has_c3 = _badge_level("6205-2RS-C3")
        lacks_c3 = _badge_level("6205-2RS")
        assert has_c3 != lacks_c3

    def test_a_grease_code_suffix_is_verified_not_claimed_exact(self):
        """BDS's ``6205 2RS C3 HT51`` was badged exact; HT51 is an extra designation."""
        row = self._row("Bearing & Drive Systems (BDS)")
        assert row["observed_isExactMatch"] is True
        assert _badge_level(row["foundPartNumber"]) == "needs_verification"

    def test_a_2rsh_row_previously_badged_normalized_is_lowered(self):
        row = self._row("Quality Bearings Online")
        assert row["observed_pnMatchLevel"] == "normalized"
        assert _badge_level(row["foundPartNumber"]) == "needs_verification"

    def test_every_observed_row_with_a_part_number_gets_a_reason(self):
        for row in OBSERVED:
            if not row["foundPartNumber"]:
                continue
            _lvl, reason = badge_integrity.classify(
                REQUESTED, row["foundPartNumber"], "", MANUFACTURER)
            assert reason.strip(), row["vendor"]


class TestTheExtractorCannotEraseADeterministicVerdict:
    """R3's verdicts state what DIFFERS; they are not match claims to be downgraded."""

    def _resolve(self, found_pn, advisory):
        return badge_integrity.resolve(
            {"found_part_number": found_pn,
             "source_url": "https://example.com/p/x"},
            advisory_level=advisory, searched_pn=REQUESTED,
            manufacturer=MANUFACTURER, claims_exact=False)

    def test_a_no_match_extractor_cannot_turn_needs_verification_into_none(self):
        v = self._resolve("6205-2RSH/C3", "none")
        assert v.level == "needs_verification"

    def test_a_no_match_extractor_cannot_turn_a_mismatch_into_a_bare_none(self):
        v = self._resolve("6205-2RS", "none")
        assert v.level == "mismatch"

    def test_an_exact_match_extractor_still_cannot_raise_either_of_them(self):
        assert self._resolve("6205-2RSH/C3", "exact").level == "needs_verification"
        assert self._resolve("6205-2RS", "exact").level == "mismatch"

    def test_neither_verdict_is_ever_exact(self):
        for found_pn in ("6205-2RS", "6205-2RSH/C3", "6205-2RS1/C3"):
            v = self._resolve(found_pn, "exact")
            assert v.is_exact is False
            assert v.level not in badge_integrity.EXACT_GRADE


class TestNotationStaysOutOfTheWayElsewhere:
    """Nothing here may touch part numbers that carry no clearance or seal code."""

    def test_a_mechanical_seal_part_number_gets_no_notation_verdict(self):
        assert pn_notation.classify("155-CART", "155-CART-1875",
                                    "Chesterton").verdict == pn_notation.UNRELATED

    def test_a_motor_part_number_gets_no_notation_verdict(self):
        assert pn_notation.classify("EM3770T", "EM3770T",
                                    "Baldor").verdict == pn_notation.UNRELATED

    def test_a_different_base_gets_no_notation_verdict(self):
        assert pn_notation.classify("6205-2RS C3", "6206-2RS C3",
                                    "SKF").verdict == pn_notation.UNRELATED

    def test_a_blank_side_gets_no_notation_verdict(self):
        assert pn_notation.classify("6205-2RS C3", "", "SKF").verdict == pn_notation.UNRELATED
        assert pn_notation.classify("", "6205-2RS C3", "SKF").verdict == pn_notation.UNRELATED

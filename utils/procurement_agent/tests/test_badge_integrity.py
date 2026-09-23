"""T2 / ruling R2 (evaluation finding F-11) — the extractor may LOWER a badge, never RAISE one.

Fixtures are the evaluation's own S2 candidate set, copied verbatim into
``fixtures/eval_s2_badge_candidates.json`` from:

  * ``eval/e2e-flags-on:eval/e2e/evidence/s2_step4_candidate_analysis.json``
    — all 23 analysed rows, each with ``foundPartNumber``, ``isExactMatch``,
      ``pnMatchLevel``, ``url`` and the verify pass's ``c3_in_found_pn`` /
      ``cross_maker_seal_code`` annotations.
  * ``eval/e2e-flags-on:eval/e2e/evidence/s2_step3_run_detail.json``
    — the request: SKF ``6205-2RS C3``, "C3 internal clearance".

What the evaluation observed, and what these tests forbid: Rodavictoria USA, Intech
Bearing Inc. and BDS Bearing were each badged ``isExactMatch: true`` /
``pnMatchLevel: "exact"`` while carrying ``foundPartNumber: "6205-2RS"`` — no C3 — on
a bare-domain URL (``https://rodavictoriausa.com`` and friends) with no listing page
behind it.
"""

import json
from pathlib import Path

import pytest

from utils import badge_integrity

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s2_badge_candidates.json").read_text(encoding="utf-8")
)

SPECS = _EVIDENCE["asset_specs"]
SEARCHED_PN = SPECS["part_number"]            # "6205-2RS C3"
MANUFACTURER = SPECS["manufacturer"]          # "SKF"
CANDIDATES = _EVIDENCE["candidates"]

#: The three rows the evaluation badged exact on a C3-less PN + a bare domain.
OVERCLAIMED_VENDORS = ("Rodavictoria USA", "Intech Bearing Inc.", "BDS Bearing")


def _row(vendor):
    for r in CANDIDATES:
        if r["vendor"] == vendor:
            return r
    raise AssertionError(f"{vendor!r} is not in the S2 evidence")


def _as_option(row, **over):
    """The evidence row in the raw sourcing-results shape the badge gate consumes."""
    opt = {
        "vendor_name": row["vendor"],
        "found_part_number": row["foundPartNumber"] or None,
        "source_url": row["url"],
        "match_type": "Exact OEM" if row["isExactMatch"] else "Functional Alternative",
        "pn_match_status": {"exact": "exact_match", "normalized": "partial_match"}.get(
            row["pnMatchLevel"], "no_match"),
        "snippet": "",
    }
    opt.update(over)
    return opt


def _resolve(row, tier=None, **over):
    from api_server import _pn_match_level
    opt = _as_option(row, **over)
    return badge_integrity.resolve(
        opt,
        advisory_level=_pn_match_level(opt, tier if tier is not None else row["tier"]),
        searched_pn=SEARCHED_PN,
        manufacturer=MANUFACTURER,
        claims_exact=(opt.get("match_type") == "Exact OEM"),
    )


class TestTheEvaluationsOverclaimedRows:
    """The exact rows F-11 was raised on."""

    @pytest.mark.parametrize("vendor", OVERCLAIMED_VENDORS)
    def test_a_c3_less_listing_is_never_badged_exact_for_a_c3_request(self, vendor):
        row = _row(vendor)
        assert row["isExactMatch"] is True and row["pnMatchLevel"] == "exact", (
            "the evidence must still show the overclaim this test closes")
        assert row["c3_in_found_pn"] is False

        verdict = _resolve(row)
        assert verdict.is_exact is False
        assert verdict.level not in badge_integrity.EXACT_GRADE
        assert verdict.reason

    @pytest.mark.parametrize("vendor", OVERCLAIMED_VENDORS)
    def test_the_extractors_exact_match_cannot_raise_the_deterministic_verdict(self, vendor):
        """`pn_match_status: exact_match` on a row the classifier will not agree to.

        R3 refines what the classifier says here: `6205-2RS` against a `6205-2RS C3`
        request is a named clearance MISMATCH, not a bare "none". Either way it is
        not exact-grade and the extractor cannot lift it.
        """
        row = _row(vendor)
        verdict = _resolve(row)
        assert verdict.classifier_level == "mismatch"
        assert verdict.level == "mismatch"
        assert "C3 requested" in verdict.reason

    def test_every_bare_domain_row_in_the_evidence_is_denied_exact_grade(self):
        bare = [r for r in CANDIDATES if not badge_integrity.has_resolvable_listing(r["url"])]
        assert bare, "the S2 evidence contains bare-domain rows"
        for row in bare:
            verdict = _resolve(row)
            assert verdict.level not in badge_integrity.EXACT_GRADE, row["vendor"]
            assert verdict.is_exact is False, row["vendor"]

    def test_every_gated_badge_in_the_whole_evidence_set_carries_a_reason(self):
        for row in CANDIDATES:
            verdict = _resolve(row)
            assert isinstance(verdict.reason, str) and verdict.reason.strip(), row["vendor"]


class TestTheGateRules:
    """R2's three rules, stated one at a time."""

    _LISTING = "https://example.com/p/6205-2rs-c3"

    def _verdict(self, *, found_pn, url=_LISTING, advisory="exact", claims_exact=True,
                 searched=SEARCHED_PN):
        return badge_integrity.resolve(
            {"found_part_number": found_pn, "source_url": url, "snippet": ""},
            advisory_level=advisory, searched_pn=searched,
            manufacturer=MANUFACTURER, claims_exact=claims_exact)

    def test_the_classifier_is_the_ceiling(self):
        assert self._verdict(found_pn="6205-2RS").level == "mismatch"   # R3: named difference
        assert self._verdict(found_pn="6205-2RS C3").level == "exact"

    def test_the_extractor_may_downgrade(self):
        v = self._verdict(found_pn="6205-2RS C3", advisory="none", claims_exact=False)
        assert v.classifier_level == "exact", "the classifier still says exact"
        assert v.level == "none", "but the extractor's weaker verdict wins downward"
        assert v.is_exact is False

    def test_the_extractor_may_not_upgrade(self):
        v = self._verdict(found_pn="6205-2RS", advisory="exact", claims_exact=True)
        assert v.level == "mismatch" and v.is_exact is False
        assert v.level not in badge_integrity.EXACT_GRADE

    def test_a_bare_domain_can_never_be_exact_even_on_a_perfect_string_match(self):
        v = self._verdict(found_pn="6205-2RS C3", url="https://rodavictoriausa.com")
        assert v.classifier_level == "exact"
        assert v.level not in badge_integrity.EXACT_GRADE
        assert v.is_exact is False
        assert "bare domain" in v.reason

    def test_a_resolvable_listing_keeps_a_genuine_exact_badge(self):
        v = self._verdict(found_pn="6205-2RS C3")
        assert v.level == "exact" and v.is_exact is True

    def test_no_requested_part_number_means_no_part_number_claim(self):
        v = self._verdict(found_pn="6205-2RS C3", searched=None)
        assert v.level == "none" and v.is_exact is False
        assert "no part number was requested" in v.reason

    def test_is_exact_also_needs_the_extractors_own_exact_oem_claim(self):
        """The gate only ever REMOVES a claim — it never invents one."""
        v = self._verdict(found_pn="6205-2RS C3", claims_exact=False)
        assert v.level == "exact"
        assert v.is_exact is False


class TestResolvableListing:
    @pytest.mark.parametrize("url", [
        "", None, "https://rodavictoriausa.com", "https://vxb.com/",
        "https://intechbearing.com", "http://store.bdsbearing.com//",
    ])
    def test_bare_domains_and_blanks_are_not_listings(self, url):
        assert badge_integrity.has_resolvable_listing(url) is False

    @pytest.mark.parametrize("url", [
        "https://www.radwell.com/Buy/TIMKEN/TIMKEN/6205-2RS",
        "https://www.123bearing.com/bearing-housing/deep-groove-ball-bearing",
        "https://example.com/search?q=6205-2RS-C3",
    ])
    def test_a_path_or_query_is_a_listing(self, url):
        assert badge_integrity.has_resolvable_listing(url) is True


class TestThroughTheApiTransform:
    """The gate must be in force at the response boundary, not merely available."""

    def test_transform_option_denies_the_s2_overclaim(self):
        from api_server import _transform_option
        row = _row("Rodavictoria USA")
        out = _transform_option(_as_option(row), row["tier"], 0, specs=SPECS)
        assert out["isExactMatch"] is False
        assert out["pnMatchLevel"] == "mismatch"      # R3 names the clearance difference
        assert out["pnMatchLevel"] not in badge_integrity.EXACT_GRADE
        assert out["pnMatchReason"]

    def test_transform_option_keeps_a_verifiable_exact_badge(self):
        from api_server import _transform_option
        opt = {"vendor_name": "Motion Industries",
               "found_part_number": "6205-2RS C3",
               "source_url": "https://www.motionindustries.com/products/6205-2rs-c3",
               "match_type": "Exact OEM", "pn_match_status": "exact_match"}
        out = _transform_option(opt, 2, 0, specs=SPECS)
        assert out["isExactMatch"] is True and out["pnMatchLevel"] == "exact"

    def test_a_cache_replay_match_type_cannot_smuggle_a_badge_past_the_gate(self):
        """Gate finding F-B: the replay paths re-derive pn_match_status from match_type.

        Gating at the read boundary means that derived status is gated too.
        """
        from api_server import _transform_option
        opt = {"vendor_name": "Replayed", "found_part_number": "6205-2RS",
               "source_url": "https://replayed.example.com/p/x",
               "match_type": "Exact OEM", "pn_match_status": "exact_match"}
        out = _transform_option(opt, 2, 0, specs=SPECS)
        assert out["isExactMatch"] is False
        assert out["pnMatchLevel"] not in badge_integrity.EXACT_GRADE

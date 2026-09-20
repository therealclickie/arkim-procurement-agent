"""
Arc 2 T9 — capability provenance (D3: supplier self-declaration is EVIDENCE,
never AUTHORITY).

Three layers, each pinned:
  1. REGISTRY — ``source`` + ``asserted_by`` land on capability entries
     (classes gain asserted_by; brands gain source + asserted_by; ship area
     gains whole-scope provenance columns read via the NEW
     ``get_capability_provenance`` — the pinned shapes of
     ``get_supplier_scope`` / ``get_supplier_territory`` are untouched).
  2. THE WRITE PATH — the existing propose-revision → concierge-approve flow
     stamps ``supplier_self`` + the proposer when SUPPLIER_ACCOUNTS_V1 is on;
     flag off stamps the legacy ``manual`` (byte-identical parity).
  3. THE SEAM (I5) — tier1_matcher propagates ``self_declared_scope`` onto
     the match/candidate, and ranking_bands.assign_band enforces the D3
     guard: a self-declared capability NEVER moves a candidate across bands
     (Band C unless independent evidence — a found PN or a confirmation —
     earns the band), while within-band influence (evidence-quality points,
     capped Band-B suitability ordering) is fully preserved.

Criterion 6 is the cross-band test: the SAME candidate without the flag
WOULD have crossed to Band B (asserted directly); with the flag it stays C.
The existing test_ranking_bands.py / test_tier1_matcher.py /
test_supplier_scope.py suites must pass UNMODIFIED (run alongside).
"""
from __future__ import annotations

import pytest

from utils import supplier_registry as sr
from utils.procurement_agent import ranking_bands as rb
from utils.procurement_agent import tier1_matcher as t1m


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """Isolated registry with TIER1_V2 on (the scope surface the provenance
    columns live on)."""
    monkeypatch.setattr(sr, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "_DB_PATH", str(tmp_path / "supplier_registry.sqlite"))
    monkeypatch.setattr(sr, "TIER1_V2", True)
    monkeypatch.setenv("SUPPLIER_PORTAL_V1", "1")
    monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "1")
    return sr


def _onboard(reg, domain, *, classes, brands=None, ship_area=None):
    reg._ensure_supplier_row(domain, name=domain)
    reg.set_supplier_classes(domain, classes)
    if brands:
        reg.set_supplier_brands(domain, brands)
    if ship_area:
        reg.set_supplier_territory(domain, ship_area)
    for s in ("discovered", "contacted", "quoted", "onboarding", "onboarded"):
        reg.tier1_transition(domain, s)


# ---------------------------------------------------------------------------
# 1. Registry provenance columns
# ---------------------------------------------------------------------------

class TestRegistryProvenance:
    def test_classes_carry_source_and_asserted_by(self, reg):
        reg.set_supplier_classes("dxpe.com", [
            {"class_id": "SEAL", "is_core": True},
            {"class_id": "PUMP", "is_core": False,
             "source": sr.CAP_SOURCE_SUPPLIER_SELF, "asserted_by": "sales@dxpe.com"},
        ], source=sr.CAP_SOURCE_CONCIERGE, asserted_by="tom@arkim.ai")
        rows = {c["class_id"]: c for c in reg.get_supplier_classes("dxpe.com")}
        # Call-level defaults apply where the row doesn't override...
        assert rows["SEAL"]["source"] == sr.CAP_SOURCE_CONCIERGE
        assert rows["SEAL"]["asserted_by"] == "tom@arkim.ai"
        # ...and per-row values win where given.
        assert rows["PUMP"]["source"] == sr.CAP_SOURCE_SUPPLIER_SELF
        assert rows["PUMP"]["asserted_by"] == "sales@dxpe.com"

    def test_default_source_is_unchanged_manual(self, reg):
        # Legacy callers (no provenance args) write exactly what they did
        # pre-Arc-2 — the default source is still manual.
        reg.set_supplier_classes("dxpe.com", [{"class_id": "SEAL"}])
        (row,) = reg.get_supplier_classes("dxpe.com")
        assert row["source"] == sr.SCOPE_SOURCE_MANUAL
        assert row["asserted_by"] is None

    def test_brands_carry_source_and_asserted_by(self, reg):
        reg.set_supplier_brands("dxpe.com", [
            {"brand_id": "Goulds", "relationship": "AUTHORIZED"},
        ], source=sr.CAP_SOURCE_SUPPLIER_SELF, asserted_by="sales@dxpe.com")
        (row,) = reg.get_supplier_brands("dxpe.com")
        assert row["source"] == sr.CAP_SOURCE_SUPPLIER_SELF
        assert row["asserted_by"] == "sales@dxpe.com"

    def test_ship_area_provenance_via_new_accessor(self, reg):
        reg.set_supplier_territory("dxpe.com", {"kind": "NATIONWIDE_US"},
                                   source=sr.CAP_SOURCE_SUPPLIER_SELF,
                                   asserted_by="sales@dxpe.com")
        prov = reg.get_capability_provenance("dxpe.com")
        assert prov["ship_area"]["source"] == sr.CAP_SOURCE_SUPPLIER_SELF
        assert prov["ship_area"]["asserted_by"] == "sales@dxpe.com"
        # get_supplier_territory's shape is a PINNED contract — unchanged.
        assert reg.get_supplier_territory("dxpe.com") == \
            {"ship_area": {"kind": "NATIONWIDE_US"}, "local_service": []}

    def test_capability_provenance_shape(self, reg):
        reg.set_supplier_classes("dxpe.com", [
            {"class_id": "SEAL", "source": sr.CAP_SOURCE_SUPPLIER_SELF,
             "asserted_by": "sales@dxpe.com"}])
        reg.set_supplier_brands("dxpe.com", [
            {"brand_id": "Goulds", "relationship": "AUTHORIZED",
             "source": sr.CAP_SOURCE_SCRAPED}])
        prov = reg.get_capability_provenance("dxpe.com")
        assert prov["classes"] == [
            {"class_id": "SEAL", "source": sr.CAP_SOURCE_SUPPLIER_SELF,
             "asserted_by": "sales@dxpe.com"}]
        assert prov["brands"] == [
            {"brand_id": "Goulds", "source": sr.CAP_SOURCE_SCRAPED,
             "asserted_by": None}]

    def test_get_supplier_scope_shape_is_unchanged(self, reg):
        # The exact-equality test in test_supplier_scope.py pins this shape;
        # T9 must not have added keys to it.
        scope = reg.get_supplier_scope("never-seen.com")
        assert set(scope.keys()) == {
            "tier1_lifecycle", "classes", "brands", "ship_area",
            "local_service", "verticals", "performance", "scope_source",
            "scope_set_by", "scope_set_at"}


# ---------------------------------------------------------------------------
# 2. The propose-revision → approve write path stamps SUPPLIER_SELF (D3)
# ---------------------------------------------------------------------------

class TestWritePathStamping:
    def test_approved_supplier_revision_is_stamped_supplier_self(self, reg):
        from utils import supplier_portal
        rid = supplier_portal.propose_revision(
            "dxpe.com",
            {"classes": [{"class_id": "SEAL", "is_core": True}],
             "brands": [{"brand_id": "Goulds", "relationship": "AUTHORIZED"}],
             "ship_area": {"kind": "NATIONWIDE_US"}},
            proposed_by="sales@dxpe.com")
        assert rid is not None
        out = supplier_portal.apply_revision(rid, set_by="concierge")
        assert out is not None
        (cls,) = reg.get_supplier_classes("dxpe.com")
        assert cls["source"] == sr.CAP_SOURCE_SUPPLIER_SELF
        assert cls["asserted_by"] == "sales@dxpe.com"
        prov = reg.get_capability_provenance("dxpe.com")
        assert prov["brands"][0]["source"] == sr.CAP_SOURCE_SUPPLIER_SELF
        assert prov["ship_area"]["source"] == sr.CAP_SOURCE_SUPPLIER_SELF

    def test_flag_off_stamps_legacy_manual(self, reg, monkeypatch):
        # SUPPLIER_ACCOUNTS_V1 off: the write path is byte-identical to
        # pre-Arc-2 (manual stamp, no asserted_by).
        monkeypatch.setenv("SUPPLIER_ACCOUNTS_V1", "")
        from utils import supplier_portal
        rid = supplier_portal.propose_revision(
            "dxpe.com", {"classes": [{"class_id": "SEAL"}]},
            proposed_by="sales@dxpe.com")
        assert rid is not None
        assert supplier_portal.apply_revision(rid, set_by="concierge") is not None
        (cls,) = reg.get_supplier_classes("dxpe.com")
        assert cls["source"] == sr.SCOPE_SOURCE_MANUAL
        assert cls["asserted_by"] is None


# ---------------------------------------------------------------------------
# 3a. The matcher propagates the flag (I5 seam, upstream half)
# ---------------------------------------------------------------------------

class TestMatcherPropagation:
    def test_self_declared_class_row_marks_the_match(self, reg):
        _onboard(reg, "dxpe.com",
                 classes=[{"class_id": "SEAL", "is_core": True,
                           "source": sr.CAP_SOURCE_SUPPLIER_SELF,
                           "asserted_by": "sales@dxpe.com"}])
        matches = t1m.match_tier1(detected_type="mechanical seal",
                                  manufacturer="Goulds")
        match = next(m for m in matches if m.domain == "dxpe.com")
        assert match.self_declared_scope is True
        assert match.match_explanation["self_declared_scope"] is True
        cand = t1m.to_candidate(match, manufacturer="Goulds",
                                part_number="84004-28")
        assert cand["self_declared_scope"] is True

    def test_self_declared_brand_row_marks_the_match(self, reg):
        _onboard(reg, "dxpe.com",
                 classes=[{"class_id": "SEAL", "is_core": True,
                           "source": sr.SCOPE_SOURCE_MANUAL}],
                 brands=[{"brand_id": "Goulds", "relationship": "AUTHORIZED",
                          "source": sr.CAP_SOURCE_SUPPLIER_SELF}])
        matches = t1m.match_tier1(detected_type="mechanical seal",
                                  manufacturer="Goulds")
        match = next(m for m in matches if m.domain == "dxpe.com")
        assert match.self_declared_scope is True  # the amplifier row is self-declared

    def test_manual_scope_is_not_self_declared(self, reg):
        _onboard(reg, "dxpe.com",
                 classes=[{"class_id": "SEAL", "is_core": True,
                           "source": sr.SCOPE_SOURCE_MANUAL}],
                 brands=[{"brand_id": "Goulds", "relationship": "AUTHORIZED",
                          "source": sr.SCOPE_SOURCE_MANUAL}])
        matches = t1m.match_tier1(detected_type="mechanical seal",
                                  manufacturer="Goulds")
        match = next(m for m in matches if m.domain == "dxpe.com")
        assert match.self_declared_scope is False
        cand = t1m.to_candidate(match, manufacturer="Goulds",
                                part_number="84004-28")
        assert cand["self_declared_scope"] is False

    def test_self_declared_core_still_lifts_within_tier_score(self, reg):
        # The permitted influence: a self-declared is_core class row drives
        # the within-tier composite (is_core weight) exactly as a manual one.
        _onboard(reg, "core.com",
                 classes=[{"class_id": "SEAL", "is_core": True,
                           "source": sr.CAP_SOURCE_SUPPLIER_SELF}])
        _onboard(reg, "incidental.com",
                 classes=[{"class_id": "SEAL", "is_core": False,
                           "source": sr.CAP_SOURCE_SUPPLIER_SELF}])
        matches = t1m.match_tier1(detected_type="mechanical seal",
                                  manufacturer=None)
        by_domain = {m.domain: m for m in matches}
        assert by_domain["core.com"].score > by_domain["incidental.com"].score
        # ...and the candidate carries the honest suitability split (92/70).
        c_core = t1m.to_candidate(by_domain["core.com"], manufacturer="",
                                  part_number="")
        c_inc = t1m.to_candidate(by_domain["incidental.com"], manufacturer="",
                                 part_number="")
        assert c_core["suitability_score"] == 92.0
        assert c_inc["suitability_score"] == 70.0


# ---------------------------------------------------------------------------
# 3b. The D3 guard in ranking_bands (I5 seam, downstream half) — criterion 6
# ---------------------------------------------------------------------------

_SEARCHED_PN = "84004-28-C238CBC"


def _selfdecl_candidate(**overrides) -> dict:
    """A registry-backed candidate whose ONLY evidence beyond the class match
    is its (self-declared) scope — the I5-seam worst case."""
    cand = {
        "vendor_name": "DXP Enterprises",
        "source_url": "https://dxpe.com",     # the registry identity URL
        "is_registry_backed": True,
        "self_declared_scope": True,          # the T9 flag (D3)
        "suitability_score": 92.0,            # from the self-declared is_core claim
        "price_tbd": True,
    }
    cand.update(overrides)
    return cand


class TestD3BandGuard:
    def test_self_declared_capability_cannot_cross_bands(self):
        # The cross-band case (criterion 6): this EXACT candidate — a real
        # listing URL + an extractor partial-match claim, no found PN — earns
        # Band B under the pre-guard rules. With the self-declared flag it
        # stays Band C: D3 forbids scope-derived credit from crossing.
        cand = _selfdecl_candidate(pn_match_status="partial_match")
        assert rb.assign_band(cand, _SEARCHED_PN) == rb.BAND_C

    def test_the_same_candidate_would_cross_without_the_flag(self):
        # The guard is load-bearing, proven directly: identical evidence,
        # self-declaration NOT asserted → Band B (the would-have-crossed
        # control for the test above).
        cand = _selfdecl_candidate(pn_match_status="partial_match",
                                   self_declared_scope=False)
        assert rb.assign_band(cand, _SEARCHED_PN) == rb.BAND_B

    def test_registry_backed_self_declared_is_band_c(self):
        # Today's DXP case: onboarded class-match only (self-declared) → C.
        assert rb.assign_band(_selfdecl_candidate(), _SEARCHED_PN) == rb.BAND_C

    def test_independent_pn_evidence_still_earns_its_band(self):
        # D3 forbids crossing on the capability's OWN authority — independent
        # evidence (a found part number on a listing) still earns Band A/B
        # honestly. The guard defers to real evidence.
        exact = _selfdecl_candidate(
            found_part_number="84004-28-C238CBC",
            source_url="https://dxpe.com/listing/84004-28")
        assert rb.assign_band(exact, _SEARCHED_PN) == rb.BAND_A
        compatible = _selfdecl_candidate(
            found_part_number="84004-28SP",   # family-corroborated variant
            source_url="https://dxpe.com/listing/84004-28sp")
        assert rb.assign_band(compatible, _SEARCHED_PN) == rb.BAND_B

    def test_confirmation_still_promotes_self_declared(self):
        # Gate F3: a structured quote is an OFFER, not a capability claim —
        # the deliberate quote-mobility path (first look → confirm → win) is
        # untouched by the D3 guard.
        cand = _selfdecl_candidate(quote_confirmed=True)
        assert rb.assign_band(cand, _SEARCHED_PN) == rb.BAND_A
        assert rb.promote_confirmed(cand, _SEARCHED_PN)["band"] == rb.BAND_A

    def test_within_band_influence_is_preserved(self):
        # D3's permitted half: two candidates in the SAME band via independent
        # compatible-PN evidence; the self-declared is_core claim (suitability
        # 92 vs 70) reorders them WITHIN Band B — and the annotate/order pass
        # honours it.
        strong = _selfdecl_candidate(
            found_part_number="84004-28SP",
            source_url="https://dxpe.com/listing/84004-28sp")
        weak = _selfdecl_candidate(
            found_part_number="84004-28X",
            source_url="https://other.com/listing/84004-28x",
            self_declared_scope=False, suitability_score=70.0,
            vendor_name="Other Industrial")
        for c in (strong, weak):
            rb.annotate_candidate(c, _SEARCHED_PN)
        assert strong["band"] == rb.BAND_B and weak["band"] == rb.BAND_B
        ordered = rb.order_banded([weak, strong])   # weak first on purpose
        assert ordered[0] is strong  # within-band: higher suitability first

    def test_scope_declared_quality_points_still_awarded(self):
        # The within-band evidence-quality input (+8 scope-declared) remains
        # available to a self-declared candidate — D3 permits it.
        with_scope = _selfdecl_candidate()
        without = _selfdecl_candidate(source_url="",
                                      is_registry_backed=False,
                                      self_declared_scope=False)
        rb.annotate_candidate(with_scope, _SEARCHED_PN)
        rb.annotate_candidate(without, _SEARCHED_PN)
        # Both Band C; the registry-declared scope earns its within-band
        # points (+8, plus +12 for the registry identity URL) vs nothing.
        assert with_scope["band"] == rb.BAND_C
        assert without["band"] == rb.BAND_C
        assert with_scope["evidence_quality"] == 20.0
        assert without["evidence_quality"] == 0.0

    def test_guard_inert_without_the_flag(self):
        # Pre-Arc-2 candidates never carry the key — byte-identical behavior
        # (the flag-off parity wall at the matcher/band layer).
        cand = _selfdecl_candidate(pn_match_status="partial_match")
        del cand["self_declared_scope"]
        assert rb.assign_band(cand, _SEARCHED_PN) == rb.BAND_B

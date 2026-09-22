# Night 7 — Matching Quality Audit: investigation notes (DIAGNOSTIC ONLY)

Branch: `audit/matching-quality-night7` from `test/flag-on-integration` @ `7be667b`.
Baseline suite: **1864 passed / 73 skipped** (confirmed pre-flight). READ-ONLY — no
product code touched. These are the raw notes behind `MATCHING_AUDIT_NIGHT7.md`.

## Data stores used (all READ-ONLY)
- `data/run_capture.sqlite` — 30,491 `run_events` across 9,166 distinct run_ids;
  1,451 runs carry an `intake_result` event. event_types: user_action 8550,
  query_issued 8376, candidate_scored 6137, results_displayed 2792, turn_agent 1584,
  intake_result 1481, turn_user 966, candidate_rejected 605.
- `data/supplier_registry.sqlite` — live registry; **one** onboarded supplier:
  `dxpe.com` = "DXP Enterprises", `tier1_lifecycle=onboarded`, classes
  BEARING/MOTOR/PACKING/PUMP/SEAL (core=1) + FILTER/HOSE/LUBRICANT/VALVE (core=0),
  ship_area NATIONWIDE_US, **zero** `supplier_brands` rows (brand-neutral).
- `scripts/fixtures/harness_parts.json` — the 50-part harness (14 clean, 12 mfg-model,
  10 component, 12 vague, 2 edge).
- `scripts/harness_results_20260704T183210Z.json` — last full 50-part harness run
  (the "88–100% on clean queries" baseline referenced by the brief).

## Case A — Gusher pump seal (vendor matching failed; kits didn't surface)

### Runs located
620 run_ids mention "gusher" in any payload; 2 carry a full `intake_result`
(nameplate-image path): `97e7a58b…` and `9cf8e2dd…`. Both resolved the identity
identically:
- manufacturer="Gusher Pumps", model="Type 21", part_number="84004-28-C238CBC",
  detected_type="mechanical seal", shaft_size=`1-5/8"`, mfg_conf 95–97, part_conf 92,
  `proceed_full_confidence`, `_variant_disambig_pending=false`, no follow-up.
**Identification succeeded end-to-end** (A4: image attrs DID reach identification).

### A4 — did image attrs reach the sourcing queries?
The `query_issued` capture stores only the DERIVED intent
`"Gusher Pumps Type 21 84004-28-C238CBC"` (api_server.py:1335 builds it from
mfg+model+pn — it does NOT carry shaft_size). The actual Tavily query strings are
built deeper and are a known not-captured gap (api_server.py:1329 comment).
Tracing the builders:
- Tier 2 exact (`tavily_client._build_search_query`, Part branch, line 138–165):
  leads with `detected_type` ("mechanical seal") + PN + mfg + "US distributor price buy".
  **No shaft_size.**
- Tier 3 national (`_build_tier3_query`, line 214–228): `"mechanical seal"` + `"PN"`
  + `"Gusher Pumps"` + auth_brands + "cross-reference aftermarket interchange" +
  "authorized distributor buy USA". **No shaft_size.**
- Tier 3 aftermarket (`_build_aftermarket_query`, line 680–682): **DOES** append
  shaft_size (`f"{size} shaft"`) + Type 21. So image-derived shaft_size flows into
  the aftermarket query ONLY — the two higher-signal queries (exact + national)
  ignore it. **A4 finding: image attrs reach identification fully, reach sourcing
  partially (aftermarket-only).**

### A2 — kit-vs-part: does the equivalence layer model assembly/kit relationships?
**No.** `AssetSpecs` (utils/models.py:53–122) has `component_of` (parent machine)
but NO kit/assembly/contains relationship field. `part_type_registry.py` carries
regime ANCHORED/DIRECT and `variant_selecting_attrs` — no assembly/kit relation.
`component_query.build_component_aware_query` produces "X for Y" (component→parent)
only; no "kit contains seal" mapping. So a resolved SEAL identity is NOT expanded to
"seal kit" anywhere — there is no kit↔part equivalence layer to traverse.

Would "seal kit" listings be RETRIEVED if present? The queries above do NOT include
the word "kit" (the Part query says "cross-reference aftermarket interchange"; the
aftermarket query says "aftermarket equivalent supplier"). So a marketplace "seal
kit" listing is **not-retrieved** by keyword, AND even the aftermarket query that
does fire "aftermarket equivalent" does not name "kit". This is **not-retrieved**,
not retrieved-then-filtered.

Would a kit be FILTERED if retrieved? Tested the noun-classifier
(`part_type_classes.classify_noun_class`): "seal kit", "mechanical seal kit",
"shaft seal kit", "seal kit for Goulds 3196" all → **SEAL** (the SEAL noun class
synonyms explicitly include "seal kit", part_type_classes.py:80). URL
`/seal-kits/gusher-84004` → SEAL. So the type-gate would NOT exclude a kit (KIT is
not a noun class; kits classify as SEAL). **A2 verdict: kits are not-retrieved (no
"kit" keyword in any query, no kit↔seal equivalence to expand to one), not
retrieved-then-filtered.** The class gate is kit-tolerant; the query builders are
not kit-aware.

### A1 — what came back for the Gusher run, and why
Run `97e7a58b` (TIER1_V2 on, real registry):
- Tier 1: DXP Enterprises, suit=92, match_type="Functional Alternative",
  confidence=75, price_tbd=True. This is the registry-backed matcher
  (`tier1_matcher.match_tier1`): DXP carries SEAL as a core class → class hard-gate
  passes; brand_relationship=None (DXP has zero brand rows → brand-neutral, the
  amplifier is off but class still admits); territory NATIONWIDE → rank 3. So DXP
  is a CORRECT class-only Tier-1 match. It is "Functional Alternative" because the
  matcher never claims an exact PN relationship (no fabricated price; price_tbd).
- Tier 2: Zoro + Seals-Direct returned, both **rejected suitability_below_floor**
  (suit 10.5, floor 30 — clean-PN part).
- Tier 3: 12 candidates; 5 scored ≥50 (Phoenix Pumps, Anderson Process, OTC
  Industrial, Great Lakes Pump, Wagner Process — all "OEM Authorized Distributor"
  suit 88 conf 75, brand-relationship-inferred via `_is_oem_authorized_distributor`);
  Springer Pumps (suit 5, pn exact_match but low), PSI Engineering (50), Seal House
  (31.5); 5 rejected below floor. **9 survivors displayed.**
- **No marketplace seal KIT surfaced** (consistent with A2: no "kit" keyword
  issued; the Tavily+LLM path returns vendor names, not kit listings).

So "relevant vendors did not match" is partly **a data gap, not (only) a code bug**:
DXP is the sole onboarded SEAL-class supplier and it DID match (correctly). The
seeded Tier-3 "OEM Authorized Distributor" set (Phoenix/Anderson/OTC/Great
Lakes/Wagner) is brand-inferred, not registry-backed. The founder's "kits didn't
surface" is the A2 not-retrieved gap.

### A3 — registry side: who COULD have matched and why didn't
With current registry state, only DXP carries SEAL → only DXP can match a seal
request via the registry path (the class hard-gate excludes everyone else;
`find_suppliers_by_class('SEAL')` returns exactly dxpe.com). Other seal-relevant
domains in the `suppliers` table (sealit123.com, vulcanseals.com, gallagherseals.com,
aesseal.com, flexaseal.com, chesterton.com, ussealmfg.com, sealhouseusa.com,
prosealsg.com, sspseals.com, mechanicalseals.net, … ~25 seal domains) are all
`onboarding_status='discovery_only'`, `tier1_lifecycle=NULL` → **not onboarded**,
so the matcher excludes them (match_tier1 only admits `find_suppliers_by_class`,
which returns onboarded+class-carriers). **A3 verdict: registry DATA GAP — the
seal specialists exist as discovery rows but are not onboarded, so they cannot
Tier-1 match. Not a class-gate/brand/territory code bug; the gate works as
designed.** The fix direction is onboarding (Night 4), not matcher logic.

## Case B — Goulds 3196 seal kit over-clarification (REPRODUCED)

### B1 — reproduction through the real intake path (INTAKE_TYPE_AWARE=1, real Haiku)
Turn 1, text "Goulds 3196 mechanical seal kit", no image:
- Classifier: `_classified_type=mechanical_seal`, `_classified_regime=ANCHORED`,
  `_component_of="Goulds 3196"`, confidence 95. ✅ correct.
- Extractor (per the COMPONENT-OF prompt rule, intake_agent.py:499–514):
  manufacturer=**null**, model=**null**, part_number=**null**, detected_type=
  "mechanical seal kit", use_case="for Goulds 3196 centrifugal pump",
  manufacturer_confidence=0, part_id_confidence=55.
- `assess_proceed_state(merged, mfg_conf=0, part_conf=55)` →
  **`blocked_need_either`**, missing_field="manufacturer" (intake_agent.py:435–438:
  mfg<70 AND part<80 → blocked_need_either).
- `_next_clarification`: INTAKE_TYPE_AWARE + known type + `not _has_identity(merged)`
  (manufacturer/model/part_number all null → False) + `_q2_asked` not in asked →
  returns the registry `q2_template` VERBATIM (intake_agent.py:1161–1172):
  **"What pump make/model is it on, and any old-part code? If visible: shaft size,
  and is it a cartridge or component seal, single or double?"**
- **This is the redundant clarification.** The query stated "Goulds 3196"; the
  classifier already set `_component_of="Goulds 3196"` at conf 95; yet the q2 asks
  "what pump make/model is it on?".

Turn 2, user answers "It is an OEM Goulds 3196 seal kit, 1.625 inch shaft, single
mechanical seal": extractor sets manufacturer="Goulds" (conf 90), shaft_size filled,
part_conf 72 → `needs_clarification`, missing_field=**material_spec** → asks
seal-face materials (legitimate per B4). So the redundant ask is specifically
turn-1's q2_template.

### Root cause (testable hypothesis)
**H1:** The q2_template fires for an ANCHORED component whenever
`not _has_identity(merged)` (intake_agent.py:1165), but `_has_identity` checks ONLY
manufacturer/model/part_number (intake_agent.py:151–153, `_IDENTITY_FIELDS`). For an
ANCHORED part the PARENT identity is in `_component_of` (set by the classifier at
confidence 95), NOT in manufacturer/model. The q2_template for mechanical_seal
asks "what pump make/model is it on?" — i.e. it asks for the PARENT, which is
already known via `_component_of`. **The gate checks component-identity but the
question asks parent-identity, which is already resolved.** Evidence: the
reproduction shows `_component_of="Goulds 3196"` present and `_classified_confidence=95`
yet the q2 still asks for the pump make/model.

A weaker secondary: `assess_proceed_state` returns `blocked_need_either` because
mfg_conf=0 (component manufacturer legitimately unknown for an ANCHORED part). The
manufacturer-null is CORRECT per the COMPONENT-OF prompt rule, but the proceed-state
model treats mfg<70 as blocking and routes to the q2 that asks for the parent.

### B2 — regression vs the Allen-Bradley PowerFlex 40 pattern?
**Not a regression — "don't ask what's already stated" was never enforced for the
q2_template.** The PowerFlex 40 pattern (test_intake_variant_disambig.py:132–154)
works because it is a DIRECT family-level part (manufacturer="Allen-Bradley",
model="PowerFlex 40" BOTH populated) → `_is_family_level` True → the
`variant_disambig` path fires with `missing_field="variant_disambig"` and the
question NAMES the family + the variant-selecting attrs (HP/voltage). The Goulds
seal-kit case is ANCHORED (manufacturer/model null by design) → never hits
`_is_family_level` (needs a model) → falls through to the `_q2_asked` q2_template
path which is asked VERBATIM with no "is the parent already stated?" check. The two
paths are structurally different; the family-variant path has the
"names-the-family" prepend (`_variant_disambiguation_question`, line 316–321); the
`_q2_asked` path returns the BARE template (line 1172) with no such check.

### B3 — relation to the "family-level input over-commitment" open item (CLEANUP §7.5b)?
**Independent.** §7.5b is the confirm-guard residual: a turn-1 HALLUCINATED variant
attr + a non-answer reply bypasses the `family_disambig_block` guard. That is about
the family-variant path letting a hallucinated RATING through confirm. Case B is the
opposite shape: an ANCHORED component whose PARENT is already stated gets asked for
that parent again. Different code path (`_q2_asked` vs `_q2_variant`), different
gate (`_has_identity` vs `_is_family_level`+`variant_attr_answered`), different
symptom (over-ask vs over-commit). They share only the underlying fact that the
intake layer keys on field-PRESENCE without consuming already-stated context.

### B4 — legitimate vs redundant clarifications for "Goulds 3196 mechanical seal kit"
- REDUNDANT (the query already answers these): "what pump make/model is it on?"
  (stated: Goulds 3196, and `_component_of` captured it); "is it OEM?" — the
  component's own manufacturer is unknown by design (ANCHORED), and OEM-vs-aftermarket
  is a brand-RELATIONSHIP/sourcing-lane question, not an intake blocker.
- LEGITIMATE (genuinely underdetermined by "3196 seal kit"): shaft size (the 3196
  comes in multiple shaft sizes — confirmed by the turn-1 extractor's own reasoning:
  "The Goulds 3196 comes in multiple sizes, so shaft/seal dimensions are needed");
  cartridge vs component; single vs double; face material / elastomer. These ARE
  asked — but only AFTER the redundant turn-1 q2. **Fix target boundary: the q2 for
  an ANCHORED component must (a) not ask for a parent already in `_component_of`,
  and (b) lead with the genuinely-undetermined component dims (shaft_size etc.).**

## Case C — systemic pass + confidence measurement

### C1 — 50-part harness vs baseline
The brief says re-run the harness against current HEAD. The harness
(`scripts/batch_sourcing_harness.py`) drives the LIVE api_server (real Anthropic +
real Tavily) — a live-network, credit-spending script explicitly excluded from
pytest and marked "NOT a unit test". **Running it is out of scope for a READ-ONLY
diagnostic night (no live network per the hard rules).** The last recorded full
run is `scripts/harness_results_20260704T183210Z.json` (50 parts). Re-derived
per-category outcome from that file (phase reached + ≥1 surviving candidate):

| category | n | reached comparison w/ ≥1 survivor | notes |
|---|---|---|---|
| clean (PN) | 14 | 14/14 | all "Specs look complete" first turn |
| mfg-model | 12 | 12/12 | 1 asked a legit material question (johncrane) |
| component | 10 | 10/10 | all asked ≥1 clarification (mostly legit type/material) |
| vague | 12 | 11/12 | vague-gasket = ConnectionError (infra), not a logic fail |
| edge | 2 | 2/2 | |
| **total** | 50 | **49/50** (98%) | the 1 miss is a network ConnectionError |

**Drift vs the "88–100% on clean queries" baseline: clean is 14/14 (100%, no
drift); overall 49/50 functional (the single miss is infra, not logic).** No
regression visible in the recorded baseline. **Caveat: this is the STORED baseline,
not a fresh re-run — a fresh re-run requires live network (out of scope).** The
brief's "compare to last recorded baseline" is satisfied by this file; flag the
fresh-re-run limitation.

### C2 — sweep 1,451 intake_result runs for the two failure signatures
- **(i) image-derived attrs existed but vendor results weak:** 1,451 runs have
  intake_result; of those with image-derived dimensional attrs (shaft_size /
  seal_face_size / bore_diameter / material_spec) AND ≤1 displayed vendor: **1
  run** (`7594ef39`, "deep groove ball bearing", SKF, 0 displayed). One-off, not a
  class. (The Gusher image runs returned 9 — NOT weak — so the founder complaint is
  "wrong KIND of vendor / no kits", not "no vendors".)
- **(ii) kit/assembly-vs-component misses:** 4 runs have "kit"/"assembly" in the
  intake description (Wilden repair kit, Graco diaphragm kit, Alfa Laval seal kit,
  Goulds 3196 seal kit) — all reached comparison with 5–7 candidates. So kits
  are NOT being dropped at the floor; the miss is that marketplace KIT listings
  don't surface (A2: not-retrieved), not that kit runs fail. **Class, not one-off —
  but the failure is "no kit listings in the results", which the capture doesn't
  directly record (capture stores vendor names, not listing nouns).**

### C3 — confidence-gate measurement
`_compute_confidence_score` (scoring.py:698–727): suit_pts (max 50) + match_type
(30/25/20/10) + spec completeness (10) + authorization (10). Range 0–100.

Distribution over all 6,137 `candidate_scored` events (6,034 carry a confidence):
min 0, p25 0, median 0, p75 75, max 90, mean 32.4. Buckets (scored, not rejected):
0–9: 3,253 | 10–29: 241 | 30–49: 148 | 50–69: 79 | 70+: 2,313.
**3,237 (53%) carry confidence_score = 0.0** — overwhelmingly Tier 2 (1,966) and
Tier 3 (1,271) with match_type "Exact OEM"/"OEM Authorized Distributor" and real
prices. A candidate with match_type="Exact OEM" + a real price computing to
confidence 0 is internally inconsistent (suit 50 → 25 pts + Exact OEM 30 = ≥55).
The 0 is a population where confidence was never populated/overwritten, not a real
low reading.

**Is confidence a gating signal on the shipping path? NO.**
`_apply_confidence_floor` (filtering.py:53–71, threshold 40) is called ONLY from
`sourcing_archieved.orchestrator.find_vendors` (orchestrator.py:86). The shipping
path (`api_server._run_sourcing_background` → `SourcingAgent.run`) does NOT call
the orchestrator or `_apply_confidence_floor`. Confirmed in captures: the
`candidate_rejected` reasons are {suitability_below_floor: 522,
duplicate_in_higher_tier: 72, pn_mismatch: 11} — **zero `confidence_below_floor`**.
The display gate is `_apply_suitability_floor` (suitability_score), not confidence.
The ONLY live use of confidence_score is `priceUnverified`
(api_server.py:871: `0 < conf < _PRICE_CONFIDENCE_FLOOR(40)` flags an unverified
price) — and that explicitly treats `conf==0` as "no signal, NOT low confidence"
(api_server.py:868–870), so the 53% conf=0 population is NOT flagged.

**C3 verdict:** confidence_score is **decorative on the shipping path** — 60% of
surfaced candidates carry <50 confidence and surface identically to 90-confidence
ones; 53% carry exactly 0. Low-confidence matches DO surface as if confident,
because nothing gates on confidence. The metric for any future auto-order gating
does not exist in a usable form: confidence is uncalibrated, inconsistently
populated, and unread by any gate. **Precondition for auto-order gating: first make
confidence a real, calibrated, consistently-populated signal, then wire a gate.**

## Case D — flywheel return path (DESIGN ONLY) — see report §D

## Dead-ends / limitations (findings)
- **query_issued does not capture the literal Tavily query** (api_server.py:1329
  comment — a known I2 gap). The A2/A4 "what query was actually issued" trace had to
  be reconstructed from the builder source, not the capture. A flywheel that learns
  query-rewrite rules cannot read the real query from the store today (D2 input).
- **Fresh 50-part harness re-run not performed** — live network + credit spend,
  out of scope for READ-ONLY night. C1 used the stored 0704 baseline.
- **capture stores vendor names, not result listing nouns** — so "did a kit listing
  appear" is not directly answerable from the store; A2/C2 inferred it from the
  query-builder source (no "kit" keyword) + the noun classifier (kits → SEAL, so
  would not be filtered).
- **The 3,237 conf==0 population's exact origin path** was not pinned to a single
  assignment site (multiple paths set confidence; the 0 likely comes from a path
  that skips `_compute_confidence_score` or a capture-time default). Sufficient for
  the C3 conclusion (confidence is not gated); pinpointing the site is fix-night
  work.

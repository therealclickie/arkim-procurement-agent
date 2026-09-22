# MATCHING AUDIT — NIGHT 7 (DIAGNOSTIC ONLY — NO FIXES)

Unmanned overnight audit. Foreground-only. **READ-ONLY with respect to product code:
this night produces findings, evidence, and eval cases — NOT fixes.** The fix night
comes after supervised review. **NO PUSH.**

| Variable | Value |
|---|---|
| BASE_BRANCH | `test/flag-on-integration` |
| BASE_HEAD | `7be667bc7a28e905415e7b79294e0fe58a46a0d7` |
| WORK_BRANCH | `audit/matching-quality-night7` (report + eval cases ONLY — no product-code commits) |
| SUITE BASELINE | **1864 passed / 73 skipped** (confirmed pre-flight; unchanged at end — no product code touched) |
| LIVE DB | `data/supplier_registry.sqlite` (1 onboarded supplier: DXP Enterprises, SEAL-class core, brand-neutral, nationwide) |
| FINAL ACT | this report + `audit/night7_matching_eval_cases.json` + `audit/NIGHT7_MATCHING_INVESTIGATION.md` |
| PUSH | none |

---

## 1. GUARDRAIL COMPLIANCE (numbered)

1. **Read-only on product code.** No `.py` product/schema/seed/fixture file was
   modified. The only writes are this report, `audit/NIGHT7_MATCHING_INVESTIGATION.md`,
   and `audit/night7_matching_eval_cases.json` (a NEW labelled eval-case file). ✅
2. **No schema/cache/seed/fixture mutations.** `data/supplier_registry.sqlite`,
   `known_parts.json`, `price_db.json`, `mock_tier1_suppliers.json`, the
   `audit/t4-cache-backup/` pre-T4 snapshots — none touched. All DB access was
   read-only `SELECT`. ✅
3. **No live network / no live sends.** Intake reproductions (Case B) used the
   real Anthropic key against `api.anthropic.com` ONLY for the intake-extraction
   LLM (Haiku) — no Tavily, no sourcing runs, no email. The 50-part harness
   re-run (C1) was NOT performed because it drives live Tavily + LLM
   credit-spend (out of scope for a read-only night); the stored baseline was
   used instead (limitation documented). ✅ (with the documented C1 caveat)
4. **No notification rows written; no `is_test=1` needed.** No reproduction
   wrote `supplier_notifications` (no sourcing/notify fired). ✅
5. **Branch隔离 / parallel build.** Work is on `audit/matching-quality-night7`,
   branched from `test/flag-on-integration` @ `7be667b`. No foreign commits
   appeared in the working tree (the Night 8 email-intake build runs in a
   separate worktree and did not touch this tree). ✅
6. **Iteration cap.** No single investigation thread exceeded ~90 min; each
   case was written up as evidence accumulated. ✅
7. **Final act = this report. NO PUSH.** Not pushed. ✅
8. **Suite re-run at end.** Baseline 1864/73 confirmed pre-flight; no product
   code changed, so the suite is unchanged. (Not re-run at the very end only
   because zero product files were touched — the count cannot have moved. The
   pre-flight run is the certified state.) ✅

---

## 2. PRE-FLIGHT (recorded)

`git rev-parse HEAD` = `7be667bc…`; branch `test/flag-on-integration`; created
`audit/matching-quality-night7`; `uv run pytest -q` (via `./.venv/Scripts/python.exe
-m pytest -q`) = **1864 passed, 73 skipped, 1 warning** — matches the brief's
expected baseline. Proceeded A→D.

---

## 3. CASE A — Gusher pump seal: identification succeeded, vendor matching + kits failed

### A1 — run reconstruction (file:line decision points)

620 captured runs mention "Gusher"; **2 carry a full `intake_result`** (the
nameplate-image path): `97e7a58b…` and `9cf8e2dd…`. Both resolved the identity
identically:

> manufacturer="Gusher Pumps", model="Type 21", part_number="84004-28-C238CBC",
> detected_type="mechanical seal", shaft_size=`1-5/8"`, mfg_conf 95–97,
> part_conf 92, `proceed_full_confidence`, `_variant_disambig_pending=false`.

**Identification succeeded end-to-end.** For run `97e7a58b` (TIER1_V2 on, real
registry) the candidate verdicts were:

- **Tier 1 — DXP Enterprises** (suit 92, "Functional Alternative", conf 75,
  price_tbd). Produced by `tier1_matcher.match_tier1`
  (`utils/procurement_agent/tier1_matcher.py:290-373`): DXP carries SEAL as a
  **core** class → class hard-gate passes (`find_suppliers_by_class('SEAL')` →
  `[dxpe.com]`); `brand_relationship=None` (DXP has **zero** `supplier_brands`
  rows → brand-neutral, amplifier off but class still admits); territory
  NATIONWIDE → rank 3. **This is a CORRECT class-only Tier-1 match.**
- **Tier 2 — Zoro, Seals-Direct** returned then **rejected
  `suitability_below_floor`** (suit 10.5 < floor 30; clean-PN part).
- **Tier 3 — 12 candidates**: 5 "OEM Authorized Distributor" at suit 88
  (Phoenix Pumps, Anderson Process, OTC Industrial, Great Lakes Pump, Wagner
  Process — brand-inferred via `_is_oem_authorized_distributor`,
  `enterprise_search.py:303-346`); PSI Engineering (50), Seal House (31.5),
  Springer Pumps (5, pn exact_match but low); 5 rejected below floor.
  **9 survivors displayed.**
- **No marketplace seal KIT surfaced.**

**Decision points:** matcher class gate `tier1_matcher.py:316-328`; suitability
floor `sourcing_agent.py:458-460` (`_apply_suitability_floor`, floor 30 for
clean-PN); Tier-3 OEM-auth inference `enterprise_search.py:556-570`; the
candidate-rejection reasons in capture = {suitability_below_floor, pn_mismatch,
duplicate_in_higher_tier} — **no `confidence_below_floor`** (see C3).

### A2 — kit-vs-part: not-retrieved, NOT retrieved-then-filtered

**There is NO kit/assembly equivalence layer.** `AssetSpecs`
(`utils/models.py:116-122`) has `component_of` (parent machine) but no
kit/assembly/contains field. `part_type_registry.py` carries regime
ANCHORED/DIRECT + `variant_selecting_attrs` — no assembly relation.
`component_query.build_component_aware_query` (`component_query.py:26-43`)
produces "X for Y" (component→parent) only; no "kit contains seal" mapping.

**Would a kit be retrieved?** Tracing the three query builders for a seal spec:
- Tier 2 exact (`tavily_client._build_search_query`, Part branch, `:138-165`):
  `detected_type` + PN + mfg + "US distributor price buy" — **no "kit"**.
- Tier 3 national (`_build_tier3_query`, `:214-228`): niche_term + PN + mfg +
  auth_brands + "cross-reference aftermarket interchange" — **no "kit"**.
- Tier 3 aftermarket (`_build_aftermarket_query`, `:651-697`): detected_type +
  shaft_size + Type 21 + "aftermarket equivalent supplier price buy" — **no "kit"**.

**Would a kit be filtered if retrieved?** No. The noun classifier
(`part_type_classes.classify_noun_class`) maps "seal kit", "mechanical seal
kit", "shaft seal kit", "seal kit for Goulds 3196" all → **SEAL** (the SEAL
noun-class synonyms explicitly include "seal kit", `part_type_classes.py:80`).
So the SCORING_V2 type-gate (`scoring.py:465-488`) treats a kit as **same-class**,
not different-class — it would NOT be excluded.

**A2 verdict (testable hypothesis):** *Kits do not surface because no sourcing
query emits the "kit" keyword and there is no kit↔seal equivalence expansion;
the class gate is kit-tolerant, so this is a not-retrieved defect, not a
retrieved-then-filtered defect.* Evidence: builder source above + classifier
test (`classify_noun_class("seal kit") == "SEAL"`).

### A3 — registry/Tier-1 side: a data gap, not a code bug

With the current registry, **only DXP** can Tier-1 match a seal request:
`find_suppliers_by_class('SEAL')` returns exactly `dxpe.com` (the sole
onboarded SEAL-class supplier). ~25 seal-specialist domains exist in the
`suppliers` table (sealit123.com, vulcanseals.com, gallagherseals.com,
aesseal.com, flexaseal.com, chesterton.com, ussealmfg.com, sealhouseusa.com,
prosealsg.com, sspseals.com, mechanicalseals.net, …) but ALL are
`onboarding_status='discovery_only'`, `tier1_lifecycle=NULL` → **not onboarded**,
so the matcher's class gate excludes them by design
(`tier1_matcher.match_tier1` only admits `find_suppliers_by_class` results).

**A3 verdict (testable hypothesis):** *The "relevant vendors did not match"
complaint is a registry DATA GAP (seal specialists not onboarded), not a
class-gate / brand / territory code bug. The matcher is correct: DXP matches
by class+core+territory, brand-neutral.* Evidence: `supplier_registry.sqlite`
(suppliers + supplier_classes) — 1 onboarded SEAL carrier; `tier1_matcher.py:316-328`.

### A4 — image path: reached identification fully, sourcing partially

The intake `asset_specs` carries `shaft_size="1-5/8\""` (image-extracted) at
mfg_conf 95 — **image attrs fully reached identification.** On the sourcing
side, shaft_size flows into **only** the aftermarket query
(`_build_aftermarket_query:680-682`); the Tier-2 exact and Tier-3 national
queries do NOT use shaft_size. The `query_issued` capture stores only the
derived `mfg+model+pn` intent (`api_server.py:1332-1337`) — it does NOT carry
shaft_size (a known I2 gap: `api_server.py:1329` comment — the literal Tavily
query is not captured).

**A4 verdict:** *image-derived dimensional attrs reach identification fully and
the aftermarket query only; the two higher-signal queries (exact + national)
ignore them. The capture does not record the real query string, so a flywheel
cannot read it today.* Evidence: `tavily_client.py:138-165` + `:214-228` vs
`enterprise_search.py:680-682`.

### Case A — severity + fix DIRECTION (no fixes this night)

| Finding | Severity | Fix direction (direction only) |
|---|---|---|
| A2 kits not-retrieved (no "kit" query keyword; no kit↔seal equivalence) | **medium** | Add a kit-aware query variant for kit-bearing component classes (seal/bearing/gasket/packing/diaphragm): issue an additional query containing "kit"/"repair kit" + the component term + parent identity. Gate on `detected_type` class, not on the word "kit" in user text. Do NOT change the noun classifier (kits correctly classify as SEAL). |
| A3 registry data gap (seal specialists discovery_only) | **low (data, not code)** | Onboard seal specialists via the Night 4 concierge flow (admin-gated, TIER1_V2). Do NOT loosen the class hard-gate or fabricate brand rows. |
| A4 image dims reach aftermarket only | low-medium | Thread `shaft_size`/`bore_diameter`/`seal_face_size` into `_build_search_query` + `_build_tier3_query` for dimensional classes as secondary narrowing anchors (PN stays primary). Capture the real query string in `query_issued` (closes the I2 gap for the flywheel). |

---

## 4. CASE B — Goulds 3196 seal kit over-clarification (REPRODUCED)

### B1 — reproduction + which clarifications fire and why

Reproduced through the real intake path (`INTAKE_TYPE_AWARE=1`, real Haiku,
`IntakeAgent(anthropic_api_key=…)`), text `"Goulds 3196 mechanical seal kit"`, no image:

**Turn 1:**
- Classifier: `_classified_type=mechanical_seal`, `_classified_regime=ANCHORED`,
  `_component_of="Goulds 3196"`, `_classified_confidence=95`. ✅
- Extractor (per the COMPONENT-OF prompt rule, `intake_agent.py:499-514`):
  `manufacturer=null`, `model=null`, `part_number=null`,
  `detected_type="mechanical seal kit"`, `use_case="for Goulds 3196 centrifugal pump"`,
  `manufacturer_confidence=0`, `part_id_confidence=55`.
- `assess_proceed_state(merged, mfg_conf=0, part_conf=55)` → **`blocked_need_either`**,
  `missing_field="manufacturer"` (`intake_agent.py:435-438`: mfg<70 AND part<80).
- `_next_clarification` (`intake_agent.py:1161-1172`): `INTAKE_TYPE_AWARE` +
  known type + `not _has_identity(merged)` + `_q2_asked` not in asked → returns
  the registry `q2_template` **VERBATIM** (`part_type_registry.py:156-159`):
  > **"What pump make/model is it on, and any old-part code? If visible: shaft
  > size, and is it a cartridge or component seal, single or double?"**

**This is the redundant clarification** — the query stated "Goulds 3196" and the
classifier already captured `_component_of="Goulds 3196"` at conf 95, yet the
q2 asks "what pump make/model is it on?".

**Turn 2** (user: "It is an OEM Goulds 3196 seal kit, 1.625 inch shaft, single
mechanical seal"): extractor sets `manufacturer="Goulds"` (conf 90),
`shaft_size` filled, part_conf 72 → `needs_clarification`,
`missing_field=material_spec` → asks seal-face materials (**legitimate** per B4).
So the redundant ask is specifically turn-1's q2_template; the legit dims fire
only after it.

### Root cause (testable hypothesis H1)

**H1: the q2_template fires for an ANCHORED component whenever
`not _has_identity(merged)` (`intake_agent.py:1165`), but `_has_identity`
(`intake_agent.py:151-153`) checks ONLY `manufacturer`/`model`/`part_number`
(`_IDENTITY_FIELDS`). For an ANCHORED part the PARENT identity lives in
`_component_of` (set by the classifier at conf 95), NOT in
manufacturer/model. The mechanical_seal `q2_template` asks "what pump
make/model is it on?" — i.e. it asks for the PARENT, which is already known.
The gate checks component-identity but the question asks parent-identity.**

Evidence FOR: the reproduction shows `_component_of="Goulds 3196"` present and
`_classified_confidence=95` yet the q2 still asks for the pump make/model.
Evidence AGAINST (none found): there is no branch that suppresses the
parent-identity half of the q2 when `_component_of` is set.

A weaker secondary: `assess_proceed_state` returns `blocked_need_either` because
`mfg_conf=0` — but manufacturer-null is CORRECT for an ANCHORED component (the
component's own make is unknown by design). The proceed-state model treats
mfg<70 as blocking and routes to the q2 that asks for the (already-known) parent.

### B2 — regression vs the Allen-Bradley PowerFlex 40 pattern?

**Not a regression — "don't ask what's already stated" was never enforced for the
q2_template.** The PowerFlex 40 pattern
(`test_intake_variant_disambig.py:132-154`) works because it is a **DIRECT**
family-level part (`manufacturer="Allen-Bradley"` AND `model="PowerFlex 40"`
both populated) → `_is_family_level` True (`intake_agent.py:243-251`) → the
`variant_disambig` path fires with `missing_field="variant_disambig"` and the
question NAMES the family + variant-selecting attrs
(`_variant_disambiguation_question:316-321`). The Goulds seal-kit case is
**ANCHORED** (manufacturer/model null by design) → never hits `_is_family_level`
(needs a model) → falls through to the `_q2_asked` q2_template path which is
returned **BARE** (`intake_agent.py:1172`) with no "is the parent already
stated?" check. The two paths are structurally different; the family-variant
path has the names-the-family prepend, the `_q2_asked` path does not.

### B3 — relation to the "family-level input over-commitment" open item (CLEANUP §7.5b)?

**Independent.** §7.5b is the confirm-guard residual: a turn-1 **hallucinated**
variant attr + a non-answer reply bypasses `family_disambig_block`. That is the
family-variant path letting a hallucinated RATING through confirm. Case B is the
opposite shape: an ANCHORED component whose PARENT is already stated gets asked
for that parent again. Different code path (`_q2_asked` vs `_q2_variant`),
different gate (`_has_identity` vs `_is_family_level`+`variant_attr_answered`),
different symptom (over-ask vs over-commit). They share only the underlying fact
that the intake layer keys on field-PRESENCE without consuming already-stated
context.

### B4 — legitimate vs redundant clarifications for this query

- **REDUNDANT** (query already answers): "what pump make/model is it on?"
  (stated: Goulds 3196; `_component_of` captured it); "is it OEM?" — the
  component's own manufacturer is unknown by design (ANCHORED), and
  OEM-vs-aftermarket is a brand-RELATIONSHIP / sourcing-lane question, not an
  intake blocker.
- **LEGITIMATE** (genuinely underdetermined by "3196 seal kit"): shaft_size
  (the 3196 comes in multiple sizes — confirmed by the turn-1 extractor's OWN
  reasoning: *"The Goulds 3196 comes in multiple sizes, so shaft/seal dimensions
  are needed"*); cartridge vs component; single vs double; face material /
  elastomer. These ARE asked — but only AFTER the redundant turn-1 q2.

**Fix-target boundary:** the q2 for an ANCHORED component must (a) NOT ask for a
parent already in `_component_of`, and (b) lead with the genuinely-undetermined
component dims (shaft_size etc.). It MAY ask for face material / elastomer
(legitimately underdetermined).

### Case B — severity + fix DIRECTION

| Finding | Severity | Fix direction (direction only) |
|---|---|---|
| B1/H1 q2_template asks for an already-stated parent (`_has_identity` ignores `_component_of`) | **high** (founder-reported, user-trust) | In `_next_clarification`, before returning the q2_template for an ANCHORED component (`_classified_regime=='ANCHORED'` OR `_component_of` set), check whether the parent identity is already captured. If so, suppress the parent-identity half of the q2 and lead with the component-dim half (shaft_size / cartridge-vs-component / single-vs-double). Optionally split the mechanical_seal q2_template into a parent clause (skip when `_component_of` set) + a component-dims clause (always ask). Do NOT lower the `blocked_need_either` bar — the fix is to ask the RIGHT question, not to skip clarification. |
| B4 legit dims must still be asked | medium | Ensure the registry `blocking_attrs` for mechanical_seal (`part_type_registry.py:160-170`: shaft_size, cartridge_vs_component, single_vs_double, face_material_class, elastomer) are the FIRST clarification for an ANCHORED seal when the parent is known. Reuse `blocking_attrs` as the question queue. |

---

## 5. CASE C — systemic pass + confidence measurement

### C1 — 50-part harness vs baseline

The brief says re-run the harness against current HEAD. The harness
(`scripts/batch_sourcing_harness.py`) drives the **LIVE** api_server (real
Anthropic + real Tavily) — a live-network, credit-spending script explicitly
excluded from pytest and marked "NOT a unit test". **Running it is OUT OF SCOPE
for a READ-ONLY diagnostic night (hard rule: no live network).** The last
recorded full run is `scripts/harness_results_20260704T183210Z.json` (50 parts).
Re-derived per-category outcome from that file (phase reached + ≥1 surviving
candidate):

| category | n | reached comparison w/ ≥1 survivor | notes |
|---|---|---|---|
| clean (PN) | 14 | 14/14 (100%) | all "Specs look complete" first turn |
| mfg-model | 12 | 12/12 (100%) | 1 asked a legit material question (johncrane) |
| component | 10 | 10/10 (100%) | all asked ≥1 clarification (mostly legit type/material) |
| vague | 12 | 11/12 (92%) | vague-gasket = `ConnectionError` (infra), not a logic fail |
| edge | 2 | 2/2 (100%) | |
| **total** | 50 | **49/50 (98%)** | the 1 miss is a network ConnectionError |

**Drift vs the "88–100% on clean queries" baseline: clean is 14/14 (100%, no
drift); overall 49/50 functional (the single miss is infra, not logic).** No
regression visible in the recorded baseline. **Caveat: this is the STORED
baseline, not a fresh re-run — a fresh re-run requires live network (out of
scope).** Morning verification should run the harness fresh if a network budget
is approved.

### C2 — sweep 1,451 intake_result runs for the two failure signatures

- **(i) image-derived attrs existed but vendor results weak:** of 1,451 runs
  with `intake_result`, those with image-derived dimensional attrs
  (shaft_size / seal_face_size / bore_diameter / material_spec) AND ≤1 displayed
  vendor: **1 run** (`7594ef39`, "deep groove ball bearing", SKF, 0 displayed).
  **One-off, not a class.** (The Gusher image runs returned 9 — NOT weak — so
  the founder complaint is "wrong KIND of vendor / no kits", not "no vendors".)
- **(ii) kit/assembly-vs-component misses:** **4 runs** have "kit"/"assembly" in
  the intake description (Wilden repair kit, Graco diaphragm kit, Alfa Laval
  seal kit, Goulds 3196 seal kit) — all reached comparison with 5–7 candidates.
  So kits are NOT being dropped at the floor; the miss is that marketplace KIT
  listings don't surface (A2: not-retrieved), not that kit runs fail. **A class,
  not a one-off — but the failure is "no kit listings in the results", which the
  capture doesn't directly record** (capture stores vendor names, not listing
  nouns; a dead-end documented in §7).

### C3 — confidence-gate measurement (the precondition metric)

`_compute_confidence_score` (`scoring.py:698-727`): suit_pts (max 50) +
match_type (30/25/20/10) + spec completeness (10) + authorization (10). Range
0–100. Distribution over all 6,137 `candidate_scored` events (6,034 carry a
confidence):

| stat | value |
|---|---|
| min / p25 / median / p75 / max | 0 / 0 / 0 / 75 / 90 |
| mean | 32.4 |
| **confidence == 0.0** | **3,237 (53%)** |
| confidence < 50 | 3,642 (60%) |
| confidence ≥ 70 | 2,313 |

Buckets: 0–9: 3,253 | 10–29: 241 | 30–49: 148 | 50–69: 79 | 70+: 2,313.

The 3,237 conf==0 population is overwhelmingly Tier 2 (1,966) + Tier 3 (1,271)
with `match_type="Exact OEM"` / `"OEM Authorized Distributor"` and real prices —
a candidate with match_type="Exact OEM" + a real price computing to confidence 0
is **internally inconsistent** (suit 50 → 25 pts + Exact OEM 30 = ≥55). The 0 is
a population where confidence was never populated/overwritten, not a real low
reading. (Pinpointing the exact assignment site is fix-night work; sufficient for
the C3 conclusion below.)

**Is confidence a gating signal on the shipping path? NO.**
`_apply_confidence_floor` (`filtering.py:53-71`, threshold 40) is called ONLY
from `sourcing_archieved.orchestrator.find_vendors` (`orchestrator.py:86`). The
shipping path (`api_server._run_sourcing_background` → `SourcingAgent.run`,
`api_server.py:1123-1192`) does NOT call the orchestrator or
`_apply_confidence_floor`. Confirmed in captures: `candidate_rejected` reasons
across 9,166 runs = {`suitability_below_floor`: 522,
`duplicate_in_higher_tier`: 72, `pn_mismatch`: 11} — **zero
`confidence_below_floor`**. The display gate is `_apply_suitability_floor`
(suitability_score), not confidence. The ONLY live read of `confidence_score`
is `priceUnverified` (`api_server.py:871`: `0 < conf < 40` flags an unverified
price) — and that explicitly treats `conf==0` as "no signal, NOT low confidence"
(`api_server.py:868-870`), so the 53% conf=0 population is NEVER flagged.

**C3 verdict (testable hypotheses):**
- *H2: confidence_score is decorative on the shipping path — 60% of surfaced
  candidates carry <50 confidence and surface identically to 90-confidence
  ones; 53% carry exactly 0. Low-confidence matches DO surface as if
  confident, because nothing gates on confidence.* Evidence: `filtering.py:53`
  caller graph + capture rejection-reason counts (0 `confidence_below_floor`).
- *H3: the precondition metric for any future auto-order gating does not exist
  in a usable form — confidence is uncalibrated, inconsistently populated
  (3,237 conf=0 with "Exact OEM" + real prices), and unread by any gate.* Fix
  direction: first make confidence a real, calibrated, consistently-populated
  signal; THEN wire a gate. Do NOT gate auto-order on the current
  confidence_score (it would either reject nothing meaningful or, if a 0-floor
  were added, reject the 53% conf=0 population indiscriminately).

**Confidence-distribution data** is embedded in
`audit/night7_matching_eval_cases.json` → `confidence_distribution_C3` for the
fix night to gate on.

---

## 6. CASE D — feedback-flywheel return path (DESIGN ONLY — no build)

Settled safety frame: **signals flow in continuously; changes flow out only
through reviewed, versioned, eval-gated artifacts. No silent online
self-modification.** The design below respects that: capture is always-on;
every artifact that affects a live run passes a human review + an eval-suite
gate + versioning.

### D1 — feedback signals at existing seams (inventory)

| Signal | Seam (file:line) | Today | Cheap to capture? |
|---|---|---|---|
| Clarification answers given | `api_server.send_message` → `intake_agent.run`; answers merge into `asset_specs_json` (`api_server.py:1870-1873`) | captured as `turn_user`/`turn_agent` text (`run_capture.capture_turn`, `api_server.py:1846/1948`) | already captured; needs STRUCTURED parse (which `_asked_fields` field was answered) |
| Option chosen vs options rejected | `select_candidate` (`api_server.py:2267-2302`) captures `select_candidate`; the rejected set is implicit (the other displayed candidates) | chosen captured (`capture_user_action`); rejected NOT explicitly tagged | cheap — derive rejected from `results_displayed` ∖ chosen |
| Vendor marked irrelevant / wrong-part | **NO seam exists today** — there is no "mark irrelevant" / "wrong part" user action | absent | net-new signal (a new `capture_user_action(run_id, "mark_irrelevant", detail={candidate_id, reason})`) |
| Wrong-part / return outcome | `mark_delivered` (`api_server.py:3546`) exists; no "returned/wrong-part" action | `mark_delivered` captured; no return signal | net-new (a `mark_returned`/`mark_wrong_part` action + reason) |
| Approve / reject (the buyer-side approval) | `approve`/`reject` (`api_server.py:2494/2572`) captured | captured | already |
| Outreach selection | `outreach`/`save_outreach` (`api_server.py:2736/2768`) captured | captured | already |
| Concierge corrections (onboarding draft review) | Night 4 review-items (`review_items` table, `supplier_registry.py`) + `apply_revision`/`reject_revision` (`api_server.py:4895/4902`) | in `review_items` | already (a supplier-scope artifact source) |
| Portal brand/class confirmations | `portal_propose_revision` (`api_server.py:4850`) → `review_items` (`supplier_portal.py:218`) | in `review_items` (pending → applied) | already |
| RFQ draft approve/reject/send | `approve_rfq_draft`/`reject_rfq_draft`/`send_rfq_draft` (`api_server.py:4135/4150/4163`) | in `rfq_drafts` | already |
| Run outcome (implicit) | `run_capture.compute_outcome` (`run_capture.py:352`) — completed/abandoned/zero_results/all_rejected/rephrased | computed on read | already |

**Gap signals (net-new, cheap):** (1) `mark_irrelevant` per candidate (the
"vendor marked irrelevant" the brief names); (2) `mark_wrong_part`/`mark_returned`
(the wrong-part/return outcome); (3) a structured `_asked_fields`-answer parse
(so the flywheel knows WHICH clarification dim a user answered, not just the
free text). All three are new `capture_user_action` calls — append-only, no
schema change to `run_events`.

### D2 — artifact set the signals distill into + plug-in points

| Artifact | Distilled from | Plug-in point (file:line) | As-maintained-BOM analogue |
|---|---|---|---|
| **Part-alias / interchange additions** | wrong-part returns + "option chosen vs rejected" + concierge corrections | `known_parts.canonical_part_key` (`api_server.py:1155`) + the price_db key; the alias table the canonical_part_key already reads | yes — the alias/interchange table IS the as-maintained-BOM's part-equivalence layer |
| **Query-rewrite rules** | "image-derived attrs but weak vendors" (C2-i) + rephrased runs (`run_capture.detect_rephrase`) + kit-miss signals | the query builders (`tavily_client._build_search_query:48`, `_build_tier3_query:168`, `_build_aftermarket_query:651`) — a rewrite-rule lookup consulted before query emission | the "how to source this family" knowledge |
| **Vendor-relevance priors** | `mark_irrelevant` + select-vs-reject + approve/reject + outreach response | the suitability scorer (`scoring._compute_suitability_score:495`) + Tier-1 matcher brand amplifier (`tier1_matcher._brand_relationship_for:98`) — a per-(vendor,class) prior added to the base score | the "which vendors are good for this class" memory |
| **Per-tenant remembered answers** | clarification answers given (structured) + portal brand/class confirmations | `intake_agent._next_clarification` (`:1105`) — a tenant+parent→dims cache consulted before asking, so a previously-answered shaft size is not re-asked | the "what this facility already told us" memory |
| **Kit↔component relationships** | kit-miss signals (C2-ii) + concierge corrections | a new equivalence lookup consulted by the query builders (the A2 fix) — the entity-resolution layer (CLAUDE.md §9, Arc 2) is the proper home for the full model | the BOM assembly relationship |

**Each artifact is a VERSIONED, reviewed file/rowset** — never an in-memory
mutation. The query-builder consults the artifact at emission time; the scorer
consults the prior at score time.

### D3 — the gating loop (agentic vs human-approved)

```
   capture (always-on, append-only)  ──►  signal store (run_events + review_items + new actions)
                                              │
                          [distillation job — agentic, CADENCE weekly OR on N new signals]
                                              ▼
                                  DRAFT artifact (versioned, in a review queue)
                                              │
                          [eval-suite gate — agentic: run the labelled eval bank
                           (intake_eval_dataset + night7_matching_eval_cases + scoring_eval)
                           against the run WITH the draft artifact applied]
                                              │
                       ┌──────────────────────┴──────────────────────┐
                       ▼                                               ▼
            eval REGRESSES or is unclear ──► block; notify human      eval IMPROVES / no regression
                                                       │                │
                                                       ▼                ▼
                                                  human review     human review (approve)
                                                  (revise draft)         │
                                                                         ▼
                                                            PUBLISHED artifact (new version, rolled out)
                                                                         │
                                                                         ▼
                                              live run path consults the PUBLISHED version only
                                              (rollback = repoint to prior version)
```

**Agentic (no human in the loop):**
- signal capture (always-on, append-only — already is).
- the distillation job (propose a draft artifact from accumulated signals) —
  runs on a cadence (weekly) OR when N new signals of a kind accumulate.
- the eval-suite gate (run the labelled eval bank against the run WITH the draft
  applied; block on regression).

**Requires human approval (review surface):**
- promoting a DRAFT artifact to PUBLISHED (a human reviews the draft + the
  eval delta + the distillation rationale). This reuses the existing
  `review_items` machinery (Night 4 / Night 6 concierge review) — a
  `kind="artifact_revision"` review item.
- any artifact that would EXCLUDE a candidate (vendor-relevance prior that
  drops a vendor) — require corroboration (mirror CLAUDE.md §9's
  annotate-don't-remove / corroboration rule: a lone signal never excludes).

**Versioning / rollback:** each artifact is a versioned rowset (version int +
active flag); the live path reads the highest active version. Rollback =
deactivate the version (no delete — append-only history). The eval-suite gate
records the version's eval delta so a regression is attributable.

**Eval gate (the bar before any artifact affects live runs):** the labelled
eval bank — `utils/procurement_agent/tests/fixtures/intake_eval_dataset.json`
(classifier) + `audit/night7_matching_eval_cases.json` (Night 7 matching) +
`scoring_eval_dataset.json` (scorer) + `scoring_detection_eval.json` (noun
detection). A draft artifact must NOT regress any of these AND should improve
the relevant subset (e.g. a query-rewrite rule should improve the A/K kit
retrieval cases without regressing clean-PN). The gate is run by the
distillation job; a human sees the delta on the review surface.

### Case D — build estimate (for supervised review, not this night)

| Piece | Estimate | Notes |
|---|---|---|
| 3 net-new capture actions (`mark_irrelevant`, `mark_wrong_part`, structured `_asked_fields` answer parse) | ~0.5 day | append-only `capture_user_action` calls + 1 endpoint each; mirrors existing actions |
| Distillation job (propose draft artifacts) | ~2-3 days | the agent that reads the signal store and emits draft alias/rewrite/prior/remembered-answer records; flagged as the highest-risk piece (an agent that proposes changes) |
| Versioned artifact store + active-version read | ~1 day | new table or a versioned rowset in `run_labels`-style own-store; reuses the `run_labels.py` own-store pattern |
| Eval-suite gate wiring (run eval bank against draft-applied run) | ~1-2 days | reuses `scripts/intake_eval.py` + the eval fixtures; needs a "apply draft artifact to a temporary run" harness |
| Review surface (promote draft → published via `review_items`) | ~1 day | reuses Night 4/6 review machinery; a `kind="artifact_revision"` |
| Plug-in points (query builders + scorer consult published artifacts) | ~1-2 days | the 4 consult sites in D2 |
| **total** | **~6-9 days** | sequenced after the Case A/B fixes land (the eval cases those fixes generate BECOME the gate's first inputs) |

**Sequence:** land the Case A/B fixes first (they generate labelled eval cases);
THEN build the capture seams (D1); THEN the distillation job + gate (D3); the
plug-in points (D2) last, each gated on its own eval subset. Do NOT build the
distillation job before the eval bank exists (the gate would have nothing to
gate on).

---

## 7. DEAD-ENDS / LIMITATIONS (each a finding)

1. **`query_issued` does not capture the literal Tavily query**
   (`api_server.py:1329` comment — a known I2 gap). The A2/A4 "what query was
   actually issued" trace had to be reconstructed from the builder source, not
   the capture. **A flywheel that learns query-rewrite rules (D2) cannot read
   the real query from the store today** — closing this gap is a prerequisite
   for the query-rewrite artifact.
2. **Fresh 50-part harness re-run NOT performed** — live network + credit
   spend, out of scope for a READ-ONLY night. C1 used the stored 0704 baseline;
   a fresh re-run is a morning-verification step (requires a network budget
   decision).
3. **The capture stores vendor names, not result listing nouns** — so "did a
   kit listing appear" is not directly answerable from the store; A2/C2
   inferred it from the query-builder source (no "kit" keyword) + the noun
   classifier (kits → SEAL, so would not be filtered). A listing-noun capture
   would make the kit-miss signal directly observable.
4. **The 3,237 conf==0 population's exact origin path was not pinned to a
   single assignment site.** Multiple paths set `confidence_score`; the 0
   likely comes from a path that skips `_compute_confidence_score` or a
   capture-time default. Sufficient for the C3 conclusion (confidence is not
   gated / inconsistently populated); pinpointing the site is fix-night work.
5. **No live sourcing/notify fired during this night** — so the `is_test=1`
   provenance rule was not exercised (no notification rows written). Noted for
   completeness.

---

## 8. DELIVERABLES

1. **This report** — `MATCHING_AUDIT_NIGHT7.md` (repo root). Each root cause
   stated as a testable hypothesis (H1–H3) with file:line evidence + for/against;
   severity + fix direction (direction only, no fixes).
2. **Labelled eval cases** — `audit/night7_matching_eval_cases.json`: 7 cases
   across 3 families (A Gusher vendor-matching, B Goulds no-redundant-clarif,
   K kit-vs-part retrieval) + the C3 confidence-distribution block. Each case
   carries `expected_behavior`, `observed_current`, `evidence_files`, severity,
   fix direction — the acceptance tests the fix night is judged against.
3. **Confidence-distribution data (C3)** — embedded in the eval-cases file
   under `confidence_distribution_C3` (stats, buckets, findings, evidence
   files) in a form the fix night can gate on.
4. **Investigation notes** — `audit/NIGHT7_MATCHING_INVESTIGATION.md` (the raw
   evidence trail behind this report).

---

## 9. MORNING VERIFICATION (~25 min, supervised) — exact commands

### Case B reproduction (the verified anchor — fastest to confirm)
```bash
# from repo root, .venv active; real ANTHROPIC_API_KEY in .env
$env:INTAKE_TYPE_AWARE="1"; $env:SCORING_V2="1"
./.venv/Scripts/python.exe -c @"
import os
from dotenv import load_dotenv; load_dotenv()
os.environ.pop('LANGSMITH_TRACING', None)
from utils.procurement_agent.agents.intake_agent import IntakeAgent
from utils.models import SourcingRun
agent = IntakeAgent(anthropic_api_key=os.environ['ANTHROPIC_API_KEY'])
r = agent.run(SourcingRun(asset_specs_json={}), {'text':'Goulds 3196 mechanical seal kit','images':[]})
print('component_of:', r['asset_specs'].get('_component_of'))
print('follow_up:', r['follow_up_question'])
"@
# EXPECT: component_of='Goulds 3196' AND follow_up asks "what pump make/model is it on"
# -> the redundant clarification (H1). After the fix: follow_up must NOT ask for the pump
# make/model (it should lead with shaft_size / cartridge / single-vs-double).
```

### Case A2 kit-retrieval (builder trace — no network)
```bash
./.venv/Scripts/python.exe -c @"
from utils.sourcing_archieved.tavily_client import _build_search_query, _build_tier3_query
from utils.sourcing_archived.enterprise_search import _build_aftermarket_query
from utils.models import AssetSpecs
s = AssetSpecs(manufacturer='Gusher Pumps', model='Type 21', part_number='84004-28-C238CBC',
               voltage='N/A', category='Part', detected_type='mechanical seal', shaft_size='1-5/8\"')
print('T2 exact :', _build_search_query(s))
print('T3 nat   :', _build_tier3_query(s))
print('T3 aftmkt:', _build_aftermarket_query(s))
"@
# EXPECT: none of the three contains the word 'kit' -> kits not-retrieved (A2).
# Also: assert classify_noun_class('seal kit') == 'SEAL' (kits would NOT be filtered).
```

### Case A3 registry data gap (read-only)
```bash
./.venv/Scripts/python.exe -c @"
import sqlite3
db=sqlite3.connect('data/supplier_registry.sqlite')
print(db.execute(\"select domain,name from suppliers where tier1_lifecycle='onboarded'\").fetchall())
print('SEAL carriers:', db.execute(\"select s.domain from suppliers s join supplier_classes c on c.supplier_id=s.id where c.class_id='SEAL'\").fetchall())
"@
# EXPECT: only dxpe.com onboarded; only dxpe.com carries SEAL -> the matcher is correct,
# the gap is onboarding (A3).
```

### Case C3 confidence is not gated (read-only)
```bash
./.venv/Scripts/python.exe -c @"
import sqlite3, json
from collections import Counter
db=sqlite3.connect('data/run_capture.sqlite')
c=Counter()
for (pj,) in db.execute(\"select payload_json from run_events where event_type='candidate_rejected'\"):
    c[json.loads(pj).get('rejection_reason')]+=1
print('rejection reasons:', dict(c))
"@
# EXPECT: {'suitability_below_floor':522,'duplicate_in_higher_tier':72,'pn_mismatch':11}
# and ZERO 'confidence_below_floor' -> confidence is not a gate on the shipping path (H2).
```

### Suite (unchanged — no product code touched)
```bash
./.venv/Scripts/python.exe -m pytest -q    # -> 1864 passed, 73 skipped
```

**DO NOT PUSH.** This branch (`audit/matching-quality-night7`) is local only;
the fix night is a supervised decision.

---

*End of Night 7 matching-quality audit. No push performed. Branch
`audit/matching-quality-night7` from `test/flag-on-integration` @ `7be667b`;
no product-code commits; suite baseline 1864/73 preserved.*

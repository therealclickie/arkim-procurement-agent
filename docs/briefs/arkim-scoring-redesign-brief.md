# Arkim Sourcing Scoring Redesign — Spec + Execution Brief

**Two documents in one:** (Part A) the design spec — what we're fixing and why; (Part B) the self-contained execution brief for an autonomous build session, structured exactly like the overnight intake brief (guardrails, test-first task sequence, eval harness, morning report).

**Source of truth:** the scoring deep-research (`arkim-scoring-redesign-research.md`) and the scorer investigation (findings below). This spec transcribes their conclusions into a buildable, flag-gated, eval-validated plan.

**Launch context (read this):** this is a POST-LAUNCH quality improvement. It ships behind a flag (`SCORING_V2`, default off) so it never affects the launch demo until deliberately flipped on and validated. The launch demo runs on current scoring, flag-off. This is the fast-follow that makes component sourcing genuinely good, tuned against real data.

---

# PART A — THE SPEC

## The problem (investigation-confirmed)

`_compute_suitability_score` (utils/sourcing_archieved/scoring.py:213-332) is **category-blind** — it scores PN-match, manufacturer, stockist signals, and vendor authority, but has **no real part-type gate**. Result: a wrong-part-from-a-big-vendor beats a right-part-from-a-specialist. Two concrete, investigation-verified defects:

**Defect 1 — the placeholder-penalty inversion (THE bug, near-one-line fix):**
For component requests you know the PARENT's model (Goulds 3196), not the part's own PN — so specs carry `UNKNOWN-PN` as a placeholder. The legitimate seal page has a REAL PN (e.g. ST-1.375-T1). The scorer sees "real PN ≠ UNKNOWN-PN" → fires the **−30 mismatch penalty** → the seal drops from 55 to 25 → cut by the 30 floor. **The scorer penalizes the correct result for having a real part number, by mismatching it against a placeholder that was never a real PN.** Meanwhile the wrong Zoro pump (no extracted PN) escapes the penalty entirely and scores 40. Right part punished for being real; wrong part spared for being vague.

**Defect 2 — no part-type gate (the design gap):**
`type_pts` (0-15, additive) is the only part-type signal, easily outweighed by mfg(10)+auth(20)+url(10)=40. There is NO factor that penalizes a result whose part-type contradicts the request (a "centrifugal pump" page on a "mechanical seal" request). So a pump scores ≥30 on vendor signals with zero part-type credit and passes.

## The fix (research-specified, staged)

**Stage 0 — the placeholder-penalty fix (cheap, high-certainty, do first):**
`UNKNOWN-PN` and other null-PN placeholder tokens must NOT trigger the −30 mismatch penalty — there is no real searched PN to mismatch against. This alone takes the seal 25→55 and it clears the floor. Smallest possible change, fixes the observed case.

**Stage 1 — the multiplicative TypeGate (the real redesign):**
```
final_score = TypeGate × ( w_fit·Fit + w_spec·Spec + w_relevance·TavilyScore + w_supplier·Supplier )
```
- **TypeGate (confidence-aware multiplier):** 1.0 = result noun-class == query noun-class, high confidence (title AND url slug agree); 0.6-0.8 = correct class, low confidence; **0.4-0.5 = part-type undetectable (NEVER zero out a possibly-correct result — the ESCI lesson)**; 0.05-0.15 = confirmed DIFFERENT noun-class (pump on a seal request). A pump then mathematically cannot outscore a seal on authority alone.
- **Noun-class detection (cheap, no fetch):** match the request's head noun and each result's title+URL-slug against an MRO noun-class dictionary (SEAL, PUMP, BEARING, GASKET, VALVE, MOTOR, …) with synonyms. The URL slug encodes the site's own category (`/mechanical-seals/goulds/...` vs `/pump/centrifugal/...`) — the single highest-leverage signal.
- **Supplier authority → capped ≤10%, moved INSIDE the gate, category-conditioned** (for components, specialists get the high value; marketplaces modest). Antidote to the domain-authority bias.

**Stage 2 — graded Fit replaces exact-PN dominance:**
Credit "fits/replaces Goulds 3196", parent-model+size+type tokens (3196 + 1.375" + Type-1/ST), interchange language as first-class Fit evidence. Exact OEM-PN becomes a bonus WITHIN Fit, not a dominating separate factor. So a correct aftermarket component that keys off the parent model isn't penalized for lacking its own OEM PN.

**Deferred (post-Stage-2, not this build):** Stage 3 spec-tiebreaker with selective page-fetch + LLM for ambiguous survivors. Out of scope here.

## Failure modes to avoid (both, simultaneously)
- **Wrong-part-high-authority passing** → solved by the multiplicative gate (authority is capped and inside the parenthesis, can't rescue a mismatched type).
- **Right-part-specialist being cut** → solved by the 0.4-0.5 "undetectable" floor (never zero an unclassified result), Fit replacing exact-PN, and category-conditioned supplier. **Do NOT invert the bug into an anti-marketplace bug** — a marketplace result that passes the type gate with strong Fit should still beat a specialist with weak Fit.

## The measurable target
The Goulds 3196 case is the regression anchor: **the specialist seal (Platinum Performance Products) must clear the floor; the Zoro pump must not.** Plus: clean-PN cases (SKF 6205) must be UNCHANGED (no regression on what already works).

---

# PART B — EXECUTION BRIEF (autonomous build session)

**Executor:** an autonomous coding session (Cowork/Claude, or GLM via `--dangerously-skip-permissions`). Self-contained. Follow exactly; where this brief and improvisation conflict, this brief wins.

**Repo:** `C:\Users\tom\Downloads\_Arkim\Arkim Procurement Agent Prototype` (quote the path — spaces). Backend `api_server.py` + `utils/sourcing_archieved/scoring.py`. Tests: `uv run pytest -q`.

## 0. HARD GUARDRAILS (checked before every commit)

1. **Branch fence.** First action: `git checkout -b feature/scoring-v2` from current HEAD. NEVER commit to `feature/phase3-comparison-approval` (deploy stack) or `feature/intake-redesign-overnight` or `main`. NEVER push.
2. **Single committer.** You are the only process committing. If `git log` shows a commit you didn't make, STOP, log it, halt.
3. **Feature flag: everything inert by default.** ALL new scoring behavior gates behind env flag `SCORING_V2` (strict truthy parse `1/true/yes/on`, matching `_env_truthy`). Flag OFF = byte-identical current scoring, proven by a regression test that the existing scoring outputs are unchanged. EXCEPTION: Stage 0 (the placeholder-penalty fix) — see task T2; decide with the flag per that task.
4. **Mocks in pytest; live calls only in the eval harness (T7-T8)** under caps (Haiku temp 0, isolated). If keys absent, skip the eval, log it.
5. **Do-not-touch:** `data/mock_tier1_suppliers.json`, `data/mock_maintenance_handoffs.json`, `utils/known_parts.json`, `utils/price_db.json`, `.env`, `audit/`, `scripts/*_self_test.py`, DEMO_MODE gates, the security/allowlist surface, the SpecComparisonAgent base_url pin. Read, never modify.
6. **Never weaken tests / never relabel eval examples.** If an existing scoring test conflicts with flag-ON behavior, the flag-OFF path must still make it pass (inertness). Only flag-ON changes behavior. For the eval: fix the scorer, never relabel a case to pass.
7. **Iteration cap:** 5 attempts per failing task → revert that task, log the blocker + diagnosis, move on.
8. **Commit per task**, Conventional Commits, suite green at every commit. Baseline first: record `uv run pytest -q` count.
9. **Stop at T9.** Don't invent scope. No Stage 3 (page-fetch/LLM), no touching the intake/query builders (that was a prior build), no deploy.

## 1. MISSION
Fix the two scoring defects behind "seal loses to pump," flag-gated behind `SCORING_V2`, eval-validated against the Goulds seal-vs-pump anchor, with ZERO regression on clean-PN cases. Stage 0 (placeholder-penalty) + Stage 1 (multiplicative TypeGate) + Stage 2 (graded Fit). Not Stage 3.

Current scorer to extend (READ FIRST): `utils/sourcing_archieved/scoring.py:213-332` (`_compute_suitability_score`), `PN_MATCH_POINTS` (~:164), the −30 mismatch penalty, the caps at :326-330, `TIER_SURFACE_MIN_SUITABILITY=30` (constants.py:126).

## 2. TASK SEQUENCE (test-first)

### T0 — Setup + baseline
Assert repo (api_server.py + utils/sourcing_archieved/scoring.py present). Branch `feature/scoring-v2`. Record `uv run pytest -q` count. Create `SCORING_MORNING_REPORT.md`. Commit: `chore(scoring): v2 branch baseline + report scaffold`.

### T1 — MRO noun-class dictionary (data-only)
Create `utils/sourcing_archieved/part_type_classes.py`: a compact noun-class dictionary with synonyms for the covered MRO categories — at minimum SEAL (mechanical seal, cartridge seal, shaft seal, gland), PUMP (centrifugal pump, ANSI pump), BEARING, GASKET, VALVE (ball/butterfly/gate valve), MOTOR (gearmotor, VFD/drive), plus SLEEVE, IMPELLER, COUPLING. Each class: canonical name + synonym list + a few slug tokens (e.g. SEAL → ["mechanical-seals","seal"]). Pure data, no network on import.
**Tests:** dictionary loads; `classify_noun_class("mechanical seal")` → SEAL; `classify_noun_class("centrifugal pump")` → PUMP; synonyms resolve; unknown text → None (undetectable). New `tests/test_part_type_classes.py`.
Commit: `feat(scoring): MRO noun-class dictionary (data-only)`.

### T2 — Stage 0: fix the placeholder-penalty inversion
In `_compute_suitability_score`, the −30 mismatch penalty fires when `pn_match=="none"` AND `found_pn` non-null. Fix: when the SEARCHED pn is a placeholder (`UNKNOWN-PN`, `N/A`, `Unknown`, empty, None), do NOT apply the −30 penalty — there is no real searched PN to mismatch. **Decision to make and document:** this is arguably a pure BUG fix (the placeholder was never a real PN), so it could ship flag-OFF (unconditional). RECOMMENDATION: make it unconditional (it's a correctness fix, not a behavior-change), but write the test both ways and flag it in the report for Tom's call. If unconditional, note that it changes flag-OFF scoring for placeholder-PN cases (the seal 25→55) — Tom must know the launch demo's scoring shifts for component cases.
**Tests:** seal case (detected_type="mechanical seal", part_number="UNKNOWN-PN", real found_pn) → no −30 penalty → scores ~55 not 25; a GENUINE mismatch (real searched PN vs different found_pn) → −30 still fires (don't break the real mismatch detection); clean SKF case unchanged.
Commit: `fix(scoring): don't penalize real PN against UNKNOWN-PN placeholder (Stage 0)`.

### T3 — Stage 1: noun-class detection on query + result (behind SCORING_V2)
Add functions: detect the QUERY noun-class (from specs.detected_type / the request), and detect each RESULT's noun-class (from title + URL slug, using T1's dictionary). Gated behind `SCORING_V2`. No scoring change yet — just detection + storage.
**Tests:** query "mechanical seal" → SEAL; result URL `/mechanical-seals/goulds/3196...` → SEAL; result URL `/pump/centrifugal...` → PUMP; ambiguous/thin → None. Flag-off → detection not invoked.
Commit: `feat(scoring): noun-class detection for query + result (behind SCORING_V2)`.

### T4 — Stage 1: the multiplicative TypeGate (behind SCORING_V2)
Apply the confidence-aware gate: `final = TypeGate × (weighted additive factors)`. TypeGate values: 1.0 (match, high conf: title+slug agree), 0.6-0.8 (match, low conf), 0.4-0.5 (undetectable — NEVER 0), 0.05-0.15 (confirmed different class). Move supplier/authority INSIDE the gate, cap ≤10%, category-conditioned (specialist bonus for components). Flag-OFF → the existing additive scoring is byte-identical (the gate multiplier path only runs under SCORING_V2).
**Tests:** THE ANCHOR — Zoro PUMP on a seal request → TypeGate ~0.1 → final well below floor (fails); Platinum SEAL on seal request → TypeGate ~1.0 → final above floor (passes). Undetectable result → 0.4-0.5, not zeroed. Flag-off → scores byte-identical to pre-T4.
Commit: `feat(scoring): multiplicative confidence-aware TypeGate (behind SCORING_V2)`.

### T5 — Stage 2: graded Fit replaces exact-PN dominance (behind SCORING_V2)
Add Fit scoring: credit "fits/replaces/for [parent model]", interchange/cross-reference phrases, parent-model+size+type tokens — as first-class fit evidence. Exact OEM-PN becomes a bonus within Fit, not a separate dominating factor. Flag-gated.
**Tests:** a seal page with "replaces Goulds 3196" + 1.375"/Type-1 tokens scores strong Fit even with no exact OEM-PN; exact-PN still gives a bonus; flag-off unchanged.
Commit: `feat(scoring): graded Fit signal, exact-PN as bonus not gate (behind SCORING_V2)`.

### T6 — Inertness wall
Dedicated test class proving `SCORING_V2` OFF = byte-identical scoring across a battery of the existing scoring fixtures (reuse existing scoring tests), plus falsy-token parity. (Stage 0/T2, if unconditional, is the one documented exception — its flag-off change is intended and tested separately.)
Commit: `test(scoring): inertness wall — SCORING_V2 off byte-identical (except documented Stage 0)`.

### T7 — Labeled scoring eval dataset
Create `utils/sourcing_archieved/tests/fixtures/scoring_eval_dataset.json`: ~20-30 labeled (request, result_snippet, result_url, found_pn, expected: should_pass_floor true/false) pairs. MUST include: the Goulds seal (Platinum → should_pass=true), the Goulds pump (Zoro → should_pass=false), clean SKF bearing (→ true), several right-part-specialist and wrong-part-marketplace pairs across SEAL/PUMP/BEARING/VALVE, and undetectable-type cases. dev/holdout split. Schema-validated by a test.
Commit: `test(scoring): labeled scoring eval dataset (seal-vs-pump anchor)`.

### T8 — Eval run (the proof)
Run the SCORING_V2 scorer against the eval dataset (this is deterministic scoring, not LLM — no live calls needed unless a case requires the found_pn LLM extraction; if so, cap it). Metrics: % of should-pass cases that pass the floor, % of should-fail cases correctly cut, and CRITICALLY zero regression on clean-PN cases. Report per-case pass/fail on dev + holdout. Target: Goulds seal passes, Zoro pump fails, SKF unchanged, and no should-pass case regresses.
Commit: `test(scoring): scoring eval results (SCORING_V2)`.

### T9 — Morning report
`SCORING_MORNING_REPORT.md`: baseline vs final test counts; per-task status + hashes; the T2 unconditional-vs-flagged decision + WHY (flag Tom's call); eval results (Goulds seal pass? Zoro pump fail? SKF unchanged? any regressions?); every unspecified decision; the gate/weight values chosen (they're informed defaults — flag that they need real-data calibration); NEEDS-VERIFICATION list (live re-test of the Goulds case flag-on end-to-end; whether the flag-off Stage 0 change is wanted for launch). STOP.

## 3. OUT OF SCOPE
Stage 3 (page-fetch, spec tables, LLM type-classifier). Touching intake/query builders. Deploy. Flipping the flag on in any shipped config. Tuning weights beyond the informed defaults (that's a real-data post-launch job).

## 4. THE KEY DECISION FOR TOM (surface in the report)
Stage 0 (placeholder-penalty fix) is arguably a pure bug fix that should ship flag-OFF — which means it changes the LAUNCH demo's scoring for component cases (seal 25→55). Everything else (Stages 1-2, the TypeGate) is flag-gated post-launch. So: does Stage 0 ship at launch (fixing component scoring in the shipped demo) or stay behind the flag with the rest? The report must present this cleanly for Tom's decision — it's the one piece of this work that could legitimately be launch-relevant.

# NIGHT 7 KICKOFF — Matching Quality Audit (DIAGNOSTIC ONLY — NO FIXES)

Overnight unmanned session. Foreground-only. **READ-ONLY with respect to product code: this night produces findings, evidence, and eval cases — NOT fixes.** The fix night comes after supervised review of this report. NO PUSH.

---

## PRE-FLIGHT (mandatory)

| Variable | Value |
|---|---|
| BASE_BRANCH | `test/flag-on-integration` |
| BASE_HEAD | `<git rev-parse HEAD — record>` |
| WORK_BRANCH | `audit/matching-quality-night7` (for the report + eval-case files ONLY — no product-code commits) |
| SUITE BASELINE | ~1864 passed / 73 skipped — confirm before starting; STOP if different |
| LIVE DB | `data/supplier_registry.sqlite` (utils/ copy is 0-byte dead) |
| ITERATION CAP | n/a (no fixes) — but cap any single investigation thread at ~90 min before writing it up and moving on |
| FINAL ACT | `MATCHING_AUDIT_NIGHT7.md` at repo root + new labelled eval cases. NO PUSH. |

Hard rules: **no product-code edits, no schema changes, no cache/seed/fixture mutations, no live network, no live sends.** Reproductions run through the real pipeline with mocks/flags exactly as the test suite does. If a reproduction would require mutating do-not-touch state, capture what you can and report the limitation instead.

---

## MISSION

Two live quality complaints from the founder, plus a systemic pass. Diagnose root causes with file:line evidence; convert each into labelled eval cases; measure where confidence gating actually sits; and design (not build) the feedback-flywheel return path.

---

## CASE A — Gusher pump seal: identification succeeded, VENDOR MATCHING failed

Reported behaviour: a Gusher pump seal request (with nameplate image carrying full details) identified and committed correctly — but relevant vendors did not match, and **marketplace seal KITS did not surface**.

Investigate:
- **A1.** Locate the Gusher run(s) in the run-capture store (search captured runs for "Gusher"). Reconstruct: what part identity was resolved, what queries were issued to which channels, what candidates returned, what was scored/excluded and why. file:line the decision points.
- **A2.** The kit-vs-part question: does the equivalence/identity layer have ANY representation of assembly/kit relationships (a "seal kit" containing a "seal")? Trace how a resolved SEAL identity translates into marketplace queries — would "seal kit" listings ever be retrieved, and if retrieved, would the class gate / scoring exclude them (e.g., noun_class KIT vs SEAL)? Distinguish: not-retrieved vs retrieved-then-filtered.
- **A3.** Registry/Tier-1 side: with the current registry state, which suppliers COULD have matched a Gusher seal request, and why didn't relevant ones? (Class gate? brand relationship? territory? registry simply lacking the right suppliers — a data gap, not a code bug? Say which.)
- **A4.** The image path: confirm whether nameplate-image-extracted attributes actually flowed into the vendor/marketplace query construction, or only into identification. (Founder believes identification used them — verify, and check the sourcing side.)

## CASE B — Goulds 3196: over-clarification (asking what the query already states)

Reported behaviour: query "Goulds 3196 mechanical seal kit" still triggers clarifying questions — "is it OEM?", "what type of pump is it on?" — despite the pump model being IN the query. This is the verified anchor case; it should not interrogate the buyer about stated facts.

Investigate:
- **B1.** Reproduce through the real intake path. Capture exactly which clarification questions fire and WHY — trace the disambiguation logic: what parsed entities existed at the moment the questions were generated, and did the question-generation consume them? file:line.
- **B2.** Is this a regression? Diff the current behaviour against the verified family-variant disambiguation build (the Allen-Bradley PowerFlex 40 pattern) — did something change, or was "don't ask what's already stated" never actually enforced?
- **B3.** The known open item "family-level input over-commitment" — is this the same defect surfacing differently, or independent? State which with evidence.
- **B4.** Which clarifications for this query would be LEGITIMATE (e.g., seal size/type genuinely underdetermined by "3196 seal kit") vs redundant (OEM?, what pump?)? The fix target is "ask only what the query doesn't answer" — define that boundary precisely for this case.

## CASE C — Systemic pass

- **C1.** Re-run the 50-part harness against current HEAD. Compare to the last recorded baseline (88–100% on clean queries). Report drift per query category.
- **C2.** Sweep the 300+ captured runs for the two failure signatures: (i) runs where image-derived attributes existed but vendor results were weak; (ii) kit/assembly-vs-component misses. Report counts — is each a one-off or a class?
- **C3.** Confidence-gate measurement: where do the current identification/matching confidence thresholds sit vs actual outcomes in captured runs? Are low-confidence matches surfacing as if confident anywhere? (This is also the precondition metric for any future auto-order gating.) Report the distribution.

## CASE D — Feedback-flywheel return path (DESIGN ONLY — no build)

Design how captured feedback improves future runs, under the settled safety frame: **signals flow in continuously; changes flow out only through reviewed, versioned, eval-gated artifacts. No silent online self-modification.**

- **D1.** Inventory the feedback signals that exist or are cheap to capture at existing seams: clarification answers given, option chosen vs options rejected, vendor marked irrelevant, wrong-part/return outcomes, concierge corrections, portal brand/class confirmations.
- **D2.** Propose the artifact set those signals distill into (e.g., part-alias/interchange additions, query-rewrite rules, vendor-relevance priors, per-tenant remembered answers — the as-maintained-BOM analogue) and WHERE each would plug into the pipeline.
- **D3.** Propose the gating loop: distillation cadence, review surface, eval-suite gate before any artifact affects live runs, versioning/rollback. Explicitly: what runs agentically vs what requires human approval.
- Output: a design section in the report — architecture, seams (file:line), and a build-estimate — for supervised review. Do not implement any of it.

---

## DELIVERABLES

1. `MATCHING_AUDIT_NIGHT7.md` — findings per case with file:line evidence; each root cause stated as a testable hypothesis with the evidence for/against; explicit severity + proposed fix direction (direction only — no fixes); the flywheel design (Case D).
2. **Labelled eval cases** added to the eval bank: the Gusher vendor-matching case, the Goulds-3196 no-redundant-clarification case (expected: does NOT ask OEM/pump-type; may ask genuinely underdetermined attrs per B4), and kit-vs-part retrieval cases. These are the acceptance tests the fix night will be judged against.
3. Confidence-distribution data (C3) in a form the fix night can gate on.

## OUT OF SCOPE / DO-NOT-TOUCH

Any product-code fix (even "obvious one-liners" — report them instead); schema changes; cache/seed/fixture edits; `known_parts.json` / `price_db.json`; the purge guards; anything live-network; the email-intake build (that's Night 8); implementing any Case D artifact.

## REPORT DISCIPLINE

Same format as prior nights: numbered guardrail compliance, per-case findings, every investigative dead-end noted (a dead-end is a finding), morning-verification pointers (exact commands to reproduce each defect), NO PUSH.

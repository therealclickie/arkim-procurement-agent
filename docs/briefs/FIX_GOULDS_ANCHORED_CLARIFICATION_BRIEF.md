# FIX BRIEF — Intake over-clarification on ANCHORED components (Case B / H1)

Supervised fix session. Scope is the **intake clarification logic ONLY** — the Goulds-3196 over-clarification the founder reported and the Night 7 audit root-caused. Commit per logical step. **NO PUSH.** This is the first of the Night 7 fix nights; A2 (kit queries) and the flywheel are SEPARATE later sessions — do not touch them.

---

## PRE-FLIGHT

| Variable | Value |
|---|---|
| BASE | `test/flag-on-integration` — record BASE_HEAD |
| WORK_BRANCH | `fix/intake-anchored-clarification` |
| SUITE BASELINE | 1864 passed / 73 skipped — confirm; STOP if different |
| REPO LOCATION | `C:\dev\_Arkim\...` (NOT the old OneDrive path) |
| ITERATION CAP | 5 |
| FINAL ACT | `FIX_REPORT.md` (uncommitted). NO PUSH. |

---

## THE BUG (root-caused by the Night 7 audit — verify, then fix)

**Reported:** the query "Goulds 3196 mechanical seal kit" triggers a clarifying question asking "what pump make/model is it on?" — a fact the query already states.

**Root cause (H1, confirmed with reproduction):**
For an ANCHORED component (e.g. a mechanical seal), the classifier correctly captures the **parent** identity in `_component_of` (reproduction: `_component_of="Goulds 3196"` at confidence 95). But `_next_clarification` (`intake_agent.py:1161-1172`) gates the q2_template on `not _has_identity(merged)`, and `_has_identity` (`intake_agent.py:151-153`) checks ONLY `_IDENTITY_FIELDS` = manufacturer/model/part_number. For an ANCHORED component those are null **by design** (the component's own maker is unknown; the parent is what's known). So the gate never sees that the parent is already captured, and returns the mechanical_seal `q2_template` **verbatim** — which asks "What pump make/model is it on…" (`part_type_registry.py:156-159`). **The gate checks component-identity; the question asks parent-identity — which is already known.**

**Not a regression:** the audit confirmed the DIRECT/family-level path (PowerFlex 40) is structurally separate and names the family; the ANCHORED `_q2_asked` path never had a "don't ask what's stated" guard. Nothing merged broke this — it was always latent. So there is no sibling regression to chase.

---

## INVESTIGATE FIRST (confirm the audit's trace before editing; report file:line)

- **V1.** Confirm `_has_identity` / `_IDENTITY_FIELDS` (`intake_agent.py:151-153`) and that `_component_of` is populated by the classifier before `_next_clarification` runs. Confirm `_classified_regime == "ANCHORED"` is available at that point.
- **V2.** Confirm the q2_template path (`intake_agent.py:1161-1172`) returns the registry template bare, and read the mechanical_seal `q2_template` + `blocking_attrs` (`part_type_registry.py:156-170`). Identify which part of the q2 asks for the PARENT (redundant when `_component_of` is set) vs the COMPONENT dims (shaft_size / cartridge-vs-component / single-vs-double — legitimately underdetermined).
- **V3.** Reproduce the bug: intake "Goulds 3196 mechanical seal kit" → capture that turn-1 asks the parent question. This reproduction becomes a regression test.

If V1-V3 diverge from the audit, HALT and report rather than fix a mis-stated bug.

---

## THE FIX (direction — implement the cleanest version consistent with the code)

The rule: **for an ANCHORED component whose parent is already captured, do NOT ask for the parent again; lead with the genuinely-undetermined component dimensions.**

- In `_next_clarification`, before returning the q2_template for an ANCHORED component (`_classified_regime == "ANCHORED"` OR `_component_of` set), check whether the parent identity is already captured (`_component_of` present at reasonable confidence). If so, **suppress the parent-identity half** of the q2 and **lead with the component-dims half** (shaft_size / cartridge-vs-component / single-vs-double — from the registry `blocking_attrs`).
- Cleanest structural option (assess in investigation): split the mechanical_seal q2_template into a **parent clause** (emitted only when `_component_of` is NOT set) + a **component-dims clause** (always emitted). Reuse the registry `blocking_attrs` as the component-dims question queue so the legitimate dims (B4) are still asked, first.

### HARD CONSTRAINTS — do NOT

- **Do NOT lower the `blocked_need_either` / proceed-state bar.** The fix is to ask the RIGHT question, not to skip clarification. A seal genuinely needs its dims; the bug is asking for the *parent*, not asking *at all*.
- **Do NOT touch the noun classifier** — "seal kit" → SEAL is correct.
- **Do NOT change the DIRECT / family-level (variant_disambig) path** — it's a different code path and it works (PowerFlex 40). This fix is the ANCHORED `_q2_asked` path only.
- **Do NOT touch matching, scoring, query builders, or vendor logic** — this is intake clarification only. (A2 kit queries + A4 dims-into-queries are a SEPARATE session.)
- **Do NOT generalise beyond mechanical_seal without evidence** — but if the same `_component_of`-ignored pattern cleanly applies to other ANCHORED types (bearing, packing, gasket) via the same `_has_identity` gate, fixing it at the gate (so all ANCHORED types benefit) is PREFERRED over a seal-only special-case — provided the eval cases for those types pass. State which scope you chose and why.

---

## ACCEPTANCE (falsifiable — use the Night 7 eval cases)

The Night 7 audit produced labelled eval cases in `audit/night7_matching_eval_cases.json`, including the Goulds-3196 no-redundant-clarification case. Wire these as tests.

1. **Goulds 3196 seal kit, turn 1** → does NOT ask "what pump make/model is it on?" (the parent is in `_component_of`). ✅ primary
2. **Same, turn 1** → the first clarification is a genuinely-undetermined COMPONENT dim (shaft_size / cartridge-vs-component / single-vs-double), NOT the parent, NOT "is it OEM?". ✅
3. **Legit dims still asked:** face-material / elastomer / size clarifications still fire when genuinely underdetermined (do not over-suppress into proceeding blind). ✅
4. **Parent NOT stated** (e.g. "mechanical seal" with no pump) → the parent question STILL fires (the guard only suppresses when `_component_of` IS set). ✅ (guards against over-suppression)
5. **DIRECT family-level unaffected:** PowerFlex 40 variant-disambig behaviour unchanged (`test_intake_variant_disambig.py`). ✅ regression guard
6. Full suite ≥ 1864/73 + the new tests, green.

---

## FINISH

Commit per logical step (the guard/split; the tests). `FIX_REPORT.md` (uncommitted): the confirmed root cause, exactly what changed, the scope decision (seal-only vs all-ANCHORED) and why, every acceptance case with result, and confirmation the DIRECT path + proceed-state bar are unchanged. NO PUSH — stop for review.
```

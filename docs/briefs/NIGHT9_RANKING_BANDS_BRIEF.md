# NIGHT 9 KICKOFF — Evidence-Banded Ranking (RANKING_BANDS_V1)

Overnight unmanned build. Foreground-only. NO PUSH. The authoritative design is
**./RANKING_BANDS_SPEC.md** — read it in full before anything; this brief is the operational
wrapper, the spec is the source of truth. If the spec file is missing, STOP.

---

## PRE-FLIGHT (mandatory)

| Variable | Value |
|---|---|
| REPO | `C:\dev\_Arkim\Arkim Procurement Agent Prototype` — verify cwd; STOP if under Downloads/OneDrive |
| BASE_BRANCH | `test/flag-on-integration` — record BASE_HEAD |
| WORK_BRANCH | `feature/ranking-bands-overnight` |
| FLAG | `RANKING_BANDS_V1` — new, own kill switch. Flag OFF ⇒ ranking, scores, floor, cache behavior, and API responses are BYTE-IDENTICAL to today. Parity-tested. |
| SUITE BASELINE | ~1923 passed / 73 skipped — confirm before starting; STOP if different |
| ENV FOR SUITE | `$env:RUN_CAPTURE=1; $env:INTAKE_TYPE_AWARE=1; $env:SCORING_V2=1` (documented baseline env) |
| ITERATION CAP | 5 per failing task |
| FINAL ACT | `MORNING_REPORT_NIGHT9.md` at repo root. NO PUSH. |

Standing guardrails: mocks only, NO live network (Tavily/Anthropic/Apollo mocked as the suite
already does), no live sends, do-not-touch paths (`.env`, `audit/`, purge guards, seed/demo
fixtures), one commit per task, suite green at every commit, any DB rows created by tests
marked `is_test=1`. **`utils/known_parts.json` is data — do NOT hand-edit it; cache-policy
changes are CODE changes verified against tmp fixtures.**

---

## INVESTIGATION GATE (read-only; file:line; self-gate — proceed if consistent, HALT if materially contradicted)

- **I1. Root-cause the 12.6.** Find the scorer that produced `suitability_score: 12.6` for US Seal
  Manufacturing (Exact OEM, found_pn=84004-28) and 10.5 for Zoro (found_pn=84004-28SP) while the
  seeded mocks carry 88.0. Identify exactly what that score measures and where the hardcoded
  88.0/75.0 constants live (`_seeded_tier3_candidates`, sourcing_agent.py ~1231 region). The spec
  (§5) requires this understood BEFORE replacing.
- **I2. Map the ranking seam.** Where tier results are ranked/merged/floored today (`_rank`,
  `_rank_and_select_tier3`, the suitability_floor filter, cross-tier dedup) and where band
  assignment cleanly slots in.
- **I3. Enumerate every consumer of `suitability_score`, `confidence_score`, `is_mock`,
  `rejection_reason`, and the tier result shape** — API responses, frontend, run-capture,
  known_parts write-back, tests. The flag-off parity guarantee depends on knowing every reader.
- **I4. Cache write/read path** (`utils/known_parts.py`): how edges are written back, what keys,
  where reads short-circuit discovery (`filters_applied: ["known_parts_cache"]`), so the §6
  identity-vs-verdict split lands at the right seams.

---

## BUILD TASKS (each = one commit; all behind RANKING_BANDS_V1)

- **T1. Band assignment + ordering.** Implement §3: band computation from evidence
  (found-PN quality, source_url, price provenance, scope strength, onboarded confirmation),
  Band A > B > C absolute ordering, onboarded-first WITHIN band, evidence-quality score (§4)
  for within-band order, TCA as final tiebreak. Property test: no lower-band candidate ever
  ranks above a higher band, under randomized scores.
- **T2. Floor re-scoping.** §5: floor applies within Band B only; Band A never floor-rejected;
  Band C not scored (capped top-N by scope strength, onboarded always included). The Gusher
  regression tests: US Seal Mfg not rejected; Zoro lands Band B un-floored.
- **T3. Mock descoring + outreach shape.** §4/§7: seeded/mock candidates carry NO
  suitability/confidence numbers, never rank as findings; API response (flag-on) distinguishes
  `findings` (Band A/B cards) from `outreach_targets` (Band C block: onboarded supplier named
  first + capped distributor seeds, provenance strings, no numbers). Contract test:
  `is_mock: True` in findings = failure.
- **T4. Band promotion.** §3 mobility: an onboarded Band-C supplier with a confirmation record
  (structured quote / parsed email quote with part+price) is promoted to Band A top. Test with a
  simulated DXP confirmation (acceptance criterion 5).
- **T5. Cache policy.** §6: identity-vs-verdict split (identity/interchange edges persist;
  vendor edges become TTL'd hints that seed but never replace fresh discovery), matcher-version
  stamp + invalidation, write-gate (only Band A/B edges ever written; mock/Band-C never).
  One-time migration marks existing vendor edges stale-hint — as CODE with a tmp-fixture test,
  not a hand edit of the live file.
- **T6. Acceptance suite.** Encode spec §9 as tests: a Gusher fixture reproducing the live run's
  candidates (Sealit123 exact-PN $53.25, US Seal exact-PN, Zoro/Seals-Direct aftermarket, the
  five mocks, DXP class-match) asserting criteria 1-8. Flag-off byte-identical parity test.

## OUT OF SCOPE (spec §8 — do not touch)

A2 kit queries / A4 dims-into-queries; RFQ live sending; live inventory integration; registry
junk cleanup; any frontend build beyond the API response shape (outreach-block UI is a follow-up
task — do not attempt it overnight).

## SUCCESS = spec §9, verbatim. If a criterion cannot be met, report WHY with evidence — do not
weaken the test to pass.

## MORNING REPORT
`MORNING_REPORT_NIGHT9.md`: guardrail compliance, I1 root-cause finding (what 12.6 measured),
per-task commits, acceptance-criteria table (each §9 item pass/fail), flag-off parity proof,
unspecified decisions enumerated, morning-verification commands. NO PUSH.

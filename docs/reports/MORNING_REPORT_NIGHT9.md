# MORNING REPORT — Night 9: Evidence-Banded Ranking (RANKING_BANDS_V1)

**Status: ALL SIX TASKS COMPLETE.** Suite green at every commit; final count
**2020 passed / 73 skipped** (baseline 1923/73 + 97 new tests). NO PUSH — everything is
local on `feature/ranking-bands-overnight`.

| Pre-flight | Value |
|---|---|
| BASE_HEAD (`test/flag-on-integration`) | `4073f6b` (work branch created from it, verified via merge-base) |
| Baseline suite | **1923 passed / 73 skipped** — exact match with the brief, confirmed before any edit |
| Baseline env | `RUN_CAPTURE=1; INTAKE_TYPE_AWARE=1; SCORING_V2=1` (RANKING_BANDS_V1 unset → the whole suite runs flag-OFF) |
| Final HEAD | `b90b373` |

---

## 1. I1 root cause — what the 12.6 actually measured

**The 12.6 is a snippet-keyword-echo score, not a part-match verdict, and it
contradicts the extractor that found the part.** Chain, with file:line:

1. The Tier-3 national-specialist LLM extractor returned US Seal Manufacturing with
   `found_part_number="84004-28"` and `pn_match_status="exact_match"`
   (`enterprise_search.py:504-505`, verdict visible in `run_full.json:961,978`).
2. `_compute_suitability_score` (`sourcing_archieved/scoring.py:495`) **ignores
   `pn_match_status` entirely** and re-derives its own PN match via
   `_classify_pn_match` (`scoring.py:219-252`): `84004-28` vs requested
   `84004-28-C238CBC` is not string-equal, not normalized-equal
   (`8400428` ≠ `8400428C238CBC`), Gusher has no stemming rule, and the full
   requested PN is absent from the homepage snippet → verdict `"none"`.
3. `"none"` + a real found_pn ⇒ **0 PN/fit points AND the -30 mismatch penalty**
   (`scoring.py:632-634`) — the scorer punished the vendor *for finding the base PN*.
4. What remains (type/manufacturer/authority keyword points off a sparse homepage
   snippet, `www.ussealmfg.com`) is multiplied by the SCORING_V2 TypeGate
   (`scoring.py:665-667`; 0.7 = same-class-low-confidence) → **12.6**. Zoro's 10.5
   is the same arithmetic over `84004-28SP`.
5. Meanwhile the five seeded distributor cards carry **hardcoded** `88.0/75.0`
   (`sourcing_agent.py:1206-1207` in `_seeded_tier3_candidates`, `is_mock: True`
   at :1214 — no URL, no PN, nothing verified).
6. The 30-floor (`sourcing_agent.py:458-461`, `_apply_suitability_floor` :214)
   then rejected the two real part-finders and passed the five mocks — the inversion.

So the score measures *"does this page's search snippet textually echo the request"*.
A vendor whose evidence is an extracted PN on a low-text page scores near zero;
a fabricated seed with a constant outranks everyone. This **confirms** the spec §5
hypothesis ("likely the crude keyword scorer") — no HALT condition; its role is now
reduced to a capped Band-B ordering input (flag-on).

Investigation I2–I4 findings (ranking seam, consumer enumeration, cache paths) are in
the session transcript; key facts: `is_mock` had **no** production readers (tests/dump
scripts only), `confidence_score`'s only live read is `priceUnverified`
(`api_server.py:886`), and the cache-first read (`api_server.py:1163-1191`) skipped
discovery on **any** edge hit — the §6 frozen-verdict defect.

## 2. Per-task commits

| Task | Commit | Content |
|---|---|---|
| T1 band assignment + ordering | `26fe7f1` | `utils/procurement_agent/ranking_bands.py` (new, standalone): band A/B/C from evidence, canonical base-PN classification, evidence-quality score, A>B>C absolute ordering, onboarded-first within band, TCA tiebreak by sort stability; flag-gated fail-soft post-pass in `SourcingAgent.run()`; randomized ordering property test |
| T2 floor re-scoping | `464bc85` | Floor = Band-B-only quality bar; Band A never floor-rejected; Band-B candidates with found-PN evidence un-floored (Zoro); Band C uncapped by floor, capped by count (onboarded always included); only `suitability_below_floor` ever cleared |
| — flake fix (separate) | `f26a51f` | `run_capture` event reads ordered `ts, rowid` — pre-existing nondeterminism (timestamp-tie swap) that intermittently failed the full suite |
| T3 mock descoring + outreach shape | `4995512` | Mocks carry NO suitability/confidence (None) + provenance only; `confidence_score` replaced by evidence-derived value for all banded candidates (C3 fix); API adds `findings[]` / `outreachTargets{}` keyed on the result's `ranking_bands:v1` marker; contract test: `is_mock` in findings = failure |
| T4 band promotion | `419b6b1` | Confirmed quote with a real price promotes Band C → Band A at read time; onboarded promotion lands top of Band A; simulated DXP confirmation test |
| T5 cache policy | `65b39c9` | Write gate (only Band A/B edges; mock/C never), matcher-version stamp, edge TTL (7d, env-overridable), stale/invalidated edges = cache MISS → fresh discovery; migration as idempotent CODE on tmp fixtures |
| T6 acceptance suite | `d9c41b5` | Spec §9 criteria 1–8 on the live-run Gusher fixture + flag-off parity tests |
| docs | `b90b373` | `design/interactions.md` Night-9 section |

## 3. Acceptance criteria (spec §9) — verdict table

| # | Criterion | Verdict | Evidence (test) |
|---|---|---|---|
| 1 | Sealit123 (exact PN, $53.25) ranks #1 or #2 overall; #1 when DXP unconfirmed | **PASS** | `test_criterion_1_*` and `_1b_*` (DXP-confirmed → DXP #1, Sealit #2) |
| 2 | US Seal Manufacturing NOT rejected — Band A, surfaced | **PASS** | `test_criterion_2_us_seal_not_rejected_band_a` |
| 3 | Zoro / Seals-Direct surface in Band B, not floor-rejected at 10.5 | **PASS** | `test_criterion_3_aftermarket_surface_band_b_unfloored` |
| 4 | No `is_mock` ranked as a finding; no mock carries numbers; five seeds in outreach block only (capped) | **PASS** | `test_criterion_4_*`, plus the T3 contract test `test_pipeline_contract_no_mock_in_findings` |
| 5 | DXP named onboarded supplier in outreach block; simulated confirmation promotes to top of Band A | **PASS** | `test_criterion_5_*`, `test_simulated_dxp_confirmation_promotes_to_top_of_findings` |
| 6 | Re-run does not replay a frozen verdict; matcher-version bump invalidates vendor edges | **PASS** | `TestCacheFirstReadPolicy` (stale → discovery runs; fresh → served banded; bump → rediscovery) |
| 7 | Band ordering property-tested — no C above B, no B above A, regardless of scores | **PASS** | `TestBandOrderingProperty` (100 randomized seeds) + `test_criterion_7_*` |
| 8 | Confidence from evidence; 0-confidence ⇒ Band C — across the eval bank, not just Gusher | **PASS** | `test_criterion_8_*` (full fixture + 600 randomized candidates spanning all evidence shapes) |
| 9 | Existing suite green; Night-7 eval cases pass | **PASS** | `uv run pytest -q` → 2020 passed / 73 skipped (includes `test_intake_anchored_clarification`, the Night-7 eval-case module) |

## 4. Flag-off parity proof

- The **entire pre-existing suite (1923 tests) runs with RANKING_BANDS_V1 unset** and
  stayed green at every commit — the widest parity net available.
- `TestFlagOffParity`: flag-off pipeline result contains **no band vocabulary anywhere**
  (recursive key scan for `band/evidence_quality/banded/provenance/band_note/
  band_c_capped/edge_stale/price_stale/matcher_version/stale_hint`), the fabricated
  88.0/75.0 and both floor rejections are preserved **exactly**, the API response keys
  are exactly the legacy five, and serialization is deterministic across runs.
- Every seam is gated: `SourcingAgent.run()` post-pass (env flag, fail-soft),
  `known_parts` write/read (`_ranking_bands_on()` live read), the api_server cache
  policy (env flag), and the response extension (keyed on the **result-embedded**
  `ranking_bands:v1` marker, so stored flag-off runs can never grow new keys).
- `known_parts` flag-off writes carry no `matcher_version`; flag-off reads carry no
  `edge_stale` — asserted in `TestCacheWriteGate`/`TestEdgeStaleness`.

## 5. Guardrail compliance

- **NO PUSH** — nothing pushed; branch is local only. ✔
- **No live network / sends** — all tiers, Apollo, and contact resolution mocked;
  `EMAIL_SEND_ENABLED` untouched. ✔
- **`utils/known_parts.json` never hand-edited** — cache policy is code; migration is
  an idempotent function tested on tmp fixtures; the live file needs **no data edit**
  (version-less edges already read stale under the flag). Every cache test monkeypatches
  `_DB_PATH` to tmp. ✔
- **Do-not-touch paths untouched** — `.env`, `audit/`, purge guards, seed/demo fixtures. ✔
- **No DB rows created by tests** in shared stores — API tests run against per-test tmp
  SQLite (the existing `api` fixture); no `is_test` rows were needed. ✔
- **One commit per task, suite green at every commit** — 8 commits, each gated on a full
  `uv run pytest -q`. ✔
- **Iteration cap never approached** — no task needed more than one fix cycle.

## 6. Unspecified decisions (made tonight, flag-on only — review these)

1. **"Canonical" PN match = normalized prefix with base ≥ 6 alphanumerics**
   (`classify_pn_evidence`): `84004-28` ↔ `84004-28-C238CBC` is canonical (config-suffix
   family), `84004-28SP` is not (divergent suffix → compatible). The 6-char guard stops
   trivial prefixes claiming Band A.
2. **Band-B floor keys on PN evidence** (reconciling spec §5 "floor applies within
   Band B" with criterion 3 "Zoro un-floored at 10.5"): a Band-B candidate with a found
   compatible PN is never floored; the crude score floors only no-PN-evidence B listings.
3. **Extractor `no_match` kills PN evidence; an uncorroborated `exact_match` claim does
   not earn Band A alone** (stays at its string-derived level — conservative).
4. **Band-C cap = 5** non-onboarded (constant `BAND_C_CAP`), onboarded never counts
   against or falls to the cap; over-cap = annotate-only (`band_c_capped`), nothing removed.
5. **Onboarded signal on a candidate dict** = `is_registry_backed` or
   `merchant_type == "Arkim Network"`.
6. **Promotion trigger** = a confirmed quote whose payload carries a real `unit_price`,
   resolved via the existing quote index; computed **at read time** (quotes arrive after
   sourcing results persist), never written back. A price-less confirmation does not promote.
7. **Evidence-quality weights** (`_EQ_*` in `ranking_bands.py`) are informed defaults
   pending real-data calibration (same status as the SCORING_V2 gate values).
8. **TTL-fresh cache hits are served** (re-verified framing) rather than always
   re-discovering — the spec's parenthetical allows it; stale/invalidated = hard miss.
9. **Tier arrays still contain Band-C/mock cards flag-on** (annotate-don't-remove,
   back-compat); mocks carry no numbers and never appear in `findings`. The outreach-block
   UI (which stops rendering them as cards) is the flagged follow-up task.
10. **`priceUnverified` extension**: an explicit `price_stale` flag (banded cache path
    only) also marks a price unverified, since the band pass replaces the legacy
    1.0-confidence stale marker. Flag-off candidates never carry the key.
11. **Out-of-band fix**: `run_capture` event ordering (`f26a51f`) — a pre-existing
    timestamp-tie flake that intermittently broke the full suite twice tonight; fixed
    with a rowid tie-break rather than rerun-until-green.

## 7. Morning verification commands

```powershell
# 1. Full suite, baseline env (expects 2020 passed / 73 skipped)
$env:RUN_CAPTURE=1; $env:INTAKE_TYPE_AWARE=1; $env:SCORING_V2=1
uv run pytest -q

# 2. Just the Night-9 module (97 tests: bands, floor, mocks, promotion, cache, acceptance, parity)
uv run pytest utils/procurement_agent/tests/test_ranking_bands.py -q

# 3. Acceptance criteria only
uv run pytest "utils/procurement_agent/tests/test_ranking_bands.py::TestAcceptanceGusher" -v

# 4. Flag-off parity only
uv run pytest "utils/procurement_agent/tests/test_ranking_bands.py::TestFlagOffParity" -v

# 5. Commits
git log --oneline 4073f6b..HEAD
```

## 8. Follow-ups (not done tonight, by design)

- Outreach-block UI rendering in the React frontend (brief: explicit follow-up task).
- Evidence-quality / confidence weight calibration against captured run data.
- Running `migrate_vendor_edges_stale_hint()` against the live store is **optional**
  (absent version already reads stale); if wanted for auditability, run it once at
  flag-enable time.
- A2 kit queries / A4 dims-into-queries, RFQ live sending, live inventory integration,
  registry junk cleanup — spec §8 out of scope, untouched.

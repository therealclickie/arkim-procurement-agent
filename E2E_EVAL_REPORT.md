# E2E EVALUATION REPORT — Post-Hardening (Flags-On, Demo Readiness)

**Evaluator:** Claude Fable 5 · **Branch:** `eval/e2e-post-hardening` · **Date:** 2026-09-24
**Baseline being re-measured:** `eval/e2e-flags-on` report (2026-09-23, 16 findings) after
arc 5 demo hardening (T1–T10) merged at `756b195`.
**Status:** IN PROGRESS — Phase 0 complete. Scenarios S1–S4 pending.

This is the post-hardening re-run of the flags-on evaluation. Arc 5 claimed fixes for
F-02, F-03, F-04, F-07, F-08, F-09, F-10, F-11(badge), F-12(notation), F-15, F-16; it
deliberately left open F-05, F-06, F-13, F-14, cross-maker equivalence, and gate finding
F-A (band vs badge). This run measures which claims hold end-to-end and what is still
broken, on the same scenarios and the same pilot flag profile.

---

## 1. Verdict

*(pending — completed after S1–S4)*

---

## 2. Pilot flag profile (Phase 0.1)

Unchanged from the previous report **plus one new variable** (arc 5 T10):

| New var | Pilot value | Effect |
|---|---|---|
| `GOFER_DATA_DIR` | unset in prod (→ `<repo>/data`); set to an isolated dir for eval/demo | Relocates **all 18** store modules + `persistence` + the two `utils/` JSON stores + `api_server._HANDOFFS_PATH`. Import-bound: set before process start. **This closes F-04** — verified live below. |

Changed semantics from arc 5:
- `SES_CONFIGURATION_SET_AUTH` is now **load-bearing at boot** under `MAIL_PROVIDER=ses` +
  `SUPPLIER_ACCOUNTS_V1`: unset ⇒ refuse to start, naming the variable (closes F-03).
  Under `MAIL_PROVIDER=fake` it is not required, and auth mail is **captured** (T7).
- `NEXT_PUBLIC_API_URL` now **defaults to `http://localhost:8001`** in `next.config.ts`
  (closes F-02); no `.env.local` needed on a fresh clone.

The full profile (backend flags, mail safety, spend controls, frontend flags) is
machine-readable in `eval/e2e/harness.py` (`PILOT_FLAGS` / `MAIL_SAFETY` / `ISOLATION` /
`SPEND_CONTROLS` / `FRONTEND_FLAGS`) and is byte-for-byte the previous report's §2 set
plus `GOFER_DATA_DIR`. `DEMO_MODE` remains structurally incompatible with the pilot
profile (boot refusal with `EMAIL_SEND_ENABLED`) — unchanged, by design.

## 3. Boot commands (Phase 0.2)

```powershell
uv sync --group dev
# pilot profile env (see harness.py), plus for isolated data:
$env:GOFER_DATA_DIR = "<isolated-dir>"     # NEW (arc 5) — a real uvicorn boot can now run isolated
uv run uvicorn api_server:app --port 8001

cd frontend
# NEXT_PUBLIC_API_URL no longer needed — defaults to :8001 (arc 5 T9)
$env:NEXT_PUBLIC_SUPPLIER_SESSION_V1 = "1"
$env:NEXT_PUBLIC_NOTIFICATIONS_V1 = "1"
npx next dev --port 3000

# notification scheduler (cron in prod; nothing reminds/escalates without it)
uv run python scripts/notifications_scheduler.py escalations   # + coalesce, digest
```

**The F-04 isolation caveat is GONE.** The previous eval had to monkeypatch 18 modules;
this eval sets `GOFER_DATA_DIR=eval/e2e/data` and verified that **all 18 store modules,
`persistence`, `known_parts`, `price_db` and `api_server._HANDOFFS_PATH` resolve there
natively** — the old monkeypatch was demonstrably a no-op
(`eval/e2e/evidence/phase0_results.json` → `t10_native_isolation`, check
`hardening.F04_gofer_data_dir_native` PASS). The API is still driven in-process
(FastAPI TestClient over the exact `app` object uvicorn would serve) so the FakeProvider
outbox is inspectable in-memory.

## 4. Mode and mail-safety proof (Phase 0.3 / 0.4)

**Mode: LIVE** — `ANTHROPIC_API_KEY` + `TAVILY_API_KEY` present in `.env` (values never
read out); budget 150 external calls hard-enforced by the harness call counter.
`APOLLO_API_KEY` / `PARALLEL_API_KEY` blanked (no credit spend). Phase 0 made **0**
external calls; the only blocked DNS lookup was the deliberate `gmail.googleapis.com`
probe.

**Mail-safety proof — all layers re-demonstrated live, all PASS**
(`eval/e2e/evidence/phase0_results.json`):

| Layer | Mechanism | Result |
|---|---|---|
| 0 | DNS guard (process-wide, only api.anthropic.com / api.tavily.com / loopback) | `gmail.googleapis.com` refused |
| 1 | transport under profile = FakeProvider (in-memory outbox) | PASS |
| 2 | misconfigured to `ses`: no `AWS_REGION` ⇒ no boto3 client | `status='error' 'SES not configured'`, zero network |
| 3 | Gmail path: creds blanked ⇒ no service | `status='error' 'Gmail credentials not configured'` |
| 4 | governance allowlist (isolated DB, 5 test domains, fail-closed) | non-allowlisted → `not_allowlisted`, blocked before transport |

Positive control: allowlisted send → `sent` with `fake-…` id, captured in outbox only.

**New (arc 5 T7) auth-mail behaviour, verified:** with `SES_CONFIGURATION_SET_AUTH`
unset, FakeProvider **captures** auth mail (no tracking domain to leak through) while
SES still **refuses** — and the refusal raises exactly one `AUTH_MAIL_REFUSED` alert at
tier **ACTION_NOW**. Boot guard verified in subprocesses: `ses`+accounts+var-unset →
**refuses to start naming the variable**; var set → boots; `fake` → boots
(checks `hardening.F03_*`, all PASS).

## 5. Seeded pilot data (Phase 0.5)

Identical scheme to the previous eval, freshly seeded into `eval/e2e/data/`
(copies of `supplier_registry.sqlite` — 410 suppliers incl. DXP Enterprises
`dxpe.com` `tier1_lifecycle=onboarded` — `brand_intelligence.sqlite`,
`spec_cache.sqlite`, `known_parts.json`, `price_db.json`,
`mock_maintenance_handoffs.json`; everything else empty):

- **Buyer:** tenant `bayfoods` → `fac-stockton` (Bay Foods · Stockton, CA); known sender
  `maintenance@bayfoods.com` (still store-level only — F-05 remains open, no admin API).
- **Supplier accounts:** `dxpe.com` OWNER + ADMIN + 2 MEMBERs; three S4 accounts
  `supplier-{a,b,c}.example.com` (OWNER each, tz America/Los_Angeles).
- **Governance allowlist:** exactly the 5 scenario domains, `is_test=1`.

Evidence: `eval/e2e/evidence/phase0_results.json` (all 30 checks PASS).

---

## 6. Scenario step tables

### S1 — Breakdown, like-for-like (the anchor demo) — COMPLETE

Run: `uv run python eval/e2e/s1_breakdown_like_for_like.py` (after `phase0_pilot_config.py`).
Run id `eed47011-8276-4b4e-9536-7435efec742f`. **27 external calls** (21 Anthropic, 6 Tavily), 87s.
Evidence: `eval/e2e/evidence/s1_*.json`.

| # | Step | Expected | Observed | Verdict | vs previous eval |
|---|---|---|---|---|---|
| 1 | Email intake | run or honest clarification | `NEEDS_CLARIFICATION`, good question | DEGRADED | same |
| 1b | Self-contained reply answering everything | `RUN_CREATED` | `NEEDS_CLARIFICATION` again | **BREAK** | **F-06 still open** (out of arc-5 scope, as briefed) |
| 1b2 | Third mail taking the family-as-is exit | `RUN_CREATED` | `NEEDS_CLARIFICATION` — email loop never terminates | **BREAK** → PH-01 | F-06 unchanged |
| 1c | In-app chat fallback | run + specs | run created; specs correct (Chesterton 155, 1.875″ shaft); chat asked a NEW question (face/elastomer materials) instead of re-asking the supplied shaft size | PASS | **F-08 FIXED** (T6) — no re-ask of a supplied attr |
| 1d | Confirm intake | phase → sourcing | **200 on the FIRST plain confirm** — no 422, no `open_family` needed | PASS | **F-08's 422-names-supplied-field FIXED** (T6); previous run needed the `open_family` affordance |
| 2 | Identification | honest like-for-like, no invented PN | Chesterton 155 for Goulds 3196 MTX, `part_number=null`, phase `comparison` | PASS | same (good) |
| 3 | Tier-1 + bands | DXP + provenance + bands | DXP Tier-1 `registryBacked` `{class_gate: SEAL, is_core, onboarded}`; `sourcing_results.findings` = 8 × Band B; every candidate honestly `pnMatchLevel=none` with a **reason** (“no part number was requested…”) — the new `pnMatchReason` field (T2) is live | PASS | badge honesty now explained per row |
| 4 | RFQ via governance | 409 direct; release delivers; captured | draft `32324c3c`, direct send **409**, release → `sent`, captured to `sales@dxpe.com` | PASS | same |
| 5 | RFQ_NEW routing | exactly OWNER+ADMIN | store + delivered = exactly `{owner,admin}@dxpe.com`, MEMBERs excluded | PASS | same |
| 5b | **(new)** supplier-mail content | names the part; no UUID/internal vocab | both RFQ_NEW mails name Chesterton/155; **zero** mails carry a run UUID or internal vocabulary | PASS | **F-09 + F-10 FIXED** (T8) |
| 6 | Magic-link login | token works, HttpOnly cookie | auth mail on `eval-auth-set`, verify 200, session established | PASS | same |
| 7 | Session inbox + RfqView | visible; view recorded | inbox shows the run; `rfq_viewed=True` | PASS | same |
| 8 | Structured quote | accepted + attributed | 200 `{ok, quote_id d1f5921a, status active}` via account | PASS | same |
| 9 | Buyer sees quote | DXP `quoted` @189 | `evidenceState=quoted`, price 189.0, quoteId, “2 days” | PASS | same |
| 10 | Buyer accepts; order advances | order AT the quoted price, quote id recorded | **order `unit_price=189.0`, `source="quote"`, `status="placed"`, `quote_id` = the step-8 quote, `placed: true`** | PASS* | **F-07 FIXED** (T1) — previously an unpriced draft |
| 11 | No escalation for viewed RFQ | silence at +10 bh | reminded 0, alerted 0, outbox unchanged | PASS | same |

\* Step 10 was first recorded BREAK by a harness parsing bug (the orders endpoint
returns an `{run_id, count, orders:[...]}` envelope; the old script iterated the dict).
Adjudicated PASS from the captured evidence — `s1_step10_adjudication.json`; the script
is fixed for future runs. Nothing about the product broke.

**Bottom line S1:** the anchor demo now completes **all the way through the order** —
the one demo avoidance the previous report imposed on the buy-flow (“stop at the quoted
card”) is gone. Email intake remains the single dead channel (PH-01/F-06, known,
deliberately out of arc-5 scope). Side observation, unchanged from the previous run: the
Apollo **cache**-rescue lifted two sub-floor Tier-3 candidates (Platinum Performance
Products, suitability 1%) into the visible list (`apollo_confirmed` rescue) — annotate-
don't-remove is by design, but a 1%-suitability rescue riding a cached verdict is worth
a human eyeball (recorded as PH-08).

### S2 — Substitute honesty (SKF 6205-2RS C3) — COMPLETE

Run: `uv run python eval/e2e/s2_substitute_honesty.py`. Run id `1ec25ea0…`.
**17 external calls**, 51s. Evidence: `eval/e2e/evidence/s2_*.json`.

| # | Step | Expected | Observed | Verdict | vs previous eval |
|---|---|---|---|---|---|
| 1 | In-app intake | request captured | specs clean: SKF `6205-2RS C3`, C3 in PN + description; **substitute/backorder intent again dropped without comment** (survives only in the raw chat message) | PASS (gap) | F-14 unchanged |
| 2 | C3 preserved at intake | yes | yes | PASS | same |
| 3 | Sourcing completes | comparison | phase `comparison`; **`no_exact_match=True`** — the system no longer claims exact matches exist for this request | PASS | previously `false` (it claimed exacts) |
| 4 | Equivalence honesty | no non-equivalent as equivalent | **ZERO exact-match claims across all 27 candidates.** The exact F-11 trap rows are now badged honestly: EIS `6205-2RS` → `mismatch` “C3 requested; listing is CN/unspecified”; Motion `6205 2RSJEM` → `mismatch`; bare-domain Intech → `stem` “no resolvable listing URL — the part number cannot be verified” | **PASS** | **F-11 badge FIXED (T2)** — was BREAK-BLOCKER |
| 4b | **(new)** band vs badge (gate F-A) | note whether the BAND still lifts C3-less rows | **it does**: EIS (C3-less, badge `mismatch`, bare domain) sits in **Band A**; Motion (mismatch) in Band B — the ranking layer contradicts the badge layer | DEGRADED → PH-02 | F-A predicted by the gate, now measured live |
| 5 | Honest fallback | needs-verification presentation | 26/27 candidates carry comparison artifacts; no priced candidate without a URL; honest | PASS | same |

**Equivalence-engine inputs — post-hardening status of the S2 group:**

1. **F-11 (was BLOCKER) — FIXED at the badge layer, verified live.** No C3-less listing is
   badged exact; every verdict carries a `pnMatchReason`. The extractor's own claims
   (`pn=exact_match` visible in the sourcing log for EIS/Intech/BDS) were **overruled by
   the deterministic gate** — exactly T2's design (“the classifier is the ceiling”).
2. **F-12 (notation) — FIXED at the badge layer.** QBO's `6205-2rsh/c3` now scores
   `needs_verification` with a reason naming both designations (“2RS requested; listing
   is 2RSH — same 2RS seal family, different designation”); `-WT-` and `HT51` suffixed
   listings likewise `needs_verification`. Residual (PH-04, MINOR): Rodavictoria's
   genuinely-C3 listing scored `none` via the extractor-may-downgrade rule — the safe
   direction, but the true match still under-ranks. Radwell's echo-back (found PN
   `6205-2RS-C3` on a Timken `6205-2RS` URL) still happens but is now **neutralized in
   the unsafe direction** (kept at `none`).
3. **F-A (band vs badge) — OPEN, now live evidence (PH-02, MAJOR).** The bands still rank
   on `classify_pn_evidence`, which treats `6205-2RS` vs `6205-2RS C3` as `canonical`:
   a C3-less, badge-`mismatch`, bare-domain row lands **Band A** while the honestly-
   flagged family variant with a real listing (QBO) sits below it in Band B. Whatever
   surface sorts by band still promotes the wrong-clearance part.
4. **F-13 (clearance not a compared field) — OPEN, unchanged.** Artifacts still compare
   only `{bore_diameter, detected_type}`; no clearance row; and the artifact still does
   the type-string-vs-PN compare (“deep groove ball bearing vs 6205-2RS-C3” →
   `incompatible`), so the artifact layer contradicts the now-honest badge layer.
5. **F-14 (substitute intent) — OPEN, unchanged.** “SKF is on backorder — what can we
   get?” produced SKF listings only; the intent appears nowhere in specs or results.

### S3 — Ambiguous request (CIP-skid pressure gauge) — COMPLETE, ALL PASS

Run: `uv run python eval/e2e/s3_ambiguous_gauge.py`. Run id `…` in `s3_steps.json`.
**10 external calls**, 37s. Evidence: `eval/e2e/evidence/s3_*.json`.

| # | Step | Expected | Observed | Verdict | vs previous eval |
|---|---|---|---|---|---|
| 1 | Ambiguous ask → questions? | asks the gauge fundamentals | asks range, output, process connection — good first question. Reference type / wetted / hygienic not asked in chat | PASS (partial coverage) | same first-turn behaviour |
| 2 | Confirm with nothing established | refused | **422 `identity_insufficient`** with an honest, user-facing message (“…nothing to match a supplier's listing against. Add them in the chat, or source anyway and review the results yourself.”) | **PASS** | **F-15 FIXED (T4)** — previously 200 → priced noise ($923 gauge) |
| 3 | Partial answer (range only) | keeps asking | asks brand/model next (the identity the floor needs) | PASS | same |
| 4 | Confirm again | still refused | 422 `identity_insufficient` again | PASS | previously moot (already sourced) |
| 5 | **(new)** labelled override `source_anyway=true` | 200 + honesty markers | 200 → sourcing; run marked `spec_incomplete: true`; banner “These results have NOT been checked against your requirement…”; acknowledgement recorded; **zero candidates badged exact** (this live run returned 0 candidates — no fabricated prices) | PASS | T4 override verified E2E |

### S3b — Hygienic gate follow-up (arc 5 T5, F-16) — gate fires, but is UN-CLEARABLE in chat

The S3 run never reached the hygienic gate (the identity floor fires first), so a
follow-up run cleared the floor: *“Ashcroft 1032, 0-60 psi, on the CIP return line.”*
Run: `uv run python eval/e2e/s3b_hygienic_gate.py`. **3 external calls.**
Evidence: `eval/e2e/evidence/s3b_*.json`.

| # | Step | Expected | Observed | Verdict |
|---|---|---|---|---|
| 1 | Identity-sufficient CIP gauge | specs capture Ashcroft 1032 | captured cleanly, CIP context in description + use_case | PASS |
| 2 | Confirm past the floor | hygienic gate blocks, naming the fields | **422 `hygienic_spec_incomplete`**, `missing_attrs: [process_connection, process_connection_size, wetted_material, hygienic_certification]`, message names 3-A/EHEDG — **F-16's gate is real and fires E2E** | PASS |
| 3 | Answer all four in chat (“1.5 inch Tri-Clamp, 316L wetted, 3-A certified”), confirm again | 200 | **still 422** — `process_connection` and `hygienic_certification` remain “missing” though the user answered them; meanwhile the chat panel says “Specs look complete — review in the panel and confirm to start sourcing” | **BREAK → PH-07 (MAJOR)** |

**Why (root cause, structural):** the gate accepts spec keys
`process_connection|connection|connection_type` and
`hygienic_certification|certification|hygienic_cert`
(`utils/hygienic_context.py` `_FIELD_SOURCES`), but **`AssetSpecs`
(`utils/models.py`) has no such fields** — the extractor stored the answers into
`connection_size` (“1.5 inch Tri-Clamp”) and `material_spec` (“316L…”), which clears
only two of the four. Two of the gate's required fields are structurally un-fillable by
the intake chat, so the hygienic gate can only ever be exited via the `source_anyway`
override. The arc-5 unit tests passed because they set the dict keys directly — this is
precisely the cross-arc seam a flags-on E2E run exists to catch.

*(S4 pending)*

---

## 7. Findings

*(pending — numbered PH-xx, with the previous report's F-xx cross-referenced)*

---

## 8. Seam inventory

*(pending)*

## 9. Demo guidance

*(pending)*

## 10. Human UI checklist

*(pending)*

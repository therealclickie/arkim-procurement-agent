# E2E EVALUATION REPORT — Post-Hardening (Flags-On, Demo Readiness)

**Evaluator:** Claude Fable 5 · **Branch:** `eval/e2e-post-hardening` · **Date:** 2026-09-24
**Baseline being re-measured:** `eval/e2e-flags-on` report (2026-09-23, 16 findings) after
arc 5 demo hardening (T1–T10) merged at `756b195`.
**Status:** COMPLETE — Phase 0 + S1–S4 (+ S3b hygienic probe, S1c quote-form probe) +
seam inventory + UI checklist. Mode LIVE, **57/150 external calls**.
Flags-off baseline re-measured this run: `uv run pytest -q` → **3205 passed, 73 skipped**
(matches arc 5's build report).

This is the post-hardening re-run of the flags-on evaluation. Arc 5 claimed fixes for
F-02, F-03, F-04, F-07, F-08, F-09, F-10, F-11(badge), F-12(notation), F-15, F-16; it
deliberately left open F-05, F-06, F-13, F-14, cross-maker equivalence, and gate finding
F-A (band vs badge). This run measures which claims hold end-to-end and what is still
broken, on the same scenarios and the same pilot flag profile.

---

## 1. Verdict

**Demo-ready with named avoidances — materially stronger than the previous run, and the
avoidance list has changed shape.** Ten of the eleven arc-5 fixes hold up end-to-end
under the full flags-on pipeline: the S1 anchor now completes **through the order**
(placed at the accepted $189 quote with quote-id provenance — the old F-07 stop-point is
gone), the old BLOCKER class is closed at the badge layer (zero exact claims on the C3
bearing run, per-row reasons, bare domains denied), the sufficiency gate refuses
spec-less confirms with an honest message and a labelled override, supplier mail names
the part and leaks nothing, the config traps (F-02/03/04) are closed and were verified
live, and S4's alert discipline passed every check again. Three things still need
steering: (1) **the hygienic gate fires but cannot be satisfied through the chat**
(PH-01, new — its required fields don't exist in the extractor's schema, so answering
its question loops; the panel simultaneously says “specs look complete”); (2) **the
band layer and the comparison artifact still contradict the honest badge** on
bearing-style runs (PH-02/F-13 — a wrong-clearance part can sit in Band A while its own
badge says mismatch); (3) **email intake still cannot complete a family request**
(F-06, untouched by arc 5, re-confirmed). Substitute intent is still silently dropped
(F-14). With those steered around, the demo story is honest, repeatable, and now
includes the buy-flow ending it previously had to avoid.

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
`APOLLO_API_KEY` / `PARALLEL_API_KEY` blanked (no credit spend).

**Total external calls: 57 of 150** — Phase 0: 0; S1: 27 (21 Anthropic / 6 Tavily);
S2: 17; S3: 10; S3b: 3; S4: 0; S1c: 0. No other host was ever contacted; the only
blocked DNS lookup all evaluation was the deliberate `gmail.googleapis.com` probe in
the Phase-0 proof (per-process network summaries in each `*_steps.json`).

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

### S4 — Supplier silence and alert discipline — COMPLETE, ALL PASS

Run: `uv run python eval/e2e/s4_silence_and_alerts.py`. **0 external calls.**
Evidence: `eval/e2e/evidence/s4_*.json`. Time driven ONLY via explicit `now` into
`notifications.run_escalations` / `run_coalesced_sends`; RFQ release Friday
2026-09-25 15:00 PT. **The bounce was injected through the store-level path
(`notifications.apply_delivery_event`), which bypasses SNS webhook signature
verification — deliberately; the webhook path has its own unit coverage.**

| # | Step | Expected | Observed | Verdict |
|---|---|---|---|---|
| 1 | Friday 15:00 PT release to A, B, C | 3 RFQ_NEW mails, one per OWNER | exactly 3 | PASS |
| 2 | A views+replies; C hard-bounces | suppression + ACTION_NOW (sole contact) | confirmed (`sole_contact: true`) | PASS |
| 3 | Weekend | zero sends across 3 scheduler passes | outbox delta 0 (considered 5 each pass — deferred, not dropped) | PASS |
| 4 | Monday reminder | ONE consolidated, B only, business hours | Mon 09:00: nothing; Mon 11:00: exactly one, `owner@supplier-b`, subject “Reminder: quote request waiting — Goulds 3196-seal”; rerun adds none | PASS |
| 5 | Escalation | per-account, QUEUE, once | Mon 15:00: one RFQ_ESCALATION (B, QUEUE); reruns + Tuesday add nothing; no supplier mail | PASS |
| 6 | Totals | 4 emails, 2 alerts, nothing dropped | **4 emails** (3 RFQ_NEW + 1 reminder), **2 alerts** (B QUEUE, C ACTION_NOW); A `cancelled` (resolved), B `reminded+escalated`, C `BOUNCED` | PASS |

Alert discipline remains the strongest part of the system — identical to the previous
run, no regression from arc 5's notification-template changes (T8).

### S1c — Public quote form (previously-unreached seam, GET side) — PASS

Run: `uv run python eval/e2e/s1c_public_quote_form.py`. **0 external calls.**
The `/quote/{token}` link minted into S1's RFQ mail resolves: `state: live`, honest
request description (Chesterton, PN `null`, qty 1), correct supplier attribution
(DXP), shows the existing active $189 quote, expiry date; an invalid token → 404.
The **POST side was deliberately not exercised** (it would supersede the S1 quote and
disturb the seeded UI-walk data) — recorded as partial coverage in the seam inventory.
Evidence: `eval/e2e/evidence/s1c_public_quote_form.json`.

---

## 7. Findings

### 7a. Previous findings FIXED and verified live this run

| Prev | Arc 5 task | Verified by | Evidence |
|---|---|---|---|
| F-02 (frontend :8000 default) | T9 | static check, no stale 8000 | `phase0_results.json` `hardening.F02_*` |
| F-03 (silent auth-mail refusal) | T7 | boot refusal names the var; FakeProvider captures; SES refusal raises one ACTION_NOW alert | `phase0_results.json` `hardening.F03_*` |
| F-04 (no data-dir override) | T10 | `GOFER_DATA_DIR` natively isolated all 18 modules + persistence + JSON stores + handoffs path; the eval's old monkeypatch was a no-op | `phase0_results.json` `t10_native_isolation` |
| F-07 (quote never prices order) | T1 | S1 order: `unit_price=189.0`, `source="quote"`, `status="placed"`, `quote_id` recorded | `s1_step10_accept_order.json` + adjudication |
| F-08 (variant guard re-asks) | T6 | S1 in-app: no re-ask of supplied shaft size; plain confirm 200 first try (no `open_family` needed) | `s1_step1c/1d` |
| F-09 (generic RFQ_NEW) | T8 | both RFQ_NEW mails name Chesterton/155 | `s1_step5b_mail_content.json` |
| F-10 (FYI leaks UUID/vocab) | T8 | zero supplier-facing mails with UUID or internal vocabulary | same |
| F-11 (C3-less badged exact) — **was the BLOCKER** | T2 | S2: **0 exact claims in 27 candidates**; the trap rows badged `mismatch` with reasons; bare domains denied exact grade; extractor's `exact_match` claims overruled | `s2_step4_candidate_analysis.json` |
| F-12 (notation defeats matcher) | T3 | 2RSH → `needs_verification` naming both designations; WT/HT51 likewise | same |
| F-15 (no sufficiency gate) | T4 | 422 `identity_insufficient` twice; `source_anyway` override works with banner + `spec_incomplete` + no exact badges | `s3_step2/4/5` |
| F-16 (no hygienic awareness) | T5 | gate fires 422 `hygienic_spec_incomplete` naming the four fields — **but see PH-01** | `s3b_step2_confirm.json` |

### 7b. NEW findings (post-hardening)

- **PH-01 (MAJOR, S3b) — The hygienic gate cannot be cleared through the chat.**
  The gate's field sources (`process_connection|connection|connection_type`,
  `hygienic_certification|certification|hygienic_cert` — `utils/hygienic_context.py`)
  do not exist in `AssetSpecs` (`utils/models.py`), so the intake extractor can never
  fill two of the four required fields. A user who answers every question verbatim
  (“1.5 inch Tri-Clamp, 316L wetted, 3-A certified”) stays blocked, while the chat
  panel says “Specs look complete — review in the panel and confirm to start
  sourcing.” Only exit: the `source_anyway` override, which then brands honest results
  as unchecked. Repro: `uv run python eval/e2e/s3b_hygienic_gate.py` (step 3).
  Evidence: `s3b_step3_answered.json`. (The arc-5 unit tests set the spec keys
  directly, which is why this passed flags-on unit testing.)
- **PH-02 (MAJOR, S2) — The band layer contradicts the now-honest badge layer**
  (gate finding F-A, measured live). `classify_pn_evidence` still scores `6205-2RS`
  vs `6205-2RS C3` as `canonical`: EIS Inc. — C3-less, badge `mismatch`, **bare
  domain** — lands **Band A**, above the honestly-flagged true-family listing (QBO,
  Band B). Any surface sorting by band still promotes the wrong-clearance part.
  Repro: `uv run python eval/e2e/s2_substitute_honesty.py` step 4b.
  Evidence: `s2_step4b_band_vs_badge.json`.
- **PH-03 (MINOR, S2) — The extractor-may-downgrade rule buries a genuine match.**
  Rodavictoria's listing (URL literally `…/6205-2rs-c3-skf…`) scored `none` with
  reason “the extractor reports a weaker match than the part numbers alone suggest”.
  Safe direction, but the true match under-ranks; same class as the residual Radwell
  echo-back (found PN echoed on a Timken `6205-2RS` URL — correctly kept at `none`).
- **PH-04 (MINOR, S1/S2) — Sub-floor candidates rescued on cached Apollo verdicts.**
  S1: Platinum Performance Products rescued at suitability **1%**; S2: VXB at 24%,
  Baker at 27% — all `apollo_confirmed` from cache with `APOLLO_API_KEY` blank.
  Annotate-don't-remove is by design (CLAUDE.md §9), but a 1%-suitability rescue is
  effectively noise reaching the buyer's list; consider a rescue floor.
  Evidence: S1/S2 sourcing logs in the scenario outputs; `s1_step3_bands.json`.

### 7c. Previous findings still OPEN (unchanged, deliberately out of arc-5 scope)

- **F-06 (MAJOR)** — email intake cannot complete a family-level request; three
  progressively complete mails all `NEEDS_CLARIFICATION`; no run created (re-confirmed
  live this run, S1 steps 1–1b2, `s1_step1b2_intake_third_turn.json`).
- **F-13 (MAJOR)** — clearance is still not a comparison-artifact field, and the
  artifact still compares the type string against the PN (“deep groove ball bearing vs
  6205-2RS-C3” → `incompatible`), so the artifact layer now **contradicts the honest
  badge layer** on the same card (re-confirmed, `s2_step4_candidate_analysis.json`).
- **F-14 (MAJOR)** — substitute/backorder intent is still dropped silently; it survives
  only in the raw chat message (re-confirmed, `s2_step3_run_detail.json`).
- **F-01 (MINOR)** — still no capture-only mail mode (safety = 4-variable configuration).
- **F-05 (MINOR)** — still no admin API for intake known senders (store-level seed only).

---

## 8. Seam inventory (Phase 2)

| # | Seam | Exercised in | Post-hardening result |
|---|---|---|---|
| 1 | email/SMS adapter → intake consumer | S1 | works mechanically; **email dead-ends on family requests (F-06, unchanged)** |
| 2 | intake consumer → run creation | S1 | never reached on email (F-06); works in-app |
| 3 | chat IntakeAgent → specs merge → confirm guards | S1, S3, S3b | **improved**: variant guard honours supplied attrs (T6); identity floor live (T4); **hygienic gate fires but is un-clearable in chat (PH-01)** |
| 3b | **(new)** identity floor → `source_anyway` override → spec-incomplete branding | S3 | PASS — banner + ack + no exact badges |
| 3c | **(new)** hygienic context → confirm gate → chat answer loop | S3b | **BREAK (PH-01)** — gate fields not in the extractor's schema |
| 4 | confirm-intake → background sourcing | S1, S2, S3 | PASS (inline under TestClient) |
| 5 | SourcingAgent → ranking-bands annotation | S1, S2 | PASS mechanically; **band semantics contradict badges (PH-02)** |
| 6 | banded result → TIER1_V2 registry re-derive | S1 | PASS — DXP with class-gate explanation |
| 7 | stored result → run-detail transform (findings/outreach/quote overlay) | S1, S2 | PASS — `pnMatchReason` (new field) present per row |
| 8 | rfq-draft → candidate snapshot → recipient resolution | S1 | PASS |
| 9 | draft approve → send governance (409 + release queue) | S1 | PASS |
| 10 | `rfq_send` → ledger → quote-token mint → `{quote_link}` | S1, S1c | PASS — and the minted link now **resolves** (S1c) |
| 11 | `rfq_send` → RFQ_NEW fan-out → OWNER+ADMIN routing | S1, S4 | PASS |
| 11b | **(new)** `rfq_send` → run-specs read → mail identity (T8) | S1 | PASS — mail names the part, no leaks |
| 12 | notifications → governance/caps → delivery gate → transport | S1, S4 | PASS |
| 13 | magic-link mail → token → verify → session | S1 | PASS |
| 14 | session → requests inbox → RfqView write | S1 | PASS |
| 15 | RfqView → escalation suppression | S1, S4 | PASS |
| 16 | session quote POST → quote_store | S1 | PASS |
| 17 | quote_store → buyer run-detail overlay | S1 | PASS |
| 18 | accepted quote → order (select/approve/execute) | S1 | **PASS — F-07 fixed: quote-priced, quote-id-stamped, PLACED** |
| 18b | **(new)** quote resolution inside `_selection_for_order` (run_id+domain join) | S1 | PASS |
| 19 | delivery event → suppression → alert tier | S4 | PASS (store-level; **SNS signature seam still unit-tested only**) |
| 20 | scheduler (`--now`) → escalations/coalesce | S4 | PASS |
| 21 | **(new)** boot guard (SES/auth-set/accounts matrix) | Phase 0 | PASS (subprocess probes) |
| 22 | **(new)** public `/quote/{token}` GET (form payload, invalid token) | S1c | PASS (GET only — **POST/submission still unproven E2E**, by choice) |

**Seams still unreached (unchanged from previous report, still findings in themselves):**
public quote-form **POST**; `/api/portal/{token}` claim portal; email reply parsing
(`process-replies`); Apollo **live** validation (cache-rescue path observed only,
PH-04); SES webhook with signed SNS envelope; admin quote review lane (`review →
active`); basket/group approval, reorder, impact endpoints; the React frontend's
rendering of all of the above (Phase-3 checklist below).

## 9. Demo guidance

**Show live, honestly (expanded from the previous report):**
- The full S1 anchor **including the order click**: in-app chat → confirm (now clean,
  no variant-guard workaround) → DXP Tier-1 with provenance → RFQ draft → 409 →
  release queue → magic-link login → inbox → structured quote → buyer card “quoted ·
  $189 · 2 days” → **select → approve → execute → order placed at $189 with quote
  provenance**. The previous “stop at the quoted card” avoidance is lifted.
- The refusal story is now a *feature*: confirm an under-specified request and show
  the honest 422 (“nothing to match a supplier's listing against”), then the labelled
  `source_anyway` override with its banner. This demonstrates spec discipline.
- S2's badge honesty: show that a C3 bearing request yields **zero** exact claims and
  per-row reasons (“C3 requested; listing is CN/unspecified”). This was the old
  BLOCKER; it is now a strength — at the badge level.
- The S4 discipline story (weekend silence → one reminder → one QUEUE escalation →
  bounce ACTION_NOW), unchanged and solid.

**Still steer around:**
- **Email intake** (F-06) — unchanged; do not demo, describe as “in pilot wiring”.
- **Anything that sorts or filters by evidence band on a bearing-style request**
  (PH-02): a knowledgeable manager can still spot a wrong-clearance part sitting in
  Band A while its own badge says mismatch. Don't open the band explanation on S2-type
  runs.
- **The comparison artifact on bearing cards** (F-13): it still says “incompatible”
  for reasons unrelated to clearance — contradicting the badge on the same card.
- **The hygienic question flow past the first refusal** (PH-01): the gate's question is
  a great moment; *answering it* dead-ends. Show the question, then use a
  fully-specified request — or the override — not the answer loop.
- **Substitute requests** (“what else can we get”, F-14): still answered with the
  original brand only, silently.

**Fix before a real plant manager uses it unsupervised:** PH-01 (hygienic loop —
either add the two spec fields to `AssetSpecs`/the extractor or widen
`_FIELD_SOURCES`), PH-02/F-13 (make band + artifact agree with the badge), F-06
(email loop), then F-14 (substitute intent).

## 10. Human UI checklist (Phase 3 — ≤25 minutes)

Playwright is not in the repo; manual walk. **Setup (5 min):**

```powershell
# Terminal 1 — backend on :8001, isolated data, mail captured & PRINTED here
uv run python eval/e2e/ui_walk_server.py
# Terminal 2 — frontend (NEXT_PUBLIC_API_URL now defaults to :8001 — arc 5 T9)
cd frontend
$env:NEXT_PUBLIC_SUPPLIER_SESSION_V1 = "1"
$env:NEXT_PUBLIC_NOTIFICATIONS_V1 = "1"
npm run dev
```

Seeded state: S1 run `eed47011…` (DXP quoted $189, **order placed at $189 with
quote id**), S3 run spec-incomplete with banner, S3b run blocked at the hygienic
gate, S4 accounts + alerts. Magic-link tokens print in Terminal 1. Admin token:
`eval-admin-token-e2e`. Logins: `owner@dxpe.com` / `admin@dxpe.com` (OWNER/ADMIN),
`member1@dxpe.com` (MEMBER).

| ✓ | Check (URL) | What proves it |
|---|---|---|
| ☐ | `http://localhost:3000` — open S1's run | DXP card “quoted · $189 · 2 days”; order section shows a **placed order at $189** (arc 5 T1) — and no claim beyond the data |
| ☐ | Same run, order provenance | The order/receipt view references the quote (price source “quote”), NOT a listing price |
| ☐ | Open the S3 run | The spec-incomplete banner (“results have NOT been checked against your requirement…”) renders; no exact badges anywhere |
| ☐ | Open the S3b run, read the last agent turn | The panel says “Specs look complete — confirm” while Confirm returns the hygienic 422 — **observe PH-01 on screen**; check the refusal renders its message, not a raw error |
| ☐ | `http://localhost:3000/supplier/login` → `owner@dxpe.com` → link from Terminal 1 | Verify page **waits for an explicit “Continue” click** (does not consume the token on load) |
| ☐ | After login, DevTools → Application → Cookies | `gofer_supplier_session` is **HttpOnly**; `document.cookie` in Console does NOT contain it |
| ☐ | `/supplier/requests` at phone width (390px) | Inbox usable; S1 request shows Chesterton; seen-state consistent |
| ☐ | Open request → quote form at phone width | Form usable; submitting a second quote says exactly what happened (supersede language), and the buyer card updates — “live to the buyer” is true. *(Do this LAST — it supersedes the $189 quote.)* |
| ☐ | `/supplier/profile` and `/supplier/members` as `member1@dxpe.com` | Member cannot manage members/rfq-contacts; polite 403s, no fake success |
| ☐ | `/admin` (token above) → send-governance | Allowlist = exactly the 5 eval domains; release queue empty; suppression shows `owner@supplier-c.example.com` |
| ☐ | Admin → notification alerts | S4's 2 alerts render with tiers (QUEUE escalation supplier-b, ACTION_NOW suppression owner@supplier-c) **plus** the Phase-0 `AUTH_MAIL_REFUSED` ACTION_NOW probe alert — counts match evidence, nothing invented |
| ☐ | Any screen | No screen claims a send FakeProvider didn't capture; no supplier mail shows a run UUID (arc 5 T8) |

Stop both terminals when done (Ctrl+C; verify :8001 released).

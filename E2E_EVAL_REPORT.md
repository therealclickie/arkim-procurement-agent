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

*(pending)*

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

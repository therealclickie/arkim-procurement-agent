---
module: api_server.py
star: yes
loc: 1430
loc_verdict: egregious
verdict: 1 HIGH (H1 dual-approver, deferred-pending-auth) + D2 med + bloat
findings: { high: 1, med: 1, low: 0, bloat: 1 }
---

# api_server.py — ★ FastAPI surface (the durable consequential surface)

*Reviewed by the Cowork pass: consequential endpoints (select/approve/execute/confirm/place-order/process-replies) + the admin gate read in full. The ~22-endpoint surface is large; non-consequential CRUD endpoints reviewed lighter.*

**1. What it does.** ~22 REST endpoints over `utils/`, consumed by the React frontend: run lifecycle, intake, sourcing trigger, approval, execute/order, buyer-loop (review/confirm/place-order), impact/reorder, admin (token-gated).

**2. Correctness.** Mostly sound; the consequential gating has one HIGH gap that is honestly deferred (not a fake gate):

- **H1 dual-approver routing (HIGH, deferred-pending-auth).** `approve_run` (line ~1072) appends the approval then **unconditionally** sets `Phase.APPROVED` — one approval approves any dollar amount; `select-candidate` (line ~1052) stores a thin `{candidate_id,tier}` with **no `_approval_path`**, so the dual-approver rule from `approval_rules` is never consulted on this surface. This is *absence of enforcement* (docstring: "Phase 3 will implement the dual-approver routing"), not a body-trusting pseudo-gate. The order-placement half of the original H1 **is** fixed (`_execute` phase guard, `20df65c`). For PRODUCTION_READINESS this is a **blocker** unless auth ships with it.
- **D2 customer-surface scoping (MED, deferred-pending-auth).** `/api/orders`, `/api/impact`, `/api/reorder`, `/api/sites/*`, `/api/review-items/*`, run endpoints are global/ungated — fine for prototype, must bind to tenant/buyer when auth lands. No partial filter creating false isolation (verified honest deferral).

  *Sound, verified:* the admin gate (`require_admin`, line ~1408) is correctly fail-closed — `secrets.compare_digest`, 401 no-header / 403 mismatch / 503 when `ARKIM_ADMIN_TOKEN` unset. `confirm` is the only customer-path `price_db` write and requires explicit human action (no auto-confirm). `_run_sourcing_background` advances to `Phase.ERROR` on failure (honest).

**3. Efficiency.** No obvious redundant external calls in the endpoint layer.

**4. Clarity.** Endpoints are readable individually.

**5. Bloat.** `loc_verdict: egregious` (1430 code lines in one file). Extractable: split into routers by concern (runs / approval / buyer-loop / admin / impact). This is the deferred `api_server.py` decomposition already noted in CLAUDE.md §4 — raise as **B** but it's a coverage-gated refactor, not a quick fix.

**6. Findings.**
- `api_server.py:~1052 select-candidate` + `~1072 approve_run` · dual-approver requirement not enforced on the durable surface · **HIGH (deferred-pending-auth)** · **proposed (behaviour change, needs auth):** `select-candidate` persists the computed `_approval_path` (mirror `Orchestrator.select_candidate`), and `approve_run` advances to APPROVED only when distinct approvals ≥ `approvers_required`, else `PENDING_SECOND_APPROVAL`. Land with the auth/identity layer (§4.1 cluster).
- customer endpoints · global/ungated · **MED (deferred-pending-auth)** · bind to tenant/buyer claims when auth lands.
- whole file · 1430 LOC · **B** · decompose into routers (deferred refactor, CLAUDE.md §4) · behaviour-preserving when done carefully.

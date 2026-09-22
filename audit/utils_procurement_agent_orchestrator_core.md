---
module: utils/procurement_agent/orchestrator/core.py
star: no
loc: 401
loc_verdict: acceptable
verdict: 2 findings (M1 med, §4.5 low) — both deferred/known
findings: { high: 0, med: 1, low: 1, bloat: 0 }
---

# utils/procurement_agent/orchestrator/core.py — run orchestrator (Streamlit path)

*Reviewed by the Cowork pass (read in full).*

**1. What it does.** Coordinates a SourcingRun through its phases, validates transitions, persists state, writes audit log. Drives the Streamlit harness; the FastAPI surface re-implements the consequential transitions separately.

**2. Correctness.** Coordination is sound (transition validation, audit on every move, fail-soft audit). Two known issues, both already tracked and deferred:

- **M1 (MED, deferred-pending-auth).** `submit_approval` advances `PENDING_FIRST → SECOND → APPROVED` on a **count** of approval rows with no distinct-approver check — "2 approvers required" can be satisfied by one identity approving twice. Root cause is the missing auth/identity primitive (§4.1 cluster). *Behaviour change to fix (needs auth).*
- **§4.5 divergence (LOW, deferred).** `_stub_sourcing` catches a `SourcingAgent` exception and advances to `COMPARISON` with an error result — masking a hard sourcing failure as "no candidates." The FastAPI path advances to `Phase.ERROR` (honest). Low urgency while Streamlit is the throwaway harness. *Behaviour change to reconcile.*

**3. Efficiency.** Re-reads run state per public method (`get_run`) — acceptable; no external-call redundancy.

**4. Clarity.** The Phase-1 stub handlers that auto-advance are clearly labelled "for test suite compatibility"; production uses the explicit methods. Fine.

**5. Bloat.** `acceptable` LOC; no extractable dead units.

**6. Findings.**
- `core.py:~195 submit_approval` · count-based dual-approval, no distinctness · **MED** · deferred-pending-auth (§4.1); proposed distinct-approver check lands with the auth layer (see `api_server.md` H1 diff for the shape) · behaviour change.
- `core.py:~296 _stub_sourcing` · sourcing failure masked as COMPARISON · **LOW** · proposed: advance to `Phase.ERROR` matching api_server · behaviour change (low urgency).

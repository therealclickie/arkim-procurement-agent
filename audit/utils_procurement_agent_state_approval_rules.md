---
module: utils/procurement_agent/state/approval_rules.py
star: no
loc: 63
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/procurement_agent/state/approval_rules.py — approval routing

*Reviewed by the Cowork pass (read in full). Consequential-adjacent: it decides how many approvers a purchase needs.*

**1. What it does.** Config-driven approval routing: pick the highest-threshold rule ≤ total; fall back to the lowest rule (the $0 baseline) below all thresholds, or `DEFAULT_RULES` when a facility has none.

**2. Correctness.** Logic is sound and the boundary handling (`<=`) is correct. Roles are display labels only — RBAC is not enforced here (by design, prototype). One robustness note: `total_usd` is assumed numeric; the caller (`core.select_candidate`) coerces with `float(... or 0.0)`, so a `None`/missing total degrades to the $0 baseline (least-strict) rather than crashing — acceptable, but worth knowing it fails *open* (1 approver) on a malformed total.

**3. Efficiency.** One DB read per evaluation; fine.

**4. Clarity.** Clear.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound (logic).**
- *Cross-ref / deferred:* RBAC enforcement of `approver_role` is the §4.1 auth-dependent cluster (with M1 distinct-approver in `core.py` and H1 dual-approver routing in `api_server.py`). Not a defect in this module; flagged where the enforcement gap actually lives.

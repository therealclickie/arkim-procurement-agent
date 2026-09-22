---
module: utils/reorder.py
star: no
loc: 83
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/reorder.py — reorder forecast (not in §2 inventory; included as extra coverage)

*Reviewed by the Cowork pass (read in full). Not listed in brief §2 — included because the `/api/reorder` endpoint exposes it.*

**1. What it does.** Pure per-part reorder forecast from the customer's own order history (cadence from ≥2 purchases → next-due projection). No external data.

**2. Correctness.** Sound. Parts with <2 dated purchases are omitted (no fabricated cadence); only `_PURCHASED_STATUSES` count; ISO parse is fail-soft. Pure function with a thin gather layer.

**3. Efficiency.** Pure computation; the gather reads the orders store once.

**4. Clarity.** Clear. Minor: loop var `os` shadows the stdlib name locally (line ~51) — harmless (module not imported here), not worth a finding.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound.**
- *Note (trivial):* `datetime.utcnow()` is deprecated in 3.12+; harmless on 3.11. Mention only.

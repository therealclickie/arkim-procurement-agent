---
module: utils/orders.py
star: yes
loc: 262
loc_verdict: acceptable
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/orders.py — ★ order state machine

*Reviewed by the Cowork pass (read in full).*

**1. What it does.** Capture-and-track order lifecycle (`draft→placed→confirmed→shipped→received`, `cancelled` off-ramp) over `data/orders.sqlite`. No payment / PO transmission / external action.

**2. Correctness.** Sound. `ALLOWED_TRANSITIONS` enforces the machine — skip-ahead/backward/un-cancel/re-place rejected. `place_order` is the only path to `placed` and refuses a non-draft or a price-less order (can't-place-without-price). `update_order_status` refuses `placed`/`cancelled` targets so the dedicated gated paths aren't bypassed. All writes fail-soft. No auto-placement.

**3. Efficiency.** One connection per call (now closed — see L1); fine for this access pattern.

**4. Clarity.** Clear, well-documented state machine.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound.**
- *Resolved & verified:* **L1** (commit `732b725`) — `contextlib.closing` now wraps all four `_get_conn()` sites (verified: import present, 4 sites wrapped). The same leak persists in the sibling stores `supplier_registry.py` / `brand_intelligence.py` — see those files (finding **N1**).

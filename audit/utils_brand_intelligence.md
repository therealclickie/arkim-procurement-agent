---
module: utils/brand_intelligence.py
star: no
loc: 480
loc_verdict: acceptable
verdict: 2 findings (N1 low, N2 bloat)
findings: { high: 0, med: 0, low: 1, bloat: 1 }
---

# utils/brand_intelligence.py — LLM manufacturer-relationship cache (not in §2 inventory; included)

*Reviewed by the Cowork pass: the cache read/write path, the LLM payload mapping, and N1/N2 read directly. Not listed in brief §2 — included because `sourcing_agent`/intake depend on it.*

**1. What it does.** Cached manufacturer-relationship intelligence (subsidiaries, distributors, competitors, niche terms): seeded data first, then a fail-soft LLM discovery with TTL, persisted in sqlite.

**2. Correctness.** Sound. LLM discovery returns `None` on no-key/error → callers get an empty structure, never a raise; cost tracking silent-fails by design; seeded data takes priority over LLM (no fabrication). TTL + `_touch` on read are intentional.

**3. Efficiency.** Cache-first with TTL avoids repeat LLM spend — correct.

**4. Clarity.** Readable.

**5. Bloat.** One dead probe (N2 below). LOC `acceptable`.

**6. Findings.**
- `brand_intelligence.py` (`get_brand_relationships`, `all_cached_entries`, `invalidate`) · same unclosed-`_get_conn()` pattern as **N1** in `supplier_registry` · **LOW (resource)** · **proposed (behaviour-preserving):** wrap in `contextlib.closing` (fold into the N1 commit).
- `brand_intelligence.py:283` · `payload.get("competitors") or payload.get("common_competitors")` — the LLM prompt (lines 47, 59) only ever defines `common_competitors`, so the `"competitors"` probe is always falsy = dead code · **B (bloat)** · **proposed diff (behaviour-preserving):**
  ```diff
  -                "common_competitors": payload.get("competitors") or payload.get("common_competitors") or [],
  +                "common_competitors": payload.get("common_competitors") or [],
  ```

---
module: utils/price_db.py
star: yes
loc: 65
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/price_db.py — ★ price write path

*Reviewed by the Cowork pass (read in full).*

**1. What it does.** JSON-backed price cache keyed by `(manufacturer, part_number) → vendor → entry`; sources `live` (search) / `rfq` (confirmed quote). `save_price`, `get_cached_prices` (freshness window), `all_entries`.

**2. Correctness.** Sound. `_make_key` composites `manufacturer.lower()|PART_NUMBER.upper()` — the CLEANUP §3.3 PN-collision risk is closed; legacy PN-only keys cleanly miss and re-populate. `get_cached_prices` swallows only per-entry `KeyError/ValueError` (malformed rows skipped), not whole-file errors. The only consequential write is via the human-confirm path (F3); no auto-write.

**3. Efficiency.** Full-file load per op — acceptable at prototype scale; flag only if the file grows large (not now).

**4. Clarity.** Clear.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound.**
- *Resolved & verified:* **L2** (`732b725`) — `_make_key` now `(part_number or '')`; a `None` PN no longer raises `AttributeError`. **§3.3** composite key verified present; CLEANUP §3.3 can be marked resolved.

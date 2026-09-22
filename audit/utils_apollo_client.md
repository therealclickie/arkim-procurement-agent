---
module: utils/apollo_client.py
star: no
loc: 234
loc_verdict: acceptable
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/apollo_client.py — Apollo client (paid; consequential, not ★ in §2)

*Reviewed by the Cowork pass (read in full). Consequential because `people_match`/`org_enrich` spend Apollo credits.*

**1. What it does.** Thin fail-soft wrapper over Apollo org-enrich + people-search/match, used by the Tier 3 clarifier.

**2. Correctness.** Sound. `enabled` is `bool(api_key)`; every method no-ops (returns `None`/`[]`) when the key is unset, and wraps the HTTP call in `try/except → None/[]` with an explicit timeout and `raise_for_status()`. A miss vs. error are distinguished in logs; never raises into the pipeline. Credit model documented (org_enrich 1 on match; people_search free; people_match 1).

**3. Efficiency.** Correct credit discipline — search is free, match is gated to a single chosen contact. No redundant calls in the client itself (cache-first is the caller's job; see CLAUDE.md §9).

**4. Clarity.** Clear; `_clean_domain` mirrors `supplier_registry._normalize_domain` (intentional duplication to keep the client standalone — documented).

**5. Bloat.** The `_clean_domain` duplication is deliberate and documented (standalone client); not flagged.

**6. Findings.** **No issues found — sound.** Not invoked during this audit (no key, no target — paid-call constraint honored).

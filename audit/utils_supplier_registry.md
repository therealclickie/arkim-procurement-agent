---
module: utils/supplier_registry.py
star: no
loc: 742
loc_verdict: bloated
verdict: 1 finding (N1 low) — bloated but cohesive
findings: { high: 0, med: 0, low: 1, bloat: 0 }
---

# utils/supplier_registry.py — supplier store (suppliers / Apollo / contacts / sent / review queue)

*Reviewed by the Cowork pass: SQL-safety and the write paths read directly; N1 verified across all call sites. Full line-by-line of every helper deferred to the fresh per-module pass — scope noted honestly.*

**1. What it does.** Raw-sqlite3 store for suppliers, Apollo enrichment, contacts, sent messages, and the inbound review queue; the source of truth contact resolution and RFQ send read.

**2. Correctness.** Write integrity is good. Every dynamic `UPDATE … SET {set_clause}` builds keys only from whitelist-filtered dicts — `allowed` (update_supplier), `_APOLLO_COLUMNS` (upsert_apollo_data), `_CONTACT_WRITABLE` (upsert_contact) — with named-param values, so **column names are never caller-controlled → no SQL-injection vector** (verified by reading the builders at lines ~397/405, ~440/471, ~496/517). The `ALTER TABLE … ADD COLUMN {col} {coltype}` at line ~235 interpolates only hardcoded dict keys (safe). Upserts create a `discovery_only` row first so cache write-backs land; bounce handling nulls only the matched contact.

**3. Efficiency.** A sourcing run calls `lookup_*` / `create_stub` / `enrich_option` many times; each opens its own connection (see N1). No redundant *external* calls.

**4. Clarity.** Readable; consistent helper shape.

**5. Bloat.** `loc_verdict: bloated` (742 code lines). The module is **cohesive** (one store, five related table-families), so no forced split — but it's a natural future candidate to break the review-queue concern into its own module if it keeps growing. No **B** finding raised (no objectively-dead or duplicated unit).

**6. Findings.**
- `supplier_registry.py` (16 sites: lines 278, 297, 310, 352, 408, 459, 505, 550, 593, 691, 724, 797, 837, 851, 862, 877) · every function opens `_get_conn()` and never closes it (no `closing()`/`try-finally`, no `contextlib` import) · **LOW (resource)** · this is the higher-traffic sibling of the fixed **L1** (`orders.py`) — in the long-lived FastAPI process it leaks file handles · **proposed (N1, behaviour-preserving):** wrap each `_get_conn()` in `contextlib.closing(...)` exactly as L1 did; return shapes unchanged. Land as one "N1: close store connections" commit. *Verify suite green in the attended env — not certifiable from the sandbox.*

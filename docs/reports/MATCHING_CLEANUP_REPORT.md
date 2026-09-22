# MATCHING CLEANUP — Closing Report

**Session:** 2026-07-22 (supervised) · **Brief:** `MATCHING_CLEANUP_BRIEF.md`
**Branch:** `fix/matching-cleanup` off `test/flag-on-integration` (BASE_HEAD `5c088ec`)
**Commits:** `1678950` (F1) → `bf2f142` (F2) → `1ab9d5d` (F3) — one per fix. **NOT PUSHED.**
**Suite:** baseline 2273 passed / 73 skipped → after F1 2278 → after F2 2287 → after F3 **2305 passed / 73 skipped** (green at every commit; +32 new tests total). Night-9 `TestAcceptanceGusher` pins green before and after every fix.

---

## I-gate findings (investigation before code)

- **I1 — how Jamieson earned Band B.** `classify_pn_evidence` (`ranking_bands.py`, pre-fix :92-121): `HHI1036215TC` vs `HHI15012447T` is neither exact nor a prefix pair, so it fell through to the blanket `return "compatible"`. The ≥6-alphanumeric base guard (`_CANONICAL_MIN_BASE_LEN`, :119) applied **only to the canonical branch** — compatible was unguarded (shared prefix `HHI1` = 4 chars was never measured). `assign_band` :201-202 turned "compatible" into Band B, and `rescope_floor` :417 used the same bogus credit to clear Jamieson's suitability floor (crude suit ≈4, eq 34.4). One old test pinned the defect (`TOTALLY-DIFFERENT-99` → "compatible"); updated to expect "none" under F1.
- **I2 — Galt's extraction miss.** Tier-3 `found_part_number` comes solely from the LLM extractor whose prompt reads "the exact part number string visible in the **snippet or page title**" (`enterprise_search.py:379`); the URL is shown (:474) but never mined. MROSupply's `LAM150-12-447T` worked because the PN was in snippet text. Candidates carry no title/snippet at banding time → F2 is URL-slug-only (conservative subset of "slug + title").
- **I3 — dedup keying sites.** (1) `_dedup_across_tiers` (`sourcing_agent.py:140`): normalized vendor-name OR **exact** URL keys; and a **floored first occurrence claims no slot** (:181 pre-fix) while the flag-on band pass later *revives* PN-evidence candidates via `rescope_floor` — the two-Zoro mechanism (both Band B $114.99). (2) Seed merge `_key` (`api_server.py:1292`) used `supplier_registry._normalize_domain`, which strips **www. only** (`supplier_registry.py:402-412`) — `static.globalindustrial.com` ($22,051 PDF edge, confirmed in `utils/known_parts.json`) could not merge with fresh `www.globalindustrial.com`. (3) `known_parts._edge_id` is shared flag-off — left untouched. `url_normalize.py` had no domain helper; extended. Marketplaces are registry-classified (`marketplace_registry.py`; zoro.com + globalindustrial.com listed, subdomain-aware) → F3 got the false-collapse guard.
- **I4 — Gusher pins under the guard.** `84004-28 ↔ 84004-28-C238CBC` is canonical (guard touches compatible only); `84004-28SP` shares the 7-char normalized base `8400428` ≥ 6 → passes the prefix branch. Ranking-bands module: 97 passed before F1, 111 after F2, 130 after F3.

No finding contradicted the brief.

## The fixes

- **F1 (`1678950`) — compatible-PN family guard.** In `pn_evidence_for` (only ever called on the RANKING_BANDS_V1 path; `classify_pn_evidence` stays pure): compatible credit requires shared normalized prefix ≥6 alphanumerics OR spec-token corroboration — the found PN / listing URL **path** carries ≥ half (rounded up) of the request's rating/frame tokens, incl. one ≥3 chars; query strings never corroborate. Tokens derive from the requested PN (`HHI150-12-447T` → 150 / 12 / 447T; mfr alpha prefix stripped). Uncorroborated cross-manufacturer PNs now earn nothing (decision-#3).
- **F2 (`bf2f142`) — PN-from-URL assist.** `extract_pn_from_url` + `_assist_pn_from_url` wired first into `apply_ranking_bands` (structurally flag-on): PN-shape heuristic (≥7 chars, ≥2 letters, ≥4 digits, unit tokens like `150hp`/`1200rpm` excluded) + REQUIRED spec-token corroboration in the same slug. Recorded `pn_source="url"`, passed through the SAME classification + F1 guard, and **capped at compatible-grade** — a slug echo can earn Band B, never Band A (spec §3: part-referencing URL = probable fit). `pn_source` survives the known_parts edge cache and seed merge. Mocks / `no_match` / extractor-PN candidates never assisted.
- **F3 (`1ab9d5d`) — registrable-domain dedup.** New `url_normalize.registrable_domain` (eTLD+1 heuristic, pure stdlib). Applied at all three keying sites: cross-tier dedup domain slots (flag-gated; flag-off never builds them), seed-merge `_key`, and a NEW post-band `_dedup_same_domain` pass inside `apply_ranking_bands` that closes the Zoro revival mechanism — richest wins (onboarded > priced > exact-PN > evidence quality, the brief's precedence), losers ANNOTATED `rejection_reason="duplicate_vendor_domain"`, never removed. Marketplace domains never collapse different-named vendors. `TECH_DEBT.md` §1 records the different-registrable-domain vendor-identity case (springerparts.com vs springerpumps.com) as future entity-resolution work. `design/interactions.md` updated for all three fixes.

## Live verification (flag-on, fresh runs on the new code, port 8002)

### Crown Triton `HHI150-12-447T` — before `2ca2a832` → after `9a33026b` (dump: `TRITON_VERIFY_AFTER.txt`)

| Vendor | Before | After |
|---|---|---|
| Jamieson Equipment | **2 visible Band-B cards** on 10HP/215TC PNs (floor cleared by bogus compatible credit) | seeded card only, PN credit denied, `suitability_below_floor` **stands** — not a finding ✓ |
| Galt Electric | floored, `found_pn=None`, $7,941.46 invisible | **Band-B finding, $7,941.46 — cheapest priced option**, `found_pn=GPT15006447TK`, `pn_source=url`, floor cleared ✓ |
| Global Industrial | 2 cards ($36,175 fresh + $22,051 seeded `static.` PDF) | **once** ($36,175); static edge merged; a second fresh listing (`C150P3C`, $16,999) floored for audit ✓ |
| MROSupply / Electric Motors For Less | same-vendor duplicate cards | one card each; duplicates marked `duplicate_vendor_domain` live ✓ |

API findings: 4 × Band A (Dealers Electric, Material Supply Network, CE Motors, NAE) + 6 × Band B (Global Industrial, Electric Motor Warehouse, **Galt**, Electric Motors For Less, MROSupply, WEG). Jamieson absent. Cross-manufacturer spec-corroborated PNs kept credit (`PE447T-150-6`, `HD150P3F` via listing-slug corroboration).

### Gusher `84004-28-C238CBC` — before `1eb85054` (11 visible A/B) → after `2daad1b9` (13 visible A/B; dump: `GUSHER_VERIFY_AFTER.txt`)

**No vendor lost.** Every previous vendor still surfaces: Platinum Performance Products (A), Seal It (A this run — fresh listing showed `84004-28`), Zoro (**once**, B, $114.99 — was twice), Springer Parts (**once** — its "Springer Parts (Springer Pumps, LLC)" copy marked `duplicate_vendor_domain`), Springer Pumps (separate registrable domain — intact, per TECH_DEBT boundary), Seals-Direct, U.S. Seal Mfg., Pump Catalog, Seal House USA, Sekas. New finds added by fresh discovery: All Seals Inc., Seal Distributors, Seal It 123. Night-9 acceptance pins green.

Decision-#3 conservatism confirmed live: uncorroborated cross-manufacturer PNs (`PS-525`, `PS-7937`, `C150P3C`) earned zero PN credit; candidates banded on their remaining evidence only.

### Observed nuances (documented, not defects)

- **Seals-Direct:** this run's fresh unpriced `84004-28` listing was collapsed in favor of the priced $85.28 copy — exactly the brief's `priced > exact-PN > bare` precedence. Vendor present (Band B, same card as the before-run).
- **U.S. Seal Mfg. A→B:** run-to-run extraction variance (this run's fresh listing showed `PS-238` on the homepage URL, not `84004-28`); membership intact.
- **"Seal It" vs "Seal It 123":** two visible cards — sealit123.com is registry-classified as a marketplace, so the different-name false-collapse guard (deliberately conservative) declines to merge differing name slugs. Never loses a vendor; worst case is one extra card.

## Success criteria

1. ✅ Fresh Triton: Jamieson out of Band-B findings; Galt a priced Band-B finding at ~$7.9k (new cheapest); Global Industrial once.
2. ✅ Fresh Gusher: no vendor lost vs the previous 11 (Zoro once, Springer Parts once); Night-9 pins green.
3. ✅ No band-rule loosening beyond F1 guard semantics; URL PNs capped below Band A; decision-#3 intact (verified live).
4. ✅ This report; per-fix commits; before/after tables; `TECH_DEBT.md` vendor-identity entry. **NO PUSH.**

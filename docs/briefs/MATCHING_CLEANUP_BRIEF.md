# MATCHING CLEANUP BRIEF — Family Guard, PN-from-URL, Dedup Normalization

Supervised session (or short fenced overnight). Three small, well-scoped fixes that fell out
of the Crown Triton live run (run `2ca2a832`, 2026-07-22) and the seed-model verification.
**The band model itself is behaving correctly and is NOT to be redesigned** — Crown Triton now
surfaces 3 Band-A + 5 Band-B findings; these fixes remove the three residual defects around
it. Branch `fix/matching-cleanup` off current `test/flag-on-integration`. One commit per fix.
Suite green at every commit (baseline ~2273/73). NO PUSH.

---

## Evidence (live, from the Triton dump + seal-run console)

**E1 — wrong-family Band B.** Jamieson Equipment banded B with `found_pn=HHI10-36-215TC`
(and a seeded sibling `HHI10-18-215TC`) against request `HHI150-12-447T`. That's a **10HP,
215TC-frame** motor offered as "probable fit" for a **150HP, 447T-frame** request — same
manufacturer prefix (`HHI1`…), completely different machine. Band B ("probable fit") is an
overclaim; a buyer clicking it wastes time, and enough of these erode the band's meaning.

**E2 — cheapest candidate floored because extraction missed a PN that's in the URL.**
Galt Electric ($7,941.46 — the CHEAPEST priced candidate on the run) was floored
(`suitability_below_floor`, no-PN Band B, per ratified decision #2) with `found_pn=None` —
while its URL is `https://vfds.com/galt-electric-gpt-motor-gpt15006447tk-150hp-1200rpm-3-phase-460v-60hz-445-7t`:
the part number **GPT1500-6-447T-K is in the URL slug**, along with 150hp/1200rpm/460v.
MROSupply's `LAM150-12-447T` (a cross-manufacturer PN, properly extracted) earned Band B on
the same run — so the crediting mechanism works; the extraction missed. US Motors ($35k) and
Tatung ($21k) are the same shape. **The ratified conservatism stays; the inputs get better.**

**E3 — same-vendor duplicates across domain variants.** Global Industrial appears twice
(fresh `www.globalindustrial.com` @ $36,175 + seeded `static.globalindustrial.com` PDF edge
@ $22,051). The seal run showed Zoro twice (both Band B $114.99) and "Springer Parts /
Springer Pumps, LLC" alongside the springerpumps seed. Both `cross_tier_dedup` and the seed
merge key on raw domain, so subdomain variants (www./static./catalog.) and same-vendor
listings escape. (True different-registrable-domain identity — springerparts.com vs
springerpumps.com — is FUTURE vendor-identity work, out of scope; note it in TECH_DEBT.)

## INVESTIGATION FIRST (read-only; file:line; self-gate)

- **I1.** How Jamieson earned B: which rule credited `HHI10-36-215TC` (compatible-PN
  classification in `classify_pn_evidence`? the extractor's match_type?). Report the exact
  path and what the shared-prefix length was (`HHI1` = 4 alphanumerics — does the ≥6-char
  base guard apply to canonical only, leaving compatible unguarded?).
- **I2.** Where found_pn extraction happens for tier-2/3/aftermarket candidates
  (enterprise_search extractor + any post-parse), and what URL/title text is available at
  that point. Confirm MROSupply's LAM150-12-447T path (the working example) vs Galt's miss.
- **I3.** Every dedup keying site: `cross_tier_dedup`, the seed-merge domain dedupe, and any
  URL normalization helpers (`url_normalize` module exists — check what it offers). Why did
  Zoro appear twice on the seal run (same domain, different URLs, different tiers?) — report
  the actual mechanism before fixing.
- **I4.** Confirm the Gusher canonical family still classifies correctly under any guard you
  design (84004-28 ↔ 84004-28-C238CBC canonical; 84004-28SP compatible) — these are pinned
  by the Night-9 acceptance tests; run them before and after.

## FIXES (one commit each; all flag-on behavior — RANKING_BANDS_V1 — flag-off untouched)

- **F1. Compatible-PN family guard.** A found PN earns COMPATIBLE (Band-B PN-evidence) credit
  only when it plausibly belongs to the requested part's family: shared normalized prefix
  meeting the same ≥6-alphanumeric base standard as canonical, OR spec-token corroboration
  (the found PN / listing carries the request's frame-size / rating tokens — e.g. `447T`,
  `150`). `HHI10-36-215TC` vs `HHI150-12-447T` fails both (prefix `HHI1` = 4; frame 215TC ≠
  447T) → no PN credit → the candidate bands on its remaining evidence (likely C or floored
  B). Cross-manufacturer PNs with spec corroboration (LAM150-12-447T: `150…447T`) keep their
  credit. Gusher pins must stay green (I4).
- **F2. PN-from-URL/title extraction assist.** When the extractor returns no found_pn,
  attempt a conservative PN candidate from the listing URL slug + title: PN-shaped token
  (letters+digits+separators, length/shape heuristic), REQUIRED corroboration by the
  request's spec tokens in the same slug (Galt: `gpt15006447tk` + `150hp` + `447` context),
  recorded with `pn_source="url"` provenance and passed through the SAME classification +
  F1 guard as extractor PNs — never a blind trust. A URL PN that fails the guard credits
  nothing. Expected live effect: Galt (and likely US Motors/Tatung if their slugs qualify)
  band B legitimately instead of floored.
- **F3. Dedup domain normalization.** All dedup keying (cross_tier_dedup + seed merge)
  normalizes to the registrable domain (strip www./static./catalog. etc — use/extend the
  existing url_normalize helper; a simple known-prefix strip + eTLD+1 heuristic is fine, no
  new dependency unless one already exists in the tree). Merge keeps the richest candidate
  (priced > exact-PN > bare) exactly like the seed merge's precedence. Fix whatever I3 found
  behind the same-domain Zoro duplicate. Add the different-registrable-domain vendor-identity
  case to TECH_DEBT.md as future work — do NOT attempt it here.

## TESTS

- F1: the Jamieson case (family mismatch → no PN credit) + the LAM150 case (spec-corroborated
  cross-mfr keeps credit) + Gusher canonical/compatible pins unchanged (the existing
  acceptance tests are the regression net — run the full ranking-bands module).
- F2: Galt's real URL slug → PN extracted with pn_source=url → classified → banded B; a junk
  slug (marketing text, no spec corroboration) credits nothing; provenance visible on the
  candidate.
- F3: subdomain-variant duplicates collapse to one (richest wins) in both cross_tier_dedup
  and seed merge; the Zoro mechanism (per I3) covered; a genuinely-different-vendor same-
  eTLD+1 false-collapse guard if I3 shows marketplaces share domains (marketplaces are
  registry-classified — check before keying purely on domain).
- Flag-off parity for all three. Suite ≥ baseline, green at every commit.

## SUCCESS CRITERIA (falsifiable, live)

1. Fresh Crown Triton run: Jamieson no longer Band B on a 10HP PN; Galt surfaces as a priced
   Band-B finding (~$7.9k — likely the new cheapest option); Global Industrial appears ONCE.
2. Fresh Gusher run: findings unchanged in membership vs the current 11 (dedup may reduce
   duplicate cards — Zoro once, Springer once — but no vendor LOST), all Night-9 acceptance
   pins green.
3. No band-rule loosening beyond F1's guard semantics: an uncorroborated cross-manufacturer
   PN with no spec tokens still earns nothing (decision-#3 conservatism intact).
4. Morning/closing report: I-gate findings, per-fix commits, before/after candidate tables
   for both parts, TECH_DEBT.md updated (vendor-identity dedup entry). NO PUSH.

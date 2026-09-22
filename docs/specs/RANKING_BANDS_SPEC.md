# SPEC — Evidence-Banded Sourcing Ranking ("Honest Ranking")

**Status:** agreed design, ready for build scoping.
**Owner:** Tom. **Origin:** live Gusher run diagnosis (run `37e6104d` cached → cache-busted live re-run), Night 7 audit C3 finding, and the tier-priority design discussion.

---

## 1. The problem (evidence, from the live Gusher run)

One ranked list currently mixes fundamentally different kinds of claim, scored on one axis:

| Candidate | Evidence | Score today | Outcome today |
|---|---|---|---|
| Sealit123 | **Exact PN `84004-28-C238CBC`, real URL, $53.25** | suit 70 | Ranked below a category match |
| US Seal Manufacturing | **Exact PN `84004-28` found**, real URL | suit 12.6 | **REJECTED** (`suitability_below_floor`) |
| Zoro | Aftermarket PN `84004-28SP`, real URL | suit 10.5 | **REJECTED** (below floor) |
| Phoenix/Anderson/OTC/Great Lakes/Wagner | `is_mock: True`, **no URL, no PN, nothing verified** | **suit 88.0, conf 75.0 (hardcoded)** | Ranked above every real Tier-3 finding |
| DXP (Tier 1, onboarded) | Carries SEAL class; **no part found, price TBD** | suit 92 | Top of list |

Three defects: (1) **fabricated scores** — seeded mock cards carry unearned 88.0/75.0 and outrank verified findings; (2) **inverted floor** — the suitability floor rejects vendors who found the part while passing mocks; (3) **category-as-answer** — an onboarded class match is presented above a confirmed exact-part match with a price. Plus (4) the **known_parts cache** freezes whatever result set existed at first search, so none of the above is even re-evaluated on later runs.

## 2. The principle

> **Rank by evidence band first. Onboarding is the tiebreaker WITHIN a band, never a ladder ACROSS bands. No candidate carries a score it didn't earn. A frozen cache never outlives an improved matcher.**

The buyer's question is "how do I get this part, fast, at a fair price." A vendor with the part and a price ANSWERS it; a vendor who plausibly could get it is a LEAD. Leads never outrank answers.

## 3. Evidence bands

Every candidate is assigned a band from its evidence, computed at ranking time:

**Band A — CONFIRMED PART.** The vendor demonstrably has/lists THIS part (or a verified equivalent):
- exact PN match (`found_part_number` equals or canonically-matches the requested PN), OR
- verified interchange/equivalent PN with the equivalence source recorded, OR
- an onboarded supplier's explicit confirmation for this request (structured portal quote or parsed email quote).
Real `source_url` (or a supplier confirmation record) REQUIRED. Price known or quoted preferred but not required for the band.

**Band B — PROBABLE FIT.** Real, candidate-specific evidence short of confirmation:
- compatible/aftermarket PN found (e.g. `84004-28SP`), OR
- a specialist whose verified scope strongly matches the request (a mechanical-seal manufacturer for a seal request — scope evidence, not category membership alone), OR
- a listing URL that references the requested part/family without a clean PN extract.

**Band C — ASK-AND-SEE (RFQ targets, not results).** No candidate-specific evidence; capability is inferred:
- onboarded supplier matching on class/category only (today's DXP case),
- brand-intelligence "authorized distributor" seeds (today's mock cards),
- capability-pivot discoveries.
Band C is PRESENTED as outreach, not findings: "Quotes requested from N suppliers" — see §7.

**Hard rule:** ordering is Band A > Band B > Band C, absolutely. No score, boost, or tier can move a candidate above a higher band.

**Within-band ordering:** (1) onboarded (tier1_lifecycle == onboarded) first — this is the onboarding benefit, applied where it is honest; (2) then evidence-quality score (§4); (3) then TCA (price/speed/reliability) as today.

**Band mobility:** a Band-C onboarded supplier that CONFIRMS (portal structured quote / parsed email quote with part+price) is PROMOTED to Band A at that moment — and, being onboarded, to the top of Band A. This is the "first look → confirm → win" loop that makes onboarding genuinely valuable. (Future: live inventory integration makes this promotion automatic at query time.)

## 4. Scoring — earned, not asserted

- `suitability_score` is REPLACED in ranking by the band + an **evidence-quality score** computed from verifiable inputs only: PN-match quality (exact > canonical-variant > compatible), URL/listing verification, price presence and provenance, scope-match strength, supplier data quality (claimed/self-declared scope scores above inferred scope).
- **No hardcoded scores.** Seeded/mock candidates carry NO suitability and NO confidence number — they carry only their provenance ("authorized distributor per brand intelligence") and Band C. Delete the fabricated 88.0/75.0 constants.
- `confidence_score` becomes REAL (this is the C3 fix landing where it was always needed): populated from the same evidence inputs, calibrated so 0 means "nothing verified" and gates display copy — Band A/B candidates render "verified/likely" states; a 0-confidence candidate can only ever be Band C.
- `is_mock: True` on anything scored/ranked as a finding is a CONTRACT VIOLATION — test-enforced.

## 5. The floor, fixed

- The suitability floor (today `suitability_floor:30%`) applies ONLY within Band B, as a quality bar on partial evidence — it exists to keep junk listings out, not to reject part-matches.
- Band A candidates are NEVER floor-rejected. (US Seal Mfg finding the exact PN and being floored at 12.6 is the defect this line kills.) If a Band-A candidate looks wrong, that's an equivalence/identity question for review — not silent rejection.
- Band C is not scored, so the floor doesn't apply; C candidates are capped in count (top-N by scope strength, onboarded always included) instead.
- Whatever produced 12.6 for an exact-PN match must be root-caused during the build (likely the crude keyword scorer) and its role reduced to Band-B ordering input only.

## 6. Cache policy (known_parts), reworked

- **Cache part IDENTITY, not vendor VERDICTS.** The canonical part key, resolved identity, extracted attributes, and known interchange edges are stable and stay cached indefinitely.
- Vendor/sourcing results become **hints, not answers**: on a repeat search, cached edges seed/accelerate discovery and are re-verified, but a fresh Tier-2/3 discovery ALWAYS runs — the cache accelerates discovery, it never replaces it. TTL-fresh edges (default 7 days, configurable) are merged into the fresh candidate pool as seeds (dedupe by domain; fresh wins volatile fields; the edge contributes its stored evidence); the TTL governs seed RELEVANCE, not replay rights — an expired edge simply stops seeding. A cached result set is never returned as the final answer.
- **Matcher-version invalidation:** cache entries record the matcher version that wrote them; a version bump marks all vendor edges stale (identity stays).
- **Quality gate on write:** only Band A/B results are written back as edges; Band C / mock cards are NEVER cached (this is how the mock cards fossilized in the first place).
- Existing cache: one-time migration marks all current vendor edges stale-hint; identity keys retained.

## 7. Presentation (buyer-facing honesty)

- Band A/B render as today's option cards, with evidence visible: found PN, price, source link, "verified listing" / "compatible part" labels from confidence.
- Band C renders as an outreach block, NOT option cards: "Quotes requested from your onboarded supplier (DXP) and N authorized Gusher distributors — responses expected within X." Onboarded supplier named first and flagged as yours. No fabricated numbers anywhere on this block.
- When a Band-C supplier confirms, their card APPEARS in Band A position ("DXP confirmed: $X, ships Y") — visibly the onboarding benefit working.
- Nothing user-visible may display a suitability/confidence number for a candidate whose number wasn't computed from evidence.

## 8. Out of scope (this spec)

- A2 kit-aware queries and A4 dims-into-queries (separate matching-quality session; they feed MORE candidates into the bands but don't change the banding).
- RFQ live sending (the outreach block records/stubs as today until the send milestone).
- Live inventory integration (future; noted as the Band-A automation for onboarded suppliers).
- Registry junk cleanup (separate; Band C's cap + scope-strength ordering reduces its blast radius meanwhile).

## 9. Success criteria (falsifiable — Gusher is the acceptance case)

Using the live Gusher run inputs (Gusher Pumps / Type 21 / 84004-28-C238CBC / 1-5/8" seal):
1. **Sealit123 (exact PN, $53.25) ranks #1 or #2 overall** — above every category-only and mock candidate. If DXP has NOT confirmed the part, Sealit123 is #1.
2. **US Seal Manufacturing (exact PN) is NOT rejected** — Band A, surfaced, no floor rejection.
3. **Zoro / Seals-Direct aftermarket (compatible PN found) surface in Band B** — not floor-rejected at 10.5.
4. **No `is_mock: True` candidate appears as a ranked option card**, and no mock carries a suitability/confidence number. The five distributor seeds appear (capped) in the outreach block only.
5. **DXP appears as the named onboarded supplier in the outreach block** (Band C) — first-look RFQ — and a simulated DXP confirmation promotes it to top of Band A.
6. **Re-running the same search does not replay a frozen verdict:** fresh discovery ALWAYS runs (TTL-fresh edges only seed the merge), and a matcher-version bump invalidates vendor edges.
7. Band ordering is property-tested: no Band-C candidate ever ranks above Band B, no B above A, regardless of scores.
8. Confidence is populated from evidence and 0-confidence implies Band C — asserted across the eval bank, not just Gusher.
9. Existing suite stays green; the Night-7 eval cases still pass.

## 10. Build shape (suggested, for the session brief)

Likely three commits-worth of coherent stages: (1) band assignment + within-band ordering + floor scoping, behind a flag (`RANKING_BANDS_V1`), with the Gusher acceptance tests; (2) mock-card descoring + outreach-block data shape (API response distinguishes findings vs outreach); (3) cache policy (identity-vs-verdict split, TTL, version invalidation, write gate). Frontend outreach-block rendering can trail as its own task. Investigate-first as always: the scorer that produced 12.6-for-exact-PN needs root-causing before replacing.

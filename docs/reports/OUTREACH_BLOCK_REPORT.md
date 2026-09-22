# Outreach Block — Build & Verification Report

**Task:** Band C candidates must render as "quotes we're requesting", not as option cards —
the last gate before RANKING_BANDS_V1 can be enabled for a customer.
**Branch:** `test/flag-on-integration` · **Date:** 2026-07-13 · **NO PUSH** (as instructed)

## Commits

1. `669567c` — `feat(options): consume banded findings[] for the option cards (RANKING_BANDS_V1)`
2. `a7743a4` — `feat(options): Band-C outreach block - quotes we're requesting, not findings (RANKING_BANDS_V1)`
   (includes the `design/interactions.md` update)

`tsc --noEmit` clean · `next lint` clean · `uv run pytest -q`: **2020 passed, 73 skipped**.

## What was built

- **Flag-on card list = `findings[]`** (Band A/B, server's banded order), detected from the
  payload itself (`Array.isArray(sr.findings)`) — the backend emits these keys only for results
  carrying the `ranking_bands:v1` marker (`api_server.py:1048`), never an env read in the client.
  Cards keep everything from the provenance fix (seller headline, PN line, View listing,
  reconciled price copy).
- **Outreach block** (`OutreachBlock`, `options-screen.tsx`): a `<section>` status panel below
  the findings — dashed border, flat surface, **no scores, no prices, no Order button, no
  "recommended" styling**. Onboarded supplier(s) lead with a "Your supplier" text badge (status
  never by colour alone); non-onboarded targets are one line — called "authorized {manufacturer}
  distributors" only when every target's server provenance says so, else "suppliers matched to
  this part category". Footer: *"These suppliers are matched on what they carry — none has
  confirmed having this exact part yet. Their quotes are that confirmation."*
  Copy is **intent-only** ("we're asking") — `EMAIL_SEND_ENABLED=False` means no RFQ is
  delivered, so nothing claims a sent email; no response-time promise (we don't know one).
  Empty `outreachTargets` ⇒ no block (no empty shell).
- **Nothing-found-but-asking state:** zero findings + targets ⇒ headline switches to
  "We're requesting quotes for this part", body says "We didn't find this part listed anywhere
  we searched" + the count, and the outreach block renders — never an empty list under a
  "best options" headline.
- **Flag-off untouched:** legacy payloads carry no banded keys ⇒ tier-array rendering unchanged;
  the layout wrapper divs are added only when the block exists, so the flag-off DOM is identical.

## Live verification (Gusher 84004-28-C238CBC)

| State | Run | Screenshot |
|---|---|---|
| Flag-on | `42e0f71f-b74e…` (banded, 2026-07-13) | `OUTREACH_VERIFY_FLAG_ON_GUSHER.png` |
| Flag-off | `03ce6bb2-2e6c…` (legacy) | `OUTREACH_VERIFY_FLAG_OFF_GUSHER.png` |
| Empty findings | synthetic copy of the banded run, A/B all rejected (inserted → screenshotted → deleted) | `OUTREACH_VERIFY_EMPTY_FINDINGS.png` |

- Flag-on: findings as cards — Seal It **$53.25** (exact PN `84004-28-C238CBC`, listing link,
  Recommended), Seals Direct $85.28, Seal It 123, …, Zoro (Band B). Outreach block below:
  "Asking 5 authorized Gusher Pumps distributors: Phoenix Pumps, Anderson Process, OTC
  Industrial, Great Lakes Pump & Supply, Wagner Process Equipment." + honesty footer. No
  scores/prices/Order buttons anywhere in the block.
- Flag-off: byte-identical legacy rendering — the 5 mock distributors still show as "Get quote"
  option cards (the pre-existing behavior), and **no** outreach block.

## ✅ DXP blocker — FIXED (follow-up commit)

**Was:** DXP Enterprises (the onboarded Band-C class-match) silently vanished from the entire
flag-on view. Root cause — an ordering bug between Night 5 (I4) and Night 9: bands were
applied before persist (discovery: `sourcing_agent.py:512-515`; cache-hit:
`api_server.py:1271-1272`), then Step 3's TIER1_V2 fresh re-derive **replaced
`result["tier_1"]`** with unbanded matcher candidates — on both paths, every flag-on run.
With `band=None`, DXP was skipped by `banded_findings()`, `outreach_targets()` **and** the
T4 read-time promotion loop (`band == "C"` check).

**Fix** (`api_server.py`, after the Step-3 re-derive): when the result carries its own
`ranking_bands:v1` marker, re-run `apply_ranking_bands` — pure/deterministic, so
re-annotating the already-banded Tier 2/3 is a no-op; only the fresh Tier 1 changes.
Marker-gated (never an env read at that point beyond what the run already recorded) and
fail-soft, matching the read-time transform. No band-logic change.

**Regression net** (`test_api_server.py::TestBandedTier1ReDeriveOrdering`, 4 tests): drives
the REAL background path (confirm-intake → `_run_sourcing_background` → persist) with
TIER1_V2 + the marker on, asserting: stored tier_1 carries `band="C"`; DXP leads
`outreachTargets` (onboarded, provenance string); **T4 promotion works on the real persisted
result** (confirmed quote with price → DXP tops findings as Band A); and flag-off (no
marker) stays byte-identical (no band keys, no banded response keys). Verified the net
bites: with the fix stashed, 3 of 4 fail (the flag-off one passes by design).

**Confirmed:** the pre-existing T4 test
(`test_ranking_bands.py::test_simulated_dxp_confirmation_promotes_to_top_of_findings`) was
passing **only on a synthetic fixture** — `_banded_gusher_result()` bands a tier_1 that
already contains DXP, so it never reproduced Step 3's replace. It still passes (valid for
the transform logic); the new class covers the live ordering.

**Live re-verification:**
- Fresh flag-on run via the API (cache-hit path, `RANKING_BANDS_V1=1 TIER1_V2=1`):
  `outreachTargets.suppliers[0] = {vendorName: "DXP Enterprises", onboarded: true,
  provenance: "Onboarded supplier — class match"}`. (A cache-hit run carries no mock seeds —
  Band C is never written to the cache by policy — so its block is DXP-only.)
- The stored demo run `42e0f71f…` was repaired in place by re-running the same band pass
  over its persisted JSON (pre-fix backup:
  `<scratchpad>/42e0f71f_sourcing_results.pre-fix.json`). UI now shows the full target:
  "YOUR SUPPLIER" badge → "DXP Enterprises — we're asking them to confirm availability and
  price." then "Also asking 5 authorized Gusher Pumps distributors: …" —
  `OUTREACH_VERIFY_FLAG_ON_DXP_GUSHER.png`.
- Flag-off run `03ce6bb2…` re-checked after the fix: payload keys unchanged (no banded
  keys), DXP still a legacy tier-1 card.
- Full suite after the fix: **2024 passed, 73 skipped** (2020 + the 4 new).

## Notes

- The Band-C cap (5) is server-side (`BAND_C_CAP`, `ranking_bands.py:56`; `cap_band_c` at
  `:425`); over-cap candidates get `band_c_capped=True` on the raw candidate and are excluded
  from the payload — the annotation itself is not surfaced, only its effect. Nothing the
  frontend needs is missing here.
- Backend for verification was started with `RANKING_BANDS_V1=1` on port 8001; the existing
  Next dev server on port 3000 was reused (hot-reloaded the changes).

# Sourcing Option Card — Seller Identity & Evidence Fix (report)

**Status: DONE.** Frontend-only, 3 commits, `tsc --noEmit` + `next lint` clean after each.
NO PUSH. Verified live, flag-on, against the real Gusher run — screenshot:
`./CARD_FIX_VERIFY_GUSHER.png` (run `42e0f71f-b74e-4eaa-a78e-ce4fee2742e3`, today's live run).

## Investigation (file:line)

**API carries everything — confirmed, both flag states.** `_transform_option`
(`api_server.py:838-907`) emits `vendorName` (:843), `url` (:868, from `source_url`),
`foundPartNumber` (:870), `price` (:846), plus `purchaseChannel`/`evidenceState`/
`priceVerified`/`priceUnverified`. Flag-on `findings[]` entries are the same card shape
(+ `band`/`evidenceQuality`/`isMock`). The frontend `Candidate` type already declared all
fields (`frontend/src/types/index.ts:121,124,135,142`).

**Flag-state scope.** The frontend renders `tier1+tier2+tier3`
(`options-screen.tsx:182`) in BOTH flag states and ignores `findings[]` entirely
(`SourcingResults`, `types/index.ts:215-221`, doesn't declare it). One card component
serves both states → the fix lands once and works flag-on **and** flag-off. Adopting
`findings[]`/`outreachTargets` for list composition/ordering belongs to the separate
outreach-block task.

**"Available through Gofer" was a deliberate substitution, not a fallback.**
`options-screen.tsx:235` rendered `namesSupplier ? c.vendorName : "Available through
Gofer"`, gated by `:220` (`namesSupplier = quoted || !isMkt`) — every non-quoted row with
a buyable marketplace price (exactly the best cards, e.g. Seal It $53.25) had its seller
hardcoded away. Comments (:218-219, :232-234) documented the intent: anti-disintermediation
("don't headline the marketplace name — it's Arkim's supply source"). The same rationale
suppressed the listing link (`:330` gated on `!isMkt && !quoted`, and buried inside the
collapsed Why panel — comment :325-329). `foundPartNumber` was bullet prose only (:85).

**Contradictory copy.** One indicative-price marketplace card could stack: "Available
immediately at this price — no quote needed" (:81) + "Price auto-extracted at low
confidence" (:86) + "Limited price data — indicative" (:87) + card sub-line "Available
now · no quote needed" (:272). ("Some fields could not be verified" is the backend
spec-comparison caveat, `spec_comparison_agent.py:272` — honest, kept.)

**Task 5 (Band C / mocks).** The Order button renders only when `c.price != null`
(:284,:303); Band-C/mock cards always arrive `price: null` (backend forces `price_tbd`),
so they structurally cannot show "Order through Gofer" — verified live (five seeds render
name + "Get quote" only, no listing link, no PN, no price). No code change needed.
Full outreach-block treatment remains the separate task.

**Other surface.** `gofer/sourcing/vendor-card.tsx` (route `/runs/[id]`) already names
the vendor and links out — not the reported card; untouched.

## Reconciled copy (as proposed, then shipped)

`priceIndicative = priceUnverified || priceVerified === false`; marketplace rows:

| Price state | Why-bullet | Card sub-line |
|---|---|---|
| Verified | "Available now at {vendor} — Gofer can order it for you at this price, no quote needed." | "Available now · no quote needed" |
| Indicative | "Available now at {vendor} — the listed price is indicative; Gofer confirms the final price before you're charged." | "Available now · final price confirmed at order" |

Price caveats no longer stack (one bullet: low-confidence extraction wins, else
limited-price-data). "Order through Gofer" unchanged on the button + confirm step —
merchant-of-record is expressed there, never as the seller identity.

## Commits

| Commit | Change |
|---|---|
| `5ad3b1b` | Card headline always names the actual seller; location shown for all rows; `namesSupplier` substitution removed |
| `5638c06` | Structural "View listing ↗" on every card with a `source_url` (new tab, `rel="noopener noreferrer"`, padded hit area + focus ring); `PN {foundPartNumber}` structural under the seller name; buried Why-panel link removed |
| `b3b3e30` | Availability copy reconciled with price evidence (table above); caveats never stack |

## Live verification (flag-on, RANKING_BANDS_V1=1)

Backend started flag-on on :8001 (left running for your inspection); your `next dev` on
:3000 hot-reloaded the changes. Page: `/parts/42e0f71f-b74e-4eaa-a78e-ce4fee2742e3`.

- **Top recommended card**: headline **"Seal It"**, structural **PN 84004-28-C238CBC**,
  **$53.25**, "Exact replacement" tag, working **View listing ↗** →
  `https://sealit123.com/oem-pump-seals/brands-f-p/gusher-pumps/84004-28-c238cb`
  (href verified via the accessibility tree), button "Order through Gofer".
- Card sub-line reads "Available now · final price confirmed at order" (its
  `limited_price_data` is true → indicative), and the Why panel shows the reconciled
  bullets with **no** "at this price / no quote needed" claim.
- Seals-Direct (low-confidence price) shows ≈$85.28 + "price unverified" chip + the
  indicative sub-line; Seal It 123 (verified) shows the "no quote needed" line — both
  states render their matching copy.
- Every real listing card links its exact source URL; the five mock seeds and DXP show
  no Order button (Get quote only), mocks carry no PN/price/link.
- `tsc --noEmit` and `next lint`: clean.

Screenshot: `CARD_FIX_VERIFY_GUSHER.png` (repo root, untracked like this report).

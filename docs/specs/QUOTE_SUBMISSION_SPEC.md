# SPEC — Supplier Quote Submission (QUOTE_SUBMIT_V1)

**Status:** design for review → build brief.
**What this closes:** the loop Night 9 built and Night 10 made sendable. Today: RFQ goes out
(soon, live) → supplier replies by email → nothing structured happens without concierge typing.
After this: a supplier — claimed OR unclaimed — submits a structured quote in ~five fields, the
quote becomes the confirmation record T4 already consumes, and the supplier visibly jumps from
the outreach block to the top of Band A ("DXP confirmed: $189 · ships Thursday"). This is the
onboarding benefit made real, and it's the data source the future dashboard (wins/losses/missed
demand) is built on.

---

## 1. Principles (carried from the settled supplier model)

1. **Quoting is unconditional; signup is the upgrade.** An unclaimed supplier can quote from
   the RFQ email alone — no account, no registration wall. The claim pitch comes AFTER the
   quote is in ("want to track this quote and see what you're missing? claim your profile").
   Never gate the quote on signup — we want the quote; they want the order; signup is our ask,
   not their toll.
2. **A structured quote is supplier-authored evidence.** It is exactly the "onboarded
   supplier's explicit confirmation" Band A was defined around — the supplier stating their own
   price for this part. It therefore drives promotion directly (with sanity checks that FLAG,
   not block — §6).
3. **Propose-don't-invent still governs the EMAIL path.** A quote parsed out of a reply email
   remains a proposal until concierge confirms it. Only the supplier's own structured
   submission (authenticated by quote token) or a concierge-keyed entry is authoritative.
4. **No path to auto-order** (unchanged, property-tested). A quote promotes a card; a human
   buyer approves an order through the existing approval flow.

## 2. The three entry paths (one quote store)

| Path | Who | Authority | Notes |
|---|---|---|---|
| **A. Quote link in the RFQ email** | any supplier, claimed or not | authoritative on submit | tokenized per-RFQ link → the quote form. THE primary path; zero-friction. |
| **B. Portal — open requests** | claimed suppliers | authoritative on submit | the portal lists their open RFQs; same form, plus their quote history. |
| **C. Concierge entry** | Tom/admin, keying in an emailed/phoned quote | authoritative (human already reviewed) | admin form; records provenance `concierge`. This works from day one, BEFORE live sends — a quote received any way can be keyed in. |

All three write the same quote record; `submitted_via` ∈ {rfq_link, portal, concierge} recorded.
(Path D — the reply-parser's extracted quotes — stays as-is: extraction PROPOSES into a review
queue; concierge confirmation converts it into a path-C record.)

## 3. Quote token (path A) — the claim-token pattern, re-applied

- Minted per RFQ send, embedded as a link in the RFQ email (`/quote/{token}`).
- Same security posture as claim tokens: unguessable, **hashed at rest**, single-RFQ scope
  (the token can only quote THAT request), expiry = the RFQ's validity window (default 14 days;
  configurable), NOT single-use (the supplier may revise their quote — each submission
  supersedes, §5), admin-gated minting.
- The token page needs NO login and exposes only: the request (part identity, qty, need-by),
  the supplier's own name/domain as addressee, and the form. It exposes NO buyer identity
  beyond what the RFQ email already contained, NO other suppliers, NO pricing signals.
- A token for a withdrawn/expired RFQ renders an honest closed-state ("this request has
  closed") — never an error page, never a live form writing to a dead request.

## 4. The form (five fields + two optional)

Required:
1. **Quote / reference number** (their own reference — free text)
2. **Unit price** (currency fixed USD v1)
3. **Quantity quoted** (prefilled from the RFQ qty; editable — partial quotes are real)
4. **Lead time** (days; or "in stock")
5. **Part number confirmation** — prefilled with the requested PN; the supplier confirms or
   edits it. If they EDIT it (quoting an alternative/equivalent), the quote is marked
   `pn_differs=true` and is flagged for review rather than auto-promoting (§6) — this is the
   wrong-part gate at the quote boundary.

Optional: **freight/shipping** (amount or "included"), **valid-until** (default = RFQ window),
**notes** (free text, shown to concierge, not raw to buyer).

One screen, mobile-friendly, portal-surface palette, ≥44px targets, no account creation, no
password. Submit → confirmation state + the claim pitch (path-A only, and only if unclaimed):
"Quote received. Want to see how it does — and what other requests match you? Claim your free
profile" → existing claim-token flow.

## 5. Data model & lifecycle

`quotes` store (new table, supplier_registry.sqlite or its own — investigation decides):
run_id/rfq_id, part_key, supplier_domain, quote_number, unit_price, currency, qty, lead_time,
freight, valid_until, pn_confirmed / pn_differs (+ the PN as quoted), notes, submitted_via,
submitted_at, submitted_by (token id / portal identity / admin), status, is_test.

Status lifecycle: `active` (drives promotion) → `superseded` (a newer submission from the same
supplier for the same RFQ — the new one wins; history kept) → `expired` (past valid_until —
promotion ceases, card reverts honestly) → `withdrawn` (supplier or admin). Buyer acceptance is
NOT a quote status — it's an order event in the existing flow, referencing the quote id.

## 6. Promotion & the honesty checks

- An `active`, `pn_confirmed` quote from supplier S for run R **is** the T4 confirmation
  record: S promotes to Band A (top of Band A if onboarded) with the card reading
  "{Supplier} confirmed: ${price} · {lead}" — the quote IS the evidence, displayed.
- **Flag-not-block sanity checks** on submission: price wildly off the price_db band for the
  part (>3x or <0.2x median where a band exists), qty far from requested, `pn_differs=true`
  → quote lands `active=false, review=true` in a concierge review queue with the reason;
  everything else promotes immediately. Review approval activates it. (Rationale: a fat-
  fingered $5,325.00 for $53.25 must not top the buyer's list unreviewed; but review is the
  exception path, not the default — the default is instant.)
- Expiry is honest: an expired quote stops promoting and the card reverts to outreach state.
  No zombie confirmations.

## 7. Buyer-side surface

- The promoted card (already built by Night 9 T4 + the outreach block) now populates from real
  quotes: supplier name, quoted price, lead time, "confirmed by supplier {date}".
- Order button follows the existing manual-fulfilment flow (quote id attached to the order
  record for provenance).
- If multiple suppliers quote, each promotes on its own evidence; normal within-band ordering
  applies (onboarded first, then evidence-quality/TCA).

## 8. Notifications (all under the send gate)

- Concierge notice on: new quote (info), review-flagged quote (action), quote nearing expiry
  on an open request (info). V1: the daily digest endpoint grows a quotes section — no new
  email surface required.
- Supplier ack email ("quote received") — stubbed under EMAIL_SEND_ENABLED + governance like
  every other send; goes live only when sends do.

## 9. Explicitly out of scope

Payments/invoicing; PO generation; auto-accept/auto-order (never); the supplier dashboard
(consumes this data later); quote negotiation/counter-offers (a buyer note→supplier round-trip
is future); multi-currency; the reply-parser changes (path D exists; only its confirm action
now writes a path-C record — small seam, in scope).

## 10. Success criteria (falsifiable)

1. **Unclaimed path:** a quote token from an RFQ send → form → submission with pn_confirmed →
   quote `active` → the supplier's card promotes to Band A on the live run — with NO account,
   NO claim. The claim pitch renders post-submit.
2. **Onboarded path:** DXP submits via portal → promotes to TOP of Band A ("Your supplier
   confirmed") — the full onboarding-benefit loop, live end-to-end.
3. **Concierge path:** an emailed quote keyed in by admin produces the identical promotion.
4. **Wrong-part gate:** a submission with an edited PN does NOT promote; it lands in review
   with pn_differs; approval promotes it labelled as the quoted PN (equivalent-alternative
   framing), never silently as the requested PN.
5. **Sanity flag:** a 100x price lands in review, not on the buyer's screen.
6. **Supersede/expiry:** a second submission supersedes the first; expiry demotes the card
   back to outreach state — both visible on the live run.
7. **Token security:** hashed at rest; token scoped to its RFQ (cannot quote another request);
   expired/withdrawn RFQ renders the closed state; enumeration returns uniform 404 (the
   portal-token posture).
8. **No auto-order:** property test — no path from quote submission to order placement.
9. **Flag-off:** QUOTE_SUBMIT_V1 off ⇒ endpoints absent, byte-identical; suite green
   (≥2122/73 + new tests).
10. All quote acks/notifications stubbed under the existing send gates; zero live sends
    introduced by this feature.

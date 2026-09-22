# FRONTEND REWORK BRIEF — Buyer & Supplier Surfaces (Kimi 3)

**Scope: FRONTEND ONLY.** A visual/UX rework of the two user-facing surfaces of Gofer — the
buyer (procurement) app and the supplier (portal + public quote) surface — consuming the
EXISTING API exactly as it is. Zero backend changes: `git diff` outside `frontend/` must be
empty at every commit. This is a restyle-and-restructure of presentation, not a product
redesign: every capability listed below already exists and already works; the job is to make
it coherent, credible, and fast to use.

**Repo:** `C:\dev\_Arkim\Arkim Procurement Agent Prototype` · frontend at `frontend/`
(Next 15 / React / TypeScript / Tailwind). Backend runs separately:
`$env:RANKING_BANDS_V1='1'; $env:QUOTE_SUBMIT_V1='1'; $env:SUPPLIER_PORTAL_V1='1'; $env:TIER1_V2='1'; $env:INTAKE_TYPE_AWARE='1'; $env:SCORING_V2='1'; $env:RUN_CAPTURE='1'; uv run uvicorn api_server:app --port 8001`
then `cd frontend; npm run dev` (:3000). Verify against live runs (the Gusher seal case is the
canonical fixture — create a fresh run through the UI to test with).

**Branch:** `feature/frontend-rework`. Commit per screen/component group. **NO PUSH.**
`npm run type-check` and `npm run lint` clean at every commit.

---

## 1. NON-NEGOTIABLE INVARIANTS (the honesty layer — breaking any of these fails the work)

These are product law, established deliberately. Restyle them freely; never weaken them:

1. **The seller is always named.** Every finding card headlines the actual vendor
   (`vendorName`), never a generic "Available through Gofer". "Order through Gofer" appears
   only as the action/button, never as the seller identity.
2. **Evidence is always linked.** Any card with a `url`/`sourceUrl` shows a working
   "View listing ↗" (new tab, `rel="noopener noreferrer"`).
3. **The found part number is structural** on the card (not buried in prose). Quotes approved
   with a differing PN are labelled as the QUOTED PN with equivalent-alternative framing —
   never silently shown as the requested PN.
4. **No fabricated numbers.** Candidates without computed scores/prices show none. No invented
   ratings, stars, or percentages anywhere.
5. **Price copy matches price state.** Verified price ≠ indicative price ≠ unverified price —
   three distinct visual/copy states; an indicative price never claims "available immediately
   at this price"; the reconciled copy is "listed price is indicative — Gofer confirms the
   final price before you're charged". `priceUnverified` renders its warning state.
6. **Findings vs outreach are structurally distinct.** `findings[]` render as option cards;
   `outreachTargets` render as a status block — no scores, no prices, no Order button, not
   card-styled. Onboarded supplier named FIRST with a "Your supplier" badge. Copy is
   INTENT-only ("we're asking") — never claims an email was sent.
7. **The empty state is honest.** Zero findings + outreach targets ⇒ "We didn't find this part
   listed — we're requesting quotes from N suppliers", never an empty "best options" list.
8. **No Order button where there is nothing to order** (no price ⇒ no order CTA — "Get quote"
   at most).
9. **Both payload shapes render.** Banded payloads (`findings[]` present) use the banded
   composition; legacy payloads (no banded keys) must still render correctly via the tier
   arrays. Detection stays payload-keyed (`Array.isArray(sr.findings)`), never an env read.
10. **Accessibility floor:** WCAG-AA contrast on both palettes, ≥44px touch targets, visible
    focus rings, status never conveyed by colour alone, semantic landmarks, keyboard-complete.

## 2. BUYER SURFACE — features, widgets, controls

**Persona & jobs:** a plant maintenance manager / storeroom lead. Jobs: (1) "this part failed —
get me options fast", (2) "what needs my attention?", (3) "approve/track orders",
(4) "what did we pay last time?". Design temperament: industrial, calm, dense-but-legible;
current dark theme + existing procurement palette tokens are the base — refine, don't rebrand
(logo/wordmark `gofer` stays).

Screens & their widgets (all exist — rework composition/hierarchy/flow):

**2.1 Home / "What needs me"** — the triage view.
- Attention queue: runs awaiting clarification answers, orders awaiting approval, quotes
  arrived (promoted findings) since last visit, in-flight run progress. Card-list widget with
  status chips + deep links.
- Primary CTA: New request (photo-first).
- Site/plant switcher (existing site_shipto data) in the shell; notifications bell (existing
  events feed) with an honest unread model.

**2.2 New request (intake)** — the front door; speed is the product here.
- Photo dropzone + mobile camera capture; multi-image; upload progress; text field for
  "or describe it"; paste-an-email affordance can be a styled textarea (email CHANNEL stays
  off — do not build channel UI).
- Clarification exchange: the anchored-clarification Q&A renders as a focused, single-question
  step (component-anchored questions verbatim from the API), quantity capture with unit
  stepper, variant disambiguation as tappable choice chips (options verbatim from API).
- Identification confirmation card: manufacturer / model / PN / confidence, editable-before-
  confirm fields as the API allows; explicit Confirm control (maps to confirm-intake).
- Run progress: a stepper (Identify → Search → Options) driven by existing run status — no
  fake progress bars; show real stage labels only.

**2.3 Options / results** — the money screen. Composition top-to-bottom:
- Part & asset summary panel (identity, specs, "identified from your request").
- **Recommended banner** (exists) — keep, restyle.
- **Findings** (Band A/B cards, server order — DO NOT re-sort): card = vendor name · PN line ·
  evidence badge (Exact replacement / Equivalent alternative — existing semantics) · price
  block (three states per invariant 5) · lead time · View listing ↗ · Order-through-Gofer or
  Get-quote CTA · collapsible "Why?" panel (existing explanation content).
- **Deduplicate by domain at RENDER time**: the same vendor appearing at multiple URL
  granularities collapses to ONE card (keep the richest: priced > exact-PN > bare); a subtle
  "also listed at N pages" affordance may expose the rest. Pure presentation — do not touch
  the API.
- **Priced vs quote-needed separation**: within the findings list, visually group "buy now"
  (has price) above/apart from "quote needed" (no price) WITHOUT violating server band order
  across the groups' internal ordering (grouping is presentation; if server order interleaves,
  group-then-preserve-relative-order).
- **Outreach block** (invariant 6) — restyle as a first-class "In progress on your behalf"
  panel; consider placing a compact version ABOVE findings when findings are thin (≤2) and
  below when rich — the buyer should always see "your supplier is being asked" without
  scrolling on thin results.
- Quote-promoted cards ("{Supplier} confirmed: $X · {lead} · confirmed {date}") get a distinct
  "confirmed by supplier" treatment — this is the platform's proudest moment; make it read
  as such honestly.

**2.4 Approvals** — queue table (existing approval rules/history data): request, requester,
amount, chosen option summary (vendor+PN+price), approve/reject controls with confirmation,
decision history. Approver-role visibility only (see RBAC).

**2.5 Orders / History & prices** — order list with status tracker (existing statuses; manual
fulfilment — no fake carrier tracking), per-part price history from price_db data as a simple
sparkline/table ("last paid", "listed range"), reorder affordance IF an existing endpoint
supports it (reorder module exists) — otherwise link back into New request prefilled, and note
the gap in the report rather than inventing an endpoint.

**2.6 Delivery settings** — site ship-to management (existing site_settings): list/edit forms,
role-gated.

**2.7 Shell** — left nav (current structure is right: What needs me / New request / Approvals /
History & prices / Delivery settings), tenant + plant context always visible, theme toggle if
trivially cheap (existing tokens), empty/loading/error states for EVERY screen (skeletons, not
spinners, for lists).

## 3. SUPPLIER SURFACE — features, widgets, controls

**Personas:** (a) an UNCLAIMED supplier holding a quote link from an RFQ email — zero patience,
possibly on a phone in a warehouse; (b) a CLAIMED supplier in the portal. Design temperament:
lighter, simpler, mobile-first; existing portal palette tokens are the base. The two surfaces
share components but the quote form must feel effortless standalone.

**3.1 Public quote form `/quote/{token}`** (exists — polish to excellence; this is the
supplier's first product experience):
- Header: the request (part identity, qty, need-by) + who's asking (Gofer, on behalf of a
  manufacturing customer) — nothing else exposed.
- The five fields (quote #, unit price, qty prefilled-editable, lead time or "in stock"
  toggle, PN confirmation prefilled) + optional freight and valid-until + notes. Single
  screen, big targets, numeric keyboards on mobile, inline validation.
- PN edit ⇒ a calm inline notice ("you're quoting a different part number — we'll review
  before showing the buyer") — matches the pn_differs behavior; never block submission.
- Post-submit: confirmation with their quote summary + the claim pitch (unclaimed only,
  as the API's `claim_pitch` indicates) — invitation framing, one CTA.
- Revision flow: returning to the link shows their latest quote and a clear "update your
  quote" (supersede semantics); closed/expired RFQ shows the honest closed state.
- States: submitted / in-review / closed / error — all designed, none dead-ends.

**3.2 Claim landing + profile (portal, token-authed)** (exists): welcome/confirm identity,
profile summary, brands & classes editor (propose→approve model — pending-approval states must
be visible: "submitted for review", never silently absorbed), demand teaser panel (existing
teaser data; honest counts only).

**3.3 Portal — open requests & quote history** (exists): open RFQs list → the same QuoteForm
component; own quote history with status chips (active / superseded / expired / in review);
NOTHING cross-supplier ever renders. Dashboard metrics (wins/losses) are OUT OF SCOPE — do not
build placeholder widgets for data that doesn't exist; the history list is the v1 dashboard.

## 4. RBAC — render what the backend grants; invent nothing

The frontend ENFORCES nothing security-critical (the API does); it RENDERS capabilities.
Rule: derive visibility from session/payload data that already exists; where a distinction
below has no backend signal yet, fall back to showing the surface the CURRENT app shows, and
LIST the gap in the report — do not fabricate client-side roles.

| Role | Auth mechanism (existing) | Sees / does |
|---|---|---|
| Buyer — Requester | tenant session (existing auth) | New request, own runs, options, order (subject to approval rules), history, home triage |
| Buyer — Approver | tenant session + approval rules | Requester surface + Approvals queue + decisions |
| Buyer — Site admin | tenant session | + Delivery settings (ship-to) |
| Gofer concierge/admin | admin token (internal) | Internal admin surfaces (labeling, run capture, release queue, review queues) — **OUT OF SCOPE for this rework**: do not restyle, do not break routes |
| Supplier — unclaimed | quote token (single-RFQ scope) | The quote form for THAT request only; no history, no portal |
| Supplier — claimed | portal claim token | Portal: profile, brands/classes (propose→approve), teaser, open requests, own quote history |

Hard rules: supplier surfaces never leak other suppliers, buyer identity beyond what the RFQ
email carried, or internal scores; buyer surfaces never leak supplier-economics internals;
token URLs never logged to third-party analytics (introduce NO analytics).

## 5. DESIGN SYSTEM EXPECTATIONS

- Consolidate into a token-driven component library within `frontend/` (buttons, cards,
  badges, chips, tables, forms, empty/loading/error, stepper, banner) — both palettes
  (procurement dark, portal light) from ONE component set. Kill one-off styles as you go.
- Typography scale, spacing rhythm, and state styles documented in a short
  `frontend/DESIGN_NOTES.md` (what the system is, how to extend it).
- Every interactive element: hover, focus-visible, active, disabled states.
- Responsive: buyer surface usable at 768px+; quote form and portal excellent at 360px+.
- Performance sanity: no new heavyweight deps without justification in the report; images
  lazy; route-level code splitting as Next provides.

## 6. OUT OF SCOPE (hard)

Backend/API changes of ANY kind · admin/internal tools restyle · new analytics ·
auth/login redesign · email templates · dashboard metrics widgets (no data yet) ·
SMS/voice/email-channel UI · payments UI · dark/light rebrand or logo change ·
i18n · anything requiring a new endpoint (list wished-for endpoints in the report instead).

## 7. SUCCESS CRITERIA (falsifiable)

1. `git diff --stat` shows changes ONLY under `frontend/` at every commit. tsc + lint clean.
2. **Invariant audit passes** (§1, all 10) on a LIVE flag-on run — screenshot each invariant
   demonstrably holding on the options screen (Gusher case: Seal It named+linked+priced,
   outreach block with "Your supplier DXP" first, dedup collapsing Seal It/Seal It 123).
3. Legacy payload (flags off / old runs) still renders the options screen correctly —
   screenshot.
4. The quote form: submit, revise (supersede), pn-differs notice, closed state — all four
   screenshotted live against the real API; usable one-handed at 360px.
5. Portal: claim → profile → brands edit (pending state visible) → open requests → quote →
   history — walked through live, screenshotted.
6. Approvals + Orders + Delivery settings render real data with designed empty states.
7. Keyboard-only walkthrough of: new request → confirm → options → order; and token → quote
   form → submit. No traps, visible focus throughout.
8. Every screen has designed loading/empty/error states (grep for raw "Loading..." — none).
9. Render-time dedup and priced-vs-quote grouping demonstrably preserve server band order
   within groups (a short unit test on the composition function).
10. `FRONTEND_REWORK_REPORT.md` at repo root: per-screen before/after screenshots, component
    inventory, RBAC gaps found (§4 rule), wished-for endpoints, decisions taken. NO PUSH.

---

## KICKOFF PROMPT (paste to Kimi 3)

You are doing a FRONTEND-ONLY rework of the Gofer app. Read ./FRONTEND_REWORK_BRIEF.md in
full — it is the contract. If it is missing, STOP.

Ground rules, absolute:
1. Changes ONLY under frontend/. `git diff` outside frontend/ empty at every commit. No new
   endpoints, no API changes — consume payloads exactly as served.
2. The §1 HONESTY INVARIANTS are product law. Restyle anything; weaken nothing: seller always
   named, listing always linked, no fabricated numbers, three price states, findings vs
   outreach structurally distinct, honest empty state, no Order button without a price, both
   payload shapes render, WCAG-AA + 44px + focus rings.
3. Branch feature/frontend-rework. One commit per screen/component group. npm run type-check
   and npm run lint clean at every commit. NO PUSH.
4. Verify against the LIVE app, not just storybook-style isolation: backend flag-on per the
   brief's command, create a real run (the Gusher seal nameplate case), screenshot every
   success criterion. Sections 2-3 define the screens/widgets; §4 is the RBAC rendering rule
   (render what the backend grants; where a role distinction has no backend signal, keep
   current behavior and LIST the gap — never fabricate client-side roles); §7 is what done
   means.
5. Work order: (a) design tokens + shared component library first; (b) buyer options screen
   (the money screen — invariants live here); (c) buyer intake flow; (d) buyer shell/home/
   approvals/orders/settings; (e) supplier quote form; (f) portal. Commit each.
6. Final act: FRONTEND_REWORK_REPORT.md at repo root per §7.10. If any brief requirement
   can't be met with the existing API, do NOT invent — document it in the report and move on.

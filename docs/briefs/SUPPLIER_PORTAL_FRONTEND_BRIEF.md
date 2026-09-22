# FRONTEND BUILD BRIEF — Supplier/Vendor Claim Portal (UI)

Builds the customer-facing UI for the supplier claim portal. The **backend is complete, verified, and merged** (Night 6 + follow-up fixes, on `test/flag-on-integration`). This brief is the remaining UI surface: the public claim page and the admin link/review controls. No new backend logic — the API contract below is fixed and live-verified.

**Session type:** supervised (frontend, first customer-facing surface, brand-sensitive). Build in a session; the *visual/brand polish* gets human review — taste can't be unit-tested. NO PUSH; stop for review.

---

## MISSION

Two surfaces against the existing portal API:
1. **Public claim page** at the frontend route the concierge sends a supplier (`link_path` = `/portal/{token}`): demand teaser (the hero) + a low-friction profile-confirm form + submit-for-review.
2. **Admin controls** in the existing admin supplier view: a "Generate claim link" button (shows the link once), a regenerate action, and a pending-revision review panel (approve/reject) that completes the propose→approve loop.

This is the app's **first public, unauthenticated, customer-facing page** — token in the URL, no login. Security and honesty posture matter more than polish.

---

## THE VERIFIED API CONTRACT (fixed — do not change the backend)

All shapes below were confirmed live against a running server. Build to these exactly.

**Public — `GET /api/portal/{token}/profile`** → 200 with:
```json
{
  "teaser": { "has_matches": false, "count": 0, "window_days": 30,
              "framing": "Buyers post procurement requests across your categories. Confirm your profile — brands, classes, and ship area — so <BRAND> can match you to the right requests." },
  "supplier_domain": "dxpe.com",
  "name": "DXP Enterprises",
  "brands": [ { "brand_id": "...", "relationship": "AUTHORIZED|CARRIES|AFTERMARKET_COMPATIBLE", "evidence": "...", "classes_for_brand": [...] } ],
  "classes": [ { "class_id": "...", "is_core": true, "subtype": "..." } ],
  "ship_area": null,
  "aftermarket_disclosure": "Aftermarket-compatible part — not the OEM brand..." | null
}
```
- When `teaser.has_matches` is `true`: `count` and `window_days` are set, `framing` is `null`.
- When `false` (zero-state): `count:0`, `framing` is the honest category text, `has_matches:false`.
- Invalid / expired / reused token → a **uniform safe rejection** (same response for all three — the backend gives away nothing; the UI must not either).
- A freshly-claimed supplier legitimately returns empty `brands`/`classes` and `null ship_area` — that is the normal "come fill me in" state, not an error.

**Public — `POST /api/portal/{token}/propose-revision`**, body:
```json
{ "brands": [{ "brand_id": "...", "relationship": "AUTHORIZED" }],
  "classes": [{ "class_id": "...", "is_core": true }],
  "ship_area": { "kind": "NATIONWIDE_US" } | { "kind": "STATES", "states": ["OH","TX"] } }
```
→ returns a revision id + pending status. **This never writes the registry** — it lands as a pending revision the concierge approves. The UI's success message must say "submitted for review," not "saved."

**Admin — `POST /api/admin/suppliers/claim-link`** (auth header), body `{ "supplier_domain": "dxpe.com" }` → returns `{ token, token_id, expires_at, link_path, supplier_name }`. **The raw `token` is returned ONCE and never again** (hashed at rest). The UI must treat it as show-once.

**Admin — `POST /api/admin/suppliers/claim-link/regenerate`** — revokes the prior token, mints a new one (same show-once rule).

**Admin — `POST /api/admin/portal/revisions/{revision_id}/approve`** and **`/reject`** (auth header) — the concierge decision. Approve applies the pending scope to the registry; reject discards it.

(Confirm the admin auth mechanism and the exact pending-revisions *list* endpoint in the investigation gate — the approve/reject verbs are known; the list/read may need locating.)

---

## SETTLED DESIGN DECISIONS

1. **Demand is the hero.** The teaser is the FIRST and most prominent element on the public page — above the profile form. The strongest, most consistent finding across the supplier research: suppliers value portals that surface demand and resent portals that only extract admin. Lead with what they get, not what you want from them.
2. **The honesty carve-out (non-negotiable).** Zero-state (`has_matches:false`) renders the `framing` text as the hero — NEVER a "0 matches" number, NEVER a fabricated/placeholder count, NEVER demo data. The backend already enforces this; the UI must not reintroduce a fake number to fill the space. A newly-onboarded supplier with no demand sees honest category framing, full stop.
3. **Low-friction edit (anti-Ariba).** The trio — brands / classes / ship-area — is editable in a single lightweight pass on one page, not a multi-screen wizard. One "Submit for review" action. The **tri-state brand relationship is the centerpiece** (only the supplier authoritatively knows authorized vs carries vs aftermarket-compatible) — it gets the most prominent, clearest control.
4. **Show-once tokens.** The admin link UI displays the raw link exactly once with a copy button and a clear "copy and send now — you won't see this again" note, plus the expiry date. Regenerate is available if lost.
5. **Uniform rejection.** Invalid/expired/reused all render the same generic "this link is no longer valid — contact your rep for a new one" page. The UI must not branch on or reveal which failure occurred.

---

## DESIGN DIRECTION

Read `/mnt/skills/public/frontend-design/SKILL.md` first. This surface's thesis is literally "lead with demand," so the hero is the teaser — build it as the memorable element and keep everything else quiet and disciplined around it.

**Build the structure, states, and behavior correctly on-palette now; final art lands later.** The logo and any illustration are going to a designer, and the brand name isn't trademark-settled. So: use the palette tokens, restrained clean type, and a solid layout — but do NOT over-invest in decorative choices (custom illustrations, elaborate motion) that a designer will redo. The goal is a portal that is correct, honest, on-palette, and structured so a later visual pass drops in without rework — not a portal that looks finished-and-permanent.

**Palette — define as CSS custom properties / design tokens (single source), from day one:**
```
--graphite:#26282B   text, headings, primary
--slate:#4E5A63      secondary text, borders, chrome
--orange:#F4581C     the ONE action accent — primary button, key highlight; used sparingly
--amber:#E8A33D      "needs review / pending" state
--green:#2E9E6B      success / confirmed
--paper:#F7F6F2      background (warm off-white, not clinical white)
--ink:#1A1D1F        dark surfaces
```
Orange is a seasoning, not a theme — if everything is orange, nothing reads as the action. Contrast floor: graphite/slate on paper pass AA; orange is for buttons/accents, never body text.

**Loading state:** a branded loading indicator while the profile fetches. A simple on-palette spinner is fine for now; the digging-gopher loader can drop in later once the character design is finalized — do not block on it, and do not ship the placeholder gopher as if it were final art.

**Mobile-legible:** a distributor inside-sales rep may open the link on a phone. The public page must render cleanly at ~380px — this is not a nice-to-have; it's the likely first view. Touch targets ≥44px.

**Public page layout (demand-as-hero):**
```
┌───────────────────────────────────┐
│  <BRAND>                DXP Ent.   │  minimal header: brand mark + supplier name
├───────────────────────────────────┤
│  ╔═════════════════════════════╗  │
│  ║   DEMAND TEASER — HERO       ║  │  largest, first, above the fold
│  ║   has_matches:               ║  │
│  ║     "12 buyers matched your  ║  │
│  ║      categories in the last  ║  │
│  ║      30 days"                ║  │
│  ║   zero-state:                ║  │
│  ║     framing text (no number) ║  │
│  ╚═════════════════════════════╝  │
│                                   │
│  Confirm your profile             │  second element
│  ┌─────────────────────────────┐  │
│  │ Brands  (tri-state — hero    │  │  the centerpiece control
│  │   field of the form)         │  │
│  │   Goulds   [Authorized ▾]    │  │
│  │   + add brand                │  │
│  ├─────────────────────────────┤  │
│  │ Classes                      │  │
│  ├─────────────────────────────┤  │
│  │ Ship area  (Nationwide/States)│ │
│  └─────────────────────────────┘  │
│  aftermarket disclosure (shown if │  so they see what buyers see
│    they carry aftermarket brands) │
│  [ Submit for review ]  (orange)  │
└───────────────────────────────────┘
```

**Public page states (all required):**
- Loading → branded spinner.
- Valid + has_matches → teaser with count/window as hero, then editable profile.
- Valid + zero-state → framing text as hero (no number), then editable profile.
- Invalid/expired/reused → uniform safe rejection page.
- Submitted → "Thanks — your rep will review and confirm these changes" (amber "pending" tone, not green "saved" — nothing is live until the concierge approves).
- Submit error → soft error that PRESERVES the supplier's input (never make them re-enter).

---

## SECURITY POSTURE (first public route — highest priority)

- **The token lives in the URL. Treat it as a secret.** Do NOT: store it in `localStorage`/`sessionStorage`/cookies; send it to any third party (analytics, error/crash reporting, tag managers, CDN beacons); include it in any logged URL or console output. Keep it in memory for the page's lifetime only.
- The page issues **no auth cookies / no persistent session** — a claim link is single-use-ish and expiring, not a login.
- Set `Referrer-Policy: no-referrer` on the page (meta or header) so an outbound click can't leak the token via Referer (the API already sets it server-side; the page should too).
- The public page must fetch ONLY `/api/portal/{token}/*`. It must never call an admin endpoint, and must expose no admin data even if a bug returned some.
- **Uniform rejection in the UI:** on any non-200 from the profile fetch, render the single generic rejection — do not surface status codes or distinguish expired vs invalid vs reused to the user.

---

## GUARDRAILS

1. No backend changes. The API contract above is fixed. If the UI seems to need a backend change, STOP and report — don't modify endpoints to fit the UI.
2. All new UI gated so it's inert without the portal being live (mirror the backend's `SUPPLIER_PORTAL_V1` posture at the app/route level as the existing frontend does for flagged features — confirm the pattern in the investigation gate).
3. Do NOT scaffold the deferred dashboard panels (RFQ inbox, order pipeline, payments/remittance, win/loss diagnostics, performance scorecards, demand heatmaps). Panels grow onto this same surface LATER. Build only the claim page + admin controls in this brief.
4. No new heavy dependencies without justification — reuse the existing frontend's framework, router, HTTP client, and component/styling conventions (investigation gate identifies them).
5. Brand-string discipline (see below) — no hard-coded "Arkim"/"Gofer" scattered through components.
6. NO PUSH. Commit per task; stop for review.

---

## INVESTIGATION GATE (read-only; report with file:line before building)

- **I1.** The existing frontend: framework, router, build tooling, HTTP/client pattern, and the component/styling convention (CSS modules? Tailwind? styled-components?). The portal UI must match, not introduce a parallel stack.
- **I2.** How the existing admin supplier view is structured and where a per-supplier "Generate claim link" button attaches cleanly. Also locate the admin **pending-revisions list/read** endpoint (approve/reject verbs are known; the list may need finding) so the review panel has data to show.
- **I3.** The existing pattern for public vs authenticated routes and for feature-flag gating in the frontend — reuse it for the portal route and its inertness.
- **I4.** Any existing loading/error/empty-state components to reuse (so the portal matches the app's established states rather than inventing new ones).

---

## BUILD TASKS

- **T1. Brand-string constant.** One place defining the customer-facing brand name (see brand note). Everything below references it.
- **T2. Public claim page — data + states.** Route at `/portal/:token`, fetch `/api/portal/:token/profile`, implement all six states above (loading / has-matches / zero-state / rejection / submitted / soft-error). Demand teaser as hero. Honest zero-state.
- **T3. Profile-confirm form.** Single-pass edit of brands (tri-state relationship control — the centerpiece), classes, ship-area; display the aftermarket disclosure when present; one "Submit for review" → `propose-revision`; success = pending message, input preserved on error.
- **T4. Admin "Generate claim link".** Button in the admin supplier view → calls claim-link → show-once panel (copy button, expiry, "won't see again" note) + regenerate.
- **T5. Admin revision-review panel.** Surface pending supplier-proposed revisions in the admin review UI with approve/reject wired to the admin endpoints, so the propose→approve loop is completable from the UI.
- **T6. Security + a11y pass.** Token hygiene (no storage, no third-party leak, no-referrer); uniform rejection; responsive to ~380px; visible keyboard focus; reduced-motion respected. Add the tests the existing frontend supports (component/integration).

---

## SUCCESS CRITERIA (falsifiable)

1. Valid token → teaser renders as the hero; profile fields populate; page works at 380px.
2. Zero-state token → framing text as hero, NO "0", NO fabricated number.
3. Invalid / expired / reused token → identical generic rejection; no status/oracle leaked.
4. Editing + submit → lands as a pending revision (verified via the admin panel); registry unchanged until approved; UI says "submitted for review," not "saved."
5. Admin generate-link → link shown once with copy + expiry; regenerate mints a new one.
6. Admin approve → the pending revision applies (registry updates); reject → discarded.
7. Token never written to storage, never sent to a third party, never logged.
8. The public page calls only `/api/portal/*` and exposes no admin data.

---

## MORNING / REVIEW VERIFICATION

Generate a claim link for DXP from the admin UI → open it in a fresh incognito window (no admin session) → confirm teaser + profile render, no admin data reachable → edit a brand relationship → confirm it lands as pending (admin panel), registry unchanged → approve → confirm it applies → hit the route with a garbage token → confirm the uniform rejection → confirm the whole thing is legible on a phone-width viewport.

---

## BRAND-STRING NOTE (decide before this ships to a real supplier)

This is the first surface a real supplier will see, so the brand name on it matters. The name is unsettled: **"Gofer" / "Gofer AI"** is the direction but is pending a USPTO Class 35 search and the gofer.ai/goferai.com ownership check — not yet cleared. The backend zero-state text currently says **"Arkim"** (`supplier_portal.py`, `_ZERO_STATE_FRAMING`).

Therefore: put the customer-facing brand name in **one constant** (T1), defaulting to the current value, so the eventual swap to "Gofer" is a one-line change in one place — not a scatter-hunt across components. Flag as a follow-up that the **backend** `_ZERO_STATE_FRAMING` string carries the same brand name and should be swapped in the same motion when the name settles. Do not hard-code the name anywhere else.

---

## OUT OF SCOPE

The fuller supplier dashboard panels (RFQ inbox, orders, payments, scorecards, win/loss, heatmaps), automated claim-link email sending, domain-matched email verification, final logo/illustration/character art, and the digging-gopher loader as final art. Structure and states now; visual polish and panels later.

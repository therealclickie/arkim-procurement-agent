# FRONTEND TEST FLOOR REPORT

**Arc:** 1 — Frontend test floor (portal / quote / admin surfaces). Tests only, no behaviour change.
**Branch:** `arc1/frontend-test-floor` (cut from `test/flag-on-integration` @ `074d126`).
**This document, at this commit:** the INVESTIGATION GATE only (I1–I6). No tests written yet, no
source touched. Build-task findings and the final test count will be appended as T1–T8 land.

**Baseline verified (read-only run):** `npm test` in `frontend/` on 2026-09-20 →
**8 passed (1 file)**, vitest 4.1.10, 546 ms. Matches the brief.

---

## I1. Test tooling

| Item | State | Evidence |
|---|---|---|
| vitest | **4.1.10 installed** (`^4.1.10` declared) | `frontend/package.json:50`; `frontend/node_modules/vitest/package.json` |
| Config location | `frontend/vitest.config.ts` — single config, no workspace file | `frontend/vitest.config.ts:6-9` |
| Environment | `"node"` — **no DOM**; comment states composition functions are React-free by design | `frontend/vitest.config.ts:8` |
| Include pattern | `src/**/*.test.ts` — **does not match `.test.tsx`** | `frontend/vitest.config.ts:8` |
| Alias | `@` → `frontend/src` | `frontend/vitest.config.ts:7` |
| Setup file | **None** (no `setupFiles`, no vitest.setup.* anywhere) | `frontend/vitest.config.ts:6-9` |
| Test script | `"test": "vitest run"` | `frontend/package.json:11` |
| `jsdom` | **NOT installed** — lockfile hits (`frontend/package-lock.json:8680-8715`) are vitest's *optional* peerDependencies; absent from `node_modules` | verified `Test-Path node_modules\jsdom` → absent |
| `happy-dom` | **NOT installed** | same check |
| `@testing-library/react` / `user-event` | **NOT installed** | `node_modules\@testing-library` absent |

**T1 is required** (the gate confirms the infrastructure is missing). Notes for T1:

- vitest 4 **removed `environmentMatchGlobs`** (verified absent from `vitest/dist`). The two
  supported routes are (a) the per-file docblock pragma `// @vitest-environment jsdom`
  (parse regex confirmed in vitest dist: `node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:100`)
  or (b) `test.projects` (workspace-style split).
- **Recommended: (a) docblock pragma.** Zero risk to the existing 8 node-environment tests; the
  include pattern must in either case grow to `src/**/*.test.{ts,tsx}` because `*.test.ts`
  does not match `.tsx` files (`frontend/vitest.config.ts:8`).
- New devDependencies (per brief guardrail 4): `jsdom`, `@testing-library/react`, `@testing-library/user-event`.
  Installed React is 18.3.1 (`frontend/node_modules/react/package.json`) so
  `@testing-library/react@^16` is the compatible major.

---

## I2. Next.js version, router mode, and the server/client split

**Next.js 15.5.18 installed** (`^15.3.2` declared, `frontend/package.json:29`); React 18.3.1 /
react-dom 18.3.1 installed. **App Router confirmed** (`src/app/` tree with `layout.tsx`, `[token]`
dynamic segments).

### Component split (the testability map)

| File | Mode | Evidence |
|---|---|---|
| `src/app/portal/[token]/page.tsx` | **SERVER** | no `"use client"`; exports `dynamic` (`:30`) and `metadata` (`:32-40`); uses React `use(params)` (`:27`, `:43`) |
| `src/app/quote/[token]/page.tsx` | **SERVER** | no `"use client"`; `dynamic` (`:24`), `metadata` (`:26-30`), `use(params)` (`:21`, `:33`) |
| `src/app/portal/[token]/claim-page.tsx` | CLIENT | `"use client"` line 1 |
| `src/app/portal/[token]/profile-form.tsx` | CLIENT | line 1 |
| `src/app/portal/[token]/open-requests.tsx` | CLIENT | line 1 |
| `src/app/portal/[token]/portal-states.tsx` | CLIENT | line 1 |
| `src/app/quote/[token]/quote-page.tsx` | CLIENT | line 1 |
| `src/app/quote/[token]/quote-form.tsx` | CLIENT | line 1 |
| `src/app/admin/page.tsx` | CLIENT | line 1 |
| `src/app/admin/portal-admin.tsx` | CLIENT | line 1 |
| `src/components/ui/gofer-loader.tsx` | CLIENT | line 1 |

### ⚠ Gate finding: the two server `page.tsx` files are NOT renderable under vitest

Both `page.tsx` files import `{ Suspense, use } from "react"` (`portal/[token]/page.tsx:27`,
`quote/[token]/page.tsx:21`). **The installed top-level React 18.3.1 does not export `use`**
(verified: `exports.use` absent from `node_modules/react/cjs/react.production.min.js`; also no
`cache`). The app runs only because Next 15 App Router substitutes its own **vendored React 19**
(`node_modules/next/dist/compiled/react*`) at build time — a substitution vitest/vite does not
perform (no react alias in `frontend/vitest.config.ts:7`). Rendering (or even calling) these
modules under vitest crashes on `use is not a function`.

Consequences for the build tasks:

- **The real behavioural surface for T2/T6 is the client bodies** — `ClaimPage` (token prop,
  `claim-page.tsx:42`) and `QuotePage` (`quote-page.tsx:41`). Both render directly in jsdom.
  This loses nothing: the server pages are 49/40-line wrappers (Suspense + `use` + inert
  `metadata`/`dynamic` constant exports) containing zero logic.
- **Success criterion 2** ("every route file listed … has at least one test exercising it") is
  satisfiable for the two `page.tsx` files only via (a) a **structural test** — import the module
  (module-level exports are reachable; only *rendering* crashes) and assert `metadata.referrer ===
  "no-referrer"`, `dynamic === "force-dynamic"`, and the token→child wiring by source scan — or
  (b) a `vi.mock("react")` partial mock injecting a minimal `use` shim (throw-the-thenable
  Suspense, which React 18 supports; the test controls the params promise). Option (b) keeps it a
  real render test but adds a harness shim that must itself be trusted. **Builder will use (a)
  structural + full behavioural coverage of the client body, and flags this decision for the
  reviewer** — if the reviewer judges structural coverage insufficient, the honest fix is a
  React 19 dependency bump, which is **out of arc scope** (`react` is a dependency, not a
  devDependency; prime directive forbids touching it).

---

## I3. How each route fetches — the single seam

Every network call on the three surfaces flows to **global `fetch`** from exactly four wrappers:

| Surface | Wrapper | Fetch call | Exported functions used by components |
|---|---|---|---|
| Portal (`/portal/[token]`) | `portalFetch` — `src/lib/portal-api.ts:89-107` | `fetch(`/api${path}`)` at `:91`; **no session header, no auth** (`:93-99`) | `getPortalProfile` (`:114`), `proposeRevision` (`:130`), `getOpenRequests` (`:183`), `getQuoteHistory` (`:192`), `submitPortalQuote` (`:213`) |
| Quote (`/quote/[token]`) | `quoteFetch` — `src/lib/quote-api.ts:79-101` | `fetch` at `:84`; no auth headers | `getQuoteContext` (`:108`), `submitQuote` (`:113`) |
| Admin (`/admin`) | local helpers `fetchAdmin` (`admin/page.tsx:58-70`) / `postAdmin` (`:72-89`) | `fetch` at `:59` / `:73`; adds `Authorization: Bearer <token>` (`:60`, `:74-76`); base from `process.env.NEXT_PUBLIC_API_URL` (`:26-29`, empty in tests → relative `/api/admin/...`) | not exported — page-internal |

**Recommended seam: stub `globalThis.fetch`** (`vi.stubGlobal`). One seam covers all three
surfaces, and — decisive for T3.3/T3.4 — it is the only level at which the **actual request URLs
and headers are observable**. Module-mocking `@/lib/portal-api` would hide exactly the URL/​header
facts the security tests must assert. Confirmed endpoint prefixes (backend, `api_server.py`):
portal `GET /api/portal/{token}/profile` (`:5553`), `POST .../propose-revision` (`:5588`),
`GET .../open-requests` (`:6289`), `GET .../quotes` (`:6340`), `POST .../quotes` (`:6370`);
quote `GET/POST /api/quote/{token}` (`:6217`/`:6255`). The brief's T3.4 prefix guess
(`/api/quote/*`) is confirmed exactly.

---

## I4. What `open-requests.tsx` actually does (load-bearing for arc 3)

`OpenRequests` (`src/app/portal/[token]/open-requests.tsx:37`) is the **Night 11
(QUOTE_SUBMIT_V1) path-B surface** — quote entry for *already-claimed* suppliers, inside the
claim portal. It was outside the earlier portal brief's scope (that brief predates Night 11) but
is wired in at `claim-page.tsx:140`, rendered **between the demand teaser and the profile form**.

**Data source:** on mount it calls `getOpenRequests` + `getQuoteHistory` in a `Promise.all`
(`open-requests.tsx:46-54`), both via the portal client — still `/api/portal/{token}/*`
(`portal-api.ts:183-198`), token-scoped to the supplier's own domain. Both re-run after a
successful inline submit (`:97`) so the quoted badge and history update honestly.

**States / behaviour:**

1. **Feature off or any fetch failure** → both results `null` → **renders nothing** (`:60`).
   Because the quote endpoints 404 uniformly when `QUOTE_SUBMIT_V1` is off, the pre-Night-11
   portal look is preserved with zero flag plumbing in the UI (`:50-53`, and the header comment
   `:12-15`).
2. **Open requests with data** → "Open requests for you" section (`:105-174`): per row,
   `manufacturer — part_number` (`:113-116`), qty + requested date (`:118-123`), a quoted badge —
   `✓ Quoted $X` (active) or `⏳ Quote in review ($X)` (`:125-137`) — and a Quote-this /
   Revise-quote / Close toggle button (`:138-150`).
3. **Quoting open** → the inline `QuoteForm` — **the same five-field form the public
   `/quote/{token]` page uses**, imported from `../../quote/[token]/quote-form` (`:25`,
   rendered `:160-167`) — prefilled: unit price from an existing quote, quantity + part number
   from the RFQ (`:62-76`); `revising` flag passed when a prior quote exists (`:166`).
4. **Submit failure** → soft-error alert with preserved input (`:154-159`; state kept in `form`,
   `:99`) — the same preserve-input posture as the claim form.
5. **Submit success** → form closes and data refreshes (`:94-97`).
6. **Quote history** → "Your quotes" section (`:176-196`): part, `$unit_price`, a status chip
   (`STATUS_LABEL` maps active/review/superseded/expired/withdrawn; **unknown values fall through
   verbatim, never masked** — `:28-35`, `:186-188`), and the submitted date.

**Submit payload** (`:82-92`): `{run_id, quote_number(trim), unit_price:Number, quantity:Number,
lead_time: "in stock" | "N days", part_number: trim || null, freight/valid_until/notes: trim || null}`.

**Honesty check (T5's concern):** empty requests → the section is hidden entirely
(`:105` length-guard); empty history → hidden (`:176`); both-null → renders null (`:60`).
**No fabricated or placeholder request exists.** T5 is expected to characterise clean — no
FINDING anticipated.

---

## I5. Admin auth mechanism

- **Bearer token in the `Authorization` header** — no session, no cookie. Set by the page-local
  `fetchAdmin`/`postAdmin` helpers: `admin/page.tsx:60` and `:74-76`
  (`Authorization: Bearer ${token}`).
- **How the page obtains it:** a password input on the token gate (`:488-494`, gate UI `:480-504`
  rendered whenever `!token`); `saveToken` persists it to **`localStorage` under
  `"arkim_admin_token"`** (`TOKEN_KEY` at `:31`; write `:212-215`, read-on-mount `:162-165`,
  clear `:217-221`). Env var name surfaced in the gate copy: `ARKIM_ADMIN_TOKEN` (`:485`).
- The page's own header comment is explicit that this localStorage holding is a UI convenience —
  **the real gate is server-side** `require_admin` → 401/403/503 on every `/api/admin/*`
  endpoint (`admin/page.tsx:6-11`; endpoints throughout `api_server.py`, e.g. `:3434+`).
- **T7.4 cross-check fact:** the public portal/quote clients attach **no** Authorization header
  (`portal-api.ts:93-99`, `quote-api.ts:84-92`) — admin material cannot appear in public-route
  fetch calls by construction; the T3.3/T3.4 URL assertions at the fetch seam cover it.

---

## I6. Existing conventions

Sole existing test: `frontend/src/components/proc/__tests__/options-compose.test.ts`.

- **Location:** `__tests__/` directory **sibling to the module under test**
  (`src/components/proc/__tests__/` beside `options-compose.ts`).
- **Naming:** kebab-case `<module>.test.ts`.
- **Imports:** plain `import { describe, it, expect } from "vitest"` (`options-compose.test.ts:5`).
- **Style:** file-header JSDoc stating what is under test (`:1-4`); a minimal **factory helper**
  (`cand()`, `:9-23`) with a JSDoc of its own; `describe` per behaviour, `it` titles as named
  behavioural statements; inline comments explaining *why* an assertion matters (e.g. `:53-56`).
- **No DOM, no mocks, no fixtures, no shared helpers, no setup file** exist anywhere yet — T1
  establishes these from scratch.
- Conventions to keep for the new tests: sibling `__tests__/` dirs, kebab-case names, named
  behavioural `it` titles (guardrail 3 bans snapshot-only anyway), factory helpers for API
  payload shapes (portal profile / quote context / claim link) rather than inline literals.

---

## GATE CONCLUSIONS — tasks that cannot be built exactly as written

Stated plainly, per the brief's instruction:

1. **T6.3 (three entry paths) is only two-thirds buildable.** The three documented paths
   (`QUOTE_SUBMISSION_SPEC.md:31-41`): **A** RFQ-email link → `/quote/{token]` page;
   **B** portal open-requests inline form; **C** concierge entry. Paths A and B are frontend
   surfaces and fully testable. **Path C has no frontend surface**: no code in `frontend/src`
   calls `/api/admin/quotes` (grep: zero matches), and the admin TABS list has no quotes tab
   (`admin/page.tsx:42-54`). Path C is a backend/admin-endpoint concern
   (`POST /api/admin/quotes`, `api_server.py:6422`). T6 will assert A and B reach the form, and
   record path C's absence of a frontend surface as a scoped limitation, not a test.
2. **T8's scan will FAIL against current source — one genuine hit.**
   `src/components/proc/gofer-mark.tsx:29` — `alt="Gofer"` — is a hard-coded brand string literal
   inside the T8 scan scope (`src/components`), outside `src/lib/brand.ts`. Per the prime
   directive: test written, observed failing, `.skip` + FINDING; source untouched. Scan-scope
   definition the builder will use (and flags for reviewer judgement): exact title-case string
   literals `"Gofer"` / `"Arkim"` per the brief's quoted strings — this excludes component/CSS
   identifiers (`"gofer"`, `"gofer--hat"` class strings at `gofer-loader.tsx:38`), comments, and
   lowercase storage keys (`"arkim:events:lastSeen"` at `components/proc/proc-shell.tsx:32`;
   `"arkim_admin_token"` at `admin/page.tsx:31` — admin is outside the specified scan dirs).
   Under that definition the hit count in scope is exactly one (`gofer-mark.tsx:29`).
3. **T8's backend-mirror clause is stale, not a defect.** The brief expects
   `utils/supplier_portal.py::_ZERO_STATE_FRAMING` to possibly still contain `"Arkim"`; it
   contains **`"Gofer"`** (`supplier_portal.py:60-63`, the name at `:62`) — consistent with
   `BRAND_NAME` (`src/lib/brand.ts:14`). No finding; the brief text predates the brand swap.
4. **T7.1 (show-once) — predicted finding, to be confirmed when the test is written.** The
   backend honours show-once (raw token returned once, hashed at rest — `admin/page.tsx:392-395`
   comment), and the UI clears `claimLink` on tab switch (`:208-210`) and on generate-failure
   (`:412`). But a **second fetch on the same tab does not clear it**: `load()` (`:167-200`)
   never touches `claimLink`, so after Generate → Refresh (same tab), the raw token **remains
   displayed**. The T7.1 assertion "a re-render or second fetch does not re-display it" is
   expected to fail → FINDING, skip-and-report. (Regenerate replacing the token — T7.2 — is
   clean: `:416-431`.)
5. **The two `page.tsx` route files cannot be *rendered* under vitest** (I2 gate finding above) —
   React 18.3.1 lacks the `use` export they import. Behavioural coverage goes to the client
   bodies; the route files get structural tests; a React 19 bump is the real fix but is out of
   arc scope (dependencies, not devDependencies).

Everything else in T2–T7 is confirmed buildable as written, with these test-design notes:

- **T2.1 loading is synchronously observable** — the `!mounted` guard renders `PortalLoading`
  on first paint (`claim-page.tsx:110-112`, loader + "Loading your profile…" `:190-197`), so the
  loading assertion needs no async trickery.
- **T2.2 document order:** teaser (`claim-page.tsx:127`) → soft-error (`:128-136`) →
  **OpenRequests (`:140`)** → profile form (`:141-147`). Teaser-before-form holds, but
  OpenRequests sits between them — the test must assert *before*, not *immediately before*.
- **T2.4/T3.5 uniform rejection is structurally guaranteed at the client seam** — `portalFetch`
  collapses every non-200 AND network error into one `rejected` outcome
  (`portal-api.ts:100-106`; the backend makes invalid/expired/reused/flag-off all 404 —
  `portal-api.ts:13-15`). The test will still drive three distinct non-200 responses
  (404/410/500) + a network throw at the fetch seam and assert byte-identical rendered text —
  stronger than the live backend's already-uniform 404s.
- **T2.5 submitted tone:** "Submitted for review" eyebrow + clock glyph labelled "Pending"
  (`portal-states.tsx:159-162`); no "saved" anywhere in the shell (`:155-171`).
- **T2.6 input preservation:** form state is parent-owned (`claim-page.tsx:51`, set on every
  submit `:88`), soft-error re-renders the form with preserved values + alert (`:128-136`).
- **T4 anchors:** tri-state `RELATIONSHIPS` exactly `AUTHORIZED | CARRIES |
  AFTERMARKET_COMPATIBLE` (`profile-form.tsx:38-54`), reaching the payload via
  `formToRevision` (`claim-page.tsx:214-220`); ship-area both shapes at
  `profile-form.tsx:312-369`; aftermarket disclosure conditional render `:374-379`; fresh-claim
  defaults (`profileToForm` null→`NATIONWIDE_US`, `claim-page.tsx:210`; empty-brands copy
  `profile-form.tsx:174-177`; empty-classes copy `:285-288`).
- **T6 anchors:** five required fields (`quote-form.tsx:84-193`), validation list shown only
  after a touched submit (`:67-74`, `:244-248`); payload via `formToSubmission`
  (`quote-page.tsx:294-305`); the PN-differs note flags but never blocks (`quote-form.tsx:180-187`,
  and PN is absent from the errors list `:67-74`).
- **T3.1/T3.2 ground truth (grep-verified):** zero `console.*`, `localStorage`,
  `sessionStorage`, or `document.cookie` references in `src/app/portal` and `src/app/quote` —
  the only hits are the security-note comments themselves (`portal/[token]/page.tsx:14-16`,
  `quote/[token]/page.tsx:12`). Both security tests are expected to pass clean.

---

## FINDINGS

*(Gate stage. Each will be confirmed by a named test — passing, or `.skip` + recorded here per
the prime directive — as the build tasks land.)*

| # | Task | Status at gate | Detail |
|---|---|---|---|
| F1 | T8 | **Confirmed against source** | Hard-coded brand string literal `alt="Gofer"` at `src/components/proc/gofer-mark.tsx:29`, inside the T8 scan scope, outside `src/lib/brand.ts`. T8's scan test will fail as specified → `.skip` + this finding; source untouched. |
| F2 | T6.3 | **Confirmed against source** | Quote entry path C (concierge) has no frontend surface — no `/api/admin/quotes` caller in `frontend/src`, no admin quotes tab. T6.3 is buildable for paths A and B only. |
| F3 | I2 / T2 scope | **Confirmed against environment** | Installed React 18.3.1 does not export `use` (used by both `page.tsx` route files); the app runs only on Next's vendored React 19. Route files get structural, not render, coverage; the real fix (React 19 dependency bump) is out of arc scope. |
| F4 | T7.1 | **Predicted** (confirmed when T7 written) | The show-once claim link is not cleared by a second fetch on the same tab (`admin/page.tsx:167-200` vs `:208-210`) — the raw token remains displayed after Refresh. Expected: T7.1's second-fetch assertion fails → `.skip` + this finding. |
| F5 | T8 (backend mirror) | **Brief staleness — no source defect** | `_ZERO_STATE_FRAMING` contains `"Gofer"`, not `"Arkim"` (`utils/supplier_portal.py:60-63`) — in sync with `BRAND_NAME`. The brief's "still contains Arkim" expectation is outdated. |

---

## BUILD LOG

*(Appended per task as T1–T8 land. Final `npm test` count and any test-stage findings will be
recorded here at the end of the arc.)*

- **Gate (this commit):** I1–I6 complete. No tests written, no source touched.

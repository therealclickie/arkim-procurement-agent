# SUPPLIER_SESSION_REPORT.md

**Arc 3 — Supplier Portal Session Conversion.** Branch `arc3/supplier-session`, cut at
`10a048e` (arc 2 merged: `95762b2`).

**This document currently contains the INVESTIGATION GATE ONLY (G1–G8).** No build task
(T1–T11) has been started. Per the brief's gate instruction ("STOP after the gate. Commit the
report. Do not begin T1 until it is written.") the builder stops here.

Every claim below carries a `file:line` or a reproduced command. Three claims were settled by
running a throwaway probe rather than by reading — those are marked **[PROBED]** and the probe
was deleted afterwards (no probe file is committed).

---

## Baselines measured at the gate (not inherited from the brief)

| Suite | Command | Result |
|---|---|---|
| Backend | `uv run pytest -q` | **2425 passed, 73 skipped**, exit 0, 143s |
| Frontend | `cd frontend ; npm test` | **64 passed** in **12 files**, exit 0, 8.4s |
| Frontend types | `cd frontend ; npm run type-check` | **5 pre-existing errors** — see Finding F4 |

Both suite counts match the brief's stated figures exactly (2425 / 64), so the arc's success
criteria 1 and 2 are anchored on a verified baseline.

Pre-existing frontend test files (the 12 that must not be modified):

```
frontend/src/app/admin/__tests__/admin-portal-controls.test.tsx
frontend/src/app/portal/[token]/__tests__/claim-page.test.tsx
frontend/src/app/portal/[token]/__tests__/open-requests.test.tsx
frontend/src/app/portal/[token]/__tests__/portal-route.test.ts
frontend/src/app/portal/[token]/__tests__/profile-form.test.tsx
frontend/src/app/portal/[token]/__tests__/security.test.tsx      <-- arc 1 security net
frontend/src/app/quote/[token]/__tests__/quote-page.test.tsx
frontend/src/app/quote/[token]/__tests__/quote-route.test.ts
frontend/src/app/quote/[token]/__tests__/security.test.tsx       <-- arc 1 security net
frontend/src/components/__tests__/brand-discipline.test.ts
frontend/src/components/proc/__tests__/options-compose.test.ts
frontend/src/components/ui/__tests__/gofer-loader.test.tsx
```

Arc 2's backend regression net for the bearer path (untouchable):
`utils/procurement_agent/tests/test_supplier_accounts_auth.py` (22 tests),
`test_supplier_accounts_requests.py` (10), `test_supplier_accounts_rbac.py` (22),
`test_supplier_accounts_store.py` (27), `test_supplier_accounts_bridge.py` (8),
`test_supplier_accounts_admin.py` (7), `test_supplier_accounts_flag.py` (4).

---

## G1 — React version blocker: **NOT a blocker. No React 19 bump is required. The arc proceeds.**

### The constraint, restated precisely

Installed React is **18.3.1** (`frontend/node_modules/react/package.json:6`,
`frontend/node_modules/react-dom/package.json:3`), declared as `"react": "^18.3.1"` at
`frontend/package.json:29-30`. Installed Next is **15.5.18**
(`frontend/node_modules/next/package.json:3`), which substitutes its own vendored React 19 at
build time.

React 18.3.1 genuinely has **no `use` export** — verified, not assumed:

```
$ node -e "const R=require('react'); console.log(R.version, typeof R.use)"
18.3.1 undefined
```

That is exactly why arc 1's two route-wrapper tests are source-scans rather than renders
(`frontend/src/app/portal/[token]/__tests__/portal-route.test.ts:5-11,33-38`,
`frontend/src/app/quote/[token]/__tests__/quote-route.test.ts:6-9,31-34`). The wrappers that
cannot render are `frontend/src/app/portal/[token]/page.tsx:43` and
`frontend/src/app/quote/[token]/page.tsx:33` — both call `use(params)` because Next 15 types a
**dynamic segment's** `params` as a Promise.

### Why arc 3's routes are not affected

The session routes this arc adds — `/supplier/login`, `/supplier/verify`, `/supplier/requests`,
`/supplier/profile`, `/supplier/members` — have **no dynamic segment**. With no `[param]` there
is no `params` Promise, therefore no `use()`, therefore nothing that React 18.3.1 lacks.

The one route that looked like it might need `use()` is `/supplier/verify`, which takes its
token from a query string. In Next 15 a *server* component's `searchParams` is a Promise (would
need `use()`), but the client-component hook `useSearchParams()` is not — and
`useSearchParams()` is already used in this codebase at
`frontend/src/components/proc/request-screen.tsx:15`. The magic-link URL the backend already
mints confirms this is the intended shape: `utils/supplier_accounts.py:955` returns
`{base}/supplier/verify?token={raw_token}`.

**[PROBED]** A throwaway test file was created, run, and deleted. It rendered a param-free
client page that consumes `useSearchParams()` and `useRouter()` from a `vi.mock("next/navigation")`
double, under the installed React:

```
Test Files  1 passed (1)
     Tests  2 passed (2)
```

The probe asserted `React.version === "18.3.1"` and `React.use === undefined` *in the same run*
as the successful render — so the render is demonstrably happening on the un-bumped React, not
on some substituted copy.

### Conclusion and the rule the build must follow

Every arc-3 route page is a `"use client"` component with no `params`/`searchParams` promise, so
all of them are **fully render-testable** under React 18.3.1 — a strictly better position than
arc 1's, which had to fall back to source-scanning. Arc 1's F3 constraint is confined to the two
existing `[token]` wrappers and is inherited unchanged, not extended.

**Build rule R-G1:** no arc-3 file may import `use` from `react`. If a future requirement forces
a dynamic segment under `/supplier/*`, that is the moment to stop and escalate the bump — not
before.

React 19 remains out of scope (brief, OUT OF SCOPE) and nothing in this arc creates pressure for it.

---

## G2 — Arc 2's session surface: the contract, verbatim

All ten routes, enumerated from source (`grep '@app\.(get|post)\("/api/supplier' api_server.py`):

| # | Route | Auth | Line |
|---|---|---|---|
| 1 | `POST /api/supplier/auth/request-link` | none (public) | `api_server.py:6599` |
| 2 | `POST /api/supplier/auth/verify` | none (public) | `api_server.py:6724` |
| 3 | `GET  /api/supplier/me` | session | `api_server.py:6790` |
| 4 | `POST /api/supplier/auth/logout` | session | `api_server.py:6812` |
| 5 | `GET  /api/supplier/requests` | session | `api_server.py:6826` |
| 6 | `POST /api/supplier/quotes` | session | `api_server.py:6838` |
| 7 | `GET  /api/supplier/members` | session + `VIEW_MEMBERS` | `api_server.py:7020` |
| 8 | `POST /api/supplier/members/invite` | session + `MANAGE_MEMBERS` | `api_server.py:7037` |
| 9 | `POST /api/supplier/members/{member_id}/role` | session + `CHANGE_ROLES` | `api_server.py:7063` |
| 10 | `POST /api/supplier/members/{member_id}/revoke` | session + `MANAGE_MEMBERS` | `api_server.py:7085` |

Plus the claim→account bridge: `POST /api/portal/{token}/request-account` (`api_server.py:7118`).

Every one of them is gated on `SUPPLIER_ACCOUNTS_V1` — flag-off raises the byte-identical
`404 {"detail":"Not Found"}` (`api_server.py:91-100`); for the session routes the gate lives
inside the dependency itself (`api_server.py:6780-6781`), so the whole authenticated surface
vanishes with one flag.

### Request / response shapes

**`POST /api/supplier/auth/request-link`** — body `{"email": str}` (`api_server.py:6595-6596`).
Response is **always** `200 {"ok": true}` — for unparseable email (`:6631`), no account (`:6644`),
store error (`:6651`), non-ACTIVE member (`:6665`), mint failure (`:6672`) and successful send
(`:6681`). Five distinct internal outcomes, one byte-identical body. **D5 is already true on the
backend; the UI's only job is not to undo it.** Rate-limited *before* any lookup (`:6626`,
limiter at `:6558-6592`) so throttling cannot be an oracle either; over-cap is
`429 {"detail":"Too many requests."}` with `Retry-After` (`:6588-6592`).

**`POST /api/supplier/auth/verify`** — body `{"token": str}` (`api_server.py:6685-6686`).
Success: `200 {"token": <raw session token>, "expires_at": <ISO8601>}` (`:6757-6759`) — raw once,
only the hash is stored (`utils/supplier_accounts.py:710-741`). Session TTL is 24h
(`utils/supplier_accounts.py:191`). **Every** failure — unknown / expired / already-used /
PENDING member / store error — is the single `401 {"detail":"Invalid or expired link"}`
(`api_server.py:6689-6695`, raised at `:6743` and `:6748`). Rate-limited per (IP, token-prefix),
cap 20 (`:6698-6721`).

**`GET /api/supplier/me`** — `200 {"account": {id, supplier_domain, status, created_at},
"member": {id, email, role, status}}` (`api_server.py:6796-6809`). **There is no `permissions`
key** — see Finding F1.

**`POST /api/supplier/auth/logout`** — `200 {"ok": true}` (`api_server.py:6822`); revokes the
session server-side (`:6816` → `utils/supplier_accounts.py:776-792`). A second logout with the
same token is the uniform session 401 (`api_server.py:6814-6815`).

**`GET /api/supplier/requests`** — `200 {"requests": [...]}` (`api_server.py:6834`), the *same*
`_supplier_open_requests(domain)` service the claim-token route uses, scoped to
`session["account"]["supplier_domain"]` (`:6833`). The row shape is therefore identical to
`OpenRequest` in `frontend/src/lib/portal-api.ts:146-157` — the frontend needs **no new row type**.

**`POST /api/supplier/quotes`** — body is the existing `PortalQuoteBody`
(`api_server.py:6839`; TS mirror at `frontend/src/lib/portal-api.ts:200-210`, i.e. `run_id`,
`quote_number`, `unit_price`, `quantity`, `lead_time` + optional `part_number`, `freight`,
`valid_until`, `notes`). Double-gated on `SUPPLIER_ACCOUNTS_V1` **and** `QUOTE_SUBMIT_V1`
(`:6849-6852`). Provenance is `submitted_via="account"`, `submitted_by=<member id>`
(`:6868-6869`). `404 {"detail":"No open request for this supplier"}` when the run has no open
RFQ to the account's domain (`:6861-6863`). Response is the existing
`PortalQuoteSubmitResponse` shape.

**`GET /api/supplier/members`** — `200 {"members": [{id, email, registrable_domain, role,
status, created_at}]}` (`api_server.py:7007-7017`, `:7027-7029`). Own account only.
**`invite`** — body `{email, role?}`, role defaults to `MEMBER` (`:7032-7034`); returns
`{"ok": true, "member": {...}}` (`:7054-7056`). **`role`** — body `{role}` (`:7059-7060`).
**`revoke`** — no body. All three map policy violations through `_supplier_rbac_error`
(`:6990-7004`): `403` forbidden/owner-transfer/cannot-demote-owner/cannot-revoke-owner,
`422` invalid-role/invalid-email, `409` already-member, `404` member-not-found, `500` store-error.

**`POST /api/portal/{token}/request-account`** — body `{"email": str}` (`api_server.py:7114-7115`).
Always `200 {"ok": true}` (`:7138, :7146, :7160, :7176`). The claim token is **validated, never
consumed** (`:7128` — `_validate_portal_token`, which does not burn the token), so D4's "the claim
token is NOT consumed by this" is already guaranteed by arc 2.

**This arc builds to the above. It does not redesign any of it.**

---

## G3 — Cookie insertion point (D1): the minimal additive change

### The two touch points

1. **Issuance.** `api_server.py:6757-6759` is the single `return JSONResponse(...)` in
   `supplier_verify_link`. The minimal change is to bind that response to a name and call
   `.set_cookie("gofer_supplier_session", sess["token"], httponly=True, secure=True,
   samesite="lax", path="/", max_age=<derived from sess["expires_at"]>)` before returning it.
   The JSON body is **unchanged** — the raw bearer token keeps being returned for API clients
   (D1: "For an API client that is correct and stays"), so arc 2's
   `test_supplier_accounts_requests.py:115` (`return r.json()["token"]`) keeps working verbatim.

2. **Acceptance.** `api_server.py:6775-6787` is `_require_supplier_session`, whose entire auth
   input today is `authorization: Optional[str] = Header(default=None)`. The additive change is
   a second parameter — `gofer_supplier_session: Optional[str] = Cookie(default=None)` (FastAPI's
   `Cookie` is not yet imported; `Header` is, at `api_server.py:150`) — and a **bearer-first**
   resolution order:
   - bearer present and well-formed → validate it, mark the request `auth_mode="bearer"`;
   - else cookie present → validate it, mark `auth_mode="cookie"`;
   - else → the existing `_supplier_session_reject_401()` (`api_server.py:6769-6772`).

   Bearer-first matters for T2: the CSRF check keys off `auth_mode`, and bearer must stay exempt
   even if a cookie happens to be in the jar as well.

3. **Clearing.** `supplier_logout` at `api_server.py:6812-6823` already revokes server-side
   (`:6816`); it additionally needs `.delete_cookie("gofer_supplier_session", path="/")` on its
   response. Note the delete must use the same `Path=/` or the browser will keep the cookie.

Nothing else in the file changes. `_portal_response_headers` (`api_server.py:5569-5575`) already
sets `Cache-Control: no-store` on both responses, which is the right posture for a `Set-Cookie`.

### **[PROBED]** Why arc 2's bearer tests are structurally immune — and how to test the cookie at all

This is the single most important mechanical fact for T1/T2, and it is not obvious. A throwaway
FastAPI app that sets exactly the D1 cookie was run under `TestClient` at both base URLs:

```
http://testserver  | Set-Cookie: gofer_supplier_session=rawtok; HttpOnly; Max-Age=3600; Path=/; SameSite=lax; Secure
http://testserver  | jar: {'gofer_supplier_session': 'rawtok'}
http://testserver  | read: {'cookie': None,     'origin': None}
https://testserver | Set-Cookie: gofer_supplier_session=rawtok; HttpOnly; Max-Age=3600; Path=/; SameSite=lax; Secure
https://testserver | jar: {'gofer_supplier_session': 'rawtok'}
https://testserver | read: {'cookie': 'rawtok', 'origin': None}
```

Three consequences:

- **Arc 2's suite cannot be perturbed by T1.** Its clients are plain `TestClient(api_server.app)`
  (`test_supplier_accounts_requests.py:94`), i.e. base URL `http://testserver`. The cookie lands
  in the jar but the RFC-6265 `Secure` rule means it is **never sent back** over http. Arc 2's
  `_login()` helper (`:100-117`) calls verify on that same client and every subsequent request
  stays bearer-only. The bearer path is untouched *by construction*, not merely by intent.
- **New cookie tests must use `TestClient(api_server.app, base_url="https://testserver")`.**
  Without that, a "cookie authenticates `me`" test would fail for the wrong reason.
- **R3 is satisfiable directly.** The `Set-Cookie` header string is readable on the response at
  either base URL, so all four attributes can be asserted on the real header rather than on a
  report claim.

### Browser-side notes the human verification step will hit

- `Secure` over `http://localhost:3000` is accepted — browsers treat localhost as a trustworthy
  origin — so dev is not broken by the attribute.
- The browser talks to `localhost:3000`, not `:8001`; the cookie is set through Next's rewrite
  proxy (`frontend/next.config.ts:5-14`, `/api/:path*` → `NEXT_PUBLIC_API_URL`, which is
  `http://localhost:8001` per `frontend/.env.local:1`). Cookies ignore port, so a host-only
  `localhost` cookie is correct for both. Same-origin requests already send cookies by fetch's
  default `credentials: "same-origin"`; `credentials: "include"` is belt-and-braces and harmless.
- `SUPPLIER_PORTAL_BASE_URL` (`utils/supplier_accounts.py:947-948`, default
  `https://procurement.arkim.ai`) must point at the same origin serving `/supplier/verify`, or
  the emailed link lands on a host that has no cookie jar for the API.

### D2 (CSRF) — where the "configured app origin" already lives

Do **not** invent a second origin list. `api_server.py:195-219` already builds `_cors_origins`
(`http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:8000`, plus
`CORS_ALLOW_ORIGINS`, plus the demo origin) and runs `CORSMiddleware` with
`allow_credentials=True` (`:216`). T2 should validate `Origin`/`Referer` against **that same
list**, so there is one configuration surface.

One design decision T2 must make explicitly, because the probe shows `TestClient` sends **no**
`Origin` header: on a *cookie-authenticated, state-changing* request with **neither** `Origin`
nor `Referer`, the choice is reject (strict, closes the hole, breaks non-browser cookie clients)
or allow (leaves the hole). **Recommendation: reject** — a cookie is by definition an ambient
browser credential, and every browser sends `Origin` on a cross-site POST and on a same-origin
POST. A non-browser client has the bearer path, which is exempt. This is a decision the reviewer
should see stated rather than discover.

---

## G4 — Frontend fetch seam, and where the auth-mode switch belongs

There are two sibling clients, deliberately separate from the internal app client
(`frontend/src/lib/api.ts`):

- `frontend/src/lib/portal-api.ts` — one primitive, `portalFetch<T>` (`:89-107`), which prefixes
  `/api`, sets `cache: "no-store"` and `Content-Type`, sends **no** auth of any kind (`:94-97`),
  and collapses every non-200 into `{ok:false, rejected:true}` (`:100, :103-105`). Six exported
  operations build on it (`:114, :130, :183, :192, :213`).
- `frontend/src/lib/quote-api.ts` — the same primitive shape (`quoteFetch`, `:79-101`) with one
  extra outcome: `409 → {ok:false, closed:true}` (`:93`), an honest state rather than an error.

Both funnel to **global `fetch`**, which is precisely why arc 1's harness stubs `globalThis.fetch`
rather than mocking the modules (`frontend/src/test-support/fetch-seam.ts:1-8, 32-43`) — URLs and
headers stay observable.

### The switch is a *path* switch, not a header switch

This is the finding that shapes T3. Session mode is not "the same URL plus a credential":

| Operation | Token mode | Session mode |
|---|---|---|
| open requests | `GET /api/portal/{token}/open-requests` | `GET /api/supplier/requests` |
| submit quote | `POST /api/portal/{token}/quotes` | `POST /api/supplier/quotes` |
| quote history | `GET /api/portal/{token}/quotes` | **no endpoint** (F2) |
| profile read | `GET /api/portal/{token}/profile` | **no endpoint** (F2) |
| propose revision | `POST /api/portal/{token}/propose-revision` | **no endpoint** (F2) |

So the seam cannot be a boolean passed down to `portalFetch`. The correct shape is a small
**auth-mode object** that resolves a *logical operation* to a concrete `(url, init)` — e.g.
`tokenMode(token)` and `sessionMode()` both satisfying one interface with methods
`openRequests()`, `submitQuote(body)`, … — with `portalFetch` / `quoteFetch` left **byte-identical**
so arc 1's request-parity assertions hold trivially. Components then take the mode object (or fall
back to token mode), never a flag.

**Hard constraint on that refactor (see G5): `portalFetch`'s current header block must not gain a
`credentials` key unconditionally.** Arc 1 asserts no `Authorization` header on every portal call
(`portal/__tests__/security.test.tsx:117-130`), and the parity requirement in T3 is "token mode
produces identical requests to today". Session-only options belong in the session mode object.

---

## G5 — `open-requests.tsx` today, and the two things that must not change

`frontend/src/app/portal/[token]/open-requests.tsx`:

- **Props:** `{ token: string }` — required, positional in the type (`:37`).
- **Data source:** on mount, `Promise.all([getOpenRequests(token), getQuoteHistory(token)])`
  (`:46-54`), both via `portalFetch`.
- **States:** rejection of either call stores `null` (`:52-53`); `if (!requests && !history)
  return null` (`:60`); each section renders only when its array is non-empty (`:105`, `:176`).
- It also owns inline quoting: `startQuoting` (`:62-76`) and `handleSubmit` (`:78-101`) calling
  `submitPortalQuote` (`:82`), reusing `QuoteForm` from the quote surface (`:25`).

### The two invariants arc 1 pins, which T6 must respect

1. **The prop signature is load-bearing.** `open-requests.test.tsx:41` renders
   `<OpenRequests token={TOKEN} />` with no provider and no other prop. If T3/T6 changes the
   signature to a required auth-mode prop, that unmodifiable test breaks. Session mode must
   therefore be **additive and defaulted**: `token` stays accepted and, absent any session
   context/prop, behaviour is byte-identical. A React context with a token-mode default satisfies
   this cleanly (the test renders outside any provider, so it gets the default).

2. **The empty state renders literally nothing — and must keep doing so.**
   `open-requests.test.tsx:138-148` asserts, on `200 {"requests":[]}` + `200 {"quotes":[]}`, that
   `container.textContent === ""` and `container.querySelectorAll("section").length === 0`. The
   flag-off case asserts the same (`:52-65`).
   **Consequence for T6:** a session inbox at `/supplier/requests` that showed a blank page for a
   supplier with no open RFQs would be a poor surface — but the honest empty-state copy
   ("no open requests right now") **must live in the `/supplier/requests` page wrapper, not
   inside `OpenRequests`**. Putting it in the component is the single most likely way to break
   arc 1's net. This is the brief's "same honest empty state (no fabricated rows)" read against
   what the test actually asserts.

Note the row type needs no change: `GET /api/supplier/requests` returns the same
`{"requests": [...]}` shape from the same service (`api_server.py:6833-6835`).

### Sibling component signatures T7/T8 will meet

`ProfileForm` takes `{initial, aftermarketDisclosure, onChange, onSubmit, submitting}`
(`frontend/src/app/portal/[token]/profile-form.tsx:68-80`) — already auth-agnostic; the token
never reaches it. `QuoteForm` takes `{initial, requestedPartNumber, onChange, onSubmit,
submitting, revising?}` (`frontend/src/app/quote/[token]/quote-form.tsx:37-52`) — likewise. Both
are reusable in session mode **without modification**; only their callers change. `QuotePage`
(`quote-page.tsx:41`) and `ClaimPage` (`claim-page.tsx:42`) are the token-bound callers.

### T9's insertion point, and the trap in it

The claim page's success state is `PortalSubmitted` (`portal-states.tsx:155-171`), rendered from
`claim-page.tsx:118-120`. That is where D4's "create your account" CTA goes. The trap:
`claim-page.test.tsx:143-153` asserts on that state that `document.body.textContent` does **not**
match `/saved/i`, and that `getByRole("img", {name: "Pending"})` is present. The CTA copy must
therefore avoid the word "saved" entirely — which it should anyway, since D4 demands honest copy
("a link is being emailed", never "you are logged in").

---

## G6 — Frontend flag convention: **there isn't one yet. This arc establishes it.**

`grep -rn "NEXT_PUBLIC" frontend/src frontend/next.config.ts` returns only
`NEXT_PUBLIC_API_URL` — config, not a feature flag (`frontend/src/lib/api.ts:15-16`,
`frontend/src/app/admin/page.tsx:27-28`, `frontend/next.config.ts:7`). `frontend/.env.local`
contains exactly one line: `NEXT_PUBLIC_API_URL=http://localhost:8001`.

Today the frontend gates flagged features **implicitly, via the backend**: when
`QUOTE_SUBMIT_V1` is off the endpoints 404 and `OpenRequests` renders nothing — stated as a
deliberate choice at `open-requests.tsx:11-14` ("no empty shells, no flag plumbing in the UI").
That pattern is excellent for a *section*, but it cannot gate a *route*: `/supplier/login` must
not render a login form at all when the arc is off, and it has no backend call to 404 on before
first paint.

So `NEXT_PUBLIC_SUPPLIER_SESSION_V1` is a **new convention**, not an existing one, and the report
should say so plainly rather than imply it was already the pattern.

**[PROBED]** Toggling it under vitest works, with one gotcha:

```
G6 probe — NEXT_PUBLIC flag toggling under vitest
  ✓ is off by default (env unset)
  ✓ can be stubbed on            (vi.stubEnv("NEXT_PUBLIC_SUPPLIER_SESSION_V1", "1"))
  ✓ is off again after unstubAllEnvs
Tests  3 passed (3)
```

**Gotcha:** `frontend/vitest.setup.ts:12-16` calls `vi.unstubAllGlobals()` and
`vi.restoreAllMocks()` but **not** `vi.unstubAllEnvs()`, and vitest's `unstubEnvs` option is off
by default. `vitest.setup.ts` is a pre-existing shared file and will not be modified, so **every
new test that stubs the flag must call `vi.unstubAllEnvs()` in its own `afterEach`** or it will
leak the flag into later tests in the same file. Recording this as a build rule (R-G6) because a
leaked flag is exactly the failure mode CLAUDE.md's conftest note describes on the backend side.

Second gotcha: Next's SWC inlines `process.env.NEXT_PUBLIC_*` only when written as a **literal
member expression**. The flag helper must read
`process.env.NEXT_PUBLIC_SUPPLIER_SESSION_V1` directly, never via a computed key, or the
production build will not substitute it.

---

## G7 — Test-support reuse: extend, and what has to be added

`frontend/src/test-support/fetch-seam.ts` needs **nothing new for the happy path**: `stubFetch`
(`:32-43`) records `{url, init}` for every call and `jsonResponse` / `notFound` (`:18-26`) cover
200s and the uniform 404. Two additions are likely and are purely additive:

- an `unauthorized()` helper mirroring `notFound()` (`:26`) for arc 2's
  `401 {"detail":"Invalid or expired session"}` (`api_server.py:6772`), which the T3/T6 "a 401
  clears session state / sends the user to login" tests need;
- a storage/console sweep helper **only if** it is genuinely shared — see F3, because arc 1's
  versions are trapped in an unmodifiable file.

`frontend/src/test-support/fixtures.ts` needs new factories for shapes that do not exist yet:
`supplierAccount()`, `supplierMember()`, and a `supplierMe()` composing them into the
`GET /api/supplier/me` body (`api_server.py:6796-6809`), plus `accountMember()` for the
`GET /api/supplier/members` row (`api_server.py:7007-7017`). **`openRequest()` (`fixtures.ts:41-51`)
is reused as-is** — the session endpoint returns the same row shape.

**Interpretation note for the reviewer:** `src/test-support/*.ts` are not test files (vitest
collects `src/**/*.test.{ts,tsx}` only — `frontend/vitest.config.ts:19`) and guardrail 5
explicitly directs reuse of them. Additions there will be **append-only**: no existing factory's
default or signature will be altered, so the 64 existing tests cannot be affected. If the
reviewer reads the prime directive as covering these two files, say so and the additions move to
a new `test-support/session-fixtures.ts` instead.

---

## G8 — Route-group / layout structure

`frontend/src/app` has no route groups at all today: `admin`, `approvals`, `design`, `history`,
`impact`, `parts`, `portal`, `quote`, `request`, `runs`, `settings`. Seven of them carry their own
`layout.tsx`; **`portal/` and `quote/` deliberately do not** — the two public surfaces inherit the
root layout (`frontend/src/app/layout.tsx:44-55`) and build their own chrome inside the page
(`PortalLayout`, used at `claim-page.tsx:126`).

**There is no `src/app/supplier/` directory.** The backend has already chosen the path for us:
`utils/supplier_accounts.py:955` emails `{base}/supplier/verify?token=...`. So:

```
frontend/src/app/supplier/
  layout.tsx          # T10's minimal authenticated shell (account name, member email, logout)
  login/page.tsx      # T4
  verify/page.tsx     # T5  (useSearchParams, NOT use() — G1)
  requests/page.tsx   # T6  (owns the empty-state copy — G5)
  profile/page.tsx    # T7
  members/page.tsx    # T11
```

A sibling tree, disjoint from `portal/[token]` and `quote/[token]` — no existing file moves, and
the `portal/`↔`supplier/` component sharing happens by import (as `open-requests.tsx:25` already
imports across from `quote/[token]/quote-form`), not by relocation.

Two cautions:

1. `supplier/layout.tsx` must not be where the auth *gate* lives. T10's "protected routes redirect
   to login without a session" should be in the pages (or a shared client guard component) —
   `/supplier/login` and `/supplier/verify` are siblings under the same layout and must stay
   reachable **without** a session. Alternatively split: `login`/`verify` outside the shell,
   `requests`/`profile`/`members` inside it.
2. The root layout wraps everything in `<Providers>` (`layout.tsx:51` →
   `providers.tsx:8-19`, QueryClientProvider + ToastStack). The session routes inherit it exactly
   as `portal/[token]` does; nothing there fetches on its own, so it is inert for these surfaces.

Flag-off inertness (guardrail 1): each new `page.tsx` reads
`process.env.NEXT_PUBLIC_SUPPLIER_SESSION_V1` and renders the not-found / nothing path when off,
so "no new route renders" is a property of the pages, provable by a render test at both flag
values — rather than a property of the router, which cannot be asserted in vitest.

---

## FINDINGS

Four findings. F1–F3 are scope facts the brief's task list does not account for and that change
what T3/T6/T7/T11 can honestly deliver; F4 is a pre-existing defect recorded so it is not later
attributed to this arc. **None of them blocks the gate** — G1, the designated stop condition,
is clear.

### F1 — `GET /api/supplier/me` returns no `permissions`; the capability matrix is server-only

T3's spec is a `useSupplierSession` hook exposing `{account, member, permissions, loading}`, and
D6 says controls are hidden "when `has_permission` says no". But `me` returns only
`account` + `member` (`api_server.py:6796-6809`) — no capability list. The matrix lives in
`utils/supplier_accounts_rbac.py:74-83` and is never serialised to any client.

The frontend therefore has two options, and only one is acceptable:

- **Reimplement the matrix in TypeScript from `member.role`** — duplicates policy across the
  language boundary, and the next row added to `CAPABILITY_MATRIX` silently desynchronises the UI.
  This is precisely what the rbac module's own docstring forbids (`supplier_accounts_rbac.py:9`,
  "ONE row in `CAPABILITY_MATRIX`, not a hunt through routes").
- **Add a `permissions: [...]` array to the `me` response**, computed from the matrix. Additive
  (no existing key changes), ~4 lines, and keeps the matrix single-source.

The second is correct, but it is a **third** backend change beyond D1's "cookie issuance +
dependency". Recording it now so the reviewer does not read it as scope creep at T3/T11 time.
Arc 2's `me` characterisation tests assert their own keys and an added key should not break them —
to be verified when the change is made, not assumed.

### F2 — T7 (session profile) has **no** backend endpoint; T6's quote history has none either

Enumerating `/api/supplier/*` gives ten routes (G2) and none of them reads or writes a profile.
There is no `GET /api/supplier/profile` and no `POST /api/supplier/propose-revision`. The
corroborating signal: `PROPOSE_REVISIONS` is declared in the matrix
(`utils/supplier_accounts_rbac.py:60`) and `grep` finds **zero** references to it in
`api_server.py` — the capability was declared for a route arc 2 never built. The same is true of
`VIEW_REQUESTS` (`:58`) and `SUBMIT_QUOTES` (`:59`): declared, enforced nowhere. Only
`VIEW_MEMBERS`, `MANAGE_MEMBERS` and `CHANGE_ROLES` are actually wired
(`api_server.py:7022, 7041, 7067, 7089`).

Likewise T6: `GET /api/portal/{token}/quotes` (history) has **no** session counterpart —
`/api/supplier/quotes` is `POST` only (`api_server.py:6838`). `OpenRequests` renders a history
section (`open-requests.tsx:176-196`) that would have no data source under a session.

So, stated honestly before building rather than discovered at review:

- **T7 cannot be "ProfileForm in session mode" alone.** It requires two new backend endpoints
  (profile read + propose-revision, the latter gated on `PROPOSE_REVISIONS`), or it must be
  deferred. That is a materially larger backend change than D1 anticipated.
- **T6 in session mode renders the open-requests section only**, unless a
  `GET /api/supplier/quotes` history route is added.

Recommended sequencing, for the human to rule on: build T1–T6 and T8–T10 as specified; treat the
T7 endpoints (and the F1 `permissions` key) as a small, explicitly-scoped backend addition inside
this arc, since without them T7 and T11 cannot be built to their own acceptance tests. If that is
refused, **T7 is deferred, not silently reduced.**

### F3 — Arc 1's two security helpers are trapped inside an unmodifiable file

`storageDump` (`portal/__tests__/security.test.tsx:36-43`), `spyConsole` (`:45-49`),
`consoleDump` (`:51-56`) and `headerValue` (`:58-65`) are exactly the sweep guardrail 4 requires
new security tests to mirror — and they are defined inside a file the prime directive forbids
touching. Importing across `__tests__` boundaries would also be fragile. The new session security
test will re-declare equivalents (or place them in `src/test-support/`). Noted so the duplication
reads as forced, not careless.

### F4 — `npm run type-check` has 5 pre-existing errors, all in arc-1 test files

```
portal/[token]/__tests__/portal-route.test.ts(17,20): TS2307 Cannot find module '../page.tsx?raw'
portal/[token]/__tests__/security.test.tsx(45,30): TS2503 Cannot find namespace 'vi'
portal/[token]/__tests__/security.test.tsx(51,35): TS2503 Cannot find namespace 'vi'
portal/[token]/__tests__/security.test.tsx(54,30): TS7006 Parameter 'a' implicitly has an 'any' type
quote/[token]/__tests__/quote-route.test.ts(15,20): TS2307 Cannot find module '../page.tsx?raw'
```

`tsc --noEmit` is **not** a success criterion for this arc and these are in files the prime
directive forbids editing (the `?raw` ones would need an ambient module declaration; the `vi`
namespace ones need a `vitest` type import). Recorded purely so the count is known to be 5 before
this arc and any increase is attributable. `npm test` is green regardless — vitest transpiles
rather than type-checks.

---

## Build rules carried out of the gate

| # | Rule | Source |
|---|---|---|
| R-G1 | No arc-3 file imports `use` from `react`. Session routes stay param-free client components. | G1 |
| R-G3 | Bearer-first resolution in the session dependency; new cookie tests use `base_url="https://testserver"`. | G3 |
| R-G3b | Cookie-authenticated state-changing requests with neither `Origin` nor `Referer` are **rejected**; bearer exempt. Origin list = the existing `_cors_origins`. | G3 |
| R-G4 | `portalFetch`/`quoteFetch` stay byte-identical; the auth mode is a path-resolving object above them, not a flag inside them. | G4 |
| R-G5 | `OpenRequests` keeps `token` working with no provider, and keeps rendering *literally nothing* when both lists are empty. Empty-state copy lives in the page. | G5 |
| R-G5b | T9's CTA copy must not contain the word "saved". | G5 |
| R-G6 | Every test that stubs the flag calls `vi.unstubAllEnvs()` in its own `afterEach`; the flag is read as a literal `process.env.NEXT_PUBLIC_SUPPLIER_SESSION_V1`. | G6 |
| R-G7 | `test-support` additions are append-only; no existing factory changes. | G7 |
| R-G8 | `/supplier/login` and `/supplier/verify` remain reachable without a session. | G8 |

---

## Status

**Gate complete. G1 is clear — no React bump prerequisite, and the arc's own routes are in a
better testing position than arc 1's.** Build has not started; no source file has been modified;
the two probe files created during the gate were deleted and are not committed.

Awaiting the go-ahead (and a ruling on F2's backend scope) before T1.

---
---

# BUILD REPORT — T1 through T11

Appended after the gate. All eleven build tasks are **complete**; T11 was
**not** deferred. Commits are one per task, named for it.

## Final test counts (run, not recalled)

| Suite | Command | Baseline | Final |
|---|---|---|---|
| Backend | `uv run pytest -q` | 2425 passed, 73 skipped | **2479 passed, 73 skipped** (2425 + 54) |
| Frontend | `cd frontend ; npm test` | 64 passed, 12 files | **172 passed, 21 files** (64 + 108) |

**Zero pre-existing test files were edited.** `git diff --name-only 4304ba3 HEAD`
lists two files under `frontend/src/test-support/` that existed before this arc
(`fetch-seam.ts`, `fixtures.ts`); both are **strictly append-only** —
`git diff --numstat` reports `50 0` and `74 0` respectively, i.e. not one line
removed or altered. They are not test files (vitest collects
`src/**/*.test.{ts,tsx}` only, `vitest.config.ts:19`) and guardrail 5 directs
reuse of them. Every other `__tests__` path in the diff is a new file. Arc 1's
two security files are untouched.

### Additional verification runs

| Run | Result |
|---|---|
| `SUPPLIER_ACCOUNTS_V1=0 QUOTE_SUBMIT_V1=0 SUPPLIER_PORTAL_V1=0 uv run pytest -q` | 2479 passed, 73 skipped |
| `npm test` with `NEXT_PUBLIC_SUPPLIER_SESSION_V1` unset (default off) | 172 passed |
| `npm test` with `NEXT_PUBLIC_SUPPLIER_SESSION_V1=1` in the ambient env | 172 passed |
| `npm run type-check` | **5 errors — exactly the 5 pre-existing ones (gate finding F4)**, all in arc-1 test files; this arc adds none |

## Scope note — the backend was larger than D1 anticipated, as the gate predicted

Gate findings F1 and F2 recorded that T7 and T11 had no backend to build on.
The build took the gate's recommended path and added the missing session
doors rather than silently reducing T7. That work is commit **T2b**, named so
it is visible rather than buried inside a frontend task:

- `member.permissions` on `GET /api/supplier/me`, computed from the capability
  matrix. Nested under `member`, not at the top level, specifically so arc 2's
  `set(body.keys()) == {"account","member"}` assertion still holds unedited.
- `GET /api/supplier/profile`, `POST /api/supplier/propose-revision`,
  `GET /api/supplier/quotes` (history).
- Each goes through the **same service** its `/api/portal/{token}` sibling uses
  — `_supplier_profile_body`, `_validate_revision_brands`,
  `_supplier_quote_history` were extracted for it, following arc 2's
  `_supplier_open_requests` pattern. Two tests call **both doors** and compare
  the bodies, so drift fails the suite rather than being caught by eye.
- It also wires `VIEW_REQUESTS` and `PROPOSE_REVISIONS`, which arc 2 declared
  in the matrix and gated on no route. Every role holds both today, so nothing
  is newly refused — but a future VIEWER row now lands correctly.

Token-door behaviour is unchanged: the extraction is a pure move, and arc 1's
portal tests pass unedited.

## Where each success criterion is evidenced

| # | Criterion | Evidence |
|---|---|---|
| 1 | Backend green, 2425 pass unedited, `2425 + N` | 2479 = 2425 + 54; run above |
| 2 | Frontend green, 64 pass unedited, `64 + M` | 172 = 64 + 108; run above |
| 3 | Flags off ⇒ suites pass, no new route reachable | explicit flags-off runs above; every new `page.tsx` has a render test at **both** flag values asserting `container.textContent === ""` and zero fetches |
| 4 | Cookie is HttpOnly + Secure + SameSite=Lax, asserted on the real `Set-Cookie` | `test_supplier_session_cookie.py::TestCookieIssuance::test_verify_sets_cookie_with_all_four_attributes` — reads `response.headers.raw`, not the jar (a jar read loses every attribute) |
| 5 | Session token never in storage / cookie-as-read-by-JS / console / URL, after a full cycle | `verify-screen.test.tsx::"leaves no trace after a successful sign-in"` (sweeps the magic-link token AND the session token from the response body); `session-chrome.test.tsx::"sweeps clean after load, interaction and sign-out"` — the sweep runs after **sign-out**, not merely after login |
| 6 | Four verify-failure modes render byte-identical output (equality) | `verify-screen.test.tsx` — Set-size-1 over the four named modes, again over divergent backend shapes (401/403/404/500/throw), again including the no-token case, **plus a contrast case** proving the equality fails on divergent output |
| 7 | No auth UI copy distinguishes known from unknown | `login-screen.test.tsx` — Set-size-1 over known / unknown / unparseable, with a contrast case; plus explicit assertions that the address is not echoed and that "spam" never appears. Same shape for the T9 bridge |
| 8 | `git diff --stat` shows no pre-existing test file modified | verified above; the only pre-existing files in the diff are the two append-only `test-support` helpers |
| 9 | Report contains G1–G8, both counts, FINDINGS, follow-ups | this document |

## Reviewer checklist, answered

- **R3** — The four attributes are read off `response.headers.raw`, filtered to
  the `gofer_supplier_session` cookie. New cookie tests run on
  `base_url="https://testserver"`, because `Secure` means an http client never
  returns the cookie. That same rule is *why* arc 2's bearer suite is
  structurally immune to T1: its clients are `http://testserver`, so the cookie
  lands in the jar and is never sent back.
- **R4** — The sweep runs after login → interact → **sign out**
  (`session-chrome.test.tsx`), and separately after a rejected verify.
- **R5** — Set-size-1, with a contrast case in the same file that renders a
  genuinely different outcome and asserts the set size is 2.
- **R7** — The CSRF tests drive the **real dependency through the real routes**
  (`test_supplier_session_csrf.py`); nothing is mocked. A foreign `Origin`, a
  foreign `Referer`, and the no-header case are each 401; near-miss origins
  (`...evil.com`, wrong scheme, wrong port, origin-in-a-query-string) are each
  rejected; a blocked attempt does **not** log the real user out.
- **R8** — `test_supplier_members_rbac_cookie.py` calls each hidden control's
  endpoint directly with the MEMBER's own **cookie** session and gets 403. One
  test guards against the 403 being the CSRF check in disguise by making the
  identical request as an ADMIN and getting 200. Another asserts `me`'s
  `permissions` list *predicts* the 403 — what it omits, the server refuses.
- **R9** — T11 was built, not deferred. FINDINGS below are genuine; F5 in
  particular is a mistake I made and corrected, reported rather than hidden.

## Decisions taken during the build that a reviewer should see stated

1. **Bearer-first resolution** in the session dependency. The CSRF exemption
   keys off the resolved mode, so the order is load-bearing: a request
   presenting both credentials is treated as bearer, which is safe precisely
   because an attacker who can cause a cookie to be sent cannot set a header.
2. **No `Origin` and no `Referer` on a cookie-authenticated state-changing
   request ⇒ reject** (R-G3b). A cookie is by definition ambient; a request
   presenting one with no provenance cannot be vouched for. Non-browser callers
   have the bearer path.
3. **The auth gate is in the pages, not `supplier/layout.tsx`.** `/supplier/login`
   and `/supplier/verify` are siblings under that layout and must stay reachable
   without a session. No `supplier/layout.tsx` was created.
4. **The session empty-state copy lives in `/supplier/requests`, not in
   `OpenRequests`.** The component still renders literally nothing when both
   lists are empty — pinned by pre-existing assertions on the claim surface.
   `OpenRequests` gained an optional `onEmpty` callback and an optional `token`
   prop; with no provider and a token it behaves exactly as before.
5. **Member management opts into carrying the HTTP status** on a rejection,
   alone among the surfaces. The caller is an authenticated admin acting inside
   their own company, so there is no enumeration concern, and "that person is
   already on your team" beats "something went wrong". Everywhere else the
   rejection stays uniform.

---

## FINDINGS

Five. F5 is mine.

### F5 — I converted six pre-existing files from LF to CRLF, and corrected it

The Python one-liners I used to patch existing files wrote with the platform
default newline, turning six LF files into CRLF: `api_server.py`,
`claim-page.tsx`, `open-requests.tsx`, `portal-states.tsx`, `fetch-seam.ts`,
`fixtures.ts`. Nothing behavioural — both suites were green throughout — but it
made `git diff` report `api_server.py` as 14,613 changed lines, which would
have made review impossible.

Corrected by normalising every file this arc touched back to LF in a final
commit. `git diff --stat 4304ba3 HEAD` now reports `api_server.py` as **351**
changed lines, and no changed file contains a CR.

**The wart that remains:** the per-task commits T1–T11 still carry the
line-ending noise internally, so reading a *single task's* diff in isolation is
still unpleasant. The cumulative diff against the branch point — which is what
the human verification step uses — is clean. I did not rewrite history to fix
the intermediate commits. If the reviewer wants clean per-task diffs, say so and
the branch can be rebuilt; I would rather be told than guess.

### F6 — Six flag-off tests were only true because the flag was unset ambiently

The route tests asserting "renders NOTHING when the flag is off" called
`vi.unstubAllEnvs()`, which falls back to the **ambient** environment. With
`NEXT_PUBLIC_SUPPLIER_SESSION_V1=1` exported, six of them failed — not because
anything was broken, but because the assertion was unsatisfiable. Caught by
running the suite with the flag explicitly on.

Fixed: they now `vi.stubEnv(FLAG, "0")` explicitly, so the assertion is true
regardless of the developer's environment. The suite is green with the flag
unset **and** with it set to 1 (both runs above). Worth flagging as a pattern —
a flag-off test that relies on ambient absence is not really testing the gate.

### F7 — `Referrer-Policy: no-referrer` and the CSRF check could interact in a real browser

`_portal_response_headers` sets `Referrer-Policy: no-referrer` on the supplier
API responses. That header governs the *API response*, not the page, so it does
not affect what the `/supplier/*` pages send — and browsers send `Origin` on all
non-GET requests regardless. So the CSRF check should be satisfied by `Origin`
alone in the real browser, and `Referer` is only the fallback.

I could not prove this in vitest (jsdom does not model `Origin` emission) and it
is the one part of T2 that is not covered by an executable assertion. **The
human verification step should confirm it:** with both flags on, sign in and
submit a profile revision from `localhost:3000` and confirm it returns 200
rather than 401. If a future page sets `referrer: no-referrer` in its metadata
export (as `portal/[token]/page.tsx` does), re-check — under that policy some
browsers send `Origin: null`, which this check rejects.

### F8 — `SUPPLIER_PORTAL_BASE_URL` must match the origin serving `/supplier/verify`

Carried forward from the gate and now load-bearing, because the verify landing
exists. `utils/supplier_accounts.py:947-948` defaults to
`https://procurement.arkim.ai`. If it does not point at the host actually
serving the route, the emailed link lands somewhere with no cookie jar for the
API and sign-in silently cannot work. Configuration, not code — but it will bite
the first live test if unset.

### F9 — The session profile inherits `SUPPLIER_PORTAL_V1`, which is an honest second gate but not an obvious one

`GET /api/supplier/profile` and `POST /api/supplier/propose-revision` go through
`utils/supplier_portal`, which returns `None` when `SUPPLIER_PORTAL_V1` is off.
So with `SUPPLIER_ACCOUNTS_V1` on but `SUPPLIER_PORTAL_V1` off, a signed-in
supplier sees "Your profile isn't ready yet" — accurate, but caused by a flag
rather than by missing data, and the UI cannot tell the difference. Pinned by a
test (`test_404_when_the_portal_module_is_dormant`) so the coupling is visible
rather than surprising. Not worth a dedicated error state at prototype stage;
worth knowing before someone debugs it as a data problem.

---

## FOLLOW-UPS (not done, deliberately out of scope)

1. **`utils/supplier_accounts_rbac.PROPOSE_REVISIONS` is now wired;
   `SUBMIT_QUOTES` is still not.** `POST /api/supplier/quotes` (arc 2) is
   plain-session, not capability-gated, and `GET /api/supplier/requests`
   likewise. I left arc 2's routes alone — changing their gating is a behaviour
   change to tested code and belongs in its own change, not smuggled into a
   frontend arc. Every role holds both capabilities today, so nothing is
   currently unprotected.
2. **Ownership transfer** remains unbuilt (arc 2 finding 1). The members UI
   reflects that honestly by offering no control for it.
3. **The in-process rate limiter** behind `request-link` / `verify` is
   single-process (arc 2's noted follow-up). A multi-worker deployment weakens
   the cap proportionally.
4. **React 19 / `use()`** stays out of scope and this arc created no pressure
   toward it — every arc-3 route is a param-free client component and is
   render-tested under the installed React 18.3.1 (G1, build rule R-G1 held).
5. **`npm run type-check`'s 5 pre-existing errors** are untouched; fixing them
   requires editing arc-1 test files, which the prime directive forbids.

**NO PUSH.** 13 commits on `arc3/supplier-session`, none pushed.

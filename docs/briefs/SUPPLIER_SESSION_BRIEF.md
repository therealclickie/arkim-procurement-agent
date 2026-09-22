# BUILD BRIEF — Supplier Portal Session Conversion

**Arc:** 3 of the supplier-account programme. **Type:** frontend + minimal backend, flag-gated.
**Branch:** cut from `test/flag-on-integration` after arc 2 has merged.
**Builder:** Claude Opus 5 (subscription). **Reviewer:** Claude Fable 5. **Merge:** human only.
**Feature flags:** backend `SUPPLIER_ACCOUNTS_V1` (existing, arc 2); frontend
`NEXT_PUBLIC_SUPPLIER_SESSION_V1` — both default OFF.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

Arc 2 built supplier identity end-to-end in the backend — accounts, members, RBAC, magic links,
sessions — behind `SUPPLIER_ACCOUNTS_V1`, with 120 tests and zero UI. No supplier can reach any of
it. This arc gives them the door: the same portal surfaces served either by a claim token (first
touch, unchanged) or by a session (returning supplier), with the claim flow ending in an
invitation to create an account.

This is also the arc where `open-requests.tsx` stops being a token-scoped fragment and becomes the
RFQ inbox a supplier logs in to see.

---

## DESIGN DECISIONS (settled — build to these)

**D1. The browser session is an httpOnly cookie, not a bearer token in JS.**
Arc 2 issues an opaque session token raw-once from `POST /api/supplier/auth/verify`. For an API
client that is correct and stays. For the browser it is not: storing it in `localStorage` or
`sessionStorage` makes it XSS-readable, and it would directly contradict the security posture arc
1 locked in (no token in any JS-readable storage — `portal/__tests__/security.test.tsx`,
`quote/__tests__/security.test.tsx`).

Therefore: on successful verify, the backend ALSO sets `gofer_supplier_session` as
`HttpOnly; Secure; SameSite=Lax; Path=/`, expiring with the session. The session dependency
accepts **either** the cookie **or** the existing `Authorization: Bearer` header; the bearer path
and its arc-2 tests are untouched. The frontend never reads, writes or sees the session value.

*Consequence:* this arc touches backend code (cookie issuance + dependency), so it is not
frontend-only. Keep that change minimal and additive.

**D2. CSRF: SameSite=Lax plus an origin check on state-changing routes.**
`SameSite=Lax` blocks cross-site POSTs in current browsers. Belt and braces: every
state-changing `/api/supplier/*` route validates `Origin`/`Referer` against the configured app
origin when authenticated by cookie (not when authenticated by bearer — bearer is not
ambient). Reject with the same uniform 401 shape arc 2 established.

**D3. One set of components, two auth modes.**
Do NOT fork the portal UI. `ClaimPage`, `ProfileForm`, `OpenRequests` and the quote form keep
working exactly as today under a token; they gain a session mode. The data-fetch seam
(`lib/portal-api.ts`, `lib/quote-api.ts`) is where the mode is decided — components should be
agnostic. Arc 1's characterisation tests for the token paths must pass **unmodified**.

**D4. The claim flow ends in an account invitation, not a dead end.**
After a successful `propose-revision` submit, the claim page offers "create your account" wired to
arc 2's `POST /api/portal/{token}/request-account` (T6). The claim token is NOT consumed by this.
Honest copy: the supplier is told a link is being emailed, never that they are logged in.

**D5. Uniform, non-enumerating auth UI.**
The login screen's response is identical whether or not the email is known — "if that address is
on file, we've sent a link". No "account not found", no "check your spam" variant that implies a
send happened. This mirrors arc 2's `request-link` contract; the UI must not reintroduce an
oracle the backend carefully avoids.

**D6. RBAC is reflected in the UI, never enforced by it.**
Member-management controls are hidden when `has_permission` says no, but the server remains the
enforcement point. A hidden button is a courtesy; the 403 is the control.

---

## PRIME DIRECTIVE

1. **Arc 1's security tests are the regression net and must not be modified.** Any change to
   `frontend/src/app/portal/[token]/__tests__/security.test.tsx` or
   `frontend/src/app/quote/[token]/__tests__/security.test.tsx` is an immediate stop.
2. **No existing test file modified** — frontend or backend. New tests, new files. The 2425
   backend and 64 frontend tests pass unedited.
3. **Flags off = today's behaviour exactly.** With `NEXT_PUBLIC_SUPPLIER_SESSION_V1` off, no new
   route renders and no session code path runs; with `SUPPLIER_ACCOUNTS_V1` off, the backend is
   inert as arc 2 established.
4. **Token paths unchanged.** The claim and quote token routes behave identically before and after
   this arc, proven by arc 1's untouched tests.

---

## INVESTIGATION GATE (read-only; report with `file:line` BEFORE building)

- **G1. React version blocker.** Arc 1 finding F3: installed React is 18.3.1, Next 15 runs its own
  vendored React 19, and `portal/[token]/page.tsx` + `quote/[token]/page.tsx` use `use()` — so
  those wrappers cannot be render-tested. This arc adds session routes. **Determine whether the
  new routes can be built and render-tested without a React bump, or whether the bump is a
  prerequisite.** If it is a prerequisite, STOP and report — that becomes its own arc with a full
  regression run. Do not bump React inside this arc.
- **G2. Arc 2's session surface.** Read `utils/supplier_accounts.py` and the session dependency in
  `api_server.py`: exact request/response shapes for `request-link`, `verify`, `me`, `logout`,
  `requests`, `quotes`, `members`. Report the contract verbatim — this arc builds to it, it does
  not redesign it.
- **G3. Cookie insertion point.** Where `verify` returns, and where the session dependency reads
  the bearer. Identify the minimal additive change for D1.
- **G4. Frontend fetch seam.** How `lib/portal-api.ts` / `lib/quote-api.ts` build requests, and
  where an auth-mode switch belongs so components stay agnostic (D3).
- **G5. `open-requests.tsx` today.** Its props, data source and states (arc 1's I4 finding). What
  changes for session mode, and what must not.
- **G6. Frontend flag convention.** How the existing frontend gates flagged features, and whether
  a `NEXT_PUBLIC_*` env is already the pattern.
- **G7. Test-support reuse.** `frontend/src/test-support/fetch-seam.ts` and `fixtures.ts` from arc
  1 — extend, don't duplicate.
- **G8. Route-group/layout structure.** Where authenticated supplier routes should live
  (`src/app/supplier/*`?) without disturbing the existing `portal/[token]` tree.

**STOP after the gate. Commit the report. Do not begin T1 until it is written.**

---

## GUARDRAILS

1. Both flags default off; every new surface inert when off.
2. The session cookie value never appears in JS-readable storage, console output, or any URL.
3. No enumeration oracle in any auth UI copy (D5).
4. New security tests mirror arc 1's shape: full render → interact → sweep storage/cookies/console
   → assert network reach confined to the expected prefix.
5. Reuse arc 1's `fetch-seam` and `fixtures`; do not fork a parallel harness.
6. **PowerShell:** `;` not `&&`.
7. **NO PUSH.** Commit per task.

---

## BUILD TASKS

### T1. Backend: cookie session (D1)
`verify` additionally sets `gofer_supplier_session` (HttpOnly, Secure, SameSite=Lax, Path=/,
expiry = session expiry). Session dependency accepts cookie OR bearer. `logout` clears the cookie
and revokes server-side. Additive only; arc 2's bearer tests untouched.
*Tests (new file):* cookie set on verify with all four attributes; cookie authenticates `me`;
bearer still authenticates `me`; logout clears and revokes; expired cookie → same uniform 401.

### T2. Backend: CSRF origin check (D2)
State-changing `/api/supplier/*` routes validate `Origin`/`Referer` against the configured app
origin **when authenticated by cookie**. Bearer requests exempt.
*Tests:* cookie POST with foreign Origin → 401; with correct Origin → 200; bearer POST with
foreign Origin → 200 (exempt); GET unaffected.

### T3. Frontend: session client + auth-mode seam (D3)
Extend the fetch seam so the same API functions work in token mode or session mode
(`credentials: "include"` for session). Components stay agnostic. A `useSupplierSession` hook
exposes `{account, member, permissions, loading}` from `me`.
*Tests:* token mode produces identical requests to today (parity with arc 1 fixtures); session
mode sends credentials and no Authorization header; a 401 clears session state.

### T4. Login screen (`/supplier/login`)
Email field → `request-link`. Response copy is uniform per D5. Rate-limit feedback must not
distinguish "too many attempts for a known address" from anything else.
*Tests:* submits the email; renders identical confirmation for success, unknown-email and
rate-limited responses; no error state that implies existence.

### T5. Verify landing (`/supplier/verify`)
Reads the token from the URL, POSTs it to `verify`, and on success redirects to the inbox. The
token is never written to storage and never logged. Failure → uniform rejection, identical for
expired/used/unknown/pending, with a path back to login.
*Tests:* success redirects; all four failure modes render byte-identical output (Set-size-1 check,
as arc 1 did); token absent from storage/console after the cycle.

### T6. Session inbox (`/supplier/requests`)
`OpenRequests` served by session, calling arc 2's `GET /api/supplier/requests`. Same component,
same honest empty state (no fabricated rows — arc 1's test asserts this and it must remain true).
*Tests:* renders the account's rows; empty state fabricates nothing; only `/api/supplier/*` is
called; a 401 sends the user to login.

### T7. Session profile (`/supplier/profile`)
`ProfileForm` in session mode, submitting revisions under the session identity. Same tri-state
brand control, same "submitted for review" pending copy — self-declaration remains a proposal
(D3/arc 2's D3).
*Tests:* payload shape matches the token-mode payload; success copy says submitted-for-review, not
saved.

### T8. Session quote submission
The quote form in session mode against arc 2's `POST /api/supplier/quotes`. Flag-not-block sanity
behaviour unchanged.
*Tests:* five required fields plus optional trio submit correctly; a review-flagged result still
confirms honestly (parity with arc 1's quote tests).

### T9. Claim → account bridge UI (D4)
After a successful claim-page submit, offer "create your account" → `request-account`. Honest
copy; claim token not consumed; the existing claim flow is otherwise untouched.
*Tests:* CTA appears post-submit; posts to the bridge endpoint; the claim page still passes arc 1's
unmodified characterisation tests.

### T10. Logout + session chrome
Logout control calls `logout`, clears client state, redirects to login. A minimal authenticated
shell (account name, member email, logout) shared by the session routes.
*Tests:* logout calls the endpoint and redirects; protected routes redirect to login without a
session.

### T11. Member management UI (D6) — droppable
List members, invite, change role, revoke — controls hidden per `has_permission`, server enforcing.
If T1–T10 have consumed the arc, report this as deferred rather than rushing it.
*Tests:* a MEMBER sees no management controls; an ADMIN does; a hidden control's endpoint still
returns 403 when called directly (proving UI is not the enforcement point).

---

## SUCCESS CRITERIA (falsifiable)

1. Backend `uv run pytest -q` green; **2425 pre-existing tests pass with zero edits**; new count
   reported as `2425 + N`.
2. Frontend `npm test` green; **arc 1's 64 tests pass with zero edits**; new count `64 + M`.
3. With both flags off, both suites pass and no new route is reachable — proven by an explicit
   flags-off run.
4. The session cookie is HttpOnly + Secure + SameSite=Lax, asserted on the actual `Set-Cookie`.
5. Session token never appears in `localStorage`, `sessionStorage`, `document.cookie` as read by
   JS, console output, or any URL — asserted after a full login→act→logout cycle.
6. All four verify-failure modes render byte-identical output (equality assertion, not
   four "renders something" checks).
7. No auth UI copy distinguishes a known email from an unknown one (D5).
8. `git diff --stat` shows no modification to any pre-existing test file, frontend or backend.
9. `SUPPLIER_SESSION_REPORT.md` contains the gate report G1–G8 with `file:line`, both final test
   counts, FINDINGS (empty is valid), and follow-ups.

---

## AGENT LOOP PROTOCOL

Filesystem-only handshake; human is sole merge authority. Builder runs gate → report → T1–T11
committing per task → appends counts + FINDINGS. Reviewer writes `SUPPLIER_SESSION_REVIEW.md` with
numbered findings and `loop/VERDICT.txt` (`APPROVED` | `CHANGES_REQUESTED`). Builder fixes only
numbered findings. Repeat.

**Reviewer checklist:**
- **R1.** Any pre-existing test file modified (esp. arc 1's two security files)? → immediate
  `CHANGES_REQUESTED`.
- **R2.** Run both suites with flags OFF. Anything leaking → BLOCKER.
- **R3.** Read the actual `Set-Cookie` in a test response: are all four attributes present? Don't
  trust the report.
- **R4.** Is the storage/console sweep run after a **complete** cycle including logout, or only
  after login?
- **R5.** Is the verify-failure equality a true Set-size-1 check with a contrast case proving it
  would fail on divergent output?
- **R6.** Does any auth UI path leak existence — copy, status code, timing-independent shape,
  or a distinguishable rate-limit message?
- **R7.** CSRF: does a cookie-authenticated POST from a foreign Origin actually fail, tested
  against the real dependency rather than a mock?
- **R8.** Is RBAC enforced server-side with the UI merely reflecting it — i.e. is there a test
  calling a hidden control's endpoint directly and getting 403?
- **R9.** Are FINDINGS genuine, or task-avoidance? Judge each. Was T11 deferred honestly or
  quietly dropped?

**Standing rule (CLAUDE.md):** an exit-checklist item may be marked done only if the evidence
exists as a committed artefact or a named passing test.

---

## OUT OF SCOPE

Notification and delivery tracking (arc 4). React 19 bump (own arc — see G1). Ownership transfer
(arc 2 finding 1). Supplier metrics, loss reasons, paid tiers. Real email delivery / domain
warming (infra track). Inventory integration.

---

## HUMAN VERIFICATION (after `VERDICT: APPROVED`)

```powershell
cd 'C:\dev\_Arkim\Arkim Procurement Agent Prototype'
git diff --stat <branch-point> HEAD
uv run pytest -q                                   # 2425 + N
cd frontend ; npm test ; cd ..                     # 64 + M
$env:SUPPLIER_ACCOUNTS_V1='0' ; uv run pytest -q   # green
Get-Content SUPPLIER_SESSION_REPORT.md
```

Then, flags on with the server running: request a link for a `dxpe.com` address, confirm it lands
in the governance outbox, paste the token into `/supplier/verify`, and confirm you reach the inbox
with the session cookie present in devtools as HttpOnly and **unreadable** from the JS console.

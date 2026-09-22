# SUPPLIER_SESSION_REVIEW.md

**Arc 3 — Supplier Portal Session Conversion. Reviewer: Claude Fable 5.**
Reviewed at `423813e` (branch `arc3/supplier-session`), diffed against the true branch point
`10a048e` (arc 2 merged). Verdict: **APPROVED** — see `loop/VERDICT.txt`. Four findings, all
MINOR, none requiring a builder pass; two are for the human merger to ratify.

A note on the R1 command as given: `git diff --name-status 423813e HEAD` is empty because
`423813e` **is** HEAD (the arc's final commit, the LF-normalization chore). The review therefore
ran R1 against the real branch point `10a048e` and, where useful, the gate commit `4304ba3`.

---

## R1 — No pre-existing test file modified. **PASS.**

`git diff --name-status 10a048e HEAD` (run, full output inspected): every path under a
`__tests__/` directory or `utils/procurement_agent/tests/` has status **A** (new file). Zero
test files carry status M. Specifically:

- `frontend/src/app/portal/[token]/__tests__/security.test.tsx` — **absent from the diff**
  (`git diff 10a048e HEAD --stat -- <path>` produces empty output).
- `frontend/src/app/quote/[token]/__tests__/security.test.tsx` — same, empty diff.
- Arc 1's other characterisation files (`claim-page.test.tsx`, `open-requests.test.tsx`, etc.)
  and arc 2's backend suites (`test_supplier_accounts_auth.py`, `..._rbac.py`) — same, empty.

The M-status files are all source (`api_server.py`, `claim-page.tsx`, `open-requests.tsx`,
`portal-states.tsx`, `globals.css`, `design/interactions.md`) plus the two `test-support`
helpers, which are not test files (vitest collects `src/**/*.test.{ts,tsx}` only,
`frontend/vitest.config.ts:19`) and which guardrail 5 directs reuse of. Verified strictly
append-only by `git diff --numstat 10a048e HEAD`: `fetch-seam.ts` **50 added / 0 removed**,
`fixtures.ts` **74 / 0**.

## R2 — Both suites run with flags OFF. **PASS (run by the reviewer, not trusted).**

- Backend: `SUPPLIER_ACCOUNTS_V1=0; QUOTE_SUBMIT_V1=0; SUPPLIER_PORTAL_V1=0; uv run pytest -q`
  → **2479 passed, 73 skipped, exit 0** (162.6s). Matches the report's 2425 + 54.
- Frontend: `npm test` with `NEXT_PUBLIC_SUPPLIER_SESSION_V1` removed from the environment
  → **172 passed, 21 files, exit 0**. Matches the report's 64 + 108.

Flag-off inertness is additionally pinned per route: every new `page.tsx` has a
"renders NOTHING and makes no request when the flag is off" test asserting
`container.textContent === ""` **and** zero fetches (e.g. `login-screen.test.tsx:237-244`,
`verify-screen.test.tsx:225-231`, `requests-screen.test.tsx`, `members-screen.test.tsx`), plus
an unrecognised-value-is-off case (`login-screen.test.tsx:252-256`). Backend flag-off is pinned
at `test_supplier_session_cookie.py:185-194` (verify → 404, **no** `Set-Cookie`).

## R3 — The actual `Set-Cookie` header, all four attributes. **PASS (read, not trusted).**

The assertion reads the raw header, not the cookie jar:
`_arc3_session_fixtures.py:151-159` (`set_cookie_header`) iterates `response.headers.raw` and
returns the `gofer_supplier_session=` header string. On it,
`test_supplier_session_cookie.py:26-36` asserts `httponly`, `secure`, `samesite=lax`, `path=/`
— all four, on the real header. Supporting cases: `Max-Age` derived from the session expiry and
bounded ≤ 24h (`:47-57`), fail-soft to a session cookie on unparseable expiry (`:59-67`), logout
clears with matching `Path=/` **and** revokes server-side so a kept cookie still 401s
(`:155-177`). Implementation confirmed at `api_server.py:6798-6812`
(`_set_supplier_session_cookie`: `httponly=True, secure=True, samesite="lax", path="/"`).

The fixtures also encode the mechanical fact that makes this testable at all: `Secure` means an
`http://testserver` client never returns the cookie, so cookie tests run on
`https://testserver` (`_arc3_session_fixtures.py:11-21, 87-93`) — and the same fact is why
arc 2's plain-http bearer suite is structurally immune to T1.

## R4 — Sweep after a COMPLETE cycle including logout. **PASS.**

`session-chrome.test.tsx:155-178` ("sweeps clean after load, interaction and sign-out"):
render → open a quote form → **Sign out** → redirect asserted, then sweeps localStorage,
sessionStorage, `document.cookie`, the console dump, **and** asserts no request in the whole
cycle carried an `Authorization` header (`:174-177`). A separate rejected-verify sweep exists at
`verify-screen.test.tsx:202-211`, and the success-path sweep also asserts the **session** token
from the response body is nowhere (`:196-199`).

## R5 — Verify-failure equality is a true Set-size-1 with a contrast case. **PASS.**

`verify-screen.test.tsx:102-113` (Set-size-1 over expired/used/unknown/pending), `:115-133`
(Set-size-1 over divergent backend shapes 401/404/403/500/network-throw), `:135-146` (no-token
case equals the rejected output, and makes zero requests), and the contrast case `:148-159`
renders a genuinely different outcome and asserts **size 2** — the equality has teeth. See
finding 2 for the honest caveat about where the four-modes distinction is actually proven.

## R6 — No enumeration oracle in any auth UI path. **PASS.**

- Copy equality: `login-screen.test.tsx:108-117` — Set-size-1 over known/unknown/unparseable,
  with a contrast case (`:132-152`) proving it would fail on divergent output.
- No echo, no existence verb, no "check your spam": `:120-130`.
- Rate-limit feedback indistinguishable from a network failure and silent about the address:
  `:160-180`; the backend applies the limit **before** any lookup (arc 2, `api_server.py`
  request-link limiter), so the 429 is not an existence signal either.
- No client-side domain/existence pre-validation (`:88-100`) — anything non-empty is sent.
- The same Set-size-1 + contrast shape covers the T9 bridge
  (`account-bridge.test.tsx`: "renders one confirmation regardless of the outcome (D5)",
  "shows a neutral retry on failure, revealing nothing about the address").
- Verify failures never name which mode occurred (`verify-screen.test.tsx:161-172`).

## R7 — CSRF tested against the real dependency, not a mock. **PASS.**

`test_supplier_session_csrf.py` drives `TestClient(api_server.app)` through the real routes;
the fixture (`_arc3_session_fixtures.py:35-76`) patches only storage paths and env flags —
nothing in the auth/CSRF path is mocked, and the magic-link token is read out of the actually
delivered email body (`:120-137`), so the whole login is end-to-end. Coverage: foreign Origin →
401 on both an ungated and a capability-gated route (`:33-40`), correct Origin → 200 (`:42-47`),
Referer fallback both ways (`:49-62`), **neither header → reject** (R-G3b, `:64-75`, including
"a blocked attempt does not log the user out"), five near-miss origins (`:77-86`), GET exempt
(`:90-99`), bearer exempt with foreign Origin (`:101-115`), bearer-first when both credentials
present (`:127-138`), and one-config-surface proof against `_cors_origins` (`:152-165`).

Implementation confirmed: `_supplier_csrf_check` (`api_server.py:6879-6893`) is called inside
`_require_supplier_session` itself (`:6952`), and the capability dependency wraps that same
dependency (`_supplier_require_capability`, `:7158-7167`) — so no session route can bypass the
check. Rejection is the byte-identical uniform 401.

## R8 — A hidden endpoint called DIRECTLY returns 403. **PASS.**

`test_supplier_members_rbac_cookie.py:49-101`: invite, change-role and revoke are each called
directly with a MEMBER's own **cookie** session (the credential the browser actually holds) and
return 403. Two guards make it non-vacuous: the identical request as an ADMIN returns 200
(`:79-89`), proving the 403 is the role and not the CSRF check in disguise; and
`test_me_permissions_predict_the_403` (`:144-157`) asserts the `permissions` list the UI reads
is exactly what the server enforces. The UI half is pinned in `members-screen.test.tsx`
("a MEMBER sees no management controls", ADMIN sees them, OWNER row untouchable). Server
enforcement is one seam: `_supplier_require_capability` → `has_permission` → 403
(`api_server.py:7158-7167`).

## R9 — FINDINGS genuine? T11 honest? **PASS.**

T11 was **built**, not deferred: commit `4b9824b`, `members-screen.tsx` + its 17-case test file
+ the backend `test_supplier_members_rbac_cookie.py`. The five findings are real work, not
task-avoidance: F5 is a self-reported mistake (CRLF churn) with the correction verified —
`api_server.py`'s cumulative diff is 351 lines (304+47 by numstat), not 14k; F6 is a genuine
test-hygiene catch (flag-off tests that only passed because the ambient env was unset) with the
fix visible in every route test (`vi.stubEnv(FLAG, "0")`); F7 honestly names the one part of T2
jsdom cannot prove and assigns it to human verification; F8/F9 are real deployment coupling
notes. The T2b scope expansion was disclosed as its own named commit rather than buried — see
finding 3.

---

## Findings

1. **MINOR — per-task diffs T1–T11 are polluted by CRLF noise; only the cumulative diff is
   clean.** (Self-reported as report finding F5.) `git show e578856 -- api_server.py` and
   siblings still contain the line-ending churn; `git diff 10a048e HEAD` is clean (verified:
   `api_server.py` 304/47). Brief clause: §5-adjacent history hygiene, not a listed guardrail —
   hence MINOR. No rebuild requested: the human verification step diffs the branch point, which
   is clean. If the human wants reviewable per-task history, ask the builder to rebuild the
   branch; do not let the builder rewrite history unprompted.

2. **MINOR — the frontend "four failure modes" equality is structurally guaranteed at the UI
   layer.** `verify-screen.test.tsx:102-113` stubs the same `unauthorized()` response for all
   four token names, so Set-size-1 cannot fail there; the distinction-collapsing is actually
   proven backend-side (arc 2's uniform verify 401, and this arc's
   `test_supplier_session_cookie.py:109-141` Set-size-1 over cookie failure modes) plus the
   divergent-shapes test (`:115-133`) and the contrast case (`:148-159`). The success criterion
   6 is satisfied by the combination; noting so nobody later reads the four-modes UI test alone
   as the proof. No change requested.

3. **MINOR (for the human to ratify) — the backend grew beyond D1's "cookie issuance +
   dependency": commit `bfc12f7` (T2b) adds `GET /api/supplier/profile`,
   `POST /api/supplier/propose-revision`, `GET /api/supplier/quotes`, and
   `member.permissions` on `me`, and wires the previously-unenforced `VIEW_REQUESTS` /
   `PROPOSE_REVISIONS` capabilities.** The gate predicted exactly this (report F1/F2), the gate
   report said "Awaiting … a ruling on F2's backend scope before T1", and the builder then
   proceeded without one — the brief's gate only required the report to be written, so this is
   within the letter of the protocol, but the merge authority should consciously accept the
   larger backend surface. Mitigations verified: each new route goes through the same shared
   service as its token sibling (`_supplier_profile_body`, `_validate_revision_brands`,
   `_supplier_quote_history` — pure extractions, `api_server.py:5579-5611, 6390-6426`), arc 1's
   portal tests pass unedited, and `permissions` is nested under `member` so arc 2's
   top-level-keys assertion holds (`api_server.py:6971-6981`). Brief clause: D1 *Consequence*
   ("Keep that change minimal and additive").

4. **MINOR (for the human to ratify) — member management alone carries the HTTP status on
   rejections** (`supplier-api.ts:93-152`, `withStatus`; surfaced in
   `members-screen.test.tsx` "server refusals are surfaced honestly"). A deliberate, argued
   deviation from the uniform-rejection posture (builder decision 5): the caller is an
   authenticated admin inside their own account, so 409 "already a member" / 422 "invalid
   email" are not enumeration oracles. I agree with the reasoning; flagging it because D5's
   uniformity rule is otherwise absolute in this arc and the exception should be a recorded
   choice, not a precedent discovered later.

No BLOCKER or MAJOR findings. Guardrail 2 was swept independently: no new source file touches
`localStorage`/`sessionStorage`/`document.cookie` (grep over `frontend/src` excluding tests —
the only hits are pre-existing `api.ts`, `admin/page.tsx`, `proc-shell.tsx`,
`settings-screen.tsx`); the session client's only credential mechanism is
`credentials: "include"` (`supplier-api.ts:120-152`), and the flag is read as a literal member
expression with default-off (`flags.ts:24-40`).

---

## Verdict

**APPROVED.** Written to `loop/VERDICT.txt` (gitignored, per protocol). Findings 3 and 4 are
ratification items for the human merger, not builder work; findings 1 and 2 require no action.

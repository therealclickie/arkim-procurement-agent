# FRONTEND TEST FLOOR — REVIEW (Fable 5, reviewer)

**Date:** 2026-09-20
**Branch:** `arc1/frontend-test-floor`, HEAD `15b9595`
**Arc baseline:** `074d126` (branch cut point per brief)
**Note:** this file replaces a stale review written in an earlier loop cycle before the builder
had committed anything; that verdict ("arc not executed") no longer applies.

## Verdict summary

**CHANGES_REQUESTED — one MAJOR finding.** The work is substantially good: the prime directive
held (zero source changes), the security tests are genuine, the uniform-rejection check is a true
equality check with a contrast proof, both suites are green and deterministic, and all five
builder FINDINGS verified as honest. But **success criterion 2 is unmet for the two public route
wrappers** (`portal/[token]/page.tsx`, `quote/[token]/page.tsx`): the structural tests the gate
promised were never written, a test-file comment claims coverage in a file that does not exist,
and report F3 asserts coverage that was not delivered. That is precisely the "marked done on
intent" failure the standing rule forbids. The fix is small and mechanical.

---

## Test-run evidence

- `npm test` (frontend), **run twice** back-to-back:
  - Run 1 (09:38:24): `Test Files 9 passed | 1 skipped (10)`, `Tests 54 passed | 2 skipped (56)`, exit 0.
  - Run 2 (09:38:33): identical counts, exit 0.
  - Matches the report's final count. No order/timing dependence observed (see R6).
- `uv run pytest -q` (backend): **`2305 passed, 73 skipped`** in 127.84s, exit 0 — exactly the
  expected baseline; the arc did not disturb the backend.

---

## Reviewer checklist R1–R8

### R1 — any change outside tests / test config / devDependencies?

`git diff --stat 15b9595 HEAD` (the commanded check) is **empty by identity** — HEAD *is*
`15b9595`. The meaningful scope check is the whole arc, `git diff --stat 074d126 HEAD`
(17 files, +2543/−3):

- **In scope:** 9 test files under `__tests__/`, `frontend/src/test-support/{fetch-seam,fixtures}.ts`,
  `frontend/vitest.config.ts`, `frontend/vitest.setup.ts` (test config), `frontend/package.json`
  (devDependencies only: `@testing-library/react`, `@testing-library/user-event`, `jsdom` —
  verified against the diff hunk) + `frontend/package-lock.json` (consequence of the devDeps),
  and `FRONTEND_TEST_FLOOR_REPORT.md` (mandated by success criterion 7).
- **Outside the allowed set, named per instruction:** `.gitignore` (+4 lines, commit `883460f`
  "chore: ignore arc1 loop working files" — ignores `build.log`, `review.log`, `loop.log`,
  `VERDICT.txt`). This commit predates the builder's gate commit (`204d2de`) and is loop-harness
  housekeeping; it touches no source, no test, and cannot affect any behaviour under test.
  **Judged: not a prime-directive violation** (recorded as finding 2, MINOR, for the human to
  confirm it was the harness/operator's own commit). No application source file was modified
  anywhere in the arc — `Source changes made this arc: none` in the report is **true**.

### R2 — vacuous tests?

**None found.** Spot evidence of the opposite:

- Exact payload equality read at the real fetch seam, not a mapper unit call:
  `profile-form.test.tsx:75-77` (brands payload `toEqual`), `:86`/`:97` (both ship-area shapes),
  `quote-page.test.tsx:122-131` (full POST body `toEqual`, all eight fields).
- Renders-nothing assertions are guarded against vacuity: `open-requests.test.tsx:55-61` first
  proves both fetches actually happened and failed, *then* asserts `container.textContent === ""`
  and zero sections.
- The equality check's discriminating power is itself proven by a contrast case:
  `quote/security.test.tsx:121-129` (409 CLOSED renders distinct copy).
- `gofer-loader.test.tsx` is thin but each of its three tests asserts a named behaviour
  (accessible name default/override, explicit size box), not mere render success.

### R3 — do the T3 security tests genuinely verify absence?

**Yes.** Both security files drive a **full render + interact + submit cycle to the confirmed
end-state before sweeping**: portal `security.test.tsx:64-91` (submit → "Submitted for review" →
sweep `localStorage`, `sessionStorage`, `document.cookie`, and five spied `console.*` methods,
spies installed *before* render at `:65`); quote `security.test.tsx:79-96` likewise. Network
reach is asserted from the recorded seam calls, not from component state: every URL must match
`^/api/portal/` (resp. `^/api/quote/`) with `calls.length > 0` guarding vacuity
(portal `:103-106`, quote `:68-71`), plus explicit no-`/api/admin` and no-Authorization-header
checks (portal `:109-123`, quote `:72-75`). Two robustness caveats → findings 3 and 4 (MINOR).

### R4 — is uniform rejection a true equality check?

**Yes — better than the brief asked.** `claim-page.test.tsx:110-138`: four failure shapes
(404 / 410 / 500 / network throw — the brief asked for three), each fully rendered, whole-body
text collected, then `expect(new Set(texts).size).toBe(1)` (`:137`) — byte-identical, not four
"renders something" checks. Same construction on the quote route
(`quote/security.test.tsx:100-119`, with 422 in the mix), and the 409 contrast test (`:121-129`)
proves the equality would actually fail on divergent output. T2.4 and T3.5 both exist, as the
brief required.

### R5 — does the honesty test assert absence?

**Yes.** `claim-page.test.tsx:102-106`: after rendering the zero-state framing, it asserts the
teaser hero section's text contains **no digit at all** (`not.toMatch(/\d/)`) and that
`/0 buyer request/` matches nothing. That is the absence requirement, not a presence-of-framing
proxy.

### R6 — order or timing dependence?

**None observed.** Two consecutive full runs produced identical results (counts above).
Hygiene supports it: `vitest.setup.ts:12-16` runs `cleanup()` + `vi.unstubAllGlobals()` +
`vi.restoreAllMocks()` after every test, so per-test fetch stubs cannot leak across files;
admin tests clear `localStorage` before seeding (`admin-portal-controls.test.tsx:45`); fixtures
use fixed date strings (no wall clock); the one "pending" case uses a never-settling promise
(`fetch-seam.ts:46-48`), not a sleep; async settlement goes through `findBy*`/`waitFor`, no
arbitrary timeouts.

### R7 — every success criterion backed by a named test or recorded FINDING?

| Criterion | Status |
|---|---|
| 1. `npm test` green, original 8 unchanged | **Met** — 54+2 both runs; `options-compose.test.ts` absent from the arc diff (untouched) |
| 2. Every listed route file exercised | **NOT MET** — `portal/[token]/page.tsx` and `quote/[token]/page.tsx` have **zero** tests (finding 1). All other listed files are genuinely exercised (client bodies directly; `portal-states.tsx` via ClaimPage's loading/rejected/submitted renders; `lib/portal-api.ts`/`quote-api.ts`/`brand.ts` run unmocked beneath the fetch seam) |
| 3. Six T2 states | **Met** — six named describes, `claim-page.test.tsx:42-176` |
| 4. Five T3 assertions | **Met, passing** — storage/cookies (T3.1), console (T3.2), portal reach (T3.3), quote reach (T3.4), uniform rejection (T3.5); none needed a skip |
| 5. Diff scope | See R1 — clean except `.gitignore` (finding 2, MINOR) |
| 6. No real network I/O | **Met** — every HTTP call intercepted at the `globalThis.fetch` stub; the T8 scan is filesystem-only |
| 7. Report with gate, count, FINDINGS | **Met** — gate I1–I6 with `file:line`, final count matching the observed runs, five FINDINGS |

### R8 — are the FINDINGS genuine, or is `.skip` dodging hard tests?

**Genuine — each independently verified against source by this review:**

- **F1 (confirmed):** `src/components/proc/gofer-mark.tsx:29` is literally `alt="Gofer"` — a
  hard-coded title-case brand literal in scope, outside `brand.ts`. Real finding.
- **F2 (confirmed):** grep of `frontend/src` for `/api/admin/quotes` → zero matches; path C has
  no frontend surface. Scoped limitation, honestly recorded.
- **F3 (constraint confirmed, delivery not):** React 18.3.1 genuinely lacks the `use` export the
  route wrappers import (Next substitutes vendored React 19 at build time). But the compensating
  structural coverage F3 claims was delivered **does not exist** — see finding 1.
- **F4 (confirmed):** `admin/page.tsx:167-200` — `load()` clears `claimLink` only inside the
  `portal-revisions` branch (`:187`); the only other clear is the tab-*switch* effect
  (`:208-210`). A Refresh on the suppliers tab (`:522` → `load(tab)`) re-fetches without
  clearing, leaving the show-once raw token displayed. Real show-once violation; real arc-2 work.
- **F5 (confirmed):** `utils/supplier_portal.py:60-63` says "Gofer", matching `BRAND_NAME` —
  the brief's "still contains Arkim" expectation is stale, correctly recorded as no-defect.

The two `.skip`s (`brand-discipline.test.ts:65`, `admin-portal-controls.test.tsx:121`) are
**complete, runnable test bodies** with `// FINDING:` comments per the prime directive — they
will run the moment the source is fixed. Not dodges. No hard test from the brief was skipped to
avoid writing it.

---

## Findings

### 1. MAJOR — Route-wrapper coverage claimed but never built (success criterion 2; standing rule / R7)

- **Evidence:** No test file imports `src/app/portal/[token]/page.tsx` or
  `src/app/quote/[token]/page.tsx` (glob of all `__tests__` dirs: 10 files, none is a route
  test; the only `page` import is admin's client page, `admin-portal-controls.test.tsx:18`).
  Yet `claim-page.test.tsx:8-10` states "The route wrapper page.tsx is covered structurally in
  **portal-route.test.ts**" — **that file does not exist** — and report F3
  (`FRONTEND_TEST_FLOOR_REPORT.md:340`) states the route files "get structural (not render)
  coverage", which they do not. The gate itself (report `:81-91`) committed to option (a):
  structural tests asserting `metadata.referrer === "no-referrer"`, `dynamic === "force-dynamic"`,
  and token→child wiring — none was written.
- **Brief clauses violated:** SUCCESS CRITERIA 2; the standing rule ("an exit-checklist item may
  be marked done only if the evidence exists as a committed artefact or a named passing test").
- **Required fix (tests only, in scope):** add the structural route tests per the gate's own
  option (a) for both `page.tsx` files (module-level import is safe per the gate — only
  *rendering* crashes), **and** correct the false comment in `claim-page.test.tsx:8-10` and the
  F3 coverage wording in the report. If structural import proves infeasible, the honest
  alternative is an explicit FINDING that both route wrappers are uncovered — not a comment
  pointing at a nonexistent file.

### 2. MINOR — `.gitignore` change is outside the arc's allowed file set (R1 / prime directive §"git diff --stat")

- **Evidence:** commit `883460f` (+4 lines: `build.log`, `review.log`, `loop.log`,
  `VERDICT.txt`), committed *before* the builder's gate commit `204d2de`.
- **Judgement:** loop-harness housekeeping with zero source/test impact — named here so the
  human can confirm it was the operator's own setup commit, not builder scope creep. Does not
  drive the verdict. (Side effect worth knowing: `VERDICT.txt` is now gitignored, so committing
  it requires `git add -f`.)

### 3. MINOR — Authorization-absence checks would pass vacuously if headers became a `Headers` instance

- **Evidence:** `portal/__tests__/security.test.tsx:58-61` and `quote/__tests__/security.test.tsx:58-61`
  read `init.headers` as a plain `Record<string, string>`. If a future client passed a `Headers`
  object, `h?.["Authorization"]` is `undefined` even when the header **is** set — the absence
  assertion (`toBeUndefined`) would keep passing while the posture regressed. (The admin-side
  *presence* check `admin-portal-controls.test.tsx:90-93` fails loudly in that case — fine.)
- **Suggested fix:** normalise via `new Headers(call.init?.headers).get("authorization")` in the
  shared helper. Non-blocking.

### 4. MINOR — "No substring of the token" is implemented as full-token containment

- **Evidence:** `portal/__tests__/security.test.tsx:74-76`, `quote/__tests__/security.test.tsx:87-89` —
  `storageDump(...).not.toContain(TOKEN)`. T3.1's wording ("no substring of the token") is
  stronger; a truncated-token leak would slip past. A literal substring check is ill-defined,
  but `expect(store.length).toBe(0)` on both storages after the cycle is simple, stronger, and
  matches what the sweep found anyway (nothing is written). Non-blocking.

### 5. MINOR — T8 fully skipped leaves no live regression net against *new* brand literals

- **Evidence:** `brand-discipline.test.ts:65` — the file's single test is `.skip`ped, so a new
  hard-coded `"Gofer"`/`"Arkim"` literal added tomorrow triggers nothing. The `.skip` follows
  the prime directive as written, so this is **not a violation** — but the stronger
  characterisation shape (assert `violations` equals exactly the one known F1 entry) would keep
  the net live while still surfacing F1. Also noted: T8's backend-mirror clause landed as a
  report note (F5) rather than an assertion — acceptable given the brief's expectation was
  confirmed stale. For the human/next cycle to consider; non-blocking.

---

## Required changes (builder)

Only finding 1 blocks: add the two structural route tests (or the honest FINDING), and fix the
false coverage statements in `claim-page.test.tsx:8-10` and report F3. Findings 3–5 are optional
hardening; finding 2 needs only human confirmation.

VERDICT: CHANGES_REQUIRED

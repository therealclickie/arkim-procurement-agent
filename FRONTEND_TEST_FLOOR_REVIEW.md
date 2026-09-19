# FRONTEND TEST FLOOR — REVIEW (Fable 5, reviewer) — RE-REVIEW OF FIX COMMIT

**Date:** 2026-09-20
**Branch:** `arc1/frontend-test-floor`, HEAD `8dfe786` (builder's remediation of the previous
CHANGES_REQUESTED verdict at `15b9595`)
**Arc baseline:** `074d126` (branch cut point per brief)
**Scope of this pass:** re-review of finding 1 (MAJOR), the truthfulness corrections
(claim-page comment, report F3), findings 3–4 fixes, and a fresh arc-wide prime-directive check.
The full R1–R8 checklist from the previous cycle stands and was not re-litigated except where the
fix commit touched it.

## Verdict summary

**APPROVED.** The MAJOR finding is genuinely fixed — this time with committed artefacts, not
claims. Both structural route-wrapper tests exist, run, and are non-vacuous (every assertion was
verified against the actual `page.tsx` sources; changing the wrappers' security posture would fail
them). The previously-false coverage comment and report F3 wording are now truthful. Findings 3
and 4 are fixed exactly as suggested. The prime directive held across the entire arc: the
baseline-to-HEAD diff contains **zero application source changes**. Suite run twice by this
reviewer: identical green results, and the commit message's claimed count matches the observed
runs exactly.

---

## Test-run evidence (this re-review)

- `npm test` (frontend), **run twice** back-to-back by the reviewer:
  - Run 1 (09:58:10): `Test Files 11 passed | 1 skipped (12)`, `Tests 62 passed | 2 skipped (64)`, exit 0.
  - Run 2 (09:58:24): identical counts, exit 0.
- Delta from the pre-fix suite (54 passed | 2 skipped) is **exactly the 8 new structural tests**
  (4 per route file, 2 new test files: 9→11 passed files). The 2 skips are unchanged (the F1/F4
  finding tests). The commit message's "62 passed | 2 skipped (twice, identical)" is **honest**.

---

## Re-review findings

### (a) Finding 1 (MAJOR) — route-wrapper structural tests: FIXED, artefacts verified

Both files exist and are committed in `8dfe786`:

- `frontend/src/app/portal/[token]/__tests__/portal-route.test.ts` (44 lines, 4 tests)
- `frontend/src/app/quote/[token]/__tests__/quote-route.test.ts` (40 lines, 4 tests)

**Non-vacuity check — every assertion cross-verified against the real wrapper sources:**

- `dynamic === "force-dynamic"` and `metadata.referrer === "no-referrer"` (+ exact titles
  `"Confirm your supplier profile"` / `"Submit your quote"`) are asserted **on the live imported
  module** (`import PortalTokenPage, { dynamic, metadata } from "../page"`), not on a copy or a
  string — deleting `referrer` from either `page.tsx` fails the test. This is exactly the gate's
  option (a) contract. Matches `portal/[token]/page.tsx:30,39` and `quote/[token]/page.tsx:24,29`.
- The module import itself proves the gate's "import is safe, only render crashes" claim — the
  suite would error at collection if it weren't true.
- Token→child wiring is asserted by raw-source scan (`page.tsx?raw`): `use(params)`,
  `<Suspense fallback={null}>`, `<ClaimPage token={token} />` / `<QuotePage token={token} />` —
  all three patterns verified present in the actual sources (`portal:43-47`, `quote:33-37`);
  rewiring or dropping the token prop fails the test. The `?raw` route is a legitimate workaround
  for vitest's non-file-scheme `import.meta.url` (documented in-file).
- The no-inline-`<meta>` negative assertion (`/<meta[^>]/i`) was checked against both wrappers'
  doc comments, which *do* contain the prose string `<meta>` — the `[^>]` guard correctly
  excludes those, so the assertion is discriminating, not accidentally-passing. (It would catch a
  real inline `<meta name=...>` element, which is the regression it exists for.)

Success criterion 2 is now met: every listed route file is exercised — client bodies by render
tests, both server wrappers by structural tests.

### (b) Truthfulness corrections: VERIFIED

- `claim-page.test.tsx:7-11` now points at `portal-route.test.ts`, **which exists**, and
  accurately describes what it does (module import, not render). The false
  pointer-to-nonexistent-file is gone.
- Report F3 (`FRONTEND_TEST_FLOOR_REPORT.md`) now states structural coverage is **delivered**,
  names both test files (verified: names match the committed files), describes the actual
  assertions (verified: description matches the test bodies), and correctly re-scopes the
  out-of-arc React 19 bump to *render* coverage only. The appended FIX LOG is accurate on every
  point this review checked, including the post-fix count.

### (c) Findings 3 and 4: FIXED correctly

- **Finding 3** — both `security.test.tsx` `headerValue` helpers now normalise via
  `new Headers(call.init?.headers).get(name)` returning `string | null`, asserted `toBeNull()`.
  The `Headers` constructor accepts plain records, `Headers` instances, and entry arrays, and
  `.get` is case-insensitive — the vacuous-pass hole is closed and the redundant second-spelling
  lookup is correctly dropped. `new Headers(undefined)` is valid, so header-less calls still
  assert cleanly.
- **Finding 4** — both files now assert `window.localStorage.length === 0` and
  `window.sessionStorage.length === 0` after the full submit cycle, positioned **after**
  `await screen.findByText(...)`/`fullSubmitCycle()` (so the cycle really completed first), with
  the original containment sweeps retained. This is the stronger shape the review suggested; a
  truncated-token leak now fails.

### (d) Prime directive — arc-wide: HELD

`git diff --stat 074d126 HEAD` (21 files, +2905/−3): 12 test files, `src/test-support/` (2),
`vitest.config.ts` + `vitest.setup.ts`, `package.json` (devDeps only) + lockfile, the report,
this review file + `VERDICT.txt`, and the previously-adjudicated `.gitignore` (finding 2, MINOR,
pre-gate harness housekeeping — judgement unchanged, still awaiting only human confirmation).
**No file under `frontend/src` outside `__tests__`/`test-support` was touched; no `utils/`,
`api_server.py`, or any backend file appears in the diff.** The fix commit itself touched only
2 new test files, 3 existing test files, and the report — the commit message's "No application
source changed" is true.

---

## Open items carried forward (non-blocking, unchanged from previous review)

- **Finding 2 (MINOR):** `.gitignore` commit `883460f` — human to confirm it was operator/harness
  housekeeping. Not a builder issue; does not block.
- **Finding 5 (MINOR):** T8 brand-discipline test remains `.skip`ped, so no live net against new
  brand literals. The builder's FIX LOG correctly defers this to the human — converting it to a
  pinned-violations characterisation changes test semantics and is the human's call. Reviewer
  continues to recommend it for the next cycle.

---

VERDICT: APPROVED

# BUILD BRIEF — Frontend Test Floor (Portal / Quote / Admin surfaces)

**Arc:** 1 of the supplier-account programme. **Type:** tests only, no behaviour change.
**Branch:** cut from `test/flag-on-integration` @ `074d126` (baseline verified green).
**Builder:** GLM 5.2 (Fireworks). **Reviewer:** Claude Fable 5.1. **Merge authority:** human only.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

Backend suite: **2305 passed / 73 skipped**. Frontend suite: **8 passed, in one file**
(`src/components/proc/__tests__/options-compose.test.ts`).

Everything else on the frontend is untested — including the only public, unauthenticated,
customer-facing routes in the product:

```
src/app/portal/[token]/page.tsx | claim-page.tsx | portal-states.tsx
                                | profile-form.tsx | open-requests.tsx
src/app/quote/[token]/page.tsx  | quote-page.tsx | quote-form.tsx
src/app/admin/page.tsx          | portal-admin.tsx
src/lib/brand.ts | src/lib/portal-*.ts | src/lib/quote-*.ts
```

These surfaces carry a security posture (token hygiene, uniform rejection, no admin reachability)
that is currently guaranteed by nothing but the care taken when they were written. The next arc
adds supplier authentication and will touch exactly these files. **A regression net must exist
before that happens.**

---

## MISSION

Add a characterisation + security test floor over the existing portal, quote and admin surfaces.
Lock in current behaviour so the account-conversion arc cannot silently break it.

**This is a TESTS-ONLY arc.** See the prime directive below — it is the single most important
instruction in this brief.

---

## PRIME DIRECTIVE — DO NOT FIX SOURCE

Tests must characterise **what the code does today**, not what it ought to do.

If a security or behavioural assertion **fails against current source**, that is a FINDING, not a
defect to patch. The builder must:

1. Leave the source untouched.
2. Mark the test `.skip` with a comment `// FINDING: <what was observed>`.
3. Record it in `FRONTEND_TEST_FLOOR_REPORT.md` under **FINDINGS**.

Rationale: a real security defect on a public route is an arc of its own, with human sign-off. A
builder that quietly edits source to turn a test green destroys the exact signal this arc exists
to produce. **`git diff --stat` at the end of this arc must show changes only under test files,
test config, and `package.json` devDependencies.**

---

## INVESTIGATION GATE (read-only; report findings with `file:line` BEFORE writing any test)

- **I1.** Test tooling: vitest version (observed 4.1.10), config location, current `test` script,
  and whether a setup file exists. Is `jsdom` / `happy-dom` configured? Is
  `@testing-library/react` + `@testing-library/user-event` already a devDependency?
- **I2.** Next.js version and router mode (App Router observed). Which of the listed components are
  server components vs `"use client"`? This determines what is testable as a unit vs what needs a
  rendered-client harness. Report the split explicitly.
- **I3.** How each route fetches: native `fetch`, a wrapper in `src/lib/portal-*.ts`, or something
  else. Identify the single seam to mock. Report the exact exported function names.
- **I4.** What `open-requests.tsx` actually does today — it was **out of scope** in
  `SUPPLIER_PORTAL_FRONTEND_BRIEF.md` yet exists. Summarise its current behaviour, its data source,
  and its states. This is load-bearing for arc 3; report it carefully.
- **I5.** Admin auth mechanism used by `portal-admin.tsx` (header? key? session?) and how the admin
  page obtains it.
- **I6.** Existing conventions: how `options-compose.test.ts` is structured, where tests live
  (`__tests__` sibling dirs), naming, and any existing mock/fixture helpers to reuse.

**STOP after the gate and emit the report. Do not begin T1 until the gate report is written.**

---

## GUARDRAILS

1. **No source changes.** Test files, test config, and devDependencies only.
2. **No network.** All HTTP mocked at the seam found in I3. A test that hits a real endpoint is a
   defect.
3. **No snapshot-only tests.** Every test asserts named behaviour. A snapshot that would pass
   against broken output is worthless here.
4. **No new heavy dependencies.** Testing Library + jsdom if absent; nothing else without
   justification in the report.
5. **Deterministic.** No reliance on wall-clock timing, no arbitrary sleeps, no ordering
   assumptions between test files.
6. **PowerShell environment:** `;` not `&&`; here-strings via `@'...'@`.
7. **NO PUSH.** Commit per task.

---

## BUILD TASKS

Each task names a **minimum** assertion set. More is fine; fewer is a failed task.

### T1. Test infrastructure (only if the gate shows it missing)
Configure a component-test environment (jsdom + Testing Library) alongside the existing unit tests
so `npm test` runs both. Existing 8 tests must still pass unchanged. Report the new `npm test`
count as `8 + N`.

### T2. Portal claim route — the six states
Against `src/app/portal/[token]/`, one test per state from the original brief:
1. **Loading** — indicator present while the profile fetch is pending.
2. **Valid + `has_matches: true`** — teaser renders `count` and `window_days`; teaser appears
   **before** the profile form in document order (demand-as-hero is a product invariant).
3. **Valid + zero-state (`has_matches: false`)** — `framing` text renders; **assert the absence of
   `"0"` as a match count** and the absence of any fabricated number. This is the honesty carve-out.
4. **Invalid / expired / reused** — all three non-200 shapes render the *identical* rejection
   output. Assert equality of rendered text across the three, not just that each renders something.
5. **Submitted** — success message is pending-toned ("submitted for review"), **not** "saved".
6. **Submit error** — the supplier's entered input is still present in the DOM after the failure.

### T3. Security posture (highest value in this arc)
1. After a full render + submit cycle, `localStorage` and `sessionStorage` contain **no** substring
   of the token; `document.cookie` likewise.
2. The token does not appear in any `console.*` output (spy on console).
3. Every URL passed to the mocked fetch seam from the public portal page matches
   `^/api/portal/` — assert **no** call to any `/api/admin/*` path.
4. Same for the quote route: only `/api/quote/*` (confirm the actual prefix in the gate).
5. Rejection path leaks no oracle: rendered output for expired vs invalid vs reused is
   byte-identical (this overlaps T2.4 deliberately — keep both).

### T4. Profile form
1. Tri-state brand relationship control offers exactly `AUTHORIZED | CARRIES | AFTERMARKET_COMPATIBLE`
   and the chosen value reaches the submit payload.
2. Ship-area submits as `{kind:"NATIONWIDE_US"}` or `{kind:"STATES", states:[...]}` — assert both shapes.
3. Aftermarket disclosure renders when `aftermarket_disclosure` is non-null, and is absent when null.
4. A freshly-claimed supplier (empty `brands`, empty `classes`, `null ship_area`) renders the normal
   "fill me in" state and **not** an error state.

### T5. Open requests
Characterise current behaviour per the I4 gate finding: what renders with data, what renders empty,
and whether an empty state fabricates anything. **If the empty state shows a fabricated or
placeholder request, that is a FINDING** under the prime directive — skip-and-report, do not fix.

### T6. Quote route
1. The five quote fields render and validate per current behaviour.
2. Submit produces the expected payload shape.
3. Each of the three documented entry paths reaches the form (confirm the three in the gate).
4. Sanity checks flag rather than block — assert a flagged submission still submits.

### T7. Admin portal controls
1. Generate-link shows the raw token **once**; a re-render or second fetch does not re-display it.
2. Regenerate replaces the displayed token.
3. Approve and reject call their respective endpoints with the revision id.
4. Admin auth material never appears in the public route's fetch calls (cross-check with T3.3).

### T8. Brand discipline
A test that scans `src/app/portal`, `src/app/quote` and `src/components` for hard-coded
`"Gofer"` / `"Arkim"` string literals and fails if any exist outside `src/lib/brand.ts`.
`BRAND_NAME` is the single source of truth. **Also assert** the backend mirror is flagged: if
`utils/supplier_portal.py::_ZERO_STATE_FRAMING` still contains `"Arkim"`, record it as a FINDING
(do not edit the backend in this arc).

---

## SUCCESS CRITERIA (falsifiable)

1. `npm test` green; original 8 tests unchanged and still passing.
2. Every route file listed in WHY THIS ARC EXISTS has at least one test exercising it.
3. All six portal states from T2 covered by named tests.
4. All five T3 security assertions present and passing (or skipped with a recorded FINDING).
5. `git diff --stat` shows **zero** changes outside test files, test config, and `package.json`
   devDependencies.
6. No test performs real network I/O.
7. `FRONTEND_TEST_FLOOR_REPORT.md` exists containing: the gate report (I1–I6 with `file:line`), the
   final test count, and a FINDINGS section (empty is a valid and good result).

---

## AGENT LOOP PROTOCOL

Filesystem is the only channel between agents. Human is the sole merge authority.

**Cycle:**
1. **GLM 5.2 (builder)** — runs the investigation gate, writes `FRONTEND_TEST_FLOOR_REPORT.md`,
   then builds T1–T8, committing per task. Ends by appending the final test count and FINDINGS.
2. **Fable 5.1 (reviewer)** — reads this brief, the report, and the diff. Writes
   `FRONTEND_TEST_FLOOR_REVIEW.md`. Ends with a line reading exactly `VERDICT: APPROVED` or
   `VERDICT: CHANGES_REQUIRED`.
3. **GLM 5.2** — if `CHANGES_REQUIRED`, fixes only the numbered findings, appends a fix log to the
   report, hands back. Repeat.
4. **Human** — reviews the FINDINGS section (that's the part only you can action) and merges.

**Reviewer checklist — Fable must explicitly answer each, with evidence:**
- **R1.** Does `git diff --stat` show any change outside tests/config/devDeps? If yes →
  `CHANGES_REQUIRED` immediately, prime directive violated.
- **R2.** Are any tests vacuous — asserting a truthy render, an empty snapshot, or a mock calling
  itself? Name each with `file:line`.
- **R3.** Do the T3 security tests genuinely verify absence (inspecting storage/cookies/console
  after a full cycle), or do they merely assert the component rendered?
- **R4.** Is the T2.4/T3.5 uniform-rejection test a true equality check across all three failure
  modes, or three separate "renders something" checks?
- **R5.** Is the T2.3 honesty test asserting the *absence* of a fabricated number, or only the
  presence of the framing text? Absence is the requirement.
- **R6.** Are any tests order-dependent or timing-dependent? Run the suite twice and report.
- **R7.** Does every success criterion have a named passing test or a recorded FINDING? Criteria
  cannot be marked done on intent.
- **R8.** Are the FINDINGS genuinely findings (source behaviour differs from the posture), or is the
  builder using skip to avoid hard tests? Judge each one.

**Standing rule (from CLAUDE.md):** an exit-checklist item may be marked done only if the evidence
exists as a committed artefact or a named passing test. A comment, an intention, or a deploy-time
promise remains unchecked.

---

## OUT OF SCOPE

Supplier authentication and magic-link login (arc 2). Route conversion from token to session
(arc 3). Notification and delivery tracking (arc 4). Visual/brand polish. Backend changes of any
kind. Fixing anything this arc discovers.

---

## HUMAN VERIFICATION (Tom, after `VERDICT: APPROVED`)

```powershell
cd 'C:\dev\_Arkim\Arkim Procurement Agent Prototype'
git diff --stat                      # expect: tests, config, package.json only
cd frontend ; npm test               # expect: 8 + N passing
cd .. ; uv run pytest -q             # expect: still 2305 passed / 73 skipped
Get-Content FRONTEND_TEST_FLOOR_REPORT.md   # read the FINDINGS section
```

The FINDINGS section is the deliverable that matters most. An empty FINDINGS section means the
public surfaces behave as specified. A non-empty one sets the agenda for the next arc.

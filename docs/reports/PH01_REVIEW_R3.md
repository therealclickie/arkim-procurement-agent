## PH-01 final review, branch `fix/ph01-hygienic-fields` (6bab036 → c395d53)

**Test results (from the saved files, not re-run)**

| Suite | File | Result |
|---|---|---|
| `uv run pytest -q` | ph01_pytest.txt:248 | 3269 passed, 73 skipped, 1 warning, 0 failed (323.7s) |
| `npm test` (vitest) | ph01_npm.txt:110-111 | 29 files, 222 tests, all passed |

The pytest log carries one stderr line from uv about a LangSmith 403 on telemetry upload. It is not a test failure. The warning is the Starlette httpx deprecation.

### Answers to the eight checks

**1. Changed files.** `git diff --name-status test/flag-on-integration...HEAD` lists 20 files. Test files created on this branch (A): `test_intake_readiness.py`, `test_run_detail_readiness.py`, `fixtures/eval_s3b_hygienic.json`, and four frontend tests (`request-screen-refusal`, `request-screen-readiness`, `request-screen-no-client-readiness`, `asset-panel-hygienic`). Modified (M): `conftest.py` is the single-token MAIL_PROVIDER pin at line 74, committed as 439cc9b. `test_hygienic_context.py` is one hunk `@@ -240,3 +240,287 @@`, 287 lines added after line 242, zero removed. No existing assertion changed. Clean.

**2. Is `assess()` the only readiness decision?** Backend: yes on all four named surfaces. Confirm refuses on it at `api_server.py:3218-3220`. The chat reply reads it at `api_server.py:2345`, and "Specs look complete" at `api_server.py:2385` is only reachable when `readiness.ready`. The nameplate reply reads it at `api_server.py:2539`. The run detail exposes it at `api_server.py:1963`, computed on the raw persisted column (`_specs_for_badges`, line 1903), the same column confirm parses. The proc card reads it at `request-screen.tsx:529`. **But the frontend still has a second rule**: `frontend/src/components/gofer/intake/spec-panel.tsx:18-19` decides `showConfirmCard` from `manufacturer || part_number`, and offers a live "Confirm & Source" button. See finding 1.

**3. Card fails closed.** Yes. `request-screen.tsx:529` is `run?.intake_readiness?.ready === true`. Pinned by `request-screen-readiness.test.tsx:93-100` (detail without the field renders "Need a little more", button disabled) and the source scan `request-screen-no-client-readiness.test.ts:26-29`, which asserts that exact assignment is the only `const ready =` in the file.

**4. Table test.** `test_hygienic_context.py:449-457` adds six identity rows: model only, manufacturer only, no identity, each with and without hygienic context. Line 471 asserts `reply.startswith("Specs look complete") == (confirm.status_code == 200)` for every row. Identity rows also assert reason `identity_insufficient` and that every `all_missing_labels` entry appears in the reply.

**5. Frontend.** `confirmIntake` accepts `sourceAnyway` (`api.ts:240`) and emits `source_anyway=true` (`api.ts:251`). `reasoned422` (`request-screen.tsx:99`) routes any 422 with a string `reason` to the card; the card renders the message (`:827`), the Still needed list from `all_missing_labels ?? missing_labels` (`:674`), and Source anyway only when `override === "source_anyway"` (`:675`). The button is `disabled={!acknowledged || sourcingAnyway}` (`:919`) and the handler re-guards at `:727`. The generic toast fires only when `reasoned422` returns null (`:320-322`, `:718`, `:736`). All four behaviours are covered in `request-screen-refusal.test.tsx:104-181`.

**6. N/A and not applicable.** Both unanswered. "N/A" folds into `_NULL_FOLDED` (`hygienic_context.py:105`); "not applicable", "n.a.", "na" are in `_CERT_UNSTATED` (`:122`) and `_stated` skips them at `:257`. `normalise` does not rewrite them. Tested for "N/A", "n/a", "not applicable", "Not applicable", "unknown" at `test_intake_readiness.py:91-97`.

**7. Hygienic override recorded separately.** Yes. `record_hygienic_override` writes `hygienic_override_ack` (`intake_readiness.py:32`, `:123-128`); the identity path writes `spec_incomplete_ack` plus `spec_incomplete` (`intake_sufficiency.py:37-39`, `:116-117`). Confirm records each per gate at `api_server.py:3221-3224`. Tested at `test_intake_readiness.py:178-197`, including both gates on one run. But see finding 2 for what happens downstream.

**8. Counts.** Table above. No failures in either file.

### Findings

1. **MAJOR. A second client-side readiness rule survives on the gofer run page.** `spec-panel.tsx:18-19` shows a "Confirm & Source" card whenever the run is in intake and has a manufacturer or part number. That offers confirm on manufacturer-only specs, which the identity floor refuses, and on any hygienic run missing its answers. It confirms via `useConfirmIntake` (`queries.ts:201-204`), which cannot send `source_anyway`, and on the 422 it prints "Failed to confirm — is the backend running?" (`spec-panel.tsx:160-163`), mislabelling a reasoned refusal as a connectivity failure. The page is routed and linked: `app/runs/[id]/page.tsx:75`, reached from `sidebar-nav.tsx:17`, `runs/page.tsx:124` and `runs/new/page.tsx:61`. This rule predates the branch, but the branch's commit message, `intake_readiness.py:1`, and CLEANUP §5.7 "RESOLVED" all claim one decision, and the source scan test covers only `request-screen.tsx`.

2. **MAJOR. A hygienic-only Source anyway promises a marking that never happens.** The chat hint at `intake_readiness.py:38-40`, the checkbox copy at `request-screen.tsx:913`, and the new `interactions.md` text all say results "will be marked as not checked against your requirement". The banner (`api_server.py:1188`) and the exact-badge cap (`api_server.py:935`, `badge_integrity.py:182`) key only on `spec_incomplete`. `record_hygienic_override` deliberately does not set it (`intake_readiness.py:116-119`), and `test_intake_readiness.py:189` asserts its absence. Nothing outside the tests reads `hygienic_override_ack`. So a run that cleared identity but overrode the hygienic gate sources with no banner and may badge candidates exact. The docstring's reason for not reusing the identity banner is sound, but no replacement marking was built, and CLEANUP.md does not record the gap.

3. **MINOR. The intake agent's own sufficiency is still a second input to the chat, in the restrictive direction.** When `result["sufficient"]` is False for a non-hygienic reason, `api_server.py:2388` replies with the agent's follow-up even if `assess` says ready, so the chat can ask while confirm would return 200. The PH-01 contradiction (chat says complete, confirm refuses) is closed. The converse disagreement is untested: every table row mocks high confidences, so only sufficient-true paths are exercised.

4. **LOW. Trailing-period asymmetry in certification parsing.** `_is_cert_negative` strips a trailing "." (`hygienic_context.py:211-212`) but the `_CERT_UNSTATED` check at `:257` does not, so "Not applicable." falls through to `_is_null`, is not null, and counts as answered, while "Not applicable" does not.

5. **INFO. Duplicated gate call.** `intake_agent.py:939` calls `hygienic_block` directly rather than through `assess`. It cannot disagree because it is the same function and `send_message` re-assesses at `api_server.py:2345`, but it is a second call site to keep in step.

6. **INFO. Round-2 items closed.** Conftest pin committed (439cc9b). "Sourcing by category" removed (`api_server.py:2382-2385`, tested `test_intake_readiness.py:109-122`). Turn-cap exemption documented as deliberate in `interactions.md`. Panel rows now tested in `asset-panel-hygienic.test.tsx`. `design/interactions.md` and CLEANUP.md updated in the same change.

CHANGES_REQUESTED

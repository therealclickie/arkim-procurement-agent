## PH-01 round 3d review, branch `fix/ph01-hygienic-fields` (7da3244)

**Test results (run by me, MAIL_PROVIDER=fake exported, both run to completion)**

| Suite | Result |
|---|---|
| `uv run pytest -q` | 3313 passed, 2 failed, 73 skipped (154s) |
| `npm test` (vitest) | 33 files, 246 tests, all passed |

The two failures are the same `test_notifications_admin_alerts.py` pair as before. I confirmed yesterday they fail identically on the pre-branch base (0c58151) and the cause is `datetime.now() + 30h` at `test_notifications_admin_alerts.py:154`, which lands on a weekend. Excluded. Note the base branch has since merged this commit (`origin/test/flag-on-integration` is now 6a8fbb5), so a name-status against it is empty; the round-3d checks below compare 7da3244 to its parent.

### Answers to the six checks

**1. Choke point.** `_commit_intake_to_sourcing` calls `ensure_ready_for_sourcing` at `api_server.py:3313` as its first statement, before the `exact_only`/`open_family` writes and the phase mutation at `:3331`. `ensure_ready_for_sourcing` (`intake_readiness.py:156-160`) raises unless `unacknowledged` is ready, and `unacknowledged` (`:139-152`) subtracts only the groups a recorded acknowledgement covers, so a hygienic acknowledgement does not stand in for the identity floor (tested `test_ph01_round3d.py:106-117`). Is it the only path to sourcing? I searched every phase write and every `_run_sourcing_background` reference across `api_server.py`, `utils/`, `scripts/` and `eval/`:
- `Phase.SOURCING.value` is assigned to a persisted run only at `api_server.py:3331`; `_run_sourcing_background` is scheduled only at `:3337`.
- `_transition_run` (`:2823-2832`) is called only with `PENDING_FIRST_APPROVAL` and `APPROVED` (`:2961-2978`). The approval path sets `PENDING_SECOND_APPROVAL`/`APPROVED` (`:3082`); `:1606` constructs an in-memory model, not a DB write.
- `orchestrator/core.py:100,255,406` write phases through `persistence.update_run`, but `api_server.py` does not import the orchestrator and it is off the shipping path.
- `scripts/*.py` build `SourcingRun(current_phase="sourcing")` objects for CLI probes, no DB transition.

The structural test (`test_ph01_round3d.py:247-285`) pins the direct assignment and the task reference. See finding 1 for its one blind spot.

**2. Email consumer.** Both consumer paths now route through `_fire_or_clarify` (`intake_channels.py`), which catches `IntakeNotReady` and replies NEEDS_CLARIFICATION with `missing_labels` from the refused readiness and reason `intake_not_ready`. The firer flushes rather than commits (`api_server.py:3389`) and re-raises `IntakeNotReady` (`:3396-3397`), so the refused run is rolled back. Tested end to end at `test_ph01_round3d.py:136-160` with the probe specs: status NEEDS_CLARIFICATION, no run row, no sourcing started, reply body naming manufacturer and model; the confirmed-sender replay at `:162-181`; a ready email still sources at `:183-196`. In-app confirm is unchanged: `confirm_intake` records both acknowledgements at `api_server.py:3256-3261` before calling the choke point at `:3263`, and `test_in_app_source_anyway_is_unchanged` (`:119-128`) pins 422 then 200.

**3. Pre-existing test files.** `git diff --name-status HEAD~1 HEAD` restricted to tests shows exactly two files, both A: `test_ph01_round3d.py` and `spec-panel-source-anyway.test.tsx`. No pre-existing test file, fixture or conftest was touched. The commit message's claim of no (a) or (b) exceptions is accurate.

**4. Spec panel Source anyway.** `spec-panel.tsx` renders `SourceAnyway` only when in intake, not ready, and `intake_readiness.override === "source_anyway"` (backend field). The button is `disabled={!acknowledged || confirm.isPending}` and the handler re-guards on `acknowledged`; the mutation now passes `sourceAnyway` through `useConfirmIntake` (`queries.ts:206-207`) to `confirmIntake(runId, false, false, true)`. The backend message renders above the Still needed list. Tested at `spec-panel-source-anyway.test.tsx:58-79` (disabled click sends nothing, ticked click sends `source_anyway=true`) and `:81-94` (not offered without override, when ready, or outside intake). The run detail carries `message` and `override` only when not ready (`api_server.py:1905-1911`, tested `test_ph01_round3d.py:333-339`), so the ready payload is unchanged.

**5. Nameplate reply.** Branch (b) at `api_server.py:2576-2588` now uses `ready_reply(follow_up)` when `assess(specs).ready`, else the old wording. Tested both ways at `test_ph01_round3d.py:304-325`.

**6. Counts.** Table above.

### Findings

1. **INFO. The structural test would not see a future `_transition_run(run, Phase.SOURCING)`.** `_sets_phase_to_sourcing` (`test_ph01_round3d.py:234-239`) matches a direct assignment of the sourcing literal; `_transition_run` assigns `target.value` (`api_server.py:2830`), and `phases.py:37` permits INVENTORY→SOURCING. No caller does this today, and such a call would still not schedule `_run_sourcing_background`, which the second test pins. A one-line addition asserting no call passes `Phase.SOURCING` to `_transition_run` would close it.

2. **INFO. Interactions doc and consumer docstrings match the code.** `design/interactions.md` gains the nameplate rows, the choke-point paragraph and the run-page Source anyway paragraph; `intake_channels.py` docstrings no longer claim "same gates" for a path that skipped them.

APPROVED
Every check has run. Both suites are green and all evidence is collected, so here is the review.

## PH-01 re-review, branch fix/ph01-hygienic-fields (6bab036 → d6bca1d)

**Test results (run by me, MAIL_PROVIDER=fake exported)**

| Suite | Result |
|---|---|
| `uv run pytest -q` | 3234 passed, 73 skipped, 0 failed (296s) |
| `npm test` (frontend) | 25 files, 205 passed |

The single failure the first review saw is gone. The working-tree conftest change pins MAIL_PROVIDER in the flag list at `utils/procurement_agent/tests/conftest.py:74`. That edit is uncommitted.

**Answers to the eight checks**

1. **Changed files.** `git diff --name-status test/flag-on-integration...HEAD` shows one test file with M: `test_hygienic_context.py`. Both commits only appended to it (6bab036: 101 added, 0 removed; d6bca1d: hunk `@@ -341,3 +341,156 @@`). No existing assertion changed. The conftest pin is in the working tree only, not in the branch diff. The other M files are source, types, panel and interactions doc. One A: the fixture. Clean.

2. **One completeness decision: no.** The hygienic half is now one check. The intake agent runs the gate's own function on the specs it persists at `intake_agent.py:939-945`, and the chat reply is the 422 message text. But the identity floor from arc 5 is still confirm-only. `intake_sufficiency.identity_block` is called at `api_server.py:3168` and nowhere in the intake agent. The chat's reply at `api_server.py:2321-2354` still keys off the agent's own sufficiency. I probed this through send_message and confirm-intake with the branch's own helpers:

   | Specs | Chat reply | Confirm |
   |---|---|---|
   | model 1032, no manufacturer, hygienic answered | "Specs look complete — review in the panel and confirm" | 422 identity_insufficient |
   | manufacturer only, no model or PN | "Sourcing by category — we have enough specs..." | 422 identity_insufficient |
   | no identity, no hygienic context | "Sourcing by category — we have enough specs..." | 422 identity_insufficient |

   The first row is the literal PH-01 contradiction, moved from the hygienic gate to the identity gate. The "Sourcing by category" branch at `api_server.py:2350` is the pre-arc-5 spec-based wording and now invites a confirm that always refuses.

3. **The 12-row table** at `test_hygienic_context.py:421-458` builds every row from the eval's step-3 specs, which carry Ashcroft plus 1032. All 12 rows are identity-sufficient. Eleven vary hygienic fields and one removes hygienic context. No identity-insufficient row exists, which is why the table did not catch item 2.

4. **Fixture.** `eval_s3b_hygienic.json` is not the same file as `s3b_step3_answered.json`, so it cannot be byte-identical. It is a composite with a different top-level shape. I compared the payloads programmatically: `step3_specs` equals the evidence `specs` object exactly, `step1_specs` equals the step-1 evidence, both replies, the 422 status and `missing_attrs` match, and both user messages match the eval script's string literals (split across lines at script lines 40-44 and 85-86). The content is faithful.

5. **Normaliser.** It runs in the intake agent on the raw extraction before the merge at `intake_agent.py:803` and on the merged specs at `:810`. The gate itself does not call `normalise`; instead `_answered` at `hygienic_context.py:253-258` derives the type read-only and `_stated` at `:242-250` accepts a certification negative without rewriting. Overwrite guard: `normalise` at `:221-237` only sets `process_connection` when `_stated` is false, and the test at `:487` holds it. The one rewrite it does make is a negative certification to "not required", which is a canonicalisation of an equivalent answer, not an overwrite of a different value.

6. **Certification tokens.** "none", "None", "no", "not needed" are in `_CERT_NEGATIVES` at `hygienic_context.py:113-118` and are answered. "N/A" and "unknown" fold into `_NULL_FOLDED` at `:105` and stay unanswered, tested at `:471-476`. One wrinkle: "not applicable" is in the negatives set at `:116` while "N/A", its abbreviation, is a null. Those two spellings of the same thing land on opposite sides of the gate.

7. **Unbounded questioning.** The hygienic override at `intake_agent.py:939-945` fires after the `if not sufficient` block at `:922`, so `_next_clarification` never runs for it and `_intake_turns` is never incremented. The cap at `INTAKE_TURN_CAP` does not apply. In practice a buyer who answers is released, because the extractor schema now has the fields and the normaliser derives the type. A buyer who keeps answering in a way the extractor does not capture loops with the same question. The question text at `hygienic_context.py:280` does not mention an override. The 422 detail carries `"override": "source_anyway"`, but the frontend never sends it: `confirmIntake` in `frontend/src/lib/api.ts:236-247` has only `exact_only` and `open_family`, and the 422 handler at `request-screen.tsx:288-296` recognises only `family_variant_unconfirmed`. Every other 422, hygienic or identity, becomes the toast "Couldn't start sourcing — please try again." So in the shipping UI there is no visible way out besides answering. This is arc 5 debt, not introduced here, but the branch's fix now depends on it.

8. Counts above.

**Findings**

1. **MAJOR. The chat and the confirm gate are still two decisions for the identity floor.** The branch unified the hygienic check only. A model-only run gets "Specs look complete" from `api_server.py:2354` and 422 `identity_insufficient` from `api_server.py:3168-3170`. Probe evidence above. The commit message claims "one check on the same specs"; that is true for hygienic and false for identity.

2. **MAJOR. The panel-equals-gate table has no identity-insufficient rows.** `test_hygienic_context.py:421-458`. As written it is a hygienic table, and it passes while finding 1 is live. The table needs at least model-only, manufacturer-only and no-identity rows to be the contract the docstring describes.

3. **MINOR. The "Sourcing by category" reply is now dead-wrong copy.** `api_server.py:2348-2352` tells the buyer "we have enough specs... no specific part number or model is required" on exactly the specs the arc-5 floor refuses. Pre-existing text, but arc 5 inverted its meaning and nobody updated it.

4. **MINOR. The hygienic ask has no override path in the UI.** `frontend/src/lib/api.ts:236-247` cannot send `source_anyway`; `request-screen.tsx:288-296` toasts on every non-variant 422. The gate is answerable now, so this is not a hard loop, but the `override` key the backend advertises is unreachable from the shipping front end. Arc 5 debt; flag in CLEANUP.md.

5. **LOW. The hygienic re-ask is outside the turn cap.** `intake_agent.py:939-945` bypasses `_intake_turns`. Acceptable given the fields are now fillable, but it should be a deliberate, documented choice in `design/interactions.md`, which currently describes the cap as the loop guard.

6. **LOW. "not applicable" is an answer, "N/A" is not.** `hygienic_context.py:116` versus `:105`. Pick one reading.

7. **LOW. The conftest MAIL_PROVIDER pin is uncommitted.** `conftest.py:74` is modified in the working tree only. The green run above depends on it, or on the exported variable.

8. **INFO.** No frontend test covers the three new panel rows from 6bab036. Carried from the first review, still open.

CHANGES_REQUESTED

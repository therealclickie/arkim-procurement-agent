Review complete. Both suites ran and the call sites are traced.

**Test results**

| Suite | Result |
|---|---|
| `uv run pytest -q` | 3208 passed, 1 failed, 73 skipped |
| `npm test` (frontend) | 25 files, 205 passed |

The one pytest failure is `test_mail_provider.py::test_flag_on_selects_ses_by_default`. My shell has `MAIL_PROVIDER=fake` exported, which overrides the test's SES default. With that variable unset the file passes 18 of 18. The test and the mail-provider module are untouched on this branch, so this is environmental, not a regression.

**Findings**

1. **MAJOR. The chat completeness message and the confirm gate are two independent checks.** The reply "Specs look complete" at `api_server.py:2354` is chosen from `result["sufficient"]`, the intake agent's own sufficiency verdict. The gate at `api_server.py:3182` calls `hygienic_context.hygienic_block`. The intake agent never references the hygienic module. Its only "hygienic" lines are prompt text at `intake_agent.py:570`, `601` and `608`. So the contradiction PH-01 named is still reachable: a partial answer such as "Tri-Clamp, 316L" makes the extractor sufficient, the panel says complete, and confirm returns 422 for the certification. The fix addressed the un-fillable-fields half of PH-01 and left the panel-versus-gate half untouched.

2. **MAJOR. The fix is prompt-only, and the end-to-end test mocks the extractor to return exactly what the fix needs.** `_ANSWER_EXTRACTION` at `test_hygienic_context.py:259` is hand-authored, not copied from the evidence. The evidence specs had `connection_size: "1.5 inch Tri-Clamp"`, `material_spec: "316L stainless steel wetted parts"`, plus psi, use_case and quantity, and no `process_connection` or `hygienic_certification`. The merge at `intake_agent.py:801` already passed any non-null key through before this branch, so the test proves pass-through, not that the model now splits the answer. No live re-run of the s3b probe is in the commit. `_ANSWER_TEXT` at line 251 does match the eval script verbatim.

3. **MINOR. There is no normaliser.** The merge at `intake_agent.py:801` is "new non-null values win", so extracted values overwrite prior ones. Nothing derives `process_connection` from an existing `connection_size` like "1.5 inch Tri-Clamp". A run persisted before this fix, or any turn where the model ignores the split instruction, stays blocked until the buyer re-answers.

4. **MINOR. No panel-equals-gate table test exists.** Per-field coverage is `test_an_answered_field_is_not_re_asked` at line 135 (one field missing) and `test_a_null_token_does_not_count_as_answered` at line 148 (all missing). The no-hygienic-context case is `test_the_same_gauge_with_no_hygienic_context_confirms` at line 220. All three exercise the gate only. Such a test cannot be written until finding 1 is resolved.

5. **LOW. A pre-existing test file was modified.** `test_hygienic_context.py` has 101 lines added and 0 removed, all appended after line 242. No existing assertion changed. The other six files changed are source, types, panel and interactions doc.

6. **LOW. The "none" answer is handled only by prompt text.** The gate's own label at `hygienic_context.py:78` offers "3-A / EHEDG / none", but `_NULL_VALUES` at `intake_agent.py:62` reads "none" as unanswered. The fix tells the model to write "not required" instead. No test sends a user typing "none", and a capitalised "None" would pass as answered.

7. **INFO.** The frontend change adds `connection_size`, `process_connection` and `hygienic_certification` to the types and three panel rows. No frontend test covers the new rows.

CHANGES_REQUESTED

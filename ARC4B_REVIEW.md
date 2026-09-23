# ARC 4b — REVIEW

**Reviewer:** Claude Fable 5 · **Date:** 2026-09-23 · **Branch:** `arc4b/flag-on-rulings`
**Diff base:** `5b0b559f160636ebf2f3056ebae497783546a2d0` (the arc's branch point, used for every diff below)
**Verdict: APPROVED** (findings below are MINOR; one item needs a human ruling before merge — Finding 1)

Both suites were run by the reviewer on this branch, flags off (conftest pins them):

| Suite | Command | Observed |
|---|---|---|
| Backend | `uv run pytest -q` | **2966 passed, 73 skipped** (257s) — matches the report |
| Frontend | `cd frontend && npm test` | **200 passed, 24 files** (report says 25 files — see Finding 3) |
| Volume scenario | `uv run pytest utils/procurement_agent/tests/test_notifications_volume_scenario.py -v` | **6/6 passed** (run separately by the reviewer for R9) |

---

## R1 — every `M` test file is in the exception table: PASS

`git diff --name-status 5b0b559..HEAD` shows exactly five pre-existing test files with `M`:

```
M  frontend/src/app/supplier/verify/__tests__/verify-screen.test.tsx
M  utils/procurement_agent/tests/test_notifications_admin_alerts.py
M  utils/procurement_agent/tests/test_notifications_escalation.py
M  utils/procurement_agent/tests/test_notifications_governance.py
M  utils/procurement_agent/tests/test_notifications_rfq_new.py
```

All five are in the PRIME DIRECTIVE exception table. `conftest.py` (the sixth authorised file) is untouched — correct, since T5 was already satisfied by commit `442667e` (verified: `conftest.py:74` contains `"NOTIFICATIONS_V1"`). No other pre-existing test file, backend or frontend, appears with `M`. Every other `M` is source, not tests (`api_server.py`, `utils/*`, frontend source, `design/interactions.md`, `scripts/notifications_scheduler.py`).

## R1b — the four arc-4 test files, read line by line: PASS

- **`test_notifications_governance.py`** — only `test_the_daily_cap_is_respected_by_notification_mail` (the gate's `:88-99`) changed: `STATE_SUPPRESSED`/empty-outbox → `STATE_SENT`/one mail, with a SUPERSEDED docstring citing R-F8. The cap invariant survives in tested form: the new `test_notification_mail_is_suppressed_by_its_OWN_cap_and_never_sent_past_it` (now `:112-127`) exhausts `NOTIFICATION_DAILY_CAP=1` and asserts suppression past it. Every other assertion in the file is byte-identical. No deletion without replacement.
- **`test_notifications_rfq_new.py`** — only the fan-out test's two assertions changed (recipients list drops `staff@dxpe.com`; outbox 2→1), per S1. The immediate-`STATE_SENT` assertions at `:65`/`:140` that the fence also authorised were NOT touched — they still hold under the shipped anchor-first coalescing (see Finding 1). Everything else byte-identical.
- **`test_notifications_admin_alerts.py`** — only `test_every_alert_kind_the_arc_raises_is_listed` changed: `count == 4` → `count == 3`, `SOFT_BOUNCE_REPEATED` removed from the queue set, and REPLACED by an assertion proving the fourth kind lands in the DIGEST tier (`list_alerts(tiers=(TIER_DIGEST,))`) — tiered away, not lost. Everything else byte-identical.
- **`test_notifications_escalation.py`** — git renders it as a full-file rewrite; compared old-vs-new hunk by hunk. Only the clock changed: `NOW` moved to a fixed Tuesday-in-business-hours instant, ages are expressed via `business_hours.business_hours_before(NOW, h)`, and every `run_escalations()` call now passes an explicit instant. The 21-row decision table (labels, kwargs, expected outcomes), the open-is-not-seen test, the threshold-config test, and every scheduler assertion are otherwise byte-identical. The named invariants all survive in tested form: one-reminder-once-and-only-once (`test_a_reminder_is_sent_once_and_only_once`), alert-once (`test_the_alert_is_raised_once_...`), same-`now` idempotency (`test_running_twice_with_the_same_now_changes_nothing`). The widening beyond the gate's `:156-253` (the `NOW` constant, `notification_row`, the two CLI tests) is exactly the same superseded S3 decision and was **reported, not silently widened** (report FINDING F2), as the fence requires. One incidental parsing change (`splitlines()[-1]` in the `--json` CLI test) is disclosed there too — see Finding 4. The AST-ban test additionally now covers `business_hours.py` (a strengthening).

## R2 — `verify-screen.test.tsx` line by line: PASS

No security assertion weakened; the diff is purely additive plus a `clickContinue()` gesture inserted before each read. Verified in the current file:
- Uniform-rejection equality is still **Set-size-1** (`:172-178` four failure tokens; `:193-198` divergent backend shapes) with the **contrast case** intact (`:225` — `Set([rejected, succeeded]).size == 2`).
- The storage/console sweep after success and failure is untouched.
- **NEW zero-requests-on-render test** exists: `makes ZERO requests on render, and exchanges only after the click` asserts `calls.toHaveLength(0)` after render and settle, then exactly one POST after the gesture (success criterion 6).
- Two extra additive tests strengthen the surface: the idle screen is identical with and without a token (no pre-exchange oracle), and the token never renders on the idle screen.
- Implementation confirmed (`verify-screen.tsx:75-95`): the POST lives only in `onContinue`; there is no `useEffect`; a `started` ref guards double-submit; missing token → same rejection.

## R3 — the notification-cap test uses the REAL gate: PASS

`test_notifications_governance.py`'s `gov` fixture sets `SEND_GOVERNANCE_V1=1` against a real tmp-path governance store; the only double is `FakeProvider`, which sits **below** governance (file docstring `:4-9`). The reworked test exhausts the RFQ cap with a real rfq-class `record_sent_message` row under `SEND_GOVERNANCE_DAILY_CAP=1` and then sends a notification through `_send_notification_mail` → `GmailSender().send` → the real `send_governance` stack, asserting `STATE_SENT`. Nothing about caps or the allowlist is mocked.

## R4 — a VERIFIED webhook event bypasses the rejection limiter: PASS (proven by test, not prose)

`test_ses_webhook_reject_throttle.py::test_a_verified_event_from_a_throttled_ip_still_succeeds` (`:136-153`): trips the limiter from IP_A (`_webhook_reject_throttled(IP_A) is True`), then posts a **really signed** envelope (real RSA signature; only `fetch_certificate_pem` is stubbed) from the same IP in the same window, and asserts `200`, `{"ok": True}`, **and that the event was APPLIED** — the notification transitions to `STATE_DELIVERED`. The companion test (`:156-168`) proves verified traffic never consumes the budget. Implementation confirms it structurally (`api_server.py:7664-7672`): `_webhook_reject_bump` is called only inside the `outcome is None` branch, so a verified event never touches the bucket.

## R5 — the throttled 403 is not an oracle: PASS

`test_the_throttled_403_is_byte_identical_to_every_other_rejection` (`:175-197`) compares `(status, body bytes, cache-control, referrer-policy, retry-after)` across within-budget and over-budget rejections of four different failure shapes, asserts the set has **size 1**, and pins `retry-after is None`. The route raises the same bare `HTTPException(403, "Forbidden")` on every rejection path (`api_server.py:7672`); the pre-existing uniform-403 test in `test_ses_webhook.py` is untouched.

## R6 — invite configuration set read from the Stubber-captured call: PASS

`test_notifications_invite_governance.py:95-102` reads `captured[0]["ConfigurationSetName"]` out of a botocore-`Stubber`-verified `send_email` call and compares it to the env value **the test set**, with a contrast assertion against the notifications set. The pre-existing arc-4 pins (`test_notifications_auth_mail.py:246-262`, `:274-280`) are untouched.

## R7 — both suites, flags OFF: PASS

Conftest pins every flag off; the runs above (2966 / 200, all green) are therefore the flags-off runs. Each new test file carries its own `test_flag_off_*` case (verified in the throttle, ceiling, and volume files).

## R8 — FINDINGS genuine: PASS

Not task-avoidance. F1 discloses a real deviation from S2's literal wording and asks for a ruling rather than burying it (see Finding 1). F2 reports the fence widening with line numbers as the brief demands. F3 is a real bug found and fixed (wall-clock `sent_at` vs supplied `now` in the send path — the exact bug class the brief cites). F5 is a real design decision (no `supplier_domain` on notification ledger rows so they can't masquerade as RFQs in the portal), pinned by tests.

## R9 — the volume scenario, run by the reviewer: PASS

Ran `test_notifications_volume_scenario.py` myself: **6/6 green**. Supplier emails 60 → 10; concierge queue rows 50 → 2, on identical inputs, both columns driven through the same code (the arc-4 column restored by configuration, and honestly documented as an under-count of true arc-4 behaviour since consolidation and per-account aggregation are not configurable). The reduction is **not a silent drop**: `test_the_reduction_never_drops_a_notification` asserts every one of the ten requests ends SENT, deferred-to-digest, or cancelled-because-resolved; `test_every_remaining_email_is_actionable_by_its_recipient` asserts every surviving mail goes to a designated contact, names quote-request work, and carries a portal link; `test_the_weekend_produces_nothing_at_all` pins the S3 headline. The ceiling code path defers (`deferred=1`, delivered by the digest — `test_the_sixth_notification_in_a_day_is_deferred_not_sent`, `test_the_deferred_sixth_is_delivered_by_the_digest`); nothing is discarded.

## R10 — no wall-clock reads inside T8/T10/T11 calculations: PASS

Grepped `datetime.now(`, `time.time(`, `date.today(`, `.today()` across `utils/business_hours.py`, `utils/notification_metrics.py`, `utils/notifications.py`, `utils/notifications_store.py`:
- `business_hours.py` — zero hits in code (one docstring mention). It also ships its own AST self-test (`test_business_hours.py::test_no_business_hours_calculation_reads_the_wall_clock`) plus `test_the_same_inputs_give_the_same_answer_on_any_calendar_date`.
- `notification_metrics.py` — zero hits; `actionability(now, ...)` takes the instant.
- `notifications.py:154` `_now()` — used only as `now or _now()` defaults at scheduler/fan-out **entry points** (`:432`, `:494`, `:638`, `:1090`, `:1117`, `:1333`, `:1424`, `:1442`); the calculations receive the resolved `moment`. This is the correct pattern (a cron entry point must be callable without an argument).
- `notifications_store.py:443` `_now()` — pre-existing row-timestamp helper, unchanged semantics.
- `time.monotonic()` in the webhook limiter is a rate-window measurement, not a calendar calculation — correct tool.

One marginal case outside R10's named scope, recorded as Finding 2.

## R11 — no path still sends one email per RFQ per member where S2 says coalesce: PASS (with the disclosed S2 deviation)

With shipped defaults, a burst produces an anchor mail plus one batch mail per mailbox (verified live in the volume run: six Friday RFQs → 2 emails per contact, not 6). Reminders are consolidated per mailbox per business day. The residual deviation from S2's literal "three RFQs → one email" is the anchor-first compromise — Finding 1, disclosed by the builder, not hidden.

---

## FINDINGS

1. **MINOR (needs a human ruling before/at merge) — S2 coalescing is "anchor + batch", not "hold them all".** `utils/notifications.py` (RFQ_NEW fan-out + `run_coalesced_sends`); report FINDING F1; brief S2. Three RFQs arriving together to an idle mailbox produce **two** emails (immediate anchor + one batch listing the rest), not the one S2's wording asks for. Full holding is blocked by two pre-existing test files **outside** the G-STOP-1 fence (`test_notifications_preferences.py:142-147`, `:238-244`, asserting `STATE_SENT` immediately). The builder correctly refused to widen the fence silently and disclosed the compromise, which is also independently defensible (a line-down part should not wait 15 minutes). Accept as shipped, or extend the exception to those two files in a follow-up — that is the merger's call, not the builder's or mine.
2. **MINOR — `_expired` reads the wall clock inside the S4 resolution judgement.** `utils/notifications.py:1066-1072` (`parsed <= _now()`), reached from `rfq_resolution` under `cancel_resolved(moment)`. T9 scope, so outside R10's named T8/T10/T11 fence, and the failure mode is benign (a quote-token expiry judged against real time rather than the supplied instant — drift only matters in simulation, and `quote_tokens` owns the authoritative judgement). Should take the caller's `moment` for consistency with everything around it.
3. **MINOR — report inaccuracy: frontend file count.** `ARC4B_REPORT.md` says "200 passed (25 files)"; the observed run is **200 passed in 24 files** (the three new tests live in the existing `verify-screen.test.tsx`, and no new frontend test file exists in the diff). Counts of tests are right; the file count is not.
4. **MINOR — `notifications_scheduler.py --json` stdout is no longer pure JSON.** The S4 cancellation sweep touches the supplier registry, which prints while creating its database; the CLI test now parses the **last** stdout line (`test_notifications_escalation.py`, disclosed in report F2). A cron consumer piping `--json` output to a parser must do the same. Worth silencing the registry print or routing it to stderr in a follow-up.
5. **MINOR (recorded so nobody over-reads it) — the webhook "throttle" enforces nothing in-process.** `api_server.py:7599-7620`: the limiter counts rejections and exposes `_webhook_reject_throttled` as an observation seam, but a throttled IP's next bad request is processed (verified and refused) exactly like its first — the bump's return value is deliberately unused. This is the only implementation R-F10 permits (shedding verification work would drop verified events; a distinguishable response would be an oracle), and both the code comment (`:7581-7586`) and report F4 say so plainly. Its value is the counted per-IP abuse signal for an upstream WAF/ALB rule — do not expect it to reduce load by itself.
6. **MINOR — report narrative detail: the two consolidated reminders fire on Monday, not Tuesday.** `ARC4B_REPORT.md` (volume section) describes the ten arc-4b emails as "…and 2 consolidated reminders on Tuesday". By the business-hour arithmetic the code implements, the Friday batch is 4.75 business hours old at the Monday 09:45 escalation run (4 bh Friday + 0.75 bh Monday), so the consolidated reminders go out **Monday morning**; the Tuesday run escalates the Monday RFQs straight to the queue (8 bh ≥ the 8 bh alert threshold — "late first pass alerts, not reminds"). The counts (10 emails, 2 queue rows) are correct and reviewer-verified; only the day label in the prose is wrong.

## Checklist verdicts

R1 PASS · R1b PASS · R2 PASS · R3 PASS · R4 PASS · R5 PASS · R6 PASS · R7 PASS (2966 backend / 200 frontend, flags off) · R8 PASS · R9 PASS (run live: 60→10, 50→2, no drops) · R10 PASS · R11 PASS.

No BLOCKER, no MAJOR. The prime-directive fence was respected precisely: superseded assertions replaced, invariants re-pinned, widenings reported. **APPROVED**, with Finding 1 flagged for the human merger's ruling.

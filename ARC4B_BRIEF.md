# BUILD BRIEF — Arc 4b: Flag-On Rulings

**Arc:** 4b (small, follow-up to arc 4). **Type:** backend + one frontend behaviour change.
**Branch:** cut from `test/flag-on-integration` (currently 2808 backend / 197 frontend).
**Builder:** Claude Opus 5. **Reviewer:** Claude Fable 5. **Merge:** human only.
**Flags:** `NOTIFICATIONS_V1`, `SUPPLIER_ACCOUNTS_V1`, `NEXT_PUBLIC_SUPPLIER_SESSION_V1` — all
remain default OFF.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

Arc 4 shipped approved with four items explicitly carried to a human flag-on checklist. Three
have now been ruled, and all of them touch what the first real supplier experiences. This arc
implements the rulings so that nothing on the checklist blocks turning the flags on.

---

## THE RULINGS (settled — implement, do not relitigate)

**R-F8. Notification mail gets its own governance class and cap, and a cap-block alerts.**
`utils/notifications.py` sets no `message_class`, so `send_governance.py` defaults it to `rfq`
(the absent-class default). Consequences today: notification mail competes for the RFQ daily cap,
and once that budget is spent notifications go `SUPPRESSED` while the supplier is never told about
RFQs already in their inbox — the exact failure the arc exists to prevent. It also writes no
`sent_messages` row, so notification volume is invisible to the ledger.

Ruling: introduce `message_class = "notification"` with its own configurable daily cap
(`NOTIFICATION_DAILY_CAP`, default generous — pilot volume is a handful per day), write
`sent_messages` rows for notification mail, and **record a `NOTIFICATION_CAP_BLOCKED` concierge
alert in the DIGEST tier (see S6) whenever a notification is suppressed by the cap**, deduped per
day. It is informational, not an interruption: a sensibly sized cap should almost never fire, and
when it does the fix is a config change, not a same-hour response. Rationale for the
separate bucket: a notification goes to an allowlisted, opted-in member about a message already
sent to them; it is not cold outbound and must not be starved by cold outbound.

**R-F10. The webhook throttles its rejection path only; verified events are never throttled.**
Per-IP throttling of all webhook traffic is wrong here: SNS delivers from AWS ranges, so it risks
dropping legitimate bursts, and **a dropped verified event is permanent data loss** — SNS retry
policy is finite and a missed Delivery/Bounce leaves a notification permanently in the wrong
state.

Ruling: rate-limit only requests that FAIL the topic allowlist or the signature check, keyed on
source IP (`WEBHOOK_REJECT_RATE_LIMIT`, default 60 per IP per minute). A request that passes
verification is processed regardless of rate. The throttled response is the SAME uniform 403 as
every other rejection — the limiter must not become an oracle that distinguishes "rate-limited"
from "bad signature".

**R-F1. The verify page requires an explicit click; it must not POST on load.**
`verify-screen.tsx` currently POSTs the magic-link token when the page loads. Corporate link
scanners (Microsoft Safe Links and equivalents) pre-fetch URLs on the *recipient* side, so a
scanner consumes the single-use token and the human then sees the uniform rejection. D2's
tracking-off configuration set does not address this — the scanning happens at the recipient's
mail gateway, not at SES.

Ruling: the page renders a "Continue to sign in" control and POSTs only on user gesture. All
existing security properties are preserved: token never stored, never logged, uniform rejection
across all failure modes.

**R-F5. Invite mail is governed like auth mail, plus a per-account limit, and names the inviter.**
`MEMBER_INVITE` is outward mail from Gofer's domain to a person who has never heard of Gofer.
Ruling: route it through the governance ledger like auth mail (R-F8's sibling: it writes a
`sent_messages` row), send it on the tracking-off `gofer-auth` configuration set, rate-limit
invites per account (`INVITE_DAILY_CAP_PER_ACCOUNT`, default 10/day), and the copy must name the
inviting member and their company so the recipient can tell it is not spam.

---

## SIGNAL DISCIPLINE (settled — the governing principle for every notification in the system)

A notification the recipient can't act on is noise, and noise trains people to ignore the signal.
The supplier promise ("never miss an RFQ") and the concierge queue both fail if either is flooded.
Arc 4 as built over-alerts in five identifiable ways; this section corrects them.

**The rule every task below implements: every notification must be actionable by the person who
receives it, and every signal has exactly one owner.**

**S1. One owner, not a broadcast.** RFQ_NEW goes to the account's designated RFQ contacts, not to
every member holding `VIEW_REQUESTS`. Default designation: OWNER and ADMIN members. Other members
receive RFQ mail only if they opt in. Rationale: broadcasting to five reps sends five emails and
diffuses ownership so each assumes another has it. (Product default; reversible later without a
schema change — the designation is a per-member flag.)

**S2. Coalesce, never repeat per item.**
- RFQ_NEW is coalesced per member over a `NOTIFY_COALESCE_MINUTES` window (default 15): three
  RFQs arriving together produce one email listing three.
- RFQ_REMINDER is consolidated to **one reminder per member per business day** listing every
  unseen RFQ, replacing arc 4's one-reminder-per-RFQ.

**S3. Business hours, in the supplier's timezone.** Arc 4 deferred this; that was wrong for a
supplier-facing product. Wall-clock timers guarantee a weekly false-alarm pattern: every Friday
afternoon RFQ escalates to the concierge on Monday morning, and 4h reminders fire overnight.
- Each account carries a timezone (default `America/Los_Angeles`, since v1 is California-only).
- Reminders send only within business hours (default 08:00–17:00 local, Mon–Fri).
- The reminder and escalation clocks count **business hours only**: reminder after 4 business
  hours unseen, escalation after 8 business hours (one business day).
- US federal holidays excluded (a static list is fine; no dependency needed).

**S4. Stop when resolved.** When an RFQ is closed, awarded, cancelled, or the buyer's quote target
is met, cancel every pending reminder and escalation for it. Escalating a supplier's silence on an
RFQ the buyer no longer needs is pure noise.

**S5. A hard daily ceiling per recipient.** No member receives more than
`MEMBER_DAILY_NOTIFICATION_CEILING` notification emails in a business day (default 5), regardless
of kind. Beyond the ceiling, items roll into that member's next digest. Auth mail and invites are
exempt (they are requested by the recipient or their colleague, not pushed by us).

**S6. Concierge alerts are tiered, and only the top tier interrupts.**
- **ACTION_NOW** — requires a human today: hard bounce or complaint on an account's *only*
  notifiable contact; an account with a live RFQ and zero notifiable members.
- **QUEUE** — normal escalation work: supplier unresponsive past one business day on an RFQ that
  still needs quotes. Escalations are **per supplier account per day**, not per RFQ.
- **DIGEST** — informational, delivered once daily: soft-bounce streaks, notification-cap blocks
  (R-F8's alert lands HERE, not in the queue), a hard bounce on a non-sole contact.

**S7. Make fatigue measurable.** Opinion won't tell us whether a notification kind is useful;
behaviour will. For each notification kind, record sent → delivered → viewed-in-portal within
one business day → quoted within the RFQ window. Surface an actionability rate per kind in the
admin view. A kind whose actionability rate falls below `NOTIFY_ACTIONABILITY_FLOOR` (default
20%) over a rolling 30 days is flagged for review. This is the only honest defence against
over-alerting: you cannot reason your way to the right volume in advance, you have to measure it.

---

## GATE RULINGS

**G-STOP-1 (raised by the gate, 2026-09-23) — RULED: option (b), fenced.**
The gate found that T1, T6, T7, T8 and T10 require behaviour whose opposite is pinned by four
arc-4 test files, while the brief authorised editing only `verify-screen.test.tsx`. That was a
defect in the brief, not in the gate.

Ruling: extend the prime-directive exception to the four arc-4 files named below, under the
same fence as `verify-screen.test.tsx`. Do NOT introduce a second flag (option (a)). Reasons,
for the record:
- Arc 4's notification behaviour has never been live — both flags default off and no supplier has
  received a notification. A second flag would not preserve production behaviour; it would
  preserve a regime that has been explicitly ruled wrong.
- A second flag leaves permanent dual code paths in every notification send, and makes
  `NOTIFICATIONS_V1=on, NOTIFY_SIGNAL_V2=off` a valid configuration that ships exactly the
  over-alerting behaviour Signal Discipline exists to prevent.
- The prime directive forbids editing tests to *hide a regression*. It does not freeze
  superseded decisions: when specified behaviour is deliberately changed by ruling, the tests
  pinning the old behaviour must change with it.

The resulting behaviour is the only behaviour under `NOTIFICATIONS_V1`.

---

## PRIME DIRECTIVE — WITH NARROW, NAMED EXCEPTIONS

1. **No pre-existing test file may be modified, EXCEPT the files listed here, and only as
   fenced below.**

   | File | Superseded behaviour it pins | Ruling that supersedes it |
   |---|---|---|
   | `frontend/src/app/supplier/verify/__tests__/verify-screen.test.tsx` | token POSTed on page load | R-F1 |
   | `utils/procurement_agent/tests/test_notifications_governance.py` (`:88-99`) | notification mail judged by the RFQ cap | R-F8 |
   | `utils/procurement_agent/tests/test_notifications_rfq_new.py` (`:57-66`, `:65`, `:140`) | every ACTIVE member receives RFQ_NEW; RFQ_NEW sent immediately | S1, S2 |
   | `utils/procurement_agent/tests/test_notifications_admin_alerts.py` (`:100-116`) | SOFT_BOUNCE_REPEATED lands in the admin queue | S6 |
   | `utils/procurement_agent/tests/test_notifications_escalation.py` (`:156-253`) | reminders on wall-clock hours; one reminder per RFQ | S2, S3 |
   | `utils/procurement_agent/tests/conftest.py` | — (already pinned during cleanup; see T5) | — |

   **The fence, applying to every file above:**
   - ONLY assertions encoding the superseded behaviour in the right-hand column may change. Line
     ranges are those the gate reported; if the true extent differs, the report must say so with
     `file:line`, not silently widen the edit.
   - Each changed assertion must be **replaced** by an assertion pinning the NEW behaviour. Deleting
     an assertion without a replacement is a violation.
   - Every other assertion in each file stays byte-identical.
   - Invariants survive in their new form and must still be tested: in the escalation file, the
     one-reminder guarantee (now: an RFQ never appears in two reminders), alert-once, and
     same-`now` idempotency; in the governance file, that a notification is suppressed by a cap
     and never sent past it (now the notification cap).
   - For `verify-screen.test.tsx`: security assertions are not weakened — uniform-rejection
     equality (Set-size-1 with contrast case), no token in storage/console — and a NEW test pins
     zero requests on render.

   Every other pre-existing test file, frontend and backend, stays untouched.
2. Flags off = today's behaviour exactly.
3. No live AWS in tests (Stubber / FakeProvider only).
4. Nothing bypasses send governance.

---

## INVESTIGATION GATE (read-only; report with `file:line` before building)

- **H1.** `utils/send_governance.py` — how `message_class` is resolved, where the absent-class
  default to `rfq` lives, how caps are configured and counted, and where a `sent_messages` row is
  written. Identify the minimal additive change for a new class.
- **H2.** `utils/notifications.py` — every send call site that needs the new class; confirm
  whether digest and reminder mail share the path.
- **H3.** The concierge alert mechanism from arc 4 T12 — the kind enum and how dedupe works, so
  `NOTIFICATION_CAP_BLOCKED` reuses it rather than inventing a second alert system.
- **H4.** `utils/ses_webhook.py` + the route in `api_server.py` — exactly where the topic-allowlist
  and signature checks return, so the limiter wraps the rejection path only. Confirm the existing
  uniform-403 helper so the throttled response is byte-identical to it.
- **H5.** Whether any in-process limiter already exists (arc 2 added one for `request-link`) —
  reuse its shape; do not add a second pattern or a dependency.
- **H6.** `verify-screen.tsx` — how the token is read and POSTed today, and which of the 13 tests
  in `verify-screen.test.tsx` depend on POST-on-load. List them by name before changing any.
- **H7.** The invite send path (arc 2 T11 / arc 4 T5) — where it currently sends and which
  configuration set it uses.
- **H8.** `conftest.py:71-81` `_FEATURE_FLAG_ENVS` — confirm `NOTIFICATIONS_V1` is absent (arc 4
  review's recommended follow-up).
- **H9.** Where RFQ lifecycle state lives — closed, awarded, cancelled, and whether a "quote target
  met" concept exists. S4 cancels on these; if "quote target met" does not exist, report it and
  implement S4 for the states that do exist only. Do not invent a quote-target model.
- **H10.** Arc 4's `run_escalations` / `decide_escalation` and the reminder path — exactly what
  changes to move from per-RFQ to per-member-per-day reminders and per-account escalations, and
  whether the existing idempotency and "alert once" guarantees survive the change.
- **H11.** Where an account's timezone could live (account model from arc 2) and whether any
  timezone handling already exists. Use the stdlib `zoneinfo`; no new dependency.
- **H12.** The arc 4 T12 admin alert queue — how severity/tier would be represented, and where a
  per-kind actionability view fits.

**STOP after the gate. Commit the report. Do not begin T1 until it is written.**

---

## BUILD TASKS

### T1. Notification governance class + cap + alert (R-F8)
Add the `notification` message class, its configurable daily cap, `sent_messages` rows for
notification mail, and a `NOTIFICATION_CAP_BLOCKED` concierge alert (deduped per day) when the cap
suppresses a send.
*Tests (new file):* notification mail no longer counts against the `rfq` cap (prove by exhausting
the RFQ cap and sending a notification successfully — through the REAL gate, not a mock); a
ledger row is written; exceeding the notification cap suppresses AND alerts; the alert dedupes
within a day and re-arms the next day.

### T2. Webhook rejection throttle (R-F10)
Rate-limit only requests failing the topic allowlist or signature check, keyed on source IP.
Verified events bypass the limiter entirely.
*Tests:* 61 bad-signature posts from one IP → the 61st is throttled; a VERIFIED event from the
same IP in the same window still succeeds (this is the important one); the throttled response is
byte-identical to the standard uniform 403 including security headers; two different IPs have
independent buckets; the limiter is bypassed when the flag is off.

### T3. Verify page click-to-continue (R-F1)
`verify-screen.tsx` renders a "Continue to sign in" control; POST happens on gesture only.
*Tests:* the NEW pinning test asserting zero requests on render; the existing file updated per the
prime-directive exception — uniform-rejection equality and the storage/console sweep unchanged and
still passing.

### T4. Invite mail governance (R-F5)
Invite mail writes a ledger row, uses the `gofer-auth` (tracking-off) configuration set, is capped
per account per day, and the copy names the inviting member and their company.
*Tests:* configuration set asserted from the Stubber-captured call (not a constant in the code
under test); ledger row written; the 11th invite in a day for one account is refused while another
account is unaffected; the rendered body contains the inviter's name and company.

### T5. Conftest pin — ALREADY DONE
`NOTIFICATIONS_V1` was pinned in `_FEATURE_FLAG_ENVS` during the pre-arc cleanup (commit
`442667e`), as the gate's F6 confirmed. No work; record it as satisfied in the report.

### T6. Designated RFQ contacts (S1)
Per-member `receives_rfq` flag, defaulting true for OWNER/ADMIN and false for MEMBER; RFQ_NEW
fan-out uses it instead of `VIEW_REQUESTS` alone. Expose it in the members screen (arc 3) for
OWNER/ADMIN to set, server-enforced via the existing RBAC matrix.
*Tests:* a five-member account (1 owner, 1 admin, 3 members) produces exactly two RFQ_NEW sends;
a member who opts in receives it; a MEMBER cannot set another member's flag (403 from the server,
not just a hidden control).

### T7. Coalescing and consolidated reminders (S2)
RFQ_NEW coalesced per member over the configurable window; RFQ_REMINDER consolidated to one per
member per business day listing all unseen RFQs.
*Tests:* three RFQs within the window → one email listing three; an RFQ arriving after the window
starts a new batch; three unseen RFQs produce one reminder, not three; the one-reminder-per-RFQ
guarantee from arc 4 still holds in the consolidated form (an RFQ never appears in two reminders).

### T8. Business-hours clocks (S3)
Account timezone (default `America/Los_Angeles`), business-hours window, US federal holidays, and
business-hour arithmetic for the reminder and escalation thresholds. Pure functions over a
supplied `now` — never read the wall clock inside the calculation (arc 4's date-dependent test
bug is the reason; every test passes an explicit instant).
*Tests (table):* an RFQ sent Friday 16:00 PT does not escalate before Monday + remaining business
hours; an RFQ sent 22:00 PT gets no reminder before 12:00 next business day; a holiday is skipped;
a DST transition week is handled; the same inputs produce the same result on any calendar date.

### T9. Cancel on resolution (S4)
When an RFQ reaches closed/awarded/cancelled (or quote-target-met, if H9 found it), pending
reminders and escalations for it are cancelled and it drops out of consolidated reminders.
*Tests:* closing an RFQ between sends removes it from the next reminder; an escalation pending on
a now-awarded RFQ never fires; cancellation is recorded, not silent.

### T10. Daily ceiling + tiered concierge alerts (S5, S6)
Per-member daily notification ceiling with overflow rolled into the digest; concierge alerts carry
a tier (ACTION_NOW / QUEUE / DIGEST); escalations aggregated per supplier account per day; a daily
concierge digest for the DIGEST tier; the admin queue shows ACTION_NOW and QUEUE only.
*Tests:* the 6th notification to a member in a day is deferred to the digest, not sent and not
dropped; auth mail and invites are exempt from the ceiling; three unresponsive RFQs from one
account on one day produce ONE queue escalation; each alert kind lands in its specified tier;
the queue view never lists a DIGEST-tier alert.

### T11. Actionability measurement (S7)
Per notification kind: sent, delivered, viewed-in-portal within one business day, quoted within
the RFQ window. An admin view showing the rolling-30-day actionability rate per kind, flagging
kinds below the floor.
*Tests:* the rate is computed from recorded events, not estimated; a kind with no sends shows
"no data", never 0% or 100%; the flag fires below the floor and not above it; the calculation is
a pure function over a supplied `now`.

---

## SUCCESS CRITERIA (falsifiable)

1. Backend green; the 2808 pre-existing tests pass with zero edits (conftest excepted per T5);
   reported as `2808 + N`.
2. Frontend green; 197 pre-existing tests pass with only the authorised `verify-screen.test.tsx`
   edit; reported as `197 + M`.
3. Flags-off run: both suites green, no behaviour change.
4. A notification sends successfully after the RFQ daily cap is exhausted — proven against the
   real governance gate.
5. A verified webhook event succeeds from an IP that is currently rate-limited on rejections.
6. Zero network requests occur on verify-page render; the POST fires only on the gesture.
7. `git diff --name-status` shows `M` only on test files in the PRIME DIRECTIVE exception table.
   Any other pre-existing test file with `M` is a violation.
8. `ARC4B_REPORT.md` contains the gate H1–H12 with `file:line`, both counts, FINDINGS, and the
   updated env-config list.
9. **Volume proof:** a scripted scenario — one five-member account, ten RFQs across a Friday
   afternoon and the following Monday, two closed before Monday — is run as a test and the report
   states the exact count of supplier emails and concierge alerts it produced, against arc 4's
   behaviour on the same scenario. The number must go DOWN, and every remaining send must be
   justifiable as actionable. This is the headline evidence for the arc.
10. No calculation in T8, T10 or T11 reads the wall clock; every one takes `now` as a parameter.

---

## REVIEWER CHECKLIST

- **R1.** Is every `M` test file in the PRIME DIRECTIVE exception table? Any other → immediate
  `CHANGES_REQUESTED`.
- **R1b.** For EACH of the four arc-4 test files, read the diff line by line: were only the
  assertions encoding the superseded behaviour changed? Was each replaced by one pinning the new
  behaviour, or merely deleted? Did the named invariants (one reminder per RFQ, alert-once,
  same-`now` idempotency, cap-suppression) survive in tested form? Any deletion without
  replacement, or any unrelated assertion touched, is MAJOR.
- **R2.** Read the `verify-screen.test.tsx` diff line by line: did any security assertion weaken?
  Is the uniform-rejection equality still a Set-size-1 with a contrast case? Is the storage/console
  sweep intact? Is there a NEW no-request-on-render test?
- **R3.** Does the notification-cap test exhaust the RFQ cap against the REAL gate, or mock it?
- **R4.** Does a VERIFIED webhook event genuinely bypass the rejection limiter — tested, not
  asserted in prose? This is the one that protects against permanent delivery-data loss.
- **R5.** Is the throttled 403 byte-identical to the other rejections (body and headers), so the
  limiter is not an oracle?
- **R6.** Is the invite configuration set read from the Stubber-captured call rather than a
  constant in the code under test?
- **R7.** Both suites with flags OFF.
- **R8.** FINDINGS genuine, or task-avoidance?
- **R9.** Run the S-criterion-9 volume scenario yourself. Does the count genuinely drop versus
  arc 4, and is every remaining send defensible as actionable by its recipient? A reduction
  achieved by silently dropping notifications (rather than coalescing, deferring to digest, or
  cancelling on resolution) is a BLOCKER — the ceiling must defer, never discard.
- **R10.** Grep T8/T10/T11 code for `datetime.now(`, `time.time(`, `date.today(` inside the
  calculations. Any wall-clock read inside a business-hours or rate calculation is MAJOR — arc 4's
  date-dependent test was exactly this bug.
- **R11.** Does any path still send one email per RFQ per member where S2 says it should coalesce?

**Standing rule (CLAUDE.md):** an exit-checklist item may be marked done only if the evidence
exists as a committed artefact or a named passing test.

---

## OUT OF SCOPE

SES/SNS provisioning (infra track). The F9 escalation-scan filter (pilot-scale non-issue; revisit
before the notification table is large). SMS or push channels. Per-supplier custom business hours
(the account timezone plus a standard window is enough for v1). React 19.

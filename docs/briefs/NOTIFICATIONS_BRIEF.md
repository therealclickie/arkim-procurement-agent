# BUILD BRIEF — Notifications & Delivery Tracking (SES)

**Arc:** 4 of the supplier-account programme. **Type:** backend-heavy, small frontend surface,
flag-gated. **Branch:** cut from `test/flag-on-integration` after arc 3 has merged.
**Builder:** Claude Opus 5. **Reviewer:** Claude Fable 5. **Merge:** human only.
**Feature flag:** `NOTIFICATIONS_V1` — default OFF. Frontend surface gated on
`NEXT_PUBLIC_NOTIFICATIONS_V1` — default OFF.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

The supplier promise is "you will never miss an RFQ." Arcs 2–3 made the portal inbox the place a
supplier *sees* RFQs. This arc makes it the **system of record**: every RFQ notification is a
tracked object with a delivery state, email becomes a channel whose success is measured rather
than assumed, and an RFQ that goes unseen escalates until a human knows about it.

Outbound mail today goes through the Gmail API (`utils/gmail_client.py`, `utils/email_sender.py`)
with no delivery events beyond bounces landing in an inbox. This arc introduces a provider
adapter with **Amazon SES** as the first implementation, behind the existing send-governance gate.

---

## DESIGN DECISIONS (settled — build to these)

**D1. Provider adapter behind send governance, SES first.**
A `MailProvider` interface: `send(message) -> provider_message_id` plus a normalised inbound
event model. Two implementations: `SesProvider` (boto3) and `FakeProvider` (tests, dev). The
adapter sits **behind** the Night-10 governance gate — allowlist, caps, suppression and the
release queue all still apply. Nothing bypasses governance to reach SES.

**D2. Two SES configuration sets.**
`gofer-notifications` — open and click tracking ON. `gofer-auth` — tracking OFF. Magic-link and
any single-use-token mail MUST use `gofer-auth`: click tracking rewrites links through SES's
tracking domain, and corporate link scanners (Microsoft Safe Links etc.) pre-fetch them, which
would consume a single-use token before the human clicks. Configuration-set names are env config,
not literals.

**D3. Normalised delivery event model.**
`Notification` (id, account_id, member_id, kind ∈ {RFQ_NEW, RFQ_REMINDER, RFQ_ESCALATION,
AUTH_MAGIC_LINK, MEMBER_INVITE, …}, subject ref e.g. rfq_id, channel=EMAIL, provider,
provider_message_id, configuration_set, state, created_at, timestamps per transition) with
state ∈ {QUEUED, SUPPRESSED, SENT, DELIVERED, OPENED, CLICKED, BOUNCED, COMPLAINED, REJECTED,
FAILED}. Transitions are monotonic per the ladder QUEUED→SENT→DELIVERED→OPENED→CLICKED; terminal
failures (BOUNCED/COMPLAINED/REJECTED/FAILED) are absorbing. Every transition writes an audit row.

**D4. SES event ingestion via SNS → HTTPS webhook.**
`POST /api/webhooks/ses` receives SNS envelopes. It MUST: verify the SNS signature against the
signing certificate (fetched only from an `amazonaws.com` `SigningCertURL`, cached), reject
envelopes whose `TopicArn` is not in the configured allowlist, handle `SubscriptionConfirmation`
by visiting `SubscribeURL` (only when the TopicArn is allowlisted), and be **idempotent** on SES
`messageId` + event type (a redelivered event is a no-op). This is a public unauthenticated
endpoint; treat it with the same posture as the claim-token routes. Unknown/invalid → uniform 403
with no detail. Events map: Send→SENT, Delivery→DELIVERED, Open→OPENED, Click→CLICKED,
Bounce→BOUNCED, Complaint→COMPLAINED, Reject→REJECTED.

**D5. "Opened" is a weak signal. Escalation keys off portal read-state and clicks.**
Pixel opens are inflated (Apple Mail Privacy Protection fires them for everyone). The strongest
signal the system has is **the supplier viewing the RFQ in the portal** — arc 3's inbox. Add
`RfqView` (rfq_id, member_id, first_viewed_at, last_viewed_at) written when the session inbox
renders a request or its detail. Escalation logic treats `viewed_in_portal OR clicked` as
"seen"; OPENED alone never counts.

**D6. Escalation ladder (configurable, defaults stated).**
For each RFQ_NEW notification to a supplier account: if not seen within `ESCALATE_REMIND_HOURS`
(default 4), send ONE RFQ_REMINDER to the same member(s); if still not seen within
`ESCALATE_ALERT_HOURS` (default 24), create a concierge alert in the admin review queue
(RFQ_ESCALATION, no further email to the supplier). Never more than one reminder per RFQ per
member. Wall-clock hours in v1; business-hours awareness is a recorded follow-up. Driven by a
scheduler entry point (`notifications.run_escalations()`) callable from cron/ECS scheduled task;
no in-process timers.

**D7. Member notification preferences.**
Per member: `IMMEDIATE | DAILY_DIGEST | NONE`, default IMMEDIATE. Only members holding
`VIEW_REQUESTS` (arc 2/3 RBAC) are notified at all. DAILY_DIGEST batches RFQ_NEW into one mail
per day via the same scheduler entry point. NONE still receives AUTH and MEMBER_INVITE mail.
An account with zero notifiable members is itself a concierge alert.

**D8. Bounces and complaints suppress.**
Hard bounce or complaint on a member's address → mark the member email SUPPRESSED, stop
notifying, and raise a concierge alert. Reuse `bounce_processor` semantics; SES gives structured
bounce sub-types so the existing NDR text parsing is bypassed for SES-originated events.
Soft bounces do not suppress; three consecutive soft bounces raise an alert.

**D9. Close arc 2 finding 2: auth mail joins the governance ledger.**
Magic-link and invite sends write `sent_messages` rows so governance caps apply. The
in-process per-email/per-IP limiter from arc 2 is retained as defence-in-depth but is no longer
the only control. Record in the report that this closes arc 2 review finding 2.

**D10. Tests never touch AWS.**
All SES interaction is exercised through `FakeProvider` or botocore `Stubber`. Any test that
would open a network connection to AWS is a defect. Live SES verification is the human's job
(HUMAN VERIFICATION below).

---

## GATE RULINGS (pre-authorised — the gate may NOT stop to ask these)

Arc 3's gate wrote "awaiting a ruling" and then built anyway because nobody was there to rule.
To prevent that, decisions the gate is likely to surface are ruled here in advance:

- **Adding boto3 as a dependency:** AUTHORISED (runtime), plus `moto` or `botocore` Stubber for
  tests (dev). No other new runtime dependencies without a FINDING.
- **Q1 — which seam owns RFQ_NEW (raised by the gate on 2026-09-20): RULED.** `rfq_send`, at
  the point an RFQ is actually sent and the `sent_messages` row is written. Not `tier1_notify`.
  See T4. The gate's STOP on this question is resolved; the builder may proceed from the
  committed gate report without re-running the gate.
- **Modifying `email_sender.py` / `gmail_client.py`:** AUTHORISED, additive only — Gmail remains
  the provider when `NOTIFICATIONS_V1` is off; SES is selected when on. Existing send tests must
  pass unedited.
- **Adding a scheduler entry point:** AUTHORISED as a plain function + CLI command; NOT
  authorised to add a background thread, APScheduler, Celery, or any in-process timer.
- **Arc 3's verify page auto-submitting on load (if found):** report it as a FINDING with
  `file:line`; do NOT change it in this arc if the change would fail a protected test. It is
  arc 4b if needed.
- **Any scope question not covered above** where the brief is silent and the builder cannot
  proceed without a decision: **STOP after the gate, report the question, do not build.** A
  stopped arc with a clear question is the correct outcome; a built arc on a guessed answer is
  not.

---

## PRIME DIRECTIVE

1. **No pre-existing test file modified** — frontend or backend. New tests, new files. The 2479
   backend and 172 frontend tests pass unedited.
2. **Flags off = today's behaviour exactly.** `NOTIFICATIONS_V1` off → Gmail path, no
   Notification rows written, webhook returns 404, scheduler no-ops.
3. **No live AWS in tests** (D10).
4. **Nothing bypasses send governance** (D1). A test must prove a notification to a
   non-allowlisted address is suppressed by the real gate, not a mock.

---

## INVESTIGATION GATE (read-only; report with `file:line` BEFORE building)

- **G1. Send path today.** `email_sender.py`, `gmail_client.py`, `rfq_send.py`, and the Night-10
  governance gate: where the allowlist/caps/suppression/release-queue checks sit, and where a
  provider adapter slots in *below* them without moving them.
- **G2. `sent_messages` ledger.** Schema, who writes it, and why arc 2's auth mail didn't (D9).
- **G3. Bounce handling.** `bounce_parser.py` / `bounce_processor.py`: entry points and the
  suppression side-effects to reuse for D8.
- **G4. Tier-1 notify.** `tier1_notify.py` (17 tests): what it sends today when a Tier-1 supplier
  matches, and how RFQ_NEW attaches to it.
- **G5. Arc 3 inbox.** Where the session inbox renders a request (`requests-screen.tsx`,
  `open-requests.tsx`) and where `RfqView` should be written server-side (D5) — from the API
  call the inbox already makes, not a new client ping.
- **G6. Admin review queue.** How concierge alerts/pending items surface today (arc 2 T10
  pending memberships; earlier revision queue) so RFQ_ESCALATION reuses the pattern.
- **G7. Persistence + migrations** per arc 2's I1 (convention to follow for new tables).
- **G8. Verify page behaviour.** Does `verify-screen.tsx` POST the token on load or on click?
  (see GATE RULINGS).
- **G9. Config surface.** How env config is declared (region, configuration-set names, SNS
  TopicArn allowlist, from-address, tracking domain) — mirror the existing pattern.

**STOP after the gate. Commit the report. Do not begin T1 until it is written.**

---

## GUARDRAILS

1. Flag-gated, default off, inert when off.
2. Webhook: SNS signature verified, TopicArn allowlisted, idempotent, uniform 403 on anything
   else, no request body echoed in logs.
3. Magic-link/single-use mail never uses a tracking-enabled configuration set (D2).
4. OPENED never counts as seen (D5).
5. At most one reminder per RFQ per member (D6).
6. No in-process timers or background threads (GATE RULINGS).
7. **PowerShell:** `;` not `&&`.
8. **NO PUSH.** Commit per task.

---

## BUILD TASKS

### T1. Models + migration
`Notification`, `NotificationEvent` (audit of transitions), `RfqView`, `MemberNotificationPref`,
`ConciergeAlert` (if G6 shows no reusable table). Per G7 convention.
*Tests:* round-trips; monotonic state ladder enforced; absorbing terminal states; idempotency
key (provider_message_id + event_type) unique.

### T2. Flag + provider adapter
`NOTIFICATIONS_V1`; `MailProvider` interface; `FakeProvider`; `SesProvider` via boto3
`SendEmail` with configuration set + tags (notification_id). Provider selection by flag.
*Tests:* flag off → Gmail path unchanged (existing tests untouched and green); flag on →
SesProvider chosen; Stubber-verified `SendEmail` call shape including configuration set; no
network.

### T3. Governance integration (D1, D9)
Route every notification through the existing gate; write `sent_messages` for auth mail.
*Tests:* non-allowlisted recipient → SUPPRESSED state via the **real** gate; caps respected;
magic-link send now appears in the ledger; arc 2 rate limiter still active.

### T4. Notification service + RFQ_NEW — **hook at the `rfq_send` seam (Q1 RULED)**
`notify_rfq_new(rfq, account)` → fan-out to notifiable members per D7, one Notification each,
QUEUED→SENT. **Trigger point: `utils/rfq_send.py` at the moment an RFQ is actually sent to a
supplier** (reached from the release queue under `SEND_GOVERNANCE_V1`) — i.e. the same event
that writes the `sent_messages` row the portal inbox renders. This is the only seam where the
notification's subject is something the supplier can subsequently *view*, which D5 requires.
Do NOT hook `tier1_notify`: it writes no `sent_messages` row (nothing to view → every
notification would escalate) and is double-gated behind `TIER1_V2`. Optionally, `tier1_notify`
may emit a distinct `TIER1_FYI` notification kind that is tracked but **excluded from the D6
ladder**. Uses `gofer-notifications` set.
*Tests:* fan-out respects VIEW_REQUESTS and prefs; DAILY_DIGEST members are deferred not sent;
NONE members skipped; zero-notifiable-members raises a concierge alert; the notification's
subject ref is the `sent_messages` row the inbox will render (so `RfqView` in T7 can bind to
it); a TIER1_FYI kind, if emitted, never enters the escalation ladder.

### T5. Auth mail migration (D2)
Magic-link and invite mail through the adapter on the `gofer-auth` set (tracking OFF).
*Tests:* the auth send uses the auth configuration set and never the notifications set;
Stubber asserts no tracking-related tags; arc 2/3 auth tests untouched and green.

### T6. SES webhook receiver (D4)
`POST /api/webhooks/ses`: SNS signature verification, TopicArn allowlist,
SubscriptionConfirmation, Notification envelope → event mapping → state transition; idempotent.
*Tests:* valid signed envelope → transition; bad signature → 403; unknown TopicArn → 403;
non-amazonaws SigningCertURL → 403; replayed event → no-op; confirmation only for allowlisted
topic; all rejections byte-identical.

### T7. Portal read-state (D5)
Write `RfqView` from the server side of the arc 3 inbox/detail API (G5).
*Tests:* first render writes first_viewed_at; subsequent renders update last_viewed_at only;
token-route (claim) views also record with member_id null + supplier_domain.

### T8. Escalation scheduler (D6)
`run_escalations(now)` pure function over pending notifications: reminder at 4h, concierge
alert at 24h, "seen" = viewed OR clicked. CLI entry point.
*Tests:* table test over (age, viewed, clicked, opened, reminded) → expected action; OPENED
alone never suppresses escalation; exactly one reminder ever; alert created once; idempotent
when run twice with the same `now`.

### T9. Bounce/complaint suppression (D8)
Map BOUNCED (hard) / COMPLAINED → member SUPPRESSED + alert; soft bounces counted.
*Tests:* hard bounce suppresses and alerts; complaint suppresses and alerts; soft bounce ×2 no
action, ×3 alerts; suppressed member excluded from fan-out.

### T10. Preferences API + digest
`GET/PUT /api/supplier/notification-preferences` (session, self only); `run_daily_digest(now)`
batching deferred RFQ_NEW into one mail per member.
*Tests:* self-only; digest batches N into 1 and marks them; IMMEDIATE unaffected.

### T11. Frontend: preferences + read receipts (small)
Preferences control in the supplier profile screen; an "unseen" indicator in the inbox derived
from `RfqView`. Gated on `NEXT_PUBLIC_NOTIFICATIONS_V1`.
*Tests:* flag off renders nothing new; preference round-trips; indicator reflects view state;
no new storage writes (sweep as arc 1/3 did).

### T12. Admin: escalation queue
RFQ_ESCALATION and suppression alerts surfaced in the admin review queue (G6 pattern) with
acknowledge action.
*Tests:* alerts listed; acknowledge clears; admin auth required.

---

## SUCCESS CRITERIA (falsifiable)

1. Backend green; **2479 pre-existing tests pass with zero edits**; reported as `2479 + N`.
2. Frontend green; **172 pre-existing tests pass with zero edits**; reported as `172 + M`.
3. Flags-off run: both suites green, webhook 404, no Notification rows written, Gmail path
   selected — proven by explicit tests.
4. No test opens a network connection to AWS (D10) — proven by Stubber/FakeProvider usage and
   a grep for unstubbed boto3 clients in tests.
5. Auth mail provably uses the tracking-off configuration set (D2).
6. Webhook rejects bad-signature, unknown-topic and non-amazonaws-cert envelopes with identical
   403s, and is idempotent on replay (D4).
7. Escalation table test proves OPENED alone never counts as seen and at most one reminder is
   ever sent (D5/D6).
8. Non-allowlisted recipient suppressed by the real governance gate (D1).
9. `git diff --name-status` shows no `M` on any pre-existing test file.
10. `NOTIFICATIONS_REPORT.md` contains gate G1–G9 with `file:line`, both counts, FINDINGS, and
    the required env config list for HUMAN VERIFICATION.

---

## AGENT LOOP PROTOCOL

Filesystem-only handshake; human is sole merge authority. Builder: gate → report → T1–T12 →
counts + FINDINGS. Reviewer: `NOTIFICATIONS_REVIEW.md` + `loop/VERDICT.txt`. Fix rounds on
numbered findings only.

**Reviewer checklist:**
- **R1.** Any `M` on a pre-existing test file → immediate `CHANGES_REQUESTED`.
- **R2.** Both suites with flags OFF; webhook must 404; no Notification rows.
- **R3.** Grep tests for `boto3.client(` / `boto3.Session(` not wrapped by Stubber or FakeProvider.
  Any live client → BLOCKER.
- **R4.** Read the webhook handler: is the signing cert fetched only from amazonaws.com? Is the
  TopicArn allowlist checked BEFORE SubscribeURL is visited? Is idempotency keyed on
  messageId + event type?
- **R5.** Is the governance test exercising the real gate or a mock? Mock → MAJOR.
- **R6.** Does the auth-mail test assert the configuration set by name from the Stubber-captured
  call, not from a constant in the code under test?
- **R7.** Escalation table test: does it include the OPENED-but-not-viewed-or-clicked row and
  assert escalation still fires?
- **R8.** Any in-process timer/thread/scheduler library introduced? → MAJOR.
- **R9.** FINDINGS genuine? Did the gate STOP on any uncovered scope question, or guess?

**Standing rule (CLAUDE.md):** an exit-checklist item may be marked done only if the evidence
exists as a committed artefact or a named passing test.

---

## OUT OF SCOPE

SMS/voice channels (intake spine stubs exist; not wired here). Business-hours-aware escalation.
Inbound reply parsing via SES receipt rules (Gmail inbound remains). Dedicated IPs. React 19.
Supplier metrics/analytics. Live SES/SNS provisioning (infra track — see below).

---

## HUMAN VERIFICATION (after `VERDICT: APPROVED`) — and the infra it depends on

Infra you must have in place before flags go on in a real environment (none of it needed for
the build or the review):
- SES domain identity for `mygofer.ai` verified; Easy DKIM CNAMEs and a custom MAIL FROM
  subdomain in Route 53; DMARC record.
- SES **production access** requested and granted (sandbox = verified recipients only).
- Two configuration sets: `gofer-notifications` (open+click on), `gofer-auth` (off).
- One SNS topic with an HTTPS subscription to `https://<app>/api/webhooks/ses`; the topic ARN
  in the app's allowlist config.
- ECS task role with `ses:SendEmail` scoped to the identity + configuration sets.

Then:

```powershell
git diff --name-status <branch-point> HEAD
uv run pytest -q                                   # 2479 + N
cd frontend ; npm test ; cd ..                     # 172 + M
$env:NOTIFICATIONS_V1='0' ; uv run pytest -q       # green
Get-Content NOTIFICATIONS_REPORT.md                # env config list + FINDINGS
```

Live: with flags on and a single allowlisted supplier address, trigger an RFQ_NEW, confirm the
SES console shows the send on `gofer-notifications`, confirm the SNS event lands and the
Notification transitions SENT→DELIVERED, open the RFQ in the portal and confirm `RfqView` is
written and the escalation would not fire. Request a magic link and confirm it went out on
`gofer-auth` with no rewritten links.

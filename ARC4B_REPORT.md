# ARC 4b — INVESTIGATION GATE REPORT

**Branch:** `arc4b/flag-on-rulings` · **Builder:** Claude Opus 5 · **Date:** 2026-09-23
**Status:** gate only. Nothing built. Every claim below carries a `file:line`.

## Baseline (measured, this branch, this machine)

| Suite | Command | Result |
|---|---|---|
| Backend | `uv run pytest -q` | **2808 passed, 73 skipped** (182s) |
| Frontend | `cd frontend && npm test` | **197 passed** (24 files, 15s) |

Both match the brief's stated baseline (2808 / 197).

---

# H1 — `utils/send_governance.py`: message class, the `rfq` default, caps, ledger

- **Class resolution.** `utils/send_governance.py:347` —
  `message_class = meta.get("message_class") or supplier_registry.MESSAGE_CLASS_RFQ`.
  That `or` **is** the absent-class default to `rfq`. The class constants live in the
  registry, not here: `utils/supplier_registry.py:1096-1097`
  (`MESSAGE_CLASS_RFQ = "rfq"`, `MESSAGE_CLASS_AUTH = "auth"`).
- **Cap selection.** `utils/send_governance.py:348-351` — a two-branch `if`:
  `auth` → `SEND_GOVERNANCE_AUTH_DAILY_CAP` (env const `:283`, default 50 at `:284`);
  everything else → `SEND_GOVERNANCE_DAILY_CAP` (env const `:271`, default 10 at `:273`).
  `_cap_from_env` (`:287-293`) returns the default when unset and **raises** on an
  unparseable value, which `_check_caps`' `except` (`:368-370`) turns into
  `cap_blocked` — fail-closed.
- **Counting.** `supplier_registry.count_send_attempts_utc_day()`
  (`utils/supplier_registry.py:1190-1215`); a `NULL` class counts as `rfq`
  (`:1204-1207`); only ATTEMPT statuses count (`SEND_ATTEMPT_STATUSES`, `:1089`) —
  blocked verdicts never consume cap.
- **Ledger write.** `supplier_registry.record_sent_message()`
  (`utils/supplier_registry.py:987-1050`), `message_class` parameter at `:1005`,
  bound into the INSERT at `:1039` / `:1043`. The column was added by the PRAGMA
  migration at `:503-505`.
- The second cap stage (per-supplier-per-part open RFQs, `:358-366`) keys on
  `metadata["part_key"]`; notification mail has none, so it is unaffected.

**Minimal additive change for a new class** (4 edits, no restructuring):

1. `utils/supplier_registry.py:1097` — add `MESSAGE_CLASS_NOTIFICATION = "notification"`.
2. `utils/send_governance.py:283-284` — add `_NOTIFICATION_DAILY_CAP_ENV = "NOTIFICATION_DAILY_CAP"` plus a generous default.
3. `utils/send_governance.py:348-351` — replace the two-branch `if` with a
   `{class: (env, default)}` lookup falling back to the RFQ pair, so the absent-class
   default stays byte-identical.
4. `utils/notifications.py:256-264` — stamp `metadata["message_class"]` on the
   notification's `EmailMessage`, and write the `sent_messages` row the way auth mail
   does (`utils/notifications.py:117-140`).

# H2 — `utils/notifications.py`: every send call site

- **There is exactly ONE transport call site:** `_send_notification_mail()`
  (`utils/notifications.py:238-280`), which builds the `EmailMessage` at `:256-264`
  and calls `GmailSender().send` at `:265`.
- Its three callers — so **digest and reminder DO share the path**:
  - RFQ_NEW immediate fan-out — `:345-347`
  - RFQ_REMINDER — `:658-666` (inside `_send_reminder`)
  - RFQ_DIGEST — `:725-728` (inside `run_daily_digest`)
- The metadata dict at `:258-263` carries `supplier_domain`, `notification_id`,
  `notification_kind`, `run_id` — **no `message_class`**, so all three fall to `rfq`
  at `send_governance.py:347`. Confirmed.
- **No `record_sent_message` anywhere on the notification path.** The only ledger
  write in this module is `record_auth_send` (`:117-140`), used by auth/invite mail
  only (`utils/supplier_accounts.py:1010`, `:1075`). Notification volume is genuinely
  invisible to the ledger, exactly as R-F8 states.
- `notify_tier1_fyi` (`:380-397`) creates a notification row but **sends no mail**, so
  it needs no class.

# H3 — the arc-4 T12 alert mechanism (reuse, do not reinvent)

- Kind constants: `utils/notifications_store.py:137-140`
  (`ALERT_RFQ_ESCALATION`, `ALERT_NO_NOTIFIABLE_MEMBERS`, `ALERT_EMAIL_SUPPRESSED`,
  `ALERT_SOFT_BOUNCE_REPEATED`); status constants at `:134-135`.
- `raise_alert(...)` — `utils/notifications_store.py:884-918`. **Dedupe is a partial
  UNIQUE index on `dedupe_key`** (`:300-303`); a duplicate raises
  `sqlite3.IntegrityError`, caught at `:913-914` and returned as `None`
  ("already raised", not a failure).
- Existing dedupe-key idioms: `f"no-members:{run_id}:{domain}"`
  (`notifications.py:319`), `f"escalation:{id}"` (`:677`),
  `f"suppressed:complaint:{addr}"` (`:499`), `f"suppressed:hard_bounce:{addr}"`
  (`:514`), `f"soft_bounce:{addr}:{streak_started_at}"` (`:533`).
- **`NOTIFICATION_CAP_BLOCKED` therefore needs no new machinery:** one constant next to
  `:140`, and a dedupe key embedding the UTC day — e.g. `f"notification_cap:{day}"`.
  "Deduped per day, re-arms the next day" falls straight out of the key.
- The `concierge_alerts` DDL (`:280-298`) has **no tier column** — see H12.

# H4 — `utils/ses_webhook.py` + the route: exactly where rejection happens

- `handle_envelope()` (`utils/ses_webhook.py:290-335`) returns `None` at five points:
  non-JSON body `:307-308`, non-dict `:309-310`, unknown envelope type `:313-314`,
  **topic not allowlisted `:316-317`**, **signature failure `:319-320`**.
- The ordering is load-bearing and already cheap-first: the allowlist check (`:316`,
  a local set lookup via `allowed_topic_arns()` `:83-90`) runs **before** any
  certificate fetch or RSA verify (`verify_signature` `:165-200`,
  `fetch_certificate_pem` `:142-162`).
- **The route:** `api_server.py:7518-7546`. Flag gate `:7538-7539`; the uniform
  rejection is `raise HTTPException(status_code=403, detail="Forbidden")` at
  **`:7544`**; success is
  `JSONResponse({"ok": True}, headers=_portal_response_headers({}))` at `:7545-7546`
  (`_portal_response_headers` at `api_server.py:5570-5576`).
- **What "byte-identical" means here, precisely.** The rejection goes through
  FastAPI's default `HTTPException` handler, so the 403 body is `{"detail":
  "Forbidden"}` with **no** `Cache-Control` / `Referrer-Policy` (those exist only on
  the 200). The pre-existing test
  `utils/procurement_agent/tests/test_ses_webhook.py:211-223` compares
  `(status, content, cache-control, referrer-policy)` across every rejection and
  asserts a set of size 1. **So the throttled response must be the same bare
  `HTTPException(403, "Forbidden")` — no `Retry-After` and no extra headers**, unlike
  every other limiter in the repo (see H5).

# H5 — existing in-process limiters (reuse the shape, not the status code)

Four, all the same pattern — module-level `dict` bucket + `threading.Lock`, fixed
window, caps read live via `_env_int`, keyed on `_client_ip(request)`
(`api_server.py:5501-5509`), **no dependency**:

| Surface | Lines | Cap envs |
|---|---|---|
| Supplier portal (claim token) | `api_server.py:5488-5535` | `SUPPLIER_PORTAL_RATE_CAP`, `SUPPLIER_PORTAL_RATE_WINDOW_SEC` |
| Quote submit | `api_server.py:6028-6068` | `QUOTE_SUBMIT_RATE_CAP`, `QUOTE_SUBMIT_RATE_WINDOW_SEC` |
| **Supplier auth `request-link` (arc 2)** | `api_server.py:6615-6660` | `SUPPLIER_AUTH_RATE_CAP_EMAIL`, `SUPPLIER_AUTH_RATE_CAP_IP`, `SUPPLIER_AUTH_RATE_WINDOW_SEC` |
| Supplier verify | `api_server.py:6767-6790` | `SUPPLIER_VERIFY_RATE_CAP` |

The arc-2 one (`_supplier_auth_rate_bump`, `:6626`) is the closest shape: two bucket
keys, `cap <= 0` ⇒ inert (`:6646`), window reset at `:6649-6651`, trip at
`:6653-6654`. **Copy the bucket mechanics; do NOT copy the 429 raise (`:6655-6660`)** —
per H4 the webhook must answer with the uniform 403 or the limiter becomes an oracle.

# H6 — `verify-screen.tsx` and its 13 tests

- **Token read + POST today:** `frontend/src/app/supplier/verify/verify-screen.tsx:58-77`
  — a `useEffect` with a StrictMode `started` ref guard (`:56`, `:59-60`); the token is
  read at `:62` (`params?.get("token")`), the empty case short-circuits to `rejected`
  at `:63-68`, and `verifyMagicLink(token)` is called at **`:69`**, with the redirect
  at `:72` and the rejection at `:74`. Phase state is `"verifying" | "rejected"`
  (`:47`, `:52`) — **T3 needs a third phase** (an idle "Continue to sign in" state)
  plus the control.

**All 13 tests in `frontend/src/app/supplier/verify/__tests__/verify-screen.test.tsx`,
and whether each depends on POST-on-load:**

| # | Line | Name | Depends on POST-on-load? |
|---|---|---|---|
| 1 | `:67` | posts the token and redirects to the inbox | **YES** — renders, then awaits `replace` |
| 2 | `:81` | uses replace, not push, so the token URL leaves history | **YES** |
| 3 | `:89` | exchanges the token exactly once (StrictMode) | **YES** |
| 4 | `:102` | renders one output across expired / used / unknown / pending | **YES** (via `renderAndRead`, `:54-60`) |
| 5 | `:115` | collapses divergent backend shapes to the same output | **YES** (`renderAndRead`) |
| 6 | `:135` | treats a missing token as the same dead end | **PARTLY** — the no-token half asserts zero calls (`:145`) and stays true; the with-token half uses `renderAndRead` |
| 7 | `:148` | the equality would FAIL on divergent output (contrast case) | **YES** (both halves) |
| 8 | `:161` | never names which failure occurred, and offers a way back | **YES** |
| 9 | `:180` | leaves no trace after a successful sign-in | **YES** |
| 10 | `:202` | leaves no trace after a rejected sign-in | **YES** (`renderAndRead`) |
| 11 | `:213` | does not render the token on screen | **YES** (`renderAndRead`) |
| 12 | `:225` | renders NOTHING and makes no request when the flag is off | **NO** — already asserts zero calls |
| 13 | `:233` | verifies when the flag is on | **YES** |

The cheapest authorised edit is to make the shared helper `renderAndRead` (`:54-60`)
click the control before reading, and add the click to the direct-render tests
(1, 2, 3, 9, 13). **No security assertion needs to move:** the Set-size-1 equality
(`:112`, `:132`), the contrast case (`:158`), the storage/console sweep (`:188-199`,
`:207-210`) and the no-token zero-request assertion (`:145`) are all independent of
*when* the POST fires. The NEW pinning test (zero requests on render, before any
gesture) is purely additive.

# H7 — the invite send path: R-F5 is already half-implemented

- `utils/supplier_accounts.py:1032-1087` (`send_member_invite_email`), called from the
  invite route at `api_server.py:7292-7294` inside a fail-soft wrapper (`:7291-7297`).
- **Already true today:** it writes a ledger row (`record_auth_send` at `:1075-1076`,
  transitioned at `:1079-1081`) in the **auth** cap class
  (`metadata["message_class"] = MESSAGE_CLASS_AUTH`, `:1073`), and sets
  `auth_mail: True` (`:1072`), which routes it onto the tracking-off auth
  configuration set (`utils/mail_provider.py:152-158`, which refuses to send when
  `SES_CONFIGURATION_SET_AUTH` is unset). Both are already pinned by pre-existing
  tests: `test_notifications_auth_mail.py:246-262` (configuration set read from the
  Stubber-captured call, `:256`) and `:274-280` (ledger row, auth class).
- **What is actually missing for R-F5:** (a) the per-account daily cap
  (`INVITE_DAILY_CAP_PER_ACCOUNT`, default 10); (b) the copy — `:1057` interpolates
  only `invited_by_email`, and `:1063-1064` names the *recipient's* domain, not the
  inviter's name or their company. The route already holds both
  (`session["member"]["email"]`, `session["account"]["supplier_domain"]`,
  `api_server.py:7293-7294`).
- There is **no per-account send counter** today. The cap needs a count over
  `sent_messages` (auth class, discriminated per account) — note the row carries
  `supplier_domain` (`supplier_registry.py:1013`), which is the account's natural key.

# H8 — conftest: `NOTIFICATIONS_V1` is ALREADY pinned. T5 is a no-op.

- `utils/procurement_agent/tests/conftest.py:71-75` — `_FEATURE_FLAG_ENVS` already
  contains `"NOTIFICATIONS_V1"` (line **74**), landed as commit `442667e`
  ("test: pin NOTIFICATIONS_V1 in conftest flag list (arc 4 review)").
- Consequence: **T5 requires no edit**, and the one authorised `conftest.py` change is
  not needed — so `git diff --name-status` should show **one** `M` test file
  (`verify-screen.test.tsx`). Criterion 7 should be read as "at most these two".
- Stale comment worth fixing (a docstring, not an assertion):
  `utils/procurement_agent/tests/_arc4_notifications_fixtures.py:11-18` still claims
  `NOTIFICATIONS_V1` "is NOT in `conftest.py`'s `_FEATURE_FLAG_ENVS` pin list".

# H9 — RFQ lifecycle state: where it lives, and what does NOT exist

**There is no `awarded` state and no "quote target met" concept anywhere in this
repo.** `grep -rn "award"` over `*.py` / `*.ts` / `*.tsx` returns only two unrelated
hits in scoring tests (`test_scoring.py`, `test_supplier_capability_provenance.py` —
"points awarded"). `grep` for `quote_target|target_quote|quotes_needed|needs_quotes`
returns nothing. Per the brief, **S4 is implemented for the states that DO exist, and
no quote-target model is invented.**

The states that DO exist, in four separate stores:

1. **The RFQ row itself — `sent_messages.status`** (the row a notification's
   `subject_ref` points at, `notifications.py:335`):
   `"released"` pre-attempt (`utils/rfq_send.py:198`) → `"sent" | "stubbed" | "error"`
   or a governance verdict (`rfq_send.py:229-241`); later `"replied"`
   (`utils/reply_processor.py:112`) or `"bounced"` (`utils/bounce_processor.py:125`).
   **`OPEN_RFQ_STATUSES = ("sent", "stubbed")`** — `utils/supplier_registry.py:1100`.
   The supplier portal's definition of an open request is exactly this filter
   (`api_server.py:6328-6348`, the test at `:6347`). **This is the primary S4 signal:
   an RFQ stops being open when its row leaves `OPEN_RFQ_STATUSES`.**
2. **Quote-token "closed"** (`QUOTE_SUBMIT_V1`, flag-gated at
   `utils/quote_tokens.py:57-62`): `state = "closed"` iff `revoked_at` is set **or**
   the window expired (`:238`; `STATE_CLOSED` at `:74`). Revocation entry points:
   `revoke()` `:256-274` and **`revoke_for_rfq(rfq_id, reason="rfq_withdrawn")`
   `:277-295`** — the closest thing the repo has to "the buyer closed this RFQ".
3. **Run phase** — `utils/procurement_agent/state/phases.py:14-28`: `CANCELLED`
   (`:27`) and `COMPLETED` (`:26`) are terminal (`VALID_TRANSITIONS` `:62-63`), and
   every phase can escape to `CANCELLED` / `ERROR` (`:55-58`). The orchestrator that
   drives these is **not on the shipping path** (CLAUDE.md §8), but phases are
   persisted and `api_server.py:2167` cancels a `pending_intake` run.
4. **Quote status** (the supplier's answer, not the RFQ) — `utils/quote_store.py:80-84`:
   `active / review / superseded / expired / withdrawn`. Orders add
   `cancelled / received` (`utils/orders.py:52`, terminal per `:55`).

**S4 as implementable today:** cancel pending reminders/escalations for an RFQ when
(a) its `sent_messages` row leaves `OPEN_RFQ_STATUSES` (replied / bounced / error),
(b) its quote token is revoked or expired (`quote_tokens.validate_token` → `closed`),
or (c) its run reaches `Phase.CANCELLED` / `Phase.COMPLETED`. Nothing else exists to
hook. "Quote target met" is **out** — reported, not invented.

# H10 — `run_escalations` / `decide_escalation` / the reminder path

- `decide_escalation` — `utils/notifications.py:563-602`. Pure over its arguments
  except `is_seen` (`:592` → `:544-560`), which reads the store. Guards in order:
  kind (`:577`), terminal state (`:579`), deferred (`:581`), already escalated
  (`:583`), seen (`:592`); age from `sent_at` else `created_at` (`:594`), **plain
  wall-clock hour arithmetic** (`:597`), alert checked before remind (`:598-601`).
- `run_escalations(now=None)` — `:605-633`. Iterates **every** `RFQ_NEW` notification
  (`:622`), one decision per notification — i.e. **per (RFQ × member)**.
- `_send_reminder` — `:636-667`. `mark_reminded` is claimed **before** the mail is
  built (`:643`), which is what makes "at most one reminder per RFQ per member" a
  write-guard rather than a check-then-act. It creates an `RFQ_REMINDER` row
  (`:645-651`) carrying the parent's `run_id` / `recipient`, then mails it (`:658-666`).
- `_raise_escalation` — `:670-688`. `mark_escalated` guard (`:674`); the alert is
  deduped on `f"escalation:{parent['id']}"` (`:677`) — **per notification, so per RFQ
  per member**, not per account.

**What must change for S2/S6, and what survives:**

- *Per-member-per-day reminders:* the decision moves from "one notification" to "one
  member's unseen set". The `mark_reminded` idempotency **survives** if it is claimed
  on every parent in the batch before sending (all-or-nothing per batch), and
  "an RFQ never appears in two reminders" follows from the same column.
- *Per-account escalations:* `mark_escalated` stays per notification (that is what
  stops re-laddering); only the **alert** dedupe key changes, from
  `escalation:{notification_id}` to `escalation:{account_id}:{business_day}`. The
  "alert once" guarantee survives — it is the unique index
  (`notifications_store.py:300-303`), and a coarser key can only reduce the count.
- *Business-hours clocks:* `decide_escalation`'s `age_hours` (`:597`) becomes a
  business-hour delta; `remind_hours()` / `alert_hours()` (`:87-95`) keep their env
  contract (`ESCALATE_REMIND_HOURS` / `ESCALATE_ALERT_HOURS`, defaults 4.0 / 24.0 at
  `:56-57`) but the unit changes meaning (24 wall → 8 business).
  **This is where the pre-existing suite breaks — FINDING F4.**

# H11 — account timezone

- **Nothing exists.** `grep -rn "zoneinfo|ZoneInfo|America/"` across `utils/` and
  `api_server.py` returns zero hits outside `timezone.utc`.
- The `supplier_accounts` DDL has no timezone column —
  `utils/supplier_accounts.py:194-203` (`id`, `supplier_domain`, `status`,
  `created_at`, `updated_at`, `is_test`) — and that module has **no `_migrate` helper
  and no `ALTER TABLE` anywhere**. The house PRAGMA-migration patterns to copy are
  `utils/notifications_store.py:334-342` and `utils/supplier_registry.py:474-505`.
- `zoneinfo` works on this Windows box **only because `tzdata` is installed
  transitively via pandas** — `uv.lock:1066-1074` shows
  `tzdata ... marker = "sys_platform == 'emscripten' or sys_platform == 'win32'"` under
  `[[package]] name = "pandas"`. Verified live: `ZoneInfo("America/Los_Angeles")`
  resolves and yields `-08:00` in January. It is **not** a declared project dependency
  (`pyproject.toml:5-32`).
  *Recommendation:* declare `tzdata; sys_platform == "win32"` explicitly. It is already
  in the lock, so it adds nothing to the install, and it stops a future pandas removal
  from silently breaking every business-hours calculation on Windows.

# H12 — the T12 admin queue, tiers, and where an actionability view fits

- Endpoints: `api_server.py:7607-7636` (list open alerts; the flag gate runs **before**
  `require_admin`, `:7631-7633`) and `:7639-7659` (acknowledge; 404 at `:7655`,
  409 at `:7658`).
- Store read: `notifications_store.list_alerts(status=, kind=)` — `:934-956`.
  **It filters on `status` and `kind` only; there is no tier column** in `_DDL_ALERTS`
  (`:280-298`).
- **How a tier would be represented:** one `tier TEXT` column added via the existing
  PRAGMA `_migrate` (`:334-342`), defaulting legacy rows to `QUEUE`; a `kind → tier`
  map next to the kind constants (`:137-140`); a `tier=` filter on `list_alerts`; the
  list endpoint passing `tier in (ACTION_NOW, QUEUE)`.
- **The frontend is free.** The admin table derives its columns from the rows' scalar
  keys (`frontend/src/app/admin/page.tsx:98-105`), so a `tier` field renders with no
  frontend change. The tab is registered at `:59` and the Acknowledge action at
  `:702-714`. A **per-kind actionability view** fits as one more `TABS` entry
  (`:42-60`) pointing at a new `GET /api/admin/notification-actionability` beside
  `:7607`, with its own `listKey` — again rendered by the generic table.

---

# FINDINGS

Five of the eleven build tasks require behaviour that pre-existing, un-editable tests
assert the opposite of. These are not style clashes — they are direct logical
inversions of the rulings.

### F1 — BLOCKER. T1 (R-F8) is the exact inverse of a pre-existing test.

`utils/procurement_agent/tests/test_notifications_governance.py:88-99`
(`test_the_daily_cap_is_respected_by_notification_mail`) sets
`SEND_GOVERNANCE_DAILY_CAP=1`, records one **rfq-class** row, sends a notification
through the real gate, and asserts it lands **`STATE_SUPPRESSED`** with an empty
outbox. T1's own required test is the same scenario asserting **SENT**. Both cannot
pass. The pre-existing test may not be edited, and no default for
`NOTIFICATION_DAILY_CAP` reconciles them: the test sets only the RFQ cap, so a
notification-class count of 0 always passes a "generous" notification cap.

### F2 — BLOCKER. T6 (S1) is the inverse of the fan-out test.

`test_notifications_rfq_new.py:57-66` asserts that an ACTIVE `ROLE_MEMBER`
(`staff@dxpe.com`) **receives** RFQ_NEW —
`sorted(recipients) == ["owner@dxpe.com", "staff@dxpe.com"]` and `len(outbox) == 2`.
S1 makes `receives_rfq` default **false** for MEMBER, so the same call yields one
recipient and one mail. (Side note: `:80-90` would still pass, but vacuously — it
would then assert its empty result for the wrong reason.)

### F3 — BLOCKER. T10 (S6) is the inverse of the alert-queue test.

`test_notifications_admin_alerts.py:100-116`
(`test_every_alert_kind_the_arc_raises_is_listed`) raises all four kinds and asserts
`count == 4` with all four present in the queue. S6 places `SOFT_BOUNCE_REPEATED`
(and a hard bounce on a non-sole contact) in the **DIGEST** tier and states that the
admin queue shows ACTION_NOW and QUEUE only → `count == 3`.

### F4 — BLOCKER (date-dependent). T8 (S3) makes five pre-existing tests calendar-dependent — the exact bug class the brief cites.

`test_notifications_escalation.py` builds rows with `aged_notification(hours=N)`
(`:139-153`), which back-dates `sent_at` **relative to the wall clock**, then calls
`run_escalations()` with **no `now`** (`:161-164`, `:190`, `:242`, `:252`) or with
`datetime.now(timezone.utc)` (`:209-211`, `:228-232`). The file documents that choice
explicitly at `:218-225`. Under business-hour arithmetic, "5 hours ago" is **zero
business hours** on a Saturday or overnight, so
`test_a_reminder_is_sent_once_and_only_once` (`:156`),
`test_a_reminder_then_an_alert_walks_the_whole_ladder` (`:206`),
`test_running_twice_with_the_same_now_changes_nothing` (`:218`),
`test_a_portal_view_between_runs_stops_the_ladder` (`:237`) and
`test_a_suppressed_member_is_not_chased` (`:247`) become green-on-Tuesday,
red-on-Sunday. Those rows also carry `account_id="acct-1"`, which exists in **no**
`supplier_accounts` row, so an account-timezone lookup finds nothing to read.

### F5 — MAJOR. T7 (S2) conflicts two ways.

(a) *Reminder consolidation:* `test_notifications_escalation.py:176-183` creates two
RFQs for the **same `member_id` ("m-1")** with **different recipients**
(`a@dxpe.com`, `b@dxpe.com`) and asserts **two** reminder rows,
`{("run-a","a@…"), ("run-b","b@…")}`. "One reminder per member per business day"
produces one. **Survivable** if the consolidation group key is the **recipient
address** rather than `member_id` — defensible (the address is the mailbox), and it
should be the design.

(b) *RFQ_NEW coalescing:* a 15-minute window inherently defers the **first** send, but
`test_notifications_rfq_new.py:65` asserts `all(n["state"] == STATE_SENT)` immediately
after `notify_rfq_new`, and `:140` asserts the same for the default preference.
"Three RFQs → one email listing three" is unreachable without holding the first.
**Not survivable** without a gate, or a "send the first, batch the rest" compromise
that yields two emails for three RFQs — contradicting S2's wording.

### F6 — T5 is already done.

See H8: `NOTIFICATIONS_V1` is at `conftest.py:74`. No `conftest.py` edit is needed;
the arc should end with **one** modified test file.

### F7 — R-F5 is half-shipped.

See H7: the ledger row and the tracking-off `gofer-auth` configuration set already
exist and are already test-pinned (`test_notifications_auth_mail.py:246-262`,
`:274-280`). T4's real content is the per-account cap and the copy.

### F8 — T2's limiter cannot shed verification work without violating R-F10.

R-F10 requires that a verified event from a throttled IP still be processed. You
cannot know an envelope is unverified without verifying it, so the limiter must run
**after** `handle_envelope` returns `None`: it can count and record, but it cannot
short-circuit. Most of the saving it appears to promise is already banked — the topic
allowlist (`ses_webhook.py:316`) is a local set lookup and runs **before** the
certificate fetch (`:319`), so a foreign-topic spray already costs nothing. The
residual protection is against a bad-**signature** spray on a correctly guessed
allowlisted ARN (RSA verify plus a cached cert read). T2 is implementable and worth
doing, but its observable behaviour must be asserted through the limiter seam (a
returned/recorded "throttled" fact), **never** through the HTTP response, which stays
byte-identical per H4 and `test_ses_webhook.py:211-223`.

### F9 — MINOR. The configuration set a notification is *recorded* with is not the one it is *sent* on.

`notifications.py:337` stores `notifications_configuration_set()` on the row (and
reminders inherit it, `:650`), but the transport picks the set from the **message
metadata** (`mail_provider.message_configuration_set`, `:143-161`), which
`_send_notification_mail` (`:256-264`) never sets — so the send falls through to
`notifications_configuration_set()` at `mail_provider.py:161`. Same value today, so no
bug; but the row is descriptive, not authoritative, and T1's metadata change touches
exactly that dict.

---

# ENV-CONFIG LIST

**Existing, relevant (all read live):**

| Var | Default | Where |
|---|---|---|
| `NOTIFICATIONS_V1` | off | `utils/mail_provider.py:61`, `api_server.py:7509-7511` |
| `SUPPLIER_ACCOUNTS_V1` | off | pinned at `conftest.py:74` |
| `SEND_GOVERNANCE_V1` | off | `utils/send_governance.py:79-82` |
| `SEND_GOVERNANCE_DAILY_CAP` | 10 | `utils/send_governance.py:271,273` |
| `SEND_GOVERNANCE_AUTH_DAILY_CAP` | 50 | `utils/send_governance.py:283-284` |
| `SEND_GOVERNANCE_OPEN_RFQ_CAP` | 1 | `utils/send_governance.py:272,274` |
| `ESCALATE_REMIND_HOURS` | 4.0 | `utils/notifications.py:54,56` |
| `ESCALATE_ALERT_HOURS` | 24.0 | `utils/notifications.py:55,57` |
| `SES_CONFIGURATION_SET_NOTIFICATIONS` | unset | `utils/mail_provider.py:63,88-92` |
| `SES_CONFIGURATION_SET_AUTH` (`gofer-auth`) | unset ⇒ auth mail refused | `utils/mail_provider.py:64,95-98,152-158` |
| `SES_SNS_TOPIC_ARN_ALLOWLIST` | empty ⇒ reject all | `utils/ses_webhook.py:46,83-90` |
| `SUPPLIER_AUTH_RATE_CAP_EMAIL` / `_IP` / `SUPPLIER_AUTH_RATE_WINDOW_SEC` | 3 / 20 / 600 | `api_server.py:6621,6640-6641` |

**New, introduced by this arc (names/defaults per the brief):**

| Var | Default | Task |
|---|---|---|
| `NOTIFICATION_DAILY_CAP` | generous (propose 200) | T1 |
| `WEBHOOK_REJECT_RATE_LIMIT` | 60 per IP per minute | T2 |
| `WEBHOOK_REJECT_RATE_WINDOW_SEC` | 60 | T2 (the house limiter shape needs the window too) |
| `INVITE_DAILY_CAP_PER_ACCOUNT` | 10 | T4 |
| `NOTIFY_COALESCE_MINUTES` | 15 | T7 |
| `ACCOUNT_DEFAULT_TIMEZONE` | `America/Los_Angeles` | T8 (per-account column overrides) |
| `BUSINESS_HOURS_START` / `BUSINESS_HOURS_END` | 8 / 17 local | T8 |
| `MEMBER_DAILY_NOTIFICATION_CEILING` | 5 | T10 |
| `NOTIFY_ACTIONABILITY_FLOOR` | 0.20 | T11 |

---

GATE STOP: F1–F5 show that T1, T6, T7, T8 and T10 each require behaviour whose opposite is asserted by a pre-existing, un-editable test — notification mail MUST be RFQ-capped (`test_notifications_governance.py:88-99`); an ACTIVE MEMBER MUST receive RFQ_NEW (`test_notifications_rfq_new.py:57-66`); a SOFT_BOUNCE_REPEATED alert MUST appear in the admin queue (`test_notifications_admin_alerts.py:100-116`); reminders MUST fire on wall-clock hours (`test_notifications_escalation.py:156-253`); RFQ_NEW MUST be SENT immediately (`test_notifications_rfq_new.py:65`, `:140`). The brief authorises editing only `verify-screen.test.tsx` (plus `conftest.py`, which F6 shows is already done) and requires all 2808 backend tests to pass, so the rulings as written cannot ship under `NOTIFICATIONS_V1` alone. Which resolution do you want — (a) a NEW default-OFF flag (e.g. `NOTIFY_SIGNAL_V2`) gating every S-series and R-F8 behaviour change, so arc-4 behaviour is preserved when off and the volume proof plus criteria 4 and 9 run with it on, accepting that a pilot must then flip two flags rather than one; or (b) extend the prime-directive exception to the four arc-4 test files named above, so the new behaviour becomes the only behaviour under `NOTIFICATIONS_V1`?

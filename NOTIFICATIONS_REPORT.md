# NOTIFICATIONS_REPORT.md — Arc 4 Investigation Gate (G1–G9)

**Branch:** `arc4/notifications` · **Branch point:** `d20ca68` (`loop/BRANCH_POINT.txt`)
**Status:** gate complete. **NOTHING BUILT.** No file outside this report was created or modified.
**Prime directive respected:** no test file (pre-existing or otherwise) was touched; read-only pass.

**Baseline measured at gate time (not quoted from the brief):**

| Suite | Command | Result |
|---|---|---|
| Backend | `uv run pytest -q` | **2479 passed, 73 skipped**, 1 warning, 177s |
| Frontend | `cd frontend ; npm test` | **172 passed** (21 files), exit 0 |

Both match the brief's success criteria (`2479 + N`, `172 + M`).

> **One question requires a ruling before T4 can be built — see OPEN QUESTION Q1 at the end.**
> Everything else in the brief is answerable from the code and is answered below.

---

## G1. Send path today, and where a provider adapter slots in

### The seam stack, as it actually runs

There is exactly **one** last seam before delivery: `GmailSender.send`
(`utils/email_sender.py:211-245`). Every outbound in the repo enters there. Inside it, in order:

1. **Governance** — `utils/email_sender.py:220-226`: `send_governance.send_governance_active()`
   then `send_governance.evaluate(message)`; a blocked verdict returns
   `SendResult(status=<verdict.status>, error=<reason>)` with **zero network** (`:224-226`).
2. **Delivery gate** — `utils/email_sender.py:227-229`: `EMAIL_SEND_ENABLED`
   (`utils/email_sender.py:58`, env-read at import, strict-truthy `_env_truthy` at `:48-51`).
   Off ⇒ `SendResult(status="stubbed")`, zero network.
3. **Provider** — `utils/email_sender.py:230-242`: builds the Gmail service
   (`utils/gmail_client.py:67-86`, lazy google imports, fail-soft `None`), encodes RFC822
   (`_build_raw`, `:177-209`), calls `service.users().messages().send(...)`. No creds ⇒
   fail-soft `status="error"` (`:233-235`), never a silent stub.

The governance stack itself (`utils/send_governance.py:355-369`, `evaluate`) is:
**suppression** (`:286-300`, domain-level, beats everything) → **allowlist** (`:302-317`,
every to+cc domain must be listed; empty list blocks everything, fail-closed) → **caps**
(`:320-352`: global per-UTC-day cap `SEND_GOVERNANCE_DAILY_CAP` default 10 at `:271`/`:273`,
and per-`(supplier_domain, part_key)` open-RFQ cap default 1 at `:272`/`:274`).
Flag: `SEND_GOVERNANCE_V1` (`utils/send_governance.py:79-82`). Store:
`data/send_governance.sqlite`, DDL at `:49-70`, `_DB_PATH` monkeypatch seam at `:47`.

**Release queue** is *not* inside `evaluate` — it is an API-layer structural gate:
with governance active the direct send endpoint refuses (`api_server.py:4915-4923`, 409) and
delivery happens only via `POST /api/admin/send-governance/release-queue/release`
(`api_server.py:3673-3721`), which calls `rfq_send.send_rfq(..., released_by=...)`
(`api_server.py:3698-3705`). The reject sibling is `api_server.py:3783`.

### Where the `MailProvider` adapter slots in — below governance, without moving it

`EmailSender` is already an ABC (`utils/email_sender.py:131-138`) with one method
`send(EmailMessage) -> SendResult`. The clean insertion is **inside `GmailSender.send`'s
provider step (`:230-242`) only** — i.e. keep lines `211-229` (governance, then delivery gate)
exactly where they are and make the *transport* below them selectable:

```
GmailSender.send()                  # the seam every caller uses — UNCHANGED position
  |- 220-226  governance            # unchanged
  |- 227-229  EMAIL_SEND_ENABLED    # unchanged
  \- 230-242  transport  <-- NOTIFICATIONS_V1 off: gmail_client (today)
                             NOTIFICATIONS_V1 on : SesProvider / FakeProvider
```

Slotting it anywhere *above* line 220 (e.g. a new `SesSender(EmailSender)` chosen by the
caller) would place a provider **beside** governance rather than below it, and the brief's
D1/guardrail "nothing bypasses governance" would then depend on every future call site
remembering to re-run `evaluate`. The five call sites that would each have to remember are
listed in G2. **Recommendation: one transport-selection point at `utils/email_sender.py:230`,
no second `EmailSender` subclass on the send path.**

`EmailMessage` (`:84-110`) already carries `metadata: dict` (`:98`) and
`all_recipients` (`:101-110`, what governance judges — `utils/send_governance.py:201-209`), so
`configuration_set` / `notification_id` tags ride in `metadata` without a signature change.
`SendResult` (`:113-128`) already has `message_id` / `thread_id` / `error` / `sent_at` —
`provider_message_id` maps onto `message_id`.

---

## G2. `sent_messages` ledger — schema, writers, and why auth mail is absent (D9)

**Schema:** `utils/supplier_registry.py:110-127` (`_SENT_MESSAGES_DDL`) — `id, run_id,
supplier_domain, vendor_name, recipients_to_json, recipients_cc_json, subject, body,
message_id, thread_id, status, approved_by, sent_at, created_at`. Later columns added by the
idempotent PRAGMA-driven `_migrate` (`:452-533`): `part_key` (`:475-480`),
`released_by`/`released_at` (`:481-486`), `status_history_json`/`template_version`/
`status_updated_at` (`:487-493`). Store: `data/supplier_registry.sqlite`, `_DB_PATH` at `:88`.

**Writers — there are exactly two, both in one module:**
- `utils/rfq_send.py:196-203` — the governance-active *record-before-attempt* row at status
  `"released"`.
- `utils/rfq_send.py:244-252` — the legacy (flag-off) record-after row.

Both call `supplier_registry.record_sent_message` (`:975-1038`). Transitions go through
`update_sent_message_status` (`:1082-1125`, appends to `status_history_json`).

**Readers that matter to this arc:** `count_send_attempts_utc_day` (`:1169-1185`, the daily
cap input, counts `SEND_ATTEMPT_STATUSES = ("sent","stubbed","error")` at `:1076`),
`count_open_rfqs` (`:1187-1199`, keyed on `(supplier_domain, part_key)`,
`OPEN_RFQ_STATUSES = ("sent","stubbed")` at `:1079`), `sent_messages_digest` (`:1127-1167`),
and — critically for D5 — `_supplier_open_requests` (`api_server.py:6328-6372`), which is
what the portal inbox renders.

**Why arc 2's auth mail didn't ledger.** `send_magic_link_email`
(`utils/supplier_accounts.py:958-992`) builds an `EmailMessage` and calls
`GmailSender().send(msg)` at `:989` — so suppression and the allowlist **do** run for real
(arc 2 review R5 confirmed this) — but it never calls `record_sent_message`. The ledger write
lives in `rfq_send`, not in the sender. Consequence, recorded verbatim in
`SUPPLIER_IDENTITY_REVIEW.md:164-175` as **arc 2 review finding 2**: link sends are exempt
from **cap accounting** entirely; the only volume control is the in-process fixed-window
limiter (`api_server.py:6576-6611`, 3/email + 20/IP per 600s), which resets on restart and
does not span workers.

That review also records the *reason* the builder left it out (FB4): a magic-link burst must
not starve the RFQ daily cap of 10. D9 instructs the opposite (write the rows so caps apply).
**Both can hold only if auth mail gets its own cap class** — see FINDING F2. D9 is explicit
that the rows are written, so the arc proceeds on that; the cap-class shape is what F2 flags.

**Other `GmailSender` call sites that also never ledger** (the full list, for D1/D9 scope):
`utils/procurement_agent/tier1_notify.py:201` (Tier-1 FYI),
`api_server.py:5742` (intake reply sink), `api_server.py:6246` (quote ack),
`utils/supplier_accounts.py:989` (magic link). Only `rfq_send` writes the ledger.

---

## G3. Bounce handling — entry points and the suppression side-effects to reuse (D8)

- **Parser (pure, no I/O):** `utils/bounce_parser.py:90` `parse_bounce(raw)` → `BounceNotice`
  or `None`. Hard/soft classification from `Action:` / `Status:` (`:33-36`). Returns `None`
  for a non-bounce — never a false positive.
- **Processor:** `utils/bounce_processor.py:80-129` `process_bounces(reader)`.
  - match precedence `message_id → thread_id → (failed address ∈ recipients AND domain match)`
    (`:41-60`);
  - soft/transient ⇒ **not cleared**, logged (`:106-110`);
  - unmatched ⇒ **nothing cleared** (`:100-104`);
  - address matching neither current primary nor generic ⇒ ambiguous, not cleared (`:63-77`,
    `:113-118`).
- **The suppression side-effect to reuse:** `supplier_registry.mark_contact_bounced(domain,
  which)` (`utils/supplier_registry.py:869-894`) — sets `contact_email = NULL,
  contact_status='bounced'` (generic) or the `primary_contact_*` pair. `recipient_set`
  (`:915-944`) then excludes it, so the send path degrades automatically.
- **Ledger stamp:** `utils/bounce_processor.py:121-125` sets the matched `sent_messages` row
  to `"bounced"`, but **only when `SEND_GOVERNANCE_V1` is active**.

**Two structural facts D8 must respect:**

1. Today's suppression model is **contact-address-on-a-supplier-row**
   (`suppliers.contact_email` / `primary_contact_email`). There is **no per-member
   suppression** — `supplier_members` (`utils/supplier_accounts.py:205-218`) has
   `status ∈ {ACTIVE, PENDING, REVOKED}` (`:105-108`) and no suppression column. D8's
   "mark the member email SUPPRESSED" therefore needs new persistence (T1), not a reuse.
2. `send_governance.suppression_add` (`utils/send_governance.py:216-234`) is **domain-level**
   and permanent-until-admin. Routing a single member's hard bounce into it would silence
   **every** member at that supplier plus all RFQ mail to them. See FINDING F3.

---

## G4. Tier-1 notify — what it sends today, and how RFQ_NEW would attach

**What it is:** `utils/procurement_agent/tier1_notify.py:115-188` `notify_tier1(matches, *,
run_id, cap, sender)`. Gated on `TIER1_V2` (`:61-63`; off ⇒ `[]` at `:141-142`).
Threshold = brand-match OR core-class (`:70-82`); per-RFQ cap 6 (`:58`, applied `:160-167`).
17 tests (`utils/procurement_agent/tests/test_tier1_notify.py`; verified — `grep -c "def test_"`
returns 17, matching the brief).

**What it actually sends:** `_send_notify` (`:204-244`) — an **FYI**, not an RFQ. Body at
`:223-232` ("Arkim matched an active procurement request… No action is required unless Arkim
follows up with a formal RFQ"). Recipients come from
`supplier_registry.assemble_recipient_set(match.domain)` (`:218`, i.e. the registry's free
cascade → a constructed `sales@<domain>`, `utils/supplier_registry.py:946-971`) — **not** from
supplier-account members. It records a `supplier_notifications` row
(`utils/supplier_registry.py:393-409` DDL, `record_supplier_notification` at `:1984`), and
**writes no `sent_messages` row**.

**Only call site:** `api_server.py:1729-1734`, inside the background sourcing write-back,
fail-soft-wrapped.

**How RFQ_NEW attaches — the honest answer: it does not attach cleanly.** The chain D5/D6
needs is:

```
rfq_send.send_rfq  ->  sent_messages row (OPEN_RFQ_STATUSES)
                   ->  _supplier_open_requests (api_server.py:6328-6372)
                   ->  the portal inbox row the supplier can VIEW
                   ->  RfqView  ->  "seen"  ->  escalation does not fire
```

`tier1_notify` sits outside that chain: no `sent_messages` row, therefore no inbox row,
therefore nothing for `RfqView` to record, therefore `viewed_in_portal` can never become true
for a notification raised there. Per D6 every such notification would take a reminder at 4h
and a concierge alert at 24h, 100% of the time. T4's own signature
(`notify_rfq_new(rfq, account)`) wants an *rfq* and an *account*; `notify_tier1` has neither —
it has a `Tier1Match` and a `run_id`. **This is OPEN QUESTION Q1.**

---

## G5. Arc 3 inbox — where `RfqView` must be written server-side

**Frontend (for context only — no client ping is needed or wanted):**
`frontend/src/app/supplier/requests/requests-screen.tsx:44` renders
`frontend/src/app/portal/[token]/open-requests.tsx:47` (`OpenRequests`), which fetches via
`mode.getOpenRequests()` (`open-requests.tsx:69`) in a `useEffect` (`:82`). The two modes
resolve to `getSessionOpenRequests` (`frontend/src/lib/supplier-api.ts:225-230`) and the
token equivalent in `frontend/src/lib/portal-api.ts`.

**Server side — two doors, one shared read service (the write point):**
- `GET /api/supplier/requests` — `api_server.py:7006-7015`. Domain from the validated session
  (`session["account"]["supplier_domain"]`, `:7013`); **`member_id` is available here** as
  `session["member_id"]`.
- `GET /api/portal/{token}/open-requests` — `api_server.py:6376-6387`. Domain from the claim
  token (`:6385`); **no member** — this is D5's "member_id null + supplier_domain" case.
- Both call `_supplier_open_requests(dom)` (`api_server.py:6328-6372`), which already
  enumerates exactly the `(run_id, sent_at)` rows a view would record (`:6344-6367`).

**Recommendation:** write `RfqView` in the two *route handlers* (which know the credential and
therefore the `member_id`), over the rows returned by `_supplier_open_requests` — not inside
the shared service, which deliberately knows nothing about the caller's identity and is
documented as one assembly for two doors (`:6328-6341`).

**There is no RFQ *detail* route.** `OpenRequests` renders the list plus an inline quote form;
no `/api/supplier/requests/{run_id}` exists (full route inventory checked —
`api_server.py:7006` is the only session-side requests route). D5's "a request or its detail"
therefore has exactly one server-side trigger today: the list read.

---

## G6. Admin review queue — the pattern RFQ_ESCALATION should reuse

Three existing patterns, in ascending order of fit:

1. **`review_items` generic queue** — table DDL `utils/supplier_registry.py:136-152`, surfaced
   by `GET /api/admin/review-queue` (`api_server.py:3801-3811`, excludes
   `kind="unmatched_reply"`). Rows carry `kind`, `status`, `payload_json`, `resolved_at`.
2. **`unmatched_reply` triage** — its own endpoints `GET /api/admin/unmatched-replies`
   (`api_server.py:3817-3845`) + `POST .../{item_id}/dismiss` (`:3848-3865`). The dismiss
   handler is the closest analogue of T12's *acknowledge*: **status flip only, never a
   delete** (`:3853-3865`; 404 unknown / 422 wrong kind / 409 already resolved).
3. **Arc 2 pending memberships** — `GET /api/admin/supplier-members/pending`
   (`api_server.py:7087-7097`) + approve/reject (`:7099-7145`). Note the **gate ordering
   convention**: the feature-flag check runs *before* `require_admin` (`:7092-7093`), so
   flag-off renders the route absent (404) even to a valid admin token.

**Admin auth:** `require_admin` (`api_server.py:3441`), bearer `ARKIM_ADMIN_TOKEN` (`:3449`).

**Frontend:** `frontend/src/app/admin/page.tsx:42-55` — a `TABS` array of
`{id, label, path, listKey}` driving a generic table; a new tab is one entry plus the `Tab`
union at `:34-36`. Arc 2's pending-membership queue has **no** frontend tab (API-only), so
T12 adding a tab would be new surface, not a copy of arc 2.

**Verdict for T1:** there is **no reusable concierge-alert table**. `review_items` is
extraction-shaped (`manufacturer` / `part_number` / `confidence` / `raw_source`) and its
`unmatched_reply` rows already needed a second endpoint to avoid polluting the main queue. A
dedicated `ConciergeAlert` store (T1's conditional) is the right call, with the
`unmatched-replies` list + acknowledge endpoints copied as the surface shape.

---

## G7. Persistence + migrations — the convention to follow

Arc 2's I1 finding still holds exactly (`SUPPLIER_IDENTITY_REPORT.md:10-52`), re-verified
against current line numbers:

- **Convention A (SQLAlchemy ORM)** — run/app state only:
  `utils/procurement_agent/state/persistence.py`, `Base.metadata.create_all` at
  `persistence.py:229` and `api_server.py:285`; hand-rolled idempotent `ALTER`s in
  `api_server._migrate_schema` (`api_server.py:288-321`). No Alembic anywhere.
- **Convention B (standalone raw-sqlite3 store per module)** — every supplier-facing surface
  since Night 6, and **what arc 4's new tables must use**:
  - own module + own `data/*.sqlite` + `_DATA_DIR` / `_DB_PATH` module attrs as the
    monkeypatch seam (`utils/supplier_accounts.py:185-186`, `utils/send_governance.py:46-47`,
    `utils/supplier_registry.py:87-88`);
  - DDL as module-level strings, `CREATE TABLE IF NOT EXISTS` executed on **every**
    `_get_conn()` (`utils/supplier_accounts.py:285-297`, `utils/supplier_registry.py:527-533`);
  - PRAGMA-driven idempotent `_migrate` for later columns
    (`utils/supplier_registry.py:452-533`);
  - uuid4 string PKs, ISO-8601-UTC TEXT timestamps, an `is_test INTEGER NOT NULL DEFAULT 0`
    provenance column (`utils/supplier_accounts.py:200`, `:215`, `:237`, `:251`, `:265`),
    `contextlib.closing` connections, fail-soft public functions (return `None` / `[]` /
    `False`, never raise);
  - constraints a plain UNIQUE cannot express go in a **partial unique index**
    (`utils/supplier_accounts.py:225-228`) — the model for D3's idempotency key
    `(provider_message_id, event_type)`.

The monotonic-state-ladder and absorbing-terminal rules of D3 have a precedent to copy:
`_tier1_can_transition` (`utils/supplier_registry.py:441-449`) — a pure, no-I/O transition
predicate over a declared transition map, table-tested.

---

## G8. Verify page behaviour — **it POSTs on load** (FINDING, not changed)

`frontend/src/app/supplier/verify/verify-screen.tsx:58-77`: a `useEffect` reads the token from
`useSearchParams()` (`:62`) and immediately calls `verifyMagicLink(token)` (`:69`) — **no user
gesture**. A `useRef` guard (`:56`, `:59-60`) exists only to stop React 18 StrictMode
double-invocation burning the single-use token twice.

Per GATE RULINGS this is reported and **not changed in this arc**. Assessment, for whoever
rules on it: the exposure is narrower than a plain auto-GET, because the token is consumed by
a `POST` issued from JavaScript — a link scanner that fetches the URL without executing JS
does not burn it. It is still one JS-executing prefetcher away from a consumed token. D2
(`gofer-auth`, tracking OFF) removes the *SES-introduced* prefetch vector; this screen is the
residual one. See FINDING F1.

---

## G9. Config surface — the pattern to mirror

**Flags (backend).** One idiom, repeated: a module-local strict-truthy `_env_truthy`
(`utils/send_governance.py:73-76`, `utils/supplier_accounts.py:78-82`, `api_server.py:24`)
accepting only `1/true/yes/on`. Two binding styles coexist:

- **live read** per call — `supplier_accounts_active()` (`utils/supplier_accounts.py:84-88`),
  `_supplier_accounts_enabled()` (`api_server.py:91-94`), `send_governance_active()`
  (`utils/send_governance.py:79-82`). **This is the style to copy** (monkeypatch-friendly).
- import-bound module constant — `EMAIL_SEND_ENABLED` (`utils/email_sender.py:58`),
  `TIER1_V2` (`utils/supplier_registry.py:85`); these need a conftest `setattr` pin to be
  test-safe (`utils/procurement_agent/tests/conftest.py:76-84`), which arc 4 must not edit
  (F4) — another reason to use the live-read style.

**Non-flag config.** Three shapes, all present:

- typed reader with a safe default: `_env_int(name, default)` (`api_server.py:498-506`), used
  at `api_server.py:6576`.
- named env constant + default constant: `_PORTAL_BASE_URL_ENV` / `_DEFAULT_PORTAL_BASE_URL`
  (`utils/supplier_accounts.py:947-948`), resolved at `:951-955`.
- `ENV_*` module constants for a provider adapter — the closest precedent to a `MailProvider`:
  `utils/search_providers.py:139-142` (`ENV_PARALLEL_API_KEY` / `_BASE_URL` / `_MODE`, all
  constructor-overridable; key absent ⇒ no-op `[]` at `:147-149`) with runtime provider
  selection by env at `:225`.

**Frontend flags.** `frontend/src/lib/flags.ts` — `isOn()` mirrors `_env_truthy` (`:24-27`);
each flag is a named function reading a **literal** `process.env.NEXT_PUBLIC_*` member
expression (`:38-40`; the rule is documented at `:16-20` — a computed key is never inlined by
SWC). `NEXT_PUBLIC_NOTIFICATIONS_V1` is one more exported function in that file.

**`.env.example` is effectively empty** (a single line, `TAVILY_API_KEY=`). It is not a
working config inventory, so the arc's env list belongs in this report rather than being
mirrored there.

**No webhook precedent exists.** `POST /api/intake/email` (`api_server.py:5790-5840`) is the
only comparable public unauthenticated POST and it has **no signature verification** — a flag
gate only (`:5806`). The nearest security posture to copy is the token routes: uniform
`404 {"detail":"Not Found"}` for flag-off *and* every rejection (`api_server.py:5537-5553`,
`:6037-6046`, `:6072-6085`), rate-limit applied **before** the token check, and
`_portal_response_headers` (`api_server.py:5570-5577`: `Referrer-Policy: no-referrer`,
`Cache-Control: no-store`). D4 asks for a uniform **403** instead — a deliberate, documented
divergence from the 404 house pattern.

**`DEMO_MODE` interaction:** the allowlist middleware is deny-by-default
(`api_server.py:245-254`). A new `/api/webhooks/ses` route is therefore automatically **403 in
demo mode** with no action needed — correct, and it must stay off that list.

### Required env config (HUMAN VERIFICATION list)

| Var | Purpose | Default / behaviour when unset |
|---|---|---|
| `NOTIFICATIONS_V1` | backend arc flag | off — Gmail path, no Notification rows, webhook 404, scheduler no-ops |
| `NEXT_PUBLIC_NOTIFICATIONS_V1` | frontend surface flag | off — no new UI |
| `AWS_REGION` | SES/SNS region | unset ⇒ `SesProvider` no-ops |
| `SES_CONFIGURATION_SET_NOTIFICATIONS` | D2 tracking-ON set (`gofer-notifications`) | unset ⇒ no config set sent |
| `SES_CONFIGURATION_SET_AUTH` | D2 tracking-OFF set (`gofer-auth`) | unset ⇒ auth mail must not send (fail-closed per D2) |
| `SES_FROM_ADDRESS` | verified SES identity sender | falls back to `gmail_client.gmail_sender_address()` semantics |
| `SES_SNS_TOPIC_ARN_ALLOWLIST` | D4 comma-separated TopicArn allowlist | empty ⇒ every envelope 403 (fail-closed) |
| `ESCALATE_REMIND_HOURS` | D6 reminder threshold | 4 |
| `ESCALATE_ALERT_HOURS` | D6 alert threshold | 24 |

AWS credentials are deliberately **not** an env var: the ECS task role supplies them (the
brief's infra section) and `boto3` resolves them from the standard chain. No credentials ⇒
`SesProvider` no-ops cleanly (D1 / CLAUDE.md §9).

---

## FINDINGS

**F1 — the verify page auto-submits the magic-link token on load.**
`frontend/src/app/supplier/verify/verify-screen.tsx:58-77` (POST fired from `useEffect`, no
gesture; StrictMode guard at `:56`). Reported per GATE RULINGS, **not changed** — the arc-3
test `frontend/src/app/supplier/verify/__tests__/verify-screen.test.tsx` pins the auto-verify
behaviour, so changing it would fail a protected test. Arc 4b if wanted. D2's `gofer-auth`
set removes the SES-tracking prefetch vector regardless; this is the residual one.

**F2 — D9's ledger write makes auth mail consume the RFQ daily cap unless it gets its own cap class.**
`utils/supplier_registry.py:1076` (`SEND_ATTEMPT_STATUSES`) + `:1169-1185`
(`count_send_attempts_utc_day`) count **every** `sent_messages` row with status
`sent|stubbed|error` against `SEND_GOVERNANCE_DAILY_CAP` (default 10,
`utils/send_governance.py:273`, enforced at `:334-338`). Writing magic-link/invite rows as D9
requires means ten sign-in links exhaust the entire day's send budget and every subsequent RFQ
is `cap_blocked`. This is precisely the failure arc 2's builder avoided by *not* ledgering
(`SUPPLIER_IDENTITY_REVIEW.md:164-175`). **Recommended resolution within D9's letter:** write
the row (so the ledger, digest and audit trail are complete — D9's stated goal) with a
`message_class` discriminator, and have the daily-cap query filter on it, with a separate
transactional cap. Additive, flag-gated, changes no flag-off behaviour. Flagged rather than
assumed silently because it changes what "caps apply" means.

**F3 — D8's member-level suppression must not route into domain-level governance suppression.**
`send_governance.suppression_add` (`utils/send_governance.py:216-234`) suppresses a whole
**domain**, permanently until an admin lifts it, and is checked first for every message
(`:286-300`). One member's hard bounce silencing every other member at that supplier *and* all
RFQ mail to them would be a severe over-reaction. D8's "mark the member email SUPPRESSED"
therefore needs address-level state in the arc's own store (T1); `supplier_members`
(`utils/supplier_accounts.py:205-218`) has no column for it today.

**F4 — `conftest.py` must NOT be edited to pin `NOTIFICATIONS_V1`; there is an established precedent for the alternative.**
`utils/procurement_agent/tests/conftest.py:71-84` is the flag pin list. Editing it violates
prime directive 1 / reviewer R1. Arc 2 hit this exact conflict, declined the edit (its FB2),
took the reviewer MINOR (`SUPPLIER_IDENTITY_REVIEW.md:177-178`), and pinned the flag in a
**separate follow-up commit after approval** (`10a048e`, "test: pin SUPPLIER_ACCOUNTS_V1 in
conftest flag list (arc 2 review finding 3)"). Arc 3 then built the reusable alternative:
`utils/procurement_agent/tests/_arc3_session_fixtures.py` (leading underscore ⇒ not collected;
shared fixtures importable into new test files — rationale documented at
`_arc3_session_fixtures.py:5-10`). **Arc 4 will mirror that: `_arc4_notifications_fixtures.py`,
conftest untouched, and a post-approval pin commit recommended.**

**F5 — MEMBER_INVITE mail does not exist today; T5 creates it rather than migrating it.**
`supplier_accounts_rbac.invite_member` (`utils/supplier_accounts_rbac.py:101-...`) and
`POST /api/supplier/members/invite` (`api_server.py:7217-7237`) create the member row and an
audit row — **no email is sent** anywhere in that path (verified: the only `GmailSender` call
sites in the repo are the five listed in G2). T5's "magic-link **and invite** mail through the
adapter" therefore means one migration plus one brand-new outward-facing channel. In scope per
D7/T5, but it is new mail to real people, not a like-for-like move — worth the human's
attention before flags go on in a live environment.

**F6 — `notify_tier1` is dormant by default, so any RFQ_NEW hook placed there is double-gated.**
`utils/procurement_agent/tier1_notify.py:141-142` returns `[]` whenever `TIER1_V2` is off, and
`TIER1_V2` is default-off and pinned off for the whole suite
(`utils/procurement_agent/tests/conftest.py:73`, `:78`). A notification path hooked only there
would need `TIER1_V2` **and** `NOTIFICATIONS_V1` on to do anything — including during the live
verification the brief describes. Relevant to Q1.

---

## OPEN QUESTION — Q1 (blocks T4; cascades into T7, T8, T12)

**Which seam owns `RFQ_NEW`: `rfq_send` (where an RFQ actually exists), or `tier1_notify`
(where the brief's G4/T4 points)?**

The brief answers this two ways and they are not compatible:

- **T4 says:** "`notify_rfq_new(rfq, account)` … **Hook into the Tier-1 notify path (G4)**."
- **D5/D6 say:** escalation keys off `viewed_in_portal OR clicked`, where `viewed_in_portal`
  is written when "the session inbox renders a request".

The mechanical facts (G4, G5) are that the portal inbox renders rows derived **only** from
`sent_messages` (`api_server.py:6328-6372`, filtered to `OPEN_RFQ_STATUSES`), and
`tier1_notify` writes no `sent_messages` row (`utils/procurement_agent/tier1_notify.py:204-244`
— it sends an FYI and records a `supplier_notifications` row instead). So:

- Hook at **`tier1_notify`** (the literal T4 reading): every RFQ_NEW is for a subject the
  supplier has nothing to view, `viewed_in_portal` can never become true, and D6 fires a
  reminder at 4h and a concierge alert at 24h for **100%** of notifications. The escalation
  ladder becomes noise. Also double-gated behind `TIER1_V2` (F6).
- Hook at **`rfq_send`** (`utils/rfq_send.py:128-271`, reached from the release queue at
  `api_server.py:3698-3705`): the notification's subject is a real RFQ with a real inbox row,
  `RfqView` works, and the ladder means what D6 says it means. But this is not the path T4
  names, and it fires on *release*, which under `SEND_GOVERNANCE_V1` is a concierge action.

T4's own signature — `notify_rfq_new(`**`rfq`**`, account)` — takes an RFQ and an account;
`notify_tier1` has neither (it has a `Tier1Match` and a `run_id`), which is further evidence
that the two readings are genuinely different systems rather than a wording slip.

**These are materially different builds** (different trigger, different subject ref for
`RfqView` in T7, different escalation semantics in T8, different alert volume in T12), so per
the GATE RULINGS' final clause the gate stops rather than guesses.

**Recommended ruling, if one sentence will do:** trigger `notify_rfq_new` at the `rfq_send`
seam (the RFQ that creates the inbox row), and leave `tier1_notify` as the separate Tier-1 FYI
it is today — optionally emitting a distinct, non-escalating notification kind so the FYI is
still tracked without entering the D6 ladder.

---

## WHAT WAS NOT DONE

Nothing was built. No source file, test file, `conftest.py`, dependency manifest or frontend
file was modified. `boto3` was **not** added (`uv.lock` contains zero `boto` entries;
runtime deps at `pyproject.toml:5-28`) — that is T2's first act, once Q1 is ruled.

---

## BUILD NOT STARTED — Q1 STILL UNRESOLVED (2026-09-20)

The builder was invoked for T1-T12 and stopped without writing any code: this gate report ends
on the unresolved STOP question Q1 (which seam owns `RFQ_NEW` — `rfq_send` or `tier1_notify`),
no ruling on it exists anywhere in the repo (`VERDICT.txt` at the root is a stale arc-3/frontend
artefact, last written by `e5aff80`, not an arc-4 ruling), and the brief's GATE RULINGS pre-authorise
no answer to it. Per the GATE RULINGS' final clause and the builder's own stop rule, a stopped arc
with a clear question is the correct outcome. T1-T12 remain unbuilt; no source, test, conftest,
dependency-manifest or frontend file was modified. Resolve Q1 (the recommended ruling is stated
above) and re-invoke the builder.

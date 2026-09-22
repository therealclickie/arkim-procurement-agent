# MORNING REPORT — NIGHT 10 (SEND_GOVERNANCE_V1)

**Status: COMPLETE — T1-T7 all landed (no T0 needed), 8 commits, suite 2122 passed / 73
skipped (baseline 2024/73 + 98 new tests), green at every commit. ZERO deliveries at any
point: EMAIL_SEND_ENABLED untouched, .env untouched, no credentials handled. NOT PUSHED.**

Branch: `feature/send-governance-overnight` off `test/flag-on-integration` @ `1ee7003`.

| Task | Commit | What |
|---|---|---|
| T1 | `bb0f6e7` | Allowlist enforcement at the delivery seam, fail-closed, admin CRUD |
| T2 | `07f727e` | Daily cap (10, env-tunable) + per-supplier-per-part open-RFQ cap (1) |
| T3 | `2f313ec` | Suppression list — checked first, beats allowlist, permanent until admin removal |
| T4 | `3e26d90` | Concierge release queue — releases run the full stack; legacy direct-send 409s flag-on |
| T5 | `a3d8b1b` | Inbox scoping — intake mail and RFQ replies can never cross |
| T6 | `e25fdc9` | Ledger: record-BEFORE-delivery, status transitions, template version, daily digest |
| T7 | `8d71479` | Acceptance suite: precedence truth table, no-bypass, fail-closed, kill-switch, parity |
| — | `5464ac6` | chore: undo an accidental CRLF rewrite so the review diff is only the real changes |

Pre-flight: cwd verified (`C:\dev\_Arkim\Arkim Procurement Agent Prototype`), spec + brief read
in full (the spec was missing at first kickoff — the earlier halt report was replaced by this
file after the spec was dropped at 21:22 and the run re-issued). BASE_HEAD on
`test/flag-on-integration`: `1ee7003`. Baseline with `RUN_CAPTURE=1 INTAKE_TYPE_AWARE=1
SCORING_V2=1`: **2024 passed / 73 skipped** — matches. Work branch:
`feature/send-governance-overnight`.

---

## I1 — Send-path audit: THE CALL GRAPH

**There is exactly ONE delivery function:** `GmailSender.send` —
`utils/email_sender.py:211`. The only real network send in the codebase is
`service.users().messages().send(...)` at `email_sender.py:223`. The gate check
(`EMAIL_SEND_ENABLED`, strict-truthy, default OFF, read at `email_sender.py:58`) is the FIRST
statement of that function (`:212`): flag off ⇒ `SendResult("stubbed")`, zero network. Flag on
+ no creds ⇒ `SendResult("error")`, never a silent half-send. `utils/inbox_reader.py` uses the
Gmail service READ-only (`messages().get/list`, lines 139/158/225) — no send.

**Every path that reaches `GmailSender.send`:**

```
[1] POST /api/rfq-drafts/{id}/send        api_server.py:4375  (RFQ wiring A2 — the RFQ path)
      → persistence.can_transition_draft gate: only 'approved' drafts    api_server.py:4395
      → rfq_send.send_rfq                  utils/rfq_send.py:88
          → HITL gate: Approval REQUIRED or refuse                rfq_send.py:115
          → recipients from supplier_registry.recipient_set       rfq_send.py:110-112
          → EMAIL_SEND_ENABLED check (module attr, call-time)     rfq_send.py:136
          → sender.send(message)  ⇒  GmailSender.send             rfq_send.py:138
          → supplier_registry.record_sent_message (AFTER attempt) rfq_send.py:155  ⚠ see below
          → write_audit_log                                       rfq_send.py:163

[2] sourcing background → tier1_notify.notify_tier1               api_server.py:1487
      → _send_notify → sender.send  ⇒  GmailSender.send           tier1_notify.py:239
      (TIER1_V2-gated FYI notify; records to supplier_notifications, NOT sent_messages)

[3] intake reply sink (_intake_reply_sink)                        api_server.py:~5168
      → GmailSender().send  (INTAKE_CHANNELS_V1-gated ack/clarify replies to CUSTOMERS;
        does not record to sent_messages)

[4] Operator scripts (not on the app path; require env gate + creds to do anything):
      scripts/gmail_send_self_test.py  → GmailSender().send directly
      scripts/outreach_self_test.py    → rfq_send.send_rfq

[X] utils/sourcing_archieved/tier3_outreach.py — carries its own dead EMAIL_SEND_ENABLED=False
    constant ("Outbound email is not implemented"); NO send call exists there. Not a path.
```

**Verdict: no ungated send path exists — every route terminates in the single gate-checked
delivery function. T0 not required.**

Findings against spec §1 (addressed by the build tasks):
- §1(b) **violated today**: `rfq_send` records to sent_messages AFTER the delivery attempt
  (`rfq_send.py:138` send → `:155` record). Paths [2] and [3] never write sent_messages at
  all. T4/T6 restructure to record-before-delivery with status transitions (flag-on).
- sent_messages statuses today: only `sent | stubbed | error` (schema at
  `utils/supplier_registry.py:111`; no releaser/template-version/part fields). T6 adds them
  (additive).

## I2 — Inbox scoping: CONFIRMED

`GmailInboxReader.fetch_replies` queries `in:inbox -from:mailer-daemon -from:postmaster` —
**no recipient filter** (`utils/inbox_reader.py:224`). Intake mail is plus-addressed
(`intake+<tenant>@arkim.ai`, `api_server.py` IntakeEmailInbound; `utils/intake_channels.py:97`
already documents the collision). With both streams live on one mailbox, intake mail WOULD
surface to the RFQ reply reader. The intake adapter itself is already structurally scoped (it
only processes intake-addressed payloads).

Chosen mechanism (T5, flag-gated): recipient scoping on the RFQ reader — Gmail-side query
narrowing (`to:` the RFQ sender address) PLUS a defensive post-parse filter that drops any
message addressed to an `intake+` address (belt and braces; the post-parse filter is what the
mocked-service tests can prove). Reverse direction is already structural; a cross-stream test
locks both.

## I3 — Send-layer surface today

- Draft lifecycle: `rfq_drafts` (persistence.py:184) `drafted → approved → sent` /
  `drafted → rejected`, enforced by `ALLOWED_DRAFT_TRANSITIONS` (persistence.py:391).
  Endpoints: create/get/list/approve/reject (api_server.py:4286-4372), send (4375).
- sent_messages: `utils/supplier_registry.py:111` DDL, `record_sent_message` (:918),
  `get_sent_messages` (:974, fail-soft returns [] — NOT usable for fail-closed cap counting;
  raising helpers added in T2). Admin read exists: GET /api/admin/sent-messages (:3272).
- Reply/bounce attach: `reply_processor.py:70` / `bounce_processor.py:90` consume the reader;
  extracted quotes land in `review_items` with `sent_message_id` join keys (propose-don't-invent).
- Admin surface: bearer-token `require_admin` (api_server.py:3197), fail-closed 503 when
  `ARKIM_ADMIN_TOKEN` unset. Governance admin endpoints reuse this dependency.

## I4 — Allowlist/cap/suppression prior art: NONE (confirmed)

Grep across utils/ + api_server.py: the only "allowlist"s are the curated marketplace registry
and the DEMO_MODE route allowlist — nothing in the send path. Believed-missing confirmed; T1-T3
build it. Seam decision: enforcement goes INSIDE `GmailSender.send` (the last function before
delivery), ahead of the EMAIL_SEND_ENABLED check, so **no caller can bypass it** — including
tier1_notify and the intake sink (uniform governance, no purpose exemptions tonight; flagged
as an open decision below).

---

## What was built (the shape of it)

One new standalone module, `utils/send_governance.py` (typed, tested, own sqlite store at
`data/send_governance.sqlite`, `is_test` marking, audit-logged admin mutations), consulted by
`GmailSender.send` as its FIRST act — ahead of the `EMAIL_SEND_ENABLED` check, at the last
seam before delivery — so **every** caller of the send layer (rfq_send, tier1_notify, the
intake reply sink, operator scripts) passes through:

```
suppression  →  allowlist  →  caps  →  (release, structural)  →  EMAIL_SEND_ENABLED  →  wire
   T3 first      T1 fail-closed   T2         T4                      (untouched)
```

**FAIL-CLOSED is the module's contract** (deliberately the inverse of the §9 provider
fail-soft rule): empty allowlist ⇒ nothing delivers; unreadable store ⇒ blocked at that
stage; junk cap env ⇒ blocked; errors never allow. Governance can only BLOCK — it has no
code path that enables delivery.

Admin surface (bearer `require_admin` + flag-gated, 404 when off):
`/api/admin/send-governance/` → `allowlist` (GET/POST/{domain}/remove), `suppression`
(same shape), `release-queue` (GET; POST `/release` batch; POST `/{id}/reject`),
`digest[?day]`.

## Precedence / property-test evidence (T7, `test_send_governance_acceptance.py`)

- **P1 precedence truth table** — all 8 combinations of (suppressed, allowlisted,
  cap-exhausted) assert the highest-precedence block wins: suppressed → `suppressed`
  regardless of anything else; unallowlisted → `not_allowlisted` even with caps exhausted;
  allowlisted+capped → `cap_blocked`; clean → pass to the gate. Release is structural:
  no Approval ⇒ `not_sent_no_approval` before the stack; flag-on, the ONLY draft→delivery
  route is the release queue (legacy send endpoint 409s — entry-point-tested).
- **P2 no-bypass** — every entry point driven with governance blocking and the delivery gate
  **simulated on** (module-attr monkeypatch only) against a counting mock at the real
  network seam (`users().messages().send()`): direct `GmailSender.send`, `send_rfq`,
  `tier1_notify._send_notify`, `api_server._intake_reply_sink` — **zero provider calls in
  every case**. (Operator scripts call these same two seams; nothing else reaches Gmail —
  see the I1 call graph.)
- **P3 fail-closed** — empty allowlist blocks all domains; a missing store FILE is an empty
  allowlist, not an open door; an unreadable store blocks at the first stage; junk
  `SEND_GOVERNANCE_DAILY_CAP` blocks.
- **P4 kill-switch** — everything released + allowlisted, gate off ⇒ `stubbed`, zero
  provider calls, ledger row carries `released_by` (criterion 4, end-to-end).
- **P5 flag-off parity** — every governance function poisoned-and-never-consulted; the
  legacy stub path never invokes the provider; queue/digest/allowlist/suppression routes
  404; flag-off ledger rows carry no governance columns; T5's flag-off Gmail query is
  asserted byte-identical (`in:inbox -from:mailer-daemon -from:postmaster`).

Composition detail worth knowing: T6's `replied` transition frees T2's per-part open-RFQ
slot — tested (`test_replied_transition_frees_open_rfq_cap_slot`).

## Success criteria — outcomes

1. ✅ I1 call graph published (above); zero ungated paths found ⇒ no T0.
2. ✅ Fail-closed proven (P3 + T1 tests; gate simulated via monkeypatch only).
3. ✅ Precedence property-tested (P1 truth table).
4. ✅ Released draft + gate off ⇒ ledger `stubbed` with releaser identity, end-to-end via
   the release-queue endpoint (`TestReleaseQueue::test_release_records_stubbed_with_releaser_identity`)
   and at the flow level (P4). Zero deliveries all night.
5. ✅ Cross-stream isolation both directions (T5 tests incl. Cc-hidden intake and bare
   `intake@`; reverse via tenant resolution).
6. ✅ Cap exceedance blocks with visible `cap_blocked` status; UTC-day rollover resets
   (tested by rewriting a row's created_at to yesterday).
7. ✅ Flag-off byte-identical (P5 + per-task parity tests); suite ≥ baseline and green at
   every commit: 2054 → 2066 → 2075 → 2084 → 2088 → 2100 → 2122.
8. ✅ `.env` untouched (0 diff lines); no credential handling anywhere. **One nuance to
   review:** the brief also required enforcement "at the last seam before delivery" and a
   stack that ends at `EMAIL_SEND_ENABLED`, which necessarily meant edits in the two files
   that read the gate: `email_sender.py` gained a governance block *above* the unchanged
   gate check (it can only return-early with a block), and `rfq_send.py`'s provider-skip
   condition became `EMAIL_SEND_ENABLED or _governed` (so a blocked verdict records honestly
   instead of masking as "stubbed" — delivery still impossible, the gate inside the sender
   is unchanged). The gate's definition, parse, default, and delivery semantics are
   untouched — enforced by P4/P5 and the conftest safety net, and visible in the diff
   (`git diff 1ee7003..HEAD | grep EMAIL_SEND_ENABLED`).

## THE CURRENT RFQ TEMPLATE (for Tom's copy review — spec §8 item 3)

Source: `utils/procurement_agent/outreach.py::_make_draft` (now stamped `rfq-v1` on every
governance-active ledger row). Rendered with a real part it reads:

```
Subject: Quote Request — {manufacturer} {model} {part_number}

Hello {vendor_name},

We are seeking pricing and availability for the following:

  Manufacturer : {manufacturer or —}
  Model        : {model or —}
  Part Number  : {part_number or —}

Please reply with unit price, lead time, and stock availability.

Regards,
Arkim Procurement
procurement@arkim.ai
```

When the recipient set fell back to a generic inbox (no resolved named contact), this is
appended (`CONTACT_NOMINATION_ASK`):

```
We'd like to keep sending procurement requests for parts like this to the right person.
If there's a specific contact these should go to, please reply with their name, position,
and email.
```

**Review notes for Tom:**
- **There is NO claim-link line in the current template.** The spec (§6) treats "want RFQs
  made easier? claim your profile" as in-scope of the settled supplier-acquisition design,
  but it has never been added to `_make_draft`. Template copy was explicitly out of scope
  tonight — decide the wording, then it's a one-line template change + a version bump to
  `rfq-v2` (the ledger records which version every supplier received).
- The compliance floor (spec §2) wants the sender's real business identity **and address**
  in the template — the current sign-off has identity but no postal address.
- Tone is terse/functional; fine for Phase 0 shadow review, worth a pass before Phase 1.

## Unspecified decisions taken (flagged for morning review)

1. **Governance applies to ALL outbound mail uniformly** — tier1_notify FYIs and intake
   ack/clarify replies are governed (suppression/allowlist/caps), not just RFQs. Rationale:
   purpose-tag exemptions are bypass holes; fail-closed beats convenience; both features are
   flag-gated off in deploy anyway. Consequence: if INTAKE_CHANNELS_V1 goes live with
   governance on, customer reply addresses must be allowlisted (or a deliberate, tested
   exemption designed then). Flagged as a follow-up decision.
2. **Flag-on, the legacy `POST /api/rfq-drafts/{id}/send` 409s** and points to the release
   queue — releaser identity is then always real (never inferred from the approver). The
   brief didn't say what to do with the old endpoint; making it refuse was the only way to
   make "no path bypasses release" structural.
3. **A released-but-stubbed draft stays `approved`** (claim-matches-reality: draft `sent`
   means a message actually went), so in shadow mode released drafts remain in the queue and
   are re-releasable. Each release attempt is its own ledger row. If the queue should
   instead drain on release-while-stubbed, that's a deliberate lifecycle change for later.
4. **"Queued" lives in rfq_drafts** (status `approved` = pending release); sent_messages
   rows begin at `released`. The brief's status list is otherwise implemented verbatim
   (`released/stubbed/sent/error/bounced/replied/cap_blocked/suppressed/not_allowlisted`).
   ("delivered" = the existing `sent` status — kept the established vocabulary.)
5. **Daily cap counts ATTEMPTS (sent/stubbed/error)** — stubbed counts so shadow mode
   exercises real cap behavior; governance-blocked rows never consume cap.
6. **Blocked reasons aren't persisted on the row** (status only; reason is in logs +
   endpoint responses). Add an `error/reason` column later if the digest needs it.
7. **Per-part cap skips messages without a part_key** (notify/intake have no part; the RFQ
   release path always stamps one — test-locked). The daily cap still binds everything.
8. `design/interactions.md` deliberately not updated: flag-off the product behavior is
   byte-identical and there is no user-facing surface; document the governance flow there
   when the flag ships on (avoids documenting a dark feature as current behavior).

## Verification commands

```powershell
$env:RUN_CAPTURE=1; $env:INTAKE_TYPE_AWARE=1; $env:SCORING_V2=1
uv run pytest -q                                                   # 2122 passed / 73 skipped
uv run pytest utils/procurement_agent/tests/test_send_governance.py `
              utils/procurement_agent/tests/test_send_governance_acceptance.py -q   # 98 new
git log --oneline 1ee7003..HEAD                                    # the 8 commits
git diff 1ee7003..HEAD -- .env                                     # empty (criterion 8)
git diff 1ee7003..HEAD | Select-String "EMAIL_SEND_ENABLED"        # gate lines unchanged
```

Handoff to the daytime sequence (brief's parallel track): merge review → Sergei's infra
items → kill-switch drill (allowlist EMPTY — fail-closed protects) → Phase 0 shadow → Tom
populates the allowlist → first concierge release. **NO PUSH performed.**

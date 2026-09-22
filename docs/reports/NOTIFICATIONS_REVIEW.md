# NOTIFICATIONS_REVIEW.md — Arc 4 Review (Claude Fable 5, 2026-09-22)

**Branch:** `arc4/notifications` · **Branch point:** `d20ca685e3033f` · **Verdict: CHANGES_REQUESTED**

One finding forces the verdict: the backend suite is **red today** — a date-dependent test in an
arc-4 test file went red the day after the builder ran it (finding 1). Everything else in the arc
held up under adversarial reading: the security posture of the webhook is real, the governance
tests exercise the real gate, no test touches AWS, and the build report's findings are honest.
The fix is a few lines in one arc-4-owned test file.

---

## Checklist answers R1–R9

**R1 — no `M` on any pre-existing test file: PASS.**
`git diff --name-status d20ca685e3033f HEAD`: 17 `M` entries, every one a source file
(`api_server.py`, `utils/email_sender.py`, `utils/rfq_send.py`, `utils/send_governance.py`,
`utils/supplier_accounts.py`, `utils/supplier_registry.py`, frontend screens/libs,
`pyproject.toml`, `uv.lock`, `design/interactions.md`). All 15 test-side files (11 backend test
files + `_arc4_notifications_fixtures.py` + 3 frontend test files) are `A`. `conftest.py`
untouched — the fixtures module pins `NOTIFICATIONS_V1` explicitly per test
(`_arc4_notifications_fixtures.py:47-76`), mirroring arc 3's precedent.

**R2 — both suites with flags OFF: FAIL (backend), PASS (frontend).**
- Backend, `NOTIFICATIONS_V1=0 uv run pytest -q`: **1 failed, 2805 passed, 73 skipped** —
  `test_notifications_escalation.py::test_running_twice_with_the_same_now_changes_nothing`.
  See finding 1. The failure is date-dependence in an arc-4 test, not a flag-off behaviour leak.
- Frontend, `NEXT_PUBLIC_NOTIFICATIONS_V1=0 npm test`: **197 passed (24 files)** = 172 + 25.
- Webhook 404 when flag off: proven by
  `test_ses_webhook.py:124` (`test_flag_off_the_route_does_not_exist`), which compares status
  *and body* against a genuinely unknown route; handler gate at `api_server.py:7538-7539`.
- No Notification rows when flag off: proven by
  `test_notifications_rfq_new.py:267` (`test_flag_off_writes_no_notification_rows`) and
  `test_notifications_auth_mail.py:206` (no ledger row, no notification, Gmail path status
  `stubbed`). All of these passed in my run.

**R3 — no live boto3 clients in tests: PASS.**
Grep over `utils/procurement_agent/tests/` finds exactly two `boto3.client(` sites:
`test_mail_provider.py:48` and `test_notifications_auth_mail.py:75`. Both construct with an
inline region **and inline fake credentials** (`aws_access_key_id="test"`) and are immediately
wrapped in a botocore `Stubber` (`:50` / `:77`). No `boto3.Session(` anywhere. Belt-and-braces:
`test_ses_webhook.py:394` (`test_handle_envelope_never_reaches_the_network`) monkeypatches
`urlopen` to `pytest.fail`, and `boto3` is imported lazily in the one production site
(`utils/mail_provider.py:301`).

**R4 — webhook handler read directly: PASS on all three properties.**
- Cert URL restricted to amazonaws.com: `utils/ses_webhook.py:112-130` — parsed hostname,
  `https` scheme required, suffix anchored on a dot (`.amazonaws.com`), so
  `sns.amazonaws.com.attacker.test`, `notamazonaws.com` and `http://` all fail; table-tested at
  `test_ses_webhook.py:316-328`.
- TopicArn checked BEFORE SubscribeURL is visited: `handle_envelope` order is envelope type
  (`ses_webhook.py:301-302`) → topic allowlist (`:305`) → signature (`:308`) → only then
  `confirm_subscription` (`:311-313`). Exact-match allowlist, no prefix logic (`:84-88`),
  fail-closed on empty (`:74-81`). The ordering is asserted as a *fact about what the server
  fetched* — `test_ses_webhook.py:280-297` posts a foreign-topic and a bad-signature
  confirmation and asserts the fetch recorder stayed empty. `confirm_subscription` additionally
  re-checks the SubscribeURL host itself (`:261-263`, tested at `:300-309`).
- Idempotency keyed on messageId + event type: partial unique index
  `ux_notification_events_idempotency` on `(provider_message_id, event_type)`
  (`utils/notifications_store.py:229-231`); the claim is the INSERT itself
  (`claim_provider_event`, `:678-697`), taken before any state change
  (`utils/notifications.py:461-462`). Replay and pair-key semantics tested at
  `test_ses_webhook.py:222-249`; monotonic-ladder protection against out-of-order events at
  `:252-265`. Uniform 403: all 12 rejection shapes compared on status, body bytes and security
  headers (`:194-206`).

**R5 — governance test exercises the REAL gate: PASS.**
`test_notifications_governance.py` drives `send_governance.evaluate` through the real
`GmailSender.send` against a real tmp_path governance store; the only double is `FakeProvider`,
which sits *below* both gates (`utils/email_sender.py:230-246` — provider selection happens
after governance at `:220-226` and `EMAIL_SEND_ENABLED` at `:227-229`, so a message reaching
the fake has provably passed the real gate). Non-allowlisted → `not_allowlisted` with empty
outbox (`:44-50`); notification state lands `SUPPRESSED` via the real verdict (`:67-75`);
suppression beats allowlist (`:60-64`); real daily cap respected (`:88-99`). The allowlist
helper writes to the real store (`_arc4_notifications_fixtures.py:91-96`). Structurally, D1
holds by call-graph position, not convention — the adapter is transport selection inside the
existing seam, below governance, exactly as the gate's G1 recommended.

**R6 — auth config set asserted from the Stubber-captured call: PASS.**
`capture_ses` wraps the real client's `send_email` and records kwargs
(`test_notifications_auth_mail.py:66-94`); assertions compare
`captured[0]["ConfigurationSetName"]` against `AUTH_SET` — the value the fixture itself
injected into `SES_CONFIGURATION_SET_AUTH` (`_arc4_notifications_fixtures.py:42,72`) — not a
constant from the code under test (`test_notifications_auth_mail.py:112-127`, invite at
`:246-257`, end-to-end through the invite route at `:302-318`). No tracking tags (`:130-137`);
fail-closed refusal when the auth set is unconfigured, with zero client calls (`:140-152`,
production at `utils/mail_provider.py:152-159` — auth mail is refused, never downgraded onto
the tracking set).

**R7 — escalation table includes the OPENED-only row: PASS.**
`test_notifications_escalation.py:87-88` — `opened_only_still_reminds` (age 6h, opened, →
`"remind"`) and `opened_only_still_alerts` (age 30h, opened, → `"alert"`); restated standalone
at `:116-122` (`is_seen` is `False` for an OPENED-state row, and it still alerts). One-reminder
guarantee at `:156-173` (three runs, one reminder, guard is the `reminded_at IS NULL` write);
alert-once + idempotency at `:186-227`.

**R8 — no in-process timer/thread/scheduler library: PASS.**
`pyproject.toml` diff adds exactly one runtime dependency, `boto3>=1.35.0` (pre-authorised in
GATE RULINGS). The scheduler is a plain function + CLI (`scripts/notifications_scheduler.py`).
Enforced structurally by an AST test over all five arc-4 modules banning
threading/APScheduler/Celery/Timer/etc. (`test_notifications_escalation.py:310-344`).

**R9 — findings genuine, gate stopped rather than guessed: PASS.**
The gate genuinely STOPPED on Q1 (report "BUILD NOT STARTED", 2026-09-20) and built only after
the brief's GATE RULINGS were amended with an explicit Q1 ruling (`rfq_send` seam), which the
report acknowledges before the build section. The hook is where the ruling says
(`utils/rfq_send.py:267-289`, fail-soft, flag-off no-op). Report findings F7–F10 are candid and
verified plausible against the code (F7: no Gmail fallback when SES unconfigured with flag on —
true, `mail_provider.py:359-373`; F8: notification mail judged by the RFQ cap but writing no
ledger row — true, `utils/notifications.py` sets no `message_class` and
`send_governance.py:341-360` defaults absent class to `rfq`). The two mid-build defects
(late-reminder-after-escalation; soft-bounce alert storm) are fixed with named pinning tests,
not just narrated.

---

## Numbered findings

**1. MAJOR — date-dependent arc-4 test is red; the backend suite fails today.**
`utils/procurement_agent/tests/test_notifications_escalation.py:218-227`
(`test_running_twice_with_the_same_now_changes_nothing`), brief D6 / success criterion 1
("Backend green"). The module pins `NOW = datetime(2026, 9, 21, 12, 0, tzinfo=utc)` (`:34`),
but `aged_notification()` back-dates `sent_at` from the **real wall clock**
(`datetime.now(timezone.utc) - timedelta(hours=hours)`, `:151`). This test is the only one that
mixes the two: it builds wall-clock-relative rows and then runs `run_escalations(NOW)` with the
frozen instant. Run on 2026-09-22, the "30h-old" row is ~7h old *relative to NOW*, so it takes
the remind rung instead of alert: observed `first == (1, 0)`, asserted `(1, 1)`. The test was
green when the builder ran it (2026-09-21 before ~13:00 UTC) and is deterministically red ever
after — a time bomb, not a flake. The invariant it pins (same-`now` idempotency) is still
covered incidentally by `:156-164` and `:200-203` at real now, so this is a test defect, not a
product defect. Fix in the arc-4 file only (no pre-existing test involved): either pass a real
`now` (as `:206-215` does) or build the rows relative to `NOW`.

**2. MINOR — webhook certificate cache is unbounded and attacker-fillable.**
`utils/ses_webhook.py:65` (`_CERT_CACHE`, keyed by URL, never evicted), brief D4. The cert
fetch happens inside `verify_signature` (`:167`), which runs *after* the topic allowlist but
*before* the signature is known good — so a sender who knows or guesses an allowlisted TopicArn
(ARNs are not secrets) can post envelopes with unlimited distinct `*.amazonaws.com` cert URLs;
each one is fetched (5s timeout) and its response cached forever. Bounded blast radius (host
restricted to amazonaws.com, cheap-rejection ordering otherwise correct) and adjacent to the
builder's own F10 (no rate limit on this route); flagging the unbounded-memory/cache-fill angle
F10 doesn't name. A small LRU bound or cap on cache entries closes it. Fine to fold into the
F10 follow-up rather than this arc.

**3. MINOR — concur with report F8; it needs a human ruling before flags go on.**
`utils/notifications.py:238-281` sets no `message_class`, so reminder/digest/RFQ_NEW mail is
judged against the `rfq` daily cap (`utils/send_governance.py:341-360`) while writing no
`sent_messages` row — cap-blocked notifications go `SUPPRESSED` silently once the day's RFQ
budget is spent, and notification volume is invisible to the ledger/digest. The builder proved
the failure mode against the real gate and correctly declined to invent a cap policy (same
class of decision as gate F2, which the brief resolved for auth mail only). Not a build defect;
recorded so it is on the human's flag-on checklist alongside F7.

No BLOCKER findings. R3's live-client check, the D2 tracking-set separation, the D4 posture and
the D1 governance routing all pass as built.

---

## Verdict

**CHANGES_REQUESTED** — solely on finding 1. The suite must be green as run by the reviewer,
and today it is not (`1 failed, 2805 passed`). The fix is confined to
`test_notifications_escalation.py`, an arc-4 file; findings 2–3 need no code change this round.

---
---

# ROUND 2 (Claude Fable 5, 2026-09-22) — re-review of fix commit `7aee801`

**Verdict: APPROVED.**

The fix round touched exactly four files (`git show --stat 7aee801`): `NOTIFICATIONS_REPORT.md`
(fix log), `utils/ses_webhook.py`, and the two arc-4-owned test files
`test_notifications_escalation.py` / `test_ses_webhook.py`. No pre-existing test file, no other
source file, no scope creep. Both round-1 findings that called for code are genuinely fixed;
finding 3 is carried on the human flag-on checklist as agreed.

## Finding-by-finding verification

**Finding 1 (MAJOR, date-dependent test) — FIXED and verified.**
`utils/procurement_agent/tests/test_notifications_escalation.py:218-234`: the test now reads one
real instant (`now = datetime.now(timezone.utc)`, `:228`) and passes that same instant to both
`run_escalations` calls (`:229-230`) — the same form the adjacent ladder test uses (`:209`). The
row ages built by `aged_notification()` (`:151`, wall-clock-relative) are now measured against the
clock that produced them, so the 5h/30h rows deterministically land on remind/alert on any
calendar date. The idempotency assertion is unweakened (`first == (1, 1)`, `second == (0, 0)`, one
reminder row — `:231-234`), and the docstring (`:219-225`) records why the module-level `NOW` is
deliberately not used here, disarming the re-armament risk. The module `NOW` remains only in
`notification_row()` (`:150` area), which feeds the pure decision function with no store and no
clock — date-safe, correctly left alone.

**Finding 2 (MINOR, unbounded cert cache) — FIXED and verified.**
`utils/ses_webhook.py:64-74`: `_CERT_CACHE_MAX = 16` (`:73`) with a why-comment naming the exact
exposure (unauthenticated caller reaches the fetch after the topic allowlist, before the signature
is known good); eviction is oldest-out before each insert (`:159-161`), cache-hit path untouched
(`:150`). Two pinning tests added, both socket-free: `test_ses_webhook.py:348-366`
(`test_the_certificate_cache_is_bounded` — 48 distinct URLs, cache stays ≤ 16, newest survives,
oldest evicted, every fetch returns the right bytes) and `:369-384`
(`test_a_cached_certificate_is_not_refetched` — five calls, one fetch, so the bound did not trade
the cache-fill surface for a fetch storm). The monkeypatch of `urllib.request.urlopen` is
effective because `fetch_certificate_pem` imports it lazily at call time
(`utils/ses_webhook.py:153`) — D10 holds; no test opens a socket.

**Finding 3 (MINOR, F8 cap-class policy) — carried forward unchanged, as agreed.**
No code change, correctly: choosing a cap policy for notification mail is the same class of
human ruling as gate F2. Recorded in the report's fix log and the flag-on checklist alongside F7.
Both flags remain default-OFF, so nothing is live.

## Checklist re-run (only what the fix could have moved; the rest stands from round 1)

- **R1 — PASS.** `git diff --name-status d20ca68..HEAD`: every `M` is a source file
  (`api_server.py`, `utils/*.py`, frontend screens/libs, `pyproject.toml`, `uv.lock`,
  `design/interactions.md`); all 15 test-side files are `A`; `conftest.py` untouched.
  `loop/VERDICT.txt` is not committed (`loop/` is untracked).
- **R2 — now PASS on both suites, run by the reviewer with flags OFF:**
  - Backend: `NOTIFICATIONS_V1=0 uv run pytest -q` → **2808 passed, 73 skipped, 0 failed**
    (209s) = 2479 + 329. The round-1 red test now passes.
  - Frontend: `NEXT_PUBLIC_NOTIFICATIONS_V1=0 npm test` → **197 passed (24 files)** = 172 + 25.
  - Webhook 404 flag-off (`test_ses_webhook.py:124`, gate at `api_server.py:7538-7539`) and
    no-Notification-rows flag-off (`test_notifications_rfq_new.py:267`,
    `test_notifications_auth_mail.py:206`) all in the green run.
- **R3 — PASS (re-grepped).** Still exactly two `boto3.client(` sites in tests
  (`test_mail_provider.py:48`, `test_notifications_auth_mail.py:75`), both inline fake
  credentials + immediate `Stubber`; no `boto3.Session(`.
- **R4 — PASS (re-read; `ses_webhook.py` changed this round).** `handle_envelope` ordering is
  unchanged: envelope type → topic allowlist (`utils/ses_webhook.py:316`, before any URL in the
  envelope is fetched) → `verify_signature` (`:319`; cert host check inside
  `certificate_url_ok`, https-only at `:136`, dot-anchored `.amazonaws.com` at `:139`) → only
  then `confirm_subscription`. Idempotency store (`notifications_store.py`) untouched this round.
- **R5–R9 — unchanged by the fix commit; round-1 PASS stands** (governance tests drive the real
  gate; auth set asserted from Stubber-captured kwargs; OPENED-only table rows still escalate at
  `test_notifications_escalation.py:87-88`; sole new runtime dep is pre-authorised `boto3`; the
  AST no-scheduler test still passes in the green run; gate STOP/Q1-ruling history is honest).

## Verdict

**APPROVED.** Both suites green as run by this reviewer with flags OFF; no pre-existing test
modified; the round-1 MAJOR is fixed at the root (clock consistency, not assertion weakening)
and the MINOR is fixed with pinning tests. Outstanding items for the human flag-on checklist,
none of which block merge of a default-OFF arc: F7 (flag on + SES unconfigured ⇒ all mail
errors, no Gmail fallback), F8/finding 3 (notification cap class), F10 (webhook rate limit —
throttle key needs a ruling), F1 (verify page auto-POST, arc 4b), F5 (MEMBER_INVITE is new
outward mail), and the recommended post-approval conftest pin commit (gate F4 precedent).

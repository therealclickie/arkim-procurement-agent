# SUPPLIER_IDENTITY_REVIEW.md — Arc 2 review (Claude Fable, reviewer)

**Scope reviewed:** brief (`SUPPLIER_IDENTITY_BRIEF.md`), report (`SUPPLIER_IDENTITY_REPORT.md`),
and the full diff `f2f6ad1..HEAD` (17 files, +5012/−45) plus every new module and test file,
read in full. Nothing was taken on the report's word: hashing, send governance, the D3 guard,
RBAC and the flag-off wall were verified against code and tests directly, and the suite was
re-run by the reviewer with the flag explicitly off.

**Baseline note on R1's commanded diff:** `git diff --stat 7fe0319..HEAD` is **empty because
HEAD *is* 7fe0319** (the arc's final docs commit). The true pre-arc baseline is `f2f6ad1`
(the commit before the gate report `2864339`). All R1 evidence below is against
`f2f6ad1..HEAD`.

**VERDICT: APPROVED** — zero BLOCKER, zero MAJOR. Four MINOR findings (all follow-up-grade,
three already disclosed by the builder's own FINDINGS) and two notes.

---

## Reviewer checklist R1–R9

### R1 — frontend / existing-test-file changes → immediate CHANGES_REQUESTED?
**PASS.** `git diff --name-status f2f6ad1 HEAD`: **nothing under `frontend/`**; every file
under `utils/procurement_agent/tests/` is status **A** (added — 8 new files); the only
modified files are non-test backend: `api_server.py`, `ranking_bands.py`, `tier1_matcher.py`,
`quote_store.py`, `supplier_portal.py`, `supplier_registry.py`. `conftest.py` untouched
(see finding 3). No existing test file modified.

### R2 — suite with the flag explicitly OFF
**PASS.** Reviewer ran `SUPPLIER_ACCOUNTS_V1=0 uv run pytest -q` → **2425 passed, 73
skipped, 0 failed** (exit 0). 2425 = 2305 pre-arc + 120 new test functions (counted:
`grep -c "def test_"` over the 8 new files = 120), matching the report's claim exactly.
The flag-off wall is asserted in one place over the arc's **complete** route list:
`test_supplier_accounts_flag.py:24-44` (all 14 routes), byte-identical-to-unknown-route 404
(`:90-95`), bearer header does not flip it to 401 (`:97-104`), a **valid admin token** still
gets 404 on the admin routes (`:106-115` — flag check precedes `require_admin`, confirmed in
code at api_server.py:6908-6911/6920-6924), and a row primed flag-on is unreadable flag-off
(`:117-135`). Store-level defense-in-depth: every write no-ops flag-off
(`test_supplier_accounts_store.py:315-326`; `supplier_accounts.py:90-92` `_dormant`).
No new behaviour leaks with the flag off (but see findings 3 and 4 for two letter-of-the-law
edges, both non-observable on the wire).

### R3 — tokens/sessions genuinely hashed at rest
**PASS — read the model and the storage test.** `supplier_accounts.py:304-306` (`_hash_token`
= SHA-256 hex), mint at `:640-656` (raw `secrets.token_urlsafe(32)` returned once, only the
digest inserted), verify/validate by hash lookup only (`:677-681`, `:754-758` — never a
string compare). Storage-inspection tests dump the actual sqlite rows and assert the raw
token is absent and its digest present: `test_supplier_accounts_store.py:107-123` (both
tables), and again at the API layer for the session token
(`test_supplier_accounts_auth.py:317-331`). Magic-link single-use consume is an atomic
conditional UPDATE (`supplier_accounts.py:689-694`).

### R4 — uniform rejection: true equality or "returns something"?
**PASS — true equality.** `test_all_modes_return_identical_bodies`
(test_supplier_accounts_auth.py:105-127): five request-link modes (known-ACTIVE, no-account,
public-mailbox, PENDING, unparseable), asserts `len({r.content}) == 1` — byte equality.
`test_every_rejection_mode_is_identical` (auth.py:336-364): five verify rejection modes
(unknown, expired, replayed, PENDING member's link, empty) — same set-of-contents equality.
Session 401s likewise (auth.py:415-439, five modes). The bridge asserts uniformity across its
email modes too (test_supplier_accounts_bridge.py:154-163).

### R5 — send-governance test: real gate or mocked?
**PASS — the real gate runs.** The test wrapper is a **delegating subclass** whose `send()`
records the message and calls `super().send()` (auth.py:228-240) — the governance stack
inside `GmailSender.send` (email_sender.py:220-226 → `send_governance.evaluate`) executes
for real. The asserted verdict `"not_allowlisted"` (auth.py:242-261, empty allowlist,
fail-closed) can only be produced by the real `evaluate`; the allowlisted-domain test then
proves pass-through-governance-then-stub-at-delivery-gate (auth.py:263-278). Not a mock of
the gate; only delivery recording is wrapped. The send enters at the same last-seam every
outbound uses (`supplier_accounts.py:958-992` → `GmailSender().send`), so it structurally
cannot bypass governance. Criterion 5 satisfied. (Cap accounting is a different matter —
finding 2.)

### R6 — D3 guard at the I5 seam; does the cross-band test really construct a would-cross case?
**PASS.** The guard sits exactly at the seam the gate report identified: `assign_band`
(ranking_bands.py:370-377), **after** the confirmation check (:366-367 — the deliberate
quote-mobility path is untouched) and before every PN-evidence band check. Provenance chain
verified end-to-end: registry columns via idempotent `_migrate`
(supplier_registry.py:326-332, :511-520), the propose→approve path stamps
`supplier_self` + proposer flag-on / legacy `manual` flag-off
(supplier_portal.py:328-336, parity test
test_supplier_capability_provenance.py:152-164), matcher propagation
(`_match_scope_self_declared` tier1_matcher.py:413-441, carried on the candidate at :600-602).
The cross-band test is genuine: `test_self_declared_capability_cannot_cross_bands`
(provenance.py:256-262) builds a candidate with a real URL + extractor `partial_match` + no
found PN — which **provably earns Band B without the flag** via the direct control
`test_the_same_candidate_would_cross_without_the_flag` (provenance.py:264-270, identical
evidence, `self_declared_scope=False` → BAND_B via ranking_bands.py:392-393). Independent
evidence still earns its band (provenance.py:276-287), confirmation still promotes
(:289-295), within-band influence preserved (:297-330), key-absent candidates behave
pre-arc (:332-337). `test_ranking_bands.py` / `test_tier1_matcher.py` pass unmodified
(R1 + R2 evidence).

### R7 — enumeration leaks
**PASS — none found** (timing aside, per the brief). Checked each surface:
- `request-link` (api_server.py:6600-6684): every path — unparseable, no-account,
  store-error, PENDING/REVOKED member, ACTIVE member — returns the same `{"ok": true}` 200;
  differences live only in the audit trail and the member's mailbox. PENDING/REVOKED get
  **no send** (:6660-6668, builder FB3), so the mailbox channel leaks nothing either.
- The two-path account lookup (`find_account_for_email`, supplier_accounts.py:904-938,
  the FB1 fix) changes nothing observable — same uniform 200 both paths.
- `verify` (:6725-6761): one identical 401 for unknown/expired/used/PENDING/store-error
  (`_supplier_verify_reject_401` :6717-6723); a PENDING member's link is consumed then
  uniformly rejected — indistinguishable from an unknown token.
- Session wall: one identical 401 for missing/invalid/expired/revoked/non-bearer
  (:6768-6787).
- Rate limiter: applied **before** any account/member lookup (:6604-6605), buckets keyed on
  email/IP regardless of existence — the 429 is not an oracle (proven:
  auth.py:489-502).
- Flag-off: routes byte-identical to unknown routes, valid admin token included (R2).
- Cross-account: member ids from another account are `member_not_found` 404,
  indistinguishable from unknown (supplier_accounts_rbac.py:208-215;
  test_supplier_accounts_rbac.py:301-316).

### R8 — are the FINDINGS genuine, or task-skipping?
**Genuine — all seven judged individually.** FB1 (public-mailbox member could never log in)
is a real bug caught by the builder's own integration test and properly fixed. FB2/FB4/FB6
are honest disclosures of judgment calls the brief left open — ruled on in findings 3, 2, 4
below. FB3 (no send to non-ACTIVE members) is a correct extension of T4's
no-status-oracle rule to the mailbox channel. FB5 (ownerless account edge) is honestly
disclosed but incomplete — see finding 1. FB7 (guard conservative on extractor claims) is
exactly what the code does and is the right reading of D3. No task was skipped: all of
T1–T11 landed with their minimum test sets or more.

### R9 — RBAC genuinely matrix-driven?
**PASS.** Reviewer grep of `api_server.py` for `role ==`, `role in`, and quoted role
literals: the **only** hit is `role: str = supplier_accounts.ROLE_MEMBER` at
api_server.py:7034 — a Pydantic request-model default referencing the vocabulary constant;
not a comparison, not a literal, not in a handler body (note A). Every management route goes
through `_supplier_require_capability` (api_server.py:6978-6992) →
`rbac.has_permission` against the ONE declared matrix
(`supplier_accounts_rbac.py:74-93`); all role policy (no invited OWNER :116-119, no
promotion to OWNER :173-177, no demoting :178-181 / revoking :197-200 the OWNER,
cross-account 404 :208-215) lives in the rbac module. The matrix test is a real table test
over **every** role × capability pair (test_supplier_accounts_rbac.py:114-139), plus
unknown-role-holds-nothing (:141-145) and vocabulary↔matrix completeness (:152-156).
Second-OWNER paths all closed and tested: invite (:243-250), role change (:203-218),
concierge approval forces role MEMBER (store: supplier_accounts.py:592-607; route test
test_supplier_accounts_admin.py:98-105), bridge second email → MEMBER
(test_supplier_accounts_bridge.py:121-128), and underneath all of it the persistence-layer
partial unique index (supplier_accounts.py:225-228) proven against **raw SQL** bypassing
every helper (test_supplier_accounts_store.py:167-183). A source-scan test enforces the
no-role-literals rule permanently (test_supplier_accounts_rbac.py:350-372).

---

## Findings

### 1. MINOR — a domain-proving member arriving via `request-link` never triggers the ownerless-account OWNER promotion
**File:** `utils/supplier_accounts.py:894-900` (promotion lives only in `establish_account`)
vs `api_server.py:6640` (`request-link` uses `ensure_member_for_link` directly, no promotion).
**Clause:** D7 "the first member to establish an account becomes OWNER" / builder FB5.
An account established ownerless (bridge + non-matching email → PENDING) that later gains
its first domain-matched ACTIVE member through `POST /api/supplier/auth/request-link`
(rather than the T6 bridge) stays **ownerless**: that member lands `role=MEMBER`
(supplier_accounts.py:834-839), and since no member then holds MANAGE_MEMBERS, member
management is a dead end for that account until a domain-proving member happens to arrive
via the bridge, or a future ownership-assignment path. FB5 discloses the ownerless state and
tests the bridge-side gap-closer (test_supplier_accounts_bridge.py:143-152) but not this
door. Low practical impact (concierge can still approve members; quoting/viewing work;
v1 deliberately has no transfer route) — follow-up: apply the same
`has_live_owner` gap-closer in the request-link establishment path, or fold into the
ownership-transfer follow-up.

### 2. MINOR — magic-link sends are exempt from governance cap accounting; the only volume limit is the in-process rate limiter
**File:** `utils/supplier_accounts.py:958-992` (`send_magic_link_email` writes no
`sent_messages` ledger row); `api_server.py:6544-6585` (in-process fixed-window limiter).
**Clause:** D5 / guardrail 5 (sends pass through "allowlist, caps and suppression"),
guardrail 4's noted follow-up. Suppression and the allowlist run for real (R5), but because
link sends never ledger (builder FB4, deliberate: link-spam must not starve the RFQ daily
cap), **no governance cap applies to them at all**; the sole volume control is the
single-process limiter (3/email + 20/IP per 10 min), which resets on restart and does not
span workers. Defensible and disclosed, and inert while `EMAIL_SEND_ENABLED=False` — but
once delivery goes live this is unbounded-by-governance transactional mail. Follow-up
(builder already lists it): either a separate transactional cap bucket in send governance or
a `sent_messages` row with its own cap class.

### 3. MINOR — `SUPPLIER_ACCOUNTS_V1` is not pinned in the conftest flag list; the pre-arc suite is ambient-environment-sensitive
**File:** `utils/procurement_agent/tests/conftest.py:71-81` (untouched — builder FB2, the
gate's F1 judgment call, referred to the reviewer).
**Clause:** prime directive 2 (letter: "no edits to existing test files") vs 3 (flag-off
inertness must be what CI actually proves). Ruling: the builder's literal reading was the
right call **for this arc** — the directive's letter wins, the flag is live-read and
default-off, and each new test file manages the env itself. But a developer shell exporting
`SUPPLIER_ACCOUNTS_V1=1` would run the pre-existing suite flag-on (e.g. the
`supplier_self` stamp in `supplier_portal._apply_scope_no_lifecycle` becomes active under
tests that assume `manual`). Follow-up for the human to apply outside the arc's constraint:
add the env to `_FEATURE_FLAG_ENVS` in a one-line commit.

### 4. MINOR — T9 flag-off internal shape drift, and a slightly overstated report claim
**File:** `utils/supplier_registry.py:1496-1500` / `:1572-1575` (`get_supplier_classes` /
`get_supplier_brands` rows now carry `asserted_by` / `source`+`asserted_by` keys regardless
of flag), `:1618-1622` (`set_supplier_territory` now always writes `ship_area_source`);
report build-log T9 ("pinned shapes … exact-key-set tested").
**Clause:** prime directive 3 (letter of "flag off = no behavioural change"); report
accuracy. Verified mitigations: no non-test consumer of `get_supplier_scope` exists
(reviewer grep), the portal profile projects explicit keys (supplier_portal.py:90-108), so
**nothing reaches the wire** and the 2305 pre-arc tests pass — the drift is internal-shape
only, consistent with the established migration convention, and builder FB6 discloses the
column writes. The nit: the report's "exact-key-set tested" holds only for
`get_supplier_scope`'s **top-level** keys (test_supplier_capability_provenance.py:119-126);
the per-row dicts inside `classes`/`brands` did gain keys. Accepted as-is; recorded so the
next arc doesn't cite that sentence as stronger than it is.

### Notes (no action required)
- **A.** `SupplierInviteBody.role` defaults to the `ROLE_MEMBER` constant at
  api_server.py:7032-7034 — route-layer code naming a role. Not a comparison and not policy
  (the rbac module still rejects OWNER invites and validates roles), so not the D7 smell;
  if desired, move the default into `rbac.invite_member` (`role: Optional[str] = None`).
- **B.** The T7 extraction changed one flag-off-reachable log line ("portal open-requests
  read failed" → "open-requests read failed", api_server.py:6357-6359). Log text only;
  the token route's wire behaviour is characterization-tested unmodified.

---

## Success criteria (falsifiable, reviewer-verified)
1. ✅ 2425 passed (2305 + 120), zero edits to existing test files (R1/R2).
2. ✅ Flag-off run green, all 14 new routes 404 byte-identical (R2).
3. ✅ No raw token persisted — storage-inspected (R3).
4. ✅ Rejection-mode equality asserted as equality (R4).
5. ✅ Non-allowlisted send suppressed by the real governance stack (R5).
6. ✅ SUPPLIER_SELF capability provably cannot cross bands, with a would-cross control (R6).
7. ✅ Matrix-driven RBAC, full role×capability table test, no second-OWNER path (R9).
8. ✅ Diff clean: backend python + new tests only (R1).
9. ✅ Report contains gate I1–I9 with file:line, final count, genuine FINDINGS, follow-ups (R8).

**VERDICT: APPROVED**

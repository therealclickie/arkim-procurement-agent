# BUILD BRIEF — Supplier Identity (backend only)

**Arc:** 2 of the supplier-account programme. **Type:** additive backend, flag-gated, no UI.
**Branch:** cut from `test/flag-on-integration` after arc 1 has merged.
**Builder:** GLM (Fireworks). **Reviewer:** Claude Fable. **Merge authority:** human only.
**Feature flag:** `SUPPLIER_ACCOUNTS_V1` — default OFF; with the flag off the platform must behave
byte-for-byte as it does today.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

Today a supplier reaches the portal only through a single-use claim token in a URL. That is a
first-touch mechanism, not an identity. It cannot support: returning to see open RFQs, being
notified reliably, quoting under a stable identity, or anything a supplier would call "my account".

The July decision named this the first post-MVP arc: a durable supplier login, with email demoted
from system-of-record to notification channel. This arc builds the identity layer that everything
supplier-facing will sit on. It builds **no UI** — arc 3 converts the existing portal routes to use
it.

---

## DESIGN DECISIONS (settled — build to these, do not relitigate)

**D1. An account belongs to a COMPANY; people are MEMBERS.**
The registry already keys suppliers by `supplier_domain`. A supplier is a company that several
inside-sales reps may act for. Therefore: one `SupplierAccount` bound 1:1 to a registry
`supplier_domain`; many `SupplierMember` rows (one per person, keyed by email) under it. A magic
link authenticates a *member*; the resulting session carries both the member and the account.
Rationale: RFQs, quotes, and capability claims are the company's, not the individual's; a rep
leaving must not orphan the account; and the claim-link flow is already issued per domain.

**D2. Domain-matched membership by default.**
A member's email must share its registrable domain with the account's `supplier_domain`
(`sales@dxpe.com` may join the `dxpe.com` account). A non-matching email is NOT rejected — it
lands as a **pending membership** the concierge approves, reusing the existing propose→approve
pattern. Public-mailbox domains (gmail, outlook, yahoo, etc.) can never auto-match.

**D3. Supplier self-declaration is EVIDENCE, never AUTHORITY.**
Anything a member asserts about capability (brands, classes, ship area) enters the registry with
`source = SUPPLIER_SELF` and can influence ordering **within** an evidence band only. It must
never, on its own, promote a supplier across a band. The existing propose-revision → concierge
approval path remains the write path; this arc adds provenance so the matcher can honour the rule.
This is the same invariant as paid placement: influence within a band, never across.

**D4. Magic links only. No passwords.**
Single-use, expiring, hashed at rest — the same posture as the existing claim tokens. Request-link
responses are uniform regardless of whether the email exists (no enumeration oracle).

**D5. Sending goes through send governance.**
Magic-link emails are sends. They pass through the Night-10 fail-closed allowlist, caps and
suppression like any other outbound mail. In dev/test they land in the outbox/log, never a real
mailbox, unless the allowlist says otherwise.

**D6. Additive. Token paths untouched.**
The claim-token portal and the quote-token routes keep working exactly as today. This arc adds a
second door; it does not move the first one.

**D7. RBAC: three roles, enforced through a permission matrix.**
Roles are `OWNER | ADMIN | MEMBER`, following the B2B-SaaS norm. Capabilities:

| Capability | OWNER | ADMIN | MEMBER |
|---|---|---|---|
| View RFQs / open requests | ✓ | ✓ | ✓ |
| Submit quotes | ✓ | ✓ | ✓ |
| Propose profile/capability revisions | ✓ | ✓ | ✓ |
| Invite / approve / revoke members | ✓ | ✓ | — |
| Change a member's role | ✓ | ✓ | — |
| Transfer ownership / delete account | ✓ | — | — |

Rules: exactly **one OWNER per account**; the first member to establish an account becomes OWNER;
a concierge-approved pending member defaults to **MEMBER** (least privilege); an ADMIN may not
promote anyone to OWNER or demote the OWNER; the OWNER cannot leave without transferring
ownership.

**Enforcement is via a single `has_permission(member, capability)` check against a declared
matrix — never scattered `if role == "OWNER"` comparisons.** This is the point of D7: a fourth
role (e.g. read-only VIEWER, deliberately out of scope for v1) must be one row in the matrix, not
a hunt through route handlers. A reviewer finding inline role comparisons in route code should
treat it as a MAJOR finding.

---

## PRIME DIRECTIVE — ADDITIVE BACKEND, INERT WHEN OFF

1. **No frontend changes.** Nothing under `frontend/` is touched. Arc 3 owns the UI.
2. **No edits to existing test files.** The current 2305 backend tests must pass **unmodified**.
   New tests go in new files. If an existing test breaks, the change that broke it is wrong.
3. **Flag off = no behavioural change.** Every new route, model hook and send path is gated on
   `SUPPLIER_ACCOUNTS_V1`. With the flag off, new routes return 404 and no new tables are read.
4. **Existing token flows unchanged.** `test_supplier_portal.py`, `test_claim_tokens.py`,
   `test_quote_*.py`, `test_send_governance*.py` are the regression net — they pass untouched.

`git diff --stat` at the end must show: new/modified Python under `utils/procurement_agent/`,
new test files, and (if required) migration/config files. Nothing under `frontend/`. No existing
test file modified.

---

## INVESTIGATION GATE (read-only; report with `file:line` BEFORE building)

- **I1. Persistence.** How models are defined (SQLAlchemy? where?), how tables are created/migrated
  today (create_all? alembic?), and what the sqlite→Postgres path assumes. New tables must follow
  the existing convention exactly.
- **I2. Claim-token machinery.** Where claim tokens are minted, hashed, verified and expired
  (`claim_tokens`, `supplier_portal.py`). The magic-link implementation should reuse this pattern,
  ideally the same helper, not reinvent it. Report what is reusable verbatim.
- **I3. Admin auth.** How `/api/admin/*` authenticates (`test_auth.py`). Confirm it is API-key
  based and that nothing here needs to change; the supplier session is a separate mechanism.
- **I4. Registry shape.** The `SupplierRegistry` entry: fields, how `supplier_domain` is keyed,
  how brands/classes/ship-area are stored, and — critically — whether capability entries carry any
  provenance/source field today. If not, D3 needs one added.
- **I5. Matcher consumption.** How `tier1_matcher.py` and `ranking_bands` read the registry.
  Identify exactly where a self-declared capability could currently influence band assignment.
  This is the seam D3 protects; report it precisely.
- **I6. Send path.** How `email_sender` / `rfq_send` route through send governance: the allowlist
  check, caps, suppression, and how an outbound message is queued/released. Magic-link mail must
  enter at the same gate.
- **I7. Open-requests data.** What backend service feeds `open-requests.tsx` via the token route
  (from arc 1's I4 finding). The session-authed equivalent must call the same service.
- **I8. Feature-flag convention.** How existing flags (`SUPPLIER_PORTAL_V1`, `QUOTE_SUBMIT_V1`,
  `INTAKE_CHANNELS_V1`, `RANKING_BANDS_V1`) are declared and checked. Mirror it exactly.
- **I9. Registrable-domain helper.** Whether a registrable-domain normaliser already exists
  (frontend has one in `options-compose`; check `url_normalize.py` backend). Reuse, don't
  duplicate.

**STOP after the gate. Write the report. Do not begin T1 until the gate report is committed.**

---

## GUARDRAILS

1. Flag-gated, default off, inert when off (prime directive 3).
2. Tokens and session secrets hashed at rest; raw values returned exactly once.
3. No email enumeration: `request-link` returns the same 200 whether or not the email is known.
4. Rate-limit `request-link` per email and per IP (reuse whatever throttle pattern exists; if none,
   a simple in-process limiter is acceptable for this arc and must be noted as a follow-up).
5. Every send goes through send governance (D5). A test must prove a magic-link email to a
   non-allowlisted address is suppressed, not sent.
6. Sessions are server-side records with an opaque bearer token; expiry enforced; logout revokes.
7. Audit: every link request, verification, login, logout, and pending-membership decision writes
   an audit row (who, what, when, from where).
8. **PowerShell environment:** `;` not `&&`.
9. **NO PUSH.** Commit per task.

---

## BUILD TASKS

Each task names a minimum test set. Tests live in NEW files.

### T1. Models
`SupplierAccount` (1:1 `supplier_domain`, status, created_at), `SupplierMember` (email,
registrable_domain, account_id, role ∈ {OWNER, ADMIN, MEMBER} per D7, status ∈ {ACTIVE, PENDING,
REVOKED}), `MagicLinkToken` (hashed, member_id, expires_at, used_at), `SupplierSession` (hashed,
member_id, account_id, expires_at, revoked_at), `SupplierAuthAudit`. Follow I1's convention.
Migration or `create_all` per I1.
*Tests:* model round-trips; hashing never stores raw; 1:1 account↔domain enforced; the
one-OWNER-per-account constraint is enforced at the persistence layer, not merely in application
code.

### T2. Feature flag
`SUPPLIER_ACCOUNTS_V1` per I8. All new routes 404 when off.
*Tests:* each new route → 404 with flag off; 200/expected with flag on.

### T3. Magic-link request
`POST /api/supplier/auth/request-link` `{email}`. Normalise email; derive registrable domain (I9);
locate account by domain. If no account exists → still 200, no send, audit row. If account exists
and member unknown → create PENDING member (D2) unless domain matches, in which case create ACTIVE.
Mint token (reuse I2 helper), enqueue email via send governance (I6).
*Tests:* uniform 200 for known/unknown/public-domain emails; domain-match creates ACTIVE;
mismatch creates PENDING; public-mailbox domain never auto-ACTIVE; suppressed when not allowlisted;
rate-limited on repeat.

### T4. Magic-link verify → session
`POST /api/supplier/auth/verify` `{token}`. Single-use; expired/used/unknown → **uniform**
rejection. PENDING member → uniform rejection too (a pending member cannot log in, and must not
learn they are pending from this endpoint). Success → session token (raw, once) + expiry.
*Tests:* success path; each rejection mode returns identical body/status; second use rejected;
PENDING cannot log in.

### T5. Session middleware + `me` + logout
Bearer session dependency for `/api/supplier/*`. `GET /api/supplier/me` → account + member
summary. `POST /api/supplier/auth/logout` → revoke.
*Tests:* missing/invalid/expired/revoked session → 401; `me` shape; logout revokes.

### T6. Claim-token → account bridge
`POST /api/portal/{token}/request-account` `{email}`: a valid claim token may request a magic link
for its own supplier domain (this is the funnel seam arc 3 will surface as "create your account").
Same D2 rules; same uniform response. Does NOT consume the claim token.
*Tests:* valid claim token + matching email → ACTIVE member + link enqueued; invalid claim token →
uniform rejection; claim token remains usable afterwards.

### T7. Session-authed open requests
`GET /api/supplier/requests` — calls the SAME service the token route uses (I7), scoped to the
session's account. No new matching logic.
*Tests:* returns the same payload the token route returns for the same supplier; cross-account
access impossible (session for A cannot read B).

### T8. Session-authed quote submission
Reuse `QUOTE_SUBMIT_V1` store; supplier identity comes from the session instead of a quote token.
Flag-not-block sanity behaviour unchanged.
*Tests:* submitted quote lands in the existing store with supplier identity set; existing
token-path quote tests untouched and green.

### T9. Provenance on capability (D3)
Per I4/I5: add `source` (`SUPPLIER_SELF | CONCIERGE | SCRAPED | VERIFIED`) and `asserted_by` to
capability entries; the existing propose-revision path stamps `SUPPLIER_SELF`. Then add a guard at
the I5 seam so `SUPPLIER_SELF` entries cannot change band assignment.
*Tests:* a `SUPPLIER_SELF` capability affects within-band ordering; the same capability does not
move a supplier from Band C to Band B; existing `test_ranking_bands.py` and `test_tier1_matcher.py`
pass unmodified.

### T10. Admin: pending memberships
`GET /api/admin/supplier-members/pending`, `POST .../{id}/approve|reject` under existing admin
auth (I3). Approve → ACTIVE; reject → REVOKED; both audited. An approved member defaults to
role MEMBER (D7, least privilege).
*Tests:* list/approve/reject; requires admin auth; audit rows written; approved member is MEMBER.

### T11. Permission matrix + member management (D7)
Declare the capability matrix in ONE module. Implement `has_permission(member, capability)` and a
FastAPI dependency that route handlers use; **no route may compare roles inline**. Then:
`GET /api/supplier/members` (own account only), `POST /api/supplier/members/invite`
`{email, role}`, `POST /api/supplier/members/{id}/role` `{role}`,
`POST /api/supplier/members/{id}/revoke` — all permission-gated via the dependency, all audited.
The first member establishing an account is OWNER.
*Tests:* the matrix asserted as DATA (a table test over every role × capability pair, so the
matrix is the specification); a MEMBER gets 403 on every member-management route; an ADMIN cannot
promote to OWNER nor demote the OWNER; the sole OWNER cannot revoke themselves; a second OWNER
cannot be created; a member of account A cannot act on account B's members; every action audited.

---

## SUCCESS CRITERIA (falsifiable)

1. `uv run pytest -q` green; **2305 original tests pass with zero edits to existing test files**;
   new count reported as `2305 + N`.
2. With `SUPPLIER_ACCOUNTS_V1` off, every new route returns 404 and the full existing suite
   passes — proven by a test run with the flag explicitly off.
3. No raw magic-link token or session token is ever persisted (test inspects storage).
4. `request-link` and `verify` return identical responses across all rejection modes (test asserts
   equality, not just status).
5. A magic-link send to a non-allowlisted address is suppressed by send governance (test proves it
   via the existing governance test harness, not a mock of the gate).
6. A `SUPPLIER_SELF` capability provably cannot move a supplier across evidence bands.
7. RBAC is matrix-driven: no route handler compares roles inline, and the matrix is asserted by a
   table test covering every role × capability pair (D7).
8. `git diff --stat` shows nothing under `frontend/` and no modified existing test file.
9. `SUPPLIER_IDENTITY_REPORT.md` contains: gate report I1–I9 with `file:line`, final test count,
   FINDINGS (empty is valid), and any follow-ups (e.g. rate limiter to be replaced).

---

## AGENT LOOP PROTOCOL

Filesystem-only handshake. Human is the sole merge authority.

**Cycle:** builder runs gate → writes `SUPPLIER_IDENTITY_REPORT.md` → builds T1–T10 committing per
task → appends count + FINDINGS. Reviewer reads brief + report + diff → writes
`SUPPLIER_IDENTITY_REVIEW.md` with numbered findings (BLOCKER/MAJOR/MINOR, `file:line`, clause) →
writes `VERDICT.txt` containing exactly `APPROVED` or `CHANGES_REQUESTED`. Builder fixes only the
numbered findings, appends a fix log, hands back. Repeat.

**Reviewer checklist — Fable answers each with evidence:**
- **R1.** Any change under `frontend/`, or any modified existing test file? → `CHANGES_REQUESTED`
  immediately.
- **R2.** Run the suite with the flag OFF. Is it exactly the pre-arc suite passing plus new tests
  asserting 404? Any new behaviour leaking through with the flag off is a BLOCKER.
- **R3.** Are tokens/sessions genuinely hashed at rest? Read the model and the storage test — do
  not trust the report's claim.
- **R4.** Is the uniform-rejection test a true equality check across modes, or separate "returns
  something" checks?
- **R5.** Does the send-governance test exercise the real gate, or mock it away? Mocking the gate
  is a MAJOR finding — it defeats D5.
- **R6.** Is the D3 guard placed at the seam I5 identified, and does the cross-band test actually
  construct a case that WOULD have crossed bands without the guard?
- **R7.** Enumeration: can any response, timing aside, distinguish a known email from an unknown
  one, or a PENDING member from a non-member? Name each leak.
- **R8.** Are FINDINGS genuine, or is the builder skipping hard tasks? Judge each.
- **R9.** RBAC (D7): is enforcement genuinely matrix-driven? Grep every route handler for inline
  role comparisons (`role == "OWNER"`, `role in (...)`, string literals of role names outside the
  matrix module). Any inline comparison in route code is a **MAJOR** finding. Is the matrix test a
  table over every role × capability pair, or a handful of happy-path cases? Can a second OWNER be
  created by any path — invite, role change, or concierge approval?

**Standing rule (CLAUDE.md):** an exit-checklist item may be marked done only if the evidence exists
as a committed artefact or a named passing test.

---

## OUT OF SCOPE

All UI (arc 3). Notification/delivery tracking (arc 4). Password auth, SSO, OAuth. Multi-account
membership (one member email → one account for now; note as follow-up). Real email delivery
configuration (mygofer.ai / domain warming — infra track). Supplier metrics, loss reasons, paid
tiers. Inventory-system integration.

---

## HUMAN VERIFICATION (after `VERDICT: APPROVED`)

```powershell
cd 'C:\dev\_Arkim\Arkim Procurement Agent Prototype'
git diff --stat <branch-point> HEAD          # expect: backend python + new tests only
git diff --stat <branch-point> HEAD -- utils/procurement_agent/tests   # expect: additions only
uv run pytest -q                              # expect: 2305 + N passed
$env:SUPPLIER_ACCOUNTS_V1 = "0" ; uv run pytest -q   # expect: still green
Get-Content SUPPLIER_IDENTITY_REPORT.md       # read FINDINGS and follow-ups
```

Then, with the flag on and the server running, request a magic link for a `dxpe.com` address and
confirm it lands in the governance outbox (not a real mailbox) with the token absent from every
log line.

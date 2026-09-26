# BUILD BRIEF — Arc 6: Buyer Identity and Roles

**Type:** backend + frontend, flag-gated.
**Branch:** `arc6/buyer-identity`, cut from `test/flag-on-integration` AFTER PH-01 has merged.
**Builder:** Claude Opus 5. **Reviewer:** Claude Fable 5. **Merge:** human only.
**Feature flags:** backend `BUYER_ACCOUNTS_V1`, frontend `NEXT_PUBLIC_BUYER_SESSION_V1` — both default OFF.
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

The supplier side has accounts, roles and login (arcs 2–3). The buyer side has none. The
flags-on evaluation and the human UI walk exposed the consequences:

- An order records `placed_by: "Dana Plant-Manager"` as free text and `company_id: null`.
- The `source_anyway` override records `acknowledged_by: null` (walk finding 6).
- The buyer header shows "Northgate Manufacturing" on runs belonging to Bay Foods (finding 5).
- Nothing stops anyone who can open the buyer screen from approving anything.

The next arc adds a purchase-order approval cap with second approval and an admin override. **A cap
is only a control if the system knows who is approving.** This arc builds that identity: companies,
members, roles, login, company isolation, and attribution of every buyer action. It defines the full
permission matrix — including the approval capabilities — and stores the approval policy. It does NOT
enforce approvals at order time; that is arc 7.

---

## DESIGN DECISIONS (settled — build to these)

**D1. A buyer account is a COMPANY; people are MEMBERS; runs belong to a facility of that company.**
Use the existing tenant/facility model the gate identifies (the eval seeded tenant `bayfoods` →
facility `fac-stockton`). A member belongs to one company. Roles are company-wide in v1;
facility-scoped roles are out of scope.

**D2. Four roles, enforced through a permission matrix.**

| Capability | Requester | Buyer | Approver | Admin |
|---|---|---|---|---|
| Raise a request / chat intake | ✓ | ✓ | ✓ | ✓ |
| View the company's runs and orders | ✓ | ✓ | ✓ | ✓ |
| Select a candidate and submit an order | — | ✓ | ✓ | ✓ |
| Approve an order up to the auto-approval limit | — | ✓ | ✓ | ✓ |
| Give the second approval above the limit | — | — | ✓ | ✓ |
| Override the limit (single-person approval above it) | — | — | — | ✓ ¹ |
| Set the auto-approval limit | — | — | — | ✓ |
| Turn the admin override on or off | — | — | — | ✓ |
| Invite, change role, revoke members | — | — | — | ✓ |

¹ Only while the company's override setting is on (D5).

Enforcement uses ONE matrix module and a `has_permission(member, capability)` dependency, exactly as
arc 2's D7 established for suppliers. **No route compares role names inline.** A source-scan test
enforces this, mirroring arc 2's.

**D3. Invite-only; Gofer creates the company and its first Admin.**
Buyer customers are sold by a rep, not self-serve. A Gofer operator creates the company through the
existing admin API and invites its first Admin. Thereafter that company's Admins invite and manage
members. No domain auto-join (unlike suppliers): plants use shared and personal mailboxes, so
membership is always an explicit invitation. Invited members default to **Requester** (least
privilege) unless the inviting Admin chooses otherwise.

**D4. Reuse the arc 2/3 auth machinery; do not change supplier behaviour.**
Magic-link issuance and verification, httpOnly cookie sessions, the CSRF origin check, uniform
rejection, rate limiting, send governance for auth mail, and the audit pattern are all reused. The
buyer session uses its OWN cookie (`gofer_buyer_session`) so buyer and supplier sessions never
collide. Shared helpers may be imported; supplier-side behaviour and every arc 2/3 test must be
unaffected. If reuse would require changing supplier code, prefer a thin buyer-side wrapper; if that
is impossible, it is a `GATE STOP`.

**D5. The approval policy is stored here and enforced in arc 7.**
Each company holds `auto_approval_limit` (USD, default **2500**, `0` = every order needs a second
approval) and `allow_admin_override` (default **true**). Only an Admin may change either, and every
change writes an audit row (who, old value, new value, when). This arc builds the storage, the
Admin settings screen and the audit trail. It does not change order behaviour.

**D6. Company isolation is the security property of this arc.**
With `BUYER_ACCOUNTS_V1` on, every buyer-facing endpoint requires a buyer session and is scoped to
that member's company. A member of company A requesting company B's run, order or setting gets the
same uniform not-found as a genuinely missing record — never a distinguishable "forbidden".

**D7. Every buyer action is attributed to an authenticated member.**
Run creation, confirm, the `source_anyway` acknowledgement, candidate selection, approval and order
execution record the acting member's id, derived from the session — never from request-body text.
`placed_by` on orders and `acknowledged_by` on overrides become member ids. Orders and runs carry
`company_id`. The buyer header shows the authenticated company's name.

**D8. Flag off = today's demo behaviour exactly.**
With `BUYER_ACCOUNTS_V1` off, the buyer UI and API work unauthenticated as they do today, and
attribution fields keep their current behaviour.

---

## GATE RULINGS (pre-authorised)

- Adding buyer-side tables, a buyer auth router, and a buyer matrix module: AUTHORISED.
- Importing arc 2/3 auth helpers: AUTHORISED. Modifying supplier auth behaviour: NOT authorised.
- Email-originated runs (intake by known sender) carry no authenticated member in this arc; attribute
  them to the known sender's facility with `acting_member: null` and a `channel: "email"` marker.
  Mapping known senders to members is out of scope.
- Any scope question not covered here: `GATE STOP`.

### Rulings on the gate stop of 26 September 2026 (Q1–Q3, F-series)

**Q1 — One buyer identity.** Under `BUYER_ACCOUNTS_V1`, the `gofer_buyer_session` cookie is the ONLY
accepted identity on buyer-facing routes. A Cognito bearer alone does not authenticate a buyer route:
without a valid buyer session the route refuses exactly as it would for an anonymous caller. Do not
build a Cognito-to-member mapping. With the flag OFF, the existing Cognito path behaves exactly as today
(no change to `utils/auth/`). Where current logic reads `caller.company_id` or `caller.user_id` (M1
distinct-approver enforcement, `approver_id`, run `company_id`), under the flag it reads the session
member's `company_id` and member id instead, and M1 compares member ids. Record in CLEANUP that mapping
the core platform's Cognito identity to buyer members is a future decision, not a pilot need.
The buyer company key IS the existing company id string (`company-bayfoods`). Your proposed D1 binding
(one `buyer_companies` row keyed by that string, carrying its facility ids so `_TENANT_MAP`,
`_MOCK_FACILITIES` and run/order `company_id` resolve to one entity) is APPROVED.

**Q2 — `POST /api/runs/from-maintenance` is service ingress, not a buyer surface.** Exempt it from the
buyer-session requirement ONLY if its service authentication (Cognito JWT plus
`X-Arkim-Service-Signature`) is actually verified in code today, not just described in the docstring.
State which, with `file:line`, in the report.
- If verified: add it to the T4 allowlist with the reason "service-to-service ingress from core;
  authenticated by service signature". Add a test that, with the flag on, an unsigned or unauthenticated
  call is refused.
- If not verified: with the flag on, the route returns 404, it is allowlisted with the reason
  "disabled under BUYER_ACCOUNTS_V1 until service auth is enforced", a test pins the 404, and CLEANUP
  records it. Do not build new signature verification in this arc.

**Q3 — Dev and debug routes are disabled under the flag.** With `BUYER_ACCOUNTS_V1` on,
`/api/debug/llm` and `/api/dev/reseed-handoffs` return 404. Allowlist them by name with the reason
"disabled under BUYER_ACCOUNTS_V1", and add a test that pins both 404s. With the flag off, they behave as
today. Record in CLEANUP that `/api/debug/llm` echoes a key prefix and should lose that behaviour
entirely in a later arc.

**The T4 guard's buyer-facing rule, as proposed in K2, is APPROVED:** every route not under
`/api/supplier/`, `/api/admin/`, `/api/webhooks/`, `/api/portal/{token}`, `/api/quote/{token}`,
`/api/intake/` or `/api/health`, with the four FastAPI auto-routes skipped. The exemptions for Q2 and
Q3 above are named individually in the allowlist, not covered by a prefix.

**Findings rulings.**
- F2: APPROVED. Seeding stamps `company-bayfoods`. Legacy rows with a NULL `company_id` are invisible
  under the flag, the same stance as DEMO_MODE. Do not backfill.
- F3: Store D5 only. Do NOT change how `approval_rules` routes approvals, and do not lock down B21
  beyond D2's Admin capability. Record the two-store reconciliation in CLEANUP for arc 7.
- F4, F5: in scope under D7. Order creation and rejection must carry the session member under the flag.
- F6: out of scope; record it in CLEANUP (inbound intake sender authentication, for the email intake arc).
- F7: buyer magic-link mail goes through the SAME send-governance path that supplier magic-link mail uses
  after arc 5. Do not add a new governance exemption or class, and do not edit `utils/send_governance.py`.
  If that path relies on the allowlist, company bootstrap (T6) adds the company's email domains, as
  given explicitly by the Gofer admin, and audits the change. If supplier auth mail uses a path that
  buyer mail cannot reuse without touching governance code: `GATE STOP`.
- F8: APPROVED. Add a nullable `company_id` to `site_shipto`, and scope by it under the flag.
- F11: APPROVED. Set `credentials: "include"` in the `request()` defaults.

**K7:** the proposed `conftest.py` edit (append `BUYER_ACCOUNTS_V1` to the flag-pin lists; no existing
entry changed) is APPROVED as additive only.

---

## PRIME DIRECTIVE — TEST EDITS REQUIRE HUMAN APPROVAL

1. A pre-existing test file may be modified only if listed in `loop/AUTHORISED_TEST_EDITS.txt`,
   written by the human after reviewing the gate's proposal. The builder may not create or edit it.
2. The fence: change only assertions encoding a behaviour this arc supersedes; replace, never merely
   delete; leave every other assertion byte-identical; keep invariants tested.
3. Every arc 2/3 supplier test passes unmodified. Flags off = today's behaviour.
4. No live model, search or AWS calls in tests.

---

## INVESTIGATION GATE (read-only; `file:line` for every claim)

- **K1.** The existing tenant/company/facility model — where tenants and facilities are defined, how a
  run is tied to a facility, and what renders "Northgate Manufacturing" in the header (finding 5).
- **K2.** EVERY buyer-facing API endpoint, with its current auth status. This list is the scope of D6;
  an endpoint missed here is an isolation hole.
- **K3.** The arc 2/3 auth machinery: magic-link mint/verify, session dependency, CSRF check, rate
  limiter, rbac matrix module — what is importable as-is for the buyer side.
- **K4.** Every place a buyer action is recorded (`placed_by`, `acknowledged_by`, run creation, confirm,
  select, approve, execute) — the attribution points for D7.
- **K5.** The admin API — where a Gofer operator can create a company and invite its first Admin.
- **K6.** The frontend buyer routes and how they fetch — where a session guard and the buyer login
  belong, reusing arc 3's supplier components where sensible.
- **K7. PROPOSED TEST EDITS.** Every pre-existing test assertion this arc supersedes, one line per file:
  `PROPOSED TEST EDIT: <repo-relative path> :: <line ranges> :: <superseded behaviour> :: <decision>`
  or `PROPOSED TEST EDITS: NONE`. Expect the attribution changes (D7) to touch assertions on
  `placed_by` and `acknowledged_by`.

If blocked, end the report with `GATE STOP:` and the question. Commit the report; stop after the gate.

---

## BUILD TASKS

### T1 — Models and flag
Buyer company link (to the existing tenant), members (email, role, status), magic-link tokens,
sessions, audit, approval-policy fields. `BUYER_ACCOUNTS_V1`.
*Tests:* round-trips; tokens and sessions hashed at rest; one company per member; flag off → new routes 404.

### T2 — Permission matrix (D2)
One module, `has_permission`, FastAPI dependency.
*Tests:* a table test over every role × capability pair (the matrix is the specification); a
source-scan test failing on any inline role comparison in buyer route code.

### T3 — Buyer login and sessions (D4)
Magic-link request and verify, `gofer_buyer_session` cookie (HttpOnly, Secure, SameSite=Lax), CSRF
origin check on cookie-authenticated state changes, logout, `GET /api/buyer/me`.
*Tests:* uniform request-link response for known, unknown and rate-limited addresses (byte equality
with a contrast case); cookie attributes asserted on the real `Set-Cookie`; supplier and buyer cookies
independent; foreign-Origin cookie POST refused.

### T4 — Company isolation (D6)
Every endpoint from K2 requires a buyer session under the flag and is company-scoped.
*Tests:* for EACH endpoint in K2, a member of company A requesting company B's resource gets the same
response as a missing resource; no endpoint from K2 is reachable without a session under the flag.

**Structural guard (required).** A per-endpoint list protects only the endpoints someone remembered.
Add a test that enumerates every route registered on the running FastAPI app (`app.routes`), not the
source text and not the K2 list, and fails if any buyer-facing route lacks the buyer-session
dependency when `BUYER_ACCOUNTS_V1` is on. Define buyer-facing by an explicit rule (for example, every
route not under the supplier, admin, webhook, auth or health prefixes) and pin the exemptions as a
short, named allowlist in the test, so adding an exemption is a visible, reviewable change. A new
buyer endpoint added by any future arc must fail this test until it is scoped. (Lesson from PH-01:
enforcing at the door beats checking a list. It took five review rounds to learn it.)

### T5 — Attribution (D7)
Acting member recorded on every action in K4; `company_id` on runs and orders; header shows the
company.
*Tests:* each action records the session member, ignoring any body-supplied name; an override records
`acknowledged_by`; an order records `placed_by` as a member id and `company_id`.

### T6 — Company bootstrap and member management (D3)
Admin API: create company, invite first Admin. Buyer Admin: invite (default Requester), change role,
revoke.
*Tests:* only a Gofer operator can create a company; only an Admin can manage members; the last Admin
cannot demote or revoke themselves; every action audited.

### T7 — Approval policy settings (D5)
Storage, Admin-only settings screen and API, audit of every change.
*Tests:* defaults are 2500 and override-on; a non-Admin gets 403 on change; each change writes old and
new values and the actor; `0` is accepted and means every order needs a second approval; negative or
non-numeric values are rejected.

### T8 — Frontend
Buyer login and verify (explicit Continue click, as arc 4b made the supplier page), session guard on
buyer routes, company name in the header, Team and Settings screens (Admin only), controls hidden by
permission with the server enforcing.
*Tests:* a Requester sees no select/approve controls; a hidden control's endpoint still returns 403
when called directly; no session value in JS-readable storage after a full login→act→logout cycle.

---

## SUCCESS CRITERIA

1. Backend and frontend green; pre-existing tests pass unedited except files in the approval list.
2. With the flag on, no buyer endpoint from K2 is reachable without a session, and no company can read
   another's data — proven per endpoint.
3. Every buyer action records an authenticated member; no attribution field accepts request-body text.
4. The permission matrix is a table test; no inline role checks in route code.
5. Flags off: both suites green, today's behaviour unchanged.
6. `BUYER_IDENTITY_REPORT.md` holds the gate K1–K7, counts, FINDINGS, and the endpoint isolation table.

## REVIEWER CHECKLIST

- **R1.** Every `M` pre-existing test file is in `loop/AUTHORISED_TEST_EDITS.txt`? Any other →
  `CHANGES_REQUESTED`.
- **R2.** Compare the K2 endpoint list against the actual router: is any buyer-facing endpoint missing
  from the isolation tests? A missing endpoint is a BLOCKER. Does the T4 structural guard enumerate
  `app.routes` at runtime (not source text), and is its exemption allowlist short, named and justified?
  A guard built from a hand-written list, or an exemption without a reason, is MAJOR.
- **R3.** Can any attribution field still be set from request-body text? Trace each. Any path → MAJOR.
- **R4.** Inline role comparisons in route code? MAJOR.
- **R5.** Is cross-company access indistinguishable from not-found (body and status)?
- **R6.** Did any supplier-side behaviour change? Run arc 2/3 tests and diff supplier auth modules.
- **R7.** Both suites with flags off.
- **R8.** FINDINGS genuine?

## OUT OF SCOPE (→ arc 7, order lifecycle)

Enforcing the approval limit, second approval, separation of duties and the admin override at order
time. Gofer-side credit terms (prepayment threshold, credit limit on outstanding balance, fail-closed
default). PO transmission, supplier confirmation, awarded state, revision lock. Facility-scoped roles.
Mapping email known-senders to members.

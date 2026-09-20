# SUPPLIER_IDENTITY_REPORT.md — Arc 2 Investigation Gate (I1–I9)

**Branch:** `arc2/supplier-identity` · **Status:** gate complete, no implementation written.
**Baseline:** `uv run pytest -q` run at gate time — result recorded in §Baseline below.
Every claim carries `file:line` evidence. Read-only investigation; nothing under `frontend/`
was touched and no test file was modified.

---

## I1. Persistence — how models/tables are defined and migrated today

Two conventions coexist; the brief's new tables must follow **convention B** (the
token-store pattern), because every supplier-facing surface built since Night 6 uses it.

**Convention A — SQLAlchemy ORM (the run/app state only):**
- ORM models live in `utils/procurement_agent/state/persistence.py` — `Base(DeclarativeBase)`
  at persistence.py:61, `SourcingRunORM` at persistence.py:65, `ApprovalHistoryORM` at
  persistence.py:132, `ApprovalRuleORM` at persistence.py:147. UUID string PKs, JSON-in-TEXT
  columns, indexed FKs; docstring states "SQLite for prototype; Postgres-ready schema"
  (persistence.py:4-5).
- Tables are created by `Base.metadata.create_all(_engine)` at persistence.py:229 (import
  time) and again in api_server at api_server.py:285. WAL mode is enabled per-connection
  (persistence.py:47-50).
- Migration is **hand-rolled idempotent ALTERs**, not Alembic: `api_server._migrate_schema`
  (api_server.py:288-321) issues `ALTER TABLE ... ADD COLUMN` statements and swallows
  "already exists"; `persistence.migrate_run_state` (persistence.py:213) does the same for
  run-state columns.

**Convention B — standalone per-module raw-sqlite3 stores (every Night 6+ surface):**
Each new store is its own module + its own sqlite file in `data/`, DDL as a module-level
string executed `CREATE TABLE IF NOT EXISTS` on every connection, plus a PRAGMA-driven
idempotent `_migrate` for later columns:
- `utils/claim_tokens.py` — `claim_tokens.sqlite`, DDL at claim_tokens.py:91-102, `_get_conn`
  at claim_tokens.py:122-129.
- `utils/supplier_registry.py` — `supplier_registry.sqlite`, base DDL at
  supplier_registry.py:90-103, child-table DDLs at supplier_registry.py:314-356,
  `_migrate` (PRAGMA table_info + ADD COLUMN) at supplier_registry.py:427-488.
- `utils/quote_tokens.py` — `quote_tokens.sqlite`, DDL at quote_tokens.py:77-97.
- `utils/quote_store.py` — `quotes.sqlite`, DDL at quote_store.py:103-134.
- `utils/send_governance.py` — `send_governance.sqlite`, DDLs at send_governance.py:49-70.
- Idiom details worth copying: uuid4 string PKs, ISO-8601-UTC TEXT timestamps, `is_test`
  provenance column on test-created rows, `_DB_PATH` module attr as the monkeypatch seam,
  `contextlib.closing` connections, fail-soft (return None/[]/False, never raise) on every
  public store function.

**sqlite→Postgres path:** only convention A claims Postgres-readiness (persistence.py:4-5).
Convention B stores are prototype-local and have no Postgres story; the new
supplier-accounts store joins that bucket. **Decision for T1:** follow convention B (own
module, own sqlite file, in-module DDL, `CREATE TABLE IF NOT EXISTS`), matching
claim_tokens/quote_tokens exactly. The one-OWNER-per-account constraint demanded by T1
"at the persistence layer" is expressible in convention B as a partial unique index
(`CREATE UNIQUE INDEX ... ON supplier_members(account_id) WHERE role='OWNER'` — SQLite
supports partial indexes) or a `UNIQUE` trigger; a plain UNIQUE column cannot express it.

## I2. Claim-token machinery — what the magic-link implementation can reuse verbatim

Minted/verified/expired in `utils/claim_tokens.py`:
- **Mint:** `generate_for(supplier_domain)` claim_tokens.py:158-195 — `secrets.token_urlsafe(32)`
  (~256 bits), SHA-256 hex stored (`_hash_token` claim_tokens.py:117-119), raw token returned
  ONCE in the mint dict (claim_tokens.py:187-192), 7-day default expiry.
- **Verify:** `validate_token(raw)` claim_tokens.py:198-230 — hash + single indexed lookup
  (never a string compare over raw tokens), rejects revoked (claim_tokens.py:217-218) and
  expired (`_is_expired` claim_tokens.py:233-249, naive-UTC tolerant).
- **Regenerate/expire:** `regenerate` claim_tokens.py:252-279 revokes all prior live tokens
  for the supplier then mints; `revoke` claim_tokens.py:282-299.
- **Route-side gate:** api_server `_validate_portal_token` api_server.py:5529-5541 —
  rate-limit BEFORE the token check; `_portal_rate_check` api_server.py:5486-5510 is an
  in-process fixed-window limiter keyed `(client IP, token-prefix)` with lock
  (api_server.py:5471-5472); `_portal_reject_404` api_server.py:5521-5526 is the uniform
  rejection (404 byte-identical to unknown route, no oracle); `_portal_response_headers`
  api_server.py:5544-5550 (no-referrer + no-store).

**Reusable verbatim:** the hash-at-rest pattern, `_hash_token`, `_is_expired`,
`_env_truthy`, the DDL/connection idiom, the rate-limit limiter shape, the uniform-404
helpers, and the response-header helper. **Not reusable as-is:** `generate_for`/`validate_token`
themselves — they are scoped to `supplier_domain` and live in the `claim_tokens` table;
per the deliberate namespace-isolation precedent (quote_tokens.py:9-19: "a claim token must
never open a quote form and vice versa — isolation is structural"), the magic-link token
must be a **separate store** (own module + own sqlite file, e.g.
`utils/procurement_agent/supplier_accounts/` or a `utils/` sibling) re-implementing the same
hygiene helpers, NOT a call into claim_tokens. `claim_tokens` is also gated by
`CLAIM_TOKENS_ENABLED` read at import (claim_tokens.py:80) which would entangle the two
flags.

## I3. Admin auth — API-key based, confirmed; nothing here needs to change

- `/api/admin/*` authenticates via `require_admin` api_server.py:3415-3431: bearer token
  constant-time-compared (`secrets.compare_digest`) against env `ARKIM_ADMIN_TOKEN`;
  unset secret → 503 (admin disabled, fail-closed), no header → 401, wrong token → 403.
  Returns the role label `"admin"` (api_server.py:3431). This is a server-secret/API-key
  mechanism, not user auth.
- A **separate** Cognito-JWT mechanism exists (`utils/auth/dependencies.py`:
  `get_caller` dependencies.py:97-138, `require_role` dependencies.py:141-167) but is an
  *optional* dependency on customer endpoints only (api_server.py:133-138) — it plays no
  part in admin auth. `test_auth.py` exercises the Cognito path; the admin-token contract
  is exercised in `test_admin_api.py` (e.g. test_admin_api.py:42, :76).
- **Confirmed:** the supplier session is a third, separate mechanism (D4/D5); admin auth
  needs no change. T10's admin routes should follow the **gate-ordering convention** proven
  at api_server.py:6415-6420: for flag-gated admin routes the FLAG check runs BEFORE
  `require_admin` (invoked in the handler body, not as a `Depends`) so flag-off renders the
  route absent (404) for any caller rather than leaking existence via 401/403.

## I4. Registry shape — fields, domain keying, capability storage, provenance today

`utils/supplier_registry.py`, `suppliers` table:
- **Keying:** `domain TEXT UNIQUE` (supplier_registry.py:93), normalized by
  `_normalize_domain` supplier_registry.py:402-412 (lowercase, strip `www.`, strip path).
  `lookup_by_domain` supplier_registry.py:552-563 is the canonical lookup. This is the key
  a `SupplierAccount` binds 1:1 to (D1).
- **Base row:** id, name, onboarding_status, contact_email, contract_status,
  vendor_authorization_status, counterfeit_risk_notes, created/updated
  (supplier_registry.py:91-102).
- **Capability storage (TIER1_V2 scope)** — three child tables + JSON columns:
  - `supplier_classes` DDL supplier_registry.py:314-328: class_id, subtype, unspsc, is_core,
    confidence, **`source` TEXT (manual|apollo|inferred)** supplier_registry.py:323,
    vocabulary `SCOPE_SOURCES` supplier_registry.py:304-307, written by
    `set_supplier_classes` supplier_registry.py:1399-1439.
  - `supplier_brands` DDL supplier_registry.py:330-344: brand_id, relationship
    (AUTHORIZED|CARRIES|AFTERMARKET_COMPATIBLE, supplier_registry.py:277-282),
    authorized_territory, classes_for_brand_json, `evidence` (free text), confidence —
    **no `source` column at all**.
  - Ship area: `ship_area_json` column on `suppliers` supplier_registry.py:294, written by
    `set_supplier_territory` supplier_registry.py:1538-1586 — **no per-entry provenance**;
    only whole-scope provenance columns `scope_source`/`scope_set_by`/`scope_set_at`
    supplier_registry.py:297-299 (one stamp for the entire scope, overwritten by every
    scope write, supplier_registry.py:1429-1434 and 1497-1501).
- **Provenance verdict (the D3 gap):** capability entries carry **no origin provenance
  today**. Classes have a `source`, but its vocabulary is `manual|apollo|inferred`
  (supplier_registry.py:304-307) — there is no `SUPPLIER_SELF`, no `asserted_by`, and the
  vocabulary is not validated on write (a caller can pass any string via the per-row
  `source` key, supplier_registry.py:1427). Brands and ship-area carry none. Critically,
  the existing propose→approve write path **erases supplier origin**: a supplier-proposed
  revision lands as a pending `review_items` row (`propose_revision`
  utils/supplier_portal.py:220-257, kind `supplier_revision` supplier_portal.py:216) and the
  concierge approve applies it via `_apply_scope_no_lifecycle`
  utils/supplier_portal.py:316-363, which stamps classes with
  `source: sr.SCOPE_SOURCE_MANUAL` (supplier_portal.py:331) and brands/territory with no
  per-entry source — so an approved supplier-originated capability is **indistinguishable
  from a concierge-entered one**. T9's `source`/`asserted_by` columns and the
  `SUPPLIER_SELF` stamp at this write path fix exactly this.

## I5. Matcher consumption — exactly where a self-declared capability could influence band assignment (the D3 seam)

**Chain:** registry scope → `tier1_matcher` → candidate dict → `ranking_bands`.

**A. tier1_matcher (utils/procurement_agent/tier1_match.py):**
- Class rows are the HARD GATE of Tier-1 eligibility: `find_suppliers_by_class`
  (tier1_matcher.py:324 → supplier_registry.py:1714-1738). A supplier whose class coverage
  includes the request's noun-class matches; onboarded-lifecycle filter at
  tier1_matcher.py:340. **A self-declared class row therefore changes WHO appears as a
  Tier-1 candidate** — eligibility, not band.
- `is_core` (tier1_matcher.py:347, `_class_is_core` :390-400) and brand relationship
  (`_brand_relationship_for` tier1_matcher.py:98-117) feed the composite score
  `_composite_score` tier1_matcher.py:275-283 (`_W_BRAND=50, _W_CORE=25, _W_TERRITORY=15,
  _W_PERF=10`, weights at :252-255) — **within-tier ordering only** (sort at
  tier1_matcher.py:382).
- `to_candidate` tier1_matcher.py:496-561 emits the fields `ranking_bands` reads:
  `is_registry_backed=True` (:556), `source_url = f"https://{match.domain}"` (:544 — a
  "real URL" by `_has_real_url` ranking_bands.py:331-332), `suitability_score` 92/70 by
  is_core (:546), `confidence_score` 90 when brand AUTHORIZED (:547),
  `found_part_number` only when a price_db entry `source="rfq"` exists
  (`_confirmed_price_for` tier1_matcher.py:471-493 — those entries are written ONLY by a
  human confirm: `reply_processor.confirm_quote` reply_processor.py:165-180 via
  api_server.py:5026-5043; grep confirms no other writer).

**B. ranking_bands (utils/procurement_agent/ranking_bands.py):**
- `assign_band` ranking_bands.py:339-383 is the band authority. Inputs that could carry
  self-declared influence:
  1. `has_confirmation` :328 (`quote_confirmed`/`supplier_confirmed`) → **Band A** (:357).
  2. `pn_evidence_for` :199-228 over `found_part_number` + `source_url` → exact/canonical
     + real URL → **Band A** (:367-369); compatible → Band B (:374).
  3. `is_registry_backed` with no PN evidence → **Band C** (:377-378) — capability alone
     does NOT cross bands today.
  4. `_EQ_SCOPE_DECLARED = +8` ranking_bands.py:70, applied at evidence_quality
     ranking_bands.py:409-410 — **within-band** evidence-quality points (allowed influence
     under D3).
  5. `suitability_score` participates only as a capped Band-B ordering input
     (ranking_bands.py:71, :412-415) — within-band.
  6. Read-time band mobility: `_transform_sourcing_results` api_server.py:1120-1125 calls
     `promote_confirmed` (ranking_bands.py:506-519) on Band-C candidates whose quote index
     entry carries a price → Band A.
- **Verdict — the seam D3 protects, precisely:** today a self-declared *capability*
  (class/brand/ship-area) can influence (a) Tier-1 eligibility (the class hard gate),
  (b) within-tier composite ordering, (c) within-band evidence quality (+8 scope-declared)
  and Band-B ordering (capped suitability) — but **cannot, by itself, change band
  assignment**: a registry-backed candidate without PN/price evidence lands Band C
  (ranking_bands.py:377), and the matcher's `found_part_number` requires a
  human-confirmed price (tier1_matcher.py:471-493). The guard T9 must add is at the
  **assign_band / evidence_quality input boundary** (ranking_bands.py:339-417): once
  capability entries carry `source=SUPPLIER_SELF` (I4), nothing stamped SUPPLIER_SELF may
  feed the Band-A/B inputs — `has_confirmation`, `pn_evidence_for`, or any future
  scope-derived PN/confirmation credit — only the within-band inputs (3)(5), which D3
  explicitly permits. The cross-band test the brief demands (C→B with a SUPPLIER_SELF
  capability) should construct a case where a SUPPLIER_SELF class/brand row is the ONLY
  evidence beyond the registry-backed class match, and assert the band stays C.
- **Adjacent door to flag for the reviewer (not a capability path, but the one
  self-service cross-band promotion that exists today):** a supplier-submitted structured
  quote (QUOTE_SUBMIT_V1, no account, no concierge sign-off unless sanity-flagged —
  `submit_quote` quote_store.py:292-395 defaults to instant "active" at :354) is adapted
  into a **"confirmed"** record (`as_confirmation_record` quote_store.py:510-541, docstring:
  "confidence None (supplier-authored, not an extraction)") which `_build_quote_index`
  merges at api_server.py:997-1006, `_quote_overlay` stamps
  `quoteConfirmed`/`supplierConfirmed` at api_server.py:1046-1048, and
  `has_confirmation` → Band A (ranking_bands.py:328,357) plus the C→A promotion loop
  (api_server.py:1120-1125). This is *by design* in QUOTE_SUBMISSION_SPEC §3 ("first look
  → confirm → win" band mobility) — a quote is an offer, not a capability claim — but T9's
  guard must be scoped so it constrains capability entries without breaking this
  deliberate quote-mobility path, and the reviewer should confirm that reading of D3.

## I6. Send path — how outbound mail enters send governance

**One seam, inside the sender, ahead of the delivery gate:**
- `GmailSender.send` email_sender.py:211-245: when `SEND_GOVERNANCE_V1` is active it runs
  `send_governance.evaluate(message)` FIRST (email_sender.py:220-226); a blocked verdict
  returns `SendResult(status=verdict.status)` — "suppressed" | "not_allowlisted" |
  "cap_blocked" — with zero network. Only then does `EMAIL_SEND_ENABLED` gate delivery
  (email_sender.py:227-229, default OFF at :58). **No caller can bypass governance by
  construction** — rfq_send, tier1_notify, the quote ack, operator scripts all come through
  here (email_sender.py:212-219).
- `evaluate` send_governance.py:355-369 — order is load-bearing: **suppression → allowlist
  → caps**. Suppression checked first and beats the allowlist
  (send_governance.py:286-299); every recipient domain (to+cc,
  `_recipient_domains` :201-209) must be on the allowlist (:302-317, empty list ⇒ blocked,
  fail-closed); caps = global per-UTC-day (default 10, :320-352, counted from
  `sent_messages` attempt rows via supplier_registry.count_send_attempts_utc_day
  :1133-1148) + per-supplier-per-part open-RFQ cap (default 1, keyed on
  `message.metadata.supplier_domain`/`part_key`, :339-348). Governance is **fail-closed by
  contract** — the opposite of the §9 external-provider fail-soft (send_governance.py:15-23).
- **Queued/released (RFQ path only):** `rfq_send.send_rfq` utils/rfq_send.py:128-271 —
  governance-active sends record the ledger row at status "released" BEFORE the attempt
  (rfq_send.py:194-203), invoke the sender even with the delivery gate off
  (rfq_send.py:212-214), then transition the row to the outcome (:238-241). The
  concierge release queue lives at api_server.py:3599-3697 (`release-queue` list/release)
  — an RFQ-draft approval step, not a general mail queue.
- **Transactional-email precedent for magic links:** `_send_quote_ack`
  api_server.py:6173-6210 — builds an `EmailMessage` (metadata carrying ids) and calls
  `GmailSender().send(msg)` directly; governance + the delivery gate run inside `send`, so
  the path structurally cannot bypass them; stubbed (outbox/log only) until sends go live.
  **Magic-link mail must enter at exactly this gate:** build `EmailMessage(to=[member
  email], metadata={...})`, call `GmailSender().send` — the T3/T6 tests then prove
  suppression via the real `evaluate` stack (allowlist empty / domain absent ⇒
  "not_allowlisted"), not a mock. Note the ack precedent records **no** `sent_messages`
  row (it only logs, api_server.py:6207-6210) while the RFQ path does; for magic links the
  audit requirement (guardrail 7, `SupplierAuthAudit`) covers the audit trail, and whether
  to also ledger them in `sent_messages` is a build-time decision to state explicitly in T3
  (a ledger row would make them count against the daily cap — likely desirable).

## I7. Open-requests data — what feeds `open-requests.tsx`

The token route is `GET /api/portal/{token}/open-requests` → `portal_open_requests`
api_server.py:6289-6337. **There is no separate backend service module** — the logic lives
inline in the handler:
- domain from the validated claim token (api_server.py:6300-6301);
- `supplier_registry.get_sent_messages(domain=dom)` filtered on
  `supplier_registry.OPEN_RFQ_STATUSES` ("sent"|"stubbed", supplier_registry.py:1043),
  deduped per run (api_server.py:6306-6312);
- request identity from `_run_specs_for_quote` api_server.py:6068-6077 (reads
  `SourcingRunORM.asset_specs_json`, strips `_`-prefixed ledger keys);
- quote state from `quote_store.get_quotes(run_id, supplier_domain)` newest-first
  (api_server.py:6317-6322);
- fail-soft to `[]` on the public surface (api_server.py:6331-6335).
**T7 consequence:** "calls the SAME service the token route uses" cannot be satisfied by
importing a service — the cleanest compliant shape is to extract the handler body into a
shared helper (e.g. in `utils/supplier_portal.py`, which already owns the portal's read
logic — supplier_portal.py:1-7) and have BOTH the token route and the session route call
it. That is a modification of existing backend code (permitted — the prime directive only
forbids `frontend/` and existing *test* edits) but it must be behavior-preserving for the
token route; alternatively T7 duplicates the read sequence against the same three stores.
This is flagged as a build-time decision; extraction is recommended.

## I8. Feature-flag convention — mirror it exactly

- Declaration: module-level `_env_truthy` helper (strict truthy: only "1/true/yes/on";
  everything else fails safe OFF) + a live-read function. Instances:
  - `SUPPLIER_PORTAL_V1`: constant api_server.py:41 + live `_portal_enabled`
    api_server.py:44-47; store-side defense-in-depth `CLAIM_TOKENS_ENABLED`
    claim_tokens.py:76-80.
  - `INTAKE_CHANNELS_V1`: `_intake_enabled` api_server.py:59-61.
  - `QUOTE_SUBMIT_V1`: `_quote_submit_enabled` api_server.py:74-77; store-side
    `quote_submit_active` quote_store.py:64-66.
  - `RANKING_BANDS_V1`: `ranking_bands_active` ranking_bands.py:83-86.
  - `SEND_GOVERNANCE_V1`: `send_governance_active` send_governance.py:79-82.
  - `TIER1_V2`: supplier_registry.py:81-85 (import-time capture, live-checked via
    `getattr` at tier1_matcher.py:76-80).
- Route gating: public routes raise the **byte-identical-to-unknown-route 404**
  (`{"detail":"Not Found"}`) at handler entry when the flag is off —
  `_portal_flag_off_404` api_server.py:5513-5518, `_quote_flag_off_404`
  api_server.py:5999-6001; uniform token rejection uses the same body
  (api_server.py:5521-5526). Admin flag-gated routes instead use **503 dormant**
  (`_require_portal_enabled` api_server.py:5314-5322) with the flag-before-admin gate
  ordering at api_server.py:6415-6420 (see I3).
- Test pinning: `utils/procurement_agent/tests/conftest.py:71-74` lists the flag envs
  (`_FEATURE_FLAG_ENVS`) that the autouse fixture pins to `""` (OFF) for every test
  (conftest.py:109-110), plus `_FEATURE_FLAG_MODULE_ATTRS` for import-time-captured
  module attrs (conftest.py:77-81). **`SUPPLIER_ACCOUNTS_V1` must be added to the conftest
  flag list** — that is an edit to `conftest.py`, an existing test *fixture* file. The brief
  forbids editing "any existing test file"; conftest.py is arguably a fixture, not a test
  file, but this is a **judgment call the reviewer must rule on** (see FINDINGS F1). The
  alternative (per-test monkeypatch.setenv in every new test file) works without touching
  conftest but leaves the flag unpinned for pre-existing tests — harmless, since default
  is OFF and nothing existing reads it.

## I9. Registrable-domain helper — exists; reuse it

- **Backend:** `utils/url_normalize.py:registrable_domain(url_or_host)`
  url_normalize.py:68-104 — eTLD+1 heuristic (last two labels; last three for a known
  two-part public suffix set at :58-63; IPv4/single-label passthrough; "" on bad input;
  never raises; pure stdlib). It returns the registrable domain of a URL or bare host —
  exactly what D2's member-domain matching needs (`sales@dxpe.com` → `dxpe.com`).
  **Gap:** no backend public-mailbox-domain (gmail/outlook/yahoo/…) list exists anywhere
  (grep over `utils/**` finds none outside bounce-parser test fixtures) — T3 must define
  one (new module, its own constant set) for D2's "public-mailbox domains can never
  auto-match" rule.
- **Frontend (reference only, not touched):** `registrableDomain`
  frontend/src/components/proc/options-compose.ts:43 — same concept, frontend-side; no
  reuse across the wire, cite-only.
- Also relevant: every store normalizes domains through
  `supplier_registry._normalize_domain` by delegation (send_governance.py:85-89,
  quote_store.py:162-166, quote_tokens.py:127-129) — the account store should do the
  same so `supplier_domain` keys stay identical across stores.

---

## Baseline test count (gate-time run)

`uv run pytest -q` executed on `arc2/supplier-identity` at gate time (exit 0):

> **BASELINE: 2305 passed, 73 skipped, 1 warning in 419.51s** — the 2305 figure the
> brief's success criterion 1 names. Zero failures; no test file was touched for this run.

## FINDINGS (genuine, none blocking; all are build-time decisions or judgment calls)

- **F1 — conftest flag-list edit vs "no existing test file" edits (I8).** Pinning
  `SUPPLIER_ACCOUNTS_V1` off for the whole suite requires adding it to
  `_FEATURE_FLAG_ENVS` (conftest.py:71-74), which edits an existing file under `tests/`.
  The prime directive's letter forbids this; its intent (existing tests pass unmodified)
  is satisfiable either way. Recommendation: ask the reviewer/human to rule; default
  fallback is per-test `monkeypatch.setenv` in the new files only.
- **F2 — no shared open-requests service exists (I7).** T7's "same service" requires
  either extracting a helper from `portal_open_requests` (api_server.py:6289-6337,
  behavior-preserving modification of existing backend code — permitted) or duplicating
  the read sequence. Extraction into `utils/supplier_portal.py` recommended.
- **F3 — the quote-mobility path is a live self-service cross-band promotion (I5).**
  A structured quote submitted via the public token form promotes its supplier to Band A
  with no concierge sign-off (quote_store.py:354 → :510-541 → api_server.py:997-1006 →
  ranking_bands.py:328,357). Deliberate per QUOTE_SUBMISSION_SPEC §3, and a quote is not a
  "capability" under D3 — but T9's guard wording must not accidentally break it, and the
  reviewer should confirm D3 is scoped to capability entries only.
- **F4 — one-OWNER uniqueness at the persistence layer (I1/T1).** The standalone-sqlite
  convention has no ORM constraint machinery; enforce via a partial unique index
  (`WHERE role='OWNER'`) or an INSERT/UPDATE trigger in the new store's DDL, and test it
  at the storage layer as the brief demands.
- **F5 — claim_tokens cannot be reused as the magic-link store (I2).** Namespace
  isolation is a settled precedent (quote_tokens.py:9-19): a claim token must never
  authenticate a member session. The magic-link store must be new, re-implementing the
  hygiene helpers (they are small and private to claim_tokens).
- **F6 — rate limiting for `request-link` (guardrail 4).** The existing throttle pattern
  is the in-process fixed-window limiter (api_server.py:5471-5510 / :5989-6030) keyed
  `(IP, token-prefix)`. For request-link the per-email key replaces token-prefix; per-IP
  and per-email buckets both needed. In-process is acceptable for this arc per the brief;
  note as follow-up (single-process only, lost on restart).
- **F7 — sent_messages ledger for magic-link sends (I6).** The quote-ack precedent sends
  without a ledger row; the RFQ path ledgers at "released" pre-attempt. If magic-link
  sends ledger into `sent_messages` they count against the governance daily cap
  (send_governance.py:320-338) — arguably correct (they are sends). Decision needed in T3;
  default recommendation: ledger them (honest counting) and state it in the task's notes.

## Follow-ups (noted, not built)

- Rate limiter → Redis-backed in production (F6).
- Multi-account membership (one member email → one account) — out of scope per brief.
- Public-mailbox domain list needs periodic review as providers appear (I9 gap).

---

*Gate complete. No implementation written. Stopping before T1 per the brief.*

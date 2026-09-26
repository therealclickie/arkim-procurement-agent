# BUYER_IDENTITY_REPORT — Arc 6 investigation gate (K1–K7)

**Branch:** `arc6/buyer-identity` (HEAD `2dedee9`; `test/flag-on-integration` is an ancestor).
**Scope of this turn:** read-only investigation. No source, test or config file modified.
**Baseline counts (flags off, this HEAD, run this turn):**
- Backend `uv run pytest -q`: **3322 passed, 73 skipped, 1 warning** (238 s).
- Frontend `npx vitest run`: **33 files, 246 tests passed**.

All `api_server.py:N` references for routes point at the route decorator line.

---

## K1 — Tenant / company / facility model

**There is no tenant, company or facility table.** What exists:

| Thing | Where | Notes |
|---|---|---|
| Tenant → company/facility map | `utils/intake_channels.py:221-224` | Python dict `_TENANT_MAP = {"bayfoods": {"company_id": "company-bayfoods", "facility_id": "fac-stockton"}}`. Only consumer path is the intake channels (email plus-address `resolve_tenant_from_address` `:232-260`, `tenant_lookup` `:263-268`, phone map `_NUMBER_TENANT_MAP` `:275-278`). |
| Known senders | `utils/intake_channels.py:313-325` | SQLite `intake_known_senders(tenant_key, sender_email, ...)`. |
| Facilities | `api_server.py:3458-3464` | `_MOCK_FACILITIES`: five hard-coded "Bay Foods · <city>" facilities. Served by `GET /api/facilities` (`api_server.py:3468`). |
| Default facility | `api_server.py:675`, `:707`; `utils/models.py:268` | all-zeros UUID; the React request flow never sends `facility_id` (`frontend/src/components/proc/request-screen.tsx:200,231,268`), so React-created runs land on it. |
| Sites (ship-to) | `utils/site_settings.py:20-36` | `site_shipto(site_id PK, company, ...)`; `site_id` is a free string from `frontend/src/lib/proc-config.ts:37-66`. Not linked to facilities or companies. |
| Cognito company PINs | `utils/auth/cognito.py:29-32,49-77`; `utils/auth/dependencies.py:40-75,97-138` | **A ported Cognito buyer identity already exists** (see FINDINGS F1): roles `admin/monitoring/technician/procurement` per company PIN, carried in the JWT; active company from `X-Arkim-CompanyId` header, accepted only if the token grants it (`dependencies.py:124-127`). |

**How a run is tied to a facility/company.** `SourcingRunORM` (`utils/procurement_agent/state/persistence.py:66-90`): `facility_id` NOT NULL (`:70`), `company_id` nullable `String(36)` (`:75`, documented as "the validated Cognito tenant PIN"), `session_id` (`:89`, DEMO_MODE token), `initiated_by_user_id` (`:90`, never written by `api_server.py` — comment `api_server.py:4535`). Migration `api_server.py:354`.

| Creation path | facility_id source | company_id source |
|---|---|---|
| `POST /api/runs` `api_server.py:2023` | body (`:2054`) | `caller.company_id` or NULL (`:2057`) |
| `POST /api/requests` `api_server.py:2140` (fan-out `_fan_out_intake` `:2106`) | body (`:2125`) | caller (`:2122`) |
| `POST /api/runs/from-maintenance` `api_server.py:2185` | body (`:2197`) | caller (`:2198`) |
| Intake email/SMS/voice → `_fire_sourcing_run_for_intake` `api_server.py:3342-3400` | `_TENANT_MAP` (`:3376`) | `_TENANT_MAP` (`:3377`) = `"company-bayfoods"` |
| Seeded handoffs `_seed_demo_maintenance_run` `api_server.py:386-428` | handoff JSON (`:414`) | not set (NULL) |

Because the React app sends no `Authorization` header (`frontend/src/lib/api.ts:81-99`), `caller` is always `None` from the UI, so **every React-created run has `company_id = NULL`**; only intake-channel runs carry `company-bayfoods`.

**Orders:** `utils/orders.py:69-90` — `company_id` (`:73`, migration `:96-97`) copied from the run (`api_server.py:2980`, `:5321-5328`; `utils/procurement_agent/agents/procurement_agent.py:104-107`); `placed_by` (`:86`). No `facility_id`.

**"Northgate Manufacturing" in the header (finding 5):** hard-coded. `frontend/src/lib/proc-config.ts:10` `PROC_TENANT = { name: "Northgate", sub: "Manufacturing" }`, rendered at `frontend/src/components/proc/proc-shell.tsx:203-204` (comment `:13-14`: a fixture "until customer auth"). The shell never reads a run's `facility_id`/`company_id` or calls `/api/facilities` (the only place "Bay Foods" names exist); `useFacilities` is used only in the legacy `frontend/src/app/runs/new/page.tsx:46`. Hence Bay Foods runs show Northgate. Ship-to fallback "Northgate Manufacturing Co." is also a fixture (`proc-config.ts:43,56`).

**Proposed binding for D1 (pending the GATE STOP below):** a `buyer_companies` row keyed by the existing company id string (`company-bayfoods`, which fits `String(36)`), carrying its facility ids (`fac-stockton`, …) so `_TENANT_MAP`, `_MOCK_FACILITIES` and runs/orders `company_id` all resolve to one entity.

---

## K2 — Every buyer-facing API endpoint and its current auth status

**Method.** Enumerated at runtime from `api_server.app.routes` (script: import `api_server`, walk every `APIRoute`, recurse `route.dependant.dependencies`). **127 API routes + 4 FastAPI auto-routes** (`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`). All routes are registered directly on `app` in `api_server.py` — **no `include_router`** exists. Middleware: CORS (`api_server.py:244`) and the DEMO_MODE allowlist (`api_server.py:315`, inert unless `DEMO_MODE`). **No middleware authenticates buyers.**

**Buyer-facing rule applied** (the one T4's structural guard should encode): every route NOT under `/api/supplier/`, `/api/admin/`, `/api/webhooks/`, `/api/portal/{token}` / `/api/quote/{token}` (supplier capability-token surfaces), `/api/intake/` (inbound channel webhooks), `/api/health`. By that rule **45 existing routes are buyer-facing** (B1–B45; B46–B48 are the routes this arc adds), plus 2 unassigned dev/ops routes (B49-B50) that the rule catches but that are not buyer surfaces (see GATE STOP Q3).

"Auth today" legend: **NONE** = no dependency, no inline check; **OPT-COGNITO** = `Depends(get_caller)` which returns `None` without a bearer (`utils/auth/dependencies.py:110-112`) — i.e. unauthenticated in practice. "DEMO-scope" = DEMO_MODE `X-Session-Id` ownership check (`api_server.py:458-506`), inert with DEMO_MODE off. Resource key = what must be company-scoped.

| # | Method | Path | Route | Auth today | Resource key / isolation lookup | Proposed D2 capability |
|---|---|---|---|---|---|---|
| B1 | POST | /api/runs | `api_server.py:2023` | OPT-COGNITO | creates run → stamp session company | raise_request |
| B2 | PUT | /api/runs/{run_id}/asset-specs | `api_server.py:2073` | NONE | run_id → run.company_id | raise_request |
| B3 | POST | /api/requests | `api_server.py:2140` | OPT-COGNITO | creates run(s)/group → stamp company | raise_request |
| B4 | GET | /api/runs | `api_server.py:2163` | NONE | list → filter by company | view |
| B5 | POST | /api/runs/from-maintenance | `api_server.py:2185` | OPT-COGNITO (S2S from core: JWT + service signature, docstring `:2190-2192`) | creates run — **see GATE STOP Q2** | (S2S) |
| B6 | GET | /api/runs/{run_id} | `api_server.py:2219` | NONE (+DEMO-scope `:2228`) | run_id | view |
| B7 | POST | /api/runs/{run_id}/open-from-pending | `api_server.py:2238` | NONE | run_id | raise_request |
| B8 | POST | /api/runs/{run_id}/reject-submission | `api_server.py:2266` | NONE | run_id | raise_request |
| B9 | POST | /api/runs/{run_id}/messages | `api_server.py:2284` | NONE (+DEMO-scope `:2294`) | run_id | raise_request |
| B10 | POST | /api/runs/{run_id}/upload | `api_server.py:2455` | NONE (+DEMO-scope `:2474`) | run_id | raise_request |
| B11 | POST | /api/runs/{run_id}/request-confirmation | `api_server.py:2711` | NONE | run_id | raise_request |
| B12 | POST | /api/runs/{run_id}/select-candidate | `api_server.py:2770` | NONE | run_id | select_and_order |
| B13 | POST | /api/runs/{run_id}/order-now | `api_server.py:2866` | NONE | run_id | select_and_order |
| B14 | POST | /api/runs/{run_id}/approve | `api_server.py:3014` | OPT-COGNITO | run_id | approve_within_limit |
| B15 | POST | /api/runs/{run_id}/reject | `api_server.py:3092` | NONE | run_id | approve_within_limit |
| B16 | POST | /api/runs/{run_id}/confirm-intake | `api_server.py:3127` | NONE (+DEMO-scope `:3185`) | run_id | raise_request |
| B17 | POST | /api/runs/{run_id}/outreach | `api_server.py:3403` | NONE | run_id | select_and_order |
| B18 | POST | /api/runs/{run_id}/save-outreach | `api_server.py:3435` | NONE | run_id | select_and_order |
| B19 | GET | /api/facilities | `api_server.py:3468` | NONE | list → company's facilities | view |
| B20 | GET | /api/approval-rules/{facility_id} | `api_server.py:3478` | NONE | facility_id → company | view |
| B21 | POST | /api/approval-rules | `api_server.py:3517` | NONE | body.facility_id → company | set_approval_limit (Admin) |
| B22 | POST | /api/runs/{run_id}/execute | `api_server.py:4476` | NONE | run_id | select_and_order |
| B23 | POST | /api/runs/{run_id}/mark-delivered | `api_server.py:4489` | NONE | run_id | select_and_order |
| B24 | GET | /api/runs/{run_id}/orders | `api_server.py:4501` | NONE | run_id | view |
| B25 | GET | /api/orders | `api_server.py:4509` | NONE (docstring `:4511-4514`: "binds to the tenant when real auth lands") | list → orders.company_id | view |
| B26 | GET | /api/reorder | `api_server.py:4520` | NONE | aggregate over orders → company filter | view |
| B27 | GET | /api/events | `api_server.py:4666` | NONE | derived feed over orders/approvals/quotes → company filter | view |
| B28 | GET | /api/groups/{group_id} | `api_server.py:4764` | NONE (+DEMO-scope `:4781`) | group_id → child runs' company | view |
| B29 | POST | /api/groups/{group_id}/approve | `api_server.py:4857` | OPT-COGNITO | group_id | approve_within_limit |
| B30 | POST | /api/groups/{group_id}/reject | `api_server.py:4930` | OPT-COGNITO (caller unused) | group_id | approve_within_limit |
| B31 | POST | /api/runs/{run_id}/rfq-draft | `api_server.py:5017` | NONE | run_id | select_and_order |
| B32 | GET | /api/rfq-drafts/{draft_id} | `api_server.py:5056` | NONE | draft_id → draft.run_id → company | view |
| B33 | GET | /api/runs/{run_id}/rfq-drafts | `api_server.py:5070` | NONE | run_id | view |
| B34 | POST | /api/rfq-drafts/{draft_id}/approve | `api_server.py:5078` | NONE | draft_id → run | select_and_order |
| B35 | POST | /api/rfq-drafts/{draft_id}/reject | `api_server.py:5093` | NONE | draft_id → run | select_and_order |
| B36 | POST | /api/rfq-drafts/{draft_id}/send | `api_server.py:5106` | NONE | draft_id → run | select_and_order |
| B37 | GET | /api/sites/{site_id}/ship-to | `api_server.py:5200` | NONE | site_id → company (**no site→company link exists**, K1) | view |
| B38 | PUT | /api/sites/{site_id}/ship-to | `api_server.py:5209` | NONE | site_id → company | Admin settings (proposed: set_approval_limit-class "manage company settings") |
| B39 | GET | /api/runs/{run_id}/review-items | `api_server.py:5228` | NONE | run_id | view |
| B40 | POST | /api/runs/{run_id}/process-replies | `api_server.py:5241` | NONE | run_id | select_and_order |
| B41 | POST | /api/review-items/{item_id}/confirm | `api_server.py:5262` | NONE | item_id → run | select_and_order |
| B42 | POST | /api/review-items/{item_id}/reject | `api_server.py:5282` | NONE | item_id → run | select_and_order |
| B43 | POST | /api/review-items/{item_id}/place-order | `api_server.py:5294` | NONE | item_id → run | select_and_order |
| B44 | GET | /api/runs/{run_id}/impact | `api_server.py:5350` | NONE | run_id | view |
| B45 | GET | /api/impact | `api_server.py:5360` | NONE | aggregate over orders → company filter | view |
| B46–B48 | — | *(new in this arc)* `/api/buyer/me`, `/api/buyer/members*`, `/api/buyer/settings*` | — | — | session company | per D2 |
| B49 | GET | /api/debug/llm | `api_server.py:3581` | NONE (spends an Anthropic call, echoes key prefix) | none | **GATE STOP Q3** |
| B50 | POST | /api/dev/reseed-handoffs | `api_server.py:3610` | NONE (deletes/re-seeds runs across all tenants) | all runs | **GATE STOP Q3** |

**Exempt routes (not buyer-facing), with current auth — the proposed T4 allowlist categories:**

| Category | Routes | Auth today |
|---|---|---|
| Admin (Gofer operator) | 50 `/api/admin/*` routes, `api_server.py:3670-5889`, `:6743-6804`, `:7356-7393`, `:7945-8017` | `Depends(require_admin)` (bearer vs `ARKIM_ADMIN_TOKEN`, `api_server.py:3651-3667`), or inline `require_admin(authorization)` after a flag check on 11 routes: `:6743,6772,6785,6804` (quotes), `:7356,7368,7393` (supplier-members), `:7945,7977,7996,8017` (notifications) |
| Supplier session | 16 `/api/supplier/*` routes `api_server.py:6884-7910` | `_require_supplier_session` (+ `_supplier_require_capability` `dep`) except request-link `:6884` / verify `:7068` (public by design) |
| Supplier capability tokens | `/api/portal/{token}/*` `:5823,5846,6631,6677,6691,7694`; `/api/quote/{token}` `:6466,6504` | path token (claim/quote token) |
| Inbound channels | `/api/intake/email` `:6001`, `/api/intake/confirm/{token}` `:6051`, `/api/intake/sms` `:6102`, `/api/intake/voice` `:6165` | flag gate `_require_intake_enabled` only — **no sender authentication** (FINDINGS F6); runs attributed per the gate ruling (`acting_member: null`, `channel`) |
| Webhook | `/api/webhooks/ses` `:7850` | SNS signature + topic allowlist |
| Health | `/api/health` `:3560` | NONE (by design) |
| FastAPI auto-routes | `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` | NONE (non-`APIRoute`; the guard must skip or pin them) |

Route count check (runtime dump, by prefix): `/api/admin` 50 + `/api/supplier` 16 + `/api/portal`/`/api/quote` 8 + `/api/intake` 4 + `/api/webhooks` 1 + `/api/health` 1 + `/api/debug`/`/api/dev` 2 + buyer B1–B45 45 = **127** — every runtime `APIRoute` is classified in one of the two tables above.

---

## K3 — Arc 2/3 auth machinery: what is importable as-is

| Piece | Where | Reusable for buyer? |
|---|---|---|
| Magic-link mint/verify | `utils/supplier_accounts.py:744` `mint_magic_link`, `:779` `verify_magic_link` (atomic consume `:806-811`, member must be ACTIVE `:816`) | **No — supplier tables** (`supplier_magic_links` `:237`), own sqlite (`:187`), gated on `SUPPLIER_ACCOUNTS_V1` (`_dormant` `:91`). Pattern is cloned into a buyer store. |
| Token hashing | `utils/supplier_accounts.py:330-332` `_hash_token` (SHA-256), `secrets.token_urlsafe(32)`, 8-char `token_prefix` (`:189-190,768`); link 30 min, session 24 h (`:191-192`) | Private helpers; module docstring `:20-23` says hygiene helpers are deliberately re-implemented per store. Buyer store re-implements (or a small shared helper — would touch supplier module, so **no**). |
| Sessions | `utils/supplier_accounts.py:827` `create_session`, `:861` `validate_session`, `:893` `revoke_session` | No (supplier tables). Clone. |
| Cookie | `api_server.py:7026` `SUPPLIER_SESSION_COOKIE = "gofer_supplier_session"`; set `:7045-7057` (`httponly=True, secure=True, samesite="lax", path="/"`, max_age from expiry `:7029-7042`); clear `:7060-7065` | Clone with `gofer_buyer_session`. |
| Session dependency | `api_server.py:7181-7215` `_require_supplier_session` (bearer first, then cookie; records `request.state.supplier_auth_mode`; CSRF `:7214`; uniform 401 `:7116-7119`) | No (hard-wires supplier store, flag, cookie param name). Thin buyer clone. |
| CSRF origin check | `api_server.py:7164-7178` `_supplier_csrf_check(request, mode)`; `_origin_of` `:7152`; methods `:7149`; allowed = `_cors_origins` `:225-242` | **Yes, importable as-is** — logic is identity-agnostic (only its 401 helper is supplier-named, and that body is generic "Invalid or expired session"). Call it from the buyer dependency; do not rename (would modify supplier code). |
| Uniform request-link response | `api_server.py:6916,6929,6936,6950,6957,6966` (same `{"ok": true}` + `_portal_response_headers` `:5780`) | `_portal_response_headers` importable as-is; pattern cloned. |
| Rate limiter | `api_server.py:6843-6877` `_supplier_auth_rate_bump` (email+IP buckets, env caps `SUPPLIER_AUTH_RATE_CAP_*`), `:6983` verify limiter, shared `_supplier_auth_rate_buckets` `:6839-6840`; `_client_ip` `:5711` | `_client_ip` importable. Calling the supplier bump would **share buckets/env caps** with suppliers — prefer a buyer-named clone with its own bucket dict (keeps supplier behaviour and its tests untouched). |
| Auth mail + send governance | `utils/supplier_accounts.py:1075-1146` `send_magic_link_email` (`message_class="auth"`, `notifications.record_auth_send` `:1127`, `GmailSender().send` `:1129`); URL `magic_link_url` `:1068` from `SUPPLIER_PORTAL_BASE_URL`; governance `utils/email_sender.py:211-226` → `utils/send_governance.py:403-417` (allowlist fail-closed `:343-353`, caps keyed on `metadata.supplier_domain` `:368-389`) | `GmailSender`/governance importable; the email builder is supplier-specific → buyer builder. **Buyer email domains must be allowlisted** or links are blocked (`not_allowlisted`) — FINDINGS F7. |
| Audit | `utils/supplier_accounts.py:1272` `audit(...)` → `supplier_auth_audit` (`:263-276`), `list_audit` `:1300` | Pattern only; buyer audit table. |
| RBAC matrix | `utils/supplier_accounts_rbac.py:58-66` capability constants, `:74-83` `CAPABILITY_MATRIX`, `:86-93` `has_permission` (fail-closed on non-ACTIVE/unknown role); dependency factory `api_server.py:7427-7436` `_supplier_require_capability`; error map `:7439-7453` | Pattern only (imports supplier store/roles `:38-51`). New `utils/buyer_accounts_rbac.py` mirrors it. |
| Source-scan test | `utils/procurement_agent/tests/test_supplier_accounts_rbac.py:350-372` — hard-coded list of 14 handlers, forbids quoted `OWNER/ADMIN/MEMBER` and `.role ==` | Does **not** cover buyer handlers; T2 adds a buyer scan in a new test file (preferably enumerating handlers from `app.routes` rather than a hand list). |
| Cognito identity (existing buyer auth) | `utils/auth/dependencies.py:49-167` (`Caller`, `get_caller`, `require_role`, `check_company_role`) | Exists and is wired (optionally) into 6 buyer routes — **GATE STOP Q1**. |

---

## K4 — Attribution points (D7)

| Action | Endpoint | Field | Current source | Persisted at |
|---|---|---|---|---|
| Create run | B1 `api_server.py:2023` | `company_id` | `caller.company_id` / NULL (`:2057`) | `persistence.py:75` via `_new_run_orm` `api_server.py:1991-2020` |
| | | `initiated_by_user_id` | **never set** (`_new_run_orm` has no param) | `persistence.py:90` |
| Create request | B3 `:2140` | `company_id` | caller (`:2122`) | same; no actor |
| From maintenance | B5 `:2185` | `submitted_by` | body free text (`MaintenanceSubmission` `:669`) | inside `maintenance_handoff_json` (`:2194,2203`) |
| Intake channels | `:6001/:6102/:6165` | sender | body `from`/number | `IntakeEvent` only; run gets tenant company/facility (`:3376-3377`), no actor — gate ruling: `acting_member: null`, `channel` marker |
| Confirm intake + `source_anyway` | B16 `:3127` (query `:3134`) | `acknowledged_by` | **None** — `intake_sufficiency.record_override(specs_dict, readiness.identity)` `api_server.py:3257` passes no actor (`utils/intake_sufficiency.py:106-123`, default None `:107,121`) | `asset_specs_json` `spec_incomplete_ack` (`intake_sufficiency.py:117-123`), written `api_server.py:3261` |
| | | hygienic ack | **no actor field at all** — `utils/intake_readiness.py:161-180`, called `api_server.py:3259` | `asset_specs_json` `hygienic_override_ack` |
| Request confirmation | B11 `:2711` | — | none | — |
| Select candidate | B12 `:2770` | — | none (only `selected_at`) | `selected_candidate_json` `:2789-2798` |
| Approve run | B14 `:3014` | `approver_id` | `caller.user_id` / None (`:3033`) | `approval_history_json` entry `:3057-3066` |
| | | `approver_name`, `approver_role` | **body free text** (`ApproveRequest` `api_server.py:742-745`) | same entry `:3060-3061` |
| Reject run | B15 `:3092` | `approver_name`, `approver_role` | body free text; no caller, no `approver_id` | `approval_history_json` `:3108-3116` |
| Approve basket | B29 `:4857` | `approver_id` + name/role | caller (`:4876`) + body | `RequestGroupApprovalORM.approvals_received_json` `:4894-4903`; children get `"(basket decision)"` `:4845-4848` |
| Reject basket | B30 `:4930` | name/role | body only (caller ignored) | children `approval_history_json` `:4962-4971` |
| Execute | B22 `:4476` | `placed_by` | `_latest_approver(run)` = last approval's **typed `approver_name`** (`utils/procurement_agent/agents/procurement_agent.py:157-162`) — this is how "Dana Plant-Manager" reaches an order (typed into `frontend/src/components/proc/approval-actions.tsx:130-140`, sent `:109,113`) | `orders.placed_by` via `place_order` (`procurement_agent.py:121` → `utils/orders.py:267-269`) |
| | | `company_id` | `run.company_id` (`procurement_agent.py:107`) | `utils/orders.py:222` |
| Order-now | B13 `:2866` | `placed_by` | not passed (`:3001-3002`); NULL | `utils/orders.py:235` |
| Place order from quote | B43 `:5294` | `placed_by` | constant `"buyer"` (`:5328,5331`) | `utils/orders.py:268` |
| RFQ draft approve / reject | B34 `:5078` / B35 `:5093` | `approved_by` / `rejected_by` | **body free text** (`RfqDraftApproveRequest` `:5009-5010`, `RfqDraftRejectRequest` `:5013-5014`) | `persistence.py:205,207,546,549` |
| RFQ send | B36 `:5106` | `approved_by` (copied from draft) | draft row (`:5162`) | `sent_messages.approved_by` via `utils/rfq_send.py:199`; `audit_log.user_selection` `rfq_send.py:262` |
| Mark purchased (admin, out of D7) | `:4101` | `placed_by` | constant `"operator"` (`:4128`) | `utils/orders.py:268` |
| Approval-rules / ship-to / outreach | B21, B38, B17/B18 | — | no actor recorded | `persistence.py:148-161`; `site_settings.py:26-35`; `:3421,3442` |

`utils/audit_log.py` has a nullable `user_id` column (`:13,75,142`) whose only writer is the non-shipping orchestrator (`utils/procurement_agent/orchestrator/core.py:429`). `run_capture.capture_user_action` (`utils/run_capture.py:314-318`) records no actor.

---

## K5 — Admin API

- `require_admin` `api_server.py:3651-3667`: bearer compared (constant-time) to `ARKIM_ADMIN_TOKEN`; unset → 503, missing → 401, wrong → 403; returns the literal `"admin"`. No operator identity exists (comment `:3639-3649`) — so audit rows for company creation can record only `actor="admin"`.
- Flag-first convention for new admin routes: flag check → 404 byte-identical, then inline `require_admin(authorization)` (rationale `api_server.py:6736-6741`, `:7331-7336`; example `:7356-7414`).
- **No endpoint creates a company, tenant, facility or member today.** The natural home for `POST /api/admin/buyer-companies` and `POST /api/admin/buyer-companies/{id}/invite-admin` is beside the arc 2 admin-member block (`api_server.py:7331-7414`), following that convention. The first Admin is created directly with the Admin role (unlike supplier `invite_member`, which forbids inviting an OWNER, `utils/supplier_accounts_rbac.py:116-119`).

---

## K6 — Frontend buyer routes and fetching

- **Buyer pages** (all under `ProcShell`, a client component `frontend/src/components/proc/proc-shell.tsx:1`): `/` (`src/app/page.tsx:9-14`, shell inside the page), `/request`, `/approvals`, `/history`, `/impact`, `/settings`, `/parts/[id]` (each via its `layout.tsx:3-6` → `ProcShell`). Legacy `/runs`, `/runs/new`, `/runs/[id]` use a separate frame `src/app/runs/layout.tsx:4-10`. `/design` is a showcase.
- **Fetching:** `frontend/src/lib/api.ts:81-114` `request<T>()` — no `credentials`, sends only `Content-Type` + `X-Session-Id` (localStorage `arkim-demo-session-id`, `:56-75`, DEMO_MODE key, not auth). Header-spread quirk: `...options` after `headers` (`:96-98`) replaces the default headers when a caller passes any. React Query hooks in `src/lib/queries.ts`. Rewrite `/api/*` → backend `next.config.ts:11-23`.
- **No `middleware.ts`**; root layout `src/app/layout.tsx:44-55` is a server component. **Guard placement:** inside `ProcShell` (covers `/` and the six shell routes) plus the `/runs` layout; buyer login/verify pages at `/login` and `/verify` (or `/buyer/login`, `/buyer/verify`) **outside** `ProcShell`, as the supplier guard sits per page (`src/app/supplier/session-guard.tsx:14-17`).
- **Reusable arc 3 pieces:** `src/lib/supplier-api.ts:125-157` `sessionFetch` (`credentials: "include"`, 401 → `UNAUTHORIZED`) — generic except the `/api/supplier` prefix; `src/lib/use-supplier-session.ts:43-91` hook — generic shape; `src/app/supplier/verify/verify-screen.tsx:59,75-95` explicit "Continue to sign in" click with double-submit ref (paths hard-coded `:50-51`); `session-guard.tsx:33-73` redirect logic; `supplier-shell.tsx:98-115` sign-out pattern. Recommendation: buyer-side copies/parameterised wrappers; do not edit supplier components (keeps arc 3 tests byte-identical).
- **Flags:** `src/lib/flags.ts:14-55` (literal `process.env.NEXT_PUBLIC_*`, `isOn` `:24-27`); add `buyerSessionEnabled()`. Tests use `vi.stubEnv` + per-file `vi.unstubAllEnvs()`.
- **Identity in UI today:** header fixture (K1); approver "Your name" free-text field `src/components/proc/approval-actions.tsx:130-140`; avatar hard-coded "CS" `proc-shell.tsx:251-253`; `placed_by` only in the type `src/types/index.ts:515`.
- **Storage:** admin token in localStorage `arkim_admin_token` (`src/app/admin/page.tsx:31`); buyer session must be cookie-only (T8 storage test via `src/test-support/fetch-seam.ts:70` `storageDump`).

---

## K7 — Proposed test edits

Principle: D8 keeps flag-off behaviour byte-identical, and every pre-existing test runs with flags pinned off, so **no pre-existing assertion on `placed_by`, `acknowledged_by`, `approver_name` or `company_id` is superseded** — the flag-on attribution behaviour is tested in new files. Checked and left untouched: `test_orders.py:44-249`, `test_procurement_execute.py:52,111,127`, `test_admin_api.py:195,432-436,451-519,812-824`, `test_api_server.py:651-663,731,842-857`, `test_intake_sufficiency.py:78-83`, `test_notifications_*` (admin alert ack, not a buyer action), `frontend/src/app/admin/__tests__/notification-alerts-tab.test.tsx:36,48` (admin alerts). The one required edit is flag hygiene: conftest's comment (`conftest.py:66-70`) requires every new flag be added to the pin list, otherwise a shell with `BUYER_ACCOUNTS_V1=1` would leak into the whole suite.

PROPOSED TEST EDIT: utils/procurement_agent/tests/conftest.py :: 71-75 (and 77-86 only if the flag is bound at import time as a module attribute) :: none superseded — the flag-pin list omits the new flag :: additive only: append "BUYER_ACCOUNTS_V1" to _FEATURE_FLAG_ENVS (and its module-attr tuple if import-bound); no existing entry changed

---

## FINDINGS

- **F1 — A buyer identity already exists (brief premise gap).** `utils/auth/` is a ported Cognito identity (`utils/auth/__init__.py:1-8`) with per-company roles `admin/monitoring/technician/procurement` (`utils/auth/cognito.py:29-32`), optionally wired into B1, B3, B5, B14, B29, B30 (`api_server.py:2027,2144,2186,3015,4858,4931`). It drives `runs.company_id` (`:2057,2122,2198`) and M1 distinct-approver enforcement on `approver_id` (`:3033-3052`, `:4876-4892`). The React app never sends a token, so it is dormant in practice, but it is the identity the core platform issues. The brief's "the buyer side has none" is not quite true → GATE STOP Q1.
- **F2 — Every React-created run and order has `company_id = NULL`** (K1). Under `BUYER_ACCOUNTS_V1` with strict isolation, all pre-existing and seeded runs (`_seed_demo_maintenance_run` `api_server.py:386-428` sets no company) become invisible to every company. Proposed: seeding stamps `company-bayfoods`; legacy NULL rows stay invisible under the flag (same stance as DEMO_MODE NULL-session rows, `api_server.py:492-506`).
- **F3 — Two approval-policy stores.** Facility-scoped `approval_rules` (thresholds, approvers_required, roles; `persistence.py:148-161`) already drive routing via `determine_approval_path` (`api_server.py:2787,2937`) and are editable unauthenticated at B21. D5 adds a company-level `auto_approval_limit`. This arc stores D5 without changing order behaviour; arc 7 must reconcile the two (flag in CLEANUP).
- **F4 — `create_order` discards `placed_by`** (`utils/orders.py:235`); only `place_order` writes it. Order-now orders have `placed_by` NULL until mark-purchased (`api_server.py:4128` → `"operator"`).
- **F5 — `reject_run` takes no caller** (`api_server.py:3092`) and `reject_group` ignores its caller (`:4930`); rejections carry only body-typed names.
- **F6 — Inbound intake webhooks are unauthenticated** (`api_server.py:6001,6102,6165`: flag gate only). Anyone who can reach the API can create a run for tenant `bayfoods` by posting to `intake+bayfoods@…`. Out of scope for arc 6 (inbound-channel auth), but it is a company-isolation-adjacent hole for the record.
- **F7 — Buyer magic-link mail will be blocked by send governance** unless buyer domains are allowlisted (fail-closed allowlist `utils/send_governance.py:343-353`). Company bootstrap (T6) should allowlist the company's domain(s) or auth mail needs a governance class exemption — the latter would touch shared governance code.
- **F8 — Site ids are not linked to companies/facilities.** Ship-to `site_id` (`lamirada`, `rancho`, `frontend/src/lib/proc-config.ts:37-66`) has no relation to `fac-*` facilities, so B37/B38 cannot be company-scoped without a site→company binding. Proposed: scope `site_shipto` rows by `company_id` (new nullable column) under the flag.
- **F9 — `approval_history.approver_id` is `String(36)`** (`persistence.py:139`) — member UUIDs fit; Cognito `sub`s also fit.
- **F10 — `/api/dev/reseed-handoffs` is unauthenticated and deletes runs across tenants** (`api_server.py:3610-3640`); `/api/debug/llm` spends a model call and echoes the key prefix (`:3581-3607`). → GATE STOP Q3.
- **F11 — Frontend `request()` header-spread quirk** (`frontend/src/lib/api.ts:96-98`): any call that passes `headers` drops the defaults. Harmless for a cookie session (cookies aren't headers set in JS), but `credentials: "include"` must be set in the `request()` defaults, not per call.

---

GATE STOP (RESOLVED 26 Sep - rulings in brief): The brief says the buyer side has no identity, but a ported Cognito buyer identity (`utils/auth`, `get_caller`) already sets `runs.company_id` and `approver_id` on six buyer routes. Please rule on three points. (Q1) Under `BUYER_ACCOUNTS_V1`, is the new magic-link `gofer_buyer_session` the ONLY accepted buyer identity, so the Cognito bearer path is ignored or refused? Or must a valid Cognito caller also be accepted, mapped to a buyer member by `company_id` PIN plus email? And is the buyer company key the existing company PIN string (e.g. `company-bayfoods`)? (Q2) `POST /api/runs/from-maintenance` is a service-to-service ingress from core (Cognito JWT plus `X-Arkim-Service-Signature`), not a browser call. Under the flag, should it be exempt from the buyer-session requirement and keep its service authentication (added to the T4 allowlist with that reason)? Or should it require a buyer session like the other buyer routes? (Q3) Under the flag, should `/api/debug/llm` and `/api/dev/reseed-handoffs` be moved behind `require_admin`, disabled (404), or exempted as named dev routes? Each fails the brief's buyer-facing rule, but neither is a buyer surface.



---

# BUILD — Arc 6 T1–T8 (builder report, appended after the gate)

**Branch:** `arc6/buyer-identity`. No task had been committed when this session started; T1–T8 were built in order, one commit each. Not pushed.

| Task | Commit |
|---|---|
| T1 Models and flag | `b5f2a9e` |
| T2 Permission matrix | `3ad136b` |
| T3 Buyer login and sessions | `2d320ef` |
| T4 Company isolation | `2712480` |
| T5 Attribution | `278aebd` |
| T6 Company bootstrap and member management | `5b09372` |
| T7 Approval policy settings | `5244743` |
| T8 Frontend | `499dcc2` |
| (fix-up) LF line endings restored in `api_server.py` | `f5cbee9` |
| (docs) CLAUDE.md notes, CLEANUP 8.7–8.8 | `4e09c61` |

## Final test counts (as observed, flags off, HEAD `4e09c61`)

- **Backend `uv run pytest -q`: 3584 passed, 73 skipped, 1 warning** (335 s). The previous full run at the same HEAD gave **1 failed, 3583 passed, 73 skipped**. The failure was `test_notifications_admin_alerts.py::test_the_list_is_newest_first`, which passed 5/5 when run alone and passed in the rerun. It is a pre-existing timing flake (FINDINGS F-H), not caused by this arc.
- **Frontend `npm test`: 34 files, 259 tests passed.**
- Gate baseline for comparison: 3322 passed / 73 skipped; 33 files / 246 tests.

## Pre-existing test files modified

- `utils/procurement_agent/tests/conftest.py`: the one approved edit (K7, listed in `loop/AUTHORISED_TEST_EDITS.txt`). It appends `"BUYER_ACCOUNTS_V1"` to `_FEATURE_FLAG_ENVS` and changes no existing entry. The flag is read live, so it has no module-attribute binding.
- No other pre-existing test file was modified. Every arc 2/3 supplier test passes unmodified.
- `utils/supplier_accounts.py`, `utils/supplier_accounts_rbac.py`, `utils/send_governance.py`, `utils/auth/` and `frontend/src/app/supplier/**` are unchanged since `7916215`. No removed line in the `api_server.py` diff mentions a supplier.

## How isolation is enforced (T4)

Every buyer-facing route declares `dependencies=[Depends(_DOOR_<CAP>)]`. Each door runs `_buyer_session_in_force`, which does three things before the handler runs:

1. Authenticates the `gofer_buyer_session` cookie, which is the only credential. It also applies the arc 3 origin check.
2. Scopes every path resource (`run_id`, `group_id`, `draft_id`, `item_id`, `facility_id`) to the session company. A resource that belongs to another company gets the same 404 as a missing one.
3. Checks the D2 matrix.

`site_id` is scoped by construction: rows are keyed by company. With the flag off, the door returns `None` and every route behaves as before. Lists, creation, events, reorder, impact and ship-to also filter or stamp by the session company inside the handler.

**Q2 (from-maintenance): service auth is NOT verified in code.** The route `api_server.py:2468-2469` depends on the optional `get_caller`. That function returns `None` when no bearer is present (`utils/auth/dependencies.py:111-112`). It computes `service_authenticated` (`:122`), but nothing requires it. So under the flag the route returns 404. It is allowlisted with the reason "disabled under BUYER_ACCOUNTS_V1 until service auth is enforced", the 404 is pinned by a test, and CLEANUP 8.2 records it. **Q3:** `/api/debug/llm` and `/api/dev/reseed-handoffs` return 404 under the flag (allowlisted and pinned), and CLEANUP 8.3 records the key-prefix echo.

**Structural guard** (`test_buyer_route_guard.py`):
- It enumerates `api_server.app.routes` at runtime and classifies each route with the approved rule (`_buyer_fixtures.NON_BUYER_PREFIXES`).
- It fails if any buyer-facing route lacks the session door plus a capability dependency.
- The `EXEMPT` allowlist has five named entries, each with a reason:
  - request-link and verify: public by design.
  - from-maintenance, debug/llm and dev/reseed: disabled under the flag.
- Every path parameter must be one the door knows how to scope.
- A runtime sweep calls every buyer route with the flag on, no session and an admin bearer. Each must return 401, or 404 for the disabled routes.

## Endpoint isolation table (every K2 endpoint → the test that proves it is scoped)

Abbreviations: `ISO` = `utils/procurement_agent/tests/test_buyer_isolation.py`; `FvM[Bn]` = `ISO::TestForeignIsIndistinguishableFromMissing::test_member_of_a_gets_the_missing_response_for_b[Bn]` (a member of A gets byte-identical status and body for B's id and for a missing id, and B's data is unchanged); `GUARD` = `test_buyer_route_guard.py`. Every row is also covered by `GUARD::TestStructuralGuard::test_every_buyer_facing_route_has_the_door` and `GUARD::TestRuntimeNoSessionNoEntry::test_no_buyer_route_is_reachable_without_a_session`.

| # | Endpoint | Scoping proof |
|---|---|---|
| B1 | POST /api/runs | `ISO::TestCreationIsScoped::test_b1_create_run_belongs_to_the_session_company`, `::test_b1_foreign_facility_equals_nonexistent_facility`, `::test_b1_cannot_join_another_companys_basket` |
| B2 | PUT /api/runs/{run_id}/asset-specs | `FvM[B2]` |
| B3 | POST /api/requests | `ISO::TestCreationIsScoped::test_b3_single_and_fan_out_requests_belong_to_the_session_company`, `::test_b3_foreign_facility_equals_nonexistent_facility` |
| B4 | GET /api/runs | `ISO::TestListsAreScoped::test_b4_list_runs`; `ISO::…::test_legacy_null_company_runs_are_invisible_under_the_flag` |
| B5 | POST /api/runs/from-maintenance | disabled: `GUARD::TestRuntimeNoSessionNoEntry::test_disabled_routes_are_the_plain_404_under_the_flag`, `::test_from_maintenance_unsigned_call_is_refused_under_the_flag` |
| B6 | GET /api/runs/{run_id} | `FvM[B6]` (+ owner positive control `ISO::…::test_the_owner_still_reaches_it[B6]`) |
| B7 | POST …/open-from-pending | `FvM[B7]` |
| B8 | POST …/reject-submission | `FvM[B8]` |
| B9 | POST …/messages | `FvM[B9]` |
| B10 | POST …/upload | `FvM[B10]` |
| B11 | POST …/request-confirmation | `FvM[B11]` |
| B12 | POST …/select-candidate | `FvM[B12]` |
| B13 | POST …/order-now | `FvM[B13]` |
| B14 | POST …/approve | `FvM[B14]`; `ISO::…::test_a_requester_gets_404_not_403_on_a_foreign_run` |
| B15 | POST …/reject | `FvM[B15]` |
| B16 | POST …/confirm-intake | `FvM[B16]` |
| B17 | POST …/outreach | `FvM[B17]` |
| B18 | POST …/save-outreach | `FvM[B18]` |
| B19 | GET /api/facilities | `ISO::TestListsAreScoped::test_b19_facilities` |
| B20 | GET /api/approval-rules/{facility_id} | `FvM[B20]` (+ `test_the_owner_still_reaches_it[B20]`) |
| B21 | POST /api/approval-rules | `ISO::TestListsAreScoped::test_b21_approval_rule_on_a_foreign_facility_equals_a_nonexistent_one` |
| B22 | POST …/execute | `FvM[B22]` |
| B23 | POST …/mark-delivered | `FvM[B23]` |
| B24 | GET …/orders | `FvM[B24]` |
| B25 | GET /api/orders | `ISO::TestListsAreScoped::test_b25_orders` |
| B26 | GET /api/reorder | `ISO::TestListsAreScoped::test_b26_reorder` |
| B27 | GET /api/events | `ISO::TestListsAreScoped::test_b27_events` |
| B28 | GET /api/groups/{group_id} | `FvM[B28]`; `ISO::TestCreationIsScoped::test_b1_cannot_join_another_companys_basket` |
| B29 | POST /api/groups/{group_id}/approve | `FvM[B29]` |
| B30 | POST /api/groups/{group_id}/reject | `FvM[B30]` |
| B31 | POST …/rfq-draft | `FvM[B31]` |
| B32 | GET /api/rfq-drafts/{draft_id} | `FvM[B32]` |
| B33 | GET …/rfq-drafts | `FvM[B33]` |
| B34 | POST /api/rfq-drafts/{draft_id}/approve | `FvM[B34]` |
| B35 | POST /api/rfq-drafts/{draft_id}/reject | `FvM[B35]` |
| B36 | POST /api/rfq-drafts/{draft_id}/send | `FvM[B36]` |
| B37 | GET /api/sites/{site_id}/ship-to | `ISO::TestListsAreScoped::test_b37_b38_ship_to` |
| B38 | PUT /api/sites/{site_id}/ship-to | `ISO::TestListsAreScoped::test_b37_b38_ship_to` |
| B39 | GET …/review-items | `FvM[B39]` |
| B40 | POST …/process-replies | `FvM[B40]` |
| B41 | POST /api/review-items/{item_id}/confirm | `FvM[B41]` |
| B42 | POST /api/review-items/{item_id}/reject | `FvM[B42]` |
| B43 | POST /api/review-items/{item_id}/place-order | `FvM[B43]` |
| B44 | GET …/impact | `FvM[B44]`; `ISO::TestListsAreScoped::test_b44_saving_is_never_measured_against_another_companys_price` |
| B45 | GET /api/impact | `ISO::TestListsAreScoped::test_b45_impact` |
| B46 | GET /api/buyer/me (new) | company taken from the session only: `test_buyer_auth.py::TestVerifyAndCookie::test_me_returns_company_member_and_permissions` |
| B47 | /api/buyer/members* (new) | `test_buyer_members.py::TestOnlyAdminManagesMembers::test_admin_lists_only_their_company`, `TestInviteChangeRevoke::test_cross_company_member_is_indistinguishable_from_missing` |
| B48 | /api/buyer/settings* (new) | `test_buyer_settings.py::TestChanges::test_changes_are_company_scoped` |
| B49 | GET /api/debug/llm | disabled: `GUARD::TestRuntimeNoSessionNoEntry::test_disabled_routes_are_the_plain_404_under_the_flag` |
| B50 | POST /api/dev/reseed-handoffs | disabled: same test |

Other invariants and the tests that hold them:
- **D7 attribution:** `test_buyer_attribution.py`. Each action records the session member and ignores a typed "Dana Plant-Manager". M1 compares member ids. `acknowledged_by` is recorded for both overrides. `placed_by` holds a member id and orders carry `company_id`. Channel runs record `acting_member: null` plus the channel.
- **D2 matrix:** `test_buyer_accounts_rbac.py` has the role × capability table and a source scan over every buyer handler taken from `app.routes`.
- **Hidden controls return 403 when called directly:** `test_buyer_hidden_controls.py`.
- **D5 policy:** `test_buyer_settings.py`. The policy is stored and audited, and `TestNotEnforcedYet` shows order routing is unchanged.
- **Flag off:**
  - `test_buyer_auth.py::TestFlagOff` checks that every new route returns 404 regardless of body.
  - `GUARD::TestFlagOffUnchanged` checks that pre-existing routes behave as before.
  - `test_buyer_attribution.py::TestFlagOffAttributionUnchanged` checks that typed approver names are still recorded.
  - The frontend flag-off cases in `buyer-session.test.tsx` check that today's UI renders.

## FINDINGS

- **F-A — Two settings routes use the Admin capability `set_approval_limit`.** D2 names no "company settings" capability. Following the gate's K2 proposal, `PUT /api/sites/{site_id}/ship-to` (B38) and `POST /api/approval-rules` (B21) require `set_approval_limit`, so only Admins can use them (F3 ruling: B21 gets nothing beyond D2's Admin capability). The frontend shows "Delivery settings" only to Admins. If a separate capability is wanted, it is one new row in `utils/buyer_accounts_rbac.py`.
- **F-B — Joining another company's basket is refused with a 422 that a brand-new id does not get.** Basket ids are minted by the client (`frontend/src/components/proc/request-screen.tsx:200,231,268`). With the flag on, `POST /api/runs` with another company's `group_id` returns 422 "group_id is not available" (`api_server.py` `_buyer_check_group_join`). A new id is accepted. So the endpoint technically reveals that a random UUID is in use. The alternative, accepting the join, would put a foreign run under the victim's basket approval record. The ids are unguessable v4 UUIDs and leak only through their owner. Minting basket ids on the server would remove the difference.
- **F-C — Buyer sign-in mail skips two parts of the supplier mail path, and the SES boot guard ignores the buyer flag.** Buyer mail goes through the same governed `GmailSender().send` seam, with the same `auth_mail` / `message_class="auth"` markers. But it writes no `sent_messages` ledger row and no tracked Notification. The supplier helpers for those are keyed on `supplier_domain`, and supplier code was not to be changed. As a result, buyer sends do not count toward the auth daily cap, and a bounce raises nothing. Separately, the SES boot guard (`api_server.py` `_assert_auth_mail_configured`) checks only `SUPPLIER_ACCOUNTS_V1`. Recorded as CLEANUP 8.7.
- **F-D — "Mark purchased" overwrites the buyer's `placed_by`.** The admin action calls `place_order(placed_by="operator")`, which replaces the buyer member id captured at order-now or manual execute. This is pre-existing admin behaviour. Recorded as CLEANUP 8.8 for arc 7.
- **F-E — Any approver can give the second approval.** D2 reserves the second approval above the limit for Approver and Admin. Nothing enforces that yet (arc 7), so the server accepts a second approval from any `approve_within_limit` holder, and the UI shows the "Approve (2nd)" button to Buyers as well. M1 now makes sure the two approvals come from distinct members.
- **F-F — `GET /api/buyer/settings` is Admin-only**, following the brief ("Admin-only settings screen and API"). Arc 7 approvers will probably need to read the limit. Opening the read to `view_company` would be a one-line change.
- **F-G — A malformed Cognito bearer can still produce a 401 under the flag.** `get_caller` still runs on B1, B3, B14, B29 and B30. The door runs first, so a request without a valid cookie gets the door's 401. A request with a valid cookie and a malformed Cognito bearer gets `get_caller`'s own 401 "Invalid or expired token". The Cognito identity is never used under the flag (Q1). This was left in place because changing `get_caller` wiring would change the flag-off Cognito path, which Q1 says must stay as it is.
- **F-H — Pre-existing flaky test, not edited (not in the approval list).** `utils/procurement_agent/tests/test_notifications_admin_alerts.py:169-175` raises three alerts back-to-back and expects newest-first order. The alert list sorts by `created_at` alone, with no tiebreaker (`utils/notifications_store.py:1221`), so equal timestamps come back in insertion order. It failed once in a full run at HEAD, then passed alone 5/5 and in a full rerun. Nothing in this arc touches it.
- **F-I — Pre-existing frontend type errors in test files.** `npx tsc --noEmit` reports errors only in test files this arc did not touch:
  - `src/app/portal/[token]/__tests__/portal-route.test.ts:17`
  - `src/app/portal/[token]/__tests__/security.test.tsx:45,51,54`
  - `src/app/quote/[token]/__tests__/quote-route.test.ts:15`
  - `src/components/gofer/__tests__/asset-panel-hygienic.test.tsx:45`

  No non-test source has type errors.
- **F-J — Line-ending churn in the T3 to T8 commits.** The T3 patch script wrote `api_server.py` in Windows text mode. That committed the whole file as CRLF in `2d320ef` and it stayed that way through T8. `f5cbee9` restores LF with no content change. **Review against `7916215..HEAD` or `--ignore-cr-at-eol`.** Per-commit diffs from T3 to T8 show whole-file churn in `api_server.py`.
- **F-K — The legacy `/runs` frame is guarded but shows no company header.** It sits behind the session guard (`frontend/src/app/runs/layout.tsx`), but the company header lives only in `ProcShell`. The `SidebarNav` frame shows no company.
- **F-L — `/settings` (Delivery settings) is still reachable by URL for non-Admins.** The nav hides it, reads still work, and saving returns 403, which the page reports as its own generic error.

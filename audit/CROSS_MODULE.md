# audit/CROSS_MODULE.md

**Cross-module / end-to-end-flow audit — spec + seam map for the v1.2 pass.**

This is the seam-level companion to the per-module files: it traces data and control
*across* module boundaries, where a per-module read can't see the bug. Authored from the
Cowork session; **intended to be executed by the Claude Code session** that can certify the
canonical suite (this sandbox cannot — see "Suite certification" below).

## Constraints (inherit from the v1.2 brief)
- **PROPOSE, DO NOT APPLY.** Findings + proposed diffs only. No source edits, no commits, no push.
- **No live/paid calls** (Apollo/Gmail/Anthropic/Tavily). Tracing reads code; it does not execute paid endpoints. Running the mocked `pytest` is fine.
- **Writes:** `audit/` only. Do not modify/delete `PHASE_R_AUDIT_REPORT.md` / `PHASE_R_AUDIT_ADDENDUM.md`.
- **Severity:** `H` / `M` / `L` for security/correctness, `B` for bloat. Each finding: `seam` · what · why it matters · severity · proposed diff · behaviour-preserving vs behaviour-change.
- File per-module findings into the relevant `audit/<module>.md`; keep the **flow-level** verdict here.

## The recurring bug class to hunt at every seam
1. An error caught on one side of a seam and surfaced to the next as **success / empty** (swallowed-failure-as-no-result).
2. A **consequential write reachable without its gate** (price write, order placement, email send) when a downstream caller skips the upstream check.
3. A **return-shape mismatch** across the `utils/` ↔ front-end seam (Streamlit imports `utils/` in-proc; FastAPI wraps the same `utils/` — a changed shape hits one path silently).
4. Data crossing a **run / tenant boundary** unintentionally (the customer endpoints are currently global/ungated — D2).

## Flows to trace end-to-end (each: list the call chain, the gate(s), and the assertion)

**F1 — RFQ outbound (send path + double gate).**
`outreach._make_draft` → `rfq_send.send_rfq` (HITL `Approval` gate) → `email_sender.GmailSender.send` (`EMAIL_SEND_ENABLED` gate) → `supplier_registry.record_sent_message`.
Assert: no send without an `Approval` AND the flag; the `metadata` keys (`run_id`, `supplier_domain`, `rfq_id`) set here are exactly the keys inbound matching (F2/F7) reads back; `Message-ID` is deterministic from `rfq_id` so a bounce DSN matches.

**F2 — Inbound reply → review queue (read path, must never send).**
`inbox_reader.GmailInboxReader.fetch_replies` → `reply_processor.process_replies` → `reply_matcher` (match to a sent message) → `quote_extractor` / `contact_extractor` → `supplier_registry.record_review_item` (status `pending`/`needs_human_review`).
Assert: fail-soft + `available:False` with **no** call when Gmail unconfigured; `gmail.readonly` only; an unmatched reply is queued for human review, never auto-applied; extraction abstains (`None`/"verification_required") rather than fabricating.

**F3 — Confirm → price write (the only customer-path `price_db` write).**
`POST /api/review-items/{id}/confirm` → `reply_processor.confirm_quote` → `price_db.save_price(source="rfq")`.
Assert: **no auto-confirm anywhere** (human action only); the `(manufacturer and part_number)` guard holds before the composite-key write; price never written on the reject path.

**F4 — Order placement (RFQ path + buy path).**
`POST /api/review-items/{id}/place-order` (requires quote `status=="confirmed"`) **or** `POST /api/runs/{id}/execute` → `ProcurementAgent._execute` → `orders.create_order` (draft) → `orders.place_order` (draft→placed).
Assert: H1 phase guard (`_execute` places only from `approved`/`executing`); `place_order` refuses without a resolvable price; the state machine rejects skip/again/backward; no path reaches `place_order` bypassing both the confirmed-quote gate and the approved-phase gate.

**F5 — Approval path across BOTH front ends (the divergence to nail).**
FastAPI: `select-candidate` (stores thin `{candidate_id,tier}`, **no `_approval_path`**) → `approve` (**always → APPROVED on one approval**, dual-approver deferred) → `execute`.
Orchestrator/Streamlit: `select_candidate` (computes `_approval_path` via `determine_approval_path`) → `submit_approval` (count-based; **no distinct-approver check**, M1).
Assert: document the two surfaces side by side; confirm dual-approver/distinctness are *honestly absent* (deferred-pending-auth), not a fake gate trusting the request body; flag that `_execute`'s phase guard (H1, landed) is the one piece that didn't need auth.

**F6 — Impact (measured vs estimated, no recompute downstream).**
`impact.gather_run_decision` / `gather_cumulative` ← reads `orders` + `price_db`; `last_paid` is date-aware (`before=created_at`, D1) → `GET /api/impact` → frontend renders verbatim.
Assert: savings are measured from the customer's own transactions (no external baseline); time-saved is labelled an estimate with its model version; the frontend does no savings math (presentational only); `last_paid` excludes the order's own/later timestamps.

**F7 — Bounce handling.**
`inbox_reader` → `bounce_parser.parse_bounce` (note the lazy import that breaks the `inbox_reader`↔`bounce_parser` cycle) → `bounce_processor` → `supplier_registry.mark_contact_bounced`.
Assert: only the matched contact is nulled; a bounced recipient is excluded from the next `recipient_set`; no unrelated contact is mutated.

**F8 — Sourcing-failure propagation (the §4.5 divergence).**
`SourcingAgent.run` exception → api `_run_sourcing_background` advances to `Phase.ERROR` (honest) **vs** `core._stub_sourcing` advances to `COMPARISON` with an error result (masks failure as "no candidates").
Assert: confirm still divergent; per-tier `_collect` `status:"error:…"` is captured but not surfaced (N3); propose surfacing tier status without changing pipeline behaviour.

## Cross-cutting checks (not a single flow)
- **Return-shape contracts** between `utils/` and the two front ends — any `utils/` return whose shape the Streamlit page or a FastAPI response model reads positionally/by-key; a recent shape change that only one path matched.
- **Line-ending / encoding hygiene** — the working tree shows whole-file CRLF churn (no `core.autocrlf` set); flag as a repo-hygiene item (it makes real diffs unreadable and can mask review), propose a `.gitattributes` normalization as a **behaviour-preserving** housekeeping diff (not an audit blocker).
- **Tenant/run scoping (D2)** — every customer endpoint (`/api/orders`, `/api/impact`, `/api/reorder`, `/api/sites/*`, `/api/review-items/*`, run endpoints) is currently global; confirm none leak across run/site and that the deferral is honest (no partial filter creating a false sense of isolation).

## Output of this pass
- Seam-level findings filed into the relevant `audit/<module>.md` per the v1.2 protocol; the flow-level pass/fail for F1–F8 summarized here with severities.
- Anything that is a true cross-module bug (no single owning module) stays here with a proposed diff and the owning modules cross-referenced.

## Suite certification (read this before writing PRODUCTION_READINESS)
The Cowork sandbox **cannot** certify the suite: the Linux mount serves a **truncated** `utils/email_sender.py` (227 lines, missing the `except` block) so sandbox `pytest` fails on a phantom `SyntaxError`, and the canonical `.venv` is Windows-only and unreachable from the sandbox shell. The host file is intact (verified via host read), and the Claude Code session reported `691 passed` on the canonical `.venv` (Python 3.11). **`PRODUCTION_READINESS.md` test-suite-state must state the certifying environment explicitly** (canonical `.venv` / 3.11), and if a run is done from the sandbox it must record "not certified from this sandbox — certify in attended env," never a guess. Red suite → verdict `blocked`.

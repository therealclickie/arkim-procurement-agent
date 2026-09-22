# NIGHT 11 KICKOFF — Supplier Quote Submission (QUOTE_SUBMIT_V1)

Overnight unmanned build. Foreground-only. NO PUSH. The authoritative design is
**./QUOTE_SUBMISSION_SPEC.md** — read it in full first; this brief is the operational wrapper.
If the spec file is missing, STOP.

---

## PRE-FLIGHT

| Variable | Value |
|---|---|
| REPO | `C:\dev\_Arkim\Arkim Procurement Agent Prototype` — STOP if under Downloads/OneDrive |
| BASE_BRANCH | `test/flag-on-integration` — record BASE_HEAD |
| WORK_BRANCH | `feature/quote-submission-overnight` |
| FLAG | `QUOTE_SUBMIT_V1` — new. Flag OFF ⇒ endpoints absent (byte-identical 404), zero behavior change anywhere. Parity-tested. |
| SUITE BASELINE | ~2122 passed / 73 skipped — confirm; STOP if different |
| ITERATION CAP | 5 per failing task |
| FINAL ACT | `MORNING_REPORT_NIGHT11.md` at repo root. NO PUSH. |

Standing guardrails: mocks only, NO live network, NO live sends (every ack/notification under
the existing EMAIL_SEND_ENABLED + governance stack — quote features must NOT add any new path
to GmailSender.send that bypasses governance), do-not-touch paths, one commit per task, suite
green every commit, test rows is_test=1. Feature flags in tests: opt in via monkeypatch (the
conftest autouse fixture pins all flags off — QUOTE_SUBMIT_V1 must be ADDED to that fixture's
pin list as part of T1).

## INVESTIGATION GATE (read-only; file:line; self-gate)

- **I1. The T4 promotion seam.** Exactly what shape of "confirmation record" the ranking-bands
  promotion consumes today (the quote index the Night-9 T4 tests use), so real quotes produce
  records the EXISTING promotion path reads — extend the promotion reader only if the current
  shape can't carry the spec's fields; never fork a second promotion mechanism.
- **I2. Claim-token machinery** (utils/claim_tokens.py): the mint/hash/expiry/uniform-404
  pattern to mirror for quote tokens. Reuse the idioms; separate store/namespace (a claim
  token must never open a quote form and vice versa — cross-token tests required).
- **I3. RFQ draft/send seam:** where a quote link would be injected into the RFQ template at
  send time (rfq_send / the template), and where the rfq/run identity lives so a token scopes
  to exactly one request. NOTE: template edits are fine but template COPY is founder-owned —
  add the {quote_link} placeholder mechanically; do not rewrite the letter.
- **I4. Portal surface:** how the claimed-supplier portal page is composed (portal/[token])
  and where an "open requests → quote" section slots in; the propose→approve write model does
  NOT apply to quotes (quotes are their own store, not registry revisions) — confirm no
  accidental coupling.
- **I5. price_db band lookup** for the sanity check (§6): what exists for median/band-by-part
  and its coverage; where a band is absent the sanity check SKIPS (absence of data must not
  flag every quote).

## BUILD TASKS (one commit each; all behind QUOTE_SUBMIT_V1)

- **T1. Quote store + lifecycle.** The `quotes` table per spec §5 (placement per I1 findings),
  status lifecycle active/superseded/expired/withdrawn, supersede-on-resubmit, expiry
  evaluated at read time (no cron), is_test provenance. Add QUOTE_SUBMIT_V1 to the conftest
  flag-pin list.
- **T2. Quote tokens.** Per-RFQ mint (admin-gated), hashed at rest, single-RFQ scope, RFQ-window
  expiry, revisable (not single-use), uniform 404, closed-state for dead RFQs. Cross-token
  isolation tests vs claim tokens.
- **T3. Submission API + promotion wiring.** POST submit (token-auth path A; portal-identity
  path B; admin path C with submitted_via provenance) → validation → sanity checks (§6:
  pn_differs / price-band / qty — flag to review, not block; band-absent skips) → active quote
  → the I1 promotion seam consumes it on the live run. Review-queue endpoints (list/approve/
  reject) admin-gated. Property test: NO path from any quote endpoint to order placement.
- **T4. The quote form (frontend).** /quote/{token}: the five required + two optional fields
  per spec §4, PN-confirmation prefill, mobile-friendly, portal-surface palette, ≥44px,
  no-account. Post-submit confirmation + claim pitch (path A, unclaimed only). Closed/expired
  states honest.
- **T5. Portal open-requests section** (path B): claimed supplier sees their open RFQs and
  quotes via the same form component; their quote history (their own only). No cross-supplier
  visibility.
- **T6. Buyer-side card + template placeholder.** The promoted card populates from the real
  quote (supplier, price, lead, "confirmed {date}"; pn_differs-approved quotes labelled as the
  QUOTED PN with equivalent framing). Inject {quote_link} into the RFQ template mechanically
  (I3). Quote-received ack email exists but is stubbed under the full send stack.
- **T7. Acceptance suite.** Spec §10 criteria 1-10 as tests, on a live-faithful path (TestClient
  through the real API, the Gusher fixture for promotion visibility). Flag-off parity. Suite
  ≥ baseline + new, green.

## OUT OF SCOPE (spec §9)
Payments, PO generation, auto-order (property-tested absent), dashboard, negotiation,
multi-currency, reply-parser changes beyond its confirm action writing a path-C record,
template COPY rewrites, any live send.

## MORNING REPORT
MORNING_REPORT_NIGHT11.md: I-gate findings (esp. I1 — the promotion-record shape decision),
per-task commits, acceptance table (spec §10, each criterion pass/fail), flag-off parity proof,
unspecified decisions enumerated, verification commands. NO PUSH.

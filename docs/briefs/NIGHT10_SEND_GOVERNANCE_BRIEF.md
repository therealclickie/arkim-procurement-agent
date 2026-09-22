# NIGHT 10 KICKOFF — RFQ Send-Governance Layer (SEND_GOVERNANCE_V1)

Overnight unmanned build. Foreground-only. NO PUSH. **ZERO LIVE SENDS — the EMAIL_SEND_ENABLED
gate stays OFF and untouched all night; this build constructs the machinery that makes turning
it on safe.** The authoritative context is ./RFQ_LIVE_WIRING_SPEC.md (§1 pre-flight audit,
§3 Phase 1 controls, §8 open items) — read it first; this brief operationalizes its code-side
items. If the spec file is missing, STOP.

---

## PRE-FLIGHT

| Variable | Value |
|---|---|
| REPO | `C:\dev\_Arkim\Arkim Procurement Agent Prototype` — STOP if under Downloads/OneDrive |
| BASE_BRANCH | `test/flag-on-integration` — record BASE_HEAD |
| WORK_BRANCH | `feature/send-governance-overnight` |
| FLAG | `SEND_GOVERNANCE_V1` — new. Flag OFF ⇒ byte-identical behavior, parity-tested. NOTE: this flag gates the GOVERNANCE features; it does not and must not enable delivery. Delivery remains gated by EMAIL_SEND_ENABLED + credentials, which this build never touches. |
| SUITE BASELINE | ~2024 passed / 73 skipped — confirm; STOP if different |
| ENV | `RUN_CAPTURE=1; INTAKE_TYPE_AWARE=1; SCORING_V2=1` |
| ITERATION CAP | 5 |
| FINAL ACT | `MORNING_REPORT_NIGHT10.md` at repo root. NO PUSH. |

Standing guardrails: mocks only, NO live network, NO live sends (double-gate + conftest net
stays), do-not-touch paths, one commit per task, suite green every commit, test rows is_test=1.
**Google Workspace mailbox/outbox integrations already exist — REUSE them; do not build a new
mail client. Any credential handling is OUT of scope (Sergei/deploy-side).**

---

## INVESTIGATION GATE (read-only; file:line; self-gate)

- **I1. Send-path audit (spec §1).** Trace every path that could reach delivery. Confirm:
  exactly one gate (EMAIL_SEND_ENABLED + credentials); every send recorded to sent_messages
  BEFORE delivery attempt; no ungated path exists (grep-level: every caller of the send layer).
  Report the full call graph. If an ungated path exists, fixing it becomes T0 (highest priority).
- **I2. Inbox scoping (spec §8).** Confirm the Night-8 finding: the RFQ reply reader queries
  in:inbox with no recipient filter, while intake mail is addressed intake+<tenant>@. Identify
  the cleanest scoping mechanism (recipient/To filter on the RFQ reader vs label-based routing)
  so intake mail and RFQ replies can NEVER cross when both are live on one mailbox.
- **I3. Current send-layer surface.** Where drafts are released today (rfq_send / outreach),
  what sent_messages records, what statuses exist, where bounce/reply processing attaches.
- **I4. Allowlist/cap/suppression prior art.** Confirm none exists in the send path (believed
  missing). Find the right seam: enforcement must live IN the send layer (the last function
  before delivery), not in callers.

## BUILD TASKS (one commit each; all governance behind SEND_GOVERNANCE_V1)

- **T0 (only if I1 finds one).** Close any ungated send path. This outranks everything.
- **T1. Allowlist enforcement.** A supplier-domain allowlist store (admin-managed: add/remove/
  list via flag-gated admin endpoints, audit-logged who/when). The send layer REFUSES delivery
  to any domain not allowlisted — enforced at the last seam before delivery, fail-CLOSED
  (empty/missing allowlist ⇒ nothing sends). Suppression beats allowlist (see T3).
- **T2. Caps.** Per-day global send cap and per-supplier-per-part open-RFQ cap (limits
  env-configurable, defaults 10/day and 1 open per part per supplier). Exceeding ⇒ blocked +
  logged + surfaced (a "cap_blocked" status, not a silent drop). Caps enforced in the send
  layer, counted from sent_messages (source of truth), UTC-day boundary.
- **T3. Suppression list.** A domain suppression store ("supplier asked to stop"): checked in
  the send path BEFORE allowlist; suppressed ⇒ blocked + logged, permanently until removed by
  admin (audit-logged). Simple admin add/remove/list endpoints, flag-gated.
- **T4. Release queue (concierge send approval).** Phase-1 model from spec §3: an RFQ draft is
  never delivered directly — it enters a release queue; a flag-gated admin endpoint lists
  pending drafts (full rendered content) and releases or rejects each (releaser identity +
  timestamp recorded on the sent_messages row). Release ⇒ the send layer runs its full gate
  stack (suppression → allowlist → caps → EMAIL_SEND_ENABLED). With the delivery gate off
  (tonight and in tests), a released send records status "stubbed" exactly as today — proving
  the whole governance stack end-to-end without delivering anything. Batch release (Phase 2)
  = same endpoint, list of ids.
- **T5. Inbox scoping fix (per I2).** Implement the separation: the RFQ reply reader only
  consumes RFQ-stream mail; the intake adapter only intake-addressed mail. Cross-stream tests:
  an intake-addressed message NEVER appears to the RFQ reader and vice versa (mocked mailbox
  fixtures — the Gmail client mocking patterns exist from Night 8/RFQ work).
- **T6. Send ledger + daily digest.** Ensure sent_messages captures the Phase-1 audit needs:
  status transitions (queued/released/stubbed|delivered/bounced/replied/cap_blocked/suppressed/
  not_allowlisted), releaser, template version. A flag-gated read endpoint returning the
  daily-ritual digest (spec §4): counts by status, bounces, replies, anything blocked. No UI —
  endpoint only (the ritual can start as curl/CLI).
- **T7. Governance acceptance suite.** Property tests: NO path from draft to delivery that
  bypasses suppression→allowlist→caps→release→gate (instrument the delivery function; drive
  every entry point). Fail-closed tests (empty allowlist, missing stores). Flag-off parity
  (byte-identical). The kill-switch semantics test: with everything released and allowlisted,
  EMAIL_SEND_ENABLED off ⇒ status "stubbed", never delivered.

## SUCCESS CRITERIA (falsifiable)

1. I1 call-graph published; zero ungated send paths (or T0 closed them, with a regression test).
2. Fail-closed proven: empty allowlist ⇒ nothing delivers, even released + gate-on (simulated
   gate via monkeypatch in tests only — the real env gate stays off).
3. Suppression beats allowlist beats caps beats release — precedence property-tested.
4. A released draft with delivery gate off records "stubbed" with releaser identity — the
   full Phase-1 flow exercised end-to-end, zero deliveries.
5. Cross-stream isolation: intake mail invisible to the RFQ reader and vice versa (tests).
6. Cap exceedance blocks with visible status; UTC-day rollover resets (tests).
7. Flag-off byte-identical; suite ≥ 2024/73 + new tests, green throughout.
8. NOTHING in the night's diff touches EMAIL_SEND_ENABLED handling, credentials, or .env.

## OUT OF SCOPE
Live sends; credentials; DNS/SPF/DKIM (Sergei); the gofer.ai migration; RFQ template copy
rewrite (flag for Tom's review — report the current template text in the morning report so he
can review tone + the claim-link line); marketing email of any kind; auto-ordering (unchanged).

## MORNING REPORT
MORNING_REPORT_NIGHT10.md: I1 call graph, per-task commits, precedence/property test evidence,
the current RFQ template text (for Tom's copy review), unspecified decisions, verification
commands. NO PUSH.

---

# PARALLEL TRACK — Sergei's AWS/infra checklist (daytime, not the agent's)

1. Deployed environment stood up (the ECS deploy) with the app running gate-OFF.
2. Dedicated sending mailbox on the live Google Workspace domain (rfq@ or procurement@arkim.ai).
3. SPF, DKIM, DMARC verified for the sending domain (test with a mail-tester before Phase 1).
4. OAuth/service credentials provisioned into the DEPLOYED environment's secret store only —
   never the repo, never local .env.
5. Kill-switch drill: with governance merged and gate ON in deploy (allowlist EMPTY — fail-closed
   protects), flip the gate off and confirm delivery stops. Then Phase 0 shadow per the spec.
6. Confirm the deployed inbox wiring matches the T5 scoping (intake stream vs RFQ stream).

**Sequence:** Night 10 builds governance (sends off) → morning review + merge → Sergei's items
land → kill-switch drill → Phase 0 shadow → Tom populates the allowlist (DXP + 3-5 real seal/pump
suppliers) → Phase 1 first concierge-released sends, human-watched. The first real send is a
DAYTIME event with Tom releasing it — by design, not limitation.

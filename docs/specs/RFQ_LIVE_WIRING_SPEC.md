# SPEC — RFQ Live-Wiring (Supervised Production Milestone)

**What this is:** turning the already-built, fully-stubbed RFQ loop into real emails to real
suppliers, safely. This is NOT an overnight agent build — it is a daytime, human-watched
production milestone run with Sergei (deploy/credentials) and Tom (concierge/first sends).
**Why now:** the entire supplier-side value proposition is gated behind it — the outreach block
says "we're asking DXP," the T4 promotion mechanic waits for real quotes, the demand teaser and
future dashboard need real events, and the onboarded-supplier benefit (first look → confirm →
win) is invisible until requests actually send.

---

## 1. What already exists (verify, don't rebuild)

Built and tested behind gates across prior nights:
- RFQ draft generation (per-candidate outreach drafts; rfq_drafts store)
- The send layer behind the `EMAIL_SEND_ENABLED` double-gate + conftest safety net (every test
  send "stubbed", recorded not delivered)
- gmail_client machinery: sending, inbox reading, MIME/attachments
- Reply processing: bounce detection, reply capture, quote extraction (propose-don't-invent —
  extracted quotes are PROPOSED for human confirmation, never auto-accepted)
- The quote index + T4 read-time promotion (a confirmed quote with a real price promotes an
  onboarded Band-C supplier to top of Band A) — live-path verified via
  TestBandedTier1ReDeriveOrdering
- Outreach-block UI (intent-only copy — currently honest BECAUSE nothing sends; this milestone
  is what keeps it honest)

**Pre-flight audit (Sergei + Tom, before anything goes live):** walk the send path end-to-end
in code and confirm: (a) exactly one gate controls delivery (`EMAIL_SEND_ENABLED` + credentials
present), (b) every send is recorded to sent_messages BEFORE delivery attempt with status
transitions, (c) there is no code path that sends outside the gate (grep-level audit), (d) the
reply reader cannot cross streams with intake mail (recipient scoping — the Night 8 I2 note:
the RFQ reader has no recipient filter; confirm the live inbox wiring separates intake+<tenant>@
traffic from RFQ replies BEFORE both are live on one mailbox).

## 2. Infrastructure prerequisites (Sergei-led)

- **Sending identity:** a dedicated mailbox on the live domain (procurement@arkim.ai or
  rfq@arkim.ai — arkim.ai is the live email identity; the gofer.ai migration is separately
  deferred and does NOT block this). Dedicated mailbox, not a personal one.
- **Deliverability basics:** SPF, DKIM, DMARC records verified for the sending domain BEFORE
  the first send (a flagged/spamfoldered first impression to a supplier is worse than none).
  Plain-text-leaning templates, real reply-to, no link-tracking in v1.
- **Credentials:** OAuth/service credentials provisioned to the deployed environment only —
  never in the repo, never in local dev .env. Local dev remains send-disabled forever.
- **Environment:** live sending runs ONLY in the deployed environment (the ECS stand-up Sergei
  is driving). The kill switch is the env flag: flipping `EMAIL_SEND_ENABLED` off (or revoking
  credentials) stops all delivery immediately — test this rollback BEFORE the first real send.
- **Compliance floor:** business RFQ email is legitimate B2B outreach; still include the
  sender's real business identity + address and honour any supplier's ask to stop (a simple
  suppression list keyed by domain — check it in the send path).

## 3. Rollout phases (each phase gates the next)

### Phase 0 — Shadow mode (deployed, gate OFF)
Run real sourcing in the deployed environment with sends still stubbed. Verify: drafts
generated correctly for real runs, sent_messages records created with "stubbed" status,
outreach block renders, nothing delivers. This is the deployed-environment dress rehearsal.
**Gate to Phase 1:** a week's (or a deliberate test batch's) worth of shadow RFQs manually
reviewed by Tom — content, addressing, tone, correctness. The drafts you'd have sent are the
drafts you WOULD send.

### Phase 1 — Concierge sends to an allowlist (the first real emails)
- **Supplier allowlist:** sends permitted ONLY to domains Tom has explicitly approved
  (start: DXP + 3-5 seal/pump specialists from the live Gusher run — suppliers where a real
  RFQ is genuinely useful). Allowlist enforced IN THE SEND PATH, not by convention.
- **Per-send human approval:** every RFQ is reviewed and released by Tom (concierge model).
  No automatic sending. The release action is logged (who, when, to whom).
- **Caps as a backstop:** hard per-day send cap (e.g. 10) and per-supplier cap (e.g. 1 open
  RFQ per part per supplier) in code, even though volume is tiny — caps are what turn a bug
  into a bounded incident.
- **Watch the first sends land:** confirm delivery (not spam-foldered) with at least one
  friendly recipient; verify the reply path by having a real reply flow: bounce test, plain
  reply, and a reply containing a quote.
- **The quote loop, live:** first real supplier reply → quote extracted → PROPOSED to Tom →
  Tom confirms → T4 promotion fires on the real result → the supplier visibly moves to the
  top of findings. This single end-to-end event is the milestone's heart.

### Phase 2 — Limited live (allowlist grows, approval loosens)
- Allowlist expands as suppliers prove real (delivered, not bounced, ideally responsive).
- Batch approval replaces per-send approval (Tom reviews a queue, releases in one action).
- Caps raised deliberately, never removed.
- The outreach-block copy may now say "we've asked" (past tense) — it's true.
**Explicitly NOT in scope at any phase:** fully unattended sending to arbitrary discovered
domains. Graduating beyond the allowlist model is a separate future decision with its own
spec (it requires the registry junk cleanup + suitability bar as prerequisites — you must
never RFQ gmail.com).

## 4. Monitoring & audit (from the first send)

- sent_messages is the ledger: every send with status (released/delivered/bounced/replied),
  releaser identity, template version. Queryable; reviewed daily during Phase 1.
- Bounce and no-reply rates tracked per supplier (feeds the future supplier-responsiveness
  signal and the dashboard).
- Any reply that fails quote-extraction lands in a review queue (never dropped).
- A daily Phase-1 ritual (5 min): what sent, what bounced, what replied, anything weird.

## 5. Success criteria (falsifiable)

1. **Rollback proven first:** with the gate ON in the deployed env, flipping it OFF stops
   delivery immediately (verified before the first real send).
2. **First delivery:** a real RFQ email delivered to a real allowlisted supplier's inbox
   (confirmed received, not spam), recorded in sent_messages with correct status.
3. **No unintended sends:** the sent_messages ledger contains ONLY allowlisted, Tom-released
   sends — audited after the first week. Zero sends from any non-gated path. Zero sends from
   local/dev environments.
4. **Reply captured:** a real supplier reply (or controlled test reply) is captured, associated
   to its RFQ, and does not cross into the intake stream.
5. **Quote loop closes:** a real reply containing a quote is extracted, proposed, confirmed by
   Tom, and the confirmed quote promotes the supplier via T4 on the live result — visible in
   the UI (supplier moves from outreach block to top of findings).
6. **Bounce path:** a bounced send is detected and statused; the supplier is flagged, not
   silently retried forever.
7. **Suppression honoured:** a supplier asking to stop is added to the suppression list and a
   subsequent send attempt to that domain is blocked in the send path (tested with a dummy).
8. **The teaser gets real:** at least one supplier_notifications / demand event derives from a
   REAL run + REAL send — the first genuinely honest data behind the portal teaser.
9. **Caps bite:** attempting to exceed the per-day cap is blocked and logged (tested
   deliberately).

## 6. Explicit non-goals

- No auto-ordering from quotes (unchanged, property-tested elsewhere).
- No unattended sending beyond the allowlist model.
- No marketing/nurture email of any kind — RFQs only. (The claim-link line inside the RFQ
  email — "want RFQs made easier? claim your profile" — IS in scope as part of the RFQ
  template, per the settled supplier-acquisition design.)
- No gofer.ai email migration (separate infra task; arkim.ai identity is fine for Phase 1).

## 7. Roles

- **Sergei:** mailbox + DNS (SPF/DKIM/DMARC), credentials into the deployed env, deploy,
  kill-switch drill, Phase-0 shadow verification.
- **Tom:** allowlist, per-send review/release, quote confirmations, the daily Phase-1 ritual,
  the go/no-go between phases.
- **Agent sessions (supervised):** the pre-flight send-path audit (§1), the allowlist/cap/
  suppression enforcement if any of it is missing in code (small build), the intake-vs-RFQ
  inbox scoping fix if the audit finds the streams can cross.

## 8. Open items the pre-flight audit must answer

- Does the send path already enforce an allowlist/cap/suppression, or is that a small build?
  (Believed missing — likely the one code task before Phase 1.)
- Inbox scoping: exact mechanism separating intake+<tenant>@ mail from RFQ replies when both
  are live (Night 8 flagged the RFQ reader reads in:inbox unfiltered).
- Template review: current RFQ draft template vs. what Tom actually wants a supplier to read
  (tone, the claim-link line, sender identity block).

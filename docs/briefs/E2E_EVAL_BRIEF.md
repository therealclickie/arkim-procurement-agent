# EVALUATION BRIEF — Flags-On End-to-End (Demo Readiness)

**Type:** EVALUATION ONLY. Nothing is built or fixed.
**Evaluator:** Claude Fable 5 (subscription). **Optional verify pass:** Claude Opus 5.
**Branch:** `eval/e2e-flags-on`, cut from `test/flag-on-integration` (2966 backend / 200 frontend).
**Deliverable:** `E2E_EVAL_REPORT.md` plus evidence under `eval/e2e/`.
**NO PUSH.**

---

## THE QUESTION THIS ANSWERS

> If the MRO rep sat a California food or pharma plant manager in front of Gofer tomorrow, with
> every pilot flag on, what would work, what would break, and what would it get wrong?

Every arc since 19 September was verified two ways: flags OFF (nothing regressed) and per-arc unit
tests with its own flag ON. **Nobody has run the whole pipeline with every flag on at once.** Intake,
identification, matching and banding, RFQ drafting, send governance, notifications, supplier login,
the session inbox, quoting and the buyer's view have never met each other in one run. This
evaluation finds out what happens when they do — before a customer or an investor does.

---

## HARD RULES

1. **No application source is modified.** You may write ONLY under `eval/e2e/` (scenario fixtures,
   runner scripts, captured output, screenshots) and `E2E_EVAL_REPORT.md`. The loop terminates if
   anything else changes.
2. **Do not fix anything.** A break you find is a finding, not a repair — however trivial, however
   obvious the fix. Record it with a repro and move on. Fixing is a separate arc.
3. **No real outbound mail.** `NOTIFICATIONS_V1` routes through `FakeProvider`; keep any Gmail/SES
   send path disabled and the governance allowlist restricted to test addresses. Before Phase 1,
   prove zero real sends are possible and record how you proved it. If you cannot prove it, STOP.
4. **No live AWS.** No boto3 client may reach AWS.
5. **Isolated data.** Run against a fresh or copied data directory and database. Never write to the
   developer's registry, known-parts cache, price DB or run history. Record the paths used.
6. **Live model and search calls — LIVE vs REPLAY mode.** Identification is the product's core
   claim, so evaluate it live if possible:
   - If `.env` provides the model and search keys the intake/sourcing agents need, run **LIVE**,
     capped at **150 external calls in total**. Stop scenarios early rather than exceed the cap.
   - Otherwise run **REPLAY/DEMO** using whatever cache-replay or demo mode exists, and mark every
     identification result **UNVERIFIED — replay**.
   - Record the mode, and the number of external calls made.
   - **Never print, log or copy any key into the report or evidence files.**
7. **Evidence or it didn't happen.** Every PASS and every BREAK carries evidence: the command run,
   the relevant output excerpt, or a file path under `eval/e2e/`. "Looks correct" is not evidence.
8. **EXECUTION RULE:** non-interactive print mode. Your session ends when your response ends. Run
   every command synchronously; never background a server test run without waiting for its result.
   Start any long-running server as a background process only if you also stop it before finishing,
   and never end your turn with a process you started still running.

---

## SEVERITY

- **BLOCKER** — the scenario cannot complete in a demo, OR the system does something unsafe: states a
  non-equivalent part as equivalent, sends real mail, fabricates data (a quote, a price, a supplier,
  an availability claim), leaks a token, or bypasses governance.
- **MAJOR** — completes, but a plant manager would see something wrong or misleading.
- **MINOR** — cosmetic, internal, or only visible in logs.

---

## PHASE 0 — PILOT CONFIGURATION (discovery; do this before any scenario)

**0.1 The pilot flag profile.** Enumerate every feature flag in the system — backend environment
variables, import-bound module attributes (see `conftest.py` `_FEATURE_FLAG_ENVS` and
`_FEATURE_FLAG_MODULE_ATTRS`), and frontend `NEXT_PUBLIC_*` flags. For each: what it gates, its
default, and any dependency on another flag. Then produce the **PILOT FLAG PROFILE** — the exact
set of variables for "everything a pilot needs on". This profile is itself a deliverable: nobody has
written it down, and the pilot cannot start without it. Record any flag combination that is
contradictory or undocumented as a finding.

**0.2 Boot.** Work out how to run the backend and frontend locally under that profile, against
isolated data. Record the exact commands so a human can reproduce the environment.

**0.3 Mode.** Decide LIVE or REPLAY per rule 6 and record why.

**0.4 Mail safety proof.** Per rule 3, demonstrate that outbound mail lands only in `FakeProvider`
capture. Record the proof.

**0.5 Seed.** Create, in the isolated data: one buyer (a California plant); three or four suppliers
drawn from a COPY of the registry, including DXP; supplier accounts with an OWNER, an ADMIN and two
MEMBERs on at least one supplier, so notification routing is exercisable. Record what was seeded.

---

## PHASE 1 — SCENARIOS

Run in this order. **S1 matters most — complete it fully before starting S2.** If you run out of
session, a thorough S1 beats four shallow scenarios.

For every scenario, record a step table: *step · expected · observed · PASS / BREAK / DEGRADED /
UNVERIFIED · evidence.*

### S1 — Breakdown, like-for-like (the anchor demo)
*"Line 2's Goulds 3196 pump is leaking at the mechanical seal. We need a replacement seal today."*

This is the path previously verified live (Tier-1 surfacing DXP on a Goulds seal request), now run
with everything on. Walk it end to end:
1. Intake through the most realistic channel available (email or text intake).
2. Identification — what part does it conclude, and is it presented as like-for-like?
3. Tier-1 matching and evidence banding — which suppliers, which bands, with what provenance?
4. RFQ drafted and released through send governance — captured in `FakeProvider`, not sent.
5. RFQ_NEW notification — does it reach ONLY the designated contacts (OWNER + ADMIN), per S1 routing?
6. Supplier signs in via the magic link (take the token from the captured mail) or the claim token.
7. The RFQ appears in the supplier's session inbox; `RfqView` is written.
8. Supplier submits a quote through the session.
9. The quote reaches the buyer's view, correctly attributed.
10. Buyer accepts; order state advances.
11. No escalation fires for this supplier, because the RFQ was viewed.

**BREAK** if any step fails, any recipient is wrong, or anything is fabricated.

### S2 — Substitute honesty (measures the gap the equivalence engine must close)
*"We need an SKF 6205-2RS C3 for the conveyor motor. SKF is on backorder — what can we get?"*

The equivalence engine is not built yet. This scenario measures how the current system behaves
without it — which is exactly what a plant manager would see today. Observe:
- Does it distinguish like-for-like from substitute, or blur them?
- Does it present cross-maker seal codes (2RS1 / 2RSR / DDU / LLU) as equivalent without evidence?
- Does it preserve the **C3** clearance, or silently drop it? (A CN bearing in a hot motor seizes —
  the dominant trap in the bearing research.)
- Does it fall back honestly — "needs verification" or "needs a human" — where it cannot be sure?

**BREAK (BLOCKER)** if a non-equivalent part is presented as equivalent. Honest uncertainty is a
PASS. Record every observation here in detail: this scenario's findings become the input spec for
the equivalence engine.

### S3 — Ambiguous request
*"The pressure gauge on the CIP skid is reading wrong. Need a new one."*

Observe whether the system asks before it acts: range, process connection thread, reference type
(gauge / absolute), wetted material, and — because CIP means hygienic service — whether it raises
hygienic requirements. **BREAK** if it sources a gauge without establishing range, connection and
reference type. Asking good questions is the PASS.

### S4 — Supplier silence and alert discipline
One RFQ sent to three suppliers on a **Friday at 15:00 Pacific**. Supplier A views and quotes.
Supplier B ignores it. Supplier C's address hard-bounces.

Drive time forward by calling the scheduler entry points with explicit `now` values through Monday
and Tuesday — never wait on the wall clock. Inject the bounce through the same store-level event
path the arc 4 tests use, and **record that this bypasses webhook signature verification** (the
webhook path itself is covered by unit tests).

Observe:
- Nothing sends over the weekend.
- B receives **one** consolidated reminder, on Monday, inside business hours.
- The escalation is **per account**, lands in the QUEUE tier, and fires once.
- C's address is suppressed and its alert lands in the correct tier (ACTION_NOW if it was the sole
  contact, DIGEST otherwise).
- Total supplier emails and concierge alerts — report the counts.

**BREAK** on any weekend send, any duplicate reminder or alert, or any notification dropped rather
than deferred.

---

## PHASE 2 — SEAM INVENTORY

List every point where two arcs hand off — intake → identification → matcher → bands → `rfq_send`
→ governance → notifications → portal session → quote store → buyer view, and governance across
all of them. For each seam: exercised in which scenario, and the result. A seam no scenario reached
is itself a finding: it means the demo has never proven it.

---

## PHASE 3 — HUMAN UI CHECKLIST

An API-level run cannot prove what is on screen. Write a click-through checklist, **no longer than
25 minutes**, for a human to walk the seeded data in a browser. It must cover at least: the session
cookie shows HttpOnly in devtools and is absent from `document.cookie`; the verify page waits for
the "Continue" click; the inbox and quote screens at phone width; that no screen claims something
the data doesn't support ("saved", "live to the buyer", a fabricated count). Give the exact URLs
and seeded logins to use.

If Playwright is already installed in the repo, you may use it for the parts it can check.
Do not add it as a dependency.

---

## DELIVERABLE — `E2E_EVAL_REPORT.md`

1. **Verdict** — one paragraph: *demo-ready*, *demo-ready with named avoidances*, or *not demo-ready*.
2. **Pilot flag profile** — the exact variables, plus contradictions found.
3. **Boot commands** — reproducible.
4. **Mode and cost** — LIVE or REPLAY, external calls made, and the mail-safety proof.
5. **Scenario step tables** — S1 to S4, as far as they got.
6. **Findings** — numbered, BLOCKER / MAJOR / MINOR, each with repro command, expected, observed,
   and evidence path. Findings from S2 grouped separately as **equivalence-engine inputs**.
7. **Seam inventory.**
8. **Demo guidance** — what can be shown live and honestly, what to steer around, and what must be
   fixed before a real plant manager sees it.
9. **Human UI checklist.**

Commit `E2E_EVAL_REPORT.md` and `eval/e2e/` on branch `eval/e2e-flags-on`. Do not push.

---

## OPTIONAL VERIFY PASS (Opus 5)

A second agent re-runs the repro for every BLOCKER and MAJOR finding and marks each **CONFIRMED** or
**NOT REPRODUCED**, appending a verification table to the report. This guards against findings that
don't hold up. Worth running before you use the report to decide what to fix.

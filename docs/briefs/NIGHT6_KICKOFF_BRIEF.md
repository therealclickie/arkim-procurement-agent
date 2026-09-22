# NIGHT 6 KICKOFF — Supplier Claim Portal (v1) + Demand-Signal Teaser

Overnight unmanned build. Foreground-only execution. NO PUSH.
Lines marked **[REVIEW-ADD]** were added in supervised review of the Night 5 report / handoff. Lines marked **[RESEARCH-ADD]** are derived from the two supplier-dashboard research studies (the Dashboard Blueprint + the Supplier-Portal Research). Strike either in supervised session if not wanted.

---

## PRE-FLIGHT (mandatory — fill before first action)

| Variable | Value |
|---|---|
| BASE_BRANCH | `test/flag-on-integration` |
| BASE_HEAD | `<capture with git rev-parse HEAD — record here>` |
| WORK_BRANCH | `feature/supplier-portal-overnight` |
| FLAG | `SUPPLIER_PORTAL_V1` **[REVIEW-ADD: settled — do NOT extend TIER1_V2; the first public route gets its own independent kill switch. The portal may DEPEND on TIER1_V2 data; the route's existence gates on SUPPLIER_PORTAL_V1 alone.]** |
| FLAG ASSERTION DIRECTION | Flag unset/absent/falsy ("", "0", "false") → the route DOES NOT EXIST (response byte-identical to any unknown route). Flag truthy → route live. Falsy-token parity test required (mirror Night 5 `test_falsy_token_is_flag_off`). |
| SUITE BASELINE | ~1795 passed / 73 skipped |
| ITERATION CAP | 5 per failing task |
| COMMIT STYLE | Conventional Commits, one commit per task |
| FINAL ACT | `MORNING_REPORT_NIGHT6.md` at repo root. NO PUSH. |

Pre-flight checks, in order:
1. `git rev-parse --abbrev-ref HEAD` = `test/flag-on-integration`; capture BASE_HEAD. (As of the Night 5 merge the base is at or ahead of the Night-5 tip — expect a HEAD later than `4cc06be`.)
2. `git status` clean of deleted/modified tracked files (pre-existing untracked briefs/audit docs acceptable). STOP if tracked files are dirty.
3. **[REVIEW-ADD — prerequisite-artifact gate (this is why Night 6 halted on the first attempt).]** Assert the Night 5 foundation is actually present on the base before proceeding, since the demand teaser is built on it. Run `git grep -l supplier_notifications -- "*.py"` and confirm it returns `utils/supplier_registry.py`. Then confirm the suite baseline is 1795 passed / 73 skipped, NOT 1740 — a 1740 baseline means Night 5 is not on the base and you MUST STOP (the teaser has no ledger to read). This check fails a stale base here in pre-flight rather than at the investigation gate.
4. Kill orphan python/node processes.
5. First action: `git checkout -b feature/supplier-portal-overnight` from BASE_HEAD; re-assert hash; STOP if it differs. If a `feature/supplier-portal-overnight` branch already exists from the halted first attempt, it is at BASE_HEAD with zero commits — either reuse it (re-assert it points at the current base) or delete and recreate; STOP if it carries any commits beyond base.

---

## MISSION

One token-authed public supplier page: claim/confirm profile + read-only demand-signal teaser. The claim portal and the future supplier dashboard are the SAME surface — panels grow later; do not scaffold them now. This is the app's FIRST PUBLIC ROUTE: security posture is the highest-risk part of the build and the most carefully-reviewed section of the morning report.

**[RESEARCH-ADD — lead with demand, not admin.]** The single strongest, most consistent finding across both supplier-dashboard studies: suppliers value portals that surface DEMAND and resent portals that only extract admin work ("Ariba-ification"). The demand teaser is therefore the HERO of this page, placed first; profile confirm/edit is the second element, not the top of the page. This is a hierarchy instruction for the page's data contract and default layout — not extra scope.

**[RESEARCH-ADD — the honesty carve-out (READ THIS).]** The dashboard research recommends filling empty states with demo/sample data so a new supplier "can imagine success." **DO NOT do this here.** Fabricated example RFQs / sample demand numbers on a real supplier's live claim page are the supplier-side analogue of the fabricated Tier-1 vendors and fabricated prices this whole build program has been eliminating — they violate the house honesty spine. The teaser shows ONLY real, genuine buyer-match data; the zero-state falls back to honest category/network framing or a plain "newly added" message (see T2). This is the one place the research is deliberately NOT followed.

---

## SETTLED DESIGN DECISIONS (rollout plan §14.1)

1. **Write model:** supplier-proposes / concierge-approves. Supplier edits land as PENDING REVISIONS via Night 4's review machinery. The portal NEVER writes the registry directly — blast radius of any bug is a pending queue, not corrupted supplier data.
2. **Entry:** concierge-generated magic-link ("Generate claim link" admin button → link → concierge sends manually). Decoupled from the unbuilt RFQ loop. Single-supplier-scoped, expiring, regenerable. Expiry default **7 days** **[REVIEW-ADD: tightened from 14 — links are regenerable on demand; if 14 is preferred, change here]**.
3. **Auth:** tokenized magic-link, no accounts/passwords. Anti-competitor protection in v1 is the concierge model. Domain-matched email verification is a later item — do not build it.
4. **Supplier sees/edits:** their extracted profile — brands (tri-state relationship the centerpiece), classes, ship-area/locations, aftermarket disclosure — plus a read-only demand teaser ("buyers matched your categories N times", from the `supplier_notifications` demand ledger). NEVER sees: lifecycle status, performance data, other suppliers, buyer/request data.
5. **Security:** token-scoped read + propose-revision endpoints ONLY; no admin surface reachable from the supplier route; invalid/expired token → safe generic page; rate-limited; flag-gated inert.

---

## STANDING GUARDRAILS

1. Branch from BASE_HEAD on `test/flag-on-integration` — verified, recorded.
2. Single committer; STOP if a foreign commit appears.
3. All new behavior behind `SUPPLIER_PORTAL_V1`; flag-off = route absent, proven by inertness tests.
4. Mocks in pytest; NO live network; NO live email sends. "Generate claim link" produces a link; it does not send. The `EMAIL_SEND_ENABLED` double-gate and conftest safety net remain in force.
5. Do-not-touch: `.env`, `audit/`, the phase3 branch, `scripts/*_self_test.py`, seed/demo fixtures, `known_parts.json`, `price_db.json`, `DEMO_MODE` gates, the §7.7 flaky orchestrator/persistence pair. The security/allowlist surface may be EXTENDED carefully for the new public route only — document the posture change explicitly in the report.
6. **[REVIEW-ADD] Do NOT create any drain/retry/re-send mechanism over `supplier_notifications`.** The demand teaser is a read-only aggregate count and nothing more. Stubbed notification events are terminal audit records until a deliberate supervised decision says otherwise.
7. THE PURGE GUARD IS SACRED — purge-guard and SCORING_V2/TIER1_V2 inertness suites untouched green.
8. LIVE-FAITHFULNESS — portal read + propose-revision tested through the REAL API (TestClient) with a real token, not just direct function calls.
9. Iteration cap 5 per failing task. Suite green (~1795 baseline) at every commit.
10. Final act: the morning report. NO PUSH.

---

## INVESTIGATION GATE (read-only; report with file:line; STOP for review before building)

- **I1.** How Night 4's review/pending machinery works — the portal's propose-revision must REUSE it, not reinvent it. Identify the exact seam (models, endpoints, approval application path).
- **I2.** Existing auth/token patterns — is there a tokenized-link primitive to reuse, or is this net-new? Report either way with evidence.
- **I3.** Frontend routing: how public vs admin routes separate; where a public supplier route attaches without exposing admin. Identify the admin-session/auth boundary precisely.
- **I4.** The `supplier_notifications` read path for the demand teaser — **and [REVIEW-ADD]** confirm the teaser can be satisfied entirely within token scope (the single supplier's own aggregate count) without widening any query surface. Nothing the route returns may be derivable per-request, per-buyer, or per-time-window beyond the bare count. **[RESEARCH-ADD]** Also confirm the read path can distinguish GENUINE buyer-match events from any seed/demo/test/synthetic notification rows, so the count shown to a real supplier is honest — report how (a flag/source field, a fixture-tenant exclusion, etc.). If it cannot distinguish them, that is a finding to STOP on, not to paper over.

---

## BUILD TASKS

- **T1. Token store + generation.** Single-supplier-scoped, expiring (7d default), regenerable claim tokens. Admin "Generate claim link" endpoint + button on the admin supplier view. **[REVIEW-ADD — token hygiene, required:]** tokens generated with `secrets.token_urlsafe(32)`-class entropy; stored HASHED at rest (SHA-256) — a registry-DB read must never yield a live link; lookup by hash, never string comparison over raw tokens; regeneration invalidates the prior token's hash.
- **T2. Public supplier route** (the first public route). Token-validated page serving the editable profile (brands/classes/ship-area/aftermarket) + read-only demand teaser. Hardened per decision 5. **[REVIEW-ADD:]** strict `Referrer-Policy: no-referrer` on the page; token kept out of server access logs where feasible; the route issues NO session cookies. **[REVIEW-ADD — zero-state teaser, per the dashboard research:]** if the supplier's own match count is 0, the teaser must NOT render "0 matches" — fall back to clearly-labelled category/network-level framing (e.g. "buyers post requests in your categories — confirm your profile to be matched") with no fabricated numbers. A zero-count hero is an anti-aha; a fabricated count is a lie. Handle both. **[RESEARCH-ADD:]** (a) **Time-windowed count** — the teaser is a bounded window ("in the last 30 days"), and the window is shown; not an unbounded all-time tally. Standard practice per the research, more useful, and it bounds the query. (b) **Honest data only** — the count reflects GENUINE buyer-match events; it must EXCLUDE any seed/demo/test/synthetic notification rows (see I4). No fabricated "example" demand under any circumstance (see mission carve-out). (c) **Hero placement** — the teaser is the first/primary element of the returned page contract; profile-confirm second. (d) **Mobile-legible** — a distributor inside-sales rep may open this on a phone; the public page must render legibly at mobile width (light touch; full styling is morning work).
- **T3. Propose-revision endpoint.** Supplier edits → pending revisions via Night 4's machinery (per I1). Registry UNCHANGED until concierge approves. **[RESEARCH-ADD — low-friction edit:]** the edit surface is confirm/correct, not a heavy multi-screen form. The research's clearest anti-pattern is the data-entry-heavy portal that offloads admin onto the supplier; keep the trio (brands/classes/ship-area + aftermarket) editable in a single lightweight pass. The tri-state brand relationship is the highest-value field (only the supplier authoritatively knows "authorized vs compatible-alternatives") — it is the centerpiece of the edit, not buried among lesser fields.
- **T4. Concierge review extension.** Supplier-proposed pending revisions surface in the existing admin review UI for approve/reject.
- **T5. Inertness + security tests.** Flag-off = route absent (byte-identical to unknown route); invalid / expired / reused-after-regeneration tokens → safe rejection, and **[REVIEW-ADD]** the rejection response is UNIFORM across all three cases (no oracle distinguishing wrong from expired). Property test: NO code path writes the registry from the supplier route. The public route exposes no admin surface. Rate-limit enforced, keyed on **[REVIEW-ADD]** both IP and token-prefix.

---

## SUCCESS CRITERIA (falsifiable)

1. Valid token → prepopulated profile + demand teaser renders through the real API.
2. Supplier edit → pending revision recorded; registry unchanged (asserted).
3. Concierge approve → revision applies via Night 4 machinery.
4. Expired / invalid / reused-after-regen token → safe, uniform, generic rejection.
5. Flag-off → route does not exist.
6. Property test proves no supplier-route registry write path.
7. Zero live sends; zero `supplier_notifications` writes from the portal (reads only).
8. **[RESEARCH-ADD]** Teaser shows only genuine buyer-match data within a stated window; zero-state renders honest category/network framing (never a "0" hero, never a fabricated count); asserted with both a has-matches and a no-matches fixture supplier.

---

## MORNING VERIFICATION (~20 min, supervised)

1. Generate a claim link for DXP Enterprises from admin.
2. Open it in a fresh browser/incognito (no admin session): profile + demand teaser render; NO admin data reachable. **[RESEARCH-ADD]** Sanity-check the teaser number against reality — DXP has the single stubbed Goulds-seal match recorded in Night 5, so the honest count should be small (≈1 in the window), not a padded or synthetic figure. If it reads higher, the query is counting test/seed rows (see I4).
3. Edit a brand relationship → lands as pending revision; registry unchanged.
4. Approve in concierge UI → revision applies.
5. Hit the route with a garbage token, an expired token, and the pre-regeneration token → identical safe page all three times.
6. Flag off → route 404s identically to an unknown route.

---

## DASHBOARD-RESEARCH GUIDANCE — applied now vs. deferred (context, not new scope)

**[RESEARCH-ADD]** So the overnight session understands where Night 6 sits in the researched blueprint and does not over-build:

Applied in v1 (this build): demand-as-hero placement; the match-event count as the day-one hook; time-windowed, honest-only teaser; the tri-state brand relationship as the centerpiece must-confirm; low-friction single-pass edit (anti-Ariba-ification).

Deliberately deferred (the research's "engagement" and "paid" tiers — later panels on this same surface, NOT now): RFQ inbox + quote aging; order-state pipeline; payment/remittance hub; win/loss diagnostics; performance scorecards (OTIF/response-time); missed-opportunity/competitive analytics; demand heatmaps; ERP/inventory API integration. Do not scaffold any of these.

Optional framing, allowed but not required (no extra endpoints): the must-confirm trio may be presented as a completeness cue ("confirm your brand relationships to improve match quality") — the research shows profile completeness lifts match quality and this is a cheap, honest nudge. Skip if it adds any scope.

Validation debt to log (not a build task): the research recommends 5–8 real supplier interviews (a distributor inside-sales rep, an aftermarket shop owner, a manufacturer's rep) before the FULL dashboard build. The thin claim portal proceeds without them; record this as an open item for when the engagement/paid panels are specced.

---

## OUT OF SCOPE

The fuller supplier dashboard (orders, quotes, payments panels), automated claim-link email sends, domain-matched email verification, any RFQ-loop wiring, any notification send-path work. Panels grow onto this page later; do not scaffold them.

---

## REPORT REQUIREMENTS

`MORNING_REPORT_NIGHT6.md` at repo root, matching the Night 5 report's structure: guardrail compliance (numbered, itemised), per-task status + commit hashes, suite counts vs baseline, every unspecified decision enumerated, blockers, morning-verification inputs with exact commands, and a dedicated **security posture** section documenting the allowlist/public-route change in full. NO PUSH.

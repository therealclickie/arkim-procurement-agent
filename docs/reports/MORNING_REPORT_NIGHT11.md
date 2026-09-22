# MORNING REPORT — Night 11: Supplier Quote Submission (QUOTE_SUBMIT_V1)

**Branch:** `feature/quote-submission-overnight` (off `test/flag-on-integration`, BASE_HEAD `0f39d10b`). **NOT pushed.**
**Suite:** baseline confirmed **2122 passed / 73 skipped** pre-flight → final **2260 passed / 73 skipped** (+138 new tests, zero regressions, green at every commit).
**Mission closed:** a supplier — claimed or unclaimed — submits a structured quote in five fields through the real API, and that quote becomes the confirmation record the Night-9 T4 promotion consumes: the card jumps from the outreach block to Band A on the live run. All of it behind `QUOTE_SUBMIT_V1`, default OFF, byte-identical when off.

---

## 1. Investigation gate (I1–I5)

**I1 — the T4 promotion seam (the decision that shaped everything).**
The confirmation record the Night-9 promotion consumes today is an `_index_quotes` item (api_server.py:929): `{status: "confirmed", supplier_domain, thread_id, confidence, payload: {unit_price, currency, lead_time, terms}}`, assembled per run by `_build_quote_index` (api_server.py:954) from `supplier_registry.get_review_items(run_id, kind="quote")` + `get_sent_messages`. Candidates join by normalized `source_url` domain, thread-primary (`_resolve_quote`, api_server.py:970); `_quote_overlay` (api_server.py:988) overlays price/lead; the read-time Band-C promotion loop (api_server.py:1054-1065 pre-change) calls `promote_confirmed` (ranking_bands.py:337) when a resolved quote carries `payload.unit_price`.
**Decision:** the current shape carries the spec's promotion-relevant fields fine, so real quotes are **adapted into that exact shape** (`quote_store.as_confirmation_record`) and merged inside `_build_quote_index` — the ONE existing index builder — flag-gated. `_resolve_quote`, the promotion loop, and `promote_confirmed` are untouched; **no second promotion mechanism exists.** Read-time expiry falls out naturally: a lapsed quote stops appearing in `get_active_quotes` → stops feeding the index → the card reverts. Precedence: a structured quote overrides the domain-fallback slot (supplier-authored, lifecycle-backed); thread-matched concierge-confirmed email quotes still win thread-primary resolution (see §5, decision 4).

**I2 — claim-token machinery** (utils/claim_tokens.py:1-323): `secrets.token_urlsafe(32)`, SHA-256 hash at rest, prefix-only in the clear, own sqlite file, fail-soft everything, flag defense-in-depth, uniform 404 (`_portal_reject_404`, api_server.py:5258), rate-limit keyed (IP, token-prefix) (api_server.py:5223). Mirrored into a **separate namespace**: `utils/quote_tokens.py` + `data/quote_tokens.sqlite`. Cross-token isolation is structural (different stores) and pinned by tests both ways.

**I3 — RFQ send seam:** `send_rfq` (utils/rfq_send.py:88) mints `rfq_id` before the body is recorded/sent — the injection point; the letter is `outreach._make_draft` (outreach.py:42), with `CONTACT_NOMINATION_ASK` as the settled precedent for a mechanical template *addition*. RFQ identity for token scope = the send-time `rfq_id` + run_id + part_key + domain, all persisted on the token row.

**I4 — portal surface:** public routes `/api/portal/{token}/*` (api_server.py:5290+); the propose→approve write model applies ONLY to profile revisions (`supplier_portal.propose_revision`) — quotes write their own store directly, authoritative on submit; **no coupling** (asserted by the T3 route: never touches revision machinery).

**I5 — price band:** `price_db.get_cached_prices(manufacturer, part_number, max_age_days=30)` (price_db.py:109) → median over vendor prices = the band; empty/missing identity/store error → `None` → the sanity check **skips** (band absence flags nothing).

No investigation finding contradicted the brief or spec — no HALT.

## 2. Per-task commits

| Task | Commit | Content |
|---|---|---|
| T1 quote store + lifecycle | `e0665e8` | `utils/quote_store.py` (own sqlite; submit/supersede/read-time expiry/review transitions/sanity reasons/`as_confirmation_record`); QUOTE_SUBMIT_V1 → conftest pin list; 43 tests |
| T2 quote tokens | `92a91bd` | `utils/quote_tokens.py` (hashed at rest, single-RFQ scope, revisable, closed-state, revoke(+per-RFQ)); cross-token isolation tests; 19 tests |
| T3 submission API + promotion | `477e6aa` | Paths A/B/C endpoints, review queue, `_build_quote_index` merge, uniform-404 posture, separate rate buckets, no-auto-order property; 35 tests |
| T4 public quote form | `1725a8c` | `frontend/src/app/quote/[token]/*` + `lib/quote-api.ts` + CSS (portal palette, ≥44px, closed/rejected/submitted/soft-error states, claim pitch) |
| T5 portal open requests | `c3ba0c8` | GET open-requests + GET quote history (own-only), portal `OpenRequests` component reusing the same `QuoteForm`; 6 tests |
| T6 card + template + ack + digest | `3fe35b1` | `_quote_overlay` card fields, `{quote_link}` template line (flag-on) + send-time token mint/substitution, stubbed ack via the real `GmailSender` seam, digest `quotes` section; 18 tests |
| T7 acceptance suite | `d0d9e88` | `test_quote_acceptance.py` — spec §10 criteria 1–10 as classes on the live path; 17 tests |
| docs | `6f82b43` | `design/interactions.md` Night-11 section; CLAUDE.md §4 flag note |

## 3. Spec §10 acceptance table

| # | Criterion | Result | Evidence (test) |
|---|---|---|---|
| 1 | Unclaimed: token → form → pn_confirmed → active → Band A, no account; claim pitch post-submit | **PASS** | `TestC1UnclaimedPath` (unclaimed capability-pivot supplier promotes from outreach block to Band A via the real POST; `claim_pitch: true`) |
| 2 | Onboarded: DXP via portal → TOP of Band A | **PASS** | `TestC2OnboardedPortalPath` (DXP tops Band A above another supplier's earlier active quote) |
| 3 | Concierge entry → identical promotion | **PASS** | `TestC3ConciergePath` |
| 4 | Wrong-part gate: edited PN → review, approval promotes labelled as QUOTED PN | **PASS** | `TestC4WrongPartGate` (`pnDiffers`/`quotedPartNumber` on the live card; reject path pinned too) |
| 5 | 100x price → review, never the buyer's screen | **PASS** | `TestC5SanityFlag` (price_db-seeded band; queue carries the reason) |
| 6 | Supersede + expiry both visible on the live run | **PASS** | `TestC6SupersedeAndExpiry` (card price updates on revision; lapsed quote reverts card to outreach — no zombie) |
| 7 | Token security: hashed at rest, single-RFQ scope, closed states, uniform-404 enumeration | **PASS** | `TestC7TokenSecurity` + `test_quote_tokens.py` |
| 8 | No auto-order (property) | **PASS** | `TestC8NoAutoOrder` + `TestNoAutoOrderProperty` (orders.create_order/place_order instrumented to raise; every quote surface exercised) |
| 9 | Flag-off ⇒ endpoints absent byte-identical; suite green ≥2122/73 + new | **PASS** | `TestC9FlagOff` (9 surfaces byte-compared against an unknown route; active quotes un-merge instantly on kill-switch) + final suite 2260/73 |
| 10 | Acks/notifications stubbed under existing gates; zero live sends | **PASS** | `TestC10StubbedSends` (real `GmailSender.send` spied → "stubbed"; digest is the only notice surface) + `TestQuoteAck` |

## 4. Flag-off parity proof

- **Route absence:** every new endpoint (public GET/POST `/api/quote/{token}`, portal open-requests/history/submit, all four admin quote routes) returns 404 with `resp.content` **byte-compared equal** to FastAPI's unknown-route body — including admin routes with valid/wrong/absent credentials (the flag check deliberately precedes `require_admin`, otherwise a 401 would reveal the route exists).
- **Behavior absence:** `_build_quote_index` merge skipped (an active quote written while ON stops surfacing the moment the flag flips — `TestC9FlagOff::test_flag_off_promotion_merge_inert`); the RFQ template is byte-identical rfq-v1 (`test_quote_t6::test_flag_off_template_is_byte_identical...`); a stale `{quote_link}` draft has the line dropped whole (never a literal placeholder to a supplier); `_quote_overlay` on email quotes keeps the exact legacy key set (pinned); the digest has no `quotes` key; the portal page renders nothing new.
- **Store dormancy (defense-in-depth):** quote_store/quote_tokens writes and transitions no-op flag-off; the sqlite files aren't even created.
- **Suite:** conftest autouse pin now includes `QUOTE_SUBMIT_V1`; the whole 2260-test suite runs flag-off-by-default and is green.

## 5. Decisions the spec/brief left open (enumerated)

1. **Store placement:** own module + own sqlite (`utils/quote_store.py`, `data/quotes.sqlite`; tokens in `data/quote_tokens.sqlite`) — the claim_tokens/intake_channels precedent, not a supplier_registry table. Keeps the module standalone and the namespaces structurally isolated.
2. **Qty sanity thresholds:** spec pinned price (3x/0.2x) but not qty — chose **>10x / <0.1x of requested**, either side missing skips (partial quotes are real).
3. **"Review" as a status:** the spec's `active=false, review=true` is expressed as `status="review"` (+ `review_reasons_json`) so the lifecycle is one column.
4. **Index precedence:** a structured quote overrides the *domain-fallback* slot; a thread-matched concierge-confirmed email quote still wins thread-primary resolution for candidates with thread history (rare pre-live-sends; flag-only surface).
5. **Claim pitch mechanics:** the POST response returns `claim_pitch: bool`; the page renders an invitation into the EXISTING concierge-issued claim flow ("reply to the email") — no inline claim-token mint (claim minting stays admin-gated, and SUPPLIER_PORTAL_V1 may be off).
6. **"Claimed" predicate:** onboarding_status ∈ onboarded statuses OR tier1_lifecycle ∈ {onboarding, onboarded}; unknown supplier ⇒ unclaimed (pitch shows — an offer, not a disclosure).
7. **Ack scope:** rfq_link + portal submissions only; concierge entries send no ack (the supplier already corresponded with a human). Ack copy is new minimal text — flag it if ack copy is founder-owned too.
8. **TEMPLATE_VERSION:** left at `rfq-v1` — the flag-on quote-link line is a mechanical addition (the ledger stores the exact body anyway). Bump if you'd rather the version distinguish it.
9. **Closed-state oracle trade-off:** a KNOWN token on a dead RFQ renders the closed state (spec requirement) rather than the uniform 404 — knowing a real token already proves receipt of the RFQ email; enumeration of unknown tokens stays uniform-404.
10. **Link base URL:** `ARKIM_PUBLIC_BASE_URL` env (path-only link when unset).
11. **Existing-quote hint:** the GET form context includes the supplier's own latest active/review quote (revision UX); exposes nothing beyond their own submission.
12. **Digest "expiring soon"** = active quotes within 3 days of `valid_until`.

## 6. Verification commands

```powershell
uv run pytest -q                                             # 2260 passed / 73 skipped
uv run pytest utils/procurement_agent/tests/test_quote_acceptance.py -q   # the §10 matrix (17)
uv run pytest utils/procurement_agent/tests/test_quote_store.py utils/procurement_agent/tests/test_quote_tokens.py utils/procurement_agent/tests/test_quote_api.py utils/procurement_agent/tests/test_quote_t6.py -q
cd frontend; npm run type-check; npm run lint                # both clean
git log --oneline 0f39d10..HEAD                              # the 8 commits above
```

## 7. Out of scope / untouched

Payments, PO generation, auto-order (property-tested absent), dashboard, negotiation, multi-currency, reply-parser changes (path D untouched — its confirm action already lands review_items rows the index reads), template copy rewrites, any live send. `utils/sourcing_archieved/`, orchestrator/core.py, and all do-not-touch paths untouched. Pre-existing untracked scratch files at repo root left as found.

**NO PUSH performed. Branch `feature/quote-submission-overnight` is local only.**

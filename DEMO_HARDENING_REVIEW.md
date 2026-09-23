# DEMO HARDENING REVIEW — Arc 5

**Reviewer:** Claude Fable 5 · **Date:** 2026-09-23
**Branch:** `arc5/demo-hardening` · **Branch point:** `028e3f18b1862b7c22a4a13e6ae30def33f8da08`
**Reviewed:** `DEMO_HARDENING_BRIEF.md`, `DEMO_HARDENING_REPORT.md`, the full diff
`028e3f18..HEAD` (12 commits: gate + T1–T10 + report), against
`git show eval/e2e-flags-on:E2E_EVAL_REPORT.md`.

## VERDICT: APPROVED

Both suites were run by the reviewer this session, synchronously, flags off:

```
uv run pytest -q          → 3205 passed, 73 skipped, 1 warning in 288.55s
cd frontend && npm test   → 25 files, 205 passed (vitest 4.1.10)
```

Exactly the report's claim (2966 + 239 backend, 200 + 5 frontend). All ten tasks
are committed, one commit per task, in order. The two BLOCKER-class traces (badge
integrity, order pricing) were done by reading the code, not the report, and both
hold. Findings below are MINOR only — none blocks approval.

---

## Reviewer checklist R1–R8

### R1 — Modified pre-existing test files vs the authorised list: PASS

`git diff --name-status 028e3f18..HEAD` shows `M` on exactly four pre-existing
test files:

- `utils/procurement_agent/tests/test_api_server.py`
- `utils/procurement_agent/tests/test_intake_variant_disambig.py`
- `utils/procurement_agent/tests/test_run_capture_live.py`
- `utils/procurement_agent/tests/test_tier1_runtime_live.py`

All four appear in `loop/AUTHORISED_TEST_EDITS.txt` (lines 1, 2, 4, 6). The other
two authorised files (`test_notifications_coalescing.py`, `test_scoring.py`) were
not touched, matching the build report's stated reason (the at-risk alternatives
were not the implementation chosen). No frontend pre-existing test file was
modified (`frontend/src/lib/__tests__/backend-url-default.test.ts` is new, `A`).

### R1b — Line-by-line read of each authorised diff: PASS

- `test_api_server.py`: four `TestConfirmIntake` fixtures gain `"model": "3196"`
  (seed-only; every assertion byte-identical); the one superseded assertion —
  `test_spec_described_no_model_unaffected`, which pinned the 200 that IS F-15 —
  is REPLACED (not deleted) with a pinned 422 + `reason == "identity_insufficient"`
  + `override == "source_anyway"` + the override reaching 200, and renamed
  (`test_api_server.py:1259-1280`). Fence clause satisfied: the invariant (the
  family guard still never fires on a spec-described request) is kept in its new
  form.
- `test_intake_variant_disambig.py:542-575`: same single replacement, same shape;
  the "not the family guard's refusal" invariant is explicitly re-asserted
  (`detail["reason"] == "identity_insufficient"   # not "family_variant"`).
- `test_run_capture_live.py:206-213`: the zero-results fixture gains a `model`;
  all assertions unchanged.
- `test_tier1_runtime_live.py:159-164`: the shared `_run_sourcing` helper takes
  `?source_anyway=true` (its fixture deliberately carries a null PN token and no
  model — that IS the fixture); all 13 call sites' assertions unchanged.

No assertion was deleted; each superseded one was replaced with a pin on the new
behaviour, per prime-directive clause 2.

### R2 (BLOCKER-class) — Can the extractor's verdict raise a match badge anywhere? NO

Every production path was traced:

- The only producers of `isExactMatch` / `pnMatchLevel` in the codebase are
  `api_server.py:964` and `:968`, both set from `badge_integrity.resolve(...)`
  (`api_server.py:922-931`) — verified by repo-wide grep: no other non-test file
  writes either field.
- `badge_integrity.resolve` (`utils/badge_integrity.py:151-188`): the
  deterministic classifier is the ceiling (`level = classifier_level`); the
  extractor-derived `advisory_level` applies only when `_rank(advisory) <
  _rank(level)` (`:170-172`) — downgrade only; a bare-domain row is capped below
  exact-grade (`:176-178`, `has_resolvable_listing` at `:92-108` rejects the three
  observed F-11 URLs); `is_exact` additionally requires the classifier's own
  `exact` (`:186`). Every verdict carries a reason (`pnMatchReason`,
  `api_server.py:966`).
- `_transform_option` is reached only via `_transform_sourcing_results`
  (`api_server.py:1170`, `:1214`), whose single production caller
  (`api_server.py:1892-1893`, `_orm_to_detail`) passes the run's parsed specs —
  so `searched_pn`/`manufacturer` are real, and the F-B cache-replay paths
  (`api_server.py:1302-1303`, `:1451-1452`), which re-derive `pn_match_status`
  from `match_type`, feed only the *advisory* input and cannot raise anything.
- A caller that omits `specs` classifies everything `none`
  (`badge_integrity.classify` `:115-116`) — loses badges, never over-claims
  (builder's F-K; the safe direction).
- `no_exact_match` (`api_server.py:1924`) is derived from the post-gate
  `pnMatchLevel`, so it inherits the gate.
- The upstream producer of the false claim (`utils/sourcing_archieved/
  enterprise_search.py:540-546`, unmodified) still writes `match_type = "Exact
  OEM"` into *stored* results — but nothing renders stored candidates to the
  buyer except through the gate, and `isAftermarket` (the only other raw
  `match_type` read, `api_server.py:969`) is not an exact badge.
- Frontend: `options-screen.tsx:36` and `approval-context.tsx:61` read only the
  two gated fields; `match.tsx:23-24` adds render entries for the new
  `mismatch` / `needs_verification` levels (red / amber — neither exact-grade).

The extractor cannot raise a badge on any path. The builder's own F-A honestly
records the residual that band *ranking* (not badging) can still be lifted by a
bare `pn_match_status` — outside R2/R3 as written, correctly left open.

### R3 (BLOCKER-class) — Can a listing price ever stand in for an accepted quote? NO

All order-creating paths traced (grep for `create_order` / `place_order`):

1. **`/execute`** — `ProcurementAgent._selection_for_order`
   (`utils/procurement_agent/agents/procurement_agent.py:213-236`) resolves the
   run's quote on the same `(run_id, source-URL domain)` key the buyer card used;
   an active quote's terms overwrite the listing terms via
   `order_quote.apply_to_selection` (price, currency, quantity, lead time,
   `quote_id`); a stale quote plants `_quote_refusal`, which `_execute` honours
   *before* capturing anything (`procurement_agent.py:86-93`) — refuses, does not
   fall back. The no-quote-no-price path returns "Order captured as draft —
   unpriced; needs a quote before it can be placed." (`:124`).
2. **`/order-now`** (gate finding F-C, closed) — `api_server.py:2843-2851`:
   refusal → 409; an active quote's `unit_price` replaces `raw_price` *before*
   the price-less 422 check (so a quote-only candidate now prices instead of
   422ing); `apply_to_selection` stamps the quote id (`:2924-2928`); the spend
   threshold is computed on the quote price. The above-threshold branch defers
   the order to `/execute`, which re-resolves the quote at execution time — a
   quote that expires between order-now and execute still refuses.
3. **`/review-items/{id}/place-order`** (`api_server.py:5200-5236`) — untouched;
   its price IS an email-parsed quote, so no listing price can be substituted.
   The builder's F-J correctly records that this path cannot notice a withdrawn
   structured quote — a genuine residual, honestly reported.

Supporting guarantees verified: `orders._resolve_price` blocks the price_db
fallback when `quote_id` is present (`utils/orders.py:163-168`);
`quotes.unit_price` is `REAL NOT NULL` (`utils/quote_store.py:121`), so
`apply_to_selection`'s price-is-None branch is unreachable for real quotes; a
new submission always supersedes prior active/review quotes for the same
`(run_id, domain)` (`quote_store.py:367-375`), so `resolve_for_order`'s
newest-first single-row check (`utils/order_quote.py:118-121`) cannot skip an
older active quote; an unreadable quote store REFUSES rather than reporting
absence (`order_quote.py:109-113`) — R1's "never claim more than provable"
applied to the failure path. The `quote_id` column is added through the existing
idempotent `_ADDED_COLUMNS` migration (`orders.py:95-100`).

### R4 — Is the request-link equality a true byte comparison with a contrast case? YES

`test_auth_mail_loud_failure.py:209-252`: the fingerprint is `(status_code,
resp.content, sorted headers)` with only `date`/`content-length`/`server`
excluded — raw bytes, not parsed JSON. Three pairwise/set equalities cover send
succeeded vs refused vs unknown address, and the contrast case (a 422 on a
malformed body) proves the comparison is not vacuous (`:245-252`). The R7
server change adds NO distinguishable response: no diff hunk touches the
`request-link` handler (verified against the hunk list); the alert is raised
inside `SesProvider.send` (`utils/mail_provider.py:345-352`), fail-soft
(`raise_auth_refusal_alert`, `:168-190` — an alerting failure never changes the
send result, pinned by `test_an_alerting_failure_never_changes_the_send_result`),
and deduped on the configuration fault via the store's unique index. The boot
guard (`api_server.py:143-169`) matches the DEMO_MODE precedent's shape and
position, names the variable, and exempts FakeProvider — which now captures auth
mail instead of refusing (`mail_provider.py:270-281`) while
`message_configuration_set` itself stays provider-agnostic, keeping every
pre-existing assertion on it intact.

### R5 — Are the eval-evidence tests built from the actual evidence? YES

Every cited evidence file exists on `eval/e2e-flags-on` (all eight checked with
`git show`), and the fixtures were compared against them:

- `eval_s1_quote_order.json` — order `20a608f1…` (`unit_price: null`, `draft`,
  timestamps to the microsecond) and quote `c9639b15…` ($189 / "2 days" /
  `dxpe.com`) match `s1_step10_accept_order.json` and the verify pass exactly.
- `eval_s2_badge_candidates.json` — all 23 rows match
  `s2_step4_candidate_analysis.json` field-for-field (Rodavictoria $18.81 /
  Intech $15.05 / BDS bare-domain `isExactMatch: true` rows; Quality Bearings
  `6205-2rsh/c3-skf` scored `none`).
- `eval_verify_s2_notation.json` — rows match
  `verify/s2_step4_candidate_analysis.json` (JSB, Radwell, 123Bearing spot-checked).
- `eval_s3_sufficiency.json` — `s3_specs_at_sourcing` matches
  `s3_step2_confirm_attempt.json` `sourced.specs_at_sourcing` verbatim, including
  the full `confidence_reasoning` string.
- `eval_s1_supplier_mail.json` — the RFQ_NEW (mail 5) and Tier-1 FYI (mail 3)
  match `s1_outbox_final.json`; the FYI subject was compared at codepoint level
  (identical, including the run UUID).
- `eval_s1_variant_guard.json`, `eval_f03_auth_mail.json` — sources exist and are
  cited with `_source` keys in every fixture.

No invented fixture found.

### R6 — Is the R4 override genuinely explicit and visibly labelled? YES (in-app)

`confirm-intake?source_anyway=true` is a distinct query parameter
(`api_server.py:3062`); without it the floor 422s with `reason:
"identity_insufficient"` and `override: "source_anyway"`
(`utils/intake_sufficiency.py:60-67`). The override records a durable
acknowledgement into `asset_specs_json` (`record_override`, `:106-127`: who/when/
what was missing), marks the run `spec_incomplete`, every read of its results
carries the banner (`api_server.py:1183-1186`, rendered at
`sourcing-view.tsx:59-67` with a testid), and no candidate in such a run can be
badged exact (`badge_integrity.resolve` `:182-184`). The floor runs AFTER the
registry-driven family guard, so registry-defined classes keep their stronger
gate; S1 (`Chesterton`/`155`) still confirms and S3 is refused — both pinned by
tests built on the eval fixtures. Residual: the email intake consumer bypasses
the floor entirely (builder's F-I / gate F-G) — an email request can still reach
sourcing unspecified. That is consistent with the brief's OUT OF SCOPE ruling on
email intake (F-06, its own arc) and is recorded, not hidden. See finding 2.

### R7 — Both suites, flags off: GREEN (run by the reviewer, above)

`tests/conftest.py` pins every flag off; the backend suite is the flags-off run.
3205 + 205, both matching the report's exact claims.

### R8 — FINDINGS genuine, or task-avoidance? GENUINE

- **F-H** (units-override narrowed): legitimate — the broad rule really does
  contradict `test_motor_units_hp_frame_override_pump` in a file NOT on the
  authorised list, and the narrow fix (`intake_agent.py:87`, bore_diameter
  priority 6→5, plus `:118-125`, bore-alone never reclassifies a named seal)
  demonstrably closes both observed seal cases while leaving the NEMA-frame
  correction untouched (pinned by four T6 tests). This is the fence working as
  designed, not avoidance.
- **F-I, F-J, F-K**: real residuals, correctly scoped out, each traced by this
  review (F-I at the email consumer's `_commit_intake_to_sourcing` call; F-J at
  `place_order_from_quote`; F-K at the `specs=None` default).
- **F-A** restated as still open: verified — `ranking_bands.assign_band` can
  still lift a row's *band* on a bare `pn_match_status`; that is ranking, not
  badging, and outside R2/R3 as written. Right call to record rather than
  silently expand scope.

---

## Findings

1. **MINOR** — `api_server.py:3182-3184` (brief R5, R4's labelling clause by
   analogy): an identity-sufficient run blocked ONLY by the hygienic question set
   can be overridden with `source_anyway=true` and leaves **no durable trace on
   the run** — `record_override` runs only when the identity floor fired
   (`:3171-3173`), so a hygienic-only override produces no `spec_incomplete`
   marker, no banner, and no recorded acknowledgement; the only record is the
   RUN_CAPTURE event (`:3193-3195`), which is flag-gated and off by default.
   Within the letter of the brief (R5 is "a question-set addition only", and
   R4's marking language covers the identity floor), but the override that skips
   hygienic questions is less visible than the one that skips identity. Worth a
   line in the next arc or CLEANUP.
2. **MINOR** — `api_server.py` email-intake path (builder's F-I, restating for
   the verdict record): the R4 floor is enforced at the `confirm_intake` endpoint
   only; the channel-agnostic email consumer shares `_commit_intake_to_sourcing`
   and can still reach sourcing on an under-specified request. Accepted per the
   brief's OUT OF SCOPE ruling (email intake statefulness, F-06); flagged so the
   post-hardening evaluation does not read S3-closed as channel-closed.

No BLOCKER or MAJOR findings.

---

## Out-of-scope confirmations

- `EMAIL_SEND_ENABLED` untouched; nothing bypasses send governance (the R7
  change only *adds* an operator alert on an already-refused send, and the
  FakeProvider exemption applies only where no real transport exists).
- No new feature flag; `QUOTE_SUBMIT_V1` (pre-existing) gates the R1 quote read,
  exactly as the gate's F-F reasoned.
- `design/interactions.md` and `frontend/README.md` were updated in the same
  change set, per house convention.

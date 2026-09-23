# DEMO HARDENING REPORT — Arc 5

**Branch:** `arc5/demo-hardening` · **Builder:** Claude Opus 5 · **Date:** 2026-09-23
**Brief:** `DEMO_HARDENING_BRIEF.md` · **Findings source:** `git show eval/e2e-flags-on:E2E_EVAL_REPORT.md`
**Status:** INVESTIGATION GATE ONLY (J1–J10). Nothing built. No test, source or config file modified.

## Baseline (measured this turn, not quoted)

```
uv run pytest -q          → 2966 passed, 73 skipped, 1 warning in 213.56s
cd frontend && npm test   → 24 files, 200 passed (vitest 4.1.10)
```

Matches the brief's `2966 / 200` starting point.

---

## J1 — The order path: where an accepted quote must enter

**The three code paths that create an order. None of them reads `quote_store`.**

| # | Path | Price source | Site |
|---|---|---|---|
| 1 | `/execute` (select → approve → execute) | candidate `base_price`/`price`, else `price_db` | `api_server.py:4266-4276` → `procurement_agent.py:81`, `:183-210` → `orders.py:141-176` |
| 2 | `/order-now` (sub-threshold, manual fulfilment) | candidate `base_price`, reconstructed server-side | `api_server.py:2775-2782`, `:2836-2851` |
| 3 | `/review-items/{id}/place-order` (email-quote path) | review-item `payload.unit_price` | `api_server.py:5099-5117` |

**Path 1 in detail (the S1 break, F-07).**

- `ProcurementAgent._execute` (`utils/procurement_agent/agents/procurement_agent.py:66-118`) gates on `Phase.APPROVED|EXECUTING` (`:75-79`), builds the selection at `:81`, captures at `:98-102`, places at `:112`.
- `_selection_for_order` (`:183-210`) resolves the candidate, then sets `"unit_price"` **only** from `candidate.base_price / basePrice / price` (`:190-193`), suppressed entirely when `price_tbd` (`:189`). The run's quotes are never consulted. `manufacturer`/`part_number` come from `asset_specs_json` (`:203-204`).
- `_resolve_candidate` (`:158-181`) handles both selection shapes; the api_server path stores only `{candidate_id, tier}` (`api_server.py:2654-2665`) and is resolved positionally against `sourcing_results_json`.
- `orders._resolve_price` (`utils/orders.py:141-176`) falls back to `price_db.get_cached_prices(manufacturer, part_number)` keyed on `(mfg, pn)` + vendor. **S1's `part_number` is `null`**, so the fallback cannot fire (`eval/e2e/evidence/s1_step10_accept_order.json`: `part_number: null`, `unit_price: null`, `status: "draft"`, `source: "rfq"`).
- `orders.place_order` refuses without a price (`utils/orders.py:291-293`), so `_execute` returns `placed: false` with `"Order captured as draft; not placed (no resolvable price)."` (`procurement_agent.py:113-116`).
- The verify pass's read-only DB check confirms the pair: order `20a608f1…` `unit_price NULL` / `draft`; quote `c9639b15…` `active` at `189.0` / `"2 days"` for the same `run_id` + `dxpe.com` (`eval/e2e/evidence/verify/verify_offline_checks.json` → `F-07`, with `order_path_references_quote_store: false`).

**Where the quote already is, and where it must enter.**

- `quote_store.get_active_quotes(run_id)` (`utils/quote_store.py:500-506`) returns effectively-active quotes, with **read-time expiry** applied by `effective_status` (`:276-289`) — an expired quote simply stops appearing, which is exactly the "expired → refuse, do not fall back" input R1 needs.
- The buyer card already receives it: `_build_quote_index` merges structured quotes as confirmation records (`api_server.py:1025-1037`), `_resolve_quote` joins **by normalized source-URL domain, thread-first** (`api_server.py:1041-1056`), and `_quote_overlay` stamps `evidenceState: "quoted"`, `price`, `leadTime` and **`quoteId`** — explicitly labelled the "order-provenance hook (spec §7)" (`api_server.py:1099-1100`).
- **The correct entry point is `_selection_for_order`** (`procurement_agent.py:183`): it holds the run (hence `run_id`) and the resolved candidate (hence `source_url` → domain), which is precisely the `(run_id, domain)` key `_resolve_quote` uses. Resolving there rather than inside `orders._resolve_price` keeps `utils/orders.py` a pure capture store (see F-E).
- Terms the quote carries that the order does not yet: `unit_price`, `currency`, `quantity`, `lead_time`, `valid_until`, `quote_number`, `quoted_part_number`/`pn_differs` (`quote_store.as_confirmation_record`, `:513-545`). The `orders` table already has `currency`, `quantity`, `lead_time` columns (`utils/orders.py:69-86`) but **no `quote_id` column** — see F-D.

**What the buyer UI shows per order state** (`frontend/src/components/proc/order-section.tsx`):
`placed|confirmed|shipped|received` → `OrderTracking` (`:71`, `:221-260`); `pending_manual_fulfilment` → `BeingPurchased` (`:73`, `:195-218`); `draft` **or** `execute.data.placed === false` → `noPrice` (`:64`) → `PlaceOrderCard` renders *"We need a confirmed quote before this order can be placed."* (`:105-111`). R1's "unpriced — needs a quote" copy is therefore largely present already; the defect is that S1 reaches it **with an accepted quote in hand**.

## J2 — Every place a match badge is set, and `_classify_pn_match`

**Badge sites (the only two fields the UI reads as "exact"):**

| Field | Set at | Derived from |
|---|---|---|
| `isExactMatch` | `api_server.py:917` | `opt["match_type"] == "Exact OEM"` |
| `pnMatchLevel` | `api_server.py:915` → `_pn_match_level` (`api_server.py:848-856`) | tier 1: `bool(found_part_number)`; tier 2/3: map of `pn_match_status` (`exact_match→exact`, `partial_match→normalized`, else `none`) |

The frontend consumes both: `options-screen.tsx:35-37` (`isExact` = `isExactMatch || pnMatchLevel in {exact, normalized}`), `approval-context.tsx:61` ("Exact replacement"), `vendor-card.tsx:145` (`<PnMatch level=…>`). `api_server.py:1858-1859` derives `no_exact_match` from `pnMatchLevel == "exact"`.

**Where those inputs are produced — all LLM/extractor-sourced, none deterministic:**

- Tier 2 discovery: `utils/sourcing_archieved/enterprise_search.py:166-173` — `exact_match = bool(item["exact_match"])` straight from `_llm_parse_results`. **One** deterministic correction exists (`:165-167`: found PN ≠ searched PN forces `exact_match=False`, but by plain uppercase string equality with no normalisation) plus a URL heuristic (`:190-194`, collection page → Functional Alternative).
- **Tier 3: `utils/sourcing_archieved/enterprise_search.py:540-546`** — `pn_status` comes off the LLM vendor JSON (`:505-507`, validated only against the allowed *vocabulary*), and `pn_status == "exact_match"` → `match_type = "Exact OEM"` with **no check at all**. This is F-11's mechanism: the extractor's verdict becomes `isExactMatch: true` *and* `pnMatchLevel: "exact"`, on rows whose `url` is a bare domain.
- Cache paths are honest: price_db hit → `"Functional Alternative"` with a written rationale (`enterprise_search.py:120-131`); the known_parts edge defaults the same way (pinned by `test_api_server.py:1546-1580`).
- Sourcing agent tier 1: `utils/procurement_agent/agents/sourcing_agent.py:620` — `"Exact OEM" if pn_match else "Functional Alternative"`.
- `api_server.py:1232` and `:1342` re-derive `pn_status` **from** `match_type` on the cache-replay paths (the inverse mapping) — see F-B.

**`_classify_pn_match` — the deterministic classifier nothing on the badge path calls.**

`utils/sourcing_archieved/scoring.py:219-252`. Levels `exact | normalized | stem | substring | none`, scored by `PN_MATCH_POINTS` (`:210-216`). Inputs `(searched_pn, found_pn, snippet, manufacturer)`. Order: raw uppercase equality → `normalize_part_number` equality (`sourcing_agent.py:91-95`, strips every non-alphanumeric) → manufacturer stem (`sourcing_agent.py:102-113`, via `brand_intelligence.get_pn_stemming_rule`) → snippet substring → `none`. Its only caller is `_compute_suitability_score` (`scoring.py:557-563`), which indexes `PN_MATCH_POINTS[level]` directly at `:564` and feeds `_fit_signal` (`:433-462`).

**Why it scores `6205-2RSH/C3` as `none`** — measured this turn, read-only:

```
scoring._classify_pn_match("6205-2RS C3", X, "", "SKF")   ranking_bands.classify_pn_evidence("6205-2RS C3", X)
  "6205-2RS"          → none                                 → canonical
  "6205-2RS C3"       → exact                                → exact
  "6205-2RS/C3"       → normalized                           → exact
  "6205-2RS-C3"       → normalized                           → exact
  "6205 2RS C3"       → normalized                           → exact
  "6205-2RSH/C3"      → none                                 → compatible
  "6205-2RS1/C3"      → none                                 → compatible
  "6205-2RSH-C3-SKF"  → none                                 → compatible
```

Cause: `normalize_part_number` yields `62052RSC3` vs `62052RSHC3` — the inserted seal-designation letter breaks equality, and no SKF stemming rule salvages it, so it falls through to `none`.

**Consequence for R3:** the separator/clearance *notation* clause is **already satisfied** by `_classify_pn_match` (`/C3`, `-C3`, ` C3` all normalise equal — the three T3 table rows pass today). What is missing is (a) a `mismatch`-with-reason verdict for "C3 requested, listing has none", which is currently indistinguishable from any other `none`, and (b) a `needs verification` verdict for a seal-designation difference within a family, currently `none`/"incompatible".

**A second, disagreeing deterministic classifier exists** — see F-A.

**Live evidence shape for T2 fixtures:** `eval/e2e/evidence/s2_step4_candidate_analysis.json` — rows of `{vendor, tier, band, foundPartNumber, isExactMatch, pnMatchLevel, price, url, suitability, comparisonArtifact, c3_in_found_pn, cross_maker_seal_code}`. Note Radwell: `url` is a **Timken `6205-2RS`** page but `foundPartNumber` is `6205-2RS-C3` — the verify pass's "the found PN is sometimes just the request echoed back."

## J3 — `confirm_intake`, `family_disambig_block`, registry required fields

**`confirm_intake`** — `api_server.py:2963-3082`. Guards, in order (`:3016-3070`):

1. `404` unknown run / demo-session mismatch (`:3018-3020`)
2. `409` not in `Phase.INTAKE` (`:3021-3028`)
3. `422` `asset_specs_json` empty (`:3029-3033`)
4. DEMO_MODE sourcing cap `429` (`:3034-3040`)
5. **`family_disambig_block`**, skipped entirely when `open_family` (`:3051-3070`)

then `_commit_intake_to_sourcing` (`:3072-3075`). **There is no other sufficiency check** — exactly the verify pass's conclusion (F-15 CONFIRMED).

**`family_disambig_block`** — `utils/procurement_agent/agents/intake_agent.py:336-399`. Returns `None` unless **all** of: `_is_family_level` (a `model` present **and** no real `part_number`, `:246-251`), a non-empty `_variant_selecting_attrs_for` (`:254-281`), and (`pending` **or** an unanswered attr) (`:380-386`). On the S3 gauge it returns `None` (measured: `verify_offline_checks.json` → `F-15.family_disambig_block_on_s3_specs: null`) because there is no `model` — so a spec-less generic part sails straight through.

**What the registry defines per class** — `utils/procurement_agent/part_type_registry.py:155-337`:

| profile | `blocking_attrs` | `variant_selecting_attrs` |
|---|---|---|
| `mechanical_seal` (ANCHORED) | shaft_size, cartridge_vs_component, single_vs_double, face_material_class, elastomer_product_cip_compatibility | **shaft_size** |
| `pump` | type, connection_size, hydraulic_duty, wetted_material | **hydraulic_duty** |
| `valve` | type, size, class_or_cwp, connection, body_and_seat_material, actuation | *(none)* |
| `sensor_instrument` (configurable) | measured_variable, range, output_type, process_connection, wetted_material, hazardous_area_rating | *(none)* |
| `motor_drive` | hp, rpm, voltage_phase, frame, enclosure, mount, inverter_duty_if_vfd_fed | **hp, voltage_phase** |
| `UNKNOWN_PROFILE` (`:121-136`) | `[]` | `[]` |

Off-registry classes fall back to `CATEGORY_REQUIRED_FIELDS` (`intake_agent.py:43-51`: motor / vfd / mechanical seal / **bearing → bore_diameter** / pressure sensor / valve). "Answered" resolves through `VARIANT_ATTR_TO_SPEC_FIELDS` + `variant_attr_answered` (`part_type_registry.py:427-452`), which maps registry attr names onto real `AssetSpecs` fields.

**Reading of R4 adopted for the build (stated explicitly, because the J10 list depends on it).**

R4 says required fields come "from the part-type registry **where it defines them**", else "the minimum is a manufacturer plus a model, or a manufacturer part number". The registry field that *discriminates* is `variant_selecting_attrs`, and only that reading satisfies all four of T4's acceptance cases at once: a `blocking_attrs` reading would refuse S1 (which supplies one of five seal blockers) and so contradict T4's *"the S1 path still confirms"*, and would make T4's *"a fully specified request confirms normally"* require a seven-field motor fixture. Therefore:

> **the gate = (the existing `family_disambig_block`, unchanged) + a new identity-sufficiency floor: refuse unless `manufacturer` AND (`model` OR `part_number`) are non-null (`_NULL_VALUES`, `intake_agent.py:60`), unless the explicit override is passed.**

S3 (`description` + `detected_type` only) is refused; S1 (`Chesterton` / `155` / `shaft_size`) confirms; the clean-PN escape hatch is untouched.

**Where the `spec_incomplete` marker goes.** `_commit_intake_to_sourcing` (`api_server.py:3084-3135`) is the SINGLE load-bearing mutation, and already writes honesty markers into `asset_specs_json` the same way (`:3115-3119`, `spec_based_sourcing` / `family_open_commit`). The `spec_incomplete` flag and the recorded acknowledgement belong there; `RunDetail` passes non-`_`-prefixed spec keys through unchanged (`api_server.py:1850-1853`), and the `exact_only` / `open_family` query-parameter idiom (`:3051`, `:3110-3119`) is the precedent for the override. See F-G for the scope boundary at the second caller.

## J4 — Where intake context and notes live, for R5

- `AssetSpecs` carries `description`, `raw_text`, `use_case`, `duty_cycle`, `failure_mode`, `material_spec`, `connection_size` (`utils/models.py:63-93`). S3's saved specs used `description: "Replacement pressure gauge for CIP skid"` **and** `use_case: "CIP skid pressure measurement replacement"` — the hygienic token was present in two fields already (`eval/e2e/evidence/s3_step2_confirm_attempt.json` → `sourced.specs_at_sourcing`), and the extractor's own `confidence_reasoning` even says *"CIP applications typically require sanitary-grade gauges"*. The signal is there; nothing acts on it.
- The user's turn text is available verbatim inside `IntakeAgent.run` (`intake_agent.py:698-711`, where `text` is passed to `apply_quantity` and `_maybe_classify`), so detection can read the live message, not only the merged specs.
- The registry already encodes the hygienic vocabulary as `inference_rules`: `_SANITARY_INFERENCE` keyed on `CIP / dairy / sanitary / food` → `{wetted: 316L, elastomer: EPDM, connection: Tri-Clamp}` (`part_type_registry.py:142-153`), replicated per profile (`:196`, `:225`, `:254-259`, `:292-297`), plus `_WASHDOWN_INFERENCE` (`:155-158`). R5's trigger list (CIP, SIP, sanitary, washdown, food, dairy, beverage, pharma, 3-A, EHEDG, tri-clamp) is a superset — extend that token set rather than introducing a parallel one.
- The question surface is `_variant_disambiguation_question` / `_next_clarification` (`intake_agent.py:284-333`, `:1105-1150`) plus the profile `q2_template`. `sensor_instrument.blocking_attrs` already holds `process_connection` and `wetted_material` (`part_type_registry.py:288-295`); **missing for R5**: connection *size*, and hygienic *certification* (3-A / EHEDG / none). No profile covers "fittings" — R5's fitting case must ride the `valve` / UNKNOWN path or gain a class; the narrow reading (question-set addition only, no hygienic equivalence logic) keeps this inside the existing question builder.
- Adding blocking attrs is safe against the registry invariants: `test_part_type_registry.py:95-101` asserts only non-empty and `len >= 3`; `:104-111` asserts `blocking_attrs ∩ refinement_attrs == ∅` (so do not duplicate a refinement name); `:144-146` asserts inference rules are well-formed.

## J5 — Why the variant guard re-asks supplied attributes (F-08), and the units override

**Two distinct mechanisms, both confirmed in code and in the evidence.**

1. **The chat re-asks by design.** `intake_agent.py:774-782`: when `_q2_variant` has never been asked and the request is family-level with variant-selecting attrs, the turn is forced to `needs_clarification` **regardless of whether the extractor already filled those attrs** — the documented "block-regardless-of-extracted" rule (`:756-765`), justified at `:363-368` by the absence of field provenance. On S1's in-app turn the request already carried the shaft size and the question fired anyway (`eval/e2e/evidence/s1_step1c_inapp_fallback.json`).
2. **The hard guard names a supplied field as missing.** `intake_agent.py:385-398`: the block fires on `pending` alone even when `missing == []`, and then `surface = missing if missing else list(vs_attrs)` (`:390`) puts **every** variant-selecting attr into `missing_attrs`. That is exactly S1's 422 — `{"missing_attrs": ["shaft_size"], "missing_labels": ["shaft size"], "pending": true}` against `asset_specs.shaft_size = "1.875 inch"` (`eval/e2e/evidence/s1_step1d_confirm_intake.json`). The *label* is the bug; blocking on `pending` is the design.

   → R6's second clause ("the hard guard must not report as missing a field present in `asset_specs`") is closable **without weakening anti-hallucination**: when `missing == []`, stop calling them missing — surface `missing_attrs: []` with a distinct reason (unconfirmed, not absent) and keep the 422.
   → R6's first clause ("must not re-ask an attribute the request already supplied") needs a provenance signal. The tractable one — which does not exist as a field but does exist as data — is **the user's own turn text**, available at `intake_agent.py:698-711`. "Supplied" = present in the message; "filled" = present only in the extraction. That keeps the hallucination guard live for the extractor-invented case, which is why the two hallucination tests in J10 are marked at-risk rather than superseded.

**The `mechanical seal -> Bearing` override IS reachable from the in-app path — so it is in scope here, not the email arc.**

`classify_by_units` (`intake_agent.py:88-127`) is called unconditionally inside `IntakeAgent.run` at `intake_agent.py:714-720`, and `IntakeAgent.run` is the single implementation behind **both** the in-app chat (`api_server.py:2230`, `:2394`) and the email consumer (`utils/intake_channels.py:518`). Measured read-only this turn:

```
classify_by_units({"detected_type":"mechanical seal","shaft_size":"1.875 inch","bore_diameter":"1.875 in"})
  → ("Bearing", "Part", True)      # override APPLIED
classify_by_units({"detected_type":"mechanical seal","shaft_size":"1.875 inch"})
  → (None, None, False)            # no override
```

Cause: `UNIT_CLASSIFICATION_RULES` (`:73-82`) gives `bore_diameter → Bearing` and `shaft_size → Mechanical Seal` **the same priority 6**, and the loop's `if priority <= best_priority: continue` (`:95-96`) lets the earlier list entry win — so a seal request with any extracted bore diameter is reclassified `Bearing`. The `best_base in current_type` guard (`:108-111`) does not catch it, because "bearing" is not a substring of "mechanical seal". The misclassification then misroutes `_variant_selecting_attrs_for` to the bearing fallback (`bore_diameter`) and the downstream noun-class gate.

## J6 — Boot sequence, `message_configuration_set`, concierge alerts (R7)

- **Boot-refusal precedent:** `api_server.py:123-141` — under `DEMO_MODE`, a module-import `RuntimeError("Refusing to start: …")` naming the offending variable, before any request is served. R7's SES guard should copy this block's shape and position.
- **Flag reads:** `SUPPLIER_ACCOUNTS_V1` is LIVE-read (`api_server.py:93-94`; `utils/supplier_accounts.py:74+`), `MAIL_PROVIDER` is read per send (`utils/mail_provider.py:66`; `active_provider` at `:359-372`, defaulting to `ses` when `NOTIFICATIONS_V1` is on). A boot check therefore reads the env directly at import; conftest pins both flags to `""` (`tests/conftest.py:71-75`), so the guard is inert for the whole suite.
- **The refusal itself:** `mail_provider.message_configuration_set` (`:143-161`) returns `(None, "auth mail requires the tracking-off configuration set (SES_CONFIGURATION_SET_AUTH is unset); refusing to send on a tracking-enabled set")` whenever `metadata["auth_mail"]` is set and the var is empty. **Both** providers honour it: `FakeProvider.send` (`:244-248`) and `SesProvider.send` (`:311-316`).
- **`auth_mail` is set by:** `supplier_accounts.send_magic_link_email` (`utils/supplier_accounts.py:1104-1107`, only when `notifications_active()`) and the member-invite path (`:1233+`).
- **R7's FakeProvider exemption belongs in `FakeProvider.send`, not in `message_configuration_set`.** `test_mail_provider.py:250-257` asserts that `message_configuration_set` itself returns the refusal when the var is unset. Keeping that function provider-agnostic, and letting `FakeProvider.send` proceed and capture on `configuration_set=None`, leaves every existing auth-mail assertion intact — which is why no edit to that file appears in J10.
- **Concierge alerts:** `notifications_store.raise_alert(kind=…, dedupe_key=…, tier=…)` (`utils/notifications_store.py:1136-1171`). Dedupe is enforced by a unique index (`IntegrityError → None`, `:1167-1168`), so "one alert, deduped" is a write-level guarantee rather than a caller convention. Tiers at `:160-179`; `alert_tier` falls back to `TIER_QUEUE` for an unknown kind (`:181-186`), so a new kind must be added to `ALERT_TIERS` to land `ACTION_NOW`. No test asserts an exhaustive alert-kind set — `test_notifications_ceiling_and_tiers.py:210-213` is table-style and explicitly accepts `"SOMETHING_NEW"` — so a new kind is purely additive.
- **The enumeration-oracle constraint:** `/api/supplier/auth/request-link` (`api_server.py:6674-6757`) already returns `JSONResponse({"ok": True}, headers=_portal_response_headers({}))` from **five** distinct branches — unknown account, member not active (`:6735-6741`), mint failure (`:6743-6747`), and post-send regardless of `send_status` (`:6749-6757`). R7 must leave all five byte-identical; the alert is raised on the send path and never reflected in the response.

## J7 — RFQ_NEW and TIER1_FYI templates

**F-09 is a CALLER bug, not a template gap.** `_rfq_subject_and_body` (`utils/notifications.py:290-309`) already renders `"New quote request — {manufacturer} {part_number}"` plus a quantity line. But `notify_rfq_sent` — the only production caller, fired from the `rfq_send` seam (`utils/rfq_send.py:280-283`) — builds its `rfq` dict with **exactly three keys**:

```python
rfq = {"run_id": run_id, "supplier_domain": supplier_domain,
       "sent_message_id": sent_message_id}          # utils/notifications.py:718-719
```

so `part` is empty and the mail degrades to the generic form. `_notify_rfq_new` then persists that same empty `detail` onto every notification row (`:829-831`), which is why the **reminder** (`_reminder_subject_and_body`, `:1185-1213`) could carry "Goulds 3196-seal" in S4 (the S4 harness supplied the part directly) while the RFQ_NEW in S1 could not. Observed: `eval/e2e/evidence/s1_outbox_final.json` mails 5–6 — subject `"New quote request"`, body `"Arkim has sent you a request for quote."`, portal link only.

**The data is available at the seam.** `send_rfq(candidate, approved_draft, approval, *, run_id, part_key, …)` (`utils/rfq_send.py:128-137`), and `_substitute_quote_link` (`:60-96`) already demonstrates the exact fail-soft lookup: `persistence.get_run(run_id)["asset_specs_json"]` → `manufacturer`, `part_number`, `quantity`, wrapped in `try/except` with the send unaffected on failure (`:78-86`). R8's needed-by date rides the same specs read (`quote_tokens.mint_for_rfq(… need_by=…)` already models the field — `test_quote_api.py:91-99`).

**Coalesced-batch form:** `_batch_subject_and_body` (`utils/notifications.py:596-614`) — a one-item batch delegates to `_rfq_subject_and_body` verbatim (`:605-606`); a multi-item batch is `"{n} new quote requests"` plus one `_rfq_line` per item (`:581-588`, rendering `"{manufacturer} {part_number} (qty {n})"`). Fixing the caller therefore fixes the batch body lines too. Digest variant: `:1355`.

**F-10 — the Tier-1 FYI leaks internals.** `utils/procurement_agent/tier1_notify.py:223-231`:

```
subject = f"Arkim matched request — {match.noun_class} (run {run_id or 'n/a'})"   # :223  RUN UUID + class vocab
  "Matched class: {noun_class}"                                                    # :226
  "Relationship: {brand_relationship or 'class-matched (no brand row)'}"           # :228
  "Core class: {'yes' if match.is_core else 'no'}"                                 # :229
```

Observed verbatim in `s1_outbox_final.json` mail 3: `Arkim matched request — SEAL (run e0489093-4d8c-4563-ab8f-cb6c789ed6db)`. `test_tier1_notify.py` (390 lines) asserts gating, notify reasons, caps and send status — **no assertion touches the subject or body** — so this is free to fix.

**Supplier-facing template inventory for T8's UUID / denylist scan:** `notifications.py:298` (RFQ_NEW subject), `:301-308` (body), `:606-613` (batch), `:1197-1204` and `:1205-1212` (reminder), `:1355` (digest), `:191` and `:413` (notification-mail seams); `rfq_send.py:177` (`_subject_from_draft`, founder-owned copy); `supplier_accounts.py:1111-1121` (magic link) and `:1233+` (invite); `tier1_notify.py:223-231` (FYI). Buyer-facing ack/clarify/confirm replies live in `intake_channels.py:544-564` and are not supplier-facing.

## J8 — Frontend default and the backend port

- `frontend/next.config.ts:6-7`: `process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"`. The backend serves `:8001` — `README.md:139` (`uv run uvicorn api_server:app --reload --port 8001`), `CLAUDE.md` §3, and the eval's boot commands.
- The only reason this machine works is the **git-ignored** `frontend/.env.local` (43 bytes, `NEXT_PUBLIC_API_URL=http://localhost:8001`; ignore rule at `.gitignore:51`). A fresh clone has no such file, so every `/api/*` rewrite misses. Confirmed by the verify pass (`verify_offline_checks.json` → `F-02.default: "http://localhost:8000"`).
- Consumers of the same variable, which inherit the corrected default automatically: `frontend/src/lib/api.ts:11-17`, `frontend/src/app/admin/page.tsx:26-29`.
- **`frontend/README.md:49` also documents `NEXT_PUBLIC_API_URL=http://localhost:8000`** and must move to `:8001` in the same change (R9's "record the convention in the README"). No test asserts either value, in either suite.

## J9 — Every hard-coded data-directory site

**15 store modules carrying `_DATA_DIR = <repo>/data`** (all the identical `os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")`):

`utils/audit_log.py:65`, `utils/brand_intelligence.py:30`, `utils/claim_tokens.py:82`, `utils/intake_channels.py:308`, `utils/notifications_store.py:217`, `utils/orders.py:39`, `utils/quote_store.py:75`, `utils/quote_tokens.py:66`, `utils/run_capture.py:68`, `utils/run_labels.py:77`, `utils/send_governance.py:46`, `utils/site_settings.py:18`, `utils/spec_lookup.py:30`, `utils/supplier_accounts.py:185`, `utils/supplier_registry.py:87`.

**Plus four the brief calls out or that list misses:**

- `utils/procurement_agent/state/persistence.py:32-36` — a four-deep `dirname` chain → `<repo>/data/sourcing_runs.sqlite`.
- **`utils/known_parts.py:40`** — `os.path.dirname(__file__)` → `utils/known_parts.json` (inside `utils/`, not `data/`).
- **`utils/price_db.py:30`** — `utils/price_db.json` (same).
- `api_server.py:353` — `_HANDOFFS_PATH = <repo>/data/mock_maintenance_handoffs.json` (the `/api/dev/reseed-handoffs` seed).

No module reads any env override (`verify_offline_checks.json` → `F-04.any_env_override: false`). The eval had to monkeypatch each module individually (`eval/e2e/harness.py`, `_STORE_MODULES` + `isolate_stores`) — that list is the working inventory and matches the 15 above, with `known_parts` / `price_db` handled separately for exactly the reason noted here.

**Test-impact note for R10.** `tests/conftest.py:24-26` computes its own `_ROOT/data/test_procurement.sqlite`; with `GOFER_DATA_DIR` unset the helper must return a byte-identical path so conftest and every `monkeypatch.setattr(mod, "_DATA_DIR", tmp_path)` in the suite keep working. Resolve the directory **inside a helper the modules call**, and keep the module-level `_DATA_DIR` / `_DB_PATH` attributes in place — dozens of fixtures patch those attributes, and removing them would break far more than this arc may touch.

---

## J10 — PROPOSED TEST EDITS

Derived by reading the tests. Every `confirm-intake` reference in the suite (78 matching lines across 11 files) was traced to the specs its test seeds; every assertion on a match badge, an order price, a PN level, an auth-mail configuration set, and a supplier-mail subject or body was read in place.

Lines marked **[at-risk]** are listed deliberately: their pinned behaviour survives under the implementation this report recommends, but is superseded under a plausible alternative implementation of the same ruling. An assertion missed here cannot be changed later without stopping the arc, so they are included for the human to prune. The builder must not touch any file absent from the approved list.

PROPOSED TEST EDIT: utils/procurement_agent/tests/test_api_server.py :: 1012-1032, 1034-1058, 1097-1114, 1116-1148 :: confirm-intake returns 200 and advances to sourcing on specs that are `{"manufacturer": "Goulds"}` only — no model, no part number :: R4
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_api_server.py :: 1247-1258 :: `test_spec_described_no_model_unaffected` — confirm-intake 200 on `{manufacturer: None, model: None, part_number: None, detected_type: "ball valve", connection_size: "2 inch"}`, i.e. the F-15 spec-less class reaching sourcing :: R4
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_api_server.py :: 1192-1201 :: [at-risk] `test_pending_with_filled_attr_still_blocks_anti_hallucination` pins "pending + extractor-filled hp/voltage still 422"; superseded only if R6 is implemented as "attrs present ⇒ do not ask / do not block" rather than as turn-text provenance :: R6
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_intake_variant_disambig.py :: 542-563 :: `test_run_spec_described_then_confirm_200_unaffected` — confirm-intake 200 on real-run specs for a spec-described valve with no manufacturer, model or part number :: R4
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_intake_variant_disambig.py :: 156-177, 523-540 :: [at-risk] the two hallucination-guard cases pinning "the extractor filled the attrs but the ask still fires / confirm still 422s"; superseded only if R6 is implemented as "attrs present ⇒ do not ask" :: R6
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_run_capture_live.py :: 206-224 :: `test_zero_results_outcome` — confirm-intake reaches background sourcing on specs that are `{"manufacturer": "ObscureCo"}` only :: R4
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_tier1_runtime_live.py :: 152-160 :: the shared `_run_sourcing` helper seeds `part_number="UNKNOWN-PN"` (a null token) with no model and relies on confirm-intake succeeding; all 13 call sites (lines 171, 180, 190, 206, 221, 231, 239, 250, 266, 281, 306, 330, 343) otherwise read `sourcing_results` off a run left in intake :: R4
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_scoring.py :: 22-85 :: [at-risk] `TestClassifyPnMatch` pins the five-level return vocabulary of `_classify_pn_match` and `PN_MATCH_POINTS[level]`; superseded only if R3's `mismatch` / `needs verification` verdicts are added to that function's return set instead of to a new badge-facing wrapper :: R3
PROPOSED TEST EDIT: utils/procurement_agent/tests/test_notifications_coalescing.py :: 137-143 :: [at-risk] `test_a_single_batched_request_keeps_the_one_request_wording` asserts the RFQ_NEW subject by exact equality (`"New quote request — Goulds 3296"`); superseded only if R8 changes the subject format rather than only supplying the part identity the caller currently drops :: R8

**Files examined and found to contain NO superseded assertion** (recorded so the absence is a finding rather than an omission): `test_orders.py`, `test_orders_state.py`, `test_procurement_execute.py`, `test_quote_acceptance.py`, `test_quote_api.py`, `test_quote_store.py`, `test_mail_provider.py`, `test_notifications_auth_mail.py`, `test_notifications_invite_governance.py`, `test_notifications_rfq_new.py`, `test_notifications_escalation.py`, `test_notifications_ceiling_and_tiers.py`, `test_tier1_notify.py`, `test_ranking_bands.py`, `test_cache_replay_banding.py`, `test_demo_mode.py`, `test_labeling_api.py`, `test_intake_api.py`, `test_part_type_registry.py`, `test_intake_agent.py`, `test_sourcing_filter.py`, `test_sourcing_agent.py`, `tests/conftest.py`, and all 24 frontend vitest files. The reasoning per class of "no" is in the J-sections: J1/F-E (flag-gating keeps the order suites untouched), J6 (FakeProvider placement keeps every auth-mail assertion intact), J4 (registry additions are additive under the existing invariants), J8 (no test asserts the config default), J3 (the remaining confirm-intake suites all seed manufacturer + part number or manufacturer + model, e.g. `test_cache_replay_banding.py:37-38`, `test_ranking_bands.py:1109-1113`, `test_quote_api.py:32-33`, `test_demo_mode.py:974-977`).

---

## FINDINGS (gate)

- **F-A (new, adjacent to F-11/F-12).** A **second** deterministic PN classifier disagrees with the first on exactly the clearance case: `ranking_bands.classify_pn_evidence("6205-2RS C3", "6205-2RS")` returns **`canonical`** (`utils/procurement_agent/ranking_bands.py:93-122`, prefix rule at `:119-121`, `_CANONICAL_MIN_BASE_LEN = 6` at `:52`) — i.e. it treats `C3` as "a configuration code on the same part identity" — and `assign_band` gives `exact|canonical` plus a real URL **Band A** (`:381-386`). Additionally `:392` lets a bare `pn_match_status in ("exact_match","partial_match")` lift a row to Band B. So even after R2 corrects the *badge*, the C3-less bearing can still be **ranked** as a confirmed part. Band assignment is outside R2/R3 as written; recorded so it is not mistaken for closed.
- **F-B (new).** `api_server.py:1232` and `:1342` derive `pn_match_status` **from** `match_type` on the cache-replay paths — the inverse of `_pn_match_level`. An R2 downgrade applied only at `_transform_option` will be bypassed on those paths unless the stored candidate is corrected too.
- **F-C (new).** A **third** order-creation path bypasses the quote entirely: `/order-now` (`api_server.py:2775-2782`, `:2836-2851`) takes the reconstructed candidate's listing price directly, and 422s a price-less candidate with *"Candidate has no buyable price — request a quote instead"* (`:2777-2779`) even when an active quote for that supplier already exists on the run. R1's "never substitute a listing price for an accepted quote" must be traced through this path, not only `/execute` — this is what the reviewer checklist's R3 trace will look for.
- **F-D (procedural).** `utils/orders.py` has no `quote_id` column; R1's "records the quote id it came from" needs one, added through the existing idempotent `_ADDED_COLUMNS` + `_migrate` pattern (`utils/orders.py:92-105`).
- **F-E (procedural, test-safety).** `quote_store.get_quotes` is deliberately **not** flag-gated (`utils/quote_store.py:451-460`) and its `_DATA_DIR` is the repo's `data/` (`:75-76`). `test_orders.py:21-26` and `test_procurement_execute.py:21-26` isolate `orders` and `price_db` to `tmp_path` but not `quote_store`. Any quote read added to the order path must sit behind `quote_store.quote_submit_active()` (`:64`, pinned OFF by `tests/conftest.py:74`), or a normal `uv run pytest` will read and create the **developer's** `data/quotes.sqlite`.
- **F-F (interpretation, stated not assumed).** The brief's prime directive 3 ("Flags off = today's behaviour exactly") refers to the existing flag set; the rulings introduce no new flag, and the post-hardening evaluation re-runs the same `PILOT_FLAGS` profile — so R1–R10 are built **unconditionally**, with R1's quote read gated by the pre-existing `QUOTE_SUBMIT_V1` (which the pilot profile sets on). Were a new default-off flag intended, both `eval/e2e/harness.py:56-70` and `tests/conftest.py:71-75` would need it, and the re-run would not measure the fix.
- **F-G (scope boundary, recorded not acted on).** R4's floor is applied at the `confirm_intake` call site only. The channel-agnostic intake consumer shares `_commit_intake_to_sourcing` (`api_server.py:3084`) but runs its own family pre-gate (`:3106-3108`); adding the floor there would deepen F-06 (email intake dead-ending on a family request), which the brief puts out of scope.

## Ruling → finding → task map (for the build turn)

| Ruling | Finding | Task | Primary sites |
|---|---|---|---|
| R1 | F-07 | T1 | `procurement_agent.py:183-210`, `orders.py:141-176`, `api_server.py:2775-2851` (F-C), `quote_store.py:500-545` |
| R2 | F-11 | T2 | `enterprise_search.py:540-546`, `:165-173`; `api_server.py:848-856`, `:915-917`, `:1232`, `:1342` (F-B) |
| R3 | F-12 | T3 | `scoring.py:219-252`; `sourcing_agent.py:91-113` |
| R4 | F-15 | T4 | `api_server.py:3029-3082`, `:3084-3135`; `intake_agent.py:336-399`; `part_type_registry.py:155-337` |
| R5 | F-16 | T5 | `part_type_registry.py:142-158`, `:264-302`; `intake_agent.py:284-333` |
| R6 | F-08 | T6 | `intake_agent.py:385-398`, `:774-782`, `:73-127` (units override) |
| R7 | F-03 | T7 | `api_server.py:123-141`, `:6674-6757`; `mail_provider.py:143-161`, `:244-248`; `notifications_store.py:1136-1171` |
| R8 | F-09, F-10 | T8 | `notifications.py:698-723`, `:290-309`, `:581-614`; `tier1_notify.py:223-231` |
| R9 | F-02 | T9 | `frontend/next.config.ts:6-7`; `frontend/README.md:49` |
| R10 | F-04 | T10 | the 19 sites listed in J9 |

**Gate complete. Nothing built; no test, source or config file modified this turn. Awaiting human approval of the J10 list in `loop/AUTHORISED_TEST_EDITS.txt` before any build begins.**

---
---

# BUILD REPORT — Arc 5 (T1–T10 complete)

**Status:** all ten tasks built and committed, one commit per task, in order.
Nothing pushed. The gate above (J1–J10) is unchanged; this section is appended.

## Final test counts (observed this turn, not quoted)

```
uv run pytest -q          → 3205 passed, 73 skipped, 1 warning in 310.50s
cd frontend && npm test   → 25 files, 205 passed (vitest 4.1.10)
```

Baseline was `2966 / 200`. So: **2966 + 239 backend, 200 + 5 frontend.**

The backend run is the flags-OFF run — `tests/conftest.py` pins every flag off,
and it is green. The rulings are built unconditionally (gate finding F-F), with
R1's quote read gated by the pre-existing `QUOTE_SUBMIT_V1`.

## Commits

```
54b56e2 feat(arc5): T1 quote-priced orders (R1, F-07)
43c42b9 feat(arc5): T2 badge integrity (R2, F-11)
fa7f16b feat(arc5): T3 notation, clearance and seal designation (R3, F-12)
230704e feat(arc5): T4 confirm sufficiency and a labelled override (R4, F-15)
e316592 feat(arc5): T5 hygienic questions (R5, F-16)
066c933 feat(arc5): T6 variant guard recognises what it was told (R6, F-08)
7c97f2b feat(arc5): T7 loud failure without an enumeration oracle (R7, F-03)
981d49f feat(arc5): T8 supplier mail says what the RFQ is and leaks nothing (R8, F-09, F-10)
c185663 fix(arc5): T9 the frontend points at the backend by default (R9, F-02)
ae9e771 feat(arc5): T10 configurable data directory (R10, F-04)
```

## Finding → task → the named tests that close it

Every fixture below was copied from `eval/e2e-flags-on` evidence; no test in
this arc is built on invented data where evidence exists.

### F-07 → R1 → T1 — `utils/procurement_agent/tests/test_order_quote_pricing.py` (17 tests)

Fixture `fixtures/eval_s1_quote_order.json` ← `s1_step9_buyer_view.json`,
`s1_step10_accept_order.json`, `verify/verify_offline_checks.json`.

`test_s1_accepted_quote_prices_the_order_and_records_the_quote_id` ·
`test_the_quote_beats_a_listing_price_on_the_same_candidate` ·
`test_the_quote_beats_a_price_db_entry_for_the_same_part` ·
`test_withdrawn_quote_refuses_with_a_reason_and_places_nothing` ·
`test_expired_quote_refuses_and_does_not_fall_back_to_the_listing_price` ·
`test_a_quote_under_review_is_not_an_accepted_quote` ·
`test_no_quote_no_price_shows_unpriced_needs_a_quote` ·
`test_no_quote_with_a_listing_price_still_places_on_the_listing_price` ·
`test_flag_off_is_a_no_op` ·
`test_order_now_takes_the_quote_not_the_listing_price` ·
`test_order_now_prices_a_quote_only_candidate_instead_of_422` ·
`test_order_now_still_422s_a_priceless_candidate_with_no_quote` ·
`test_order_now_refuses_on_a_withdrawn_quote_rather_than_falling_back` ·
`test_a_quote_on_another_run_never_prices_this_one` ·
`test_an_unreadable_quote_store_refuses_rather_than_reporting_absence`

### F-11 → R2 → T2 — `test_badge_integrity.py` (27 tests)

Fixture `fixtures/eval_s2_badge_candidates.json` ← all 23 analysed rows of
`s2_step4_candidate_analysis.json` + the specs from `s2_step3_run_detail.json`.

`test_a_c3_less_listing_is_never_badged_exact_for_a_c3_request` (×3: Rodavictoria,
Intech, BDS) ·
`test_the_extractors_exact_match_cannot_raise_the_deterministic_verdict` (×3) ·
`test_every_bare_domain_row_in_the_evidence_is_denied_exact_grade` ·
`test_every_gated_badge_in_the_whole_evidence_set_carries_a_reason` ·
`test_the_classifier_is_the_ceiling` · `test_the_extractor_may_downgrade` ·
`test_the_extractor_may_not_upgrade` ·
`test_a_bare_domain_can_never_be_exact_even_on_a_perfect_string_match` ·
`test_a_resolvable_listing_keeps_a_genuine_exact_badge` ·
`test_is_exact_also_needs_the_extractors_own_exact_oem_claim` ·
`test_transform_option_denies_the_s2_overclaim` ·
`test_a_cache_replay_match_type_cannot_smuggle_a_badge_past_the_gate` (gate F-B)

### F-12 → R3 → T3 — `test_pn_notation.py` (37 tests)

Fixture `fixtures/eval_verify_s2_notation.json` ←
`verify/s2_step4_candidate_analysis.json`.

`test_every_separator_form_is_equal` (×5) ·
`test_and_reaches_the_badge_as_an_exact_grade_match` (×3) ·
`test_c3_requested_and_absent` ·
`test_a_different_clearance_is_also_a_mismatch_and_names_both` ·
`test_a_family_variant_needs_verification` (×4) ·
`test_it_is_never_exact_and_never_none` (×2) ·
`test_the_reason_names_both_designations` ·
`test_cross_maker_seal_equivalence_is_not_decided_here` ·
`test_a_correct_clearance_row_is_never_none` (JSB, 123Bearing, Motion) ·
`test_a_clearance_less_row_is_distinguishable_from_them` (EIS, PGN) ·
`test_the_two_cases_no_longer_collapse_to_the_same_verdict` ·
`test_a_grease_code_suffix_is_verified_not_claimed_exact` ·
`test_a_2rsh_row_previously_badged_normalized_is_lowered` ·
`test_a_no_match_extractor_cannot_turn_needs_verification_into_none` ·
`test_a_mechanical_seal_part_number_gets_no_notation_verdict`

### F-15 → R4 → T4 — `test_intake_sufficiency.py` (23 tests)

Fixture `fixtures/eval_s3_sufficiency.json` ← `s3_step2_confirm_attempt.json`,
`s3_step2_sourced_anyway.json`, `s3_step1_chat.json`, `s1_step9_buyer_view.json`.

`test_the_s3_specs_are_refused` · `test_the_s1_specs_clear_it` ·
`test_a_manufacturer_part_number_clears_it_without_a_model` ·
`test_a_half_identified_request_is_refused` (×6) ·
`test_null_tokens_are_not_identity` ·
`test_the_s3_specs_are_returned_to_clarification` ·
`test_the_run_stays_in_intake_and_sources_nothing` ·
`test_the_refusal_names_the_floor_not_the_family_guard` ·
`test_it_starts_sourcing` · `test_it_records_the_acknowledgement_on_the_run` ·
`test_the_run_is_marked_spec_incomplete` · `test_the_results_carry_the_banner` ·
`test_no_candidate_in_such_a_run_may_be_badged_exact` ·
`test_a_sufficient_request_is_never_marked_spec_incomplete` ·
`test_the_s1_path_still_confirms` · `test_a_fully_specified_request_confirms` ·
`test_the_floor_runs_after_the_family_guard_not_instead_of_it`

### F-16 → R5 → T5 — `test_hygienic_context.py` (42 tests)

Fixture: the same `eval_s3_sufficiency.json` (the S3 specs and the S3 user turn).

`test_the_s3_gauge_is_hygienic` ·
`test_the_same_gauge_without_the_context_is_not` ·
`test_r5s_whole_trigger_vocabulary` (×11 — CIP, SIP, sanitary, washdown, food,
dairy, beverage, pharma, 3-A, EHEDG, tri-clamp) ·
`test_a_substring_is_not_a_hygienic_signal` (×3) ·
`test_the_users_turn_text_counts_as_context` ·
`test_instruments_are_in_scope` (×4) · `test_fittings_are_in_scope` (×4) ·
`test_other_classes_are_not` (×3) · `test_a_hygienic_pump_gets_no_hygienic_block` ·
`test_the_required_fields_are_r5s_four` ·
`test_it_is_composed_from_the_labels_not_free_text` ·
`test_the_certification_question_offers_3a_ehedg_or_none` ·
`test_an_answered_field_is_not_re_asked` ·
`test_a_null_token_does_not_count_as_answered` ·
`test_the_cip_gauge_is_asked_all_four_before_confirm` ·
`test_the_run_does_not_reach_sourcing` ·
`test_the_same_gauge_with_no_hygienic_context_confirms` ·
`test_answering_the_four_lets_it_confirm` ·
`test_the_identity_floor_is_asked_first`

### F-08 → R6 → T6 — `test_variant_guard_provenance.py` (28 tests)

Fixture `fixtures/eval_s1_variant_guard.json` ← `s1_step9_buyer_view.json`
(message thread + specs), `s1_step1c_inapp_fallback.json` (the re-ask),
`s1_step1d_confirm_intake.json` (the 422).

`test_the_observed_422_named_a_field_the_specs_held` ·
`test_the_s1_turn_supplies_the_shaft_size` ·
`test_an_extractor_filled_value_with_no_user_turn_behind_it_is_not_supplied` ·
`test_notation_differences_in_the_users_wording_still_count` (×4) ·
`test_the_ledger_accumulates_across_turns_and_is_internal` ·
`test_a_supplied_attr_does_not_block_at_all` ·
`test_a_filled_but_unconfirmed_attr_still_blocks` ·
`test_but_it_is_no_longer_reported_as_missing` ·
`test_it_says_what_it_actually_needs_instead` ·
`test_a_genuinely_absent_attr_is_still_reported_missing` ·
`test_a_supplied_shaft_size_is_not_re_asked` ·
`test_an_extractor_invented_shaft_size_is_still_asked` ·
`test_a_mechanical_seal_is_never_reclassified_a_bearing` ·
`test_not_even_on_a_bore_diameter_alone` ·
`test_shaft_size_now_outranks_a_lone_bore_diameter` ·
`test_the_nema_frame_pump_to_motor_correction_is_untouched`

### F-03 → R7 → T7 — `test_auth_mail_loud_failure.py` (20 tests)

Fixture `fixtures/eval_f03_auth_mail.json` ← `verify/verify_offline_checks.json`
(`F-03.message_configuration_set`).

`test_ses_with_accounts_on_and_the_var_unset_refuses_to_boot` ·
`test_the_same_config_with_the_var_set_boots` ·
`test_the_fake_provider_does_not_require_it` ·
`test_accounts_off_does_not_require_it` ·
`test_notifications_off_does_not_require_it` ·
`test_it_captures_auth_mail_with_no_auth_configuration_set` ·
`test_the_provider_agnostic_helper_still_reports_the_refusal` ·
`test_ses_still_refuses` ·
`test_a_refused_auth_send_raises_one_action_now_alert` ·
`test_the_alert_names_the_missing_variable` ·
`test_it_is_deduped_across_a_burst` ·
`test_a_successful_auth_send_raises_nothing` ·
`test_an_alerting_failure_never_changes_the_send_result` ·
`test_send_succeeded_vs_send_refused_are_byte_identical` ·
`test_send_refused_vs_unknown_address_are_byte_identical` ·
`test_all_three_cases_agree` ·
`test_the_contrast_case_proves_the_comparison_has_teeth` ·
`test_the_alert_is_raised_but_never_reflected_in_the_response`

The equality is a true byte comparison of `(status_code, response.content,
headers)` with `Date` / `Content-Length` / `Server` excluded, and the contrast
case (a 422 on a malformed body) proves the three equalities are not vacuous.

### F-09, F-10 → R8 → T8 — `test_supplier_mail_content.py` (26 tests)

Fixture `fixtures/eval_s1_supplier_mail.json` ← `s1_outbox_final.json` (mail 5 =
the RFQ_NEW, mail 3 = the Tier-1 FYI) + `s1_step9_buyer_view.json` (the specs).

`test_the_observed_rfq_new_named_nothing` ·
`test_the_observed_tier1_fyi_leaked_a_run_uuid_and_internal_vocabulary` ·
`test_the_subject_names_the_part` ·
`test_the_body_names_part_manufacturer_quantity_and_the_portal_link` ·
`test_the_needed_by_date_rides_along_when_known` · `test_it_carries_no_price` ·
`test_the_run_identity_helper_reads_the_specs` ·
`test_it_only_ever_carries_the_allowed_keys` · `test_null_tokens_are_dropped` ·
`test_an_unreadable_run_is_fail_soft` ·
`test_a_multi_item_subject_gives_the_count` · `test_the_body_lists_every_item` ·
`test_a_one_item_batch_keeps_the_single_request_wording` ·
`test_the_subject_no_longer_carries_a_run_uuid` · `test_neither_does_the_body` ·
`test_the_internal_classification_vocabulary_is_gone` ·
`test_no_supplier_facing_template_renders_a_uuid` ·
`test_no_supplier_facing_template_renders_internal_vocabulary` ·
`test_the_uuid_matcher_would_catch_the_evidence`

The scan is a RENDERED scan, not a source grep: every supplier-facing template
(single RFQ, one-item batch, multi-item batch, single and consolidated reminder)
is built with hostile inputs — a `run_id` and a `sent_message_id` in the dict —
and the output is checked against a UUID pattern and the denylist.

### F-02 → R9 → T9 — `frontend/src/lib/__tests__/backend-url-default.test.ts` (5 tests)

`matches the port the backend actually serves on` (read out of `README.md`'s
uvicorn command, so the test fails if they drift again) ·
`is no longer the 8000 the evaluation observed` ·
`is an absolute http origin with no trailing slash` ·
`is documented in the frontend README` ·
`leaves no stale 8000 reference in the frontend README`

### F-04 → R10 → T10 — `test_data_dir.py` (19 tests)

Source: the 15 modules in `verify/verify_offline_checks.json`
(`F-04.modules_with_hardcoded_data_dir`) plus the 4 the gate's J9 added.

`test_it_defaults_to_the_repo_data_directory` · `test_the_env_var_wins` ·
`test_a_blank_value_is_not_an_override` ·
`test_the_utils_json_stores_do_not_move_when_it_is_unset` ·
`test_every_store_still_resolves_to_repo_data` ·
`test_persistence_still_resolves_to_repo_data` ·
`test_the_two_utils_json_stores_still_live_in_utils` ·
`test_conftest_computes_the_same_path` ·
`test_every_store_writes_under_it` · `test_including_persistence` ·
`test_including_the_two_utils_json_stores` ·
`test_nothing_is_left_pointing_at_the_repo` ·
`test_no_module_builds_the_data_path_itself` ·
`test_the_scan_would_catch_the_old_idiom`

The "with it set" cases run in a SUBPROCESS with a clean import graph, since a
process-level setting is read at import.

**Total new tests: 239 backend (17+27+37+23+42+28+20+26+19) + 5 frontend.**

## Test edits to pre-existing files

`git diff --name-status 0c49371..HEAD` shows `M` on exactly four pre-existing
test files, all four listed in `loop/AUTHORISED_TEST_EDITS.txt`:

| File | What changed | Ruling |
|---|---|---|
| `test_api_server.py` | Four `TestConfirmIntake` fixtures seeded `{"manufacturer": "Goulds"}` only; a `model` is added so each still exercises its own subject — **every assertion byte-identical**. `test_spec_described_no_model_unaffected` pinned the 200 that IS F-15: REPLACED with `422` + `reason == "identity_insufficient"` + `override == "source_anyway"` + the override reaching 200, and renamed. | R4 |
| `test_intake_variant_disambig.py` | `test_run_spec_described_then_confirm_200_unaffected` likewise REPLACED and renamed; the other invariant it carried (the refusal is the floor's, not the family guard's) is kept in its new form. | R4 |
| `test_run_capture_live.py` | The zero-results fixture gains a `model`; **all assertions unchanged**. | R4 |
| `test_tier1_runtime_live.py` | The shared `_run_sourcing` helper deliberately seeds a null PN token and no model — that IS the fixture — so it takes the explicit `source_anyway` override. **All 13 call sites' assertions unchanged.** | R4 |

**Two files on the authorised list were NOT edited, and did not need to be:**

- `test_scoring.py` — the gate marked `TestClassifyPnMatch` at-risk *"superseded
  only if R3's verdicts are added to that function's return set instead of a new
  badge-facing wrapper."* They were added to the wrapper. `_classify_pn_match`
  and `PN_MATCH_POINTS` are byte-unchanged.
- `test_notifications_coalescing.py` — the gate marked lines 137-143 at-risk
  *"superseded only if R8 changes the subject FORMAT rather than only supplying
  the part identity the caller currently drops."* Only the caller changed.

The two at-risk R6 hallucination-guard items (`test_api_server.py:1192-1201`,
`test_intake_variant_disambig.py:156-177`, `:523-540`) also survive unmodified,
because R6 was implemented as turn-text provenance exactly as the gate
recommended.

Five assertions in this arc's OWN `test_badge_integrity.py` (written in T2)
pinned `"none"` for the C3-less rows; T3 supersedes that with the more precise
`"mismatch"`. Each was replaced, not deleted, and the not-exact-grade invariant
kept alongside. That file is not pre-existing, so it is outside the fence.

## New environment configuration

| Variable | Default | Effect |
|---|---|---|
| `GOFER_DATA_DIR` | unset → `<repo>/data` | Relocates every store, `persistence.py`, and the two JSON stores under `utils/`. Unset, every path is byte-identical to today. Read at import, so set it before launching. |
| `SES_CONFIGURATION_SET_AUTH` | unset | **Now load-bearing at boot:** with `SUPPLIER_ACCOUNTS_V1` on under `MAIL_PROVIDER=ses`, an unset value refuses to start, naming the variable. Unchanged under `MAIL_PROVIDER=fake`. |
| `NEXT_PUBLIC_API_URL` | **changed:** `http://localhost:8000` → `http://localhost:8001` | The frontend now defaults to the port the backend serves on; a fresh clone needs no `.env.local`. |

No new feature flag was introduced (gate finding F-F).

New query parameter: `POST /api/runs/{id}/confirm-intake?source_anyway=true` —
the explicit, recorded override of the R4 identity floor and the R5 hygienic
question set.

New response fields: `pnMatchReason` on every candidate; `specIncomplete` and
`specIncompleteBanner` on a spec-incomplete run's `sourcing_results`;
`quote_id` on an order; `unconfirmed_attrs` / `unconfirmed_labels` on a
family-variant 422.

## FINDINGS

- **F-H (new, R6 scope).** The gate's recommended broad implementation of R6's
  units-override clause — "the units may refine a stated type, never contradict
  it" — **breaks a legitimate, pre-existing correction**: `test_intake_agent.py`
  `test_motor_units_hp_frame_override_pump` requires `"centrifugal pump"` +
  `hp` + NEMA `frame` → `Electric Motor`, which that rule forbids. That file is
  NOT on the authorised list, and the override is correct on its merits (a NEMA
  frame is decisive evidence the thing is a motor). The fix was therefore
  narrowed to the actual defect: `shaft_size` now outranks a lone
  `bore_diameter` (they were both priority 6, so list order decided), plus one
  targeted rule — a bore diameter alone never reclassifies a part already named
  a seal, because on a cartridge seal the shaft size and the bore are the same
  dimension, which is why the extractor fills both. Both observed seal cases
  are closed and every other override is untouched. **No blocked test edit —
  the narrower fix needed none.**
- **F-A (gate finding, still open).** `ranking_bands.classify_pn_evidence(
  "6205-2RS C3", "6205-2RS")` returns `canonical`, and `assign_band`
  (`ranking_bands.py:392`) lets a bare `pn_match_status in ("exact_match",
  "partial_match")` lift a row to Band B. R2 and R3 correct the **badge**; a
  C3-less bearing can still be **ranked** as a confirmed part. Band assignment
  is outside R2/R3 as written and outside this arc's task list. Deliberately
  left open, and worth an arc of its own.
- **F-I (new, R4 scope boundary — gate F-G, restated as shipped state).** The
  identity floor is applied at the `confirm_intake` call site only, not inside
  `_commit_intake_to_sourcing`. The channel-agnostic email intake consumer
  shares that helper and runs its own family pre-gate; adding the floor there
  would deepen F-06 (email intake dead-ending on an under-specified request),
  which the brief puts out of scope. So **an email-channel request can still
  reach sourcing without clearing the R4 floor.**
- **F-J (new, R1 residual).** `/review-items/{id}/place-order` (the email-quote
  path, `api_server.py:5099-5117`) prices from `payload.unit_price` and was not
  touched: it has no candidate and no `source_url`, so there is no
  `(run_id, domain)` key for `order_quote.resolve_for_order` to join on. It
  cannot substitute a *listing* price for a quote — its price IS a quote — so
  R1's prohibition is not violated, but it is the one order path that does not
  consult `quote_store` and therefore cannot notice a withdrawn or expired one.
- **F-K (new, R2 residual).** The badge gate needs the run's specs, so
  `_transform_option` called with `specs=None` (the default, kept for
  back-compat) classifies everything as `none`. The single production caller
  passes them. A future caller that forgets loses badges rather than
  over-claiming — the safe direction, but worth knowing.
- **Test-edit fence:** no blocked edit to record. Every edit this arc needed was
  to a file on the authorised list.

## Out of scope, as briefed

Email intake statefulness (F-06), known-sender registration (F-05), clearance as
a comparison-schema field and substitute-intent handling (F-13, F-14),
cross-maker seal equivalence, hygienic equivalence logic, infra provisioning.

`design/interactions.md` was updated on this branch with the buyer- and
supplier-facing behaviour changes.

**Not pushed. Ready for review.**

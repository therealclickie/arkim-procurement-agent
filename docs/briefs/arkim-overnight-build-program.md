# Arkim — Overnight Build Program: Data Flywheel + Tier 1 Onboarding

**How to use this:** each NIGHT below is a self-contained unmanned session (GLM `--dangerously-skip-permissions`, the proven overnight pattern). Evening: pre-flight → paste the night's brief → sleep. Morning: read `MORNING_REPORT.md` → run the morning verification checklist → label/decide → queue the next night. Nights run in order (each builds on the last); one night per session — do NOT combine.

**Executor:** GLM via LiteLLM proxy (proven overnight on this repo; Cowork's sandbox cannot write to it). Proxy must stay up all night.

---

## STANDING GUARDRAILS (every night — paste into every kickoff)

1. **Branch fence:** first action `git checkout -b <night-branch>` from the agreed base (set per night at kickoff; pre-flight asserts base branch + HEAD hash). NEVER commit to the deploy branch or main. NEVER push.
2. **Single committer:** if `git log` shows a commit you didn't make → STOP, log, halt.
3. **Flag-gated inertness:** every night's behavior sits behind its named flag (per night below), default OFF, strict `_env_truthy` parse. Flag-off = byte-identical behavior, proven by a dedicated inertness test against fixtures captured from the pre-build code (the T6 pattern: extract baseline behavior from the base commit, freeze outputs, assert the flag-off path reproduces them).
4. **Mocks in pytest; no live network in tests.** Live LLM/fetch calls only inside an explicitly capped eval harness if the night defines one. If keys are absent, skip and log.
5. **Do-not-touch:** `.env`, `audit/`, `scripts/*_self_test.py`, seed/demo fixture files, `known_parts.json`, `price_db.json` (read-only), DEMO_MODE gates, the security/allowlist surface, the deploy branch.
6. **Never weaken a test, never relabel an eval case.** A failing case means fix the code. Known-gaps get labeled with the desired verdict and reported as known-gap (the sub-type precedent).
7. **Live-faithfulness rule (today's lesson, twice):** any eval/test of a classifier, matcher, or scorer must feed it EXACTLY what the live call path feeds it (same fields, same absences). Before writing such a test, read the live call site and mirror its inputs. A test that passes a field the live path doesn't pass is invalid — this exact trap shipped two bugs.
8. **Investigation-gate rule (unmanned adaptation of investigate-first):** each night opens with an INVESTIGATION phase whose findings are written to the report. Each finding has an EXPECTED result stated in the brief. If findings match → proceed. If a finding CONTRADICTS a stated assumption → do not improvise around it: log the contradiction, skip the dependent tasks, continue with independent ones (guardrail-7 style). A skipped-with-diagnosis task is a good outcome; built-on-a-wrong-assumption is not.
9. **Iteration cap 5 per failing task** → revert task, log, move on. Commit per task, Conventional Commits, suite green at every commit. Record the pytest baseline first.
10. **Final act every night:** `MORNING_REPORT.md` at repo root — per-task status + hashes, investigation findings vs expectations, every unspecified decision, every blocker with diagnosis, and the NEEDS-LIVE-VERIFICATION list (the morning checklist inputs).

**Evening pre-flight (Tom, every night):** repo clean (`git status`), correct base branch + expected HEAD, kill all orphan python/node processes, cache files present-or-absent as the night expects, keys in `.env` as the night requires, proxy up (Terminal A), single GLM terminal (Terminal B), brief file in repo root, paste kickoff, watch the pre-flight turn go green, sleep.

---

# NIGHT 1 — RUN CAPTURE + OUTCOME SIGNALS (flywheel foundation)

**Flag:** `RUN_CAPTURE` · **Mission:** every procurement run produces a complete, queryable capture record (inputs, queries, candidates, scores, verdicts, user actions) in a first-party store, fail-soft but alert-visible. This is the flywheel's raw material; every uncaptured run is training data lost.

## Investigation phase (report before building)
- **I1. Persistence inventory:** enumerate every store written during a run today (the runs store behind POST /api/runs — schema/lifecycle; price_db; supplier_registry; orders; sent_messages; review_items; any message/turn persistence). For each: schema, keys, what survives the run. *Expected: user turns and per-candidate scores/verdicts are NOT durably persisted (console prints only) — the gap this night fills. If they ARE persisted somewhere, report where; the build then extends rather than duplicates.*
- **I2. Hook points:** locate the exact seams for capture: (a) intake turn boundary (where user input arrives + agent responds — likely the /messages handler), (b) sourcing collect/score/reject points (the code that today emits the `[Sourcing]` print lines — each print is a capture hook candidate), (c) result assembly (what was displayed vs rejected), (d) any frontend→backend action events (order click, report click). *Expected: seams exist at the print-log sites; if scoring/rejection happens in a place with no run_id in scope, report it — capture needs run_id threading.*
- **I3. PII path:** where does input PII redaction run today? Can captured raw turns pass through it at write time, or must capture store pre-redaction text (eval fidelity) with redaction at export? *Report the tension with file:line; default decision if unclear: store post-redaction (safe), flag the fidelity cost.*
- **I4. Async write option:** cheapest fail-soft-but-visible write in this FastAPI setup (background task vs direct try/except + failure counter exposed on /api/health). *Expected: a simple try/except + counter is sufficient at demo volume; report if something better is trivially available.*

## Build tasks (test-first, flag-gated)
- **T1. Schema:** `run_events` append-only table (own sqlite, e.g. `data/run_capture.sqlite`): `event_id, run_id, ts, source_tag (demo_prospect|customer:<tenant>|internal_test), event_type (turn_user|turn_agent|intake_result|query_issued|candidate_scored|candidate_rejected|results_displayed|user_action|outcome), payload JSON`. Plus a `run_outcomes` view/table computing per-run outcome status.
- **T2. Capture hooks** at the I2 seams, all behind `RUN_CAPTURE`: turn capture, intake result, per-tier queries, every candidate with score+gate verdict+rejection reason, displayed set, user actions (wire what exists; log which actions have no event source yet as a report item).
- **T3. Outcome computation:** implicit signals — completed_with_action / abandoned_after_results / rephrased (same session new run with similar input) / zero_results / all_rejected. Computed post-run or on read.
- **T4. Fail-soft + visibility:** every capture write wrapped; failures increment a counter surfaced via /api/health (`capture_failures: N`) — never breaks a run, never silently dies.
- **T5. Inertness wall:** flag-off = zero writes, zero new codepath effects, byte-identical API responses (baseline-fixture pattern).

## Overnight testing (pytest must prove)
- Every event type writes and reads back with correct shape (mocked runs).
- A full simulated run (mocked LLM/search) produces the complete expected event sequence.
- Outcome computation correct for each signal type (fixture runs per outcome).
- Fail-soft: a forced write failure doesn't raise into the request path AND increments the counter.
- Flag-off inertness: no table writes, responses byte-identical.
- Live-faithfulness: hooks tested via the REAL handler path (TestClient through /api/runs + /messages), not by calling capture functions directly.

## Success criteria
- A flag-on run through the real API produces a complete capture record; flag-off is proven inert; capture failure is visible on /api/health; suite green.

## Morning verification (Tom, ~15 min)
1. Flag-on backend, run 3 real parts through the UI (one clean PN, one component, one vague).
2. Inspect `run_capture.sqlite` (report includes a read snippet): confirm turns, queries, candidates+scores, displayed set are all there and match what you saw on screen.
3. Abandon a run mid-way; confirm the outcome computes as abandoned.
4. `/api/health` shows `capture_failures: 0`.
5. Flag-off restart → confirm no new rows written.
**Iterate trigger:** any missing event type or score/verdict mismatch between screen and capture → that's the day's fix before Night 2.

---

# NIGHT 2 — LABELING SURFACE + EVAL EXPORT (turning capture into the wheel)

**Flag:** `RUN_CAPTURE` (extends Night 1; labeling endpoints additionally admin-gated) · **Mission:** captured runs become labeled eval cases in <1 minute of human time, exported in the existing eval schemas. **Depends on Night 1 verified.**

## Investigation phase
- **I1. Admin inspector inventory:** what the role-gated inspector already renders (runs? candidates? verdicts?), its auth pattern (`ARKIM_ADMIN_TOKEN` bearer), and where a labeling view slots in. *Expected: read-only views exist to extend; auth is server-enforced — reuse exactly that pattern for label-write endpoints.*
- **I2. Eval schema contract:** read the existing eval dataset schemas (intake eval JSON; scoring eval JSON; detection eval) — exact fields, so exported cases are drop-in. *Expected: schemas exist from the prior builds; report exact required fields per schema and where provenance (`real:<run_id>`) can attach without breaking their validators.*
- **I3. Queue source:** confirm Night 1's outcome statuses can drive a "failures-first" queue ordering. 

## Build tasks
- **T1. Label store:** `run_labels` (run_id/candidate ref, label fields: intake_correct + corrections; per-candidate right_part_type + should_pass_floor; free-text note; labeled_by; ts).
- **T2. Admin labeling endpoints** (server-enforced admin auth, the inspector pattern): GET queue (failures-first), POST label, POST export.
- **T3. Labeling UI:** minimal inspector extension — run's input/intake/results with score+verdict per candidate, one-click label controls, keyboard-friendly. Function over polish (polish is morning work).
- **T4. Exporter:** labeled case → the matching eval schema (intake/scoring/detection) with `provenance: real:<run_id>`, appended to a real-cases dataset file; existing schema validators must pass on exported cases; dev/holdout assignment on export (e.g. hash-based 75/25).
- **T5. Provenance metric:** a small report function: % real vs synthetic per eval suite (the flywheel KPI).

## Overnight testing
- Auth: label/export endpoints 401/403/503 semantics identical to existing admin endpoints (reuse those tests' pattern).
- Round-trip: fixture captured run → label → export → the EXISTING eval validator accepts the case; the case's fields mirror what the live path feeds (live-faithfulness rule — export must reproduce live inputs, e.g. no clean title if live passes none).
- Queue ordering: failure outcomes first.
- Read-only guarantee: GETs mutate nothing.

## Success criteria
- End-to-end in tests: captured → labeled → exported → validates in the real eval schema with provenance. Admin-gated writes. Suite green.

## Morning verification (~20 min)
1. Label 5 real captured runs from Night 1's traffic through the UI — time yourself; the <1-minute claim is the criterion.
2. Export them; run the eval harness including the real-cases file — it loads and runs.
3. Check one exported case by eye against its captured run: inputs faithful, labels correct.
**Iterate trigger:** labeling friction >1 min/case → UI fix list for the day. This is also where Q-A2 (who labels) gets answered by experience.

---

# NIGHT 3 — SUPPLIER RECORD & TIER 1 REGISTRY (the settled scope model as schema)

**Flag:** `TIER1_V2` · **Mission:** the supplier entity per the research's settled model — `brands × classes × territory`, tri-state authorization, lifecycle — as a durable registry with a clean read/write API. Schema + logic night (no UI, no live sourcing change yet).

## Investigation phase
- **I1. supplier_registry today:** full schema, lifecycle fields, every read/write call site (the Apollo cache + reachability). *Expected: domain-keyed Apollo-enrichment cache with staleness fields; the new model EXTENDS it (same store, new tables/columns) rather than replacing — report if extension is awkward and a parallel table set is cleaner.*
- **I2. Taxonomy seed:** read `part_type_classes.py` (the scorer's noun-class dictionary) — current classes + synonyms. *Expected: ~10 classes; the supplier taxonomy target is ~25-40; report the current set and which research-named classes are missing (gasket/packing split, hose, filter, sensor/instrument, gearbox, conveyor components...).*
- **I3. Lifecycle collision check:** does anything today assume supplier_registry rows are only Apollo-cache entries (staleness auto-refresh, re-enrichment jobs)? *Expected: the graduation rule (onboarded exits Apollo refresh) needs a status check added at the refresh site(s) — locate them.*
- **I4. UNSPSC crosswalk shape:** confirm nothing exists; plan a static mapping file (class → UNSPSC code, pinned release) per the research.

## Build tasks
- **T1. Taxonomy expansion (shared asset — touches the scorer's dictionary):** grow `part_type_classes.py` toward the research's ~25-40 classes with synonyms + slug tokens + UNSPSC crosswalk per class. CAUTION: this dictionary is live in SCORING_V2 detection — every addition must keep the existing detection tests green (additions only; no renames of existing classes). The scorer and the supplier model now share one vocabulary by construction.
- **T2. Supplier scope schema** per the research's record: `classes[] {class_id, subtype?, unspsc, is_core, confidence, source}`, `brands[] {brand_id, relationship AUTHORIZED|CARRIES|AFTERMARKET_COMPATIBLE, authorized_territory?, classes_for_brand?, evidence, confidence}`, `ship_area {NATIONWIDE_US | states[]}`, `local_service_area[] {branch_zip, radius, services[]}`, `verticals[]`, `performance {}`, `status lifecycle discovered→contacted→quoted→onboarding→onboarded→suspended`, provenance.
- **T3. Registry API (internal functions):** create/update supplier scope, status transitions (enforced state machine — the orders pattern), graduation rule (onboarded ⇒ excluded from Apollo staleness refresh — wired at the I3 sites), lookup primitives: by class, by brand+relationship, by territory-fit.
- **T4. Migration/coexistence:** existing Apollo-cache rows remain valid `discovered/contacted`-stage records; nothing breaks in the Tier 3 clarifier path (its tests stay green untouched).
- **T5. Inertness:** flag-off = registry extensions dormant, Tier 3 clarifier byte-identical.

## Overnight testing
- State machine: legal transitions pass, illegal rejected (skip/backward/un-suspend), onboarded exits refresh (assert the refresh query excludes them).
- Scope CRUD round-trips; tri-state brand relationships; territory fields.
- Lookup primitives: class lookup respects is_core; brand lookup respects relationship filter; territory-fit returns rank data not hard exclusion (except local_service).
- Dictionary expansion: ALL existing SCORING_V2 detection tests still green (the shared-asset guard); new classes classify.
- Tier 3 clarifier regression: its existing suite untouched and green.

## Success criteria
- The research's schema exists, enforced lifecycle, graduation rule live, one shared taxonomy with the scorer proven by both suites green. No live sourcing behavior changed.

## Morning verification (~10 min)
1. Report's schema dump matches the research model (brands tri-state, territory split, lifecycle).
2. Spot-create a supplier via the report's snippet (e.g. a real seal shop you know), walk it discovered→onboarded, confirm it exits the refresh set.
3. Confirm scoring detection tests count unchanged-or-up.
**Decision this morning unblocks:** Q-B3 (registry placement) — take the schema to Sergei; the build used the procurement-local store, portable later.

---

# NIGHT 4 — THE ONBOARDING AGENT (extraction → prepopulate → review/approve)

**Flag:** `TIER1_V2` (+ endpoints admin/token-gated) · **Mission:** URL in → extracted, confidence-scored, prepopulated supplier profile out → review/approve flow writes an `onboarded` supplier. **The deterministic core is overnight work; UI polish is morning work.** **Depends on Night 3.**

## Investigation phase
- **I1. Fetch capability:** what HTML-fetch machinery exists in-repo (the Tavily client fetches? requests available?) for retrieving supplier pages — and what the demo security posture requires (this runs server-side, admin-triggered; confirm no SSRF exposure via the allowlist surface). *Expected: simple requests-based fetch is fine for an admin-gated internal flow; report constraints.*
- **I2. Line-card reconnaissance (the fixture set):** fetch and save AS TEST FIXTURES the real HTML of 5-8 actual supplier sites' relevant pages (a line-card distributor, an aftermarket seal shop, a bearing house, an instruments distributor, one messy small-shop site). These fixtures are the eval — extraction is tested against REAL pages offline, never live-fetched in pytest. *(This is the one investigation step that uses live network — cap it at ~10 fetches, save to tests/fixtures/supplier_sites/.)*
- **I3. Extraction LLM seam:** which model/call pattern the repo uses for extraction tasks (the intake extraction pattern) — reuse it; note cost per extraction (a page set is ~3-10 LLM calls).
- **I4. Magic-link/token pattern:** does any tokenized-link flow exist to reuse for the supplier review link, or is v1 concierge-only (admin drives the review)? *Expected: nothing exists → v1 = concierge mode (admin UI drives review/approve); the supplier-facing magic link is a flagged follow-on. Report and proceed concierge-first.*

## Build tasks
- **T1. Page harvester:** given a URL → fetch home + discover candidate pages (line-card/brands, about, locations, products) via link-text heuristics → bounded set (≤8 pages), DOM-pruned text per the research.
- **T2. Extraction pipeline (LLM, the repo's pattern):** pages → structured scope draft: brands[] with relationship guess + evidence quote + confidence; classes[] (classified via the shared dictionary) + confidence; locations/ship-area guess; verticals. Every field carries {value, confidence, evidence, source_url}.
- **T3. Prepopulate + confirmation contract:** draft → a profile object marking which fields are PRE-CHECKED (high-confidence brands/classes) vs MUST-CONFIRM (authorization tri-state, ship/local service area, aftermarket disclosure — ALWAYS must-confirm per the research, regardless of confidence).
- **T4. Review/approve flow (concierge v1):** admin endpoints + minimal inspector UI: load draft → edit/confirm → approve ⇒ writes the Night 3 supplier record, status `onboarding→onboarded`. Nothing writes to the registry without the approve action (asserted).
- **T5. The extraction eval:** run the pipeline against the I2 fixtures; hand-label the expected scope per fixture site (brands present on the real line card, true classes); score extraction accuracy per field type. Report per-site precision/recall on brands and classes. *(Live-faithful by construction — real pages.)*

## Overnight testing
- Harvester: page discovery on fixtures (finds the line-card page), bounded, no live fetch in tests.
- Extraction: against fixtures with mocked-or-capped LLM — if live Haiku calls are permitted for the eval, cap ≤60 calls total; else mock and mark the eval as morning work.
- Confirmation contract: the three load-bearing fields are must-confirm in every draft regardless of confidence (asserted).
- Approve gate: no registry write without approve; approve produces a valid Night-3 record; double-approve idempotent.
- Flag-off inert; endpoints auth-gated (the admin pattern's 401/403/503 semantics).

## Success criteria
- Fixture eval: brands extraction ≥ ~80% precision on line-card sites (the research's benchmark), classes reasonable, every draft enforces the must-confirm trio; approve-gated writes; suite green.

## Morning verification (~30 min)
1. Run the agent on 2-3 REAL supplier URLs not in the fixtures (live, admin-gated). Review the drafts: is the line card captured? Are confidences honest?
2. Concierge-onboard ONE real supplier end-to-end (ideally a relationship candidate — Q-B2): review, correct, approve → confirm it lands `onboarded` in the registry.
3. Judge the review UX: what would a supplier stumble on? (Feeds the magic-link follow-on.)
**This morning can produce your first real onboarded supplier.**

---

# NIGHT 5 — TIER 1 RUNTIME: MATCHING + NOTIFY≫DISPLAY (closing the loop)

**Flag:** `TIER1_V2` · **Mission:** onboarded suppliers appear in live sourcing (honest relationship-backed cards, no fabricated prices) and the notification path exists with the research's conservative asymmetry. **Depends on Nights 3-4; ideally ≥1 real onboarded supplier from the Night 4 morning.**

## Investigation phase
- **I1. Tier 1 path today:** exact post-purge `_run_tier1` behavior and every downstream consumer of tier_1 results (api_server assembly, frontend card rendering, `known_parts` caching). *Expected: honest-empty; the candidate shape the frontend needs (fields, price_tbd/is_mock handling) — report the exact contract a registry-backed candidate must satisfy.*
- **I2. Request-side match inputs:** at the point Tier 1 runs, what does the request carry — detected_type/class (the shared dictionary), manufacturer/brand, buyer location (does a tenant/site location exist in-context?). *Expected: class + manufacturer available; buyer location may be absent → territory ranking degrades gracefully to neutral; report.*
- **I3. Notification seam:** where a matched-request→notify hook would live (post-sourcing assembly?), and what send machinery exists (the stubbed EmailSender — notifications reuse it behind the same stubs/flags; NO live sends).
- **I4. Cache interaction:** how Tier 1 results interact with `known_parts`/price caching — onboarded-supplier cards must not get cached into staleness the way the poison bug did. *Expected: registry-backed results should be computed fresh per run (cheap — local lookup), bypassing the price-cache write path; confirm feasibility.*

## Build tasks
- **T1. The matcher** (the research's function): hard gate = class match (shared dictionary, request detected_type vs supplier classes); amplifier = brand (request manufacturer vs supplier brands, AUTHORIZED > CARRIES > AFTERMARKET_COMPATIBLE); ranking = territory fit + is_core + performance; local_service = the only geographic hard filter. Returns scored matches with match-explanation metadata.
- **T2. Tier 1 candidates from matches:** honest card data — supplier identity, relationship badge (authorized/carries/aftermarket — aftermarket carries the disclosure flag), confirmed prior quote (price_db `source="rfq"`, dated) if any, else quote-expected framing from response history. NO price fabrication (the purge guard tests must stay green). Satisfies the I1 frontend contract. Fresh-per-run (no cache write, per I4).
- **T3. Notify≫display asymmetry:** two thresholds + per-RFQ notification cap (5-8) + class-gate on notify; notification EVENTS recorded (`supplier_notifications` table) with the send itself behind the existing stubbed/flagged EmailSender (nothing sends live). Display threshold lower, notify threshold requires brand-match-or-core-class.
- **T4. Aftermarket disclosure:** the AFTERMARKET_COMPATIBLE badge/disclosure text reaches the candidate payload (frontend rendering is morning work; the data must be there).
- **T5. Inertness + regression:** flag-off = Tier 1 stays honest-empty byte-identical; SCORING_V2 and purge-guard suites untouched green.

## Overnight testing
- Matcher truth table: class-gate excludes wrong-class always (even brand-matched); brand tri-state ordering; territory ranks-not-filters except local_service; no-buyer-location degrades to neutral.
- The Goulds anchor: request class SEAL + manufacturer Goulds vs a registry with (a) an authorized Goulds distributor, (b) an aftermarket fits-Goulds seal shop, (c) an onboarded PUMP-only supplier → a & b match (a ranks first, b carries disclosure), c excluded by class-gate.
- Honesty: no candidate ever carries a price without a dated confirmed price_db entry (property test across fixtures); purge guards green.
- Notification: cap enforced, class-gate on notify enforced, events recorded, zero live sends possible (the send layer's double-gate asserted, reusing its tests' pattern).
- Live-faithfulness: matcher tested through the real `_run_tier1` path with a fixture registry, via the API (TestClient), not by calling the matcher directly only.

## Success criteria
- Flag-on: a request matching an onboarded supplier renders an honest Tier 1 card through the real API; wrong-class never matches; notify events respect cap+gate; zero fabrication; flag-off inert; all prior suites green.

## Morning verification (~30 min)
1. Flag-on backend + your Night-4 onboarded supplier in the registry: run a matching request through the UI → the Tier 1 card appears, honest, correctly badged.
2. Run a wrong-class request → no Tier 1 card (the gate).
3. Inspect `supplier_notifications` → the event recorded, nothing sent.
4. If a confirmed quote exists for the supplier, verify the dated-price display; else the quote-expected framing.
**This morning = the Tier 1 loop's first visible turn.** The remaining gap to a CLOSED loop (fast-RFQ → reply → confirmed quote → orderable) is the Stage-1 Gmail live-wiring — a deliberate daytime task, never unmanned.

---

## THE MORNING ITERATE LOOP (every day of the program)

1. Read `MORNING_REPORT.md` (attach here as a file — we triage together).
2. Run the night's morning-verification checklist (above, per night).
3. File the day's fixes: anything the checklist catches is fixed in a SUPERVISED daytime session (the overnight sessions build; the supervised sessions correct — don't queue fixes into the next unmanned night).
4. Label 10-15 captured runs (once Night 2 lands) — the flywheel habit, ~15 min.
5. Queue the next night only when the current night's checklist is clean.

## SEQUENCING & PARALLEL TRACKS
- Nights 1→2 (flywheel) and 3→4→5 (Tier 1) are two tracks; 1-2 can interleave with 3-5 (e.g. N1, N3, N2, N4, N5) since they touch different code — but never two nights in one session, and Night 5 wants a real supplier from Night 4's morning.
- **Base branch decision before Night 1:** these build on the post-launch line (the integration branch or its successor after the detection fix lands and the flag strategy is settled). Set explicitly at each kickoff; pre-flight asserts it.
- **Not in this program (deliberate):** Gmail live wiring (daytime, HITL), the supplier magic-link/self-serve UI (follow-on after concierge proves the flow), Stage 3 dashboard (unlocks at ~5 suppliers), Stage 4 API integration (own brief later), any deploy.

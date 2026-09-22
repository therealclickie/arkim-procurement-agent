# OVERNIGHT BUILD BRIEF — Arkim Intake Redesign, Phases 1 + 2 (plumbing)

**Executor:** Claude Cowork (managing the build autonomously overnight). This brief is self-contained: mission, hard guardrails, task sequence with test-first acceptance criteria, the distilled per-type registry seed, and the morning-report contract. Follow it exactly. Where this brief and improvisation conflict, this brief wins.

**Repo:** `C:\Users\tom\Downloads\_Arkim\Arkim Procurement Agent Prototype` (path has spaces — always quote). Python backend `api_server.py` + agents under `utils/procurement_agent/`; Next.js frontend under `frontend/`. Tests: `uv run pytest -q` (backend; currently ~1113 green). Frontend has NO test harness — `npm run type-check` + `npm run build` only.

---

## 0. HARD GUARDRAILS (non-negotiable, checked before every commit)

1. **Branch fence.** First action: `git checkout -b feature/intake-redesign-overnight` from current HEAD. ALL work on this branch. NEVER commit to `feature/phase3-comparison-approval` (it carries an unpushed deploy stack). NEVER push any branch. NEVER touch `main`.
2. **Single committer.** You are the only process committing tonight. If `git log` ever shows a commit you didn't make, STOP all git operations, write the anomaly to the morning report, and halt.
3. **Feature flag: everything inert by default.** All new behavior gates behind env flag `INTAKE_TYPE_AWARE` (strict truthy parse: `1/true/yes/on`, matching the codebase's `_env_truthy` convention). Flag OFF = byte-identical current behavior, proven by regression tests (see T6). The demo must be unaffected by this branch even if merged.
4. **Mocks in pytest; live calls ONLY inside the eval harness (T7–T9).** The pytest suite stays fully mocked (the suite's existing pattern). Live Anthropic calls are permitted EXCLUSIVELY within the LangSmith eval loop of T7–T9, under its caps: Haiku-class model, temperature 0, calling the classifier/extractor functions in ISOLATION only — never trigger sourcing, never call Tavily, never route through any proxy (real api.anthropic.com via the codebase's requests.post pattern; never read ANTHROPIC_BASE_URL). If the LangSmith or Anthropic keys are absent from the environment, skip T7–T9 gracefully and log it in the morning report — do not improvise credentials.
5. **Do-not-touch list:** `data/mock_tier1_suppliers.json`, `data/mock_maintenance_handoffs.json`, `utils/known_parts.json` (a purge workstream is in flight — collision risk), `.env`, `audit/`, `scripts/*_self_test.py`, all DEMO_MODE gates and the api_server allowlist/security middleware, the SpecComparisonAgent base_url pin. Read them if needed; never modify.
6. **Never weaken the suite.** No deleting/skipping/loosening existing tests to get green. If an existing test genuinely conflicts with new gated behavior, the new behavior is wrong (it should be inert when the flag is off) — fix the behavior, not the test. The same integrity rule applies to the eval harness: never delete, relabel, or cherry-pick dataset examples to raise a score — fix the prompt; if an example looks genuinely mislabeled, flag it in the morning report instead of editing it.
7. **Iteration cap.** Max 5 fix attempts per failing task. On the 5th failure: revert that task's changes (`git checkout -- <files>` or drop the commit), log the blocker + your diagnosis to the morning report, move to the next independent task.
8. **Commit discipline.** One Conventional-Commits commit per task (T1, T2, …), suite green at every commit. Baseline first: run `uv run pytest -q` before any change and record the count in the report.
9. **Stop condition.** When T1–T7 are done (or blocked-and-logged), STOP. Do not invent additional scope. Do not start Phase 3, RAG, LangSmith, frontend redesigns, or "improvements" outside this brief.

---

## 1. MISSION

Build the **structural spine of the intake redesign** — part-type classifier, per-type question registry, quantity capture, component-of resolution plumbing, type-aware question selection — fully gated behind `INTAKE_TYPE_AWARE`, verified by mock-driven tests. The live-LLM behaviors (does the real extractor preserve "mechanical seal"? does the classifier classify real inputs correctly?) are **explicitly out of scope tonight** and belong to the morning verification checklist you will produce.

Current intake internals you are extending (from the bounded fix, commit 9a76b51 — read `utils/procurement_agent/intake_agent.py` first):
- `_DEFAULT_QUESTIONS` incl. `part_identity` opener; `_asked_fields` / `_intake_turns` internal `_`-prefixed keys riding on `asset_specs_json`; `INTAKE_TURN_CAP = 3`; `assess_proceed_state` with states incl. `proceed_spec_based` and `forced_commit`; `CATEGORY_REQUIRED_FIELDS`; `_build_context_summary` filters `_`-prefixed keys (any new internal keys must also be `_`-prefixed).
- Frontend gate: `specsReady` in `frontend/src/components/proc/request-screen.tsx` honors `spec_based_sourcing`.

---

## 2. TASK SEQUENCE (test-first: write the acceptance tests, watch them fail, then implement)

### T0 — Setup + baseline
FIRST: verify you are in the procurement repo — the working directory must contain `api_server.py` and `utils/procurement_agent/`. (Two prior investigation sessions were accidentally launched in arkim.core and a messaging clone. If this check fails, STOP and report — do not operate on whatever repo you happen to be in.) Then branch per guardrail 1. Run `uv run pytest -q`, record exact pass count. Verify the eval-harness credentials are available **in the environment or in `.env`** (the repo's standard provisioning — check via load_dotenv then os.environ): `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY` (messaging's only LangSmith env var — confirmed by investigation), and `ENVIRONMENT=dev` (messaging derives project names and tags from it). If keys are absent, note that T7–T9 will be skipped and proceed with T1–T6 only. Create `MORNING_REPORT.md` at repo root with a header and the baseline. Commit: `chore(intake): overnight branch baseline + morning report scaffold`.

### T1 — The per-type registry (data + module)
Create `utils/procurement_agent/part_type_registry.py`: a typed structure (dataclass or TypedDict) per part type with fields: `part_type`, `regime` (`DIRECT|ANCHORED`), `sourcing` (`STANDARD|OEM|MIXED`), `configurable` (bool), `identity_anchor_question` (str), `q2_template` (str — the batched highest-entropy blocking question), `blocking_attrs` (list), `refinement_attrs` (list), `inference_rules` (dict: context_token → {attr: proposed_value}), `nameplate_guidance` (str), plus `UNKNOWN` sentinel. Populate the **4 priority types from the DISTILLED REGISTRY SEED in §4 of this brief** (source of truth: the parts-matching research; if `docs/parts_matching_research.md` exists in the repo, cross-check against it — do not invent attributes not in the seed/doc).
**Acceptance tests (new file `tests/test_part_type_registry.py`):** registry loads; all 4 types present with non-empty blocking_attrs and q2_template; every type's blocking/refinement sets are disjoint; `get_profile("unknown_gibberish")` returns the UNKNOWN sentinel; registry is pure data (importing it makes no network/LLM calls).
Commit: `feat(intake): per-type question registry (4 priority types, data-only)`.

### T2 — The classifier (function + schema, mocked)
Add `classify_part_type(first_message: str, llm_call=<injectable>) -> Classification` returning `{part_type, regime, sourcing, component_of: str|None, confidence}` with a **constrained output contract**: the LLM is prompted to return strict JSON limited to the registry's known types + UNKNOWN; the parser validates and falls back to UNKNOWN on any parse/validation failure. `component_of` captures parent-machine identity when the message names a component of a machine (design for F1: "Goulds 3196 mechanical seal" → `part_type=mechanical_seal, component_of="Goulds 3196"`). The `llm_call` is injectable so tests mock it; production wiring uses the same raw `requests.post` pattern as the other agents (api.anthropic.com — NOT the SDK, NOT ANTHROPIC_BASE_URL).
**Acceptance tests:** mocked-LLM returns valid JSON → parsed Classification; malformed JSON → UNKNOWN fallback (no exception); unknown type string → UNKNOWN; component_of populated from a mocked Goulds-style response; NO real network call occurs (assert transport mock called, or no socket).
Commit: `feat(intake): part-type classifier with constrained schema + UNKNOWN fallback (mock-verified)`.

### T3 — Quantity capture (gated)
Under `INTAKE_TYPE_AWARE`: add `quantity` as a first-class intake field — extracted when stated ("need 6 of…"), otherwise defaulted to 1 with an internal `_quantity_assumed=true` marker; persisted in `asset_specs_json`; surfaced on `RunDetail.asset_specs` (not `_`-prefixed once confirmed); editable via the existing specs-update path if one exists (investigate — if the frontend "your parts" section renders specs read-only today, add the minimal editable field for quantity only; frontend verified by `type-check` + `build`, and flag visual verification for morning).
**Acceptance tests:** flag off → no quantity behavior change (byte-identical specs); flag on → "I need 6 SKF 6205 bearings" (mocked extraction) yields quantity=6; unstated → 1 + assumed marker; `_`-prefixed markers never leak to RunDetail/context summary.
Commit: `feat(intake): quantity capture behind INTAKE_TYPE_AWARE`.

### T4 — Wire classifier into intake (gated, no question changes yet)
Under the flag: run `classify_part_type` after the first user message's extraction; store `_classified_type`, `_classified_regime`, `_component_of` as internal `_`-keys on `asset_specs_json`. Flag off: classifier never invoked (assert zero calls).
**Acceptance tests:** flag off → classifier not called, behavior identical; flag on with mocked classifier → keys stored, excluded from context summary and RunDetail (the `_`-prefix filters already exist — prove they cover the new keys); classification failure (UNKNOWN) → intake proceeds exactly as current behavior.
Commit: `feat(intake): classification step wired behind flag (inert off, UNKNOWN falls through)`.

### T5 — Phase 2 plumbing: type-aware Q2 + component-aware sourcing query (gated)
Two pieces, both flag-gated, both mock-verified:
(a) **Type-aware question selection:** when identity is absent and `_classified_type` is a known type, the next clarification uses the registry's `q2_template` (verbatim, no LLM phrasing call) instead of the generic missing-field walk. Respects `_asked_fields` de-dup and `INTAKE_TURN_CAP`. UNKNOWN type → current generic behavior.
(b) **Component-aware query construction:** when `_component_of` is set, the sourcing query targets the COMPONENT for the parent — e.g. specs carrying `part_type=mechanical_seal, component_of="Goulds 3196"` must produce search text like "mechanical seal for Goulds 3196" and MUST NOT produce a bare "Goulds 3196" query. Locate where sourcing queries are built from specs (sourcing_agent) and thread `component_of` through. Flag off: query construction byte-identical.
**Acceptance tests:** (a) mocked state with classified valve type + no identity → next question == the valve q2_template; asked-field recorded; cap still enforced; UNKNOWN → generic question. (b) THE F1 FIXTURE: specs with component_of → generated query string contains both the component term and the parent identity, and a test asserting the bare-parent query is NOT produced; flag off → query identical to today's for the same specs.
Commit: `feat(intake): type-aware Q2 from registry + component-aware sourcing queries behind flag`.

### T6 — The inertness regression wall
A dedicated test class proving the ENTIRE branch is inert when `INTAKE_TYPE_AWARE` is off: no classifier call, no registry consultation, no quantity behavior, no query-construction difference, identical question flow on the bounded-fix fixtures (reuse the existing valve/seal-kit test scenarios), and all pre-existing tests green untouched. Also the falsy-token test (`0/false/no/""/junk` → inert), mirroring the DEMO_MODE convention.
Commit: `test(intake): inertness wall — INTAKE_TYPE_AWARE off is byte-identical`.

### T7 — LangSmith instrumentation (the Phase 0 slice — implement messaging's investigated pattern)
The investigation is DONE — implement, don't re-derive. Messaging's confirmed pattern: NO LangGraph auto-tracing; manual `ls.trace()` context managers everywhere. The only env var is `LANGSMITH_API_KEY` (endpoint hardcoded `https://api.smith.langchain.com`); enablement is programmatic via `tracing_context(enabled=True, project_name=…, tags=…, metadata=…)`; project name `f"Arkim Assistant ({environment})"`, tags `[environment, f"v{version}"]`; root spans `run_type="chain"`, nested LLM spans `run_type="llm"` with `ls_model_name` + usage metadata; children attach to the root via contextvar propagation. Dependency: `langsmith>=0.4.32` ONLY — no langchain, no langgraph.
Implement the procurement slice:
(1) add `langsmith>=0.4.32`;
(2) port a minimal `langsmith_client.py` equivalent exposing the root `trace()` context manager per that pattern — project name `f"Arkim Procurement ({environment})"` (sibling project, decided), reading `LANGSMITH_API_KEY` + `ENVIRONMENT`;
(3) a `traced_llm(name, model, …)` helper mirroring messaging's `traced_invoke` (run_type="llm", model + usage metadata);
(4) route the INTAKE-side calls through it: the three `intake_agent` requests.post calls (text extraction ~:620, multimodal ~:683, clarification ~:826) and the new `classify_part_type`; open a root trace around intake `run(...)` with metadata including the run_id.
Do NOT instrument the sourcing pipeline tonight (`_run_sourcing_background`, sourcing/brand-intel/spec-comparison spans, `root_run_id` persistence on SourcingRun) — that is a named, supervised follow-up; record it in the morning report. Trace linkage convention: every trace carries `run_id` in metadata (per-arc traces, filterable by run_id — the cross-HTTP-boundary decision).
Tracing must be env-gated: with `LANGSMITH_API_KEY` unset, everything is inert and offline — prove with a test (import + call with the env unset, no network, no exception).
**Acceptance tests:** offline-inert test green; dependency pinned; a smoke invocation with the key set produces a run (verify via the langsmith client if straightforward; otherwise log "verify in UI" for morning).
Commit: `feat(observability): LangSmith tracing on intake (messaging's ls.trace pattern, sibling project)`.

### T8 — Labeled eval dataset (from the stress-test scorecard + observed failures)
Create `utils/procurement_agent/tests/fixtures/intake_eval_dataset.json`: ~24–30 labeled examples. Sources: the stress-test scorecard inputs (all tiers) + the observed failure cases — F1 "Goulds 3196 mechanical seal" → `{part_type: mechanical_seal, component_of: "Goulds 3196"}`; the 2-inch stainless ball valve → valve; the Grundfos CR seal kit → mechanical_seal with component_of; the Cerabar PMC21 → sensor_instrument; realistic phrasings covering all 5 registry types; and several off-registry inputs (a hydraulic hose, a light fixture, gibberish) that MUST label as UNKNOWN. Each example: `{input, expected_part_type, expected_component_of (nullable), expected_regime, split: "dev"|"holdout"}` — roughly 2/3 dev, 1/3 holdout, every type represented in both splits where possible. Push the same dataset to LangSmith (create_dataset) so experiments are comparable in the UI; record the dataset name.
**Acceptance tests:** a schema-validation test over the fixture (all fields present, splits non-empty, every registry type + UNKNOWN represented); no live calls in the test itself.
Commit: `test(intake): labeled eval dataset for classifier + extraction (dev/holdout split)`.

### T9 — The live eval loop (the feedback mechanism)
Two experiments via LangSmith evaluate (live Haiku, temperature 0, DEV split only while iterating):
(a) **Classifier accuracy:** run `classify_part_type` on each dev input. Programmatic evaluators: part_type exact-match, component_of match (null-safe), valid-JSON rate. Threshold: ≥90% type accuracy on dev.
(b) **Extraction component-preservation (the F1 live check):** run the real extraction on the component-of-parent inputs; the evaluator asserts the extracted state preserves the component (the part is the SEAL, the parent identity is captured — it is NOT reduced to the parent machine alone).
If below threshold: revise the relevant prompt and re-run the dev split — **max 5 prompt revisions per experiment**, every iteration's score logged. When the threshold is met (or the cap hit): run the HOLDOUT split exactly once and record both scores. Never run holdout mid-iteration; never tune against it.
**Acceptance:** both experiments executed end-to-end; per-iteration dev scores + the single holdout score in the morning report, with LangSmith experiment names; total live-call count logged (expected: a few hundred Haiku calls — trivial spend).
Commit: `feat(intake): eval-tuned classifier + extraction prompts (LangSmith experiments, scores in report)`.

### T10 — Morning report (the contract)
Finalize `MORNING_REPORT.md`:
- Baseline vs final test counts; per-task status table (done/blocked/skipped + commit hash).
- **The eval results:** LangSmith project + dataset + experiment names, per-iteration dev scores, final holdout scores, and which examples still fail (with links) — so Tom opens the UI and inspects the exact traces.
- **VERIFIED tonight** (by test, and by measured eval) vs **NEEDS LIVE VERIFICATION by Tom** (mandatory list — at minimum: the Goulds run END-TO-END with the flag on — classifier and extraction are eval-measured, but the full pipeline through component-aware query construction and real sourcing is not; sourcing quality on committed spec-based inputs; question-flow feel/phrasing with the flag on; quantity edit in the real UI; any dataset examples the eval still fails).
- Every decision you made that wasn't specified here (with file:line), every blocker hit (with diagnosis), any test you added that encodes an assumption Tom should sanity-check.
- The exact commands for morning: how to run with the flag on locally, which fixtures to try first.
Commit: `docs(intake): morning report`. Then STOP.

---

## 3. WHAT IS EXPLICITLY OUT OF SCOPE TONIGHT
Phase 3 (inference proposals, variant/order-code questions). Live-LLM tuning OUTSIDE the T7–T9 eval harness — the harness measures classification accuracy and extraction component-preservation against the labeled dataset; it does NOT measure sourcing quality, question phrasing/UX, or the end-to-end pipeline, so no "iterate until the Goulds case works end-to-end" loops and no full sourcing runs. The seed-file purge. The loading-screen or any frontend redesign beyond the minimal quantity field. Anything touching the security guards or DEMO_MODE surface. Full messaging-parity reconciliation of LangSmith conventions beyond T7's match (fine-tuning naming/tags can happen later, supervised).

---

## 4. DISTILLED REGISTRY SEED (source: the parts-matching research; transcribe, don't invent)

**mechanical_seal** — regime ANCHORED (parent = pump), sourcing MIXED, configurable false.
Identity anchor: "What pump is it on — brand and model off the pump nameplate? And is there a seal cartridge tag or old-seal part number?"
Q2: "What pump make/model is it on, and any old-part code? If visible: shaft size, and is it a cartridge or component seal, single or double?"
Blocking: shaft_size, cartridge_vs_component, single_vs_double, face_material_class, elastomer_product_cip_compatibility. Refinement: seal_brand, premium_face_upgrades.
Inference: {"CIP"|"dairy"|"sanitary"|"food": {wetted:"316L", elastomer:"EPDM", connection:"Tri-Clamp"}}.
Nameplate: pump casing tag near the shaft; old seal's cartridge tag.

**pump** — regime DIRECT, sourcing OEM-leaning MIXED, configurable false.
Identity anchor: "What's the make and model on the pump nameplate (e.g., Alfa Laval LKH-20, Fristam FPX3542)?"
Q2: "Pump type, connection size, and duty — flow/head or HP/RPM? And wetted material if it's product-contact."
Blocking: type, connection_size, hydraulic_duty, wetted_material. Refinement: brand_equivalence, impeller_trim_exactness.
Inference: sanitary/food tokens as above. Nameplate: pump casing plate.

**valve** — regime DIRECT (if tagged, else spec-built), sourcing STANDARD, configurable false.
Identity anchor: "Any make/model on the valve body or tag? If not: what type (ball/butterfly/gate), line size, connection, and is it sanitary?"
Q2: "Type, size, connection, and body material? (And pressure class if known.)"
Blocking: type, size, class_or_cwp, connection, body_and_seat_material, actuation. Refinement: brand, handle_actuator_accessories, trim_upgrade.
Inference: sanitary tokens → {body:"316L", connection:"Tri-Clamp"}. Nameplate: cast/stamped on the body.

**sensor_instrument** — regime DIRECT, sourcing OEM, **configurable TRUE** (order-code family — Phase 3 will add variant questions; tonight just mark it).
Identity anchor: "What's the manufacturer and model on the instrument, or its loop tag? And what does it measure and over what range?"
Q2: "What does it measure and over what range, what output (4-20mA / 0-10V / HART), and what process connection/thread?"
Blocking: measured_variable, range, output_type, process_connection, wetted_material, hazardous_area_rating. Refinement: display_option, brand, accuracy_above_requirement, cable_connector_style.
Inference: sanitary tokens → {wetted:"316L", connection:"Tri-Clamp"}. Nameplate: instrument tag/label; the extended order code is printed on the plate (note in guidance).

**motor_drive** — regime DIRECT, sourcing STANDARD, configurable false.
Identity anchor: "Can you read the motor nameplate — HP/kW, RPM, voltage, and frame?"
Q2: "HP, RPM, voltage/phase, and frame? And is it VFD-fed (inverter duty)?"
Blocking: hp, rpm, voltage_phase, frame, enclosure, mount, inverter_duty_if_vfd_fed. Refinement: brand, efficiency_tier_above_minimum, paint.
Inference: washdown/food tokens → {enclosure:"washdown/TEFC stainless-clad"}. Nameplate: motor nameplate on the frame.

(That is 5 profiles — mechanical_seal + pump count as the "pumps+seals" priority pair. Do not add more types tonight.)

---

## 5. REPORT FORMAT REMINDER
Everything Tom needs to trust this branch in the morning lives in MORNING_REPORT.md: what exists, what's proven, what's assumed, what's blocked, what he must verify live, and how. If the report is honest and the flag-off inertness wall is green, the worst case tonight is zero — the branch gets reviewed, fixed, or deleted, and the demo never noticed.

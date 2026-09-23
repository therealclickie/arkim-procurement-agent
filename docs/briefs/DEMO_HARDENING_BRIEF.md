# BUILD BRIEF — Arc 5: Demo Hardening

**Type:** fixes to confirmed findings from the flags-on end-to-end evaluation.
**Branch:** `arc5/demo-hardening`, cut from `test/flag-on-integration` (2966 backend / 200 frontend).
**Builder:** Claude Opus 5. **Reviewer:** Claude Fable 5. **Merge:** human only.
**Source of truth for the findings:** `E2E_EVAL_REPORT.md` on branch `eval/e2e-flags-on`
(read it with `git show eval/e2e-flags-on:E2E_EVAL_REPORT.md`).
**NO PUSH.** Commit per task; stop for review.

---

## WHY THIS ARC EXISTS

The evaluation found the S1 anchor demo genuinely strong, and every "steer around" in its demo
guidance traces to a specific, independently confirmed defect. This arc removes the reasons to
steer around them, so the demo — and a supervised first pilot — can show the product as it is
claimed to work. Each task maps to a finding ID in the eval report.

The standard for every task: **the system must never claim more than it can prove.** A wrong
answer presented confidently (an "exact match" that isn't, a "$189 accepted" that records no
price, priced results for a request that was never specified) is worse than an honest "needs
verification".

---

## RULINGS (settled — build to these)

**R1 — An accepted structured quote is the order's price (F-07).**
When the buyer accepts a candidate that carries an active structured quote, the order uses the
quote's price, currency, quantity and lead time, and records the quote id it came from. The raw
listing price must NEVER be substituted for an accepted quote. If the quote has expired, been
withdrawn, or no longer matches the run, execution refuses with an explicit, buyer-visible reason
rather than falling back. Where there is genuinely no quote and no price, the existing draft path
remains, but the buyer-facing state must say "unpriced — needs a quote", never a success.

**R2 — The model can lower a match badge, never raise it (F-11).**
`isExactMatch` / `pnMatchLevel` of `exact` or `normalized` may be shown only when the deterministic
part-number classifier independently agrees. The extractor's own `pn_match_status` is advisory: it
may downgrade a result but can never upgrade one. A row that points only at a bare domain, with no
resolvable listing URL, can never be badged exact. Every badge carries the classifier's reason.

**R3 — Notation is normalised; clearance and seal differences are never "exact" (F-12).**
The deterministic classifier normalises separator and clearance notation, so `C3`, `/C3`, `-C3`
and ` C3` compare equal. A clearance mismatch — requested and absent, or different — is a
`mismatch` with the reason stated ("C3 requested; listing is CN/unspecified"). A seal-designation
difference within a family (for example SKF `2RSH` or `2RS1` against a generic `2RS`) is
`needs verification`: never `exact`, and never `none`/`incompatible` purely on notation. Cross-maker
seal equivalence is NOT decided here — that belongs to the equivalence engine.

**R4 — Confirm is gated on sufficiency; overriding it is explicit and visibly labelled (F-15).**
`confirm-intake` refuses to start sourcing, returning the request to clarification, when the
identified class lacks its required discriminators. Required fields come from the part-type
registry where it defines them. Where it does not, the minimum is a manufacturer plus a model, or a
manufacturer part number. An explicit "source anyway" override is allowed only with an
acknowledgement recorded on the run. Such a run is marked `spec_incomplete`, its results carry a
banner stating they have not been checked against the requirement, and no candidate in it may be
badged exact.

**R5 — Hygienic context adds hygienic questions (F-16), narrowly.**
When intake context indicates hygienic service (CIP, SIP, sanitary, washdown, food, dairy, beverage,
pharma, 3-A, EHEDG, tri-clamp), instruments and fittings require process connection type and size,
wetted material, and hygienic certification (3-A / EHEDG / none) before confirm. This is a
question-set addition only — no hygienic equivalence logic in this arc.

**R6 — The variant guard recognises what it has already been told (F-08).**
A family-variant clarification must not re-ask an attribute the request already supplied, and the
hard guard must not report as missing a field present in `asset_specs`. If the gate confirms the
`mechanical seal -> Bearing` units-classification override the verify pass logged is reachable from
the in-app path, fix it here; if it is email-path only, record it as a finding for the email arc.

**R7 — Misconfiguration fails loudly to the operator, never to the supplier (F-03).**
Under the SES provider, starting with `SUPPLIER_ACCOUNTS_V1` on and `SES_CONFIGURATION_SET_AUTH`
unset refuses to boot, with an error naming the missing variable. Under `FakeProvider` (dev, demo,
evaluation) the auth configuration set is NOT required and auth mail is captured normally. At
runtime, any refused auth-mail send raises an ACTION_NOW concierge alert, deduped. The supplier-facing
`request-link` response stays byte-identical in every case — failing loudly must never create an
enumeration oracle.

**R8 — Supplier mail says what the RFQ is and leaks nothing internal (F-09, F-10).**
RFQ_NEW names the part (description and manufacturer), quantity, the needed-by date where known, and
the portal link — and no prices, per the existing no-numbers rule. The subject names the part, or
the count for a coalesced batch ("3 new quote requests"), with each item listed in the body.
Supplier-facing mail must never contain an internal identifier (run UUID or similar) or internal
classification vocabulary ("class-matched", "no brand row", band names).

**R9 — The frontend points at the backend by default (F-02).**
`next.config.ts` defaults `NEXT_PUBLIC_API_URL` to the port the backend actually serves on. Record the
convention in the README.

**R10 — Data location is configurable (F-04).**
One helper resolves the data directory from `GOFER_DATA_DIR`, defaulting to the current
`<repo>/data`. Every store, `persistence.py`, and the JSON stores under `utils/` use it. With the
variable unset, behaviour is identical to today.

---

## PRIME DIRECTIVE — TEST EDITS REQUIRE HUMAN APPROVAL

1. **A pre-existing test file may be modified only if it is listed in
   `loop/AUTHORISED_TEST_EDITS.txt`, a file written by the human after reviewing the gate's
   proposal.** The builder may not create or edit that file.
2. **The fence, for every authorised file:** change ONLY assertions that encode a behaviour one of
   the rulings above supersedes; REPLACE each with an assertion pinning the new behaviour, never
   merely delete it; leave every other assertion byte-identical; keep every invariant tested in its
   new form.
3. Flags off = today's behaviour exactly.
4. No live model, search or AWS calls in any test. Evaluation evidence is used as FIXTURES: read it
   with `git show eval/e2e-flags-on:<path>` and copy it into new fixture files. Tests must be
   built from what the evaluation actually observed, not from invented data.
5. Nothing bypasses send governance; nothing weakens a uniform-rejection or enumeration guarantee.

---

## INVESTIGATION GATE (read-only; report with `file:line` before building)

- **J1.** The order path: `ProcurementAgent._selection_for_order`, `orders._resolve_price`, and
  `quote_store` — where an accepted quote must enter, and what the buyer UI shows for each order
  state.
- **J2.** Every place a match badge is set (`enterprise_search.py:505-543`, `api_server.py:917`, and
  any others), and `_classify_pn_match` — its levels, its inputs, and why it scores `6205-2RSH/C3`
  as `none`.
- **J3.** `confirm_intake` (`api_server.py:3029-3070`), `family_disambig_block`, and what the
  part-type registry defines as required fields per class.
- **J4.** Where intake context and notes are available for R5's hygienic detection.
- **J5.** Why the variant guard re-asks supplied attributes (F-08); whether the
  `mechanical seal -> Bearing` override is reachable in-app.
- **J6.** Boot sequence, `message_configuration_set`, and the concierge alert mechanism, for R7.
- **J7.** RFQ_NEW and TIER1_FYI templates, including the coalesced-batch form.
- **J8.** `frontend/next.config.ts` and where the backend port is defined.
- **J9.** Every hard-coded `_DATA_DIR` or `<repo>/data` site, including the two JSON stores in `utils/`.
- **J10. PROPOSED TEST EDITS.** List every pre-existing test assertion that pins a behaviour this
  arc supersedes. One line per file, in exactly this form:

  `PROPOSED TEST EDIT: <repo-relative path> :: <line ranges> :: <superseded behaviour> :: <ruling>`

  If there are none, write the single line `PROPOSED TEST EDITS: NONE`. Be exhaustive — an assertion
  you miss here cannot be changed later without stopping the arc.

If a scope question the brief does not answer blocks you, end the report with a line beginning
`GATE STOP:` and the question. **Commit the report and stop after the gate.**

---

## BUILD TASKS

Each task names its finding. Tests go in NEW files unless the file is human-authorised.

### T1 — Quote-priced orders (R1, F-07)
*Tests:* using the S1 evidence (`s1_step9_buyer_view.json`, `s1_step10_accept_order.json`), accepting
the $189 DXP quote produces an order priced at $189 carrying the quote id; an expired or withdrawn
quote refuses with a reason and places nothing; for a candidate with a listing price AND an accepted
quote, the order takes the quote; the no-quote-no-price path shows "unpriced — needs a quote".

### T2 — Badge integrity (R2, F-11)
*Tests:* using the S2 candidate set (`s2_step4_candidate_analysis.json`), no C3-less listing is
badged exact for a C3 request; a bare-domain row is never exact; an extractor `exact_match` on a
row the classifier scores `none` is shown as not exact; every badge carries a reason.

### T3 — Notation and clearance (R3, F-12)
*Tests (table):* `6205-2RS C3` against `6205-2RS/C3`, `6205-2RS-C3`, `6205 2RS C3` → equal;
against `6205-2RS` → mismatch with a clearance reason; against `6205-2RSH/C3` and `6205-2RS1/C3` →
needs verification; against the verify pass's observed rows (JSB `6205-2RS-C3`, 123Bearing
`6205-2RSH-C3-SKF`, Motion `6205-2RS-C3`) → never `none`.

### T4 — Confirm sufficiency and labelled override (R4, F-15)
*Tests:* the S3 saved specs (`s3_step2_confirm_attempt.json` and siblings) are refused back to
clarification; the explicit override starts sourcing, records the acknowledgement, marks the run
`spec_incomplete`, shows the banner, and badges nothing exact; a fully specified request confirms
normally; the S1 path still confirms.

### T5 — Hygienic questions (R5, F-16)
*Tests:* "pressure gauge on the CIP skid" asks connection type and size, wetted material and
certification before confirm; the same gauge with no hygienic context does not; the questions come
from R5's field set, not free text.

### T6 — Variant guard (R6, F-08)
*Tests:* the S1 request carrying make, model and `shaft_size` is not re-asked any of them; the hard
guard does not name a supplied field as missing; if the units override is in scope, "mechanical
seal" is never classified as a bearing.

### T7 — Loud failure without an oracle (R7, F-03)
*Tests:* boot under `MAIL_PROVIDER=ses` with accounts on and the auth set unset → refuses with the
variable named; under FakeProvider → boots and captures auth mail; a refused auth send → one
ACTION_NOW alert, deduped; the `request-link` response is byte-identical whether the send succeeded,
was refused, or the address is unknown (equality assertion with a contrast case).

### T8 — Supplier mail content (R8, F-09, F-10)
*Tests:* RFQ_NEW names part, manufacturer, quantity and portal link, contains no price; a coalesced
batch's subject gives the count and its body lists each item; a scan of every supplier-facing
template for a UUID pattern and the internal-vocabulary denylist finds nothing.

### T9 — Frontend default (R9, F-02)
*Tests:* the config default matches the backend's serving port.

### T10 — Configurable data directory (R10, F-04)
*Tests:* with `GOFER_DATA_DIR` set, every store writes under it; with it unset, paths are unchanged;
a source-scan test fails if any module hard-codes `<repo>/data`.

---

## SUCCESS CRITERIA (falsifiable)

1. Backend green, reported as `2966 + N`; frontend green, `200 + M`.
2. `git diff --name-status` shows `M` on pre-existing test files ONLY where the file is listed in
   `loop/AUTHORISED_TEST_EDITS.txt`.
3. No code path substitutes a listing price for an accepted quote.
4. No code path lets the extractor's verdict raise a match badge.
5. The S3 specs cannot reach sourcing without the recorded override.
6. The `request-link` response is byte-identical across send success, send refusal and unknown email.
7. Every test built on eval evidence cites the evidence file it was derived from.
8. Flags-off run: both suites green.
9. `DEMO_HARDENING_REPORT.md` holds the gate J1–J10, both counts, FINDINGS, and a map from each eval
   finding ID to the task and tests that close it.

---

## REVIEWER CHECKLIST

- **R1.** Is every `M` pre-existing test file listed in `loop/AUTHORISED_TEST_EDITS.txt`? Any other →
  immediate `CHANGES_REQUESTED`.
- **R1b.** For each authorised file, read the diff line by line: only superseded assertions changed,
  each replaced rather than deleted, invariants intact?
- **R2.** Trace every place a match badge is set. Can the extractor's verdict raise one anywhere?
  Any path → BLOCKER.
- **R3.** Trace order execution. Can a listing price ever stand in for an accepted quote? Any path →
  BLOCKER.
- **R4.** Is the `request-link` equality a true byte comparison with a contrast case? Did R7 add any
  distinguishable response, status or timing-independent shape?
- **R5.** Are the S1, S2 and S3 tests built from the actual eval evidence (check the cited files
  exist on `eval/e2e-flags-on` and the fixture matches), or from invented data? Invented → MAJOR.
- **R6.** Is the R4 override genuinely explicit and visibly labelled, or could an unspecified request
  still reach priced results silently?
- **R7.** Both suites with flags OFF.
- **R8.** FINDINGS genuine, or task-avoidance?

**Standing rule (CLAUDE.md):** an exit-checklist item may be marked done only if the evidence exists
as a committed artefact or a named passing test.

---

## OUT OF SCOPE

Email intake statefulness (F-06 — its own arc). Known-sender registration (F-05). Clearance as a
comparison-schema field and substitute-intent handling (F-13, F-14 — the equivalence engine).
Cross-maker seal equivalence. Hygienic equivalence logic. Infra provisioning.

---

## HUMAN VERIFICATION (after `APPROVED`)

Merge, then re-run the evaluation on a fresh branch so it measures the hardened system rather than
continuing the old report:

```powershell
.\loop\run_e2e_eval.ps1 -Branch eval/e2e-post-hardening
```

Compare the two verdicts. The target is that S1 now completes through ordering, S2 no longer badges
a wrong bearing exact, S3 no longer returns priced noise, and the demo guidance's "steer around" list
is down to email intake.

"""S3 — Ambiguous request: "The pressure gauge on the CIP skid is reading
wrong. Need a new one."

PASS = asks range / process connection / reference type / wetted material,
and (CIP => hygienic service) raises hygienic requirements.
BREAK = sources a gauge without establishing range, connection, reference type.

Run:  uv run python eval/e2e/s3_ambiguous_gauge.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.install(live=True)
harness.isolate_stores(fresh=False)
client, api_server = harness.make_client()
provider = harness.fake_provider()

STEPS: list[dict] = []


def record(step, expected, observed, verdict, evidence):
    STEPS.append({"step": step, "expected": expected, "observed": observed,
                  "verdict": verdict, "evidence": evidence})
    print(f"\n[{verdict}] {step}\n  observed: {observed[:500]}")


t0 = time.time()

rc = client.post("/api/runs", json={"facility_id": "fac-stockton",
                                    "urgency_factor": 0.5})
run_id = rc.json().get("id")
rm = client.post(f"/api/runs/{run_id}/messages",
                 json={"content": "The pressure gauge on the CIP skid is "
                                  "reading wrong. Need a new one."})
chat1 = rm.json() if rm.status_code < 500 else {"raw": rm.text}
reply1 = (chat1.get("message") or {}).get("content", "")
specs1 = client.get(f"/api/runs/{run_id}").json().get("asset_specs") or {}
ev = harness.save_evidence("s3_step1_chat.json",
                           {"run_id": run_id, "chat": chat1, "specs": specs1})

TOPICS = {
    "range": r"range|psi|bar|0-\d|pressure rating",
    "process_connection": r"connection|npt|thread|tri-?clamp|fitting|mount",
    "reference_type": r"gauge pressure|absolute|reference|vacuum|compound",
    "wetted_material": r"wetted|material|316|stainless|diaphragm",
    "hygienic": r"hygienic|sanitary|tri-?clamp|3-?a|cip|clean-?in-?place|washdown",
    "brand_or_model": r"brand|make|model|manufacturer|part number|old.*(code|number)",
}
asked = {k: bool(re.search(p, reply1, re.I)) for k, p in TOPICS.items()}
record("1. ambiguous ask -> what does it ask back?",
       "asks range, process connection, reference type, wetted material; "
       "raises hygienic (CIP)",
       f"agent reply: {reply1!r} | topics detected: {asked}",
       "PASS" if asked["range"] and (asked["process_connection"]
                                     or asked["reference_type"]) else "DEGRADED",
       ev)

# The hard gate: can an under-specified gauge request be confirmed into sourcing?
rci = client.post(f"/api/runs/{run_id}/confirm-intake")
body = rci.json() if rci.status_code < 500 else rci.text
sourced = None
if rci.status_code == 200:
    d = client.get(f"/api/runs/{run_id}").json()
    sr = d.get("sourcing_results") or {}
    sourced = {"phase": d.get("phase"),
               "counts": {k: len(sr.get(k) or []) for k in ("tier1", "tier2", "tier3")},
               "specs_at_sourcing": d.get("asset_specs")}
    harness.save_evidence("s3_step2_sourced_anyway.json", d)
ev = harness.save_evidence("s3_step2_confirm_attempt.json",
                           {"status": rci.status_code, "body": body,
                            "sourced": sourced})
record("2. confirm-intake without range/connection/reference",
       "refused (422) — sourcing must not start on an unspecified gauge",
       f"HTTP {rci.status_code}: {json.dumps(body, default=str)[:300]}"
       + (f" | SOURCED ANYWAY: {json.dumps(sourced, default=str)[:300]}" if sourced else ""),
       "PASS" if rci.status_code == 422 else "BREAK",
       ev)

# Partial answer: give range only — does it keep asking for the rest?
rm2 = client.post(f"/api/runs/{run_id}/messages",
                  json={"content": "It reads 0-60 psi I think. It's the one "
                                   "on the CIP skid return line."})
chat2 = rm2.json() if rm2.status_code < 500 else {"raw": rm2.text}
reply2 = (chat2.get("message") or {}).get("content", "")
specs2 = client.get(f"/api/runs/{run_id}").json().get("asset_specs") or {}
asked2 = {k: bool(re.search(p, reply2, re.I)) for k, p in TOPICS.items()}
ev = harness.save_evidence("s3_step3_partial_answer.json",
                           {"chat": chat2, "specs": specs2, "asked": asked2})
record("3. partial answer (range only) -> remaining asks",
       "asks connection / reference type / wetted material / hygienic next",
       f"agent reply: {reply2!r} | topics: {asked2} | "
       f"specs now: {json.dumps({k: specs2.get(k) for k in ('manufacturer', 'model', 'part_number', 'detected_type', 'description')}, default=str)}",
       "PASS" if any(asked2[k] for k in ("process_connection", "reference_type",
                                         "wetted_material", "hygienic",
                                         "brand_or_model")) else "DEGRADED",
       ev)

# Second confirm attempt with range known but connection/reference still open.
rci2 = client.post(f"/api/runs/{run_id}/confirm-intake")
body2 = rci2.json() if rci2.status_code < 500 else rci2.text
sourced2 = None
if rci2.status_code == 200:
    d2 = client.get(f"/api/runs/{run_id}").json()
    sr2 = d2.get("sourcing_results") or {}
    sourced2 = {"phase": d2.get("phase"),
                "counts": {k: len(sr2.get(k) or []) for k in ("tier1", "tier2", "tier3")},
                "specs_at_sourcing": d2.get("asset_specs"),
                "sample": [{ "vendor": c.get("vendorName"),
                             "foundPartNumber": c.get("foundPartNumber"),
                             "price": c.get("price"), "url": c.get("url")}
                           for c in (sr2.get("tier2", []) + sr2.get("tier3", []))[:6]]}
    harness.save_evidence("s3_step4_sourced_detail.json", d2)
ev = harness.save_evidence("s3_step4_confirm2.json",
                           {"status": rci2.status_code, "body": body2,
                            "sourced": sourced2})
record("4. confirm with range only (connection/reference still unknown)",
       "still refused — connection + reference type not established",
       f"HTTP {rci2.status_code}"
       + (f" | SOURCED: {json.dumps(sourced2, default=str)[:400]}" if sourced2
          else f": {json.dumps(body2, default=str)[:250]}"),
       "PASS" if rci2.status_code == 422 else "BREAK",
       ev)

# Step 5 — POST-HARDENING (arc 5 T4): the labelled override. source_anyway=true
# must confirm (200), record the acknowledgement, mark the run spec_incomplete,
# carry the banner on results, and badge NO candidate exact.
rci3 = client.post(f"/api/runs/{run_id}/confirm-intake?source_anyway=true")
body3 = rci3.json() if rci3.status_code < 500 else rci3.text
d3 = {}
if rci3.status_code == 200:
    for _ in range(3):
        d3 = client.get(f"/api/runs/{run_id}").json()
        if d3.get("phase") in ("comparison", "error"):
            break
        time.sleep(2)
sr3 = (d3.get("sourcing_results") or {}) if d3 else {}
all3 = (sr3.get("tier1", []) + sr3.get("tier2", []) + sr3.get("tier3", [])
        + (d3.get("findings") or [] if d3 else []))
exact3 = [{"vendor": c.get("vendorName"), "isExactMatch": c.get("isExactMatch"),
           "pnMatchLevel": c.get("pnMatchLevel")} for c in all3
          if c.get("isExactMatch") or c.get("pnMatchLevel") == "exact"]
specs3 = (d3.get("asset_specs") or {}) if d3 else {}
marker = {"spec_incomplete": sr3.get("specIncomplete") or specs3.get("spec_incomplete"),
          "banner": sr3.get("specIncompleteBanner"),
          "ack": {k: v for k, v in specs3.items() if "anyway" in str(k).lower()
                  or "incomplete" in str(k).lower()}}
if d3:
    harness.save_evidence("s3_step5_sourced_detail.json", d3)
ev = harness.save_evidence("s3_step5_override.json",
                           {"status": rci3.status_code, "body": body3,
                            "phase": d3.get("phase") if d3 else None,
                            "marker": marker, "exact_badged": exact3,
                            "candidate_count": len(all3)})
record("5. labelled override (arc5 T4: source_anyway=true)",
       "200; run marked spec_incomplete with banner; acknowledgement recorded; "
       "NO candidate badged exact on a spec-incomplete run",
       f"HTTP {rci3.status_code}, phase={d3.get('phase') if d3 else None}, "
       f"marker={json.dumps(marker, default=str)[:250]}, "
       f"exact-badged={len(exact3)}, candidates={len(all3)}",
       "PASS" if rci3.status_code == 200
       and (marker["spec_incomplete"] or marker["banner"])
       and not exact3 else "BREAK", ev)

summary = {"scenario": "S3 ambiguous gauge", "run_id": run_id,
           "duration_s": round(time.time() - t0, 1),
           "network": harness.network_summary(), "steps": STEPS}
harness.save_evidence("s3_steps.json", summary)
print(f"\n=== S3 done in {summary['duration_s']}s — calls this process: "
      f"{summary['network']['external_call_count']} ===")
print("verdicts:", [(s["step"].split(".")[0], s["verdict"]) for s in STEPS])

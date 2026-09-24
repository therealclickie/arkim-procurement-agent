"""S3b — POST-HARDENING follow-up: the hygienic question set (arc 5 T5, F-16).

The S3 run never reached the hygienic gate because the identity floor (T4)
fires first. This probe clears the floor (manufacturer + model) on a CIP-skid
gauge and observes whether the hygienic gate now blocks confirm until the
hygienic fields are established — and whether answering them clears it.

Run:  uv run python eval/e2e/s3b_hygienic_gate.py
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
                                  "reading wrong. Need a new one. It's an "
                                  "Ashcroft 1032, 0-60 psi, on the CIP "
                                  "return line."})
chat1 = rm.json() if rm.status_code < 500 else {"raw": rm.text}
reply1 = (chat1.get("message") or {}).get("content", "")
specs1 = client.get(f"/api/runs/{run_id}").json().get("asset_specs") or {}
harness.save_evidence("s3b_step1_chat.json",
                      {"run_id": run_id, "chat": chat1, "specs": specs1})

HYG = re.compile(r"hygienic|sanitary|tri-?clamp|3-?a\b|ehedg|washdown|"
                 r"wetted|connection size|certification", re.I)
record("1. identity-sufficient CIP gauge intake",
       "specs capture Ashcroft 1032 + range; hygienic context available",
       f"reply={reply1!r} | specs: "
       f"{json.dumps({k: specs1.get(k) for k in ('manufacturer', 'model', 'detected_type', 'description', 'use_case')}, default=str)[:300]}",
       "PASS" if (specs1.get("manufacturer") or "").lower().startswith("ashcroft")
       else "DEGRADED", "s3b_step1_chat.json")

# Confirm WITHOUT override: the identity floor should clear; does the
# hygienic gate (T5) now block, naming the hygienic fields?
rci = client.post(f"/api/runs/{run_id}/confirm-intake")
body = rci.json() if rci.status_code < 500 else rci.text
detail_d = body.get("detail") if isinstance(body, dict) else None
reason = (detail_d or {}).get("reason") if isinstance(detail_d, dict) else None
blocked_fields = (detail_d or {}).get("missing_attrs") \
    if isinstance(detail_d, dict) else None
hyg_named = bool(HYG.search(json.dumps(body, default=str)))
harness.save_evidence("s3b_step2_confirm.json",
                      {"status": rci.status_code, "body": body})
record("2. confirm past the identity floor (arc5 T5: hygienic gate)",
       "422 blocking on the hygienic question set (connection size / wetted "
       "material / process connection / certification), NOT on identity",
       f"HTTP {rci.status_code}, reason={reason!r}, "
       f"missing={blocked_fields!r}, hygienic-vocab in refusal={hyg_named}, "
       f"body={json.dumps(body, default=str)[:300]}",
       "PASS" if rci.status_code == 422 and reason != "identity_insufficient"
       and hyg_named else
       ("BREAK" if rci.status_code == 200 else "DEGRADED"), "s3b_step2_confirm.json")

# Answer the hygienic fields in chat, then confirm again.
sourced = None
if rci.status_code == 422:
    rm2 = client.post(f"/api/runs/{run_id}/messages", json={
        "content": "It's on a 1.5 inch Tri-Clamp connection, 316L stainless "
                   "wetted parts, 3-A certified sanitary gauge."})
    chat2 = rm2.json() if rm2.status_code < 500 else {"raw": rm2.text}
    specs2 = client.get(f"/api/runs/{run_id}").json().get("asset_specs") or {}
    rci2 = client.post(f"/api/runs/{run_id}/confirm-intake")
    body2 = rci2.json() if rci2.status_code < 500 else rci2.text
    if rci2.status_code == 200:
        for _ in range(3):
            d2 = client.get(f"/api/runs/{run_id}").json()
            if d2.get("phase") in ("comparison", "error"):
                break
            time.sleep(2)
        sourced = {"phase": d2.get("phase")}
        harness.save_evidence("s3b_step3_sourced.json", d2)
    harness.save_evidence("s3b_step3_answered.json",
                          {"chat": chat2, "specs": specs2,
                           "confirm_status": rci2.status_code, "body": body2,
                           "sourced": sourced})
    record("3. answering the hygienic fields lets it confirm",
           "chat records tri-clamp/316L/3-A; confirm now 200 -> sourcing",
           f"specs after: "
           f"{json.dumps({k: v for k, v in specs2.items() if k in ('process_connection', 'connection_size', 'wetted_material', 'material_spec', 'hygienic_certification', 'certification')}, default=str)[:250]} "
           f"| confirm={rci2.status_code}, sourced={sourced}",
           "PASS" if rci2.status_code == 200 else "DEGRADED",
           "s3b_step3_answered.json")

summary = {"scenario": "S3b hygienic gate (T5)", "run_id": run_id,
           "duration_s": round(time.time() - t0, 1),
           "network": harness.network_summary(), "steps": STEPS}
harness.save_evidence("s3b_steps.json", summary)
print(f"\n=== S3b done in {summary['duration_s']}s — calls this process: "
      f"{summary['network']['external_call_count']} ===")
print("verdicts:", [(s["step"].split(".")[0], s["verdict"]) for s in STEPS])

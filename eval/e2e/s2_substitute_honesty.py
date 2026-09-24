"""S2 — Substitute honesty (equivalence-engine gap measurement). LIVE.

"We need an SKF 6205-2RS C3 for the conveyor motor. SKF is on backorder —
what can we get?"

Observes (does NOT fix): like-for-like vs substitute distinction, cross-maker
seal-code equivalences (2RS1/2RSR/DDU/LLU) presented with/without evidence,
C3 clearance preservation, honest fallback. BLOCKER only if a non-equivalent
part is presented AS equivalent.

Run:  uv run python eval/e2e/s2_substitute_honesty.py
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
OBS: list[dict] = []   # detailed observations -> equivalence-engine inputs


def record(step, expected, observed, verdict, evidence):
    STEPS.append({"step": step, "expected": expected, "observed": observed,
                  "verdict": verdict, "evidence": evidence})
    print(f"\n[{verdict}] {step}\n  observed: {observed[:400]}")


def observe(topic, detail):
    OBS.append({"topic": topic, "detail": detail})
    print(f"  OBS[{topic}]: {json.dumps(detail, default=str)[:300]}")


t0 = time.time()

# Step 1 — in-app intake
rc = client.post("/api/runs", json={"facility_id": "fac-stockton",
                                    "urgency_factor": 0.7})
run_id = rc.json().get("id")
msg_text = ("We need an SKF 6205-2RS C3 for the conveyor motor. "
            "SKF is on backorder — what can we get?")
rm = client.post(f"/api/runs/{run_id}/messages", json={"content": msg_text})
chat1 = rm.json() if rm.status_code < 500 else {"raw": rm.text}
agent_reply = (chat1.get("message") or {}).get("content", "")
ev = harness.save_evidence("s2_step1_intake.json",
                           {"run_id": run_id, "chat": chat1})
record("1. intake (in-app chat)",
       "SKF 6205-2RS C3 captured; C3 preserved; substitute intent recognized",
       f"run={run_id}, agent reply: {agent_reply[:300]!r}",
       "PASS" if run_id and rm.status_code == 200 else "BREAK", ev)

# One clarification turn if asked, staying inside the scenario facts.
r = client.get(f"/api/runs/{run_id}")
specs = r.json().get("asset_specs") or {}
if not specs.get("part_number"):
    rm2 = client.post(f"/api/runs/{run_id}/messages",
                      json={"content": "SKF 6205-2RS C3, deep groove ball "
                            "bearing for the conveyor drive motor. SKF is "
                            "backordered everywhere, we need alternatives."})
    harness.save_evidence("s2_step1b_chat2.json", rm2.json())
    specs = client.get(f"/api/runs/{run_id}").json().get("asset_specs") or {}

spec_text = json.dumps(specs, default=str)
c3_in_specs = "C3" in spec_text.upper().replace("-", "")
observe("intake_specs", {"part_number": specs.get("part_number"),
                         "manufacturer": specs.get("manufacturer"),
                         "description": specs.get("description"),
                         "detected_type": specs.get("detected_type"),
                         "C3_preserved_anywhere": c3_in_specs})
record("2. C3 preservation at intake",
       "C3 clearance retained in captured specs",
       f"part_number={specs.get('part_number')!r}, C3 anywhere in specs={c3_in_specs}",
       "PASS" if c3_in_specs else "BREAK",
       "s2_step1_intake.json")

# Step 3 — confirm + sourcing
rci = client.post(f"/api/runs/{run_id}/confirm-intake")
if rci.status_code == 422 and isinstance(rci.json().get("detail"), dict) \
        and rci.json()["detail"].get("reason") == "family_variant_unconfirmed":
    rci = client.post(f"/api/runs/{run_id}/confirm-intake?open_family=true")
detail = {}
for _ in range(3):
    detail = client.get(f"/api/runs/{run_id}").json()
    if detail.get("phase") in ("comparison", "error"):
        break
    time.sleep(2)
ev = harness.save_evidence("s2_step3_run_detail.json", detail)
record("3. sourcing completes",
       "phase=comparison",
       f"confirm={rci.status_code}, phase={detail.get('phase')!r}, "
       f"no_exact_match={detail.get('no_exact_match')!r}",
       "PASS" if detail.get("phase") == "comparison" else "BREAK", ev)

# Step 4 — candidate honesty analysis
sr = detail.get("sourcing_results") or {}
findings = sr.get("findings") or []
all_c = sr.get("tier1", []) + sr.get("tier2", []) + sr.get("tier3", [])
SEAL_EQUIV = re.compile(r"2RS1|2RSR|2RSH|DDU|LLU|EE\b", re.I)
analysis = []
for c in all_c + findings:
    fpn = (c.get("foundPartNumber") or "")
    entry = {
        "vendor": c.get("vendorName"), "tier": c.get("tier"),
        "band": c.get("band"), "foundPartNumber": fpn,
        "isExactMatch": c.get("isExactMatch"),
        "pnMatchLevel": c.get("pnMatchLevel"),
        "pnMatchReason": c.get("pnMatchReason"),
        "isAftermarket": c.get("isAftermarket"),
        "evidenceState": c.get("evidenceState"),
        "price": c.get("price"), "url": c.get("url"),
        "suitability": c.get("suitability"),
        "aftermarketDisclosure": c.get("aftermarketDisclosure"),
        "comparisonArtifact": c.get("comparisonArtifact"),
        "c3_in_found_pn": "C3" in fpn.upper(),
        "cross_maker_seal_code": bool(SEAL_EQUIV.search(fpn)),
    }
    analysis.append(entry)
ev = harness.save_evidence("s2_step4_candidate_analysis.json", analysis)

exact_claims = [a for a in analysis if a["isExactMatch"]]
bad_exact = [a for a in exact_claims
             if a["foundPartNumber"] and (
                 "C3" not in a["foundPartNumber"].upper()
                 or "6205" not in a["foundPartNumber"])]
cross_maker = [a for a in analysis if a["cross_maker_seal_code"]]
c3_dropped = [a for a in analysis
              if a["foundPartNumber"] and "6205" in a["foundPartNumber"]
              and "C3" not in a["foundPartNumber"].upper()]
observe("exact_match_claims", exact_claims)
observe("exact_claims_missing_C3_or_wrong_size", bad_exact)
observe("cross_maker_seal_codes_presented", cross_maker)
observe("c3_dropped_variants_presented", c3_dropped)
record("4. equivalence honesty",
       "no non-equivalent part presented as equivalent (exact-match claims "
       "must carry 6205 AND C3); cross-maker codes only with evidence",
       f"candidates={len(analysis)}, exact_claims={len(exact_claims)}, "
       f"exact-but-not-C3/size={len(bad_exact)}, cross-maker={len(cross_maker)}, "
       f"C3-dropped-shown={len(c3_dropped)}",
       "BREAK-BLOCKER" if bad_exact else "PASS", ev)

# Step 4b — POST-HARDENING follow-ups on the badge gate (arc 5 T2/T3) and the
# open gate finding F-A: does the BAND still rank a C3-less listing as a
# confirmed part even where the badge is now honest? And does the true-C3
# listing still score below the C3-less ones (F-12)?
c3_less = [a for a in analysis
           if a["foundPartNumber"] and "6205" in a["foundPartNumber"]
           and "C3" not in a["foundPartNumber"].upper()]
c3_true = [a for a in analysis if a["c3_in_found_pn"]]
fa_check = {
    "c3_less_rows": [{"vendor": a["vendor"], "band": a["band"],
                      "pnMatchLevel": a["pnMatchLevel"],
                      "pnMatchReason": a["pnMatchReason"],
                      "isExactMatch": a["isExactMatch"], "url": a["url"]}
                     for a in c3_less],
    "c3_true_rows": [{"vendor": a["vendor"], "band": a["band"],
                      "pnMatchLevel": a["pnMatchLevel"],
                      "pnMatchReason": a["pnMatchReason"],
                      "isExactMatch": a["isExactMatch"], "url": a["url"]}
                     for a in c3_true],
    "c3_less_in_band_A_or_B": [a["vendor"] for a in c3_less
                               if (a["band"] or "").upper() in ("A", "B")],
    "true_c3_scored_none": [a["vendor"] for a in c3_true
                            if (a["pnMatchLevel"] or "none") == "none"],
}
observe("f_a_band_vs_badge", fa_check)
ev = harness.save_evidence("s2_step4b_band_vs_badge.json", fa_check)
record("4b. band honesty (gate finding F-A) + true-C3 ranking (F-12)",
       "badge gate holds (T2/T3); note whether the BAND still lifts C3-less "
       "rows (F-A, known-open) and whether the genuine C3 listing scores none",
       f"C3-less rows={len(c3_less)} (bands: "
       f"{[a['band'] for a in c3_less]}), in A/B={fa_check['c3_less_in_band_A_or_B']}, "
       f"true-C3 rows={len(c3_true)} scored none={fa_check['true_c3_scored_none']}",
       "PASS" if not fa_check["true_c3_scored_none"] else "DEGRADED",
       ev)

# Step 5 — how is uncertainty presented? (banners, disclosures, artifacts)
uncertainty = {
    "no_exact_match_flag": detail.get("no_exact_match"),
    "warrantyBanner": sr.get("warrantyBanner"),
    "aftermarket_disclosures": [a["aftermarketDisclosure"] for a in analysis
                                if a.get("aftermarketDisclosure")],
    "comparison_artifacts_present": sum(1 for a in analysis
                                        if a.get("comparisonArtifact")),
    "quoted_confirmed": [a for a in analysis if a["evidenceState"] == "quoted"],
}
ev = harness.save_evidence("s2_step5_uncertainty.json", uncertainty)
record("5. honest fallback",
       "'needs verification' style presentation where evidence is missing; "
       "no fabricated availability/price claims",
       json.dumps({k: (len(v) if isinstance(v, list) else v)
                   for k, v in uncertainty.items()}, default=str),
       "PASS" if not any(a["price"] and not a["url"] for a in analysis)
       else "BREAK", ev)

summary = {"scenario": "S2 substitute honesty", "run_id": run_id,
           "duration_s": round(time.time() - t0, 1),
           "network": harness.network_summary(),
           "steps": STEPS, "observations": OBS}
harness.save_evidence("s2_steps.json", summary)
print(f"\n=== S2 done in {summary['duration_s']}s — external calls "
      f"{summary['network']['external_call_count']}/{harness.MAX_EXTERNAL_CALLS} "
      f"(cumulative this process) ===")
print("verdicts:", [(s["step"].split(".")[0], s["verdict"]) for s in STEPS])

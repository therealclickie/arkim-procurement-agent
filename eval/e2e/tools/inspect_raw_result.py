"""Read the RAW stored sourcing result for a run from the ISOLATED DB."""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "data", "sourcing_runs.sqlite")
run_id = sys.argv[1]

con = sqlite3.connect(f"file:{os.path.abspath(DB)}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
row = con.execute("select sourcing_results_json from sourcing_runs where id=?",
                  (run_id,)).fetchone()
if not row or not row[0]:
    print("no stored sourcing result")
    sys.exit(0)
res = row[0]
if isinstance(res, (bytes, str)):
    res = json.loads(res)
print("result keys:", sorted(res.keys()))
print("filters_applied:", res.get("filters_applied"))
for tk in ("tier_1", "tier_2", "tier_3"):
    tier = res.get(tk) or {}
    print(f"\n{tk}: status={tier.get('status')} count={tier.get('count')}")
    for c in (tier.get("results") or []):
        print("  ", json.dumps({k: c.get(k) for k in (
            "vendor_name", "band", "evidence_quality", "provenance",
            "confidence_score", "banded", "is_mock", "rejection_reason",
            "suitability_score", "source_url", "found_part_number")
            if c.get(k) is not None}, default=str)[:400])

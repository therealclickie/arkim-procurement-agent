"""Summarize a saved run-detail JSON (tiers, bands, findings, outreach)."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else \
    "eval/e2e/evidence/s1_step2_run_detail.json"
d = json.load(open(path, encoding="utf-8"))
sr = d.get("sourcing_results") or {}
print("top-level keys:", sorted(d.keys()))
print("tiers:", {k: len(sr.get(k) or []) for k in ("tier1", "tier2", "tier3")})
f = d.get("findings")
print("findings:", None if f is None else len(f))
print("outreachTargets:", json.dumps(d.get("outreachTargets"))[:500])
print("warrantyBanner:", sr.get("warrantyBanner"),
      "tier3CapabilityPivot:", sr.get("tier3CapabilityPivot"))
FIELDS = ("vendorName", "band", "evidenceState", "evidenceQuality", "price",
          "url", "foundPartNumber", "suitability", "pnMatchLevel", "isMock",
          "registryBacked", "confidence", "isExactMatch", "leadTime")
for t in ("tier1", "tier2", "tier3"):
    for c in (sr.get(t) or []):
        print(t, json.dumps({k: c.get(k) for k in FIELDS if c.get(k) is not None},
                            default=str)[:400])
for c in (f or []):
    print("finding", json.dumps({k: c.get(k) for k in FIELDS
                                 if c.get(k) is not None}, default=str)[:400])

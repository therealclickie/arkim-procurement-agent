import sqlite3, json
c = sqlite3.connect("data/sourcing_runs.sqlite")
c.row_factory = sqlite3.Row
row = list(c.execute("select * from sourcing_runs order by rowid desc limit 1"))[0]
d = dict(row)
out = {
    "id": d["id"],
    "initiated_at": d["initiated_at"],
    "asset_specs": json.loads(d["asset_specs_json"] or "{}"),
    "sourcing_results": json.loads(d["sourcing_results_json"] or "{}"),
}
open("run_full.json", "w").write(json.dumps(out, indent=2))
print("wrote run_full.json")

sr = out["sourcing_results"]
for tier in ("tier_1", "tier_2", "tier_3"):
    t = sr.get(tier, {})
    res = t.get("results", [])
    print(f"\n=== {tier}: count={t.get('count')} status={t.get('status')} ===")
    for r in res:
        print(f"  {r.get('vendor_name')}")
        print(f"     match={r.get('match_type')} found_pn={r.get('found_part_number')}")
        print(f"     suit={r.get('suitability_score')} conf={r.get('confidence_score')} price={r.get('base_price')} tbd={r.get('price_tbd')}")
        print(f"     url={r.get('source_url')}")
        print(f"     mock={r.get('is_mock')} rej={r.get('rejection_reason')} rank_tier={r.get('suitability_rank_tier')}")
for k, v in sr.items():
    if k not in ("tier_1", "tier_2", "tier_3"):
        print(f"\n{k}: {v}")

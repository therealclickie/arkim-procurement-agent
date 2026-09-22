import sqlite3, json
c = sqlite3.connect("data/sourcing_runs.sqlite")
c.row_factory = sqlite3.Row
row = list(c.execute("select * from sourcing_runs order by rowid desc limit 1"))[0]
d = dict(row)
print("run:", d["id"], "| initiated:", d["initiated_at"])
sr = json.loads(d["sourcing_results_json"] or "{}")
for tier in ("tier_1","tier_2","tier_3"):
    res = sr.get(tier,{}).get("results",[])
    print(f"\n===== {tier} ({len(res)}) =====")
    for r in res:
        print(f"{str(r.get('vendor_name'))[:44]:<46} band={r.get('band')} seeded={r.get('seeded_from_cache')}")
        print(f"    found_pn={r.get('found_part_number')} match={r.get('match_type')} pn_status={r.get('pn_match_status')}")
        print(f"    band_reason={str(r.get('band_reason'))[:70]} eq={r.get('evidence_quality')}")
        print(f"    price={r.get('base_price')} url={'Y' if r.get('source_url') else 'N'} rej={r.get('rejection_reason')} mock={r.get('is_mock')}")
print("\nfindings[]:", [f.get("vendorName") or f.get("vendor_name") for f in sr.get("findings",[])] if "findings" in sr else "ABSENT")
ot = sr.get("outreachTargets")
print("outreachTargets:", json.dumps(ot)[:400] if ot else "ABSENT")
print("filters_applied:", sr.get("filters_applied"))

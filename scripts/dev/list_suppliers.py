import sqlite3, json
c = sqlite3.connect("data/supplier_registry.sqlite")

# how many suppliers total, and what lifecycle states exist
print("--- lifecycle distribution ---")
for r in c.execute("select tier1_lifecycle, count(*) from suppliers group by tier1_lifecycle"):
    print(r)
print()

print("--- discovery_only suppliers ---")
rows = list(c.execute("select domain, name, tier1_lifecycle, verticals_json, suitability_status from suppliers where tier1_lifecycle = 'discovery_only'"))
print(f"count: {len(rows)}\n")
for d, n, lc, v, s in rows:
    print(f"{n or '?':<40} {d:<30} suitability={s}")
    if v:
        print(f"    verticals: {v[:160]}")
print()

print("--- anything already onboarded/live ---")
for r in c.execute("select domain, name, tier1_lifecycle from suppliers where tier1_lifecycle != 'discovery_only' or tier1_lifecycle is null"):
    print(r)

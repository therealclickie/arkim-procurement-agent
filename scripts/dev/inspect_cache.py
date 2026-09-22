import json, sys
sys.path.insert(0, ".")
from utils.known_parts import canonical_part_key, get_edges, _DB_PATH

print("DB:", _DB_PATH)
d = json.load(open(_DB_PATH))
print("top-level keys:", list(d.keys())[:5], "... total:", len(d))

pk = canonical_part_key("Gusher Pumps", "84004-28-C238CBC")
print("\ncanonical key for Gusher:", pk)

edges = get_edges(pk)
print(f"\nedges cached for this part: {len(edges)}")
for e in edges:
    print("  ", json.dumps(e)[:200])

# show any other gusher-ish keys
print("\nall keys containing 'gusher' or '84004':")
for k in d:
    if "gusher" in k.lower() or "84004" in k:
        print("  ", k, "->", len(d[k]) if isinstance(d[k], list) else type(d[k]))

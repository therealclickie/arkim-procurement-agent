import json
p = "utils/known_parts.json"
d = json.load(open(p))
key = "gusher pumps|8400428C238CBC"
if key in d:
    del d[key]
    json.dump(d, open(p, "w"), indent=2)
    print(f"REMOVED '{key}'. Remaining keys: {list(d.keys())}")
else:
    print("key not found:", list(d.keys()))

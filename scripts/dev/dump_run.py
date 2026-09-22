import sqlite3, json

def show(path, table, limit=2):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(f"select * from {table} order by rowid desc limit {limit}"))
    print(f"\n{'='*70}\n{path} :: {table}  ({len(rows)} most recent)\n{'='*70}")
    if not rows:
        print("(empty)")
        return
    print("columns:", list(rows[0].keys()))
    for i, row in enumerate(rows):
        print(f"\n----- record {i} -----")
        for k, v in dict(row).items():
            s = str(v)
            if len(s) > 6000:
                s = s[:6000] + f"\n...[truncated, total {len(str(v))} chars]"
            print(f"\n### {k}:\n{s}")

# the sourcing run — this should hold the tier results
show("data/sourcing_runs.sqlite", "sourcing_runs", 1)

# the capture events
show("data/run_capture.sqlite", "run_events", 3)
show("data/run_capture.sqlite", "run_outcomes", 2)

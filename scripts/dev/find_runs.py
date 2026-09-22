import sqlite3, glob
for p in glob.glob("data/*.sqlite") + glob.glob("data/*.db"):
    try:
        c = sqlite3.connect(p)
        tabs = [t[0] for t in c.execute("select name from sqlite_master where type='table'")]
        print(f"{p}: {tabs}")
    except Exception as e:
        print(f"{p}: ERR {e}")

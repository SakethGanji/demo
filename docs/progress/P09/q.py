"""Run SQL against the metadata DB (the sqlite3 CLI is not installed here): python q.py <db> "<sql>" …"""
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
for q in sys.argv[2:]:
    try:
        for r in con.execute(q).fetchall():
            print(" | ".join(str(x) for x in r))
    except Exception as e:
        print("ERROR:", e)
    con.commit()

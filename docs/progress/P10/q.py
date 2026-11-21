"""SQL from Python (no sqlite3 CLI here). usage: q.py <db> "<sql>" ..."""
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
for q in sys.argv[2:]:
    for r in con.execute(q):
        print(" ", *r)

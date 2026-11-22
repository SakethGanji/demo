"""U3 (F-X4b hand-off) set-up: an SQLite file in the import dir, and Credit Risk (Ana member+, Vic viewer) with project
PD 2026. Usage: python sql_setup.py <api> <import dir> <state.json>"""
import json, sqlite3, sys
from pathlib import Path
import httpx
B = sys.argv[1].rstrip("/") + "/api"; D = Path(sys.argv[2]); D.mkdir(parents=True, exist_ok=True)
db = D / "branches.sqlite"; db.unlink(missing_ok=True)
con = sqlite3.connect(db); con.execute("CREATE TABLE branches (branch_id TEXT, region TEXT, opened TEXT, loans INTEGER, defaults INTEGER)")
con.executemany("INSERT INTO branches VALUES (?,?,?,?,?)", [(f"B{i:03d}", ["north", "south", "east", "west"][i % 4], f"20{10 + i % 12}-0{1 + i % 9}-15", 200 + 7 * i, 3 + i % 11) for i in range(120)])
con.commit(); con.close()
c = httpx.Client(timeout=120); h = lambda w: {"X-Ensemble-Actor": w}  # noqa: E731
cr = c.post(B + "/teams", json={"name": "Credit Risk", "by": "Ada"}, headers=h("Ada")).json()["id"]
for who, role in (("Ana", "maintainer"), ("Vic", "viewer")):
    c.post(f"{B}/teams/{cr}/members", json={"name": who, "role": role, "by": "Ada"}, headers=h("Ada"))
pd = c.post(B + "/projects", json={"name": "PD 2026", "team_id": cr, "by": "Ana"}, headers=h("Ana")).json()["id"]
st = {"cr": cr, "pd": pd, "db": str(db)}; Path(sys.argv[3]).write_text(json.dumps(st)); print(json.dumps(st))

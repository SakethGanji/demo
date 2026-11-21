import sqlite3, json, sys
con = sqlite3.connect(sys.argv[1]); rid = sys.argv[2]
r = json.loads(con.execute("SELECT result FROM runs WHERE id = ?", (rid,)).fetchone()[0]); r.pop("doctor_cache", None); r.pop("doctor", None)
con.execute("UPDATE runs SET result = ? WHERE id = ?", (json.dumps(r), rid)); con.commit(); print("doctor_cache cleared (doctor_gaps kept: quick_curve ci", r["doctor_gaps"]["data"]["quick_curve"]["ci"], ")")

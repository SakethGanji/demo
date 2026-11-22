import sys; sys.path.insert(0, "."); from x3 import *
F = sys.argv[1]
up, err = upload([("edge.xlsx", F)], name="x3 excel")
s, c = check(up); print("check", s)
if s != 200: print(c); raise SystemExit
print("blocks", c.get("blocks")); print("warnings", [(w["code"], w.get("table"), w["message"][:120]) for w in c.get("warnings", [])])
print("sheets", c.get("sheets"))
for t in c["tables"]: print(" table", repr(t["name"]), t.get("n_rows"), [(x["name"], x["type"]) for x in t["schema"]])
s, m = commit(up); print("commit", s, str(m)[:300] if s >= 300 else m["version_id"])
if s in (200, 201):
    V = m["version_id"]; s, d = version(V)
    for t in d["tables"]:
        s, g = grid(V, t["name"]); print(" rows", repr(t["name"]), s, [{k: v for k, v in x.items() if not k.startswith("_")} for x in g[:4]] if s == 200 else g)

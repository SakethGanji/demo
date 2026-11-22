import sys; sys.path.insert(0, "."); from x3 import *
csv = "k,d\n1,03/02/2024\n2,04/02/2024\n3,05/06/2024\n"
up, _ = upload([("t.csv", csv.encode())], name="x3 ambiguous plain"); s, c = check(up)
print("plain upload of 03/02/2024…: types", [(x["name"], x["type"]) for x in c["tables"][0]["schema"]] if "tables" in c else c, "warnings", [(w["code"], w["message"][:140]) for w in c.get("warnings", [])])
# US-style delta: 02/13/2024 (only reads as M/D) mixed with 02/03/2024
csv = "k,d,target\n" + "".join(f"{i},2024-01-{1+i%28:02d},{i%2}\n" for i in range(30))
r = chain([("t.csv", csv.encode())], name="x3 date delta US", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); V = k["version_id"]
Vx = delta("US dates 02/03/2024 + 02/13/2024 in a delta", "k,d,target\n1,02/03/2024,1\n2,02/13/2024,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", [x for x in g if x["k"] in (1, 2)])
Vx = delta("US dates only ambiguous 02/03/2024, 02/04/2024", "k,d,target\n1,02/03/2024,1\n2,02/04/2024,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", [x for x in g if x["k"] in (1, 2)])

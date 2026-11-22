import sys; sys.path.insert(0, "."); from x3 import *
# A: the parent has a real data column named _op
csv = "k,_op,v,target\n" + "".join(f"{i},{'UDI'[i%3]},{i},{i%2}\n" for i in range(30))
r = chain([("t.csv", csv.encode())], name="x3 op data col", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); V = k["version_id"]
Vx = delta("parent has a data column _op; delta sends _op=D as data", "k,_op,v,target\n1,D,100,1\n2,D,200,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", len(g), [x for x in g if x["k"] in (1, 2)])
# B: int key parent, zero-padded delta keys
csv = "k,v,target\n" + "".join(f"{i},{i},{i%2}\n" for i in range(30))
r = chain([("t.csv", csv.encode())], name="x3 int key", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); V = k["version_id"]
Vx = delta("int key parent, delta keys '007','008'", "k,v,target\n007,700,1\n008,800,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", len(g), [x for x in g if x["k"] in (7, 8)])
# C: plan key naming a parent column with NULLs
csv = "k,alt,v,target\n" + "".join(f"{i},{'' if i == 3 else i},{i},{i%2}\n" for i in range(30))
r = chain([("t.csv", csv.encode())], name="x3 null key", target="target"); V = r["commit"]["version_id"]
delta("plan key = alt (a NULL in the parent)", "k,alt,v,target\n1,1,5,1\n2,2,5,0\n", V, spec={"key": ["alt"]}, table="t")
# D: dates in another format in a delta
csv = "k,d,target\n" + "".join(f"{i},2024-01-{1+i%28:02d},{i%2}\n" for i in range(30))
r = chain([("t.csv", csv.encode())], name="x3 date delta", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); V = k["version_id"]
Vx = delta("dates as 03/02/2024 in a delta of a DATE column", "k,d,target\n1,03/02/2024,1\n2,04/02/2024,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", [x for x in g if x["k"] in (1, 2)])
Vx = delta("dates as Excel serials 45000 in a delta of a DATE column", "k,d,target\n1,45000,1\n2,45001,0\n", V, table="t")
if Vx: s, g = grid(Vx, "t"); print("  rows", [x for x in g if x["k"] in (1, 2)])

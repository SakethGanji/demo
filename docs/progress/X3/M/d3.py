import sys; sys.path.insert(0, "."); from x3 import *
V2 = json.load(open("V.json"))["V2"]
# base row i: amt 100+i, seg 'ab'[i%2], target i%2 → 00004: 104,a,0 ; 00005: 105,b,1
V6 = delta("true no-op delta", "acct,amt,seg,target\n00004,104,a,0\n00005,105,b,1\n", V2)
s, d = version(V6); print("  returned", V6, "v", d.get("version_no"), [(t["name"], t["change"], t["table_sha256"][:12]) for t in d["tables"]], "parent is", V2, V6 == V2)
V7 = delta("true no-op with float spelled 104.0", "acct,amt,seg,target\n00004,104.0,a,0\n00005,105.0,b,1\n", V2)
print("  ", V7 == V2)
V8 = delta("float 104.4 (rounds to parent 104)", "acct,amt,seg,target\n00004,104.4,a,0\n00005,105.4,b,1\n", V2)
print("  same as parent (value change swallowed)?", V8 == V2)
if V8 and V8 != V2: show(V8, {"00004", "00005"})
# plain (non-delta) new version with floats into the BIGINT column
base = "acct,amt,seg,target\n" + "".join(f"{i:05d},{100+i}.6,{'ab'[i%2]},{i%2}\n" for i in range(1, 41))
r = chain([("loans.csv", base.encode())], parent=V2, plan={"table": "loans"})
print("\n### plain new version with amt = 101.6 …", r["stage"], r.get("check_status"), r.get("commit_status"))
c = r["check"]; print("  blocks", c.get("blocks"), "warnings", [(w["code"], w["message"][:120]) for w in c.get("warnings", [])])
print("  schema", [[(s["name"], s["type"]) for s in t["schema"]] for t in c.get("tables", [])])
if r.get("commit_status") in (200, 201): show(r["commit"]["version_id"], {"00001", "00002"})

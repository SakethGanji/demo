"""F-X5b J1: F-X3a M1 (key_invalidated / relation_dropped + acknowledge) on POST …/edit. A = API base (env).
Same fixture as X5/x3/m1_edit.py: apps (cust_id → customers) n:1, customers key cust_id."""
import sys
sys.path.insert(0, "/home/user/demo/docs/progress/F-X3a")
from x3 import *  # noqa: F401,F403

def short(x, n=260):
    return str(x)[:n]

apps = "app_id,cust_id,amount,target\n" + "".join(f"A{i:04d},C{i%20:03d},{1000+i},{i%2}\n" for i in range(200))
cust = "cust_id,region,score\n" + "".join(f"C{i:03d},{'NS'[i%2]},{500+i}\n" for i in range(20))
r = chain([("apps.csv", apps.encode()), ("customers.csv", cust.encode())], name="fx5b j1 edit", target="target")
V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/customers/key", {"columns": ["cust_id"]}); V = k["version_id"]
s, rr = put(f"/datasets/{V}/relations/app_cust", {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]}, "cardinality": "n:1"})
VR = rr["version_id"]; print("parent: key customers.cust_id + relation app_cust →", s, VR)

dup = {"table": "customers", "ops": [{"op": "add_rows", "rows": [{"cust_id": "C001", "region": "S", "score": 1}]}], "reason": "fx5b j1 edit", "by": "Ana"}
s, p = post(f"/datasets/{VR}/edit", {**dup, "preview": True})
print("1. preview, add_rows repeating C001 →", s, "warnings", [(w["code"], w.get("needs_ack"), w.get("why")) for w in p["checks"]["warnings"]],
      "needs_acknowledge", p.get("needs_acknowledge"), "would_be_default", p.get("would_be_default"))
s, e = post(f"/datasets/{VR}/edit", dup)
print("2. commit, no acknowledge →", s, e["detail"]["code"], "acknowledge", e["detail"]["acknowledge"])
s, e = post(f"/datasets/{VR}/edit", {**dup, "acknowledge": ["key_invalidated"]})
print("3. commit, acknowledge key_invalidated only →", s, e["detail"]["code"], "acknowledge", e["detail"]["acknowledge"])
s, e = post(f"/datasets/{VR}/edit", {**dup, "acknowledge": ["key_invalidated", "relation_dropped"]})
VE = e.get("id"); print("4. commit, both acknowledged →", s, VE)
s, d = version(VE)
print("   keys", [(t["name"], [(x["columns"], x["kind"], x["valid"]) for x in t["keys"]]) for t in d["tables"] if t["name"] == "customers"])
s, rl = get(f"/datasets/{VE}/relations"); print("   relations", rl.get("relations"))
s, mf = get(f"/datasets/{VE}/manifest")
ops = (mf.get("manifest") or mf).get("provenance", {}).get("ops", []) if isinstance(mf, dict) else []
print("   manifest ops", [o for o in ops if "relation" in str(o.get("op"))])
print("   edit warnings", [(w["code"], w.get("acknowledged")) for w in (e.get("edit") or {}).get("checks", {}).get("warnings", [])])

ok = {"table": "customers", "ops": [{"op": "add_rows", "rows": [{"cust_id": "C999", "region": "S", "score": 1}]}], "reason": "fx5b j1 ok", "by": "Ana"}
s, e = post(f"/datasets/{VR}/edit", ok)
VO = e.get("id"); print("5. control: add_rows with a new cust_id, no acknowledge →", s, VO)
s, rl = get(f"/datasets/{VO}/relations"); print("   relations", [x["name"] for x in rl.get("relations")])

drop = {"table": "customers", "ops": [{"op": "drop_column", "column": "cust_id"}], "reason": "fx5b j1 drop", "by": "Ana"}
s, p = post(f"/datasets/{VR}/edit", {**drop, "preview": True})
print("6. preview, drop the key column →", s, [(w["code"], w.get("why")) for w in p.get("checks", {}).get("warnings", [])] if s == 200 else short(p))
s, e = post(f"/datasets/{VR}/edit", drop)
print("7. commit drop, no acknowledge →", s, short(e.get("detail") if isinstance(e, dict) else e, 200))

apps_ed = {"table": "apps", "ops": [{"op": "add_rows", "rows": [{"app_id": "A9999", "cust_id": "C777", "amount": 1, "target": 0}]}], "reason": "fx5b j1 orphan", "by": "Ana"}
s, e = post(f"/datasets/{VR}/edit", apps_ed)
VA = e.get("id"); print("8. control: from-side edit adds an orphan (relation kept, orphans counted) →", s, VA)
s, rl = get(f"/datasets/{VA}/relations"); print("   relations", [(x["name"], x["n_orphans"]) for x in rl.get("relations")])
s, kk = put(f"/datasets/{VR}/tables/customers/key", {"columns": ["region"], "kind": "unique"})
print("9. PUT key on a repeating column →", s, short(kk.get("detail") if isinstance(kk, dict) else kk, 160))
s, kk = put(f"/datasets/{VR}/tables/customers/key", {"columns": ["score"], "kind": "unique"})
s2, rl = get(f"/datasets/{kk['version_id']}/relations")
print("10. PUT a second key (unique score) → relation kept", s, [x["name"] for x in rl.get("relations")])

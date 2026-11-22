"""X5/x3 M1 neighbour: does a typed EDIT (not an upload) that repeats a declared key drop the n:1 relation without an
acknowledgement? (F-X3a: "the edit path and key/relation PUTs don't set relations_acknowledged".) A = API base (env)."""
import sys
sys.path.insert(0, "/home/user/demo/docs/progress/F-X3a")
from x3 import *  # noqa: F401,F403
apps = "app_id,cust_id,amount,target\n" + "".join(f"A{i:04d},C{i%20:03d},{1000+i},{i%2}\n" for i in range(200))
cust = "cust_id,region,score\n" + "".join(f"C{i:03d},{'NS'[i%2]},{500+i}\n" for i in range(20))
r = chain([("apps.csv", apps.encode()), ("customers.csv", cust.encode())], name="x5 m1 edit", target="target")
V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/customers/key", {"columns": ["cust_id"]}); V = k["version_id"]
s, rr = put(f"/datasets/{V}/relations/app_cust", {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]}, "cardinality": "n:1"})
VR = rr["version_id"]; print("parent: key customers.cust_id + relation app_cust →", s, VR)
body = {"table": "customers", "ops": [{"op": "add_rows", "rows": [{"cust_id": "C001", "region": "S", "score": 1}]}], "reason": "x5 m1 edit", "by": "Ana"}
s, p = post(f"/datasets/{VR}/edit", {**body, "preview": True}); print("edit preview (repeats C001) →", s, str(p)[:300])
s, e = post(f"/datasets/{VR}/edit", body); print("edit commit (no acknowledge) →", s, str(e)[:300])
VE = e.get("id") or e.get("version_id") if isinstance(e, dict) else None
if VE:
    s, d = version(VE)
    print("  keys", [(t["name"], [(x["columns"], x["kind"], x["valid"]) for x in t["keys"]]) for t in d["tables"] if t["name"] == "customers"])
    s, rl = get(f"/datasets/{VE}/relations"); print("  relations", rl.get("relations"))
    s, mf = get(f"/datasets/{VE}/manifest")
    ops = (mf.get("manifest") or mf).get("provenance", {}).get("ops", []) if isinstance(mf, dict) else []
    print("  manifest ops", [o for o in ops if "relation" in str(o.get("op"))])
    print("  edit response warnings", e.get("warnings"))

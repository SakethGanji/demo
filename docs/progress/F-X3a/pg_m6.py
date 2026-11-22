"""F-X3a (X3 m6): the Postgres-only 500s: a numeric version_id in POST …/joins and …/validate, and %00 in a dataset path."""
import sys, requests
sys.path.insert(0, ".")
from x3 import *  # noqa: F401,F403

apps = "app_id,cust_id,amount,target\n" + "".join(f"A{i:04d},C{i%20:03d},{1000+i},{i%2}\n" for i in range(200))
cust = "cust_id,region,score\n" + "".join(f"C{i:03d},{'NS'[i%2]},{500+i}\n" for i in range(20))
r = chain([("apps.csv", apps.encode()), ("customers.csv", cust.encode())], name="fx3a pg", target="target")
V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/customers/key", {"columns": ["cust_id"]}); V = k["version_id"]
s, d = get(f"/datasets/{V}"); R = d.get("root_id") or V
spec = {"grain": "apps", "joins": [{"type": "n:1", "table": "customers", "on": ["cust_id"], "prefix": "c__"}]}
s, j = post(f"/datasets/{R}/joins", {"name": "fx3a_j", "spec": spec}); print("save join", s); J = j.get("id") or j.get("join_id")
for body in ({"name": "fx3a_v", "version_id": 5, "spec": spec}, {"name": "fx3a_v", "version_id": {"a": 1}, "spec": spec}):
    s, x = post(f"/datasets/{R}/joins", body); print("POST joins version_id", repr(body["version_id"]), "→", s, str(x)[:120])
for vid in (5, 1.5, "ds_nope"):
    s, x = post(f"/datasets/{R}/joins/{J}/validate", {"version_id": vid}); print("validate version_id", repr(vid), "→", s, str(x)[:120])
for path in (f"/datasets/ds_%00", f"/datasets/{V}%00", f"/datasets/ds_%00/versions", f"/datasets/{V}/diff/ds_%00", f"/datasets/{V}/tables/customers%00/key"):
    r = requests.get(A + path, headers=H) if "key" not in path else requests.put(A + path, headers=H, json={"columns": ["x"]})
    print(r.request.method, path, "→", r.status_code, r.text[:100])

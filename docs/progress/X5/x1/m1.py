"""M1: v1 (3,000 accounts) -> 3 sibling next-version uploads, each adding 50 *different* accounts, committed at once."""
import numpy as np, threading, json
from c import *
rng = np.random.default_rng(7)
N = 3000
acct = [f"A{i:05d}" for i in range(N)]
x1 = rng.normal(size=N).round(4); y = (rng.random(N) < .3).astype(int)
def csv(ac, xs, ys): return ("acct,x1,y\n" + "".join(f"{a},{x},{v}\n" for a, x, v in zip(ac, xs, ys))).encode()
u = upload(csv(acct, x1, y), "m1.csv")
st, ck = c("POST", f"/api/uploads/{u}/check", show=80)
st, v1 = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, show=120)
v1 = v1["version_id"]
ups = []
for k in range(3):
    ac = acct + [f"Z{k}{i:04d}" for i in range(50)]
    xs = np.concatenate([x1, rng.normal(size=50).round(4)]); ys = np.concatenate([y, (rng.random(50) < .3).astype(int)])
    qs = f"filename=m1_{k}.csv&by=fx1&parent_version_id={v1}"
    st, uu = c("POST", f"/api/uploads?{qs}", csv(ac, xs, ys), quiet=True)
    uid = uu.get("upload_id"); c("PATCH", f"/api/uploads/{uid}", {"match": {"method": "key", "columns": ["acct"]}}, quiet=True)
    st, ck = c("POST", f"/api/uploads/{uid}/check", quiet=True); ups.append((uid, ck["check_sha256"]))
res = [None] * 3
def go(i):
    uid, sha = ups[i]
    res[i] = c("POST", f"/api/uploads/{uid}/commit", {"by": "fx1", "check_sha256": sha, "match": {"method": "key", "columns": ["acct"]}}, quiet=True)
ts = [threading.Thread(target=go, args=(i,)) for i in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
vs = []
for st, r in res:
    print("commit", st, r.get("version_id"), r.get("version_no"), (r.get("detail") if st >= 400 else ""))
    vs.append(r.get("version_id"))
import duckdb
for v in vs:
    st, rows = c("GET", f"/api/datasets/{v}/rows?after=2999&limit=100", quiet=True)
    ids = [r["_row_id"] for r in rows["rows"]]
    print(v, "new ids", min(ids), "-", max(ids), "n", len(ids), "accts", rows["rows"][0]["acct"], "...")
st, d = c("GET", f"/api/datasets/{vs[0]}/diff/{vs[1]}", show=0, quiet=True)
print("diff", vs[0], "vs", vs[1], json.dumps(d)[:300])
json.dump({"v1": v1, "siblings": vs}, open("m1_ids.json", "w"))

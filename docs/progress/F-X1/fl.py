"""C2 Feature Lab: setup | run <tag> (POST returns at once; poll the job) """
import json, sys, time
import numpy as np
from c import *
F = "fl.json"
CANDS = [{"op": "agg", "entity": "cust", "col": "a", "fn": "mean"}, {"op": "agg", "entity": "cust", "col": "a", "fn": "count"},
         {"op": "agg", "entity": "cust", "col": "b", "fn": "std"}, {"op": "ratio", "a": "a", "b": "b"}, {"op": "diff", "a": "a", "b": "b"},
         {"op": "date_part", "col": "d", "part": "dayofweek"}, {"op": "date_part", "col": "d", "part": "month"},
         {"op": "bin", "col": "b", "k": 5}, {"op": "log", "col": "b"}, {"op": "cross", "a": "c1", "b": "c2"}]
if sys.argv[1] == "setup":
    rng = np.random.default_rng(7); N = 10000
    cust = rng.integers(0, 400, N); eff = rng.normal(size=400)
    a = (eff[cust] + rng.normal(scale=.5, size=N)).round(3); b = rng.integers(-3, 10, N).astype(float); b[rng.random(N) < .05] = np.nan
    d = [f"2021-{m:02d}-{dd:02d}" for m, dd in zip(rng.integers(1, 13, N), rng.integers(1, 29, N))]
    c1, c2 = rng.choice(["p", "q", "r"], N), rng.choice(["s", "t", ""], N)
    y = (rng.random(N) < 1 / (1 + np.exp(-2 * eff[cust]))).astype(int)
    body = ("cust,a,b,d,c1,c2,y\n" + "".join(f"C{cust[i]:03d},{a[i]},{'' if np.isnan(b[i]) else int(b[i])},{d[i]},{c1[i]},{c2[i]},{y[i]}\n" for i in range(N))).encode()
    u = upload(body, "fl.csv"); c("PATCH", f"/api/uploads/{u}", {"target": "y"}, quiet=True)
    st, ck = c("POST", f"/api/uploads/{u}/check", quiet=True)
    st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, quiet=True)
    json.dump({"v": v["version_id"]}, open(F, "w")); print("v", v["version_id"])
else:
    d = json.load(open(F)); v = sys.argv[3] if len(sys.argv) > 3 else d["v"]
    body = {"by": "fx1", "candidates": CANDS, "max_candidates": 10, "time_limit": 60, "entity": "cust"}
    with Peak(kill_gb=8):
        st, j = c("POST", f"/api/datasets/{v}/feature-lab", body, show=200)
        t0 = time.time()
        while True:
            st, x = c("GET", f"/api/datasets/{v}/feature-lab/{j['id']}", quiet=True)
            if x.get("status") in ("done", "failed", "succeeded"): break
            time.sleep(2)
    res = x.get("result") or {}
    print(f"job {x['status']} after {time.time() - t0:.0f}s; kept {[t['name'] for t in res.get('kept') or []]}; sample {res.get('sample', {}).get('rows') if res.get('sample') else None}; version {x.get('version')}; error {x.get('error')}")
    d[sys.argv[2]] = {"job": j["id"], "version": (x.get("version") or {}).get("id"), "kept": res.get("kept")}
    json.dump(d, open(F, "w"))

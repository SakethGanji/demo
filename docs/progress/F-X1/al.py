"""F-X1 extra: active learning (pool = missing) on a __row_id-gapped version with a group split."""
import json, sys, time
import numpy as np
from c import *
rng = np.random.default_rng(5); N = 3000
cust = rng.integers(0, 300, N); x1, x2 = rng.normal(size=N).round(4), rng.normal(size=N).round(4)
y = (rng.random(N) < 1 / (1 + np.exp(-(2 * x1 - x2)))).astype(int).astype(str).astype(object); y[rng.random(N) < .1] = ""
body = ("cust,x1,x2,y\n" + "".join(f"C{cust[i]:03d},{x1[i]},{x2[i]},{y[i]}\n" for i in range(N))).encode()
u = upload(body, "al.csv"); c("PATCH", f"/api/uploads/{u}", {"target": "y"}, quiet=True)
st, ck = c("POST", f"/api/uploads/{u}/check", quiet=True)
st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, quiet=True); v1 = v["version_id"]
st, e = c("POST", f"/api/datasets/{v1}/edit", {"by": "fx1", "reason": "F-X1 AL: every 7th row deleted", "ops": [{"op": "delete_rows", "row_ids": list(range(0, N, 7))}]}, quiet=True)
child = e.get("version_id") or e.get("id"); print("v1", v1, "gapped child", child)
st, r = c("POST", "/api/runs", {"dataset_id": child, "label": "y", "time_limit": 20, "presets": "medium_quality", "name": "fx1-al",
                                "by": "fx1", "split_spec": {"strategy": "group", "group_column": "cust"}}, show=120)
rid = r["id"]
for _ in range(100):
    st, x = c("GET", f"/api/runs/{rid}", quiet=True)
    if x["status"] in ("succeeded", "failed"): break
    time.sleep(3)
print("run", rid, x["status"], "missing-label train rows", (x.get("result") or {}).get("data", {}).get("dropped_missing_label_train"), x.get("error"))
st, a = c("POST", f"/api/runs/{rid}/active-learning", {"pool": "missing", "budget": 10, "rounds": 1, "time_limit": 15}, show=150)
for _ in range(100):
    st, a = c("GET", f"/api/runs/{rid}/active-learning", quiet=True)
    s = (a.get("state") or a).get("status")
    if s in ("done", "failed", "ready", "waiting", "succeeded") or a.get("error"): break
    time.sleep(3)
q = ((a.get("state") or a).get("arms") or {}).get("queue") or {}
rows = [it.get("row") for it in q.get("queue") or []]
print("active learning:", s, "error", (a.get("state") or a).get("error"), "queue rows", rows)
bad = 0
for row in rows:  # each queued row is a training row of the gapped version with an empty label
    st, g = c("GET", f"/api/datasets/{child}/rows?after={row - 1}&limit=1", quiet=True)
    got = g["rows"][0]
    bad += not (got["_row_id"] == row and got["y"] in (None, ""))
print(f"queued rows that are blank-label rows of the version: {len(rows) - bad} of {len(rows)}")

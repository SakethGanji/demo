"""reg.py <port> train <dataset> <label> | register <run> <name> → dumps model json to stdout summary"""
import json, sys, time, urllib.request, urllib.error
P = sys.argv[1]; B = f"http://127.0.0.1:{P}/api"; H = {"X-Ensemble-Actor": "Ada"}
def c(m, p, body=None):
    h = dict(H); d = None
    if body is not None: d = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=d, method=m, headers=h), timeout=3600) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, e.read()[:800]
if sys.argv[2] == "train":
    body = {"dataset_id": sys.argv[3], "label": sys.argv[4], "time_limit": 30, "by": "Ada", **(json.loads(sys.argv[5]) if len(sys.argv) > 5 else {})}
    st, r = c("POST", "/runs", body); print(st, str(r)[:300]); rid = r["id"]
    while True:
        st, x = c("GET", f"/runs/{rid}")
        if x.get("status") in ("succeeded", "failed", "cancelled"): break
        time.sleep(4)
    print("RUN", rid, x.get("status"), (x.get("error") or "")[:300])
else:
    rid, name = sys.argv[3], sys.argv[4]
    st, g = c("POST", f"/runs/{rid}/register", {"created_by": "Ada", "model_name": name}); print(st, str(g)[:300]); mid = g.get("id")
    t0 = time.time()
    while True:
        st, m = c("GET", f"/registry/versions/{mid}")
        if m.get("status") not in ("building", "queued", "pending", "running") or time.time() - t0 > 1500: break
        time.sleep(3)
    print("MODEL", mid, m.get("status"), str(m.get("error"))[:300])
    json.dump(m, open(sys.argv[5], "w"), indent=1, sort_keys=True, default=str)

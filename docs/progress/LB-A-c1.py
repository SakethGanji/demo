import csv, io, json, sys, time, random, httpx
B, S = sys.argv[1], sys.argv[2]
c = httpx.Client(base_url=B, timeout=600); A = {"X-Ensemble-Actor": "ana"}
def j(r, code=None):
    if code and r.status_code != code:
        print("UNEXPECTED", r.status_code, r.text[:600]); sys.exit(1)
    return r.json()
random.seed(2); cats = ["A", "B", "C"]
buf = io.StringIO(); w = csv.writer(buf); w.writerow(["acct", "x1", "x2", "y"])
for i in range(1500):
    x1 = random.random(); x2 = random.random()
    w.writerow([f"K{i:05d}", round(x1, 4), round(x2, 4), "" if i % 10 == 0 else cats[int(x1 * 3) % 3]])
v1 = j(c.post("/datasets", headers=A, files={"file": ("big.csv", buf.getvalue())}, data={"target": "y", "by": "ana"}), 201)["id"]
spec = {"columns": [{"column": "y", "role": "target", "type": "class", "classes": cats}]}
L = j(c.post(f"/datasets/{v1}/label-layers", headers=A, json={"name": "y", "spec": spec}), 201)["id"]
ev = [{"row_id": i, "column": "y", "value": cats[i % 3]} for i in range(0, 1500, 10)]  # label the 150 unlabelled rows
ev2 = [{"row_id": i, "column": "y", "value": "C"} for i in range(1, 400, 10)]          # relabel 40 labelled rows to C
j(c.post(f"/label-layers/{L}/events", headers=A, json={"events": ev}), 201)
j(c.post(f"/label-layers/{L}/events", headers=A, json={"events": ev2}), 201)
sn = j(c.post(f"/label-layers/{L}/snapshots", headers=A, json={}), 201)
print("snapshot", sn["id"], sn["counts"]["by_status"])
run = j(c.post("/runs", headers=A, json={"dataset_id": v1, "label": "y", "time_limit": 20, "feature_importance": False,
                                         "fit_kwargs": {"hyperparameters": {"GBM": {}}}, "labels": {"snapshot_id": sn["id"]}}), 201)["id"]
base = j(c.post("/runs", headers=A, json={"dataset_id": v1, "label": "y", "time_limit": 20, "feature_importance": False,
                                          "fit_kwargs": {"hyperparameters": {"GBM": {}}}}), 201)["id"]
for rid in (run, base):
    for _ in range(200):
        s = j(c.get(f"/runs/{rid}"))
        if s["status"] in ("succeeded", "failed"):
            break
        time.sleep(3)
    print("run", rid, s["status"], s.get("error"), "train_rows", (s.get("result") or {}).get("train_rows"))
vr = j(c.get(f"/label-runs/{run}/verify"))
print("verify:", vr["status"], "rows_applied", vr["rows_applied"], "labels_fitted", vr["labels_fitted"])
d = c.get(f"/runs/{run}/doctor", headers=A)
dj = d.json()
print("doctor overlay run:", d.status_code, "sampled:", json.dumps(dj.get("sampled") or dj.get("bounded") or dj.get("rows_info") or "")[:300])
db = c.get(f"/runs/{base}/doctor", headers=A)
print("doctor base run:", db.status_code)
g = c.get(f"/runs/{run}/export.py", headers=A)
print("gym export overlay run:", g.status_code, (g.json().get("detail") or {}).get("code") if g.status_code != 200 else "")
gb = c.get(f"/runs/{base}/export.py", headers=A)
print("gym export base run:", gb.status_code)
r = c.post(f"/runs/{run}/register", headers=A, json={"created_by": "ana", "model_name": "lbA.c1"})
print("register:", r.status_code, r.text[:200] if r.status_code >= 300 else "")
json.dump({"run": run, "base": base, "v1": v1, "reg": r.json() if r.status_code < 300 else None}, open(f"{S}/c1.json", "w"))

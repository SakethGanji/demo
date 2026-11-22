"""LB-A curl chain as httpx calls against a running API (SQLite)."""
import csv, json, sys, time, random, io
import httpx

B = sys.argv[1]
S = sys.argv[2]
c = httpx.Client(base_url=B, timeout=300)
A = {"X-Ensemble-Actor": "ana"}
Bob = {"X-Ensemble-Actor": "bob"}
out = {}

def j(r, code=None):
    if code and r.status_code != code:
        print("UNEXPECTED", r.status_code, r.text[:500]); sys.exit(1)
    return r.json()

random.seed(1)
cats = ["Account", "Billing", "Fraud", "Other"]
buf = io.StringIO(); w = csv.writer(buf); w.writerow(["acct", "amount", "region", "category"])
for i in range(200):
    w.writerow([f"A{i:04d}", random.randint(10, 5000), random.choice("NSEW"), random.choice(cats) if i % 5 else ""])
v1 = j(c.post("/datasets", headers=A, files={"file": ("tickets.csv", buf.getvalue())}, data={"target": "category", "by": "ana"}), 201)["id"]
print("1 upload v1", v1)
spec = {"columns": [{"column": "category", "role": "target", "type": "class", "classes": cats},
                    {"column": "amount", "role": "feature", "type": "number", "min": "0"}]}
L = j(c.post(f"/datasets/{v1}/label-layers", headers=A, json={"name": "category", "spec": spec}), 201)["id"]
ev = [{"row_id": i, "column": "category", "value": cats[(i // 5) % 4]} for i in range(0, 200, 5)]
ev += [{"row_id": 1, "column": "category", "value": "Billing"}, {"row_id": 3, "column": "amount", "value": 42}]
r = j(c.post(f"/label-layers/{L}/events", headers=A, json={"events": ev}), 201)
print("2 batch append", r["appended"], "seqs", r["first_seq"], r["last_seq"])
j(c.post(f"/label-layers/{L}/events", headers=Bob, json={"row_id": 2, "column": "category", "value": "Account"}), 201)
j(c.post(f"/label-layers/{L}/events", headers=A, json={"row_id": 2, "column": "category", "value": "Fraud"}), 201)
for name, body in [("bad class", {"row_id": 4, "column": "category", "value": "Bogus"}),
                   ("bad source", {"row_id": 4, "column": "category", "value": "Fraud", "source": "bogus"}),
                   ("missing row", {"row_id": 99999, "column": "category", "value": "Fraud"})]:
    r = c.post(f"/label-layers/{L}/events", headers=A, json=body)
    print("  ", name, r.status_code, r.json()["detail"]["code"])
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": v1}))["totals"]
print("  status v1", {k: st[k] for k in ("ok", "disputed", "unlabelled")})
ed = {"by": "ana", "reason": "fixes", "make_default": "yes", "ops": [
    {"op": "set_cells", "cells": [{"row_id": 5, "column": "region", "value": "Q"}, {"row_id": 10, "column": "region", "value": "Q"},
                                  {"row_id": 3, "column": "amount", "value": 7}]},
    {"op": "delete_rows", "row_ids": [20, 25]}]}
v2 = j(c.post(f"/datasets/{v1}/edit", headers=A, json=ed), 201)["id"]
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": v2, "rows": 1}))
print("3 edit v2", v2, {k: v for k, v in st["totals"].items() if v})
print("   non-ok:", [(r["row_id"], r["column"], r["carry"]) for r in st["rows"] if r["carry"] != "ok"])
j(c.post(f"/label-layers/{L}/events", headers=Bob, json={"row_id": 5, "column": "category", "value": "Other", "version_id": v2}), 201)
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": v2, "rows": 1}))
print("   C2 re-review of stale row 5 on v2:", [(r["row_id"], r["value"], r["carry"], r["n_epochs"]) for r in st["rows"] if r["row_id"] == 5] if "n_epochs" in st["rows"][0] else [(r["row_id"], r["value"], r["carry"]) for r in st["rows"] if r["row_id"] == 5])
rows = j(c.get(f"/datasets/{v2}/rows", headers=A, params={"limit": 500}))["rows"]
def csvof(f):
    b = io.StringIO(); w = csv.writer(b); w.writerow(["acct", "amount", "region", "category"])
    for x in rows:
        w.writerow(f(x))
    return b.getvalue()
lossy = csvof(lambda x: [x["acct"], x["amount"] + 1 if x["_row_id"] % 5 == 0 else x["amount"], x["region"], x["category"] or ""])
r = c.post(f"/datasets/{v2}/versions", headers=A, files={"file": ("t.csv", lossy)},
           data={"by": "ana", "row_key": "hash", "make_default": "no", "acknowledge": "holdout_identity_loss"})
d = r.json()["detail"]
print("4 lossy re-upload (row hash) without ack:", r.status_code, d["code"], "label_identity_lost", d["label_identity_lost"]["n"])
r = c.post(f"/datasets/{v2}/versions", headers=A, files={"file": ("t.csv", lossy)},
           data={"by": "ana", "row_key": "hash", "make_default": "no", "acknowledge": "holdout_identity_loss,label_identity_loss"})
vl = j(r, 201)["id"]
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": vl}))["totals"]
print("   acked", vl, "match.label_identity_lost", (r.json().get("match") or r.json().get("upload") or {}).get("label_identity_lost", "n/a"), "status orphaned", st["orphaned"])
v3csv = csvof(lambda x: [x["acct"], x["amount"], x["region"], "Billing" if x["acct"] == "A0035" else (x["category"] or "")])
v3 = j(c.post(f"/datasets/{v2}/versions", headers=A, files={"file": ("t.csv", v3csv)},
              data={"by": "ana", "row_key": "acct", "make_default": "yes"}), 201)["id"]
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": v3, "rows": 1}))
print("   key re-upload v3", v3, "base_changed:", [(r["row_id"], r["carry"]) for r in st["rows"] if r["carry"] == "base_changed"])
sn = j(c.post(f"/label-layers/{L}/snapshots", headers=A, json={"policy": {"columns": ["category"]}}), 201)
print("5 snapshot", sn["id"], sn["counts"]["by_status"], "verify", j(c.get(f"/label-snapshots/{sn['id']}/verify"))["status"])
sn2 = j(c.post(f"/label-layers/{L}/snapshots", headers=A, json={"policy": {"columns": ["category"]}}), 201)
print("   rebuild: same values/events sha", sn2["values_sha256"] == sn["values_sha256"], sn2["events_sha256"] == sn["events_sha256"])
run = j(c.post("/runs", headers=A, json={"dataset_id": v3, "label": "category", "time_limit": 20, "feature_importance": False,
                                         "fit_kwargs": {"hyperparameters": {"GBM": {}}}, "labels": {"snapshot_id": sn["id"]}}), 201)["id"]
for _ in range(120):
    s = j(c.get(f"/runs/{run}"))
    if s["status"] in ("succeeded", "failed"):
        break
    time.sleep(3)
print("6 run", run, s["status"], s.get("error"))
vr = j(c.get(f"/label-runs/{run}/verify"))
print("   run overlay verify", vr["status"], "rows_applied", vr["rows_applied"])
json.dump({"v1": v1, "v2": v2, "v3": v3, "L": L, "sn": sn["id"], "run": run}, open(f"{S}/ids.json", "w"))
m = j(c.post(f"/label-snapshots/{sn['id']}/materialise", headers=A, json={"version_id": v3, "reason": "apply"}), 201)
print("7 materialise", m["version_id"], "rows_changed", m["rows_changed"], m["counts"])
diff = j(c.get("/datasets/diff", headers=A, params={"a": v3, "b": m["version_id"]}))
print("   diff changed rows", diff["rows"]["changed"])
st = j(c.get(f"/label-layers/{L}/status", params={"version_id": m["version_id"]}))["totals"]
print("   status on materialised version: absorbed", st["absorbed"], "conflict", st["conflict"], "base_changed", st["base_changed"])
p = j(c.post(f"/datasets/{m['version_id']}/purge", headers=A, json={"columns": ["category"], "reason": "test purge of a labelled column", "by": "ana"}), 201)
dd = j(c.post(f"/purges/{p['id']}/decide", headers=Bob, json={"team": "tm_default", "approve": True, "by": "bob"}))
ex = (dd.get("payload") or dd).get("executed") or {}
print("8 purge", p["id"], "labels:", ex.get("labels"), "db scrub error:", ((ex.get("derived") or {}).get("db") or {}).get("error"))
print("   sweep", (dd.get("sweep") or {}).get("status"), [h.get("table") or h.get("path") for h in (dd.get("sweep") or {}).get("hits") or []])
print("   run verify after purge", j(c.get(f"/label-runs/{run}/verify"))["status"],
      "| snapshot verify", j(c.get(f"/label-snapshots/{sn['id']}/verify"))["status"])
evs = j(c.get(f"/label-layers/{L}/events", params={"limit": 5000}))["events"]
print("   events with value left on category:", sum(1 for e in evs if e["column"] == "category" and e["value"] is not None),
      "| amount events kept:", [e["value"] for e in evs if e["column"] == "amount"])

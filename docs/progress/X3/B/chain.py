"""X3/B core chain driven over HTTP the way the UI calls it: multi-table upload (keys + relation) → key on bureau →
delta on applications (_op) → delta on bureau (upsert) → relation PUT → join → frame → join run → verify (dataset,
frame, run). Prints a normalised JSON record (no ids / timestamps) for engine/store parity.
Usage: chain.py <api base> <files dir> <out json> [label suffix]"""
import json, sys, time
import requests

A, F, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
H = {"X-Ensemble-Actor": "Ana"}; HA = {"X-Ensemble-Actor": "Ada"}
rec = {}
def j(r, ok=(200, 201, 202)):
    if r.status_code not in ok:
        raise SystemExit(f"{r.request.method} {r.url} → {r.status_code} {r.text[:600]}")
    return r.json()
def up(files, data=None, params=None, plan=None, commit=None):
    fs = [("file", (n, open(f"{F}/{n}", "rb"))) for n in files]
    u = j(requests.post(f"{A}/uploads", headers=H, files=fs, data=data or {}, params=params or {}))
    if plan: j(requests.patch(f"{A}/uploads/{u['upload_id']}", headers=H, json=plan))
    c = j(requests.post(f"{A}/uploads/{u['upload_id']}/check", headers=H))
    m = j(requests.post(f"{A}/uploads/{u['upload_id']}/commit", headers=H, json=commit or {"reason": "x3 chain"}))
    return c, m
def ver(root, vid):
    v = j(requests.get(f"{A}/datasets/{root}/versions/{vid}", headers=H))
    return {"data_sha256": v["data_sha256"], "tables": {t["name"]: [t["change"], t["n_rows"], t["table_sha256"], [k["columns"] for k in t["keys"]]] for t in v["tables"]},
            "relations": sorted((r["name"], r["n_orphans"]) for r in v.get("relations", []))}

c, m = up(["applications.csv", "bureau.csv", "payments.csv"], data={"name": "x3 book", "target": "defaulted"},
          plan={"tables": {"applications": {"key": ["app_id"]}, "payments": {"key": ["payment_id"]}},
                "relations": [{"name": "pay_app", "from": {"table": "payments", "columns": ["app_id"]}, "to": {"table": "applications", "columns": ["app_id"]}}]})
V1 = m["version_id"]; ROOT = V1
rec["check1"] = {"blocks": c["blocks"], "tables": [(t["name"], t["action"], t["n_rows"]) for t in c["tables"]]}
rec["v1"] = ver(ROOT, V1)
k = j(requests.put(f"{A}/datasets/{V1}/tables/bureau/key", headers=H, json={"columns": ["customer_id"], "reason": "one per customer"}))
V2 = k["version_id"]; rec["v2"] = ver(ROOT, V2)
c, m = up(["apps_delta.csv"], params={"parent_version_id": V2}, plan={"table": "applications", "tables": {"applications": {"action": "delta"}}})
V3 = m["version_id"]; rec["delta3"] = {t: {k_: x[k_] for k_ in ("mode", "change", "inserted", "updated", "deleted", "missing")} for t, x in m["deltas"].items()}
rec["v3"] = ver(ROOT, V3)
c, m = up(["bureau_delta.csv"], params={"parent_version_id": V3}, plan={"table": "bureau", "tables": {"bureau": {"action": "delta"}}})
V4 = m["version_id"]; rec["delta4"] = {t: {k_: x[k_] for k_ in ("mode", "change", "inserted", "updated", "deleted", "missing")} for t, x in m["deltas"].items()}
rec["v4"] = ver(ROOT, V4)
r = j(requests.put(f"{A}/datasets/{V4}/relations/app_bureau", headers=H, json={"from": {"table": "applications", "columns": ["customer_id"]}, "to": {"table": "bureau", "columns": ["customer_id"]}, "reason": "x3"}))
V5 = r["version_id"]; rec["v5"] = ver(ROOT, V5)
SPEC = {"grain": "applications", "joins": [{"type": "n:1", "table": "bureau", "on": ["customer_id"]},
        {"type": "aggregate", "table": "payments", "on": ["app_id"], "prefix": "pay__", "aggs": [{"name": "n_pay", "fn": "count"},
         {"name": "late_mean", "fn": "mean", "column": "days_late"}, {"name": "late_180d", "fn": "sum", "column": "days_late", "window": {"date": "paid_on", "relative_to": "applied_on", "days": 180}}]}]}
jn = j(requests.post(f"{A}/datasets/{ROOT}/joins", headers=H, json={"name": "apps_full", "spec": SPEC}))
rec["join"] = {"spec_sha256": jn["spec_sha256"], "coverage": [(x["table"], x["matched"], x["grain_rows"], x["orphans"]) for x in jn["validation"]["coverage"]]}
fr = j(requests.post(f"{A}/datasets/{ROOT}/versions/{V5}/frames", headers=H, json={"join_id": jn["id"]}))
rec["frame"] = {k_: fr[k_] for k_ in ("n_rows", "frame_sha256")} | {"inputs": [(x["role"], x["table"], x["table_sha256"]) for x in fr["inputs"]]}
fv = j(requests.get(f"{A}/datasets/{ROOT}/frames/{fr['id']}?verify=1", headers=H))
rec["frame_verify"] = fv["verify"]["ok"]
run = j(requests.post(f"{A}/runs", headers=H, json={"dataset_id": V5, "join_id": jn["id"], "label": "defaulted", "time_limit": 20, "presets": "medium_quality"}))
RID = run["id"]
for _ in range(240):
    s = j(requests.get(f"{A}/runs/{RID}", headers=H))
    if s["status"] not in ("queued", "running", "pending"): break
    time.sleep(3)
rec["run"] = {"status": s["status"], "data_sha256": s.get("data", {}).get("data_sha256"), "rows_used": s.get("data", {}).get("rows_used"),
              "read": (s.get("data", {}).get("read") or {}).get("kind"), "split_sha256": ((s.get("data") or {}).get("split") or {}).get("split_sha256")}
vr = requests.get(f"{A}/admin/lake/runs/{RID}/verify", headers=HA).json()
rec["run_verify"] = {"ok": vr.get("ok"), "data": (vr.get("data") or {}).get("ok"), "split": (vr.get("split") or {}).get("ok")}
dv = j(requests.get(f"{A}/datasets/{V5}/verify", headers=H))
rec["dataset_verify"] = dv["ok"]
rec["_ids"] = {"root": ROOT, "v": [V1, V2, V3, V4, V5], "join": jn["id"], "frame": fr["id"], "run": RID}
json.dump(rec, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k_: v for k_, v in rec.items() if k_ not in ("v1", "v2", "v3", "v4", "v5")}, default=str)[:3000])

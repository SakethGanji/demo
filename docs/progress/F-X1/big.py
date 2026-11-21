"""C2 at 10M rows. usage: big.py upload | doctor | apply | featurelab | labels   (ids in big.json)"""
import json, os, sys, time, urllib.request
from c import *
S = "$SCRATCH"
F = "big.json"
d = json.load(open(F)) if os.path.exists(F) else {}
what = sys.argv[1]
if what == "upload":
    p = f"{S}/seed10m.csv"
    with Peak(kill_gb=8):
        t = time.time()
        with open(p, "rb") as fh:
            r = urllib.request.Request(BASE + "/api/uploads?filename=seed10m.csv&by=fx1", data=fh, method="POST",
                                       headers={**ACT, "Content-Type": "text/csv", "Content-Length": str(os.path.getsize(p))})
            u = json.loads(urllib.request.urlopen(r, timeout=3600).read())["upload_id"]
        print(f"upload {u} {time.time() - t:.1f}s")
        c("PATCH", f"/api/uploads/{u}", {"target": "y", "name": "seed10m"}, quiet=True)
        st, ck = c("POST", f"/api/uploads/{u}/check", show=0)
        st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, show=0)
    d["v"] = v["version_id"]; print("version", d["v"], v.get("rows"))
elif what == "doctor":
    with Peak(kill_gb=8):
        st, r = c("GET", f"/api/datasets/{d['v']}/data-doctor", show=0)
    print("rows", r.get("rows"), "sample", r.get("sample"), "\nfindings", [(f["id"], f["severity"], f["title"][:70]) for f in r.get("findings", [])])
    print("timings", r.get("timings"))
    d["report_fixes"] = r.get("selected_fixes")
elif what == "labels":  # the label job (background thread) on the sample
    with Peak(kill_gb=8):
        for _ in range(120):
            st, r = c("GET", f"/api/datasets/{d['v']}/data-doctor", quiet=True)
            lf = next((f for f in r["findings"] if f["id"] == "label_quality"), {})
            if lf.get("status") != "running": break
            time.sleep(5)
    print("label_quality", lf.get("status"), lf.get("severity"), lf.get("title"), "\n  evidence rows", [x[:4] for x in (lf.get("evidence") or {}).get("rows", [])][:3],
          "\n  fix rows (first 5)", [t.get("rows", [])[:5] for t in lf["fix"]["transforms"]] if lf.get("fix") else None)
elif what == "apply":
    tr = json.loads(sys.argv[2]) if len(sys.argv) > 2 else [{"op": "drop_columns", "columns": ["note"]}]
    with Peak(kill_gb=8):
        st, r = c("POST", f"/api/datasets/{d['v']}/data-doctor/apply", {"transforms": tr, "by": "fx1"}, show=0)
    print(st, {k: r.get(k) for k in ("how", "rows_before", "rows_after", "columns_added", "columns_removed", "created", "effects")} if st < 300 else r)
    if st < 300: d.setdefault("applied", []).append(r["dataset"]["id"]); print("version", r["dataset"]["id"])
elif what == "featurelab":
    body = {"by": "fx1", "max_candidates": 2, "time_limit": 60, "apply": len(sys.argv) > 2}
    with Peak(kill_gb=8):
        st, j = c("POST", f"/api/datasets/{d['v']}/feature-lab", body, show=150)
        t0 = time.time()
        while True:
            st, x = c("GET", f"/api/datasets/{d['v']}/feature-lab/{j['id']}", quiet=True)
            if x.get("status") in ("done", "failed", "succeeded"): break
            time.sleep(3)
    res = x.get("result") or {}
    print(f"job {x['status']} {time.time() - t0:.0f}s after the 202; kept {[t['name'] for t in res.get('kept') or []]}; sample {(res.get('sample') or {}).get('rows')}; version {x.get('version')}; error {x.get('error')}")
json.dump(d, open(F, "w"))

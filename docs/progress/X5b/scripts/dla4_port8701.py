"""X5: DLA4's four open items, re-run. usage: dla4.py   (API on :8000, actor x5)"""
import io, json, sys, time, zipfile, urllib.request
import numpy as np
sys.path.insert(0, "/var/tmp/x5b/dla4")
import c as C
C.ACT["X-Ensemble-Actor"] = "x5"
from c import c, upload
rng = np.random.default_rng(11)
N = 3000
acct = [f"A{i:05d}" for i in range(N)]
zips = [f"{z:05d}" for z in rng.choice([2139, 501, 10001, 90210, 7030], N)]
x1 = rng.normal(size=N).round(4); amt = rng.integers(100, 9000, N)
y = ((x1 + rng.normal(size=N) * .8) > .5).astype(int)
def csv(ac, zz, xs, am, ys):
    return ("acct,zip,x1,amt,y\n" + "".join(f"{a},{z},{x},{m},{v}\n" for a, z, x, m, v in zip(ac, zz, xs, am, ys))).encode()
u = upload(csv(acct, zips, x1, amt, y), "dla4.csv")
c("PATCH", f"/api/uploads/{u}", {"target": "y", "name": "dla4"}, quiet=True)
st, ck = c("POST", f"/api/uploads/{u}/check", show=0)
print("check types", [(x["name"], x["type"]) for x in ck["tables"][0]["schema"]])
st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "x5", "check_sha256": ck["check_sha256"]}, show=0); V1 = v["version_id"]
print("## item 1: extreme values into an int column / add_column / fill_missing / replace_values")
vals = [1e30, 1e308, 2**63, 2**64, -2**63 - 1, 1.797e308, "1e400", "Infinity", "NaN"]
cnt = {}
for val in vals:
    for name, op in [("set_cells amt", {"op": "set_cells", "cells": [{"row_id": 1, "column": "amt", "value": val}]}),
                     ("set_cells x1", {"op": "set_cells", "cells": [{"row_id": 1, "column": "x1", "value": val}]}),
                     ("add_column", {"op": "add_column", "name": "k", "value": val}),
                     ("fill_missing amt", {"op": "fill_missing", "column": "amt", "value": val}),
                     ("replace_values amt", {"op": "replace_values", "column": "amt", "from": 100, "to": val}),
                     ("add_rows amt", {"op": "add_rows", "rows": [{"acct": "Q1", "zip": "00001", "x1": 0.1, "amt": val, "y": 0}]})]:
        st, r = c("POST", f"/api/datasets/{V1}/edit", {"by": "x5", "reason": "dla4", "preview": True, "ops": [op]}, quiet=True)
        cnt[st] = cnt.get(st, 0) + 1
        leak = any(s in json.dumps(r) for s in ("Traceback", "pandas", "numpy", "duckdb", "Cannot safely", "int64'>", "cast float64"))
        if st >= 500 or leak or (st == 200 and name.startswith("set_cells amt")):
            print(f"  {name} {val!r}: {st} leak={leak} {json.dumps(r)[:220]}")
print("  status counts", cnt)
print("## item 2: upload-as-version of a file whose key is a plain id (acct): no silent guess, rows keep identity")
x1b = x1.copy(); fix = rng.choice(N, 15, replace=False); x1b[fix] += 10
order = rng.permutation(N)
f2 = csv([acct[i] for i in order], [zips[i] for i in order], x1b[order], amt[order], y[order])
st, uu = c("POST", f"/api/uploads?filename=dla4_v2.csv&by=x5&parent_version_id={V1}", f2, quiet=True); u2 = uu["upload_id"]
st, ck2 = c("POST", f"/api/uploads/{u2}/check", show=0)
m = ck2.get("match") or (ck2["tables"][0].get("match") if ck2.get("tables") else None)
print("  check match candidates", json.dumps(m)[:400])
st, r = c("POST", f"/api/uploads/{u2}/commit", {"by": "x5", "check_sha256": ck2["check_sha256"]}, show=300)
st, r = c("POST", f"/api/uploads/{u2}/commit", {"by": "x5", "check_sha256": ck2["check_sha256"], "match": {"method": "key", "columns": ["acct"]}}, show=0)
V2 = r.get("version_id"); print("  commit with key acct ->", st, V2)
st, d = c("GET", f"/api/datasets/{V1}/diff/{V2}", show=0)
print("  diff", {k: d["rows"].get(k) for k in ("matched", "changed", "added", "removed", "matched_by")}, "changed_by_column", d["rows"].get("changed_by_column") or d.get("changed_by_column"))
print("## item 3: split tamper: see X5/x1/m26.txt, m2_forge_restore.txt; here: compare wording over a partial holdout overlap")
st, r1 = c("POST", "/api/runs", {"dataset_id": V1, "label": "y", "time_limit": 15, "drop_columns": ["acct"], "by": "x5"}, show=0)
# child version: delete 2/3 of the rows -> the carried holdout overlaps partially
st, rows = c("GET", f"/api/datasets/{V1}/rows?limit=3000", show=0)
ids = [rw["_row_id"] for rw in rows["rows"]]
st, e = c("POST", f"/api/datasets/{V1}/edit", {"by": "x5", "reason": "dla4 overlap", "ops": [{"op": "delete_rows", "row_ids": [i for i in ids if i % 3 != 0]}]}, show=200)
V3 = (e.get("id") or (e.get("dataset") or {}).get("id"))
st, r3 = c("POST", "/api/runs", {"dataset_id": V3, "label": "y", "time_limit": 15, "drop_columns": ["acct"], "by": "x5"}, show=0)
def wait(rid):
    for _ in range(200):
        st, x = c("GET", f"/api/runs/{rid}", quiet=True)
        if x.get("status") in ("succeeded", "failed"): return x
        time.sleep(3)
a, b = wait(r1["id"]), wait(r3["id"])
print("  runs", a["status"], b["status"])
st, cmp = c("GET", f"/api/compare?a={r1['id']}&b={r3['id']}", show=0)
txt = json.dumps(cmp)
import re
print("  compare paired wording:", re.findall(r'paired[^"]{0,120}', txt)[:3])
print("## item 4: ZIP stays text through explain, Model Doctor, slices, scoring, export")
st, ex = c("GET", f"/api/runs/{r1['id']}/explain?rows=0,1,2", show=0)
print("  explain", st, re.findall(r'"zip"[^,}]{0,30}', json.dumps(ex))[:4])
st, dr = c("GET", f"/api/runs/{r1['id']}/doctor", show=0, timeout=600)
print("  doctor", st, "zip mentions", re.findall(r'zip[^,}]{0,40}', json.dumps(dr))[:3])
st, sl = c("GET", f"/api/runs/{r1['id']}/slices", show=0, timeout=600)
print("  slices", st, re.findall(r'"zip[^}]{0,80}', json.dumps(sl))[:3])
st, rg = c("POST", f"/api/runs/{r1['id']}/register", {"created_by": "x5", "model_name": "dla4"}, show=200)
mv = rg.get("id")
for _ in range(100):
    st, mm = c("GET", f"/api/registry/versions/{mv}", quiet=True)
    if mm.get("status") not in ("building", "queued", "pending", "running"): break
    time.sleep(3)
print("  model", mm.get("status"), str(mm.get("error"))[:200])
st, sc = c("POST", f"/api/registry/versions/{mv}/score", {"rows": [{"zip": "02139", "x1": 1.2, "amt": 500}]}, show=0)
print("  score", st, re.findall(r'[Zz]ip[^,}\]]{0,40}', json.dumps(sc))[:4])
req = urllib.request.Request(f"http://localhost:8701/api/datasets/{V1}/export?format=zip", headers={"X-Ensemble-Actor": "x5"})
z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req).read()))
print("  export zip files", z.namelist(), "| data first rows", z.read([n for n in z.namelist() if n.endswith(".csv")][0]).decode().splitlines()[1:3],
      "| schema zip", [cc for cc in json.loads(z.read("schema.json")).get("columns", json.loads(z.read("schema.json")).get("tables", [{}])[0].get("columns", [])) if cc.get("name") == "zip"] if "schema.json" in z.namelist() else "NO schema.json")

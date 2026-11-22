"""X5/x2: after during_job.py: retry the refused run on A3, wait, verify it and the concurrent L1 run (run_inputs, pins),
the split JSONs of A3 in the fresh folder and the .migrated one (blob? Parquet split?), diff A3 → the concurrent edit child,
and lake_migrations of A3. usage: c1_after.py <state> <data dir> <port> <during_job out.json>"""
import glob, hashlib, json, sqlite3, sys, time
import httpx
st = json.load(open(sys.argv[1])); D = sys.argv[2]; port = sys.argv[3]; out = json.load(open(sys.argv[4]))
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=600, headers={"X-Ensemble-Actor": "Ada"})
db = sqlite3.connect(f"{D}/ensemble.sqlite3")
A3 = st["A3"]
r = c.post("/runs", json={"dataset_id": A3, "label": "active", "time_limit": 10, "presets": "medium_quality", "name": "x5 retry active", "by": "Ana"})
print("retry POST /runs A3 active →", r.status_code, r.text[:160]); rid = r.json().get("id")
while rid and c.get(f"/runs/{rid}").json()["status"] not in ("succeeded", "failed", "cancelled"): time.sleep(3)
for k, x in (("rL1c", out.get("rL1c")), ("rA3 retry", rid)):
    if not x: continue
    v = c.get(f"/admin/lake/runs/{x}/verify").json()
    print(f"{k} {x}: status={c.get(f'/runs/{x}').json()['status']} verify ok={v.get('ok')} data={v['data'].get('ok')} split={json.dumps(v.get('split'))[:240]}")
    print("   run_inputs:", db.execute("SELECT version_id, table_sha256 IS NOT NULL, split_id, read_mode FROM run_inputs WHERE run_id = ?", (x,)).fetchall(),
          "pins:", sorted(r[0] for r in db.execute("SELECT role FROM blob_refs WHERE owner_kind='run' AND owner_id=?", (x,))))
for f in sorted(glob.glob(f"{D}/datasets/{A3}*/splits/*.json")):
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    print(" ", f.split("/datasets/")[1], sha[:12], "blob:", [r[0] for r in db.execute("SELECT kind FROM blobs WHERE sha256=?", (sha,))],
          "split row:", [r[0] for r in db.execute("SELECT id FROM splits WHERE legacy_sha256=?", (sha,))])
if str(out.get("AE", "")).startswith("ds_"):
    d = c.get("/datasets/diff", params={"a": A3, "b": out["AE"]}).json()
    print(f"diff A3 → concurrent edit child {out['AE']}: identical={d.get('identical')} rows={ {k: (d.get('rows') or {}).get(k) for k in ('matched','added','removed','changed')} }")
print("lake_migrations A3:", db.execute("SELECT status, reason, attempts FROM lake_migrations WHERE version_id=?", (A3,)).fetchall())
print("migrated folders with split JSONs lacking a blob:")
blobs = {r[0] for r in db.execute("SELECT sha256 FROM blobs")}
n = 0
for f in glob.glob(f"{D}/datasets/*.migrated/**/*.json", recursive=True):
    if hashlib.sha256(open(f, "rb").read()).hexdigest() not in blobs and "/splits" in f: print("   ", f); n += 1
print("   total", n)

"""F-X2: after during_job.py: the concurrent run on A3 (its re-verify, run_inputs, pins), the split JSONs in both
folders and whether each has a blob and a Parquet split. usage: c1_check.py <data dir> <during_job out.json> <port>"""
import glob, hashlib, json, sqlite3, sys
import httpx
D, out, port = sys.argv[1], json.load(open(sys.argv[2])), sys.argv[3]
c = sqlite3.connect(f"{D}/ensemble.sqlite3")
print("during_job:", json.dumps({k: v for k, v in out.items()})[:900])
for k in ("rL1c", "rA3c"):
    rid = out.get(k)
    if not rid or not str(rid).startswith("run_"):
        continue
    v = httpx.get(f"http://localhost:{port}/api/admin/lake/runs/{rid}/verify", headers={"X-Ensemble-Actor": "Ada"}, timeout=300).json()
    print(f"{k} {rid}: verify ok={v.get('ok')} data={v['data'].get('ok')} split={json.dumps(v.get('split'))[:300]}")
    print("   run_inputs:", c.execute("SELECT version_id, split_id, split_sha256, split_legacy_sha256, read_mode FROM run_inputs WHERE run_id = ?", (rid,)).fetchall())
    print("   pins:", sorted(r[0] for r in c.execute("SELECT role FROM blob_refs WHERE owner_kind='run' AND owner_id = ?", (rid,))))
for f in sorted(glob.glob(f"{D}/datasets/ds_877f080f*/splits/*.json")):
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    print(f.split("/datasets/")[1], sha[:12], "blob:", [r[0] for r in c.execute("SELECT kind FROM blobs WHERE sha256 = ?", (sha,))],
          "split row:", [r[0] for r in c.execute("SELECT id FROM splits WHERE legacy_sha256 = ?", (sha,))],
          "view of:", (json.load(open(f)).get("view_of") or {}).get("split_id"))
print("lake_migrations A3:", c.execute("SELECT status, reason, attempts FROM lake_migrations WHERE version_id = 'ds_877f080f'").fetchall())

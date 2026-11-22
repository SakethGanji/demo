"""F-X2: the X2 findings, checked on a migrated data dir through the API (+ the sqlite file, read-only).
usage: fx2_checks.py <state.json> <data dir> <port>"""
import hashlib, json, sqlite3, sys
from pathlib import Path
import httpx
st = json.loads(Path(sys.argv[1]).read_text()); D = Path(sys.argv[2]); port = sys.argv[3]
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=600, headers={"X-Ensemble-Actor": "Ada"})
con = sqlite3.connect(D / "ensemble.sqlite3"); con.row_factory = sqlite3.Row
q = lambda s, *a: [dict(r) for r in con.execute(s, a)]
lm = {r["version_id"]: r for r in q("SELECT version_id, status, reason, message FROM lake_migrations")}
show = lambda k: print(f"  {k} {st[k]}: storage={q('SELECT storage FROM datasets WHERE id=?', st[k])[0]['storage']} "
                       f"lake_migrations={ {x: (lm.get(st[k]) or {}).get(x) for x in ('status', 'reason')} }\n      {((lm.get(st[k]) or {}).get('message') or '')[:420]}")
print("M4 (outside change e: pre-DL4 split of loans v1, no recorded hash)"); show("L1")
print("M5 (c: split JSON + split_files.sha256 of accounts v3; f: data.csv + datasets hashes of loans relabel)"); show("A3"); show("LR")
print("DL3/DL4 (a: data of churn v1)"); show("C1")
print("M2 (Feature Lab job records)")
for k in ("F2",):
    fl = c.get(f"/datasets/{st[k]}/feature-lab").json()
    jobs = fl.get("jobs") if isinstance(fl, dict) else fl
    print(f"  GET /datasets/{st[k]}/feature-lab → {len(jobs or [])} job(s): {[j.get('id') for j in jobs or []]}")
    for p in sorted((D / "datasets").glob(f"{st[k]}*/featurelab/*.json")):
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        print(f"  {p.relative_to(D / 'datasets')}: blob {[r['kind'] for r in q('SELECT kind FROM blobs WHERE sha256=?', sha)]}, "
              f"refs {[r['role'] for r in q('SELECT role FROM blob_refs WHERE blob_sha256=?', sha)]}")
print("M3 (the deleted version's split)")
for r in q("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", st["T2"]):
    print(f"  split_files {r['file']} {r['sha256'][:12]}…: blob {[x['kind'] for x in q('SELECT kind FROM blobs WHERE sha256=?', r['sha256'])]}, "
          f"Parquet split {[x['id'] for x in q('SELECT id FROM splits WHERE legacy_sha256=?', r['sha256'])]}")
print(f"  run {st['rT2']}: run_inputs {q('SELECT split_id, split_legacy_sha256 FROM run_inputs WHERE run_id=?', st['rT2'])}, "
      f"pins {sorted(x['role'] for x in q('SELECT role FROM blob_refs WHERE owner_kind=? AND owner_id=?', 'run', st['rT2']))}")
v = c.get(f"/admin/lake/runs/{st['rT2']}/verify").json()
print(f"  GET /admin/lake/runs/{st['rT2']}/verify → ok={v.get('ok')} deleted={v.get('deleted')} split={json.dumps(v.get('split'))[:300]}")
lv = c.post(f"/datasets/{st['T2']}/verify", json={"by": "Ada"}).json()
print(f"  POST /datasets/{st['T2']}/verify → {json.dumps(lv)[:300]}")
print("M6 (SQL refresh, no row map)")
for b in ("Q2", "Q3"):
    d = c.get("/datasets/diff", params={"a": st["Q1"], "b": st[b]}).json()
    print(f"  GET /datasets/diff a=Q1 b={b} → method={d.get('method')} identical={d.get('identical')} "
          f"rows={ {k: (d.get('rows') or d.get('summary') or {}).get(k) for k in ('matched', 'added', 'removed', 'changed')} }")
print("Every run: GET /admin/lake/runs/{id}/verify")
for r in q("SELECT id, dataset_id, status FROM runs ORDER BY created_at"):
    v = c.get(f"/admin/lake/runs/{r['id']}/verify").json()
    print(f"  {r['id']} on {r['dataset_id']} ({r['status']}): ok={v.get('ok')} data={(v.get('data') or {}).get('ok')} "
          f"split={(v.get('split') or {}).get('ok')} split_id={[x['split_id'] for x in q('SELECT split_id FROM run_inputs WHERE run_id=?', r['id'])]}")

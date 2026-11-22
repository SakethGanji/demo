"""X2: record the JSON the UI gets for every old run and every version (before or after the migration), one file per call.
usage: record.py <data dir> <out dir> [port]"""
import json, sqlite3, sys
from itertools import combinations
from pathlib import Path
import httpx

D, out = Path(sys.argv[1]), Path(sys.argv[2]); port = sys.argv[3] if len(sys.argv) > 3 else "8711"
out.mkdir(parents=True, exist_ok=True)
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=900, headers={"X-Ensemble-Actor": "Ana"})
con = sqlite3.connect(D / "ensemble.sqlite3")
runs = [r for r in con.execute("SELECT r.id, r.dataset_id, COALESCE(d.root_id, d.id) FROM runs r JOIN datasets d ON d.id = r.dataset_id ORDER BY r.created_at")]
vers = [r for r in con.execute("SELECT id, parent_id, COALESCE(storage,'csv') FROM datasets WHERE COALESCE(storage,'csv') = 'csv' OR id IN (SELECT dataset_id FROM runs) ORDER BY created_at")]
vl = out / "_versions.json"  # the same version list before and after (the first recording writes it)
if len(sys.argv) > 4 and Path(sys.argv[4]).exists():
    vers = [tuple(v) for v in json.loads(Path(sys.argv[4]).read_text())]
vl.write_text(json.dumps(vers))
codes = {}


class _Err:
    def __init__(self, e): self.status_code, self.text, self.e = 599, f"{type(e).__name__}: {e}", e
    def json(self): raise ValueError


def call(fn, *a, **k):
    for i in range(2):
        try:
            return fn(*a, **k)
        except httpx.HTTPError as e:
            err = _Err(e)
    return err


def save(name, r):
    try:
        body = r.json()
    except ValueError:
        body = r.text
    (out / f"{name}.json").write_text(json.dumps({"status": r.status_code, "body": body}, indent=1, sort_keys=True, default=str))
    codes[name] = r.status_code


for rid, vid, root in runs:
    save(f"run_{rid}", call(c.get, f"/runs/{rid}"))
    save(f"explain_{rid}", call(c.get, f"/runs/{rid}/explain"))
    save(f"holdout_{rid}", call(c.get, f"/runs/{rid}/holdout-predictions"))
by_root = {}
for rid, vid, root in runs:
    by_root.setdefault(root, []).append(rid)
for root, rs in by_root.items():
    for a, b in combinations(rs, 2):
        save(f"compare_{a}_{b}", call(c.post, "/compare", json={"a": a, "b": b}))
for vid, parent, sto in vers:
    save(f"verify_post_{vid}", call(c.post, f"/datasets/{vid}/verify", json={"by": "Ana"}))
    save(f"dataset_{vid}", call(c.get, f"/datasets/{vid}"))
    save(f"history_{vid}", call(c.get, f"/datasets/{vid}/history"))
    save(f"lineage_{vid}", call(c.get, f"/lineage/dataset/{vid}"))
    save(f"featurelab_{vid}", call(c.get, f"/datasets/{vid}/feature-lab"))
    save(f"splits_{vid}", call(c.get, f"/datasets/{vid}/splits"))
    if parent:
        save(f"diff_{parent}_{vid}", call(c.get, "/datasets/diff", params={"a": parent, "b": vid}))
print(json.dumps({k: v for k, v in sorted(codes.items()) if v >= 400}, indent=0)[:3000])
print("calls", len(codes), "non-2xx", sum(1 for v in codes.values() if v >= 300))

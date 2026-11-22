"""X2: the GC dry run (lake/gc.plan, the code behind POST /admin/lake/gc?dry_run=1) evaluated N days ahead, so no grace
period hides a candidate. Also: every legacy / split / run-pinned blob and whether it has a ref. usage: gc_future.py <days>"""
import json, sys
from datetime import datetime, timedelta, timezone
from app import config
from app.db import Database
from app.lake import gc
from app.lake.store import open_store
s = config.load_settings(); db = Database(s.db_path); st = open_store(s)
p = gc.plan(db, st, now=datetime.now(timezone.utc) + timedelta(days=float(sys.argv[1])))
print(json.dumps({"at": p["at"], "blobs": p["blobs"], "blob_refs": p["blob_refs"],
                  "candidates": [{k: c[k] for k in ("sha256", "kind", "state", "bytes")} for c in p["candidates"]],
                  "unreferenced_in_grace": len(p["unreferenced_in_grace"]), "orphans": p["orphans"], "integrity": p["integrity"],
                  "stale_tmp": p["stale_tmp"]}, indent=1))
with db.connect() as con:
    q = lambda sql: [tuple(r) for r in con.execute(sql)]
    print("by kind: blobs / with a ref / refs by owner kind")
    for k, n, nr in q("SELECT b.kind, COUNT(*), SUM(EXISTS (SELECT 1 FROM blob_refs r WHERE r.blob_sha256 = b.sha256)) FROM blobs b GROUP BY b.kind"):
        owners = q(f"SELECT r.owner_kind, COUNT(*) FROM blob_refs r JOIN blobs b ON b.sha256 = r.blob_sha256 WHERE b.kind = '{k}' GROUP BY 1")
        print(f"  {k:<15} {n:>4} {nr:>4}  {owners}")
    print("runs and their pins (role list):")
    for rid, ps, roles in q("SELECT r.id, r.predictor_state, (SELECT GROUP_CONCAT(x.role) FROM blob_refs x WHERE x.owner_kind = 'run' AND x.owner_id = r.id) FROM runs r ORDER BY r.created_at"):
        print(f"  {rid} {ps:<8} {roles}")

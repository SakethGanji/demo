"""X5/x2: plant a live migration lock (catalog_meta lake_migrate:lock:<id>, no job, t = now: honoured for 1 h, as in
F-X2's lock_409 test) on a CSV-era version of an UNMIGRATED copy, then call every route that draws a split on it.
Expected: 409 version_migrating everywhere. usage: lock_probe.py <data dir> <port> <version id> <target>"""
import json, sqlite3, sys, time
import httpx
D, port, vid, tgt = sys.argv[1:5]
db = sqlite3.connect(f"{D}/ensemble.sqlite3")
db.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)", (f"lake_migrate:lock:{vid}", json.dumps({"t": time.time(), "job_id": None, "pid": 0}))); db.commit()
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=300, headers={"X-Ensemble-Actor": "Ana"})
calls = [("POST /runs", lambda: c.post("/runs", json={"dataset_id": vid, "label": tgt, "time_limit": 10, "presets": "medium_quality", "name": "x5 probe", "by": "Ana"})),
         ("POST /datasets/{id}/splits (new strategy)", lambda: c.post(f"/datasets/{vid}/splits", json={"target": tgt, "strategy": "random", "seed": 7})),
         ("POST /datasets/{id}/splits", lambda: c.post(f"/datasets/{vid}/splits", json={"target": tgt})),
         ("POST /datasets/{id}/data-doctor/apply", lambda: c.post(f"/datasets/{vid}/data-doctor/apply", json={"transforms": [{"op": "drop_rows", "rows": [0], "reason": "x5"}], "by": "Ana"})),
         ("POST /datasets/{id}/feature-lab", lambda: c.post(f"/datasets/{vid}/feature-lab", json={"candidates": [{"op": "ratio", "a": "a", "b": "b"}], "time_limit": 10, "max_candidates": 1, "by": "Ana"})),
         ]
for name, f in calls:
    try:
        r = f(); print(f"{name} → {r.status_code} {r.text[:200]}")
    except httpx.HTTPError as e:
        print(f"{name} → {type(e).__name__} {e}")
db.execute("DELETE FROM catalog_meta WHERE key = ?", (f"lake_migrate:lock:{vid}",)); db.commit(); print("lock removed")

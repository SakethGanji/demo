"""X5/x2: X2's extra runs on loans v2 at acfe988 (rL2x0, rL2x1 → evicted by retention). usage: extra_runs.py <state> <port>"""
import json, sys, time
from pathlib import Path
import httpx
p = Path(sys.argv[1]); st = json.loads(p.read_text()); port = sys.argv[2]
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"})
for k in ("rL2x0", "rL2x1"):
    rid = c.post("/runs", json={"dataset_id": st["L2"], "label": "y", "time_limit": 20, "presets": "medium_quality", "name": k, "by": "Ana"}).json()["id"]
    while c.get(f"/runs/{rid}").json()["status"] not in ("succeeded", "failed", "cancelled"):
        time.sleep(3)
    st[k] = rid; print(k, rid, c.get(f"/runs/{rid}").json()["status"])
r = c.post("/retention/run", json={"by": "Ada", "confirm": "retention", "run_ids": [st["rL2x1"]], "tier": "evict"}, headers={"X-Ensemble-Actor": "Ada"})
print("evict", r.status_code, r.text[:400]); p.write_text(json.dumps(st, indent=1))

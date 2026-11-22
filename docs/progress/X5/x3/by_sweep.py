"""X5/x3: every POST/PUT/PATCH/DELETE route from openapi.json gets a body whose `by` (and created_by / reviewer /
decided_by) is not text, path ids filled with live ids from the chain's work dir. Prints every 5xx.
Skips routes whose path names a destructive admin action (reset, purge, gc, restore, delete, sync, migrate, shutdown).
Usage: by_sweep.py <port> <chain work dir>"""
import json, re, sys
from pathlib import Path
import requests

P, W = sys.argv[1], Path(sys.argv[2])
A = f"http://localhost:{P}"
H = {"X-Ensemble-Actor": "Ana"}
rd = lambda f: (W / f).read_text().strip() if (W / f).exists() else "x"
V = rd("vkeys.vid"); R = rd("v1.vid")
s = requests.get(f"{A}/api/shares", headers=H).json()["shares"]
c = requests.get(f"{A}/api/datasets/{V}/certifications", headers=H).json().get("certifications", [])
runs = requests.get(f"{A}/api/runs", headers=H).json()
runs = runs.get("runs", runs) if isinstance(runs, dict) else runs
fill = {"dataset_id": V, "version_id": V, "vid": V, "root_id": R, "run_id": rd("r1.id"), "rid": rd("r1.id"), "join_id": rd("jm.id"),
        "sid": s[0]["id"] if s else "x", "cid": c[0]["id"] if c else "x", "team": "tm_default", "table": "apps", "mv": rd("m1.id"),
        "pid": "x", "project_id": "x"}
spec = requests.get(f"{A}/openapi.json").json()
SKIP = re.compile(r"reset|purge|/gc|restore|delete|sync|migrat|shutdown|archive|revoke-all|wipe")
BAD = [123, ["x"], {"a": 1}, True]
n, hits = 0, []
for path, ops in spec["paths"].items():
    for m in ops:
        if m not in ("post", "put", "patch", "delete") or SKIP.search(path):
            continue
        url = re.sub(r"\{(\w+)\}", lambda g: fill.get(g.group(1), "x"), path)
        for b in BAD:
            body = {"by": b, "created_by": b, "reviewer": b, "decided_by": b, "reason": "x5 by sweep", "comment": "x5"}
            n += 1
            try:
                r = requests.request(m.upper(), A + url, headers=H, json=body, timeout=120)
            except Exception as e:  # noqa: BLE001
                hits.append((m.upper(), path, json.dumps(b), "EXC", str(e)[:100])); continue
            if r.status_code >= 500:
                hits.append((m.upper(), path, json.dumps(b), r.status_code, r.text[:100]))
for h in hits:
    print(*h)
print(n, "calls;", len(hits), "5xx; routes with a 5xx:", sorted({(h[0], h[1]) for h in hits}))

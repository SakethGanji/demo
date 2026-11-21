"""P10: record the JSON the UI gets for old runs and versions (before or after the migration), one file per call.
usage: record.py <state.json> <out dir>"""
import json
import sys
from pathlib import Path

import httpx

st = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
c = httpx.Client(base_url="http://localhost:8000/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"})


def save(name, r):
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    (out / f"{name}.json").write_text(json.dumps({"status": r.status_code, "body": body}, indent=1, sort_keys=True, default=str))
    print(f" {name:28s} HTTP {r.status_code}")


PAIRS = [("rL1", "rL2"), ("rA1", "rA2"), ("rA2", "rA4"), ("rL2", "rL4"), ("rA2", "rA5")]
for a, b in PAIRS:
    save(f"compare_{a}_{b}", c.post("/compare", json={"a": st[a], "b": st[b]}))
for v in ("L1", "L2", "L3", "A2", "L4", "T2", "C1"):
    save(f"verify_post_{v}", c.post(f"/datasets/{st[v]}/verify", json={"by": "Ana"}))
    save(f"verify_get_{v}", c.get(f"/datasets/{st[v]}/verify"))
for r in ("rL2", "rA2"):
    save(f"explain_{r}", c.get(f"/runs/{st[r]}/explain"))

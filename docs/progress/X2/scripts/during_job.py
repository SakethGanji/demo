"""X2: work done while the migration job runs (head API with hooks X2_SLOW and X2_SLEEP_AFTER_RUNS=<A3>:25):
  1 an edit of SQL refresh v2 (CSV-era, no row map) before the job reaches it
  2 a run on loans v1 while the job migrates the samples
  3 a run on accounts v3 with a new target ('active': a new split JSON is drawn) while A3 sits between its runs listing
    and its commit
  4 an edit of loans v1 after it was migrated, while the job goes on
usage: concurrent.py <state.json> <port> <out.json>"""
import json, sys, time
from pathlib import Path
import httpx
st = json.loads(Path(sys.argv[1]).read_text()); port = sys.argv[2]
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"}, limits=httpx.Limits(max_keepalive_connections=0))
adm = {"X-Ensemble-Actor": "Ada"}
log = {}
t0 = time.time()
say = lambda *a: print(f"[{time.time() - t0:5.1f}s]", *a, flush=True)


def prog():
    d = c.get("/admin/lake/migrate", headers=adm).json()
    return d.get("progress") or {}, d


def run(ds, label):
    r = c.post("/runs", json={"dataset_id": ds, "label": label, "time_limit": 10, "presets": "medium_quality", "name": f"x2 concurrent {label}", "by": "Ana"})
    say("POST /runs", ds, label, "→", r.status_code, r.text[:200])
    return r.json().get("id") if r.status_code < 300 else None


r = c.post("/admin/lake/migrate?dry_run=0", json={"by": "Ada"}, headers=adm); say("migrate job", r.status_code)
r = c.post(f"/datasets/{st['Q2']}/edit", json={"ops": [{"op": "delete_rows", "rows": [0, 1]}], "reason": "x2 concurrent edit", "by": "Ana"})
say("edit SQL refresh v2 (not migrated yet)", r.status_code, r.text[:200]); log["QE"] = r.json().get("id") if r.status_code < 300 else r.text[:300]
r = c.post(f"/datasets/{st['A3']}/edit", json={"ops": [{"op": "delete_rows", "rows": [0, 1]}], "reason": "x2 concurrent edit", "by": "Ana"})
say("edit accounts v3 (not migrated yet, has a row map)", r.status_code, r.text[:200]); log["AE"] = r.json().get("id") if r.status_code < 300 else r.text[:300]
log["rL1c"] = run(st["L1"], "y")
done_l1 = False
while True:
    p, d = prog()
    if not done_l1 and any(v["version_id"] == st["L1"] and v["status"] == "migrated" for v in d["versions"]):
        r = c.post(f"/datasets/{st['L1']}/edit", json={"ops": [{"op": "set_cells", "cells": [{"row": 2, "column": "note", "value": "x2"}]}], "reason": "x2 edit after migration", "by": "Ana"})
        say("edit loans v1 (migrated, job still running)", r.status_code, r.text[:160]); log["LE"] = r.json().get("id") if r.status_code < 300 else r.text[:300]
        done_l1 = True
    if p.get("current") == st["A3"]:
        time.sleep(3)  # inside the hook's window (after A3's runs were listed)
        log["rA3c"] = run(st["A3"], "active")
        break
    if p.get("status") == "finished" and p.get("job_id"):
        say("job finished before A3 was reached"); break
    time.sleep(0.5)
while prog()[0].get("status") != "finished":
    time.sleep(1)
say("job finished", prog()[0])
for k in ("rL1c", "rA3c"):
    if log.get(k):
        for _ in range(200):
            s = c.get(f"/runs/{log[k]}").json()
            if s["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(3)
        log[k + "_status"] = s["status"]; log[k + "_error"] = (s.get("error") or "")[:400]
        say(k, s["status"], (s.get("error") or "")[:300])
Path(sys.argv[3]).write_text(json.dumps(log, indent=1))

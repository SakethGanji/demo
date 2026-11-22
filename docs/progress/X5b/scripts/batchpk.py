"""X5b: a batch on a model version, peaks over the API + workerd trees. usage: batchpk.py <api pid> <mv> <csv>"""
import sys, time, subprocess, json
sys.path.insert(0, "/var/tmp/x5b/scale")
from lib import Peak, c, BASE
pid, mv, f = int(sys.argv[1]), sys.argv[2], sys.argv[3]
with Peak(pid, f"batch {f.split('/')[-1]} on {mv}") as pk:
    out = subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor: x5", *sum([["-F", f"files=@{x}"] for x in f.split(",")], []), "-F", "created_by=x5", f"{BASE}/api/registry/versions/{mv}/batches"], capture_output=True, text=True).stdout
    b = json.loads(out); bid = b.get("id"); print("  ->", str(b)[:200], flush=True)
    while bid:
        st, x = c("GET", f"/api/registry/batches/{bid}")
        if x.get("status") in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(2)
print("  batch", bid, x.get("status"), "rows", x.get("n_rows"), str(x.get("error") or "")[:300])
print("  max VmHWM per process (GB):", sorted([round(v / 1e6, 2) for v in pk.hwm.values()], reverse=True)[:5])

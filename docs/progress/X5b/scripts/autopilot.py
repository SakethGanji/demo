"""X5b J3: a full Autopilot on credit-default (12k rows) through the API; peaks over the API tree (single process,
tree sum) and every process's VmHWM. usage: autopilot.py <api pid> ; X5_BASE set."""
import sys, time, json
sys.path.insert(0, "/var/tmp/x5b/scale")
from lib import Peak, c
pid = int(sys.argv[1])
body = {"dataset_id": "credit-default", "requirements": [{"metric": sys.argv[2] if len(sys.argv) > 2 else "roc_auc", "value": 0.5}],
        "budget_seconds": 300, "autonomy": "auto", "decider": "layer1"}
with Peak(pid, "autopilot credit-default (12k rows), API tree") as pk:
    st, a = c("POST", "/api/autopilot", body)
    print(st, str(a)[:300], flush=True)
    aid = a.get("id")
    while aid:
        st, x = c("GET", f"/api/autopilot/{aid}")
        if x.get("status") not in ("running", "queued", "pending", "starting"):
            break
        time.sleep(3)
print("status", x.get("status"), "error", str(x.get("error"))[:300], "steps", [(s.get("kind"), s.get("status")) for s in x.get("steps", [])])
print("max VmHWM per process (GB):", sorted([round(v / 1e6, 2) for v in pk.hwm.values()], reverse=True)[:8])

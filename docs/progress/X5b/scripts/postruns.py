"""X5b: API-process memory of POST /runs on the 10M versions (the request, then 45 s after). usage: postruns.py <api pid> <dataset> [join]"""
import sys, time
sys.path.insert(0, "/var/tmp/x5b/scale")
from lib import Peak, c, rss_kb
pid = int(sys.argv[1]); body = {"dataset_id": sys.argv[2], "label": "y", "time_limit": 30, "frame_budget_mb": 300, "by": "x5"}
if len(sys.argv) > 3: body["join_id"] = sys.argv[3]
import os; os.environ["X5_PIDS"] = str(pid)
with Peak(pid, "POST /runs request only (API tree)") as pk:
    st, r = c("POST", "/api/runs", body)
print(" ", st, r.get("id"), r.get("status"))
with Peak(pid, "next 45 s (API tree)"):
    time.sleep(45)
print("  API VmHWM now", rss_kb(pid, "VmHWM") / 1e6, "GB")
print("  cancel", c("POST", f"/api/runs/{r['id']}/cancel")[0])

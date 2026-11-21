import sys, time, json
from c import *
rid = sys.argv[1]
for _ in range(60):
    st, d = c("GET", f"/api/runs/{rid}/doctor", quiet=True)
    if st != 200 or d.get("status") not in ("running", "queued"): break
    time.sleep(3)
print(f"GET /runs/{rid}/doctor -> {st}", "" if st == 200 else str(d)[:100])
if st == 200:
    gaps = d.get("gaps") or (d.get("missing") or {}).get("gaps") or []
    print("  overfit:", [x["status"] for x in d.get("checks", d.get("findings", [])) if x.get("id") == "overfit"],
          "| missing/gaps keys:", [g.get("id") for g in gaps][:8] if isinstance(gaps, list) else type(gaps))

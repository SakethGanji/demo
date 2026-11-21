from c import *
import os, json, urllib.request, time
p = "$SCRATCH/seed10m.csv"
with Peak(kill_gb=8):
    with open(p, "rb") as fh:
        r = urllib.request.Request(BASE + "/api/uploads?filename=seed10m.csv&by=fx1", data=fh, method="POST",
                                   headers={**ACT, "Content-Type": "text/csv", "Content-Length": str(os.path.getsize(p))})
        u = json.loads(urllib.request.urlopen(r, timeout=3600).read())["upload_id"]
    c("POST", f"/api/uploads/{u}/check", show=0)
c("DELETE", f"/api/uploads/{u}", show=0)

"""C1 end to end: W columns x R rows -> upload, check, commit, then the stats profile job (watch RSS)."""
import sys, time
from c import *
W, R = int(sys.argv[1]), int(sys.argv[2])
body = (",".join(f"c{i}" for i in range(W)) + "\n" + "\n".join(",".join(str((r * 7 + i) % 97) for i in range(W)) for r in range(R)) + "\n").encode()
with Peak(kill_gb=6) as p:
    u = upload(body, f"wide{W}.csv")
    st, ck = c("POST", f"/api/uploads/{u}/check", show=0)
    st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, show=0)
    vid = v["version_id"]; t = time.time()
    while time.time() - t < 300:
        st, s = c("GET", f"/api/datasets/{vid}/stats", quiet=True)
        if st == 200 and s.get("status") in ("ready", "done", None) and s.get("columns"): break
        time.sleep(1)
    print(f"stats {vid}: {st} status={s.get('status')} n_cols={len(s.get('columns') or [])} after {time.time()-t:.1f}s")

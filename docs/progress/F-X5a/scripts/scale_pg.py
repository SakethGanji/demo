"""X5 scale gates at 10M rows. usage: scale.py <api pid> <step> ...   state in $X5_STATE (json).
steps: upload | edit | diff | doctor | apply | featurelab | multi | join | train | register | trainjoin"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from lib import Peak, c, poll_job  # noqa: E402

PID = int(sys.argv[1])
step = sys.argv[2]
SF = os.environ.get("X5_STATE", "/var/tmp/fx5/scale/state.json")
S = json.load(open(SF)) if os.path.exists(SF) else {}
SEED = os.environ.get("X5_SEED", "/var/tmp/fx5/scale/seed10m.csv")
BY = "x5"


def show(st, r, keys=None):
    if isinstance(r, dict) and keys:
        r = {k: r.get(k) for k in keys}
    print("  ->", st, json.dumps(r, default=str)[:700], flush=True)


def settle(st, r):
    """202 {job_id} → poll → the job's result (the old body)."""
    if st == 202 and isinstance(r, dict) and r.get("job_id"):
        j = poll_job(r["job_id"])
        print("  job", j.get("status"), "error", (j.get("error") or "")[:300])
        return j.get("result") or {}
    return r


if step == "upload":
    with Peak(PID, "upload+check+commit") as pk:
        with Peak(PID, "POST /uploads (stream)"):
            import urllib.request
            with open(SEED, "rb") as fh:
                rq = urllib.request.Request(os.environ.get("X5_BASE", "http://127.0.0.1:8601") + f"/api/uploads?filename=seed10m.csv&by={BY}",
                                            data=fh, method="POST", headers={"X-Ensemble-Actor": BY, "Content-Type": "text/csv",
                                                                             "Content-Length": str(os.path.getsize(SEED))})
                u = json.loads(urllib.request.urlopen(rq, timeout=3600).read())["upload_id"]
        st, r = c("PATCH", f"/api/uploads/{u}", {"target": "y", "name": "x5 seed10m"})
        with Peak(PID, "/check"):
            st, ck = c("POST", f"/api/uploads/{u}/check")
        print("  check", st, [(t.get("name"), t.get("n_rows"), [(x["name"], x["type"]) for x in t.get("schema", [])][:4]) for t in ck.get("tables", [])], "blocks", ck.get("blocks"))
        with Peak(PID, "/commit"):
            st, v = c("POST", f"/api/uploads/{u}/commit", {"by": BY, "check_sha256": ck.get("check_sha256")})
        show(st, v, ["version_id", "rows", "created", "detail"])
    S["v"] = v.get("version_id")
    S["upload_s"] = pk.secs
    S["upload_gb"] = pk.gb()
elif step == "edit":
    body = {"by": BY, "reason": "x5 scale", "ops": [{"op": "set_cells", "cells": [
        {"row_id": 5, "column": "amount", "value": 1234.5}, {"row_id": 9_000_001, "column": "zip", "value": "02139"}]}]}
    with Peak(PID, "edit preview"):
        st, r = c("POST", f"/api/datasets/{S['v']}/edit", {**body, "preview": True})
    show(st, r, ["changes", "summary", "job_id", "detail"])
    if st == 202:
        settle(st, r)
    with Peak(PID, "edit commit (job)"):
        st, r = c("POST", f"/api/datasets/{S['v']}/edit", body)
        print("  ", st, str(r)[:200])
        r = settle(st, r)
    show(st, r, ["dataset", "created", "summary", "refused"])
    S["edited"] = ((r.get("dataset") or {}).get("id")) or r.get("version_id")
elif step == "diff":
    for k in ("first", "repeat"):
        with Peak(PID, f"diff {k}"):
            st, r = c("GET", f"/api/datasets/{S['v']}/diff/{S['edited']}")
        show(st, r, ["matched", "changed", "added", "removed", "changed_by_column", "cached"])
elif step == "doctor":
    with Peak(PID, "GET data-doctor"):
        st, r = c("GET", f"/api/datasets/{S['v']}/data-doctor")
    print("  ", st, "rows", r.get("rows"), "sample", r.get("sample"), [(f["id"], f.get("severity")) for f in r.get("findings", [])][:12])
elif step == "apply":
    with Peak(PID, "data-doctor apply drop_columns (job)"):
        st, r = c("POST", f"/api/datasets/{S['v']}/data-doctor/apply", {"transforms": [{"op": "drop_columns", "columns": ["note"]}], "by": BY})
        print("  ", st, str(r)[:200])
        r = settle(st, r)
    show(st, r, ["how", "rows_before", "rows_after", "columns_removed", "created", "refused"])
elif step == "featurelab":
    with Peak(PID, "feature-lab (job)"):
        st, j = c("POST", f"/api/datasets/{S['v']}/feature-lab", {"by": BY, "max_candidates": 4, "time_limit": 60, "apply": True})
        print("  ", st, str(j)[:200])
        jid = j.get("job_id") or j.get("id")
        t0 = time.time()
        while time.time() - t0 < 3600:
            st, x = c("GET", f"/api/datasets/{S['v']}/feature-lab/{j.get('id')}") if j.get("id") else (0, {})
            if not j.get("id"):
                x = poll_job(jid)
            if x.get("status") in ("done", "failed", "succeeded", "cancelled"):
                break
            time.sleep(3)
    res = x.get("result") or {}
    print("  status", x.get("status"), "kept", [t.get("name") for t in res.get("kept") or []], "sample", (res.get("sample") or {}).get("rows"),
          "version", x.get("version") or res.get("version"), "error", str(x.get("error"))[:300])
elif step == "multi":
    # a 10M grain + a 10k-row dimension (branches), one multi-file upload → key on branches.branch → relation
    br = "/var/tmp/fx5/scale/branches.csv"
    with open(br, "w") as f:
        f.write("branch,region,branch_risk\n")
        for i in range(10000):
            f.write(f"{i:04d},R{i % 7},{(i * 37 % 101) / 100:.2f}\n")
    import subprocess
    with Peak(PID, "multi upload (10M + branches) + check + commit") as pk:
        out = subprocess.run(["curl", "-s", "-H", f"X-Ensemble-Actor: {BY}", "-X", "POST",
                              os.environ.get("X5_BASE", "http://127.0.0.1:8601") + "/api/uploads",
                              "-F", f"file=@{SEED};filename=apps.csv", "-F", f"file=@{br}", "-F", "name=x5 multi", "-F", "target=y", "-F", f"by={BY}"],
                             capture_output=True, text=True).stdout
        u = json.loads(out)
        uid = u.get("upload_id") or u.get("id")
        st, ck = c("POST", f"/api/uploads/{uid}/check")
        print("  check", st, [(t.get("name"), t.get("action"), t.get("n_rows")) for t in ck.get("tables", [])], "blocks", ck.get("blocks"))
        st, v = c("POST", f"/api/uploads/{uid}/commit", {"by": BY, "check_sha256": ck.get("check_sha256")})
        show(st, v, ["version_id", "rows", "created", "detail"])
    V = v.get("version_id")
    st, k = c("PUT", f"/api/datasets/{V}/tables/branches/key", {"columns": ["branch"], "reason": "x5", "by": BY})
    show(st, k, ["version_id", "detail"])
    S["mroot"] = V
    S["mv"] = k.get("version_id") or V
elif step == "join":
    R, V = S["mroot"], S["mv"]
    st, j = c("POST", f"/api/datasets/{R}/joins", {"name": "x5_branch", "version_id": V, "by": BY,
                                                     "spec": {"grain": "apps", "joins": [{"type": "n:1", "table": "branches", "on": ["branch"], "prefix": "b__"}]}})
    show(st, j, ["id", "coverage", "detail"])
    S["join"] = j.get("id")
    with Peak(PID, "frame build 10M"):
        st, f = c("POST", f"/api/datasets/{R}/versions/{V}/frames", {"join_id": S["join"], "by": BY})
        f = settle(st, f)
    show(st, f, ["id", "rows", "coverage", "frame_sha256", "detail"])
    S["frame"] = f.get("id")
elif step in ("train", "trainjoin"):
    body = {"dataset_id": S["v"] if step == "train" else S["mv"], "label": "y", "time_limit": int(os.environ.get("X5_TL", "60")),
            "frame_budget_mb": int(os.environ.get("X5_BUDGET", "300")), "by": BY}
    if step == "trainjoin":
        body["join_id"] = S["join"]
    with Peak(PID, f"{step} (budget {body['frame_budget_mb']} MB)"):
        st, r = c("POST", "/api/runs", body)
        show(st, r, ["id", "status", "detail"])
        rid = r.get("id")
        while True:
            st, x = c("GET", f"/api/runs/{rid}")
            if x.get("status") in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(5)
    res = x.get("result") or {}
    keys = ("rows_used", "rows_total", "sample_sha256", "frame_estimate_mb", "budget_mb")
    print("  run", rid, x.get("status"), (x.get("error") or "")[:300], {k: x.get(k, res.get(k)) for k in keys},
          {k: v for k, v in (x.get("sampling") or res.get("sampling") or {}).items()} if (x.get("sampling") or res.get("sampling")) else "")
    S[f"run_{step}"] = rid
elif step == "register":
    rid = S[sys.argv[3]]
    with Peak(PID, f"register {rid} (parity)"):
        st, g = c("POST", f"/api/runs/{rid}/register", {"created_by": BY, "model_name": f"x5-{rid}"})
        show(st, g, ["id", "status", "detail", "code", "parity"]); T0 = time.time()
        mid = g.get("id")
        while mid:
            st, m = c("GET", f"/api/registry/versions/{mid}")
            if m.get("status") not in ("building", "queued", "pending", "running") or time.time() - T0 > 1500:
                break
            time.sleep(5)
    if mid:
        print("  model", mid, m.get("status"), str(m.get("error"))[:300], "parity", json.dumps((m.get("card") or {}).get("data", {}).get("parity") or m.get("parity"), default=str)[:400])
        S[f"model_{sys.argv[3]}"] = mid
json.dump(S, open(SF, "w"), indent=1)

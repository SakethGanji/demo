"""X5 (lane x4): open note X4 m4 / F-X4c follow-up. After a purge, and after a plain (four-eyes) delete, the reads of the
gone version and of the runs trained on it: GET /datasets/{old}/export, /runs/{id}/explain, /runs/{id}/shap. Expected 4xx
(410 / 409 `purged` / 404), the note says 500. Run after F-X4c purge.py (state: v1 purged, run / run2 trained on v1).

    PYTHONPATH=…/F-X4c python m4.py <base> <state.json>"""
import time

import httpx

from x4lib import B, R, c, csv, get, h, load, post, save, show, wait


def call(n, who, url, params=None):
    """One GET on a fresh connection (a 500 used to drop keep-alive); prints status and the first bytes."""
    try:
        r = httpx.get(B + url, params=params or {}, headers=h(who), timeout=300)
        body = r.text[:220].replace("\n", " ") if not r.headers.get("content-type", "").startswith(("application/zip", "text/csv")) \
            else f"<{r.headers.get('content-type')} {len(r.content)} bytes>"
        print(f"## {n} → HTTP {r.status_code} | {body}")
        return r.status_code
    except Exception as e:  # noqa: BLE001
        print(f"## {n} → {type(e).__name__}: {e}")
        return None


st = load()
print(f"# mode {st['mode']}")
v1, run, run2 = st["v1"], st["run"], st.get("run2")
PART2_ONLY = __import__("os").environ.get("X5_M4_PART") == "2"
print("\n# 1. after the purge of loans.ssn (v1 is the purged/deleted version; new_v the kept one)")
if PART2_ONLY:
    print("    (skipped: X5_M4_PART=2)")
else:
    show("1x GET /datasets/{v1}", get("Ana", f"/datasets/{v1}"), ["id", "status", "deleted_at", "purged"], cut=200)
    for who in ("Ana", "Ada"):
        call(f"1a {who} GET /datasets/{{v1}}/export (zip)", who, f"/datasets/{v1}/export", {"by": who})
        call(f"1b {who} GET /datasets/{{v1}}/export?format=csv", who, f"/datasets/{v1}/export", {"by": who, "format": "csv"})
        call(f"1c {who} GET /datasets/{{v1}}/export?unmasked=1&reason", who, f"/datasets/{v1}/export",
             {"by": who, "format": "csv", "unmasked": 1, "reason": "post purge check"})
    for rid, tag in ((run, "run (used ssn)"), (run2, "run2 (ssn dropped)")):
        if not rid:
            continue
        call(f"1d Ana GET /runs/{{{tag}}}/explain", "Ana", f"/runs/{rid}/explain")
        call(f"1e Ana GET /runs/{{{tag}}}/shap?row=0", "Ana", f"/runs/{rid}/shap", {"row": 0})
        call(f"1f Ana GET /runs/{{{tag}}}/shap?row=<holdout row>", "Ana", f"/runs/{rid}/shap", {"row": st.get("shap_row", 1)})
        call(f"1g Ana GET /runs/{{{tag}}}/holdout-predictions", "Ana", f"/runs/{rid}/holdout-predictions", {"by": "Ana"})
    if st.get("new_v"):
        call("1h Ana GET /datasets/{new_v}/export?format=csv (the kept version)", "Ana", f"/datasets/{st['new_v']}/export",
             {"by": "Ana", "format": "csv"})

print("\n# 2. a plain delete (request + four-eyes decide) of a version with a run on it")
u = c.post(B + "/uploads", params={"filename": "small.csv", "project_id": st["pd"]},
           files={"file": ("small.csv", csv([{k: v for k, v in r.items() if k not in ("applicant_name", "email", "ssn", "dob")}
                                              for r in R[:300]]), "text/csv")}, headers=h("Ana")).json()["upload_id"]
post("Ana", f"/uploads/{u}/check")
vs = show("2a commit small", post("Ana", f"/uploads/{u}/commit", {"reason": "small for delete", "by": "Ana"}), ["version_id"])["version_id"]
rs = show("2b run on small", post("Ana", "/runs", {"dataset_id": vs, "label": "y", "time_limit": 10, "by": "Ana", "project_id": st["pd"],
                                                    "drop_columns": ["app_id"]}), ["id"])["id"]
print("    run:", wait(f"/runs/{rs}")["status"])
rs2 = show("2b' a second run on small (never read before the delete: no explain / SHAP cache)",
           post("Ana", "/runs", {"dataset_id": vs, "label": "y", "time_limit": 10, "by": "Ana", "project_id": st["pd"],
                                 "drop_columns": ["app_id"]}), ["id"])["id"]
print("    run2:", wait(f"/runs/{rs2}")["status"])
call("2c before: Ana GET /runs/{rs}/explain", "Ana", f"/runs/{rs}/explain")
call("2d before: Ana GET /runs/{rs}/shap?row=0", "Ana", f"/runs/{rs}/shap", {"row": 0})
call("2e before: Ana GET /datasets/{vs}/export?format=csv", "Ana", f"/datasets/{vs}/export", {"by": "Ana", "format": "csv"})
dr = show("2f Ana POST /datasets/{vs}/delete", post("Ana", f"/datasets/{vs}/delete", {"reason": "superseded small extract", "by": "Ana"}),
          None, cut=400)
if not dr.get("id"):  # a run uses it: accept that it becomes irreproducible (four eyes, audited)
    dr = show("2f' Ana POST /datasets/{vs}/delete {irreproducible: {reason}}",
              post("Ana", f"/datasets/{vs}/delete", {"reason": "superseded small extract", "by": "Ana",
                                                     "irreproducible": {"reason": "X5 check: run kept for its record only"}}),
              ["id", "status", "runs_made_irreproducible"], cut=400)
if dr.get("id") and dr.get("status") != "done":
    show("2g Ben decides", post("Ben", f"/dataset-requests/{dr['id']}/decide", {"approve": True, "by": "Ben"}), ["status"], cut=300)
time.sleep(2)
show("2h GET /datasets/{vs}", get("Ana", f"/datasets/{vs}"), ["id", "status", "deleted_at"], cut=200)
for who in ("Ana", "Ada"):
    call(f"2i {who} GET /datasets/{{vs}}/export (zip)", who, f"/datasets/{vs}/export", {"by": who})
    call(f"2j {who} GET /datasets/{{vs}}/export?format=csv", who, f"/datasets/{vs}/export", {"by": who, "format": "csv"})
call("2k Ana GET /runs/{rs}/explain (run on the deleted version)", "Ana", f"/runs/{rs}/explain")
call("2l Ana GET /runs/{rs}/shap?row=0", "Ana", f"/runs/{rs}/shap", {"row": 0})
call("2m Ana GET /runs/{rs}/shap?row=3", "Ana", f"/runs/{rs}/shap", {"row": 3})
call("2n Ana GET /datasets/{vs}/rows", "Ana", f"/datasets/{vs}/rows", {"by": "Ana"})
hp = httpx.get(B + f"/runs/{rs2}/holdout-predictions", params={"by": "Ana"}, headers=h("Ana"), timeout=120)
hrow = ((hp.json() if hp.status_code == 200 else {}).get("rows") or [0])[0]
print(f"    run2 holdout-predictions → HTTP {hp.status_code}; first holdout row {hrow}")
call("2o Ana GET /runs/{rs2}/explain (uncached, deleted data)", "Ana", f"/runs/{rs2}/explain")
call(f"2p Ana GET /runs/{{rs2}}/shap?row={hrow} (uncached)", "Ana", f"/runs/{rs2}/shap", {"row": hrow})
call(f"2q Ana GET /runs/{{rs}}/shap?row={hrow} (uncached row on the warmed run)", "Ana", f"/runs/{rs}/shap", {"row": hrow})
st["small_v"], st["small_run"] = vs, rs
save(st)

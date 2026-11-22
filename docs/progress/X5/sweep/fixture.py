"""X5 sweep fixture: team+members, project, loans v1 (+PII confirmed) and v2, multi-table jx (apps, cust) with keys + relation
+ join + frame, contract, certification, share, a run (time_limit 15), registered model version, batch, anomaly run,
legal hold (released), annotation note, autopilot, agent session, review, slices, reset request, dataset delete request.
    python fixture.py <base> <state.json>"""
from slib import B, R, CUST, c, csv, get, h, patch, post, put, save, show, wait

st = {}
st["mode"] = get("Ada", "/me").json().get("authz_mode")
cr = show("team", post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"})).get("id")
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer"), ("Vic", "viewer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = show("team2", post("Ada", "/teams", {"name": "Collections", "by": "Ada"})).get("id")
post("Ada", f"/teams/{co}/members", {"name": "Cole", "role": "owner", "by": "Ada"})
st.update(cr=cr, co=co)
st["pd"] = show("project", post("Ana", "/projects", {"name": "PD", "team_id": cr, "by": "Ana"})).get("id")

def upload(files, who="Ana", match=None, ack=None, **params):
    u = c.post(B + "/uploads", params={"project_id": st["pd"], **params}, files=files, headers=h(who)).json()
    up = u.get("upload_id"); print("upload", up)
    show("check", post(who, f"/uploads/{up}/check"))
    return show("commit", post(who, f"/uploads/{up}/commit", {"reason": "x5 sweep", "by": who, **({"match": match, "acknowledge": ack} if match else {})})).get("version_id"), up

v1, _ = upload({"file": ("loans.csv", csv(R), "text/csv")}, filename="loans.csv")
st["v1"] = v1
show("pii", put("Ana", f"/datasets/{v1}/pii", {"confirm": "suggested", "by": "Ana"}))
st["root"] = get("Ana", f"/datasets/{v1}").json().get("root_id") or v1
v2, _ = upload({"file": ("loans.csv", csv(R[:250]), "text/csv")}, match={"method": "key", "columns": ["app_id"]}, ack=["holdout_identity_loss"], filename="loans.csv", root_id=st["root"], parent_version_id=v1)
st["v2"] = v2
# pending upload for upload routes
st["up"] = c.post(B + "/uploads", params={"filename": "p.csv", "project_id": st["pd"]},
                  files={"file": ("p.csv", csv(R[:30]), "text/csv")}, headers=h("Ana")).json().get("upload_id")
# multi-table
apps = [{k: r[k] for k in ("app_id", "cust", "applied_on", "income", "y")} for r in R]
jv, _ = upload([("file", ("apps.csv", csv(apps), "text/csv")), ("file", ("cust.csv", csv(CUST), "text/csv"))], name="jx", target="y")
st["jroot"] = get("Ana", f"/datasets/{jv}").json().get("root_id") or jv
for t, k in (("apps", "app_id"), ("cust", "cust")):
    j = show(f"key {t}", put("Ana", f"/datasets/{jv}/tables/{t}/key", {"columns": [k], "reason": "x5", "by": "Ana"}))
    jv = j.get("version_id") or jv
j = show("relation", put("Ana", f"/datasets/{jv}/relations/apps_cust", {"from": {"table": "apps", "columns": ["cust"]},
                                                                        "to": {"table": "cust", "columns": ["cust"]}, "cardinality": "n:1", "by": "Ana"}))
jv = j.get("version_id") or jv
st["jv"] = jv
jn = show("join", post("Ana", f"/datasets/{st['jroot']}/joins", {"name": "j1", "version_id": jv, "spec": {
    "grain": "apps", "joins": [{"type": "n:1", "table": "cust", "on": ["cust"], "prefix": "c__"}]}, "by": "Ana"}))
st["join"] = jn.get("id")
fr = show("frame", post("Ana", f"/datasets/{st['jroot']}/versions/{jv}/frames", {"join_id": st["join"], "by": "Ana"}))
st["frame"] = fr.get("id")
# governance
show("contract", post("Ana", f"/datasets/{v1}/contract/from-version/{v1}", {"by": "Ana"}))
st["cert"] = show("cert", post("Ana", f"/datasets/{v1}/certifications", {"level": "certified", "reason": "PD input", "by": "Ana"})).get("id")
show("cert decide", post("Ben", f"/certifications/{st['cert']}/decide", {"approve": True, "comment": "ok", "by": "Ben"}))
st["share"] = show("share", post("Ben", "/shares", {"object_id": v1, "grantee_team": co, "scope": "certified", "reason": "rec", "by": "Ben"})).get("id")
show("share decide", post("Bo", f"/shares/{st['share']}/decide", {"approve": True, "comment": "ok", "by": "Bo"}))
# run, registry
rr = show("run", post("Ana", "/runs", {"dataset_id": v1, "label": "y", "time_limit": 15, "by": "Ana", "project_id": st["pd"],
                                      "drop_columns": ["app_id", "applicant_name", "email"]})).get("id")
print("run", wait(f"/runs/{rr}").get("status")); st["run"] = rr
mv = show("register", post("Ana", f"/runs/{rr}/register", {"created_by": "Ana", "model_name": "pd-x5"})).get("id")
print("version", wait(f"/registry/versions/{mv}", ok=("ready", "failed", "error")).get("status")); st["mv"] = mv
b = c.post(f"{B}/registry/versions/{mv}/batches", files={"file": ("batch.csv", csv(R[:40]), "text/csv")}, data={"created_by": "Ana"},
           headers=h("Ana")).json()
st["batch"] = b.get("id"); print("batch", st["batch"], wait(f"/registry/batches/{st['batch']}").get("status"))
st["anomaly"] = show("anomaly", post("Ana", "/anomaly/runs", {"dataset_id": v1, "features": ["income", "age", "segment"], "lof": False, "by": "Ana"})).get("id")
print("anomaly", wait(f"/runs/{st['anomaly']}").get("status"))
# misc ids
n = show("note", post("Ana", f"/annotations/dataset/{v1}", {"note": "hello", "tags": ["t1"], "by": "Ana"}))
notes = (n.get("notes") or [{}]) if isinstance(n.get("notes"), list) else [{}]
st["note"] = (notes[-1] or {}).get("id") or n.get("id")
st["hold"] = show("hold", post("Ada", "/legal-holds", {"scope_kind": "dataset", "scope_id": v2, "case_ref": "C1", "reason": "x5 test", "by": "Ada"})).get("id")
show("hold release", post("Adam", f"/legal-holds/{st['hold']}/release", {"reason": "case closed", "by": "Ada"}))
st["agent"] = show("agent", post("Ana", "/agent/sessions", {"message": "list datasets", "user": "Ana", "decider": "stub"})).get("id")
st["review"] = show("review", post("Ana", f"/runs/{rr}/review", {"by": "Ana"})).get("id")
st["slice"] = show("slices", post("Ana", f"/runs/{rr}/slices", {"by": "Ana"})).get("id")
st["autopilot"] = show("autopilot", post("Ana", "/autopilot", {"dataset_id": v1, "label": "y", "by": "Ana", "dry_run": True})).get("id")
st["dreq"] = show("delete request", post("Vic", f"/datasets/{v2}/delete", {"reason": "superseded", "by": "Vic"})).get("id")
st["rreq"] = show("reset request", post("Ana", "/admin/reset-requests", {"reason": "x5 sweep fixture reset request", "by": "Ana"})).get("id")
st["champ"] = show("champion", post("Ana", f"/projects/{st['pd']}/champion", {"version_id": mv, "reason": "best model", "by": "Ana"})).get("id")
st["change"] = show("change", post("Ana", f"/registry/versions/{mv}/changes", {"kind": "threshold", "reason": "x5 change", "by": "Ana", "actor": "Ana"})).get("id")
st["shiftkey"] = show("shift", post("Ana", f"/runs/{rr}/shift", {"dataset_id": v2, "by": "Ana"})).get("key")
jobs = get("Ada", "/jobs", params={"limit": 1}).json()
jl = jobs.get("jobs") if isinstance(jobs, dict) else jobs
st["job"] = (jl or [{}])[0].get("id")
save(st)
print("STATE", st)

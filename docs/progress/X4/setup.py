"""X4 set-up (shared by every probe): teams, people, loans v1 with PII tags confirmed, a contract + certification, a share to
Collections (scope certified, masked), a trained run, a registered model version, a batch, an anomaly run.

    python setup.py <base> <state.json>"""
from x4lib import B, R, audit_rows, c, csv, get, h, post, put, save, show, wait  # noqa: F401

mode = get("Ada", "/me").json().get("authz_mode")
print(f"# authz mode: {mode}")
st = {"mode": mode}

cr = post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"}).json()["id"]
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer"), ("Mo", "member"), ("Vic", "viewer"),
                  ("Rita", "risk_reviewer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = post("Ada", "/teams", {"name": "Collections", "by": "Ada"}).json()["id"]
for who, role in (("Cole", "owner"), ("Carl", "member")):
    post("Ada", f"/teams/{co}/members", {"name": who, "role": role, "by": "Ada"})
fr = post("Ada", "/teams", {"name": "Fraud", "by": "Ada"}).json()["id"]
for who, role in (("Fox", "owner"), ("Fay", "member")):
    post("Ada", f"/teams/{fr}/members", {"name": who, "role": role, "by": "Ada"})
print("members:", {t: [(m["display_name"], m["role"]) for m in get("Ada", f"/teams/{t}").json()["members"]] for t in (cr, co, fr)})
st.update(cr=cr, co=co, fr=fr)
st["pd"] = post("Ana", "/projects", {"name": "PD", "team_id": cr, "by": "Ana"}).json()["id"]
st["rp"] = post("Cole", "/projects", {"name": "Recoveries", "team_id": co, "by": "Cole"}).json()["id"]
st["fp"] = post("Fay", "/projects", {"name": "Fraud lab", "team_id": fr, "by": "Fay"}).json()["id"]

u = c.post(B + "/uploads", params={"filename": "loans.csv", "project_id": st["pd"]},
           files={"file": ("loans.csv", csv(R), "text/csv")}, headers=h("Ana")).json()["upload_id"]
post("Ana", f"/uploads/{u}/check")
v1 = show("v1 commit", post("Ana", f"/uploads/{u}/commit", {"reason": "loans v1", "by": "Ana"}), ["version_id"])["version_id"]
st["v1"] = v1
show("pii confirm", put("Ana", f"/datasets/{v1}/pii", {"confirm": "suggested", "by": "Ana"}), ["changed", "pending_review"])
print("tags:", [(t["column"], t["tag"], t["mask"]) for t in get("Ana", f"/datasets/{v1}/pii").json()["tags"] if t["mask"] != "none"])
st["root"] = get("Ana", f"/datasets/{v1}").json().get("root_id") or v1
show("contract", post("Ana", f"/datasets/{v1}/contract/from-version/{v1}", {"by": "Ana"}), ["id", "version"], cut=100)
cid = show("cert request", post("Ana", f"/datasets/{v1}/certifications", {"level": "certified", "reason": "PD input", "by": "Ana"}),
           ["id", "status"])["id"]
show("cert decide Ben", post("Ben", f"/certifications/{cid}/decide", {"approve": True, "comment": "ok", "by": "Ben"}),
     ["status", "expires_at"])
st["cert"] = cid
sid = show("share → collections (certified, masked)", post("Ben", "/shares", {"object_id": v1, "grantee_team": co, "scope": "certified",
                                                                              "reason": "recoveries", "by": "Ben"}), ["id"])["id"]
show("share decide Bo", post("Bo", f"/shares/{sid}/decide", {"approve": True, "comment": "ok", "by": "Bo"}), ["status"])
st["share_co"] = sid

rr = show("run", post("Ana", "/runs", {"dataset_id": v1, "label": "y", "time_limit": 20, "by": "Ana", "project_id": st["pd"],
                                       "drop_columns": ["app_id", "applicant_name", "email"]}), ["id"])["id"]
print("run:", wait(f"/runs/{rr}")["status"])
st["run"] = rr
reg = show("register", post("Ana", f"/runs/{rr}/register", {"created_by": "Ana", "model_name": "pd-x4"}), ["id", "status"])
mv = reg.get("id")
print("version:", wait(f"/registry/versions/{mv}", ok=("ready", "failed", "error")).get("status"))
st["mv"] = mv
b = c.post(f"{B}/registry/versions/{mv}/batches", files={"file": ("batch.csv", csv(R[:50]), "text/csv")},
           data={"created_by": "Ana"}, headers=h("Ana")).json()
st["batch"] = b.get("id")
print("batch:", st["batch"], wait(f"/registry/batches/{st['batch']}", ok=("succeeded", "failed", "done", "error", "completed")).get("status"))
ar = post("Ana", "/anomaly/runs", {"dataset_id": v1, "features": ["income", "age", "ssn", "segment"], "lof": False, "by": "Ana"}).json()
st["anomaly"] = ar.get("id")
print("anomaly:", wait(f"/runs/{st['anomaly']}").get("status"))
save(st)
print("state:", st)

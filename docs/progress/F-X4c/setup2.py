"""F-X4c set-up on top of X4's setup.py: a second run that does NOT use ssn (dropped), registered, with a batch that
still carries the ssn column; plus warm-ups of more value-bearing caches (credit report, shift, export unmasked).

    python setup2.py <base> <state.json>"""
from x4lib import R, c, B, csv, get, h, load, post, save, show, wait

st = load()
v1 = st["v1"]
r2 = show("run2 (ssn dropped)", post("Ana", "/runs", {"dataset_id": v1, "label": "y", "time_limit": 20, "by": "Ana", "project_id": st["pd"],
                                                    "drop_columns": ["app_id", "applicant_name", "email", "ssn"]}), ["id"])["id"]
print("run2:", wait(f"/runs/{r2}")["status"])
st["run2"] = r2
mv2 = show("register run2", post("Ana", f"/runs/{r2}/register", {"created_by": "Ana", "model_name": "pd-x4-nossn"}), ["id", "status"]).get("id")
print("version2:", wait(f"/registry/versions/{mv2}", ok=("ready", "failed", "error")).get("status"))
st["mv2"] = mv2
b = c.post(f"{B}/registry/versions/{mv2}/batches", files={"file": ("batch2.csv", csv(R[50:100]), "text/csv")},
           data={"created_by": "Ana"}, headers=h("Ana")).json()
st["batch2"] = b.get("id")
print("batch2:", st["batch2"], wait(f"/registry/batches/{st['batch2']}", ok=("succeeded", "failed", "error")).get("status"))
for n, r in (("credit report mv", get("Ana", f"/registry/versions/{st['mv']}/credit-report")),
             ("export v1 unmasked", get("Ana", f"/datasets/{v1}/export", params={"by": "Ana", "unmasked": 1, "reason": "purge check", "format": "csv"})),
             ("fairness ssn", get("Ana", f"/runs/{st['run']}/fairness", params={"group": "ssn"})),
             ("recourse", get("Ana", f"/registry/versions/{st['mv']}/recourse")),
             ("card", get("Ana", f"/registry/versions/{st['mv']}/card")),
             ("predict mv2 (before)", post("Ana", f"/registry/versions/{mv2}/predict", {"row": {k: v for k, v in R[0].items() if k != "y"}, "explain": False}))):
    print(f"## {n} → HTTP {r.status_code}")
save(st)

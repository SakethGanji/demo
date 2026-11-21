"""P19 verification chain: manual API calls chained as the UI makes them. Usage: python chain.py [base] (API on :8000,
ENSEMBLE_ADMINS=Ada, fresh data dir, default mode = audit)."""
import io, json, sys
import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
c = httpx.Client(timeout=300)


def h(who):
    return {"X-Ensemble-Actor": who} if who else {}


def show(n, r, keys=None):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    if keys and isinstance(j, dict):
        j = {k: j.get(k) for k in keys}
    print(f"## {n} → HTTP {r.status_code}" + (f" | X-Ensemble-Authz: {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else "")
          + f" | X-Request-Id: {r.headers.get('x-request-id')}")
    print("   ", json.dumps(j, default=str)[:600])
    return j


# 1. Ada (admin) makes the team and its owner; Ana (owner) adds Ben as viewer
t = show("1 Ada POST /teams {Credit Risk}", c.post(f"{B}/teams", json={"name": "Credit Risk", "by": "Ada"}, headers=h("Ada")), ["id", "slug", "my_role"])
show("1b Ada POST /teams/credit-risk/members {Ana owner}", c.post(f"{B}/teams/credit-risk/members", json={"name": "Ana", "role": "owner", "by": "Ada"}, headers=h("Ada")), ["members"])
show("1c Ana POST /teams/credit-risk/members {Ben viewer}", c.post(f"{B}/teams/credit-risk/members", json={"name": "Ben", "role": "viewer", "by": "Ana"}, headers=h("Ana")), ["members"])
show("1d Ben POST /teams {Ben's team} (not admin, self-service off)", c.post(f"{B}/teams", json={"name": "Bens team", "by": "Ben"}, headers=h("Ben")), ["id", "slug"])
p = show("2 Ana POST /projects {PD scorecard, team credit-risk}", c.post(f"{B}/projects", json={"name": "PD scorecard", "team_id": "credit-risk", "by": "Ana"}, headers=h("Ana")), ["id", "team_id"])
csv = "acct,zip,income,age,y\n" + "".join(f"A{i:04d},{i % 90000:05d},{30000 + i * 37 % 50000},{20 + i % 50},{i % 3 == 0:d}\n" for i in range(600))
d = show("3 Ana POST /datasets (multipart, project_id)", c.post(f"{B}/datasets", files={"file": ("accounts.csv", io.BytesIO(csv.encode()), "text/csv")},
                                                                data={"project_id": p["id"], "target": "y", "by": "Ana"}, headers=h("Ana")), ["id", "rows", "team_id"])
ds = d["id"]
edit = {"ops": [{"op": "set_cells", "cells": [{"row_id": 3, "column": "income", "value": 51000}]}], "reason": "fix a typo in income", "by": "Ben"}
e = show("4 Ben (viewer) POST /datasets/{id}/edit", c.post(f"{B}/datasets/{ds}/edit", json=edit, headers=h("Ben")), ["id", "created"])
a = show("5 Ana GET /teams/credit-risk/audit?action=authz_would_deny", c.get(f"{B}/teams/credit-risk/audit", params={"action": "authz_would_deny"}, headers=h("Ana")))
for r in a["entries"]:
    print("    ", r["id"], r["actor"], r["object_kind"], r["object_id"], r["principal_id"], r["auth"], r["request_id"], r["decision"], r["row_hash"][:12])
show("6 Ana POST /datasets/{id}/edit (owner)", c.post(f"{B}/datasets/{ds}/edit", json={**edit, "ops": [{"op": "set_cells", "cells": [{"row_id": 4, "column": "income", "value": 52000}]}], "by": "Ana",
                                                                   "reason": "fix another typo in income"}, headers=h("Ana")), ["id", "created"])
show("7 Ben GET /datasets/{id}/rows (viewer: allowed, no header)", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 2}, headers=h("Ben")), ["total"])
show("8 Dee (only tm_default) GET /datasets/{id}/grid", c.get(f"{B}/datasets/{ds}/grid", params={"limit": 2}, headers=h("Dee")), ["total"])
show("9 anonymous POST /datasets/{id}/splits", c.post(f"{B}/datasets/{ds}/splits", json={"target": "y"}), ["dataset_id", "drawn_now"])
show("10 Ana POST /authz/check {data.version, dataset, principal Ben}", c.post(f"{B}/authz/check", json={"action": "data.version", "object_kind": "dataset", "object_id": ds, "principal": "Ben"}, headers=h("Ana")),
     ["allow", "needed", "role", "team", "reason"])
show("11 Ben POST /authz/check {data.preview, dataset}", c.post(f"{B}/authz/check", json={"action": "data.preview", "object_kind": "dataset", "object_id": ds}, headers=h("Ben")), ["allow", "needed", "role", "reason"])
import time
run = show("11b Ana POST /runs {dataset, time_limit 30}", c.post(f"{B}/runs", json={"dataset_id": ds, "label": "y", "time_limit": 30, "by": "Ana"}, headers=h("Ana")), ["id", "status"])
while (st := c.get(f"{B}/runs/{run['id']}").json()["status"]) not in ("succeeded", "failed", "cancelled"):
    time.sleep(3)
print("    run", run["id"], st)
mv = show("11c Ana POST /runs/{id}/register", c.post(f"{B}/runs/{run['id']}/register", json={"created_by": "Ana", "model_name": "pd-accounts"}, headers=h("Ana")), ["id", "status"])
while (st := c.get(f"{B}/registry/versions/{mv['id']}").json()["status"]) not in ("ready", "failed"):
    time.sleep(3)
print("    version", mv["id"], st)
show("11d Ben POST /registry/versions/{id}/transition {Staging}", c.post(f"{B}/registry/versions/{mv['id']}/transition", json={"stage": "Staging", "actor": "Ben"}, headers=h("Ben")), ["stage", "detail"])
show("11e Ana POST /registry/versions/{id}/transition {Production} (owner: model.promote_production allowed; the four-eyes rule answers)",
     c.post(f"{B}/registry/versions/{mv['id']}/transition", json={"stage": "Production", "actor": "Ana"}, headers=h("Ana")), ["stage", "detail"])
cov = c.get(f"{B}/authz/coverage", headers=h("Ana")).json()
print(f"## 12 GET /authz/coverage → mode {cov['mode']}, chain {cov['identity_chain']}, routes {cov['n_routes']}, must {cov['n_must']}, covered {cov['n_covered']}, uncovered {cov['n_uncovered']}")
print("    sample:", [(r["method"], r["path"], r.get("action") or r.get("public")) for r in cov["routes"] if r["path"].endswith(("/edit", "/transition", "/authz/check"))])
lst = c.get(f"{B}/datasets", headers=h("Ana")).json()["datasets"]
print("## 13 GET /datasets rows with team:", sum(1 for r in lst if r.get("team")), "/", len(lst), "|", [(r["id"], r["team"]["slug"]) for r in lst if r["id"] in (ds, e.get("id"))])
for path in ("/runs", "/registry", "/registry/versions", "/registry/summary", "/data/search", "/projects"):
    j = c.get(B + path, headers=h("Ana")).json()
    k = {"/runs": "runs", "/registry": "versions", "/registry/versions": "versions", "/registry/summary": "production", "/data/search": "datasets", "/projects": "projects"}[path]
    print(f"    {path}: {sum(1 for r in j[k] if r.get('team'))}/{len(j[k])} rows with team")
show("14 Ana GET /datasets/{id}/verify (split check 3 chain link)", c.get(f"{B}/datasets/{ds}/verify", headers=h("Ana")), ["ok", "splits_ok"])
v = c.get(f"{B}/datasets/{ds}/verify", headers=h("Ana")).json()
for sp in v.get("splits") or []:
    for ch in sp.get("checks") or []:
        if ch["n"] == 3:
            print("    check 3:", ch["ok"], ch["detail"])
for team in ("credit-risk", "tm_default"):
    show(f"15 GET /teams/{team}/audit/verify", c.get(f"{B}/teams/{team}/audit/verify", headers=h("Ana")))

"""P23 verification chain, the same on both engines: team → project → upload → next version with a match → edit →
run (small) → register → GET /teams/{t}/audit/verify (+ GET /datasets/{v}/verify). Usage: python chain.py <base> <tag>.
API started with ENSEMBLE_ADMINS=Ada and a fresh data dir; ENSEMBLE_DB_URL set for Postgres, unset for SQLite."""
import io, json, sys, time
import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
TAG = sys.argv[2] if len(sys.argv) > 2 else "x"
c = httpx.Client(timeout=600)
H = {"X-Ensemble-Actor": "Ana"}


def show(n, r, keys=None):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:300]
    v = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    print(f"## {n} → HTTP {r.status_code}\n    {json.dumps(v, default=str)[:700]}")
    if r.status_code >= 400:
        raise SystemExit(f"FAILED at {n}")
    return j


def csv(fix=0):
    rows = []
    for i in range(1000):
        inc = 30000 + i * 37 % 50000 + (1000 if i < fix else 0)
        rows.append(f"A{i:04d},{inc},{20 + i % 50},{(i * 7) % 3 == 0:d}\n")
    return ("acct,income,age,y\n" + "".join(rows)).encode()


t = show("1 Ada POST /teams", c.post(f"{B}/teams", json={"name": f"P23 Risk {TAG}", "by": "Ada"}, headers={"X-Ensemble-Actor": "Ada"}), ["id", "slug"])
show("1b Ada POST /teams/{t}/members {Ana owner}", c.post(f"{B}/teams/{t['id']}/members", json={"name": "Ana", "role": "owner", "by": "Ada"},
                                                           headers={"X-Ensemble-Actor": "Ada"}), ["members"])
p = show("2 Ana POST /projects", c.post(f"{B}/projects", json={"name": f"P23 project {TAG}", "team_id": t["id"], "by": "Ana"}, headers=H), ["id", "team_id"])
u = show("3 POST /uploads (v1)", c.post(f"{B}/uploads", params={"filename": "accounts.csv", "project_id": p["id"], "target": "y", "by": "Ana",
                                                                  "name": f"accounts {TAG}"}, content=csv(), headers=H), ["upload_id", "status"])
show("3b POST /uploads/{u}/check", c.post(f"{B}/uploads/{u['upload_id']}/check", headers=H), ["status"])
v1 = show("3c POST /uploads/{u}/commit", c.post(f"{B}/uploads/{u['upload_id']}/commit", json={"reason": "P23 v1"}, headers=H),
          ["version_id", "version_no", "team_id"])["version_id"]
u2 = show("4 POST /uploads parent_version_id=v1 (15 corrected incomes)", c.post(f"{B}/uploads", params={"filename": "accounts.csv", "parent_version_id": v1, "by": "Ana"},
                                                                                content=csv(15), headers=H), ["upload_id"])
ck = show("4b POST /uploads/{u2}/check", c.post(f"{B}/uploads/{u2['upload_id']}/check", headers=H), ["status"])
print("    preselect", ck["next_version"]["preselect"])
v2r = show("4c POST /uploads/{u2}/commit {match: key acct}", c.post(f"{B}/uploads/{u2['upload_id']}/commit",
                                                                     json={"match": {"method": "key", "columns": ["acct"]}, "reason": "15 corrected incomes"}, headers=H),
           ["version_id", "version_no", "match"])
v2 = v2r["version_id"]
show("4d GET /datasets/v1/diff/v2", c.get(f"{B}/datasets/{v1}/diff/{v2}", headers=H), ["rows"])
e = show("5 POST /datasets/v2/edit {set_cells}", c.post(f"{B}/datasets/{v2}/edit", json={"ops": [{"op": "set_cells", "cells": [
    {"row_id": 3, "column": "income", "value": 51000}]}], "reason": "fix a typo in income", "by": "Ana"}, headers=H), ["id", "created", "version_no"])
v3 = e["id"]
run = show("6 POST /runs {v3, time_limit 20}", c.post(f"{B}/runs", json={"dataset_id": v3, "label": "y", "time_limit": 20, "by": "Ana",
                                                                         "presets": "medium_quality"}, headers=H), ["id", "status"])
t0 = time.time()
while (st := c.get(f"{B}/runs/{run['id']}", headers=H).json()["status"]) not in ("succeeded", "failed", "cancelled"):
    time.sleep(3)
print(f"    run {run['id']} {st} in {time.time() - t0:.0f}s")
mv = show("7 POST /runs/{id}/register", c.post(f"{B}/runs/{run['id']}/register", json={"created_by": "Ana", "model_name": f"p23-{TAG}"}, headers=H),
          ["id", "status"])
while (st := c.get(f"{B}/registry/versions/{mv['id']}", headers=H).json()["status"]) not in ("ready", "failed"):
    time.sleep(3)
print("    model version", mv["id"], st)
show("8 GET /teams/{t}/audit/verify", c.get(f"{B}/teams/{t['id']}/audit/verify", headers=H))
show("8b GET /teams/tm_default/audit/verify", c.get(f"{B}/teams/tm_default/audit/verify", headers={"X-Ensemble-Actor": "Ada"}))
show("9 GET /datasets/v3/verify", c.get(f"{B}/datasets/{v3}/verify", headers=H), ["ok", "splits_ok"])
a = c.get(f"{B}/teams/{t['id']}/audit", headers=H).json()
print("    audit actions:", [r["action"] for r in a["entries"]][::-1])
json.dump({"team": t["id"], "project": p["id"], "v1": v1, "v2": v2, "v3": v3, "run": run["id"], "mv": mv["id"]},
          open(f"/tmp/claude-0/p23/ids_{TAG}.json", "w"))

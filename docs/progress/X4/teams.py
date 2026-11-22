"""X4: team management against C §1.3 (`team.manage`: members below owner; owners / maintainers: `team.settings`), the
risk-reviewer axis, the SQL source reaching other databases, and the PATCH /datasets/{id} action.

    python teams.py <base> <state.json>"""
from x4lib import c, B, get, h, load, patch, post, show

st = load()
cr, v1 = st["cr"], st.get("new_v") or st["v1"]
print(f"# mode {st['mode']}")


def members():
    return [(m["display_name"], m["role"]) for m in get("Ada", f"/teams/{cr}").json()["members"]]


print("# 0. PATCH /datasets/{id} is guarded by run.train (a member)")
show("2a Mo (member) PATCH /datasets/{v} {description}", patch("Mo", f"/datasets/{v1}", {"description": "changed by a member", "by": "Mo"}),
     None, cut=200)

print("\n# 1. team management")
print("before:", members())
show("1a Ana (maintainer) PATCH /teams/cr/members/Bo {role: viewer}  (demote an owner)",
     patch("Ana", f"/teams/{cr}/members/Bo", {"role": "viewer", "by": "Ana"}), ["display_name", "role"], cut=200)
show("1b Ana (maintainer) DELETE /teams/cr/members/Bo  (remove an owner)",
     c.delete(f"{B}/teams/{cr}/members/Bo", params={"by": "Ana"}, headers=h("Ana")), cut=200)
show("1c Ana (maintainer) PATCH /teams/cr/members/Mo {role: risk_reviewer}",
     patch("Ana", f"/teams/{cr}/members/Mo", {"role": "risk_reviewer", "by": "Ana"}), cut=200)
show("1d Ana (maintainer) PATCH /teams/cr/members/Ana {role: risk_reviewer}  (herself)",
     patch("Ana", f"/teams/{cr}/members/Ana", {"role": "risk_reviewer", "by": "Ana"}), cut=200)
print("after:", members())
show("1e /authz/check Ana model.review on the team", post("Ana", "/authz/check", {"action": "model.review", "object_kind": "model_version",
                                                                                 "object_id": st["mv"]}), ["allow", "reason"], cut=200)

print("\n# 3. the SQL source reads other databases under the data dir (Fay, Fraud member, in her own project)")
show("3a Fay POST /sql/preview {sqlite:///mlflow/mlflow.db, SELECT name FROM registered_models}",
     post("Fay", f"/sql/preview?project_id={st['fp']}", {"url": "sqlite:///mlflow/mlflow.db",
                                                         "query": "SELECT name, description FROM registered_models"}), ["rows"], cut=300)
show("3b Fay POST /sql/preview {sqlite:///ensemble.sqlite3}", post("Fay", f"/sql/preview?project_id={st['fp']}",
     {"url": "sqlite:///ensemble.sqlite3", "query": "SELECT 1"}), None, cut=200)

PLATFORM = __import__("os").environ.get("X4_PLATFORM_URL")
if PLATFORM:
    print("\n# 4. Postgres deployment: the SQL source pointed at the platform's own database (the server's connection settings)")
    for qy in ("SELECT actor, action, team_id FROM audit_log ORDER BY id DESC LIMIT 3",
               "SELECT display_name, is_admin FROM principals WHERE is_admin = 1",
               "SELECT id, grantee_team, pii FROM shares",
               "SELECT result FROM batch_jobs WHERE result IS NOT NULL LIMIT 1",
               "SELECT pg_read_file('/etc/hostname') AS f"):
        show(f"4 Fay POST /sql/preview {{{PLATFORM.split('@')[-1]}, {qy[:40]}…}}",
             post("Fay", f"/sql/preview?project_id={st['fp']}", {"url": PLATFORM, "query": qy}), ["rows"], cut=300)

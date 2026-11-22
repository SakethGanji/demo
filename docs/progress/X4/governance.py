"""X4: cross-team reach after a share ends (expiry, the certification behind a `certified` scope expiring, revoke), joins
over unshared inputs, and legal holds on every destructive path. Time is simulated where a period is long: the
certification's expires_at is moved into the past in the scratch database (X4_DB = the SQLite file or a postgresql:// URL).

    X4_DB=… python governance.py <base> <state.json>"""
import datetime as dt
import os
import time

from x4lib import B, R, c, csv, get, h, load, post, save, show

st = load()
v1, cr, co, fr, pd, fp = st["v1"], st["cr"], st["co"], st["fr"], st["pd"], st["fp"]
DB = os.environ["X4_DB"]


def sql(q, a=()):
    if DB.startswith("postgres"):
        import psycopg
        with psycopg.connect(DB, autocommit=True) as con:
            con.execute(q.replace("?", "%s"), a)
    else:
        import sqlite3
        con = sqlite3.connect(DB)
        con.execute(q, a)
        con.commit()
        con.close()


def probe(tag, who, ds, proj):
    out = []
    for name, r in (("GET /datasets/v", get(who, f"/datasets/{ds}")),
                    ("rows", get(who, f"/datasets/{ds}/rows", params={"by": who, "limit": 2})),
                    ("export", get(who, f"/datasets/{ds}/export", params={"by": who})),
                    ("list", get(who, "/datasets", params={"root_id": st["root"]})),
                    ("intake", get(who, f"/datasets/{ds}/intake")),
                    ("data-doctor", get(who, f"/datasets/{ds}/data-doctor")),
                    ("POST /runs", post(who, "/runs", {"dataset_id": ds, "label": "y", "time_limit": 5, "project_id": proj,
                                                       "by": who, "dry_run": True, "drop_columns": ["app_id"]}))):
        extra = ""
        if name == "list":
            extra = f" total={r.json().get('total')}"
        if name == "POST /runs" and r.status_code in (200, 201, 202):
            rid = r.json().get("id")
            if rid:
                c.post(f"{B}/runs/{rid}/cancel", headers=h(who))
        out.append(f"{name} {r.status_code}{extra}")
    print(f"    {tag}: " + " · ".join(out))


print(f"# mode {st['mode']}")
print("\n# 1. Carl (Collections) holds a masked share of loans with scope `certified`; v1's certification expires")
probe("1a before", "Carl", v1, st["rp"])
sql("UPDATE certifications SET expires_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", st["cert"]))
show("1b GET /certifications/{c} (expires_at moved to 2026-01-01)", get("Ana", f"/certifications/{st['cert']}"), ["status", "expires_at"], cut=150)
probe("1c after the certification expired", "Carl", v1, st["rp"])

print("\n# 2. a share to Fraud (scope all, masked) that expires in 75 s")
exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=75)).isoformat(timespec="seconds")
sid = show("2a Ben POST /shares {loans → fraud, scope all, expires_at +75 s}",
           post("Ben", "/shares", {"object_id": v1, "grantee_team": fr, "scope": "all", "expires_at": exp, "reason": "fraud pilot",
                                   "by": "Ben"}), ["id", "expires_at"])["id"]
show("2b Bo approves", post("Bo", f"/shares/{sid}/decide", {"approve": True, "comment": "ok", "by": "Bo"}), ["status", "expires_at"])
probe("2c Fay while live", "Fay", v1, fp)
u = c.post(B + "/uploads", params={"filename": "alerts.csv", "project_id": fp},
           files={"file": ("alerts.csv", csv([{"app_id": r["app_id"], "alert": i % 2} for i, r in enumerate(R[:300])]), "text/csv")},
           headers=h("Fay")).json()["upload_id"]
post("Fay", f"/uploads/{u}/check")
fds = post("Fay", f"/uploads/{u}/commit", {"reason": "alerts", "by": "Fay"}).json()["version_id"]
spec = {"grain": "alerts", "joins": [{"type": "n:1", "table": {"root_id": v1, "version_id": v1, "table": "loans"}, "on": ["app_id"],
                                       "prefix": "loan__", "columns": ["income", "segment"]}]}
jn = show("2d Fay POST /datasets/{alerts}/joins {loans v1: income, segment} (share live)",
          post("Fay", f"/datasets/{fds}/joins", {"name": "al", "spec": spec, "by": "Fay"}), ["id"], cut=120)
fr1 = show("2e Fay POST frames", post("Fay", f"/datasets/{fds}/versions/{fds}/frames", {"join_id": jn.get("id"), "by": "Fay"}),
           ["id", "n_rows"], cut=120)
time.sleep(max(0, (dt.datetime.fromisoformat(exp) - dt.datetime.now(dt.timezone.utc)).total_seconds() + 3))
show("2f GET /shares/{s} after expiry", get("Ben", f"/shares/{sid}"), ["status", "expires_at"], cut=150)
probe("2g Fay after expiry", "Fay", v1, fp)
show("2h Fay POST frames after expiry", post("Fay", f"/datasets/{fds}/versions/{fds}/frames", {"join_id": jn.get("id"), "by": "Fay"}),
     None, cut=200)
show("2i Fay POST /runs {join} after expiry", post("Fay", "/runs", {"dataset_id": fds, "join_id": jn.get("id"), "label": "alert",
                                                                     "time_limit": 5, "by": "Fay"}), None, cut=200)
show("2j Fay GET existing frame", get("Fay", f"/datasets/{fds}/frames/{fr1.get('id')}"), ["id", "n_rows", "inputs"], cut=200)
show("2k Fay POST /datasets/{alerts}/joins after expiry", post("Fay", f"/datasets/{fds}/joins", {"name": "al2", "spec": spec, "by": "Fay"}),
     None, cut=200)
st["fay_ds"] = fds

print("\n# 3. revoke Carl's share (restore the certification first)")
sql("UPDATE certifications SET expires_at = ? WHERE id = ?", ("2027-11-22T00:00:00+00:00", st["cert"]))
probe("3a Carl, certification live again", "Carl", v1, st["rp"])
show("3b Ben revokes", post("Ben", f"/shares/{st['share_co']}/revoke", {"reason": "end", "by": "Ben"}), ["status"])
probe("3c Carl after revoke", "Carl", v1, st["rp"])

print("\n# 4. legal hold on project PD; every destructive path")
hold = show("4a Ada POST /legal-holds {project PD}", post("Ada", "/legal-holds", {"scope_kind": "project", "scope_id": pd, "case_ref": "CASE-X4",
                                                                                  "reason": "regulator inquiry", "by": "Ada"}), ["id", "active"])
st["hold"] = hold.get("id")
show("4b Ana DELETE /runs/{run}", c.delete(B + f"/runs/{st['run']}", params={"by": "Ana", "reason": "cleanup"}, headers=h("Ana")), cut=200)
dr = show("4c Mo POST /datasets/v1/delete (request)", post("Mo", f"/datasets/{v1}/delete", {"reason": "superseded extract", "by": "Mo"}),
          ["id", "status"], cut=200)
if dr.get("id"):
    show("4d Ben decides the delete", post("Ben", f"/dataset-requests/{dr['id']}/decide", {"approve": True, "by": "Ben"}), cut=200)
show("4e Ana POST /datasets/v1/purge {ssn}", post("Ana", f"/datasets/{v1}/purge", {"table": "loans", "columns": ["ssn"], "reason": "erasure requests batch 4",
                                                                                 "by": "Ana"}), cut=200)
u2 = c.post(B + "/uploads", params={"filename": "loans2.csv", "project_id": pd}, files={"file": ("l.csv", csv(R[:20]), "text/csv")},
            headers=h("Ana")).json()["upload_id"]
show("4f Ana DELETE /uploads/{pending upload in PD}", c.delete(B + f"/uploads/{u2}", headers=h("Ana")), ["status"], cut=200)
show("4g Ada POST /retention/run {run_ids, tier evict}", post("Ada", "/retention/run", {"run_ids": [st["run"]], "tier": "evict",
                                                                                      "confirm": "retention", "by": "Ada"}), cut=250)
show("4h Ana POST /registry/versions/{mv}/retire", post("Ana", f"/registry/versions/{st['mv']}/retire", {"reason": "end of life", "by": "Ana"}),
     cut=200)
show("4i Ana DELETE /registry/versions/{mv}/credit-report/tape", c.delete(B + f"/registry/versions/{st['mv']}/credit-report/tape",
                                                                         headers=h("Ana")), cut=200)
save(st)

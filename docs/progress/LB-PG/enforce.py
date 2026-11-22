"""LB-PG pass (b): labeling under ENSEMBLE_AUTHZ=enforce with real people (POC mode off). Run after LB-X/chain.py, or alone.

Team "risk": ana owner (creator), olu owner, bob + rev maintainers, wen member. vik is not in the team (a task guest);
eve is not in the team (an outsider; every new name lands in tm_default only)."""
import csv, io, json, random, sys, time
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8802/api"
c = httpx.Client(base_url=B, timeout=300)
H = lambda n: {"X-Ensemble-Actor": n}
ANA, OLU, BOB, REV, WEN, VIK, EVE = (H(n) for n in ("ana", "olu", "bob", "rev", "wen", "vik", "eve"))
RES = []


def rec(step, check, ok, detail=""):
    RES.append(bool(ok))
    print(("PASS" if ok else "FAIL"), step, check, "|", str(detail)[:260], flush=True)


def J(r):
    try:
        return r.json()
    except Exception:
        return {"_text": r.text[:200]}


def code(r):
    d = J(r).get("detail")
    return d.get("code") if isinstance(d, dict) else d


TAG = str(int(time.time()))[-6:]
for n in ("ana", "olu", "bob", "rev", "wen", "vik", "eve"):
    c.get("/me", headers=H(n))
r = c.post("/teams", headers=ANA, json={"name": f"risk {TAG}", "by": "ana"})
t = J(r)["id"]
rec(0, "team created", r.status_code == 201, t)
for n, role in (("olu", "owner"), ("bob", "maintainer"), ("rev", "maintainer"), ("wen", "member")):
    r = c.post(f"/teams/{t}/members", headers=ANA, json={"name": n, "role": role, "by": "ana"})
    rec(0, f"add {n} {role}", r.status_code == 201, r.status_code)
p = J(c.post("/projects", headers=ANA, json={"name": f"lbpg {TAG}", "team_id": t, "by": "ana"}))["id"]
random.seed(3)
rows = [{"acct": f"A{i:04d}", "income": random.randint(15, 200) * 1000, "age": random.randint(19, 75),
         "y": random.choice(["bad", "good"])} for i in range(300)]
b = io.StringIO(); w = csv.DictWriter(b, list(rows[0])); w.writeheader(); w.writerows(rows)
r = c.post("/uploads", headers=ANA, params={"filename": "accts.csv", "project_id": p, "target": "y", "by": "ana", "name": f"accts {TAG}"},
           content=b.getvalue().encode())
up = J(r)["upload_id"]
c.post(f"/uploads/{up}/check", headers=ANA)
r = c.post(f"/uploads/{up}/commit", headers=ANA, json={"reason": "v1"})
v1 = J(r).get("version_id")
rec(0, "upload v1 in team risk", r.status_code == 201 and v1, (r.status_code, J(r).get("team_id")))
ds = J(c.get(f"/datasets/{v1}", headers=ANA))
table = ds.get("primary_table") or (ds.get("tables") or [{}])[0].get("name") if isinstance(ds.get("tables"), list) else ds.get("primary_table")
spec = {"columns": [{"column": "y", "role": "target", "type": "class", "classes": ["bad", "good"]}]}
r = c.post(f"/datasets/{v1}/label-layers", headers=ANA, json={"name": "y-labels", "table": table, "spec": spec})
L = J(r).get("id")
rec(1, "layer (ana, owner)", r.status_code == 201 and L, (r.status_code, table, "" if r.status_code == 201 else J(r)))
flip = {"bad": "good", "good": "bad"}
r = c.post(f"/label-layers/{L}/events", headers=BOB, json={"events": [{"row_id": i, "column": "y", "value": flip[rows[i]["y"]]} for i in range(40)]})
rec(1, "bob (maintainer) appends 40 labels", r.status_code == 201, (r.status_code, J(r).get("appended")))
st = J(c.get(f"/label-layers/{L}/status", headers=BOB, params={"version_id": v1}))
rec(1, "status as bob: ok = 40", (st.get("totals") or {}).get("ok") == 40, st.get("totals"))

# -- outsider ------------------------------------------------------------------------------------------------------------
for what, r in (("GET layer", c.get(f"/label-layers/{L}", headers=EVE)),
                ("GET status", c.get(f"/label-layers/{L}/status", headers=EVE, params={"version_id": v1})),
                ("GET events", c.get(f"/label-layers/{L}/events", headers=EVE)),
                ("POST events", c.post(f"/label-layers/{L}/events", headers=EVE, json={"events": [{"row_id": 1, "column": "y", "value": "bad"}]})),
                ("POST snapshot", c.post(f"/label-layers/{L}/snapshots", headers=EVE, json={})),
                ("POST tasks", c.post(f"/label-layers/{L}/tasks", headers=EVE, json={"name": "x", "columns": ["y"]})),
                ("GET dataset rows", c.get(f"/datasets/{v1}/rows", headers=EVE)),
                ("GET layers of dataset", c.get(f"/datasets/{v1}/label-layers", headers=EVE))):
    rec(2, f"outsider eve {what} → 403", r.status_code == 403, (r.status_code, code(r)))
r = c.get(f"/label-layers/{L}/status", params={"version_id": v1})
rec(2, "no actor header → 401/403", r.status_code in (401, 403), r.status_code)

# -- guest annotator -----------------------------------------------------------------------------------------------------
r = c.post(f"/label-layers/{L}/tasks", headers=ANA, json={"name": "t1", "columns": ["y"], "votes_per_row": 1, "gold_share": "0"})
T = J(r).get("id")
rec(3, "task t1", r.status_code == 201 and T, (r.status_code, "" if r.status_code == 201 else J(r)))
r = c.post(f"/label-tasks/{T}/members", headers=ANA, json={"principal": "vik", "role": "annotator"})
mem = J(r)
rec(3, "vik (no team role) added as annotator → guest", r.status_code == 201 and "guest" in json.dumps(mem), (r.status_code, mem))
c.post(f"/label-tasks/{T}/members", headers=ANA, json={"principal": "rev", "role": "reviewer"})
r = c.post(f"/label-tasks/{T}/fill", headers=ANA, json={"strategy": "random", "n": 10, "version_id": v1, "seed": 1})
rec(3, "fill 10", r.status_code == 200, (r.status_code, J(r)))
r = c.post(f"/label-tasks/{T}/next", headers=VIK, params={"n": 5})
items = J(r).get("items") or []
rec(3, "guest vik next → items with row context", r.status_code == 200 and items, (r.status_code, len(items), list((items or [{}])[0].keys())[:8]))
r = c.post(f"/label-tasks/{T}/answers", headers=VIK, json={"items": [{"assignment_id": it.get("assignment_id") or it.get("id"),
                                                                     "value": rows[it["row_id"]]["y"]} for it in items]})
rec(3, "guest vik answers", r.status_code == 200, (r.status_code, J(r) if r.status_code != 200 else ""))
r2 = c.post(f"/label-layers/{L}/tasks", headers=ANA, json={"name": "t2", "columns": ["y"], "votes_per_row": 1, "gold_share": "0"})
T2 = J(r2).get("id")
for what, r in (("GET layer", c.get(f"/label-layers/{L}", headers=VIK)),
                ("GET status", c.get(f"/label-layers/{L}/status", headers=VIK, params={"version_id": v1})),
                ("GET events", c.get(f"/label-layers/{L}/events", headers=VIK)),
                ("POST events", c.post(f"/label-layers/{L}/events", headers=VIK, json={"events": [{"row_id": 1, "column": "y", "value": "bad"}]})),
                ("GET dataset rows", c.get(f"/datasets/{v1}/rows", headers=VIK)),
                ("GET task quality", c.get(f"/label-tasks/{T}/quality", headers=VIK)),
                ("POST next on another task", c.post(f"/label-tasks/{T2}/next", headers=VIK)),
                ("POST snapshot", c.post(f"/label-layers/{L}/snapshots", headers=VIK, json={}))):
    rec(3, f"guest vik {what} → 403", r.status_code == 403, (r.status_code, code(r)))
r = c.get(f"/label-tasks/{T}/quality", headers=REV)
rec(3, "reviewer rev reads quality", r.status_code == 200, r.status_code)

# -- four eyes: proposals ------------------------------------------------------------------------------------------------
r = c.post(f"/label-layers/{L}/proposals", headers=BOB, json={"version_id": v1, "reason": "fix", "cells": [{"row_id": 200, "column": "y", "value": flip[rows[200]["y"]]}]})
pid = J(r).get("id")
rec(4, "bob proposes", r.status_code == 201 and pid, (r.status_code, "" if r.status_code == 201 else J(r)))
r = c.post(f"/label-proposals/{pid}/decide", headers=BOB, json={"approve": True})
rec(4, "bob decides own proposal → refused (four eyes)", r.status_code in (403, 409), (r.status_code, code(r)))
r = c.post(f"/label-proposals/{pid}/decide", headers=WEN, json={"approve": True})
rec(4, "wen (member, not maintainer) decides → 403", r.status_code == 403, (r.status_code, code(r)))
r = c.post(f"/label-proposals/{pid}/decide", headers=REV, json={"approve": True, "comment": "ok"})
d = J(r)
rec(4, "rev (maintainer, ≠ proposer) approves → applied + new version", r.status_code == 200 and (d.get("version_id") or (d.get("materialised") or {}).get("version_id")),
    (r.status_code, d.get("status"), d.get("version_id") or (d.get("materialised") or {}).get("version_id")))

# -- snapshot, pinned run, verify ----------------------------------------------------------------------------------------
r = c.post(f"/label-layers/{L}/snapshots", headers=WEN, json={"policy": {"columns": ["y"]}})
SN = J(r).get("id")
rec(5, "snapshot by wen (member; label.snapshot = member)", r.status_code == 201 and SN, (r.status_code, (J(r).get("counts") or J(r))))
rec(5, "snapshot verify ok", J(c.get(f"/label-snapshots/{SN}/verify", headers=BOB)).get("status") == "ok", "")
r = c.post("/runs", headers=ANA, json={"dataset_id": v1, "label": "y", "time_limit": 20, "feature_importance": False,
                                      "fit_kwargs": {"hyperparameters": {"GBM": {}}}, "labels": {"snapshot_id": SN}})
run = J(r).get("id")
rec(5, "run pinned to snapshot", r.status_code == 201 and run, (r.status_code, "" if r.status_code == 201 else J(r)))
s = {}
for _ in range(200):
    s = J(c.get(f"/runs/{run}", headers=ANA))
    if s.get("status") in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(2)
rec(5, "run succeeded", s.get("status") == "succeeded", (s.get("status"), s.get("error")))
vr = J(c.get(f"/label-runs/{run}/verify", headers=ANA))
rec(5, "label-runs verify ok incl. labels_fitted", vr.get("status") == "ok" and (vr.get("labels_fitted") or {}).get("ok") is True,
    (vr.get("status"), vr.get("rows_applied"), (vr.get("labels_fitted") or {}).get("status")))
r = c.get(f"/label-runs/{run}/verify", headers=EVE)
rec(5, "outsider run verify → 403", r.status_code == 403, r.status_code)
r = c.post(f"/label-snapshots/{SN}/materialise", headers=EVE, json={"version_id": v1, "reason": "x"})
rec(6, "outsider materialise → 403", r.status_code == 403, r.status_code)
r = c.post(f"/label-snapshots/{SN}/materialise", headers=BOB, json={"version_id": v1, "reason": "apply labels"})
rec(6, "materialise (bob)", r.status_code == 201, (r.status_code, J(r).get("rows_changed") if r.status_code == 201 else J(r)))
v3 = J(r).get("version_id") or (J(r).get("version") or {}).get("id")

# -- LLM: fake is refused in enforce (by design) -------------------------------------------------------------------------
c.patch(f"/teams/{t}", headers=ANA, json={"by": "ana", "policy": {"llm_labeling": {"enabled": True, "destinations": ["fake"], "max_usd_month": 5}}})
r = c.post(f"/label-layers/{L}/prompts", headers=ANA, json={"name": "p", "input": {"kind": "sql", "query": f"SELECT __row_id, income, age FROM {table}"},
                                                         "template": "income {income} age {age}: bad or good?", "column": "y", "destination": "fake"})
P = J(r).get("id")
r2 = c.post(f"/label-prompts/{P}/runs", headers=ANA, json={"version_id": v1, "max_usd": 1, "destination": "fake"}) if P else r
rec(7, "LLM fake under enforce → clean 4xx (fake_refused), not 5xx", r2.status_code < 500 and (r2.status_code >= 400), (r.status_code, r2.status_code, code(r2)))

# -- purge: owner ≠ requester --------------------------------------------------------------------------------------------
r = c.post(f"/datasets/{v3 or v1}/purge", headers=ANA, json={"columns": ["y"], "table": table, "reason": "purge test", "by": "ana"})
pg = J(r).get("id")
rec(8, "purge request (ana)", r.status_code == 201 and pg, (r.status_code, "" if r.status_code == 201 else J(r)))
r = c.post(f"/purges/{pg}/decide", headers=ANA, json={"team": t, "approve": True, "by": "ana"})
rec(8, "requester decides own purge → refused (four eyes)", r.status_code in (403, 409), (r.status_code, code(r)))
r = c.post(f"/purges/{pg}/decide", headers=BOB, json={"team": t, "approve": True, "by": "bob"})
rec(8, "maintainer bob decides → 403 (owner only)", r.status_code == 403, (r.status_code, code(r)))
r = c.post(f"/purges/{pg}/decide", headers=OLU, json={"team": t, "approve": True, "by": "olu"})
ex = (J(r).get("payload") or J(r)).get("executed") or {}
rec(8, "owner olu decides → executed, labels redacted", r.status_code == 200 and (ex.get("labels") or {}).get("events_redacted", 0) > 0,
    (r.status_code, ex.get("labels")))
evs = J(c.get(f"/label-layers/{L}/events", headers=ANA, params={"limit": 5000})).get("events", [])
left = [e for e in evs if e.get("column") == "y" and e.get("value") not in (None, "")]
rec(8, "no event on y keeps a value", evs and not left, (len(evs), len(left)))
rec(8, "run verify → purged", J(c.get(f"/label-runs/{run}/verify", headers=ANA)).get("status") == "purged", "")
rec(8, "snapshot verify → purged", J(c.get(f"/label-snapshots/{SN}/verify", headers=ANA)).get("status") == "purged", "")

# -- audit ---------------------------------------------------------------------------------------------------------------
for team in (t, "tm_default"):
    a = J(c.get(f"/teams/{team}/audit/verify", headers=ANA))
    rec(9, f"audit verify {team}", a.get("ok") is True, {k: a.get(k) for k in ("ok", "rows", "checked", "error") if k in a})
print(f"DONE {sum(RES)} / {len(RES)}")

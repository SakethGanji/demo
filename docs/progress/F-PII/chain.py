"""F-PII verification chain: manual API calls chained as the UI makes them. The same script runs in `audit` and in
`enforce` mode, on SQLite and on Postgres. Usage:

    python chain.py <base>     (API on <base>, ENSEMBLE_ADMINS=Ada, a fresh data dir)

People: Ada (platform admin); Credit Risk: Ben + Bo (owners), Ana (maintainer), Vic (viewer); Collections: Cole (owner).
`pii N` after each call = raw names / SSNs / e-mails / dates of birth of the loans table in the JSON body."""
import io
import json
import re
import sys
import time

import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
c = httpx.Client(timeout=900)

# -- the data: 600 loan applications, PII: applicant_name, ssn (9-digit BIGINT), email, dob ---------------------------------
N = 600


def row(i):
    inc = 30000 + (i * 37) % 50000
    return {"app_id": f"A{i:05d}", "applicant_name": f"Person{i:04d} Example", "ssn": 123456789 + (i * 2654435761) % 800000000,
            "email": f"user{i:04d}@example.com", "dob": f"{1950 + i % 50}-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "income": inc, "age": 20 + i % 50, "segment": ["retail", "sme", "corporate"][i % 3],
            "y": int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0),
            # tier: the upper half of SSNs, 20 % flipped (Feature Lab keeps a bin of ssn for it, below the leakage line)
            "tier": int(((i * 2654435761) % 800000000 >= 400_000_000) != (i % 5 == 0))}


def csv(rows):
    cols = list(rows[0])
    return (",".join(cols) + "\n" + "".join(",".join(str(r[k]) for k in cols) + "\n" for r in rows)).encode()


R = [row(i) for i in range(N)]
RAW = {str(r["ssn"]) for r in R} | {f"{r['ssn']:,}" for r in R} | {r["applicant_name"] for r in R} | {r["email"] for r in R} \
    | {r["dob"] for r in R}
_rx = re.compile(r"(?<![\d.])(?:" + "|".join(re.escape(v) for v in sorted(RAW, key=len, reverse=True)) + r")(?!\d)")


def h(who):
    return {"X-Ensemble-Actor": who} if who else {}


def leak(r):
    return len(_rx.findall(r.text)) if r.headers.get("content-type", "").startswith("application/json") else "n/a"


def show(n, r, keys=None, cut=500):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    shown = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    print(f"## {n} → HTTP {r.status_code}{hdr} | pii {leak(r)}")
    if cut:
        print("   ", json.dumps(shown, default=str, ensure_ascii=False)[:cut])
    return j


def post(who, url, js=None, **kw):
    return c.post(B + url, json=js, headers=h(who), **kw)


def get(who, url, **kw):
    return c.get(B + url, headers=h(who), **kw)


def wait(url, ok=("succeeded", "failed", "cancelled"), key="status", who="Ana", limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = get(who, url).json()
        if j.get(key) in ok:
            return j
        time.sleep(3)
    return j


def audit_rows(team, action, who="Ada"):
    return get(who, f"/teams/{team}/audit", params={"action": action, "limit": 200}).json().get("entries") or []


mode = get("Ada", "/me").json().get("authz_mode")
print(f"# authz mode: {mode}")

# 0. teams, people, projects -----------------------------------------------------------------------------------------------
cr = post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"}).json()["id"]
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer"), ("Vic", "viewer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = post("Ada", "/teams", {"name": "Collections", "by": "Ada"}).json()["id"]
post("Ada", f"/teams/{co}/members", {"name": "Cole", "role": "owner", "by": "Ada"})
print("0  members:", {t: [(m["display_name"], m["role"]) for m in get("Ada", f"/teams/{t}").json()["members"]] for t in (cr, co)})
pd_ = post("Ana", "/projects", {"name": "PD", "team_id": cr, "by": "Ana"}).json()["id"]
rp = post("Cole", "/projects", {"name": "Recoveries", "team_id": co, "by": "Cole"}).json()["id"]

# 1. loans v1 (Credit Risk) and its PII tags --------------------------------------------------------------------------------
u = post("Ana", "/uploads", params={"filename": "loans.csv", "project_id": pd_}, files={"file": ("loans.csv", csv(R), "text/csv")}).json()["upload_id"] \
    if False else c.post(B + "/uploads", params={"filename": "loans.csv", "project_id": pd_},
                         files={"file": ("loans.csv", csv(R), "text/csv")}, headers=h("Ana")).json()["upload_id"]
post("Ana", f"/uploads/{u}/check")
v1 = show("1a Ana commit loans v1", post("Ana", f"/uploads/{u}/commit", {"reason": "loans v1", "by": "Ana"}), ["version_id"])["version_id"]
j = show("1b Ana GET /datasets/v1/pii", get("Ana", f"/datasets/{v1}/pii"), cut=0)
print("    tags:", [(t["column"], t["tag"], t["mask"]) for t in j["tags"] if t["mask"] != "none"])
c.put(f"{B}/datasets/{v1}/pii", json={"confirm": "suggested", "by": "Ana"}, headers=h("Ana"))

# 2. anomaly top rows ------------------------------------------------------------------------------------------------------
print("\n# 2. anomaly top rows")
ar = show("2a Ana POST /anomaly/runs {v1, features income, age, ssn, segment}",
          post("Ana", "/anomaly/runs", {"dataset_id": v1, "features": ["income", "age", "ssn", "segment"], "lof": False, "by": "Ana"}),
          ["id", "status"])["id"]
print("    run:", wait(f"/runs/{ar}")["status"])
j = show("2b Vic GET /anomaly/runs/{id}/top?limit=3", get("Vic", f"/anomaly/runs/{ar}/top", params={"limit": 3, "by": "Vic"}),
         ["masked_columns", "unmasked"])
r0 = j.get("rows", [{}])[0] if isinstance(j, dict) else {}
print("    rows[0].values:", r0.get("values"), "| reasons:", [(x["feature"], x.get("value"), x.get("median")) for x in r0.get("reasons", [])][:3])
show("2c Vic …/top?unmasked=1&reason=QA", get("Vic", f"/anomaly/runs/{ar}/top", params={"limit": 3, "by": "Vic", "unmasked": 1, "reason": "QA"}),
     ["masked_columns", "unmasked", "unmasked_refused", "code", "action"], cut=400)
show("2d Ana …/top?unmasked=1 (no reason)", get("Ana", f"/anomaly/runs/{ar}/top", params={"limit": 3, "by": "Ana", "unmasked": 1}), cut=200)
j = show("2e Ana …/top?unmasked=1&reason=QA", get("Ana", f"/anomaly/runs/{ar}/top", params={"limit": 3, "by": "Ana", "unmasked": 1, "reason": "QA"}),
         ["masked_columns", "unmasked"])
print("    rows[0].values:", (j.get("rows") or [{}])[0].get("values"))

# 3. a trained run: shift check, active-learning queue, annotations --------------------------------------------------------
print("\n# 3. a trained run (label y; features ssn, income, age, segment)")
rr = show("3a Ana POST /runs {v1, label y}", post("Ana", "/runs", {"dataset_id": v1, "label": "y", "time_limit": 20, "by": "Ana",
                                                                  "drop_columns": ["app_id", "applicant_name", "email", "dob", "tier"]}),
          ["id", "status"])["id"]
print("    run:", wait(f"/runs/{rr}")["status"])

sh = show("3b Ana POST /runs/{id}/shift {holdout}", post("Ana", f"/runs/{rr}/shift", {"source": "holdout", "null_refits": 0,
                                                                                     "permutations": 200, "shuffle_sets": 1,
                                                                                     "time_limit": 10, "fit_kwargs": {"num_bag_folds": 2}}),
          ["key", "status"])
key = sh.get("key")
st = wait(f"/runs/{rr}/shift/{key}", ok=("succeeded", "failed"))
print("    shift:", st.get("status"), st.get("error"))
j = show("3c Vic GET /runs/{id}/shift/{key}", get("Vic", f"/runs/{rr}/shift/{key}", params={"by": "Vic"}), ["masked_columns"])
f = {x["feature"]: x for x in (j.get("result") or {}).get("features") or []}
print("    ssn:", {k: f.get("ssn", {}).get(k) for k in ("psi", "mean_expected", "mean_actual", "std_expected")},
      "| income:", {k: f.get("income", {}).get(k) for k in ("mean_expected", "mean_actual")})
j = show("3d Ana …/shift/{key}?unmasked=1&reason=QA", get("Ana", f"/runs/{rr}/shift/{key}", params={"by": "Ana", "unmasked": 1, "reason": "QA"}),
         ["masked_columns", "unmasked"])
f = {x["feature"]: x for x in (j.get("result") or {}).get("features") or []}
print("    ssn:", {k: f.get("ssn", {}).get(k) for k in ("mean_expected", "mean_actual")})
j = show("3e Vic GET …/shift/{key}/rows?limit=2", get("Vic", f"/runs/{rr}/shift/{key}/rows", params={"by": "Vic", "limit": 2}),
         ["rows", "masked_columns", "unmasked"])
show("3f Vic …/shift/{key}/rows?unmasked=1&reason=QA", get("Vic", f"/runs/{rr}/shift/{key}/rows",
                                                          params={"by": "Vic", "limit": 2, "unmasked": 1, "reason": "QA"}),
     ["masked_columns", "unmasked", "unmasked_refused", "code"], cut=300)

al = show("3g Ana POST /runs/{id}/active-learning", post("Ana", f"/runs/{rr}/active-learning",
                                                       {"budget": 4, "rounds": 1, "seeds": 1, "compare_random": False, "time_limit": 10}),
          ["status"], cut=100)
for _ in range(100):
    j = get("Ana", f"/runs/{rr}/active-learning").json()
    if (j.get("queue") or {}).get("queue") or j.get("status") in ("failed",):
        break
    time.sleep(3)
print("    active learning:", j.get("status"), j.get("error"), "| items", len((j.get("queue") or {}).get("queue") or []))
j = show("3h Vic GET /runs/{id}/active-learning", get("Vic", f"/runs/{rr}/active-learning", params={"by": "Vic"}), ["masked_columns", "unmasked"])
it = ((j.get("queue") or {}).get("queue") or [{}])[0]
print("    queue[0]:", {k: it.get(k) for k in ("row", "features", "text", "suggested")})
j = show("3i Ana …/active-learning?unmasked=1&reason=labelling", get("Ana", f"/runs/{rr}/active-learning",
                                                                     params={"by": "Ana", "unmasked": 1, "reason": "labelling"}),
         ["masked_columns", "unmasked"])
print("    queue[0].features:", ((j.get("queue") or {}).get("queue") or [{}])[0].get("features"))
show("3j Vic …/active-learning?unmasked=1&reason=QA", get("Vic", f"/runs/{rr}/active-learning", params={"by": "Vic", "unmasked": 1, "reason": "QA"}),
     ["masked_columns", "unmasked_refused", "code"], cut=300)

hold = set(get("Ana", f"/runs/{rr}/holdout-predictions", params={"by": "Ana"}).json().get("rows") or [])
train = [i for i in range(N) if i not in hold][:60]
lines = ["row,annotator,label"] + [f"{r},{a},{(R[r]['y'] if (r + k) % 5 else 1 - R[r]['y'])}" for r in train for k, a in enumerate(("ann1", "ann2", "ann3"))]
show("3k Ana POST /runs/{id}/annotations/upload (60 rows × 3 annotators)",
     c.post(f"{B}/runs/{rr}/annotations/upload", files={"file": ("ann.csv", "\n".join(lines).encode(), "text/csv")},
            data={"mode": "replace"}, headers=h("Ana")), ["status", "n_raw"], cut=150)
for _ in range(200):
    j = get("Ana", f"/runs/{rr}/annotations").json()
    if j.get("status") in ("ready", "failed"):
        break
    time.sleep(3)
print("    annotations:", j.get("status"), j.get("error"))
j = show("3l Vic GET /runs/{id}/annotations?limit=2", get("Vic", f"/runs/{rr}/annotations", params={"by": "Vic", "limit": 2}),
         ["masked_columns", "unmasked", "show_columns"])
print("    queue.items[0].fields:", ((j.get("queue") or {}).get("items") or [{}])[0].get("fields"))
j = show("3m Ana …/annotations?unmasked=1&reason=adjudication", get("Ana", f"/runs/{rr}/annotations",
                                                                    params={"by": "Ana", "limit": 2, "unmasked": 1, "reason": "adjudication"}),
         ["masked_columns", "unmasked"])
print("    queue.items[0].fields:", ((j.get("queue") or {}).get("items") or [{}])[0].get("fields"))

# 4. Feature Lab, split options, intake ---------------------------------------------------------------------------------------
print("\n# 4. Feature Lab, split options")
fl = show("4a Ana POST /datasets/v1/feature-lab {target tier, entity ssn, bin ssn, log income; apply false}",
          post("Ana", f"/datasets/{v1}/feature-lab", {"target": "tier", "entity": "ssn", "apply": False, "by": "Ana", "time_limit": 60,
                                                       "candidates": [{"op": "bin", "col": "ssn", "k": 4}, {"op": "log", "col": "income"}]}),
          ["job_id", "id", "status"])
jid = fl.get("job_id") or fl.get("id")
j = wait(f"/datasets/{v1}/feature-lab/{jid}", ok=("succeeded", "failed"))
print("    job:", j.get("status"), j.get("error"))
j = show("4b Vic GET /datasets/v1/feature-lab/{job}", get("Vic", f"/datasets/{v1}/feature-lab/{jid}", params={"by": "Vic"}), ["masked_columns"])
print("    journal:", [(e.get("name"), (e.get("transform") or {}).get("edges"), e.get("status")) for e in j.get("journal") or []],
      "| result.kept:", [(k.get("name"), k.get("edges")) for k in (j.get("result") or {}).get("kept") or []])
j = show("4c Ana …/feature-lab/{job}?unmasked=1&reason=QA", get("Ana", f"/datasets/{v1}/feature-lab/{jid}",
                                                              params={"by": "Ana", "unmasked": 1, "reason": "QA"}), ["masked_columns", "unmasked"])
print("    result.kept:", [(k.get("name"), k.get("edges")) for k in (j.get("result") or {}).get("kept") or []])

j = show("4d Vic GET /datasets/v1/split-options", get("Vic", f"/datasets/{v1}/split-options", params={"by": "Vic"}), ["masked_columns"])
print("    date_columns:", j.get("date_columns"), "| warnings:", j.get("warnings"))
j = show("4e Ana …/split-options?unmasked=1&reason=QA", get("Ana", f"/datasets/{v1}/split-options",
                                                           params={"by": "Ana", "unmasked": 1, "reason": "QA"}), ["unmasked"])
print("    date_columns:", j.get("date_columns"))

# 5. search text in the dataset_read audit rows ---------------------------------------------------------------------------
print("\n# 5. q in dataset_read audit rows")
show("5a Vic GET /datasets/v1/rows?q=Person0007", get("Vic", f"/datasets/{v1}/rows", params={"q": "Person0007", "by": "Vic", "limit": 2}),
     ["searched_columns", "masked_columns"], cut=200)
show("5b Vic GET /datasets/v1/grid?q=user0042", get("Vic", f"/datasets/{v1}/grid", params={"q": "user0042", "by": "Vic"}), ["q"], cut=100)
rows = [r for r in audit_rows(cr, "dataset_read") if (r.get("details") or {}).get("read") in ("rows_page", "grid")]
print("    dataset_read q:", [(r["actor"], r["details"]["read"], r["details"].get("q")) for r in rows][:4])
print("    raw search text in any audit row:", sum(("Person0007" in json.dumps(r)) or ("user0042" in json.dumps(r))
                                                   for r in audit_rows(cr, "dataset_read")))

# 6. cross-team joins ------------------------------------------------------------------------------------------------------
print("\n# 6. cross-team joins (Collections joins Credit Risk's loans v1)")
REC = [{"app_id": f"A{i:05d}", "recovered": (i * 13) % 900, "paid_back": int(i % 3 == 0)} for i in range(0, N, 2)]
u = c.post(B + "/uploads", params={"filename": "recoveries.csv", "project_id": rp},
           files={"file": ("recoveries.csv", csv(REC), "text/csv")}, headers=h("Cole")).json()["upload_id"]
post("Cole", f"/uploads/{u}/check")
g1 = show("6a Cole commit recoveries", post("Cole", f"/uploads/{u}/commit", {"reason": "recoveries", "by": "Cole"}), ["version_id"])["version_id"]


def spec(cols=None, on="app_id", aggs=None):
    j = {"type": "n:1", "table": {"root_id": v1, "version_id": v1, "table": "loans"}, "on": [on], "prefix": "loan__"}
    if cols is not None:
        j["columns"] = cols
    return {"grain": "recoveries", "joins": [j]}


show("6b Cole POST /datasets/{rec}/joins {loans v1: income, segment} (no share)",
     post("Cole", f"/datasets/{g1}/joins", {"name": "rec_loans", "spec": spec(["income", "segment"]), "by": "Cole"}), cut=400)
sid = show("6c Ben POST /shares {loans → collections, scope all, pii masked}",
           post("Ben", "/shares", {"object_id": v1, "grantee_team": co, "scope": "all", "pii": "masked", "reason": "recoveries model",
                                   "by": "Ben"}), ["id", "status"])["id"]
show("6d Bo decides", post("Bo", f"/shares/{sid}/decide", {"approve": True, "comment": "ok", "by": "Bo"}), ["status", "pii"])
jn = show("6e Cole POST joins {income, segment} (masked share)",
          post("Cole", f"/datasets/{g1}/joins", {"name": "rec_loans", "spec": spec(["income", "segment"]), "by": "Cole"}),
          ["id", "validation"], cut=300)
jid1 = jn.get("id")
print("    shared_inputs:", (jn.get("validation") or {}).get("shared_inputs"))
show("6f Cole POST joins {ssn, income}", post("Cole", f"/datasets/{g1}/joins", {"name": "rec_ssn", "spec": spec(["ssn", "income"]), "by": "Cole"}), cut=400)
show("6g Cole POST joins (every column)", post("Cole", f"/datasets/{g1}/joins", {"name": "rec_all", "spec": spec(None), "by": "Cole"}), cut=250)
show("6h Cole POST joins on email", post("Cole", f"/datasets/{g1}/joins",
                                        {"name": "rec_mail", "spec": {"grain": "recoveries", "joins": [{"type": "aggregate",
                                         "table": {"root_id": v1, "version_id": v1, "table": "loans"}, "on": [["app_id", "email"]],
                                         "aggs": [{"name": "n", "fn": "count"}]}]}, "by": "Cole"}), cut=250)
fr = show("6i Cole POST /datasets/{rec}/versions/{rec}/frames {rec_loans}",
          post("Cole", f"/datasets/{g1}/versions/{g1}/frames", {"join_id": jid1, "by": "Cole"}), ["id", "n_rows", "cached", "inputs"], cut=600)
frid = fr.get("id")
run = show("6j Cole POST /runs {join rec_loans, label paid_back}",
           post("Cole", "/runs", {"dataset_id": g1, "join_id": jid1, "label": "paid_back", "time_limit": 15, "by": "Cole"}), ["id", "status"])
jr = run.get("id")
print("    join run:", wait(f"/runs/{jr}", who="Cole")["status"] if jr else None)
show("6k Ben POST /shares/{s}/revoke", post("Ben", f"/shares/{sid}/revoke", {"reason": "end of pilot", "by": "Ben"}), ["status", "runs_kept"])
show("6l Cole POST frames {rec_loans} after revoke (the cached frame too)",
     post("Cole", f"/datasets/{g1}/versions/{g1}/frames", {"join_id": jid1, "by": "Cole"}), cut=300)
show("6m Cole POST /runs {join rec_loans} after revoke",
     post("Cole", "/runs", {"dataset_id": g1, "join_id": jid1, "label": "paid_back", "time_limit": 15, "by": "Cole"}), cut=250)
show("6n Cole POST joins/{id}/validate after revoke", post("Cole", f"/datasets/{g1}/joins/{jid1}/validate", {"by": "Cole"}), cut=200)
j = show("6o Cole GET /datasets/{rec}/frames/{f}?verify=1 (existing frame)", get("Cole", f"/datasets/{g1}/frames/{frid}", params={"verify": 1}),
         cut=0)
print("    verify:", (j.get("verify") or {}).get("ok"), "| runs:", j.get("runs"))
if jr:
    j = show("6p Ada GET /admin/lake/runs/{join run}/verify", get("Ada", f"/admin/lake/runs/{jr}/verify"), cut=0)
    print("    ok:", j.get("ok"), "| data:", (j.get("data") or {}).get("ok"), "| split:", (j.get("split") or {}).get("ok"))
    print("    Cole GET /runs/{join run}:", get("Cole", f"/runs/{jr}").json().get("status"))
s2 = show("6q Ben POST /shares {loans → collections, pii unmasked}",
          post("Ben", "/shares", {"object_id": v1, "grantee_team": co, "scope": "all", "pii": "unmasked", "reason": "skip tracing",
                                  "by": "Ben"}), ["id"])["id"]
show("6r Bo decides with grant_unmasked", post("Bo", f"/shares/{s2}/decide", {"approve": True, "grant_unmasked": True, "comment": "ok",
                                                                               "by": "Bo"}), ["status", "pii"])
j = show("6s Cole POST joins {ssn, income} (unmasked share)",
         post("Cole", f"/datasets/{g1}/joins", {"name": "rec_ssn", "spec": spec(["ssn", "income"]), "by": "Cole"}), ["id"], cut=100)
print("    shared_inputs:", (j.get("validation") or {}).get("shared_inputs"))
show("6t Cole POST frames {rec_loans} (share live again)", post("Cole", f"/datasets/{g1}/versions/{g1}/frames", {"join_id": jid1, "by": "Cole"}),
     ["id", "cached"], cut=200)
print("    join_input_refused (collections):", [(r["actor"], r["details"].get("code"), [x.get("pii_columns") or x["version_id"] for x in r["details"].get("inputs") or []])
                                                 for r in audit_rows(co, "join_input_refused")])

# 7. intake: a money column tagged sensitive ---------------------------------------------------------------------------------
print("\n# 7. Smart Intake evidence of a tagged money column")
j = show("7a Vic GET /datasets/v1/intake (income untagged)", get("Vic", f"/datasets/{v1}/intake"), cut=0)
ev = lambda j: next((x.get("evidence") for x in j.get("columns") or [] if x.get("name") == "income"), None)  # noqa: E731
print("    income evidence:", ev(j))
c.put(f"{B}/datasets/{v1}/pii", json={"columns": [{"column": "income", "tag": "sensitive"}], "by": "Ana"}, headers=h("Ana"))
j = show("7b Vic GET /datasets/v1/intake (income tagged sensitive)", get("Vic", f"/datasets/{v1}/intake"), ["masked_columns"])
print("    income evidence:", ev(j))
j = show("7c Ana …/intake?unmasked=1&reason=QA&by=Ana", get("Ana", f"/datasets/{v1}/intake", params={"by": "Ana", "unmasked": 1, "reason": "QA"}),
         ["unmasked"])
print("    income evidence:", ev(j))

# 8. the unmasked reads in the audit, and the chains -------------------------------------------------------------------------
print("\n# 8. audit")
print("    dataset_rows_read (credit-risk):", [(r["actor"], r["details"].get("read"), r["details"].get("reason"), r["details"].get("columns"))
                                              for r in audit_rows(cr, "dataset_rows_read")])
print("    authz_would_deny / denied data.read_unmasked:",
      [(r["actor"], (r.get("details") or {}).get("path", "").split("/api")[-1][:40]) for a in ("authz_would_deny", "authz_denied")
       for r in audit_rows(cr, a) if (r.get("details") or {}).get("action") == "data.read_unmasked"])
for t in (cr, co):
    show(f"8 GET /teams/{t}/audit/verify", get("Ada", f"/teams/{t}/audit/verify"), ["ok", "rows", "checked"], cut=200)

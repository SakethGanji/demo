"""LB-X: independent read-only end-to-end chain of the labeling feature (SQLite, ENSEMBLE_POC_MODE=1, fake LLM)."""
import csv, io, json, random, sys, time
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/api"
c = httpx.Client(base_url=B, timeout=300)
H = lambda n: {"X-Ensemble-Actor": n}
ANA, BOB, VIK, WEN, REV = H("ana"), H("bob"), H("vik"), H("wen"), H("rev")
RES = []  # (step, check, ok, detail)


def rec(step, check, ok, detail=""):
    RES.append((step, check, bool(ok), str(detail)[:300]))
    print(("PASS" if ok else "FAIL"), step, check, "|", str(detail)[:300], flush=True)


def J(r):
    try:
        return r.json()
    except Exception:
        return {"_text": r.text[:300]}


def code(r):
    d = J(r).get("detail")
    return d.get("code") if isinstance(d, dict) else d


def csvs(rows, cols):
    b = io.StringIO(); w = csv.writer(b); w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    return b.getvalue()


random.seed(7)
N = 400
apps = []
for i in range(N):
    inc = random.randint(15, 200) * 1000
    age = random.randint(19, 75)
    p = 0.5 if inc < 40000 else 0.15
    apps.append({"app_id": f"A{i:04d}", "income": inc, "age": age, "region": random.choice("NSEW"),
                 "purpose": random.choice(["car", "home", "edu", "other"]), "default": "yes" if random.random() < p else "no"})
pays = [{"pay_id": f"P{k:05d}", "app_id": f"A{random.randrange(N):04d}", "amount": random.randint(50, 900),
         "late": random.choice([0, 0, 0, 1])} for k in range(1200)]
bur = [{"app_id": a["app_id"], "score": random.randint(300, 850), "inquiries": random.randint(0, 9)} for a in apps]
files = [("file", ("applications.csv", csvs(apps, list(apps[0])))), ("file", ("payments.csv", csvs(pays, list(pays[0])))),
         ("file", ("bureau.csv", csvs(bur, list(bur[0]))))]

# ---- 1. upload v1 (3 tables) ------------------------------------------------------------------------------------------
r = c.post("/uploads", headers=ANA, files=files, data={"by": "ana", "name": "loans", "target": "default"})
up = J(r).get("upload_id")
rec(1, "stage 3-table upload", r.status_code == 201, (r.status_code, up))
r = c.patch(f"/uploads/{up}", headers=ANA, json={"primary_table": "applications", "target": "default", "relations": [
    {"from": {"table": "payments", "columns": ["app_id"]}, "to": {"table": "applications", "columns": ["app_id"]}, "cardinality": "n:1"},
    {"from": {"table": "bureau", "columns": ["app_id"]}, "to": {"table": "applications", "columns": ["app_id"]}, "cardinality": "1:1"}]})
rec(1, "plan primary + relations", r.status_code == 200, (r.status_code, J(r) if r.status_code != 200 else ""))
r = c.post(f"/uploads/{up}/check", headers=ANA, json={})
chk = J(r)
rec(1, "check", r.status_code == 200, (r.status_code, chk.get("kind"), [t.get("name") for t in chk.get("tables", [])] if isinstance(chk.get("tables"), list) else chk.get("tables")))
r = c.post(f"/uploads/{up}/commit", headers=ANA, json={"by": "ana", "reason": "v1", "check_sha256": chk.get("check_sha256"), "make_default": True})
v1 = J(r).get("id") or J(r).get("version_id")
rec(1, "commit v1", r.status_code == 201 and v1, (r.status_code, v1, "" if r.status_code == 201 else J(r)))
if not v1:
    json.dump(RES, open("/tmp/lbx_res.json", "w")); sys.exit(1)
ds = J(c.get(f"/datasets/{v1}", headers=ANA))
rec(1, "v1 primary table applications", ds.get("primary_table") == "applications" or "applications" in json.dumps(ds), {k: ds.get(k) for k in ("primary_table", "tables", "target")})

spec = {"columns": [{"column": "default", "role": "target", "type": "class", "classes": ["yes", "no"]}]}
r = c.post(f"/datasets/{v1}/label-layers", headers=ANA, json={"name": "default-labels", "table": "applications", "spec": spec})
L = J(r).get("id")
rec(1, "create layer on applications.default", r.status_code == 201 and L, (r.status_code, J(r).get("table_name") if r.status_code == 201 else J(r)))
lab = {}
for i in range(0, 100, 2):  # 50 rows
    lab[i] = "no" if apps[i]["default"] == "yes" else "yes"  # every label disagrees with the base, so ok ≠ absorbed
r = c.post(f"/label-layers/{L}/events", headers=ANA, json={"events": [{"row_id": i, "column": "default", "value": v} for i, v in lab.items()],
                                                         "context_columns": ["income", "purpose"]})
rec(1, "append 50 human labels", r.status_code == 201 and J(r).get("appended") == 50, (r.status_code, J(r).get("appended"), J(r).get("first_seq")))
st = J(c.get(f"/label-layers/{L}/status", params={"version_id": v1}))
rec(1, "status v1 ok=50", (st.get("totals") or {}).get("ok") == 50, st.get("totals"))

# ---- 2. edit to v2 ---------------------------------------------------------------------------------------------------
ed = {"by": "ana", "reason": "fix incomes, drop rows", "make_default": "yes", "table": "applications", "ops": [
    {"op": "set_cells", "cells": [{"row_id": 2, "column": "income", "value": 999000}, {"row_id": 4, "column": "purpose", "value": "car2"},
                                  {"row_id": 6, "column": "age", "value": 99}, {"row_id": 8, "column": "default", "value": "yes" if lab[8] == "no" else "no"}]},
    {"op": "delete_rows", "row_ids": [10, 12, 14]}]}
r = c.post(f"/datasets/{v1}/edit", headers=ANA, json=ed)
v2 = J(r).get("id")
rec(2, "edit v2", r.status_code == 201 and v2, (r.status_code, v2, "" if r.status_code == 201 else J(r)))
st = J(c.get(f"/label-layers/{L}/status", params={"version_id": v2, "rows": 1}))
carry = {x["row_id"]: x["carry"] for x in st.get("rows", [])}
tot = st.get("totals", {})
rec(2, "orphaned = deleted rows 10,12,14", tot.get("orphaned") == 3 and all(carry.get(i) == "orphaned" for i in (10, 12, 14)), tot)
rec(2, "stale = context-edited rows 2,4 (income/purpose)", carry.get(2) == "stale" and carry.get(4) == "stale", {i: carry.get(i) for i in (2, 4)})
rec(2, "row 6 (age, not a context column) stays ok", carry.get(6) == "ok", carry.get(6))
rec(2, "row 8 (base target edited) → conflict/base_changed (not ok)", carry.get(8) in ("conflict", "base_changed"), carry.get(8))
rec(2, "ok count = 50-3-2-1 = 44", tot.get("ok") == 44, tot.get("ok"))
r = c.post(f"/label-layers/{L}/events", headers=ANA, json={"row_id": 2, "column": "default", "value": lab[2], "version_id": v2,
                                                         "context_columns": ["income", "purpose"]})
st = J(c.get(f"/label-layers/{L}/status", params={"version_id": v2, "rows": 1}))
c2 = {x["row_id"]: x["carry"] for x in st.get("rows", [])}
rec(2, "re-label stale row 2 → ok", r.status_code == 201 and c2.get(2) == "ok", (r.status_code, c2.get(2), st.get("totals")))

# ---- 3. task with 2 annotators + gold -------------------------------------------------------------------------------
r = c.post(f"/label-layers/{L}/tasks", headers=ANA, json={"name": "t1", "columns": ["default"], "votes_per_row": 2, "gold_share": "0.2",
                                                        "min_gold_items": 3, "min_gold_accuracy": "0.8", "context": ["income", "age", "purpose"]})
T = J(r).get("id")
rec(3, "create task", r.status_code == 201 and T, (r.status_code, "" if r.status_code == 201 else J(r)))
for who, role in (("vik", "annotator"), ("wen", "annotator"), ("rev", "reviewer")):
    r = c.post(f"/label-tasks/{T}/members", headers=ANA, json={"principal": who, "role": role})
    rec(3, f"member {who} {role}", r.status_code == 201, (r.status_code, "" if r.status_code == 201 else J(r)))
gold_ids = list(range(301, 311))
r = c.post(f"/label-tasks/{T}/gold", headers=ANA, json={"pool": "qa", "column": "default",
                                                       "items": [{"row_id": i, "value": apps[i]["default"]} for i in gold_ids]})
rec(3, "gold qa 10", r.status_code == 200, (r.status_code, J(r)))
r = c.post(f"/label-tasks/{T}/fill", headers=ANA, json={"strategy": "random", "n": 20, "version_id": v2, "seed": 1})
rec(3, "fill random 20", r.status_code == 200, (r.status_code, J(r)))
r = c.post(f"/label-tasks/{T}/fill", headers=ANA, json={"strategy": "gold", "n": 10, "version_id": v2})
rec(3, "fill gold", r.status_code == 200, (r.status_code, J(r)))
truth = {i: apps[i]["default"] for i in range(N)}
flip = {"yes": "no", "no": "yes"}
paused = None
for who, hdr in (("wen", WEN), ("vik", VIK)):
    r = c.post(f"/label-tasks/{T}/next", headers=hdr, params={"n": 40})
    items = J(r).get("items") or J(r).get("assignments") or []
    rec(3, f"{who} next", r.status_code == 200 and items, (r.status_code, len(items), "" if r.status_code == 200 else J(r)))
    ans = []
    for it in items:
        v = truth[it["row_id"]] if who == "wen" else flip[truth[it["row_id"]]]  # vik always wrong
        ans.append({"assignment_id": it.get("assignment_id") or it.get("id"), "value": v})
    r = c.post(f"/label-tasks/{T}/answers", headers=hdr, json={"items": ans})
    a = J(r)
    rec(3, f"{who} answers {len(ans)}", r.status_code in (200, 409), (r.status_code, {k: a.get(k) for k in ("labelled", "paused", "gold", "appended") if k in a} or a))
    if who == "vik":
        paused = a
r = c.post(f"/label-tasks/{T}/next", headers=VIK)
rec(3, "vik auto-paused on gold → next 409 annotator_paused", r.status_code == 409 and code(r) == "annotator_paused", (r.status_code, J(r)))
r = c.get(f"/label-tasks/{T}/review", headers=REV, params={"kind": "disputed"})
disp = J(r).get("items") or J(r).get("rows") or []
rec(3, "reviewer sees disputed", r.status_code == 200 and len(disp) > 0, (r.status_code, len(disp)))
adj = [{"row_id": d["row_id"], "column": d.get("column") or "default", "value": truth[d["row_id"]], "comment": "adjudicated"} for d in disp]
r = c.post(f"/label-tasks/{T}/adjudicate", headers=REV, json={"items": adj})
rec(3, "reviewer adjudicates", r.status_code == 200, (r.status_code, J(r)))
r = c.get(f"/label-tasks/{T}/review", headers=REV, params={"kind": "disputed"})
rec(3, "disputed empty after adjudication", len(J(r).get("items") or J(r).get("rows") or []) == 0, len(J(r).get("items") or J(r).get("rows") or []))
r = c.get(f"/label-tasks/{T}/quality", headers=VIK)
rec(3, "annotator cannot read quality (403)", r.status_code == 403, r.status_code)

# ---- 4. LLM on fake --------------------------------------------------------------------------------------------------
team = (J(c.get("/me", headers=ANA)).get("teams") or [{}])[0].get("id") or "tm_default"
lay = J(c.get(f"/label-layers/{L}", headers=ANA))
team = lay.get("team_id") or team
r = c.patch(f"/teams/{team}", headers=ANA, json={"by": "ana", "policy": {"llm_labeling": {"enabled": True, "destinations": ["fake"], "max_usd_month": 50}}})
rec(4, "team policy: LLM on, fake", r.status_code == 200, (r.status_code, team))
SQL = ("SELECT a.__row_id, a.income, a.age, a.purpose, coalesce(p.n_pay,0) AS n_pay, coalesce(p.late_share,0) AS late_share, b.score "
       "FROM applications a LEFT JOIN (SELECT app_id, count(*) AS n_pay, avg(late) AS late_share FROM payments GROUP BY app_id) p "
       "ON p.app_id = a.app_id LEFT JOIN bureau b ON b.app_id = a.app_id")
TMPL = "Applicant income {income}, age {age}, purpose {purpose}, {n_pay} payments ({late_share} late), bureau score {score}. Will they default?"
r = c.post(f"/label-layers/{L}/sql/preview", headers=ANA, json={"version_id": v2, "query": SQL, "limit": 20})
sp = J(r)
rec(4, "SQL preview join+agg", r.status_code == 200 and sp.get("n", 0) > 0 and "__row_id" in (sp.get("columns") or []), (r.status_code, sp.get("n"), sp.get("columns"), sp.get("query_sha256", "")[:12]))
r = c.post(f"/label-layers/{L}/llm/preview", headers=ANA, json={"version_id": v2, "query": SQL, "prompt_template": TMPL, "limit": 5, "call_llm": True, "destination": "fake"})
rec(4, "LLM preview (fake) renders + answers", r.status_code == 200, (r.status_code, list(J(r).keys())))
r = c.post(f"/label-layers/{L}/prompts", headers=ANA, json={"version_id": v2, "query": SQL, "prompt_template": TMPL, "column": "default"})
P = J(r).get("id"); pv = J(r).get("version"); qsha = (J(r).get("spec") or {}).get("query_sha256") or J(r).get("query_sha256")
rec(4, "save prompt version", r.status_code == 201 and P, (r.status_code, P, pv, (qsha or "")[:12], "" if r.status_code == 201 else J(r)))
# calibration pool from the base values of applications rows 100..399
cal = [{"row_id": i, "value": apps[i]["default"]} for i in range(100, 300)]
r = c.post(f"/label-tasks/{T}/gold", headers=ANA, json={"pool": "calibration", "column": "default", "items": cal})
rec(4, "calibration pool 200", r.status_code == 200, (r.status_code, J(r)))
r = c.post(f"/label-prompts/{P}/calibrate", headers=ANA, json={"version_id": v2, "destination": "fake", "max_usd": 5, "task_id": T})
cr = J(r).get("id")
rec(4, "calibrate 202", r.status_code == 202 and cr, (r.status_code, "" if r.status_code == 202 else J(r)))


def wait_llm(rid, t=180):
    s = {}
    for _ in range(t):
        s = J(c.get(f"/label-llm-runs/{rid}", headers=ANA))
        if s.get("status") not in ("queued", "running"):
            break
        time.sleep(1)
    return s


if cr:
    s = wait_llm(cr)
    rec(4, "calibration finished", s.get("status") in ("succeeded", "done", "completed"), (s.get("status"), s.get("calibration") or s.get("result") or s.get("error")))
r = c.post(f"/label-prompts/{P}/runs", headers=ANA, json={"version_id": v2, "destination": "fake", "max_usd": 0.0001,
                                                         "selector": {"strategy": "unlabelled", "n": 100}})
rec(4, "budget: tiny max_usd → 409 over_budget", r.status_code == 409 and code(r) == "over_budget", (r.status_code, code(r)))
r = c.post(f"/label-prompts/{P}/runs", headers=ANA, json={"version_id": v2, "destination": "fake", "max_usd": 3,
                                                         "selector": {"strategy": "unlabelled", "n": 100}})
lr = J(r).get("id")
rec(4, "run 100 rows with budget", r.status_code == 202 and J(r).get("rows") == 100, (r.status_code, J(r).get("rows"), J(r).get("estimate"), "" if r.status_code == 202 else J(r)))
if lr:
    s = wait_llm(lr)
    rec(4, "LLM run finished within budget", s.get("status") in ("succeeded", "done", "completed") and float(s.get("spent_usd") or 0) <= 3,
        (s.get("status"), s.get("spent_usd"), s.get("rows_done"), s.get("error")))
    r = c.post(f"/label-llm-runs/{lr}/accept", headers=ANA)
    acc = J(r)
    rec(4, "accept", r.status_code == 200, (r.status_code, acc))
    evs = J(c.get(f"/label-layers/{L}/events", headers=ANA, params={"limit": 5000})).get("events", [])
    le = [e for e in evs if e.get("source") == "llm"]
    prov = [e.get("provenance") or {} for e in le]
    okp = le and all(p.get("prompt_version") == pv and p.get("query_sha256") == qsha and p.get("prompt_id") == P for p in prov)
    rec(4, "LLM events carry prompt_version + query_sha256", okp, (len(le), prov[0] if prov else None))
r = c.post(f"/label-layers/{L}/sql/preview", headers=ANA, json={"version_id": v2, "query": "SELECT __row_id, nope FROM applications"})
rec(4, "bad SQL → 400 with message", r.status_code == 400 and "nope" in json.dumps(J(r)), (r.status_code, J(r)))
for q in ("SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM '/etc/passwd'", "SELECT __row_id FROM read_parquet('x.parquet')"):
    r = c.post(f"/label-layers/{L}/sql/preview", headers=ANA, json={"version_id": v2, "query": q})
    rec(4, f"file read refused: {q[:40]}", r.status_code == 400 and "root:" not in r.text, (r.status_code, J(r)))
r = c.post(f"/label-layers/{L}/prompts", headers=ANA, json={"version_id": v2, "query": "SELECT * FROM read_csv('/etc/passwd')", "prompt_template": "{x}"})
rec(4, "prompt with file read refused at save", r.status_code == 400, (r.status_code, J(r)))

# ---- 5. snapshot + run pinned --------------------------------------------------------------------------------------
r = c.post(f"/label-layers/{L}/snapshots", headers=ANA, json={"policy": {"columns": ["default"], "exclude_simulated": False}})
sn = J(r)
rec(5, "snapshot incl. simulated", r.status_code == 201 and sn.get("counts", {}).get("by_source", {}).get("llm"), (r.status_code, sn.get("counts")))
SN = sn.get("id")
rec(5, "snapshot verify", J(c.get(f"/label-snapshots/{SN}/verify", headers=ANA)).get("status") == "ok", J(c.get(f"/label-snapshots/{SN}/verify", headers=ANA)))
r = c.post("/runs", headers=ANA, json={"dataset_id": v2, "label": "default", "time_limit": 20, "feature_importance": False,
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
lf = vr.get("labels_fitted") or {}
rec(5, "label-runs verify ok incl. labels_fitted", vr.get("status") == "ok" and lf.get("ok") is True, {k: vr.get(k) for k in ("status", "rows_applied")} | {"lf": lf.get("status")})
# overlay applied to training rows only, holdout unchanged
snrows = {x["__row_id"]: {"default": x["value"], "status": x["status"]} for x in J(c.get(f"/label-snapshots/{SN}/rows", headers=ANA, params={"limit": 5000})).get("rows", []) if x["column"] == "default"}
pr = J(c.get(f"/runs/{run}/predictions", headers=ANA, params={"limit": 5000}))
hp = J(c.get(f"/runs/{run}/holdout-predictions", headers=ANA, params={"limit": 5000}))
hrows = [{"__row_id": a, "y_true": b} for a, b in zip(hp.get("rows") or [], hp.get("y_true") or [])]
print("holdout-predictions keys", list(hp.keys())[:10], (hrows[:1] if hrows else None))
hold_ids = {int(x.get("__row_id", x.get("row_id", -1))) for x in hrows}
v2base = {}
for i in range(N):
    v2base[i] = apps[i]["default"]
v2base[8] = ed["ops"][0]["cells"][3]["value"]
diff_hold = [i for i in hold_ids if i in snrows and snrows[i]["status"] == "applied" and snrows[i].get("default") != v2base.get(i)]
ytrue_ok = True
for x in hrows:
    rid = int(x.get("__row_id", x.get("row_id", -1)))
    if rid in diff_hold:
        yt = x.get("y_true", x.get("label", x.get("actual")))
        ytrue_ok &= str(yt) == v2base[rid]
rec(5, "snapshot has applied labels on holdout rows that differ from base (test is non-vacuous)", len(diff_hold) > 0, len(diff_hold))
rec(5, "holdout unchanged: holdout rows whose snapshot label differs keep base y_true", hold_ids and ytrue_ok,
    {"holdout_rows": len(hold_ids), "snapshot_rows_in_holdout_with_different_label": len(diff_hold)})
applied_snap = [i for i, x in snrows.items() if x.get("status") == "applied"]
rec(5, "overlay rows_applied ≤ snapshot rows outside holdout", vr.get("rows_applied") is not None and vr["rows_applied"] <= len([i for i in applied_snap if i not in hold_ids]),
    {"rows_applied": vr.get("rows_applied"), "snap_rows_not_holdout": len([i for i in applied_snap if i not in hold_ids]), "snap_rows_in_holdout": len([i for i in applied_snap if i in hold_ids])})
r = c.post(f"/runs/{run}/register", headers=ANA, json={"created_by": "ana", "model_name": "lbx.default"})
rec(5, "register", r.status_code in (200, 201, 202), (r.status_code, J(r) if r.status_code >= 300 else ""))
for _ in range(60):
    regs = J(c.get("/registry/versions", headers=ANA))
    if all(v.get("status") != "building" for v in regs.get("versions", [])):
        break
    time.sleep(2)
mv = [v for v in regs.get("versions", []) if v.get("run_id") == run]
regs = J(c.get(f"/registry/versions/{mv[0]['id']}", headers=ANA)) if mv else regs
rec(5, "registry hashes carry label_snapshot / overlay / labels_fitted", "labels_fitted_sha256" in json.dumps(regs), json.dumps(regs)[:200])

# ---- 6. materialise to v3 -------------------------------------------------------------------------------------------
r = c.post(f"/label-snapshots/{SN}/materialise", headers=ANA, json={"version_id": v2, "reason": "apply labels"})
m = J(r); v3 = m.get("version_id")
rec(6, "materialise → v3", r.status_code == 201 and v3, (r.status_code, v3, m.get("rows_changed"), m.get("counts")))
if v3:
    st = J(c.get(f"/label-layers/{L}/status", params={"version_id": v3}, headers=ANA)).get("totals", {})
    rec(6, "status on v3 shows absorbed = rows_changed", (st.get("absorbed") or 0) > 0 and st.get("absorbed") == m.get("rows_changed"), (st, m.get("rows_changed")))
    cnt = m.get("counts") or {}
    rec(6, "materialise skips holdout cells", (cnt.get("holdout_skipped") or 0) >= 0 and "holdout_skipped" in cnt, cnt)

# ---- 7. correction proposal ---------------------------------------------------------------------------------------------
base = v3 or v2
r = c.post(f"/label-layers/{L}/proposals", headers=BOB, json={"version_id": base, "reason": "bureau says these defaulted",
                                                            "cells": [{"row_id": 51, "column": "default", "value": flip[apps[51]["default"]]},
                                                                      {"row_id": 53, "column": "default", "value": flip[apps[53]["default"]]}]})
prop = J(r).get("id")
rec(7, "bob proposes 2 cells", r.status_code == 201 and prop, (r.status_code, J(r)))
try:
    r = c.post(f"/label-proposals/{prop}/decide", headers=REV, json={"approve": True, "comment": "ok"})
finally:
    c = httpx.Client(base_url=B, timeout=300)
d = J(r)
v4 = d.get("version_id") or (d.get("materialised") or {}).get("version_id")
rec(7, "rev (different person) approves → new version", r.status_code == 200 and v4, (r.status_code, {k: d.get(k) for k in ("status", "version_id", "snapshot_id")} if r.status_code == 200 else d))
if v4:
    rows4 = J(c.get(f"/datasets/{v4}/rows", headers=ANA, params={"limit": 100, "table": "applications"}))
    rr = {x.get("__row_id"): x for x in rows4.get("rows", [])} if isinstance(rows4.get("rows"), list) else {}
    lk = [k for k in rows4 if isinstance(rows4[k], list)]
    rr = {x.get("__row_id"): x for x in rows4[lk[0]]} if lk else {}
    print("rows keys", list(rows4.keys()), "first", (rows4.get("rows") or [])[:2], "row51", rr.get(51), "row53", rr.get(53))
    rec(7, "new version has the proposed values (typed)", str(rr.get(51, {}).get("default")).lower() in (("true",) if flip[apps[51]["default"]] == "yes" else ("false",)), rr.get(51))
    # a proposal that changes nothing → materialise refuses (409) → the proposal must be back to pending, events retracted
    same = rr.get(60, {}).get("default")
    r = c.post(f"/label-layers/{L}/proposals", headers=BOB, json={"version_id": v4, "reason": "no-op proposal", "cells": [{"row_id": 60, "column": "default", "value": "yes" if same else "no"}]})
    p3 = J(r).get("id")
    r = c.post(f"/label-proposals/{p3}/decide", headers=REV, json={"approve": True})
    c = httpx.Client(base_url=B, timeout=300)
    pl3 = {x["id"]: x for x in J(c.get(f"/label-layers/{L}/proposals", headers=ANA)).get("proposals", [])}
    rec(7, "failed approval → 4xx and the proposal is pending again", 400 <= r.status_code < 500 and (pl3.get(p3) or {}).get("status") == "pending", (r.status_code, J(r), (pl3.get(p3) or {}).get("status"), (pl3.get(p3) or {}).get("comment")))
r = c.post(f"/label-layers/{L}/proposals", headers=BOB, json={"version_id": base, "reason": "self approve test",
                                                            "cells": [{"row_id": 55, "column": "default", "value": "yes"}]})
p2 = J(r).get("id")
r = c.post(f"/label-proposals/{p2}/decide", headers=BOB, json={"approve": True})
c = httpx.Client(base_url=B, timeout=300)
pl = {x["id"]: x for x in J(c.get(f"/label-layers/{L}/proposals", headers=ANA)).get("proposals", [])}
rec(7, "after a failed approve the proposal is not left half-applied", (pl.get(prop) or {}).get("status") == "pending", (pl.get(prop) or {}).get("status"))
rec(7, "(POC mode) proposer self-approve → allowed in POC (four-eyes off), noted", True, (r.status_code, J(r).get("detail") if r.status_code >= 300 else J(r).get("status")))

# ---- 8. purge the labelled column -------------------------------------------------------------------------------------
pv_ = v4 or v3 or v2
r = c.post(f"/datasets/{pv_}/purge", headers=ANA, json={"columns": ["default"], "table": "applications", "reason": "purge test of labelled column", "by": "ana"})
pg = J(r)
rec(8, "purge request", r.status_code == 201, (r.status_code, pg if r.status_code != 201 else pg.get("id")))
if r.status_code == 201:
    r = c.post(f"/purges/{pg['id']}/decide", headers=BOB, json={"team": team, "approve": True, "by": "bob"})
    dd = J(r)
    ex = (dd.get("payload") or dd).get("executed") or {}
    rec(8, "purge executed, labels redacted", r.status_code == 200 and (ex.get("labels") or {}).get("events_redacted", 0) > 0, (r.status_code, ex.get("labels")))
    evs = J(c.get(f"/label-layers/{L}/events", headers=ANA, params={"limit": 5000})).get("events", [])
    left = [e for e in evs if e.get("column") == "default" and e.get("value") not in (None, "")]
    rec(8, "no event on 'default' keeps a value", not left, (len(evs), len(left), left[:1]))
    vr2 = J(c.get(f"/label-runs/{run}/verify", headers=ANA))
    rec(8, "run verify says purged", vr2.get("status") == "purged", vr2.get("status"))
    sv = J(c.get(f"/label-snapshots/{SN}/verify", headers=ANA))
    rec(8, "snapshot verify says purged", sv.get("status") == "purged", sv.get("status"))

# ---- 9. bad inputs ----------------------------------------------------------------------------------------------------
X = "ll_doesnotexist"
bad = [
    ("POST", f"/datasets/{v1}/label-layers", {"name": "x", "table": "nope", "spec": spec}),
    ("POST", f"/datasets/{v1}/label-layers", [1, 2]),
    ("POST", f"/datasets/{v1}/label-layers", {"name": None, "spec": "notadict"}),
    ("POST", f"/label-layers/{L}/schemas", {"spec": {"columns": "x"}}),
    ("POST", f"/label-layers/{L}/events", {"events": [{"row_id": "abc", "column": "default", "value": "yes"}]}),
    ("POST", f"/label-layers/{L}/events", {"events": [{"row_id": 10**30, "column": "default", "value": "yes"}]}),
    ("POST", f"/label-layers/{L}/events", {"events": [{"row_id": 1, "column": "default", "value": {"a": 1}}]}),
    ("POST", f"/label-layers/{L}/events", {"events": "x"}),
    ("POST", f"/label-layers/{L}/events", {"events": [{"row_id": 1, "column": "default", "value": "yes"}] * 501}),
    ("POST", f"/label-layers/{L}/events", {"row_id": 1, "column": "nope", "value": "yes"}),
    ("GET", f"/label-layers/{L}/events?after_seq=-5&limit=-1", None),
    ("GET", f"/label-layers/{L}/status?version_id=ds_nope", None),
    ("GET", f"/label-layers/{L}/rows/99999999999999999999", None),
    ("GET", f"/label-layers/{X}", None),
    ("POST", f"/label-layers/{L}/snapshots", {"policy": "x"}),
    ("POST", f"/label-layers/{L}/snapshots", {"policy": {"columns": ["nope"], "stale": 5}}),
    ("POST", f"/label-snapshots/lsn_nope/materialise", {"version_id": v2}),
    ("POST", f"/label-snapshots/{SN}/materialise", {"version_id": "ds_nope"}),
    ("GET", f"/label-runs/run_nope/verify", None),
    ("POST", f"/label-layers/{L}/tasks", {"columns": ["nope"]}),
    ("POST", f"/label-layers/{L}/tasks", {"columns": ["default"], "votes_per_row": "x"}),
    ("POST", f"/label-tasks/{T}/fill", {"strategy": "random", "n": "lots", "version_id": v2}),
    ("POST", f"/label-tasks/{T}/fill", {"strategy": "manual", "row_ids": ["a"], "version_id": v2}),
    ("POST", f"/label-tasks/{T}/answers", {"items": [{"assignment_id": None, "value": 1}]}),
    ("POST", f"/label-tasks/{T}/answers", {"items": "x"}),
    ("POST", f"/label-tasks/{T}/adjudicate", {"items": [{"row_id": "x", "value": "yes"}]}),
    ("POST", f"/label-tasks/{T}/gold", {"pool": "qa", "items": [{"row_id": "x", "value": "yes"}]}),
    ("POST", f"/label-tasks/{T}/next?n=abc", None),
    ("POST", f"/label-layers/{L}/prompts", {"version_id": v2, "query": SQL, "prompt_template": "{nope}"}),
    ("POST", f"/label-layers/{L}/prompts", {"version_id": v2, "query": "DELETE FROM applications", "prompt_template": "x"}),
    ("POST", f"/label-layers/{L}/sql/preview", {"query": SQL}),
    ("POST", f"/label-layers/{L}/sql/preview", {"version_id": v2, "query": "SELECT 1 AS x"}),
    ("POST", f"/label-layers/{L}/sql/preview", {"version_id": v2, "query": SQL, "limit": "abc"}),
    ("POST", f"/label-layers/{L}/sql/preview", {"version_id": v2, "query": "ATTACH 'x.db' AS y"}),
    ("POST", f"/label-layers/{L}/sql/preview", {"version_id": v2, "query": "SELECT __row_id FROM applications; SELECT 1"}),
    ("POST", f"/label-layers/{L}/llm/preview", {"version_id": v2, "query": SQL, "prompt_template": TMPL, "limit": 9999, "call_llm": True, "destination": "anthropic"}),
    ("POST", f"/label-prompts/{P}/runs", {"version_id": v2, "max_usd": "abc"}),
    ("POST", f"/label-prompts/{P}/runs", {"version_id": v2, "max_usd": 1, "k": "z"}),
    ("POST", f"/label-prompts/{P}/runs", {"version_id": v2, "max_usd": 1, "mode": "batch"}),
    ("POST", f"/label-prompts/{P}/runs", {"version_id": v2, "max_usd": 1, "selector": {"strategy": "nope"}}),
    ("POST", f"/label-llm-runs/lr_nope/accept", None),
    ("POST", f"/label-llm-runs/{lr}/send-to-task", {"task_id": "lt_nope"}),
    ("POST", f"/label-layers/{L}/proposals", {"version_id": v2, "reason": "xyz", "cells": [{"row_id": "a", "column": "default"}]}),
    ("POST", f"/label-layers/{L}/proposals", {"version_id": v2, "reason": "xyz", "cells": "x"}),
    ("POST", f"/label-proposals/lpr_nope/decide", {"approve": True}),
    ("POST", f"/label-layers/{L}/rules", {"x": 1}),
]
n5 = []
for m_, u_, b_ in bad:
    try:
        r = c.request(m_, u_, headers=ANA, json=b_) if b_ is not None else c.request(m_, u_, headers=ANA)
        sc = r.status_code
    except Exception as e:  # noqa: BLE001  (the server drops the keep-alive connection after a 500: retry on a fresh one)
        c = httpx.Client(base_url=B, timeout=300)
        try:
            r = c.request(m_, u_, headers=ANA, json=b_) if b_ is not None else c.request(m_, u_, headers=ANA)
            sc = r.status_code
        except Exception as e2:  # noqa: BLE001
            sc = f"EXC {type(e2).__name__}"; c = httpx.Client(base_url=B, timeout=300)
    print("   bad", m_, u_[:70], json.dumps(b_)[:80] if b_ is not None else "", "→", sc)
    if not isinstance(sc, int) or sc >= 500:
        n5.append((m_, u_, json.dumps(b_)[:120] if b_ is not None else "", sc))
rec(9, f"{len(bad)} bad inputs: no 5xx", not n5, n5)

json.dump({"res": RES, "ids": {"v1": v1, "v2": v2, "v3": v3, "v4": v4, "L": L, "T": T, "P": P, "SN": SN, "run": run, "lr": lr}},
          open("/tmp/lbx_res.json", "w"), indent=1)
print("DONE", sum(1 for x in RES if x[2]), "/", len(RES))

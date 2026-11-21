"""X2: build a populated CSV-era data dir by driving the API of the code that made such data, phase by phase.
  dl3   (9a01901, pre-DL4)  loans v1 + run, edit v2 + run, next file v3 (shuffled → unsorted ids), Doctor (gapped),
        relabel, Feature Lab; churn + run; SQL load + refresh; big (mixed-type column); a registered model + champion
  dl4   (1270b77, DL4)      accounts v1..v3 + runs; DL4 edit of the pre-DL4 v3 (unsorted parent) + run; Doctor + relabel
        on DL4; SQL refresh again; scratch v1/v2 + run, v2 deleted (four eyes); churn v2 archived; model + champion
  head  (acfe988)           a run on accounts v2 (run_inputs), a P06 child of CSV-era accounts v3 + run, a run evicted
usage: gen.py <phase> <state.json> <files dir> [port]"""
import json
import sys
import time
from pathlib import Path

import httpx

phase, state_p, files = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
port = sys.argv[4] if len(sys.argv) > 4 else "8710"
st = json.loads(state_p.read_text()) if state_p.exists() else {}
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=900, headers={"X-Ensemble-Actor": "Ana"})


def ok(r, *codes):
    if r.status_code not in (codes or (200, 201, 202)):
        raise RuntimeError(f"{r.request.method} {r.request.url} → {r.status_code} {r.text[:600]}")
    return r.json()


def save():
    state_p.write_text(json.dumps(st, indent=1))


def step(name, fn):
    try:
        v = fn()
        print(f" ✓ {name}: {v if not isinstance(v, dict) else json.dumps(v)[:160]}")
        return v
    except Exception as e:  # noqa: BLE001
        print(f" ✗ {name}: {str(e)[:500]}")
        return None
    finally:
        save()


def upload(fn, target):
    with open(files / fn, "rb") as f:
        return ok(c.post("/datasets", files={"file": (fn, f, "text/csv")}, data={"target": target, "by": "Ana"}))["id"]


def edit(ds, ops, reason, who="Ana"):
    return ok(c.post(f"/datasets/{ds}/edit", json={"ops": ops, "reason": reason, "by": who}, headers={"X-Ensemble-Actor": who}))["id"]


def run(name, ds, label="y", extra=None):
    body = {"dataset_id": ds, "label": label, "time_limit": 20, "presets": "medium_quality", "name": name, "by": "Ana", **(extra or {})}
    rid = ok(c.post("/runs", json=body))["id"]
    for _ in range(300):
        s = ok(c.get(f"/runs/{rid}"))
        if s["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(3)
    if s["status"] != "succeeded":
        raise RuntimeError(f"run {rid} {s['status']} {(s.get('error') or '')[:300]}")
    return rid


def doctor(ds, transforms):
    return ok(c.post(f"/datasets/{ds}/data-doctor/apply", json={"transforms": transforms, "by": "Ana"}))["dataset"]["id"]


def feature_lab(ds, cands):
    j = ok(c.post(f"/datasets/{ds}/feature-lab", json={"candidates": cands, "time_limit": 30, "max_candidates": 3, "by": "Ana"}))
    jid = j.get("id") or j.get("job_id")
    for _ in range(200):
        s = ok(c.get(f"/datasets/{ds}/feature-lab/{jid}"))
        if s.get("status") in ("done", "succeeded", "failed", "error", "cancelled"):
            break
        time.sleep(3)
    v = (s.get("result") or s).get("version") or s.get("version")
    if not v:
        raise RuntimeError(f"feature lab {s.get('status')}: {json.dumps(s)[:400]}")
    return v.get("id") or (v.get("dataset") or {}).get("id")


def register(rid, name):
    v = ok(c.post(f"/runs/{rid}/register", json={"created_by": "Ana", "model_name": name}))
    return v.get("id") or v.get("version_id") or (v.get("version") or {}).get("id")


def champion(ds, mv):
    c.post(f"/registry/versions/{mv}/review", json={"decision": "approve", "reviewer": "Ben", "comment": "ok"}, headers={"X-Ensemble-Actor": "Ben"})
    pid = "prj_" + st["root_of"][ds].split("_", 1)[1]
    r = ok(c.post(f"/projects/{pid}/champion", json={"version_id": mv, "by": "Ana", "comment": "x2", "override_reason": "x2 test data"}))
    rq = r.get("id") or (r.get("request") or {}).get("id")
    if rq:
        r = ok(c.post(f"/champions/{rq}/decide", json={"approve": True, "by": "Ben", "comment": "ok", "override_reason": "x2 test data"},
                      headers={"X-Ensemble-Actor": "Ben"}))
    return {"project": pid, "status": r.get("status")}


def sql_load():
    r = ok(c.post("/sql/datasets", json={"name": "bank-sql", "target": "defaulted", "created_by": "Ana", "limit": 3000}))
    return (r.get("dataset") or r).get("id")


def sql_refresh(ds):
    r = ok(c.post(f"/sql/datasets/{ds}/refresh", json={"created_by": "Ana"}))
    return (r.get("dataset") or r).get("id")


def relabel(ds, rows, target="y"):
    labs = [i % 2 for i in range(len(rows))]
    return doctor(ds, [{"op": "relabel", "rows": rows, "labels": labs, "reason": "x2 relabel"}])


if phase == "dl3":
    st["L1"] = step("upload loans", lambda: upload("loans.csv", "y"))
    st["rL1"] = step("run loans v1", lambda: run("loans v1", st["L1"]))
    st["L2"] = step("edit loans → v2", lambda: edit(st["L1"], [
        {"op": "delete_rows", "rows": list(range(5, 25))},
        {"op": "set_cells", "cells": [{"row": 40, "column": "income", "value": 61000}, {"row": 41, "column": "name", "value": "Łukasz ✓"}]},
        {"op": "add_rows", "rows": [{"loan_id": "L09001", "zip": "02139", "acct_no": "00000001", "income": 40000, "rate": "3.0", "n_late": 1,
                                     "name": "Ñandú", "note": "", "flag": True, "opened": "2024-02-29", "age": 30, "y": 1},
                                    {"loan_id": "L09002", "zip": "00501", "acct_no": "00000002", "income": 80000, "rate": "4.0",
                                     "name": "", "flag": False, "opened": "2023-12-31", "age": 50, "y": 0}]}],
        "drop test rows, fix an income, two late files"))
    st["rL2"] = step("run loans v2", lambda: run("loans v2", st["L2"]))

    def nxt():
        with open(files / "loans_v3.csv", "rb") as f:
            return ok(c.post(f"/datasets/{st['L1']}/versions", files={"file": ("loans_v3.csv", f, "text/csv")},
                             data={"by": "Ana", "row_key": "loan_id", "reason": "November file"}))["id"]
    st["L3"] = step("next file loans v3 (shuffled)", nxt)
    st["LD"] = step("Data Doctor on loans v2 (flag + drop rows)", lambda: doctor(st["L2"], [
        {"op": "add_missing_flag", "column": "n_late"}, {"op": "drop_rows", "rows": [0, 1, 2, 77], "reason": "x2 bad rows"}]))
    st["C1"] = step("upload churn", lambda: upload("churn.csv", "churn"))
    st["rC1"] = step("run churn v1", lambda: run("churn v1", st["C1"], "churn"))
    st["Q1"] = step("SQL load", sql_load)
    st["Q2"] = step("SQL refresh (pre-DL4)", lambda: sql_refresh(st["Q1"]))
    st["B1"] = step("upload big (mixed-type column)", lambda: upload("big.csv", "y"))
    st["mL1"] = step("register rL1", lambda: register(st["rL1"], "x2-loans"))
if phase in ("dl3", "dl3b"):
    st.setdefault("root_of", {})[st["L1"]] = st["L1"]
    st["LR"] = step("relabel loans v1", lambda: relabel(st["L1"], [3, 4, 9]))
    st["F1"] = step("upload fl", lambda: upload("fl.csv", "y"))
    st["F2"] = step("upload fl2", lambda: upload("fl2.csv", "y"))
    st["LF"] = step("Feature Lab on fl v1", lambda: feature_lab(st["F2"], [{"op": "ratio", "a": "a", "b": "b"}]))
    st["chL1"] = step("champion rL1", lambda: champion(st["L1"], st["mL1"]))
elif phase == "dl4":
    st["A1"] = step("upload accounts", lambda: upload("accounts.csv", "y"))
    st["rA1"] = step("run accounts v1", lambda: run("accounts v1", st["A1"]))
    st["A2"] = step("edit accounts → v2", lambda: edit(st["A1"], [
        {"op": "delete_rows", "rows": [0, 1, 2, 3, 4, 100, 101, 102]},
        {"op": "set_cells", "cells": [{"row": 10, "column": "balance", "value": 1234.5}, {"row": 11, "column": "memo", "value": ""}]},
        {"op": "add_rows", "rows": [{"acct": "00009001", "zip": "02139", "balance": 10.0, "tenure": 3, "active": True, "region": "north",
                                     "since": "2024-02-29", "owner": "Đặng 🙂", "memo": "", "y": 1}]}], "close 8, fix 2, one new"))
    st["rA2"] = step("run accounts v2", lambda: run("accounts v2", st["A2"]))
    st["A3"] = step("edit accounts → v3", lambda: edit(st["A2"], [{"op": "delete_rows", "rows": list(range(200, 230))}], "drop a branch"))
    st["L7"] = step("DL4 edit of pre-DL4 loans v3 (unsorted)", lambda: edit(st["L3"], [
        {"op": "delete_rows", "rows": [0, 5]}, {"op": "set_cells", "cells": [{"row": 3, "column": "note", "value": "fixed"}]}], "DL4 fix"))
    st["rL7"] = step("run loans v7", lambda: run("loans v7", st["L7"]))
    st["AD"] = step("Data Doctor on accounts v2 (DL4)", lambda: doctor(st["A2"], [
        {"op": "add_missing_flag", "column": "balance"}, {"op": "drop_rows", "rows": [5, 6], "reason": "x2"}]))
    st["AR"] = step("relabel accounts v1 (DL4)", lambda: relabel(st["A1"], [1, 2]))
    st["Q3"] = step("SQL refresh (DL4)", lambda: sql_refresh(st["Q1"]))
    st["T1"] = step("upload scratch", lambda: upload("scratch.csv", "y"))
    st["T2"] = step("edit scratch → v2", lambda: edit(st["T1"], [{"op": "delete_rows", "rows": [0, 1, 2]}], "trim"))
    st["rT2"] = step("run scratch v2", lambda: run("scratch v2", st["T2"]))
    step("default scratch v1", lambda: ok(c.post(f"/datasets/{st['T1']}/default", json={"by": "Ana", "version_id": st["T1"], "reason": "back"})))

    def delete():
        d = ok(c.post(f"/datasets/{st['T2']}/delete", json={"by": "Ana", "reason": "superseded", "only_version": True, "freeze_holdout": True,
                                                           "irreproducible": {"reason": "a scratch cut"}}))
        rid = d.get("id") or d.get("request", {}).get("id")
        return ok(c.post(f"/dataset-requests/{rid}/decide", json={"by": "Ben", "approve": True, "comment": "ok"},
                         headers={"X-Ensemble-Actor": "Ben"})).get("status")
    step("delete scratch v2 (four eyes)", delete)
    st["C2"] = step("edit churn → v2", lambda: edit(st["C1"], [{"op": "delete_rows", "rows": [7]}], "one bad row"))
    step("default churn v1", lambda: ok(c.post(f"/datasets/{st['C1']}/default", json={"by": "Ana", "version_id": st["C1"], "reason": "keep"})))
    step("archive churn v2", lambda: ok(c.post(f"/datasets/{st['C2']}/archive", json={"by": "Ana", "reason": "x", "archived": True, "only_version": True})))
    st["mA2"] = step("register rA2", lambda: register(st["rA2"], "x2-accounts"))
    st.setdefault("root_of", {})[st["A2"]] = st["A1"]
    st["chA2"] = step("champion rA2", lambda: champion(st["A2"], st["mA2"]))
elif phase == "head":
    st["rA4"] = step("run accounts v2 (acfe988)", lambda: run("accounts v2 head", st["A2"]))
    st["rQ3"] = step("run SQL refresh v3", lambda: run("bank-sql v3", st["Q3"], "defaulted"))

    def p06():
        with open(files / "accounts_next.csv", "rb") as f:
            up = ok(c.post("/uploads", files={"file": ("accounts_next.csv", f, "text/csv")}, data={"parent_version_id": st["A3"], "by": "Ana"}))["upload_id"]
        ok(c.post(f"/uploads/{up}/check"))
        return ok(c.post(f"/uploads/{up}/commit", json={"reason": "December file", "match": {"method": "key", "columns": ["acct"]},
                                                         "acknowledge": ["holdout_identity_loss", "better_key_available"]}))["version_id"]
    st["A4"] = step("P06 child of CSV-era accounts v3", p06)
    st["rA5"] = step("run accounts v4 (Parquet child)", lambda: run("accounts v4", st["A4"]))
    st["rLD"] = step("run loans Doctor version", lambda: run("loans doctor", st["LD"]))
save()
print(json.dumps(st))

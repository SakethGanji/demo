"""P10: build a realistic CSV-era data dir by driving the API of the code that made such data, phase by phase:
  dl3   (9a01901, before DL4: no schema.json)  loans (upload, run, edit → v2, run, next file → v3), churn (upload, run)
  dl4   (1270b77, DL4: schema.json, split_files) accounts (upload, run, edit → v2, run, edit → v3), loans v4 (an edit of
        a pre-DL4 version), scratch (upload, edit → v2, a run on v2, v2 deleted by four eyes: TOMBSTONE + holdout sample),
        churn v2 archived
  head  (acfe988, the pre-P10 head: P01–P09 on CSV-era data) a run on accounts v2 (run_inputs), a P06 next-version
        upload of accounts v3 (a Parquet child of a CSV-era parent), then two outside changes (tamper)
usage: gen.py <phase> <state.json> <files dir> [data dir]"""
import json
import sys
import time
from pathlib import Path

import httpx

phase, state_p, files = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
st = json.loads(state_p.read_text()) if state_p.exists() else {}
c = httpx.Client(base_url="http://localhost:8000/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"})


def ok(r, *codes):
    if r.status_code not in (codes or (200, 201)):
        raise SystemExit(f"{r.request.method} {r.request.url} → {r.status_code} {r.text[:800]}")
    return r.json()


def save():
    state_p.write_text(json.dumps(st, indent=1))


def upload(name, fn, target):
    with open(files / fn, "rb") as f:
        d = ok(c.post("/datasets", files={"file": (fn, f, "text/csv")}, data={"target": target, "by": "Ana"}))
    print(" upload", name, d["id"], d.get("rows"))
    return d["id"]


def edit(ds, ops, reason, who="Ana"):
    d = ok(c.post(f"/datasets/{ds}/edit", json={"ops": ops, "reason": reason, "by": who}, headers={"X-Ensemble-Actor": who}))
    print(" edit", ds, "→", d["id"], d.get("rows"))
    return d["id"]


def run(name, ds, label="y", extra=None):
    body = {"dataset_id": ds, "label": label, "time_limit": 20, "presets": "medium_quality", "name": name, "by": "Ana", **(extra or {})}
    d = ok(c.post("/runs", json=body))
    rid = d["id"]
    for _ in range(200):
        s = ok(c.get(f"/runs/{rid}"))
        if s["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(3)
    print(" run", name, rid, s["status"], (s.get("error") or "")[:200])
    return rid


def splits_of(ds, target="y"):
    return ok(c.get(f"/datasets/{ds}/splits"))


if phase == "dl3":
    st["L1"] = upload("loans", "loans.csv", "y")
    st["rL1"] = run("loans v1", st["L1"])
    st["L2"] = edit(st["L1"], [{"op": "delete_rows", "rows": list(range(5, 25))},
                               {"op": "set_cells", "cells": [{"row": 40, "column": "income", "value": 61000}]},
                               {"op": "add_rows", "rows": [{"loan_id": "L09001", "zip": "02139", "income": 40000, "age": 30, "n_late": 1,
                                                            "segment": "sme", "approved": True, "y": 1},
                                                           {"loan_id": "L09002", "zip": "00501", "income": 80000, "age": 50,
                                                            "segment": "retail", "approved": False, "y": 0}]}],
                     "drop test rows, fix an income, two late files")
    st["rL2"] = run("loans v2", st["L2"])
    with open(files / "loans_v3.csv", "rb") as f:
        d = ok(c.post(f"/datasets/{st['L1']}/versions", files={"file": ("loans_v3.csv", f, "text/csv")},
                      data={"by": "Ana", "row_key": "loan_id", "reason": "November file"}))
    st["L3"] = d["id"]
    print(" next file → L3", st["L3"], d.get("rows"))
    st["C1"] = upload("churn", "churn.csv", "churn")
    st["rC1"] = run("churn v1", st["C1"], "churn")
elif phase == "dl4":
    st["A1"] = upload("accounts", "accounts.csv", "y")
    st["rA1"] = run("accounts v1", st["A1"])
    sp = [s for s in splits_of(st["A1"])["splits"]] if isinstance(splits_of(st["A1"]), dict) else splits_of(st["A1"])
    st["A2"] = edit(st["A1"], [{"op": "delete_rows", "rows": [0, 1, 2, 3, 4, 100, 101, 102]},
                               {"op": "set_cells", "cells": [{"row": 10, "column": "balance", "value": 1234.5}]},
                               {"op": "add_rows", "rows": [{"acct": "00009001", "zip": "02139", "balance": 10.0, "tenure": 3,
                                                            "active": True, "region": "north", "y": 1}]}],
                     "close 8 accounts, a corrected balance, one new account")
    st["rA2"] = run("accounts v2", st["A2"])
    st["A3"] = edit(st["A2"], [{"op": "delete_rows", "rows": list(range(200, 230))}], "drop a migrated branch")
    st["L4"] = edit(st["L2"], [{"op": "set_cells", "cells": [{"row": 3, "column": "segment", "value": "private"}]}],
                    "a corrected segment (DL4 edit of a pre-DL4 version)")
    st["rL4"] = run("loans v4", st["L4"])
    st["T1"] = upload("scratch", "scratch.csv", "y")
    st["T2"] = edit(st["T1"], [{"op": "delete_rows", "rows": [0, 1, 2]}], "trim")
    st["rT2"] = run("scratch v2", st["T2"])
    save()
if phase in ("dl4", "dl4b"):
    ok(c.post(f"/datasets/{st['T1']}/default", json={"by": "Ana", "version_id": st["T1"], "reason": "back to the full cut"}))
if phase == "dl4":
    d = ok(c.post(f"/datasets/{st['T2']}/delete", json={"by": "Ana", "reason": "superseded test cut", "only_version": True,
                                                        "freeze_holdout": True,
                                                        "irreproducible": {"reason": "a scratch cut; its one run is a throwaway"}}))
    rid = d.get("id") or d.get("request", {}).get("id")
    d2 = ok(c.post(f"/dataset-requests/{rid}/decide", json={"by": "Ben", "approve": True, "comment": "ok"},
                   headers={"X-Ensemble-Actor": "Ben"}))
    print(" deleted T2", d2.get("status"), d2.get("deleted"))
    d3 = ok(c.post(f"/datasets/{st['C1']}/edit", json={"ops": [{"op": "delete_rows", "rows": [7]}], "reason": "one bad row", "by": "Ana"}))
    st["C2"] = d3["id"]
    ok(c.post(f"/datasets/{st['C1']}/default", json={"by": "Ana", "version_id": st["C1"], "reason": "keep v1"}))
    ok(c.post(f"/datasets/{st['C2']}/archive", json={"by": "Ana", "reason": "not needed", "archived": True, "only_version": True}))
    print(" archived C2", st["C2"])
if phase == "head":
    if "rA4" not in st:
        st["rA4"] = run("accounts v2 (pre-P10 head)", st["A2"])
    st["rS1"] = run("credit-default sample", "credit-default", "defaulted")
    with open(files / "accounts_next.csv", "rb") as f:
        d = ok(c.post("/uploads", files={"file": ("accounts_next.csv", f, "text/csv")},
                      data={"parent_version_id": st["A3"], "by": "Ana"}))
    up = d["upload_id"]
    ok(c.post(f"/uploads/{up}/check"))
    d = ok(c.post(f"/uploads/{up}/commit", json={"reason": "December file", "match": {"method": "key", "columns": ["acct"]},
                                                  "acknowledge": ["holdout_identity_loss", "better_key_available"]}))
    st["A4"] = d["version_id"]
    print(" P06 upload, Parquet child of CSV-era A3 →", st["A4"])
    st["rA5"] = run("accounts v4 (Parquet child)", st["A4"])
save()
print(json.dumps(st))

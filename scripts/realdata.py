#!/usr/bin/env python3
"""D5 · the real-data proof (`make realdata`). A manual driver, not a test suite: it chains the API calls the UI makes,
prints what it observed, and one PASS / FAIL line per expectation.

    backend/.venv/bin/python -I scripts/realdata.py [--api http://127.0.0.1:8000] [--only taiwan,bank_leak,…]
        [--redo] [--recheck] [--no-review]

The chain per dataset (or planted copy):

  POST /api/datasets                          upload (the target named)
  GET  /api/datasets/{id}/data-doctor         Data Doctor, polled until the label check has finished
  POST /api/runs → GET /api/runs/{id}         train (AutoGluon medium_quality, 3-fold bagging, a short budget)
  GET  /api/runs/{id}/ladder                  the ladder and its recommendation
  GET  /api/runs/{id}/doctor                  the Model Doctor: headline, What's missing, How to improve
  (+ the Doctor's own prescription where the expectation needs it: the leak ablation, the shift check)
  POST /api/runs/{id}/review → GET …/review/{rid}   the review loop, one at a time (offline or Claude)
  (+ post-deployment: register, score labelled batches, GET/POST …/changes)

Datasets: the four of round 4 (scripts/realdata_d1.py), two new OpenML sets with published scores, and planted-issue
copies of real data with known answers (scripts/realdata_data.py). Budgets are short where the answer does not depend
on the budget. Results: backend/data/realdata/results.json (not committed). --recheck re-reads the Data Doctor and the
Model Doctor of finished runs (after a product fix) without retraining; --redo trains again.
"""

from __future__ import annotations

import argparse
import builtins
import functools
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pandas as pd

print = functools.partial(builtins.print, flush=True)  # noqa: A001
sys.path.insert(0, str(Path(__file__).resolve().parent))
import realdata_d1 as rd  # noqa: E402
import realdata_data as rdd  # noqa: E402

OUT = rd.ROOT / "results.json"
TL = 150  # seconds per run unless the item says otherwise
FIT = {"num_bag_folds": 3, "num_stack_levels": 0}

# Published scores (holdout AUC, accuracy for Banking77). Sources in docs/progress/D5.md.
PUBLISHED = {
    "taiwan": ("roc_auc", 0.76, 0.80, "Yeh & Lien 2009; GBM benchmarks ≈ 0.78"),
    "bank": ("roc_auc", 0.92, 0.96, "Moro et al. 2014 (with duration) ≈ 0.94"),
    "bank_noduration": ("roc_auc", 0.77, 0.82, "without duration ≈ 0.79–0.80"),
    "german_cost": ("roc_auc", 0.70, 0.82, "OpenML 31 task runs ≈ 0.75–0.80"),
    "banking77": ("accuracy", 0.85, 0.94, "Casanueva et al. 2020: 0.93 (BERT); TF-IDF baselines ≈ 0.87–0.89"),
    "gmsc": ("roc_auc", 0.84, 0.88, "Kaggle Give Me Some Credit: winning AUC 0.869; GBMs ≈ 0.86"),
    "credit_approval": ("roc_auc", 0.88, 0.96, "OpenML 29 task runs: AUC ≈ 0.92–0.94, accuracy ≈ 0.86–0.88"),
}


# ------------------------------------------------------------------------------------------------------- the items

def items() -> dict[str, dict[str, Any]]:
    files = rd.prepare()
    new = rdd.new_datasets()
    pl = rdd.planted()
    src = {k: (files[k], rd.OUT[k][1]) for k in files} | new | pl
    off1 = {"offline": True, "max_experiments": 1, "max_minutes": 15}
    return {
        # the four round-4 datasets
        "taiwan": {"src": src["taiwan"], "drop": ["ID"], "review": off1},
        "bank": {"src": src["bank"], "ablate": True, "review": off1},
        "german_cost": {"src": src["german"], "tl": 120, "cost": {"c_fp": 1, "c_fn": 5}, "review": off1},
        "banking77": {"src": src["banking77"], "tl": 300, "multiclass": True, "review": None},
        # two new public datasets (Claude reviews them)
        "gmsc": {"src": src["gmsc"], "drop": ["borrower_id"], "review": {"offline": False, "max_experiments": 2, "max_minutes": 20}},
        "credit_approval": {"src": src["credit_approval"], "tl": 90, "review": {"offline": False, "max_experiments": 2, "max_minutes": 15}},
        # planted-issue copies of real data
        "bank_leak": {"src": pl["bank_leak"], "ablate": True, "review": off1},
        "taiwan_flip": {"src": pl["taiwan_flip"], "drop": ["ID"], "review": {"offline": True, "max_experiments": 3, "max_minutes": 30}},
        "taiwan_ratio": {"src": pl["taiwan_ratio"], "drop": ["ID"], "review": {"offline": True, "max_experiments": 3, "max_minutes": 30}},
        "taiwan_drift": {"src": pl["taiwan_drift"], "drop": ["ID"], "shift": True, "postdeploy": True, "review": None},
        "bank_999": {"src": pl["bank_999"], "train": False, "apply_fix": "disguised_missing"},
        "bank_dupes": {"src": pl["bank_dupes"], "train": False},
        "gmsc_mnar": {"src": pl["gmsc_mnar"], "drop": ["borrower_id"], "review": None},
        "taiwan_nopay0": {"src": src["taiwan"], "drop": ["ID", "PAY_0"], "review": None, "dataset_of": "taiwan"},
    }


# ---------------------------------------------------------------------------------------------- the API chain

def wait_run(c: httpx.Client, rid: str, timeout: float = 5400) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(5)
    raise TimeoutError(rid)


def upload(c: httpx.Client, path: Path, target: str) -> str:
    r = c.post("/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")}, data={"target": target})
    r.raise_for_status()
    return r.json()["id"]


def data_doctor(c: httpx.Client, ds: str, wait_labels: float = 300) -> dict:
    t0 = time.time()
    while True:
        dd = c.get(f"/api/datasets/{ds}/data-doctor").json()
        lab = next((f for f in dd["findings"] if f["id"] == "label_quality"), None)
        if not lab or lab.get("status") != "running" or time.time() - t0 > wait_labels:
            break
        time.sleep(5)
    return {"findings": [{"id": f["id"], "severity": f["severity"], "title": f["title"], "columns": f.get("columns"),
                          "numbers": f.get("numbers"), "fix": (f.get("fix") or {}).get("kind"),
                          "selected": (f.get("fix") or {}).get("selected"),
                          "transforms": (f.get("fix") or {}).get("transforms") if f["id"] not in ("conflicts", "label_quality") else None,
                          "suspects": [s["column"] for s in f.get("suspects") or []] or None} for f in dd["findings"]],
            "counts": dd.get("counts"), "rows": dd.get("rows")}


def train(c: httpx.Client, name: str, ds: str, target: str, it: dict) -> dict:
    multi = it.get("multiclass")
    body = {"dataset_id": ds, "label": target, "presets": "medium_quality", "time_limit": it.get("tl", TL),
            "problem_type": "multiclass" if multi else "binary", "eval_metric": None if multi or it.get("cost") else "roc_auc",
            "fit_kwargs": dict(FIT), "source": "realdata-d5", "name": f"{name} · D5"}
    if it.get("drop"):
        body["drop_columns"] = it["drop"]
    if it.get("cost"):
        body["cost"] = it["cost"]
    t0 = time.time()
    rid = c.post("/api/runs", json=body).json()["id"]
    print(f"  training {rid} ({body['time_limit']} s budget)")
    rec = wait_run(c, rid)
    sc = ((rec.get("holdout") or {}).get("scores")) or {}
    return {"run": rid, "status": rec["status"], "error": rec.get("error"), "seconds": round(time.time() - t0),
            "best_model": rec.get("best_model"), "holdout": {k: sc.get(k) for k in ("roc_auc", "accuracy", "balanced_accuracy")},
            "holdout_rows": (rec.get("data") or {}).get("holdout_rows")}


def ladder(c: httpx.Client, rid: str) -> dict:
    lad = c.get(f"/api/runs/{rid}/ladder").json()
    rungs = [{k: r.get(k) for k in ("rung", "name", "model", "score", "within")} | {"gap": (r.get("gap") or {}).get("delta"),
              "ci": (r.get("gap") or {}).get("ci")} for r in lad.get("rungs") or []]
    return {"metric": lad.get("metric_label"), "recommended": (lad.get("recommended") or {}).get("rung"),
            "recommended_name": (lad.get("recommended") or {}).get("name"), "sentence": lad.get("sentence"),
            "small_data": lad.get("small_data"), "rungs": rungs}


def doctor(c: httpx.Client, rid: str) -> dict:
    d = c.get(f"/api/runs/{rid}/doctor").json()
    m = d.get("missing_info") or {}
    p = d.get("primary") or {}
    chk = {x["id"]: x for x in d.get("checks") or []}
    return {"headline": d["headline"], "primary": p.get("id"), "severity": p.get("severity"),
            "secondary": [f"{x['id']}:{x['severity']}" for x in d.get("secondary") or []],
            "leak_suspects": [s.get("feature") for s in ((chk.get("leakage") or {}).get("evidence") or {}).get("suspects") or []],
            "ablation": ((chk.get("leakage") or {}).get("evidence") or {}).get("ablation"),
            "shift": {k: (chk.get("shift") or {}).get(k) for k in ("status", "severity", "reason")},
            "rx": (p.get("prescription") or {}),
            "irreducible": m.get("irreducible"), "caveat": m.get("caveat"),
            "gaps": [{"rank": g.get("rank"), "id": g.get("id"), "kind": g.get("kind"), "title": g.get("title"), "gain": g.get("gain"),
                      "finding": g.get("finding"), "request": g.get("request"), "segment": g.get("segment")} for g in m.get("gaps") or []],
            "levers": [{"rank": x.get("rank"), "id": x.get("id"), "title": x.get("title"), "delta": x.get("delta"), "ci": x.get("ci")}
                       for x in d.get("improve") or []],
            "errors": m.get("errors")}


def review(c: httpx.Client, rid: str, opts: dict) -> dict:
    llm_off = opts.get("offline")
    if llm_off:
        c.put("/api/llm/settings", json={"enabled": False})
    try:
        t0 = time.time()
        r = c.post(f"/api/runs/{rid}/review", json={"by": "realdata-d5", **{k: v for k, v in opts.items()}})
        if r.status_code >= 300:
            return {"error": f"{r.status_code} {r.text[:300]}"}
        r = r.json()
        last = None
        while (g := c.get(f"/api/runs/{rid}/review/{r['id']}").json())["status"] in ("queued", "running"):
            if g.get("progress") != last:
                last = g.get("progress")
                print(f"    review: {last}")
            time.sleep(10)
    finally:
        if llm_off:
            c.put("/api/llm/settings", json={"enabled": None})
    journal = [{"step": e.get("step"), "title": e["hypothesis"]["title"], "kind": e["experiment"]["kind"],
                "spec": e["experiment"], "run_id": e.get("run_id"), "status": e.get("status"), "verdict": e.get("verdict"),
                "reason": e.get("reason"), "result": {k: (e.get("result") or {}).get(k) for k in ("metric", "parent", "child", "delta", "ci")},
                "note": e.get("note") or e.get("error")} for e in g.get("journal") or []]
    plan = g.get("plan") or {}
    return {"id": g["id"], "status": g["status"], "mode": g.get("mode"), "decider": g.get("decider"), "stop": g.get("stop_reason"),
            "error": g.get("error"), "wall_seconds": round(time.time() - t0), "journal": journal,
            "kept": [j for j in journal if j["verdict"] in ("kept", "kept-honest")],
            "plan": {"summary": plan.get("summary"), "source": plan.get("source"),
                     "next": [{"kind": a.get("kind"), "title": a.get("title"), "why": a.get("why")} for a in plan.get("next") or []],
                     "domain_gaps": plan.get("domain_gaps")},
            "guard": g.get("guard"), "invalid": [h for h in g.get("hypotheses") or [] if not h.get("valid")]}


def ablate(c: httpx.Client, rid: str, d: dict) -> dict | None:
    """The Doctor's own prescription for a leak: POST the ablation, wait for the child, read the Δ on the card."""
    rx = d.get("rx") or {}
    if d.get("primary") != "leakage" or not rx.get("endpoint"):
        return None
    ex = c.post(rx["endpoint"], json=rx.get("body") or {}).json()
    child = wait_run(c, ex["id"])
    after = doctor(c, rid)
    sc = ((child.get("holdout") or {}).get("scores")) or {}
    return {"run": ex["id"], "status": child["status"], "spec": rx.get("body"), "ablation": after.get("ablation"),
            "child_auc": sc.get("roc_auc"), "child_doctor": doctor(c, ex["id"])}


def shift(c: httpx.Client, rid: str, cutoff: str) -> dict:
    r = c.post(f"/api/runs/{rid}/shift", json={"source": "time", "cutoff": cutoff})
    if r.status_code >= 300:
        return {"error": r.text[:300]}
    key = r.json()["key"]
    t0 = time.time()
    while (g := c.get(f"/api/runs/{rid}/shift/{key}").json()).get("status") not in ("succeeded", "failed", "cancelled"):
        if time.time() - t0 > 1200:
            break
        time.sleep(5)
    return {"key": key, "status": g.get("status"), "auc": g.get("auc"), "ci": g.get("ci"), "verdict": g.get("verdict"),
            "top": [f.get("feature") for f in g.get("top_features") or [] if f.get("significant")][:5]}


def apply_fix(c: httpx.Client, ds: str, fid: str) -> dict:
    """The Data Report's 'Apply' for one finding: its transforms → a new dataset version; then read the version back."""
    dd = c.get(f"/api/datasets/{ds}/data-doctor").json()
    f = next((x for x in dd["findings"] if x["id"] == fid), None)
    if not f:
        return {"error": f"no {fid} finding"}
    r = c.post(f"/api/datasets/{ds}/data-doctor/apply", json={"transforms": f["fix"]["transforms"], "by": "realdata-d5"})
    if r.status_code >= 300:
        return {"error": r.text[:300]}
    out = r.json()
    vid = out["dataset"]["id"]
    after = data_doctor(c, vid, wait_labels=0)
    return {"version": vid, "columns_added": out["columns_added"], "effects": out["effects"], "rows_after": out["rows_after"],
            "after_findings": [f["id"] for f in after["findings"]]}


# ------------------------------------------------------------------------------- post-deployment (the drift copy)

def postdeploy(c: httpx.Client, path: Path, target: str, cut: str) -> dict:
    """Train on the months before the drift, register, score the drifted months as labelled batches (three window
    batches, then the newest), and ask the Changes card for a retrain on the recent window."""
    df = pd.read_csv(path)
    old = df[df["application_month"] < cut].drop(columns=["application_month"])
    new = df[df["application_month"] >= cut].sample(frac=1.0, random_state=0)
    p_old = rdd.PLANT / "taiwan_drift_before.csv"
    old.to_csv(p_old, index=False)
    ds = upload(c, p_old, target)
    body = {"dataset_id": ds, "label": target, "problem_type": "binary", "presets": "medium_quality", "time_limit": 90,
            "eval_metric": "roc_auc", "drop_columns": ["ID"], "fit_kwargs": {"num_bag_folds": 3}, "source": "realdata-d5",
            "name": "taiwan_drift before the drift · D5"}
    rid = c.post("/api/runs", json=body).json()["id"]
    print(f"  post-deployment: training {rid} on the {len(old):,} rows before {cut}")
    rec = wait_run(c, rid)
    if rec["status"] != "succeeded":
        return {"run": rid, "error": rec.get("error")}
    v = c.post(f"/api/runs/{rid}/register", json={"created_by": "d5-owner", "model_name": "d5-taiwan-drift"}).json()
    while (v := c.get(f"/api/registry/versions/{v['id']}").json())["status"] not in ("ready", "failed"):
        time.sleep(3)
    out: dict[str, Any] = {"run": rid, "version": v["id"], "batches": []}
    chunks = [(new.iloc[0:1000], "2025-08-31"), (new.iloc[1000:2000], "2025-09-30"), (new.iloc[2000:3000], "2025-10-31"),
              (new.iloc[3000:4500], "2025-12-31")]
    for i, (part, date) in enumerate(chunks):
        feats = part.drop(columns=[target, "application_month"])
        for _ in range(3):
            b = c.post(f"/api/registry/versions/{v['id']}/batches", files={"file": (f"drift_{i}.csv", feats.to_csv(index=False).encode(), "text/csv")},
                       data={"created_by": "d5-ops", "data_date": date}).json()
            while (b := c.get(f"/api/registry/batches/{b['id']}").json())["status"] not in ("succeeded", "failed", "cancelled"):
                time.sleep(3)
            if b["status"] == "succeeded":
                break
        lab = part[["ID", target]].assign(label_date=date)
        r = c.post(f"/api/registry/batches/{b['id']}/labels", files={"file": (f"labels_{i}.csv", lab.to_csv(index=False).encode(), "text/csv")},
                   data={"created_by": "d5-ops", "id_column": "ID", "label_column": target})
        out["batches"].append({"id": b["id"], "rows": len(part), "date": date, "labels": r.status_code})
        print(f"    batch {b['id']} {len(part)} rows {date} → labels {r.status_code}")
    st = c.get(f"/api/registry/versions/{v['id']}/changes").json()
    out["state"] = {"eligible": st.get("eligible"), "newest": st.get("newest_batch"), "cost": (st.get("cost") or {}).get("source"),
                    "drifted": (st.get("drifted") or [])[:4]}
    for kind in ("threshold", "retrain_window"):
        it = c.post(f"/api/registry/versions/{v['id']}/changes", json={"kind": kind, "actor": "d5-analyst"}).json()
        if it.get("run_id"):
            wait_run(c, it["run_id"])
            it = next(x for x in c.get(f"/api/registry/versions/{v['id']}/changes").json()["experiments"] if x["id"] == it["id"])
        r = it.get("result") or {}
        out[kind] = {"id": it.get("id"), "verdict": it.get("verdict"), "metric": r.get("metric"), "current": r.get("current"),
                     "proposed": r.get("proposed"), "delta": r.get("delta"), "ci": r.get("ci"), "auc": r.get("auc"),
                     "error": it.get("error") or it.get("detail")}
        print(f"    {kind}: {r.get('metric')} {r.get('current')} → {r.get('proposed')} Δ {r.get('delta')} {r.get('ci')} · {it.get('verdict')}")
    return out


# --------------------------------------------------------------------------------------------- expectations

def _f(o: dict, fid: str) -> dict | None:
    return next((f for f in (o.get("dd") or {}).get("findings") or [] if f["id"] == fid), None)


def _sev_ok(f: dict | None, levels=("low", "medium", "high")) -> bool:
    return bool(f and f["severity"] in levels)


def _score(o: dict, key: str) -> float | None:
    return ((o.get("run") or {}).get("holdout") or {}).get(key)


def _published(name: str, o: dict, key: str | None = None) -> tuple[str, Callable[[dict], Any]]:
    metric, lo, hi, src = PUBLISHED[key or name]
    return (f"holdout {metric} in the published range [{lo}, {hi}] ({src})",
            lambda o: (lambda s: (s is not None and lo <= s <= hi, f"{s:.4f}" if s is not None else "no score"))(_score(o, metric)))


def _gap(o: dict, kind: str, top: int = 3) -> dict | None:
    return next((g for g in ((o.get("doctor") or {}).get("gaps") or [])[:top] if g.get("kind") == kind or g.get("id") == kind), None)


def _kept(o: dict, kind: str) -> bool:
    return any(j["kind"] == kind for j in ((o.get("review") or {}).get("kept") or []))


def _journal(o: dict, kind: str) -> str:
    js = [j for j in (o.get("review") or {}).get("journal") or [] if j["kind"] == kind]
    if not js:
        return f"no {kind} in the journal"
    j = js[0]
    r = j.get("result") or {}
    d = f"Δ {r['delta']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]" if r.get("delta") is not None else (j.get("note") or j.get("status"))
    return f"{kind}: {j['verdict'] or j['status']} ({d})"


EXPECT: dict[str, list[tuple[str, Callable[[dict], Any]]]] = {
    "taiwan": [
        ("Data Doctor: coded values in PAY_*", lambda o: (_sev_ok(_f(o, "coded_values")) and any("PAY" in c for c in _f(o, "coded_values")["columns"]), (_f(o, "coded_values") or {}).get("title"))),
        ("Data Doctor: SEX / AGE protected", lambda o: (bool(_f(o, "protected")) and {"SEX", "AGE"} <= set(_f(o, "protected")["columns"] or []), (_f(o, "protected") or {}).get("title"))),
        ("Ladder: a simpler rung within the CI of the best", lambda o: (o["ladder"]["recommended"] in (1, 2), o["ladder"]["sentence"])),
        ("Model Doctor: at the data's limit", lambda o: (o["doctor"]["primary"] == "ceiling", o["doctor"]["headline"][:160])),
        ("What's missing: twins in the top 3", lambda o: (bool(_gap(o, "twins")), [g["title"] for g in o["doctor"]["gaps"][:3]])),
        ("Review: nothing kept (at the data's limit)", lambda o: (not (o.get("review") or {}).get("kept"), [j["kind"] + ":" + str(j["verdict"]) for j in (o.get("review") or {}).get("journal") or []])),
    ],
    "bank": [
        ("Data Doctor: leakage suspect duration", lambda o: ("duration" in ((_f(o, "leakage") or {}).get("suspects") or []), (_f(o, "leakage") or {}).get("title"))),
        ("Model Doctor: leakage (critical)", lambda o: (o["doctor"]["primary"] == "leakage" and o["doctor"]["severity"] == "critical", o["doctor"]["headline"][:160])),
        ("Ablation Δ < 0 beyond noise", lambda o: (lambda a: (bool(a) and a.get("ci") and a["ci"][1] < 0, a))(((o.get("ablation") or {}).get("ablation")))),
        ("Without duration: AUC ≈ 0.80 (published 0.77–0.82)", lambda o: (lambda s: (s is not None and 0.77 <= s <= 0.82, s))((o.get("ablation") or {}).get("child_auc"))),
        ("Review: the ablation is kept-honest", lambda o: (_kept(o, "ablate"), _journal(o, "ablate"))),
    ],
    "german_cost": [
        ("Data Doctor: small sample", lambda o: (_sev_ok(_f(o, "sample_size"), ("medium", "high")), (_f(o, "sample_size") or {}).get("title"))),
        ("Model Doctor: cost trade-off", lambda o: (o["doctor"]["primary"] == "cost_tradeoff", o["doctor"]["headline"][:160])),
        ("Ladder: a simpler rung", lambda o: (o["ladder"]["recommended"] in (1, 2), o["ladder"]["sentence"])),
        ("Plan: confirm the cost ratio", lambda o: (any(a["kind"] == "confirm_cost" for a in ((o.get("review") or {}).get("plan") or {}).get("next") or []), [a["kind"] for a in ((o.get("review") or {}).get("plan") or {}).get("next") or []])),
    ],
    "banking77": [
        ("Data Doctor: many classes, few rows in the rarest", lambda o: (_sev_ok(_f(o, "class_balance")), (_f(o, "class_balance") or {}).get("title"))),
        ("What's missing: overlapping label pairs", lambda o: (any(g.get("kind") == "overlap" or "overlap" in str(g.get("title")).lower() or "confus" in str(g.get("title")).lower() for g in o["doctor"]["gaps"][:3]), [g["title"] for g in o["doctor"]["gaps"][:3]])),
    ],
    "gmsc": [
        ("Data Doctor: no leakage false alarm", lambda o: (not _f(o, "leakage"), (_f(o, "leakage") or {}).get("title"))),
        ("Model Doctor: no leakage false alarm", lambda o: (o["doctor"]["primary"] != "leakage", o["doctor"]["headline"][:160])),
    ],
    "credit_approval": [
        ("Data Doctor: small sample", lambda o: (_sev_ok(_f(o, "sample_size"), ("medium", "high")), (_f(o, "sample_size") or {}).get("title"))),
        ("Ladder: a simpler rung within the (99 %) CI", lambda o: (o["ladder"]["recommended"] in (1, 2), o["ladder"]["sentence"])),
        ("Model Doctor: no leakage false alarm", lambda o: (o["doctor"]["primary"] != "leakage", o["doctor"]["headline"][:160])),
    ],
    "bank_leak": [
        ("Data Doctor: leakage suspect deposit_amount (high)", lambda o: ("deposit_amount" in ((_f(o, "leakage") or {}).get("suspects") or []) and _f(o, "leakage")["severity"] == "high", (_f(o, "leakage") or {}).get("title"))),
        ("Model Doctor: leakage names deposit_amount", lambda o: (o["doctor"]["primary"] == "leakage" and "deposit_amount" in (o["doctor"]["leak_suspects"] or []), o["doctor"]["headline"][:160])),
        ("Ablation Δ < 0 beyond noise", lambda o: (lambda a: (bool(a) and a.get("ci") and a["ci"][1] < 0, a))(((o.get("ablation") or {}).get("ablation")))),
        ("Review: the ablation is kept-honest", lambda o: (_kept(o, "ablate"), _journal(o, "ablate"))),
    ],
    "taiwan_flip": [
        ("Data Doctor: label quality flags (≥ low)", lambda o: (_sev_ok(_f(o, "label_quality")), (_f(o, "label_quality") or {}).get("title"))),
        ("Review: clean_labels kept", lambda o: (_kept(o, "clean_labels"), _journal(o, "clean_labels"))),
    ],
    "taiwan_ratio": [
        ("Review: add_features kept", lambda o: (_kept(o, "add_features"), _journal(o, "add_features"))),
    ],
    "taiwan_drift": [
        ("Data Doctor: outcome drifts over time", lambda o: (_sev_ok(_f(o, "time"), ("medium", "high")), (_f(o, "time") or {}).get("title"))),
        ("Model Doctor: shift (after the time shift check)", lambda o: (o["doctor"]["shift"].get("status") == "finding", (o["doctor"]["shift"].get("reason") or "")[:200])),
        ("Post-deployment: retrain_window better", lambda o: (((o.get("postdeploy") or {}).get("retrain_window") or {}).get("verdict") == "better",
                                                            {k: ((o.get("postdeploy") or {}).get("retrain_window") or {}).get(k) for k in ("current", "proposed", "delta", "ci", "verdict")})),
    ],
    "bank_999": [
        ("Data Doctor: disguised missing in age and campaign", lambda o: (bool(_f(o, "disguised_missing")) and {"age", "campaign"} <= set(_f(o, "disguised_missing")["columns"]), (_f(o, "disguised_missing") or {}).get("title"))),
        ("The fix applied: a version with the flags, the finding gone", lambda o: (lambda a: ({"age_was_missing", "campaign_was_missing"} <= set(a.get("columns_added") or []) and "disguised_missing" not in (a.get("after_findings") or []), a.get("columns_added")))(o.get("apply") or {})),
    ],
    "bank_dupes": [  # bank without duration already holds real duplicates and conflicts: the plant must add its block
        ("Data Doctor: the planted block's 1,200 exact duplicates on top of the base's", lambda o: (
            ((_f(o, "duplicates") or {}).get("numbers") or {}).get("exact duplicates", 0) - o["_base_counts"]["duplicates"] >= 1200,
            f"{(_f(o, 'duplicates') or {}).get('title')} (base {o['_base_counts']['duplicates']:,})")),
        ("Data Doctor: the planted 300 conflicting labels on top of the base's", lambda o: (
            ((_f(o, "conflicts") or {}).get("numbers") or {}).get("groups", 0) - o["_base_counts"]["conflict_groups"] >= 250,
            f"{(_f(o, 'conflicts') or {}).get('title')} (base {o['_base_counts']['conflict_groups']:,} groups)")),
    ],
    "gmsc_mnar": [
        ("What's missing: the young segment named, income missing inside it",
         lambda o: (any(("age" in str(g.get("title")) + str(g.get("finding"))) and "MonthlyIncome" in str(g.get("finding")) for g in o["doctor"]["gaps"][:3]),
                    [f"{g['title']}: {str(g.get('finding'))[:160]}" for g in o["doctor"]["gaps"][:3]])),
    ],
    "taiwan_nopay0": [
        ("Relative to Taiwan: model error rises", lambda o: (lambda a, b: (a is not None and b is not None and b > a, f"{a} → {b}"))(
            ((o.get("_base") or {}).get("irreducible") or {}).get("model_error"), (o["doctor"].get("irreducible") or {}).get("model_error"))),
        ("Relative to Taiwan: irreducible error R_NN rises", lambda o: (lambda a, b: (a is not None and b is not None and b > a, f"{a} → {b}"))(
            ((o.get("_base") or {}).get("irreducible") or {}).get("r_nn"), (o["doctor"].get("irreducible") or {}).get("r_nn"))),
        ("Relative to Taiwan: twins oracle rises", lambda o: (lambda a, b: (a is not None and b is not None and b > a, f"{a} → {b}"))(
            ((next((g for g in (o.get("_base") or {}).get("gaps") or [] if g.get("kind") == "twins"), {}) or {}).get("gain") or {}).get("delta"),
            ((next((g for g in o["doctor"].get("gaps") or [] if g.get("kind") == "twins"), {}) or {}).get("gain") or {}).get("delta"))),
    ],
}
for _n in ("taiwan", "bank", "german_cost", "banking77", "gmsc", "credit_approval"):
    EXPECT[_n].insert(0, _published(_n, {}))
# a planted issue must not raise false alarms elsewhere: the leak copy's honest twin is bank without duration
EXPECT["bank_leak"].append(("Without the leak: AUC in the published no-duration range", lambda o: (lambda s: (s is not None and 0.77 <= s <= 0.82, s))((o.get("ablation") or {}).get("child_auc"))))


def evaluate(name: str, o: dict) -> list[dict]:
    out = []
    for text, fn in EXPECT.get(name, []):
        try:
            ok, seen = fn(o)
        except Exception as e:  # noqa: BLE001 - a missing piece of the chain is a FAIL, with the reason
            ok, seen = False, f"{type(e).__name__}: {e}"
        out.append({"expect": text, "pass": bool(ok), "seen": seen})
    return out


# ------------------------------------------------------------------------------------------------- printing

def show(name: str, o: dict) -> None:
    print(f"\n=== {name} ===")
    if o.get("run"):
        r = o["run"]
        print(f"  run {r['run']} {r['status']} in {r['seconds']} s · holdout {r['holdout']} on {r.get('holdout_rows')} rows")
    for f in (o.get("dd") or {}).get("findings") or []:
        if f["severity"] != "info" or f["id"] in ("time", "label_quality"):
            print(f"  DD [{f['severity']}] {f['title'][:150]}")
    if o.get("ladder"):
        print(f"  ladder → {o['ladder']['sentence']}")
    if o.get("doctor"):
        d = o["doctor"]
        print(f"  Doctor: {d['headline'][:300]}")
        ir = d.get("irreducible")
        if ir:
            print(f"  irreducible [{ir.get('lower')}, {ir.get('r_nn')}] vs model error {ir.get('model_error')}")
        for g in d["gaps"][:3]:
            gn = g.get("gain") or {}
            print(f"  missing {g['rank']}. {g['title']}: {str(g.get('finding'))[:170]}" + (f" · gain {gn.get('delta'):+.4f}" if gn.get("delta") is not None else ""))
    if o.get("ablation"):
        print(f"  ablation {o['ablation']['run']}: {o['ablation']['ablation']}")
    if o.get("shift_check"):
        print(f"  shift check: {o['shift_check']}")
    if o.get("review"):
        rv = o["review"]
        print(f"  review {rv.get('id')} ({rv.get('mode')}, {rv.get('decider')}) {rv.get('status')} in {rv.get('wall_seconds')} s · stop: {rv.get('stop')}")
        for j in rv.get("journal") or []:
            r = j.get("result") or {}
            d = f"Δ {r['delta']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}] {r['metric']}" if r.get("delta") is not None else (j.get("note") or j.get("status"))
            print(f"    {j['step']}. [{j['verdict'] or j['status']}] {j['title'][:80]} → {j['kind']} {d}")
        p = rv.get("plan") or {}
        print(f"    plan: {str(p.get('summary'))[:300]}")
        for a in (p.get("next") or [])[:6]:
            print(f"     - [{a['kind']}] {a['title'][:120]}")
    if o.get("apply"):
        print(f"  applied: {o['apply']}")
    for e in o.get("expect") or []:
        print(f"  {'PASS' if e['pass'] else 'FAIL'}  {e['expect']}  — seen: {str(e['seen'])[:220]}")


# ------------------------------------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default=None)
    ap.add_argument("--redo", action="store_true", help="train again even when a finished run is recorded")
    ap.add_argument("--recheck", action="store_true", help="re-read the Data Doctor and Model Doctor of recorded runs")
    ap.add_argument("--no-review", action="store_true")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api.rstrip("/"), timeout=1800)
    its = items()
    names = [x.strip() for x in (a.only or ",".join(its)).split(",") if x.strip()]
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    t_all = time.time()
    for name in names:
        it = its[name]
        o = res.get(name) or {}
        path, target = it["src"]
        fresh = a.redo or not o.get("dataset") or c.get(f"/api/datasets/{o['dataset']}").status_code != 200
        print(f"\n--- {name}: {path.name}" + (f" · planted: {rdd.PLANTED[name][2]}" if name in rdd.PLANTED else ""))
        t0 = time.time()
        if fresh:
            o = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
            ds = (res.get(it["dataset_of"]) or {}).get("dataset") if it.get("dataset_of") else None
            o["dataset"] = ds or upload(c, path, target)
        if fresh or a.recheck or not o.get("dd"):
            o["dd"] = data_doctor(c, o["dataset"])
        if it.get("apply_fix") and (fresh or a.recheck or not o.get("apply")):
            o["apply"] = apply_fix(c, o["dataset"], it["apply_fix"])
        if it.get("train", True):
            if fresh or not (o.get("run") or {}).get("status") == "succeeded":
                o["run"] = train(c, name, o["dataset"], target, it)
                for k in ("ladder", "doctor", "ablation", "review", "shift_check", "postdeploy"):
                    o.pop(k, None)
            if o["run"]["status"] != "succeeded":
                print(f"  run failed: {o['run'].get('error')}")
                res[name] = o
                OUT.write_text(json.dumps(res, indent=1, default=str))
                continue
            rid = o["run"]["run"]
            if fresh or a.recheck or not o.get("ladder"):
                o["ladder"] = ladder(c, rid)
            if it.get("shift") and not o.get("shift_check"):
                o["shift_check"] = shift(c, rid, "2025-06-01")
            if fresh or a.recheck or not o.get("doctor"):
                o["doctor"] = doctor(c, rid)
            if it.get("ablate") and not o.get("ablation"):
                o["ablation"] = ablate(c, rid, o["doctor"])
            elif it.get("ablate") and a.recheck and o.get("ablation"):
                o["ablation"]["child_doctor"] = doctor(c, o["ablation"]["run"])
            if it.get("review") and not a.no_review and not (o.get("review") or {}).get("status") == "done":
                o["review"] = review(c, rid, it["review"])
            if it.get("postdeploy") and not ((o.get("postdeploy") or {}).get("retrain_window")):
                o["postdeploy"] = postdeploy(c, path, target, "2025-06-01")
        if name == "bank_dupes":  # the base table's own duplicates and conflicts, counted the Data Doctor's way
            b = pd.read_csv(rd.prepare()["bank"]).drop(columns=["duration"])
            feats = [x for x in b.columns if x != "y"]
            key = pd.util.hash_pandas_object(b[feats].astype(str), index=False)
            nun = b["y"].groupby(key.to_numpy()).transform("nunique").to_numpy()
            o["_base_counts"] = {"duplicates": int(b.duplicated().sum()), "conflict_groups": int(pd.Series(key[nun > 1]).nunique())}
        if it.get("dataset_of"):
            o["_base"] = (res.get(it["dataset_of"]) or {}).get("doctor")
        o["expect"] = evaluate(name, o)
        o["planted"] = rdd.PLANTED.get(name)
        o["seconds_this_pass"] = round(time.time() - t0)
        res[name] = o
        OUT.write_text(json.dumps(res, indent=1, default=str))
        show(name, o)
    # the table
    print("\n=== expectations ===")
    n_pass = n_all = 0
    for name in res:
        for e in res[name].get("expect") or []:
            n_all += 1
            n_pass += e["pass"]
            print(f"  {'PASS' if e['pass'] else 'FAIL'}  {name:16s} {e['expect']}")
    print(f"\n{n_pass}/{n_all} expectations met · this pass {round((time.time() - t_all) / 60, 1)} min · written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

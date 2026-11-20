#!/usr/bin/env python3
"""D2 on real data: the model ladder on the four public datasets and Feature Lab on Taiwan.

    backend/.venv/bin/python -I scripts/realdata_d2.py [--api http://127.0.0.1:8000] [--only taiwan,bank,german,banking77,featurelab]

Downloads and converts with scripts/realdata_d1.py (cached in backend/data/realdata/, not committed), uploads each CSV,
trains one run (AutoGluon medium_quality defaults, 5-fold bagging, one stack level, the SCORECARD on binary runs), reads
GET /api/runs/{id}/ladder, then runs Feature Lab on Taiwan (heuristic proposals, then the utilisation-style ratios
named in the plan as given candidates). Results go to backend/data/realdata/d2_results.json and are printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realdata_d1 as rd  # noqa: E402

RUNS = {  # name → (time_limit, extra body)
    "taiwan": (900, {"drop_columns": ["ID"]}),
    "bank": (900, {}),
    "german": (600, {}),
    "banking77": (900, {}),
}


def wait(c: httpx.Client, rid: str, timeout: float = 4000) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(5)
    raise TimeoutError(rid)


def ladder(c: httpx.Client, name: str, path: Path, target: str) -> dict:
    ds = c.post("/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")}, data={"target": target}).json()["id"]
    tl, extra = RUNS[name]
    df = pd.read_csv(path, nrows=5)
    binary = name != "banking77"
    body = {"dataset_id": ds, "label": target, "presets": "medium_quality", "time_limit": tl,
            "eval_metric": "roc_auc" if binary else None, "problem_type": "binary" if binary else "multiclass",
            "fit_kwargs": {"num_bag_folds": 5, "num_stack_levels": 1, "dynamic_stacking": False},
            "feature_importance": False, "source": "realdata-d2", **extra}
    t0 = time.time()
    rid = c.post("/api/runs", json=body).json()["id"]
    rec = wait(c, rid)
    if rec["status"] != "succeeded":
        return {"dataset": ds, "run": rid, "status": rec["status"], "error": rec.get("error")}
    lad = c.get(f"/api/runs/{rid}/ladder").json()
    rungs = [{k: r.get(k) for k in ("rung", "name", "model", "score", "gap", "within", "latency_ms_p95", "size_bytes", "missing")} for r in lad["rungs"]]
    out = {"dataset": ds, "run": rid, "rows": rec["data"]["rows"], "seconds": round(time.time() - t0), "metric": lad["metric_label"],
           "level": lad["level"], "small_data": lad.get("small_data"), "recommended": lad.get("recommended"),
           "sentence": lad.get("sentence"), "distill": lad["distill"].get("available"), "rungs": rungs}
    stack = next(r for r in rungs if r["rung"] == 3)
    recr = next((r for r in rungs if r["rung"] == (lad.get("recommended") or {}).get("rung")), None)
    if recr and recr.get("gap"):
        out["delta_to_stack"] = (recr["score"] or 0) - (stack["score"] or 0) if recr["rung"] != 3 else 0.0
    print(f"\n{name}: run {rid} · {rec['data']['rows']:,} rows · {out['seconds']} s · level {lad['level']}")
    for r in rungs:
        g = r.get("gap") or {}
        print(f"  {r['rung']} {r['name']:28s} {str(r['model'] or r.get('missing') or ''):32.32s} "
              f"{(r['score'] if r['score'] is not None else float('nan')):.4f}  gap {g.get('delta', float('nan')):+.4f} "
              f"[{(g.get('ci') or [float('nan')] * 2)[0]:+.4f}, {(g.get('ci') or [float('nan')] * 2)[1]:+.4f}]  "
              f"{'within' if r.get('within') else '      '}  {r.get('latency_ms_p95') or 0:7.1f} ms  {(r.get('size_bytes') or 0) / 1e6:7.1f} MB")
    print(f"  → {lad.get('sentence')}")
    return out


def featurelab(c: httpx.Client, path: Path, target: str) -> dict:
    ds = c.post("/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")}, data={"target": target}).json()["id"]
    out = {"dataset": ds}
    given = [{"op": "ratio", "a": f"BILL_AMT{i}", "b": "LIMIT_BAL", "why": "utilisation (plan)"} for i in (1, 2, 3)] + \
            [{"op": "ratio", "a": f"PAY_AMT{i}", "b": f"BILL_AMT{i + 1}", "why": "payment ÷ the bill it pays (plan)"} for i in (1, 2)] + \
            [{"op": "diff", "a": "BILL_AMT1", "b": "BILL_AMT2", "why": "balance trend"},
             {"op": "diff", "a": "BILL_AMT1", "b": "PAY_AMT1", "why": "unpaid this month"}]
    for key, body in (("heuristics", {"max_candidates": 20, "time_limit": 600}),
                      ("plan_ratios", {"candidates": given, "time_limit": 600})):
        j = c.post(f"/api/datasets/{ds}/feature-lab", json=body).json()
        while True:
            g = c.get(f"/api/datasets/{ds}/feature-lab/{j['id']}").json()
            if g["status"] != "running":
                break
            time.sleep(3)
        r = g.get("result") or {}
        out[key] = {"job": g["id"], "status": g["status"], "error": g.get("error"), "seconds": r.get("seconds"),
                    "base_oof": r.get("base_score_oof"), "final_oof": r.get("final_score_oof"), "kept": [t["name"] for t in r.get("kept") or []],
                    "holdout": r.get("holdout"), "version": (g.get("version") or {}).get("id"),
                    "journal": [{k: e.get(k) for k in ("name", "status", "delta", "ci", "single", "reason")} for e in g.get("journal") or []]}
        print(f"\nFeature Lab on Taiwan ({key}): {g['status']} in {r.get('seconds')} s · base OOF AUC {r.get('base_score_oof')}")
        for e in out[key]["journal"]:
            ci = e.get("ci") or [None, None]
            print(f"  {e['name']:34s} {e['status']:8s} Δ {e['delta'] if e['delta'] is None else f'{e['delta']:+.4f}'}"
                  f" {'' if ci[0] is None else f'[{ci[0]:+.4f}, {ci[1]:+.4f}]'}  alone {e['single'] if e['single'] is None else f'{e['single']:.3f}'}")
        print(f"  kept {out[key]['kept']} · holdout {r.get('holdout')}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="taiwan,bank,german,banking77,featurelab")
    a = ap.parse_args()
    files = rd.prepare()
    c = httpx.Client(base_url=a.api.rstrip("/"), timeout=600)
    res_path = rd.ROOT / "d2_results.json"
    res = json.loads(res_path.read_text()) if res_path.exists() else {}
    for name in a.only.split(","):
        if name == "featurelab":
            res["featurelab_taiwan"] = featurelab(c, files["taiwan"], rd.OUT["taiwan"][1])
        else:
            res[name] = ladder(c, name, files[name], rd.OUT[name][1])
        res_path.write_text(json.dumps(res, indent=1, default=str))
    print(f"\nwritten {res_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

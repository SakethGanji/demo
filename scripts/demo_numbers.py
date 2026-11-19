#!/usr/bin/env python3
"""The numbers docs/demo-script.md quotes, measured by replaying the demo's clicks as the API calls the UI makes.

    backend/.venv/bin/python scripts/demo_numbers.py [--api http://127.0.0.1:8000]

Not a test (the smoke is the gate): one pass of the bank story on the seeded credit-default sample, printing each
number the presenter points at. The bodies are the ones the UI sends: New Model's wizard body (ID columns dropped,
the advanced fit_kwargs pasted from the script), Results' split twin, the Gym's body of that run plus the F7 cost and
F8 constraint, Register, Try It, Explain → Scorecard / Learning Curve / Model Doctor.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke as S  # noqa: E402  (the smoke's HTTP helpers: get, post, wait_run, poll, primary)

DS, LABEL = "credit-default", "defaulted"
HP = {"GBM": {"num_boost_round": 40}, "RF": {"n_estimators": 40}, "LR": {}}  # pasted in New Model → Advanced


def say(k: str, v: object) -> None:
    print(f"  {k:<34} {v}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    S.API = a.api.rstrip("/")
    S.C = httpx.Client(base_url=S.API, timeout=httpx.Timeout(900, connect=10))
    get, post = S.get, S.post
    t0 = time.time()

    prof = get(f"/api/datasets/{DS}")["profile"]
    drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != LABEL]
    opts = get(f"/api/datasets/{DS}/split-options", target=LABEL)
    p = opts["suggested_params"]
    oot = {"strategy": "time", "time_column": p.get("time_column"), "cutoff": None, "tuning_share": p.get("tuning_share", 0.2)}
    print("1 · New Model, out-of-time split (ROC AUC)")
    say("random-split warnings", " | ".join(opts["warnings"])[:160])
    body = {"dataset_id": DS, "label": LABEL, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
            "time_limit": 120, "fit_kwargs": {"hyperparameters": HP}, "drop_columns": drop, "feature_importance": False,
            "calibrate_threshold": "f1", "split_spec": oot, "source": "demo"}
    A = S.wait_run(post("/api/runs", body)["id"])
    sp = A["split"]
    say("run", f"{A['id']} · split {sp.get('strategy')} · cut-off {sp.get('cutoff')} · holdout {A['data']['holdout_rows']:,}")
    say("holdout ROC AUC (out-of-time)", f"{S.primary(A)[1]:.4f} · t = {A['threshold']['value']:.3f}")
    R = S.wait_run(post(f"/api/runs/{A['id']}/split-twin", {"split_spec": None})["id"])
    c = get(f"/api/runs/{A['id']}/split-comparison")
    by = {x["strategy"]: x for x in c["runs"] if x["same_config"] or x["this_run"]}
    say("random twin ROC AUC", f"{by['random-stratified']['score']:.4f} ({R['id']})")
    say("optimism random − out-of-time", f"{by['time']['optimism']:+.4f}")

    print("2 · Gym from that run: business cost FN = 10 × FP + debt_to_income ↑ (compliant families)")
    g = get(f"/api/runs/{A['id']}/gym")["body"]
    g = {**g, "eval_metric": None, "cost": {"c_fp": 1, "c_fn": 10, "g_tp": 0}, "calibrate_threshold": None,
         "monotone": {"constraints": {"debt_to_income": "increasing"}, "compliant_only": True}, "source": "gym"}
    B = S.wait_run(post("/api/runs", g)["id"])
    say("run", f"{B['id']} · best {B['best_model']} · t = {B['threshold']['value']:.3f} (cost-calibrated)")
    tw = S.wait_run(post(f"/api/runs/{B['id']}/metric-twin", {"eval_metric": "roc_auc"})["id"])
    cc = get(f"/api/runs/{B['id']}/cost-comparison")
    cb = {("cost" if x["trained_on_cost"] else x["eval_metric"]): x for x in cc["runs"]}
    me, roc = cb["cost"], cb.get("roc_auc")
    say("cost per 1,000 (cost run)", f"{me['deployed']['per_1000']:.1f} [{me['deployed']['ci_per_1000'][0]:.1f}, {me['deployed']['ci_per_1000'][1]:.1f}]")
    if roc:
        d = roc["delta"]
        say("cost per 1,000 (ROC AUC twin)", f"{roc['deployed']['per_1000']:.1f} at t = {roc['threshold']['value']:.3f} ({tw['id']})")
        say("Δ per 1,000 (twin − cost)", f"{d['per_1000']:+.1f} [{d['ci_per_1000'][0]:+.1f}, {d['ci_per_1000'][1]:+.1f}]")
    m = get(f"/api/runs/{B['id']}/monotone", constraints="debt_to_income:increasing")["columns"]["debt_to_income"]
    say("monotone check (served model)", {k: m.get(k) for k in ("curves_violating", "n_curves", "pd_monotone", "pd_range") if k in m})

    print("3 · Register → Reason Codes / Try It")
    v = post(f"/api/runs/{B['id']}/register", {"created_by": "Demo Analyst", "model_name": "demo.credit-default"})
    v = S.poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
    say("version", f"{v['id']} · {v['status']} · serves {v['result']['version']['best_model']}")
    hp_ = get(f"/api/runs/{B['id']}/holdout-predictions")
    import numpy as np
    import pandas as pd
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    from app.samples import generate_credit
    df, _ = generate_credit()
    rows = df.iloc[np.asarray(hp_["rows"], dtype=int)].drop(columns=[LABEL])
    import json
    recs = json.loads(rows.to_json(orient="records"))
    sc = post(f"/api/registry/versions/{v['id']}/score", {"rows": recs[:400]})
    adv = [r for r in (sc.get("reasons") or {}).get("codes", []) if r]
    say("/score 400 holdout rows", f"{len(adv)} adverse with codes · first: {adv[0] if adv else '—'}"[:300])
    for r in recs[:60]:
        out = post(f"/api/registry/versions/{v['id']}/predict", {"row": r, "explain": False})
        rs = out.get("reasons") or {}
        if rs.get("adverse"):
            say("Try It: an adverse row", f"p = {rs.get('p_positive'):.3f} · " + " · ".join(f"{x.get('code')} {x.get('feature')}" for x in rs.get("codes", [])))
            break

    print("4 · Explain → Scorecard")
    s = get(f"/api/runs/{B['id']}/scorecard")
    h = s["holdout"]
    say("scorecard", f"{s['table']['n_features']} features · AUC scorecard {h['scorecard']['auc']:.4f} vs served {h['served']['auc']:.4f}")
    say("ΔAUC [95% CI]", f"{h['delta_auc']['delta']:+.4f} [{h['delta_auc']['ci'][0]:+.4f}, {h['delta_auc']['ci'][1]:+.4f}]")

    print("5 · Explain → Learning Curve (defaults)")
    post(f"/api/runs/{B['id']}/learning-curve", {})
    lc = S.poll(f"/api/runs/{B['id']}/learning-curve", lambda x: (x.get("status") or "") in ("succeeded", "failed"), 900, 2)
    vd = lc.get("verdict") or {}
    say("verdict", f"{vd.get('label')} · gain per doubling {vd.get('slope_per_doubling', {}).get('value', float('nan')):+.4f} "
                   f"{vd.get('slope_per_doubling', {}).get('ci')} · 2N {vd.get('extrapolation', {}).get('x2', {}).get('score')}")
    say("points", [(pt.get("n"), round(pt.get("mean", float("nan")), 4)) for pt in lc.get("curve", [])][:4])

    print("6 · Explain → Model Doctor")
    d = get(f"/api/runs/{B['id']}/doctor")
    pr = d.get("primary") or {}
    say("primary", f"{pr.get('title')} ({pr.get('severity')}) · {pr.get('reason', '')[:200]}")
    say("headline", d.get("headline"))
    say("missing checks", [c["id"] for c in d["checks"] if c["status"] == "missing"])
    print(f"\n{time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

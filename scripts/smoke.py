#!/usr/bin/env python
"""End-to-end smoke check of the ENSEMBLE API, called the way the UI calls it, chained step by step.

    make dev                                   # API on :8000 (or any running API)
    backend/.venv/bin/python scripts/smoke.py [--api http://127.0.0.1:8000]

Upload a CSV → Gym validate → start a run (a small fast config) → follow its SSE to the end → leaderboard and results →
explain, threshold, SHAP → custom ensemble → Vela finding → apply its patch and retrain → register, approve, promote →
Try It → batch score with drift → export.py and run the exported script. Prints ✓ / ✗ per step; exits 1 on a failure.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

HP = {"GBM": {"num_boost_round": 40}, "LR": {}, "RF": {"n_estimators": 50}}
results: list[tuple[str, bool, str]] = []


class Fail(Exception):
    pass


def step(name: str):
    def deco(fn):
        def run(*a, **k):
            t0 = time.time()
            try:
                note = fn(*a, **k)
                results.append((name, True, note or ""))
                print(f"✓ {name}  ({time.time() - t0:.1f} s){'  · ' + note if note else ''}", flush=True)
                return note
            except Exception as e:  # noqa: BLE001
                results.append((name, False, str(e)))
                print(f"✗ {name}  ({time.time() - t0:.1f} s)  · {type(e).__name__}: {e}", flush=True)
                if not isinstance(e, Fail):
                    traceback.print_exc(limit=2)
                raise SystemExit(1)
        return run
    return deco


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def ok(r: httpx.Response, *codes: int) -> dict:
    if r.status_code not in (codes or (200,)):
        raise Fail(f"{r.request.method} {r.request.url.path} → {r.status_code}: {r.text[:300]}")
    return r.json()


def make_csv(n: int = 1500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    income = rng.lognormal(10.8, 0.4, n).round(0)
    util = rng.beta(2, 4, n).round(3)
    region = rng.choice(["north", "south", "east", "west"], n)
    logit = -1.0 + 2.6 * util - 0.00002 * (income - 50_000) + (region == "south") * 0.6 + rng.normal(0, 1, n)
    y = np.where(logit > 0.4, "bad", "good")
    return pd.DataFrame({"applicant_id": [f"A{i:05d}" for i in range(n)], "income": income, "utilization": util,
                         "region": region, "collections_flag": np.where(y == "bad", "sent", "none"),  # planted leak
                         "outcome": y})


def wait_run(c: httpx.Client, rid: str, timeout: float = 600) -> dict:
    t = time.time() + timeout
    while time.time() < t:
        r = ok(c.get(f"/api/runs/{rid}"))
        if r["status"] not in ("queued", "running"):
            check(r["status"] == "succeeded", f"run {rid} {r['status']}: {r.get('error')}")
            return r
        time.sleep(1)
    raise Fail(f"run {rid} did not finish in {timeout:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.api, timeout=180)
    s: dict = {}
    df = make_csv()

    @step("health")
    def _():
        h = ok(c.get("/api/health"))
        return f"AutoGluon {h['engine']['version']}, {h['runs']} runs"
    _()

    @step("upload a CSV")
    def _():
        d = ok(c.post("/api/datasets", files={"file": ("smoke_applicants.csv", df.to_csv(index=False).encode(), "text/csv")}), 201)
        check(d["profile"]["rows"] == len(df), "row count")
        s["ds"] = d
        return f"{d['id']} · {d['profile']['rows']} rows · target {d['profile'].get('target')}"
    _()

    body = lambda: {"dataset_id": s["ds"]["id"], "label": "outcome", "time_limit": 120, "presets": "medium_quality",  # noqa: E731
                    "calibrate_threshold": "f1", "fit_kwargs": {"hyperparameters": HP}, "source": "smoke"}

    @step("gym: schema and validate")
    def _():
        sch = ok(c.get("/api/gym/schema"))
        check(len(sch["families"]) == 11 and "num_bag_folds" in sch["fit"]["keys"], "schema")
        bad = ok(c.post("/api/gym/validate", json={**body(), "fit_kwargs": {"num_bag_fold": 3}}))
        check(not bad["ok"] and "num_bag_folds" in bad["errors"][0]["message"], "a typo must be refused")
        v = ok(c.post("/api/gym/validate", json=body()))
        check(v["ok"], f"validate: {v['errors']}")
        check(v["resolved"]["fit_kwargs"]["hyperparameters"] == HP, "resolved hyperparameters")
        return f"T̂ ≈ {v['estimate']['seconds']:.0f} s · k={v['effective']['num_bag_folds']} L={v['effective']['num_stack_levels']}"
    _()

    @step("gym: start a run")
    def _():
        r = ok(c.post("/api/runs", json=body()), 201)
        s["run"] = r["id"]
        return r["id"]
    _()

    @step("follow the run's SSE to the end")
    def _():
        types = []
        with c.stream("GET", f"/api/runs/{s['run']}/events", timeout=httpx.Timeout(600, connect=10)) as r:
            check(r.headers["content-type"].startswith("text/event-stream"), "SSE content type")
            for line in r.iter_lines():
                if line.startswith("event:"):
                    types.append(line[6:].strip())
        check(types[0] == "run_queued" and types[-1] == "run_succeeded", f"stream {types[:2]}…{types[-2:]}")
        check("model_finished" in types and "leaderboard" in types and "tracked" in types, "missing events")
        return f"{len(types)} events · {types.count('model_finished')} models finished"
    _()

    @step("leaderboard and results")
    def _():
        r = wait_run(c, s["run"])
        s["r"] = r
        check(r["leaderboard"] and r["holdout"]["metrics"], "leaderboard / holdout")
        check(r["mlflow"] and r["mlflow"]["run_id"], "tracked in MLflow")
        m = r["eval_metric"]["name"]
        return f"{len(r['leaderboard'])} models · best {r['best_model']} · holdout {m} {r['holdout']['metrics'][m]:.4f} · t = {r['threshold']['value']:.3f}"
    _()

    @step("explain, threshold, SHAP")
    def _():
        rid = s["run"]
        e = ok(c.get(f"/api/runs/{rid}/explain"))
        t = ok(c.get(f"/api/runs/{rid}/threshold"))
        cal = ok(c.post(f"/api/runs/{rid}/threshold", json={"metric": "balanced_accuracy"}))
        hp = ok(c.get(f"/api/runs/{rid}/holdout-predictions"))
        sh = ok(c.get(f"/api/runs/{rid}/shap", params={"row": hp["rows"][0]}))
        check(abs(sh["additivity_error"]) < 1e-6, f"SHAP additivity error {sh['additivity_error']}")
        check(len(t["grid"]) > 100, "threshold grid")
        return (f"{len(e)} explain sections · {len(t['grid'])} t-grid points · t(bacc) = {cal['threshold']['value']:.3f} · "
                f"f(x) = φ₀ + Σφ = {sh['base_value']:.3f} + {sh['sum_phi']:.3f}")
    _()

    @step("custom ensemble")
    def _():
        base = [x["model"] for x in s["r"]["leaderboard"] if x["stack_level"] == 1][:2]
        e = ok(c.post(f"/api/runs/{s['run']}/ensemble", json={"models": base}, timeout=600), 201)
        return f"{e.get('model')} over {', '.join(base)}"
    _()

    @step("vela: findings from the run")
    def _():
        v = ok(c.get(f"/api/runs/{s['run']}/vela"))
        kinds = [f["kind"] for f in v["findings"]]
        check("leakage" in kinds, f"the planted leak must be flagged, got {kinds}")
        f = next(x for x in v["findings"] if x["kind"] == "leakage")
        check(f["patch"]["body"]["drop_columns"] and "collections_flag" in f["patch"]["body"]["drop_columns"], "patch")
        s["finding"] = f
        return f"{kinds} · {f['text'][:90]}…"
    _()

    @step("apply the Vela patch and retrain")
    def _():
        f = s["finding"]
        r = ok(c.post("/api/runs", json={**f["patch"]["body"], "parent_run_id": s["run"], "source": "vela",
                                         "patch": {"id": f["patch"]["id"], "title": f["patch"]["title"], "changes": f["patch"]["changes"]}}), 201)
        child = wait_run(c, r["id"])
        check("collections_flag" not in child["features"], "patched run still uses the leak")
        check(child["config"]["parent_run_id"] == s["run"], "lineage")
        s["child"] = child
        m = child["eval_metric"]["name"]
        return f"{child['id']} · holdout {m} {s['r']['holdout']['metrics'][m]:.4f} → {child['holdout']['metrics'][m]:.4f}"
    _()

    @step("register, approve, promote")
    def _():
        v = ok(c.post(f"/api/runs/{s['child']['id']}/register", json={"created_by": "Smoke Analyst"}), 202)
        t = time.time() + 600
        while v["status"] == "building" and time.time() < t:
            time.sleep(1)
            v = ok(c.get(f"/api/registry/versions/{v['id']}"))
        check(v["status"] == "ready", f"version {v['status']}: {v.get('error')}")
        ok(c.post(f"/api/registry/versions/{v['id']}/review", json={"decision": "approve", "reviewer": "Smoke Validator", "comment": "ok"}))
        ok(c.post(f"/api/registry/versions/{v['id']}/transition", json={"stage": "Production", "actor": "Smoke Validator"}))
        v = ok(c.get(f"/api/registry/versions/{v['id']}"))
        check(v["stage"] == "Production", "stage")
        s["v"] = v
        return f"{v['model_name']} v{v['version']} in Production"
    _()

    @step("try it: predict with reasons")
    def _():
        row = df.drop(columns=["outcome", "collections_flag", "applicant_id"]).iloc[0].to_dict()
        p = ok(c.post(f"/api/registry/versions/{s['v']['id']}/predict", json={"row": row, "explain": True}, timeout=300))
        check(p["prediction"] in ("bad", "good"), "prediction")
        return f"{p['prediction']} · keys {sorted(p)[:6]}"
    _()

    @step("batch score with drift")
    def _():
        b = df.drop(columns=["outcome"]).sample(400, random_state=1).copy()
        b["utilization"] = (b["utilization"] * 1.6).clip(0, 1)
        r = ok(c.post(f"/api/registry/versions/{s['v']['id']}/batches", files={"file": ("smoke_batch.csv", b.to_csv(index=False).encode(), "text/csv")},
                      data={"created_by": "Smoke Analyst"}), 202)
        t = time.time() + 600
        while r["status"] in ("queued", "running") and time.time() < t:
            time.sleep(1)
            r = ok(c.get(f"/api/registry/batches/{r['id']}"))
        check(r["status"] == "succeeded", f"batch {r['status']}: {r.get('error')}")
        feats = {f["feature"]: f for f in r["result"]["drift"]["features"]}
        check(feats["utilization"]["band"] != "stable", "the shifted feature must drift")
        dl = c.get(f"/api/registry/batches/{r['id']}/download")
        check(dl.status_code == 200 and len(pd.read_csv(io.StringIO(dl.text))) == 400, "download")
        return f"utilization PSI {feats['utilization']['psi']:.3f} ({feats['utilization']['band']}) · income PSI {feats['income']['psi']:.3f}"
    _()

    @step("export.py and run the exported script")
    def _():
        text = c.get(f"/api/runs/{s['child']['id']}/export.py").text
        check(text.startswith("#!/usr/bin/env python"), "script")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.py"
            p.write_text(text)
            out = subprocess.run([sys.executable, "-I", str(p), "--out", str(Path(d) / "repro")], capture_output=True, text=True, timeout=900)
        last = [ln for ln in out.stdout.splitlines() if "leaderboard check" in ln]
        check(out.returncode == 0, f"exit {out.returncode}: {(out.stdout + out.stderr)[-500:]}")
        return last[0] if last else "ok"
    _()

    print(f"\n{sum(1 for _, g, _ in results if g)}/{len(results)} steps green")
    return 0


if __name__ == "__main__":
    sys.exit(main())

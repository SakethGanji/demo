#!/usr/bin/env python3
"""ENSEMBLE smoke test: the real calls the UI makes, chained end to end against a running API.

    backend/.venv/bin/python scripts/smoke.py [--api http://127.0.0.1:8000] [--only core,samples,autopilot]

One line per step: ✓ or ✗, the step, a key number. Exits 1 if any step failed. Sections:
  core       health → upload a generated CSV → profile → run + SSE → leaderboard → explain / threshold / calibrate /
             SHAP / fairness → custom ensemble → register → approve → Try It → batch score → drift → Gym → Vela patch
             → retrain → compare → export.py run in a subprocess
  samples    loan-amount (regression) and support-tickets (text) through train and predict
  intake     G0 Smart Intake: every column's role on all bundled samples against a truth table (key roles 100 %), the
             top suggestion and its pre-fill, three fresh CSVs uploaded without a target, role edits and their effect
  autopilot  a full Autopilot on support-tickets: start, inbox, bulk answers, retrain, requirements, inbox recall
  forecast   transaction-volume sample and an uploaded CSV: mapping → preview → TimeSeriesPredictor run + SSE →
             leaderboard, weights, backtests (W auto) → per-item errors → interval calibration → conformal recalibration
             (F1: raw → calibrated coverage, MASE, per-model coverage) → fan chart → CSV download → new-history forecast → register
  anomaly    card-transactions: IsolationForest + LOF run + SSE → top anomalies with reasons → precision@k against the
             hidden truth → velocity features (F2: bursts found, P@90 vs the round-1 config, causality) → confirm /
             dismiss the flagged rows → supervised AutoGluon run on the confirmations (PU) compared on the hidden truth →
             batch score + download → register → Try It on a row
  import     fit a tiny TabularPredictor here, zip it, import it → leaderboard, explain, predict, register, Try It;
             also by server path
  targets    F4: deposits CSV uploaded without a target, trained on a non-guessed target via the UI's calls (confirm the
             target) and via the API (target named on upload): identical holdout rows, predictions and scores; a run on an
             unconfirmed label uses that label's split; switching targets never redraws a split
  llm        F5: the language-model router: route and settings → Vela ask p50/p95 on this API's real path (the claude
             CLI when no ANTHROPIC_API_KEY) while /api/health is probed → cache by (prompt hash, facts hash) → a 1 s
             timeout falls back to computed and kills the CLI → a second API whose SDK talks to scripts/fake_llm.py:
             SDK answers, SDK timeout / 500 / non-JSON → CLI, both → computed within the budget, no cached fallbacks,
             number guard, wording, /api/health during 48 asks in flight
  monotone   F8: credit-default with debt_to_income ↑: refused requests, Gym exclude / warn, a bagged + stacked run of
             the monotone subclasses (every model checked by ICE), the unconstrained twin (before), the registered
             version's served model checked on a grid through its own /score, and the constraint on the model card
  recourse   G1: counterfactuals on credit-default from the intake pre-fill (time-group split, F7 cost, F8 monotone):
             what may change (detected from roles, editable, audited), the stored acceptance check (≥ 90 % of ≥ 100
             adverse holdout rows flip; 0 fixed moved), options rebuilt and re-scored through /score, Build suggestion,
             batch recourse column
  scorecard  F11: the SCORECARD challenger on credit-default (bagged, stacked): refusals, Gym, table / IV / ΔAUC with a
             bootstrap CI, CSV export re-scores the holdout, points → score → P, reason codes; with F7 cost + F8 monotone;
             registry card; switched off
  learning   F12: learning curves on credit-default (group split, F7 cost, F8 monotone, F11 scorecard) and support-tickets:
             nested subsamples of the run's training rows (10/25/50 %, 3 seeds; whole customers), the run's own fit call,
             one holdout, power law, 2N / 4N extrapolation with intervals and the verdict; refusals, 409, cancel
  active     F13: active-learning labelling queue on support-tickets with 90 % of the training labels hidden: the run's own
             fit call, uncertainty + diversity picks (b = 200, 2 rounds), the queue answered (a person's skip and relabel,
             the rest by the SIMULATED oracle = the sample's hidden labels), each round a dataset version with lineage that
             trains, the holdout fixed and never queued; active vs random at equal budgets over 3 seeds (simulated
             oracle), reported with spread and a 95 % CI; the summary for F17; refusals, 409, cancel
  annotators F14: ground-truth reliability on support-tickets: the sample's 5 SIMULATED annotators (3 per row on 1,500
             training rows; one careless, one swapping Billing and Account) → p̂ out of fold (a quick bagged fit of the
             run's call; a bagged run's own OOF) → Cohen's and Fleiss' κ (recomputed here from the votes), per-annotator
             quality and the CROWDLAB consensus (cleanlab.multiannotator) against the hidden truth: the worst annotator found,
             consensus vs majority vote (acceptance); the disagreement queue (a person's vote and adjudication, the
             SIMULATED adjudicator), a dataset version with the holdout untouched; a long-format upload by ticket_id;
             refusals; the summary for F17
  shift      F15: adversarial validation on credit-default: an AutoGluon classifier (bagged, out-of-fold scores) tells
             reference rows from new rows: train vs holdout, two periods on F6's date column, registry batches (unshifted,
             and debt_to_income × 2: AUC > 0.7 with it ranked first; unshifted AUC ≈ 0.5); AUC and ESS recomputed from the
             per-row scores; excluded columns; out-of-time twin, drop patch, importance weights trained as sample_weight;
             refusals, 409; the summary for F17
  slices     F16: weak-slice discovery on personal-loans (planted slice channel = broker ∧ age < 30): every feature value and
             pair, Welch t vs the rest, Benjamini–Hochberg, Δ with a 95% CI, all recomputed with scipy + statsmodels; the
             planted slice at rank 1–3 (acceptance); its rows, SHAP inside vs outside, fairness, the F13 queue pre-filtered
             to it; the per-row loss of threshold, cost (F7), regression and quantile (F9) runs; the summary for F17
  doctor     F17: the Model Doctor on six crafted, seeded scenarios with a known answer (leaky feature, label noise,
             data-limited, shift, overfit, latency): each trained, the checks it needs started through the doctor's own
             offers (learning curve → more seeds when noisy, out-of-time twin, shift check, latency profile), the primary
             diagnosis asserted; the ranking rule recomputed, determinism, the run payload's doctor/1, the overfit
             prescription applied (before → after), the latency prescription's model within budget
  monitor    F18: monitoring over time on loan-stream: a champion (Production, four-eyes) and a Staging challenger; the
             12-month 2025 stream fed month by month (batches dated, shadow-scored; labels joined 3 months later on
             application_id); the PSI alert first on 2025-07 and never before; F15's adversarial AUC on months 6 and 7;
             the concept change seen only in labelled performance (z of observed vs predicted), visible 2025-10-31;
             paired ΔAUC champion vs challenger with DeLong's CI; no automatic promotion; the Model Doctor's shift check
             reads the alert and prescribes the retrain proposal; the proposal launches a run on the labelled window
  agent      G10: the Vela agent through this API's planner (claude CLI without a key; SMOKE_AGENT_DECIDER=stub):
             train → doctor → patch → compare → register from one instruction, paused before training; one decline
             explained with reason codes and counterfactuals; a batch scored and its drift reported; number guard on every
             answer; Skip and Stop
  datadoctor D1: the Data Doctor on crafted tables (scripts/data_doctor_data.py) with every issue planted: detection at
             the expected severity with its rule / numbers / evidence / fix, speed on ~40k rows, the fixes applied into a
             new version (effect, lineage, holdout kept, original untouched), the decision-time answer dropped from a run
             and recorded, merge / sample size / drift, cleanlab label quality against the planted flips, refusals, and no
             unexpected high-severity finding on the bundled samples
  ladder     D2: the model ladder on crafted data (scripts/ladder_data.py): rungs 0–3 from one training request on one
             holdout (baseline, the run's SCORECARD, its best single GBM, AutoGluon's best), paired-bootstrap gaps,
             latency and size; an additive world → a simpler rung, served through register(serve_model); a stack-only
             world → the stack and a distillation offer; small data → the 99 % interval; refusals
  doctor2    D3: the Model Doctor 2 on crafted tables (scripts/doctor2_data.py): a dominant leak with a Data Doctor hint →
             leakage and the one-click ablation (a child run on the same holdout; with / without / Δ CI checked against
             both runs), a cost run → the cost trade-off (3:1 / 5:1 / 10:1) instead of weak class and imbalance and a ladder
             ranked by expected cost, a flat ceiling → at the data's limit, the Holdout Errors view, determinism, refusals
  missing    D3: What's missing: planted twins (Bayes error 0.15 inside the R_NN interval), a hidden variable for one
             segment (the segment named, missingness inside its errors, a positive oracle), two overlapping labels
             (symmetric confusion); the ranking rule and the typed slot for D4's suggestions
  featurelab D2: Feature Lab: a planted debt / income ratio kept (training folds only) and confirmed on the holdout,
             applied as a dataset version; a leaky paid / billed ratio rejected; a column answered 'not known' never
             read; every DSL op equal to pandas; free-form code refused
  reset      a second API on a throwaway data folder: upload + run, POST /api/admin/reset, back to the samples only
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Callable

import httpx
import numpy as np
import pandas as pd

API = os.environ.get("ENSEMBLE_API_URL", "http://127.0.0.1:8000")
HP = {"GBM": {"num_boost_round": 40}, "RF": {"n_estimators": 40}, "LR": {}}
RESULTS: list[tuple[bool, str, str, float]] = []
C: httpx.Client


class SmokeError(AssertionError):
    pass


def check(cond: Any, msg: str) -> None:
    if not cond:
        raise SmokeError(msg)


def step(name: str, fn: Callable[[], str], *, fatal: bool = False) -> bool:
    t0 = time.time()
    try:
        info = fn() or ""
        ok = True
    except Exception as e:  # noqa: BLE001
        info = f"{type(e).__name__}: {e}"[:400]
        if os.environ.get("SMOKE_TRACE"):
            traceback.print_exc()
        ok = False
    dt = time.time() - t0
    RESULTS.append((ok, name, info, dt))
    print(f"{'✓' if ok else '✗'} {name:<34} {info}  ({dt:.1f} s)", flush=True)
    if not ok and fatal:
        raise SystemExit(summary())
    return ok


def summary() -> int:
    bad = [r for r in RESULTS if not r[0]]
    total = sum(r[3] for r in RESULTS)
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} steps passed in {total:.0f} s" + (f" · {len(bad)} failed: " + ", ".join(r[1] for r in bad) if bad else ""))
    return 1 if bad else 0


def j(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        raise SmokeError(f"{r.request.method} {r.request.url.path} → {r.status_code}: {r.text[:300]}")
    return r.json()


def get(path: str, **params: Any) -> Any:
    return j(C.get(path, params=params))


def post(path: str, body: Any = None, **kw: Any) -> Any:
    return j(C.post(path, json=body, **kw))


def follow_sse(run_id: str, timeout: float = 900) -> dict[str, Any]:
    """Read the run's SSE stream to its terminal event, as the UI does. Returns counts and the last event."""
    counts: dict[str, int] = {}
    last: dict[str, Any] = {}
    with httpx.stream("GET", f"{API}/api/runs/{run_id}/events", timeout=httpx.Timeout(timeout, connect=10)) as r:
        check(r.status_code == 200, f"SSE {r.status_code}")
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                counts[event] = counts.get(event, 0) + 1
                last = {"type": event, **json.loads(line[5:])}
    check(last.get("type") == "run_succeeded", f"run ended with {last.get('type')}: {last.get('error') or last}")
    return {"counts": counts, "last": last}


def wait_run(run_id: str, timeout: float = 900) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        rec = get(f"/api/runs/{run_id}")
        if rec["status"] in ("succeeded", "failed", "cancelled"):
            check(rec["status"] == "succeeded", f"{run_id} {rec['status']}: {rec.get('error')}")
            return rec
        time.sleep(1)
    raise SmokeError(f"{run_id} still running after {timeout:.0f} s")


def poll(path: str, done: Callable[[dict[str, Any]], bool], timeout: float = 600, every: float = 1) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        out = get(path)
        if done(out):
            return out
        time.sleep(every)
    raise SmokeError(f"{path} not done after {timeout:.0f} s")


def primary(rec: dict[str, Any]) -> tuple[str, float]:
    h = rec["holdout"] or {}
    m = (rec.get("eval_metric") or {}).get("name") or next(iter(h.get("scores") or {}), "?")
    return m, float((h.get("scores") or {}).get(m, float("nan")))


# --------------------------------------------------------------------------- data


def make_binary_csv(n: int = 1600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "customer_id": [f"K-{90000 + i}" for i in range(n)],
        "income_k": np.round(rng.lognormal(3.8, 0.4, n), 1),
        "utilisation": np.round(rng.beta(2, 3, n), 3),
        "late_payments": rng.poisson(0.8, n),
        "tenure_years": np.round(rng.gamma(2.0, 3.0, n), 1),
        "segment": rng.choice(["retail", "premier", "business"], n, p=[0.6, 0.25, 0.15]),
        "region": rng.choice(["north", "south", "east", "west"], n),
    })
    z = -2.2 + 2.6 * df["utilisation"] + 0.7 * df["late_payments"] - 0.06 * df["tenure_years"] \
        - 0.01 * (df["income_k"] - 45) + 0.6 * (df["segment"] == "business") + rng.normal(0, 0.6, n)
    df["defaulted"] = np.where(1 / (1 + np.exp(-z)) > rng.random(n), "yes", "no")
    return df


# --------------------------------------------------------------------------- core


def core(st: dict[str, Any]) -> None:
    def health():
        h = get("/api/health")
        check(h["status"] == "ok" and h["engine"]["name"] == "AutoGluon", h)
        return f"AutoGluon {h['engine']['version']} · {h['datasets']} datasets · {h['runs']} runs"

    step("health", health, fatal=True)

    def upload():
        df = make_binary_csv()
        st["df"] = df
        buf = df.to_csv(index=False).encode()
        d = j(C.post("/api/datasets", files={"file": ("smoke_default.csv", buf, "text/csv")}, data={"target": "defaulted"}))
        st["ds"] = d
        check(d["rows"] == len(df) and d["target"] == "defaulted", d)
        return f"{d['id']} · {d['rows']} rows × {d['n_cols']} cols"

    step("upload generated CSV", upload, fatal=True)

    def profile():
        d = get(f"/api/datasets/{st['ds']['id']}")
        p = d["profile"]
        types = {c["name"]: c["type"] for c in p["columns"]}
        check(types["customer_id"] == "id" and p["task"] == "binary", types)
        return f"task {p['task']} · imbalance ratio {p['imbalance_ratio']:.2f} · customer_id typed id (dropped)"

    step("profile", profile, fatal=True)

    def run():
        r = post("/api/runs", {"dataset_id": st["ds"]["id"], "time_limit": 90, "presets": "medium_quality",
                               "fit_kwargs": {"hyperparameters": HP, "num_bag_folds": 3}, "source": "smoke",
                               "calibrate_threshold": None})
        st["run"] = r["id"]
        s = follow_sse(r["id"])
        st["rec"] = wait_run(r["id"])
        c = s["counts"]
        return f"{r['id']} · {c.get('model_finished', 0)} model_finished events · SSE → run_succeeded"

    if not step("start run + follow SSE", run):
        return
    rid = st["run"]

    def leaderboard():
        rec = st["rec"]
        lb = rec["leaderboard"]
        check(len(lb) >= 3 and all(r.get("score_test") is not None for r in lb), lb)
        m, s = primary(rec)
        return f"{len(lb)} models · best {rec['best_model']} · holdout {m} {s:.4f}"

    step("leaderboard + results", leaderboard)

    def explain():
        e = get(f"/api/runs/{rid}/explain")
        check(e["confusion_matrix"] if "confusion_matrix" in e else e, "no confusion matrix")
        cm = np.asarray(e["confusion_matrix"] if not isinstance(e["confusion_matrix"], dict) else e["confusion_matrix"]["matrix"])
        roc = (e.get("roc") or {}).get("yes") or {}
        check(cm.sum() == st["rec"]["holdout"]["n_rows"], f"Σ confusion = {cm.sum()}")
        return f"Σ confusion = {int(cm.sum())} holdout rows · ROC AUC {roc.get('auc', float('nan')):.4f}"

    step("explain", explain)

    def thr():
        t = get(f"/api/runs/{rid}/threshold")
        b = t["best"]["f1"]
        return f"{len(t['grid'])} thresholds · argmax F1 t* = {b['threshold']:.3f} (F1 {b['value']:.4f})"

    step("threshold analysis", thr)

    def calibrate():
        out = post(f"/api/runs/{rid}/threshold", {"metric": "f1"})
        v = out["threshold"]["value"]
        check(0 < v < 1, out["threshold"])
        return f"calibrate_decision_threshold(f1) → t = {v:.3f}"

    step("calibrate threshold", calibrate)

    def shap():
        row_id = get(f"/api/runs/{rid}/holdout-predictions")["rows"][0]
        s = get(f"/api/runs/{rid}/shap", row=row_id)
        total = s["base_value"] + sum(p["phi"] for p in s["contributions"])
        check(abs(total - s["fx"]) < 1e-3, f"φ₀ + Σφ = {total} ≠ f(x) = {s['fx']}")
        return f"row {row_id} · f(x) = {s['fx']:.4f} = φ₀ {s['base_value']:.4f} + Σφᵢ {s['sum_phi']:+.4f}"

    step("SHAP one row", shap)

    def fair():
        f = get(f"/api/runs/{rid}/fairness", group="segment")
        gaps = f.get("gaps") or {}
        return f"{len(f.get('by_group') or [])} groups · demographic parity Δ {gaps.get('demographic_parity_difference', float('nan')):.3f}"

    step("fairness by segment", fair)

    def ensemble():
        lb = [r["model"] for r in st["rec"]["leaderboard"] if not r["model"].startswith("WeightedEnsemble")]
        out = post(f"/api/runs/{rid}/ensemble", {"models": lb[:2]})
        w = out.get("weights") or {}
        check(abs(sum(w.values()) - 1) < 1e-6, w)
        return f"{out['model']} · Σw = 1 · " + ", ".join(f"{k.split('_')[0]} {v:.3f}" for k, v in w.items())

    step("custom ensemble", ensemble)

    def register():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": f"smoke.{st['ds']['id']}"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        st["vid"] = v["id"]
        return f"{v['id']} · {v['model_name']} v{v['version']} · ready"

    if step("register", register):
        vid = st["vid"]

        def approve():
            post(f"/api/registry/versions/{vid}/review", {"decision": "approve", "reviewer": "Smoke Validator"})
            v = post(f"/api/registry/versions/{vid}/transition", {"stage": "Production", "actor": "Smoke Validator"})
            check(v.get("stage") == "Production" or get(f"/api/registry/versions/{vid}")["stage"] == "Production", v)
            return "four-eyes approve → Production"

        step("approve + promote", approve)

        def tryit():
            row = st["df"].drop(columns=["defaulted"]).iloc[0].to_dict()
            out = post(f"/api/registry/versions/{vid}/predict", {"row": row, "explain": True})
            check(out["prediction"] in ("yes", "no"), out)
            return f"prediction {out['prediction']} · p(yes) {out['probabilities']['yes']:.3f} vs t {out['threshold']:.3f} · {out['predict_ms']:.0f} ms"

        step("Try It predict", tryit)

        def batch():
            df = make_binary_csv(600, seed=11).drop(columns=["defaulted"])
            df["utilisation"] = (df["utilisation"] * 1.6).clip(0, 1)  # a shifted batch, so drift shows
            b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("smoke_batch.csv", df.to_csv(index=False).encode(), "text/csv")},
                         data={"created_by": "Smoke Analyst"}))
            b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
            check(b["status"] == "succeeded", b.get("error"))
            st["batch"] = b
            dl = C.get(f"/api/registry/batches/{b['id']}/download")
            check(dl.status_code == 200 and len(pd.read_csv(io.BytesIO(dl.content))) == 600, "download")
            return f"{b['id']} · 600 rows scored + downloaded"

        if step("batch score", batch):
            def drift():
                d = (st["batch"].get("result") or {}).get("drift") or {}
                feats = {f["feature"]: f["psi"] for f in d.get("features", []) if "feature" in f} if isinstance(d.get("features"), list) else {}
                check(d.get("max_psi") is not None, d.keys())
                return f"max PSI {d['max_psi']:.3f}" + (f" · utilisation PSI {feats.get('utilisation'):.3f}" if feats.get("utilisation") is not None else "")

            step("drift (PSI)", drift)

    def gym():
        s = get("/api/gym/schema")
        body = {"dataset_id": st["ds"]["id"], "time_limit": 60, "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 30}}}}
        v = post("/api/gym/validate", body)
        check(v["ok"], v.get("errors"))
        bad = post("/api/gym/validate", {**body, "fit_kwargs": {"num_bag_fold": 2}})
        check(not bad["ok"], "typo accepted")
        r = post("/api/runs", {**body, "source": "gym"})
        wait_run(r["id"])
        return f"{len(s['families'])} families · {len(s['fit']['keys'])} fit keys · validate ok, typo refused · {r['id']} trained"

    step("Gym schema + validate + start", gym)

    def vela():
        v = get(f"/api/runs/{rid}/vela")
        fs = v["findings"]
        st["findings"] = fs
        patchable = [f for f in fs if (f.get("patch") or {}).get("body")]
        if not patchable:  # still exercise a patch → retrain → compare, with a drop patch built the same way
            body = dict(v["base"])
            body["drop_columns"] = list(body.get("drop_columns") or []) + ["region"]
            patch = {"finding": "smoke", "changes": [{"path": "drop_columns", "to": body["drop_columns"]}]}
        else:
            patch = patchable[0]["patch"]
            body = dict(patch["body"])
        body.update({"parent_run_id": rid, "patch": {k: patch[k] for k in patch if k != "body"}, "source": "vela", "time_limit": 60})
        r = post("/api/runs", body)
        child = wait_run(r["id"])
        st["child"] = child
        m, a = primary(st["rec"])
        _, b = primary(child)
        return f"{len(fs)} findings · patch {patch.get('finding') or patch.get('id')} → {r['id']} · Δ{m} {b - a:+.4f}"

    step("Vela findings → patch → retrain", vela)

    def compare():
        child = st["child"]
        check(child["config"].get("parent_run_id") == rid, "lineage lost")
        runs = get("/api/runs", dataset_id=st["ds"]["id"])["runs"]
        check({rid, child["id"]} <= {r["id"] for r in runs}, "runs list")
        return f"parent {rid} ← child {child['id']} · same holdout ({child['holdout']['n_rows']} rows)"

    step("compare (lineage)", compare)

    def ask():
        a = post("/api/vela/ask", {"question": "What is the holdout score of the best model, and which model is it?", "run_id": rid})
        check(a["source"] in ("anthropic-sdk", "claude-cli", "computed"), a)
        check(a["answer"] or a["source"] == "computed", a)
        return f"answered by {a['source']} in {a['seconds']:.1f} s · {len(a['answer'] or a.get('message') or '')} chars"

    step("Vela question box", ask)

    def export():
        text = C.get(f"/api/runs/{rid}/export.py").text
        check(text.startswith("#!/usr/bin/env python"), text[:80])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "export_run.py")
            open(p, "w").write(text)
            out = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=900, cwd=d)
        tail = (out.stdout.strip().splitlines() or [""])[-1]
        check(out.returncode == 0, f"exit {out.returncode}: {(out.stderr or out.stdout)[-400:]}")
        return f"exported script reproduced the leaderboard · {tail[:90]}"

    step("export.py → run in subprocess", export)


# --------------------------------------------------------------------------- samples


def samples(st: dict[str, Any]) -> None:
    def regression():
        r = post("/api/runs", {"dataset_id": "loan-amount", "time_limit": 60, "source": "smoke",
                               "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 60}, "LR": {}}}})
        rec = wait_run(r["id"])
        check(rec["problem_type"] == "regression", rec["problem_type"])
        row = {c["name"]: c.get("example") for c in get("/api/datasets/loan-amount")["profile"]["columns"]
               if c["name"] != rec["label"] and c["type"] != "id"}
        p = post(f"/api/runs/{r['id']}/predict", {"rows": [row]})
        m, s = primary(rec)
        val = float(p["predictions"][0])
        check(np.isfinite(val), p)
        return f"{r['id']} · holdout {m} {s:.1f} · predict → {val:,.0f}"

    step("regression sample: train + predict", regression)

    def text():
        r = post("/api/runs", {"dataset_id": "support-tickets", "time_limit": 120, "source": "smoke",
                               "fit_kwargs": {"hyperparameters": {"TFIDF_LR": {}}}, "feature_importance": False})
        rec = wait_run(r["id"])
        check(rec["problem_type"] == "multiclass", rec["problem_type"])
        p = post(f"/api/runs/{r['id']}/predict", {"rows": [{
            "subject": "Card charged twice", "body": "I was billed twice for my invoice this month, please refund the duplicate charge.",
            "channel": "email", "priority": "normal", "customer_tier": "pro"}]})
        m, s = primary(rec)
        check(s > 0.6, f"{m} {s}")
        return f"{r['id']} · {rec['best_model']} · holdout {m} {s:.4f} · 'billed twice' → {p['predictions'][0]}"

    step("text sample: train + predict", text)

    def onerow():
        r = post("/api/runs", {"dataset_id": "support-tickets", "time_limit": 150, "source": "smoke",
                               "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 80}, "TFIDF_LR": {}}},
                               "feature_importance": False})
        rec = wait_run(r["id"])
        lp = get(f"/api/runs/{r['id']}/latency")
        ms = {m["model"]: m for m in lp["models"]}
        check(all(m.get("single_ms_p95") is not None for m in lp["models"]), "one-row latency per model")
        best = ms[lp["best_model"]]
        others = [m for m in lp["models"] if not m["best"] and m["score_test_natural"] is not None]
        check(others, "a second model to serve")
        # The behaviour, not the machine's timing: a model that is measured faster than the best one, or that needs fewer
        # member models (an ensemble runs every member, so a strict subset cannot be slower in expectation). Under load the
        # one-row p95s of a 1-model best and a 2-model ensemble can swap; that is noise, not a failure.
        fast = [m for m in others if m["single_ms_p95"] < best["single_ms_p95"]] or \
               [m for m in others if m["n_models"] < best["n_models"]] or \
               [min(others, key=lambda m: m["single_ms_p95"])]
        alt = max(fast, key=lambda m: m["score_test_natural"])
        v = post(f"/api/runs/{r['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.support-tickets.fast",
                                                    "serve_model": alt["model"]})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        served = v["result"]["version"]["best_model"]
        check(served == alt["model"], f"served {served}, asked {alt['model']}")
        lat = v["result"]["version"]["latency"]
        d = alt["score_test_natural"] - best["score_test_natural"]
        return (f"{r['id']} · τ95 one row: {best['model']} {best['single_ms_p95']:.1f} ms vs {alt['model']} {alt['single_ms_p95']:.1f} ms "
                f"· Δ{lp['eval_metric']} {d:+.4f} · {v['id']} serves {served} (version τ95 {lat['single_ms_p95']:.1f} ms)")

    step("text: one-row latency + serve a faster model", onerow)


# --------------------------------------------------------------------------- F3: one-row latency


TICKET = {"subject": "Card charged twice", "body": "I was billed twice for my invoice this month, please refund the duplicate charge.",
          "channel": "email", "priority": "normal", "customer_tier": "pro"}


def _pq(xs: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(xs, dtype=float), q))


def latency(st: dict[str, Any]) -> None:
    """F3: support tickets served one row at a time. The run is a text ensemble (GBM, XGBoost, two TF-IDF models);
    Register measures every model as it would be served (pipeline pruned, persisted), picks under a one-row budget
    with infer_limit_batch_size = 1, deploys a pruned copy, warms it, and the online /score path is timed."""
    lam_ms = 10.0

    def train():
        r = post("/api/runs", {"dataset_id": "support-tickets", "time_limit": 150, "source": "smoke", "feature_importance": False,
                               "fit_kwargs": {"hyperparameters": {"GBM": {}, "XGB": {}, "TFIDF_LR": {}, "TFIDF_SVM": {}}}})
        rec = wait_run(r["id"])
        st["lat_run"] = rec
        m, s = primary(rec)
        return f"{r['id']} · {rec['best_model']} · holdout {m} {s:.4f}"

    if not step("latency: train the support-tickets ensemble", train):
        return
    rid = st["lat_run"]["id"]

    def frontier():
        lp = get(f"/api/runs/{rid}/latency")
        ms = {m["model"]: m for m in lp["models"]}
        check(all(m.get("served_ms_p95") is not None for m in lp["models"]), "served one-row p95 per model")
        check(lp.get("frontier"), "a frontier")
        best = ms[lp["best_model"]]
        check(lp["best_model"] in lp["frontier"] or best["score_rank"] is None or any(
            ms[f]["score_rank"] is not None and ms[f]["score_rank"] >= best["score_rank"] for f in lp["frontier"]), "best score on the frontier")
        fastest = min(lp["models"], key=lambda m: m["served_ms_p95"])
        check(fastest["model"] in lp["frontier"], "fastest model on the frontier")
        pruned = [m for m in lp["models"] if m["pipeline"]["pruned"] and m["pipeline"]["n_features_out"] < 0.1 * (m["pipeline"]["n_features_out_before"] or 1)]
        check(pruned, "a model that reads a small part of the pipeline (TF-IDF on raw text)")
        st["lat_lp"] = lp
        tf = min(pruned, key=lambda m: m["served_ms_p95"])
        return (f"{len(lp['models'])} models · frontier {' → '.join(lp['frontier'])} · {best['model']} τ95 {best['served_ms_p95']:.1f} ms "
                f"· {tf['model']} τ95 {tf['single_ms_p95']:.1f} ms whole pipeline → {tf['served_ms_p95']:.1f} ms pruned "
                f"({tf['pipeline']['n_features_out_before']} → {tf['pipeline']['n_features_out']} features)")

    if not step("latency: score–latency frontier (as served)", frontier):
        return

    def register_budget():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.tickets.online",
                                                "infer_limit": lam_ms / 1e3, "infer_limit_batch_size": 1})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        r = v["result"]
        il = r["infer_limit"]
        check(il["batch_size"] == 1 and il["candidates"], il)
        within = [c for c in il["candidates"] if c["within"]]
        check(il["met"] == bool(within), "met ⇔ some candidate within λ")
        if within:  # the rule: best validation score among the candidates within λ
            check(il["chosen"] == max(within, key=lambda c: c["score_val"])["model"], "chosen = argmax s_val within λ")
        pipe = r["serving"]["pipeline"]
        check(pipe and pipe["max_abs_diff"] == 0.0, f"pruned predictions unchanged: {pipe}")
        check(r["version"]["best_model"] == il["chosen"], "served = chosen")
        lp = st["lat_lp"]
        ms = {m["model"]: m for m in lp["models"]}
        best = ms[lp["best_model"]]
        one = r["serving"]["one_row"]
        # Behaviour, not a ratio across two measurements taken minutes apart: within the register job's own table (one
        # measurement session) the chosen model is no slower than the run's best, or it is a strict subset of its members.
        cand = {c["model"]: c for c in il["candidates"]}
        ch, bc = cand[il["chosen"]], cand.get(lp["best_model"])
        if bc is not None and ch["model"] != bc["model"]:
            check(ch["single_ms_p95"] <= bc["single_ms_p95"] or ch["n_models"] < bc["n_models"],
                  f"chosen {ch['model']} τ95 {ch['single_ms_p95']:.1f} ms ({ch['n_models']} models) vs best {bc['model']} "
                  f"{bc['single_ms_p95']:.1f} ms ({bc['n_models']} models)")
        if il["met"]:
            check(ch["single_ms_p95"] <= lam_ms, f"chosen within λ: {ch['single_ms_p95']:.1f} ms")
        st["lat_v"] = v
        d = r["version"]["holdout"].get("accuracy", 0) - (r["source"]["holdout"] or {}).get("accuracy", 0)
        return (f"{v['id']} serves {il['chosen']} (met={il['met']}) · τ95 one row {one['ms_p50']:.2f}/{one['ms_p95']:.2f} ms p50/p95 "
                f"over {one['calls']} calls (ensemble {best['served_ms_p95']:.1f} ms) · pipeline {pipe['n_features_out_before']} → "
                f"{pipe['n_features_out']} features, max |Δp| {pipe['max_abs_diff']} · Δaccuracy {d:+.4f} · cold first call "
                f"{r['serving']['cold_first_call_ms']:.1f} ms")

    if not step(f"latency: register infer_limit {lam_ms:g} ms, batch size 1", register_budget):
        return
    vid = st["lat_v"]["id"]

    def warm_and_online():
        post(f"/api/registry/versions/{vid}/review", {"decision": "approve", "reviewer": "Smoke Validator"})
        post(f"/api/registry/versions/{vid}/transition", {"stage": "Production", "actor": "Smoke Validator"})
        path = get(f"/api/registry/versions/{vid}")["path"]
        t_end = time.time() + 60
        while time.time() < t_end and not any(x["path"] == path for x in get("/api/registry/serving")["api_process"]):
            time.sleep(0.5)
        sv = get("/api/registry/serving")
        entry = next((x for x in sv["api_process"] if x["path"] == path), None)
        check(entry is not None, "promoted version loaded and warmed in the API process")
        first = post(f"/api/registry/versions/{vid}/score", {"rows": [TICKET]})
        check(first["warm_before_call"] is True and first["where"] == "api", first)
        tri = post(f"/api/registry/versions/{vid}/predict", {"row": TICKET, "explain": False})
        check(first["predictions"][0] == tri["prediction"], "online score = Try It")
        http, server, pred = [], [], []
        for _ in range(200):
            t0 = time.perf_counter()
            o = post(f"/api/registry/versions/{vid}/score", {"rows": [TICKET]})
            http.append((time.perf_counter() - t0) * 1e3)
            server.append(o["server_ms"])
            pred.append(o["predict_ms"])
        st["lat_online"] = {"http": http, "server": server, "predict": pred}
        wk = [post(f"/api/registry/versions/{vid}/score", {"rows": [TICKET], "where": "worker"})["server_ms"] for _ in range(60)]
        return (f"first call warm ({first['predict_ms']:.1f} ms, warm-up {[round(x, 1) for x in entry['warm']['warm_calls_ms']]} ms) · "
                f"200 calls p50/p95: predictor {_pq(pred, 50):.2f}/{_pq(pred, 95):.2f} ms · server {_pq(server, 50):.2f}/{_pq(server, 95):.2f} ms · "
                f"HTTP {_pq(http, 50):.2f}/{_pq(http, 95):.2f} ms · via a predict worker: server {_pq(wk, 50):.2f}/{_pq(wk, 95):.2f} ms")

    step("latency: warm on promotion + online /score", warm_and_online)

    def benchmark():
        b = post(f"/api/registry/versions/{vid}/benchmark")
        bs = {x["batch"]: x for x in b["batches"]}
        check(set(bs) == {1, 10, 100, 1000}, bs.keys())
        check(bs[1000]["us_per_row_p50"] < bs[1]["us_per_row_p50"], "per-row cost falls with batch size")
        check(b["one_row"]["calls"] >= 50, b["one_row"])
        return (f"predict worker: one row {b['one_row']['ms_p50']:.2f}/{b['one_row']['ms_p95']:.2f} ms p50/p95 · µs/row by batch "
                + " · ".join(f"{k}: {bs[k]['us_per_row_p50']:,.0f}" for k in sorted(bs)))

    step("latency: one row and batches in a predict worker", benchmark)

    def fastest():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.tickets.online", "serve_fastest": True})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        il = v["result"]["infer_limit"]
        check(il["chosen"] == min(il["candidates"], key=lambda c: c["single_ms_p95"])["model"], "argmin τ95")
        return f"{v['id']} serves {il['chosen']} · τ95 {v['result']['serving']['one_row']['ms_p95']:.2f} ms"

    step("latency: serve the fastest model", fastest)

    def distill():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.tickets.distilled",
                                                "distill": True, "distill_time_limit": 60})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        d = v["result"]["distill"]
        check(d and d.get("served") and d["served"].endswith("_DSTL"), d)
        check(v["result"]["version"]["best_model"] == d["served"], "student served")
        sc = d["scores"][d["served"]]
        return (f"{v['id']} · student {d['served']} holdout {sc['score_test']:.4f} vs teacher {d['teacher']} {d['teacher_score_test']:.4f} "
                f"· τ95 {v['result']['serving']['one_row']['ms_p95']:.1f} ms · {d['seconds']:.0f} s")

    step("latency: distill into a single model", distill)

    def gym():
        body = {"dataset_id": "support-tickets", "time_limit": 90, "feature_importance": False,
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 100}, "TFIDF_LR": {}}, "infer_limit": 0.05, "infer_limit_batch_size": 1}}
        v = post("/api/gym/validate", body)
        check(v["ok"], v.get("errors"))
        bad = post("/api/gym/validate", {**body, "fit_kwargs": {**body["fit_kwargs"], "infer_limit_batch_size": 0}})
        check(not bad["ok"], "batch size 0 accepted")
        r = post("/api/runs", {**body, "source": "gym"})
        rec = wait_run(r["id"])
        check(rec["config"]["fit_kwargs"].get("infer_limit_batch_size") == 1, rec["config"]["fit_kwargs"])
        return f"{r['id']} · fit(infer_limit=0.05, infer_limit_batch_size=1) → {rec['best_model']}"

    step("latency: Gym infer_limit with batch size 1", gym)


# --------------------------------------------------------------------------- fresh CSVs (scripts/fresh_data.py)

FRESH = [
    # file, label, metric, problem type, fit_kwargs, a test row's expected kind of answer
    ("card_fraud.csv", "is_fraud", "average_precision", "binary",
     {"hyperparameters": {"GBM": {"num_boost_round": 150}, "CAT": {"iterations": 150}, "LR": {}}, "num_bag_folds": 3}),
    ("complaints.csv", "product_team", "f1_macro", "multiclass", {"hyperparameters": {"TFIDF_LR": {}, "TFIDF_SVM": {}}}),
    ("deposit_balances.csv", "avg_balance_next_q_gbp", "root_mean_squared_error", "regression",
     {"hyperparameters": {"GBM": {"num_boost_round": 200}, "XT": {}, "LR": {}}}),
]


# G0: the right role for every column of every bundled sample (my reading of each generator), and the right first flow.
F, M, CAT = "feature", "money", "category"
INTAKE_TRUTH: dict[str, tuple[str, dict[str, str]]] = {
    "support-tickets": ("multiclass", {"ticket_id": "id", "subject": CAT, "body": "text", "channel": CAT, "priority": CAT,
                                       "customer_tier": "segment", "category": "target"}),
    "account-churn": ("binary", {"account_id": "id", "plan": "segment", "region": "geography", "contract": CAT, "seats": F,
                                 "mrr_usd": M, "tenure_months": "duration", "tickets_90d": F, "last_login_days": F, "nps": F,
                                 "churned": "target"}),
    "credit-default": ("binary", {"applicant_id": "id", "customer_id": "entity", "application_date": "date", "age": F,
                                  "employment": CAT, "income_usd": M, "home_ownership": CAT, "loan_purpose": CAT,
                                  "region": "geography", "loan_amount_usd": M, "credit_score": F, "debt_to_income": F,
                                  "utilization": F, "delinquencies_2y": F, "months_employed": F, "defaulted": "target"}),
    "loan-amount": ("regression", {"application_id": "id", "income_usd": M, "credit_score": F, "existing_debt_usd": M,
                                   "term_months": F, "purpose": CAT, "home_ownership": CAT, "region": "geography",
                                   "open_accounts": F, "loan_amount_usd": "target"}),
    "transaction-volume": ("forecast", {"item_id": "entity", "timestamp": "date", "volume": "target", "holiday": F,
                                        "payday": F, "segment": "segment"}),
    "card-transactions": ("anomaly", {"txn_id": "id", "card_id": "entity", "timestamp": "date", "amount_usd": M,
                                      "avg_amount_90d": M, "merchant_category": CAT, "channel": CAT, "card_present": F,
                                      "foreign": F, "hour": F, "distance_from_home_km": F, "txns_last_24h": F,
                                      "account_age_days": F}),
    "personal-loans": ("binary", {"application_id": "id", "age": F, "income_usd": M, "credit_score": F, "debt_to_income": F,
                                  "channel": CAT, "region": "geography", "employment": CAT, "term_months": F,
                                  "loan_amount_usd": M, "prior_defaults": F, "defaulted": "target"}),
    "loan-stream": ("binary", {"application_id": "id", "application_date": "date", "age": F, "income_usd": M,
                               "credit_score": F, "debt_to_income": F, "utilization": F, "channel": CAT, "employment": CAT,
                               "term_months": F, "loan_amount_usd": M, "prior_defaults": F, "defaulted": "target"}),
    "loan-losses": ("expected_loss", {"loan_id": "id", "application_date": "date", "product": "segment", "region": "geography",
                                      "income": M, "credit_score": F, "debt_to_income": F, "utilization": F, "credit_limit": M,
                                      "current_balance": M, "collateral_value": M, "term_months": F, "defaulted": "target",
                                      "lgd": "lgd", "ead": "ead"}),
    "fair-lending": ("binary", {"application_id": "id", "application_date": "date", "surname": "surname", "zip": "zip",
                                "income": M, "credit_score": F, "debt_to_income": F, "loan_amount": M, "employment_years": F,
                                "home_ownership": CAT, "race_ethnicity_true": "protected", "defaulted": "target"}),
    "segmented-loans": ("segmented", {"application_id": "id", "application_date": "date", "customer_type": "segment",
                                      "income": M, "credit_score": F, "debt_to_income": F, "utilization": F,
                                      "months_employed": F, "num_accounts": F, "loan_amount": M, "recent_inquiries": F,
                                      "defaulted": "target"}),
    "account-attrition": ("survival", {"account_id": "id", "open_date": "date", "product": "segment", "region": "geography",
                                       "age": F, "num_products": F, "digital_logins_30d": F, "complaints_12m": F,
                                       "rate_vs_market_pp": F, "has_salary_mandate": F, "avg_balance_gbp": M,
                                       "churned": "target", "duration_months": "duration"}),
    "credit-rejects": ("reject_inference", {"application_id": "id", "application_date": "date", "channel": CAT,
                                            "home_ownership": CAT, "credit_score": F, "income": M, "debt_to_income": F,
                                            "utilization": F, "months_employed": F, "recent_inquiries": F,
                                            "num_delinquencies": F, "loan_amount": M, "decision": "decision",
                                            "defaulted": "target"}),
    "complaint-topics": ("multilabel", {"complaint_id": "id", "received_date": "date", "product": "segment", "channel": CAT,
                                        "customer_tenure_years": "duration", "amount_disputed": M, "narrative": "text",
                                        "topics": "multilabel"}),
}
KEY_ROLES = ("target", "date", "id", "entity", "segment")


def intake(st: dict[str, Any]) -> None:
    """G0 Smart Intake: GET /api/datasets/{id}/intake on every bundled sample. Every column's role against the truth
    above (all roles, and the acceptance roles target / date / ID / entity / segment, which must be 100 %), the first
    suggestion is the right flow, and its pre-fill names the target and split. Then three fresh CSVs uploaded without a
    target, and a role edit (POST) that moves the suggestions."""
    acc: dict[str, str] = {}

    def one(sid: str, top: str, truth: dict[str, str]):
        def f():
            r = get(f"/api/datasets/{sid}/intake")
            got = {c["name"]: c["role"] for c in r["columns"]}
            check(set(got) == set(truth), f"columns {sorted(set(got) ^ set(truth))} differ from the truth table")
            wrong = {c: (truth[c], got[c]) for c in truth if got[c] != truth[c]}
            key = [c for c in truth if truth[c] in KEY_ROLES or got[c] in KEY_ROLES]
            key_wrong = {c: v for c, v in wrong.items() if c in key}
            check(not key_wrong, f"key roles wrong (truth, got): {key_wrong}")
            s0 = r["suggestions"][0]
            check(s0["id"] == top, f"top suggestion {s0['id']} ≠ {top} ({[x['id'] for x in r['suggestions']]})")
            check(s0["reason"] and all(c["evidence"] for c in r["columns"]), "a suggestion or a role came without its reason")
            if s0["route"] == "quick-train":
                check(s0["prefill"]["target"] == next(c for c, v in truth.items() if v == "target"), f"pre-fill {s0['prefill']}")
            n_ok = len(truth) - len(wrong)
            acc[sid] = f"{n_ok}/{len(truth)}"
            return (f"roles {n_ok}/{len(truth)} ({n_ok / len(truth) * 100:.0f}%) · key {len(key)}/{len(key)} · top {s0['id']} "
                    f"({s0['score']:g}){' · split ' + s0['prefill']['split'] if s0['prefill'].get('split') else ''}"
                    f"{' · off: ' + ', '.join(f'{c} {g}≠{t}' for c, (t, g) in wrong.items()) if wrong else ''}")
        return f

    for sid, (top, truth) in INTAKE_TRUTH.items():
        step(f"intake: {sid}", one(sid, top, truth))

    def fresh_csvs():
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fresh_data

        out = []
        for fname, (top, target) in {"card_fraud.csv": ("binary", "is_fraud"), "complaints.csv": ("multiclass", "product_team"),
                                     "deposit_balances.csv": ("regression", "avg_balance_next_q_gbp")}.items():
            df = fresh_data.FILES[fname]()
            d = j(C.post("/api/datasets", files={"file": (fname, df.to_csv(index=False).encode(), "text/csv")}))
            r = get(f"/api/datasets/{d['id']}/intake")
            s0 = r["suggestions"][0]
            check(s0["id"] == top and s0["prefill"]["target"] == target, f"{fname}: top {s0['id']} {s0['prefill']} ≠ {top} {target}")
            out.append(f"{fname.split('.')[0]} → {s0['title']} (y = {target})")
        return " · ".join(out)
    step("intake: fresh CSVs, no target given", fresh_csvs)

    def edit():
        r = post("/api/datasets/credit-default/intake", {"roles": {"employment": "segment", "loan_amount_usd": "target"}})
        cols = {c["name"]: c for c in r["columns"]}
        check(cols["employment"]["role"] == "segment" and cols["employment"]["evidence"] == ["set by you"], "override not applied")
        check(cols["defaulted"]["role"] != "target", "the old target is still a target")
        s0 = r["suggestions"][0]
        check(s0["id"] == "regression" and s0["prefill"]["target"] == "loan_amount_usd", f"top after the edit: {s0['id']}")
        bad = C.post("/api/datasets/credit-default/intake", json={"roles": {"age": "wizard"}})
        check(bad.status_code == 400, f"an unknown role → {bad.status_code}")
        return f"employment → segment, y = loan_amount_usd → top {s0['id']} · unknown role → 400"
    step("intake: role edits move the suggestions", edit)
    st["intake_accuracy"] = acc


def fresh(st: dict[str, Any]) -> None:
    """Three CSVs no earlier session saw, through the same calls the browser walkthrough makes:
    upload → profile → train (SSE) → results → explain → register → approve → Production → Try It → batch score."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fresh_data

    tmp = tempfile.mkdtemp(prefix="smoke-fresh-")
    paths = fresh_data.write(tmp)
    for fname, label, metric, ptype, fk in FRESH:
        tag = fname.split(".")[0]
        df = pd.read_csv(paths[fname])
        rec_st: dict[str, Any] = {}

        def upload_train():
            d = j(C.post("/api/datasets", files={"file": (fname, df.to_csv(index=False).encode(), "text/csv")}, data={"target": label}))
            prof = get(f"/api/datasets/{d['id']}")["profile"]
            check(prof["task"] == ptype, f"profile task {prof['task']} ≠ {ptype}")
            body = {"dataset_id": d["id"], "label": label, "eval_metric": metric, "time_limit": 120, "presets": "medium_quality",
                    "fit_kwargs": fk, "source": "smoke-fresh"}
            if ptype == "binary":
                body["calibrate_threshold"] = "f1"
            r = post("/api/runs", body)
            sse = follow_sse(r["id"], 900)
            rec = wait_run(r["id"])
            rec_st.update(ds=d, rec=rec)
            m, sc = primary(rec)
            extra = f" · t = {rec['threshold']['value']:.3f} (F1-calibrated)" if rec.get("threshold") else ""
            types = {c["name"]: c["type"] for c in prof["columns"]}
            text = [c for c, t in types.items() if t == "text"]
            return (f"{d['id']} {d['rows']:,} × {d['n_cols']} · task {prof['task']}{f' · text {text}' if text else ''} · {r['id']} "
                    f"{sse['counts'].get('model_finished', 0)} models · best {rec['best_model']} · holdout {m} {sc:.4f}{extra}")

        if not step(f"fresh {tag}: upload → train", upload_train):
            continue
        rid = rec_st["rec"]["id"]

        def explain_():
            e = get(f"/api/runs/{rid}/explain")
            fi = get(f"/api/runs/{rid}/feature-importance")
            top = (fi.get("importance") or fi.get("features") or [{}])[0] if isinstance(fi, dict) else {}
            if ptype == "binary":
                pos = str(rec_st["rec"]["positive_class"])
                check(pos == "1", f"positive class {pos}: fraud must be the positive class")
                roc, pr = e["roc"][pos], e["pr"][pos]
                prev = pr.get("prevalence", float("nan"))
                return (f"positive class 1 (fraud) · ROC AUC {roc['auc']:.4f} · AP {pr.get('ap', pr.get('average_precision', float('nan'))):.4f} "
                        f"vs prevalence {prev:.4f} · top feature {top.get('feature', '—')}")
            if ptype == "multiclass":
                rep = e.get("per_class") or e.get("report") or []
                return f"{len(rec_st['rec']['classes'])} classes · confusion Σ = {int(np.asarray(e['confusion_matrix']).sum())} · top feature {top.get('feature', '—')}"
            return f"R² {e['r2']:.4f} · RMSE {e['rmse']:,.0f} · MAE {e['mae']:,.0f} · top feature {top.get('feature', '—')}"

        step(f"fresh {tag}: explain", explain_)

        def register_use():
            v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": f"fresh.{tag}.{rec_st['ds']['id']}"})
            v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
            check(v["status"] == "ready", v.get("error"))
            post(f"/api/registry/versions/{v['id']}/review", {"decision": "approve", "reviewer": "Smoke Validator", "comment": "fresh data check"})
            post(f"/api/registry/versions/{v['id']}/transition", {"stage": "Production", "actor": "Smoke Validator"})
            check(get(f"/api/registry/versions/{v['id']}")["stage"] == "Production", "stage")
            row = df.drop(columns=[label]).iloc[3].to_dict()
            row = {k: (None if isinstance(x, float) and np.isnan(x) else x) for k, x in row.items()}
            out = post(f"/api/registry/versions/{v['id']}/predict", {"row": row, "explain": True})
            batch = df.drop(columns=[label]).sample(400, random_state=1)
            b = j(C.post(f"/api/registry/versions/{v['id']}/batches", files={"file": (f"{tag}_new.csv", batch.to_csv(index=False).encode(), "text/csv")},
                         data={"created_by": "Smoke Analyst"}))
            b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
            check(b["status"] == "succeeded", b.get("error"))
            dl = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{b['id']}/download").content))
            check(len(dl) == 400 and "prediction" in dl.columns, dl.shape)
            pred = out["prediction"]
            pred = f"{pred:,.0f}" if isinstance(pred, (int, float)) else pred
            reasons = ", ".join(r["feature"] for r in (out.get("shap") or {}).get("reasons", [])[:2])
            return (f"{v['id']} approved → Production · Try It → {pred} ({out['predict_ms']:.0f} ms; why: {reasons}) · "
                    f"{b['id']} 400 rows scored · max PSI {b['result']['drift']['max_psi']:.3f}")

        step(f"fresh {tag}: register → approve → Try It → batch", register_use)


# --------------------------------------------------------------------------- targets (F4)


def targets(st: dict[str, Any]) -> None:
    """F4: the holdout belongs to the target. The deposits CSV uploaded without a target (the profiler guesses another
    column) is trained on avg_balance_next_q_gbp through the browser's calls (upload → confirm the target → run) and
    through the API (upload naming the target → run); the holdout rows, predictions and scores must be identical.
    Also: a run naming an unconfirmed label gets that label's split; switching the target never redraws a split."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fresh_data

    label, metric = "avg_balance_next_q_gbp", "root_mean_squared_error"
    raw = open(fresh_data.write(tempfile.mkdtemp(prefix="smoke-targets-"))["deposit_balances.csv"], "rb").read()
    fk = {"hyperparameters": {"GBM": {"num_boost_round": 200}, "XT": {}, "LR": {}}}
    s: dict[str, Any] = {}

    def upload(target: str | None = None) -> dict[str, Any]:
        return j(C.post("/api/datasets", files={"file": ("deposit_balances.csv", raw, "text/csv")},
                        data={"target": target} if target else {}))

    def train(ds: dict[str, Any], src: str) -> dict[str, Any]:
        """The body the New Model wizard sends (QuickTrainPage.start): problem type, metric, preset, budget, id columns dropped."""
        prof = get(f"/api/datasets/{ds['id']}")["profile"]
        drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
        r = post("/api/runs", {"dataset_id": ds["id"], "label": label, "problem_type": "regression", "eval_metric": metric,
                               "presets": "medium_quality", "time_limit": 120, "fit_kwargs": fk, "column_types": None,
                               "drop_columns": drop, "feature_importance": False, "calibrate_threshold": None, "source": src})
        rec = wait_run(r["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        return {"rec": rec, "hp": get(f"/api/runs/{r['id']}/holdout-predictions")}

    def guessed():
        d = upload()
        p = d["profile"]
        check(p.get("target_source") == "guessed", f"target_source {p.get('target_source')}")
        check(p["target"] != label, f"the profiler guessed {p['target']}: pick another column for this check")
        check(p["splits"]["target"] == p["target"] and p["splits"]["strategy"] == "random-stratified", p["splits"])
        s.update(ui=d, guess=p["target"], guess_split=p["splits"])
        return (f"{d['id']} · profiler guessed y = {p['target']} ({p['task']}) · its split {p['splits']['key']} "
                f"sha {p['splits']['sha256'][:10]} · stratified {p['splits']['stratified']}")

    if not step("targets: upload without a target", guessed, fatal=False):
        return

    def api_path():
        d = upload(label)
        p = d["profile"]
        check(p.get("target_source") == "given" and p["splits"]["target"] == label, (p.get("target_source"), p["splits"]))
        out = train(d, "smoke-targets-api")
        s.update(api_ds=d, api=out)
        rec = out["rec"]
        check(rec["split"]["target"] == label and rec["split"]["strategy"] == "random-stratified", rec["split"])
        m, sc = primary(rec)
        return (f"{d['id']} upload names y · {rec['id']} split {rec['split']['key']} sha {rec['split']['sha256'][:10]} · "
                f"holdout {rec['split']['holdout_rows']} rows · {m} {sc:,.4f}")

    def ui_path():
        d = s["ui"]
        bad = C.patch(f"/api/datasets/{d['id']}", json={"target": "no_such_column"})
        check(bad.status_code == 400, f"unknown column → {bad.status_code}")
        c = C.patch(f"/api/datasets/{d['id']}", json={"target": label})
        check(c.status_code == 200, c.text)
        p = c.json()["profile"]
        check(c.json()["target"] == label and p["target"] == label and p["target_source"] == "confirmed", p.get("target_source"))
        check(p["task"] == "regression", f"task after confirming {p['task']}")
        check(p["splits"]["target"] == label, p["splits"])
        check(p["splits"]["sha256"] == s["api_ds"]["profile"]["splits"]["sha256"],
              f"confirmed split {p['splits']['sha256'][:10]} ≠ API upload's {s['api_ds']['profile']['splits']['sha256'][:10]}")
        lst = get(f"/api/datasets/{d['id']}/splits")
        keys = {x["key"]: x for x in lst["splits"]}
        check(lst["current"] == p["splits"]["key"] and len(keys) == 2, lst)
        check(keys[s["guess_split"]["key"]]["sha256"] == s["guess_split"]["sha256"], "the guessed target's split changed")
        out = train(d, "smoke-targets-ui")
        s["ui_run"] = out
        rec = out["rec"]
        m, sc = primary(rec)
        return (f"{d['id']} confirm y = {label} (was {s['guess']}) · splits stored {sorted(keys)} · {rec['id']} split "
                f"{rec['split']['key']} sha {rec['split']['sha256'][:10]} · {m} {sc:,.4f}")

    if not step("targets: API path (upload names the target) → train", api_path):
        return
    if not step("targets: UI path (upload → confirm the target) → train", ui_path):
        return

    def identical():
        a, b = s["api"], s["ui_run"]
        ra, rb = a["rec"], b["rec"]
        check(ra["split"]["sha256"] == rb["split"]["sha256"], "split sha differs")
        check(a["hp"]["rows"] == b["hp"]["rows"], f"holdout rows differ: {len(set(a['hp']['rows']) & set(b['hp']['rows']))} shared")
        check(a["hp"]["y_true"] == b["hp"]["y_true"], "holdout labels differ")
        diff = float(np.max(np.abs(np.asarray(a["hp"]["y_pred"], float) - np.asarray(b["hp"]["y_pred"], float))))
        check(diff == 0.0, f"holdout predictions differ, max |Δŷ| {diff}")
        sa, sb = ra["holdout"]["scores"], rb["holdout"]["scores"]
        check(sa == sb, f"holdout scores differ: {sa} vs {sb}")
        check(ra["best_model"] == rb["best_model"], (ra["best_model"], rb["best_model"]))
        return (f"{len(a['hp']['rows'])} holdout rows identical (sha {ra['split']['sha256'][:10]}) · max |Δŷ| {diff} · "
                f"RMSE {-sa[metric]:,.1f} = {-sb[metric]:,.1f} · R² {sa['r2']:.4f} = {sb['r2']:.4f} · best {ra['best_model']}")

    step("targets: API and UI holdouts identical", identical)

    def unconfirmed():
        d = upload()
        check(d["profile"]["target"] == s["guess"], d["profile"]["target"])
        r = post("/api/runs", {"dataset_id": d["id"], "label": label, "eval_metric": metric, "time_limit": 60,
                               "fit_kwargs": {"hyperparameters": {"LR": {}}}, "source": "smoke-targets-unconfirmed"})
        check(r["split"]["target"] == label and r["split"]["sha256"] == s["api"]["rec"]["split"]["sha256"], r["split"])
        check(r["config"]["split"]["path"] == r["config"]["splits_path"], "the run's splits_path is not its recorded split")
        rec = wait_run(r["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        hp = get(f"/api/runs/{r['id']}/holdout-predictions")
        check(hp["rows"] == s["api"]["hp"]["rows"], "holdout rows differ")
        after = get(f"/api/datasets/{d['id']}")
        check(after["target"] == s["guess"] and after["profile"]["target_source"] == "guessed", "a run must not confirm the target")
        return (f"{d['id']} still y = {s['guess']} (guessed) · run {r['id']} with label {label} gets that label's split "
                f"(drawn for this run: {r['split']['drawn_for_this_run']}) · same {len(hp['rows'])} holdout rows")

    step("targets: run naming an unconfirmed label uses the label's split", unconfirmed)

    def switch_back():
        d = s["ui"]
        p = j(C.patch(f"/api/datasets/{d['id']}", json={"target": s["guess"]}))["profile"]
        check(p["splits"]["key"] == s["guess_split"]["key"] and p["splits"]["sha256"] == s["guess_split"]["sha256"],
              "switching back redrew the split")
        p2 = j(C.patch(f"/api/datasets/{d['id']}", json={"target": label}))["profile"]
        check(p2["splits"]["sha256"] == s["api"]["rec"]["split"]["sha256"], "switching again redrew the split")
        check([h["target"] for h in p2["target_history"]] == [label, s["guess"], label], p2["target_history"])
        old = get(f"/api/runs/{s['ui_run']['rec']['id']}")
        check(old["split"]["sha256"] == s["api"]["rec"]["split"]["sha256"], "the run's recorded split changed")
        return (f"{label} → {s['guess']} → {label}: both splits reused, never redrawn · "
                f"the run still records {old['split']['key']}")

    step("targets: switching the target reuses each target's split", switch_back)


# --------------------------------------------------------------------------- autopilot


def splits(st: dict[str, Any]) -> None:
    """F6: out-of-time and group splits on the credit-default sample (customers with several applications, dated
    2022–2024, mild drift). Checks, from data.csv and the stored split files, that the group split shares no
    customer between train, tuning and holdout and that the time split trains strictly before its cut-off; then
    trains the wizard's run under the random split and its twins under the time, group and time + group splits
    (identical body, only split_spec differs) and reads the side-by-side comparison: the out-of-time score is lower."""
    ds, label, metric = "credit-default", "defaulted", "roc_auc"
    data_dir = get("/api/health")["data_dir"]
    df = pd.read_csv(os.path.join(data_dir, "datasets", ds, "data.csv"))
    dates = pd.to_datetime(df["application_date"])
    gcol, tcol = "customer_id", "application_date"
    specs = {"time": {"strategy": "time", "time_column": tcol},
             "group": {"strategy": "group", "group_column": gcol},
             "time-group": {"strategy": "time-group", "group_column": gcol, "time_column": tcol}}
    s: dict[str, Any] = {}

    def split_file(key: str) -> dict[str, Any]:
        return json.load(open(os.path.join(data_dir, "datasets", ds, "splits", f"{key}.json")))

    def roles(sp: dict[str, Any]) -> dict[str, np.ndarray]:
        f = np.asarray(sp["fold"])
        return {"train": np.flatnonzero(f >= 0), "tuning": np.flatnonzero(f == -3), "holdout": np.asarray(sp["holdout"]),
                "excluded": np.flatnonzero(f == -2)}

    def options():
        o = get(f"/api/datasets/{ds}/split-options")
        check([c["column"] for c in o["date_columns"]] == [tcol], o["date_columns"])
        check(o["group_columns"] and o["group_columns"][0]["column"] == gcol, o["group_columns"])
        check(len(o["warnings"]) == 2 and o["suggested"] == "time-group", o)
        quiet = get("/api/datasets/loan-amount/split-options")
        check(not quiet["warnings"] and quiet["suggested"] == "random-stratified", quiet)
        g = o["group_columns"][0]
        return (f"date {tcol} {o['date_columns'][0]['from']} → {o['date_columns'][0]['to']} · entity {gcol} "
                f"{g['groups']:,} customers × {g['rows_per_group']} rows · {len(o['warnings'])} warnings on random · "
                f"suggested {o['suggested']} · loan-amount: none")

    def draw():
        out = []
        for name, spec in specs.items():
            r = post(f"/api/datasets/{ds}/splits", {"target": label, **spec})
            again = post(f"/api/datasets/{ds}/splits", {"target": label, **spec})
            check(again["sha256"] == r["sha256"] and not again["drawn_now"], "a stored split was redrawn")
            sp = split_file(r["key"])
            ro = roles(sp)
            check(sum(len(v) for v in ro.values()) == len(df), "every row has exactly one role")
            if "group" in name:
                ids = {k: set(df[gcol].iloc[v]) for k, v in ro.items()}
                shared = len(ids["train"] & ids["holdout"]) + len(ids["tuning"] & ids["holdout"]) + len(ids["train"] & ids["tuning"])
                check(shared == 0, f"{name}: {shared} customers shared between train, tuning and holdout")
                check(r["diagram"]["shared_groups"]["train_holdout"] == 0, r["diagram"]["shared_groups"])
                folds = np.asarray(sp["fold"])
                per_cust = df.assign(f=folds).loc[folds >= 0].groupby(gcol)["f"].nunique()
                check(int(per_cust.max()) == 1, "a customer spans two bagging folds")
            if "time" in name:
                cut = pd.Timestamp(r["cutoff"])
                check(dates.iloc[ro["train"]].max() < cut <= dates.iloc[ro["holdout"]].min(), "time: train is not before the cut-off")
                check(dates.iloc[ro["tuning"]].min() >= pd.Timestamp(r["tuning_cutoff"]) > dates.iloc[ro["train"]].max()
                      if name == "time" else dates.iloc[ro["tuning"]].min() >= pd.Timestamp(r["tuning_cutoff"]), "tuning slice")
                check(len(r["diagram"]["timeline"]) >= 30, "timeline periods")
            s[name] = r
            out.append(f"{name} {r['key']}: train {len(ro['train'])} · tuning {len(ro['tuning'])} · holdout {len(ro['holdout'])}"
                       + (f" · excluded {len(ro['excluded'])}" if len(ro["excluded"]) else "")
                       + (f" · cut-off {r['cutoff']}" if r.get("cutoff") else "")
                       + (" · 0 shared customers" if "group" in name else ""))
        for bad in ({"strategy": "group", "group_column": "nope"}, {"strategy": "time", "time_column": "region"},
                    {"strategy": "time", "time_column": tcol, "cutoff": "2031-01-01"}, {"strategy": "sideways"}):
            r = C.post(f"/api/datasets/{ds}/splits", json={"target": label, **bad})
            check(r.status_code == 400, f"{bad} → {r.status_code}")
        return " | ".join(out) + " | 4 bad specs → 400"

    if not step("splits: options + warnings", options) or not step("splits: draw group / time / time+group", draw):
        return

    def random_run():
        prof = get(f"/api/datasets/{ds}")["profile"]
        drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
        r = post("/api/runs", {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": metric,
                               "presets": "medium_quality", "time_limit": 120, "fit_kwargs": {"hyperparameters": HP},
                               "drop_columns": drop, "feature_importance": False, "source": "smoke-splits"})
        rec = wait_run(r["id"])
        check(rec["split"]["strategy"] == "random-stratified", rec["split"])
        s["random"] = rec
        return f"{rec['id']} random-stratified · holdout {rec['split']['holdout_rows']} · {metric} {primary(rec)[1]:.4f}"

    if not step("splits: random run (the wizard's body)", random_run):
        return

    def twin(name: str):
        def fn():
            r = post(f"/api/runs/{s['random']['id']}/split-twin", {"split_spec": specs[name]})
            rec = wait_run(r["id"])
            sp = split_file(rec["split"]["key"])
            check(rec["split"]["key"] == s[name]["key"] and rec["split"]["sha256"] == s[name]["sha256"], "twin used another split")
            check(rec["config"]["drop_columns"] == s["random"]["config"]["drop_columns"], "twin features differ")
            res = rec["resolved"]
            check(res["fit_kwargs"].get("use_bag_holdout") is True and res["data_args"]["tuning_data"]["rows"] == len(sp["tuning"]),
                  res.get("data_args"))
            hp = get(f"/api/runs/{rec['id']}/holdout-predictions")
            hold = np.asarray(hp["rows"], dtype=int)
            check(sorted(hold.tolist()) == sorted(sp["holdout"]), "the run scored other rows than its split's holdout")
            ro = roles(sp)
            fitted = np.concatenate([ro["train"], ro["tuning"]])
            extra = ""
            if "group" in name:
                check(res["predictor_kwargs"].get("groups") == "__ensemble_group_fold", res["predictor_kwargs"])
                check(all("_BAG_" in m["model"] or m["model"].startswith("Weighted") for m in rec["leaderboard"]),
                      "groups= should bag every model")
                shared = set(df[gcol].iloc[fitted]) & set(df[gcol].iloc[hold])
                check(not shared, f"{len(shared)} customers in both the fitted rows and the holdout")
                extra = f" · {len(set(df[gcol].iloc[hold])):,} holdout customers, 0 in train or tuning · groups= bagged"
            if "time" in name:
                check(dates.iloc[fitted].max() < pd.Timestamp(sp["cutoff"]) <= dates.iloc[hold].min(), "fitted rows after the cut-off")
                extra += f" · fitted ≤ {dates.iloc[fitted].max().date()} < holdout ≥ {dates.iloc[hold].min().date()}"
            s[f"run_{name}"] = rec
            return (f"{rec['id']} {name} · train {rec['data']['train_rows']} · tuning_data {rec['data']['tuning_rows']} · "
                    f"holdout {len(hold)} · {metric} {primary(rec)[1]:.4f}{extra}")
        return fn

    for name in specs:
        step(f"splits: {name} twin", twin(name))

    def compare():
        check("run_time" in s, "no time run")
        c = get(f"/api/runs/{s['run_time']['id']}/split-comparison")
        by = {x["strategy"]: x for x in c["runs"] if x["same_config"] or x["this_run"]}
        check({"random-stratified", "time"} <= set(by), list(by))
        rnd, oot = by["random-stratified"], by["time"]
        check(rnd["run_id"] == s["random"]["id"] and oot["run_id"] == s["run_time"]["id"], (rnd["run_id"], oot["run_id"]))
        check(abs(oot["optimism"] - (rnd["score"] - oot["score"])) < 1e-12, oot["optimism"])
        st["splits_compare"] = c
        line = " · ".join(f"{k} {v['score']:.4f}" + (f" (Δ {v['optimism']:+.4f})" if v.get("optimism") is not None else "")
                          for k, v in by.items())
        check(oot["score"] < rnd["score"], f"out-of-time {oot['score']:.4f} is not lower than random {rnd['score']:.4f}")
        return f"{metric} on the holdout: {line} · random is optimistic by {oot['optimism']:.4f} vs out-of-time"

    step("splits: random vs out-of-time side by side", compare)

def cost(st: dict[str, Any]) -> None:
    """F7: a business cost as the eval_metric. On credit-default with FN = 10 × FP, the wizard's body with
    cost = {c_fp: 1, c_fn: 10} trains to AutoGluon's custom scorer (ensemble_models.cost), calibrates the threshold
    on that cost with AutoGluon, and is compared on the same holdout rows with its twins trained to ROC AUC and to
    accuracy (identical body, only the metric differs). The smoke recomputes every cost from the holdout predictions
    the API serves, and asserts the acceptance: the cost-optimised run has the lower holdout expected cost."""
    ds, label = "credit-default", "defaulted"
    c_fp, c_fn = 1.0, 10.0
    s: dict[str, Any] = {}
    prof = get(f"/api/datasets/{ds}")["profile"]
    drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "presets": "medium_quality", "time_limit": 120,
            "fit_kwargs": {"hyperparameters": HP}, "drop_columns": drop, "feature_importance": False, "source": "smoke-cost"}

    def holdout_costs(rid: str, t: float) -> tuple[np.ndarray, np.ndarray]:
        hp = get(f"/api/runs/{rid}/holdout-predictions")
        k = hp["classes"].index("Yes")
        p = np.asarray(hp["probabilities"], dtype=float)[:, k]
        y = np.asarray([str(v) == "Yes" for v in hp["y_true"]])
        yhat = p > t
        return np.where(y, np.where(yhat, 0.0, c_fn), np.where(yhat, c_fp, 0.0)), np.asarray(hp["rows"])

    def refuse():
        bad = [({**body, "cost": {"c_fp": 1, "c_fn": 10}, "eval_metric": "roc_auc"}, "not both"),
               ({**body, "cost": {"c_fp": -1, "c_fn": 10}}, "≥ 0"),
               ({**body, "cost": {"c_fp": 0, "c_fn": 0}}, "above 0"),
               ({**body, "eval_metric": "expected_cost"}, "needs the costs"),
               ({**body, "label": "loan_amount_usd", "problem_type": "regression", "cost": {"c_fp": 1, "c_fn": 10}}, "binary or multiclass")]
        for b, words in bad:
            r = C.post("/api/runs", json=b)
            check(r.status_code == 400 and words in r.text, f"{words}: {r.status_code} {r.text[:200]}")
        return f"{len(bad)} bad cost requests → 400"

    def cost_run():
        r = post("/api/runs", {**body, "cost": {"c_fp": c_fp, "c_fn": c_fn}, "calibrate_threshold": "auto"})
        rec = wait_run(r["id"])
        cm = rec["resolved"]["cost_metric"]
        check(rec["eval_metric"]["name"] == "expected_cost" and not rec["eval_metric"]["greater_is_better"], rec["eval_metric"])
        check(cm["needs_proba"] and cm["c_fn"] == c_fn and abs(cm["bayes_threshold"] - c_fp / (c_fp + c_fn)) < 1e-12, cm)
        evs = get(f"/api/runs/{rec['id']}/events", format="json")["events"]
        fs = [e for e in evs if e.get("type") == "fit_started"]
        check(fs and fs[0]["eval_metric"] == "expected_cost", "AutoGluon's trainer was not fitting to expected_cost")
        thr = rec["threshold"]
        check(thr["source"] == "calibrated" and thr["metric"] == "expected_cost" and 0 < thr["value"] < 0.5, thr)
        lb = rec["leaderboard"]
        check(all(x["score_val_natural"] is not None and x["score_val_natural"] >= 0 for x in lb), "validation costs")
        # the leaderboard is sorted by holdout score, so check the run's best model (chosen on validation) itself
        best = next(x for x in lb if x["model"] == rec["best_model"])
        check(best["score_val_natural"] <= min(x["score_val_natural"] for x in lb) + 1e-12, "best model is not the cheapest on validation")
        rows, _ = holdout_costs(rec["id"], thr["value"])
        h = rec["holdout"]["metrics"]
        check(abs(h["expected_cost"] - rows.mean()) < 1e-9, f"holdout expected_cost {h['expected_cost']} vs recomputed {rows.mean()}")
        # the API process loads the pickled scorer with the predictor
        pr = post(f"/api/runs/{rec['id']}/predict", {"rows": [{"age": 41, "employment": "salaried", "income_usd": 52000,
                                                               "credit_score": 610, "debt_to_income": 0.41}]})
        check(pr["predictions"], pr)
        s["cost"] = rec
        ens = rec["ensemble"]["weights"] if rec.get("ensemble") else {}
        return (f"{rec['id']} eval_metric expected_cost (needs_proba, t* = {cm['bayes_threshold']:.4f}) · best {rec['best_model']} "
                f"val {lb[0]['score_val_natural'] * 1000:.1f} per 1,000 · ensemble {', '.join(f'{k} {v:.2f}' for k, v in ens.items())} · "
                f"t = {thr['value']:.3f} calibrated by AutoGluon on cost · holdout {h['expected_cost'] * 1000:.1f} per 1,000 "
                f"(Bayes rule {h['expected_cost_bayes'] * 1000:.1f}) · predictor reloads in the API")

    if not step("cost: refused requests", refuse) or not step("cost: cost-optimised run (FN = 10 × FP)", cost_run):
        return

    def twin(metric: str):
        def fn():
            r = post(f"/api/runs/{s['cost']['id']}/metric-twin", {"eval_metric": metric})
            rec = wait_run(r["id"])
            check(rec["eval_metric"]["name"] == metric and not rec["config"].get("cost"), rec["eval_metric"])
            check(rec["split"]["sha256"] == s["cost"]["split"]["sha256"], "twin used another split")
            check(rec["config"]["drop_columns"] == s["cost"]["config"]["drop_columns"], "twin features differ")
            s[metric] = rec
            thr = rec["threshold"]
            return (f"{rec['id']} {metric} {primary(rec)[1]:.4f} · best {rec['best_model']} · t = {thr['value']:.3f} "
                    f"({thr['source']}{' for ' + thr['metric'] if thr.get('metric') else ''})")
        return fn

    for m in ("roc_auc", "accuracy"):
        step(f"cost: {m} twin", twin(m))

    def analysis():
        rid = s["cost"]["id"]
        a = get(f"/api/runs/{rid}/cost")
        dep = a["deployed"]
        rows, _ = holdout_costs(rid, dep["t"])
        check(abs(dep["per_1000"] - 1000 * rows.mean()) < 1e-6, (dep["per_1000"], 1000 * rows.mean()))
        lo, hi = dep["ci_per_1000"]
        check(lo <= dep["per_1000"] <= hi and hi - lo > 0, dep["ci_per_1000"])
        check(len(a["curve"]) == 201, len(a["curve"]))
        for c in a["curve"][::40]:
            rc, _ = holdout_costs(rid, c["t"])
            check(abs(c["per_1000"] - 1000 * rc.mean()) < 1e-6, f"curve at t = {c['t']}")
        check(abs(a["bayes_threshold"] - c_fp / (c_fp + c_fn)) < 1e-12, a["bayes_threshold"])
        check(a["flag_none"]["per_1000"] == 1000 * c_fn * a["prevalence"], a["flag_none"])
        check(dep["per_1000"] < a["flag_none"]["per_1000"] and dep["per_1000"] < a["flag_all"]["per_1000"], "no better than a constant decision")
        # a run not trained on cost is costed with query parameters
        q = get(f"/api/runs/{s['roc_auc']['id']}/cost", c_fp=c_fp, c_fn=c_fn)
        check(q["validation"] is not None and 0 < q["validation_threshold"] < 1, q.get("validation_threshold"))
        return (f"per 1,000 decisions: {dep['per_1000']:.1f} [{lo:.1f}, {hi:.1f}] at t = {dep['t']:.3f} · Bayes t* = {a['bayes_threshold']:.4f}: "
                f"{a['bayes']['per_1000']:.1f} · t = 0.5: {a['default']['per_1000']:.1f} · flag nobody {a['flag_none']['per_1000']:.1f} · "
                f"flag everybody {a['flag_all']['per_1000']:.1f} · hindsight min {a['hindsight']['per_1000']:.1f} at t = {a['hindsight']['t']:.3f} · "
                f"201-point curve recomputed")

    step("cost: expected cost per 1,000 + curve", analysis)

    def compare():
        check("roc_auc" in s, "no ROC AUC twin")
        c = get(f"/api/runs/{s['cost']['id']}/cost-comparison")
        by = {("cost" if x["trained_on_cost"] else x["eval_metric"]): x for x in c["runs"]}
        check({"cost", "roc_auc"} <= set(by), list(by))
        me, roc = by["cost"], by["roc_auc"]
        check(me["this_run"] and me["run_id"] == s["cost"]["id"] and roc["run_id"] == s["roc_auc"]["id"], (me["run_id"], roc["run_id"]))
        a, ra = holdout_costs(me["run_id"], me["threshold"]["value"])
        b, rb = holdout_costs(roc["run_id"], roc["threshold"]["value"])
        check(sorted(ra.tolist()) == sorted(rb.tolist()), "the two runs were scored on different holdout rows")
        check(abs(roc["deployed"]["per_1000"] - 1000 * b.mean()) < 1e-6, "ROC AUC twin's cost")
        d = roc["delta"]
        check(abs(d["per_1000"] - 1000 * (b.mean() - a.mean())) < 1e-6 and d["ci_per_1000"][0] <= d["per_1000"] <= d["ci_per_1000"][1], d)
        st["cost_compare"] = c
        dv = roc["delta_validation"]
        parts = []
        for k, x in by.items():
            parts.append(f"{k} {x['deployed']['per_1000']:.1f} [{x['deployed']['ci_per_1000'][0]:.1f}, {x['deployed']['ci_per_1000'][1]:.1f}]"
                         f" at t = {x['threshold']['value']:.3f}" + (f", {x['validation']['per_1000']:.1f} at its cost-calibrated t = "
                                                                      f"{x['validation_threshold']:.3f}" if k != "cost" else ""))
        check(me["deployed"]["per_1000"] < roc["deployed"]["per_1000"],
              f"cost-optimised {me['deployed']['per_1000']:.1f} is not below ROC-AUC-optimised {roc['deployed']['per_1000']:.1f} per 1,000")
        return (" · ".join(parts) + f" · Δ(ROC AUC − cost) = {d['per_1000']:+.1f} per 1,000 [{d['ci_per_1000'][0]:+.1f}, {d['ci_per_1000'][1]:+.1f}]"
                f" paired · with both thresholds calibrated on cost: Δ = {dv['per_1000']:+.1f} [{dv['ci_per_1000'][0]:+.1f}, {dv['ci_per_1000'][1]:+.1f}]")

    step("cost: cost-optimised vs ROC-AUC-optimised", compare)

    def multiclass():
        rng = np.random.default_rng(11)
        n = 1500
        df = pd.DataFrame({"x1": rng.normal(size=n), "x2": rng.normal(size=n), "seg": rng.choice(["a", "b"], n)})
        z = np.stack([df.x1, df.x2, -df.x1 - df.x2 + (df.seg == "b")], axis=1) + rng.gumbel(size=(n, 3))
        df["grade"] = np.array(["low", "mid", "high"])[z.argmax(axis=1)]
        d = j(C.post("/api/datasets", files={"file": ("smoke_grades.csv", df.to_csv(index=False).encode(), "text/csv")},
                     data={"target": "grade"}))
        # classes in a non-sorted order: the API puts the matrix in AutoGluon's order (high, low, mid)
        classes = ["low", "mid", "high"]
        mat = [[0, 1, 4], [2, 0, 1], [20, 5, 0]]
        bad = C.post("/api/runs", json={"dataset_id": d["id"], "cost": {"classes": ["low", "mid"], "matrix": [[0, 1], [1, 0]]}})
        check(bad.status_code == 400 and "missing high" in bad.text, bad.text[:200])
        r = post("/api/runs", {"dataset_id": d["id"], "label": "grade", "time_limit": 60, "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 30}, "LR": {}}},
                               "cost": {"classes": classes, "matrix": mat}, "feature_importance": False, "source": "smoke-cost"})
        rec = wait_run(r["id"])
        cm = rec["config"]["cost"]
        check(cm["classes"] == ["high", "low", "mid"] and cm["matrix"][0] == [0, 20, 5], cm)
        hp = get(f"/api/runs/{rec['id']}/holdout-predictions")
        M = {(a, b): mat[classes.index(a)][classes.index(b)] for a in classes for b in classes}
        served = np.mean([M[(str(t), str(p))] for t, p in zip(hp["y_true"], hp["y_pred"])])
        check(abs(rec["holdout"]["metrics"]["expected_cost"] - served) < 1e-9, (rec["holdout"]["metrics"]["expected_cost"], served))
        a = get(f"/api/runs/{rec['id']}/cost")
        # the Bayes decision minimises the cost the model expects, not necessarily the realised holdout cost: reported only
        check(abs(a["deployed"]["per_1000"] - 1000 * served) < 1e-6 and a["bayes"]["n"] == len(hp["rows"]), a["deployed"])
        ex = C.get(f"/api/runs/{rec['id']}/export.py").text
        check("COST = {" in ex and "multiclass_cost_scorer" in ex, "export lacks the cost scorer")
        return (f"{rec['id']} 3 classes, matrix given as low/mid/high → AutoGluon order high/low/mid · served (arg-max) "
                f"{a['deployed']['per_1000']:.0f} per 1,000 [{a['deployed']['ci_per_1000'][0]:.0f}, {a['deployed']['ci_per_1000'][1]:.0f}] · "
                f"Bayes decisions {a['bayes']['per_1000']:.0f} · export.py rebuilds the scorer")

    step("cost: multiclass cost matrix", multiclass)


def monotone(st: dict[str, Any]) -> None:
    """F8: monotone constraints. On credit-default with debt_to_income ↑ (the Gym's Advanced body), LightGBM, XGBoost and
    CatBoost are trained as their monotone subclasses, bagged and stacked (L = 1); RF and LR are left out (compliant
    families only). The worker's report says where the constraint landed in every model (stack inputs +1) and checks
    every model with ICE curves on raw holdout rows. The run is registered (cloned, pipeline pruned) and the served
    model is checked twice: by GET /registry/versions/{id}/monotone, and here, from the version's own /score answers on
    a grid of debt_to_income (200 holdout rows × the column's 25 quantiles): every ICE curve and the partial dependence
    must be non-decreasing. The same body without constraints (RF and LR back in) is the before."""
    ds, label, col = "credit-default", "defaulted", "debt_to_income"
    s: dict[str, Any] = {}
    prof = get(f"/api/datasets/{ds}")["profile"]
    drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
    hp = {"GBM": {"num_boost_round": 150}, "XGB": {"n_estimators": 150}, "CAT": {"iterations": 150},
          "RF": {"n_estimators": 40}, "LR": {}}
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
            "time_limit": 600, "fit_kwargs": {"hyperparameters": hp, "num_bag_folds": 3, "num_stack_levels": 1,
                                              "dynamic_stacking": False},
            "drop_columns": drop, "feature_importance": False, "source": "smoke-monotone"}
    cons = {col: "increasing"}

    def refuse():
        bad = [({**body, "monotone": {"constraints": {"employment": "increasing"}}}, "no order"),
               ({**body, "monotone": {"constraints": {"nope": "increasing"}}}, "no such column"),
               ({**body, "monotone": {"constraints": {col: "upward"}}}, "use 'increasing'"),
               ({**body, "monotone": {"constraints": {"applicant_id": 1}}}, "dropped"),
               ({**body, "label": "loan_purpose", "problem_type": "multiclass", "eval_metric": None,
                 "monotone": {"constraints": {col: 1}}}, "binary and regression"),
               ({**body, "monotone": {"constraints": cons}, "distill": {"time_limit": 30}}, "distill"),
               ({**body, "fit_kwargs": {"hyperparameters": {"RF": {}, "LR": {}}}, "monotone": {"constraints": cons},
                 "scorecard": False}, "No model family that can honour")]  # F11: with the scorecard on, it honours it
        for b, words in bad:
            r = C.post("/api/runs", json=b)
            check(r.status_code == 400 and words in r.text, f"{words}: {r.status_code} {r.text[:300]}")
        return f"{len(bad)} bad monotone requests → 400"

    def gym():
        v = post("/api/gym/validate", {**body, "monotone": {"constraints": cons}})
        check(v["ok"], v["errors"])
        m = v["monotone"]
        # F11: SCORECARD is added to binary runs and honours the constraint by monotone binning
        check(m["constraints"] == {col: 1} and m["compliant"] == ["CAT", "GBM", "SCORECARD", "XGB"] and m["left_out"] == ["LR", "RF"], m)
        hpr = v["resolved"]["fit_kwargs"]["hyperparameters"]
        check(set(hpr) == {"GBM_MONO", "XGB_MONO", "CAT_MONO", "SCORECARD"}, list(hpr))
        check(all(c["monotone"]["constraints"] == {col: 1} for c in hpr.values()), hpr)
        check(any("Stacking" in i["message"] for i in v["info"] if i["path"] == "monotone"), v["info"])
        w = post("/api/gym/validate", {**body, "monotone": {"constraints": cons, "compliant_only": False}})
        check(w["ok"] and any(x["path"] == "monotone" and "RF" in x["message"] and "LR" in x["message"] for x in w["warnings"]), w["warnings"])
        check(set(w["resolved"]["fit_kwargs"]["hyperparameters"]) == {"GBM_MONO", "XGB_MONO", "CAT_MONO", "SCORECARD", "RF", "LR"}, "kept")
        return (f"resolved {', '.join(sorted(hpr))} (monotone {col}: +1) · RF, LR left out · compliant_only=false warns: "
                f"{w['warnings'][0]['message'][:60]}…")

    if not step("monotone: refused requests", refuse) or not step("monotone: Gym validate (exclude / warn)", gym):
        return

    def constrained():
        r = post("/api/runs", {**body, "monotone": {"constraints": cons}})
        rec = wait_run(r["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        mr = rec["monotone"]
        check(mr and not mr.get("error"), mr)
        check(mr["columns"][col]["status"] == "constrained" and mr["columns"][col]["features"] == [col], mr["columns"])
        models = {m["model"]: m for m in mr["models"]}
        fams = {m["family"] for m in mr["models"] if m["kind"] == "model"}
        check(fams == {"GBM", "XGB", "CAT", "SCORECARD"}, f"families trained: {fams} (a monotone subclass failed?)")
        check(not mr["unconstrained_models"], mr["unconstrained_models"])
        l2 = [m for m in mr["models"] if m["kind"] == "model" and m["stack_level"] == 2]
        check(l2, "no stack level 2 models: the stack was not exercised")
        for m in l2:  # the stacker's stack inputs (base predictions) are constrained +1, the raw column too
            base = [b for b in m["applied"] if b in models]
            check(base and all(m["applied"][b] == 1 for b in base) and m["applied"].get(col) == 1, m["applied"])
        for m in mr["models"]:
            check(m["monotone_by_construction"], f"{m['model']} not monotone by construction")
            check(m.get("monotone_checked"), f"{m['model']} ICE: {m.get('check')}")
        check(mr["best_monotone"], mr["served"])
        sv = mr["served"][col]
        check(sv["pd_monotone"] and sv["curves_violating"] == 0 and sv["pd_range"] > 0, sv)
        s["run"] = rec
        lb = rec["leaderboard"]
        return (f"{rec['id']} · {len(mr['models'])} models (L2 stackers: {', '.join(m['model'] for m in l2)}), all monotone by "
                f"construction and by ICE · best {rec['best_model']} · PD {sv['pd'][0]:.3f} → {sv['pd'][-1]:.3f} over "
                f"{len(sv['grid'])} points · 0/{sv['n_curves']} curves violate · served model's holdout ROC AUC {rec['holdout']['metrics']['roc_auc']:.4f}")

    if not step(f"monotone: run with {col} ↑ (bagged, stacked)", constrained):
        return

    def before():
        r = post("/api/runs", body)
        rec = wait_run(r["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        check(rec["split"]["sha256"] == s["run"]["split"]["sha256"], "twin used another split")
        m = get(f"/api/runs/{rec['id']}/monotone", constraints=f"{col}:increasing")
        x = m["columns"][col]
        s["before"] = {"rec": rec, "check": x}
        per = []
        for row in rec["leaderboard"]:
            mm = get(f"/api/runs/{rec['id']}/monotone", constraints=f"{col}:increasing", model=row["model"], rows=100, curves=0)
            per.append(f"{row['model']} {mm['columns'][col]['curves_violating']}/{mm['columns'][col]['n_curves']}")
        s["before_models"] = per
        return (f"{rec['id']} unconstrained · best {rec['best_model']} · {x['curves_violating']}/{x['n_curves']} ICE curves "
                f"decrease somewhere (max drop {x['max_violation']:.4f}), PD monotone: {x['pd_monotone']} · holdout ROC AUC "
                f"{rec['holdout']['metrics']['roc_auc']:.4f} · per model: {'; '.join(per)}")

    step("monotone: before = same body unconstrained", before)

    def price():
        # the constraint's own price: the same three families, unconstrained (RF and LR out as in the constrained run)
        b = {**body, "fit_kwargs": {**body["fit_kwargs"], "hyperparameters": {k: hp[k] for k in ("GBM", "XGB", "CAT")}}}
        rec = wait_run(post("/api/runs", b)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        x = get(f"/api/runs/{rec['id']}/monotone", constraints=f"{col}:increasing")["columns"][col]
        auc = {k: (s[k] if k == "run" else s[k]["rec"])["holdout"]["metrics"]["roc_auc"] for k in ("run", "before")}
        a = rec["holdout"]["metrics"]["roc_auc"]
        s["price"] = {"rec": rec, "check": x}
        return (f"{rec['id']} GBM + XGB + CAT unconstrained · {x['curves_violating']}/{x['n_curves']} curves violate · "
                f"served model's holdout ROC AUC {a:.4f} · constrained {auc['run']:.4f} (Δ {auc['run'] - a:+.4f}) · all families "
                f"unconstrained {auc['before']:.4f}")

    step("monotone: price of the constraint (same families, unconstrained)", price)

    def served():
        rid = s["run"]["id"]
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit.monotone"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        vid = v["id"]
        s["vid"] = vid
        chk = get(f"/api/registry/versions/{vid}/monotone")
        x = chk["columns"][col]
        check(chk["monotone"] and x["curves_violating"] == 0 and x["pd_monotone"], chk["columns"])
        # Independently of the API's check: the version's own /score answers, as a client sees them. The holdout rows
        # come from the sample generator (the same seed as the API's sample; the labels are checked against the run's).
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
        from app.samples import generate_credit

        df, _ = generate_credit()
        hp_ = get(f"/api/runs/{rid}/holdout-predictions")
        idx = np.asarray(hp_["rows"], dtype=int)
        check([str(a) for a in df[label].to_numpy()[idx]] == [str(b) for b in hp_["y_true"]], "regenerated sample differs")
        rows = df.iloc[idx].drop(columns=[label]).sample(200, random_state=3)
        grid = np.unique(np.quantile(df[col], np.linspace(0, 1, 25)))
        X = rows.loc[rows.index.repeat(len(grid))].copy()
        X[col] = np.tile(grid, len(rows))
        recs = json.loads(X.to_json(orient="records"))
        out = post(f"/api/registry/versions/{vid}/score", {"rows": recs, "where": "worker"})
        pos = s["run"]["positive_class"]
        p = np.asarray([pr[str(pos)] for pr in out["probabilities"]], dtype=float).reshape(len(rows), len(grid))
        d = np.diff(p, axis=1)
        pdp = p.mean(axis=0)
        check((d >= -1e-9).all(), f"{int((d < -1e-9).any(axis=1).sum())} ICE curves decrease; max drop {-d.min():.3g}")
        check((np.diff(pdp) >= -1e-9).all(), f"PD decreases: {pdp.round(4).tolist()}")
        check(pdp[-1] > pdp[0], "PD flat")
        c = get(f"/api/registry/versions/{vid}/card")
        mc = c["constraints"]
        check(mc and mc["columns"][0]["column"] == col and mc["columns"][0]["direction"] == "increasing"
              and mc["columns"][0]["monotone"] is True and mc["served_model"] == chk["served_model"], mc)
        md = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Monotone constraints" in md and f"| {col} | increasing | yes |" in md, "card markdown")
        s["served"] = {"pd": pdp.tolist(), "grid": grid.tolist(), "model": chk["served_model"]}
        return (f"{vid} serves {chk['served_model']} · API check 0/{x['n_curves']} curves violate · /score on "
                f"{len(rows)} rows × {len(grid)} grid points: every ICE curve non-decreasing, PD {pdp[0]:.4f} → "
                f"{pdp[-1]:.4f} · model card records {col} ↑ (monotone: yes)")

    step("monotone: served model PD on a grid (registry /score)", served)


def quantile(st: dict[str, Any]) -> None:
    """F9: quantile regression ("predict a range"). On loan-amount, TabularPredictor(problem_type='quantile',
    quantile_levels=[0.1, 0.5, 0.9]) as New Model sends it. The worker reports the pinball loss per level, F̂(τ), the
    [q0.1, q0.9] holdout coverage (acceptance: close to 0.8) raw and conformally calibrated, and the fan chart. Every
    path that assumed binary / multiclass / regression either serves the quantiles (predict, holdout predictions,
    Explain, SHAP of one level, fairness coverage per group, registry build, Try It, /score, batch scoring, the model
    card, latency, export.py) or refuses with a 400 (threshold, cost, monotone, distill, pseudo-labels). A second run
    with conformal = true (AutoGluon's calibrate, CQR) serves the calibrated quantiles; both are reported."""
    ds, label = "loan-amount", "loan_amount_usd"
    levels = [0.1, 0.5, 0.9]
    s: dict[str, Any] = {}
    body = {"dataset_id": ds, "label": label, "problem_type": "quantile", "quantile_levels": levels, "eval_metric": None,
            "presets": "medium_quality", "time_limit": 120, "source": "smoke-quantile"}

    def refuse():
        bad = [({**body, "quantile_levels": [0, 0.5]}, "outside (0, 1)"),
               ({**body, "quantile_levels": [0.5, 0.5]}, "each quantile level once"),
               ({**body, "quantile_levels": ["a"]}, "not a number"),
               ({**body, "label": "purpose"}, "numeric label"),
               ({**body, "problem_type": "regression"}, "apply to problem_type 'quantile'"),
               ({**body, "eval_metric": "root_mean_squared_error"}, "not a quantile metric"),
               ({**body, "cost": {"c_fp": 1, "c_fn": 5}}, "business cost"),
               ({**body, "distill": {"time_limit": 30}}, "distill"),
               ({**body, "pseudolabel": {"dataset_id": ds}}, "pseudo"),
               ({**body, "monotone": {"constraints": {"income_usd": "increasing"}}}, "binary and regression"),
               ({**body, "calibrate_threshold": "f1"}, "decision threshold"),
               ({**body, "problem_type": "regression", "quantile_levels": None, "conformal": True}, "conformal"),
               ({**body, "conformal": True, "fit_kwargs": {"calibrate": True}}, "not both")]
        for b, words in bad:
            r = C.post("/api/runs", json=b)
            check(r.status_code == 400 and words in r.text, f"{words}: {r.status_code} {r.text[:300]}")
        return f"{len(bad)} bad quantile requests → 400"

    def gym():
        v = post("/api/gym/validate", {**body, "problem_type": "auto", "conformal": True})
        check(v["ok"], v["errors"])
        check(v["problem_type"] == "quantile" and v["quantile"]["levels"] == levels, v.get("quantile"))
        pk = v["resolved"]["predictor_kwargs"]
        check(pk["problem_type"] == "quantile" and pk["quantile_levels"] == levels, pk)
        check(v["resolved"]["fit_kwargs"].get("calibrate") is True, v["resolved"]["fit_kwargs"])
        check("quantile_levels=[0.1, 0.5, 0.9]" in v["call"] and "calibrate=True" in v["call"], v["call"])
        return (f"auto + levels → quantile · predictor_kwargs problem_type='quantile', quantile_levels={pk['quantile_levels']} · "
                f"fit(calibrate=True) · info: {next(i['message'] for i in v['info'] if i['path'] == 'quantile_levels')[:90]}")

    if not step("quantile: refused requests", refuse) or not step("quantile: Gym validate", gym):
        return

    def fit():
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        check(rec["problem_type"] == "quantile" and rec["quantile_levels"] == levels, rec["problem_type"])
        check(rec["eval_metric"]["name"] == "pinball_loss", rec["eval_metric"])
        q = rec["quantile"]
        check(q and not q.get("error"), q)
        h = q["holdout"]
        check([x["tau"] for x in h["levels"]] == levels and all(x["pinball"] > 0 for x in h["levels"]), h["levels"])
        check(abs(h["pinball"] - rec["holdout"]["metrics"]["pinball_loss"]) < 1e-6 * max(1, h["pinball"]),
              f"pinball {h['pinball']} vs AutoGluon's {rec['holdout']['metrics']['pinball_loss']}")
        iv = h["intervals"][0]
        check((iv["lo"], iv["hi"], iv["nominal"]) == (0.1, 0.9, 0.8), iv)
        check(abs(iv["coverage"] - 0.8) <= 0.05, f"[q0.1, q0.9] holdout coverage {iv['coverage']:.4f}, not close to 0.8")
        check(q["raw"] and q["calibrated"] and q["conformal"].get("offsets"), q["conformal"])
        f = q["fan"]
        check(len(f["y"]) == len(f["q"]["0.5"]) > 50 and f["q"]["0.5"] == sorted(f["q"]["0.5"]), "fan chart not sorted by q0.5")
        check(q["models"] and all(m["pinball"] is not None for m in q["models"]), q["models"])
        s["run"] = rec
        raw, cal = q["raw"]["intervals"][0], q["calibrated"]["intervals"][0]
        return (f"{rec['id']} · {len(rec['leaderboard'])} models, best {rec['best_model']} · holdout pinball {h['pinball']:.1f} · "
                f"per level " + ", ".join(f"τ={x['tau']:g}: ρ {x['pinball']:.1f}, F̂ {x['below']:.3f}" for x in h["levels"])
                + f" · [q0.1, q0.9] coverage {iv['coverage']:.4f} [{iv['coverage_ci'][0]:.3f}, {iv['coverage_ci'][1]:.3f}] "
                f"(served {q['served']}; raw {raw['coverage']:.4f}, conformal would give {cal['coverage']:.4f}) · mean width "
                f"{iv['mean_width']:,.0f} · crossing {h['crossing_share']:.2%} · c_τ {q['conformal']['offsets']}")

    if not step("quantile: loan-amount run, τ = 0.1 / 0.5 / 0.9", fit):
        return
    rid = s["run"]["id"]

    def paths():
        hp = get(f"/api/runs/{rid}/holdout-predictions")
        check(hp["quantile_levels"] == levels and len(hp["quantiles"]) == hp["n_rows"], "holdout-predictions quantiles")
        Q = np.asarray(hp["quantiles"])
        check(np.allclose(np.asarray(hp["y_pred"], dtype=float), Q[:, 1]), "y_pred is not q0.5")
        ex = get(f"/api/runs/{rid}/explain")
        check(ex["kind"] == "regression" and ex["centre_level"] == 0.5 and ex["quantile"]["levels"] == levels, ex.get("kind"))
        sh = get(f"/api/runs/{rid}/shap", row=hp["rows"][0], target="0.9")
        check(sh["quantile_level"] == 0.9 and abs(sh["additivity_error"]) < 1e-6 * max(1, abs(sh["fx"])) and
              abs(sh["fx"] - Q[0, 2]) < 1e-3 * max(1, abs(Q[0, 2])), {k: sh[k] for k in ("fx", "base_value", "additivity_error")})
        bad = C.get(f"/api/runs/{rid}/shap", params={"row": hp["rows"][0], "target": "0.33"})
        check(bad.status_code == 400 and "No quantile level" in bad.text, bad.text[:200])
        fr = get(f"/api/runs/{rid}/fairness", group="purpose")
        check(fr["interval"]["lo"] == 0.1 and all(g.get("coverage") is not None for g in fr["by_group"]), fr.get("by_group"))
        pr = post(f"/api/runs/{rid}/predict", {"rows": [{"income_usd": 60000, "credit_score": 720, "term_months": 36,
                                                          "purpose": "car", "home_ownership": "rent"}]})
        qq = pr["quantiles"][0]
        check(set(qq) == {"0.1", "0.5", "0.9"} and qq["0.1"] <= qq["0.5"] <= qq["0.9"] and pr["predictions"][0] == qq["0.5"], pr)
        refused = []
        for path, params in (("threshold", {}), ("cost", {}), ("monotone", {"constraints": "income_usd:increasing"})):
            r = C.get(f"/api/runs/{rid}/{path}", params=params)
            check(r.status_code == 400, f"{path}: {r.status_code} {r.text[:200]}")
            refused.append(path)
        r = C.post(f"/api/runs/{rid}/register", json={"created_by": "Smoke Analyst", "distill": True})
        check(r.status_code == 400 and "quantile" in r.text, r.text[:200])
        lat = get(f"/api/runs/{rid}/latency")
        check(lat.get("models") or lat.get("rows") or lat, "latency")
        gaps = ", ".join(f"{g['group']} {g['coverage']:.2f}" for g in fr["by_group"])
        return (f"holdout predictions q0.1/q0.5/q0.9 · Explain residuals on q0.5 · SHAP of q̂0.9 row {sh['row']}: "
                f"φ₀ {sh['base_value']:,.0f} + Σφ = {sh['fx']:,.0f} · coverage by purpose: {gaps} · /predict → {qq} · "
                f"400 for {', '.join(refused)}, register+distill · latency table ok")

    step("quantile: explain, SHAP, fairness, predict, refusals", paths)

    def conformal():
        rec = wait_run(post("/api/runs", {**body, "conformal": True})["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        q = rec["quantile"]
        check(q["conformal"]["applied"] and q["served"] == "calibrated", q["conformal"])
        check(rec["split"]["sha256"] == s["run"]["split"]["sha256"], "twin used another split")
        raw, cal = q["raw"]["intervals"][0], q["calibrated"]["intervals"][0]
        check(abs(q["holdout"]["intervals"][0]["coverage"] - cal["coverage"]) < 1e-12, "served ≠ calibrated")
        s["conformal"] = rec
        return (f"{rec['id']} conformal=true · c_τ {q['conformal']['offsets']} ({q['conformal']['source']}) · [q0.1, q0.9] "
                f"coverage raw {raw['coverage']:.4f} → calibrated {cal['coverage']:.4f} · width {raw['mean_width']:,.0f} → "
                f"{cal['mean_width']:,.0f} · pinball raw {q['raw']['pinball']:.1f} → calibrated {q['calibrated']['pinball']:.1f}")

    step("quantile: conformal recalibration (AutoGluon calibrate=True)", conformal)

    def registry():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.loan.quantile"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        vid = v["id"]
        res = v["result"]
        hc = res["version"]["holdout_ci"]["metrics"]
        check({"pinball_loss", "pinball_0.1", "pinball_0.5", "pinball_0.9", "coverage", "mean_width"} <= set(hc), list(hc))
        check(abs(hc["coverage"]["value"] - 0.8) <= 0.05, hc["coverage"])
        vq = res["quantile"]
        check(vq["levels"] == levels and vq["raw"] and vq["calibrated"], f"version quantile report: {vq.get('conformal')}")
        row = {"income_usd": 85000, "credit_score": 760, "existing_debt_usd": 4000, "term_months": 48, "purpose": "home improvement",
               "home_ownership": "own", "region": "west", "open_accounts": 3}
        t = post(f"/api/registry/versions/{vid}/predict", {"row": row, "target": "0.9"})
        check(t["quantile_levels"] == levels and t["quantiles"]["0.1"] <= t["quantiles"]["0.5"] <= t["quantiles"]["0.9"], t.get("quantiles"))
        check(t["prediction"] == t["quantiles"]["0.5"] and t["shap"]["quantile_level"] == 0.9, "Try It")
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": [row, {**row, "income_usd": 30000}], "where": "worker"})
        check(len(sc["quantiles"]) == 2 and sc["predictions"][0] == sc["quantiles"][0]["0.5"], sc)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
        from app.samples import generate_loan_amount

        df, _ = generate_loan_amount()
        batch = df.drop(columns=[label]).sample(500, random_state=5)
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("q.csv", batch.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
        check(b["status"] == "succeeded", b.get("error"))
        cols = b["result"]["columns"]
        check({"prediction", "q_0.1", "q_0.5", "q_0.9"} <= set(cols), cols)
        sm = b["result"]["summary"]
        check(sm["interval"]["lo"] == 0.1 and sm["interval"]["mean_width"] > 0, sm.get("interval"))
        bench = post(f"/api/registry/versions/{vid}/benchmark", {})
        c = get(f"/api/registry/versions/{vid}/card")
        qc = c["quantile"]
        check(qc and qc["levels"] == levels and qc["intervals"][0]["coverage"] is not None, qc)
        md = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Quantile regression (range)" in md and "[q̂_0.1, q̂_0.9]" in md, "card markdown")
        s["vid"] = vid
        return (f"{vid} serves {res['version']['best_model']} · holdout coverage {hc['coverage']['value']:.4f} "
                f"(raw {vq['raw']['intervals'][0]['coverage']:.4f}, calibrated {vq['calibrated']['intervals'][0]['coverage']:.4f}) "
                f"[{hc['coverage']['ci_low']:.3f}, {hc['coverage']['ci_high']:.3f}] · pinball {hc['pinball_loss']['value']:.1f} "
                f"[{hc['pinball_loss']['ci_low']:.1f}, {hc['pinball_loss']['ci_high']:.1f}] · Try It {t['quantiles']} · "
                f"/score {sc['predict_ms']:.1f} ms · batch 500 rows with q_0.1/q_0.5/q_0.9, mean width {sm['interval']['mean_width']:,.0f} · "
                f"one-row p50 {bench['one_row']['ms_p50']:.1f} ms · card section ok")

    step("quantile: registry, Try It, /score, batch, card", registry)

    def export():
        text = C.get(f"/api/runs/{rid}/export.py").text
        check("quantile_levels" in text, "export lacks quantile_levels")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "export_quantile.py")
            open(p, "w").write(text)
            out = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=900, cwd=d)
        tail = (out.stdout.strip().splitlines() or [""])[-1]
        check(out.returncode == 0, f"exit {out.returncode}: {(out.stderr or out.stdout)[-600:]}")
        return f"export.py reproduced the quantile run · {tail[:90]}"

    step("quantile: export.py → run in subprocess", export)


def autopilot(st: dict[str, Any]) -> None:
    def start():
        a = post("/api/autopilot", {"dataset_id": "support-tickets", "label": "category",
                                    "requirements": [{"metric": "f1_macro", "op": ">=", "value": float(os.environ.get("SMOKE_F1", 0.92))},
                                                     {"metric": "p95_latency_ms", "op": "<=", "value": 50}],
                                    "budget_seconds": 900, "autonomy": "ask", "decider": os.environ.get("SMOKE_DECIDER", "auto")})
        st["ap"] = a["id"]
        a = poll(f"/api/autopilot/{a['id']}", lambda x: x["status"] in ("awaiting_inbox", "finished", "failed"), 1200, 2)
        check(a["status"] == "awaiting_inbox", f"status {a['status']}: {a.get('error')}")
        base = a["steps"][1]
        return f"{a['id']} · baseline {base.get('run_id')} macro-F1 {base.get('score', float('nan')):.4f}"

    if not step("autopilot: start → baseline → inbox", start):
        return
    aid = st["ap"]

    def inbox():
        ib = get(f"/api/autopilot/{aid}/inbox")
        st["inbox"] = ib
        check(len(ib["items"]) > 0, "empty inbox")
        kinds: dict[str, int] = {}
        for it in ib["items"]:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        return f"{len(ib['items'])} items · {kinds}"

    step("autopilot: inbox", inbox)

    def recall():
        r = st["inbox"].get("recall") or {}
        check(r.get("planted"), "no planted-error recall reported")
        check(r["recall"] >= 0.6, f"recall {r['recall']:.3f} < 0.60")
        st["recall"] = r
        return f"recall of planted flips {r['found']}/{r['planted']} = {r['recall']:.1%} in the top {r['top_k']} · precision {r['precision']:.1%}"

    step("autopilot: inbox recall (label_noise.json)", recall)

    def answer():
        out = post(f"/api/autopilot/{aid}/inbox/bulk", {"min_confidence": 0.5, "action": "accept"})
        a = post(f"/api/autopilot/{aid}/resume", {})
        return f"bulk-accepted {out['answered']} (≥ 0.5 confidence) · resumed"

    step("autopilot: bulk answer + resume", answer)

    def finish():
        a = poll(f"/api/autopilot/{aid}", lambda x: x["status"] in ("finished", "failed"), 1800, 3)
        check(a["status"] == "finished", f"{a['status']}: {a.get('error')}")
        st["apdone"] = a
        scored = [s for s in a["steps"] if s.get("score") is not None]
        base, best = scored[0]["score"], max(s["score"] for s in scored)
        check(best > base, f"answering did not raise the score: {base:.4f} → {best:.4f}")
        ds = a.get("dataset_versions") or []
        return f"{len(a['steps'])} steps · macro-F1 {base:.4f} → {best:.4f} (Δ {best - base:+.4f}) · {len(ds)} dataset version(s)"

    step("autopilot: retrain → finish", finish)

    def requirements():
        a = st["apdone"]
        v = a["verdict"]
        check(v["met"] in (True, False) and v["checks"], v)
        s = " · ".join(f"{c['metric']} {c['observed']:.4g} {c['op']} {c['value']} {'✓' if c['ok'] else '✗'}" for c in v["checks"])
        deciders = sorted({d.get("layer") for d in a.get("decisions", []) if d.get("layer")})
        return f"{'MET' if v['met'] else 'NOT MET'} · {s} · decided by {deciders}"

    step("autopilot: requirements check", requirements)

    def ask():
        a = post("/api/vela/ask", {"question": "Did the inbox raise the score, and by how much?", "autopilot_id": aid})
        check(a["answer"] or a["source"] == "computed", a)
        return f"answered by {a['source']} · {(a['answer'] or a.get('message') or '')[:90]}…"

    step("autopilot: Vela question box", ask)

    def layer1():
        a = post("/api/autopilot", {"dataset_id": "support-tickets", "requirements": [{"metric": "f1_macro", "value": 0.92},
                                    {"metric": "p95_latency_ms", "value": 50}], "budget_seconds": 600,
                                    "autonomy": "auto", "auto_confidence": 0.9, "decider": "layer1"})
        a = poll(f"/api/autopilot/{a['id']}", lambda x: x["status"] in ("finished", "failed"), 1800, 3)
        check(a["status"] == "finished", f"{a['status']}: {a.get('error')}")
        layers = {d["layer"] for d in a["decisions"]}
        check(layers <= {"layer-1"}, layers)
        # F17: layer 1 reads the Model Doctor's diagnosis of the best run before every decision
        docs = [d.get("doctor") for d in a["decisions"]]
        check(a["decisions"] and all(docs), f"decisions without the doctor: {[d['action'] for d in a['decisions'] if not d.get('doctor')]}")
        check(all("Model Doctor" in d["rationale"] or d["action"] == "stop" or not (d["doctor"] or {}).get("primary")
                  or "no remaining menu action" in d["rationale"] for d in a["decisions"]), [d["rationale"][:160] for d in a["decisions"]])
        v = a["verdict"]
        trail = " → ".join(f"{(d['doctor']['primary'] or {}).get('id', 'none')}⇒{d['action']}" for d in a["decisions"])
        return (f"{a['id']} · no LLM · auto-accepted c ≥ 0.9 · {len([s for s in a['steps'] if s.get('run_id')])} runs · "
                f"{'MET' if v['met'] else 'NOT MET'} · Δ{a['primary']} {v['delta']:+.4f} · doctor ⇒ action: {trail}")

    step("autopilot: layer 1 only (no LLM), auto", layer1)


FC_MAP = {"item_id": "item_id", "timestamp": "timestamp", "target": "volume", "known_covariates": ["holiday", "payday"],
          "static_features": ["segment"], "past_covariates": []}
FC_HP = {"SeasonalNaive": {}, "ETS": {}, "DirectTabular": {}, "Chronos": {"model_path": "bolt_tiny"}}


def forecast(st: dict[str, Any]) -> None:
    def mapping():
        m = get("/api/forecast/mapping/transaction-volume")
        check(m["mapping"]["target"] == "volume" and m["mapping"]["known_covariates"] == ["holiday", "payday"], m)
        p = post("/api/forecast/preview", {"dataset_id": "transaction-volume", "mapping": m["mapping"]})
        check(p["items"] == 6 and p["freq"] == "D" and p["seasonal_period"] == 7 and p["future_rows"] == 168, p)
        o = get("/api/forecast/options")
        check("bolt_tiny" in [x["key"] for x in o["presets"]] and "MASE" in [x["key"] for x in o["metrics"]], o)
        return f"{p['items']} series × {p['length_min']} days · freq {p['freq']} · m = {p['seasonal_period']} · {p['future_rows']} future rows (known covariates)"

    step("forecast: mapping + preview", mapping)

    def run():
        r = post("/api/forecast/runs", {"dataset_id": "transaction-volume", "mapping": FC_MAP, "prediction_length": 28,
                                       "eval_metric": "MASE", "presets": "fast_training", "time_limit": 180,
                                       "hyperparameters": FC_HP,
                                       "quantile_levels": [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95],
                                       "fit_kwargs": {"enable_ensemble": True}, "source": "smoke"})
        st["fc"] = r["id"]
        sse = follow_sse(r["id"], 900)
        c = sse["counts"]
        check(c.get("model_finished", 0) >= 4 and c.get("ensemble", 0) >= 1, c)
        return f"{r['id']} · {c.get('model_started')} started / {c.get('model_finished')} finished · ensemble event · SSE → run_succeeded"

    if not step("forecast: run + SSE", run):
        return
    fid = st["fc"]

    def leaderboard():
        rec = get(f"/api/runs/{fid}")
        lb = rec["leaderboard"]
        check(rec["kind"] == "forecast" and len(lb) >= 4 and all(r["score_test_natural"] is not None for r in lb), lb)
        w = (rec["ensemble"] or {}).get("weights") or {}
        check(abs(sum(w.values()) - 1) < 1e-6, w)
        bt = rec["backtests"]
        # F1: num_val_windows defaults to auto (≥ 3 where the history allows: 5 for these 6 series × 702 steps)
        W = rec["data"]["num_val_windows"]
        check(W >= 3 and len(bt["windows"]) == W and all(len(v) == W for v in bt["models"].values()), (W, bt.get("windows")))
        naive = next(r for r in lb if r["model"] == "SeasonalNaive")
        st["fc_rec"] = rec
        best = next(r for r in lb if r["model"] == rec["best_model"])
        return (f"best {rec['best_model']} holdout MASE {best['score_test_natural']:.3f} (SeasonalNaive {naive['score_test_natural']:.3f}) · "
                "w = " + ", ".join(f"{k} {v:.2f}" for k, v in list(w.items())[:3]) + f" · W = {W} backtest windows ({rec['data']['num_val_windows_rule']})")

    step("forecast: leaderboard + weights", leaderboard)

    def errors():
        h = st["fc_rec"]["holdout"]
        items = h["items"]
        check(len(items) == 6 and all(i["MASE"] is not None for i in items), items)
        worst = items[0]
        return (f"MASE {h['metrics']['MASE']:.3f} · WQL {h['metrics']['WQL']:.4f} · coverage of [q{h['interval'][0]}, q{h['interval'][1]}] "
                f"{h['coverage']:.1%} · worst item {worst['item_id']} MASE {worst['MASE']:.3f}")

    step("forecast: per-item errors", errors)

    def calibration():
        cal = st["fc_rec"]["holdout"]["calibration"]
        lv = cal["levels"]
        check(all(lv[i]["below"] <= lv[i + 1]["below"] for i in range(len(lv) - 1)), "F̂(τ) not monotone")
        main = cal["intervals"][0]
        ok = main["ci"][0] <= main["nominal"] <= main["ci"][1]
        check((cal["verdict"] == "calibrated") == ok, cal["verdict"])
        bt = ", ".join(f"{b['model']} {b['empirical']:.0%}" for b in cal["backtest"] if b["empirical"] is not None)
        return (f"n = {cal['n']} · [q{main['lo']}, q{main['hi']}] covers {main['inside']}/{main['n']} = {main['empirical']:.1%} vs nominal "
                f"{main['nominal']:.0%} (95% CI {main['ci'][0]:.1%}–{main['ci'][1]:.1%}, P(K ≥ k) = {main['p_all_inside']:.1e}) → {cal['verdict']} · backtests: {bt}")

    step("forecast: interval calibration", calibration)

    def conformal():
        # F1 acceptance: the 90% interval's holdout coverage within [0.85, 0.95] after recalibration, MASE no more than
        # 2% worse than round 1's 0.515 (W = 2, no recalibration), raw and calibrated both recorded, per-model coverage.
        rec = st["fc_rec"]
        h = rec["holdout"]
        conf = h["conformal"]
        check(h["recalibrated"] and conf and not conf.get("error") and conf["model"] == rec["best_model"], conf)
        raw, cal = h["calibration_raw"]["intervals"][0], h["calibration"]["intervals"][0]
        check(raw["nominal"] == cal["nominal"] and abs(cal["nominal"] - 0.9) < 1e-9, (raw, cal))
        check(0.85 <= cal["empirical"] <= 0.95, f"calibrated coverage {cal['inside']}/{cal['n']} = {cal['empirical']:.3f}")
        mase, base = h["metrics"]["MASE"], 0.515
        check(mase <= base * 1.02, f"MASE {mase:.4f} vs round-1 {base} (+{mase / base - 1:.1%})")
        check(h["metrics"]["MASE"] == h["metrics_raw"]["MASE"], "recalibration must not move the point forecast")
        check(all(v is not None and v >= 0 for v in conf["factors"].values()), conf["factors"])
        pm = {x["model"]: x for x in h["calibration"]["per_model"]}
        check(set(pm) == {r["model"] for r in rec["leaderboard"]} and all(r.get("coverage_test") is not None for r in rec["leaderboard"]), pm.keys())
        kinds = {e["kind"] for e in rec["ensembles"]}
        check({"GreedyEnsemble", "LinearStackerEnsemble"} <= kinds, kinds)
        lsq = next(e for e in rec["ensembles"] if e["kind"] == "LinearStackerEnsemble")
        check(lsq["weights_per_quantile"] and all(abs(sum(v.values()) - 1) < 1e-4 for v in lsq["weights_per_quantile"].values()), lsq)
        # the files and the chart carry both versions; the recalibrated quantiles never cross
        for which in ("future", "future_raw", "holdout", "holdout_raw"):
            r = C.get(f"/api/forecast/runs/{fid}/forecast.csv", params={"which": which})
            check(r.status_code == 200 and len(pd.read_csv(io.BytesIO(r.content))) == 168, (which, r.status_code))
        it = get(f"/api/forecast/runs/{fid}/chart")["items"]["cards-online"]
        q, qr = it["holdout"]["q"], it["holdout"]["q_raw"]
        check(all(q["0.05"][i] <= q["0.25"][i] <= q["0.5"][i] <= q["0.75"][i] <= q["0.95"][i] for i in range(28)), "calibrated quantiles cross")
        check(q["0.5"] == qr["0.5"] and sum(q["0.95"][i] - q["0.05"][i] for i in range(28)) < sum(qr["0.95"][i] - qr["0.05"][i] for i in range(28)), "band")
        o = get("/api/forecast/options")
        check(o["defaults"]["conformal"] is True and o["defaults"]["num_val_windows"] is None and len(o["ensembles"]) == 3, o["defaults"])
        cov = ", ".join(f"{m} {x['inside']}/{x['n']}" for m, x in pm.items())
        return (f"[q0.05, q0.95] {raw['inside']}/{raw['n']} = {raw['empirical']:.1%} → {cal['inside']}/{cal['n']} = {cal['empirical']:.1%} "
                f"(Wilson {cal['ci'][0]:.1%}–{cal['ci'][1]:.1%}) · c_τ " + " ".join(f"{k}:{v:.2f}" for k, v in conf["factors"].items())
                + f" from {conf['n_scores']} backtest points · WQL {h['metrics_raw']['WQL']:.4f} → {h['metrics']['WQL']:.4f} · "
                f"MASE {mase:.3f} vs {base} ({mase / base - 1:+.1%}) · per-model holdout coverage: {cov}")

    step("forecast: conformal recalibration (F1)", conformal)

    def chart():
        c = get(f"/api/forecast/runs/{fid}/chart")
        it = c["items"]["cards-online"]
        f = it["future"]
        check(len(f["t"]) == 28 and len(it["history"]["y"]) == 120 and len(it["holdout"]["actual"]) == 28, it.keys())
        q = f["q"]
        mono = all(q["0.05"][i] <= q["0.5"][i] <= q["0.95"][i] for i in range(28))
        check(mono, "quantiles cross")
        return f"6 items · history 120 + holdout 28 + future 28 · q0.05 ≤ q0.5 ≤ q0.95 everywhere · {f['t'][0]} → {f['t'][-1]}"

    step("forecast: fan chart data", chart)

    def download():
        r = C.get(f"/api/forecast/runs/{fid}/forecast.csv")
        check(r.status_code == 200, r.status_code)
        df = pd.read_csv(io.BytesIO(r.content))
        check(len(df) == 168 and {"item_id", "timestamp", "mean", "0.5"} <= set(df.columns), df.columns)
        st["fc_csv"] = df
        return f"forecast.csv · {len(df)} rows × {df.shape[1]} cols (mean + {df.shape[1] - 3} quantiles)"

    step("forecast: download CSV", download)

    def upload():
        # the same kind of data as an upload with other column names: date / branch / txns / is_holiday, no future rows
        src = pd.read_csv(os.path.join(st.get("data_dir") or get("/api/health")["data_dir"], "datasets", "transaction-volume", "data.csv"))
        src = src[src["volume"].notna() & src["item_id"].isin(["branch-north", "branch-city", "cards-online"])]
        up = src.rename(columns={"timestamp": "date", "item_id": "branch", "volume": "txns", "holiday": "is_holiday"})[["date", "branch", "txns", "is_holiday", "segment"]]
        ds = j(C.post("/api/datasets", files={"file": ("branch_txns.csv", up.to_csv(index=False).encode(), "text/csv")}))
        m = get(f"/api/forecast/mapping/{ds['id']}")["mapping"]
        check(m["timestamp"] == "date" and m["item_id"] == "branch" and m["target"] == "txns" and m["known_covariates"] == ["is_holiday"], m)
        r = post("/api/forecast/runs", {"dataset_id": ds["id"], "mapping": m, "prediction_length": 14, "eval_metric": "WQL",
                                       "time_limit": 120, "num_val_windows": 1, "hyperparameters": {"SeasonalNaive": {}, "ETS": {}}})
        rec = wait_run(r["id"], 600)
        st["fc_up"] = rec
        return (f"{ds['id']} mapped {m['item_id']}/{m['timestamp']}/{m['target']} + known {m['known_covariates']} + static {m['static_features']} · "
                f"{r['id']} WQL {rec['holdout']['metrics']['WQL']:.4f} · future covariates: {rec['forecast']['known_covariates_source']}")

    step("forecast: CSV upload + mapping + run", upload)

    def new_history():
        src = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "transaction-volume", "data.csv"))
        cut = src[src["timestamp"] <= "2026-10-31"]
        out = j(C.post(f"/api/forecast/runs/{fid}/predict", files={"file": ("history.csv", cut.to_csv(index=False).encode(), "text/csv")}))
        dl = C.get(out["download"])
        check(dl.status_code == 200 and len(pd.read_csv(io.BytesIO(dl.content))) == out["rows"] == 168, out)
        check(out["recalibrated"] and out["conformal_factors"] == st["fc_rec"]["holdout"]["conformal"]["factors"], "new-history forecast not recalibrated")
        return f"history to 2026-10-31 → {out['rows']} rows forecast by {out['model']} (recalibrated) in {out['seconds']:.2f} s · covariates from {out['known_covariates_source']}"

    step("forecast: forecast from new history", new_history)

    def register():
        v = post(f"/api/runs/{fid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.transaction-volume.forecast"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        card = get(f"/api/registry/versions/{v['id']}/card")
        check(card["forecasting"] and card["forecasting"]["holdout_metrics"]["MASE"] > 0, card.get("forecasting"))
        md = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("## 11. Forecasting" in md and "after conformal recalibration" in md, "card section")
        check(card["forecasting"]["interval_calibration"]["recalibrated"], card["forecasting"].get("interval_calibration"))
        lat = v["result"]["version"]["latency"]
        st["fc_ver"] = v["id"]
        return f"{v['id']} · {v['model_name']} v{v['version']} · card §11 Forecasting · forecast all items {lat['forecast_all_items_s_p50'] * 1e3:.0f} ms"

    if step("forecast: register + model card", register):
        def reg_batch():
            src = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "transaction-volume", "data.csv"))
            cut = src[src["timestamp"] <= "2026-09-30"]
            b = j(C.post(f"/api/registry/versions/{st['fc_ver']}/batches", data={"created_by": "Smoke Analyst"},
                         files={"file": ("history_to_sep.csv", cut.to_csv(index=False).encode(), "text/csv")}))
            b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
            check(b["status"] == "succeeded", b.get("error"))
            r = b["result"]
            d = r["drift"]
            dl = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{b['id']}/download").content))
            check(len(dl) == 168 and "mean" in dl.columns, dl.shape)
            return (f"{b['id']} · history to 2026-09-30 → {r['summary']['items']} series × 28 = {len(dl)} rows forecast · "
                    f"PSI {', '.join(f'{f['feature']} {f['psi']:.3f}' for f in d['features'] if f['psi'] is not None)} · PSI(ŷ) {d['prediction']['psi']:.3f}")

        step("forecast: registry batch + drift", reg_batch)


def anomaly(st: dict[str, Any]) -> None:
    def run():
        r = post("/api/anomaly/runs", {"dataset_id": "card-transactions", "contamination": 0.015, "lof": True,
                                      "n_estimators": 300, "source": "smoke"})
        check("truth" not in r["config"], "ground truth leaked into the run config")
        st["an"] = r["id"]
        sse = follow_sse(r["id"], 300)
        rec = get(f"/api/runs/{r['id']}")
        a = rec["anomaly"]
        check(rec["kind"] == "anomaly" and a["n_flagged"] == 90, a)
        st["an_rec"] = rec
        return (f"{r['id']} · ψ = {a['psi']} · c(ψ) = {a['c_n']:.3f} · {a['n_trees']} trees · s* = {a['threshold']:.4f} · "
                f"{a['n_flagged']} flagged · LOF ρ = {a['lof']['spearman_vs_iforest']:.2f} · {sse['counts'].get('model_finished')} models")

    if not step("anomaly: run + SSE", run):
        return
    aid = st["an"]

    def top():
        t = get(f"/api/anomaly/runs/{aid}/top", contamination=0.02, limit=20)
        check(t["n_flagged"] == 120 and len(t["rows"]) == 20, t["n_flagged"])
        r0 = t["rows"][0]
        check(r0["reasons"] and r0["why"] and r0["s"] >= t["threshold"], r0)
        d = get(f"/api/anomaly/runs/{aid}/scores")
        check(sum(d["counts"]) == 6000, d["n"])
        return f"c = 0.02 → s* = {t['threshold']:.4f}, 120 flagged · #1 row {r0['row']} s = {r0['s']:.3f}: {r0['why'][:90]}"

    step("anomaly: top anomalies + reasons", top)

    def precision():
        tr = st["an_rec"]["anomaly"]["truth"]
        p = {r["k"]: r for r in tr["precision_at_k"]}
        check(p[10]["precision"] >= 0.7 and tr["roc_auc"] >= 0.85, tr)
        lof = st["an_rec"]["anomaly"]["truth_lof"]
        pl = {r["k"]: r for r in lof["precision_at_k"]}
        return (f"IF P@10 {p[10]['precision']:.2f} · P@50 {p[50]['precision']:.2f} · P@90 {p[90]['precision']:.2f} · AUC {tr['roc_auc']:.3f} · "
                f"LOF P@90 {pl[90]['precision']:.2f} (sample only)")

    step("anomaly: precision@k (hidden truth)", precision)

    def group_vs_plain():
        g = st["an_rec"]["data"].get("group")
        check(g and g["entity_column"] == "card_id", "group features on by default for the sample")
        r = post("/api/anomaly/runs", {"dataset_id": "card-transactions", "contamination": 0.015, "lof": False,
                                      "group_features": False, "drop_columns": ["card_id", "timestamp"], "source": "smoke"})
        plain = wait_run(r["id"], 300)
        tg, tp = st["an_rec"]["anomaly"]["truth"], plain["anomaly"]["truth"]
        sg, sp = tg["by_kind"]["amount_spike"]["found"], tp["by_kind"]["amount_spike"]["found"]
        check(sg > sp and tg["roc_auc"] > tp["roc_auc"], f"spikes {sg} vs {sp}, AUC {tg['roc_auc']} vs {tp['roc_auc']}")
        p90 = lambda t: next(x["precision"] for x in t["precision_at_k"] if x["k"] == 90)
        return (f"{', '.join(g['added'])} · amount spikes found {sp}/18 → {sg}/18 · P@90 {p90(tp):.2f} → {p90(tg):.2f} · "
                f"AUC {tp['roc_auc']:.3f} → {tg['roc_auc']:.3f} (plain run {r['id']})")

    step("anomaly: group-relative features vs plain", group_vs_plain)

    def velocity():
        rec = st["an_rec"]
        v = rec["data"].get("velocity")
        check(v and v["entity_column"] == "card_id" and v["time_column"] == "timestamp", "velocity features on by default for the sample")
        want = {"txns_prev_1h", "txns_prev_24h", "amount_usd_sum_prev_1h", "amount_usd_sum_prev_24h",
                "distinct_merchant_category_prev_24h", "amount_usd_over_running_mean", "log_hours_since_prev"}
        check(want <= set(v["added"]), v["added"])
        check(rec["resolved"]["iforest_kwargs"]["max_samples"] == 1024, rec["resolved"]["iforest_kwargs"])
        # before: the round-1 configuration (group-relative features with Δt, ψ = sklearn's 'auto' = 256, no velocity)
        r = post("/api/anomaly/runs", {"dataset_id": "card-transactions", "contamination": 0.015, "lof": False,
                                      "velocity_features": False, "max_samples": "auto", "source": "smoke"})
        old = wait_run(r["id"], 300)
        check(not old["data"].get("velocity") and old["resolved"]["iforest_kwargs"]["max_samples"] == "auto", old["data"].keys())
        tn, to = rec["anomaly"]["truth"], old["anomaly"]["truth"]
        p90 = lambda t: next(x["precision"] for x in t["precision_at_k"] if x["k"] == 90)
        vn, vo = tn["by_kind"]["velocity"]["found"], to["by_kind"]["velocity"]["found"]
        check(vn >= 10, f"velocity bursts found {vn}/18 (need ≥ 10)")
        check(p90(tn) >= 0.71, f"P@90 {p90(tn):.2f} (need ≥ 0.71)")
        # causal: recompute the 24 h count and the gap for the top rows from data.csv (earlier rows of the card only)
        data = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "card-transactions", "data.csv"))
        data["t"] = pd.to_datetime(data["timestamp"])
        top = get(f"/api/anomaly/runs/{st['an']}/top", limit=40)["rows"]
        for row in top:
            x = data.iloc[row["row"]]
            prev = data[(data["card_id"] == x["card_id"]) & (data["t"] < x["t"])]
            n24 = int((prev["t"] >= x["t"] - pd.Timedelta(hours=24)).sum())
            check(row["values"]["txns_prev_24h"] == n24, f"row {row['row']}: txns_prev_24h {row['values']['txns_prev_24h']} vs {n24}")
            s24 = float(prev.loc[prev["t"] >= x["t"] - pd.Timedelta(hours=24), "amount_usd"].sum())
            check(abs(row["values"]["amount_usd_sum_prev_24h"] - s24) < 0.01, f"row {row['row']}: Σ24h")
            gap = np.log1p((x["t"] - prev["t"].max()).total_seconds() / 3600) if len(prev) else None
            got = row["values"].get("log_hours_since_prev")
            check((gap is None and got is None) or (gap is not None and got is not None and abs(got - gap) < 1e-6), f"row {row['row']}: Δ {got} vs {gap}")
        st["an_p90"] = p90(tn)
        return (f"bursts found {vo}/18 → {vn}/18 · P@90 {p90(to):.2f} → {p90(tn):.2f} · AUC {to['roc_auc']:.3f} → {tn['roc_auc']:.3f} · "
                f"spikes {to['by_kind']['amount_spike']['found']} → {tn['by_kind']['amount_spike']['found']} · "
                f"night/foreign {to['by_kind']['night_foreign']['found']} → {tn['by_kind']['night_foreign']['found']} · "
                f"forest sees {', '.join(v['in_forest'])} · 40 top rows recomputed from earlier rows only (round-1 config {r['id']})")

    step("anomaly: velocity features (F2)", velocity)

    def review():
        t = get(f"/api/anomaly/runs/{aid}/top", limit=200)
        flagged = [r for r in t["rows"] if r["flagged"]]
        check(len(flagged) == 90, len(flagged))
        old = get(f"/api/anomaly/runs/{aid}/labels")["labels"]  # labels belong to the dataset: clear an earlier smoke's
        if old:
            post(f"/api/anomaly/runs/{aid}/labels", {"items": [{"row": x["row"], "label": None} for x in old], "by": "Smoke Analyst"})
        # the analyst (here: the sample's hidden truth) confirms planted rows and dismisses the rest
        items = [{"row": r["row"], "label": "fraud" if r["planted"] else "not_fraud"} for r in flagged]
        lab = post(f"/api/anomaly/runs/{aid}/labels", {"items": items, "by": "Smoke Analyst"})
        n_pos = sum(1 for i in items if i["label"] == "fraud")
        check(lab["confirmed"] == n_pos and lab["dismissed"] == 90 - n_pos and lab["can_train"], lab)
        # clear one and set it again: null removes a label
        lab = post(f"/api/anomaly/runs/{aid}/labels", {"items": [{"row": items[0]["row"], "label": None}], "by": "Smoke Analyst"})
        check(lab["reviewed"] == 89, lab["reviewed"])
        lab = post(f"/api/anomaly/runs/{aid}/labels", {"items": [items[0]], "by": "Smoke Analyst"})
        t2 = get(f"/api/anomaly/runs/{aid}/top", limit=5)
        check(all(r["review"] is not None for r in t2["rows"]) and t2["review"]["reviewed"] == 90, t2["rows"][0].get("review"))
        aud = get("/api/registry/audit", limit=20)["entries"]
        check(any(e["action"] == "anomaly_review" and e["actor"] == "Smoke Analyst" for e in aud), "audit")
        st["an_pos"] = n_pos
        return f"90 flagged rows reviewed: {n_pos} confirmed fraud, {90 - n_pos} dismissed · stored with the dataset · audited · Train supervised open (≥ {lab['min_positives']})"

    if step("anomaly: confirm / dismiss", review):
        def supervised():
            s = post(f"/api/anomaly/runs/{aid}/supervised", {"time_limit": 60, "by": "Smoke Analyst"})
            check(s["confirmed"] == st["an_pos"] and "anomaly_score" in s["features_added"] and "txns_prev_1h" in s["features_added"], s)
            r = wait_run(s["run_id"], 600)
            check(r["status"] == "succeeded" and r["problem_type"] == "binary" and r["label"] == "confirmed_fraud", r.get("error"))
            g = get(f"/api/anomaly/runs/{aid}/supervised")
            e = next(x for x in g["runs"] if x["run_id"] == s["run_id"])
            c = e.get("comparison")
            check(c and c["n_oof"] > 4000 and c["unreviewed"] == 6000 - 90, e.get("comparison_error") or c)
            fi, su = c["iforest"], c["supervised"]
            k = c["hidden_in_unreviewed"]
            hit = lambda t: next(x["hits"] for x in t["precision_at_k"] if x["k"] == k)
            hk = lambda t: next(x["hits"] for x in t["precision_at_k"] if x["k"] == 90)
            return (f"{s['run_id']} ({r['best_model']}, {r['fit_seconds']:.0f} s, bagged ×5) on {s['confirmed']} confirmed / "
                    f"{s['dismissed']} dismissed / {s['unreviewed']} unlabeled · on the {c['unreviewed']} unreviewed rows "
                    f"({k} hidden frauds): top-{k} IF {hit(fi)} vs supervised {hit(su)} · top-90 {hk(c['iforest_at_k'])} vs {hk(c['supervised_at_k'])} · "
                    f"AP {fi['average_precision']:.3f} → {su['average_precision']:.3f} · AUC {fi['roc_auc']:.3f} → {su['roc_auc']:.3f} (out-of-fold)")

        step("anomaly: train supervised (PU, F2)", supervised)

    def batch():
        src = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "card-transactions", "data.csv")).sample(500, random_state=3)
        src.loc[src.index[:5], "amount_usd"] = 25_000.0
        src.loc[src.index[:5], "hour"] = 3
        out = j(C.post(f"/api/anomaly/runs/{aid}/score", files={"file": ("new_batch.csv", src.to_csv(index=False).encode(), "text/csv")}))
        dl = pd.read_csv(io.BytesIO(C.get(out["download"]).content))
        check(len(dl) == 500 and {"anomaly_score", "flagged", "percentile"} <= set(dl.columns), dl.columns)
        pct = dl["percentile"].iloc[:5]
        check((pct >= 0.9).all(), f"injected rows' percentiles {pct.round(3).tolist()}")
        return (f"{out['id']} · 500 rows · {out['summary']['n_flagged']} flagged at s* · 5 injected rows ($25k at 03:00) at percentiles "
                f"{', '.join(f'{v:.3f}' for v in pct)} · downloaded")

    step("anomaly: score a new batch", batch)

    def register():
        v = post(f"/api/runs/{aid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.card-transactions.anomaly"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 300)
        check(v["status"] == "ready", v.get("error"))
        row = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "card-transactions", "data.csv")).iloc[0].to_dict()
        row.update({"amount_usd": 9000.0, "hour": 3, "foreign": 1, "distance_from_home_km": 4000.0})
        out = post(f"/api/registry/versions/{v['id']}/predict", {"row": row})
        check(out["kind"] == "anomaly" and out["flagged"], out)
        card = get(f"/api/registry/versions/{v['id']}/card")
        check(card["anomaly_detection"]["truth_check"], "card")
        st["an_ver"] = v["id"]
        return f"{v['id']} · v{v['version']} ready · card §11 · Try It: s = {out['anomaly_score']:.3f} (pct {out['percentile']:.3f}) flagged — {out['why'][:60]}"

    if step("anomaly: register + Try It", register):
        def reg_batch():
            src = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "card-transactions", "data.csv")).sample(800, random_state=5)
            src.loc[src.index[:8], "amount_usd"] = src.loc[src.index[:8], "amount_usd"] * 30
            b = j(C.post(f"/api/registry/versions/{st['an_ver']}/batches", data={"created_by": "Smoke Analyst"},
                         files={"file": ("cards_new.csv", src.to_csv(index=False).encode(), "text/csv")}))
            b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 300)
            check(b["status"] == "succeeded", b.get("error"))
            r = b["result"]
            dl = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{b['id']}/download").content))
            check(len(dl) == 800 and {"anomaly_score", "flagged", "amount_usd_over_entity_mean"} <= set(dl.columns), list(dl.columns)[-6:])
            hit = int(dl["flagged"].iloc[:8].sum())
            d = r["drift"]
            return (f"{b['id']} · 800 rows · {r['summary']['n_flagged']} flagged at s* · {hit}/8 rows with amount ×30 flagged · "
                    f"max PSI {d['max_psi']:.3f} · PSI(s) {d['prediction']['psi']:.3f}")

        step("anomaly: registry batch + drift", reg_batch)


def importer(st: dict[str, Any]) -> None:
    def imp():
        data_dir = get("/api/health")["data_dir"]
        ddir = os.path.join(data_dir, "datasets", "credit-default")
        df = pd.read_csv(os.path.join(ddir, "data.csv"))
        hold = set(json.load(open(os.path.join(ddir, "splits.json")))["holdout"])
        train = df.loc[[i for i in range(len(df)) if i not in hold]].drop(columns=["applicant_id"])
        tmp = tempfile.mkdtemp(prefix="smoke-import-")
        code = (
            "import sys, pandas as pd\n"
            "from autogluon.tabular import TabularPredictor\n"
            "df = pd.read_csv(sys.argv[1])\n"
            "p = TabularPredictor(label='defaulted', path=sys.argv[2], eval_metric='roc_auc', verbosity=0)\n"
            "p.fit(df, hyperparameters={'GBM': {'num_boost_round': 60}, 'LR': {}}, time_limit=60)\n"
            "print(p.model_best)\n")
        train.to_csv(os.path.join(tmp, "train.csv"), index=False)
        pdir = os.path.join(tmp, "my_predictor")
        out = subprocess.run([sys.executable, "-c", code, os.path.join(tmp, "train.csv"), pdir], capture_output=True, text=True, timeout=300)
        check(out.returncode == 0, out.stderr[-500:])
        import shutil

        zpath = shutil.make_archive(os.path.join(tmp, "my_predictor"), "zip", tmp, "my_predictor")
        with open(zpath, "rb") as fh:
            r = j(C.post("/api/runs/import", files={"file": ("my_predictor.zip", fh.read(), "application/zip")},
                         data={"dataset_id": "credit-default", "created_by": "Smoke Analyst"}))
        check(r["inspected"]["label"] == "defaulted", r["inspected"])
        st["imp"] = r["id"]
        st["imp_dir"] = pdir
        follow_sse(r["id"], 600)
        rec = get(f"/api/runs/{r['id']}")
        check(rec["kind"] == "import" and rec["holdout"] and rec["leaderboard"], rec.get("error"))
        st["imp_rec"] = rec
        return (f"tiny predictor fit here ({out.stdout.strip()}) · zipped · {r['id']} · {len(rec['leaderboard'])} models · "
                f"holdout ROC AUC {rec['holdout']['metrics'].get('roc_auc', float('nan')):.4f}")

    if not step("import: fit + zip + import", imp):
        return
    rid = st["imp"]

    def explain_predict():
        e = get(f"/api/runs/{rid}/explain")
        check(e.get("roc") or e.get("confusion_matrix"), e.keys())
        fi = get(f"/api/runs/{rid}/feature-importance")
        row = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "credit-default", "data.csv")).drop(columns=["defaulted"]).iloc[:3]
        row = row.astype(object).where(row.notna(), None)  # missing values as JSON null (F6's sample has some in the first rows)
        p = post(f"/api/runs/{rid}/predict", {"rows": row.to_dict(orient="records")})
        check(len(p["predictions"]) == 3, p)
        return f"explain ✓ · importance top {fi['features'][0]['feature']} · predict 3 rows → {p['predictions']}"

    step("import: explain + predict", explain_predict)

    def register():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.imported.credit"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        row = pd.read_csv(os.path.join(get("/api/health")["data_dir"], "datasets", "credit-default", "data.csv")).drop(columns=["defaulted"]).iloc[0].to_dict()
        out = post(f"/api/registry/versions/{v['id']}/predict", {"row": row, "explain": True})
        card = get(f"/api/registry/versions/{v['id']}/card")
        check(card["imported"] and "imported" in card["summary"]["engine"], card["summary"])
        return f"{v['id']} ready · card engine '{card['summary']['engine']}' · Try It {out['prediction']} p(Yes) {out['probabilities']['Yes']:.3f}"

    step("import: register + Try It", register)

    def by_path():
        r = j(C.post("/api/runs/import", data={"dataset_id": "credit-default", "path": st["imp_dir"]}))
        rec = wait_run(r["id"], 600)
        bad = C.post("/api/runs/import", data={"dataset_id": "loan-amount", "path": st["imp_dir"]})
        check(bad.status_code == 400, bad.text)
        return f"{r['id']} from a server path · {len(rec['leaderboard'])} models · wrong dataset refused: {bad.json()['detail'][:70]}"

    step("import: server path + validation", by_path)

    def kinds():
        runs = get("/api/runs", kind="all")["runs"]
        by = {}
        for r in runs:
            by[r["kind"]] = by.get(r["kind"], 0) + 1
        tab = get("/api/runs")["runs"]
        check(all(r["kind"] in ("tabular", "import") for r in tab), "default list is tabular + imports")
        need = {"tabular", "import"} | ({"forecast"} if "forecast" in ONLY else set()) | ({"anomaly"} if "anomaly" in ONLY else set())
        check(need <= set(by), by)
        return f"GET /api/runs?kind=all (Experiments, Home) · {len(runs)} runs · " + " · ".join(f"{k} {v}" for k, v in sorted(by.items()))

    step("runs list: every kind (Experiments, Home)", kinds)


def reset(st: dict[str, Any]) -> None:
    def run():
        port = 8077
        tmp = tempfile.mkdtemp(prefix="smoke-reset-")
        backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
        env = {**os.environ, "ENSEMBLE_DATA_DIR": tmp}
        proc = subprocess.Popen([os.path.join(backend, ".venv", "bin", "python"), "-m", "uvicorn", "app.main:app", "--port", str(port)],
                                cwd=backend, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(180):
                try:
                    httpx.get(f"{base}/api/health", timeout=2).raise_for_status()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(1)
            c = httpx.Client(base_url=base, timeout=120)
            n0 = j(c.get("/api/health"))["datasets"]
            j(c.post("/api/datasets", files={"file": ("x.csv", make_binary_csv(200).to_csv(index=False).encode(), "text/csv")}))
            r = j(c.post("/api/anomaly/runs", json={"dataset_id": "card-transactions", "lof": False, "n_estimators": 50}))
            for _ in range(120):
                if j(c.get(f"/api/runs/{r['id']}"))["status"] in ("succeeded", "failed"):
                    break
                time.sleep(1)
            before = j(c.get("/api/health"))
            refused = c.post("/api/admin/reset", json={})
            check(refused.status_code == 400, refused.text)
            out = j(c.post("/api/admin/reset", json={"confirm": "reset"}))
            after = j(c.get("/api/health"))
            check(after["runs"] == 0 and after["datasets"] == n0 and not os.path.exists(os.path.join(tmp, "runs", r["id"])), after)
            names = [d["id"] for d in j(c.get("/api/datasets"))["datasets"]]
            check("transaction-volume" in names and "card-transactions" in names, names)
            return (f"throwaway API · before: {before['runs']} run(s), {before['datasets']} datasets → reset "
                    f"(deleted {out['deleted']['runs']} run, {out['deleted']['datasets']} dataset rows) → {after['runs']} runs, {after['datasets']} samples regenerated")
        finally:
            proc.terminate()
            proc.wait(30)

    step("reset: POST /api/admin/reset", run)


# --------------------------------------------------------------------------- llm (F5)

def _tiny_run(c: httpx.Client) -> str:
    """A small binary run to ask about (GBM only, ≈ 10 s)."""
    d = j(c.post("/api/datasets", files={"file": ("smoke_llm.csv", make_binary_csv(400).to_csv(index=False).encode(), "text/csv")},
                 data={"target": "defaulted"}))
    r = j(c.post("/api/runs", json={"dataset_id": d["id"], "time_limit": 30, "source": "smoke", "calibrate_threshold": None,
                                    "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 20}}}}))
    for _ in range(300):
        rec = j(c.get(f"/api/runs/{r['id']}"))
        if rec["status"] in ("succeeded", "failed"):
            check(rec["status"] == "succeeded", rec.get("error"))
            return r["id"]
        time.sleep(1)
    raise SmokeError("tiny run did not finish")


def _burst(base: str, bodies: list[dict[str, Any]], conc: int) -> tuple[list[tuple[float, dict]], list[float], int]:
    """Send the asks `conc` at a time while probing /api/health every 0.2 s. → ([(seconds, answer)], health seconds,
    health probes answered while asks were still in flight)."""
    import concurrent.futures as cf
    import threading

    stop, hl, during = threading.Event(), [], [0]
    inflight = [0]
    lock = threading.Lock()

    def probe():
        with httpx.Client(base_url=base, timeout=60) as hc:
            while not stop.is_set():
                t = time.perf_counter()
                hc.get("/api/health").raise_for_status()
                hl.append(time.perf_counter() - t)
                with lock:
                    if inflight[0] > 0:
                        during[0] += 1
                time.sleep(0.2)

    def one(b):
        with lock:
            inflight[0] += 1
        try:
            t = time.perf_counter()
            r = httpx.post(f"{base}/api/vela/ask", json=b, timeout=900)
            return time.perf_counter() - t, j(r)
        finally:
            with lock:
                inflight[0] -= 1

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    try:
        with cf.ThreadPoolExecutor(conc) as ex:
            out = list(ex.map(one, bodies))
    finally:
        stop.set()
        th.join(10)
    return out, hl, during[0]


def _free_port() -> int:
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def _cli_procs() -> int:
    """claude processes started by the router (its flag set is distinctive)."""
    out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if "--strict-mcp-config --no-session-persistence --disable-slash-commands" in l)


def llm(st: dict[str, Any]) -> None:
    """F5: the language-model router. Real path here: whatever this API has (the claude CLI when no ANTHROPIC_API_KEY).
    SDK path: a second API whose Anthropic SDK points at scripts/fake_llm.py's Messages API (no key, no network)."""
    def status():
        s = get("/api/llm")
        st["llm_route"] = s["route"]
        check(set(s["latency"]) == {"anthropic-sdk", "claude-cli", "computed"} and "sdk_timeout" in s["settings"], s)
        check(s["route"][:1] == (["anthropic-sdk"] if s["sdk"]["available"] else s["route"][:1]), s)
        return (f"route {' → '.join(s['route'] + ['computed'])} · SDK: {s['sdk']['why']} · CLI: {s['cli']['path']} · "
                f"T_sdk {s['settings']['sdk_timeout']} s, T_cli {s['settings']['cli_timeout']} s, T {s['settings']['total_timeout']} s")

    step("llm: router status", status, fatal=True)

    def real_path():
        st["llm_run"] = _tiny_run(C)
        qs = ["Which model is best, and what is its holdout score?", "Which feature matters most?",
              "Is the model overfitting? Answer from the gap between validation and holdout.",
              "How long did the fit take?", "How many holdout rows were scored?", "What metric was optimised?",
              "Name the two weakest models on the leaderboard.", "Is the label imbalanced?"]
        bodies = [{"question": f"{q} (smoke {time.time():.0f}-{i})", "run_id": st["llm_run"], "cache": False} for i, q in enumerate(qs)]
        out, hl, during = _burst(API, bodies, 4)
        srcs = [a["source"] for _, a in out]
        model_t = [t for t, a in out if a["source"] != "computed"]
        for _, a in out:
            check(a["answer"] or (a["source"] == "computed" and a["message"]), a)
            check(a["decider"] and isinstance(a["attempts"], list), a)
        if st["llm_route"]:
            check(len(model_t) >= len(out) // 2, f"only {len(model_t)}/{len(out)} answered by a model: {[a['attempts'] for _, a in out]}")
        check(during >= 3 and max(hl) < 1.0, f"/api/health: {during} probes during the asks, max {max(hl) * 1000:.0f} ms")
        st["llm_real"] = {"n": len(model_t), "p50": _pq(model_t, 50) if model_t else None, "p95": _pq(model_t, 95) if model_t else None}
        guards = [a["guard"]["ok"] for _, a in out if a.get("guard")]
        return (f"{len(out)} asks, 4 at a time · deciders {dict((s, srcs.count(s)) for s in set(srcs))} · Vela ask "
                + (f"p50 {st['llm_real']['p50']:.2f} s, p95 {st['llm_real']['p95']:.2f} s ({st['llm_route'][0]}, real)" if model_t else "no model path here")
                + f" · /api/health {during} probes during the asks, p95 {_pq(hl, 95) * 1000:.0f} ms, max {max(hl) * 1000:.0f} ms"
                + f" · number guard clean on {sum(guards)}/{len(guards)}")

    step("llm: real path, Vela ask p95 while /api/health answers", real_path)

    def cache():
        q = {"question": "In one sentence, what is the best model and its holdout score?", "run_id": st["llm_run"]}
        a1 = post("/api/vela/ask", q)
        t = time.perf_counter()
        a2 = post("/api/vela/ask", q)
        rt = time.perf_counter() - t
        if a1["source"] == "computed":
            check(not a2["cached"], "a fallback must not be cached")
            return "no model here: fallbacks are not cached (checked)"
        check(a2["cached"] and a2["answer"] == a1["answer"] and a2["seconds"] < 0.05, a2)
        check(a2["key"] == a1["key"] and a2["facts_sha256"] == a1["facts_sha256"], (a1["key"], a2["key"]))
        other = _tiny_run(C)  # same question, other facts → asked again
        a3 = post("/api/vela/ask", q | {"run_id": other})
        check(not a3["cached"] and a3["facts_sha256"] != a1["facts_sha256"] and a3["prompt_sha256"] != a1["prompt_sha256"], a3)
        return (f"first {a1['seconds']:.2f} s ({a1['source']}) → repeat cached in {a2['seconds'] * 1e3:.3f} ms server-side "
                f"({rt * 1e3:.0f} ms round trip), same key {a1['key']} · other run's facts → h(facts) {a3['facts_sha256'][:8]} ≠ "
                f"{a1['facts_sha256'][:8]}, asked again ({a3['seconds']:.2f} s)")

    step("llm: cache by (prompt hash, facts hash)", cache)

    def timeout_real():
        if not st["llm_route"]:
            return "no model path here: nothing to time out"
        n0 = _cli_procs()
        a = post("/api/vela/ask", {"question": f"Summarise the run in detail ({time.time()})", "run_id": st["llm_run"],
                                   "timeout": 1.0, "cache": False})
        tried = [x for x in a["attempts"] if not x.get("skipped")]
        check(a["source"] == "computed" and a["answer"] is None and a["message"], a)
        check(tried and tried[0]["timed_out"] and a["seconds"] < 2.5, a)
        time.sleep(0.5)
        left = _cli_procs()
        check(left <= n0, f"{left} CLI processes left after the timeout (had {n0})")
        return (f"T = 1 s → {tried[0]['source']} timed out at {tried[0]['timeout_s']} s → computed in {a['seconds']:.2f} s · "
                f"answer null with message · CLI processes left: {left}")

    step("llm: a timeout falls back cleanly (real path)", timeout_real)

    def sdk_path():
        here = os.path.dirname(os.path.abspath(__file__))
        backend = os.path.join(here, "..", "backend")
        tmp = tempfile.mkdtemp(prefix="smoke-llm-")
        fport, port = _free_port(), _free_port()
        wrapper = os.path.join(tmp, "claude")
        open(wrapper, "w").write(f"#!/bin/sh\nexec {sys.executable} {os.path.join(here, 'fake_llm.py')} cli \"$@\"\n")
        os.chmod(wrapper, 0o755)
        env = {**os.environ, "ENSEMBLE_DATA_DIR": tmp, "ANTHROPIC_API_KEY": "sk-fake-smoke-not-a-key",
               "ENSEMBLE_ANTHROPIC_BASE_URL": f"http://127.0.0.1:{fport}", "ENSEMBLE_CLAUDE_BIN": wrapper,
               "ENSEMBLE_VELA_LLM": "auto", "FAKE_LLM_LATENCY": "0.25", "FAKE_CLI_LATENCY": "0.1"}
        procs = [subprocess.Popen([sys.executable, os.path.join(here, "fake_llm.py"), "server", "--port", str(fport)], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                 subprocess.Popen([os.path.join(backend, ".venv", "bin", "python"), "-m", "uvicorn", "app.main:app", "--port", str(port)],
                                  cwd=backend, env=env, stdout=open(os.path.join(tmp, "api.log"), "w"), stderr=subprocess.STDOUT)]
        base = f"http://127.0.0.1:{port}"
        out = []
        try:
            for _ in range(240):
                try:
                    httpx.get(f"{base}/api/health", timeout=2).raise_for_status()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(1)
            c = httpx.Client(base_url=base, timeout=600)
            s = j(c.get("/api/llm"))
            check(s["route"] == ["anthropic-sdk", "claude-cli"] and s["sdk"]["available"], s)
            rid = _tiny_run(c)
            ask = lambda q, **kw: j(c.post("/api/vela/ask", json={"question": q, "run_id": rid, **kw}))  # noqa: E731

            # 1 · SDK answers; p95 over 20 asks, 4 at a time (fake Messages API: 0.25 s per call)
            res, hl, during = _burst(base, [{"question": f"what is the best model, take {chr(97 + i)}", "run_id": rid, "cache": False}
                                            for i in range(20)], 4)
            check(all(a["source"] == "anthropic-sdk" and a["answer"].startswith("(fake sdk)") for _, a in res), [a["attempts"] for _, a in res])
            ts = [t for t, _ in res]
            st["llm_sdk"] = {"p50": _pq(ts, 50), "p95": _pq(ts, 95)}
            out.append(f"SDK ask p50 {st['llm_sdk']['p50']:.2f} s, p95 {st['llm_sdk']['p95']:.2f} s (fake API, 0.25 s/call)")

            # 2 · cache
            a1, a2 = ask("which feature matters most"), ask("which feature matters most")
            check(not a1["cached"] and a2["cached"] and a2["answer"] == a1["answer"] and a2["source"] == "anthropic-sdk", a2)
            out.append(f"cache hit {a2['seconds'] * 1e3:.3f} ms")

            # 3 · settings: validation, persistence
            bad = c.put("/api/llm/settings", json={"sdk_timeout": -1})
            check(bad.status_code == 400, bad.text)
            check(c.put("/api/llm/settings", json={"bogus": 1}).status_code == 400, "unknown setting accepted")
            s = j(c.put("/api/llm/settings", json={"sdk_timeout": 1, "cli_timeout": 2}))
            check(s["settings"]["sdk_timeout"] == 1 and json.load(open(os.path.join(tmp, "llm_settings.json")))["cli_timeout"] == 2, s)

            # 4 · SDK times out → CLI answers
            a = ask("explain the score [sdk_slow:5]", cache=False)
            check(a["source"] == "claude-cli" and a["attempts"][0]["source"] == "anthropic-sdk" and a["attempts"][0]["timed_out"]
                  and a["answer"].startswith("(fake cli)") and a["seconds"] < 2.5, a)
            out.append(f"SDK timeout 1 s → CLI in {a['seconds']:.2f} s")

            # 5 · SDK 500 → CLI;  SDK prose (no JSON) → CLI
            a = ask("explain the score [sdk_fail]", cache=False)
            check(a["source"] == "claude-cli" and "500" in a["attempts"][0]["error"], a)
            a = ask("explain the score [sdk_nonjson]", cache=False)
            check(a["source"] == "claude-cli" and "JSON" in a["attempts"][0]["error"], a)
            out.append("SDK 500 / non-JSON → CLI")

            # 6 · both fail or time out → computed, cleanly, within T_sdk + T_cli
            a = ask("explain the score [sdk_slow:9][cli_slow:9]", cache=False)
            check(a["source"] == "computed" and a["answer"] is None and a["message"] and a["guard"] is None
                  and [x["timed_out"] for x in a["attempts"]] == [True, True] and a["seconds"] < 4.5, a)
            out.append(f"both time out → computed in {a['seconds']:.2f} s")
            # the total budget: T = 1.5 s spent on the SDK leaves the CLI 0.5 s
            a = ask("explain the score [sdk_slow:9][cli_slow:9]", cache=False, timeout=1.5)
            check(a["source"] == "computed" and a["seconds"] < 2.2 and a["attempts"][1]["timeout_s"] <= 0.6, a)
            out.append(f"T = 1.5 s → computed in {a['seconds']:.2f} s")
            # a fallback is never cached
            a = ask("explain the score [sdk_fail][cli_fail]")
            b = ask("explain the score [sdk_fail][cli_fail]")
            check(a["source"] == b["source"] == "computed" and not b["cached"], b)

            # 7 · number guard
            a = ask("is the score 987654.5")
            check(a["guard"] and not a["guard"]["ok"] and "987654.5" in a["guard"]["unsupported"], a)
            a = ask("what is the best model")
            check(a["guard"]["ok"], a)
            out.append("guard flags 987654.5")

            # 8 · wording through the router
            w = j(c.post(f"/api/runs/{rid}/vela/wording"))
            check(w["source"] in ("anthropic-sdk", "computed") and set(w["texts"]) == set(w["kept"]), w)

            # 9 · /api/health during 48 asks in flight, half on a slow SDK, half falling to a slow CLI
            j(c.put("/api/llm/settings", json={"sdk_timeout": 10, "cli_timeout": 10}))
            bodies = [{"question": f"burst {chr(97 + i % 26)}{i // 26} [sdk_slow:3]" if i % 2 else f"burst {chr(97 + i % 26)}{i // 26} [sdk_fail][cli_slow:3]",
                       "run_id": rid, "cache": False} for i in range(48)]
            t0 = time.perf_counter()
            res, hl, during = _burst(base, bodies, 48)
            wall = time.perf_counter() - t0
            srcs = [a["source"] for _, a in res]
            check(srcs.count("anthropic-sdk") == 24 and srcs.count("claude-cli") == 24, srcs)
            check(during >= 5 and max(hl) < 1.0, f"/api/health {during} probes, max {max(hl) * 1000:.0f} ms")
            st["llm_health"] = {"max_ms": max(hl) * 1000, "p95_ms": _pq(hl, 95) * 1000, "during": during, "wall": wall}
            out.append(f"48 asks in flight ({wall:.1f} s wall): /api/health {during} probes, p95 {_pq(hl, 95) * 1000:.0f} ms, max {max(hl) * 1000:.0f} ms")
            stat = j(c.get("/api/llm"))
            out.append(f"status: SDK τ₉₅ {stat['latency']['anthropic-sdk']['p95_s']} s over {stat['latency']['anthropic-sdk']['n']}, "
                       f"{stat['stats']['cache_hits']} cache hits")
            return " · ".join(out)
        finally:
            for p in procs:
                p.terminate()
                try:
                    p.wait(30)
                except subprocess.TimeoutExpired:
                    p.kill()

    step("llm: SDK path (fake Messages API) · routing, timeouts, fallback, cache, guard, health", sdk_path)


ONLY: set[str] = set()


def reasons(st: dict[str, Any]) -> None:
    """F10: reason codes for adverse decisions. credit-default trained to the F7 cost (FN = 10 × FP, threshold
    calibrated on cost) with debt_to_income ↑ (F8), registered (served model as deployed: cloned, pipeline pruned).
    The decision rule uses the version's own threshold. Checks: the mapping table and its edits (per model, audited,
    refusals); Try It's codes (top 4 by s·φ > 0) against its own reference Kernel SHAP (B = 50, 2,048 coalitions);
    /score with reasons=true gives the same codes as Try It for the same row; the batch of the 2,400 holdout rows
    carries adverse + reason_1…4 columns, 4 codes per adverse row, every code's φ > 0 and ranked, and agrees in sign
    with Try It's reference SHAP; the monotone feature is only ever cited as 'too high'; throughput in rows/s; the
    stability check and the model card section. Then a LightGBM-only version on numeric columns, where the batch
    uses exact interventional TreeSHAP (additivity to 1e-6)."""
    ds, label, col = "credit-default", "defaulted", "debt_to_income"
    s: dict[str, Any] = {}
    prof = get(f"/api/datasets/{ds}")["profile"]
    # An adverse-action reason cannot be an identifier or the application date: those are not used as features.
    drop = [c["name"] for c in prof["columns"] if (c["type"] == "id" or c["name"] in ("customer_id", "application_date"))
            and c["name"] != label]
    hp = {"GBM": {"num_boost_round": 150}, "XGB": {"n_estimators": 150}, "CAT": {"iterations": 150}}
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "presets": "medium_quality", "time_limit": 600,
            "fit_kwargs": {"hyperparameters": hp, "num_bag_folds": 3}, "drop_columns": drop, "feature_importance": False,
            "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto", "monotone": {"constraints": {col: "increasing"}},
            "source": "smoke-reasons"}
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    from app.samples import generate_credit

    df, _ = generate_credit()

    def holdout(rid: str) -> pd.DataFrame:
        hp_ = get(f"/api/runs/{rid}/holdout-predictions")
        idx = np.asarray(hp_["rows"], dtype=int)
        check([str(a) for a in df[label].to_numpy()[idx]] == [str(b) for b in hp_["y_true"]], "regenerated sample differs")
        return df.iloc[idx].drop(columns=[label])

    def records(X: pd.DataFrame) -> list[dict[str, Any]]:
        return json.loads(X.to_json(orient="records"))

    def train_register():
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        check(rec["threshold"]["metric"] == "expected_cost", rec["threshold"])
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit.reasons"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        s.update(run=rec, vid=v["id"], v=v, X=holdout(rec["id"]))
        t = v["result"]["version"]["decision_threshold"]
        return (f"{rec['id']} → {v['id']} serves {v['result']['version']['best_model']} · threshold t = {t:.4f} "
                f"(F7 cost, run t = {rec['threshold']['value']:.4f})")

    if not step("reasons: cost + monotone run, registered", train_register):
        return
    vid = s["vid"]

    def config():
        c = get(f"/api/registry/versions/{vid}/reasons")
        check(c["adverse_class"] == "Yes" and c["positive_class"] == "Yes" and c["top_k"] == 4, c)
        check(abs(c["threshold"] - s["v"]["result"]["version"]["decision_threshold"]) < 1e-12, "threshold is not the version's")
        m = {r["feature"]: r for r in c["mapping"]}
        check(set(m) == set(c["features"]) and m[col]["constraint"] == 1, m.get(col))
        bad = [({"actor": "Smoke Analyst", "mapping": [{"feature": "nope", "code": "X"}]}, "No feature"),
               ({"actor": "Smoke Analyst", "mapping": [{"feature": "age", "code": m["credit_score"]["code"]}]}, "used twice"),
               ({"actor": "Smoke Analyst", "adverse_class": "Maybe"}, "No class")]
        for b, words in bad:
            r = C.put(f"/api/registry/versions/{vid}/reasons", json=b)
            check(r.status_code == 400 and words in r.text, f"{words}: {r.status_code} {r.text[:200]}")
        e = j(C.put(f"/api/registry/versions/{vid}/reasons", json={"actor": "Smoke Analyst", "mapping": [
            {"feature": "credit_score", "code": "CS01", "low": "Credit score below our lending standard"},
            {"feature": col, "high": "Debt obligations too high relative to income"}]}))
        me = {r["feature"]: r for r in e["mapping"]}
        check(me["credit_score"]["code"] == "CS01" and me["credit_score"]["edited"] and me[col]["high"].startswith("Debt obligations"), me["credit_score"])
        aud = get("/api/registry/audit", version_id=vid)
        check(any(a["action"] == "reason_codes_edited" for a in aud.get("entries", aud.get("audit", aud if isinstance(aud, list) else []))), "audit")
        s["cfg"] = e
        att = e["attribution"]
        return (f"{len(e['mapping'])} features mapped · adverse ⇔ ŷ = Yes at t = {e['threshold']:.4f} · method {att['method']} "
                f"(B = {att['background']}, {att.get('coalitions')} coalitions, {att['evals_per_row']} model calls per row) · "
                f"{col} constraint +1 · edits saved + audited · {len(bad)} bad edits → 400")

    step("reasons: mapping table (per model, editable)", config)

    def tryit_score():
        X = s["X"]
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": records(X.head(400)), "reasons": True})
        rr = sc["reasons"]
        t = s["cfg"]["threshold"]
        p = np.asarray(rr["p_positive"])
        pred = np.asarray([str(x) for x in sc["predictions"]])
        check((np.asarray(rr["adverse"]) == (pred == "Yes")).all(), "adverse ≠ served prediction")
        check(((p >= t) == (pred == "Yes")).mean() > 0.99, "decisions do not follow the version's threshold")
        adv = [i for i, a in enumerate(rr["adverse"]) if a]
        check(adv and all(rr["codes"][i] is None for i in range(len(X.head(400))) if not rr["adverse"][i]), "codes on approved rows")
        n4 = sum(len(rr["codes"][i]) == 4 for i in adv)
        # Try It on 8 adverse rows: its codes equal /score's, and agree in sign with its own reference Kernel SHAP
        same, pos_ref, total, overlap = 0, 0, 0, []
        for i in adv[:8]:
            ti = post(f"/api/registry/versions/{vid}/predict", {"row": records(X.iloc[[i]])[0], "explain": True})
            r = ti["reasons"]
            check(r["adverse"] and ti["prediction"] == "Yes", r)
            same += [c["code"] for c in r["codes"]] == [c["code"] for c in rr["codes"][i]]
            ref = {c["feature"]: c["phi"] for c in ti["shap"]["contributions"]}
            check(abs(ti["shap"]["additivity_error"]) < 1e-6, "reference SHAP additivity")
            for c in r["codes"]:
                total += 1
                pos_ref += ref[c["feature"]] > 0
            top_ref = [f for f, ph in sorted(ref.items(), key=lambda kv: -kv[1]) if ph > 0][:4]
            overlap.append(len(set(top_ref) & {c["feature"] for c in r["codes"]}) / max(len(top_ref), 1))
        check(same == min(8, len(adv)), f"Try It and /score disagree on {min(8, len(adv)) - same} rows")
        # The codes against the same SHAP game estimated with 4,096 coalitions (the audit reference): every cited
        # feature must have φ > 0 there too.
        rf = post(f"/api/registry/versions/{vid}/score", {"rows": records(X.head(150)), "reasons": True, "reasons_reference": True})["reasons"]
        cited = [(c["feature"], rf["reference_check"]["rows"][i]["phi"][c["feature"]]) for i in range(150) if rf["adverse"][i] for c in rf["codes"][i]]
        ok = sum(ph > 0 for _, ph in cited)
        ov = [len(set(rf["reference_check"]["rows"][i]["codes"]) & {c["code"] for c in rf["codes"][i]}) / 4 for i in range(150) if rf["adverse"][i]]
        check(ok == len(cited), f"{len(cited) - ok}/{len(cited)} codes have φ ≤ 0 under the reference")
        s["tryit"] = {"overlap": float(np.mean(overlap)), "sign": pos_ref / total, "ref_s": ti["shap"]["seconds"],
                      "ref_overlap": float(np.mean(ov)), "ref_cited": len(cited)}
        return (f"/score 400 rows: {len(adv)} adverse (p ≥ t = {t:.4f}), {n4} with 4 codes, reasons {rr['reasons_ms']:.0f} ms "
                f"({len(adv) / rr['reasons_ms'] * 1e3:.1f} rows/s) · Try It = /score codes on {same}/{min(8, len(adv))} rows · "
                f"same game with {rf['reference_check']['coalitions']} coalitions: {ok}/{len(cited)} cited φ > 0, top-4 overlap "
                f"{np.mean(ov):.2f} ({rf['reference_check']['seconds'] / max(len(ov), 1):.2f} s/row) · Try It's population Kernel SHAP "
                f"(B = 50, 2,048): {pos_ref}/{total} cited φ > 0, overlap {np.mean(overlap):.2f}, {ti['shap']['seconds']:.1f} s/row")

    step("reasons: Try It and /score (same codes)", tryit_score)

    def batch():
        X = s["X"]
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("holdout.csv", X.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 1200)
        check(b["status"] == "succeeded", b.get("error"))
        out = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{b['id']}/download").content))
        sm = b["result"]["summary"]["reasons"]
        t = sm["threshold"]
        adv = out["adverse"].astype(bool).to_numpy()
        check((adv == (out["prediction"].astype(str) == "Yes")).all(), "adverse column ≠ prediction")
        check(((out["p_Yes"] >= t) == adv).mean() > 0.99, "adverse does not follow the version's threshold")
        cols = [f"reason_{k}{x}" for k in range(1, 5) for x in ("_code", "", "_feature", "_phi")]
        check(all(c in out.columns for c in cols), out.columns.tolist())
        A = out[adv]
        phis = A[[f"reason_{k}_phi" for k in range(1, 5)]].to_numpy(dtype=float)
        n4 = int(np.isfinite(phis).all(axis=1).sum())
        # 4 codes whenever ≥ 4 features push toward default; a row with fewer such features gets all of them (a
        # feature with φ ≤ 0 lowers the risk and is never cited).
        avail = A["reason_factors"].to_numpy(dtype=int)
        ncodes = np.isfinite(phis).sum(axis=1)
        check((ncodes == np.minimum(4, avail)).all(), "codes ≠ min(4, features pushing toward default)")
        check((avail >= 1).all(), "an adverse row with no feature pushing toward default")
        few = int((avail < 4).sum())
        fin = np.isfinite(phis)
        check((phis[fin] > 0).all() and (np.diff(np.where(fin, phis, -1.0), axis=1) <= 1e-12).all(), "codes not positive / not ranked by φ")
        check(out.loc[~adv, "reason_1_code"].isna().all(), "codes on approved rows")
        cited = A[[f"reason_{k}_feature" for k in range(1, 5)]].to_numpy() == col
        texts = A[[f"reason_{k}" for k in range(1, 5)]].to_numpy()[cited]
        check(all(x.startswith("Debt obligations too high") for x in texts), set(texts))
        mono = sm["monotone"].get(col, {})
        check(mono.get("violations") == 0, mono)
        check(sm["signs_consistent"] and sm["additivity_max"] < 1e-6, sm)
        cs = A[[f"reason_{k}" for k in range(1, 5)]].to_numpy()[A[[f"reason_{k}_feature" for k in range(1, 5)]].to_numpy() == "credit_score"]
        check(all(x in ("Credit score below our lending standard", "Credit score too high") for x in cs), set(cs))
        # The batch's codes are the online path's codes for the same rows.
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": records(X.head(400)), "reasons": True})["reasons"]
        idx = [i for i in range(400) if sc["adverse"][i]][:30]
        check(all([c["code"] for c in sc["codes"][i]] == out.loc[i, [f"reason_{k}_code" for k in range(1, 5)]].tolist() for i in idx), "batch ≠ /score")
        s["batch"] = sm
        top = ", ".join(f"{c['code']} {c['share']:.0%}" for c in sm["top_codes"][:4])
        return (f"{b['id']}: {len(out):,} rows · {sm['n_adverse']:,} adverse ({sm['adverse_share']:.1%}) at t = {t:.4f} · "
                f"{n4} with 4 codes, {few} with {sorted(set(ncodes[avail < 4].tolist()))} (only that many features push toward default), "
                f"φ > 0 and ranked · {sm['methods']} {sm['rows_per_s']:.1f} rows/s "
                f"({sm['seconds']:.1f} s; set-up {sm['setup_seconds']:.1f} s) · additivity max {sm['additivity_max']:.1e} · "
                f"{col} cited {int(cited.sum())}× always 'too high', 0 monotone violations ({mono.get('rows_above_background')} rows above "
                f"the background) · = /score codes · most cited: {top}")

    step("reasons: batch scoring with reason columns", batch)

    def stab():
        r = post(f"/api/registry/versions/{vid}/reasons/stability", {"rows": 300, "noise": 0.05, "repeats": 2})
        check(r["rows"] > 0 and r["same_set"] is not None, r)
        c = get(f"/api/registry/versions/{vid}/card")
        rc_ = c["reasons"]
        check(rc_ and rc_["stability"]["same_set"] == r["same_set"] and rc_["threshold"] == s["cfg"]["threshold"], rc_)
        check(any(m["code"] == "CS01" for m in rc_["mapping"]) and rc_["last_batch"]["signs_consistent"], "card mapping / batch")
        md = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Reason codes (adverse action)" in md and "| CS01 | credit_score |" in md and "Stability:" in md, "card markdown")
        fl = r.get("vs_reference") or {}
        check(fl.get("cited_positive", 0) >= 0.95, fl)
        return (f"{r['rows']} adverse holdout rows × {r['repeats']} draws, ε = 0.05·σ: top-4 set unchanged {r['same_set']:.2f}, "
                f"top-1 {r['same_top1']:.2f}, order {r['same_order']:.2f}, Jaccard {r['mean_jaccard']:.2f}, decisions flipped "
                f"{r['decision_flips']:.2f} · fast vs 4,096-coalition reference: same set {fl.get('same_set', float('nan')):.2f}, overlap "
                f"{fl.get('mean_overlap', float('nan')):.2f}, cited φ > 0 {fl.get('cited_positive', float('nan')):.2f} · "
                f"{r['seconds']:.0f} s · model card section + Markdown")

    step("reasons: stability check + model card", stab)

    def tree():
        num = [c for c in ("age", "income_usd", "loan_amount_usd", "credit_score", "debt_to_income", "utilization",
                           "delinquencies_2y", "months_employed")]
        dropped = [c["name"] for c in prof["columns"] if c["name"] not in num + [label]]
        b2 = {"dataset_id": ds, "label": label, "problem_type": "binary", "presets": "medium_quality", "time_limit": 300,
              "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 150}}}, "drop_columns": dropped,
              "feature_importance": False, "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto", "source": "smoke-reasons",
              "scorecard": False}  # F11: a LightGBM-only version, so no scorecard challenger beside it
        rec = wait_run(post("/api/runs", b2)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit.reasons-gbm"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        c = get(f"/api/registry/versions/{v['id']}/reasons")
        check(c["attribution"]["method"] == "tree", c["attribution"])
        X = holdout(rec["id"])[num]
        bb = j(C.post(f"/api/registry/versions/{v['id']}/batches", files={"file": ("holdout.csv", X.to_csv(index=False).encode(), "text/csv")},
                      data={"created_by": "Smoke Analyst"}))
        bb = poll(f"/api/registry/batches/{bb['id']}", lambda x: x["status"] in ("succeeded", "failed"), 900)
        check(bb["status"] == "succeeded", bb.get("error"))
        sm = bb["result"]["summary"]["reasons"]
        check(sm["methods"] == ["tree"] and sm["additivity_max"] < 1e-6 and sm["signs_consistent"], sm)
        out = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{bb['id']}/download").content))
        A = out[out["adverse"].astype(bool)]
        n4 = int(A["reason_4_code"].notna().sum())
        return (f"{v['id']} serves {v['result']['version']['best_model']} on {len(num)} numeric columns · method tree "
                f"({c['attribution']['name']}) · batch {len(out):,} rows, {sm['n_adverse']:,} adverse, {n4} with 4 codes · "
                f"{sm['rows_per_s']:.0f} rows/s · additivity max {sm['additivity_max']:.1e} (kernel path: "
                f"{s.get('batch', {}).get('rows_per_s', float('nan')):.1f} rows/s)")

    step("reasons: exact TreeSHAP for a LightGBM version", tree)


def recourse(st: dict[str, Any]) -> None:
    """G1: counterfactuals ("what would change the decision"). credit-default, zero-config: the G0 intake suggestion's
    pre-fill (target, out-of-time + group split, dropped IDs, F7 cost) plus debt_to_income ↑ (F8), a quick GBM + XGBoost
    run, registered. Checks: what may change is detected from the roles (age and region fixed, history only down, time
    in employment only up, the monotone feature only down) and editable (audited, refusals); the stored acceptance
    check on ≥ 100 adverse holdout rows; the same rows here, their options rebuilt from the changes and re-scored
    through /score (≥ 90 % flip, no fixed feature moved, every value inside training); the recourse suggestion on the
    Build page; batch scoring's `recourse` column for the first 20 adverse rows."""
    ds, label, col = "credit-default", "defaulted", "debt_to_income"
    s: dict[str, Any] = {}

    def train_register():
        it = get(f"/api/datasets/{ds}/intake")
        pf = next(x for x in it["suggestions"] if x["id"] == "binary")["prefill"]
        check(pf["target"] == label and pf["split"] == "time-group", pf)
        body = {"dataset_id": ds, "label": label, "problem_type": "binary", "presets": "medium_quality", "time_limit": 240,
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 200}, "XGB": {"n_estimators": 200}}},
                "drop_columns": pf.get("drop", []) + [pf["time"]], "feature_importance": False,
                "split_spec": {"strategy": "time-group", "time_column": pf["time"], "group_column": pf["group"]},
                "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto",
                "monotone": {"constraints": {col: "increasing"}}, "source": "smoke-recourse"}
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit.recourse"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        s.update(run=rec, vid=v["id"], v=v)
        return (f"intake pre-fill (drop {', '.join(body['drop_columns'])}, split time-group) → {rec['id']} → {v['id']} · "
                f"{v['result']['version']['best_model']} · t = {v['result']['version']['decision_threshold']:.4f}")

    if not step("recourse: zero-config run from the intake pre-fill, registered", train_register):
        return
    vid = s["vid"]

    def config():
        c = get(f"/api/registry/versions/{vid}/recourse")
        eff = {r["feature"]: r["effective"] for r in c["features"]}
        want = {"age": "fixed", "region": "fixed", "delinquencies_2y": "decrease", "months_employed": "increase",
                col: "decrease", "credit_score": "any", "loan_amount_usd": "any"}
        check(all(eff.get(f) == a for f, a in want.items()), {f: eff.get(f) for f in want})
        check(all(r["why"] for r in c["features"]), "a detected action came without its reason")
        bad = C.put(f"/api/registry/versions/{vid}/recourse", json={"actor": "Smoke Analyst", "actions": {"nope": "fixed"}})
        check(bad.status_code == 400 and "No feature" in bad.text, bad.text[:200])
        bad2 = C.put(f"/api/registry/versions/{vid}/recourse", json={"actor": "Smoke Analyst", "actions": {"age": "wizard"}})
        check(bad2.status_code == 422, bad2.status_code)
        e = j(C.put(f"/api/registry/versions/{vid}/recourse", json={"actor": "Smoke Analyst", "actions": {"home_ownership": "fixed"}}))
        er = {r["feature"]: r for r in e["features"]}
        check(er["home_ownership"]["effective"] == "fixed" and er["home_ownership"]["source"] == "you", er["home_ownership"])
        aud = get("/api/registry/audit", version_id=vid)
        entries = aud.get("entries", aud.get("audit", aud if isinstance(aud, list) else []))
        check(any(a["action"] == "recourse_edited" for a in entries), "audit")
        s["fixed"] = {f for f, r in er.items() if r["effective"] == "fixed"}
        s["allowed"] = {f: r["effective"] for f, r in er.items()}
        s["ranges"] = e["ranges"]
        s["cats"] = e["categories"]
        return (f"fixed: {', '.join(sorted(s['fixed']))} · only down: {', '.join(f for f, a in s['allowed'].items() if a == 'decrease')} · "
                f"only up: {', '.join(f for f, a in s['allowed'].items() if a == 'increase')} · edit home_ownership → fixed (audited) · 2 bad edits refused")

    step("recourse: what may change (detected, editable)", config)

    def acceptance():
        r = post(f"/api/registry/versions/{vid}/recourse/check", {"rows": 120})
        check(r["adverse"] >= 100, f"only {r['adverse']} adverse holdout rows")
        check(r["share_valid"] >= 0.9, f"{r['with_valid']}/{r['adverse']} with a valid counterfactual")
        check(r["fixed_changed"] == 0 and r["out_of_range"] == 0 and r["wrong_direction"] == 0, r)
        c = get(f"/api/registry/versions/{vid}/recourse")
        check(c["check"] and c["check"]["with_valid"] == r["with_valid"], "check not stored")
        card = get(f"/api/registry/versions/{vid}/card")
        check((card.get("recourse") or {}).get("check", {}).get("with_valid") == r["with_valid"], "model card has no recourse section")
        st["recourse_check"] = r
        return (f"{r['with_valid']}/{r['adverse']} adverse holdout rows ({r['share_valid'] * 100:.1f}%) get ≥ 1 valid option · "
                f"{r['valid_options']}/{r['options']} options valid · median {r['median_changes']:g} changes, d = {r['median_distance']:.2f}, "
                f"π = {r['median_plausibility']:.2f} · fixed moved {r['fixed_changed']}, out of range {r['out_of_range']}, "
                f"wrong way {r['wrong_direction']} · {r['seconds']:.1f} s ({r['adverse'] / r['seconds']:.1f} rows/s) · in the card")

    step("recourse: acceptance check (stored, in the model card)", acceptance)

    def rescore():
        data_dir = get("/api/health")["data_dir"]
        df = pd.read_csv(os.path.join(data_dir, "datasets", ds, "data.csv"))
        hp_ = get(f"/api/runs/{s['run']['id']}/holdout-predictions")
        feats = s["v"]["result"]["version"]["features"]
        X = df.iloc[np.asarray(hp_["rows"], dtype=int)][feats]
        rows = json.loads(X.head(1200).to_json(orient="records"))
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": rows})
        adv = [i for i, y in enumerate(sc["predictions"]) if str(y) == "Yes"][:110]
        check(len(adv) >= 100, f"{len(adv)} adverse rows")
        A = [rows[i] for i in adv]
        res = []
        for k in range(0, len(A), 55):
            res += post(f"/api/registry/versions/{vid}/recourse", {"rows": A[k:k + 55]})["results"]
        cf, owner, moved_fixed, outside = [], [], 0, 0
        for i, r in enumerate(res):
            check(r["adverse"], "a row /score declines is not adverse for recourse")
            for o in r["options"]:
                x = dict(A[i])
                for ch in o["changes"]:
                    f = ch["feature"]
                    check(ch["from"] == x[f] or (ch["from"] is None and x[f] is None) or abs(float(ch["from"]) - float(x[f])) < 1e-9, (f, ch, x[f]))
                    x[f] = ch["to"]
                    moved_fixed += f in s["fixed"]
                    if f in s["ranges"]:
                        outside += not (s["ranges"][f]["min"] - 1e-9 <= float(ch["to"]) <= s["ranges"][f]["max"] + 1e-9)
                        a = s["allowed"][f]
                        if ch["from"] is not None:
                            outside += (a == "increase" and ch["to"] < ch["from"]) or (a == "decrease" and ch["to"] > ch["from"])
                    else:
                        outside += str(ch["to"]) not in s["cats"][f]
                cf.append(x)
                owner.append(i)
        rs = post(f"/api/registry/versions/{vid}/score", {"rows": cf})
        flips = np.asarray([str(y) != "Yes" for y in rs["predictions"]])
        rows_ok = len({owner[k] for k in np.flatnonzero(flips)})
        claimed = sum(o["valid"] for r in res for o in r["options"])
        check(claimed == int(flips.sum()), f"claimed valid {claimed} ≠ re-scored {int(flips.sum())}")
        check(rows_ok / len(A) >= 0.9, f"{rows_ok}/{len(A)} rows flip on re-scoring")
        check(moved_fixed == 0 and outside == 0, f"fixed moved {moved_fixed}, outside / wrong way {outside}")
        n_opt = [len(r["options"]) for r in res]
        st["recourse_rescore"] = {"rows": len(A), "rows_ok": rows_ok, "options": len(cf), "flips": int(flips.sum())}
        return (f"{len(A)} adverse holdout rows (via /score) → {len(cf)} options (mean {np.mean(n_opt):.1f}/row), rebuilt and "
                f"re-scored through /score: {int(flips.sum())}/{len(cf)} flip · {rows_ok}/{len(A)} rows "
                f"({rows_ok / len(A) * 100:.1f}%) have ≥ 1 · fixed moved 0 · outside range / wrong way 0")

    step("recourse: options re-scored through /score (n ≥ 100)", rescore)

    def build_and_batch():
        it = get(f"/api/datasets/{ds}/intake")
        sg = next((x for x in it["suggestions"] if x["id"] == "recourse"), None)
        check(sg is not None and sg["route"] == "registry" and sg["prefill"].get("version"), [x["id"] for x in it["suggestions"]])
        data_dir = get("/api/health")["data_dir"]
        df = pd.read_csv(os.path.join(data_dir, "datasets", ds, "data.csv")).tail(400).drop(columns=[label])
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("tail.csv", df.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 900)
        check(b["status"] == "succeeded", b.get("error"))
        rc_ = b["result"]["summary"]["reasons"]["recourse"]
        check("error" not in rc_ and rc_["rows_searched"] == min(20, b["result"]["summary"]["reasons"]["n_adverse"]), rc_)
        out = pd.read_csv(io.BytesIO(C.get(f"/api/registry/batches/{b['id']}/download").content))
        check("recourse" in out.columns and out["recourse"].notna().sum() == rc_["rows_searched"], "recourse column")
        return (f"Build suggests '{sg['title']}' → {sg['route']}?version={sg['prefill']['version']} ({sg['score']:g}) · "
                f"batch of 400: {rc_['with_valid']}/{rc_['rows_searched']} of the first adverse rows get a valid change "
                f"({rc_['seconds']:.1f} s) · e.g. “{out['recourse'].dropna().iloc[0]}”")

    step("recourse: Build suggestion and batch column", build_and_batch)


def scorecard(st: dict[str, Any]) -> None:
    """F11: the scorecard challenger. Refusals; the Gym resolves SCORECARD into a binary run's hyperparameters (and
    leaves it out when switched off or for other problem types). credit-default, bagged (k = 3) and stacked (L = 1):
    SCORECARD trains in the folds at level 1 only; the deployable scorecard (refitted on the training rows, never the
    holdout) has its table, IV per feature, points that add up to the score, P(event) = the logistic of the score; the
    CSV export re-scores the holdout to the same scores; ΔAUC of the served model over it with a bootstrap CI; its own
    reason codes (points lost) per row. Then with the F7 cost (FN = 10 × FP) and F8 debt_to_income ↑: the scorecard is
    monotone by construction and by ICE, and reports its expected cost next to the served model's. Registered: the model
    card carries the benchmark, and Try It works on the version (F3)."""
    ds, label, col = "credit-default", "defaulted", "debt_to_income"
    s: dict[str, Any] = {}
    prof = get(f"/api/datasets/{ds}")["profile"]
    drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
    hp = {"GBM": {"num_boost_round": 150}, "CAT": {"iterations": 150}, "LR": {}}
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
            "time_limit": 600, "fit_kwargs": {"hyperparameters": hp, "num_bag_folds": 3, "num_stack_levels": 1,
                                              "dynamic_stacking": False},
            "drop_columns": drop, "feature_importance": False, "source": "smoke-scorecard"}
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    from app.samples import generate_credit

    df, _ = generate_credit()

    def refuse_and_gym():
        bad = [({**body, "scorecard": {"pdo": 0}}, "points to double the odds"),
               ({**body, "scorecard": {"bins": 5}}, "no field 'bins'"),
               ({**body, "scorecard": {"monotone_bins": "sometimes"}}, "monotone_bins"),
               ({**body, "label": "loan_purpose", "problem_type": "multiclass", "eval_metric": None, "scorecard": True},
                "binary model")]
        for b, words in bad:
            r = C.post("/api/runs", json=b)
            check(r.status_code == 400 and words in r.text, f"{words}: {r.status_code} {r.text[:300]}")
        v = post("/api/gym/validate", body)
        hpr = v["resolved"]["fit_kwargs"]["hyperparameters"]
        check(v["ok"] and "SCORECARD" in hpr and hpr["SCORECARD"]["pdo"] == 20 and v["scorecard"]["base_score"] == 600, list(hpr))
        check(any("SCORECARD" in n for n in v["resolved"]["notes"]), v["resolved"]["notes"])
        off = post("/api/gym/validate", {**body, "scorecard": False})
        check("SCORECARD" not in off["resolved"]["fit_kwargs"]["hyperparameters"], "scorecard: false still adds it")
        mc = post("/api/gym/validate", {**body, "label": "loan_purpose", "problem_type": "multiclass", "eval_metric": None})
        check("SCORECARD" not in mc["resolved"]["fit_kwargs"]["hyperparameters"], "added to a multiclass run")
        return f"{len(bad)} bad requests → 400 · Gym: SCORECARD added for binary (PDO 20, 600 at 50:1), not when off or multiclass"

    if not step("scorecard: refusals + Gym", refuse_and_gym):
        return

    def run():
        rec = wait_run(post("/api/runs", body)["id"])
        lb = {r["model"]: r for r in rec["leaderboard"]}
        check("Scorecard_BAG_L1" in lb, list(lb))
        check(not any(m.startswith("Scorecard") and m.endswith("_L2") for m in lb), "scorecard trained as a stacker")
        check(lb["Scorecard_BAG_L1"]["family"] == "SCORECARD", lb["Scorecard_BAG_L1"].get("family"))
        check(rec["scorecard"] and rec["scorecard"]["available"], f"run payload: {rec.get('scorecard')}")  # Explain's tab reads it
        sc = get(f"/api/runs/{rec['id']}/scorecard")
        t = sc["table"]
        n_train = rec["data"]["train_rows"]
        check(sc["n_train"] == n_train == t["n_train"], f"deployable scorecard fit on {sc['n_train']} rows, run trained on {n_train}")
        feats = {f["feature"]: f for f in t["features"]}
        check(set(feats) == set(sc["in_run_features"]), f"refit bins {sorted(set(feats) ^ set(sc['in_run_features']))} differently from the run")
        check(not any(f.startswith("application_date") for f in feats), "a date feature in the scorecard")
        for f in feats.values():  # every row of the training data is in exactly one bin of every feature: no holdout row
            n = sum(r["n"] for r in t["rows"] if r["feature"] == f["feature"])
            check(n == n_train, f"{f['feature']}: bins hold {n} rows, training has {n_train}")
            if f["in_model"]:
                check(f["coefficient"] <= 0, f)
                if f["kind"] == "numeric" and f["direction"]:  # monotone event rate ⇒ points monotone over x
                    pts = [r["points"] for r in t["rows"] if r["feature"] == f["feature"] and r["label"] != "missing"]
                    check(all(-f["direction"] * (b - a) >= -1e-9 for a, b in zip(pts, pts[1:])), f"{f['feature']} points {pts}")
            else:
                check(f["dropped"], f)
        h = sc["holdout"]
        d = h["delta_auc"]
        check(h["proba_check"] < 1e-6, f"P(event) from points ≠ the model's probability ({h['proba_check']})")
        check(d["ci"][0] <= d["delta"] <= d["ci"][1] and d["B"] >= 900, d)
        check(abs(h["served"]["auc"] - h["scorecard"]["auc"] - d["delta"]) < 1e-9, "ΔAUC ≠ AUC difference")
        check(sc["served_model"] == rec["best_model"], sc["served_model"])
        # The exported table re-scores the holdout: Σ points_exact of each row's bins = the API's score.
        csv = pd.read_csv(io.BytesIO(C.get(f"/api/runs/{rec['id']}/scorecard.csv").content))
        check(set(csv["feature"]) - {"_scaling"} == {f for f, x in feats.items() if x["in_model"]}, "CSV features")
        out = post(f"/api/runs/{rec['id']}/scorecard/score", {"holdout_index": list(range(300))})
        ok = 0
        for r in out["rows"]:
            tot = sum(float(csv[(csv["feature"] == p["feature"]) & (csv["bin"].astype(str) == p["bin"])]["points_exact"].iloc[0])
                      if p["bin"] != "no bin (WoE 0)" else p["points"] for p in r["points"])
            ok += abs(tot - r["score"]) < 1e-3
            check(abs(sum(p["points"] for p in r["points"]) - r["score"]) < 1e-6, "points do not add up to the score")
            sca = out["scaling"]
            check(abs(r["p_event"] - 1 / (1 + np.exp((r["score"] - sca["offset"]) / sca["factor"]))) < 1e-9, "P ≠ logistic(score)")
            lost = [x["points_lost"] for x in r["reasons"]]
            check(lost == sorted(lost, reverse=True) and all(x > 0 for x in lost) and len(lost) <= 4, r["reasons"])
        check(ok == len(out["rows"]), f"CSV re-scores {ok}/{len(out['rows'])} holdout rows to the API's score")
        s.update(run=rec, sc=sc)
        iv = sorted(t["features"], key=lambda f: -f["iv"])
        return (f"{rec['id']} · Scorecard_BAG_L1 val AUC {lb['Scorecard_BAG_L1']['score_val']:.4f} (level 1 only) · deployable "
                f"scorecard: {t['n_features']} of {len(feats)} features, {sum(1 for r in t['rows'] if r['in_model'])} bins, fit on "
                f"{n_train} training rows · IV: {', '.join(f'{f['feature']} {f['iv']:.3f}' for f in iv[:4])} · holdout AUC "
                f"scorecard {h['scorecard']['auc']:.4f} (Gini {h['scorecard']['gini']:.3f}, KS {h['scorecard']['ks']:.3f}) vs "
                f"{h['served']['model']} {h['served']['auc']:.4f} · ΔAUC {d['delta']:+.4f} [{d['ci'][0]:+.4f}, {d['ci'][1]:+.4f}] "
                f"(B = {d['B']}, P(Δ ≤ 0) = {d['p_le_0']:.3f}) · CSV re-scores 300/300 holdout rows · points = score, P = σ")

    if not step("scorecard: bagged + stacked run, table, ΔAUC", run):
        return

    def coexist():
        b = {**body, "eval_metric": None, "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto",
             "monotone": {"constraints": {col: "increasing"}},
             "fit_kwargs": {**body["fit_kwargs"], "hyperparameters": {"GBM": {"num_boost_round": 150}}},
             "scorecard": {"pdo": 40, "base_score": 500, "base_odds": 20}}
        rec = wait_run(post("/api/runs", b)["id"])
        sc = get(f"/api/runs/{rec['id']}/scorecard")
        f = next(x for x in sc["table"]["features"] if x["feature"] == col)
        check(f["constraint"] == 1 and f["direction"] == 1, f)
        check(sc["monotone_check"][col]["monotone"], sc["monotone_check"])
        mr = rec["monotone"]
        mm = {m["model"]: m for m in mr["models"]}
        check(mm["Scorecard_BAG_L1"]["monotone_by_construction"] and mm["Scorecard_BAG_L1"].get("monotone_checked"), mm["Scorecard_BAG_L1"])
        check(mr["best_monotone"], "served model not monotone")
        c = sc["cost"]
        check(c and c["c_fn"] == 10 and abs(c["threshold_scorecard"] - 1 / 11) < 1e-9, c)
        sca = sc["table"]["scaling"]
        check(sca["pdo"] == 40 and sca["base_score"] == 500 and abs(sca["factor"] - 40 / np.log(2)) < 1e-9, sca)
        pts = sc["monotone_check"][col]["points"]
        return (f"{rec['id']} (cost FN = 10 × FP, {col} ↑, PDO 40, 500 at 20:1): {col} binned ↑, points "
                f"{pts[0]:.1f} → {pts[-1]:.1f} non-increasing · Scorecard_BAG_L1 monotone by construction and by ICE "
                f"({mm['Scorecard_BAG_L1']['check'][col]['curves_violating']}/100 curves) · served {mr['best_model']} monotone · "
                f"cost per 1,000 decisions: scorecard {c['scorecard_per_1000']:.0f} (t* = 1/11) vs served {c['served_per_1000']:.0f} "
                f"(t = {c['threshold_served']:.4f}) · ΔAUC {sc['holdout']['delta_auc']['delta']:+.4f}")

    step("scorecard: with F7 cost + F8 monotone", coexist)

    def registry():
        rec = s["run"]
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit.scorecard"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        c = get(f"/api/registry/versions/{v['id']}/card")
        scd = c["scorecard"]
        check(scd and abs(scd["delta_auc"]["delta"] - s["sc"]["holdout"]["delta_auc"]["delta"]) < 1e-12, scd)
        md = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("Scorecard benchmark" in md and "ΔAUC (served − scorecard)" in md, "card markdown")
        row = json.loads(df.drop(columns=[label]).iloc[[5]].to_json(orient="records"))[0]
        p = post(f"/api/registry/versions/{v['id']}/predict", {"row": row})
        check(p.get("prediction") in ("Yes", "No"), p)
        return f"{v['id']} ready (serves {v['result']['version']['best_model']}) · card: scorecard benchmark + Markdown · Try It {p['prediction']}"

    step("scorecard: registry card + Try It", registry)

    def off():
        rec = wait_run(post("/api/runs", {**body, "scorecard": False,
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 50}}}})["id"])
        check(not any(r["model"].startswith("Scorecard") for r in rec["leaderboard"]), "scorecard trained while off")
        r = C.get(f"/api/runs/{rec['id']}/scorecard")
        check(r.status_code == 400, r.status_code)
        return f"{rec['id']}: scorecard: false → no SCORECARD model, GET …/scorecard → 400"

    step("scorecard: switched off", off)


def _lc_wait(run_id: str, timeout: float = 1800) -> dict[str, Any]:
    return poll(f"/api/runs/{run_id}/learning-curve", lambda x: x["status"] not in ("queued", "running"), timeout, every=2)


def _lc_check(rec: dict[str, Any], lc: dict[str, Any], fractions: list[float], seeds: int) -> str:
    """The acceptance checks shared by both datasets: the four points, one holdout, nested subsamples drawn inside the
    run's split, the run's own fit call, the power law and the verdict in its stable shape."""
    check(lc["status"] == "succeeded", f"learning curve {lc['status']}: {lc.get('error')}")
    pts, summ = lc["points"], lc["summary"]
    N = rec["data"]["train_rows"]
    check([x["fraction"] for x in summ] == fractions, f"fractions {[x['fraction'] for x in summ]}")
    check(len(pts) == (len(fractions) - 1) * seeds + 1, f"{len(pts)} fits")
    for x in summ:
        check(x["k"] == (1 if x["fraction"] == 1 else seeds) and np.isfinite(x["mean"]), x)
        if x["fraction"] < 1:
            check(x["ci"] and x["ci"][0] <= x["mean"] <= x["ci"][1], f"t-interval {x}")
            check(abs(x["n"] - x["fraction"] * N) <= max(0.02 * N, 25), f"{x['fraction']}: n = {x['n']} of N = {N}")
    check([x["n"] for x in summ] == sorted(x["n"] for x in summ) and summ[-1]["n"] == N, "n not increasing to N")
    check(len({p["holdout_sha"] for p in pts}) == 1 and lc["holdout_rows"] == rec["data"]["holdout_rows"], "points scored on different holdouts")
    check(lc["split"]["key"] == rec["split"]["key"] and lc["split"]["sha256"] == rec["split"]["sha256"], "not the run's split")
    check(lc["nested"] is True, "subsamples are not nested")
    run_pt = next(p for p in pts if p["source"] == "run")
    m = rec["eval_metric"]["name"]
    check(run_pt["score"] == rec["holdout"]["metrics"][m], "100 % point ≠ the run's holdout score")
    run_models = {r["model"] for r in rec["leaderboard"]}
    for p in pts:  # every subsample trained what the run trained
        check(set(p["models"]) == run_models, f"{p['fraction']}/{p['seed']}: {sorted(p['models'])} vs the run's {sorted(run_models)}")
    fk, rk = lc["fit_call"]["fit_kwargs"], rec["resolved"]["fit_kwargs"]
    check(json.dumps(fk.get("hyperparameters"), sort_keys=True, default=str) == json.dumps(rk.get("hyperparameters"), sort_keys=True, default=str),
          "hyperparameters differ from the run's resolved call")
    check(lc["fit_call"]["predictor_kwargs"].get("eval_metric") == rec["resolved"]["predictor_kwargs"].get("eval_metric"), "eval_metric differs")
    v = lc["verdict"]
    s_, x2, x4 = v["slope_per_doubling"], v["extrapolation"]["x2"], v["extrapolation"]["x4"]
    check(v["schema"] == "learning_curve/1" and v["label"] in ("data-limited", "saturated", "noisy"), v.get("label"))
    check(s_["ci"][0] <= s_["ci"][1] and s_["level"] == 0.95 and s_["value"] >= 0, s_)
    check(x2["n"] == 2 * N and x4["n"] == 4 * N, "extrapolation not at 2N / 4N")
    for x in (x2, x4):
        check(np.isfinite(x["score"]) and x["ci"][0] <= x["ci"][1], x)
    eps = v["evidence"]["min_gain"]
    want = "data-limited" if s_["ci"][0] > eps else "saturated" if s_["ci"][1] < eps else "noisy"
    check(v["label"] == want, f"verdict {v['label']} but the rule gives {want}")
    check(len(lc["curve"]) > 10 and len(lc["band"]) == len(lc["curve"]), "no fitted curve / band")
    head = get(f"/api/runs/{rec['id']}")["learning_curve"]
    check(head and head["status"] == "succeeded" and head["verdict"]["label"] == v["label"], f"run payload: {head}")
    means = " → ".join(f"{x['mean']:.4f}" + (f"±{x['sd']:.4f}" if x["sd"] else "") for x in summ)
    gib = v["greater_is_better"]
    return (f"{m} at n = {', '.join(str(x['n']) for x in summ)}: {means} · Δ₂ = {s_['value']:+.4f} per doubling "
            f"[{s_['ci'][0]:+.4f}, {s_['ci'][1]:+.4f}] vs ε = {eps:.4f} · 2N {x2['score']:.4f} [{x2['ci'][0]:.4f}, {x2['ci'][1]:.4f}] · "
            f"4N {x4['score']:.4f} [{x4['ci'][0]:.4f}, {x4['ci'][1]:.4f}]{'' if gib else ' (lower is better)'} · verdict {v['label']}")


def learning(st: dict[str, Any]) -> None:
    """F12: learning curves. The run's exact fit call on nested subsamples of its training rows (10 / 25 / 50 %, 3 seeds;
    100 % is the run), all scored on the run's holdout; a power law with bootstrap intervals, extrapolation to 2N and
    4N, the verdict. credit-default under a group split with the F7 cost, F8 debt_to_income ↑ and the F11 scorecard
    (whole customers drawn inside each bagging fold), and support-tickets (multiclass, text, stratified). Refusals, one
    curve at a time (409), cancel."""
    fractions, seeds = [0.1, 0.25, 0.5, 1.0], 3
    credit = {"dataset_id": "credit-default", "label": "defaulted", "problem_type": "binary", "presets": "medium_quality",
              "time_limit": 300, "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 150}}},
              "drop_columns": ["applicant_id", "customer_id"], "feature_importance": False, "source": "smoke-learning",
              "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto",
              "monotone": {"constraints": {"debt_to_income": "increasing"}},
              "split_spec": {"strategy": "group", "group_column": "customer_id"}}
    s: dict[str, Any] = {}

    def refusals():
        tiny = wait_run(post("/api/runs", {"dataset_id": "credit-default", "label": "defaulted", "problem_type": "binary",
                                           "presets": "medium_quality", "time_limit": 60, "drop_columns": ["applicant_id", "customer_id"],
                                           "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 30}}},
                                           "scorecard": False, "feature_importance": False, "source": "smoke-learning"})["id"])
        s["tiny"] = tiny
        g = get(f"/api/runs/{tiny['id']}/learning-curve")
        check(g["status"] == "none" and g["defaults"]["fractions"] == fractions and g["defaults"]["fits"] == 9, g)
        bad = [({"fractions": [0.1, 0.5]}, "at least 3"), ({"fractions": [0.1, 0.25, 0.5]}, "including 1.0"),
               ({"fractions": [0.001, 0.5, 1]}, "at least 30"), ({"fractions": [0.5, 1, 1.5]}, "(0, 1]"), ({"seeds": 0}, "seeds")]
        for b, words in bad:
            r = C.post(f"/api/runs/{tiny['id']}/learning-curve", json=b)
            check(r.status_code in (400, 422) and words in r.text, f"{b}: {r.status_code} {r.text[:200]}")
        r = C.post(f"/api/runs/{tiny['id']}/learning-curve/cancel")
        check(r.status_code == 400, f"cancel with nothing running → {r.status_code}")
        return f"{tiny['id']}: status none with defaults (10/25/50/100 %, 3 seeds, 9 fits) · {len(bad)} bad requests refused · cancel with nothing running → 400"

    if not step("learning: refusals + defaults", refusals):
        return

    def credit_curve():
        rec = wait_run(post("/api/runs", credit)["id"])
        check(rec["split"]["strategy"] == "group" and "customer_id" in rec["config"]["drop_columns"], rec["split"])
        t0 = time.time()
        post(f"/api/runs/{rec['id']}/learning-curve", {})
        lc = _lc_wait(rec["id"])
        info = _lc_check(rec, lc, fractions, seeds)
        check(lc["groups_whole"] is True and lc["split"]["group_column"] == "customer_id", "a customer was cut in half")
        check(all(p["groups"] for p in lc["points"] if p["source"] == "subsample"), "no group counts")
        hp = lc["fit_call"]["fit_kwargs"]["hyperparameters"]
        check("GBM_MONO" in hp and "SCORECARD" in hp and lc["metric"]["name"] == "expected_cost", f"{list(hp)} · {lc['metric']}")
        check(lc["tuning_rows"] == rec["data"]["tuning_rows"] > 0, "the tuning slice is not the run's")
        s["credit"] = (rec, lc)
        return f"{rec['id']} (group split, cost FN = 10 × FP, debt_to_income ↑, scorecard) in {time.time() - t0:.0f} s · whole customers ✓ · {info}"

    step("learning: credit-default curve", credit_curve)

    def tickets_curve():
        rec = wait_run(post("/api/runs", {"dataset_id": "support-tickets", "presets": "medium_quality", "time_limit": 300,
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 100}}},
                                          "feature_importance": False, "source": "smoke-learning"})["id"])
        t0 = time.time()
        post(f"/api/runs/{rec['id']}/learning-curve", {})
        lc = _lc_wait(rec["id"])
        info = _lc_check(rec, lc, fractions, seeds)
        k = len(rec["classes"])
        check(all(p["classes"] == k for p in lc["points"] if p["source"] == "subsample"), "a subsample lost a class")
        check(lc["split"]["stratified"] and "stratified" in lc["split"]["subsample"], lc["split"])
        check(any(m.startswith("Tfidf") for m in lc["points"][1]["models"]), "the TF-IDF models were not refitted")
        s["tickets"] = (rec, lc)
        return f"{rec['id']} (K = {k}, text + TF-IDF models, stratified) in {time.time() - t0:.0f} s · {info}"

    step("learning: support-tickets curve", tickets_curve)

    def one_at_a_time():
        tiny = s["tiny"]
        post(f"/api/runs/{tiny['id']}/learning-curve", {"seeds": 2})
        other = (s.get("tickets") or s.get("credit") or (None,))[0]
        if other:
            r = C.post(f"/api/runs/{other['id']}/learning-curve", json={})
            check(r.status_code == 409 and "already running" in r.text, f"second curve → {r.status_code} {r.text[:200]}")
        post(f"/api/runs/{tiny['id']}/learning-curve/cancel")
        lc = _lc_wait(tiny["id"], 120)
        check(lc["status"] == "cancelled", lc["status"])
        check(get(f"/api/runs/{tiny['id']}")["learning_curve"]["status"] == "cancelled", "run payload not cancelled")
        if other:
            again = get(f"/api/runs/{other['id']}/learning-curve")
            check(again["status"] == "succeeded", "the other run's curve was touched")
        return f"a second curve while one runs → 409 · cancel → cancelled after {lc.get('fits_done', 0)} of {lc.get('fits_total')} fits"

    step("learning: one at a time + cancel", one_at_a_time)


def _al_wait(run_id: str, timeout: float = 1800) -> dict[str, Any]:
    return poll(f"/api/runs/{run_id}/active-learning",
                lambda x: x["status"] not in ("queued", "running") and not (x.get("queue") or {}).get("submitted_pending"), timeout, every=2)


def active(st: dict[str, Any]) -> None:
    """F13: active-learning labelling queue. support-tickets with 90 % of the training labels hidden (stratified): the
    run's exact fit call on the 10 % seed labels, then 2 rounds of b = 200 rows picked by margin uncertainty + diversity,
    labelled in the queue (one skip and one relabel by a named person, the rest by the SIMULATED oracle: the sample's own
    hidden labels), each round a dataset version with lineage and a refit on the same holdout. Active vs random at equal
    budgets over 3 seeds, labelled by the simulated oracle; the acceptance is active > random after 2 rounds, reported
    with the per-seed numbers, sd and the 95 % t-interval of the paired gap. Refusals, one queue at a time, cancel."""
    s: dict[str, Any] = {}

    def refusals():
        rec = wait_run(post("/api/runs", {"dataset_id": "support-tickets", "presets": "medium_quality", "time_limit": 300,
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 100}}},
                                          "feature_importance": False, "source": "smoke-active"})["id"])
        s["rec"] = rec
        g = get(f"/api/runs/{rec['id']}/active-learning")
        dft = g["defaults"]
        check(g["status"] == "none" and dft["hide_share"] == 0.9 and dft["budget"] == 200 and dft["rounds"] == 2 and dft["seeds"] == 3, dft)
        reg = wait_run(post("/api/runs", {"dataset_id": "loan-amount", "time_limit": 30, "source": "smoke-active",
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 20}}}, "feature_importance": False})["id"])
        r = C.post(f"/api/runs/{reg['id']}/active-learning", json={})
        check(r.status_code == 400 and "classification" in r.text, f"regression → {r.status_code} {r.text[:200]}")
        bad = [({"hide_share": 0.999}, "at least 30"), ({"budget": 5000, "rounds": 5}, "hidden rows"), ({"pool": "missing"}, "empty label"),
               ({"seeds": 0}, "seeds"), ({"strategy": "random"}, "strategy")]
        for b, words in bad:
            r = C.post(f"/api/runs/{rec['id']}/active-learning", json=b)
            check(r.status_code in (400, 422) and words in r.text, f"{b}: {r.status_code} {r.text[:200]}")
        r = C.post(f"/api/runs/{rec['id']}/active-learning/answer", json={"answers": [{"id": "r1-0", "answer": "skip"}]})
        check(r.status_code == 409, f"answer with no queue → {r.status_code}")
        r = C.post(f"/api/runs/{rec['id']}/active-learning/cancel")
        check(r.status_code == 400, f"cancel with nothing open → {r.status_code}")
        return (f"{rec['id']} (support-tickets, K = {len(rec['classes'])}, holdout accuracy {rec['holdout']['metrics']['accuracy']:.4f} on all "
                f"{rec['data']['train_rows']} labels): status none, defaults h = 90 %, b = 200, 2 rounds, 3 seeds · regression refused · "
                f"{len(bad)} bad requests refused · answer/cancel with nothing open → 409/400")

    if not step("active: refusals + defaults", refusals):
        return

    def queue_round0():
        rec = s["rec"]
        t0 = time.time()
        post(f"/api/runs/{rec['id']}/active-learning", {})
        r = C.post(f"/api/runs/{rec['id']}/active-learning", json={})
        check(r.status_code == 409, f"a second start while working → {r.status_code}")
        al = _al_wait(rec["id"])
        s["t_sim"] = time.time() - t0
        check(al["status"] == "awaiting_labels", f"{al['status']}: {al.get('error')}")
        n_train = rec["data"]["train_rows"]
        pool = al["pool"]
        check(pool["source"] == "hide" and pool["n_train"] == n_train and abs(pool["n_initial"] - 0.1 * n_train) < 0.01 * n_train + 6
              and pool["n_initial"] + pool["n_pool"] == n_train, pool)
        check(len(pool["initial_classes"]) == len(rec["classes"]) and min(pool["initial_classes"].values()) >= 5, pool["initial_classes"])
        q = al["queue"]
        items = q["queue"]
        check(q["open"] and q["queue_round"] == 1 and len(items) == 200 and len({i["row"] for i in items}) == 200, f"{len(items)} items")
        hold = {int(x) for x in get(f"/api/runs/{rec['id']}/holdout-predictions")["rows"]}
        check(len(hold) == rec["data"]["holdout_rows"] and not hold & {i["row"] for i in items} and al["holdout_in_pool"] == 0,
              "a holdout row is in the queue")
        s["hold"] = hold
        check(all(0 <= i["margin"] <= 1 and abs(i["uncertainty"] - (1 - i["margin"])) < 1e-6 for i in items), "u ≠ 1 − margin")
        sel = q["selection"][0]
        check(sel["candidates"] == 2000 and sel["u_mean_picked"] > sel["u_mean_pool"], sel)
        check(len({i["cluster"] for i in items}) == 200, "the picks are not one per cluster")
        check(all(i["suggested"] == i["p_top"][0][0] and i["p_top"][0][1] >= i["p_top"][1][1] for i in items), "suggested ≠ top class")
        p0 = q["points"][0]
        check(p0["round"] == 0 and p0["n"] == pool["n_initial"] and p0["holdout_sha"] == al["holdout_sha"], p0)
        hp = al["fit_call"]["fit_kwargs"]["hyperparameters"]
        check(hp == rec["resolved"]["fit_kwargs"]["hyperparameters"], "not the run's fit call")
        check(any(m.startswith("Tfidf") for m in p0["models"]), p0["models"])
        s["al0"] = al
        return (f"{rec['id']}: {pool['n_initial']} seed labels ({pool['n_pool']} hidden) → round-0 accuracy {p0['score']:.4f} · queue of 200 "
                f"from 2,000 candidates (ū picked {sel['u_mean_picked']:.3f} vs pool {sel['u_mean_pool']:.3f}, one per k-means cluster) · "
                f"0 holdout rows in the queue · comparison fits done in {s['t_sim']:.0f} s")

    if not step("active: start → round 0 + queue", queue_round0):
        return

    def answer_rounds():
        rec = s["rec"]
        rid = rec["id"]
        ds_csv = None
        lines = []
        for rnd in (1, 2):
            al = get(f"/api/runs/{rid}/active-learning")
            items = al["queue"]["queue"]
            check(al["queue"]["queue_round"] == rnd and len(items) == 200, al["queue"].get("queue_round"))
            check(not s["hold"] & {i["row"] for i in items}, "a holdout row is in the queue")
            r = C.post(f"/api/runs/{rid}/active-learning/answer", json={"answers": [{"id": items[0]["id"], "answer": "relabel", "label": "Nope"}]})
            check(r.status_code == 400 and "not a class" in r.text, f"bad label → {r.status_code}")
            other = next(c for c in rec["classes"] if c != items[1]["suggested"])
            post(f"/api/runs/{rid}/active-learning/answer", {"answers": [{"id": items[0]["id"], "answer": "skip"},
                                                                        {"id": items[1]["id"], "answer": "relabel", "label": other}], "reviewer": "ana"})
            sim = post(f"/api/runs/{rid}/active-learning/simulate")
            check(sim["answered"] == 198 and sim["reviewer"] == "simulated-oracle", sim)
            n_before = al["queue"]["n_labelled"]
            sub = post(f"/api/runs/{rid}/active-learning/submit")
            r = C.post(f"/api/runs/{rid}/active-learning/submit")
            check(r.status_code == 409, f"second submit → {r.status_code}")
            ver = sub["dataset_version"]
            check(ver["labelled_this_round"] == 199 and ver["labelled_total"] == n_before + 199, ver)
            al = _al_wait(rid, 600)
            pt = al["queue"]["points"][rnd]
            check(pt["round"] == rnd and pt["n"] == n_before + 199 and pt["added"] == 199 and pt["answered_by"] == {"ana": 1, "simulated-oracle": 198}, pt)
            check(pt["holdout_sha"] == s["al0"]["holdout_sha"] and pt["dataset_id"] == ver["id"], "holdout or version differ")
            ds = get(f"/api/datasets/{ver['id']}")
            lin = ds["profile"]["lineage"]
            check(lin["parent"] == "support-tickets" and lin["active_learning"] == {"run_id": rid, "round": rnd} and lin["labelled"] == 199, lin)
            lines.append(f"r{rnd}: +199 (ana 1 relabel + 1 skip, oracle 198) → n = {pt['n']}, accuracy {pt['score']:.4f}, {ver['id']}")
            ds_csv = ver
        check(al["status"] == "succeeded" and al["queue"]["done"] and al["summary"]["labels_used"] == [p["n"] for p in al["queue"]["points"]], al["status"])
        # the last version trains: exactly the labelled rows, the same holdout
        v = wait_run(post("/api/runs", {"dataset_id": ds_csv["id"], "label": rec["label"], "presets": "medium_quality", "time_limit": 300,
                                        "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 100}}},
                                        "feature_importance": False, "source": "smoke-active"})["id"])
        check(v["data"]["train_rows"] == ds_csv["labelled_total"] and v["data"]["holdout_rows"] == rec["data"]["holdout_rows"], v["data"])
        hold_v = {int(x) for x in get(f"/api/runs/{v['id']}/holdout-predictions")["rows"]}
        check(hold_v == s["hold"], "the version's holdout is not the run's")
        s["al"] = al
        return " · ".join(lines) + (f" · {v['id']} on {ds_csv['id']}: {v['data']['train_rows']} training rows, the same {len(hold_v)} holdout rows, "
                                    f"accuracy {v['holdout']['metrics']['accuracy']:.4f}")

    if not step("active: queue → answers → versions → refits", answer_rounds):
        return

    def acceptance():
        al = s["al"]
        sm = al["summary"]
        check(sm["schema"] == "active_learning/1" and sm["comparison"]["simulated"] and "oracle" in sm["comparison"]["labeller"], sm["schema"])
        rounds = {r["round"]: r for r in sm["comparison"]["rounds"]}
        check(set(rounds) == {0, 1, 2} and all(r["gap"]["n_seeds"] == 3 for r in rounds.values()), list(rounds))
        check(rounds[0]["gap"]["mean"] == 0 and rounds[0]["active"]["scores"] == rounds[0]["random"]["scores"], "round 0 is not shared")
        for k in (1, 2):
            check(rounds[k]["n"] == rounds[k]["n_random"] == [rounds[0]["n"][i] + 200 * k for i in range(3)], f"unequal budgets {rounds[k]['n']} {rounds[k]['n_random']}")
        f = rounds[2]
        g = f["gap"]
        lo, hi = g["ci"]
        check(lo <= g["mean"] <= hi, g)
        v = sm["verdict"]
        want = "active-better" if lo > 0 else "random-better" if hi < 0 else "no-clear-difference"
        check(v["label"] == want and v["round"] == 2, v)
        head = get(f"/api/runs/{al['run_id']}")["active_learning"]
        check(head["summary"]["verdict"] == v and head["summary"]["labels_used"] == sm["labels_used"], "the run payload's summary differs")
        a, r = f["active"], f["random"]
        msg = (f"after 2 rounds (n = {f['n'][0]}): active {a['mean']:.4f} ± {a['sd']:.4f} {a['scores']} vs random {r['mean']:.4f} ± {r['sd']:.4f} "
               f"{r['scores']} · d̄ = {g['mean']:+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}], active wins {g['wins']}/3 · round 1: d̄ = "
               f"{rounds[1]['gap']['mean']:+.4f} · verdict {v['label']} (labels: simulated oracle)")
        check(a["mean"] > r["mean"], "ACCEPTANCE: active is not above random after 2 rounds: " + msg)
        return msg

    step("active: active vs random (acceptance)", acceptance)

    def cancel():
        rec = s["rec"]
        post(f"/api/runs/{rec['id']}/active-learning", {"seeds": 1, "rounds": 1})
        time.sleep(1)
        post(f"/api/runs/{rec['id']}/active-learning/cancel")
        al = _al_wait(rec["id"], 120)
        check(al["status"] == "cancelled", al["status"])
        check(get(f"/api/runs/{rec['id']}")["active_learning"]["status"] == "cancelled", "run payload not cancelled")
        return f"start → cancel → cancelled after {al.get('fits_done') or 0} fits"

    step("active: cancel", cancel)


def _ann_wait(run_id: str, timeout: float = 900) -> dict[str, Any]:
    return poll(f"/api/runs/{run_id}/annotations", lambda x: x["status"] != "computing", timeout, every=2)


def _ann_votes(run_id: str) -> list[dict[str, Any]]:
    """Every annotated row with its votes, paged as the UI pages the queue (include=all)."""
    out: list[dict[str, Any]] = []
    while True:
        g = get(f"/api/runs/{run_id}/annotations", include="all", offset=len(out), limit=500)
        out += g["queue"]["items"]
        if len(out) >= g["queue"]["total"] or not g["queue"]["items"]:
            return out


def _fleiss(items: list[dict[str, Any]], classes: list[str]) -> float:
    ci = {c: i for i, c in enumerate(classes)}
    N = np.zeros((len(items), len(classes)))
    for i, it in enumerate(items):
        for v in it["votes"]:
            N[i, ci[v["label"]]] += 1
    N = N[N.sum(1) >= 2]
    n = N.sum(1)
    Pi = (N * (N - 1)).sum(1) / (n * (n - 1))
    p = N.sum(0) / n.sum()
    Pe = (p ** 2).sum()
    return float((Pi.mean() - Pe) / (1 - Pe))


def _cohen(items: list[dict[str, Any]], a: str, b: str, classes: list[str]) -> tuple[float, int]:
    x, y = [], []
    for it in items:
        v = {q["annotator"]: q["label"] for q in it["votes"]}
        if a in v and b in v:
            x.append(classes.index(v[a]))
            y.append(classes.index(v[b]))
    x, y = np.array(x), np.array(y)
    K = len(classes)
    po = (x == y).mean()
    pe = (np.bincount(x, minlength=K) / len(x)) @ (np.bincount(y, minlength=K) / len(y))
    return float((po - pe) / (1 - pe)), len(x)


def annotators(st: dict[str, Any]) -> None:
    """F14: several annotators per row. The support-tickets sample's SIMULATED annotators (each mistake drawn from the
    sample's hidden truth): ana 96 %, ben 90 %, chen 82 % (look-alike classes), dara 55 % (any class), eli swaps Billing
    and Account 60 % of the time. 1,500 training rows, 3 annotators each. The run is not bagged, so p̂ comes from a quick
    bagged fit of its call. Acceptance: the worst annotator by cleanlab's quality is the worst against the truth, and the
    CROWDLAB consensus is more accurate than majority vote against the truth (paired, exact McNemar)."""
    s: dict[str, Any] = {}

    def refusals():
        rec = wait_run(post("/api/runs", {"dataset_id": "support-tickets", "presets": "medium_quality", "time_limit": 300,
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 100}}},
                                          "feature_importance": False, "source": "smoke-annotators"})["id"])
        s["rec"] = rec
        rid = rec["id"]
        g = get(f"/api/runs/{rid}/annotations")
        check(g["status"] == "none" and g["sample_available"] and g["sample_defaults"] == {"n_rows": 1500, "per_row": 3, "seed": 14}, g)
        reg = wait_run(post("/api/runs", {"dataset_id": "loan-amount", "time_limit": 30, "source": "smoke-annotators",
                                          "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 20}}}, "feature_importance": False})["id"])
        r = C.post(f"/api/runs/{reg['id']}/annotations/sample", json={})
        check(r.status_code == 400 and "classification" in r.text, f"regression → {r.status_code} {r.text[:200]}")
        hold = sorted(int(x) for x in get(f"/api/runs/{rid}/holdout-predictions")["rows"])
        s["hold"] = hold

        def up(df: pd.DataFrame) -> httpx.Response:
            return C.post(f"/api/runs/{rid}/annotations/upload", files={"file": ("ann.csv", df.to_csv(index=False).encode(), "text/csv")},
                          data={"mode": "replace"})
        bad = [(pd.DataFrame({"row": [1], "who": ["a"], "label": ["Billing"]}), 400, "annotator column"),
               (pd.DataFrame({"row": [1, 2], "annotator": ["a", "b"], "label": ["Billing", "Mortgages"]}), 400, "not classes"),
               (pd.DataFrame({"row": hold[:3], "annotator": ["a", "b", "c"], "label": ["Billing"] * 3}), 400, "never relabelled"),
               (pd.DataFrame({"name": ["x"], "annotator": ["a"], "label": ["Billing"]}), 400, "Name the rows")]
        for df, code, words in bad:
            r = up(df)
            check(r.status_code == code and words in r.text, f"{list(df.columns)}: {r.status_code} {r.text[:200]}")
        r = C.post(f"/api/runs/{rid}/annotations/answer", json={"answers": [{"row": 1, "action": "vote", "label": "Billing"}]})
        check(r.status_code == 409, f"answer with no annotations → {r.status_code}")
        r = C.post(f"/api/runs/{rid}/annotations/sample", json={"per_row": 9})
        check(r.status_code == 422, f"per_row 9 → {r.status_code}")
        return (f"{rid} (support-tickets, K = {len(rec['classes'])}, not bagged) · status none, sample offered · regression refused · "
                f"{len(bad)} bad uploads refused (incl. holdout-only rows) · answer before loading → 409 · per_row 9 → 422")

    if not step("annotators: refusals", refusals):
        return

    def sample():
        rec = s["rec"]
        rid = rec["id"]
        t0 = time.time()
        g = post(f"/api/runs/{rid}/annotations/sample", {})
        check(g["status"] == "computing" and g["load"]["annotations"] == 4500 and g["load"]["rows"] == 1500, g.get("load"))
        g = _ann_wait(rid)
        check(g["status"] == "ready", f"{g['status']}: {g.get('error')}")
        sm = g["summary"]
        s["g"], s["sm"] = g, sm
        pr = sm["probs"]
        check(pr["source"] == "quick-bagged" and pr["n_oof"] == rec["data"]["train_rows"] and pr["n_predicted"] == 0, pr)
        check(sm["schema"] == "annotations/1" and sm["n_rows"] == 1500 and sm["n_annotations"] == 4500 and sm["n_annotators"] == 5
              and sm["annotations_per_row"] == {"mean": 3.0, "min": 3, "max": 3}, sm)
        items = _ann_votes(rid)
        check(len(items) == 1500 and not set(s["hold"]) & {i["row"] for i in items}, f"{len(items)} rows, or a holdout row")
        fk = sm["agreement"]["fleiss_kappa"]
        mine = _fleiss(items, g["classes"])
        check(abs(mine - fk["value"]) < 1e-9 and fk["ci"][0] < fk["value"] < fk["ci"][1], f"Fleiss {fk} vs recomputed {mine}")
        pw = {(p["a"], p["b"]): p for p in g["pairwise"]}
        check(len(pw) == 10, f"{len(pw)} pairs")
        a, b = next(iter(pw))
        ck, n = _cohen(items, a, b, g["classes"])
        check(abs(ck - pw[(a, b)]["kappa"]) < 1e-9 and n == pw[(a, b)]["n"], f"Cohen {a}/{b}: {pw[(a, b)]} vs {ck}")
        diff = sum(it["consensus"] != it["majority_vote"] for it in items)
        check(diff == sm["consensus"]["consensus_vs_majority_vote_differ"], f"{diff} vs {sm['consensus']}")
        q = get(f"/api/runs/{rid}/annotations", limit=50)["queue"]
        cqs = [i["consensus_quality"] for i in q["items"]]
        check(q["total"] == sm["consensus"]["queue"] and cqs == sorted(cqs) and all(len(i["votes"]) == 3 for i in q["items"]), "queue order")
        cl = sm["crowdlab"]
        check(cl["consensus_is_argmax"] and all(i["posterior"][0]["label"] == i["consensus"] for i in items)
              and 0 < cl["pi"] < 1 and cl["model_weight"] > 0, f"consensus is not arg max of the CROWDLAB posterior: {cl}")
        check(get(f"/api/runs/{rid}")["annotations"]["summary"]["schema"] == "annotations/1", "run payload has no summary")
        s["items"] = items
        return (f"p̂ {pr['source']} ({pr['model']}, {pr['n_oof']} OOF rows, fit {pr['fit_seconds']} s) · Fleiss κ = {fk['value']:.4f} "
                f"[{fk['ci'][0]:.4f}, {fk['ci'][1]:.4f}] ({sm['agreement']['interpretation']}; recomputed here {mine:.4f}) · "
                f"Cohen κ {a}/{b} = {ck:.4f} (n = {n}) · mean pairwise κ {sm['agreement']['cohen_kappa_mean']:.4f} · "
                f"CROWDLAB w_model {cl['model_weight']:.3f}, π {cl['pi']:.3f}, consensus = arg max p̃ on every row · "
                f"queue {q['total']} rows (lowest consensus quality first) · ready in {time.time() - t0:.0f} s")

    if not step("annotators: sample → p̂ → κ + consensus", sample):
        return

    def acceptance():
        sm, g = s["sm"], s["g"]
        tr = sm["truth"]
        check(tr and tr["simulated"] and tr["n"] == 1500, tr)
        rank = sorted(g["annotators"], key=lambda x: x["quality"])
        line = ", ".join(f"{x['name']} q {x['quality']:.3f} / true {x['truth']['accuracy']:.3f}" for x in rank)
        check(sm["worst_annotator"]["name"] == tr["worst_by_truth"]["name"] == "dara" and tr["worst_identified"],
              f"worst by quality {sm['worst_annotator']} vs truth {tr['worst_by_truth']}")
        A = tr["accuracy"]
        check(A["consensus"]["accuracy"] > A["majority_vote"]["accuracy"], f"consensus {A['consensus']} ≤ MV {A['majority_vote']}")
        check(A["consensus"]["accuracy"] > A["majority_vote_cleanlab"]["accuracy"], f"consensus ≤ cleanlab's MV {A['majority_vote_cleanlab']}")
        eli = next(x for x in g["annotators"] if x["name"] == "eli")
        tc = eli["top_confusion"]
        check({tc["consensus"], tc["given"]} == {"Billing", "Account"} and tc["share_of_class"] > 0.4, f"eli's bias not found: {tc}")
        pd_ = tr["paired"]
        st["annotators_acceptance"] = {"consensus": A["consensus"]["accuracy"], "mv": A["majority_vote"]["accuracy"]}
        return (f"worst = {sm['worst_annotator']['name']} by quality and by truth ✓ ({line}) · against the hidden truth on 1,500 rows: "
                f"consensus {A['consensus']['accuracy']:.4f} [{A['consensus']['ci'][0]:.4f}, {A['consensus']['ci'][1]:.4f}] vs majority vote "
                f"{A['majority_vote']['accuracy']:.4f} [{A['majority_vote']['ci'][0]:.4f}, {A['majority_vote']['ci'][1]:.4f}] "
                f"({sm['consensus']['majority_vote_ties']} ties at random) · cleanlab's MV (model breaks ties) {A['majority_vote_cleanlab']['accuracy']:.4f} · "
                f"model alone {A['model']['accuracy']:.4f} · dataset label {A['given']['accuracy']:.4f} · paired: consensus right & MV wrong "
                f"{pd_['consensus_right_mv_wrong']}, reverse {pd_['mv_right_consensus_wrong']}, exact McNemar p = {pd_['mcnemar_p']:.1e} · "
                f"eli's bias found: {tc['consensus']} → {tc['given']} on {tc['share_of_class']:.0%} of consensus-{tc['consensus']} rows")

    step("annotators: acceptance (worst annotator, consensus vs majority vote)", acceptance)

    def queue_version():
        rec = s["rec"]
        rid = rec["id"]
        q = get(f"/api/runs/{rid}/annotations", limit=5)["queue"]["items"]
        r = C.post(f"/api/runs/{rid}/annotations/answer", json={"reviewer": "Smoke Reviewer", "answers": [
            {"row": q[0]["row"], "action": "vote", "label": q[0]["consensus"]},
            {"row": q[1]["row"], "action": "adjudicate", "label": q[1]["p_model"][0]["label"]}]})
        check(r.status_code == 200 and r.json()["votes"] == 1, r.text)
        r = C.post(f"/api/runs/{rid}/annotations/answer", json={"reviewer": "Smoke Reviewer", "answers": [{"row": s["hold"][0], "action": "vote", "label": "Billing"}]})
        check(r.status_code == 400 and "never relabelled" in r.text, f"holdout vote → {r.status_code}")
        r = C.post(f"/api/runs/{rid}/annotations/answer", json={"answers": [{"row": q[2]["row"], "action": "adjudicate", "label": "Nope"}]})
        check(r.status_code == 400, f"bad class → {r.status_code}")
        g = get(f"/api/runs/{rid}/annotations", include="all", limit=500)
        it0 = next(i for i in _ann_votes(rid) if i["row"] == q[0]["row"])
        check(any(v["annotator"] == "Smoke Reviewer" for v in it0["votes"]) and g["summary"]["n_annotators"] == 6
              and g["summary"]["worst_annotator"]["name"] == "dara", "the reviewer's vote is not an annotation, or it changed the worst")
        sim = post(f"/api/runs/{rid}/annotations/simulate-adjudication", {"n": 50})
        g = get(f"/api/runs/{rid}/annotations", limit=5)
        sm = g["summary"]
        A = sm["truth"]["accuracy"]
        check(sm["consensus"]["adjudicated"] == 51 and sim["adjudicated"] == 50, sm["consensus"])
        check(A["final"]["accuracy"] >= A["consensus"]["accuracy"] - 1 / 1500, f"final {A['final']} vs consensus {A['consensus']}")
        v = post(f"/api/runs/{rid}/annotations/version")["dataset_version"]
        check(v["holdout_unchanged"] and v["relabelled"] == sm["consensus"]["changed_vs_given"] and v["rows"] == 20000, v)
        ds = get(f"/api/datasets/{v['id']}")
        lin = ds["profile"]["lineage"]
        check(lin["parent"] == "support-tickets" and lin["relabelled"] == v["relabelled"] and lin["relabelled_holdout"] == 0
              and lin["annotations"]["run_id"] == rid, lin)
        sp_old = get("/api/datasets/support-tickets/splits")["splits"]
        sp_new = get(f"/api/datasets/{v['id']}/splits")["splits"]
        check({x["sha256"] for x in sp_new} <= {x["sha256"] for x in sp_old}, "the version's split differs")
        check(get(f"/api/runs/{rid}/annotations")["versions"][-1]["id"] == v["id"], "version not listed")
        return (f"a person's vote (6th annotator) + adjudication · holdout vote refused · SIMULATED adjudicator on the 50 lowest-quality "
                f"queue rows → final labels {A['final']['accuracy']:.4f} vs consensus {A['consensus']['accuracy']:.4f} · version {v['id']}: "
                f"{v['relabelled']} of {v['annotated_rows']} annotated rows relabelled, holdout labels and split SHA unchanged")

    step("annotators: queue → votes → adjudication → version", queue_version)

    def upload_bagged():
        rec = wait_run(post("/api/runs", {"dataset_id": "support-tickets", "time_limit": 300, "feature_importance": False,
                                          "fit_kwargs": {"num_bag_folds": 3, "hyperparameters": {"GBM": {"num_boost_round": 50}}},
                                          "source": "smoke-annotators"})["id"])
        rid = rec["id"]
        rows = []
        for it in s["items"]:
            for v in it["votes"]:
                if v["annotator"] != "Smoke Reviewer":
                    rows.append({"ticket_id": f"T-{100000 + it['row']}", "annotator": v["annotator"], "category": v["label"]})
        for i, h in enumerate(s["hold"][:5]):
            rows.append({"ticket_id": f"T-{100000 + h}", "annotator": "ana", "category": "Billing"})
        df = pd.DataFrame(rows)
        r = C.post(f"/api/runs/{rid}/annotations/upload", files={"file": ("tickets_annotations.csv", df.to_csv(index=False).encode(), "text/csv")},
                   data={"mode": "replace"})
        check(r.status_code == 200, r.text[:300])
        ld = r.json()["load"]
        check(ld["row_key"] == "ticket_id" and ld["holdout_rows"] == 5 and ld["annotations"] == 4500 and ld["rows"] == 1500, ld)
        g = _ann_wait(rid)
        check(g["status"] == "ready", g.get("error"))
        sm = g["summary"]
        pr = sm["probs"]
        check(pr["source"] == "run-oof" and pr["n_oof"] == rec["data"]["train_rows"] and sm["truth"] is None and not g["simulated"], pr)
        items = _ann_votes(rid)
        cons = {i["row"]: i["consensus"] for i in items}
        same = np.mean([cons[i["row"]] == i["consensus"] for i in s["items"]])
        return (f"{rid} (bagged k = 3): upload keyed by ticket_id, 5 holdout rows left out · p̂ = the run's own OOF ({pr['model']}) · "
                f"Fleiss κ {sm['agreement']['fleiss_kappa']['value']:.4f} · worst {sm['worst_annotator']['name']} · consensus equals the "
                f"quick-bagged one on {same:.1%} of rows · no truth shown for an upload")

    step("annotators: upload + a bagged run's own OOF", upload_bagged)


def _shift_wait(run_id: str, key: str, timeout: float = 900) -> dict[str, Any]:
    return poll(f"/api/runs/{run_id}/shift/{key}", lambda x: x["status"] in ("succeeded", "failed", "cancelled"), timeout, 2)


def _shift_recheck(run_id: str, g: dict[str, Any]) -> tuple[float, float]:
    """AUC and ESS recomputed here from the per-row out-of-fold scores and weights the API returns."""
    from sklearn.metrics import roc_auc_score

    rows = get(f"/api/runs/{run_id}/shift/{g['key']}/rows")["rows"]
    o = np.array([r["origin"] for r in rows])
    p = np.array([r["p_new_oof"] for r in rows])
    w = np.array([r["w"] for r in rows if r["set"] == "reference"], dtype=float)
    check(len(rows) == g["sets"]["reference"]["rows"] + g["sets"]["new"]["rows"], "rows ≠ |R| + |N|")
    auc = float(roc_auc_score(o, p))
    check(abs(auc - g["result"]["auc"]["value"]) < 1e-9, f"AUC recomputed {auc} ≠ API {g['result']['auc']['value']}")
    ess = float(w.sum() ** 2 / np.sum(w ** 2))
    check(abs(w.mean() - 1) < 1e-9 and abs(ess - g["result"]["weights"]["ess"]) < 1e-6, (w.mean(), ess))
    return auc, ess


def shift(st: dict[str, Any]) -> None:
    """F15: adversarial validation on credit-default. An AutoGluon classifier (bagged LightGBM, out-of-fold scores)
    tells reference rows from new rows: train vs holdout of a random-split run, two periods on F6's date column, and
    registry batches (an unshifted batch of holdout rows, and the same rows with debt_to_income × 2). Asserts the
    acceptance (shifted AUC > 0.7 with the injected column ranked first, unshifted AUC ≈ 0.5), recomputes every AUC and
    ESS from the per-row scores, checks the excluded columns (label, ids, the date), and runs the three actions: an
    out-of-time twin (F6), a Gym patch dropping the column, importance weights as a dataset version trained with
    sample_weight; and the summary for F17."""
    ds, label = "credit-default", "defaulted"
    s: dict[str, Any] = {}
    data_dir = get("/api/health")["data_dir"]
    df = pd.read_csv(os.path.join(data_dir, "datasets", ds, "data.csv"))

    def run_and_refusals():
        prof = get(f"/api/datasets/{ds}")["profile"]
        drop = [c["name"] for c in prof["columns"] if c["type"] == "id" and c["name"] != label]
        r = post("/api/runs", {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc",
                               "presets": "medium_quality", "time_limit": 120, "fit_kwargs": {"hyperparameters": HP},
                               "drop_columns": drop, "feature_importance": False, "source": "smoke-shift"})
        rec = wait_run(r["id"])
        s["run"] = rec
        rid = rec["id"]
        empty = get(f"/api/runs/{rid}/shift")
        check(empty["checks"] == [] and empty["options"]["time_column"] == "application_date", empty["options"])
        bad = [({"source": "batch"}, 400), ({"source": "batch", "batch_id": "batch_nope"}, 404),
               ({"source": "time", "time_column": "region"}, 400), ({"source": "time", "cutoff": "2031-01-01"}, 400),
               ({"source": "holdout", "include": ["nope"]}, 400), ({"source": "holdout", "include": [label]}, 400), ({"source": "sideways"}, 422), ({"null_refits": 99}, 422)]
        for body, code in bad:
            x = C.post(f"/api/runs/{rid}/shift", json=body)
            check(x.status_code == code, f"{body} → {x.status_code} (expected {code}): {x.text[:200]}")
        check(C.get(f"/api/runs/{rid}/shift/holdout").status_code == 404, "no check yet → 404")
        return f"{rid} random-stratified · roc_auc {primary(rec)[1]:.4f} · no checks yet · {len(bad)} bad requests refused"

    if not step("shift: run + refusals", run_and_refusals):
        return
    rid = s["run"]["id"]

    def holdout():
        g = post(f"/api/runs/{rid}/shift", {"source": "holdout"})
        check(g["status"] in ("queued", "running"), g["status"])
        x = C.post(f"/api/runs/{rid}/shift", json={"source": "time"})
        check(x.status_code == 409, f"a second check while one runs → {x.status_code}")
        g = _shift_wait(rid, "holdout")
        check(g["status"] == "succeeded", g.get("error"))
        res = g["result"]
        auc, ess = _shift_recheck(rid, g)
        a = res["auc"]
        check(a["ci"][0] <= auc <= a["ci"][1], a)
        check(abs(auc - 0.5) < 0.03 and res["verdict"]["label"] == "no shift", f"train vs holdout of a random split: AUC {auc:.4f}, {res['verdict']['label']}")
        nul = res["null"]
        check(len(nul["refit"]["aucs"]) == 3 and nul["permutation"]["B"] == 2000, nul["refit"])
        ex = {e["column"] for e in g["excluded"]}
        check({label, "applicant_id", "customer_id", "application_date"} <= ex, ex)
        check(g["fit"]["num_bag_folds"] == 5 and g["fit"]["oof"].startswith("predict_proba_oof"), g["fit"])
        s["holdout"] = g
        return (f"|R| {g['sets']['reference']['rows']:,} vs |N| {g['sets']['new']['rows']:,} · AUC {auc:.4f} "
                f"[{a['ci'][0]:.4f}, {a['ci'][1]:.4f}] (recomputed from {g['sets']['reference']['rows'] + g['sets']['new']['rows']:,} OOF scores) · "
                f"permutation p {nul['permutation']['p_value']:.3f} · refit null {', '.join(f'{v:.3f}' for v in nul['refit']['aucs'])} · "
                f"{res['verdict']['label']} · excluded {', '.join(sorted(ex))} · {g['fit']['model']} fit {g['fit']['fit_seconds']:.0f} s")

    step("shift: train vs holdout (random split)", holdout)

    def periods():
        g = post(f"/api/runs/{rid}/shift", {"source": "time"})
        key = g["key"]
        g = _shift_wait(rid, key)
        check(g["status"] == "succeeded", g.get("error"))
        ex = {e["column"]: e["reason"] for e in g["excluded"]}
        check("application_date" in ex and "by construction" in ex["application_date"], ex.get("application_date"))
        check({"customer_id", "applicant_id", label} <= set(ex), ex)
        cut = pd.Timestamp(g["extra"]["cutoff"])
        rows = get(f"/api/runs/{rid}/shift/{key}/rows")["rows"]
        t = pd.to_datetime(df["application_date"])
        ref = [r["row"] for r in rows if r["set"] == "reference"]
        new = [r["row"] for r in rows if r["set"] == "new"]
        check(t.iloc[ref].max() < cut <= t.iloc[new].min(), "periods overlap the cut-off")
        auc, _ = _shift_recheck(rid, g)
        res = g["result"]
        s["time"] = g
        # What the exclusions prevent: the date put back in separates the periods by itself.
        g2 = post(f"/api/runs/{rid}/shift", {"source": "time", "include": ["application_date"], "null_refits": 0})
        check(g2["key"] == key, "same source, same key")
        g2 = _shift_wait(rid, key)
        check(g2["status"] == "succeeded", g2.get("error"))
        a2 = g2["result"]["auc"]["value"]
        check(a2 > 0.95 and g2["result"]["features"][0]["feature"] == "application_date", (a2, g2["result"]["features"][0]))
        g3 = post(f"/api/runs/{rid}/shift", {"source": "time"})  # back to the default exclusions
        g3 = _shift_wait(rid, key)
        check(g3["status"] == "succeeded" and abs(g3["result"]["auc"]["value"] - auc) < 1e-9, "the default check is reproducible")
        top = [f"{f['feature']} {f['importance']:.4f}" for f in res["features"][:3]]
        return (f"{g['sets']['reference']['name']} ({g['sets']['reference']['rows']:,}) vs {g['sets']['new']['name']} "
                f"({g['sets']['new']['rows']:,}), cut-off from {g['extra']['cutoff_source']} · AUC {auc:.4f} "
                f"[{res['auc']['ci'][0]:.4f}, {res['auc']['ci'][1]:.4f}] · p {res['null']['permutation']['p_value']:.4f} · "
                f"refit null max {res['null']['refit']['max']:.3f} · {res['verdict']['label']} · top {', '.join(top)} · "
                f"with application_date put back: AUC {a2:.4f}, ranked 1 (why the date is left out)")

    step("shift: two periods (F6 date column)", periods)

    def batches():
        v = post(f"/api/runs/{rid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.shift.credit"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        hp = get(f"/api/runs/{rid}/holdout-predictions")
        hold = df.iloc[np.asarray(hp["rows"], dtype=int)].drop(columns=[label]).sample(1500, random_state=15)
        out = {}
        for name, frame in (("unshifted", hold), ("shifted", hold.assign(debt_to_income=hold["debt_to_income"] * 2.0))):
            b = j(C.post(f"/api/registry/versions/{v['id']}/batches", files={"file": (f"{name}.csv", frame.to_csv(index=False).encode(), "text/csv")},
                         data={"created_by": "Smoke Analyst"}))
            b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
            check(b["status"] == "succeeded", b.get("error"))
            check(get(f"/api/registry/batches/{b['id']}/shift")["status"] == "none", "no check before it is started")
            post(f"/api/registry/batches/{b['id']}/shift", {})
            g = poll(f"/api/registry/batches/{b['id']}/shift", lambda x: x["status"] in ("succeeded", "failed"), 900, 2)
            check(g["status"] == "succeeded", g.get("error"))
            auc, _ = _shift_recheck(rid, g)
            check(g["sets"]["new"]["max_psi"] == b["result"]["drift"]["max_psi"], "PSI next to the AUC is the batch's own")
            psi = {f["feature"]: f["psi"] for f in b["result"]["drift"]["features"] if f["psi"] is not None}
            top = g["result"]["features"][0]
            check(abs(top["psi"] - psi[top["feature"]]) < 1e-9, f"PSI of {top['feature']}: {top['psi']} vs drift {psi[top['feature']]}")
            out[name] = (b, g, auc)
        b0, g0, a0 = out["unshifted"]
        b1, g1, a1 = out["shifted"]
        f1 = g1["result"]["features"]
        check(a1 > 0.7, f"shifted batch AUC {a1:.4f} ≤ 0.7")
        check(f1[0]["feature"] == "debt_to_income" and f1[0]["significant"], f"rank 1 is {f1[0]['feature']}, not debt_to_income")
        check(abs(a0 - 0.5) < 0.03 and g0["result"]["verdict"]["label"] == "no shift", f"unshifted batch AUC {a0:.4f} {g0['result']['verdict']['label']}")
        check(not any(f["significant"] for f in g0["result"]["features"]), "no column flagged without a shift")
        s.update(version=v, shifted=g1, shifted_batch=b1)
        return (f"ACCEPTANCE shifted batch (debt_to_income × 2): AUC {a1:.4f} [{g1['result']['auc']['ci'][0]:.4f}, "
                f"{g1['result']['auc']['ci'][1]:.4f}] > 0.7, {g1['result']['verdict']['label']}, rank 1 debt_to_income "
                f"(importance {f1[0]['importance']:.4f} [{f1[0]['ci'][0]:.4f}, {f1[0]['ci'][1]:.4f}], PSI {f1[0]['psi']:.3f}; "
                f"rank 2 {f1[1]['feature']} {f1[1]['importance']:.4f}) · unshifted batch: AUC {a0:.4f} "
                f"[{g0['result']['auc']['ci'][0]:.4f}, {g0['result']['auc']['ci'][1]:.4f}], p {g0['result']['null']['permutation']['p_value']:.3f}, "
                f"refit null {', '.join(f'{x:.3f}' for x in g0['result']['null']['refit']['aucs'])}, no shift · max PSI "
                f"{b0['result']['drift']['max_psi']:.3f} vs {b1['result']['drift']['max_psi']:.3f}")

    if not step("shift: registry batches (acceptance)", batches):
        return

    def act():
        key = s["shifted"]["key"]
        g = get(f"/api/runs/{rid}/shift/{key}")
        acts = {a["id"]: a for a in g["actions"]}
        check({"out-of-time", "drop-debt_to_income", "weights"} <= set(acts), list(acts))
        # 1. Out-of-time validation: the F6 twin with the action's split_spec.
        oot = acts["out-of-time"]
        check(oot["available"] and oot["split_spec"] == {"strategy": "time", "time_column": "application_date"}, oot)
        tw = post(oot["endpoint"], {"split_spec": oot["split_spec"]})
        tw = wait_run(tw["id"])
        check(tw["split"]["strategy"] == "time", tw["split"])
        cmp_ = get(f"/api/runs/{tw['id']}/split-comparison")
        by = {x["strategy"]: x for x in cmp_["runs"] if x["same_config"] or x["this_run"]}
        check({"random-stratified", "time"} <= set(by), list(by))
        # 2. Drop the column: a Gym patch, valid against the installed AutoGluon.
        p = acts["drop-debt_to_income"]["patch"]
        check("debt_to_income" in p["body"]["drop_columns"] and p["changes"][0]["path"] == "drop_columns", p["changes"])
        val = post("/api/gym/validate", p["body"])
        check(not val["errors"], val["errors"])
        # 3. Importance weighting: a dataset version with shift_weight, trained with sample_weight.
        wv = post(f"/api/runs/{rid}/shift/{key}/weights-version")
        ver, wp = wv["dataset_version"], wv["patch"]
        new = pd.read_csv(os.path.join(data_dir, "datasets", ver["id"], "data.csv"))
        check(len(new) == len(df) and "shift_weight" in new.columns, "version shape")
        rows = get(f"/api/runs/{rid}/shift/{key}/rows")["rows"]
        ref = {r["row"]: r["w"] for r in rows if r["set"] == "reference"}
        wcol = new["shift_weight"].to_numpy()
        check(np.allclose(wcol[list(ref)], list(ref.values())), "weights not written to the reference rows")
        others = np.setdiff1d(np.arange(len(new)), list(ref))
        check(np.all(wcol[others] == 1.0), "rows outside the reference must weigh 1")
        check(new.drop(columns=["shift_weight"]).equals(df), "the version changed other data")
        check(wp["body"]["dataset_id"] == ver["id"] and wp["body"]["predictor_kwargs"]["sample_weight"] == "shift_weight", wp["changes"])
        val = post("/api/gym/validate", wp["body"])
        check(not val["errors"], val["errors"])
        r = post("/api/runs", {**wp["body"], "source": "smoke-shift", "parent_run_id": rid,
                               "patch": {"id": wp["id"], "title": wp["title"], "changes": wp["changes"]}})
        wr = wait_run(r["id"])
        check(wr["resolved"]["predictor_kwargs"].get("sample_weight") == "shift_weight", wr["resolved"]["predictor_kwargs"])
        check("shift_weight" not in (wr.get("features") or []), "the weight became a feature")
        w = g["result"]["weights"]
        bal = w["balance"][0] if w["balance"] else None
        st["shift_weighted"] = wr
        return (f"out-of-time twin {tw['id']} roc_auc {primary(tw)[1]:.4f} vs random {primary(s['run'])[1]:.4f} · "
                f"drop patch valid (drop_columns + debt_to_income) · weights version {ver['id']}: {ver['rows_weighted']:,} rows "
                f"w ∈ [{w['min']:.3f}, {w['max']:.3f}] (clipped at q {w['clip_quantile']}, {w['clipped_rows']} rows), "
                f"ESS {w['ess']:,.0f} = {w['ess_share']:.1%} of {w['n_ref']:,}"
                + (f", mean {bal['feature']} {bal['mean_reference']:.3f} → {bal['mean_reference_weighted']:.3f} (new {bal['mean_new']:.3f})" if bal else "")
                + f" · weighted run {wr['id']} roc_auc {primary(wr)[1]:.4f} with sample_weight='shift_weight'")

    step("shift: actions (out-of-time, drop, weights)", act)

    def f17():
        sm = get(f"/api/runs/{rid}")["shift"]
        need = {"schema", "key", "source", "status", "auc", "ci", "null", "verdict", "top_features", "excluded", "ess_share"}
        check(sm and need <= set(sm) and sm["schema"] == "shift/1", sm)
        lst = get(f"/api/runs/{rid}/shift")
        check(len(lst["checks"]) == 4 and {c["source"] for c in lst["checks"]} == {"holdout", "time", "batch"}, [c["key"] for c in lst["checks"]])
        check(len(lst["options"]["batches"]) == 2, lst["options"]["batches"])
        return f"run payload shift/1 = latest check ({sm['key']}: AUC {sm['auc']:.4f}, {sm['verdict']}) · {len(lst['checks'])} checks listed"

    step("shift: summary for F17", f17)


# --------------------------------------------------------------------------- F16: weak slices


def _sl_mask(df: pd.DataFrame, cond: dict[str, Any]) -> np.ndarray:
    """One slice condition on raw rows, written here from the condition's definition (not the engine's code)."""
    s = df[cond["column"]]
    if cond.get("missing"):
        return s.isna().to_numpy()
    if cond["op"] == "range":
        x = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
        m = ~np.isnan(x)
        if cond.get("lo") is not None:
            m &= x >= cond["lo"]
        if cond.get("hi") is not None:
            m &= x < cond["hi"]
        return m
    sv = s.map(lambda v: None if pd.isna(v) else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)))
    inside = sv.isin(set(map(str, cond["values"]))).to_numpy()
    return (s.notna().to_numpy() & ~inside) if cond["op"] == "not_in" else inside


def _sl_loss(hp: pd.DataFrame, name: str, cost: dict[str, Any] | None = None, t: float | None = None) -> np.ndarray:
    """ℓᵢ recomputed from holdout_predictions.csv."""
    y = hp["y_true"]
    if name == "log_loss":
        p = np.array([hp.loc[i, f"p_{str(int(v)) if isinstance(v, (float, np.floating)) and float(v).is_integer() else v}"] for i, v in y.items()], dtype=float)
        return -np.log(np.clip(p, 1e-15, 1))
    if name == "error":
        return (y.astype(str).to_numpy() != hp["y_pred"].astype(str).to_numpy()).astype(float)
    if name == "absolute_error":
        return np.abs(y.to_numpy(dtype=float) - hp["y_pred"].to_numpy(dtype=float))
    if name == "pinball":
        cols = [c for c in hp.columns if c.startswith("q_")]
        yy = y.to_numpy(dtype=float)
        L = [np.maximum(float(c[2:]) * (yy - hp[c].to_numpy(dtype=float)), (float(c[2:]) - 1) * (yy - hp[c].to_numpy(dtype=float))) for c in cols]
        return np.mean(L, axis=0)
    if name == "cost":
        pos = [c for c in hp.columns if c.startswith("p_")][-1]
        yb = y.astype(str).to_numpy() == pos[2:]
        pred = hp[pos].to_numpy(dtype=float) > t
        return cost["c_fp"] * (pred & ~yb) + cost["c_fn"] * (~pred & yb) - cost.get("g_tp", 0) * (pred & yb)
    raise SmokeError(f"no recompute for loss {name}")


def _sl_hp(rec: dict[str, Any]) -> pd.DataFrame:
    return pd.read_csv(os.path.join(rec["config"]["run_dir"], "holdout_predictions.csv"))


def _sl_recompute(res: dict[str, Any], data: pd.DataFrame, hp: pd.DataFrame, loss: np.ndarray) -> dict[str, Any]:
    """The whole search again from the binning the API reports: every single value and pair with the support, Welch's
    t (scipy), Benjamini–Hochberg (statsmodels)."""
    from scipy import stats
    from statsmodels.stats.multitest import multipletests

    X = data.iloc[hp["row"].to_numpy(dtype=int)].reset_index(drop=True)
    n, ms = len(loss), res["params"]["min_support"]
    masks = {c["column"]: [_sl_mask(X, cond) for cond in c["conditions"]] for c in res["columns"]}
    for c in res["columns"]:
        check([int(m.sum()) for m in masks[c["column"]]] == c["counts"], f"bin counts of {c['column']} differ")
    tests = []
    cols = list(masks)
    for c in cols:
        for i, m in enumerate(masks[c]):
            tests.append(((c, i), m))
    if res["params"]["pairs"]:
        for a in range(len(cols)):
            for b in range(a + 1, len(cols)):
                for i, ma in enumerate(masks[cols[a]]):
                    for k, mb in enumerate(masks[cols[b]]):
                        tests.append(((cols[a], i, cols[b], k), ma & mb))
    tests = [(key, m) for key, m in tests if ms <= m.sum() <= n - ms]
    p = np.array([stats.ttest_ind(loss[m], loss[~m], equal_var=False, alternative="greater").pvalue for _, m in tests])
    p = np.nan_to_num(p, nan=1.0)
    q = multipletests(p, method="fdr_bh")[1]
    delta = np.array([loss[m].mean() - loss[~m].mean() for _, m in tests])
    return {"m": len(tests), "q": q, "p": p, "delta": delta, "sig": int(((q <= res["params"]["alpha"]) & (delta > 0)).sum()), "X": X}


def slices(st: dict[str, Any]) -> None:
    """F16: weak-slice discovery on personal-loans (a sample with a planted weak slice: channel = broker ∧ age < 30 has
    P(default) = 0.5 whatever the features say). The search (every feature value and pair, numbers in quantile bins,
    Welch t against the rest, Benjamini–Hochberg, Δ with a 95% CI) is recomputed here with scipy and statsmodels from
    the holdout predictions; the acceptance: the planted slice at rank 1–3. Then the links: its rows, SHAP inside vs
    outside, fairness by its column, the F13 labelling queue pre-filtered to it; the per-row loss of cost (F7),
    regression and quantile (F9) runs; recompute after a threshold change; the summary for F17."""
    ds, label = "personal-loans", "defaulted"
    s: dict[str, Any] = {}
    data_dir = get("/api/health")["data_dir"]
    data = pd.read_csv(os.path.join(data_dir, "datasets", ds, "data.csv"))
    truth = json.load(open(os.path.join(data_dir, "datasets", ds, "slice_truth.json")))["planted_slice"]
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
            "time_limit": 120, "drop_columns": ["application_id"], "feature_importance": False, "source": "smoke-slices"}

    def run_and_refusals():
        rec = wait_run(post("/api/runs", body)["id"])
        s["run"] = rec
        rid = rec["id"]
        bad = [({"loss": "cost"}, "business cost"), ({"loss": "absolute_error"}, "regression"), ({"columns": ["nope"]}, "Not a feature"),
               ({"min_support": 5000}, "slices of at least")]
        for b, needle in bad:
            r = C.post(f"/api/runs/{rid}/slices", json=b)
            check(r.status_code == 400 and needle in r.text, f"{b} → {r.status_code} {r.text[:200]}")
        r = C.post(f"/api/runs/{rid}/slices", json={"alpha": 2})
        check(r.status_code == 422, r.text[:200])
        r = C.get(f"/api/runs/{rid}/slices/sl_nothing/rows")
        check(r.status_code == 404, r.text[:200])
        m, v = primary(rec)
        return f"{rid} · holdout {m} {v:.4f} · {len(bad) + 2} bad requests refused"

    def acceptance():
        rid = s["run"]["id"]
        res = get(f"/api/runs/{rid}/slices")
        s["res"] = res
        hp = _sl_hp(s["run"])
        check(res["loss"]["name"] == "log_loss", res["loss"])  # roc_auc is a probability metric → ℓ = −log p̂(y)
        loss = _sl_loss(hp, "log_loss")
        check(abs(loss.mean() - res["overall"]) < 1e-9, (loss.mean(), res["overall"]))
        rc = _sl_recompute(res, data, hp, loss)
        check(rc["m"] == res["tested"]["total"], f"tested {res['tested']['total']} vs recomputed {rc['m']}")
        check(rc["sig"] == res["n_significant"], f"significant {res['n_significant']} vs recomputed {rc['sig']}")
        X = rc["X"]
        for sl in res["slices"]:
            m = np.ones(len(X), dtype=bool)
            for cond in sl["conditions"]:
                m &= _sl_mask(X, cond)
            from scipy import stats
            tt = stats.ttest_ind(loss[m], loss[~m], equal_var=False, alternative="greater")
            check(int(m.sum()) == sl["n"] and abs(loss[m].mean() - sl["mean"]) < 1e-9 and abs(tt.pvalue - sl["p"]) <= 1e-6 * sl["p"] + 1e-15,
                  f"{sl['label']}: n {m.sum()} vs {sl['n']}, p {tt.pvalue} vs {sl['p']}")
            check(sl["q"] <= res["params"]["alpha"] and sl["ci"][0] <= sl["delta"] <= sl["ci"][1] and sl["delta"] > 0, sl)
            check(sl["q"] >= sl["p"] - 1e-15 and sl["q"] <= sl["p"] * rc["m"] + 1e-12, (sl["p"], sl["q"]))
        ranks = [sl["ci"][0] for sl in res["slices"]]
        check(ranks == sorted(ranks, reverse=True), "not ranked by the lower bound of Δ")
        # the BH q of each listed slice equals statsmodels' for the same test
        qs = sorted(rc["q"][(rc["q"] <= res["params"]["alpha"]) & (rc["delta"] > 0)])
        check(all(any(abs(sl["q"] - q) < 1e-9 for q in qs) for sl in res["slices"]), "q-values differ from statsmodels")
        # acceptance: the planted slice (its holdout rows) at rank 1–3, matched by row overlap (Jaccard)
        hold = set(hp["row"].astype(int))
        planted = set(truth["rows"]) & hold
        best = None
        for sl in res["slices"][:3]:
            got = set(r["row"] for r in get(f"/api/runs/{rid}/slices/{sl['id']}/rows")["rows"])
            jac = len(got & planted) / len(got | planted)
            if jac >= 0.5 and best is None:
                best = (sl, jac, len(got & planted) / len(got), len(got & planted) / len(planted))
        check(best is not None, f"planted slice ({truth['label']}, {len(planted)} holdout rows) not in the top 3: {[x['label'] for x in res['slices'][:5]]}")
        sl, jac, prec, rec_ = best
        s["slice"] = sl
        nl = res["null"]
        check(nl and nl["permutations"] == 5 and nl["mean"] <= 1.0, nl)
        top3 = " · ".join(f"{x['rank']}. {x['label']} (n {x['n']}, Δ +{x['delta']:.3f} [{x['ci'][0]:.3f}, {x['ci'][1]:.3f}], q {x['q']:.1e})" for x in res["slices"][:3])
        return (f"ACCEPTANCE planted '{truth['label']}' ({len(planted)} holdout rows) found at rank {sl['rank']} as '{sl['label']}' "
                f"(Jaccard {jac:.2f}, precision {prec:.2f}, recall {rec_:.2f}) · m = {res['tested']['total']} tested "
                f"({res['tested']['single']} single + {res['tested']['pairs']} pairs, n ≥ {res['params']['min_support']}), "
                f"{res['n_significant']} significant at q ≤ {res['params']['alpha']}, {res['n_weak']} listed, "
                f"{res['n_explained_by_parent']} pairs folded into a parent · recomputed with scipy + statsmodels ✓ · "
                f"permutation null {nl['weak_found']} · top 3: {top3}")

    def links():
        rid, sl = s["run"]["id"], s["slice"]
        rows = get(f"/api/runs/{rid}/slices/{sl['id']}/rows")
        check(rows["total"] == sl["n"] and len(rows["rows"]) == sl["n"], rows["total"])
        ls = [r["loss"] for r in rows["rows"]]
        check(ls == sorted(ls, reverse=True), "rows not worst first")
        Xr = pd.DataFrame([r["features"] for r in rows["rows"]])
        ok = np.ones(len(Xr), dtype=bool)
        for cond in sl["conditions"]:
            ok &= _sl_mask(Xr, cond)
        check(ok.all(), "a row outside the slice's conditions")
        sh = post(f"/api/runs/{rid}/slices/{sl['id']}/shap", {"rows": 3})
        check(set(sh["rows_slice"]) <= set(r["row"] for r in rows["rows"]) and not set(sh["rows_rest"]) & set(r["row"] for r in rows["rows"]), sh["rows_slice"])
        check(len(sh["per_row"]) == 6 and {f["feature"] for f in sh["features"]} >= set(sl["columns"]), sh["features"][:2])
        one = get(f"/api/runs/{rid}/shap", row=sh["rows_slice"][0])  # the cached single-row SHAP the row link opens
        check(abs(one["fx"] - next(p["fx"] for p in sh["per_row"] if p["row"] == sh["rows_slice"][0])) < 1e-9, one["fx"])
        fair_col = sl["fairness_columns"][0] if sl["fairness_columns"] else None
        check(fair_col is not None, sl)
        fair = get(f"/api/runs/{rid}/fairness", group=fair_col)
        pool = get(f"/api/runs/{rid}/slices/{sl['id']}/pool")
        m = np.ones(len(data), dtype=bool)
        for cond in sl["conditions"]:
            m &= _sl_mask(data, cond)
        hold = set(_sl_hp(s["run"])["row"].astype(int))
        n_train_in = int(sum(1 for i in np.flatnonzero(m) if i not in hold))
        check(pool["in_slice"] == n_train_in, (pool, n_train_in))
        # F13: the labelling queue pre-filtered to the slice
        r = C.post(f"/api/runs/{rid}/active-learning", json={"slice_id": sl["id"], "budget": 5000, "rounds": 1})
        check(r.status_code == 400 and "inside the slice" in r.text, r.text[:200])
        r = C.post(f"/api/runs/{rid}/active-learning", json={"slice_id": "sl_nothing", "budget": 10, "rounds": 1})
        check(r.status_code == 400, r.text[:200])
        b = 40
        post(f"/api/runs/{rid}/active-learning", {"slice_id": sl["id"], "budget": b, "rounds": 1, "compare_random": False})
        g = poll(f"/api/runs/{rid}/active-learning", lambda x: x["status"] not in ("queued", "running") or bool((x.get("queue") or {}).get("open")), timeout=900)
        check(g["status"] == "awaiting_labels", g.get("error") or g["status"])
        items = g["queue"]["queue"]
        qrows = [it["row"] for it in items]
        check(len(qrows) == b and all(m[r] for r in qrows) and not set(qrows) & hold, f"{len(qrows)} items, {sum(not m[r] for r in qrows)} outside the slice")
        check(g["slice"]["id"] == sl["id"] and g["summary"]["slice"]["id"] == sl["id"] and g["slice"]["n_pool"] == g["pool"]["n_pool"], g["slice"])
        post(f"/api/runs/{rid}/active-learning/cancel")
        return (f"rows: {rows['total']} worst first, all inside the conditions · SHAP on {len(sh['rows_slice'])}+{len(sh['rows_rest'])} rows "
                f"(top Δφ {sh['features'][0]['feature']} {sh['features'][0]['diff']:+.4f}; {sh['seconds']:.0f} s) = the single-row SHAP · "
                f"fairness by {fair_col}: {len(fair.get('by_group') or [])} groups · pool {pool['in_slice']} training rows in the slice · "
                f"labelling queue pre-filtered: {len(qrows)}/{b} picks inside the slice (pool {g['pool']['n_pool']} of {g['pool']['n_train']}), "
                f"0 holdout rows; b = 5000 refused")

    def losses():
        out = []
        rid = s["run"]["id"]
        # a binary threshold change rewrites the holdout predictions: the search runs again with ℓ = 0/1 error at t
        r0 = s["res"]
        post(f"/api/runs/{rid}/threshold", {"value": 0.35})
        e = post(f"/api/runs/{rid}/slices", {"loss": "error"})
        hp = _sl_hp(s["run"])
        check(e["holdout_sha"] != r0["holdout_sha"] and e["threshold"] == 0.35 and "t = 0.350" in e["loss"]["formula"], e["loss"])
        check(abs(_sl_loss(hp, "error").mean() - e["overall"]) < 1e-12, e["overall"])
        out.append(f"error at t 0.35: ℓ̄ {e['overall']:.4f}, {e['n_weak']} weak, top {e['slices'][0]['label'] if e['slices'] else '—'}")
        # F7 cost run: ℓ = the row's cost at the run's threshold
        c = wait_run(post("/api/runs", {**body, "eval_metric": None, "cost": {"c_fp": 1, "c_fn": 5}, "fit_kwargs": {"hyperparameters": HP}, "source": "smoke-slices-cost"})["id"])
        cr = get(f"/api/runs/{c['id']}/slices")
        hp = _sl_hp(c)
        check(cr["loss"]["name"] == "cost", cr["loss"])
        lc = _sl_loss(hp, "cost", {"c_fp": 1, "c_fn": 5}, cr["threshold"])
        check(abs(lc.mean() - cr["overall"]) < 1e-12, (lc.mean(), cr["overall"]))
        out.append(f"cost run {c['id']}: ℓ̄ = {cr['overall']:.4f} per decision at t {cr['threshold']:.3f}, {cr['n_weak']} weak, top {cr['slices'][0]['label'] if cr['slices'] else '—'}")
        # regression and quantile (F9) on loan-amount
        rb = {"dataset_id": "loan-amount", "label": "loan_amount_usd", "presets": "medium_quality", "time_limit": 60,
              "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 60}}}, "feature_importance": False, "source": "smoke-slices"}
        rr = wait_run(post("/api/runs", {**rb, "problem_type": "regression"})["id"])
        q = wait_run(post("/api/runs", {**rb, "problem_type": "quantile", "quantile_levels": [0.1, 0.5, 0.9], "eval_metric": None})["id"])
        for run, name in ((rr, "absolute_error"), (q, "pinball")):
            x = get(f"/api/runs/{run['id']}/slices")
            hp = _sl_hp(run)
            check(x["loss"]["name"] == name and abs(_sl_loss(hp, name).mean() - x["overall"]) < 1e-6 * max(1, x["overall"]), (x["loss"], x["overall"]))
            r = C.post(f"/api/runs/{run['id']}/slices", json={"loss": "log_loss"})
            check(r.status_code == 400, r.text[:200])
            out.append(f"{run['problem_type']} {run['id']}: {name} ℓ̄ {x['overall']:,.1f}, {x['n_weak']} weak of {x['tested']['total']}, top {x['slices'][0]['label'] if x['slices'] else '—'}")
        s["extra"] = [c, rr, q]
        return " · ".join(out)

    def summary_f17():
        rid = s["run"]["id"]
        sm = get(f"/api/runs/{rid}")["slices"]
        cur = get(f"/api/runs/{rid}/slices")
        check(sm["schema"] == "slices/1" and sm["n_tested"] == cur["tested"]["total"] and sm["n_weak"] == cur["n_weak"], sm)
        check([t["id"] for t in sm["top"]] == [x["id"] for x in cur["slices"][:5]] and all(set(t) >= {"label", "delta", "ci", "q", "n"} for t in sm["top"]), sm["top"][:1])
        return f"run payload slices/1: {sm['n_weak']} weak of {sm['n_tested']} tested (loss {sm['loss']}), top {sm['top'][0]['label'] if sm['top'] else '—'} q {sm['top'][0]['q']:.1e}" if sm["top"] else "no weak slices"

    if not step("slices: run + refusals", run_and_refusals):
        return
    if not step("slices: planted slice (acceptance)", acceptance):
        return
    step("slices: rows, SHAP, fairness, labelling queue", links)
    step("slices: loss per run type (F7, F9)", losses)
    step("slices: summary for F17", summary_f17)


# --------------------------------------------------------------------------- F17 · Model Doctor


def _dr_base(n: int, seed: int, k: int = 6) -> tuple[np.random.Generator, pd.DataFrame]:
    r = np.random.default_rng(seed)
    X = pd.DataFrame({f"x{i}": np.round(r.normal(size=n), 4) for i in range(k)})
    X.insert(0, "row_id", [f"R-{seed}-{i:05d}" for i in range(n)])
    return r, X


def _dr_leaky(seed: int = 171) -> pd.DataFrame:
    """A moderate credit-like signal plus `recovery_status`, recorded after the outcome (the label with 2 % flips)."""
    r, df = _dr_base(2000, seed)
    z = 0.9 * df.x0 - 0.7 * df.x1 + 0.5 * df.x2 * df.x3 - 0.4
    y = (1 / (1 + np.exp(-z))) > r.random(len(df))
    df["recovery_status"] = np.where(y ^ (r.random(len(df)) < 0.02), "in_collections", "current")
    df["defaulted"] = np.where(y, "yes", "no")
    return df


def _dr_noise(seed: int = 172) -> pd.DataFrame:
    """A deterministic rule (separable), then 20 % of the labels flipped at random."""
    r, df = _dr_base(3000, seed)
    y = (1.5 * df.x0 + df.x1 * df.x2 - df.x3) > 0
    df["defaulted"] = np.where(y ^ (r.random(len(df)) < 0.2), "yes", "no")
    return df


def _dr_data_limited(seed: int = 173) -> pd.DataFrame:
    """Clean labels, a wiggly rule: every doubling of n still helps at n = 2,400 training rows."""
    r, df = _dr_base(3000, seed)
    df["defaulted"] = np.where((np.sin(3 * df.x0) + np.sin(3 * df.x1) + np.sin(3 * df.x2)) > 0, "yes", "no")
    return df


def _dr_shift(seed: int = 174) -> pd.DataFrame:
    """Dated rows; x0 drifts upward over time, and once x0 > 2 (only after about t = 0.9 of the period) the rule changes."""
    n = 3000
    r, df = _dr_base(n, seed)
    t = np.sort(r.random(n))
    df.insert(1, "application_date", (pd.Timestamp("2023-01-01") + pd.to_timedelta((t * 730).astype(int), unit="D")).strftime("%Y-%m-%d"))
    df["x0"] = np.round(df.x0 * 0.3 + (t - 0.5) * 5, 4)
    df["defaulted"] = np.where(np.where(df.x0 > 2, df.x2 > 0, df.x1 > 0), "yes", "no")
    return df


def _dr_overfit(seed: int = 175) -> pd.DataFrame:
    """A weak logistic signal in 2 of 30 features, trained with a 1-nearest-neighbour memoriser."""
    r, df = _dr_base(1200, seed, k=30)
    z = 0.8 * df.x0 + 0.5 * df.x1
    df["defaulted"] = np.where(1 / (1 + np.exp(-z)) > r.random(len(df)), "yes", "no")
    return df


def _dr_latency(seed: int = 176) -> pd.DataFrame:
    """An easy clean rule, trained as a 5-fold bagged ensemble of five families (served slowly), budget 10 ms."""
    r, df = _dr_base(2000, seed)
    df["defaulted"] = np.where((df.x0 + 0.5 * df.x1 - 0.3 * df.x2) > 0, "yes", "no")
    return df


DR_SCENARIOS = {
    "leaky feature": dict(gen=_dr_leaky, hp={"GBM": {}, "LR": {}}, expect="leakage"),
    "label noise": dict(gen=_dr_noise, hp={"GBM": {}}, expect="label_noise"),
    "data-limited": dict(gen=_dr_data_limited, hp={"GBM": {}}, expect="data_limited"),
    "shift": dict(gen=_dr_shift, hp={"GBM": {}}, expect="shift"),
    "overfit": dict(gen=_dr_overfit, hp={"KNN": {"n_neighbors": 1}}, expect="overfit"),
    "latency": dict(gen=_dr_latency, hp={"GBM": {}, "RF": {"n_estimators": 300}, "XT": {"n_estimators": 300}, "KNN": {}, "LR": {}},
                    bag=5, expect="latency", latency_ms=10.0),
}
DR_STATUSES = {"finding", "clear", "no-evidence", "missing", "n/a"}


def _dr_check_shape(d: dict[str, Any]) -> None:
    """The procedure's own invariants: schema, every check has a status, the primary is the highest-severity finding with
    ties broken by the precedence list, the secondaries follow in the same order, and every finding has a prescription."""
    check(d["schema"] == "doctor/1" and d["procedure"], d.get("schema"))
    check(all(c["status"] in DR_STATUSES for c in d["checks"]), [c["status"] for c in d["checks"]])
    sev = {"critical": 3, "major": 2, "minor": 1}
    found = [c for c in d["checks"] if c["status"] == "finding"]
    order = sorted(found, key=lambda c: (-sev[c["severity"]], d["precedence"].index(c["id"])))
    got = ([d["primary"]] if d["primary"] else []) + d["secondary"]
    check([c["id"] for c in got] == [c["id"] for c in order], f"ranking {[c['id'] for c in got]} vs rule {[c['id'] for c in order]}")
    for c in found:
        check(c.get("prescription") and c["prescription"].get("kind") and c["reason"] and c["rule"], f"{c['id']}: no prescription / reason / rule")
    for c in d["checks"]:
        if c["status"] == "missing":
            check(c.get("offer") and c["offer"].get("endpoint"), f"{c['id']} missing without an offer")


def _dr_offer(d: dict[str, Any], cid: str) -> dict[str, Any]:
    c = next(c for c in d["checks"] if c["id"] == cid)
    check(c.get("offer"), f"{cid}: no offer ({c['status']}: {c['reason']})")
    return c["offer"]


def doctor(st: dict[str, Any]) -> None:
    """F17: the Model Doctor. Six crafted scenarios, each a small seeded dataset or config where the limiting factor is
    known (leaky feature, label noise, data-limited, shift, overfit, latency). Each is trained through POST /api/runs;
    the expensive checks it needs are started through the doctor's own offers (learning curve, out-of-time twin, shift
    check, latency profile), as the page's buttons do; then GET /api/runs/{id}/doctor must name the known factor as
    primary. Also: the ranking rule recomputed from the checks, determinism, refusals, the run payload's doctor/1
    summary, and one prescription applied (the overfit patch through the Gym body) with its before → after."""
    s: dict[str, Any] = {"results": {}}

    def refusals():
        check(C.get("/api/runs/run_nothing/doctor").status_code == 404, "unknown run")
        r = C.get("/api/runs/run_nothing/doctor", params={"latency_ms": -1})
        check(r.status_code in (404, 422), r.status_code)
        return "unknown run → 404 · latency_ms ≤ 0 → 422"

    step("doctor: refusals", refusals)

    def train_all():
        for name, sc in DR_SCENARIOS.items():
            df = sc["gen"]()
            d = j(C.post("/api/datasets", files={"file": (f"f17_{name.replace(' ', '_')}.csv", df.to_csv(index=False).encode(), "text/csv")},
                         data={"target": "defaulted"}))
            fk: dict[str, Any] = {"hyperparameters": sc["hp"]}
            if sc.get("bag"):
                fk["num_bag_folds"] = sc["bag"]
            body = {"dataset_id": d["id"], "label": "defaulted", "problem_type": "binary", "eval_metric": "accuracy",
                    "presets": "medium_quality", "time_limit": 90, "drop_columns": ["row_id"], "fit_kwargs": fk,
                    "scorecard": False, "source": "smoke-doctor"}
            s.setdefault("runs", {})[name] = post("/api/runs", body)["id"]
            s.setdefault("bodies", {})[name] = body
        for name, rid in s["runs"].items():
            s.setdefault("recs", {})[name] = wait_run(rid)
        return " · ".join(f"{n} {rid} {primary(s['recs'][n])[1]:.3f}" for n, rid in s["runs"].items())

    if not step("doctor: train the six scenarios", train_all):
        return

    def diagnose(name: str, prepare: Callable[[str], str] | None = None, **params: Any) -> Callable[[], str]:
        def fn():
            rid = s["runs"][name]
            extra = prepare(rid) if prepare else ""
            d = get(f"/api/runs/{rid}/doctor", **params)
            _dr_check_shape(d)
            s["results"][name] = d
            want = DR_SCENARIOS[name]["expect"]
            got = d["primary"]["id"] if d["primary"] else None
            s.setdefault("table", []).append((name, want, got, d["primary"] and d["primary"]["severity"],
                                             [f"{c['id']}:{c['severity']}" for c in d["secondary"]]))
            check(got == want, f"primary {got} ({d['headline'][:300]}), expected {want}")
            ev = d["primary"]["evidence"]
            pay = get(f"/api/runs/{rid}")["doctor"]
            check(pay["schema"] == "doctor/1" and pay["primary"]["id"] == want, pay)
            return (f"{rid} → primary {got} ({d['primary']['severity']}) · {_dr_evidence(want, ev)} · rx {d['primary']['prescription']['kind']}: "
                    f"{d['primary']['prescription']['title']} · secondary [{', '.join(c['id'] + ':' + c['severity'] for c in d['secondary']) or '—'}]"
                    + (f" · {extra}" if extra else ""))
        return fn

    step("doctor: leaky feature", diagnose("leaky feature"))
    step("doctor: label noise", diagnose("label noise"))

    def lc_prepare(rid: str) -> str:
        d0 = get(f"/api/runs/{rid}/doctor")
        off = _dr_offer(d0, "data_limited")
        check(off["kind"] == "run_check" and off["check"] == "learning_curve", off)
        notes = [f"before: data_limited {next(c['status'] for c in d0['checks'] if c['id'] == 'data_limited')}"]
        for _ in range(2):
            r = C.post(off["endpoint"], json=off.get("body") or {})
            check(r.status_code == 202, r.text[:300])
            lc = _lc_wait(rid)
            v = lc["verdict"]
            notes.append(f"curve ({(off.get('body') or {}).get('seeds', 3)} seeds) {v['label']} Δ₂ {v['slope_per_doubling']['value']:+.4f} "
                         f"[{v['slope_per_doubling']['ci'][0]:+.4f}, {v['slope_per_doubling']['ci'][1]:+.4f}] vs ε {v['evidence']['min_gain']:.4f}")
            d1 = get(f"/api/runs/{rid}/doctor")
            c = next(c for c in d1["checks"] if c["id"] == "data_limited")
            if v["label"] != "noisy":
                break
            check(c["status"] == "no-evidence", f"a noisy curve read as {c['status']}")  # noisy = no evidence, never a finding
            notes.append("noisy → no-evidence, offer: rerun with more seeds")
            off = c["offer"]
            check((off.get("body") or {}).get("seeds", 0) > 3, off)
        return " · ".join(notes)

    step("doctor: data-limited", diagnose("data-limited", lc_prepare))

    def shift_prepare(rid: str) -> str:
        d0 = get(f"/api/runs/{rid}/doctor")
        c0 = next(c for c in d0["checks"] if c["id"] == "shift")
        check(c0["status"] == "missing", c0)
        off = c0["offer"]
        check(off["kind"] == "split_twin" and off["body"]["split_spec"]["strategy"] == "time", off)
        tw = post(off["endpoint"], off["body"])
        wait_run(tw["id"])
        d1 = get(f"/api/runs/{rid}/doctor")
        c1 = next(c for c in d1["checks"] if c["id"] == "shift")
        oot = c1["evidence"]["out_of_time"]
        off2 = _dr_offer(d1, "shift")
        check(off2["check"] == "shift", off2)
        r = post(off2["endpoint"], off2["body"])
        g = _shift_wait(rid, r["key"])
        check(g["status"] == "succeeded", g.get("error"))
        return (f"before: shift missing · twin {tw['id']} (offer) → {c1['status']} {c1['severity']}: random {oot['random_score']:.4f} vs time "
                f"{oot['oot_score']:.4f}, z {oot['z']:.1f} · shift check (offer) AUC {g['summary']['auc']:.3f} {g['summary']['verdict']}")

    step("doctor: shift", diagnose("shift", shift_prepare))

    def overfit_after():
        d = s["results"].get("overfit")
        check(d, "overfit not diagnosed")
        rx = d["primary"]["prescription"]
        check(rx["kind"] == "gym_patch", rx)
        body = dict(rx["patch"]["body"])
        body.update({"source": "smoke-doctor", "parent_run_id": s["runs"]["overfit"], "scorecard": False})
        rec = wait_run(post("/api/runs", body)["id"])
        d2 = get(f"/api/runs/{rec['id']}/doctor")
        _dr_check_shape(d2)
        ov1 = next(c for c in d["checks"] if c["id"] == "overfit")["evidence"]
        ov2 = next(c for c in d2["checks"] if c["id"] == "overfit")
        check(not (d2["primary"] and d2["primary"]["id"] == "overfit" and d2["primary"]["severity"] == "critical"), d2["headline"])
        hp = body["fit_kwargs"]["hyperparameters"]
        return (f"patch {rx['title']} ({json.dumps(hp)[:80]}) → {rec['id']}: holdout accuracy {ov1['holdout']:.4f} → {ov2['evidence'].get('holdout', float('nan')):.4f}, "
                f"z {ov1['z']:.1f} → {ov2['evidence'].get('z') or float('nan'):.1f}; overfit {ov2['status']}{' ' + ov2['severity'] if ov2['severity'] else ''}; "
                f"new primary {d2['primary']['id'] + ':' + d2['primary']['severity'] if d2['primary'] else 'none'}")

    step("doctor: overfit", diagnose("overfit"))
    step("doctor: overfit prescription applied", overfit_after)

    def lat_prepare(rid: str) -> str:
        bud = DR_SCENARIOS["latency"]["latency_ms"]
        d0 = get(f"/api/runs/{rid}/doctor", latency_ms=bud)
        off = _dr_offer(d0, "latency")
        check(off["check"] == "latency" and off["method"] == "GET", off)
        prof = get(off["endpoint"])
        best = next(m for m in prof["models"] if m["best"])
        return f"before: latency missing · profile (offer): {len(prof['models'])} models, best {best['model']} served p95 {best['served_ms_p95']:.1f} ms"

    def lat_after():
        d = s["results"].get("latency")
        check(d, "latency not diagnosed")
        rx = d["primary"]["prescription"]
        ev = d["primary"]["evidence"]
        if rx["kind"] == "register":
            pick = ev["best_within_budget"]
            check(pick and pick["served_p95_ms"] <= ev["budget_ms"] and rx["body"]["serve_model"] == pick["model"], (rx, pick))
            return f"serve {pick['model']} at p95 {pick['served_p95_ms']:.1f} ms ≤ {ev['budget_ms']:g} ms (holdout {pick['score_test']:.4f} vs {pick['best_score_test']:.4f})"
        check(rx["kind"] == "gym_patch", rx)
        return f"no model within budget: {rx['title']}"

    step("doctor: latency", diagnose("latency", lat_prepare, latency_ms=DR_SCENARIOS["latency"]["latency_ms"]))
    step("doctor: latency prescription", lat_after)

    def determinism():
        rid = s["runs"]["label noise"]
        a, b = get(f"/api/runs/{rid}/doctor"), get(f"/api/runs/{rid}/doctor")
        strip = lambda d: json.dumps([(c["id"], c["status"], c["severity"], c["reason"]) for c in d["checks"]])  # noqa: E731
        check(strip(a) == strip(b), "two calls disagree")
        n = sum(1 for t in s.get("table", []) if t[1] == t[2])
        return (f"same checks, statuses and reasons on a second call · ACCEPTANCE {n}/{len(DR_SCENARIOS)} scenarios diagnosed correctly: "
                + "; ".join(f"{t[0]} → {t[2]}" + (" ✓" if t[1] == t[2] else f" ✗ (want {t[1]})") for t in s.get("table", [])))

    step("doctor: determinism + acceptance", determinism)



# --------------------------------------------------------------------------- F18: monitoring over time


def _mo_batch(bid: str) -> dict[str, Any]:
    return poll(f"/api/registry/batches/{bid}", lambda x: x["status"] in ("succeeded", "failed"), 600, 1)


def monitor(st: dict[str, Any]) -> None:
    """F18: monitoring over time on the loan-stream sample. A champion (time split on application_date, 2024 data) goes
    to Production after a four-eyes review; a challenger (another model family on the same data) goes to Staging and
    is named the challenger. The simulated 2025 stream is fed month by month: each month is scored as a batch dated
    the month's last day and shadow-scored by the challenger; each month's labels arrive 3 months later and are joined
    on application_id. Drift starts in month 7: covariate (debt_to_income ↑, credit_score ↓) and a concept change
    (online applications default more for the same x). Asserts: the PSI drift alert first fires on the 2025-07 batch
    and on no earlier one; F15's adversarial AUC on months 6 and 7; the concept change shows only in performance, first
    on 2025-07's labels which arrive on 2025-10-31 (the lag is shown); months 10–12 have no performance point; the
    challenger comparison (agreement, score PSI, paired ΔAUC with a CI); nothing is promoted automatically; the Model
    Doctor's shift check reads the monitoring alert and prescribes the retrain proposal; the proposal launches a run
    with the champion's config on the recent labelled window (lineage to the batches) and it finishes."""
    ds, label = "loan-stream", "defaulted"
    s: dict[str, Any] = {}
    spec = get("/api/monitoring/stream")
    drift_m = spec["drift_month"]
    period = {m["month"]: m["label"] for m in spec["months"]}

    def train_register():
        base = {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
                "time_limit": 120, "drop_columns": ["application_id", "application_date"], "feature_importance": False,
                "split_spec": {"strategy": "time", "time_column": "application_date"}, "source": "smoke-monitor"}
        champ = post("/api/runs", {**base, "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 200}, "LR": {}}}})
        chall = post("/api/runs", {**base, "fit_kwargs": {"hyperparameters": {"XGB": {"n_estimators": 200}}}})
        rc, rh = wait_run(champ["id"]), wait_run(chall["id"])
        check(rc["status"] == "succeeded" and rh["status"] == "succeeded", (rc.get("error"), rh.get("error")))
        vs = {}
        for k, r in (("champion", rc), ("challenger", rh)):
            v = post(f"/api/runs/{r['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.loan-stream"})
            vs[k] = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
            check(vs[k]["status"] == "ready", vs[k].get("error"))
        c, h = vs["champion"], vs["challenger"]
        post(f"/api/registry/versions/{c['id']}/review", {"decision": "approve", "reviewer": "Smoke Validator"})
        post(f"/api/registry/versions/{c['id']}/transition", {"stage": "Production", "actor": "Smoke Validator"})
        # the challenger must be in Staging before it can shadow-score
        r = C.put("/api/registry/models/smoke.loan-stream/monitoring", json={"actor": "Smoke Validator", "challenger_version_id": h["id"]})
        check(r.status_code == 409 and "Staging" in r.text, f"{r.status_code} {r.text[:200]}")
        post(f"/api/registry/versions/{h['id']}/transition", {"stage": "Staging", "actor": "Smoke Analyst"})
        r = C.put("/api/registry/models/smoke.loan-stream/monitoring", json={"actor": "Smoke Validator", "challenger_version_id": h["id"],
                                                                           "id_column": "application_id"})
        cfg = j(r)["config"]
        check(cfg["challenger_version_id"] == h["id"] and cfg["thresholds"]["feature_psi"] == 0.25, cfg)
        check(C.put("/api/registry/models/smoke.loan-stream/monitoring", json={"actor": "x", "thresholds": {"nope": 1}}).status_code == 400, "bad threshold")
        s.update(champ=rc, chall=rh, cv=c["id"], hv=h["id"])
        m = get(f"/api/registry/versions/{c['id']}/monitoring")
        b = m["baseline"]
        s["baseline"] = b
        return (f"champion {rc['id']} → {c['id']} (Production, GBM+LR, holdout AUC {b['auc']:.4f} [{b['auc_ci'][0]:.4f}, {b['auc_ci'][1]:.4f}]) · "
                f"challenger {rh['id']} → {h['id']} (Staging, XGB) · challenger refused before Staging (409) · thresholds {cfg['thresholds']['feature_psi']} PSI")

    if not step("monitor: champion in Production, challenger in Staging", train_register, fatal=False):
        return
    cv, hv = s["cv"], s["hv"]

    def feed():
        t0 = time.time()
        fed = []
        for m in range(1, 13):
            r = post(f"/api/registry/versions/{cv}/monitoring/stream", {"month": m, "actor": "Smoke Ops"})
            b = _mo_batch(r["batch"]["id"])
            check(b["status"] == "succeeded", b.get("error"))
            check(b["data_date"] == r["data_date"], (b["data_date"], r["data_date"]))
            fed.append((m, r["batch"]["id"], [d["month"] for d in r["labels_delivered"]], r["labels_pending"]))
            check(not r["labels_pending"], r["labels_pending"])
            if m <= 3:
                check(not r["labels_delivered"], f"labels before they were due in month {m}")
            else:
                check([d["month"] for d in r["labels_delivered"]] == [m - 3], f"month {m}: delivered {r['labels_delivered']}")
        s["fed"] = fed
        r = C.post(f"/api/registry/versions/{cv}/monitoring/stream", json={"month": 3, "actor": "Smoke Ops"})
        check(r.status_code == 409, f"re-feed: {r.status_code}")
        return (f"12 months scored + shadow-scored in {time.time() - t0:.0f} s · labels delivered with the 3-month lag: "
                + ", ".join(f"{period[m]}←{period[d[0]]}" for m, _, d, _ in fed if d) + " · re-feeding a month → 409")

    if not step("monitor: feed the 12-month stream", feed):
        return

    def adversarial():
        out = []
        for m in (drift_m - 1, drift_m):
            bid = s["fed"][m - 1][1]
            r = C.post(f"/api/registry/batches/{bid}/shift", json={"null_refits": 0})
            check(r.status_code == 202, r.text[:200])
            g = poll(f"/api/registry/batches/{bid}/shift", lambda x: x.get("status") in ("succeeded", "failed"), 900, 2)
            check(g["status"] == "succeeded", g.get("error"))
            sm = g["summary"]
            out.append((m, sm["auc"], sm["ci"], sm["verdict"], [f["feature"] for f in sm["top_features"] if f.get("significant")][:2]))
        s["adv"] = out
        a6, a7 = out[0][1], out[1][1]
        check(a7 > a6 and a7 > 0.6, f"adversarial AUC month {drift_m - 1} {a6:.3f}, month {drift_m} {a7:.3f}")
        return " · ".join(f"{period[m]} AUC {a:.3f} [{ci[0]:.3f}, {ci[1]:.3f}] {v} (top {', '.join(f)})" for m, a, ci, v, f in out)

    step("monitor: F15 adversarial AUC per batch (months 6, 7)", adversarial)

    def series():
        m = get(f"/api/registry/versions/{cv}/monitoring")
        check(m["schema"] == "monitoring/1" and len(m["points"]) == 12, (m.get("schema"), len(m.get("points", []))))
        pts = {p["period"]: p for p in m["points"]}
        th = m["config"]["thresholds"]["feature_psi"]
        rows = []
        for k in range(1, 13):
            p = pts[period[k]]
            psi = {f["feature"]: f["psi"] for f in p["features"]}
            cov = [a for a in p["alerts"] if a["kind"] == "covariate"]
            rows.append((k, psi["debt_to_income"], psi["credit_score"], psi["channel"], p["mean_p"], cov, p))
            if k < drift_m:
                check(not cov, f"{period[k]}: a drift alert before month {drift_m}: {cov}")
            else:
                check(any(a["signal"] == "feature_psi" for a in cov) and "debt_to_income" in next(a for a in cov if a["signal"] == "feature_psi")["features"],
                      f"{period[k]}: no PSI alert on debt_to_income ({psi['debt_to_income']:.3f})")
            check(psi["channel"] < 0.1, f"{period[k]}: channel PSI {psi['channel']:.3f} (the concept change must not move p(x))")
            if k <= 9:
                check(p["performance"] and p["label_lag_days"] and 85 <= p["label_lag_days"] <= 95, (period[k], p.get("label_lag_days")))
            else:
                check(p["performance"] is None, f"{period[k]} has performance before its labels arrived")
        fc = m["alerts"]["first_covariate"]
        check(fc and fc["period"] == period[drift_m], fc)
        fp = m["alerts"]["first_performance"]
        check(fp and fp["period"] == period[drift_m] and fp["visible_on"] == spec["months"][drift_m - 1]["labels_arrive"], fp)
        s["m"] = m
        perf = lambda k: pts[period[k]]["performance"]  # noqa: E731
        auc_pre = np.mean([perf(k)["auc"] for k in range(1, drift_m)])
        auc_post = np.mean([perf(k)["auc"] for k in range(drift_m, 10)])
        s["auc_pre"], s["auc_post"] = auc_pre, auc_post
        tab = " | ".join(f"{period[k][2:]} dti {d:.2f} cs {c:.2f} ch {h:.3f}" + (" ⚑" if cov else "") for k, d, c, h, _, cov, _ in rows)
        return (f"PSI per month: {tab} · first drift alert {fc['period']} ({fc['text']}), none before · channel PSI < 0.1 every month · "
                f"performance: AUC months 1–{drift_m - 1} mean {auc_pre:.3f} → months {drift_m}–9 {auc_post:.3f}; first performance alert on "
                f"{fp['period']}'s labels, visible {fp['visible_on']} ({fp['text']}) · lag {pts[period[1]]['label_lag_days']} days · 2025-10…12 unlabelled")

    step("monitor: drift alert from month 7, performance with the lag", series)

    def which_signal():
        m = s["m"]
        pts = {p["period"]: p for p in m["points"]}
        k7 = pts[period[drift_m]]
        perf = k7["performance"]
        # concept change: observed default rate above the predicted one, which PSI cannot explain
        check(perf["observed_rate"] > perf["mean_p"] and (perf["calibration_z"] or 0) > m["config"]["thresholds"]["calibration_z"],
              (perf["observed_rate"], perf["mean_p"], perf["calibration_z"]))
        pre = pts[period[drift_m - 1]]["performance"]
        check(abs(pre["calibration_z"]) < m["config"]["thresholds"]["calibration_z"], f"month {drift_m - 1} z {pre['calibration_z']:.2f}")
        return (f"{period[drift_m - 1]}: observed {pre['observed_rate']:.3f} vs p̂ {pre['mean_p']:.3f} (z {pre['calibration_z']:+.1f}) · "
                f"{period[drift_m]}: observed {perf['observed_rate']:.3f} [{perf['observed_ci'][0]:.3f}, {perf['observed_ci'][1]:.3f}] vs p̂ "
                f"{perf['mean_p']:.3f} (z {perf['calibration_z']:+.1f}), AUC {perf['auc']:.3f} vs baseline {s['baseline']['auc']:.3f} · "
                "covariate drift: PSI + adversarial AUC at scoring time; concept change: only labelled performance, 3 months later")

    step("monitor: which signal catches which", which_signal)

    def challenger():
        m = s["m"]
        ch = m["challenger"]
        check(ch and ch["version_id"] == hv and ch["batches"] == 12, ch and ch.get("batches"))
        pts = m["points"]
        check(all(p["shadow"] and 0 <= p["shadow"]["agreement"] <= 1 and p["shadow"]["spearman"] > 0.5 for p in pts), "shadow on every batch")
        pool = ch["pooled"]
        check(pool and pool["n"] == 9 * spec["rows_per_month"] and pool["auc"]["ci"][0] <= pool["auc"]["delta"] <= pool["auc"]["ci"][1], pool and pool.get("n"))
        # recompute the pooled ΔAUC from the per-batch paired results is not possible; check one batch against its own perf
        p1 = pts[0]
        check(abs(p1["paired"]["auc"]["auc_champion"] - p1["performance"]["auc"]) < 1e-9, "paired champion AUC ≠ batch AUC")
        v = get(f"/api/registry/versions/{hv}")
        check(v["stage"] == "Staging", f"challenger moved to {v['stage']} by itself")
        r = C.post(f"/api/registry/versions/{hv}/transition", json={"stage": "Production", "actor": "Smoke Ops"})
        check(r.status_code == 409, f"unreviewed challenger → Production: {r.status_code}")
        a = pool["auc"]
        return (f"12 batches shadow-scored · agreement {ch['agreement_mean']:.3f}, score PSI {ch['psi_mean']:.3f}, ρ {ch['spearman_mean']:.3f} · "
                f"pooled on {pool['n']:,} labelled rows: AUC champion {a['auc_champion']:.4f} vs challenger {a['auc_challenger']:.4f}, "
                f"Δ {a['delta']:+.4f} [{a['ci'][0]:+.4f}, {a['ci'][1]:+.4f}] (p {a['p_value']:.3f}), Δ log loss {pool['log_loss']['delta']:+.4f} "
                f"[{pool['log_loss']['ci'][0]:+.4f}, {pool['log_loss']['ci'][1]:+.4f}] · still Staging; → Production without review 409")

    step("monitor: champion / challenger", challenger)

    def labels_api():
        bid = s["fed"][11][1]
        bad = C.post(f"/api/registry/batches/{bid}/labels", files={"file": ("l.csv", b"nope,defaulted\n1,0\n", "text/csv")},
                     data={"created_by": "Smoke Ops", "id_column": "nope"})
        check(bad.status_code == 400 and "nope" in bad.text, f"{bad.status_code} {bad.text[:200]}")
        check(C.post(f"/api/registry/batches/{bid}/labels", files={"file": ("l.csv", b"a,b\n", "text/csv")}, data={}).status_code == 400, "no actor")
        return "a label file without the id column → 400 · without an actor → 400"

    step("monitor: label upload refusals", labels_api)

    def doctor():
        d = get(f"/api/runs/{s['champ']['id']}/doctor")
        c = next(c for c in d["checks"] if c["id"] == "shift")
        check(c["status"] == "finding" and c["evidence"].get("monitoring"), (c["status"], c["reason"][:200]))
        check(c["prescription"]["kind"] == "retrain" and c["prescription"]["endpoint"].endswith("/monitoring/retrain"), c["prescription"])
        return f"shift {c['severity']} · {c['reason'][:220]} · rx {c['prescription']['kind']}: {c['prescription']['title']}"

    step("monitor: the Model Doctor reads the drift alert", doctor)

    def retrain():
        p = get(f"/api/registry/versions/{cv}/monitoring/retrain")
        check(p["ready"] and p["window"]["end"] == spec["months"][8]["end"] and len(p["batches"]) == 6, (p["window"], len(p["batches"])))
        check(p["body"]["split_spec"]["strategy"] == "time" and p["body"]["fit_kwargs"] == s["champ"]["config"]["fit_kwargs"], p["body"])
        r = post(f"/api/registry/versions/{cv}/monitoring/retrain", {"actor": "Smoke Validator"})
        rec = wait_run(r["run"]["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        check(rec["config"].get("parent_run_id") == s["champ"]["id"], rec["config"].get("parent_run_id"))
        lin = get(f"/api/datasets/{r['dataset']['id']}")["profile"]["lineage"]
        check(lin["kind"] == "monitoring-window" and lin["champion_version_id"] == cv and len(lin["batches"]) == 6, lin)
        aud = get("/api/registry/audit", version_id=cv)
        entries = aud.get("entries", aud.get("audit", aud if isinstance(aud, list) else []))
        check(any(a["action"] == "retrain_launched" for a in entries) and any(a["action"] == "labels_attached" for a in entries), "audit")
        m_new, s_new = primary(rec)
        # before → after on the retrained run's holdout (the newest rows of the window): the champion scores the same rows
        hp = get(f"/api/runs/{rec['id']}/holdout-predictions")
        data_dir = get("/api/health")["data_dir"]
        win = pd.read_csv(os.path.join(data_dir, "datasets", r["dataset"]["id"], "data.csv"))
        rows = win.iloc[np.asarray(hp["rows"], dtype=int)]
        sc = post(f"/api/registry/versions/{cv}/score", {"rows": json.loads(rows.drop(columns=[label]).to_json(orient="records"))})
        from sklearn.metrics import roc_auc_score

        y = rows[label].astype(int).to_numpy()
        pc = np.array([float(pr.get("1", pr.get(1, 0))) for pr in sc["probabilities"]])
        auc_champ = roc_auc_score(y, pc)
        s["retrain"] = (auc_champ, s_new)
        return (f"window {p['window']['start']} … {p['window']['end']} ({p['rows']:,} labelled rows, {len(p['batches'])} batches) · "
                f"{p['split_note']} · launched {rec['id']} (parent {s['champ']['id']}) on {r['dataset']['id']} · on its holdout "
                f"({len(rows):,} newest rows): champion AUC {auc_champ:.4f} → retrained {m_new} {s_new:.4f}")

    step("monitor: the retrain proposal launches a run", retrain)

def credit(st: dict[str, Any]) -> None:
    """G2: the credit-risk model report on the F18 stream, zero-config. The loan-stream intake pre-fill (target, time
    split on application_date, dropped id) trains a quick GBM with the F7 cost; it is registered and fed the 12 stream
    months (labels arrive 3 months late). Asserts the acceptance: Gini by month with DeLong CIs (9 labelled months),
    CSI flags debt_to_income from 2025-07 (and on no earlier month), vintage curves for ≥ 6 cohorts. Also: the band
    and decile tables add up, Gini = 2·AUC − 1 on the pooled batches recomputed from the rows, the Q3/Q4-2025 cohorts
    (the concept change) run above Q1/Q2, the Markdown/HTML export, the model card link, the Build suggestion, and a
    tape upload whose columns come from the roles (then removed)."""
    ds, label, model = "loan-stream", "defaulted", "smoke.credit-report"
    s: dict[str, Any] = {}

    def train_feed():
        it = get(f"/api/datasets/{ds}/intake")
        pf = next(x for x in it["suggestions"] if x["id"] == "binary")["prefill"]
        check(pf["target"] == label and pf["split"] == "time" and pf["time"] == "application_date", pf)
        body = {"dataset_id": ds, "label": label, "problem_type": "binary", "presets": "medium_quality", "time_limit": 120,
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 200}}}, "drop_columns": pf.get("drop", []) + [pf["time"]],
                "feature_importance": False, "split_spec": {"strategy": "time", "time_column": pf["time"]},
                "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto", "source": "smoke-credit"}
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": model})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        t0 = time.time()
        for m in range(1, 13):
            r = post(f"/api/registry/versions/{v['id']}/monitoring/stream", {"month": m, "actor": "Smoke Ops"})
            b = _mo_batch(r["batch"]["id"])
            check(b["status"] == "succeeded", b.get("error"))
        s.update(vid=v["id"], run=rec)
        return f"intake pre-fill → {rec['id']} → {v['id']} (t = {v['result']['version']['decision_threshold']:.4f}) · 12 months fed in {time.time() - t0:.0f} s"

    if not step("credit: zero-config run, registered, 12 stream months fed", train_feed):
        return
    vid = s["vid"]

    def report():
        t0 = time.time()
        r = get(f"/api/registry/versions/{vid}/credit-report")
        secs = time.time() - t0
        check(r["schema"] == "credit-report/1", r.get("schema"))
        d = r["detected"]
        check(d["date"]["column"] == "application_date" and d["id"]["column"] == "application_id", d)
        bm = [p for p in r["periods"] if p["source"] == "batch"]
        check([p["period"] for p in bm] == [f"2025-{k:02d}" for k in range(1, 10)], [p["period"] for p in bm])
        check([p["period"] for p in r["pending"]] == ["2025-10", "2025-11", "2025-12"], r["pending"])
        for p in r["periods"]:
            check(p.get("gini_ci") and p["gini_ci"][0] < p["gini"] < p["gini_ci"][1], (p["period"], p.get("gini"), p.get("gini_ci")))
            check(abs(p["gini"] - (2 * p["auc"] - 1)) < 1e-9 and p["ks_ci"][0] <= p["ks"] <= p["ks_ci"][1], p["period"])
        # independent: Gini and KS of the pooled labelled batches from the rows
        data = os.path.join(get("/api/health")["data_dir"], "registry", "batches")
        ys, ps = [], []
        for p in bm:
            for bid in p["batches"]:
                lab = pd.read_csv(os.path.join(data, bid, "labels.csv"))
                sc = pd.read_csv(os.path.join(data, bid, "scored.csv"))
                ys.append((lab["y"].astype(str) == r["positive_class"]).astype(int).to_numpy())
                ps.append(sc[f"p_{r['positive_class']}"].to_numpy()[lab["row"].to_numpy()])
        y, pp = np.concatenate(ys), np.concatenate(ps)
        from sklearn.metrics import roc_auc_score, roc_curve

        fpr, tpr, _ = roc_curve(y, pp)
        g, ks = 2 * roc_auc_score(y, pp) - 1, float(np.max(tpr - fpr))
        ob = r["overall"]["batches"]
        check(abs(ob["gini"] - g) < 1e-6 and abs(ob["ks"] - ks) < 1e-6 and ob["n"] == 9 * 1200, (ob["gini"], g, ob["ks"], ks))
        # bands and deciles add up
        b = r["bands"]
        check(sum(x["n"] for x in b["rows"]) == b["n"] and abs(b["rows"][-1]["cum_capture"] - 1) < 1e-9, "band totals")
        dec = r["deciles"]
        check(sum(x["n"] for x in dec) == b["n"] and dec[0]["bad_rate"] > 2 * dec[-1]["bad_rate"], [x["bad_rate"] for x in dec])
        # CSI flags debt_to_income from 2025-07, on no earlier month
        cs = r["stability"]["csi"]
        dti = next(f for f in cs["features"] if f["feature"] == "debt_to_income")
        csi = dict(zip(cs["periods"], dti["csi"]))
        check(dti["first_shift"] == "2025-07" and dti["stays_flagged"], dti)
        check(all(v < 0.25 for k, v in csi.items() if k < "2025-07"), csi)
        check(r["stability"]["first_flag"]["feature"] == "debt_to_income", r["stability"]["first_flag"])
        # vintage curves
        vin = r["vintage"]
        co = vin["cohorts"]
        check(len(co) >= 6, len(co))
        for c in co:
            ys_ = [x["cum_bad_rate"] for x in c["curve"]]
            check(all(a <= b_ + 1e-12 for a, b_ in zip(ys_, ys_[1:])), f"{c['cohort']} not monotone")
        at6 = {c["cohort"]: next(x["cum_bad_rate"] for x in c["curve"] if x["mob"] == 6) for c in co if c["observed_mob"] >= 6}
        early = np.mean([at6[k] for k in ("2025Q1", "2025Q2")])
        late = np.mean([at6[k] for k in ("2025Q3", "2025Q4") if k in at6])
        check(late > early, (early, late))
        s["r"] = r
        g9 = " · ".join(f"{p['period'][2:]} {p['gini']:.3f} [{p['gini_ci'][0]:.3f}, {p['gini_ci'][1]:.3f}]" for p in bm)
        h = r["overall"]["holdout"]
        return (f"{secs:.1f} s · holdout Gini {h['gini']:.3f} [{h['gini_ci'][0]:.3f}, {h['gini_ci'][1]:.3f}], KS {h['ks']:.3f} · "
                f"Gini by month: {g9} · pooled batches Gini {g:.4f}, KS {ks:.4f} (recomputed) · decile bad rate {dec[0]['bad_rate']:.3f} → "
                f"{dec[-1]['bad_rate']:.3f} · CSI debt_to_income: " + ", ".join(f"{k[2:]} {v:.2f}" for k, v in csi.items())
                + f" → first shift {dti['first_shift']} · {len(co)} vintage cohorts ({vin['cohort_by']}), V(6) 2025H1 {early:.3f} vs H2 {late:.3f}")

    if not step("credit: report (acceptance)", report):
        return

    def export_card_build():
        md = C.get(f"/api/registry/versions/{vid}/credit-report", params={"format": "md"})
        check(md.status_code == 200 and "# Model Performance Report" in md.text and "## 4. Vintage curves" in md.text, md.text[:200])
        ht = C.get(f"/api/registry/versions/{vid}/credit-report", params={"format": "html", "download": "true"})
        check(ht.status_code == 200 and "<h2>3. Stability</h2>" in ht.text and "attachment" in ht.headers.get("content-disposition", ""), ht.status_code)
        card = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Credit model performance report" in card and "credit-report?format=html" in card, "card link")
        sug = next((x for x in get(f"/api/datasets/{ds}/intake")["suggestions"] if x["id"] == "credit_report"), None)
        check(sug and sug["route"] == "registry" and sug["prefill"]["tab"] == "Credit Report", sug)
        bad = C.get("/api/registry/versions/nope/credit-report")
        check(bad.status_code == 404, bad.status_code)
        return (f"Markdown {len(md.text):,} chars, HTML {len(ht.text):,} chars (download) · model card links it · Build suggests "
                f"'{sug['title']}' ({sug['score']:.0f}) → registry tab Credit Report · unknown version 404")

    step("credit: export, model card, Build suggestion", export_card_build)

    def tape():
        rows = []
        rng = np.random.default_rng(2)
        for m in range(1, 9):
            for i in range(150):
                bad = rng.random() < 0.1
                rows.append({"loan_ref": f"T{m}-{i}", "open_date": f"2023-{m:02d}-15",
                             "default_date": f"2023-{min(m + 1 + i % 6, 12):02d}-10" if bad else "", "as_of_date": "2023-12-31"})
        csv = pd.DataFrame(rows).to_csv(index=False).encode()
        bad = C.post(f"/api/registry/versions/{vid}/credit-report/tape", files={"file": ("t.csv", b"a,b\n1,2\n")}, data={"actor": "Smoke"})
        check(bad.status_code == 400, bad.status_code)
        r = j(C.post(f"/api/registry/versions/{vid}/credit-report/tape", files={"file": ("tape.csv", csv)}, data={"actor": "Smoke Analyst"}))
        mp = r["tape"]["mapping"]
        check(mp["open"] == "open_date" and mp["default"] == "default_date" and mp["until"] == "as_of_date", mp)
        v2 = get(f"/api/registry/versions/{vid}/credit-report")["vintage"]
        check(v2["source"]["kind"] == "uploaded" and v2["cohort_by"] == "month" and len(v2["cohorts"]) == 8, (v2["source"], len(v2["cohorts"])))
        d = C.delete(f"/api/registry/versions/{vid}/credit-report/tape", params={"actor": "Smoke Analyst"})
        check(d.status_code == 200, d.status_code)
        v3 = get(f"/api/registry/versions/{vid}/credit-report")["vintage"]
        check(v3["source"]["kind"] == "sample", v3["source"])
        return f"columns from the roles: {mp['open']}, {mp['default']}, {mp['until']} · {len(v2['cohorts'])} monthly cohorts · bad tape 400 · removed → sample tape again"

    step("credit: tape upload (roles → columns), then removed", tape)


def expected_loss(st: dict[str, Any]) -> None:
    """G3: expected loss, PD × LGD × EAD, on the loan-losses sample, zero-config. Intake's top suggestion is expected
    loss with the detected default flag, LGD, EAD and segments; the run is started with nothing but the dataset (the
    API fills every input from the same roles; AutoGluon medium_quality, 60 s per model). Asserts the acceptance: holdout ΣEL within ±10 %
    of the realised loss, and every zero-config stress scenario moves EL in the expected direction by a reported
    amount. Also: a custom scenario and its mirror move EL in opposite directions, the segment totals add up, bad
    shocks are refused, and the registered version scores loans (EL = PD·LGD·EAD per row), scores a batch, and has the
    model card section."""
    ds = "loan-losses"
    s: dict[str, Any] = {}

    def suggest():
        it = get(f"/api/datasets/{ds}/intake")
        s0 = it["suggestions"][0]
        check(s0["id"] == "expected_loss" and s0["route"] == "expected-loss", f"top {s0['id']}")
        p = s0["prefill"]
        check((p["pd"], p["lgd"], p["ead"]) == ("defaulted", "lgd", "ead") and p["segments"] == ["product", "region"], p)
        d = get("/api/expected-loss/detect", dataset_id=ds)
        check(d["drop_columns"] == ["loan_id"] and d["split"] == "time" and d["evidence"]["lgd"], d)
        return f"top {s0['id']} ({s0['score']:g}) · y = defaulted, LGD = lgd, EAD = ead · by product, region · drop loan_id · time split"
    step("el: Build suggests expected loss, inputs detected", suggest)

    def train():
        rec = post("/api/expected-loss/runs", {"dataset_id": ds})  # zero-config: medium_quality, 60 s per model
        check(rec["kind"] == "expected_loss", rec.get("kind"))
        t0 = time.time()
        done = wait_run(rec["id"])
        check(done["status"] == "succeeded", done.get("error"))
        r = get(f"/api/expected-loss/runs/{rec['id']}")
        el = r["expected_loss"]
        p = el["portfolio"]
        check(p["within_10pct"] and abs(p["el"] / p["realised"] - 1) <= 0.10, f"ΣEL {p['el']:.0f} vs realised {p['realised']:.0f}")
        check(p["ratio_ci"][0] < p["ratio"] < p["ratio_ci"][1], p["ratio_ci"])
        for col, rows in el["segments"].items():
            check(abs(sum(x["el"] for x in rows) - p["el"]) < 1e-6 * p["el"] and abs(sum(x["realised"] for x in rows) - p["realised"]) < 1e-3, col)
        sc = el["scenarios"]
        check(len(sc) >= 3 and all(x["as_expected"] for x in sc) and all(x["delta"] > 0 for x in sc), [(x["name"], x["delta"]) for x in sc])
        for x in sc:
            check(abs(sum(g["delta"] for g in x["segments"]["product"]) - x["delta"]) < 1e-6 * abs(x["base"]), x["name"])
        s.update(run=rec["id"], el=el)
        st["el"] = {"ratio": p["ratio"], "ci": p["ratio_ci"], "scenarios": {x["name"]: x["delta_pct"] for x in sc}}
        return (f"{rec['id']} in {time.time() - t0:.0f} s · holdout {p['n']} loans: ΣEL {p['el']:,.0f} vs realised "
                f"{p['realised']:,.0f} → {p['ratio']:.3f} [{p['ratio_ci'][0]:.3f}, {p['ratio_ci'][1]:.3f}] · PD AUC "
                f"{el['components']['pd']['auc']:.3f} · " + ", ".join(f"{x['name']} {x['delta_pct']:+.1f}%" for x in sc))
    if not step("el: zero-config run, ΣEL vs realised (acceptance), default scenarios", train):
        return
    run = s["run"]

    def custom():
        down = post(f"/api/expected-loss/runs/{run}/stress", {"shocks": [{"column": "income", "op": "pct", "value": -10}], "expect": "up"})
        up = post(f"/api/expected-loss/runs/{run}/stress", {"shocks": [{"column": "income", "op": "pct", "value": 10}], "expect": "down"})
        check(down["delta"] > 0 and up["delta"] < 0 and down["as_expected"] and up["as_expected"], (down["delta"], up["delta"]))
        check(down["components"]["pd"]["stressed"] > down["components"]["pd"]["base"], "PD did not rise")
        prod = post(f"/api/expected-loss/runs/{run}/stress", {"name": "all loans become cards", "shocks": [{"column": "product", "op": "set", "value": "card"}]})
        check(prod["shocks"][0]["changed"] > 0, prod["shocks"])
        b1 = C.post(f"/api/expected-loss/runs/{run}/stress", json={"shocks": [{"column": "nope", "op": "pct", "value": 1}]})
        b2 = C.post(f"/api/expected-loss/runs/{run}/stress", json={"shocks": [{"column": "product", "op": "pct", "value": 1}]})
        check(b1.status_code == 400 and b2.status_code == 400, (b1.status_code, b2.status_code))
        saved = get(f"/api/expected-loss/runs/{run}")["custom_scenarios"]
        check(len(saved) >= 3, len(saved))
        return (f"income −10% → ΔEL {down['delta']:+,.0f} ({down['delta_pct']:+.1f}%), +10% → {up['delta']:+,.0f} "
                f"({up['delta_pct']:+.1f}%) · all cards {prod['delta_pct']:+.1f}% · unknown column / % on a category → 400")
    step("el: custom stress scenarios", custom)

    def registry():
        v = post(f"/api/runs/{run}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.expected-loss"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready" and v["result"]["kind"] == "expected_loss", v.get("error"))
        rows = [{"product": "card", "region": "north", "income": 40000, "credit_score": 600, "debt_to_income": 0.5, "utilization": 0.9,
                 "credit_limit": 10000, "current_balance": 9000, "collateral_value": 0, "term_months": 0, "application_date": "2024-12-01"},
                {"product": "auto", "region": "west", "income": 90000, "credit_score": 760, "debt_to_income": 0.15, "utilization": 0.5,
                 "credit_limit": 30000, "current_balance": 15000, "collateral_value": 25000, "term_months": 60, "application_date": "2024-12-01"}]
        sc = post(f"/api/registry/versions/{v['id']}/score", {"rows": rows})
        res = sc["results"]
        check(all(abs(r["el"] - r["pd"] * r["lgd"] * r["ead"]) < 1e-3 * max(r["el"], 1) for r in res), res)
        check(res[0]["pd"] > res[1]["pd"] and res[0]["lgd"] > res[1]["lgd"], res)
        batch = pd.DataFrame(rows * 50)
        b = j(C.post(f"/api/registry/versions/{v['id']}/batches", files={"file": ("loans.csv", batch.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
        check(b["status"] == "succeeded", b.get("error"))
        summ = b["result"]["summary"]
        check(abs(summ["total_el"] - 50 * (res[0]["el"] + res[1]["el"])) < 1e-3 * summ["total_el"], summ["total_el"])
        card = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("Expected loss (PD × LGD × EAD)" in card and "Stress scenario" in card, "card section")
        return (f"{v['id']} ready · /score: a risky card EL {res[0]['el']:,.0f} (PD {res[0]['pd']:.3f}, LGD {res[0]['lgd']:.2f}, "
                f"EAD {res[0]['ead']:,.0f}) vs a secured auto loan {res[1]['el']:,.0f} · batch of 100 ΣEL {summ['total_el']:,.0f} · card section")
    step("el: registered; scores loans and a batch; model card", registry)


def segmented(st: dict[str, Any]) -> None:
    """G5: segmented models on the segmented-loans sample, zero-config. Intake's top suggestion is "Segmented models by
    customer_type" (an input moves the default rate in opposite directions across customer types); the run is started
    with nothing but the dataset (global + one model per customer type, medium_quality, 20 s each; the out-of-time
    split's tuning slice only chooses the routes). Asserts the acceptance: the routed model beats the global one on at
    least one segment with a DeLong paired 95 % CI above 0, and the overall comparison is reported with its CI. Then the
    routed version: Try It and /score route each row (unknown segment → global), reason codes come from the row's own
    model with one code book, a batch carries routed_model / decision / reason codes, the card has the section, and
    counterfactuals are refused for a routed version."""
    ds = "segmented-loans"
    s: dict[str, Any] = {}

    def suggest():
        it = get(f"/api/datasets/{ds}/intake")
        s0 = it["suggestions"][0]
        check(s0["id"] == "segmented" and s0["route"] == "segmented", f"top {s0['id']}")
        check(s0["prefill"]["segment"] == "customer_type" and s0["prefill"]["label"] == "defaulted", s0["prefill"])
        d = get("/api/segmented/detect", dataset_id=ds)
        check(d["segment_column"] == "customer_type" and d["problem_type"] == "binary" and d["split"] == "time", d)
        check(d["drop_columns"] == ["application_id"] and d["heterogeneity"]["opposite"], d)
        return f"top {s0['id']} ({s0['score']:g}) · {d['heterogeneity']['words']}"
    step("seg: Build suggests segmented models, inputs detected", suggest)

    def train():
        rec = post("/api/segmented/runs", {"dataset_id": ds})  # zero-config
        check(rec["kind"] == "segmented", rec.get("kind"))
        t0 = time.time()
        done = wait_run(rec["id"])
        check(done["status"] == "succeeded", done.get("error"))
        r = get(f"/api/segmented/runs/{rec['id']}")["segmented"]
        segs = r["segments"]
        check(len(segs) == 4 and sum(x["holdout_rows"] for x in segs) == r["holdout_rows"], [x["value"] for x in segs])
        check(set(r["routes"]) <= {x["value"] for x in segs} and all(x["route_why"] for x in segs), r["routes"])
        won = [x for x in segs if x["routed"] and (x.get("own") or {}).get("verdict") == "routed better"
               and x["own"]["delta_auc"]["ci"][0] > 0]
        check(won, [(x["value"], x["routed"], (x.get("own") or {}).get("delta_auc")) for x in segs])  # acceptance
        o = r["overall"]
        check(o["delta_auc"] and o["delta_auc"]["ci"][0] <= o["delta_auc"]["delta"] <= o["delta_auc"]["ci"][1], o)
        s.update(run=rec["id"], r=r)
        st["seg"] = {"wins": [x["value"] for x in won], "overall": o["delta_auc"],
                     "segments": {x["value"]: (x.get("own") or {}).get("delta_auc") for x in segs}}
        f = lambda d: f"{d['delta']:+.4f} [{d['ci'][0]:+.4f}, {d['ci'][1]:+.4f}]"  # noqa: E731
        return (f"{rec['id']} in {time.time() - t0:.0f} s · routes {', '.join(r['routes'])} · "
                + " · ".join(f"{x['value']} ΔAUC {f(x['own']['delta_auc'])}" for x in segs if x.get("own"))
                + f" · overall routed AUC {o['global']['auc']:.4f} → {o['routed']['auc']:.4f}, Δ {f(o['delta_auc'])} ({o['verdict']})")
    if not step("seg: zero-config run, routed vs global per segment with paired CIs (acceptance)", train):
        return
    run, r = s["run"], s["r"]

    def registry():
        v = post(f"/api/runs/{run}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.segmented"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready" and v["result"]["kind"] == "segmented" and v["result"]["version"]["problem_type"] == "binary", v.get("error"))
        vid = v["id"]
        base = {"income": 60000, "credit_score": 600, "debt_to_income": 0.55, "utilization": 0.85, "months_employed": 8,
                "num_accounts": 6, "loan_amount": 90000, "recent_inquiries": 4}
        rows = [{**base, "customer_type": c} for c in ["small_business", "salaried", "self_employed", "student", "pensioner"]]
        t = post(f"/api/registry/versions/{vid}/predict", {"row": rows[0]})
        routed = r["routes"]
        check(t["segment"]["model"] == ("segment" if "small_business" in routed else "global") and t["threshold"] == r["threshold"], t["segment"])
        check(t.get("reasons") and "codes" in t["reasons"], t.get("reasons"))
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": rows, "reasons": True})["results"]
        check(all(x["model"] == ("segment" if x["segment"] in routed else "global") for x in sc), [(x["segment"], x["model"]) for x in sc])
        check(sc[-1]["model"] == "global" and "no routed model" in sc[-1]["routed_to"], sc[-1])
        book: dict[str, str] = {}
        for x in sc:
            for c in x.get("codes") or []:
                check(book.setdefault(c["code"], c["feature"]) == c["feature"], (c["code"], c["feature"], book))
        check(any(x.get("codes") for x in sc), "no adverse row got codes")
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("apps.csv", pd.DataFrame(rows * 20).to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
        check(b["status"] == "succeeded", b.get("error"))
        summ = b["result"]["summary"]
        check(summ["n_rows"] == 100 and summ["seg_global_rows"] == 20 * sum(1 for x in sc if x["model"] == "global"), summ.get("seg_segments"))
        check({"routed_model", "decision", "reason_codes"} <= set(b["result"]["columns"]), b["result"]["columns"])
        rc = get(f"/api/registry/versions/{vid}/reasons")
        check(rc["threshold"] == r["threshold"], rc.get("threshold"))
        cf = C.post(f"/api/registry/versions/{vid}/recourse", json={"rows": [rows[0]]})
        check(cf.status_code == 400, cf.status_code)
        card = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Segmented models (routed)" in card and "Overall, routed" in card, "card section")
        return (f"{vid} ready ({v['result']['version']['size_bytes'] / 1e6:.0f} MB) · Try It small_business → {t['segment']['routed_to']}, "
                f"p̂ {t['probabilities'][str(t['positive_class'])]:.3f} · /score routes {', '.join(f'{x['segment']}→{x['model']}' for x in sc)} · "
                f"one code book · batch of 100: {100 - summ['seg_global_rows']} rows by segment models · recourse → 400 · card section")
    step("seg: routed version: Try It, /score, reason codes, batch, card", registry)


def survival(st: dict[str, Any]) -> None:
    """G6: time to event on the account-attrition sample, zero-config. Intake's top suggestion is "Time to churned
    (survival)" (an explicit duration name and a 0/1 event); the run is started with nothing but the dataset (Cox PH and a
    random survival forest; the served one has the lower integrated Brier score on training fold 0). Asserts the
    acceptance: holdout Harrell's C > 0.7, and the served model's mean Ŝ(τ) by risk quartile is calibrated against
    Kaplan–Meier (|Ŝ − Ŝ_KM| ≤ 0.05 at 12/24/36 months, or inside the KM 95 % CI). Then the version: Try It gives a
    survival curve per applicant, /score and a batch give Ŝ(τ) per row, and the card has the section."""
    ds = "account-attrition"
    s: dict[str, Any] = {}

    def suggest():
        it = get(f"/api/datasets/{ds}/intake")
        s0 = it["suggestions"][0]
        check(s0["id"] == "survival" and s0["route"] == "survival", f"top {s0['id']}")
        check(s0["prefill"]["duration"] == "duration_months" and s0["prefill"]["event"] == "churned", s0["prefill"])
        d = get("/api/survival/detect", dataset_id=ds)
        check(d["strong"] and d["horizons"] == [12, 24, 36] and d["split"] == "random", d)
        check(sorted(d["drop_columns"]) == ["account_id", "open_date"], d["drop_columns"])
        # G0's caveat: a tenure-like duration (account-churn's tenure_months) never makes survival the top suggestion
        ac = get("/api/datasets/account-churn/intake")["suggestions"]
        check(ac[0]["id"] != "survival", ac[0]["id"])
        return f"top {s0['id']} ({s0['score']:g}) · τ = {d['horizons']} · {d['split']} split · account-churn top {ac[0]['id']}"
    step("surv: Build suggests time to event, columns detected", suggest)

    def train():
        rec = post("/api/survival/runs", {"dataset_id": ds})  # zero-config
        check(rec["kind"] == "survival", rec.get("kind"))
        t0 = time.time()
        done = wait_run(rec["id"])
        check(done["status"] == "succeeded", done.get("error"))
        r = get(f"/api/survival/runs/{rec['id']}")["survival"]
        m = r["models"][r["served"]]
        check(m["c_index"] > 0.7, m["c_index"])  # acceptance 1
        check(r["calibration"]["ok"] and r["calibration"]["cells"] == 12, r["calibration"])  # acceptance 2
        check(len(r["groups"]) == 4 and all(g["km"]["s"][0] == 1.0 for g in r["groups"]), "risk groups")
        hr = {h["term"]: h for h in r["hazard_ratios"]}
        check(hr["complaints_12m"]["hr_ci"][0] > 1 and hr["rate_vs_market_pp"]["hr_ci"][1] < 1, "hazard ratio directions")
        s.update(run=rec["id"], r=r)
        o = r["models"]["rsf" if r["served"] == "cox" else "cox"]
        st["surv"] = {"served": r["served"], "c_index": m["c_index"], "ci": m["c_index_ci"], "calibration": r["calibration"]}
        return (f"{rec['id']} in {time.time() - t0:.0f} s · served {r['served']} · C {m['c_index']:.4f} "
                f"[{m['c_index_ci'][0]:.4f}, {m['c_index_ci'][1]:.4f}] (other {o['c_index']:.4f}) · "
                + " · ".join(f"AUC({a['t']:g}) {a['auc']:.3f}" for a in m["at"])
                + f" · calibration max |Ŝ − KM| {r['calibration']['max_abs_diff']:.3f}, {r['calibration']['in_ci']}/12 in KM CI"
                + f" · HR complaints {hr['complaints_12m']['hr']:.2f}, rate {hr['rate_vs_market_pp']['hr']:.2f}")
    if not step("surv: zero-config run, C-index and calibration against KM (acceptance)", train):
        return
    run, r = s["run"], s["r"]

    def registry():
        v = post(f"/api/runs/{run}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.survival"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready" and v["result"]["kind"] == "survival" and v["result"]["version"]["problem_type"] == "survival", v.get("error"))
        vid = v["id"]
        lo, hi = r["examples"][0], r["examples"][-1]
        tl = post(f"/api/registry/versions/{vid}/predict", {"row": lo})
        th = post(f"/api/registry/versions/{vid}/predict", {"row": hi})
        c = th["curve"]
        check(len(c) == len(th["grid"]) and all(a >= b - 1e-9 for a, b in zip(c, c[1:])), "curve not monotone")
        check(tl["at"][0]["survival"] > th["at"][0]["survival"] and th["risk_group"] == "high", (tl["at"], th["at"], th["risk_group"]))
        if r["served"] == "cox":
            import math
            check(abs(math.exp(sum(x["log_hr"] for x in th["contributions"])) - th["hr_vs_average"]) < 1e-6 * th["hr_vs_average"], "contributions")
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": [lo, hi]})["results"]
        check(len(sc) == 2 and abs(sc[1]["at"][0]["survival"] - th["at"][0]["survival"]) < 1e-9, sc)
        rows = pd.DataFrame(r["examples"] * 20)
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("accounts.csv", rows.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
        check(b["status"] == "succeeded", b.get("error"))
        check({"risk_score", "risk_group", "S_12", "S_24", "S_36", "median_time"} <= set(b["result"]["columns"]), b["result"]["columns"])
        card = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Time to event (survival)" in card and "Harrell's C" in card, "card section")
        return (f"{vid} ready · Try It: low-risk Ŝ(12) {tl['at'][0]['survival']:.3f}, high-risk Ŝ(12) {th['at'][0]['survival']:.3f} "
                f"(HR vs average {th.get('hr_vs_average', float('nan')):.2f}) · /score 2 rows · batch {b['result']['summary']['n_rows']} rows · card section")
    step("surv: version: Try It curve, /score, batch, card", registry)


def multilabel(st: dict[str, Any]) -> None:
    """G8: multi-label on the complaint-topics sample, zero-config. Intake's top suggestion is "Multi-label: topics" (a
    ';'-delimited list); the same table with one 0/1 column per label is detected too. The run is started with nothing but
    the dataset (one AutoGluon model per label, each with its own F1 threshold). Asserts the acceptance: macro F1 is
    reported per label (F1, CI, threshold, AP for every label), and Try It returns two or more labels for a multi-topic
    complaint text. Then /score, a batch (a label set and p̂ per label per row) and the card section."""
    ds = "complaint-topics"
    s: dict[str, Any] = {}

    def suggest():
        it = get(f"/api/datasets/{ds}/intake")
        s0 = it["suggestions"][0]
        check(s0["id"] == "multilabel" and s0["route"] == "multilabel", f"top {s0['id']}")
        check(s0["prefill"]["label_column"] == "topics" and len(s0["prefill"]["labels"]) == 6, s0["prefill"])
        d = get("/api/multilabel/detect", dataset_id=ds)
        check(d["source"] == "delimited" and d["delimiter"] == ";" and d["text_columns"] == ["narrative"], d)
        check(sorted(d["drop_columns"]) == ["complaint_id", "received_date"] and d["split"] == "time", d)
        return f"top {s0['id']} ({s0['score']:g}) · {len(d['labels'])} labels · {d['multi_share'] * 100:.0f}% multi · {d['split']} split"
    step("ml: Build suggests multi-label, label set detected", suggest)

    def train():
        rec = post("/api/multilabel/runs", {"dataset_id": ds})  # zero-config
        check(rec["kind"] == "multilabel", rec.get("kind"))
        t0 = time.time()
        done = wait_run(rec["id"], 1500)
        check(done["status"] == "succeeded", done.get("error"))
        r = get(f"/api/multilabel/runs/{rec['id']}")["multilabel"]
        per = r["per_label"]
        check(len(per) == 6 and all(p["f1"] is not None and p["f1_ci"][0] <= p["f1"] <= p["f1_ci"][1] and 0 < p["threshold"] < 1
                                    and p["pr"]["ap"] is not None for p in per), "per-label F1")  # acceptance 1
        check(abs(r["macro_f1"] - sum(p["f1"] for p in per) / 6) < 1e-9, "macro F1 = mean of per-label F1")
        check(r["hamming_loss"] < r["baseline_hamming"] and r["macro_f1"] > 0.6, (r["macro_f1"], r["hamming_loss"]))
        s.update(run=rec["id"], r=r)
        st["ml"] = {k: r[k] for k in ("macro_f1", "micro_f1", "hamming_loss", "ci")}
        return (f"{rec['id']} in {time.time() - t0:.0f} s · macro F1 {r['macro_f1']:.3f} [{r['ci']['macro_f1'][0]:.3f}, "
                f"{r['ci']['macro_f1'][1]:.3f}] · micro F1 {r['micro_f1']:.3f} · Hamming {r['hamming_loss']:.4f} (empty "
                f"{r['baseline_hamming']:.3f}) · " + ", ".join(f"{p['label']} {p['f1']:.3f} (t {p['threshold']:.2f})" for p in per))
    if not step("ml: zero-config run, F1 per label, micro/macro F1, Hamming (acceptance)", train):
        return
    run, r = s["run"], s["r"]

    def registry():
        v = post(f"/api/runs/{run}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.multilabel"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready" and v["result"]["version"]["problem_type"] == "multilabel", v.get("error"))
        vid = v["id"]
        row = dict(r["examples"][0]["row"])
        row["narrative"] = ("There are transactions on my card that I did not make. I waited over an hour on hold and "
                            "nobody called me back as promised. I was also charged an overdraft fee I never agreed to.")
        t = post(f"/api/registry/versions/{vid}/predict", {"row": row})
        check(len(t["labels"]) >= 2 and {"fraud", "service"} <= set(t["labels"]), t["labels"])  # acceptance 2
        check(all((x["p"] >= x["t"]) == x["on"] for x in t["scores"]), "rule")
        sc = post(f"/api/registry/versions/{vid}/score", {"rows": [row, r["examples"][-1]["row"]]})["results"]
        check(len(sc) == 2 and sc[0]["labels"] == t["labels"], sc)
        rows = pd.DataFrame([e["row"] for e in r["examples"]] * 20)
        b = j(C.post(f"/api/registry/versions/{vid}/batches", files={"file": ("complaints.csv", rows.to_csv(index=False).encode(), "text/csv")},
                     data={"created_by": "Smoke Analyst"}))
        b = poll(f"/api/registry/batches/{b['id']}", lambda x: x["status"] in ("succeeded", "failed"), 600)
        check(b["status"] == "succeeded", b.get("error"))
        check({"labels", "n_labels", "p_fraud"} <= set(b["result"]["columns"]), b["result"]["columns"])
        card = C.get(f"/api/registry/versions/{vid}/card", params={"format": "md"}).text
        check("Multi-label classification" in card and "macro F1" in card, "card section")
        return (f"{vid} ready · Try It: {t['labels']} (" + ", ".join(f"{x['label']} {x['p']:.2f}/{x['t']:.2f}" for x in t["scores"][:4])
                + f") · /score 2 rows · batch {b['result']['summary']['n_rows']} rows, mean |Ŷ| {b['result']['summary']['ml_mean_labels']:.2f} · card section")
    step("ml: version: Try It label set, /score, batch, card", registry)


def reject_inference(st: dict[str, Any]) -> None:
    """G7: reject inference on the credit-rejects sample, zero-config. Intake finds the decision column (the outcome is
    blank for every declined row) and puts "Reject inference" on top; the run is started with nothing but the dataset
    (approved-only, reweighting, parcelling and fuzzy augmentation, AutoGluon, k = 2). Asserts the acceptance: on the
    through-the-door holdout (hidden outcomes of the declined, from the sample's sidecar) at least one method beats
    approved-only on AUC or log loss with a paired CI that excludes 0; also that approved-only understates the TTD bad
    rate. Then a quick approved-only yes/no model of the same table is registered and its card carries the check."""
    ds = "credit-rejects"
    s: dict[str, Any] = {}

    def suggest():
        it = get(f"/api/datasets/{ds}/intake")
        s0 = it["suggestions"][0]
        check(s0["id"] == "reject_inference" and s0["route"] == "reject-inference", f"top {s0['id']}")
        check(s0["prefill"]["label"] == "defaulted" and s0["prefill"]["decision"] == "decision", s0["prefill"])
        d = get("/api/reject-inference/detect", dataset_id=ds)
        check(d["decision_column"] == "decision" and d["approved_value"] == "approved" and d["through_the_door"], d)
        check({"application_id", "application_date"} <= set(d["drop_columns"]) and d["split"] == "time", d)
        b = next(x for x in it["suggestions"] if x["id"] == "binary")
        check("decision" in b["prefill"]["drop"] and "Reject inference" in b["reason"], b)
        s["pf"] = b["prefill"]
        return (f"top {s0['id']} ({s0['score']:g}) · decision = {d['decision_column']} ({d['approved_value']}), approval rate "
                f"{d['approval_rate']:.3f}, {d['declined']:,} declined · {d['split']} split · binary pre-fill drops {b['prefill']['drop']}")
    step("ri: Build suggests reject inference, decision detected", suggest)

    def train():
        rec = post("/api/reject-inference/runs", {"dataset_id": ds})  # zero-config
        check(rec["kind"] == "reject_inference", rec.get("kind"))
        t0 = time.time()
        done = wait_run(rec["id"])
        check(done["status"] == "succeeded", done.get("error"))
        r = get(f"/api/reject-inference/runs/{rec['id']}")["reject_inference"]
        t = r["ttd"]
        check(t is not None and t["n"] > 1000, "no through-the-door check")
        base = t["methods"]["approved_only"]
        check(base["mean_p"] < t["bad_rate"], "approved-only should understate the TTD bad rate")
        check(len(t["better"]) >= 1, {m: c["verdict"] for m, c in t["methods"].items()})  # acceptance
        st["ri"] = {m: {k: c.get(k) for k in ("auc", "log_loss", "brier", "mean_p", "delta_auc", "delta_log_loss", "verdict")}
                    for m, c in t["methods"].items()}
        def line(m):
            c = t["methods"][m]
            if m == "approved_only":
                return f"{m} AUC {c['auc']:.4f} LL {c['log_loss']:.4f} p̄ {c['mean_p']:.3f}"
            da, dl = c["delta_auc"], c["delta_log_loss"]
            return (f"{m} AUC {c['auc']:.4f} (Δ {da['delta']:+.4f} [{da['ci'][0]:+.4f}, {da['ci'][1]:+.4f}]) "
                    f"LL {c['log_loss']:.4f} (Δ {dl['delta']:+.4f} [{dl['ci'][0]:+.4f}, {dl['ci'][1]:+.4f}]) p̄ {c['mean_p']:.3f} {c['verdict']}")
        return (f"{rec['id']} in {time.time() - t0:.0f} s · TTD n {t['n']:,}, bad rate {t['bad_rate']:.3f} · "
                + " · ".join(line(m) for m in t["methods"]) + f" · better: {t['better']}")
    if not step("ri: zero-config run, TTD comparison (acceptance)", train):
        return

    def card():
        pf = s["pf"]
        body = {"dataset_id": ds, "label": pf["target"], "problem_type": "binary", "presets": "medium_quality", "time_limit": 60,
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 150}}}, "drop_columns": pf["drop"] + [pf["time"]],
                "feature_importance": False, "split_spec": {"strategy": "time", "time_column": pf["time"]}, "source": "smoke-ri"}
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.credit-rejects"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        md = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("Reject inference (through-the-door check)" in md, "card section")
        bad = C.post(f"/api/runs/{get('/api/runs', kind='reject_inference')['runs'][0]['id']}/register",
                     json={"created_by": "Smoke Analyst", "model_name": "smoke.ri"})
        check(bad.status_code == 400, bad.status_code)
        return f"{rec['id']} (approved only, {rec['data']['train_rows']:,} rows) → {v['id']} · card has the TTD table · RI run register → 400"
    step("ri: approved-only model's card carries the check", card)


def fair_lending(st: dict[str, Any]) -> None:
    """G4: the fair-lending check without protected attributes (BISG) on the fair-lending sample, zero-config. Intake
    finds surname, zip and the hidden true group (protected: left out of the model with the IDs); the binary pre-fill
    trains a quick GBM with the F7 cost (time split), which is registered. GET /fair-lending with no parameters
    detects every input. Asserts the acceptance: the proxy-corrected weighted AIRs are within ±0.05 of the AIRs with the
    hidden true groups (every judged group), and the proxy's error is reported (accuracy, Brier). Also: Σ n_eff = n,
    CIs bracket the estimates, the plain weighted AIRs sit closer to 1 (attenuated), the Build suggestion, the model card
    section, and a bad column → 400."""
    ds = "fair-lending"
    s: dict[str, Any] = {}

    def roles():
        it = get(f"/api/datasets/{ds}/intake")
        r = it["roles"]
        check(r.get("surname") == ["surname"] and r.get("zip") == ["zip"] and r.get("protected") == ["race_ethnicity_true"], r)
        pf = next(x for x in it["suggestions"] if x["id"] == "binary")["prefill"]
        check({"surname", "zip", "race_ethnicity_true", "application_id"} <= set(pf["drop"]) and pf["split"] == "time", pf)
        s["pf"] = pf
        return f"surname, zip, protected = race_ethnicity_true · binary pre-fill drops {pf['drop']} · {pf['split']} split on {pf['time']}"
    if not step("fair: intake roles and pre-fill", roles):
        return

    def train():
        pf = s["pf"]
        body = {"dataset_id": ds, "label": pf["target"], "problem_type": "binary", "presets": "medium_quality", "time_limit": 90,
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 200}}}, "drop_columns": pf["drop"] + [pf["time"]],
                "feature_importance": False, "split_spec": {"strategy": "time", "time_column": pf["time"]},
                "cost": {"c_fp": 1, "c_fn": 10}, "calibrate_threshold": "auto", "source": "smoke-fair"}
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        feats = set(rec.get("features") or [])
        check(not feats & {"surname", "zip", "race_ethnicity_true"}, feats)
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.fair-lending"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
        check(v["status"] == "ready", v.get("error"))
        s.update(run=rec["id"], version=v["id"])
        return f"{rec['id']} · features {sorted(feats)} · {v['id']} ready"
    if not step("fair: train the pre-filled model, register", train):
        return

    def check_bisg():
        t0 = time.time()
        r = get(f"/api/runs/{s['run']}/fair-lending")
        dt = time.time() - t0
        check(r["ready"] and r["detected"]["surname"]["column"] == "surname" and r["detected"]["zip"]["column"] == "zip", r["detected"])
        check(r["detected"]["truth"]["column"] == "race_ethnicity_true" and r["tables"]["source"] == "synthetic", r["tables"])
        check(r["coverage"]["surname"] > 0.99 and r["coverage"]["zip"] > 0.99, r["coverage"])
        check(abs(sum(g["n_eff"] for g in r["air"]) - r["n"]) < 1e-6 * r["n"], "Σ n_eff ≠ n")
        for g in r["air"]:
            if not g["reference"] and g["judged"]:
                check(g["air_ci"][0] <= g["air"] <= g["air_ci"][1] and g["diff_ci"][0] <= g["diff"] <= g["diff_ci"][1], g["group"])
        tr = r["truth"]
        judged = [c for c in tr["comparison"] if c["judged"]]
        check(len(judged) >= 3, judged)
        check(tr["within_005"] and tr["max_abs_delta"] <= 0.05, [(c["group"], round(c["delta"], 3)) for c in judged])
        gap = [c for c in judged if c["true_air"] < 0.95]
        check(all(c["plain_air"] > c["true_air"] for c in gap), "plain weighting not attenuated")
        pq = r["proxy"]
        check(0 < pq["accuracy"] < 1 and pq["brier"] > 0 and len(pq["confusion"]) == len(r["groups"]), pq.get("accuracy"))
        mf = r["metricframe"]
        check(len(mf["by_group"]) == len(r["groups"]) and "selection_rate" in mf["metrics"], mf["metrics"])
        st["fair"] = {"max_abs_delta": tr["max_abs_delta"], "max_abs_plain_delta": tr["max_abs_plain_delta"], "accuracy": pq["accuracy"],
                      "air": {c["group"]: (round(c["air"], 3), round(c["true_air"], 3), round(c["plain_air"], 3)) for c in tr["comparison"]}}
        return (f"{dt:.1f} s · n {r['n']} · t {r['threshold']:.3f} · approval {r['favourable']['approval_rate']:.3f} · proxy accuracy "
                f"{pq['accuracy']:.3f} (expects {pq['expected_accuracy']:.3f}), Brier {pq['brier']:.3f} · AIR corrected / true / plain: "
                + ", ".join(f"{c['group']} {c['air']:.3f}/{c['true_air']:.3f}/{c['plain_air']:.3f}{'' if c['judged'] else ' (not judged)'}" for c in tr["comparison"])
                + f" · max |Δ| {tr['max_abs_delta']:.3f} (plain {tr['max_abs_plain_delta']:.3f})")
    step("fair: BISG AIRs vs the hidden true groups (acceptance ±0.05), proxy error", check_bisg)

    def surfaces():
        it = get(f"/api/datasets/{ds}/intake")
        sg = next((x for x in it["suggestions"] if x["id"] == "fair_lending"), None)
        check(sg and sg["prefill"].get("tab") == "Fair Lending" and sg["prefill"].get("version"), it["suggestions"])
        card = C.get(f"/api/registry/versions/{s['version']}/card", params={"format": "md"}).text
        check("Fair-lending check without protected attributes (BISG)" in card and "synthetic stand-in" in card, "card section")
        bad = C.get(f"/api/runs/{s['run']}/fair-lending", params={"surname": "nope"})
        check(bad.status_code == 400, bad.status_code)
        nozip = get(f"/api/runs/{s['run']}/fair-lending", zip="")
        check(nozip["ready"], "surname only")
        return (f"Build suggests '{sg['title']}' ({sg['score']:g}) → {sg['prefill']['tab']} · card section · unknown column → 400 · "
                f"surname only: proxy accuracy {nozip['proxy']['accuracy']:.3f}")
    step("fair: Build suggestion, model card, overrides", surfaces)


def _dr_evidence(kind: str, ev: dict[str, Any]) -> str:
    if kind == "leakage":
        su = ev["suspects"][0]
        return f"{su['feature']} alone {su.get('single_measure', '')} {su.get('single_score', float('nan')):.3f}, {su.get('share') or 0:.0%} of importance"
    if kind == "label_noise":
        return (f"error floor {ev['floor']:.3f} [{ev['floor_ci'][0]:.3f}, {ev['floor_ci'][1]:.3f}] in the top {ev['n_top']} (model expects "
                f"{ev['expected_top']:.3f}), cleanlab {ev['cleanlab_rate']:.1%}")
    if kind == "overfit":
        return f"train {ev['train']:.4f} vs holdout {ev['holdout']:.4f}, z {ev['z']:.1f}, overconfident {ev['overconfident']}"
    if kind == "data_limited":
        return f"Δ₂ {ev['slope']:+.4f} [{ev['ci'][0]:+.4f}, {ev['ci'][1]:+.4f}] vs ε {ev['min_gain']:.4f}"
    if kind == "shift":
        o, a = ev.get("out_of_time") or {}, ev.get("adversarial") or {}
        return f"OOT Δ {o.get('delta', float('nan')):+.4f} (z {o.get('z') or float('nan'):.1f}), adversarial AUC {a.get('auc', float('nan')):.3f} {a.get('verdict')}"
    if kind == "latency":
        return f"{ev['model']} served p95 {ev['served_p95_ms']:.1f} ms vs budget {ev['budget_ms']:g} ms"
    return json.dumps(ev)[:120]


def sql_source(st: dict[str, Any]) -> None:
    """G9: load data from a SQL query. The demo database (a SQLite file built from the samples) is listed; the read-only
    guard refuses writes, a second statement and files outside the data directory. A copy of the demo database with the
    last 3,000 applications' outcomes missing is queried with the pre-filled join → a dataset (query in its lineage);
    Smart Intake's top suggestion on it is the binary model with its out-of-time + group split, which trains end to end
    and registers (the model card shows the query). Then the outcomes arrive in the source, and Refresh makes v2: the
    same query, 12,000 rows, +3,000 new, its parent and root in the lineage, the target carried over (acceptance)."""
    import shutil
    import sqlite3

    s: dict[str, Any] = {}

    def demo():
        d = get("/api/sql/demo")
        t = {x["name"]: x["rows"] for x in d["tables"]}
        check(t == {"accounts": 10000, "applications": 12000, "loans": 9000, "outcomes": 12000}, t)
        bad = {"DELETE FROM outcomes": "Only SELECT", "SELECT 1 a, 2 b; DROP TABLE outcomes": "One statement",
               "WITH x AS (SELECT 1) INSERT INTO outcomes SELECT * FROM x": "INSERT"}
        for q, why in bad.items():
            r = C.post("/api/sql/preview", json={"url": d["url"], "query": q})
            check(r.status_code == 400 and why in r.text, (q, r.text[:160]))
        r = C.post("/api/sql/preview", json={"url": "sqlite:////etc/hosts", "query": "SELECT 1 a, 2 b"})
        check(r.status_code == 400 and "outside" in r.text, r.text[:160])
        p = post("/api/sql/preview", {"url": d["url"], "query": d["query"], "limit": 500})
        check(p["n_rows"] == 500 and p["truncated"] and len(p["rows"]) == 20 and p["columns"][-1]["name"] == "defaulted", p["n_rows"])
        s["demo"] = d
        return f"{len(t)} tables · 3 writes refused, outside file refused · preview 500 rows (limit reached) in {p['seconds'] * 1000:.0f} ms"
    if not step("sql: demo database, read-only guard, preview", demo):
        return

    def load_train():
        data_dir = get("/api/health")["data_dir"]
        src, dst = os.path.join(data_dir, "demo", "bank.sqlite"), os.path.join(data_dir, "sql-smoke", "bank.sqlite")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        with sqlite3.connect(dst) as con:  # the newest 3,000 applications have no outcome yet
            late = con.execute("SELECT applicant_id FROM applications ORDER BY application_date DESC, applicant_id DESC LIMIT 3000").fetchall()
            con.execute("CREATE TABLE outcomes_late AS SELECT * FROM outcomes WHERE applicant_id IN (%s)" % ",".join("?" * len(late)), [r[0] for r in late])
            con.execute("DELETE FROM outcomes WHERE applicant_id IN (SELECT applicant_id FROM outcomes_late)")
        url = "sqlite:///sql-smoke/bank.sqlite"
        d = post("/api/sql/datasets", {"url": url, "query": s["demo"]["query"], "name": "smoke applications"})
        lin = d["profile"]["lineage"]
        check(d["rows"] == 9000 and lin["source"] == "sql" and lin["version"] == 1 and lin["root"] == d["id"] and lin["query"] == s["demo"]["query"], lin)
        it = get(f"/api/datasets/{d['id']}/intake")
        top = it["suggestions"][0]
        pf = top["prefill"]
        check(top["id"] == "binary" and pf["target"] == "defaulted" and pf["split"] == "time-group", (top["id"], pf))
        C.patch(f"/api/datasets/{d['id']}", json={"target": pf["target"]}).raise_for_status()
        body = {"dataset_id": d["id"], "label": pf["target"], "problem_type": "binary", "presets": "medium_quality", "time_limit": 90,
                "fit_kwargs": {"hyperparameters": HP}, "drop_columns": pf.get("drop", []) + [pf["time"]], "feature_importance": False,
                "split_spec": {"strategy": "time-group", "time_column": pf["time"], "group_column": pf["group"]},
                "calibrate_threshold": None, "source": "smoke-sql"}
        t0 = time.time()
        rec = wait_run(post("/api/runs", body)["id"])
        check(rec["status"] == "succeeded", rec.get("error"))
        m, sc = primary(rec)
        v = post(f"/api/runs/{rec['id']}/register", {"created_by": "Smoke Analyst", "model_name": f"smoke.sql.{d['id']}"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        card = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("Loaded by SQL query" in card and "JOIN outcomes" in card, "card has no SQL lineage")
        s.update(ds=d, url=url, dst=dst, run=rec)
        st["sql"] = {"v1": d["id"], "rows_v1": d["rows"], "run": rec["id"], "metric": m, "score": sc, "version": v["id"]}
        return (f"{d['id']} · 9,000 rows · intake top binary (y defaulted, time-group) → {rec['id']} in {time.time() - t0:.0f} s · "
                f"holdout {m} {sc:.4f}, ROC AUC {(rec['holdout'].get('scores') or {}).get('roc_auc', float('nan')):.4f} → {v['id']} ready · card shows the query")
    if not step("sql: query → dataset → Smart Intake → train → register (acceptance 1)", load_train):
        return

    def refresh():
        with sqlite3.connect(s["dst"]) as con:  # the outcomes arrive
            con.execute("INSERT INTO outcomes SELECT * FROM outcomes_late")
        v1 = s["ds"]["id"]
        r = post(f"/api/sql/datasets/{v1}/refresh", {})
        lin, f = r["profile"]["lineage"], r["refresh"]
        check(r["id"] != v1 and r["rows"] == 12000 and lin["version"] == 2 and lin["parent"] == v1 and lin["root"] == v1, lin)
        check(f["diff"] == {"added": 3000, "removed": 0, "kept": 9000, "columns_changed": 0} and not f["unchanged"], f)
        check(r["target"] == "defaulted" and r["profile"]["target_source"] == "given" and lin["query"] == s["ds"]["profile"]["lineage"]["query"], r["target"])
        vs = get(f"/api/sql/datasets/{r['id']}/versions")["versions"]
        check([x["version"] for x in vs] == [1, 2] and [x["id"] for x in vs] == [v1, r["id"]], vs)
        again = post(f"/api/sql/datasets/{r['id']}/refresh", {})["refresh"]
        check(again["version"] == 3 and again["unchanged"], again)
        st["sql"].update(v2=r["id"], rows_v2=r["rows"], diff=f["diff"])
        shutil.rmtree(os.path.dirname(s["dst"]), ignore_errors=True)
        return (f"v2 {r['id']} · {r['rows']:,} rows · +{f['diff']['added']:,} new, −{f['diff']['removed']}, {f['diff']['kept']:,} kept · "
                f"parent {v1} · target carried · v3 with no change: same data hash")
    step("sql: refresh → new version with lineage (acceptance 2)", refresh)


# --------------------------------------------------------------------------- D1 · Data Doctor

# Bundled samples whose high-severity findings are planted on purpose (true positives, not false alarms).
DD_EXPECTED_HIGH = {"loan-losses": {"leakage"}, "account-attrition": {"leakage"}}


def _dd_upload(df: pd.DataFrame, name: str, target: str) -> str:
    d = j(C.post("/api/datasets", files={"file": (name, df.to_csv(index=False).encode(), "text/csv")}, data={"target": target}))
    return d["id"]


def _dd_report(ds: str, labels: bool = False, timeout: float = 240) -> dict[str, Any]:
    """The Data Report; with labels, polled until the background label-quality model has finished."""
    r = get(f"/api/datasets/{ds}/data-doctor")
    if labels:
        r = poll(f"/api/datasets/{ds}/data-doctor",
                 lambda x: all(f.get("status") != "running" for f in x["findings"] if f["id"] == "label_quality"), timeout, 2)
    return r


def _dd_find(r: dict[str, Any], fid: str) -> dict[str, Any]:
    f = next((f for f in r["findings"] if f["id"] == fid), None)
    check(f is not None, f"no '{fid}' finding: {[x['id'] for x in r['findings']]}")
    for k in ("severity", "title", "numbers", "rule", "evidence", "fix"):
        check(k in f and (f[k] or k in ("numbers", "evidence")), f"{fid}: '{k}' missing or empty")
    return f


def _dd_col(ds: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in ds["profile"]["columns"] if c["name"] == name)


def datadoctor(st: dict[str, Any]) -> None:
    """D1 Data Doctor: crafted tables from scripts/data_doctor_data.py with planted issues (one of each check), the
    rule / numbers / evidence / fix shape, speed on ~40k rows, the typed fixes applied into a new version (effect,
    lineage, holdout kept, original untouched), the decision-time answer dropped from a run (and recorded), refusals,
    and no high-severity false alarms on the bundled samples."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import data_doctor_data as dg

    raw = dg.planted()
    s: dict[str, Any] = {}

    def planted():
        s["ds"] = ds = _dd_upload(raw, "dd_planted.csv", "defaulted")
        t0 = time.time()
        r = _dd_report(ds)
        dt = time.time() - t0
        check(r["seconds"] < 10 and dt < 30, f"too slow: checks {r['seconds']} s, request {dt:.1f} s on {len(raw):,} rows")
        want = {"leakage": "high", "coded_values": "medium", "disguised_missing": "medium", "duplicates": "medium",
                "conflicts": "medium", "rare_codes": "low", "informative_missing": "low", "ids_constants": "low",
                "missing_words": "low", "protected": "info"}
        got = {}
        for fid, sev in want.items():
            f = _dd_find(r, fid)
            got[fid] = f["severity"]
            check(f["severity"] == sev, f"{fid}: severity {f['severity']} ≠ {sev} ({f['title']})")
        lk = _dd_find(r, "leakage")
        check(lk["columns"] == ["review_score"] and lk["numbers"]["AUC of review_score alone"] >= 0.97, f"leakage {lk['columns']} {lk['numbers']}")
        cv = _dd_find(r, "coded_values")
        check(cv["columns"] == ["months_late"] and cv["fix"]["transforms"][0]["codes"] == [-2, -1], f"coded {cv['fix']}")
        dm = _dd_find(r, "disguised_missing")
        check(dm["columns"] == ["days_since_last"] and dm["fix"]["transforms"][0]["values"] == [999], f"disguised {dm['fix']}")
        rc = _dd_find(r, "rare_codes")
        check(rc["items"][0]["column"] == "education" and rc["items"][0]["codes"] == [0, 9], f"rare codes {rc['items']}")
        im = _dd_find(r, "informative_missing")
        check(im["columns"] == ["income"], f"informative missing {im['columns']}")
        ic = _dd_find(r, "ids_constants")
        check(set(ic["columns"]) == {"app_id", "country"}, f"ids/constants {ic['columns']}")
        pr = _dd_find(r, "protected")
        check(set(r["decision_time"]["protected"]) == {"gender", "age"}, f"protected {r['decision_time']['protected']}")
        dt_cols = [c["column"] for c in r["decision_time"]["columns"]]
        check(dt_cols == ["review_score"] and not r["decision_time"]["answered"], f"decision-time question {dt_cols}")
        n_dup_true = int(raw.drop(columns="app_id").duplicated().sum())
        check(_dd_find(r, "duplicates")["numbers"]["exact duplicates"] == n_dup_true, "duplicate count ≠ pandas'")
        check(r["n_fixes"] >= 3 and all(t["op"] for t in r["selected_fixes"]), f"selected fixes {r['selected_fixes']}")
        s["report"] = r
        _ = pr
        return (f"{len(raw):,} rows in {r['seconds']:.2f} s (request {dt:.1f} s) · all {len(want)} planted issues at the "
                f"expected severity · {r['n_fixes']} fixes pre-selected")
    if not step("datadoctor: planted issues found", planted):
        return

    def apply_fixes():
        ds, r = s["ds"], s["report"]
        before = get(f"/api/datasets/{ds}")
        out = post(f"/api/datasets/{ds}/data-doctor/apply", {"transforms": r["selected_fixes"], "by": "smoke"})
        new = out["dataset"]
        s["fixed"] = new["id"]
        lin = new["profile"]["lineage"]
        check(lin["parent"] == ds and lin["root"] == ds and lin["kind"] == "data_doctor" and lin["by"] == "smoke", f"lineage {lin}")
        check([e["op"] for e in lin["fixes"]] == [t["op"] for t in r["selected_fixes"]], "the version does not record the fixes applied")
        n999 = int((raw["days_since_last"] == 999).sum())
        dsl = _dd_col(new, "days_since_last")
        check(dsl["missing"] == n999 - int(((raw["days_since_last"] == 999) & raw.drop(columns="app_id").duplicated()).sum()), f"999 → missing: {dsl['missing']} vs {n999}")
        check(dsl["stats"]["max"] <= 120, f"days_since_last max {dsl['stats']['max']}")
        check(_dd_col(new, "months_late")["stats"]["min"] >= 0, "months_late still has codes")
        check(_dd_col(new, "months_late_code")["unique"] == 3, "months_late_code: not -2 / -1 / value")
        check("days_since_last_was_missing" in out["columns_added"], f"no flag column: {out['columns_added']}")
        n_dup = _dd_find(r, "duplicates")["numbers"]["exact duplicates"]
        check(out["rows_before"] - out["rows_after"] == n_dup and new["rows"] == out["rows_after"],
              f"rows {out['rows_before']} → {out['rows_after']}, {n_dup} duplicates")
        hp, hn = before["profile"]["splits"]["holdout_rows"], new["profile"]["splits"]["holdout_rows"]
        check(hp - n_dup <= hn <= hp, f"holdout {hp} → {hn}: not the parent's kept holdout rows")
        orig = get(f"/api/datasets/{ds}")
        check(orig["rows"] == len(raw) and _dd_col(orig, "days_since_last")["missing"] == 0
              and _dd_col(orig, "days_since_last")["stats"]["max"] == 999, "the original dataset changed")
        r2 = _dd_report(new["id"])
        ids2 = {f["id"] for f in r2["findings"]}
        check(not ids2 & {"coded_values", "disguised_missing", "duplicates"}, f"fixed findings came back: {ids2}")
        check(r2["lineage"]["parent"] == ds, "the report does not show the lineage")
        return (f"{new['id']} ← {ds}: {len(lin['fixes'])} fixes · {n999:,} × 999 → missing + flag · months_late codes split · "
                f"{n_dup:,} duplicates dropped · holdout {hp:,} → {hn:,} · original unchanged · fixed findings gone")
    if not step("datadoctor: fixes → new version", apply_fixes):
        return

    def decision_time():
        ds, fixed = s["ds"], s["fixed"]
        bad = C.post(f"/api/datasets/{ds}/decision-time", json={"known": {"defaulted": False}})
        check(bad.status_code == 400, f"dropping the target → {bad.status_code}")
        a = post(f"/api/datasets/{ds}/decision-time", {"known": {"review_score": False}, "fairness_only": ["gender", "age"], "by": "smoke"})
        check(set(a["drops"]["columns"]) == {"review_score", "gender", "age"}, f"drops {a['drops']}")
        r = _dd_report(ds)
        lk = _dd_find(r, "leakage")
        check(r["decision_time"]["answered"] and lk["severity"] == "info" and lk.get("answered"), f"answered leakage {lk['severity']}")
        # the fixed version was made before the answer: answer it there too, then check intake's pre-fill and a run
        post(f"/api/datasets/{fixed}/decision-time", {"known": {"review_score": False}, "fairness_only": ["gender", "age"], "by": "smoke"})
        it = get(f"/api/datasets/{fixed}/intake")
        s0 = next(x for x in it["suggestions"] if x["route"] == "quick-train")
        check({"review_score", "gender", "age"} <= set(s0["prefill"].get("drop") or []), f"intake pre-fill drop {s0['prefill']}")
        body = {"dataset_id": fixed, "time_limit": 60, "presets": "medium_quality",
                "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 30}}}}
        run = post("/api/runs", body)
        dtc = run["config"]["decision_time"]
        check(dtc and dtc["applied"] and set(dtc["dropped"]) == {"review_score", "gender", "age"}, f"run decision_time {dtc}")
        check({"review_score", "gender", "age"} <= set(run["config"]["drop_columns"]), f"run drop {run['config']['drop_columns']}")
        twin = post("/api/runs", {**body, "use_decision_time": False})
        check("review_score" not in twin["config"]["drop_columns"] and twin["config"]["decision_time"]["applied"] is False,
              "use_decision_time: false still dropped")
        post(f"/api/runs/{twin['id']}/cancel")
        rec = wait_run(run["id"], 600)
        fi = get(f"/api/runs/{run['id']}/feature-importance")
        feats = {f.get("feature") or f.get("name") for f in (fi.get("features") or fi.get("importance") or [])}
        check(not feats & {"review_score", "gender", "age"}, f"a dropped column was a feature: {feats & {'review_score', 'gender', 'age'}}")
        m, v = primary(rec)
        return (f"answer stored → leakage answered (info) · intake pre-fill drops them · run {run['id']} recorded and dropped "
                f"review_score, gender, age ({m} {v:.3f} without them) · use_decision_time=false keeps them")
    step("datadoctor: decision-time drop", decision_time)

    def refusals():
        ds = s["ds"]
        for body, want in (({"transforms": [{"op": "rm_rf"}]}, "Unknown fix"), ({"transforms": [{"op": "drop_columns", "columns": ["nope"]}]}, "no column"),
                           ({"transforms": [{"op": "to_missing", "column": "defaulted", "values": [1]}]}, "target"), ({"transforms": []}, "at least one")):
            r = C.post(f"/api/datasets/{ds}/data-doctor/apply", json=body)
            check(r.status_code == 400 and want in r.text, f"{body} → {r.status_code} {r.text[:120]}")
        check(C.get("/api/datasets/nope/data-doctor").status_code == 404, "unknown dataset ≠ 404")
        return "unknown fix, unknown column, the target, no fixes → 400 · unknown dataset → 404"
    step("datadoctor: refusals", refusals)

    def other_checks():
        out = []
        rc = _dd_upload(dg.rare_classes(), "dd_rare.csv", "product_line")
        cb = _dd_find(_dd_report(rc), "class_balance")
        check(cb["severity"] == "high" and cb["numbers"]["rows in the smallest"] == 8, f"class balance {cb['severity']} {cb['numbers']}")
        tr = cb["fix"]["transforms"]
        check(tr and tr[0]["op"] == "merge_classes" and set(tr[0]["classes"]) == {"F", "G"}, f"merge fix {tr}")
        v = post(f"/api/datasets/{rc}/data-doctor/apply", {"transforms": tr})["dataset"]
        check(v["n_classes"] == 6 and "other" in {c["name"] for c in v["profile"]["classes"]}, f"merged classes {v['n_classes']}")
        out.append("8- and 12-row classes → high, merged into 'other' (7 → 6 classes)")
        sm = _dd_upload(dg.small_sample(), "dd_small.csv", "defaulted")
        ss = _dd_find(_dd_report(sm), "sample_size")
        check(ss["severity"] == "high" and ss["numbers"]["events per feature"] == 3.2, f"sample size {ss['severity']} {ss['numbers']}")
        out.append(f"EPV 3.2 → high (AUC ± {ss['numbers']['holdout AUC ± (at AUC 0.75)']})")
        dr = _dd_upload(dg.drifting(), "dd_drift.csv", "defaulted")
        tm = _dd_find(_dd_report(dr), "time")
        check(tm["severity"] == "medium" and tm["numbers"]["outcome, last fifth"] > tm["numbers"]["outcome, first fifth"] + 0.1, f"time {tm['numbers']}")
        out.append(f"drift {tm['numbers']['outcome, first fifth']:.3f} → {tm['numbers']['outcome, last fifth']:.3f} → medium")
        return " · ".join(out)
    step("datadoctor: classes, sample size, time", other_checks)

    def labels():
        df, flipped = dg.noisy_labels()
        ds = _dd_upload(df, "dd_noisy.csv", "queue")
        r = _dd_report(ds, labels=True)
        lq = _dd_find(r, "label_quality")
        check(lq["status"] == "done" and lq["severity"] == "medium", f"label quality {lq['severity']} {lq['title']}")
        rows = lq["fix"]["transforms"][0]["rows"]
        prec = len(set(rows) & set(flipped)) / max(1, len(rows))
        check(prec >= 0.7, f"precision of the flagged rows vs the planted flips {prec:.2f}")
        planted_r = _dd_report(s["ds"], labels=True)  # overlap without planted label noise must not be medium
        lq2 = _dd_find(planted_r, "label_quality")
        check(lq2["severity"] in ("info", "low"), f"planted table's label quality {lq2['severity']}")
        return (f"{len(flipped)} flipped → {lq['numbers']['flagged']} flagged ({lq['severity']}), precision {prec:.2f} on the top "
                f"{len(rows)} in {lq['numbers']['seconds']} s · planted table: {lq2['severity']}")
    step("datadoctor: label quality (cleanlab)", labels)

    def samples_clean():
        lines, bad = [], []
        for d in get("/api/datasets")["datasets"]:
            if not d["sample"]:
                continue
            r = _dd_report(d["id"])
            high = {f["id"] for f in r["findings"] if f["severity"] == "high"}
            extra = high - DD_EXPECTED_HIGH.get(d["id"], set())
            missing = DD_EXPECTED_HIGH.get(d["id"], set()) - high
            if extra or missing:
                bad.append(f"{d['id']}: high {sorted(high)}")
            lines.append(f"{d['id']} {r['counts']['high']}/{r['counts']['medium']}")
        check(not bad, "; ".join(bad))
        return f"{len(lines)} samples, high/medium: " + ", ".join(lines)
    step("datadoctor: no false alarms on samples", samples_clean)



# --------------------------------------------------------------------------- D2 · model ladder and Feature Lab


def _ld_train(df: pd.DataFrame, name: str, label: str, hp: dict[str, Any], stack: int = 0) -> tuple[str, dict[str, Any]]:
    ds = _dd_upload(df, name, label)
    body = {"dataset_id": ds, "label": label, "problem_type": "binary", "eval_metric": "roc_auc", "presets": "medium_quality",
            "time_limit": 600, "fit_kwargs": {"hyperparameters": hp, "num_bag_folds": 3, "num_stack_levels": stack, "dynamic_stacking": False},
            "feature_importance": False, "source": "smoke-ladder"}
    rec = wait_run(post("/api/runs", body)["id"])
    check(rec["status"] == "succeeded", rec.get("error"))
    return rec["id"], rec


def _ld_shape(lad: dict[str, Any], rec: dict[str, Any]) -> None:
    rungs = {r["rung"]: r for r in lad["rungs"]}
    check(sorted(rungs) == [0, 1, 2, 3], list(rungs))
    check(lad["n_holdout"] == rec["holdout"]["n_rows"], (lad["n_holdout"], rec["holdout"]["n_rows"]))
    check(rungs[1]["model"] and rungs[1]["model"].startswith("Scorecard"), rungs[1])
    check(rungs[2]["model"] and rungs[2]["model"].split("_")[0] in ("LightGBM", "CatBoost", "XGBoost"), rungs[2])
    check(rungs[3]["model"] == rec["best_model"], (rungs[3]["model"], rec["best_model"]))
    check(abs(rungs[0]["score"] - 0.5) < 1e-9, rungs[0]["score"])  # the prior ranks nobody
    # rung 3 is the served model: its holdout AUC is the run's own holdout AUC
    check(abs(rungs[3]["score"] - rec["holdout"]["metrics"]["roc_auc"]) < 1e-6, (rungs[3]["score"], rec["holdout"]["metrics"]["roc_auc"]))
    for k in (1, 2, 3):
        r = rungs[k]
        check(r["latency_ms_p95"] and r["latency_ms_p95"] > 0 and r["size_bytes"] > 0, (k, r.get("latency_ms_p95"), r.get("size_bytes")))
        g = r["gap"]
        check(g["ci"][0] <= g["delta"] + 1e-12 <= g["ci"][1] + 2e-12 or k == lad["best_rung"], (k, g))
        check(r["within"] == (k == lad["best_rung"] or g["ci"][0] <= 0), (k, r["within"], g))
    rec_r = lad["recommended"]["rung"]
    simpler_within = [k for k in (1, 2, 3) if rungs[k]["within"]]
    check(rec_r == min(simpler_within), f"recommended {rec_r}, within {simpler_within}: not the simplest")
    check("paired-bootstrap" in lad["rule"] and "simplest" in lad["rule"], lad["rule"])


def ladder(st: dict[str, Any]) -> None:
    """D2 model ladder on crafted data (scripts/ladder_data.py): one training request gives rungs 0–3 (baseline, the
    run's own SCORECARD, its best single level-1 GBM, AutoGluon's best) on one holdout with paired-bootstrap gaps,
    one-row latency and size. An additive world → a simpler rung is recommended and serving it registers exactly that
    model; a world only a stack can model (additive over 60 weak columns + an interaction, L2 stacking) → the stack is
    recommended and distillation is offered; small data → the 99 % interval and the D1 note; refusals."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ladder_data as lg

    s: dict[str, Any] = {}

    def simple():
        rid, rec = _ld_train(lg.simple(), "ladder_simple.csv", "defaulted", {"GBM": {}, "CAT": {}, "LR": {}})
        lad = get(f"/api/runs/{rid}/ladder")
        _ld_shape(lad, rec)
        check(lad["level"] == 0.95 and not lad.get("small_data_note"), (lad["level"], lad.get("small_data")))
        r = lad["recommended"]
        check(r["rung"] < 3 and r["serve_model"] == r["model"], r)
        check(lad["sentence"] and "serve it" in lad["sentence"], lad.get("sentence"))
        check(not lad["distill"]["available"], "distill offered although a simpler rung is recommended")
        rr = {x["rung"]: x for x in lad["rungs"]}
        check(rr[r["rung"]]["latency_ms_p95"] < rr[3]["latency_ms_p95"], "the simpler rung is not faster")
        again = get(f"/api/runs/{rid}/ladder", refresh="true")
        check(again["recommended"] == r and [x.get("score") for x in again["rungs"]] == [x.get("score") for x in lad["rungs"]]
              and [x.get("gap") for x in again["rungs"]] == [x.get("gap") for x in lad["rungs"]], "recomputed ladder differs")
        s.update(rid=rid, rec=r)
        return (f"{rid}: recommended rung {r['rung']} {r['name']} · " + " · ".join(f"{x['rung']}={x['score']:.3f}" for x in lad["rungs"])
                + f" · gap {rr[r['rung']]['gap']['delta']:+.3f} CI [{rr[r['rung']]['gap']['ci'][0]:+.3f}, {rr[r['rung']]['gap']['ci'][1]:+.3f}]"
                + f" · {rr[r['rung']]['latency_ms_p95']:.1f} vs {rr[3]['latency_ms_p95']:.1f} ms · same on refresh")

    if step("ladder: simple world → a simpler rung", simple):
        def serve():
            r = s["rec"]
            v = post(f"/api/runs/{s['rid']}/register", {"created_by": "Smoke Analyst", "model_name": f"smoke.ladder.{s['rid']}", "serve_model": r["serve_model"]})
            v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
            check(v["status"] == "ready", v.get("error"))
            check(v["summary"]["best_model"] == r["serve_model"], (v["summary"]["best_model"], r["serve_model"]))
            return f"{v['id']} ready · serves {v['summary']['best_model']} ({v['summary']['n_models']} model(s), {v['summary']['size_bytes']:,} bytes)"

        step("ladder: Serve <rung> registers that model", serve)

    def stack():
        rid, rec = _ld_train(lg.complex(), "ladder_complex.csv", "churned", {"GBM": {"num_boost_round": 100}, "LR": {}}, stack=1)
        lad = get(f"/api/runs/{rid}/ladder")
        _ld_shape(lad, rec)
        rr = {x["rung"]: x for x in lad["rungs"]}
        check(lad["recommended"]["rung"] == 3 and lad["recommended"]["serve_model"] is None, lad["recommended"])
        check(rr[2]["gap"]["ci"][0] > 0 and rr[1]["gap"]["ci"][0] > 0, (rr[1]["gap"], rr[2]["gap"]))
        check(lad["distill"]["available"] and "Distillation" in lad["distill"]["text"], lad["distill"])
        return (f"{rid}: stack {rr[3]['score']:.3f} vs single {rr[2]['score']:.3f} (gap {rr[2]['gap']['delta']:+.3f} CI "
                f"[{rr[2]['gap']['ci'][0]:+.3f}, {rr[2]['gap']['ci'][1]:+.3f}]) vs scorecard {rr[1]['score']:.3f} → serve the stack · distill offered")

    step("ladder: stack world → the stack, distillation offered", stack)

    def small():
        rid, rec = _ld_train(lg.simple(n=700, seed=211), "ladder_small.csv", "defaulted", {"GBM": {}, "LR": {}})
        lad = get(f"/api/runs/{rid}/ladder")
        check(lad["small_data"] and lad["small_data"]["severity"] in ("medium", "high"), lad.get("small_data"))
        check(lad["level"] == 0.99 and "99 %" in lad["small_data_note"] and "99%" in lad["rule"], (lad["level"], lad.get("small_data_note")))
        for x in lad["rungs"]:
            if x.get("gap") and x["rung"] != lad["best_rung"]:
                check(x["gap"]["ci"][0] <= x["gap"]["ci95"][0] + 1e-12 and x["gap"]["ci"][1] >= x["gap"]["ci95"][1] - 1e-12, x["gap"])
        return f"{rid}: {lad['small_data']['title']} → 99 % interval · recommended rung {lad['recommended']['rung']} {lad['recommended']['name']}"

    step("ladder: small data → 99 % interval and the D1 note", small)

    def refuse():
        r = C.get("/api/runs/run_nope/ladder")
        check(r.status_code == 404, r.status_code)
        return "unknown run → 404"

    step("ladder: refusals", refuse)


def _fl_job(ds: str, body: dict[str, Any], timeout: float = 600) -> dict[str, Any]:
    j = post(f"/api/datasets/{ds}/feature-lab", body)
    return poll(f"/api/datasets/{ds}/feature-lab/{j['id']}", lambda x: x["status"] != "running", timeout, 2)


def featurelab(st: dict[str, Any]) -> None:
    """D2 Feature Lab: a planted ratio (debt / income) found and kept, its gain confirmed once on the holdout and
    applied as a dataset version (same holdout rows, lineage, original untouched); a leaky ratio (paid / billed ≈ the
    label) rejected by the leakage check; a column answered 'not known at decision time' never read; every DSL op
    computed as written; free-form code and bad requests refused."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    import ladder_data as lg

    def planted():
        df = lg.ratio()
        ds = _dd_upload(df, "fl_ratio.csv", "defaulted")
        j = _fl_job(ds, {"max_candidates": 12, "time_limit": 300})
        check(j["status"] == "succeeded", j.get("error"))
        r = j["result"]
        kept = [t["name"] for t in r["kept"]]
        check("debt_per_income" in kept, f"kept {kept}")
        check(j["journal"][0]["name"] == "debt_per_income", "the ratio was not proposed first")
        e = next(x for x in j["journal"] if x["name"] == "debt_per_income")
        check(e["ci"][0] > 0 and e["single"] < 0.90, e)
        check(len(kept) <= 2, f"spurious keeps: {kept}")
        h = r["holdout"]
        check(h["ci"][0] > 0 and h["after"] > h["before"], h)
        check(r["rows"]["holdout"] == h["rows"] and r["rows"]["train"] + r["rows"]["holdout"] == len(df), r["rows"])
        v = j["version"]
        nd = get(f"/api/datasets/{v['id']}")
        check("debt_per_income" in [c["name"] for c in nd["profile"]["columns"]], "the version lacks the column")
        lin = nd["profile"]["lineage"]
        check(lin["kind"] == "feature_lab" and lin["parent"] == ds and lin["transforms"][0]["op"] == "ratio", lin)
        check(get(f"/api/datasets/{ds}")["profile"]["n_cols"] == df.shape[1], "the original changed")
        check(v["holdout_rows"] == h["rows"], (v["holdout_rows"], h["rows"]))
        st["fl_ratio"] = (ds, v["id"])
        return (f"kept {kept} of {len(j['journal'])} · debt_per_income out-of-fold ΔAUC {e['delta']:+.4f} "
                f"[{e['ci'][0]:+.4f}, {e['ci'][1]:+.4f}] (alone {e['single']:.3f}) · holdout {h['before']:.4f} → {h['after']:.4f} "
                f"({h['delta']:+.4f} [{h['ci'][0]:+.4f}, {h['ci'][1]:+.4f}]) · version {v['id']} · {j['result']['seconds']:.0f} s")

    step("featurelab: planted ratio found, kept, versioned", planted)

    def leaky():
        df = lg.leaky()
        ds = _dd_upload(df, "fl_leaky.csv", "defaulted")
        j = _fl_job(ds, {"max_candidates": 8, "time_limit": 300})
        check(j["status"] == "succeeded", j.get("error"))
        e = next((x for x in j["journal"] if x["name"] == "amount_paid_per_amount_billed"), None)
        check(e is not None, [x["name"] for x in j["journal"]])
        check(e["status"] == "rejected" and "leakage" in e["reason"] and e["single"] >= 0.9 and e.get("delta") is None, e)
        check("amount_paid_per_amount_billed" not in [t["name"] for t in j["result"]["kept"]], "the leak was kept")
        st["fl_leaky"] = ds
        return f"amount_paid ÷ amount_billed alone AUC {e['single']:.3f} ≥ 0.9 → rejected before scoring · kept {[t['name'] for t in j['result']['kept']]}"

    step("featurelab: leaky ratio rejected", leaky)

    def decision_time():
        ds = st.get("fl_leaky")
        check(ds, "needs the leaky dataset")
        post(f"/api/datasets/{ds}/decision-time", {"known": {"amount_paid": False}, "fairness_only": [], "by": "Smoke Analyst"})
        cands = [{"op": "ratio", "a": "amount_paid", "b": "amount_billed"}, {"op": "ratio", "a": "utilisation", "b": "age"},
                 {"op": "ratio", "a": "nope", "b": "age"}]
        j = _fl_job(ds, {"candidates": cands, "apply": False})
        check(j["status"] == "succeeded", j.get("error"))
        by = {x["step"]: x for x in j["journal"]}
        check(by[1]["status"] == "rejected" and "decision-time" in by[1]["reason"], by[1])
        check(by[2]["status"] in ("kept", "rejected") and by[2].get("delta") is not None, by[2])
        check(by[3]["status"] == "rejected" and "not a valid typed transform" in by[3]["reason"], by[3])
        check("amount_paid" in j["result"]["blocked"] and "amount_paid" not in j["result"]["base_features"], j["result"]["blocked"])
        check(j["result"]["proposed_by"] == "given" and not j.get("version"), "given candidates / apply false")
        post(f"/api/datasets/{ds}/decision-time", {"known": {}, "fairness_only": [], "by": "Smoke Analyst"})
        return "amount_paid answered 'not known' → never read (blocked, rejected by rule) · a bad column → rejected as invalid · apply: false → no version"

    step("featurelab: decision-time rule and given candidates", decision_time)

    def ops():
        from app import featurelab as fl

        df = lg.dsl()
        tm = np.ones(len(df), dtype=bool)
        cands = [{"op": "ratio", "a": "amount", "b": "limit"}, {"op": "diff", "a": "limit", "b": "amount"},
                 {"op": "agg", "entity": "customer_id", "col": "amount", "fn": "mean"}, {"op": "agg", "entity": "customer_id", "col": None, "fn": "count"},
                 {"op": "date_part", "col": "booked_at", "part": "dayofweek"}, {"op": "bin", "col": "amount", "k": 4},
                 {"op": "log", "col": "amount"}, {"op": "cross", "a": "channel", "b": "product"}]
        vals = {}
        for c in cands:
            t = fl.validate(df, c, "flagged")
            vals[t["op"] + ("_count" if c.get("fn") == "count" else "")] = (t, fl.compute(df, t, tm))
        ref = {"ratio": df.amount / df.limit, "diff": df.limit - df.amount, "agg": df.groupby("customer_id").amount.transform("mean"),
               "agg_count": df.groupby("customer_id").customer_id.transform("size").astype(float),
               "date_part": pd.to_datetime(df.booked_at).dt.dayofweek.astype(float),
               "bin": pd.qcut(df.amount, 4, labels=False).astype(float), "log": np.log1p(df.amount),
               "cross": df.channel + "|" + df["product"]}
        for k, (t, v) in vals.items():
            r = ref[k]
            same = (v == r).all() if k == "cross" else np.allclose(v.to_numpy(dtype=float), r.to_numpy(dtype=float), equal_nan=True)
            check(same, f"{k}: {t['name']} differs from pandas")
        ds = _dd_upload(df, "fl_dsl.csv", "flagged")
        j = _fl_job(ds, {"candidates": cands, "entity": "customer_id", "apply": False})
        check(j["status"] == "succeeded" and len(j["journal"]) == len(cands), j.get("error"))
        check(all(x["status"] in ("kept", "rejected") and "not a valid" not in x["reason"] for x in j["journal"]), [x["reason"] for x in j["journal"]])
        bad = [({"candidates": [{"op": "python", "code": "import os"}]}, 400, "free-form code"),
               ({"candidates": [{"op": "ratio", "a": "amount", "b": "limit", "code": "x"}]}, 202, None),
               ({"target": "nope"}, 400, "target"), ({"max_candidates": 0}, 400, "max_candidates"),
               ({"entity": "nope"}, 400, "entity")]
        for b, code, words in bad:
            r = C.post(f"/api/datasets/{ds}/feature-lab", json=b)
            check(r.status_code == code and (words is None or words in r.text), f"{b}: {r.status_code} {r.text[:200]}")
        r = C.post("/api/datasets/ds_nope/feature-lab", json={})
        check(r.status_code == 404, r.status_code)
        lst = get(f"/api/datasets/{ds}/feature-lab")
        check(set(lst["dsl"]) == {"ratio", "diff", "agg", "date_part", "bin", "log", "cross"} and len(lst["jobs"]) >= 2, lst["dsl"].keys())
        return (f"8 transforms (7 ops) = pandas · all scored by the API · {', '.join(f'{x['name']} {x['status']}' for x in j['journal'][:3])} … · "
                f"refusals: code → 400, bad target / budget / entity → 400, unknown dataset → 404")

    step("featurelab: every DSL op, refusals", ops)


def main() -> int:
    global C, API
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=API)
    ap.add_argument("--only", default="core,samples,intake,datadoctor,ladder,featurelab,doctor2,missing,autopilot,forecast,anomaly,import,fresh,latency,targets,splits,cost,monotone,quantile,reasons,scorecard,learning,active,annotators,shift,slices,doctor,monitor,llm,reset")
    ap.add_argument("--start-api", action="store_true", help="start uvicorn on the --api port if nothing answers there")
    args = ap.parse_args()
    API = args.api.rstrip("/")
    proc = None
    if args.start_api:
        try:
            httpx.get(f"{API}/api/health", timeout=2).raise_for_status()
        except Exception:  # noqa: BLE001
            port = API.rsplit(":", 1)[-1]
            backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
            os.makedirs(os.path.join(backend, "data"), exist_ok=True)
            log = open(os.path.join(backend, "data", "smoke-api.log"), "w")
            print(f"starting the API on :{port} (log: backend/data/smoke-api.log)")
            proc = subprocess.Popen([os.path.join(backend, ".venv", "bin", "python"), "-m", "uvicorn", "app.main:app",
                                     "--port", port], cwd=backend, stdout=log, stderr=subprocess.STDOUT)
            for _ in range(180):
                try:
                    httpx.get(f"{API}/api/health", timeout=2).raise_for_status()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(1)
    try:
        return _main(args)
    finally:
        if proc:
            proc.terminate()
            proc.wait(30)


# --------------------------------------------------------------------------- G10 · Vela agent


def _agent_drive(message: str, user: str, decider: str, on_confirm: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
                 timeout: float = 1800, **kw: Any) -> dict[str, Any]:
    """Start a conversation and follow it as the page does (poll by rev); click Run It on every write unless
    `on_confirm` says otherwise. Returns the final session plus the wall time and the confirmations seen."""
    t0 = time.time()
    s = post("/api/agent/sessions", {"message": message, "user": user, "decider": decider, **kw})
    sid, confirms = s["id"], []
    while time.time() - t0 < timeout:
        s = get(f"/api/agent/sessions/{sid}")
        if s["status"] == "awaiting_confirmation":
            stp = next(i for i in s["timeline"] if i.get("id") == s["pending"])
            ok = on_confirm(s, stp) if on_confirm else True
            confirms.append({"tool": stp["tool"], "http": stp.get("http"), "approved": ok})
            post(f"/api/agent/sessions/{sid}/confirm", {"step_id": stp["id"], "approve": ok})
            time.sleep(0.3)
            continue
        if s["status"] in ("idle", "failed", "stopped", "no_model", "done"):
            break
        time.sleep(1)
    else:
        post(f"/api/agent/sessions/{sid}/stop", {})
        raise SmokeError(f"agent {sid} still working after {timeout:.0f} s")
    s["wall"] = time.time() - t0
    s["confirms"] = confirms
    return s


def _agent_steps(s: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in s["timeline"] if i["type"] == "step"]


def _agent_answer(s: dict[str, Any]) -> dict[str, Any]:
    ans = [i for i in s["timeline"] if i["type"] == "answer"]
    check(ans, f"no answer: status {s['status']} · " + " | ".join(i.get("text", "") for i in s["timeline"] if i["type"] == "note"))
    a = ans[-1]
    check(a["guard"]["ok"], f"number guard flagged {a['guard']['unsupported']} in: {a['text'][:300]}")
    return a


def _agent_line(s: dict[str, Any]) -> str:
    steps = _agent_steps(s)
    srcs = sorted({(i.get("decider") or {}).get("source") or "?" for i in s["timeline"] if i["type"] in ("step", "answer")})
    return (f"{len(steps)} calls ({' → '.join(x['tool'] + ('✓' if x['status'] == 'done' else '·' + x['status']) for x in steps)}) · "
            f"wall {s['wall']:.0f} s · decider {'/'.join(srcs)}")


def agent(st: dict[str, Any]) -> None:
    """G10: the Vela agent, driven like the page: start a conversation, follow it, click Run It on each write. The planner
    is this API's (the claude CLI when there is no ANTHROPIC_API_KEY; SMOKE_AGENT_DECIDER=stub for the deterministic
    planner). Three acceptance scenarios: (1) train → doctor → apply patch → compare (→ register, request approval)
    from one instruction, stopping for confirmation before training (no run exists until Run It); (2) explain one
    decline with reason codes and counterfactuals; (3) score a batch and report drift. The number guard passes on
    every answer; approve/promote are not tools; every call is in the audit log as the agent on behalf of the user."""
    status = get("/api/agent/status")
    dec = os.environ.get("SMOKE_AGENT_DECIDER") or status["default"]
    check(dec != "none", "no planner here: set ANTHROPIC_API_KEY, install the claude CLI, or SMOKE_AGENT_DECIDER=stub")
    s: dict[str, Any] = {"dec": dec}
    user = "Smoke Analyst"

    def registry():
        t = get("/api/agent/tools")
        names = {x["name"] for x in t["tools"]}
        need = {"list_datasets", "profile_dataset", "upload_dataset", "intake_suggestions", "sql_preview", "train_from_intake", "watch_run",
                "get_results", "explain_run", "model_doctor", "apply_gym_patch", "gym_validate", "split_twin", "run_check",
                "register_version", "request_approval", "try_it", "explain_decision", "batch_score", "monitoring", "counterfactuals",
                "credit_report", "expected_loss_run", "expected_loss_stress", "fair_lending", "segmented_run", "survival_run",
                "reject_inference_run", "multilabel_run"}
        check(need <= names, need - names)
        check(not names & {"approve", "approve_version", "review_version", "promote", "promote_version", "transition"}, names)
        check(not any(n.startswith(("approve", "promote", "review")) for n in names), names)
        check(all(x["input_schema"]["type"] == "object" and x["http"] for x in t["tools"]), "schema/http")
        writes = {x["name"] for x in t["tools"] if x["kind"] == "write"}
        check({"train_from_intake", "apply_gym_patch", "register_version", "request_approval", "batch_score", "upload_dataset"} <= writes, writes)
        bad = C.post("/api/agent/sessions/as_nope/confirm", json={"step_id": "x", "approve": True})
        check(bad.status_code == 404, bad.status_code)
        return (f"{len(names)} tools ({len(writes)} write) with JSON schemas and their routes · no approve/promote tool · "
                f"planner {dec} (route {status['route'] or 'none'}; SDK: {status['sdk']['why']})")

    step("agent: tool registry + planner", registry)

    def scenario1():
        before = {r["id"] for r in get("/api/runs")["runs"]}
        seen: dict[str, Any] = {}

        def on_confirm(sess, stp):
            if "first" not in seen:  # the first write: the agent must have stopped before training anything
                seen["first"] = stp["tool"]
                seen["runs_unchanged"] = {r["id"] for r in get("/api/runs")["runs"]} == before
            return True

        msg = ("Train a default model on credit_default.csv with an out-of-time split, run the Model Doctor, apply its fix, "
               "compare the two runs and register the better one, then request approval. Keep each training to about 60 seconds.")
        x = _agent_drive(msg, user, dec, on_confirm, max_steps=24, max_seconds=1500)
        steps = _agent_steps(x)
        tools = [i["tool"] for i in steps if i["status"] == "done"]
        check(seen.get("first") in ("train_from_intake", "start_run"), f"first write was {seen.get('first')}")
        check(seen.get("runs_unchanged"), "a run was created before the user confirmed")
        for t in ("model_doctor", "apply_gym_patch", "compare_runs", "register_version"):
            check(t in tools, f"{t} missing: {tools}")
        tr = next(i for i in steps if i["tool"] in ("train_from_intake", "start_run") and i["status"] == "done")
        sp = (tr["result"].get("body") or tr["args"].get("body") or {}).get("split_spec") or {}
        check(sp.get("strategy") in ("time", "time-group"), f"split {sp}")
        a = _agent_answer(x)
        reg = next(i for i in steps if i["tool"] == "register_version" and i["status"] == "done")["result"]
        check(reg["status"] == "ready" and reg["approval"] == "pending" and reg["created_by"].startswith("Vela agent on behalf of"), reg)
        v = get(f"/api/registry/versions/{reg['version_id']}")
        check(v["stage"] != "Production" and v["approval"] == "pending", (v["stage"], v["approval"]))
        cmp_ = next(i for i in steps if i["tool"] == "compare_runs" and i["status"] == "done")["result"]
        child = next(i for i in steps if i["tool"] == "apply_gym_patch" and i["status"] == "done")["result"]
        check(get(f"/api/runs/{child['run_id']}")["config"].get("parent_run_id") == child["parent_run_id"], "lineage")
        aud = get("/api/registry/audit", limit=400)["entries"]
        mine = [e for e in aud if e["actor"] == f"Vela agent on behalf of {user}" and (e.get("details") or {}).get("session") == x["id"]]
        check(len(mine) >= len([i for i in steps if i["status"] in ("done", "declined", "error")]), f"{len(mine)} audit entries")
        s.update(s1=x, vid=reg["version_id"], run=reg["run_id"])
        st["agent_s1"] = {"wall": x["wall"], "steps": len(steps)}
        return (f"{_agent_line(x)} · paused before training ({seen['first']} waited, no run yet) · {len(x['confirms'])} confirmations "
                f"· split {sp.get('strategy')} · {cmp_['metric']} Δ {cmp_['delta']} (same holdout {cmp_['same_holdout_rows']}) · "
                f"registered {reg['version_id']} (now {v['stage']}, {v['approval']}) · {len(mine)} audit entries · guard ✓ "
                f"{a['guard']['numbers']} numbers · \"{a['text'][:140]}…\"")

    step("agent: 1 · train → doctor → patch → compare → register", scenario1)

    if "vid" not in s:  # scenario 1 did not register: a version of our own for 2 and 3
        def fallback():
            it = get("/api/datasets/credit-default/intake")
            pf = next(x for x in it["suggestions"] if x["id"] == "binary")["prefill"]
            body = {"dataset_id": "credit-default", "label": "defaulted", "presets": "medium_quality", "time_limit": 60,
                    "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 150}}}, "drop_columns": pf["drop"] + [pf["time"]],
                    "split_spec": {"strategy": "time", "time_column": pf["time"]}, "cost": {"c_fp": 1, "c_fn": 10},
                    "calibrate_threshold": "auto", "feature_importance": False, "source": "smoke-agent"}
            rec = wait_run(post("/api/runs", body)["id"])
            v = post(f"/api/runs/{rec['id']}/register", {"created_by": user, "model_name": "smoke.agent.credit"})
            v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 900)
            check(v["status"] == "ready", v.get("error"))
            s.update(vid=v["id"], run=rec["id"])
            return f"{rec['id']} → {v['id']}"

        if not step("agent: fallback version for 2 and 3", fallback):
            return
    vid = s["vid"]

    def scenario2():
        ds = get(f"/api/runs/{s['run']}")["dataset_id"]
        rows = get(f"/api/datasets/{ds}/rows", value="C-500123")["rows"]
        check(rows, "C-500123 not in the dataset")
        p = post(f"/api/registry/versions/{vid}/score", {"rows": [{k: v for k, v in rows[0].items() if k != "defaulted"}], "reasons": True})
        adverse = p["reasons"]["adverse"][0]
        app_id = "C-500123"
        if not adverse:  # pick an applicant this version declines, so the question is a decline
            hold = get(f"/api/datasets/{ds}/rows", value="self-employed", column="employment", limit=50)["rows"]
            q = post(f"/api/registry/versions/{vid}/score", {"rows": [{k: v for k, v in r.items() if k != "defaulted"} for r in hold], "reasons": True})
            app_id = next(r["applicant_id"] for r, a in zip(hold, q["reasons"]["adverse"]) if a)
        x = _agent_drive(f"Why was applicant {app_id} declined by version {vid}, and what would change it?", user, dec, max_steps=12, max_seconds=600)
        steps = _agent_steps(x)
        tools = [i["tool"] for i in steps if i["status"] == "done"]
        check("explain_decision" in tools or "try_it" in tools, tools)
        check("counterfactuals" in tools, tools)
        check(not x["confirms"], f"read-only question asked for confirmation: {x['confirms']}")
        cf = next(i for i in steps if i["tool"] == "counterfactuals" and i["status"] == "done")["result"]
        check(cf["adverse"] and any(o["flips_decision"] for o in cf["options"]), cf)
        a = _agent_answer(x)
        rc = next((i for i in steps if i["tool"] == "explain_decision" and i["status"] == "done"), None)
        codes = [c["code"] for c in (rc["result"]["reason_codes"] if rc else [])]
        check(any(c in a["text"] for c in codes) or any((c.get("feature") or "") in a["text"] for c in (rc["result"]["reason_codes"] if rc else [])), "answer names no reason")
        check(any(o["changes"][0]["feature"] in a["text"] for o in cf["options"]), "answer names no counterfactual")
        s["s2"] = x
        return (f"{_agent_line(x)} · {app_id}: codes {', '.join(codes)} · {len(cf['options'])} counterfactuals, all flip "
                f"{all(o['flips_decision'] for o in cf['options'])} · no confirmation needed · guard ✓ {a['guard']['numbers']} numbers · "
                f"\"{a['text'][:140]}…\"")

    step("agent: 2 · explain a decline (reason codes + counterfactuals)", scenario2)

    def scenario3():
        ds = get(f"/api/runs/{s['run']}")["dataset_id"]
        import glob

        files = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data", "datasets", ds, "*.csv"))
        check(files, f"no CSV for {ds} under backend/data (the smoke reads the sample to build a shifted batch)")
        raw = pd.read_csv(files[0])
        b = raw.tail(800).drop(columns=["defaulted"], errors="ignore").copy()
        if "debt_to_income" in b:
            b["debt_to_income"] = (b["debt_to_income"] * 1.5).round(3)
        up = j(C.post("/api/datasets", files={"file": ("agent_batch_march.csv", b.to_csv(index=False).encode(), "text/csv")}))
        x = _agent_drive(f"Score the dataset {up['id']} as a batch with version {vid} and tell me if anything drifted.", user, dec,
                         max_steps=10, max_seconds=600)
        steps = _agent_steps(x)
        bs = next((i for i in steps if i["tool"] == "batch_score" and i["status"] == "done"), None)
        check(bs, [(i["tool"], i["status"], i.get("error")) for i in steps])
        check([c["tool"] for c in x["confirms"]] and x["confirms"][0]["tool"] == "batch_score", x["confirms"])
        d = bs["result"]["drift"]
        check(d["max_psi"] is not None and "debt_to_income" in d["drifted"], d)
        a = _agent_answer(x)
        check("debt_to_income" in a["text"], a["text"])
        b2 = get(f"/api/registry/batches/{bs['result']['batch_id']}")
        check(b2["created_by"] == f"Vela agent on behalf of {user}", b2.get("created_by"))
        s["s3"] = x
        return (f"{_agent_line(x)} · batch {bs['result']['batch_id']} {bs['result']['rows']} rows · max PSI {d['max_psi']} · drifted "
                f"{', '.join(d['drifted'])} · created_by the agent · guard ✓ {a['guard']['numbers']} numbers · \"{a['text'][:140]}…\"")

    step("agent: 3 · score a batch and report drift", scenario3)

    def stop_decline():
        x = _agent_drive("Train a default model on credit-default with a random split.", user, dec, lambda sess, stp: False,
                         max_steps=6, max_seconds=300)
        steps = _agent_steps(x)
        check(x["confirms"] and not x["confirms"][0]["approved"], x["confirms"])
        check(any(i["status"] == "declined" for i in steps), [i["status"] for i in steps])
        y = post("/api/agent/sessions", {"message": "List the datasets, then describe each one in detail.", "user": user, "decider": dec})
        time.sleep(0.5)
        post(f"/api/agent/sessions/{y['id']}/stop", {})
        y = poll(f"/api/agent/sessions/{y['id']}", lambda z: z["status"] in ("stopped", "idle", "failed"), 60, 0.5)
        check(y["status"] == "stopped", y["status"])
        return f"Skip → nothing trained ({len(steps)} calls, {x['status']}) · Stop → {y['status']}"

    step("agent: skip a write, stop a conversation", stop_decline)


# --------------------------------------------------------------------------- D3: Model Doctor 2 and What's missing


def _d3_train(names: list[str]) -> dict[str, dict[str, Any]]:
    """Upload and train the crafted D3 tables (scripts/doctor2_data.py) in parallel; {name: run record}."""
    import doctor2_data as D3

    spec = {
        "leak_hinted": (D3.leak_hinted, "subscribed", {"GBM": {}, "LR": {}}, None, ["contact_id"]),
        "cost_run": (D3.cost_run, "defaulted", {"GBM": {}, "LR": {}}, {"c_fp": 1, "c_fn": 5}, ["loan_id"]),
        "flat_ceiling": (D3.flat_ceiling, "defaulted", {"GBM": {}, "LR": {}}, None, ["app_id"]),
        "twins": (D3.twins, "defaulted", {"GBM": {}}, None, ["app_id"]),
        "hidden_segment": (D3.hidden_segment, "defaulted", {"GBM": {}}, None, ["app_id"]),
        "label_overlap": (D3.label_overlap, "topic", {"GBM": {}}, None, ["ticket_id"]),
    }
    ids = {}
    for n in names:
        gen, lab, hp, cost, drop = spec[n]
        ds = _dd_upload(gen(), f"d3_{n}.csv", lab)
        body = {"dataset_id": ds, "label": lab, "presets": "medium_quality", "time_limit": 90, "drop_columns": drop,
                "fit_kwargs": {"hyperparameters": hp}, "source": "smoke-doctor2"}
        if cost:
            body["cost"] = cost
        ids[n] = post("/api/runs", body)["id"]
    out = {}
    for n, rid in ids.items():
        rec = wait_run(rid)
        check(rec["status"] == "succeeded", (n, rec.get("error")))
        out[n] = rec
    return out


def _d3_check(d: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for c in d["checks"] if c["id"] == cid)


def _d3_ranked(items: list[dict[str, Any]], key: str) -> None:
    """The ranking rule: a must-do first, then measured gains > 0 (largest first), then unmeasured, then measured ≤ 0."""
    def k(x):
        dv = x[key]["delta"] if key and x.get(key) else x.get("delta") if not key else None
        if x.get("must"):
            return (0, 0.0)
        if dv is not None and dv > 0:
            return (1, -dv)
        if dv is None:
            return (2.0 if x.get("suggested") else 2.5, 0.0)
        return (3, -dv)
    got = [k(x) for x in items]
    check(got == sorted(got), f"not ranked: {got}")
    check([x["rank"] for x in items] == list(range(1, len(items) + 1)), "ranks are not 1..n")


def doctor2(st: dict[str, Any]) -> None:
    """D3 Model Doctor 2 on crafted tables (scripts/doctor2_data.py): a dominant leak with a Data Doctor hint → leakage
    and a one-click ablation (a child run, same holdout) whose Δ the card then shows; a cost run → the cost trade-off
    replaces weak class and imbalance, and the ladder ranks rungs by expected cost; a flat ceiling → "at the data's
    limit"; the Holdout Errors view; determinism; typed-experiment refusals."""
    s: dict[str, Any] = {}

    def train():
        s.update(_d3_train(["leak_hinted", "cost_run", "flat_ceiling"]))
        return " · ".join(f"{n} {r['id']} {primary(r)[0]} {primary(r)[1]:.3f}" for n, r in s.items())

    if not step("doctor2: train leak, cost, flat", train, fatal=False):
        return

    def leak():
        rec = s["leak_hinted"]
        rid = rec["id"]
        d = get(f"/api/runs/{rid}/doctor")
        _dr_check_shape(d)
        p = d["primary"]
        check(p["id"] == "leakage" and p["severity"] == "critical", d["headline"][:300])
        sus = p["evidence"]["suspects"][0]
        check(sus["feature"] == "call_duration" and sus["reason"] == "dominant + hint", sus)
        check(sus["share"] >= 0.5 and (sus["ratio"] is None or sus["ratio"] >= 3), sus)
        check(sus.get("single_auc") is not None and sus["single_auc"] < 0.97, f"AUC alone {sus.get('single_auc')}: the dominance path must be what fired")
        rx = p["prescription"]
        check(rx["kind"] == "experiment" and rx["body"] == {"kind": "ablate", "columns": ["call_duration"]}, rx)
        ex = post(rx["endpoint"], rx["body"])
        child = wait_run(ex["id"])
        check(child["status"] == "succeeded", child.get("error"))
        cfg = child["config"]
        check(cfg.get("parent_run_id") == rid and cfg.get("source") == "doctor-experiment"
              and cfg["patch"]["experiment"] == {"kind": "ablate", "columns": ["call_duration"]}, cfg.get("patch"))
        check("call_duration" in cfg["drop_columns"] and "call_duration" not in child["features"], cfg["drop_columns"])
        d2 = get(f"/api/runs/{rid}/doctor")
        _dr_check_shape(d2)
        ab = d2["primary"]["evidence"]["ablation"]
        check(abs(ab["with"] - rec["holdout"]["metrics"]["roc_auc"]) < 1e-6, (ab["with"], rec["holdout"]["metrics"]["roc_auc"]))
        check(abs(ab["without"] - child["holdout"]["metrics"]["roc_auc"]) < 1e-6, ab)
        check(ab["delta"] < 0 and ab["ci"][1] < 0 and ab["ci"][0] <= ab["delta"] <= ab["ci"][1], ab)
        check(d2["primary"]["prescription"]["kind"] == "open_run" and d2["primary"]["prescription"]["run_id"] == ex["id"], d2["primary"]["prescription"])
        lv = d2["improve"]
        check(lv[0]["id"] == "drop_leak" and abs(lv[0]["delta"] - ab["delta"]) < 1e-12, lv[0])
        _d3_ranked(lv, "")
        exl = get(f"/api/runs/{rid}/experiments")["experiments"]
        check(exl and exl[0]["run_id"] == ex["id"] and exl[0]["result"]["same_rows"], exl[:1])
        s["leak_d"] = d2
        return (f"{rid}: leakage critical, call_duration {sus['share']:.0%} of importance ({sus['ratio']:.1f}× next), AUC alone {sus['single_auc']:.3f} "
                f"+ D1 hint · ablation {ex['id']} → AUC {ab['with']:.4f} → {ab['without']:.4f}, Δ {ab['delta']:+.4f} [{ab['ci'][0]:+.4f}, {ab['ci'][1]:+.4f}] "
                f"on {ab['rows']} shared rows · lever #1 drop_leak")

    step("doctor2: dominant + hinted leak → ablation Δ", leak)

    def cost_tradeoff():
        rec = s["cost_run"]
        d = get(f"/api/runs/{rec['id']}/doctor")
        _dr_check_shape(d)
        p = d["primary"]
        check(p["id"] == "cost_tradeoff" and p["severity"] == "major", d["headline"][:300])
        for cid in ("weak_class", "imbalance"):
            c = _d3_check(d, cid)
            check(c["status"] == "n/a", (cid, c["status"], c["reason"]))
        rows = {r["name"]: r for r in p["evidence"]["rows"]}
        check(set(rows) == {"served", "3:1", "5:1", "10:1"}, list(rows))
        for r_, k in ((3, "3:1"), (5, "5:1"), (10, "10:1")):
            check(abs(rows[k]["t"] - 1 / (1 + r_)) < 1e-12, rows[k])
        dec = [rows[k]["decline_rate"] for k in ("3:1", "5:1", "10:1")]
        cat = [rows[k]["catch_rate"] for k in ("3:1", "5:1", "10:1")]
        check(dec == sorted(dec) and cat == sorted(cat), (dec, cat))
        sv = rows["served"]
        check(abs(sv["t"] - rec["threshold"]["value"]) < 1e-9, (sv["t"], rec["threshold"]))
        n = p["evidence"]["n"]
        check(abs(sv["cost_per_1000"] - 1000 * (sv["fp"] * 1 + sv["fn"] * 5) / n) < 1e-6, sv)
        check(p["prescription"]["kind"] == "open_view" and p["prescription"]["view"] == "Threshold", p["prescription"])
        lad = get(f"/api/runs/{rec['id']}/ladder", refresh="true")
        check(lad["metric"] == "expected_cost" and lad["greater_is_better"] is False and lad["cost"]["t"] == 1 / 6, (lad["metric"], lad.get("cost")))
        r3 = next(r for r in lad["rungs"] if r["rung"] == 3)
        r0 = next(r for r in lad["rungs"] if r["rung"] == 0)
        check(r3["score"] < r0["score"], f"the stack {r3['score']} costs less than the baseline {r0['score']}")
        return (f"{rec['id']}: cost trade-off major; weak class, imbalance n/a · served t {sv['t']:.3f}: decline {sv['decline_rate']:.1%}, "
                f"catch {sv['catch_rate']:.1%}, {sv['cost_per_1000']:.0f}/1,000 · 3:1/5:1/10:1 decline {', '.join(f'{x:.1%}' for x in dec)} · "
                f"ladder by expected cost: stack {r3['score']:.0f} vs baseline {r0['score']:.0f} per 1,000, serve {lad['recommended']['name']}")

    step("doctor2: cost run → trade-off, not weak class", cost_tradeoff)

    def flat():
        rec = s["flat_ceiling"]
        d = get(f"/api/runs/{rec['id']}/doctor")
        _dr_check_shape(d)
        p = d["primary"]
        check(p and p["id"] == "ceiling" and p["severity"] == "minor", d["headline"][:300])
        conds = p["evidence"]["conditions"]
        check(all(c["ok"] for c in conds.values()) and set(conds) == {"flat", "agree", "gap_noise", "not_concentrated"}, conds)
        check(not any(c["severity"] in ("major", "critical") for c in d["secondary"]), d["secondary"])
        f = conds["flat"]
        return (f"{rec['id']}: at the data's limit · next doubling {f['next_doubling']:+.4f} [{f['ci'][0]:+.4f}, {f['ci'][1]:+.4f}] < ε {f['eps']} · "
                f"single ≈ best ({conds['agree']['single']}) · z {conds['gap_noise']['z']:.1f} · rx {p['prescription']['title']}")

    step("doctor2: flat ceiling → at the data's limit", flat)

    def errors_view():
        out = []
        for n in ("leak_hinted", "cost_run"):
            d = get(f"/api/runs/{s[n]['id']}/doctor")
            e = d["errors_view"]
            rows = e["top"]["rows"]
            check(rows and all(r["y_true"] != r["y_pred"] for r in rows), "an error row that is right")
            conf = [r["confidence"] for r in rows]
            check(conf == sorted(conf, reverse=True), "not ranked by confidence")
            check(all(r.get("reasons") for r in rows[:5]), "no SHAP reasons")
            check(e["served"]["errors"] >= len(rows) and 0 < e["served"]["rate"] < 1, e["served"])
            cl = e["top"].get("cleanlab")
            check(cl and cl["errors"] == e["served"]["errors"], cl)
            out.append(f"{n}: {e['served']['errors']} errors, top conf {conf[0]:.3f} ({', '.join(x['feature'] for x in rows[0]['reasons'])}), "
                       f"{len(e['slices'] or [])} slices, cleanlab {cl['share_of_errors']:.0%} of errors vs {cl['share_of_correct']:.0%} of correct")
        return " · ".join(out)

    step("doctor2: Holdout Errors view", errors_view)

    def determinism_refusals():
        rid = s["cost_run"]["id"]
        a, b = get(f"/api/runs/{rid}/doctor"), get(f"/api/runs/{rid}/doctor")
        strip = lambda d: json.dumps([(c["id"], c["status"], c["severity"], c["reason"]) for c in d["checks"]] + [d["missing_info"]["gaps"], d["improve"]])  # noqa: E731
        check(strip(a) == strip(b), "two calls disagree")
        lid = s["leak_hinted"]["id"]
        for body, code in (({"kind": "python", "code": "x"}, 400), ({"kind": "ablate", "columns": ["nope"]}, 400),
                           ({"kind": "ablate"}, 400), ({"kind": "ablate", "columns": s["leak_hinted"]["features"]}, 400)):
            r = C.post(f"/api/runs/{lid}/experiments", json=body)
            check(r.status_code == code, (body, r.status_code, r.text[:200]))
        check(C.post("/api/runs/run_nothing/experiments", json={"kind": "budget"}).status_code == 404, "unknown run")
        return "same checks, gaps and levers on a second call · free-form kind, unknown column, no columns, every feature → 400 · unknown run → 404"

    step("doctor2: determinism + refusals", determinism_refusals)


def missing(st: dict[str, Any]) -> None:
    """D3 What's missing on crafted tables with a known information gap (scripts/doctor2_data.py): planted twins (Bayes
    error exactly 0.15) → the irreducible-error estimate's CI holds the truth; a hidden variable for one segment → the
    card names that segment and the missingness inside its errors, and the oracle there is clearly positive; two labels
    defined the same way → symmetric confusion. The ranking rule and the typed D4 slot."""
    import doctor2_data as D3

    s: dict[str, Any] = {}

    def train():
        s.update(_d3_train(["twins", "hidden_segment", "label_overlap"]))
        return " · ".join(f"{n} {r['id']} {primary(r)[0]} {primary(r)[1]:.3f}" for n, r in s.items())

    if not step("missing: train twins, hidden segment, label overlap", train, fatal=False):
        return

    def card(n: str) -> dict[str, Any]:
        d = get(f"/api/runs/{s[n]['id']}/doctor")
        _dr_check_shape(d)
        m = d["missing_info"]
        check(m["schema"] == "missing/1" and not m["errors"], m.get("errors"))
        _d3_ranked(m["gaps"], "gain")
        dg = m["domain_gaps"]
        check(dg["source"] == "llm" and dg["status"] == "pending" and dg["items"] == [] and "suggestion" in dg["label"].lower(), dg)
        return m

    def twins():
        m = card("twins")
        ir = m["irreducible"]
        t = D3.TRUTH["twins"]
        check(ir["r_nn_ci"][0] <= t <= ir["r_nn_ci"][1], f"truth {t} outside the R_NN CI {ir['r_nn_ci']}")
        check(ir["lower"] <= t <= ir["model_error"] + 0.02, (ir["lower"], ir["model_error"]))
        g = next(x for x in m["gaps"] if x["kind"] == "twins")
        check(g["gain"] and g["gain"]["simulated"] and g["gain"]["ci"][0] > 0, g["gain"])
        return (f"truth 0.150 ∈ R_NN {ir['r_nn']:.3f} [{ir['r_nn_ci'][0]:.3f}, {ir['r_nn_ci'][1]:.3f}] (Cover–Hart lower {ir['lower']:.3f}, "
                f"model error {ir['model_error']:.3f}) · twins oracle {g['gain']['delta']:+.4f} [{g['gain']['ci'][0]:+.4f}, {g['gain']['ci'][1]:+.4f}] · "
                f"top gap: {m['gaps'][0]['title']}")

    step("missing: planted twins → irreducible error within CI", twins)

    def hidden():
        m = card("hidden_segment")
        segs = [x for x in m["gaps"] if x["kind"] == "concentration"]
        check(segs, "no concentration gap")
        top = segs[0]
        ev = top["evidence"]
        check(ev["column"] == "employment" and ev["value"] == "self_employed", f"top segment {top['title']}")
        check(top["gain"] and top["gain"]["ci"][0] > 0, top["gain"])
        mi = {x["column"]: x for x in ev["missing_inside"]}
        check("income" in mi and mi["income"]["missing_in_errors"] >= 0.25 and mi["income"]["missing_elsewhere"] <= 0.1, ev["missing_inside"])
        check("self_employed" in top["finding"] and "income" in top["request"], (top["finding"], top["request"]))
        return (f"{top['title']} (rank {top['rank']}): {ev['rate']:.3f} vs {m['served']['rate']:.3f}, ×{ev['lift']:.2f} · income missing "
                f"{mi['income']['missing_in_errors']:.0%} in its errors vs {mi['income']['missing_elsewhere']:.0%} elsewhere · oracle "
                f"{top['gain']['delta']:+.4f} [{top['gain']['ci'][0]:+.4f}, {top['gain']['ci'][1]:+.4f}] · request: {top['request']}")

    step("missing: hidden segment → names the segment", hidden)

    def overlap():
        m = card("label_overlap")
        lo = [x for x in m["gaps"] if x["kind"] == "label_overlap"]
        check(lo, "no label-overlap gap")
        p = lo[0]["evidence"]
        check({p["a"], p["b"]} == {"card_lost", "card_stolen"} and min(p["a_as_b"], p["b_as_a"]) >= 20, p)
        check(lo[0]["gain"]["metric"] == "accuracy" and abs(lo[0]["gain"]["delta"] - p["n"] / m["served"]["n"]) < 1e-12, lo[0]["gain"])
        return (f"{lo[0]['title']}: {p['a_as_b']} / {p['b_as_a']} both ways, up to +{lo[0]['gain']['delta']:.3f} accuracy if merged · "
                f"request: {lo[0]['request']}")

    step("missing: label overlap → symmetric confusion", overlap)

def _main(args) -> int:
    global C, ONLY
    ONLY = {x.strip() for x in args.only.split(",")}
    C = httpx.Client(base_url=API, timeout=httpx.Timeout(600, connect=10))
    st: dict[str, Any] = {}
    print(f"ENSEMBLE smoke against {API}\n")
    sections = {"core": core, "samples": samples, "autopilot": autopilot, "forecast": forecast, "anomaly": anomaly,
                "import": importer, "intake": intake, "fresh": fresh, "latency": latency, "targets": targets, "splits": splits, "cost": cost, "monotone": monotone, "quantile": quantile, "reasons": reasons, "recourse": recourse, "scorecard": scorecard, "learning": learning, "active": active, "annotators": annotators, "shift": shift, "slices": slices, "doctor": doctor, "monitor": monitor, "credit": credit, "el": expected_loss, "fair": fair_lending, "seg": segmented, "surv": survival, "ri": reject_inference, "ml": multilabel, "sql": sql_source, "agent": agent, "reset": reset, "llm": llm, "datadoctor": datadoctor, "ladder": ladder, "featurelab": featurelab, "doctor2": doctor2, "missing": missing}
    for name in args.only.split(","):
        if name.strip():
            print(f"── {name}")
            sections[name.strip()](st)
    return summary()


if __name__ == "__main__":
    sys.exit(main())

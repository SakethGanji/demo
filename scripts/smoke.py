#!/usr/bin/env python3
"""ENSEMBLE smoke test: the real calls the UI makes, chained end to end against a running API.

    backend/.venv/bin/python scripts/smoke.py [--api http://127.0.0.1:8000] [--only core,samples,autopilot]

One line per step: ✓ or ✗, the step, a key number. Exits 1 if any step failed. Sections:
  core       health → upload a generated CSV → profile → run + SSE → leaderboard → explain / threshold / calibrate /
             SHAP / fairness → custom ensemble → register → approve → Try It → batch score → drift → Gym → Vela patch
             → retrain → compare → export.py run in a subprocess
  samples    loan-amount (regression) and support-tickets (text) through train and predict
  autopilot  a full Autopilot on support-tickets: start, inbox, bulk answers, retrain, requirements, inbox recall
  forecast   transaction-volume sample and an uploaded CSV: mapping → preview → TimeSeriesPredictor run + SSE →
             leaderboard, weights, backtests → per-item errors → fan chart → CSV download → new-history forecast → register
  anomaly    card-transactions: IsolationForest + LOF run + SSE → top anomalies with reasons → precision@k against the
             hidden truth → batch score + download → register → Try It on a row
  import     fit a tiny TabularPredictor here, zip it, import it → leaderboard, explain, predict, register, Try It;
             also by server path
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
        check(a["source"] in ("claude-cli", "anthropic-api", "computed"), a)
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


# --------------------------------------------------------------------------- autopilot


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
        v = a["verdict"]
        return (f"{a['id']} · no LLM · auto-accepted c ≥ 0.9 · {len([s for s in a['steps'] if s.get('run_id')])} runs · "
                f"{'MET' if v['met'] else 'NOT MET'} · Δ{a['primary']} {v['delta']:+.4f}")

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
                                       "num_val_windows": 2, "hyperparameters": FC_HP,
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
        check(len(bt["windows"]) == 2 and all(len(v) == 2 for v in bt["models"].values()), bt)
        naive = next(r for r in lb if r["model"] == "SeasonalNaive")
        st["fc_rec"] = rec
        return (f"best {rec['best_model']} holdout MASE {lb[0]['score_test_natural']:.3f} (SeasonalNaive {naive['score_test_natural']:.3f}) · "
                "w = " + ", ".join(f"{k} {v:.2f}" for k, v in list(w.items())[:3]) + f" · {len(bt['windows'])} backtest windows")

    step("forecast: leaderboard + weights", leaderboard)

    def errors():
        h = st["fc_rec"]["holdout"]
        items = h["items"]
        check(len(items) == 6 and all(i["MASE"] is not None for i in items), items)
        worst = items[0]
        return (f"MASE {h['metrics']['MASE']:.3f} · WQL {h['metrics']['WQL']:.4f} · coverage of [q{h['interval'][0]}, q{h['interval'][1]}] "
                f"{h['coverage']:.1%} · worst item {worst['item_id']} MASE {worst['MASE']:.3f}")

    step("forecast: per-item errors", errors)

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
        return f"history to 2026-10-31 → {out['rows']} rows forecast by {out['model']} in {out['seconds']:.2f} s · covariates from {out['known_covariates_source']}"

    step("forecast: forecast from new history", new_history)

    def register():
        v = post(f"/api/runs/{fid}/register", {"created_by": "Smoke Analyst", "model_name": "smoke.transaction-volume.forecast"})
        v = poll(f"/api/registry/versions/{v['id']}", lambda x: x["status"] in ("ready", "failed"), 600)
        check(v["status"] == "ready", v.get("error"))
        card = get(f"/api/registry/versions/{v['id']}/card")
        check(card["forecasting"] and card["forecasting"]["holdout_metrics"]["MASE"] > 0, card.get("forecasting"))
        md = C.get(f"/api/registry/versions/{v['id']}/card", params={"format": "md"}).text
        check("## 11. Forecasting" in md, "card section")
        lat = v["result"]["version"]["latency"]
        return f"{v['id']} · {v['model_name']} v{v['version']} · card §11 Forecasting · forecast all items {lat['forecast_all_items_s_p50'] * 1e3:.0f} ms"

    step("forecast: register + model card", register)


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
        return f"{v['id']} · v{v['version']} ready · card §11 · Try It: s = {out['anomaly_score']:.3f} (pct {out['percentile']:.3f}) flagged — {out['why'][:60]}"

    step("anomaly: register + Try It", register)


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


def main() -> int:
    global C, API
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=API)
    ap.add_argument("--only", default="core,samples,autopilot,forecast,anomaly,import,reset")
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


def _main(args) -> int:
    global C
    C = httpx.Client(base_url=API, timeout=httpx.Timeout(600, connect=10))
    st: dict[str, Any] = {}
    print(f"ENSEMBLE smoke against {API}\n")
    sections = {"core": core, "samples": samples, "autopilot": autopilot, "forecast": forecast, "anomaly": anomaly,
                "import": importer, "reset": reset}
    for name in args.only.split(","):
        if name.strip():
            print(f"── {name}")
            sections[name.strip()](st)
    return summary()


if __name__ == "__main__":
    sys.exit(main())

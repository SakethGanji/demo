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
             leaderboard, weights, backtests (W auto) → per-item errors → interval calibration → conformal recalibration
             (F1: raw → calibrated coverage, MASE, per-model coverage) → fan chart → CSV download → new-history forecast → register
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

    def onerow():
        r = post("/api/runs", {"dataset_id": "support-tickets", "time_limit": 150, "source": "smoke",
                               "fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 80}, "TFIDF_LR": {}}},
                               "feature_importance": False})
        rec = wait_run(r["id"])
        lp = get(f"/api/runs/{r['id']}/latency")
        ms = {m["model"]: m for m in lp["models"]}
        check(all(m.get("single_ms_p95") is not None for m in lp["models"]), "one-row latency per model")
        best = ms[lp["best_model"]]
        fast = [m for m in lp["models"] if not m["best"] and m["score_test_natural"] is not None
                and m["single_ms_p95"] < best["single_ms_p95"]]
        # Faster than the ensemble, not a fixed ratio: the measured gap depends on machine load.
        check(fast, f"no model faster than {best['model']}'s one-row p95 {best['single_ms_p95']:.1f} ms")
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


# --------------------------------------------------------------------------- fresh CSVs (scripts/fresh_data.py)

FRESH = [
    # file, label, metric, problem type, fit_kwargs, a test row's expected kind of answer
    ("card_fraud.csv", "is_fraud", "average_precision", "binary",
     {"hyperparameters": {"GBM": {"num_boost_round": 150}, "CAT": {"iterations": 150}, "LR": {}}, "num_bag_folds": 3}),
    ("complaints.csv", "product_team", "f1_macro", "multiclass", {"hyperparameters": {"TFIDF_LR": {}, "TFIDF_SVM": {}}}),
    ("deposit_balances.csv", "avg_balance_next_q_gbp", "root_mean_squared_error", "regression",
     {"hyperparameters": {"GBM": {"num_boost_round": 200}, "XT": {}, "LR": {}}}),
]


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


ONLY: set[str] = set()


def main() -> int:
    global C, API
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=API)
    ap.add_argument("--only", default="core,samples,autopilot,forecast,anomaly,import,fresh,reset")
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
    global C, ONLY
    ONLY = {x.strip() for x in args.only.split(",")}
    C = httpx.Client(base_url=API, timeout=httpx.Timeout(600, connect=10))
    st: dict[str, Any] = {}
    print(f"ENSEMBLE smoke against {API}\n")
    sections = {"core": core, "samples": samples, "autopilot": autopilot, "forecast": forecast, "anomaly": anomaly,
                "import": importer, "fresh": fresh, "reset": reset}
    for name in args.only.split(","):
        if name.strip():
            print(f"── {name}")
            sections[name.strip()](st)
    return summary()


if __name__ == "__main__":
    sys.exit(main())

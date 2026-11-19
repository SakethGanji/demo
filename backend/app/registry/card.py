"""The model card of a registered version: one structured document (JSON for the page) rendered to Markdown and
standalone HTML for download.

It is written for a bank's model risk reviewer, in the spirit of SR 11-7 (Federal Reserve / OCC guidance on model
risk management): what the model is for and not for, the data and its provenance, how it was built, how it was
validated and with what uncertainty, its limits, and who approved it. It documents; it does not replace independent
validation, and it says so.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..splits import holdout_mask

DISCLAIMER = ("This card is generated from the run's recorded artefacts to support model risk review. It documents the "
              "model's development and the developer's own testing; it does not replace independent model validation, "
              "ongoing monitoring or the effective challenge expected under SR 11-7 / OCC 2011-12.")

TEMPLATE_FIELDS = [
    ("purpose", "Business purpose", "What decision does this model support, for which product and portfolio?"),
    ("intended_users", "Intended users", "Who uses the output, and how (score, flag, ranking)?"),
    ("intended_use", "Intended use", "The population and conditions the model is approved for."),
    ("out_of_scope", "Out of scope", "Populations, products or decisions the model must not be used for."),
    ("limitations", "Known limitations and assumptions", "Data gaps, proxies, stability assumptions, expert overrides."),
    ("monitoring", "Monitoring plan", "Metrics, frequency, owners and triggers (e.g. PSI > 0.25 on any input, AUC drop > 0.03)."),
    ("owner", "Model owner", "Name and team accountable for the model."),
    ("validator", "Independent validator", "Name and team performing independent validation."),
    ("risk_tier", "Model risk tier", "Tier per the bank's model risk policy (e.g. 1 = high materiality)."),
]

STAGE_RULES = "None → Staging → Production → Archived. Production requires an approval; rejecting archives a live version."


def _fmt(v: Any, d: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not np.isfinite(v):
            return "—"
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        if v != 0 and abs(v) < 1e-3:
            return f"{v:.3g}"
        return f"{v:.{d}f}".rstrip("0").rstrip(".")
    return str(v)


def _bytes(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return str(n)


def fit_call(resolved: dict[str, Any], cls: str = "TabularPredictor") -> str:
    pk = dict(resolved.get("predictor_kwargs") or {})
    fk = {k: v for k, v in (resolved.get("fit_kwargs") or {}).items() if k != "callbacks"}

    def r(v: Any) -> str:
        return json.dumps(v, default=str).replace("null", "None").replace("true", "True").replace("false", "False")

    a = ", ".join(f"{k}={r(v)}" for k, v in pk.items() if v is not None)
    b = ",\n    ".join(f"{k}={r(v)}" for k, v in fk.items())
    if cls == "IsolationForest":
        return f"pre = ColumnTransformer(...)  # median impute + one-hot\nforest = IsolationForest({', '.join(f'{k}={r(v)}' for k, v in (resolved.get('iforest_kwargs') or {}).items())})\nforest.fit(pre.fit_transform(X))"
    return f"predictor = {cls}({a}, path=...)\npredictor.fit(\n    train_data,\n    {b},\n)"


def build_card(version: dict[str, Any], run: dict[str, Any], dataset: dict[str, Any] | None,
               audit: list[dict[str, Any]], batches: list[dict[str, Any]], reasons: dict[str, Any] | None = None) -> dict[str, Any]:
    res = run.get("result") or {}
    cfg = run["config"]
    vres = version.get("result") or {}
    ver = vres.get("version") or {}
    src = vres.get("source") or {}
    hashes = vres.get("hashes") or {}
    data = res.get("data") or {}
    label = run["label"]

    # Label balance on the rows the model saw (training) and on the holdout.
    balance = None
    try:
        df = pd.read_csv(cfg["csv_path"], usecols=[label])
        splits = json.loads(Path(cfg["splits_path"]).read_text())
        is_h = holdout_mask(splits, len(df))
        y = df[label]
        if res.get("problem_type") in ("binary", "multiclass"):
            tr = y[~is_h].dropna().astype(str).value_counts()
            ho = y[is_h].dropna().astype(str).value_counts()
            balance = [{"class": k, "train": int(tr.get(k, 0)), "train_share": float(tr.get(k, 0) / tr.sum()),
                        "holdout": int(ho.get(k, 0)), "holdout_share": float(ho.get(k, 0) / max(ho.sum(), 1))} for k in tr.index]
        else:
            yy = pd.to_numeric(y, errors="coerce").dropna()
            balance = {"mean": float(yy.mean()), "std": float(yy.std()), "min": float(yy.min()), "median": float(yy.median()),
                       "max": float(yy.max())}
    except Exception:  # noqa: BLE001
        pass

    prof = (dataset or {}).get("profile") or {}
    cols = prof.get("columns") or []
    features = ver.get("features") or res.get("features") or []
    feat_rows = [{"name": c["name"], "type": (cfg.get("column_types") or {}).get(c["name"], c["type"]),
                  "missing_share": c.get("missing_share"), "unique": c.get("unique")}
                 for c in cols if c["name"] in features]
    fi = res.get("feature_importance")
    from ..engine.explain import importance_with_ci

    fi = importance_with_ci(fi) or []
    ens = res.get("ensemble") or {}
    fairness = res.get("fairness_reports") or {}
    thr = res.get("threshold")
    user = version.get("card") or {}
    reviews = [a for a in audit if a["action"] in ("approved", "rejected")]
    li = vres.get("infer_limit")

    return {
        "title": f"Model card · {version['model_name']} v{version['version']}",
        "model_name": version["model_name"], "version": version["version"], "version_id": version["id"],
        "generated_note": DISCLAIMER,
        "summary": {
            "stage": version["stage"], "approval": version["approval"], "registered_at": version["created_at"],
            "registered_by": version["created_by"], "model_sha256": version.get("model_hash"),
            "problem_type": res.get("problem_type"), "label": label, "positive_class": res.get("positive_class"),
            "eval_metric": (res.get("eval_metric") or {}).get("name"),
            "engine": {"forecast": "AutoGluon TimeSeriesPredictor", "anomaly": "scikit-learn IsolationForest (+ LOF)"}.get(
                cfg.get("kind"), "AutoGluon TabularPredictor (imported)" if cfg.get("import") else "AutoGluon TabularPredictor"),
            "best_model": ver.get("best_model"), "source_run": run["id"], "source_run_name": run["name"],
            "mlflow": vres.get("mlflow"), "path": version.get("path"),
        },
        "data": {
            "dataset_id": run["dataset_id"], "name": (dataset or {}).get("name"), "source": (dataset or {}).get("source"),
            "filename": (dataset or {}).get("filename"), "rows": data.get("rows") or (dataset or {}).get("rows"),
            "columns": prof.get("n_cols"), "features": len(features), "train_rows": data.get("train_rows"),
            "holdout_rows": data.get("holdout_rows"), "dropped_missing_label": data.get("dropped_missing_label"),
            "dropped_columns": data.get("dropped_columns"), "sha256": hashes.get("data_sha256"),
            "splits_sha256": hashes.get("splits_sha256"), "label_balance": balance, "feature_table": feat_rows,
            "split": {"forecast": "the last H steps of every series held out; backtest windows before them for model selection",
                      "anomaly": "unsupervised: every row is scored; no labels and no holdout"}.get(cfg.get("kind")) or "a fixed stratified 20% holdout (seed 42) never seen in training or model selection; "
                     "AutoGluon validates on its own split of the remaining rows (or out-of-fold when bagging)"
                     + (f"; drawn for y = {cfg['split']['target']} (split {cfg['split']['key']}, {cfg['split']['strategy']})"
                        if cfg.get("kind") in (None, "tabular") and cfg.get("split") else ""),
            "missing_cells_share": (prof.get("missing") or {}).get("share"), "duplicates": (prof.get("duplicates") or {}).get("rows"),
            "warnings": prof.get("warnings") or [],
        },
        "training": {
            "call": fit_call(res.get("resolved") or {}, {"forecast": "TimeSeriesPredictor", "anomaly": "IsolationForest"}.get(cfg.get("kind"), "TabularPredictor")), "resolved": res.get("resolved"),
            "notes": (res.get("resolved") or {}).get("notes") or [], "fit_seconds": res.get("fit_seconds"),
            "n_models": len(res.get("leaderboard") or []), "presets": cfg.get("presets"), "time_limit": cfg.get("time_limit"),
            "registration": {k: version["options"].get(k) for k in ("refit_full", "infer_limit", "infer_limit_batch_size", "clone_for_deployment")},
            "refit": vres.get("refit"),
        },
        "validation": {
            "holdout_ci": ver.get("holdout_ci"), "source_holdout_ci": src.get("holdout_ci"), "threshold": thr,
            "version_threshold": ver.get("decision_threshold"), "confusion_matrix": ver.get("confusion_matrix"),
            "classes": ver.get("classes"),
            "validation_score": next((r.get("score_val_natural") for r in res.get("leaderboard") or []
                                      if r.get("model") == res.get("best_model")), None),
        },
        "importance": {"features": fi[:20], "method": "permutation importance on the holdout (AutoGluon feature_importance), 95% t-intervals"},
        "fairness": {"reports": fairness,
                     "note": None if fairness else "Not computed. Open Explain → Fairness for a group column (e.g. region, age band) to add it."},
        "ensemble": {"model": ens.get("model"), "weights": ens.get("weights"), "stack_level": ens.get("stack_level"),
                     "version_best": ver.get("best_model"), "dependencies": ver.get("dependencies"),
                     "leaderboard": [{k: r.get(k) for k in ("model", "family", "stack_level", "score_val_natural", "score_test_natural", "fit_time", "pred_time_test")}
                                     for r in (res.get("leaderboard") or [])[:12]]},
        "deployment": {"source": {k: src.get(k) for k in ("best_model", "size_bytes", "n_models", "latency")},
                       "version": {k: ver.get(k) for k in ("best_model", "size_bytes", "n_models", "latency")},
                       "tradeoff": vres.get("tradeoff"), "infer_limit": li},
        "use": {"fields": [{"key": k, "label": lab, "hint": hint, "value": user.get(k, "")} for k, lab, hint in TEMPLATE_FIELDS]},
        "monitoring": {"batches": [{"id": b["id"], "created_at": b["created_at"], "n_rows": b.get("n_rows"),
                                    "max_psi": ((b.get("result") or {}).get("drift") or {}).get("max_psi"),
                                    "prediction_psi": (((b.get("result") or {}).get("drift") or {}).get("prediction") or {}).get("psi"),
                                    "counts": ((b.get("result") or {}).get("drift") or {}).get("counts")}
                                   for b in batches if b["status"] == "succeeded"][:10],
                       "bands": "PSI < 0.10 stable · 0.10–0.25 watch · > 0.25 shift"},
        "approval": {"stage": version["stage"], "approval": version["approval"], "rules": STAGE_RULES,
                     "reviews": reviews, "audit": audit},
        "kind": cfg.get("kind") or "tabular",
        "forecasting": _forecast_section(cfg, res) if cfg.get("kind") == "forecast" else None,
        "anomaly_detection": _anomaly_section(cfg, res) if cfg.get("kind") == "anomaly" else None,
        "imported": res.get("imported"),
        "constraints": _monotone_section(cfg, res, vres),
        "quantile": _quantile_section(cfg, res, vres),
        "reasons": _reasons_section(reasons, batches),
        "scorecard": _scorecard_section(res),
        "diagnostics": _diagnostics_section(cfg, res),
    }


def _scorecard_section(res: dict[str, Any]) -> dict[str, Any] | None:
    """F11: the interpretable benchmark: the scorecard's holdout AUC next to the served model's, and ΔAUC with its CI."""
    sc = res.get("scorecard") or {}
    if not sc.get("available"):
        return None
    h = sc["holdout"]
    t = sc["table"]
    return {"model": sc["model"], "served": h["served"], "scorecard": h["scorecard"], "delta_auc": h["delta_auc"],
            "n_features": t["n_features"], "scaling": {k: t["scaling"][k] for k in ("pdo", "base_score", "base_odds")},
            "iv": [{"feature": f["feature"], "iv": f["iv"], "in_model": f["in_model"]} for f in sorted(t["features"], key=lambda f: -f["iv"])],
            "cost": sc.get("cost"),
            "method": "binning on the training rows (monotone event rate, ≥ min share per bin) → WoE → logistic "
                      "regression (coefficients ≤ 0) → points; refitted on all training rows; ΔAUC = AUC(served) − "
                      "AUC(scorecard) on the holdout, paired bootstrap 95% CI"}


def _reasons_section(r: dict[str, Any] | None, batches: list[dict[str, Any]]) -> dict[str, Any] | None:
    """F10: how adverse decisions are explained: the rule at the served threshold, the attribution method, the
    mapping table, the stability check and the latest batch's reason-code summary."""
    if not r:
        return None
    last = next((((b.get("result") or {}).get("summary") or {}).get("reasons") for b in batches
                 if b["status"] == "succeeded" and (((b.get("result") or {}).get("summary") or {}).get("reasons"))), None)
    st = r.get("stability")
    return {"adverse_class": r["adverse_class"], "positive_class": r["positive_class"], "threshold": r["threshold"],
            "top_k": r["top_k"], "mapping": r["mapping"], "constraints": r.get("constraints") or {},
            "stability": st, "method": (st or {}).get("method") or (last or {}).get("attribution"),
            "reference": r.get("reference", "approved"),
            "last_batch": {k: (last or {}).get(k) for k in ("n_adverse", "rows_per_s", "methods", "additivity_max",
                                                             "signs_consistent", "codes_per_row", "top_codes", "monotone")} if last else None,
            "rule": f"adverse ⇔ the served prediction is {r['adverse_class']} at t = {r['threshold']:.4g}; reasons = the top "
                    f"{r['top_k']} features with s·φⱼ > 0, ranked by s·φⱼ (φⱼ: SHAP value of P({r['positive_class']}), "
                    f"s = {'+1' if r['adverse_class'] == r['positive_class'] else '−1'})",
            "monotone_note": "A monotone-constrained feature (F8) pushes toward the adverse class only through background "
                             "rows it lies beyond in its constrained direction, so its statement has a fixed direction; "
                             "and xⱼ beyond every background value forces the sign of φⱼ (checked on every explained row)."}


def _quantile_section(cfg: dict[str, Any], res: dict[str, Any], vres: dict[str, Any]) -> dict[str, Any] | None:
    """F9: a quantile model's levels, and the served version's pinball loss, calibration per level and interval
    coverage on the holdout, raw and conformally calibrated."""
    if res.get("problem_type") != "quantile":
        return None
    q = vres.get("quantile") or res.get("quantile") or {}

    def ivs(e):
        return [{k: iv.get(k) for k in ("lo", "hi", "nominal", "coverage", "coverage_ci", "mean_width", "below_lo", "above_hi")}
                for iv in (e or {}).get("intervals") or []]

    conf = q.get("conformal") or {}
    return {"levels": q.get("levels") or res.get("quantile_levels"), "centre": q.get("centre"),
            "served_model": q.get("model"), "served": q.get("served"), "pinball": (q.get("holdout") or {}).get("pinball"),
            "per_level": [{k: x.get(k) for k in ("tau", "pinball", "below", "below_ci")} for x in (q.get("holdout") or {}).get("levels") or []],
            "intervals": ivs(q.get("holdout")), "raw": ivs(q.get("raw")), "calibrated": ivs(q.get("calibrated")),
            "crossing_share": (q.get("holdout") or {}).get("crossing_share"),
            "conformal": {"applied": conf.get("applied"), "offsets": conf.get("offsets"), "source": conf.get("source")},
            "requested_calibrate": (cfg.get("quantile") or {}).get("calibrate"),
            "method": "AutoGluon quantile regression (pinball loss ρ_τ(y, q) = max(τ(y − q), (τ − 1)(y − q))); "
                      "coverage = share of holdout rows inside [q̂_lo, q̂_hi]; F̂(τ) = share at or below q̂_τ; "
                      "conformal recalibration is AutoGluon's CQR (q̃_τ = q̂_τ + c_τ)"}


def _monotone_section(cfg: dict[str, Any], res: dict[str, Any], vres: dict[str, Any]) -> dict[str, Any] | None:
    """F8: the monotone constraints the model was trained under, and the ICE / PD check of the served model."""
    m = cfg.get("monotone")
    if not m:
        return None
    rep = res.get("monotone") or {}
    v = vres.get("monotone") or {}
    cols = []
    for c, s in m["constraints"].items():
        chk = (v.get("columns") or {}).get(c) or {}
        cols.append({"column": c, "direction": "increasing" if s > 0 else "decreasing", "sign": s,
                     "features": ((rep.get("columns") or {}).get(c) or {}).get("features"),
                     "status": ((rep.get("columns") or {}).get(c) or {}).get("status"),
                     "monotone": chk.get("monotone"), "pd_monotone": chk.get("pd_monotone"),
                     "curves_violating": chk.get("curves_violating"), "n_curves": chk.get("n_curves"),
                     "grid_points": len(chk.get("grid") or []), "max_violation": chk.get("max_violation"),
                     "pd_range": chk.get("pd_range")})
    return {"columns": cols, "compliant_only": m.get("compliant_only"), "served_model": v.get("served_model"),
            "monotone": v.get("monotone"), "unconstrained_models": rep.get("unconstrained_models"),
            "method": "LightGBM / XGBoost / CatBoost monotone_constraints mapped onto each model's training columns "
                      "(ensemble_models.monotone); stack inputs constrained to +1; checked with ICE curves of the "
                      "served model on raw holdout rows over a grid of the column's quantiles (ε = 1e-9)"}


def _forecast_section(cfg: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    d, h, fc, bt = res.get("data") or {}, res.get("holdout") or {}, res.get("forecast") or {}, res.get("backtests") or {}
    best = res.get("best_model")
    return {
        "setup": {"items": d.get("items"), "freq": d.get("freq"), "seasonal_period_m": d.get("seasonal_period"),
                  "prediction_length_H": d.get("prediction_length"), "start": d.get("start"), "end": d.get("end"),
                  "known_covariates": d.get("known_covariates"), "static_features": d.get("static_features"),
                  "past_covariates": d.get("past_covariates"), "eval_metric": (res.get("eval_metric") or {}).get("name"),
                  "metric_formula": (res.get("eval_metric") or {}).get("formula"),
                  "quantile_levels": (res.get("resolved") or {}).get("predictor_kwargs", {}).get("quantile_levels"),
                  "presets": cfg.get("presets"), "num_val_windows": cfg.get("num_val_windows")},
        "validation": "the last H steps of every series held out (never seen in fit or model selection); "
                      f"{len(bt.get('windows') or [])} backtest window(s) inside the training range for model selection",
        "holdout_metrics": h.get("metrics"), "coverage": h.get("coverage"), "interval": h.get("interval"),
        "items": (h.get("items") or [])[:20],
        "backtests": {"windows": bt.get("windows"), "best": (bt.get("models") or {}).get(best)},
        "forecast": fc, "ensemble": res.get("ensemble"),
        "interval_calibration": _fc_calibration(h),
        "limitations": ["Known covariates past the data come from the file's future rows; when absent they repeat the "
                        "value one season back (said in forecast.known_covariates_source).",
                        "Structural breaks (new branches, policy changes) after the training window are not anticipated."],
    }


def _fc_calibration(h: dict[str, Any]) -> dict[str, Any] | None:
    """The widest central interval's holdout coverage before and after conformal recalibration, with c_τ."""
    def main(c):
        i = ((c or {}).get("intervals") or [None])[0]
        return None if not i else {k: i[k] for k in ("lo", "hi", "nominal", "inside", "n", "empirical", "ci")}
    if not h.get("calibration"):
        return None
    conf = h.get("conformal") or {}
    return {"recalibrated": bool(h.get("recalibrated")), "raw": main(h.get("calibration_raw") or h.get("calibration")),
            "calibrated": main(h.get("calibration")) if h.get("recalibrated") else None,
            "method": conf.get("method"), "scope": conf.get("scope"), "factors": conf.get("factors"),
            "n_scores": conf.get("n_scores"), "windows": conf.get("windows"), "error": conf.get("error")}


def _anomaly_section(cfg: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    a = res.get("anomaly") or {}
    return {
        "method": "IsolationForest: s(x,n) = 2^(−E[h(x)]/c(n)), c(n) = 2H(n−1) − 2(n−1)/n; flagged when s ≥ the (1 − contamination) quantile",
        "params": (res.get("resolved") or {}).get("iforest_kwargs"), "contamination": a.get("contamination"),
        "threshold": a.get("threshold"), "n_flagged": a.get("n_flagged"), "psi": a.get("psi"), "c_n": a.get("c_n"),
        "n_trees": a.get("n_trees"), "lof": a.get("lof"), "features": res.get("features"),
        "truth_check": a.get("truth"),
        "validation": "Unsupervised: no labels. On the sample only, precision@k against planted anomalies that the model never saw.",
        "limitations": ["Scores rank unusualness, not fraud: a flag needs human review.",
                        "The threshold is a share of rows (contamination), not a calibrated probability."],
    }


# --------------------------------------------------------------------------- Markdown


def _ci(m: dict[str, Any]) -> str:
    if m.get("ci_low") is None:
        return _fmt(m.get("value"))
    return f"{_fmt(m['value'])} [{_fmt(m['ci_low'])}, {_fmt(m['ci_high'])}]"


def to_markdown(c: dict[str, Any]) -> str:
    s, d, t, v = c["summary"], c["data"], c["training"], c["validation"]
    L: list[str] = [f"# {c['title']}", "", f"> {c['generated_note']}", ""]
    L += ["## 1. Summary", "", "| Field | Value |", "| --- | --- |"]
    for k, lab in (("stage", "Stage"), ("approval", "Approval"), ("problem_type", "Problem type"), ("label", "Label"),
                   ("positive_class", "Positive class"), ("eval_metric", "Selection metric"), ("engine", "Engine"),
                   ("best_model", "Model served"), ("source_run", "Source run"), ("registered_by", "Registered by"),
                   ("registered_at", "Registered at"), ("model_sha256", "Model SHA-256")):
        L.append(f"| {lab} | `{s.get(k)}` |" if k in ("model_sha256", "source_run", "best_model") else f"| {lab} | {s.get(k) if s.get(k) is not None else '—'} |")
    if s.get("mlflow"):
        L.append(f"| MLflow | model `{s['mlflow'].get('name')}` version {s['mlflow'].get('version')}, run `{s['mlflow'].get('run_id')}` |")
    L += ["", "## 2. Intended use and limitations", "", "_To be completed by the model owner._", ""]
    for f in c["use"]["fields"]:
        L += [f"**{f['label']}.** {f['value'] or '_' + f['hint'] + '_'}", ""]
    L += ["## 3. Data", "", "| Field | Value |", "| --- | --- |"]
    for k, lab in (("name", "Dataset"), ("source", "Source"), ("filename", "File"), ("rows", "Rows"), ("columns", "Columns"),
                   ("features", "Features used"), ("train_rows", "Training rows"), ("holdout_rows", "Holdout rows"),
                   ("dropped_missing_label", "Rows dropped (no label)"), ("sha256", "File SHA-256"), ("splits_sha256", "Split SHA-256")):
        L.append(f"| {lab} | {_fmt(d.get(k)) if isinstance(d.get(k), (int, float)) else (d.get(k) or '—')} |")
    L += ["", f"Split: {d['split']}.", ""]
    if isinstance(d.get("label_balance"), list):
        L += ["**Label balance**", "", "| Class | Train | Share | Holdout | Share |", "| --- | ---: | ---: | ---: | ---: |"]
        for b in d["label_balance"]:
            L.append(f"| {b['class']} | {b['train']:,} | {b['train_share']:.2%} | {b['holdout']:,} | {b['holdout_share']:.2%} |")
    elif isinstance(d.get("label_balance"), dict):
        b = d["label_balance"]
        L.append(f"Label: mean {_fmt(b['mean'])}, σ {_fmt(b['std'])}, range [{_fmt(b['min'])}, {_fmt(b['max'])}].")
    if d.get("feature_table"):
        L += ["", "**Features**", "", "| Feature | Type | Missing | Distinct |", "| --- | --- | ---: | ---: |"]
        for f in d["feature_table"]:
            L.append(f"| {f['name']} | {f['type']} | {(f['missing_share'] or 0):.1%} | {f['unique']:,} |")
    for w in d.get("warnings") or []:
        L.append(f"- Data warning: {w}")
    L += ["", "## 4. Development", "", "The resolved AutoGluon call (every argument as passed):", "", "```python", t["call"], "```", ""]
    for n in t["notes"]:
        L.append(f"- {n}")
    r = t["registration"]
    L += ["", f"Registration options: refit_full = {r.get('refit_full')}, infer_limit = {r.get('infer_limit')} s/row, "
              f"clone_for_deployment = {r.get('clone_for_deployment')}.", ""]
    e = c["ensemble"]
    if e.get("weights"):
        L += ["**Stacked ensemble** ŷ(x) = Σₘ wₘ fₘ(x)", "", "| Base model fₘ | Weight wₘ |", "| --- | ---: |"]
        for m, w in e["weights"].items():
            L.append(f"| {m} | {w:.3f} |")
        L.append("")
    L += [f"Model served by this version: `{e.get('version_best')}` (with {len(e.get('dependencies') or [])} model(s) it depends on).", ""]
    L += ["## 5. Validation (holdout)", "", "Point estimates on the untouched holdout with 95% percentile-bootstrap intervals.", "",
          "| Metric | Definition | Version | Source run |", "| --- | --- | ---: | ---: |"]
    hc = (v.get("holdout_ci") or {}).get("metrics") or {}
    sc = (v.get("source_holdout_ci") or {}).get("metrics") or {}
    for k, m in hc.items():
        L.append(f"| {k} | {m['formula']} | {_ci(m)} | {_ci(sc[k]) if k in sc else '—'} |")
    if v.get("threshold"):
        th = v["threshold"]
        L += ["", f"Decision threshold t = {_fmt(th.get('value'))} ({th.get('source')}{', ' + th['metric'] if th.get('metric') else ''}); "
                  f"predict positive when p̂ > t.", ""]
    qc = c.get("quantile")
    if qc:
        L += ["**Quantile regression (range)**", "",
              f"Levels τ = {', '.join(f'{t:g}' for t in qc.get('levels') or [])}; the point prediction is the centre "
              f"level q̂_{(qc.get('centre') or 0.5):g}. Holdout pinball loss {_fmt(qc.get('pinball'))}; the version serves "
              f"the {qc.get('served')} quantiles (conformal offsets {'applied' if (qc.get('conformal') or {}).get('applied') else 'not applied'}). "
              f"Method: {qc['method']}.", "",
              "| Level τ | Pinball loss | F̂(τ) = share of y ≤ q̂_τ | 95% CI |", "| ---: | ---: | ---: | --- |"]
        for r in qc.get("per_level") or []:
            ci = r.get("below_ci") or [None, None]
            L.append(f"| {r['tau']:g} | {_fmt(r.get('pinball'))} | {_fmt(r.get('below'))} | [{_fmt(ci[0])}, {_fmt(ci[1])}] |")
        L += ["", "| Interval | Nominal | Coverage (served) | 95% CI | Raw | Calibrated | Mean width |", "| --- | ---: | ---: | --- | ---: | ---: | ---: |"]
        raw = {(r["lo"], r["hi"]): r for r in qc.get("raw") or []}
        cal = {(r["lo"], r["hi"]): r for r in qc.get("calibrated") or []}
        for r in qc.get("intervals") or []:
            ci = r.get("coverage_ci") or [None, None]
            k = (r["lo"], r["hi"])
            L.append(f"| [q̂_{r['lo']:g}, q̂_{r['hi']:g}] | {_fmt(r.get('nominal'))} | {_fmt(r.get('coverage'))} | [{_fmt(ci[0])}, {_fmt(ci[1])}] "
                     f"| {_fmt((raw.get(k) or {}).get('coverage'))} | {_fmt((cal.get(k) or {}).get('coverage'))} | {_fmt(r.get('mean_width'))} |")
        if qc.get("crossing_share"):
            L.append(f"\nQuantile crossing (q̂ out of order) on {qc['crossing_share']:.2%} of holdout rows.")
        L.append("")
    mc = c.get("constraints")
    if mc:
        L += ["**Monotone constraints**", "", f"Method: {mc['method']}.", "",
              "| Feature | Direction | Served model monotone (ICE) | Curves violating | PD range |", "| --- | --- | --- | ---: | ---: |"]
        for r in mc["columns"]:
            L.append(f"| {r['column']} | {r['direction']} | {'yes' if r['monotone'] else 'no' if r['monotone'] is not None else '—'} "
                     f"| {r['curves_violating'] if r['curves_violating'] is not None else '—'} / {r['n_curves'] or '—'} | {_fmt(r['pd_range'])} |")
        if mc.get("unconstrained_models"):
            L += ["", f"Unconstrained models in the run: {', '.join(mc['unconstrained_models'])}."]
        L.append("")
    rc = c.get("reasons")
    if rc:
        L += ["**Reason codes (adverse action)**", "", f"Rule: {rc['rule']}.", "",
              "Reference applicants (SHAP background): " + ("training rows the served model approves (φ₀ = E[f | approved])"
                                                           if rc.get("reference") == "approved" else "all training rows") + ".", ""]
        m = rc.get("method") or {}
        if m:
            L += [f"Attribution: {m.get('name')} · background B = {m.get('background')}"
                  + (f" · {m.get('coalitions')} coalitions" if m.get("coalitions") else "") + f" · exact: {'yes' if m.get('exact') else 'no (sampled)'}.", ""]
        L += ["| Code | Feature | Constraint | Statement (high / category) | Statement (low) | Missing |", "| --- | --- | --- | --- | --- | --- |"]
        for r in rc["mapping"]:
            con = {1: "↑", -1: "↓"}.get(r.get("constraint") or 0, "")
            L.append(f"| {r['code']} | {r['feature']} | {con} | {r.get('high') or r.get('category') or ''} | {r.get('low') or ''} | {r.get('missing') or ''} |")
        st = rc.get("stability")
        if st and not st.get("error"):
            L += ["", f"Stability: on {st['rows']} adverse holdout decisions × {st['repeats']} draws of noise ε = {st['noise']:g}·σ, "
                      f"the top-{rc['top_k']} set is unchanged in {_fmt(st.get('same_set'))}, the first reason in {_fmt(st.get('same_top1'))}, "
                      f"mean Jaccard {_fmt(st.get('mean_jaccard'))}; decisions flipped {_fmt(st.get('decision_flips'))}."]
            vr = st.get("vs_reference")
            if vr:
                L += ["", f"Estimator check: against the same game with {vr['coalitions']} coalitions the fast codes cite the same set in "
                          f"{_fmt(vr['same_set'])} of rows (mean overlap {_fmt(vr['mean_overlap'])}); {_fmt(vr['cited_positive'])} of cited "
                          f"features have reference φ pushing toward adverse."]
        else:
            L += ["", "Stability: not run yet (Registry → version → Reason codes → Run the stability check)."]
        lb = rc.get("last_batch")
        if lb:
            L += ["", f"Latest batch: {lb.get('n_adverse')} adverse rows explained at {_fmt(lb.get('rows_per_s'))} rows/s; "
                      f"codes consistent with the SHAP signs: {'yes' if lb.get('signs_consistent') else 'no'}."]
        L += ["", rc["monotone_note"], ""]
    scd = c.get("scorecard")
    if scd:
        d = scd["delta_auc"]
        sc_ = scd["scaling"]
        L += ["**Scorecard benchmark (interpretable challenger)**", "", f"Method: {scd['method']}.", "",
              "| Model | Holdout AUC | Gini | KS |", "| --- | ---: | ---: | ---: |",
              f"| {scd['served']['model']} (served) | {_fmt(scd['served']['auc'])} | {_fmt(scd['served']['gini'])} | {_fmt(scd['served']['ks'])} |",
              f"| Scorecard ({scd['n_features']} features; PDO {sc_['pdo']:g}, {sc_['base_score']:g} at {sc_['base_odds']:g}:1) "
              f"| {_fmt(scd['scorecard']['auc'])} | {_fmt(scd['scorecard']['gini'])} | {_fmt(scd['scorecard']['ks'])} |", "",
              f"ΔAUC (served − scorecard) = {_fmt(d['delta'])}, 95% CI [{_fmt(d['ci'][0])}, {_fmt(d['ci'][1])}] "
              f"({d['B']} paired bootstrap resamples; P(Δ ≤ 0) = {_fmt(d['p_le_0'])}).", "",
              "IV per feature: " + ", ".join(f"{x['feature']} {x['iv']:.3f}{'' if x['in_model'] else ' (out)'}" for x in scd["iv"]) + "."]
        if scd.get("cost"):
            cc = scd["cost"]
            L += ["", f"Expected cost per 1,000 decisions (c_FP = {cc['c_fp']:g}, c_FN = {cc['c_fn']:g}): served "
                      f"{_fmt(cc['served_per_1000'])}, scorecard {_fmt(cc['scorecard_per_1000'])}."]
        L.append("")
    L += _diagnostics_md(c.get("diagnostics"))
    L += ["## 6. Feature importance", "", "| Feature | Importance | 95% CI | p |", "| --- | ---: | --- | ---: |"]
    for f in c["importance"]["features"][:15]:
        L.append(f"| {f['feature']} | {_fmt(f.get('importance'))} | [{_fmt(f.get('ci_low'))}, {_fmt(f.get('ci_high'))}] | {_fmt(f.get('p_value'))} |")
    L += ["", "## 7. Fairness", ""]
    if c["fairness"]["reports"]:
        for g, rep in c["fairness"]["reports"].items():
            gaps = rep.get("gaps") or {}
            L.append(f"- Group `{g}`: " + ", ".join(f"{k} = {_fmt(val)}" for k, val in gaps.items() if isinstance(val, (int, float))))
    else:
        L.append(c["fairness"]["note"])
    dp = c["deployment"]
    L += ["", "## 8. Deployment trade-off (measured)", "", "| | Source run | This version |", "| --- | ---: | ---: |"]
    for k, lab, f in (("best_model", "Model", str), ("n_models", "Models on disk", str), ("size_bytes", "Size on disk", _bytes)):
        L.append(f"| {lab} | {f(dp['source'].get(k))} | {f(dp['version'].get(k))} |")
    for k, lab in (("us_per_row_p50", "Batch µs/row p50"), ("us_per_row_p95", "Batch µs/row p95"),
                   ("single_ms_p50", "Single-row ms p50"), ("single_ms_p95", "Single-row ms p95")):
        L.append(f"| {lab} | {_fmt((dp['source'].get('latency') or {}).get(k), 3)} | {_fmt((dp['version'].get('latency') or {}).get(k), 3)} |")
    L += ["", "## 9. Monitoring", "", f"Bands: {c['monitoring']['bands']}.", ""]
    for b in c["monitoring"]["batches"]:
        L.append(f"- Batch `{b['id']}` ({b['created_at']}): {b['n_rows']:,} rows, max feature PSI {_fmt(b['max_psi'])}, prediction PSI {_fmt(b['prediction_psi'])}")
    a = c["approval"]
    L += ["", "## 10. Approval", "", f"Stage **{a['stage']}**, approval **{a['approval']}**. {a['rules']}", "",
          "| When (UTC) | Who | Action | Stage | Comment | Model SHA-256 |", "| --- | --- | --- | --- | --- | --- |"]
    for x in a["audit"]:
        st = f"{x.get('from_stage') or ''} → {x.get('to_stage') or ''}" if x.get("to_stage") else ""
        L.append(f"| {x['ts']} | {x['actor']} | {x['action']} | {st} | {(x.get('comment') or '').replace('|', '/')} | `{(x.get('model_hash') or '')[:12]}` |")
    if c.get("forecasting"):
        f = c["forecasting"]
        st = f["setup"]
        L += ["", "## 11. Forecasting", "", f"ŷ_{{t+h|t}} for h = 1…H, H = {st['prediction_length_H']}, freq `{st['freq']}`, "
              f"season m = {st['seasonal_period_m']}, {st['items']} series from {st['start']} to {st['end']}.", "",
              f"Metric {st['eval_metric']}: {st.get('metric_formula') or ''}", "", f"Validation: {f['validation']}.", "",
              "| Holdout metric | Value |", "| --- | ---: |"]
        for k, val in (f.get("holdout_metrics") or {}).items():
            L.append(f"| {k} | {_fmt(val)} |")
        if f.get("coverage") is not None:
            L.append(f"| coverage of [q{f['interval'][0]}, q{f['interval'][1]}] | {_fmt(f['coverage'])} |")
        ic = f.get("interval_calibration") or {}
        if ic.get("raw"):
            r, c = ic["raw"], ic.get("calibrated")
            L += ["", f"Interval calibration, [q{r['lo']}, q{r['hi']}] (nominal {_fmt(r['nominal'])}) on the holdout: "
                  f"AutoGluon's quantiles cover {r['inside']}/{r['n']} = {_fmt(r['empirical'])}"
                  + (f"; after conformal recalibration {c['inside']}/{c['n']} = {_fmt(c['empirical'])} "
                     f"(95% Wilson CI {_fmt(c['ci'][0])}–{_fmt(c['ci'][1])}). q̃_τ = m + c_τ (q̂_τ − m) with "
                     + ", ".join(f"c({k}) = {_fmt(v, 3)}" for k, v in (ic.get('factors') or {}).items())
                     + f", fitted on {ic.get('n_scores')} backtest points ({ic.get('scope')})." if c else
                     ". Not recalibrated" + (f" ({ic['error']})" if ic.get("error") else "") + ".")]
        L += ["", "| Item | MASE | WQL | sMAPE | coverage |", "| --- | ---: | ---: | ---: | ---: |"]
        for it in f.get("items") or []:
            L.append(f"| {it['item_id']} | {_fmt(it.get('MASE'))} | {_fmt(it.get('WQL'))} | {_fmt(it.get('sMAPE'))} | {_fmt(it.get('coverage'))} |")
        ens = (f.get("ensemble") or {}).get("weights") or {}
        if ens:
            L += ["", "Ensemble: ŷ = Σ wₘ ŷₘ with " + ", ".join(f"w({k}) = {_fmt(v, 3)}" for k, v in ens.items()) + "."]
        L += ["", "Limitations:"] + [f"- {x}" for x in f["limitations"]]
    if c.get("anomaly_detection"):
        a = c["anomaly_detection"]
        L += ["", "## 11. Anomaly detection", "", a["method"] + ".", "",
              f"n = ψ = {a.get('psi')} rows per tree, c(n) = {_fmt(a.get('c_n'))}, {a.get('n_trees')} trees, "
              f"contamination {_fmt(a.get('contamination'))}, threshold s* = {_fmt(a.get('threshold'))}, {a.get('n_flagged')} rows flagged.", "",
              f"Validation: {a['validation']}"]
        tc = a.get("truth_check") or {}
        if tc:
            L += ["", "| k | hits | precision@k | recall |", "| ---: | ---: | ---: | ---: |"]
            for r in tc.get("precision_at_k") or []:
                L.append(f"| {r['k']} | {r['hits']} | {_fmt(r['precision'])} | {_fmt(r['recall'])} |")
            L.append(f"\nROC AUC on the planted rows {_fmt(tc.get('roc_auc'))}, AP {_fmt(tc.get('average_precision'))}.")
        L += ["", "Limitations:"] + [f"- {x}" for x in a["limitations"]]
    if c.get("imported"):
        im = c["imported"]
        L += ["", "## 11. Imported predictor", "", f"Imported from {im.get('source_kind')} `{im.get('source')}`, AutoGluon "
              f"{im.get('autogluon_version')}, {im.get('n_models')} models. Not refit. Holdout rows may have been in its "
              "training data, so holdout scores may be optimistic."]
    L += ["", "Sign-off", "", "| Role | Name | Date | Signature |", "| --- | --- | --- | --- |",
          "| Model owner | | | |", "| Independent validator | | | |", "| Model risk management | | | |", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------- HTML


CSS = """body{font:14px/1.55 'IBM Plex Sans',system-ui,sans-serif;color:#1f2937;max-width:960px;margin:32px auto;padding:0 24px}
h1{font-size:24px;border-bottom:2px solid #0b1f3a;padding-bottom:8px}h2{font-size:17px;margin-top:28px;color:#0b1f3a;border-bottom:1px solid #e5e7eb;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}th,td{border:1px solid #e5e7eb;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f3f4f6}td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}code,pre{font-family:'IBM Plex Mono',monospace;font-size:12px}
pre{background:#0b1f3a;color:#e5e7eb;padding:12px;border-radius:4px;overflow:auto}blockquote{border-left:3px solid #d97706;margin:12px 0;padding:6px 12px;background:#fffbeb}
em{color:#6b7280}"""


def _inline(s: str) -> str:
    out = html.escape(s)
    import re

    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])_([^_]+)_(?!\w)", r"<em>\1</em>", out)
    return out


def to_html(c: dict[str, Any]) -> str:
    """The Markdown rendered by a small converter (headings, tables, code, quotes, lists, paragraphs)."""
    md = to_markdown(c).splitlines()
    out: list[str] = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(c['title'])}</title>"
                      f"<style>{CSS}</style></head><body>"]
    i = 0
    while i < len(md):
        ln = md[i]
        if ln.startswith("```"):
            j = i + 1
            block = []
            while j < len(md) and not md[j].startswith("```"):
                block.append(md[j])
                j += 1
            out.append("<pre>" + html.escape("\n".join(block)) + "</pre>")
            i = j + 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(md) and md[i].startswith("|"):
                rows.append([x.strip() for x in md[i].strip("|").split("|")])
                i += 1
            head, align, body = rows[0], rows[1] if len(rows) > 1 else [], rows[2:]
            right = [a.endswith(":") for a in align]
            out.append("<table><thead><tr>" + "".join(f"<th{' class=r' if right[k:k+1] == [True] else ''}>{_inline(h)}</th>" for k, h in enumerate(head)) + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td{' class=r' if right[k:k+1] == [True] else ''}>{_inline(x)}</td>" for k, x in enumerate(r)) + "</tr>")
            out.append("</tbody></table>")
            continue
        if ln.startswith("# "):
            out.append(f"<h1>{_inline(ln[2:])}</h1>")
        elif ln.startswith("## "):
            out.append(f"<h2>{_inline(ln[3:])}</h2>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
        elif ln.startswith("- "):
            out.append(f"<ul><li>{_inline(ln[2:])}</li></ul>")
        elif ln.strip():
            out.append(f"<p>{_inline(ln)}</p>")
        i += 1
    out.append("</body></html>")
    return "\n".join(out)


def _diagnostics_section(cfg: dict[str, Any], res: dict[str, Any]) -> dict[str, Any] | None:
    """F19: what the source run's later checks found, as the run payload holds their summaries (F6 split, F7 cost,
    F12 learning curve, F15 shift, F16 weak slices, F14 annotators, F17 Model Doctor). Each item is None when the check
    has not been run; the card says where to run it. Nothing is recomputed here."""
    if cfg.get("kind") not in (None, "tabular"):
        return None
    sp = cfg.get("split") or {}
    spp = sp.get("params") or {}
    cost = cfg.get("cost")
    lc = res.get("learning_curve") or {}
    v = lc.get("verdict") or {}
    sh = res.get("shift") or {}
    sl = res.get("slices") or {}
    an = (res.get("annotations") or {}).get("summary") or {}
    doc = res.get("doctor") or {}
    x2 = ((v.get("extrapolation") or {}).get("x2") or {})
    out = {
        "split": {"strategy": sp.get("strategy") or "random-stratified", "key": sp.get("key"),
                  "group_column": spp.get("group_column") or sp.get("group_column"),
                  "time_column": spp.get("time_column") or sp.get("time_column"), "cutoff": sp.get("cutoff")},
        "cost": ({"kind": cost.get("kind"), "c_fp": cost.get("c_fp"), "c_fn": cost.get("c_fn"), "g_tp": cost.get("g_tp")}
                 if isinstance(cost, dict) else None),
        "learning_curve": ({"verdict": v.get("label"), "reason": v.get("reason"), "metric": v.get("metric"),
                            "slope": (v.get("slope_per_doubling") or {}).get("value"),
                            "slope_ci": (v.get("slope_per_doubling") or {}).get("ci"),
                            "x2_score": x2.get("score"), "x2_ci": x2.get("ci")} if v else None),
        "shift": ({"reference": sh.get("reference"), "new": sh.get("new"), "auc": sh.get("auc"), "ci": sh.get("ci"),
                   "verdict": sh.get("verdict"), "top": (sh.get("top_features") or [{}])[0].get("feature")}
                  if sh.get("status") == "succeeded" and sh.get("auc") is not None else None),
        "slices": ({"loss": sl.get("loss"), "n_tested": sl.get("n_tested"), "n_weak": sl.get("n_weak"), "alpha": sl.get("alpha"),
                    "top": [{"label": t["label"], "n": t["n"], "delta": t["delta"], "ci": t["ci"], "q": t["q"]}
                            for t in (sl.get("top") or [])[:3]]} if sl else None),
        "annotators": ({"worst": (an.get("worst_annotator") or {}).get("name"),
                        "fleiss_kappa": ((an.get("agreement") or {}).get("fleiss_kappa") or {}).get("value"),
                        "changed_vs_given": (an.get("consensus") or {}).get("changed_vs_given"),
                        "mean_quality": (an.get("consensus") or {}).get("mean_quality")} if an else None),
        "doctor": ({"headline": doc.get("headline"), "computed_at": doc.get("computed_at"),
                    "primary": doc.get("primary"), "secondary": [c.get("title") for c in doc.get("secondary") or []],
                    "missing": doc.get("missing")} if doc else None),
    }
    return out


def _diagnostics_md(d: dict[str, Any] | None) -> list[str]:
    """F19: the diagnostics block of section 5 in the exported card."""
    if not d:
        return []
    sp = d["split"]
    L = ["### Diagnostics", "", "| Check | Result |", "| --- | --- |"]
    where = ", ".join(x for x in (sp.get("group_column") and f"group `{sp['group_column']}`",
                                  sp.get("time_column") and f"time `{sp['time_column']}`") if x)
    name = {"random-stratified": "random (stratified)", "time": "out-of-time", "time-group": "out-of-time + group"}.get(sp["strategy"], sp["strategy"])
    L.append(f"| Split (F6) | {name}{f' ({where})' if where else ''} |")
    if d.get("cost"):
        c = d["cost"]
        L.append(f"| Business cost (F7) | c_FP = {c.get('c_fp')}, c_FN = {c.get('c_fn')}, g_TP = {c.get('g_tp') or 0}; eval_metric expected_cost |")
    lc = d.get("learning_curve")
    L.append(f"| Learning curve (F12) | {lc['verdict']}: {lc['reason']} |" if lc else "| Learning curve (F12) | not run |")
    sh = d.get("shift")
    L.append(f"| Shift (F15) | {sh['reference']} vs {sh['new']}: AUC {_fmt(sh['auc'], 3)} ({sh['verdict']}), top `{sh['top']}` |" if sh else "| Shift (F15) | not run |")
    sl = d.get("slices")
    L.append(f"| Weak slices (F16) | {sl['n_weak']} of {sl['n_tested']} tested ({sl['loss']}, α = {sl['alpha']})"
             + (f"; first: {sl['top'][0]['label']} (n = {sl['top'][0]['n']}, Δ = {_fmt(sl['top'][0]['delta'], 3)})" if sl["top"] else "") + " |"
             if sl else "| Weak slices (F16) | not run |")
    an = d.get("annotators")
    if an:
        L.append(f"| Annotators (F14) | least reliable {an.get('worst')}; Fleiss κ {_fmt(an.get('fleiss_kappa'), 3)}; consensus changes {an.get('changed_vs_given')} labels |")
    doc = d.get("doctor")
    L.append(f"| Model Doctor (F17) | {doc['headline']} |".replace("\n", " ") if doc else "| Model Doctor (F17) | not run (Explain → Model Doctor) |")
    return L + [""]

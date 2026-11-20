"""D2: the model ladder. One training request, four rungs, one holdout, one rule.

| Rung | Model                                   | Where it comes from                                         |
|------|-----------------------------------------|-------------------------------------------------------------|
| 0    | Baseline (prior / mean)                 | computed: the training rows' class shares or mean           |
| 1    | Scorecard (binary) / linear (others)    | the run's own SCORECARD (F11) or LR model, trained in the run |
| 2    | Single gradient-boosted model           | the best level-1 LightGBM / CatBoost / XGBoost of the run    |
| 3    | AutoGluon's best (the stack)            | `predictor.model_best`                                       |

No extra fit: every servable rung is a model of the run's own leaderboard, so `serve_model` registers it (F19).

Scores are on the run's fixed holdout: AUC for binary runs (the ranking quality a decision threshold then uses), the
run's eval metric otherwise. Each rung's gap to the best is a **paired bootstrap** over holdout rows (the same draw for
every rung). **Rule:** serve the simplest servable rung whose gap's interval includes 0. On small data (D1's
`sample_size` finding, medium or high) the interval is 99 % instead of 95 %: a more complex rung has to win more clearly
before it is worth its cost. Latency is the measured one-row p95 (pipeline pruned to what the model reads, as served),
size is the bytes on disk of the model and every model it needs.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

GBM_FAMILIES = ("GBM", "CAT", "XGB")
FAMILY_NAMES = {"GBM": "LightGBM", "CAT": "CatBoost", "XGB": "XGBoost", "RF": "random forest", "XT": "extra trees",
                "NN_TORCH": "neural net", "FASTAI": "neural net", "LR": "linear model", "KNN": "k-nearest neighbours",
                "SCORECARD": "scorecard"}
B_AUC, B_OTHER = 1000, 300


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    from .scorecard import auc

    return auc(y, p)


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def _family(lb_row: dict[str, Any]) -> str | None:
    from .catalogue import family_for_class

    return family_for_class(lb_row.get("child_model_type")) or family_for_class(lb_row.get("model_type"))


def pick_rungs(predictor) -> list[dict[str, Any]]:
    """The servable rungs (1–3) as models of the run, or a reason when a rung is missing."""
    lb = predictor.leaderboard(extra_info=True, silent=True).to_dict(orient="records")
    rows = [r for r in lb if r.get("can_infer", True) and not str(r["model"]).endswith("_FULL")]
    l1 = [r for r in rows if int(r.get("stack_level") or 1) == 1 and "WeightedEnsemble" not in str(r["model"])]
    for r in rows:
        r["_family"] = _family(r)
    sv = lambda r: -math.inf if r.get("score_val") is None or pd.isna(r.get("score_val")) else float(r["score_val"])  # noqa: E731
    out: list[dict[str, Any]] = []
    pt = predictor.problem_type
    # rung 1: the interpretable one
    if pt == "binary":
        from .scorecard import scorecard_models

        names = scorecard_models(predictor)
        r1 = next((r for r in rows if r["model"] in names), None)
        out.append({"rung": 1, "key": "scorecard", "name": "Scorecard", "model": r1["model"] if r1 else None,
                    "why": "points per bin, readable by a reviewer; the regulator's baseline",
                    "missing": None if r1 else "no SCORECARD model in this run (switched off, or skipped by AutoGluon)"})
    else:
        lr = sorted([r for r in l1 if r["_family"] in ("LR", "TFIDF_LR")], key=sv, reverse=True)
        tfidf = bool(lr) and lr[0]["_family"] == "TFIDF_LR"
        out.append({"rung": 1, "key": "linear", "name": "Linear model" + (" (TF-IDF)" if tfidf else ""),
                    "model": lr[0]["model"] if lr else None,
                    "why": "one weight per word and class" if tfidf else "one coefficient per feature",
                    "missing": None if lr else "no linear (LR) model in this run's hyperparameters"})
    # rung 2: one boosted-tree model, no stacking
    gbm = sorted([r for r in l1 if r["_family"] in GBM_FAMILIES], key=sv, reverse=True)
    if gbm:
        r2 = gbm[0]
        out.append({"rung": 2, "key": "single", "name": f"Single {FAMILY_NAMES.get(r2['_family'], r2['_family'])}",
                    "model": r2["model"], "why": "one fast model, explainable with SHAP", "missing": None})
    else:
        other = sorted([r for r in l1 if r["_family"] not in ("SCORECARD", "LR", "TFIDF_LR")], key=sv, reverse=True)
        r2 = other[0] if other else None
        out.append({"rung": 2, "key": "single",
                    "name": f"Single {FAMILY_NAMES.get(r2['_family'], r2['_family'] or 'model')}" if r2 else "Single GBM",
                    "model": r2["model"] if r2 else None, "why": "one model, no stacking",
                    "missing": None if r2 else "no single level-1 model in this run"})
    # rung 3: what AutoGluon picked
    best = predictor.model_best
    same = best == out[-1]["model"]
    out.append({"rung": 3, "key": "stack", "name": "AutoGluon best" + ("" if same else " (stack)"), "model": best,
                "why": "the ceiling of what the data supports", "missing": None,
                "same_as": 2 if same else None})
    return out


def _metric(predictor, cost: dict[str, Any] | None = None):
    """(name, needs_proba, scorer(y, pred) higher-is-better, natural(score), greater_is_better).
    D3: a binary cost run (F7) is scored by its expected cost per 1,000 decisions, every rung deciding by the cost's
    Bayes rule p > t* (as AutoGluon scores the cost metric), lower is better; AUC would rank the rungs on something the
    run does not optimise."""
    pt = predictor.problem_type
    if pt == "binary" and cost and cost.get("kind") == "binary":
        from ensemble_models.cost import binary_row_costs

        t = float(cost["bayes_threshold"])

        def cost_score(y, p):
            return -1000.0 * float(binary_row_costs(y, p > t, cost["c_fp"], cost["c_fn"], cost.get("g_tp") or 0).mean())

        return "expected_cost", "proba1", cost_score, (lambda s: -s), False
    if pt == "binary":
        return "roc_auc", "proba1", _auc, (lambda s: s), True
    em = predictor.eval_metric
    needs = "proba" if getattr(em, "needs_proba", False) or getattr(em, "needs_threshold", False) else "pred"

    def score(y, pred):
        return float(em(y, pred))  # AutoGluon scorers are higher-is-better (errors negated)

    return em.name, needs, score, (lambda s: float(em.convert_score_to_original(s))), int(getattr(em, "_sign", 1)) > 0


def _predictions(predictor, model: str | None, X: pd.DataFrame, needs: str):
    if needs == "proba1":
        return predictor.predict_proba(X, model=model, as_multiclass=False).to_numpy(dtype=float)
    if needs == "proba":
        return predictor.predict_proba(X, model=model).to_numpy(dtype=float)
    return predictor.predict(X, model=model).to_numpy()


def _baseline(predictor, train_y: pd.Series, n: int, needs: str):
    pt = predictor.problem_type
    if pt == "binary":
        return np.full(n, float((train_y == predictor.positive_class).mean()))
    if pt == "multiclass":
        if needs == "proba":
            pri = train_y.value_counts(normalize=True)
            return np.tile(np.asarray([float(pri.get(c, 0.0)) for c in predictor.class_labels]), (n, 1))
        return np.full(n, train_y.mode().iloc[0], dtype=object)
    return np.full(n, float(pd.to_numeric(train_y, errors="coerce").mean()))


def _sample_size(train: pd.DataFrame, holdout: pd.DataFrame, label: str) -> dict[str, Any] | None:
    """D1's sample-size finding on the run's own columns (None when the data is big enough)."""
    from .. import data_doctor as dd
    from ..intake import detect

    df = pd.concat([train, holdout]).drop(columns=["__ensemble_group_fold"], errors="ignore").reset_index(drop=True)
    try:
        f = dd.check_sample_size(dd.Ctx(df, label, detect(df)["columns"]))
    except Exception:  # noqa: BLE001 — a hint, never a failure
        return None
    return {"severity": f[0]["severity"], "title": f[0]["title"], "numbers": f[0]["numbers"]} if f else None


def compute(predictor, train: pd.DataFrame, holdout: pd.DataFrame, label: str, cfg: dict[str, Any] | None = None,
            seed: int = 0, measure_latency: bool = True) -> dict[str, Any]:
    from ..registry import fastpath

    t0 = time.time()
    cfg = cfg or {}
    feats = list(predictor.original_features)
    X = holdout[feats]
    y_raw = holdout[label]
    name, needs, score, natural, gib = _metric(predictor, cfg.get("cost"))
    label_of = {"roc_auc": "AUC", "expected_cost": "cost / 1,000"}
    if needs == "proba1":
        y = (y_raw == predictor.positive_class).to_numpy().astype(int)
    else:
        y = y_raw.to_numpy()
    rungs = [{"rung": 0, "key": "baseline", "name": "Baseline", "model": None, "missing": None,
              "why": "predicts the training " + ("event rate" if predictor.problem_type == "binary" else
                                                 "class shares" if predictor.problem_type == "multiclass" else "mean")
                     + " for everyone: the floor"}] + pick_rungs(predictor)
    preds: dict[int, Any] = {0: _baseline(predictor, train[label], len(X), needs)}
    for r in rungs[1:]:
        if r["model"] and not r.get("same_as"):
            preds[r["rung"]] = _predictions(predictor, r["model"], X, needs)
    present = [r["rung"] for r in rungs if r["rung"] in preds]
    full = {k: score(y, preds[k]) for k in present}
    # paired bootstrap: one draw of rows, every rung scored on it
    rng = np.random.default_rng(seed)
    n = len(y)
    B = B_AUC if needs == "proba1" else B_OTHER
    S = []
    for _ in range(B):
        i = rng.integers(0, n, n)
        if needs == "proba1" and name == "roc_auc" and y[i].min() == y[i].max():
            continue
        S.append([score(y[i], preds[k][i]) for k in present])
    S = np.asarray(S, dtype=float)
    col = {k: j for j, k in enumerate(present)}
    best = max(present, key=lambda k: (full[k], k))  # ties → the more complex rung counts as best
    small = _sample_size(train, holdout, label)
    level = 0.99 if small and small["severity"] in ("high", "medium") else 0.95
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    for r in rungs:
        k = r["rung"]
        if k not in col:
            if r.get("same_as"):
                r["score"] = rungs[r["same_as"]].get("score")
            continue
        d = S[:, col[best]] - S[:, col[k]]
        r["score"] = float(natural(full[k]))
        r["score_internal"] = float(full[k])
        r["gap"] = {"delta": float(full[best] - full[k]), "ci": [float(np.quantile(d, lo_q)), float(np.quantile(d, hi_q))],
                    "ci95": [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))],
                    "p_le_0": float((d <= 0).mean())} if k != best else {"delta": 0.0, "ci": [0.0, 0.0], "ci95": [0.0, 0.0], "p_le_0": 1.0}
        r["within"] = k == best or r["gap"]["ci"][0] <= 0
    # latency and size, as served
    root = Path(predictor.path) / "models"
    for r in rungs:
        if r["rung"] == 0:
            r.update(latency_ms_p95=0.0, latency_ms_p50=0.0, size_bytes=0, n_models=0)
            continue
        if not r["model"]:
            continue
        try:
            deps = list(predictor._trainer.get_minimum_model_set(r["model"]))
        except Exception:  # noqa: BLE001
            deps = [r["model"]]
        r["n_models"] = len(deps)
        r["size_bytes"] = int(sum(_dir_size(root / m) for m in deps))
        if measure_latency:
            try:
                predictor.persist(models=[r["model"]], with_ancestors=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                with fastpath.pruned(predictor, r["model"]):
                    t = fastpath.one_row(fastpath.predict_fn(predictor, r["model"]), X, n=30, budget_s=1.5)
                r["latency_ms_p50"], r["latency_ms_p95"] = t["ms_p50"], t["ms_p95"]
            except Exception as e:  # noqa: BLE001
                r["latency_error"] = f"{type(e).__name__}: {e}"[:200]
            try:
                predictor.unpersist(models=[r["model"]])
            except Exception:  # noqa: BLE001
                pass
    # the recommendation: the simplest servable rung within the interval of the best
    servable = [r for r in rungs if r["rung"] > 0 and r["model"] and not r.get("same_as") and r["rung"] in col]
    rec = next((r for r in servable if r["within"]), servable[-1] if servable else None)
    stack = next((r for r in rungs if r["rung"] == 3), None)
    out: dict[str, Any] = {
        "metric": name, "metric_label": label_of.get(name, name), "greater_is_better": True if name == "roc_auc" else gib,
        "level": level, "B": int(len(S)), "n_holdout": int(n), "best_rung": best,
        "rungs": [{k: v for k, v in r.items() if k != "score_internal"} for r in rungs],
        "rule": (f"Serve the simplest rung whose holdout {label_of.get(name, name)} is within the "
                 f"{level:.0%} paired-bootstrap interval of the best: its gap's interval includes 0 "
                 f"({len(S):,} resamples of the {n:,} holdout rows, every rung on the same draw)."),
        "small_data": small,
        "seconds": None,
    }
    if name == "expected_cost":
        c = cfg["cost"]
        out["cost"] = {"c_fp": c["c_fp"], "c_fn": c["c_fn"], "g_tp": c.get("g_tp") or 0, "t": c["bayes_threshold"]}
        out["rule"] += (f" Cost run: the score is the expected cost per 1,000 decisions (c_FP {c['c_fp']:g}, c_FN {c['c_fn']:g}), "
                        f"every rung deciding by the Bayes rule p > t* = {c['bayes_threshold']:.3f}; lower is better.")
    if small and level == 0.99:
        ev = small["numbers"].get("events per feature")
        out["small_data_note"] = (f"Small data ({small['title'].split(':')[0].lower()}: "
                                  + (f"{ev:g} events per feature" if ev is not None else f"{small['numbers'].get('rows per feature')} rows per feature")
                                  + "): the interval is 99 %, not 95 %, so a more complex rung must win more clearly.")
    if rec:
        out["recommended"] = {"rung": rec["rung"], "name": rec["name"], "model": rec["model"],
                              "serve_model": None if rec["rung"] == 3 else rec["model"]}
        b = next(r for r in rungs if r["rung"] == best)
        if rec["rung"] == best:
            out["sentence"] = f"{rec['name']} is the best rung ({out['metric_label']} {rec['score']:.3f}); no simpler rung is within the interval."
        else:
            g = rec["gap"]
            if name == "expected_cost":
                out["sentence"] = (f"{rec['name']} costs {g['delta']:.1f} more per 1,000 decisions than {b['name']} "
                                   f"({level:.0%} CI [{g['ci'][0]:+.1f}, {g['ci'][1]:+.1f}]): serve it.")
            else:
                out["sentence"] = (f"{rec['name']} is {g['delta']:.3f} {out['metric_label']} below {b['name']} "
                                   f"({level:.0%} CI [{g['ci'][0]:+.3f}, {g['ci'][1]:+.3f}]): serve it.")
            if rec.get("latency_ms_p95") and b.get("latency_ms_p95"):
                x = b["latency_ms_p95"] / max(rec["latency_ms_p95"], 1e-6)
                if x >= 1.5:
                    out["sentence"] += f" It is {x:.0f}× faster per row"
                    if b.get("size_bytes") and rec.get("size_bytes") and b["size_bytes"] / max(rec["size_bytes"], 1) >= 1.5:
                        out["sentence"] += f" and {b['size_bytes'] / max(rec['size_bytes'], 1):.0f}× smaller"
                    out["sentence"] += "."
    base = rungs[0]
    if base.get("within"):
        out["floor_warning"] = "The baseline is within the interval of the best: no model clearly beats predicting the average."
    # distillation: offered when the stack clearly beats the single model
    single = next((r for r in rungs if r["rung"] == 2), None)
    if (stack and single and rec and rec["rung"] == 3 and stack["rung"] in col and single["rung"] in col and not single["within"]
            and not cfg.get("monotone") and predictor.problem_type != "quantile"):
        out["distill"] = {"available": True, "gap": single["gap"],
                          "text": (f"The stack is clearly better than the single model (+{single['gap']['delta']:.3f} "
                                   f"{out['metric_label']}, {level:.0%} CI [{single['gap']['ci'][0]:+.3f}, "
                                   f"{single['gap']['ci'][1]:+.3f}]). Distillation trains one LightGBM on the stack's "
                                   "predictions (Register → distill); the build reports how much of the gap it keeps.")}
    else:
        out["distill"] = {"available": False}
    out["seconds"] = round(time.time() - t0, 2)
    return out


def for_run(cfg: dict[str, Any]) -> dict[str, Any]:
    """Heavy-pool job for runs that finished before D2 (or whose ladder failed): recompute from the saved predictor."""
    from .pool import load_predictor
    from .worker import apply_column_types, load_frames

    p = load_predictor(cfg["predictor_path"])
    train, holdout, _, tuning = load_frames(cfg)
    apply_column_types(cfg, train, holdout, tuning)
    return compute(p, train, holdout, cfg["label"], cfg)

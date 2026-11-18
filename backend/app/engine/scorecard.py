"""F11: the scorecard challenger. The request, the run's report after the fit, the exported table, and scoring.

* `normalise(spec, problem_type)`: `scorecard` = None / true (on, defaults), false (off) or {pdo, base_score,
  base_odds, max_bins, min_bin_share, min_iv, monotone_bins, fine_bins, C} → the SCORECARD hyperparameters, False,
  or None (not a binary run). On by default for binary runs: the interpretable benchmark.
* The model: `ensemble_models.scorecard.ScorecardModel` (key SCORECARD), trained by AutoGluon with the other models,
  in the same folds (binning on each fold's training rows only).
* `report(...)`: after the fit. The in-run model is bagged (k fold children, each with its own bins) when the run
  bags, so it is not one table. The *deployable* scorecard is the same model class with the same hyperparameters
  refitted on all the training rows (AutoGluon's refit-full idea; never on the holdout), through the predictor's own
  feature generator. Its table is what is shown and exported, and it is scored on the holdout next to the served
  model: ΔAUC = AUC(served) − AUC(scorecard) with a paired bootstrap CI (the same resampled rows for both).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KEY = "SCORECARD"
DIR = "scorecard"
BOOTSTRAP = 1000
PARAMS = {
    "pdo": (float, lambda v: v > 0, "points to double the odds must be > 0"),
    "base_score": (float, lambda v: math.isfinite(v), "base score must be a number"),
    "base_odds": (float, lambda v: v > 0, "base odds must be > 0 (odds of a non-event, e.g. 50 for 50 : 1)"),
    "max_bins": (int, lambda v: 2 <= v <= 20, "max_bins must be 2–20"),
    "fine_bins": (int, lambda v: 2 <= v <= 100, "fine_bins must be 2–100"),
    "min_bin_share": (float, lambda v: 0 < v < 0.5, "min_bin_share must be between 0 and 0.5"),
    "min_iv": (float, lambda v: 0 <= v < 1, "min_iv must be between 0 and 1"),
    "monotone_bins": (str, lambda v: v in ("auto", "none"), "monotone_bins is 'auto' or 'none'"),
    "C": (float, lambda v: v > 0, "C must be > 0"),
}


def defaults() -> dict[str, Any]:
    from ensemble_models.scorecard import DEFAULTS

    return {k: DEFAULTS[k] for k in PARAMS}


def normalise(spec: Any, problem_type: str | None) -> dict[str, Any] | bool | None:
    if spec is False:
        return False if problem_type == "binary" else None
    if spec is None or spec is True:
        if problem_type != "binary":
            if spec is True:
                raise ValueError(f"the scorecard is a binary model (events vs non-events), not {problem_type}.")
            return None
        return defaults()
    if not isinstance(spec, dict):
        raise ValueError("scorecard is true, false or {pdo, base_score, base_odds, max_bins, min_bin_share, min_iv, monotone_bins}.")
    if problem_type != "binary":
        raise ValueError(f"the scorecard is a binary model (events vs non-events), not {problem_type}.")
    unknown = [k for k in spec if k not in PARAMS]
    if unknown:
        raise ValueError(f"no field {', '.join(map(repr, unknown))} (use {', '.join(PARAMS)}).")
    out = defaults()
    for k, v in spec.items():
        t, ok, msg = PARAMS[k]
        try:
            v = t(v)
        except (TypeError, ValueError):
            raise ValueError(msg) from None
        if not ok(v):
            raise ValueError(msg)
        out[k] = v
    return out


def add_to_hyperparameters(hp: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """SCORECARD added at stack level 1 (it is never a stacker). Returns (hyperparameters, added)."""
    levelled = bool(hp) and all(isinstance(k, int) or (isinstance(k, str) and k.isdigit()) for k in hp)
    if levelled:
        key = next((k for k in hp if int(k) == 1), None)
        if key is None or KEY in (hp[key] or {}):
            return hp, False
        return {**hp, key: {**hp[key], KEY: dict(params)}}, True
    if KEY in hp:
        return hp, False
    return {**hp, KEY: dict(params)}, True


# --------------------------------------------------------------------------- after the fit


def scorecard_models(predictor) -> list[str]:
    from ensemble_models.scorecard import ScorecardModel

    out = []
    tr = predictor._trainer
    for name in predictor.model_names():
        try:
            if issubclass(tr.get_model_attribute(name, "type_inner") or object, ScorecardModel) \
                    or issubclass(tr.get_model_attribute(name, "type") or object, ScorecardModel):
                out.append(name)
        except Exception:  # noqa: BLE001
            continue
    return out


def _child(predictor, name: str):
    m = predictor._trainer.load_model(name)
    if hasattr(m, "load_child") and getattr(m, "models", None):
        return m.load_child(m.models[0])
    return m


def auc(y: np.ndarray, p: np.ndarray) -> float:
    from scipy.stats import rankdata

    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(p)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def ks(y: np.ndarray, p: np.ndarray) -> float:
    o = np.argsort(-p, kind="stable")
    yy = y[o]
    tpr = np.cumsum(yy) / max(yy.sum(), 1)
    fpr = np.cumsum(1 - yy) / max((1 - yy).sum(), 1)
    return float(np.max(np.abs(tpr - fpr)))


def delta_auc(y: np.ndarray, p_a: np.ndarray, p_b: np.ndarray, B: int = BOOTSTRAP, seed: int = 0) -> dict[str, Any]:
    """ΔAUC = AUC(a) − AUC(b) on the same rows, with a paired percentile bootstrap: each resample draws n rows with
    replacement and scores both models on the same draw (resamples with one class only are skipped)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    d0 = auc(y, p_a) - auc(y, p_b)
    ds = []
    for _ in range(B):
        i = rng.integers(0, n, n)
        yy = y[i]
        if yy.min() == yy.max():
            continue
        ds.append(auc(yy, p_a[i]) - auc(yy, p_b[i]))
    ds = np.asarray(ds)
    return {"delta": float(d0), "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))],
            "se": float(ds.std(ddof=1)), "p_le_0": float((ds <= 0).mean()), "B": int(len(ds)), "level": 0.95,
            "method": "paired percentile bootstrap over holdout rows", "seed": seed}


def _bands(score: np.ndarray, y: np.ndarray, k: int = 10) -> list[dict[str, Any]]:
    """Equal-frequency score bands (lowest score first) with their event rate: the scorecard's rank ordering."""
    o = np.argsort(score, kind="stable")
    out = []
    for part in np.array_split(o, k):
        if not len(part):
            continue
        out.append({"lo": float(score[part].min()), "hi": float(score[part].max()), "n": int(len(part)),
                    "events": int(y[part].sum()), "event_rate": float(y[part].mean())})
    return out


def _cost(y: np.ndarray, yhat: np.ndarray, c: dict[str, Any]) -> float:
    from ensemble_models.cost import binary_expected_cost_labels

    return float(binary_expected_cost_labels(y, yhat, c_fp=c["c_fp"], c_fn=c["c_fn"], g_tp=c.get("g_tp", 0.0)))


def report(predictor, train: pd.DataFrame, holdout: pd.DataFrame, label: str, cfg: dict[str, Any],
           run_dir: Path) -> dict[str, Any]:
    from ensemble_models.scorecard import DEFAULTS, ScorecardModel

    names = scorecard_models(predictor)
    if not names:
        return {"available": False, "reason": "no SCORECARD model in this run (it was switched off, or AutoGluon "
                                              "skipped it: see the run's log)"}
    name = names[0]
    ch = _child(predictor, name)
    params = {k: v for k, v in (ch.params or {}).items() if k in DEFAULTS or k == "monotone"}
    feats = list(predictor.original_features)
    X = predictor.transform_features(train[feats])
    y = predictor.transform_labels(train[label]).to_numpy().astype(int)
    out_dir = run_dir / DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    m = ScorecardModel(path=str(out_dir) + "/", name="Scorecard_deploy", problem_type="binary",
                       eval_metric=predictor.eval_metric, hyperparameters=params)
    m.fit(X=X, y=pd.Series(y, index=X.index))
    m.level_names = level_names(X, train)
    m.save()
    tbl = m.table()
    (out_dir / "model_path.txt").write_text(m.path)

    Xh = predictor.transform_features(holdout[feats])
    yh = predictor.transform_labels(holdout[label]).to_numpy().astype(int)
    sc = m.score_frame(Xh)
    p_sc = m.predict_proba(Xh)
    check = float(np.max(np.abs(p_sc - sc["p_event"]))) if len(p_sc) else 0.0
    best = predictor.model_best
    p_best = predictor.predict_proba(holdout[feats], model=best, as_multiclass=False).to_numpy(dtype=float)
    p_bag = predictor.predict_proba(holdout[feats], model=name, as_multiclass=False).to_numpy(dtype=float)
    a_sc, a_best, a_bag = auc(yh, p_sc), auc(yh, p_best), auc(yh, p_bag)
    d = delta_auc(yh, p_best, p_sc)
    d_bag = delta_auc(yh, p_best, p_bag, B=300)
    lb = {r["model"]: r for r in predictor.leaderboard(silent=True).to_dict(orient="records")}
    res: dict[str, Any] = {
        "available": True, "model": name, "served_model": best, "positive_class": str(predictor.positive_class),
        "params": {k: v for k, v in params.items() if k != "monotone"},
        "monotone": (params.get("monotone") or {}).get("constraints") or {},
        "table": tbl, "n_train": int(len(y)), "n_holdout": int(len(yh)),
        "holdout": {
            "scorecard": {"auc": a_sc, "gini": 2 * a_sc - 1, "ks": ks(yh, p_sc)},
            "served": {"model": best, "auc": a_best, "gini": 2 * a_best - 1, "ks": ks(yh, p_best)},
            "scorecard_in_run": {"model": name, "auc": a_bag, "gini": 2 * a_bag - 1, "score_val": lb.get(name, {}).get("score_val"),
                                 "bagged": bool(getattr(predictor._trainer, "bagged_mode", False))},
            "delta_auc": d, "delta_auc_in_run": d_bag,
            "bands": _bands(sc["score"], yh), "proba_check": check,
            "score": {"min": float(sc["score"].min()), "max": float(sc["score"].max()),
                      "mean_event": float(sc["score"][yh == 1].mean()) if (yh == 1).any() else None,
                      "mean_non_event": float(sc["score"][yh == 0].mean()) if (yh == 0).any() else None},
        },
    }
    # F8: a constrained column must get points in the constraint's direction over its ordered bins.
    mono = {}
    for f in tbl["features"]:
        if f.get("constraint"):
            pts = [r["points"] for r in tbl["rows"] if r["feature"] == f["feature"] and r["label"] != "missing"]
            if f["in_model"]:
                # more points = lower risk, so ↑ (risk non-decreasing in x) means points non-increasing in x
                ok = all(-f["constraint"] * (b - a) >= -1e-9 for a, b in zip(pts, pts[1:]))
            else:
                ok = True
            mono[f["feature"]] = {"sign": f["constraint"], "in_model": f["in_model"], "monotone": ok, "points": pts}
    res["monotone_check"] = mono
    cost = cfg.get("cost")
    if cost and cost.get("kind") == "binary":  # F7: the scorecard deciding at the Bayes threshold of the run's costs
        t = float(cost.get("bayes_threshold") or cost["c_fp"] / (cost["c_fp"] + cost["c_fn"] + cost.get("g_tp", 0.0)))
        yhat_sc = (p_sc > t).astype(int)
        yhat_best = (predictor.predict(holdout[feats], model=best) == predictor.positive_class).to_numpy().astype(int)
        res["cost"] = {"threshold_scorecard": t, "threshold_served": float(predictor.decision_threshold),
                       "scorecard_per_1000": 1000 * _cost(yh, yhat_sc, cost), "served_per_1000": 1000 * _cost(yh, yhat_best, cost),
                       "c_fp": cost["c_fp"], "c_fn": cost["c_fn"], "g_tp": cost.get("g_tp", 0.0)}
    table_frame(tbl).to_csv(out_dir / "scorecard.csv", index=False)
    (out_dir / "report.json").write_text(json.dumps(res, default=float))
    return res


def level_names(X: pd.DataFrame, raw: pd.DataFrame) -> dict[str, dict[str, str]]:
    """{feature: {AutoGluon category code: raw level}} for categorical features that keep their raw column's name."""
    out = {}
    for f in X.columns:
        if isinstance(X[f].dtype, pd.CategoricalDtype) and f in raw.columns:
            pair = pd.DataFrame({"c": X[f].astype(object).to_numpy(), "r": raw[f].to_numpy()}).dropna()
            out[f] = {str(c): str(g["r"].mode().iloc[0]) for c, g in pair.groupby("c")}
    return out


def table_frame(tbl: dict[str, Any]) -> pd.DataFrame:
    """The exported scorecard: one row per bin of every feature in the model, then the scaling as comments-free
    columns a reviewer can re-add: score = Σ points of the applicant's bins."""
    rows = [r for r in tbl["rows"] if r["in_model"]]
    feats = {f["feature"]: f for f in tbl["features"]}
    df = pd.DataFrame([{"feature": r["feature"], "bin": r["label"], "points": r["points_rounded"],
                        "points_exact": round(r["points"], 6), "woe": round(r["woe"], 6), "n": r["n"],
                        "share": round(r["share"], 6), "events": r["events"],
                        "event_rate": None if r["event_rate"] is None else round(r["event_rate"], 6),
                        "iv_bin": round(r["iv"], 6), "iv_feature": round(feats[r["feature"]]["iv"], 6),
                        "coefficient": round(feats[r["feature"]]["coefficient"], 6)} for r in rows])
    sc = tbl["scaling"]
    meta = pd.DataFrame([{"feature": "_scaling", "bin": f"PDO={sc['pdo']:g}; base score {sc['base_score']:g} at odds "
                          f"{sc['base_odds']:g}:1; factor={sc['factor']:.6f}; offset={sc['offset']:.6f}; "
                          f"intercept={tbl['intercept']:.6f}; unseen values score WoE 0"}])
    out = pd.concat([df, meta], ignore_index=True)
    for c in ("points", "n", "events"):
        out[c] = out[c].astype("Int64")
    return out


# --------------------------------------------------------------------------- scoring (heavy worker)


def load_deployed(run_dir: Path):
    from ensemble_models.scorecard import ScorecardModel

    p = (run_dir / DIR / "model_path.txt")
    if not p.exists():
        raise FileNotFoundError("This run has no deployable scorecard (no SCORECARD model, or the report failed).")
    return ScorecardModel.load(p.read_text())


def score_rows(predictor, model, rows: pd.DataFrame, top_k: int = 4) -> dict[str, Any]:
    """Raw rows → per row: score, P(event), points per feature (with the bin each value fell in), and the reason codes
    (features ranked by points lost against their best bin)."""
    from ensemble_models.scorecard import reasons

    feats = list(predictor.original_features)
    X = predictor.transform_features(rows.reindex(columns=feats))
    s = model.score_frame(X)
    by = {b.name: b for b in model.bins}
    out = []
    rs = reasons(s["shortfall"], top_k)
    for i in range(len(X)):
        detail = []
        for f in model.selected:
            b = by[f]
            j = int(b.assign(X[f].iloc[[i]])[0])
            raw = rows[f].iloc[i] if f in rows.columns else X[f].iloc[i]
            detail.append({"feature": f, "value": _jsonable(raw), "bin": model.label(b, j),
                           "points": float(s["points"][f].iloc[i]), "max_points": float(model.points_of(f)[:-1].max()),
                           "points_lost": float(s["shortfall"][f].iloc[i])})
        out.append({"score": float(s["score"][i]), "p_event": float(s["p_event"][i]), "points": detail, "reasons": rs[i]})
    return {"rows": out, "scaling": model.scaling(), "positive_class": None}


def _jsonable(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v if isinstance(v, (int, float, str, bool)) else str(v)

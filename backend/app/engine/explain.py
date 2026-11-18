"""Understanding a finished run (S4): the decision threshold, ROC / PR / calibration curves, errors, residuals,
fairness by group, permutation importance with confidence intervals and SHAP for one holdout row.

Everything is computed from the run's saved holdout predictions (`holdout_predictions.csv`: the best model's
probabilities on the fixed holdout) and the saved predictor, with scikit-learn, fairlearn and shap, so the numbers in
the UI can be checked against those libraries directly (the tests do).

Conventions
- Binary: the positive class is AutoGluon's `predictor.positive_class`; p = P̂(y = positive | x). A row is predicted
  positive when p > t (strictly greater, as AutoGluon does: `1 if pred > decision_threshold else 0`).
- Costs: C(t) = c_FP·FP(t) + c_FN·FN(t), in whatever unit the user gives (default 1 each); also per row.
"""

from __future__ import annotations

import json
import logging
import math
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .jsonsafe import jsonable


logging.getLogger("shap").setLevel(logging.WARNING)


class ExplainError(ValueError):
    """Shown to the user as a 400."""


# --------------------------------------------------------------------------- holdout predictions


def load_holdout(run_dir: Path) -> pd.DataFrame:
    path = Path(run_dir) / "holdout_predictions.csv"
    if not path.exists():
        raise FileNotFoundError("This run has no saved holdout predictions.")
    return pd.read_csv(path)


def _proba(df: pd.DataFrame, classes: list[Any]) -> np.ndarray:
    return df[[f"p_{c}" for c in classes]].to_numpy(dtype=float)


def _classes(df: pd.DataFrame) -> list[str]:
    return [c[2:] for c in df.columns if c.startswith("p_")]


def _as_str(v: Any) -> str:
    """Labels compared as strings: the CSV round trip turns 1 into 1 and 'Yes' into 'Yes', but True into 'True'."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def binary_arrays(df: pd.DataFrame, positive: Any) -> tuple[np.ndarray, np.ndarray]:
    """(y ∈ {0,1}, p = probability of the positive class)."""
    pos = _as_str(positive)
    col = f"p_{pos}"
    if col not in df.columns:
        raise ExplainError(f"No probability column for the positive class {pos!r}.")
    y = (df["y_true"].map(_as_str) == pos).to_numpy(dtype=int)
    return y, df[col].to_numpy(dtype=float)


# --------------------------------------------------------------------------- threshold


THRESHOLD_GRID = np.round(np.linspace(0, 1, 201), 6)


def counts_at(y: np.ndarray, p: np.ndarray, t: float) -> dict[str, Any]:
    """The confusion counts and every threshold metric at threshold t (predict positive when p > t)."""
    yhat = p > t
    tp = int(np.sum(yhat & (y == 1)))
    fp = int(np.sum(yhat & (y == 0)))
    fn = int(np.sum(~yhat & (y == 1)))
    tn = int(np.sum(~yhat & (y == 0)))
    return {"threshold": float(t), **metrics_from_counts(tp, fp, fn, tn)}


def metrics_from_counts(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    n = tp + fp + fn + tn
    prec = tp / (tp + fp) if tp + fp else 0.0  # sklearn's zero_division=0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    tnr = tn / (tn + fp) if tn + fp else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    bacc = (rec + tnr) / 2
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = (tp * tn - fp * fn) / denom if denom else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n, "precision": prec, "recall": rec, "f1": f1,
            "specificity": tnr, "fpr": fpr, "balanced_accuracy": bacc, "accuracy": (tp + tn) / n if n else 0.0,
            "mcc": mcc, "predicted_positive_rate": (tp + fp) / n if n else 0.0}


def threshold_analysis(df: pd.DataFrame, positive: Any, *, current: float, cost_fp: float = 1.0, cost_fn: float = 1.0,
                       at: float | None = None) -> dict[str, Any]:
    y, p = binary_arrays(df, positive)
    grid = []
    for t in THRESHOLD_GRID:
        c = counts_at(y, p, float(t))
        c["cost"] = cost_fp * c["fp"] + cost_fn * c["fn"]
        c["cost_per_row"] = c["cost"] / c["n"] if c["n"] else 0.0
        grid.append(c)

    def best(key: str, minimise: bool = False) -> dict[str, Any]:
        vals = [g[key] for g in grid]
        i = int(np.argmin(vals) if minimise else np.argmax(vals))
        return {"threshold": grid[i]["threshold"], "value": vals[i]}

    def point(t: float) -> dict[str, Any]:
        c = counts_at(y, p, t)
        c["cost"] = cost_fp * c["fp"] + cost_fn * c["fn"]
        c["cost_per_row"] = c["cost"] / c["n"] if c["n"] else 0.0
        return c

    # Bayes-optimal threshold for these costs if p were calibrated: predict positive when p·c_FN > (1 − p)·c_FP.
    bayes = cost_fp / (cost_fp + cost_fn) if cost_fp + cost_fn > 0 else 0.5
    return {
        "positive_class": jsonable(positive), "n": int(len(y)), "n_positive": int(y.sum()),
        "prevalence": float(y.mean()) if len(y) else 0.0,
        "cost_fp": cost_fp, "cost_fn": cost_fn, "rule": "predict positive when p > t",
        "current": point(current), "default": point(0.5), "at": point(at) if at is not None else None,
        "best": {"f1": best("f1"), "balanced_accuracy": best("balanced_accuracy"), "mcc": best("mcc"),
                 "cost": best("cost", minimise=True)},
        "bayes_threshold": bayes,
        "grid": grid,
        # Positive-class probabilities as a 50-bin histogram per true class: where the threshold cuts.
        "score_histogram": _score_hist(y, p),
    }


def _score_hist(y: np.ndarray, p: np.ndarray, bins: int = 50) -> dict[str, Any]:
    edges = np.linspace(0, 1, bins + 1)
    pos, _ = np.histogram(p[y == 1], bins=edges)
    neg, _ = np.histogram(p[y == 0], bins=edges)
    return {"edges": edges.round(6).tolist(), "positive": pos.tolist(), "negative": neg.tolist()}


# --------------------------------------------------------------------------- curves and reports


def _roc(y: np.ndarray, s: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import roc_auc_score, roc_curve

    fpr, tpr, thr = roc_curve(y, s)
    return {"fpr": fpr.tolist(), "tpr": tpr.tolist(), "thresholds": [None if not np.isfinite(t) else float(t) for t in thr],
            "auc": float(roc_auc_score(y, s)), "n_pos": int(y.sum()), "n_neg": int(len(y) - y.sum())}


def _pr(y: np.ndarray, s: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import auc, average_precision_score, precision_recall_curve

    prec, rec, thr = precision_recall_curve(y, s)
    return {"precision": prec.tolist(), "recall": rec.tolist(), "thresholds": thr.tolist(),
            "average_precision": float(average_precision_score(y, s)), "auc_trapezoid": float(auc(rec, prec)),
            "prevalence": float(y.mean())}


def _calibration(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> dict[str, Any]:
    """Reliability diagram on equal-width bins (sklearn's calibration_curve, strategy='uniform'), with each bin's
    count, and the expected calibration error ECE = Σ_b (n_b/n)·|ȳ_b − p̄_b|."""
    from sklearn.calibration import calibration_curve
    from sklearn.metrics import brier_score_loss

    frac_pos, mean_pred = calibration_curve(y, p, n_bins=n_bins, strategy="uniform")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.searchsorted(edges[1:-1], p, side="right")  # sklearn's binning
    counts = np.bincount(idx, minlength=n_bins)
    nonempty = counts > 0
    ece = float(np.sum(counts[nonempty] / len(p) * np.abs(frac_pos - mean_pred)))
    return {"n_bins": n_bins, "strategy": "uniform", "edges": edges.round(6).tolist(),
            "mean_predicted": mean_pred.tolist(), "fraction_positive": frac_pos.tolist(),
            "counts": counts[nonempty].tolist(), "bins": np.flatnonzero(nonempty).tolist(), "brier": float(brier_score_loss(y, p)), "ece": ece,
            "brier_reference": float(y.mean() * (1 - y.mean()))}


def _features_for_rows(cfg: dict[str, Any], rows: list[int]) -> dict[int, dict[str, Any]]:
    if not rows:
        return {}
    df = pd.read_csv(cfg["csv_path"])
    keep = [c for c in df.columns if c not in (cfg.get("drop_columns") or []) and c != cfg["label"]]
    sub = df.loc[rows, keep]
    return {int(i): jsonable(r) for i, r in zip(sub.index, sub.to_dict(orient="records"))}


def classification_report(df: pd.DataFrame, classes: list[str], positive: Any | None, cfg: dict[str, Any],
                          worst_k: int = 12) -> dict[str, Any]:
    from sklearn.metrics import confusion_matrix, log_loss, precision_recall_fscore_support

    y_true = df["y_true"].map(_as_str).to_numpy()
    y_pred = df["y_pred"].map(_as_str).to_numpy()
    P = _proba(df, classes)
    cm = confusion_matrix(y_true, y_pred, labels=classes)
    prec, rec, f1, sup = precision_recall_fscore_support(y_true, y_pred, labels=classes, zero_division=0)
    per_class = [{"class": c, "precision": float(prec[i]), "recall": float(rec[i]), "f1": float(f1[i]),
                  "support": int(sup[i])} for i, c in enumerate(classes)]
    out: dict[str, Any] = {"kind": "classification", "classes": classes, "n": int(len(df)),
                           "confusion_matrix": cm.tolist(), "per_class": per_class,
                           "accuracy": float((y_true == y_pred).mean()),
                           "log_loss": _log_loss(log_loss, y_true, P, classes)}

    if len(classes) == 2 and positive is not None:
        y, p = binary_arrays(df, positive)
        out["positive_class"] = _as_str(positive)
        out["roc"] = {_as_str(positive): _roc(y, p)}
        out["pr"] = {_as_str(positive): _pr(y, p)}
        out["calibration"] = {"kind": "binary", "class": _as_str(positive), **_calibration(y, p)}
    else:
        out["roc"], out["pr"] = {}, {}
        for i, c in enumerate(classes):
            yc = (y_true == c).astype(int)
            if 0 < yc.sum() < len(yc):
                out["roc"][c] = _roc(yc, P[:, i])
                out["pr"][c] = _pr(yc, P[:, i])
        aucs = [v["auc"] for v in out["roc"].values()]
        out["roc_macro_auc"] = float(np.mean(aucs)) if aucs else None
        # Top-label calibration: confidence max_k p̂_k against whether the top label was right.
        conf = P.max(axis=1)
        top = np.asarray(classes)[P.argmax(axis=1)]
        cal = _calibration((top == y_true).astype(int), conf)
        onehot = (y_true[:, None] == np.asarray(classes)[None, :]).astype(float)
        cal["brier"] = float(np.mean(np.sum((P - onehot) ** 2, axis=1)))  # multiclass Brier: mean Σ_k (p̂_k − y_k)²
        cal.pop("brier_reference", None)
        out["calibration"] = {"kind": "top_label", **cal}

    # The worst mistakes: wrong predictions with the least probability on the true class.
    idx = {c: i for i, c in enumerate(classes)}
    p_true = np.array([P[r, idx[t]] if t in idx else 0.0 for r, t in enumerate(y_true)])
    wrong = np.where(y_true != y_pred)[0]
    order = wrong[np.argsort(p_true[wrong], kind="stable")][:worst_k]
    feats = _features_for_rows(cfg, [int(df["row"].iloc[i]) for i in order])
    out["n_wrong"] = int(len(wrong))
    out["worst"] = [{"row": int(df["row"].iloc[i]), "y_true": y_true[i], "y_pred": y_pred[i],
                     "p_true": float(p_true[i]), "p_pred": float(P[i, idx[y_pred[i]]]) if y_pred[i] in idx else None,
                     "probabilities": {c: float(P[i, j]) for j, c in enumerate(classes)},
                     "features": feats.get(int(df["row"].iloc[i]), {})} for i in order]
    return out


def _log_loss(fn, y_true, P, classes) -> float:
    # Saved probabilities can miss Σ = 1 by ~1e-7 (CSV round trip); sklearn renormalises them and warns. Same value.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return float(fn(y_true, P, labels=classes))


def regression_report(df: pd.DataFrame, cfg: dict[str, Any], worst_k: int = 12, max_points: int = 3000) -> dict[str, Any]:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    y = df["y_true"].to_numpy(dtype=float)
    yhat = df["y_pred"].to_numpy(dtype=float)
    r = y - yhat
    n = len(r)
    counts, edges = np.histogram(r, bins=30)
    q = np.quantile(r, [0.05, 0.25, 0.5, 0.75, 0.95])
    sd = float(np.std(r, ddof=1)) if n > 1 else 0.0
    # Normal reference density scaled to the histogram: n·w·φ((r − r̄)/s)/s.
    w = float(edges[1] - edges[0])
    centres = (edges[:-1] + edges[1:]) / 2
    normal = (n * w * np.exp(-0.5 * ((centres - r.mean()) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))).tolist() if sd else None
    pts = np.arange(n)
    if n > max_points:
        pts = np.random.default_rng(0).choice(n, size=max_points, replace=False)
        pts.sort()
    order = np.argsort(-np.abs(r), kind="stable")[:worst_k]
    feats = _features_for_rows(cfg, [int(df["row"].iloc[i]) for i in order])
    return {
        "kind": "regression", "n": n,
        "rmse": float(math.sqrt(mean_squared_error(y, yhat))), "mae": float(mean_absolute_error(y, yhat)),
        "r2": float(r2_score(y, yhat)), "mean_residual": float(r.mean()), "sd_residual": sd,
        "quantiles": {"q05": float(q[0]), "q25": float(q[1]), "q50": float(q[2]), "q75": float(q[3]), "q95": float(q[4])},
        "points": {"row": df["row"].to_numpy()[pts].tolist(), "y_pred": yhat[pts].tolist(), "residual": r[pts].tolist(),
                   "y_true": y[pts].tolist(), "sampled": bool(n > max_points)},
        "histogram": {"edges": edges.tolist(), "counts": counts.tolist(), "normal": normal},
        "worst": [{"row": int(df["row"].iloc[i]), "y_true": float(y[i]), "y_pred": float(yhat[i]), "residual": float(r[i]),
                   "abs_error": float(abs(r[i])), "features": feats.get(int(df["row"].iloc[i]), {})} for i in order],
    }


def group_candidates(cfg: dict[str, Any], features: list[str], profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Columns that can define groups for fairness: categories (≤ 50 values) and numbers (binned into quartiles)."""
    cols = {c["name"]: c for c in (profile or {}).get("columns", [])}
    out = []
    for f in features:
        c = cols.get(f)
        if not c:
            continue
        if c["type"] == "category" and c["unique"] <= 50:
            out.append({"column": f, "type": "category", "groups": c["unique"]})
        elif c["type"] == "number":
            out.append({"column": f, "type": "number", "groups": min(4, c["unique"]) if c["unique"] > 8 else c["unique"]})
    return out


# --------------------------------------------------------------------------- fairness


def group_labels(values: pd.Series, max_groups: int = 50) -> tuple[pd.Series, str]:
    """Group label per row. Numbers with more than 8 distinct values are cut into quartiles; missing is a group."""
    if pd.api.types.is_numeric_dtype(values) and values.nunique(dropna=True) > 8:
        q = values.quantile([0, 0.25, 0.5, 0.75, 1.0]).to_numpy()
        edges = np.unique(q)
        cats = pd.cut(values, bins=edges, include_lowest=True, duplicates="drop")
        lab = cats.map(lambda iv: f"[{_fmt(iv.left)}, {_fmt(iv.right)}]" if pd.notna(iv) else "(missing)")
        return lab.astype(str).where(values.notna(), "(missing)"), "quartiles"
    lab = values.map(lambda v: "(missing)" if pd.isna(v) else _as_str(v))
    if lab.nunique() > max_groups:
        raise ExplainError(f"'{values.name}' has {lab.nunique()} distinct values; pick a column with at most {max_groups}.")
    return lab, "values"


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}"


def fairness(df: pd.DataFrame, cfg: dict[str, Any], group: str, *, problem_type: str, positive: Any | None,
             threshold: float | None) -> dict[str, Any]:
    from fairlearn.metrics import (MetricFrame, count, demographic_parity_difference, demographic_parity_ratio,
                                   equalized_odds_difference, false_positive_rate, selection_rate, true_positive_rate)
    from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, precision_score

    data = pd.read_csv(cfg["csv_path"], usecols=lambda c: c == group)
    if group not in data.columns:
        raise ExplainError(f"No column named {group!r} in the dataset.")
    if group == cfg["label"]:
        raise ExplainError("The label can't be the group column.")
    g, binning = group_labels(data[group].iloc[df["row"].to_numpy()].reset_index(drop=True).rename(group))

    out: dict[str, Any] = {"group": group, "binning": binning, "n": int(len(df)), "problem_type": problem_type}
    if problem_type == "binary":
        y, p = binary_arrays(df, positive)
        yhat = (p > threshold).astype(int)
        metrics = {"count": count, "selection_rate": selection_rate, "base_rate": lambda yt, yp: float(np.mean(yt)),
                   "accuracy": accuracy_score, "tpr": true_positive_rate, "fpr": false_positive_rate,
                   "precision": lambda yt, yp: precision_score(yt, yp, zero_division=0)}
        mf = MetricFrame(metrics=metrics, y_true=y, y_pred=yhat, sensitive_features=g)
        out["threshold"] = threshold
        out["positive_class"] = _as_str(positive)
        out["gaps"] = {
            "demographic_parity_difference": float(demographic_parity_difference(y, yhat, sensitive_features=g)),
            "demographic_parity_ratio": float(demographic_parity_ratio(y, yhat, sensitive_features=g)),
            "equalized_odds_difference": float(equalized_odds_difference(y, yhat, sensitive_features=g)),
            "tpr_difference": float(mf.difference()["tpr"]), "fpr_difference": float(mf.difference()["fpr"]),
            "accuracy_difference": float(mf.difference()["accuracy"]),
        }
    elif problem_type == "multiclass":
        yt = df["y_true"].map(_as_str).to_numpy()
        yp = df["y_pred"].map(_as_str).to_numpy()
        metrics = {"count": count, "accuracy": accuracy_score,
                   "f1_macro": lambda a, b: f1_score(a, b, average="macro", zero_division=0)}
        mf = MetricFrame(metrics=metrics, y_true=yt, y_pred=yp, sensitive_features=g)
        out["gaps"] = {"accuracy_difference": float(mf.difference()["accuracy"]),
                       "f1_macro_difference": float(mf.difference()["f1_macro"])}
    else:
        yt = df["y_true"].to_numpy(dtype=float)
        yp = df["y_pred"].to_numpy(dtype=float)
        metrics = {"count": count, "mae": mean_absolute_error,
                   "rmse": lambda a, b: float(np.sqrt(np.mean((a - b) ** 2))),
                   "mean_residual": lambda a, b: float(np.mean(a - b)), "mean_prediction": lambda a, b: float(np.mean(b))}
        mf = MetricFrame(metrics=metrics, y_true=yt, y_pred=yp, sensitive_features=g)
        out["gaps"] = {"mae_difference": float(mf.difference()["mae"]), "rmse_difference": float(mf.difference()["rmse"]),
                       "mean_prediction_difference": float(mf.difference()["mean_prediction"])}
    bg = mf.by_group
    out["metrics"] = list(metrics)
    out["overall"] = {k: float(v) for k, v in mf.overall.items()}
    out["by_group"] = [{"group": str(idx), **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()}}
                       for idx, row in bg.iterrows()]
    out["difference"] = {k: (None if pd.isna(v) else float(v)) for k, v in mf.difference().items()}
    out["ratio"] = {k: (None if pd.isna(v) or not np.isfinite(v) else float(v)) for k, v in mf.ratio().items()}
    return out


# --------------------------------------------------------------------------- feature importance


def importance_with_ci(fi: list[dict[str, Any]] | None, level: float = 0.95) -> list[dict[str, Any]] | None:
    """AutoGluon's permutation importance with a t-interval: Î ± t_{(1+level)/2, n−1}·s/√n over the n shuffles."""
    if fi is None:
        return None
    from scipy import stats

    out = []
    for r in fi:
        n = int(r.get("n") or 0)
        sd = r.get("stddev")
        imp = r.get("importance")
        row = dict(r)
        if n > 1 and sd is not None and imp is not None and np.isfinite(sd):
            half = float(stats.t.ppf((1 + level) / 2, n - 1) * sd / math.sqrt(n))
            row.update(ci_low=imp - half, ci_high=imp + half, ci_level=level, t_crit=float(stats.t.ppf((1 + level) / 2, n - 1)))
        else:
            row.update(ci_low=None, ci_high=None, ci_level=level, t_crit=None)
        out.append(row)
    return out


# --------------------------------------------------------------------------- SHAP


SHAP_BACKGROUND = 50
SHAP_NSAMPLES = 2048


def _shap_background(cfg: dict[str, Any], features: list[str], background: int):
    """The fixed background sample: `background` training rows (data.csv minus the holdout, labelled), seed 0."""
    import shap

    df = pd.read_csv(cfg["csv_path"])
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = np.zeros(len(df), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
    train = df.loc[~is_h & df[cfg["label"]].notna(), features]
    return df, train, shap.sample(train, min(background, len(train)), random_state=0)


def resolve_target(predictor, x: pd.DataFrame, target: str | None) -> tuple[str | None, list[str] | None]:
    pt = predictor.problem_type
    classes = [_as_str(c) for c in predictor.class_labels] if pt in ("binary", "multiclass") else None
    if classes is not None:
        if target is None:
            if pt == "binary":
                target = _as_str(predictor.positive_class)
            else:
                pr = predictor.predict_proba(x)
                target = _as_str(pr.columns[int(np.argmax(pr.to_numpy()[0]))])
        if target not in classes:
            raise ExplainError(f"No class {target!r}; the classes are {', '.join(classes)}.")
    return target, classes


def shap_explain(predictor, train: pd.DataFrame, bg: pd.DataFrame, x: pd.DataFrame, target: str | None,
                 classes: list[str] | None, nsamples: int = SHAP_NSAMPLES) -> dict[str, Any]:
    """Kernel SHAP for the one-row frame `x` against the background `bg`. f(x) = φ₀ + Σᵢ φᵢ exactly."""
    import shap

    dtypes = train.dtypes
    col_index = list(train.columns)
    k = classes.index(target) if classes is not None else None

    def f(arr: np.ndarray) -> np.ndarray:
        frame = pd.DataFrame(arr, columns=col_index)
        for c in col_index:
            if pd.api.types.is_numeric_dtype(dtypes[c]):
                frame[c] = pd.to_numeric(frame[c], errors="coerce")
        if k is not None:
            return predictor.predict_proba(frame, as_multiclass=True).to_numpy()[:, k]
        return predictor.predict(frame).to_numpy(dtype=float)

    t0 = time.time()
    explainer = shap.KernelExplainer(f, bg.to_numpy(dtype=object), link="identity", keep_index=False)
    # l1_reg=False: no feature selection, every feature gets its φ (with d ≤ 11, 2048 coalitions enumerate them all).
    phi = np.asarray(explainer.shap_values(x.to_numpy(dtype=object), nsamples=nsamples, l1_reg=False, silent=True)).reshape(-1)
    base = float(np.asarray(explainer.expected_value).reshape(-1)[0])
    fx = float(f(x.to_numpy(dtype=object))[0])
    elapsed = time.time() - t0
    vals = jsonable(x.iloc[0].to_dict())
    contrib = sorted(({"feature": c, "value": vals.get(c), "phi": float(phi[i])} for i, c in enumerate(col_index)),
                     key=lambda d: -abs(d["phi"]))
    return {
        "model": predictor.model_best, "problem_type": predictor.problem_type, "target": target,
        "output": "probability" if classes is not None else "prediction",
        "base_value": base, "fx": fx, "sum_phi": float(phi.sum()), "additivity_error": float(fx - base - phi.sum()),
        "contributions": contrib, "background_size": int(len(bg)), "nsamples": nsamples, "seconds": elapsed,
        "classes": classes,
        "method": "shap.KernelExplainer(f, background, link='identity'): Kernel SHAP, a weighted linear regression over "
                  "feature coalitions; features outside a coalition take background values",
    }


def shap_row(predictor, cfg: dict[str, Any], run_dir: Path, row: int, target: str | None = None,
             background: int = SHAP_BACKGROUND, nsamples: int = SHAP_NSAMPLES) -> dict[str, Any]:
    """Kernel SHAP for one holdout row on `predictor.predict_proba` (classification: the probability of `target`,
    default the predicted class; binary default the positive class) or `predictor.predict` (regression).

    Background: `background` training rows sampled with a fixed seed (shap.sample). φ₀ = E_bg[f], and Kernel SHAP's
    efficiency constraint makes f(x) = φ₀ + Σᵢ φᵢ exact. Cached per (row, target) in <run_dir>/shap/."""
    holdout = load_holdout(run_dir)
    if row not in set(holdout["row"].astype(int)):
        raise ExplainError(f"Row {row} is not in this run's holdout.")
    features = list(predictor.original_features)
    model = predictor.model_best
    df = pd.read_csv(cfg["csv_path"])
    x = df.loc[[row], features]
    target, classes = resolve_target(predictor, x, target)
    cache_dir = Path(run_dir) / "shap"
    cache_dir.mkdir(exist_ok=True)
    key = f"{row}_{target or 'value'}_{background}_{nsamples}_{model}.json".replace("/", "_")
    path = cache_dir / key
    if path.exists():
        out = json.loads(path.read_text())
        out["cached"] = True
        return out
    _, train, bg = _shap_background(cfg, features, background)
    out = {"row": row, **shap_explain(predictor, train, bg, x, target, classes, nsamples),
           "y_true": jsonable(df.loc[row, cfg["label"]]), "cached": False}
    path.write_text(json.dumps(out))
    return out

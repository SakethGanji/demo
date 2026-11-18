"""Business cost (F7): the cost request of a run, and the expected cost of a run's decisions on its holdout.

The AutoGluon scorers live in `ensemble_models.cost` (importable by fit workers and by every process that loads a
saved predictor). This module checks the request and computes what the UI shows, from the run's saved holdout
predictions (`holdout_predictions.csv`), so every number can be recomputed from that file:

- Binary, costs c_FP, c_FN and gain g_TP:  C(t) = (c_FP·FP(t) + c_FN·FN(t) − g_TP·TP(t)) / n  per decision, with a
  row predicted positive when p > t (as AutoGluon does). Reported ×1000: the cost of 1,000 decisions.
- Multiclass, matrix C:  E[C] = (1/n) Σᵢ C[yᵢ][ŷᵢ]  for the served labels (arg-max) and for the Bayes decision
  ŷᵢ* = argmin_j Σ_k p̂ᵢₖ·C[k][j].
- Intervals: percentile bootstrap over holdout rows (B = 1,000, seeded). Two runs scored on the same holdout rows are
  compared with a paired bootstrap of the per-row difference (the same resampled rows for both).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

from ensemble_models.cost import CLASS_METRIC_NAME, METRIC_NAME, bayes_threshold, binary_row_costs  # noqa: F401

from .explain import THRESHOLD_GRID, _as_str, binary_arrays

PER = 1000  # costs are shown per 1,000 decisions
B = 1000
LEVEL = 0.95


# --------------------------------------------------------------------------- the request


def _num(v: Any, name: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number (got {v!r}).") from None
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite.")
    return x


def normalise(cost: dict[str, Any], problem_type: str, classes: Sequence[Any]) -> dict[str, Any]:
    """Binary → {kind, c_fp, c_fn, g_tp, classes, positive_class, bayes_threshold}; multiclass → {kind, classes,
    matrix} with classes in AutoGluon's internal order (sorted). Raises ValueError with a message for the user."""
    names = [_as_str(c) for c in classes]
    if problem_type == "binary":
        if len(names) != 2:
            raise ValueError(f"a binary cost needs a label with two classes (this one has {len(names)}).")
        c_fp = _num(cost.get("c_fp"), "c_fp")
        c_fn = _num(cost.get("c_fn"), "c_fn")
        g_tp = _num(cost.get("g_tp") or 0, "g_tp")
        if min(c_fp, c_fn, g_tp) < 0:
            raise ValueError("c_fp, c_fn and g_tp are amounts ≥ 0 (g_tp is a gain, entered as a positive number).")
        if c_fp + c_fn + g_tp <= 0:
            raise ValueError("at least one of c_fp, c_fn, g_tp must be above 0.")
        return {"kind": "binary", "c_fp": c_fp, "c_fn": c_fn, "g_tp": g_tp, "classes": names,
                "positive_class": names[1], "bayes_threshold": bayes_threshold(c_fp, c_fn, g_tp)}
    if problem_type == "multiclass":
        given = [_as_str(c) for c in cost.get("classes") or []]
        m = cost.get("matrix")
        if not given or not isinstance(m, list):
            raise ValueError("a multiclass cost is {classes: [...], matrix: [[...], ...]} with matrix[i][j] the cost of "
                             "deciding classes[j] when the truth is classes[i].")
        if sorted(given) != sorted(names) or len(set(given)) != len(given):
            missing = [c for c in names if c not in given]
            extra = [c for c in given if c not in names]
            raise ValueError("the matrix must name each class of the label once"
                             + (f"; missing {', '.join(missing)}" if missing else "")
                             + (f"; not in the label: {', '.join(extra)}" if extra else "") + ".")
        k = len(given)
        if len(m) != k or any(not isinstance(r, list) or len(r) != k for r in m):
            raise ValueError(f"the matrix must be {k} × {k}.")
        mat = [[_num(v, f"matrix[{i}][{j}]") for j, v in enumerate(r)] for i, r in enumerate(m)]
        idx = [given.index(c) for c in names]
        mat = [[mat[i][j] for j in idx] for i in idx]
        return {"kind": "multiclass", "classes": names, "matrix": mat}
    raise ValueError(f"a cost applies to binary or multiclass labels; this label is {problem_type}.")


def describe(cost: dict[str, Any]) -> str:
    if cost["kind"] == "binary":
        return (f"ensemble_models.cost.binary_cost_scorer(c_fp={cost['c_fp']:g}, c_fn={cost['c_fn']:g}, "
                f"g_tp={cost['g_tp']:g})")
    return f"ensemble_models.cost.multiclass_cost_scorer(matrix={cost['matrix']})  # classes {cost['classes']}"


def key(cost: dict[str, Any]) -> str:
    if cost["kind"] == "binary":
        return f"b:{cost['c_fp']:g}:{cost['c_fn']:g}:{cost['g_tp']:g}"
    return "m:" + ";".join(",".join(f"{v:g}" for v in r) for r in cost["matrix"])


# --------------------------------------------------------------------------- statistics


def boot_mean(x: np.ndarray, *, b: int = B, seed: int = 0, idx: np.ndarray | None = None) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean of x."""
    if len(x) == 0:
        return float("nan"), float("nan")
    if idx is None:
        idx = np.random.default_rng(seed).integers(0, len(x), size=(b, len(x)))
    m = x[idx].mean(axis=1)
    a = (1 - LEVEL) / 2
    return float(np.quantile(m, a)), float(np.quantile(m, 1 - a))


def boot_index(n: int, *, b: int = B, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, n, size=(b, n))


def _stat(x: np.ndarray, idx: np.ndarray) -> dict[str, Any]:
    lo, hi = boot_mean(x, idx=idx)
    return {"per_decision": float(x.mean()), "per_1000": PER * float(x.mean()), "ci_per_1000": [PER * lo, PER * hi],
            "n": int(len(x)), "B": int(idx.shape[0]), "level": LEVEL}


# --------------------------------------------------------------------------- one run


def _binary_rows(hp: pd.DataFrame, positive: Any, cost: dict[str, Any], t: float) -> np.ndarray:
    y, p = binary_arrays(hp, positive)
    return binary_row_costs(y, p > t, cost["c_fp"], cost["c_fn"], cost["g_tp"])


def row_costs(hp: pd.DataFrame, cost: dict[str, Any], *, positive: Any = None, t: float | None = None,
              decision: str = "served") -> np.ndarray:
    """Per-row cost of a run's holdout decisions. Binary: at threshold t. Multiclass: decision = served (arg-max,
    the saved y_pred) or bayes (argmin_j Σ_k p̂ₖ·C[k][j])."""
    if cost["kind"] == "binary":
        return _binary_rows(hp, positive, cost, 0.5 if t is None else t)
    names = cost["classes"]
    m = np.asarray(cost["matrix"], dtype=float)
    pos = {c: i for i, c in enumerate(names)}
    yi = hp["y_true"].map(_as_str).map(pos).to_numpy()
    if decision == "bayes":
        P = hp[[f"p_{c}" for c in names]].to_numpy(dtype=float)
        ji = np.argmin(P @ m, axis=1)
    else:
        ji = hp["y_pred"].map(_as_str).map(pos).to_numpy()
    return m[yi.astype(int), ji.astype(int)]


def analysis(hp: pd.DataFrame, cost: dict[str, Any], *, positive: Any = None, current: float | None = None,
             validation_threshold: float | None = None) -> dict[str, Any]:
    """Expected cost of a run's holdout decisions, per 1,000, with intervals; binary adds the cost curve over t."""
    n = len(hp)
    idx = boot_index(n)
    out: dict[str, Any] = {"cost": cost, "n": n, "per": PER, "B": B, "level": LEVEL}
    if cost["kind"] == "binary":
        y, p = binary_arrays(hp, positive)
        t_now = 0.5 if current is None else float(current)
        tb = cost["bayes_threshold"]
        curve = []
        for t in THRESHOLD_GRID:
            yhat = p > t
            tp, fp = int(np.sum(yhat & (y == 1))), int(np.sum(yhat & (y == 0)))
            fn = int(np.sum(~yhat & (y == 1)))
            c = cost["c_fp"] * fp + cost["c_fn"] * fn - cost["g_tp"] * tp
            curve.append({"t": float(t), "per_1000": PER * c / n if n else 0.0, "tp": tp, "fp": fp, "fn": fn,
                          "flagged": tp + fp})
        best = min(curve, key=lambda r: r["per_1000"])

        def at(t: float) -> dict[str, Any]:
            yhat = p > t
            r = _stat(binary_row_costs(y, yhat, cost["c_fp"], cost["c_fn"], cost["g_tp"]), idx)
            r.update(t=float(t), tp=int(np.sum(yhat & (y == 1))), fp=int(np.sum(yhat & (y == 0))),
                     fn=int(np.sum(~yhat & (y == 1))), tn=int(np.sum(~yhat & (y == 0))))
            return r

        out.update(
            positive_class=positive, prevalence=float(y.mean()) if n else 0.0, n_positive=int(y.sum()),
            bayes_threshold=tb, rule="predict positive when p > t",
            deployed=at(t_now), bayes=at(tb), default=at(0.5),
            validation=at(validation_threshold) if validation_threshold is not None else None,
            hindsight={"t": best["t"], "per_1000": best["per_1000"],
                       "note": "the t that minimises cost on the holdout itself: a bound, not a decision rule"},
            flag_none=_stat(binary_row_costs(y, np.zeros(n, bool), cost["c_fp"], cost["c_fn"], cost["g_tp"]), idx),
            flag_all=_stat(binary_row_costs(y, np.ones(n, bool), cost["c_fp"], cost["c_fn"], cost["g_tp"]), idx),
            curve=curve,
        )
    else:
        served = row_costs(hp, cost, decision="served")
        bayes = row_costs(hp, cost, decision="bayes")
        names = cost["classes"]
        m = np.asarray(cost["matrix"], dtype=float)
        pos = {c: i for i, c in enumerate(names)}
        yi = hp["y_true"].map(_as_str).map(pos).to_numpy().astype(int)
        ji = hp["y_pred"].map(_as_str).map(pos).to_numpy().astype(int)
        counts = np.zeros_like(m, dtype=int)
        np.add.at(counts, (yi, ji), 1)
        # the best constant decision: deciding the same class for every row
        const = [{"class": c, **_stat(m[yi, j], idx)} for j, c in enumerate(names)]
        out.update(deployed=_stat(served, idx), bayes=_stat(bayes, idx), counts=counts.tolist(),
                   cost_by_cell=(counts * m).tolist(), best_constant=min(const, key=lambda r: r["per_1000"]))
    return out


def paired(a: np.ndarray, b: np.ndarray, rows_a: np.ndarray, rows_b: np.ndarray) -> dict[str, Any] | None:
    """Δ = cost(b) − cost(a) per 1,000 with a paired bootstrap, when both were scored on the same rows."""
    if len(rows_a) != len(rows_b) or not np.array_equal(np.sort(rows_a), np.sort(rows_b)):
        return None
    ob = pd.Series(b, index=rows_b).loc[rows_a].to_numpy()
    d = ob - a
    idx = boot_index(len(d), seed=1)
    means = d[idx].mean(axis=1)
    lo, hi = boot_mean(d, idx=idx)
    return {"per_1000": PER * float(d.mean()), "ci_per_1000": [PER * lo, PER * hi], "B": B, "level": LEVEL,
            "share_positive": float(np.mean(means > 0))}

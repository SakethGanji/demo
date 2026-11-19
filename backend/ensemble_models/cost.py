"""Business-cost metrics as AutoGluon scorers (F7).

A cost matrix C[i][j] is what one decision costs when the truth is class i and the decision is class j (a gain is a
negative cost). The expected cost per decision of a set of decisions is  E[C] = (1/n) Σᵢ C[yᵢ][ŷᵢ]  (lower is better).

Binary, in the user's words: c_FP (a negative flagged), c_FN (a positive missed), g_TP (gain of a positive caught,
optional), c_TN = 0:  C = [[0, c_FP], [c_FN, −g_TP]]  with rows / columns in AutoGluon's internal order (0 = negative,
1 = positive). Deciding "positive" for a row with P̂(positive) = p costs (1 − p)·c_FP − p·g_TP in expectation, and
"negative" costs p·c_FN, so the Bayes rule is  predict positive when p > t* = c_FP / (c_FP + c_FN + g_TP).

Two scorers per binary cost:
- `binary_cost_scorer(...)`  (`needs_proba`): the cost of deciding by the Bayes rule p > t*. It is the `eval_metric`
  of a cost run, so AutoGluon ranks models, early-stops and weights the ensemble on the cost each model would incur
  with cost-aware decisions. (A label-based scorer would score every model at AutoGluon's t = 0.5, where on a rare
  positive class almost nothing is flagged and every model costs about the same.)
- `binary_cost_class_scorer(...)`  (`needs_class`): the cost of hard predictions. Passed to
  `predictor.calibrate_decision_threshold(metric=...)`, it picks the decision threshold that minimises the expected
  cost on AutoGluon's own validation data.

Multiclass: `multiclass_cost_scorer(matrix)` scores AutoGluon's predicted labels (the arg-max of the probabilities,
what `predict()` serves), with the matrix in AutoGluon's internal class order (the sorted class labels). It is a
`needs_proba` scorer that takes the arg-max itself, because AutoGluon passes a `needs_class` multiclass scorer its
internal integers during the fit but the original labels in `evaluate` / `leaderboard`.

The score functions are module-level functions with plain keyword arguments, so a scorer pickles by reference: fit
workers, bagged child processes and every later `TabularPredictor.load(path)` need only this package importable
(`backend/` on the path), exactly like the TF-IDF models.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

METRIC_NAME = "expected_cost"
CLASS_METRIC_NAME = "expected_cost_at_threshold"


def bayes_threshold(c_fp: float, c_fn: float, g_tp: float = 0.0) -> float:
    d = float(c_fp) + float(c_fn) + float(g_tp)
    return float(c_fp) / d if d > 0 else 0.5


def binary_row_costs(y: np.ndarray, yhat: np.ndarray, c_fp: float, c_fn: float, g_tp: float = 0.0) -> np.ndarray:
    """Cost of each decision: y, yhat ∈ {0, 1} (1 = positive)."""
    y = np.asarray(y).astype(int)
    yhat = np.asarray(yhat).astype(int)
    return np.where(y == 1, np.where(yhat == 1, -float(g_tp), float(c_fn)), np.where(yhat == 1, float(c_fp), 0.0))


def _positive_proba(y_pred_proba) -> np.ndarray:
    p = np.asarray(y_pred_proba, dtype=float)
    return p[:, 1] if p.ndim == 2 else p


def binary_expected_cost_bayes(y_true, y_pred_proba, *, c_fp: float, c_fn: float, g_tp: float = 0.0) -> float:
    """E[C] when each row is decided by the Bayes rule p > t*."""
    p = _positive_proba(y_pred_proba)
    t = bayes_threshold(c_fp, c_fn, g_tp)
    return float(binary_row_costs(y_true, p > t, c_fp, c_fn, g_tp).mean())


def binary_expected_cost_labels(y_true, y_pred, *, c_fp: float, c_fn: float, g_tp: float = 0.0) -> float:
    """E[C] of hard predictions (0 / 1)."""
    return float(binary_row_costs(y_true, y_pred, c_fp, c_fn, g_tp).mean())


def multiclass_expected_cost_argmax(y_true, y_pred_proba, *, matrix: Sequence[Sequence[float]]) -> float:
    """E[C] = mean of C[y][ŷ] with ŷ = argmax_k p̂ₖ (what `predict()` serves); y as AutoGluon's internal integers
    0..K−1 and the probability columns in the same order."""
    m = np.asarray(matrix, dtype=float)
    p = np.asarray(y_pred_proba, dtype=float)
    return float(m[np.asarray(y_true).astype(int), p.argmax(axis=1)].mean())


def _binary_scorer(name: str, score_fn, c_fp: float, c_fn: float, g_tp: float, **kind):
    from autogluon.core.metrics import make_scorer

    return make_scorer(name, score_fn, optimum=-float(g_tp), greater_is_better=False, **kind,
                       c_fp=float(c_fp), c_fn=float(c_fn), g_tp=float(g_tp))


def binary_cost_scorer(c_fp: float, c_fn: float, g_tp: float = 0.0):
    return _binary_scorer(METRIC_NAME, binary_expected_cost_bayes, c_fp, c_fn, g_tp, needs_proba=True)


def binary_cost_class_scorer(c_fp: float, c_fn: float, g_tp: float = 0.0):
    return _binary_scorer(CLASS_METRIC_NAME, binary_expected_cost_labels, c_fp, c_fn, g_tp, needs_class=True)


def multiclass_cost_scorer(matrix: Sequence[Sequence[float]]):
    from autogluon.core.metrics import make_scorer

    m = [[float(v) for v in row] for row in matrix]
    best = sum(min(row) for row in m) / len(m)  # not attainable in general; only a bound for the scorer
    # needs_proba, not needs_class: AutoGluon then hands the scorer its internal class integers both inside the trainer
    # and in predictor.evaluate / leaderboard (a needs_class multiclass scorer gets the original labels there).
    return make_scorer(METRIC_NAME, multiclass_expected_cost_argmax, optimum=min(best, 0.0), greater_is_better=False,
                       needs_proba=True, matrix=m)


def reorder_matrix(cost: dict, classes: Sequence | None) -> list[list[float]]:
    names = [str(c) for c in cost["classes"]]
    if classes is None:
        return [[float(v) for v in row] for row in cost["matrix"]]
    idx = [names.index(str(c)) for c in classes]
    return [[float(cost["matrix"][i][j]) for j in idx] for i in idx]

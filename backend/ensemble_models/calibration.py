"""D4 · A served binary predictor whose probabilities are Platt-recalibrated: p' = σ(a · logit p + b).

`apply_platt(predictor, a, b)` swaps the predictor's learner for `PlattLearner` (a `DefaultLearner` that applies the
map to the positive-class probability right after the models) and stores (a, b) on it; `predictor.save()` then
pickles both into learner.pkl. Every caller of `predict_proba` / `predict` / `evaluate` on the loaded predictor
(Try It, the REST API, batch scoring, the shadow, the build's holdout metrics) gets the calibrated probabilities,
and the decision threshold is read on the calibrated scale. A predictor saved this way loads once this package is
importable (as for the custom models).
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
from autogluon.tabular.learner.default_learner import DefaultLearner


def platt(p: np.ndarray, a: float, b: float) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return 1 / (1 + np.exp(-(a * np.log(p / (1 - p)) + b)))


def formula(a: float, b: float) -> str:
    """σ(1.0733 · logit p − 1.2080)"""
    return f"σ({float(a):.4f} · logit p {'−' if float(b) < 0 else '+'} {abs(float(b)):.4f})"


class PlattLearner(DefaultLearner):
    ensemble_platt: dict[str, Any] | None = None

    def predict_proba(self, X, model: str | None = None, as_pandas: bool = True, as_multiclass: bool = True,
                      inverse_transform: bool = True, transform_features: bool = True):
        cal = self.ensemble_platt
        if not cal:
            return super().predict_proba(X, model=model, as_pandas=as_pandas, as_multiclass=as_multiclass,
                                         inverse_transform=inverse_transform, transform_features=transform_features)
        X_index = copy.deepcopy(X.index) if as_pandas else None
        if X.empty:
            y = np.array([])
        else:
            if transform_features:
                X = self.transform_features(X)
            y = self.load_trainer().predict_proba(X, model=model)  # binary: p of the positive class, 1-D
            y = platt(y, float(cal["a"]), float(cal["b"]))
        return self._post_process_predict_proba(y_pred_proba=y, as_pandas=as_pandas, index=X_index,
                                                as_multiclass=as_multiclass, inverse_transform=inverse_transform)


def apply_platt(predictor, a: float, b: float, **info: Any) -> None:
    """Make a loaded binary predictor serve σ(a · logit p + b) (call predictor.save() to keep it)."""
    if predictor.problem_type != "binary":
        raise ValueError("Platt recalibration applies to binary predictors only.")
    if not a > 0:
        raise ValueError("Platt slope a must be > 0 (the ranking is kept).")
    lr = predictor._learner
    if type(lr) not in (DefaultLearner, PlattLearner):
        raise ValueError(f"Cannot recalibrate a {type(lr).__name__}.")
    lr.__class__ = PlattLearner
    lr.ensemble_platt = {"method": "platt", "a": float(a), "b": float(b), **info}


def calibration_of(predictor) -> dict[str, Any] | None:
    """The Platt map a predictor serves, or None."""
    return getattr(predictor._learner, "ensemble_platt", None)

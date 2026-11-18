"""Load saved predictors in the API process (lazily, a few at a time) to predict, read out-of-fold predictions
and compute feature importance on demand. AutoGluon is imported on first use, not at API start."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .jsonsafe import jsonable

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


class ServingError(ValueError):
    """Shown to the user as a 400."""


class PredictorCache:
    def __init__(self, size: int = 4) -> None:
        self.size = size
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: str):
        with self._lock:
            if path in self._cache:
                self._cache.move_to_end(path)
                return self._cache[path]
        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401 — custom models must be importable to unpickle them

        p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False)
        with self._lock:
            self._cache[path] = p
            while len(self._cache) > self.size:
                self._cache.popitem(last=False)
        return p


def _frame(predictor, rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise ServingError("Give at least one row.")
    df = pd.DataFrame(rows)
    features = list(predictor.original_features)
    missing = [c for c in features if c not in df.columns]
    for c in missing:
        df[c] = np.nan
    return df[features]


def predict(predictor, rows: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
    if model is not None and model not in predictor.model_names():
        raise ServingError(f"No model '{model}' in this run.")
    X = _frame(predictor, rows)
    given = set().union(*(r.keys() for r in rows))
    missing = [c for c in predictor.original_features if c not in given]
    out: dict[str, Any] = {"model": model or predictor.model_best, "problem_type": predictor.problem_type,
                           "n_rows": len(X), "missing_columns": missing}
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba(X, model=model)
        pred = predictor.predict_from_proba(proba)
        out["classes"] = jsonable(list(proba.columns))
        out["predictions"] = jsonable(pred.tolist())
        out["probabilities"] = [{str(c): float(v) for c, v in r.items()} for r in proba.to_dict(orient="records")]
        if predictor.problem_type == "binary":
            out["positive_class"] = jsonable(predictor.positive_class)
    else:
        out["predictions"] = jsonable(predictor.predict(X, model=model).tolist())
        out["probabilities"] = None
    return out


def oof(predictor, model: str | None = None) -> dict[str, Any]:
    if not predictor._trainer.bagged_mode:
        raise ServingError("This run has no out-of-fold predictions (it was fit without bagging: num_bag_folds = 0).")
    if model is not None and model not in predictor.model_names():
        raise ServingError(f"No model '{model}' in this run.")
    model = model or predictor.model_best
    if model.endswith("_FULL"):
        raise ServingError("Refit (_FULL) models have no out-of-fold predictions; ask for the bagged model.")
    out: dict[str, Any] = {"model": model, "problem_type": predictor.problem_type}
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba_oof(model=model)
        out["rows"] = [int(i) for i in proba.index]
        out["classes"] = jsonable(list(proba.columns))
        out["probabilities"] = jsonable(proba.to_numpy().round(6))
        out["predictions"] = jsonable(predictor.predict_from_proba(proba).tolist())
    else:
        pred = predictor.predict_oof(model=model)
        out["rows"] = [int(i) for i in pred.index]
        out["predictions"] = jsonable(pred.tolist())
    return out


def feature_importance(predictor, holdout: pd.DataFrame, time_limit: float = 60) -> list[dict[str, Any]]:
    fi = predictor.feature_importance(holdout, subsample_size=min(2000, len(holdout)), num_shuffle_sets=3,
                                      time_limit=time_limit, silent=True)
    return jsonable(fi.reset_index().rename(columns={"index": "feature"}))


def holdout_frame(csv_path: Path, splits: dict[str, Any], label: str, drop: list[str]) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    h = df.loc[np.asarray(splits["holdout"], dtype=int)]
    h = h[h[label].notna()]
    return h.drop(columns=[c for c in drop if c in h.columns and c != label])

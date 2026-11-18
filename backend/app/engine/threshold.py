"""The decision threshold of a binary run: calibrate it with AutoGluon, set it, persist it in the predictor, and
re-score the holdout at it.

AutoGluon predicts the positive class when p > t (`predictor.decision_threshold`, default 0.5). For imbalanced labels
0.5 is rarely right: with 8% positives few rows ever reach it, so recall and F1 collapse. `calibrate_decision_threshold`
searches t on AutoGluon's own validation data (the tuning split or out-of-fold predictions, never the holdout) to
maximise a metric; this module applies the result and records where it came from.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .explain import binary_arrays, counts_at, load_holdout
from .jsonsafe import jsonable

# Metrics worth calibrating a threshold for (they depend on the hard prediction).
CALIBRATION_METRICS = ("f1", "balanced_accuracy", "mcc", "accuracy", "precision", "recall")


def calibrate(predictor, metric: str) -> float:
    if predictor.problem_type != "binary":
        raise ValueError("A decision threshold applies to binary classification only.")
    if metric not in CALIBRATION_METRICS:
        raise ValueError(f"Calibrate for one of {', '.join(CALIBRATION_METRICS)} (got {metric!r}).")
    return float(predictor.calibrate_decision_threshold(metric=metric, verbose=False))


def apply(predictor, run_dir: Path, holdout: pd.DataFrame, label: str, value: float, *, source: str,
          metric: str | None, seconds: float | None = None) -> dict[str, Any]:
    """Set the threshold, save the predictor, rewrite the holdout predictions' y_pred, and return the record
    (with the holdout counts at 0.5 and at t) plus the re-scored holdout. The caller stores both."""
    from .worker import holdout_metrics  # one definition of the holdout result

    if not 0.0 <= value <= 1.0:
        raise ValueError("The threshold must be between 0 and 1.")
    predictor.set_decision_threshold(float(value))
    predictor.save()
    hp = load_holdout(run_dir)
    pos = predictor.positive_class
    y, p = binary_arrays(hp, pos)
    neg = [c for c in predictor.class_labels if c != pos][0]
    hp["y_pred"] = np.where(p > value, pos, neg)
    hp.to_csv(Path(run_dir) / "holdout_predictions.csv", index=False)
    rec = record(predictor, hp, source=source, metric=metric, seconds=seconds)
    return {"threshold": rec, "holdout": holdout_metrics(predictor, holdout, label)}


def record(predictor, hp: pd.DataFrame, *, source: str, metric: str | None, seconds: float | None = None) -> dict[str, Any]:
    y, p = binary_arrays(hp, predictor.positive_class)
    t = float(predictor.decision_threshold)
    return jsonable({
        "value": t, "default": 0.5, "source": source, "metric": metric, "seconds": seconds,
        "positive_class": predictor.positive_class, "rule": "predict positive when p > t",
        "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "holdout_at_default": counts_at(y, p, 0.5), "holdout_at_threshold": counts_at(y, p, t),
    })


def minority_share(y: pd.Series) -> float:
    vc = y.value_counts(normalize=True)
    return float(vc.min()) if len(vc) else 1.0

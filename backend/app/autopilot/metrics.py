"""Requirement metrics computed from a run's holdout predictions against the current labels.

Every step's holdout predictions are re-scored against the same truth (the newest dataset version's labels, on the
holdout rows it kept), so Δ against the baseline compares like with like after the inbox corrected labels.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

QUALITY = {
    "f1_macro": ("Macro F₁", "F̄₁", ">="),
    "accuracy": ("Accuracy", "acc", ">="),
    "balanced_accuracy": ("Balanced accuracy", "BA", ">="),
    "mcc": ("MCC", "MCC", ">="),
    "roc_auc": ("ROC AUC", "AUC", ">="),
    "f1": ("F₁ (positive)", "F₁", ">="),
    "r2": ("R²", "R²", ">="),
    "rmse": ("RMSE", "RMSE", "<="),
    "mae": ("MAE", "MAE", "<="),
}
LATENCY = {"p95_latency_ms": ("p95 latency per row", "τ₉₅", "<="), "p50_latency_ms": ("median latency per row", "τ₅₀", "<=")}
RECALL = re.compile(r"^recall\[(.+)\]$")


def describe(metric: str) -> dict[str, str]:
    if metric in QUALITY:
        name, sym, op = QUALITY[metric]
    elif metric in LATENCY:
        name, sym, op = LATENCY[metric]
    elif (m := RECALL.match(metric)):
        name, sym, op = f"{m.group(1)} recall", f"r({m.group(1)})", ">="
    else:
        raise ValueError(f"Unknown requirement metric {metric!r}: use one of {', '.join([*QUALITY, *LATENCY])} or recall[<class>].")
    return {"name": name, "sym": sym, "default_op": op}


def is_latency(metric: str) -> bool:
    return metric in LATENCY


def score(y_true: list[Any], y_pred: list[Any], proba: list[list[float]] | None, classes: list[str] | None,
          problem_type: str, positive: str | None = None) -> dict[str, float]:
    from sklearn import metrics as M

    if problem_type == "regression":
        yt, yp = np.asarray(y_true, float), np.asarray(y_pred, float)
        return {"r2": float(M.r2_score(yt, yp)), "rmse": float(math.sqrt(M.mean_squared_error(yt, yp))),
                "mae": float(M.mean_absolute_error(yt, yp)), "n": int(len(yt))}
    yt = np.asarray([str(v) for v in y_true])
    yp = np.asarray([str(v) for v in y_pred])
    labels = [str(c) for c in (classes or sorted(set(yt)))]
    out: dict[str, float] = {
        "accuracy": float(M.accuracy_score(yt, yp)),
        "f1_macro": float(M.f1_score(yt, yp, labels=labels, average="macro", zero_division=0)),
        "balanced_accuracy": float(M.balanced_accuracy_score(yt, yp)),
        "mcc": float(M.matthews_corrcoef(yt, yp)),
        "n": int(len(yt)),
    }
    rec = M.recall_score(yt, yp, labels=labels, average=None, zero_division=0)
    for c, r in zip(labels, rec):
        out[f"recall[{c}]"] = float(r)
    if problem_type == "binary" and positive is not None:
        out["f1"] = float(M.f1_score(yt, yp, pos_label=str(positive), zero_division=0))
        if proba is not None and str(positive) in labels:
            p = np.asarray(proba, float)[:, labels.index(str(positive))]
            if len(set(yt)) == 2:
                out["roc_auc"] = float(M.roc_auc_score((yt == str(positive)).astype(int), p))
    return out


def check(requirements: list[dict[str, Any]], m: dict[str, float], latency: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for r in requirements:
        metric, op, target = r["metric"], r["op"], float(r["value"])
        if metric in LATENCY:
            key = "p95_ms" if metric == "p95_latency_ms" else "p50_ms"
            obs = (latency or {}).get(key)
        else:
            obs = m.get(metric)
        ok = obs is not None and (obs >= target if op == ">=" else obs <= target)
        gap = None if obs is None else (target - obs if op == ">=" else obs - target)
        out.append({"metric": metric, "op": op, "value": target, "observed": obs, "ok": bool(ok),
                    "gap": gap, **describe(metric)})
    return out

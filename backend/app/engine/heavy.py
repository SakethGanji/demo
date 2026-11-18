"""Job functions that run inside the worker pool (`pool.HeavyPool`). Each takes plain data (paths, the run's
config) and returns JSON-ready results; predictors come from the worker's own cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..splits import load_splits
from . import explain, serving
from .pool import load_predictor


def shap_holdout_row(cfg: dict[str, Any], row: int, target: str | None) -> dict[str, Any]:
    p = load_predictor(cfg["predictor_path"])
    return explain.shap_row(p, cfg, Path(cfg["run_dir"]), row, target)


def permutation_importance(cfg: dict[str, Any], num_shuffle_sets: int) -> tuple[list[dict[str, Any]], int]:
    p = load_predictor(cfg["predictor_path"])
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    return serving.feature_importance(p, h, num_shuffle_sets=num_shuffle_sets, time_limit=120), min(2000, len(h))


def predict_rows(predictor_path: str, rows: list[dict[str, Any]], model: str | None) -> dict[str, Any]:
    return serving.predict(load_predictor(predictor_path), rows, model)


def out_of_fold(predictor_path: str, model: str | None) -> dict[str, Any]:
    return serving.oof(load_predictor(predictor_path), model)


def monotone_check(predictor_path: str, cfg: dict[str, Any], constraints: dict[str, int], model: str | None,
                   rows: int, grid: int, curves: int) -> dict[str, Any]:
    """F8: ICE / PD of one model of a predictor (a run's, or a registered version's) on the run's raw holdout rows."""
    from . import monotone

    p = load_predictor(predictor_path)
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    m = model or p.model_best
    if m not in p.model_names():
        raise serving.ServingError(f"No model '{m}' in this predictor; models: {', '.join(p.model_names())}.")
    out = monotone.ice_check(p, h, constraints, m, rows=rows, grid=grid, curves=curves)
    return {"model": m, "best_model": p.model_best, "problem_type": p.problem_type,
            "score": "P(" + str(p.positive_class) + ")" if p.problem_type == "binary" else "ŷ",
            "rows": min(rows, len(h)), "columns": out, "monotone": all(x.get("monotone", False) for x in out.values()),
            "eps": monotone.EPS}

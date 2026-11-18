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

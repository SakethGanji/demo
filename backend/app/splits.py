"""The fixed holdout and the 5 folds every model trains on (plan §2: identical folds for a fair leaderboard).

Stored per dataset in `splits.json`:

    {"seed": 42, "target": "category", "stratified": true, "holdout_share": 0.2, "n_folds": 5,
     "holdout": [row, ...],                 # sorted row positions held out for the final score
     "fold": [f, ...]}                      # one entry per row: 0..4 for training rows, -1 for holdout

Use `load_splits(path)` and `fold_indices(splits, k)` from the engine; never re-split.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold, StratifiedShuffleSplit, ShuffleSplit


def make_splits(y: pd.Series | None, n_rows: int, seed: int, holdout_share: float, n_folds: int) -> dict[str, Any]:
    idx = np.arange(n_rows)
    labels = None if y is None else y.astype(str).fillna("").to_numpy()
    counts = None if labels is None else pd.Series(labels).value_counts()
    # Stratify when every class can appear in the holdout and in each fold.
    stratified = counts is not None and len(counts) >= 2 and int(counts.min()) >= n_folds + 1

    splitter = (StratifiedShuffleSplit if stratified else ShuffleSplit)(n_splits=1, test_size=holdout_share, random_state=seed)
    train, holdout = next(splitter.split(idx, labels if stratified else None))

    fold = np.full(n_rows, -1, dtype=int)
    kf = (StratifiedKFold if stratified else KFold)(n_splits=n_folds, shuffle=True, random_state=seed)
    for k, (_, val) in enumerate(kf.split(train, labels[train] if stratified else None)):
        fold[train[val]] = k

    return {
        "seed": seed,
        "target": None if y is None else str(y.name),
        "stratified": bool(stratified),
        "holdout_share": holdout_share,
        "n_folds": n_folds,
        "holdout": sorted(int(i) for i in holdout),
        "fold": fold.tolist(),
    }


def summarize(splits: dict[str, Any], y: pd.Series | None) -> dict[str, Any]:
    fold = np.asarray(splits["fold"])
    out: dict[str, Any] = {
        "seed": splits["seed"],
        "target": splits["target"],
        "stratified": splits["stratified"],
        "holdout_share": splits["holdout_share"],
        "holdout_rows": len(splits["holdout"]),
        "n_folds": splits["n_folds"],
        "fold_rows": [int((fold == k).sum()) for k in range(splits["n_folds"])],
    }
    if y is not None:
        ys = y.astype(str).to_numpy()
        out["holdout_classes"] = {str(k): int(v) for k, v in pd.Series(ys[splits["holdout"]]).value_counts().items()}
    return out


def save_splits(path: Path, splits: dict[str, Any]) -> None:
    path.write_text(json.dumps(splits))


def load_splits(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def fold_indices(splits: dict[str, Any], k: int) -> tuple[np.ndarray, np.ndarray]:
    """Row positions (train, validation) for fold k. Holdout rows are in neither."""
    fold = np.asarray(splits["fold"])
    return np.flatnonzero((fold >= 0) & (fold != k)), np.flatnonzero(fold == k)

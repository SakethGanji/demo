"""The fixed holdout and the 5 folds every model trains on (plan §2: identical folds for a fair leaderboard).

Stored per dataset in `splits.json`:

    {"seed": 42, "target": "category", "stratified": true, "holdout_share": 0.2, "n_folds": 5,
     "holdout": [row, ...],                 # sorted row positions held out for the final score
     "fold": [f, ...]}                      # one entry per row: 0..4 for training rows, -1 for holdout

Use `load_splits(path)` and `fold_indices(splits, k)` from the engine; never re-split.

F4: a split belongs to a target, not to a dataset. Splits are stored per (target, strategy) under
`data/datasets/<id>/splits/<key>.json` (`split_key`), each recording its own `target`, `strategy` and `key`, and are
drawn once, the first time that target is used (confirmed in the UI or named by a run). Changing the target never
changes another target's split; every run records the key and SHA-256 of the split it used. The only strategy so far
is "random-stratified" (stratified on the target when every class has ≥ n_folds + 1 rows, else random); later
strategies (group, out-of-time) get their own keys without a migration. The legacy `splits.json` (the split drawn on
upload for the profiler's target) is kept unchanged, because runs made before F4 point at it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold, StratifiedShuffleSplit, ShuffleSplit


RANDOM_STRATIFIED = "random-stratified"
STRATEGIES = (RANDOM_STRATIFIED,)


def split_key(target: str | None, strategy: str = RANDOM_STRATIFIED) -> str:
    """File-safe key of a (target, strategy) split: readable slug + hash of the exact column name (names can collide
    once slugged, e.g. "a b" and "a_b")."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown split strategy '{strategy}'. Use one of: {', '.join(STRATEGIES)}.")
    if target is None:
        return f"{strategy}__no-target"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", target).strip("-")[:40] or "col"
    return f"{strategy}__{slug}-{hashlib.sha1(target.encode()).hexdigest()[:8]}"


def split_sha256(splits: dict[str, Any]) -> str:
    """Identity of a split: SHA-256 of its holdout rows and fold assignment (not of the JSON's other fields)."""
    return hashlib.sha256(json.dumps([splits["holdout"], splits["fold"]]).encode()).hexdigest()


def make_splits(y: pd.Series | None, n_rows: int, seed: int, holdout_share: float, n_folds: int,
                strategy: str = RANDOM_STRATIFIED) -> dict[str, Any]:
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

    target = None if y is None else str(y.name)
    return {
        "seed": seed,
        "target": target,
        "strategy": strategy,
        "key": split_key(target, strategy),
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
        "strategy": splits.get("strategy", RANDOM_STRATIFIED),
        "key": splits.get("key") or split_key(splits["target"], splits.get("strategy", RANDOM_STRATIFIED)),
        "sha256": split_sha256(splits),
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

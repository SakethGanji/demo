"""Shared interval statistics: the Wilson score interval and DeLong's AUC variance."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def wilson(k: int, n: int, z: float = 1.959964, empty: Any = None) -> Any:
    """Wilson score interval [lo, hi] for k successes in n trials; `empty` when n = 0 (callers keep their own)."""
    if n <= 0:
        return empty
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def delong_parts(y: np.ndarray, s: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """AUC and DeLong's structural components V10 (per positive) and V01 (per negative), from midranks."""
    from scipy.stats import rankdata

    pos, neg = s[y == 1], s[y == 0]
    m, n = len(pos), len(neg)
    tx, ty, tz = rankdata(pos), rankdata(neg), rankdata(np.concatenate([pos, neg]))
    auc = float((tz[:m].sum() - m * (m + 1) / 2) / (m * n))
    v10 = (tz[:m] - tx) / n
    v01 = 1.0 - (tz[m:] - ty) / m
    return auc, v10, v01


def delong(y: np.ndarray, s: np.ndarray, level: float = 0.95) -> dict[str, Any]:
    """AUC with DeLong's variance: V10ᵢ (each new row: share of reference rows it outranks), V01ⱼ (each reference
    row: share of new rows that outrank it); Var = S10/m + S01/n."""
    from scipy.stats import norm

    y = np.asarray(y).astype(int)
    s = np.asarray(s, dtype=float)
    auc, v10, v01 = delong_parts(y, s)
    m, n = len(v10), len(v01)
    s10, s01 = float(np.var(v10, ddof=1)), float(np.var(v01, ddof=1))
    var = s10 / m + s01 / n
    se = float(np.sqrt(max(var, 0.0)))
    z = float(norm.ppf(0.5 + level / 2))
    return {"value": auc, "se": se, "ci": [max(0.0, auc - z * se), min(1.0, auc + z * se)], "level": level, "z": z,
            "m": m, "n": n, "s10": s10, "s01": s01, "method": "DeLong (midranks)"}

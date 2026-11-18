"""Turn numpy / pandas / AutoGluon values into plain JSON (NaN and ±inf become null)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in (sorted(v, key=str) if isinstance(v, (set, frozenset)) else v)]
    if isinstance(v, np.ndarray):
        return [jsonable(x) for x in v.tolist()]
    if isinstance(v, pd.Series):
        return [jsonable(x) for x in v.tolist()]
    if isinstance(v, pd.DataFrame):
        return [jsonable(r) for r in v.to_dict(orient="records")]
    if v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, type):
        return v.__name__
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)

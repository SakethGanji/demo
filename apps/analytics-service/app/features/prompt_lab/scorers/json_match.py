"""JSON-match scorer — parses both sides and compares structurally.

1.0 iff both parse to equal Python objects (dicts compared key-set + values,
lists compared element-wise after sort-of-equality). Returns 0.0 if either fails to parse.
"""

from __future__ import annotations

import json
from typing import Any


def _parse(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (ValueError, TypeError):
        return None


async def score(prediction: str, expected: Any, **kwargs) -> float:
    pred_obj = _parse(prediction)
    exp_obj = _parse(expected)
    if pred_obj is None or exp_obj is None:
        return 0.0
    return 1.0 if pred_obj == exp_obj else 0.0

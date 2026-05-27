"""Token-level F1 scorer — harmonic mean of token precision and recall."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


async def score(prediction: str, expected: Any, **kwargs) -> float:
    pred_toks = _tokenize(str(prediction))
    exp_toks = _tokenize(str(expected))
    if not pred_toks or not exp_toks:
        return 0.0
    common = Counter(pred_toks) & Counter(exp_toks)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_toks)
    recall = overlap / len(exp_toks)
    return 2 * precision * recall / (precision + recall)

"""Substring-contains scorer — 1.0 iff prediction contains the expected substring (case-insensitive)."""

from __future__ import annotations

from typing import Any


async def score(prediction: str, expected: Any, **kwargs) -> float:
    pred = str(prediction).lower()
    exp = str(expected).lower().strip()
    if not exp:
        return 0.0
    return 1.0 if exp in pred else 0.0

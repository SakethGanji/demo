"""Exact-match scorer — 1.0 iff stripped, lowercased prediction equals expected."""

from __future__ import annotations

from typing import Any


async def score(prediction: str, expected: Any, **kwargs) -> float:
    return 1.0 if str(prediction).strip().lower() == str(expected).strip().lower() else 0.0

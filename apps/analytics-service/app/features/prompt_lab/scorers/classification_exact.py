"""Classification-exact scorer — 1.0 iff stripped, lowercased prediction matches expected.

Same shape as ``exact_match`` but lives under a distinct name so the
classification evaluator can be configured independently from string
prompts that happen to want loose matching for other reasons.
"""

from __future__ import annotations

from typing import Any


async def score(prediction: str, expected: Any, **kwargs) -> float:
    return 1.0 if str(prediction).strip().lower() == str(expected).strip().lower() else 0.0

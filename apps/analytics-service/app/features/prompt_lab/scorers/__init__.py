"""Scorer registry for PromptLab.

Each scorer exposes ``async def score(prediction: str, expected: Any, **kwargs) -> float``
returning a value in [0.0, 1.0]. ``llm_judge`` additionally requires an
``anthropic_client`` kwarg.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import classification_exact, contains, exact_match, f1_token, json_match, llm_judge

Scorer = Callable[..., Awaitable[float]]

SCORERS: dict[str, Scorer] = {
    "exact_match": exact_match.score,
    "contains": contains.score,
    "f1_token": f1_token.score,
    "json_match": json_match.score,
    "llm_judge": llm_judge.score,
    "classification_exact": classification_exact.score,
}


def get_scorer(name: str) -> Scorer:
    """Resolve a scorer by name. Raises KeyError if not found."""
    if name not in SCORERS:
        raise KeyError(f"Unknown scorer: {name!r}. Available: {sorted(SCORERS)}")
    return SCORERS[name]


__all__ = ["SCORERS", "Scorer", "get_scorer"]

"""Mongo-backed budget tracker.

Thin re-export over ``mongo_store`` so the evaluator's import surface stays
the same (``budget.check_budget``, ``budget.add_spend``). Budget state lives
on the session doc as ``budget_spent_usd`` and is mutated atomically via
``$inc``; checks are best-effort (a small overshoot is possible if two
evals race on the same session, but never silent silence — the next call
will see the higher total).
"""

from __future__ import annotations

from . import mongo_store


async def check_budget(
    session_id: str, projected_cost_usd: float, max_cost_usd: float | None = None
) -> tuple[bool, float]:
    """Return ``(allowed, current_spend)``.

    ``max_cost_usd`` is accepted for backwards compatibility but the session's
    own ``max_cost_usd`` is the source of truth.
    """
    allowed, spent, _max = await mongo_store.check_budget(
        session_id, projected_cost_usd
    )
    return allowed, spent


async def add_spend(session_id: str, cost_usd: float) -> float:
    """Atomically increment ``budget_spent_usd`` on the session."""
    return await mongo_store.add_spend(session_id, cost_usd)


async def get_spend(session_id: str) -> float:
    spent, _max = await mongo_store.get_budget(session_id)
    return spent

"""Consensus plug-ins: fn(votes, policy) -> dict | None. `votes` are the effective human votes of one (row, column):
[{actor, source, value, is_null, seq}]. Returns {value, is_null, status, method, agreement} or None (no human decision)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..registry import consensus


def _key(v: dict[str, Any]) -> tuple[Any, int]:
    return (v["value"], v["is_null"])


@consensus("majority")
def majority(votes: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any] | None:
    if len(votes) < int(policy.get("min_votes") or 1):
        return None
    c = Counter(_key(v) for v in votes)
    (win, n), *rest = c.most_common()
    tie = bool(rest) and rest[0][1] == n
    agreement = n / len(votes)
    disputed = len(votes) >= 2 and (tie or agreement < float(policy.get("min_agreement") or "0.66"))
    return {"value": win[0], "is_null": win[1], "status": "disputed" if disputed else "applied",
            "method": "consensus" if len(votes) >= 2 else "single", "agreement": agreement}


@consensus("single")
def single(votes: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any] | None:
    if not votes:
        return None
    v = max(votes, key=lambda x: x["seq"])
    return {"value": v["value"], "is_null": v["is_null"], "status": "applied", "method": "single", "agreement": 1.0}

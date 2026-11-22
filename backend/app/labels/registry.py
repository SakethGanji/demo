"""Plug-in registries (labeling plan §15.1): sources, task types, consensus methods, queue strategies are rows; adding
one is an INSERT (+ its module), never DDL. Readers here; LB-B's tasks / queues / LLM modules register their own."""

from __future__ import annotations

from typing import Any


def sources(con: Any) -> dict[str, dict[str, Any]]:
    return {r["name"]: dict(r) for r in con.execute("SELECT * FROM label_source_types ORDER BY precedence")}


def task_types(con: Any) -> dict[str, dict[str, Any]]:
    return {r["name"]: dict(r) for r in con.execute("SELECT * FROM label_task_types")}


def consensus_methods(con: Any) -> dict[str, dict[str, Any]]:
    return {r["name"]: dict(r) for r in con.execute("SELECT * FROM label_consensus_methods")}


def strategies(con: Any) -> dict[str, dict[str, Any]]:
    return {r["name"]: dict(r) for r in con.execute("SELECT * FROM label_queue_strategies")}


#: consensus plug-ins: name → fn(votes, policy) → (value, is_null, status, method, agreement) | None (see resolve.py)
CONSENSUS: dict[str, Any] = {}


def consensus(name: str):
    def deco(fn):
        CONSENSUS[name] = fn
        return fn
    return deco

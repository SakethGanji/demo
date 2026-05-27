"""Mongo storage for PromptLab sessions, runs, and eval cache.

Three collections, all TTL'd:

- ``sessions``         — one doc per optimization session (config + cumulative spend)
- ``prompt_runs``      — one doc per (session, prompt) evaluation, full metrics
- ``eval_cache``       — idempotency cache: (prompt_hash, dataset_id, model, splits)

All writes are atomic via ``find_one_and_update`` / ``$inc`` so concurrent
calls from a workflow loop don't lose updates. Budget aggregation is
intentionally best-effort (may overshoot by one in-flight call) to avoid
distributed reservation complexity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from app.infra.db.mongo import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Collection names + TTLs
# ---------------------------------------------------------------------------

SESSIONS_COLLECTION = "sessions"
RUNS_COLLECTION = "prompt_runs"
EVAL_CACHE_COLLECTION = "eval_cache"

# TTLs (seconds). Session TTL bumps on every touch via last_accessed_at.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 days
RUN_TTL_SECONDS = 14 * 24 * 60 * 60      # 14 days
EVAL_CACHE_TTL_SECONDS = 24 * 60 * 60    # 24 hours


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Index setup (idempotent)
# ---------------------------------------------------------------------------

async def ensure_indexes() -> None:
    """Create TTL + query indexes. Safe to call repeatedly."""
    db = get_db()
    sessions = db[SESSIONS_COLLECTION]
    runs = db[RUNS_COLLECTION]
    cache = db[EVAL_CACHE_COLLECTION]

    try:
        await sessions.create_index(
            [("last_accessed_at", 1)],
            expireAfterSeconds=SESSION_TTL_SECONDS,
            name="ttl_last_accessed_at",
        )
        await runs.create_index(
            [("created_at", 1)],
            expireAfterSeconds=RUN_TTL_SECONDS,
            name="ttl_created_at",
        )
        await runs.create_index(
            [("session_id", 1), ("created_at", -1)],
            name="ix_session_created",
        )
        await runs.create_index(
            [("session_id", 1), ("prompt_hash", 1)],
            name="ix_session_prompt_hash",
        )
        await cache.create_index(
            [("created_at", 1)],
            expireAfterSeconds=EVAL_CACHE_TTL_SECONDS,
            name="ttl_created_at",
        )
        await cache.create_index(
            [("cache_key", 1)],
            unique=True,
            name="ux_cache_key",
        )
        logger.info("PromptLab Mongo indexes ensured")
    except Exception:
        logger.exception("Failed to ensure PromptLab indexes")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _new_session_id() -> str:
    return f"sess_{secrets.token_hex(8)}"


async def create_session(
    *,
    name: str,
    dataset_id: str,
    intent_classes: list[str],
    target_column: str,
    input_columns: list[str] | None,
    train_split: str = "Train",
    val_split: str = "Test",
    holdout_split: str = "Holdout",
    model: str = "gemini-2.5-flash",
    max_cost_usd: float = 5.0,
    description: str = "",
    extra: dict | None = None,
) -> dict:
    """Insert a new session doc and return it."""
    now = _utc_now()
    doc = {
        "session_id": _new_session_id(),
        "name": name,
        "description": description,
        "dataset_id": dataset_id,
        "intent_classes": list(intent_classes),
        "target_column": target_column,
        "input_columns": list(input_columns) if input_columns else None,
        "splits": {
            "train": train_split,
            "val": val_split,
            "holdout": holdout_split,
        },
        "model": model,
        "max_cost_usd": float(max_cost_usd),
        "budget_spent_usd": 0.0,
        "n_runs": 0,
        "best_run_id": None,
        "best_macro_f1": None,
        "status": "active",
        "extra": extra or {},
        "created_at": now,
        "last_accessed_at": now,
    }
    await get_db()[SESSIONS_COLLECTION].insert_one(doc)
    return doc


async def get_session(session_id: str) -> dict | None:
    """Read a session; bumps ``last_accessed_at`` (and TTL) as a side effect."""
    doc = await get_db()[SESSIONS_COLLECTION].find_one_and_update(
        {"session_id": session_id},
        {"$set": {"last_accessed_at": _utc_now()}},
        return_document=True,  # ReturnDocument.AFTER
    )
    return doc


async def upsert_session_minimal(
    session_id: str, *, max_cost_usd: float
) -> dict:
    """Upsert a minimal session doc — used by /evaluate when the caller
    passes an unknown ``session_id``. ``max_cost_usd`` is set only on
    insert; existing sessions keep their original cap.
    """
    now = _utc_now()
    doc = await get_db()[SESSIONS_COLLECTION].find_one_and_update(
        {"session_id": session_id},
        {
            "$setOnInsert": {
                "session_id": session_id,
                "name": session_id,
                "description": "",
                "max_cost_usd": float(max_cost_usd),
                "budget_spent_usd": 0.0,
                "n_runs": 0,
                "best_run_id": None,
                "best_macro_f1": None,
                "status": "active",
                "created_at": now,
            },
            "$set": {"last_accessed_at": now},
        },
        upsert=True,
        return_document=True,
    )
    return doc


async def list_sessions(limit: int = 50) -> list[dict]:
    cursor = (
        get_db()[SESSIONS_COLLECTION]
        .find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [d async for d in cursor]


async def update_session_best(
    session_id: str, *, run_id: str, macro_f1: float
) -> None:
    """Set best_run_id/best_macro_f1 iff this run beats the current best."""
    await get_db()[SESSIONS_COLLECTION].update_one(
        {
            "session_id": session_id,
            "$or": [
                {"best_macro_f1": None},
                {"best_macro_f1": {"$lt": macro_f1}},
            ],
        },
        {
            "$set": {
                "best_run_id": run_id,
                "best_macro_f1": float(macro_f1),
                "last_accessed_at": _utc_now(),
            },
        },
    )


# ---------------------------------------------------------------------------
# Budget (atomic via $inc)
# ---------------------------------------------------------------------------

async def get_budget(session_id: str) -> tuple[float, float]:
    """Return ``(spent_usd, max_usd)`` for a session, or ``(0, 0)`` if missing."""
    doc = await get_db()[SESSIONS_COLLECTION].find_one(
        {"session_id": session_id},
        {"budget_spent_usd": 1, "max_cost_usd": 1},
    )
    if not doc:
        return 0.0, 0.0
    return float(doc.get("budget_spent_usd", 0.0)), float(doc.get("max_cost_usd", 0.0))


async def check_budget(
    session_id: str, projected_cost_usd: float
) -> tuple[bool, float, float]:
    """Return ``(allowed, current_spend, max_usd)``. Does not mutate state."""
    spent, max_usd = await get_budget(session_id)
    if max_usd <= 0:
        # No budget configured — allow.
        return True, spent, max_usd
    return (spent + projected_cost_usd) <= max_usd, spent, max_usd


async def add_spend(session_id: str, cost_usd: float) -> float:
    """Atomically $inc spend on the session doc; returns the new total."""
    doc = await get_db()[SESSIONS_COLLECTION].find_one_and_update(
        {"session_id": session_id},
        {
            "$inc": {"budget_spent_usd": float(cost_usd)},
            "$set": {"last_accessed_at": _utc_now()},
        },
        return_document=True,
        projection={"budget_spent_usd": 1},
    )
    if not doc:
        return 0.0
    return float(doc.get("budget_spent_usd", 0.0))


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def _new_run_id() -> str:
    return f"run_{secrets.token_hex(8)}"


async def insert_run(
    *,
    session_id: str,
    prompt_hash: str,
    prompt_system: str,
    prompt_user_template: str,
    model: str,
    response: dict,
    parent_run_id: str | None = None,
    strategy: str | None = None,
) -> dict:
    """Persist a completed evaluation as a ``prompt_runs`` doc."""
    now = _utc_now()
    run_id = _new_run_id()
    doc = {
        "run_id": run_id,
        "session_id": session_id,
        "prompt_hash": prompt_hash,
        "prompt_system": prompt_system,
        "prompt_user_template": prompt_user_template,
        "model": model,
        "parent_run_id": parent_run_id,
        "strategy": strategy,
        # Full eval response (metrics + failures + cost + latency).
        "response": response,
        "created_at": now,
    }
    await get_db()[RUNS_COLLECTION].insert_one(doc)
    # Bump n_runs on the session.
    await get_db()[SESSIONS_COLLECTION].update_one(
        {"session_id": session_id},
        {
            "$inc": {"n_runs": 1},
            "$set": {"last_accessed_at": now},
        },
    )
    return doc


async def list_runs(
    session_id: str, *, limit: int = 100, skip: int = 0
) -> list[dict]:
    cursor = (
        get_db()[RUNS_COLLECTION]
        .find({"session_id": session_id}, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    return [d async for d in cursor]


async def find_run_by_prompt_hash(
    session_id: str, prompt_hash: str
) -> dict | None:
    """Return any prior run with the same prompt within this session."""
    return await get_db()[RUNS_COLLECTION].find_one(
        {"session_id": session_id, "prompt_hash": prompt_hash},
        {"_id": 0},
    )


# ---------------------------------------------------------------------------
# Eval cache (idempotency)
# ---------------------------------------------------------------------------

def make_cache_key(
    *,
    prompt_hash: str,
    dataset_id: str,
    model: str,
    intent_classes: list[str],
    evaluation_splits: list[str],
    target_column: str,
    input_columns: list[str] | None,
) -> str:
    """Stable sha256 over all evaluation-affecting inputs."""
    payload = {
        "p": prompt_hash,
        "d": dataset_id,
        "m": model,
        "c": sorted(intent_classes),
        "s": sorted(evaluation_splits),
        "t": target_column,
        "i": sorted(input_columns) if input_columns else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def get_cached_eval(cache_key: str) -> dict | None:
    """Return cached response (a dict) or ``None``."""
    doc = await get_db()[EVAL_CACHE_COLLECTION].find_one(
        {"cache_key": cache_key}, {"_id": 0, "response": 1}
    )
    if doc and "response" in doc:
        return doc["response"]
    return None


async def put_cached_eval(cache_key: str, response: dict) -> None:
    """Upsert a cached response. Idempotent."""
    await get_db()[EVAL_CACHE_COLLECTION].update_one(
        {"cache_key": cache_key},
        {
            "$set": {
                "cache_key": cache_key,
                "response": response,
                "created_at": _utc_now(),
            },
        },
        upsert=True,
    )


# ---------------------------------------------------------------------------
# JSON helpers (convert mongo docs → API-friendly)
# ---------------------------------------------------------------------------

def to_jsonable(doc: Any) -> Any:
    """Strip ``_id`` and convert datetimes to ISO strings recursively."""
    if doc is None:
        return None
    if isinstance(doc, dict):
        return {
            k: to_jsonable(v)
            for k, v in doc.items()
            if k != "_id"
        }
    if isinstance(doc, list):
        return [to_jsonable(v) for v in doc]
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc

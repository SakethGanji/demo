"""F5 routes for the language-model router (app/llm.py): what it would use, how fast it has been, and its settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import llm
from .authz import require  # P19

router = APIRouter(prefix="/api/llm")


@router.get("")
async def llm_status() -> dict[str, Any]:
    """Settings, route (the paths a call would try, in order), SDK / CLI availability, p50/p95 latency per decider over
    the last 200 calls, call and cache counts."""
    return llm.status()


@router.put("/settings", dependencies=[require("admin.config")])
async def llm_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Change timeouts (sdk_timeout, cli_timeout, total_timeout, seconds), cache_ttl, model, enabled, use_sdk, use_cli.
    A null value restores the environment default. Stored in data/llm_settings.json."""
    try:
        llm.update_settings(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from e
    return llm.status()


@router.post("/cache/clear", dependencies=[require("admin.config")])
async def llm_cache_clear() -> dict[str, Any]:
    return {"cleared": llm.clear_cache()}

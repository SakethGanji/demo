"""What this engine can do for a notebook right now (BUILD-PLAN §1.2 ``capabilities.py``).

- ``ai``: an LLM provider is configured (a key or the LLM proxy) or the test fake is installed, and the last Ask did
  not fail with ``llm_unavailable`` in the last ``AI_FAILURE_TTL_S`` seconds (a successful Ask clears that).
- ``browser``: the jobs-service ``/health`` answers within 1 s and the engine has a jobs token (``JOBS_SERVICE_TOKEN``
  or ``JOBS_DEV=1``).
- ``writes``: at least one writable connection exists (in the project, when one is given).
Only setting *names* are read for ``ai``; no value is ever returned.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

AI_FAILURE_TTL_S = 600.0
HEALTH_TIMEOUT_S = 1.0

_last_ai_failure: dict[str, Any] | None = None  # {"at": monotonic, "reason": str}


def note_ai_failure(reason: str) -> None:
    global _last_ai_failure
    _last_ai_failure = {"at": time.monotonic(), "reason": reason[:300]}


def note_ai_success() -> None:
    global _last_ai_failure
    _last_ai_failure = None


def ai_capability() -> dict[str, Any]:
    from ...core.config import settings
    from ...engine import fake_llm

    if fake_llm.ACTIVE is not None:  # the test fake decides (PUT /api/_test/fake-llm/state {unavailable})
        configured = not fake_llm.ACTIVE.unavailable
    else:
        configured = any(bool(getattr(settings, k, None))
                         for k in ("anthropic_api_key", "openai_api_key", "gemini_api_key", "llm_proxy_base_url"))
    if not configured:
        return {"available": False, "reason": "No AI provider is configured on this engine."}
    f = _last_ai_failure
    if f and time.monotonic() - f["at"] < AI_FAILURE_TTL_S:
        return {"available": False, "reason": f"The last Ask could not reach the AI provider: {f['reason']}"}
    return {"available": True, "reason": None}


async def browser_capability() -> dict[str, Any]:
    import httpx

    from ...broker import jobs

    cfg = jobs.config()
    if not cfg.token:
        return {"available": False, "reason": "The jobs service is not configured (JOBS_SERVICE_TOKEN, or JOBS_DEV=1 in dev)."}
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S, follow_redirects=False) as c:
            r = await c.get(cfg.url + "/health")
        if r.status_code == 200:
            return {"available": True, "reason": None}
        return {"available": False, "reason": f"The jobs service health check answered {r.status_code}."}
    except httpx.HTTPError as e:
        return {"available": False, "reason": f"The jobs service is unreachable ({type(e).__name__})."}


async def writes_capability(session: AsyncSession, project_id: str | None = None) -> dict[str, Any]:
    from ...db.connection_models import ProjectConnectionModel as C

    q = select(C.id).where(C.readonly.is_(False))
    if project_id:
        q = q.where(C.project_id == project_id)
    return {"available": (await session.execute(q.limit(1))).first() is not None}


async def capabilities(session: AsyncSession, project_id: str | None = None) -> dict[str, Any]:
    return {"ai": ai_capability(), "browser": await browser_capability(),
            "writes": await writes_capability(session, project_id)}


__all__ = ["ai_capability", "browser_capability", "capabilities", "note_ai_failure", "note_ai_success",
           "writes_capability"]

"""``/api/tasks*``: the approvals inbox (Stage 5, DESIGN-STAGE5-UIS section 7.1). Internal (studio) routes.

Errors use the existing envelope ``{"detail": {"code", "message", "fields"?}}``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.actor import current_actor
from ..core.dependencies import get_db_session
from ..core.rate_limit import client_key, get_rate_limiter
from ..services.task_service import TaskError, TaskService

router = APIRouter(prefix="/tasks")

MAX_DECIDE_BYTES = 32 * 1024


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    comment: str | None = None
    form_data: dict[str, Any] | None = None
    decided_by: str | None = Field(default=None, max_length=200)   # 5.4: refused over 200, never truncated
    idempotency_key: str | None = None  # also accepted in the body: some proxies strip the header


def _http(e: TaskError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail())


@router.get("/counts")
async def task_counts(
    session: Annotated[AsyncSession, Depends(get_db_session)], project_id: str | None = None,
) -> dict[str, Any]:
    """``{open, by_workflow}``: the "3 waiting" badge."""
    return await TaskService(session).counts(project_id)


@router.get("")
async def list_tasks(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    project_id: str | None = None,
    status: str = "open",
    workflow_id: str | None = None,
    assignee: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query()] = 25,
) -> dict[str, Any]:
    try:
        return await TaskService(session).list(project_id=project_id, status=status, workflow_id=workflow_id,
                                               assignee=assignee, cursor=cursor, limit=limit)
    except TaskError as e:
        raise _http(e)


@router.get("/{task_id}")
async def get_task(task_id: str, session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, Any]:
    try:
        return await TaskService(session).get(task_id)
    except TaskError as e:
        raise _http(e)


@router.post("/{task_id}/decide")
async def decide_task(
    task_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Approve or reject. Needs an ``Idempotency-Key`` header (or ``idempotency_key`` in the body; the header wins)."""
    get_rate_limiter().check("task.decide", client_key(request))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_DECIDE_BYTES:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "The decision is too large"})
    raw = await request.body()
    if len(raw) > MAX_DECIDE_BYTES:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "The decision is too large"})
    try:
        body = DecideBody.model_validate_json(raw or b"{}")
    except ValidationError as e:
        fields = {".".join(str(p) for p in err["loc"]) or "body": err["msg"] for err in e.errors()}
        raise HTTPException(422, detail={"code": "VALIDATION_FAILED", "message": "The request body is not valid", "fields": fields})
    actor = current_actor(request, body.decided_by)
    key = (idempotency_key or body.idempotency_key or "").strip()
    try:
        return await TaskService(session).decide(
            task_id, body.decision, body.comment, body.form_data, actor.label, key or None, actor=actor,
        )
    except TaskError as e:
        raise _http(e)

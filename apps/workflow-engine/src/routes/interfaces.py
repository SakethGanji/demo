"""Internal (studio) interface routes: ``/api/workflows/{id}/interface*`` (Stage 5, DESIGN-STAGE5-UIS section 7.1).

Behind the same network controls as the rest of ``/api``. Preview endpoints run the *draft*; the public surface
(``routes/public_interfaces.py``) runs the published version.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.exceptions import WorkflowNotFoundError
from ..schemas.interface import AiEditRequest, ChatSendRequest, ChatSessionCreate, GenerateRequest, SubmitRequest
from ..services.chat_service import ChatService
from ..services.interface_service import InterfaceError, InterfaceService, bearer, clean_idempotency_key
from .public_interfaces import MAX_CHAT_BODY_BYTES, MAX_SUBMIT_BYTES, parse_model, read_json

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_db_session)]


def _http(e: InterfaceError | WorkflowNotFoundError) -> HTTPException:
    if isinstance(e, WorkflowNotFoundError):
        return HTTPException(status_code=404, detail=e.message)
    return HTTPException(status_code=e.status, detail=e.detail(), headers=e.headers)


@router.get("/workflows/{workflow_id}/interface")
async def get_interface(workflow_id: str, session: Session) -> dict[str, Any]:
    try:
        return await InterfaceService(session).get_interface(workflow_id)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.put("/workflows/{workflow_id}/interface")
async def put_interface(workflow_id: str, request: Request, session: Session) -> dict[str, Any]:
    """Full replace; a JSON ``null`` body removes the interface."""
    try:
        raw = await read_json(request, 512 * 1024)
        return await InterfaceService(session).put_interface(workflow_id, raw)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/interface/generate")
async def generate_interface(workflow_id: str, body: GenerateRequest, session: Session) -> dict[str, Any]:
    try:
        return await InterfaceService(session).generate(workflow_id, body.kind, body.slug)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/interface/preview/submit", status_code=202)
async def preview_submit(
    workflow_id: str, request: Request, session: Session,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        body = parse_model(SubmitRequest, await read_json(request, MAX_SUBMIT_BYTES))
        key = clean_idempotency_key(idempotency_key, body.idempotency_key)
        return await InterfaceService(session).preview_submit(workflow_id, body.fields, key)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.get("/workflows/{workflow_id}/interface/preview/data")
async def preview_data(workflow_id: str, session: Session, execution_id: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    try:
        return await InterfaceService(session).preview_data(workflow_id, execution_id)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/interface/screens/{screen_id:path}/ai-edit")
async def ai_edit(workflow_id: str, screen_id: str, body: AiEditRequest, session: Session) -> dict[str, Any]:
    try:
        return await InterfaceService(session).ai_edit(workflow_id, screen_id, body.instruction, body.spec)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.get("/workflows/{workflow_id}/interface/client.ts", response_class=PlainTextResponse)
async def client_ts(workflow_id: str, session: Session) -> PlainTextResponse:
    try:
        text = await InterfaceService(session).client_ts(workflow_id)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.post("/workflows/{workflow_id}/interface/promote")
async def promote(workflow_id: str, session: Session) -> JSONResponse:
    try:
        status, out = await InterfaceService(session).promote(workflow_id)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)
    return JSONResponse(out, status_code=status)


# ── chat preview (draft) ─────────────────────────────────────────────────────────────────────


@router.post("/workflows/{workflow_id}/interface/preview/chat/sessions", status_code=201)
async def preview_chat_create(workflow_id: str, request: Request, session: Session) -> dict[str, Any]:
    try:
        svc = ChatService(session)
        t = await svc.interfaces.resolve_preview(workflow_id, kinds=("chat",))
        raw = await read_json(request, MAX_CHAT_BODY_BYTES)
        body = parse_model(ChatSessionCreate, raw if raw is not None else {})
        return await svc.create_session(t, body.user_label)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.get("/workflows/{workflow_id}/interface/preview/chat/sessions/{session_id}/messages")
async def preview_chat_messages(
    workflow_id: str, session_id: str, session: Session, after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50, authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        svc = ChatService(session)
        t = await svc.interfaces.resolve_preview(workflow_id, kinds=("chat",))
        return await svc.messages(t, session_id, bearer(authorization), after, limit)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)


@router.post("/workflows/{workflow_id}/interface/preview/chat/sessions/{session_id}/messages", status_code=202)
async def preview_chat_send(
    workflow_id: str, session_id: str, request: Request, session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        svc = ChatService(session)
        t = await svc.interfaces.resolve_preview(workflow_id, kinds=("chat",))
        body = parse_model(ChatSendRequest, await read_json(request, MAX_CHAT_BODY_BYTES))
        return await svc.send(t, session_id, bearer(authorization), body.content, body.client_message_id)
    except (InterfaceError, WorkflowNotFoundError) as e:
        raise _http(e)

"""Public (anonymous) interface routes: ``/api/i/{project}/{slug}/...`` (Stage 5, DESIGN-STAGE5-UIS section 7.2).

Narrow on purpose: form schema + submit, run polling, chat. Every miss (unknown project / slug, unpublished,
``access.mode == "studio"``, wrong token, wrong kind) is the same 404. Responses are ``Cache-Control: no-store``,
bodies are capped and strictly validated, the rate-limit hook is called on every route, and nothing here evaluates
caller input as an expression (the run is started with ``substitute_inputs=False``).

Task routes (``/tasks/*``) are not served here: public task approval is off by default (section 6.4); approvers use the
studio inbox (``/api/tasks``).
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import rate_limit
from ..core.dependencies import get_db_session
from ..schemas.interface import ChatSendRequest, ChatSessionCreate, SubmitRequest
from ..services import chat_service
from ..services.chat_service import ChatService
from ..services.interface_service import MAX_SUBMIT_BYTES, InterfaceError, InterfaceService, bearer, clean_idempotency_key

NO_STORE = {"Cache-Control": "no-store"}
MAX_CHAT_BODY_BYTES = 32 * 1024


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/i", dependencies=[Depends(_no_store)])
Session = Annotated[AsyncSession, Depends(get_db_session)]


def http_error(e: InterfaceError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail(), headers={**NO_STORE, **(e.headers or {})})


async def read_json(request: Request, limit: int) -> Any:
    """The JSON body, refused with 413 above ``limit`` bytes and 400 when it isn't JSON (the size is checked before parsing)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise InterfaceError(413, "PAYLOAD_TOO_LARGE", "That request is too large.")
    raw = await request.body()
    if len(raw) > limit:
        raise InterfaceError(413, "PAYLOAD_TOO_LARGE", "That request is too large.")
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise InterfaceError(400, "INVALID_JSON", "The request body isn't valid JSON.")


def parse_model(model: type, data: Any) -> Any:
    try:
        return model.model_validate(data)
    except PydanticValidationError as e:
        fields = {".".join(str(p) for p in x["loc"]) or "body": x["msg"] for x in e.errors()}
        raise InterfaceError(422, "VALIDATION_FAILED", "The request isn't valid.", fields=fields)


@router.get("/{project}/{slug}/schema")
async def get_schema(project: str, slug: str, request: Request, session: Session) -> dict[str, Any]:
    try:
        rate_limit.check(f"iface.poll:{project}:{slug}", request)
        svc = InterfaceService(session)
        return svc.schema_of(await svc.resolve_live(project, slug))
    except InterfaceError as e:
        raise http_error(e)


@router.post("/{project}/{slug}/submit", status_code=202)
async def submit(
    project: str, slug: str, request: Request, session: Session,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        svc = InterfaceService(session)
        t = await svc.resolve_live(project, slug, kinds=("form", "custom"))
        rate_limit.check(f"iface.submit:{project}:{slug}", request)
        body = parse_model(SubmitRequest, await read_json(request, MAX_SUBMIT_BYTES))
        key = clean_idempotency_key(idempotency_key, body.idempotency_key)
        out = await svc.submit(t, body.fields, key)
    except InterfaceError as e:
        raise http_error(e)
    return {**out, "poll_url": f"/api/i/{project}/{slug}/runs/{out['execution_id']}"}


@router.get("/{project}/{slug}/runs/{execution_id}")
async def get_run(
    project: str, slug: str, execution_id: str, request: Request, session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        rate_limit.check(f"iface.poll:{project}:{slug}", request)
        svc = InterfaceService(session)
        t = await svc.resolve_live(project, slug, kinds=("form", "custom"))
        return await svc.run_view(t, execution_id, bearer(authorization))
    except InterfaceError as e:
        raise http_error(e)


# ── chat ─────────────────────────────────────────────────────────────────────────────────────


@router.post("/{project}/{slug}/chat/sessions", status_code=201)
async def create_chat_session(project: str, slug: str, request: Request, session: Session) -> dict[str, Any]:
    try:
        rate_limit.check(f"chat.send:{project}:{slug}", request)
        chat_service.register_hooks()          # idempotent; picks up Builder A's on_complete_hooks as soon as it exists
        svc = ChatService(session)
        t = await svc.interfaces.resolve_live(project, slug, kinds=("chat",))
        raw = await read_json(request, MAX_CHAT_BODY_BYTES)
        body = parse_model(ChatSessionCreate, raw if raw is not None else {})
        return await svc.create_session(t, body.user_label)
    except InterfaceError as e:
        raise http_error(e)


@router.get("/{project}/{slug}/chat/sessions/{session_id}/messages")
async def list_chat_messages(
    project: str, slug: str, session_id: str, request: Request, session: Session,
    after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        rate_limit.check(f"iface.poll:{project}:{slug}", request)
        svc = ChatService(session)
        t = await svc.interfaces.resolve_live(project, slug, kinds=("chat",))
        return await svc.messages(t, session_id, bearer(authorization), after, limit)
    except InterfaceError as e:
        raise http_error(e)


@router.post("/{project}/{slug}/chat/sessions/{session_id}/messages", status_code=202)
async def send_chat_message(
    project: str, slug: str, session_id: str, request: Request, session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        svc = ChatService(session)
        t = await svc.interfaces.resolve_live(project, slug, kinds=("chat",))
        rate_limit.check(f"chat.send:{project}:{slug}", request)
        body = parse_model(ChatSendRequest, await read_json(request, MAX_CHAT_BODY_BYTES))
        return await svc.send(t, session_id, bearer(authorization), body.content, body.client_message_id)
    except InterfaceError as e:
        raise http_error(e)

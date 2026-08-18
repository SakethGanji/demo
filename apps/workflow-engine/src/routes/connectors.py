"""Tool-connector routes.

    POST   /api/connectors                      register
    GET    /api/connectors                      list
    GET    /api/connectors/{id}                 read
    PATCH  /api/connectors/{id}                 update
    DELETE /api/connectors/{id}                 remove (tools cascade)
    POST   /api/connectors/{id}/discover        speak to the server, import tools
    GET    /api/connectors/{id}/tools           ?selected=&q=
    PATCH  /api/connectors/{id}/tools           bulk select / deselect
    POST   /api/connectors/{id}/tools/{name}/test   one live call

Identity is per request: ``X-User-Id`` / ``X-Team-Id`` headers are forwarded to
the remote server for discovery and test calls. The connector's stored headers
supply a default; a header on the request overrides it. Nothing is baked in at
registration, because a connector row is shared by a whole team and a baked-in
identity would make every member's calls act as whoever registered it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..nodes.ai.connectors.base import CallerIdentity, ConnectorError
from ..services.connector_service import (
    ConnectorConflictError,
    ConnectorNotFoundError,
    ConnectorService,
)

router = APIRouter(prefix="/connectors")


async def get_connector_service(
    session: AsyncSession = Depends(get_session),
) -> ConnectorService:
    return ConnectorService(session)


ServiceDep = Annotated[ConnectorService, Depends(get_connector_service)]


def _identity(user_id: str | None, team_id: str | None) -> CallerIdentity:
    return CallerIdentity(user_id=user_id, team_id=team_id)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ConnectorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str
    kind: str = "mcp"
    team_id: str = "default"
    tool_prefix: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    spec_url: str | None = None


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    tool_prefix: str | None = None
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    spec_url: str | None = None


class ConnectorResponse(BaseModel):
    id: str
    team_id: str
    name: str
    kind: str
    base_url: str
    tool_prefix: str
    enabled: bool
    status: str
    last_error: str | None = None
    instructions: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    last_discovered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tool_count: int = 0
    selected_count: int = 0
    selected_est_tokens: int = 0


class ConnectorToolResponse(BaseModel):
    tool_name: str
    remote_id: str
    description: str
    input_schema: dict[str, Any]
    optional_args: list[str]
    selected: bool
    read_only: bool
    unsupported_reason: str | None = None
    est_tokens: int
    last_seen_at: datetime
    removed_at: datetime | None = None


class ToolSelectionRequest(BaseModel):
    select: list[str] = Field(default_factory=list)
    deselect: list[str] = Field(default_factory=list)
    select_all: bool | None = None


def _to_response(row: Any, *, tool_count: int = 0, selected_count: int = 0,
                 selected_est_tokens: int = 0) -> ConnectorResponse:
    return ConnectorResponse(
        id=row.id,
        team_id=row.team_id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        tool_prefix=row.tool_prefix or "",
        enabled=row.enabled,
        status=row.status,
        last_error=row.last_error,
        instructions=row.instructions,
        # Header VALUES are secrets (a bearer token lives here). Names are
        # returned so an operator can see what is configured; values are not.
        headers={key: "***" for key in (row.headers or {})},
        config=dict(row.config or {}),
        last_discovered_at=row.last_discovered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tool_count=tool_count,
        selected_count=selected_count,
        selected_est_tokens=selected_est_tokens,
    )


def _tool_to_response(row: Any) -> ConnectorToolResponse:
    return ConnectorToolResponse(
        tool_name=row.tool_name,
        remote_id=row.remote_id,
        description=row.description or "",
        input_schema=row.input_schema or {},
        optional_args=list(row.optional_args or []),
        selected=row.selected,
        read_only=row.read_only,
        unsupported_reason=row.unsupported_reason,
        est_tokens=row.est_tokens,
        last_seen_at=row.last_seen_at,
        removed_at=row.removed_at,
    )


async def _counts(service: ConnectorService, connector_id: str) -> tuple[int, int, int]:
    rows = await service.list_tools(connector_id)
    selected = [r for r in rows if r.selected]
    return len(rows), len(selected), sum(r.est_tokens for r in selected)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=ConnectorResponse, status_code=201)
async def create_connector(
    body: ConnectorCreateRequest, service: ServiceDep
) -> ConnectorResponse:
    """Register a connector. Registration does not contact the server."""
    try:
        row = await service.create(
            name=body.name,
            base_url=body.base_url,
            kind=body.kind,
            team_id=body.team_id,
            tool_prefix=body.tool_prefix,
            headers=body.headers,
            config=body.config,
            spec_url=body.spec_url,
        )
    except ConnectorConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    except ConnectorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return _to_response(row)


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    service: ServiceDep,
    team_id: str = Query("default"),
    kind: str | None = Query(None),
) -> list[ConnectorResponse]:
    rows = await service.list(team_id=team_id, kind=kind)
    out = []
    for row in rows:
        total, selected, tokens = await _counts(service, row.id)
        out.append(_to_response(row, tool_count=total, selected_count=selected,
                                selected_est_tokens=tokens))
    return out


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(connector_id: str, service: ServiceDep) -> ConnectorResponse:
    try:
        row = await service.get(connector_id)
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    total, selected, tokens = await _counts(service, connector_id)
    return _to_response(row, tool_count=total, selected_count=selected,
                        selected_est_tokens=tokens)


@router.patch("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: str, body: ConnectorUpdateRequest, service: ServiceDep
) -> ConnectorResponse:
    try:
        row = await service.update(connector_id, body.model_dump(exclude_unset=True))
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ConnectorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return _to_response(row)


@router.delete("/{connector_id}")
async def delete_connector(connector_id: str, service: ServiceDep) -> dict[str, Any]:
    try:
        await service.delete(connector_id)
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    return {"success": True, "message": f"Connector '{connector_id}' deleted"}


@router.post("/{connector_id}/discover")
async def discover_connector(
    connector_id: str,
    service: ServiceDep,
    x_user_id: Annotated[str | None, Header()] = None,
    x_team_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Contact the server, import every tool it lists — all unselected."""
    try:
        return await service.discover(
            connector_id, identity=_identity(x_user_id, x_team_id)
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ConnectorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/{connector_id}/tools", response_model=list[ConnectorToolResponse])
async def list_connector_tools(
    connector_id: str,
    service: ServiceDep,
    selected: bool | None = Query(None),
    q: str | None = Query(None),
    include_removed: bool = Query(False),
) -> list[ConnectorToolResponse]:
    try:
        rows = await service.list_tools(
            connector_id, selected=selected, q=q, include_removed=include_removed
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    return [_tool_to_response(r) for r in rows]


@router.patch("/{connector_id}/tools")
async def update_tool_selection(
    connector_id: str, body: ToolSelectionRequest, service: ServiceDep
) -> dict[str, Any]:
    """Bulk select/deselect. Only selected tools ever reach an agent."""
    try:
        return await service.set_selection(
            connector_id,
            select_names=body.select,
            deselect_names=body.deselect,
            select_all=body.select_all,
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.post("/{connector_id}/tools/{tool_name}/test")
async def test_connector_tool(
    connector_id: str,
    tool_name: str,
    service: ServiceDep,
    arguments: dict[str, Any] = Body(default_factory=dict),
    x_user_id: Annotated[str | None, Header()] = None,
    x_team_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Call one tool live, exactly as an agent would — pruning included."""
    try:
        return await service.test_tool(
            connector_id,
            tool_name,
            arguments,
            identity=_identity(x_user_id, x_team_id),
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ConnectorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)

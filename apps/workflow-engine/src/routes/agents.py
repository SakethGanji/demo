"""Agent definition routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ValidationError
from ..db.session import get_session
from ..repositories.agent_repository import AgentRepository
from ..repositories.agent_run_repository import AgentRunRepository
from ..schemas.agent import (
    AgentCreateRequest,
    AgentListItem,
    AgentResponse,
    AgentToolsUpdateRequest,
    AgentUpdateRequest,
    AvailableToolItem,
    SessionCreateRequest,
    SessionResponse,
    ToolBindingSchema,
)
from ..schemas.common import SuccessResponse
from ..services.agent_run_service import AgentRunService
from ..services.agent_service import AgentNotFoundError, AgentService

router = APIRouter(prefix="/agents")


# --- Dependency providers (local to this vertical) ---


async def get_agent_repository(
    session: AsyncSession = Depends(get_session),
) -> AgentRepository:
    return AgentRepository(session)


async def get_agent_run_repository(
    session: AsyncSession = Depends(get_session),
) -> AgentRunRepository:
    return AgentRunRepository(session)


async def get_agent_service(
    agent_repo: AgentRepository = Depends(get_agent_repository),
) -> AgentService:
    return AgentService(agent_repo)


async def get_agent_run_service(
    run_repo: AgentRunRepository = Depends(get_agent_run_repository),
    agent_repo: AgentRepository = Depends(get_agent_repository),
) -> AgentRunService:
    return AgentRunService(run_repo, agent_repo)


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
AgentRunServiceDep = Annotated[AgentRunService, Depends(get_agent_run_service)]


# --- Catalogue -------------------------------------------------------------
# Declared before /{agent_id} — otherwise "tools" is swallowed as an agent id.


@router.get("/tools/available", response_model=list[AvailableToolItem])
async def list_available_tools(service: AgentServiceDep) -> list[AvailableToolItem]:
    """The builtin tools that can be bound to an agent."""
    return service.available_tools()


# --- CRUD ------------------------------------------------------------------


@router.get("", response_model=list[AgentListItem])
async def list_agents(
    service: AgentServiceDep,
    team_id: str | None = None,
    folder_id: str | None = None,
    active: bool | None = None,
) -> list[AgentListItem]:
    """List agents, newest activity first."""
    return await service.list_agents(team_id=team_id, folder_id=folder_id, active=active)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    request: AgentCreateRequest,
    service: AgentServiceDep,
) -> AgentResponse:
    """Create an agent, optionally with its initial tool bindings."""
    try:
        return await service.create_agent(request)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str, service: AgentServiceDep) -> AgentResponse:
    """Get one agent with its bindings."""
    try:
        return await service.get_agent(agent_id)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    request: AgentUpdateRequest,
    service: AgentServiceDep,
) -> AgentResponse:
    """Update an agent. Config changes bump ``version``."""
    try:
        return await service.update_agent(agent_id, request)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.delete("/{agent_id}", response_model=SuccessResponse)
async def delete_agent(agent_id: str, service: AgentServiceDep) -> SuccessResponse:
    """Delete an agent, or archive it when sessions still reference it."""
    try:
        await service.delete_agent(agent_id)
        return SuccessResponse(message="Agent deleted")
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


# --- Bindings --------------------------------------------------------------


@router.get("/{agent_id}/tools", response_model=list[ToolBindingSchema])
async def get_agent_tools(
    agent_id: str, service: AgentServiceDep
) -> list[ToolBindingSchema]:
    """The agent's tool bindings, in the order the model sees them."""
    try:
        return await service.get_tools(agent_id)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.put("/{agent_id}/tools", response_model=list[ToolBindingSchema])
async def set_agent_tools(
    agent_id: str,
    request: AgentToolsUpdateRequest,
    service: AgentServiceDep,
) -> list[ToolBindingSchema]:
    """Replace the agent's bindings wholesale. Bumps ``version``."""
    try:
        return await service.set_tools(agent_id, request.tools)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


# --- Sessions --------------------------------------------------------------


@router.get("/{agent_id}/sessions", response_model=list[SessionResponse])
async def list_agent_sessions(
    agent_id: str,
    service: AgentRunServiceDep,
    status: str | None = None,
    limit: int = 50,
) -> list[SessionResponse]:
    """Sessions opened against this agent."""
    return await service.list_sessions(agent_id=agent_id, status=status, limit=limit)


@router.post("/{agent_id}/sessions", response_model=SessionResponse, status_code=201)
async def create_agent_session(
    agent_id: str,
    service: AgentRunServiceDep,
    request: SessionCreateRequest | None = None,
) -> SessionResponse:
    """Open a session, pinning the agent's current version and config."""
    try:
        return await service.create_session(agent_id, request)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

"""FastAPI dependency injection for workflow engine."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings


# --- Database Session Dependency ---


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session."""
    from ..db import get_session

    async for session in get_session():
        yield session


# --- Repository Dependencies ---


def get_workflow_repository(session: AsyncSession = Depends(get_db_session)):
    """Get workflow repository instance."""
    from ..repositories import WorkflowRepository

    return WorkflowRepository(session)


def get_execution_repository(session: AsyncSession = Depends(get_db_session)):
    """Get execution repository instance."""
    from ..repositories import ExecutionRepository

    return ExecutionRepository(session, max_records=settings.max_execution_records)


@lru_cache
def get_node_registry():
    """Get node registry instance."""
    from ..engine.node_registry import node_registry

    return node_registry


# --- Service Dependencies ---


def get_node_service(
    node_registry=Depends(get_node_registry),
):
    """Get node service instance."""
    from ..services.node_service import NodeService

    return NodeService(node_registry)


def get_workflow_service(
    workflow_repo=Depends(get_workflow_repository),
    execution_repo=Depends(get_execution_repository),
    node_service=Depends(get_node_service),
    node_registry=Depends(get_node_registry),
):
    """Get workflow service instance."""
    from ..services.workflow_service import WorkflowService

    return WorkflowService(workflow_repo, execution_repo, node_service, node_registry)


def get_execution_service(
    execution_repo=Depends(get_execution_repository),
    workflow_repo=Depends(get_workflow_repository),
):
    """Get execution service instance."""
    from ..services.execution_service import ExecutionService

    return ExecutionService(execution_repo, workflow_repo)


def get_ai_chat_service(
    node_registry=Depends(get_node_registry),
    workflow_service=Depends(get_workflow_service),
):
    """Get AI chat service instance (per-request, since workflow_service needs DB session)."""
    from ..services.ai_chat_service import AIChatService

    return AIChatService(node_registry, workflow_service)


def get_ai_chat_service_adk(
    node_registry=Depends(get_node_registry),
    workflow_service=Depends(get_workflow_service),
):
    """Get ADK-based AI chat service instance."""
    from ..services.ai_chat_service_adk import AIChatServiceADK

    return AIChatServiceADK(node_registry, workflow_service)

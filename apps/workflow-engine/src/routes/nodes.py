"""Node routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.exceptions import NodeNotFoundError
from ..core.dependencies import get_node_service
from ..services.node_service import NodeService

router = APIRouter(prefix="/nodes")


# Type alias for dependency injection
NodeServiceDep = Annotated[NodeService, Depends(get_node_service)]


@router.get("", response_model=list[dict[str, Any]])
async def list_nodes(
    service: NodeServiceDep,
    group: str | None = Query(None, description="Filter by node group"),
) -> list[dict[str, Any]]:
    """List all available node types with schemas."""
    if group:
        return service.get_nodes_by_group(group)
    return service.list_nodes()


@router.get("/{node_type}", response_model=dict[str, Any])
async def get_node_schema(
    node_type: str,
    service: NodeServiceDep,
) -> dict[str, Any]:
    """Get schema for a specific node type."""
    try:
        return service.get_node(node_type)
    except NodeNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

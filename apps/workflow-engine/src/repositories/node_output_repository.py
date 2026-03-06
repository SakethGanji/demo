"""Repository for per-node output persistence during execution."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlmodel import select

from ..db.models import NodeOutputModel

logger = logging.getLogger(__name__)


class NodeOutputRepository:
    """Write/read node outputs. Append-only during execution."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_output(
        self,
        execution_id: str,
        node_name: str,
        output: list[dict[str, Any]],
        metrics: dict[str, Any] | None,
        status: str,
        run_index: int = 0,
        error: str | None = None,
    ) -> None:
        """Persist a single node's output. Upserts on (execution_id, node_name, run_index)."""
        try:
            # Check for existing
            stmt = select(NodeOutputModel).where(
                NodeOutputModel.execution_id == execution_id,
                NodeOutputModel.node_name == node_name,
                NodeOutputModel.run_index == run_index,
            )
            result = await self._session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.output = output
                existing.metrics = metrics
                existing.status = status
                existing.error = error
            else:
                record = NodeOutputModel(
                    execution_id=execution_id,
                    node_name=node_name,
                    output=output,
                    metrics=metrics,
                    status=status,
                    error=error,
                    run_index=run_index,
                )
                self._session.add(record)

            await self._session.commit()
        except Exception:
            logger.warning(f"Failed to persist node output for {node_name}", exc_info=True)
            await self._session.rollback()

    async def get_outputs(self, execution_id: str) -> list[NodeOutputModel]:
        """Get all outputs for an execution."""
        stmt = (
            select(NodeOutputModel)
            .where(NodeOutputModel.execution_id == execution_id)
            .order_by(NodeOutputModel.created_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_successful_outputs(self, execution_id: str) -> list[NodeOutputModel]:
        """Get successful outputs for retry-from-failure."""
        stmt = (
            select(NodeOutputModel)
            .where(
                NodeOutputModel.execution_id == execution_id,
                NodeOutputModel.status == "success",
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_failed_node(self, execution_id: str) -> NodeOutputModel | None:
        """Get the last failed node output."""
        stmt = (
            select(NodeOutputModel)
            .where(
                NodeOutputModel.execution_id == execution_id,
                NodeOutputModel.status == "error",
            )
            .order_by(NodeOutputModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

"""Broker call log repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import BrokerCallModel


def call_to_dict(c: BrokerCallModel) -> dict[str, Any]:
    return {
        "id": c.id, "execution_id": c.execution_id, "project_id": c.project_id, "environment": c.environment,
        "origin": c.origin, "node_type": c.node_type, "parameters": c.params_redacted, "status": c.status,
        "error_code": c.error_code, "error": c.error, "duration_ms": c.duration_ms,
        "created_at": c.created_at.isoformat(),
    }


class BrokerCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, row: BrokerCallModel) -> None:
        self._s.add(row)
        await self._s.commit()

    async def list(self, execution_id: str | None = None, project_id: str | None = None, limit: int = 200,
                   before: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        stmt = select(BrokerCallModel)
        if status:
            stmt = stmt.where(BrokerCallModel.status == status)
        if before:
            pivot = await self._s.get(BrokerCallModel, before)
            if pivot is not None:
                stmt = stmt.where(BrokerCallModel.created_at < pivot.created_at)
        if execution_id:
            stmt = stmt.where(BrokerCallModel.execution_id == execution_id)
        if project_id:
            stmt = stmt.where(BrokerCallModel.project_id == project_id)
        stmt = stmt.order_by(BrokerCallModel.created_at.desc()).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [call_to_dict(r) for r in rows]

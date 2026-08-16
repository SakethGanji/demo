"""Repository for the API Tester executions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import ApiTestExecutionModel


class ApiTestRepository:
    """CRUD for ApiTestExecutionModel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, model: ApiTestExecutionModel) -> ApiTestExecutionModel:
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return model

    async def get(self, execution_id: str) -> ApiTestExecutionModel | None:
        return await self._session.get(ApiTestExecutionModel, execution_id)

    async def get_many(self, ids: list[str]) -> list[ApiTestExecutionModel]:
        if not ids:
            return []
        stmt = select(ApiTestExecutionModel).where(ApiTestExecutionModel.id.in_(ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list(self, team_id: str = "default", limit: int = 200) -> list[ApiTestExecutionModel]:
        stmt = (
            select(ApiTestExecutionModel)
            .where(ApiTestExecutionModel.team_id == team_id)
            .order_by(ApiTestExecutionModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def rename(self, execution_id: str, name: str | None) -> ApiTestExecutionModel | None:
        row = await self._session.get(ApiTestExecutionModel, execution_id)
        if not row:
            return None
        row.name = name
        row.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, execution_id: str) -> bool:
        row = await self._session.get(ApiTestExecutionModel, execution_id)
        if not row:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

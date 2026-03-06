"""Repository for team-scoped variables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import VariableModel


class VariableRepository:
    """CRUD for VariableModel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        key: str,
        value: str,
        team_id: str = "default",
        type: str = "string",
        description: str | None = None,
    ) -> VariableModel:
        variable = VariableModel(
            team_id=team_id,
            key=key,
            value=value,
            type=type,
            description=description,
        )
        self._session.add(variable)
        await self._session.commit()
        await self._session.refresh(variable)
        return variable

    async def get(self, variable_id: int) -> VariableModel | None:
        return await self._session.get(VariableModel, variable_id)

    async def get_by_key(self, team_id: str, key: str) -> VariableModel | None:
        stmt = select(VariableModel).where(
            VariableModel.team_id == team_id,
            VariableModel.key == key,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list(self, team_id: str = "default") -> list[VariableModel]:
        stmt = (
            select(VariableModel)
            .where(VariableModel.team_id == team_id)
            .order_by(VariableModel.key)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        variable_id: int,
        value: str | None = None,
        description: str | None = ...,
    ) -> VariableModel | None:
        variable = await self._session.get(VariableModel, variable_id)
        if not variable:
            return None
        if value is not None:
            variable.value = value
        if description is not ...:
            variable.description = description
        variable.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(variable)
        return variable

    async def delete(self, variable_id: int) -> bool:
        variable = await self._session.get(VariableModel, variable_id)
        if not variable:
            return False
        await self._session.delete(variable)
        await self._session.commit()
        return True

"""Repository for folder management."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import FolderModel


class FolderRepository:
    """CRUD for FolderModel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        id: str,
        name: str,
        team_id: str = "default",
        parent_folder_id: str | None = None,
        created_by: str | None = None,
    ) -> FolderModel:
        folder = FolderModel(
            id=id,
            team_id=team_id,
            parent_folder_id=parent_folder_id,
            name=name,
            created_by=created_by,
        )
        self._session.add(folder)
        await self._session.commit()
        await self._session.refresh(folder)
        return folder

    async def get(self, folder_id: str) -> FolderModel | None:
        return await self._session.get(FolderModel, folder_id)

    async def list(self, team_id: str = "default") -> list[FolderModel]:
        stmt = (
            select(FolderModel)
            .where(FolderModel.team_id == team_id)
            .order_by(FolderModel.name)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        folder_id: str,
        name: str | None = None,
        parent_folder_id: str | None = ...,
    ) -> FolderModel | None:
        folder = await self._session.get(FolderModel, folder_id)
        if not folder:
            return None
        if name is not None:
            folder.name = name
        if parent_folder_id is not ...:
            folder.parent_folder_id = parent_folder_id
        folder.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(folder)
        return folder

    async def delete(self, folder_id: str) -> bool:
        folder = await self._session.get(FolderModel, folder_id)
        if not folder:
            return False
        await self._session.delete(folder)
        await self._session.commit()
        return True

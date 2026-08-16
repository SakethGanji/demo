"""Repository for encrypted credentials."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import CredentialModel


class CredentialRepository:
    """Standard CRUD on CredentialModel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        id: str,
        name: str,
        type: str,
        data: str,
        team_id: str = "default",
        created_by: str | None = None,
    ) -> CredentialModel:
        cred = CredentialModel(
            id=id,
            name=name,
            type=type,
            data=data,
            team_id=team_id,
            created_by=created_by,
        )
        self._session.add(cred)
        await self._session.commit()
        await self._session.refresh(cred)
        return cred

    async def get(self, credential_id: str) -> CredentialModel | None:
        return await self._session.get(CredentialModel, credential_id)

    async def list(self, team_id: str = "default") -> list[CredentialModel]:
        stmt = (
            select(CredentialModel)
            .where(CredentialModel.team_id == team_id)
            .order_by(CredentialModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        credential_id: str,
        encrypted_data: str | None = None,
        name: str | None = None,
        type: str | None = None,
    ) -> CredentialModel | None:
        cred = await self._session.get(CredentialModel, credential_id)
        if not cred:
            return None
        if encrypted_data is not None:
            cred.data = encrypted_data
        if name is not None:
            cred.name = name
        if type is not None:
            cred.type = type
        cred.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(cred)
        return cred

    async def delete(self, credential_id: str) -> bool:
        cred = await self._session.get(CredentialModel, credential_id)
        if not cred:
            return False
        await self._session.delete(cred)
        await self._session.commit()
        return True

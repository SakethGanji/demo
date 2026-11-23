"""Repository for the API catalog (``api_specs``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiSpecModel


class ApiSpecRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, project_id: str, name: str, base_url: str, spec: dict[str, Any],
                     source: str, source_url: str | None = None) -> ApiSpecModel:
        row = ApiSpecModel(id=f"api_{uuid.uuid4().hex[:12]}", project_id=project_id, name=name, base_url=base_url,
                           spec=spec, source=source, source_url=source_url,
                           created_at=datetime.now(), updated_at=datetime.now())
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get(self, spec_id: str) -> ApiSpecModel | None:
        return await self._session.get(ApiSpecModel, spec_id)

    async def get_many(self, spec_ids: list[str]) -> list[ApiSpecModel]:
        if not spec_ids:
            return []
        res = await self._session.execute(select(ApiSpecModel).where(ApiSpecModel.id.in_(spec_ids)))
        return list(res.scalars().all())

    async def list(self, project_id: str | None = None) -> list[ApiSpecModel]:
        stmt = select(ApiSpecModel).order_by(ApiSpecModel.created_at.desc(), ApiSpecModel.id)
        if project_id:
            stmt = stmt.where(ApiSpecModel.project_id == project_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def delete(self, spec_id: str) -> bool:
        row = await self.get(spec_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

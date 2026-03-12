"""Service layer for app CRUD operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from ..db.models import AppModel, AppVersionModel
from ..schemas.app import (
    AppCreateRequest,
    AppDetailResponse,
    AppListItem,
    AppPublishResponse,
    AppUpdateRequest,
)
from ..utils.ids import app_id


class AppService:
    """Handles app persistence using the dedicated apps table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_apps(self, folder_id: str | None = None) -> list[AppListItem]:
        stmt = select(AppModel)
        if folder_id is not None:
            stmt = stmt.where(AppModel.folder_id == folder_id)
        stmt = stmt.order_by(AppModel.updated_at.desc())
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            AppListItem(
                id=r.id,
                name=r.name,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
            )
            for r in rows
        ]

    async def get_app(self, app_id: str) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, app_id)
        if not row:
            return None
        return self._to_detail(row)

    async def create_app(self, req: AppCreateRequest) -> AppDetailResponse:
        now = datetime.now()
        row = AppModel(
            id=app_id(),
            name=req.name,
            description=req.description,
            folder_id=req.folder_id,
            active=False,
            draft_definition=req.definition,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return self._to_detail(row)

    async def update_app(self, app_id: str, req: AppUpdateRequest) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, app_id)
        if not row:
            return None
        if req.name is not None:
            row.name = req.name
        if req.definition is not None:
            row.draft_definition = req.definition
        if req.description is not None:
            row.description = req.description
        row.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(row)
        return self._to_detail(row)

    async def delete_app(self, app_id: str) -> bool:
        row = await self._session.get(AppModel, app_id)
        if not row:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def publish_app(self, app_id: str) -> AppPublishResponse | None:
        row = await self._session.get(AppModel, app_id)
        if not row:
            return None

        # Get next version number
        stmt = (
            select(AppVersionModel.version_number)
            .where(AppVersionModel.app_id == app_id)
            .order_by(col(AppVersionModel.version_number).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        last = result.scalar_one_or_none()
        next_version = (last or 0) + 1

        version = AppVersionModel(
            app_id=app_id,
            version_number=next_version,
            definition=row.draft_definition,
        )
        self._session.add(version)
        await self._session.flush()

        row.published_version_id = version.id
        row.active = True
        row.published_at = datetime.now()
        row.updated_at = datetime.now()
        await self._session.commit()
        return AppPublishResponse(id=app_id, active=True, version_id=version.id)

    def _to_detail(self, row: AppModel) -> AppDetailResponse:
        return AppDetailResponse(
            id=row.id,
            name=row.name,
            definition=row.draft_definition or {},
            active=row.active,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

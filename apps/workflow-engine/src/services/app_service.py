"""Service layer for app CRUD operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from ..db.models import AppModel, AppVersionModel
from ..schemas.app import (
    AppCreateRequest,
    AppDetailResponse,
    AppFilePayload,
    AppListItem,
    AppPublishResponse,
    AppUpdateRequest,
    AppVersionDetail,
    AppVersionListItem,
    AppVersionResponse,
)
from ..services.file_storage import FileStorageBackend
from ..utils.ids import app_id


class AppService:
    """Handles app persistence using the dedicated apps table."""

    def __init__(self, session: AsyncSession, file_storage: FileStorageBackend) -> None:
        self._session = session
        self._file_storage = file_storage

    # ── List / Get / Create / Update / Delete ────────────────────────────────

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

    async def get_app(self, aid: str) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        return await self._to_detail(row)

    async def create_app(self, req: AppCreateRequest) -> AppDetailResponse:
        now = datetime.now()
        row = AppModel(
            id=app_id(),
            name=req.name,
            description=req.description,
            folder_id=req.folder_id,
            workflow_ids=req.workflow_ids,
            active=False,
            draft_definition=req.definition,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return await self._to_detail(row)

    async def update_app(self, aid: str, req: AppUpdateRequest) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        if req.name is not None:
            row.name = req.name
        if req.definition is not None:
            row.draft_definition = req.definition
        if req.description is not None:
            row.description = req.description
        if req.workflow_ids is not None:
            row.workflow_ids = req.workflow_ids
        if req.source_code is not None:
            row.draft_source_code = req.source_code
        row.updated_at = datetime.now()

        # Atomically create a version alongside the save
        version = None
        if req.create_version and req.source_code is not None:
            files_dicts = [f.model_dump() for f in req.files] if req.files else None
            version = await self._create_version_row(
                row,
                source_code=req.source_code,
                trigger=req.version_trigger,
                prompt=req.version_prompt,
                files=files_dicts,
            )

        await self._session.commit()
        await self._session.refresh(row)
        if version:
            await self._session.refresh(version)
        return await self._to_detail(row)

    async def delete_app(self, aid: str) -> bool:
        row = await self._session.get(AppModel, aid)
        if not row:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def publish_app(self, aid: str) -> AppPublishResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None

        source = row.draft_source_code or ""
        version = await self._create_version_row(
            row,
            source_code=source,
            trigger="publish",
        )
        await self._session.flush()

        row.published_version_id = version.id
        row.active = True
        row.published_at = datetime.now()
        row.updated_at = datetime.now()
        await self._session.commit()
        return AppPublishResponse(id=aid, active=True, version_id=version.id)

    # ── Version CRUD ─────────────────────────────────────────────────────────

    async def create_version(
        self,
        aid: str,
        source_code: str,
        trigger: str = "manual",
        label: str | None = None,
        prompt: str | None = None,
        message: str | None = None,
    ) -> AppVersionDetail | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        version = await self._create_version_row(
            row,
            source_code=source_code,
            trigger=trigger,
            label=label,
            prompt=prompt,
            message=message,
        )
        await self._session.commit()
        await self._session.refresh(version)
        return self._version_to_detail(version)

    async def list_versions(self, aid: str) -> list[AppVersionListItem]:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid)
            .order_by(col(AppVersionModel.version_number).asc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            AppVersionListItem(
                id=v.id,  # type: ignore[arg-type]
                version_number=v.version_number,
                parent_version_id=v.parent_version_id,
                trigger=v.trigger,
                label=v.label,
                prompt=v.prompt,
                message=v.message,
                created_at=v.created_at.isoformat(),
            )
            for v in rows
        ]

    async def get_version(self, aid: str, version_id: int) -> AppVersionDetail | None:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None
        detail = self._version_to_detail(version)
        # Populate files
        file_dicts = await self._file_storage.get_files(version.id)  # type: ignore[arg-type]
        detail.files = [AppFilePayload(**f) for f in file_dicts]
        return detail

    async def get_version_files(self, aid: str, version_id: int) -> list[dict] | None:
        """Get files for a specific version."""
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None

        return await self._file_storage.get_files(version.id)  # type: ignore[arg-type]

    async def revert_to_version(self, aid: str, version_id: int) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None

        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None

        row.current_version_id = version.id
        row.draft_source_code = version.source_code
        row.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(row)
        return await self._to_detail(row)

    async def update_version_label(
        self, aid: str, version_id: int, label: str | None
    ) -> AppVersionListItem | None:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None
        version.label = label
        await self._session.commit()
        await self._session.refresh(version)
        return AppVersionListItem(
            id=version.id,  # type: ignore[arg-type]
            version_number=version.version_number,
            parent_version_id=version.parent_version_id,
            trigger=version.trigger,
            label=version.label,
            prompt=version.prompt,
            message=version.message,
            created_at=version.created_at.isoformat(),
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _next_version_number(self, aid: str) -> int:
        stmt = (
            select(AppVersionModel.version_number)
            .where(AppVersionModel.app_id == aid)
            .order_by(col(AppVersionModel.version_number).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        last = result.scalar_one_or_none()
        return (last or 0) + 1

    async def _create_version_row(
        self,
        app: AppModel,
        *,
        source_code: str,
        trigger: str,
        label: str | None = None,
        prompt: str | None = None,
        message: str | None = None,
        files: list[dict[str, str]] | None = None,
    ) -> AppVersionModel:
        next_num = await self._next_version_number(app.id)
        version = AppVersionModel(
            app_id=app.id,
            version_number=next_num,
            parent_version_id=app.current_version_id,
            definition=app.draft_definition or {},
            source_code=source_code,
            trigger=trigger,
            label=label,
            prompt=prompt,
            message=message,
        )
        self._session.add(version)
        await self._session.flush()  # get version.id

        if files:
            await self._file_storage.save_files(version.id, files)  # type: ignore[arg-type]

        app.current_version_id = version.id
        app.draft_source_code = source_code
        return version

    async def _get_current_version(self, app: AppModel) -> AppVersionResponse | None:
        if not app.current_version_id:
            return None
        version = await self._session.get(AppVersionModel, app.current_version_id)
        if not version:
            return None
        return AppVersionResponse(
            id=version.id,  # type: ignore[arg-type]
            version_number=version.version_number,
            parent_version_id=version.parent_version_id,
            trigger=version.trigger,
            label=version.label,
            prompt=version.prompt,
            message=version.message,
            created_at=version.created_at.isoformat(),
        )

    async def _to_detail(self, row: AppModel) -> AppDetailResponse:
        current_version = await self._get_current_version(row)
        # Populate files from current version
        files: list[AppFilePayload] = []
        if row.current_version_id:
            file_dicts = await self._file_storage.get_files(row.current_version_id)
            files = [AppFilePayload(**f) for f in file_dicts]
        return AppDetailResponse(
            id=row.id,
            name=row.name,
            definition=row.draft_definition or {},
            active=row.active,
            workflow_ids=row.workflow_ids or [],
            source_code=row.draft_source_code,
            files=files,
            current_version=current_version,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    @staticmethod
    def _version_to_detail(v: AppVersionModel) -> AppVersionDetail:
        return AppVersionDetail(
            id=v.id,  # type: ignore[arg-type]
            version_number=v.version_number,
            parent_version_id=v.parent_version_id,
            trigger=v.trigger,
            label=v.label,
            prompt=v.prompt,
            message=v.message,
            source_code=v.source_code,
            created_at=v.created_at.isoformat(),
        )

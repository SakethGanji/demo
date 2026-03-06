"""Repository for workflow version persistence."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from ..db.models import WorkflowVersionModel, WorkflowModel


class VersionRepository:
    """CRUD for immutable workflow versions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(
        self,
        workflow_id: str,
        definition: dict,
        message: str | None = None,
        created_by: str | None = None,
    ) -> WorkflowVersionModel:
        """Create a new immutable version from the current draft."""
        # Get next version number
        stmt = (
            select(WorkflowVersionModel.version_number)
            .where(WorkflowVersionModel.workflow_id == workflow_id)
            .order_by(col(WorkflowVersionModel.version_number).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        last = result.scalar_one_or_none()
        next_version = (last or 0) + 1

        version = WorkflowVersionModel(
            workflow_id=workflow_id,
            version_number=next_version,
            definition=definition,
            message=message,
            created_by=created_by,
        )
        self._session.add(version)
        await self._session.flush()  # get the ID without committing
        return version

    async def get(self, version_id: int) -> WorkflowVersionModel | None:
        return await self._session.get(WorkflowVersionModel, version_id)

    async def get_latest(self, workflow_id: str) -> WorkflowVersionModel | None:
        stmt = (
            select(WorkflowVersionModel)
            .where(WorkflowVersionModel.workflow_id == workflow_id)
            .order_by(col(WorkflowVersionModel.version_number).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions(self, workflow_id: str) -> list[WorkflowVersionModel]:
        stmt = (
            select(WorkflowVersionModel)
            .where(WorkflowVersionModel.workflow_id == workflow_id)
            .order_by(col(WorkflowVersionModel.version_number).desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

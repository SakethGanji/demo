"""Repository for team-scoped, environment-scoped variables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import VariableModel
from ..services.variable_crypto import encrypt_secret, decrypt_secret


class VariableRepository:
    """CRUD for VariableModel. Secrets are encrypted at rest, decrypted on read."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        key: str,
        value: str,
        team_id: str = "default",
        environment: str = "default",
        type: str = "string",
        description: str | None = None,
        project_id: str | None = None,
        allowed_hosts: list[str] | None = None,
    ) -> VariableModel:
        stored_value = encrypt_secret(value) if type == "secret" else value
        variable = VariableModel(
            team_id=team_id,
            environment=environment,
            key=key,
            value=stored_value,
            type=type,
            description=description,
            project_id=project_id,
            allowed_hosts=allowed_hosts,
        )
        self._session.add(variable)
        await self._session.commit()
        await self._session.refresh(variable)
        return variable

    async def get(self, variable_id: int) -> VariableModel | None:
        return await self._session.get(VariableModel, variable_id)

    async def get_by_key(
        self, team_id: str, key: str, environment: str = "default", project_id: str | None = None,
    ) -> VariableModel | None:
        """Exact-scope lookup: project_id=None means the team-shared row."""
        stmt = select(VariableModel).where(
            VariableModel.team_id == team_id,
            VariableModel.environment == environment,
            VariableModel.key == key,
            self._scope(project_id),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    def _scope(project_id: str | None):
        return VariableModel.project_id.is_(None) if project_id is None else VariableModel.project_id == project_id

    async def list(
        self, team_id: str = "default", environment: str | None = None,
        project_id: str | None = None, include_team: bool = False,
    ) -> list[VariableModel]:
        """Rows of one scope (team when project_id is None), or team+project when include_team."""
        stmt = select(VariableModel).where(VariableModel.team_id == team_id)
        if project_id is not None and include_team:
            stmt = stmt.where((VariableModel.project_id.is_(None)) | (VariableModel.project_id == project_id))
        else:
            stmt = stmt.where(self._scope(project_id))
        if environment is not None:
            stmt = stmt.where(VariableModel.environment == environment)
        stmt = stmt.order_by(VariableModel.environment, VariableModel.key)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_environments(self, team_id: str = "default") -> list[str]:
        """Distinct environment names that have at least one variable defined."""
        stmt = (
            sa_select(VariableModel.environment)
            .where(VariableModel.team_id == team_id, VariableModel.project_id.is_(None))
            .distinct()
            .order_by(VariableModel.environment)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def load_runtime_map(
        self, team_id: str = "default", environment: str = "default", project_id: str | None = None,
    ) -> dict[str, str]:
        """Decrypted {key: value} map for a single environment — for the engine.

        Team rows first, then project rows (project wins on collisions).
        Never return this through the API."""
        variables = await self.list(team_id=team_id, environment=environment)
        out = {
            v.key: (decrypt_secret(v.value) if v.type == "secret" else v.value)
            for v in variables
        }
        if project_id is not None:
            for v in await self.list(team_id=team_id, environment=environment, project_id=project_id):
                out[v.key] = decrypt_secret(v.value) if v.type == "secret" else v.value
        return out

    async def load_runtime_rows(
        self, team_id: str = "default", environment: str = "default", project_id: str | None = None,
    ) -> dict[str, VariableModel]:
        """Effective {key: row} map (project row wins) — for policy checks (allowed_hosts)."""
        out = {v.key: v for v in await self.list(team_id=team_id, environment=environment)}
        if project_id is not None:
            for v in await self.list(team_id=team_id, environment=environment, project_id=project_id):
                out[v.key] = v
        return out

    async def load_runtime_secret_values(
        self, team_id: str = "default", environment: str = "default", project_id: str | None = None,
    ) -> set[str]:
        """Decrypted set of `type=secret` values — for log/UI redaction.

        Used by the engine to scrub these values out of execution metadata
        (e.g. resolved request URLs) before they reach `node_outputs` or SSE.
        Empty values are filtered out so we never match the empty string."""
        stmt = select(VariableModel).where(
            VariableModel.team_id == team_id,
            VariableModel.environment == environment,
            VariableModel.type == "secret",
        )
        if project_id is None:
            stmt = stmt.where(VariableModel.project_id.is_(None))
        else:
            # Union of team + project secrets (incl. shadowed team values) — strict redaction.
            stmt = stmt.where((VariableModel.project_id.is_(None)) | (VariableModel.project_id == project_id))
        result = await self._session.execute(stmt)
        return {decrypt_secret(v.value) for v in result.scalars().all() if v.value}

    async def update(
        self,
        variable_id: int,
        value: str | None = None,
        description: str | None = ...,
        allowed_hosts: list[str] | None = ...,
    ) -> VariableModel | None:
        variable = await self._session.get(VariableModel, variable_id)
        if not variable:
            return None
        if value is not None:
            variable.value = encrypt_secret(value) if variable.type == "secret" else value
        if description is not ...:
            variable.description = description
        if allowed_hosts is not ...:
            variable.allowed_hosts = allowed_hosts
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

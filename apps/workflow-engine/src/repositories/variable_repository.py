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
    ) -> VariableModel:
        stored_value = encrypt_secret(value) if type == "secret" else value
        variable = VariableModel(
            team_id=team_id,
            environment=environment,
            key=key,
            value=stored_value,
            type=type,
            description=description,
        )
        self._session.add(variable)
        await self._session.commit()
        await self._session.refresh(variable)
        return variable

    async def get(self, variable_id: int) -> VariableModel | None:
        return await self._session.get(VariableModel, variable_id)

    async def get_by_key(
        self, team_id: str, key: str, environment: str = "default"
    ) -> VariableModel | None:
        stmt = select(VariableModel).where(
            VariableModel.team_id == team_id,
            VariableModel.environment == environment,
            VariableModel.key == key,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list(
        self, team_id: str = "default", environment: str | None = None
    ) -> list[VariableModel]:
        stmt = select(VariableModel).where(VariableModel.team_id == team_id)
        if environment is not None:
            stmt = stmt.where(VariableModel.environment == environment)
        stmt = stmt.order_by(VariableModel.environment, VariableModel.key)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_environments(self, team_id: str = "default") -> list[str]:
        """Distinct environment names that have at least one variable defined."""
        stmt = (
            sa_select(VariableModel.environment)
            .where(VariableModel.team_id == team_id)
            .distinct()
            .order_by(VariableModel.environment)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def load_runtime_map(
        self, team_id: str = "default", environment: str = "default"
    ) -> dict[str, str]:
        """Decrypted {key: value} map for a single environment — for the engine.

        Never return this through the API."""
        variables = await self.list(team_id=team_id, environment=environment)
        return {
            v.key: (decrypt_secret(v.value) if v.type == "secret" else v.value)
            for v in variables
        }

    async def load_runtime_secret_values(
        self, team_id: str = "default", environment: str = "default"
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
        result = await self._session.execute(stmt)
        return {decrypt_secret(v.value) for v in result.scalars().all() if v.value}

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
            variable.value = encrypt_secret(value) if variable.type == "secret" else value
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

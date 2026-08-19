"""Persistence for agent definitions and their tool bindings.

An agent is a saved, runnable configuration. Its bindings are ordered and
replaced wholesale — the studio always sends the full list, so we never have
to reconcile a partial diff against the unique index
``(agent_id, source, coalesce(connector_id,''), tool_key)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete as sa_delete, func, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import (
    AgentModel,
    AgentRunModel,
    AgentSessionModel,
    AgentToolBindingModel,
)
from ..utils.ids import agent_id as new_agent_id


class AgentRepository:
    """Repository for ``agents`` + ``agent_tool_bindings``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- agents ------------------------------------------------------------

    async def create(self, values: dict[str, Any]) -> AgentModel:
        now = datetime.now()
        agent = AgentModel(
            id=values.get("id") or new_agent_id(),
            team_id=values.get("team_id") or "default",
            folder_id=values.get("folder_id"),
            name=values["name"],
            description=values.get("description"),
            role=values.get("role") or "asks",
            model=values.get("model") or "claude-sonnet-5",
            system_prompt=values.get("system_prompt") or "",
            task_template=values.get("task_template"),
            settings=values.get("settings") or {},
            memory=values.get("memory"),
            output_schema=values.get("output_schema"),
            version=1,
            active=values.get("active", True),
            created_by=values.get("created_by"),
            updated_by=values.get("created_by"),
            created_at=now,
            updated_at=now,
        )
        self._session.add(agent)
        await self._session.commit()
        await self._session.refresh(agent)
        return agent

    async def get(self, agent_id: str) -> AgentModel | None:
        result = await self._session.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        team_id: str | None = None,
        folder_id: str | None = None,
        active: bool | None = None,
        include_archived: bool = False,
    ) -> list[AgentModel]:
        stmt = select(AgentModel)
        if team_id:
            stmt = stmt.where(AgentModel.team_id == team_id)
        if folder_id:
            stmt = stmt.where(AgentModel.folder_id == folder_id)
        if active is not None:
            stmt = stmt.where(AgentModel.active == active)
        if not include_archived:
            stmt = stmt.where(AgentModel.archived_at.is_(None))
        stmt = stmt.order_by(AgentModel.updated_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self, agent_id: str, changes: dict[str, Any], bump_version: bool = False
    ) -> AgentModel | None:
        agent = await self.get(agent_id)
        if agent is None:
            return None
        for key, value in changes.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        if bump_version:
            agent.version = (agent.version or 1) + 1
        agent.updated_at = datetime.now()
        self._session.add(agent)
        await self._session.commit()
        await self._session.refresh(agent)
        return agent

    async def delete(self, agent_id: str) -> bool:
        """Hard-delete when no session references the agent, else archive it.

        ``agent_sessions.agent_id`` is ON DELETE RESTRICT on purpose: a run's
        transcript must never point at a vanished agent.
        """
        agent = await self.get(agent_id)
        if agent is None:
            return False

        sessions = await self._session.execute(
            sa_select(func.count())
            .select_from(AgentSessionModel)
            .where(AgentSessionModel.agent_id == agent_id)
        )
        if (sessions.scalar() or 0) > 0:
            agent.active = False
            agent.archived_at = datetime.now()
            agent.updated_at = datetime.now()
            self._session.add(agent)
            await self._session.commit()
            return True

        await self._session.execute(
            sa_delete(AgentToolBindingModel).where(
                AgentToolBindingModel.agent_id == agent_id
            )
        )
        await self._session.delete(agent)
        await self._session.commit()
        return True

    # -- bindings ----------------------------------------------------------

    async def get_bindings(
        self, agent_id: str, enabled_only: bool = False
    ) -> list[AgentToolBindingModel]:
        stmt = select(AgentToolBindingModel).where(
            AgentToolBindingModel.agent_id == agent_id
        )
        if enabled_only:
            stmt = stmt.where(AgentToolBindingModel.enabled == True)  # noqa: E712
        stmt = stmt.order_by(
            AgentToolBindingModel.position, AgentToolBindingModel.id
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def replace_bindings(
        self, agent_id: str, bindings: list[dict[str, Any]]
    ) -> list[AgentToolBindingModel]:
        """Delete-then-insert. Duplicate (source, connector, key) pairs collapse."""
        await self._session.execute(
            sa_delete(AgentToolBindingModel).where(
                AgentToolBindingModel.agent_id == agent_id
            )
        )
        seen: set[tuple[str, str, str]] = set()
        rows: list[AgentToolBindingModel] = []
        for position, binding in enumerate(bindings):
            source = binding.get("source") or "builtin"
            connector_id = binding.get("connector_id")
            tool_key = binding.get("tool_key") or ""
            if not tool_key:
                continue
            identity = (source, connector_id or "", tool_key)
            if identity in seen:
                continue
            seen.add(identity)
            row = AgentToolBindingModel(
                agent_id=agent_id,
                source=source,
                connector_id=connector_id,
                tool_key=tool_key,
                alias=binding.get("alias"),
                config=binding.get("config") or {},
                requires_approval=bool(binding.get("requires_approval", False)),
                enabled=bool(binding.get("enabled", True)),
                position=binding.get("position") if binding.get("position") is not None else position,
                created_at=datetime.now(),
            )
            self._session.add(row)
            rows.append(row)
        await self._session.commit()
        return await self.get_bindings(agent_id)

    # -- list-view aggregates ---------------------------------------------

    async def binding_counts(self, agent_ids: list[str]) -> dict[str, int]:
        if not agent_ids:
            return {}
        result = await self._session.execute(
            sa_select(
                AgentToolBindingModel.agent_id, func.count()
            )
            .where(AgentToolBindingModel.agent_id.in_(agent_ids))
            .group_by(AgentToolBindingModel.agent_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def session_counts(self, agent_ids: list[str]) -> dict[str, int]:
        if not agent_ids:
            return {}
        result = await self._session.execute(
            sa_select(AgentSessionModel.agent_id, func.count())
            .where(AgentSessionModel.agent_id.in_(agent_ids))
            .group_by(AgentSessionModel.agent_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def last_run_times(self, agent_ids: list[str]) -> dict[str, datetime]:
        if not agent_ids:
            return {}
        result = await self._session.execute(
            sa_select(AgentRunModel.agent_id, func.max(AgentRunModel.started_at))
            .where(AgentRunModel.agent_id.in_(agent_ids))
            .group_by(AgentRunModel.agent_id)
        )
        return {row[0]: row[1] for row in result.all() if row[1] is not None}

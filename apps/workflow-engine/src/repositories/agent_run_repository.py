"""Persistence for agent sessions, runs and run events.

The turn allocation in :meth:`begin_turn` is the only place in this vertical
that needs real concurrency control: two studio tabs hitting the same session
must not both get a run. It takes ``SELECT ... FOR UPDATE`` on the session row,
checks for a non-terminal run, then inserts the new run and bumps
``run_count`` in the same transaction. The unique index
``(session_id, turn)`` is the belt to that suspenders.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import (
    AgentRunEventModel,
    AgentRunModel,
    AgentSessionModel,
)
from ..utils.ids import agent_run_id as new_run_id, agent_session_id as new_session_id

# Statuses that mean "this run still owns the session".
ACTIVE_STATUSES = ("queued", "running", "waiting")
TERMINAL_STATUSES = ("success", "failed", "cancelled")


class SessionBusyError(Exception):
    """A run is already in flight for this session."""

    def __init__(self, session_id: str, run_id: str) -> None:
        self.session_id = session_id
        self.run_id = run_id
        super().__init__(f"Session {session_id} is busy with run {run_id}")


class AgentRunRepository:
    """Repository for ``agent_sessions`` / ``agent_runs`` / ``agent_run_events``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- sessions ----------------------------------------------------------

    async def create_session(self, values: dict[str, Any]) -> AgentSessionModel:
        session_id = values.get("id") or new_session_id()
        now = datetime.now()
        row = AgentSessionModel(
            id=session_id,
            agent_id=values["agent_id"],
            team_id=values.get("team_id") or "default",
            title=values.get("title") or "Untitled session",
            status=values.get("status") or "active",
            agent_version=values.get("agent_version") or 1,
            agent_config=values.get("agent_config") or {},
            app_id=values.get("app_id"),
            workflow_id=values.get("workflow_id"),
            # Per-session memory namespace. Never "default" — a shared key
            # would let one team read another team's conversation.
            memory_key=values.get("memory_key") or f"sess:{session_id}",
            created_by=values.get("created_by"),
            run_count=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_session(self, session_id: str) -> AgentSessionModel | None:
        result = await self._session.execute(
            select(AgentSessionModel).where(AgentSessionModel.id == session_id)
        )
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        agent_id: str | None = None,
        team_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentSessionModel]:
        stmt = select(AgentSessionModel)
        if agent_id:
            stmt = stmt.where(AgentSessionModel.agent_id == agent_id)
        if team_id:
            stmt = stmt.where(AgentSessionModel.team_id == team_id)
        if status:
            stmt = stmt.where(AgentSessionModel.status == status)
        stmt = stmt.order_by(AgentSessionModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_session(
        self, session_id: str, changes: dict[str, Any]
    ) -> AgentSessionModel | None:
        row = await self.get_session(session_id)
        if row is None:
            return None
        for key, value in changes.items():
            if hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = datetime.now()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    # -- runs --------------------------------------------------------------

    async def begin_turn(self, session_id: str, values: dict[str, Any]) -> AgentRunModel:
        """Lock the session, reject a concurrent run, insert a queued run.

        Raises:
            SessionBusyError: another run on this session is non-terminal.
            KeyError: the session does not exist.
        """
        locked = await self._session.execute(
            select(AgentSessionModel)
            .where(AgentSessionModel.id == session_id)
            .with_for_update()
        )
        session_row = locked.scalar_one_or_none()
        if session_row is None:
            await self._session.rollback()
            raise KeyError(session_id)

        busy = await self._session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.session_id == session_id)
            .where(AgentRunModel.status.in_(ACTIVE_STATUSES))
            .limit(1)
        )
        busy_run = busy.scalar_one_or_none()
        if busy_run is not None:
            # Read the id *before* rolling back: rollback expires the instance
            # and the next attribute access would try to lazy-load it from a
            # sync context (MissingGreenlet), turning a 409 into a 500.
            busy_run_id = busy_run.id
            await self._session.rollback()
            raise SessionBusyError(session_id, busy_run_id)

        turn = (session_row.run_count or 0) + 1
        now = datetime.now()
        run = AgentRunModel(
            id=values.get("id") or new_run_id(),
            session_id=session_id,
            agent_id=session_row.agent_id,
            team_id=session_row.team_id,
            turn=turn,
            status="queued",
            trigger=values.get("trigger") or "studio",
            task=values["task"],
            input=values.get("input") or {},
            agent_snapshot=values.get("agent_snapshot") or {},
            parent_execution_id=values.get("parent_execution_id"),
            created_by=values.get("created_by"),
            started_at=now,
        )
        self._session.add(run)

        session_row.run_count = turn
        session_row.last_run_at = now
        session_row.updated_at = now
        session_row.status = "active"
        self._session.add(session_row)

        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def get_run(self, run_id: str) -> AgentRunModel | None:
        result = await self._session.execute(
            select(AgentRunModel).where(AgentRunModel.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        agent_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentRunModel]:
        stmt = select(AgentRunModel)
        if agent_id:
            stmt = stmt.where(AgentRunModel.agent_id == agent_id)
        if session_id:
            stmt = stmt.where(AgentRunModel.session_id == session_id)
        if status:
            stmt = stmt.where(AgentRunModel.status == status)
        stmt = stmt.order_by(AgentRunModel.started_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_run(
        self, run_id: str, changes: dict[str, Any]
    ) -> AgentRunModel | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        for key, value in changes.items():
            if hasattr(run, key):
                setattr(run, key, value)
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run

    # -- events ------------------------------------------------------------

    async def insert_events(self, rows: Sequence[dict[str, Any]]) -> int:
        """Batch-insert events. Returns the number written."""
        if not rows:
            return 0
        models = [
            AgentRunEventModel(
                run_id=row["run_id"],
                seq=row["seq"],
                type=row["type"],
                node_name=row.get("node_name"),
                payload=row.get("payload") or {},
                truncated=bool(row.get("truncated", False)),
                created_at=row.get("created_at") or datetime.now(),
            )
            for row in rows
        ]
        self._session.add_all(models)
        await self._session.commit()
        return len(models)

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 500
    ) -> list[AgentRunEventModel]:
        stmt = (
            select(AgentRunEventModel)
            .where(AgentRunEventModel.run_id == run_id)
            .where(AgentRunEventModel.seq > after_seq)
            .order_by(AgentRunEventModel.seq)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_events(self, run_id: str) -> int:
        result = await self._session.execute(
            sa_select(func.count())
            .select_from(AgentRunEventModel)
            .where(AgentRunEventModel.run_id == run_id)
        )
        return result.scalar() or 0

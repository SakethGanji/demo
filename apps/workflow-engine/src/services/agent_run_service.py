"""Sessions and runs — the write path for actually running an agent.

A *session* is a continuous body of work: it pins the agent's version and
config at creation, so behaviour cannot shift mid-conversation, and it owns a
private ``memory_key``. A *run* is one turn inside a session. Runs are queued
synchronously (so the caller gets a 202 with a real id) and executed on a
background task registered with :mod:`..engine.execution_registry` so it can
be cancelled.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ..core.exceptions import ValidationError
from ..engine import execution_registry
from ..engine.agent_runtime import DEFAULT_MAX_RUN_SECONDS, AgentRunSpec, AgentRuntime
from ..repositories.agent_run_repository import TERMINAL_STATUSES, SessionBusyError
from ..schemas.agent import (
    RunEventItem,
    RunListItem,
    RunResponse,
    RunTriggerRequest,
    SessionCreateRequest,
    SessionResponse,
)
from ..utils.ids import agent_run_id as new_run_id
from .agent_service import AgentNotFoundError

logger = logging.getLogger(__name__)

# The AIAgent node's memory parameters are flat and prefixed. An agent's
# ``memory`` JSONB is the un-prefixed form, so it is mapped on the way in.
_MEMORY_PARAM_MAP = {
    "maxMessages": "memoryMaxMessages",
    "maxTokens": "memoryMaxTokens",
    "maxTurns": "memoryMaxTurns",
    "summaryModel": "memorySummaryModel",
    "recentMessages": "memoryRecentMessages",
    "summaryThreshold": "memorySummaryThreshold",
    "updateFrequency": "memoryUpdateFrequency",
    "topK": "memoryTopK",
    "embeddingProvider": "memoryEmbeddingProvider",
    "connectionString": "memoryNeo4jUri",
    "username": "memoryNeo4jUsername",
    "password": "memoryNeo4jPassword",
}


async def _run_to_completion(coro: Any) -> Any:
    """Await ``coro`` even if the surrounding task keeps being cancelled.

    ``asyncio.shield`` alone protects the inner work from one cancellation but
    re-raises CancelledError into the *waiter* — and CancelledError is a
    BaseException, so the ``except Exception`` guards around final cleanup
    never stop it. A second cancel() arriving mid-cleanup would then skip the
    terminal DB write entirely, leaving the run "running" and its session
    busy forever. Here the shielded work is re-awaited until it actually
    finishes; only a cancellation of the work itself propagates.
    """
    inner = asyncio.ensure_future(coro)
    while True:
        try:
            return await asyncio.shield(inner)
        except asyncio.CancelledError:
            if inner.cancelled():
                raise
            continue


class SessionNotFoundError(Exception):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' not found")


class RunNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run '{run_id}' not found")


class AgentRunService:
    """Sessions + runs. Owns the background execution of a turn."""

    def __init__(
        self,
        run_repo: Any,
        agent_repo: Any,
        session_factory: Any | None = None,
    ) -> None:
        self._runs = run_repo
        self._agents = agent_repo
        if session_factory is None:
            from ..db.session import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    # -- sessions ----------------------------------------------------------

    async def create_session(
        self, agent_id: str, request: SessionCreateRequest | None = None
    ) -> SessionResponse:
        agent = await self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)

        request = request or SessionCreateRequest()
        bindings = await self._agents.get_bindings(agent_id)
        row = await self._runs.create_session(
            {
                "agent_id": agent_id,
                "team_id": agent.team_id,
                "title": (request.title or "").strip() or f"{agent.name} session",
                "agent_version": agent.version,
                # Pinned config. Tool entries are metadata only — executors are
                # closures and would not survive a round-trip through JSONB.
                "agent_config": _snapshot(agent, bindings),
                "app_id": request.app_id,
                "workflow_id": request.workflow_id,
                "created_by": request.created_by,
            }
        )
        return _session_response(row, agent_name=agent.name)

    async def get_session(self, session_id: str) -> SessionResponse:
        row = await self._runs.get_session(session_id)
        if row is None:
            raise SessionNotFoundError(session_id)
        agent = await self._agents.get(row.agent_id)
        return _session_response(row, agent_name=agent.name if agent else None)

    async def list_sessions(
        self, agent_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[SessionResponse]:
        rows = await self._runs.list_sessions(agent_id=agent_id, status=status, limit=limit)
        return [_session_response(row) for row in rows]

    # -- runs --------------------------------------------------------------

    async def trigger(
        self,
        request: RunTriggerRequest,
        session_id: str | None = None,
    ) -> RunResponse:
        """Queue one turn and return immediately. Raises SessionBusyError on 409."""
        task_text = (request.task or "").strip()
        if not task_text:
            raise ValidationError("Task is required")

        if session_id:
            session_row = await self._runs.get_session(session_id)
            if session_row is None:
                raise SessionNotFoundError(session_id)
        else:
            if not request.agent_id:
                raise ValidationError("Either a session_id or an agent_id is required")
            # A run always lives in a session; an ad-hoc run gets a session of one.
            created = await self.create_session(
                request.agent_id,
                SessionCreateRequest(
                    title=task_text[:80] or None, created_by=request.created_by
                ),
            )
            session_row = await self._runs.get_session(created.id)
            if session_row is None:  # pragma: no cover - just created
                raise SessionNotFoundError(created.id)

        snapshot = dict(session_row.agent_config or {})
        snapshot["memory_key"] = session_row.memory_key
        snapshot["agent_version"] = session_row.agent_version

        run = await self._runs.begin_turn(
            session_row.id,
            {
                "id": new_run_id(),
                "task": task_text,
                "input": request.input or {},
                "trigger": request.trigger or "studio",
                "agent_snapshot": snapshot,
                "created_by": request.created_by,
            },
        )

        max_run_seconds = request.max_run_seconds or int(
            (snapshot.get("settings") or {}).get("maxRunSeconds") or DEFAULT_MAX_RUN_SECONDS
        )
        task = asyncio.create_task(
            self._execute(
                run_id=run.id,
                session_id=session_row.id,
                snapshot=snapshot,
                task_text=task_text,
                input_json=dict(request.input or {}),
                variables=dict(request.variables or {}),
                max_run_seconds=max_run_seconds,
            ),
            name=f"agent-run:{run.id}",
        )
        # Same registry the workflow engine uses, so cancellation and graceful
        # shutdown treat an agent run like any other execution.
        execution_registry.register(run.id, task)
        return _run_response(run)

    async def get_run(self, run_id: str) -> RunResponse:
        run = await self._runs.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return _run_response(run)

    async def list_runs(
        self,
        agent_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[RunListItem]:
        rows = await self._runs.list_runs(
            agent_id=agent_id, session_id=session_id, status=status, limit=limit
        )
        return [
            RunListItem(
                id=r.id,
                session_id=r.session_id,
                agent_id=r.agent_id,
                turn=r.turn,
                status=r.status,
                trigger=r.trigger,
                task=r.task,
                iterations=r.iterations,
                tool_call_count=r.tool_call_count,
                event_count=r.event_count,
                started_at=r.started_at.isoformat(),
                ended_at=r.ended_at.isoformat() if r.ended_at else None,
            )
            for r in rows
        ]

    async def list_events(
        self, run_id: str, after_seq: int = 0, limit: int = 500
    ) -> list[RunEventItem]:
        run = await self._runs.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        rows = await self._runs.list_events(run_id, after_seq=after_seq, limit=limit)
        return [
            RunEventItem(
                seq=r.seq,
                type=r.type,
                node_name=r.node_name,
                payload=r.payload or {},
                truncated=r.truncated,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ]

    async def cancel(self, run_id: str) -> RunResponse:
        run = await self._runs.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status in TERMINAL_STATUSES:
            return _run_response(run)

        cancelled = execution_registry.cancel(run_id)
        if not cancelled:
            # No live task (different worker, or a restart lost it) — the row
            # would otherwise be stuck "running" forever. Conditional write:
            # the task may have finished (and committed its real outcome)
            # between the status check above and here, and that outcome must
            # not be stamped over.
            now = datetime.now()
            updated, applied = await self._runs.finalize_run_if_active(
                run_id,
                {
                    "status": "cancelled",
                    "error": "Cancelled; no live task was registered",
                    "cancelled_at": now,
                    "ended_at": now,
                },
            )
            if applied:
                # A terminal run always releases its session, same as _finalize.
                await self._runs.update_session(
                    run.session_id, {"status": "idle", "last_run_at": now}
                )
            return _run_response(updated or run)

        # The task's own finaliser writes the terminal row.
        return _run_response(run)

    # -- background execution ---------------------------------------------

    async def _execute(
        self,
        run_id: str,
        session_id: str,
        snapshot: dict[str, Any],
        task_text: str,
        input_json: dict[str, Any],
        variables: dict[str, str],
        max_run_seconds: int,
    ) -> None:
        """Run one turn. Never raises out of the task except on cancellation."""
        from ..engine.agent_event_recorder import AgentEventRecorder

        recorder = AgentEventRecorder(run_id)
        outcome = None
        cancelled = False

        try:
            await self._set_status(run_id, {"status": "running"})

            from .agent_tool_resolver import AgentToolResolver

            # The resolver gets the session *factory*, not a session: resolved
            # tools carry live executor closures invoked mid-run, long after
            # any session opened here would have been closed.
            resolved = await AgentToolResolver(
                session_factory=self._session_factory
            ).resolve(snapshot.get("tools") or [])
            if resolved.unavailable:
                logger.warning(
                    "run %s: %d bound tool(s) unavailable: %s",
                    run_id, len(resolved.unavailable), resolved.unavailable,
                )

            # Tools may ship documentation the model needs BEFORE its first
            # call (the SDK tool ships its signature reference this way).
            # Splice it into the system prompt and strip the key — the seam
            # that feeds extra_tools to the model must only see tool fields.
            system_prompt = snapshot.get("system_prompt") or ""
            appendixes = [
                t.pop("system_prompt_appendix")
                for t in resolved.extra_tools
                if isinstance(t, dict) and t.get("system_prompt_appendix")
            ]
            if appendixes:
                system_prompt = "\n\n".join([system_prompt, *appendixes]).strip()

            spec = AgentRunSpec(
                run_id=run_id,
                agent_name=snapshot.get("name") or "agent",
                model=snapshot.get("model") or "claude-sonnet-5",
                task=task_text,
                system_prompt=system_prompt,
                parameters=_parameters_from_snapshot(snapshot),
                builtin_tool_specs=resolved.builtin_tool_specs,
                extra_tools=resolved.extra_tools,
                input_json=input_json,
                variables=variables,
                output_schema=snapshot.get("output_schema"),
                memory_key=snapshot.get("memory_key"),
                max_run_seconds=max_run_seconds,
            )

            await recorder.start()
            outcome = await AgentRuntime(self._session_factory).run(spec, recorder)
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent run %s crashed outside the loop", run_id)
            from ..engine.agent_runtime import AgentRunOutcome

            outcome = AgentRunOutcome(
                status="failed", error=f"{type(exc).__name__}: {exc}"
            )
        finally:
            # Always close the recorder: it owns a writer task and a queue of
            # rows that are the only trace of what the agent did.
            try:
                await _run_to_completion(recorder.close())
            except Exception:
                logger.warning("recorder close failed for run %s", run_id, exc_info=True)

        try:
            await _run_to_completion(
                self._finalize(run_id, session_id, outcome, recorder.event_count, cancelled)
            )
        except Exception:
            logger.exception("failed to persist terminal state for run %s", run_id)

    async def _finalize(
        self,
        run_id: str,
        session_id: str,
        outcome: Any,
        event_count: int,
        cancelled: bool,
    ) -> None:
        now = datetime.now()
        if cancelled or outcome is None:
            changes: dict[str, Any] = {
                "status": "cancelled",
                "error": "Run cancelled",
                "cancelled_at": now,
                "ended_at": now,
                "event_count": event_count,
            }
        else:
            changes = {
                "status": outcome.status,
                "response": outcome.response or None,
                "structured_output": outcome.structured,
                "error": outcome.error,
                "iterations": outcome.iterations,
                "tool_call_count": outcome.tool_call_count,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "llm_time_ms": outcome.llm_time_ms,
                "event_count": event_count,
                "ended_at": now,
            }
        async with self._session_factory() as db_session:
            from ..repositories.agent_run_repository import AgentRunRepository

            repo = AgentRunRepository(db_session)
            # Conditional: cancel()'s no-live-task branch may have already
            # written a terminal status; the first terminal write wins.
            await repo.finalize_run_if_active(run_id, changes)
            await repo.update_session(session_id, {"status": "idle", "last_run_at": now})

    async def _set_status(self, run_id: str, changes: dict[str, Any]) -> None:
        async with self._session_factory() as db_session:
            from ..repositories.agent_run_repository import AgentRunRepository

            await AgentRunRepository(db_session).update_run(run_id, changes)


# ---------------------------------------------------------------------------
# Snapshot / mapping helpers
# ---------------------------------------------------------------------------


def _snapshot(agent: Any, bindings: list[Any]) -> dict[str, Any]:
    """JSON-safe frozen copy of an agent's config at session-creation time."""
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "version": agent.version,
        "model": agent.model,
        "system_prompt": agent.system_prompt,
        "task_template": agent.task_template,
        "settings": agent.settings or {},
        "memory": agent.memory,
        "output_schema": agent.output_schema,
        "role": agent.role,
        "tools": [
            {
                "source": b.source,
                "connector_id": b.connector_id,
                "tool_key": b.tool_key,
                "alias": b.alias,
                "config": b.config or {},
                "requires_approval": b.requires_approval,
                "enabled": b.enabled,
                "position": b.position,
            }
            for b in bindings
        ],
    }


def _parameters_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """AIAgent node parameters: ``settings`` verbatim plus mapped memory config."""
    parameters = dict(snapshot.get("settings") or {})
    memory = snapshot.get("memory") or {}
    if isinstance(memory, dict) and memory.get("type") and memory.get("type") != "none":
        parameters["memoryType"] = memory["type"]
        for key, value in memory.items():
            if key == "type":
                continue
            parameters[_MEMORY_PARAM_MAP.get(key, key)] = value
    return parameters


def _session_response(row: Any, agent_name: str | None = None) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        agent_id=row.agent_id,
        agent_name=agent_name,
        team_id=row.team_id,
        title=row.title,
        status=row.status,
        agent_version=row.agent_version,
        agent_config=row.agent_config or {},
        app_id=row.app_id,
        workflow_id=row.workflow_id,
        memory_key=row.memory_key,
        holder_id=row.holder_id,
        run_count=row.run_count,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        created_by=row.created_by,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _run_response(row: Any) -> RunResponse:
    return RunResponse(
        id=row.id,
        session_id=row.session_id,
        agent_id=row.agent_id,
        team_id=row.team_id,
        turn=row.turn,
        status=row.status,
        trigger=row.trigger,
        task=row.task,
        input=row.input or {},
        agent_snapshot=row.agent_snapshot or {},
        response=row.response,
        structured_output=row.structured_output,
        error=row.error,
        iterations=row.iterations,
        tool_call_count=row.tool_call_count,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        llm_time_ms=row.llm_time_ms,
        event_count=row.event_count,
        created_by=row.created_by,
        started_at=row.started_at.isoformat(),
        ended_at=row.ended_at.isoformat() if row.ended_at else None,
        cancelled_at=row.cancelled_at.isoformat() if row.cancelled_at else None,
    )


__all__ = [
    "AgentRunService",
    "RunNotFoundError",
    "SessionBusyError",
    "SessionNotFoundError",
]

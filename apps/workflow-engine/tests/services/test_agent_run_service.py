"""Regression tests for the four agent-run lifecycle bugs found by the
2026-08-19 re-audit (originally flagged 2026-08-18, never fixed):

1. A second cancel() while _execute is inside its shielded cleanup raised
   CancelledError past both ``except Exception`` handlers, skipping _finalize —
   the run stayed "running" and the session stayed busy forever.
2. cancel()'s no-live-task branch raced the run's own finalizer and could
   stamp a legitimately-completed run as "cancelled" (with the successful
   response still on the row).
3. AgentToolResolver was handed a session that closed before the resolved
   extra-tool executor closures ran, so any call-time DB work hit a closed
   session mid-run.
4. cancel()'s no-live-task branch never returned the session to "idle",
   unlike every other terminal path.

All tests drive the real AgentRunService with fake repositories/sessions —
no database, no LLM. Async work runs through asyncio.run() (this suite has
no async pytest plugin, same convention as tests/engine).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.repositories.agent_run_repository import ACTIVE_STATUSES  # noqa: E402
from src.services.agent_run_service import AgentRunService  # noqa: E402
from src.services.agent_tool_resolver import AgentToolResolver  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_run_db(run_id: str, session_id: str, **over: Any) -> dict[str, Any]:
    """A dict standing in for the agent_runs row, with every field
    _run_response reads."""
    db: dict[str, Any] = {
        "id": run_id,
        "session_id": session_id,
        "agent_id": "agent-1",
        "team_id": "default",
        "turn": 1,
        "status": "running",
        "trigger": "studio",
        "task": "do the thing",
        "input": {},
        "agent_snapshot": {},
        "response": None,
        "structured_output": None,
        "error": None,
        "iterations": 0,
        "tool_call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "llm_time_ms": 0,
        "event_count": 0,
        "created_by": None,
        "started_at": datetime.now(),
        "ended_at": None,
        "cancelled_at": None,
    }
    db.update(over)
    return db


class FakeRunRepo:
    """Mimics AgentRunRepository over one in-memory run row.

    get_run returns a *snapshot* (like a real SELECT), so a later mutation of
    the underlying dict models another writer committing concurrently.
    """

    def __init__(self, db: dict[str, Any]) -> None:
        self.db = db
        self.run_updates: list[dict[str, Any]] = []
        self.session_updates: list[tuple[str, dict[str, Any]]] = []
        self.get_run_calls = 0
        self.after_first_get: Any = None  # callable mutating self.db

    async def get_run(self, run_id: str) -> Any:
        if run_id != self.db["id"]:
            return None
        self.get_run_calls += 1
        snapshot = SimpleNamespace(**self.db)
        if self.get_run_calls == 1 and self.after_first_get is not None:
            hook, self.after_first_get = self.after_first_get, None
            hook()
        return snapshot

    async def update_run(self, run_id: str, changes: dict[str, Any]) -> Any:
        # Deliberately as blind as the real update_run: last write wins.
        if run_id != self.db["id"]:
            return None
        self.db.update(changes)
        self.run_updates.append(dict(changes))
        return SimpleNamespace(**self.db)

    async def finalize_run_if_active(
        self, run_id: str, changes: dict[str, Any]
    ) -> tuple[Any, bool]:
        if run_id != self.db["id"]:
            return None, False
        if self.db["status"] not in ACTIVE_STATUSES:
            return SimpleNamespace(**self.db), False
        self.db.update(changes)
        self.run_updates.append(dict(changes))
        return SimpleNamespace(**self.db), True

    async def update_session(self, session_id: str, changes: dict[str, Any]) -> Any:
        self.session_updates.append((session_id, dict(changes)))
        return SimpleNamespace(id=session_id, **changes)


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> str:
        if self.closed:
            raise RuntimeError("session is closed")
        return "pong"


class FakeSessionCM:
    def __init__(self, opened: list[FakeSession]) -> None:
        self._opened = opened

    async def __aenter__(self) -> FakeSession:
        self.session = FakeSession()
        self._opened.append(self.session)
        return self.session

    async def __aexit__(self, *exc: Any) -> bool:
        self.session.closed = True
        return False


def make_session_factory(opened: list[FakeSession]):
    return lambda: FakeSessionCM(opened)


def make_service(repo: FakeRunRepo, opened: list[FakeSession] | None = None) -> AgentRunService:
    return AgentRunService(
        run_repo=repo,
        agent_repo=None,
        session_factory=make_session_factory(opened if opened is not None else []),
    )


def outcome(**over: Any) -> SimpleNamespace:
    base = dict(
        status="success",
        response="ok",
        structured=None,
        error=None,
        iterations=1,
        tool_call_count=0,
        input_tokens=1,
        output_tokens=1,
        llm_time_ms=1,
    )
    base.update(over)
    return SimpleNamespace(**base)


class QuietRecorder:
    """Stands in for AgentEventRecorder: no writer task, no DB."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.event_count = 0

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


def patch_execute_seams(monkeypatch, repo: FakeRunRepo, recorder_cls, runtime_cls) -> None:
    """_execute reaches its collaborators by late import — patch all three."""
    monkeypatch.setattr(
        "src.engine.agent_event_recorder.AgentEventRecorder", recorder_cls
    )
    monkeypatch.setattr("src.services.agent_run_service.AgentRuntime", runtime_cls)
    # _set_status/_finalize build a fresh AgentRunRepository per session.
    monkeypatch.setattr(
        "src.repositories.agent_run_repository.AgentRunRepository",
        lambda session: repo,
    )


# ---------------------------------------------------------------------------
# 1. Double-cancel mid-cleanup must still write the terminal row
# ---------------------------------------------------------------------------


def test_second_cancel_during_cleanup_still_finalizes(monkeypatch):
    async def scenario():
        db = make_run_db("run-dc", "sess-dc", status="running")
        repo = FakeRunRepo(db)
        svc = make_service(repo)

        close_started = asyncio.Event()
        release_close = asyncio.Event()

        class SlowCloseRecorder(QuietRecorder):
            async def close(self) -> None:
                close_started.set()
                await release_close.wait()

        run_entered = asyncio.Event()

        class BlockingRuntime:
            def __init__(self, session_factory: Any) -> None:
                pass

            async def run(self, spec: Any, recorder: Any) -> Any:
                run_entered.set()
                await asyncio.Event().wait()  # parked until cancelled

        patch_execute_seams(monkeypatch, repo, SlowCloseRecorder, BlockingRuntime)

        task = asyncio.create_task(
            svc._execute(
                run_id="run-dc",
                session_id="sess-dc",
                snapshot={},
                task_text="t",
                input_json={},
                variables={},
                max_run_seconds=5,
            )
        )
        await run_entered.wait()
        task.cancel()  # first cancel: caught, cleanup begins
        await close_started.wait()
        task.cancel()  # second cancel: lands on the shielded cleanup await
        await asyncio.sleep(0)
        release_close.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # The whole point: the terminal write must have happened anyway.
        assert db["status"] == "cancelled", (
            f"run left in non-terminal status {db['status']!r} — a second "
            "cancel() mid-cleanup skipped _finalize and wedged the session"
        )
        assert any(
            changes.get("status") == "idle" for _, changes in repo.session_updates
        ), "session was never returned to idle"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. cancel() must not overwrite a run that finished in the race window
# ---------------------------------------------------------------------------


def test_cancel_does_not_overwrite_run_that_finished_meanwhile():
    async def scenario():
        db = make_run_db("run-race", "sess-race", status="running")
        repo = FakeRunRepo(db)

        # The instant after cancel() reads the row, the background task's
        # finalizer commits the real outcome (registry has no task for this
        # id, so cancel() takes the no-live-task branch).
        def finish_for_real():
            db.update(status="success", response="the real answer", ended_at=datetime.now())

        repo.after_first_get = finish_for_real

        svc = make_service(repo)
        result = await svc.cancel("run-race")

        assert db["status"] == "success", (
            "cancel() stamped a completed run as cancelled — the successful "
            "outcome row was corrupted"
        )
        assert db["response"] == "the real answer"
        assert db["error"] is None
        assert result.status == "success"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. Resolved tool executors must not capture a session that closes at
#    resolve time
# ---------------------------------------------------------------------------


def test_tool_executor_still_has_db_access_at_call_time(monkeypatch):
    async def scenario():
        db = make_run_db("run-tool", "sess-tool", status="running")
        repo = FakeRunRepo(db)
        opened: list[FakeSession] = []
        svc = make_service(repo, opened)

        # A provider whose executor does DB work at CALL time — the shape the
        # resolver's docstring promises ("execute is a live Python closure").
        def provider(bindings: Any, session_or_factory: Any):
            async def execute(**kwargs: Any) -> str:
                target = session_or_factory
                if callable(target):  # a session factory: open per call
                    async with target() as session:
                        return await session.ping()
                return await target.ping()  # a captured live session

            return [
                {
                    "name": "probe",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {}},
                    "execute": execute,
                }
            ]

        monkeypatch.setattr(
            AgentToolResolver, "_load_provider", lambda self, source: provider
        )

        class ToolCallingRuntime:
            def __init__(self, session_factory: Any) -> None:
                pass

            async def run(self, spec: Any, recorder: Any) -> Any:
                (tool,) = [t for t in spec.extra_tools if t["name"] == "probe"]
                result = await tool["execute"]()  # mid-run, long after resolve
                return outcome(response=result, tool_call_count=1)

        patch_execute_seams(monkeypatch, repo, QuietRecorder, ToolCallingRuntime)

        await svc._execute(
            run_id="run-tool",
            session_id="sess-tool",
            snapshot={"tools": [{"source": "node", "tool_key": "probe", "enabled": True}]},
            task_text="t",
            input_json={},
            variables={},
            max_run_seconds=5,
        )

        assert db["status"] == "success", (
            f"tool call failed mid-run (status={db['status']!r}, "
            f"error={db['error']!r}) — the executor closure was handed a "
            "session that closed at resolve time"
        )
        assert db["response"] == "pong"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 4. The no-live-task cancel branch must release the session like every
#    other terminal path
# ---------------------------------------------------------------------------


def test_cancel_without_live_task_sets_session_idle():
    async def scenario():
        db = make_run_db("run-idle", "sess-idle", status="running")
        repo = FakeRunRepo(db)
        svc = make_service(repo)

        result = await svc.cancel("run-idle")

        assert result.status == "cancelled"
        assert db["status"] == "cancelled"
        assert any(
            sid == "sess-idle" and changes.get("status") == "idle"
            for sid, changes in repo.session_updates
        ), "session left 'active' forever by the no-live-task cancel branch"

    asyncio.run(scenario())

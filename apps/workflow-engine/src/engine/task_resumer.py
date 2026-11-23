"""Resuming runs that paused at an Approval (Stage 5, DESIGN-STAGE5-UIS sections 5.3, 5.5, 5.6).

* ``build_snapshot``: what a task stores so the run can be re-entered at its Approval node days later.
* ``claim_and_resume(task_id)``: claim a decided task (conditional UPDATE) and run only what is downstream of the
  Approval, **in the same execution** (same id, same node_outputs).
* The scheduler helpers: ``expire_due`` (timeouts), ``sweep_pending`` (decided but never claimed, safe to resume),
  ``fail_interrupted`` (a resume that was running when a process died is **never** re-run: the execution fails with
  ``RESUME_INTERRUPTED``, because re-running could pay a refund twice).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Identifies this engine process; written to ``human_tasks.resume_owner`` when a resume is claimed.
BOOT_ID = uuid.uuid4().hex
HEARTBEAT_SECONDS = 30.0
STALE_RESUME_SECONDS = 120.0
MAX_SNAPSHOT_BYTES = 1024 * 1024
SNAPSHOT_VERSION = 1

#: Test-only fault injection (set through ``/api/_test/tasks/fault``): ``after_claim`` makes the next resume stop
#: right after the claim, leaving it ``running`` like a process that died there.
FAULT: dict[str, bool] = {}

INTERRUPTED_MESSAGE = (
    "The engine stopped while resuming this run after an approval. Downstream steps may have partly run - "
    "check before retrying."
)

_inflight: set[asyncio.Task] = set()


class SnapshotTooLargeError(ValueError):
    code = "APPROVAL_SNAPSHOT_TOO_LARGE"

    def __init__(self, size: int) -> None:
        super().__init__(f"The state needed to resume this run is too large ({size} bytes; limit {MAX_SNAPSHOT_BYTES})")
        self.details = {"size": size, "limit": MAX_SNAPSHOT_BYTES}


def _session_factory(sf: Callable[[], Any] | None) -> Callable[[], Any]:
    if sf is not None:
        return sf
    from ..db import session as db_session

    return db_session.async_session_factory  # looked up at call time so tests can swap it


# ── snapshot ──────────────────────────────────────────────────────────────────


def _descendants(connections: list[Any], node: str) -> set[str]:
    seen: set[str] = set()
    todo = [node]
    while todo:
        cur = todo.pop()
        for c in connections:
            if c.source_node == cur and c.target_node not in seen:
                seen.add(c.target_node)
                todo.append(c.target_node)
    return seen


def _dump_pending(context: Any, node: str) -> dict[str, dict[str, Any]]:
    """Merge inputs received so far by nodes downstream of ``node`` (an empty branch is stored as null)."""
    from .types import NO_OUTPUT_SIGNAL

    down = _descendants(context.workflow.connections, node)
    out: dict[str, dict[str, Any]] = {}
    for key, inputs in context.pending_inputs.items():
        if key.rsplit(":", 1)[0] not in down:
            continue
        out[key] = {
            src: (None if data is NO_OUTPUT_SIGNAL else [{"json": d.json} for d in data])
            for src, data in inputs.items()
        }
    return out


def load_pending(raw: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    from .types import NO_OUTPUT_SIGNAL, NodeData

    return {
        key: {src: (NO_OUTPUT_SIGNAL if data is None else [NodeData(json=i.get("json", {})) for i in data])
              for src, data in inputs.items()}
        for key, inputs in (raw or {}).items()
    }


def _dump_internal_state(context: Any) -> dict[str, Any]:
    """Per-node state kept across a node's runs in one execution (a Loop's pending items and cursor, a Poll's attempt
    counter). Without it a resumed Loop forgets its items and re-emits the loop-back item as the next one.

    Only JSON-safe entries are stored; a sub-workflow call memo (keyed by the identity of an in-memory input list) is
    meaningless in a fresh runner and is left out."""
    out: dict[str, Any] = {}
    for key, value in (getattr(context, "node_internal_state", None) or {}).items():
        if key.endswith(":subworkflow"):
            continue
        try:
            out[key] = json.loads(json.dumps(value))
        except (TypeError, ValueError):
            logger.warning("node state %r is not JSON-safe and is not kept across the approval pause", key)
    return out


def build_snapshot(context: Any, entry: dict[str, Any]) -> dict[str, Any]:
    """The resume snapshot of one suspension (``context.suspensions`` entry). Raises ``SnapshotTooLargeError`` over 1 MB.

    Secrets and variables are never stored: they are reloaded when the run resumes."""
    from ..repositories.workflow_repository import workflow_to_definition

    node = entry["node"]
    payload = entry["suspension"].payload
    iface = getattr(context.workflow, "interface", None) or {}
    counts = dict(context.node_run_counts)
    if counts.get(node):  # the Approval job counts itself again when it runs on resume
        counts[node] = max(0, counts[node] - 1)
    source: dict[str, Any] = (
        {"version_id": context.version_id} if context.version_id is not None
        else {"draft_definition": workflow_to_definition(context.workflow)}
    )
    secrets = context.extras.get("secret_values") or set()
    items_out = [{"json": d.json} for d in entry["input_items"]]
    pending = _dump_pending(context, node)
    if secrets:  # never stored: a secret value that travelled in the data is masked (variables are reloaded at resume)
        from ..services.vars_substitution import redact_deep

        items_out, pending = redact_deep(items_out, secrets), redact_deep(pending, secrets)
    snapshot = {
        "v": SNAPSHOT_VERSION,
        "definition_source": source,
        "start_node": node,
        "input_items": items_out,
        "pending_inputs": pending,
        "node_run_counts": counts,
        "node_internal_state": _dump_internal_state(context),
        "mode": context.mode,
        "environment": context.environment,
        "project_id": context.project_id,
        "workflow_name": context.workflow.name,
        "extras_keys": {k: context.extras[k] for k in ("interface",) if k in context.extras},
        "approval": {
            "allow_comment": payload.get("allow_comment", True),
            "require_comment_on_reject": payload.get("require_comment_on_reject", False),
            "four_eyes": payload.get("four_eyes", False),
        },
        "screen": (iface.get("screens") or {}).get(f"task:{node}"),
    }
    if payload.get("per_item"):  # one task per item: the run resumes once every task of the group is settled
        snapshot["per_item"] = True
    size = len(json.dumps(snapshot, default=str))
    if size > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLargeError(size)
    return snapshot


# ── claim and resume ──────────────────────────────────────────────────────────


async def _claim(sf: Callable[[], Any], task_id: str) -> dict[str, Any] | str:
    """Claim a decided task for this process. Returns the task row (with ``snapshot``) or why nothing happened:
    ``not_found`` / ``not_pending`` / ``busy`` (another resume of the same run is in flight, retried later) /
    ``closed`` (the run was cancelled or already ended) / ``waiting`` (a per_item task whose group still has open
    tasks: it is marked ``done`` and the claim of the group's last settled task resumes for all of them)."""
    from ..repositories.task_repository import _row

    async with sf() as session:
        ex = (await session.execute(
            text("""SELECT e.status FROM executions e
                     WHERE e.id = (SELECT execution_id FROM human_tasks WHERE id = :id) FOR UPDATE"""),
            {"id": task_id},
        )).first()
        row = (await session.execute(
            text("SELECT * FROM human_tasks WHERE id = :id FOR UPDATE"), {"id": task_id}
        )).mappings().first()
        if row is None or ex is None:
            await session.rollback()
            return "not_found"
        if row["resume_state"] != "pending":
            await session.rollback()
            return "not_pending"
        exec_status = ex[0]
        if exec_status == "running":
            await session.rollback()
            return "busy"
        if exec_status != "waiting":
            await session.execute(
                text("UPDATE human_tasks SET resume_state = 'done', updated_at = now() WHERE id = :id"), {"id": task_id}
            )
            await session.commit()
            return "closed"
        # The tasks of this node-run (more than one for a per_item Approval). The execution row lock above serialises
        # every claim of this run, and each claim reads after its own decision committed, so the claim of the last
        # task to settle always sees the whole group settled: the run resumes exactly once.
        group = [_row(g) for g in (await session.execute(
            text("""SELECT id, status, decision, decided_by, item_index, resume_state FROM human_tasks
                     WHERE execution_id = :e AND node_name = :n AND run_index = :r ORDER BY item_index FOR UPDATE"""),
            {"e": row["execution_id"], "n": row["node_name"], "r": row["run_index"]},
        )).mappings().all()]
        if any(g["status"] == "open" for g in group):
            # settled, but others of its group are still open: nothing to resume yet (the last one resumes for all)
            await session.execute(
                text("UPDATE human_tasks SET resume_state = 'done', updated_at = now() WHERE id = :id"), {"id": task_id}
            )
            await session.commit()
            return "waiting"
        await session.execute(
            text("""UPDATE human_tasks SET resume_state = 'done', updated_at = now()
                     WHERE execution_id = :e AND node_name = :n AND run_index = :r AND id <> :id
                       AND resume_state = 'pending'"""),
            {"e": row["execution_id"], "n": row["node_name"], "r": row["run_index"], "id": task_id},
        )  # absorbed: this claim resumes for the whole group
        await session.execute(
            text("""UPDATE human_tasks SET resume_state = 'running', resume_owner = :boot, resume_heartbeat = now(),
                           updated_at = now() WHERE id = :id"""),
            {"id": task_id, "boot": BOOT_ID},
        )
        await session.execute(
            text("""UPDATE executions SET status = 'running', end_time = NULL,
                           metadata = metadata || CAST(:patch AS jsonb)
                     WHERE id = :e"""),
            {"e": row["execution_id"], "patch": json.dumps({"resumed_at": datetime.now().isoformat()})},
        )
        await session.commit()
        claimed = _row(row)
        claimed["group"] = group
        return claimed


async def _heartbeat(sf: Callable[[], Any], task_id: str) -> None:
    from ..repositories.task_repository import TaskRepository

    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            async with sf() as session:
                await TaskRepository(session).heartbeat(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("resume heartbeat failed for %s", task_id, exc_info=True)


async def _outputs_of(session: Any, execution_id: str, skip: str) -> dict[str, list[Any]]:
    """Successful saved outputs of the execution (latest run_index wins), without the Approval node itself."""
    from sqlalchemy import select

    from ..db.models import NodeOutputModel
    from .types import NodeData

    rows = (await session.execute(
        select(NodeOutputModel)
        .where(NodeOutputModel.execution_id == execution_id, NodeOutputModel.status == "success")
        .order_by(NodeOutputModel.run_index, NodeOutputModel.created_at)
    )).scalars().all()
    out: dict[str, list[Any]] = {}
    for r in rows:
        if r.node_name == skip:
            continue
        out[r.node_name] = [NodeData(json=i.get("json", {})) for i in r.output] if isinstance(r.output, list) else []
    return out


def decision_of(task: dict[str, Any]) -> dict[str, Any]:
    d = dict(task.get("decision") or {})
    d["decision"] = task["status"]
    d.setdefault("decided_by", task.get("decided_by"))
    return d


def resume_decision_of(task: dict[str, Any]) -> dict[str, Any]:
    """What the Approval node is handed on resume. A per_item group also gets ``items``: every task's decision, by
    ``item_index``."""
    snap = task.get("snapshot") or {}
    out: dict[str, Any] = {
        "task_id": task["id"], "node": task["node_name"], "run_index": task["run_index"],
        "decision": decision_of(task), "node_run_counts": snap.get("node_run_counts") or {},
        "node_internal_state": snap.get("node_internal_state") or {},
    }
    if snap.get("per_item"):
        out["items"] = [
            {"item_index": g["item_index"], "task_id": g["id"], "decision": decision_of(g)}
            for g in task.get("group") or [task]
        ]
    return out


async def _run_resume(sf: Callable[[], Any], task: dict[str, Any]) -> None:
    from ..repositories.execution_repository import ExecutionRepository
    from ..repositories.workflow_repository import WorkflowRepository, definition_to_workflow
    from ..services.execution_service import ExecutionService
    from .types import NodeData, StoredWorkflow

    snap = task["snapshot"]
    src = snap.get("definition_source") or {}
    async with sf() as session:
        exec_repo = ExecutionRepository(session)
        wf_repo = WorkflowRepository(session)
        override = None
        if "draft_definition" in src:
            name = snap.get("workflow_name") or task["workflow_id"]
            now = datetime.now()
            override = StoredWorkflow(
                id=task["workflow_id"], name=name,
                workflow=definition_to_workflow(src["draft_definition"], name, task["workflow_id"], None),
                active=False, created_at=now, updated_at=now, project_id=task["project_id"],
            )
        states = await _outputs_of(session, task["execution_id"], task["node_name"])
        await ExecutionService(exec_repo, wf_repo).run_saved_workflow(
            task["workflow_id"],
            environment=snap.get("environment") or "default",
            mode=snap.get("mode"),
            substitute_inputs=False,
            version_id=src.get("version_id"),
            extras=dict(snap.get("extras_keys") or {}),
            start_node_name=task["node_name"],
            pre_populated_states=states,
            initial_data_override=[NodeData(json=i.get("json", {})) for i in snap.get("input_items") or []],
            pending_inputs=load_pending(snap.get("pending_inputs")),
            execution_id=task["execution_id"],
            skip_start=True,
            resume_decision=resume_decision_of(task),
            stored_override=override,
        )


async def claim_and_resume(task_id: str, session_factory: Callable[[], Any] | None = None) -> str:
    """Claim and run the resume of one decided task. Returns an outcome string:
    ``resumed`` / ``failed`` / ``fault_injected`` / ``busy`` / ``closed`` / ``waiting`` / ``not_pending`` / ``not_found``."""
    from ..repositories.execution_repository import ExecutionRepository
    from ..repositories.task_repository import TaskRepository
    from . import execution_registry

    sf = _session_factory(session_factory)
    claimed = await _claim(sf, task_id)
    if isinstance(claimed, str):
        return claimed
    task = claimed
    if FAULT.pop("after_claim", None):
        return "fault_injected"  # stays "running": exactly what a crash right after the claim leaves behind
    me = asyncio.current_task()
    if me is not None:
        execution_registry.register(task["execution_id"], me)
    beat = asyncio.create_task(_heartbeat(sf, task_id))
    try:
        await _run_resume(sf, task)
        async with sf() as session:
            await TaskRepository(session).set_resume_state(task_id, "done")
        return "resumed"
    except asyncio.CancelledError:
        async with sf() as session:  # the run was cancelled: nothing more to resume
            await TaskRepository(session).set_resume_state(task_id, "done")
        raise
    except Exception as e:
        logger.exception("resume of task %s failed", task_id)
        async with sf() as session:
            await TaskRepository(session).set_resume_state(task_id, "failed")
            await ExecutionRepository(session).mark_failed(
                task["execution_id"], f"Resuming after approval failed: {e}", "RESUME_FAILED",
                node_name=task["node_name"], run_index=task["run_index"],
            )
        return "failed"
    finally:
        beat.cancel()


def schedule_resume(task_id: str, session_factory: Callable[[], Any] | None = None) -> asyncio.Task:
    """Start ``claim_and_resume`` in the background (the in-process kick after a decision). The sweep is the backstop."""
    task = asyncio.create_task(claim_and_resume(task_id, session_factory))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return task


# ── scheduler / startup ───────────────────────────────────────────────────────


async def expire_due(session_factory: Callable[[], Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Time out open tasks past their deadline (``SKIP LOCKED``). Returns the expired ``{id, execution_id, status}``."""
    from ..repositories.task_repository import TaskRepository

    async with _session_factory(session_factory)() as session:
        return await TaskRepository(session).expire_due(limit)


async def sweep_pending(
    session_factory: Callable[[], Any] | None = None, *, min_age_s: float = 5.0, limit: int = 20,
) -> list[asyncio.Task]:
    """Resume decided tasks nobody claimed (the engine died between the decision and the claim). Nothing downstream has
    run for these, so resuming is safe. Returns the started background tasks."""
    from ..repositories.task_repository import TaskRepository

    sf = _session_factory(session_factory)
    async with sf() as session:
        ids = await TaskRepository(session).pending_ids(min_age_s, limit)
    return [schedule_resume(i, session_factory) for i in ids]


async def fail_interrupted(session_factory: Callable[[], Any] | None = None, *, startup: bool = False) -> list[str]:
    """Fail the executions whose resume was running in a process that is gone. They are **never** re-run.

    Only heartbeat staleness counts (``startup`` is kept for callers and does not change the rule): a boot id says who
    claimed a resume, not whether that process is alive, so a second engine instance never fails a live peer's resume."""
    from ..repositories.execution_repository import ExecutionRepository
    from ..repositories.task_repository import TaskRepository

    sf = _session_factory(session_factory)
    async with sf() as session:
        stuck = await TaskRepository(session).interrupted(stale_after_s=STALE_RESUME_SECONDS)
    failed: list[str] = []
    for t in stuck:
        async with sf() as session:
            if not await TaskRepository(session).set_resume_state(t["id"], "failed", only_from=("running",)):
                continue
            status = (await session.execute(
                text("SELECT status FROM executions WHERE id = :e"), {"e": t["execution_id"]}
            )).scalar()
            if status == "running":
                await ExecutionRepository(session).mark_failed(
                    t["execution_id"], INTERRUPTED_MESSAGE, "RESUME_INTERRUPTED", node_name=t["node_name"], run_index=0,
                )
            failed.append(t["id"])
    if failed:
        logger.warning("%d interrupted resume(s) marked RESUME_INTERRUPTED: %s", len(failed), failed)
    return failed


async def tick(session_factory: Callable[[], Any] | None = None) -> list[asyncio.Task]:
    """One scheduler pass: time out due tasks (and kick their resumes), resume anything decided and unclaimed, and fail
    resumes whose process died. Returns the background resume tasks it started."""
    started: list[asyncio.Task] = []
    for step in (_tick_timeouts, _tick_pending, _tick_stale):
        try:
            started.extend(await step(session_factory))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("task tick step %s failed", step.__name__)
    return started


async def _tick_timeouts(sf: Callable[[], Any] | None) -> list[asyncio.Task]:
    return [schedule_resume(t["id"], sf) for t in await expire_due(sf)]


async def _tick_pending(sf: Callable[[], Any] | None) -> list[asyncio.Task]:
    return await sweep_pending(sf)


async def _tick_stale(sf: Callable[[], Any] | None) -> list[asyncio.Task]:
    await fail_interrupted(sf, startup=False)
    return []


async def startup_sweep(session_factory: Callable[[], Any] | None = None) -> list[str]:
    """Run once before the scheduler starts: resumes whose heartbeat went stale (the process died) fail loudly."""
    return await fail_interrupted(session_factory, startup=True)

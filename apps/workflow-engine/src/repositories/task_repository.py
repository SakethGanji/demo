"""Persistence of human tasks (Stage 5, DESIGN-STAGE5-UIS sections 3.2, 5.2-5.6).

Raw SQL over the ``human_tasks`` table: every state change is one conditional UPDATE, so Postgres row locks (not
application locks) decide races: two approvers, a decision against a timeout, two engine instances.

Rows come back as plain dicts (JSONB decoded, timestamps as aware ``datetime``).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..utils.ids import generate_id

#: task statuses that mean "someone decided or it was closed"
CLOSED_STATUSES = ("approved", "rejected", "timed_out", "cancelled")
_JSON_COLS = ("context", "form", "assignees", "decision", "snapshot")
_LIST_COLS = (
    "t.id, t.project_id, t.workflow_id, t.workflow_version_id, t.execution_id, t.node_name, t.run_index, t.item_index, "
    "t.kind, t.title, "
    "t.context, t.form, t.assignees, t.status, t.due_at, t.on_timeout, t.decision, t.decided_by, t.decided_at, "
    "t.decide_key, t.resume_state, t.created_at, t.updated_at"
)


def new_task_id() -> str:
    return generate_id("tsk")


def _row(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    for k in _JSON_COLS:
        if isinstance(out.get(k), (str, bytes)):  # driver gave raw JSON text
            out[k] = json.loads(out[k])
    return out


def encode_cursor(created_at: datetime, task_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{task_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts, _, tid = raw.partition("|")
        if not tid:
            return None
        dt = datetime.fromisoformat(ts)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)), tid
    except Exception:
        return None


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── create ────────────────────────────────────────────────────────────────

    async def insert(self, row: dict[str, Any]) -> bool:
        """Insert a task; ``(execution_id, node_name, run_index, item_index)`` is unique, so a repeat is a no-op. True when
        inserted. A ``per_item`` Approval's tasks share ``(execution_id, node_name, run_index)``: that is the group the
        run waits on."""
        res = await self._s.execute(
            text("""
                INSERT INTO human_tasks (id, project_id, workflow_id, workflow_version_id, execution_id, node_name, run_index,
                                         item_index, kind, title, context, form, assignees, due_at, on_timeout, snapshot)
                VALUES (:id, :project_id, :workflow_id, :workflow_version_id, :execution_id, :node_name, :run_index,
                        :item_index, :kind, :title, CAST(:context AS jsonb), CAST(:form AS jsonb), CAST(:assignees AS jsonb),
                        :due_at, :on_timeout, CAST(:snapshot AS jsonb))
                ON CONFLICT (execution_id, node_name, run_index, item_index) DO NOTHING
            """),
            {
                "id": row["id"], "project_id": row["project_id"], "workflow_id": row["workflow_id"],
                "workflow_version_id": row.get("workflow_version_id"), "execution_id": row["execution_id"],
                "node_name": row["node_name"], "run_index": row.get("run_index", 0),
                "item_index": row.get("item_index", 0), "kind": row.get("kind", "approval"),
                "title": row["title"], "context": json.dumps(row.get("context") or {}, default=str),
                "form": json.dumps(row.get("form") or [], default=str),
                "assignees": json.dumps(row.get("assignees") or [], default=str),
                "due_at": row.get("due_at"), "on_timeout": row.get("on_timeout") or "timed_out",
                "snapshot": json.dumps(row["snapshot"], default=str),
            },
        )
        return (res.rowcount or 0) > 0

    # ── reads ─────────────────────────────────────────────────────────────────

    async def get(self, task_id: str, *, with_snapshot: bool = False) -> dict[str, Any] | None:
        cols = _LIST_COLS + (", t.snapshot" if with_snapshot else "")
        r = (await self._s.execute(
            text(f"""SELECT {cols}, w.name AS workflow_name, e.status AS run_status
                     FROM human_tasks t
                     LEFT JOIN workflows w ON w.id = t.workflow_id
                     LEFT JOIN executions e ON e.id = t.execution_id
                     WHERE t.id = :id"""),
            {"id": task_id},
        )).mappings().first()
        return _row(r) if r else None

    async def list(
        self, *, project_id: str | None = None, status: str = "open", workflow_id: str | None = None,
        assignee: str | None = None, cursor: str | None = None, limit: int = 25,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Keyset page ordered by ``(created_at DESC, id DESC)``; returns ``(rows, next_cursor)``."""
        where, params = ["1=1"], {}
        if project_id:
            where.append("t.project_id = :project_id")
            params["project_id"] = project_id
        if status == "open":
            where.append("t.status = 'open'")
        elif status == "closed":
            where.append("t.status <> 'open'")
        if workflow_id:
            where.append("t.workflow_id = :workflow_id")
            params["workflow_id"] = workflow_id
        if assignee:
            where.append("t.assignees @> CAST(:assignee AS jsonb)")
            params["assignee"] = json.dumps([assignee])
        if cursor:
            decoded = decode_cursor(cursor)
            if decoded is None:
                raise ValueError("bad cursor")
            where.append("(t.created_at, t.id) < (:c_at, :c_id)")
            params["c_at"], params["c_id"] = decoded
        params["lim"] = limit + 1
        rows = [
            _row(r) for r in (await self._s.execute(
                text(f"""SELECT {_LIST_COLS}, w.name AS workflow_name
                         FROM human_tasks t LEFT JOIN workflows w ON w.id = t.workflow_id
                         WHERE {' AND '.join(where)}
                         ORDER BY t.created_at DESC, t.id DESC LIMIT :lim"""),
                params,
            )).mappings().all()
        ]
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
        return rows, nxt

    async def counts(self, project_id: str | None = None) -> dict[str, Any]:
        """``{open, by_workflow}``: open tasks (the "3 waiting" badge)."""
        params: dict[str, Any] = {}
        where = "status = 'open'"
        if project_id:
            where += " AND project_id = :p"
            params["p"] = project_id
        rows = (await self._s.execute(
            text(f"SELECT workflow_id, count(*) AS n FROM human_tasks WHERE {where} GROUP BY workflow_id"), params
        )).all()
        by_wf = {r[0]: int(r[1]) for r in rows}
        return {"open": sum(by_wf.values()), "by_workflow": by_wf}

    async def blocking_count(self, execution_id: str, exclude_task_id: str | None = None) -> int:
        """Tasks that keep an execution ``waiting``: still open, or decided and not yet resumed (``pending``/``running``)."""
        r = await self._s.execute(
            text("""SELECT count(*) FROM human_tasks
                    WHERE execution_id = :e AND (status = 'open' OR resume_state IN ('pending', 'running'))
                      AND (CAST(:x AS text) IS NULL OR id <> :x)"""),
            {"e": execution_id, "x": exclude_task_id},
        )
        return int(r.scalar() or 0)

    async def open_ids(self, execution_id: str) -> list[str]:
        r = await self._s.execute(
            text("SELECT id FROM human_tasks WHERE execution_id = :e AND status = 'open' ORDER BY created_at, id"),
            {"e": execution_id},
        )
        return [x[0] for x in r.all()]

    # ── writes ────────────────────────────────────────────────────────────────

    async def decide(
        self, task_id: str, status: str, decision: dict[str, Any], decided_by: str, key: str, decided_at: datetime,
    ) -> dict[str, Any] | None:
        """The one decisive UPDATE: only an ``open`` task can be decided, so exactly one caller wins."""
        r = (await self._s.execute(
            text("""UPDATE human_tasks
                       SET status = :status, decision = CAST(:decision AS jsonb), decided_by = :by, decided_at = :at,
                           decide_key = :key, resume_state = 'pending', updated_at = now()
                     WHERE id = :id AND status = 'open'
                 RETURNING id, execution_id, status"""),
            {"id": task_id, "status": status, "decision": json.dumps(decision, default=str), "by": decided_by,
             "at": decided_at, "key": key},
        )).mappings().first()
        await self._s.commit()
        return dict(r) if r else None

    async def cancel_open_for_execution(self, execution_id: str) -> int:
        r = await self._s.execute(
            text("UPDATE human_tasks SET status = 'cancelled', updated_at = now() WHERE execution_id = :e AND status = 'open'"),
            {"e": execution_id},
        )
        return r.rowcount or 0

    async def set_notify(self, task_id: str, outcome: dict[str, Any]) -> None:
        """Record the approver email's outcome on the task (``snapshot.notify``; no column of its own)."""
        await self._s.execute(
            text("""UPDATE human_tasks SET snapshot = jsonb_set(snapshot, '{notify}', CAST(:n AS jsonb), true), updated_at = now()
                    WHERE id = :id"""),
            {"id": task_id, "n": json.dumps(outcome, default=str)},
        )
        await self._s.commit()

    async def set_resume_state(self, task_id: str, state: str, *, only_from: tuple[str, ...] | None = None) -> bool:
        sql = "UPDATE human_tasks SET resume_state = :s, updated_at = now() WHERE id = :id"
        params: dict[str, Any] = {"s": state, "id": task_id}
        if only_from:
            sql += " AND resume_state = ANY(:from_states)"
            params["from_states"] = list(only_from)
        r = await self._s.execute(text(sql), params)
        await self._s.commit()
        return (r.rowcount or 0) > 0

    async def heartbeat(self, task_id: str) -> None:
        await self._s.execute(
            text("UPDATE human_tasks SET resume_heartbeat = now() WHERE id = :id AND resume_state = 'running'"), {"id": task_id}
        )
        await self._s.commit()

    # ── scheduler ─────────────────────────────────────────────────────────────

    async def expire_due(self, limit: int = 100) -> list[dict[str, Any]]:
        """Time out open tasks past ``due_at`` (``SKIP LOCKED``: two engine instances never take the same row).

        The status comes from ``on_timeout`` (approve -> approved, reject -> rejected, else timed_out); the decider is
        ``system:timeout``. Returns ``[{id, execution_id, status}]`` of the tasks that were expired by this call."""
        now = datetime.now(timezone.utc)
        rows = (await self._s.execute(
            text("""SELECT id, on_timeout FROM human_tasks
                     WHERE status = 'open' AND due_at IS NOT NULL AND due_at <= now()
                     ORDER BY due_at LIMIT :lim FOR UPDATE SKIP LOCKED"""),
            {"lim": limit},
        )).all()
        out: list[dict[str, Any]] = []
        for tid, on_timeout in rows:
            status = {"approve": "approved", "reject": "rejected"}.get(on_timeout or "", "timed_out")
            decision = {"decision": status, "comment": "Deadline passed", "form_data": {}, "decided_by": "system:timeout",
                        "decided_at": now.isoformat()}
            r = (await self._s.execute(
                text("""UPDATE human_tasks
                           SET status = :status, decision = CAST(:decision AS jsonb), decided_by = 'system:timeout',
                               decided_at = :at, resume_state = 'pending', updated_at = now()
                         WHERE id = :id AND status = 'open' RETURNING id, execution_id, status"""),
                {"id": tid, "status": status, "decision": json.dumps(decision), "at": now},
            )).mappings().first()
            if r:
                out.append(dict(r))
        await self._s.commit()
        return out

    async def pending_ids(self, older_than_s: float = 5.0, limit: int = 20) -> list[str]:
        """Decided tasks nobody has started resuming (the in-process kick had its chance for ``older_than_s``)."""
        r = await self._s.execute(
            text("""SELECT id FROM human_tasks
                     WHERE resume_state = 'pending' AND updated_at <= now() - make_interval(secs => :age)
                     ORDER BY updated_at LIMIT :lim"""),
            {"age": older_than_s, "lim": limit},
        )
        return [x[0] for x in r.all()]

    async def interrupted(self, boot_id: str = "", *, startup: bool = False, stale_after_s: float = 120.0) -> list[dict[str, Any]]:
        """Resumes that are ``running`` but whose heartbeat has gone stale: nobody is working on them any more.

        Only the heartbeat decides. A boot id says who claimed a resume, not whether that process is alive, so a second
        engine instance must never fail a live peer's resume (its heartbeat is fresh). ``boot_id`` and ``startup`` are
        accepted for old callers and ignored.
        """
        sql = """SELECT id, execution_id, node_name, resume_owner FROM human_tasks
                  WHERE resume_state = 'running'
                    AND (resume_heartbeat IS NULL OR resume_heartbeat < now() - make_interval(secs => :stale))"""
        r = await self._s.execute(text(sql), {"stale": stale_after_s})
        return [dict(x) for x in r.mappings().all()]

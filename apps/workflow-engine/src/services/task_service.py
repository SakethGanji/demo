"""Human tasks: list, read, decide (Stage 5, DESIGN-STAGE5-UIS sections 5.4, 7.1).

``decide`` is idempotent and race-safe: validation first, then one conditional UPDATE (``WHERE status = 'open'``) that exactly
one caller wins. The loser (a double click, a second approver) re-reads and is told who won. The winner kicks the resumer
in the background; the scheduler sweep is the backstop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.actor import Actor
from ..repositories.task_repository import TaskRepository

DECISIONS = ("approved", "rejected")
MAX_COMMENT = 4000
MAX_FIELD_TEXT = 4000
MAX_KEY = 128


class TaskError(Exception):
    """A request problem with a stable code: ``status`` is the HTTP status, ``fields`` maps field name -> message."""

    def __init__(self, status: int, code: str, message: str, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or {}

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            d["fields"] = self.fields
        return d


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def summary_of(row: dict[str, Any]) -> dict[str, Any]:
    """The ``TaskSummary`` of a task row."""
    return {
        "id": row["id"], "workflow_id": row["workflow_id"], "workflow_name": row.get("workflow_name") or row["workflow_id"],
        "execution_id": row["execution_id"], "node_name": row["node_name"], "title": row["title"], "status": row["status"],
        "assignees": list(row.get("assignees") or []), "due_at": _iso(row.get("due_at")), "created_at": _iso(row["created_at"]),
        "decided_by": row.get("decided_by"), "decided_at": _iso(row.get("decided_at")),
    }


def detail_of(row: dict[str, Any]) -> dict[str, Any]:
    """The ``TaskDetail`` of a task row loaded with its snapshot. The snapshot itself never leaves the engine; the trigger input
    captured at suspend time (``context._input``) is returned as ``input``."""
    snap = row.get("snapshot") or {}
    approval = snap.get("approval") or {}
    ctx = dict(row.get("context") or {})
    inp = ctx.pop("_input", {})
    return {
        **summary_of(row),
        "context": ctx,
        "form": list(row.get("form") or []),
        "decision": row.get("decision"),
        "screen": snap.get("screen"),
        "input": inp if isinstance(inp, dict) else {},
        "allow_comment": bool(approval.get("allow_comment", True)),
        "require_comment_on_reject": bool(approval.get("require_comment_on_reject", False)),
        "on_timeout": row.get("on_timeout") or "timed_out",
        "run_status": row.get("run_status") or "unknown",
    }


def validate_form_data(form: list[dict[str, Any]], data: Any) -> dict[str, str]:
    """Check the approver's answers against the task form. Returns ``{field: message}`` (empty = valid)."""
    errors: dict[str, str] = {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {"form_data": "must be an object"}
    spec = {f["name"]: f for f in form}
    for name in data:
        if name not in spec:
            errors[name] = "is not a field of this form"
    for name, f in spec.items():
        present = name in data and data[name] is not None and data[name] != ""
        if not present:
            if f.get("required") and f.get("type") != "checkbox":
                errors[name] = "is required"
            continue
        v, t = data[name], f.get("type", "text")
        if t in ("text", "textarea"):
            if not isinstance(v, str):
                errors[name] = "must be text"
            elif len(v) > MAX_FIELD_TEXT:
                errors[name] = f"must be at most {MAX_FIELD_TEXT} characters"
        elif t == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors[name] = "must be a number"
        elif t == "checkbox":
            if not isinstance(v, bool):
                errors[name] = "must be true or false"
        elif t == "select":
            if v not in (f.get("options") or []):
                errors[name] = "must be one of the listed options"
    return errors


def authorize_decide(actor: Actor, task: dict[str, Any]) -> None:
    """The one place assignee and four-eyes enforcement will go once identity exists. Stage 5: always allowed."""
    return None


def _check_text(value: Any, field: str, *, required: bool, max_len: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise TaskError(422, "VALIDATION_FAILED", f"{field} is required", {field: "is required"})
        return None
    if not isinstance(value, str):
        raise TaskError(422, "VALIDATION_FAILED", f"{field} must be text", {field: "must be text"})
    value = value.strip()
    if len(value) > max_len:
        raise TaskError(422, "VALIDATION_FAILED", f"{field} is too long", {field: f"must be at most {max_len} characters"})
    return value


class TaskService:
    def __init__(self, session: AsyncSession, *, kick_resume: bool = True) -> None:
        self._session = session
        self._repo = TaskRepository(session)
        self._kick = kick_resume

    async def list(
        self, *, project_id: str | None = None, status: str = "open", workflow_id: str | None = None,
        assignee: str | None = None, cursor: str | None = None, limit: int = 25,
    ) -> dict[str, Any]:
        if status not in ("open", "closed", "all"):
            raise TaskError(422, "VALIDATION_FAILED", "status must be open, closed or all", {"status": "must be open, closed or all"})
        if not 1 <= limit <= 100:
            raise TaskError(422, "VALIDATION_FAILED", "limit must be 1 to 100", {"limit": "must be 1 to 100"})
        try:
            rows, nxt = await self._repo.list(project_id=project_id, status=status, workflow_id=workflow_id,
                                              assignee=assignee, cursor=cursor, limit=limit)
        except ValueError:
            raise TaskError(422, "INVALID_CURSOR", "The cursor is not valid", {"cursor": "is not valid"})
        return {"items": [summary_of(r) for r in rows], "next_cursor": nxt}

    async def get(self, task_id: str) -> dict[str, Any]:
        row = await self._repo.get(task_id, with_snapshot=True)
        if row is None:
            raise TaskError(404, "TASK_NOT_FOUND", "Task not found")
        return detail_of(row)

    async def counts(self, project_id: str | None = None) -> dict[str, Any]:
        return await self._repo.counts(project_id)

    async def decide(
        self, task_id: str, decision: str, comment: str | None, form_data: dict[str, Any] | None, decided_by: str | None,
        idempotency_key: str | None, *, actor: Actor | None = None,
    ) -> dict[str, Any]:
        """Decide a task. Returns ``{task, outcome}`` with outcome ``decided`` (this call won, or a retry of the winning
        call), ``already_decided`` (someone else decided; ``task`` carries the winner) or ``already_closed``
        (cancelled, timed out, or its run ended)."""
        key = _check_text(idempotency_key, "Idempotency-Key", required=True, max_len=MAX_KEY)
        row = await self._repo.get(task_id, with_snapshot=True)
        if row is None:
            raise TaskError(404, "TASK_NOT_FOUND", "Task not found")
        actor = actor or Actor(id=None, label=(decided_by or "").strip(), kind="typed")
        authorize_decide(actor, row)

        if row["status"] != "open":
            return self._lost(row, key)

        if decision not in DECISIONS:
            raise TaskError(422, "VALIDATION_FAILED", "decision must be approved or rejected", {"decision": "must be approved or rejected"})
        by = _check_text(decided_by, "decided_by", required=True, max_len=200)
        approval = (row.get("snapshot") or {}).get("approval") or {}
        comment = _check_text(comment, "comment", required=False, max_len=MAX_COMMENT)
        if comment and not approval.get("allow_comment", True):
            raise TaskError(422, "VALIDATION_FAILED", "comments are turned off for this task", {"comment": "comments are turned off for this task"})
        if decision == "rejected" and approval.get("require_comment_on_reject") and not comment:
            raise TaskError(422, "VALIDATION_FAILED", "a comment is required to reject", {"comment": "is required to reject"})
        errors = validate_form_data(row.get("form") or [], form_data)
        if errors:
            raise TaskError(422, "VALIDATION_FAILED", "The form has errors", errors)

        now = datetime.now(timezone.utc)
        payload = {"decision": decision, "comment": comment, "form_data": form_data or {}, "decided_by": by,
                   "decided_at": now.isoformat()}
        won = await self._repo.decide(task_id, decision, payload, by, key, now)
        if won is not None:
            if self._kick:
                from ..engine.task_resumer import schedule_resume

                schedule_resume(task_id)
            fresh = await self._repo.get(task_id, with_snapshot=True)
            return {"task": detail_of(fresh), "outcome": "decided"}
        # lost the race (or a retry): re-read and report what is true now
        fresh = await self._repo.get(task_id, with_snapshot=True)
        if fresh is None:
            raise TaskError(404, "TASK_NOT_FOUND", "Task not found")
        return self._lost(fresh, key)

    @staticmethod
    def _lost(row: dict[str, Any], key: str) -> dict[str, Any]:
        detail = detail_of(row)
        if row["status"] in ("approved", "rejected"):
            same = row.get("decide_key") == key
            return {"task": detail, "outcome": "decided" if same else "already_decided"}
        return {"task": detail, "outcome": "already_closed"}

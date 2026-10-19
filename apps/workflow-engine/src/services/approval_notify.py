"""Approver notification: the email an Approval with ``notify_email`` sends when its task is created.

The Approval node resolves ``notify_email`` into the suspend payload (``notify: {to, invalid, message}``). Once
``ExecutionRepository.complete`` has committed the ``human_tasks`` row (so the link can carry the task id), it calls
``notify_created_tasks``: one email per task, to every valid address, with a button to the studio's task page
(``WORKFLOW_STUDIO_URL`` + ``/p/<project slug>/approvals/<task id>``).

Nothing here ever blocks the run: a missing SMTP host, a refused send or a bad address is logged as a warning and recorded
on the task as ``snapshot.notify = {to, invalid?, sent, error? | note? | message_id?, at}`` (no migration). Test runs never
send (a mocked Approval never suspends, and ``complete`` skips test mode).
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

MAX_CONTEXT_ROWS = 12
MAX_CONTEXT_VALUE_CHARS = 500
NO_SMTP_NOTE = "SMTP isn't configured (WORKFLOW_SMTP_HOST), so the approver email was not sent."
NO_RECIPIENT_ERROR = "notify_email has no valid email address"


def split_recipients(value: Any) -> tuple[list[str], list[str]]:
    """``"a@x.example, nope; b@x.example"`` (or a list) -> ``(["a@x.example", "b@x.example"], ["nope"])``. Blank entries are
    dropped and a repeat (any case) is kept once."""
    from ..nodes.integrations.send_email import _ADDRESS

    if value is None:
        return [], []
    parts = value if isinstance(value, (list, tuple)) else re.split(r"[,;]", str(value))
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for p in parts:
        a = str(p if p is not None else "").strip()
        if not a or a.lower() in seen:
            continue
        seen.add(a.lower())
        (valid if _ADDRESS.match(a) else invalid).append(a[:320])
    return valid, invalid


def task_link(project_slug: str, task_id: str, base_url: str | None = None) -> str:
    """The studio's task page: ``/p/$project/approvals/$taskId`` (``$project`` is the project slug)."""
    if base_url is None:
        from ..core.config import settings

        base_url = settings.studio_url
    return f"{str(base_url or '').rstrip('/')}/p/{quote(project_slug or 'default', safe='')}/approvals/{quote(task_id, safe='')}"


def context_rows(ctx: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The context values the approver sees, as ``(label, text)``: captured ``_*`` keys are skipped, at most 12 rows, each
    value cut to 500 characters."""
    rows: list[tuple[str, str]] = []
    for k, v in (ctx or {}).items():
        if str(k).startswith("_"):
            continue
        if len(rows) >= MAX_CONTEXT_ROWS:
            rows.append(("…", "more values on the task page"))
            break
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        if len(text) > MAX_CONTEXT_VALUE_CHARS:
            text = text[:MAX_CONTEXT_VALUE_CHARS - 1] + "…"
        rows.append((str(k), text))
    return rows


def _due_text(due: Any) -> str:
    if not due:
        return ""
    if isinstance(due, str):
        try:
            due = datetime.fromisoformat(due)
        except ValueError:
            return due
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(due)


def build_email(
    task: dict[str, Any], *, workflow_name: str, project_name: str, link: str, message: str = "",
) -> tuple[str, str, str]:
    """``(subject, html, text)`` of the approver email for a task row (``title``, ``context``, ``due_at``)."""
    from ..utils.report_html import report_document

    title = str(task.get("title") or "Approval")
    subject = f"Approval needed: {title}"[:250]
    rows = context_rows(task.get("context"))
    due = _due_text(task.get("due_at"))
    e = _html.escape

    parts = [f"<h1>{e(title)}</h1>",
             f"<p><strong>Workflow:</strong> {e(workflow_name)}<br><strong>Project:</strong> {e(project_name)}</p>"]
    if message:
        parts.append(f"<p>{e(message)}</p>".replace("\n", "<br>"))
    if rows:
        body = "".join(f"<tr><td><strong>{e(k)}</strong></td><td>{e(v)}</td></tr>" for k, v in rows)
        parts.append(f"<table><tbody>{body}</tbody></table>")
    if due:
        parts.append(f"<p><strong>Decide by:</strong> {e(due)}</p>")
    parts.append(
        f'<p><a href="{e(link, quote=True)}" style="display:inline-block;padding:10px 18px;background:#1f6feb;color:#ffffff;'
        f'border-radius:6px;text-decoration:none;font-weight:600">Open the task</a></p>'
        f'<p style="font-size:12px;color:#666666">Or paste this link into your browser: {e(link)}</p>'
    )
    html = report_document("\n".join(parts), subject, heading=False)

    lines = [title, "", f"Workflow: {workflow_name}", f"Project: {project_name}"]
    if message:
        lines += ["", message]
    if rows:
        lines += [""] + [f"{k}: {v}" for k, v in rows]
    if due:
        lines += ["", f"Decide by: {due}"]
    lines += ["", f"Open the task: {link}"]
    return subject, html, "\n".join(lines)


async def send_notification(
    notify: dict[str, Any], task: dict[str, Any], *, workflow_name: str, project_name: str, project_slug: str,
) -> dict[str, Any]:
    """Send one approver email for ``task`` (needs ``id``). Never raises: returns the outcome to record on the task."""
    from ..core.config import settings
    from ..nodes.integrations.send_email import deliver_email

    to = list(notify.get("to") or [])
    invalid = list(notify.get("invalid") or [])
    out: dict[str, Any] = {"to": to, "sent": False, "at": datetime.now(timezone.utc).isoformat()}
    if invalid:
        out["invalid"] = invalid
        logger.warning("approval task %s: skipped invalid notify_email address(es) %s", task.get("id"), invalid)
    if not to:
        out["error"] = NO_RECIPIENT_ERROR
        logger.warning("approval task %s: %s", task.get("id"), NO_RECIPIENT_ERROR)
        return out
    if not settings.smtp_host:
        out["note"] = NO_SMTP_NOTE
        logger.warning("approval task %s: %s", task.get("id"), NO_SMTP_NOTE)
        return out
    try:
        link = task_link(project_slug, str(task["id"]))
        subject, html, text = build_email(task, workflow_name=workflow_name, project_name=project_name, link=link,
                                          message=str(notify.get("message") or ""))
        out["message_id"] = await deliver_email(to, subject, text, html)
        out["sent"] = True
    except Exception as e:  # EmailSendError carries the SMTP text (never the password); anything else is a bug, still not fatal
        out["error"] = str(e) or type(e).__name__
        logger.warning("approval task %s: approver email failed: %s", task.get("id"), out["error"])
    return out


async def notify_created_tasks(session: Any, created: list[dict[str, Any]], *, workflow_name: str, project_id: str | None) -> None:
    """Email the approvers of tasks this run just created and record each outcome on its task. ``created`` is
    ``[{"task": <inserted row>, "notify": <payload notify>}]``. Never raises."""
    from ..db.models import ProjectModel
    from ..repositories.task_repository import TaskRepository

    if not created:
        return
    project_name, project_slug = project_id or "default", project_id or "default"
    try:
        project = await session.get(ProjectModel, project_id) if project_id else None
        if project is not None:
            project_name, project_slug = project.name, project.slug
    except Exception:
        logger.warning("approval notify: couldn't load project %s", project_id, exc_info=True)
    repo = TaskRepository(session)
    for entry in created:
        task = entry["task"]
        outcome = await send_notification(entry["notify"], task, workflow_name=workflow_name, project_name=project_name,
                                          project_slug=project_slug)
        try:
            await repo.set_notify(task["id"], outcome)
        except Exception:
            logger.warning("approval task %s: couldn't record the notify outcome", task.get("id"), exc_info=True)
            try:
                await session.rollback()
            except Exception:
                pass

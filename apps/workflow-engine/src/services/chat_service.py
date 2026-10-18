"""Interface chat (Stage 5, DESIGN-STAGE5-UIS section 6.2).

A chat session is a bearer-token-scoped conversation with one workflow. Each user message starts one detached run
whose ``ChatInput`` item carries the last 20 messages (at most 32 KB). One turn may be in flight per session
(``409 CHAT_BUSY``), which keeps history ordered. The assistant's reply is written when the run completes: by
``finish_turn`` for a run that ends while the starting task is still alive, and by the ``on_complete_hooks`` hook
(``register_hooks``) for a run that was suspended on an approval and resumed later. Both go through the same
idempotent writer (``sys:final:<execution>`` is the message's dedupe key), so a reply is never written twice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..repositories.chat_repository import ChatRepository
from . import interface_service as isvc
from .interface_service import InterfaceError, Target, hash_token, new_id, new_token, token_matches

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 16 * 1024
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_BYTES = 32 * 1024
SESSION_TOKEN_PREFIX = "cs_"
FINAL_KEY = "sys:final:"
WAIT_KEY = "sys:wait:"


def reply_text(context_or_result: Any) -> str:
    """The assistant message of a finished turn (section 6.2), first that exists:

    1. the Respond body's ``text`` / ``message`` / ``response`` (string);
    2. the last node's first item ``response`` / ``text`` / ``message`` / ``output``;
    3. the JSON text of the Respond body (or of the last item), truncated to 16 KB.
    """
    ctx = context_or_result
    body = getattr(getattr(ctx, "webhook_response", None), "body", None)
    last = None
    states = getattr(ctx, "node_states", None) or {}
    name = getattr(ctx, "last_completed_node", None)
    if name and states.get(name):
        first = states[name][0]
        last = getattr(first, "json", None)
    if isinstance(body, dict):
        for key in ("text", "message", "response"):
            if isinstance(body.get(key), str) and body[key]:
                return _cap(body[key])
    elif isinstance(body, str) and body:
        return _cap(body)
    if isinstance(last, dict):
        for key in ("response", "text", "message", "output"):
            if isinstance(last.get(key), str) and last[key]:
                return _cap(last[key])
    for candidate in (body, last):
        if candidate not in (None, "", {}, []):
            try:
                return _cap(json.dumps(candidate, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                continue
    return ""


def _cap(text: str) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= MAX_MESSAGE_BYTES else raw[:MAX_MESSAGE_BYTES].decode("utf-8", "ignore")


def trim_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ChatInput ``history``: at most 20 messages and 32 KB, oldest dropped first."""
    items = [{"role": m["role"], "content": m["content"],
              "at": m["created_at"].isoformat() if hasattr(m.get("created_at"), "isoformat") else m.get("created_at")}
             for m in messages[-MAX_HISTORY_MESSAGES:]]
    while items and sum(len(i["content"].encode("utf-8")) for i in items) > MAX_HISTORY_BYTES:
        items.pop(0)
    return items


class ChatService:
    def __init__(self, session: Any, *, repo: ChatRepository | None = None, interfaces: isvc.InterfaceService | None = None) -> None:
        self._session = session
        self.repo = repo or ChatRepository(session)
        self.interfaces = interfaces or isvc.InterfaceService(session)

    # ── sessions ─────────────────────────────────────────────────────────────────────────────

    async def create_session(self, t: Target, user_label: str | None) -> dict[str, Any]:
        if t.kind != "chat":
            raise isvc.not_found()
        sid, token = new_id("chs"), new_token(SESSION_TOKEN_PREFIX)
        label = (user_label or "").strip()[:200] or None
        await self.repo.create_session(id=sid, project_id=t.project_id, workflow_id=t.workflow_id, slug=t.slug, mode=t.mode,
                                       user_label=label, token_hash=hash_token(token))
        return {"session_id": sid, "session_token": token}

    async def _session_row(self, t: Target, session_id: str, token: str | None) -> dict[str, Any]:
        """The session if ``token`` owns it for this (project, slug, mode); everything else is a 404."""
        row = await self.repo.get_session(session_id)
        if (row is None or row["project_id"] != t.project_id or row["slug"] != t.slug or row["mode"] != t.mode
                or not token_matches(token, row["token_hash"])):
            raise isvc.not_found()
        return row

    async def messages(self, t: Target, session_id: str, token: str | None, after: int = 0, limit: int = 50) -> dict[str, Any]:
        row = await self._session_row(t, session_id, token)
        limit = max(1, min(int(limit), 100))
        msgs = await self.repo.messages(session_id, max(0, int(after)), limit)
        out = [{"seq": m["seq"], "role": m["role"], "content": m["content"],
                "created_at": m["created_at"].isoformat() if hasattr(m["created_at"], "isoformat") else m["created_at"]} for m in msgs]
        busy = bool(row.get("busy_execution_id")) and await self.repo._still_busy(row["busy_execution_id"], bool(row.get("fresh", True)))
        await self.repo.rollback()
        return {"messages": out, "busy": busy, "next_after": out[-1]["seq"] if out else max(0, int(after))}

    # ── sending ──────────────────────────────────────────────────────────────────────────────

    async def send(self, t: Target, session_id: str, token: str | None, content: str, client_message_id: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise InterfaceError(413, "PAYLOAD_TOO_LARGE", "That message is too long.")
        if client_message_id.startswith("sys:"):
            raise InterfaceError(422, "VALIDATION_FAILED", "client_message_id is invalid.", fields={"client_message_id": "must not start with sys:"})
        text = content.strip()
        if not text:
            raise InterfaceError(422, "VALIDATION_FAILED", "Say something first.", fields={"content": "this field is required"})
        row = await self._session_row(t, session_id, token)
        from ..utils.ids import execution_id as gen_exec_id

        exec_id = gen_exec_id()
        turn = await self.repo.begin_turn(session_id, message_id=new_id("chm"), content=text, client_message_id=client_message_id,
                                          execution_id=exec_id)
        if turn["outcome"] == "missing":
            raise isvc.not_found()
        if turn["outcome"] == "busy":
            raise InterfaceError(409, "CHAT_BUSY", "Wait for the reply before sending another message.")
        if turn["outcome"] == "replay":
            return {"seq": turn["seq"], "execution_id": turn["execution_id"]}
        item = {"message": text, "timestamp": isvc._utcnow(), "session_id": session_id,
                "history": trim_history(turn["history"]), "user_label": row.get("user_label") or ""}
        meta = {"trigger": {"type": "interface", "slug": t.slug},
                "interface": {"slug": t.slug, "mode": t.mode, "chat_session_id": session_id}}
        try:
            exec_id = await self.interfaces._start_run(
                t.workflow_id, execution_id=exec_id, item=item, start_node=t.trigger_node, environment=t.environment, version_id=t.version_id,
                extras=t.extras, metadata=meta, on_finished=lambda ctx: self.finish_turn(session_id, ctx))
        except BaseException:
            await self.repo.add_reply(session_id, message_id=new_id("chm"), role="error",
                                      content="Something went wrong. Please try again.", execution_id=None, clear_busy=True)
            raise
        await self.repo.attach_execution(session_id, turn["seq"], exec_id)
        return {"seq": turn["seq"], "execution_id": exec_id}

    async def finish_turn(self, session_id: str, context: Any) -> None:
        """A run ended (called inside the run's task, with its own session). ``context`` is None when the run crashed."""
        from ..db.session import async_session_factory

        async with async_session_factory() as session:
            await ChatService(session).record_outcome(session_id, context)

    async def record_outcome(self, session_id: str, context: Any, *, waiting: bool | None = None, titles: list[str] | None = None) -> None:
        """Write the assistant / error / waiting message of a finished turn. Idempotent per execution."""
        exec_id = getattr(context, "execution_id", None)
        errors = getattr(context, "errors", None) or []
        suspended = waiting if waiting is not None else bool(getattr(context, "suspensions", None)) and not errors
        if context is None or errors:
            await self.repo.add_reply(session_id, message_id=new_id("chm"), role="error",
                                      content=f"Something went wrong (ref {exec_id or 'unknown'}).", execution_id=exec_id,
                                      clear_busy=True, dedupe_key=f"{FINAL_KEY}{exec_id or new_id('x')}")
        elif suspended:
            title = (titles or [None])[0] or _suspension_title(context)
            await self.repo.add_reply(session_id, message_id=new_id("chm"), role="assistant", content=f"Waiting for {title}…",
                                      execution_id=exec_id, clear_busy=False, dedupe_key=f"{WAIT_KEY}{exec_id}:{title}")
        else:
            content = reply_text(context) or "Done."
            await self.repo.add_reply(session_id, message_id=new_id("chm"), role="assistant", content=content, execution_id=exec_id,
                                      clear_busy=True, dedupe_key=f"{FINAL_KEY}{exec_id}")


def _suspension_title(context: Any) -> str:
    """The title of the first pending approval: ``context.suspensions`` holds ``{node, run_index, suspension, input_items}``
    dicts whose ``suspension`` is a ``Suspension`` (``.payload``) or its dict form."""
    for s in getattr(context, "suspensions", None) or []:
        susp = s.get("suspension") if isinstance(s, dict) else getattr(s, "suspension", None)
        payload = susp.get("payload") if isinstance(susp, dict) else getattr(susp, "payload", None)
        title = payload.get("title") if isinstance(payload, dict) else None
        if title:
            return str(title)
    return "approval"


# ── completion hook (runs for every execution that completes, resumed ones included) ─────────


async def on_execution_complete(context: Any, execution: Any = None) -> None:
    """``ExecutionRepository.on_complete_hooks`` entry: a chat turn that was suspended finishes here after the resume.

    A first-time run is handled by ``finish_turn`` (same idempotent writer, so there is no double message); this hook
    matters for the run that continues after an approval, whose starting task is long gone."""
    exec_id = getattr(context, "execution_id", None)
    if not exec_id:
        return
    try:
        from sqlalchemy import text

        from ..db.session import async_session_factory

        async with async_session_factory() as session:
            r = await session.execute(text("SELECT metadata->'interface'->>'chat_session_id' FROM executions WHERE id=:e"), {"e": exec_id})
            row = r.first()
            sid = row[0] if row else None
            if not sid:
                return
            status = getattr(execution, "status", None)
            tasks = (await session.execute(text("SELECT title FROM human_tasks WHERE execution_id=:e AND status='open' ORDER BY created_at"), {"e": exec_id})).all()
            waiting = bool(tasks) or status == "waiting"
            await session.rollback()
            await ChatService(session).record_outcome(sid, context, waiting=waiting, titles=[t[0] for t in tasks])
    except Exception:   # noqa: BLE001 - a chat reply must never break completing an execution
        logger.exception("chat completion hook failed for %s", exec_id)


_registered = False


def register_hooks() -> bool:
    """Add :func:`on_execution_complete` to ``ExecutionRepository.on_complete_hooks`` (once). False while A's list doesn't exist yet."""
    global _registered
    from ..repositories import execution_repository as er

    hooks = getattr(er, "on_complete_hooks", None)
    if hooks is None:
        hooks = getattr(er.ExecutionRepository, "on_complete_hooks", None)
    if hooks is None:
        return False
    if on_execution_complete not in hooks:
        hooks.append(on_execution_complete)
    _registered = True
    return True

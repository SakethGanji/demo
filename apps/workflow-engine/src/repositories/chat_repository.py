"""Persistence for interface chat (Stage 5, DESIGN-STAGE5-UIS section 6.2): ``chat_sessions`` + ``chat_messages``.

Raw SQL against the frozen tables. ``begin_turn`` is the one place that serialises a session: it locks the session
row, refuses a second in-flight turn, appends the user message and marks the session busy, all in one transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .interface_repository import _row


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_session(self, *, id: str, project_id: str, workflow_id: str, slug: str, mode: str,
                             user_label: str | None, token_hash: str) -> None:
        await self._s.execute(text("""
            INSERT INTO chat_sessions (id, project_id, workflow_id, slug, mode, user_label, token_hash)
            VALUES (:id, :p, :w, :s, :m, :u, :h)"""),
            {"id": id, "p": project_id, "w": workflow_id, "s": slug, "m": mode, "u": user_label, "h": token_hash})
        await self._s.commit()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        r = await self._s.execute(text(
            "SELECT *, (updated_at > now() - interval '2 minutes') AS fresh FROM chat_sessions WHERE id=:i"), {"i": session_id})
        return _row(r.first())

    async def messages(self, session_id: str, after: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        r = await self._s.execute(text("""
            SELECT seq, role, content, created_at FROM chat_messages
             WHERE session_id=:i AND seq > :a ORDER BY seq LIMIT :l"""), {"i": session_id, "a": after, "l": limit})
        return [dict(x._mapping) for x in r.all()]

    async def history(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """The last ``limit`` messages (oldest first); roles user / assistant only."""
        r = await self._s.execute(text("""
            SELECT role, content, created_at FROM chat_messages
             WHERE session_id=:i AND role IN ('user','assistant') ORDER BY seq DESC LIMIT :l"""), {"i": session_id, "l": limit})
        return list(reversed([dict(x._mapping) for x in r.all()]))

    async def begin_turn(self, session_id: str, *, message_id: str, content: str, client_message_id: str,
                         execution_id: str | None = None) -> dict[str, Any]:
        """Lock the session, then one of:

        * ``{"outcome": "replay", "seq", "execution_id"}``: this ``client_message_id`` was already accepted;
        * ``{"outcome": "busy"}``: a turn is in flight (the caller answers 409 CHAT_BUSY);
        * ``{"outcome": "started", "seq", "history"}``: the user message is stored and the session is busy
          (``busy_execution_id`` holds a placeholder until ``attach_execution``). ``execution_id`` is allocated by the
          caller and stored on the user message in the same transaction, so a duplicate ``client_message_id`` always
          replays the original execution id, even while the run is still starting.
        """
        r = await self._s.execute(text("SELECT id, busy_execution_id, last_seq, (updated_at > now() - interval '2 minutes') AS fresh FROM chat_sessions WHERE id=:i FOR UPDATE"), {"i": session_id})
        sess = r.first()
        if sess is None:
            await self._s.rollback()
            return {"outcome": "missing"}
        dup = await self._s.execute(text("SELECT seq, execution_id FROM chat_messages WHERE session_id=:i AND client_message_id=:c"),
                                    {"i": session_id, "c": client_message_id})
        d = dup.first()
        if d is not None:
            await self._s.rollback()
            return {"outcome": "replay", "seq": d[0], "execution_id": d[1]}
        if sess[1] and await self._still_busy(sess[1], bool(sess[3])):
            await self._s.rollback()
            return {"outcome": "busy"}
        hist = await self._s.execute(text("""
            SELECT role, content, created_at FROM chat_messages
             WHERE session_id=:i AND role IN ('user','assistant') ORDER BY seq DESC LIMIT 20"""), {"i": session_id})
        history = list(reversed([dict(x._mapping) for x in hist.all()]))
        seq = (sess[2] or 0) + 1
        await self._s.execute(text("""
            INSERT INTO chat_messages (id, session_id, seq, role, content, client_message_id, execution_id)
            VALUES (:id, :i, :q, 'user', :c, :cm, :e)"""), {"id": message_id, "i": session_id, "q": seq, "c": content,
                                                             "cm": client_message_id, "e": execution_id})
        await self._s.execute(text("UPDATE chat_sessions SET last_seq=:q, busy_execution_id='pending', updated_at=now() WHERE id=:i"),
                              {"q": seq, "i": session_id})
        await self._s.commit()
        return {"outcome": "started", "seq": seq, "history": history}

    async def _still_busy(self, busy_execution_id: str, fresh: bool) -> bool:
        """Is the turn named by ``busy_execution_id`` still in flight?

        A run that is running / waiting is. ``pending`` (between the lock and the run id) and a run that already finished
        count only while ``fresh`` (the session was touched in the last 2 minutes): that grace covers the moment between a
        run finishing and its reply being written, so a client never sees "not busy" without the reply, while a run that
        died without writing one cannot block the session for long. A vanished run never blocks."""
        if busy_execution_id == "pending":
            return fresh
        r = await self._s.execute(text("SELECT status FROM executions WHERE id=:e"), {"e": busy_execution_id})
        row = r.first()
        if row is None:
            return False
        if row[0] in ("running", "waiting"):
            return True
        if row[0] in ("cancelled", "canceled"):   # cancelled runs write no reply, so there is nothing to wait for
            return False
        return fresh

    async def attach_execution(self, session_id: str, seq: int, execution_id: str) -> None:
        await self._s.execute(text("UPDATE chat_sessions SET busy_execution_id=:e, updated_at=now() WHERE id=:i"), {"e": execution_id, "i": session_id})
        await self._s.execute(text("UPDATE chat_messages SET execution_id=:e WHERE session_id=:i AND seq=:q"), {"e": execution_id, "i": session_id, "q": seq})
        await self._s.commit()

    async def add_reply(self, session_id: str, *, message_id: str, role: str, content: str, execution_id: str | None,
                        clear_busy: bool, dedupe_key: str | None = None) -> int:
        """Append an assistant / error message (next seq) and, if ``clear_busy``, free the session.

        ``dedupe_key`` (stored in ``client_message_id``) makes the append idempotent: a second call with the same key
        adds nothing, frees nothing and returns 0. A reply only frees the session while the session is still busy with
        *its* run (``execution_id``; ``None`` = a start that failed before a run existed), so a late or repeated report of
        turn 1 can never release turn 2. Returns the new seq."""
        r = await self._s.execute(text("SELECT last_seq FROM chat_sessions WHERE id=:i FOR UPDATE"), {"i": session_id})
        row = r.first()
        if row is None:
            await self._s.rollback()
            return 0
        seq = (row[0] or 0) + 1
        ins = await self._s.execute(text("""
            INSERT INTO chat_messages (id, session_id, seq, role, content, execution_id, client_message_id)
            VALUES (:id, :i, :q, :r, :c, :e, :k)
            ON CONFLICT (session_id, client_message_id) WHERE client_message_id IS NOT NULL DO NOTHING RETURNING seq"""),
                                    {"id": message_id, "i": session_id, "q": seq, "r": role, "c": content, "e": execution_id, "k": dedupe_key})
        inserted = ins.first() is not None
        await self._s.execute(text("""
            UPDATE chat_sessions SET last_seq = CASE WHEN :ins THEN :q ELSE last_seq END, updated_at=now(),
                   busy_execution_id = CASE WHEN :clr AND :ins AND (busy_execution_id = CAST(:e AS text) OR CAST(:e AS text) IS NULL)
                                            THEN NULL ELSE busy_execution_id END
             WHERE id=:i"""), {"q": seq, "i": session_id, "clr": clear_busy, "ins": inserted, "e": execution_id})
        await self._s.commit()
        return seq if inserted else 0

    async def rollback(self) -> None:
        await self._s.rollback()

    async def release(self, session_id: str) -> None:
        await self._s.execute(text("UPDATE chat_sessions SET busy_execution_id=NULL, updated_at=now() WHERE id=:i"), {"i": session_id})
        await self._s.commit()

"""Persistence for interfaces (Stage 5): slug index, submissions, and read-only views of executions / tasks.

Raw SQL against the frozen tables of DESIGN-STAGE5-UIS section 3.2 (``interface_slugs``, ``interface_submissions``,
``human_tasks``) and the existing ``executions`` / ``node_outputs``. Nothing here depends on SQLModel classes for the
new tables, so the service layer can be unit-tested with an in-memory fake of this class.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_READY = False


def _row(r: Any) -> dict[str, Any] | None:
    return dict(r._mapping) if r is not None else None


class InterfaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── slug index ───────────────────────────────────────────────────────────────────────────

    async def tables_ready(self) -> bool:
        """False until the Stage 5 migration has run (so older code paths never touch a missing table)."""
        global _READY
        if _READY:
            return True
        r = await self._s.execute(text("SELECT to_regclass('interface_slugs') IS NOT NULL"))
        _READY = bool(r.scalar())
        return _READY

    async def get_slug(self, project_id: str, slug: str) -> dict[str, Any] | None:
        if not await self.tables_ready():
            return None
        r = await self._s.execute(text("SELECT * FROM interface_slugs WHERE project_id=:p AND slug=:s"), {"p": project_id, "s": slug})
        return _row(r.first())

    async def slug_of_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        if not await self.tables_ready():
            return None
        r = await self._s.execute(text("SELECT * FROM interface_slugs WHERE workflow_id=:w"), {"w": workflow_id})
        return _row(r.first())

    async def claim_slug(self, project_id: str, slug: str, workflow_id: str, kind: str, version_id: int) -> bool:
        """Point ``(project, slug)`` at ``workflow_id``'s version. False when another workflow owns the slug.

        One statement decides it (``ON CONFLICT ... WHERE workflow_id = EXCLUDED.workflow_id``), so two concurrent
        publishes can't both win. A workflow that changes slug releases its old one in the same transaction."""
        await self._s.execute(text("DELETE FROM interface_slugs WHERE workflow_id=:w AND NOT (project_id=:p AND slug=:s)"),
                              {"w": workflow_id, "p": project_id, "s": slug})
        r = await self._s.execute(text("""
            INSERT INTO interface_slugs (project_id, slug, workflow_id, kind, version_id, updated_at)
            VALUES (:p, :s, :w, :k, :v, now())
            ON CONFLICT (project_id, slug) DO UPDATE
               SET kind = EXCLUDED.kind, version_id = EXCLUDED.version_id, updated_at = now()
             WHERE interface_slugs.workflow_id = EXCLUDED.workflow_id
            RETURNING slug"""), {"p": project_id, "s": slug, "w": workflow_id, "k": kind, "v": version_id})
        return r.first() is not None

    async def release_slug(self, workflow_id: str) -> None:
        if not await self.tables_ready():
            return
        await self._s.execute(text("DELETE FROM interface_slugs WHERE workflow_id=:w"), {"w": workflow_id})

    async def purge_workflow(self, workflow_id: str) -> None:
        """A deleted workflow takes its slug, submissions and chat sessions (and their messages) with it."""
        if not await self.tables_ready():
            return
        for sql in ("DELETE FROM interface_slugs WHERE workflow_id=:w",
                    "DELETE FROM interface_submissions WHERE workflow_id=:w",
                    "DELETE FROM chat_sessions WHERE workflow_id=:w"):
            await self._s.execute(text(sql), {"w": workflow_id})

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()

    # ── submissions ──────────────────────────────────────────────────────────────────────────

    async def insert_submission(self, *, id: str, project_id: str, workflow_id: str, slug: str, key: str,
                                token_hash: str, mode: str) -> bool:
        """Insert; False when ``(project, slug, key)`` already exists (an idempotent replay)."""
        r = await self._s.execute(text("""
            INSERT INTO interface_submissions (id, project_id, workflow_id, slug, idempotency_key, run_token_hash, mode)
            VALUES (:id, :p, :w, :s, :k, :h, :m)
            ON CONFLICT (project_id, slug, idempotency_key) DO NOTHING RETURNING id"""),
            {"id": id, "p": project_id, "w": workflow_id, "s": slug, "k": key, "h": token_hash, "m": mode})
        return r.first() is not None

    async def get_submission_by_key(self, project_id: str, slug: str, key: str) -> dict[str, Any] | None:
        r = await self._s.execute(text("SELECT * FROM interface_submissions WHERE project_id=:p AND slug=:s AND idempotency_key=:k"),
                                  {"p": project_id, "s": slug, "k": key})
        return _row(r.first())

    async def get_submission_by_execution(self, execution_id: str) -> dict[str, Any] | None:
        r = await self._s.execute(text("SELECT * FROM interface_submissions WHERE execution_id=:e"), {"e": execution_id})
        return _row(r.first())

    async def set_submission_execution(self, submission_id: str, execution_id: str | None) -> None:
        await self._s.execute(text("UPDATE interface_submissions SET execution_id=:e WHERE id=:i"), {"e": execution_id, "i": submission_id})

    async def set_submission_token_hash(self, submission_id: str, token_hash: str) -> None:
        await self._s.execute(text("UPDATE interface_submissions SET run_token_hash=:h WHERE id=:i"), {"h": token_hash, "i": submission_id})

    async def delete_submission(self, submission_id: str) -> None:
        await self._s.execute(text("DELETE FROM interface_submissions WHERE id=:i"), {"i": submission_id})

    # ── read-only views ──────────────────────────────────────────────────────────────────────

    async def get_run(self, execution_id: str) -> dict[str, Any] | None:
        r = await self._s.execute(text("""
            SELECT id, workflow_id, workflow_version_id, status, mode, start_time, end_time, error_count, metadata
              FROM executions WHERE id=:e"""), {"e": execution_id})
        return _row(r.first())

    async def progress(self, execution_id: str) -> dict[str, Any]:
        """``{done, current}``: distinct nodes with a stored output, and the node that produced the latest one."""
        r = await self._s.execute(text("""
            SELECT count(DISTINCT node_name) AS done FROM node_outputs WHERE execution_id=:e"""), {"e": execution_id})
        done = r.scalar() or 0
        r2 = await self._s.execute(text("""
            SELECT node_name FROM node_outputs WHERE execution_id=:e ORDER BY created_at DESC, id DESC LIMIT 1"""), {"e": execution_id})
        row = r2.first()
        return {"done": int(done), "current": row[0] if row else None}

    async def node_outputs(self, execution_id: str) -> dict[str, Any]:
        """``{node: first item json}`` of the successful node outputs of a run (for the studio preview only)."""
        r = await self._s.execute(text("""
            SELECT DISTINCT ON (node_name) node_name, output FROM node_outputs
             WHERE execution_id=:e AND status='success' ORDER BY node_name, run_index DESC"""), {"e": execution_id})
        out: dict[str, Any] = {}
        for name, output in r.all():
            items = output if isinstance(output, list) else (output or {}).get("main") if isinstance(output, dict) else None
            first = items[0] if isinstance(items, list) and items else None
            out[name] = first.get("json") if isinstance(first, dict) and "json" in first else first
        return out

    async def latest_run(self, workflow_id: str) -> dict[str, Any] | None:
        r = await self._s.execute(text("""
            SELECT id, workflow_id, status, mode, start_time, end_time, metadata FROM executions
             WHERE workflow_id=:w AND mode <> 'test' ORDER BY start_time DESC LIMIT 1"""), {"w": workflow_id})
        return _row(r.first())

    async def open_tasks(self, execution_id: str) -> list[dict[str, Any]]:
        r = await self._s.execute(text("""
            SELECT id, title, due_at FROM human_tasks WHERE execution_id=:e AND status='open' ORDER BY created_at, id"""), {"e": execution_id})
        return [dict(x._mapping) for x in r.all()]

    async def tasks_of_execution(self, execution_id: str) -> list[dict[str, Any]]:
        r = await self._s.execute(text("""
            SELECT id, workflow_id, execution_id, node_name, title, status, assignees, due_at, created_at, decided_by, decided_at
              FROM human_tasks WHERE execution_id=:e ORDER BY created_at, id"""), {"e": execution_id})
        return [dict(x._mapping) for x in r.all()]

    async def workflow_row(self, workflow_id: str) -> dict[str, Any] | None:
        r = await self._s.execute(text("SELECT id, name, project_id, active, draft_definition, published_version_id FROM workflows WHERE id=:w"), {"w": workflow_id})
        return _row(r.first())

    async def version_definition(self, version_id: int) -> dict[str, Any] | None:
        r = await self._s.execute(text("SELECT workflow_id, definition FROM workflow_versions WHERE id=:v"), {"v": version_id})
        return _row(r.first())


def jsonb(value: Any) -> str:
    return json.dumps(value, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))

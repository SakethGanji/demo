"""Persistence of multi-part application builds (Capability Graph, PLAN section 5; CONTRACTS-CAPGRAPH.md section 9).

Same conventions as ``ai_proposal_repository``: raw SQL, rows as plain dicts (JSONB decoded), no commits (the caller owns the
transaction) and every state change is one conditional ``UPDATE ... WHERE status = ANY(:from)``, so Postgres row locks decide
races (two Keeps, a Keep against a Discard). ``transition`` returns the row when this caller won and ``None`` otherwise.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.ai_proposal_repository import UnknownColumn, decode_cursor, encode_cursor

STATUSES = ("clarify", "planned", "specs", "building", "ready", "kept", "discarded", "failed")
_JSON_COLS = ("understanding", "answers", "plan", "bundle")
_INSERT_COLS = (
    "id", "project_id", "team_id", "actor", "request", "request_sha256", "status",
    "understanding", "answers", "plan", "bundle", "install_id",
)
#: columns ``transition`` / ``update_fields`` may set; the id, project, asker and request never change
_SETTABLE = frozenset(_INSERT_COLS) - {"id", "project_id", "actor", "request", "request_sha256"} | {"kept_by", "kept_at"}


def new_app_build_id() -> str:
    return "ab_" + uuid.uuid4().hex[:12]


def _row(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    for k in _JSON_COLS:
        if isinstance(out.get(k), (str, bytes)):
            out[k] = json.loads(out[k])
    return out


def _bind(col: str, value: Any) -> Any:
    return json.dumps(value, default=str) if col in _JSON_COLS and value is not None else value


def _placeholder(col: str) -> str:
    return f"CAST(:{col} AS jsonb)" if col in _JSON_COLS else f":{col}"


class AppBuildRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert an app build and return it. ``id`` is generated when missing; unknown keys raise."""
        row = dict(row)
        row.setdefault("id", new_app_build_id())
        bad = set(row) - set(_INSERT_COLS)
        if bad:
            raise UnknownColumn(f"cannot insert {sorted(bad)}")
        for required in ("project_id", "status", "actor", "request", "request_sha256"):
            if row.get(required) is None:
                raise ValueError(f"{required} is required")
        if row["status"] not in STATUSES:
            raise ValueError(f"unknown status {row['status']!r}")
        cols = list(row)
        res = await self._s.execute(
            text(f"INSERT INTO app_builds ({', '.join(cols)}) VALUES ({', '.join(_placeholder(c) for c in cols)}) RETURNING *"),
            {c: _bind(c, row[c]) for c in cols},
        )
        return _row(res.mappings().one())

    async def get(self, app_build_id: str, project_id: str | None = None, *, for_update: bool = False) -> dict[str, Any] | None:
        """The app build, or None. With ``project_id`` a build of another project is None too (404 hides existence)."""
        sql = "SELECT * FROM app_builds WHERE id = :id"
        params: dict[str, Any] = {"id": app_build_id}
        if project_id is not None:
            sql += " AND project_id = :pid"
            params["pid"] = project_id
        if for_update:
            sql += " FOR UPDATE"
        m = (await self._s.execute(text(sql), params)).mappings().first()
        return _row(m) if m else None

    async def list(
        self, project_id: str, *, status: str | None = None, limit: int = 50, cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Newest first; ``(rows, next_cursor)``. The listing leaves out the heavy JSON columns (use ``get`` for those)."""
        limit = max(1, min(int(limit), 200))
        where = ["project_id = :pid"]
        params: dict[str, Any] = {"pid": project_id, "lim": limit + 1}
        if status is not None:
            where.append("status = :status")
            params["status"] = status
        if cursor:
            decoded = decode_cursor(cursor)
            if decoded:
                where.append("(created_at, id) < (:c_at, :c_id)")
                params["c_at"], params["c_id"] = decoded
        cols = "id, project_id, team_id, actor, request_sha256, status, install_id, kept_by, kept_at, created_at, updated_at"
        res = await self._s.execute(
            text(f"SELECT {cols} FROM app_builds WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT :lim"),
            params,
        )
        rows = [_row(m) for m in res.mappings().all()]
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
        return rows, nxt

    async def transition(
        self, app_build_id: str, from_statuses: Iterable[str], to_status: str, fields: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """``UPDATE ... SET status = :to WHERE id = :id AND status = ANY(:from)``; the row when this call won, else None."""
        if to_status not in STATUSES:
            raise ValueError(f"unknown status {to_status!r}")
        return await self._update(app_build_id, list(from_statuses), {**(fields or {}), "status": to_status})

    async def update_fields(
        self, app_build_id: str, fields: dict[str, Any], *, only_in: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Set columns without changing the status; ``only_in`` guards on the current status. None when the guard fails."""
        return await self._update(app_build_id, list(only_in) if only_in is not None else None, dict(fields))

    async def _update(self, app_build_id: str, from_statuses: list[str] | None, fields: dict[str, Any]) -> dict[str, Any] | None:
        bad = set(fields) - _SETTABLE - {"status"}
        if bad:
            raise UnknownColumn(f"cannot set {sorted(bad)}")
        sets = [f"{c} = {_placeholder(c)}" for c in fields] + ["updated_at = now()"]
        params: dict[str, Any] = {c: _bind(c, v) for c, v in fields.items()}
        params["id"] = app_build_id
        sql = f"UPDATE app_builds SET {', '.join(sets)} WHERE id = :id"
        if from_statuses is not None:
            sql += " AND status = ANY(:from_statuses)"
            params["from_statuses"] = from_statuses
        m = (await self._s.execute(text(sql + " RETURNING *"), params)).mappings().first()
        return _row(m) if m else None

"""Append-only audit log writes and reads.

The ``audit_log`` table is protected by a DB trigger that rejects UPDATE/DELETE,
so history cannot be rewritten from the application.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

logger = logging.getLogger(__name__)


async def record(
    *,
    method: str,
    path: str,
    status_code: int,
    action: str | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    team_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert one audit row. Best-effort: never raises into the request path."""
    try:
        async with async_session_factory() as s:
            await s.execute(
                text("""
                    INSERT INTO audit_log
                        (actor_user_id, actor_email, team_id, action, method, path,
                         status_code, resource_type, resource_id, ip, user_agent,
                         request_id, metadata)
                    VALUES
                        (:auid, :aemail, :team, :action, :method, :path,
                         :status, :rtype, :rid, :ip, :ua,
                         :reqid, CAST(:meta AS jsonb))
                """),
                {
                    "auid": actor_user_id if is_uuid(actor_user_id) else None,
                    "aemail": actor_email,
                    "team": team_id if is_uuid(team_id) else None,
                    "action": action or f"{method} {path}",
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "rtype": resource_type,
                    "rid": resource_id,
                    "ip": ip,
                    "ua": user_agent,
                    "reqid": request_id,
                    "meta": json.dumps(metadata) if metadata else None,
                },
            )
            await s.commit()
    except Exception:
        logger.exception("Failed to write audit log for %s %s", method, path)


async def query(
    *,
    team_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Read audit rows, newest first, optionally scoped to *team_ids*."""
    scope = "" if team_ids is None else " WHERE team_id = ANY(:tids)"
    params: dict = {"tids": team_ids, "limit": limit, "offset": offset}
    async with async_session_factory() as s:
        total = (await s.execute(text(f"SELECT COUNT(*) FROM audit_log{scope}"), params)).scalar()
        rows = (await s.execute(
            text(f"""
                SELECT id, occurred_at::text AS occurred_at,
                       actor_user_id::text AS actor_user_id, actor_email,
                       team_id::text AS team_id, action, method, path, status_code,
                       resource_type, resource_id, ip, user_agent, request_id, metadata
                FROM audit_log{scope}
                ORDER BY occurred_at DESC, id DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )).mappings().all()
        return [dict(r) for r in rows], total

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

# The team a request is attributed to is the team that OWNS the resource, not
# the caller's home team: `query(team_ids=...)` is "what happened to our data",
# and a superuser or a cross-team actor must not move a row out of the owning
# team's history. Only these two lookups are needed — every team-scoped write
# route in the service carries one of them in its path.
_TEAM_OF_DATASET = "SELECT team_id::text FROM datasets WHERE id = :id"
_TEAM_OF_VERSION = ("SELECT d.team_id::text FROM dataset_versions v "
                    "JOIN datasets d ON d.id = v.dataset_id WHERE v.id = :id")


async def resolve_target(path_params: dict) -> tuple[str | None, str | None, str | None]:
    """Map a matched route's path params to (team_id, resource_type, resource_id).

    Returns the AGGREGATE ROOT, not the innermost id: a DELETE of
    ``/datasets/{dataset_id}/rules/{rule_id}`` is recorded against the dataset,
    because that is the thing RBAC is scoped to and the thing anyone reading the
    trail asks about ("what happened to this dataset?"). The rule id is still
    in ``metadata.path_params``.

    Any other ``*_id`` path param is recorded as its own resource with no team —
    better an unscoped row naming what it touched than the NULLs this used to
    write for every request in the service.
    """
    if not path_params:
        return None, None, None
    dataset_id = path_params.get("dataset_id")
    if is_uuid(dataset_id):
        return await _team_of(_TEAM_OF_DATASET, dataset_id), "dataset", str(dataset_id)
    version_id = path_params.get("version_id")
    if is_uuid(version_id):
        return (await _team_of(_TEAM_OF_VERSION, version_id),
                "dataset_version", str(version_id))
    team_id = path_params.get("team_id")
    if is_uuid(team_id):
        return str(team_id), "team", str(team_id)
    for name, value in path_params.items():
        if name.endswith("_id") and value not in (None, ""):
            return None, name[:-3], str(value)
    return None, None, None


async def _team_of(sql: str, resource_id: str) -> str | None:
    """The owning team of a dataset/version, or None if it is already gone."""
    try:
        async with async_session_factory() as s:
            return (await s.execute(text(sql), {"id": resource_id})).scalar()
    except Exception:  # noqa: BLE001 — attribution is never worth a failed request
        logger.exception("Failed to resolve audit team for %s", resource_id)
        return None


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
    duration_ms: int | None = None,
) -> None:
    """Insert one audit row. Best-effort: never raises into the request path."""
    try:
        async with async_session_factory() as s:
            await s.execute(
                text("""
                    INSERT INTO audit_log
                        (actor_user_id, actor_email, team_id, action, method, path,
                         status_code, resource_type, resource_id, ip, user_agent,
                         request_id, metadata, duration_ms)
                    VALUES
                        (:auid, :aemail, :team, :action, :method, :path,
                         :status, :rtype, :rid, :ip, :ua,
                         :reqid, CAST(:meta AS jsonb), :durms)
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
                    "durms": duration_ms,
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
                       resource_type, resource_id, ip, user_agent, request_id,
                       metadata, duration_ms
                FROM audit_log{scope}
                ORDER BY occurred_at DESC, id DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )).mappings().all()
        return [dict(r) for r in rows], total

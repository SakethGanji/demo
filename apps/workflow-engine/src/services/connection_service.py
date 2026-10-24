"""Project-level external database connections.

The connection URL is stored only as a project secret (``CONN_<KEY>_URL``, one row per environment); the
``project_connections`` row holds the non-secret facts and the last schema snapshot. Nothing here returns or logs the URL.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.connection_models import ProjectConnectionModel
from ..db.models import ProjectModel
from ..repositories.variable_repository import VariableRepository
from ..utils.ids import generate_id
from . import external_db as xdb
from .project_service import ProjectError, env_names

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CONTEXT_MAX_BYTES = 6000


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "db_" + s if s else "db"
    return s[:40].rstrip("_") or "db"


def secret_key_for(key: str) -> str:
    return "CONN_" + key.upper() + "_URL"


def _public(c: ProjectConnectionModel) -> dict[str, Any]:
    out = {"id": c.id, "key": c.key, "name": c.name, "kind": c.kind, "host": c.host, "database": c.database,
           "readonly": c.readonly, "status": c.status if c.status in ("ok", "error") else "unknown",
           "table_count": len((c.schema_snapshot or {}).get("tables") or [])}
    if c.error:
        out["error"] = c.error
    if c.introspected_at:
        out["introspected_at"] = c.introspected_at.isoformat()
    return out


def parse_url(url: str) -> dict[str, Any]:
    """Host/port/database of a postgres URL; only one explicit TCP host is accepted (same rule as the egress check)."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        info = conninfo_to_dict(url)
    except Exception:
        raise ProjectError(422, "invalid_url", "url is not a valid PostgreSQL connection string") from None
    host = info.get("host", "")
    if (not host or any(c in host for c in ",/ ") or any(k in info for k in ("hostaddr", "service", "servicefile"))
            or "," in info.get("port", "")):
        raise ProjectError(422, "invalid_url", "the url needs one explicit TCP host (services, hostaddr and sockets are not supported)")
    return {"host": host.lower(), "port": int(info["port"]) if info.get("port") else None, "database": info.get("dbname", "")}


class ConnectionService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._vars = VariableRepository(session)

    # ── lookups ──────────────────────────────────────────────────────────────
    async def rows(self, project_id: str) -> list[ProjectConnectionModel]:
        return list((await self._s.execute(select(ProjectConnectionModel).where(
            ProjectConnectionModel.project_id == project_id).order_by(ProjectConnectionModel.created_at))).scalars().all())

    async def by_key(self, project_id: str, key: str) -> ProjectConnectionModel | None:
        return (await self._s.execute(select(ProjectConnectionModel).where(
            ProjectConnectionModel.project_id == project_id, ProjectConnectionModel.key == key))).scalars().first()

    async def require(self, project: ProjectModel, conn_id: str) -> ProjectConnectionModel:
        c = await self._s.get(ProjectConnectionModel, conn_id)
        if c is None or c.project_id != project.id:
            raise ProjectError(404, "connection_not_found", "Connection not found in this project")
        return c

    async def url_of(self, project: ProjectModel, c: ProjectConnectionModel, environment: str | None = None) -> str:
        env = environment or project.default_environment
        v = await self._vars.get_by_key(project.team_id, c.secret_key, environment=env, project_id=project.id)
        if v is None:
            for e in env_names(project):
                v = await self._vars.get_by_key(project.team_id, c.secret_key, environment=e, project_id=project.id)
                if v is not None:
                    break
        if v is None:
            raise ProjectError(409, "connection_secret_missing", f"the secret {c.secret_key} of connection '{c.key}' is gone")
        from .variable_crypto import decrypt_secret

        return decrypt_secret(v.value)

    # ── CRUD ─────────────────────────────────────────────────────────────────
    async def list(self, project: ProjectModel) -> list[dict[str, Any]]:
        return [_public(c) for c in await self.rows(project.id)]

    async def create(self, project: ProjectModel, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 100:
            raise ProjectError(422, "invalid_name", "name is required (at most 100 characters)")
        if (body.get("kind") or "postgres") != "postgres":
            raise ProjectError(422, "unsupported_kind", "only kind 'postgres' is supported")
        url = body.get("url") or ""
        if not isinstance(url, str) or not url.strip():
            raise ProjectError(422, "invalid_url", "url is required")
        url = url.strip()
        facts = parse_url(url)
        key = slugify(name)
        if not KEY_RE.match(key):
            raise ProjectError(422, "invalid_name", "name does not make a usable key")
        if await self.by_key(project.id, key):
            raise ProjectError(409, "exists", f"a connection with key '{key}' already exists in this project", key=key)
        skey = secret_key_for(key)
        for env in env_names(project):
            if await self._vars.get_by_key(project.team_id, skey, environment=env, project_id=project.id):
                raise ProjectError(409, "exists", f"a secret named {skey} already exists in this project")
        for env in env_names(project):
            await self._vars.create(key=skey, value=url, team_id=project.team_id, environment=env, type="secret",
                                    description=f"URL of the '{name}' database connection (managed)", project_id=project.id,
                                    allowed_hosts=[facts["host"]])
        c = ProjectConnectionModel(id=generate_id("conn"), project_id=project.id, key=key, name=name, kind="postgres",
                                   host=facts["host"], port=facts["port"], database=facts["database"],
                                   readonly=body.get("readonly") is not False,  # writable only when asked for explicitly
                                   secret_key=skey)
        self._s.add(c)
        await self._s.commit()
        await self._introspect(c, url)
        return _public(c)

    async def delete(self, project: ProjectModel, conn_id: str) -> None:
        c = await self.require(project, conn_id)
        for env in env_names(project):
            v = await self._vars.get_by_key(project.team_id, c.secret_key, environment=env, project_id=project.id)
            if v is not None:
                await self._vars.delete(v.id)
        await self._s.delete(c)
        await self._s.commit()

    # ── remote work ──────────────────────────────────────────────────────────
    async def test(self, project: ProjectModel, conn_id: str) -> dict[str, Any]:
        c = await self.require(project, conn_id)
        res = await xdb.test_connection(await self.url_of(project, c))
        c.status = "ok" if res["ok"] else "error"
        c.error = None if res["ok"] else res.get("error")
        c.updated_at = datetime.now()
        await self._s.commit()
        return res

    async def _introspect(self, c: ProjectConnectionModel, url: str) -> dict[str, Any]:
        try:
            snap = await xdb.introspect(url)
            c.schema_snapshot, c.status, c.error = snap, "ok", None
            c.introspected_at = datetime.now()
        except xdb.ExternalDbError as e:
            c.status, c.error = "error", e.message
            snap = c.schema_snapshot or {"tables": []}
        c.updated_at = datetime.now()
        await self._s.commit()
        return snap

    async def introspect(self, project: ProjectModel, conn_id: str) -> dict[str, Any]:
        c = await self.require(project, conn_id)
        snap = await self._introspect(c, await self.url_of(project, c))
        if c.status == "error":
            raise ProjectError(502, "introspect_failed", c.error or "could not read the schema")
        return snap

    async def schema(self, project: ProjectModel, conn_id: str) -> dict[str, Any]:
        c = await self.require(project, conn_id)
        return c.schema_snapshot or {"tables": []}

    async def query(self, project: ProjectModel, conn_id: str, sql: str, params: Any = None, limit: Any = None) -> dict[str, Any]:
        c = await self.require(project, conn_id)
        url = await self.url_of(project, c)
        try:
            return await xdb.run_readonly(url, sql, params, limit)
        except xdb.ExternalDbError as e:
            raise ProjectError(e.status, e.code, e.message) from None


# ── runtime (broker / node) ──────────────────────────────────────────────────

async def lookup(session: AsyncSession, project_id: str, key: str) -> ProjectConnectionModel | None:
    return (await session.execute(select(ProjectConnectionModel).where(
        ProjectConnectionModel.project_id == project_id, ProjectConnectionModel.key == key))).scalars().first()


# ── prompt context ───────────────────────────────────────────────────────────

def _col_text(table: str, c: dict[str, Any]) -> str:
    bits = [f"{c['name']} {c['type']}"]
    if c.get("pk"):
        bits.append("pk")
    if c.get("fk"):
        bits.append(f"fk→{c['fk']['table']}.{c['fk']['column']}")
    out = " ".join(bits)
    if c.get("samples"):
        out += " {" + ",".join(str(s) for s in c["samples"]) + "}"
    return out


def render_context(conns: list[dict[str, Any]], tables: list[str] | None = None, max_bytes: int = CONTEXT_MAX_BYTES) -> str:
    """``conns``: ``[{key, readonly, tables: [...snapshot tables...]}]``. One line per table:
    ``key.schema.table (~rows): col type [pk|fk→t.c] {samples}, ...``; tables named in ``tables`` (``name``, ``schema.name``
    or ``key.name``) come first; lines are cut to fit ``max_bytes`` and the rest is counted."""
    wanted = {t.lower() for t in tables or []}

    def prio(key: str, t: dict[str, Any]) -> int:
        names = {t["name"].lower(), f"{t['schema']}.{t['name']}".lower(), f"{key}.{t['name']}".lower(),
                 f"{key}.{t['schema']}.{t['name']}".lower()}
        return 0 if names & wanted else 1

    head = "External databases (read-only; query with studio.connection(key).query(sql, params, limit); these are NOT studio.db tables):"
    lines: list[tuple[int, str]] = []
    for c in conns:
        for t in c.get("tables") or []:
            rows = t.get("row_estimate")
            tag = "" if t.get("kind", "table") == "table" else " view"
            body = ", ".join(_col_text(t["name"], x) for x in t.get("columns") or [])
            lines.append((prio(c["key"], t), f"{c['key']}.{t['schema']}.{t['name']}{tag} (~{rows if rows is not None else '?'} rows): {body}"))
    lines.sort(key=lambda p: p[0])
    out, size, skipped = [head], len(head) + 1, 0
    for pr, line in lines:
        if size + len(line.encode()) + 1 > max_bytes - 60:
            room = max_bytes - 60 - size
            if pr == 0 and room > 200:   # a named table that does not fit whole is cut, not dropped
                line = line[:room - 3] + "..."
                out.append(line)
                size += len(line.encode()) + 1
            else:
                skipped += 1
            continue
        out.append(line)
        size += len(line.encode()) + 1
    if skipped:
        out.append(f"(+{skipped} more tables; ask for them by name)")
    return "\n".join(out) if len(out) > 1 else ""


async def connections_context(project_id: str, tables: list[str] | None = None, keys: list[str] | None = None, *,
                              session: AsyncSession | None = None, max_bytes: int = CONTEXT_MAX_BYTES) -> str:
    """Compact schema summary of the project's external databases for prompts (``""`` when there are none).
    ``tables`` = names to list first; ``keys`` = only these connection keys."""
    if session is None:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            return await connections_context(project_id, tables, keys, session=s, max_bytes=max_bytes)
    conns = [{"key": c.key, "readonly": c.readonly, "tables": (c.schema_snapshot or {}).get("tables") or []}
             for c in await ConnectionService(session).rows(project_id) if not keys or c.key in keys]
    return render_context(conns, tables, max_bytes)


async def facts_for(session: AsyncSession, project_id: str) -> list[dict[str, Any]]:
    """For ``gather_facts``: each connection with its tables marked external/read-only (column names and types, no samples)."""
    out = []
    for c in await ConnectionService(session).rows(project_id):
        tbls = [{"name": t["name"], "schema": t["schema"], "kind": t.get("kind", "table"), "row_count": t.get("row_estimate") or 0,
                 "external": True, "read_only": True, "connection": c.key,
                 "columns": [{"name": x["name"], "type": x["type"]} for x in t.get("columns") or []]}
                for t in (c.schema_snapshot or {}).get("tables") or []]
        out.append({"key": c.key, "name": c.name, "readonly": c.readonly, "status": c.status, "tables": tbls})
    return out

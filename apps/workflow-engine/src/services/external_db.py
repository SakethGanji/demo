"""Talking to a project's external Postgres database: read-only queries and schema introspection.

Everything here takes the connection URL as an argument and never logs or returns it. Errors are rewritten so the URL
(and so any password in it) can not leak through a driver message.
"""

from __future__ import annotations

import re
import time
from typing import Any

import psycopg
from psycopg import sql as psql

from ..utils.serialization import serialize_value

STATEMENT_TIMEOUT_MS = 10_000
CONNECT_TIMEOUT_S = 10
DEFAULT_LIMIT = 100
MAX_LIMIT = 500          # the HTTP route's cap
SDK_MAX_LIMIT = 5000     # studio.connection(...).query / the Postgres node
MAX_TABLES = 200
SAMPLE_VALUES = 5
SAMPLE_SCAN_ROWS = 5000
EXACT_COUNT_BELOW = 50_000
MAX_SAMPLE_QUERIES = 300


class ExternalDbError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _scrub(text: str, url: str | None) -> str:
    out = str(text)
    if url:
        out = out.replace(url, "***")
        try:
            from psycopg.conninfo import conninfo_to_dict

            pw = conninfo_to_dict(url).get("password")
            if pw:
                out = out.replace(str(pw), "***")
        except Exception:
            pass
    return out.strip()[:500]


# ── SQL screening ────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$([A-Za-z_][A-Za-z0-9_]*)?\$.*?\$\1\$", re.S)
_FORBIDDEN_WORDS = {"insert", "update", "delete", "merge", "into", "truncate", "drop", "alter", "create", "grant", "revoke"}
_FORBIDDEN_FUNCS = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_ls_logdir|lo_import|lo_export|dblink\w*|pg_terminate_backend|"
    r"pg_cancel_backend|set_config|pg_reload_conf|pg_advisory\w*|pg_sleep\w*)\s*\(", re.I)


def _blank(sql: str) -> str:
    """The statement with comments, string literals and quoted identifiers blanked out (for keyword scanning)."""
    return _TOKEN_RE.sub(" ", sql)


def check_select(sql: Any) -> str:
    """Return the statement without a trailing ``;`` or raise ``query_not_allowed``: one SELECT/WITH statement only.
    This is the up-front screen; the READ ONLY transaction is what really stops a write."""
    if not isinstance(sql, str) or not sql.strip():
        raise ExternalDbError("query_not_allowed", "sql is required", 422)
    body = _blank(sql).strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    stmt = sql.strip()
    while stmt.endswith(";"):
        stmt = stmt[:-1].rstrip()
    if ";" in body:
        raise ExternalDbError("query_not_allowed", "only one statement is allowed", 422)
    first = re.match(r"\(*\s*([A-Za-z_]+)", body)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise ExternalDbError("query_not_allowed", "only SELECT (or WITH ... SELECT) queries are allowed on a read-only connection", 422)
    bad = sorted({w for w in re.findall(r"[A-Za-z_]+", body.lower()) if w in _FORBIDDEN_WORDS})
    if bad:
        raise ExternalDbError("query_not_allowed", f"read-only connection: '{bad[0].upper()}' is not allowed", 422)
    if _FORBIDDEN_FUNCS.search(body):
        raise ExternalDbError("query_not_allowed", "that function is not allowed on a read-only connection", 422)
    return stmt


def _norm_params(params: Any) -> Any:
    if params is None or params == [] or params == {}:
        return None
    if isinstance(params, (list, tuple)):
        return list(params)
    if isinstance(params, dict):
        return params
    raise ExternalDbError("invalid_params", "params must be a list or an object", 422)


def clamp_limit(limit: Any, maximum: int = MAX_LIMIT) -> int:
    try:
        n = int(limit) if limit is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        raise ExternalDbError("invalid_limit", "limit must be a number", 422) from None
    return max(1, min(n, maximum))


async def _connect(url: str, **kw: Any) -> psycopg.AsyncConnection:
    try:
        return await psycopg.AsyncConnection.connect(url, connect_timeout=CONNECT_TIMEOUT_S, **kw)
    except psycopg.Error as e:
        raise ExternalDbError("connect_failed", _scrub(f"could not connect: {e}", url), 502) from None
    except Exception as e:  # malformed URL and the like
        raise ExternalDbError("connect_failed", _scrub(f"could not connect: {e}", url), 502) from None


async def run_readonly(url: str, sql: str, params: Any = None, limit: Any = DEFAULT_LIMIT, *,
                       max_limit: int = MAX_LIMIT, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> dict[str, Any]:
    """One SELECT in a READ ONLY transaction with a statement timeout; at most ``limit`` rows.
    Returns ``{columns, rows (dicts), truncated, ms}``."""
    stmt = check_select(sql)
    bound = _norm_params(params)
    n = clamp_limit(limit, max_limit)
    t0 = time.perf_counter()
    conn = await _connect(url)
    try:
        await conn.set_read_only(True)
        try:
            async with conn.cursor() as c:
                await c.execute(psql.SQL("SET LOCAL statement_timeout = {}").format(psql.Literal(int(timeout_ms))))
            async with conn.cursor(name="ro_query") as cur:
                await cur.execute(stmt, bound)
                raw = await cur.fetchmany(n + 1)
                columns = [d.name for d in cur.description] if cur.description else []
        except psycopg.errors.QueryCanceled:
            raise ExternalDbError("timeout", f"query took longer than {timeout_ms // 1000}s and was cancelled", 408) from None
        except psycopg.errors.ReadOnlySqlTransaction:
            raise ExternalDbError("query_not_allowed", "read-only connection: writes are not allowed", 422) from None
        except psycopg.Error as e:
            raise ExternalDbError("query_failed", _scrub(getattr(e, "diag", None) and e.diag.message_primary or str(e), url), 400) from None
        finally:
            try:
                await conn.rollback()
            except Exception:
                pass
    finally:
        await conn.close()
    truncated = len(raw) > n
    raw = raw[:n]
    rows = [{c: serialize_value(r[i]) for i, c in enumerate(columns)} for r in raw]
    return {"columns": columns, "rows": rows, "truncated": truncated, "ms": round((time.perf_counter() - t0) * 1000)}


async def test_connection(url: str) -> dict[str, Any]:
    try:
        conn = await _connect(url)
    except ExternalDbError as e:
        return {"ok": False, "error": e.message}
    try:
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            await cur.execute("SELECT current_setting('server_version')")
            row = await cur.fetchone()
        await conn.rollback()
        return {"ok": True, "server_version": row[0] if row else None}
    except psycopg.Error as e:
        return {"ok": False, "error": _scrub(str(e), url)}
    finally:
        await conn.close()


# ── introspection ────────────────────────────────────────────────────────────

SENSITIVE_RE = re.compile(
    r"(e_?mail|phone|mobile|\bcell\b|fax|ssn|social_?sec|tax_?id|passport|licen[sc]e|passw|pwd|passcode|token|secret|api_?key|"
    r"credential|private_?key|salt|hash|address|street|city|zip|postal|postcode|dob|birth|salary|iban|account_?n|card|cvv|"
    r"(first|last|full|middle|given|family|sur|user|customer|contact|person|owner|employee|patient|display|nick)_?name|"
    r"\bip(_?addr)?\b|latitude|longitude|geo|gender|ethnic|religion)", re.I)
_PERSON_TABLE_RE = re.compile(r"(customer|user|person|people|employee|contact|patient|member|account|profile|lead|client|staff|author)", re.I)
_SAMPLE_TYPES = {"text", "character varying", "character", "boolean", "name", "citext"}


def is_sensitive(table: str, column: str) -> bool:
    if SENSITIVE_RE.search(column):
        return True
    return column.lower() == "name" and bool(_PERSON_TABLE_RE.search(table))


_TABLES_SQL = """
SELECT n.nspname, c.relname, c.relkind::text, c.reltuples::bigint, c.oid::bigint
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\\_%%'
ORDER BY (n.nspname = 'public') DESC, n.nspname, c.relname LIMIT %s
"""
_COLUMNS_SQL = """
SELECT a.attrelid::bigint, a.attnum, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
       t.typtype::text, t.typname, a.atttypid::bigint
FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
WHERE a.attrelid = ANY(%s) AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attrelid, a.attnum
"""
_ENUM_SQL = """
SELECT enumtypid::bigint, enumlabel FROM pg_catalog.pg_enum WHERE enumtypid = ANY(%s) ORDER BY enumtypid, enumsortorder
"""
ENUM_LABELS_MAX = 30
_PK_SQL = """
SELECT con.conrelid::bigint, a.attname
FROM pg_catalog.pg_constraint con CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum)
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
WHERE con.contype = 'p' AND con.conrelid = ANY(%s)
"""
_FK_SQL = """
SELECT con.conrelid::bigint, a.attname, fn.nspname, fc.relname, fa.attname
FROM pg_catalog.pg_constraint con CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(attnum, fattnum)
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
JOIN pg_catalog.pg_class fc ON fc.oid = con.confrelid
JOIN pg_catalog.pg_namespace fn ON fn.oid = fc.relnamespace
JOIN pg_catalog.pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = k.fattnum
WHERE con.contype = 'f' AND con.conrelid = ANY(%s)
"""


async def _samples(conn: psycopg.AsyncConnection, schema: str, table: str, column: str) -> list[Any] | None:
    """Up to SAMPLE_VALUES distinct values when the column looks low-cardinality, else None."""
    q = psql.SQL("SELECT DISTINCT c::text FROM (SELECT {col} AS c FROM {sch}.{tbl} WHERE {col} IS NOT NULL LIMIT {n}) s "
                 "ORDER BY 1 LIMIT {k}").format(col=psql.Identifier(column), sch=psql.Identifier(schema),
                                                 tbl=psql.Identifier(table), n=psql.Literal(SAMPLE_SCAN_ROWS),
                                                 k=psql.Literal(SAMPLE_VALUES + 1))
    cnt = psql.SQL("SELECT count(*) FROM (SELECT 1 FROM {sch}.{tbl} WHERE {col} IS NOT NULL LIMIT 20) s").format(
        col=psql.Identifier(column), sch=psql.Identifier(schema), tbl=psql.Identifier(table))
    try:
        async with conn.cursor() as cur:
            await cur.execute(cnt)
            if ((await cur.fetchone()) or [0])[0] < 20:   # too few rows to call anything low-cardinality
                return None
            await cur.execute(q)
            vals = [r[0] for r in await cur.fetchall()]
    except psycopg.Error:
        return None
    if len(vals) > SAMPLE_VALUES or any(len(v) > 40 for v in vals):
        return None
    return vals


async def introspect(url: str) -> dict[str, Any]:
    """The schema snapshot ``{tables: [{schema, name, kind, row_estimate, columns: [{name, type, nullable, pk, fk, samples}]}]}``."""
    conn = await _connect(url, autocommit=True)
    try:
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            await cur.execute(f"SET statement_timeout = {3000}")
            await cur.execute(_TABLES_SQL, (MAX_TABLES,))
            trows = await cur.fetchall()
            oids = [r[4] for r in trows]
            await cur.execute(_COLUMNS_SQL, (oids,))
            crows = await cur.fetchall()
            await cur.execute(_PK_SQL, (oids,))
            pks = {(r[0], r[1]) for r in await cur.fetchall()}
            await cur.execute(_FK_SQL, (oids,))
            fks: dict[tuple[int, str], dict[str, str]] = {}
            for oid, col, fs, ft, fcol in await cur.fetchall():
                fks[(oid, col)] = {"table": ft if fs == "public" else f"{fs}.{ft}", "column": fcol}
            # an enum's labels are schema, not data: list them all (declared order) instead of sampling rows
            await cur.execute(_ENUM_SQL, (list({r[7] for r in crows if r[5] == "e"}),))
            enums: dict[int, list[str]] = {}
            for typ, label in await cur.fetchall():
                enums.setdefault(typ, []).append(label)
        cols_by: dict[int, list[tuple]] = {}
        for r in crows:
            cols_by.setdefault(r[0], []).append(r)
        budget = MAX_SAMPLE_QUERIES
        tables: list[dict[str, Any]] = []
        for schema, name, relkind, est, oid in trows:
            kind = "table" if relkind in ("r", "p", "f") else "view"
            estimate: int | None = int(est) if est is not None and est >= 0 else None
            if kind == "table" and (estimate is None or estimate <= EXACT_COUNT_BELOW):
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(psql.SQL("SELECT count(*) FROM {}.{}").format(psql.Identifier(schema), psql.Identifier(name)))
                        estimate = int((await cur.fetchone())[0])
                except psycopg.Error:
                    pass
            columns = []
            for _, _, cname, ctype, nullable, typtype, typname, typoid in cols_by.get(oid, []):
                samples: list[Any] = list(enums.get(typoid, []))[:ENUM_LABELS_MAX]
                lowtype = typtype == "e" or ctype.split("(")[0] in _SAMPLE_TYPES
                if not samples and lowtype and budget > 0 and not is_sensitive(name, cname) and (estimate is None or estimate >= 20):
                    budget -= 1
                    got = await _samples(conn, schema, name, cname)
                    if got:
                        samples = [v == "true" for v in got] if typname == "bool" else got
                columns.append({"name": cname, "type": ctype, "nullable": bool(nullable), "pk": (oid, cname) in pks,
                                "fk": fks.get((oid, cname)), "samples": samples})
            tables.append({"schema": schema, "name": name, "kind": kind, "row_estimate": estimate, "columns": columns})
        return {"tables": tables}
    except psycopg.Error as e:
        raise ExternalDbError("introspect_failed", _scrub(str(e), url), 502) from None
    finally:
        await conn.close()

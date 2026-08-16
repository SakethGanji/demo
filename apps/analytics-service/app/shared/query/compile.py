"""Compile a validated QuerySpec to SQL and execute it with cursor paging.

Reuses ``compile_filter`` as-is for the WHERE clause (typed models are dumped
back to the dict shape it consumes, with column refs remapped to physical
parquet names). Cursors exploit version immutability: an opaque base64 of
``{"v": version_id, "h": spec_hash, "o": offset}`` — stateless and stable
because the underlying parquet never changes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, NamedTuple

import duckdb

from app.api.errors import ProblemException
from app.shared.filters import compile_filter
from app.shared.query.schemas import QueryPage, QuerySpec
from app.shared.query.validate import _is_text, validate_spec
from app.shared.utils.sql import quote_ident, safe_value


class CompiledQuery(NamedTuple):
    """SQL fragments for one QuerySpec — assemble as
    ``SELECT {select_list} FROM <src> [WHERE {where}] [ORDER BY {order_by}]``."""

    select_list: str
    where: str      # "" when there is no WHERE clause
    order_by: str   # "" when no sort was requested


def _remap_columns(node: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Rewrite a dumped Filter/FilterGroup dict's column refs to physical names."""
    if "conditions" in node:
        return {**node, "conditions": [_remap_columns(c, mapping) for c in node["conditions"]]}
    return {**node, "column": mapping[node["column"]]}


def compile_query(spec: QuerySpec, schema_json: list[dict]) -> tuple[CompiledQuery, list[Any]]:
    """Validate *spec* against *schema_json* and compile it to SQL fragments + binds."""
    mapping = validate_spec(spec, schema_json)
    binds: list[Any] = []

    if spec.columns:
        select_list = ", ".join(
            f"{quote_ident(mapping[c])} AS {quote_ident(c)}" for c in spec.columns)
    else:
        select_list = "*"

    where_parts: list[str] = []
    if spec.filters is not None:
        clause = compile_filter(_remap_columns(spec.filters.model_dump(), mapping), binds)
        # An empty clause here can only mean an intentionally empty group:
        # ``FilterGroup.conditions`` is shape-discriminated, so a malformed
        # condition raises at parse time rather than collapsing into one.
        if clause:
            where_parts.append(clause)
    if spec.search:
        text_cols = [c["name"] for c in schema_json if _is_text(c.get("dtype") or "")]
        if text_cols:
            where_parts.append(compile_filter(
                {"logic": "or", "conditions": [
                    {"column": c, "op": "icontains", "value": spec.search} for c in text_cols
                ]}, binds))
        else:
            where_parts.append("FALSE")  # nothing to search — match no rows

    sort_physical = {mapping[s.column] for s in spec.sort}
    order_terms = [
        f"{quote_ident(mapping[s.column])} {'ASC' if s.direction == 'asc' else 'DESC'}"
        for s in spec.sort
    ]
    # Append every remaining column so the ORDER BY is a *total* order. Paging
    # is LIMIT/OFFSET over separate per-page queries; over a partial order (rows
    # tied on the user's sort key, or no sort at all) DuckDB may return tied
    # rows in a different order each page, silently skipping some rows and
    # duplicating others while `total` still looks right. The parquet is
    # immutable, so ordering by the full row is stable across pages.
    for c in sorted(schema_json, key=lambda c: c.get("position", 0)):
        phys = c["name"]
        if phys not in sort_physical:
            order_terms.append(f"{quote_ident(phys)} ASC")
    order_by = ", ".join(order_terms)

    return CompiledQuery(select_list, " AND ".join(where_parts), order_by), binds


# --- Cursors -----------------------------------------------------------------

def spec_hash(spec: QuerySpec) -> str:
    """sha256 over the canonical JSON of the spec minus cursor/limit."""
    payload = spec.model_dump(mode="json", exclude={"cursor", "limit"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def encode_cursor(version_id: str, hash_: str, offset: int) -> str:
    raw = json.dumps({"v": version_id, "h": hash_, "o": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor; any malformation → problem+json 400 ``invalid-cursor``."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if not (isinstance(data, dict) and isinstance(data.get("o"), int)
                and data["o"] >= 0 and isinstance(data.get("h"), str)
                and isinstance(data.get("v"), str)):
            raise ValueError("bad cursor payload")
    except (binascii.Error, ValueError, UnicodeDecodeError, AttributeError) as exc:
        raise ProblemException(
            400, "Invalid or corrupted cursor", code="invalid-cursor") from exc
    return data


# --- Execution ---------------------------------------------------------------

def execute_query(
    conn: duckdb.DuckDBPyConnection,
    source_table: str,
    spec: QuerySpec,
    schema_json: list[dict],
    *,
    version_id: str,
) -> QueryPage:
    """Run *spec* against *source_table* (table name or read_parquet(...) expr).

    Fetches ``limit + 1`` rows to detect the last page; ``next_cursor`` is None
    when there is no further page. A resubmitted cursor must match both the
    version and the spec's hash, otherwise ``invalid-cursor``.
    """
    compiled, binds = compile_query(spec, schema_json)
    h = spec_hash(spec)

    offset = 0
    if spec.cursor is not None:
        c = decode_cursor(spec.cursor)
        if c["h"] != h or c["v"] != version_id:
            raise ProblemException(
                400, "Cursor does not match this query spec and version",
                code="invalid-cursor")
        offset = c["o"]

    where_sql = f" WHERE {compiled.where}" if compiled.where else ""
    order_sql = f" ORDER BY {compiled.order_by}" if compiled.order_by else ""

    # top_n/pct/duplicate operators reference _filter_src, same as apply_filters.
    conn.execute(f"CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM {source_table}")
    try:
        total: int = conn.execute(
            f"SELECT COUNT(*) FROM {source_table}{where_sql}", binds).fetchone()[0]
        cur = conn.execute(
            f"SELECT {compiled.select_list} FROM {source_table}{where_sql}{order_sql} "
            f"LIMIT {spec.limit + 1} OFFSET {offset}",
            binds,
        )
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except duckdb.Error as e:
        # A value DuckDB rejects only at execution — e.g. an unparseable regex
        # pattern in a filter — is caller input, not a server fault. Translate
        # it to the same 400 the aggregate path already returns instead of a 500.
        raise ProblemException(400, f"Query could not be executed: {e}", code="invalid-query") from e
    finally:
        conn.execute("DROP VIEW IF EXISTS _filter_src")

    has_more = len(rows) > spec.limit
    items = [
        {name: safe_value(v) for name, v in zip(col_names, row)}
        for row in rows[: spec.limit]
    ]
    next_cursor = encode_cursor(version_id, h, offset + spec.limit) if has_more else None
    return QueryPage(items=items, next_cursor=next_cursor, total=total)

"""LLM labeling input as a DuckDB SQL query (user decision 2026-11-22, overrides labeling.md §5.10's fixed row).

The query runs read-only in a private in-memory DuckDB connection over the pinned version's tables, each registered as
a view under its table name (`__row_id` included). Its result has one row per item to label: a `__row_id` of the
layer's table (unique, every id present in that table) plus the columns the prompt template uses (`{income}`,
`{n_late_payments}`). The query and template are saved as a prompt version; a job re-runs exactly that query on the pinned
version, ordered by `__row_id`, so the same query and version give the same input.

Safety: one SELECT / WITH statement (the F-X4b guard `sqlsource.core.check_query`: no write / ATTACH / COPY / INSTALL /
SET / PRAGMA words, no read_* / glob / file or settings functions, no string-literal tables); the tables it names must be
registered views; a timeout (interrupt) and a row cap.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any

from fastapi import HTTPException

from . import lbb_common as C

TIMEOUT_S = 20.0
PREVIEW_CAP = 200
JOB_CAP = 100_000
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def query_sha(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def template_names(template: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER.findall(template or "")))


def version_tables(version_id: str) -> list[str]:
    with C.db().connect() as con:
        r = C.rows(con, "SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY table_name", (version_id,))
        if r:
            return [x["table_name"] for x in r]
        d = C.one(con, "SELECT primary_table FROM datasets WHERE id = ?", (version_id,))
    if not d:
        raise HTTPException(404, f"No dataset version '{version_id}'.")
    return [d["primary_table"] or "data"]


def _guard(sql: str) -> str:
    from ..sqlsource.core import SqlError, check_query

    try:
        return check_query(sql)
    except SqlError as e:
        raise HTTPException(400, {"code": e.code, "message": str(e)}) from None


def run(version_id: str, sql: str, *, limit: int = PREVIEW_CAP, timeout_s: float = TIMEOUT_S) -> dict[str, Any]:
    """{columns, rows, n, truncated, tables}. 400 with DuckDB's message for bad SQL; 408 on timeout."""
    import duckdb

    from ..lake import pages as PG

    q = _guard(sql)
    names = version_tables(version_id)
    con = duckdb.connect(":memory:")
    try:
        for t in names:
            src = PG.source(version_id, t)
            if src.arrow is not None:
                con.register(f"__arrow_{t}", src.arrow)
                rel = f"__arrow_{t}"
            else:
                rel = src.rel
            con.execute(f"CREATE VIEW {PG.q(t)} AS SELECT * FROM {rel}")
        try:
            pc = duckdb.connect(":memory:")  # parse only: an empty connection (views over read_parquet confuse the parser)
            try:
                used = set(pc.get_table_names(q))
            finally:
                pc.close()
        except duckdb.Error as e:
            raise HTTPException(400, {"code": "sql_error", "message": str(e).split("\n")[0][:500]}) from None
        bad = sorted(u for u in used if u.split(".")[-1] not in names)
        if bad:
            raise HTTPException(400, {"code": "sql_table", "message": f"Only this version's tables can be queried: {', '.join(names)}; "
                                      f"not {', '.join(bad)}.", "tables": names})
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            cols = [d[0] for d in con.execute(f"SELECT * FROM ({q}) AS _q LIMIT 0").description]
            order = ' ORDER BY "__row_id"' if "__row_id" in cols else ""
            df = con.execute(f"SELECT * FROM ({q}) AS _q{order} LIMIT {int(limit) + 1}").df()
        except duckdb.InterruptException:
            raise HTTPException(408, {"code": "sql_timeout", "message": f"The query ran over {timeout_s:.0f} s."}) from None
        except duckdb.Error as e:
            raise HTTPException(400, {"code": "sql_error", "message": str(e).split("\n")[0][:500]}) from None
        finally:
            timer.cancel()
    finally:
        con.close()
    truncated = len(df) > limit
    df = df.head(limit)
    rows = [{k: (None if C._isna(v) else (v.item() if hasattr(v, "item") else v)) for k, v in r.items()} for r in df.to_dict("records")]
    return {"columns": cols, "rows": rows, "n": len(rows), "truncated": truncated, "tables": names, "query_sha256": query_sha(q)}


def items(version_id: str, table: str, sql: str, template: str, *, limit: int) -> dict[str, Any]:
    """Run the query for labelling: validates `__row_id` (present, unique, every id in `table`) and the template names."""
    res = run(version_id, sql, limit=limit)
    if "__row_id" not in res["columns"]:
        raise HTTPException(400, {"code": "no_row_id", "message": f"The query must return a __row_id column of table '{table}'.",
                                  "columns": res["columns"]})
    unknown = [n for n in template_names(template) if n not in res["columns"]]
    if unknown:
        raise HTTPException(400, {"code": "template_unknown_columns", "message": f"The template names {', '.join(unknown)}, "
                                  "which the query does not return.", "unknown": unknown, "columns": res["columns"]})
    ids = [r["__row_id"] for r in res["rows"]]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        raise HTTPException(400, {"code": "row_id_not_unique", "message": "__row_id must be unique and not null (one row per item)."})
    have = {int(r["__row_id"]) for r in C.read_rows(version_id, table, [int(i) for i in ids], [])} if ids else set()
    missing = [int(i) for i in ids if int(i) not in have]
    if missing:
        raise HTTPException(400, {"code": "row_id_not_in_table", "message": f"{len(missing)} __row_id value(s) are not rows of "
                                  f"'{table}' in this version: {missing[:5]}.", "row_ids": missing[:50]})
    return res


def check_saved(version_id: str, table: str, sql: str, template: str) -> dict[str, Any]:
    """At save: the query runs (LIMIT 0 + a small sample) and every template name is one of its columns."""
    return items(version_id, table, sql, template, limit=20)


def render(template: str, row: dict[str, Any]) -> str:
    """Placeholders become JSON-quoted values: cell text stays data (delimited, escaped), never instructions."""
    import json

    return PLACEHOLDER.sub(lambda m: json.dumps(row.get(m.group(1)), ensure_ascii=False, default=str), template)

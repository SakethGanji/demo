"""Quality rule engine — compiles each rule to a single DuckDB query.

Rules target sheets by ``sheet_key`` and columns by *normalized* name, both of
which are stable across versions; the engine maps them to the physical parquet
column names recorded in the sheet's schema. Every evaluation returns a result
snapshot dict ready for ``validation_rule_results``.
"""

from __future__ import annotations

from typing import Any

import duckdb

from app.features.data_accelerator.services.sampling import _persist_table
from app.infra.db.storage import ArtifactLayout
from app.shared.data_io import _connect_s3
from app.shared.datasets import sheet_data_path
from app.shared.utils.sql import quote_ident

SAMPLE_LIMIT = 5


def _conn_for(path: str) -> duckdb.DuckDBPyConnection:
    return _connect_s3() if path.startswith("s3://") else duckdb.connect()


def _read_expr(path: str) -> str:
    return f"read_parquet('{path.replace(chr(39), chr(39) * 2)}')"


def _find_sheet(
    sheets: list[dict], selector: str | None, logical_sheet_id: str | None = None,
) -> dict | None:
    """Resolve a rule's target sheet — logical identity first, then name/key.

    The logical id survives confirmed renames, so a rule keeps following its
    sheet even when the selector text has gone stale mid-transaction.
    """
    if logical_sheet_id:
        for r in sheets:
            if r.get("logical_sheet_id") == logical_sheet_id:
                return r
    if not selector:
        return None
    for r in sheets:
        if r["sheet_key"] == selector or r["sheet_name"] == selector:
            return r
    return None


def _physical_column(sheet: dict, selector: str | None) -> str | None:
    """Map a normalized (or physical) column selector to the parquet name."""
    if not selector:
        return None
    schema = sheet.get("schema_json") or []
    for c in schema:
        if c["normalized_name"] == selector:
            return c["name"]
    for c in schema:
        if c["name"] == selector:
            return c["name"]
    return None


def _result(rule: dict, status: str, *, failure_count: int | None = None,
            message: str | None = None, samples: str | None = None) -> dict[str, Any]:
    return {
        "rule_id": rule["id"],
        "rule_name": rule["name"],
        "rule_type": rule["rule_type"],
        "scope_type": rule["scope_type"],
        "sheet_selector": rule.get("sheet_selector"),
        "column_selector": rule.get("column_selector"),
        "severity": rule["severity"],
        "status": status,
        "failure_count": failure_count,
        "message": message,
        "failure_sample_file": samples,
    }


def _persist_failures(conn: duckdb.DuckDBPyConnection, sql: str,
                      params: list, layout: ArtifactLayout) -> str | None:
    """Write the failing rows to parquet in the storage backend.

    Failing rows are dataset content, so they never go into Postgres — the
    result row keeps a count and a pointer. Returns the sample filename, or
    None if the query produced nothing.
    """
    conn.execute(f"CREATE OR REPLACE TABLE _rule_failures AS {sql}", params)
    if conn.execute("SELECT COUNT(*) FROM _rule_failures").fetchone()[0] == 0:
        return None
    return _persist_table(conn, "_rule_failures", "validation_failures", layout)


def evaluate_rule(rule: dict, ver: dict, sheets: list[dict],
                  layout: ArtifactLayout | None = None) -> dict[str, Any]:
    """Evaluate one rule against a version's sheets. Never raises.

    *layout* must be the same one the caller will register the failure files
    under — the storage key is not derivable from the filename, so a mismatch
    would leave the parquet unreachable. It defaults to the shared/ad-hoc
    prefix for callers that never register (unit tests).
    """
    try:
        return _evaluate(rule, ver, sheets,
                         layout or ArtifactLayout("validation_failures"))
    except Exception as e:  # a broken rule must not sink the whole run
        return _result(rule, "error", message=f"{type(e).__name__}: {e}")


def _evaluate(rule: dict, ver: dict, sheets: list[dict],
              layout: ArtifactLayout) -> dict[str, Any]:
    rtype = rule["rule_type"]
    params = rule.get("parameters") or {}

    if rtype == "sheet_exists":
        target = _find_sheet(sheets, rule.get("sheet_selector"), rule.get("logical_sheet_id"))
        if target and target.get("status", "ready") == "ready":
            return _result(rule, "passed")
        return _result(rule, "failed", failure_count=1,
                       message=f"Required sheet '{rule.get('sheet_selector')}' is missing")

    sheet = _find_sheet(sheets, rule.get("sheet_selector"), rule.get("logical_sheet_id"))
    if not sheet:
        return _result(rule, "error",
                       message=f"Sheet '{rule.get('sheet_selector')}' not found in this version")
    path = sheet_data_path(ver, sheet)
    expr = _read_expr(path)

    if rtype == "row_count_min":
        minimum = int(params.get("min", 1))
        conn = _conn_for(path)
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {expr}").fetchone()[0]
        finally:
            conn.close()
        if count >= minimum:
            return _result(rule, "passed")
        return _result(rule, "failed", failure_count=minimum - count,
                       message=f"Sheet has {count} rows, minimum is {minimum}")

    # Column-scoped rules need the physical column name.
    col_name = _physical_column(sheet, rule.get("column_selector"))
    if not col_name:
        return _result(rule, "error",
                       message=f"Column '{rule.get('column_selector')}' not found "
                               f"in sheet '{sheet['sheet_name']}'")
    col = quote_ident(col_name)

    if rtype == "foreign_key":
        ref_sheet = _find_sheet(sheets, params.get("ref_sheet"))
        if not ref_sheet:
            return _result(rule, "error",
                           message=f"Referenced sheet '{params.get('ref_sheet')}' not found")
        ref_col_name = _physical_column(ref_sheet, params.get("ref_column"))
        if not ref_col_name:
            return _result(rule, "error",
                           message=f"Referenced column '{params.get('ref_column')}' not found "
                                   f"in sheet '{ref_sheet['sheet_name']}'")
        ref_path = sheet_data_path(ver, ref_sheet)
        ref_expr = _read_expr(ref_path)
        ref_col = quote_ident(ref_col_name)
        where = (f"{col} IS NOT NULL AND {col} NOT IN "
                 f"(SELECT {ref_col} FROM {ref_expr} WHERE {ref_col} IS NOT NULL)")
        conn = _conn_for(path)
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {expr} WHERE {where}").fetchone()[0]
            samples = _persist_failures(
                conn, f"SELECT DISTINCT {col} AS orphan_value FROM {expr} "
                      f"WHERE {where} LIMIT {SAMPLE_LIMIT}", [],
                layout) if n else None
        finally:
            conn.close()
        if n == 0:
            return _result(rule, "passed")
        return _result(rule, "failed", failure_count=n, samples=samples,
                       message=f"{n} value(s) missing from "
                               f"{params.get('ref_sheet')}.{params.get('ref_column')}")

    # Single-column predicates: (violation WHERE clause, bind params, message)
    if rtype == "not_null":
        where, binds, what = f"{col} IS NULL", [], "NULL value(s)"
    elif rtype == "unique":
        where = (f"{col} IN (SELECT {col} FROM {expr} "
                 f"GROUP BY {col} HAVING COUNT(*) > 1)")
        binds, what = [], "duplicated value(s)"
    elif rtype == "accepted_values":
        values = params.get("values") or []
        placeholders = ", ".join("?" for _ in values)
        where = f"{col} IS NOT NULL AND {col} NOT IN ({placeholders})"
        binds, what = list(values), "unaccepted value(s)"
    elif rtype == "range":
        clauses, binds = [], []
        if params.get("min") is not None:
            clauses.append(f"{col} < ?")
            binds.append(params["min"])
        if params.get("max") is not None:
            clauses.append(f"{col} > ?")
            binds.append(params["max"])
        if not clauses:
            return _result(rule, "error", message="range rule needs min and/or max")
        where = f"{col} IS NOT NULL AND ({' OR '.join(clauses)})"
        what = "out-of-range value(s)"
    elif rtype == "regex_match":
        pattern = params.get("pattern")
        if not pattern:
            return _result(rule, "error", message="regex_match rule needs a pattern")
        where = f"{col} IS NOT NULL AND NOT regexp_matches(CAST({col} AS VARCHAR), ?)"
        binds, what = [pattern], "non-matching value(s)"
    else:
        return _result(rule, "error", message=f"Unknown rule type: {rtype}")

    conn = _conn_for(path)
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {expr} WHERE {where}", binds).fetchone()[0]
        samples = None
        if n:
            samples = _persist_failures(
                conn, f"SELECT * FROM {expr} WHERE {where} LIMIT {SAMPLE_LIMIT}",
                binds, layout)
    finally:
        conn.close()
    if n == 0:
        return _result(rule, "passed")
    return _result(rule, "failed", failure_count=n, samples=samples,
                   message=f"{n} {what} in {sheet['sheet_name']}.{rule.get('column_selector')}")

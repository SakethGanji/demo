"""Sandboxed DuckDB connections for user-supplied SQL.

Safety is engine-enforced, never regex/blocklist: string-matching SQL is
evadable, and DuckDB's replacement scans will read files from bare string
literals in FROM. The sandbox therefore:

1. MATERIALIZES every sheet as a table while file access is still enabled
   (``enable_external_access=false`` kills the lazy reads backing views —
   including local files — so "register views then lock down" cannot work);
2. disables external access and locks the configuration so the query can't
   flip it back;
3. gates statements with DuckDB's own parser (exactly one, type SELECT);
4. bounds execution with a watchdog interrupt (DuckDB has no statement
   timeout) and a row cap applied by wrapping the query.

DuckDB error text is sanitized (storage paths/keys stripped) before it reaches
a problem+json response.
"""

from __future__ import annotations

import re
import threading

import duckdb
import pandas as pd

from app.api.errors import ProblemException
from app.shared.utils.sql import quote_ident

SQL_TIMEOUT_S = 30.0
SQL_ROW_CAP = 10_000

# Anything that looks like a filesystem path, URI, or storage key.
_PATH_RE = re.compile(r"(s3://\S+|file:/\S+|(?:/[\w.\-]+){2,})")


def _sanitize_error(msg: str) -> str:
    return _PATH_RE.sub("<path>", msg).strip()


def open_sandboxed(sheets: dict[str, str]) -> duckdb.DuckDBPyConnection:
    """A locked-down in-memory connection holding *sheets* as tables.

    *sheets* maps table name (sheet_key) -> parquet path (local or s3://).
    Materialization happens first, then external access is disabled and the
    configuration locked — after that the connection can only read the tables
    it holds. Caller owns closing the connection.
    """
    # Import here: data_io imports pandas at module load; keep duck.py light.
    from app.shared.data_io import _connect_s3

    needs_s3 = any(p.startswith("s3://") for p in sheets.values())
    conn = _connect_s3() if needs_s3 else duckdb.connect()
    try:
        for name, path in sheets.items():
            escaped = str(path).replace("'", "''")
            conn.execute(
                f"CREATE TABLE {quote_ident(name)} AS "
                f"SELECT * FROM read_parquet('{escaped}')")
        conn.execute("SET enable_external_access = false")
        conn.execute("SET lock_configuration = true")
    except Exception:
        conn.close()
        raise
    return conn


def ensure_single_select(sql: str) -> None:
    """Reject anything but exactly one SELECT statement, via DuckDB's parser."""
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as e:
        raise ProblemException(
            400, f"SQL could not be parsed: {_sanitize_error(str(e))}",
            code="invalid-sql") from e
    if len(statements) != 1:
        raise ProblemException(
            400, f"Exactly one statement is allowed, got {len(statements)}",
            code="invalid-sql")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise ProblemException(
            400, f"Only SELECT statements are allowed, got {statements[0].type.name}",
            code="select-only")


def run_sandboxed(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    timeout_s: float = SQL_TIMEOUT_S,
    row_cap: int = SQL_ROW_CAP,
) -> tuple[pd.DataFrame, bool]:
    """Execute one gated SELECT on a sandboxed connection.

    Returns ``(result_frame, truncated)`` with at most *row_cap* rows — a
    DataFrame so callers can both render rows and persist a typed parquet
    (the locked connection itself can never write files again).
    Raises problem+json 400s: ``invalid-sql`` / ``select-only`` (gate),
    ``sql-timeout`` (watchdog fired), ``sql-error`` (sanitized engine error).
    """
    ensure_single_select(sql)
    wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {row_cap + 1}"
    watchdog = threading.Timer(timeout_s, conn.interrupt)
    watchdog.start()
    try:
        df = conn.execute(wrapped).df()
    except duckdb.InterruptException as e:
        raise ProblemException(
            400, f"Query exceeded the {timeout_s:g}s time limit",
            code="sql-timeout") from e
    except duckdb.Error as e:
        raise ProblemException(
            400, f"Query failed: {_sanitize_error(str(e))}",
            code="sql-error") from e
    finally:
        watchdog.cancel()
    truncated = len(df) > row_cap
    return df.iloc[:row_cap], truncated

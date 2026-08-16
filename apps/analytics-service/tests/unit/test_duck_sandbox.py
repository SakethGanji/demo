"""Unit tests for the sandboxed DuckDB helper (§6b) — no Postgres, tmp parquet.

The safety property under test: after open_sandboxed returns, the connection
can only see the materialized sheet tables — every escape route (file reads,
replacement scans, settings changes, non-SELECT statements) is closed by the
engine, not by string matching.
"""

from __future__ import annotations

import duckdb
import pytest

from app.api.errors import ProblemException
from app.shared.duck import ensure_single_select, open_sandboxed, run_sandboxed


@pytest.fixture
def sheets(tmp_path):
    """Two small parquet 'sheets' (customers/orders) on local disk."""
    conn = duckdb.connect()
    conn.execute("CREATE TABLE c AS SELECT * FROM (VALUES (1, 'gold'), (2, 'silver')) t(customer_id, tier)")
    conn.execute("CREATE TABLE o AS SELECT * FROM (VALUES (10, 1, 99.5), (11, 2, 15.0), (12, 1, 60.0)) t(order_id, customer_id, total)")
    cpath = tmp_path / "customers.parquet"
    opath = tmp_path / "orders.parquet"
    conn.execute(f"COPY c TO '{cpath}' (FORMAT PARQUET)")
    conn.execute(f"COPY o TO '{opath}' (FORMAT PARQUET)")
    conn.close()
    return {"customers": str(cpath), "orders": str(opath)}


@pytest.fixture
def sandbox(sheets):
    conn = open_sandboxed(sheets)
    yield conn
    conn.close()


def _code(exc_info) -> str:
    return exc_info.value.code


def test_select_and_cross_sheet_join(sandbox):
    df, truncated = run_sandboxed(
        sandbox,
        "SELECT c.tier, SUM(o.total) AS spend FROM orders o "
        "JOIN customers c USING (customer_id) GROUP BY c.tier ORDER BY spend DESC")
    assert not truncated
    assert df.values.tolist() == [["gold", 159.5], ["silver", 15.0]]


def test_row_cap_truncates(sandbox):
    df, truncated = run_sandboxed(sandbox, "SELECT * FROM orders", row_cap=2)
    assert truncated and len(df) == 2


def test_cte_is_a_select(sandbox):
    df, _ = run_sandboxed(
        sandbox, "WITH big AS (SELECT * FROM orders WHERE total > 50) "
                 "SELECT COUNT(*) AS n FROM big")
    assert df["n"].tolist() == [2]


@pytest.mark.parametrize("sql", [
    "INSERT INTO orders VALUES (13, 2, 1.0)",
    "CREATE TABLE x AS SELECT 1",
    "DROP TABLE orders",
    "COPY orders TO '/tmp/exfil.parquet' (FORMAT PARQUET)",
    "SET enable_external_access = true",
    "PRAGMA memory_limit='1GB'",  # config pragmas parse as SET statements
    "ATTACH '/tmp/other.db' AS other",
])
def test_non_select_statements_rejected(sandbox, sql):
    with pytest.raises(ProblemException) as e:
        run_sandboxed(sandbox, sql)
    assert _code(e) == "select-only"


def test_multi_statement_rejected(sandbox):
    with pytest.raises(ProblemException) as e:
        run_sandboxed(sandbox, "SELECT 1; SELECT 2")
    assert _code(e) == "invalid-sql"


def test_parse_error_rejected(sandbox):
    with pytest.raises(ProblemException) as e:
        run_sandboxed(sandbox, "SELEC wat FRUM")
    assert _code(e) == "invalid-sql"


@pytest.mark.parametrize("sql", [
    # Replacement scan from a bare string literal — DuckDB would read the file.
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('s3://bucket/secret.parquet')",
])
def test_file_reads_blocked_by_engine(sandbox, sql, sheets):
    with pytest.raises(ProblemException) as e:
        run_sandboxed(sandbox, sql)
    assert _code(e) == "sql-error"
    # Sanitized: no path from the attempt (or anywhere) leaks back.
    assert "/etc/passwd" not in e.value.detail
    assert "s3://" not in e.value.detail
    for p in sheets.values():
        assert p not in e.value.detail


def test_lock_configuration_survives_function_tricks(sandbox):
    # current_setting is readable, but the lock keeps the value pinned false.
    df, _ = run_sandboxed(
        sandbox, "SELECT current_setting('enable_external_access') AS v")
    assert df["v"].tolist() == [False]


def test_timeout_interrupts(sandbox):
    with pytest.raises(ProblemException) as e:
        run_sandboxed(
            sandbox,
            "SELECT COUNT(*) FROM range(100000000) a, range(1000) b",
            timeout_s=0.1)
    assert _code(e) == "sql-timeout"


def test_ensure_single_select_accepts_plain_select():
    ensure_single_select("SELECT 42")  # no raise


def test_sheets_with_spaces_in_key(tmp_path):
    conn = duckdb.connect()
    p = tmp_path / "one.parquet"
    conn.execute(f"COPY (SELECT 1 AS x) TO '{p}' (FORMAT PARQUET)")
    conn.close()
    sandbox = open_sandboxed({"odd sheet": str(p)})
    try:
        df, _ = run_sandboxed(sandbox, 'SELECT x FROM "odd sheet"')
        assert df["x"].tolist() == [1]
    finally:
        sandbox.close()

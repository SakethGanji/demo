"""Unit tests — quality rule engine against tiny local parquet files (no DB).

Sheet rows carry ``storage_key``s resolved by a LocalStorageBackend rooted at
``tmp_path``, so cross-sheet rules (foreign_key) read the right files without
any Postgres or app server.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.quality.engine import evaluate_rule
from app.infra.config import settings
from app.infra.db.storage import LocalStorageBackend, init_storage


@pytest.fixture()
def workbook(tmp_path):
    """Two parquet 'sheets' (customers / orders) with seeded violations."""
    init_storage(LocalStorageBackend(base_dir=tmp_path))
    cust = tmp_path / "customers.parquet"
    orders = tmp_path / "orders.parquet"
    conn = duckdb.connect()
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, 'gold'), (2, 'silver'), (2, 'copper'), (NULL, 'bronze'), (3, 'PLATINUM')
        ) AS t(customer_id, tier)) TO '{cust}' (FORMAT PARQUET)
    """)
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (10, 1, 99.5), (11, 2, -4.0), (12, 999, 5.0)
        ) AS t(order_id, customer_id, total)) TO '{orders}' (FORMAT PARQUET)
    """)
    conn.close()

    def schema(cols):
        return [{"name": c, "normalized_name": c, "dtype": "x", "nullable": True,
                 "position": i} for i, c in enumerate(cols)]

    sheets = [
        {"sheet_key": "customers", "sheet_name": "Customers", "status": "ready",
         "storage_key": "customers.parquet",
         "schema_json": schema(["customer_id", "tier"])},
        {"sheet_key": "orders", "sheet_name": "Orders", "status": "ready",
         "storage_key": "orders.parquet",
         "schema_json": schema(["order_id", "customer_id", "total"])},
    ]
    ver = {"path": str(cust)}  # canonical fallback; storage_key wins per sheet
    yield ver, sheets
    init_storage(LocalStorageBackend(base_dir=settings.storage_dir))


def _read_failures(result: dict) -> list[dict]:
    """Read a rule's persisted failing rows back out of the storage backend."""
    import duckdb

    from app.infra.db.storage import ArtifactLayout, get_storage

    # evaluate_rule() with no layout writes to the shared/ad-hoc prefix.
    key = ArtifactLayout("validation_failures").key(result["failure_sample_file"])
    path = get_storage().resolve(key)
    escaped = str(path).replace("'", "''")
    cur = duckdb.connect().execute(f"SELECT * FROM read_parquet('{escaped}')")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _rule(rtype, *, sheet=None, column=None, params=None, name="r"):
    return {"id": "00000000-0000-0000-0000-00000000000r", "name": name,
            "rule_type": rtype, "scope_type": "sheet", "sheet_selector": sheet,
            "column_selector": column, "parameters": params or {},
            "severity": "error"}


def test_sheet_exists_pass_and_fail(workbook):
    ver, sheets = workbook
    assert evaluate_rule(_rule("sheet_exists", sheet="customers"),
                         ver, sheets)["status"] == "passed"
    r = evaluate_rule(_rule("sheet_exists", sheet="nope"), ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1


def test_not_null_detects_seeded_null(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("not_null", sheet="customers", column="customer_id"),
                      ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1
    # Failing rows are dataset content: they go to the object store, and the
    # result carries only a pointer. Postgres never sees them.
    assert r["failure_sample_file"]
    assert "sample_failures" not in r
    assert len(_read_failures(r)) == 1


def test_unique_detects_duplicate_id(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("unique", sheet="customers", column="customer_id"),
                      ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 2  # both dup rows


def test_accepted_values_flags_outsiders_but_not_nulls(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("accepted_values", sheet="customers", column="tier",
                            params={"values": ["gold", "silver", "bronze", "copper"]}),
                      ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1  # only PLATINUM


def test_row_count_min_boundary(workbook):
    ver, sheets = workbook
    ok = evaluate_rule(_rule("row_count_min", sheet="orders", params={"min": 3}),
                       ver, sheets)
    assert ok["status"] == "passed"
    bad = evaluate_rule(_rule("row_count_min", sheet="orders", params={"min": 5}),
                        ver, sheets)
    assert bad["status"] == "failed" and bad["failure_count"] == 2  # shortfall


def test_range_min_and_max(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("range", sheet="orders", column="total",
                            params={"min": 0}), ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1  # -4.0
    r = evaluate_rule(_rule("range", sheet="orders", column="total",
                            params={"min": 0, "max": 50}), ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 2  # -4.0 and 99.5
    r = evaluate_rule(_rule("range", sheet="orders", column="total", params={}),
                      ver, sheets)
    assert r["status"] == "error"  # needs min and/or max


def test_foreign_key_detects_orphan(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("foreign_key", sheet="orders", column="customer_id",
                            params={"ref_sheet": "customers",
                                    "ref_column": "customer_id"}),
                      ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1
    assert _read_failures(r)[0]["orphan_value"] == 999


def test_regex_match(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("regex_match", sheet="customers", column="tier",
                            params={"pattern": "^[a-z]+$"}),
                      ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1  # PLATINUM


def test_unknown_column_and_rule_type_are_errors_not_crashes(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("not_null", sheet="customers", column="nope"),
                      ver, sheets)
    assert r["status"] == "error" and "not found" in r["message"]
    r = evaluate_rule(_rule("no_such_rule", sheet="customers", column="tier"),
                      ver, sheets)
    assert r["status"] == "error"

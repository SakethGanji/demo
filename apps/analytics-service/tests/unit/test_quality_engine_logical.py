"""Unit tests — evaluate_rule follows logical_sheet_id through stale selectors.

Mirrors test_quality_engine's local-parquet setup, but the sheet rows carry
``logical_sheet_id``s and the rules' ``sheet_selector`` text is deliberately
stale (a pre-rename name matching nothing). The logical id must still land the
rule on the right parquet.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.quality.engine import evaluate_rule
from app.infra.config import settings
from app.infra.db.storage import LocalStorageBackend, init_storage


@pytest.fixture()
def workbook(tmp_path):
    """One renamed parquet 'sheet' whose selector text has gone stale."""
    init_storage(LocalStorageBackend(base_dir=tmp_path))
    cust = tmp_path / "customers.parquet"
    conn = duckdb.connect()
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, 'gold'), (NULL, 'silver'), (3, 'bronze')
        ) AS t(customer_id, tier)) TO '{cust}' (FORMAT PARQUET)
    """)
    conn.close()

    schema = [{"name": c, "normalized_name": c, "dtype": "x", "nullable": True,
               "position": i} for i, c in enumerate(["customer_id", "tier"])]
    # Post-rename row: neither sheet_key nor sheet_name matches the rules'
    # stale 'customers' selector — only the logical id links them.
    sheets = [
        {"sheet_key": "clients_2026", "sheet_name": "Clients 2026",
         "logical_sheet_id": "L-cust", "status": "ready",
         "storage_key": "customers.parquet", "schema_json": schema},
    ]
    ver = {"path": str(cust)}
    yield ver, sheets
    init_storage(LocalStorageBackend(base_dir=settings.storage_dir))


def _rule(rtype, *, sheet=None, logical=None, column=None, params=None, name="r"):
    return {"id": "00000000-0000-0000-0000-00000000000r", "name": name,
            "rule_type": rtype, "scope_type": "sheet", "sheet_selector": sheet,
            "logical_sheet_id": logical, "column_selector": column,
            "parameters": params or {}, "severity": "error"}


def test_stale_selector_with_logical_id_still_evaluates(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("not_null", sheet="customers", logical="L-cust",
                            column="customer_id"), ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1  # the seeded NULL
    ok = evaluate_rule(_rule("not_null", sheet="customers", logical="L-cust",
                             column="tier"), ver, sheets)
    assert ok["status"] == "passed"  # right parquet, clean column


def test_same_stale_selector_without_logical_id_errors(workbook):
    ver, sheets = workbook
    r = evaluate_rule(_rule("not_null", sheet="customers", column="customer_id"),
                      ver, sheets)
    assert r["status"] == "error" and "not found" in r["message"]


def test_sheet_exists_follows_logical_id(workbook):
    ver, sheets = workbook
    ok = evaluate_rule(_rule("sheet_exists", sheet="customers", logical="L-cust"),
                       ver, sheets)
    assert ok["status"] == "passed"
    gone = evaluate_rule(_rule("sheet_exists", sheet="customers"), ver, sheets)
    assert gone["status"] == "failed" and gone["failure_count"] == 1


def test_unknown_logical_id_falls_back_to_selector(workbook):
    ver, sheets = workbook
    # Selector matches the CURRENT name, logical id matches nothing: fallback wins.
    r = evaluate_rule(_rule("not_null", sheet="clients_2026", logical="L-gone",
                            column="customer_id"), ver, sheets)
    assert r["status"] == "failed" and r["failure_count"] == 1

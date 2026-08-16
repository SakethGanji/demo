"""Unit tests — foreign_key rule → link-key resolution (ROADMAP §5, shared by §22).

`_fk_link_candidates` is the single place that reads direction out of a
foreign_key quality rule: the rule lives ON the child sheet and names the
parent in its parameters, but coordinated sampling may address the pair from
either end. Rule selectors are NORMALIZED column names, so the resolver also
maps them back to physical parquet names.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.features.data_accelerator.services.sampling import (
    _default_link_keys,
    _fk_link_candidates,
)

SHEETS = [
    {
        "sheet_key": "customers", "sheet_name": "Customers",
        "schema_json": [
            {"name": "Customer ID", "normalized_name": "customer_id"},
            {"name": "tier", "normalized_name": "tier"},
        ],
    },
    {
        "sheet_key": "orders", "sheet_name": "Orders",
        "schema_json": [
            {"name": "order_id", "normalized_name": "order_id"},
            {"name": "Customer ID", "normalized_name": "customer_id"},
        ],
    },
]


def fk_rule(name, sheet, column, ref_sheet, ref_column):
    return {"id": f"id-{name}", "name": name, "rule_type": "foreign_key",
            "sheet_selector": sheet, "column_selector": column,
            "parameters": {"ref_sheet": ref_sheet, "ref_column": ref_column}}


ORDERS_TO_CUSTOMERS = fk_rule("orders-fk", "Orders", "customer_id",
                              "Customers", "customer_id")


def test_no_rule_yields_no_candidates():
    assert _fk_link_candidates([], SHEETS, "Orders", "Customers") == []


def test_rule_found_when_the_related_sheet_is_the_fk_child():
    [candidate] = _fk_link_candidates([ORDERS_TO_CUSTOMERS], SHEETS,
                                      related="Orders", parent="Customers")
    assert candidate.rule_name == "orders-fk"
    # Normalized selectors resolve to the PHYSICAL parquet names.
    assert candidate.left_on == "Customer ID"    # on the parent
    assert candidate.right_on == "Customer ID"   # on the related sheet


def test_the_same_rule_is_found_from_the_other_direction():
    # Here the related sheet is the FK PARENT — the rule still links the pair.
    [candidate] = _fk_link_candidates([ORDERS_TO_CUSTOMERS], SHEETS,
                                      related="Customers", parent="Orders")
    assert candidate.rule_name == "orders-fk"
    assert candidate.left_on == "Customer ID"
    assert candidate.right_on == "Customer ID"


def test_sheet_keys_match_as_well_as_display_names():
    rule = fk_rule("keyed", "orders", "customer_id", "customers", "customer_id")
    assert len(_fk_link_candidates([rule], SHEETS, "Orders", "Customers")) == 1


def test_rules_about_other_sheets_are_ignored():
    other = fk_rule("elsewhere", "Invoices", "customer_id", "Customers", "customer_id")
    assert _fk_link_candidates([other], SHEETS, "Orders", "Customers") == []


def test_multiple_rules_all_come_back():
    second = fk_rule("second", "Orders", "order_id", "Customers", "tier")
    candidates = _fk_link_candidates([ORDERS_TO_CUSTOMERS, second], SHEETS,
                                     "Orders", "Customers")
    assert {c.rule_name for c in candidates} == {"orders-fk", "second"}


def test_an_unmapped_column_passes_through_unchanged():
    # A selector naming a column this version no longer has stays as-is rather
    # than silently resolving to something else.
    rule = fk_rule("stale", "Orders", "ghost", "Customers", "customer_id")
    [candidate] = _fk_link_candidates([rule], SHEETS, "Orders", "Customers")
    assert candidate.right_on == "ghost"


# --- the §5 caller ------------------------------------------------------------

def test_default_link_keys_returns_the_single_match():
    assert _default_link_keys([ORDERS_TO_CUSTOMERS], SHEETS, "Orders", "Customers") \
        == ("Customer ID", "Customer ID")


def test_default_link_keys_rejects_no_match():
    with pytest.raises(HTTPException) as e:
        _default_link_keys([], SHEETS, "Orders", "Customers")
    assert e.value.status_code == 400
    assert "No foreign_key quality rule" in e.value.detail


def test_default_link_keys_rejects_ambiguity_by_naming_the_rules():
    second = fk_rule("second", "Orders", "order_id", "Customers", "tier")
    with pytest.raises(HTTPException) as e:
        _default_link_keys([ORDERS_TO_CUSTOMERS, second], SHEETS, "Orders", "Customers")
    assert e.value.status_code == 400
    assert "orders-fk" in e.value.detail and "second" in e.value.detail

"""Unit tests — logical sheet identity helpers (no DB, no parquet).

Covers the three sheet-resolution paths that rename-safety leans on: the
quality engine's logical-id-first ``_find_sheet``, the shared name-first
``_find_sheet``, and coordinated sampling's FK-rule default link keys.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.features.quality.engine import _find_sheet as quality_find_sheet
from app.shared.datasets import _find_sheet as datasets_find_sheet
from app.features.data_accelerator.services.sampling import (
    _default_link_keys,
    _physical_key,
)


def _sheet(key, name, logical=None, schema=None):
    return {"sheet_key": key, "sheet_name": name, "logical_sheet_id": logical,
            "schema_json": schema}


SHEETS = [
    _sheet("customers", "Customers", logical="L-cust"),
    _sheet("orders", "Orders", logical="L-ord"),
]


# --- quality engine _find_sheet: logical id first ---------------------------

def test_logical_id_wins_over_stale_selector():
    # Selector points at a real but WRONG sheet; the logical id must win.
    row = quality_find_sheet(SHEETS, "orders", "L-cust")
    assert row["sheet_key"] == "customers"


def test_logical_id_matches_even_when_selector_matches_nothing():
    row = quality_find_sheet(SHEETS, "renamed_away", "L-ord")
    assert row["sheet_key"] == "orders"


def test_unknown_logical_id_falls_back_to_selector():
    row = quality_find_sheet(SHEETS, "orders", "L-gone")
    assert row["sheet_key"] == "orders"


def test_selector_matches_key_or_name():
    assert quality_find_sheet(SHEETS, "customers", None)["sheet_key"] == "customers"
    assert quality_find_sheet(SHEETS, "Customers", None)["sheet_key"] == "customers"


def test_no_selector_no_logical_id_is_none():
    assert quality_find_sheet(SHEETS, None, None) is None
    assert quality_find_sheet(SHEETS, "", None) is None


def test_no_match_is_none_not_error():
    assert quality_find_sheet(SHEETS, "nope", None) is None
    assert quality_find_sheet(SHEETS, "nope", "L-gone") is None
    assert quality_find_sheet([], "customers", "L-cust") is None


# --- shared datasets _find_sheet: name first, then key ----------------------

def test_datasets_find_sheet_name_beats_key():
    # One sheet's key collides with another sheet's name — name match wins.
    tricky = [
        _sheet("orders", "Q1", logical=None),
        _sheet("q1", "orders", logical=None),  # sheet literally NAMED "orders"
    ]
    assert datasets_find_sheet(tricky, "orders")["sheet_key"] == "q1"
    assert datasets_find_sheet(tricky, "Q1")["sheet_key"] == "orders"


def test_datasets_find_sheet_falls_back_to_key():
    assert datasets_find_sheet(SHEETS, "customers")["sheet_name"] == "Customers"


def test_datasets_find_sheet_no_match_is_none():
    assert datasets_find_sheet(SHEETS, "nope") is None


# --- sampling _physical_key -------------------------------------------------

CUST = _sheet("customers", "Customers", schema=[
    {"name": "Customer ID", "normalized_name": "customer_id"},
    {"name": "Tier", "normalized_name": "tier"},
])
ORDERS = _sheet("orders", "Orders", schema=[
    {"name": "Cust Ref", "normalized_name": "cust_ref"},
    {"name": "Total", "normalized_name": "total"},
])


def test_physical_key_maps_normalized_to_physical():
    assert _physical_key(CUST, "customer_id") == "Customer ID"


def test_physical_key_passes_through_unknown_and_none():
    assert _physical_key(CUST, "not_a_column") == "not_a_column"  # as-given
    assert _physical_key(None, "customer_id") == "customer_id"  # no sheet row
    assert _physical_key(CUST, None) is None
    assert _physical_key({"schema_json": None}, "x") == "x"  # schema missing


# --- sampling _default_link_keys --------------------------------------------

def _fk_rule(name, on_sheet, column, ref_sheet, ref_column):
    return {"name": name, "sheet_selector": on_sheet, "column_selector": column,
            "parameters": {"ref_sheet": ref_sheet, "ref_column": ref_column}}


ROWS = [CUST, ORDERS]
CHILD_RULE = _fk_rule("orders_fk", "orders", "cust_ref", "customers", "customer_id")


def test_child_direction_related_is_fk_child():
    # Rule ON orders referencing customers; related=orders, parent=customers.
    left, right = _default_link_keys([CHILD_RULE], ROWS, "orders", "customers")
    assert (left, right) == ("Customer ID", "Cust Ref")  # both mapped to physical


def test_parent_direction_related_is_fk_parent():
    # Same rule, roles flipped: related=customers, parent=orders.
    left, right = _default_link_keys([CHILD_RULE], ROWS, "customers", "orders")
    assert (left, right) == ("Cust Ref", "Customer ID")


def test_rule_selectors_match_by_sheet_name_too():
    rule = _fk_rule("by_name", "Orders", "cust_ref", "Customers", "customer_id")
    left, right = _default_link_keys([rule], ROWS, "orders", "customers")
    assert (left, right) == ("Customer ID", "Cust Ref")


def test_no_matching_rule_is_400():
    with pytest.raises(HTTPException) as exc:
        _default_link_keys([], ROWS, "orders", "customers")
    assert exc.value.status_code == 400
    assert "No foreign_key" in exc.value.detail


def test_two_matching_rules_is_400_multiple():
    dup = _fk_rule("orders_fk_2", "orders", "cust_ref", "customers", "customer_id")
    with pytest.raises(HTTPException) as exc:
        _default_link_keys([CHILD_RULE, dup], ROWS, "orders", "customers")
    assert exc.value.status_code == 400
    assert "Multiple" in exc.value.detail
    assert "orders_fk" in exc.value.detail  # names listed for the caller


def test_prefiltering_disabled_rules_is_the_callers_job():
    # The function uses whatever rules it's handed — a rule the caller failed
    # to filter out (e.g. disabled) still counts and creates ambiguity.
    unfiltered = {**CHILD_RULE, "name": "disabled_fk", "enabled": False}
    with pytest.raises(HTTPException) as exc:
        _default_link_keys([CHILD_RULE, unfiltered], ROWS, "orders", "customers")
    assert "Multiple" in exc.value.detail
    # And handed only the "disabled" rule, it happily uses it.
    left, right = _default_link_keys([unfiltered], ROWS, "orders", "customers")
    assert (left, right) == ("Customer ID", "Cust Ref")


def test_unknown_sheet_names_never_match():
    with pytest.raises(HTTPException) as exc:
        _default_link_keys([CHILD_RULE], ROWS, "orders", "no_such_sheet")
    assert "No foreign_key" in exc.value.detail
    with pytest.raises(HTTPException):
        _default_link_keys([CHILD_RULE], ROWS, "no_such_sheet", "customers")

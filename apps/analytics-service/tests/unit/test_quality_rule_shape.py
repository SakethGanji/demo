"""The rule-shape invariants, checked once, away from Postgres.

``check_rule_shape`` exists because two code paths have to agree about what a
usable rule looks like: POST validates the body, PATCH validates the *merged*
rule. When those checks lived only inside ``RuleCreate``, an edit could store a
shape create rejects, and the only symptom was a per-rule ``error`` on some
later validation run. These tests pin the shared predicate itself so neither
caller can drift from it.
"""

from __future__ import annotations

import pytest

from app.features.quality.schemas import RuleCreate, check_rule_shape


def test_check_rule_shape_rejects_accepted_values_with_no_values():
    """An accepted_values rule with no value list accepts everything.

    It cannot fail, so it silently reports a column as conforming to a contract
    nobody ever wrote down — worse than having no rule at all, because the
    dashboard shows a green check.
    """
    with pytest.raises(ValueError, match="accepted_values requires parameters.values"):
        check_rule_shape("accepted_values", "orders", "tier", {})
    with pytest.raises(ValueError):
        check_rule_shape("accepted_values", "orders", "tier", {"values": []})
    check_rule_shape("accepted_values", "orders", "tier", {"values": ["gold"]})


def test_check_rule_shape_rejects_foreign_key_without_both_reference_parameters():
    """A foreign_key rule needs somewhere to point; half a reference is not a rule."""
    for params in ({}, {"ref_sheet": "customers"}, {"ref_column": "customer_id"}):
        with pytest.raises(ValueError, match="foreign_key requires parameters"):
            check_rule_shape("foreign_key", "orders", "customer_id", params)
    check_rule_shape("foreign_key", "orders", "customer_id",
                     {"ref_sheet": "customers", "ref_column": "customer_id"})


def test_check_rule_shape_requires_a_sheet_selector_for_every_rule_type():
    """Every rule targets a sheet — a rule with no sheet has nothing to evaluate.

    Guards the PATCH path in particular, where ``{"sheet_selector": null}`` is a
    well-formed JSON body that would otherwise unpin a live rule.
    """
    for rule_type in ("sheet_exists", "row_count_min", "not_null"):
        with pytest.raises(ValueError, match="requires sheet_selector"):
            check_rule_shape(rule_type, None, "customer_id", {})


def test_check_rule_shape_requires_a_column_for_column_and_cross_sheet_scopes():
    """Column-scoped rules resolve a physical parquet column; None resolves to nothing."""
    for rule_type in ("not_null", "unique", "range", "regex_match"):
        with pytest.raises(ValueError, match="requires column_selector"):
            check_rule_shape(rule_type, "orders", None, {})
    with pytest.raises(ValueError, match="requires column_selector"):
        check_rule_shape("foreign_key", "orders", "",
                         {"ref_sheet": "customers", "ref_column": "customer_id"})
    # Dataset/sheet scoped rules legitimately have no column.
    check_rule_shape("sheet_exists", "orders", None, {})
    check_rule_shape("row_count_min", "orders", None, {"min": 1})


def test_rule_create_still_enforces_the_shared_shape_check():
    """RuleCreate must delegate, not keep a private copy that can drift from PATCH."""
    with pytest.raises(ValueError, match="accepted_values requires parameters.values"):
        RuleCreate(name="tiers", rule_type="accepted_values",
                   sheet_selector="orders", column_selector="tier")
    ok = RuleCreate(name="tiers", rule_type="accepted_values",
                    sheet_selector="orders", column_selector="tier",
                    parameters={"values": ["gold"]})
    assert ok.scope_type == "column"

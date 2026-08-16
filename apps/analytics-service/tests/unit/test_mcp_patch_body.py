"""Assembling a merge-PATCH body for the MCP documentation tool.

A tool parameter left unset is indistinguishable from one set to ``None``, so
"erase this field" cannot be expressed as a value — it gets its own ``clear``
parameter. ``_patch_body`` is the single place that turns (values, clear) into
the body the PATCH routes read, for all three documentation targets.

The dataset target is the one where "writable" and "clearable" differ: four
``datasets`` columns are NOT NULL, so an explicit null on them is a 422 rather
than a clear.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.mcp.tools.curate import (
    DATASET_CLEARABLE,
    DATASET_FIELDS,
    _patch_body,
)

SHEET_FIELDS = ("grain", "primary_key_columns", "description")


def _dataset(values=None, clear=None):
    return _patch_body(values or {}, clear, DATASET_FIELDS, noun="dataset",
                       clearable=DATASET_CLEARABLE)


def test_unset_parameters_are_omitted_and_cleared_ones_become_explicit_nulls():
    body = _dataset({"domain": "finance", "description": None}, ["source_system"])
    # `description` was not passed, so it is absent (keep) — not null (erase).
    assert body == {"domain": "finance", "source_system": None}


def test_clearable_defaults_to_the_whole_writable_set():
    """Sheet and column documentation has no required field; every one clears."""
    body = _patch_body({"grain": "one row per order"}, ["description"],
                       SHEET_FIELDS, noun="sheet")
    assert body == {"grain": "one row per order", "description": None}


@pytest.mark.parametrize("field", ["name", "classification", "deprecated"])
def test_a_required_dataset_field_cannot_be_cleared(field):
    """NOT NULL columns are refused here, not round-tripped to the 422."""
    with pytest.raises(ToolError) as e:
        _dataset({}, [field])
    message = str(e.value)
    assert f"{field} cannot be cleared" in message
    # The message has to be recoverable-from: it names the alternative and the
    # fields that DO clear.
    assert "Write a new value" in message
    for clearable in DATASET_CLEARABLE:
        assert clearable in message


def test_metadata_is_not_writable_here_so_it_reads_as_an_unknown_field():
    """``metadata`` is NOT NULL too, but this tool never writes it."""
    assert "metadata" not in DATASET_FIELDS
    with pytest.raises(ToolError, match="not a dataset field"):
        _dataset({}, ["metadata"])


def test_an_unknown_name_is_told_apart_from_a_required_one():
    with pytest.raises(ToolError) as e:
        _dataset({}, ["grain"])          # a SHEET field, on the dataset target
    assert "not a dataset field" in str(e.value)
    assert "cannot be cleared" not in str(e.value)


def test_writing_and_clearing_the_same_field_is_refused():
    with pytest.raises(ToolError, match="pick one"):
        _dataset({"domain": "finance"}, ["domain"])


def test_an_empty_request_is_refused_rather_than_sent_as_a_no_op():
    with pytest.raises(ToolError) as e:
        _dataset({}, [])
    assert "at least one dataset field" in str(e.value)
    # The prompt lists everything writable, including the un-clearable ones.
    for field in DATASET_FIELDS:
        assert field in str(e.value)


def test_false_is_a_value_not_an_omission():
    """`deprecated=False` un-deprecates; it must not be dropped as falsy."""
    assert _dataset({"deprecated": False}) == {"deprecated": False}


def test_clearable_is_a_subset_of_writable():
    assert set(DATASET_CLEARABLE) < set(DATASET_FIELDS)

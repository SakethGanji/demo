"""Binding a saved definition's stored params to its request model.

Definitions store free-form JSON, so a definition saved under a looser schema
can hold a value the current model rejects. That is a 400 naming the parameter,
not a 500 — see ``app.features.library.service.build_definition_request``.
"""

from __future__ import annotations

import pytest

from app.api.errors import ProblemException
from app.features.data_accelerator.schemas import (
    AggregateRequest,
    ProfileRequest,
    SampleRequest,
)
from app.features.library.service import build_definition_request

BASE = {"dataset_id": "11111111-1111-1111-1111-111111111111", "sheet": None}
AGG_PARAMS = {
    "group_by": ["region"],
    "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
}


def test_valid_params_bind_to_the_kind_s_request_model():
    req = build_definition_request(
        "aggregate", {**AGG_PARAMS, "sort_by": "total", "sort_order": "desc"}, BASE)
    assert isinstance(req, AggregateRequest)
    assert req.sort_order == "desc" and req.dataset_id == BASE["dataset_id"]

    assert isinstance(build_definition_request("profile", {}, BASE), ProfileRequest)
    assert isinstance(
        build_definition_request(
            "sample",
            {"target_total_volume": 5,
             "sampling_steps": [{"method": "random", "sample_size": 5}]},
            BASE),
        SampleRequest)


def test_legacy_uppercase_sort_order_is_a_400_naming_the_param():
    """The regression: `sort_order: "ASC"` was legal when the field was a bare
    `str` (and ran as *descending*). Tightening it to Literal["asc","desc"] made
    every such saved definition raise ValidationError, which the run path's
    blanket `except Exception` reported as a 500."""
    with pytest.raises(ProblemException) as e:
        build_definition_request(
            "aggregate", {**AGG_PARAMS, "sort_by": "total", "sort_order": "ASC"}, BASE)

    exc = e.value
    assert exc.status_code == 400
    assert exc.code == "invalid-definition"
    assert exc.extra["kind"] == "aggregate"
    # The caller is told which param, why, and what it currently holds.
    assert "sort_order" in exc.detail and "'asc' or 'desc'" in exc.detail
    assert exc.extra["errors"] == [
        {"param": "sort_order",
         "reason": "Input should be 'asc' or 'desc'",
         "value": "ASC"},
    ]


def test_definition_missing_group_by_is_the_same_400():
    """Pre-existing case, not caused by the Literal change: a definition saved
    without `group_by` also 500'd because a missing required field is the same
    ValidationError."""
    with pytest.raises(ProblemException) as e:
        build_definition_request(
            "aggregate", {"aggregations": AGG_PARAMS["aggregations"]}, BASE)

    assert e.value.status_code == 400 and e.value.code == "invalid-definition"
    assert [err["param"] for err in e.value.extra["errors"]] == ["group_by"]


def test_every_bad_param_is_reported_not_just_the_first():
    with pytest.raises(ProblemException) as e:
        build_definition_request(
            "aggregate",
            {"sort_by": "total", "sort_order": "ascending"}, BASE)

    params = {err["param"] for err in e.value.extra["errors"]}
    assert params == {"group_by", "aggregations", "sort_order"}


def test_a_problem_exception_from_a_nested_model_is_not_reworded():
    """`unknown-operator` is raised by the filter grammar as a ProblemException,
    not a ValidationError. It has a better message than a generic
    `invalid-definition` wrapper, so it must pass through untouched."""
    with pytest.raises(ProblemException) as e:
        build_definition_request(
            "aggregate",
            {**AGG_PARAMS,
             "filters": {"conditions": [
                 {"column": "amount", "op": "greater_than", "value": 1}]}},
            BASE)

    assert e.value.code == "unknown-operator"

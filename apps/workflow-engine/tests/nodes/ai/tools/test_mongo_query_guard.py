"""Tests for the MongoQueryTool validator (``_mongo_query_guard``).

These are plain ``assert`` tests. They run under pytest, and also stand alone
when invoked with ``python -m tests.nodes.ai.tools.test_mongo_query_guard``
or via the ``__main__`` block at the bottom of this file. We keep them
dependency-free so they're cheap to run during a refactor.
"""

from __future__ import annotations

import os
import sys
import traceback

# Make ``src`` importable when running this file directly from the
# workflow-engine root, since the project doesn't ship a pyproject.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.nodes.ai.tools._mongo_query_guard import (  # noqa: E402
    apply_default_projection,
    clamp_limit,
    validate_aggregate,
    validate_find,
)


EXP_ID = "exp_sentiment_v1"
FIELD = "experiment_id"


def _expect_raises(fn, *args, contains: str | None = None, **kwargs):
    """Assert that calling ``fn(*args, **kwargs)`` raises ValueError."""
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        if contains is not None:
            assert contains in str(e), f"expected {contains!r} in error, got {e!r}"
        return
    raise AssertionError(f"Expected ValueError from {fn.__name__}")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_find_simple_passes():
    validate_find({"experiment_id": EXP_ID}, None, None, FIELD, EXP_ID)


def test_find_with_extra_fields_passes():
    validate_find(
        {"experiment_id": EXP_ID, "improved": True},
        {"prompt_system": 0},
        {"created_at": -1},
        FIELD,
        EXP_ID,
    )


def test_find_with_and_branch_passes():
    validate_find(
        {"$and": [{"experiment_id": EXP_ID}, {"improved": True}]},
        None,
        None,
        FIELD,
        EXP_ID,
    )


def test_aggregate_with_leading_match_passes():
    validate_aggregate(
        [
            {"$match": {"experiment_id": EXP_ID}},
            {"$sort": {"metrics.overall.macro_f1": -1}},
            {"$limit": 5},
        ],
        FIELD,
        EXP_ID,
    )


def test_aggregate_with_facet_passes():
    validate_aggregate(
        [
            {"$match": {"experiment_id": EXP_ID}},
            {
                "$facet": {
                    "byPair": [{"$group": {"_id": "$change_criteria.direction", "n": {"$sum": 1}}}],
                    "best": [{"$sort": {"metrics.overall.macro_f1": -1}}, {"$limit": 1}],
                }
            },
        ],
        FIELD,
        EXP_ID,
    )


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------

def test_find_missing_mandatory_field_raises():
    _expect_raises(
        validate_find, {"improved": True}, None, None, FIELD, EXP_ID,
        contains=FIELD,
    )


def test_find_wrong_mandatory_value_raises():
    _expect_raises(
        validate_find, {"experiment_id": "wrong_exp"}, None, None, FIELD, EXP_ID,
        contains=FIELD,
    )


def test_find_with_where_raises():
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "$where": "this.x > 0"},
        None, None, FIELD, EXP_ID,
        contains="$where",
    )


def test_find_with_lookup_raises():
    # $lookup is meaningless inside a filter, but if the agent sneaks it in
    # as a key we still want it rejected by the recursive scanner.
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "$lookup": {"from": "x"}},
        None, None, FIELD, EXP_ID,
        contains="$lookup",
    )


def test_aggregate_with_lookup_stage_raises():
    _expect_raises(
        validate_aggregate,
        [
            {"$match": {"experiment_id": EXP_ID}},
            {"$lookup": {"from": "x", "localField": "a", "foreignField": "b", "as": "c"}},
        ],
        FIELD, EXP_ID,
        contains="$lookup",
    )


def test_find_or_with_unguarded_branch_raises():
    _expect_raises(
        validate_find,
        {"$or": [{"experiment_id": EXP_ID}, {"improved": True}]},
        None, None, FIELD, EXP_ID,
        contains=FIELD,
    )


def test_find_or_where_every_branch_guards_passes():
    validate_find(
        {"$or": [
            {"experiment_id": EXP_ID, "improved": True},
            {"experiment_id": EXP_ID, "improved": False},
        ]},
        None, None, FIELD, EXP_ID,
    )


def test_aggregate_without_leading_match_raises():
    _expect_raises(
        validate_aggregate,
        [{"$sort": {"created_at": -1}}, {"$limit": 5}],
        FIELD, EXP_ID,
        contains="$match",
    )


def test_aggregate_match_missing_field_raises():
    _expect_raises(
        validate_aggregate,
        [{"$match": {"improved": True}}, {"$limit": 5}],
        FIELD, EXP_ID,
        contains=FIELD,
    )


def test_aggregate_over_max_stages_raises():
    pipeline = [{"$match": {"experiment_id": EXP_ID}}] + [
        {"$limit": 100} for _ in range(8)
    ]
    assert len(pipeline) == 9
    _expect_raises(
        validate_aggregate, pipeline, FIELD, EXP_ID,
        contains="stages",
    )


def test_regex_non_string_raises():
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "prompt_hash": {"$regex": 123}},
        None, None, FIELD, EXP_ID,
        contains="$regex",
    )


def test_regex_string_passes():
    validate_find(
        {"experiment_id": EXP_ID, "prompt_hash": {"$regex": "^abc"}},
        None, None, FIELD, EXP_ID,
    )


def test_expr_with_js_function_string_raises():
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "$expr": "function(){ return true; }"},
        None, None, FIELD, EXP_ID,
        contains="JS",
    )


def test_expr_with_nested_function_op_raises():
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "$expr": {"$function": {"body": "x", "args": [], "lang": "js"}}},
        None, None, FIELD, EXP_ID,
        contains="$function",
    )


def test_disallowed_filter_op_raises():
    _expect_raises(
        validate_find,
        {"experiment_id": EXP_ID, "score": {"$mod": [2, 0]}},
        None, None, FIELD, EXP_ID,
        contains="$mod",
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def test_apply_default_projection_uses_strip_when_none():
    p = apply_default_projection(None, ["prompt_system", "failures_sample.utterance"])
    assert p == {"prompt_system": 0, "failures_sample.utterance": 0}


def test_apply_default_projection_passthrough_when_supplied():
    supplied = {"metrics.overall": 1, "_id": 1}
    assert apply_default_projection(supplied, ["prompt_system"]) is supplied


def test_apply_default_projection_empty_strip():
    assert apply_default_projection(None, []) == {}


def test_clamp_limit_none_returns_max():
    assert clamp_limit(None, 50) == 50


def test_clamp_limit_over_returns_max():
    assert clamp_limit(500, 50) == 50


def test_clamp_limit_under_returns_value():
    assert clamp_limit(10, 50) == 10


def test_clamp_limit_negative_or_zero_returns_max():
    assert clamp_limit(0, 50) == 50
    assert clamp_limit(-1, 50) == 50


def test_clamp_limit_bad_type_returns_max():
    assert clamp_limit("oops", 50) == 50  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _all_tests():
    g = globals()
    return [(name, fn) for name, fn in g.items() if name.startswith("test_") and callable(fn)]


def main() -> int:
    passed = 0
    failed: list[tuple[str, str]] = []
    for name, fn in _all_tests():
        try:
            fn()
        except Exception:
            failed.append((name, traceback.format_exc()))
        else:
            passed += 1
    total = passed + len(failed)
    print(f"{passed}/{total} passed")
    for name, tb in failed:
        print("-" * 60)
        print(f"FAIL: {name}")
        print(tb)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

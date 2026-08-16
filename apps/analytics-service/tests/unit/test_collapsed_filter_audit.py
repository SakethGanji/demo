"""The saved-view filter audit's tree walk (scripts/audit_collapsed_view_filters).

Read-only reporting: these tests pin what it flags and — just as important —
what it refuses to call corruption, since an empty ``FilterGroup`` is also a
legitimate saved state.
"""

from __future__ import annotations

from scripts.audit_collapsed_view_filters import (
    AMBIGUOUS,
    DELIBERATE,
    LIKELY_COLLAPSED,
    scan_query,
)


def _verdicts(query):
    return [(f["path"], f["verdict"]) for f in scan_query(query)]


def test_a_view_with_no_filters_is_not_reported():
    assert scan_query({"columns": None, "filters": None, "limit": 100}) == []


def test_a_healthy_filter_tree_is_not_reported():
    assert scan_query({"filters": {"logic": "and", "conditions": [
        {"column": "amount", "op": "gt", "value": 1, "case_sensitive": True},
        {"logic": "or", "conditions": [
            {"column": "region", "op": "eq", "value": "US", "case_sensitive": True}]},
    ]}}) == []


def test_the_collapse_shape_is_flagged_where_it_actually_lands():
    """A malformed condition used to be re-read as a FilterGroup, whose fields
    all have defaults — so it was stored as an empty ``and`` group *inside* the
    parent's conditions list, exactly here."""
    assert _verdicts({"filters": {"logic": "and", "conditions": [
        {"column": "amount", "op": "gt", "value": 1},
        {"logic": "and", "conditions": []},
    ]}}) == [("query.filters.conditions[1]", LIKELY_COLLAPSED)]


def test_nested_collapses_are_found_at_any_depth():
    assert _verdicts({"filters": {"logic": "or", "conditions": [
        {"logic": "and", "conditions": [
            {"column": "a", "op": "eq", "value": 1},
            {"logic": "and", "conditions": []},
        ]},
        {"logic": "and", "conditions": []},
    ]}}) == [
        ("query.filters.conditions[0].conditions[1]", LIKELY_COLLAPSED),
        ("query.filters.conditions[1]", LIKELY_COLLAPSED),
    ]


def test_an_empty_group_at_the_root_is_ambiguous_not_corruption():
    """Same shape whether a whole malformed condition was passed as `filters`
    or the view was simply saved with no filter. A human has to judge."""
    assert _verdicts({"filters": {"logic": "and", "conditions": []}}) == [
        ("query.filters", AMBIGUOUS)]


def test_a_non_and_logic_proves_the_empty_group_was_deliberate():
    """The one hard signal: a collapse always leaves the default `logic="and"`,
    because the malformed dict carried no `logic` key."""
    assert _verdicts({"filters": {"logic": "or", "conditions": []}}) == [
        ("query.filters", DELIBERATE)]
    assert _verdicts({"filters": {"logic": "and", "conditions": [
        {"column": "a", "op": "eq", "value": 1},
        {"logic": "or", "conditions": []},
    ]}}) == [("query.filters.conditions[1]", DELIBERATE)]


def test_the_walk_tolerates_shapes_the_current_model_would_reject():
    """It reads historical rows written by older code, so it must not raise."""
    assert _verdicts({"filters": {}}) == [("query.filters", AMBIGUOUS)]
    assert _verdicts({"filters": {"conditions": "nonsense"}}) == [
        ("query.filters", AMBIGUOUS)]
    assert scan_query({"filters": "nonsense"}) == []
    assert scan_query("not even a dict") == []

"""shapes.validate and the new keywords, the effects table, rename_node_in_tests and the lock-rule diff."""

from __future__ import annotations

import pytest

from src.engine import shapes as sh
from src.engine.effects import NODE_EFFECTS, effect_of
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.test_mode import locked_test_ids, rename_node_in_test_defaults, rename_node_in_tests
from src.schemas.workflow import TestCaseSchema, normalize_tests


# ── effects ──────────────────────────────────────────────────────────────────

def test_every_registered_node_type_has_an_effect():
    register_all_nodes()
    missing = [t for t in node_registry.list() if t not in NODE_EFFECTS]
    assert missing == []
    assert set(NODE_EFFECTS) <= set(node_registry.list())  # no stale entries either


def test_effect_table_matches_the_contract():
    expected = {
        "network": {"HttpRequest", "SendEmail", "Poll", "Sample", "Profile", "Aggregate"}, "database": {"Postgres", "MongoDB", "Neo4j", "DataTable"},
        "llm": {"LLMChat", "AIAgent"}, "subworkflow": {"ExecuteWorkflow"},
    }
    for effect, types in expected.items():
        assert {t for t, e in NODE_EFFECTS.items() if e == effect} == types
    for t in ("Code", "PythonStep", "Wait", "Respond", "Webhook", "Set", "If"):
        assert effect_of(t) == "none"
    assert effect_of("NoSuchType") == "none"


# ── shapes.validate ──────────────────────────────────────────────────────────

ORDER = {
    "type": "object", "x-source": "declared", "required": ["id", "amount"],
    "properties": {
        "id": {"type": "string", "minLength": 2, "maxLength": 5, "pattern": "^o-"},
        "amount": {"type": "number", "minimum": 0, "maximum": 1000},
        "status": {"type": "string", "enum": ["new", "paid"]},
        "note": {"type": "string", "nullable": True},
        "lines": {"type": "array", "items": {"type": "object", "properties": {"qty": {"type": "integer"}}, "required": ["qty"]}},
    },
}


def paths(violations):
    return sorted(v["path"] for v in violations)


def test_valid_value_has_no_violations_and_extra_properties_are_fine():
    assert sh.validate(ORDER, {"id": "o-1", "amount": 5, "extra": object(), "note": None, "lines": [{"qty": 2}]}) == []


def test_missing_required_type_enum_and_keyword_misses():
    v = sh.validate(ORDER, {"amount": "5", "status": "lost", "lines": [{"qty": "x"}, {}]})
    assert paths(v) == ["amount", "id", "lines[0].qty", "lines[1].qty", "status"]
    by = {x["path"]: x for x in v}
    assert by["id"]["expected"] == "required" and by["amount"]["expected"] == "number" and by["amount"]["actual"] == "string"
    assert by["status"]["expected"] == ["new", "paid"]
    assert set(v[0]) == {"path", "expected", "actual", "message"}


@pytest.mark.parametrize("value,bad_path", [
    ({"id": "o-1", "amount": -1}, "amount"), ({"id": "o-1", "amount": 1001}, "amount"),
    ({"id": "o", "amount": 1}, "id"), ({"id": "o-123456", "amount": 1}, "id"), ({"id": "x-123", "amount": 1}, "id"),
])
def test_keywords(value, bad_path):
    assert set(paths(sh.validate(ORDER, value))) == {bad_path}


def test_boundaries_are_inclusive():
    assert sh.validate(ORDER, {"id": "o-", "amount": 0}) == []
    assert sh.validate(ORDER, {"id": "o-123", "amount": 1000}) == []


def test_null_nullable_unknown_and_integer_float():
    assert paths(sh.validate(ORDER, {"id": "o-1", "amount": None})) == ["amount"]
    assert sh.validate(ORDER, {"id": "o-1", "amount": 1, "note": None}) == []
    assert sh.validate(sh.unknown(), {"anything": 1}) == [] and sh.validate(None, 5) == []
    assert sh.validate({"type": "integer"}, 3.0) == [] and sh.validate({"type": "integer"}, 3.5) != []
    assert sh.validate({"type": "integer"}, True) != []  # bool is not an integer
    assert sh.validate({"type": "number"}, True) != []
    assert sh.validate({"type": "null"}, None) == []
    assert sh.validate({"type": "object"}, [1])[0]["message"] == "expected object, got array"


def test_nested_paths_use_dots_and_brackets():
    shape = {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "object", "properties": {"b": {"type": "string"}}}}}}
    assert paths(sh.validate(shape, {"a": [{"b": "x"}, {"b": 1}]})) == ["a[1].b"]
    assert paths(sh.validate({"type": "string"}, 5, path="body.x")) == ["body.x"]


def test_normalize_keeps_new_keywords_and_spec_is_a_known_source():
    n = sh.normalize({"type": "string", "minLength": 1, "maxLength": 9, "pattern": "^a"}, "spec")
    assert n == {"type": "string", "minLength": 1, "maxLength": 9, "pattern": "^a", "x-source": "spec"}
    n = sh.normalize({"type": "integer", "minimum": 0, "maximum": 5})
    assert n["minimum"] == 0 and n["maximum"] == 5
    assert "spec" in sh.SOURCES
    merged = sh.merge_shapes({"type": "string", "x-source": "spec"}, {"type": "string", "x-source": "observed"})
    assert merged["x-source"] == "observed"
    assert sh.with_source({"type": "string"}, "spec")["x-source"] == "spec"


# ── rename_node_in_tests ─────────────────────────────────────────────────────

def _test(**kw):
    base = {"id": "t_00000001", "name": "t", "target": {"kind": "node", "node": "Old"}, "input": {"items": []},
            "mocks": {"Old": {"items": [{"a": 1}]}, "Other": {"timeout": True}},
            "expect": [{"node": "Old", "op": "count", "value": 1}, {"node": "Other", "op": "count", "value": 1}],
            "covers": ["Old", "Other"]}
    base.update(kw)
    return base


def test_rename_touches_targets_mocks_expect_and_covers_without_mutating():
    original = [_test(), _test(id="t_00000002", target={"kind": "range", "from": "Old", "to": "Other"})]
    snapshot = [dict(t) for t in original]
    out = rename_node_in_tests(original, "Old", "New")
    assert out[0]["target"] == {"kind": "node", "node": "New"}
    assert out[1]["target"] == {"kind": "range", "from": "New", "to": "Other"}
    assert set(out[0]["mocks"]) == {"New", "Other"} and out[0]["mocks"]["New"] == {"items": [{"a": 1}]}
    assert [a["node"] for a in out[0]["expect"]] == ["New", "Other"]
    assert out[0]["covers"] == ["New", "Other"]
    assert original[0]["target"]["node"] == "Old" and [dict(t) for t in original] == snapshot
    assert rename_node_in_tests(original, "Old", "Old") == original
    assert rename_node_in_tests([], "a", "b") == []


def test_rename_in_test_defaults():
    d = {"mocks": {"Old": {"timeout": True}}, "seed": 3}
    assert rename_node_in_test_defaults(d, "Old", "New") == {"mocks": {"New": {"timeout": True}}, "seed": 3}
    assert rename_node_in_test_defaults({}, "a", "b") == {}


# ── lock-rule diffing ────────────────────────────────────────────────────────

def mk(i: str, **kw) -> dict:
    return TestCaseSchema(id=f"t_{i * 8}", name=kw.pop("name", f"test {i}"), **kw).to_stored()


def locked(i: str, **kw) -> dict:
    return mk(i, status="approved", approved_by="sam", approved_at="2026-01-01T00:00:00", **kw)


def test_locked_status_is_derived_from_status():
    assert TestCaseSchema(name="x", status="approved", locked=False).locked is True
    assert TestCaseSchema(name="x", status="draft", locked=True).locked is False


def test_unchanged_locked_test_and_edited_draft_are_fine():
    stored = [locked("a"), mk("b")]
    incoming = [dict(stored[0]), {**stored[1], "name": "edited"}]
    assert locked_test_ids(stored, incoming) == []


def test_changing_or_deleting_a_locked_test_is_refused():
    stored = [locked("a"), locked("b"), mk("c")]
    assert locked_test_ids(stored, [{**stored[0], "name": "x"}, stored[1], stored[2]]) == ["t_aaaaaaaa"]
    assert locked_test_ids(stored, [stored[0], stored[2]]) == ["t_bbbbbbbb"]
    assert locked_test_ids(stored, []) == ["t_aaaaaaaa", "t_bbbbbbbb"]


def test_setting_approval_fields_from_outside_is_refused():
    stored = [mk("a"), mk("b")]
    sneaky = {**stored[0], "status": "approved", "locked": True}
    assert locked_test_ids(stored, [sneaky, stored[1]]) == ["t_aaaaaaaa"]
    assert locked_test_ids(stored, [{**stored[0], "approved_by": "me"}, stored[1]]) == ["t_aaaaaaaa"]
    new_approved = locked("c")
    assert locked_test_ids(stored, [*stored, new_approved]) == ["t_cccccccc"]  # a new test must arrive as a draft
    assert locked_test_ids(stored, [*stored, mk("c")]) == []


def test_unlocking_through_a_plain_save_is_refused_too():
    stored = [locked("a")]
    assert locked_test_ids(stored, [mk("a", name=stored[0]["name"])]) == ["t_aaaaaaaa"]


def test_a_rename_applied_to_stored_first_does_not_trip_the_lock():
    stored = [locked("a", target={"kind": "node", "node": "Old"}, expect=[{"node": "Old", "op": "count", "value": 1}])]
    renamed = normalize_tests(rename_node_in_tests(stored, "Old", "New"))
    incoming = normalize_tests(rename_node_in_tests(stored, "Old", "New"))
    assert locked_test_ids(renamed, incoming) == []
    assert locked_test_ids(stored, incoming) == ["t_aaaaaaaa"]  # the same save without the rename is an edit


def test_test_case_schema_validation():
    with pytest.raises(ValueError):
        TestCaseSchema(id="bad", name="x")
    with pytest.raises(ValueError):
        TestCaseSchema(name="x", target={"kind": "node"})
    with pytest.raises(ValueError):
        TestCaseSchema(name="")
    with pytest.raises(ValueError):
        TestCaseSchema(name="x", category="nope")
    t = TestCaseSchema(name="x")
    assert t.id.startswith("t_") and len(t.id) == 10 and t.target == {"kind": "workflow"} and t.input == {"items": []}

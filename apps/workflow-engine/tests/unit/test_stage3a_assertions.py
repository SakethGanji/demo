"""Assertions (CONTRACTS-STAGE3A §4): every op, paths into arrays, item/all/any, type edges, shape ops."""

from __future__ import annotations

import pytest

from src.engine.assertions import RunView, compare_value, evaluate, resolve_path

RUN = RunView(
    outputs={
        "N": {
            "main": [
                {"id": 1, "name": "Ada", "tags": ["a", "b"], "meta": {"n": 2.5, "ok": True}, "none": None,
                 "rows": [{"v": 10}, {"v": 20}]},
                {"id": 2, "name": "Bob", "tags": [], "meta": {"n": 7, "ok": False}, "rows": []},
            ],
            "error": [],
        },
        "Empty": {},
    },
    requests={"Api": [{"method": "POST", "url": "http://x/y?id=7", "headers": {}, "body": {"amount": 900}}]},
    node_status={"N": "success", "Empty": "no_output", "Bad": "error"},
    contracts={"N": {"main": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                              "required": ["id", "name"], "x-source": "derived"}}},
)


def ev(**a):
    a.setdefault("node", "N")
    return evaluate(a, RUN)


@pytest.mark.parametrize("assertion,passed", [
    ({"path": "name", "op": "eq", "value": "Ada"}, True),
    ({"path": "name", "op": "eq", "value": "Bob"}, False),
    ({"path": "id", "op": "eq", "value": 1.0}, True),
    ({"path": "meta.ok", "op": "eq", "value": 1}, False),  # True is not 1
    ({"path": "name", "op": "neq", "value": "Bob"}, True),
    ({"path": "name", "op": "neq", "value": "Ada"}, False),
    ({"path": "id", "op": "gt", "value": 0}, True),
    ({"path": "id", "op": "gt", "value": 1}, False),
    ({"path": "id", "op": "gte", "value": 1}, True),
    ({"path": "meta.n", "op": "lt", "value": 3}, True),
    ({"path": "meta.n", "op": "lte", "value": 2.5}, True),
    ({"path": "name", "op": "contains", "value": "da"}, True),
    ({"path": "tags", "op": "contains", "value": "b"}, True),
    ({"path": "tags", "op": "contains", "value": "z"}, False),
    ({"path": "meta", "op": "contains", "value": "n"}, True),
    ({"path": "name", "op": "matches", "value": "^A.a$"}, True),
    ({"path": "name", "op": "matches", "value": "^b"}, False),
    ({"path": "name", "op": "exists"}, True),
    ({"path": "nope", "op": "exists"}, False),
    ({"path": "none", "op": "exists"}, True),  # null counts as existing
    ({"path": "nope", "op": "not_exists"}, True),
    ({"path": "name", "op": "not_exists"}, False),
    ({"op": "count", "value": 2}, True),
    ({"op": "count", "value": 3}, False),
    ({"port": "error", "op": "count", "value": 0}, True),
    ({"path": "name", "op": "type", "value": "string"}, True),
    ({"path": "id", "op": "type", "value": "integer"}, True),
    ({"path": "id", "op": "type", "value": "number"}, True),  # an integer is also a number
    ({"path": "meta.n", "op": "type", "value": "integer"}, False),
    ({"path": "meta.ok", "op": "type", "value": "number"}, False),  # bool is not a number
    ({"path": "meta.ok", "op": "type", "value": "boolean"}, True),
    ({"path": "none", "op": "type", "value": "null"}, True),
    ({"path": "tags", "op": "type", "value": "array"}, True),
    ({"path": "meta", "op": "type", "value": "object"}, True),
    ({"path": "name", "op": "one_of", "value": ["Ada", "Eve"]}, True),
    ({"path": "name", "op": "one_of", "value": ["Eve"]}, False),
    ({"path": "meta.n", "op": "range", "value": {"min": 2, "max": 3}}, True),
    ({"path": "meta.n", "op": "range", "value": {"min": 2.5, "max": 2.5}}, True),  # inclusive
    ({"path": "meta.n", "op": "range", "value": {"min": 3}}, False),
    ({"path": "meta.n", "op": "range", "value": {"max": 3}}, True),
    ({"op": "status", "value": "success"}, True),
    ({"node": "Empty", "op": "status", "value": "no_output"}, True),
    ({"node": "Bad", "op": "status", "value": "error"}, True),
    ({"node": "Missing", "op": "status", "value": "not_run"}, True),
    ({"node": "Missing", "op": "status", "value": "success"}, False),
    ({"subject": "node", "op": "status", "value": "success"}, True),
])
def test_ops(assertion, passed):
    r = ev(**assertion)
    assert r["passed"] is passed, r


def test_paths_into_arrays_and_nested_arrays():
    assert ev(path="rows[1].v", op="eq", value=20)["passed"]
    assert ev(path="rows[2]", op="not_exists")["passed"]
    assert ev(path="tags[0]", op="eq", value="a")["passed"]
    assert ev(path="rows[0].v", op="eq", value=11)["actual"] == 10
    assert resolve_path({"a": [[1, 2], [3]]}, "a[1][0]") == (True, 3)
    assert resolve_path([5, 6], "[1]") == (True, 6)
    assert resolve_path({"a": 1}, "") == (True, {"a": 1})


def test_item_selection_all_any_and_out_of_range():
    assert ev(item=1, path="name", op="eq", value="Bob")["passed"]
    assert ev(item="all", path="id", op="gt", value=0)["passed"]
    r = ev(item="all", path="id", op="gt", value=1)
    assert not r["passed"] and r["actual"] == 1 and "item 0" in r["message"]
    assert ev(item="any", path="name", op="eq", value="Bob")["passed"]
    assert not ev(item="any", path="name", op="eq", value="Zed")["passed"]
    assert not ev(item=5, path="name", op="eq", value="Ada")["passed"]
    assert ev(item=5, path="name", op="not_exists")["passed"]
    empty = ev(node="Empty", item="all", path="x", op="eq", value=1)
    assert not empty["passed"]  # nothing to check never passes silently


def test_type_mismatch_fails_with_a_message_and_never_raises():
    for op, actual, value in [("gt", "10", 5), ("lt", 5, "10"), ("gte", None, 1), ("gt", True, 0), ("range", "x", {"min": 1}),
                              ("matches", 5, "a"), ("contains", 5, 1), ("contains", "abc", 1)]:
        passed, msg = compare_value(op, True, actual, value)
        assert passed is False and "type mismatch" in msg, (op, actual, value, msg)
    assert compare_value("matches", True, "abc", "(")[0] is False  # invalid regex
    assert compare_value("one_of", True, 1, "notalist")[0] is False
    assert compare_value("bogus", True, 1, 1)[0] is False
    assert compare_value("gt", True, "b", "a")[0] is True  # strings compare with strings


def test_missing_path_fails_value_ops():
    r = ev(path="ghost", op="eq", value=1)
    assert not r["passed"] and r["message"] == "path not found"


def test_request_subject_reads_captured_requests():
    base = {"node": "Api", "subject": "request"}
    assert evaluate({**base, "path": "body.amount", "op": "eq", "value": 900}, RUN)["passed"]
    assert evaluate({**base, "path": "method", "op": "eq", "value": "POST"}, RUN)["passed"]
    assert evaluate({**base, "path": "url", "op": "contains", "value": "id=7"}, RUN)["passed"]
    assert evaluate({**base, "op": "count", "value": 1}, RUN)["passed"]
    assert not evaluate({**base, "item": 1, "path": "method", "op": "eq", "value": "POST"}, RUN)["passed"]


def test_shape_op_uses_an_inline_shape_or_the_contract():
    assert ev(op="shape", item="all")["passed"]  # contract: id integer, name string
    bad = evaluate({"node": "N", "op": "shape", "path": "meta", "value": {"type": "object", "properties": {"n": {"type": "string"}}}}, RUN)
    assert not bad["passed"] and "meta.n".split(".")[1] in bad["message"]
    nocontract = evaluate({"node": "Empty", "op": "shape"}, RUN)
    assert nocontract["passed"] and nocontract["note"] == "no contract"
    unknown = RunView(outputs={"U": {"main": [{"a": 1}]}}, contracts={"U": {"main": {"type": "unknown"}}})
    assert evaluate({"node": "U", "op": "shape"}, unknown)["note"] == "no contract"


def test_result_structure():
    r = ev(id="a1", path="name", op="eq", value="Ada", port="main")
    assert set(r) >= {"passed", "node", "port", "path", "op", "expected", "actual", "message"} and r["id"] == "a1"
    assert r["actual"] == "Ada" and r["expected"] == "Ada" and r["port"] == "main"

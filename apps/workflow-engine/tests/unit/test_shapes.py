"""Shape helpers (src/engine/shapes.py): lookup None vs UNKNOWN, merge, from_sample, normalize."""

from src.engine import shapes as sh
from src.engine.shapes import UNKNOWN, lookup, merge_shapes, object_of, scalar


def user():
    return object_of({"name": scalar("string"), "address": object_of({"city": scalar("string")})}, ["name"])


# ── lookup ──

def test_lookup_present_path_returns_child():
    assert lookup(user(), ["address", "city"])["type"] == "string"


def test_lookup_missing_property_of_closed_object_is_none():
    assert lookup(user(), ["nope"]) is None
    assert lookup(user(), ["address", "zip"]) is None


def test_lookup_open_object_is_unknown_not_none():
    open_obj = scalar("object")  # no "properties": we only know it is an object
    r = lookup(open_obj, ["anything"])
    assert r is not None and r["type"] == "unknown"


def test_lookup_through_unknown_and_scalars_is_unknown():
    assert lookup(UNKNOWN, ["a", "b"])["type"] == "unknown"
    assert lookup(scalar("string"), ["length"])["type"] == "unknown"
    assert lookup(object_of({"x": UNKNOWN}), ["x", "deep"])["type"] == "unknown"


def test_lookup_arrays_index_and_methods():
    arr = sh.array_of(object_of({"id": scalar("integer")}))
    assert lookup(arr, [0, "id"])["type"] == "integer"
    assert lookup(arr, ["0", "id"])["type"] == "integer"
    assert lookup(arr, ["length"])["type"] == "unknown"  # not a "missing property"
    assert lookup(sh.array_of(), [0])["type"] == "unknown"


def test_lookup_empty_path_and_none_shape():
    assert lookup(user(), []) == user()
    assert lookup(None, ["a"])["type"] == "unknown"


def test_lookup_child_inherits_parent_source():
    parent = {**user(), "x-source": "observed"}
    parent["properties"] = {"name": {"type": "string"}}
    assert lookup(parent, ["name"])["x-source"] == "observed"


def test_lookup_does_not_mutate():
    s = user()
    before = repr(s)
    lookup(s, ["address", "city"])
    assert repr(s) == before


# ── merge ──

def test_merge_unions_properties_and_intersects_required():
    a = object_of({"a": scalar("string"), "shared": scalar("string")}, ["a", "shared"])
    b = object_of({"b": scalar("integer"), "shared": scalar("string")}, ["shared"])
    m = merge_shapes(a, b)
    assert set(m["properties"]) == {"a", "b", "shared"}
    assert m["required"] == ["shared"]


def test_merge_type_conflict_is_unknown():
    assert merge_shapes(scalar("string"), scalar("boolean"))["type"] == "unknown"
    assert merge_shapes(object_of({"x": scalar("string")}), object_of({"x": scalar("boolean")}))["properties"]["x"]["type"] == "unknown"


def test_merge_widenings_are_not_conflicts():
    assert merge_shapes(scalar("integer"), scalar("number"))["type"] == "number"
    m = merge_shapes(scalar("string"), scalar("null"))
    assert m["type"] == "string" and m["nullable"] is True


def test_merge_with_unknown_is_unknown():
    assert merge_shapes(user(), UNKNOWN)["type"] == "unknown"


def test_merge_open_object_stays_open():
    m = merge_shapes(user(), scalar("object"))
    assert m["type"] == "object" and "properties" not in m


def test_merge_arrays_merge_items():
    m = merge_shapes(sh.array_of(object_of({"a": scalar("string")})), sh.array_of(object_of({"b": scalar("string")})))
    assert set(m["items"]["properties"]) == {"a", "b"}


def test_merge_does_not_mutate_inputs():
    a, b = user(), object_of({"z": scalar("string")})
    ra, rb = repr(a), repr(b)
    merge_shapes(a, b)
    assert (repr(a), repr(b)) == (ra, rb)


def test_merge_all_empty_is_unknown():
    assert sh.merge_all([])["type"] == "unknown"


# ── from_sample / normalize ──

def test_from_sample_observed_and_merges_all_items():
    s = sh.from_sample([{"id": 1, "name": "a"}, {"id": 2, "extra": True, "name": None}])
    assert s["x-source"] == "observed"
    assert s["properties"]["id"]["type"] == "integer"
    assert s["properties"]["extra"]["type"] == "boolean"
    assert s["properties"]["name"]["type"] == "string" and s["properties"]["name"]["nullable"]
    assert s["required"] == ["id", "name"]  # extra only in one item
    assert s["properties"]["id"]["x-source"] == "observed"


def test_from_sample_nested_and_empty():
    s = sh.from_sample([{"tags": ["a"], "owner": {"id": 1}}])
    assert s["properties"]["tags"]["items"]["type"] == "string"
    assert lookup(s, ["owner", "id"])["type"] == "integer"
    assert lookup(s, ["owner", "nope"]) is None
    assert sh.from_sample([])["type"] == "unknown"


def test_normalize_accepts_json_string_and_nullable_type_list():
    s = sh.normalize('{"type": "object", "properties": {"a": {"type": ["string", "null"]}}}')
    assert s["properties"]["a"]["type"] == "string" and s["properties"]["a"]["nullable"]
    assert sh.normalize("not json")["type"] == "unknown"
    assert sh.normalize({"type": "weird"})["type"] == "unknown"


def test_param_shape_and_expression_value_shape():
    assert sh.param_shape({}, "input_schema") is None
    assert sh.param_shape({"input_schema": {"type": "object", "properties": {}}}, "input_schema")["x-source"] == "declared"
    item = object_of({"a": object_of({"b": scalar("integer")})})
    assert sh.expression_value_shape("{{ $json.a.b }}", item)["type"] == "integer"
    assert sh.expression_value_shape("{{ $json.a.b + 1 }}", item)["type"] == "unknown"
    assert sh.expression_value_shape("hello", item)["type"] == "string"
    assert sh.expression_value_shape(3, item)["type"] == "integer"


def test_error_shape_extends_closed_objects_only():
    e = sh.error_shape(user())
    assert set(e["properties"]) == {"name", "address", "error"}
    assert set(e["properties"]["error"]["properties"]) == {"message", "node"}
    assert sh.error_shape(UNKNOWN)["type"] == "unknown"

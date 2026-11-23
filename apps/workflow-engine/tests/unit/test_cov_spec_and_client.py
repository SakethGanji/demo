"""Spec validation/diff/OpenAPI, typed-client codegen, API-call scanning, schema inference (pure)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.api_usage import extract_api_calls, scan_api_calls
from src.services.client_codegen import _ts_type, generate_client
from src.services.schema_inference import infer_json_schema
from src.services.spec_tools import build_openapi, diff_specs, spec_hash, validate

P = SimpleNamespace(name="Marketplace", slug="mkt")


def route(**kw):
    base = {"id": "route_1", "name": "getX", "method": "GET", "path": "/x", "auth": "none", "description": None,
            "request_schema": None, "response_schema": None, "response_mode": "auto", "handler": {"type": "stub"}}
    return {**base, **kw}


# ── validate() ──────────────────────────────────────────────────────────────

def test_validate_types_required_nested_arrays_enum():
    schema = {"type": "object", "required": ["amount", "items"], "properties": {
        "amount": {"type": "number"}, "kind": {"enum": ["a", "b"]},
        "items": {"type": "array", "items": {"type": "object", "required": ["sku"], "properties": {"sku": {"type": "string"}}}}}}
    assert validate({"amount": 1, "items": [{"sku": "s"}]}, schema) == []
    errs = validate({"amount": "1", "kind": "z", "items": [{}, {"sku": 3}]}, schema)
    paths = {e["path"] for e in errs}
    assert paths == {"amount", "kind", "items[0].sku", "items[1].sku"}
    assert {e["path"] for e in validate({}, schema)} == {"amount", "items"}
    assert validate([], schema)[0]["path"] == "$"


def test_validate_bool_is_not_number_or_integer_and_integer_rejects_float():
    assert validate(True, {"type": "number"}) and validate(True, {"type": "integer"})
    assert validate(1.5, {"type": "integer"})
    assert validate(2, {"type": "number"}) == []
    assert validate(None, {"type": ["string", "null"]}) == []
    assert validate(5, {"type": ["string", "null"]})


@pytest.mark.parametrize("schema", [None, {}, "x", []])
def test_validate_ignores_empty_or_bad_schema(schema):
    assert validate({"anything": 1}, schema) == []


# ── OpenAPI / hash / diff ───────────────────────────────────────────────────

def test_build_openapi_shape_auth_params_and_determinism():
    rs = [route(id="r2", name="createOrder", method="POST", path="/orders", auth="api_key",
                request_schema={"type": "object", "properties": {"body": {"type": "object", "properties": {"t": {"type": "number"}}, "required": ["t"]}}}),
          route(id="r1", name="getOrder", path="/orders/{id}", auth="app_token",
                request_schema={"type": "object", "properties": {"query": {"type": "object", "properties": {"expand": {"type": "string"}}, "required": ["expand"]}}})]
    spec = build_openapi(P, rs, revision=3)
    assert spec["openapi"].startswith("3.1") and spec["info"] == {"title": "Marketplace", "version": "3"}
    assert spec["servers"] == [{"url": "/api/p/mkt"}]
    post = spec["paths"]["/orders"]["post"]
    assert post["security"] == [{"apiKey": []}] and post["requestBody"]["required"] is True
    get = spec["paths"]["/orders/{id}"]["get"]
    assert get["security"] == [{"appToken": []}, {"apiKey": []}]
    assert {(p["name"], p["in"], p["required"]) for p in get["parameters"]} == {("id", "path", True), ("expand", "query", True)}
    assert build_openapi(P, [route(auth="none")])["paths"]["/x"]["get"]["security"] == []
    assert spec_hash(build_openapi(P, rs, 1)) == spec_hash(build_openapi(P, rs[::-1], 99))  # revision/order independent
    assert spec_hash(build_openapi(P, rs)) != spec_hash(build_openapi(P, rs[:1]))


def _diff(old, new):
    return diff_specs(build_openapi(P, old), build_openapi(P, new))


def test_diff_added_removed_renamed_moved():
    a, b = route(id="r1"), route(id="r2", name="getY", path="/y")
    kinds = lambda ch: {(c["kind"], c["breaking"]) for c in ch}
    assert kinds(_diff([a], [a, b])) == {("route_added", False)}
    assert kinds(_diff([a, b], [a])) == {("route_removed", True)}
    assert ("operation_renamed", True) in kinds(_diff([a], [route(id="r1", name="fetchX")]))
    assert ("path_changed", True) in kinds(_diff([a], [route(id="r1", path="/z")]))
    assert ("method_changed", True) in kinds(_diff([a], [route(id="r1", method="POST")]))
    assert _diff([a], [a]) == []


def test_diff_response_and_request_fields():
    obj = lambda **p: {"type": "object", "properties": p}
    old = route(response_schema=obj(a={"type": "string"}, b={"type": "number"}))
    new = route(response_schema=obj(a={"type": "number"}, c={"type": "string"}))
    got = {(c["kind"], c["field"], c["breaking"]) for c in _diff([old], [new])}
    assert ("response_field_type_changed", "a", True) in got
    assert ("response_field_removed", "b", True) in got
    assert ("field_added", "c", False) in got
    req_old = route(method="POST", request_schema={"type": "object", "properties": {"body": obj(t={"type": "number"})}})
    req_new = route(method="POST", request_schema={"type": "object", "properties": {"body": {**obj(t={"type": "number"}, cur={"type": "string"}), "required": ["cur"]}}})
    assert ("request_required_added", "body.cur", True) in {(c["kind"], c["field"], c["breaking"]) for c in _diff([req_old], [req_new])}


def test_diff_from_none_lists_everything_as_added():
    ch = diff_specs(None, build_openapi(P, [route()]))
    assert [c["kind"] for c in ch] == ["route_added"]


# ── client codegen ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("schema,expected", [
    (None, "any"), ({}, "any"), ({"type": "string"}, "string"), ({"type": "integer"}, "number"), ({"type": "boolean"}, "boolean"),
    ({"type": "array", "items": {"type": "string"}}, "Array<string>"), ({"enum": ["a", "b"]}, '"a" | "b"'),
    ({"type": ["string", "null"]}, "null | string"), ({"type": "object"}, "Record<string, any>"),
    ({"type": "object", "properties": {"a": {"type": "string"}, "b-c": {"type": "number"}}, "required": ["a"]}, '{ a: string; "b-c"?: number }'),
])
def test_ts_type(schema, expected):
    assert _ts_type(schema) == expected


def test_ts_type_depth_limit_does_not_recurse_forever():
    deep = {"type": "string"}
    for _ in range(30):
        deep = {"type": "array", "items": deep}
    assert "any" in _ts_type(deep)


def test_generate_client_omits_api_key_routes_and_embeds_hash():
    rs = [route(id="a", name="listOrders", path="/orders"),
          route(id="b", name="adminStats", path="/admin/stats", auth="api_key"),
          route(id="c", name="createOrder", method="POST", path="/orders/{id}", auth="app_token",
                request_schema={"type": "object", "properties": {"body": {"type": "object", "properties": {"t": {"type": "number"}}}}})]
    src = generate_client("mkt", rs, 4, "HASH123")
    assert "adminStats" not in src and "listOrders" in src and "createOrder" in src
    assert "spec rev 4, spec_hash HASH123" in src and "BASE = '/api/p/mkt'" in src
    assert "encodeURIComponent(String(params.id))" in src and "CreateOrderBody" in src
    assert "spec rev draft" in generate_client("mkt", [], None, "h")


# ── API-call scanning ───────────────────────────────────────────────────────

APP = """import { api } from './api/client'
export default function App() {
  const a = () => api.listOrders()
  const b = () => fetch('/api/p/mkt/orders/7', { method: 'DELETE' })
  const c = () => fetch(`/api/p/mkt/disputes?x=1`)
  const d = '/api/p/other/orders'
  return <div/>
}
"""


def test_scan_and_filter_api_calls_by_slug():
    calls = extract_api_calls(APP, "mkt")
    assert [(c["kind"], c["operation"], c["method"], c["path"], c["line"]) for c in calls] == [
        ("client_call", "listOrders", None, None, 3),
        ("literal", None, "DELETE", "/orders/7", 4),
        ("literal", None, "GET", "/disputes", 5)]
    assert [c["path"] for c in extract_api_calls(APP, "other") if c["kind"] == "literal"] == ["/orders"]
    assert extract_api_calls("not (valid tsx", "mkt") is not None
    assert scan_api_calls("") == []


# ── schema inference ────────────────────────────────────────────────────────

def test_infer_json_schema_scalars_nested_and_item_wrapper():
    assert infer_json_schema(None) == {"type": "null"} and infer_json_schema(True) == {"type": "boolean"}
    assert infer_json_schema(1) == {"type": "integer"} and infer_json_schema(1.5) == {"type": "number"}
    assert infer_json_schema([]) == {"type": "array", "items": {}}
    assert infer_json_schema([{"json": {"a": 1}}]) == {"type": "array", "items": {"type": "object", "properties": {"a": {"type": "integer"}}}}
    assert infer_json_schema({"a": [1, 2], "b": {"c": "s"}})["properties"]["b"]["properties"]["c"] == {"type": "string"}
    deep: dict = {}
    cur = deep
    for _ in range(50):
        cur["n"] = {}
        cur = cur["n"]
    assert "unknown" in str(infer_json_schema(deep))

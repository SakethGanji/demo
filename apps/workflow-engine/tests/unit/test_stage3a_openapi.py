"""Stage 3A: OpenAPI 3 import (services/openapi_import.py), written from CONTRACTS-STAGE3A section 6."""

import json
from pathlib import Path

import pytest

from src.services.openapi_import import SWAGGER2_MESSAGE, SpecError, import_openapi, parse_spec_text, schema_to_shape

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "openapi"


def load(name):
    return import_openapi((FIX / name).read_text())


def op(spec, operation_id):
    return next(o for o in spec["operations"] if o["operation_id"] == operation_id)


def test_risk_api_yaml():
    spec = load("risk_api.yaml")
    assert spec["title"] == "Risk API" and spec["base_url"] == "http://127.0.0.1:9999"
    risk = op(spec, "riskCheck")
    assert risk["method"] == "POST" and risk["path"] == "/risk-check" and risk["path_params"] == []
    assert risk["request_shape"]["properties"]["amount"]["type"] == "number"
    assert risk["request_shape"]["required"] == ["order_id", "amount"]
    assert list(risk["responses"]) == ["200", "400", "404", "500"]
    ok = risk["responses"]["200"]["shape"]
    assert ok["x-source"] == "spec" and ok["properties"]["decision"]["enum"] == ["approve", "review", "deny"]
    assert ok["properties"]["decision"]["x-source"] == "spec"
    assert ok["properties"]["score"]["minimum"] == 0 and ok["properties"]["score"]["maximum"] == 100
    assert risk["responses"]["404"]["shape"] is None and risk["responses"]["404"]["description"] == "Order not found"


def test_operation_id_fallback_and_path_params():
    spec = load("risk_api.yaml")
    get = op(spec, "GET /orders/{order_id}")
    assert get["path_params"] == ["order_id"] and get["method"] == "GET"
    shape = get["responses"]["200"]["shape"]["properties"]
    assert shape["id"]["format"] == "uuid" and shape["placed_at"]["format"] == "date-time"


def test_petstore_refs_resolved_and_path_level_params():
    spec = load("petstore_3_0.json")
    assert spec["base_url"] == "/api/v3" and len(spec["operations"]) == 4
    pet = op(spec, "getPetById")
    assert pet["path_params"] == ["petId"]
    shape = pet["responses"]["200"]["shape"]
    assert shape["required"] == ["name", "photoUrls"]
    assert shape["properties"]["category"]["properties"]["name"]["type"] == "string"
    assert shape["properties"]["tags"]["items"]["properties"]["id"]["type"] == "integer"
    assert shape["properties"]["status"]["enum"] == ["available", "pending", "sold"]
    add = op(spec, "addPet")
    assert add["request_shape"]["properties"]["photoUrls"]["items"]["type"] == "string"
    find = op(spec, "findPetsByStatus")
    assert find["responses"]["200"]["shape"]["type"] == "array"
    assert "$ref" not in json.dumps(spec)


def test_openapi_31_features():
    spec = load("tree_3_1.yaml")
    assert spec["base_url"] == "https://eu.example.com/v1"
    node = op(spec, "getNode")
    assert node["path_params"] == ["id"]
    resp = node["responses"]["200"]
    assert resp["example"] == {"id": "n1", "label": "root", "children": []}
    s = resp["shape"]
    assert s["properties"]["label"] == {"type": "string", "nullable": True, "x-source": "spec"}
    assert s["properties"]["id"]["minLength"] == 2 and s["properties"]["id"]["maxLength"] == 8 and s["properties"]["id"]["pattern"] == "^n"
    assert s["properties"]["count"]["minimum"] == 0
    # allOf merges objects; a recursive $ref collapses to unknown rather than looping
    meta = s["properties"]["meta"]
    assert set(meta["properties"]) == {"a", "b"} and meta["required"] == ["a"]
    assert s["properties"]["children"]["items"]["type"] == "unknown"


def test_swagger2_rejected_with_exact_message():
    with pytest.raises(SpecError) as e:
        import_openapi({"swagger": "2.0", "info": {}, "paths": {}})
    assert str(e.value) == "Swagger 2 isn't supported; convert to OpenAPI 3" == SWAGGER2_MESSAGE
    with pytest.raises(SpecError, match="Swagger 2"):
        import_openapi("swagger: '2.0'\npaths: {}\n")


@pytest.mark.parametrize("doc,msg", [
    ({"paths": {}}, "openapi"),
    ({"openapi": "4.0.0", "paths": {}}, "isn't supported"),
    ({"openapi": "3.0.0"}, "paths"),
    ([1, 2], "object"),
    ({"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {"$ref": "http://x.test/a.json#/r"}}}}}}, "Remote"),
    ({"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {"$ref": "#/components/nope"}}}}}}, "Unresolvable"),
])
def test_bad_specs(doc, msg):
    with pytest.raises(SpecError, match=msg):
        import_openapi(doc)


def test_text_parsing_errors():
    with pytest.raises(SpecError):
        parse_spec_text("")
    with pytest.raises(SpecError, match="JSON"):
        parse_spec_text("{not json")
    with pytest.raises(SpecError):
        import_openapi("a: [unclosed")


def test_yaml_dates_stay_strings():
    spec = import_openapi("""
openapi: 3.0.0
paths:
  /a:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              example: {when: 2026-01-01}
""")
    assert spec["operations"][0]["responses"]["200"]["example"] == {"when": "2026-01-01"}
    json.dumps(spec)


def test_schema_to_shape_basics():
    assert schema_to_shape({"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a", "ghost"]}, {}) == {
        "type": "object", "properties": {"a": {"type": "integer", "x-source": "spec"}}, "required": ["a"], "x-source": "spec"}
    assert schema_to_shape({"type": "string", "nullable": True, "enum": ["x"]}, {})["nullable"] is True
    assert schema_to_shape({"oneOf": [{"type": "string"}, {"type": "integer"}]}, {})["type"] == "unknown"
    assert schema_to_shape({"anyOf": [{"type": "string"}, {"type": "null"}]}, {}) == {"type": "string", "nullable": True, "x-source": "spec"}
    assert schema_to_shape(None, {})["type"] == "unknown"
    assert schema_to_shape({"type": "object"}, {}) == {"type": "object", "x-source": "spec"}  # open object

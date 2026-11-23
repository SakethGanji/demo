"""Stage 3A: deterministic example data and mock options (services/mock_gen.py)."""

import copy
from pathlib import Path

from src.services.mock_gen import example_from_shape, mock_options
from src.services.openapi_import import import_openapi

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "openapi"


def test_rules():
    shape = {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["approve", "deny"]},
        "score": {"type": "number", "minimum": 5.5, "maximum": 100},
        "count": {"type": "integer"},
        "capped": {"type": "integer", "minimum": 50, "maximum": 10},
        "ok": {"type": "boolean"},
        "at": {"type": "string", "format": "date-time"},
        "day": {"type": "string", "format": "date"},
        "id": {"type": "string", "format": "uuid"},
        "name": {"type": "string"},
        "short": {"type": "string", "maxLength": 3},
        "long": {"type": "string", "minLength": 20},
        "tags": {"type": "array", "items": {"type": "string"}},
        "none": {"type": "array"},
        "nested": {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}}},
        "free": {"type": "object"},
        "nul": {"type": "null"},
        "what": {"type": "unknown"},
    }}
    v = example_from_shape(shape, 1)
    assert v["decision"] == "approve" and v["score"] == 5.5 and v["count"] == 1 and v["capped"] == 10
    assert v["ok"] is True and v["at"] == "2026-01-01T09:00:00Z" and v["day"] == "2026-01-01"
    assert v["id"] == "00000000-0000-4000-8000-000000000001"
    assert v["name"] == "name_example" and v["short"] == "sho" and len(v["long"]) == 20
    assert v["tags"] == ["tags_example"] and v["none"] == [] and v["nested"] == {"x": 0} and v["free"] == {}
    assert v["nul"] is None and v["what"] is None


def test_deterministic_and_pure():
    shape = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {"type": "integer"}}}}
    before = copy.deepcopy(shape)
    assert example_from_shape(shape, 1) == example_from_shape(shape, 1) == example_from_shape(shape, 99)
    assert shape == before
    assert example_from_shape(None) is None
    assert example_from_shape({"type": "string"}, 1, "who") == "who_example"


def test_mock_options_for_risk_api():
    spec = import_openapi((FIX / "risk_api.yaml").read_text())
    risk = next(o for o in spec["operations"] if o["operation_id"] == "riskCheck")
    opts = mock_options(risk)
    assert [(o["label"], o["category"]) for o in opts] == [
        ("200 success", "ext_success"), ("400 Bad request", "ext_error"), ("404 Not found", "ext_error"),
        ("500 Internal server error", "ext_error"), ("200 empty", "ext_empty"), ("Timeout", "ext_timeout")]
    by = {o["label"]: o["mock"] for o in opts}
    assert by["200 success"] == {"http": {"status": 200, "body": {"decision": "approve", "score": 0}}}
    assert by["400 Bad request"] == {"http": {"status": 400, "body": {"error": "error_example"}}}
    assert by["404 Not found"] == {"http": {"status": 404}}  # no schema, no example: no body
    assert by["200 empty"] == {"http": {"status": 200, "body": {}}}
    assert by["Timeout"] == {"timeout": True}


def test_example_beats_shape_and_array_empty():
    op = {"responses": {"200": {"description": "", "shape": {"type": "array", "items": {"type": "integer"}}, "example": [7, 8]}}}
    opts = mock_options(op)
    assert opts[0]["mock"]["http"]["body"] == [7, 8]
    assert next(o for o in opts if o["category"] == "ext_empty")["mock"]["http"]["body"] == []


def test_no_documented_success_means_no_empty_option():
    opts = mock_options({"responses": {"404": {"description": "", "shape": None, "example": None}}})
    assert [o["category"] for o in opts] == ["ext_error", "ext_timeout"]

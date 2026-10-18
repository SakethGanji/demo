"""Shape -> form fields and server-side validation of a submission (Stage 5, DESIGN-STAGE5-UIS section 4.1)."""

from __future__ import annotations

import pytest

from src.engine import form_fields as ff

SCHEMA = {"type": "object", "required": ["order_id", "amount", "reason", "email"], "properties": {
    "order_id": {"type": "string", "pattern": r"^A-\d{5}$"},
    "amount": {"type": "number", "minimum": 0.01, "maximum": 100000, "format": "currency"},
    "reason": {"type": "string", "enum": ["not_received", "damaged", "wrong_item", "other"]},
    "email": {"type": "string", "format": "email"},
}}


@pytest.mark.parametrize("shape,widget", [
    ({"type": "string"}, "text"),
    ({"type": "string", "maxLength": 200}, "text"),
    ({"type": "string", "maxLength": 201}, "textarea"),
    ({"type": "string", "format": "email"}, "email"),
    ({"type": "string", "format": "date"}, "date"),
    ({"type": "string", "enum": ["a", "b"]}, "radio"),
    ({"type": "string", "enum": list("abcdefg")}, "radio"),
    ({"type": "string", "enum": list("abcdefgh")}, "select"),
    ({"type": "number"}, "number"),
    ({"type": "integer"}, "number"),
    ({"type": "number", "format": "currency"}, "currency"),
    ({"type": "boolean"}, "checkbox"),
])
def test_widget_mapping(shape, widget):
    assert ff.derive_widget(shape) == widget


def test_derive_fields_for_the_refund_form():
    fields = ff.derive_fields(SCHEMA, {"order_id": {"label": "Order number", "placeholder": "A-12345"}}, ["order_id", "amount"])
    assert [f["name"] for f in fields] == ["order_id", "amount", "reason", "email"]
    order, amount, reason, email = fields
    assert order["label"] == "Order number" and order["placeholder"] == "A-12345" and order["pattern"] == r"^A-\d{5}$"
    assert order["maxLength"] == ff.DEFAULT_MAX_LENGTH and order["required"] is True
    assert amount["widget"] == "currency" and amount["min"] == 0.01 and amount["max"] == 100000
    assert reason["widget"] == "radio" and [o["value"] for o in reason["options"]] == ["not_received", "damaged", "wrong_item", "other"]
    assert reason["options"][0]["label"] == "Not received"
    assert email["widget"] == "email" and email["label"] == "Email"
    assert set(order) <= {"name", "label", "widget", "required", "help", "placeholder", "options", "min", "max", "minLength", "maxLength", "pattern"}


def test_unsupported_types_are_omitted_and_reported():
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
                                                "address": {"type": "object", "properties": {"zip": {"type": "string"}}}}}
    assert [f["name"] for f in ff.derive_fields(schema)] == ["name"]
    assert ff.unsupported_fields(schema) == ["items", "address"]


def test_a_widget_override_is_honoured_only_when_known():
    fields = ff.derive_fields({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}},
                              {"a": {"widget": "textarea"}, "b": {"widget": "nonsense"}})
    assert [f["widget"] for f in fields] == ["textarea", "text"]


def test_a_json_string_schema_and_an_empty_one_do_not_raise():
    assert ff.derive_fields('{"type": "object", "properties": {"a": {"type": "string"}}}')[0]["name"] == "a"
    assert ff.derive_fields(None) == [] and ff.derive_fields("not json") == []


GOOD = {"order_id": "A-12345", "amount": 742, "reason": "damaged", "email": "dana@example.com"}


def test_a_valid_submission_passes_unchanged():
    clean, errors = ff.validate_submission(SCHEMA, GOOD)
    assert errors == {} and clean == GOOD


@pytest.mark.parametrize("change,field", [
    ({"order_id": "12345"}, "order_id"),
    ({"amount": 0}, "amount"),
    ({"amount": 100001}, "amount"),
    ({"amount": "742"}, "amount"),
    ({"reason": "refund me"}, "reason"),
    ({"email": "not-an-email"}, "email"),
    ({"order_id": ""}, "order_id"),
    ({"order_id": "   "}, "order_id"),
])
def test_invalid_values_are_field_errors(change, field):
    clean, errors = ff.validate_submission(SCHEMA, {**GOOD, **change})
    assert field in errors and field not in clean


def test_missing_required_and_unknown_fields():
    rest = {k: v for k, v in GOOD.items() if k != "email"}
    _, errors = ff.validate_submission(SCHEMA, {**rest, "extra": 1})
    assert errors == {"email": "this field is required", "extra": "unknown field"}


def test_an_unsupported_schema_field_cannot_be_submitted():
    schema = {"type": "object", "properties": {"tags": {"type": "array"}}}
    assert ff.validate_submission(schema, {"tags": ["a"]})[1] == {"tags": "unknown field"}


def test_strings_are_nfc_normalised_and_trimmed_and_never_evaluated():
    schema = {"type": "object", "properties": {"note": {"type": "string"}}}
    clean, errors = ff.validate_submission(schema, {"note": "  Café {{ $vars.SECRET }}  "})
    assert errors == {} and clean == {"note": "Café {{ $vars.SECRET }}"}


def test_optional_blank_strings_are_dropped():
    schema = {"type": "object", "properties": {"note": {"type": "string"}, "n": {"type": "number"}}}
    assert ff.validate_submission(schema, {"note": "", "n": 3}) == ({"n": 3}, {})


def test_the_default_max_length_applies_only_without_an_explicit_one():
    schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string", "maxLength": 10000}}}
    big = "x" * (ff.DEFAULT_MAX_LENGTH + 1)
    assert "a" in ff.validate_submission(schema, {"a": big})[1]
    assert ff.validate_submission(schema, {"b": big})[1] == {}


def test_dates_and_booleans():
    schema = {"type": "object", "properties": {"d": {"type": "string", "format": "date"}, "ok": {"type": "boolean"}}}
    assert ff.validate_submission(schema, {"d": "2026-02-30"})[1].keys() == {"d"}
    assert ff.validate_submission(schema, {"d": "2026-02-28", "ok": True}) == ({"d": "2026-02-28", "ok": True}, {})
    assert "ok" in ff.validate_submission(schema, {"ok": "yes"})[1]


def test_a_body_that_is_not_an_object_is_one_error():
    assert ff.validate_submission(SCHEMA, ["a"])[1] == {"": "fields must be an object"}

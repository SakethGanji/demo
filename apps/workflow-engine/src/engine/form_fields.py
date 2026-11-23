"""Shape -> form fields, and server-side validation of a submission (Stage 5, DESIGN-STAGE5-UIS section 4.1).

Pure (no I/O). Shared by the FormTrigger node, the public ``/schema`` + ``/submit`` routes and the studio
preview. The widget mapping is frozen:

* string -> text; ``maxLength > 200`` -> textarea; ``format: email`` -> email; ``format: date`` -> date
* ``enum`` -> select (at most 7 options -> radio)
* number / integer -> number; ``format: currency`` -> currency
* boolean -> checkbox
* array / nested object -> unsupported (omitted from the form; the checker warns WF013)

``fields_ui`` may override ``label`` / ``help`` / ``placeholder`` / ``widget`` per field.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from . import shapes as sh

WIDGETS = ("text", "textarea", "number", "currency", "select", "radio", "checkbox", "email", "date")
DEFAULT_MAX_LENGTH = 4000          # a string field with no maxLength (DESIGN section 3.4)
RADIO_MAX_OPTIONS = 7
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCALAR = ("string", "number", "integer", "boolean")


def humanize(name: str) -> str:
    """``order_id`` -> ``Order id``."""
    text = re.sub(r"[_\-]+", " ", name).strip()
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return text[:1].upper() + text[1:].lower() if text else name


def is_renderable(shape: dict[str, Any] | None) -> bool:
    """A field the form can render: a scalar type (or an enum of scalars)."""
    return isinstance(shape, dict) and shape.get("type") in _SCALAR


def derive_widget(shape: dict[str, Any]) -> str:
    t, fmt = shape.get("type"), shape.get("format")
    if isinstance(shape.get("enum"), list) and shape["enum"]:
        return "radio" if len(shape["enum"]) <= RADIO_MAX_OPTIONS else "select"
    if t == "boolean":
        return "checkbox"
    if t in ("number", "integer"):
        return "currency" if fmt == "currency" else "number"
    if t == "string":
        if fmt == "email":
            return "email"
        if fmt == "date":
            return "date"
        if isinstance(shape.get("maxLength"), int) and shape["maxLength"] > 200:
            return "textarea"
        return "text"
    return "text"


def schema_properties(input_schema: Any) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """``(properties, required)`` of a FormTrigger ``input_schema`` (dict or JSON string); empty when unusable."""
    shape = sh.normalize(input_schema or {}, "declared")
    props = shape.get("properties") if isinstance(shape.get("properties"), dict) else {}
    return dict(props), set(shape.get("required") or [])


def field_order(input_schema: Any, order: list[str] | None = None) -> list[str]:
    """Field names in display order: ``order`` first (known names only), then the rest in schema order."""
    props, _ = schema_properties(input_schema)
    names = list(props)
    first = [n for n in (order or []) if n in props]
    return first + [n for n in names if n not in first]


def unsupported_fields(input_schema: Any) -> list[str]:
    props, _ = schema_properties(input_schema)
    return [n for n, s in props.items() if not is_renderable(s)]


def derive_fields(input_schema: Any, fields_ui: dict[str, Any] | None = None,
                  order: list[str] | None = None) -> list[dict[str, Any]]:
    """The public ``FormField`` list for a schema. Contains only what the form needs (no node internals)."""
    props, required = schema_properties(input_schema)
    ui_all = fields_ui if isinstance(fields_ui, dict) else {}
    out: list[dict[str, Any]] = []
    for name in field_order(input_schema, order):
        shape = props[name]
        if not is_renderable(shape):
            continue
        ui = ui_all.get(name) if isinstance(ui_all.get(name), dict) else {}
        widget = ui.get("widget") if ui.get("widget") in WIDGETS else derive_widget(shape)
        field: dict[str, Any] = {
            "name": name,
            "label": str(ui.get("label") or humanize(name)),
            "widget": widget,
            "required": name in required,
        }
        if ui.get("help"):
            field["help"] = str(ui["help"])
        if ui.get("placeholder"):
            field["placeholder"] = str(ui["placeholder"])
        if isinstance(shape.get("enum"), list) and shape["enum"]:
            field["options"] = [{"value": v, "label": humanize(v) if isinstance(v, str) else str(v)} for v in shape["enum"]]
        for key in ("minimum", "maximum"):
            if isinstance(shape.get(key), (int, float)) and not isinstance(shape.get(key), bool):
                field["min" if key == "minimum" else "max"] = shape[key]
        if shape.get("type") == "string":
            if isinstance(shape.get("minLength"), int):
                field["minLength"] = shape["minLength"]
            field["maxLength"] = shape["maxLength"] if isinstance(shape.get("maxLength"), int) else DEFAULT_MAX_LENGTH
            if isinstance(shape.get("pattern"), str):
                field["pattern"] = shape["pattern"]
        out.append(field)
    return out


# ── server-side validation of a submission ───────────────────────────────────────────────────


def normalize_text(value: str) -> str:
    """NFC-normalise and trim. Values are data: nothing here evaluates ``{{ ... }}``."""
    return unicodedata.normalize("NFC", value).strip()


def validate_submission(input_schema: Any, fields: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a submitted ``fields`` object against the contract.

    Returns ``(clean, errors)``: ``clean`` holds the normalised values of the accepted fields, ``errors`` maps a
    field name (``""`` for the body) to a message. Unknown fields are rejected (``unknown field``). Optional blank
    strings are dropped; a required blank string is an error. Unsupported (array / object) schema fields can't be
    submitted either.
    """
    if not isinstance(fields, dict):
        return {}, {"": "fields must be an object"}
    props, required = schema_properties(input_schema)
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}
    for name, value in fields.items():
        if not isinstance(name, str) or name not in props or not is_renderable(props[name]):
            errors[str(name)] = "unknown field"
            continue
        shape = props[name]
        if isinstance(value, str):
            value = normalize_text(value)
            if value == "":
                if name in required:
                    errors[name] = "this field is required"
                continue                                    # an untouched optional field is simply absent
        clean[name] = value
    for name in props:
        if name in clean or name in errors or name not in required:
            continue
        errors[name] = "this field is required"
    for name, value in list(clean.items()):
        shape = props[name]
        if shape.get("type") == "string" and "maxLength" not in shape and isinstance(value, str) and len(value) > DEFAULT_MAX_LENGTH:
            errors[name] = f"text is longer than {DEFAULT_MAX_LENGTH}"
            continue
        if shape.get("type") == "string" and isinstance(value, str):
            fmt = shape.get("format")
            if fmt == "email" and not _EMAIL_RE.match(value):
                errors[name] = "enter a valid email address"
                continue
            if fmt == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    errors[name] = "enter a date as YYYY-MM-DD"
                    continue
        for v in sh.validate(shape, value, name):
            errors.setdefault(name, v["message"])
    for name in errors:
        clean.pop(name, None)
    return clean, errors

"""Infer JSON Schema descriptors from workflow execution outputs."""

from __future__ import annotations

from typing import Any

_MAX_DEPTH = 20


def infer_json_schema(value: Any, *, _depth: int = 0) -> dict[str, Any]:
    """Walk a JSON value and produce a JSON-Schema-like descriptor.

    Handles the n8n-style item wrapper: [{"json": {...}}, ...].
    Stops recursing beyond _MAX_DEPTH to avoid stack overflow on deeply nested data.
    """
    if _depth > _MAX_DEPTH:
        return {"type": "unknown"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        first = value[0]
        # Unwrap n8n-style [{"json": {...}}] items
        if isinstance(first, dict) and "json" in first and len(first) <= 2:
            return {"type": "array", "items": infer_json_schema(first["json"], _depth=_depth + 1)}
        return {"type": "array", "items": infer_json_schema(first, _depth=_depth + 1)}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: infer_json_schema(v, _depth=_depth + 1) for k, v in value.items()},
        }
    return {"type": "unknown"}


def truncate_sample(output: Any, max_items: int = 3) -> Any:
    """Return a truncated sample suitable for inclusion in an LLM prompt.

    Unwraps n8n-style items so the LLM sees clean objects.
    """
    if isinstance(output, list):
        items = output[:max_items]
        # Unwrap n8n items
        if items and isinstance(items[0], dict) and "json" in items[0]:
            return [item["json"] for item in items]
        return items
    if isinstance(output, dict):
        return {k: truncate_sample(v, max_items) for k, v in output.items()}
    return output

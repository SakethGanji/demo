"""Deterministic example data and mock options for the API catalog (Stage 3A, CONTRACTS-STAGE3A §6).

``example_from_shape(shape, seed)`` is a pure function: the same shape always gives the same value.
Rules: first ``enum`` value; ``minimum`` (or 1) for numbers (clamped to ``maximum``); dates are
``2026-01-01T09:00:00Z`` (``date`` -> ``2026-01-01``); a uuid is ``00000000-0000-4000-8000-000000000001``;
every other string is ``"<field>_example"`` (padded / cut to ``minLength`` / ``maxLength``).
``seed`` is accepted for API stability: today's rules need no randomness, so it doesn't change the result.

``mock_options(operation, seed)`` lists the ``MockOption`` choices for one operation.
"""

from __future__ import annotations

import copy
from http import HTTPStatus
from typing import Any

from ..engine.shapes import Shape

DATETIME_EXAMPLE = "2026-01-01T09:00:00Z"
DATE_EXAMPLE = "2026-01-01"
UUID_EXAMPLE = "00000000-0000-4000-8000-000000000001"
_MAX_DEPTH = 12


def example_from_shape(shape: Shape | None, seed: int = 1, field: str = "value") -> Any:
    return _example(shape, field, 0)


def subworkflow_mock(output: Shape | None, name: str = "") -> tuple[dict[str, Any], str | None]:
    """``(mock, warning)`` for an ExecuteWorkflow node (DESIGN-STAGE8-REUSE section 6.2): one item shaped like the child's declared
    output (``workflow_io.output``). An unknown output keeps ``{items: [{}]}`` plus a warning."""
    if isinstance(output, dict) and output.get("type") == "object" and isinstance(output.get("properties"), dict) and output["properties"]:
        example = example_from_shape(output, 1, "row")
        if isinstance(example, dict):
            return {"items": [example]}, None
    label = f"'{name}' " if name else ""
    return {"items": [{}]}, f"{label}runs a sub-workflow whose output can't be typed; its default mock is one empty item"


def _example(shape: Shape | None, field: str, depth: int) -> Any:
    if not isinstance(shape, dict) or depth > _MAX_DEPTH:
        return None
    if isinstance(shape.get("enum"), list) and shape["enum"]:
        return copy.deepcopy(shape["enum"][0])
    t = shape.get("type", "unknown")
    if t == "object":
        props = shape.get("properties")
        if not isinstance(props, dict):
            return {}
        return {k: _example(v, k, depth + 1) for k, v in props.items()}
    if t == "array":
        items = shape.get("items")
        return [_example(items, field, depth + 1)] if isinstance(items, dict) else []
    if t == "boolean":
        return True
    if t in ("integer", "number"):
        v: Any = shape.get("minimum", 1)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            v = 1
        if isinstance(shape.get("maximum"), (int, float)) and v > shape["maximum"]:
            v = shape["maximum"]
        if t == "integer" and isinstance(v, float):
            v = int(v) if v == int(v) else int(v) + 1
        return v
    if t == "string":
        fmt = shape.get("format")
        if fmt == "date-time":
            return DATETIME_EXAMPLE
        if fmt == "date":
            return DATE_EXAMPLE
        if fmt == "uuid":
            return UUID_EXAMPLE
        s = f"{field}_example"
        lo, hi = shape.get("minLength"), shape.get("maxLength")
        if isinstance(lo, int) and len(s) < lo:
            s = s + "x" * (lo - len(s))
        if isinstance(hi, int) and len(s) > hi:
            s = s[:hi]
        return s
    return None  # null, unknown


# ── mock options ─────────────────────────────────────────────────────────────────────────────


def _phrase(status: int) -> str:
    try:
        p = HTTPStatus(status).phrase
    except ValueError:
        return "Error"
    return p[:1].upper() + p[1:].lower()


def _body(response: dict[str, Any], seed: int) -> Any:
    if response.get("example") is not None:
        return copy.deepcopy(response["example"])
    if isinstance(response.get("shape"), dict):
        return example_from_shape(response["shape"], seed)
    return None


def _http(status: int, body: Any) -> dict[str, Any]:
    http: dict[str, Any] = {"status": status}
    if body is not None:
        http["body"] = body
    return {"http": http}


def mock_options(operation: dict[str, Any], seed: int = 1) -> list[dict[str, Any]]:
    """One option per documented response (2xx ext_success, 4xx/5xx ext_error), then an empty 2xx and a timeout."""
    successes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    first_ok: tuple[int, dict[str, Any]] | None = None
    entries = []
    for key, resp in (operation.get("responses") or {}).items():
        if isinstance(key, str) and key.isdigit() and isinstance(resp, dict):
            entries.append((int(key), resp))
    for status, resp in sorted(entries, key=lambda e: e[0]):
        if 200 <= status < 300:
            successes.append({"label": f"{status} success", "category": "ext_success", "mock": _http(status, _body(resp, seed))})
            if first_ok is None:
                first_ok = (status, resp)
        elif status >= 400:
            errors.append({"label": f"{status} {_phrase(status)}", "category": "ext_error", "mock": _http(status, _body(resp, seed))})
    out = [*successes, *errors]
    if first_ok is not None:
        status, resp = first_ok
        shape = resp.get("shape")
        empty: Any = [] if isinstance(shape, dict) and shape.get("type") == "array" else {}
        out.append({"label": f"{status} empty", "category": "ext_empty", "mock": _http(status, empty)})
    out.append({"label": "Timeout", "category": "ext_timeout", "mock": {"timeout": True}})
    return out


def first_success(operation: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """(status, response) of the first documented 2xx response, or None."""
    entries = sorted(
        ((int(k), v) for k, v in (operation.get("responses") or {}).items() if isinstance(k, str) and k.isdigit() and isinstance(v, dict)),
        key=lambda e: e[0],
    )
    return next(((s, r) for s, r in entries if 200 <= s < 300), None)

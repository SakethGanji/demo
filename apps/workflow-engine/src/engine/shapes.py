"""Shapes: the JSON-Schema subset that describes ONE item's ``json`` (Stage 2, CONTRACTS-STAGE2 §1).

A shape is a plain dict::

    {"type": "object"|"array"|"string"|"integer"|"number"|"boolean"|"null"|"unknown",
     "properties"?: {name: Shape}, "items"?: Shape, "required"?: [str], "enum"?: [...],
     "format"?: str, "nullable"?: bool,
     "minimum"?: number, "maximum"?: number, "minLength"?: int, "maxLength"?: int, "pattern"?: str,
     "x-source"?: "derived"|"declared"|"spec"|"observed"|"unknown"}

Conventions that the checker and the studio rely on:

* An ``object`` shape WITH a ``properties`` key is *closed*: a name that isn't listed definitely
  doesn't exist (``lookup`` returns ``None``).  An ``object`` WITHOUT ``properties`` is *open* (we
  know it's an object, not what's in it): every lookup into it is ``UNKNOWN``.
* ``unknown`` means "can't tell"; it never produces a finding.
* ``x-source`` says where the knowledge came from.  Helpers stamp it on the root of the shape they
  build (``with_source(..., deep=True)`` stamps every level); ``lookup`` hands a child the parent's
  source when the child has none.

Every function here is pure: inputs are never mutated, results never alias the inputs' mutable
parts that were changed.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

Shape = dict[str, Any]
Path = Sequence["str | int"]

TYPES = ("object", "array", "string", "integer", "number", "boolean", "null", "unknown")
SOURCES = ("derived", "declared", "spec", "observed", "unknown")  # "spec" (an API spec) counts like "declared"
SCALARS = ("string", "integer", "number", "boolean", "null")

UNKNOWN: Shape = {"type": "unknown", "x-source": "unknown"}


class NodeContract(TypedDict):
    """What a node takes and gives: ``{"inputs": {port: Shape}, "outputs": {port: Shape}, "error": Shape}``.

    ``error`` is the shape of the optional ``error`` port (``on_error="error_output"``): the input
    item plus ``error: {message, node}``.
    """

    inputs: dict[str, Shape]
    outputs: dict[str, Shape]
    error: Shape

#: Cap on how many sample items ``from_sample`` merges (keeps learn cheap on big outputs).
MAX_SAMPLE_ITEMS = 200

_NUMBER_KEYWORDS = ("minimum", "maximum")
_LENGTH_KEYWORDS = ("minLength", "maxLength")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ── constructors ─────────────────────────────────────────────────────────────────────────────


def unknown() -> Shape:
    """A fresh UNKNOWN shape."""
    return {"type": "unknown", "x-source": "unknown"}


def is_unknown(shape: Shape | None) -> bool:
    return shape is None or shape.get("type", "unknown") == "unknown"


def scalar(type_: str, source: str = "derived", **extra: Any) -> Shape:
    """A scalar/array/object-without-properties shape, e.g. ``scalar("string")``."""
    return {"type": type_, **extra, "x-source": source}


def array_of(items: Shape | None = None, source: str = "derived") -> Shape:
    """``array`` shape; omit ``items`` for an array of unknown elements."""
    out: Shape = {"type": "array", "x-source": source}
    if items is not None:
        out["items"] = copy.deepcopy(items)
    return out


def object_of(
    props: Mapping[str, Shape] | None = None,
    required: Iterable[str] | None = None,
    source: str = "derived",
) -> Shape:
    """A closed object shape. ``required`` defaults to ``[]`` (nothing is guaranteed)."""
    properties = {k: copy.deepcopy(v) for k, v in (props or {}).items()}
    out: Shape = {"type": "object", "properties": properties, "x-source": source}
    req = [r for r in (required or []) if r in properties]
    if req:
        out["required"] = req
    return out


def with_source(shape: Shape, source: str, *, deep: bool = False, only_missing: bool = False) -> Shape:
    """Copy of ``shape`` with ``x-source`` set on the root (every level when ``deep``)."""
    out = copy.deepcopy(shape)

    def stamp(s: Shape) -> None:
        if s.get("type") == "unknown":
            return  # unknown stays unknown, whatever the caller stamps
        if not only_missing or "x-source" not in s:
            s["x-source"] = source
        if deep:
            for child in (s.get("properties") or {}).values():
                if isinstance(child, dict):
                    stamp(child)
            if isinstance(s.get("items"), dict):
                stamp(s["items"])

    stamp(out)
    return out


# ── normalisation of user / JSON-Schema-ish input ────────────────────────────────────────────


def normalize(raw: Any, source: str = "declared") -> Shape:
    """Coerce a loosely-typed schema (a param value, inferred JSON-Schema, a JSON string) into a Shape.

    Anything that isn't understandable becomes UNKNOWN. ``type`` may be a list such as
    ``["string", "null"]`` (the null becomes ``nullable``). Every level is stamped with ``source``.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return unknown()
    out = _normalize(raw, source, 0)
    return out


def _normalize(raw: Any, source: str, depth: int) -> Shape:
    if not isinstance(raw, dict) or depth > 20:
        return {"type": "unknown", "x-source": "unknown"}
    t = raw.get("type")
    nullable = bool(raw.get("nullable", False))
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        nullable = nullable or len(non_null) != len(t)
        t = non_null[0] if len(non_null) == 1 else ("null" if not non_null else "unknown")
    if t not in TYPES:
        # A JSON-Schema without "type" but with properties/items is still understandable.
        t = "object" if "properties" in raw else ("array" if "items" in raw else "unknown")
    if t == "unknown":
        return {"type": "unknown", "x-source": "unknown"}
    out: Shape = {"type": t}
    if t == "object" and isinstance(raw.get("properties"), dict):
        out["properties"] = {str(k): _normalize(v, source, depth + 1) for k, v in raw["properties"].items()}
        req = [r for r in (raw.get("required") or []) if r in out["properties"]] if isinstance(raw.get("required"), list) else []
        if req:
            out["required"] = req
    if t == "array" and isinstance(raw.get("items"), dict) and raw["items"]:
        out["items"] = _normalize(raw["items"], source, depth + 1)
    if isinstance(raw.get("enum"), list):
        out["enum"] = list(raw["enum"])
    if isinstance(raw.get("format"), str):
        out["format"] = raw["format"]
    for key in _NUMBER_KEYWORDS:
        if _is_number(raw.get(key)):
            out[key] = raw[key]
    for key in _LENGTH_KEYWORDS:
        if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if isinstance(raw.get("pattern"), str):
        out["pattern"] = raw["pattern"]
    if nullable:
        out["nullable"] = True
    out["x-source"] = source
    return out


def from_value(value: Any, source: str = "derived") -> Shape:
    """Shape of one concrete JSON value (e.g. a literal in a Set node)."""
    return _from_inferred(_infer(value), source)


def _infer(value: Any) -> Shape:
    from ..services.schema_inference import infer_json_schema

    return infer_json_schema(value)


def _from_inferred(inferred: Any, source: str) -> Shape:
    """infer_json_schema output -> Shape (objects get every seen key as ``required``)."""
    shape = _normalize(inferred, source, 0)

    def mark_required(s: Shape) -> None:
        props = s.get("properties")
        if isinstance(props, dict) and props:
            s["required"] = list(props)
            for child in props.values():
                mark_required(child)
        if isinstance(s.get("items"), dict):
            mark_required(s["items"])

    mark_required(shape)
    return shape


# ── merge ────────────────────────────────────────────────────────────────────────────────────


def merge_shapes(a: Shape | None, b: Shape | None) -> Shape:
    """Lenient union of two shapes.

    * a property on either side is kept (recursively merged when on both sides);
    * ``required`` keeps only names required on both sides;
    * a type conflict becomes ``unknown`` — except ``integer``+``number`` -> ``number`` and
      ``null``+T -> T with ``nullable`` (those are widenings, not conflicts);
    * an open object (no ``properties``) merged with anything object stays open;
    * ``unknown`` absorbs everything (we can't claim anything about the union).
    """
    if a is None and b is None:
        return unknown()
    if a is None:
        return copy.deepcopy(b)  # type: ignore[arg-type]
    if b is None:
        return copy.deepcopy(a)
    ta, tb = a.get("type", "unknown"), b.get("type", "unknown")
    if ta == "unknown" or tb == "unknown":
        return unknown()

    nullable = bool(a.get("nullable")) or bool(b.get("nullable"))
    if ta == "null" and tb == "null":
        return {"type": "null", "x-source": _src(a, b)}
    if ta == "null" or tb == "null":
        other = copy.deepcopy(b if ta == "null" else a)
        other["nullable"] = True
        other["x-source"] = _src(a, b)
        return other
    if ta != tb:
        if {ta, tb} == {"integer", "number"}:
            ta = tb = "number"
        else:
            return unknown()

    out: Shape = {"type": ta}
    if ta == "object":
        pa, pb = a.get("properties"), b.get("properties")
        if isinstance(pa, dict) and isinstance(pb, dict):
            props: dict[str, Shape] = {}
            for name in [*pa, *[k for k in pb if k not in pa]]:
                if name in pa and name in pb:
                    props[name] = merge_shapes(pa[name], pb[name])
                else:
                    props[name] = copy.deepcopy(pa.get(name) or pb[name])
            out["properties"] = props
            req = [r for r in (a.get("required") or []) if r in (b.get("required") or []) and r in props]
            if req:
                out["required"] = req
    elif ta == "array":
        if isinstance(a.get("items"), dict) and isinstance(b.get("items"), dict):
            out["items"] = merge_shapes(a["items"], b["items"])
    if "enum" in a and "enum" in b:
        out["enum"] = [*a["enum"], *[v for v in b["enum"] if v not in a["enum"]]]
    if a.get("format") is not None and a.get("format") == b.get("format"):
        out["format"] = a["format"]
    if nullable:
        out["nullable"] = True
    out["x-source"] = _src(a, b)
    return out


def merge_all(shapes: Iterable[Shape]) -> Shape:
    """Fold ``merge_shapes`` over many shapes; no shapes -> UNKNOWN."""
    result: Shape | None = None
    for s in shapes:
        result = copy.deepcopy(s) if result is None else merge_shapes(result, s)
    return result if result is not None else unknown()


def _src(a: Shape, b: Shape) -> str:
    """The more tentative provenance of the two (unknown < observed < declared < derived)."""
    order = {"unknown": 0, "observed": 1, "declared": 2, "spec": 2, "derived": 3}
    sa, sb = a.get("x-source", "derived"), b.get("x-source", "derived")
    return sa if order.get(sa, 3) <= order.get(sb, 3) else sb


# ── lookup ───────────────────────────────────────────────────────────────────────────────────


def lookup(shape: Shape | None, path: Sequence[str | int]) -> Shape | None:
    """Follow ``path`` through ``shape``.

    Returns the sub-shape; ``None`` when the path DEFINITELY doesn't exist (a closed object without
    that property); ``UNKNOWN`` whenever we can't tell (unknown/open/scalar/array-method access, an
    array index out of what we know, ...). An empty path returns the shape itself.
    """
    if shape is None:
        return unknown()
    cur: Shape = shape
    inherited = shape.get("x-source")
    for seg in path:
        t = cur.get("type", "unknown")
        if t == "object":
            props = cur.get("properties")
            if not isinstance(props, dict):
                return unknown()  # open object
            key = str(seg)
            if key not in props:
                return None
            cur = props[key]
        elif t == "array":
            idx_ok = isinstance(seg, int) or (isinstance(seg, str) and seg.isdigit())
            items = cur.get("items")
            if idx_ok and isinstance(items, dict):
                cur = items
            else:
                return unknown()  # `.length`, methods, unknown element type...
        else:
            return unknown()  # unknown, or property access on a scalar (.length, methods)
        if "x-source" in cur:
            inherited = cur["x-source"]
    if "x-source" not in cur and inherited:
        cur = {**cur, "x-source": inherited}
    return cur


def is_closed_object(shape: Shape | None) -> bool:
    """True for an object whose property list is known."""
    return bool(shape) and shape.get("type") == "object" and isinstance(shape.get("properties"), dict)  # type: ignore[union-attr]


# ── observed samples ─────────────────────────────────────────────────────────────────────────


def from_sample(items: Sequence[Any]) -> Shape:
    """Shape of a list of item-``json`` dicts, via ``services/schema_inference.infer_json_schema``.

    Each item is inferred and the shapes are merged (so a key missing from some items isn't
    ``required``). Stamped ``x-source: "observed"``. No items -> UNKNOWN.
    """
    shapes = [_from_inferred(_infer(i), "observed") for i in list(items)[:MAX_SAMPLE_ITEMS] if i is not None]
    if not shapes:
        return unknown()
    merged = merge_all(shapes)
    return with_source(merged, "observed", deep=True)


# ── contract plumbing ────────────────────────────────────────────────────────────────────────


def input_shape(inputs: Mapping[str, Shape] | None) -> Shape:
    """The item shape a single-input node sees: port ``main``, else the lenient union of all ports.

    A multi-input node (Merge) is fed several ports/connections; their union is what a
    pass-through of "everything received" looks like. No inputs at all -> UNKNOWN.
    """
    if not inputs:
        return unknown()
    if "main" in inputs:
        return copy.deepcopy(inputs["main"])
    return merge_all(inputs.values())


def error_shape(input_: Shape) -> Shape:
    """The ``error`` port item: the input item plus ``error: {message: string, node: string}``.

    Only a closed-object input can be extended without lying; anything else stays UNKNOWN.
    """
    if not is_closed_object(input_):
        return unknown()
    err = object_of({"message": scalar("string"), "node": scalar("string")}, ["message", "node"])
    out = copy.deepcopy(input_)
    out["properties"]["error"] = err
    out["required"] = [*(r for r in out.get("required", []) if r != "error"), "error"]
    out["x-source"] = "derived"
    return out


def make_contract(inputs: Mapping[str, Shape] | None, outputs: Mapping[str, Shape]) -> NodeContract:
    """Assemble a NodeContract; ``error`` is derived from the (main) input shape."""
    return {
        "inputs": {k: copy.deepcopy(v) for k, v in (inputs or {}).items()},
        "outputs": {k: copy.deepcopy(v) for k, v in outputs.items()},
        "error": error_shape(input_shape(inputs)),
    }


def passthrough_contract(inputs: Mapping[str, Shape] | None, ports: Sequence[str] = ("main",)) -> NodeContract:
    """Every port in ``ports`` carries the input item unchanged."""
    shape = input_shape(inputs)
    return make_contract(inputs, {p: copy.deepcopy(shape) for p in ports})


def param_shape(params: Mapping[str, Any] | None, *keys: str) -> Shape | None:
    """A Shape stored in a node parameter (dict or JSON string), first of ``keys`` that is present.

    Returns ``None`` when no key holds a usable, non-unknown shape — callers then fall back to their
    default. The result is stamped ``declared`` at every level.
    """
    for key in keys:
        raw = (params or {}).get(key)
        if raw in (None, "", {}):
            continue
        shape = normalize(raw, "declared")
        if not is_unknown(shape):
            return shape
    return None


_JSON_PATH_EXPR = re.compile(r"^\s*\{\{\s*\$json((?:\.[A-Za-z_][A-Za-z0-9_]*)+)\s*\}\}\s*$")


def expression_value_shape(value: Any, item: Shape) -> Shape:
    """Best-effort shape of a node-parameter value that may be an expression.

    * a literal string -> ``string``; bool/int/float/None/list/dict -> their own shape
      (strings nested in them that hold ``{{ }}`` become UNKNOWN);
    * exactly ``{{ $json.a.b }}`` -> the shape of ``a.b`` in ``item`` (UNKNOWN if not known);
    * any other expression -> UNKNOWN.
    """
    if isinstance(value, str):
        if "{{" not in value:
            return scalar("string")
        m = _JSON_PATH_EXPR.match(value)
        if m:
            found = lookup(item, m.group(1).strip(".").split("."))
            return unknown() if found is None else found
        return unknown()
    if isinstance(value, dict):
        return object_of({str(k): expression_value_shape(v, item) for k, v in value.items()}, list(map(str, value)))
    if isinstance(value, list):
        return array_of(merge_all(expression_value_shape(v, item) for v in value) if value else None)
    return from_value(value)


# ── validation of concrete values ────────────────────────────────────────────────────────────


class Violation(TypedDict):
    """One place where a value breaks a shape (``path`` is dotted with ``[i]``, ``""`` = the root)."""

    path: str
    expected: Any
    actual: Any
    message: str


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_ok(expected: str, value: Any) -> bool:
    if expected == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if expected == "number":
        return _is_number(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "null":
        return value is None
    return True


def _join(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def validate(shape: Shape | None, value: Any, path: str = "") -> list[Violation]:
    """Check one concrete value against a shape; returns every violation (empty = valid).

    ``unknown`` shapes (and a missing shape) accept anything. Extra object properties are allowed; a
    ``required`` property that is missing, a wrong type, an ``enum`` miss and a keyword miss
    (minimum/maximum/minLength/maxLength/pattern) are violations. ``nullable`` allows null.
    """
    out: list[Violation] = []
    _validate(shape, value, path, out, 0)
    return out


def _validate(shape: Shape | None, value: Any, path: str, out: list[Violation], depth: int) -> None:
    if is_unknown(shape) or depth > 30:
        return
    assert shape is not None
    t = shape.get("type", "unknown")
    if value is None:
        if t != "null" and not shape.get("nullable"):
            out.append({"path": path, "expected": t, "actual": "null", "message": f"expected {t}, got null"})
        return
    if not _type_ok(t, value):
        out.append({"path": path, "expected": t, "actual": _type_name(value), "message": f"expected {t}, got {_type_name(value)}"})
        return
    if isinstance(shape.get("enum"), list) and value not in shape["enum"]:
        out.append({"path": path, "expected": shape["enum"], "actual": value, "message": f"value is not one of {shape['enum']}"})
    if t in ("integer", "number"):
        if _is_number(shape.get("minimum")) and value < shape["minimum"]:
            out.append({"path": path, "expected": f">= {shape['minimum']}", "actual": value, "message": f"{value} is below the minimum {shape['minimum']}"})
        if _is_number(shape.get("maximum")) and value > shape["maximum"]:
            out.append({"path": path, "expected": f"<= {shape['maximum']}", "actual": value, "message": f"{value} is above the maximum {shape['maximum']}"})
    elif t == "string":
        if isinstance(shape.get("minLength"), int) and len(value) < shape["minLength"]:
            out.append({"path": path, "expected": f"length >= {shape['minLength']}", "actual": len(value), "message": f"text is shorter than {shape['minLength']}"})
        if isinstance(shape.get("maxLength"), int) and len(value) > shape["maxLength"]:
            out.append({"path": path, "expected": f"length <= {shape['maxLength']}", "actual": len(value), "message": f"text is longer than {shape['maxLength']}"})
        if isinstance(shape.get("pattern"), str):
            try:
                ok = re.search(shape["pattern"], value) is not None
            except re.error:
                ok = True  # an unusable pattern never fails a run
            if not ok:
                out.append({"path": path, "expected": f"matches {shape['pattern']}", "actual": value, "message": f"text does not match {shape['pattern']}"})
    elif t == "object":
        props = shape.get("properties") if isinstance(shape.get("properties"), dict) else {}
        for name in shape.get("required") or []:
            if name not in value:
                out.append({"path": _join(path, name), "expected": "required", "actual": "missing", "message": f"required property '{name}' is missing"})
        for name, child in props.items():
            if name in value:
                _validate(child, value[name], _join(path, name), out, depth + 1)
    elif t == "array" and isinstance(shape.get("items"), dict):
        for i, el in enumerate(value):
            _validate(shape["items"], el, _join(path, i), out, depth + 1)

"""Flattening a remote JSON Schema into something four LLM providers accept.

The engine's tool pipeline (``src/engine/tool_schema.py``) is not a general
JSON-Schema processor. It is a handful of shallow rewrites, and several of them
are actively hostile to the schemas real servers publish:

* ``ensure_complete_schema`` calls ``schema.get("type", "").lower()``. A
  ``"type": ["string", "null"]`` — which is how half the world spells "optional
  string" — raises ``AttributeError`` before any provider is reached.
* ``harden_schema(provider="openai")`` *strips* ``$ref``/``anyOf``/``oneOf``
  keys rather than resolving them, so a property whose entire definition lived
  behind a ``$ref`` arrives at the model as ``{}`` — no type, no description.
  ``validate_tool_definition`` then warns, and the model guesses.
* Nothing resolves ``$defs``. A schema that references one and does not carry
  it is unusable by every provider.

So everything must be resolved *here*, once, at discovery time, and the result
stored. What survives is the intersection all four providers understand:
objects, arrays, scalars, ``enum``, ``description``, ``required`` — and a
plain-string ``type`` on every single node.

The analytics MCP server is the worst realistic case and the reason for each
rule below: FastMCP renders every optional parameter as
``{"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}``, hangs a
``title`` on every node (Gemini rejects ``title``), and puts enums behind
``$ref`` into ``$defs``.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any
from urllib.parse import unquote

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_CHARS = 12_000
DEFAULT_MAX_ENUM = 40
DEFAULT_MAX_DESCRIPTION = 600

#: The only keys allowed through. Anything absent from this set either has no
#: meaning to a provider or is actively rejected by one of them ($schema, $id,
#: title, examples, patternProperties, not/if/then/else, discriminator...).
_KEEP = frozenset({
    "type", "description", "enum", "properties", "required", "items",
    "default", "format", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems",
    "pattern",
})

_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})
_ALL_TYPES = _SCALAR_TYPES | {"object", "array"}


class SchemaFlattenError(ValueError):
    """The input was not a JSON Schema we can make sense of at all."""


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------

def _pointer(root: dict[str, Any], ref: str) -> Any | None:
    """Resolve a local JSON pointer (``#/components/schemas/Foo``)."""
    if not ref.startswith("#"):
        # Remote refs would mean a second fetch, from a URL the connector's
        # egress policy never approved. Refused, not followed.
        return None
    path = ref[1:].lstrip("/")
    node: Any = root
    if not path:
        return root
    for raw in path.split("/"):
        token = unquote(raw).replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            if token not in node:
                return None
            node = node[token]
        else:
            return None
    return node


def _deref(node: dict[str, Any], root: dict[str, Any], seen: frozenset[str]) -> tuple[dict[str, Any], frozenset[str]]:
    """Follow ``$ref`` chains. Returns the target and the updated cycle set."""
    hops = 0
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or ref in seen or hops > 20:
            # A cycle. Real: OpenAPI documents describe recursive trees, and a
            # naive resolver here loops until the stack dies.
            return {"type": "object", "description": "(recursive structure)"}, seen
        target = _pointer(root, ref)
        seen = seen | {ref}
        hops += 1
        if not isinstance(target, dict):
            return {"type": "string", "description": f"(unresolved reference {ref})"}, seen
        # Sibling keys alongside $ref (description, default) win over the target's.
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        node = {**target, **siblings}
    return node if isinstance(node, dict) else {}, seen


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def _is_null(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _merge_all_of(node: dict[str, Any], root: dict[str, Any], seen: frozenset[str]) -> dict[str, Any]:
    """Fold ``allOf`` branches into their parent. Later branches lose ties."""
    merged: dict[str, Any] = {k: v for k, v in node.items() if k != "allOf"}
    for branch in node.get("allOf") or []:
        if not isinstance(branch, dict):
            continue
        branch, seen = _deref(branch, root, seen)
        branch = _merge_all_of(branch, root, seen) if "allOf" in branch else branch
        for key, value in branch.items():
            if key == "properties" and isinstance(value, dict):
                props = dict(value)
                props.update(merged.get("properties") or {})
                merged["properties"] = props
            elif key == "required" and isinstance(value, list):
                merged["required"] = list(dict.fromkeys(list(merged.get("required") or []) + value))
            elif key not in merged:
                merged[key] = value
    return merged


def _branch_score(node: dict[str, Any]) -> int:
    """Rank ``anyOf`` branches so the informative one wins.

    A branch with properties says more than a bare ``{"type": "object"}``, and
    any typed branch says more than an untyped one.
    """
    score = 0
    if node.get("properties"):
        score += 4
    if node.get("enum"):
        score += 3
    if isinstance(node.get("type"), str):
        score += 2
    if node.get("items"):
        score += 1
    return score


def _collapse_union(
    node: dict[str, Any], key: str, root: dict[str, Any], seen: frozenset[str]
) -> tuple[dict[str, Any], bool, list[str]]:
    """Collapse ``anyOf``/``oneOf`` to one branch. Returns (node, nullable, dropped)."""
    branches = [b for b in (node.get(key) or []) if isinstance(b, dict)]
    resolved = []
    for branch in branches:
        deref, _ = _deref(branch, root, seen)
        resolved.append(deref)

    nullable = any(_is_null(b) for b in resolved)
    live = [b for b in resolved if not _is_null(b)]
    if not live:
        # `anyOf: [{"type": "null"}]` — degenerate, but real in generated specs.
        return {"type": "string"}, True, []

    best = max(live, key=_branch_score)
    dropped = [
        str(b.get("type") or "object") for b in live if b is not best
    ]
    rest = {k: v for k, v in node.items() if k not in (key, "anyOf", "oneOf")}
    # The chosen branch's own keys win; the parent contributes description etc.
    return {**rest, **best}, nullable, dropped


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def _infer_type(node: dict[str, Any]) -> str:
    if node.get("properties") is not None or node.get("additionalProperties") is not None:
        return "object"
    if node.get("items") is not None:
        return "array"
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        first = next((e for e in enum if e is not None), None)
        if isinstance(first, bool):
            return "boolean"
        if isinstance(first, int):
            return "integer"
        if isinstance(first, float):
            return "number"
        return "string"
    return "string"


def _normalize_type(node: dict[str, Any]) -> tuple[str, bool]:
    """Return a plain-string type and whether ``null`` was one of the options.

    Never a list. ``ensure_complete_schema`` crashes on a list, and Gemini's
    ``_normalize_schema_types`` would uppercase list members into nonsense.
    """
    raw = node.get("type")
    nullable = False
    if isinstance(raw, list):
        options = [t for t in raw if isinstance(t, str)]
        nullable = "null" in options
        options = [t for t in options if t != "null"]
        raw = options[0] if options else None
    if isinstance(raw, str):
        raw = raw.lower()
        if raw == "null":
            return "string", True
        if raw in _ALL_TYPES:
            return raw, nullable
        # "integer64", "str", vendor types...
        if raw.startswith("int"):
            return "integer", nullable
        if raw in ("float", "double", "decimal"):
            return "number", nullable
        if raw in ("bool",):
            return "boolean", nullable
        if raw in ("dict", "map"):
            return "object", nullable
        if raw in ("list", "tuple", "set"):
            return "array", nullable
        return "string", nullable
    return _infer_type(node), nullable


def _describe(node: dict[str, Any], extra: list[str], max_description: int) -> str | None:
    parts: list[str] = []
    text = node.get("description") or node.get("title")
    if isinstance(text, str) and text.strip():
        parts.append(text.strip())
    parts.extend(extra)
    if not parts:
        return None
    joined = " ".join(parts)
    if len(joined) > max_description:
        joined = joined[: max_description - 1].rstrip() + "…"
    return joined


def _walk(
    node: Any,
    root: dict[str, Any],
    *,
    depth: int,
    max_depth: int,
    max_enum: int,
    max_description: int,
    seen: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {"type": "string"}
    if node is True:  # pragma: no cover - JSON Schema boolean form
        return {"type": "string"}

    node, seen = _deref(node, root, seen)
    if "allOf" in node:
        node = _merge_all_of(node, root, seen)

    notes: list[str] = []
    nullable = False
    for union_key in ("anyOf", "oneOf"):
        if union_key in node:
            node, was_null, dropped = _collapse_union(node, union_key, root, seen)
            nullable = nullable or was_null
            if dropped:
                notes.append(f"(may also be given as: {', '.join(sorted(set(dropped)))})")
            node, seen = _deref(node, root, seen)
            if "allOf" in node:
                node = _merge_all_of(node, root, seen)

    kind, type_nullable = _normalize_type(node)
    nullable = nullable or type_nullable

    out: dict[str, Any] = {"type": kind}

    # Scalar constraints worth keeping verbatim.
    for key in ("format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                "minLength", "maxLength", "minItems", "maxItems", "pattern"):
        if key in node and isinstance(node[key], (str, int, float, bool)):
            out[key] = node[key]

    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        values = [e for e in enum if e is not None]
        if len(values) > max_enum:
            notes.append(f"({len(values)} allowed values, {max_enum} shown)")
            values = values[:max_enum]
        if values:
            out["enum"] = values
        if len(values) < len(enum):
            nullable = True

    default = node.get("default")
    if default is not None:
        out["default"] = default

    if kind == "object":
        props = node.get("properties")
        if depth >= max_depth:
            # Deeper than any provider will usefully render. Keep the node but
            # stop describing it, rather than emitting a half-tree.
            if props:
                notes.append("(nested object; fields omitted for depth)")
            out["properties"] = {}
        else:
            flat_props: dict[str, Any] = {}
            if isinstance(props, dict):
                for prop_name, prop_def in props.items():
                    # Property names are NEVER normalized. The remote server's
                    # argument guard matches them literally; a "helpful" rename
                    # here surfaces as an unexplained model failure.
                    flat_props[str(prop_name)] = _walk(
                        prop_def, root,
                        depth=depth + 1, max_depth=max_depth, max_enum=max_enum,
                        max_description=max_description, seen=seen,
                    )
            out["properties"] = flat_props
        required = [
            r for r in (node.get("required") or [])
            if isinstance(r, str) and r in out["properties"]
        ]
        if required:
            out["required"] = required
    elif kind == "array":
        items = node.get("items")
        if isinstance(items, list):  # tuple validation
            items = items[0] if items else None
        if depth >= max_depth or not isinstance(items, dict):
            out["items"] = {"type": "string"}
        else:
            out["items"] = _walk(
                items, root,
                depth=depth + 1, max_depth=max_depth, max_enum=max_enum,
                max_description=max_description, seen=seen,
            )

    if nullable:
        notes.append("(optional; omit rather than sending null)")

    description = _describe(node, notes, max_description)
    if description:
        out["description"] = description

    return {k: v for k, v in out.items() if k in _KEEP}


# ---------------------------------------------------------------------------
# Size control
# ---------------------------------------------------------------------------

def _size(schema: dict[str, Any]) -> int:
    return len(json.dumps(schema, default=str))


def _shrink_text(node: Any, limit: int) -> None:
    if isinstance(node, dict):
        desc = node.get("description")
        if isinstance(desc, str) and len(desc) > limit:
            node["description"] = desc[: limit - 1].rstrip() + "…"
        enum = node.get("enum")
        if isinstance(enum, list) and len(enum) > 10:
            node["enum"] = enum[:10]
        for value in node.values():
            _shrink_text(value, limit)
    elif isinstance(node, list):
        for item in node:
            _shrink_text(item, limit)


def _enforce_size(schema: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Bring a schema under *max_chars*, sacrificing the least useful parts first.

    Descriptions and long enums go first; only then are optional properties
    dropped, largest first. A required property is never dropped — a schema
    missing one produces calls that cannot succeed, which is worse than a big
    schema.
    """
    if _size(schema) <= max_chars:
        return schema

    _shrink_text(schema, 160)
    if _size(schema) <= max_chars:
        return schema

    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema
    required = set(schema.get("required") or [])
    droppable = sorted(
        (name for name in props if name not in required),
        key=lambda n: _size(props[n]) if isinstance(props[n], dict) else 0,
        reverse=True,
    )
    dropped: list[str] = []
    for name in droppable:
        if _size(schema) <= max_chars:
            break
        props.pop(name, None)
        dropped.append(name)
    if dropped:
        logger.warning(
            "connector schema exceeded %d chars; dropped optional properties: %s",
            max_chars, ", ".join(dropped),
        )
    return schema


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def flatten_schema(
    schema: Any,
    *,
    root: dict[str, Any] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_enum: int = DEFAULT_MAX_ENUM,
    max_description: int = DEFAULT_MAX_DESCRIPTION,
) -> dict[str, Any]:
    """Return an object schema containing no ``$ref``, ``$defs``, ``anyOf``,
    ``oneOf``, ``allOf`` or list-valued ``type``, and a string ``type`` on every
    node.

    Args:
        schema: the remote schema (an MCP ``inputSchema`` or an OpenAPI node).
        root: the document ``$ref`` pointers resolve against. Defaults to
            *schema* itself, which is right for MCP (``$defs`` are inline).
    """
    if schema is None:
        return {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        raise SchemaFlattenError(f"schema must be an object, got {type(schema).__name__}")

    document = root if isinstance(root, dict) else schema
    flat = _walk(
        deepcopy(schema), document,
        depth=0, max_depth=max_depth, max_enum=max_enum,
        max_description=max_description, seen=frozenset(),
    )

    # A tool's arguments are always an object, whatever the remote said.
    if flat.get("type") != "object":
        flat = {"type": "object", "properties": {}, "description": flat.get("description", "")}
        flat = {k: v for k, v in flat.items() if v not in (None, "")}
        flat.setdefault("properties", {})
    flat.setdefault("properties", {})

    return _enforce_size(flat, max_chars)


def optional_args(schema: dict[str, Any]) -> list[str]:
    """Property names the remote server did NOT mark required.

    Captured at discovery, from the pristine schema. By the time a tool reaches
    a provider, ``harden_schema(provider="openai")`` has overwritten
    ``required`` with *every* property name, so this is the last honest record
    of which arguments the model was free to omit.
    """
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = {r for r in (schema.get("required") or []) if isinstance(r, str)}
    return [name for name in props if name not in required]


def schema_issues(schema: Any, *, path: str = "$") -> list[str]:
    """Every reason *schema* would still break a provider. Empty means clean."""
    issues: list[str] = []
    if isinstance(schema, dict):
        for banned in ("$ref", "$defs", "definitions", "anyOf", "oneOf", "allOf",
                       "patternProperties", "not", "if", "then", "else", "$schema"):
            if banned in schema:
                issues.append(f"{path}: contains {banned}")
        node_type = schema.get("type")
        if node_type is not None and not isinstance(node_type, str):
            issues.append(f"{path}: type is {type(node_type).__name__}, must be a string")
        elif isinstance(node_type, str) and node_type not in _ALL_TYPES:
            issues.append(f"{path}: unknown type {node_type!r}")
        props = schema.get("properties")
        if isinstance(props, dict):
            for name, value in props.items():
                if isinstance(value, dict) and "type" not in value:
                    issues.append(f"{path}.{name}: missing type")
                issues.extend(schema_issues(value, path=f"{path}.{name}"))
        items = schema.get("items")
        if isinstance(items, dict):
            issues.extend(schema_issues(items, path=f"{path}[]"))
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            issues.extend(schema_issues(item, path=f"{path}[{index}]"))
    return issues

"""OpenAPI 3.0 / 3.1 import for the API catalog (Stage 3A, CONTRACTS-STAGE3A §6).

``parse_spec_text(text)`` reads JSON or YAML; ``import_openapi(doc)`` normalises the document into::

    {"title": str, "base_url": str, "operations": [Operation]}
    Operation = {operation_id, method, path, summary, path_params: [str], request_shape: Shape | None,
                 responses: {"<status>": {description, shape: Shape | None, example: Any | None}}}

Local ``$ref`` (``#/...``) are resolved (a cycle collapses to UNKNOWN); any other ``$ref`` is refused.
Schemas become Shapes (``x-source: "spec"``) carrying enum, format, nullable, required and the
minimum / maximum / minLength / maxLength / pattern keywords. Swagger 2 documents are refused.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import yaml

from ..engine.shapes import Shape

SWAGGER2_MESSAGE = "Swagger 2 isn't supported; convert to OpenAPI 3"
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
MAX_DEPTH = 24
_KEYWORDS = ("minimum", "maximum", "minLength", "maxLength", "pattern")
_PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")


class SpecError(ValueError):
    """The document can't be imported; the message says why (the API turns it into a 422)."""


# ── parsing ──────────────────────────────────────────────────────────────────────────────────


class _PlainLoader(yaml.SafeLoader):
    """SafeLoader that leaves timestamps as strings (specs are JSON documents in YAML clothing)."""


_PlainLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_spec_text(text: str) -> Any:
    if not isinstance(text, str) or not text.strip():
        raise SpecError("The spec is empty")
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(text)
        except ValueError as e:
            raise SpecError(f"The spec isn't valid JSON: {e}") from e
    try:
        loaded = yaml.load(text, Loader=_PlainLoader)  # noqa: S506 - SafeLoader subclass
        return json.loads(json.dumps(loaded, default=str))  # plain JSON types only (int keys -> str)
    except yaml.YAMLError as e:
        raise SpecError(f"The spec isn't valid JSON or YAML: {str(e).splitlines()[0] if str(e) else 'parse error'}") from e


def import_openapi(doc: Any) -> dict[str, Any]:
    """Normalise an OpenAPI 3 document (dict, or JSON/YAML text). Raises ``SpecError``."""
    if isinstance(doc, str):
        doc = parse_spec_text(doc)
    if not isinstance(doc, dict):
        raise SpecError("The spec must be a JSON or YAML object")
    if "swagger" in doc:
        raise SpecError(SWAGGER2_MESSAGE)
    version = doc.get("openapi")
    if not isinstance(version, str):
        raise SpecError("Not an OpenAPI document: the 'openapi' version field is missing")
    if not (version.startswith("3.0") or version.startswith("3.1")):
        raise SpecError(f"OpenAPI {version} isn't supported; use OpenAPI 3.0 or 3.1")
    _refuse_remote_refs(doc)
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("The spec has no 'paths' object")

    ctx = _Ctx(doc)
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, item in paths.items():
        item = ctx.deref(item)
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        shared = [ctx.deref(p) for p in (item.get("parameters") or [])]
        for method in METHODS:
            op = ctx.deref(item.get(method))
            if not isinstance(op, dict):
                continue
            operations.append(_operation(ctx, method.upper(), path, op, shared, seen))
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    return {"title": str(info.get("title") or ""), "base_url": _base_url(doc), "operations": operations}


# ── $ref handling ────────────────────────────────────────────────────────────────────────────


def _refuse_remote_refs(node: Any, depth: int = 0) -> None:
    if depth > 200:
        return
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            raise SpecError(f"Remote $ref isn't supported: {ref}")
        for v in node.values():
            _refuse_remote_refs(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _refuse_remote_refs(v, depth + 1)


class _Ctx:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc

    def lookup_ref(self, ref: str) -> Any:
        if ref == "#":
            return self.doc
        if not ref.startswith("#/"):
            raise SpecError(f"Remote $ref isn't supported: {ref}")
        cur: Any = self.doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                raise SpecError(f"Unresolvable $ref: {ref}")
        return cur

    def deref(self, node: Any, _seen: tuple[str, ...] = ()) -> Any:
        """Follow a chain of ``$ref`` at this node (not into children). A cycle returns None."""
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if ref in _seen:
                return None
            _seen = (*_seen, ref)
            node = self.lookup_ref(ref)
        return node


# ── operations ───────────────────────────────────────────────────────────────────────────────


def _base_url(doc: dict[str, Any]) -> str:
    servers = doc.get("servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], dict):
        return ""
    url = str(servers[0].get("url") or "")
    for name, var in (servers[0].get("variables") or {}).items():
        if isinstance(var, dict) and "default" in var:
            url = url.replace("{" + str(name) + "}", str(var["default"]))
    return url.rstrip("/")


def _json_media(ctx: _Ctx, content: Any) -> dict[str, Any] | None:
    content = ctx.deref(content)
    if not isinstance(content, dict) or not content:
        return None
    for mime, media in content.items():
        if "json" in str(mime).lower():
            media = ctx.deref(media)
            return media if isinstance(media, dict) else None
    media = ctx.deref(next(iter(content.values())))
    return media if isinstance(media, dict) else None


def _example_of(ctx: _Ctx, media: dict[str, Any]) -> Any:
    if "example" in media:
        return copy.deepcopy(media["example"])
    examples = ctx.deref(media.get("examples"))
    if isinstance(examples, dict):
        for ex in examples.values():
            ex = ctx.deref(ex)
            if isinstance(ex, dict) and "value" in ex:
                return copy.deepcopy(ex["value"])
    schema = ctx.deref(media.get("schema"))
    if isinstance(schema, dict) and "example" in schema:
        return copy.deepcopy(schema["example"])
    return None


def _operation(ctx: _Ctx, method: str, path: str, op: dict[str, Any], shared: list[Any], seen: set[str]) -> dict[str, Any]:
    op_id = op.get("operationId")
    operation_id = op_id if isinstance(op_id, str) and op_id.strip() else f"{method} {path}"
    if operation_id in seen:  # operationId must be unique; keep ours unique so refs can't collide
        operation_id = f"{method} {path}"
    seen.add(operation_id)

    params = [p for p in (*shared, *[ctx.deref(p) for p in (op.get("parameters") or [])]) if isinstance(p, dict)]
    path_params = [str(p["name"]) for p in params if p.get("in") == "path" and p.get("name")]
    for name in _PATH_PARAM_RE.findall(path):
        if name not in path_params:
            path_params.append(name)
    path_params = list(dict.fromkeys(path_params))

    request_shape = None
    body = ctx.deref(op.get("requestBody"))
    if isinstance(body, dict):
        media = _json_media(ctx, body.get("content"))
        if media and media.get("schema") is not None:
            request_shape = schema_to_shape(media["schema"], ctx)

    responses: dict[str, Any] = {}
    raw_responses = ctx.deref(op.get("responses"))
    for status, resp in (raw_responses.items() if isinstance(raw_responses, dict) else []):
        resp = ctx.deref(resp)
        if not isinstance(resp, dict):
            continue
        media = _json_media(ctx, resp.get("content"))
        shape = schema_to_shape(media["schema"], ctx) if media and media.get("schema") is not None else None
        responses[str(status)] = {
            "description": str(resp.get("description") or ""),
            "shape": shape,
            "example": _example_of(ctx, media) if media else None,
        }
    summary = op.get("summary") or op.get("description") or ""
    return {
        "operation_id": operation_id, "method": method, "path": path, "summary": str(summary).strip().split("\n")[0],
        "path_params": path_params, "request_shape": request_shape, "responses": responses,
    }


# ── JSON Schema -> Shape ─────────────────────────────────────────────────────────────────────


def schema_to_shape(schema: Any, ctx: _Ctx | dict[str, Any] | None = None, source: str = "spec") -> Shape:
    """Convert a JSON Schema (OpenAPI 3.0/3.1 flavour) into a Shape. Never raises for odd schemas."""
    if isinstance(ctx, dict):
        ctx = _Ctx(ctx)
    return _shape(schema, ctx or _Ctx({}), source, 0, ())


def _unknown() -> Shape:
    return {"type": "unknown", "x-source": "unknown"}


def _shape(schema: Any, ctx: _Ctx, source: str, depth: int, refs: tuple[str, ...]) -> Shape:
    if depth > MAX_DEPTH:
        return _unknown()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if ref in refs:  # recursive schema: stop here
            return _unknown()
        refs = (*refs, ref)
        schema = ctx.lookup_ref(ref)
    if not isinstance(schema, dict):
        return _unknown()

    nullable = bool(schema.get("nullable", False))

    if isinstance(schema.get("allOf"), list) and schema["allOf"]:
        parts = [_shape(s, ctx, source, depth + 1, refs) for s in schema["allOf"]]
        merged = _merge_all_of(parts, {k: v for k, v in schema.items() if k != "allOf"}, ctx, source, depth, refs)
        if nullable:
            merged["nullable"] = True
        return merged
    for key in ("oneOf", "anyOf"):
        variants = schema.get(key)
        if isinstance(variants, list) and variants:
            non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
            has_null = len(non_null) != len(variants)
            if len(non_null) == 1:
                inner = _shape(non_null[0], ctx, source, depth + 1, refs)
                if (has_null or nullable) and inner.get("type") != "unknown":
                    inner["nullable"] = True
                return inner
            return _unknown()

    t = schema.get("type")
    if isinstance(t, list):  # OpenAPI 3.1: ["string", "null"]
        non_null = [x for x in t if x != "null"]
        nullable = nullable or len(non_null) != len(t)
        t = non_null[0] if len(non_null) == 1 else ("null" if not non_null else None)
    if "const" in schema and "enum" not in schema:
        schema = {**schema, "enum": [schema["const"]]}
    if t not in ("object", "array", "string", "integer", "number", "boolean", "null"):
        t = "object" if "properties" in schema else ("array" if "items" in schema else None)
    if t is None:
        return _unknown()

    out: Shape = {"type": t}
    if t == "object":
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            out["properties"] = {str(k): _shape(v, ctx, source, depth + 1, refs) for k, v in props.items()}
            req = schema.get("required")
            names = [r for r in (req if isinstance(req, list) else []) if r in out["properties"]]
            if names:
                out["required"] = names
    elif t == "array":
        if isinstance(schema.get("items"), dict) and schema["items"]:
            out["items"] = _shape(schema["items"], ctx, source, depth + 1, refs)
    if isinstance(schema.get("enum"), list):
        out["enum"] = list(schema["enum"])
    if isinstance(schema.get("format"), str):
        out["format"] = schema["format"]
    for kw in _KEYWORDS:
        v = schema.get(kw)
        if v is None or isinstance(v, bool):
            continue
        if kw == "pattern" and isinstance(v, str):
            out[kw] = v
        elif kw != "pattern" and isinstance(v, (int, float)):
            out[kw] = v
    if nullable:
        out["nullable"] = True
    out["x-source"] = source
    return out


def _merge_all_of(parts: list[Shape], own: dict[str, Any], ctx: _Ctx, source: str, depth: int, refs: tuple[str, ...]) -> Shape:
    if "properties" in own or "type" in own or "enum" in own:
        parts = [*parts, _shape(own, ctx, source, depth + 1, refs)]
    parts = [p for p in parts if p.get("type") != "unknown"]
    if not parts:
        return _unknown()
    objects = [p for p in parts if p.get("type") == "object"]
    if len(objects) != len(parts):
        return copy.deepcopy(parts[0])
    out: Shape = {"type": "object"}
    props: dict[str, Shape] = {}
    required: list[str] = []
    for p in objects:
        for k, v in (p.get("properties") or {}).items():
            props[k] = v
        required.extend(r for r in p.get("required", []) if r not in required)
    if props:
        out["properties"] = props
        req = [r for r in required if r in props]
        if req:
            out["required"] = req
    out["x-source"] = source
    return out


def required_all(shape: Shape) -> Shape:
    """Mark every property of every object in ``shape`` required (an example shows what a reply carries)."""
    shape = copy.deepcopy(shape)

    def walk(s: Shape) -> None:
        props = s.get("properties")
        if isinstance(props, dict) and props:
            s["required"] = list(props)
            for c in props.values():
                walk(c)
        if isinstance(s.get("items"), dict):
            walk(s["items"])

    walk(shape)
    return shape

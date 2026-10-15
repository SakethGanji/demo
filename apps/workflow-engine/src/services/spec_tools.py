"""OpenAPI generation, spec diff and request validation for project API routes (§3.5, G13, G34)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
AUTH_RANK = {"none": 0, "app_token": 1, "api_key": 2}


def route_label(spec: dict[str, Any]) -> str:
    return f"{spec['method']} {spec['path']}"


# ── OpenAPI ──────────────────────────────────────────────────────────────────


def _sub(schema: dict | None, key: str) -> dict | None:
    if not isinstance(schema, dict):
        return None
    sub = (schema.get("properties") or {}).get(key)
    return sub if isinstance(sub, dict) else None


def build_openapi(project: Any, routes: list[dict[str, Any]], revision: int | None = None) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for r in sorted(routes, key=lambda x: (x["path"], x["method"])):
        params_schema = _sub(r.get("request_schema"), "params") or {}
        query_schema = _sub(r.get("request_schema"), "query") or {}
        body_schema = _sub(r.get("request_schema"), "body")
        parameters = []
        for name in PARAM_RE.findall(r["path"]):
            parameters.append({"name": name, "in": "path", "required": True,
                               "schema": (params_schema.get("properties") or {}).get(name) or {"type": "string"}})
        q_required = set(query_schema.get("required") or [])
        for name, sch in (query_schema.get("properties") or {}).items():
            parameters.append({"name": name, "in": "query", "required": name in q_required, "schema": sch})
        op: dict[str, Any] = {
            "operationId": r["name"],
            "summary": r.get("description") or route_label(r),
            "x-route-id": r["id"],
            "x-handler": (r.get("handler") or {}).get("type"),
            "parameters": parameters,
            "responses": {"200": {"description": "Success", "content": {
                "application/json": {"schema": r.get("response_schema") or {}}}}},
        }
        if body_schema is not None:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": body_schema}}}
        if r.get("auth") == "api_key":
            op["security"] = [{"apiKey": []}]
        elif r.get("auth") == "app_token":
            op["security"] = [{"appToken": []}, {"apiKey": []}]
        else:
            op["security"] = []
        paths.setdefault(r["path"], {})[r["method"].lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {"title": getattr(project, "name", "Project API"),
                 "version": str(revision) if revision is not None else "draft"},
        "servers": [{"url": f"/api/p/{getattr(project, 'slug', '')}"}],
        "paths": paths,
        "components": {"securitySchemes": {
            "apiKey": {"type": "http", "scheme": "bearer", "description": "Project API key (sk_<env>_...)"},
            "appToken": {"type": "http", "scheme": "bearer", "description": "Published-app or preview token"},
        }},
    }


def spec_hash(spec: dict[str, Any]) -> str:
    body = {k: v for k, v in spec.items() if k != "info"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ── Diff ─────────────────────────────────────────────────────────────────────


def _ops(spec: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out = {}
    for path, methods in ((spec or {}).get("paths") or {}).items():
        for m, op in methods.items():
            rid = op.get("x-route-id") or f"{m}:{path}"
            out[rid] = {"method": m.upper(), "path": path, "op": op}
    return out


def _auth_of(op: dict[str, Any]) -> str:
    sec = op.get("security") or []
    names = {k for s in sec for k in s}
    if names == {"apiKey"}:
        return "api_key"
    if "appToken" in names:
        return "app_token"
    return "none"


def _req_schema(op: dict[str, Any]) -> dict[str, Any]:
    """Re-assemble {params, query, body} from an operation for field-level diffs."""
    props: dict[str, Any] = {}
    for loc, key in (("path", "params"), ("query", "query")):
        ps = [p for p in op.get("parameters") or [] if p.get("in") == loc]
        if ps:
            props[key] = {"type": "object", "properties": {p["name"]: p.get("schema") or {} for p in ps},
                          "required": [p["name"] for p in ps if p.get("required")]}
    body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    if body is not None:
        props["body"] = body
    return {"type": "object", "properties": props}


def _resp_schema(op: dict[str, Any]) -> dict[str, Any]:
    return (((op.get("responses") or {}).get("200") or {}).get("content") or {}).get("application/json", {}).get("schema") or {}


def _walk(old: Any, new: Any, prefix: str, side: str, out: list[tuple[str, str, bool]]) -> None:
    """Field-level changes: (kind, field, breaking). side = 'request' | 'response'."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    ot, nt = old.get("type"), new.get("type")
    if ot and nt and ot != nt and prefix:
        out.append((f"{side}_field_type_changed", prefix, True))
        return
    if (ot or "object") == "array" and (nt or "object") == "array":
        _walk(old.get("items"), new.get("items"), prefix + "[]", side, out)
        return
    op, np_ = old.get("properties") or {}, new.get("properties") or {}
    oreq, nreq = set(old.get("required") or []), set(new.get("required") or [])
    for k in sorted(set(op) | set(np_)):
        f = f"{prefix}.{k}" if prefix else k
        if k in op and k not in np_:
            out.append(("response_field_removed" if side == "response" else "request_field_removed", f,
                        side == "response"))
        elif k not in op and k in np_:
            if side == "request" and k in nreq:
                out.append(("request_required_added", f, True))
            else:
                out.append(("field_added", f, False))
        else:
            if side == "request" and k in nreq and k not in oreq:
                out.append(("request_required_added", f, True))
            _walk(op[k], np_[k], f, side, out)


def diff_specs(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    o, n = _ops(old), _ops(new)
    changes: list[dict[str, Any]] = []

    def ch(kind: str, rec: dict, breaking: bool, field: str | None = None, **extra) -> None:
        changes.append({"kind": kind, "route": f"{rec['method']} {rec['path']}", "operation": rec["op"].get("operationId"),
                        "route_id": rec["op"].get("x-route-id"), "field": field, "breaking": breaking,
                        "affected": [], **extra})

    for rid in sorted(set(o) - set(n)):
        ch("route_removed", o[rid], True)
    for rid in sorted(set(n) - set(o)):
        ch("route_added", n[rid], False)
    for rid in sorted(set(o) & set(n)):
        a, b = o[rid], n[rid]
        if a["method"] != b["method"]:
            ch("method_changed", b, True, before=a["method"], after=b["method"])
        if a["path"] != b["path"]:
            ch("path_changed", b, True, before=a["path"], after=b["path"])
        if a["op"].get("operationId") != b["op"].get("operationId"):
            ch("operation_renamed", b, True, before=a["op"].get("operationId"), after=b["op"].get("operationId"))
        aa, ba = _auth_of(a["op"]), _auth_of(b["op"])
        if aa != ba:
            ch("auth_changed", b, AUTH_RANK[ba] > AUTH_RANK[aa], before=aa, after=ba)
        fields: list[tuple[str, str, bool]] = []
        _walk(_req_schema(a["op"]), _req_schema(b["op"]), "", "request", fields)
        _walk(_resp_schema(a["op"]), _resp_schema(b["op"]), "", "response", fields)
        for kind, field, breaking in fields:
            ch(kind, b, breaking, field=field)
    return changes


# ── Request validation (validate_request, decision 4) ───────────────────────


_PY_TYPES = {"string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,),
             "object": (dict,), "array": (list,), "null": (type(None),)}


def validate(value: Any, schema: dict[str, Any] | None, path: str = "") -> list[dict[str, str]]:
    """Minimal JSON-Schema check: type, required, properties, items, enum."""
    errors: list[dict[str, str]] = []
    if not isinstance(schema, dict) or not schema:
        return errors
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for tt in types:
            py = _PY_TYPES.get(tt)
            if py and isinstance(value, py) and not (tt in ("number", "integer") and isinstance(value, bool)):
                ok = True
        if not ok:
            errors.append({"path": path or "$", "message": f"expected {t}"})
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path or "$", "message": f"must be one of {schema['enum']}"})
    if isinstance(value, dict):
        for k in schema.get("required") or []:
            if k not in value:
                errors.append({"path": f"{path}.{k}" if path else k, "message": "is required"})
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errors.extend(validate(value[k], sub, f"{path}.{k}" if path else k))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            errors.extend(validate(v, schema["items"], f"{path}[{i}]"))
    return errors

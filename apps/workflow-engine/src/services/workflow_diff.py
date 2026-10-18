"""Pure diff of two workflow definitions (Stage 6, DESIGN-STAGE6-AI-BUILDER section 4.9).

A Python port of the studio's ``features/workflow-editor/lib/workflowDiff.ts``: a node is "changed" when its type,
parameters, disabled flag, on_error or label differ (key order and position are ignored; the legacy
``continue_on_fail`` means ``on_error = "continue"``). ``diff_nodes`` is the exact port (the two must agree on the
shared fixtures); ``diff_definitions`` adds connections, tests, the interface and the bank's review flags.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

_CODE_KEYS = ("code", "jsCode", "pythonCode", "script")


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _on_error(n: dict[str, Any]) -> str:
    v = n.get("on_error")
    if v is None:
        v = n.get("onError")
    if v is None:
        v = "continue" if n.get("continue_on_fail") else "stop"
    return v


def node_fingerprint(n: dict[str, Any]) -> str:
    """Comparable view of a node: everything except position (same fields as the TS ``fingerprint``)."""
    return _stable({
        "type": n.get("type"), "parameters": n.get("parameters") or {}, "disabled": bool(n.get("disabled")),
        "onError": _on_error(n), "label": n.get("label"),
    })


def _nodes(defn: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {n["name"]: n for n in (defn or {}).get("nodes") or [] if isinstance(n, dict) and n.get("name")}


def diff_nodes(base: dict[str, Any] | None, target: dict[str, Any] | None) -> dict[str, list[str]]:
    """``{added, removed, changed}`` node names; ``added``/``changed`` in the target's order, ``removed`` in the base's."""
    b, t = _nodes(base), _nodes(target)
    added = [name for name in t if name not in b]
    changed = [name for name, node in t.items() if name in b and node_fingerprint(b[name]) != node_fingerprint(node)]
    removed = [name for name in b if name not in t]
    return {"added": added, "removed": removed, "changed": changed}


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Which parts of a node differ: ``type``, ``disabled``, ``on_error``, ``label``, ``parameters.<top-level key>``."""
    out: list[str] = []
    if before.get("type") != after.get("type"):
        out.append("type")
    bp, ap = before.get("parameters") or {}, after.get("parameters") or {}
    if isinstance(bp, dict) and isinstance(ap, dict):
        for k in sorted(set(bp) | set(ap)):
            if _stable(bp.get(k, _MISSING)) != _stable(ap.get(k, _MISSING)):
                out.append(f"parameters.{k}")
    elif _stable(bp) != _stable(ap):
        out.append("parameters")
    if bool(before.get("disabled")) != bool(after.get("disabled")):
        out.append("disabled")
    if _on_error(before) != _on_error(after):
        out.append("on_error")
    if before.get("label") != after.get("label"):
        out.append("label")
    return out


_MISSING = "\u0000missing"


def _conn_key(c: dict[str, Any]) -> tuple[str, str, str, str]:
    return (c.get("source_node"), c.get("source_output", "main"), c.get("target_node"), c.get("target_input", "main"))  # type: ignore[return-value]


def _conns(defn: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    seen: dict[tuple[str, str, str, str], None] = {}
    for c in (defn or {}).get("connections") or []:
        if isinstance(c, dict):
            seen.setdefault(_conn_key(c), None)
    return list(seen)


_URL_RE = re.compile(r"^(https?)://([^/\s{}?#]+)", re.IGNORECASE)


def origin_of(url: Any) -> str | None:
    """``scheme://host[:port]`` of a literal URL; None for anything else (an expression, a relative path, an api_ref)."""
    if not isinstance(url, str):
        return None
    m = _URL_RE.match(url.strip())
    if not m:
        return None
    try:
        parts = urlsplit(m.group(0))
        if not parts.hostname:
            return None
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    except ValueError:
        return None


def destinations_of(defn: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    for n in _nodes(defn).values():
        if n.get("type") != "HttpRequest":
            continue
        o = origin_of((n.get("parameters") or {}).get("url"))
        if o:
            out.add(o)
    return out


def _code_of(n: dict[str, Any]) -> str | None:
    params = n.get("parameters") or {}
    for k in _CODE_KEYS:
        if isinstance(params.get(k), str) and params[k].strip():
            return params[k]
    return None


def _tests_by_id(defn: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in (defn or {}).get("tests") or [] if isinstance(t, dict) and t.get("id")}


def diff_definitions(base: dict[str, Any] | None, target: dict[str, Any] | None) -> dict[str, Any]:
    """The proposal diff of section 4.9. ``base`` None means a new workflow (everything is added)."""
    base = base or {"nodes": [], "connections": [], "tests": []}
    target = target or {"nodes": [], "connections": [], "tests": []}
    nd = diff_nodes(base, target)
    bn, tn = _nodes(base), _nodes(target)
    bc, tc = _conns(base), _conns(target)
    bset, tset = set(bc), set(tc)
    bt, tt = _tests_by_id(base), _tests_by_id(target)
    new_code = [
        name for name, n in tn.items()
        if (code := _code_of(n)) is not None and (name not in bn or _code_of(bn[name]) != code)
    ]
    return {
        "nodes": {
            "added": nd["added"], "removed": nd["removed"],
            "changed": [{"name": name, "fields": changed_fields(bn[name], tn[name])} for name in nd["changed"]],
        },
        "connections": {
            "added": [list(c) for c in tc if c not in bset],
            "removed": [list(c) for c in bc if c not in tset],
        },
        "tests": {
            "added": [i for i in tt if i not in bt],
            "changed": [i for i in tt if i in bt and _stable(tt[i]) != _stable(bt[i])],
            "removed": [i for i in bt if i not in tt],
        },
        "interface": {"changed": _stable(base.get("interface")) != _stable(target.get("interface"))},
        "new_destinations": sorted(destinations_of(target) - destinations_of(base)),
        "new_code": new_code,
        "counts": {"added": len(nd["added"]), "removed": len(nd["removed"]), "changed": len(nd["changed"])},
    }

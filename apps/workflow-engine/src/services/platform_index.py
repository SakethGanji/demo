"""Platform index: the parts of Studio the AI builders write against, as exact lookups (PLAN-BUILDER section 3, WP-1).

Three global, in-memory catalogues, built lazily and cached by content:
  * SDK symbols   - ``sdk.generate.reference()`` (the same source as the studio's SDK reference).
  * kit components - ``services/app_kit/*.ts(x)``; props are parsed from the TypeScript types, not hand-written.
  * templates     - ``templates/*/template.json``.

``search_*`` rank by token overlap (name > use-when text > props); ``get_*`` are exact. ``get_table_schema`` and
``get_route_signature`` are per-project and read the project knowledge store, falling back to the live tables.
``TOOLS`` are Anthropic-format tool schemas and ``dispatch_tool`` executes one, so a builder can expose them as-is.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .tsx_parser import parse_tsx_file

_KIT_DIR = Path(__file__).resolve().parent / "app_kit"
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_STOP = {"a", "an", "the", "with", "and", "or", "of", "to", "for", "in", "on", "by", "is", "it", "that", "this", "from", "as",
         "at", "be", "can", "me", "my", "i", "show", "make", "want", "need", "use", "get"}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


# ── tokens and scoring ───────────────────────────────────────────────────────


def _stem(w: str) -> str:
    w = w.lower()
    for suf in ("ing", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list[str]:
    """Lower-case stemmed words; camelCase and snake_case are split (``DataTable`` -> data, table)."""
    out: list[str] = []
    for w in _WORD.findall(text or ""):
        for part in re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", w).split():
            p = part.lower()
            if p not in _STOP and len(p) > 1:
                out.append(_stem(p))
    return out


def _score(query: str, fields: list[tuple[str, float]], name: str = "") -> float:
    q = tokens(query)
    if not q:
        return 0.0
    total = 0.0
    for text, weight in fields:
        toks = set(tokens(text))
        total += weight * sum(1 for t in q if t in toks)
    if name:
        nl = name.lower()
        if query.strip().lower() == nl:
            total += 100
        elif nl in query.lower():
            total += 6
    return total


def _top(items: list[tuple[float, dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    items = [i for i in items if i[0] > 0]
    items.sort(key=lambda i: (-i[0], i[1]["name"]))
    return [{**d, "score": round(s, 2)} for s, d in items[:limit]]


# ── kit components (props parsed from the .tsx types) ───────────────────────


def _match_close(src: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at ``start`` (strings and comments are skipped); -1 if unbalanced."""
    depth, i, n = 0, start, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2)
            if i < 0:
                return -1
            i += 1
        elif src.startswith("//", i):
            nl = src.find("\n", i)
            i = n if nl < 0 else nl
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_members(body: str) -> list[tuple[str, str]]:
    """Split an interface/object-type body into ``(doc, member_text)`` at top-level newlines, ``;`` and ``,``."""
    members: list[tuple[str, str]] = []
    depth, cur, doc, i, n = 0, [], "", 0, len(body)

    def flush() -> None:
        nonlocal cur, doc
        text = "".join(cur).strip()
        if text:
            members.append((doc, text))
            doc = ""
        cur = []

    while i < n:
        c = body[i]
        if body.startswith("/*", i):
            end = body.find("*/", i + 2)
            end = n - 2 if end < 0 else end
            if not "".join(cur).strip():
                doc = " ".join(re.sub(r"^\s*\*+\s?", "", ln).strip() for ln in body[i + 2:end].splitlines()).strip()
            i = end + 2
            continue
        if body.startswith("//", i):
            nl = body.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and body[j] != c:
                j += 2 if body[j] == "\\" else 1
            cur.append(body[i:j + 1])
            i = j + 1
            continue
        if c in "({[<":
            depth += 1
        elif c in ")}]>" and not (c == ">" and i > 0 and body[i - 1] == "="):
            depth -= 1
        if depth == 0 and c in "\n;,":
            nxt = body[i + 1:].lstrip()
            if c == "\n" and (nxt.startswith("|") or nxt.startswith("&") or "".join(cur).rstrip().endswith(("|", "&", "=>"))):
                cur.append(" ")
            else:
                flush()
        else:
            cur.append(" " if c == "\n" else c)
        i += 1
    flush()
    return members


_MEMBER = re.compile(r"^(?:readonly\s+)?([A-Za-z_$][\w$]*|'[^']+'|\"[^\"]+\")(\?)?\s*:\s*(.+)$", re.S)


def _members_to_props(body: str, defaults: dict[str, str]) -> list[dict[str, Any]]:
    props = []
    for doc, text in _split_members(body):
        m = _MEMBER.match(text)
        if not m:
            continue
        name = m.group(1).strip("'\"")
        prop = {"name": name, "type": re.sub(r"\s+", " ", m.group(3)).strip(), "required": not m.group(2), "doc": doc}
        if name in defaults:
            prop["default"] = defaults[name]
        props.append(prop)
    return props


def _type_body(src: str, name: str) -> str | None:
    m = re.search(rf"(?:interface\s+{re.escape(name)}\b|type\s+{re.escape(name)}\b)[^{{=]*(?:=\s*)?\{{", src)
    if not m:
        return None
    start = m.end() - 1
    end = _match_close(src, start, "{", "}")
    return src[start + 1:end] if end > 0 else None


def parse_component_props(src: str, name: str) -> list[dict[str, Any]]:
    """Props of the component/function ``name`` in ``src``: named ``Props`` type, or the inline ``({...}: {...})`` type."""
    m = re.search(rf"(?:function\s+{re.escape(name)}\b|const\s+{re.escape(name)}\b)", src)
    if not m:
        return []
    p = src.find("(", m.end())
    if p < 0:
        return []
    between = src[m.end():p]
    if "=>" in between or ";" in between:
        return []
    q = _match_close(src, p, "(", ")")
    if q < 0:
        return []
    params = src[p + 1:q].strip()
    defaults: dict[str, str] = {}
    type_text = ""
    if params.startswith("{"):
        end = _match_close(params, 0, "{", "}")
        destructure = params[1:end]
        for dm in re.finditer(r"([A-Za-z_$][\w$]*)\s*=\s*('[^']*'|\"[^\"]*\"|[^,}\s][^,}]*)", destructure):
            defaults[dm.group(1)] = dm.group(2).strip()
        rest = params[end + 1:].lstrip()
        type_text = rest[1:].strip() if rest.startswith(":") else ""
    else:
        pm = re.match(r"[A-Za-z_$][\w$]*\s*:\s*(.+)$", params, re.S)
        type_text = pm.group(1).strip() if pm else ""
    props: list[dict[str, Any]] = []
    for part in _split_intersection(type_text):
        if part.startswith("{"):
            end = _match_close(part, 0, "{", "}")
            if end > 0:
                props += _members_to_props(part[1:end], defaults)
            continue
        im = re.match(r"([A-Za-z_$][\w$]*)\s*(?:<.*)?$", part, re.S)
        body = _type_body(src, im.group(1)) if im else None
        if body is not None:
            props += _members_to_props(body, defaults)
    return props


def _split_intersection(type_text: str) -> list[str]:
    """``A & { ... } & B`` -> parts, splitting only at top-level ``&``."""
    parts, depth, cur = [], 0, []
    for i, c in enumerate(type_text):
        if c in "({[<":
            depth += 1
        elif c in ")}]>" and not (c == ">" and type_text[i - 1:i] == "="):
            depth -= 1
        if c == "&" and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _example(name: str, props: list[dict[str, Any]]) -> str:
    req = [p["name"] for p in props if p["required"] and p["name"] != "children"]
    has_children = any(p["name"] == "children" for p in props)
    attrs = "".join(f" {r}={{...}}" for r in req)
    return f"<{name}{attrs}>...</{name}>" if has_children else f"<{name}{attrs} />"


@lru_cache(maxsize=1)
def _kit_cached(stamp: tuple) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for path in sorted(_KIT_DIR.iterdir()):
        if path.suffix not in (".ts", ".tsx"):
            continue
        src = path.read_text(encoding="utf-8")
        parsed = parse_tsx_file(src)
        file_doc = parsed.get("file_comment") or ""
        for d in parsed.get("definitions", []):
            if not d.get("exported") or d["kind"] not in ("component", "hook", "function"):
                continue
            props = parse_component_props(src, d["name"]) if d["kind"] == "component" else []
            items.append({
                "name": d["name"], "kind": d["kind"], "file": f"kit/{path.name}", "signature": d.get("signature") or "",
                "use_when": d.get("doc") or "", "file_doc": file_doc, "props": props,
                "example": _example(d["name"], props) if d["kind"] == "component" else "",
                "import": f"import {{ {d['name']} }} from './kit'",
            })
    return tuple(items)


def kit_components() -> list[dict[str, Any]]:
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in sorted(_KIT_DIR.iterdir()) if p.suffix in (".ts", ".tsx"))
    return list(_kit_cached(stamp))


def search_components(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Kit components ranked for a request such as "table with filters"; each hit carries its props."""
    scored = []
    for c in kit_components():
        prop_text = " ".join(f"{p['name']} {p['doc']}" for p in c["props"])
        s = _score(query, [(c["name"], 5.0), (c["use_when"], 3.0), (c["file_doc"], 1.0), (prop_text, 1.5)], c["name"])
        scored.append((s, c))
    return _top(scored, limit)


def get_component(name: str) -> dict[str, Any] | None:
    n = (name or "").strip().lower()
    return next((c for c in kit_components() if c["name"].lower() == n), None)


def kit_index_lines() -> list[str]:
    """One line per kit component (name - use-when): the generated replacement for a hand-written prop list."""
    return [f"{c['name']}: {(c['use_when'] or c['file_doc']).splitlines()[0][:120]}" for c in kit_components()
            if c["kind"] == "component"]


# ── SDK symbols ───────────────────────────────────────────────────────────────


def sdk_symbols() -> list[dict[str, Any]]:
    from ..sdk.generate import reference

    ref = reference()
    out = [dict(e) for e in ref["entries"]]
    out += [{"name": f"rule:{k}", "kind": "rule", "signature": "", "doc": v, "params": []} for k, v in ref["rules"].items()]
    return out


def search_sdk(query: str, limit: int = 6) -> list[dict[str, Any]]:
    scored = []
    for e in sdk_symbols():
        params = " ".join(str(p.get("name", "")) for p in e.get("params") or [])
        scored.append((_score(query, [(e["name"], 5.0), (e.get("doc", ""), 2.0), (params, 1.0)], e["name"]), e))
    return _top(scored, limit)


def get_sdk_symbol(name: str) -> dict[str, Any] | None:
    """Exact (case-insensitive) match; a trailing part is enough when unambiguous (``jobs.start``, ``db.table``)."""
    n = (name or "").strip().lower().removeprefix("studio.")
    syms = sdk_symbols()
    for e in syms:
        if e["name"].lower().removeprefix("studio.") == n:
            return e
    hits = [e for e in syms if e["name"].lower().removeprefix("studio.").endswith("." + n) or e["name"].lower().endswith("." + n)]
    return hits[0] if len(hits) == 1 else None


# ── templates ─────────────────────────────────────────────────────────────────


def _cut_readme(text: str, cap: int = 2000) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n[truncated: {len(text) - cap} more characters; the full readme is in the template folder]"


@lru_cache(maxsize=1)
def _templates_cached(stamp: tuple) -> tuple[dict[str, Any], ...]:
    items = []
    for d in sorted(_TEMPLATES_DIR.iterdir()) if _TEMPLATES_DIR.is_dir() else []:
        f = d / "template.json"
        if not f.is_file():
            continue
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        readme = d / "README.md"
        items.append({"name": meta.get("slug") or d.name, "title": meta.get("name") or d.name, "summary": meta.get("summary") or "",
                      "category": meta.get("category"), "tags": meta.get("tags") or [],
                      "requires": {k: [(x.get("name") or x.get("slug")) if isinstance(x, dict) else str(x) for x in v]
                                   for k, v in (meta.get("requires") or {}).items() if isinstance(v, list)},
                      "readme": _cut_readme(readme.read_text(encoding="utf-8")) if readme.is_file() else ""})
    return tuple(items)


def templates() -> list[dict[str, Any]]:
    stamp = tuple((d.name, (d / "template.json").stat().st_mtime_ns) for d in sorted(_TEMPLATES_DIR.iterdir())
                  if (d / "template.json").is_file()) if _TEMPLATES_DIR.is_dir() else ()
    return list(_templates_cached(stamp))


def search_templates(query: str, limit: int = 5) -> list[dict[str, Any]]:
    scored = [(_score(query, [(t["name"], 4.0), (t["title"], 4.0), (t["summary"], 3.0), (" ".join(t["tags"]), 3.0),
                               (t["readme"], 0.5)], t["name"]), t) for t in templates()]
    return _top(scored, limit)


def get_template(name: str) -> dict[str, Any] | None:
    n = (name or "").strip().lower()
    return next((t for t in templates() if t["name"].lower() == n), None)


# ── per-project exact getters (read the knowledge store) ─────────────────────


async def get_table_schema(session, project_id: str, name: str) -> dict[str, Any] | None:
    """The columns of a project table: the knowledge row when indexed, else straight from ``data_tables``."""
    from . import project_knowledge as pk

    row = await pk.get(session, project_id, "table_schema", name)
    if row:
        return row["body"]
    from sqlalchemy import select

    from ..db.models import DataTableModel

    t = (await session.execute(select(DataTableModel).where(DataTableModel.project_id == project_id,
                                                              DataTableModel.name == name))).scalars().first()
    return {"name": t.name, "description": t.description, "columns": t.columns} if t else None


async def get_route_signature(session, project_id: str, method: str, path: str) -> dict[str, Any] | None:
    """Request/response fields of ``METHOD /path`` (path params may be named differently: ``{id}`` equals ``{order_id}``)."""
    from . import project_knowledge as pk

    method, path = _str(method) or "GET", _str(path)
    want = f"{method.upper()} {_shape(path)}"
    for row in await pk.list_kind(session, project_id, "route"):
        if f"{row['body'].get('method')} {_shape(row['body'].get('path', ''))}" == want:
            return row["body"]
    return None


def _str(v: Any) -> str:
    """Tolerate None / list / number where a string is expected (model-supplied tool args are untrusted)."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        return " ".join(_str(x) for x in v)
    return v if isinstance(v, str) else str(v)


def _str_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple, set)):
        return [_str(x) for x in v if x]
    return [_str(v)]


def _shape(path: Any) -> str:
    path = _str(path)
    return re.sub(r"\{[^}/]*\}|:[A-Za-z_]\w*", "{}", path or "").rstrip("/") or "/"


# ── tool schemas for the builders ─────────────────────────────────────────────


def _tool(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


_Q = {"type": "string", "description": "what you are looking for, in plain words"}
TOOLS: list[dict[str, Any]] = [
    _tool("search_components", "Find Studio UI kit components for a need (e.g. 'table with filters'). Returns props.", {"query": _Q}, ["query"]),
    _tool("get_component", "Exact kit component: props with types and defaults, import line and a minimal example.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("search_sdk", "Find studio SDK functions (api.*, studio.db.*, studio.jobs.*, studio.secret ...) for a need.", {"query": _Q}, ["query"]),
    _tool("get_sdk_symbol", "Exact SDK symbol: signature, parameters and doc, e.g. 'studio.jobs.start' or 'db.table'.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("get_table_schema", "Columns (name, type, required, default) of one project data table.", {"name": {"type": "string"}}, ["name"]),
    _tool("get_route_signature", "Request and response fields of one project route.",
          {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "path": {"type": "string"}},
          ["method", "path"]),
    _tool("search_templates", "Find reusable backend/workflow templates for a need.", {"query": _Q}, ["query"]),
    _tool("get_template", "One template by name, with its summary, requirements and readme.", {"name": {"type": "string"}}, ["name"]),
    _tool("search_project_knowledge", "Search this project's recorded requirements, decisions, answers, tables, routes, screens and past failures.",
          {"query": _Q, "kinds": {"type": "array", "items": {"type": "string"}}}, ["query"]),
]
TOOL_NAMES = [t["name"] for t in TOOLS]


async def dispatch_tool(session, project_id: str, name: str, args: dict[str, Any]) -> Any:
    """Run one of ``TOOLS``. Unknown names, misses and malformed args return an ``{"error": ...}`` dict (the model
    reads it), never raise. Batched args (a list of arg objects, or ``method``/``path``/``name`` given as lists)
    return a list with one result per item."""
    if isinstance(args, str):  # a model sometimes sends the one expected value bare
        key = {"get_route_signature": "path", "search_project_knowledge": "query"}.get(name, "query" if name.startswith("search_") else "name")
        args = {key: args}
    if isinstance(args, (list, tuple)):
        return [await dispatch_tool(session, project_id, name, a) for a in args[:12]]
    if args is not None and not isinstance(args, dict):
        return {"error": f"arguments for {name} must be an object, got {type(args).__name__}"}
    args = args or {}
    for plural in ("routes", "items", "requests"):
        if isinstance(args.get(plural), list) and len(args) == 1:
            return [await dispatch_tool(session, project_id, name, a) for a in args[plural][:12]]
    if name == "get_route_signature" and isinstance(args.get("path"), list):
        methods = args.get("method") if isinstance(args.get("method"), list) else [args.get("method")] * len(args["path"])
        return [await dispatch_tool(session, project_id, name, {"method": m, "path": pth})
                for m, pth in list(zip(methods, args["path"]))[:12]]
    if name in ("get_component", "get_sdk_symbol", "get_table_schema", "get_template") and isinstance(args.get("name"), list):
        return [await dispatch_tool(session, project_id, name, {"name": n}) for n in args["name"][:12]]
    try:
        if name in ("search_components", "search_sdk", "search_templates", "search_project_knowledge"):
            if not _str(args.get("query")).strip() and name != "search_project_knowledge":
                return {"error": f"{name} needs a non-empty 'query' string (plain words describing what you need)"}
        if name in ("get_component", "get_sdk_symbol", "get_table_schema", "get_template") and not _str(args.get("name")).strip():
            return {"error": f"{name} needs a 'name' string"}
        if name == "get_route_signature" and not _str(args.get("path")).strip():
            return {"error": "get_route_signature needs 'method' (GET/POST/...) and 'path' (e.g. /orders/{id}); "
                             "for several routes call it once per route"}
        if name == "search_components":
            return search_components(_str(args.get("query")))
        if name == "get_component":
            n = _str(args.get("name"))
            return get_component(n) or {"error": f"no kit component named {n!r}"}
        if name == "search_sdk":
            return search_sdk(_str(args.get("query")))
        if name == "get_sdk_symbol":
            n = _str(args.get("name"))
            return get_sdk_symbol(n) or {"error": f"no SDK symbol named {n!r}"}
        if name == "search_templates":
            return search_templates(_str(args.get("query")))
        if name == "get_template":
            n = _str(args.get("name"))
            return get_template(n) or {"error": f"no template named {n!r}"}
        if name == "get_table_schema":
            n = _str(args.get("name"))
            return await get_table_schema(session, project_id, n) or {"error": f"no table named {n!r}"}
        if name == "get_route_signature":
            m, pth = _str(args.get("method")) or "GET", _str(args.get("path"))
            return await get_route_signature(session, project_id, m, pth) or {"error": f"no route {m} {pth}"}
        if name == "search_project_knowledge":
            from . import project_knowledge as pk

            return await pk.retrieve(session, project_id, _str(args.get("query")), kinds=_str_list(args.get("kinds")) or None)
    except Exception as e:  # a lookup must never break the builder's loop
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": f"unknown tool {name!r}"}

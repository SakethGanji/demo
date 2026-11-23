"""Project knowledge store: durable, searchable facts about one project, for the AI builders (PLAN-BUILDER sections 3-5, WP-1).

Rows are ``(project_id, kind, key)``-unique and carry a ``text`` rendering that Postgres indexes (stored tsvector + GIN).
Two writers:
  * design/human/failure rows (requirements, decisions, answers, failures ...) through :func:`upsert`;
  * code-derived rows (tables, routes, screens, kit use) rebuilt from the project's own files by :func:`index_tables`,
    :func:`index_module`, :func:`index_app` and :func:`reindex_project`. They are idempotent: re-running over unchanged
    code changes nothing (no version bump), and rows for code that disappeared are pruned.

:func:`retrieve` is the read side: ranked, filtered by kind, and never over its token budget.
"""

from __future__ import annotations

import ast
import json
import math
import re
from typing import Any

from sqlalchemy import select, text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.knowledge_models import KINDS, SOURCES, STATUSES
from ..utils.ids import generate_id

CODE_KINDS = ("table_schema", "route", "screen", "component_use")
DEFAULT_BUDGET = 1500
# Relevance prior per kind: facts about what to call or decide outrank per-file usage bookkeeping.
KIND_WEIGHT = {"component_use": 0.35, "screen": 0.7, "table_schema": 0.9}
TRUNCATION_MARKER = "[truncated: {n} more matching rows omitted; narrow the query or raise the budget]"
_SNIPPET_CUT = " …[cut]"
_COLS = "id, project_id, kind, key, title, body, text, status, version, source, source_ref, created_at, updated_at"


class KnowledgeError(ValueError):
    pass


def est_tokens(s: str) -> int:
    """The same rough rule the plan uses (chars / 4), rounded up."""
    return math.ceil(len(s) / 4)


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    if isinstance(d.get("body"), str):  # raw text() queries may hand JSONB back undecoded
        d["body"] = json.loads(d["body"])
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
    return d


def render_text(title: str, body: Any) -> str:
    """Searchable text for a row: the title, then every string/number leaf of the body (keys included for dict bodies)."""
    parts: list[str] = [title] if title else []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                parts.append(str(k))
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            parts.append(str(v))

    walk(body)
    return " ".join(parts)


# ── repository ───────────────────────────────────────────────────────────────


async def upsert(session: AsyncSession, project_id: str, kind: str, key: str, *, title: str = "", body: dict[str, Any] | None = None,
                 text: str | None = None, status: str | None = None, source: str = "human", source_ref: str | None = None,
                 commit: bool = True) -> dict[str, Any]:
    """Create or update the row ``(project_id, kind, key)``.

    ``version`` starts at 1 and bumps only when title, body, text or status really change, so re-running an indexer over
    unchanged code is a no-op. ``status=None`` keeps the existing status (``draft`` for a new row). Returns the row plus
    ``changed`` (``created`` / ``updated`` / ``unchanged``)."""
    if kind not in KINDS:
        raise KnowledgeError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if status is not None and status not in STATUSES:
        raise KnowledgeError(f"unknown status {status!r}; one of {', '.join(STATUSES)}")
    if source not in SOURCES:
        raise KnowledgeError(f"unknown source {source!r}; one of {', '.join(SOURCES)}")
    key = (key or "").strip()
    if not key or len(key) > 500:
        raise KnowledgeError("key must be 1-500 characters")
    body = body or {}
    text_ = text if text is not None else render_text(title, body)
    body_json = json.dumps(body, sort_keys=True, default=str)
    cur = (await session.execute(sql(f"SELECT {_COLS} FROM project_knowledge WHERE project_id=:p AND kind=:k AND key=:key FOR UPDATE"),
                                 {"p": project_id, "k": kind, "key": key})).first()
    if cur is None:
        res = await session.execute(sql(
            f"INSERT INTO project_knowledge (id, project_id, kind, key, title, body, text, status, source, source_ref) "
            f"VALUES (:id, :p, :k, :key, :title, CAST(:body AS jsonb), :text, :status, :source, :ref) "
            f"ON CONFLICT (project_id, kind, key) DO UPDATE SET title=EXCLUDED.title, body=EXCLUDED.body, text=EXCLUDED.text, "
            f"status=EXCLUDED.status, source=EXCLUDED.source, source_ref=EXCLUDED.source_ref, "
            f"version=project_knowledge.version+1, updated_at=now() RETURNING {_COLS}"),
            {"id": generate_id("pk"), "p": project_id, "k": kind, "key": key, "title": title, "body": body_json, "text": text_,
             "status": status or "draft", "source": source, "ref": source_ref})
        out, changed = _row(res.first()), "created"
    else:
        c = _row(cur)
        new_status = status or c["status"]
        if (c["title"] == title and json.dumps(c["body"], sort_keys=True, default=str) == body_json and c["text"] == text_
                and c["status"] == new_status and c["source_ref"] == source_ref and c["source"] == source):
            out, changed = c, "unchanged"
        else:
            res = await session.execute(sql(
                f"UPDATE project_knowledge SET title=:title, body=CAST(:body AS jsonb), text=:text, status=:status, source=:source, "
                f"source_ref=:ref, version=version+1, updated_at=now() WHERE id=:id RETURNING {_COLS}"),
                {"id": c["id"], "title": title, "body": body_json, "text": text_, "status": new_status, "source": source,
                 "ref": source_ref})
            out, changed = _row(res.first()), "updated"
    if commit:
        await session.commit()
    return {**out, "changed": changed}


async def get(session: AsyncSession, project_id: str, kind: str, key: str) -> dict[str, Any] | None:
    r = (await session.execute(sql(f"SELECT {_COLS} FROM project_knowledge WHERE project_id=:p AND kind=:k AND key=:key"),
                               {"p": project_id, "k": kind, "key": key})).first()
    return _row(r) if r else None


async def list_kind(session: AsyncSession, project_id: str, kind: str) -> list[dict[str, Any]]:
    rows = (await session.execute(sql(f"SELECT {_COLS} FROM project_knowledge WHERE project_id=:p AND kind=:k ORDER BY key"),
                                  {"p": project_id, "k": kind})).all()
    return [_row(r) for r in rows]


async def delete(session: AsyncSession, project_id: str, kind: str, key: str, commit: bool = True) -> bool:
    res = await session.execute(sql("DELETE FROM project_knowledge WHERE project_id=:p AND kind=:k AND key=:key"),
                                {"p": project_id, "k": kind, "key": key})
    if commit:
        await session.commit()
    return res.rowcount > 0


async def prune(session: AsyncSession, project_id: str, kinds: tuple[str, ...], source_ref: str, keep_keys: set[str],
                commit: bool = True) -> int:
    """Delete code-derived rows of ``source_ref`` whose key is no longer produced (code that was removed)."""
    rows = (await session.execute(sql("SELECT kind, key FROM project_knowledge WHERE project_id=:p AND kind = ANY(:ks) "
                                      "AND source='code' AND source_ref=:ref"), {"p": project_id, "ks": list(kinds), "ref": source_ref})).all()
    stale = [(k, key) for k, key in rows if key not in keep_keys]
    for k, key in stale:
        await session.execute(sql("DELETE FROM project_knowledge WHERE project_id=:p AND kind=:k AND key=:key"),
                              {"p": project_id, "k": k, "key": key})
    if commit and stale:
        await session.commit()
    return len(stale)


# ── retrieval ────────────────────────────────────────────────────────────────

_WORDS = re.compile(r"[A-Za-z0-9]+")


def _tsquery(query: str) -> str:
    """OR-of-prefixes tsquery text from free text (``approve triage`` -> ``approve:* | triage:*``); '' when no words."""
    seen: list[str] = []
    for w in _WORDS.findall(query or ""):
        w = w.lower()
        if len(w) >= 2 and w not in seen:
            seen.append(w)
    return " | ".join(f"{w}:*" if len(w) >= 3 else w for w in seen[:24])


def _format_item(r: dict[str, Any]) -> str:
    return f"[{r['kind']}] {r['key']}: {r['text']}" if r["text"] else f"[{r['kind']}] {r['key']}"


async def retrieve(session: AsyncSession, project_id: str, query: str, kinds: list[str] | None = None,
                   token_budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Rows of ``project_id`` most relevant to ``query``, best first, whose rendered text fits ``token_budget`` tokens.

    Returns ``{items: [{kind, key, snippet, score, status, version}], tokens, budget, truncated, omitted, text}``.
    ``text`` is the prompt-ready rendering: one ``[kind] key: ...`` line per item and, when rows had to be dropped or cut,
    a final truncation marker. ``est_tokens(text) <= token_budget`` always holds. Ranking is Postgres ``ts_rank_cd`` over the
    weighted tsvector (key/title weigh more than body text), with exact key matches first; an empty query lists the
    newest rows."""
    kinds = [k for k in (kinds or []) if k]
    for k in kinds:
        if k not in KINDS:
            raise KnowledgeError(f"unknown kind {k!r}; one of {', '.join(KINDS)}")
    budget = max(int(token_budget), 1)
    q = _tsquery(query)
    params: dict[str, Any] = {"p": project_id, "ks": kinds or list(KINDS), "q": q, "lim": 200}
    if q:
        rows = (await session.execute(sql(
            f"SELECT {_COLS}, ts_rank_cd(tsv, to_tsquery('english', :q), 1) AS rank FROM project_knowledge "
            f"WHERE project_id=:p AND kind = ANY(:ks) AND tsv @@ to_tsquery('english', :q) ORDER BY rank DESC, updated_at DESC LIMIT :lim"),
            params)).all()
        if not rows:  # stems that miss (identifiers, odd tokens): fall back to a plain substring match on any word
            likes = [f"%{w}%" for w in _WORDS.findall(query.lower())][:8]
            rows = (await session.execute(sql(
                f"SELECT {_COLS}, 0.01::float AS rank FROM project_knowledge WHERE project_id=:p AND kind = ANY(:ks) "
                f"AND (lower(key) LIKE ANY(:likes) OR lower(text) LIKE ANY(:likes)) ORDER BY updated_at DESC LIMIT :lim"),
                {**params, "likes": likes})).all() if likes else []
    else:
        rows = (await session.execute(sql(
            f"SELECT {_COLS}, 0::float AS rank FROM project_knowledge WHERE project_id=:p AND kind = ANY(:ks) "
            f"ORDER BY updated_at DESC LIMIT :lim"), params)).all()
    ql = (query or "").strip().lower()
    ranked = []
    for r in rows:
        d = _row(r)
        score = float(d.pop("rank") or 0) * 10 * KIND_WEIGHT.get(d["kind"], 1.0)
        if ql and d["key"].lower() == ql:
            score += 100
        elif ql and d["key"].lower() in ql:
            score += 2
        if d["status"] == "approved":
            score += 0.05
        ranked.append((score, d))
    ranked.sort(key=lambda t: (-t[0], t[1]["kind"], t[1]["key"]))

    lines: list[str] = []
    items: list[dict[str, Any]] = []
    truncated = False
    for idx, (score, d) in enumerate(ranked):
        left = len(ranked) - idx - 1
        reserve = TRUNCATION_MARKER.format(n=len(ranked) - idx)  # marker if this item and everything after are dropped
        line = _format_item(d)
        trial = lines + [line]
        if est_tokens("\n".join(trial + ([TRUNCATION_MARKER.format(n=left)] if left else []))) <= budget:
            lines.append(line)
            items.append({"kind": d["kind"], "key": d["key"], "snippet": d["text"], "score": round(score, 3), "status": d["status"],
                          "version": d["version"]})
            continue
        truncated = True
        # cut this one row to what is left, but only if that leaves a useful amount of it
        room = budget * 4 - len("\n".join(lines + [""])) - len(reserve) - 1 - len(_SNIPPET_CUT)
        if room >= 60:
            cut = line[:room] + _SNIPPET_CUT
            lines.append(cut)
            items.append({"kind": d["kind"], "key": d["key"], "snippet": cut, "score": round(score, 3), "status": d["status"],
                          "version": d["version"], "cut": True})
            dropped = len(ranked) - idx - 1
        else:
            dropped = len(ranked) - idx
        if dropped:
            lines.append(TRUNCATION_MARKER.format(n=dropped))
        break
    out = "\n".join(lines)
    if est_tokens(out) > budget:  # a budget too small even for the marker: hard clip, still flagged
        out = out[: budget * 4 - len(_SNIPPET_CUT)] + _SNIPPET_CUT if budget * 4 > len(_SNIPPET_CUT) else out[: budget * 4]
        truncated = True
    return {"items": items, "tokens": est_tokens(out), "budget": budget, "truncated": truncated,
            "omitted": max(len(ranked) - len(items), 0), "text": out}


# ── indexers: code -> rows ───────────────────────────────────────────────────

_KIT_PREFIX = "kit/"


def _attr_chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _scan_usage(source: str) -> dict[str, Any]:
    """module_generator.scan_usage when it imports (it is being edited elsewhere), else the same literal-table scan."""
    try:
        from .module_generator import scan_usage

        return scan_usage(source)
    except Exception:
        tables: dict[str, list[int]] = {}
        try:
            for n in ast.walk(ast.parse(source)):
                if (isinstance(n, ast.Call) and _attr_chain(n.func) in ("studio.db.table", "db.table") and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                    tables.setdefault(n.args[0].value, []).append(n.lineno)
        except SyntaxError:
            pass
        return {"tables": tables}


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """The nodes of a function in source order, without descending into nested function/class definitions."""
    out, stack = [], list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        out.append(n)
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))
    out.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    return out


def _dict_keys(node: ast.AST | None) -> list[str]:
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _is_req_json(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _attr_chain(node.func) == "req.json"


def _route_facts(fn: ast.AST, path: str) -> dict[str, Any]:
    """Request and response fields of a route handler, read literally from the AST (nothing is guessed from dynamic code)."""
    body_vars: set[str] = set()
    assigns: dict[str, ast.AST] = {}
    for n in _own_nodes(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            assigns[n.targets[0].id] = n.value
            if any(_is_req_json(x) for x in ast.walk(n.value)):
                body_vars.add(n.targets[0].id)
    body_fields: list[str] = []
    query_fields: list[str] = []

    def add(lst: list[str], v: str) -> None:
        if v not in lst:
            lst.append(v)

    for n in _own_nodes(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            owner = n.func.value
            if isinstance(owner, ast.Name) and owner.id in body_vars or _is_req_json(owner):
                add(body_fields, n.args[0].value)
            elif _attr_chain(owner) == "req.query":
                add(query_fields, n.args[0].value)
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
            owner = n.value
            if isinstance(owner, ast.Name) and owner.id in body_vars or _is_req_json(owner):
                add(body_fields, n.slice.value)
            elif _attr_chain(owner) == "req.query":
                add(query_fields, n.slice.value)
    response: list[str] = []
    shape = None
    statuses: list[int] = []

    def from_value(v: ast.AST | None, depth: int = 0) -> None:
        nonlocal shape
        if v is None or depth > 3:
            return
        if isinstance(v, ast.Dict):
            shape = shape or "object"
            for k in _dict_keys(v):
                add(response, k)
        elif isinstance(v, (ast.List, ast.ListComp)):
            shape = "list"
            elt = v.elt if isinstance(v, ast.ListComp) else (v.elts[0] if v.elts else None)
            from_value(elt, depth + 1)
        elif isinstance(v, ast.Tuple) and v.elts:
            from_value(v.elts[0], depth + 1)
            if len(v.elts) > 1 and isinstance(v.elts[1], ast.Constant) and isinstance(v.elts[1].value, int):
                statuses.append(v.elts[1].value)
        elif isinstance(v, ast.Name) and v.id in assigns:
            from_value(assigns[v.id], depth + 1)
        elif isinstance(v, ast.Call) and _attr_chain(v.func).split(".")[-1] == "Response":
            for kw in v.keywords:
                if kw.arg == "body":
                    from_value(kw.value, depth + 1)
                elif kw.arg == "status" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    statuses.append(kw.value.value)

    for n in _own_nodes(fn):
        if isinstance(n, ast.Return):
            from_value(n.value)
        elif isinstance(n, ast.Call) and _attr_chain(n.func).split(".")[-1] == "HTTPError" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, int):
            statuses.append(n.args[0].value)
    path_params = re.findall(r"\{([^}/]+)\}|:([A-Za-z_]\w*)", path)
    return {"path_params": [a or b for a, b in path_params], "query": query_fields, "body": body_fields,
            "response": response, "response_shape": shape, "statuses": sorted(set(statuses))}


def _module_functions(source: str) -> dict[str, ast.AST]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def route_rows_from_source(source: str, module_name: str = "module") -> list[dict[str, Any]]:
    """Route rows for a module, as ``{key, title, body}``. Decorators come from ``code_module_service.discover_routes``
    (the parser ApiRouteService itself uses), so method/path/function/auth/name cannot disagree with the registered routes."""
    from .code_module_service import discover_routes

    fns = _module_functions(source)
    out = []
    for d in discover_routes(source):  # raises ProjectError on a syntax error / bad decorator
        fn = fns.get(d["function"])
        facts = _route_facts(fn, d["path"]) if fn is not None else {}
        seg = ast.get_source_segment(source, fn) if fn is not None else ""
        tables = sorted(_scan_usage(seg or "").get("tables", {}))
        jobs = bool(re.search(r"\bstudio\.jobs\.|\bjobs\.(start|get|events|frame)\(", seg or ""))
        desc = (d.get("description") or "").strip()
        key = f"{d['method']} {d['path']}"
        body = {"method": d["method"], "path": d["path"], "name": d.get("name"), "function": d["function"], "module": module_name,
                "handler": f"python module {module_name}", "auth": d["auth"], "description": desc, "line": d["line"],
                "tables": tables, "uses_jobs": jobs, **facts}
        out.append({"key": key, "title": f"{key} ({d['function']})", "body": body,
                    "text": _route_text(body)})
    return out


def _route_text(b: dict[str, Any]) -> str:
    bits = [f"{b['method']} {b['path']} handled by python module {b['module']} function {b['function']}"]
    if b.get("name"):
        bits.append(f"name {b['name']}")
    if b.get("description"):
        bits.append(b["description"])
    bits.append(f"auth {b['auth']}")
    if b.get("path_params"):
        bits.append("path params " + ", ".join(b["path_params"]))
    if b.get("query"):
        bits.append("query " + ", ".join(b["query"]))
    if b.get("body"):
        bits.append("body " + ", ".join(b["body"]))
    if b.get("response"):
        bits.append(f"returns {b.get('response_shape') or 'value'} with " + ", ".join(b["response"]))
    if b.get("statuses"):
        bits.append("status " + ", ".join(map(str, b["statuses"])))
    if b.get("tables"):
        bits.append("tables " + ", ".join(b["tables"]))
    if b.get("uses_jobs"):
        bits.append("uses jobs service start run poll")
    return "; ".join(bits)


async def index_module(session: AsyncSession, project_id: str, source: str, module_name: str = "module",
                       commit: bool = True) -> dict[str, Any]:
    """Upsert one route row per decorated handler of ``source``; drop rows of this module that no longer exist.
    A module that does not parse leaves its existing rows alone and reports ``error``."""
    from .project_service import ProjectError

    ref = f"module:{module_name}"
    try:
        rows = route_rows_from_source(source, module_name)
    except ProjectError as e:
        return {"module": module_name, "routes": 0, "created": 0, "updated": 0, "unchanged": 0, "pruned": 0,
                "error": getattr(e, "message", None) or str(e)}
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for r in rows:
        res = await upsert(session, project_id, "route", r["key"], title=r["title"], body=r["body"], text=r["text"], status="approved",
                           source="code", source_ref=ref, commit=False)
        counts[res["changed"]] += 1
    pruned = await prune(session, project_id, ("route",), ref, {r["key"] for r in rows}, commit=False)
    if commit:
        await session.commit()
    return {"module": module_name, "routes": len(rows), **counts, "pruned": pruned}


async def index_tables(session: AsyncSession, project_id: str, commit: bool = True) -> dict[str, Any]:
    from ..db.models import DataTableModel

    tables = (await session.execute(select(DataTableModel).where(DataTableModel.project_id == project_id)
                                    .order_by(DataTableModel.name))).scalars().all()
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for t in tables:
        cols = [{k: c[k] for k in ("name", "type", "required", "default") if k in c} for c in (t.columns or [])]
        body = {"name": t.name, "description": t.description, "columns": cols}
        desc = ", ".join(f"{c['name']} {c['type']}{' required' if c.get('required') else ''}" for c in cols)
        text = f"table {t.name}" + (f" ({t.description})" if t.description else "") + f" columns: {desc}"
        res = await upsert(session, project_id, "table_schema", t.name, title=f"table {t.name}", body=body, text=text,
                           status="approved", source="code", source_ref="tables", commit=False)
        counts[res["changed"]] += 1
    pruned = await prune(session, project_id, ("table_schema",), "tables", {t.name for t in tables}, commit=False)
    if commit:
        await session.commit()
    return {"tables": len(tables), **counts, "pruned": pruned}


_IMPORT_FROM_KIT = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"](?:\.{1,2}/)+(?:src/)?kit(?:/[\w.]+)?['\"]")
_API_CALL = re.compile(r"(?<![\w$.])api\s*\.\s*([A-Za-z_]\w*)\s*\(")   # tolerates ``api\n  .op(``; not ``xapi.`` / ``.api.``


def _jsx_attrs(src: str, name: str) -> tuple[int, list[str]]:
    """(uses, sorted attribute names) of ``<name ...>`` across ``src``; braces and strings in values are skipped."""
    uses, attrs = 0, set()
    for m in re.finditer(rf"<{re.escape(name)}(?=[\s/>])", src):
        uses += 1
        i, depth, n = m.end(), 0, len(src)
        start = i
        while i < n:
            c = src[i]
            if c in "'\"`" and depth >= 0:
                j = i + 1
                while j < n and src[j] != c:
                    j += 2 if src[j] == "\\" else 1
                i = j
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif depth == 0 and (c == ">" or src.startswith("/>", i)):
                break
            i += 1
        head = re.sub(r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}", "{}", src[start:i])
        head = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", '""', head)
        attrs.update(re.findall(r"(?:^|\s)([A-Za-z_][\w-]*)(?==|\s|$)", head))
    return uses, sorted(a for a in attrs if a not in ("key",))


def _is_screen(path: str) -> bool:
    p = path.lower()
    return bool(re.search(r"(^|/)(views|pages|screens|routes)/", p)) or p.rsplit("/", 1)[-1] in ("app.tsx", "app.jsx")


async def index_app(session: AsyncSession, project_id: str, files: list[dict[str, str]], app_ref: str = "app",
                    commit: bool = True, key_prefix: str = "") -> dict[str, Any]:
    """``files`` is ``[{path, content}]`` of an app (the kit copy under ``kit/`` is skipped). Writes one ``screen`` row per
    view/page file and one ``component_use`` row per (file, kit component) with the props the file really passes.
    ``key_prefix`` keeps the keys of several apps in one project apart."""
    from .platform_index import kit_components
    from .tsx_parser import parse_tsx_file

    kit_names = {c["name"] for c in kit_components()}
    ref = f"app:{app_ref}"
    screen_keys: set[str] = set()
    use_keys: set[str] = set()
    counts = {"created": 0, "updated": 0, "unchanged": 0}

    for f in files:
        path = f["path"].lstrip("./")
        rel = path.removeprefix("src/")
        if rel.startswith(_KIT_PREFIX) or not path.endswith((".tsx", ".jsx", ".ts")):
            continue
        src = f.get("content") or ""
        parsed = parse_tsx_file(src)
        imported = set()
        for m in _IMPORT_FROM_KIT.finditer(src):
            imported |= {s.split(" as ")[0].strip() for s in m.group(1).split(",") if s.strip()}
        used_kit = []
        for name in sorted(n for n in imported if n in kit_names):
            uses, attrs = _jsx_attrs(src, name)
            if not uses:
                continue
            used_kit.append(name)
            key = f"{key_prefix}{path}#{name}"
            use_keys.add(key)
            body = {"file": path, "component": name, "uses": uses, "props_used": attrs}
            text = f"{path} uses kit component {name} {uses} time(s) with props " + (", ".join(attrs) or "none")
            res = await upsert(session, project_id, "component_use", key, title=f"{name} in {path}", body=body, text=text,
                               status="approved", source="code", source_ref=ref, commit=False)
            counts[res["changed"]] += 1
        if _is_screen(path):
            comps = [d["name"] for d in parsed.get("definitions", []) if d["kind"] == "component" and d.get("exported")]
            api_calls = sorted(set(_API_CALL.findall(src)))
            title = (parsed.get("file_comment") or "").strip().splitlines()[0][:160] if parsed.get("file_comment") else ""
            body = {"path": path, "title": title, "components": comps, "kit_components": used_kit, "api_calls": api_calls}
            text = (f"screen {path}" + (f" {title}" if title else "") + (f" components {', '.join(comps)}" if comps else "")
                    + (f"; calls api {', '.join(api_calls)}" if api_calls else "")
                    + (f"; uses kit {', '.join(used_kit)}" if used_kit else ""))
            screen_keys.add(key_prefix + path)
            res = await upsert(session, project_id, "screen", key_prefix + path, title=title or f"screen {path}", body=body, text=text,
                               status="approved", source="code", source_ref=ref, commit=False)
            counts[res["changed"]] += 1
    pruned = await prune(session, project_id, ("screen",), ref, screen_keys, commit=False)
    pruned += await prune(session, project_id, ("component_use",), ref, use_keys, commit=False)
    if commit:
        await session.commit()
    return {"screens": len(screen_keys), "component_uses": len(use_keys), **counts, "pruned": pruned}


async def reindex_project(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """Rebuild every code-derived row of a project from its modules, tables and apps. Idempotent."""
    from ..db.models import AppFileModel, AppModel
    from .code_module_service import CodeModuleService

    result: dict[str, Any] = {"modules": [], "apps": []}
    sources = await CodeModuleService(session).sources(project_id)
    for name in sorted(sources):
        result["modules"].append(await index_module(session, project_id, sources[name], name, commit=False))
    # routes of modules that were deleted
    live = {f"module:{n}" for n in sources}
    stale_refs = (await session.execute(sql("SELECT DISTINCT source_ref FROM project_knowledge WHERE project_id=:p AND kind='route' "
                                            "AND source='code' AND source_ref LIKE 'module:%'"), {"p": project_id})).scalars().all()
    pruned = 0
    for ref in stale_refs:
        if ref not in live:
            pruned += await prune(session, project_id, ("route",), ref, set(), commit=False)
    result["pruned_modules"] = pruned
    result["tables"] = await index_tables(session, project_id, commit=False)
    apps = (await session.execute(select(AppModel).where(AppModel.project_id == project_id).order_by(AppModel.created_at))).scalars().all()
    live_apps = set()
    for app in apps:
        vid = app.current_version_id
        if vid is None:
            continue
        rows = (await session.execute(select(AppFileModel).where(AppFileModel.version_id == vid))).scalars().all()
        live_apps.add(f"app:{app.id}")
        files = [{"path": r.path, "content": r.content} for r in rows]
        prefix = f"{app.name or app.slug or 'app'} [{app.id[-6:]}]: "
        result["apps"].append({"app": app.id, **await index_app(session, project_id, files, app_ref=app.id, commit=False,
                                                                  key_prefix=prefix)})
    gone = (await session.execute(sql("SELECT DISTINCT source_ref FROM project_knowledge WHERE project_id=:p AND source='code' "
                                      "AND source_ref LIKE 'app:%'"), {"p": project_id})).scalars().all()
    for ref in gone:
        if ref not in live_apps:
            await prune(session, project_id, ("screen", "component_use"), ref, set(), commit=False)
    await session.commit()
    counts = (await session.execute(sql("SELECT kind, count(*) FROM project_knowledge WHERE project_id=:p GROUP BY kind"),
                                    {"p": project_id})).all()
    result["rows_by_kind"] = {k: n for k, n in counts}
    return result

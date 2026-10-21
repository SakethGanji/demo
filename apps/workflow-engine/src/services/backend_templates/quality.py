"""Cheap static gate for code that only looks finished: stub bodies, TODO markers and handlers that answer with a hardcoded
sample instead of reading data. Findings are ``[{code, line, message}]`` like ``checks``; the repair loop feeds them back."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from typing import Any

_MARKER = re.compile(r"\b(TODO|FIXME|XXX|placeholder|not\s+implemented|unimplemented|stubbed?|mock(?:ed)?\s+(?:data|response)|"
                     r"dummy|sample\s+data|lorem\s+ipsum|to\s+be\s+implemented|implement\s+later)\b", re.IGNORECASE)
_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete"}
_DATA_CALLS = ("studio.db", "studio.jobs", "studio.nodes", ".table(", "jobs.", "db.table")


def _is_route(fn: ast.AST) -> bool:
    for d in getattr(fn, "decorator_list", []):
        c = d.func if isinstance(d, ast.Call) else d
        if isinstance(c, ast.Attribute) and c.attr in _ROUTE_DECORATORS:
            return True
    return False


def _only_stub(fn: ast.AST) -> str | None:
    body = list(getattr(fn, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return "has no body"
    if all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                       and s.value.value is Ellipsis) for s in body):
        return "is only `pass` / `...`"
    for s in body:
        if isinstance(s, ast.Raise) and s.exc is not None:
            e = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
            if isinstance(e, ast.Name) and e.id == "NotImplementedError":
                return "raises NotImplementedError"
            if isinstance(s.exc, ast.Call) and isinstance(e, ast.Name) and e.id == "HTTPError" and s.exc.args \
                    and isinstance(s.exc.args[0], ast.Constant) and s.exc.args[0].value == 501:
                return "answers 501 not implemented"
    if len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call):
        c = body[0].value
        if isinstance(c.func, ast.Name) and c.func.id == "Response" and c.args and isinstance(c.args[0], ast.Constant) \
                and c.args[0].value == 501:
            return "answers 501 not implemented"
    return None


def _const_dict_rows(node: ast.AST) -> int:
    """How many dict literals made only of constants a list literal holds (0 if it is not such a list)."""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return 0
    n = 0
    for e in node.elts:
        if not isinstance(e, ast.Dict) or len(e.keys) < 2:
            return 0
        if not all(isinstance(v, (ast.Constant, ast.List, ast.Tuple, ast.Dict)) and not _has_name(v) for v in e.values):
            return 0
        n += 1
    return n


def _has_name(node: ast.AST) -> bool:
    return any(isinstance(x, (ast.Name, ast.Call, ast.Attribute, ast.Subscript, ast.JoinedStr)) for x in ast.walk(node))


def _returned_samples(fn: ast.AST) -> int:
    """Hardcoded sample rows a handler returns: ``return [{..}, {..}]``, ``return {"rows": [{..}]}`` or via a local name."""
    local: dict[str, ast.AST] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            local[n.targets[0].id] = n.value
    best = 0
    for r in [x for x in ast.walk(fn) if isinstance(x, ast.Return) and x.value is not None]:
        vals = [r.value]
        if isinstance(r.value, ast.Dict):
            vals += list(r.value.values)
        for v in vals:
            if isinstance(v, ast.Name) and v.id in local:
                v = local[v.id]
            best = max(best, _const_dict_rows(v))
    return best


def placeholder_problems(source: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    spans = [(f.lineno, getattr(f, "end_lineno", f.lineno), f.name) for f in fns]

    def owner(line: int) -> str | None:
        inner = [s for s in spans if s[0] <= line <= s[1]]
        return min(inner, key=lambda s: s[1] - s[0])[2] if inner else None

    for fn in fns:
        why = _only_stub(fn)
        if why and (_is_route(fn) or not fn.name.startswith("__")):
            out.append({"code": "placeholder_body", "line": fn.lineno,
                        "message": f"{fn.name}() {why}: write the real implementation (or remove the route and say it is not built)"})
        if _is_route(fn):
            seg = ast.get_source_segment(source, fn) or ""
            n = _returned_samples(fn)
            if n and not any(t in seg for t in _DATA_CALLS):
                out.append({"code": "placeholder_data", "line": fn.lineno,
                            "message": (f"{fn.name}() returns {n} hardcoded sample row(s) and reads no table, Jobs result or node: "
                                        "derive the response from real data, never invented examples")})
    seen: set[int] = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        toks = []
    docs = [n.body[0] for n in fns if n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)]
    texts: list[tuple[int, str]] = [(t.start[0], t.string) for t in toks if t.type == tokenize.COMMENT]
    texts += [(d.value.lineno, d.value.value) for d in docs]
    for line, text in texts:
        m = _MARKER.search(text)
        if m and line not in seen and owner(line) is not None:
            seen.add(line)
            out.append({"code": "placeholder_marker", "line": line,
                        "message": (f"'{m.group(0)}' marks unfinished or invented behaviour in {owner(line)}(): implement it from "
                                    "real data, or drop the claim")})
    return out

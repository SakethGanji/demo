"""``patch_module``: function-level edits to a module, applied and validated through ``ast``.

A big module (50 KB+) cannot be regenerated inside one output window. The model reads the outline, fetches only the
functions it needs and sends ops; this file applies them to the current source and refuses anything that would leave
a broken file. Nothing here runs the module.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import re
import textwrap
from typing import Any

MAX_OPS = 40
OP_NAMES = ("replace_function", "add_function", "remove_function", "add_import", "set_constant")
_ALLOWED_TOP = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)

PATCH_MODULE_TOOL: dict[str, Any] = {
    "name": "patch_module",
    "description": ("Edit the existing module without re-emitting it: function-level operations applied in order, validated "
                    "by AST. Use it for every change to an existing module and to add the remaining areas of a large "
                    "new one. Keep one call under ~8 functions. `ops` may be empty ONLY when you are just correcting "
                    "self_test steps."),
    "parameters": {
        "type": "object", "required": ["ops", "summary"],
        "properties": {
            "ops": {"type": "array", "items": {"type": "object", "required": ["op"], "properties": {
                "op": {"enum": list(OP_NAMES)},
                "name": {"type": "string", "description": "function or constant name (replace_function, remove_function, set_constant)"},
                "source": {"type": "string", "description": ("full source: decorators + def for functions, `NAME = ...` for "
                                                              "set_constant, import lines for add_import")},
                "after": {"type": "string", "description": "add_function: insert after this top-level function"}}}},
            "summary": {"type": "string", "maxLength": 300},
            "tables": {"type": "array", "items": {"type": "object"}, "description": "new tables, as in emit_module"},
            "self_test": {"type": "array", "items": {"type": "object"},
                          "description": "steps to ADD to the module's stored self-test (same shape as emit_module.self_test)"},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "remaining": {"type": "array", "items": {"type": "string"},
                          "description": "areas of a large build still to write; empty when finished"},
            "planned_routes": {"type": "array", "items": {"type": "string"},
                               "description": "'METHOD /path' of every route the module should have when finished (checked against the source)"},
        },
    },
}


class PatchError(Exception):
    """One op that cannot be applied; ``message`` is what the model reads."""


def _span(node: ast.stmt) -> tuple[int, int]:
    """1-based inclusive line span of a top-level statement, decorators included."""
    start = node.lineno
    for d in getattr(node, "decorator_list", []) or []:
        start = min(start, d.lineno)
    return start, node.end_lineno or node.lineno


def _parse(source: str, what: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as e:
        raise PatchError(f"{what}: syntax error line {e.lineno}: {e.msg}") from None


def _top_names(tree: ast.Module) -> dict[str, ast.stmt]:
    out: dict[str, ast.stmt] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out[n.target.id] = n
    return out


def _check_snippet(src: str, what: str) -> tuple[str, ast.Module]:
    src = textwrap.dedent(src or "").strip("\n")
    if not src.strip():
        raise PatchError(f"{what}: empty source")
    tree = _parse(src, what)
    for n in tree.body:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            continue
        if not isinstance(n, _ALLOWED_TOP):
            raise PatchError(f"{what}: only defs, classes, assignments and imports are allowed at module level "
                             f"(line {n.lineno} is a {type(n).__name__})")
    return src, tree


def _lines(source: str) -> list[str]:
    return source.split("\n")


def _join(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _tidy(lines: list[str]) -> list[str]:
    """Collapse runs of 3+ blank lines (left behind by a removal) to two."""
    out: list[str] = []
    for ln in lines:
        if not ln.strip() and len(out) >= 2 and not out[-1].strip() and not out[-2].strip():
            continue
        out.append(ln)
    return out


def _insert_after_line(lines: list[str], line: int, text: str) -> list[str]:
    block = ["", ""] + text.split("\n")
    return lines[:line] + block + lines[line:]


def _last_import_line(tree: ast.Module) -> int:
    last = 0
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            last = n.end_lineno or n.lineno
        elif isinstance(n, ast.Expr) and last == 0 and isinstance(n.value, ast.Constant):
            last = n.end_lineno or n.lineno    # module docstring
    return last


def _replace_function(source: str, op: dict[str, Any]) -> str:
    name = str(op.get("name") or "")
    tree = _parse(source, "current module")
    cur = _top_names(tree).get(name)
    if cur is None or not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        raise PatchError(f"replace_function: no top-level function '{name}' (use add_function for a new one)")
    src, snippet = _check_snippet(op.get("source"), f"replace_function {name}")
    defs = [n for n in snippet.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if not any(d.name == name for d in defs):
        raise PatchError(f"replace_function {name}: the source must define '{name}'")
    existing = _top_names(tree)
    for n in snippet.body:           # helpers that ride along must be new, otherwise the op is ambiguous
        for nm in ([n.name] if hasattr(n, "name") else []):
            if nm != name and nm in existing and not isinstance(n, (ast.Import, ast.ImportFrom)):
                raise PatchError(f"replace_function {name}: '{nm}' already exists; replace it with its own op")
    a, b = _span(cur)
    lines = _lines(source)
    return _join(lines[:a - 1] + src.split("\n") + lines[b:])


def _add_function(source: str, op: dict[str, Any]) -> str:
    src, snippet = _check_snippet(op.get("source"), "add_function")
    tree = _parse(source, "current module")
    existing = _top_names(tree)
    for n in snippet.body:
        names = [n.name] if hasattr(n, "name") else [t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)]
        for nm in names:
            if nm in existing:
                raise PatchError(f"add_function: '{nm}' already exists (use replace_function)")
    after = op.get("after")
    lines = _lines(source.rstrip("\n"))
    if after:
        node = existing.get(str(after))
        if node is None:
            raise PatchError(f"add_function: no top-level '{after}' to insert after")
        return _join(_insert_after_line(lines, _span(node)[1], src))
    return _join(lines + ["", ""] + src.split("\n"))


def _remove_function(source: str, op: dict[str, Any]) -> str:
    name = str(op.get("name") or "")
    tree = _parse(source, "current module")
    cur = _top_names(tree).get(name)
    if cur is None or not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        raise PatchError(f"remove_function: no top-level function '{name}'")
    a, b = _span(cur)
    if not getattr(cur, "decorator_list", None):      # a helper: nothing may still call it
        users = sorted({n.lineno for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name
                        and isinstance(n.ctx, ast.Load) and not (a <= n.lineno <= b)})
        if users:
            raise PatchError(f"remove_function {name}: still used on line(s) {', '.join(map(str, users[:8]))}")
    lines = _lines(source)
    return _join(_tidy(lines[:a - 1] + lines[b:]))


def _add_import(source: str, op: dict[str, Any]) -> str:
    src, snippet = _check_snippet(op.get("source"), "add_import")
    if any(not isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.Expr)) for n in snippet.body):
        raise PatchError("add_import: only import lines")
    tree = _parse(source, "current module")
    have = {ast.dump(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    new = [n for n in snippet.body if isinstance(n, (ast.Import, ast.ImportFrom)) and ast.dump(n) not in have]
    if not new:
        return source
    text = "\n".join(ast.get_source_segment(src, n) or "" for n in new)
    lines = _lines(source.rstrip("\n"))
    at = _last_import_line(tree)
    return _join(lines[:at] + text.split("\n") + lines[at:])


def _set_constant(source: str, op: dict[str, Any]) -> str:
    name = str(op.get("name") or "")
    src, snippet = _check_snippet(op.get("source"), f"set_constant {name}")
    if not any(isinstance(n, (ast.Assign, ast.AnnAssign)) and name in
               ([t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)]
                + ([n.target.id] if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) else []))
               for n in snippet.body):
        raise PatchError(f"set_constant {name}: the source must assign '{name}'")
    tree = _parse(source, "current module")
    cur = _top_names(tree).get(name)
    lines = _lines(source.rstrip("\n"))
    if cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            raise PatchError(f"set_constant {name}: '{name}' is a function, not a constant")
        a, b = _span(cur)
        return _join(lines[:a - 1] + src.split("\n") + lines[b:])
    at = _last_import_line(tree)
    return _join(lines[:at] + [""] + src.split("\n") + lines[at:])


_APPLY = {"replace_function": _replace_function, "add_function": _add_function, "remove_function": _remove_function,
          "add_import": _add_import, "set_constant": _set_constant}


def apply_ops(source: str, ops: Any) -> tuple[str, list[str]]:
    """``(new_source, errors)``. Ops run in order on the evolving source; any error returns the ORIGINAL source untouched,
    with every failing op reported (later ops are still tried so one repair turn can fix them all)."""
    if not isinstance(ops, list) or not ops:
        return source, ["ops must be a non-empty list"]
    if len(ops) > MAX_OPS:
        return source, [f"at most {MAX_OPS} ops per call (got {len(ops)}); split the patch"]
    cur = source
    errors: list[str] = []
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in _APPLY:
            errors.append(f"op {i}: unknown op {op.get('op') if isinstance(op, dict) else op!r} (one of {', '.join(OP_NAMES)})")
            continue
        try:
            cur = _APPLY[op["op"]](cur, op)
        except PatchError as e:
            errors.append(f"op {i} ({op['op']}): {e}")
    if errors:
        return source, errors
    try:
        ast.parse(cur)
    except SyntaxError as e:
        return source, [f"the patched module does not parse: line {e.lineno}: {e.msg}"]
    return cur, []


# ── reading a big module without pasting it ───────────────────────────────────

_ROUTE_DEC = re.compile(r"^(?:api|studio\.api)\.(get|post|put|patch|delete)$")


def _route_label(node: ast.stmt) -> str | None:
    for d in getattr(node, "decorator_list", []) or []:
        if isinstance(d, ast.Call):
            m = _ROUTE_DEC.match(ast.unparse(d.func))
            if m and d.args and isinstance(d.args[0], ast.Constant):
                return f"{m.group(1).upper()} {d.args[0].value}"
    return None


def outline(source: str) -> list[dict[str, Any]]:
    """One entry per top-level definition: ``{name, kind, route, start, end, chars}``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = _lines(source)
    out: list[dict[str, Any]] = []
    for n in tree.body:
        a, b = _span(n)
        chars = sum(len(x) + 1 for x in lines[a - 1:b])
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            route = _route_label(n)
            out.append({"name": n.name, "kind": "route" if route else "function", "route": route, "start": a, "end": b,
                        "chars": chars})
        elif isinstance(n, ast.ClassDef):
            out.append({"name": n.name, "kind": "class", "route": None, "start": a, "end": b, "chars": chars})
        elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets):
            out.append({"name": ",".join(t.id for t in n.targets), "kind": "constant", "route": None, "start": a,
                        "end": b, "chars": chars})
    return out


def outline_text(source: str) -> str:
    """The outline as prompt text. Imports are listed as one line so the model keeps them consistent."""
    rows = outline(source)
    try:
        tree = ast.parse(source)
        imports = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    except SyntaxError:
        imports = []
    out = [f"imports: {'; '.join(imports) or '(none)'}"]
    for r in rows:
        tag = r["route"] or r["kind"]
        out.append(f"L{r['start']}-{r['end']} {tag}: {r['name']} ({r['chars']} chars)")
    return "\n".join(out)


def _resolve(name: str, top: dict[str, ast.stmt], routes: dict[str, str]) -> str | None:
    """A top-level name from what the model wrote: exact, a route label ('GET /x' or '/x'), or one unambiguous partial match."""
    if name in top:
        return name
    key = name.strip().lower()
    for label, fn in routes.items():
        if label.lower() == key or label.lower().split(" ", 1)[-1] == key:
            return fn
    partial = [n for n in top if key and (key in n.lower() or n.lower() in key)]
    return partial[0] if len(partial) == 1 else None


def function_sources(source: str, names: list[str], limit_chars: int = 24_000) -> dict[str, Any]:
    """Source of the named top-level definitions (decorators included), capped. A name may also be a route label such as
    ``GET /metrics/summary``. Unknown names come back with the closest real names, so one lookup is enough."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"error": "the module does not parse"}
    top = _top_names(tree)
    routes = {r["route"]: r["name"] for r in outline(source) if r.get("route")}
    lines = _lines(source)
    found: dict[str, str] = {}
    used = 0
    missing: dict[str, list[str]] = {}
    for want in names[:12]:
        nm = _resolve(str(want), top, routes)
        node = top.get(nm) if nm else None
        if node is None:
            near = difflib.get_close_matches(str(want), list(top), n=4, cutoff=0.4)
            missing[str(want)] = near or [r["name"] for r in outline(source) if r["kind"] == "route"][:6]
            continue
        a, b = _span(node)
        text = "\n".join(lines[a - 1:b])
        if used + len(text) > limit_chars:
            found[nm] = "[omitted: size cap; ask for it alone]"
            continue
        used += len(text)
        found[nm] = text
    return {"functions": found, **({"no_such_name_try": missing} if missing else {})}


def changed_functions(old: str, new: str) -> list[str]:
    """Names of top-level definitions added, removed or changed between two sources (what a patch touched)."""
    def bodies(src: str) -> dict[str, str]:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return {}
        return {nm: ast.dump(n) for nm, n in _top_names(tree).items()}
    a, b = bodies(old), bodies(new)
    return sorted(nm for nm in set(a) | set(b) if a.get(nm) != b.get(nm))


# ── names that nothing defines ────────────────────────────────────────────────

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__"}


def undefined_names(source: str) -> list[tuple[str, int]]:
    """Names read but bound nowhere in the module (not a builtin, import, def, class, assignment, argument, loop or
    handler target). Deliberately flat (no scoping): it misses a name defined in a different function, but it never
    flags a name that is defined, so a hit is always real. Catches a helper that a patch removed or misnamed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bound: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            bound.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            bound.add(n.rest)
    first: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound and n.id not in _BUILTINS:
            first[n.id] = min(first.get(n.id, n.lineno), n.lineno)
    return sorted(first.items(), key=lambda kv: (kv[1], kv[0]))


# ── which routes a patch can have changed ─────────────────────────────────────

def _decorator_roots(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for d in getattr(fn, "decorator_list", []):
        node = d.func if isinstance(d, ast.Call) else d
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name) and node.id not in ("api", "schedule"):
            out.add(node.id)
    return out


def touched_routes(old: str, new: str, decls: list[dict[str, Any]]) -> set[str] | None:
    """``{"METHOD /path"}`` of the routes whose behaviour a patch from ``old`` to ``new`` can have changed: handlers that
    changed, handlers that (through helpers) use something that changed, and handlers of a resource a hook was attached to.
    None when that cannot be told (the caller then checks every route)."""
    try:
        tree = ast.parse(new)
    except SyntaxError:
        return None
    changed = set(changed_functions(old, new))
    top = _top_names(tree)
    touched = set(changed)
    for nm in changed:
        if nm in top:
            touched |= _decorator_roots(top[nm])
    uses = {nm: {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} for nm, node in top.items()}
    grew = True
    while grew:                                  # a function that uses a touched name is touched too
        grew = False
        for nm, names in uses.items():
            if nm not in touched and names & touched:
                touched.add(nm)
                grew = True
    return {f"{d['method']} {d['path']}" for d in decls if d["function"] in touched}

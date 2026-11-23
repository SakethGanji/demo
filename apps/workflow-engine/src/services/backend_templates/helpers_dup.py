"""Find helper functions that are defined more than once across modules.

A fingerprint is the hash of a function's normalized AST: docstrings are
stripped and parameters/locals are renamed to positional placeholders, so a
copy with renamed variables still matches while different logic does not.
"""
from __future__ import annotations

import ast
import hashlib


class _Normalize(ast.NodeTransformer):
    def __init__(self, names: set[str]):
        self.map: dict[str, str] = {}
        self.names = names

    def _alias(self, name: str) -> str:
        return self.map.setdefault(name, f"v{len(self.map)}")

    def visit_arg(self, node: ast.arg):
        node.arg = self._alias(node.arg)
        node.annotation = None
        return node

    def visit_Name(self, node: ast.Name):
        if node.id in self.names:
            node.id = self._alias(node.id)
        return node


def _strip_doc(body: list[ast.stmt]) -> list[ast.stmt]:
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:] or [ast.Pass()]
    return body


def _locals(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def helper_fingerprints(source: str) -> dict[str, str]:
    """Top-level function name -> structural hash. Unparseable source -> {}."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fn = ast.parse(ast.unparse(node)).body[0]
        fn.body = _strip_doc(fn.body)
        fn.decorator_list = []
        fn.returns = None
        fn.name = "f"
        fn = _Normalize(_locals(fn)).visit(fn)
        ast.fix_missing_locations(fn)
        out[node.name] = hashlib.sha1(ast.dump(fn).encode()).hexdigest()[:16]
    return out


def duplicates(sources_by_module: dict[str, str],
               shared: str | None = None) -> list[dict]:
    """Helpers defined twice. A function in a non-shared module that matches a
    shared-module helper (by fingerprint or name) is reported as a duplicate of
    it; otherwise identical fingerprints in two or more modules are reported.
    Returns [{name, modules, shared}] (names sorted)."""
    prints = {m: helper_fingerprints(s) for m, s in sources_by_module.items()}
    found: dict[tuple, dict] = {}
    shared_fp = prints.get(shared, {}) if shared else {}
    shared_hashes = {h: n for n, h in shared_fp.items()}
    for mod, fps in prints.items():
        if mod == shared:
            continue
        for name, h in fps.items():
            hit = shared_hashes.get(h) or (name if name in shared_fp else None)
            if hit:
                e = found.setdefault(("shared", hit), {"name": hit, "modules": [shared], "shared": True})
                e["modules"].append(mod)
    seen: dict[str, list[tuple[str, str]]] = {}
    for mod, fps in prints.items():
        if mod == shared:
            continue
        for name, h in fps.items():
            seen.setdefault(h, []).append((mod, name))
    for h, items in seen.items():
        mods = sorted({m for m, _ in items})
        if len(mods) < 2:
            continue
        name = items[0][1]
        if ("shared", name) in found:
            continue
        found[("peer", h)] = {"name": name, "modules": mods, "shared": False}
    return sorted(found.values(), key=lambda d: d["name"])

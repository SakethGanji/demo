"""References between cells: ``{{cells.c3.rows}}``, ``{{cells.c3.count}}``, ``{{cells.c3.rows[0].id}}``, ``{{row.wire_id}}``.

JSONPath-lite: dotted keys and ``[n]`` indexes; a key on a list of rows (``{{cells.c3.rows.wire_id}}``) takes that
field from every row. A string that is exactly one reference becomes the referenced value
(a list stays a list); a reference inside a longer string is interpolated as text. Anything unknown raises ``BadRef``,
which the run loop records as the cell error ``bad_ref``.

The ``cells`` namespace of a cell ``cN`` resolves against that cell's latest usable result:
- ``rows`` = the full row set (from storage when the preview was cut), ``count`` = ``row_count``,
- ``output`` = the whole output dict, and any other key = ``output[key]``.

Variables: ``{{vars.base_url}}`` or just ``{{base_url}}`` is a variable: the newest value an http cell above saved
(``extract``), else the notebook's active environment. A secret environment variable is the engine expression
``{{ $vars.KEY }}``, which only the engine fills in.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SEG_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_\-]*)|\[(-?\d+)\]")


class BadRef(Exception):
    def __init__(self, ref: str, message: str) -> None:
        super().__init__(message)
        self.ref = ref
        self.message = message


def parse_path(expr: str) -> list[str | int]:
    """``cells.c3.rows[0].id`` -> ``["cells", "c3", "rows", 0, "id"]``."""
    out: list[str | int] = []
    pos = 0
    expr = expr.strip()
    while pos < len(expr):
        if expr[pos] == ".":
            pos += 1
            continue
        m = _SEG_RE.match(expr, pos)
        if not m:
            raise BadRef(expr, f"cannot read reference '{{{{{expr}}}}}'")
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    if not out:
        raise BadRef(expr, "empty reference")
    return out


def refs_in(value: Any) -> list[str]:
    """Every notebook reference expression (without braces) in a spec value, depth-first ({{ $… }} engine
    expressions are not notebook references)."""
    found: list[str] = []
    if isinstance(value, str):
        found.extend(m.group(1).strip() for m in REF_RE.finditer(value) if not m.group(1).strip().startswith("$"))
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(refs_in(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(refs_in(v))
    return found


def cell_names_in(value: Any) -> set[str]:
    out: set[str] = set()
    for r in refs_in(value):
        try:
            p = parse_path(r)
        except BadRef:
            continue
        if len(p) >= 2 and p[0] == "cells" and isinstance(p[1], str):
            out.add(p[1])
    return out


def uses_row(value: Any) -> bool:
    for r in refs_in(value):
        try:
            p = parse_path(r)
        except BadRef:
            continue
        if p and p[0] == "row":
            return True
    return False


def _walk(base: Any, path: list[str | int], expr: str) -> Any:
    cur = base
    for seg in path:
        if isinstance(seg, int):
            if not isinstance(cur, list):
                raise BadRef(expr, f"'{{{{{expr}}}}}': [{seg}] needs a list")
            if not -len(cur) <= seg < len(cur):
                raise BadRef(expr, f"'{{{{{expr}}}}}': index {seg} is out of range ({len(cur)} rows)")
            cur = cur[seg]
        else:
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            elif isinstance(cur, list) and seg in ("length", "count"):
                cur = len(cur)
            elif isinstance(cur, list) and cur and all(isinstance(x, dict) and seg in x for x in cur):
                # {{cells.c3.rows.wire_id}}: that field from every row (a change keyed on all of them)
                cur = [x[seg] for x in cur]
            elif isinstance(cur, list) and not cur:
                cur = []
            else:
                raise BadRef(expr, f"'{{{{{expr}}}}}': no field '{seg}'")
    return cur


class Resolver:
    """Resolves references for one cell execution.

    ``cell_value(name, key)`` returns the value of ``cells.<name>.<key>`` (raises ``KeyError`` when the cell or its
    result does not exist); ``row`` is the current for-each row (or None outside a loop).
    """

    def __init__(self, cell_value: Callable[[str, str], Any], row: Any = None, has_row: bool = False,
                 var: Callable[[str], Any] | None = None) -> None:
        self._cell_value = cell_value
        self.row = row
        self.has_row = has_row
        self._var = var  # name -> value (KeyError when unknown): extracted variables, then the environment

    def lookup(self, expr: str) -> Any:
        path = parse_path(expr)
        head = path[0]
        if head == "row":
            if not self.has_row:
                raise BadRef(expr, f"'{{{{{expr}}}}}' only works inside a for-each")
            return _walk(self.row, path[1:], expr)
        if head == "cells":
            if len(path) < 2 or not isinstance(path[1], str):
                raise BadRef(expr, f"'{{{{{expr}}}}}': name a cell, e.g. {{{{cells.c2.rows}}}}")
            name = path[1]
            key = path[2] if len(path) > 2 and isinstance(path[2], str) else "output"
            rest = path[3:] if len(path) > 2 and isinstance(path[2], str) else path[2:]
            try:
                base = self._cell_value(name, key)
            except KeyError as e:
                raise BadRef(expr, f"'{{{{{expr}}}}}': {e.args[0] if e.args else 'unknown reference'}") from None
            return _walk(base, rest, expr)
        if head == "vars" or (self._var is not None and isinstance(head, str)):
            name = path[1] if head == "vars" and len(path) > 1 else head
            rest = path[2:] if head == "vars" else path[1:]
            if not isinstance(name, str) or self._var is None:
                raise BadRef(expr, f"'{{{{{expr}}}}}': there are no variables here")
            try:
                base = self._var(name)
            except KeyError:
                raise BadRef(expr, f"'{{{{{expr}}}}}': no variable '{name}' (set it in an environment, or save it "
                                   "from an earlier request)") from None
            return _walk(base, rest, expr)
        raise BadRef(expr, f"'{{{{{expr}}}}}': references start with cells. or row.")

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            m = REF_RE.fullmatch(value.strip()) if "{{" in value else None
            if m and value.strip().count("{{") == 1:
                if m.group(1).strip().startswith("$"):
                    return value
                return self.lookup(m.group(1).strip())
            if "{{" not in value:
                return value

            def text(mm: re.Match[str]) -> str:
                expr = mm.group(1).strip()
                # leave engine expressions ({{ $json.x }}, {{ $vars.X }}) to the workflow runner
                if expr.startswith("$"):
                    return mm.group(0)
                v = self.lookup(expr)
                return v if isinstance(v, str) else json.dumps(v, default=str)

            return REF_RE.sub(text, value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value

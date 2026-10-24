"""Keep: turn notebook cells into a saved workflow (BUILD-PLAN §1.2 ``keep.py``). Deterministic; no LLM.

Mapping (one linear chain from the trigger; ``cN`` is always the node holding cell ``cN``'s result, one item):

- query    → ``cN_in`` (Code: resolve refs/params) → ``cN_run`` Postgres (operation query, ``connection``,
             ``queryParameters``) → ``cN`` (Code: ``{columns, rows, row_count, count}``)
- http     → ``cN_in`` → ``cN_run`` HttpRequest → ``cN`` (Code: checks ``expect``, fails the run on a mismatch)
- workflow → ``cN_in`` → ``cN_run`` ExecuteWorkflow (pinned version number) → ``cN``
- change   → ``cN_in`` → ``cN_ok`` Approval (unless the write is ``auto``) → ``cN_run`` Postgres
             ``UPDATE … WHERE key RETURNING *`` on the writable connection → ``cN``
- store    → ``cN_in`` → ``cN_run`` DataTable insert → ``cN``
- data / rule → ``cN`` (Code). A rule runs the *same* ``rule.py`` source the notebook uses, so the result is identical.
- for_each → ``cN_in`` (Code: one item per row) → ``cN_has`` If → body cells (per item) → ``cN`` (Code: ``{ok, failed, rows}``)
- note     → the workflow description. browser / ask → excluded with a reason (browser: exported as ``.spec.ts``).
  A removed kind (approval / person) is left out with a reason.

A guard becomes ``cN_if`` (If on the prep node's ``go``); its false branch skips to the next cell. Code nodes carry
their logic base64-encoded so ``{{…}}`` inside it is never read as an engine expression.

References: ``{{cells.cK.rows|count|output|<field>}}`` read node ``cK``'s item; ``{{row.x}}`` the for-each row;
``{{$input.name}}`` a ``mode: input`` param from the run's input (else its notebook value); ``{{ $vars.X }}`` and
other engine expressions are left to the engine. A data cell's rows can be replaced by passing ``{"cN": [...]}``.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.notebook_models import NotebookCellModel, NotebookModel
from ...schemas.notebook import KeepRequest, KeepResponse, KeepTrigger, notebook_error
from . import apitest as apitest_mod
from . import refs as R
from . import report as report_mod
from . import rule as rule_mod

# ── the widened Keep request / response (local subclasses; W1's schema keeps the three base targets) ────────────────

KeepTargetX = Literal["workflow", "test_suite", "scheduled_check", "dataset", "report"]


class KeepTriggerX(KeepTrigger):
    """``business_day``: a weekday cron (holidays are not skipped: there is no holiday calendar). ``mailbox``: coming.
    ``after_deploy``: a webhook the deploy pipeline calls (its URL is in the preview and the response)."""

    type: Literal["manual", "cron", "business_day", "mailbox", "after_deploy"] = "manual"


class KeepRequestX(KeepRequest):
    target: KeepTargetX
    trigger: KeepTriggerX = Field(default_factory=KeepTriggerX)
    dataset_table: str | None = None    # dataset: the data table to fill (default: from the notebook title)
    dataset_cell_id: str | None = None  # dataset: the cell whose rows go in (default: the last kept cell with rows)


class KeepResponseX(KeepResponse):
    target: KeepTargetX
    webhook_url: str | None = None
    table: str | None = None


NATIVE = {"query", "http", "workflow", "change", "store", "wait"}
CODE_ONLY = {"data", "rule"}
EXCLUDED_REASON = {
    "browser": "Browser steps can't be workflow steps yet. Export as test suite (.spec.ts) instead.",
    "ask": "Ask cells need AI; their planned cells are kept instead.",
    "approval": "This step type was removed; it is left out.",
    "person": "This step type was removed; it is left out.",
}
DEFAULT_CRON = "45 6 * * 1-5"
WAIT_MAX_S = 300  # the engine's Wait node caps a pause at 5 minutes
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_COL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ── the code that runs inside the kept workflow's Code nodes ─────────────────────────────────────────────────────────

RUNTIME = r'''
import re as _re
import json as _json

_REF = _re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SEG = _re.compile(r"(\$?[A-Za-z_][A-Za-z0-9_\-]*)|\[(-?\d+)\]")


class KeepError(Exception):
    pass


def _path(expr):
    return [m.group(1) if m.group(1) is not None else int(m.group(2)) for m in _SEG.finditer(expr)]


def _walk(cur, path, expr):
    for seg in path:
        if isinstance(seg, int):
            if isinstance(cur, list) and -len(cur) <= seg < len(cur):
                cur = cur[seg]
                continue
            raise KeepError("{{%s}}: index %s is out of range" % (expr, seg))
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
            continue
        if isinstance(cur, list) and seg in ("length", "count"):
            cur = len(cur)
            continue
        if isinstance(cur, list) and all(isinstance(x, dict) and seg in x for x in cur):
            cur = [x[seg] for x in cur]  # {{cells.c3.rows.wire_id}}: that field from every row
            continue
        raise KeepError("{{%s}}: no field %r" % (expr, seg))
    return cur


def _trigger():
    j = ((node_data.get(T["trigger"]) or {}).get("json")) or {}
    # a webhook and a manual POST /workflows/{id}/run both arrive as {body, headers, query, method}
    if T.get("trigger_kind") == "webhook" or ("body" in j and "method" in j):
        b = j.get("body")
        return b if isinstance(b, dict) else {}
    return j


def _cell_out(name, j):
    state = j.get("cells") or {}
    if name in state:
        out = state[name]
    elif name in node_data:
        out = node_data[name].get("json") or {}
    else:
        raise KeepError("cell %s did not run before this step" % name)
    if isinstance(out, dict) and out.get("__skipped"):
        raise KeepError("cell %s was skipped (its guard was false)" % name)
    return out


def _lookup(expr, j):
    p = _path(expr)
    if not p:
        raise KeepError("{{%s}}: empty reference" % expr)
    head = p[0]
    if head == "$input":
        name = p[1]
        trig = _trigger()
        if name in trig:
            base = trig[name]
        elif name in T.get("defaults", {}):
            base = T["defaults"][name]
        else:
            raise KeepError("input %r is missing" % name)
        return _walk(base, p[2:], expr)
    if head == "row":
        return _walk(j.get("row"), p[1:], expr)
    if head == "cells":
        name = p[1]
        has_key = len(p) > 2 and isinstance(p[2], str)
        key = p[2] if has_key else "output"
        rest = p[3:] if has_key else p[2:]
        out = _cell_out(name, j)
        if key == "output":
            base = out
        elif key == "rows":
            base = out.get("rows") or []
        elif key == "count":
            base = out["count"] if "count" in out else len(out.get("rows") or [])
        elif key in out:
            base = out[key]
        else:
            raise KeepError("{{%s}}: cell %s has no %r" % (expr, name, key))
        return _walk(base, rest, expr)
    if head == "vars" or head in T.get("var_names", ()):
        name = p[1] if head == "vars" and len(p) > 1 else head
        rest = p[2:] if head == "vars" else p[1:]
        for cn in reversed(T.get("var_cells") or []):
            try:
                o = _cell_out(cn, j)
            except KeepError:
                continue
            if name in (o.get("vars") or {}):
                return _walk(o["vars"][name], rest, expr)
        if name in (T.get("env") or {}):
            return _walk(T["env"][name], rest, expr)
        raise KeepError("{{%s}}: no variable %r" % (expr, name))
    raise KeepError("{{%s}}: references start with cells. or row." % expr)


def _res(v, j):
    if isinstance(v, str):
        if "{{" not in v:
            return v
        s = v.strip()
        m = _REF.fullmatch(s)
        if m and s.count("{{") == 1:
            e = m.group(1).strip()
            if e.startswith("$") and not e.startswith("$input."):
                return v
            return _lookup(e, j)

        def t(mm):
            e = mm.group(1).strip()
            if e.startswith("$") and not e.startswith("$input."):
                return mm.group(0)
            x = _lookup(e, j)
            return x if isinstance(x, str) else _json.dumps(x, default=str)

        return _REF.sub(t, v)
    if isinstance(v, dict):
        return {k: _res(x, j) for k, x in v.items()}
    if isinstance(v, list):
        return [_res(x, j) for x in v]
    return v


def _cols(rows):
    out = []
    for r in rows[:200]:
        if isinstance(r, dict):
            for k in r:
                if k not in out:
                    out.append(k)
    return out


def _go(j):
    g = T.get("guard")
    if not g:
        return True
    g = _res(g, j)
    return guard_holds(g.get("left"), g.get("op") or "nonempty", g.get("right"))


def _carry(o, src):
    """A body cell's output item also carries the loop row and the body results so far."""
    if not T.get("body"):
        return o
    cells = dict(src.get("cells") or {})
    cells[T["cell"]] = o
    return {**o, "row": src.get("row"), "i": src.get("i"), "cells": cells}


def _jpath(obj, path):
    cur = obj
    for part in [x for x in _re.split(r"[.\[\]]", path.lstrip("$")) if x]:
        if isinstance(cur, list) and part.lstrip("-").isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _shape(kind, nj, pj):
    v = pj.get("v") or {}
    if kind == "query":
        rows = nj.get("rows") or []
        return {"columns": _cols(rows), "rows": rows, "row_count": len(rows), "count": len(rows),
                "truncated": bool(nj.get("truncated"))}
    if kind == "http":
        status, body, hdrs = nj.get("statusCode"), nj.get("body"), nj.get("headers") or {}
        exp = v.get("expect") or {}
        if exp.get("status") is not None and status != exp["status"]:
            raise KeepError("%s: expected HTTP %s, got %s" % (T["cell"], exp["status"], status))
        for path, want in (exp.get("json_path_equals") or {}).items():
            got = _jpath(body, path)
            if got != want:
                raise KeepError("%s: %s is %r, expected %r" % (T["cell"], path, got, want))
        o = {}
        if v.get("assertions"):
            results, problems = check_all({"assertions": v["assertions"]}, status, hdrs, body, None)
            if problems:
                raise KeepError("%s: %s" % (T["cell"], "; ".join(problems)))
            o["assertions"] = results
        if v.get("extract"):
            saved, problems = extract(v["extract"], status, hdrs, body)
            if problems:
                raise KeepError("%s: %s" % (T["cell"], "; ".join(problems)))
            o["vars"] = saved
        rows = body if isinstance(body, list) else ([body] if isinstance(body, dict) else [])
        rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
        return {"status": status, "body": body, "rows": rows, "count": len(rows), **o}
    if kind == "change":
        rows = nj.get("rows") or []
        return {"rows": rows, "count": len(rows), "applied": True}
    if kind == "store":
        return {"rows": [], "count": 1}
    if kind == "wait":
        return {"waited": v.get("duration"), "rows": [], "count": 0}
    return dict(nj)


def _coerce(t, x):
    if x is None:
        return None
    if t == "json":
        return x
    if t == "boolean":
        return x if isinstance(x, bool) else None
    if t == "number":
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return x
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    return x if isinstance(x, str) else (_json.dumps(x, default=str) if isinstance(x, (dict, list)) else str(x))


def _report_cell(c):
    j = (node_data.get(c["name"]) or {}).get("json")
    status = None
    if isinstance(j, dict):
        status = "guard_false" if j.get("__skipped") else "success"
    return {"name": c["name"], "kind": c["kind"], "title": c.get("title"), "markdown": c.get("markdown"),
            "status": status, "output": j if status == "success" else None, "error": None,
            "children": [_report_cell(x) for x in c.get("children") or []]}


op = T["op"]
if op == "prep":
    out = []
    for it in (items or [{"json": {}}]):
        j = it.get("json") or {}
        nj = {"row": j.get("row"), "i": j.get("i"), "cells": j.get("cells") or {}, "go": True, "v": None}
        nj["go"] = _go(j)
        if nj["go"]:
            v = _res(T["spec"], j)
            nj["v"] = v
            if T["kind"] == "query":
                nj["p"] = v.get("params") or {}
            elif T["kind"] == "http" and T.get("http_req"):
                b = build_request({k: x for k, x in v.items() if k not in ("auth", "credential_slot")})
                nj["req"] = {"url": b["url"], "body": b["body"]}
                if T.get("auth_query"):  # the secret goes after it in the node's URL: end with ? or &
                    nj["req"]["url"] += "&" if "?" in b["url"] else "?"
            elif T["kind"] == "change":
                p = {}
                for i, c in enumerate(T["key_cols"]):
                    kv = v["key"][c]
                    if c in (T.get("list_cols") or []):  # "any of these" keys: text[] for ``::text = ANY(...)``
                        kv = [None if x is None else str(x) for x in (kv if isinstance(kv, list) else [kv])]
                    p["k%d" % i] = kv
                for i, c in enumerate(T["set_cols"]):
                    p["s%d" % i] = v["set"][c]
                nj["p"] = p
        out.append({"json": nj})
    __result__ = out
elif op == "post":
    srcs = (node_data.get(T["src"]) or {}).get("data") or []
    out = []
    if T["kind"] == "workflow" and not T.get("body"):
        rows = [it.get("json") or {} for it in items]
        o = {"status": "success", "rows": rows, "count": len(rows), "output": rows[0] if len(rows) == 1 else None}
        out = [{"json": o}]
    else:
        for i, it in enumerate(items):
            nj = it.get("json") or {}
            pj = srcs[i] if i < len(srcs) else nj
            out.append({"json": _carry(_shape(T["kind"], nj, pj), pj)})
    if T.get("stop_empty") and out and not out[0]["json"].get("rows"):
        out = []
    __result__ = out
elif op == "cell":
    out = []
    for it in (items or [{"json": {}}]):
        j = it.get("json") or {}
        if not _go(j):
            out.append({"json": _carry({"__skipped": True}, j)})
            continue
        v = _res(T["spec"], j)
        if T["kind"] == "data":
            given = _trigger().get(T["cell"])
            rows = given if isinstance(given, list) else (v.get("rows") or [])
            o = {"columns": v.get("columns") or _cols(rows), "rows": rows, "row_count": len(rows), "count": len(rows)}
        else:
            r = evaluate(v)
            if v.get("op") == "assert" and not r.get("passed"):
                raise KeepError("rule %s failed: %s" % (T["cell"], r.get("summary")))
            o = dict(r)
            o.setdefault("count", len(r.get("rows") or []))
            o.setdefault("columns", _cols(r.get("rows") or []))
            o["row_count"] = len(r.get("rows") or [])
        out.append({"json": _carry(o, j)})
    if T.get("stop_empty") and out and not out[0]["json"].get("__skipped") and not out[0]["json"].get("rows"):
        out = []
    __result__ = out
elif op == "each":
    j = (items[0].get("json") if items else {}) or {}
    v = _res(T["spec"], j)
    over = v.get("over")
    if isinstance(over, dict) and isinstance(over.get("rows"), list):
        over = over["rows"]
    if not isinstance(over, list):
        raise KeepError("%s: 'over' must be a list of rows" % T["cell"])
    cap = int(v.get("max_rows") or 500)
    if len(over) > cap:
        raise KeepError("%s: %d rows is over the for-each cap of %d" % (T["cell"], len(over), cap))
    if not over:
        __result__ = [{"json": {"__empty": True}}]
    else:
        __result__ = [{"json": {"row": r, "i": i, "cells": {}}} for i, r in enumerate(over)]
elif op == "pick":
    src = node_data.get(T["src"]) or {}
    o = src.get("json") or {}
    out = []
    if isinstance(o, dict) and not o.get("__skipped"):
        for r in (o.get("rows") or []):
            r = r if isinstance(r, dict) else {"value": r}
            out.append({"json": {c["name"]: _coerce(c["type"], r.get(c["from"])) for c in T["columns"]}})
    __result__ = out
elif op == "report":
    md = render(T["title"], [_report_cell(c) for c in T["cells"]])
    __result__ = [{"json": {"title": T["title"], "markdown": md}}]
elif op == "collapse":
    rows = []
    for it in items:
        j = it.get("json") or {}
        if j.get("__empty"):
            continue
        rows.append({"i": j.get("i", len(rows)), "status": "success"})
    __result__ = [{"json": {"ok": len(rows), "failed": 0, "rows": rows, "count": len(rows)}}]
else:
    raise KeepError("unknown keep op %r" % op)
'''


def _source(mod: Any) -> str:
    src = inspect.getsource(mod)
    return "\n".join(line for line in src.splitlines() if not line.startswith("from __future__"))


_RUNTIME_B64: dict[bool, str] = {}


def _runtime_b64(with_report: bool = False) -> str:
    """rule.py + apitest.py (+ report.py for the report node) + RUNTIME, base64. All pure stdlib by design."""
    if with_report not in _RUNTIME_B64:
        parts = [_source(rule_mod), _source(apitest_mod)] + ([_source(report_mod)] if with_report else []) + [RUNTIME]
        _RUNTIME_B64[with_report] = base64.b64encode("\n\n".join(parts).encode()).decode()
    return _RUNTIME_B64[with_report]


def code_for(task: dict[str, Any], label: str) -> str:
    """A Code node's ``code``: the task (base64 JSON) plus the shared runtime (base64)."""
    t = base64.b64encode(json.dumps(task, default=str, sort_keys=True).encode()).decode()
    label = re.sub(r"[\r\n]+", " ", label)[:160]
    return (f"# Kept from a notebook: {label}\n"
            "# Generated by Keep (deterministic, no AI). Rule logic is the notebook's own rule.py; do not edit by hand.\n"
            f"__t = json.loads(base64.b64decode(\"{t}\").decode())\n"
            "__ns = {\"items\": items, \"node_data\": node_data, \"T\": __t}\n"
            f"exec(base64.b64decode(\"{_runtime_b64(task.get('op') == 'report')}\").decode(), __ns)\n"
            "return __ns[\"__result__\"]")


# ── the graph builder ────────────────────────────────────────────────────────────────────────────────────────────────


class _Graph:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.connections: list[dict[str, Any]] = []
        self.col = 0

    def add(self, name: str, type_: str, params: dict[str, Any], label: str | None = None, row: int = 0) -> str:
        self.nodes.append({"name": name, "type": type_, "label": label, "parameters": params,
                           "position": {"x": 80 + 240 * self.col, "y": 120 + 160 * row}})
        self.col += 1
        return name

    def link(self, tails: list[tuple[str, str]], target: str) -> None:
        for src, port in tails:
            self.connections.append({"source_node": src, "target_node": target, "source_output": port,
                                     "target_input": "main"})


def _set_path(obj: Any, path: list[str | int], value: Any) -> bool:
    cur = obj
    for seg in path[:-1]:
        try:
            cur = cur[seg]
        except (KeyError, IndexError, TypeError):
            return False
    try:
        cur[path[-1]]  # must exist
        cur[path[-1]] = value
        return True
    except (KeyError, IndexError, TypeError):
        return False


def _get_path(obj: Any, path: list[str | int]) -> Any:
    cur = obj
    for seg in path:
        cur = cur[seg]
    return cur


def _param_path(ref: str) -> tuple[str, list[str | int]]:
    """``"c2.spec.params.days"`` → (``"c2"``, ``["params", "days"]``)."""
    parts = R.parse_path(ref)
    if len(parts) < 3 or parts[1] != "spec" or not isinstance(parts[0], str):
        raise ValueError(f"param ref {ref!r} must look like c2.spec.params.days")
    return parts[0], parts[2:]


def param_candidates(cells: list[NotebookCellModel]) -> list[dict[str, Any]]:
    """Literal values a kept workflow could take as an input: query params and workflow inputs."""
    out = []
    for c in cells:
        spec = c.spec or {}
        bag = spec.get("params") if c.kind == "query" else spec.get("inputs") if c.kind == "workflow" else None
        sub = "params" if c.kind == "query" else "inputs"
        for k, v in (bag or {}).items():
            if isinstance(v, (str, int, float, bool)) and not (isinstance(v, str) and "{{" in v):
                out.append({"ref": f"{c.name}.spec.{sub}.{k}", "cell": c, "name": k, "value": v})
    return out


# ── preview / compile ────────────────────────────────────────────────────────────────────────────────────────────────


def _cron_time(cron: str | None) -> tuple[str, str]:
    parts = (cron or "").split()
    return (parts[0], parts[1]) if len(parts) == 5 else ("45", "6")


def webhook_path_for(nb: NotebookModel) -> str:
    """The after-deploy webhook path: unique per keep of this notebook (the n-th keep gets ``-n``)."""
    return f"notebook-{re.sub(r'[^A-Za-z0-9_-]', '-', nb.id)}-deploy-{len(nb.kept or []) + 1}"


def trigger_plan(nb: NotebookModel, body: Any) -> dict[str, Any]:
    """``{type, cron, webhook_path, webhook_url, status, message, publish}`` for the request's trigger + target."""
    t = body.trigger.type
    target = body.target
    out: dict[str, Any] = {"type": t, "cron": None, "webhook_path": None, "webhook_url": None, "status": "ok",
                           "publish": False}
    scheduled_target = target in ("scheduled_check", "report")
    # Manual chosen explicitly: never publish a schedule (a draft that runs when started). Only an omitted trigger
    # gives a scheduled target its default cron.
    explicit_manual = t == "manual" and "trigger" in getattr(body, "model_fields_set", set())
    if t == "business_day":
        m, h = _cron_time(body.trigger.cron)
        out["cron"] = f"{m} {h} * * 1-5"
        out["message"] = (f"Runs every business day at {int(h):02d}:{int(m):02d} (`{out['cron']}`). Holidays are not "
                          "skipped: no holiday calendar is set up." if m.isdigit() and h.isdigit() else
                          f"Runs every business day (`{out['cron']}`). Holidays are not skipped: no holiday calendar "
                          "is set up.")
        out["publish"] = target != "test_suite"
    elif t == "after_deploy":
        out["webhook_path"] = webhook_path_for(nb)
        out["webhook_url"] = f"/webhook/p/{out['webhook_path']}"
        out["message"] = (f"Runs after each deploy: the pipeline sends POST {out['webhook_url']} (JSON body fields "
                          "become the inputs).")
        out["publish"] = target != "test_suite"
    elif t == "mailbox":
        out["status"] = "needs_choice"
        out["message"] = ("A mailbox trigger is coming (there is no email trigger yet). Until then this runs when "
                          "started; pick another trigger to schedule it.")
    elif t == "cron" and body.trigger.cron:
        out["cron"] = body.trigger.cron
        out["publish"] = target != "test_suite" and target != "workflow"
        out["message"] = f"Runs on schedule `{out['cron']}`" + ("" if out["publish"] else " once published")
    elif explicit_manual and scheduled_target:
        out["message"] = ("Saved as a draft: runs when started (manual or from another workflow). Pick a schedule to "
                          "activate it.")
    elif scheduled_target or (target == "dataset" and t == "cron"):
        out["cron"] = DEFAULT_CRON
        out["publish"] = True
        out["message"] = f"Runs on schedule `{DEFAULT_CRON}` (default: weekdays 06:45)"
    else:
        out["message"] = "Runs when started (manual or from another workflow)"
    if target == "test_suite":
        out["cron"], out["webhook_path"], out["webhook_url"], out["publish"] = None, None, None, False
        out["status"] = "ok"
        out["message"] = "Runs as tests (the suite's Run tests button, or POST …/tests/run)"
    return out


class KeepPlan:
    def __init__(self) -> None:
        self.trigger: dict[str, Any] = {}
        self.dataset: dict[str, Any] | None = None
        self.items: list[dict[str, Any]] = []
        self.definition: dict[str, Any] = {}
        self.blocked = False
        self.unbound: list[dict[str, Any]] = []
        self.included: list[NotebookCellModel] = []
        self.browser_cells: list[NotebookCellModel] = []

    def item(self, key: str, kind: str, status: str, message: str, cell_id: str | None = None,
             choices: list[dict[str, Any]] | None = None) -> None:
        self.items.append({"key": key, "kind": kind, "status": status, "message": message, "cell_id": cell_id,
                           "choices": choices})
        if status == "blocked":
            self.blocked = True

    @property
    def can_save(self) -> bool:
        return not self.blocked and bool(self.included)

    @property
    def can_schedule(self) -> bool:
        return self.can_save and not self.unbound


async def _secret_keys(session: AsyncSession, nb: NotebookModel) -> list[str]:
    from ...db.models import VariableModel as V

    rows = (await session.execute(select(V.key).where(V.type == "secret", V.team_id == (nb.team_id or "default"))
                                  .where((V.project_id == nb.project_id) | (V.project_id.is_(None))).distinct())
            ).scalars().all()
    return sorted(set(rows))


async def _pinned_version(session: AsyncSession, spec: dict[str, Any]) -> int | None:
    from ...db.models import WorkflowModel, WorkflowVersionModel

    v = spec.get("version", "published")
    if isinstance(v, int):
        return v
    wf = await session.get(WorkflowModel, spec.get("workflow_id"))
    if wf is None or not wf.published_version_id:
        return None
    ver = await session.get(WorkflowVersionModel, wf.published_version_id)
    return ver.version_number if ver else None


async def _table_name(session: AsyncSession, spec: dict[str, Any]) -> str | None:
    if spec.get("new_table"):
        return str(spec["new_table"])
    from ...db.models import DataTableModel

    t = await session.get(DataTableModel, spec.get("table_id")) if spec.get("table_id") else None
    return t.name if t else None


async def build(session: AsyncSession, nb: NotebookModel, cells: list[NotebookCellModel], body: KeepRequestX,
                latest: dict[str, Any] | None = None) -> KeepPlan:
    """Strip items + the workflow definition for ``body``. Pure apart from DB reads; no LLM, no writes."""
    from .service import resolve_connection

    plan = KeepPlan()
    top = sorted([c for c in cells if c.parent_cell_id is None], key=lambda c: c.position)
    kids: dict[str, list[NotebookCellModel]] = {}
    for c in cells:
        if c.parent_cell_id:
            kids.setdefault(c.parent_cell_id, []).append(c)
    for v in kids.values():
        v.sort(key=lambda c: c.position)
    by_id = {c.id: c for c in cells}
    by_name = {c.name: c for c in cells}

    wanted = set(body.cell_ids or [])
    unknown = sorted(wanted - set(by_id))
    if unknown:
        raise notebook_error(422, "invalid", f"cells not in this notebook: {', '.join(unknown)}")
    chosen = [c for c in top if not wanted or c.id in wanted]

    # excluded cells
    notes: list[str] = []
    keep_cells: list[NotebookCellModel] = []
    for c in chosen:
        if c.kind == "note":
            if (c.spec or {}).get("markdown"):
                notes.append(str(c.spec["markdown"]))
            continue
        if c.kind in EXCLUDED_REASON:
            if c.kind == "browser":
                plan.browser_cells.append(c)
                msg = ("Exported to the .spec.ts file with this suite." if body.target == "test_suite"
                       else EXCLUDED_REASON["browser"])
            else:
                msg = EXCLUDED_REASON[c.kind]
            plan.item(f"excluded:{c.id}", "excluded", "ok", f"{c.name} {c.title or c.kind}: {msg}", c.id)
            continue
        keep_cells.append(c)
    kept_names = {c.name for c in keep_cells} | {ch.name for c in keep_cells for ch in kids.get(c.id, [])}

    # references to cells that are not kept
    for c in keep_cells:
        for x in [c] + kids.get(c.id, []):
            missing = sorted(n for n in R.cell_names_in({"s": x.spec, "g": x.guard}) if n not in kept_names)
            if missing:
                plan.item(f"excluded:{x.id}:refs", "excluded", "blocked",
                          f"{x.name} uses {', '.join(missing)}, which is not kept. Keep {', '.join(missing)} too, "
                          "or remove the reference.", x.id)
            if x.kind == "query" and "{{" in str((x.spec or {}).get("sql")):
                plan.item(f"excluded:{x.id}:sql", "excluded", "blocked",
                          f"{x.name}: put referenced values in params (%(name)s), not inside the SQL text.", x.id)
            if x.parent_cell_id and x.guard:
                plan.item(f"excluded:{x.id}:guard", "excluded", "blocked",
                          f"{x.name}: a guard inside a for-each body can't be kept yet.", x.id)
            if x.kind == "store" and (x.spec or {}).get("mode") == "upsert":
                plan.item(f"excluded:{x.id}:upsert", "excluded", "blocked",
                          f"{x.name}: an upsert into a data table can't be kept yet; use append.", x.id)
    plan.included = keep_cells
    if not keep_cells:
        plan.item("excluded:none", "excluded", "blocked", "Nothing here can become a workflow step.")

    # params
    chosen_params = {p.ref: p for p in body.params}
    defaults: dict[str, Any] = {}
    overrides: dict[str, tuple[list[str | int], str]] = {}  # cell name -> (path, input name)
    param_overrides: dict[str, list[tuple[list[str | int], str]]] = {}
    for cand in param_candidates(keep_cells + [ch for c in keep_cells for ch in kids.get(c.id, [])]):
        p = chosen_params.pop(cand["ref"], None)
        mode = p.mode if p else "fixed"
        name = (p.name if p and p.name else cand["name"])
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            plan.item(f"params:{cand['ref']}", "params", "blocked", f"input name {name!r} must be a plain identifier",
                      cand["cell"].id)
            continue
        if mode == "input":
            defaults[name] = cand["value"]
            _, path = _param_path(cand["ref"])
            param_overrides.setdefault(cand["cell"].name, []).append((path, name))
            msg = f"{cand['ref']} is an input `{name}` (default {cand['value']!r})"
        else:
            msg = f"{cand['ref']} = {cand['value']!r} (fixed)"
        plan.item(f"params:{cand['ref']}", "params", "ok", msg, cand["cell"].id,
                  [{"value": "fixed", "label": "Fixed"}, {"value": "input", "label": "Input"}])
    for ref in chosen_params:
        plan.item(f"params:{ref}", "params", "blocked", f"{ref} is not a literal value in a kept cell")
    del overrides

    # trigger
    trig = trigger_plan(nb, body)
    cron, webhook_path = trig["cron"], trig["webhook_path"]
    plan.trigger = trig
    plan.item("trigger", "trigger", trig["status"], trig["message"], None, [
        {"value": "manual", "label": "When started"}, {"value": "cron", "label": "On a schedule"},
        {"value": "business_day", "label": "Every business day"}, {"value": "after_deploy", "label": "After a deploy"},
        {"value": "mailbox", "label": "When an email arrives (coming)"}])

    # slots
    secret_keys: list[str] | None = None
    slot_choice = {s.cell_id: s.credential_id for s in body.slots}
    slot_var: dict[str, str] = {}
    for c in keep_cells + [ch for c in keep_cells for ch in kids.get(c.id, [])]:
        slot = (c.spec or {}).get("credential_slot")
        if not slot:
            continue
        if secret_keys is None:
            secret_keys = await _secret_keys(session, nb)
        chosen_key = slot_choice.get(c.id)
        choices = [{"value": k, "label": k} for k in secret_keys]
        if chosen_key and chosen_key in secret_keys:
            slot_var[c.id] = chosen_key
            plan.item(f"slots:{c.id}", "slots", "ok", f"{c.name} signs in with the secret {chosen_key}", c.id, choices)
        else:
            msg = (f"{c.name} needs a credential for '{slot}'. Pick a secret"
                   + (f" ({chosen_key!r} is not a secret here)" if chosen_key else "") + ".")
            plan.item(f"slots:{c.id}", "slots", "needs_choice", msg, c.id, choices)
            plan.unbound.append({"key": f"slots:{c.id}", "cell_id": c.id, "slot": slot})

    # run_as
    run_as = body.run_as or nb.run_as
    plan.item("run_as", "run_as", "ok", f"Runs as {run_as}" if run_as else "Runs as the person who starts it")

    # writes
    write_mode = {w.cell_id: w.mode for w in body.writes}
    all_cells = keep_cells + [ch for c in keep_cells for ch in kids.get(c.id, [])]
    for c in all_cells:
        if c.kind == "change":
            m = write_mode.get(c.id, "approval")
            plan.item(f"writes:{c.id}", "writes", "ok",
                      f"{c.name} changes {c.spec.get('table')}: " + ("waits for an approval each time" if m == "approval"
                                                                     else "applies automatically"),
                      c.id, [{"value": "approval", "label": "Approval each time"}, {"value": "auto", "label": "Auto"}])
    has_rows = any(c.kind in ("query", "rule") for c in keep_cells)
    if has_rows and body.target == "report":
        plan.item("empty_policy", "empty_policy", "ok", "The report always renders, even when a query finds no rows")
    elif has_rows:
        plan.item("empty_policy", "empty_policy", "ok",
                  "When a query or rule finds no rows, the run stops quietly" if body.empty_policy == "stop_quietly"
                  else "When a query or rule finds no rows, the run continues",
                  None, [{"value": "stop_quietly", "label": "Stop quietly"}, {"value": "continue", "label": "Continue"}])

    # test_suite needs a run to copy
    latest = latest or {}
    if body.target == "test_suite":
        missing = [c.name for c in all_cells if c.id not in latest or latest[c.id].status not in
                   ("success", "applied", "needs_approval", "waiting", "guard_false")]
        failed = [c.name for c in all_cells if c.id in latest and latest[c.id].status == "error"]
        if failed:
            plan.item("excluded:run", "excluded", "blocked",
                      f"{', '.join(failed)} failed in the last run; a test suite copies a passing run, so fix or "
                      "remove those checks and run again.")
        elif missing:
            plan.item("excluded:run", "excluded", "blocked",
                      f"Run the notebook first: the tests copy a run's results ({', '.join(missing)} has none).")

    # the graph
    g = _Graph()
    trigger_name = "Schedule" if cron else "Deploy" if webhook_path else "Start"
    if cron:
        g.add(trigger_name, "Cron", {"mode": "cron", "cronExpression": cron}, "Schedule")
    elif webhook_path:
        g.add(trigger_name, "Webhook", {"method": "POST", "path": webhook_path, "responseMode": "onReceived"},
              "After a deploy")
    else:
        props = {k: {"type": "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else
                     "boolean" if isinstance(v, bool) else "string", "default": v} for k, v in defaults.items()}
        g.add(trigger_name, "Start", {"input_schema": {"type": "object", "properties": props}} if props else {}, "Start")
    tails: list[tuple[str, str]] = [(trigger_name, "main")]
    stop_empty = body.empty_policy == "stop_quietly" and body.target != "report"
    from .runner import load_env

    env_vars = await load_env(session, nb.id)
    var_cells = [x.name for c in keep_cells for x in [c] + kids.get(c.id, [])
                 if x.kind == "http" and (x.spec or {}).get("extract")]
    var_names = sorted(set(env_vars) | {e.get("name") for c in keep_cells for x in [c] + kids.get(c.id, [])
                                        if x.kind == "http" for e in (x.spec or {}).get("extract") or []})
    ctx = {"trigger": trigger_name, "trigger_kind": "webhook" if webhook_path else "cron" if cron else "start",
           "defaults": defaults}
    if env_vars or var_names:  # only then, so a kept graph without variables is byte-identical to before
        ctx.update({"env": env_vars, "var_cells": var_cells, "var_names": var_names})
    mocks: dict[str, dict[str, Any]] = {}
    expects: list[dict[str, Any]] = []

    async def spec_for(c: NotebookCellModel) -> dict[str, Any]:
        spec = json.loads(json.dumps(c.spec or {}, default=str))
        for path, name in param_overrides.get(c.name, []):
            _set_path(spec, path, "{{$input.%s}}" % name)
        return spec

    async def add_cell(c: NotebookCellModel, tails: list[tuple[str, str]], body_of: str | None, row: int
                       ) -> list[tuple[str, str]]:
        spec = await spec_for(c)
        label = f"{c.name} {c.title or c.kind}"
        in_body = body_of is not None
        base_task = {**ctx, "cell": c.name, "kind": c.kind, "spec": spec, "guard": c.guard, "body": in_body}
        lr = latest.get(c.id)
        out = (lr.output if lr is not None else None) or {}
        if c.kind in CODE_ONLY:
            se = stop_empty and not in_body and (c.kind == "rule" and spec.get("op") in ("filter", "assert"))
            name = g.add(c.name, "Code", {"code": code_for({**base_task, "op": "cell", "stop_empty": se}, label)},
                         c.title or c.name, row)
            g.link(tails, name)
            if lr is not None and not in_body and lr.status == "success":
                if "count" in out or "row_count" in out:
                    expects.append({"node": c.name, "path": "count", "op": "eq",
                                    "value": out.get("count", out.get("row_count"))})
            return [(name, "main")]
        if c.kind == "for_each":
            first = g.add(f"{c.name}_in", "Code", {"code": code_for({**base_task, "op": "each"}, label)},
                          f"{c.title or c.name} (rows)", row)
            g.link(tails, first)
            has = g.add(f"{c.name}_has", "If", {"field": "__empty", "operation": "isFalse"}, "Any rows?", row)
            g.link([(first, "main")], has)
            inner: list[tuple[str, str]] = [(has, "true")]
            for ch in kids.get(c.id, []):
                inner = await add_cell(ch, inner, c.name, row + 1)
            coll = g.add(c.name, "Code", {"code": code_for({**base_task, "op": "collapse"}, label)},
                         c.title or c.name, row)
            g.link(inner + [(has, "false")], coll)
            return [(coll, "main")]
        # native kinds
        prep = g.add(f"{c.name}_in", "Code", {"code": code_for({
            **base_task, "op": "prep",
            **({"http_req": True, "auth_query": (spec.get("auth") or {}).get("type") == "api_key" and (
                spec.get("auth") or {}).get("add_to") == "query"} if c.kind == "http" and (
                spec.get("params") or spec.get("body_type") == "form" or (
                    (spec.get("auth") or {}).get("type") == "api_key" and (spec.get("auth") or {}).get("add_to") == "query"))
               else {}),
            "key_cols": list((spec.get("key") or {}).keys()) if c.kind == "change" else [],
            "list_cols": _list_key_cols(spec, out if lr is not None else None) if c.kind == "change" else [],
            "set_cols": list((spec.get("set") or {}).keys()) if c.kind == "change" else []}, label)},
            f"{c.title or c.name} (inputs)", row)
        g.link(tails, prep)
        cur: list[tuple[str, str]] = [(prep, "main")]
        skip: list[tuple[str, str]] = []
        src = prep
        if c.guard:
            gi = g.add(f"{c.name}_if", "If", {"field": "go", "operation": "isTrue"}, f"Only if: {c.name} guard", row)
            g.link(cur, gi)
            cur, skip = [(gi, "true")], [(gi, "false")]
            src = gi
        run_name = f"{c.name}_run"
        if c.kind == "query":
            conn = await resolve_connection(session, nb.project_id, spec.get("connection"))
            if conn is None:
                plan.item(f"excluded:{c.id}:conn", "excluded", "blocked", f"{c.name}: connection not found", c.id)
                key = str(spec.get("connection"))
            else:
                key = conn.key
            g.add(run_name, "Postgres", {"operation": "query", "connection": key, "query": spec.get("sql"),
                                         "queryParameters": "{{ $json.p }}", "limit": int(spec.get("limit") or 500)},
                  c.title or c.name, row)
            if lr is not None:
                rows = _full_rows(lr)
                mocks[run_name] = {"items": [{"rows": rows, "rowCount": len(rows), "truncated": bool(out.get("truncated"))}]}
        elif c.kind == "http":
            headers = [{"name": k, "value": _static_secret_header(v, env_vars) if _secret_var_in(v, env_vars) else
                        "{{ $json.v.headers.%s }}" % k if re.match(r"^\w+$", k) else
                        '{{ $json.v.headers["%s"] }}' % k if R.refs_in(v) and re.match(r"^[A-Za-z0-9_.\-]+$", k) else v}
                       for k, v in (spec.get("headers") or {}).items()]
            if c.id in slot_var:
                headers.append({"name": "Authorization", "value": "Bearer {{ $vars.%s }}" % slot_var[c.id]})
            form = spec.get("body") is not None and spec.get("body_type") == "form"
            try:
                a_headers, a_query = apitest_mod.auth_parts({"auth": spec.get("auth")})
            except apitest_mod.ApiTestError as e:
                plan.item(f"excluded:{c.id}:auth", "excluded", "blocked", f"{c.name}: {e}", c.id)
                a_headers, a_query = [], []
            if form and not any(k.lower() == "content-type" for k in (spec.get("headers") or {})):
                headers.append({"name": "Content-Type", "value": "application/x-www-form-urlencoded"})
            for k, val in a_headers:
                headers = [h for h in headers if h["name"].lower() != k.lower()] + [{"name": k, "value": val}]
            needs_req = bool(spec.get("params") or a_query or form)
            params: dict[str, Any] = {"method": spec.get("method") or "GET", "headers": headers, "responseType": "json"}
            if spec.get("api_ref"):
                params["api_ref"] = spec["api_ref"]
            if spec.get("url") and needs_req:
                params["url"] = "{{ $json.req.url }}" + "&".join(f"{k}={val}" for k, val in a_query)
            else:
                params["url"] = "{{ $json.v.url }}" if spec.get("url") else ""
            if spec.get("body") is not None:
                params["body"] = "{{ $json.req.body }}" if form else "{{ $json.v.body }}"
            if _secret_var_in({k: spec.get(k) for k in ("url", "params", "body")}, env_vars):
                plan.item(f"excluded:{c.id}:urlsecret", "excluded", "blocked",
                          f"{c.name}: a secret variable in the URL, params or body can't be kept; sign in with Auth "
                          "instead", c.id)
            g.add(run_name, "HttpRequest", params, c.title or c.name, row)
            if lr is not None:
                mbody = out.get("body") if out.get("body") is not None else (_full_rows(lr) or None)
                mock: dict[str, Any] = {"status": out.get("status") or 200, "body": mbody}
                if out.get("headers"):
                    mock["headers"] = {k: v for k, v in out["headers"].items() if v != "(hidden)"}
                mocks[run_name] = {"http": mock}
        elif c.kind == "workflow":
            ver = await _pinned_version(session, spec)
            if ver is None:
                plan.item(f"excluded:{c.id}:version", "excluded", "blocked",
                          f"{c.name}: workflow {spec.get('workflow_id')} has no published version to pin", c.id)
            g.add(run_name, "ExecuteWorkflow", {"workflowId": spec.get("workflow_id"), "version": ver or "latest",
                                                "inputMode": "custom", "customInput": "{{ $json.v.inputs }}",
                                                "mode": "each" if in_body else "once", "outputMode": "items"},
                  c.title or c.name, row)
            if lr is not None:
                mocks[run_name] = {"items": list(out.get("rows") or [out.get("output") or {}])}
        elif c.kind == "change":
            table = str(spec.get("table") or "")
            cols = list((spec.get("key") or {}).keys()) + list((spec.get("set") or {}).keys())
            if not _IDENT.match(table) or not all(_COL.match(x) for x in cols):
                plan.item(f"excluded:{c.id}:ident", "excluded", "blocked",
                          f"{c.name}: table and column names must be plain identifiers", c.id)
            conn = await resolve_connection(session, nb.project_id, spec.get("connection"))
            if conn is not None and conn.readonly:
                plan.item(f"excluded:{c.id}:ro", "excluded", "blocked",
                          f"{c.name}: connection {conn.key} is read-only", c.id)
            qt = ".".join(f'"{p}"' for p in table.split("."))
            sets = ", ".join(f'"{col}" = %(s{i})s' for i, col in enumerate((spec.get("set") or {}).keys()))
            lists = set(_list_key_cols(spec, out if lr is not None else None))
            where = " AND ".join((f'"{col}"::text = ANY(%(k{i})s::text[])' if col in lists else f'"{col}" = %(k{i})s')
                                 for i, col in enumerate((spec.get("key") or {}).keys()))
            if write_mode.get(c.id, "approval") == "approval":
                ok = g.add(f"{c.name}_ok", "Approval", {
                    "title": c.title or f"Apply the change to {table}", "per_item": in_body, "assignees": [],
                    "context": {"table": table, "key": "{{ $json.v.key }}", "set": "{{ $json.v.set }}",
                                "reason": spec.get("reason") or ""}}, f"Approve {c.name}", row)
                g.link(cur, ok)
                cur = [(ok, "approved")]
                src = ok
                mocks[ok] = {"approval": {"decision": "approved"}}
            g.add(run_name, "Postgres", {"operation": "query", "connection": conn.key if conn else str(spec.get("connection")),
                                         "query": f"UPDATE {qt} SET {sets} WHERE {where} RETURNING *",
                                         "queryParameters": "{{ $json.p }}"}, c.title or c.name, row)
            if lr is not None:
                after = out.get("after") or out.get("rows") or []
                mocks[run_name] = {"items": [{"rows": after if isinstance(after, list) else [], "rowCount": 1}]}
        elif c.kind == "store":
            tname = await _table_name(session, spec)
            if not tname:
                plan.item(f"excluded:{c.id}:table", "excluded", "blocked", f"{c.name}: data table not found", c.id)
            g.add(run_name, "DataTable", {"table": tname or "", "operation": "insert", "data": "{{ $json.v.rows }}"},
                  c.title or c.name, row)
            mocks[run_name] = {"items": [{"id": 1}]}
        elif c.kind == "wait":
            secs: float | None = None
            if spec.get("mode") == "condition":
                plan.item(f"excluded:{c.id}:wait", "excluded", "blocked",
                          f"{c.name}: waiting on a query condition can't be kept yet (coming); use a fixed duration.",
                          c.id)
            else:
                try:
                    secs = rule_mod.duration_seconds(spec.get("duration"))
                except rule_mod.RuleError as e:
                    plan.item(f"excluded:{c.id}:wait", "excluded", "blocked", f"{c.name}: {e.message}", c.id)
                if secs is not None and secs > WAIT_MAX_S:
                    plan.item(f"excluded:{c.id}:wait", "excluded", "blocked",
                              f"{c.name}: a kept wait can be at most 5 minutes (the workflow Wait step is capped "
                              "there; longer, durable waits are coming).", c.id)
            n = int(secs or 0)
            unit, dur = ("minutes", n // 60) if n and n % 60 == 0 else ("seconds", max(n, 1))
            g.add(run_name, "Wait", {"unit": unit, "duration": dur}, c.title or f"Wait {spec.get('duration')}", row)
        g.link(cur, run_name)
        se = stop_empty and not in_body and c.kind == "query"
        post = g.add(c.name, "Code", {"code": code_for({**base_task, "op": "post", "src": src, "stop_empty": se}, label)},
                     f"{c.title or c.name} (result)", row)
        g.link([(run_name, "main")], post)
        if lr is not None and not in_body and lr.status == "success" and c.kind in ("query", "http", "workflow"):
            cnt = out.get("count", out.get("row_count"))
            if isinstance(cnt, int):
                expects.append({"node": c.name, "path": "count", "op": "eq", "value": cnt})
        return [(post, "main")] + skip

    for i, c in enumerate(keep_cells):
        tails = await add_cell(c, tails, None, 0)

    if body.target == "dataset":
        await _dataset_tail(session, nb, plan, body, g, tails, keep_cells, latest, ctx)
    elif body.target == "report":
        desc_cells = []
        for c in chosen:
            if c.kind == "note":
                desc_cells.append({"name": c.name, "kind": "note", "title": c.title,
                                   "markdown": (c.spec or {}).get("markdown")})
            elif c in keep_cells:
                desc_cells.append({"name": c.name, "kind": c.kind, "title": c.title, "children": [
                    {"name": x.name, "kind": x.kind, "title": x.title} for x in kids.get(c.id, [])]})
        rep = g.add("report", "Code", {"code": code_for({**ctx, "op": "report", "title": (body.name or nb.title)[:200],
                                                         "cells": desc_cells}, "report")}, "Report")
        g.link(tails, rep)
        plan.item("writes:report", "writes", "ok",
                  "Each run ends with the notebook report (markdown) as its output; nothing else is written.")

    desc = "\n\n".join(notes)[:1000] or f"Kept from the notebook “{nb.title}”."
    settings = {"source_notebook_id": nb.id, "kept_target": body.target, "run_as": run_as,
                "empty_policy": body.empty_policy}
    tests: list[dict[str, Any]] = []
    if body.target == "test_suite" and not plan.blocked:
        run_n = max((getattr(latest[c.id], "_run_n", 0) or 0 for c in all_cells if c.id in latest), default=0)
        tests.append({"name": f"Reproduces the notebook run{f' {run_n}' if run_n else ''}"[:120],
                      "description": "Outside steps replay the notebook's results; every cell's count must match.",
                      "input": {"items": [{}]}, "mocks": mocks,
                      "expect": expects or [{"node": g.nodes[-1]["name"], "op": "status", "value": "success"}],
                      "source": "replay"})
    plan.definition = {"name": (body.name or nb.title)[:255], "description": desc, "nodes": g.nodes,
                       "connections": g.connections, "settings": settings, "tests": tests}
    return plan


_SYSTEM_COLS = {"id", "created_at", "updated_at"}
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _slug_table(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")[:50] or "notebook"
    return s if re.match(r"^[a-z_]", s) else f"nb_{s}"


def _infer_type(values: list[Any]) -> str:
    seen = {("boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else
             "json" if isinstance(v, (dict, list)) else "string") for v in values if v is not None}
    return seen.pop() if len(seen) == 1 else "string" if not seen else "json"


async def _dataset_tail(session: AsyncSession, nb: NotebookModel, plan: KeepPlan, body: Any, g: _Graph,
                        tails: list[tuple[str, str]], keep_cells: list[NotebookCellModel], latest: dict[str, Any],
                        ctx: dict[str, Any]) -> None:
    """``dataset``: the chosen cell's rows go into a data table each run (``dataset_rows`` Code → ``dataset``)."""
    from ..data_table_service import DataTableService

    rows_kinds = ("query", "rule", "data", "http", "workflow", "change")
    want = getattr(body, "dataset_cell_id", None)
    if want:
        src = next((c for c in keep_cells if c.id == want or c.name == want), None)
        if src is None or src.kind not in rows_kinds:
            plan.item("writes:dataset", "writes", "blocked",
                      f"{want} is not a kept cell with rows (query, rule, data, http, workflow or change).")
            return
    else:
        src = next((c for c in reversed(keep_cells) if c.kind in rows_kinds), None)
        if src is None:
            plan.item("writes:dataset", "writes", "blocked", "Nothing kept here produces rows for a dataset.")
            return
    table = getattr(body, "dataset_table", None) or _slug_table(nb.title)
    if not _TABLE_NAME.match(table):
        plan.item("writes:dataset", "writes", "blocked", f"table name {table!r} must be a plain identifier")
        return
    existing = await DataTableService(session).table_by_name(nb.project_id, table)
    if existing is not None:
        cols = [{"name": c["name"], "type": c["type"], "from": c["name"]} for c in existing.columns or []]
        create = False
    else:
        lr = latest.get(src.id)
        rows = [r for r in (_full_rows(lr) if lr is not None else []) if isinstance(r, dict)][:200]
        # the result's own column order first (stored rows may come back key-sorted from JSONB)
        out_cols = ((getattr(lr, "output", None) or {}).get("columns") if lr is not None else None) or []
        keys: list[str] = [str(k) for k in out_cols if any(isinstance(r, dict) and k in r for r in rows)]
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        if not keys:
            plan.item("writes:dataset", "writes", "blocked",
                      f"Run the notebook first so {src.name}'s columns are known for the new table `{table}` "
                      "(or pick an existing table).", src.id)
            return
        cols = []
        for k in keys[:100]:
            name = re.sub(r"[^A-Za-z0-9_]", "_", str(k))[:62] or "col"
            if not re.match(r"^[A-Za-z_]", name):
                name = f"c_{name}"[:62]
            if name in _SYSTEM_COLS:
                name = f"src_{name}"
            if any(c["name"] == name for c in cols):
                continue
            cols.append({"name": name, "type": _infer_type([r.get(k) for r in rows]), "from": k})
        create = True
    plan.dataset = {"table": table, "create": create, "columns": [{"name": c["name"], "type": c["type"]} for c in cols]}
    pick = g.add("dataset_rows", "Code", {"code": code_for({**ctx, "op": "pick", "src": src.name, "columns": cols},
                                                           f"rows of {src.name} for {table}")}, f"Rows of {src.name}")
    g.link(tails, pick)
    g.add("dataset", "DataTable", {"table": table, "operation": "insert"}, f"Add to {table}")
    g.link([(pick, "main")], "dataset")
    plan.item("writes:dataset", "writes", "ok",
              f"Each run adds {src.name}'s rows to the data table `{table}`"
              + (f" (created on save: {', '.join(c['name'] + ' ' + c['type'] for c in cols[:8])}"
                 + (", …" if len(cols) > 8 else "") + ")" if create else ""), src.id)


def _static_secret_header(value: str, env_vars: dict[str, Any]) -> str:
    """A header naming a secret variable (``Bearer {{token}}``) is written into the node as the engine expression
    (``Bearer {{ $vars.KEY }}``), so the engine fills it in; other references in it are filled from the environment."""
    def sub(m: re.Match[str]) -> str:
        e = m.group(1).strip()
        if e.startswith("$"):
            return m.group(0)
        name = e[5:] if e.startswith("vars.") else e
        return str(env_vars.get(name, m.group(0)))
    return R.REF_RE.sub(sub, value)


def _secret_var_in(value: Any, env_vars: dict[str, Any]) -> bool:
    """A secret environment variable (``{{ $vars.KEY }}``) used in ``value``: a kept prep node would pass it on as text."""
    if "{{ $vars." in json.dumps(value, default=str):
        return True
    for r in R.refs_in(value):
        try:
            p = R.parse_path(r)
        except R.BadRef:
            continue
        name = p[1] if p[0] == "vars" and len(p) > 1 else p[0]
        if isinstance(name, str) and str(env_vars.get(name, "")).startswith("{{ $vars."):
            return True
    return False


def _list_key_cols(spec: dict[str, Any], out: dict[str, Any] | None) -> list[str]:
    """Change key columns that mean "any of these": a literal list, a ``{{cells.cN.rows.col}}`` reference, or a
    reference whose latest resolved value was a list (the selection a user made, or every row of a result)."""
    key, resolved = spec.get("key") or {}, ((out or {}).get("key") or {})
    cols = []
    for col, v in key.items():
        if isinstance(v, list) or isinstance(resolved.get(col), list) or (
                isinstance(v, str) and re.fullmatch(r"\{\{\s*cells\.c\d+\.rows\.[A-Za-z_]\w*\s*\}\}", v)):
            cols.append(col)
    return cols


def _full_rows(lr: Any) -> list[dict[str, Any]]:
    out = lr.output or {}
    if lr.output_ref:
        from .storage import read_rows

        got = read_rows(lr.output_ref)
        if got and isinstance(got.get("rows"), list):
            return got["rows"]
    return list(out.get("rows") or [])


def preview_out(plan: KeepPlan) -> dict[str, Any]:
    return {"items": plan.items, "definition": plan.definition, "can_save": plan.can_save,
            "can_schedule": plan.can_schedule}


async def commit(session: AsyncSession, nb: NotebookModel, plan: KeepPlan, body: KeepRequestX) -> dict[str, Any]:
    """Save the plan as a workflow. Raises 422 ``invalid`` / 409 ``unbound_credential``."""
    from ...engine.node_registry import node_registry
    from ...repositories.execution_repository import ExecutionRepository
    from ...repositories.workflow_repository import WorkflowRepository
    from ...schemas.workflow import WorkflowCreateRequest
    from ..node_service import NodeService
    from ..workflow_service import WorkflowService

    if not plan.can_save:
        raise notebook_error(422, "invalid", "this notebook can't be kept as it is",
                             items=[i for i in plan.items if i["status"] == "blocked"])
    trig = plan.trigger or {}
    publish = bool(trig.get("publish"))
    if publish and plan.unbound:
        raise notebook_error(409, "unbound_credential", "pick a credential for every slot before scheduling",
                             items=[i for i in plan.items if i["kind"] == "slots" and i["status"] != "ok"])
    d = plan.definition
    if plan.dataset and plan.dataset["create"]:
        from ...db.models import ProjectModel
        from ..data_table_service import DataTableService

        project = await session.get(ProjectModel, nb.project_id)
        dts = DataTableService(session)
        if await dts.table_by_name(nb.project_id, plan.dataset["table"]) is None:
            await dts.create_table(project, {"name": plan.dataset["table"], "columns": plan.dataset["columns"],
                                             "description": f"Filled by the notebook “{nb.title}” (Keep → dataset)."},
                                   actor=body.by)
    svc = WorkflowService(WorkflowRepository(session), ExecutionRepository(session), NodeService(node_registry),
                          node_registry)
    req = WorkflowCreateRequest(name=d["name"], description=d["description"], nodes=d["nodes"],
                                connections=d["connections"], settings=d["settings"], tests=d["tests"],
                                project_id=nb.project_id)
    wf = await svc.create_workflow(req, created_by=body.by)
    wid = wf.id
    schedule_active = False
    if publish:
        await svc.publish(wid, f"Kept from notebook {nb.title}")
        schedule_active = True
    entry = {"target": body.target, "workflow_id": wid, "at": datetime.now().isoformat(), "by": body.by,
             "trigger": trig.get("type")}
    if trig.get("webhook_path"):
        entry["webhook_path"] = trig["webhook_path"]
    if plan.dataset:
        entry["table"] = plan.dataset["table"]
    fresh = await session.get(NotebookModel, nb.id)
    if fresh is not None:
        fresh.kept = list(fresh.kept or []) + [entry]
        fresh.updated_at = datetime.now()
    await session.commit()
    spec_url = f"/api/notebooks/{nb.id}/export.spec.ts" if body.target == "test_suite" and plan.browser_cells else None
    return {"workflow_id": wid, "target": body.target, "schedule_active": schedule_active, "spec_url": spec_url,
            "webhook_url": trig.get("webhook_url"), "table": plan.dataset["table"] if plan.dataset else None}


__all__ = ["KeepPlan", "KeepRequestX", "KeepResponseX", "KeepTriggerX", "build", "code_for", "commit", "preview_out",
           "trigger_plan"]

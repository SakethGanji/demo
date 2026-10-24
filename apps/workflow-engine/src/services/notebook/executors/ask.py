"""Ask: one LLM call plans every cell for a request (BUILD-PLAN §1.2 + the one-shot AMENDMENT).

The shape copies ``prototypes/workflow-oneshot``: a short rules-only system prompt, all the context in the first and
only call (the notebook's cells with their latest result summaries, the project's connections with tables/columns,
saved workflows with their input fields, and the cell-kind specs), and a forced tool (``plan_cells``) that returns
every planned cell at once. The specs are validated with the same Pydantic models the cell routes use; only when that
validation fails is the model asked once more, with the errors (one repair call). No follow-up turns.

Any exception reaching the provider (no key, quota, network, the test fake's ``{"raise": "fatal"}``) gives the ask
cell the error ``llm_unavailable``; the caller still answers HTTP 200. In a run, ``run`` records ``skipped_ask``:
an Ask is never replayed, so a rerun makes no LLM call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from . import CellOutcome

logger = logging.getLogger(__name__)

TOOL_NAME = "plan_cells"
MAX_PLANNED = 12
PREVIEW_ROWS_IN_PROMPT = 3
CONTEXT_MAX_BYTES = 24_000
LLM_TIMEOUT_S = 25.0


async def run(ctx: Any, cell: Any, inputs: Any = None) -> CellOutcome:
    """In a run an Ask cell is never re-asked (no LLM call on rerun)."""
    return CellOutcome(status="skipped_ask", output={"note": "Ask cells are not replayed; their planned cells run instead."})


def model_name() -> str:
    from ....core.config import settings

    return (os.environ.get("WORKFLOW_NOTEBOOK_ASK_MODEL", "").strip() or settings.app_builder_model
            or settings.module_gen_model)


# ── the prompt ───────────────────────────────────────────────────────────────

SYSTEM = """You add cells to a data notebook used by a bank's operations teams. Read the request and the notebook, then call `plan_cells` once with every cell needed, in order. Don't ask questions.

Rules
- Plan the fewest cells that answer the request. Prefer deterministic cells (query, rule, http, workflow) over prose.
- New cells are named in order after the existing ones (the next names are given). Refer to an earlier result with `{{cells.cN.rows}}`, `{{cells.cN.count}}` or `{{cells.cN.rows[0].field}}`; inside a for_each body use `{{row.field}}`.
- Use only the connections, tables, columns and workflows listed. Never invent ids.
- SQL is one SELECT (or WITH … SELECT). Put values in `params` and write them as `%(name)s`.
- Changing data is only ever a `change` cell, on a connection marked writable. It is reviewed before it is applied.
- A key value may be a list: `key:{"wire_id": ["W1","W2"]}` changes exactly those rows. When the request is about "the selected" rows, key the change (or a for_each over them) on exactly the selected keys given below, nothing else. To change every row an earlier cell found, key on `{{cells.cN.rows.col}}` (that column from every row); narrow first with a query or a rule filter ("older than 2 days" is a SQL condition or a rule `older_than`).
- A check ("is X within Y", "which rows mismatch") is a `rule` cell over earlier rows.
- Use `browser` only when the request is about a web page; recipes are read-only.
- Testing an API is one http cell per request, each with `assertions` (status, a time limit, the fields that matter, a schema when the response shape is known). Write URLs with environment variables (`{{base_url}}/refunds`) when they exist, sign in with `auth` naming a listed secret (never put a secret value in a header). To chain, `extract` a value (a token, a new id) and use it later as `{{name}}`. Negative tests (missing or wrong auth, bad input, unknown id) assert the error status they should get. For many inputs use a for_each over a data cell.
- Titles are short sentences saying what the cell does.

Cell kinds and their `spec`
- query: {connection, sql, params:{}, limit≤500}
- rule: {op:"filter"|"count"|"assert"|"compare", left:"{{cells.cN.rows}}", right?, key?, where:[{field, op, value}], fields:[{left, right, op, tolerance?}]}. where ops: eq ne lt le gt ge in not_in contains is_null not_null older_than. compare matches `left` and `right` rows on `key` and reports field mismatches.
- http: {method, url, params:{}, headers:{}, body?, body_type:"json"|"form", auth?:{type:"bearer"|"basic"|"api_key", secret, username?, name?, add_to:"header"|"query"}, assertions:[{kind:"status"|"time"|"json_path"|"header"|"schema", op, path?, name?, value?, json_schema?}], extract:[{name, source:"json_path"|"header"|"status", path?}]} — or api_ref:"<spec_id>:<operation_id>" instead of url. assertion ops: eq ne lt le gt ge contains not_contains exists not_exists matches in; time is `op:"lt", value:<ms>`.
- workflow: {workflow_id, version:"published"|n, inputs:{}}
- change: {connection, table, key:{col: value}, set:{col: value}, reason}
- wait: {mode:"duration", duration:"30 min"} — pauses the run
- data: {columns:[...], rows:[{...}]}
- store: {new_table|table_id, mode:"append"|"upsert", key?, rows:"{{cells.cN.rows}}"}
- browser: {base_url, credential_slot?, steps:[{action, target:{role,name}|{label}|{text}, value?}], writes:false}. actions: goto click fill select press hover waitFor expectVisible expectText expectCount. A secret from the slot is written `{{$cred.password}}`.
- for_each: {over:"{{cells.cN.rows}}", max_rows≤500, stop_on_fail?} with its body in `children` (query, http, workflow, rule, change, data or store cells)
- note: {markdown}
A cell may have `guard` {left, op:"eq"|"ne"|"lt"|"le"|"gt"|"ge"|"nonempty"|"empty", right?}: it runs only when that holds."""

TOOL = {
    "name": TOOL_NAME,
    "description": "Add these cells, in order, to the end of the notebook.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what the planned cells do."},
            "cells": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["query", "http", "workflow", "rule", "change", "data",
                                                            "store", "browser", "for_each", "note", "wait"]},
                        "title": {"type": "string"},
                        "spec": {"type": "object"},
                        "guard": {"type": "object"},
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["kind", "title", "spec"],
                },
            },
        },
        "required": ["summary", "cells"],
    },
}


def _short(v: Any, n: int = 400) -> str:
    s = json.dumps(v, default=str, separators=(",", ":"))
    return s if len(s) <= n else s[: n - 3] + "..."


def _result_line(latest: Any) -> str:
    if latest is None:
        return "not run yet"
    status = getattr(latest, "status", None)
    out = getattr(latest, "output", None) or {}
    err = getattr(latest, "error", None) or {}
    bits = [str(status)]
    if err:
        bits.append(f"error {err.get('code')}: {str(err.get('message'))[:160]}")
    if isinstance(out, dict):
        if "row_count" in out:
            bits.append(f"{out.get('row_count')} rows, columns {out.get('columns')}")
            rows = out.get("rows") or []
            if rows:
                bits.append("first rows " + _short(rows[:PREVIEW_ROWS_IN_PROMPT], 500))
        else:
            bits.append(_short(out, 300))
    return "; ".join(bits)


def selection_line(sel: Any) -> str:
    """``Selected rows: 2 rows of c1, by wire_id: W1, W2`` (the keys the user ticked; at most 500)."""
    keys = [str(k) for k in sel.keys]
    shown = ", ".join(keys[:200]) + (f", … ({len(keys) - 200} more)" if len(keys) > 200 else "")
    return f"Selected rows: {len(keys)} row{'s' if len(keys) != 1 else ''} of {sel.cell}, by {sel.key}: {shown}"


async def build_context(session: Any, notebook: Any, cells: list[Any], latest: dict[str, Any]) -> str:
    """Everything the model needs, as one user-message prefix."""
    from sqlalchemy import select

    from ....db.connection_models import ProjectConnectionModel as C
    from ....repositories.workflow_repository import WorkflowRepository
    from ...connection_service import render_context

    parts: list[str] = [f"Notebook: {notebook.title}"]
    if cells:
        parts.append("Cells so far:")
        by_parent: dict[str | None, list[Any]] = {}
        for c in cells:
            by_parent.setdefault(c.parent_cell_id, []).append(c)
        for c in sorted(by_parent.get(None, []), key=lambda x: x.position):
            parts.append(f"- {c.name} {c.kind} \"{c.title or ''}\" spec {_short(c.spec, 600)}"
                         + (f" guard {_short(c.guard, 200)}" if c.guard else "")
                         + f" → {_result_line(latest.get(c.id))}")
            for ch in sorted(by_parent.get(c.id, []), key=lambda x: x.position):
                parts.append(f"  - body {ch.name} {ch.kind} \"{ch.title or ''}\" spec {_short(ch.spec, 400)}")
    else:
        parts.append("Cells so far: none.")

    conns = (await session.execute(select(C).where(C.project_id == notebook.project_id))).scalars().all()
    if conns:
        parts.append("Connections: " + ", ".join(f"{c.key} ({'read-only' if c.readonly else 'writable'})" for c in conns))
        ctx = render_context([{"key": c.key, "readonly": c.readonly, "tables": (c.schema_snapshot or {}).get("tables") or []}
                              for c in conns], max_bytes=CONTEXT_MAX_BYTES)
        if ctx:
            parts.append("Tables (connection.schema.table: columns):\n" + "\n".join(ctx.splitlines()[1:]))
    else:
        parts.append("Connections: none.")

    wfs = await WorkflowRepository(session).list(project_id=notebook.project_id)
    if wfs:
        parts.append("Saved workflows:")
        for w in wfs[:40]:
            parts.append(f"- {w.id} \"{w.name}\" inputs {workflow_inputs(w.workflow)}"
                         + (" (published)" if getattr(w, "active", False) else ""))
    else:
        parts.append("Saved workflows: none.")
    parts.extend(await api_context(session, notebook))
    return "\n".join(parts)


async def api_context(session: Any, notebook: Any) -> list[str]:
    """For API testing: the active environment's variable names, the project's secret keys (names only, never values)
    and the API catalog's endpoints."""
    from sqlalchemy import select

    from ....db.models import ApiSpecModel, VariableModel as V
    from ..runner import load_env

    out: list[str] = []
    env = await load_env(session, notebook.id)
    if env:
        out.append("Environment variables (write {{name}}): " + ", ".join(
            f"{k}{' (secret)' if isinstance(v, str) and v.startswith('{{ $vars.') else '=' + _short(v, 80)}"
            for k, v in list(env.items())[:40]))
    keys = (await session.execute(select(V.key).where(V.type == "secret")
                                  .where((V.project_id == notebook.project_id) | (V.project_id.is_(None)))
                                  .distinct())).scalars().all()
    if keys:
        out.append("Secrets for auth.secret (names only): " + ", ".join(sorted(set(keys))[:40]))
    specs = (await session.execute(select(ApiSpecModel).where(ApiSpecModel.project_id == notebook.project_id)
                                   .limit(10))).scalars().all()
    for sp in specs:
        ops = (sp.spec or {}).get("operations") or []
        out.append(f"API {sp.id} \"{sp.name}\" base {sp.base_url}: " + "; ".join(
            f"{o.get('operation_id')} {o.get('method')} {o.get('path')}"
            + (f" ({str(o.get('summary'))[:60]})" if o.get('summary') else "") for o in ops[:30]))
    return out


def workflow_inputs(wf: Any) -> list[str]:
    """Input field names from the trigger's ``input_schema`` (FormTrigger / Start / ExecuteWorkflowTrigger)."""
    for n in getattr(wf, "nodes", None) or []:
        params = getattr(n, "parameters", None) or {}
        schema = params.get("input_schema") or params.get("inputSchema")
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                schema = None
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            return list(schema["properties"])
    return []


# ── validation ───────────────────────────────────────────────────────────────

_REF = re.compile(r"\{\{\s*cells\.(c\d+)\b")


def _refs(value: Any) -> set[str]:
    return set(_REF.findall(json.dumps(value, default=str)))


def validate_plan(raw_cells: Any, existing_names: list[str], next_index: int, writable: set[str], readonly: set[str],
                  workflow_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """``(cells, errors)``: each planned cell normalised through its spec model, plus semantic checks the cell routes
    also make (SELECT-only SQL, change on a writable connection, references to cells that exist, body kinds)."""
    from ....schemas.notebook import Guard, TOP_LEVEL_ONLY_KINDS, validate_spec
    from ...external_db import ExternalDbError, check_select

    errors: list[str] = []
    out: list[dict[str, Any]] = []
    if not isinstance(raw_cells, list) or not raw_cells:
        return [], ["`cells` must be a non-empty list"]
    if len(raw_cells) > MAX_PLANNED:
        errors.append(f"plan at most {MAX_PLANNED} cells")
        raw_cells = raw_cells[:MAX_PLANNED]
    known = set(existing_names)
    idx = next_index

    def check_one(i: str, c: Any, top: bool) -> dict[str, Any] | None:
        nonlocal idx
        name = f"c{idx}"  # names follow the model's order whether or not the cell validates
        idx += 1
        known.add(name)
        if not isinstance(c, dict):
            errors.append(f"{i}: must be an object")
            return None
        kind = c.get("kind")
        if kind == "ask":
            errors.append(f"{i}: do not plan ask cells")
            return None
        if not top and kind in TOP_LEVEL_ONLY_KINDS:
            errors.append(f"{i}: a {kind} cell cannot be inside a for_each body")
            return None
        try:
            spec = validate_spec(str(kind), c.get("spec") if isinstance(c.get("spec"), dict) else {})
        except Exception as e:  # noqa: BLE001 - HTTPException from validate_spec
            detail = getattr(e, "detail", None) or {}
            fields = "; ".join(f"{'.'.join(map(str, f['loc']))}: {f['msg']}" for f in detail.get("fields") or [])
            errors.append(f"{i} ({kind}): {detail.get('message', str(e))}{' — ' + fields if fields else ''}")
            return None
        guard = None
        if c.get("guard"):
            try:
                guard = Guard.model_validate(c["guard"]).model_dump(mode="json")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{i}: bad guard ({str(e)[:200]})")
                return None
        if kind == "query":
            try:
                check_select(spec["sql"])
            except ExternalDbError as e:
                errors.append(f"{i} (query): {e}")
                return None
            if spec["connection"] not in writable | readonly:
                errors.append(f"{i} (query): unknown connection {spec['connection']!r}")
                return None
        if kind == "change":
            if spec["connection"] in readonly:
                errors.append(f"{i} (change): connection {spec['connection']!r} is read-only; a change needs a writable one")
                return None
            if spec["connection"] not in writable:
                errors.append(f"{i} (change): unknown connection {spec['connection']!r}")
                return None
        if kind == "workflow" and spec["workflow_id"] not in workflow_ids:
            errors.append(f"{i} (workflow): unknown workflow {spec['workflow_id']!r}")
            return None
        missing = sorted(r for r in _refs({"s": spec, "g": guard}) if r not in known or r == name)
        if missing:
            errors.append(f"{i}: refers to {', '.join(missing)}, which is not an earlier cell")
            return None
        title = str(c.get("title") or "").strip()[:200] or None
        cell = {"kind": kind, "title": title, "spec": spec, "guard": guard, "children": [], "name": name}
        if kind == "for_each":
            kids = c.get("children") or []
            if not isinstance(kids, list) or not kids:
                errors.append(f"{i} (for_each): needs at least one body cell in `children`")
                return None
            for j, ch in enumerate(kids):
                got = check_one(f"{i}.children[{j}]", ch, False)
                if got is None:
                    return None
                cell["children"].append(got)
        return cell

    broken = False
    for n, c in enumerate(raw_cells):
        got = check_one(f"cells[{n}]", c, True)
        if got is None:
            broken = True  # keep only the valid prefix, so every kept reference still names the planned cell
        elif not broken:
            out.append(got)
    return out, errors


# ── the call ─────────────────────────────────────────────────────────────────


class AskFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_args(resp: Any) -> dict[str, Any] | None:
    for tc in getattr(resp, "tool_calls", None) or []:
        if tc.name == TOOL_NAME and isinstance(tc.args, dict):
            return tc.args
    text = getattr(resp, "text", None) or ""
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            v = json.loads(text[a:b + 1])
            return v if isinstance(v, dict) else None
        except ValueError:
            return None
    return None


async def _call(messages: list[dict[str, Any]]) -> dict[str, Any]:
    import asyncio

    from ....engine import llm_provider

    if not llm_provider.is_configured(model_name()):
        raise AskFailed("llm_unavailable", f"no AI provider is configured for {model_name()}")
    try:
        resp = await asyncio.wait_for(llm_provider.call_llm(model_name(), messages, temperature=0.2, tools=[TOOL],
                                                            tool_choice="required", max_tokens=8000),
                                      timeout=LLM_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - every provider failure is "AI unavailable" to the notebook
        raise AskFailed("llm_unavailable", f"{type(e).__name__}: {str(e)[:240]}") from None
    args = _tool_args(resp)
    if args is None:
        raise AskFailed("plan_invalid", f"the model did not call {TOOL_NAME}")
    return args


async def plan(context: str, text: str, *, existing_names: list[str], next_index: int, writable: set[str],
               readonly: set[str], workflow_ids: set[str]) -> dict[str, Any]:
    """One call (plus at most one repair call on a validation error). Returns
    ``{cells, summary, calls, ms, rejected}``; raises :class:`AskFailed`."""
    t0 = time.monotonic()
    user = (f"{context}\n\nThe next new cell is c{next_index}.\n\nRequest: {text}")
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    args = await _call(messages)
    calls = 1
    cells, errors = validate_plan(args.get("cells"), existing_names, next_index, writable, readonly, workflow_ids)
    if errors:
        messages += [
            {"role": "assistant", "content": json.dumps({"tool": TOOL_NAME, "args": args}, default=str)[:20000]},
            {"role": "user", "content": "These planned cells are invalid:\n- " + "\n- ".join(errors[:20])
             + f"\n\nCall {TOOL_NAME} again with the whole corrected plan."},
        ]
        args2 = await _call(messages)
        calls = 2
        cells2, errors2 = validate_plan(args2.get("cells"), existing_names, next_index, writable, readonly, workflow_ids)
        if cells2 or not cells:
            args, cells, errors = args2, cells2, errors2
    if not cells:
        raise AskFailed("plan_invalid", "the planned cells did not validate: " + "; ".join(errors[:5]))
    return {"cells": cells, "summary": str(args.get("summary") or "")[:500], "calls": calls,
            "ms": int((time.monotonic() - t0) * 1000), "rejected": errors}


__all__ = ["AskFailed", "SYSTEM", "TOOL", "TOOL_NAME", "build_context", "plan", "run", "selection_line",
           "validate_plan"]

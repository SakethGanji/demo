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
- To answer a question from what is already in the notebook, or to refuse (the data, connection or workflow isn't listed, or the request is unsafe), plan one `note` cell saying so, or no cells with just `notes`; never invent anything to fill the gap.
- `notes` is your memory for this notebook: you see it at the top of the next request. Rewrite it whole when you learn something worth keeping (key facts, decisions, what to run, what failed); omit it to keep it as it is.
- New cells are named in order after the existing ones (the next names are given). Refer to an earlier result with `{{cells.cN.rows}}`, `{{cells.cN.count}}` or `{{cells.cN.rows[0].field}}`; inside a for_each body use `{{row.field}}`.
- Use only the connections, tables, columns and workflows listed. Never invent ids.
- SQL is one SELECT (or WITH … SELECT). Put values in `params` and write them as `%(name)s`.
- Changing data is only ever a `change` cell, on a connection marked writable. It is reviewed before it is applied.
- A key value may be a list: `key:{"wire_id": ["W1","W2"]}` changes exactly those rows. When the request is about "the selected" rows, key the change (or a for_each over them) on exactly the selected keys given below, nothing else. To change every row an earlier cell found, key on `{{cells.cN.rows.col}}` (that column from every row); narrow first with a query or a rule filter ("older than 2 days" is a SQL condition or a rule `older_than`).
- A check ("is X within Y", "which rows mismatch") is a `rule` cell over earlier rows. `assert` passes when its input has no rows; to require that a row exists, `count` it and guard or compare on `{{cells.cN.count}}` (e.g. a later rule or a guard `{left:"{{cells.cN.count}}", op:"gt", right:0}`).
- `change.table` is `schema.table` exactly as listed after the connection key (`bank_rw.bank.loans` → table `bank.loans`). `set` values may be references (`{{cells.cN.rows[0].amount}}`), resolved when the cell runs; one change sets the same values on every keyed row. After a change is applied its source query may find 0 rows (they no longer match), so refer to the change cell's own captured rows (`{{cells.cN.rows}}` of the change cell) for what changed.
- Use `browser` only when the request is about a web page; recipes are read-only unless the spec says `writes:true`.
- Site details (base URL, page paths, sign-in field labels, which secret signs in): record them in `notes` the first time the user gives them or a run confirms them, and reuse them from there after that; use a listed variable such as BASE_URL when there is one.
- Database numerics come back as decimals (`15000.0`) while a page shows `15000`: when a query value is compared with page text, cast it in SQL to the page's format (`balance::bigint AS balance`, `to_char(amount, 'FM999,990.00')`).
- To replace a failed or obsolete cell, plan the new one and list the old in `remove` (`remove:["c3"]`); it is deleted in the same call. Cells can't be removed during a run or after they applied a change.
- Testing an API is one http cell per request, each with `assertions` (status, a time limit, the fields that matter, a schema when the response shape is known). Write URLs with environment variables (`{{base_url}}/refunds`) when they exist, sign in with `auth` naming a listed secret (never put a secret value in a header). To chain, `extract` a value (a token, a new id) and use it later as `{{name}}`. Negative tests (missing or wrong auth, bad input, unknown id) assert the error status they should get. For many inputs use a for_each over a data cell.
- A successful POST/PUT/PATCH/DELETE counts as a write: the next run stops before it. Give logins and test calls that are safe to send again `rerun:"always"`. Give a check whose failure should not stop later cells (so cleanup still runs) `continue_on_fail:true` (http and rule cells).
- Titles are short sentences saying what the cell does.

Cell kinds and their `spec`
- query: {connection, sql, params:{}, limit≤500}
- rule: {op:"filter"|"count"|"assert"|"compare", left:"{{cells.cN.rows}}", right?, key?, where:[{field, op, value}], fields:[{left, right, op, tolerance?}], fail_on_mismatch?, continue_on_fail?}. where ops: eq ne lt le gt ge in not_in contains is_null not_null older_than. compare matches `left` and `right` rows on `key` (column names present on both sides; alias them in SQL, `SELECT user_id AS id`) and reports field and missing-row mismatches as rows; it passes even with mismatches unless `fail_on_mismatch:true`, so set that when the two sides must agree.
- http: {method, url, params:{}, headers:{}, body?, body_type:"json"|"form", auth?:{type:"bearer"|"basic"|"api_key", secret, username?, name?, add_to:"header"|"query"}, assertions:[{kind:"status"|"time"|"json_path"|"header"|"schema", op, path?, name?, value?, json_schema?}], extract:[{name, source:"json_path"|"header"|"status", path?}], rerun?:"always", continue_on_fail?} — or api_ref:"<spec_id>:<operation_id>" instead of url. assertion ops: eq ne lt le gt ge contains not_contains exists not_exists matches in has_item; time is `op:"lt", value:<ms>`; `has_item` checks that the list at `path` has an item whose fields equal `value` (`{path:"$.items", op:"has_item", value:{"id":"L7","status":"open"}}`).
- json_path paths: `$` is the whole body; then `.field` and `[n]` steps only (`$.data.items[0].id`, `$[-1]` is the last item, `.length` on a list is its size). No wildcards, filters or `..`.
- workflow: {workflow_id, version:"published"|n, inputs:{}}
- change: {connection, table:"schema.table", key:{col: value}, set:{col: value}, reason} (top level only, not inside a for_each)
- wait: {mode:"duration", duration:"30 min"} — pauses the run
- data: {columns:[...], rows:[{...}]} — or {from_table:"<data table name or id>"} to read a listed data table's current rows when the cell runs
- store: {new_table|table_id, mode:"append"|"upsert", key?, rows:"{{cells.cN.rows}}"} — table_id from the listed data tables, or new_table to create one. Fields named id, created_at or updated_at are stored as src_id, src_created_at, src_updated_at (those names are reserved)
- browser: {base_url, credential_slot?, steps:[...], writes:false}. Each step's required fields: goto {url}; click/hover/expectVisible {target}; fill {target, value}; select {target, option}; press {key, target?}; waitFor {target, state?:"visible"|"hidden"}; expectText {target, text}; expectCount {target, count}. A target is {role, name} or {label} or {text}; name, label and text match the whole text exactly unless the target adds `exact:false` (contains, ignoring case), and `within:{target}` looks only inside another element. A `{role:"row", name}` must equal the row's entire text (all its cells), so check a row through a cell (`{role:"cell", name:"Cy"}`), `exact:false`, or `within` the row. A failed expectText or expectCount says what the page had (`found 2 (expected 3)`, `found: '…'`); use it to explain the failure. `credential_slot` is the name of a listed secret; its values are written `{{$cred.password}}` / `{{$cred.username}}` in steps. `{{$cred.password}}` (any secret field) only works as a fill `value`; `{{$cred.username}}` (also email, login) works in any step, e.g. expectText `Signed in as {{$cred.username}}`. Every browser cell starts a fresh browser (nothing carries over from an earlier browser cell), so when the site needs a login, begin each browser cell with the sign-in steps; group steps by page section with `group`. Set `writes:true` only when the request is to change something on the site (add, edit, submit).
- for_each: {over:"{{cells.cN.rows}}", max_rows≤500, stop_on_fail?} with its body in `children` (query, http, workflow, rule, data, store or browser cells). Its result rows are {i, status:"success"|"error", error, row} where `row` is the input row: to act on the rows that failed, `rule` filter `where:[{field:"status", op:"eq", value:"error"}]` and then use `{{cells.cN.rows.row.id}}` (a change key) or `{{cells.cN.rows.row}}` (a new for_each over just those inputs).
- note: {markdown}
A cell may have `guard` {left, op:"eq"|"ne"|"lt"|"le"|"gt"|"ge"|"nonempty"|"empty", right?}: it runs only when that holds."""

TOOL = {
    "name": TOOL_NAME,
    "description": "Add these cells, in order, to the end of the notebook.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what the planned cells do."},
            "notes": {"type": "string", "description": "Your running notes for this notebook: key facts, decisions, "
                      "what to run, what failed. Rewrite the whole thing; keep it short. Omit to keep current notes."},
            "remove": {"type": "array", "items": {"type": "string"},
                       "description": "Optional: names of existing cells to delete in this same call (e.g. [\"c3\"]), "
                       "for a failed or obsolete cell you replace. A cell can't be removed while a run is in "
                       "progress or after it applied a change."},
            "cells": {
                "type": "array",
                "description": "The cells to add, in order. May be empty when you only answer or refuse in `notes`, "
                               "or only `remove` cells.",
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


NOTES_SHOWN = 2000
LAST_RUN_MAX = 1500
#: Per failed cell in the last-run line; long enough to keep a browser step's "found ..." after its description.
FAIL_MSG_SHOWN = 260
FAILED_CHECKS_SHOWN = 3


def compact(v: Any) -> Any:
    """A spec as the prompt echoes it: null fields dropped (they are defaults and only cost tokens)."""
    if isinstance(v, dict):
        return {k: compact(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [compact(x) for x in v]
    return v


def failed_checks(out: Any, n: int = FAILED_CHECKS_SHOWN) -> list[str]:
    """``"$.status equals open (got closed)"`` for the first ``n`` failed http assertions of a result output."""
    if not isinstance(out, dict):
        return []
    bad = [a for a in out.get("assertions") or [] if isinstance(a, dict) and a.get("passed") is False]
    return [f"{a.get('label')} (got {_short(a.get('actual'), 80)})" + (f": {str(a['message'])[:80]}" if a.get("message")
                                                                         else "") for a in bad[:n]]


def _result_line(latest: Any) -> str:
    if latest is None:
        return "not run yet"
    status = getattr(latest, "status", None)
    out = getattr(latest, "output", None) or {}
    err = getattr(latest, "error", None) or {}
    bits = [str(status)]
    if isinstance(out, dict) and isinstance(out.get("passed"), bool):
        bits.append("passed" if out["passed"] else "did not pass")
        bad = failed_checks(out)
        if bad:
            bits.append("failed checks: " + "; ".join(bad))
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
    notes = (getattr(notebook, "ai_notes", None) or "").strip()
    parts.append("Your notes from earlier: " + (notes[:NOTES_SHOWN] if notes else "none yet"))
    if cells:
        parts.append("Cells so far:")
        by_parent: dict[str | None, list[Any]] = {}
        for c in cells:
            by_parent.setdefault(c.parent_cell_id, []).append(c)
        for c in sorted(by_parent.get(None, []), key=lambda x: x.position):
            parts.append(f"- {c.name} {c.kind} \"{c.title or ''}\" spec {_short(compact(c.spec), 600)}"
                         + (f" guard {_short(c.guard, 200)}" if c.guard else "")
                         + f" → {_result_line(latest.get(c.id))}")
            for ch in sorted(by_parent.get(c.id, []), key=lambda x: x.position):
                parts.append(f"  - body {ch.name} {ch.kind} \"{ch.title or ''}\" spec {_short(compact(ch.spec), 400)}")
        last = await last_run_line(session, notebook, cells)
        if last:
            parts.append(last)
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
    parts.extend(await data_tables_context(session, notebook))
    parts.extend(await api_context(session, notebook))
    return "\n".join(parts)


_OK = ("success", "applied", "kept_earlier")


async def last_run_line(session: Any, notebook: Any, cells: list[Any]) -> str | None:
    """``Last run: run 3 at 2026-10-24 18:40, failed at c4. Cells: c2 ok, c3 ok, c4 FAILED. Failures: c4: …``
    (the newest real run, not an Ask's record; at most ``LAST_RUN_MAX`` characters)."""
    from sqlalchemy import select

    from ....db.notebook_models import NotebookCellResultModel as Res, NotebookRunModel as Run

    run = (await session.execute(select(Run).where(Run.notebook_id == notebook.id)
                                 .where((Run.via_agent.is_(None)) | (Run.via_agent != "ask"))
                                 .order_by(Run.n.desc()).limit(1))).scalars().first()
    if run is None:
        return None
    results = (await session.execute(select(Res).where(Res.run_id == run.id, Res.row_index.is_(None))
                                     .order_by(Res.created_at))).scalars().all()
    by_cell = {r.cell_id: r for r in results}
    name_of = {c.id: c.name for c in cells}
    stop = run.stop or {}
    head = f"Last run: run {run.n} at {run.started_at:%Y-%m-%d %H:%M}, {str(run.status).replace('_', ' ')}"
    if stop.get("cell_id") in name_of and run.status != "done":
        head += f" at {name_of[stop['cell_id']]}"
    states, fails = [], []
    for c in sorted((c for c in cells if c.parent_cell_id is None), key=lambda x: x.position):
        if c.kind in ("note", "ask"):
            continue
        r = by_cell.get(c.id)
        if r is None:
            states.append(f"{c.name} not run")
            continue
        out = r.output if isinstance(r.output, dict) else {}
        if r.status == "error":
            states.append(f"{c.name} FAILED")
            err = str((r.error or {}).get("message") or "")[:FAIL_MSG_SHOWN]
            checks = [x for x in failed_checks(out, 2) if x.split(" (got ")[0] not in err]  # not said twice
            fails.append(f"{c.name}: " + "; ".join(m for m in [err] + checks if m))
        elif r.status in _OK:
            extra = f" ({out.get('failed')} of {out.get('row_count')} rows failed)" if c.kind == "for_each" and \
                out.get("failed") else ""
            states.append(f"{c.name} ok{extra}")
            if extra:
                fails.append(f"{c.name}: " + failed_rows(out))
        else:
            states.append(f"{c.name} {str(r.status).replace('_', ' ')}")
    line = head + ". Cells: " + (", ".join(states) or "none") + "."
    if fails:
        line += " Failures: " + " | ".join(fails)
    return line if len(line) <= LAST_RUN_MAX else line[: LAST_RUN_MAX - 3] + "..."


def failed_rows(out: dict[str, Any], n: int = 5) -> str:
    """``rows 1 (id L2), 3 (id L4) failed; row 1: status is 200 (got 500)`` for a for_each output."""
    bad = [r for r in (out.get("rows") or []) if isinstance(r, dict) and r.get("status") == "error"]
    def tag(r: dict[str, Any]) -> str:
        row = r.get("row") if isinstance(r.get("row"), dict) else {}
        k = next((k for k in ("id", "key", "name", "title") if k in row), None) or next(iter(row), None)
        return f"{r.get('i')} ({k} {_short(row[k], 40)})" if k else str(r.get("i"))
    head = "rows " + ", ".join(tag(r) for r in bad[:n]) + (" …" if len(bad) > n else "") + " failed"
    first = bad[0].get("error") if bad else None
    msg = str((first or {}).get("message") or "")[:160] if isinstance(first, dict) else ""
    return head + (f"; row {bad[0].get('i')}: {msg}" if msg else "")


async def data_tables_context(session: Any, notebook: Any) -> list[str]:
    """The project's data tables (where store cells write): ``name (id): col type, ...``."""
    from sqlalchemy import select

    from ....db.models import DataTableModel as T

    tables = (await session.execute(select(T).where(T.project_id == notebook.project_id).order_by(T.name)
                                    .limit(30))).scalars().all()
    if not tables:
        return ["Data tables (for store cells): none yet; a store cell can create one with new_table."]
    out = ["Data tables (store cells write them: table_id, or new_table to create one; a data cell reads one with from_table):"]
    for t in tables:
        cols = ", ".join(f"{c.get('name')} {c.get('type') or 'string'}" for c in (t.columns or [])[:40]
                         if isinstance(c, dict))
        out.append(f"- {t.name} (table_id {t.id}): {cols or 'no columns'}")
    return out


async def api_context(session: Any, notebook: Any) -> list[str]:
    """For API testing: the active environment's variable names, the project's secret keys (names only, never values)
    and the API catalog's endpoints."""
    from sqlalchemy import select

    from ....db.models import ApiSpecModel, VariableModel as V
    from ..runner import load_env

    from ....db.notebook_models import NotebookEnvironmentModel

    out: list[str] = []
    env = await load_env(session, notebook.id)
    if env:
        out.append("Environment variables (write {{name}}): " + ", ".join(
            f"{k}{' (secret)' if isinstance(v, str) and v.startswith('{{ $vars.') else '=' + _short(v, 80)}"
            for k, v in list(env.items())[:40]))
    envs = await session.get(NotebookEnvironmentModel, notebook.id)
    if envs is not None and envs.sets:
        out.append(f"Environments (active: {envs.active or 'none'}; the active one fills {{{{name}}}}): " + "; ".join(
            f"{st.get('name')}: " + (", ".join(
                f"{v.get('name')}{' (secret)' if v.get('secret') is not None else ''}"
                for v in (st.get("vars") or [])[:30] if isinstance(v, dict)) or "no variables")
            for st in envs.sets[:10] if isinstance(st, dict)))
    out.extend(await project_vars_line(session, notebook))
    keys = (await session.execute(select(V.key).where(V.type == "secret")
                                  .where((V.project_id == notebook.project_id) | (V.project_id.is_(None)))
                                  .distinct())).scalars().all()
    keys = [k for k in keys if not _CONN_SECRET.fullmatch(str(k))]  # a connection's own URL: internal, never auth
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


_CONN_SECRET = re.compile(r"CONN_[A-Z0-9_]+_URL")
PROJECT_VARS_SHOWN = 20


async def project_vars_line(session: Any, notebook: Any) -> list[str]:
    """The project's (and team's) non-secret variables in its default environment, with values: site details such as
    BASE_URL. Secrets are never read here."""
    from sqlalchemy import select

    from ....db.models import ProjectModel, VariableModel as V

    p = await session.get(ProjectModel, notebook.project_id)
    if p is None:
        return []
    env = getattr(p, "default_environment", None) or "default"
    rows = (await session.execute(select(V.key, V.value, V.project_id).where(V.team_id == p.team_id)
                                  .where(V.type != "secret").where(V.environment.in_([env, "default"]))
                                  .where((V.project_id == p.id) | (V.project_id.is_(None)))
                                  .order_by(V.key))).all()
    vals: dict[str, str] = {}
    for k, v, pid in rows:
        if k not in vals or pid is not None:  # the project's value wins over the team's
            vals[str(k)] = str(v)
    vals = {k: v for k, v in vals.items() if not _CONN_SECRET.fullmatch(k)}
    if not vals:
        return []
    return ["Project variables (not secrets; copy the value into the spec): " + ", ".join(
        f"{k}={_short(v, 80)}" for k, v in list(vals.items())[:PROJECT_VARS_SHOWN])]


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
                  workflow_ids: set[str], allow_empty: bool = False,
                  removed: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict[str, Any]], list[str]]:
    """``(cells, errors)``: each planned cell normalised through its spec model, plus semantic checks the cell routes
    also make (SELECT-only SQL, change on a writable connection, references to cells that exist, body kinds).
    ``allow_empty``: an empty (or missing) ``cells`` is a valid answer (the model replied with notes only).
    ``removed``: existing cells this same call removes; a planned cell may not refer to them."""
    from ....schemas.notebook import Guard, TOP_LEVEL_ONLY_KINDS, validate_spec
    from ...external_db import ExternalDbError, check_select

    errors: list[str] = []
    out: list[dict[str, Any]] = []
    if allow_empty and (raw_cells is None or raw_cells == []):
        return [], []
    if not isinstance(raw_cells, list) or not raw_cells:
        return [], ["`cells` must be a non-empty list (or give `notes` to answer without cells)"]
    if len(raw_cells) > MAX_PLANNED:
        errors.append(f"plan at most {MAX_PLANNED} cells")
        raw_cells = raw_cells[:MAX_PLANNED]
    known = set(existing_names) - set(removed)
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
        if kind == "browser":
            from .browser import cred_misuse

            bad = cred_misuse(spec.get("steps"))
            if bad:
                errors.append(f"{i} (browser): " + "; ".join(bad[:3]))
                return None
        if kind == "workflow" and spec["workflow_id"] not in workflow_ids:
            errors.append(f"{i} (workflow): unknown workflow {spec['workflow_id']!r}")
            return None
        missing = sorted(r for r in _refs({"s": spec, "g": guard}) if r not in known or r == name)
        if missing:
            gone = [r for r in missing if r in removed]
            errors.append(f"{i}: refers to {', '.join(missing)}, which is not an earlier cell"
                          + (f" ({', '.join(gone)} is removed by this plan)" if gone else ""))
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
    cells, errors = validate_plan(args.get("cells"), existing_names, next_index, writable, readonly, workflow_ids,
                                  allow_empty=has_notes(args) or bool(remove_names(args)),
                                  removed=set(remove_names(args)))
    if errors:
        messages += [
            {"role": "assistant", "content": json.dumps({"tool": TOOL_NAME, "args": args}, default=str)[:20000]},
            {"role": "user", "content": "These planned cells are invalid:\n- " + "\n- ".join(errors[:20])
             + f"\n\nCall {TOOL_NAME} again with the whole corrected plan."},
        ]
        args2 = await _call(messages)
        calls = 2
        cells2, errors2 = validate_plan(args2.get("cells"), existing_names, next_index, writable, readonly, workflow_ids,
                                        allow_empty=has_notes(args2) or bool(remove_names(args2)),
                                        removed=set(remove_names(args2)))
        if cells2 or not cells or (not errors2 and (has_notes(args2) or remove_names(args2))):
            args, cells, errors = args2, cells2, errors2
    if not cells and not ((has_notes(args) or remove_names(args)) and not errors):
        raise AskFailed("plan_invalid", "the planned cells did not validate: " + "; ".join(errors[:5]))
    notes = args.get("notes") if isinstance(args.get("notes"), str) else None
    return {"cells": cells, "summary": str(args.get("summary") or "")[:500], "calls": calls,
            "ms": int((time.monotonic() - t0) * 1000), "rejected": errors, "notes": notes,
            "remove": remove_names(args)}


MAX_REMOVE = 50


def remove_names(args: Any) -> list[str]:
    """The cell names the model asked to remove (``remove: ["c3", ...]``): ``cN`` strings only, deduplicated."""
    raw = args.get("remove") if isinstance(args, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        n = str(x).strip() if isinstance(x, (str, int)) else ""
        if re.fullmatch(r"c\d+", n) and n not in out:
            out.append(n)
    return out[:MAX_REMOVE]


def has_notes(args: Any) -> bool:
    """The model gave notes (an answer or a refusal without cells is then a valid reply)."""
    return isinstance(args, dict) and isinstance(args.get("notes"), str) and bool(args["notes"].strip())


__all__ = ["AskFailed", "SYSTEM", "TOOL", "TOOL_NAME", "build_context", "compact", "has_notes", "plan",
           "remove_names", "run", "selection_line", "validate_plan"]

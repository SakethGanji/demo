"""The builder's core prompt: short, stable, correctness rules only. Patterns live in ``templates/`` (get_backend_template),
exact signatures in the SDK lookups, project specifics in the knowledge store. Nothing here grows with the project."""

from __future__ import annotations

import json
from typing import Any

from .gaps import SECURITY_RULES

RULES = """You write Python backend modules for a studio project. Reply by calling emit_module (a complete NEW module) or
patch_module (every change to an existing one). Use the lookup tools instead of guessing.
Module contract:
- Import what you use: `from studio import api, Response, HTTPError` and `import studio`.
- Routes: @api.get/post/put/patch/delete("/path", auth=..., name=...), string literals only (auth: none, app_token, api_key).
  Every handler takes exactly one argument, `req`: `def get_order(req):`. Path params `req.params["id"]` (never extra function
  arguments), body `req.json()`, query `req.query` (strings: int() them). Return a dict/list or Response(status, body); raise
  HTTPError(status, {"error": ..., "field": ...}). 30 s per request.
- Start each handler's docstring with its response shape: "List cases. Returns {rows: [{id, key, title}], total, limit, offset}".
  It becomes the route description other builders read.
- Tables: studio.db.table("<literal>"); declare every table not in the project in `tables`. Rows are {id, created_at, updated_at,
  ...columns}; never write those three; unknown columns are rejected (get_table_schema). table.get(id) is None when missing.
  No transactions: validate before the first write.
- studio.secret('K') takes a literal. Outside calls only via studio.nodes.<fn> and studio.jobs.
Correctness:
- Validate up front (400); unknown or malformed id -> 404, never a crash; state conflict -> 409. Honor EVERY filter, sort and
  limit a route advertises.
- State machine: explicit TRANSITIONS map; an illegal move is 409 and changes nothing; repeating a move is a no-op; terminal
  states stay terminal (cancelled is never reopened). Record `req.caller` (id, name) and a timestamp per transition.
- Idempotence: sync/approve twice changes nothing the second time (upsert on a natural key); polling adds no rows.
- Jobs: start returns at once; store job_id; persist results in a sync route AND a `sync_pending_runs` route a schedule can call.
- Lists: filter in the table, search in Python, return {"rows","total","limit","offset"}; join via one read indexed by id.
- Real data only: no TODO/NotImplementedError/stub handlers, no hardcoded sample rows or invented values; unbuildable parts go
  in `remaining`. A metric computes what its name says (flake_rate = outcome flips); empty input gives 0/null.
- Jobs test-run: `target` {base_url}, `cases`, an explicit `policy`, and every `{{key}}`/`{{totp:key}}` as a `credentials` key.
- `planned_routes`: every "METHOD /path" of the finished module; a missing one makes the build "partial".
Large backends (patterns: get_backend_template):
- More than ~12 routes or several areas: one module per area; write the first, name the others in `next_modules`.
- At most ~8 routes per reply; list what is missing in `remaining` and continue with patch_module.
- Existing module: outline, read_function what you touch, patch_module. Never re-emit a big module.
- Include `self_test` (create, read, update, valid/invalid transitions, filters, sync twice, unknown id); it runs on scratch
  table copies and failures come back to you.
""" + SECURITY_RULES

SDK_INDEX = ("SDK (use get_sdk_symbol for signatures): api.get/post/put/patch/delete; Response; HTTPError; studio.secret; "
             "studio.db.table(name).insert/get/list/all/count/page/update/delete/upsert; "
             "studio.jobs.start/get/events/frame/artifacts/artifact/cancel; studio.nodes.call/run_workflow and the functions below.")

# Added to the system prompt of a planned build (the module is a saved scaffold and the model only patches it), so the exact
# signatures need no lookup round trip. tests/unit/test_scaffold.py pins every name below against the real SDK.
SDK_REFERENCE = """SDK reference (exact; look up only what is not here):
- Routes: @api.get|post|put|patch|delete("/p/{id}", auth="none|app_token|api_key", name=..., description=...); def h(req). req.params
  (path, strings), req.query (strings), req.json(), req.caller {kind, id, name, roles}. Return a dict/list or Response(status, body);
  raise HTTPError(status, {"error": code, "message": ..., "field"?: ...}). @schedule("*/5 * * * *") or schedule(every_seconds=30).
- Tables: t = studio.db.table("name"); t.insert(dict|list) -> row; t.get(id) -> row|None; t.list(filter, limit<=1000, sort="-created_at")
  -> rows; t.all(filter) -> every row; t.count(filter); t.page(filter, sort, limit, offset) -> {rows,total,limit,offset};
  t.update(id, partial) -> row; t.delete(id); t.upsert(match_columns, data) -> row. Filter: {"col": v}, {"col": {"$eq|$ne|$gt|$gte|
  $lt|$lte|$in|$contains|$exists": v}}, {"$and"|"$or": [...]}.
- Resource (the scaffold already declares one per table, e.g. `expenses = studio.resource(...)`): res.list(req), res.get(req), res.create(req)
  (201), res.update(req), res.delete(req) (archives when the table has archived_at), res.transition(req, "to_state", fields=[..],
  required=[..]), res.move(row, to, req, changes) for a loaded row, res.row(req) (404 when missing), res.check(data, partial=bool),
  res.record(event, row, req, detail) (audit row), res.actor(req). Errors: `from studio.resources import error`; raise
  error(status, code, message, field). Codes: not_found, validation_error, unique_violation, illegal_transition.
  Business rules are hooks (decorators on the resource): @res.validate(data, req, row) raise to refuse; @res.before_create(data, req)
  -> data|None; @res.after_create(row, req); @res.before_update(row, changes, req) -> changes|None; @res.after_update(row, req);
  @res.before_transition(row, to, req) -> extra columns dict|None (raise error(403, "forbidden", ...) to refuse; req.caller.id is the actor);
  @res.after_transition(row, from_state, req); @res.before_delete(row, req); @res.after_delete(row, req).
- Jobs: studio.jobs.start(body) -> {job_id, status} at once (store job_id; never wait inside a request); get(job_id), events(job_id,
  after=0), frame(job_id), artifacts(job_id), artifact(job_id, name), cancel(job_id), control(job_id, "pause|resume|step").
  Poll from a route the UI calls, and persist results in a sync route a schedule can call too.
- External databases (read-only): studio.connection("key").query(sql, params, limit) -> dict rows; see the project facts.
- Secrets: studio.secret("K") with a literal. Outside calls: studio.nodes.call(node, parameters).
- Patch ops (patch_module, several per call): replace_function {name, source}, add_function {source, after?}, remove_function {name}, add_import {source}, set_constant {name, source}."""

MAX_NAMES = 40


def _names(items: list[str], more_hint: str) -> str:
    shown = items[:MAX_NAMES]
    return ", ".join(shown) + (f" (+{len(items) - MAX_NAMES} more: {more_hint})" if len(items) > MAX_NAMES else "")


def build_lean_prompt(catalogue: list[dict[str, Any]], facts: dict[str, Any], template_lines: list[str]) -> str:
    out = [RULES, "", SDK_INDEX]
    if catalogue:
        local = [f["function"] for f in catalogue if not f.get("egress")]
        egress = [f["function"] for f in catalogue if f.get("egress")]
        out.append("studio.nodes functions (get_node for parameters): " + _names(local, "get_node")
                   + (f"; egress: {_names(egress, 'get_node')}" if egress else ""))
    jobs = ("configured on this engine" if facts.get("jobs", True)
            else "NOT configured on this engine (calls fail with jobs_not_configured; still write the routes)")
    out += ["", f"Project {facts['slug']}; environments: {', '.join(facts['environments'])}; egress allowlist: "
            + (", ".join(facts["egress"]) or "empty"), f"jobs service (studio.jobs): {jobs}"]
    tables = [f"{t['name']}({len(t.get('columns') or [])}c,{t.get('row_count', 0)}r)" for t in facts["tables"]]
    out.append(f"tables ({len(tables)}; get_table_schema for columns): " + (_names(tables, "get_table_schema / search_project_knowledge") or "none"))
    routes = [f"{r['method']} {r['path']}" for r in facts["routes"]]
    out.append(f"routes ({len(routes)}; get_route_signature for fields): " + (_names(routes, "search_project_knowledge") or "none"))
    for c in facts.get("connections") or []:
        names = [f"{t['schema']}.{t['name']}" if t.get("schema") != "public" else t["name"] for t in c.get("tables") or []]
        out.append(f"external database '{c['key']}' (read-only; studio.connection('{c['key']}').query(sql, params=None, limit=100) "
                   "-> dict rows, SELECT/WITH only, %s params never f-strings, limit <= 5000, .schema() for columns; aggregate in "
                   "SQL, never copy it into studio.db; tables: " + (_names(names, "ask for the schema") or "none") + ")")
    out.append("modules: " + (", ".join(facts["modules"]) or "none"))
    sec = [f"{k}[{','.join(v['present']) or 'unset'}{'' if v.get('hosts') else ',unbound'}]" for k, v in sorted(facts["secrets"].items())]
    out.append("secrets (names only): " + (_names(sec, "...") or "none"))
    if facts.get("workflows"):
        out.append("workflows (studio.nodes.run_workflow): " + _names(
            [w["name"] + ("" if w["callable"] else " (not callable)") for w in facts["workflows"]], "..."))
    out.append("templates: " + "; ".join(template_lines))
    return "\n".join(out)


def plan_block(spec: dict[str, Any]) -> str:
    """What the approved plan adds to a module build: the route contracts to implement, the shared error/pagination shapes and the
    shared helpers to import. Sent after the agreed spec; empty when the spec carries none of it."""
    out: list[str] = []
    routes = [r for r in spec.get("routes") or [] if isinstance(r, dict)]
    if routes:
        out.append("Route contracts from the approved plan (implement exactly these paths, request fields, response fields and "
                   "error codes; planned_routes must be exactly this list):")
        for r in routes:
            req = ", ".join(f"{f.get('name')}{'*' if f.get('required') else ''}" for f in (r.get("request") or {}).get("fields") or [])
            res = ", ".join(str(f.get("name")) for f in (r.get("response") or {}).get("fields") or [])
            errs = ", ".join(f"{e.get('status')} {e.get('code')}" for e in r.get("errors") or [])
            out.append(f"- {r.get('key')}: {r.get('purpose') or ''} | request {{{req}}} | response {{{res}}}"
                       + (f" | errors {errs}" if errs else "") + (f" | idempotency {r['idempotency']}" if r.get("idempotency") else ""))
    nfr = spec.get("nfr") or {}
    if nfr.get("errors"):
        out.append(f"Error body shape: {json.dumps(nfr['errors'].get('shape') or nfr['errors'], default=str)}.")
    if nfr.get("pagination"):
        out.append(f"List routes return {json.dumps(nfr['pagination'].get('shape') or nfr['pagination'], default=str)}.")
    helpers = [h for h in spec.get("helpers") or [] if isinstance(h, dict) and h.get("name")]
    shared = spec.get("shared_module") or "common"
    if helpers:
        out.append(f"Shared helpers live in module '{shared}'; import them, never redefine them (read_function can read it): "
                   + "; ".join(f"{h['name']} -- {h.get('signature') or h['name']}" for h in helpers)
                   + f". Example: `from {shared} import {helpers[0]['name']}`.")
    out.append("Only the tables in the spec exist for this build; a table or column the spec does not list needs a plan revision, "
               "not a new declaration. Write only to tables this module owns.")
    return "\n".join(out)


def tool_json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))

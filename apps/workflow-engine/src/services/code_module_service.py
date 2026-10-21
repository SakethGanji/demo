"""Python modules + static route discovery (BACKEND-DESIGN §3.8, phase 8, G32).

Decorators (``@api.get("/path", auth=..., name=...)``) are discovered with ``ast`` in the engine —
no user code runs at save time. A module's routes are ``source="code"`` and are created, updated
and removed to match the decorators. A clash with any other route refuses the whole save.
"""

from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiRouteModel, CodeModuleModel, ProjectModel
from ..utils.ids import generate_id
from .api_route_service import ApiRouteService, default_name, path_shape, route_spec, route_to_dict
from .backend_templates import coverage
from .module_schedules import discover_schedules, reconcile as reconcile_schedules
from .project_service import ProjectError

MODULE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}
DECORATOR_KEYWORDS = {"auth", "name", "description"}
ROUTE_AUTHS = {"none", "app_token", "api_key"}


SYSTEM_COLUMNS = ("id", "created_at", "updated_at")
_SCHEMA_TYPES = {"integer": {"type": "integer"}, "number": {"type": "number"}, "string": {"type": "string"},
                 "boolean": {"type": "boolean"}, "datetime": {"type": "string", "format": "date-time"}, "json": {}}


async def approved_plan(session: Any, project_id: str) -> dict[str, Any] | None:
    """The project's approved plan ``{version, body}``, or None (no plan: today's behaviour)."""
    try:
        from . import project_plan
    except ModuleNotFoundError as e:  # the plan module is not part of this build
        if e.name != f"{__package__}.project_plan":
            raise
        return None
    got = await project_plan.load_approved(session, project_id)
    return got if got and isinstance(got.get("body"), dict) else None


def fields_schema(fields: Any) -> dict[str, Any] | None:
    """JSON Schema for a plan route's ``[{name, type, required?}]`` field list (None when there are no fields)."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in fields if isinstance(fields, list) else []:
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            props[f["name"]] = dict(_SCHEMA_TYPES.get(f.get("type"), {}))
            if f.get("required"):
                required.append(f["name"])
    if not props:
        return None
    return {"type": "object", "properties": props, **({"required": required} if required else {})}


def _bad_decorator(dec: ast.AST, message: str) -> None:
    raise ProjectError(422, "invalid_decorator", message,
                       errors=[{"line": dec.lineno, "column": dec.col_offset + 1, "message": message}])


def discover_routes(source: str) -> list[dict[str, Any]]:
    """Parse decorators. Raises ProjectError(422 syntax_error / invalid_decorator)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ProjectError(422, "syntax_error", "module has a syntax error",
                           errors=[{"line": e.lineno or 1, "column": e.offset, "message": e.msg}])
    found = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "api"
                    and dec.func.attr in METHODS):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant) or not isinstance(dec.args[0].value, str):
                _bad_decorator(dec, "path must be a string literal")
            if len(dec.args) > 1:
                _bad_decorator(dec, "only the path may be positional; pass auth=/name= as keywords")
            # Review D1: a non-literal auth/name must never silently become "none" (a public route).
            kw = {}
            for k in dec.keywords:
                if k.arg is None:
                    _bad_decorator(dec, "**kwargs are not allowed in route decorators; use literal auth=/name=")
                if k.arg not in DECORATOR_KEYWORDS:
                    _bad_decorator(dec, f"unknown decorator argument '{k.arg}' (allowed: {', '.join(sorted(DECORATOR_KEYWORDS))})")
                if not (isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)):
                    _bad_decorator(dec, f"'{k.arg}' must be a string literal")
                kw[k.arg] = k.value.value
            if "auth" in kw and kw["auth"] not in ROUTE_AUTHS:
                _bad_decorator(dec, f"auth must be one of {', '.join(sorted(ROUTE_AUTHS))}")
            found.append({"method": METHODS[dec.func.attr], "path": dec.args[0].value, "function": node.name,
                          "auth": kw.get("auth", "none"), "name": kw.get("name"),
                          "description": kw.get("description") or ast.get_docstring(node),
                          "description_explicit": "description" in kw, "line": dec.lineno})
    return found


def _invalid(found: list[dict[str, Any]]) -> ProjectError:
    return ProjectError(422, "module_invalid", f"{len(found)} problem(s) in the module: " + "; ".join(
        f"line {e['line']}: {e['message']}" if e.get("line") else e["message"] for e in found[:3]), errors=found)


def static_checks(source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The module builder's static checks, run on every save: ``(errors, warnings)``.

    Errors (the save is refused): a route handler that cannot be called as ``fn(req)``, and studio names used but never
    imported (``api``, ``Response``, ``error``, ...) - both die with a 5xx on the first request. Warnings: the rest of
    the builder's per-module findings. A syntax error is not reported here (the decorator scan already refuses it)."""
    from .backend_templates import checks, patching
    from .module_generator import SDK_NAMES, undefined_sdk_names

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name)
                   and d.func.value.id == "api" and d.func.attr in METHODS for d in node.decorator_list):
            continue
        a = node.args
        positional = list(a.posonlyargs) + list(a.args)
        required = len(positional) - len(a.defaults)
        required_kw = [k.arg for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
        if required > 1 or required_kw or (not positional and a.vararg is None):
            errors.append({"code": "handler_signature", "line": node.lineno, "column": None,
                           "message": f"route handler '{node.name}' is called as {node.name}(req): it must take exactly one "
                                      f"argument (the request), not ({ast.unparse(a)})"})
    sdk = set(SDK_NAMES)
    seen: set[str] = set()
    for name, line in undefined_sdk_names(source):
        seen.add(name)
        errors.append({"code": "undefined_name", "line": line, "column": None,
                       "message": f"'{name}' is used but not imported (add: {SDK_NAMES[name]})"})
    kit = {"error": "from studio.resources import error"}   # studio helpers a handler calls bare; a miss is a NameError
    for name, line in patching.undefined_names(source):
        if name in sdk or name in seen:
            continue
        item = {"code": "undefined_name", "line": line, "column": None}
        if name in kit:
            errors.append({**item, "message": f"'{name}' is used but not imported (add: {kit[name]})"})
        else:   # may be a typo or a name the sandbox provides: reported, never refused
            warnings.append({**item, "message": f"'{name}' is used but defined nowhere in the module (missing import?)"})
    for p in checks.all_problems(source):
        warnings.append({"code": p.get("code", "check"), "line": p.get("line"), "message": p.get("message", "")})
    return errors, warnings


def module_to_dict(m: CodeModuleModel, with_source: bool = True) -> dict[str, Any]:
    out = {"id": m.id, "project_id": m.project_id, "name": m.name, "source_sha": m.source_sha,
           "created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat()}
    if with_source:
        out["source"] = m.source
    return out


class CodeModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._routes = ApiRouteService(session)

    async def list(self, project: ProjectModel) -> list[dict[str, Any]]:
        rows = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id)
                                      .order_by(CodeModuleModel.name))).scalars().all()
        return [module_to_dict(m, with_source=False) for m in rows]

    async def sources(self, project_id: str) -> dict[str, str]:
        rows = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project_id))).scalars().all()
        return {m.name: m.source for m in rows}

    async def get(self, project: ProjectModel, mid: str) -> CodeModuleModel:
        m = await self._s.get(CodeModuleModel, mid)
        if not m or m.project_id != project.id:
            raise ProjectError(404, "module_not_found", "Module not found")
        return m

    async def _plan(self, project: ProjectModel, module_id: str | None, source: str,
                    old_source: str | None = None, collect: list[ProjectError] | None = None) -> dict[str, Any]:
        """Compute route changes for a module source without writing anything.

        ``collect``: instead of raising on the first route clash, append each clash (with its decorator line)
        and keep planning the other routes — the generate analysis reports all of them at once."""
        decls = discover_routes(source)
        discover_schedules(source)  # an invalid @schedule refuses the save like an invalid route decorator
        # Review C8: a description the code produced last time (decorator/docstring) follows the code;
        # one edited through route settings is preserved unless the decorator now sets description=.
        old_desc: dict[str, Any] = {}
        if old_source is not None:
            try:
                old_desc = {o["function"]: o["description"] for o in discover_routes(old_source)}
            except ProjectError:
                old_desc = {}
        all_routes = await self._routes.rows(project.id)
        # A function can carry several decorators, so one module owns several rows per function: rows are matched to
        # decorators one-to-one, by (method, path shape) first, then by function (a re-pathed decorator keeps its row).
        mine_rows = [r for r in all_routes if r.source == "code" and r.handler_module_id == module_id] if module_id else []
        claimed: set[str] = set()
        others = [r for r in all_routes if not (r.source == "code" and r.handler_module_id == module_id and module_id)]
        # A placeholder route (handler "stub", declared by the UI builder) is ADOPTED by a module that declares the
        # same METHOD + path shape: the row keeps its id/name/schemas/app attachment and gets the python handler.
        stubs_by_shape = {(r.method, r.path_shape): r for r in others if r.handler_type == "stub"}
        adopted_ids: set[str] = set()
        for d in decls:
            stub = stubs_by_shape.get((d["method"], path_shape(d["path"])))
            if stub is not None and "_row" not in d and stub.id not in adopted_ids:
                adopted_ids.add(stub.id)
                d["_row"] = stub
                d["_adopt"] = True
        others = [o for o in others if o.id not in adopted_ids]
        taken_names = {o.name for o in others}
        seen_shapes: dict[tuple[str, str], dict[str, Any]] = {}
        seen_names: dict[str, dict[str, Any]] = {}
        by_shape = {(r.method, r.path_shape): r for r in mine_rows}
        for d in decls:  # exact (method, shape) matches claim their row before any function-name fallback
            row = by_shape.get((d["method"], path_shape(d["path"])))
            if row is not None and row.id not in claimed:
                claimed.add(row.id)
                d["_row"] = row
        create, update = [], []
        for d in decls:
            shape = (d["method"], path_shape(d["path"]))
            if shape in seen_shapes:
                first = seen_shapes[shape]
                msg = f"{d['method']} {d['path']} is declared twice in this module (first on line {first['line']})"
                # UI-DESIGN §4.4: a duplicate inside one module is an invalid decorator (422), not a conflict.
                raise ProjectError(422, "invalid_decorator", msg, line=d["line"],
                                   errors=[{"line": d["line"], "column": None, "message": msg}])
            seen_shapes[shape] = d
            existing = d.get("_row")
            if existing is None:
                existing = next((r for r in mine_rows if r.id not in claimed and r.handler_function == d["function"]), None)
                if existing is not None:
                    claimed.add(existing.id)
            spec = {"name": d["name"] or (existing.name if existing else default_name(d["method"], d["path"])),
                    "method": d["method"], "path": d["path"], "auth": d["auth"],
                    "description": d["description"] if (not existing or d["description_explicit"]
                                                         or existing.description == old_desc.get(d["function"]))
                                   else existing.description,
                    "request_schema": existing.request_schema if existing else None,
                    "response_schema": existing.response_schema if existing else None,
                    "response_mode": existing.response_mode if existing else "auto",
                    "validate_request": existing.validate_request if existing else False,
                    "timeout_s": existing.timeout_s if existing else 30, "handler": {"type": "python"}}
            if not d["name"] and not existing:
                base, n = spec["name"], 2
                while spec["name"] in taken_names:
                    spec["name"], n = f"{base}{n}", n + 1
            if spec["name"] in seen_names:  # two decorators, one operation name: the unique index would 500 on save
                msg = f"route name '{spec['name']}' is used twice in this module (first on line {seen_names[spec['name']]['line']})"
                raise ProjectError(422, "invalid_decorator", msg, line=d["line"],
                                   errors=[{"line": d["line"], "column": None, "message": msg}])
            try:
                await self._routes._validate(project, spec, existing.id if existing else None, others)
            except ProjectError as e:
                # Review C7: point at the decorator line and say who owns the clashing route.
                extra = {"line": d["line"], "errors": [{"line": d["line"], "column": None, "message": e.message}]}
                if e.code in ("route_conflict", "name_taken"):
                    rid = e.extra.get("route_id")
                    handler = await self._routes.handler_info(rid)
                    if handler and handler["type"] == "workflow" and e.code == "route_conflict":
                        e.message = f"{e.message}, handled by workflow '{handler['name']}'"
                        extra["errors"][0]["message"] = e.message
                    extra.update({"existing_route_id": rid, "handler": handler,
                                  "conflicts": [{"method": d["method"], "path": d["path"], "line": d["line"],
                                                 "existing_route_id": rid, "handler": handler, "message": e.message}]})
                clash = ProjectError(e.status, e.code, e.message, **{**e.extra, **extra})
                if collect is not None and e.code in ("route_conflict", "name_taken"):
                    collect.append(clash)
                    continue
                raise clash from None
            taken_names.add(spec["name"])
            seen_names[spec["name"]] = d
            (update if existing else create).append((d, spec, existing))
        remove = [r for r in mine_rows if r.id not in claimed]
        return {"create": create, "update": update, "remove": remove}

    def _summary(self, project: ProjectModel, plan: dict[str, Any]) -> dict[str, Any]:
        return {"created": [{"name": s["name"], "method": s["method"], "path": s["path"], "function": d["function"]} for d, s, _ in plan["create"]],
                "updated": [{"name": s["name"], "method": s["method"], "path": s["path"], "function": d["function"], "id": e.id,
                             **({"adopted_placeholder": True} if d.get("_adopt") else {})} for d, s, e in plan["update"]],
                "removed": [{"name": r.name, "method": r.method, "path": r.path, "id": r.id} for r in plan["remove"]]}

    async def _apply(self, project: ProjectModel, m: CodeModuleModel, plan: dict[str, Any], actor: str | None,
                     contracts: dict[str, tuple[Any, Any]] | None = None):
        now = datetime.now()
        contracts = contracts or {}
        for r in plan["remove"]:
            await self._s.delete(r)
        await self._s.flush()
        for d, spec, existing in plan["update"]:
            existing.name, existing.method, existing.path = spec["name"], spec["method"], spec["path"]
            existing.path_shape, existing.auth, existing.description = path_shape(spec["path"]), spec["auth"], spec["description"]
            existing.handler_function, existing.updated_at = d["function"], now
            if d.get("_adopt"):  # placeholder adopted: same row, now a python route owned by this module
                existing.handler_type, existing.handler_module_id, existing.source = "python", m.id, "code"
                existing.handler_workflow_id = None
            req, resp = contracts.get(coverage.norm(spec["method"], spec["path"]), (None, None))
            if req and existing.request_schema is None:  # a plan contract only fills schemas nobody has set
                existing.request_schema = req
            if resp and existing.response_schema is None:
                existing.response_schema = resp
        for d, spec, _ in plan["create"]:
            req, resp = contracts.get(coverage.norm(spec["method"], spec["path"]), (None, None))
            self._s.add(ApiRouteModel(
                id=generate_id("route"), project_id=project.id, name=spec["name"], method=spec["method"], path=spec["path"],
                path_shape=path_shape(spec["path"]), auth=spec["auth"], description=spec["description"],
                handler_type="python", handler_module_id=m.id, handler_function=d["function"], source="code",
                request_schema=req, response_schema=resp, created_by=actor, created_at=now, updated_at=now))
        await self._s.commit()
        rev, h, changes = await self._routes._record_revision(project)
        return rev, h, [c for c in changes if c["breaking"]]

    async def _dry_result(self, project: ProjectModel, plan: dict[str, Any], module: dict[str, Any]) -> dict[str, Any]:
        """Review C9/D7/D9: every dry run (route or module, save or delete) has one shape:
        {dry_run, routes:{created,updated,removed}, changes, breaking_changes, spec_revision:null, spec_hash:null}."""
        removed_ids = {r.id for r in plan["remove"]}
        by_id = {s["id"]: s for s in (route_spec(r) for r in await self._routes.rows(project.id)) if s["id"] not in removed_ids}
        for d, spec, e in plan["update"]:
            by_id[e.id] = {**by_id[e.id], **{k: spec[k] for k in ("name", "method", "path", "auth", "description")}}
        for d, spec, _ in plan["create"]:
            by_id[f"new:{d['function']}"] = {**spec, "id": f"new:{d['function']}"}
        changes = await self._routes._preview(project, list(by_id.values()))
        return {"module": module, "routes": self._summary(project, plan), "errors": [], "dry_run": True,
                "spec_revision": None, "spec_hash": None, "changes": changes,
                "breaking_changes": [c for c in changes if c["breaking"]]}

    # ── save side effects: tables, app availability, secret warnings ─────────

    async def schema_preflight(self, project: ProjectModel, source: str, tables: Any, plan: dict[str, Any]) -> dict[str, Any]:
        """Decide the schema side of a save BEFORE anything is written. Raises 422 ``schema_not_in_plan`` /
        ``schema_change_refused`` (or 409 ``unique_conflict`` / ``rows_incompatible``) so a refused schema writes nothing.

        With an approved plan the declared tables must be plan tables with plan columns (existing tables are then brought to
        the plan definition, additively); without one the declared columns are diffed against the live tables and only
        additive changes go through. Returns ``{plan_version, tables, contracts}``: the table definitions to ensure and the
        route schemas the plan gives the routes this save creates or adopts."""
        from .data_table_service import DataTableService
        from .module_generator import scan_usage, scan_writes

        svc = DataTableService(self._s)
        pid = project.id
        declared = [t for t in tables if isinstance(t, dict)] if isinstance(tables, list) else []
        approved = await approved_plan(self._s, pid)
        out: dict[str, Any] = {"plan_version": None, "tables": declared, "contracts": {}}
        if approved is not None:
            from . import project_plan

            body, out["plan_version"] = approved["body"], approved.get("version")
            ptables = {t["name"]: t for t in (body.get("data") or {}).get("tables") or [] if isinstance(t, dict) and t.get("name")}
            bad: list[dict[str, Any]] = []
            use: list[dict[str, Any]] = []
            for t in declared:
                name = t.get("name")
                pt = project_plan.table_plan(body, name) if name in ptables else None
                if pt is None:
                    bad.append({"code": "unplanned_table", "table": name,
                                "message": f"table '{name}' is not in the approved plan (v{out['plan_version']}); revise the plan first"})
                    continue
                pcols = {c["name"]: c for c in pt.get("columns") or []}
                for c in t.get("columns") or []:
                    if not isinstance(c, dict) or c.get("name") not in pcols:
                        bad.append({"code": "unplanned_column", "table": name, "column": (c or {}).get("name") if isinstance(c, dict) else c,
                                    "message": f"column '{name}.{c.get('name') if isinstance(c, dict) else c}' is not in the approved plan"})
                    elif c.get("type") != pcols[c["name"]].get("type"):
                        bad.append({"code": "column_differs_from_plan", "table": name, "column": c["name"],
                                    "message": f"column '{name}.{c['name']}' is {c.get('type')} but the plan says {pcols[c['name']].get('type')}"})
                use.append({"name": name, "description": pt.get("description") or t.get("description"),
                            "columns": [c for c in pt.get("columns") or []]})
            usage = scan_usage(source)["tables"]
            declared_names = {t.get("name") for t in declared}
            for n in sorted(usage):
                if n not in ptables and n not in declared_names and await svc.table_by_name(pid, n) is None:
                    bad.append({"code": "unplanned_table", "table": n, "message": f"code uses table '{n}', which is not in the "
                                                                                 f"approved plan and does not exist"})
            for w in scan_writes(source):
                pt = ptables.get(w["table"])
                if pt is None or w["column"] in SYSTEM_COLUMNS or w["column"] in {c["name"] for c in pt.get("columns") or []}:
                    continue
                live = await svc.table_by_name(pid, w["table"])
                if live is None or w["column"] not in {c["name"] for c in live.columns or []}:
                    bad.append({"code": "unplanned_column", "table": w["table"], "column": w["column"], "line": w["line"],
                                "message": f"line {w['line']}: writes '{w['table']}.{w['column']}', which is not in the approved plan"})
            if bad:
                raise ProjectError(422, "schema_not_in_plan", f"{len(bad)} schema item(s) are not in the approved plan "
                                                              f"(v{out['plan_version']}): " + "; ".join(b["message"] for b in bad[:3]),
                                   violations=bad, plan_version=out["plan_version"])
            out["tables"] = use
            routes = {coverage.norm(*r["key"].split(" ", 1)): r["key"] for r in body.get("routes") or []
                      if isinstance(r, dict) and isinstance(r.get("key"), str) and " " in r["key"]}
            for d, spec, existing in plan["create"] + plan["update"]:
                key = routes.get(coverage.norm(spec["method"], spec["path"]))
                rc = project_plan.route_contract(body, key) if key else None
                if rc:
                    out["contracts"][coverage.norm(spec["method"], spec["path"])] = (
                        fields_schema((rc.get("request") or {}).get("fields")), fields_schema((rc.get("response") or {}).get("fields")))
        # destructive / conflicting changes to existing tables refuse the save now, not after the module is written
        refused: list[dict[str, Any]] = []
        for t in out["tables"]:
            live = await svc.table_by_name(pid, t.get("name") or "")
            if live is None:
                continue
            try:
                diff = await svc.diff_declared(live, t)
            except ProjectError:  # an invalid declaration is reported by the save itself (table_errors), as before
                continue
            refused += [{"table": t["name"], **r} for r in diff["refused"]]
            if diff["ops"] and not diff["refused"]:
                await svc.apply_migration(project, t["name"], diff["ops"], dry_run=True)   # 409 unique_conflict / rows_incompatible
        if refused:
            raise ProjectError(422, "schema_change_refused", "the declared tables need destructive changes, which a save never makes: "
                               + "; ".join(r.get("reason") or r.get("op", "?") for r in refused[:3]), refused=refused)
        return out

    async def _extras(self, project: ProjectModel, m_id: str | None, source: str, plan: dict[str, Any],
                      tables: Any, actor: str | None, dry_run: bool, pre: dict[str, Any] | None = None) -> dict[str, Any]:
        """Everything a save reports beyond the routes. Never raises: these are conveniences on top of a save that
        already succeeded (or, in a dry run, would)."""
        from .data_table_service import DataTableService

        pre = pre or {"plan_version": None, "tables": tables}
        out: dict[str, Any] = {}
        if pre.get("plan_version") is not None:
            out["plan_version"] = pre["plan_version"]
        try:
            svc = DataTableService(self._s)
            res = await svc.ensure_tables(project, pre["tables"], actor=actor, dry_run=dry_run, migrate=True,
                                          plan_version=pre.get("plan_version"))
            out["tables_created" if not dry_run else "tables_to_create"] = res["created"]
            out["tables_migrated" if not dry_run else "tables_to_migrate"] = res["migrated"]
            out["schema_refused"] = res["refused"]
            if res["errors"]:
                out["table_errors"] = res["errors"]
            declared = set(res["created"]) | set(res["existing"])
            from .module_generator import scan_usage
            usage = scan_usage(source)
            missing = [n for n in sorted(usage["tables"]) if n not in declared and await svc.table_by_name(project.id, n) is None]
            if missing:
                out["warnings"] = out.get("warnings", []) + [
                    {"code": "table_missing", "table": n,
                     "message": f"code uses table '{n}' but it does not exist; pass it in 'tables' with its columns "
                                f"(or create it) before calling these routes"} for n in missing]
        except Exception as e:  # noqa: BLE001
            out["table_errors"] = [{"name": None, "message": f"{type(e).__name__}: {e}"}]
        try:
            out["warnings"] = out.get("warnings", []) + await self.secret_warnings(project, source)
        except Exception:  # noqa: BLE001
            pass
        try:
            out["warnings"] = out.get("warnings", []) + await self.write_warnings(project, source, pre["tables"])
        except Exception:  # noqa: BLE001
            pass
        out["warnings"] = out.get("warnings", []) + static_checks(source)[1]
        if not dry_run and plan["create"]:
            try:
                av = await self.app_availability(project, m_id, [s["name"] for _, s, _ in plan["create"]])
                if av:
                    out["apps"] = av
            except Exception:  # noqa: BLE001
                pass
        return out

    async def write_warnings(self, project: ProjectModel, source: str, declared: Any) -> list[dict[str, Any]]:
        """Writes of a column the table does not have (declared in this save, else the live table), like the builder's check."""
        from .data_table_service import DataTableService
        from .module_generator import scan_writes

        svc = DataTableService(self._s)
        by_decl = {t.get("name"): t for t in declared or [] if isinstance(t, dict)}
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for w in scan_writes(source):
            key = (w["table"], w["column"])
            if key in seen or w["column"] in SYSTEM_COLUMNS:
                continue
            seen.add(key)
            if w["table"] in by_decl:
                cols = {c.get("name") for c in by_decl[w["table"]].get("columns") or [] if isinstance(c, dict)}
            else:
                live = await svc.table_by_name(project.id, w["table"])
                if live is None:
                    continue   # reported as table_missing
                cols = {c["name"] for c in live.columns or []}
            if w["column"] not in cols:
                out.append({"code": "unknown_column", "line": w["line"], "table": w["table"], "column": w["column"],
                            "message": f"line {w['line']}: table '{w['table']}' has no column '{w['column']}' "
                                       f"(columns: {', '.join(sorted(c for c in cols if c)) or 'none'})"})
        return out

    def refuse_invalid(self, source: str) -> list[dict[str, Any]]:
        errors, _ = static_checks(source)
        return errors

    async def secret_warnings(self, project: ProjectModel, source: str) -> list[dict[str, Any]]:
        """One warning per secret the code reads that has no allowed_hosts (egress would be refused at run time),
        naming the hosts the code calls. Policy is untouched: this only tells the author."""
        from .module_generator import scan_usage
        from .secret_service import SecretService

        used = scan_usage(source)["secrets"]
        if not used:
            return []
        hosts = sorted(set(re.findall(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*)", source)))
        by_key: dict[str, list[Any]] = {}
        for row in await SecretService(self._s).effective(project):
            if row.get("type") == "secret":
                by_key.setdefault(row["key"], []).append(row.get("allowed_hosts"))
        out = []
        for key in sorted(used):
            if key not in by_key:
                out.append({"code": "secret_missing", "secret": key,
                            "message": f"secret '{key}' is used but not set in this project"
                                       + (f"; when you add it, set allowed_hosts to {hosts}" if hosts else "")})
            elif not any(by_key[key]):
                out.append({"code": "secret_no_allowed_hosts", "secret": key, "hosts": hosts,
                            "message": f"secret '{key}' has no allowed_hosts, so runs that send it will be refused"
                                       + (f"; the code calls {', '.join(hosts)} - set allowed_hosts on '{key}' to those hosts"
                                          if hosts else "; set allowed_hosts to the host(s) it is sent to")})
        return out

    async def app_availability(self, project: ProjectModel, module_id: str | None, new_ops: list[str]) -> list[dict[str, Any]]:
        """Routes are not stored per app: an app reaches a route through the project-wide generated client (which
        already includes every new route) and 'uses' it only by calling it in its source. So 'attaching' = telling the
        apps that already call this module's routes which new operations they can now call."""
        from .api_usage import ApiUsageService

        if not module_id:
            return []
        mine = {r.name for r in await self._routes.rows(project.id) if r.handler_module_id == module_id}
        apps: dict[str, dict[str, Any]] = {}
        for row in await ApiUsageService(self._s).project_usage(project):
            if row.get("operation") in mine and row["operation"] not in new_ops:
                apps.setdefault(row["app_id"], {"app_id": row["app_id"], "app_name": row["app_name"]})
        return [{**a, "new_operations": new_ops,
                 "note": "available now in the app's generated api client; call them from the app source to use them"}
                for a in apps.values()]

    async def _check_name(self, project: ProjectModel, name: str) -> None:
        if not MODULE_NAME_RE.match(name or "") or name in ("studio",):
            raise ProjectError(422, "invalid_name", "module name must be a lowercase python identifier (not 'studio')")
        if (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id,
                                                                CodeModuleModel.name == name))).scalars().first():
            raise ProjectError(409, "name_taken", f"module '{name}' already exists")

    async def create(self, project: ProjectModel, name: str, source: str, actor: str | None = None,
                     dry_run: bool = False, tables: Any = None) -> dict[str, Any]:
        await self._check_name(project, name)
        plan = await self._plan(project, None, source)
        pre = await self.schema_preflight(project, source, tables, plan)
        found = self.refuse_invalid(source)
        if found and not dry_run:
            raise _invalid(found)
        if dry_run:
            res = await self._dry_result(project, plan, {"id": None, "project_id": project.id, "name": name,
                                                         "source_sha": hashlib.sha256(source.encode()).hexdigest(),
                                                         "source": source})
            return {**res, **await self._extras(project, None, source, plan, tables, actor, True, pre), "errors": found}
        now = datetime.now()
        m = CodeModuleModel(id=generate_id("mod"), project_id=project.id, name=name, source=source,
                            source_sha=hashlib.sha256(source.encode()).hexdigest(), created_by=actor,
                            created_at=now, updated_at=now)
        self._s.add(m)
        await self._s.flush()
        summary = self._summary(project, plan)
        rev, h, breaking = await self._apply(project, m, plan, actor, pre["contracts"])
        await reconcile_schedules(self._s, project.id, m.id, source)
        extras = await self._extras(project, m.id, source, plan, tables, actor, False, pre)
        await self._record(project, m.name, None, source)
        return {"module": module_to_dict(m), "routes": await self._with_ids(project, m, summary), "errors": [],
                "spec_revision": rev, "spec_hash": h, "breaking_changes": breaking, **extras}

    async def _record(self, project: ProjectModel, name: str, before: str | None, after: str | None,
                      old_name: str | None = None) -> None:
        """The project file change log (never fails the save)."""
        from . import project_files as pf

        changes = [{"path": pf.module_path(name), "before": before if old_name in (None, name) else None, "after": after}]
        if old_name not in (None, name):
            changes.append({"path": pf.module_path(old_name), "before": before, "after": None, "summary": f"renamed to {name}.py"})
        await pf.record_changes(self._s, project.id, changes, commit=True)

    async def _with_ids(self, project: ProjectModel, m: CodeModuleModel, summary: dict[str, Any]) -> dict[str, Any]:
        rows = {(r.method, r.path_shape): r for r in await self._routes.rows(project.id) if r.handler_module_id == m.id}
        for item in summary["created"] + summary["updated"]:
            r = rows.get((item["method"], path_shape(item["path"])))
            if r:
                item["id"] = r.id
        return summary

    async def update(self, project: ProjectModel, mid: str, source: str, dry_run: bool = False,
                     actor: str | None = None, base_source_sha: str | None = None,
                     base_updated_at: str | None = None, new_name: str | None = None,
                     tables: Any = None) -> dict[str, Any]:
        # Audit #5: serialise saves of one module — the sha check and the write happen under a row lock, so two
        # PUTs with the same base cannot both win (the loser sees the winner's sha → 409 module_changed).
        m = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.id == mid)
                                   .with_for_update().execution_options(populate_existing=True))).scalars().first()
        if not m or m.project_id != project.id:
            raise ProjectError(404, "module_not_found", "Module not found")
        if new_name is not None and new_name != m.name:
            await self._check_name(project, new_name)
            refs = await self._name_references(project, m)
            if refs:
                raise ProjectError(409, "module_referenced",
                                   f"{m.name}.py is referenced by {len(refs)} place(s); renaming it would break them",
                                   references=refs)
        stale = (base_source_sha is not None and base_source_sha != m.source_sha) or \
                (base_updated_at is not None and base_updated_at != m.updated_at.isoformat())
        if stale:  # review B4: never silently overwrite a newer save from another tab / the builder
            raise ProjectError(409, "module_changed", f"{m.name}.py changed since you opened it",
                               current_sha=m.source_sha, updated_at=m.updated_at.isoformat(),
                               current={"source_sha": m.source_sha, "updated_at": m.updated_at.isoformat()})
        plan = await self._plan(project, m.id, source, old_source=m.source)
        pre = await self.schema_preflight(project, source, tables, plan)
        found = self.refuse_invalid(source)
        if found and not dry_run:
            raise _invalid(found)
        summary = self._summary(project, plan)
        if dry_run:
            res = await self._dry_result(project, plan, {**module_to_dict(m), **({"name": new_name} if new_name else {})})
            return {**res, **await self._extras(project, m.id, source, plan, tables, actor, True, pre), "errors": found}
        old_name, old_source = m.name, m.source
        if new_name:
            m.name = new_name
        m.source, m.source_sha, m.updated_at = source, hashlib.sha256(source.encode()).hexdigest(), datetime.now()
        rev, h, breaking = await self._apply(project, m, plan, actor, pre["contracts"])
        await reconcile_schedules(self._s, project.id, m.id, source)
        extras = await self._extras(project, m.id, source, plan, tables, actor, False, pre)
        await self._record(project, m.name, old_source, source, old_name)
        return {"module": module_to_dict(m), "routes": await self._with_ids(project, m, summary), "errors": [],
                "spec_revision": rev, "spec_hash": h, "breaking_changes": breaking, **extras}

    async def _name_references(self, project: ProjectModel, m: CodeModuleModel) -> list[dict[str, Any]]:
        """Places that name this module: PythonStep nodes in the project's workflows and other modules' imports."""
        from ..db.models import WorkflowModel

        refs: list[dict[str, Any]] = []
        for w in (await self._s.execute(select(WorkflowModel).where(WorkflowModel.project_id == project.id))).scalars().all():
            for node in (w.draft_definition or {}).get("nodes", []):
                if node.get("type") == "PythonStep" and (node.get("parameters") or {}).get("module") == m.name:
                    refs.append({"kind": "workflow", "id": w.id, "name": w.name, "node": node.get("name")})
        pat = re.compile(rf"^\s*(?:import\s+{re.escape(m.name)}\b|from\s+{re.escape(m.name)}\s+import\b)", re.M)
        for other in (await self._s.execute(select(CodeModuleModel).where(
                CodeModuleModel.project_id == project.id, CodeModuleModel.id != m.id))).scalars().all():
            hit = pat.search(other.source)
            if hit:
                refs.append({"kind": "module", "id": other.id, "name": other.name,
                             "line": other.source.count("\n", 0, hit.start()) + 1})
        return refs

    async def delete(self, project: ProjectModel, mid: str, dry_run: bool = False) -> dict[str, Any]:
        m = await self.get(project, mid)
        mine = [r for r in await self._routes.rows(project.id) if r.handler_module_id == m.id]
        removed = [{"name": r.name, "method": r.method, "path": r.path, "id": r.id} for r in mine]
        if dry_run:  # review D9: what a delete would remove/break, without deleting
            return await self._dry_result(project, {"create": [], "update": [], "remove": mine}, module_to_dict(m))
        for r in mine:
            await self._s.delete(r)
        await self._s.flush()
        await reconcile_schedules(self._s, project.id, m.id, "")  # no decorators left: drops the schedule rows
        name, source = m.name, m.source
        await self._s.delete(m)
        await self._s.commit()
        await self._record(project, name, source, None)
        rev, h, changes = await self._routes._record_revision(project)
        return {"routes": {"removed": removed}, "spec_revision": rev, "spec_hash": h,
                "breaking_changes": [c for c in changes if c["breaking"]]}

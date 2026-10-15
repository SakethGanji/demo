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
from .project_service import ProjectError

MODULE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}


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
                raise ProjectError(422, "invalid_decorator", "route path must be a string literal",
                                   errors=[{"line": dec.lineno, "message": "path must be a string literal"}])
            kw = {}
            for k in dec.keywords:
                if k.arg in ("auth", "name", "description") and isinstance(k.value, ast.Constant):
                    kw[k.arg] = k.value.value
            found.append({"method": METHODS[dec.func.attr], "path": dec.args[0].value, "function": node.name,
                          "auth": kw.get("auth", "none"), "name": kw.get("name"),
                          "description": kw.get("description") or ast.get_docstring(node), "line": dec.lineno})
    return found


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

    async def _plan(self, project: ProjectModel, module_id: str | None, source: str) -> dict[str, Any]:
        """Compute route changes for a module source without writing anything."""
        decls = discover_routes(source)
        all_routes = await self._routes.rows(project.id)
        mine = {r.handler_function: r for r in all_routes if r.source == "code" and r.handler_module_id == module_id} if module_id else {}
        others = [r for r in all_routes if not (r.source == "code" and r.handler_module_id == module_id and module_id)]
        taken_names = {o.name for o in others}
        seen_shapes: set[tuple[str, str]] = set()
        create, update = [], []
        for d in decls:
            shape = (d["method"], path_shape(d["path"]))
            if shape in seen_shapes:
                raise ProjectError(409, "route_conflict", f"{d['method']} {d['path']} is declared twice in this module",
                                   line=d["line"])
            seen_shapes.add(shape)
            existing = mine.get(d["function"])
            spec = {"name": d["name"] or (existing.name if existing else default_name(d["method"], d["path"])),
                    "method": d["method"], "path": d["path"], "auth": d["auth"], "description": d["description"],
                    "request_schema": existing.request_schema if existing else None,
                    "response_schema": existing.response_schema if existing else None,
                    "response_mode": "auto", "validate_request": existing.validate_request if existing else False,
                    "timeout_s": existing.timeout_s if existing else 30, "handler": {"type": "python"}}
            if not d["name"] and not existing:
                base, n = spec["name"], 2
                while spec["name"] in taken_names:
                    spec["name"], n = f"{base}{n}", n + 1
            await self._routes._validate(project, spec, existing.id if existing else None, others)
            taken_names.add(spec["name"])
            (update if existing else create).append((d, spec, existing))
        keep = {d["function"] for d in decls}
        remove = [r for fn, r in mine.items() if fn not in keep]
        return {"create": create, "update": update, "remove": remove}

    def _summary(self, project: ProjectModel, plan: dict[str, Any]) -> dict[str, Any]:
        return {"created": [{"name": s["name"], "method": s["method"], "path": s["path"], "function": d["function"]} for d, s, _ in plan["create"]],
                "updated": [{"name": s["name"], "method": s["method"], "path": s["path"], "function": d["function"], "id": e.id} for d, s, e in plan["update"]],
                "removed": [{"name": r.name, "method": r.method, "path": r.path, "id": r.id} for r in plan["remove"]]}

    async def _apply(self, project: ProjectModel, m: CodeModuleModel, plan: dict[str, Any], actor: str | None):
        now = datetime.now()
        for r in plan["remove"]:
            await self._s.delete(r)
        await self._s.flush()
        for d, spec, existing in plan["update"]:
            existing.name, existing.method, existing.path = spec["name"], spec["method"], spec["path"]
            existing.path_shape, existing.auth, existing.description = path_shape(spec["path"]), spec["auth"], spec["description"]
            existing.handler_function, existing.updated_at = d["function"], now
        for d, spec, _ in plan["create"]:
            self._s.add(ApiRouteModel(
                id=generate_id("route"), project_id=project.id, name=spec["name"], method=spec["method"], path=spec["path"],
                path_shape=path_shape(spec["path"]), auth=spec["auth"], description=spec["description"],
                handler_type="python", handler_module_id=m.id, handler_function=d["function"], source="code",
                created_by=actor, created_at=now, updated_at=now))
        await self._s.commit()
        rev, h, changes = await self._routes._record_revision(project)
        return rev, h, [c for c in changes if c["breaking"]]

    async def create(self, project: ProjectModel, name: str, source: str, actor: str | None = None) -> dict[str, Any]:
        if not MODULE_NAME_RE.match(name or "") or name in ("studio",):
            raise ProjectError(422, "invalid_name", "module name must be a lowercase python identifier (not 'studio')")
        if (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id,
                                                                CodeModuleModel.name == name))).scalars().first():
            raise ProjectError(409, "name_taken", f"module '{name}' already exists")
        plan = await self._plan(project, None, source)
        now = datetime.now()
        m = CodeModuleModel(id=generate_id("mod"), project_id=project.id, name=name, source=source,
                            source_sha=hashlib.sha256(source.encode()).hexdigest(), created_by=actor,
                            created_at=now, updated_at=now)
        self._s.add(m)
        await self._s.flush()
        summary = self._summary(project, plan)
        rev, h, breaking = await self._apply(project, m, plan, actor)
        return {"module": module_to_dict(m), "routes": await self._with_ids(project, m, summary), "errors": [],
                "spec_revision": rev, "spec_hash": h, "breaking_changes": breaking}

    async def _with_ids(self, project: ProjectModel, m: CodeModuleModel, summary: dict[str, Any]) -> dict[str, Any]:
        rows = {r.handler_function: r for r in await self._routes.rows(project.id) if r.handler_module_id == m.id}
        for item in summary["created"] + summary["updated"]:
            r = rows.get(item["function"])
            if r:
                item["id"] = r.id
        return summary

    async def update(self, project: ProjectModel, mid: str, source: str, dry_run: bool = False,
                     actor: str | None = None) -> dict[str, Any]:
        m = await self.get(project, mid)
        plan = await self._plan(project, m.id, source)
        summary = self._summary(project, plan)
        if dry_run:
            specs = [route_spec(r) for r in await self._routes.rows(project.id) if r not in plan["remove"]]
            by_id = {s["id"]: s for s in specs}
            for d, spec, e in plan["update"]:
                by_id[e.id] = {**by_id[e.id], **{k: spec[k] for k in ("name", "method", "path", "auth", "description")}}
            for d, spec, _ in plan["create"]:
                by_id[f"new:{d['function']}"] = {**spec, "id": f"new:{d['function']}"}
            changes = await self._routes._preview(project, list(by_id.values()))
            return {"module": module_to_dict(m), "routes": summary, "errors": [], "dry_run": True,
                    "spec_revision": None, "spec_hash": None, "breaking_changes": [c for c in changes if c["breaking"]]}
        m.source, m.source_sha, m.updated_at = source, hashlib.sha256(source.encode()).hexdigest(), datetime.now()
        rev, h, breaking = await self._apply(project, m, plan, actor)
        return {"module": module_to_dict(m), "routes": await self._with_ids(project, m, summary), "errors": [],
                "spec_revision": rev, "spec_hash": h, "breaking_changes": breaking}

    async def delete(self, project: ProjectModel, mid: str) -> dict[str, Any]:
        m = await self.get(project, mid)
        mine = [r for r in await self._routes.rows(project.id) if r.handler_module_id == m.id]
        removed = [{"name": r.name, "method": r.method, "path": r.path, "id": r.id} for r in mine]
        for r in mine:
            await self._s.delete(r)
        await self._s.flush()
        await self._s.delete(m)
        await self._s.commit()
        rev, h, changes = await self._routes._record_revision(project)
        return {"routes": {"removed": removed}, "spec_revision": rev, "spec_hash": h,
                "breaking_changes": [c for c in changes if c["breaking"]]}

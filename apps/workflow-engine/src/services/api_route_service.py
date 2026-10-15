"""API routes: CRUD, validation, spec revisions, breaking-change diffs (BACKEND-DESIGN §3.5, phase 5)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiRouteModel, ApiSpecRevisionModel, ProjectModel, WorkflowModel
from ..utils.ids import generate_id
from .project_service import ProjectError
from .spec_tools import PARAM_RE, build_openapi, diff_specs, spec_hash

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
AUTHS = {"none", "app_token", "api_key"}
RESPONSE_MODES = {"auto", "first_item", "all_items"}
PATH_RE = re.compile(r"^(/|(/([A-Za-z0-9._~-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))+)$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def path_shape(path: str) -> str:
    return PARAM_RE.sub("{}", path)


def default_name(method: str, path: str) -> str:
    parts = [method.lower()]
    for seg in [s for s in path.split("/") if s]:
        m = PARAM_RE.fullmatch(seg)
        if m:
            parts.append("By" + _camel(m.group(1)))
        else:
            parts.append(_camel(seg))
    return "".join(parts) if len(parts) > 1 else parts[0] + "Root"


def _camel(s: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", s) if w]
    return "".join(w[:1].upper() + w[1:] for w in words)


def route_spec(r: ApiRouteModel) -> dict[str, Any]:
    """The serialisable route definition (used for specs, snapshots and dispatch)."""
    handler: dict[str, Any] = {"type": r.handler_type}
    if r.handler_type == "workflow":
        handler["workflow_id"] = r.handler_workflow_id
    elif r.handler_type == "python":
        handler.update({"module_id": r.handler_module_id, "function": r.handler_function})
    return {
        "id": r.id, "name": r.name, "method": r.method, "path": r.path, "auth": r.auth,
        "description": r.description, "request_schema": r.request_schema, "response_schema": r.response_schema,
        "response_mode": r.response_mode, "validate_request": r.validate_request, "handler": handler,
        "source": r.source, "timeout_s": r.timeout_s,
    }


def route_to_dict(r: ApiRouteModel, slug: str, used_by_count: int = 0) -> dict[str, Any]:
    return {
        **route_spec(r), "project_id": r.project_id, "used_by_count": used_by_count,
        "url": f"/api/p/{slug}{r.path}" if r.path != "/" else f"/api/p/{slug}/",
        "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
    }


class ApiRouteService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── queries ──────────────────────────────────────────────────────────

    async def rows(self, project_id: str) -> list[ApiRouteModel]:
        res = await self._s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == project_id)
                                    .order_by(ApiRouteModel.path, ApiRouteModel.method))
        return list(res.scalars().all())

    async def get(self, project: ProjectModel, rid: str) -> ApiRouteModel:
        r = await self._s.get(ApiRouteModel, rid)
        if not r or r.project_id != project.id:
            raise ProjectError(404, "route_not_found", "Route not found")
        return r

    async def used_by_counts(self, project: ProjectModel) -> dict[str, int]:
        try:
            from .api_usage import ApiUsageService
        except ImportError:
            return {}
        return await ApiUsageService(self._s).used_by_counts(project)

    async def list(self, project: ProjectModel) -> list[dict[str, Any]]:
        counts = await self.used_by_counts(project)
        return [route_to_dict(r, project.slug, counts.get(r.id, 0)) for r in await self.rows(project.id)]

    async def to_dict(self, project: ProjectModel, r: ApiRouteModel) -> dict[str, Any]:
        counts = await self.used_by_counts(project)
        return route_to_dict(r, project.slug, counts.get(r.id, 0))

    # ── specs ────────────────────────────────────────────────────────────

    async def latest_revision(self, project_id: str) -> ApiSpecRevisionModel | None:
        res = await self._s.execute(select(ApiSpecRevisionModel).where(ApiSpecRevisionModel.project_id == project_id)
                                    .order_by(ApiSpecRevisionModel.revision.desc()).limit(1))
        return res.scalars().first()

    async def current_spec(self, project: ProjectModel) -> dict[str, Any]:
        rev = await self.latest_revision(project.id)
        return build_openapi(project, [route_spec(r) for r in await self.rows(project.id)],
                             rev.revision if rev else None)

    async def _record_revision(self, project: ProjectModel) -> tuple[int | None, str, list[dict[str, Any]]]:
        """Regenerate the draft spec; store a revision when it changed. Returns (revision, hash, changes)."""
        latest = await self.latest_revision(project.id)
        specs = [route_spec(r) for r in await self.rows(project.id)]
        new = build_openapi(project, specs, (latest.revision + 1) if latest else 1)
        h = spec_hash(new)
        if latest and latest.spec_hash == h:
            return latest.revision, h, []
        changes = diff_specs(latest.spec if latest else None, new)
        changes = await self._with_affected(project, changes)
        rev = ApiSpecRevisionModel(project_id=project.id, revision=(latest.revision + 1) if latest else 1,
                                   spec_hash=h, spec=new, changes=changes, created_at=datetime.now())
        self._s.add(rev)
        await self._s.commit()
        return rev.revision, h, changes

    async def _with_affected(self, project: ProjectModel, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            from .api_usage import ApiUsageService
        except ImportError:
            return changes
        return await ApiUsageService(self._s).annotate_changes(project, changes)

    async def _preview(self, project: ProjectModel, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        old = build_openapi(project, [route_spec(r) for r in await self.rows(project.id)])
        new = build_openapi(project, specs)
        return await self._with_affected(project, diff_specs(old, new))

    # ── validation ───────────────────────────────────────────────────────

    async def _validate(self, project: ProjectModel, spec: dict[str, Any], exclude_id: str | None,
                        others: list[ApiRouteModel]) -> None:
        if spec["method"] not in METHODS:
            raise ProjectError(422, "invalid_method", f"method must be one of {sorted(METHODS)}")
        p = spec["path"]
        if not isinstance(p, str) or not PATH_RE.match(p) or (len(p) > 1 and p.endswith("/")):
            raise ProjectError(422, "invalid_path", "path must start with '/' and use {param} segments, no trailing '/'")
        names = PARAM_RE.findall(p)
        if len(set(names)) != len(names):
            raise ProjectError(422, "invalid_path", "path parameters must be unique")
        if spec["auth"] not in AUTHS:
            raise ProjectError(422, "invalid_auth", f"auth must be one of {sorted(AUTHS)}")
        if spec["response_mode"] not in RESPONSE_MODES:
            raise ProjectError(422, "invalid_response_mode", f"response_mode must be one of {sorted(RESPONSE_MODES)}")
        if not NAME_RE.match(spec["name"] or ""):
            raise ProjectError(422, "invalid_name", "name must be an identifier (operationId)")
        for key in ("request_schema", "response_schema"):
            if spec.get(key) is not None and not isinstance(spec[key], dict):
                raise ProjectError(422, "invalid_schema", f"{key} must be a JSON Schema object")
        for o in others:
            if o.id == exclude_id:
                continue
            if o.method == spec["method"] and o.path_shape == path_shape(p):
                raise ProjectError(409, "route_conflict", f"{o.method} {o.path} already exists", route_id=o.id)
            if o.name == spec["name"]:
                raise ProjectError(409, "name_taken", f"operation name '{spec['name']}' is already used", route_id=o.id)

    def _unique_name(self, base: str, taken: set[str]) -> str:
        name, n = base, 2
        while name in taken:
            name, n = f"{base}{n}", n + 1
        return name

    async def _resolve_handler(self, project: ProjectModel, handler: dict[str, Any] | None, spec: dict[str, Any],
                               dry_run: bool, actor: str | None) -> dict[str, Any]:
        handler = handler or {"type": "stub"}
        t = handler.get("type")
        if t == "stub":
            return {"type": "stub"}
        if t == "workflow":
            if handler.get("create"):
                if dry_run:
                    return {"type": "workflow", "workflow_id": "wf_(new)"}
                wid = await self._create_handler_workflow(project, spec, actor)
                return {"type": "workflow", "workflow_id": wid}
            wid = handler.get("workflow_id")
            wf = await self._s.get(WorkflowModel, wid) if wid else None
            if not wf or wf.project_id != project.id:
                raise ProjectError(422, "handler_not_in_project", "handler workflow must exist in this project",
                                   workflow_id=wid)
            return {"type": "workflow", "workflow_id": wid}
        if t == "python":
            try:
                from ..db.models import CodeModuleModel
            except ImportError:
                raise ProjectError(422, "invalid_handler", "python handlers are declared in code modules")
            mod = await self._s.get(CodeModuleModel, handler.get("module_id")) if handler.get("module_id") else None
            if not mod or mod.project_id != project.id or not handler.get("function"):
                raise ProjectError(422, "handler_not_in_project", "python handler module must exist in this project")
            return {"type": "python", "module_id": mod.id, "function": handler["function"]}
        raise ProjectError(422, "invalid_handler", "handler.type must be workflow, python or stub")

    async def _create_handler_workflow(self, project: ProjectModel, spec: dict[str, Any], actor: str | None) -> str:
        from ..utils.ids import workflow_id
        now = datetime.now()
        wid = workflow_id()
        definition = {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0},
                 "retry_on_fail": 0, "retry_delay": 1000, "continue_on_fail": False},
                {"name": "Respond", "type": "Respond",
                 "parameters": {"statusCode": 200, "headers": [], "body": "{{ $json.req.params }}",
                                "contentType": "application/json"},
                 "position": {"x": 250, "y": 0}, "retry_on_fail": 0, "retry_delay": 1000, "continue_on_fail": False},
            ],
            "connections": [{"source_node": "Start", "target_node": "Respond", "source_output": "main", "target_input": "main"}],
            "settings": {},
        }
        self._s.add(WorkflowModel(id=wid, name=f"{spec['method']} {spec['path']}", project_id=project.id,
                                  draft_definition=definition, active=False, created_by=actor,
                                  created_at=now, updated_at=now))
        await self._s.flush()
        return wid

    # ── writes ───────────────────────────────────────────────────────────

    def _spec_from_body(self, body: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
        base = dict(base or {"name": None, "method": None, "path": None, "auth": "none", "description": None,
                             "request_schema": None, "response_schema": None, "response_mode": "auto",
                             "validate_request": False, "handler": {"type": "stub"}, "timeout_s": 30})
        for k in ("name", "auth", "description", "request_schema", "response_schema", "response_mode",
                  "validate_request", "timeout_s"):
            if k in body:
                base[k] = body[k]
        if "method" in body and body["method"] is not None:
            base["method"] = str(body["method"]).upper()
        if "path" in body and body["path"] is not None:
            base["path"] = body["path"]
        return base

    async def create(self, project: ProjectModel, body: dict[str, Any], actor: str | None = None,
                     source: str = "ui") -> dict[str, Any]:
        others = await self.rows(project.id)
        spec = self._spec_from_body(body)
        if not spec["method"] or not spec["path"]:
            raise ProjectError(422, "invalid_route", "method and path are required")
        spec["method"] = spec["method"].upper()
        if not spec.get("name"):
            spec["name"] = self._unique_name(default_name(spec["method"], spec["path"]) if PATH_RE.match(str(spec["path"])) else "route",
                                             {o.name for o in others})
        await self._validate(project, spec, None, others)
        spec["handler"] = await self._resolve_handler(project, body.get("handler"), spec, False, actor)
        now = datetime.now()
        r = ApiRouteModel(
            id=generate_id("route"), project_id=project.id, name=spec["name"], method=spec["method"], path=spec["path"],
            path_shape=path_shape(spec["path"]), auth=spec["auth"], description=spec["description"],
            request_schema=spec["request_schema"], response_schema=spec["response_schema"],
            response_mode=spec["response_mode"], validate_request=bool(spec["validate_request"]),
            handler_type=spec["handler"]["type"], handler_workflow_id=spec["handler"].get("workflow_id"),
            handler_module_id=spec["handler"].get("module_id"), handler_function=spec["handler"].get("function"),
            source=source, timeout_s=int(spec["timeout_s"] or 30), created_by=actor, created_at=now, updated_at=now,
        )
        self._s.add(r)
        await self._s.commit()
        rev, h, changes = await self._record_revision(project)
        return {"route": await self.to_dict(project, r), "spec_revision": rev, "spec_hash": h,
                "breaking_changes": [c for c in changes if c["breaking"]]}

    async def update(self, project: ProjectModel, rid: str, body: dict[str, Any], dry_run: bool = False,
                     actor: str | None = None, allow_code: bool = False) -> dict[str, Any]:
        r = await self.get(project, rid)
        if r.source == "code" and not allow_code:
            changed = {k for k in body if k not in ("request_schema", "response_schema", "description",
                                                    "response_mode", "validate_request", "timeout_s")}
            if changed:
                raise ProjectError(409, "route_managed_by_code",
                                   "method/path/auth/handler of code routes are set by the module's decorators",
                                   fields=sorted(changed))
        others = await self.rows(project.id)
        spec = self._spec_from_body(body, route_spec(r))
        await self._validate(project, spec, r.id, others)
        if "handler" in body:
            spec["handler"] = await self._resolve_handler(project, body["handler"], spec, dry_run, actor)
        if dry_run:
            specs = [route_spec(o) for o in others if o.id != r.id] + [{**spec, "id": r.id}]
            changes = await self._preview(project, specs)
            latest = await self.latest_revision(project.id)
            return {"route": {**route_to_dict(r, project.slug), **spec}, "spec_revision": latest.revision if latest else None,
                    "spec_hash": latest.spec_hash if latest else None, "dry_run": True,
                    "routes": {"created": [], "removed": [],
                               "updated": [{"id": r.id, "name": spec["name"], "method": spec["method"], "path": spec["path"]}]},
                    "breaking_changes": [c for c in changes if c["breaking"]], "changes": changes}
        r.name, r.method, r.path, r.path_shape = spec["name"], spec["method"], spec["path"], path_shape(spec["path"])
        r.auth, r.description = spec["auth"], spec["description"]
        r.request_schema, r.response_schema = spec["request_schema"], spec["response_schema"]
        r.response_mode, r.validate_request = spec["response_mode"], bool(spec["validate_request"])
        r.timeout_s = int(spec["timeout_s"] or 30)
        h = spec["handler"]
        r.handler_type, r.handler_workflow_id = h["type"], h.get("workflow_id")
        r.handler_module_id, r.handler_function = h.get("module_id"), h.get("function")
        r.updated_at = datetime.now()
        await self._s.commit()
        rev, sh, changes = await self._record_revision(project)
        return {"route": await self.to_dict(project, r), "spec_revision": rev, "spec_hash": sh,
                "breaking_changes": [c for c in changes if c["breaking"]]}

    async def delete(self, project: ProjectModel, rid: str, dry_run: bool = False, allow_code: bool = False) -> dict[str, Any]:
        r = await self.get(project, rid)
        if r.source == "code" and not allow_code:
            raise ProjectError(409, "route_managed_by_code", "remove the decorator from the module to delete this route")
        if dry_run:
            others = [route_spec(o) for o in await self.rows(project.id) if o.id != r.id]
            changes = await self._preview(project, others)
            return {"spec_revision": None, "spec_hash": None, "dry_run": True, "changes": changes,
                    "routes": {"created": [], "updated": [],
                               "removed": [{"id": r.id, "name": r.name, "method": r.method, "path": r.path}]},
                    "breaking_changes": [c for c in changes if c["breaking"]]}
        await self._s.delete(r)
        await self._s.commit()
        rev, h, changes = await self._record_revision(project)
        return {"spec_revision": rev, "spec_hash": h, "breaking_changes": [c for c in changes if c["breaking"]]}

    async def revisions(self, project: ProjectModel) -> list[dict[str, Any]]:
        res = await self._s.execute(select(ApiSpecRevisionModel).where(ApiSpecRevisionModel.project_id == project.id)
                                    .order_by(ApiSpecRevisionModel.revision))
        return [{"revision": r.revision, "spec_hash": r.spec_hash, "created_at": r.created_at.isoformat(),
                 "changes": r.changes} for r in res.scalars().all()]

    async def revision_spec(self, project: ProjectModel, revision: int) -> dict[str, Any]:
        res = await self._s.execute(select(ApiSpecRevisionModel).where(
            ApiSpecRevisionModel.project_id == project.id, ApiSpecRevisionModel.revision == revision))
        r = res.scalars().first()
        if not r:
            raise ProjectError(404, "revision_not_found", f"spec revision {revision} not found")
        return r.spec

    async def stub_count(self, project_id: str) -> int:
        return (await self._s.execute(select(func.count()).select_from(ApiRouteModel).where(
            ApiRouteModel.project_id == project_id, ApiRouteModel.handler_type == "stub"))).scalar_one()

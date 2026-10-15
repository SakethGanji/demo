"""Releases: snapshot → checks → activate; promote; rollback; diff (BACKEND-DESIGN §3.9, G10–G14, G28).

A release pins app versions, workflow versions (route handlers), python module sources, the
route set + OpenAPI spec, and fingerprints of the secrets it references. In a ``kind:"release"``
environment with an active release the router serves the snapshot; promoting/rolling back into
the project's live environment re-publishes the pinned app versions at /a/{slug}.
"""

from __future__ import annotations

import ast
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AppModel, AppVersionModel, CodeModuleModel, ProjectModel, ReleaseModel, WorkflowModel
from ..repositories.version_repository import VersionRepository
from ..utils.ids import generate_id
from .api_route_service import ApiRouteService, route_spec
from .project_service import ProjectError, env_names
from .secret_service import VAR_REF_RE, _flatten, fingerprint
from .spec_tools import build_openapi, diff_specs, spec_hash


def release_to_dict(r: ReleaseModel, with_snapshot: bool = False) -> dict[str, Any]:
    out = {"id": r.id, "project_id": r.project_id, "environment": r.environment, "number": r.number,
           "is_active": r.is_active, "status": r.status, "checks": r.checks, "source_release_id": r.source_release_id,
           "notes": r.notes, "created_by": r.created_by, "created_at": r.created_at.isoformat()}
    if with_snapshot:
        out["snapshot"] = r.snapshot
    return out


class ReleaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── queries ──────────────────────────────────────────────────────────

    async def active_release(self, project_id: str, env: str) -> ReleaseModel | None:
        return (await self._s.execute(select(ReleaseModel).where(
            ReleaseModel.project_id == project_id, ReleaseModel.environment == env, ReleaseModel.is_active.is_(True)))
        ).scalars().first()

    async def list(self, project: ProjectModel, environment: str | None = None) -> list[dict[str, Any]]:
        stmt = select(ReleaseModel).where(ReleaseModel.project_id == project.id)
        if environment:
            stmt = stmt.where(ReleaseModel.environment == environment)
        rows = (await self._s.execute(stmt.order_by(ReleaseModel.environment, ReleaseModel.number.desc()))).scalars().all()
        return [release_to_dict(r) for r in rows]

    async def get(self, project: ProjectModel, rid: str) -> ReleaseModel:
        r = await self._s.get(ReleaseModel, rid)
        if not r or r.project_id != project.id:
            raise ProjectError(404, "release_not_found", "Release not found")
        return r

    # ── snapshot ─────────────────────────────────────────────────────────

    async def _pin_workflow(self, wid: str, cache: dict[str, int], dry: bool = False) -> int | None:
        if wid in cache:
            return cache[wid]
        wf = await self._s.get(WorkflowModel, wid)
        if wf is None:
            return None
        repo = VersionRepository(self._s)
        latest = await repo.get_latest(wid)
        if latest is not None and latest.definition == wf.draft_definition:
            cache[wid] = latest.id
        elif dry:
            cache[wid] = -1  # dry run: a new version would be created
        else:
            cache[wid] = (await repo.create_version(wid, wf.draft_definition, message="release")).id
        return cache[wid]

    async def _bindings(self, project: ProjectModel, snapshot: dict[str, Any], env: str) -> list[dict[str, Any]]:
        from ..repositories.variable_repository import VariableRepository
        from .variable_crypto import decrypt_secret

        rows = await VariableRepository(self._s).load_runtime_rows(project.team_id, env, project.id)
        out = []
        for key in sorted(snapshot.get("referenced_vars") or []):
            v = rows.get(key)
            if v is None:
                out.append({"key": key, "scope": None, "variable_id": None, "fingerprint": None, "present": False})
                continue
            value = decrypt_secret(v.value) if v.type == "secret" else v.value
            out.append({"key": key, "scope": "project" if v.project_id else "team", "variable_id": v.id,
                        "type": v.type, "fingerprint": fingerprint(value), "present": True})
        return out

    async def build_snapshot(self, project: ProjectModel, dry: bool = False) -> dict[str, Any]:
        routes = [route_spec(r) for r in await ApiRouteService(self._s).rows(project.id)]
        pins: dict[str, int] = {}
        for r in routes:
            h = r["handler"]
            if h.get("type") == "workflow":
                h["version_id"] = await self._pin_workflow(h["workflow_id"], pins, dry)
        mods = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
        modules = [{"module_id": m.id, "name": m.name, "source_sha": m.source_sha, "source": m.source} for m in mods]
        apps = (await self._s.execute(select(AppModel).where(AppModel.project_id == project.id))).scalars().all()
        app_pins = []
        for a in apps:
            if a.current_version_id:
                app_pins.append({"app_id": a.id, "version_id": a.current_version_id})
            for wid in a.workflow_ids or []:
                await self._pin_workflow(wid, pins, dry)
        await self._s.flush()
        refs: set[str] = set()
        for wid, vid in pins.items():
            from ..db.models import WorkflowVersionModel
            if vid == -1:
                wfm = await self._s.get(WorkflowModel, wid)
                nodes = (wfm.draft_definition or {}).get("nodes", []) if wfm else []
            else:
                v = await self._s.get(WorkflowVersionModel, vid)
                nodes = (v.definition or {}).get("nodes", []) if v else []
            for node in nodes:
                refs.update(VAR_REF_RE.findall(_flatten(node.get("parameters", {}))))
        import re as _re
        sec_call = _re.compile(r"""studio\.secret\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\)""")
        for m in modules:
            refs.update(VAR_REF_RE.findall(m["source"]))
            refs.update(sec_call.findall(m["source"]))
        spec = build_openapi(project, routes)
        return {"apps": app_pins, "workflows": [{"workflow_id": w, "version_id": v} for w, v in sorted(pins.items())],
                "modules": modules, "routes": routes, "spec": spec, "spec_hash": spec_hash(spec),
                "referenced_vars": sorted(refs), "secret_bindings": []}

    # ── checks ───────────────────────────────────────────────────────────

    async def run_checks(self, project: ProjectModel, snapshot: dict[str, Any], env: str) -> list[dict[str, Any]]:
        from .api_usage import extract_api_calls
        from .route_dispatcher import match_route
        from ..db.models import AppFileModel, WorkflowVersionModel

        checks: list[dict[str, Any]] = []
        routes = snapshot.get("routes") or []
        by_name = {r["name"]: r for r in routes}
        # 1. app_calls_resolve
        missing = []
        for pin in snapshot.get("apps") or []:
            files = (await self._s.execute(select(AppFileModel).where(AppFileModel.version_id == pin["version_id"]))).scalars().all()
            if not files:
                v = await self._s.get(AppVersionModel, pin["version_id"])
                files_iter = [("App.tsx", v.source_code if v else "")]
            else:
                files_iter = [(f.path, f.content) for f in files if f.path != "api/client.ts"]
            for path, content in files_iter:
                if not path.endswith((".tsx", ".ts", ".jsx", ".js")):
                    continue
                for c in extract_api_calls(content or "", project.slug):
                    ok = (c["operation"] in by_name) if c["kind"] == "client_call" else \
                        match_route(routes, c["method"], c["path"].replace("{_}", "_"))[0] is not None
                    if not ok:
                        missing.append({"app_id": pin["app_id"], "file": path, "line": c["line"],
                                        "operation": c["operation"], "method": c["method"], "path": c["path"]})
        checks.append({"id": "app_calls_resolve", "status": "fail" if missing else "pass",
                       "message": f"{len(missing)} app call(s) have no matching route" if missing else "all app calls resolve",
                       "details": {"missing": missing}})
        # 2. handlers_exist
        bad = []
        mod_names = {m["module_id"]: m for m in snapshot.get("modules") or []}
        for r in routes:
            h = r["handler"]
            if h.get("type") == "workflow":
                vid = h.get("version_id")
                v = True if vid == -1 else (await self._s.get(WorkflowVersionModel, vid) if vid else None)
                if v is None:
                    bad.append(f"{r['method']} {r['path']}")
            elif h.get("type") == "python":
                m = mod_names.get(h.get("module_id"))
                if not m or f"def {h.get('function')}(" not in m["source"]:
                    bad.append(f"{r['method']} {r['path']}")
        checks.append({"id": "handlers_exist", "status": "fail" if bad else "pass", "details": {"routes": bad}})
        # 3. secrets_present (every $vars / studio.secret reference, any type)
        bindings = await self._bindings(project, snapshot, env)
        snapshot["secret_bindings"] = [b for b in bindings if b["present"]]
        absent = [b["key"] for b in bindings if not b["present"]]
        checks.append({"id": "secrets_present", "status": "fail" if absent else "pass",
                       "message": f"missing in '{env}': {', '.join(absent)}" if absent else "all referenced variables exist",
                       "details": {"missing": absent, "environment": env}})
        # 4. modules_parse
        broken = []
        for m in snapshot.get("modules") or []:
            try:
                ast.parse(m["source"])
            except SyntaxError as e:
                broken.append({"module": m["name"], "line": e.lineno, "message": e.msg})
        checks.append({"id": "modules_parse", "status": "fail" if broken else "pass", "details": {"errors": broken}})
        # 5. stub_routes (warn)
        stubs = [f"{r['method']} {r['path']}" for r in routes if r["handler"].get("type") == "stub"]
        checks.append({"id": "stub_routes", "status": "warn" if stubs else "pass", "details": {"routes": stubs}})
        # 6. breaking_vs_active (warn, G28)
        active = await self.active_release(project.id, env)
        changes = diff_specs(active.snapshot.get("spec"), snapshot.get("spec")) if active else []
        breaking = [c for c in changes if c["breaking"]]
        checks.append({"id": "breaking_vs_active", "status": "warn" if breaking else "pass",
                       "details": {"against": active.id if active else None, "changes": breaking}})
        return checks

    # ── writes ───────────────────────────────────────────────────────────

    async def _next_number(self, project_id: str, env: str) -> int:
        n = (await self._s.execute(select(func.max(ReleaseModel.number)).where(
            ReleaseModel.project_id == project_id, ReleaseModel.environment == env))).scalar_one()
        return (n or 0) + 1

    async def _activate(self, project: ProjectModel, rel: ReleaseModel, previous_status: str = "superseded") -> None:
        prev = await self.active_release(project.id, rel.environment)
        if prev is not None and prev.id != rel.id:
            prev.is_active = False
            prev.status = previous_status
            await self._s.flush()
        rel.is_active = True
        rel.status = "active"
        await self._s.flush()
        if rel.environment == project.live_environment:
            await self._publish_apps(project, rel)

    async def _publish_apps(self, project: ProjectModel, rel: ReleaseModel) -> None:
        from .app_service import AppService
        from .bundle_storage import PostgresBundleStorage
        from .client_codegen import generate_client
        from .file_storage import PostgresFileStorage

        svc = AppService(self._s, PostgresFileStorage(self._s), PostgresBundleStorage(self._s))
        routes = rel.snapshot.get("routes") or []
        client = generate_client(project.slug, routes, None, rel.snapshot.get("spec_hash") or "") if routes else None
        for pin in rel.snapshot.get("apps") or []:
            try:
                await svc.publish_release_version(pin["app_id"], pin["version_id"], client)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("release re-publish failed for %s", pin["app_id"], exc_info=True)

    def _check_env(self, project: ProjectModel, env: str) -> None:
        if env not in env_names(project):
            raise ProjectError(422, "unknown_environment", f"environment '{env}' is not in this project", environment=env)

    async def _store(self, project: ProjectModel, env: str, snapshot: dict, checks: list, notes: str | None,
                     actor: str | None, source_id: str | None) -> ReleaseModel:
        failed = any(c["status"] == "fail" for c in checks)
        rel = ReleaseModel(id=generate_id("rel"), project_id=project.id, environment=env,
                           number=await self._next_number(project.id, env), is_active=False,
                           status="blocked" if failed else "superseded", snapshot=snapshot, checks=checks,
                           source_release_id=source_id, notes=notes, created_by=actor, created_at=datetime.now())
        self._s.add(rel)
        await self._s.flush()
        if failed:
            await self._s.commit()
            raise ProjectError(422, "checks_failed", "release checks failed", checks=checks,
                               release_id=rel.id, number=rel.number)
        await self._activate(project, rel)
        await self._s.commit()
        return rel

    async def create(self, project: ProjectModel, env: str | None, notes: str | None = None, dry_run: bool = False,
                     actor: str | None = None) -> dict[str, Any]:
        env = env or project.default_environment
        self._check_env(project, env)
        snapshot = await self.build_snapshot(project, dry=dry_run)
        checks = await self.run_checks(project, snapshot, env)
        if dry_run:
            active = await self.active_release(project.id, env)
            return {"checks": checks, "diff": self._diff(active.snapshot if active else {}, snapshot)}
        return release_to_dict(await self._store(project, env, snapshot, checks, notes, actor, None), with_snapshot=False)

    async def promote(self, project: ProjectModel, rid: str, to_env: str, dry_run: bool = False,
                      actor: str | None = None) -> dict[str, Any]:
        src = await self.get(project, rid)
        self._check_env(project, to_env)
        if src.status == "blocked":
            raise ProjectError(409, "release_blocked", "a blocked release cannot be promoted")
        snapshot = {k: v for k, v in src.snapshot.items()}
        checks = await self.run_checks(project, snapshot, to_env)
        if dry_run:
            active = await self.active_release(project.id, to_env)
            return {"checks": checks, "diff": self._diff(active.snapshot if active else {}, snapshot)}
        return release_to_dict(await self._store(project, to_env, snapshot, checks, src.notes, actor, src.id))

    async def rollback(self, project: ProjectModel, env: str, release_id: str | None = None) -> dict[str, Any]:
        self._check_env(project, env)
        current = await self.active_release(project.id, env)
        if release_id:
            target = await self.get(project, release_id)
            if target.environment != env:
                raise ProjectError(422, "wrong_environment", f"release {release_id} belongs to '{target.environment}'")
            if target.status == "blocked":
                raise ProjectError(409, "release_blocked", "a blocked release cannot be activated")
        else:
            q = select(ReleaseModel).where(ReleaseModel.project_id == project.id, ReleaseModel.environment == env,
                                           ReleaseModel.status.in_(["superseded", "rolled_back"]))
            if current is not None:
                q = q.where(ReleaseModel.number < current.number)
            target = (await self._s.execute(q.order_by(ReleaseModel.number.desc()).limit(1))).scalars().first()
            if target is None:
                raise ProjectError(409, "no_previous_release", f"no previous release in '{env}' to roll back to")
        snap = dict(target.snapshot)
        checks = await self.run_checks(project, snap, env)
        for c in checks:  # rollback is never blocked: failures are reported as warnings
            if c["status"] == "fail":
                c["status"] = "warn"
        target.checks = checks
        if current is None or current.id != target.id:
            await self._activate(project, target, previous_status="rolled_back")
        await self._s.commit()
        return release_to_dict(target)

    # ── diff ─────────────────────────────────────────────────────────────

    @staticmethod
    def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        def idx(items, key):
            return {i[key]: i for i in items or []}
        oa, na = idx(old.get("apps"), "app_id"), idx(new.get("apps"), "app_id")
        ow, nw = idx(old.get("workflows"), "workflow_id"), idx(new.get("workflows"), "workflow_id")
        os_, ns = idx(old.get("secret_bindings"), "key"), idx(new.get("secret_bindings"), "key")
        apps = [{"app_id": k, "from_version": (oa.get(k) or {}).get("version_id"), "to_version": (na.get(k) or {}).get("version_id")}
                for k in sorted(set(oa) | set(na)) if (oa.get(k) or {}).get("version_id") != (na.get(k) or {}).get("version_id")]
        wfs = [{"workflow_id": k, "from_version": (ow.get(k) or {}).get("version_id"), "to_version": (nw.get(k) or {}).get("version_id")}
               for k in sorted(set(ow) | set(nw))]
        secrets = []
        for k in sorted(set(os_) | set(ns)):
            if k not in os_:
                secrets.append({"key": k, "change": "added"})
            elif k not in ns:
                secrets.append({"key": k, "change": "removed"})
            elif os_[k].get("fingerprint") != ns[k].get("fingerprint"):
                secrets.append({"key": k, "change": "value_changed"})
        return {"routes": diff_specs(old.get("spec"), new.get("spec")), "apps": apps, "workflows": wfs, "secrets": secrets}

    async def diff(self, project: ProjectModel, rid: str, against: str = "active", environment: str | None = None) -> dict[str, Any]:
        rel = await self.get(project, rid)
        if against == "active":
            other = await self.active_release(project.id, environment or rel.environment)
        else:
            other = await self.get(project, against)
        base = other.snapshot if other is not None else {}
        return {"release_id": rel.id, "against": other.id if other is not None else None, **self._diff(base, rel.snapshot)}

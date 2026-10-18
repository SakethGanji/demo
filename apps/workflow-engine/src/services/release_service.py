"""Releases: snapshot → checks → activate; promote; rollback; diff (BACKEND-DESIGN §3.9, G10–G14, G28).

A release pins app versions, workflow versions (route handlers), python module sources, the
route set + OpenAPI spec, and fingerprints of the secrets it references. In a ``kind:"release"``
environment with an active release the router serves the snapshot; promoting/rolling back into
the project's live environment re-publishes the pinned app versions at /a/{slug}.
"""

from __future__ import annotations

import ast
import copy
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AppModel, AppVersionModel, CodeModuleModel, ProjectModel, ReleaseModel, WorkflowModel
from ..repositories.version_repository import VersionRepository
from ..utils.ids import generate_id
from .api_route_service import ApiRouteService, route_spec
from .project_service import ProjectError, env_kind, env_names
from .secret_service import VAR_REF_RE, _flatten, fingerprint
from .spec_tools import build_openapi, diff_specs, spec_hash


def release_to_dict(r: ReleaseModel, with_snapshot: bool = False, project: ProjectModel | None = None) -> dict[str, Any]:
    # Review D10: `serving` = this release is what the router serves (an active release of a release env).
    serving = bool(r.is_active) and (env_kind(project, r.environment) == "release" if project is not None else True)
    out = {"id": r.id, "project_id": r.project_id, "environment": r.environment, "number": r.number,
           "is_active": r.is_active, "status": r.status, "serving": serving, "checks": r.checks,
           "source_release_id": r.source_release_id,
           # Stage 8 (4.5): False = cut before sub-workflow pinning existed; its sub-workflow calls resolve live and warn
           "pins_subworkflows": isinstance(r.snapshot, dict) and "subworkflows" in r.snapshot,
           "notes": r.notes, "created_by": r.created_by, "created_at": r.created_at.isoformat()}
    if with_snapshot:
        out["snapshot"] = r.snapshot
    return out


logger = logging.getLogger(__name__)

#: checker findings that mean a sub-workflow call is wrong (WF006 missing target, WF020-WF029 the Stage 8 rules)
_SUBWORKFLOW_CODES = frozenset({"WF006", *(f"WF02{i}" for i in range(10))})


def pins_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, int] | None:
    """``release_pins`` for a run in a release (DESIGN-STAGE8-REUSE 4.4): ``{"<parent_version_id>:<node>": child_version_id}``.

    None when the snapshot predates sub-workflow pinning (no ``subworkflows`` key): the run then resolves live and warns."""
    if not isinstance(snapshot, dict) or "subworkflows" not in snapshot:
        return None
    return {f"{e['parent_version_id']}:{e.get('pin_node') or e['node']}": int(e["child_version_id"])
            for e in snapshot.get("subworkflows") or []
            if e.get("child_version_id") not in (None, -1) and not e.get("cycle")}


class ReleaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── queries ──────────────────────────────────────────────────────────

    async def active_release(self, project_id: str, env: str) -> ReleaseModel | None:
        return (await self._s.execute(select(ReleaseModel).where(
            ReleaseModel.project_id == project_id, ReleaseModel.environment == env, ReleaseModel.is_active.is_(True)))
        ).scalars().first()

    async def baseline(self, project_id: str, env: str, before_number: int | None = None) -> ReleaseModel | None:
        """What a new release of ``env`` is compared with: the active release, or — in a draft
        environment, which never has one (review D10) — the latest non-blocked snapshot."""
        active = await self.active_release(project_id, env)
        if active is not None:
            return active
        q = select(ReleaseModel).where(
            ReleaseModel.project_id == project_id, ReleaseModel.environment == env, ReleaseModel.status != "blocked")
        if before_number is not None:
            q = q.where(ReleaseModel.number < before_number)
        return (await self._s.execute(q.order_by(ReleaseModel.number.desc()).limit(1))).scalars().first()

    async def list(self, project: ProjectModel, environment: str | None = None) -> list[dict[str, Any]]:
        stmt = select(ReleaseModel).where(ReleaseModel.project_id == project.id)
        if environment:
            stmt = stmt.where(ReleaseModel.environment == environment)
        rows = (await self._s.execute(stmt.order_by(ReleaseModel.environment, ReleaseModel.number.desc()))).scalars().all()
        return [release_to_dict(r, project=project) for r in rows]

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
        via: dict[str, str] = {}
        for r in routes:
            h = r["handler"]
            if h.get("type") == "workflow":
                h["version_id"] = await self._pin_workflow(h["workflow_id"], pins, dry)
                via.setdefault(h["workflow_id"], "route")
        mods = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
        modules = [{"module_id": m.id, "name": m.name, "source_sha": m.source_sha, "source": m.source} for m in mods]
        apps = (await self._s.execute(select(AppModel).where(AppModel.project_id == project.id))).scalars().all()
        app_pins, unversioned = [], []
        for a in apps:
            if a.current_version_id:
                app_pins.append({"app_id": a.id, "version_id": a.current_version_id})
            elif a.draft_source_code:
                # §3.9 / review D10: an app with only a draft gets a version (trigger "release") so it can be pinned.
                app_pins.append({"app_id": a.id, "version_id": -1 if dry else await self._version_app(a)})
            else:
                unversioned.append(a.id)  # nothing to snapshot → app_calls_resolve fails
            for wid in a.workflow_ids or []:
                await self._pin_workflow(wid, pins, dry)
                via.setdefault(wid, "app")
        await self._s.flush()
        # Stage 8 (4.4): follow sub-workflow calls from every pinned root and pin the children they reach.
        roots = dict(pins)
        edges = await self._closure(project, roots, pins, dry)
        workflows = [{"workflow_id": w, "version_id": v, "via": via.get(w, "route")} for w, v in sorted(roots.items())]
        seen_children = {(w, v) for w, v in roots.items()}
        for e in edges:
            cv = e.get("child_version_id")
            if cv is None or (e["child_workflow_id"], cv) in seen_children:
                continue
            seen_children.add((e["child_workflow_id"], cv))
            workflows.append({"workflow_id": e["child_workflow_id"], "version_id": cv, "via": "subworkflow"})
        refs: set[str] = set()
        for entry in workflows:
            wid, vid = entry["workflow_id"], entry["version_id"]
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
        return {"apps": app_pins, "unversioned_apps": unversioned,
                "workflows": workflows, "subworkflows": edges,
                "modules": modules, "routes": routes, "spec": spec, "spec_hash": spec_hash(spec),
                "referenced_vars": sorted(refs), "secret_bindings": []}

    # ── sub-workflow closure (DESIGN-STAGE8-REUSE 4.4) ────────────────────

    async def _definition_of(self, wid: str, vid: int | None) -> dict[str, Any] | None:
        """The definition a snapshot pin stands for; ``-1`` / None = the draft (a dry run's would-be version)."""
        from ..db.models import WorkflowVersionModel

        if vid in (None, -1):
            wf = await self._s.get(WorkflowModel, wid)
            return wf.draft_definition if wf else None
        v = await self._s.get(WorkflowVersionModel, vid)
        return v.definition if v else None

    async def _closure(self, project: ProjectModel, roots: dict[str, int], pins: dict[str, int], dry: bool) -> list[dict[str, Any]]:
        """Every sub-workflow call reachable from the pinned roots, as edges
        ``{parent_workflow_id, parent_version_id, node, child_workflow_id, child_version_id, child_version_number, spec}``.

        ``spec`` is what the node asked for (``draft`` | ``latest`` | ``<n>``); the edge records what it resolves to *now*,
        so a run in a release environment never reads the live publish pointer again. A call that cannot be resolved keeps
        ``child_version_id: None`` and an ``error`` (the release check blocks on it); a call back into the path to it is
        marked ``cycle: true`` and is not followed. Two parents may pin different versions of one child: edges are keyed
        per (parent version, node). Disabled nodes are not followed (they never run)."""
        from ..db.models import WorkflowVersionModel
        from .subworkflow_service import version_id_for
        from .workflow_io import call_refs

        edges: list[dict[str, Any]] = []
        visited: set[tuple[str, int]] = set()

        async def number_of(vid: int | None) -> int | None:
            if vid in (None, -1):
                return None
            v = await self._s.get(WorkflowVersionModel, vid)
            return v.version_number if v else None

        async def visit(wid: str, vid: int, path: tuple[str, ...]) -> None:
            if (wid, vid) in visited:
                return
            visited.add((wid, vid))
            definition = await self._definition_of(wid, vid)
            for ref in call_refs(definition):
                if ref.disabled:
                    continue
                edge: dict[str, Any] = {
                    "parent_workflow_id": wid, "parent_version_id": vid, "node": ref.node, "child_workflow_id": ref.workflow_id,
                    "child_version_id": None, "child_version_number": None, "spec": ref.spec,
                }
                if ref.node_type == "WorkflowTool":
                    edge["pin_node"] = ref.pin_node or "WorkflowTool"  # the agent tool calls the invoker under this per-tool name
                child = await self._s.get(WorkflowModel, ref.workflow_id)
                if child is None:
                    edge["error"] = "the sub-workflow no longer exists"
                elif child.project_id != project.id:
                    edge["error"] = "the sub-workflow is in another project"
                else:
                    cvid: int | None
                    if ref.spec == "draft":
                        cvid = await self._pin_workflow(ref.workflow_id, pins, dry)
                    elif ref.spec == "latest":
                        cvid = child.published_version_id
                        if cvid is None:
                            edge["error"] = f"'{child.name}' has never been published"
                    else:
                        cvid = await version_id_for(self._s, ref.workflow_id, int(ref.spec))
                        if cvid is None:
                            edge["error"] = f"'{child.name}' has no version {ref.spec}"
                    edge["child_version_id"] = cvid
                    edge["child_version_number"] = await number_of(cvid)
                    if ref.workflow_id == wid or ref.workflow_id in path:
                        edge["cycle"] = True
                    elif cvid is not None:
                        edges.append(edge)
                        await visit(ref.workflow_id, cvid, (*path, wid))
                        continue
                edges.append(edge)

        for wid, vid in sorted(roots.items()):
            await visit(wid, vid, ())
        return edges

    async def _version_app(self, app: AppModel) -> int:
        from .app_service import AppService
        from .bundle_storage import PostgresBundleStorage
        from .file_storage import PostgresFileStorage

        svc = AppService(self._s, PostgresFileStorage(self._s), PostgresBundleStorage(self._s))
        v = await svc._create_version_row(app, source_code=app.draft_source_code or "", trigger="release",
                                          message="created for a release",
                                          files=[{"path": "App.tsx", "content": app.draft_source_code or ""}])
        await self._s.flush()
        return v.id

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
            if pin["version_id"] == -1:  # dry run: the version would be created from the draft
                a = await self._s.get(AppModel, pin["app_id"])
                files_iter = [("App.tsx", (a.draft_source_code if a else "") or "")]
            else:
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
        unversioned = list(snapshot.get("unversioned_apps") or [])
        msg = f"{len(missing)} app call(s) have no matching route" if missing else "all app calls resolve"
        if unversioned:
            msg = (msg + "; " if missing else "") + f"{len(unversioned)} app(s) have no saved code — Save the app once before releasing"
        checks.append({"id": "app_calls_resolve", "status": "fail" if (missing or unversioned) else "pass",
                       "message": msg, "details": {"missing": missing, "unversioned": unversioned}})
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
        active = await self.baseline(project.id, env)
        changes = diff_specs(active.snapshot.get("spec"), snapshot.get("spec")) if active else []
        breaking = [c for c in changes if c["breaking"]]
        checks.append({"id": "breaking_vs_active", "status": "warn" if breaking else "pass",
                       "details": {"against": active.id if active else None, "changes": breaking}})
        # 7. workflow_tests_pass: one per workflow in the snapshot; an approved test that fails blocks the release
        checks.extend(await self._workflow_test_checks(snapshot))
        # 7b. Stage 8: every sub-workflow call resolves, and each parent still fits the child version it pinned
        checks.extend(await self._subworkflow_checks(project, snapshot))
        # 8. smoke_tests_pass: workflows that opted in run their live smoke tests; a failure or an unreachable system blocks
        checks.extend(await self._smoke_checks(snapshot))
        return checks

    async def _workflow_test_checks(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        from ..db.models import WorkflowVersionModel
        from ..repositories.workflow_repository import WorkflowRepository
        from .scorecard import failing_approved_tests

        out: list[dict[str, Any]] = []
        repo = WorkflowRepository(self._s)
        for pin in snapshot.get("workflows") or []:
            wid, vid = pin["workflow_id"], pin.get("version_id")
            stored = await repo.get(wid)
            if stored is None:
                continue
            definition = None  # -1 means a dry run: the draft would be the version
            if vid not in (None, -1):
                v = await self._s.get(WorkflowVersionModel, vid)
                definition = v.definition if v else None
            failed = await failing_approved_tests(repo, stored, definition)
            out.append({
                "id": "workflow_tests_pass", "status": "fail" if failed else "pass",
                "message": (f"{stored.name}: {len(failed)} approved test(s) fail" if failed
                            else f"{stored.name}: approved tests pass"),
                "details": {"workflow_id": wid, "name": stored.name, "version_id": vid, "failed_tests": failed},
            })
        return out

    async def _subworkflow_checks(self, project: ProjectModel, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """``subworkflows_resolved`` and ``subworkflow_contracts_compatible`` (DESIGN-STAGE8-REUSE 4.4).

        A snapshot without a ``subworkflows`` key predates Stage 8: nothing to check (promoting or rolling back an old
        release keeps working; its calls resolve live and the run warns, section 4.5). A release is *blocked* when a child
        cannot be resolved, sits on a cycle, holds an Approval, or no longer fits the parent that pins it."""
        from .check_lookups import build_lookups
        from .subworkflow_service import version_number_of
        from .workflow_checker import check_workflow
        from .workflow_io import trigger_node

        if "subworkflows" not in snapshot:
            return []
        edges = list(snapshot.get("subworkflows") or [])
        names: dict[str, str] = {}

        async def name_of(wid: str) -> str:
            if wid not in names:
                wf = await self._s.get(WorkflowModel, wid)
                names[wid] = wf.name if wf else wid
            return names[wid]

        problems: list[dict[str, Any]] = []
        for e in edges:
            base = {"parent_workflow_id": e["parent_workflow_id"], "parent_name": await name_of(e["parent_workflow_id"]),
                    "node": e["node"], "child_workflow_id": e["child_workflow_id"], "spec": e.get("spec")}
            if e.get("cycle"):
                problems.append({**base, "reason": "cycle", "message": f"'{await name_of(e['parent_workflow_id'])}' and "
                                 f"'{await name_of(e['child_workflow_id'])}' call each other"})
                continue
            if e.get("child_version_id") is None:
                problems.append({**base, "reason": "unresolved", "message": e.get("error") or "the sub-workflow cannot be resolved"})
                continue
            child_def = await self._definition_of(e["child_workflow_id"], e["child_version_id"])
            if child_def is None:
                problems.append({**base, "reason": "unresolved", "message": "the pinned version no longer exists"})
            elif trigger_node(child_def) is None:
                problems.append({**base, "reason": "not_callable",
                                 "message": f"'{await name_of(e['child_workflow_id'])}' has no Execute Workflow Trigger"})
            elif any(isinstance(n, dict) and n.get("type") == "Approval" for n in child_def.get("nodes") or []):
                problems.append({**base, "reason": "approval", "message":
                                 f"'{await name_of(e['child_workflow_id'])}' has an Approval step; approvals must be in the top-level workflow"})
        out = [{"id": "subworkflows_resolved", "status": "fail" if problems else "pass",
                "message": (f"{len(problems)} sub-workflow call(s) cannot run: {problems[0]['message']}" if problems
                            else (f"{len(edges)} sub-workflow call(s) pinned" if edges else "no sub-workflow calls")),
                "details": {"problems": problems, "edges": len(edges)}}]

        # parents against the child versions they pin: check each parent with its calls rewritten to the pinned numbers
        by_parent: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for e in edges:
            if e.get("child_version_id") is not None and not e.get("cycle"):
                by_parent.setdefault((e["parent_workflow_id"], e["parent_version_id"]), []).append(e)
        broken: list[dict[str, Any]] = []
        warned: list[dict[str, Any]] = []
        for (pid, pvid), group in sorted(by_parent.items()):
            definition = await self._definition_of(pid, pvid)
            parent = await self._s.get(WorkflowModel, pid)
            if definition is None or parent is None:
                continue
            pinned = copy.deepcopy(definition)
            nodes = {n.get("name"): n for n in pinned.get("nodes") or [] if isinstance(n, dict)}
            for e in group:
                node = nodes.get(e["node"])
                if node is None or node.get("type") != "ExecuteWorkflow":
                    continue
                number = e.get("child_version_number")
                if number is None and e["child_version_id"] not in (None, -1):
                    number = await version_number_of(self._s, e["child_version_id"])
                node.setdefault("parameters", {})["version"] = number if number is not None else "draft"
            try:
                lookups = await build_lookups(self._s, project.id, None, team_id=parent.team_id or "default",
                                              workflow_id=pid, definition=pinned)
                findings = check_workflow(pinned, lookups=lookups, workflow_id=pid)["findings"]
            except Exception:
                logger.warning("could not check %s against its pinned sub-workflows", pid, exc_info=True)
                continue
            call_nodes = {e["node"] for e in group}
            for f in findings:
                if f.get("node") not in call_nodes or not (f.get("code") in _SUBWORKFLOW_CODES):
                    continue
                row = {"workflow_id": pid, "workflow_name": parent.name, "node": f.get("node"), "code": f.get("code"),
                       "severity": f.get("severity"), "message": f.get("message")}
                if f.get("severity") == "error":
                    broken.append(row)
                elif f.get("severity") == "warning":
                    warned.append(row)
        status = "fail" if broken else ("warn" if warned else "pass")
        out.append({"id": "subworkflow_contracts_compatible", "status": status,
                    "message": (f"{broken[0]['workflow_name']}: {broken[0]['message']}" if broken
                                else (f"{warned[0]['workflow_name']}: {warned[0]['message']}" if warned
                                      else "every sub-workflow call fits the version it pins")),
                    "details": {"broken": broken, "warnings": warned}})
        return out

    async def _smoke_checks(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """``smoke_tests_pass``: one check per workflow that opted in (enabled and before_release)."""
        from ..db.models import SmokeConfigModel, WorkflowVersionModel
        from ..repositories.workflow_repository import WorkflowRepository
        from . import smoke

        out: list[dict[str, Any]] = []
        repo = WorkflowRepository(self._s)
        for pin in snapshot.get("workflows") or []:
            wid, vid = pin["workflow_id"], pin.get("version_id")
            cfg = await self._s.get(SmokeConfigModel, wid)
            if cfg is None or not (cfg.enabled and cfg.before_release):
                continue
            stored = await repo.get(wid)
            if stored is None:
                continue
            details: dict[str, Any] = {"workflow_id": wid, "name": stored.name, "version_id": vid}
            try:
                definition = None  # -1 means a dry run: the draft would be the version
                if vid not in (None, -1):
                    v = await self._s.get(WorkflowVersionModel, vid)
                    definition = v.definition if v else None
                run = await smoke.run_smoke(repo, stored, "release", definition=definition)
                details.update(run_id=run["id"], results=run["results"])
                status = "pass" if run["status"] == "passed" else "fail"
                msg = (f"{stored.name}: smoke tests pass" if status == "pass"
                       else f"{stored.name}: smoke run {run['status']}")
            except smoke.SmokeError as e:
                status, msg = "fail", f"{stored.name}: smoke is enabled but cannot run: {e.message}"
            except Exception as e:  # never turn an infrastructure problem into a 500 or a silent pass
                logger.warning("smoke check of %s failed", wid, exc_info=True)
                status, msg = "fail", f"{stored.name}: smoke run could not reach the system: {type(e).__name__}"
                details["error"] = str(e)[:300]
            out.append({"id": "smoke_tests_pass", "status": status, "message": msg, "details": details})
        return out

    # ── writes ───────────────────────────────────────────────────────────

    async def _next_number(self, project_id: str, env: str) -> int:
        n = (await self._s.execute(select(func.max(ReleaseModel.number)).where(
            ReleaseModel.project_id == project_id, ReleaseModel.environment == env))).scalar_one()
        return (n or 0) + 1

    async def _activate(self, project: ProjectModel, rel: ReleaseModel, previous_status: str = "superseded") -> None:
        if rel.environment == project.live_environment:
            await self._publish_apps(project, rel)
        prev = await self.active_release(project.id, rel.environment)
        if prev is not None and prev.id != rel.id:
            prev.is_active = False
            prev.status = previous_status
            await self._s.flush()
        rel.is_active = True
        rel.status = "active"
        await self._s.flush()

    async def _publish_apps(self, project: ProjectModel, rel: ReleaseModel) -> None:
        from .app_service import AppService, AppPublishError
        from .bundle_storage import PostgresBundleStorage
        from .client_codegen import generate_client
        from .file_storage import PostgresFileStorage

        svc = AppService(self._s, PostgresFileStorage(self._s), PostgresBundleStorage(self._s))
        routes = rel.snapshot.get("routes") or []
        client = generate_client(project.slug, routes, None, rel.snapshot.get("spec_hash") or "") if routes else None
        for pin in rel.snapshot.get("apps") or []:
            try:
                await svc.publish_release_version(pin["app_id"], pin["version_id"], client)
            except AppPublishError as exc:
                await self._s.rollback()
                raise ProjectError(422, "release_publish_failed", "Release activation failed; published apps were not changed",
                                   app_id=pin["app_id"]) from exc

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
        if env_kind(project, env) == "draft":
            # Review D10: a draft environment always serves the drafts — its releases are snapshots
            # (numbered, promotable), never "active".
            rel.status = "snapshot"
        else:
            await self._activate(project, rel)
        await self._s.commit()
        return rel

    async def create(self, project: ProjectModel, env: str | None, notes: str | None = None, dry_run: bool = False,
                     actor: str | None = None) -> dict[str, Any]:
        env = env or project.default_environment
        self._check_env(project, env)
        # Audit #7: check on a side-effect-free snapshot first; app/workflow versions are only created
        # once the checks pass, so a blocked release leaves apps and workflows untouched.
        snapshot = await self.build_snapshot(project, dry=True)
        checks = await self.run_checks(project, snapshot, env)
        if dry_run:
            active = await self.baseline(project.id, env)
            return {"checks": checks, "diff": self._diff(active.snapshot if active else {}, snapshot),
                    "snapshot_apps": snapshot.get("apps") or []}
        if not any(c["status"] == "fail" for c in checks):
            snapshot = await self.build_snapshot(project, dry=False)
            checks = await self.run_checks(project, snapshot, env)
        return release_to_dict(await self._store(project, env, snapshot, checks, notes, actor, None), with_snapshot=False,
                               project=project)

    async def promote(self, project: ProjectModel, rid: str, to_env: str, dry_run: bool = False,
                      actor: str | None = None) -> dict[str, Any]:
        src = await self.get(project, rid)
        self._check_env(project, to_env)
        if src.status == "blocked":
            raise ProjectError(409, "release_blocked", "a blocked release cannot be promoted")
        snapshot = {k: v for k, v in src.snapshot.items()}
        checks = await self.run_checks(project, snapshot, to_env)
        if dry_run:
            active = await self.baseline(project.id, to_env)
            return {"checks": checks, "diff": self._diff(active.snapshot if active else {}, snapshot),
                    "snapshot_apps": snapshot.get("apps") or []}
        return release_to_dict(await self._store(project, to_env, snapshot, checks, src.notes, actor, src.id), project=project)

    async def rollback(self, project: ProjectModel, env: str, release_id: str | None = None) -> dict[str, Any]:
        self._check_env(project, env)
        if env_kind(project, env) != "release":  # review D10: draft envs serve drafts, nothing to roll back
            raise ProjectError(422, "not_a_release_environment", f"'{env}' is a draft environment; it always serves the latest saved code",
                               environment=env)
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
        return release_to_dict(target, project=project)

    # ── diff ─────────────────────────────────────────────────────────────

    @staticmethod
    def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        def idx(items, key):
            return {i[key]: i for i in items or []}
        oa, na = idx(old.get("apps"), "app_id"), idx(new.get("apps"), "app_id")
        roots = lambda snap: [w for w in snap.get("workflows") or [] if w.get("via") != "subworkflow"]  # noqa: E731
        ow, nw = idx(roots(old), "workflow_id"), idx(roots(new), "workflow_id")
        os_, ns = idx(old.get("secret_bindings"), "key"), idx(new.get("secret_bindings"), "key")
        apps = [{"app_id": k, "from_version": (oa.get(k) or {}).get("version_id"), "to_version": (na.get(k) or {}).get("version_id")}
                for k in sorted(set(oa) | set(na)) if (oa.get(k) or {}).get("version_id") != (na.get(k) or {}).get("version_id")]
        # Review D10: unchanged workflows are not "changes".
        wfs = [{"workflow_id": k, "from_version": (ow.get(k) or {}).get("version_id"), "to_version": (nw.get(k) or {}).get("version_id")}
               for k in sorted(set(ow) | set(nw)) if (ow.get(k) or {}).get("version_id") != (nw.get(k) or {}).get("version_id")]
        secrets = []
        for k in sorted(set(os_) | set(ns)):
            if k not in os_:
                secrets.append({"key": k, "change": "added"})
            elif k not in ns:
                secrets.append({"key": k, "change": "removed"})
            elif os_[k].get("fingerprint") != ns[k].get("fingerprint"):
                secrets.append({"key": k, "change": "value_changed"})
        # Stage 8: a child whose pinned version changed, with the parents that call it
        def child_versions(snap):
            out: dict[str, dict[str, Any]] = {}
            for e in snap.get("subworkflows") or []:
                row = out.setdefault(e["child_workflow_id"], {"versions": set(), "parents": []})
                row["versions"].add(e.get("child_version_number") if e.get("child_version_number") is not None else e.get("child_version_id"))
                p = {"workflow_id": e["parent_workflow_id"], "node": e["node"]}
                if p not in row["parents"]:
                    row["parents"].append(p)
            return out
        oc, nc = child_versions(old), child_versions(new)
        subs = []
        for k in sorted(set(oc) | set(nc)):
            ov = sorted((oc.get(k) or {}).get("versions", ()), key=lambda x: (x is None, x))
            nv = sorted((nc.get(k) or {}).get("versions", ()), key=lambda x: (x is None, x))
            if ov != nv:
                subs.append({"workflow": k, "from_version": ov, "to_version": nv, "via": "subworkflow",
                             "parents": (nc.get(k) or oc.get(k))["parents"]})
        return {"routes": diff_specs(old.get("spec"), new.get("spec")), "apps": apps, "workflows": wfs, "secrets": secrets,
                "subworkflows": subs}

    async def diff(self, project: ProjectModel, rid: str, against: str = "active", environment: str | None = None) -> dict[str, Any]:
        rel = await self.get(project, rid)
        if against == "active":
            env = environment or rel.environment
            other = await self.baseline(project.id, env, before_number=rel.number if env == rel.environment else None)
            if other is not None and other.id == rel.id:
                other = None
        else:
            other = await self.get(project, against)
        base = other.snapshot if other is not None else {}
        return {"release_id": rel.id, "against": other.id if other is not None else None, **self._diff(base, rel.snapshot)}

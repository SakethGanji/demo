"""Install a template bundle into a project, and export a workflow as a bundle (DESIGN-STAGE8-REUSE section 8.3, 8.4).

``plan_install`` is pure: it decides every name (workflows, tables, APIs, interface slugs, webhook paths) from a
snapshot of the project, so the preview and the install can never disagree. ``TemplateInstaller`` then does the writes.

Atomicity. The services this uses (data tables, the workflow repository, ``WorkflowService.publish``) commit on their
own, so one SQL transaction around the whole install is not possible without rewriting them. The install is instead
all-or-nothing by compensation: everything it creates is recorded as it goes, and any failure deletes it again in
reverse order (workflows, tables, API specs, variables, the egress hosts it added) before the error is raised. The
``template_installs`` row is written last, so a project never has a row for an install that did not finish.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ApprovedTestsFailError, ConflictError, ValidationError, WorkflowHasErrorsError
from ..db.models import ActiveTriggerModel, ProjectModel, TemplateInstallModel, WorkflowModel, WorkflowVersionModel
from ..schemas.template import InstallRequest, TemplateBundle
from ..schemas.workflow import normalize_tests
from ..utils.ids import generate_id, workflow_id as new_workflow_id
from .project_service import ProjectError, env_names
from .template_bundle import (
    LoadedTemplate,
    TemplateBundleError,
    get_catalog,
    normalized_apis,
    replace_tokens,
    satisfies,
    token_uses,
    validate_bundle,
)

logger = logging.getLogger(__name__)


class TemplateError(Exception):
    """An error with an HTTP status and the ``{code, message, ...}`` envelope the routes raise as ``detail``."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def template_not_found(slug: str) -> TemplateError:
    return TemplateError(404, "TEMPLATE_NOT_FOUND", f"Template '{slug}' not found")


# ── planning (pure) ──────────────────────────────────────────────────────────────────────────


@dataclass
class ProjectSnapshot:
    """What the project already has, as far as names go."""

    workflow_names: set[str] = field(default_factory=set)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)   # name -> columns
    apis: dict[str, str] = field(default_factory=dict)                       # name -> content hash
    interface_slugs: set[str] = field(default_factory=set)
    webhook_paths: set[str] = field(default_factory=set)


@dataclass
class TablePlan:
    name: str
    reuse: bool = False


@dataclass
class ApiPlan:
    name: str
    reuse: bool = False


@dataclass
class InstallPlan:
    workflow_names: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)                    # workflow keys, sub-workflows before their callers
    tables: dict[str, TablePlan] = field(default_factory=dict)
    apis: dict[str, ApiPlan] = field(default_factory=dict)
    slugs: dict[str, str] = field(default_factory=dict)               # workflow key -> interface slug
    webhook_paths: dict[tuple[str, str], str] = field(default_factory=dict)   # (workflow key, node) -> path
    renamed: list[dict[str, str]] = field(default_factory=list)
    clashes: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflows": [{"key": k, "name": self.workflow_names[k]} for k in self.order],
            "tables": [{"key": k, "name": t.name, "reused": t.reuse} for k, t in self.tables.items()],
            "apis": [{"key": k, "name": a.name, "reused": a.reuse} for k, a in self.apis.items()],
            "interface_slugs": dict(self.slugs),
            "webhook_paths": {f"{k[0]}/{k[1]}": v for k, v in self.webhook_paths.items()},
            "renamed": list(self.renamed),
            "clashes": list(self.clashes),
        }


def _numbered(base: str, taken: set[str], fmt: str) -> str:
    """``base`` if free, else ``fmt`` with the first free number from 2 ("{} ({})", "{}_{}", "{}-{}")."""
    if base not in taken:
        return base
    n = 2
    while fmt.format(base, n) in taken:
        n += 1
    return fmt.format(base, n)


def columns_compatible(existing: list[dict[str, Any]], wanted: list[Any]) -> bool:
    """An existing table can stand in for the bundle's when it has every wanted column with the same type, and no
    required column the bundle's rows would not fill."""
    have = {c["name"]: c for c in existing}
    names = {c.name for c in wanted}
    for c in wanted:
        if c.name not in have or have[c.name].get("type") != c.type:
            return False
    return all(not (c.get("required") and "default" not in c and name not in names) for name, c in have.items())


def api_content_hash(spec: dict[str, Any]) -> str:
    blob = json.dumps({"base_url": spec.get("base_url") or "", "operations": spec.get("operations") or []},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def workflow_order(bundle: TemplateBundle) -> list[str]:
    """Workflow keys with callees before callers (the bundle is acyclic; validated)."""
    deps = {w.key: sorted({u.key for u in token_uses(w.definition) if u.kind == "workflow" and ":" not in u.key})
            for w in bundle.workflows}
    done: list[str] = []

    def visit(k: str) -> None:
        if k in done:
            return
        for d in deps.get(k, []):
            visit(d)
        done.append(k)

    for w in bundle.workflows:
        visit(w.key)
    return done


def plan_install(
    bundle: TemplateBundle, snapshot: ProjectSnapshot, *, clash: str = "rename", reuse_tables: list[str] | None = None,
    api_specs: dict[str, dict[str, Any]] | None = None,
) -> InstallPlan:
    """Decide every name. Pure. With ``clash="fail"`` nothing is renamed: the clashes are listed instead."""
    reuse_tables = list(reuse_tables or [])
    api_specs = api_specs or {}
    plan = InstallPlan(order=workflow_order(bundle))
    fail = clash == "fail"

    def clashed(kind: str, name: str) -> None:
        plan.clashes.append({"kind": kind, "name": name})

    taken = set(snapshot.workflow_names)
    for w in bundle.workflows:
        if w.name in taken:
            if fail:
                clashed("workflow", w.name)
                plan.workflow_names[w.key] = w.name
                continue
            new = _numbered(w.name, taken, "{} ({})")
            plan.renamed.append({"kind": "workflow", "from": w.name, "to": new})
        else:
            new = w.name
        plan.workflow_names[w.key] = new
        taken.add(new)

    taken_tables = set(snapshot.tables)
    for t in bundle.tables:
        existing = snapshot.tables.get(t.name)
        if existing is not None and t.key in reuse_tables and columns_compatible(existing, t.columns):
            plan.tables[t.key] = TablePlan(t.name, reuse=True)
            continue
        if existing is not None:
            if fail:
                clashed("table", t.name)
                plan.tables[t.key] = TablePlan(t.name)
                continue
            new = _numbered(t.name, taken_tables, "{}_{}")
            plan.renamed.append({"kind": "table", "from": t.name, "to": new})
        else:
            new = t.name
        plan.tables[t.key] = TablePlan(new)
        taken_tables.add(new)

    taken_apis = set(snapshot.apis)
    for a in bundle.apis:
        digest = api_content_hash(api_specs[a.key]) if a.key in api_specs else None
        if a.name in snapshot.apis and digest is not None and snapshot.apis[a.name] == digest:
            plan.apis[a.key] = ApiPlan(a.name, reuse=True)
            continue
        if a.name in taken_apis:
            if fail:
                clashed("api", a.name)
                plan.apis[a.key] = ApiPlan(a.name)
                continue
            new = _numbered(a.name, taken_apis, "{} ({})")
            plan.renamed.append({"kind": "api", "from": a.name, "to": new})
        else:
            new = a.name
        plan.apis[a.key] = ApiPlan(new)
        taken_apis.add(new)

    slugs = set(snapshot.interface_slugs)
    paths = set(snapshot.webhook_paths)
    for w in bundle.workflows:
        iface = w.definition.get("interface")
        if isinstance(iface, dict) and iface.get("slug"):
            slug = iface["slug"]
            if slug in slugs:
                if fail:
                    clashed("interface_slug", slug)
                    plan.slugs[w.key] = slug
                else:
                    new = _numbered(slug, slugs, "{}-{}")
                    plan.renamed.append({"kind": "interface_slug", "from": slug, "to": new})
                    plan.slugs[w.key] = new
                    slugs.add(new)
            else:
                plan.slugs[w.key] = slug
                slugs.add(slug)
        for n in w.definition.get("nodes") or []:
            if isinstance(n, dict) and n.get("type") == "Webhook":
                p = (n.get("parameters") or {}).get("path")
                if not p or not isinstance(p, str):
                    continue
                if p in paths:
                    if fail:
                        clashed("webhook_path", p)
                        plan.webhook_paths[(w.key, n["name"])] = p
                    else:
                        new = _numbered(p, paths, "{}-{}")
                        plan.renamed.append({"kind": "webhook_path", "from": p, "to": new})
                        plan.webhook_paths[(w.key, n["name"])] = new
                        paths.add(new)
                else:
                    plan.webhook_paths[(w.key, n["name"])] = p
                    paths.add(p)
    return plan


# ── helpers over the database ────────────────────────────────────────────────────────────────


async def snapshot_of(session: AsyncSession, project: ProjectModel) -> ProjectSnapshot:
    from ..db.models import ApiSpecModel, DataTableModel, InterfaceSlug

    snap = ProjectSnapshot()
    wfs = (await session.execute(select(WorkflowModel.name, WorkflowModel.draft_definition)
                                 .where(WorkflowModel.project_id == project.id))).all()
    for name, definition in wfs:
        snap.workflow_names.add(name)
        for n in (definition or {}).get("nodes") or []:
            if n.get("type") == "Webhook" and (n.get("parameters") or {}).get("path"):
                snap.webhook_paths.add(n["parameters"]["path"])
    for t in (await session.execute(select(DataTableModel).where(DataTableModel.project_id == project.id))).scalars():
        snap.tables[t.name] = list(t.columns or [])
    for a in (await session.execute(select(ApiSpecModel).where(ApiSpecModel.project_id == project.id))).scalars():
        snap.apis[a.name] = api_content_hash({"base_url": a.base_url, "operations": (a.spec or {}).get("operations") or []})
    for (slug,) in (await session.execute(select(InterfaceSlug.slug).where(InterfaceSlug.project_id == project.id))).all():
        snap.interface_slugs.add(slug)
    for wid_defn in wfs:
        iface = (wid_defn[1] or {}).get("interface")
        if isinstance(iface, dict) and iface.get("slug"):
            snap.interface_slugs.add(iface["slug"])
    for (p,) in (await session.execute(select(ActiveTriggerModel.webhook_path)
                                       .where(ActiveTriggerModel.webhook_path.is_not(None)))).all():
        snap.webhook_paths.add(p)
    return snap


async def compute_needs(
    session: AsyncSession, project: ProjectModel, bundle: TemplateBundle, environment: str | None = None,
) -> list[dict[str, Any]]:
    """Secrets and variables the template needs that some environment of the project does not have yet."""
    from ..repositories.variable_repository import VariableRepository

    envs = [environment] if environment else env_names(project)
    repo = VariableRepository(session)
    out: list[dict[str, Any]] = []
    items = [("secret", s.name, list(s.used_by)) for s in bundle.requires.secrets]
    items += [("variable", v.name, []) for v in bundle.requires.variables]
    for kind, name, used_by in items:
        missing = []
        for env in envs:
            have = (await repo.get_by_key(project.team_id, name, env, project.id)
                    or await repo.get_by_key(project.team_id, name, env, None))
            if have is None:
                missing.append(env)
        if missing:
            out.append({"kind": kind, "name": name, "environments_missing": missing, "used_by": used_by})
    return out


def _stamp_tests(definition: dict[str, Any], slug: str, version: str, now: str) -> list[dict[str, Any]]:
    stamped = []
    for t in normalize_tests(definition.get("tests")):
        t = dict(t)
        t.update(status="approved", locked=True, approved_by=f"template:{slug}@{version}", approved_at=now)
        stamped.append(t)
    return stamped


def preview_of(bundle: TemplateBundle) -> dict[str, Any]:
    """What the template looks like, per workflow (the preview drawer): nodes, connections, interface kind."""
    out: dict[str, Any] = {}
    for w in bundle.workflows:
        d = w.definition
        out[w.key] = {
            "name": w.name, "role": w.role,
            "nodes": [{"name": n.get("name"), "type": n.get("type"), "label": n.get("label"), "position": n.get("position")}
                      for n in d.get("nodes") or []],
            "connections": [{k: c.get(k) for k in ("source_node", "target_node", "source_output", "target_input")}
                            for c in d.get("connections") or []],
            "interface": (d.get("interface") or {}).get("kind"),
            "tests": [t.get("name") for t in d.get("tests") or []],
        }
    return out


# ── install ──────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Created:
    workflows: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    variables: list[int] = field(default_factory=list)
    egress_before: list[str] | None = None
    install_row: str | None = None


class TemplateInstaller:
    def __init__(self, session: AsyncSession, catalog: dict[str, LoadedTemplate] | None = None) -> None:
        self._s = session
        self._catalog = catalog if catalog is not None else get_catalog()

    # ── lookups ──

    def template(self, slug: str, version: str | None = None) -> LoadedTemplate:
        lt = self._catalog.get(slug)
        if lt is None:
            raise template_not_found(slug)
        if version and version != lt.bundle.version:
            raise TemplateError(404, "TEMPLATE_VERSION_NOT_FOUND",
                                f"Template '{slug}' has no version {version} (catalog has {lt.bundle.version})")
        return lt

    async def _row(self, project_id: str, slug: str, install_name: str) -> TemplateInstallModel | None:
        return (await self._s.execute(select(TemplateInstallModel).where(
            TemplateInstallModel.project_id == project_id, TemplateInstallModel.template_slug == slug,
            TemplateInstallModel.install_name == install_name))).scalars().first()

    async def _live_row(self, project_id: str, slug: str, install_name: str, *, retire_stale: bool = False) -> TemplateInstallModel | None:
        """The install row for ``slug`` only while every workflow it made still exists.

        Rule (section 8.3): an install row is a claim that the template's workflows are in the project. When the user
        deleted any of them (all, or some) the claim is false, so the template no longer counts as installed: the row is
        dropped (``retire_stale``) and a new install proceeds, with the normal clash handling for what survived.
        """
        row = await self._row(project_id, slug, install_name)
        if row is None:
            return None
        ids = list(((row.mapping or {}).get("workflows") or {}).values())
        if not ids:
            return row
        have = {i for (i,) in (await self._s.execute(select(WorkflowModel.id).where(WorkflowModel.id.in_(ids)))).all()}
        if have >= set(ids):
            return row
        if retire_stale:
            await self._s.delete(row)
            await self._s.commit()
        return None

    async def _free_install_name(self, project_id: str, slug: str) -> str:
        taken = {r for (r,) in (await self._s.execute(select(TemplateInstallModel.install_name).where(
            TemplateInstallModel.project_id == project_id, TemplateInstallModel.template_slug == slug))).all()}
        return _numbered(slug, taken, "{}-{}")

    # ── preview ──

    async def preview(self, project: ProjectModel, slug: str, req: InstallRequest) -> dict[str, Any]:
        """The install, decided but not done: names after clash handling, what is already installed, what is needed."""
        lt = self.template(slug, req.version)
        return await self._preview(project, lt, req, top=True)

    async def _preview(self, project: ProjectModel, lt: LoadedTemplate, req: InstallRequest, top: bool) -> dict[str, Any]:
        bundle = lt.bundle
        existing = await self._live_row(project.id, bundle.slug, bundle.slug)
        state = "new"
        if existing is not None:
            state = "already_installed" if existing.bundle_hash == lt.hash else "version_installed"
        if req.as_copy and top:
            state = "new"
        apis = normalized_apis(bundle, lt.api_texts)
        snap = await snapshot_of(self._s, project)
        plan = plan_install(bundle, snap, clash=req.clash, reuse_tables=req.reuse_tables, api_specs=apis)
        deps = []
        for r in bundle.requires.templates:
            dep = self.template(r.slug)
            row = await self._live_row(project.id, r.slug, r.slug)
            deps.append({"slug": r.slug, "version": dep.bundle.version,
                         "status": "already_installed" if row is not None else "will_install"})
        out: dict[str, Any] = {
            "template": {"slug": bundle.slug, "version": bundle.version}, "status": state,
            "plan": plan.as_dict(), "dependencies": deps,
            "needs": await compute_needs(self._s, project, bundle, req.environment),
            "egress_hosts": list(bundle.requires.egress_hosts),
        }
        if existing is not None:
            out["installed"] = {"install_id": existing.id, "version": existing.template_version}
        return out

    # ── install ──

    async def install(
        self, project: ProjectModel, slug: str, req: InstallRequest, actor: str | None = None, *, _dependency: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Install (or recognise an install of) ``slug`` into ``project``. Returns ``(http status, body)``."""
        lt = self.template(slug, req.version)
        bundle = lt.bundle
        project_id = project.id   # captured now: after a failed flush the instance is expired and reading it raises

        # 2. idempotency
        if not req.as_copy:
            row = await self._live_row(project.id, slug, slug, retire_stale=True)
            if row is not None:
                if row.bundle_hash == lt.hash:
                    return 200, await self._render(project, row, lt, status="already_installed", deps=await self._dep_rows(row))
                if _dependency:
                    return 200, await self._render(project, row, lt, status="already_installed", deps=await self._dep_rows(row))
                raise TemplateError(409, "TEMPLATE_VERSION_INSTALLED",
                                    f"'{slug}' {row.template_version} is already installed in this project",
                                    installed_version=row.template_version, catalog_version=bundle.version)
        install_name = await self._free_install_name(project.id, slug) if req.as_copy else slug

        # 2b. with clash="fail" refuse before anything is written, dependencies included
        if req.clash == "fail":
            early = plan_install(bundle, await snapshot_of(self._s, project), clash="fail", reuse_tables=req.reuse_tables)
            clashes = [c for c in early.clashes if c["kind"] != "api"]   # an identical API is reused, decided after the dependencies
            if clashes:
                raise TemplateError(409, "TEMPLATE_NAME_CLASH", "Some names are already taken in this project", clashes=clashes)

        # 3. dependencies, first, with the same clash options
        dep_results: list[dict[str, Any]] = []
        dep_workflows: dict[str, dict[str, str]] = {}
        for r in bundle.requires.templates:
            dep_lt = self.template(r.slug)
            if not satisfies(dep_lt.bundle.version, r.version):
                raise TemplateError(409, "TEMPLATE_DEPENDENCY_CONFLICT",
                                    f"'{slug}' needs '{r.slug}' {r.version}; the catalog has {dep_lt.bundle.version}")
            existing_dep = await self._live_row(project.id, r.slug, r.slug)
            if existing_dep is not None and not satisfies(existing_dep.template_version, r.version):
                raise TemplateError(409, "TEMPLATE_DEPENDENCY_CONFLICT",
                                    f"'{slug}' needs '{r.slug}' {r.version}; this project has {existing_dep.template_version}",
                                    installed_version=existing_dep.template_version)
            dep_req = InstallRequest(clash=req.clash, run_tests=False, environment=req.environment)
            _status, dep_body = await self.install(project, r.slug, dep_req, actor, _dependency=True)
            dep_results.append({"slug": r.slug, "install_id": dep_body["install_id"], "status": dep_body["status"]})
            dep_workflows[r.slug] = {w["key"]: w["id"] for w in dep_body["workflows"]}

        # 4. plan
        apis = normalized_apis(bundle, lt.api_texts)
        snap = await snapshot_of(self._s, project)
        plan = plan_install(bundle, snap, clash=req.clash, reuse_tables=req.reuse_tables, api_specs=apis)
        if plan.clashes:
            raise TemplateError(409, "TEMPLATE_NAME_CLASH", "Some names are already taken in this project",
                                clashes=plan.clashes)

        # 5. execute, undoing everything on any failure
        created = _Created()
        try:
            body = await self._execute(project, lt, plan, apis, dep_workflows, dep_results, install_name, actor, created, req)
        except IntegrityError as e:
            await self._undo(project, created, project_id)
            raise TemplateError(409, "TEMPLATE_INSTALL_CONFLICT",
                                "Another install changed this project at the same time; try again") from e
        except TemplateError:
            await self._undo(project, created, project_id)
            raise
        except Exception:
            await self._undo(project, created, project_id)
            raise

        # 8. tests, after the commit
        if req.run_tests:
            body["tests"] = await self._run_tests(body)
        return 201, body

    async def _dep_rows(self, row: TemplateInstallModel) -> list[dict[str, Any]]:
        out = []
        for dep_id in row.depends_on or []:
            d = await self._s.get(TemplateInstallModel, dep_id)
            if d is not None:
                out.append({"slug": d.template_slug, "install_id": d.id, "status": "already_installed"})
        return out

    async def _execute(
        self, project: ProjectModel, lt: LoadedTemplate, plan: InstallPlan, apis: dict[str, dict[str, Any]],
        dep_workflows: dict[str, dict[str, str]], dep_results: list[dict[str, Any]], install_name: str,
        actor: str | None, created: _Created, req: InstallRequest,
    ) -> dict[str, Any]:
        from ..repositories.api_spec_repository import ApiSpecRepository
        from ..repositories.workflow_repository import WorkflowRepository, definition_to_workflow
        from .data_table_service import DataTableService
        from .workflow_refs import refresh_draft_index

        bundle = lt.bundle
        s = self._s

        # 1. API specs, then the egress hosts (only when the project already restricts egress)
        api_ids: dict[str, str] = {}
        api_repo = ApiSpecRepository(s)
        for a in bundle.apis:
            ap = plan.apis[a.key]
            spec = apis[a.key]
            if ap.reuse:
                from ..db.models import ApiSpecModel
                row = (await s.execute(select(ApiSpecModel).where(ApiSpecModel.project_id == project.id,
                                                                  ApiSpecModel.name == ap.name))).scalars().first()
                api_ids[a.key] = row.id
            else:
                row = await api_repo.create(project_id=project.id, name=ap.name, base_url=spec["base_url"],
                                            spec={"operations": spec["operations"]}, source="upload")
                api_ids[a.key] = row.id
                created.apis.append(row.id)

        egress_added = await self._add_egress(project, bundle, created)

        # 2. tables and their seed rows
        table_ids: dict[str, str] = {}
        seeded: dict[str, int] = {}
        tsvc = DataTableService(s)
        for t in bundle.tables:
            tp = plan.tables[t.key]
            if tp.reuse:
                table_ids[t.key] = (await tsvc.table_by_name(project.id, tp.name)).id
                seeded[t.key] = 0
                continue
            made = await tsvc.create_table(project, {
                "name": tp.name, "description": t.description,
                "columns": [{"name": c.name, "type": c.type, "required": c.required,
                             **({"default": c.default} if c.default is not None else {})} for c in t.columns],
            }, actor=actor)
            table_ids[t.key] = made["id"]
            created.tables.append(made["id"])
            if t.seed_rows:
                try:
                    await tsvc.insert_rows(await tsvc.get_table(project, made["id"]), copy.deepcopy(t.seed_rows))
                except ProjectError as e:
                    raise TemplateError(422, "TEMPLATE_INSTALL_FAILED", f"Seed rows for table '{t.name}' were refused: {e.message}",
                                        stage="tables", table_key=t.key, details=e.extra) from e
            seeded[t.key] = len(t.seed_rows)

        # variables with a default, only when missing
        variables_added = await self._add_variables(project, bundle, created)

        # 3. workflows: ids first (callers need their callees' ids), created callees first
        wf_ids = {w.key: new_workflow_id() for w in bundle.workflows}
        now = datetime.now().isoformat()
        by_key = {w.key: w for w in bundle.workflows}

        def resolver(kind: str, key: str) -> str:
            if kind == "workflow":
                if ":" in key:
                    dslug, _, dkey = key.partition(":")
                    return dep_workflows[dslug][dkey]
                return wf_ids[key]
            if kind == "table":
                return plan.tables[key].name   # the DataTable node addresses a table by name
            return api_ids[key]

        repo = WorkflowRepository(s)
        for key in plan.order:
            w = by_key[key]
            try:
                definition = replace_tokens(w.definition, resolver)
            except KeyError as e:
                raise TemplateError(422, "TEMPLATE_INSTALL_FAILED", f"Token {e} could not be resolved", stage="tokens",
                                    workflow_key=key) from e
            definition["tests"] = _stamp_tests(definition, bundle.slug, bundle.version, now)
            if key in plan.slugs and isinstance(definition.get("interface"), dict):
                definition["interface"]["slug"] = plan.slugs[key]
            for node in definition.get("nodes") or []:
                p = plan.webhook_paths.get((key, node.get("name")))
                if p is not None and node.get("type") == "Webhook":
                    node["parameters"]["path"] = p
            workflow = definition_to_workflow(definition, plan.workflow_names[key], wf_ids[key], w.description)
            stored = await repo.create(workflow, project_id=project.id, created_by=actor)
            created.workflows.append(stored.id)
            await refresh_draft_index(s, stored.id)
            await s.commit()

        # 4. publish the sub-workflows, callees first; entry workflows stay drafts
        service = self._workflow_service()
        published: dict[str, int | None] = {w.key: None for w in bundle.workflows}
        for key in plan.order:
            w = by_key[key]
            if w.role != "subworkflow" or not w.publish:
                continue
            try:
                await service.publish(wf_ids[key], message=f"Installed from template {bundle.slug} {bundle.version}")
            except WorkflowHasErrorsError as e:
                raise TemplateError(422, "TEMPLATE_INSTALL_FAILED",
                                    f"'{plan.workflow_names[key]}' did not pass the mistake checker when publishing",
                                    stage="publish", workflow_key=key, findings=e.findings) from e
            except ApprovedTestsFailError as e:
                raise TemplateError(422, "TEMPLATE_INSTALL_FAILED",
                                    f"'{plan.workflow_names[key]}' has failing tests, so it was not published",
                                    stage="publish", workflow_key=key, test_failures=e.failed_tests) from e
            except (ConflictError, ValidationError) as e:
                raise TemplateError(422, "TEMPLATE_INSTALL_FAILED", getattr(e, "message", str(e)),
                                    stage="publish", workflow_key=key) from e
            published[key] = await self._published_number(wf_ids[key])

        # 5. the install row, last
        needs = await compute_needs(s, project, bundle, req.environment)
        dep_ids = [d["install_id"] for d in dep_results]
        mapping = {
            "workflows": dict(wf_ids), "roles": {w.key: w.role for w in bundle.workflows},
            "tables": dict(table_ids), "table_names": {k: v.name for k, v in plan.tables.items()},
            "tables_created": [k for k, v in plan.tables.items() if not v.reuse],
            "apis": dict(api_ids), "apis_created": [k for k, v in plan.apis.items() if not v.reuse],
            "slugs": dict(plan.slugs), "egress_added": egress_added, "variables_added": variables_added,
            "renamed": plan.renamed, "seeded": seeded,
        }
        row = TemplateInstallModel(
            id=generate_id("tpi"), project_id=project.id, template_slug=bundle.slug, template_version=bundle.version,
            bundle_hash=lt.hash, install_name=install_name, status="installed", mapping=mapping, needs=needs,
            depends_on=dep_ids, installed_by=actor, created_at=datetime.now(), updated_at=datetime.now())
        s.add(row)
        await s.commit()
        created.install_row = row.id

        body = await self._render(project, row, lt, status="installed", deps=dep_results, plan=plan, published=published)
        body["renamed"] = plan.renamed
        return body

    def _workflow_service(self):
        from ..repositories import ExecutionRepository, WorkflowRepository
        from ..engine.node_registry import node_registry
        from .node_service import NodeService
        from .workflow_service import WorkflowService

        return WorkflowService(WorkflowRepository(self._s), ExecutionRepository(self._s), NodeService(node_registry), node_registry)

    async def _published_number(self, workflow_id: str) -> int | None:
        m = await self._s.get(WorkflowModel, workflow_id)
        if m is None or m.published_version_id is None:
            return None
        await self._s.refresh(m)
        v = await self._s.get(WorkflowVersionModel, m.published_version_id)
        return v.version_number if v else None

    async def _add_egress(self, project: ProjectModel, bundle: TemplateBundle, created: _Created) -> list[str]:
        """Add the template's hosts to the allowlist, but only when the project already has one (empty = unrestricted)."""
        from ..broker.policy import host_matches

        settings = dict(project.settings or {})
        current = list(settings.get("egress_allowlist") or [])
        if not current:
            return []
        added = [h for h in bundle.requires.egress_hosts if not host_matches(h.lstrip("*."), current)]
        if not added:
            return []
        created.egress_before = list(current)
        settings["egress_allowlist"] = current + added
        project.settings = settings
        project.updated_at = datetime.now()
        await self._s.commit()
        return added

    async def _add_variables(self, project: ProjectModel, bundle: TemplateBundle, created: _Created) -> list[dict[str, str]]:
        from ..repositories.variable_repository import VariableRepository

        repo = VariableRepository(self._s)
        env = project.default_environment
        added = []
        for v in bundle.requires.variables:
            if v.default is None:
                continue
            have = (await repo.get_by_key(project.team_id, v.name, env, project.id)
                    or await repo.get_by_key(project.team_id, v.name, env, None))
            if have is not None:
                continue
            row = await repo.create(key=v.name, value=v.default, team_id=project.team_id, environment=env, type=v.type,
                                    description=v.description, project_id=project.id)
            created.variables.append(row.id)
            added.append({"key": v.name, "environment": env})
        return added

    async def _undo(self, project: ProjectModel, created: _Created, project_id: str) -> None:
        """Delete everything a failed install created, newest first. Best effort: each step is tried even if one fails."""
        from sqlalchemy import delete as sa_delete

        from ..db.models import ApiSpecModel, DataTableModel, DataTableRowModel, VariableModel
        from ..repositories.workflow_repository import WorkflowRepository

        s = self._s
        try:
            await s.rollback()
        except Exception:
            logger.exception("template install: rollback before undo failed")
        steps: list[tuple[str, Any]] = []
        if created.install_row:
            steps.append(("install row", lambda: s.execute(sa_delete(TemplateInstallModel).where(TemplateInstallModel.id == created.install_row))))
        for wid in reversed(created.workflows):
            steps.append((f"workflow {wid}", lambda wid=wid: WorkflowRepository(s).delete(wid)))
        for tid in reversed(created.tables):
            async def drop_table(tid=tid):
                await s.execute(sa_delete(DataTableRowModel).where(DataTableRowModel.table_id == tid))
                await s.execute(sa_delete(DataTableModel).where(DataTableModel.id == tid))
            steps.append((f"table {tid}", drop_table))
        for aid in reversed(created.apis):
            steps.append((f"api {aid}", lambda aid=aid: s.execute(sa_delete(ApiSpecModel).where(ApiSpecModel.id == aid))))
        for vid in created.variables:
            steps.append((f"variable {vid}", lambda vid=vid: s.execute(sa_delete(VariableModel).where(VariableModel.id == vid))))
        for label, step in steps:
            try:
                await step()
                await s.commit()
            except Exception:
                logger.exception("template install: could not undo %s", label)
                await s.rollback()
        if created.egress_before is not None:
            try:
                p = await s.get(ProjectModel, project_id)
                if p is not None:
                    await s.refresh(p)
                    p.settings = {**(p.settings or {}), "egress_allowlist": created.egress_before}
                    await s.commit()
                    project.settings = p.settings
            except Exception:
                logger.exception("template install: could not restore the egress allowlist")
                await s.rollback()

    # ── response ──

    async def _render(
        self, project: ProjectModel, row: TemplateInstallModel, lt: LoadedTemplate | None, *, status: str,
        deps: list[dict[str, Any]] | None = None, plan: InstallPlan | None = None, published: dict[str, int | None] | None = None,
    ) -> dict[str, Any]:
        from ..db.models import DataTableModel, DataTableRowModel
        from sqlalchemy import func

        m = row.mapping or {}
        roles = m.get("roles") or {}
        wfs = []
        for key, wid in (m.get("workflows") or {}).items():
            model = await self._s.get(WorkflowModel, wid)
            if model is not None:
                await self._s.refresh(model)
            pv = (published or {}).get(key) if published is not None else (await self._published_number(wid) if model else None)
            wfs.append({"key": key, "id": wid, "name": model.name if model else None, "published_version": pv,
                        "role": roles.get(key)})
        tables = []
        for key, tid in (m.get("tables") or {}).items():
            t = await self._s.get(DataTableModel, tid)
            n = (await self._s.execute(select(func.count()).select_from(DataTableRowModel)
                                       .where(DataTableRowModel.table_id == tid))).scalar_one() if t else 0
            tables.append({"key": key, "id": tid, "name": t.name if t else (m.get("table_names") or {}).get(key),
                           "reused": key not in (m.get("tables_created") or []), "rows": n,
                           "seeded_rows": (m.get("seeded") or {}).get(key, 0)})
        apis = [{"key": k, "id": v, "reused": k not in (m.get("apis_created") or [])} for k, v in (m.get("apis") or {}).items()]
        return {
            "install_id": row.id, "status": status,
            "template": {"slug": row.template_slug, "version": row.template_version},
            "install_name": row.install_name, "project_id": row.project_id,
            "workflows": wfs, "tables": tables, "apis": apis,
            "renamed": list(m.get("renamed") or []), "needs": list(row.needs or []),
            "dependencies": deps or [], "egress_added": list(m.get("egress_added") or []),
            "slugs": dict(m.get("slugs") or {}),
        }

    async def _run_tests(self, body: dict[str, Any]) -> dict[str, Any]:
        from ..repositories.workflow_repository import WorkflowRepository
        from .test_runner import run_tests

        repo = WorkflowRepository(self._s)
        out: dict[str, Any] = {}
        for w in body["workflows"]:
            stored = await repo.get(w["id"])
            if stored is None:
                continue
            report = await run_tests(repo, stored, persist=True)
            summary = report["summary"]
            out[w["key"]] = {"passed": summary["passed"], "failed": summary["failed"] + summary["errored"],
                             "total": summary["total"]}
            bad = [{"test_id": r["test_id"], "name": r["name"], "failures": r["failures"]}
                   for r in report["results"] if r["status"] != "passed"]
            if bad:
                out[w["key"]]["failing"] = bad
        return out

    # ── reads ──

    async def installs_of(self, project: ProjectModel) -> list[dict[str, Any]]:
        rows = (await self._s.execute(select(TemplateInstallModel).where(TemplateInstallModel.project_id == project.id)
                                      .order_by(TemplateInstallModel.created_at))).scalars().all()
        out = []
        for r in rows:
            m = r.mapping or {}
            wfs = []
            for key, wid in (m.get("workflows") or {}).items():
                model = await self._s.get(WorkflowModel, wid)
                wfs.append({"key": key, "id": wid, "name": model.name if model else None})
            out.append({"install_id": r.id, "slug": r.template_slug, "version": r.template_version,
                        "install_name": r.install_name, "created_at": r.created_at.isoformat(), "workflows": wfs})
        return out

    async def install_detail(self, project: ProjectModel, install_id: str) -> dict[str, Any]:
        row = await self._s.get(TemplateInstallModel, install_id)
        if row is None or row.project_id != project.id:
            raise TemplateError(404, "TEMPLATE_INSTALL_NOT_FOUND", "Install not found")
        lt = self._catalog.get(row.template_slug)
        body = await self._render(project, row, lt, status=row.status, deps=await self._dep_rows(row))
        body["mapping"] = row.mapping
        body["created_at"] = row.created_at.isoformat()
        body["installed_by"] = row.installed_by
        if lt is not None:
            body["needs"] = await compute_needs(self._s, project, lt.bundle)   # recomputed: secrets may exist by now
        return body

    async def installed_in(self, slug: str) -> list[str]:
        return sorted({p for (p,) in (await self._s.execute(select(TemplateInstallModel.project_id).where(
            TemplateInstallModel.template_slug == slug))).all()})


# ── export (section 8.4) ─────────────────────────────────────────────────────────────────────


_KEY_CLEAN = re.compile(r"[^a-z0-9]+")


def _key_for(name: str, taken: set[str]) -> str:
    base = _KEY_CLEAN.sub("_", name.lower()).strip("_") or "workflow"
    if not base[0].isalpha():
        base = "w_" + base
    base = base[:36]
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    taken.add(key)
    return key


async def export_template(
    session: AsyncSession, workflow_id: str, slug: str | None = None, version: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """A ``wf-template/1`` bundle of a workflow and everything it needs, ids replaced by tokens.

    Contains the workflow (role ``entry``) and its transitive sub-workflows (role ``subworkflow``), the tables the
    DataTable nodes use (schema only, no rows), the API specs the HTTP nodes reference (as normalised specs) and the
    secrets / variables the definitions read (names only). Returns ``(bundle, warnings)``; the bundle is validated in
    warn mode, so a rough draft comes back with its problems listed instead of being refused."""
    from ..db.models import ApiSpecModel, DataTableModel, VariableModel
    from .workflow_refs import refs_of

    warnings: list[str] = []
    root = await session.get(WorkflowModel, workflow_id)
    if root is None:
        raise TemplateError(404, "WORKFLOW_NOT_FOUND", f"Workflow '{workflow_id}' not found")
    slug = slug or (_KEY_CLEAN.sub("-", root.name.lower()).strip("-") or "template")

    # the workflow and its callees, in the project (a call to another project is not followed)
    models: dict[str, WorkflowModel] = {root.id: root}
    queue = [root.id]
    while queue:
        cur = models[queue.pop()]
        for _node, child, _spec in refs_of(cur.draft_definition):
            if child in models:
                continue
            m = await session.get(WorkflowModel, child)
            if m is None:
                warnings.append(f"'{cur.name}' calls workflow '{child}', which does not exist; the call is left as it is")
            elif m.project_id != root.project_id:
                warnings.append(f"'{cur.name}' calls '{m.name}' in another project; the call is left as it is")
            else:
                models[child] = m
                queue.append(child)

    taken: set[str] = set()
    keys = {wid: ("main" if wid == root.id else _key_for(m.name, taken)) for wid, m in models.items()}
    taken.add("main")

    tables: dict[str, dict[str, Any]] = {}   # table name -> bundle table
    table_keys: dict[str, str] = {}
    api_rows: dict[str, ApiSpecModel] = {}
    api_keys: dict[str, str] = {}
    var_refs: dict[str, set[str]] = {}
    secret_scan = re.compile(r"\$vars\.([A-Za-z_][A-Za-z0-9_]*)")
    out_workflows = []

    for wid, m in models.items():
        defn = copy.deepcopy(m.draft_definition or {})
        wkey = keys[wid]
        for n in defn.get("nodes") or []:
            params = n.get("parameters") or {}
            if n.get("type") in ("ExecuteWorkflow", "WorkflowTool") and isinstance(params.get("workflowId"), str) \
                    and params["workflowId"] in keys:
                params["workflowId"] = f"@tpl:workflow/{keys[params['workflowId']]}"
            elif n.get("type") == "DataTable" and isinstance(params.get("table"), str) and params["table"] \
                    and "{{" not in params["table"]:
                tname = params["table"]
                if tname not in table_keys:
                    t = (await session.execute(select(DataTableModel).where(
                        DataTableModel.project_id == root.project_id, DataTableModel.name == tname))).scalars().first()
                    if t is None:
                        warnings.append(f"'{m.name}' uses table '{tname}', which does not exist; it is not in the bundle")
                        continue
                    k = _key_for(tname, set(table_keys.values()))
                    table_keys[tname] = k
                    tables[tname] = {"key": k, "name": tname, "description": t.description,
                                     "columns": [{kk: vv for kk, vv in c.items() if kk in ("name", "type", "required", "default")}
                                                 for c in t.columns], "seed_rows": []}
                params["table"] = f"@tpl:table/{table_keys[tname]}"
            elif n.get("type") == "HttpRequest" and isinstance(params.get("api_ref"), dict) and params["api_ref"].get("spec_id"):
                sid = params["api_ref"]["spec_id"]
                if sid not in api_keys:
                    row = await session.get(ApiSpecModel, sid)
                    if row is None:
                        warnings.append(f"'{m.name}' uses API '{sid}', which does not exist; it is not in the bundle")
                        continue
                    k = _key_for(row.name, set(api_keys.values()))
                    api_keys[sid] = k
                    api_rows[sid] = row
                params["api_ref"]["spec_id"] = f"@tpl:api/{api_keys[sid]}"
            for pth, s in _strings(n):
                for v in secret_scan.findall(s):
                    var_refs.setdefault(v, set()).add(f"{wkey}/{n.get('name')}")
            for ck in ("credential", "credentialId", "credential_id", "credentials"):
                if params.get(ck):
                    warnings.append(f"node '{n.get('name')}' in '{m.name}' has a credential reference; it was removed")
                    params[ck] = ""
        wf_settings = defn.get("settings") or {}
        if isinstance(wf_settings.get("error_workflow_id"), str) and wf_settings["error_workflow_id"] in keys:
            wf_settings["error_workflow_id"] = f"@tpl:workflow/{keys[wf_settings['error_workflow_id']]}"
        for t in defn.get("tests") or []:
            if t.get("status") != "approved":
                warnings.append(f"test '{t.get('name')}' of '{m.name}' is not approved; bundle tests ship approved")
            t.pop("approved_by", None)   # who approved is not the template's business: the installer stamps its own
            t.pop("approved_at", None)
        out_workflows.append({
            "key": wkey, "role": "entry" if wid == root.id else "subworkflow", "name": m.name,
            "description": m.description, "publish": wid != root.id, "definition": defn,
        })

    # secrets and variables, by their definition in the project; unknown names are listed as plain variables
    secrets, variables = [], []
    for name, used in sorted(var_refs.items()):
        row = (await session.execute(select(VariableModel).where(
            VariableModel.team_id == root_team(root), VariableModel.key == name))).scalars().first()
        if row is not None and row.type == "secret":
            secrets.append({"name": name, "used_by": sorted(used)})
        else:
            variables.append({"name": name, "default": None if row is None else (row.value if row.type != "secret" else None),
                              "type": "number" if row is not None and row.type == "number" else "string"})
            if row is None:
                warnings.append(f"$vars.{name} is not defined in this project; it is listed as a variable without a default")

    from urllib.parse import urlparse
    apis, hosts = [], set()
    for sid, row in api_rows.items():
        apis.append({"key": api_keys[sid], "name": row.name,
                     "spec": {"base_url": row.base_url, "operations": (row.spec or {}).get("operations") or []}})
        if urlparse(row.base_url or "").hostname:
            hosts.add(urlparse(row.base_url).hostname)
    for w in out_workflows:
        for n in w["definition"].get("nodes") or []:
            if n.get("type") == "HttpRequest" and not (n.get("parameters") or {}).get("api_ref"):
                h = urlparse(str((n.get("parameters") or {}).get("url") or "")).hostname
                if h:
                    hosts.add(h)

    bundle = {
        "format": "wf-template/1", "slug": slug, "version": version or "0.1.0", "name": root.name,
        "summary": root.description or f"{root.name}, exported from a workflow.", "category": "exported", "tags": [],
        "min_engine_stage": 8,
        "requires": {"templates": [], "secrets": secrets, "variables": variables, "egress_hosts": sorted(hosts)},
        "apis": apis, "tables": list(tables.values()), "workflows": sorted(out_workflows, key=lambda w: w["role"] != "entry"),
        "ci": {"min_tests_per_workflow": 3},
    }
    api_texts: dict[str, str] = {}
    try:
        _b, issues = validate_bundle(bundle, api_texts, expected_slug=None)
        warnings += [f"{i.code} at {i.path}: {i.message}" for i in issues]
    except TemplateBundleError as e:
        warnings += [f"{i.code} at {i.path}: {i.message}" for i in e.issues]
    return bundle, warnings


def root_team(model: WorkflowModel) -> str:
    return model.team_id or "default"


def _strings(obj: Any, path: str = ""):
    from .template_bundle import iter_strings

    return iter_strings(obj, path)

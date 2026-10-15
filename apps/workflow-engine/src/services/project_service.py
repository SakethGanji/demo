"""Projects: CRUD, id/slug resolution, environments, summary + health (BACKEND-DESIGN §3.1)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    DEFAULT_PROJECT_ID,
    AppModel,
    DataTableModel,
    ExecutionModel,
    ProjectModel,
    VariableModel,
    WorkflowModel,
)
from ..utils.ids import generate_id

SLUG_RE = re.compile(r"^[a-z0-9](?:-?[a-z0-9])*$")
ENV_RE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")


class ProjectError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:63] or "project"


def env_names(project: ProjectModel) -> list[str]:
    return [e.get("name") for e in (project.environments or []) if e.get("name")]


def env_kind(project: ProjectModel, env: str) -> str:
    for e in project.environments or []:
        if e.get("name") == env:
            return e.get("kind") or "draft"
    return "draft"


def effective_environment(project: ProjectModel | None, requested: str | None, strict: bool = False) -> str:
    """Map a run's requested environment onto the project.

    The studio always sends ``environment: "default"`` (schemas default). For projects that
    have no ``default`` environment this means "the project's default environment".
    Any other explicit name is used as-is (variables can define free-form envs).
    """
    if project is None:
        return requested or "default"
    names = env_names(project)
    if not requested or (requested == "default" and "default" not in names):
        return project.default_environment
    if strict and project.id != DEFAULT_PROJECT_ID and requested not in names:
        raise ProjectError(422, "unknown_environment", f"environment '{requested}' is not one of {names}",
                           environment=requested)
    return requested


async def resolve_project(session: AsyncSession, ref: str) -> ProjectModel | None:
    """Accept a project id (proj_...) or a slug."""
    if not ref:
        return None
    p = await session.get(ProjectModel, ref)
    if p:
        return p
    res = await session.execute(
        select(ProjectModel).where(ProjectModel.team_id == "default", func.lower(ProjectModel.slug) == ref.lower())
    )
    return res.scalars().first()


async def get_project_for_workflow(session: AsyncSession, workflow_id: str) -> ProjectModel | None:
    wf = await session.get(WorkflowModel, workflow_id)
    pid = wf.project_id if wf else DEFAULT_PROJECT_ID
    return await session.get(ProjectModel, pid)


def project_to_dict(p: ProjectModel) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "description": p.description,
        "environments": p.environments,
        "default_environment": p.default_environment,
        "live_environment": p.live_environment,
        "settings": p.settings or {},
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def require(self, ref: str) -> ProjectModel:
        p = await resolve_project(self._s, ref)
        if not p:
            raise ProjectError(404, "project_not_found", f"Project '{ref}' not found")
        return p

    async def list(self) -> list[ProjectModel]:
        res = await self._s.execute(select(ProjectModel).order_by(ProjectModel.created_at))
        return list(res.scalars().all())

    async def _slug_taken(self, slug: str, exclude_id: str | None = None) -> bool:
        stmt = select(ProjectModel.id).where(ProjectModel.team_id == "default", func.lower(ProjectModel.slug) == slug.lower())
        rows = (await self._s.execute(stmt)).scalars().all()
        return any(r != exclude_id for r in rows)

    async def create(self, name: str, slug: str | None = None, description: str | None = None,
                     created_by: str | None = None) -> ProjectModel:
        if not name or not name.strip():
            raise ProjectError(422, "invalid_name", "name is required")
        if slug is None:
            base = slugify(name)
            slug = base
            n = 2
            while await self._slug_taken(slug) or slug.startswith("proj_"):
                slug = f"{base}-{n}"
                n += 1
        else:
            self._validate_slug(slug)
            if await self._slug_taken(slug):
                raise ProjectError(409, "slug_taken", f"Slug '{slug}' is already used")
        now = datetime.now()
        p = ProjectModel(
            id=generate_id("proj"), team_id="default", name=name.strip(), slug=slug, description=description,
            environments=[{"name": "dev", "kind": "draft"}, {"name": "prod", "kind": "release"}],
            default_environment="dev", live_environment="prod", settings={"egress_allowlist": []},
            created_by=created_by, created_at=now, updated_at=now,
        )
        self._s.add(p)
        await self._s.commit()
        await self._s.refresh(p)
        return p

    @staticmethod
    def _validate_slug(slug: str) -> None:
        if not (3 <= len(slug) <= 63) or not SLUG_RE.match(slug) or slug.startswith("proj"):
            raise ProjectError(422, "invalid_slug", "slug must be 3-63 chars of a-z, 0-9 and single hyphens")

    async def update(self, ref: str, body: dict[str, Any]) -> ProjectModel:
        p = await self.require(ref)
        if "name" in body and body["name"] is not None:
            if not str(body["name"]).strip():
                raise ProjectError(422, "invalid_name", "name is required")
            p.name = str(body["name"]).strip()
        if "description" in body:
            p.description = body["description"]
        if body.get("slug") is not None and body["slug"] != p.slug:
            if p.id == DEFAULT_PROJECT_ID:
                raise ProjectError(409, "default_project", "The Default project's slug cannot change")
            self._validate_slug(body["slug"])
            if await self._slug_taken(body["slug"], exclude_id=p.id):
                raise ProjectError(409, "slug_taken", f"Slug '{body['slug']}' is already used")
            p.slug = body["slug"]
        if body.get("environments") is not None:
            envs = body["environments"]
            names: list[str] = []
            for e in envs:
                if not isinstance(e, dict) or not ENV_RE.match(str(e.get("name", ""))) or e.get("kind", "draft") not in ("draft", "release"):
                    raise ProjectError(422, "invalid_environment", f"invalid environment {e!r}")
                names.append(e["name"])
            if len(set(names)) != len(names) or not names:
                raise ProjectError(422, "invalid_environment", "environment names must be unique and non-empty")
            removed = set(env_names(p)) - set(names)
            for env in removed:
                if await self._env_in_use(p.id, env):
                    raise ProjectError(409, "environment_in_use", f"Environment '{env}' still has secrets or API keys", environment=env)
            p.environments = [{"name": e["name"], "kind": e.get("kind", "draft")} for e in envs]
        names = env_names(p)
        for field in ("default_environment", "live_environment"):
            if body.get(field) is not None:
                setattr(p, field, body[field])
            if getattr(p, field) not in names:
                raise ProjectError(422, "unknown_environment", f"{field} '{getattr(p, field)}' is not one of {names}")
        if body.get("settings") is not None:
            settings = dict(p.settings or {})
            settings.update(body["settings"])
            allow = settings.get("egress_allowlist", [])
            if not isinstance(allow, list) or not all(isinstance(h, str) and h for h in allow):
                raise ProjectError(422, "invalid_settings", "egress_allowlist must be a list of host patterns")
            p.settings = settings
        p.updated_at = datetime.now()
        await self._s.commit()
        await self._s.refresh(p)
        return p

    async def _env_in_use(self, project_id: str, env: str) -> bool:
        n = (await self._s.execute(
            select(func.count()).select_from(VariableModel).where(VariableModel.project_id == project_id, VariableModel.environment == env)
        )).scalar_one()
        if n:
            return True
        try:
            from ..db.models import ApiKeyModel  # phase 5
            k = (await self._s.execute(
                select(func.count()).select_from(ApiKeyModel).where(
                    ApiKeyModel.project_id == project_id, ApiKeyModel.environment == env, ApiKeyModel.revoked_at.is_(None))
            )).scalar_one()
            return bool(k)
        except ImportError:
            return False

    async def counts(self, project_id: str) -> dict[str, int]:
        async def c(model) -> int:
            return (await self._s.execute(select(func.count()).select_from(model).where(model.project_id == project_id))).scalar_one()
        out = {"apps": await c(AppModel), "workflows": await c(WorkflowModel), "tables": await c(DataTableModel), "routes": 0}
        try:
            from ..db.models import ApiRouteModel  # phase 5
            out["routes"] = await c(ApiRouteModel)
        except ImportError:
            pass
        return out

    async def last_run_and_health(self, project_id: str) -> tuple[dict | None, dict]:
        last = (await self._s.execute(
            select(ExecutionModel).where(ExecutionModel.project_id == project_id).order_by(ExecutionModel.start_time.desc()).limit(1)
        )).scalars().first()
        since = datetime.now() - timedelta(hours=24)
        rows = (await self._s.execute(
            select(ExecutionModel.status, func.count()).where(
                ExecutionModel.project_id == project_id, ExecutionModel.start_time >= since
            ).group_by(ExecutionModel.status)
        )).all()
        total = sum(n for _, n in rows)
        failed = sum(n for st, n in rows if st == "failed")
        if total == 0:
            status = "unknown"
        elif failed / total > 0.5:
            status = "failing"
        elif failed / total > 0.05:
            status = "degraded"
        else:
            status = "ok"
        last_run = None
        if last:
            last_run = {"execution_id": last.id, "status": last.status, "mode": last.mode,
                        "start_time": last.start_time.isoformat()}
        return last_run, {"status": status, "runs_24h": total, "failed_24h": failed}

    async def summary_extras(self, p: ProjectModel) -> dict[str, Any]:
        """G4: stub_routes, api_keys (active), active_releases per environment."""
        out: dict[str, Any] = {"stub_routes": 0, "api_keys": 0, "active_releases": {e: None for e in env_names(p)}}
        try:
            from ..db.models import ApiKeyModel, ApiRouteModel
            out["stub_routes"] = (await self._s.execute(select(func.count()).select_from(ApiRouteModel).where(
                ApiRouteModel.project_id == p.id, ApiRouteModel.handler_type == "stub"))).scalar_one()
            out["api_keys"] = (await self._s.execute(select(func.count()).select_from(ApiKeyModel).where(
                ApiKeyModel.project_id == p.id, ApiKeyModel.revoked_at.is_(None)))).scalar_one()
        except ImportError:
            pass
        try:
            from ..db.models import ReleaseModel
            rows = (await self._s.execute(select(ReleaseModel).where(
                ReleaseModel.project_id == p.id, ReleaseModel.is_active.is_(True)))).scalars().all()
            for r in rows:
                out["active_releases"][r.environment] = {"release_id": r.id, "number": r.number,
                                                         "created_at": r.created_at.isoformat()}
        except ImportError:
            pass
        return out

    async def preview_update(self, ref: str, body: dict[str, Any]) -> dict[str, Any]:
        """G26: what an update would affect (slug change → apps that call /api/p/<old>/ or are published)."""
        p = await self.require(ref)
        affected: list[dict[str, Any]] = []
        new_slug = body.get("slug")
        if new_slug is not None and new_slug != p.slug:
            self._validate_slug(new_slug)
            if await self._slug_taken(new_slug, exclude_id=p.id):
                raise ProjectError(409, "slug_taken", f"Slug '{new_slug}' is already used")
            from ..db.models import AppFileModel
            apps = (await self._s.execute(select(AppModel).where(AppModel.project_id == p.id))).scalars().all()
            needle = f"/api/p/{p.slug}/"
            for a in apps:
                uses = False
                if a.current_version_id:
                    files = (await self._s.execute(select(AppFileModel).where(AppFileModel.version_id == a.current_version_id))).scalars().all()
                    uses = any(needle in (f.content or "") for f in files)
                uses = uses or needle in (a.draft_source_code or "")
                if uses or a.published_version_id:
                    affected.append({"app_id": a.id, "name": a.name, "references_slug": uses,
                                     "published": a.published_version_id is not None})
        preview = {**project_to_dict(p), **{k: v for k, v in body.items() if k in ("name", "slug", "description")}}
        return {"project": preview, "affected_apps": affected}

    async def list_item(self, p: ProjectModel) -> dict[str, Any]:
        last_run, health = await self.last_run_and_health(p.id)
        return {**project_to_dict(p), "counts": await self.counts(p.id), "last_run": last_run, "health": health}

    async def delete(self, ref: str) -> None:
        p = await self.require(ref)
        if p.id == DEFAULT_PROJECT_ID:
            raise ProjectError(409, "default_project", "The Default project cannot be deleted")
        counts = await self.counts(p.id)
        if any(counts.values()):
            raise ProjectError(409, "project_not_empty", "Project still has resources", counts=counts)
        # Remove project-scoped secrets and folders, then the project.
        from sqlalchemy import delete as sa_delete
        from ..db.models import FolderModel
        await self._s.execute(sa_delete(VariableModel).where(VariableModel.project_id == p.id))
        await self._s.execute(sa_delete(FolderModel).where(FolderModel.project_id == p.id))
        try:
            from ..db.models import ApiKeyModel, ApiSpecRevisionModel
            await self._s.execute(sa_delete(ApiKeyModel).where(ApiKeyModel.project_id == p.id))
            await self._s.execute(sa_delete(ApiSpecRevisionModel).where(ApiSpecRevisionModel.project_id == p.id))
        except ImportError:
            pass
        await self._s.delete(p)
        await self._s.commit()

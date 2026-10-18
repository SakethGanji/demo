"""Template routes (Stage 8, DESIGN-STAGE8-REUSE section 8 and 11).

    GET  /templates                                         the catalog
    GET  /templates/{slug}                                  one template: bundle, readme, preview
    POST /projects/{project}/templates/{slug}/install       install (201, or 200 when it already is)
    POST /projects/{project}/templates/{slug}/install-preview   the same decisions, nothing written
    GET  /projects/{project}/template-installs              what is installed
    GET  /projects/{project}/template-installs/{install_id} one install, with its mapping and a recomputed needs list
    GET  /workflows/{workflow_id}/export-template           a bundle of a workflow and its sub-workflows

Errors use the engine's ``{detail: {code, message, ...}}`` envelope; an unknown template, project, install or workflow
is a plain 404 and says no more than that.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..schemas.template import InstallRequest
from ..services.authz import actor_of, authorize
from ..services.project_service import ProjectError, ProjectService
from ..services.template_bundle import LoadedTemplate, TemplateBundleError, get_catalog
from ..services.template_installer import TemplateError, TemplateInstaller, export_template, preview_of
from .projects import raise_project_error

router = APIRouter()


def _http(e: TemplateError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail())


def _catalog() -> dict[str, LoadedTemplate]:
    try:
        return get_catalog()
    except TemplateBundleError as e:
        raise HTTPException(status_code=500, detail={
            "code": "TEMPLATE_CATALOG_INVALID", "message": "The template catalog has an invalid bundle",
            "issues": [i.as_dict() for i in e.issues]}) from e


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Not allowed"})


def _summary(lt: LoadedTemplate) -> dict[str, Any]:
    b = lt.bundle
    return {
        "slug": b.slug, "version": b.version, "name": b.name, "summary": b.summary, "category": b.category,
        "tags": list(b.tags), "min_engine_stage": b.min_engine_stage,
        "workflows": [{"key": w.key, "name": w.name, "role": w.role} for w in b.workflows],
        "tables": [t.name for t in b.tables], "apis": [a.name for a in b.apis],
        "requires": b.requires.model_dump(),
    }


def _api_summaries(lt: LoadedTemplate) -> list[dict[str, Any]]:
    from ..services.template_bundle import normalized_apis

    specs = normalized_apis(lt.bundle, lt.api_texts)
    return [{"key": a.key, "name": a.name, "base_url": specs[a.key]["base_url"],
             "operations": [{"operation_id": o.get("operation_id"), "method": o.get("method"), "path": o.get("path"),
                             "summary": o.get("summary")} for o in specs[a.key]["operations"]]}
            for a in lt.bundle.apis if a.key in specs]


@router.get("/templates")
async def list_templates(category: str | None = None, q: str | None = None, project: str | None = None,
                         session: AsyncSession = Depends(get_db_session)):
    items = list(_catalog().values())
    if category:
        items = [t for t in items if t.bundle.category == category]
    if q:
        needle = q.lower()
        items = [t for t in items if needle in " ".join([t.bundle.slug, t.bundle.name, t.bundle.summary, *t.bundle.tags]).lower()]
    out = [_summary(t) for t in items]
    if project:
        try:
            p = await ProjectService(session).require(project)
        except ProjectError as e:
            raise_project_error(e)
        installer = TemplateInstaller(session, _catalog())
        installed = {i["slug"] for i in await installer.installs_of(p)}
        for row in out:
            row["installed_in"] = [p.id] if row["slug"] in installed else []
    return out


@router.get("/templates/{slug}")
async def get_template(slug: str):
    lt = _catalog().get(slug)
    if lt is None:
        raise _http(TemplateError(404, "TEMPLATE_NOT_FOUND", f"Template '{slug}' not found"))
    return {**lt.raw, "readme": lt.readme, "bundle_hash": lt.hash, "preview": preview_of(lt.bundle),
            "api_summaries": _api_summaries(lt)}


async def _project(session: AsyncSession, ref: str):
    try:
        return await ProjectService(session).require(ref)
    except ProjectError as e:
        raise_project_error(e)


@router.post("/projects/{project}/templates/{slug}/install")
async def install_template(project: str, slug: str, request: Request, body: InstallRequest | None = None,
                           session: AsyncSession = Depends(get_db_session)):
    from fastapi.responses import JSONResponse

    p = await _project(session, project)
    if not authorize(request, "template.install", {"project_id": p.id, "slug": slug}):
        raise _forbidden()
    try:
        status, result = await TemplateInstaller(session, _catalog()).install(p, slug, body or InstallRequest(), actor_of(request))
    except TemplateError as e:
        raise _http(e) from e
    except ProjectError as e:
        raise_project_error(e)
    return JSONResponse(status_code=status, content=_jsonable(result))


@router.post("/projects/{project}/templates/{slug}/install-preview")
async def preview_install(project: str, slug: str, request: Request, body: InstallRequest | None = None,
                          session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    if not authorize(request, "template.install", {"project_id": p.id, "slug": slug}):
        raise _forbidden()
    try:
        return await TemplateInstaller(session, _catalog()).preview(p, slug, body or InstallRequest())
    except TemplateError as e:
        raise _http(e) from e


@router.get("/projects/{project}/template-installs")
async def list_installs(project: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    return await TemplateInstaller(session, _catalog()).installs_of(p)


@router.get("/projects/{project}/template-installs/{install_id}")
async def get_install(project: str, install_id: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    try:
        return await TemplateInstaller(session, _catalog()).install_detail(p, install_id)
    except TemplateError as e:
        raise _http(e) from e


@router.get("/workflows/{workflow_id}/export-template")
async def export_workflow_template(workflow_id: str, request: Request, slug: str | None = None, version: str | None = None,
                                   session: AsyncSession = Depends(get_db_session)):
    if not authorize(request, "template.export", {"workflow_id": workflow_id}):
        raise _forbidden()
    try:
        bundle, warnings = await export_template(session, workflow_id, slug, version)
    except TemplateError as e:
        raise _http(e) from e
    return {"bundle": bundle, "warnings": warnings}


def _jsonable(value: Any) -> Any:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(value)

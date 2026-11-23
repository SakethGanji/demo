"""One project file tree (LIVE-FILES §1): a read model over the stores a project already has. Nothing is moved.

    PLAN.md                        the approved plan as markdown (read-only; change it with a plan revision)
    plan.json                      the approved plan body (read-only)
    backend/<module>.py            a code module's source
    backend/tables/<table>.json    {name, columns, owner, rows} (read-only; change it with a migration)
    backend/routes.json            the project's API routes (read-only)
    apps/<app-slug>/<path>         the app's current version files (its draft source when it has no version yet)

A write goes to the owning store through its normal save path: a module through the module save (gates, schema preflight,
routes), an app file as a new app version with that file changed. Every change is recorded in ``project_file_changes`` (the
project's commit log, diff stats only) by the saves themselves via :func:`record_changes`; who made it (a builder in a build
step, the plan, or the user) comes from :func:`change_context`, which the build coordinator sets around each step.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Iterator

from .project_service import ProjectError

logger = logging.getLogger(__name__)

READ_ONLY = {
    "plan": "the plan is changed by revising it: start a plan revision, edit it there and approve it",
    "table": "a table's schema is changed by a migration (a module save that declares the columns, or the table's migrate "
             "action on the Data page)",
    "routes": "routes follow the module code: change the @api decorators in backend/<module>.py",
}
_LANGS = {".md": "markdown", ".json": "json", ".py": "python", ".ts": "typescript", ".js": "typescript", ".tsx": "tsx",
          ".jsx": "tsx", ".css": "css"}
_ENTRY = re.compile(r"(src/)?[Aa]pp\.tsx")

_CTX: ContextVar[dict[str, Any] | None] = ContextVar("project_file_change_context", default=None)


# ── who is changing files ────────────────────────────────────────────────────


@contextlib.contextmanager
def change_context(actor: str, build_id: str | None = None, step_key: str | None = None) -> Iterator[None]:
    """Changes recorded inside this block carry ``actor`` / ``build_id`` / ``step_key`` (default: the user, no build)."""
    token = _CTX.set({"actor": actor, "build_id": build_id, "step_key": step_key})
    try:
        yield
    finally:
        _CTX.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_CTX.get() or {"actor": "user", "build_id": None, "step_key": None})


# ── pure helpers ─────────────────────────────────────────────────────────────


def diff_stats(before: str | None, after: str | None) -> tuple[int, int]:
    """``(lines_added, lines_removed)`` between two texts (None is an absent file)."""
    a, b = (before or "").splitlines(), (after or "").splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def language_of(path: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", path)
    return _LANGS.get(m.group(0).lower(), "text") if m else "text"


def check_path(path: Any) -> str:
    """A tree path: relative, ``/``-separated, no empty / ``.`` / ``..`` segment. 422 ``invalid_path`` otherwise."""
    if not isinstance(path, str) or not path or len(path) > 1000:
        raise ProjectError(422, "invalid_path", "path is required")
    if path.startswith("/") or "\\" in path or "\x00" in path or re.match(r"^[A-Za-z]:", path):
        raise ProjectError(422, "invalid_path", "path must be relative to the project (no leading '/', no '\\')")
    if any(seg in ("", ".", "..") for seg in path.split("/")):
        raise ProjectError(422, "invalid_path", "path must not contain empty, '.' or '..' segments")
    return path


def slug_base(name: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60].strip("-") or "app"


def app_slugs(apps: list[Any]) -> dict[str, str]:
    """``{app_id: slug}``: the slug of the app name, deduplicated (``-2``, ``-3`` …) in creation order, so an app keeps its
    slug while apps are added after it."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for a in sorted(apps, key=lambda a: (a.created_at or datetime.min, a.id)):
        base = slug_base(a.name)
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        taken.add(slug)
        out[a.id] = slug
    return out


def module_path(name: str) -> str:
    return f"backend/{name}.py"


def table_path(name: str) -> str:
    return f"backend/tables/{name}.json"


def table_json(name: str, columns: list[dict[str, Any]] | None, owner: str | None = None, rows: int | None = None) -> str:
    return json.dumps({"name": name, "columns": list(columns or []), "owner": owner, "rows": rows}, indent=2) + "\n"


def routes_json(routes: list[dict[str, Any]]) -> str:
    keys = ("method", "path", "name", "module", "handler_type", "request_schema", "response_schema")
    return json.dumps([{k: r.get(k) for k in keys} for r in routes], indent=2) + "\n"


def plan_json(body: dict[str, Any]) -> str:
    return json.dumps(body, indent=2, default=str) + "\n"


def plan_markdown(body: dict[str, Any], version: int | None = None) -> str:
    """The approved plan as a readable markdown document (the body itself is plan.json)."""
    from . import project_plan as pp

    p = pp.normalize(body)
    out = [f"# {p['title'] or 'Project plan'}" + (f" (v{version})" if version is not None else ""), ""]
    if p["summary"]:
        out += [p["summary"], ""]
    reqs = p["requirements"]
    for title, items in (("Assumptions", reqs["assumptions"]), ("Limitations", reqs["limitations"])):
        if items:
            out += [f"## {title}", ""] + [f"- {x}" for x in items] + [""]
    if reqs["acceptance"]:
        out += ["## Acceptance criteria", ""] + [f"- **{a['id']}** {a['text']}" for a in reqs["acceptance"]] + [""]
    if reqs["answers"]:
        out += ["## Decisions", ""] + [f"- {a['question']} — {a['answer']}" for a in reqs["answers"]] + [""]
    if p["environments"]:
        out += ["## Environments", ""] + [f"- **{e['name']}**{' (live)' if e['live'] else ''}"
                                          + (f" {e['base_url']}" if e["base_url"] else "") for e in p["environments"]] + [""]
    if p["secrets"]:
        out += ["## Secrets", ""] + [f"- `{s['name']}`" + (f" — {s['purpose']}" if s["purpose"] else "") for s in p["secrets"]] + [""]
    if p["integrations"]:
        out += ["## Integrations", ""] + [f"- **{i['key']}** ({i['kind']})" + (f" — {i['purpose']}" if i["purpose"] else "")
                                          for i in p["integrations"]] + [""]
    if p["data"]["tables"]:
        out += ["## Data", ""]
        for t in p["data"]["tables"]:
            out += [f"### {t['name']}" + (f" (owner: {t['owner']})" if t["owner"] else ""), ""]
            if t["description"]:
                out += [t["description"], ""]
            out += ["| column | type | required |", "| --- | --- | --- |"]
            out += [f"| {c['name']} | {c.get('type')} | {'yes' if c.get('required') else ''} |" for c in t["columns"]] + [""]
    if p["modules"]:
        out += ["## Modules", ""]
        for m in p["modules"]:
            line = f"- **{m['key']}** ({m['kind']})" + (f" — {m['purpose']}" if m["purpose"] else "")
            if m["depends_on"]:
                line += f"; depends on {', '.join(m['depends_on'])}"
            if m["owns"]:
                line += f"; owns {', '.join(m['owns'])}"
            out.append(line)
        out.append("")
    if p["routes"]:
        out += ["## Routes", ""]
        for r in p["routes"]:
            line = f"- `{r['key']}`" + (f" ({r['module']})" if r["module"] else "") + (f" — {r['purpose']}" if r["purpose"] else "")
            req = ", ".join(f["name"] for f in r["request"]["fields"])
            resp = ", ".join(f["name"] for f in r["response"]["fields"])
            if req:
                line += f"; request: {req}"
            if resp:
                line += f"; response: {resp}"
            out.append(line)
        out.append("")
    if p["apps"]:
        out += ["## Apps", ""]
        for a in p["apps"]:
            out.append(f"### {a['name']}")
            out.append("")
            if a["users"]:
                out += [f"Users: {a['users']}", ""]
            out += [f"- **{s['name']}**" + (f" — {s['purpose']}" if s["purpose"] else "") for s in a["screens"]] + [""]
    order = pp.build_order(p)
    if order:
        out += ["## Build order", ""] + [f"{n}. {k}" for n, k in enumerate(order, 1)] + [""]
    return "\n".join(out).rstrip() + "\n"


# ── the change log ───────────────────────────────────────────────────────────


def change_to_dict(c: Any) -> dict[str, Any]:
    return {"id": c.id, "path": c.path, "op": c.op, "actor": c.actor, "build_id": c.build_id, "step_key": c.step_key,
            "summary": c.summary, "lines_added": c.lines_added, "lines_removed": c.lines_removed,
            "created_at": c.created_at.isoformat() if c.created_at else None}


async def record_changes(session: Any, project_id: str, changes: list[dict[str, Any]], *, actor: str | None = None,
                         commit: bool = False) -> int:
    """Record file changes ``[{path, before, after, summary?}]`` (``before`` None: created; ``after`` None: deleted; equal:
    nothing changed, not recorded). Never raises: a failure is logged and the save that called it goes on (the rows are
    written in a savepoint, so the caller's transaction is untouched). Returns the number of rows written."""
    from ..db.file_change_models import ProjectFileChangeModel
    from ..utils.ids import generate_id

    ctx = current_context()
    rows = []
    for ch in changes:
        before, after = ch.get("before"), ch.get("after")
        if before == after:
            continue
        op = "create" if before is None else "delete" if after is None else "update"
        added, removed = diff_stats(before, after)
        rows.append(ProjectFileChangeModel(
            id=generate_id("chg"), project_id=project_id, path=ch["path"], op=op, actor=actor or ctx["actor"],
            build_id=ctx["build_id"], step_key=ctx["step_key"], summary=(ch.get("summary") or None),
            lines_added=added, lines_removed=removed, created_at=datetime.now()))
    if not rows:
        return 0
    try:
        async with session.begin_nested():
            session.add_all(rows)
        if commit:
            await session.commit()
    except Exception:  # noqa: BLE001 - the change log never fails the save it describes
        logger.warning("could not record %d file change(s) for project %s", len(rows), project_id, exc_info=True)
        return 0
    return len(rows)


async def history(session: Any, project_id: str, path: str, limit: int = 50) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.file_change_models import ProjectFileChangeModel as M

    q = (select(M).where(M.project_id == project_id, M.path == check_path(path))
         .order_by(M.created_at.desc(), M.id.desc()).limit(max(1, min(int(limit), 500))))
    return [change_to_dict(c) for c in (await session.execute(q)).scalars().all()]


async def changes(session: Any, project_id: str, limit: int = 100, build_id: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.file_change_models import ProjectFileChangeModel as M

    q = select(M).where(M.project_id == project_id)
    if build_id:
        q = q.where(M.build_id == build_id)
    q = q.order_by(M.created_at.desc(), M.id.desc()).limit(max(1, min(int(limit), 500)))
    return [change_to_dict(c) for c in (await session.execute(q)).scalars().all()]


# ── the tree ─────────────────────────────────────────────────────────────────


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def _entry(path: str, kind: str, content: str, updated_at: Any, ref: dict[str, Any]) -> dict[str, Any]:
    return {"path": path, "kind": kind, "size": len(content.encode()), "updated_at": _iso(updated_at),
            "writable": kind in ("module", "app"), "ref": ref, "_content": content}


async def app_slug(session: Any, project_id: str, app_id: str) -> str:
    """The tree slug of one app (``apps/<slug>/``)."""
    from sqlalchemy import select

    from ..db.models import AppModel

    apps = (await session.execute(select(AppModel).where(AppModel.project_id == project_id))).scalars().all()
    return app_slugs(list(apps)).get(app_id) or slug_base(None)


async def version_files(session: Any, version: Any) -> dict[str, dict[str, Any]]:
    """``{path: {content, file_type}}`` of an app version; a single-source version is ``App.tsx``."""
    from sqlalchemy import select

    from ..db.models import AppFileModel

    rows = (await session.execute(select(AppFileModel).where(AppFileModel.version_id == version.id)
                                  .order_by(AppFileModel.path))).scalars().all()
    if rows:
        return {r.path: {"content": r.content, "file_type": r.file_type} for r in rows}
    return {"App.tsx": {"content": version.source_code, "file_type": "tsx"}} if version.source_code else {}


async def _app_entries(session: Any, project_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models import AppModel, AppVersionModel

    apps = list((await session.execute(select(AppModel).where(AppModel.project_id == project_id))).scalars().all())
    slugs = app_slugs(apps)
    out: list[dict[str, Any]] = []
    for a in sorted(apps, key=lambda a: slugs[a.id]):
        version = await session.get(AppVersionModel, a.current_version_id) if a.current_version_id else None
        if version is not None:
            files, at = await version_files(session, version), version.created_at
        else:
            files = {"App.tsx": {"content": a.draft_source_code, "file_type": "tsx"}} if a.draft_source_code else {}
            at = a.updated_at
        for path, f in sorted(files.items()):
            out.append(_entry(f"apps/{slugs[a.id]}/{path}", "app", f["content"], at, {"type": "app", "id": a.id}))
    return out


async def tree(session: Any, project: Any) -> list[dict[str, Any]]:
    """Every file of the project with its content (``_content``), in tree order: plan, backend, tables, routes, apps."""
    from sqlalchemy import func, select

    from ..db.models import ApiRouteModel, CodeModuleModel, DataTableModel, DataTableRowModel
    from . import project_plan as pp

    pid = project.id
    out: list[dict[str, Any]] = []
    plan = await pp._approved_row(session, pid)
    owners: dict[str, str | None] = {}
    if plan is not None:
        at = plan.approved_at or plan.updated_at
        ref = {"type": "plan", "id": plan.id}
        out.append(_entry("PLAN.md", "plan", plan_markdown(plan.body, plan.version), at, ref))
        out.append(_entry("plan.json", "plan", plan_json(plan.body), at, ref))
        owners = {t["name"]: t.get("owner") for t in pp.normalize(plan.body)["data"]["tables"]}
    mods = list((await session.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == pid)
                                       .order_by(CodeModuleModel.name))).scalars().all())
    for m in mods:
        out.append(_entry(module_path(m.name), "module", m.source, m.updated_at, {"type": "module", "id": m.id}))
    tables = list((await session.execute(select(DataTableModel).where(DataTableModel.project_id == pid)
                                         .order_by(DataTableModel.name))).scalars().all())
    counts = dict((await session.execute(
        select(DataTableRowModel.table_id, func.count()).where(DataTableRowModel.table_id.in_([t.id for t in tables]))
        .group_by(DataTableRowModel.table_id))).all()) if tables else {}
    for t in tables:
        out.append(_entry(table_path(t.name), "table", table_json(t.name, t.columns, owners.get(t.name), counts.get(t.id, 0)),
                          t.updated_at, {"type": "table", "id": t.id}))
    routes = list((await session.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid)
                                         .order_by(ApiRouteModel.path, ApiRouteModel.method))).scalars().all())
    names = {m.id: m.name for m in mods}
    out.append(_entry("backend/routes.json", "routes", routes_json([
        {"method": r.method, "path": r.path, "name": r.name, "module": names.get(r.handler_module_id),
         "handler_type": r.handler_type, "request_schema": r.request_schema, "response_schema": r.response_schema}
        for r in routes]), max((r.updated_at for r in routes), default=None), {"type": "routes", "id": pid}))
    out += await _app_entries(session, pid)
    return out


def public_entry(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if k != "_content"}


async def list_files(session: Any, project: Any) -> list[dict[str, Any]]:
    return [public_entry(e) for e in await tree(session, project)]


async def find(session: Any, project: Any, path: str) -> dict[str, Any]:
    path = check_path(path)
    e = next((x for x in await tree(session, project) if x["path"] == path), None)
    if e is None:
        raise ProjectError(404, "file_not_found", f"no file '{path}' in this project")
    return e


async def read_file(session: Any, project: Any, path: str) -> dict[str, Any]:
    e = await find(session, project, path)
    return {"path": e["path"], "content": e["_content"], "language": language_of(e["path"]), "kind": e["kind"],
            "writable": e["writable"], "updated_at": e["updated_at"]}


def _app_service(session: Any) -> Any:
    from .app_service import AppService
    from .bundle_storage import PostgresBundleStorage
    from .file_storage import PostgresFileStorage

    return AppService(session, PostgresFileStorage(session), PostgresBundleStorage(session))


async def write_file(session: Any, project: Any, path: str, content: str, *, base_updated_at: str | None = None,
                     actor: str | None = None) -> dict[str, Any]:
    """Save one file through its owner's normal save path. 409 ``read_only`` (with how to change it) for the plan, tables and
    routes; 409 ``conflict`` when ``base_updated_at`` is not the file's current ``updated_at``."""
    e = await find(session, project, path)
    if not e["writable"]:
        raise ProjectError(409, "read_only", f"{e['path']} is read-only: {READ_ONLY[e['kind']]}", kind=e["kind"])
    if base_updated_at is not None and base_updated_at != e["updated_at"]:
        raise ProjectError(409, "conflict", f"{e['path']} changed since you opened it; reload it", updated_at=e["updated_at"])
    if not isinstance(content, str):
        raise ProjectError(422, "invalid_request", "content must be a string")
    if e["kind"] == "module":
        from .code_module_service import CodeModuleService

        try:
            out = await CodeModuleService(session).update(project, e["ref"]["id"], content, actor=actor,
                                                          base_updated_at=base_updated_at)
        except ProjectError as err:
            if err.code == "module_changed":
                raise ProjectError(409, "conflict", f"{e['path']} changed since you opened it; reload it",
                                   updated_at=(err.extra.get("updated_at"))) from None
            raise
        return {"path": e["path"], "updated_at": out["module"]["updated_at"], "result": out}
    # an app file: a new app version with this one file changed (the Studio's own version save)
    from ..db.models import AppModel, AppVersionModel
    from ..schemas.app import AppFilePayload, AppUpdateRequest

    app_id = e["ref"]["id"]
    app = await session.get(AppModel, app_id)
    version = await session.get(AppVersionModel, app.current_version_id) if app.current_version_id else None
    files = await version_files(session, version) if version is not None else (
        {"App.tsx": {"content": app.draft_source_code or "", "file_type": "tsx"}})
    rel = e["path"].split("/", 2)[2]
    files[rel] = {**files.get(rel, {"file_type": "tsx"}), "content": content}
    entry = next((p for p in files if _ENTRY.fullmatch(p)), next(iter(files)))
    detail = await _app_service(session).update_app(app_id, AppUpdateRequest(
        source_code=files[entry]["content"],
        files=[AppFilePayload(path=p, content=f["content"], file_type=f.get("file_type") or "tsx") for p, f in files.items()],
        create_version=True, version_trigger="manual", version_prompt=f"Edited {rel}"))
    cv = detail.current_version
    new = await session.get(AppVersionModel, cv.id) if cv else None
    return {"path": e["path"], "updated_at": _iso(new.created_at) if new else detail.updated_at,
            "result": {"app_id": app_id, "version_id": str(cv.id) if cv else None,
                       "version_number": cv.version_number if cv else None}}

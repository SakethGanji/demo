"""Which app code calls which route ("used by", api-usage, breaking-change impact) — phase 6, G17.

Scans app source with tree-sitter (the parser from services/tsx_parser.py) for
* ``api.<operation>(...)`` calls on the generated client, and
* ``fetch('/api/p/<slug>/...', {method})`` literals (template literals: ``${…}`` matches a path param).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiSpecRevisionModel, AppFileModel, AppModel, AppVersionModel, ProjectModel
from .client_codegen import CLIENT_PATH
from .route_dispatcher import match_route


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _literal_value(node, src: bytes) -> str | None:
    if node.type == "string":
        return _text(node, src)[1:-1]
    if node.type == "template_string":
        out = []
        for ch in node.children:
            if ch.type in ("`",):
                continue
            if ch.type == "template_substitution":
                out.append("{_}")
            else:
                out.append(_text(ch, src))
        return "".join(out)
    return None


def _fetch_method(args_node, src: bytes) -> str:
    named = [c for c in args_node.children if c.is_named]
    if len(named) >= 2 and named[1].type == "object":
        for pair in named[1].children:
            if pair.type == "pair":
                key = pair.child_by_field_name("key")
                val = pair.child_by_field_name("value")
                if key is not None and _text(key, src).strip("'\"") == "method" and val is not None:
                    v = _literal_value(val, src)
                    if v:
                        return v.upper()
    return "GET"


def extract_api_calls(content: str, slug: str) -> list[dict[str, Any]]:
    from .tsx_parser import _parser

    src = content.encode("utf-8")
    try:
        tree = _parser.parse(src)
    except Exception:
        return []
    prefix = f"/api/p/{slug}"
    calls: list[dict[str, Any]] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        stack.extend(reversed(n.children))
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        if fn is None:
            continue
        line = n.start_point[0] + 1
        if fn.type == "member_expression":
            obj, prop = fn.child_by_field_name("object"), fn.child_by_field_name("property")
            if obj is not None and prop is not None and _text(obj, src) == "api":
                calls.append({"kind": "client_call", "operation": _text(prop, src), "method": None, "path": None, "line": line})
                continue
        if _text(fn, src) in ("fetch", "window.fetch") and args is not None:
            named = [c for c in args.children if c.is_named]
            if not named:
                continue
            url = _literal_value(named[0], src)
            if url is None or not (url == prefix or url.startswith(prefix + "/") or url.startswith(prefix + "?")):
                continue
            path = url[len(prefix):].split("?", 1)[0] or "/"
            calls.append({"kind": "literal", "operation": None, "method": _fetch_method(args, src), "path": path, "line": line})
    calls.sort(key=lambda c: c["line"])
    return calls


class ApiUsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _files(self, app: AppModel) -> tuple[list[tuple[str, str]], AppVersionModel | None]:
        version = await self._s.get(AppVersionModel, app.current_version_id) if app.current_version_id else None
        if version is not None:
            rows = (await self._s.execute(select(AppFileModel).where(AppFileModel.version_id == version.id))).scalars().all()
            if rows:
                return [(f.path, f.content) for f in rows if f.path != CLIENT_PATH], version
            return [("App.tsx", version.source_code or "")], version
        return ([("App.tsx", app.draft_source_code)] if app.draft_source_code else []), None

    async def _route_specs(self, project: ProjectModel) -> list[dict[str, Any]]:
        from .api_route_service import ApiRouteService, route_spec
        return [route_spec(r) for r in await ApiRouteService(self._s).rows(project.id)]

    async def _breaking_ops_since(self, project_id: str, since: datetime | None) -> set[str]:
        if since is None:
            return set()
        res = await self._s.execute(select(ApiSpecRevisionModel).where(
            ApiSpecRevisionModel.project_id == project_id, ApiSpecRevisionModel.created_at > since))
        out: set[str] = set()
        for rev in res.scalars().all():
            for c in rev.changes or []:
                if c.get("breaking"):
                    out.update(x for x in (c.get("operation"), c.get("route_id")) if x)
        return out

    async def app_usage(self, app: AppModel, project: ProjectModel | None = None,
                        specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        project = project or await self._s.get(ProjectModel, app.project_id)
        specs = specs if specs is not None else await self._route_specs(project)
        by_name = {s["name"]: s for s in specs}
        files, version = await self._files(app)
        changed = await self._breaking_ops_since(project.id, version.created_at if version else None)
        out = []
        for path, content in files:
            if not path.endswith((".tsx", ".ts", ".jsx", ".js")):
                continue
            for c in extract_api_calls(content, project.slug):
                if c["kind"] == "client_call":
                    spec = by_name.get(c["operation"])
                else:
                    spec, _, _ = match_route(specs, c["method"], c["path"].replace("{_}", "_"))
                row = {**c, "file": path, "route_id": spec["id"] if spec else None}
                if spec:
                    row["operation"] = spec["name"]
                    row["method"] = row["method"] or spec["method"]
                    row["path"] = spec["path"] if c["kind"] == "client_call" else row["path"]
                    row["status"] = "changed" if (spec["name"] in changed or spec["id"] in changed) else "ok"
                else:
                    row["status"] = "missing"
                out.append(row)
        return out

    async def project_usage(self, project: ProjectModel) -> list[dict[str, Any]]:
        specs = await self._route_specs(project)
        apps = (await self._s.execute(select(AppModel).where(AppModel.project_id == project.id))).scalars().all()
        out = []
        for a in apps:
            for row in await self.app_usage(a, project, specs):
                out.append({**row, "app_id": a.id, "app_name": a.name, "version_id": a.current_version_id})
        return out

    async def used_by(self, project: ProjectModel, route_id: str) -> list[dict[str, Any]]:
        return [{"app_id": r["app_id"], "app_name": r["app_name"], "version_id": r["version_id"], "file": r["file"],
                 "line": r["line"], "operation": r["operation"], "kind": r["kind"]}
                for r in await self.project_usage(project) if r["route_id"] == route_id]

    async def used_by_counts(self, project: ProjectModel) -> dict[str, int]:
        seen: dict[str, set[str]] = {}
        for r in await self.project_usage(project):
            if r["route_id"]:
                seen.setdefault(r["route_id"], set()).add(r["app_id"])
        return {k: len(v) for k, v in seen.items()}

    async def annotate_changes(self, project: ProjectModel, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not changes:
            return changes
        usage = await self.project_usage(project)
        for c in changes:
            c["affected"] = [{"app_id": u["app_id"], "app_name": u["app_name"], "file": u["file"], "line": u["line"]}
                             for u in usage
                             if (c.get("route_id") and u["route_id"] == c["route_id"])
                             or (c.get("operation") and u["kind"] == "client_call" and u["operation"] == c["operation"])]
        return changes

"""The API catalog service (Stage 3A, CONTRACTS-STAGE3A §6): register, list, read and delete APIs.

An API is stored as the normalised operation list (``api_specs.spec = {"operations": [...]}``) plus its
``base_url``. ``load_api_specs`` gives the run path the specs a workflow's HttpRequest nodes reference.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiSpecModel
from ..repositories.api_spec_repository import ApiSpecRepository
from .mock_gen import mock_options
from .openapi_import import SpecError, import_openapi, parse_spec_text, required_all
from .project_service import resolve_project
from .schema_inference import infer_json_schema

MAX_SPEC_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
SOURCES = ("paste", "upload", "url", "example")


class ApiCatalogError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ── views ────────────────────────────────────────────────────────────────────────────────────


def summary_of(row: ApiSpecModel) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "base_url": row.base_url,
            "operation_count": len((row.spec or {}).get("operations") or []), "source": row.source,
            "project_id": row.project_id}


def detail_of(row: ApiSpecModel) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "base_url": row.base_url, "source": row.source,
            "operations": list((row.spec or {}).get("operations") or []),
            "project_id": row.project_id, "source_url": row.source_url}


def find_operation(detail: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
    return next((o for o in detail.get("operations") or [] if o.get("operation_id") == operation_id), None)


# ── service ──────────────────────────────────────────────────────────────────────────────────


class ApiCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ApiSpecRepository(session)

    async def _project(self, ref: str):
        project = await resolve_project(self._session, ref)
        if project is None:
            raise ApiCatalogError(404, f"Project '{ref}' not found")
        return project

    async def list(self, project_id: str | None) -> list[dict[str, Any]]:
        pid = (await self._project(project_id)).id if project_id else None
        return [summary_of(r) for r in await self._repo.list(pid)]

    async def get(self, spec_id: str) -> ApiSpecModel:
        row = await self._repo.get(spec_id)
        if row is None:
            raise ApiCatalogError(404, f"API spec '{spec_id}' not found")
        return row

    async def delete(self, spec_id: str) -> None:
        if not await self._repo.delete(spec_id):
            raise ApiCatalogError(404, f"API spec '{spec_id}' not found")

    async def create(self, project_id: str, name: str | None, spec_text: str | None, url: str | None,
                     source: str | None = None) -> ApiSpecModel:
        project = await self._project(project_id)
        if bool(spec_text) == bool(url):
            raise ApiCatalogError(422, "Give either spec_text or url")
        if url:
            allow = list(((project.settings or {}).get("egress_allowlist")) or [])
            spec_text = await fetch_spec(url, allow)
            src = "url"
        else:
            if len(spec_text.encode("utf-8", "ignore")) > MAX_SPEC_BYTES:
                raise ApiCatalogError(422, "The spec is larger than 2 MB")
            src = source if source in SOURCES and source != "url" else "paste"
        try:
            imported = import_openapi(parse_spec_text(spec_text))
        except SpecError as e:
            raise ApiCatalogError(422, str(e)) from e
        return await self._repo.create(
            project_id=project.id, name=(name or "").strip() or imported["title"] or "API",
            base_url=imported["base_url"], spec={"operations": imported["operations"]}, source=src, source_url=url)

    async def create_from_example(self, project_id: str, name: str, method: str, base_url: str, path: str,
                                  status: int, example: Any) -> ApiSpecModel:
        project = await self._project(project_id)
        method = (method or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            raise ApiCatalogError(422, f"Unsupported method '{method}'")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ApiCatalogError(422, "path must start with '/'")
        schema = infer_json_schema(example)
        doc = {
            "openapi": "3.0.3", "info": {"title": name, "version": "1"},
            "servers": [{"url": base_url or ""}],
            "paths": {path: {method.lower(): {"responses": {str(status): {
                "description": "Example reply",
                "content": {"application/json": {"schema": schema, "example": example}},
            }}}}},
        }
        try:
            imported = import_openapi(doc)
        except SpecError as e:
            raise ApiCatalogError(422, str(e)) from e
        for op in imported["operations"]:  # everything the example carries is a promise of the reply
            for resp in op["responses"].values():
                if isinstance(resp.get("shape"), dict):
                    resp["shape"] = required_all(resp["shape"])
        return await self._repo.create(
            project_id=project.id, name=name, base_url=imported["base_url"],
            spec={"operations": imported["operations"]}, source="example")

    async def mocks(self, spec_id: str, operation_id: str) -> list[dict[str, Any]]:
        op = find_operation(detail_of(await self.get(spec_id)), operation_id)
        if op is None:
            raise ApiCatalogError(404, f"Operation '{operation_id}' not found in API '{spec_id}'")
        return mock_options(op)


# ── URL fetch ────────────────────────────────────────────────────────────────────────────────


def _check_host(url: str, allowlist: list[str]) -> None:
    from ..broker.policy import host_matches

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiCatalogError(422, "The spec URL must be an http(s) URL")
    if allowlist and not host_matches(parsed.hostname, allowlist):
        raise ApiCatalogError(422, f"Host '{parsed.hostname}' is not on this project's egress allowlist")


async def fetch_spec(url: str, allowlist: list[str], client: httpx.AsyncClient | None = None) -> str:
    """GET the spec text: allowlisted host on every hop, 10 s timeout, 2 MB cap."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=False)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(current, allowlist)
            try:
                async with client.stream("GET", current, headers={"Accept": "application/json, application/yaml, */*"}) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise ApiCatalogError(422, f"Fetching the spec failed: HTTP {r.status_code}")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_SPEC_BYTES:
                            raise ApiCatalogError(422, "The spec is larger than 2 MB")
                        chunks.append(chunk)
                    return b"".join(chunks).decode("utf-8", "replace")
            except httpx.TimeoutException as e:
                raise ApiCatalogError(422, "Fetching the spec timed out after 10 s") from e
            except httpx.HTTPError as e:
                raise ApiCatalogError(422, f"Fetching the spec failed: {type(e).__name__}") from e
        raise ApiCatalogError(422, "Fetching the spec failed: too many redirects")
    finally:
        if own:
            await client.aclose()


# ── run path ─────────────────────────────────────────────────────────────────────────────────


def api_refs_of(nodes: Any) -> set[str]:
    """spec ids referenced by ``api_ref`` on HttpRequest nodes (NodeDefinition objects or dicts)."""
    ids: set[str] = set()
    for n in nodes or []:
        get = (lambda k, n=n: n.get(k)) if isinstance(n, dict) else (lambda k, n=n: getattr(n, k, None))
        if get("type") != "HttpRequest":
            continue
        ref = (get("parameters") or {}).get("api_ref")
        if isinstance(ref, dict) and isinstance(ref.get("spec_id"), str) and ref["spec_id"]:
            ids.add(ref["spec_id"])
    return ids


async def load_api_specs(session: AsyncSession, nodes: Any) -> dict[str, dict[str, Any]]:
    """``{spec_id: ApiSpecDetail dict}`` for every spec the nodes reference (missing specs are absent).

    The value is ``{id, name, base_url, source, operations, project_id, source_url}``; put it in
    ``context.extras["api_specs"]`` before every run.
    """
    ids = sorted(api_refs_of(nodes))
    if not ids:
        return {}
    return {r.id: detail_of(r) for r in await ApiSpecRepository(session).get_many(ids)}


async def project_operations(session: AsyncSession, project_id: str | None) -> dict[str, dict[str, dict[str, Any]]]:
    """``{spec_id: {operation_id: Operation}}`` for the checker's lookups."""
    if not project_id:
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in await ApiSpecRepository(session).list(project_id):
        out[r.id] = {o["operation_id"]: o for o in (r.spec or {}).get("operations") or []}
    return out

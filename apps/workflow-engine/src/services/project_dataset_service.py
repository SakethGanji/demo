"""Project datasets: files (CSV, Parquet, XLSX, XLS) stored by the analytics service, linked to a project.

The analytics service owns the bytes, the versions and the compute (sample / profile / aggregate) and knows nothing
about projects. The engine keeps one ``project_datasets`` row per import: its own UUIDv7 ``id`` (what a workflow node
stores), the project's name for the dataset, the analytics ``dataset_id`` / ``version_id`` it points at and a copy of
the shape (rows, columns, size) so a list needs no round trip.

The two databases are separate (no foreign key; see ``ProjectDatasetModel``), so this service keeps them in step:

* upload: if the link insert fails after analytics accepted the file, the analytics dataset is deleted again;
* delete: analytics first (a 404 there is fine); if that fails the link stays, so nothing is half-deleted;
* drift: a link whose analytics dataset is gone reads as ``missing: true`` (preview) or 409 (profile).

``resolve_dataset_ref`` turns a node's ``datasetId`` value into the analytics UUID within the run's project.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import PurePath
from typing import IO, Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.models import DEFAULT_PROJECT_ID, ProjectDatasetModel, ProjectModel
from ..utils.ids import project_dataset_id
from .project_service import ProjectError

FORMATS = ("csv", "parquet", "xlsx", "xls")
PREVIEW_ROWS = 20
MAX_NAME = 200
SUFFIX_RE = re.compile(r"^(.*) \((\d+)\)$")
UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=5.0)
CALL_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=60.0, pool=5.0)

#: tests swap in an ``httpx.MockTransport``; None = the real network
ANALYTICS_TRANSPORT: httpx.AsyncBaseTransport | None = None


def analytics_url() -> str:
    """The analytics service base URL (settings field, env ``ANALYTICS_SERVICE_URL``), read at call time."""
    return settings.analytics_service_url.rstrip("/")


def as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def is_uuid(value: Any) -> bool:
    return as_uuid(value) is not None


def dataset_to_dict(d: ProjectDatasetModel, missing: bool = False) -> dict[str, Any]:
    return {
        "id": str(d.id), "dataset_id": str(d.dataset_id), "version_id": str(d.version_id) if d.version_id else None,
        "name": d.name, "file_name": d.file_name, "format": d.format,
        "row_count": d.row_count, "column_count": d.column_count, "size_bytes": d.size_bytes,
        "columns": list(d.columns or []), "missing": missing,
        "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat(),
    }


def file_format(file_name: str) -> str:
    ext = PurePath(file_name or "").suffix.lower().lstrip(".")
    if ext not in FORMATS:
        shown = f"'.{ext}'" if ext else "a file without an extension"
        raise ProjectError(400, "unsupported_format",
                           f"Can't import {shown}. Choose a CSV, Parquet or Excel (.xlsx, .xls) file.")
    return ext


def clean_name(name: str | None) -> str:
    n = " ".join(str(name or "").split())
    if not n:
        raise ProjectError(422, "invalid_name", "A dataset needs a name.")
    if len(n) > MAX_NAME:
        raise ProjectError(422, "invalid_name", f"A dataset name can be at most {MAX_NAME} characters.")
    return n


class AnalyticsUnavailable(Exception):
    """The analytics service could not be reached (the route answers 503 with a string detail)."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"The analytics service isn't reachable at {url}. Start it to import datasets.")


def _analytics_error(resp: httpx.Response) -> ProjectError:
    """A non-2xx analytics answer as a 502 that carries the service's own message."""
    msg: Any = None
    try:
        body = resp.json()
        msg = body.get("detail") if isinstance(body, dict) else None
    except ValueError:
        pass
    if not isinstance(msg, str):
        msg = resp.text.strip() if not msg else str(msg)
    return ProjectError(502, "analytics_error", f"The analytics service refused the request ({resp.status_code}): {msg or 'no message'}",
                        analytics_status=resp.status_code)


def _analytics_404(e: ProjectError) -> bool:
    return e.code == "analytics_error" and e.extra.get("analytics_status") == 404


class ProjectDatasetService:
    def __init__(self, session: AsyncSession, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._s = session
        self._transport = transport if transport is not None else ANALYTICS_TRANSPORT

    # ── analytics calls ──────────────────────────────────────────────────

    async def _call(self, method: str, path: str, *, timeout: httpx.Timeout = CALL_TIMEOUT, **kw: Any) -> httpx.Response:
        base = analytics_url()
        try:
            async with httpx.AsyncClient(base_url=base, timeout=timeout, transport=self._transport) as c:
                return await c.request(method, path, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise AnalyticsUnavailable(base) from e
        except httpx.TimeoutException as e:
            raise ProjectError(504, "analytics_timeout", f"The analytics service at {base} took too long to answer.") from e
        except httpx.TransportError as e:
            raise AnalyticsUnavailable(base) from e

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self._call(method, path, **kw)
        if resp.status_code >= 400:
            raise _analytics_error(resp)
        return resp.json()

    async def _imported_version(self, dataset_id: str, upload: dict[str, Any]) -> str | None:
        """The analytics version the upload created. A synchronous upload's answer has no ``version_id``, so ask for the
        dataset's versions; best effort (a link without a version still works: analytics reads the current one)."""
        if is_uuid(upload.get("version_id")):
            return str(upload["version_id"])
        try:
            versions = await self._json("GET", f"/datasets/{dataset_id}/versions")
        except (ProjectError, AnalyticsUnavailable):
            return None
        if not isinstance(versions, list) or not versions:
            return None
        latest = max(versions, key=lambda v: v.get("version_number") or 0)
        return str(latest["id"]) if is_uuid(latest.get("id")) else None

    # ── lookups ──────────────────────────────────────────────────────────

    async def list(self, project: ProjectModel) -> list[dict[str, Any]]:
        """Newest first. ``missing`` is always false here: checking analytics per row would make the list slow."""
        rows = (await self._s.execute(select(ProjectDatasetModel).where(ProjectDatasetModel.project_id == project.id)
                                      .order_by(ProjectDatasetModel.created_at.desc(), ProjectDatasetModel.id.desc()))).scalars().all()
        return [dataset_to_dict(d) for d in rows]

    async def get(self, project: ProjectModel, did: str) -> ProjectDatasetModel:
        d = await self._s.get(ProjectDatasetModel, str(did)) if is_uuid(did) else None  # a non-UUID can't name a link
        if not d or d.project_id != project.id:
            raise ProjectError(404, "dataset_not_found", "Dataset not found")
        return d

    async def by_name(self, project_id: str, name: str) -> ProjectDatasetModel | None:
        return (await self._s.execute(select(ProjectDatasetModel).where(
            ProjectDatasetModel.project_id == project_id, ProjectDatasetModel.name == name))).scalars().first()

    async def by_dataset_id(self, project_id: str, dataset_id: str) -> ProjectDatasetModel | None:
        return (await self._s.execute(select(ProjectDatasetModel).where(
            ProjectDatasetModel.project_id == project_id, ProjectDatasetModel.dataset_id == dataset_id))).scalars().first()

    async def free_name(self, project_id: str, wanted: str) -> str:
        """``wanted`` if no dataset in the project has it, else the first free ``wanted (n)``."""
        names = set((await self._s.execute(select(ProjectDatasetModel.name).where(
            ProjectDatasetModel.project_id == project_id))).scalars().all())
        if wanted not in names:
            return wanted
        m = SUFFIX_RE.match(wanted)
        stem = m.group(1) if m else wanted
        n = 2
        while f"{stem} ({n})" in names:
            n += 1
        return f"{stem} ({n})"

    # ── import / rename / delete ─────────────────────────────────────────

    async def upload(self, project: ProjectModel, file_name: str, fileobj: IO[bytes], content_type: str | None = None,
                     name: str | None = None, actor: str | None = None) -> dict[str, Any]:
        fmt = file_format(file_name)
        wanted = clean_name(name if name and name.strip() else PurePath(file_name).stem)
        # httpx streams a file object in chunks, so a large upload is never held in memory here
        resp = await self._call("POST", "/files/upload", params={"sync": "true"}, timeout=UPLOAD_TIMEOUT,
                                files={"file": (PurePath(file_name).name, fileobj, content_type or "application/octet-stream")})
        if resp.status_code >= 400:
            raise _analytics_error(resp)
        up = resp.json()
        dataset_id = str(up.get("dataset_id") or "")
        if not is_uuid(dataset_id):
            raise ProjectError(502, "analytics_error", "The analytics service accepted the file but returned no dataset id.")
        # from here on analytics holds a dataset: any failure before the link is committed must delete it again
        try:
            return await self._insert_link(project, up, dataset_id, wanted, file_name, fmt, actor)
        except BaseException:
            await self._forget_in_analytics(dataset_id)
            raise

    async def _insert_link(self, project: ProjectModel, up: dict[str, Any], dataset_id: str, wanted: str, file_name: str,
                           fmt: str, actor: str | None) -> dict[str, Any]:
        version_id = await self._imported_version(dataset_id, up)
        cols = [{"name": str(c.get("name")), "type": str(c.get("dtype") or c.get("type") or "")} for c in up.get("columns") or []]
        now = datetime.now()
        team_id, project_id = project.team_id, project.id  # a rollback below expires ``project``; don't reload it
        for _ in range(5):  # a concurrent import may take the free name between the check and the insert
            d = ProjectDatasetModel(
                id=project_dataset_id(), team_id=team_id, project_id=project_id, dataset_id=dataset_id,
                version_id=version_id, name=await self.free_name(project_id, wanted), file_name=PurePath(file_name).name,
                format=fmt, row_count=up.get("row_count"),
                column_count=up.get("column_count") if up.get("column_count") is not None else len(cols),
                size_bytes=up.get("file_size_bytes"), columns=cols, created_by=actor, created_at=now, updated_at=now)
            self._s.add(d)
            try:
                await self._s.commit()
                return dataset_to_dict(d)
            except IntegrityError:
                await self._s.rollback()
        raise ProjectError(409, "name_taken", f"Could not find a free name for '{wanted}'; try again.")

    async def rename(self, project: ProjectModel, did: str, name: str | None) -> dict[str, Any]:
        d = await self.get(project, did)
        new = clean_name(name)
        if new != d.name:
            other = await self.by_name(project.id, new)
            if other and other.id != d.id:
                raise ProjectError(409, "name_taken", f"A dataset named '{new}' already exists in this project.")
            d.name = new
            d.updated_at = datetime.now()
            try:
                await self._s.commit()
            except IntegrityError:
                await self._s.rollback()
                raise ProjectError(409, "name_taken", f"A dataset named '{new}' already exists in this project.")
        return dataset_to_dict(d)

    async def delete(self, project: ProjectModel, did: str) -> None:
        """Analytics first, then the link. A 404 there means it is already gone; any other failure (5xx -> 502,
        unreachable -> 503) leaves the link in place so the user can retry and nothing is half-deleted."""
        d = await self.get(project, did)
        resp = await self._call("DELETE", f"/datasets/{d.dataset_id}")
        if resp.status_code >= 400 and resp.status_code != 404:
            raise _analytics_error(resp)
        await self._s.delete(d)
        await self._s.commit()

    async def _forget_in_analytics(self, dataset_id: str) -> None:
        try:
            await self._call("DELETE", f"/datasets/{dataset_id}")
        except Exception:  # best effort: the import already failed for another reason, which is what gets raised
            pass

    # ── reads served by analytics ────────────────────────────────────────

    async def detail(self, d: ProjectDatasetModel, limit: int = PREVIEW_ROWS) -> dict[str, Any]:
        """The item plus up to ``limit`` preview rows; ``missing: true`` and no rows when analytics no longer has it."""
        try:
            return {**dataset_to_dict(d), "preview": await self.preview(d, limit)}
        except ProjectError as e:
            if _analytics_404(e):
                return {**dataset_to_dict(d, missing=True), "preview": []}
            raise

    async def preview(self, d: ProjectDatasetModel, limit: int = PREVIEW_ROWS) -> list[dict[str, Any]]:
        """Up to ``limit`` rows in column order: a systematic sample (every n-th row; the first rows of a small file)."""
        body = await self._json("POST", "/sample", json={
            "dataset_id": str(d.dataset_id), "target_total_volume": limit, "return_data": True,
            "sampling_steps": [{"method": "systematic", "sample_size": limit}],
        })
        rows = body.get("data") if isinstance(body, dict) else None
        if rows is None:
            rows = body.get("preview") if isinstance(body, dict) else None
        return list(rows or [])[:limit]


# ── resolution for nodes ─────────────────────────────────────────────────


class DatasetNotFound(ValueError):
    """A node named a dataset the project does not have."""


async def resolve_dataset_ref(session: AsyncSession, project_id: str, ref: Any) -> str:
    """A node's ``datasetId`` value -> the analytics dataset UUID, within ``project_id``. In order:

    1. a link ``id`` of the project (what the studio picker saves) -> that link's ``dataset_id``;
    2. the ``dataset_id`` of one of the project's links (legacy values) -> itself;
    3. any other UUID -> passed through unchanged to analytics. This keeps workflows saved before project datasets
       existed (they hold raw analytics ids) working. The exception is a UUIDv7: link ids are v7 and analytics ids
       are v4 (``gen_random_uuid``), so an unmatched v7 is a link that was deleted;
    4. anything else is a dataset name in the project (expressions, the AI agent).
    """
    value = str(ref or "").strip()
    if not value:
        raise DatasetNotFound("Choose a dataset: no dataset is selected.")
    u = as_uuid(value)
    svc = ProjectDatasetService(session)
    if u is not None:
        key = str(u)
        link = await session.get(ProjectDatasetModel, key)
        if link is not None and link.project_id == project_id:
            return str(link.dataset_id)
        legacy = await svc.by_dataset_id(project_id, key)
        if legacy is not None:
            return str(legacy.dataset_id)
        if u.version == 7:
            raise DatasetNotFound("This dataset was deleted from the project.")
        return value  # pre-feature workflows: a raw analytics dataset id
    d = await svc.by_name(project_id, value)
    if d is None:
        raise DatasetNotFound(f"No dataset named '{value}' in this project.")
    return str(d.dataset_id)


async def resolve_dataset_for_run(project_id: str | None, ref: Any) -> str:
    """``resolve_dataset_ref`` in its own session, scoped to the run's project (the Default project for unscoped runs)."""
    from ..db import session as db_session  # the factory is looked up at call time: tests swap it

    async with db_session.async_session_factory() as s:
        return await resolve_dataset_ref(s, project_id or DEFAULT_PROJECT_ID, ref)

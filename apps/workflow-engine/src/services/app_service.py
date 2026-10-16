"""Service layer for app CRUD operations + publishing pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from ..db.models import AppModel, AppVersionModel
from ..schemas.app import (
    AppCreateRequest,
    AppDetailResponse,
    AppFilePayload,
    AppListItem,
    AppPublishRequest,
    AppPublishResponse,
    AppUpdateRequest,
    AppVersionDetail,
    AppVersionListItem,
    AppVersionResponse,
)
from ..services.app_bundler import AppFileInput, BundleBuildError, BundlerUnavailableError, bundle_app
from ..services.bundle_storage import BundleStorageBackend
from ..services.file_storage import FileStorageBackend
from ..services.slug_utils import (
    SlugValidationError,
    normalize_slug,
    validate_slug,
)
from ..utils.ids import app_id


# Argon2 hasher with library defaults — argon2-cffi picks sensible parameters.
# We re-use a single instance because hasher state is internal config only.
_password_hasher = PasswordHasher()


class AppPublishError(RuntimeError):
    """Raised when a publish request can't be satisfied (bad slug, no source,
    bundler down, etc). The message is safe to surface to the caller."""


class SlugTakenError(AppPublishError):
    """Review C4: the slug belongs to another app → 409 {"detail":{"code":"slug_taken"}}."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"slug '{slug}' is already in use")
        self.slug = slug

    def detail(self) -> dict:
        return {"code": "slug_taken", "message": str(self), "slug": self.slug}


class AppValidationError(ValueError):
    def __init__(self, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def rotate_token_key(row: AppModel) -> str:
    """Review D3: the published-app token rotation key lives in ``apps.settings.token_key``."""
    import secrets as _secrets
    from sqlalchemy.orm.attributes import flag_modified

    key = _secrets.token_urlsafe(16)
    row.settings = {**(row.settings or {}), "token_key": key}
    try:
        flag_modified(row, "settings")
    except Exception:
        pass
    return key


class AppService:
    """Handles app persistence using the dedicated apps table."""

    def __init__(
        self,
        session: AsyncSession,
        file_storage: FileStorageBackend,
        bundle_storage: BundleStorageBackend,
    ) -> None:
        self._session = session
        self._file_storage = file_storage
        self._bundle_storage = bundle_storage

    # ── List / Get / Create / Update / Delete ────────────────────────────────

    async def list_apps(self, folder_id: str | None = None, project_id: str | None = None) -> list[AppListItem]:
        stmt = select(AppModel)
        if folder_id is not None:
            stmt = stmt.where(AppModel.folder_id == folder_id)
        if project_id is not None:
            stmt = stmt.where(AppModel.project_id == project_id)
        stmt = stmt.order_by(AppModel.updated_at.desc())
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        vids = [r.current_version_id for r in rows if r.current_version_id]
        numbers: dict[int, int] = {}
        if vids:
            vres = await self._session.execute(
                select(AppVersionModel.id, AppVersionModel.version_number).where(AppVersionModel.id.in_(vids)))
            numbers = {vid: n for vid, n in vres.all()}
        return [
            AppListItem(
                id=r.id,
                name=r.name,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
                project_id=r.project_id,
                slug=r.slug,
                active=r.active,
                published_at=r.published_at.isoformat() if r.published_at else None,
                current_version_number=numbers.get(r.current_version_id) if r.current_version_id else None,
            )
            for r in rows
        ]

    async def get_app(self, aid: str) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        return await self._to_detail(row)

    async def get_app_by_slug(self, slug: str) -> AppModel | None:
        """Internal: fetch the app row by slug (case-insensitive). Used by
        public routes to resolve /a/{slug} to a database app + published version.
        Returns the raw model so the route can read access/published_version_id
        without an extra round trip."""
        stmt = select(AppModel).where(func.lower(AppModel.slug) == slug.lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _project_id(self, ref: str | None) -> str | None:
        if not ref:
            return None
        from .project_service import resolve_project
        p = await resolve_project(self._session, ref)
        if not p:
            raise AppPublishError(f"project '{ref}' not found")
        return p.id

    async def check_workflow_ids(self, project_id: str, workflow_ids: list[str] | None) -> None:
        """G22: allow-listed workflows that exist must belong to the app's project."""
        if not workflow_ids:
            return
        from ..db.models import WorkflowModel
        res = await self._session.execute(
            select(WorkflowModel.id, WorkflowModel.project_id).where(WorkflowModel.id.in_(list(workflow_ids))))
        bad = sorted(wid for wid, pid in res.all() if pid != project_id)
        if bad:
            raise AppValidationError("workflow_not_in_project",
                                     "workflow_ids must belong to the app's project", workflow_ids=bad)

    async def create_app(self, req: AppCreateRequest, created_by: str | None = None) -> AppDetailResponse:
        now = datetime.now()
        project_id = await self._project_id(getattr(req, "project_id", None))
        await self.check_workflow_ids(project_id or "proj_default", req.workflow_ids)
        row = AppModel(
            id=app_id(),
            name=req.name,
            description=req.description,
            folder_id=req.folder_id,
            workflow_ids=req.workflow_ids,
            api_execution_ids=req.api_execution_ids,
            active=False,
            draft_definition=req.definition,
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )
        if project_id:
            row.project_id = project_id
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return await self._to_detail(row)

    async def update_app(self, aid: str, req: AppUpdateRequest) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        if req.name is not None:
            row.name = req.name
        if getattr(req, "project_id", None):
            row.project_id = await self._project_id(req.project_id)
        if req.workflow_ids is not None or getattr(req, "project_id", None):
            await self.check_workflow_ids(
                row.project_id, req.workflow_ids if req.workflow_ids is not None else list(row.workflow_ids or []))
        if req.definition is not None:
            row.draft_definition = req.definition
        if req.description is not None:
            row.description = req.description
        if req.workflow_ids is not None:
            row.workflow_ids = req.workflow_ids
        if req.api_execution_ids is not None:
            row.api_execution_ids = req.api_execution_ids
        if req.source_code is not None:
            row.draft_source_code = req.source_code
        if req.embed_enabled is not None:
            row.embed_enabled = req.embed_enabled

        # Publishing settings — applied here so the studio can edit slug/access
        # before the user actually clicks "publish".
        if req.slug is not None:
            await self._apply_slug(row, req.slug)
        if req.access is not None:
            await self._apply_access(row, req.access, req.access_password)
        elif req.access_password is not None:
            # Setting/clearing the password without changing access mode.
            await self._apply_access(row, row.access, req.access_password)

        if getattr(req, "token_ttl_s", None) is not None:  # test-only knob (review D3), honoured in test mode
            row.settings = {**(row.settings or {}), "token_ttl_s": int(req.token_ttl_s)}

        row.updated_at = datetime.now()

        # Atomically create a version alongside the save
        version = None
        if req.create_version and req.source_code is not None:
            files_dicts = [f.model_dump() for f in req.files] if req.files else None
            version = await self._create_version_row(
                row,
                source_code=req.source_code,
                trigger=req.version_trigger,
                prompt=req.version_prompt,
                files=files_dicts,
            )

        await self._session.commit()
        await self._session.refresh(row)
        if version:
            await self._session.refresh(version)
        return await self._to_detail(row)

    async def delete_app(self, aid: str) -> bool:
        row = await self._session.get(AppModel, aid)
        if not row:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def grant_api_executions(self, aid: str, execution_ids: list[str]) -> None:
        """Idempotently append API tester execution ids to an app's allow-list.

        Called when the app builder chat receives `api_execution_ids` for an
        existing app, so that the published page is permitted to replay them.
        """
        if not execution_ids:
            return
        row = await self._session.get(AppModel, aid)
        if not row:
            return
        existing = list(row.api_execution_ids or [])
        seen = set(existing)
        added = False
        for eid in execution_ids:
            if eid not in seen:
                existing.append(eid)
                seen.add(eid)
                added = True
        if added:
            row.api_execution_ids = existing
            row.updated_at = datetime.now()
            await self._session.commit()

    async def publish_app(
        self, aid: str, req: AppPublishRequest | None = None
    ) -> AppPublishResponse:
        """Publish an app: snapshot source as a new version, server-side bundle
        it, persist the artifact, flip published_version_id and active.

        Raises AppPublishError on slug conflicts, missing source, or bundler
        failure. The 404 case (app missing) is surfaced via raising too — the
        route turns it into an HTTP 404."""
        row = await self._session.get(AppModel, aid)
        if not row:
            raise AppPublishError("app not found")

        req = req or AppPublishRequest()
        if req.slug is not None:
            await self._apply_slug(row, req.slug)
        if req.access is not None:
            await self._apply_access(row, req.access, req.access_password)
        elif req.access_password is not None:
            await self._apply_access(row, row.access, req.access_password)

        # If the app still has no slug, derive one from the name. We try the
        # normalized name first, fall back with random suffixes on conflict.
        if not row.slug:
            row.slug = await self._unique_slug_from_name(row.name)

        source = row.draft_source_code or ""
        if not source.strip():
            raise AppPublishError("app has no source to publish")

        # Bundle from the current version's files if available; fall back to
        # the single draft source. Files give us a richer entry-point search.
        files_for_bundle = await self._files_for_publish(row)

        try:
            artifact = await bundle_app(files_for_bundle)
        except BundlerUnavailableError as e:
            raise AppPublishError(f"bundler unavailable: {e}") from e
        except BundleBuildError as e:
            # Surface the esbuild stderr — actionable for the user.
            detail = f"bundle failed: {e}"
            if e.stderr:
                detail = f"{detail}\n{e.stderr.strip()}"
            raise AppPublishError(detail) from e

        # If the current version's source matches what we're publishing,
        # promote it directly — no phantom version row. Only snapshot when
        # the draft has changes the current version doesn't already capture.
        # The "live" badge in version history is what surfaces publish events
        # now, so we don't need a separate trigger='publish' row.
        target_version: AppVersionModel | None = None
        if row.current_version_id:
            cur = await self._session.get(AppVersionModel, row.current_version_id)
            if cur is not None and cur.source_code == source:
                target_version = cur

        if target_version is None:
            target_version = await self._create_version_row(
                row,
                source_code=source,
                trigger="publish",
            )
            await self._session.flush()  # populate version.id

        await self._bundle_storage.save_bundle(target_version.id, artifact)  # type: ignore[arg-type]

        row.published_version_id = target_version.id
        row.active = True
        row.published_at = datetime.now()
        row.updated_at = datetime.now()
        await self._session.commit()

        return AppPublishResponse(
            id=aid,
            active=True,
            version_id=target_version.id,
            slug=row.slug,
            bundle_hash=artifact.hash,
            public_url=None,  # route layer fills this with the absolute URL
        )

    async def publish_release_version(self, aid: str, version_id: int, client_src: str | None) -> bool:
        """Phase 9: serve a release-pinned app version at /a/{slug} (promote/rollback into the live env).
        Only for apps that are already published (slug + active). Returns True if re-published."""
        from .client_codegen import CLIENT_PATH
        row = await self._session.get(AppModel, aid)
        version = await self._session.get(AppVersionModel, version_id)
        if not row or not version or version.app_id != aid or not row.slug or not row.active:
            return False
        file_dicts = await self._file_storage.get_files(version_id)
        files = [AppFileInput(path=f["path"], content=f["content"]) for f in file_dicts if f["path"] != CLIENT_PATH] \
            or [AppFileInput(path="App.tsx", content=version.source_code or "")]
        if client_src:
            files.append(AppFileInput(path=CLIENT_PATH, content=client_src))
        try:
            artifact = await bundle_app(files)
        except (BundlerUnavailableError, BundleBuildError) as e:
            raise AppPublishError(f"bundle failed: {e}") from e
        await self._bundle_storage.save_bundle(version_id, artifact)
        row.published_version_id = version_id
        row.published_at = datetime.now()
        # The release service owns the transaction: app pointers and the active
        # backend release must become visible together, or all roll back.
        await self._session.flush()
        return True

    async def ensure_token_key(self, row: AppModel) -> str:
        """The app's current token rotation key, created on first use (review D3)."""
        key = (row.settings or {}).get("token_key")
        if not key:
            key = rotate_token_key(row)
            await self._session.commit()
        return key

    async def unpublish_app(self, aid: str) -> AppPublishResponse:
        """Take a published app offline. Bundle bytes stay on the version row
        (re-publish is then a no-op rebuild) and history is preserved — we
        only flip the markers that drive the public surface."""
        row = await self._session.get(AppModel, aid)
        if not row:
            raise AppPublishError("app not found")
        row.active = False
        row.published_version_id = None
        row.published_at = None
        rotate_token_key(row)  # review D3: tokens of the unpublished app die even if it is re-published
        row.updated_at = datetime.now()
        await self._session.commit()
        return AppPublishResponse(
            id=aid,
            active=False,
            version_id=None,
            slug=row.slug,
            bundle_hash=None,
            public_url=None,
        )

    # ── Public-side lookups ─────────────────────────────────────────────────

    async def get_published_version_response(
        self, version_id: int | None
    ) -> AppVersionResponse | None:
        """Public read of a version row, formatted for HTTP responses."""
        return await self._version_response(version_id)

    async def get_published_asset(
        self, version_id: int, kind: str, expected_hash: str
    ):
        """Proxy to the bundle storage backend for the public asset routes.
        Returns BundleAsset | None — caller turns None into 404."""
        return await self._bundle_storage.get_asset(version_id, kind, expected_hash)

    async def verify_app_password(self, app: AppModel, password: str) -> bool:
        """Constant-time check of plaintext against stored argon2 hash."""
        if not app.access_password_hash:
            return False
        try:
            _password_hasher.verify(app.access_password_hash, password)
            return True
        except VerifyMismatchError:
            return False
        except Exception:
            return False

    # ── Version CRUD ─────────────────────────────────────────────────────────

    async def create_version(
        self,
        aid: str,
        source_code: str,
        trigger: str = "manual",
        label: str | None = None,
        prompt: str | None = None,
        message: str | None = None,
    ) -> AppVersionDetail | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None
        version = await self._create_version_row(
            row,
            source_code=source_code,
            trigger=trigger,
            label=label,
            prompt=prompt,
            message=message,
        )
        await self._session.commit()
        await self._session.refresh(version)
        return self._version_to_detail(version)

    async def list_versions(self, aid: str) -> list[AppVersionListItem]:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid)
            .order_by(col(AppVersionModel.version_number).asc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            AppVersionListItem(
                id=v.id,  # type: ignore[arg-type]
                version_number=v.version_number,
                parent_version_id=v.parent_version_id,
                trigger=v.trigger,
                label=v.label,
                prompt=v.prompt,
                message=v.message,
                created_at=v.created_at.isoformat(),
            )
            for v in rows
        ]

    async def get_version(self, aid: str, version_id: int) -> AppVersionDetail | None:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None
        detail = self._version_to_detail(version)
        # Populate files
        file_dicts = await self._file_storage.get_files(version.id)  # type: ignore[arg-type]
        detail.files = [AppFilePayload(**f) for f in file_dicts]
        return detail

    async def get_version_files(self, aid: str, version_id: int) -> list[dict] | None:
        """Get files for a specific version."""
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None

        return await self._file_storage.get_files(version.id)  # type: ignore[arg-type]

    async def revert_to_version(self, aid: str, version_id: int) -> AppDetailResponse | None:
        row = await self._session.get(AppModel, aid)
        if not row:
            return None

        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None

        row.current_version_id = version.id
        row.draft_source_code = version.source_code
        row.updated_at = datetime.now()
        await self._session.commit()
        await self._session.refresh(row)
        return await self._to_detail(row)

    async def update_version_label(
        self, aid: str, version_id: int, label: str | None
    ) -> AppVersionListItem | None:
        stmt = (
            select(AppVersionModel)
            .where(AppVersionModel.app_id == aid, AppVersionModel.id == version_id)
        )
        result = await self._session.execute(stmt)
        version = result.scalar_one_or_none()
        if not version:
            return None
        version.label = label
        await self._session.commit()
        await self._session.refresh(version)
        return AppVersionListItem(
            id=version.id,  # type: ignore[arg-type]
            version_number=version.version_number,
            parent_version_id=version.parent_version_id,
            trigger=version.trigger,
            label=version.label,
            prompt=version.prompt,
            message=version.message,
            created_at=version.created_at.isoformat(),
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _apply_slug(self, row: AppModel, slug: str) -> None:
        slug = slug.strip().lower()
        try:
            validate_slug(slug)
        except SlugValidationError as e:
            raise AppPublishError(str(e)) from e
        if row.slug == slug:
            return
        if await self._slug_exists(slug, exclude_id=row.id):
            raise SlugTakenError(slug)
        row.slug = slug

    async def _apply_access(
        self, row: AppModel, access: str, password: str | None
    ) -> None:
        if access not in {"private", "public", "password"}:
            raise AppPublishError(f"invalid access mode '{access}'")

        if access == "password":
            # Set or keep an existing password. Empty string is rejected to
            # avoid accidentally locking with a known-blank value.
            if password is not None:
                if not password:
                    raise AppPublishError("password may not be empty")
                row.access_password_hash = _password_hasher.hash(password)
                rotate_token_key(row)  # a new password invalidates outstanding page tokens
            elif not row.access_password_hash:
                raise AppPublishError(
                    "access='password' requires access_password to be set"
                )
        else:
            # Switching off password mode clears any previously-set hash, so we
            # don't carry around stale credentials.
            if password == "":
                row.access_password_hash = None
            if access == "private" and row.access != "private":
                rotate_token_key(row)

        row.access = access

    async def _slug_exists(self, slug: str, exclude_id: str | None = None) -> bool:
        stmt = select(AppModel.id).where(func.lower(AppModel.slug) == slug.lower())
        if exclude_id is not None:
            stmt = stmt.where(AppModel.id != exclude_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _unique_slug_from_name(self, name: str) -> str:
        base = normalize_slug(name)
        try:
            validate_slug(base)
        except SlugValidationError:
            base = normalize_slug(f"app-{name}")
        candidate = base
        for _ in range(8):
            if not await self._slug_exists(candidate):
                return candidate
            candidate = normalize_slug(f"{base}-{datetime.now().microsecond:06d}")
        raise AppPublishError("could not allocate unique slug — please set one explicitly")

    async def _files_for_publish(self, row: AppModel) -> list[AppFileInput]:
        """Resolve the file list to bundle. Prefer current_version_id files
        (richer multi-file structure); fall back to the single draft source.
        The project's typed client (api/client.ts) is always regenerated (phase 6, G18)."""
        files: list[AppFileInput] | None = None
        if row.current_version_id:
            file_dicts = await self._file_storage.get_files(row.current_version_id)
            if file_dicts:
                files = [AppFileInput(path=f["path"], content=f["content"]) for f in file_dicts]
        if files is None:
            files = [AppFileInput(path="App.tsx", content=row.draft_source_code or "")]
        client = await self.project_client(row, live=True)
        if client is not None:
            from .client_codegen import CLIENT_PATH
            files = [f for f in files if f.path != CLIENT_PATH] + [AppFileInput(path=CLIENT_PATH, content=client)]
        return files

    async def project_client(self, row: AppModel, live: bool = False) -> str | None:
        """Generated client for the app's project (None when the project has no routes)."""
        from ..db.models import ApiRouteModel, ProjectModel
        project = await self._session.get(ProjectModel, row.project_id)
        if project is None:
            return None
        n = (await self._session.execute(select(func.count()).select_from(ApiRouteModel).where(
            ApiRouteModel.project_id == project.id))).scalar_one()
        if not n:
            return None
        from ..routes.api_routes import client_source
        src, _ = await client_source(self._session, project, project.live_environment if live else project.default_environment)
        return src

    async def _next_version_number(self, aid: str) -> int:
        stmt = (
            select(AppVersionModel.version_number)
            .where(AppVersionModel.app_id == aid)
            .order_by(col(AppVersionModel.version_number).desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        last = result.scalar_one_or_none()
        return (last or 0) + 1

    async def _create_version_row(
        self,
        app: AppModel,
        *,
        source_code: str,
        trigger: str,
        label: str | None = None,
        prompt: str | None = None,
        message: str | None = None,
        files: list[dict[str, str]] | None = None,
    ) -> AppVersionModel:
        next_num = await self._next_version_number(app.id)
        version = AppVersionModel(
            app_id=app.id,
            version_number=next_num,
            parent_version_id=app.current_version_id,
            definition=app.draft_definition or {},
            source_code=source_code,
            trigger=trigger,
            label=label,
            prompt=prompt,
            message=message,
        )
        self._session.add(version)
        await self._session.flush()  # get version.id

        if files:
            await self._file_storage.save_files(version.id, files)  # type: ignore[arg-type]

        app.current_version_id = version.id
        app.draft_source_code = source_code
        return version

    async def _version_response(self, version_id: int | None) -> AppVersionResponse | None:
        if not version_id:
            return None
        version = await self._session.get(AppVersionModel, version_id)
        if not version:
            return None
        return AppVersionResponse(
            id=version.id,  # type: ignore[arg-type]
            version_number=version.version_number,
            parent_version_id=version.parent_version_id,
            trigger=version.trigger,
            label=version.label,
            prompt=version.prompt,
            message=version.message,
            created_at=version.created_at.isoformat(),
            bundle_hash=version.bundle_hash,
            bundled_at=version.bundled_at.isoformat() if version.bundled_at else None,
        )

    async def _to_detail(self, row: AppModel) -> AppDetailResponse:
        current_version = await self._version_response(row.current_version_id)
        published_version = await self._version_response(row.published_version_id)
        files: list[AppFilePayload] = []
        if row.current_version_id:
            file_dicts = await self._file_storage.get_files(row.current_version_id)
            files = [AppFilePayload(**f) for f in file_dicts]
        return AppDetailResponse(
            id=row.id,
            name=row.name,
            description=row.description,
            definition=row.draft_definition or {},
            active=row.active,
            project_id=row.project_id,
            workflow_ids=row.workflow_ids or [],
            api_execution_ids=row.api_execution_ids or [],
            source_code=row.draft_source_code,
            files=files,
            current_version=current_version,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
            slug=row.slug,
            access=row.access,  # type: ignore[arg-type]
            access_password_set=bool(row.access_password_hash),
            embed_enabled=row.embed_enabled,
            published_at=row.published_at.isoformat() if row.published_at else None,
            published_version=published_version,
        )

    @staticmethod
    def _version_to_detail(v: AppVersionModel) -> AppVersionDetail:
        return AppVersionDetail(
            id=v.id,  # type: ignore[arg-type]
            version_number=v.version_number,
            parent_version_id=v.parent_version_id,
            trigger=v.trigger,
            label=v.label,
            prompt=v.prompt,
            message=v.message,
            source_code=v.source_code,
            created_at=v.created_at.isoformat(),
            bundle_hash=v.bundle_hash,
            bundled_at=v.bundled_at.isoformat() if v.bundled_at else None,
        )

"""Bundle storage abstraction for published apps.

The bundle (compiled JS + CSS for a published app version) is the artifact
served to public visitors. We keep the storage interface narrow so the
backend can be swapped — Postgres TEXT today, S3 / object storage later —
without touching the service layer or HTTP routes.

Backends are keyed by `version_id` because every published version owns
exactly one bundle, and we already index `app_versions` heavily.

Future S3 backend will:
  * Write JS/CSS as `apps/{app_id}/versions/{version_id}/{hash}.{js,css}`
  * Stash only the small metadata (hash, bundled_at, sizes) on the version row
  * Implement the same Protocol so callers don't change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AppVersionModel


@dataclass(frozen=True)
class BundleArtifact:
    """The compiled artifact for one published app version."""

    js: str
    css: str
    hash: str
    bundled_at: datetime


@dataclass(frozen=True)
class BundleAsset:
    """A single asset (js or css) returned to a public HTTP request."""

    content: str
    content_type: str
    hash: str


class BundleStorageBackend(Protocol):
    """Protocol implemented by every bundle storage backend.

    All operations are keyed by `version_id` so a single `app_versions` row
    has exactly one bundle. Concrete backends decide whether the bytes live
    in Postgres, on disk, or in object storage.
    """

    async def save_bundle(self, version_id: int, artifact: BundleArtifact) -> None: ...

    async def get_asset(
        self, version_id: int, kind: str, expected_hash: str
    ) -> BundleAsset | None:
        """Fetch a single asset (kind = 'js' | 'css'). Returns None if the
        version has no bundle, or if `expected_hash` doesn't match the
        stored bundle (URL hash is stale → 404 to force a fresh fetch)."""
        ...


# ── Postgres backend ─────────────────────────────────────────────────────────


_CONTENT_TYPES = {
    "js": "application/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
}


class PostgresBundleStorage:
    """Stores the bundle in `app_versions.bundle_js / bundle_css / bundle_hash /
    bundled_at`. Suitable up to a few MB per version; swap to S3 when bundles
    grow or read traffic warrants a CDN."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_bundle(self, version_id: int, artifact: BundleArtifact) -> None:
        version = await self._session.get(AppVersionModel, version_id)
        if version is None:
            raise ValueError(f"version {version_id} not found")
        version.bundle_js = artifact.js
        version.bundle_css = artifact.css
        version.bundle_hash = artifact.hash
        version.bundled_at = artifact.bundled_at
        # Flush so callers can read back through other methods in the same txn.
        await self._session.flush()

    async def get_asset(
        self, version_id: int, kind: str, expected_hash: str
    ) -> BundleAsset | None:
        if kind not in _CONTENT_TYPES:
            return None
        version = await self._session.get(AppVersionModel, version_id)
        if version is None or not version.bundle_hash:
            return None
        if version.bundle_hash != expected_hash:
            return None
        content = version.bundle_js if kind == "js" else version.bundle_css
        if content is None:
            return None
        return BundleAsset(
            content=content,
            content_type=_CONTENT_TYPES[kind],
            hash=version.bundle_hash,
        )

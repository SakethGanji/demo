"""Per-project API keys (BACKEND-DESIGN §5.4). Shown once; sha256 at rest; bound to (project, env)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiKeyModel, ProjectModel
from ..utils.ids import generate_id
from .project_service import ProjectError, env_names


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_to_dict(k: ApiKeyModel) -> dict[str, Any]:
    return {"id": k.id, "name": k.name, "environment": k.environment, "prefix": k.prefix,
            "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None}


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, project: ProjectModel, name: str, environment: str | None, actor: str | None = None) -> dict[str, Any]:
        if not name or not name.strip():
            raise ProjectError(422, "invalid_name", "name is required")
        env = environment or project.default_environment
        if env not in env_names(project):
            raise ProjectError(422, "unknown_environment", f"environment '{env}' is not in this project", environment=env)
        key = f"sk_{env}_{secrets.token_urlsafe(32)}"
        prefix = key[: len(f"sk_{env}_") + 6]
        k = ApiKeyModel(id=generate_id("key"), project_id=project.id, environment=env, name=name.strip(), prefix=prefix,
                        key_hash=_hash(key), created_by=actor, created_at=datetime.now())
        self._s.add(k)
        await self._s.commit()
        return {**key_to_dict(k), "key": key}

    async def list(self, project: ProjectModel) -> list[dict[str, Any]]:
        res = await self._s.execute(select(ApiKeyModel).where(ApiKeyModel.project_id == project.id).order_by(ApiKeyModel.created_at))
        return [key_to_dict(k) for k in res.scalars().all()]

    async def revoke(self, project: ProjectModel, key_id: str) -> None:
        k = await self._s.get(ApiKeyModel, key_id)
        if not k or k.project_id != project.id:
            raise ProjectError(404, "api_key_not_found", "API key not found")
        if k.revoked_at is None:
            k.revoked_at = datetime.now()
            await self._s.commit()

    async def verify(self, project_id: str, key: str) -> ApiKeyModel | None:
        res = await self._s.execute(select(ApiKeyModel).where(ApiKeyModel.key_hash == _hash(key)))
        k = res.scalars().first()
        if not k or k.revoked_at is not None or not hmac.compare_digest(k.project_id, project_id):
            return None
        k.last_used_at = datetime.now()
        await self._s.commit()
        return k

"""Credential service — encrypt/decrypt + CRUD."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from cryptography.fernet import Fernet

from ..core.config import settings

if TYPE_CHECKING:
    from ..repositories.credential_repository import CredentialRepository


def _get_fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        raise RuntimeError(
            "WORKFLOW_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(data: dict[str, Any]) -> str:
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(encrypted: str) -> dict[str, Any]:
    return json.loads(_get_fernet().decrypt(encrypted.encode()))


class CredentialService:
    """Business logic for credential management."""

    def __init__(self, credential_repo: CredentialRepository) -> None:
        self._repo = credential_repo

    async def create(
        self,
        name: str,
        type: str,
        data: dict[str, Any],
        team_id: str = "default",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        encrypted = encrypt(data)
        from ..utils.ids import credential_id
        cred_id = credential_id()
        cred = await self._repo.create(
            id=cred_id,
            name=name,
            type=type,
            data=encrypted,
            team_id=team_id,
            created_by=created_by,
        )
        return {"id": cred.id, "name": cred.name, "type": cred.type, "created_at": cred.created_at.isoformat()}

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        cred = await self._repo.get(credential_id)
        if not cred:
            return None
        return {"id": cred.id, "name": cred.name, "type": cred.type, "created_at": cred.created_at.isoformat()}

    async def get_decrypted(self, credential_id: str) -> dict[str, Any] | None:
        """Internal use only — returns decrypted credential data."""
        cred = await self._repo.get(credential_id)
        if not cred:
            return None
        return decrypt(cred.data)

    async def list(self, team_id: str = "default") -> list[dict[str, Any]]:
        creds = await self._repo.list(team_id)
        return [
            {"id": c.id, "name": c.name, "type": c.type, "created_at": c.created_at.isoformat()}
            for c in creds
        ]

    async def update(
        self,
        credential_id: str,
        data: dict[str, Any] | None = None,
        name: str | None = None,
        type: str | None = None,
    ) -> dict[str, Any] | None:
        encrypted = encrypt(data) if data is not None else None
        cred = await self._repo.update(credential_id, encrypted_data=encrypted, name=name, type=type)
        if not cred:
            return None
        return {"id": cred.id, "name": cred.name, "type": cred.type, "updated_at": cred.updated_at.isoformat()}

    async def delete(self, credential_id: str) -> bool:
        return await self._repo.delete(credential_id)

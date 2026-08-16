"""File storage abstraction for multi-file app versions."""

from __future__ import annotations

import os
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import AppFileModel
from .tsx_parser import parse_tsx_file


# File-type inference map
_EXT_TO_TYPE: dict[str, str] = {
    ".tsx": "tsx",
    ".ts": "ts",
    ".jsx": "jsx",
    ".js": "js",
    ".css": "css",
    ".json": "json",
    ".html": "html",
    ".md": "md",
    ".svg": "svg",
}

# File types that should be parsed with tree-sitter
_PARSEABLE_TYPES = {"tsx", "ts", "jsx", "js"}


def _infer_file_type(path: str) -> str:
    """Infer file type from extension."""
    _, ext = os.path.splitext(path)
    return _EXT_TO_TYPE.get(ext.lower(), "txt")


class FileStorageBackend(Protocol):
    """Protocol for file storage implementations."""

    async def save_files(self, version_id: int, files: list[dict[str, str]]) -> list[dict]: ...
    async def get_files(self, version_id: int) -> list[dict]: ...
    async def get_file(self, version_id: int, path: str) -> dict | None: ...


class PostgresFileStorage:
    """Store app files in the app_files PostgreSQL table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_files(self, version_id: int, files: list[dict[str, str]]) -> list[dict]:
        """Save a batch of files for a version. Parses TSX/TS files for structural index."""
        result: list[dict] = []
        for f in files:
            path = f["path"]
            content = f["content"]
            file_type = f.get("file_type") or _infer_file_type(path)

            # Parse TSX/TS files to extract structural information
            parsed_index: dict[str, Any] | None = None
            if file_type in _PARSEABLE_TYPES:
                parsed_index = parse_tsx_file(content)

            row = AppFileModel(
                version_id=version_id,
                path=path,
                content=content,
                file_type=file_type,
                parsed_index=parsed_index,
                size_bytes=len(content.encode("utf-8")),
            )
            self._session.add(row)
            result.append({
                "path": path,
                "content": content,
                "file_type": file_type,
                "parsed_index": parsed_index,
            })
        await self._session.flush()
        return result

    async def get_files(self, version_id: int) -> list[dict]:
        """Get all files for a version."""
        stmt = (
            select(AppFileModel)
            .where(AppFileModel.version_id == version_id)
            .order_by(AppFileModel.path)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "path": r.path,
                "content": r.content,
                "file_type": r.file_type,
                "parsed_index": r.parsed_index,
            }
            for r in rows
        ]

    async def get_file(self, version_id: int, path: str) -> dict | None:
        """Get a single file by version and path."""
        stmt = select(AppFileModel).where(
            AppFileModel.version_id == version_id,
            AppFileModel.path == path,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if not row:
            return None
        return {
            "path": row.path,
            "content": row.content,
            "file_type": row.file_type,
            "parsed_index": row.parsed_index,
        }

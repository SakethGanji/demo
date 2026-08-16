"""File browser API routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel


router = APIRouter(prefix="/files")


class FileEntry(BaseModel):
    """File or directory entry."""

    name: str
    path: str
    type: Literal["file", "directory"]
    size: int | None = None
    extension: str | None = None


class BrowseResponse(BaseModel):
    """Response for file browser."""

    current_path: str
    parent_path: str | None
    entries: list[FileEntry]


@router.get("/browse", response_model=BrowseResponse)
async def browse_files(
    path: str = Query(default="~", description="Directory path to browse"),
    filter_extensions: str | None = Query(
        default=None,
        description="Comma-separated list of extensions to filter (e.g., '.csv,.parquet')",
    ),
) -> BrowseResponse:
    """
    Browse files and directories at the given path.

    Returns a list of files and directories in the specified path.
    Use filter_extensions to show only files with specific extensions.
    """
    # Expand user home directory
    expanded_path = os.path.expanduser(path)
    target_path = Path(expanded_path)

    # Validate path exists and is a directory
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")

    # Parse extensions filter
    extensions: set[str] | None = None
    if filter_extensions:
        extensions = {ext.strip().lower() for ext in filter_extensions.split(",")}
        # Ensure extensions start with dot
        extensions = {ext if ext.startswith(".") else f".{ext}" for ext in extensions}

    # Get parent path
    parent_path: str | None = None
    if target_path.parent != target_path:
        parent_path = str(target_path.parent)

    # List directory contents
    entries: list[FileEntry] = []
    try:
        for entry in sorted(target_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Skip hidden files/directories
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                entries.append(
                    FileEntry(
                        name=entry.name,
                        path=str(entry),
                        type="directory",
                    )
                )
            elif entry.is_file():
                ext = entry.suffix.lower()
                # Apply extension filter if specified
                if extensions and ext not in extensions:
                    continue

                try:
                    size = entry.stat().st_size
                except OSError:
                    size = None

                entries.append(
                    FileEntry(
                        name=entry.name,
                        path=str(entry),
                        type="file",
                        size=size,
                        extension=ext if ext else None,
                    )
                )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")

    return BrowseResponse(
        current_path=str(target_path),
        parent_path=parent_path,
        entries=entries,
    )


@router.get("/validate")
async def validate_file_path(
    path: str = Query(..., description="File path to validate"),
    extensions: str | None = Query(
        default=None,
        description="Comma-separated list of allowed extensions",
    ),
) -> dict:
    """
    Validate that a file path exists and optionally matches allowed extensions.
    """
    expanded_path = os.path.expanduser(path)
    file_path = Path(expanded_path)

    if not file_path.exists():
        return {"valid": False, "error": "File not found"}

    if not file_path.is_file():
        return {"valid": False, "error": "Path is not a file"}

    if extensions:
        allowed = {ext.strip().lower() for ext in extensions.split(",")}
        allowed = {ext if ext.startswith(".") else f".{ext}" for ext in allowed}
        if file_path.suffix.lower() not in allowed:
            return {
                "valid": False,
                "error": f"Invalid extension. Allowed: {', '.join(allowed)}",
            }

    return {
        "valid": True,
        "path": str(file_path),
        "name": file_path.name,
        "size": file_path.stat().st_size,
        "extension": file_path.suffix.lower(),
    }

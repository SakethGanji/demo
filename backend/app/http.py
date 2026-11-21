"""Shared 404 lookups for the API routers."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, UploadFile

from . import config


def run_or_404(request: Request, run_id: str, msg: str | None = None) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, msg or f"No run with id '{run_id}'.")
    return rec


def dataset_or_404(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = request.app.state.db.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    if ds.get("state") == "deleted":  # DL1: the rows are gone; the tombstone is in GET /api/datasets/{id}/history
        raise HTTPException(410, f"Dataset '{dataset_id}' was deleted ({ds.get('state_at')}, {ds.get('state_by')}); its lineage is kept.")
    return ds


def version_or_404(request: Request, vid: str, what: str = "version") -> dict[str, Any]:
    v = request.app.state.registry.get_version(vid)
    if not v:
        raise HTTPException(404, f"No {what} with id '{vid}'.")
    return v


def batch_or_404(request: Request, bid: str) -> dict[str, Any]:
    b = request.app.state.registry.get_batch(bid)
    if not b:
        raise HTTPException(404, f"No batch with id '{bid}'.")
    return b


async def stash_upload(rec: dict[str, Any], file: UploadFile, sub: str, prefix: str) -> tuple[str, Path]:
    """Save an uploaded CSV as <run_dir>/<sub>/<prefix+id>/input.csv; returns (the new id, that folder)."""
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if not content.strip():
        raise HTTPException(400, "The file is empty.")
    new_id = prefix + secrets.token_hex(4)
    d = Path(rec["config"]["run_dir"]) / sub / new_id
    d.mkdir(parents=True)
    (d / "input.csv").write_bytes(content)
    return new_id, d

"""Shared 404 lookups for the API routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


def run_or_404(request: Request, run_id: str, msg: str | None = None) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, msg or f"No run with id '{run_id}'.")
    return rec


def dataset_or_404(request: Request, dataset_id: str) -> dict[str, Any]:
    ds = request.app.state.db.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
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

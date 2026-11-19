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

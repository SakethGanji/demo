"""Snapshots and materialise (labeling plan §6.2; LB-A).

    POST /api/label-layers/{layer_id}/snapshots {policy?}            label.snapshot → 201 the snapshot (built in-request, POC)
    GET  /api/label-layers/{layer_id}/snapshots                      label.read
    GET  /api/label-snapshots/{snapshot_id}                          label.read
    GET  /api/label-snapshots/{snapshot_id}/verify                   label.read
    GET  /api/label-snapshots/{snapshot_id}/rows?after=&limit=       label.read
    POST /api/label-snapshots/{snapshot_id}/materialise {version_id?, columns?, reason}   label.read + data.version → 201
    GET  /api/label-runs/{run_id}/verify                             run.read: the run's overlay pin re-verified"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..authz import body, path, require
from . import snapshot as SN
from .api_common import call, obj, who

router = APIRouter(prefix="/api")


@router.post("/label-layers/{layer_id}/snapshots", status_code=201,
             dependencies=[require("label.snapshot", path("layer_id", "label_layer"))])
async def create(layer_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    from ..catalog import holds  # noqa: F401 - holds don't block a snapshot (it destroys nothing)

    b = obj(body)
    return await call(SN.create, request.app.state.db, request.app.state.lake, layer_id, b.get("policy"), who(request, b))


def _list(db: Any, layer_id: str) -> dict[str, Any]:
    from .store import layer

    with db.connect() as con:
        layer(con, layer_id)
        ids = [r[0] for r in con.execute("SELECT id FROM label_snapshots WHERE layer_id = ? ORDER BY created_at DESC", (layer_id,))]
    return {"snapshots": [SN.get(db, i) for i in ids]}


@router.get("/label-layers/{layer_id}/snapshots", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def listing(layer_id: str, request: Request) -> Any:
    return await call(_list, request.app.state.db, layer_id)


@router.get("/label-snapshots/{snapshot_id}", dependencies=[require("label.read", path("snapshot_id", "label_snapshot"))])
async def get(snapshot_id: str, request: Request) -> Any:
    return await call(SN.get, request.app.state.db, snapshot_id)


@router.get("/label-snapshots/{snapshot_id}/verify", dependencies=[require("label.read", path("snapshot_id", "label_snapshot"))])
async def verify(snapshot_id: str, request: Request) -> Any:
    return await call(SN.verify, request.app.state.db, request.app.state.lake, snapshot_id)


def _rows(db: Any, store: Any, sid: str, after: int, limit: int) -> dict[str, Any]:
    rs = [r for r in SN.load(db, store, sid) if int(r["__row_id"]) > after]
    limit = max(1, min(limit, 5000))
    page = rs[:limit]
    return {"rows": page, "next_after": page[-1]["__row_id"] if len(rs) > limit else None}


@router.get("/label-snapshots/{snapshot_id}/rows", dependencies=[require("label.read", path("snapshot_id", "label_snapshot"))])
async def rows(snapshot_id: str, request: Request, after: int = -1, limit: int = 1000) -> Any:
    return await call(_rows, request.app.state.db, request.app.state.lake, snapshot_id, after, limit)


@router.post("/label-snapshots/{snapshot_id}/materialise", status_code=201,
             dependencies=[require("label.read", path("snapshot_id", "label_snapshot"),
                                   also=[("data.version", path("snapshot_id", "label_snapshot"))])])
async def materialise(snapshot_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    from .materialise_snapshot import materialise as m

    b = obj(body)
    return await call(m, request.app, snapshot_id, b.get("version_id"), who(request, b), columns=b.get("columns"),
                      reason=str(b.get("reason") or ""))


@router.get("/label-runs/{run_id}/verify", dependencies=[require("run.read", path("run_id", "run"))])
async def verify_run(run_id: str, request: Request) -> Any:
    from .overlay import verify_run as v

    return await call(v, request.app.state.db, request.app.state.lake, run_id)


_ = body

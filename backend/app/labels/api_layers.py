"""Label layers, schemas, events, status (labeling plan §6.2; LB-A).

    GET   /api/datasets/{dataset_id}/label-layers                      label.read
    POST  /api/datasets/{dataset_id}/label-layers {table?, name, kind?, purpose?, spec?}    label.layer_manage → 201
    GET   /api/label-layers/{layer_id}                                 label.read
    PATCH /api/label-layers/{layer_id} {state?, name?, purpose?}       label.layer_manage
    POST  /api/label-layers/{layer_id}/schemas {spec, guidelines?, class_map?}   label.layer_manage → 201
    POST  /api/label-layers/{layer_id}/events {events:[…≤500], version_id?}      label.task_manage (LBL-VERIFY M12: direct
          events bypass queues, gold and blind; annotators label through LB-B's task routes, which call append_events)
    GET   /api/label-layers/{layer_id}/events?after_seq=&limit=&row_id=          label.read
    GET   /api/label-layers/{layer_id}/rows/{row_id}                   label.read: history + resolved + carry status
    GET   /api/label-layers/{layer_id}/status?version_id=&rows=1       label.read
    GET   /api/label-layers/{layer_id}/resolve?columns=                label.read (live resolution, default policy)"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..authz import path, require
from . import store as ST
from .api_common import call, obj, who
from .resolve import resolve
from .status import judged, status

router = APIRouter(prefix="/api")


@router.get("/datasets/{dataset_id}/label-layers", dependencies=[require("label.read", path("dataset_id", "dataset"))])
async def list_layers(dataset_id: str, request: Request) -> Any:
    return {"layers": await call(ST.layers, request.app.state.db, dataset_id)}


@router.post("/datasets/{dataset_id}/label-layers", status_code=201,
             dependencies=[require("label.layer_manage", path("dataset_id", "dataset"))])
async def create_layer(dataset_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    b = obj(body)
    return await call(ST.create_layer, request.app.state.db, dataset_id, b.get("table"), b.get("name"), who(request, b),
                      kind=b.get("kind") or "labels", purpose=b.get("purpose") or "", spec=b.get("spec"))


@router.get("/label-layers/{layer_id}", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def get_layer(layer_id: str, request: Request) -> Any:
    from . import get_layer as g

    return await call(g, request.app.state.db, layer_id)


@router.patch("/label-layers/{layer_id}", dependencies=[require("label.layer_manage", path("layer_id", "label_layer"))])
async def patch_layer(layer_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    b = obj(body)
    return await call(ST.patch_layer, request.app.state.db, layer_id, b, who(request, b))


@router.post("/label-layers/{layer_id}/schemas", status_code=201,
             dependencies=[require("label.layer_manage", path("layer_id", "label_layer"))])
async def add_schema(layer_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    b = obj(body)
    return await call(ST.add_schema, request.app.state.db, layer_id, b.get("spec"), who(request, b),
                      guidelines=str(b.get("guidelines") or ""), class_map=b.get("class_map"))



@router.post("/label-layers/{layer_id}/events", status_code=201,
             dependencies=[require("label.task_manage", path("layer_id", "label_layer"))])
async def append(layer_id: str, request: Request, body: dict[str, Any] | None = None) -> Any:
    b = obj(body)
    evs = b.get("events")
    if evs is None and "row_id" in b:
        evs = [b]  # a single event as the body
    return await call(ST.append_events, request.app.state.db, layer_id, evs, who(request, b),
                      version_id=b.get("version_id"), source=b.get("source") or "human",
                      ctx_columns=_ctx_cols(b.get("context_columns")))


def _ctx_cols(v: Any) -> list[str] | None:
    if v is None or v == []:
        return None  # as before: an empty list means every other column of the row
    if not isinstance(v, list) or not all(isinstance(c, str) for c in v):
        from fastapi import HTTPException

        raise HTTPException(400, {"code": "bad_request", "message": "context_columns is a list of column names."})
    return v


@router.get("/label-layers/{layer_id}/events", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def list_events(layer_id: str, request: Request, after_seq: int = 0, limit: int = 500, row_id: int | None = None) -> Any:
    return await call(ST.events, request.app.state.db, layer_id, after_seq=after_seq, limit=limit, row_id=row_id)


def _row(db: Any, layer_id: str, row_id: int, version_id: str | None) -> dict[str, Any]:
    with db.connect() as con:
        lay = ST._layer_full(con, layer_id)
        vid = version_id or ST.default_version(con, lay["root_id"])
    evs = ST.events(db, layer_id, row_id=row_id, limit=5000)["events"]
    res = resolve(db, layer_id, row_ids=[row_id])
    return {"layer_id": layer_id, "row_id": row_id, "version_id": vid, "events": evs,
            "resolved": judged(db, layer_id, vid, res, res["policy"]["stale_on"])}


@router.get("/label-layers/{layer_id}/rows/{row_id}", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def row_history(layer_id: str, row_id: int, request: Request, version_id: str | None = None) -> Any:
    return await call(_row, request.app.state.db, layer_id, row_id, version_id)


@router.get("/label-layers/{layer_id}/status", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def get_status(layer_id: str, request: Request, version_id: str | None = None, rows: int = 0) -> Any:
    return await call(status, request.app.state.db, layer_id, version_id, None, bool(rows))


@router.get("/label-layers/{layer_id}/resolve", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def get_resolve(layer_id: str, request: Request, columns: str | None = None) -> Any:
    cols = [c for c in (columns or "").split(",") if c] or None
    return await call(resolve, request.app.state.db, layer_id, None, columns=cols)


_ = JSONResponse

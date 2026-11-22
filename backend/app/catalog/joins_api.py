"""P14 (§5.8): saved joins and join frames.

    POST /api/datasets/{root}/joins {name, spec, version_id?, by?}      → 201 the join + its validation (coverage), 400 when invalid
    GET  /api/datasets/{root}/joins                                     → every saved join of the root (spec versions)
    GET  /api/datasets/{root}/joins/{join_id}                           → one join + its frames
    POST /api/datasets/{root}/joins/{join_id}/validate {version_id?}    → the validation on a version (nothing saved)
    POST /api/datasets/{root}/versions/{v}/frames {join_id, by?}        → the frame (materialised, or `cached: true`)
    GET  /api/datasets/{root}/versions/{v}/frames                       → the frames on a version
    GET  /api/datasets/{root}/frames/{frame_id}?verify=1                → one frame (verify: its cache blob and inputs re-hashed)

A run on a join is `POST /api/runs {dataset_id, join_id, label}` (engine/api.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, require
from . import governance as G
from . import joins as J

router = APIRouter(prefix="/api")


async def _run(fn, *a, **k):
    try:
        return await run_in_threadpool(fn, *a, **k)
    except J.JoinError as e:
        raise HTTPException(e.status, e.payload) from e


def _by(request: Request, body: dict[str, Any]) -> str:
    s = str((body or {}).get("by") or G.current_actor() or "").strip()[:80]
    if not s:
        raise HTTPException(400, "'by' is required: say who you are (the name goes in the audit log).")
    return s


def _vid_ok(v: Any) -> None:
    """X3 m6: a version id is a string (a number reached Postgres as `text = smallint`, a 500)."""
    if v not in (None, "") and not isinstance(v, str):
        raise HTTPException(400, {"code": "bad_request", "message": "version_id is a dataset version id (a string, ds_…)."})


@router.post("/datasets/{root_id}/joins", status_code=201, dependencies=[require("data.version", path("root_id", "dataset"))])
async def save_join(root_id: str, request: Request, body: dict[str, Any]) -> Any:
    from fastapi.responses import JSONResponse

    body = body if isinstance(body, dict) else {}
    who = _by(request, body)
    _vid_ok(body.get("version_id"))
    out, created = await _run(J.save, request.app.state.db, root_id, body.get("name"), body.get("spec"), who,
                              body.get("version_id") or None)
    return out if created else JSONResponse(out, status_code=200)


@router.get("/datasets/{root_id}/joins", dependencies=[require("data.read_meta", path("root_id", "dataset"))])
async def list_joins(root_id: str, request: Request) -> dict[str, Any]:
    return {"root_id": root_id, "joins": await _run(J.list_joins, request.app.state.db, root_id)}


@router.get("/datasets/{root_id}/joins/{join_id}", dependencies=[require("data.read_meta", path("root_id", "dataset"))])
async def get_join(root_id: str, join_id: str, request: Request) -> dict[str, Any]:
    db = request.app.state.db

    def work():
        j = J.get(db, join_id, root_id)
        with db.connect() as con:
            fr = [J.frame_out(r) for r in con.execute("SELECT * FROM join_frames WHERE join_id = ? ORDER BY created_at", (join_id,))]
        return {**j, "frames": fr}

    return await _run(work)


@router.post("/datasets/{root_id}/joins/{join_id}/validate", dependencies=[require("data.read_meta", path("root_id", "dataset"))])
async def validate_join(root_id: str, join_id: str, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    db = request.app.state.db
    vid = (body or {}).get("version_id") if isinstance(body, dict) else None
    _vid_ok(vid)

    def work():
        j = J.get(db, join_id, root_id)
        v = vid or J._latest_version(db, root_id, J._root(db, root_id).get("default_version_id"))
        J.version_of_root(db, root_id, v)
        return {"join_id": join_id, **J.public(J.validate(db, j["spec"], v))}

    return await _run(work)


@router.post("/datasets/{root_id}/versions/{version_id}/frames", status_code=201,
             dependencies=[require("run.train", path("version_id", "dataset"))])
async def make_frame(root_id: str, version_id: str, request: Request, body: dict[str, Any]) -> Any:
    from fastapi.responses import JSONResponse

    body = body if isinstance(body, dict) else {}
    jid = body.get("join_id")
    if not isinstance(jid, str) or not jid:
        raise HTTPException(400, {"code": "bad_request", "message": 'Send {"join_id": "jn_…"}.'})
    who = _by(request, body)
    app = request.app

    def work():
        J.version_of_root(app.state.db, root_id, version_id)
        j = J.get(app.state.db, jid)
        if j["root_id"] != root_id:
            raise J.JoinError(404, "not_found", f"Join {jid} belongs to {j['root_id']}, not {root_id}.")
        return J.frame(app.state.db, app.state.lake, jid, version_id, who)

    out = await _run(work)
    return JSONResponse(out, status_code=200) if out.get("cached") else out


@router.get("/datasets/{root_id}/versions/{version_id}/frames", dependencies=[require("data.read_meta", path("version_id", "dataset"))])
async def list_frames(root_id: str, version_id: str, request: Request) -> dict[str, Any]:
    db = request.app.state.db

    def work():
        J.version_of_root(db, root_id, version_id)
        return {"version_id": version_id, "frames": J.frames_of_version(db, version_id)}

    return await _run(work)


@router.get("/datasets/{root_id}/frames/{frame_id}", dependencies=[require("data.read_meta", path("root_id", "dataset"))])
async def get_frame(root_id: str, frame_id: str, request: Request, verify: int = 0) -> dict[str, Any]:
    app = request.app

    def work():
        fr = J.get_frame(app.state.db, frame_id)
        J.version_of_root(app.state.db, root_id, fr["version_id"])
        if verify:
            from .. import frames

            try:
                now, rec, _b = frames.hash_table(frames.JoinFrame(frame_id))
            except frames.FrameError as e:
                now, rec = f"unreadable: {e}", fr["cache_blob"]
            ins = J.check_inputs(app.state.db, fr)
            cache_ok = now == fr["cache_blob"]
            # F-X3b (M8): the cache is also rebuilt from the (good) inputs and compared: a frame built from other bytes fails
            derived = (J.derived_check(app.state.db, app.state.lake, fr) if cache_ok and all(x["ok"] for x in ins)
                       else {"ok": None, "summary": "not checked: the cache or an input does not verify"})
            fr["verify"] = {"cache": {"recorded": fr["cache_blob"], "now": now, "ok": cache_ok}, "inputs": ins, "derived": derived,
                            "ok": cache_ok and all(x["ok"] for x in ins) and derived["ok"] is True}
            if any(x.get("state") == "deleted" for x in ins):
                fr["verify"]["inputs_deleted"] = [x["version_id"] for x in ins if x.get("state") == "deleted"]
        with app.state.db.connect() as con:
            fr["runs"] = [dict(r) for r in con.execute(
                "SELECT DISTINCT run_id FROM run_inputs WHERE join_frame_id = ? ORDER BY run_id", (frame_id,))]
        return fr

    return await _run(work)


@router.get("/datasets/{root_id}/frames/{frame_id}/rows", dependencies=[require("data.preview", path("root_id", "dataset"))])
async def frame_rows(root_id: str, frame_id: str, request: Request, limit: int = 20) -> dict[str, Any]:
    """U2: the first rows of a join frame (in __row_id order) for the join builder's preview. Masked like every row read:
    a frame column whose source column (grain or joined table, or an aggregate's column) is PII-tagged gets that
    column's mask; there is no unmasked path here."""
    from .. import frames
    from ..lake import duck
    from ..lake import mask as MK

    db = request.app.state.db
    n = max(1, min(int(limit), 100))

    def work():
        fr = J.get_frame(db, frame_id)
        J.version_of_root(db, root_id, fr["version_id"])
        spec = J.get(db, fr["join_id"])["spec"]
        ins = fr["inputs"]
        lts = [frames.lake_table(frames.Table(x["version_id"], x["table"])) for x in ins]
        masks = [MK.for_version(x["version_id"], x["table"]) for x in ins]
        lt = frames.lake_table(frames.JoinFrame(frame_id))
        types = {c["name"]: c["type"] for c in lt.schema}
        sel, masked = ["__row_id"], {}
        for name, origin, src in frames.join_output_columns(spec, lts):
            i = 0 if origin == "grain" else int(origin.split(":")[1]) + 1
            col = src
            if origin != "grain" and spec["joins"][i - 1]["type"] == "aggregate":
                agg = next((a for a in spec["joins"][i - 1]["aggs"] if spec["joins"][i - 1].get("prefix", "") + a["name"] == name), {})
                col = agg.get("column") if agg.get("fn") not in ("count",) else None
            m = masks[i] if i < len(masks) else None
            if m is not None and col and col in m.cols:
                masked[name] = m.cols[col]
                sel.append(f"{m.sql(col, frames._qi(name), types.get(name, 'VARCHAR'))} AS {frames._qi(name)}")
            else:
                sel.append(frames._qi(name))
        with duck.job() as con:
            cur = con.execute(f"SELECT {', '.join(sel)} FROM {frames.lake_sql(lt)} ORDER BY __row_id LIMIT {n}")
            names = [d[0] for d in cur.description]
            rows = sorted(cur.fetchall(), key=lambda r: r[0])
        return {"join_frame_id": frame_id, "n_rows": fr["n_rows"], "columns": names[1:],
                "rows": [[v if v is None or isinstance(v, (int, float, str, bool)) else str(v) for v in r[1:]] for r in rows],
                "row_ids": [r[0] for r in rows], "masked_columns": sorted(masked), "masks": masked}

    return await _run(work)

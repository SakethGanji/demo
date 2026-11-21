"""DL1 routes: a dataset's versions, upload as the next version, diff, reverse lineage, default / rollback, card,
archive, four-eyes delete, integrity, export, search. Every write takes {by} and is audited."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse

from .. import config
from ..datasets import DatasetError
from . import dsets as D
from . import governance as G
from .api import _text, _who

router = APIRouter(prefix="/api")


def _call(fn, *a, _gov: tuple | None = None, **k):
    """`_gov` = (app, by, action, details): a 403 / 409 refusal is audited as `<action>_refused` (DL2)."""
    try:
        return fn(*a, **k)
    except D.DsError as e:
        if _gov and e.status in (403, 409):
            G.refused(_gov[0].state.db, _gov[1], _gov[2], e.status, e.payload, _gov[3])
        raise HTTPException(e.status, e.payload) from e
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e


async def _thread(fn, *a, **k):
    try:
        return await run_in_threadpool(fn, *a, **k)
    except D.DsError as e:
        raise HTTPException(e.status, e.payload) from e
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/data/search")
def search(request: Request, q: str = "", project: str | None = None, tag: str | None = None, owner: str | None = None,
           op: str | None = None, since: str | None = None, until: str | None = None, state: str = "active") -> dict[str, Any]:
    """Datasets (one row per root) by text (name, file, id, description, owner, source, tags), project, tag, owner,
    op (any version's) and date (any version made in [since, until]). state: active | archived | deleted | all, matched
    on any version (DL2: state=deleted finds a dataset with one deleted version; versions_in_state lists them)."""
    if state not in ("active", "archived", "deleted", "all"):
        raise HTTPException(400, "state is active, archived, deleted or all.")
    from datetime import datetime

    for k, v in (("since", since), ("until", until)):
        if v:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise HTTPException(400, f"{k} is a date (YYYY-MM-DD) or an ISO date-time, not {v[:40]!r}.") from None
    out = D.search(request.app, q=q, project=project, tag=tag, owner=owner, op=op, since=since, until=until, state=state)
    return {"datasets": out, "total": len(out)}


@router.get("/datasets/{dataset_id}/history")
async def history(dataset_id: str, request: Request) -> dict[str, Any]:
    """The dataset this id belongs to: card, default version, every version (who, when, op, changes, rows × cols, hash,
    integrity, use), pending requests and its audit trail."""
    return await _thread(D.history, request.app, dataset_id)


@router.post("/datasets/{dataset_id}/versions", status_code=201)
async def upload_version(dataset_id: str, request: Request, file: UploadFile = File(...), by: str = Form(""),
                         row_key: str | None = Form(None), preview: bool = Form(False), reason: str = Form(""),
                         make_default: str = Form("auto")) -> dict[str, Any]:
    """A new file as the next version of `dataset_id`: rows matched by `row_key` (default: an id-like column, else an
    exact row hash; "hash" forces the hash), the same holdout on matched rows, contract warnings (never a block).
    DL2: `preview=true` writes nothing and answers the matching summary, schema changes and warnings. `reason`: an
    optional reason / change ticket, kept with the version. The new version becomes the default only when it has no
    contract warnings and `make_default` is not "no"; otherwise POST …/default promotes it after review."""
    who = _who(by)
    if make_default not in ("auto", "yes", "no"):
        raise HTTPException(400, "make_default is auto, yes or no.")
    rec = request.app.state.db.get_dataset(dataset_id)
    if not rec:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    if rec.get("state") == D.DELETED:
        raise HTTPException(410, f"{dataset_id} was deleted.")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    before = None if preview else await _thread(D.current_default, request.app, dataset_id)
    out = await _thread(request.app.state.datasets.upload_version, dataset_id, file.filename or "", content, by=who,
                        row_key=(row_key or None) if row_key != "" else "", preview=preview, reason=reason.strip()[:500] or None)
    if preview:
        return out
    dflt = await _thread(D.settle_default, request.app, out["id"], before, out["upload"]["checks"]["warnings"], who, make_default)
    from ..main import _detail

    return {**_detail(request.app.state.db.get_dataset(out["id"]) or out), "upload": {**out["upload"], "default": dflt}}


@router.get("/datasets/{a}/diff/{b}")
async def diff(a: str, b: str, request: Request) -> dict[str, Any]:
    """a → b: schema (added / removed columns, type changes), rows (added / removed / changed: by b's row map onto its
    parent, the shared original rows, or an exact hash), per-column missing %, mean / median, top categories, PSI."""
    out = await _thread(D.diff, request.app, a, b)
    if out["rows"].get("changed_examples"):  # DL2: the diff shows rows cell by cell: a read of both versions
        for x in (a, b):
            G.read_audit(request.app.state.db, "diff_rows", x, {"other": b if x == a else a})
    return out


@router.get("/datasets/{dataset_id}/usage")
def usage(dataset_id: str, request: Request, descendants: bool = True) -> dict[str, Any]:
    """Reverse lineage: the runs trained on this version (and, by default, on versions derived from it), the registered
    versions built from them and the champions among those; plus what blocks an archive or delete."""
    with request.app.state.db.connect() as con:
        _call(D.root_of, con, dataset_id)
        ids = [dataset_id, *(D._descendants(con, dataset_id) if descendants else [])]
        u = D.usage(con, ids)
        return {"dataset_id": dataset_id, "datasets": ids, **u, "blocks_archive": D.blockers(con, [dataset_id], "archive"),
                "blocks_archive_or_delete": D.blockers(con, [dataset_id], "delete")}


@router.post("/datasets/{dataset_id}/default")
def set_default(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{version_id, by, reason}: the version Build opens. Moving it to an older version is a rollback (audited as
    dataset_rolled_back); no data is changed either way."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    vid = body.get("version_id")
    if not isinstance(vid, str) or not vid:
        raise HTTPException(400, 'Send {"version_id": …, "by": …}.')
    return _call(D.set_default, request.app, dataset_id, vid, who, str(body.get("reason") or "")[:500],
                 _gov=(request.app, who, "dataset_default_set", {"dataset_id": vid}))


@router.patch("/datasets/{dataset_id}/card")
def edit_card(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, name?, description?, owner?, source?, label_definition?, known_issues?}. The name renames every version."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    fields: dict[str, Any] = {}
    for k, hi in D.CARD_FIELDS.items():
        v = _text(body, k, 0, hi, False)
        if v is not None:
            fields[k] = v
    nm = _text(body, "name", 1, 120, False)
    if nm is not None:
        fields["name"] = nm
    if not fields:
        raise HTTPException(400, f"Send one of name, {', '.join(D.CARD_FIELDS)}, with by.")
    return _call(D.update_card, request.app, dataset_id, fields, who)


@router.post("/datasets/{dataset_id}/archive")
def archive(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, archived: true|false, reason, only_version?}: hide (or show again) a version, or the whole dataset when the id
    is its root (only_version: v1 alone). Refused while a champion, or a registered version that is not rejected,
    retired or Archived, comes from it."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    arch = body.get("archived", True)
    if not isinstance(arch, bool):
        raise HTTPException(400, "archived is true or false.")
    return _call(D.archive, request.app, dataset_id, who, arch, str(body.get("reason") or "")[:500], bool(body.get("only_version")),
                 _gov=(request.app, who, "dataset_archive" if arch else "dataset_unarchive", {"dataset_id": dataset_id}))


@router.post("/datasets/{dataset_id}/delete", status_code=201)
def request_delete(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, reason}: ask to delete a version, or the whole dataset when the id is its root. Someone else approves
    (POST /api/dataset-requests/{id}/decide); an admin (ENSEMBLE_ADMINS) deletes at once. The files go; the datasets
    row, lineage.json, a TOMBSTONE.json, every edge and the audit log stay."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    return _call(D.request_delete, request.app, dataset_id, who, reason, bool(body.get("only_version")), bool(body.get("freeze_holdout")),
                 _gov=(request.app, who, "dataset_delete", {"dataset_id": dataset_id}))


@router.post("/dataset-requests/{rid}/decide")
def decide(rid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    approve = body.get("approve")
    if not isinstance(approve, bool):
        raise HTTPException(400, 'Send {"approve": true} or {"approve": false}.')
    return _call(D.decide_delete, request.app, rid, approve, who, str(body.get("comment") or "")[:500],
                 _gov=(request.app, who, "dataset_delete_decide", {"request_id": rid, "approve": approve}))


@router.post("/datasets/{dataset_id}/verify")
async def verify(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, all?}: re-hash the file(s) and compare with the sha256 stored when each version was made."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    return await _thread(D.verify, request.app, dataset_id, who, bool(body.get("all")))


@router.post("/data/verify-all")
async def verify_all(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by}: an admin (ENSEMBLE_ADMINS) re-hashes every version of every dataset that is not deleted. Also runs on a
    timer when ENSEMBLE_VERIFY_EVERY_HOURS is set (no default)."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    if not G.is_admin(who):
        G.refused(request.app.state.db, who, "dataset_verify_all", 403, "not an admin")
        raise HTTPException(403, f"Only an admin (ENSEMBLE_ADMINS) runs verify-all; {who} is not one.")
    return await _thread(D.verify_all, request.app, who)


@router.get("/datasets/{dataset_id}/export")
async def export(dataset_id: str, request: Request, by: str = "", format: str = "zip"):
    """format=zip: data.csv + lineage.json + manifest.json (the chain to the root, the stored and current sha256).
    format=csv: the file alone. Audited."""
    who = _who(by)
    if format not in ("zip", "csv"):
        raise HTTPException(400, "format is zip or csv.")
    man = await _thread(D.export_manifest, request.app, dataset_id, who)
    store = request.app.state.datasets
    fname = f"{man['name']}-v{man['v']}-{dataset_id}".replace("/", "_").replace(" ", "_")
    if format == "csv":
        return FileResponse(store.csv_path(dataset_id), media_type="text/csv", filename=f"{fname}.csv",
                            headers={"X-Ensemble-SHA256": man["sha256_file"] or ""})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(store.csv_path(dataset_id), "data.csv")
        z.writestr("lineage.json", json.dumps(store.lineage(dataset_id) or {"root": man["root_id"], "parent": None, "op": "upload"}))
        z.writestr("manifest.json", json.dumps(man, indent=1))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{fname}.zip"'})

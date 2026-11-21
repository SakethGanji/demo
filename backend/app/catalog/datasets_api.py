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

from .. import config, frames
from ..authz import path, query, require, with_team  # P19
from ..datasets import DatasetConflict, DatasetError
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
    except DatasetConflict as e:
        raise HTTPException(409, str(e)) from e
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
    return with_team(request, {"datasets": out, "total": len(out)}, "search")


@router.get("/datasets/{dataset_id}/history")
async def history(dataset_id: str, request: Request) -> dict[str, Any]:
    """The dataset this id belongs to: card, default version, every version (who, when, op, changes, rows × cols, hash,
    integrity, use), pending requests and its audit trail. P20: values of PII-tagged columns in edit logs and audit
    details are masked."""
    from ..lake import mask as M

    out = await _thread(D.history, request.app, dataset_id)
    m = await _thread(M.masked_view, dataset_id)
    return m.scrub(out) if m is not None else out


# -- P20: PII tags ---------------------------------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/pii", dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])
async def pii_tags(dataset_id: str, request: Request, table: str | None = None) -> dict[str, Any]:
    """P20 (§6.3): the dataset root's PII tags for a table: {column, tag, mask, source (suggested | confirmed), set_by,
    set_at}, which columns are masked, and which suggestions wait for a maintainer. Columns the root has not seen yet
    are tagged first by the profiler's rules (names, then value patterns on a sample)."""
    from ..lake import mask as M

    _live(request, dataset_id)
    try:
        return await _thread(M.describe, dataset_id, table)
    except M.MaskError as e:
        raise _mask_err(e) from e


@router.put("/datasets/{dataset_id}/pii", dependencies=[require("data.tag_pii", path("dataset_id", "dataset"))])
async def set_pii_tags(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """P20: {columns: [{column, tag, mask?}], table?, by} confirms or changes tags (mask defaults by tag: pii_direct →
    redact, pii_quasi → bucket, sensitive → redact, protected / key / none → none), or {confirm: "suggested", by}
    confirms every suggestion as it stands. Tags belong to the root, so they apply to every version. Audited as
    pii_tags_set. Needs data.tag_pii (maintainer)."""
    from ..lake import mask as M

    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    _live(request, dataset_id)
    items = {"confirm": "suggested"} if body.get("confirm") == "suggested" else body.get("columns")
    try:
        return await _thread(M.set_tags, dataset_id, body.get("table"), items, who)
    except M.MaskError as e:
        raise _mask_err(e) from e


@router.post("/datasets/{dataset_id}/versions", status_code=201, dependencies=[require("data.version", path("dataset_id", "dataset"))])
async def upload_version(dataset_id: str, request: Request, file: UploadFile = File(...), by: str = Form(""),
                         row_key: str | None = Form(None), preview: bool = Form(False), reason: str = Form(""),
                         make_default: str = Form("auto"), acknowledge: str = Form("")) -> dict[str, Any]:
    """A new file as the next version of `dataset_id`. P06: a wrapper of POST /uploads (parent = dataset_id) → check →
    commit, so the rows are matched in DuckDB and **nothing is guessed**: `row_key` = a column (or "a,b") → match by that
    key; "hash" → by row content; "none" → all rows new. Without `row_key` the commit answers 400 match_required with
    the candidates (the best one first: type it as the key). `acknowledge` (comma list, e.g. holdout_identity_loss) is
    needed when the match loses ≥ 1 % of the holdout's identities or a better key exists. `preview=true` writes nothing
    and answers the matching summary (for the given key, else the best candidate, labelled as such). The new version
    becomes the default only when it has no warnings and `make_default` is not "no"."""
    from ..lake import upload as U
    from ..lake.writer import LakeError

    who = _who(by)
    if make_default not in ("auto", "yes", "no"):
        raise HTTPException(400, "make_default is auto, yes or no.")
    rec = request.app.state.db.get_dataset(dataset_id)
    if not rec:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    if rec.get("state") == D.DELETED:
        raise HTTPException(410, f"{dataset_id} was deleted.")
    db, lake, ds = request.app.state.db, request.app.state.lake, request.app.state.datasets
    rk = (row_key or "").strip()
    match = None if not rk else {"method": "row_hash"} if rk == "hash" else {"method": "none"} if rk == "none" else \
        {"method": "key", "columns": [c.strip() for c in rk.split(",") if c.strip()]}
    acks = [a.strip() for a in (acknowledge or "").split(",") if a.strip()]
    fname = file.filename or ""

    def chunks():
        while True:
            b = file.file.read(U.CHUNK)
            if not b:
                return
            yield b

    def run() -> dict[str, Any]:
        plan = {"parent_version_id": dataset_id, **({"match": match} if match else {}),
                **({"reason": reason.strip()[:500]} if reason.strip() else {})}
        u = U.stage_stream(db, lake, by=who, filename=fname, chunks=chunks(), plan=plan)
        up = u["id"]
        keep = False
        try:
            chk = U.check(db, lake, up, ds)
            if chk.get("blocks"):
                raise LakeError(400, "check_blocked", " ".join(b_["message"] for b_ in chk["blocks"]), blocks=chk["blocks"])
            nv = chk.get("next_version") or {}
            methods = nv.get("methods") or []
            if preview:
                return _legacy_preview(ds, dataset_id, fname, chk, nv, methods, match)
            if match is None:
                raise _match_required_legacy(nv)
            ver, created = U.commit(db, lake, up, by=who, reason=reason.strip()[:500], match=match, acknowledge=acks,
                                    make_default={"auto": None, "yes": True, "no": False}[make_default], dstore=ds)
            keep = True
            rec2 = ds.finish_lake_version(ver["version_id"], target=ver.get("target"), chk=U.get(db, up).get("check") or chk,
                                          by=who) if created else db.get_dataset(ver["version_id"])
            return {"ver": ver, "rec": rec2}
        finally:
            if not keep:
                U.discard(db, lake, up)

    try:
        out = await run_in_threadpool(run)
    except LakeError as e:
        raise HTTPException(e.status, e.payload) from e
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if "ver" not in out:  # the preview: 200, nothing written
        from fastapi.responses import JSONResponse

        return JSONResponse(json.loads(json.dumps(out, default=str)), status_code=200)
    ver = out["ver"]
    m = ver.get("match") or {}
    dflt = ver.get("default") or {}
    if ver.get("deduped"):
        dflt = {"is_default": False, "why": f"the same data as {ver['version_id']}: no new version was made"}
    elif not dflt.get("is_default"):
        dflt = {**dflt, "why": dflt.get("why") or ("it has warnings: review them" if ver.get("warnings") else "make_default was no"),
                "how_to_promote": f"POST /api/datasets/{ver['root_id']}/default {{version_id: {ver['version_id']}}}"}
    from ..main import _detail

    c = ver.get("contract") or {}
    summary = {"matched_by": m.get("matched_by"), "row_key": (m.get("columns") or [None])[0] if m.get("method") in ("key", "declared") else None,
               **{k: m.get(k) for k in ("matched", "changed", "added", "removed", "holdout_identity_lost", "acknowledged")},
               "match": m, "deduped": ver.get("deduped"),
               "checks": {"warnings": ver.get("warnings") or [], **{k: c.get(k) for k in ("columns_added", "columns_removed", "type_changes",
                                                                                        "rows_before", "rows_after", "renamed_maybe")}},
               **({"reason": reason.strip()[:500]} if reason.strip() else {}), "default": dflt}
    body = {**_detail(db.get_dataset(ver["version_id"]) or out["rec"]), "upload": summary}
    if ver.get("deduped"):  # nothing new was made: 200 with the existing version
        from ..lake.values import SafeJSONResponse

        return SafeJSONResponse(body, status_code=200)
    return body


def _match_required_legacy(nv: dict[str, Any]):
    from ..lake.writer import LakeError

    best = next((c for c in nv.get("candidates") or [] if c.get("valid")), None)
    hint = (f"The best candidate is '{', '.join(best['columns'])}' (unique on both sides, {best['overlap']:.0%} of the new keys are "
            f"in the parent): type it as the key column. " if best else "No column is a unique key on both sides. ")
    return LakeError(400, "match_required", "Say how rows are matched to the current version: nothing is guessed any more. " + hint +
                     "Or pick 'exact row hash' to match by row content.", candidates=nv.get("candidates") or [],
                     methods=nv.get("methods") or [], preselect=nv.get("preselect"))


def _legacy_preview(ds, dataset_id: str, fname: str, chk: dict[str, Any], nv: dict[str, Any], methods: list[dict[str, Any]],
                    match: dict[str, Any] | None) -> dict[str, Any]:
    """The DL2 preview shape (the UI of today), from the P06 check: the given match, else the best candidate."""
    sim = next((x for x in methods if "chosen" in x.get("roles", [])), None) if match else None
    if match and sim is None:  # the legacy form sends the key per request (no PATCH): simulate it here
        sim = next((x for x in methods if x.get("method") == match["method"] and x.get("columns") == match.get("columns")), None)
    guessed = False
    if sim is None and match is None:
        sim = next((x for x in methods if "best_candidate" in x.get("roles", [])), None) or \
            next((x for x in methods if x.get("method") == "row_hash"), None)
        guessed = True
    t = chk["tables"][0]
    c = nv.get("contract") or {}
    lbl = (sim or {}).get("label") or "nothing"
    with ds.db.connect() as con:
        r = con.execute("SELECT root_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        root = (r[0] if r else None) or dataset_id
        n_ver = 1 + con.execute("SELECT count(*) FROM datasets WHERE id = ? OR root_id = ?", (root, root)).fetchone()[0]
    ok = bool(sim and sim.get("valid"))
    return {"preview": True, "v": n_ver, "parent": dataset_id, "filename": fname, "rows": t["n_rows"], "n_cols": t["n_cols"],
            "sha256": t.get("source_sha256"),
            "matched_by": (f"{lbl} — the best candidate, not chosen yet: type it as the key to use it" if guessed and ok else lbl),
            "row_key": (sim or {}).get("columns", [None])[0] if (sim or {}).get("method") == "key" else None,
            **{k: (sim or {}).get(k, 0) if ok else 0 for k in ("matched", "changed", "added", "removed", "holdout_identity_lost")},
            "checks": {"warnings": (c.get("warnings") or []) + ((sim or {}).get("warnings") or []),
                       **{k: c.get(k) for k in ("columns_added", "columns_removed", "type_changes", "rows_before", "rows_after")}},
            "candidates": nv.get("candidates") or [], "methods": methods, "needs_match": match is None,
            "needs_acknowledge": (sim or {}).get("needs_acknowledge") or [], **({"problem": sim.get("problem")} if sim and not ok else {}),
            "would_be_default": ok and not (c.get("warnings") or sim.get("warnings")),
            "note": "nothing was written: send the file again without preview (with a row key) to create the version"}


@router.get("/datasets/{a}/diff/{b}", dependencies=[require("data.preview", path("a", "dataset"), also=[("data.preview", path("b", "dataset"))])])
async def diff(a: str, b: str, request: Request, by: str = "", key: str = "", wait: float = 0) -> dict[str, Any]:
    """a → b (P08: the two-pass DuckDB diff, cached): tables (unchanged by sha, changed, added, removed, renamed),
    schema (added / removed columns, type and dtype changes), rows (matched / changed / added / removed on `__row_id`
    in one dataset, on `key=col[,col]` (a confirmed business key) across datasets, else by row content), per-column
    changed cells, samples, missing %, mean / median, top categories and PSI from table_stats.
    DL3: when b was made by an edit of a, the operations and their cells (old → new) come with it (`edit`).
    The diff shows cells: it needs a name (by or the X-Ensemble-Actor header) and is audited as a read."""
    return await diff_q(request, a, b, by, key, wait)


async def diff_q(request: Request, a: str, b: str, by: str = "", key: str = "", wait: float = 0) -> dict[str, Any]:
    """GET /datasets/diff?a=&b=&key= (main.py routes it before /datasets/{id}) and the path form above."""
    if not a or not b:
        raise HTTPException(400, "Send a and b (two dataset version ids).")
    from ..lake import diff as LD
    from ..lake import mask as M

    who = G.reader(by)
    cols = [c.strip() for c in key.split(",") if c.strip()] if key else None
    q = request.query_params
    try:  # P20: each side's PII columns are masked (unmasked=1&reason=… asks for both in clear)
        ma, mb = await _thread(lambda: [M.for_request(request, x, None, unmasked=q.get("unmasked"), reason=q.get("reason"), by=who,
                                                      read="diff_rows") for x in (a, b)])
    except M.MaskError as e:
        raise _mask_err(e) from e
    try:
        out = await _thread(D.diff, request.app, a, b, cols, max(0.0, min(float(wait or 0), 30.0)))
    except HTTPException as e:
        if e.status_code == 400 and isinstance(e.detail, dict) and e.detail.get("code") == "key_invalid":
            e.detail = LD.mask_key_error(e.detail, ma if e.detail.get("side") != "b" else mb, cols)
        raise
    out = await _thread(LD.mask_result, out, ma, mb)
    if out.get("edit"):  # DL3: b's edit log (cells old → new)
        out["edit"] = mb.scrub(out["edit"])
    if ma.refused or mb.refused:
        out["unmasked_refused"] = ma.refused or mb.refused
    if out["rows"].get("changed_examples") or out.get("edit") or any((out.get("samples") or {}).values()):
        for x in (a, b):  # DL2: the diff shows rows cell by cell: a read of both versions
            G.read_audit(request.app.state.db, "diff_rows", x, {"other": b if x == a else a}, by=who)
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


@router.post("/datasets/{dataset_id}/default", dependencies=[require("data.set_default", path("dataset_id", "dataset"))])
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


@router.patch("/datasets/{dataset_id}/card", dependencies=[require("data.card", path("dataset_id", "dataset"))])
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


@router.post("/datasets/{dataset_id}/archive", dependencies=[require("data.archive", path("dataset_id", "dataset"))])
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


@router.post("/datasets/{dataset_id}/delete", status_code=201, dependencies=[require("data.delete", path("dataset_id", "dataset"))])
def request_delete(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, reason}: ask to delete a version, or the whole dataset when the id is its root. Someone else approves
    (POST /api/dataset-requests/{id}/decide); an admin (ENSEMBLE_ADMINS) deletes at once. The files go; the datasets
    row, lineage.json, a TOMBSTONE.json, every edge and the audit log stay."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    return _call(D.request_delete, request.app, dataset_id, who, reason, bool(body.get("only_version")), bool(body.get("freeze_holdout")),
                 irreproducible=body.get("irreproducible"), _gov=(request.app, who, "dataset_delete", {"dataset_id": dataset_id}))


@router.post("/dataset-requests/{rid}/decide", dependencies=[require("data.delete_decide", path("rid", "dataset_request"))])
def decide(rid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    approve = body.get("approve")
    withdraw = body.get("withdraw") is True  # DL3 (m6): the requester (or an admin) withdraws; a reviewer rejects
    if withdraw:
        approve = False
    if not isinstance(approve, bool):
        raise HTTPException(400, 'Send {"approve": true}, {"approve": false} or {"withdraw": true}.')
    return _call(D.decide_delete, request.app, rid, approve, who, str(body.get("comment") or "")[:500], withdraw=withdraw,
                 _gov=(request.app, who, "dataset_delete_decide", {"request_id": rid, "approve": approve, "withdraw": withdraw}))


@router.post("/datasets/{dataset_id}/verify", dependencies=[require("data.verify", path("dataset_id", "dataset"))])
async def verify(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, all?}: re-hash the file(s) and compare with the sha256 stored when each version was made."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    return await _thread(D.verify, request.app, dataset_id, who, bool(body.get("all")))


@router.post("/data/verify-all", dependencies=[require("admin.config")])
async def verify_all(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by}: an admin (ENSEMBLE_ADMINS) re-hashes every version of every dataset that is not deleted. Also runs on a
    timer when ENSEMBLE_VERIFY_EVERY_HOURS is set (no default)."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    if not G.is_admin(who):
        G.refused(request.app.state.db, who, "dataset_verify_all", 403, "not an admin")
        raise HTTPException(403, f"Only an admin (ENSEMBLE_ADMINS) runs verify-all; {who} is not one.")
    return await _thread(D.verify_all, request.app, who)


@router.get("/datasets/{dataset_id}/export", dependencies=[require("data.export", path("dataset_id", "dataset"))])
async def export(dataset_id: str, request: Request, by: str = "", format: str = "zip", unmasked: str = "", reason: str = ""):
    """format=zip: data.csv + schema.json (the tables and their column types, §4.1) + lineage.json + manifest.json (the
    chain to the root, the stored and current sha256). format=csv: the file alone. Audited.
    P20: PII-tagged columns are masked in the file (inside DuckDB's COPY, never after); schema.json marks them and
    the X-Ensemble-Masked header lists them. `unmasked=1&reason=…` exports them in clear to a principal with
    data.export_unmasked, audited as dataset_rows_read."""
    from .. import frames
    from ..lake import mask as M

    who = _who(by)
    if format not in ("zip", "csv"):
        raise HTTPException(400, "format is zip or csv.")
    try:
        m = await _thread(M.for_request, request, dataset_id, None, unmasked=unmasked, reason=reason, by=who, read="export",
                          export=True)
    except M.MaskError as e:
        raise _mask_err(e) from e
    if frames.is_parquet(dataset_id):  # P05: a Parquet version is exported from its lake table
        return await _export_parquet(request, dataset_id, who, format, m)
    man = await _thread(D.export_manifest, request.app, dataset_id, who)
    store = request.app.state.datasets
    fname = f"{man['name']}-v{man['v']}-{dataset_id}".replace("/", "_").replace(" ", "_")
    data_file, data_sha = frames.version_file(dataset_id), man["sha256_file"] or ""
    if m.cols:  # P20: the masked file, written by DuckDB from the masked scan
        data_file, data_sha = await _thread(_masked_csv, request, dataset_id, m)
    mhdr = {"X-Ensemble-Masked": ",".join(m.masked)} if m.cols else {}
    if format == "csv":
        return FileResponse(data_file, media_type="text/csv", filename=f"{fname}.csv",
                            headers={"X-Ensemble-SHA256": data_sha, **mhdr})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(data_file, "data.csv")
        z.writestr("schema.json", json.dumps(_schema_masked(frames.export_schema(dataset_id), m), indent=1))
        z.writestr("lineage.json", json.dumps(store.lineage(dataset_id) or {"root": man["root_id"], "parent": None, "op": "upload"}))
        z.writestr("manifest.json", json.dumps({**man, **({"masked": m.info(), "sha256_data_csv_masked": data_sha} if m.cols else {})},
                                               indent=1))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}.zip"', **mhdr})


def _mask_tag(m: Any) -> str:
    """The cache name part of a masked export: the masks and the team salt (a re-tag makes a new file)."""
    import hashlib

    return hashlib.sha256(json.dumps([sorted(m.cols.items()), m.salt]).encode()).hexdigest()[:12]


def _schema_masked(sch: dict[str, Any], m: Any) -> dict[str, Any]:
    """schema.json with each masked column marked (P20): its values in data.csv are the mask's text."""
    if not m.cols:
        return sch
    for t in sch.get("tables") or []:
        t["columns"] = [{**c, "masked": True, "mask": m.cols[c["name"]], "type": "VARCHAR", "kind": "text",
                         "stored_type": c.get("type")} if c.get("name") in m.cols else c for c in t.get("columns") or []]
    sch["masked_columns"] = m.masked
    return sch


def _masked_csv(request: Request, dataset_id: str, m: Any) -> tuple[Any, str]:
    """A CSV-era version's masked data.csv (P20): DuckDB COPY from the masked scan, cached under lake/exports."""
    import hashlib

    d = request.app.state.settings.lake_dir / "exports" / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"data.masked-{_mask_tag(m)}.csv"
    if not out.exists():
        tmp = d / f".data.{__import__('secrets').token_hex(4)}.csv"
        frames.scan_csv(frames.scan(dataset_id, None, mask_for=m), tmp)
        tmp.replace(out)
    return out, hashlib.sha256(out.read_bytes()).hexdigest()


def _export_csv(request: Request, dataset_id: str, m: Any = None) -> tuple[Any, Any, str]:
    """(the lake table, the CSV path, its sha256): the table written as CSV by DuckDB in __row_id order, text as stored
    (codes keep their zeros), cached under lake/exports/<version>/<table>.csv (re-creatable, never a blob).
    P20: with masked columns, the COPY reads the masked scan (`frames.scan(mask_for=m)`) and the file is
    <table>.masked-<masks>.csv."""
    import hashlib

    from .. import frames
    from ..lake import duck
    from ..lake.writer import _lit, _q

    lt = frames.lake_table(frames.table(dataset_id))
    d = request.app.state.settings.lake_dir / "exports" / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    masked = m is not None and bool(m.cols)
    out = d / (f"{lt.name}.masked-{_mask_tag(m)}.csv" if masked else f"{lt.name}.csv")
    if not out.exists():
        tmp = d / f".{lt.name}.{__import__('secrets').token_hex(4)}.csv"
        if masked:
            frames.scan_csv(frames.scan(dataset_id, lt.name, mask_for=m), tmp)
        else:
            cols = ", ".join(_q(c["name"]) for c in lt.schema)
            src = frames.lake_sql(lt)
            with duck.job() as con:
                con.execute("SET preserve_insertion_order = true")
                con.execute(f"COPY (SELECT {cols} FROM {src} ORDER BY __row_id) TO {_lit(tmp)} (FORMAT csv, HEADER true)")
        tmp.replace(out)
    h = hashlib.sha256()
    with open(out, "rb") as f:
        while b := f.read(8 * 1024 * 1024):
            h.update(b)
    return lt, out, h.hexdigest()


async def _export_parquet(request: Request, dataset_id: str, who: str, format: str, m: Any = None):
    """P05: export of a Parquet version: data.csv from the table (format=csv), or a zip with data.csv, schema.json (the
    manifest's tables and schemas), manifest.json (the stored lake manifest, verbatim) and export.json (the chain, the
    committed table sha and the sha now). Audited as dataset_exported."""
    from .. import frames
    from ..lake import verify as LV
    from . import schema as S

    app = request.app

    def build() -> dict[str, Any]:
        lt, path, csv_sha = _export_csv(request, dataset_id, m)
        read, exp, bad = frames.hash_table(frames.table(dataset_id))
        with app.state.db.connect() as con:
            root = D.root_of(con, dataset_id)
            rows = {r["id"]: r for r in D.tree_rows(con, root)}
            r = rows[dataset_id]
            if r["state"] == D.DELETED:
                raise D.DsError(410, f"{dataset_id} was deleted: only its tombstone and lineage are kept.")
            chain, x = [], r
            while x:
                chain.append({k: x.get(k) for k in ("id", "v", "op", "created_by", "created_at", "rows", "n_cols", "sha256", "parent_id")})
                x = rows.get(x["parent_id"]) if x.get("parent_id") else None
            S.audit(con, who, "dataset_exported", {"dataset_id": dataset_id, "root_id": root, "v": r["v"], "storage": "parquet",
                                                   "table": lt.name, "table_sha256": exp, "csv_sha256": csv_sha,
                                                   "integrity_ok": read == exp, "format": format,
                                                   "masked_columns": m.masked if m is not None else [],
                                                   "unmasked": bool(m is not None and m.unmasked)})
        try:
            _v, man, raw = LV.load_manifest(app.state.db, app.state.lake, dataset_id)
        except Exception:  # noqa: BLE001
            man, raw = None, None
        return {"lt": lt, "path": path, "csv_sha": csv_sha, "name": r["name"], "v": r["v"], "root": root, "chain": chain,
                "table_sha256": exp, "table_sha256_now": read, "integrity_ok": read == exp, "manifest": man, "manifest_raw": raw,
                "target": r["target"], "rows": r["rows"]}

    x = await _thread(build)
    fname = f"{x['name']}-v{x['v']}-{dataset_id}".replace("/", "_").replace(" ", "_")
    hdr = {"X-Ensemble-SHA256": x["csv_sha"], "X-Ensemble-Table-SHA256": x["table_sha256"],
           **({"X-Ensemble-Masked": ",".join(m.masked)} if m is not None and m.cols else {})}
    if format == "csv":
        return FileResponse(x["path"], media_type="text/csv", filename=f"{fname}.csv", headers=hdr)
    schema_json = _schema_masked(frames.export_schema(dataset_id), m) if m is not None else frames.export_schema(dataset_id)
    info = {"dataset_id": dataset_id, "name": x["name"], "v": x["v"], "root_id": x["root"], "target": x["target"], "rows": x["rows"],
            "storage": "parquet", "table": x["lt"].name, "table_sha256_stored": x["table_sha256"],
            "table_sha256_now": x["table_sha256_now"], "integrity_ok": x["integrity_ok"], "csv_sha256": x["csv_sha"],
            "lineage_chain": x["chain"], "exported_at": S.now(), "exported_by": who,
            "how_to_check": "sha256sum data.csv must equal csv_sha256; the Parquet table is checked by GET /api/datasets/{id}/verify",
            **({"masked": {**m.info(), "note": "the PII-tagged columns of data.csv hold their masks (applied inside DuckDB's COPY): "
                                                "csv_sha256 is the masked file's; table_sha256 is the stored table's"}}
               if m is not None and m.cols else {}),
            **({"unmasked": {"reason": m.reason, "columns": getattr(m, "unmasked_columns", [])}} if m is not None and m.unmasked and m.reason else {})}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(x["path"], "data.csv")
        z.writestr("schema.json", json.dumps(schema_json, indent=1))
        if x["manifest_raw"]:
            z.writestr("manifest.json", x["manifest_raw"])
        z.writestr("export.json", json.dumps(info, indent=1))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{fname}.zip"', **hdr})


# -- DL3: edit-as-version, the row grid, and a version from a file changed on disk ----------------------------------------

def _live(request: Request, dataset_id: str) -> dict[str, Any]:
    rec = request.app.state.db.get_dataset(dataset_id)
    if not rec:
        raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
    if rec.get("state") == D.DELETED:
        raise HTTPException(410, f"{dataset_id} was deleted.")
    return rec


def _page_err(e) -> HTTPException:
    return HTTPException(e.status, e.payload())


def _mask_err(e) -> HTTPException:
    """P20: a lake.mask.MaskError (no reason, a refused unmasked read in enforce mode) as its HTTP answer."""
    return HTTPException(e.status, e.payload())


def _src_where(dataset_id: str, table: str | None, q: str, flt: str | None, m: Any = None):
    """(source, WHERE, searched columns) for a page / count: the search `q` ANDed with the filter AST. P20: the source
    is `frames.scan` with the mask `m` (the masked view when None): masked columns are not searched, and a filter on
    one compares masked values."""
    from ..lake import pages as PG
    from ..lake import stats as ST

    src = frames.scan(dataset_id, table, mask_for=m if m is not None else "masked")
    st = ST.get(src.identity)
    if st is None:
        ST.schedule(dataset_id, table, src.identity)  # the profile job; search narrows its columns once it is done
    conds, searched = [], None
    if q.strip():
        w, searched = PG.search_where(src, q, st)
        conds.append(w)
    fw = PG.filter_where(src, flt)
    if fw != "true":
        conds.append(fw)
    return src, " AND ".join(f"({c})" for c in conds) or "true", searched


@router.get("/datasets/{dataset_id}/grid", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
async def grid(dataset_id: str, request: Request, offset: int = 0, limit: int = 50, q: str = "", by: str = "",
               after: int | None = None, filter: str | None = None, sort: str | None = None, desc: bool = False,
               table: str | None = None, unmasked: str = "", reason: str = "") -> dict[str, Any]:
    """The rows of a version, a page at a time, for the editor (P08: DuckDB, never a full read). `_row` is the row's
    position and `_row_id` its `__row_id` (what an edit names, P07). `offset` jumps to a position; `after` (the last
    `_row_id`) pages on by keyset. `q` keeps rows where any column contains it (case-insensitive); `filter` is the edit
    filter AST (JSON). `matching` is counted within 3 s (null when that runs out). `sort` is refused above 1M rows.
    A read of the rows: needs a name (by or the X-Ensemble-Actor header) and is audited as dataset_read.
    P20: PII columns are masked (`masked_columns`, their `masked: true`); `unmasked=1&reason=…` as on …/rows."""
    from ..lake import mask as M
    from ..lake import pages as PG

    who = G.reader(by)
    _live(request, dataset_id)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    def page() -> dict[str, Any]:
        try:
            m = M.for_request(request, dataset_id, table, unmasked=unmasked, reason=reason, by=who, read="grid")
            src, where, _ = _src_where(dataset_id, table, q, filter, m)
            pg = PG.page(src, after=after, offset=None if after is not None else offset, limit=limit, where=where,
                         sort=sort, desc=desc)
            cnt = PG.count(src, where)
        except PG.PageError as e:
            raise _page_err(e) from e
        except M.MaskError as e:
            raise _mask_err(e) from e
        return {"dataset_id": dataset_id, "table": src.table,
                "columns": [{"name": c, "kind": PG.kind(t), "type": t, **({"masked": True, "mask": m.cols[c]} if c in m.cols else {})}
                            for c, t in src.columns],
                "rows": pg["rows"], "total": src.n_rows, "matching": cnt["count"], "matching_timed_out": cnt["timed_out"],
                "offset": offset, "limit": limit, "q": q, "next_after": pg["next_after"], "how": pg["how"], **m.info()}

    out = await _thread(page)
    G.read_audit(request.app.state.db, "grid", dataset_id, {"q": q[:80] or None, "offset": offset}, by=who)
    return out


@router.get("/datasets/{dataset_id}/rows/count", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
async def rows_count(dataset_id: str, request: Request, q: str = "", filter: str | None = None, table: str | None = None,
                     budget: float = 3.0) -> dict[str, Any]:
    """P08 (R5): how many rows match a search / filter, as its own cancellable call: stopped after `budget` seconds
    (≤ 3) with {count: null, timed_out: true}. A count shows no values, so it needs no name."""
    from ..lake import pages as PG

    _live(request, dataset_id)

    def run() -> dict[str, Any]:
        try:
            src, where, searched = _src_where(dataset_id, table, q, filter)
            return {"dataset_id": dataset_id, "table": src.table, "q": q, "searched_columns": searched, "total": src.n_rows,
                    **PG.count(src, where, max(0.1, min(float(budget), PG.COUNT_BUDGET_S)))}
        except PG.PageError as e:
            raise _page_err(e) from e

    return await _thread(run)


@router.post("/datasets/{dataset_id}/rows/preview", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
async def filter_preview(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """P08: filter preview: {filter (the edit filter AST), q?, limit?, by} → the first rows that match and how many
    (counted within 3 s). Nothing is written; what a `filter_rows` / `delete_rows` edit with this filter would touch."""
    from ..lake import pages as PG

    body = body or {}
    who = G.reader(str(body.get("by") or ""))
    _live(request, dataset_id)
    flt = body.get("filter")

    def run() -> dict[str, Any]:
        from ..lake import mask as M

        try:
            m = M.for_request(request, dataset_id, body.get("table"), unmasked=body.get("unmasked"), reason=body.get("reason"),
                              by=who, read="filter_preview")
            src, where, searched = _src_where(dataset_id, body.get("table"), str(body.get("q") or ""),
                                              json.dumps(flt) if flt is not None else None, m)
            pg = PG.page(src, limit=int(body.get("limit") or 20), where=where)
            cnt = PG.count(src, where)
        except PG.PageError as e:
            raise _page_err(e) from e
        except M.MaskError as e:
            raise _mask_err(e) from e
        return m.scrub({"dataset_id": dataset_id, "table": src.table, "filter": flt, "rows": pg["rows"], "next_after": pg["next_after"],
                        "matching": cnt["count"], "matching_timed_out": cnt["timed_out"], "total": src.n_rows,
                        "share": round(cnt["count"] / src.n_rows, 6) if cnt["count"] is not None and src.n_rows else None,
                        **m.info()})  # P20: a filter value on a masked column is not echoed

    out = await _thread(run)
    from ..lake import mask as M

    m = await _thread(M.masked_view, dataset_id)  # P20: the audit row keeps the filter with a masked column's value masked
    G.read_audit(request.app.state.db, "filter_preview", dataset_id,
                 {"filter": json.dumps(m.scrub(flt) if m is not None else flt, ensure_ascii=False)[:200] if flt else None}, by=who)
    return out


@router.get("/datasets/{dataset_id}/stats", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
async def table_stats(dataset_id: str, request: Request, table: str | None = None, wait: float = 0) -> dict[str, Any]:
    """P08: the table's column statistics (table_stats, keyed by table_sha256): rows, nulls, distinct, min / max /
    mean / std / quantiles / histogram for numbers, length and top values for text, ... `status` is `running` while the
    background profile job computes them (it is started here when missing); `wait` (≤ 30 s) waits for it."""
    from ..lake import mask as M
    from ..lake import pages as PG
    from ..lake import stats as ST

    _live(request, dataset_id)

    def run() -> dict[str, Any]:
        try:
            src = PG.source(dataset_id, table)
        except PG.PageError as e:
            raise _page_err(e) from e
        st = ST.ensure(dataset_id, table, wait_s=max(0.0, min(float(wait or 0), 30.0)), n_rows=src.n_rows)
        return M.stats(ST.public(st, dataset_id, src.table, src.identity), M.masked_view(dataset_id, src.table))

    return await _thread(run)


@router.post("/datasets/{dataset_id}/edit", status_code=201, dependencies=[require("data.version", path("dataset_id", "dataset"))])
async def edit(dataset_id: str, request: Request, body: dict[str, Any]):
    """{ops: [...], reason, by, preview?, make_default?}: typed operations (set_cells, delete_rows, add_rows, filter_rows,
    rename_column, drop_column, add_column (constant or a safe formula), cast_column, replace_values, fill_missing) applied
    to this version and committed as a NEW version (lineage, root rows and holdout carried over, a change log with old →
    new values, an audit row). Nothing is modified in place. preview=true writes nothing. Like an upload, a version with
    contract warnings is not made the default. The same bytes as an existing version return that version (created:
    false)."""
    from ..lake.values import SafeJSONResponse

    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    preview = bool(body.get("preview"))
    reason = str(body.get("reason") or "").strip()
    if not preview and not 3 <= len(reason) <= 500:
        raise HTTPException(400, "An edit needs a reason (3–500 characters): it is kept with the version and in the audit log.")
    make_default = body.get("make_default", "auto")
    if make_default not in ("auto", "yes", "no"):
        raise HTTPException(400, "make_default is auto, yes or no.")
    _live(request, dataset_id)
    store = request.app.state.datasets
    from ..lake import mask as M
    from ..lake.values import ValueRefused

    m = await _thread(M.masked_view, dataset_id)
    sent = M.tagged_values(m, body.get("ops")) if m is not None else {}

    def scrubbed(detail: Any) -> Any:  # P20: an error never echoes a value sent for a PII-tagged column
        return M.scrub_error(detail, m, sent) if sent else detail

    try:
        if preview:
            out = await _thread(store.edit_version, dataset_id, body.get("ops"), by=who, reason=reason, preview=True,
                                set_target=body.get("set_target"), acknowledge=body.get("acknowledge"))
            return SafeJSONResponse(m.scrub(out) if m is not None else out, status_code=200)  # P03: non-finite → null
        before = await _thread(D.current_default, request.app, dataset_id)
        try:
            out = await _thread(store.edit_version, dataset_id, body.get("ops"), by=who, reason=reason, set_target=body.get("set_target"),
                                acknowledge=body.get("acknowledge"))
        except HTTPException as e:
            if e.status_code == 409:
                G.refused(request.app.state.db, who, "dataset_edit", 409, scrubbed(e.detail), {"dataset_id": dataset_id})
            raise
    except ValueRefused as e:
        if sent:
            raise HTTPException(e.status, scrubbed(dict(e.payload))) from None
        raise
    except HTTPException as e:
        if sent and 400 <= e.status_code < 500:
            raise HTTPException(e.status_code, scrubbed(e.detail)) from None
        raise
    from ..main import _detail

    if not out.get("created"):
        return SafeJSONResponse(json.loads(json.dumps({**_detail(out), "created": False, "duplicate_of": out["id"], "edit": out.get("edit"),
                                                   "note": "the result has the same bytes as this existing version: nothing new was made"},
                                                  default=str)), status_code=200)
    warnings = out["edit"]["checks"]["warnings"]
    dflt = await _thread(D.settle_default, request.app, out["id"], before, warnings, who,
                         make_default)
    edit_out = {k: v for k, v in out["edit"].items()}
    return {**_detail(request.app.state.db.get_dataset(out["id"]) or out), "created": True,
            "edit": m.scrub(edit_out) if m is not None else edit_out, "default": dflt}


@router.post("/datasets/{dataset_id}/adopt-disk-file", status_code=201, dependencies=[require("data.restore", path("dataset_id", "dataset"))])
async def adopt_disk_file(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, reason}: the remedy for a version whose data.csv was changed outside the app (verify says MISMATCH). The
    bytes now on disk are committed as a NEW version of it (upload-as-version: rows matched by an exact hash, lineage,
    audit), so they are trusted only as a new, named, reasoned version; the old version stays flagged. Its file must
    still be restored (from an export or a backup) before it can train again without an admin override."""
    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    rec = _live(request, dataset_id)
    store = request.app.state.datasets
    from .schema import file_sha256

    p = frames.version_file(dataset_id)  # the bytes on disk, hashed and adopted as they are
    now_sha = file_sha256(p)
    if not now_sha:
        raise HTTPException(409, f"{dataset_id}'s file is missing: there is nothing on disk to adopt.")
    if now_sha == rec.get("sha256"):
        raise HTTPException(409, f"{dataset_id}'s file matches its stored sha256: nothing changed on disk.")
    content = p.read_bytes()
    before = await _thread(D.current_default, request.app, dataset_id)
    out = await _thread(store.upload_version, dataset_id, f"{dataset_id}-on-disk.csv", content, by=who, row_key="hash",
                        reason=f"from the file changed on disk ({now_sha[:12]}…): {reason}")
    with request.app.state.db.connect() as con:
        from .schema import audit

        audit(con, who, "dataset_disk_file_adopted", comment=reason,
              details={"dataset_id": out["id"], "root_id": rec.get("root_id") or dataset_id, "from": dataset_id,
                       "sha256_on_disk": now_sha, "sha256_stored": rec.get("sha256"),
                       "note": "the parent's file still differs from its version: restore it, or train with an admin override"})
    dflt = await _thread(D.settle_default, request.app, out["id"], before, [{"code": "adopted_from_disk"}], who, "no")
    from ..main import _detail

    return {**_detail(request.app.state.db.get_dataset(out["id"]) or out), "upload": out["upload"], "default": dflt,
            "parent_still_mismatched": True}


@router.post("/datasets/{dataset_id}/restore-splits", dependencies=[require("data.restore", path("dataset_id", "dataset"))])
async def restore_splits(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """DL4 (M-d): {by, reason}: the remedy for a split file (holdout, folds) changed outside the app. Every split file of
    this version whose bytes differ from the copy recorded when it was drawn is written back (the copy is hash-checked
    first), the version is verified again, and both steps are audited. The data file is not touched."""
    body = body if isinstance(body, dict) else {}
    if frames.is_parquet(dataset_id):
        return await _restore_lake_splits(dataset_id, request, body)
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    rec = _live(request, dataset_id)
    store = request.app.state.datasets
    before = store.check_splits(dataset_id)
    if all(x["status"] == "ok" for x in before):
        raise HTTPException(409, f"{dataset_id}'s split files match the hashes recorded when they were drawn: nothing to restore.")
    restored = await _thread(store.restore_split_files, dataset_id)
    with request.app.state.db.connect() as con:
        from .schema import audit

        audit(con, who, "dataset_splits_restored", comment=reason,
              details={"dataset_id": dataset_id, "root_id": rec.get("root_id") or dataset_id, "restored": restored,
                       "before": [{k: x[k] for k in ("file", "status", "stored", "now")} for x in before]})
    check = await _thread(D.verify, request.app, dataset_id, who)
    return {"dataset_id": dataset_id, "restored": restored, "verify": check}


async def _restore_lake_splits(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """P09 (§4.3): restore from run pins. Without `blob_sha256` it only offers: per failing split, the split the
    earliest run on it used (that run's pinned blob, re-hashed). With {split_key, blob_sha256, by, reason} it puts
    that split back (the failing row is replaced in one audited transaction) and verifies the version again."""
    from ..lake import splitstore as SS

    _live(request, dataset_id)
    db, lake = request.app.state.db, request.app.state.lake
    offers = await _thread(SS.restore_offer, db, lake, dataset_id)
    if not body.get("blob_sha256"):
        if not offers:
            raise HTTPException(409, {"code": "nothing_to_restore", "message": f"Every split of {dataset_id} passes its five "
                                      "checks: nothing to restore."})
        return {"dataset_id": dataset_id, "applied": False, "offers": offers,
                "how": "send {split_key, blob_sha256, by, reason} to put the offered split back (audited)"}
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    key = str(body.get("split_key") or (offers[0]["split_key"] if len(offers) == 1 else ""))
    try:
        res = await _thread(SS.restore, db, lake, dataset_id, split_key=key, blob_sha256=str(body["blob_sha256"]), by=who,
                            reason=reason)
    except SS.SplitError as e:
        raise HTTPException(e.status, e.payload) from e
    check = await _thread(D.verify, request.app, dataset_id, who)
    return {"dataset_id": dataset_id, "applied": True, **res, "verify": check}


@router.post("/datasets/match", dependencies=[require("data.upload", query("project_id", "project"))])
async def match_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """DL4 (#11): does this CSV look like a dataset already here? Nothing is written. Compares the file's columns,
    its likely target and its name with every live dataset (its default version); answers the best match (or none)
    with the reasons, so Build can offer "Add as a new version of <dataset>" next to "Create a new dataset"."""
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return await _thread(D.match_upload, request.app, file.filename or "", content)


# -- P05 (§5.1, R10): staged uploads → check → commit, a new single-table Parquet dataset ------------------------------

def _lake_err(e) -> HTTPException:
    return HTTPException(e.status, e.payload)


async def _lthread(fn, *a, **k):
    from ..lake.writer import LakeError

    try:
        return await run_in_threadpool(fn, *a, **k)
    except LakeError as e:
        raise _lake_err(e) from e
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e


class _MultipartSink:
    """A streaming multipart/form-data receiver: file parts go straight to lake/tmp/<up>/<n>.src (sha256 on the fly),
    small text fields are kept. Fed from request.stream() in batches on a worker thread; nothing else is buffered."""

    FIELD_MAX = 64 * 1024

    def __init__(self, boundary: bytes, tmp, cap: int) -> None:
        from python_multipart.multipart import MultipartParser

        self.tmp, self.cap = tmp, cap
        self.fields: dict[str, str] = {}
        self.files: list[dict[str, Any]] = []
        self._hdr_f, self._hdr_v = b"", b""
        self._headers: dict[bytes, bytes] = {}
        self._sink = None
        self._field: tuple[str, bytearray] | None = None
        self.parser = MultipartParser(boundary, {
            "on_part_begin": self._begin, "on_header_field": self._hf, "on_header_value": self._hv,
            "on_header_end": self._he, "on_headers_finished": self._hdone, "on_part_data": self._data, "on_part_end": self._end})

    def _begin(self) -> None:
        self._headers = {}

    def _hf(self, d: bytes, s: int, e: int) -> None:
        self._hdr_f += d[s:e]

    def _hv(self, d: bytes, s: int, e: int) -> None:
        self._hdr_v += d[s:e]

    def _he(self) -> None:
        self._headers[self._hdr_f.lower()] = self._hdr_v
        self._hdr_f, self._hdr_v = b"", b""

    def _hdone(self) -> None:
        from python_multipart.multipart import parse_options_header

        from ..lake.upload import SinkFile

        _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
        name = (opts.get(b"name") or b"").decode("utf-8", "replace")
        fn = opts.get(b"filename")
        if fn is not None:
            n = len(self.files) + (1 if self._sink else 0)
            self._sink = SinkFile(self.tmp / f"{n}.src", fn.decode("utf-8", "replace").replace("\\", "/").rsplit("/", 1)[-1], n, self.cap)
            self._field = None
        else:
            self._field = (name, bytearray())

    def _data(self, d: bytes, s: int, e: int) -> None:
        if self._sink is not None:
            self._sink.write(d[s:e])
        elif self._field is not None:
            buf = self._field[1]
            if len(buf) + (e - s) > self.FIELD_MAX:
                from ..lake.writer import LakeError

                raise LakeError(400, "field_too_long", f"Form field {self._field[0]!r} is longer than 64 KB.")
            buf += d[s:e]

    def _end(self) -> None:
        if self._sink is not None:
            self.files.append(self._sink.close())
            self._sink = None
        elif self._field is not None:
            self.fields[self._field[0]] = self._field[1].decode("utf-8", "replace")
            self._field = None

    def feed(self, data: bytes) -> None:
        self.parser.write(data)

    def close(self) -> None:
        if self._sink is not None:  # an aborted part
            self._sink.close()
            self._sink = None


@router.post("/uploads", status_code=201, dependencies=[require("data.upload", query("parent_version_id", "dataset"), query("root_id", "dataset"), query("project_id", "project"))])
async def create_upload(request: Request, filename: str | None = None, by: str | None = None, project_id: str | None = None,
                        name: str | None = None, target: str | None = None, parent_version_id: str | None = None,
                        root_id: str | None = None) -> dict[str, Any]:
    """Stage an upload (§5.1 step 1). multipart/form-data with one file part (`file`) and optional fields by, name,
    target, project_id; or the raw file as the body (any other content type) with ?filename=…. The bytes stream to the
    lake's tmp directory with sha256 on the fly; the cap is ENSEMBLE_MAX_UPLOAD_GB (5) and the disk must have 3x free.
    P06: `parent_version_id` (or `root_id`: its default version) makes it the next version of that dataset; the check
    then shows the match candidates and the commit needs `match`."""
    from python_multipart.multipart import parse_options_header

    from ..lake import upload as U
    from ..lake.writer import LakeError

    db, lake = request.app.state.db, request.app.state.lake
    ctype, opts = parse_options_header(request.headers.get("content-type", ""))
    clen = int(request.headers.get("content-length") or 0)
    cap = U.max_upload_bytes()
    if clen and clen > cap + 1024 * 1024:
        raise HTTPException(413, {"code": "too_large", "message": f"The upload is larger than {cap / 1024 ** 3:g} GB (ENSEMBLE_MAX_UPLOAD_GB)."})
    is_mp = ctype == b"multipart/form-data"
    who0 = (by or G.current_actor() or "").strip()
    plan0 = {k: v for k, v in (("name", name), ("target", target)) if v}
    try:
        if parent_version_id or root_id:
            plan0["parent_version_id"] = await run_in_threadpool(U.resolve_parent, db, parent_version_id, root_id)
        up, tmp = await run_in_threadpool(U.new_upload, db, lake, by=who0 or "unknown", project_id=project_id, plan=plan0)
        await run_in_threadpool(U.disk_guard, tmp, clen)
    except LakeError as e:
        raise _lake_err(e) from e
    batch, size = [], 0
    try:
        if is_mp:
            if not opts.get(b"boundary"):
                raise LakeError(400, "bad_multipart", "multipart/form-data without a boundary.")
            sink = _MultipartSink(opts[b"boundary"], tmp, cap)
            try:
                async for chunk in request.stream():
                    batch.append(chunk)
                    size += len(chunk)
                    if size >= 4 * 1024 * 1024:
                        await run_in_threadpool(sink.feed, b"".join(batch))
                        batch, size = [], 0
                if batch:
                    await run_in_threadpool(sink.feed, b"".join(batch))
            finally:
                sink.close()
            files, fields = sink.files, sink.fields
        else:
            fn = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            if not fn:
                raise LakeError(400, "filename_required", "Send the file as multipart/form-data, or the raw body with ?filename=….")
            f = U.SinkFile(tmp / "0.src", fn, 0, cap)
            try:
                async for chunk in request.stream():
                    batch.append(chunk)
                    size += len(chunk)
                    if size >= 4 * 1024 * 1024:
                        await run_in_threadpool(f.write, b"".join(batch))
                        batch, size = [], 0
                if batch:
                    await run_in_threadpool(f.write, b"".join(batch))
            finally:
                info = f.close()
            files, fields = [info], {}
        if not files:
            raise LakeError(400, "no_file", "The upload has no file part (send it as the form field 'file').")
        who = _who((fields.get("by") or by or G.current_actor() or "").strip())
        plan = {**plan0, **{k: fields[k].strip() for k in ("name", "target") if fields.get(k, "").strip()}}
        if (fields.get("parent_version_id") or fields.get("root_id") or "").strip():
            plan["parent_version_id"] = await run_in_threadpool(U.resolve_parent, db, (fields.get("parent_version_id") or "").strip()
                                                                or None, (fields.get("root_id") or "").strip() or None)
        pid = (fields.get("project_id") or project_id or "").strip() or None
        if plan.get("parent_version_id"):
            pid = None  # the parent's root decides the project

        def save() -> dict[str, Any]:
            with db.connect() as con:
                if pid and not con.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                    raise LakeError(404, "project_not_found", f"No project '{pid}'.")
                from . import schema as S

                con.execute("UPDATE uploads SET files_json = ?, plan_json = ?, created_by = ?, project_id = ?, team_id = ? WHERE id = ?",
                            (json.dumps(files), json.dumps({**plan, **({"project_id": pid} if pid else {})}), who, pid or "",
                             S.team_of_project(con, pid), up))
                if plan.get("parent_version_id"):
                    U._set_parent_cols(con, up, plan["parent_version_id"])
                S.audit(con, who, "upload_staged", {"upload_id": up, "files": [{k: f[k] for k in ("filename", "bytes", "sha256")}
                                                                                for f in files], "project_id": pid,
                                                    "parent_version_id": plan.get("parent_version_id")})
            return U.public(U.get(db, up))

        return await run_in_threadpool(save)
    except BaseException as e:
        await run_in_threadpool(U.discard, db, lake, up, "failed while receiving")
        if isinstance(e, LakeError):
            raise _lake_err(e) from e
        raise


@router.get("/uploads/{up}")
async def get_upload(up: str, request: Request) -> dict[str, Any]:
    from ..lake import upload as U

    return await _lthread(lambda: U.public(U.get(request.app.state.db, up)))


@router.patch("/uploads/{up}", dependencies=[require("data.upload", path("up", "upload"))])
async def patch_upload(up: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """The plan: {name?, target?, project_id?, table?, types?: {column: VARCHAR|BIGINT|DOUBLE|BOOLEAN|DATE|TIMESTAMP},
    formats?: {column: strptime format}, parent_version_id? | root_id? (P06: the next version of that dataset),
    match?: {method: key|declared|row_hash|none, columns?} (the check simulates it)}. Declared types win over the
    import's choice. A changed plan needs a new check."""
    from ..lake import upload as U

    return await _lthread(lambda: U.public(U.patch_plan(request.app.state.db, up, body if isinstance(body, dict) else {})))


@router.post("/uploads/{up}/check", dependencies=[require("data.upload", path("up", "upload"))])
async def check_upload(up: str, request: Request) -> dict[str, Any]:
    """Detect and type the staged file (§5.1 steps 2–3, 6): per table the decided schema with a reason per column,
    warnings (with examples), blocks, a preview, and check_sha256. Writes nothing but tmp files."""
    from ..lake import upload as U

    db, lake = request.app.state.db, request.app.state.lake
    out = await _lthread(U.check, db, lake, up, request.app.state.datasets)
    return await run_in_threadpool(_pii_suggestions, db, up, out)


def _pii_suggestions(db: Any, up: str, out: dict[str, Any]) -> dict[str, Any]:
    """P20 (C §5 /check): per table, the PII tag each column will get: the root's tag when it has one (a new version
    of a tagged dataset), else the profiler's suggestion (names, then the values of the preview rows)."""
    from .. import profiling
    from ..lake import mask as M

    try:
        with db.connect() as con:
            u = con.execute("SELECT parent_version_id, root_id FROM uploads WHERE id = ?", (up,)).fetchone()
            root = None
            if u and (u["root_id"] or u["parent_version_id"]):
                r = con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (u["root_id"] or u["parent_version_id"],)).fetchone()
                root = r[0] if r else u["root_id"]
            for t in out.get("tables") or []:
                have = M.tags(con, root, t["name"]) if root else {}
                cols = (t.get("preview") or {}).get("columns") or [c.get("name") for c in t.get("schema") or []]
                rows = (t.get("preview") or {}).get("rows") or []
                sugg = []
                for i, c in enumerate(cols):
                    if c in have:
                        x = have[c]
                        if x["tag"] != "none":
                            sugg.append({"column": c, "tag": x["tag"], "mask": x["mask"], "source": x["source"], "from": "the dataset's tags"})
                        continue
                    sg = profiling.pii_suggestion(c, [r[i] for r in rows if i < len(r)])
                    if sg:
                        sugg.append({"column": c, "tag": sg["tag"], "mask": sg["mask"], "source": "suggested", "why": sg["why"]})
                t["pii"] = sugg
    except Exception:  # suggestions never fail a check
        import logging

        logging.getLogger(__name__).exception("PII suggestions for %s", up)
    return out


@router.post("/uploads/{up}/commit", status_code=201, dependencies=[require("data.upload", path("up", "upload"))])
async def commit_upload(up: str, request: Request, body: dict[str, Any] | None = None):
    """{by?, reason?, check_sha256?, make_default?, match?, acknowledge?}: the checked upload becomes a new Parquet
    dataset (or, with a parent, its next version) through the VersionWriter (job_id = the upload id). 201 with the
    version; a repeat returns the same version (200, repeat: true). 409 when it was not checked, the plan or check
    changed (check_stale), or the check blocks. P06, next version: 400 match_required without `match`; 400
    acknowledge_required when the chosen match loses ≥ 1 % of the holdout's identities or a better key exists;
    400 key_invalid for a key that is not unique and present; an identical version returns it (deduped: data)."""
    from ..lake import upload as U
    from ..lake.values import SafeJSONResponse

    body = body if isinstance(body, dict) else {}
    db, lake = request.app.state.db, request.app.state.lake
    who = _who((body.get("by") or G.current_actor() or "").strip())
    reason = str(body.get("reason") or G.current_reason() or "").strip()[:500]

    def run() -> tuple[dict[str, Any], bool]:
        ver, created = U.commit(db, lake, up, by=who, reason=reason, check_sha256=body.get("check_sha256"),
                                make_default=body.get("make_default"), match=body.get("match"),
                                acknowledge=body.get("acknowledge"), dstore=request.app.state.datasets)
        rec0 = request.app.state.db.get_dataset(ver["version_id"]) or {}
        if created or "label_candidates" not in (rec0.get("profile") or {}):  # (a repeat finishes an interrupted profile)
            chk = U.get(db, up).get("check") or {}
            rec = request.app.state.datasets.finish_lake_version(ver["version_id"], target=(U.get(db, up)["plan"] or {}).get("target")
                                                                 or ver.get("target"), chk=chk, by=who)
            ver["profile_target"] = rec.get("target")
            ver["kind"] = rec.get("kind")
        return ver, created

    ver, created = await _lthread(run)
    return SafeJSONResponse({**ver, "repeat": not created and not ver.get("deduped")}, status_code=201 if created else 200)


@router.delete("/uploads/{up}", dependencies=[require("data.upload", path("up", "upload"))])
async def discard_upload(up: str, request: Request) -> dict[str, Any]:
    from ..lake import upload as U

    db, lake = request.app.state.db, request.app.state.lake

    def run() -> dict[str, Any]:
        u = U.get(db, up)
        if u["status"] == "committed":
            from ..lake.writer import LakeError

            raise LakeError(409, "upload_committed", f"Upload {up} is committed ({u['committed_version_id']}).")
        U.discard(db, lake, up)
        return U.public(U.get(db, up))

    return await _lthread(run)

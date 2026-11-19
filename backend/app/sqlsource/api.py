"""G9 routes: load a dataset from a SQL query, and refresh it into a new version.

    GET  /api/sql/demo                       the demo database (URL, example query, tables) built from the samples
    POST /api/sql/tables                     {url, password?} → tables and views with their columns
    POST /api/sql/preview                    {url, query, limit?, password?} → columns, the first 20 rows, rows fetched
    POST /api/sql/datasets                   {url, query, limit?, name?, target?, password?} → a dataset (lineage: the query)
    POST /api/sql/datasets/{id}/refresh      {password?} → the same query again, as the next version (lineage: parent, diff)
    GET  /api/sql/datasets/{id}/versions     every version of the query's dataset, oldest first

Credentials travel in the request only. Lineage stores the URL with the password masked (`***`); a refresh of a source
that had one needs `password` again.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..datasets import DatasetError
from ..http import dataset_or_404
from . import core
from .core import SqlError

router = APIRouter(prefix="/api/sql")


class Conn(BaseModel):
    url: str = Field(core.DEMO_URL, description="sqlite:///file, duckdb:///file (under the data directory) or postgresql://…")
    password: str | None = Field(None, description="Used for this request only, never stored.")


class Query(Conn):
    query: str = core.DEMO_QUERY
    limit: int = Field(core.DEFAULT_LIMIT, ge=1, le=core.MAX_LIMIT)


class Save(Query):
    name: str | None = None
    target: str | None = None
    created_by: str | None = Field(None, description="Who loads it (no sign-in; recorded in the audit log).")


class Refresh(BaseModel):
    password: str | None = None
    created_by: str | None = None


def _audit(request: Request, actor: str | None, action: str, rec: dict[str, Any], comment: str, **details: Any) -> None:
    """A SQL load or refresh in the registry's audit log: who ran which query, from where, into which dataset. The URL
    is the redacted one; the password is never stored."""
    lin = (rec.get("profile") or {}).get("lineage") or {}
    request.app.state.registry.audit(actor=(actor or "").strip() or "unknown", action=action, comment=comment,
                                     details={"dataset_id": rec["id"], "dataset": rec["name"], "url": lin.get("url"),
                                              "query": lin.get("query"), "rows": lin.get("rows"), "version": lin.get("version"),
                                              "data_sha": lin.get("data_sha"), **details})


def _data_dir(request: Request):
    st = request.app.state
    core.ensure_demo_db(st.settings.data_dir, st.datasets)
    return st.settings.data_dir


def _columns(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [{"name": c, "dtype": str(df[c].dtype), "missing": int(df[c].isna().sum())} for c in df.columns]


def _rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.head(core.PREVIEW_ROWS).astype(object).where(df.head(core.PREVIEW_ROWS).notna(), None).to_dict("records")


@router.get("/demo")
def demo(request: Request) -> dict[str, Any]:
    dd = _data_dir(request)
    try:
        tabs = core.tables(core.DEMO_URL, dd)
    except SqlError as e:
        raise HTTPException(400, str(e)) from e
    return {"url": core.DEMO_URL, "query": core.DEMO_QUERY, "tables": tabs, "limit": core.DEFAULT_LIMIT,
            "max_limit": core.MAX_LIMIT, "note": "A SQLite file built from the bundled samples: applications + outcomes "
            "(credit-default), loans (loan-stream) and accounts (account-churn)."}


@router.post("/tables")
async def list_tables(body: Conn, request: Request) -> dict[str, Any]:
    try:
        return {"tables": await run_in_threadpool(core.tables, body.url, _data_dir(request), body.password)}
    except SqlError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/preview")
async def preview(body: Query, request: Request) -> dict[str, Any]:
    try:
        r = await run_in_threadpool(core.run_query, body.url, body.query, _data_dir(request), limit=body.limit,
                                    password=body.password)
    except SqlError as e:
        raise HTTPException(400, str(e)) from e
    df = r["frame"]
    return {"columns": _columns(df), "rows": _rows(df), "n_rows": len(df), "truncated": r["truncated"],
            "limit": r["limit"], "seconds": r["seconds"], "url": r["src"]["redacted"], "dialect": r["src"]["dialect"]}


def _save(request: Request, res: dict[str, Any], *, name: str, target: str | None, version: int, root: str | None,
          parent: str | None, base_name: str, diff: dict[str, int] | None = None) -> dict[str, Any]:
    df = res["frame"]
    lin = core.lineage_record(res, df, version=version, root=root, parent=parent, base_name=base_name, diff=diff)
    tgt = target if target and target in df.columns else None
    return request.app.state.datasets.create_from_frame(
        df, name=name, filename=f"{base_name}.sql", note=f"SQL query · {lin['url']} · v{version}", target=tgt, lineage=lin)


@router.post("/datasets", status_code=201)
async def create(body: Save, request: Request) -> dict[str, Any]:
    from ..main import _detail

    dd = _data_dir(request)
    try:
        res = await run_in_threadpool(core.run_query, body.url, body.query, dd, limit=body.limit, password=body.password)
        base = (body.name or "").strip() or f"{core.first_table(res['query']) or 'query'} (SQL)"
        rec = await run_in_threadpool(lambda: _save(request, res, name=base, target=body.target, version=1, root=None,
                                                    parent=None, base_name=base))
    except (SqlError, DatasetError) as e:
        raise HTTPException(400, str(e)) from e
    _audit(request, body.created_by, "dataset_loaded", rec, f"SQL load into {rec['name']}")
    return _detail(rec)


def _family(request: Request, root: str) -> list[dict[str, Any]]:
    out = [r for r in request.app.state.db.list_datasets() if ((r["profile"].get("lineage") or {}).get("root") == root
                                                                and (r["profile"].get("lineage") or {}).get("source") == "sql")]
    return sorted(out, key=lambda r: r["profile"]["lineage"].get("version", 0))


@router.post("/datasets/{dataset_id}/refresh", status_code=201)
async def refresh(dataset_id: str, request: Request, body: Refresh | None = None) -> dict[str, Any]:
    """Run the stored query again into a new dataset: the next version, with the parent, the row diff and both
    data hashes in its lineage. The confirmed target carries over."""
    from ..main import _detail

    rec = dataset_or_404(request, dataset_id)
    lin = request.app.state.datasets.lineage(dataset_id) or {}
    if lin.get("source") != "sql":
        raise HTTPException(400, "This dataset was not loaded from a SQL query, so there is nothing to refresh.")
    pw = (body.password if body else None) or None
    if lin.get("needs_password") and not pw:
        raise HTTPException(400, "This source needs its password again: credentials are never stored.")
    dd = _data_dir(request)
    url = lin["url"]
    if lin.get("needs_password"):
        url = url.replace(":***@", "@", 1)  # the masked password is replaced by the one sent now
    fam = _family(request, lin["root"])
    version = max((r["profile"]["lineage"].get("version", 1) for r in fam), default=1) + 1
    try:
        res = await run_in_threadpool(core.run_query, url, lin["query"], dd, limit=lin["limit"], password=pw)
        old = await run_in_threadpool(request.app.state.datasets.load_frame, dataset_id)
        new = pd.read_csv(pd.io.common.StringIO(res["frame"].to_csv(index=False)))  # compare as the engine reads it
        diff = core.row_diff(old, new)
        target = rec["target"] if rec["profile"].get("target_source") in ("given", "confirmed") else None
        base = lin.get("base_name") or rec["name"]
        out = await run_in_threadpool(lambda: _save(request, res, name=f"{base} · v{version}", target=target, version=version,
                                                    root=lin["root"], parent=dataset_id, base_name=base, diff=diff))
    except (SqlError, DatasetError) as e:
        raise HTTPException(400, str(e)) from e
    _audit(request, body.created_by if body else None, "dataset_refreshed", out, f"SQL refresh of {dataset_id} → v{version}",
           parent=dataset_id, diff=diff)
    return {**_detail(out), "refresh": {"parent": dataset_id, "version": version, "diff": diff,
                                        "unchanged": out["profile"]["lineage"]["data_sha"] == lin.get("data_sha")}}


@router.get("/datasets/{dataset_id}/versions")
def versions(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    lin = request.app.state.datasets.lineage(dataset_id) or {}
    if lin.get("source") != "sql":
        return {"dataset_id": dataset_id, "versions": []}
    return {"dataset_id": dataset_id, "root": lin["root"], "query": lin["query"], "url": lin["url"],
            "versions": [{"id": r["id"], "name": r["name"], "rows": r["rows"], "created_at": r["created_at"],
                          **{k: r["profile"]["lineage"].get(k) for k in ("version", "parent", "data_sha", "diff", "truncated", "fetched_at")}}
                         for r in _family(request, lin["root"])]}

"""D2 Feature Lab routes.

    POST /api/datasets/{id}/feature-lab            {target?, entity?, candidates?, max_candidates?, time_limit?, apply?, by?}
                                                   → 202 {job}; the search runs as a `featurelab` job on
                                                   the durable queue (P22), on whichever worker leases it
    GET  /api/datasets/{id}/feature-lab            the dataset's jobs, newest first, plus the DSL
    GET  /api/datasets/{id}/feature-lab/{job_id}   one job: status, journal (live), kept transforms, holdout check, version

`candidates` (typed transforms only) replaces the heuristic proposals: D4's language model proposes through this.
With `apply` (default true) the kept transforms become a new dataset version (lineage kind `feature_lab`) with the same
holdout rows; the decision-time answers carry over.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import featurelab as fl
from . import frames
from .authz import path, require  # P19
from .data_doctor_api import answers, run_drops
from .datasets import DatasetError, now
from .http import dataset_or_404
from .splits import holdout_mask

router = APIRouter(prefix="/api")


@router.get("/datasets/{dataset_id}/feature-lab")
def list_feature_lab(dataset_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    store = request.app.state.datasets
    return {"dataset_id": dataset_id, "jobs": fl.list_jobs(store.dir(dataset_id)),
            "dsl": {k: {"args": v["args"], "text": v["text"]} for k, v in fl.OPS.items()},
            "agg_fns": list(fl.AGG_FNS), "date_parts": list(fl.DATE_PARTS)}


@router.get("/datasets/{dataset_id}/feature-lab/{job_id}")
def get_feature_lab(dataset_id: str, job_id: str, request: Request) -> dict[str, Any]:
    dataset_or_404(request, dataset_id)
    j = fl.read_job(request.app.state.datasets.dir(dataset_id), job_id)
    if not j:
        raise HTTPException(404, f"No Feature Lab job '{job_id}' on {dataset_id}.")
    return j


def _check_request(dataset_id: str, rec: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """F-X1 C2: the route's checks, from the schema and one DuckDB count (no table read): the job reads the data."""
    from .lake import duck
    from .lake.pages import q, source

    cols = frames.column_names(frames.table(dataset_id))
    target = body.get("target") or rec["target"]
    if not target or target not in cols:
        raise HTTPException(400, "Name a target column (the dataset has none set).")
    src = source(dataset_id)
    with duck.job(threads=2) as con:
        if src.arrow is not None:
            con.register(src.rel, src.arrow)
        k = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {q(target)} FROM {src.rel} WHERE {q(target)} IS NOT NULL LIMIT 2)").fetchall()[0][0]
    if k < 2:
        raise HTTPException(400, f"'{target}' has fewer than two values.")
    return _check_args(cols, target, body)


def _check_args(cols: list[str], target: str, body: dict[str, Any]) -> dict[str, Any]:
    entity = body.get("entity")
    if entity is not None and entity not in cols:
        raise HTTPException(400, f"entity: no column named {entity!r}.")
    if entity == target:
        raise HTTPException(400, "entity: the target can't be the entity column.")
    try:
        max_c = int(body.get("max_candidates", 20))
        limit = float(body.get("time_limit", 240))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_candidates and time_limit are numbers.") from None
    if not 1 <= max_c <= 100 or not 5 <= limit <= 3600:
        raise HTTPException(400, "max_candidates is 1–100 and time_limit 5–3600 seconds.")
    cands = body.get("candidates")
    if cands is not None:
        if not isinstance(cands, list) or not cands:
            raise HTTPException(400, "candidates is a non-empty list of typed transforms ({op, ...}).")
        if any(not isinstance(c, dict) or c.get("op") not in fl.OPS for c in cands):
            bad = next(c for c in cands if not isinstance(c, dict) or c.get("op") not in fl.OPS)
            raise HTTPException(400, f"Not a typed transform: {json.dumps(bad)[:120]}. op is one of {', '.join(fl.OPS)}; free-form code is never run.")
    return {"target": target, "entity": entity, "max_c": max_c, "limit": limit, "cands": cands}


def _prepare(store, dataset_id: str, rec: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Worker side: read what the search needs. F-X1 C2: the version whole up to config.doctor_rows() rows, else its
    recorded splitmix64 sample (doctor_frame); the holdout mask is the version's, taken at the sampled positions."""
    from . import doctor_frame as DF

    fr = DF.load(dataset_id)
    df = fr.df
    target = body.get("target") or rec["target"]
    if not target or target not in df.columns:
        raise HTTPException(400, "Name a target column (the dataset has none set).")
    if df[target].nunique(dropna=True) < 2:
        raise HTTPException(400, f"'{target}' has fewer than two values.")
    entity = body.get("entity")
    if entity is not None and entity not in df.columns:
        raise HTTPException(400, f"entity: no column named {entity!r}.")
    if entity == target:
        raise HTTPException(400, "entity: the target can't be the entity column.")
    try:
        max_c = int(body.get("max_candidates", 20))
        limit = float(body.get("time_limit", 240))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_candidates and time_limit are numbers.") from None
    if not 1 <= max_c <= 100 or not 5 <= limit <= 3600:
        raise HTTPException(400, "max_candidates is 1–100 and time_limit 5–3600 seconds.")
    cands = body.get("candidates")
    if cands is not None:
        if not isinstance(cands, list) or not cands:
            raise HTTPException(400, "candidates is a non-empty list of typed transforms ({op, ...}).")
        if any(not isinstance(c, dict) or c.get("op") not in fl.OPS for c in cands):
            bad = next(c for c in cands if not isinstance(c, dict) or c.get("op") not in fl.OPS)
            raise HTTPException(400, f"Not a typed transform: {json.dumps(bad)[:120]}. op is one of {', '.join(fl.OPS)}; free-form code is never run.")
    try:
        _, sp, _ = store.ensure_splits(dataset_id, target)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    if len(sp["fold"]) != fr.n_rows:
        raise HTTPException(400, "The dataset's split does not match its rows.")
    hold = holdout_mask(sp, fr.n_rows)
    if fr.positions is not None:
        hold = hold[fr.positions]
    from .intake import detect

    roles = detect(df)["columns"]
    drops = run_drops(store, dataset_id, list(df.columns))
    return {"df": df, "target": target, "entity": entity, "max_c": max_c, "limit": limit, "cands": cands, "sp": sp,
            "roles": roles, "drops": drops, "hold": hold, "sample": fr.sample}


@router.post("/datasets/{dataset_id}/feature-lab", status_code=202, dependencies=[require("data.version", path("dataset_id", "dataset"))])
def start_feature_lab(dataset_id: str, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    rec = dataset_or_404(request, dataset_id)
    store = request.app.state.datasets
    p = _check_request(dataset_id, rec, body)  # F-X1 C2: no table read here; the job reads a bounded frame
    apply = body.get("apply", True) is not False
    from .catalog import governance as G

    # DL3 (m3): the version this job makes records a real name, taken now (the job runs on a worker)
    by = str(body.get("by") or "").strip()[:80] or G.current_actor()
    if not by and apply:
        raise HTTPException(400, "Say who is running Feature Lab: send by (or the X-Ensemble-Actor header); the version it makes records the name.")
    job = fl.create(store.dir(dataset_id), {**body, "target": p["target"]})
    from .jobs import queue as Q

    team = None
    with request.app.state.db.connect() as con:
        r = con.execute("SELECT p.team_id FROM datasets d JOIN projects p ON p.id = d.project_id WHERE d.id = ?",
                        (dataset_id,)).fetchone()
        team = r[0] if r else None
    Q.enqueue(request.app.state.db, "featurelab", {"dataset_id": dataset_id, "job_id": job["id"], "body": {**body, "target": p["target"]},
                                                   "apply": apply, "by": by},
              resource="train", by=by or "system", team_id=team, ref_kind="featurelab", ref_id=job["id"])
    return job


def run_job(ctx) -> dict[str, Any]:
    """Worker side: read the version, run the search, write the new version (P22)."""
    from types import SimpleNamespace

    pl = ctx.payload
    store = ctx.state.datasets
    dataset_id, body = pl["dataset_id"], pl["body"]
    rec = ctx.state.db.get_dataset(dataset_id)
    ds_dir = store.dir(dataset_id)
    req = SimpleNamespace(app=SimpleNamespace(state=ctx.state))  # what _version reads from a request

    from .jobs import progress as JP
    from .jobs import writes as JW

    def work(job, progress):
        JP.mark(0.01, "reading")
        p = _prepare(store, dataset_id, rec, body)
        n_c = len(p["cands"]) if p["cands"] else p["max_c"]

        def prog(journal):  # F-JOBS: the search is the first 60 % of the job's progress, the write the rest
            progress(journal)
            JP.mark(0.05 + 0.55 * min(1.0, len(journal) / max(1, n_c)), "searching")

        JP.mark(0.05, "searching")
        res = fl.search(p["df"], p["target"], p["hold"], roles=p["roles"], drops=p["drops"],
                        entity=p["entity"], candidates=p["cands"], max_candidates=p["max_c"], time_limit=p["limit"],
                        progress=prog)
        if p["sample"]:
            res["sample"] = p["sample"]  # F-X1 C2: the search ran on this recorded sample
        out: dict[str, Any] = {"result": res, "journal": res["journal"], "target": p["target"]}
        if pl["apply"] and res["kept"]:
            if p["sample"]:  # F-JOBS: the SQL write reads the version itself; free the search frame before it
                import gc

                p["df"] = None
                gc.collect()
            with JP.band(0.6, 1.0):  # the SQL write commits with this queue job's id (doctor_sql.write_chain)
                out["version"] = (_version_sql(req, rec, res, pl["by"]) if p["sample"] else
                                  _version(req, rec, p["df"], p["sp"], res, pl["by"]))
        return out

    done = JW._finished_version(ctx.state.db, ctx.job["id"], dataset_id)
    if done:  # F-JOBS: an earlier attempt of this job wrote and finished its version: do not search again
        prev = fl.read_job(ds_dir, pl["job_id"]) or {}
        if prev.get("status") != "succeeded":
            from .datasets import now as _now

            prev.update(status="succeeded", phase="done", finished_at=_now(),
                        version={**(prev.get("version") or {}), "id": done, "resumed": True})
            fl.write_job(ds_dir, prev)
        return {"job_id": pl["job_id"], "status": "succeeded", "version": done, "resumed": True}
    job = fl.execute(ds_dir, pl["job_id"], work)
    if job["status"] == "failed":
        raise RuntimeError(job.get("error") or "Feature Lab failed")
    JP.mark(1.0, "done")
    return {"job_id": job["id"], "status": job["status"], "version": (job.get("version") or {}).get("id")}


def on_dead(state, job: dict[str, Any]) -> None:
    pl = job.get("payload") or {}
    fl.close(state.datasets.dir(pl["dataset_id"]), pl["job_id"], "failed", f"Its worker stopped every time ({job.get('error')}).")


def _version(request: Request, rec: dict[str, Any], df, sp: dict[str, Any], res: dict[str, Any], by: str) -> dict[str, Any]:
    """The kept transforms applied to a copy: a new dataset version, every row kept, the same holdout."""
    from .main import _detail

    store = request.app.state.datasets
    dataset_id = rec["id"]
    new_df = df.copy()
    effects = []
    for t in res["kept"]:
        new_df[t["name"]] = fl.compute(new_df, t)  # bins carry their training-row edges, so this is the same column
        effects.append({"kind": "feature_lab", "transform": t, "text": fl.describe(t), "column": t["name"],
                        "missing": int(new_df[t["name"]].isna().sum())})
    root = (store.lineage(dataset_id) or {}).get("root") or dataset_id
    extra = {"kind": "feature_lab", "fixes": effects, "transforms": res["kept"], "applied_at": now(), "by": by,
             "columns_before": int(df.shape[1]), "columns_after": int(new_df.shape[1]),
             "search": {"metric": res["metric"], "improvement_oof": res["improvement_oof"], "holdout": res.get("holdout")}}
    base = rec["name"].split(" · features")[0]
    n_ver = sum(1 for d in request.app.state.db.list_datasets() if (d["profile"].get("lineage") or {}).get("root") == root
                and (d["profile"].get("lineage") or {}).get("kind") == "feature_lab") + 1
    new = store.derive(dataset_id, new_df, None, "feature_lab", [], splits=sp, extra=extra, by=by,  # P1: every row kept
                       name=f"{base} · features v{n_ver}",
                       note=f"Feature Lab: {len(effects)} feature{'s' if len(effects) != 1 else ''} added to {dataset_id}")
    a = answers(store, dataset_id)
    if a:
        (store.dir(new["id"]) / "decisions.json").write_text(json.dumps({**a, "inherited_from": a.get("inherited_from") or dataset_id}))
    d = _detail(new)
    return {"id": new["id"], "name": new["name"], "columns_added": [t["name"] for t in res["kept"]], "rows": int(len(new_df)),
            "holdout_rows": int(len(sp["holdout"])), "profile_rows": d.get("rows")}


def _feature_sql(t: dict[str, Any], types: dict[str, str]) -> str:
    """F-X1 C2: featurelab.compute as a SQL expression over the parent table (alias s), the same values: numbers
    are TRY_CAST to DOUBLE (pd.to_numeric), agg is a window over the whole table, bin uses the edges the search fitted
    on its training rows, date_part follows pandas (dayofweek Monday = 0)."""
    from .doctor_sql import _lit, q

    def num(c: str) -> str:
        return f"TRY_CAST(s.{q(c)} AS DOUBLE)"

    op = t["op"]
    if op == "ratio":
        a, b = num(t["a"]), num(t["b"])
        return f"CASE WHEN {b} = 0 OR NOT isfinite({a} / {b}) THEN NULL ELSE {a} / {b} END"
    if op == "diff":
        return f"{num(t['a'])} - {num(t['b'])}"
    if op == "agg":
        part = f"PARTITION BY s.{q(t['entity'])}"
        if t["fn"] == "count":
            return f"CAST(count(*) OVER ({part}) AS DOUBLE)"
        fn = {"mean": "avg", "sum": "sum", "max": "max", "min": "min", "std": "stddev_samp"}[t["fn"]]
        x = num(t["col"])
        if t["fn"] == "sum":  # pandas: the sum of an all-missing group is 0
            return f"CAST(COALESCE(sum({x}) OVER ({part}), 0) AS DOUBLE)"
        return f"CAST({fn}({x}) OVER ({part}) AS DOUBLE)"
    if op == "date_part":
        d = f"TRY_CAST(s.{q(t['col'])} AS TIMESTAMP)"
        part = {"year": "year", "quarter": "quarter", "month": "month", "dayofyear": "dayofyear", "hour": "hour"}.get(t["part"])
        if t["part"] == "dayofweek":
            return f"CAST(isodow({d}) - 1 AS DOUBLE)"
        if part is None:
            raise ValueError(f"date_part {t['part']!r} has no SQL form")
        return f"CAST({part}({d}) AS DOUBLE)"
    if op == "bin":
        x = num(t["col"])
        edges = [float(e) for e in t.get("edges") or []]
        if not edges:
            return f"CASE WHEN {x} IS NULL THEN NULL ELSE CAST(0 AS DOUBLE) END"
        return f"CASE WHEN {x} IS NULL THEN NULL ELSE CAST({' + '.join(f'CAST({x} > {_lit(e)} AS INTEGER)' for e in edges)} AS DOUBLE) END"
    if op == "log":
        x = num(t["col"])
        return f"sign({x}) * ln(1 + abs({x}))"

    def txt(c: str) -> str:  # pandas astype(str): a missing cell reads 'nan'
        return f"COALESCE(CAST(s.{q(c)} AS VARCHAR), 'nan')"
    return f"{txt(t['a'])} || '|' || {txt(t['b'])}"


def _version_sql(request: Any, rec: dict[str, Any], res: dict[str, Any], by: str) -> dict[str, Any]:
    """F-X1 C2: the kept transforms added to every row of a version above config.doctor_rows() rows, as SQL columns
    written through the VersionWriter (no full pandas read). Every row is kept, so the parent's split is carried as is."""
    from . import doctor_sql
    from .main import _detail

    store = request.app.state.datasets
    dataset_id = rec["id"]
    effects = []

    def build(con: Any, sql: str, ids: Any, target: Any):
        ch = doctor_sql._Chain(con, sql)
        add = {}
        for t in res["kept"]:
            add[t["name"]] = _feature_sql(t, dict(ch.cols))
        ch.select(add=add)
        for t in res["kept"]:
            effects.append({"kind": "feature_lab", "transform": t, "text": fl.describe(t), "column": t["name"],
                            "missing": ch.count(f"{doctor_sql.q(t['name'])} IS NULL")})
        return ch, effects

    root = (store.lineage(dataset_id) or {}).get("root") or dataset_id
    base = rec["name"].split(" · features")[0]
    n_ver = sum(1 for d in request.app.state.db.list_datasets() if (d["profile"].get("lineage") or {}).get("root") == root
                and (d["profile"].get("lineage") or {}).get("kind") == "feature_lab") + 1
    out = doctor_sql.write_chain(store, dataset_id, build, op="feature_lab", kind="feature_lab", by=by, acknowledge=None,
                                 name=f"{base} · features v{n_ver}",
                                 note=f"Feature Lab: {len(res['kept'])} feature{'s' if len(res['kept']) != 1 else ''} added to {dataset_id}",
                                 extra={"transforms": res["kept"], "sample": res.get("sample"),
                                        "search": {"metric": res["metric"], "improvement_oof": res["improvement_oof"],
                                                   "holdout": res.get("holdout")}})
    new = out["rec"]
    a = answers(store, dataset_id)
    if a and not new.get("deduplicated"):
        (store.dir(new["id"]) / "decisions.json").write_text(json.dumps({**a, "inherited_from": a.get("inherited_from") or dataset_id}))
    d = _detail(new)
    return {"id": new["id"], "name": new["name"], "columns_added": [t["name"] for t in res["kept"]], "rows": int(out["rows_after"]),
            "profile_rows": d.get("rows"), "how": "sql", **({"deduplicated": new["deduplicated"]} if new.get("deduplicated") else {})}

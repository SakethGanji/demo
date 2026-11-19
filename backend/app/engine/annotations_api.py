"""F14 routes: several annotators per row on a finished classification run. Load annotations (a long-format file,
or the support-tickets sample's simulated annotators), read agreement (Cohen's κ per pair, Fleiss' κ), per-annotator
quality and the CROWDLAB consensus (cleanlab.multiannotator), work the disagreement queue (votes under reviewer
names, adjudications), and save the consensus labels as a dataset version with lineage (holdout untouched)."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from . import annotations as an
from .jobs import alive, kill_group, spawn
from .jsonsafe import write_json
from .store import now
from ..http import run_or_404

router = APIRouter(prefix="/api")

_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}
_CACHE: dict[str, tuple[tuple, dict[str, Any], pd.DataFrame]] = {}


class SampleRequest(BaseModel):
    n_rows: int = Field(an.SAMPLE_DEFAULTS["n_rows"], ge=50, le=10000, description="Training rows to annotate.")
    per_row: int = Field(an.SAMPLE_DEFAULTS["per_row"], ge=2, le=len(an.SAMPLE_ANNOTATORS), description="Annotators per row.")
    seed: int = Field(an.SAMPLE_DEFAULTS["seed"], ge=0)


class Answer(BaseModel):
    row: int
    action: Literal["vote", "adjudicate", "clear"] = "vote"
    label: str | None = None


class Answers(BaseModel):
    answers: list[Answer]
    reviewer: str = Field("you", min_length=1, max_length=60)


class SimAdj(BaseModel):
    n: int = Field(50, ge=1, le=10000, description="Adjudicate the n lowest-quality queue rows from the hidden truth.")


def _check(rec: dict[str, Any]) -> None:
    cfg, res = rec["config"], rec.get("result") or {}
    if rec["status"] != "succeeded":
        raise HTTPException(400, f"Run {rec['id']} is {rec['status']}; annotator agreement needs a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival") or cfg.get("import"):
        raise HTTPException(400, "Annotator agreement uses the run's out-of-fold class probabilities: only trained "
                                 "tabular runs have them.")
    if res.get("problem_type") not in ("binary", "multiclass"):
        raise HTTPException(400, f"Several annotators' labels and a consensus label need a classification run; this one "
                                 f"is {res.get('problem_type')}.")


def _classes(rec: dict[str, Any]) -> list[str]:
    return [str(c) for c in (rec.get("result") or {}).get("classes") or []]


def _spawn(request: Request, rec: dict[str, Any]) -> None:
    """p̂ in its own process (the run's OOF, or a quick bagged fit). One per run."""
    run_id = rec["id"]
    run_dir = Path(rec["config"]["run_dir"])
    p = _PROCS.get(run_id)
    if p is not None and p.poll() is None:
        return
    d = an.ann_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    st = an.read_state(run_dir) or {}
    st.update(status="computing", error=None, started_at=now())
    an.write_state(run_dir, st)
    p = spawn("app.engine.annotations", [str(run_dir)], d / "probs.log")
    _PROCS[run_id] = p
    st["pid"] = p.pid
    an.write_state(run_dir, st)
    store = request.app.state.runs

    def wait():
        code = p.wait()
        s = an.read_state(run_dir) or {}
        if s.get("status") == "computing":
            s.update(status="failed", error=f"The p̂ process exited with code {code}.")
            an.write_state(run_dir, s)
        store.merge_result(run_id, annotations=an.headline(s))

    threading.Thread(target=wait, name=f"ann-{run_id}", daemon=True).start()


def _status(rec: dict[str, Any]) -> dict[str, Any] | None:
    run_dir = Path(rec["config"]["run_dir"])
    st = an.read_state(run_dir)
    if st and st.get("status") == "computing":
        p = _PROCS.get(rec["id"])
        if (p is None or p.poll() is not None) and not alive(st.get("pid"), "app.engine.annotations"):
            st = an.read_state(run_dir) or st
            if st.get("status") == "computing":
                st.update(status="failed", error="The p̂ process stopped (the API restarted). Load the annotations again.")
                an.write_state(run_dir, st)
    return st


def _analysis(request: Request, rec: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame] | None:
    run_dir = Path(rec["config"]["run_dir"])
    d = an.ann_dir(run_dir)
    key = tuple((d / f).stat().st_mtime_ns if (d / f).exists() else 0
                for f in ("annotations.csv", "adjudications.json", "probs.json", "truth.json"))
    hit = _CACHE.get(rec["id"])
    if hit and hit[0] == key:
        return hit[1], hit[2]
    cfg = rec["config"]
    data = pd.read_csv(cfg["csv_path"])
    res = an.analyze(run_dir, cfg, data[cfg["label"]])
    _CACHE[rec["id"]] = (key, res, data)
    st = an.read_state(run_dir) or {}
    st.update(summary=res["summary"], updated_at=now())
    an.write_state(run_dir, st)
    request.app.state.runs.merge_result(rec["id"], annotations=an.headline(st))
    return res, data


def _show_cols(cfg: dict[str, Any], data: pd.DataFrame) -> list[str]:
    label = cfg["label"]
    drop = set(cfg.get("drop_columns") or [])
    text = [c for c in cfg.get("text_columns") or [] if c in data.columns]
    rest = [c for c in data.columns if c not in text and c != label and c not in drop]
    return (text + rest)[:4]


def _sample_available(request: Request, rec: dict[str, Any]) -> bool:
    try:
        return an.sample_truth(request.app.state.datasets, rec["dataset_id"]) is not None
    except Exception:  # noqa: BLE001
        return False


@router.get("/runs/{run_id}/annotations")
def get_annotations(run_id: str, request: Request, offset: int = 0, limit: int = 30,
                    include: Literal["queue", "all"] = "queue") -> dict[str, Any]:
    """F14: annotators' agreement and quality, the CROWDLAB consensus, and the disagreement queue (lowest consensus
    quality first). `summary` (schema annotations/1) is also on the run payload as `annotations.summary` for F17."""
    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    run_dir = Path(cfg["run_dir"])
    st = _status(rec)
    base = {"run_id": run_id, "classes": _classes(rec), "sample_available": _sample_available(request, rec),
            "sample_defaults": an.SAMPLE_DEFAULTS, "sample_annotators": [{k: a[k] for k in ("name", "profile")} for a in an.SAMPLE_ANNOTATORS],
            "versions": (st or {}).get("versions") or []}
    long = an.read_long(run_dir)
    if not st or long.empty:
        return {**base, "status": "none"}
    out = {**base, "status": st.get("status"), "error": st.get("error"), "source": st.get("source"),
           "n_raw": int(len(long)), "load": st.get("load")}
    if st.get("status") != "ready":
        return out
    try:
        got = _analysis(request, rec)
    except ValueError as e:
        return {**out, "status": "failed", "error": str(e)}
    res, data = got
    items, total = an.queue_items(res, data, cfg["label"], _show_cols(cfg, data), offset=max(0, offset),
                                  limit=max(1, min(limit, 500)), include=include)
    truth = an.read_truth(run_dir)
    return {**out, "summary": res["summary"], "annotators": res["annotators"], "pairwise": res["pairwise"],
            "fleiss": res["fleiss"], "probs": {k: v for k, v in res["probs_meta"].items() if k not in ("rows",)},
            "queue": {"items": items, "total": total, "offset": offset, "limit": limit, "include": include},
            "show_columns": _show_cols(cfg, data), "simulated": bool(truth),
            "simulation": {"annotators": truth.get("annotators"), "params": truth.get("params")} if truth else None}


def _replace(request: Request, rec: dict[str, Any], long: pd.DataFrame, source: str, load: dict[str, Any],
             truth: dict[str, Any] | None) -> dict[str, Any]:
    run_dir = Path(rec["config"]["run_dir"])
    d = an.ann_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    long = long.copy()
    long["source"] = source
    long["at"] = now()
    with _LOCK:
        an.write_long(run_dir, long)
        an.write_adjudications(run_dir, {})
        tp = d / "truth.json"
        if truth:
            write_json(tp, truth)
        elif tp.exists():
            tp.unlink()
        st = an.read_state(run_dir) or {}
        st.update(source=source, load=load, summary=None, created_at=now())
        an.write_state(run_dir, st)
        _CACHE.pop(rec["id"], None)
        if an.read_probs(run_dir) is None:
            _spawn(request, rec)
        else:
            st.update(status="ready", error=None)
            an.write_state(run_dir, st)
    return get_annotations(rec["id"], request)


@router.post("/runs/{run_id}/annotations/sample")
def load_sample(run_id: str, body: SampleRequest, request: Request) -> dict[str, Any]:
    """The support-tickets sample's SIMULATED annotators (5, of different quality, one systematically biased): each of
    n_rows training rows labelled by per_row of them, every label drawn from the sample's hidden truth. Replaces the
    run's annotations."""
    rec = run_or_404(request, run_id)
    _check(rec)
    got = an.sample_truth(request.app.state.datasets, rec["dataset_id"])
    if got is None:
        raise HTTPException(400, "Simulated annotators exist for the support-tickets sample (or a version of it) only: "
                                 "their labels are drawn from its hidden truth. Upload an annotations file instead.")
    truth, tsrc = got
    classes = _classes(rec)
    rows, _ = an.split_rows(rec["config"])
    long = an.simulate(truth, rows, classes, n_rows=body.n_rows, per_row=body.per_row, seed=body.seed)
    used = sorted(long["row"].unique().tolist())
    tdoc = {"simulated": True, "source": tsrc, "truth": {str(r): str(truth[r]) for r in used},
            "annotators": an.SAMPLE_ANNOTATORS, "params": body.model_dump()}
    load = {"rows": len(used), "annotations": int(len(long)), "annotators": len(an.SAMPLE_ANNOTATORS),
            "holdout_rows": 0, "simulated": True, **body.model_dump()}
    return _replace(request, rec, long, "sample", load, tdoc)


@router.post("/runs/{run_id}/annotations/upload")
async def upload(run_id: str, request: Request, file: UploadFile = File(...),
                 mode: Literal["replace", "append"] = Form("replace")) -> dict[str, Any]:
    """A long-format annotations file: one line per (row, annotator, label). Rows by `row` (the dataset's row number,
    from 0) or by a unique ID column of the dataset. Holdout rows are refused: the holdout is never relabelled."""
    rec = run_or_404(request, run_id)
    _check(rec)
    cfg = rec["config"]
    content = await file.read()
    sep = "\t" if (file.filename or "").lower().endswith(".tsv") else ","
    try:
        up = pd.read_csv(io.BytesIO(content), sep=sep)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read {file.filename} as CSV: {str(e).splitlines()[0]}") from e
    data = pd.read_csv(cfg["csv_path"])
    try:
        long, info = an.parse_upload(data, up, cfg["label"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    classes = set(_classes(rec))
    unknown = sorted(set(long["label"]) - classes)
    if unknown:
        raise HTTPException(400, f"Labels that are not classes of this run: {', '.join(unknown[:8])}. The classes are "
                                 f"{', '.join(sorted(classes))}.")
    tr, hold = an.split_rows(cfg)
    in_hold = long["row"].isin(set(hold.tolist()))
    in_train = long["row"].isin(set(tr.tolist()))
    info.update(holdout_rows=int(long.loc[in_hold, "row"].nunique()),
                other_rows=int(long.loc[~in_hold & ~in_train, "row"].nunique()))
    long = long[in_train]
    if long.empty:
        raise HTTPException(400, "No annotation is on a training row of this run"
                                 + (f" ({info['holdout_rows']} holdout rows were left out: the holdout is never relabelled)."
                                    if info["holdout_rows"] else "."))
    if mode == "append":
        old = an.read_long(Path(cfg["run_dir"]))
        long = pd.concat([old[["row", "annotator", "label"]], long], ignore_index=True)
    long = long.drop_duplicates(["row", "annotator"], keep="last")
    load = {"file": file.filename, "mode": mode, "rows": int(long["row"].nunique()), "annotations": int(len(long)),
            "annotators": int(long["annotator"].nunique()), **info}
    return _replace(request, rec, long, "upload", load, None if mode == "replace" else an.read_truth(Path(cfg["run_dir"])))


@router.post("/runs/{run_id}/annotations/answer")
def answer(run_id: str, body: Answers, request: Request) -> dict[str, Any]:
    """The disagreement queue: `vote` adds the reviewer's label for the row as one more annotation (the inbox under
    another name); `adjudicate` sets the row's final label, over the consensus; `clear` removes the reviewer's vote and
    any adjudication of the row."""
    rec = run_or_404(request, run_id)
    _check(rec)
    cfg = rec["config"]
    run_dir = Path(cfg["run_dir"])
    classes = set(_classes(rec))
    tr, hold = an.split_rows(cfg)
    tr_set, hold_set = set(tr.tolist()), set(hold.tolist())
    with _LOCK:
        long = an.read_long(run_dir)
        if long.empty:
            raise HTTPException(409, "No annotations are loaded for this run.")
        adj = an.read_adjudications(run_dir)
        add = []
        for a in body.answers:
            if a.row in hold_set:
                raise HTTPException(400, f"Row {a.row} is in the holdout: the holdout is never relabelled.")
            if a.row not in tr_set:
                raise HTTPException(404, f"Row {a.row} is not a training row of this run.")
            if a.action != "clear" and (a.label is None or a.label not in classes):
                raise HTTPException(400, f"Row {a.row}: '{a.label}' is not a class; one of {', '.join(sorted(classes))}.")
            if a.action == "vote":
                add.append({"row": a.row, "annotator": body.reviewer, "label": a.label, "source": "queue", "at": now()})
            elif a.action == "adjudicate":
                if a.row not in set(long["row"].tolist()):
                    raise HTTPException(404, f"Row {a.row} has no annotations to adjudicate.")
                adj[str(a.row)] = {"label": a.label, "by": body.reviewer, "at": now()}
            else:
                long = long[~((long["row"] == a.row) & (long["annotator"] == body.reviewer))]
                adj.pop(str(a.row), None)
        if add:
            long = pd.concat([long, pd.DataFrame(add)], ignore_index=True).drop_duplicates(["row", "annotator"], keep="last")
        an.write_long(run_dir, long)
        an.write_adjudications(run_dir, adj)
    return {"answered": len(body.answers), "votes": len(add), "adjudicated": len(adj), "reviewer": body.reviewer}


@router.post("/runs/{run_id}/annotations/simulate-adjudication")
def simulate_adjudication(run_id: str, body: SimAdj, request: Request) -> dict[str, Any]:
    """Adjudicate the n lowest-quality queue rows from the sample's hidden truth, as 'simulated-adjudicator' (a stand-in
    for a person; the sample only)."""
    rec = run_or_404(request, run_id)
    run_dir = Path(rec["config"]["run_dir"])
    truth = an.read_truth(run_dir)
    if not truth:
        raise HTTPException(400, "These annotations have no hidden truth to simulate an adjudicator with.")
    st = _status(rec)
    if not st or st.get("status") != "ready":
        raise HTTPException(409, "The consensus is not ready yet.")
    res, _ = _analysis(request, rec)
    d = res["detail"]
    sel = np.flatnonzero(d["in_queue"] & ~d["adjudicated"])
    order = sel[np.argsort(d["cq"][sel], kind="stable")][: body.n]
    with _LOCK:
        adj = an.read_adjudications(run_dir)
        for i in order:
            r = int(d["rows"][i])
            adj[str(r)] = {"label": truth["truth"][str(r)], "by": "simulated-adjudicator", "at": now()}
        an.write_adjudications(run_dir, adj)
    return {"adjudicated": int(len(order)), "reviewer": "simulated-adjudicator"}


@router.post("/runs/{run_id}/annotations/version")
def make_version(run_id: str, request: Request) -> dict[str, Any]:
    """The final labels (an adjudication, else the CROWDLAB consensus) of the annotated training rows as a dataset
    version: the run's data with those labels, the same split record (holdout rows and labels untouched), lineage with
    one change per relabelled row."""
    from ..splits import save_splits

    rec = run_or_404(request, run_id)
    _check(rec)
    cfg = rec["config"]
    st = _status(rec)
    if not st or st.get("status") != "ready":
        raise HTTPException(409, "No consensus is ready for this run.")
    res, data = _analysis(request, rec)
    d = res["detail"]
    classes = res["classes"]
    label = cfg["label"]
    df = data.copy()
    adj = an.read_adjudications(Path(cfg["run_dir"]))
    cmap = {str(c): c for c in pd.unique(df[label].dropna())}
    changes = []
    for i, r in enumerate(d["rows"]):
        r = int(r)
        new = classes[d["final"][i]]
        old = None if pd.isna(df.at[r, label]) else str(df.at[r, label])
        if old != new:
            df.at[r, label] = cmap.get(new, new)
            a = adj.get(str(r))
            changes.append({"op": "relabel", "row": r, "root_row": r, "from": old, "to": new,
                            "by": a["by"] if a else "consensus (CROWDLAB)",
                            "consensus_quality": float(d["cq"][i]), "annotations": int(d["n_ann"][i])})
    _, hold = an.split_rows(cfg)
    ds = request.app.state.datasets
    src = rec["dataset_id"]
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    parent_lin = ds.lineage(src) or {}
    root = parent_lin.get("root") or src
    root_rows = parent_lin.get("root_rows") or list(range(len(df)))
    for ch in changes:
        ch["root_row"] = int(root_rows[ch["row"]]) if len(root_rows) == len(df) else ch["row"]
    versions = st.get("versions") or []
    lineage = {"parent": src, "root": root, "version": int(parent_lin.get("version") or 1) + 1,
               "annotations": {"run_id": run_id, "method": "crowdlab", "rows": int(len(d["rows"])),
                               "annotators": res["summary"]["n_annotators"], "adjudicated": int(d["adjudicated"].sum())},
               "labelled": 0, "relabelled": len(changes), "dropped": 0, "synthetic": 0, "relabelled_holdout": 0,
               "root_rows": root_rows if len(root_rows) == len(df) else list(range(len(df))), "changes": changes}
    name = (ds.db.get_dataset(src) or {}).get("name", src).replace(" (sample)", "")
    new_rec = ds.create_version(src, df, {**sp, "derived_from": src}, name=f"{name} · consensus labels",
                                note=f"Annotator consensus on {run_id}: {len(changes)} of {len(d['rows'])} annotated training "
                                     f"rows relabelled (CROWDLAB, {int(d['adjudicated'].sum())} adjudicated)", lineage=lineage)
    split = cfg.get("split") or {}
    if split.get("strategy") and split.get("strategy") != "random-stratified":  # F6: keep the structured split too
        p = ds.split_path(new_rec["id"], label, split["strategy"], split.get("params"))
        p.parent.mkdir(parents=True, exist_ok=True)
        save_splits(p, {**sp, "derived_from": src})
    holdout_same = bool((df.loc[hold, label].astype(str).to_numpy() == data.loc[hold, label].astype(str).to_numpy()).all())
    ver = {"id": new_rec["id"], "name": new_rec["name"], "rows": new_rec["rows"], "relabelled": len(changes),
           "annotated_rows": int(len(d["rows"])), "adjudicated": int(d["adjudicated"].sum()),
           "holdout_unchanged": holdout_same, "created_at": now()}
    with _LOCK:
        st = an.read_state(Path(cfg["run_dir"])) or st
        st["versions"] = [*versions, ver]
        an.write_state(Path(cfg["run_dir"]), st)
    return {"dataset_version": ver}


def shutdown() -> None:
    for p in list(_PROCS.values()):
        if p.poll() is None:
            kill_group(p.pid)


def active() -> list[str]:
    return [r for r, p in _PROCS.items() if p.poll() is None]

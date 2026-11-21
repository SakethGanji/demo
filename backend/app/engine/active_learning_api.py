"""F13 routes: start an active-learning labelling queue on a finished classification run, answer its queue (the
Ground-Truth Inbox's answer shape), submit a round (a dataset version with lineage, then a refit), read the curves and
the active-vs-random comparison, cancel. The work runs in its own process (`python -m app.engine.active_learning`,
see active_learning.py), one queue at a time across the workbench.

P22: the process runs as an `al` job (resource `train`) on the durable queue (`run_job`, on whichever worker leases it);
"is it working" is the job's status, so an API restart costs nothing. A round submitted while the process is finishing
is picked up by the same job (it runs the process again)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import frames
from ..splits import holdout_mask
from . import active_learning as al
from ..jobs import queue as Q
from .store import now
from ..http import run_or_404

router = APIRouter(prefix="/api")

_LOCK = threading.Lock()  # the answers file, within this API process
_DB = None  # bound at API start (main.py): active() needs it


class ALRequest(BaseModel):
    pool: Literal["hide", "missing"] = Field("hide", description="hide: hide the labels of a share of the run's training "
                                             "rows (they become the simulated oracle); missing: the dataset's training rows "
                                             "with an empty label cell (upload a CSV with blank labels).")
    hide_share: float = Field(al.DEFAULTS["hide_share"], gt=0, lt=1, description="Share of training labels hidden (pool = hide).")
    budget: int = Field(al.DEFAULTS["budget"], ge=1, le=5000, description="Rows to label per round.")
    rounds: int = Field(al.DEFAULTS["rounds"], ge=1, le=10)
    strategy: Literal["margin", "entropy", "least_confident"] = al.DEFAULTS["strategy"]
    diversity: Literal["cluster", "kcenter", "none"] = al.DEFAULTS["diversity"]
    candidates_factor: int = Field(al.DEFAULTS["candidates_factor"], ge=1, le=100,
                                   description="β: diversity picks among the β·b most uncertain rows.")
    compare_random: bool = Field(True, description="Simulate active vs random at equal budgets with the oracle (pool = hide).")
    seeds: int = Field(al.DEFAULTS["seeds"], ge=1, le=10, description="Seeds of the simulated comparison.")
    time_limit: float | None = Field(None, gt=0, description="Seconds per fit; default: the run's own time_limit.")
    slice_id: str | None = Field(None, description="F16: pre-filter the pool to a weak slice of the run's latest slice "
                                                   "search (its feature conditions applied to the training rows).")


class Answer(BaseModel):
    id: str
    answer: Literal["confirm", "relabel", "skip"] | None
    label: str | None = None


class Answers(BaseModel):
    answers: list[Answer]
    reviewer: str = Field("you", min_length=1, max_length=60)


def _state(rec: dict[str, Any], store: Any) -> dict[str, Any] | None:
    run_dir = Path(rec["config"]["run_dir"])
    st = al.read_state(run_dir)
    if st and st.get("status") in ("queued", "running") and not Q.active_for(store.db, "run", rec["id"], "al"):
        st = al.read_state(run_dir)  # the handler may have just closed it
        if st and st.get("status") in ("queued", "running"):
            j = Q.latest_for(store.db, "run", rec["id"], "al") or {}
            st.update(status="failed", finished_at=now(),
                      error=f"The labelling queue's job ended without finishing ({j.get('error') or j.get('status') or 'no job'}).")
            store.merge_result(rec["id"], active_learning=al.headline(st))
            al.write_state(run_dir, st)
    return st


def defaults(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result") or {}
    n = (res.get("data") or {}).get("train_rows") or 0
    d = dict(al.DEFAULTS)
    S, R = d["seeds"], d["rounds"]
    fits = (R + 1) + S * (1 + 2 * R) - 1
    return {**d, "n_train": n, "n_initial": int(round((1 - d["hide_share"]) * n)), "fits": fits,
            "estimate_seconds": fits * ((res.get("fit_seconds") or 10) * 0.3 + 4) if res.get("fit_seconds") else None,
            "missing_labels": int((res.get("data") or {}).get("dropped_missing_label_train") or 0)}


def _view(rec: dict[str, Any], st: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """The state with the queue's items merged with their answers (the inbox shape: answer, answer_label, answered_by)."""
    st = {k: v for k, v in st.items() if k != "traceback"}
    arms = st.get("arms") or {}
    q = dict(arms.get("queue") or {})
    items = []
    a = answers.get("answers") or {}
    for it in q.get("queue") or []:
        x = dict(it)
        ans = a.get(it["id"])
        if ans and ans.get("answer"):
            x["answer"], x["answered_by"] = ans["answer"], ans.get("by")
            if ans["answer"] in ("confirm", "relabel"):
                x["answer_label"] = ans.get("label") or it["suggested"]
        items.append(x)
    qr = q.get("queue_round")
    submitted = qr is not None and str(qr) in (answers.get("submitted") or {})
    q["queue"] = items
    q["open"] = bool(items) and qr is not None and not submitted and not q.get("done")
    q["submitted_pending"] = submitted and not q.get("done")
    q["answered"] = sum(1 for i in items if i.get("answer"))
    for k in ("labelled", "labels", "skipped"):
        q.pop(k, None)
    q["n_labelled"] = len((arms.get("queue") or {}).get("labelled") or [])
    out_arms = {}
    for k, v in arms.items():
        if k == "queue":
            continue
        out_arms[k] = {kk: vv for kk, vv in v.items() if kk not in ("labelled", "labels", "skipped", "next_batch")}
        out_arms[k]["n_labelled"] = len(v.get("labelled") or [])
    return {**st, "arms": out_arms, "queue": q, "versions": answers.get("versions") or []}


@router.get("/runs/{run_id}/active-learning")
def get_active_learning(run_id: str, request: Request) -> dict[str, Any]:
    """F13: the labelling queue (items with their answers), the score per round against labels used, and the simulated
    active-vs-random comparison with its summary (`summary`, schema active_learning/1). Status 'none' with the defaults
    when none was started."""
    rec = run_or_404(request, run_id)
    st = _state(rec, request.app.state.runs)
    if not st:
        return {"run_id": run_id, "status": "none", "defaults": defaults(rec)}
    return {"run_id": run_id, **_view(rec, st, al.read_answers(Path(rec["config"]["run_dir"]))), "defaults": defaults(rec)}


def _enqueue(request: Request, rec: dict[str, Any]) -> None:
    from ..catalog import governance as G

    db = request.app.state.db
    if not Q.active_for(db, "run", rec["id"], "al"):
        Q.enqueue(db, "al", {"run_id": rec["id"]}, resource="train", by=G.current_actor() or "system",
                  team_id=rec.get("team_id"), ref_kind="run", ref_id=rec["id"])


def run_job(ctx) -> dict[str, Any]:
    """Worker side: run the queue's process; again while a submitted round is waiting; close the state if it did not."""
    from .workerd import Cancelled

    store = ctx.state.runs
    run_id = ctx.payload["run_id"]
    run_dir = Path(store.get(run_id)["config"]["run_dir"])
    d = al.al_dir(run_dir)
    passes = 0
    while True:
        passes += 1
        code = ctx.run_process("app.engine.active_learning", [str(run_dir), ctx.watch_pid()], d / "active_learning.log")
        if ctx.stop_reason in ("shutdown", "lost"):
            raise Cancelled(ctx.stop_reason)
        st = al.read_state(run_dir) or {}
        if st.get("status") in ("queued", "running"):
            st.update(status="cancelled" if ctx.cancelled else "failed", finished_at=now(),
                      error="Cancelled." if ctx.cancelled else f"The labelling queue's process exited with code {code}.")
            store.merge_result(run_id, active_learning=al.headline(st))
            al.write_state(run_dir, st)
            return {"run_id": run_id, "status": st["status"], "passes": passes}
        if ctx.cancelled or not (st.get("status") == "awaiting_labels" and _pending(run_dir, st)):
            return {"run_id": run_id, "status": st.get("status"), "passes": passes}
        # answers submitted while the process was finishing: run them now


def on_dead(state, job: dict[str, Any]) -> None:
    rec = state.runs.get(job["ref_id"])
    if rec:
        _state(rec, state.runs)


def _pending(run_dir: Path, st: dict[str, Any]) -> bool:
    q = (st.get("arms") or {}).get("queue") or {}
    qr = q.get("queue_round")
    return qr is not None and not q.get("done") and str(qr) in (al.read_answers(run_dir).get("submitted") or {})


@router.post("/runs/{run_id}/active-learning", status_code=202)
def start_active_learning(run_id: str, body: ALRequest, request: Request) -> dict[str, Any]:
    """F13: start a labelling queue on the run: fit its exact call on the seed labels, pick the first batch by
    uncertainty + diversity, and (pool = hide) simulate active vs random with the hidden labels as the oracle."""
    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    res = rec.get("result") or {}
    if rec["status"] != "succeeded":
        raise HTTPException(400, f"Run {run_id} is {rec['status']}; a labelling queue needs a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "A labelling queue refits the run's own config: only trained tabular runs have one.")
    if res.get("problem_type") not in ("binary", "multiclass"):
        raise HTTPException(400, f"Active learning picks rows by class-probability uncertainty: it needs a classification "
                                 f"run, and this one is {res.get('problem_type')}.")
    n = (res.get("data") or {}).get("train_rows") or 0
    if body.pool == "hide":
        n0 = int(round((1 - body.hide_share) * n))
        if n0 < 30:
            raise HTTPException(400, f"hide_share: {body.hide_share:g} of {n} training rows leaves {n0} labelled rows; "
                                     "a first fit needs at least 30.")
        if body.budget * body.rounds > n - n0:
            raise HTTPException(400, f"budget × rounds = {body.budget * body.rounds} is more than the {n - n0} hidden rows.")
    else:
        miss = int((res.get("data") or {}).get("dropped_missing_label_train") or 0)
        if not miss:
            raise HTTPException(400, "pool = missing: the run's dataset has no training rows with an empty label. Upload "
                                     "the unlabelled rows with a blank label cell, or use pool = hide.")
    sli = None
    if body.slice_id:
        sli = _slice_filter(request, rec, body.slice_id)
        n_in = sli["train_in_slice"]
        avail = int(n_in * body.hide_share) if body.pool == "hide" else sli["missing_in_slice"]
        if body.budget * body.rounds > avail:
            raise HTTPException(400, f"budget × rounds = {body.budget * body.rounds} is more than the about {avail} unlabelled "
                                     f"training rows inside the slice '{sli['label']}' ({n_in} training rows in it); "
                                     f"lower the budget to {max(1, avail // body.rounds)} or less.")
    store = request.app.state.runs
    busy = Q.active_of_kind(store.db, "al")
    if busy:
        other = busy[0]["ref_id"]
        raise HTTPException(409, f"A labelling queue is working (run {other}); wait for it or cancel it."
                            if other != run_id else "This run's labelling queue is already working.")
    with _LOCK:
        run_dir = Path(cfg["run_dir"])
        d = al.al_dir(run_dir)
        import shutil

        shutil.rmtree(d, ignore_errors=True)  # a new start replaces an open queue (the UI asks first)
        d.mkdir(parents=True, exist_ok=True)
        req = body.model_dump()
        req["slice"] = ({"id": sli["id"], "label": sli["label"], "conditions": sli["conditions"]} if sli else None)
        (d / "request.json").write_text(json.dumps(req, indent=1))
        al.write_answers(run_dir, {"answers": {}, "submitted": {}, "versions": []})
        al.write_state(run_dir, {"schema": al.SCHEMA, "status": "queued", "request": req, "created_at": now(),
                                 "fits_done": 0, "fits_total": None})
        _enqueue(request, rec)
    store.merge_result(run_id, active_learning=al.headline(al.read_state(run_dir)))
    return get_active_learning(run_id, request)


def _slice_filter(request: Request, rec: dict[str, Any], sid: str) -> dict[str, Any]:
    """F16: a weak slice of the run's latest search, and how many training rows fall inside it."""
    from . import slices as sl

    p = Path(rec["config"]["run_dir"]) / "slices.json"
    out = json.loads(p.read_text()) if p.exists() else None
    s = next((s for s in (out or {}).get("slices", []) if s["id"] == sid), None)
    if s is None:
        raise HTTPException(400, f"slice_id: no weak slice {sid!r} in this run's latest slice search (Explain → Weak Slices).")
    cfg = rec["config"]
    cols = {c["column"] for c in s["conditions"]} | {rec["label"]}
    data = frames.read(frames.resolve(cfg), columns=lambda c: c in cols)
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = holdout_mask(splits, len(data))
    m = sl.slice_mask(data, s["conditions"]) & ~is_h
    lab = data[rec["label"]].notna().to_numpy()
    return {"id": sid, "label": s["label"], "conditions": s["conditions"], "train_in_slice": int((m & lab).sum()),
            "missing_in_slice": int((m & ~lab).sum())}


def _open_queue(rec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int]:
    run_dir = Path(rec["config"]["run_dir"])
    st = al.read_state(run_dir)
    if not st or not st.get("arms"):
        raise HTTPException(409, "No labelling queue is open for this run.")
    q = st["arms"]["queue"]
    qr = q.get("queue_round")
    ans = al.read_answers(run_dir)
    if qr is None or q.get("done") or not q.get("queue"):
        raise HTTPException(409, "No labelling queue is open for this run.")
    if str(qr) in (ans.get("submitted") or {}):
        raise HTTPException(409, f"Round {qr} was already submitted; the next queue appears after the refit.")
    return st, ans, qr


@router.post("/runs/{run_id}/active-learning/answer")
def answer(run_id: str, body: Answers, request: Request) -> dict[str, Any]:
    """Answer queue items: confirm (the model's guess is right), relabel (give the label), skip (can't tell; the row
    leaves the pool), or null (clear). Stored per item with the reviewer's name."""
    rec = run_or_404(request, run_id)
    with _LOCK:
        st, ans, qr = _open_queue(rec)
        items = {it["id"]: it for it in st["arms"]["queue"]["queue"]}
        classes = set(st.get("classes") or [])
        for a in body.answers:
            it = items.get(a.id)
            if not it:
                raise HTTPException(404, f"No queue item '{a.id}' in round {qr}.")
            if a.answer is None:
                ans["answers"].pop(a.id, None)
                continue
            lab = (a.label or it["suggested"]) if a.answer == "relabel" else it["suggested"] if a.answer == "confirm" else None
            if lab is not None and classes and str(lab) not in classes:
                raise HTTPException(400, f"{a.id}: '{lab}' is not a class; one of {', '.join(sorted(classes))}.")
            ans["answers"][a.id] = {"answer": a.answer, "label": lab, "by": body.reviewer, "at": now()}
        al.write_answers(Path(rec["config"]["run_dir"]), ans)
    n = sum(1 for it in items if it in ans["answers"])
    return {"answered": len(body.answers), "round": qr, "queue_answered": n, "queue_items": len(items)}


@router.post("/runs/{run_id}/active-learning/simulate")
def simulate(run_id: str, request: Request) -> dict[str, Any]:
    """Fill the open queue's unanswered items from the hidden labels (pool = hide only), as reviewer
    'simulated-oracle': a stand-in for a person, marked as such everywhere it is shown."""
    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    with _LOCK:
        st, ans, qr = _open_queue(rec)
        if (st.get("pool") or {}).get("source") != "hide":
            raise HTTPException(400, "The pool's labels are really missing, so there is no oracle to simulate the labeller.")
        df = frames.read(frames.resolve(cfg), columns=[cfg["label"]])
        n = 0
        for it in st["arms"]["queue"]["queue"]:
            if it["id"] in ans["answers"]:
                continue
            truth = str(df[cfg["label"]].iloc[it["row"]])
            ans["answers"][it["id"]] = {"answer": "confirm" if truth == str(it["suggested"]) else "relabel", "label": truth,
                                        "by": "simulated-oracle", "at": now()}
            n += 1
        al.write_answers(Path(cfg["run_dir"]), ans)
    return {"answered": n, "round": qr, "reviewer": "simulated-oracle"}


def _make_version(request: Request, rec: dict[str, Any], st: dict[str, Any], ans: dict[str, Any], qr: int) -> dict[str, Any]:
    """The labelled set after round qr as a dataset version: the run's data with the still-unlabelled pool rows'
    labels empty and the answers filled in; the same split record (holdout and folds unchanged), with lineage."""
    from ..splits import save_splits

    cfg = rec["config"]
    ds = request.app.state.datasets
    label = cfg["label"]
    df = frames.read(frames.resolve(cfg)).reset_index(drop=True)  # rows by position (queue rows, split positions)
    q = st["arms"]["queue"]
    answered = {it["row"]: ans["answers"][it["id"]] for it in q["queue"] if it["id"] in ans["answers"]}
    new = {r: a["label"] for r, a in answered.items() if a["answer"] in ("confirm", "relabel")}
    labels = {int(k): v for k, v in (q.get("labels") or {}).items()} | new
    labelled = set(q["labelled"]) | set(new)
    src = rec["dataset_id"]
    changes = []
    # P07 (P04's handoff): the label keeps its kind. An integer label stays a number column (float while cells are
    # empty; derive() writes it back as the parent's BIGINT), and an answer given as text ("1") becomes the class value.
    num = pd.api.types.is_numeric_dtype(df[label]) and not pd.api.types.is_bool_dtype(df[label])
    cmap: dict[str, Any] = {}
    for c in pd.unique(df[label].dropna()):
        cmap[str(c)] = c
        if isinstance(c, (float, np.floating)) and float(c).is_integer():
            cmap.setdefault(str(int(c)), c)

    def as_label(v: Any) -> Any:
        if str(v) in cmap:
            return cmap[str(v)]
        if num:
            try:
                f = float(str(v).strip())
                return int(f) if f.is_integer() else f
            except ValueError:
                return v
        return v

    if (st.get("pool") or {}).get("source") == "hide":  # the hidden labels that are still unlabelled stay empty
        hidden = [r for r in al_train_rows(cfg) if r not in labelled]
        df[label] = df[label].astype("float64") if num else df[label].astype(object)
        df.loc[hidden, label] = np.nan
        changes.append({"op": "hide", "rows": len(hidden), "share": st["pool"]["hide_share"]})
    for r, v in sorted(labels.items()):
        a = answered.get(r)
        df.at[r, label] = as_label(v)
        changes.append({"op": "label", "row": int(r), "root_row": int(r), "label": str(v),
                        "by": (a or {}).get("by"), "round": qr if a else None})
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    versions = ans.get("versions") or []
    n_ver = len(versions) + 2
    extra = {"version": n_ver, "active_learning": {"run_id": rec["id"], "round": qr},
             "labelled": len(new), "relabelled": 0, "dropped": 0, "synthetic": 0, "relabelled_holdout": 0}
    name = (ds.db.get_dataset(src) or {}).get("name", src).replace(" (sample)", "")
    new_rec = ds.derive(src, df, None, "active_learning", changes, splits=sp, extra=extra,  # P1: root rows from the parent
                        name=f"{name} · labelled r{qr}",
                        note=f"Active learning on {rec['id']}: round {qr}, {len(new)} rows labelled "
                             f"({len(labelled)} labelled training rows in all)")
    split = cfg.get("split") or {}
    if split.get("strategy") and split.get("strategy") != "random-stratified":  # F6: keep the structured split too
        p = ds.split_path(new_rec["id"], label, split["strategy"], split.get("params"))
        p.parent.mkdir(parents=True, exist_ok=True)
        save_splits(p, {**sp, "derived_from": src})
    return {"id": new_rec["id"], "name": new_rec["name"], "round": qr, "labelled_this_round": len(new),
            "labelled_total": len(labelled), "rows": new_rec["rows"], "created_at": now()}


def al_train_rows(cfg: dict[str, Any]) -> list[int]:
    """data.csv rows of the run's training set (not holdout, tuning or excluded), with a label."""
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(sp["fold"], dtype=int)
    hold = set(np.asarray(sp["holdout"], dtype=int).tolist())
    structured = bool(sp.get("params"))
    return [i for i in range(len(fold)) if i not in hold and not (structured and fold[i] in (-2, -3))]


@router.post("/runs/{run_id}/active-learning/submit")
def submit(run_id: str, request: Request) -> dict[str, Any]:
    """Close the round: answered rows join the labelled set (open items count as skipped), a dataset version with
    lineage is made, and the run's call is refitted; the next queue follows."""
    rec = run_or_404(request, run_id)
    run_dir = Path(rec["config"]["run_dir"])
    with _LOCK:
        st, ans, qr = _open_queue(rec)
        try:
            ver = _make_version(request, rec, st, ans, qr)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Could not make the dataset version: {type(e).__name__}: {e}") from e
        ans.setdefault("versions", []).append(ver)
        ans.setdefault("submitted", {})[str(qr)] = {"at": now(), "dataset_id": ver["id"]}
        al.write_answers(run_dir, ans)
        if not Q.active_for(request.app.state.db, "run", run_id, "al"):  # else its job runs the round when it finishes
            st["status"] = "queued"
            al.write_state(run_dir, st)
            _enqueue(request, rec)
    return {"round": qr, "dataset_version": ver, "status": "refitting"}


@router.post("/runs/{run_id}/active-learning/cancel")
def cancel(run_id: str, request: Request) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    run_dir = Path(rec["config"]["run_dir"])
    db = request.app.state.db
    with _LOCK:
        job = Q.active_for(db, "run", run_id, "al")
        before = Q.cancel(db, job["id"]) if job else None
        st = al.read_state(run_dir)
        if before and before["status"] == "leased":
            pass  # its worker stops the process and closes the state
        elif before or (st and st.get("status") == "awaiting_labels"):
            st = st or {}
            st.update(status="cancelled", finished_at=now(), error="Cancelled.")
            al.write_state(run_dir, st)
            request.app.state.runs.merge_result(run_id, active_learning=al.headline(st))
        else:
            raise HTTPException(400, "No labelling queue is working or open for this run.")
    return {"run_id": run_id, "cancelled": True}


def bind(db) -> None:
    global _DB
    _DB = db


def shutdown() -> None:
    """Nothing to stop: labelling queues run on the queue's workers and survive the API."""


def active() -> list[str]:
    return [j["ref_id"] for j in Q.active_of_kind(_DB, "al")] if _DB is not None else []

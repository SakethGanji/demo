"""F13 routes: start an active-learning labelling queue on a finished classification run, answer its queue (the
Ground-Truth Inbox's answer shape), submit a round (a dataset version with lineage, then a refit), read the curves and
the active-vs-random comparison, cancel. The work runs in its own process (`python -m app.engine.active_learning`,
see active_learning.py), one queue at a time across the workbench."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config
from . import active_learning as al
from .runner import dev_reload
from .store import now

router = APIRouter(prefix="/api")

_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()


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


def _run(request: Request, run_id: str) -> dict[str, Any]:
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    return rec


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return False
    return "app.engine.active_learning" in cmd


def _kill(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def _running(run_id: str) -> bool:
    with _LOCK:
        p = _PROCS.get(run_id)
    return p is not None and p.poll() is None


def _state(rec: dict[str, Any], store: Any) -> dict[str, Any] | None:
    run_dir = Path(rec["config"]["run_dir"])
    st = al.read_state(run_dir)
    if st and st.get("status") in ("queued", "running"):
        with _LOCK:
            p = _PROCS.get(rec["id"])
        if (p is None or p.poll() is not None) and not _alive(st.get("pid")):
            if p is not None and p.poll() is not None:
                return al.read_state(run_dir)
            st.update(status="failed", error="The labelling queue's process stopped (the API restarted).", finished_at=now())
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
    rec = _run(request, run_id)
    st = _state(rec, request.app.state.runs)
    if not st:
        return {"run_id": run_id, "status": "none", "defaults": defaults(rec)}
    return {"run_id": run_id, **_view(rec, st, al.read_answers(Path(rec["config"]["run_dir"]))), "defaults": defaults(rec)}


def _spawn(request: Request, rec: dict[str, Any]) -> None:
    run_id = rec["id"]
    run_dir = Path(rec["config"]["run_dir"])
    store = request.app.state.runs
    d = al.al_dir(run_dir)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "MLFLOW_DISABLE_AGENT_HINT": "1",
           "PYTHONPATH": os.pathsep.join(x for x in (str(config.BACKEND_DIR), os.environ.get("PYTHONPATH", "")) if x)}
    watch = str(os.getppid()) if dev_reload() else str(os.getpid())
    with open(d / "active_learning.log", "ab") as out:
        p = subprocess.Popen([sys.executable, "-m", "app.engine.active_learning", str(run_dir), watch],
                             cwd=config.BACKEND_DIR, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    _PROCS[run_id] = p
    _CANCELLED.discard(run_id)

    def wait():
        code = p.wait()
        _kill(p.pid, signal.SIGKILL)
        with _LOCK:
            st = al.read_state(run_dir) or {}
            superseded = _PROCS.get(run_id) is not p  # a submit already started the next process
            cancelled = run_id in _CANCELLED and not superseded
            if cancelled:
                _CANCELLED.discard(run_id)
            # answers submitted while the process was finishing: run them now
            again = not cancelled and not superseded and st.get("status") == "awaiting_labels" and _pending(run_dir, st)
        if superseded:
            return
        if st.get("status") in ("queued", "running"):
            st.update(status="cancelled" if cancelled else "failed", finished_at=now(),
                      error="Cancelled." if cancelled else f"The labelling queue's process exited with code {code}.")
            store.merge_result(run_id, active_learning=al.headline(st))
            al.write_state(run_dir, st)
        elif again:
            with _LOCK:
                _spawn(request, store.get(run_id))

    threading.Thread(target=wait, name=f"al-{run_id}", daemon=True).start()


def _pending(run_dir: Path, st: dict[str, Any]) -> bool:
    q = (st.get("arms") or {}).get("queue") or {}
    qr = q.get("queue_round")
    return qr is not None and not q.get("done") and str(qr) in (al.read_answers(run_dir).get("submitted") or {})


@router.post("/runs/{run_id}/active-learning", status_code=202)
def start_active_learning(run_id: str, body: ALRequest, request: Request) -> dict[str, Any]:
    """F13: start a labelling queue on the run: fit its exact call on the seed labels, pick the first batch by
    uncertainty + diversity, and (pool = hide) simulate active vs random with the hidden labels as the oracle."""
    rec = _run(request, run_id)
    cfg = rec["config"]
    res = rec.get("result") or {}
    if rec["status"] != "succeeded":
        raise HTTPException(400, f"Run {run_id} is {rec['status']}; a labelling queue needs a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly") or cfg.get("import"):
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
    with _LOCK:
        for other, p in _PROCS.items():
            if p.poll() is None:
                ost = al.read_state(Path(store.get(other)["config"]["run_dir"])) if store.get(other) else None
                if ost and ost.get("status") not in ("queued", "running"):
                    try:  # finished its work, and its process is only cleaning up
                        p.wait(30)
                        continue
                    except subprocess.TimeoutExpired:
                        pass
                raise HTTPException(409, f"A labelling queue is working (run {other}); wait for it or cancel it."
                                    if other != run_id else "This run's labelling queue is already working.")
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
        _spawn(request, rec)
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
    data = pd.read_csv(cfg["csv_path"], usecols=lambda c: c in cols)
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = np.zeros(len(data), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
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
    rec = _run(request, run_id)
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
    rec = _run(request, run_id)
    cfg = rec["config"]
    with _LOCK:
        st, ans, qr = _open_queue(rec)
        if (st.get("pool") or {}).get("source") != "hide":
            raise HTTPException(400, "The pool's labels are really missing, so there is no oracle to simulate the labeller.")
        df = pd.read_csv(cfg["csv_path"], usecols=[cfg["label"]])
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
    df = pd.read_csv(cfg["csv_path"])
    q = st["arms"]["queue"]
    answered = {it["row"]: ans["answers"][it["id"]] for it in q["queue"] if it["id"] in ans["answers"]}
    new = {r: a["label"] for r, a in answered.items() if a["answer"] in ("confirm", "relabel")}
    labels = {int(k): v for k, v in (q.get("labels") or {}).items()} | new
    labelled = set(q["labelled"]) | set(new)
    src = rec["dataset_id"]
    changes = []
    if (st.get("pool") or {}).get("source") == "hide":  # the hidden labels that are still unlabelled stay empty
        hidden = [r for r in al_train_rows(cfg) if r not in labelled]
        df[label] = df[label].astype(object)
        df.loc[hidden, label] = np.nan
        changes.append({"op": "hide", "rows": len(hidden), "share": st["pool"]["hide_share"]})
    cmap = {str(c): c for c in pd.unique(df[label].dropna())}
    for r, v in sorted(labels.items()):
        a = answered.get(r)
        df.at[r, label] = cmap.get(str(v), v)
        changes.append({"op": "label", "row": int(r), "root_row": int(r), "label": str(v),
                        "by": (a or {}).get("by"), "round": qr if a else None})
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    versions = ans.get("versions") or []
    root = src
    lin_parent = ds.lineage(src)
    if lin_parent and lin_parent.get("root"):
        root = lin_parent["root"]
    n_ver = len(versions) + 2
    lineage = {"parent": src, "root": root, "version": n_ver, "active_learning": {"run_id": rec["id"], "round": qr},
               "labelled": len(new), "relabelled": 0, "dropped": 0, "synthetic": 0, "relabelled_holdout": 0,
               "root_rows": list(range(len(df))), "changes": changes}
    name = (ds.db.get_dataset(src) or {}).get("name", src).replace(" (sample)", "")
    new_rec = ds.create_version(src, df, {**sp, "derived_from": src},
                                name=f"{name} · labelled r{qr}",
                                note=f"Active learning on {rec['id']}: round {qr}, {len(new)} rows labelled "
                                     f"({len(labelled)} labelled training rows in all)", lineage=lineage)
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
    rec = _run(request, run_id)
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
        p = _PROCS.get(run_id)
        alive = (p is not None and p.poll() is None) or _alive(st.get("pid"))
        if not alive:
            st["status"] = "queued"
            al.write_state(run_dir, st)
            _spawn(request, rec)
    return {"round": qr, "dataset_version": ver, "status": "refitting"}


@router.post("/runs/{run_id}/active-learning/cancel")
def cancel(run_id: str, request: Request) -> dict[str, Any]:
    rec = _run(request, run_id)
    run_dir = Path(rec["config"]["run_dir"])
    with _LOCK:
        p = _PROCS.get(run_id)
        st = al.read_state(run_dir)
        if p is None or p.poll() is not None:
            if st and _alive(st.get("pid")):
                _CANCELLED.add(run_id)
                _kill(st["pid"])
            elif st and st.get("status") == "awaiting_labels":
                st.update(status="cancelled", finished_at=now(), error="Cancelled.")
                al.write_state(run_dir, st)
                request.app.state.runs.merge_result(run_id, active_learning=al.headline(st))
            else:
                raise HTTPException(400, "No labelling queue is working or open for this run.")
        else:
            _CANCELLED.add(run_id)
            _kill(p.pid)
    return {"run_id": run_id, "cancelled": True}


def shutdown() -> None:
    if dev_reload():
        return
    with _LOCK:
        procs = list(_PROCS.values())
    for p in procs:
        if p.poll() is None:
            _kill(p.pid)


def active() -> list[str]:
    with _LOCK:
        return [r for r, p in _PROCS.items() if p.poll() is None]

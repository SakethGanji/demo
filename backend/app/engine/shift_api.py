"""F15 routes: adversarial validation (shift check) of a run's data. The check runs in its own process
(`python -m app.engine.shift <job_dir>`, see shift.py), one at a time across the workbench.

Sources (the two sets of rows, R = reference, N = new):
  holdout  R = the run's training rows (every non-holdout row with a label), N = its holdout rows;
  time     R = the run's dataset before a cut-off τ on a date column (F6's), N = on or after τ;
  batch    R = the run's training rows (the drift reference), N = a batch scored by a registered version of the run.

Columns that would separate the sets by construction are left out and reported: the label, the run's dropped
columns, identifiers, entity ids and dates (always in a time comparison; dates are left out by default elsewhere too).
"""

from __future__ import annotations

import hashlib
import json
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..splits import holdout_mask
from . import shift as sh
from .jobs import alive, dev_reload, kill_group, spawn, watch_arg
from .store import now
from ..http import run_or_404

router = APIRouter(prefix="/api")

_LOCK = threading.Lock()
_PROCS: dict[str, tuple[str, subprocess.Popen]] = {}  # job dir → (run id, process)
_CANCELLED: set[str] = set()
MIN_ROWS = 20


class ShiftRequest(BaseModel):
    source: Literal["holdout", "time", "batch"] = Field("holdout", description="Which two sets of rows to compare.")
    batch_id: str | None = Field(None, description="source = batch: a batch scored by a registered version of this run.")
    time_column: str | None = Field(None, description="source = time: the date column (default: the run's F6 time column, "
                                                      "else the dataset's first date column).")
    cutoff: str | None = Field(None, description="source = time: τ; reference = before τ, new = on or after τ (default: the "
                                                 "run's out-of-time cut-off, else the date at the 80th row percentile).")
    exclude: list[str] = Field(default_factory=list, description="More columns to leave out.")
    include: list[str] = Field(default_factory=list, description="Columns to keep although the default rules leave them out "
                                                                "(never the label).")
    null_refits: int = Field(sh.DEFAULT_NULL_REFITS, ge=0, le=20, description="R: refits on shuffled origin labels.")
    permutations: int = Field(sh.DEFAULT_PERMUTATIONS, ge=100, le=100000, description="B: label shuffles of the fixed OOF scores.")
    shuffle_sets: int = Field(sh.DEFAULT_SHUFFLES, ge=1, le=50, description="Permutation-importance shuffles per fold model.")
    clip: float = Field(sh.DEFAULT_CLIP, ge=0, lt=0.5, description="Weights are clipped at this quantile and 1 − it.")
    time_limit: float | None = Field(None, gt=0, description="Seconds per fit (none: no limit).")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to fit() unchanged (overrides "
                                                                         "num_bag_folds = 5, hyperparameters = {'GBM': {}}, …).")


def _check_run(rec: dict[str, Any]) -> None:
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(400, f"Run {rec['id']} is {rec['status']}; a shift check needs a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "Shift checks compare the rows of a trained tabular run (not forecast, anomaly or imported runs).")


def _kinds(request: Request, rec: dict[str, Any]) -> dict[str, str]:
    ds = request.app.state.db.get_dataset(rec["dataset_id"])
    types = {c["name"]: c["type"] for c in (ds or {}).get("profile", {}).get("columns", [])}
    types.update(rec["config"].get("column_types") or {})
    return {c: ("number" if t == "number" else "text" if t == "text" else "id" if t == "id" else "category") for c, t in types.items()}


def _split_rows(cfg: dict[str, Any], df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(reference rows, holdout rows): every non-holdout row with a label (the drift reference), the labelled holdout."""
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = holdout_mask(sp, len(df))
    lab = df[cfg["label"]].notna().to_numpy()
    return np.flatnonzero(~is_h & lab), np.flatnonzero(is_h & lab)


def _time_defaults(request: Request, rec: dict[str, Any], opts: dict[str, Any]) -> tuple[str | None, str | None]:
    sp = rec["config"].get("split") or {}
    params = sp.get("params") or {}
    tcol = params.get("time_column") or (opts["date_columns"][0]["column"] if opts.get("date_columns") else None)
    cut = sp.get("cutoff") if params.get("time_column") == tcol and sp.get("cutoff") else None
    return tcol, cut


def plan(request: Request, rec: dict[str, Any], body: ShiftRequest) -> dict[str, Any]:
    """Resolve the two sets, the columns and the reasons for every column left out. 400 on a bad request."""
    from .. import splits as splits_mod

    cfg = rec["config"]
    label = cfg["label"]
    dsm = request.app.state.datasets
    df = pd.read_csv(cfg["csv_path"])
    ref_rows, hold_rows = _split_rows(cfg, df)
    opts = dsm.split_options(rec["dataset_id"], label)
    dates = [d["column"] for d in opts.get("date_columns") or []]
    entities = [g["column"] for g in opts.get("group_columns") or []]
    kinds_all = _kinds(request, rec)
    new_df = df
    ref = {"csv": cfg["csv_path"], "rows": ref_rows.tolist(), "name": "training rows", "rows_n": len(ref_rows),
           "what": "every non-holdout row of the run's dataset with a label (the drift reference)"}
    extra: dict[str, Any] = {}
    if body.source == "holdout":
        key = "holdout"
        new = {"csv": cfg["csv_path"], "rows": hold_rows.tolist(), "name": "holdout rows", "rows_n": len(hold_rows),
               "what": f"the run's holdout ({(cfg.get('split') or {}).get('strategy', 'random-stratified')} split)"}
    elif body.source == "time":
        tdef, cdef = _time_defaults(request, rec, opts)
        tcol = body.time_column or tdef
        if not tcol:
            raise HTTPException(400, "This dataset has no date column to compare two periods; use another source.")
        if tcol not in df.columns or tcol == label:
            raise HTTPException(400, f"time_column: '{tcol}' is not a date column of the dataset.")
        t = splits_mod.time_values(df[tcol])
        ok = t.notna().to_numpy()
        if ok.mean() < 0.9:
            raise HTTPException(400, f"Only {ok.mean():.0%} of '{tcol}' could be read as dates or numbers.")
        cut_s = body.cutoff or cdef
        try:
            cut = splits_mod._parse_cutoff(cut_s, t) if cut_s else t[ok].sort_values().iloc[int(np.floor(0.8 * ok.sum()))]
        except ValueError as e:
            raise HTTPException(400, f"cutoff: {e}") from e
        before = np.flatnonzero(ok & (t < cut).to_numpy())
        after = np.flatnonzero(ok & (t >= cut).to_numpy())
        cut_txt = splits_mod._fmt_time(cut)
        key = "time-" + hashlib.sha256(f"{tcol}|{cut_txt}".encode()).hexdigest()[:8]
        ref = {"csv": cfg["csv_path"], "rows": before.tolist(), "name": f"before {cut_txt}", "rows_n": len(before),
               "what": f"rows of the dataset with {tcol} < {cut_txt}"}
        new = {"csv": cfg["csv_path"], "rows": after.tolist(), "name": f"on or after {cut_txt}", "rows_n": len(after),
               "what": f"rows of the dataset with {tcol} ≥ {cut_txt}"}
        extra = {"time_column": tcol, "cutoff": cut_txt, "cutoff_source": "request" if body.cutoff else
                 "the run's out-of-time cut-off" if cdef else "the date at the 80th row percentile",
                 "from": splits_mod._fmt_time(t[ok].min()), "to": splits_mod._fmt_time(t[ok].max()),
                 "undated_rows": int((~ok).sum())}
    else:
        if not body.batch_id:
            raise HTTPException(400, "source = batch needs batch_id.")
        reg = request.app.state.registry
        b = reg.get_batch(body.batch_id)
        if not b:
            raise HTTPException(404, f"No batch with id '{body.batch_id}'.")
        v = reg.get_version(b["version_id"]) or {}
        if v.get("run_id") != rec["id"]:
            raise HTTPException(400, f"Batch {body.batch_id} was scored by {b['version_id']}, which is not a version of run {rec['id']}.")
        path = request.app.state.settings.data_dir / "registry" / "batches" / body.batch_id / "input.csv"
        if not path.exists():
            raise HTTPException(404, f"The input file of batch {body.batch_id} is gone.")
        new_df = pd.read_csv(path)
        key = f"batch-{body.batch_id}"
        new = {"csv": str(path), "rows": None, "name": f"batch {b.get('filename') or body.batch_id}", "rows_n": len(new_df),
               "what": f"the rows of batch {body.batch_id} ({b.get('filename')}), scored by {b['version_id']}",
               "batch_id": body.batch_id, "version_id": b["version_id"], "max_psi": ((b.get("result") or {}).get("drift") or {}).get("max_psi")}
    for side, s in (("reference", ref), ("new", new)):
        if s["rows_n"] < MIN_ROWS:
            raise HTTPException(400, f"The {side} set ({s['name']}) has {s['rows_n']} rows; a shift check needs at least {MIN_ROWS} per side.")

    # Columns: everything in both sets, minus what separates them by construction.
    unknown = [c for c in body.include + body.exclude if c not in df.columns and c not in new_df.columns]
    if unknown:
        raise HTTPException(400, f"Unknown column(s): {', '.join(unknown)}.")
    if label in body.include:
        raise HTTPException(400, f"'{label}' is the label; a shift check compares the inputs.")
    tcol_self = extra.get("time_column")
    excluded: list[dict[str, str]] = []
    feats: list[str] = []
    drop = set(cfg.get("drop_columns") or [])
    for c in df.columns:
        reason = None
        if c == label:
            reason = ("the label: this check asks whether the inputs x differ; a change in y given x is not covariate shift "
                      "(and a new batch has no label)")
        elif c in body.exclude:
            reason = "left out in the request"
        elif c in body.include:
            reason = None  # kept on request (Advanced), even the cut-off's own date: to see why it is left out
        elif c == tcol_self:
            reason = "the date the periods are cut on: it separates them perfectly (AUC = 1) by construction"
        elif c in drop:
            reason = "dropped from the run's features (drop_columns): the model never reads it"
        elif c in dates:
            reason = ("a date: rows of a later period carry later dates, so it separates any earlier and later rows "
                      "by construction; time shift is checked on the other columns")
        elif c in entities:
            reason = "an entity id (customer, account): new rows bring new ids, so it separates who, not what"
        elif kinds_all.get(c) == "id":
            reason = "a row identifier: unique per row and usually assigned in order, so it encodes when a row arrived"
        if reason is None and c not in new_df.columns:
            reason = "not in the new rows"
        if reason:
            excluded.append({"column": c, "reason": reason})
        else:
            feats.append(c)
    for c in new_df.columns:
        if c not in df.columns:
            excluded.append({"column": c, "reason": "only in the new rows"})
    if not feats:
        raise HTTPException(400, "No columns are left to compare after the exclusions.")
    kinds = {c: (kinds_all.get(c) if kinds_all.get(c) in ("number", "category", "text") else "category") for c in feats}
    req = body.model_dump()
    return {"key": key, "source": body.source, "run_id": rec["id"], "dataset_id": rec["dataset_id"], "label": label,
            "reference": ref, "new": new, "features": feats, "kinds": kinds, "excluded": excluded, "extra": extra,
            "request": req, "fit_kwargs": body.fit_kwargs, "time_limit": body.time_limit, "null_refits": body.null_refits,
            "permutations": body.permutations, "shuffle_sets": body.shuffle_sets, "clip": body.clip,
            "db_path": str(request.app.state.settings.db_path)}


def _public(st: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in st.items() if k not in ("traceback",)}


def _state(rec: dict[str, Any], key: str, store: Any) -> dict[str, Any] | None:
    d = sh.job_dir(Path(rec["config"]["run_dir"]), key)
    st = sh.read_state(d)
    if st and st.get("status") in ("queued", "running"):
        with _LOCK:
            item = _PROCS.get(str(d))
        p = item[1] if item else None
        if (p is None or p.poll() is not None) and not alive(st.get("pid"), "app.engine.shift"):
            if p is not None and p.poll() is not None:
                return sh.read_state(d)
            st.update(status="failed", error="The shift check's process stopped (the API restarted).", finished_at=now())
            store.merge_result(rec["id"], shift=sh.summary(st))
            sh.write_state(d, st)
    return st


def actions(request: Request, rec: dict[str, Any], st: dict[str, Any]) -> list[dict[str, Any]]:
    """The remedies, each a concrete request: an out-of-time twin (F6), a Gym patch dropping a shifting column, or
    importance weights as a dataset column (`shift_weight`) passed as sample_weight."""
    from ..gym.api import run_body
    from ..vela.findings import make_patch

    res = st.get("result") or {}
    if st.get("status") != "succeeded":
        return []
    out: list[dict[str, Any]] = []
    sp = rec["config"].get("split") or {}
    opts = request.app.state.datasets.split_options(rec["dataset_id"], rec["label"])
    dates = opts.get("date_columns") or []
    if dates:
        tcol = (st.get("extra") or {}).get("time_column") or dates[0]["column"]
        groups = opts.get("group_columns") or []
        oot = sp.get("strategy") in ("time", "time-group")
        spec = {"strategy": "time", "time_column": tcol}
        out.append({"id": "out-of-time", "kind": "split-twin", "title": "Use Out-Of-Time Validation",
                    "available": not oot, "split_spec": spec, "group_spec": {"strategy": "time-group", "time_column": tcol,
                                                                               "group_column": groups[0]["column"]} if groups else None,
                    "why": (f"This run is already validated out of time on {tcol}." if oot else
                            f"Validate on rows after a cut-off on {tcol} (F6): the holdout then looks like the future, so the "
                            "score shows what the shift costs. POST /api/runs/{id}/split-twin with this split_spec."),
                    "endpoint": f"/api/runs/{rec['id']}/split-twin"})
    base = run_body(rec)
    drop0 = list(base.get("drop_columns") or [])
    for f in (res.get("features") or [])[:3]:
        if not f.get("significant") or not (res.get("verdict") or {}).get("shift"):
            continue
        c = f["feature"]
        patch = make_patch(base, f"shift-drop-{c}", f"Drop {c}", [("drop_columns", list(dict.fromkeys(drop0 + [c])),
                                                                     f"leave {c} out: it shifts most between the two sets")])
        out.append({"id": f"drop-{c}", "kind": "gym-patch", "title": f"Drop {c}", "feature": c, "patch": patch,
                    "why": (f"{c} carries {f['importance']:.4f} of the origin classifier's AUC (95% CI "
                            f"{f['ci'][0]:.4f}–{f['ci'][1]:.4f}){', PSI ' + format(f['psi'], '.3f') if f.get('psi') is not None else ''}. "
                            "Dropping it trades its signal for robustness; transforming it (a ratio, a rank within period) "
                            "is the alternative.") if f.get("ci") else f"{c} shifts most."})
    w = res.get("weights") or {}
    out.append({"id": "weights", "kind": "weights-version", "title": "Importance Weighting",
                "available": rec["dataset_id"] is not None, "column": sh.WEIGHT_COLUMN,
                "ess": w.get("ess"), "ess_share": w.get("ess_share"),
                "why": (f"Weight the {st['sets']['reference']['name']} by w(x) = p_N(x)/p_R(x) so training looks like the "
                        f"{st['sets']['new']['name']}: saved as column {sh.WEIGHT_COLUMN} of a dataset version and passed "
                        f"as sample_weight. ESS {w.get('ess', 0):,.0f} of {w.get('n_ref', 0):,} rows ({w.get('ess_share', 0):.1%})."),
                "endpoint": f"/api/runs/{rec['id']}/shift/{st['key']}/weights-version"})
    return out


@router.get("/runs/{run_id}/shift")
def list_shift(run_id: str, request: Request) -> dict[str, Any]:
    """F15: this run's shift checks (one per source: holdout, a time cut, a batch) and what a new one can compare."""
    rec = run_or_404(request, run_id)
    store = request.app.state.runs
    root = sh.shift_root(Path(rec["config"]["run_dir"]))
    checks = []
    for d in sorted(root.glob("*/")) if root.exists() else []:
        st = _state(rec, d.name, store)
        if st:
            checks.append({**(sh.summary(st) or {}), "created_at": st.get("created_at"), "stage": st.get("stage")})
    checks.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    opts = request.app.state.datasets.split_options(rec["dataset_id"], rec["label"]) if rec.get("dataset_id") else {}
    tcol, cut = _time_defaults(request, rec, opts) if opts else (None, None)
    batches = []
    reg = request.app.state.registry
    for v in reg.list_versions() if hasattr(reg, "list_versions") else []:
        if v.get("run_id") != run_id:
            continue
        for b in reg.list_batches(v["id"]):
            if b.get("status") == "succeeded":
                batches.append({"id": b["id"], "version_id": v["id"], "filename": b.get("filename"), "created_at": b.get("created_at"),
                                "rows": ((b.get("result") or {}).get("summary") or {}).get("n_rows"),
                                "max_psi": ((b.get("result") or {}).get("drift") or {}).get("max_psi")})
    return {"run_id": run_id, "checks": checks, "latest": (rec.get("result") or {}).get("shift"),
            "options": {"date_columns": opts.get("date_columns") or [], "time_column": tcol, "cutoff": cut, "batches": batches,
                        "defaults": {"null_refits": sh.DEFAULT_NULL_REFITS, "permutations": sh.DEFAULT_PERMUTATIONS,
                                     "shuffle_sets": sh.DEFAULT_SHUFFLES, "clip": sh.DEFAULT_CLIP, "num_bag_folds": sh.DEFAULT_FOLDS,
                                     "hyperparameters": {"GBM": {}}}}}


@router.get("/runs/{run_id}/shift/{key}")
def get_shift(run_id: str, key: str, request: Request) -> dict[str, Any]:
    """F15: one shift check: status while it runs; then the AUC with its DeLong CI, the permutation and refit nulls,
    the verdict, the shifting features (importance with CI, PSI), the excluded columns, the weights and the actions."""
    rec = run_or_404(request, run_id)
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise HTTPException(400, "Bad key.")
    st = _state(rec, key, request.app.state.runs)
    if not st:
        raise HTTPException(404, f"Run {run_id} has no shift check '{key}'.")
    return {"run_id": run_id, **_public(st), "summary": sh.summary(st), "actions": actions(request, rec, st)}


@router.get("/runs/{run_id}/shift/{key}/rows")
def shift_rows(run_id: str, key: str, request: Request, offset: int = 0, limit: int = 100000) -> dict[str, Any]:
    """Per row: its set, origin o, row number (in the dataset, or in the batch file) and the out-of-fold P(o = 1 | x);
    reference rows also carry their weight w. Paged."""
    rec = run_or_404(request, run_id)
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise HTTPException(400, "Bad key.")
    d = sh.job_dir(Path(rec["config"]["run_dir"]), key)
    if not (d / "scores.csv").exists():
        raise HTTPException(404, "No finished shift check with this key.")
    sc = pd.read_csv(d / "scores.csv")
    w = pd.read_csv(d / "weights.csv")
    sc["w"] = np.nan
    sc.loc[sc["set"] == "reference", "w"] = w["w"].to_numpy()
    page = sc.iloc[offset: offset + limit]
    return {"run_id": run_id, "key": key, "total": len(sc), "offset": offset,
            "rows": [{"set": r.set, "origin": int(r.origin), "row": int(r.row), "p_new_oof": float(r.p_new_oof),
                      "w": None if pd.isna(r.w) else float(r.w)} for r in page.itertuples()]}


def start(request: Request, rec: dict[str, Any], body: ShiftRequest) -> dict[str, Any]:
    _check_run(rec)
    p = plan(request, rec, body)
    run_dir = Path(rec["config"]["run_dir"])
    d = sh.job_dir(run_dir, p["key"])
    store = request.app.state.runs
    with _LOCK:
        for jd, (rid, proc) in _PROCS.items():
            if proc.poll() is None:
                ost = sh.read_state(Path(jd))
                if ost and ost.get("status") not in ("queued", "running"):
                    try:
                        proc.wait(30)
                        continue
                    except subprocess.TimeoutExpired:
                        pass
                raise HTTPException(409, f"A shift check is already running (run {rid}); wait for it or cancel it.")
        d.mkdir(parents=True, exist_ok=True)
        (d / "request.json").write_text(json.dumps(p, indent=1))
        sets = {s: {"name": p[s]["name"], "rows": p[s]["rows_n"], "what": p[s]["what"],
                    **{k: p[s][k] for k in ("batch_id", "version_id", "max_psi") if k in p[s]}} for s in ("reference", "new")}
        sh.write_state(d, {"schema": sh.SCHEMA, "key": p["key"], "source": p["source"], "status": "queued",
                           "created_at": now(), "request": p["request"], "sets": sets, "features": p["features"],
                           "kinds": p["kinds"], "excluded": p["excluded"], "extra": p["extra"], "stage": "queued"})
        proc = spawn("app.engine.shift", [str(d), watch_arg()], d / "shift.log")
        _PROCS[str(d)] = (rec["id"], proc)
        _CANCELLED.discard(str(d))

    def wait():
        code = proc.wait()
        kill_group(proc.pid, signal.SIGKILL)
        st = sh.read_state(d) or {}
        with _LOCK:
            cancelled = str(d) in _CANCELLED
            _CANCELLED.discard(str(d))
        if st.get("status") in ("queued", "running"):
            st.update(status="cancelled" if cancelled else "failed", finished_at=now(), stage=None,
                      error="Cancelled." if cancelled else f"The shift check's process exited with code {code}.")
            store.merge_result(rec["id"], shift=sh.summary(st))
            sh.write_state(d, st)

    threading.Thread(target=wait, name=f"shift-{rec['id']}-{p['key']}", daemon=True).start()
    return get_shift(rec["id"], p["key"], request)


@router.post("/runs/{run_id}/shift", status_code=202)
def start_shift(run_id: str, body: ShiftRequest, request: Request) -> dict[str, Any]:
    """F15: start a shift check: an AutoGluon classifier (bagged, scored out of fold) tells the reference rows from the
    new rows. Poll GET …/shift/{key}. A new check of the same source replaces the old one."""
    return start(request, run_or_404(request, run_id), body)


@router.post("/runs/{run_id}/shift/{key}/cancel")
def cancel_shift(run_id: str, key: str, request: Request) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    d = sh.job_dir(Path(rec["config"]["run_dir"]), key)
    with _LOCK:
        item = _PROCS.get(str(d))
        if item is None or item[1].poll() is not None:
            st = sh.read_state(d)
            if st and alive(st.get("pid"), "app.engine.shift"):
                _CANCELLED.add(str(d))
                kill_group(st["pid"])
            else:
                raise HTTPException(400, "No shift check is running here.")
        else:
            _CANCELLED.add(str(d))
            kill_group(item[1].pid)
    return {"run_id": run_id, "key": key, "cancelled": True}


@router.post("/runs/{run_id}/shift/{key}/weights-version", status_code=201)
def weights_version(run_id: str, key: str, request: Request) -> dict[str, Any]:
    """Importance weighting: the run's dataset with a `shift_weight` column (w(x) for each reference row, 1 for every
    other row) as a dataset version with the same split (holdout untouched), plus the Gym patch that trains on it with
    predictor_kwargs.sample_weight = 'shift_weight'."""
    from ..gym.api import run_body
    from ..splits import save_splits
    from ..vela.findings import make_patch

    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    d = sh.job_dir(Path(cfg["run_dir"]), key)
    st = _state(rec, key, request.app.state.runs)
    if not st or st.get("status") != "succeeded":
        raise HTTPException(409, "This shift check has not finished.")
    req = json.loads((d / "request.json").read_text())
    if req["reference"]["csv"] != cfg["csv_path"]:
        raise HTTPException(400, "The reference rows are not rows of the run's dataset.")
    ds = request.app.state.datasets
    src = rec["dataset_id"]
    df = pd.read_csv(cfg["csv_path"])
    if sh.WEIGHT_COLUMN in df.columns:
        df = df.drop(columns=[sh.WEIGHT_COLUMN])
    wdf = pd.read_csv(d / "weights.csv")
    col = np.ones(len(df))
    col[wdf["row"].to_numpy(dtype=int)] = wdf["w"].to_numpy(dtype=float)
    df[sh.WEIGHT_COLUMN] = col
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    parent_lin = ds.lineage(src) or {}
    w = (st.get("result") or {}).get("weights") or {}
    extra = {"version": int(parent_lin.get("version") or 1) + 1,
             "shift_weights": {"run_id": run_id, "key": key, "column": sh.WEIGHT_COLUMN, "reference": st["sets"]["reference"]["name"],
                               "new": st["sets"]["new"]["name"], "ess": w.get("ess"), "clip_quantile": w.get("clip_quantile")},
             "labelled": 0, "relabelled": 0, "dropped": 0, "synthetic": 0, "relabelled_holdout": 0}
    name = (ds.db.get_dataset(src) or {}).get("name", src).replace(" (sample)", "")
    new_rec = ds.derive(src, df, None, "shift_weights",  # P1: every row kept, one column added
                        [{"op": "add_column", "column": sh.WEIGHT_COLUMN, "rows_weighted": int(len(wdf))}], splits=sp, extra=extra,
                        name=f"{name} · shift weights",
                        note=f"Importance weights from shift check {key} of {run_id}: {sh.WEIGHT_COLUMN} = p_N(x)/p_R(x) on "
                             f"{len(wdf):,} reference rows (ESS {w.get('ess', 0):,.0f}), 1 elsewhere")
    split = cfg.get("split") or {}
    if split.get("strategy") and split.get("strategy") != "random-stratified":
        p = ds.split_path(new_rec["id"], cfg["label"], split["strategy"], split.get("params"))
        p.parent.mkdir(parents=True, exist_ok=True)
        save_splits(p, {**sp, "derived_from": src})
    base = run_body(rec)
    pk = dict(base.get("predictor_kwargs") or {})
    prev = pk.get("sample_weight")
    patch = make_patch(base, f"shift-weights-{key}", "Importance Weighting", [
        ("dataset_id", new_rec["id"], "the dataset version with the shift_weight column"),
        ("predictor_kwargs.sample_weight", sh.WEIGHT_COLUMN, "wᵢ = p_N(xᵢ)/p_R(xᵢ): training rows that look like the new rows count more"),
    ])
    ver = {"id": new_rec["id"], "name": new_rec["name"], "rows": new_rec["rows"], "column": sh.WEIGHT_COLUMN,
           "rows_weighted": int(len(wdf)), "ess": w.get("ess"), "ess_share": w.get("ess_share"),
           "replaces_sample_weight": prev, "created_at": now()}
    st["versions"] = [*(st.get("versions") or []), ver]
    sh.write_state(d, st)
    return {"dataset_version": ver, "patch": patch}


@router.post("/registry/batches/{bid}/shift", status_code=202)
def start_batch_shift(bid: str, request: Request, body: ShiftRequest | None = None) -> dict[str, Any]:
    """F15: a shift check of a scored batch against the training rows of the run behind its version."""
    reg = request.app.state.registry
    b = reg.get_batch(bid)
    if not b:
        raise HTTPException(404, f"No batch with id '{bid}'.")
    if b.get("status") != "succeeded":
        raise HTTPException(409, f"Batch {bid} is {b.get('status')}.")
    v = reg.get_version(b["version_id"]) or {}
    rec = run_or_404(request, v.get("run_id") or "")
    body = (body or ShiftRequest()).model_copy(update={"source": "batch", "batch_id": bid})
    return start(request, rec, body)


@router.get("/registry/batches/{bid}/shift")
def get_batch_shift(bid: str, request: Request) -> dict[str, Any]:
    """The batch's shift check (status 'none' when none was started), for Batch & Drift next to PSI."""
    reg = request.app.state.registry
    b = reg.get_batch(bid)
    if not b:
        raise HTTPException(404, f"No batch with id '{bid}'.")
    v = reg.get_version(b["version_id"]) or {}
    rec = request.app.state.runs.get(v.get("run_id") or "")
    if not rec:
        return {"batch_id": bid, "status": "none"}
    st = _state(rec, f"batch-{bid}", request.app.state.runs)
    if not st:
        return {"batch_id": bid, "run_id": rec["id"], "status": "none", "key": f"batch-{bid}"}
    return {"batch_id": bid, **get_shift(rec["id"], f"batch-{bid}", request)}


def shutdown() -> None:
    if dev_reload():
        return
    with _LOCK:
        procs = [p for _, p in _PROCS.values()]
    for p in procs:
        if p.poll() is None:
            kill_group(p.pid)


def active() -> list[str]:
    with _LOCK:
        return [rid for rid, p in _PROCS.values() if p.poll() is None]

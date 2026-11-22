"""Routes for runs, their events (SSE with replay), predictions and the model catalogue."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from importlib import metadata
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from ..authz import body, path, require, with_team  # P19
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .. import config, frames, intake
from ..datasets import DatasetError
from ..splits import load_splits
from . import catalogue, costs, explain, heavy, serving, threshold
from .runner import JobRunner
from .store import TERMINAL_EVENTS, RunStore, now
from ..http import run_or_404, dataset_or_404

router = APIRouter(prefix="/api")

ProblemType = Literal["auto", "binary", "multiclass", "regression", "quantile"]

# Leaderboard columns kept in GET /api/runs (the list); GET /api/runs/{id} returns every column.
LIST_LB_COLS = ("model", "family", "rank", "score_val", "score_test", "score_val_natural", "score_test_natural",
                "fit_time", "pred_time_val", "pred_time_test", "stack_level", "can_infer", "val_score_std",
                "fold_scores", "num_models", "weights")


class RunCreate(BaseModel):
    dataset_id: str
    project_id: str | None = Field(None, description="P21: the project the run goes into (default: the dataset's). A version "
                                                     "shared by another team trains into one of the caller's own projects.")
    table: str | None = Field(None, description="P12: the table of a multi-table version to train on (default: its "
                                               "primary table).")
    join_id: str | None = Field(None, description="P14: train on a saved join (POST /datasets/{root}/joins) applied to "
                                                 "dataset_id: the run reads its join frame; the label is a grain column.")
    label: str | None = Field(None, description="Label column; defaults to the dataset's target.")
    name: str | None = None
    problem_type: ProblemType = "auto"
    eval_metric: str | None = Field(None, description="Any AutoGluon metric name; None lets AutoGluon choose.")
    presets: str | list[str] | None = "medium_quality"
    time_limit: float | None = Field(120, gt=0, description="Seconds; None means no limit.")
    fit_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TabularPredictor.fit unchanged.")
    predictor_kwargs: dict[str, Any] = Field(default_factory=dict, description="Passed to TabularPredictor(...).")
    custom_text_models: bool = Field(True, description="Add TFIDF_LR and TFIDF_SVM when the data has a text column.")
    drop_columns: list[str] | None = Field(None, description="Columns left out of training, on top of the ID columns.")
    keep_id_columns: bool = Field(False, description="A1 #10: the columns the profile types as row IDs (role ignored) "
                                                     "are left out of training unless this is true or column_types "
                                                     "gives them a type; the run records them as id_columns_dropped.")
    feature_importance: bool = Field(True, description="Compute permutation importance on the holdout after the fit.")
    column_types: dict[str, Literal["number", "category", "text"]] | None = Field(
        None, description="Override inferred column types; passed to AutoGluon as feature_metadata.")
    calibrate_threshold: str | None = Field(
        None, description="Binary only: after the fit, predictor.calibrate_decision_threshold(metric=...) and keep it. "
                          "A metric name (f1, balanced_accuracy, mcc, …) or 'auto' (f1 when the minority class < 20%). "
                          "Ignored when fit_kwargs.calibrate_decision_threshold is given.")
    pseudolabel: dict[str, Any] | None = Field(
        None, description="Pseudo-labelling with unlabeled rows: {dataset_id, method: fit_pseudolabel | unlabeled_data, "
                          "max_iter=3}. fit_pseudolabel runs predictor.fit_pseudolabel(pseudo_data=...) after the fit; "
                          "unlabeled_data passes the rows to fit(unlabeled_data=...).")
    distill: dict[str, Any] | None = Field(
        None, description="After the fit, predictor.distill(**distill) (time_limit defaults to half the budget).")
    parent_run_id: str | None = Field(None, description="The run this one was derived from (Gym, Vela patch).")
    patch: dict[str, Any] | None = Field(None, description="The Vela patch applied to the parent's config, if any.")
    source: str | None = Field(None, description="Where the run was started: wizard, gym, client, vela.")
    use_decision_time: bool = Field(True, description="D1: drop the columns the dataset's Data Doctor answers mark as not "
                                                       "known at decision time or fairness-only (recorded in the run).")
    split_spec: dict[str, Any] | None = Field(
        None, description="F6: how the holdout is drawn. {strategy: random-stratified (default) | group | time | "
                          "time-group, group_column, time_column, cutoff (YYYY-MM-DD; default: the latest 20 % of rows), "
                          "tuning_share (default 0.2)}. Group splits pass group-disjoint folds to AutoGluon as groups=; "
                          "time splits pass the latest slice of the training period as tuning_data.")
    cost: dict[str, Any] | None = Field(
        None, description="F7: a business cost to optimise instead of a standard metric. Binary: {c_fp, c_fn, g_tp=0} "
                          "(cost of a false positive, of a false negative, gain of a true positive; per decision). "
                          "Multiclass: {classes, matrix} with matrix[i][j] = cost of deciding class j when the truth is "
                          "class i. It becomes the run's eval_metric 'expected_cost' (an AutoGluon make_scorer from "
                          "ensemble_models.cost), so model selection, early stopping and ensemble weights minimise the "
                          "expected cost; binary runs also calibrate the decision threshold on it.")
    monotone: dict[str, Any] | None = Field(
        None, description="F8: monotone constraints. {constraints: {column: increasing | decreasing | none (or 1, -1, "
                          "0)}, compliant_only: true}. Numeric columns of binary and regression runs. LightGBM, XGBoost "
                          "and CatBoost are trained as GBM_MONO / XGB_MONO / CAT_MONO (ensemble_models.monotone), which "
                          "map each constraint onto the columns AutoGluon gives the model and constrain stack features "
                          "to +1, so stacked and weighted ensembles stay monotone. compliant_only (default) leaves the "
                          "other families out; false keeps them and reports which models break the constraint. After "
                          "the fit every model is checked with ICE / partial dependence on raw holdout rows.")


    scorecard: bool | dict[str, Any] | None = Field(
        None, description="F11: the scorecard challenger, a SCORECARD model (ensemble_models.scorecard: binning → weight "
                          "of evidence → logistic regression → points) trained with the other models as the "
                          "interpretable benchmark. On by default for binary runs (None or true); false leaves it out; "
                          "{pdo, base_score, base_odds, max_bins, min_bin_share, min_iv, monotone_bins, fine_bins, C} "
                          "sets its binning and points scaling. After the fit: the scorecard table (refitted on all "
                          "training rows), IV per feature, and ΔAUC of the served model over it with a bootstrap CI.")
    quantile_levels: list[Any] | None = Field(
        None, description="F9: predict a range. The quantile levels τ (0 < τ < 1) of TabularPredictor(problem_type="
                          "'quantile', quantile_levels=...); giving them makes problem_type 'quantile'. Default for a "
                          "quantile run: [0.1, 0.5, 0.9]. The eval_metric is AutoGluon's pinball_loss.")
    integrity_override_reason: str | None = Field(
        None, description="DL2: train on a version whose last integrity check was a MISMATCH; an admin only (with `by`), audited.")
    by: str | None = Field(None, description="DL2: who starts the run (the integrity override's admin).")
    frame_budget_mb: float | None = Field(
        None, gt=0, description="P11 (§5.9): the training frame's memory budget for this run, in MB (default: "
                                "ENSEMBLE_TRAIN_FRAME_SHARE = 10 % of the worker's RAM). Over it, the training rows are a "
                                "recorded stratified splitmix64 sample (result.data.frame, sample_sha256); the holdout is "
                                "still scored on every row.")
    sample_seed: int | None = Field(None, ge=0, description="P11: the sample's seed (default: the split's seed, 42).")
    conformal: bool | None = Field(
        None, description="F9: conformal recalibration of the quantiles (CQR), AutoGluon's fit(calibrate=...): true "
                          "adds a constant c_τ per level fitted on the validation (or out-of-fold) rows; false never; "
                          "None leaves it to AutoGluon ('auto': on with at least 1,000 validation rows). The holdout "
                          "is reported raw and calibrated either way.")


class EnsembleRequest(BaseModel):
    models: list[str] = Field(..., min_length=1, description="Base models the weighted ensemble may use.")


class PredictRequest(BaseModel):
    rows: list[dict[str, Any]]
    model: str | None = None


def display_name(ds: dict[str, Any]) -> str:
    return ds["name"].replace(" (sample)", "")


def alloc_run(app: Any) -> tuple[str, Path]:
    """A fresh run id and its (created) folder."""
    run_id = "run_" + secrets.token_hex(4)
    run_dir = app.state.settings.runs_dir / run_id
    run_dir.mkdir(parents=True)
    return run_id, run_dir


def queue_run(app: Any, run_id: str, run_dir: Path, name: str, dataset_id: str, label: str, cfg: dict[str, Any], /,
              **queued: Any) -> RunStore:
    """Write the run's config, record it, emit run_queued (with `queued` as the event's fields) and hand it to the runner.
    DL3 (M1): a run that reads its dataset's data.csv carries the version's stored sha256; the worker hashes the bytes it
    reads at fit start and refuses the fit on a mismatch (worker.data_gate).
    P05: a run on a Parquet version carries the table's committed `table_sha256` as the expected hash (`input_table`),
    and its part files are re-hashed here first: a changed byte refuses the run now (409 data_integrity, the version
    flagged), not minutes later in the worker. The worker hashes the bytes it parses again (worker.load_frames)."""
    ref = frames.from_json(cfg.get("input_ref")) if cfg.get("input_ref") else None
    if isinstance(ref, frames.Table) and frames.is_parquet(ref) and "data_sha256_expected" not in cfg:
        _parquet_input(app, cfg, ref)
    elif isinstance(ref, frames.JoinFrame) and "data_sha256_expected" not in cfg:
        _join_input(app, cfg, ref)  # P14
    try:
        if cfg.get("csv_path") and not frames.is_parquet(dataset_id) and \
                str(frames.path_of(frames.table(dataset_id))) == str(cfg["csv_path"]) and "data_sha256_expected" not in cfg:
            rec = app.state.db.get_dataset(dataset_id) or {}
            cfg["data_sha256_expected"] = rec.get("sha256")
            cfg.setdefault("dataset_id", dataset_id)
            if cfg.get("splits_path"):  # DL4 (M-d): the split file's hash when it was drawn; the worker checks it too
                st = app.state.datasets
                rel = st.split_rel(dataset_id, cfg["splits_path"])
                cfg["splits_file"] = rel
                cfg["splits_sha256_expected"] = st.split_hashes(dataset_id).get(rel)
            if not cfg.get("data_version_label"):
                from ..catalog import dsets as DS

                with app.state.db.connect() as con:
                    v = next((r["v"] for r in DS.tree_rows(con, DS.root_of(con, dataset_id)) if r["id"] == dataset_id), None)
                cfg["data_version_label"] = f"v{v} ({dataset_id})" if v else dataset_id
    except Exception:  # noqa: BLE001 - a dataset outside the catalog (none in practice): the run records its hash, unchecked
        pass
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    store = app.state.runs
    with _csv_hold(app, dataset_id, cfg, run_dir):
        store.create({"id": run_id, "name": name, "dataset_id": dataset_id, "label": label, "created_at": now(), "config": cfg})
    if cfg.get("join"):  # P14: join_input rows, the cache pin, the join_frame -trained_on-> run edge
        from ..catalog import joins as J

        J.record_run(app.state.db, run_id, cfg, cfg.get("by") or "system")
    store.emit(run_id, "run_queued", **queued)
    app.state.runner.submit(run_id)
    return store


def _csv_hold(app: Any, dataset_id: str, cfg: dict[str, Any], run_dir: Path) -> Any:
    """F-X2 C1: a run whose split was drawn from its version's CSV-era files (cfg.split without a split_id) is recorded
    only while that version is not being migrated, and is still CSV-era (409 otherwise: start it again)."""
    import contextlib

    sp = cfg.get("split")
    if not (isinstance(sp, dict) and cfg.get("splits_path") and not sp.get("split_id")):
        return contextlib.nullcontext()
    from ..lake.migrate import MigrateError, csv_write_hold

    @contextlib.contextmanager
    def hold():
        try:
            with csv_write_hold(app.state.db, dataset_id, "creating a run"):
                yield
        except MigrateError as e:
            import shutil

            shutil.rmtree(run_dir, ignore_errors=True)  # the run is not recorded: its folder goes too
            raise HTTPException(409, e.payload) from e
    return hold()


def _parquet_input(app: Any, cfg: dict[str, Any], ref: Any) -> None:
    """P05: the expected hashes of a run on a Parquet table, and the byte check before the run is queued."""
    from ..catalog import dsets as DS
    from ..catalog.schema import audit, now as _now

    lt = frames.lake_table(ref)
    cfg["dataset_id"] = cfg.get("dataset_id") or ref.version_id
    cfg["data_sha256_expected"] = lt.table_sha256
    cfg["input_table"] = {"version_id": ref.version_id, "table": lt.name, "table_sha256": lt.table_sha256, "n_rows": lt.n_rows,
                          "parts": len(lt.parts), "read_mode": "engine_v1"}
    if (cfg.get("split") or {}).get("split_id"):  # P09: the Parquet split, five checks now (and again at fit)
        _split_gate(app, cfg, ref)
    elif cfg.get("splits_path"):  # the (JSON) split drawn for this label: its hash at draw time, checked by the worker
        st = app.state.datasets
        rel = st.split_rel(ref.version_id, cfg["splits_path"])
        cfg["splits_file"] = rel
        cfg["splits_sha256_expected"] = st.split_hashes(ref.version_id).get(rel)
    if not cfg.get("data_version_label"):
        with app.state.db.connect() as con:
            v = next((r["v"] for r in DS.tree_rows(con, DS.root_of(con, ref.version_id)) if r["id"] == ref.version_id), None)
        cfg["data_version_label"] = f"v{v} ({ref.version_id})" if v else ref.version_id
    read, exp, bad = frames.hash_table(ref)
    if read == exp:
        return
    with app.state.db.connect() as con:
        root = (con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (ref.version_id,)).fetchone() or [ref.version_id])[0]
        con.execute("UPDATE datasets SET verified_at = ?, verified_ok = 0 WHERE id = ?", (_now(), ref.version_id))
        audit(con, "system:run-queue hash", "dataset_integrity_failed",
              details={"dataset_id": ref.version_id, "root_id": root, "table": lt.name, "stored": exp, "read": read,
                       "parts": [lt.parts[i]["sha256"] for i in bad], "storage": "parquet", "at": "run queued"})
        if not (cfg.get("data_integrity_override") or {}).get("by"):
            audit(con, cfg.get("by") or "unknown", "training_refused", comment=f"table {lt.name} of {ref.version_id} differs",
                  details={"dataset_id": ref.version_id, "root_id": root, "stored": exp, "read": read, "status": 409})
    if (cfg.get("data_integrity_override") or {}).get("by"):
        return  # an admin armed an override: the worker records it and trains (audited there)
    raise HTTPException(409, {"code": "data_integrity", "message":
                              f"Table {lt.name} of {cfg['data_version_label']} no longer has the bytes it was committed with "
                              f"(table_sha256 {exp[:12]}… committed, {read[:12]}… now; {len(bad)} of {len(lt.parts)} file(s) "
                              "differ). The run was refused. GET /api/datasets/" + ref.version_id + "/verify names the file; an "
                              "admin may train anyway with integrity_override_reason (audited).",
                              "dataset_id": ref.version_id, "table": lt.name, "stored": exp, "read": read})


def _split_gate(app: Any, cfg: dict[str, Any], ref: Any) -> None:
    """P09 (§4.3): before a run on a Parquet version is queued, its split passes the five checks (blob hash, split_sha256,
    the split_created audit row, every earlier run's recorded hash, the carry). A failure refuses the run (409
    split_integrity, audited, the version flagged) unless an admin armed an override; the worker checks again at fit.
    The run pins the exact split blob (blob_refs owner 'run', role 'split')."""
    from ..catalog.schema import audit, now as _now
    from ..lake import splitstore as SS

    sid = cfg["split"]["split_id"]
    row = SS.get_by_id(app.state.db, sid)
    if row is None:
        raise HTTPException(409, {"code": "split_integrity", "message": f"split_integrity: split {sid} is no longer in the "
                                  "splits table. GET /api/datasets/" + ref.version_id + "/verify says more."})
    chk = SS.integrity(app.state.db, app.state.lake, row)
    cfg["split_pin"] = {**SS.pin_of(row), "checked_at_queue": {"ok": chk["ok"], "failed": chk["failed"]}}
    ov = (cfg.get("data_integrity_override") or {}).get("by")
    if not chk["ok"]:
        with app.state.db.connect() as con:
            root = (con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (ref.version_id,)).fetchone() or [ref.version_id])[0]
            con.execute("UPDATE datasets SET verified_at = ?, verified_ok = 0 WHERE id = ?", (_now(), ref.version_id))
            audit(con, "system:run-queue split check", "dataset_integrity_failed",
                  details={"dataset_id": ref.version_id, "root_id": root, "what": "split", "split_id": sid,
                           "split_key": row["split_key"], "failed": chk["failed"], "at": "run queued",
                           "checks": [{k: c[k] for k in ("n", "check", "ok", "detail")} for c in chk["checks"]]})
            if not ov:
                audit(con, cfg.get("by") or "unknown", "training_refused", comment=chk["message"][:500],
                      details={"dataset_id": ref.version_id, "root_id": root, "split_id": sid, "status": 409,
                               "failed": chk["failed"]})
        if not ov:
            raise HTTPException(409, {"code": "split_integrity", "message": chk["message"] +
                                      ". The run was refused. GET /api/datasets/" + ref.version_id + "/verify names the check; "
                                      "POST …/restore-splits offers the split the earliest run used; an admin may train anyway "
                                      "with integrity_override_reason (audited).", "dataset_id": ref.version_id,
                                      "split_id": sid, "failed": chk["failed"], "checks": chk["checks"]})
    if cfg.get("run_id"):
        SS.pin_run(app.state.db, cfg["run_id"], row)


def _runner(request: Request) -> JobRunner:
    return request.app.state.runner


def _store(request: Request) -> RunStore:
    return request.app.state.runs


def split_record(datasets: Any, dataset_id: str, label: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """The (label, strategy[, parameters]) split of the dataset, drawn on first use, as a run records it."""
    from ..splits import RANDOM_STRATIFIED, split_sha256

    spec = dict(spec or {})
    strategy = spec.pop("strategy", None) or RANDOM_STRATIFIED
    path, sp, created = datasets.ensure_splits(dataset_id, label, strategy, spec)
    rec = {"path": str(path), "key": sp["key"], "target": sp["target"], "strategy": sp["strategy"],
           "seed": sp["seed"], "stratified": sp["stratified"], "holdout_share": sp["holdout_share"],
           "n_folds": sp["n_folds"], "holdout_rows": len(sp["holdout"]), "sha256": split_sha256(sp),
           "drawn_for_this_run": created}
    vo = sp.get("view_of") or {}
    if vo.get("split_id"):  # P09: the Parquet split this run pins (its view file is what `path` names)
        rec.update(split_id=vo["split_id"], split_sha256=vo["split_sha256"], blob_sha256=vo["blob_sha256"],
                   table=vo.get("table"), storage="parquet")
    if sp.get("params"):
        rec.update(params=sp["params"], tuning_rows=len(sp.get("tuning") or []),
                   excluded_rows=len(sp.get("excluded") or []), cutoff=sp.get("cutoff"),
                   tuning_cutoff=sp.get("tuning_cutoff"))
    return rec


def run_out(rec: dict[str, Any], store: RunStore, full: bool = True) -> dict[str, Any]:
    res = rec.get("result") or {}
    out = {
        "id": rec["id"], "name": rec["name"], "dataset_id": rec["dataset_id"], "label": rec["label"],
        "status": rec["status"], "created_at": rec["created_at"], "started_at": rec["started_at"],
        "finished_at": rec["finished_at"], "error": rec["error"],
        "config": {k: v for k, v in rec["config"].items() if k not in ("truth", "profile")},
        "kind": rec["config"].get("kind") or ("import" if rec["config"].get("import") else "tabular"),
        "predictor_path": rec["config"].get("predictor_path"), "n_events": store.count_events(rec["id"]),
        "split": rec["config"].get("split"),
        "predictor_state": rec.get("predictor_state"),  # F-P2: present | slimmed | evicted | none
    }
    for key in ("problem_type", "eval_metric", "classes", "positive_class", "best_model", "fit_seconds",
                "elapsed_seconds", "data", "features", "ensemble", "ensembles", "holdout", "oof", "text_models",
                "feature_importance", "leaderboard", "resolved", "custom_ensembles", "threshold", "mlflow",
                "forecast", "backtests", "anomaly", "imported", "monotone", "quantile", "quantile_levels",
                "learning_curve", "active_learning", "annotations", "shift", "slices", "doctor", "expected_loss", "segmented", "survival", "reject_inference", "multilabel",
                "data_sha256", "data_integrity", "splits_sha256", "splits_integrity"):  # DL3 (M1): the sha256 of the bytes the fit read, and the check against its version
        out[key] = res.get(key)
    out["feature_importance"] = explain.importance_with_ci(out["feature_importance"])
    sc = res.get("scorecard")  # F11: the headline only; the table is GET /runs/{id}/scorecard
    if sc:
        h = sc.get("holdout") or {}
        out["scorecard"] = {"available": bool(sc.get("available")), "error": sc.get("error"), "reason": sc.get("reason"),
                            "model": sc.get("model"), "auc": (h.get("scorecard") or {}).get("auc"),
                            "served_auc": (h.get("served") or {}).get("auc"), "delta_auc": h.get("delta_auc"),
                            "n_features": (sc.get("table") or {}).get("n_features")}
    else:
        out["scorecard"] = None
    if not full:
        out["leaderboard"] = [{k: r.get(k) for k in LIST_LB_COLS} for r in (out["leaderboard"] or [])] or out["leaderboard"]
        out["feature_importance"] = None
        out["resolved"] = None
        out["text_models"] = None
        if out["monotone"]:
            out["monotone"] = {k: out["monotone"].get(k) for k in ("constraints", "compliant_only", "best_monotone")}
        if out["quantile"]:  # F9: the headline numbers only (no fan chart, no per-model table)
            q = out["quantile"]
            out["quantile"] = {"levels": q.get("levels"), "served": q.get("served"), "main_interval": q.get("main_interval"),
                               "pinball": (q.get("holdout") or {}).get("pinball"),
                               "coverage": ((q.get("holdout") or {}).get("intervals") or [{}])[0].get("coverage")}
    return out


@router.get("/models")
def models() -> dict[str, Any]:
    return {"families": catalogue.catalogue(), "engine": engine_info()}


def engine_info() -> dict[str, Any]:
    def v(p: str) -> str | None:
        try:
            return metadata.version(p)
        except metadata.PackageNotFoundError:
            return None

    return {"name": "AutoGluon", "version": v("autogluon.tabular"), "timeseries": v("autogluon.timeseries")}


@router.post("/runs", status_code=201, dependencies=[require("run.train", body("project_id", "project"), body("dataset_id", "dataset"),
                                                         also=[("data.preview", body("dataset_id", "dataset"))])])
async def create_run(body: RunCreate, request: Request) -> dict[str, Any]:
    from ..catalog import shares
    from ..gym.api import check_run_body

    try:  # P21, the project rule: a shared version trains into the caller's own project (decided like a require())
        body.project_id = await run_in_threadpool(shares.train_project, request, body.dataset_id, body.project_id, body.by)
    except shares.ShareError as e:
        raise e.http() from e
    await check_run_body(request, body.model_dump())
    return await run_in_threadpool(_create_run, body, request)


def _create_run(body: RunCreate, request: Request) -> dict[str, Any]:
    if body.join_id:
        return _create_join_run(body, request)
    if not body.table:
        return _create_run_on(body, request)
    # P12: a run on one table of a multi-table version: the profile, split, input ref and checks below name that table
    ds = dataset_or_404(request, body.dataset_id)
    with request.app.state.db.connect() as con:
        names = [r[0] for r in con.execute("SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY ordinal",
                                           (body.dataset_id,))]
    if body.table not in names:
        raise HTTPException(400, {"code": "table_unknown", "message": f"{body.dataset_id} has no table {body.table!r}"
                                  + (f" (its tables: {', '.join(names)})." if names else " (it is a CSV-era version with one table).")})
    if body.table == (ds.get("primary_table") or names[0]):
        with frames.use_table(body.table):
            return _create_run_on(body, request)
    if not body.label:
        raise HTTPException(400, f"Say which column of table {body.table!r} to predict (label): the dataset's target "
                                 f"belongs to its primary table.")
    try:
        prof = request.app.state.datasets.table_profile(body.dataset_id, body.table, body.label)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    with frames.use_table(body.table):
        return _create_run_on(body, request, ds={**ds, "profile": prof, "target": prof.get("target")})


def _create_join_run(body: RunCreate, request: Request) -> dict[str, Any]:
    """P14 (§5.8): a run on a saved join applied to `dataset_id`. The frame is materialised (or found cached), profiled,
    and read by the run (`input_ref {kind: join_frame}`); the split is the grain table's (the frame has exactly the
    grain's rows and __row_ids), so the label is a grain column."""
    from ..catalog import governance as G
    from ..catalog import joins as J

    app = request.app
    ds = dataset_or_404(request, body.dataset_id)
    who = (body.by or "").strip()[:80] or G.current_actor() or "unknown"
    try:
        j = J.get(app.state.db, body.join_id)
        J.version_of_root(app.state.db, j["root_id"], body.dataset_id)
        fr = J.frame(app.state.db, app.state.lake, body.join_id, body.dataset_id, who)
    except J.JoinError as e:
        raise HTTPException(e.status, e.payload) from e
    grain = fr["grain_table"]
    if body.table and body.table != grain:
        raise HTTPException(400, {"code": "table_not_grain", "message": f"A run on join {j['name']!r} trains on its grain "
                                  f"table {grain!r}; leave table out (or send {grain!r})."})
    with app.state.db.connect() as con:
        r = con.execute("SELECT schema_json FROM version_tables WHERE version_id = ? AND table_name = ?",
                        (body.dataset_id, grain)).fetchone()
    gcols = [c["name"] for c in json.loads(r[0])] if r else []
    label = body.label or (ds.get("target") if grain == ds.get("primary_table") else None)
    if not label:
        raise HTTPException(400, f"Say which column of the grain table {grain!r} to predict (label).")
    if label not in gcols:
        raise HTTPException(400, {"code": "label_not_in_grain", "message": f"The label {label!r} is not a column of the grain "
                                  f"table {grain!r}: the split (holdout) is drawn on the grain, so the label lives there."})
    try:
        prof = J.profile(app, fr, label)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    b2 = body.model_copy(update={"table": grain, "label": label,
                                 "name": body.name or f"{display_name(ds)} ⋈ {j['name']} · {label}"})
    with frames.use_table(grain), frames.use_join(fr["id"]):
        return _create_run_on(b2, request, ds={**ds, "profile": prof, "target": prof.get("target")})


def _join_input(app: Any, cfg: dict[str, Any], ref: Any) -> None:
    """P14: the expected hashes of a run on a join frame: its cache file's sha256 (what the worker's data gate compares
    with the bytes it reads, recorded as read_sha256), every pinned input re-hashed now, and the grain's split gate."""
    from ..catalog import dsets as DS
    from ..catalog import joins as J
    from ..catalog.schema import audit

    fr = J.get_frame(app.state.db, ref.join_frame_id)
    j = J.get(app.state.db, fr["join_id"])
    lt = frames.lake_table(ref)
    cfg["dataset_id"] = cfg.get("dataset_id") or fr["version_id"]
    grain = frames.Table(cfg["dataset_id"], fr["grain_table"])
    ins = J.check_inputs(app.state.db, fr)
    cfg["data_sha256_expected"] = lt.table_sha256
    cfg["input_table"] = {"version_id": cfg["dataset_id"], "table": fr["grain_table"], "table_sha256": lt.table_sha256,
                          "n_rows": lt.n_rows, "parts": 1, "read_mode": "engine_v1", "join_frame_id": fr["id"]}
    cfg["join"] = {"join_id": j["id"], "name": j["name"], "spec_version": j["spec_version"], "spec_sha256": j["spec_sha256"],
                   "join_frame_id": fr["id"], "frame_sha256": fr["frame_sha256"], "cache_blob": fr["cache_blob"],
                   "grain_table": fr["grain_table"], "n_rows": fr["n_rows"],
                   "inputs": [{**{k: x[k] for k in ("version_id", "table", "table_sha256", "n_rows")},
                               "now": next((c["now"] for c in ins if c["version_id"] == x["version_id"] and c["table"] == x["table"]), None)}
                              for x in fr["inputs"]]}
    if (cfg.get("split") or {}).get("split_id"):
        _split_gate(app, cfg, grain)
    elif cfg.get("splits_path"):
        st = app.state.datasets
        rel = st.split_rel(grain.version_id, cfg["splits_path"])
        cfg["splits_file"] = rel
        cfg["splits_sha256_expected"] = st.split_hashes(grain.version_id).get(rel)
    if not cfg.get("data_version_label"):
        with app.state.db.connect() as con:
            v = next((r["v"] for r in DS.tree_rows(con, DS.root_of(con, grain.version_id)) if r["id"] == grain.version_id), None)
        cfg["data_version_label"] = (f"v{v} ({grain.version_id})" if v else grain.version_id) + f" ⋈ {j['name']}"
    read, exp, _bad = frames.hash_table(ref)
    bad = [x for x in ins if not x["ok"]]
    if read == exp and not bad:
        return
    what = ([f"the frame's cache file ({exp[:12]}… recorded, {str(read)[:12]}… now)"] if read != exp else []) + \
        [f"table {x['table']} of {x['version_id']} ({x['pinned'][:12]}… pinned, {str(x['now'])[:12]}… now)" for x in bad]
    with app.state.db.connect() as con:
        audit(con, "system:run-queue hash", "dataset_integrity_failed",
              details={"dataset_id": grain.version_id, "join_frame_id": fr["id"], "what": what, "at": "run queued"})
        if not (cfg.get("data_integrity_override") or {}).get("by"):
            audit(con, cfg.get("by") or "unknown", "training_refused", comment="join frame inputs differ",
                  details={"dataset_id": grain.version_id, "join_frame_id": fr["id"], "what": what, "status": 409})
    if (cfg.get("data_integrity_override") or {}).get("by"):
        return
    raise HTTPException(409, {"code": "data_integrity", "message": "The join frame no longer reads what it was made from: "
                              + "; ".join(what) + ". The run was refused.", "join_frame_id": fr["id"]})


def _create_run_on(body: RunCreate, request: Request, ds: dict[str, Any] | None = None) -> dict[str, Any]:
    app = request.app
    ds = ds or dataset_or_404(request, body.dataset_id)
    from ..catalog import dsets as DS
    from ..catalog import governance as G

    who = (body.by or "").strip()[:80] or G.current_actor()
    try:
        data_gate = DS.integrity_gate(app, body.dataset_id, body.integrity_override_reason, who)
    except DS.DsError as e:
        if e.status in (403, 409):
            G.refused(app.state.db, who, "training", e.status, e.payload, {"dataset_id": body.dataset_id})
        raise HTTPException(e.status, e.payload) from e
    profile = ds["profile"]
    cols = {c["name"]: c for c in profile["columns"]}
    label = body.label or ds["target"]
    if not label:
        raise HTTPException(400, "This dataset has no label column; say which column to predict (label).")
    if label not in cols:
        raise HTTPException(400, f"Column '{label}' is not in the dataset.")
    if cols[label]["unique"] < 2:
        raise HTTPException(400, f"Column '{label}' is empty: every row is missing, so there is nothing to learn."
                            if not cols[label]["unique"] else
                            f"Column '{label}' has one value in every labelled row: a model needs at least two "
                            "outcomes to learn from.")
    for k in ("label", "path"):
        if k in body.predictor_kwargs:
            raise HTTPException(400, f"predictor_kwargs.{k} is set by the run; use the run's '{k}' field instead."
                                if k == "label" else "predictor_kwargs.path is set by the run (its own folder).")
    for k in ("train_data",):
        if k in body.fit_kwargs:
            raise HTTPException(400, "fit_kwargs.train_data is set by the run (the dataset minus its holdout).")
    drop = list(body.drop_columns or [])
    unknown = [c for c in drop if c not in cols]
    if unknown:
        raise HTTPException(400, f"drop_columns: no column named {', '.join(map(repr, unknown))}.")
    if label in drop:
        raise HTTPException(400, "The label column can't be dropped.")
    types = body.column_types or {}
    # A1 #10: the profile's row-ID columns never train by default, whatever drop_columns says (as in the UI);
    # a column_types entry for one, or keep_id_columns, keeps it.
    id_dropped = [] if body.keep_id_columns else \
        [c["name"] for c in profile["columns"] if c["type"] == "id" and c["name"] != label
         and c["name"] not in types and c["name"] not in drop]
    if id_dropped:  # A2 M7: an "ID" tied to the target is a value (often recorded with the outcome), not a key: keep it,
        # the Data Doctor raises it as a leakage suspect instead of hiding it
        try:
            from ..data_doctor import id_tie

            fr = frames.read(frames.table(ds["id"]), columns=[*id_dropped, label])  # P05: only the columns compared
            tied = [c for c in id_dropped if c in fr.columns and label in fr.columns and id_tie(fr[c], fr[label])]
        except Exception:  # noqa: BLE001
            tied = []
        id_dropped = [c for c in id_dropped if c not in tied]
    drop = [*drop, *id_dropped]
    bad = [c for c in types if c not in cols]
    if bad:
        raise HTTPException(400, f"column_types: no column named {', '.join(map(repr, bad))}.")
    if label in types:
        raise HTTPException(400, "column_types: the label's type is set by problem_type, not column_types.")
    if types and "feature_metadata" in body.fit_kwargs:
        raise HTTPException(400, "Give column_types or fit_kwargs.feature_metadata, not both.")
    cost = None
    if body.cost is not None:
        cost = check_cost(body, app, ds, label, cols)
    elif body.eval_metric == costs.METRIC_NAME:
        raise HTTPException(400, "eval_metric 'expected_cost' needs the costs: give cost = {c_fp, c_fn, g_tp} "
                                 "(binary) or {classes, matrix} (multiclass).")
    quant = check_quantile(body, label, cols)
    mono = check_monotone(body, ds, label, cols, drop, types)
    card = check_scorecard(body, ds, label)
    allowed_cal = (None, "auto", *threshold.CALIBRATION_METRICS, *((costs.METRIC_NAME,) if cost and cost["kind"] == "binary" else ()))
    if body.calibrate_threshold not in allowed_cal:
        raise HTTPException(400, f"calibrate_threshold: use 'auto' or one of {', '.join(a for a in allowed_cal if a and a != 'auto')}.")
    text_cols = [c["name"] for c in profile["columns"] if types.get(c["name"], c["type"]) == "text"
                 and c["name"] not in drop and c["name"] != label]
    if label == profile.get("target"):
        task = profile.get("task")
    else:
        task = "regression" if cols[label]["type"] == "number" and cols[label]["unique"] > 50 else None

    # F4: the holdout belongs to the label, not to the dataset's (possibly guessed) target.
    # F6: and to the strategy (random, group, time, time + group) and its parameters.
    spec = body.split_spec or {}
    if spec.get("strategy") not in (None, "random-stratified"):
        for k in ("tuning_data", "use_bag_holdout"):
            if k in body.fit_kwargs:
                raise HTTPException(400, f"fit_kwargs.{k} is set by the {spec['strategy']} split (its tuning slice).")
        if "groups" in body.predictor_kwargs:
            raise HTTPException(400, f"predictor_kwargs.groups is set by the {spec['strategy']} split (its folds).")
        if "validation_structure" in body.fit_kwargs:
            raise HTTPException(400, "Give split_spec or fit_kwargs.validation_structure, not both.")
    try:
        split = split_record(app.state.datasets, body.dataset_id, label, spec)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:  # P09: DatasetConflict (the split's file is missing or unreadable): a 409, not a 500
        if type(e).__name__ != "DatasetConflict":
            raise
        code = getattr(e, "code", None) or "split_integrity"  # F-X2: version_migrating while its migration holds it
        raise HTTPException(409, {"code": code, "message": f"{code}: {e}"}) from e
    _min_rows_check(app, body.dataset_id, label, split, task, body.problem_type)  # DL3 (m5)
    if body.drop_columns is None and (split.get("params") or {}).get("group_column"):
        # The entity id is what the split keeps apart; as a feature it can only memorise customers.
        g = split["params"]["group_column"]
        if g != label and g not in drop:
            drop = [*drop, g]

    # D1: the Data Doctor's decision-time answers for this dataset drop their columns by default; the run records it.
    from ..data_doctor_api import run_drops
    dt = run_drops(app.state.datasets, body.dataset_id, list(cols))
    if dt:
        added = [c for c in dt["columns"] if c != label and c not in drop] if body.use_decision_time else []
        drop = [*drop, *added]
        dt = {**dt, "applied": body.use_decision_time, "dropped": added}
        text_cols = [c for c in text_cols if c not in drop]

    settings = app.state.settings
    run_id, run_dir = alloc_run(app)
    cfg = {
        **body.model_dump(exclude={"label", "name", "drop_columns", "column_types", "cost", "monotone", "scorecard",
                                   "integrity_override_reason"}),
        "decision_time": dt, "data_gate": data_gate, "by": who,
        **({"data_integrity_override": data_gate["override_armed"]} if data_gate.get("override_armed") else {}),
        "column_types": {c: t for c, t in types.items() if c not in drop},
        "problem_type": None if body.problem_type == "auto" else body.problem_type,
        "label": label, "drop_columns": drop, "id_columns_dropped": id_dropped, "text_columns": text_cols, "task": task,
        "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(frames.path_of(frames.table(body.dataset_id))),
        "input_ref": frames.input_ref_for(body.dataset_id),  # P03: what frames.resolve reads (csv_path kept for old readers)
        "splits_path": split["path"], "split": split, "run_dir": str(run_dir),
        "predictor_path": str(run_dir / "predictor"),
    }
    if cost is not None:
        cfg["cost"] = cost
        cfg["eval_metric"] = costs.METRIC_NAME
        if cost["kind"] == "binary" and body.calibrate_threshold in (None, "auto") \
                and "calibrate_decision_threshold" not in body.fit_kwargs:
            cfg["calibrate_threshold"] = costs.METRIC_NAME
    if mono is not None:
        cfg["monotone"] = mono
    if card is not None:
        cfg["scorecard"] = card
    if quant is not None:
        cfg["quantile"] = quant
        cfg["problem_type"] = "quantile"
        cfg["quantile_levels"] = quant["levels"]
        cfg["predictor_kwargs"] = {k: v for k, v in (cfg.get("predictor_kwargs") or {}).items() if k != "quantile_levels"}
    if body.pseudolabel:
        cfg["unlabeled_csv_path"] = str(frames.path_of(frames.table(body.pseudolabel["dataset_id"])))
        cfg["unlabeled_ref"] = frames.input_ref_for(body.pseudolabel["dataset_id"])
    name = body.name or f"{display_name(ds)} · {label}"
    store = queue_run(app, run_id, run_dir, name, body.dataset_id, label, cfg, dataset_id=body.dataset_id, label=label,
                      problem_type=cfg["problem_type"], eval_metric=body.eval_metric, presets=body.presets,
                      time_limit=body.time_limit)
    return {**run_out(store.get(run_id), store), **({"notes": data_gate["notes"]} if data_gate.get("notes") else {})}


#: DL3 (m5): the least a fit needs, checked before a run is queued (a fit on less fails minutes later, unexplained)
MIN_TRAIN_ROWS = 20
MIN_CLASS_TRAIN_ROWS = 2


def _min_rows_check(app: Any, dataset_id: str, label: str, split: dict[str, Any], task: str | None, problem_type: str | None) -> None:
    """Refuse (400, in plain words) a run whose training rows are too few, or (classification) whose classes are too thin."""
    import numpy as np

    from ..splits import load_splits

    y = frames.read(frames.table(dataset_id), columns=[label])[label]
    sp = load_splits(Path(split["path"]))
    fold = np.asarray(sp["fold"], dtype=int)
    hold = np.zeros(len(y), dtype=bool)
    hold[[int(i) for i in sp.get("holdout") or [] if int(i) < len(y)]] = True
    train = y[(~hold) & (fold > -2) & y.notna().to_numpy()]
    if len(train) < MIN_TRAIN_ROWS:
        raise HTTPException(400, f"This version has {int(y.notna().sum()):,} labelled rows, and {len(train):,} of them are left for "
                                 f"training after the holdout: at least {MIN_TRAIN_ROWS} are needed. Add rows (upload a new version) "
                                 "or pick a larger version.")
    pt = problem_type if problem_type not in (None, "auto") else task
    if pt in ("binary", "multiclass") or (pt is None and train.nunique() <= 20):
        vc = train.astype(str).value_counts()
        thin = vc[vc < MIN_CLASS_TRAIN_ROWS]
        if len(vc) < 2:
            raise HTTPException(400, f"Only one class ('{vc.index[0]}') is left in the training rows: a model needs at least two.")
        if len(vc) - len(thin) < 2:
            raise HTTPException(400, "Too few rows per class to learn from: " + ", ".join(f"class '{k}' has only {int(n)} training row(s)"
                                for k, n in thin.items()) + f"; at least {MIN_CLASS_TRAIN_ROWS} per class are needed in at least two classes.")


QUANTILE_METRICS = (None, "pinball_loss", "pinball")


def check_quantile(body: RunCreate, label: str, cols: dict[str, Any]) -> dict[str, Any] | None:
    """F9: the run's quantile request, checked and normalised ({levels, calibrate}), or None for other runs. Giving
    quantile_levels (here or in predictor_kwargs) makes an 'auto' problem type 'quantile'. Sets body.problem_type."""
    from . import quantile as qt

    pk = body.predictor_kwargs.get("quantile_levels")
    if pk is not None and body.quantile_levels is not None and list(pk) != list(body.quantile_levels):
        raise HTTPException(400, "Give quantile_levels or predictor_kwargs.quantile_levels, not both.")
    given = body.quantile_levels if body.quantile_levels is not None else pk
    if given is not None and body.problem_type == "auto":
        body.problem_type = "quantile"
    if body.problem_type != "quantile":
        if given is not None:
            raise HTTPException(400, f"quantile_levels apply to problem_type 'quantile', not '{body.problem_type}'.")
        if body.conformal is not None:
            raise HTTPException(400, "conformal recalibrates quantiles; it applies to problem_type 'quantile' only.")
        return None
    if cols[label]["type"] != "number":
        raise HTTPException(400, f"Quantile regression predicts a range of a numeric label; '{label}' is "
                                 f"{cols[label]['type']}. Pick a numeric column or another problem type.")
    try:
        levels = qt.normalise_levels(given)
    except ValueError as e:
        raise HTTPException(400, f"quantile_levels: {e}") from e
    em = body.eval_metric or body.predictor_kwargs.get("eval_metric")
    if em not in QUANTILE_METRICS:
        raise HTTPException(400, f"A quantile run is scored by the pinball loss (eval_metric 'pinball_loss'); "
                                 f"'{em}' is not a quantile metric in AutoGluon.")
    if body.cost is not None:
        raise HTTPException(400, "cost: a business cost applies to binary or multiclass labels, not quantile regression.")
    if body.calibrate_threshold not in (None, "auto"):  # 'auto' is the wizard's default and means nothing here
        raise HTTPException(400, "calibrate_threshold: a decision threshold applies to binary labels, not quantile regression.")
    if body.distill:
        raise HTTPException(400, "distill: distillation is not offered for quantile runs (its students learn one "
                                 "prediction per row, not one per level).")
    if body.pseudolabel:
        raise HTTPException(400, "pseudolabel: pseudo-labelling is not offered for quantile runs (a range is not a label).")
    if body.conformal is not None and "calibrate" in body.fit_kwargs:
        raise HTTPException(400, "Give conformal or fit_kwargs.calibrate, not both (conformal is AutoGluon's calibrate).")
    return {"levels": levels, "calibrate": body.conformal, "centre": qt.centre(levels),
            "main_interval": qt.main_interval(levels)}


def check_monotone(body: RunCreate, ds: dict[str, Any], label: str, cols: dict[str, Any], drop: list[str],
                   types: dict[str, str]) -> dict[str, Any] | None:
    """F8: the run's monotone constraints, checked and normalised ({constraints: {column: ±1}, compliant_only, stack}),
    or None when no column is constrained."""
    from . import monotone as mono
    from ..gym.validate import infer_problem_type

    if not body.monotone:
        return None
    try:
        m = mono.normalise(body.monotone, problem_type=infer_problem_type(body.model_dump(), ds, label), columns=cols,
                           label=label, drop=drop, column_types=types)
    except ValueError as e:
        raise HTTPException(400, f"monotone: {e}") from e
    if m and body.distill:
        raise HTTPException(400, "monotone: distill trains unconstrained student models; leave distill off.")
    return m


def check_scorecard(body: RunCreate, ds: dict[str, Any], label: str) -> dict[str, Any] | bool | None:
    """F11: the scorecard's hyperparameters (on: binary runs by default), False (switched off) or None (not binary)."""
    from . import scorecard as sc
    from ..gym.validate import infer_problem_type

    pt = infer_problem_type(body.model_dump(), ds, label)
    try:
        return sc.normalise(body.scorecard, pt)
    except ValueError as e:
        raise HTTPException(400, f"scorecard: {e}") from e


def check_cost(body: RunCreate, app: Any, ds: dict[str, Any], label: str, cols: dict[str, Any]) -> dict[str, Any]:
    """F7: the run's cost request, checked and normalised: {kind: binary, c_fp, c_fn, g_tp, positive_class} or
    {kind: multiclass, classes, matrix} (classes in AutoGluon's order, the sorted labels)."""

    if body.eval_metric not in (None, costs.METRIC_NAME):
        raise HTTPException(400, f"Give cost or eval_metric '{body.eval_metric}', not both: the cost is the metric.")
    if "eval_metric" in body.predictor_kwargs:
        raise HTTPException(400, "predictor_kwargs.eval_metric is set by the cost.")
    y = frames.read(frames.table(body.dataset_id), columns=[label])[label].dropna()
    classes = list(y.astype("category").cat.categories)
    pt = body.problem_type
    if pt == "auto":
        pt = "binary" if len(classes) == 2 else \
            "regression" if cols[label]["type"] == "number" and cols[label]["unique"] > 50 else "multiclass"
    cost = dict(body.cost or {})
    if pt == "binary" and len(classes) == 2:
        # The positive class of a cost is the one whose miss costs c_FN: the given one, else AutoGluon's, else the rarer
        # class (bad of good/bad, as Smart Intake describes it), not the alphabetically last one AutoGluon would pick.
        if cost.get("positive_class") is None:
            vc = y.astype(str).value_counts()
            cost["positive_class"] = body.predictor_kwargs.get("positive_class") or (
                vc.idxmin() if vc.iloc[0] != vc.iloc[-1] else str(classes[1]))
        body.predictor_kwargs.setdefault("positive_class", next((c for c in classes if str(c) == str(cost["positive_class"])),
                                                                 cost["positive_class"]))
    try:
        return costs.normalise(cost, pt, classes)
    except ValueError as e:
        raise HTTPException(400, f"cost: {e}") from e


@router.get("/runs")
def list_runs(request: Request, dataset_id: str | None = None, full: bool = False,
              kind: str = Query("tabular", description="tabular (includes imports) | forecast | anomaly | all"),
              project_id: str | None = None, light: bool = False, limit: int | None = Query(None, ge=1, le=5000),
              offset: int = Query(0, ge=0), status: str | None = Query(None, description="comma-separated statuses"),
              ids: str | None = Query(None, description="comma-separated run ids (at most 200)")) -> dict[str, Any]:
    """Runs, newest first. Forecast and anomaly runs are listed only with kind=forecast / anomaly / all, so pages
    built for tabular runs never see them. P1: the filters run in SQL on the indexed columns; light=true returns those
    columns only (no config or result JSON), the list path for thousands of runs. F-P2: `status` and `ids` filter too,
    and every response says `total` (the rows matching, before limit / offset)."""
    store = _store(request)
    kinds = None if kind == "all" else (["tabular", "import"] if kind == "tabular" else [kind])
    flt: dict[str, Any] = {"dataset_id": dataset_id, "project_id": project_id, "kind": kinds}
    if status:
        flt["status"] = [x for x in status.split(",") if x]
    if ids:
        idl = [x for x in ids.split(",") if x][:200]
        flt["id"] = idl or ["-"]
    total = store.count_where(**flt)
    if light:
        return with_team(request, {"runs": store.list_light(limit=limit, offset=offset, **flt), "total": total}, "runs")
    recs = store.list(limit=limit, offset=offset, **flt)
    return with_team(request, {"runs": [run_out(r, store, full=full) for r in recs], "total": total}, "runs")


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return run_out(run_or_404(request, run_id), _store(request))


class SplitTwin(BaseModel):
    split_spec: dict[str, Any] = Field(..., description="The other split: {strategy, group_column, time_column, cutoff, tuning_share}.")
    name: str | None = None


FAIR_KEYS = ("presets", "time_limit", "fit_kwargs", "predictor_kwargs", "eval_metric", "problem_type", "drop_columns",
             "column_types", "custom_text_models", "calibrate_threshold", "cost", "monotone", "quantile_levels", "conformal",
             "scorecard")


@router.post("/runs/{run_id}/split-twin", status_code=201, dependencies=[require("run.train", path("run_id", "run"))])
async def split_twin(run_id: str, body: SplitTwin, request: Request) -> dict[str, Any]:
    """F6: the same run again under another split: the body that started it (presets, budget, hyperparameters, metric,
    dropped columns: kept explicitly, so the features are identical) with `split_spec` replaced. Compare the two
    with GET /api/runs/{id}/split-comparison."""
    from ..gym.api import run_body

    rec = run_or_404(request, run_id)
    if rec["config"].get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or rec["config"].get("import"):
        raise HTTPException(400, "Only trained tabular runs can be re-run under another split.")
    b = run_body(rec)
    b["split_spec"] = body.split_spec
    b["parent_run_id"] = run_id
    b["source"] = "split-twin"
    strategy = body.split_spec.get("strategy") or "random-stratified"
    b["name"] = body.name or f"{rec['name']} · {strategy} split"
    return await run_in_threadpool(_create_run, RunCreate(**b), request)


def _score(rec: dict[str, Any]) -> tuple[str | None, float | None, bool]:
    h = (rec.get("result") or {}).get("holdout") or {}
    em = (rec.get("result") or {}).get("eval_metric") or {}
    m = em.get("name") or h.get("eval_metric")
    v = (h.get("metrics") or {}).get(m)
    return m, (None if v is None else float(v)), bool(em.get("greater_is_better", True))


@router.get("/runs/{run_id}/split-comparison")
def split_comparison(run_id: str, request: Request) -> dict[str, Any]:
    """F6: this run's holdout score next to the same dataset and label scored under other splits (the latest
    succeeded run per split), so the optimism of a random split is visible: Δ = score(random) − score(other) in the
    metric's direction (positive: the random split looks better than the other split says). `same_config` says
    whether the run's fit settings match this run's (only then is Δ about the split alone)."""
    store = _store(request)
    base = run_or_404(request, run_id)
    metric, _, gib = _score(base)
    cfg0 = base["config"]
    rows, seen = [], set()
    for r in store.list(dataset_id=base["dataset_id"], status="succeeded"):  # newest first; P1: filtered in SQL
        cfg = r["config"]
        if r["dataset_id"] != base["dataset_id"] or r["label"] != base["label"] or r["status"] != "succeeded" \
                or cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
            continue
        m, v, _ = _score(r)
        sp = cfg.get("split") or {}
        key = sp.get("key") or "legacy"
        same = all(cfg.get(k) == cfg0.get(k) for k in FAIR_KEYS)
        if m != metric or v is None or (key, same) in seen:
            continue
        if r["id"] != run_id and any(x["key"] == key and x["same_config"] for x in rows) and not same:
            continue
        seen.add((key, same))
        data = (r.get("result") or {}).get("data") or {}
        rows.append({"run_id": r["id"], "name": r["name"], "created_at": r["created_at"], "key": key,
                     "strategy": sp.get("strategy", "random-stratified"), "params": sp.get("params"),
                     "cutoff": sp.get("cutoff"), "tuning_cutoff": sp.get("tuning_cutoff"),
                     "holdout_rows": data.get("holdout_rows"), "train_rows": data.get("train_rows"),
                     "tuning_rows": data.get("tuning_rows", 0), "excluded_rows": data.get("excluded_rows", 0),
                     "score": v, "same_config": same, "this_run": r["id"] == run_id,
                     "best_model": (r.get("result") or {}).get("best_model")})
    rows = [x for x in rows if x["same_config"] or x["this_run"]] + [x for x in rows if not (x["same_config"] or x["this_run"])]
    rand = next((x for x in rows if x["strategy"] == "random-stratified" and (x["same_config"] or x["this_run"])), None)
    for x in rows:
        x["optimism"] = None
        if rand is not None and x is not rand and x["strategy"] != "random-stratified":
            d = rand["score"] - x["score"]
            x["optimism"] = d if gib else -d
    return {"run_id": run_id, "dataset_id": base["dataset_id"], "label": base["label"], "metric": metric,
            "greater_is_better": gib, "random_run_id": rand["run_id"] if rand else None, "runs": rows,
            "strategies_missing": [k for k in ("random-stratified", "time", "group", "time-group")
                                   if not any(x["strategy"] == k and (x["same_config"] or x["this_run"]) for x in rows)]}


@router.post("/runs/{run_id}/cancel", dependencies=[require("run.manage", path("run_id", "run"))])
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    if not _runner(request).cancel(run_id):
        raise HTTPException(409, f"Run {run_id} has already finished ({rec['status']}).")
    return {"id": run_id, "cancelling": True}


@router.delete("/runs/{run_id}", status_code=204, dependencies=[require("run.manage", path("run_id", "run"))])
def delete_run(run_id: str, request: Request, by: str = "", reason: str = "") -> None:
    """DL2 (B1): `by` is required and the delete is audited (run_deleted). Refused (409) while the run is the source of a
    registered version that is not rejected, failed or retired, or of a pending or approved champion: the lineage edges
    stay either way, so the data it trained on keeps its guard."""
    from ..catalog import governance as G
    from ..catalog.api import _who
    from ..catalog.schema import audit

    who = _who(by or G.current_actor())
    rec = run_or_404(request, run_id)
    db = request.app.state.db
    if rec["status"] in ("queued", "running"):
        G.refused(db, who, "run_delete", 409, "the run is still " + rec["status"], {"run_id": run_id})
        raise HTTPException(409, "Cancel the run before deleting it.")
    from ..catalog import holds as H  # P24: a legal hold on the run, its data, project, team or model blocks the delete

    with db.connect() as con:
        hs = H.active(con, H.run_keys(con, run_id))
    if hs:
        msg = H.refusal(hs, f"delete run {run_id}")
        G.refused(db, who, "run_delete", 409, msg, {"run_id": run_id})
        raise HTTPException(409, msg)
    with db.connect() as con:
        vers = [dict(r) for r in con.execute(
            "SELECT v.id, v.model_name, v.version, v.approval, v.status, v.retired_at FROM lineage_edges e JOIN model_versions v"
            " ON v.id = e.dst_id WHERE e.relation = 'built_from' AND e.src_id = ? UNION SELECT id, model_name, version, approval,"
            " status, retired_at FROM model_versions WHERE run_id = ?", (run_id, run_id))]
        vids = [v["id"] for v in vers] or [""]
        champs = [dict(r) for r in con.execute(
            f"SELECT id, status, project_id, version_id FROM champions WHERE status IN ('pending', 'approved') AND (run_id = ?"
            f" OR version_id IN ({','.join('?' * len(vids))}))", (run_id, *vids))]
    bad = [f"{v['model_name']} v{v['version']} ({v['approval']}) was built from it: reject it or retire it in the Registry first "
           f"(POST /api/registry/versions/{v['id']}/retire)" for v in vers
           if v["approval"] != "rejected" and v["status"] != "failed" and not v["retired_at"]]
    bad += [f"champion {c['id']} ({c['status']}) of project {c['project_id']} uses version {c['version_id']} built from it" for c in champs]
    if bad:
        msg = {"message": f"Can't delete run {run_id}: a registered version or champion comes from it.", "blockers": bad}
        G.refused(db, who, "run_delete", 409, msg, {"run_id": run_id})
        raise HTTPException(409, msg)
    with db.connect() as con:
        audit(con, who, "run_deleted", comment=reason[:500], run_id=run_id,
              details={"run_id": run_id, "dataset_id": rec["dataset_id"], "name": rec.get("name"), "status": rec["status"],
                       "registered_versions": [{"id": v["id"], "approval": v["approval"], "retired": bool(v["retired_at"])} for v in vers],
                       "kept": "lineage edges and the audit log"})
        con.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM blob_refs WHERE owner_kind = 'run' AND owner_id = ?", (run_id,))  # P24: its pins go with it
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    shutil.rmtree(request.app.state.settings.runs_dir / run_id, ignore_errors=True)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, start: int = Query(0, alias="from", ge=0),
                     format: Literal["sse", "json"] = "sse"):
    """Every event of the run from `from` (default 0). SSE by default: `id` is the event's seq, `event` its type,
    `data` the JSON payload; a reconnect with Last-Event-ID resumes after it. The stream ends after the terminal
    event (run_succeeded, run_failed or run_cancelled). `format=json` returns what is there now, once."""
    run_or_404(request, run_id)
    store = _store(request)
    if format == "json":
        evs = await run_in_threadpool(store.events, run_id, start, 100_000)
        return {"events": evs, "next": (evs[-1]["seq"] + 1) if evs else start}
    last_id = request.headers.get("last-event-id")
    if last_id is not None and last_id.strip().lstrip("-").isdigit():
        start = max(start, int(last_id) + 1)

    async def gen():
        cursor = start
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            evs = await run_in_threadpool(store.events, run_id, cursor, 500)
            for e in evs:
                yield {"id": str(e["seq"]), "event": e["type"], "data": json.dumps(e)}
                cursor = e["seq"] + 1
                if e["type"] in TERMINAL_EVENTS:
                    return
            if not evs:
                idle += 1
                # Asked to start past the terminal event: nothing more will come.
                if idle % 8 == 1 and await run_in_threadpool(store.has_terminal_event, run_id):
                    rec = await run_in_threadpool(store.get, run_id)
                    # F19: the terminal event may have been written after this loop's read; deliver it (and anything
                    # before it) instead of ending the stream one event short.
                    if rec and rec["status"] not in ("queued", "running") \
                            and not await run_in_threadpool(store.events, run_id, cursor, 1):
                        return
                    continue
                await asyncio.sleep(0.25)
            else:
                idle = 0

    return EventSourceResponse(gen(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _require_succeeded(rec: dict[str, Any]) -> None:
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {rec['id']} is {rec['status']}; it needs to finish first.")


@router.post("/runs/{run_id}/predict", dependencies=[require("run.use", path("run_id", "run"))])
async def predict(run_id: str, body: PredictRequest, request: Request) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    if len(body.rows) > 10_000:
        raise HTTPException(413, "At most 10,000 rows per request.")
    try:
        out = await request.app.state.heavy.run(heavy.predict_rows, rec["config"]["predictor_path"], body.rows, body.model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


def parse_constraints(text: str | None, rec: dict[str, Any]) -> dict[str, int]:
    """`col:1,col2:-1` (or increasing / decreasing), else the run's own constraints."""
    from . import monotone

    if text:
        out = {}
        for part in text.split(","):
            if not part.strip():
                continue
            col, _, v = part.rpartition(":")
            if not col:
                raise HTTPException(400, f"constraints: write column:increasing or column:decreasing (got {part!r}).")
            try:
                s = monotone._sign(v, col)
            except ValueError as e:
                raise HTTPException(400, f"constraints: {e}") from e
            if s:
                out[col.strip()] = s
        if out:
            return out
    cons = (rec["config"].get("monotone") or {}).get("constraints")
    if not cons:
        raise HTTPException(400, "This run has no monotone constraints; give constraints=column:increasing to check one.")
    return cons


@router.get("/runs/{run_id}/monotone")
async def monotone_check(run_id: str, request: Request, model: str | None = None, constraints: str | None = None,
                         rows: int = Query(200, ge=10, le=2000), grid: int = Query(25, ge=3, le=101),
                         curves: int = Query(30, ge=0, le=200)) -> dict[str, Any]:
    """F8: individual conditional expectation and partial dependence of one model (default: the best) on raw holdout
    rows, for the run's monotone constraints (or `constraints=col:increasing,…` for any run, e.g. an unconstrained
    twin), plus the report the worker stored after the fit (where each constraint landed, every model's check)."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    if rec["config"].get("kind") or rec["config"].get("import"):
        raise HTTPException(400, "Monotone checks apply to tabular runs trained here.")
    pt = (rec.get("result") or {}).get("problem_type")
    if pt not in (None, "binary", "regression"):
        raise HTTPException(400, f"Monotone checks apply to binary and regression runs, not {pt}.")
    cons = parse_constraints(constraints, rec)
    try:
        out = await request.app.state.heavy.run(heavy.monotone_check, rec["config"]["predictor_path"], rec["config"],
                                                cons, model, rows, grid, curves)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    if out["problem_type"] not in ("binary", "regression"):
        raise HTTPException(400, f"Monotone checks apply to binary and regression runs, not {out['problem_type']}.")
    return {"run_id": run_id, "constraints": cons, "request": rec["config"].get("monotone"),
            "report": (rec.get("result") or {}).get("monotone"), **out}


def _scorecard_report(rec: dict[str, Any]) -> dict[str, Any]:
    _require_succeeded(rec)
    sc = (rec.get("result") or {}).get("scorecard")
    if not sc:
        pt = (rec.get("result") or {}).get("problem_type")
        raise HTTPException(400, "The scorecard challenger applies to binary runs." if pt != "binary" else
                                 "This run has no scorecard (it was switched off, or the run predates F11).")
    if not sc.get("available"):
        raise HTTPException(400, f"This run's scorecard is not available: {sc.get('error') or sc.get('reason')}")
    return sc


@router.get("/runs/{run_id}/scorecard")
def scorecard_report(run_id: str, request: Request) -> dict[str, Any]:
    """F11: the scorecard challenger. The deployable scorecard's table (points per bin, refitted on all training rows),
    IV and coefficient per feature, its holdout AUC / Gini / KS next to the served model's, and ΔAUC (served −
    scorecard) with a paired bootstrap CI. With an F7 cost: both models' expected cost per 1,000 decisions."""
    rec = run_or_404(request, run_id)
    sc = _scorecard_report(rec)
    return {"run_id": run_id, **sc, "family": catalogue.get("SCORECARD"),
            "export": f"/api/runs/{run_id}/scorecard.csv"}


@router.get("/runs/{run_id}/scorecard.csv", dependencies=[require("run.read", path("run_id", "run"))])
def scorecard_csv(run_id: str, request: Request):
    """F11: the exported scorecard: feature, bin, points (rounded and exact), WoE, counts, event rate, IV, coefficient;
    a last `_scaling` row with PDO, base score / odds, factor, offset and intercept."""
    from fastapi.responses import FileResponse

    rec = run_or_404(request, run_id)
    _scorecard_report(rec)
    path = Path(rec["config"]["run_dir"]) / "scorecard" / "scorecard.csv"
    if not path.exists():
        raise HTTPException(404, "The scorecard table was not saved.")
    return FileResponse(path, media_type="text/csv", filename=f"scorecard_{run_id}.csv")


class ScorecardScore(BaseModel):
    rows: list[dict[str, Any]] | None = None
    holdout_index: list[int] | None = Field(None, description="Holdout rows by position (0 … n−1) instead of rows.")
    top_k: int = Field(4, ge=1, le=10)


@router.post("/runs/{run_id}/scorecard/score", dependencies=[require("run.use", path("run_id", "run"))])
async def scorecard_score(run_id: str, body: ScorecardScore, request: Request) -> dict[str, Any]:
    """F11: score rows with the deployable scorecard: points per feature (and the bin), the total score, P(event), and
    the scorecard's own reason codes: features ranked by points lost against their best bin."""
    rec = run_or_404(request, run_id)
    _scorecard_report(rec)
    if (body.rows is None) == (body.holdout_index is None):
        raise HTTPException(400, "Give rows or holdout_index (one of them).")
    try:
        out = await request.app.state.heavy.run(heavy.scorecard_score, rec["config"], body.rows, body.holdout_index, body.top_k)
    except (serving.ServingError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/ladder")
async def model_ladder(run_id: str, request: Request, refresh: bool = False) -> dict[str, Any]:
    """D2: the model ladder. Baseline → scorecard (or linear) → single GBM → AutoGluon's best, every rung a model of
    this run scored on the same holdout, the gap to the best with a paired bootstrap CI, one-row latency and size, and
    the recommendation (the simplest rung within the interval; 99 % on small data). Computed at the end of the fit;
    recomputed here for older runs or with ?refresh=true."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    if rec["config"].get("kind") or rec["config"].get("import"):
        raise HTTPException(400, "The ladder applies to tabular runs trained here (binary, multiclass, regression).")
    if res.get("problem_type") not in ("binary", "multiclass", "regression"):
        raise HTTPException(400, f"The ladder applies to binary, multiclass and regression runs, not {res.get('problem_type')}.")
    lad = res.get("ladder")
    if refresh or not lad or lad.get("error"):
        from . import ladder

        try:
            lad = await request.app.state.heavy.run(ladder.for_run, rec["config"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"The ladder failed: {type(e).__name__}: {e}") from e
        _store(request).merge_result(run_id, ladder=lad)
    return {"run_id": run_id, "registrable": True, **lad}


@router.get("/runs/{run_id}/oof", dependencies=[require("run.read", path("run_id", "run"))])
async def out_of_fold(run_id: str, request: Request, model: str | None = None) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    try:
        out = await request.app.state.heavy.run(heavy.out_of_fold, rec["config"]["predictor_path"], model)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/feature-importance")
async def feature_importance(run_id: str, request: Request,
                             shuffles: int | None = Query(None, ge=2, le=50)) -> dict[str, Any]:
    """Permutation importance on the holdout: stored if the run computed it, else computed now and stored.
    `shuffles` recomputes it with that many shuffle sets (tighter intervals). Each feature has a 95% t-interval
    importance ± t₀.₉₇₅,ₙ₋₁·s/√n over the n shuffles."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    fi = res.get("feature_importance")
    meta = res.get("feature_importance_meta") or {"num_shuffle_sets": 3, "subsample_size": None}
    if fi is None or (shuffles is not None and shuffles != meta.get("num_shuffle_sets")):
        cfg = rec["config"]
        n_sets = shuffles or 5

        # In a worker process: permutation importance re-predicts the holdout many times (CPU-bound).
        fi, sub = await request.app.state.heavy.run(heavy.permutation_importance, cfg, n_sets)
        meta = {"num_shuffle_sets": n_sets, "subsample_size": sub}
        _store(request).merge_result(run_id, feature_importance=fi, feature_importance_meta=meta)
    return {"run_id": run_id, "features": explain.importance_with_ci(fi), "meta": meta,
            "method": "permutation importance on the holdout: score drop when one feature's values are shuffled "
                      "(AutoGluon's predictor.feature_importance)", "eval_metric": (res.get("eval_metric") or {}).get("name")}


@router.get("/runs/{run_id}/holdout-predictions", dependencies=[require("run.read", path("run_id", "run"))])
async def holdout_predictions(run_id: str, request: Request, by: str = "") -> dict[str, Any]:
    """The best model's predictions on the holdout rows: data.csv row, true label, prediction and (classification)
    one probability per class. Residuals and curves are computed from these. DL3 (m2): audited as a read (needs a name)."""
    from ..catalog import governance as G

    G.reader(by)
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    path = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if not path.exists():
        raise HTTPException(404, "This run has no saved holdout predictions.")
    G.run_read_audit(request.app, "holdout_predictions", run_id, by=by)

    def read():
        from ..lake.mask import for_run as mask_for_run
        from . import quantile as qt
        from .jsonsafe import jsonable

        df = frames.read_artifact(path)
        prob_cols = [c for c in df.columns if c.startswith("p_")]
        m = mask_for_run(rec)  # P20: the page holds no feature values; only a PII-tagged label would need masking
        if m is not None and rec["label"] in m.cols:
            df["y_true"] = [m.value(rec["label"], v) for v in df["y_true"]]
            df["y_pred"] = [m.value(rec["label"], v) for v in df["y_pred"]]
        return {"run_id": run_id, "model": (rec.get("result") or {}).get("best_model"), "n_rows": len(df),
                "rows": jsonable(df["row"].tolist()), "y_true": jsonable(df["y_true"].tolist()),
                "y_pred": jsonable(df["y_pred"].tolist()),
                "classes": [c[2:] for c in prob_cols] or None,
                "probabilities": jsonable(df[prob_cols].to_numpy()) if prob_cols else None,
                **({"quantile_levels": saved[0], "quantiles": jsonable(saved[1])} if (saved := qt.from_saved(df)) else {})}

    return await run_in_threadpool(read)


_ensemble_locks: dict[str, threading.Lock] = {}
_ensemble_locks_guard = threading.Lock()


def _fit_custom_ensemble(app, rec: dict[str, Any], models: list[str]) -> dict[str, Any]:
    """Run app.engine.custom_ensemble in its own process (it writes into the predictor folder), one per run at a time."""
    run_id = rec["id"]
    with _ensemble_locks_guard:
        lock = _ensemble_locks.setdefault(run_id, threading.Lock())
    with lock:
        store: RunStore = app.state.runs
        rec = store.get(run_id)
        existing = (rec.get("result") or {}).get("custom_ensembles") or []
        n = len(existing) + 1
        run_dir = Path(rec["config"]["run_dir"])
        req_dir = run_dir / "custom_ensembles"
        req_dir.mkdir(exist_ok=True)
        req_path = req_dir / f"request_{n}.json"
        req_path.write_text(json.dumps({"models": models, "suffix": f"_Custom{n}"}))
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "MLFLOW_DISABLE_AGENT_HINT": "1",
               "PYTHONPATH": os.pathsep.join(p for p in (str(config.BACKEND_DIR), os.environ.get("PYTHONPATH", "")) if p)}
        try:
            p = subprocess.run([sys.executable, "-m", "app.engine.custom_ensemble", str(run_dir), str(req_path)],
                               cwd=config.BACKEND_DIR, env=env, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise HTTPException(504, "Fitting the ensemble took longer than 10 minutes; it was stopped.") from e
        (req_dir / f"log_{n}.txt").write_text((p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""))
        lines = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
        if not lines:
            raise HTTPException(500, f"The ensemble process exited with code {p.returncode}: {(p.stderr or '')[-800:]}")
        out = json.loads(lines[-1])
        if not out["ok"]:
            raise HTTPException(400, out["error"])
        res = out["result"]
        app.state.predictors.drop(rec["config"]["predictor_path"])

        s = res["scores"]
        lb = list((rec.get("result") or {}).get("leaderboard") or [])
        lb.append({"model": res["model"], "family": "ENS_WEIGHTED", "score_val": s["score_val"], "score_test": s["score_test"],
                   "score_val_natural": s["score_val_natural"], "score_test_natural": s["score_test_natural"],
                   "fit_time": s["fit_time"], "pred_time_test": s["pred_time_test"], "pred_time_val": None,
                   "stack_level": s["stack_level"], "can_infer": True, "fold_scores": None, "val_score_std": None,
                   "weights": res["weights"], "custom": True, "model_type": "WeightedEnsembleModel",
                   "predict_model": res.get("predict_model")})
        lb.sort(key=lambda r: -(r.get("score_val") if r.get("score_val") is not None else float("-inf")))
        for i, r in enumerate(lb, start=1):
            r["rank"] = i
        store.merge_result(run_id, custom_ensembles=existing + [res], leaderboard=lb)
        return res


@router.post("/runs/{run_id}/ensemble", status_code=201, dependencies=[require("run.train", path("run_id", "run"))])
async def custom_ensemble(run_id: str, body: EnsembleRequest, request: Request) -> dict[str, Any]:
    """Fit a weighted ensemble on the models you choose: `TabularPredictor.fit_weighted_ensemble(base_models=...)`.
    The result has its weights, its validation and holdout scores, and whether it beats the best single model."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    models = list(dict.fromkeys(body.models))
    names = {r["model"] for r in (rec.get("result") or {}).get("leaderboard") or []}
    unknown = [m for m in models if m not in names]
    if unknown:
        raise HTTPException(400, f"No model named {', '.join(map(repr, unknown))} in this run.")
    ens = [m for m in models if m.startswith("WeightedEnsemble")]
    if ens:
        raise HTTPException(400, f"Choose base models, not weighted ensembles ({', '.join(ens)}).")
    full = [m for m in models if m.endswith("_FULL")]
    if full:
        raise HTTPException(400, f"Refit models ({', '.join(full)}) have no validation predictions to weight; "
                                 "choose the models they were refit from.")
    res = await run_in_threadpool(_fit_custom_ensemble, request.app, rec, models)
    return {"run_id": run_id, **res}


# --------------------------------------------------------------------------- understand (S4)


def _holdout_df(rec: dict[str, Any]):
    try:
        return explain.load_holdout(Path(rec["config"]["run_dir"]))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


def _require_binary(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result") or {}
    if res.get("problem_type") != "binary":
        raise HTTPException(400, f"A decision threshold applies to binary classification; this run is {res.get('problem_type')}.")
    return res


def _current_threshold(res: dict[str, Any]) -> float:
    return float((res.get("threshold") or {}).get("value", 0.5))


@router.get("/runs/{run_id}/threshold")
async def get_threshold(run_id: str, request: Request, cost_fp: float = Query(1.0, ge=0), cost_fn: float = Query(1.0, ge=0),
                        t: float | None = Query(None, ge=0, le=1)) -> dict[str, Any]:
    """Binary runs: precision, recall, F1, balanced accuracy, MCC, the confusion counts and the cost
    C(t) = c_FP·FP + c_FN·FN on the holdout at every t on a 0.005 grid (and at `t` exactly), the thresholds that
    maximise F1 / balanced accuracy / MCC and minimise cost, and the threshold the predictor uses now."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    res = _require_binary(rec)
    df = _holdout_df(rec)
    out = await run_in_threadpool(explain.threshold_analysis, df, res["positive_class"], current=_current_threshold(res),
                                  cost_fp=cost_fp, cost_fn=cost_fn, at=t)
    return {"run_id": run_id, "model": res.get("best_model"), "threshold": res.get("threshold"), **out}


# --------------------------------------------------------------------------- business cost (F7)


def _cost_for(rec: dict[str, Any], c_fp: float | None, c_fn: float | None, g_tp: float | None) -> dict[str, Any]:
    """The cost to score a run with: the query's (binary: c_fp, c_fn[, g_tp]) or else the run's own."""
    res = rec.get("result") or {}
    if c_fp is not None or c_fn is not None:
        if res.get("problem_type") != "binary":
            raise HTTPException(400, "c_fp / c_fn apply to binary runs; a multiclass run is costed with its own matrix.")
        if c_fp is None or c_fn is None:
            raise HTTPException(400, "Give both c_fp and c_fn.")
        try:
            return costs.normalise({"c_fp": c_fp, "c_fn": c_fn, "g_tp": g_tp or 0, "positive_class": res.get("positive_class")},
                                   "binary", res.get("classes") or [])
        except ValueError as e:
            raise HTTPException(400, f"cost: {e}") from e
    cost = rec["config"].get("cost")
    if not cost:
        raise HTTPException(400, "This run was not trained on a cost; give c_fp and c_fn to cost its decisions.")
    return cost


def _validation_threshold(app: Any, rec: dict[str, Any], cost: dict[str, Any]) -> float | None:
    """Binary: the threshold that minimises this cost on the run's own validation data (AutoGluon's
    calibrate_decision_threshold with the label-based cost scorer; never the holdout). A cost run trained on this
    cost already uses it; for other runs it is computed once and kept in the run's result."""
    if cost["kind"] != "binary":
        return None
    res = rec.get("result") or {}
    thr = res.get("threshold") or {}
    if rec["config"].get("cost") and costs.key(rec["config"]["cost"]) == costs.key(cost) and thr.get("metric") == costs.METRIC_NAME:
        return float(thr["value"])
    k = costs.key(cost)
    cached = (res.get("cost_thresholds") or {}).get(k)
    if cached is not None:
        return float(cached)
    from ensemble_models.cost import binary_cost_class_scorer

    with _run_lock(rec["id"]):
        p = app.state.predictors.get(rec["config"]["predictor_path"])
        t = float(p.calibrate_decision_threshold(metric=binary_cost_class_scorer(cost["c_fp"], cost["c_fn"], cost["g_tp"]),
                                                 verbose=False))
    store = app.state.runs
    now_res = (store.get(rec["id"]) or {}).get("result") or {}
    store.merge_result(rec["id"], cost_thresholds={**(now_res.get("cost_thresholds") or {}), k: t})
    return t


@router.get("/runs/{run_id}/cost")
async def run_cost(run_id: str, request: Request, c_fp: float | None = Query(None, ge=0), c_fn: float | None = Query(None, ge=0),
                   g_tp: float | None = Query(None, ge=0)) -> dict[str, Any]:
    """F7: the expected cost of the run's holdout decisions per 1,000 with a 95% bootstrap interval. Binary: at the
    threshold in use, at the Bayes threshold t* = c_FP/(c_FP + c_FN + g_TP), at 0.5, at the threshold calibrated for
    this cost on the run's validation data, flagging nobody / everybody, and the cost curve over t on a 0.005 grid.
    Multiclass: the served labels (arg-max) and the Bayes decisions, with the cost per confusion cell."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    cost = _cost_for(rec, c_fp, c_fn, g_tp)
    res = rec.get("result") or {}
    df = _holdout_df(rec)
    vt = await run_in_threadpool(_validation_threshold, request.app, rec, cost)
    out = await run_in_threadpool(costs.analysis, df, cost, positive=res.get("positive_class"),
                                  current=_current_threshold(res), validation_threshold=vt)
    return {"run_id": run_id, "model": res.get("best_model"), "eval_metric": (res.get("eval_metric") or {}).get("name"),
            "trained_on_cost": bool(rec["config"].get("cost")), "threshold": res.get("threshold"),
            "validation_threshold": vt, **out}


class MetricTwin(BaseModel):
    eval_metric: str | None = Field(None, description="The other metric (any AutoGluon metric name).")
    cost: dict[str, Any] | None = Field(None, description="Or another cost (as in POST /runs).")
    calibrate_threshold: str | None = Field("auto", description="The twin's threshold: 'auto' (F1 when the minority "
                                            "class < 20%, as the wizard does), a metric, or null for t = 0.5.")
    name: str | None = None


@router.post("/runs/{run_id}/metric-twin", status_code=201, dependencies=[require("run.train", path("run_id", "run"))])
async def metric_twin(run_id: str, body: MetricTwin, request: Request) -> dict[str, Any]:
    """F7: the same run (data, split, presets, budget, hyperparameters, dropped columns) trained to another metric or
    cost, so the cost-optimised run can be compared with, say, the ROC-AUC-optimised one on the same holdout rows."""
    from ..gym.api import check_run_body, run_body

    rec = run_or_404(request, run_id)
    if rec["config"].get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or rec["config"].get("import"):
        raise HTTPException(400, "Only trained tabular runs can be re-run to another metric.")
    if (body.eval_metric is None) == (body.cost is None):
        raise HTTPException(400, "Give eval_metric or cost.")
    b = run_body(rec)
    b.pop("cost", None)
    b["eval_metric"] = body.eval_metric
    if body.cost is not None:
        b["cost"] = body.cost
        b["eval_metric"] = None
    b["calibrate_threshold"] = body.calibrate_threshold
    b["parent_run_id"] = run_id
    b["source"] = "metric-twin"
    b["name"] = body.name or f"{rec['name']} · {'cost' if body.cost is not None else body.eval_metric}"
    await check_run_body(request, b)
    return await run_in_threadpool(_create_run, RunCreate(**b), request)


COST_FAIR_KEYS = tuple(k for k in FAIR_KEYS if k not in ("eval_metric", "cost", "calibrate_threshold"))


@router.get("/runs/{run_id}/cost-comparison")
async def cost_comparison(run_id: str, request: Request, c_fp: float | None = Query(None, ge=0),
                          c_fn: float | None = Query(None, ge=0), g_tp: float | None = Query(None, ge=0)) -> dict[str, Any]:
    """F7: this run beside the same fit trained to other metrics (same data, label, split rows and fit settings;
    the latest succeeded run per metric), all costed with this run's cost: expected cost per 1,000 at the threshold
    each run uses, and (binary) at the threshold each run would get if calibrated for this cost on its own validation
    data, with paired bootstrap intervals of Δ = cost(other) − cost(this) on the shared holdout rows, and each run's
    cost curve over t."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    cost = _cost_for(rec, c_fp, c_fn, g_tp)
    app = request.app

    def build() -> dict[str, Any]:
        store = _store(request)
        cfg0 = rec["config"]
        key0 = (cfg0.get("split") or {}).get("key")
        picked: list[dict[str, Any]] = [rec]
        seen = {_metric_slot(rec)}
        for r in store.list(dataset_id=rec["dataset_id"], status="succeeded"):  # newest first; P1: filtered in SQL
            cfg = r["config"]
            if r["id"] == run_id or r["dataset_id"] != rec["dataset_id"] or r["label"] != rec["label"] \
                    or r["status"] != "succeeded" or cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
                continue
            if (cfg.get("split") or {}).get("key") != key0 or any(cfg.get(k) != cfg0.get(k) for k in COST_FAIR_KEYS):
                continue
            slot = _metric_slot(r)
            if slot in seen or (r["config"].get("cost") and costs.key(r["config"]["cost"]) != costs.key(cost)):
                continue
            seen.add(slot)
            picked.append(r)
        rows, base_rows, base_ids, base_val = [], None, None, None
        for r in picked:
            res = r.get("result") or {}
            hp = explain.load_holdout(Path(r["config"]["run_dir"]))
            pos = res.get("positive_class")
            t_now = _current_threshold(res) if res.get("problem_type") == "binary" else None
            vt = _validation_threshold(app, r, cost)
            a = costs.analysis(hp, cost, positive=pos, current=t_now, validation_threshold=vt)
            dep = costs.row_costs(hp, cost, positive=pos, t=t_now)
            val = costs.row_costs(hp, cost, positive=pos, t=vt) if vt is not None else None
            ids = hp["row"].to_numpy()
            if r["id"] == run_id:
                base_rows, base_ids, base_val = dep, ids, val
            thr = res.get("threshold") or {}
            rows.append({
                "run_id": r["id"], "name": r["name"], "this_run": r["id"] == run_id, "created_at": r["created_at"],
                "eval_metric": (res.get("eval_metric") or {}).get("name"), "trained_on_cost": bool(r["config"].get("cost")),
                "best_model": res.get("best_model"), "holdout_rows": len(hp),
                "threshold": {"value": t_now, "source": thr.get("source"), "metric": thr.get("metric")} if t_now is not None else None,
                "validation_threshold": vt, "deployed": a["deployed"], "validation": a.get("validation"),
                "bayes": a["bayes"], "roc_auc": ((res.get("holdout") or {}).get("metrics") or {}).get("roc_auc"),
                "accuracy": ((res.get("holdout") or {}).get("metrics") or {}).get("accuracy"),
                "curve": [[c["t"], c["per_1000"]] for c in a.get("curve", [])], "_dep": dep, "_val": val, "_ids": ids,
            })
        for x in rows:
            x["delta"] = None if x["this_run"] else costs.paired(base_rows, x.pop("_dep"), base_ids, x["_ids"])
            v = x.pop("_val")
            x["delta_validation"] = None if x["this_run"] or v is None or base_val is None else costs.paired(base_val, v, base_ids, x["_ids"])
            x.pop("_ids", None)
            x.pop("_dep", None)
        base_res = rec.get("result") or {}
        wanted = ["roc_auc", "accuracy"] if base_res.get("problem_type") == "binary" else ["accuracy", "log_loss"]
        have = {x["eval_metric"] for x in rows if not x["trained_on_cost"]}
        return {"run_id": run_id, "cost": cost, "per": costs.PER, "B": costs.B, "level": costs.LEVEL,
                "problem_type": base_res.get("problem_type"), "runs": rows,
                "trained_on_cost_present": any(x["trained_on_cost"] for x in rows),
                "metrics_missing": [m for m in wanted if m not in have]}

    try:
        return await run_in_threadpool(build)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


def _metric_slot(r: dict[str, Any]) -> str:
    cfg = r["config"]
    return "cost:" + costs.key(cfg["cost"]) if cfg.get("cost") else "metric:" + str(((r.get("result") or {}).get("eval_metric") or {}).get("name"))


class ThresholdRequest(BaseModel):
    metric: str | None = Field(None, description="Calibrate: predictor.calibrate_decision_threshold(metric=metric).")
    value: float | None = Field(None, ge=0, le=1, description="Or set this threshold directly.")


@router.post("/runs/{run_id}/threshold", dependencies=[require("run.train", path("run_id", "run"))])
async def set_threshold(run_id: str, body: ThresholdRequest, request: Request) -> dict[str, Any]:
    """Calibrate (`metric`) or set (`value`) the decision threshold and persist it in the predictor
    (`set_decision_threshold` + `save`), so every later prediction uses it. The holdout is re-scored at it."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    _require_binary(rec)
    if (body.metric is None) == (body.value is None):
        raise HTTPException(400, "Give either metric (to calibrate) or value (to set the threshold).")
    if body.metric is not None and body.metric not in threshold.CALIBRATION_METRICS:
        raise HTTPException(400, f"Calibrate for one of {', '.join(threshold.CALIBRATION_METRICS)}.")
    cfg = rec["config"]
    app = request.app

    def work():
        with _run_lock(run_id):
            p = app.state.predictors.get(cfg["predictor_path"])
            h = serving.holdout_frame(frames.resolve(cfg), load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
            t0 = time.time()
            if body.metric is not None:
                value, source = threshold.calibrate(p, body.metric), "calibrated"
            else:
                value, source = float(body.value), "manual"
            out = threshold.apply(p, Path(cfg["run_dir"]), h, cfg["label"], value, source=source, metric=body.metric,
                                  seconds=time.time() - t0)
            res_now = (_store(request).get(run_id) or {}).get("result") or {}
            extra = {}
            if "fit_threshold" not in res_now:  # the threshold the run's leaderboard was scored at (export compares it)
                extra["fit_threshold"] = res_now.get("threshold") or {"source": "default", "value": 0.5, "metric": None}
            _store(request).merge_result(run_id, threshold=out["threshold"], holdout=out["holdout"], **extra)
            return out

    try:
        out = await run_in_threadpool(work)
    except (ValueError, AssertionError) as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


@router.get("/runs/{run_id}/explain", dependencies=[require("run.read", path("run_id", "run"))])
async def explain_run(run_id: str, request: Request) -> dict[str, Any]:
    """Classification: confusion matrix, per-class precision/recall/F1, ROC and PR curves (one-vs-rest for
    multiclass) with AUC and average precision, the reliability curve with the Brier score and ECE, and the worst
    mistakes with their features. Regression: residuals against predictions, their histogram, RMSE/MAE/R² and the
    largest errors. Plus the columns that can define groups for fairness."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    df = _holdout_df(rec)
    cfg = rec["config"]
    ds = request.app.state.db.get_dataset(rec["dataset_id"])

    def build():
        if res.get("problem_type") in ("binary", "multiclass"):
            body = explain.classification_report(df, [str(c) for c in explain._classes(df)], res.get("positive_class"), cfg)
        else:
            body = explain.regression_report(df, cfg)
            if res.get("problem_type") == "quantile":  # F9: the residuals are about the centre level q̂₀.₅
                body["centre_level"] = (res.get("quantile") or {}).get("centre")
                body["quantile"] = {k: (res.get("quantile") or {}).get(k) for k in ("levels", "centre", "main_interval", "holdout", "served")}
        return M.run_scrub(rec, body)  # P20: the worst cases' feature values of PII columns are masked

    from ..lake import mask as M

    body = await run_in_threadpool(build)
    return {"run_id": run_id, "model": res.get("best_model"), "problem_type": res.get("problem_type"),
            "eval_metric": res.get("eval_metric"), "label": rec["label"], "threshold": res.get("threshold"),
            "group_candidates": explain.group_candidates(cfg, res.get("features") or [], ds["profile"] if ds else None),
            "text_columns": cfg.get("text_columns") or [],
            # G4: a surname column makes the BISG estimated groups available in Fairness (binary runs)
            "bisg_surname": next((c["name"] for c in (ds["profile"] if ds else {}).get("columns", [])
                                  if res.get("problem_type") == "binary" and intake.SURNAME.search(str(c["name"]))), None),
            **body}


@router.get("/runs/{run_id}/fairness")
async def fairness(run_id: str, request: Request, group: str = Query(..., min_length=1),
                   t: float | None = Query(None, ge=0, le=1)) -> dict[str, Any]:
    """fairlearn's MetricFrame on the holdout, one row per value of `group` (numbers with more than 8 values are cut
    into quartiles). Binary: selection rate, base rate, accuracy, TPR, FPR, precision per group at the run's threshold
    (or `t`), with the demographic parity difference and the equalised odds difference."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    res = rec.get("result") or {}
    df = _holdout_df(rec)
    pt = res.get("problem_type")
    thr_value = t if t is not None else (_current_threshold(res) if pt == "binary" else None)
    try:
        out = await run_in_threadpool(explain.fairness, df, rec["config"], group, problem_type=pt,
                                      positive=res.get("positive_class"), threshold=thr_value)
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, f"No column named {group!r} in the dataset." if "not in list" in str(e) or "Usecols" in str(e) else str(e)) from e
    reports = dict(res.get("fairness_reports") or {})
    reports[group] = {"gaps": out.get("gaps"), "threshold": thr_value, "computed_at": now(),
                      "binning": out.get("binning"), "by_group": (out.get("by_group") or [])[:20]}
    _store(request).merge_result(run_id, fairness_reports=reports)
    return {"run_id": run_id, "model": res.get("best_model"), **out}


@router.get("/runs/{run_id}/shap", dependencies=[require("run.read", path("run_id", "run"))])
async def shap_values(run_id: str, request: Request, row: int = Query(..., ge=0), target: str | None = None) -> dict[str, Any]:
    """Kernel SHAP for one holdout row (data.csv row number) on the best model: f(x) = φ₀ + Σᵢ φᵢ, where f is
    predict_proba of `target` (default: the positive class, or the predicted class for multiclass) or the predicted
    value (regression), φ₀ = E[f] over a fixed sample of 50 training rows. Cached per row."""
    rec = run_or_404(request, run_id)
    _require_succeeded(rec)
    cfg = rec["config"]
    app = request.app

    try:
        # In a worker process (Kernel SHAP evaluates thousands of coalitions); cached per row in the run folder.
        out = await app.state.heavy.run(heavy.shap_holdout_row, cfg, row, target)
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id, **out}


def _run_lock(run_id: str) -> threading.Lock:
    with _ensemble_locks_guard:
        return _ensemble_locks.setdefault(run_id, threading.Lock())


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    """What the status bar shows in live mode: the machine (CPU count, memory), versions, the active run and its
    progress (models started and finished, best validation score so far, elapsed against the budget) and the queue."""
    from .. import __version__

    store = _store(request)
    active = store.list_active()
    running = [r for r in active if r["status"] == "running"]
    cur = None
    if running:
        r = running[0]
        with request.app.state.db.connect() as con:
            rows = con.execute("SELECT type, data FROM run_events WHERE run_id = ? AND type IN "
                               "('fit_started', 'model_started', 'model_finished', 'model_failed', 'ensemble', "
                               "'leaderboard', 'evaluation', 'calibrating', 'explaining') ORDER BY seq", (r["id"],)).fetchall()
        started = finished = failed = 0
        best = None
        stage = "starting"
        greater = True
        for typ, data in rows:
            d = json.loads(data)
            if d.get("phase") == "dystack":
                continue
            stage = typ
            if typ == "fit_started":
                greater = bool(d.get("greater_is_better", True))
            elif typ == "model_started":
                started += 1
            elif typ == "model_finished":
                finished += 1
                v = d.get("val_score_natural")
                if v is not None and (best is None or (v > best if greater else v < best)):
                    best = v
            elif typ == "model_failed":
                failed += 1
        cfg = r["config"]
        t0 = r.get("started_at")
        cur = {"id": r["id"], "name": r["name"], "status": r["status"], "started_at": t0,
               "time_limit": cfg.get("time_limit"), "presets": cfg.get("presets"), "eval_metric": cfg.get("eval_metric"),
               "models_started": started, "models_finished": finished, "models_failed": failed, "best_val": best,
               "stage": stage}
    mem = None
    try:
        import psutil

        mem = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    import platform

    return {"version": __version__, "engine": engine_info(), "cpu_count": os.cpu_count(), "memory_gb": mem,
            "python": platform.python_version(), "host": platform.node(), "active": cur,
            "queued": sum(1 for r in active if r["status"] == "queued"), "runs": store.count(),
            "registry": request.app.state.registry.counts(), "pool": request.app.state.heavy.info(),
            "max_parallel_runs": getattr(request.app.state.runner, "max_parallel", None)}

"""Training Gym routes: the configuration schema, validation with a fit-time estimate, a run's config for the Gym,
and the export of a run as a Python script."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.concurrency import run_in_threadpool

router = APIRouter(prefix="/api")

# The RunCreate fields a run's config keeps, so the Gym (and a Vela patch) can start from it.
BODY_FIELDS = ("dataset_id", "problem_type", "eval_metric", "presets", "time_limit", "fit_kwargs", "predictor_kwargs",
               "custom_text_models", "feature_importance", "column_types", "calibrate_threshold", "pseudolabel", "distill",
               "split_spec", "cost", "monotone", "quantile_levels", "conformal")


def _schema_job() -> dict[str, Any]:
    from . import schema

    return schema.schema()


def _validate_job(body: dict[str, Any], ds: dict[str, Any] | None, unlabeled: dict[str, Any] | None,
                  history: list[dict[str, Any]]) -> dict[str, Any]:
    from . import validate

    return validate.validate(body, ds, unlabeled, history)


def _history(request: Request, limit: int = 40) -> list[dict[str, Any]]:
    recs = [r for r in request.app.state.runs.list() if r["status"] == "succeeded"]
    out = []
    for r in recs[:limit]:
        res = r.get("result") or {}
        out.append({"id": r["id"], "result": {"data": res.get("data"), "leaderboard": [
            {k: x.get(k) for k in ("model", "family", "fit_time", "num_models", "custom")} for x in res.get("leaderboard") or []]}})
    return out


async def run_validation(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    db = request.app.state.db
    ds = db.get_dataset(body.get("dataset_id") or "")
    pl = body.get("pseudolabel") or {}
    unlabeled = db.get_dataset(pl.get("dataset_id") or "") if pl else None
    hist = await run_in_threadpool(_history, request)
    return await request.app.state.heavy.run(_validate_job, body, ds, unlabeled, hist)


async def check_run_body(request: Request, body: dict[str, Any]) -> None:
    """Refuse a run AutoGluon would reject (unknown fit arguments, impossible values). Dataset, label and column
    errors are left to the run route, which words them for the wizard."""
    if not request.app.state.db.get_dataset(body.get("dataset_id") or ""):
        return
    v = await run_validation(request, body)
    errs = [e for e in v.get("errors", []) if e["path"] not in ("label", "dataset_id", "drop_columns")]
    if errs:
        raise HTTPException(400, "; ".join(f"{e['path']}: {e['message']}" for e in errs))


@router.get("/gym/schema")
async def gym_schema(request: Request) -> dict[str, Any]:
    cached = getattr(request.app.state, "gym_schema", None)
    if cached is None:
        cached = await request.app.state.heavy.run(_schema_job)
        request.app.state.gym_schema = cached
    return cached


@router.post("/gym/validate")
async def gym_validate(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Validate a run body (the same JSON `POST /api/runs` takes) without starting it: errors and warnings, the
    resolved fit call and its diff against TabularPredictor.fit's defaults, the bagging and stacking AutoGluon will
    choose for this data, and the fit-time estimate."""
    return await run_validation(request, body)


def run_body(rec: dict[str, Any]) -> dict[str, Any]:
    """The `POST /api/runs` body that starts this run again."""
    cfg = rec["config"]
    body = {k: cfg.get(k) for k in BODY_FIELDS if k in cfg}
    body["label"] = rec["label"]
    body["problem_type"] = cfg.get("problem_type") or "auto"
    body["drop_columns"] = cfg.get("drop_columns")
    return body


@router.get("/runs/{run_id}/gym")
def run_gym_config(run_id: str, request: Request) -> dict[str, Any]:
    """The run's request as the Gym edits it (the body that would start it again), plus its lineage."""
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    cfg = rec["config"]
    body = run_body(rec)
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "body": body,
            "parent_run_id": cfg.get("parent_run_id"), "patch": cfg.get("patch")}


@router.get("/runs/{run_id}/export.py")
async def export_py(run_id: str, request: Request, download: bool = False):
    from importlib import metadata

    from .. import config
    from . import export

    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; export a run that finished.")
    pl = rec["config"].get("pseudolabel") or {}
    unlabeled = str(request.app.state.datasets.csv_path(pl["dataset_id"])) if pl.get("dataset_id") else None
    try:
        ag = metadata.version("autogluon.tabular")
    except metadata.PackageNotFoundError:
        ag = None
    try:
        text = await run_in_threadpool(export.build_script, rec, backend_dir=config.BACKEND_DIR if hasattr(config, "BACKEND_DIR")
                                       else __import__("pathlib").Path(__file__).resolve().parents[2],
                                       unlabeled_csv=unlabeled, ag_version=ag)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    headers = {"Content-Disposition": f'{"attachment" if download else "inline"}; filename="export_{run_id}.py"'}
    return PlainTextResponse(text, media_type="text/x-python; charset=utf-8", headers=headers)

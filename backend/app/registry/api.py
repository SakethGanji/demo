"""Routes for the model registry (S5): register a run as a version, stages and reviews with an audit log, the
model card, Try It, batch scoring with drift, and MLflow's view of a version."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import config
from ..engine import explain
from . import build, card, scoring, tracking
from .store import STAGES, RegistryStore

router = APIRouter(prefix="/api")


def _reg(request: Request) -> RegistryStore:
    return request.app.state.registry


def _dir(request: Request) -> Path:
    d = request.app.state.settings.data_dir / "registry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _name_ok(s: str | None, what: str) -> str:
    s = (s or "").strip()
    if not s:
        raise HTTPException(400, f"{what} is required: say who you are (there is no sign-in; the name goes in the audit log).")
    if len(s) > 80:
        raise HTTPException(400, f"{what} is longer than 80 characters.")
    return s


def default_model_name(dataset_id: str, label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{dataset_id}.{label}").strip("-")[:100]


def version_out(v: dict[str, Any], full: bool = True) -> dict[str, Any]:
    res = v.get("result") or {}
    out = {k: v.get(k) for k in ("id", "model_name", "version", "run_id", "status", "stage", "approval", "created_at",
                                 "created_by", "options", "progress", "error", "path", "model_hash")}
    ver, src = res.get("version") or {}, res.get("source") or {}
    out["summary"] = {
        "best_model": ver.get("best_model"), "size_bytes": ver.get("size_bytes"), "n_models": ver.get("n_models"),
        "us_per_row_p50": (ver.get("latency") or {}).get("us_per_row_p50"),
        "us_per_row_p95": (ver.get("latency") or {}).get("us_per_row_p95"),
        "single_ms_p50": (ver.get("latency") or {}).get("single_ms_p50"),
        "holdout": ver.get("holdout"), "eval_metric": ver.get("eval_metric"), "problem_type": ver.get("problem_type"),
        "source_size_bytes": src.get("size_bytes"), "source_us_per_row_p50": (src.get("latency") or {}).get("us_per_row_p50"),
        "mlflow": res.get("mlflow"), "data_sha256": (res.get("hashes") or {}).get("data_sha256"),
    }
    out["result"] = res if full else None
    return out


# --------------------------------------------------------------------------- register


class RegisterRequest(BaseModel):
    created_by: str = Field(..., description="Who registers it (no sign-in; recorded in the audit log).")
    model_name: str | None = Field(None, description="Registered model name; default '<dataset>.<label>'.")
    description: str = ""
    refit_full: bool = Field(False, description="refit_full: retrain on train + validation data, one model per family.")
    infer_limit: float | None = Field(None, gt=0, description="Latency budget in seconds per row (AutoGluon's unit).")
    infer_limit_batch_size: int | None = Field(None, ge=1, le=100_000)
    clone_for_deployment: bool = Field(True, description="Keep only the served model and what it needs.")


@router.post("/runs/{run_id}/register", status_code=202)
async def register(run_id: str, body: RegisterRequest, request: Request) -> dict[str, Any]:
    """Register a finished run as a new version of a registered model. The version is built in a worker process
    (status 'building', then 'ready' or 'failed'); poll GET /api/registry/versions/{id} for its steps."""
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; only a finished run can be registered.")
    who = _name_ok(body.created_by, "created_by")
    reg = _reg(request)
    name = (body.model_name or default_model_name(rec["dataset_id"], rec["label"])).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
        raise HTTPException(400, "model_name: letters, digits, '.', '_' and '-' only (≤ 100 characters).")
    existing = reg.get_model(name)
    if existing and (existing["dataset_id"], existing["label"]) != (rec["dataset_id"], rec["label"]):
        raise HTTPException(409, f"Model '{name}' predicts {existing['label']} on {existing['dataset_id']}; "
                                 "a version must predict the same label on the same dataset.")
    reg.ensure_model(name, rec["dataset_id"], rec["label"], body.description)
    vid = "mv_" + secrets.token_hex(4)
    opts = body.model_dump(exclude={"created_by", "model_name"})
    v = reg.create_version({"id": vid, "model_name": name, "run_id": run_id, "created_by": who, "options": opts})
    fut = request.app.state.heavy.submit(build.build_version, str(request.app.state.settings.db_path), vid, str(_dir(request)))
    request.app.state.jobs[vid] = fut
    return version_out(v)


@router.get("/runs/{run_id}/latency")
async def latency_profile(run_id: str, request: Request, batch: int = Query(1000, ge=1, le=10_000)) -> dict[str, Any]:
    """Every model of the run that can predict: validation and holdout score, measured batch time per row on the
    holdout (µs, p50 of 3), and the models and disk space it needs. What refit_full / infer_limit /
    clone_for_deployment trade against. Measured in a worker process; cached per batch size."""
    rec = request.app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run with id '{run_id}'.")
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    cached = ((rec.get("result") or {}).get("latency_profile") or {}).get(str(batch))
    if cached:
        return {"run_id": run_id, **cached, "cached": True}
    out = await request.app.state.heavy.run(scoring_profile, rec["config"], batch)
    prof = dict((rec.get("result") or {}).get("latency_profile") or {})
    prof[str(batch)] = out
    request.app.state.runs.merge_result(run_id, latency_profile=prof)
    return {"run_id": run_id, **out, "cached": False}


def scoring_profile(cfg: dict[str, Any], batch: int) -> dict[str, Any]:
    """(Runs in a worker.) See latency_profile."""
    from ..engine import serving
    from ..engine.pool import load_predictor
    from ..engine.worker import _natural
    from ..splits import load_splits

    p = load_predictor(cfg["predictor_path"])
    h = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    X = h.drop(columns=[cfg["label"]])
    lb = p.leaderboard(h, silent=True).set_index("model")
    root = Path(cfg["predictor_path"]) / "models"
    rows = []
    for m in p.model_names(can_infer=True):
        deps = build._deps(p, m)
        size = sum(build.dir_size(root / d) for d in deps if (root / d).exists())
        t = build.measure_latency(p, X, model=m, batch_rows=min(batch, len(X)), repeats=3, singles=0, budget_s=4.0)
        sv, st = lb.loc[m, "score_val"], lb.loc[m, "score_test"]
        parent = p._trainer.get_model_attribute(m, "refit_full_parent", default=None)
        if (sv != sv) and parent in lb.index:
            sv = lb.loc[parent, "score_val"]
        rows.append({"model": m, "us_per_row": t["us_per_row_p50"], "us_per_row_all": t["us_per_row"],
                     "score_val_natural": None if sv != sv else _natural(p.eval_metric, float(sv)),
                     "score_test_natural": None if st != st else _natural(p.eval_metric, float(st)),
                     "n_models": len(deps), "size_bytes": size, "stack_level": int(lb.loc[m, "stack_level"]),
                     "refit_full": m.endswith("_FULL"), "best": m == p.model_best,
                     "ag_predict_1_time_us": (lambda v: None if v is None else v * 1e6)(p._trainer.get_model_attribute(m, "predict_1_time", default=None))})
    rows.sort(key=lambda r: r["us_per_row"])
    return {"batch_rows": min(batch, len(X)), "models": rows, "eval_metric": p.eval_metric.name,
            "greater_is_better": bool(p.eval_metric.greater_is_better_internal), "best_model": p.model_best,
            "total_size_bytes": build.dir_size(Path(cfg["predictor_path"])), "bagged": bool(p._trainer.bagged_mode)}


# --------------------------------------------------------------------------- read


@router.get("/registry")
def registry(request: Request) -> dict[str, Any]:
    reg = _reg(request)
    versions = [version_out(v, full=False) for v in reg.list_versions()]
    models = []
    for m in reg.list_models():
        vs = [v for v in versions if v["model_name"] == m["name"]]
        models.append({**m, "versions": len(vs), "latest": vs[0]["version"] if vs else None,
                       "production": next((v["version"] for v in vs if v["stage"] == "Production"), None),
                       "staging": next((v["version"] for v in vs if v["stage"] == "Staging"), None)})
    return {"models": models, "versions": versions, "counts": reg.counts()}


@router.get("/registry/summary")
def registry_summary(request: Request) -> dict[str, Any]:
    reg = _reg(request)
    return {"counts": reg.counts(), "audit": reg.audit_log(limit=8),
            "production": [version_out(v, full=False) for v in reg.list_versions() if v["stage"] == "Production"]}


def _version(request: Request, vid: str) -> dict[str, Any]:
    v = _reg(request).get_version(vid)
    if not v:
        raise HTTPException(404, f"No model version with id '{vid}'.")
    return v


def _ready(v: dict[str, Any]) -> None:
    if v["status"] != "ready":
        raise HTTPException(409, f"Version {v['model_name']} v{v['version']} is {v['status']}"
                                 + (f": {v['error']}" if v.get("error") else "; wait until it is ready."))


@router.get("/registry/versions")
def list_versions(request: Request, model: str | None = None) -> dict[str, Any]:
    return {"versions": [version_out(v, full=False) for v in _reg(request).list_versions(model)]}


@router.get("/registry/versions/{vid}")
def get_version(vid: str, request: Request) -> dict[str, Any]:
    return version_out(_version(request, vid))


@router.get("/registry/audit")
def audit(request: Request, model: str | None = None, version_id: str | None = None,
          limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    return {"entries": _reg(request).audit_log(model_name=model, version_id=version_id, limit=limit)}


@router.get("/registry/versions/{vid}/mlflow")
async def version_mlflow(vid: str, request: Request) -> dict[str, Any]:
    """MLflow's own record of the version: stage, aliases, tags, and the source run's params and metrics."""
    v = _version(request, vid)
    _ready(v)
    ml = (v.get("result") or {}).get("mlflow")
    if not ml:
        raise HTTPException(404, "This version has no MLflow record (registration in MLflow failed; see its steps).")
    out = await request.app.state.heavy.run(tracking.read_version, request.app.state.settings.data_dir, ml["name"], ml["version"])
    uri, _, _ = tracking.paths(request.app.state.settings.data_dir)
    return {**out, "tracking_uri": uri}


# --------------------------------------------------------------------------- stages and reviews


class TransitionRequest(BaseModel):
    stage: Literal["None", "Staging", "Production", "Archived"]
    actor: str
    comment: str = ""


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str
    comment: str = ""


async def _sync(request: Request, v: dict[str, Any], extra: dict[str, str] | None = None) -> dict[str, Any] | None:
    ml = (v.get("result") or {}).get("mlflow")
    if not ml:
        return None
    try:
        tags = {"approval": v["approval"], **(extra or {})}
        return await request.app.state.heavy.run(tracking.sync_version, request.app.state.settings.data_dir, ml["name"],
                                                 ml["version"], stage=v["stage"], tags=tags)
    except Exception as e:  # noqa: BLE001 — the registry's own record stays the source of truth
        return {"error": f"{type(e).__name__}: {e}"}


@router.post("/registry/versions/{vid}/transition")
async def transition(vid: str, body: TransitionRequest, request: Request) -> dict[str, Any]:
    """Move a version between stages. Production needs an approved review; promoting a version to Production
    archives the model's previous Production version. Every change is written to the audit log."""
    reg = _reg(request)
    v = _version(request, vid)
    _ready(v)
    actor = _name_ok(body.actor, "actor")
    if body.stage == v["stage"]:
        raise HTTPException(409, f"Version {v['version']} is already in {v['stage']}.")
    if body.stage == "Production" and v["approval"] != "approved":
        raise HTTPException(409, f"Version {v['version']} is {v['approval']}: Production needs an approved review first.")
    if v["approval"] == "rejected" and body.stage in ("Staging", "Production"):
        raise HTTPException(409, f"Version {v['version']} was rejected; it can only be archived or left in None.")
    changed = []
    if body.stage == "Production":
        for o in reg.list_versions(v["model_name"]):
            if o["id"] != vid and o["stage"] == "Production":
                reg.update_version(o["id"], stage="Archived")
                o2 = reg.get_version(o["id"])
                reg.audit(actor=actor, action="stage_changed", version=o2, from_stage="Production", to_stage="Archived",
                          comment=f"superseded by v{v['version']}")
                changed.append(o2)
    reg.update_version(vid, stage=body.stage)
    v2 = reg.get_version(vid)
    entry = reg.audit(actor=actor, action="stage_changed", version=v2, from_stage=v["stage"], to_stage=body.stage,
                      comment=body.comment)
    for o in changed:
        await _sync(request, o)
    ml = await _sync(request, v2, {"stage_changed_by": actor})
    return {"version": version_out(v2), "audit": entry, "archived": [o["id"] for o in changed], "mlflow": ml}


@router.post("/registry/versions/{vid}/review")
async def review(vid: str, body: ReviewRequest, request: Request) -> dict[str, Any]:
    """Approve or reject a version, with the reviewer's name and comment. Four eyes: the reviewer cannot be the
    person who registered it. Rejecting needs a comment, and archives the version if it was in Staging or
    Production."""
    reg = _reg(request)
    v = _version(request, vid)
    _ready(v)
    who = _name_ok(body.reviewer, "reviewer")
    if v["approval"] != "pending":
        raise HTTPException(409, f"Version {v['version']} was already {v['approval']}; register the run again for a new review.")
    if who.casefold() == (v["created_by"] or "").strip().casefold():
        raise HTTPException(409, f"Four-eyes rule: {who} registered this version, so someone else must review it.")
    if body.decision == "reject" and not body.comment.strip():
        raise HTTPException(400, "A rejection needs a comment saying why.")
    approval = "approved" if body.decision == "approve" else "rejected"
    stage = v["stage"]
    new_stage = "Archived" if approval == "rejected" and stage in ("Staging", "Production") else stage
    reg.update_version(vid, approval=approval, stage=new_stage)
    v2 = reg.get_version(vid)
    entry = reg.audit(actor=who, action=approval, version=v2, from_stage=stage if new_stage != stage else None,
                      to_stage=new_stage if new_stage != stage else None, comment=body.comment)
    ml = await _sync(request, v2, {"reviewer": who, "review_comment": body.comment[:500]})
    return {"version": version_out(v2), "audit": entry, "mlflow": ml}


# --------------------------------------------------------------------------- the model card


def _card(request: Request, vid: str) -> dict[str, Any]:
    v = _version(request, vid)
    _ready(v)
    reg = _reg(request)
    run = request.app.state.runs.get(v["run_id"])
    ds = request.app.state.db.get_dataset(run["dataset_id"])
    return card.build_card(v, run, ds, list(reversed(reg.audit_log(version_id=vid))), reg.list_batches(vid))


@router.get("/registry/versions/{vid}/card")
async def model_card(vid: str, request: Request, format: Literal["json", "md", "html"] = "json", download: bool = False):
    c = await run_in_threadpool(_card, request, vid)
    fname = f"model-card_{c['model_name']}_v{c['version']}"
    if format == "md":
        return PlainTextResponse(card.to_markdown(c), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}.md"'} if download else None)
    if format == "html":
        return HTMLResponse(card.to_html(c), headers={"Content-Disposition": f'attachment; filename="{fname}.html"'} if download else None)
    return c


class CardFields(BaseModel):
    actor: str
    fields: dict[str, str]


@router.put("/registry/versions/{vid}/card")
def edit_card(vid: str, body: CardFields, request: Request) -> dict[str, Any]:
    """Fill in the card's template (intended use, limitations, owner, …). Recorded in the audit log."""
    reg = _reg(request)
    v = _version(request, vid)
    who = _name_ok(body.actor, "actor")
    allowed = {k for k, _, _ in card.TEMPLATE_FIELDS}
    bad = [k for k in body.fields if k not in allowed]
    if bad:
        raise HTTPException(400, f"Unknown card field(s): {', '.join(bad)}. Fields: {', '.join(sorted(allowed))}.")
    cur = dict(v.get("card") or {})
    changed = [k for k, val in body.fields.items() if cur.get(k, "") != val]
    cur.update({k: val[:4000] for k, val in body.fields.items()})
    reg.update_version(vid, card=cur)
    if changed:
        reg.audit(actor=who, action="card_edited", version=v, comment=", ".join(changed))
    return {"card": cur, "changed": changed}


# --------------------------------------------------------------------------- Try It and batches


class TryRequest(BaseModel):
    row: dict[str, Any]
    explain: bool = True
    target: str | None = None


@router.post("/registry/versions/{vid}/predict")
async def try_it(vid: str, body: TryRequest, request: Request) -> dict[str, Any]:
    """One row through the version's predictor: the prediction, the class probabilities and (explain) Kernel SHAP
    for that row, f(x) = φ₀ + Σ φᵢ, with the top reasons. Computed in a worker process."""
    v = _version(request, vid)
    _ready(v)
    run = request.app.state.runs.get(v["run_id"])
    try:
        out = await request.app.state.heavy.run(scoring.try_it, run["config"], v["path"], body.row, body.target, body.explain)
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], **out}


def _kinds(request: Request, run: dict[str, Any]) -> dict[str, str]:
    ds = request.app.state.db.get_dataset(run["dataset_id"])
    types = {c["name"]: c["type"] for c in (ds or {}).get("profile", {}).get("columns", [])}
    types.update(run["config"].get("column_types") or {})
    return {c: ("number" if t == "number" else "text" if t == "text" else "category") for c, t in types.items()}


@router.post("/registry/versions/{vid}/batches", status_code=202)
async def create_batch(vid: str, request: Request, file: UploadFile = File(...), created_by: str = Form("")) -> dict[str, Any]:
    """Score a CSV with the version, in a worker process: progress on GET /registry/batches/{id}, then a preview,
    the scored CSV (predictions and probabilities) and PSI drift of every feature and of the predictions."""
    v = _version(request, vid)
    _ready(v)
    who = _name_ok(created_by, "created_by")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not content.strip():
        raise HTTPException(400, "The file is empty.")
    bid = "batch_" + secrets.token_hex(4)
    bdir = _dir(request) / "batches" / bid
    bdir.mkdir(parents=True)
    name = Path(file.filename or "batch.csv").name
    csv_path = bdir / "input.csv"
    csv_path.write_bytes(content)
    reg = _reg(request)
    reg.create_batch({"id": bid, "version_id": vid, "filename": name, "created_by": who})
    run = request.app.state.runs.get(v["run_id"])
    fut = request.app.state.heavy.submit(scoring.score_batch, str(request.app.state.settings.db_path), bid, v["path"],
                                         run["config"], str(csv_path), str(bdir), _kinds(request, run), {"id": vid})
    request.app.state.jobs[bid] = fut
    return reg.get_batch(bid)


@router.get("/registry/versions/{vid}/batches")
def list_batches(vid: str, request: Request) -> dict[str, Any]:
    _version(request, vid)
    out = []
    for b in _reg(request).list_batches(vid):
        r = b.get("result") or {}
        out.append({**b, "result": None, "summary": r.get("summary"),
                    "drift_counts": (r.get("drift") or {}).get("counts"), "max_psi": (r.get("drift") or {}).get("max_psi"),
                    "prediction_psi": ((r.get("drift") or {}).get("prediction") or {}).get("psi")})
    return {"batches": out}


@router.get("/registry/batches/{bid}")
def get_batch(bid: str, request: Request) -> dict[str, Any]:
    b = _reg(request).get_batch(bid)
    if not b:
        raise HTTPException(404, f"No batch with id '{bid}'.")
    return b


@router.get("/registry/batches/{bid}/download")
def download_batch(bid: str, request: Request):
    b = _reg(request).get_batch(bid)
    if not b:
        raise HTTPException(404, f"No batch with id '{bid}'.")
    if b["status"] != "succeeded":
        raise HTTPException(409, f"Batch {bid} is {b['status']}.")
    path = _dir(request) / "batches" / bid / "scored.csv"
    stem = Path(b["filename"]).stem
    return FileResponse(path, media_type="text/csv", filename=f"{stem}_scored_{bid}.csv")

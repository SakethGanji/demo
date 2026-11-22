"""Routes for the model registry (S5): register a run as a version, stages and reviews with an audit log, the
model card, Try It, batch scoring with drift, and MLflow's view of a version."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from ..authz import path, query, require, variant, with_team  # P19
from pydantic import BaseModel, Field

from .. import config, frames
from ..engine import explain
from ..http import batch_or_404, run_or_404, version_or_404
from . import build, card, scoring, tracking
from .store import RegistryStore

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
    if not identity_key(s):
        raise HTTPException(400, f"{what} needs at least one letter or digit (four eyes compares letters and digits only).")
    return s


def identity_key(s: str | None) -> str:
    """A name as four-eyes compares it (catalog.governance.identity_key): letters and digits only, accents, full-width
    forms and Cyrillic/Greek lookalikes folded. Names, not authenticated identities: there is no sign-in."""
    from ..catalog.governance import identity_key as _k

    return _k(s)


def default_model_name(dataset_id: str, label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{dataset_id}.{label}").strip("-")[:100]


def version_out(v: dict[str, Any], full: bool = True) -> dict[str, Any]:
    res = v.get("result") or {}
    out = {k: v.get(k) for k in ("id", "model_name", "version", "run_id", "status", "stage", "approval", "created_at",
                                 "created_by", "options", "progress", "error", "path", "model_hash", "retired_at", "retired_by")}
    ver, src = res.get("version") or {}, res.get("source") or {}
    out["summary"] = {
        "best_model": ver.get("best_model"), "size_bytes": ver.get("size_bytes"), "n_models": ver.get("n_models"),
        "us_per_row_p50": (ver.get("latency") or {}).get("us_per_row_p50"),
        "us_per_row_p95": (ver.get("latency") or {}).get("us_per_row_p95"),
        "single_ms_p50": (ver.get("latency") or {}).get("single_ms_p50"),
        "single_ms_p95": (ver.get("latency") or {}).get("single_ms_p95"),
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
    serve_model: str | None = Field(None, description="Serve this model of the run (set_model_best), e.g. a faster one "
                                    "with a measured one-row latency; after refit_full its _FULL copy is served.")
    serve_fastest: bool = Field(False, description="Serve the single model with the lowest measured one-row p95 "
                                "(as served: pipeline pruned, persisted), whatever its score.")
    distill: bool = Field(False, description="predictor.distill(): train single student models on the ensemble's "
                          "soft labels (augmented data); the best student is served unless another rule picks.")
    distill_time_limit: float = Field(120, gt=0, le=3600)
    distill_hyperparameters: dict[str, Any] | None = Field(None, description="Student families, as fit()'s "
                                                           "hyperparameters; default {'GBM': {}}.")
    prune_pipeline: bool = Field(True, description="With clone_for_deployment: prune AutoGluon's feature pipeline "
                                 "to the features the served model reads (predictions checked unchanged).")
    decision_threshold: float | None = Field(None, gt=0, lt=1, description="Binary: serve this decision threshold (D4 "
                                             "post-deployment change requests: a threshold move or a recalibration).")
    calibration: dict[str, float] | None = Field(None, description="Binary: serve Platt-recalibrated probabilities "
                                                 "σ(a · logit p + b), {a > 0, b} (D4 recalibration); decision_threshold "
                                                 "is then on the calibrated scale.")
    change_id: str | None = Field(None, description="The D4 change request this version carries out.")
    change_by: str | None = Field(None, description="Who designed the D4 change experiment; four eyes: they cannot "
                                  "approve the version either.")


@router.post("/runs/{run_id}/register", status_code=202, dependencies=[require("model.register", path("run_id", "run"))])
async def register(run_id: str, body: RegisterRequest, request: Request) -> dict[str, Any]:
    """Register a finished run as a new version of a registered model. The version is built in a worker process
    (status 'building', then 'ready' or 'failed'); poll GET /api/registry/versions/{id} for its steps."""
    rec = run_or_404(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; only a finished run can be registered.")
    if rec["config"].get("kind") == "reject_inference":
        raise HTTPException(400, "A reject-inference run is a comparison of methods, not one model to serve: train the "
                                 "chosen method as a yes/no model and register that.")
    if body.distill and rec["config"].get("monotone"):
        raise HTTPException(400, "This run has monotone constraints; distill would serve an unconstrained student. "
                                 "Register without distill.")
    if body.distill and (rec.get("result") or {}).get("problem_type") == "quantile":
        raise HTTPException(400, "This is a quantile run; distill trains students on one prediction per row, not one "
                                 "per quantile level. Register without distill.")
    if body.calibration is not None:
        a, b = body.calibration.get("a"), body.calibration.get("b")
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) and a > 0):
            raise HTTPException(400, "calibration is {a, b}: Platt p′ = σ(a · logit p + b), finite, with a > 0 (ranking kept).")
        if (rec.get("result") or {}).get("problem_type") not in (None, "binary"):
            raise HTTPException(400, "calibration applies to binary (yes/no) runs only.")
    who = _name_ok(body.created_by, "created_by")
    if rec["config"].get("join"):  # P15: serving parity at registration (§7 risk 11): a mismatch is refused now
        par = await run_in_threadpool(build.register_gate, str(request.app.state.settings.db_path), run_id, rec["config"], _dir(request), who)
        if par["status"] != "ok":
            raise HTTPException(409, {"code": "parity_mismatch", "message": f"Serving parity failed for run {run_id}: "
                                      f"{par['summary']}. The model was not registered.", "parity": par})
    reg = _reg(request)
    name = (body.model_name or default_model_name(rec["dataset_id"], rec["label"])).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
        raise HTTPException(400, "model_name: letters, digits, '.', '_' and '-' only (≤ 100 characters).")
    existing = reg.get_model(name)
    lin = request.app.state.datasets.lineage(rec["dataset_id"]) or {}
    derived = existing and existing["dataset_id"] in (lin.get("parent"), lin.get("root")) and rec["label"] == existing["label"]
    if existing and (existing["dataset_id"], existing["label"]) != (rec["dataset_id"], rec["label"]) and not derived:
        raise HTTPException(409, f"Model '{name}' predicts {existing['label']} on {existing['dataset_id']}; "
                                 "a version must predict the same label on the same dataset (or a version of it: a fix, features, a monitoring window).")
    reg.ensure_model(name, rec["dataset_id"], rec["label"], body.description)
    vid = "mv_" + secrets.token_hex(4)
    opts = body.model_dump(exclude={"created_by", "model_name"})
    v = reg.create_version({"id": vid, "model_name": name, "run_id": run_id, "created_by": who, "options": opts})
    heavy = request.app.state.heavy
    fut = heavy.submit(build.build_version, str(request.app.state.settings.db_path), vid, str(_dir(request)))
    request.app.state.jobs[vid] = fut

    def _warm_after(f, reg=reg, vid=vid, heavy=heavy) -> None:
        # Warm the new version in every predict worker, so its first Try It or /score call is not a cold one.
        try:
            got = reg.get_version(vid)
            if got and got["status"] == "ready" and got.get("path") and (rec["config"].get("kind") not in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel")):
                heavy.warm_path(got["path"])
                if _inproc_ok(got, 1):  # its own thread: this callback runs in the pool's management thread
                    import threading

                    threading.Thread(target=lambda: _SERVE.get(got["path"]), daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    fut.add_done_callback(_warm_after)
    return version_out(v)


@router.get("/runs/{run_id}/latency")
async def latency_profile(run_id: str, request: Request, batch: int = Query(1000, ge=1, le=10_000)) -> dict[str, Any]:
    """Every model of the run that can predict: validation and holdout score, measured batch time per row on the
    holdout (µs, p50 of 3), one-row latency as the run holds it and **as it would be served** (feature pipeline
    pruned to what the model reads, models persisted: `served_ms_p50/p95`), and the models and disk space it needs.
    `frontier` lists the models no other model beats on both served one-row p95 and validation score. What
    refit_full / infer_limit / distillation / clone_for_deployment trade against. Measured in a worker process;
    cached per batch size."""
    rec = run_or_404(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}.")
    cached = ((rec.get("result") or {}).get("latency_profile") or {}).get(str(batch))
    if cached and all("served_ms_p95" in m for m in cached.get("models", [])):
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
    h = serving.holdout_frame(frames.resolve(cfg), load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
    X = h.drop(columns=[cfg["label"]])
    lb = p.leaderboard(h, silent=True).set_index("model")
    root = Path(cfg["predictor_path"]) / "models"
    from . import fastpath

    rows = []
    for m in p.model_names(can_infer=True):
        deps = build._deps(p, m)
        size = sum(build.dir_size(root / d) for d in deps if (root / d).exists())
        t = build.measure_latency(p, X, model=m, batch_rows=min(batch, len(X)), repeats=3, singles=25, budget_s=2.5)
        with fastpath.pruned(p, m) as pinfo:
            served = fastpath.one_row(fastpath.predict_fn(p, m), X, n=40, budget_s=2.5) if pinfo["pruned"] else None
        served = served or {"ms_p50": t["single_ms_p50"], "ms_p95": t["single_ms_p95"], "calls": t["single_calls"]}
        sv, st = lb.loc[m, "score_val"], lb.loc[m, "score_test"]
        parent = p._trainer.get_model_attribute(m, "refit_full_parent", default=None)
        if (sv != sv) and parent in lb.index:
            sv = lb.loc[parent, "score_val"]
        rows.append({"model": m, "us_per_row": t["us_per_row_p50"], "us_per_row_all": t["us_per_row"],
                     "single_ms_p50": t["single_ms_p50"], "single_ms_p95": t["single_ms_p95"], "single_calls": t["single_calls"],
                     "score_val_natural": None if sv != sv else _natural(p.eval_metric, float(sv)),
                     "score_test_natural": None if st != st else _natural(p.eval_metric, float(st)),
                     "served_ms_p50": served["ms_p50"], "served_ms_p95": served["ms_p95"], "served_calls": served["calls"],
                     "pipeline": {"pruned": pinfo["pruned"], "features_in": pinfo["features_in"],
                                  "n_features_out": pinfo["n_features_out"], "n_features_out_before": pinfo.get("n_features_out_before"),
                                  "generators": pinfo.get("generators"), "generators_before": pinfo.get("generators_before")},
                     "n_models": len(deps), "size_bytes": size, "stack_level": int(lb.loc[m, "stack_level"]),
                     "refit_full": m.endswith("_FULL"), "best": m == p.model_best,
                     "ag_predict_1_time_us": (lambda v: None if v is None else v * 1e6)(p._trainer.get_model_attribute(m, "predict_1_time", default=None))})
    rows.sort(key=lambda r: r["us_per_row"])
    nat_greater = bool(p.eval_metric.greater_is_better)
    for r in rows:
        r["score_rank"] = r["score_val_natural"] if r["score_val_natural"] is not None else r["score_test_natural"]
    frontier = fastpath.pareto(rows, "served_ms_p95", "score_rank", nat_greater)
    for r in rows:
        r["on_frontier"] = r["model"] in frontier
    return {"batch_rows": min(batch, len(X)), "models": rows, "eval_metric": p.eval_metric.name,
            "frontier": frontier, "natural_greater_is_better": nat_greater, "cpus": os.cpu_count(),
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
    return with_team(request, {"models": models, "versions": versions, "counts": reg.counts()}, "registry")


@router.get("/registry/summary")
def registry_summary(request: Request) -> dict[str, Any]:
    reg = _reg(request)
    return with_team(request, {"counts": reg.counts(), "audit": reg.audit_log(limit=8),
                               "production": [version_out(v, full=False) for v in reg.list_versions() if v["stage"] == "Production"]},
                     "registry_summary")


def _version(request: Request, vid: str) -> dict[str, Any]:
    return version_or_404(request, vid, "model version")


def _ready_version(request: Request, vid: str) -> dict[str, Any]:
    v = _version(request, vid)
    if str(v.get("error") or "").startswith("purged_model"):  # F-X4c: a purge removed the model (trained with purged values)
        raise HTTPException(409, {"code": "purged_model", "message": v["error"]})
    if v["status"] != "ready":
        raise HTTPException(409, f"Version {v['model_name']} v{v['version']} is {v['status']}"
                                 + (f": {v['error']}" if v.get("error") else "; wait until it is ready."))
    return v


@router.get("/registry/versions")
def list_versions(request: Request, model: str | None = None) -> dict[str, Any]:
    return with_team(request, {"versions": [version_out(v, full=False) for v in _reg(request).list_versions(model)]},
                     "registry_versions")


@router.get("/registry/versions/{vid}")
def get_version(vid: str, request: Request) -> dict[str, Any]:
    return version_out(_version(request, vid))


@router.get("/registry/audit", dependencies=[require("model.read", query("version_id", "model_version"), query("model", "model"))])
def audit(request: Request, model: str | None = None, version_id: str | None = None,
          limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    return {"entries": _reg(request).audit_log(model_name=model, version_id=version_id, limit=limit)}


@router.get("/registry/versions/{vid}/mlflow")
async def version_mlflow(vid: str, request: Request) -> dict[str, Any]:
    """MLflow's own record of the version: stage, aliases, tags, and the source run's params and metrics."""
    v = _ready_version(request, vid)
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
    override_reason: str = ""  # Production with an open critical Doctor finding: why it goes ahead anyway (audited)


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


def _champion_of(request: Request, vid: str) -> dict[str, Any] | None:
    with request.app.state.db.connect() as con:
        r = con.execute("SELECT id, status, project_id FROM champions WHERE version_id = ? AND status IN ('approved', 'pending')"
                        " ORDER BY status = 'approved' DESC LIMIT 1", (vid,)).fetchone()
    return dict(r) if r else None


def _refusal_audited(action: str):
    """DL2: a governance route's 403 / 409 is written to the audit log as `<action>_refused`."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        async def inner(**kw):
            try:
                return await fn(**kw)
            except HTTPException as e:
                if e.status_code in (403, 409):
                    from ..catalog import governance as G

                    body, request = kw.get("body"), kw["request"]
                    who = getattr(body, "actor", None) or getattr(body, "reviewer", None) or (body.get("by") if isinstance(body, dict) else None)
                    G.refused(request.app.state.db, who, action, e.status_code, e.detail,
                              {k: v for k, v in kw.items() if k in ("vid", "rid")})
                raise
        return inner
    return deco


@router.post("/registry/versions/{vid}/transition", dependencies=[require("model.promote", path("vid", "model_version"), when=variant("stage", {"Production": "model.promote_production"}))])
@_refusal_audited("stage_change")
async def transition(vid: str, body: TransitionRequest, request: Request) -> dict[str, Any]:
    """Move a version between stages. Production needs an approved review; promoting a version to Production
    archives the model's previous Production version. Every change is written to the audit log. DL2: the current
    champion (approved or requested) can't be archived, and a retired version stays Archived."""
    reg = _reg(request)
    v = _ready_version(request, vid)
    actor = _name_ok(body.actor, "actor")
    if body.stage == v["stage"]:
        raise HTTPException(409, f"Version {v['version']} is already in {v['stage']}.")
    if v.get("retired_at"):
        raise HTTPException(409, f"Version {v['version']} was retired ({str(v['retired_at'])[:10]}, {v.get('retired_by')}): it stays Archived.")
    ch = _champion_of(request, vid) if body.stage == "Archived" else None
    if ch:
        raise HTTPException(409, f"Version {v['version']} is the {'requested ' if ch['status'] == 'pending' else ''}champion of project "
                                 f"{ch['project_id']} ({ch['id']}): approve another champion first (POST /api/projects/"
                                 f"{ch['project_id']}/champion), or reject the request.")
    if body.stage == "Production" and v["approval"] != "approved":
        raise HTTPException(409, f"Version {v['version']} is {v['approval']}: Production needs an approved review first.")
    if v["approval"] == "rejected" and body.stage in ("Staging", "Production"):
        raise HTTPException(409, f"Version {v['version']} was rejected; it can only be archived or left in None.")
    gate: dict[str, Any] = {}
    if body.stage == "Production":
        from ..catalog import governance as G

        # Policy (documented in docs/progress/F-P2.md): whoever registered a version does not promote it themselves,
        # unless ENSEMBLE_ALLOW_SELF_PROMOTE is on; the audit marks a self-promotion either way.
        self_promo = identity_key(actor) == identity_key(v["created_by"])
        if self_promo and not G.allow_self_promote():
            raise HTTPException(409, f"Four-eyes rule: {actor} registered this version ({v['created_by']}); someone else "
                                     "moves it to Production (policy ENSEMBLE_ALLOW_SELF_PROMOTE is off).")
        try:
            gate = G.leak_gate(request.app, v["run_id"], body.override_reason, "Production")
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        from ..catalog import certify as CT

        try:  # P21: team policy require_certified_inputs (every input version certified, at this moment)
            certs = CT.inputs_gate(request.app.state.db, v["run_id"])
        except CT.CertError as e:
            G.refused(request.app.state.db, actor, "production_inputs", 409, e.message, {"version_id": vid, "run_id": v["run_id"]})
            raise e.http() from e
        gate = {**gate, "self_promotion": self_promo, "registered_by": v["created_by"],
                **({"input_certifications": certs} if certs else {})}
        if certs:  # kept on the version: a later expiry does not rewrite what Production was checked against
            from ..catalog.schema import now as _now

            reg.merge_version_result(vid, input_certifications={**certs, "checked_at": _now()})
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
                      comment=body.comment, **({"details": gate} if gate else {}))
    for o in changed:
        await _sync(request, o)
    ml = await _sync(request, v2, {"stage_changed_by": actor})
    if body.stage == "Production" and v2.get("path"):
        warm_version(request, v2)  # F3: Production is served warm
    return {"version": version_out(v2), "audit": entry, "archived": [o["id"] for o in changed], "mlflow": ml}


@router.post("/registry/versions/{vid}/review", dependencies=[require("model.review", path("vid", "model_version"))])
@_refusal_audited("review")
async def review(vid: str, body: ReviewRequest, request: Request) -> dict[str, Any]:
    """Approve or reject a version, with the reviewer's name and comment. Four eyes: the reviewer cannot be the
    person who registered it, nor (a D4 change) the author of its experiment; names are compared on letters and digits
    only, with accents and lookalike letters folded (identity_key); names, not authenticated identities. Rejecting needs a comment, and archives the version if it was in Staging or
    Production."""
    reg = _reg(request)
    v = _ready_version(request, vid)
    who = _name_ok(body.reviewer, "reviewer")
    if v["approval"] != "pending":
        raise HTTPException(409, f"Version {v['version']} was already {v['approval']}; register the run again for a new review.")
    # Four eyes (C §1.5, P21: through policy.four_eyes): the reviewer (and the request's principal) is not the registrant,
    # the change experiment's author, nor the run's creator; names fold punctuation, accents and lookalike letters.
    from ..authz.policy import four_eyes

    run = request.app.state.runs.get(v["run_id"]) if v.get("run_id") else None
    author = (v.get("options") or {}).get("change_by")
    msg = four_eyes("model.review", {"registered_by": v["created_by"], "change_by": author,
                                     "run_created_by": ((run or {}).get("config") or {}).get("by")}, who)
    if msg:
        raise HTTPException(409, f"Four-eyes rule: {msg.removeprefix('Four eyes: ')} ({who} cannot review this version.)")
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


# --------------------------------------------------------------------------- DL2: retire (four eyes)

def _vreq(request: Request, rid: str) -> dict[str, Any]:
    with request.app.state.db.connect() as con:
        r = con.execute("SELECT * FROM version_requests WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, f"No version request '{rid}'.")
    return dict(r)


def _retire_check(request: Request, v: dict[str, Any]) -> None:
    name = f"{v['model_name']} v{v['version']}"
    if v.get("retired_at"):
        raise HTTPException(409, f"{name} is already retired.")
    if v["approval"] == "rejected":
        raise HTTPException(409, f"{name} was rejected: it does not block its data, there is nothing to retire.")
    ch = _champion_of(request, v["id"])
    if ch:
        raise HTTPException(409, f"{name} is the {'requested ' if ch['status'] == 'pending' else ''}champion of project {ch['project_id']} "
                                 f"({ch['id']}): approve another champion first (POST /api/projects/{ch['project_id']}/champion).")
    if v.get("stage") == "Production":
        raise HTTPException(409, f"{name} is in Production: promote its successor or move it out of Production first.")


@router.post("/registry/versions/{vid}/retire", status_code=201, dependencies=[require("model.retire", path("vid", "model_version"))])
@_refusal_audited("retire")
async def request_retire(vid: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """{by, reason}: ask to retire a registered version (end of life: it is Archived for good, and its training data may
    then be archived, and deleted after the retention period). Someone else approves (POST
    /api/registry/version-requests/{id}/decide); an admin (ENSEMBLE_ADMINS) retires at once. Refused while it is the
    champion (approved or requested) or in Production. Audited."""
    import secrets

    from ..catalog import governance as G
    from ..catalog.api import _text, _who
    from ..catalog.schema import now

    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    reason = _text(body, "reason", 5, 500, True)
    v = _version(request, vid)
    _retire_check(request, v)
    with request.app.state.db.connect() as con:
        if con.execute("SELECT 1 FROM version_requests WHERE version_id = ? AND status = 'pending'", (vid,)).fetchone():
            raise HTTPException(409, f"A retirement of {v['model_name']} v{v['version']} is already waiting for approval.")
        rid = "vrq_" + secrets.token_hex(4)
        con.execute("INSERT INTO version_requests (id, version_id, action, status, requested_by, requested_at, reason)"
                    " VALUES (?, ?, 'retire', 'pending', ?, ?, ?)", (rid, vid, who, now(), reason))
    _reg(request).audit(actor=who, action="retire_requested", version=v, comment=reason,
                        details={"request_id": rid, "needs": "approval by someone other than " + who + " (or an admin)"})
    if G.is_admin(who) and G.allow_admin_self_approve():  # DL3 (m1): only when the policy allows it, and said so in the audit
        return await _decide_retire(request, rid, True, who, G.SELF_APPROVED, admin_self=True)
    return {**_vreq(request, rid), **({"note": "you are an admin, but a retirement still needs a second person "
                                               "(ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE is off)"} if G.is_admin(who) else {})}


@router.post("/registry/version-requests/{rid}/decide", dependencies=[require("model.retire_decide", path("rid", "version_request"))])
@_refusal_audited("retire_decide")
async def decide_retire(rid: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """{approve, by, comment}: four eyes. The requester may withdraw (approve=false) but not approve."""
    from ..catalog.api import _who

    body = body if isinstance(body, dict) else {}
    who = _who(body.get("by"))
    approve = body.get("approve")
    if not isinstance(approve, bool):
        raise HTTPException(400, 'Send {"approve": true} or {"approve": false}.')
    return await _decide_retire(request, rid, approve, who, str(body.get("comment") or "")[:500])


async def _decide_retire(request: Request, rid: str, approve: bool, who: str, comment: str, admin_self: bool = False) -> dict[str, Any]:
    from ..catalog import governance as G
    from ..catalog.schema import now

    req = _vreq(request, rid)
    if req["status"] != "pending":
        raise HTTPException(409, f"This request is {req['status']}.")
    reg = _reg(request)
    v = _version(request, req["version_id"])
    from ..authz.policy import four_eyes

    mine = four_eyes("model.retire_decide", req, who) is not None  # C §1.5 (P21): one check for every decide path
    self_admin = mine and approve and G.is_admin(who) and G.allow_admin_self_approve()
    if mine and approve and not self_admin:
        raise HTTPException(403, f"Four eyes: {req['requested_by']} asked to retire this version, so someone else must approve, "
                                 "admins included (ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE is off; names are compared on letters and "
                                 "digits only; names, not authenticated identities).")
    if self_admin:
        comment = G.SELF_APPROVED + (f": {comment}" if comment and comment != G.SELF_APPROVED else "")
    status = "approved" if approve else ("withdrawn" if mine else "rejected")
    if approve:
        _retire_check(request, v)  # re-checked: it may have become champion since
    with request.app.state.db.connect() as con:
        con.execute("UPDATE version_requests SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    (status, who, now(), comment, rid))
    if not approve:
        reg.audit(actor=who, action=f"retire_{status}", version=v, comment=comment, details={"request_id": rid, "requested_by": req["requested_by"]})
        return _vreq(request, rid)
    stage = v["stage"]
    with request.app.state.db.connect() as con:
        con.execute("UPDATE model_versions SET retired_at = ?, retired_by = ?, stage = 'Archived' WHERE id = ?", (now(), who, v["id"]))
    v2 = reg.get_version(v["id"])
    reg.audit(actor=who, action="retired", version=v2, from_stage=stage if stage != "Archived" else None,
              to_stage="Archived" if stage != "Archived" else None, comment=comment or req["reason"],
              details={"request_id": rid, "requested_by": req["requested_by"], "approved_by": who, "admin": G.is_admin(who),
                       "reason": req["reason"], "self_approved_by_admin": self_admin})
    await _sync(request, v2, {"retired_by": who})
    return {**_vreq(request, rid), "version": version_out(v2, full=False), "self_approved_by_admin": self_admin}


@router.get("/registry/versions/{vid}/retire")
def retire_state(vid: str, request: Request) -> dict[str, Any]:
    """The version's retirement requests, newest first."""
    _version(request, vid)
    with request.app.state.db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM version_requests WHERE version_id = ? ORDER BY requested_at DESC", (vid,))]
    return {"version_id": vid, "requests": rows}


# --------------------------------------------------------------------------- the model card


def _card(request: Request, vid: str) -> dict[str, Any]:
    v = _ready_version(request, vid)
    reg = _reg(request)
    run = request.app.state.runs.get(v["run_id"])
    ds = request.app.state.db.get_dataset(run["dataset_id"])
    return card.build_card(v, run, ds, list(reversed(reg.audit_log(version_id=vid))), reg.list_batches(vid),
                           reasons=_reasons_for_card(request, v, run), recourse=_recourse_for_card(request, v, run),
                           credit_report=_credit_for_card(v, run), reject_inference=_ri_for_card(request, run))


def _ri_for_card(request: Request, run: dict[str, Any]) -> dict[str, Any] | None:
    """G7: a yes/no tabular model of a table with declined applicants: the latest reject-inference check on that table."""
    if run["config"].get("kind") or (run.get("result") or {}).get("problem_type") != "binary":
        return None
    from ..reject.api import card_section

    same = request.app.state.runs.list(dataset_id=run["dataset_id"])  # P1: filtered in SQL
    return card_section(sorted(same, key=lambda r: r.get("created_at") or "", reverse=True))


def _credit_for_card(v: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """G2: the card's link to the credit-risk report and its last headline (no recompute)."""
    if not _is_binary(v, run):
        return None
    from .credit_report_api import card_section

    return card_section(v)


def _recourse_for_card(request: Request, v: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """G1: the card's counterfactual section: the user's overrides and the stored acceptance check (no worker call)."""
    if _reason_args(request, v, run) is None or run["config"].get("kind") == "segmented":
        return None
    from ..engine import recourse as rcs

    saved = _recourse_saved(request, v)
    cp = _recourse_check_path(v)
    return {"method": rcs.METHOD, "overrides": saved.get("actions") or {}, "updated_by": saved.get("updated_by"),
            "check": json.loads(cp.read_text()) if cp.exists() else None}


def _reasons_for_card(request: Request, v: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    """F10: the card's reason-code section from the saved mapping (defaults filled), with no worker call."""

    args = _reason_args(request, v, run)
    if args is None:
        return None
    from ..engine import reasons as rc

    ver = (v.get("result") or {}).get("version") or {}
    features = ver.get("features") or []
    cons = {k: int(x) for k, x in (((run["config"].get("monotone") or {}).get("constraints")) or {}).items()}
    conf = args[0]
    pos = str(ver.get("positive_class"))
    sp = _stability_path(v)
    return {"adverse_class": str(conf.get("adverse_class") or pos), "positive_class": pos,
            "threshold": ver.get("decision_threshold"), "top_k": int(conf.get("top_k") or rc.TOP_K),
            "mapping": rc.merge_mapping(conf.get("mapping"), rc.default_mapping(features, args[1], cons)),
            "constraints": cons, "stability": json.loads(sp.read_text()) if sp.exists() else None,
            "reference": str(conf.get("reference") or "approved")}


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


@router.get("/registry/versions/{vid}/inputs.zip", dependencies=[require("model.read", path("vid", "model_version"))])
async def inputs_zip(vid: str, request: Request):
    """P15 (C §4.6): the manifest (input record), join spec, contract results and hashes of the version. No rows."""
    from fastapi.responses import Response

    v = _version(request, vid)
    data = await run_in_threadpool(card.inputs_zip, v, request.app.state.runs.get(v["run_id"]))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="inputs_{v["model_name"]}_v{v["version"]}.zip"'})


@router.put("/registry/versions/{vid}/card", dependencies=[require("model.card", path("vid", "model_version"))])
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


@router.get("/registry/versions/{vid}/monotone")
async def version_monotone(vid: str, request: Request, constraints: str | None = None,
                           rows: int = Query(200, ge=10, le=2000), grid: int = Query(25, ge=3, le=101),
                           curves: int = Query(30, ge=0, le=200)) -> dict[str, Any]:
    """F8: ICE / partial dependence of the model this version serves (its own predictor files, as deployed: cloned,
    pipeline pruned), on the run's raw holdout rows, for the run's monotone constraints."""
    from ..engine import heavy, serving
    from ..engine.api import parse_constraints

    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    if run["config"].get("kind") or run["config"].get("import"):
        raise HTTPException(400, "Monotone checks apply to tabular versions trained here.")
    cons = parse_constraints(constraints, run)
    try:
        out = await request.app.state.heavy.run(heavy.monotone_check, v["path"], run["config"], cons, None, rows, grid, curves)
    except serving.ServingError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, "run_id": v["run_id"], "constraints": cons, "served_model": out["model"], **out}


# --------------------------------------------------------------------------- Try It and batches


class TryRequest(BaseModel):
    row: dict[str, Any]
    explain: bool = True
    target: str | None = None


@router.post("/registry/versions/{vid}/predict", dependencies=[require("model.use", path("vid", "model_version"))])
async def try_it(vid: str, body: TryRequest, request: Request) -> dict[str, Any]:
    """One row through the version's predictor: the prediction, the class probabilities and (explain) Kernel SHAP
    for that row, f(x) = φ₀ + Σ φᵢ, with the top reasons. Computed in a worker process."""
    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    kind = run["config"].get("kind")
    if kind == "forecast":
        raise HTTPException(400, "A forecasting version forecasts series, not rows: use POST /api/forecast/runs/{run}/predict.")
    if kind == "expected_loss":  # G3: one loan → PD̂, LGD̂, EAD̂ and EL
        from ..el import core as elc

        try:
            out = await run_in_threadpool(elc.score_rows, v["path"], [body.row])
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out}
    if kind == "survival":  # G6: one subject → its survival curve, Ŝ(τ) at the horizons, and (Cox) what moves its hazard
        from ..survival import core as svc

        try:
            out = await run_in_threadpool(svc.try_row, v["path"], body.row)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out}
    if kind == "multilabel":  # G8: one row → p̂ per label against its threshold, and the label set
        from ..multilabel import core as mlc

        try:
            out = await run_in_threadpool(mlc.try_row, v["path"], body.row)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out}
    if kind == "segmented":  # G5: the row's own segment model (or the global one), with its reason codes
        from ..segmented import core as sgc

        try:
            out = await request.app.state.heavy.run(sgc.try_row, v["path"], body.row, *(_reason_args(request, v, run) or (None, None)))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out}
    if kind == "anomaly":
        from ..anomaly import core as an

        try:
            out = await request.app.state.heavy.run(an.score_one, v["path"], body.row)
        except an.AnomalyError as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": "anomaly", **out}
    try:
        rc_args = _reason_args(request, v, run)
        out = await request.app.state.heavy.run(scoring.try_it, run["config"], v["path"], body.row, body.target, body.explain,
                                                explain.SHAP_NSAMPLES, *(rc_args or (None, None)))
    except explain.ExplainError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], **out}


class ScoreRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=10_000)
    where: Literal["auto", "api", "worker"] = Field("auto", description="auto: in the API process for a deployed "
                                                    "version (≤ 200 MB) and ≤ 100 rows, else in a predict worker.")
    reasons: bool = Field(False, description="F10: also return each row's decision at the version's threshold and, "
                                             "for adverse ones, the top reason codes (computed in a predict worker).")
    reasons_reference: bool = Field(False, description="F10 audit: also estimate the same SHAP game with 4,096 "
                                                       "coalitions for every explained row (slow).")


INPROC_MAX_BYTES = 200 * 1024 * 1024
INPROC_MAX_ROWS = 100


class _ServeCache:
    """Deployed versions loaded in the API process for the online path: persisted and warmed on load, a few kept.
    One-row scoring of a pruned single model is a few ms of CPU, cheaper than the hop to a worker process."""

    def __init__(self, size: int = 4) -> None:
        import threading
        from collections import OrderedDict

        self.size, self._d, self._lock, self.warm = size, OrderedDict(), threading.Lock(), {}
        self._loading: dict[str, Any] = {}

    def get(self, path: str):
        with self._lock:
            if path in self._d:
                self._d.move_to_end(path)
                return self._d[path]
        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401

        from .fastpath import warm

        p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False)
        self.warm[path] = warm(p)
        with self._lock:
            self._d[path] = p
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return p

    def has(self, path: str) -> bool:
        return path in self._d


_SERVE = _ServeCache()
_SCORE_META: dict[str, dict[str, Any]] = {}


def clear_serving_caches() -> None:
    global _SERVE
    _SCORE_META.clear()
    _SERVE = _ServeCache()


def _inproc_ok(v: dict[str, Any], n_rows: int) -> bool:
    size = ((v.get("result") or {}).get("version") or {}).get("size_bytes") or 0
    return bool((v.get("options") or {}).get("clone_for_deployment")) and 0 < size <= INPROC_MAX_BYTES and n_rows <= INPROC_MAX_ROWS


def warm_version(request: Request, v: dict[str, Any]) -> None:
    """Warm a tabular version in every predict worker and, when it qualifies, in the API process (in a thread)."""
    if ((request.app.state.runs.get(v["run_id"]) or {}).get("config") or {}).get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel"):
        return
    request.app.state.heavy.warm_path(v["path"])
    if _inproc_ok(v, 1):
        import threading

        threading.Thread(target=lambda: _SERVE.get(v["path"]), daemon=True).start()


@router.post("/registry/versions/{vid}/score", dependencies=[require("model.use", path("vid", "model_version"))])
async def score(vid: str, body: ScoreRequest, request: Request) -> dict[str, Any]:
    """The online path (F3): rows → predictions with no SHAP, in a predict worker whose copy of the version is
    persisted and warmed. Returns the predictor's own time (`predict_ms`), the worker's (`worker_ms`) and the API's
    (`server_ms`, including the hop to the worker)."""
    import time as _t

    t0 = _t.perf_counter()
    meta = _SCORE_META.get(vid)
    if meta is None:
        # A ready version's path, kind and size never change: read them once, not on every online call.
        v = _ready_version(request, vid)
        run = request.app.state.runs.get(v["run_id"])
        meta = {"path": v["path"], "kind": run["config"].get("kind"), "model_name": v["model_name"],
                "version": v["version"], "inproc": _inproc_ok(v, 1)}
        _SCORE_META[vid] = meta
    v = meta
    kind = meta["kind"]
    if kind == "forecast":
        raise HTTPException(400, "A forecasting version forecasts series, not rows: use POST /api/forecast/runs/{run}/predict.")
    if kind == "expected_loss":
        from ..el import core as elc

        try:
            out = await run_in_threadpool(elc.score_rows, v["path"], body.rows)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out,
                "server_ms": (_t.perf_counter() - t0) * 1e3}
    if kind == "survival":  # G6: risk score, risk group, Ŝ(τ) and P(event by τ) per row
        from ..survival import core as svc

        try:
            out = await run_in_threadpool(svc.score_rows, v["path"], body.rows)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out,
                "server_ms": (_t.perf_counter() - t0) * 1e3}
    if kind == "multilabel":  # G8: a label set per row
        from ..multilabel import core as mlc

        try:
            out = await run_in_threadpool(mlc.score_rows, v["path"], body.rows)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out,
                "server_ms": (_t.perf_counter() - t0) * 1e3}
    if kind == "segmented":  # G5: each row through its segment's model; reasons=true adds codes for adverse rows
        from ..segmented import core as sgc

        full = _version(request, vid)
        run = request.app.state.runs.get(full["run_id"])
        rc_args = _reason_args(request, full, run) if body.reasons else None
        try:
            out = await request.app.state.heavy.run(sgc.score_rows, v["path"], body.rows, *(rc_args or (None, None)))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], "kind": kind, **out,
                "server_ms": (_t.perf_counter() - t0) * 1e3}
    if kind == "anomaly":
        from ..anomaly import core as an

        try:
            outs = [await request.app.state.heavy.run(an.score_one, v["path"], r) for r in body.rows]
        except an.AnomalyError as e:
            raise HTTPException(400, str(e)) from e
        return {"version_id": vid, "kind": "anomaly", "results": outs, "server_ms": (_t.perf_counter() - t0) * 1e3}
    inproc = body.where == "api" or (body.where == "auto" and meta["inproc"] and len(body.rows) <= INPROC_MAX_ROWS)
    was_warm = _SERVE.has(v["path"]) if inproc else None
    try:
        if inproc:
            if _SERVE.has(v["path"]):
                out = scoring.score_with(_SERVE.get(v["path"]), body.rows)  # a few ms of CPU: no thread hop
            else:
                out = await run_in_threadpool(lambda: scoring.score_with(_SERVE.get(v["path"]), body.rows))
        else:
            out = await request.app.state.heavy.run(scoring.score_rows, v["path"], body.rows)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.reasons:
        full = _version(request, vid)
        run = request.app.state.runs.get(full["run_id"])
        rc_args = _reason_args_or_400(request, full, run)
        try:
            out["reasons"] = await request.app.state.heavy.run(scoring.reasons_rows, full["path"], run["config"], *rc_args,
                                                               body.rows, True, body.reasons_reference)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return {"version_id": vid, "model_name": v["model_name"], "version": v["version"], **out,
            "where": "api" if inproc else "worker", "warm_before_call": was_warm, "server_ms": (_t.perf_counter() - t0) * 1e3}


@router.get("/registry/serving")
def serving_state() -> dict[str, Any]:
    """Deployed versions loaded (persisted, warmed) in the API process for the online path, with their warm-up."""
    return {"api_process": [{"path": p, "warm": _SERVE.warm.get(p)} for p in list(_SERVE._d)],
            "inproc_max_bytes": INPROC_MAX_BYTES, "inproc_max_rows": INPROC_MAX_ROWS}


@router.post("/registry/versions/{vid}/benchmark", dependencies=[require("model.use", path("vid", "model_version"))])
async def benchmark(vid: str, request: Request) -> dict[str, Any]:
    """Measure the version as served, in a predict worker: one-row p50 / p95 on holdout rows and batches of
    1 → 1,000. Stored on the version (`result.serving_benchmark`)."""
    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    if run["config"].get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel"):
        raise HTTPException(400, "The serving benchmark measures tabular versions (one row per call).")
    out = await request.app.state.heavy.run(scoring.benchmark_version, v["path"], run["config"])
    _reg(request).merge_version_result(vid, serving_benchmark=out)
    return {"version_id": vid, **out}


def _kinds(request: Request, run: dict[str, Any]) -> dict[str, str]:
    ds = request.app.state.db.get_dataset(run["dataset_id"])
    types = {c["name"]: c["type"] for c in (ds or {}).get("profile", {}).get("columns", [])}
    types.update(run["config"].get("column_types") or {})
    return {c: ("number" if t == "number" else "text" if t == "text" else "category") for c, t in types.items()}


@router.post("/registry/versions/{vid}/batches", status_code=202, dependencies=[require("model.use", path("vid", "model_version"))])
async def create_batch(vid: str, request: Request, file: UploadFile | None = File(None), created_by: str = Form(""),
                       data_date: str | None = Form(None), files: list[UploadFile] | None = File(None)) -> dict[str, Any]:
    """Score a CSV with the version, in a worker process: progress on GET /registry/batches/{id}, then a preview,
    the scored CSV (predictions and probabilities) and PSI drift of every feature and of the predictions. F18:
    `data_date` (YYYY-MM-DD) is the date of the data in the batch (default: the latest date in the run's time column
    when the file has it, else the upload day); a Production version whose model has a challenger also shadow-scores
    the batch with it."""
    v = _ready_version(request, vid)
    who = _name_ok(created_by, "created_by")
    ups = [f for f in [file, *(files or [])] if f is not None]
    if not ups:
        raise HTTPException(400, "Send the rows to score as `file` (or, for a model on a join, one file per input table as `files`).")
    got: list[tuple[str, bytes]] = []
    for f in ups:
        content = await f.read(config.MAX_UPLOAD_BYTES + 1)
        if len(content) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        if not content.strip():
            raise HTTPException(400, f"The file {f.filename or ''} is empty.".replace("  ", " "))
        got.append((Path(f.filename or "batch.csv").name, content))
    cfg = (request.app.state.runs.get(v["run_id"]) or {}).get("config") or {}
    if cfg.get("join"):  # P15: one file per input table; the model's saved join applied to them (frames.materialise)
        return await run_in_threadpool(submit_join_batch, request, v, cfg, got, who, data_date)
    if len(got) > 1:
        raise HTTPException(400, f"This model reads one table: send one file, not {len(got)}.")
    return submit_batch(request, v, got[0][1], got[0][0], who, data_date)


def submit_join_batch(request: Request, v: dict[str, Any], cfg: dict[str, Any], got: list[tuple[str, bytes]], who: str,
                      data_date: str | None) -> dict[str, Any]:
    bdir = _dir(request) / "batches" / ("_join_" + secrets.token_hex(4))
    try:
        info = scoring.join_batch_frame(str(request.app.state.settings.db_path), cfg, got, bdir)
    except scoring.BatchInputError as e:
        shutil.rmtree(bdir, ignore_errors=True)
        raise HTTPException(400, e.detail()) from e
    name = " + ".join(t["file"] for t in info["tables"].values())[:300]
    try:
        out = submit_batch(request, v, Path(info["path"]).read_bytes(), name, who, data_date, join_info=info)
        (_dir(request) / "batches" / out["id"] / "join.json").write_text(json.dumps(info, default=str))
        shutil.move(str(bdir / "tables"), str(_dir(request) / "batches" / out["id"] / "tables"))
        return out
    finally:
        shutil.rmtree(bdir, ignore_errors=True)


def submit_batch(request: Request, v: dict[str, Any], content: bytes, name: str, who: str,
                 data_date: str | None = None, join_info: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import monitoring_api

    vid = v["id"]
    kind = (request.app.state.runs.get(v["run_id"]) or {}).get("config", {}).get("kind")
    bid = "batch_" + secrets.token_hex(4)
    bdir = _dir(request) / "batches" / bid
    bdir.mkdir(parents=True)
    csv_path = bdir / "input.csv"
    csv_path.write_bytes(content)
    run = request.app.state.runs.get(v["run_id"])
    data_date = monitoring_api.resolve_data_date(request, run, csv_path, data_date)
    reg = _reg(request)
    reg.create_batch({"id": bid, "version_id": vid, "filename": name, "created_by": who, "data_date": data_date})
    if kind in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel"):
        # forecast: the file is new history (same mapping); anomaly: rows to score. Same batch record and drift format.
        fut = request.app.state.heavy.submit(scoring.score_flow_batch, str(request.app.state.settings.db_path), bid, kind,
                                             v["path"], run["config"], str(csv_path), str(bdir), {"id": vid},
                                             _reason_args(request, v, run) if kind == "segmented" else None)
        request.app.state.jobs[bid] = fut
        return reg.get_batch(bid)
    rc_args = _reason_args(request, v, run)
    bcfg = {**run["config"], "_batch_join": join_info} if join_info else run["config"]
    fut = request.app.state.heavy.submit(scoring.score_batch, str(request.app.state.settings.db_path), bid, v["path"],
                                         bcfg, str(csv_path), str(bdir), _kinds(request, run), {"id": vid},
                                         rc_args[0] if rc_args else None, monitoring_api.shadow_for(request, v),
                                         _recourse_saved(request, v) if rc_args else None)
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
    return batch_or_404(request, bid)


@router.get("/registry/batches/{bid}/download", dependencies=[require("model.read", path("bid", "batch"))])
def download_batch(bid: str, request: Request):
    b = batch_or_404(request, bid)
    if b["status"] != "succeeded":
        raise HTTPException(409, f"Batch {bid} is {b['status']}.")
    path = _dir(request) / "batches" / bid / "scored.csv"
    stem = Path(b["filename"]).stem
    return FileResponse(path, media_type="text/csv", filename=f"{stem}_scored_{bid}.csv")


# --------------------------------------------------------------------------- reason codes (F10)


def _reasons_path(request: Request, model_name: str) -> Path:
    return _dir(request) / "reasons" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', model_name)}.json"


def _is_binary(v: dict[str, Any], run: dict[str, Any] | None) -> bool:
    if not run or run["config"].get("kind") in ("forecast", "anomaly", "expected_loss"):
        return False
    pt = ((v.get("result") or {}).get("version") or {}).get("problem_type")
    return pt == "binary"


def _reason_args(request: Request, v: dict[str, Any], run: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, str]] | None:
    """(the model's saved reason-code config, column kinds) for a binary version, else None."""
    if not _is_binary(v, run):
        return None
    from ..engine import reasons as rc

    return rc.load_config(_reasons_path(request, v["model_name"])), _kinds(request, run)


def _no_routed_recourse(run: dict[str, Any] | None) -> None:
    """G1's counterfactual search runs one predictor; a routed (G5) version has several, so it is not offered there yet."""
    if run and run["config"].get("kind") == "segmented":
        raise HTTPException(400, "Counterfactuals are not available for a routed (segmented) version yet: each row's model "
                                 "differs. Reason codes are.")


def _reason_args_or_400(request: Request, v: dict[str, Any], run: dict[str, Any] | None):
    args = _reason_args(request, v, run)
    if args is None:
        raise HTTPException(400, "Reason codes are for binary classification versions.")
    return args


def _stability_path(v: dict[str, Any]) -> Path:
    return Path(v["path"]).parent / "reasons_stability.json"


@router.get("/registry/versions/{vid}/reasons")
async def reasons_config(vid: str, request: Request) -> dict[str, Any]:
    """F10: the reason-code set-up of a binary version: the adverse class, the served threshold t, the attribution
    method chosen for the served model, and the mapping table (feature → code and statements; edited per model)."""

    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    args = _reason_args_or_400(request, v, run)
    from ..engine import reasons as rc

    try:
        info = await request.app.state.heavy.run(scoring.reasons_info, v["path"], run["config"], *args)
    except rc.ReasonError as e:
        raise HTTPException(400, str(e)) from e
    sp = _stability_path(v)
    saved = args[0]
    return {"version_id": vid, "model_name": v["model_name"], **info, "saved": bool(saved),
            "updated_by": saved.get("updated_by"), "updated_at": saved.get("updated_at"),
            "stability": json.loads(sp.read_text()) if sp.exists() else None}


class ReasonsBody(BaseModel):
    actor: str
    mapping: list[dict[str, Any]] | None = None
    adverse_class: str | None = None
    top_k: int | None = Field(None, ge=1, le=10)
    reference: Literal["approved", "population"] | None = None


@router.put("/registry/versions/{vid}/reasons", dependencies=[require("model.card", path("vid", "model_version"))])
async def edit_reasons(vid: str, body: ReasonsBody, request: Request) -> dict[str, Any]:
    """Edit the model's mapping table (codes and plain-English statements) or the adverse class. Stored per registry
    model (every version of it uses it) and recorded in the audit log."""

    from ..engine import reasons as rc
    from ..engine.store import now

    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    args = _reason_args_or_400(request, v, run)
    who = _name_ok(body.actor, "actor")
    cur = dict(args[0])
    features = ((v.get("result") or {}).get("version") or {}).get("features") or []
    classes = [str(c) for c in (((v.get("result") or {}).get("version") or {}).get("classes") or [])]
    changed = []
    if body.mapping is not None:
        unknown = [str(r.get("feature")) for r in body.mapping if not isinstance(r, dict) or r.get("feature") not in features]
        if unknown:
            raise HTTPException(400, f"No feature {unknown[0]!r} in this model; its features are {', '.join(features)}.")
        defaults = rc.default_mapping(features, args[1], ((run["config"].get("monotone") or {}).get("constraints")))
        merged = rc.merge_mapping(body.mapping, rc.merge_mapping(cur.get("mapping"), defaults))
        try:
            rc.validate_mapping(merged, features)
        except rc.ReasonError as e:
            raise HTTPException(400, str(e)) from e
        keep = ("feature", "code", "high", "low", "category", "missing")
        cur["mapping"] = [{k: r[k] for k in keep if k in r} for r in merged]
        changed.append("mapping")
    if body.adverse_class is not None:
        if classes and body.adverse_class not in classes:
            raise HTTPException(400, f"No class {body.adverse_class!r}; the classes are {', '.join(classes)}.")
        cur["adverse_class"] = body.adverse_class
        changed.append("adverse_class")
    if body.top_k is not None:
        cur["top_k"] = body.top_k
        changed.append("top_k")
    if body.reference is not None:
        cur["reference"] = body.reference
        changed.append("reference")
    cur.update({"updated_by": who, "updated_at": now()})
    path = _reasons_path(request, v["model_name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=1))
    _reg(request).audit(actor=who, action="reason_codes_edited", version=v, comment=", ".join(changed) or "no change")
    return await reasons_config(vid, request)


class StabilityBody(BaseModel):
    rows: int = Field(300, ge=20, le=5000)
    noise: float = Field(0.05, gt=0, le=1)
    repeats: int = Field(3, ge=1, le=20)


@router.post("/registry/versions/{vid}/reasons/stability", dependencies=[require("model.use", path("vid", "model_version"))])
async def reasons_stability(vid: str, body: StabilityBody, request: Request) -> dict[str, Any]:
    """The stability check: on the adverse decisions among the first `rows` holdout rows, how often the top reasons
    change when every numeric input moves by ε·σ·N(0, 1). Stored beside the version; the model card shows it."""
    from ..engine import reasons as rc

    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    args = _reason_args_or_400(request, v, run)
    try:
        out = await request.app.state.heavy.run(scoring.reasons_stability, v["path"], run["config"], *args, body.rows,
                                                body.noise, body.repeats, str(_stability_path(v)))
    except rc.ReasonError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, **out}


# --------------------------------------------------------------------------- counterfactuals / recourse (G1)


def _recourse_path(request: Request, model_name: str) -> Path:
    return _dir(request) / "recourse" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', model_name)}.json"


def _recourse_saved(request: Request, v: dict[str, Any]) -> dict[str, Any]:
    path = _recourse_path(request, v["model_name"])
    return json.loads(path.read_text()) if path.exists() else {}


def _recourse_check_path(v: dict[str, Any]) -> Path:
    return Path(v["path"]).parent / "recourse_check.json"


@router.get("/registry/versions/{vid}/recourse")
async def recourse_config(vid: str, request: Request) -> dict[str, Any]:
    """G1: what a counterfactual may change for this binary version: feature → fixed / any / increase / decrease,
    detected from G0's column roles (age, dates, IDs, geography fixed; history only down; time only up) and narrowed
    by F8 monotone constraints; the training ranges and categories it stays inside; the last acceptance check."""
    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    _no_routed_recourse(run)
    args = _reason_args(request, v, run)
    if args is None:
        raise HTTPException(400, "Counterfactuals are for binary classification versions (a decision that can be adverse).")
    saved = _recourse_saved(request, v)
    try:
        info = await request.app.state.heavy.run(scoring.recourse_info, v["path"], run["config"], *args, saved)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    cp = _recourse_check_path(v)
    return {"version_id": vid, "model_name": v["model_name"], **info, "updated_by": saved.get("updated_by"),
            "updated_at": saved.get("updated_at"), "check": json.loads(cp.read_text()) if cp.exists() else None}


class RecourseEdit(BaseModel):
    actor: str
    actions: dict[str, Literal["fixed", "any", "increase", "decrease"]]


@router.put("/registry/versions/{vid}/recourse", dependencies=[require("model.card", path("vid", "model_version"))])
async def edit_recourse(vid: str, body: RecourseEdit, request: Request) -> dict[str, Any]:
    """Override what may change, per registry model (every version uses it); audited."""
    from ..engine.store import now

    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    _no_routed_recourse(run)
    if _reason_args(request, v, run) is None:
        raise HTTPException(400, "Counterfactuals are for binary classification versions.")
    who = _name_ok(body.actor, "actor")
    features = ((v.get("result") or {}).get("version") or {}).get("features") or []
    unknown = [f for f in body.actions if f not in features]
    if unknown:
        raise HTTPException(400, f"No feature {unknown[0]!r} in this model; its features are {', '.join(features)}.")
    cur = _recourse_saved(request, v)
    acts = {**(cur.get("actions") or {}), **body.actions}
    cur.update({"actions": acts, "updated_by": who, "updated_at": now()})
    path = _recourse_path(request, v["model_name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=1))
    _reg(request).audit(actor=who, action="recourse_edited", version=v,
                        comment=", ".join(f"{f}: {a}" for f, a in body.actions.items()) or "no change")
    return await recourse_config(vid, request)


class RecourseRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=200)


@router.post("/registry/versions/{vid}/recourse", dependencies=[require("model.use", path("vid", "model_version"))])
async def recourse(vid: str, body: RecourseRequest, request: Request) -> dict[str, Any]:
    """Counterfactuals for posted rows: for each adverse one, up to 3 diverse sets of changes that flip the served
    decision at the version's threshold, each re-scored by the served model, with its distance and plausibility."""
    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    _no_routed_recourse(run)
    args = _reason_args(request, v, run)
    if args is None:
        raise HTTPException(400, "Counterfactuals are for binary classification versions.")
    try:
        out = await request.app.state.heavy.run(scoring.recourse_rows, v["path"], run["config"], *args,
                                                _recourse_saved(request, v), body.rows)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, **out}


class RecourseCheck(BaseModel):
    rows: int = Field(120, ge=10, le=1000, description="adverse holdout rows to test")


@router.post("/registry/versions/{vid}/recourse/check", dependencies=[require("model.use", path("vid", "model_version"))])
async def recourse_check(vid: str, body: RecourseCheck, request: Request) -> dict[str, Any]:
    """The acceptance check on the first `rows` adverse holdout rows: the share with ≥ 1 valid counterfactual, and
    0 fixed features moved, 0 values outside training, 0 direction breaks. Stored; the model card shows it."""
    v = _ready_version(request, vid)
    run = request.app.state.runs.get(v["run_id"])
    _no_routed_recourse(run)
    args = _reason_args(request, v, run)
    if args is None:
        raise HTTPException(400, "Counterfactuals are for binary classification versions.")
    try:
        out = await request.app.state.heavy.run(scoring.recourse_check, v["path"], run["config"], *args,
                                                _recourse_saved(request, v), body.rows, str(_recourse_check_path(v)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"version_id": vid, **out}

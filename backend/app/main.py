"""The FastAPI app. Run with: uvicorn app.main:app --app-dir backend --port 8000"""

from __future__ import annotations

import asyncio

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from . import __version__, config
from .datasets import DatasetError, DatasetStore
from .db import Database
from .autopilot import api as autopilot_api
from .autopilot.engine import Manager as AutopilotManager
from .engine import api as engine_api
from .engine import importer as import_api
from .forecast import api as forecast_api
from .anomaly import api as anomaly_api
from .gym import api as gym_api
from .vela import api as vela_api
from .agent import api as agent_api
from .agent.loop import AgentManager
from . import llm, llm_api
from . import data_doctor_api, featurelab_api
from .engine.runner import JobRunner
from .engine import active_learning_api, annotations_api, learning_curve_api, shift_api, slices_api
from .engine.pool import HeavyPool
from .registry import api as registry_api
from .registry import changes_api, monitoring_api
from .registry import credit_report_api
from .registry.store import RegistryStore
from .engine.serving import PredictorCache
from .engine.store import RunStore
from .catalog import api as catalog_api
from .catalog.retention import EvictedError

log = logging.getLogger("ensemble.main")
from .http import dataset_or_404
from .samples import SAMPLES
from .samples_g3 import SAMPLES_G3
from .samples_g4 import SAMPLES_G4
from .samples_g5 import SAMPLES_G5
from .samples_g6 import SAMPLES_G6
from .samples_g7 import SAMPLES_G7
from .samples_g8 import SAMPLES_G8
from .segmented import api as segmented_api
from .survival import api as survival_api
from .multilabel import api as multilabel_api
from .sqlsource import api as sql_api
from .reject import api as reject_api
from .el import api as el_api
from .fairlending import api as fairlending_api
from .splits import summarize as summarize_split
from .schemas import Dataset, DatasetList, Health

# The background-job modules: shut down with the API, block an admin reset while busy (409 message), and own a router.
JOB_MODULES = (
    (learning_curve_api, "A learning curve is running; cancel it first."),
    (active_learning_api, "A labelling queue is working; cancel it first."),
    (annotations_api, "Annotator class probabilities are being computed; wait for them."),
    (shift_api, "A shift check is running; cancel it first."),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _summary(rec: dict[str, Any]) -> dict[str, Any]:
    p = rec["profile"]
    return {
        "id": rec["id"], "name": rec["name"], "filename": rec["filename"], "source": rec["source"],
        "sample": rec["source"] == "sample", "note": rec["note"], "created_at": rec["created_at"],
        "size_bytes": rec["size_bytes"], "rows": rec["rows"], "n_cols": p["n_cols"], "kind": rec["kind"],
        "target": rec["target"], "n_classes": p["n_classes"],
        "flows": {"timeseries": ["forecast"], "transactions": ["anomaly"]}.get(rec["kind"], ["tabular", "anomaly"]),
    }


def _detail(rec: dict[str, Any]) -> dict[str, Any]:
    return {**_summary(rec), "profile": rec["profile"]}


def catalog_backfill(db: Database, data_dir) -> dict[str, Any]:
    from .catalog.schema import backfill

    with db.connect() as con:
        out = backfill(con, data_dir)
    if not out.get("skipped"):
        log.info("P1 catalog backfill: %s", out)
    return out


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        store = DatasetStore(settings, db)
        await run_in_threadpool(store.ensure_samples)  # first start: generate the samples
        await run_in_threadpool(catalog_backfill, db, settings.data_dir)  # P1: once per database (idempotent)
        app.state.settings, app.state.db, app.state.datasets = settings, db, store
        app.state.catalog = store.catalog  # P1: projects, edges, tags and notes, champions
        llm.configure(settings.data_dir)  # F5: runtime timeouts / model / switches from data/llm_settings.json
        llm.warm()
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        app.state.runs = RunStore(db)
        app.state.runner = JobRunner(settings, app.state.runs)
        app.state.runner.start()  # marks runs interrupted by a previous stop as failed
        app.state.predictors = PredictorCache()
        app.state.registry = RegistryStore(db)
        app.state.registry.recover()  # builds and batches cut off by a previous stop are marked failed
        app.state.jobs = {}
        app.state.heavy = HeavyPool()
        app.state.heavy.start()  # SHAP, importance, predictions, version builds and batch scoring run here
        app.state.autopilot = AutopilotManager(app, settings.data_dir / "autopilot")
        app.state.autopilot.resume_all()  # autopilots cut off by a restart continue from their saved state
        app.state.agent = AgentManager(app, settings.data_dir / "agent")  # G10: the Vela agent's conversations
        from .vela.api import warm_doctor

        loop = asyncio.get_running_loop()
        app.state.runner.on_success = lambda rid: loop.call_soon_threadsafe(lambda: asyncio.ensure_future(warm_doctor(app, rid)))
        yield
        app.state.agent.shutdown()
        app.state.runner.shutdown()
        for mod, _ in JOB_MODULES:
            mod.shutdown()
        app.state.heavy.shutdown()

    app = FastAPI(title="ENSEMBLE demo API", version=__version__, lifespan=lifespan)

    def store(request: Request) -> DatasetStore:
        return request.app.state.datasets

    @app.get("/api/health", response_model=Health)
    def health(request: Request) -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "datasets": len(request.app.state.db.list_datasets()),
                "data_dir": str(settings.data_dir), "runs": request.app.state.runs.count(),
                "engine": engine_api.engine_info()}

    @app.get("/api/datasets", response_model=DatasetList)
    def list_datasets(request: Request) -> dict[str, Any]:
        # Samples first, in a fixed order; then uploads, oldest first.
        order = [*SAMPLES, *SAMPLES_G3, *SAMPLES_G4, *SAMPLES_G5, *SAMPLES_G6, *SAMPLES_G7, *SAMPLES_G8]
        recs = sorted(request.app.state.db.list_datasets(),
                      key=lambda r: (0, order.index(r["id"]), "") if r["id"] in order else (1, 0, r["created_at"]))
        return {"datasets": [_summary(r) for r in recs]}

    @app.get("/api/datasets/{dataset_id}", response_model=Dataset)
    def get_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
        rec = dataset_or_404(request, dataset_id)
        return _detail(rec)

    @app.post("/api/datasets", response_model=Dataset, status_code=201)
    async def upload_dataset(request: Request, file: UploadFile = File(...), target: str | None = Form(None)) -> dict[str, Any]:
        content = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(content) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            rec = await run_in_threadpool(store(request).create_from_upload, file.filename or "", content, target or None)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return _detail(rec)

    @app.patch("/api/datasets/{dataset_id}", response_model=Dataset)
    def confirm_target(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Confirm the column to predict (F4). Body {"target": column}. Re-profiles for that target and draws (or
        reuses) its own holdout and folds, keyed by the target; the profile's `splits` describe that split."""
        target = (body or {}).get("target")
        if not isinstance(target, str) or not target:
            raise HTTPException(400, 'Send {"target": "<column to predict>"}.')
        dataset_or_404(request, dataset_id)
        try:
            rec = store(request).set_target(dataset_id, target)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return _detail(rec)

    @app.get("/api/datasets/{dataset_id}/splits")
    def dataset_splits(dataset_id: str, request: Request) -> dict[str, Any]:
        """Every split stored for the dataset, one per (target, strategy) used so far, and the current target's key."""
        rec = dataset_or_404(request, dataset_id)
        cur = rec["profile"].get("splits") or {}
        return {"dataset_id": dataset_id, "target": rec["target"], "current": cur.get("key"),
                "splits": store(request).list_splits(dataset_id)}

    @app.get("/api/datasets/{dataset_id}/split-options")
    def split_options(dataset_id: str, request: Request, target: str | None = None) -> dict[str, Any]:
        """F6: the split strategies (random, group, time, time + group), the date and entity columns found, the
        suggested strategy, and warnings for a random split on data with a date or entity column."""
        rec = dataset_or_404(request, dataset_id)
        return {"dataset_id": dataset_id, "target": target or rec["target"],
                **store(request).split_options(dataset_id, target or rec["target"])}

    @app.get("/api/datasets/{dataset_id}/intake")
    def dataset_intake(dataset_id: str, request: Request) -> dict[str, Any]:
        """G0 Smart Intake: each column's role (target, date, entity, money, ...) with a confidence and its evidence,
        and what can be built from the table, best first, each with a reason and the pre-fill for its flow."""
        return _intake(request, dataset_id, None)

    @app.post("/api/datasets/{dataset_id}/intake")
    def dataset_intake_edit(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """The same with the user's role edits, body {"roles": {"column": "role"}}: the suggestions follow them."""
        roles = (body or {}).get("roles") or {}
        if not isinstance(roles, dict):
            raise HTTPException(400, 'Send {"roles": {"<column>": "<role>"}}.')
        return _intake(request, dataset_id, roles)

    def _intake(request: Request, dataset_id: str, roles: dict[str, str] | None) -> dict[str, Any]:
        from .intake import intake

        rec = dataset_or_404(request, dataset_id)
        # A2 M9: a confirmed target (Data Report's y, the run's label) is the target here too, unless the user's role
        # edits name another; Smart Intake's own guess is only for an unconfirmed table
        tgt = rec.get("target")
        if tgt and (rec.get("profile") or {}).get("target_source") != "guessed" and not any(v == "target" for v in (roles or {}).values()):
            roles = {**(roles or {}), tgt: "target"}
        try:
            df = store(request).load_frame(dataset_id)
            if tgt not in df.columns:
                roles = {k: v for k, v in (roles or {}).items() if k in df.columns} or None
            out = {"dataset_id": dataset_id, **intake(df, roles, versions=_binary_versions(request, dataset_id))}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # D1: the Data Doctor's stored answers (not known at decision time, fairness only) join every pre-filled drop list.
        drops = data_doctor_api.run_drops(store(request), dataset_id, list(df.columns))
        if drops and drops["columns"]:
            for s in out["suggestions"]:
                p = s["prefill"]
                if s["route"] == "quick-train":
                    tgt = p.get("target")
                    p["drop"] = list(dict.fromkeys([*(p.get("drop") or []), *[c for c in drops["columns"] if c != tgt]]))
            out["decision_time_drops"] = drops
        return out

    def _binary_versions(request: Request, dataset_id: str) -> list[dict[str, Any]]:
        """Ready binary registry versions trained on this dataset, newest first (G1's recourse suggestion opens one)."""
        reg = request.app.state.registry
        out = []
        for m in reg.list_models():
            if m.get("dataset_id") != dataset_id:
                continue
            for v in reg.list_versions(m["name"]):
                ver = (v.get("result") or {}).get("version") or {}
                if v["status"] == "ready" and ver.get("problem_type") == "binary":
                    out.append({"id": v["id"], "model_name": v["model_name"], "version": v["version"], "label": m.get("label"),
                                "stage": v.get("stage"), "created_at": v.get("created_at")})
        return sorted(out, key=lambda v: v["created_at"] or "", reverse=True)

    @app.get("/api/datasets/{dataset_id}/rows")
    def dataset_rows(dataset_id: str, request: Request, value: str, column: str | None = None, limit: int = 3) -> dict[str, Any]:
        """G10: rows where `column` equals `value` (as text), or where any text column does when no column is given. For
        looking up one applicant or account by its id."""
        dataset_or_404(request, dataset_id)
        df = store(request).load_frame(dataset_id)
        if column is not None and column not in df.columns:
            raise HTTPException(400, f"No column '{column}' in {dataset_id}.")
        cols = [column] if column else [c for c in df.columns if df[c].dtype == object]
        hit = None
        for c in cols:
            m = df[c].astype(str) == value
            if m.any():
                hit, column = df[m], c
                break
        rows = [] if hit is None else hit.head(max(1, min(limit, 50))).astype(object).where(hit.head(max(1, min(limit, 50))).notna(), None).to_dict("records")
        return {"dataset_id": dataset_id, "column": column, "value": value, "n": 0 if hit is None else int(len(hit)), "rows": rows}

    @app.post("/api/datasets/{dataset_id}/splits")
    def draw_split(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """F6: draw (or reuse) the split for {target, strategy, group_column?, time_column?, cutoff?, tuning_share?}
        and describe it: rows, groups and dates per role (train, tuning, holdout, excluded), shared groups, and rows
        per period for time splits. Runs that name the same split (POST /api/runs, `split_spec`) reuse it."""
        from .splits import RANDOM_STRATIFIED

        rec = dataset_or_404(request, dataset_id)
        body = body or {}
        target = body.get("target") or rec["target"]
        strategy = body.get("strategy") or RANDOM_STRATIFIED
        try:
            _, sp, created = store(request).ensure_splits(dataset_id, target, strategy, body)
            df = store(request).load_frame(dataset_id)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return {"dataset_id": dataset_id, "drawn_now": created, **summarize_split(sp, df[target] if target else None)}

    @app.post("/api/admin/reset")
    def admin_reset(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Delete every run, registry entry, batch, autopilot and upload; regenerate the samples. Body {"confirm": "reset"}.
        Refused while a run is queued or running."""
        from . import reset as reset_mod

        if (body or {}).get("confirm") != "reset":
            raise HTTPException(400, 'Send {"confirm": "reset"} to delete all runs, models and uploads.')
        st = request.app.state
        if st.runs.list_active():
            raise HTTPException(409, "A run is queued or running; cancel it first.")
        for mod, busy in JOB_MODULES:
            if mod.active():
                raise HTTPException(409, busy)
        if any(getattr(a, "is_alive", lambda: False)() for a in getattr(st.autopilot, "threads", {}).values()):
            raise HTTPException(409, "An autopilot is running; cancel it first.")
        if any(not t.done() for t in st.agent.tasks.values()):
            raise HTTPException(409, "The Vela agent is working on a conversation; stop it first.")
        st.predictors = PredictorCache()
        registry_api.clear_serving_caches()
        out = reset_mod.reset(settings)
        st.autopilot.store.root.mkdir(parents=True, exist_ok=True)
        st.autopilot.threads.clear()
        st.agent.sessions.clear()
        st.agent.root.mkdir(parents=True, exist_ok=True)
        return out

    app.include_router(import_api.router)
    app.include_router(engine_api.router)
    app.include_router(catalog_api.router)  # P1: projects, lineage, compare, annotations, champions, retention
    app.middleware("http")(catalog_api.evicted_guard)
    app.add_exception_handler(EvictedError, catalog_api.evicted_handler)
    for mod, _ in JOB_MODULES:
        app.include_router(mod.router)
    app.include_router(slices_api.router)
    app.include_router(forecast_api.router)
    app.include_router(anomaly_api.router)
    app.include_router(registry_api.router)
    app.include_router(monitoring_api.router)
    app.include_router(changes_api.router)
    app.include_router(credit_report_api.router)
    app.include_router(el_api.router)
    app.include_router(fairlending_api.router)
    app.include_router(segmented_api.router)
    app.include_router(survival_api.router)
    app.include_router(multilabel_api.router)
    app.include_router(sql_api.router)
    app.include_router(reject_api.router)
    app.include_router(gym_api.router)
    app.include_router(vela_api.router)
    app.include_router(autopilot_api.router)
    app.include_router(llm_api.router)
    app.include_router(agent_api.router)
    app.include_router(data_doctor_api.router)  # D1
    app.include_router(featurelab_api.router)  # D2
    return app


app = create_app()

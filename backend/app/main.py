"""The FastAPI app. Run with: uvicorn app.main:app --app-dir backend --port 8000"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from . import __version__, config
from .datasets import DatasetError, DatasetStore
from .db import Database
from .engine import api as engine_api
from .engine.runner import JobRunner
from .engine.pool import HeavyPool
from .registry import api as registry_api
from .registry.store import RegistryStore
from .engine.serving import PredictorCache
from .engine.store import RunStore
from .samples import SAMPLES
from .schemas import Dataset, DatasetList, DatasetSummary, Health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _summary(rec: dict[str, Any]) -> dict[str, Any]:
    p = rec["profile"]
    return {
        "id": rec["id"], "name": rec["name"], "filename": rec["filename"], "source": rec["source"],
        "sample": rec["source"] == "sample", "note": rec["note"], "created_at": rec["created_at"],
        "size_bytes": rec["size_bytes"], "rows": rec["rows"], "n_cols": p["n_cols"], "kind": rec["kind"],
        "target": rec["target"], "n_classes": p["n_classes"],
    }


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        store = DatasetStore(settings, db)
        await run_in_threadpool(store.ensure_samples)  # first start: generate the samples
        app.state.settings, app.state.db, app.state.datasets = settings, db, store
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
        yield
        app.state.runner.shutdown()
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
        order = list(SAMPLES)
        recs = sorted(request.app.state.db.list_datasets(),
                      key=lambda r: (0, order.index(r["id"]), "") if r["id"] in order else (1, 0, r["created_at"]))
        return {"datasets": [_summary(r) for r in recs]}

    @app.get("/api/datasets/{dataset_id}", response_model=Dataset)
    def get_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
        rec = request.app.state.db.get_dataset(dataset_id)
        if not rec:
            raise HTTPException(404, f"No dataset with id '{dataset_id}'.")
        return {**_summary(rec), "profile": rec["profile"]}

    @app.post("/api/datasets", response_model=Dataset, status_code=201)
    async def upload_dataset(request: Request, file: UploadFile = File(...), target: str | None = Form(None)) -> dict[str, Any]:
        content = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(content) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            rec = await run_in_threadpool(store(request).create_from_upload, file.filename or "", content, target or None)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return {**_summary(rec), "profile": rec["profile"]}

    app.include_router(engine_api.router)
    app.include_router(registry_api.router)
    return app


app = create_app()

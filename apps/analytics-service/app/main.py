"""Analytics Service — FastAPI app for data sampling, profiling, and aggregation.

Uses DuckDB for fast analytical queries on CSV, Parquet, and Excel files.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infra.config import settings
from app.infra.db.postgres import dispose_engine, init_db
from app.infra.db.storage import get_storage, uploads_dir as _uploads_dir
from app.features.data_accelerator.api import router as data_accelerator_router
from app.features.files.api import router as files_router
from app.features.prompt_lab import router as prompt_lab_router
from app.features.prompt_lab.services import dataset_files as _promptlab_files

logger = logging.getLogger(__name__)


# PromptLab uploads parquet files to disk; sweep stale ones periodically.
_PROMPTLAB_SWEEP_INTERVAL_SEC = 30 * 60   # 30 min


async def _promptlab_dataset_sweeper() -> None:
    """Periodically remove parquet files in the promptlab datasets dir
    whose mtime is older than ``DATASET_FILE_TTL_SECONDS`` (24h).
    """
    while True:
        try:
            removed = _promptlab_files.sweep_expired_dataset_files()
            if removed:
                logger.info("promptlab dataset sweeper: removed %d files", removed)
        except Exception:
            logger.exception("promptlab dataset sweeper iteration failed")
        await asyncio.sleep(_PROMPTLAB_SWEEP_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown lifecycle."""
    # Ensure storage dirs exist
    storage = get_storage()
    storage.ensure_dir("datasets")
    storage.ensure_dir("samples")
    storage.ensure_dir("exports")
    _uploads_dir().mkdir(parents=True, exist_ok=True)

    # PromptLab dataset storage root (idempotent).
    _promptlab_files.datasets_dir()

    await init_db()
    sweeper_task = asyncio.create_task(_promptlab_dataset_sweeper())
    logger.info("Analytics Service started")

    try:
        yield
    finally:
        sweeper_task.cancel()
        try:
            await sweeper_task
        except (asyncio.CancelledError, Exception):
            pass
        await dispose_engine()
        logger.info("Analytics Service stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Analytics Service",
        description="Data sampling, profiling, and aggregation operations",
        version="1.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    application.include_router(data_accelerator_router)
    application.include_router(files_router)
    application.include_router(prompt_lab_router)

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return application


app = create_app()

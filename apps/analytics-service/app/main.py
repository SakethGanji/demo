"""Analytics Service — FastAPI app for dataset management and analytics.

Datasets are first-class, versioned, taggable entities. Analytical operations
(sampling, profiling, aggregation) run on them via DuckDB. Every route is served
under a single ``/api/v1`` prefix with a uniform problem+json error envelope and
a uniform ``Page`` list envelope.
"""

from __future__ import annotations

import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import UnhandledExceptionMiddleware, install_error_handlers
from app.api.middleware import install_middleware
from app.infra.config import settings
from app.infra.db.postgres import dispose_engine, init_db
from app.infra.db.storage import ARTIFACT_ROOT, get_storage, uploads_dir as _uploads_dir
from app.features.audit.api import router as audit_router
from app.features.auth.api import router as auth_router, teams_router
from app.features.auth.deps import get_principal
from app.features.data_accelerator.api import router as data_accelerator_router
from app.features.discovery.api import router as discovery_router
from app.features.explorer.api import router as explorer_router
from app.features.files.api import router as files_router
from app.features.jobs.api import router as jobs_router
from app.features.library.api import router as library_router
from app.features.mcp import mount as mount_mcp
from app.features.quality.api import router as quality_router
# Importing these APIs also registers their job handlers (`transform`,
# `relationship_discovery`), so the worker loop can claim work enqueued
# with sync=false.
from app.features.relationships.api import router as relationships_router
from app.features.transform.api import router as transform_router
from app.features.webhooks.api import router as webhooks_router

logger = logging.getLogger(__name__)

# Tag metadata drives the grouping/order in the OpenAPI docs.
OPENAPI_TAGS = [
    {"name": "auth", "description": "Current user and user provisioning (X-User-Id header identity)."},
    {"name": "teams", "description": "Teams and membership/role management (RBAC)."},
    {"name": "datasets", "description": "Create, list, search, inspect, and delete datasets."},
    {"name": "versions", "description": "Immutable, auto-incrementing versions of a dataset."},
    {"name": "tags", "description": "Named pointers (e.g. 'production') to a specific version."},
    {"name": "sheets", "description": "Per-sheet metadata for multi-sheet (Excel) datasets."},
    {"name": "uploads", "description": "Streaming and resumable (TUS) dataset ingestion."},
    {"name": "downloads", "description": "Download datasets/versions and read sample slices."},
    {"name": "analytics", "description": "Sampling, profiling, and aggregation over data."},
    {"name": "explorer", "description": "Row-level preview and structured queries over version sheets."},
    {"name": "transform", "description": "Saved transformation pipelines: preview, run, publish."},
    {"name": "relationships", "description": "Discovered and declared relationships between sheets."},
    {"name": "joins", "description": "Guided joins over confirmed relationships, with pre-flight warnings."},
    {"name": "webhooks", "description": "Outbound notifications on dataset lifecycle events."},
    {"name": "storage", "description": "Storage usage and housekeeping."},
    {"name": "audit", "description": "Append-only audit trail (platform admins)."},
    {"name": "system", "description": "Health and service metadata."},
]

# Response headers a cross-origin browser client is allowed to READ. Anything
# not listed here (and not one of the seven CORS-safelisted headers) is stripped
# from the JS-visible response even though it arrives on the wire, so a header
# the client must act on has to be named explicitly:
#   - Content-Disposition — carries the download filename for all five download
#     responses; without it a fetch/blob download saves as the URL's last path
#     segment ("download", "1") instead of "orders_v3.csv".
#   - Content-Length — progress bars for large streamed downloads. (Safelisted
#     for fetch, but not for XHR's getResponseHeader; listing it costs nothing.)
#   - TUS headers — a resumable-upload client MUST read Location (where to PATCH)
#     and Upload-Offset (where to resume). Hiding them breaks resumable upload
#     entirely, not cosmetically.
#   - X-Request-Id — lets a UI quote the id of a failed request in a bug report.
CORS_EXPOSE_HEADERS = [
    "X-Request-Id",
    "Content-Disposition",
    "Content-Length",
    "Location",
    "Upload-Offset",
    "Upload-Length",
    "Tus-Resumable",
    "Tus-Version",
    "Tus-Checksum-Algorithm",
]


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown lifecycle."""
    storage = get_storage()
    storage.ensure_dir("datasets")
    storage.ensure_dir(ARTIFACT_ROOT)
    _uploads_dir().mkdir(parents=True, exist_ok=True)

    await init_db()

    # Feature modules register their job handlers at import time (their routers
    # are imported at module top). Start the worker over whatever registered.
    from app.shared import worker

    worker_stop: asyncio.Event | None = None
    worker_task: asyncio.Task | None = None
    if settings.job_worker_enabled:
        worker_stop = asyncio.Event()
        worker_task = asyncio.create_task(
            worker.run_worker_loop(settings.job_worker_poll_seconds, worker_stop))

    logger.info("Analytics Service started (api_prefix=%s)", settings.api_prefix)

    try:
        # The MCP session manager refuses to serve before its task group exists,
        # so its lifespan has to wrap the whole serving window.
        async with application.state.mcp.lifespan():
            yield
    finally:
        if worker_stop is not None:
            worker_stop.set()
        if worker_task is not None:
            await worker_task
        await dispose_engine()
        logger.info("Analytics Service stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Analytics Service",
        description=(
            "Dataset management (versioning, tagging, upload) plus sampling, "
            "profiling, and aggregation. All endpoints live under `/api/v1`."
        ),
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Added FIRST so it runs INNERMOST (last-added is outermost). An unhandled
    # exception has to become a response here, beneath every other layer, or
    # Starlette's ServerErrorMiddleware writes the 500 from ABOVE CORS and the
    # browser sees a bare network error instead of the problem+json envelope.
    # Being innermost also means the 500 still collects X-Request-Id, the
    # security headers, and an audit row. See UnhandledExceptionMiddleware.
    application.add_middleware(UnhandledExceptionMiddleware)

    install_middleware(application)

    # Added after install_middleware so CORS runs outermost — any middleware
    # that short-circuits a response still gets CORS headers attached.
    # Browsers reject wildcard origin + credentials; never send that combo.
    wildcard = "*" in settings.cors_origins
    if wildcard and settings.cors_allow_credentials:
        logger.warning(
            "CORS origins are '*'; disabling allow_credentials. Set "
            "ACCELERATOR_CORS_ORIGINS to explicit origins for credentialed requests."
        )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials and not wildcard,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=CORS_EXPOSE_HEADERS,
    )

    install_error_handlers(application)

    # Everything the outside world touches hangs off one versioned prefix.
    api = APIRouter(prefix=settings.api_prefix)

    # Identity/team endpoints self-guard per-route (me, users, teams).
    api.include_router(auth_router)
    api.include_router(teams_router)

    # The data plane sits behind a blanket authentication guard; individual
    # routes then enforce team-scoped RBAC.
    protected = APIRouter(dependencies=[Depends(get_principal)])
    # discovery first: its static /datasets/facets must beat /datasets/{id}
    protected.include_router(discovery_router)
    protected.include_router(explorer_router)
    protected.include_router(data_accelerator_router)
    protected.include_router(files_router)
    protected.include_router(quality_router)
    protected.include_router(library_router)
    protected.include_router(transform_router)
    protected.include_router(relationships_router)
    protected.include_router(webhooks_router)
    protected.include_router(jobs_router)
    protected.include_router(audit_router)
    api.include_router(protected)

    application.include_router(api)

    # The MCP surface: a JSON-RPC endpoint at {api_prefix}/mcp, mounted rather
    # than routed so it stays out of the REST OpenAPI document. Identity comes
    # from the same get_principal every route above uses.
    application.state.mcp = mount_mcp(application, api_prefix=settings.api_prefix)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Liveness probe (unversioned, for load balancers)."""
        return {"status": "healthy"}

    return application


app = create_app()

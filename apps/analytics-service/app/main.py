"""Analytics Service — FastAPI app for dataset management and analytics.

Datasets are first-class, versioned, taggable entities. Analytical operations
(sampling, profiling, aggregation) run on them via DuckDB. Every route is served
under a single ``/api/v1`` prefix with a uniform problem+json error envelope and
a uniform ``Page`` list envelope.
"""

from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.middleware import install_middleware
from app.infra.config import settings
from app.infra.db.postgres import dispose_engine, init_db
from app.infra.db.storage import get_storage, uploads_dir as _uploads_dir
from app.features.audit.api import router as audit_router
from app.features.auth.api import router as auth_router, teams_router
from app.features.auth.deps import get_principal
from app.features.data_accelerator.api import router as data_accelerator_router
from app.features.discovery.api import router as discovery_router
from app.features.files.api import router as files_router
from app.features.library.api import router as library_router
from app.features.quality.api import router as quality_router

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
    {"name": "storage", "description": "Storage usage and housekeeping."},
    {"name": "audit", "description": "Append-only audit trail (platform admins)."},
    {"name": "system", "description": "Health and service metadata."},
]


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown lifecycle."""
    storage = get_storage()
    storage.ensure_dir("datasets")
    storage.ensure_dir("samples")
    storage.ensure_dir("exports")
    _uploads_dir().mkdir(parents=True, exist_ok=True)

    await init_db()
    logger.info("Analytics Service started (api_prefix=%s)", settings.api_prefix)

    try:
        yield
    finally:
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
        expose_headers=["X-Request-Id"],
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
    protected.include_router(data_accelerator_router)
    protected.include_router(files_router)
    protected.include_router(quality_router)
    protected.include_router(library_router)
    protected.include_router(audit_router)
    api.include_router(protected)

    application.include_router(api)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Liveness probe (unversioned, for load balancers)."""
        return {"status": "healthy"}

    return application


app = create_app()

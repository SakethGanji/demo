"""Main entry point for the workflow engine server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import os

from .core.config import settings
from .engine.node_registry import register_all_nodes
from .engine.logging import setup_logging
from .routes import api_router, webhook_router, stream_router, public_app_router
from .schemas.common import RootResponse, HealthResponse
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    import asyncio
    import logging

    # Structured logging
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    # Initialize database tables
    await init_db()
    register_all_nodes()

    # Auto-seed demo workflows in dev (SEED=1)
    if os.environ.get("SEED", "").strip() in ("1", "true"):
        from .db.seed import seed_workflows
        await seed_workflows(reset=False)
        logger.info("Dev seed applied")

    from .db.session import async_session_factory
    from .engine.cron_scheduler import CronScheduler
    from .engine.stale_reaper import StaleReaper
    from .engine.retention_cleaner import RetentionCleaner
    from .engine import execution_registry

    # Start background services
    cron_scheduler = CronScheduler(async_session_factory)
    stale_reaper = StaleReaper(async_session_factory)
    retention_cleaner = RetentionCleaner(async_session_factory)

    cron_scheduler.start()
    stale_reaper.start()
    retention_cleaner.start()

    logger.info(f"{settings.app_name} v{settings.app_version} started on http://{settings.host}:{settings.port}")

    yield

    # Graceful shutdown
    logger.info("Shutting down background services...")
    await cron_scheduler.stop()
    await stale_reaper.stop()
    await retention_cleaner.stop()

    # Wait for running executions to finish
    running = execution_registry.get_all()
    if running:
        logger.info("Waiting for %d running executions to finish...", len(running))
        tasks = list(running.values())
        done, pending = await asyncio.wait(tasks, timeout=30)
        if pending:
            logger.warning("Cancelling %d executions that didn't finish in time", len(pending))
            for task in pending:
                task.cancel()

    # Close database connection pool
    from .db.session import dispose_engine

    await dispose_engine()

    logger.info(f"{settings.app_name} stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        description="Python workflow engine - DAG-based workflow execution",
        version=settings.app_version,
        lifespan=lifespan,
        debug=settings.debug,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    # Include routers
    app.include_router(api_router)
    app.include_router(webhook_router, tags=["Webhooks"])
    app.include_router(stream_router, tags=["Streaming"])
    # Public deployed-apps surface — owns /a/{slug} at the root of the host.
    app.include_router(public_app_router, tags=["Public Apps"])

    # Root endpoints
    @app.get("/", response_model=RootResponse)
    async def root() -> RootResponse:
        """Root endpoint."""
        return RootResponse(
            name=settings.app_name,
            version=settings.app_version,
            status="running",
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint — verifies DB connectivity."""
        from sqlalchemy import text
        from .db.session import async_session_factory

        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
            return HealthResponse(status="healthy", version=settings.app_version)
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "version": settings.app_version},
            )

    return app


# Create app instance
app = create_app()


def main() -> None:
    """Run the server."""
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()

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
from .routes import api_router, webhook_router, stream_router, public_app_router, project_router
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

    # UI-DESIGN §10.3 / re-review N7: test mode only on a workflows_test_* DB; the test-only fake LLM
    # only when all six activation conditions hold (else RuntimeError — startup aborts). Both read
    # os.environ only, and run before anything touches the database.
    from .core import test_mode
    from .engine import fake_llm
    installed = fake_llm.install()
    test_mode.check_startup()
    if installed is not None:
        from .routes.test_hooks import router as test_hooks_router
        app.include_router(test_hooks_router)
        logger.warning("FAKE LLM INSTALLED (test mode, db=%s) — /api/_test/* is enabled", settings.db_name)

    # Initialize database tables
    await init_db()
    register_all_nodes()
    try:
        from .sdk.generate import sdk_dir
        sdk_dir()  # generate the studio SDK (nodes as functions) for this registry
    except Exception:
        logger.warning("studio SDK generation failed", exc_info=True)

    # Auto-seed demo workflows in dev. On by default; set SEED=0 to disable.
    if os.environ.get("SEED", "1").strip().lower() not in ("0", "false", "no"):
        from .db.seed import seed_workflows
        await seed_workflows(reset=False)
        logger.info("Dev seed applied")

    from .db.session import async_session_factory

    # Stage 8: fill the sub-workflow reference index once, when it is empty (idempotent; never blocks startup)
    try:
        from .services.workflow_refs import backfill_if_empty
        await backfill_if_empty(async_session_factory)
    except Exception:
        logger.warning("workflow_refs backfill failed", exc_info=True)

    # Capability graph: seed the ontology and the global capabilities (idempotent; WORKFLOW_KG_SEED=0 skips; a failure never blocks startup)
    if os.environ.get("WORKFLOW_KG_SEED", "1").strip().lower() not in ("0", "false", "no"):
        try:
            from .services import kg_extract
            kg_report = await kg_extract.seed_global()
            logger.info("Capability graph seeded (+%s new, %s changed)", kg_report.added, kg_report.updated)
        except Exception:
            logger.warning("capability graph seed failed (is the capability_graph migration applied?)", exc_info=True)

    from .engine.cron_scheduler import CronScheduler
    from .engine.stale_reaper import StaleReaper
    from .engine.retention_cleaner import RetentionCleaner
    from .engine import execution_registry

    # Start background services
    cron_scheduler = CronScheduler(async_session_factory)
    stale_reaper = StaleReaper(async_session_factory)
    retention_cleaner = RetentionCleaner(async_session_factory)

    # Stage 5: a resume that was running when the previous process died is never re-run (it could pay twice); it fails loudly.
    from .engine import task_resumer
    try:
        await task_resumer.startup_sweep(async_session_factory)
    except Exception:
        logger.warning("startup sweep of interrupted approvals failed (is the interfaces_tasks migration applied?)", exc_info=True)

    # Stage 5 6.2: chat completion hooks live for the whole process, so a run that waited on an approval still replies after a restart.
    from .services import chat_service
    if not chat_service.register_hooks():
        logger.warning("chat completion hooks not registered (ExecutionRepository.on_complete_hooks is missing)")

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

    # Review D7: ?dry_run=true on a write without a dry-run mode → 422 (never a silent real write).
    # Added before CORS so CORS stays the outermost middleware (its headers land on the 422 too).
    from .core.dry_run_guard import DryRunGuard
    app.add_middleware(DryRunGuard, fastapi_app=app)

    # UI-DESIGN §10.3 / N7: /api/_test/* from a non-loopback client is always a 404.
    from .routes.test_hooks import LoopbackOnlyTestPaths
    app.add_middleware(LoopbackOnlyTestPaths)
    # Audit #1: remember our own listening port so read-only broker egress can never call back in.
    from .core.self_address import RecordOwnPort
    app.add_middleware(RecordOwnPort)

    # CORS middleware. Review C1: the studio is cross-origin, so the headers it reads must be exposed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=["X-Next-Cursor", "X-Execution-Id", "ETag", "Allow"],
    )

    # Include routers. The project router (/api/p/...) goes first so it can't be shadowed.
    app.include_router(project_router, tags=["Project API"])
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

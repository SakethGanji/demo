"""Postgres async engine and session management."""

from __future__ import annotations

import logging
from typing import AsyncGenerator
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.infra.config import Settings, settings

logger = logging.getLogger(__name__)


def resolve_database_url(s: Settings) -> str:
    """Build the async Postgres database URL from settings."""
    password = s.db_password or ""
    return (
        f"postgresql+asyncpg://{s.db_user}:{quote_plus(password)}"
        f"@{s.db_host}:{s.db_port}"
        f"/{s.db_name}"
    )


DATABASE_URL = resolve_database_url(settings)

DB_SCHEMA = '"accelerator"'

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.debug,
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_timeout=30,
    pool_recycle=1800,
    connect_args={"server_settings": {"search_path": DB_SCHEMA}},
)

async_session_factory = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Test database connection on startup."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            logger.info("Postgres connection OK: %s", result.scalar())
    except Exception as e:
        logger.warning("Postgres not available (non-fatal): %s", e)


async def dispose_engine() -> None:
    """Cleanly close all pooled connections."""
    await engine.dispose()
    logger.info("Database engine disposed")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session."""
    async with async_session_factory() as session:
        yield session

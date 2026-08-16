"""Database session management."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import settings
from ..core.secrets import resolve_database_url

logger = logging.getLogger(__name__)

DATABASE_URL = resolve_database_url(settings)

DB_SCHEMA = '"workflow-app"'

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
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        logger.info("Postgres connection OK: %s", result.scalar())


async def dispose_engine() -> None:
    """Cleanly close all pooled connections."""
    await engine.dispose()
    logger.info("Database engine disposed")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session."""
    async with async_session_factory() as session:
        yield session

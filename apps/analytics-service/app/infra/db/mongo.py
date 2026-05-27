"""Async Mongo client lifecycle for PromptLab state.

Single module-level AsyncIOMotorClient, lazily created. ``init_mongo`` is
called at startup, ``dispose_mongo`` at shutdown. Other modules import
``get_db`` to access the database handle.
"""

from __future__ import annotations

import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.infra.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None


def get_client() -> AsyncIOMotorClient:
    """Return the process-wide Motor client. ``init_mongo`` must run first."""
    if _client is None:
        raise RuntimeError("Mongo client not initialised; call init_mongo() first")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    """Return the configured database handle."""
    return get_client()[settings.mongo_db]


async def init_mongo() -> None:
    """Create the client and verify connectivity with a ping."""
    global _client
    if _client is not None:
        return
    _client = AsyncIOMotorClient(
        settings.mongo_url,
        serverSelectionTimeoutMS=3000,
    )
    try:
        await _client.admin.command("ping")
        logger.info("Mongo connected at %s (db=%s)", settings.mongo_url, settings.mongo_db)
    except Exception:
        logger.exception("Mongo ping failed at %s", settings.mongo_url)
        # Keep the client around — individual ops will surface errors with
        # better context than a startup abort.


async def dispose_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None

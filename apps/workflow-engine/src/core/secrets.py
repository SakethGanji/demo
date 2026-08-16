"""NGC secret fetching and database URL resolution."""

from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from urllib.parse import quote_plus

from .config import Settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@lru_cache()
def _get_ngc_secret(secret_nickname: str, csiid: str) -> str:
    """Fetch a secret from NGC SecretAgent (cached after first call)."""
    cmd = f"ngc getSecret --secretNickname {secret_nickname} --csiid {csiid}"
    proc = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ngc getSecret failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    value = proc.stdout.strip()
    if not value:
        raise RuntimeError(f"ngc getSecret returned empty value for {secret_nickname}")
    return value


def _resolve_password(settings: Settings) -> str:
    """Resolve the Postgres password — from env or NGC secret manager."""
    if not settings.db_password_secret_name:
        return settings.db_password or ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            password = _get_ngc_secret(
                settings.db_password_secret_name,
                settings.ngc_csiid or "179492",
            )
            logger.info("PostgreSQL password fetched from NGC secret manager")
            return password
        except Exception as e:
            if attempt == MAX_RETRIES:
                logger.error("NGC secret fetch failed after %d attempts: %s", MAX_RETRIES, e)
                raise
            logger.warning("NGC secret fetch attempt %d failed, retrying...", attempt)


def resolve_database_url(settings: Settings) -> str:
    """Build the async Postgres database URL from settings."""
    password = _resolve_password(settings)
    return (
        f"postgresql+asyncpg://{settings.db_user}:{quote_plus(password)}"
        f"@{settings.db_host}:{settings.db_port}"
        f"/{settings.db_name}"
    )

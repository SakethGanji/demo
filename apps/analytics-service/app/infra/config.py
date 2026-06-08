"""Application configuration using pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="ACCELERATOR_",
    )

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8001
    reload: bool = True
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Application settings
    app_name: str = "Accelerator Service"
    app_version: str = "1.0.0"
    debug: bool = False

    # CORS settings
    cors_origins: list[str] = ["*"]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]

    # Database (Postgres)
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "accelerator"
    db_password: str | None = None
    db_name: str = "accelerator"

    # Storage
    storage_dir: Path = Path("/tmp/accelerator")  # base for datasets/, samples/
    tus_upload_dir: Path = Path("/tmp/accelerator/tus_uploads")  # local-only staging

    # S3 (set storage_backend="s3" to enable)
    storage_backend: str = "local"  # "local" or "s3"
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_region: str = "us-east-1"

    # Upload settings
    upload_chunk_size: int = 8 * 1024 * 1024  # 8MB

    # AI/LLM settings
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Company LLM proxy settings
    llm_proxy_base_url: str | None = None
    llm_proxy_project: str = ""
    ssl_cert_file: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()

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

    # API
    api_prefix: str = "/api/v1"  # single versioned mount point for all routes

    # Identity (POC): callers pass X-User-Id; RBAC is enforced server-side.
    # Set False only for local debugging (requests then run as the system superuser).
    auth_enabled: bool = True

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

    # S3 / object store (set storage_backend="s3" to enable).
    # Works against any S3-compatible endpoint: AWS, MinIO (dev), or an internal
    # S3 gateway — point s3_endpoint_url at it and set path-style as needed.
    # Credentials fall back to the standard AWS chain (env/instance profile)
    # when the explicit keys are unset.
    storage_backend: str = "local"  # "local" or "s3"
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None      # e.g. http://localhost:9000 (MinIO) or internal gateway
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_force_path_style: bool = True        # MinIO/internal gateways typically require path-style

    # Upload settings
    upload_chunk_size: int = 8 * 1024 * 1024  # 8MB
    max_upload_bytes: int = 1024 * 1024 * 1024  # 1GB cap for the simple upload path

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

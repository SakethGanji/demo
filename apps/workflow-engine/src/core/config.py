"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="WORKFLOW_",
    )

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Application settings
    app_name: str = "Workflow Engine"
    app_version: str = "0.1.0"
    debug: bool = False

    # CORS settings
    cors_origins: list[str] = ["*"]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]

    # Database (Postgres only)
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "workflow"
    db_password: str | None = None
    db_name: str = "workflows"

    # NGC secret management (optional — fetches password from NGC if set)
    db_password_secret_name: str | None = None
    ngc_csiid: str | None = "179492"

    # Credential encryption (Fernet key, generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    encryption_key: str | None = None

    # External services
    redis_url: str | None = None

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

"""Application configuration using pydantic-settings."""

import os
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        # UI-DESIGN §10.3 condition 6: WORKFLOW_DISABLE_DOTENV=1 → nothing can come from .env.
        env_file=None if os.environ.get("WORKFLOW_DISABLE_DOTENV") == "1" else ".env",
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

    # Public-app settings.
    # public_base_url is the externally-visible base where /a/{slug} is served
    # (e.g. https://apps.example.com). Used to build the URL returned from publish.
    # Falls back to the request's own scheme+host if unset.
    public_base_url: str | None = None
    # HMAC secret used to sign in-page tokens issued by /a/{slug}. If unset
    # the engine derives a per-process secret on startup — fine for single-pod
    # POC, but set this in production to keep tokens valid across restarts.
    public_token_secret: str | None = None

    # External services
    redis_url: str | None = None
    # The analytics service stores project datasets and serves Sample / Profile / Aggregate. Read from
    # ANALYTICS_SERVICE_URL (no WORKFLOW_ prefix), the name the data nodes have always used.
    analytics_service_url: str = Field(
        default="http://localhost:8001",
        validation_alias=AliasChoices("ANALYTICS_SERVICE_URL", "WORKFLOW_ANALYTICS_SERVICE_URL"),
    )

    # MongoDB (PromptLab session/run state)
    mongo_url: str | None = None

    # Outgoing email (the SendEmail node). Unset smtp_host = emails are rendered but not sent. For local development run the
    # mailpit service in docker-compose.yml: WORKFLOW_SMTP_HOST=localhost (port 1025), then read the mail at http://localhost:8025.
    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "studio@localhost"
    smtp_starttls: bool = False  # upgrade a plain connection with STARTTLS (typically port 587)
    smtp_ssl: bool = False  # implicit TLS from the first byte (typically port 465)
    smtp_timeout_s: float = 30.0
    # Where the studio runs: links in emails the engine sends (an Approval's approver notification) point here.
    studio_url: str = "http://localhost:5174"

    # AI/LLM settings
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Company LLM proxy settings
    llm_proxy_base_url: str | None = None
    llm_proxy_project: str = ""
    ssl_cert_file: str | None = None

    # POST /projects/{p}/modules/generate (UI-DESIGN §10.1)
    module_gen_model: str = "claude-sonnet-4-6"
    module_gen_timeout_s: float = 120.0
    # Optional provider/model override for the app builder (both agent tiers).
    app_builder_model: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()

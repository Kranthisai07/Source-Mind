"""
Application configuration loaded from environment variables.

Uses pydantic-settings for type-safe, validated configuration.
All secrets come from environment — never hardcoded.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment identifiers."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Structured log level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """
    SourceMind application settings.

    All fields are loaded from environment variables (case-insensitive).
    Sensitive fields are marked with repr=False to prevent accidental logging.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────
    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "SourceMind API"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:3001"]
    )

    # ── Database ──────────────────────────────────────────────────
    database_url: Annotated[str, Field(repr=False)] = Field(
        default="postgresql+asyncpg://sourcemind:sourcemind@localhost:5432/sourcemind"
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalise_database_driver(cls, value: str) -> str:
        """Force the async driver on a bare postgresql:// URL.

        Platforms that provision Postgres inject a driver-less
        `postgresql://...` URL. SQLAlchemy then defaults to psycopg2, and
        every create_async_engine() call fails with:

            InvalidRequestError: The asyncio extension requires an async
            driver to be used. The loaded 'psycopg2' is not async.

        Three modules build engines from this value — core/database.py,
        workers/ingestion.py and workers/connector_tasks.py — so normalising
        here fixes all of them at once. Doing it in only one of them is what
        let the API start cleanly while every Celery task died.

        alembic/env.py still rewrites +asyncpg to +psycopg2 for migrations,
        which continues to work because the scheme is now always explicit.
        """
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 1800

    # ── Redis ─────────────────────────────────────────────────────
    redis_url: Annotated[str, Field(repr=False)] = Field(
        default="redis://localhost:6379/0"
    )
    redis_pool_size: int = 20
    redis_decode_responses: bool = True
    embedding_cache_ttl: int = 60 * 60 * 24 * 30  # 30 days

    # ── Neo4j ─────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_auth: Annotated[str, Field(repr=False)] = "neo4j/sourcemind_dev"

    # ── Kafka / Redpanda ──────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "sourcemind-api"

    # ── AI / ML ───────────────────────────────────────────────────
    openai_api_key: Annotated[str, Field(repr=False)] = ""
    anthropic_api_key: Annotated[str, Field(repr=False)] = ""
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 3072
    anthropic_model: str = "claude-sonnet-4-6"

    # ── Auth (Clerk) ──────────────────────────────────────────────
    clerk_secret_key: Annotated[str, Field(repr=False)] = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = "https://api.clerk.com/v1/jwks"

    # ── AWS / S3 ──────────────────────────────────────────────────
    aws_access_key_id: Annotated[str, Field(repr=False)] = ""
    aws_secret_access_key: Annotated[str, Field(repr=False)] = ""
    aws_region: str = "us-east-1"
    s3_bucket_name: str = "sourcemind-dev"

    # ── Feature Flags ─────────────────────────────────────────────
    # Default off for optional infra (Neo4j, Kafka/Redpanda) so a missing
    # .env doesn't accidentally try to talk to services that aren't running.
    ff_conflict_detection: bool = True
    ff_neo4j_attribution: bool = False
    ff_kafka_events: bool = False

    # ── Rate Limiting ─────────────────────────────────────────────
    rate_limit_free: int = 100       # requests per minute
    rate_limit_pro: int = 1000
    rate_limit_enterprise: int = 10000

    # ── Sentry ────────────────────────────────────────────────────
    sentry_dsn: Annotated[str, Field(repr=False)] = ""
    sentry_traces_sample_rate: float = 0.1

    # ── GitHub App ────────────────────────────────────────────────
    github_app_id: str = Field(default="", repr=False)
    github_app_installation_id: str = Field(default="", repr=False)
    github_app_private_key_path: str = Field(default="", repr=False)
    github_webhook_secret: str = Field(default="", repr=False)
    github_research_pat: str = Field(default="", repr=False)

    # ── Slack ─────────────────────────────────────────────────────
    slack_bot_token: Annotated[str, Field(repr=False)] = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_signing_secret: Annotated[str, Field(repr=False)] = Field(default="", alias="SLACK_SIGNING_SECRET")
    slack_app_token: Annotated[str, Field(repr=False)] = Field(default="", alias="SLACK_APP_TOKEN")
    slack_default_workspace_id: str = Field(default="", alias="SLACK_DEFAULT_WORKSPACE_ID")

    # ── App URLs ──────────────────────────────────────────────────
    sourcemind_app_url: str = Field(default="https://app.sourcemind.ai", alias="SOURCEMIND_APP_URL")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Enforce required secrets in production."""
        if self.environment == Environment.PRODUCTION:
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required in production")
            if not self.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is required in production")
            if not self.clerk_secret_key:
                raise ValueError("CLERK_SECRET_KEY is required in production")
            if not self.sentry_dsn:
                raise ValueError("SENTRY_DSN is required in production")
        return self

    @property
    def is_development(self) -> bool:
        """True when running in local development mode."""
        return self.environment == Environment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        """True when running in production."""
        return self.environment == Environment.PRODUCTION

    @property
    def neo4j_user(self) -> str:
        """Extract Neo4j username from auth string 'user/password'."""
        return self.neo4j_auth.split("/")[0]

    @property
    def neo4j_password(self) -> str:
        """Extract Neo4j password from auth string 'user/password'."""
        return self.neo4j_auth.split("/", 1)[1]

    # Cached PEM content — populated lazily on first access by
    # `github_app_private_key`. Kept off the model schema with `exclude=True`.
    _github_app_private_key_cache: str | None = None

    @property
    def github_app_private_key(self) -> str:
        """Read and cache the GitHub App private key from disk."""
        cached = self._github_app_private_key_cache
        if cached is not None:
            return cached
        from pathlib import Path
        pem_path = Path(self.github_app_private_key_path)
        if not pem_path.exists():
            raise ValueError(f"GitHub App private key not found: {pem_path}")
        pem = pem_path.read_text()
        # Bypass Pydantic's frozen-instance check since this is purely a
        # memoization cache, not part of the settings schema.
        object.__setattr__(self, "_github_app_private_key_cache", pem)
        return pem


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings singleton.

    Using lru_cache ensures settings are loaded once and reused.
    In tests, call get_settings.cache_clear() to reset.
    """
    return Settings()

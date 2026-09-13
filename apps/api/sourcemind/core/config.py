"""
Application configuration loaded from environment variables.

Uses pydantic-settings for type-safe, validated configuration.
All secrets come from environment — never hardcoded.
"""

from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlsplit

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
    # noqa S104: binding all interfaces is required inside a container -
    # Railway routes to the service by its published port, and 127.0.0.1
    # would be unreachable from outside the container.
    host: str = "0.0.0.0"  # noqa: S104
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

    # ── Derived database URLs ─────────────────────────────────────
    # Every engine in the codebase must build its URL from one of these
    # three properties. Four modules used to derive it inline with slightly
    # different rules, and the two worker modules were missed when the
    # bare-postgresql:// normalisation was added — the API looked perfectly
    # healthy while every background task died. Duplication is what caused
    # that, so the logic lives here once.

    @property
    def async_database_url(self) -> str:
        """URL for create_async_engine(). asyncpg driver, no ssl query param.

        asyncpg rejects `?ssl=require` as a query parameter; SSL is passed
        through connect_args instead.
        """
        return (
            self.database_url
            .replace("?ssl=require", "")
            .replace("&ssl=require", "")
        )

    @property
    def sync_database_url(self) -> str:
        """URL for synchronous create_engine() — Alembic migrations."""
        return (
            self.async_database_url
            .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        )

    @property
    def requires_ssl(self) -> bool:
        """Whether the connection needs TLS.

        Railway's private network does not terminate TLS, so requiring SSL
        against a *.railway.internal host fails with "server does not support
        SSL". Everything else — public proxies, Supabase — does require it.
        """
        if "ssl=require" in self.database_url or "sslmode=require" in self.database_url:
            return True

        hostname = urlsplit(self.database_url).hostname
        if hostname is None or hostname.endswith(".railway.internal"):
            return False
        if hostname == "localhost":
            return False
        try:
            return not ip_address(hostname).is_loopback
        except ValueError:
            return True
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
    auth_dev_bypass_enabled: bool = False
    clerk_secret_key: Annotated[str, Field(repr=False)] = ""
    clerk_publishable_key: str = ""
    clerk_authorized_parties: list[str] = Field(default_factory=list)
    clerk_audience: str | None = None

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
    url_ingestion_enabled: bool = False
    url_ingestion_timeout_seconds: float = 15.0
    url_ingestion_max_redirects: int = 5
    url_ingestion_max_bytes: int = 2_000_000
    slack_memory_commands_enabled: bool = False

    # ── Rate Limiting ─────────────────────────────────────────────
    rate_limit_free: int = 100       # requests per minute
    rate_limit_pro: int = 1000
    rate_limit_enterprise: int = 10000
    rate_limit_fail_closed: bool = True
    rate_limit_search_per_minute: int = 60
    rate_limit_ingestion_per_minute: int = 10
    rate_limit_analytics_per_minute: int = 30
    rate_limit_workspace_create_per_hour: int = 5
    rate_limit_connector_sync_per_hour: int = 5
    rate_limit_slack_per_minute: int = 20

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
    slack_signing_secret: Annotated[str, Field(repr=False)] = Field(
        default="", alias="SLACK_SIGNING_SECRET"
    )
    slack_app_token: Annotated[str, Field(repr=False)] = Field(default="", alias="SLACK_APP_TOKEN")
    slack_installations: Annotated[
        dict[str, dict[str, object]], Field(repr=False)
    ] = Field(default_factory=dict, alias="SLACK_INSTALLATIONS")

    # ── App URLs ──────────────────────────────────────────────────
    sourcemind_app_url: str = Field(default="https://app.sourcemind.ai", alias="SOURCEMIND_APP_URL")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Enforce required secrets in production."""
        if self.auth_dev_bypass_enabled:
            if self.environment != Environment.DEVELOPMENT:
                raise ValueError(
                    "AUTH_DEV_BYPASS_ENABLED is permitted only in development"
                )
        else:
            if not self.clerk_secret_key:
                raise ValueError(
                    "CLERK_SECRET_KEY is required unless the explicit development-only "
                    "AUTH_DEV_BYPASS_ENABLED flag is true"
                )
            if not self.clerk_publishable_key:
                raise ValueError(
                    "CLERK_PUBLISHABLE_KEY is required unless the explicit development-only "
                    "AUTH_DEV_BYPASS_ENABLED flag is true"
                )
            if not self.clerk_authorized_parties:
                raise ValueError(
                    "CLERK_AUTHORIZED_PARTIES must list the frontend origins allowed "
                    "to mint session tokens"
                )

        if self.environment == Environment.PRODUCTION:
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required in production")
            if not self.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is required in production")
            if not self.clerk_secret_key:
                raise ValueError("CLERK_SECRET_KEY is required in production")
            # The publishable key is not optional despite the name: the JWKS
            # URL used to verify every token is derived from it. Unset, no
            # request can authenticate, and the only symptom is a confusing
            # 401 from the JWKS endpoint on every call. Fail at boot instead.
            if not self.clerk_publishable_key:
                raise ValueError(
                    "CLERK_PUBLISHABLE_KEY is required in production — the "
                    "Clerk JWKS URL for token verification is derived from it"
                )
            if not self.sentry_dsn:
                raise ValueError("SENTRY_DSN is required in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")

        if self.url_ingestion_timeout_seconds <= 0:
            raise ValueError("URL_INGESTION_TIMEOUT_SECONDS must be positive")
        if not 0 <= self.url_ingestion_max_redirects <= 10:
            raise ValueError("URL_INGESTION_MAX_REDIRECTS must be between 0 and 10")
        if not 1_024 <= self.url_ingestion_max_bytes <= 10_000_000:
            raise ValueError("URL_INGESTION_MAX_BYTES must be between 1024 and 10000000")
        if self.slack_memory_commands_enabled and not self.slack_installations:
            raise ValueError(
                "SLACK_INSTALLATIONS is required when Slack memory commands are enabled"
            )
        if self.slack_memory_commands_enabled and (
            not self.slack_bot_token or not self.slack_signing_secret
        ):
            raise ValueError(
                "SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET are required when Slack "
                "memory commands are enabled"
            )
        if any(
            limit <= 0
            for limit in (
                self.rate_limit_search_per_minute,
                self.rate_limit_ingestion_per_minute,
                self.rate_limit_analytics_per_minute,
                self.rate_limit_workspace_create_per_hour,
                self.rate_limit_connector_sync_per_hour,
                self.rate_limit_slack_per_minute,
            )
        ):
            raise ValueError("Operation-specific rate limits must be positive")
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

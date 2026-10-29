"""Settings guards for the graph projection pipeline."""

import os
from unittest.mock import patch

import pytest

from sourcemind.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


PRODUCTION = {
    "ENVIRONMENT": "production",
    "OPENAI_API_KEY": "test-key",
    "ANTHROPIC_API_KEY": "test-key",
    "CLERK_SECRET_KEY": "test-key",
    "CLERK_PUBLISHABLE_KEY": "pk_test_dGVzdC5leGFtcGxlJA",
    "CLERK_AUTHORIZED_PARTIES": '["https://app.example.com"]',
    "AUTH_DEV_BYPASS_ENABLED": "false",
    "SENTRY_DSN": "https://test@sentry.io/1",
}
GRAPH_ON = {
    "FF_NEO4J_ATTRIBUTION": "true",
    "FF_KAFKA_EVENTS": "true",
    "NEO4J_URI": "bolt://graph.internal:7687",
    "NEO4J_AUTH": "neo4j/not-the-default-credential",
    "KAFKA_BOOTSTRAP_SERVERS": "broker.internal:9092",
}


def _settings(**env: str) -> Settings:
    with patch.dict(os.environ, {**PRODUCTION, **env}):
        return Settings()


@pytest.mark.unit
def test_flags_default_off_so_nothing_changes_until_enabled() -> None:
    settings = Settings()
    assert settings.ff_neo4j_attribution is False
    assert settings.ff_kafka_events is False


@pytest.mark.unit
def test_production_with_both_flags_and_real_settings_is_accepted() -> None:
    assert _settings(**GRAPH_ON).ff_neo4j_attribution is True


@pytest.mark.unit
@pytest.mark.parametrize("only", ["FF_NEO4J_ATTRIBUTION", "FF_KAFKA_EVENTS"])
def test_production_rejects_half_a_pipeline(only: str) -> None:
    env = {**GRAPH_ON, "FF_NEO4J_ATTRIBUTION": "false", "FF_KAFKA_EVENTS": "false", only: "true"}
    with pytest.raises(ValueError, match="enabled together"):
        _settings(**env)


@pytest.mark.unit
def test_production_rejects_the_development_credential() -> None:
    with pytest.raises(ValueError, match="development default"):
        _settings(**{**GRAPH_ON, "NEO4J_AUTH": "neo4j/sourcemind_dev"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "value"),
    [("NEO4J_URI", "bolt://localhost:7687"), ("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")],
)
def test_production_rejects_localhost_endpoints(name: str, value: str) -> None:
    with pytest.raises(ValueError, match="localhost"):
        _settings(**{**GRAPH_ON, name: value})


@pytest.mark.unit
def test_graph_checks_do_not_apply_while_the_flags_are_off() -> None:
    assert _settings().ff_kafka_events is False  # default credential, localhost: allowed


@pytest.mark.unit
def test_neo4j_auth_without_a_slash_fails_at_boot_not_at_use() -> None:
    with patch.dict(os.environ, {"NEO4J_AUTH": "no-separator"}):
        with pytest.raises(ValueError, match="user/password"):
            Settings()


@pytest.mark.unit
@pytest.mark.parametrize(
    "env",
    [
        {"NEO4J_QUERY_TIMEOUT_SECONDS": "0"},
        {"GRAPH_RELAY_BATCH_SIZE": "0"},
        {"GRAPH_RELAY_POLL_SECONDS": "0"},
        {"GRAPH_RELAY_SETTLE_SECONDS": "-1"},
        {"GRAPH_PROJECTOR_MAX_ATTEMPTS": "0"},
    ],
)
def test_pipeline_limits_must_be_in_range(env: dict[str, str]) -> None:
    with patch.dict(os.environ, env):
        with pytest.raises(ValueError):
            Settings()


@pytest.mark.unit
def test_relay_database_url_is_not_printed() -> None:
    with patch.dict(os.environ, {"GRAPH_RELAY_DATABASE_URL": "postgresql://relay:secret-pw@h/db"}):
        assert "secret-pw" not in repr(Settings())

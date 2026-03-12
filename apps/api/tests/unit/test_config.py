"""
Unit tests for application configuration.

Tests validate that settings load correctly from environment variables
and that production validations work.
"""

import os
import pytest
from unittest.mock import patch

from sourcemind.core.config import Environment, Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Clear the lru_cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.unit
def test_default_settings_load() -> None:
    """Default settings should load without errors in development mode."""
    settings = Settings()
    assert settings.environment == Environment.DEVELOPMENT
    assert settings.app_name == "SourceMind API"


@pytest.mark.unit
def test_environment_from_env_var() -> None:
    """ENVIRONMENT env var should override the default."""
    with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
        settings = Settings()
        assert settings.environment == Environment.STAGING


@pytest.mark.unit
def test_production_requires_openai_key() -> None:
    """Production environment must have OPENAI_API_KEY set."""
    env_vars = {
        "ENVIRONMENT": "production",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "test-key",
        "CLERK_SECRET_KEY": "test-key",
        "SENTRY_DSN": "https://test@sentry.io/1",
    }
    with patch.dict(os.environ, env_vars):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            Settings()


@pytest.mark.unit
def test_is_development_property() -> None:
    """is_development should be True only for development environment."""
    settings = Settings()
    assert settings.is_development is True
    assert settings.is_production is False


@pytest.mark.unit
def test_neo4j_auth_parsing() -> None:
    """neo4j_user and neo4j_password should parse the NEO4J_AUTH string."""
    with patch.dict(os.environ, {"NEO4J_AUTH": "myuser/mypassword"}):
        settings = Settings()
        assert settings.neo4j_user == "myuser"
        assert settings.neo4j_password == "mypassword"

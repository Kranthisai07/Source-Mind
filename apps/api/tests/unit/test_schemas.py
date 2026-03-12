"""
Unit tests for Pydantic v2 schemas.

Tests validate both happy-path and error cases for all input schemas.
No external dependencies required.
"""

import pytest
from pydantic import ValidationError

from sourcemind.schemas.memory import MemoryCreate, MemoryUpdate, SearchRequest, SearchMode
from sourcemind.schemas.workspace import WorkspaceCreate


# ─── MemoryCreate ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_memory_create_with_content() -> None:
    """MemoryCreate with content field is valid."""
    m = MemoryCreate(content="The deployment target is EKS with Istio service mesh.")
    assert m.content == "The deployment target is EKS with Istio service mesh."
    assert m.url is None


@pytest.mark.unit
def test_memory_create_with_url() -> None:
    """MemoryCreate with url field is valid."""
    m = MemoryCreate(url="https://example.com/docs")
    assert m.url == "https://example.com/docs"
    assert m.content is None


@pytest.mark.unit
def test_memory_create_requires_content_or_url() -> None:
    """MemoryCreate without content or url raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        MemoryCreate()
    errors = exc_info.value.errors()
    assert any("content" in str(e) or "url" in str(e) for e in errors)


@pytest.mark.unit
def test_memory_create_rejects_both_content_and_url() -> None:
    """MemoryCreate with both content and url raises ValidationError."""
    with pytest.raises(ValidationError):
        MemoryCreate(
            content="Some content",
            url="https://example.com",
        )


@pytest.mark.unit
def test_memory_create_strips_whitespace() -> None:
    """str_strip_whitespace should trim leading/trailing whitespace."""
    m = MemoryCreate(content="  hello world  ")
    assert m.content == "hello world"


# ─── WorkspaceCreate ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_workspace_create_valid_slug() -> None:
    """Valid slugs must be lowercase alphanumeric with hyphens."""
    ws = WorkspaceCreate(name="Engineering Team", slug="engineering-team")
    assert ws.slug == "engineering-team"


@pytest.mark.unit
@pytest.mark.parametrize("invalid_slug", [
    "Engineering-Team",   # uppercase
    "engineering team",   # space
    "-engineering",       # leading hyphen
    "engineering-",       # trailing hyphen
    "eng--team",          # double hyphen
    "eng@team",           # special char
])
def test_workspace_create_invalid_slug(invalid_slug: str) -> None:
    """Invalid slugs raise ValidationError with helpful message."""
    with pytest.raises(ValidationError):
        WorkspaceCreate(name="Test", slug=invalid_slug)


# ─── SearchRequest ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_search_request_defaults() -> None:
    """SearchRequest should default to hybrid mode with limit=10."""
    req = SearchRequest(query="deployment strategy")
    assert req.mode == SearchMode.HYBRID
    assert req.limit == 10
    assert req.min_similarity == 0.3


@pytest.mark.unit
def test_search_request_limit_clamped() -> None:
    """limit must be between 1 and 100."""
    with pytest.raises(ValidationError):
        SearchRequest(query="test", limit=200)
    with pytest.raises(ValidationError):
        SearchRequest(query="test", limit=0)

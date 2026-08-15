"""Unit tests for GitHubMapper — raw API payload → ConnectorDocument."""

from __future__ import annotations

import pytest

from sourcemind.connectors.github.mapper import ConnectorDocument, GitHubMapper


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def raw_commit():
    return {
        "sha": "abc123def456789012345678901234567890abcd",
        "html_url": "https://github.com/acme/repo/commit/abc123",
        "commit": {
            "message": "fix: resolve null pointer in auth middleware\n\nFixes #42",
            "author": {"name": "Alice", "date": "2024-03-01T12:00:00Z"},
        },
        "author": {"login": "alice"},
    }


@pytest.fixture
def raw_pr():
    return {
        "number": 101,
        "title": "feat: add OAuth2 support",
        "body": "This PR adds OAuth2 support to the auth service.",
        "html_url": "https://github.com/acme/repo/pull/101",
        "state": "closed",
        "merged_at": "2024-03-05T10:00:00Z",
        "created_at": "2024-03-01T09:00:00Z",
        "updated_at": "2024-03-05T10:00:00Z",
        "user": {"login": "bob"},
        "base": {"ref": "main"},
        "head": {"ref": "feature/oauth2"},
        "labels": [{"name": "enhancement"}],
    }


@pytest.fixture
def raw_issue():
    return {
        "number": 55,
        "title": "Bug: login fails on Safari",
        "body": "Steps to reproduce: ...",
        "html_url": "https://github.com/acme/repo/issues/55",
        "state": "open",
        "created_at": "2024-02-28T08:00:00Z",
        "updated_at": "2024-02-28T08:00:00Z",
        "user": {"login": "carol"},
        "labels": [{"name": "bug"}],
        "assignees": [],
    }


@pytest.fixture
def raw_discussion():
    return {
        "number": 10,
        "title": "RFC: migration to async workers",
        "body": "We should consider moving to async workers for better throughput.",
        "url": "https://github.com/acme/repo/discussions/10",
        "createdAt": "2024-01-15T07:00:00Z",
        "updatedAt": "2024-01-20T12:00:00Z",
        "author": {"login": "dave"},
        "category": {"name": "Ideas"},
        "comments": {
            "nodes": [
                {"body": "Agreed!", "author": {"login": "alice"}},
            ]
        },
    }


# ── Commit tests ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_commit_source_fields(raw_commit):
    doc = GitHubMapper.from_commit("acme", "repo", raw_commit)
    assert doc.source_tool == "github"
    assert doc.source_type == "commit"
    assert "abc123def456" in doc.source_id
    assert doc.source_author == "alice"
    assert doc.source_url == "https://github.com/acme/repo/commit/abc123"


@pytest.mark.unit
def test_commit_content_contains_message(raw_commit):
    doc = GitHubMapper.from_commit("acme", "repo", raw_commit)
    assert "resolve null pointer" in doc.content
    assert "acme/repo" in doc.content


@pytest.mark.unit
def test_commit_idempotency_key_deterministic(raw_commit):
    doc1 = GitHubMapper.from_commit("acme", "repo", raw_commit)
    doc2 = GitHubMapper.from_commit("acme", "repo", raw_commit)
    assert doc1.idempotency_key == doc2.idempotency_key
    assert len(doc1.idempotency_key) == 64  # SHA-256 hex


# ── PR tests ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_pr_source_fields(raw_pr):
    doc = GitHubMapper.from_pull_request("acme", "repo", raw_pr)
    assert doc.source_type == "pull_request"
    assert doc.source_id == "acme/repo/pull/101"
    assert doc.source_author == "bob"


@pytest.mark.unit
def test_pr_merged_state_reflected(raw_pr):
    doc = GitHubMapper.from_pull_request("acme", "repo", raw_pr)
    assert doc.metadata["merged"] is True
    assert "merged" in doc.content


@pytest.mark.unit
def test_pr_labels_in_metadata(raw_pr):
    doc = GitHubMapper.from_pull_request("acme", "repo", raw_pr)
    assert "enhancement" in doc.metadata["labels"]


# ── Issue tests ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_issue_excludes_pull_requests():
    """from_issue should not receive PR-like payloads (no pull_request key)."""
    raw = {
        "number": 1,
        "title": "test",
        "body": "",
        "html_url": "https://github.com/a/b/issues/1",
        "state": "open",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "user": {"login": "x"},
        "labels": [],
        "assignees": [],
    }
    doc = GitHubMapper.from_issue("a", "b", raw)
    assert doc.source_type == "issue"
    assert doc.source_id == "a/b/issues/1"


@pytest.mark.unit
def test_issue_content_contains_title(raw_issue):
    doc = GitHubMapper.from_issue("acme", "repo", raw_issue)
    assert "Bug: login fails on Safari" in doc.content


# ── Discussion tests ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_discussion_source_fields(raw_discussion):
    doc = GitHubMapper.from_discussion("acme", "repo", raw_discussion)
    assert doc.source_type == "discussion"
    assert doc.source_id == "acme/repo/discussions/10"
    assert doc.source_author == "dave"


@pytest.mark.unit
def test_discussion_comments_in_content(raw_discussion):
    doc = GitHubMapper.from_discussion("acme", "repo", raw_discussion)
    assert "Agreed!" in doc.content
    assert "alice" in doc.content


@pytest.mark.unit
def test_discussion_category_in_metadata(raw_discussion):
    doc = GitHubMapper.from_discussion("acme", "repo", raw_discussion)
    assert doc.metadata["category"] == "Ideas"


# ── ConnectorDocument idempotency key ─────────────────────────────────────────

@pytest.mark.unit
def test_make_key_is_sha256_hex():
    key = ConnectorDocument.make_key("github", "commit", "acme/repo/commits/abc123")
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


@pytest.mark.unit
def test_make_key_different_ids_produce_different_keys():
    k1 = ConnectorDocument.make_key("github", "commit", "id_a")
    k2 = ConnectorDocument.make_key("github", "commit", "id_b")
    assert k1 != k2

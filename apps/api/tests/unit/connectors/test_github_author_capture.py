"""GitHub author capture: the mapper keeps GitHub's NUMERIC user id.

Attribution of synced artifacts resolves only through an admin-asserted link
keyed by that numeric id (D-021). A login, display name or e-mail is a label
and is never used to resolve a SourceMind user, so the mapper must record the
id when GitHub supplies one and must mark the git display-name fallback as
non-resolvable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sourcemind
from sourcemind.connectors.github.mapper import GitHubMapper

API_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.unit
def test_sourcemind_resolves_inside_this_checkout() -> None:
    """Guard against the stale editable install shadowing the code under test."""
    assert Path(sourcemind.__file__).resolve().is_relative_to(API_ROOT)


def _commit(author: dict | None, name: str = "Alice Display") -> dict:
    return {
        "sha": "a" * 40,
        "html_url": "https://github.com/acme/repo/commit/" + "a" * 40,
        "commit": {
            "author": {"name": name, "email": "alice@example.com", "date": "2026-01-01T00:00:00Z"},
            "message": "Fix the thing\n\nbody",
        },
        "author": author,
    }


@pytest.mark.unit
def test_commit_with_linked_account_records_numeric_id() -> None:
    doc = GitHubMapper.from_commit("acme", "repo", _commit({"login": "alice", "id": 101}))

    assert doc.metadata["author"] == {
        "github_user_id": 101,
        "login": "alice",
        "kind": "github_account",
    }
    assert doc.source_author == "alice"


@pytest.mark.unit
def test_commit_without_account_falls_back_to_unresolvable_git_name() -> None:
    doc = GitHubMapper.from_commit("acme", "repo", _commit(None, name="Alice Display"))

    assert doc.metadata["author"] == {
        "github_user_id": None,
        "login": None,
        "kind": "git_name",
    }
    # The display name is still shown as the source author, but carries no id.
    assert doc.source_author == "Alice Display"


@pytest.mark.unit
@pytest.mark.parametrize("bad_id", [None, 0, -5, "101", True])
def test_non_positive_or_non_integer_ids_are_not_recorded(bad_id: object) -> None:
    doc = GitHubMapper.from_commit("acme", "repo", _commit({"login": "alice", "id": bad_id}))

    assert doc.metadata["author"]["github_user_id"] is None
    assert doc.metadata["author"]["kind"] != "github_account"


@pytest.mark.unit
def test_pull_request_records_numeric_id() -> None:
    raw = {"number": 7, "title": "T", "user": {"login": "bob", "id": 202}, "state": "open"}

    simple = GitHubMapper.from_pull_request("acme", "repo", raw)
    enriched = GitHubMapper.pull_request_to_document(raw, "acme", "repo", "ws")

    for doc in (simple, enriched):
        assert doc.metadata["author"] == {
            "github_user_id": 202,
            "login": "bob",
            "kind": "github_account",
        }


@pytest.mark.unit
def test_issue_records_numeric_id() -> None:
    raw = {"number": 9, "title": "T", "user": {"login": "carol", "id": 303}, "state": "open"}

    doc = GitHubMapper.from_issue("acme", "repo", raw)

    assert doc.metadata["author"] == {
        "github_user_id": 303,
        "login": "carol",
        "kind": "github_account",
    }


@pytest.mark.unit
def test_discussion_author_stays_without_numeric_id() -> None:
    """GraphQL asks only for author { login }; no id is invented from it."""
    raw = {"number": 3, "title": "T", "author": {"login": "dave"}}

    doc = GitHubMapper.from_discussion("acme", "repo", raw)

    assert (doc.metadata.get("author") or {}).get("github_user_id") is None

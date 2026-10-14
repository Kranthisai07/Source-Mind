"""Per-segment mapping (contract C1): authorship, filters, verbatim routing.

Mapper-level tests only. T-A* are limited to what the mapper decides (who a segment's
author reference is); resolution against workspace links lives in the receiver tests.
"""

from __future__ import annotations

import hashlib
import unicodedata

import pytest

from sourcemind.connectors.github.mapper import (
    GitHubMapper,
    content_revision,
    decide_ingestion_mode,
    skip_bots,
    skip_empty_body,
)

OWN, R = "acme", "repo"


def _user(login: str, uid: int | None, type_: str = "User") -> dict:
    return {"login": login, "id": uid, "type": type_}


def _pr() -> dict:
    return {
        "number": 7,
        "title": "Add cache",
        "body": "opener text",
        "user": _user("opener", 1),
        "state": "open",
        "html_url": "https://github.com/acme/repo/pull/7",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ── T-A1: each segment attributed only to its own author ─────────────────────


@pytest.mark.unit
def test_ta1_opener_two_reviewers_one_commenter_give_four_separately_attributed_docs():
    pr = GitHubMapper.from_pull_request(OWN, R, _pr())
    rv1 = GitHubMapper.from_pull_review(
        OWN, R, 7, {"id": 11, "user": _user("rev1", 2), "body": "r1", "state": "COMMENTED"},
        pr_title="Add cache",
    )
    rv2 = GitHubMapper.from_pull_review(
        OWN, R, 7, {"id": 12, "user": _user("rev2", 3), "body": "r2", "state": "APPROVED"},
        pr_title="Add cache",
    )
    cm = GitHubMapper.from_issue_comment(
        OWN, R, {"id": 13, "user": _user("talker", 4), "body": "c", "issue_url": "x/issues/7"}
    )
    docs = [pr, rv1, rv2, cm]
    assert [d.metadata["author"]["github_user_id"] for d in docs] == [1, 2, 3, 4]
    assert [d.content for d in docs] == ["opener text", "r1", "r2", "c"]
    assert len({d.source_id for d in docs}) == 4
    assert pr.source_id == "acme/repo/pull/7"
    assert rv1.source_id == "acme/repo/pull/7/review/11"
    assert cm.source_id == "acme/repo/issues/7/comment/13"
    for d in docs:  # no other author's name leaks into another's text
        assert "opener" not in d.content or d is pr


@pytest.mark.unit
def test_review_comment_source_id_and_number_from_url():
    d = GitHubMapper.from_pull_review_comment(
        OWN, R,
        {"id": 21, "user": _user("x", 9), "body": "nit",
         "pull_request_url": "https://api.github.com/repos/acme/repo/pulls/7"},
    )
    assert d.source_type == "pull_review_comment"
    assert d.source_id == "acme/repo/pull/7/review_comment/21"


# ── T-A2: unresolved stays unresolved ────────────────────────────────────────


@pytest.mark.unit
def test_ta2_author_without_numeric_id_is_unresolved_and_not_matched_by_login():
    d = GitHubMapper.from_issue_comment(
        OWN, R, {"id": 1, "user": {"login": "ghost"}, "body": "hi", "issue_url": "x/issues/3"}
    )
    assert d.authorship == "unresolved"
    assert d.metadata["author"]["github_user_id"] is None
    assert d.metadata["author"]["kind"] != "github_account"


# ── T-A3: shared commit ──────────────────────────────────────────────────────


def _commit(message: str, author=None, name="Alice") -> dict:
    return {
        "sha": "b" * 40,
        "html_url": "u",
        "commit": {"message": message, "author": {"name": name, "date": "2026-01-01T00:00:00Z"}},
        "author": author,
    }


@pytest.mark.unit
def test_ta3_co_authored_commit_is_shared_with_no_single_author_ref():
    msg = "Fix it\n\nCo-authored-by: Bob Builder <bob@example.com>\nCo-authored-by: Carol <c@x.io>"
    d = GitHubMapper.from_commit(OWN, R, _commit(msg, _user("alice", 5)))
    assert d.authorship == "shared"
    assert d.source_authors == ["alice", "Bob Builder", "Carol"]
    assert "author" not in d.metadata
    assert d.source_author is None
    assert d.content == msg  # exact text, trailers included


@pytest.mark.unit
def test_commit_attributes_author_not_committer():
    raw = _commit("msg", _user("alice", 5))
    raw["committer"] = _user("web-flow", 999)
    d = GitHubMapper.from_commit(OWN, R, raw)
    assert d.metadata["author"]["github_user_id"] == 5
    assert d.authorship == "single"


@pytest.mark.unit
def test_commit_without_account_is_unresolved_git_name():
    d = GitHubMapper.from_commit(OWN, R, _commit("msg", None, name="Alice D"))
    assert d.authorship == "unresolved"
    assert d.metadata["author"]["github_user_id"] is None


# ── T-A4/A5 substitutes: no connector-creator concept in the mapper ──────────


@pytest.mark.unit
def test_mapper_has_no_notion_of_the_connecting_user():
    import inspect

    sig = inspect.signature(GitHubMapper.from_issue)
    assert not any("user_id" in p or "creator" in p for p in sig.parameters)


@pytest.mark.unit
def test_edited_comment_same_source_id_new_revision():
    base = {"id": 5, "user": _user("x", 9), "issue_url": "a/issues/2"}
    a = GitHubMapper.from_issue_comment(OWN, R, {**base, "body": "v1"})
    b = GitHubMapper.from_issue_comment(OWN, R, {**base, "body": "v2"})
    assert a.source_id == b.source_id and a.idempotency_key == b.idempotency_key
    assert a.source_revision != b.source_revision
    assert a.metadata["author"] == b.metadata["author"]


# ── Content purity ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ts7_context_labels_are_never_in_content():
    d = GitHubMapper.from_pull_request(OWN, R, _pr())
    assert d.content == "opener text"
    for label in ("acme/repo", "Add cache", "#7", "github.com"):
        assert label not in d.content
    assert d.context["title"] == "Add cache" and d.context["repo"] == "acme/repo"
    assert d.context["number"] == 7 and d.context["kind"] == "pull_request"


# ── T-S1: verbatim boundary ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("delta", "mode"), [(-1, "verbatim"), (0, "verbatim"), (1, "extract")]
)
def test_ts1_boundary_max_minus_one_max_plus_one(delta, mode):
    n = 200 + delta
    assert decide_ingestion_mode("x" * n, 200) == mode
    d = GitHubMapper.from_issue(
        OWN, R, {"number": 1, "title": "t", "body": "x" * n, "user": _user("u", 1), "state": "open"},
        verbatim_max_chars=200,
    )
    assert d.ingestion_mode == mode


@pytest.mark.unit
def test_ts2_exact_text_preserved_and_revision_is_sha256_of_bytes():
    body = "  Keep\r\n whitespace \t exactly.  \n"
    d = GitHubMapper.from_issue(
        OWN, R, {"number": 1, "title": "t", "body": body, "user": _user("u", 1), "state": "open"}
    )
    assert d.content == body
    assert d.source_revision == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert d.source_revision == content_revision(body)


@pytest.mark.unit
def test_ts8_decomposed_unicode_is_stored_unchanged_and_routed_by_nfc_length():
    decomposed = "é" * 100  # 200 code points; NFC length is 100
    assert len(decomposed) == 200 and len(unicodedata.normalize("NFC", decomposed)) == 100
    # Boundary: NFC length 100 <= 100 -> verbatim even though raw length is 200.
    d = GitHubMapper.from_issue(
        OWN, R,
        {"number": 1, "title": "t", "body": decomposed, "user": _user("u", 1), "state": "open"},
        verbatim_max_chars=100,
    )
    assert d.ingestion_mode == "verbatim"
    assert d.content == decomposed
    assert d.content != unicodedata.normalize("NFC", decomposed)
    assert d.content.encode("utf-8") == decomposed.encode("utf-8")
    # One more normalized character crosses the boundary.
    over = GitHubMapper.from_issue(
        OWN, R,
        {"number": 1, "title": "t", "body": decomposed + "x", "user": _user("u", 1),
         "state": "open"},
        verbatim_max_chars=100,
    )
    assert over.ingestion_mode == "extract"


# ── Filters ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_skip_bots_reason():
    assert skip_bots({"type": "Bot"}) == "skip_bots"
    assert skip_bots({"type": "User"}) is None
    assert skip_bots(None) is None


@pytest.mark.unit
@pytest.mark.parametrize("body", ["", "   ", "\n\t ", None])
def test_skip_empty_body_reason(body):
    assert skip_empty_body(body) == "skip_empty_body"


@pytest.mark.unit
def test_no_minimum_length_filter():
    assert skip_empty_body("k") is None
    assert skip_empty_body("ok") is None

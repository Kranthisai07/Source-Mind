"""GitHub artifact mapper: raw API payloads to per-segment ConnectorDocuments (C1).

One authored segment = one document attributed only to its own author. ``content``
is the EXACT authored text; repo / number / title / kind / url live in ``context``
and ``title`` and are never part of the attributed text.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_VERBATIM_MAX_CHARS = 200

_CO_AUTHOR_RE = re.compile(
    r"^\s*co-authored-by:\s*(?P<name>[^<\r\n]*?)\s*(?:<[^>\r\n]*>)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TRAILING_INT_RE = re.compile(r"/(\d+)/?$")


class ConnectorDocument(BaseModel):
    """Normalized document produced by any connector before ingestion.

    The ``idempotency_key`` is a deterministic SHA-256 hash over the tuple
    ``(source_tool, source_type, source_id)`` so duplicate sync runs never
    create duplicate memories.
    """

    source_tool: str = Field(description="Connector type, e.g. 'github'")
    source_type: str = Field(description="Artifact type, e.g. 'commit', 'pull_request'")
    source_id: str = Field(description="Stable external ID for dedup")
    source_url: str | None = Field(default=None)
    source_author: str | None = Field(default=None)
    source_timestamp: datetime | None = Field(default=None)
    title: str = Field(description="Short human-readable title")
    content: str = Field(description="Exact authored text of this one segment")
    idempotency_key: str = Field(description="SHA-256(source_tool+source_type+source_id)")
    memory_type: str = Field(
        default="", description="Semantic memory category (e.g. 'technical_decision')"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Searchable labels derived from artifact metadata",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-authored labels (repo, number, title, kind, url); never content",
    )
    authorship: Literal["single", "shared", "unresolved"] = "single"
    source_authors: list[str] = Field(
        default_factory=list, description="All named authors when authorship is 'shared'"
    )
    ingestion_mode: Literal["extract", "verbatim"] = "extract"
    source_revision: str = Field(
        default="", description="sha256 hex of the exact UTF-8 content bytes"
    )

    @classmethod
    def make_key(cls, source_tool: str, source_type: str, source_id: str) -> str:
        """Compute the deterministic SHA-256 idempotency key."""
        raw = json.dumps([source_tool, source_type, source_id], separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


# ── Pure helpers ──────────────────────────────────────────────────────────────


def content_revision(content: str) -> str:
    """sha256 hex of the exact content bytes (UTF-8, as stored)."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def decide_ingestion_mode(
    content: str, max_chars: int = DEFAULT_VERBATIM_MAX_CHARS
) -> Literal["extract", "verbatim"]:
    """``verbatim`` when the NFC-normalized length is <= *max_chars*, else ``extract``.

    The normalized copy is used only for this length decision; callers keep and
    store the original *content* unchanged.
    """
    if len(unicodedata.normalize("NFC", content)) <= max_chars:
        return "verbatim"
    return "extract"


def skip_bots(user: dict[str, Any] | None) -> str | None:
    """``'skip_bots'`` when the author account is a Bot, else ``None``."""
    if isinstance(user, dict) and user.get("type") == "Bot":
        return "skip_bots"
    return None


def skip_empty_body(body: str | None) -> str | None:
    """``'skip_empty_body'`` for an empty or whitespace-only body, else ``None``."""
    if body is None or not body.strip():
        return "skip_empty_body"
    return None


def parse_co_authors(message: str) -> list[str]:
    """Names from ``Co-authored-by:`` trailers, in order, de-duplicated."""
    names: list[str] = []
    for match in _CO_AUTHOR_RE.finditer(message or ""):
        name = match.group("name").strip()
        if name and name not in names:
            names.append(name)
    return names


def number_from_url(url: str | None) -> int | None:
    """Trailing integer of an API URL (``.../issues/12`` -> 12)."""
    match = _TRAILING_INT_RE.search(url or "")
    return int(match.group(1)) if match else None


class GitHubMapper:
    """Maps raw GitHub API payloads to per-segment :class:`ConnectorDocument` s."""

    TOOL = "github"

    @classmethod
    def _build(
        cls,
        *,
        source_type: str,
        source_id: str,
        content: str,
        title: str,
        context: dict[str, Any],
        verbatim_max_chars: int,
        source_url: str | None,
        source_author: str | None,
        timestamp: datetime | None,
        metadata: dict[str, Any],
        tags: list[str] | None = None,
        authorship: Literal["single", "shared", "unresolved"] = "single",
        source_authors: list[str] | None = None,
    ) -> ConnectorDocument:
        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            source_author=source_author,
            source_timestamp=timestamp,
            title=title,
            content=content,
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, source_type, source_id),
            tags=tags or [],
            metadata=metadata,
            context=context,
            authorship=authorship,
            source_authors=source_authors or [],
            ingestion_mode=decide_ingestion_mode(content, verbatim_max_chars),
            source_revision=content_revision(content),
        )

    @staticmethod
    def _author_fields(user: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
        ref = _author_ref(user)
        return (user or {}).get("login"), ref

    # ── Commits ───────────────────────────────────────────────────────────────

    @classmethod
    def from_commit(
        cls,
        owner: str,
        repo: str,
        raw: dict[str, Any],
        *,
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map a commit. Author = the commit AUTHOR (never the committer).

        A message with ``Co-authored-by:`` trailers is ``shared``: no single author
        ref is recorded, so nothing is credited.
        """
        sha = raw["sha"]
        commit = raw.get("commit") or {}
        git_author = commit.get("author") or {}
        account = raw.get("author") or {}
        message: str = commit.get("message", "") or ""
        source_id = f"{owner}/{repo}/commits/{sha}"
        title_line = message.split("\n")[0][:200]
        context = {
            "repo": f"{owner}/{repo}",
            "sha": sha,
            "kind": "commit",
            "url": raw.get("html_url"),
        }
        metadata: dict[str, Any] = {
            "sha": sha,
            "repo": f"{owner}/{repo}",
            "files_changed": len(raw.get("files", []) or []),
        }
        timestamp = _parse_dt(git_author.get("date"))
        co_authors = parse_co_authors(message)
        if co_authors:
            primary = account.get("login") or git_author.get("name")
            names = [primary, *co_authors] if primary else list(co_authors)
            names = list(dict.fromkeys(names))
            metadata["source_authors"] = names
            # Readers (insights SQL, memory read path) look for this key in the STORED
            # link metadata; the ConnectorDocument.authorship field is never persisted.
            metadata["authorship"] = "shared"
            return cls._build(
                source_type="commit",
                source_id=source_id,
                content=message,
                title=f"[{owner}/{repo}] {title_line}",
                context=context,
                verbatim_max_chars=verbatim_max_chars,
                source_url=raw.get("html_url"),
                source_author=None,
                timestamp=timestamp,
                metadata=metadata,
                authorship="shared",
                source_authors=names,
            )

        author_ref = _author_ref(account)
        login = account.get("login") or git_author.get("name")
        if author_ref["kind"] != "github_account" and git_author.get("name"):
            # A git display name is a label only. It is never resolvable to a
            # SourceMind user (D-021), so no id and no login are recorded.
            author_ref = {"github_user_id": None, "login": None, "kind": "git_name"}
        metadata["author"] = author_ref
        return cls._build(
            source_type="commit",
            source_id=source_id,
            content=message,
            title=f"[{owner}/{repo}] {title_line}",
            context=context,
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=timestamp,
            metadata=metadata,
            authorship="single" if author_ref["kind"] == "github_account" else "unresolved",
        )

    # ── Pull requests ────────────────────────────────────────────────────────

    @classmethod
    def from_pull_request(
        cls,
        owner: str,
        repo: str,
        raw: dict[str, Any],
        *,
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map a pull request BODY (author = the opener)."""
        number = raw["number"]
        title = raw.get("title", "") or ""
        login, ref = cls._author_fields(raw.get("user"))
        merged = raw.get("merged_at") is not None
        state = raw.get("state", "unknown")
        labels = [lbl["name"] for lbl in raw.get("labels", []) or []]
        return cls._build(
            source_type="pull_request",
            source_id=f"{owner}/{repo}/pull/{number}",
            content=raw.get("body") or "",
            title=f"[{owner}/{repo}] PR #{number}: {title[:150]}",
            context={
                "repo": f"{owner}/{repo}",
                "number": number,
                "title": title,
                "kind": "pull_request",
                "url": raw.get("html_url"),
            },
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "state": state,
                "merged": merged,
                "labels": labels,
                "author": ref,
            },
            tags=[*labels, "merged" if merged else state, f"repo:{owner}/{repo}"],
            authorship="single" if ref["kind"] == "github_account" else "unresolved",
        )

    @classmethod
    def pull_request_to_document(
        cls,
        raw: dict[str, Any],
        owner: str,
        repo: str,
        workspace_id: Any = None,
        comments: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        review_comments: list[dict[str, Any]] | None = None,
    ) -> ConnectorDocument:
        """Deprecated shim: the old multi-author "enriched" PR document is gone.

        Reviews and comments are separate per-segment documents now; this returns
        the PR body segment only and ignores the extra arguments. Kept solely so
        callers outside this package's ownership keep importing; remove with them.
        """
        return cls.from_pull_request(owner, repo, raw)

    @classmethod
    def from_pull_review(
        cls,
        owner: str,
        repo: str,
        number: int,
        raw: dict[str, Any],
        *,
        pr_title: str = "",
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map a pull request review BODY (author = the reviewer)."""
        login, ref = cls._author_fields(raw.get("user"))
        review_id = raw["id"]
        return cls._build(
            source_type="pull_review",
            source_id=f"{owner}/{repo}/pull/{number}/review/{review_id}",
            content=raw.get("body") or "",
            title=f"[{owner}/{repo}] PR #{number} review: {pr_title[:150]}",
            context={
                "repo": f"{owner}/{repo}",
                "number": number,
                "title": pr_title,
                "kind": "pull_review",
                "url": raw.get("html_url"),
                "review_state": raw.get("state"),
            },
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=_parse_dt(raw.get("submitted_at")),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "review_id": review_id,
                "review_state": raw.get("state"),
                "author": ref,
            },
            tags=[f"repo:{owner}/{repo}"],
            authorship="single" if ref["kind"] == "github_account" else "unresolved",
        )

    @classmethod
    def from_pull_review_comment(
        cls,
        owner: str,
        repo: str,
        raw: dict[str, Any],
        *,
        number: int | None = None,
        pr_title: str = "",
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map an inline review comment (number from ``pull_request_url``)."""
        number = number if number is not None else number_from_url(raw.get("pull_request_url"))
        login, ref = cls._author_fields(raw.get("user"))
        comment_id = raw["id"]
        return cls._build(
            source_type="pull_review_comment",
            source_id=f"{owner}/{repo}/pull/{number}/review_comment/{comment_id}",
            content=raw.get("body") or "",
            title=f"[{owner}/{repo}] PR #{number} review comment",
            context={
                "repo": f"{owner}/{repo}",
                "number": number,
                "title": pr_title,
                "kind": "pull_review_comment",
                "url": raw.get("html_url"),
                "path": raw.get("path"),
            },
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "comment_id": comment_id,
                "author": ref,
            },
            tags=[f"repo:{owner}/{repo}"],
            authorship="single" if ref["kind"] == "github_account" else "unresolved",
        )

    # ── Issues ────────────────────────────────────────────────────────────────

    @classmethod
    def from_issue(
        cls,
        owner: str,
        repo: str,
        raw: dict[str, Any],
        *,
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map an issue BODY (author = the opener)."""
        number = raw["number"]
        title = raw.get("title", "") or ""
        login, ref = cls._author_fields(raw.get("user"))
        state = raw.get("state", "unknown")
        labels = [lbl["name"] for lbl in raw.get("labels", []) or []]
        return cls._build(
            source_type="issue",
            source_id=f"{owner}/{repo}/issues/{number}",
            content=raw.get("body") or "",
            title=f"[{owner}/{repo}] Issue #{number}: {title[:150]}",
            context={
                "repo": f"{owner}/{repo}",
                "number": number,
                "title": title,
                "kind": "issue",
                "url": raw.get("html_url"),
            },
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "state": state,
                "labels": labels,
                "author": ref,
            },
            tags=[*labels, state, f"repo:{owner}/{repo}"],
            authorship="single" if ref["kind"] == "github_account" else "unresolved",
        )

    @classmethod
    def from_issue_comment(
        cls,
        owner: str,
        repo: str,
        raw: dict[str, Any],
        *,
        number: int | None = None,
        issue_title: str = "",
        verbatim_max_chars: int = DEFAULT_VERBATIM_MAX_CHARS,
    ) -> ConnectorDocument:
        """Map an issue or PR-conversation comment (number from ``issue_url``)."""
        number = number if number is not None else number_from_url(raw.get("issue_url"))
        login, ref = cls._author_fields(raw.get("user"))
        comment_id = raw["id"]
        return cls._build(
            source_type="issue_comment",
            source_id=f"{owner}/{repo}/issues/{number}/comment/{comment_id}",
            content=raw.get("body") or "",
            title=f"[{owner}/{repo}] #{number} comment",
            context={
                "repo": f"{owner}/{repo}",
                "number": number,
                "title": issue_title,
                "kind": "issue_comment",
                "url": raw.get("html_url"),
            },
            verbatim_max_chars=verbatim_max_chars,
            source_url=raw.get("html_url"),
            source_author=login,
            timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "comment_id": comment_id,
                "author": ref,
            },
            tags=[f"repo:{owner}/{repo}"],
            authorship="single" if ref["kind"] == "github_account" else "unresolved",
        )

    # ── Discussions (legacy; out of scope for the automatic path) ────────────

    @classmethod
    def from_discussion(
        cls, owner: str, repo: str, raw: dict[str, Any]
    ) -> ConnectorDocument:
        """Map a GitHub Discussions GraphQL node (legacy connector path only)."""
        number = raw["number"]
        title = raw.get("title", "")
        body = raw.get("body") or ""
        author_login = (raw.get("author") or {}).get("login")
        category = (raw.get("category") or {}).get("name", "General")
        source_id = f"{owner}/{repo}/discussions/{number}"

        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Discussion #{number}: {title}",
            f"Author: {author_login or 'unknown'}",
            f"Category: {category}",
            "",
            body,
        ]
        comments = (raw.get("comments") or {}).get("nodes", [])
        if comments:
            content_parts.append("\n--- Comments ---")
            for comment in comments:
                comment_author = (comment.get("author") or {}).get("login", "unknown")
                content_parts.append(f"{comment_author}: {comment.get('body', '')}")

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="discussion",
            source_id=source_id,
            source_url=raw.get("url"),
            source_author=author_login,
            source_timestamp=_parse_dt(raw.get("updatedAt") or raw.get("createdAt")),
            title=f"[{owner}/{repo}] Discussion #{number}: {title[:150]}",
            content="\n".join(content_parts),
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "discussion", source_id),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "category": category,
                "comment_count": len(comments),
            },
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _author_ref(account: dict[str, Any] | None) -> dict[str, Any]:
    """Record a REST author object by GitHub's numeric user id (D-021).

    The numeric id is the only value attribution ever resolves on, and only
    through an admin-asserted workspace link. The login is kept as a display
    label and is never matched. A missing, non-integer or non-positive id
    (bool included) is not recorded, so such an author stays unresolved.
    """
    account = account or {}
    raw_id = account.get("id")
    login = account.get("login")
    if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id > 0:
        return {"github_user_id": raw_id, "login": login, "kind": "github_account"}
    return {"github_user_id": None, "login": login, "kind": "unknown"}


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to a timezone-aware datetime, or return None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
